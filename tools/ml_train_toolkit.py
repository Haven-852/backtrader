"""
ml_train_daily 训练特征采集工具

设计目标：
  - 千万级行：按「一只股票」循环，单票内存峰值可控
  - 先 check_env 再采集；不足则拒绝启动
  - 经 Datahubco（selective.ensure_range）补日线，再本地算特征写入 ml_train_daily
  - board_type：主板 / 科创板 / 创业板 / 北交所 / ST

LightGBM 友好采集约定（P01–P11，采集时即遵守）：
  P01 标签 = 次日高低 / 当日收盘 - 1（不是当日振幅）
  P02 截面用表内当日重算（涨跌家数/分位），不依赖远端 daily 全市场
  P03 滚动窗口不足时留空，不 fillna(0)；前 60 根 train_ready=0
  P04 一字板 close_location=0.5
  P05 写入 session_flag / is_one_word / is_halted
  P06 个股首末日之间缺开市日插入停牌占位（OHLC=前收）
  P07 当日全表行数 < 中位数一半 → train_ready=0
  P08 OHLC 矛盾时 high=GREATEST、low=LEAST
  P09 每票最后一根无次日：y 留空，train_ready=0
  P10 industry_l1 可能等于 industry_name，训练时只用其一（本工具不改行业源）
  P11 指数收益按日广播；行业收益用同业均值补
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import os
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, TypeVar

import numpy as np
import pandas as pd
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

# 单票约 10 年日线 < 3k 行；全市场千万级靠循环，不整表进内存
_DEFAULT_START = "2016-01-01"
_UPSERT_CHUNK = 500
_MIN_RAM_GB = 1.5
_WARN_RAM_GB = 3.0
_MIN_DISK_GB = 10.0
# 滚动特征最长窗口 60 交易日 ≈ 90 自然日；再留余量做预热
_WARMUP_CAL_DAYS = 150
_WARMUP_BARS = 60  # P03：前 N 根不进训练集
_FETCH_RETRIES = 3
_FETCH_RETRY_SLEEP = 1.5
_CHECK_NULL_DEFAULT_ROWS = 200
_CHECK_NULL_PROGRESS_FILE = Path(__file__).resolve().parents[1] / "data" / "ml_train_check_null_progress.json"

# P03：这些列为空时绝不能填 0（0 有业务含义或会误导模型）
_NEVER_ZERO_FILL_COLS = {
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "ret_60",
    "atr_5_pct", "atr_14_pct",
    "volatility_5", "volatility_10", "volatility_20", "volatility_mean_60",
    "beta_60", "price_volume_corr20",
    "hs300_ret_1", "hs300_ret_5", "zz1000_ret_5",
    "market_volatility_20", "size_style_spread",
    "industry_ret_1", "industry_ret_5", "industry_ret_20", "industry_vol_20",
    "stock_excess_ret_5", "industry_rank_ret_5", "stock_rank_industry",
    "market_adv_decl_ratio", "market_limit_up_count", "market_limit_down_count",
    "industry_breadth", "turnover_rate", "turnover_ma5", "turnover_ma20",
    "turnover_std20", "turnover_mean_60",
    "y_high", "y_low", "train_ready",
}

# check_null 自动补数时按列分流
_CROSS_SECTION_COLS = {
    "market_adv_decl_ratio",
    "market_limit_up_count",
    "market_limit_down_count",
    "industry_breadth",
    "stock_rank_industry",
    "market_amount_ratio",
}
_INDUSTRY_RANK_COLS = {"industry_rank_ret_5"}

T = TypeVar("T")


def _with_retry(
    fn: Callable[[], T],
    *,
    retries: int = _FETCH_RETRIES,
    sleep_s: float = _FETCH_RETRY_SLEEP,
    what: str = "request",
) -> T:
    """远端拉取失败时最多重试 retries 次。"""
    last: Optional[BaseException] = None
    attempts = max(1, int(retries))
    for i in range(attempts):
        try:
            return fn()
        except BaseException as e:  # noqa: BLE001 — 网络层异常需重试
            last = e
            logger.warning("%s 失败 (%d/%d): %s", what, i + 1, attempts, e)
            if i + 1 < attempts:
                time.sleep(sleep_s * (i + 1))
    assert last is not None
    raise last

# 东财板块日线经常缺历史时，用行业指数代理（Datahubco index_daily）
_INDUSTRY_PROXY_INDEX = {
    "银行": "399986.SZ",
    "银行Ⅱ": "399986.SZ",
    "银行I": "399986.SZ",
    "银行Ⅰ": "399986.SZ",
}

# LightGBM 输入特征（不含 OHLC / y_*）
FEATURE_COLS = [
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "ret_60",
    "close_ma5", "close_ma10", "close_ma20", "ma5_ma20",
    "atr_5_pct", "atr_14_pct",
    "volatility_5", "volatility_10", "volatility_20",
    "range_1", "range_mean_5", "range_mean_20",
    "upper_shadow", "lower_shadow", "body_pct", "close_location",
    "high_20_ratio", "low_20_ratio",
    "volume_ratio_5", "volume_ratio_20", "amount_ratio_20",
    "turnover_rate", "turnover_ma5", "turnover_ma20", "turnover_std20",
    "price_volume_corr20",
    "log_amount", "log_float_mv", "log_total_mv",
    "listing_age", "board_type", "industry_l1",
    "session_flag", "is_one_word", "is_halted",
    "beta_60", "turnover_mean_60", "volatility_mean_60", "limit_touch_count_20",
    "hs300_ret_1", "hs300_ret_5", "zz1000_ret_5",
    "market_volatility_20", "market_amount_ratio", "market_adv_decl_ratio",
    "market_limit_up_count", "market_limit_down_count", "size_style_spread",
    "industry_ret_1", "industry_ret_5", "industry_ret_20", "industry_vol_20",
    "industry_amount_ratio", "industry_breadth",
    "stock_excess_ret_5", "industry_rank_ret_5", "stock_rank_industry",
]

AUX_COLS = ["open", "high", "low", "close"]
LABEL_COLS = ["y_high", "y_low"]

# 训练表要求非空的业务列。y_high/y_low 在每票最后一天允许为空（没有次日）。
REQUIRED_COLS = [
    "symbol",
    "trade_date",
    "industry_code",
    "industry_name",
    *FEATURE_COLS,
    *AUX_COLS,
]


def _parse_date(v: Optional[str]) -> Optional[date]:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _gb(n: float) -> float:
    return round(float(n) / (1024 ** 3), 2)


def _is_st_name(name: Optional[str]) -> bool:
    if not name:
        return False
    u = str(name).upper().replace(" ", "")
    return "ST" in u or u.startswith("*ST") or "ＳＴ" in str(name)


def classify_board_type(ts_code: str, name: Optional[str] = None, market: Optional[str] = None) -> str:
    """板块类型：ST 优先，否则主板/科创板/创业板/北交所。"""
    if _is_st_name(name):
        return "ST"
    code, _, exch = (ts_code or "").partition(".")
    exch = (exch or "").upper()
    m = (market or "").strip()
    if m in ("主板", "科创板", "创业板", "北交所"):
        # 仍用代码纠正明显错误；ST 已在上面处理
        pass
    if exch == "BJ" or code.startswith(("8", "4")) and exch == "BJ":
        return "北交所"
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if m in ("主板", "科创板", "创业板", "北交所"):
        return m
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "主板"
    return m or "主板"


SESSION_NORMAL = "正常"
SESSION_ONE_WORD_UP = "一字涨停"
SESSION_ONE_WORD_DOWN = "一字跌停"
SESSION_ONE_WORD_FLAT = "平盘一字"
SESSION_HALT = "停牌"
_CHINEXT_20PCT_FROM = date(2020, 8, 24)


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    out = a / b.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def limit_pct_for(board_type: Optional[str], trade_date: Optional[date] = None) -> float:
    """涨跌停幅度（小数）。ST=5%，主板=10%，科创/注册制创业板=20%，北交所=30%。"""
    b = (board_type or "主板").strip()
    if b == "ST":
        return 0.05
    if b == "北交所":
        return 0.30
    if b == "科创板":
        return 0.20
    if b == "创业板":
        if trade_date is not None and trade_date >= _CHINEXT_20PCT_FROM:
            return 0.20
        return 0.10
    return 0.10


def next_day_return_labels(close: pd.Series, high: pd.Series, low: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """
    y_high = 次日最高价 / 当日收盘 - 1
    y_low  = 次日最低价 / 当日收盘 - 1
    最后一根没有次日，结果为 NaN。
    """
    y_high = _safe_div(high.shift(-1), close) - 1.0
    y_low = _safe_div(low.shift(-1), close) - 1.0
    return y_high, y_low


def close_location_filled(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    """收盘在当日高低区间的位置；一字/停牌高低相同则记 0.5。"""
    loc = _safe_div(close - low, (high - low).replace(0, np.nan))
    return loc.fillna(0.5)


def session_state(
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    prev_close: Optional[float],
    vol: Optional[float] = None,
    amount: Optional[float] = None,
    halted: bool = False,
    board_type: Optional[str] = None,
    trade_date: Optional[date] = None,
) -> str:
    """把当日交易现象标成可供 LightGBM 学习的类别。"""
    if halted:
        return SESSION_HALT
    vol_v = float(vol) if vol is not None and vol == vol else None
    amt_v = float(amount) if amount is not None and amount == amount else None
    if vol_v is not None and vol_v <= 0 and (amt_v is None or amt_v <= 0):
        return SESSION_HALT
    one_word = (
        open_ is not None
        and high is not None
        and low is not None
        and close is not None
        and open_ == high == low == close
    )
    if not one_word:
        return SESSION_NORMAL
    prev = float(prev_close) if prev_close not in (None, 0) else 0.0
    chg = (float(close) / prev - 1.0) if prev > 0 else 0.0
    lim = limit_pct_for(board_type, trade_date)
    if chg >= lim * 0.95:
        return SESSION_ONE_WORD_UP
    if chg <= -lim * 0.95:
        return SESSION_ONE_WORD_DOWN
    return SESSION_ONE_WORD_FLAT


def _true_range(high: pd.Series, low: pd.Series, prev_close: pd.Series) -> pd.Series:
    a = (high - low).abs()
    b = (high - prev_close).abs()
    c = (low - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


class MlTrainToolkit:
    """训练表采集：check_env / fill_one / status / fill_batch（循环控内存）。"""

    def __init__(self) -> None:
        self._engine = None
        self._selective = None
        self._datahub = None

    @property
    def engine(self):
        if self._engine is None:
            from data_layer.db_manager import storage_manager

            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @property
    def selective(self):
        if self._selective is None:
            from data_layer.collectors.datahubco.selective import selective_fetcher

            self._selective = selective_fetcher
        return self._selective

    @property
    def datahub(self):
        if self._datahub is None:
            from tools.datahubco_toolkit import datahubco_toolkit

            self._datahub = datahubco_toolkit
        return self._datahub

    # ─── 环境检查 ─────────────────────────────────────

    def check_env(self, *, require_ram_gb: float = _MIN_RAM_GB) -> Dict[str, Any]:
        """确认内存/磁盘/PG/Redis/Datahubco 足以启动采集。"""
        checks: Dict[str, Any] = {}
        ok = True
        blockers: List[str] = []
        warnings: List[str] = []

        # RAM
        try:
            import psutil

            mem = psutil.virtual_memory()
            avail = _gb(mem.available)
            checks["ram"] = {
                "total_gb": _gb(mem.total),
                "available_gb": avail,
                "percent": mem.percent,
            }
            if avail < float(require_ram_gb):
                ok = False
                blockers.append(
                    f"可用内存 {avail}GB < 最低要求 {require_ram_gb}GB，拒绝启动采集"
                )
            elif avail < _WARN_RAM_GB:
                warnings.append(f"可用内存仅 {avail}GB，请保持单票循环、勿并行大批量")
        except Exception as e:
            checks["ram"] = {"error": str(e)}
            warnings.append(f"无法读取内存信息: {e}")

        # Disk（项目所在盘）
        try:
            root = os.path.splitdrive(os.path.abspath(__file__))[0] or "C:\\"
            usage = shutil.disk_usage(root + "\\")
            free = _gb(usage.free)
            checks["disk"] = {
                "root": root,
                "total_gb": _gb(usage.total),
                "free_gb": free,
            }
            if free < _MIN_DISK_GB:
                ok = False
                blockers.append(f"磁盘剩余 {free}GB < {_MIN_DISK_GB}GB")
        except Exception as e:
            checks["disk"] = {"error": str(e)}
            warnings.append(f"无法读取磁盘: {e}")

        # PostgreSQL + 训练表
        try:
            with self.engine.connect() as conn:
                conn.execute(sa_text("SELECT 1"))
                exists = conn.execute(
                    sa_text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name='ml_train_daily'"
                    )
                ).scalar()
                n = 0
                if exists:
                    n = int(conn.execute(sa_text("SELECT COUNT(*) FROM ml_train_daily")).scalar() or 0)
                daily_n = int(
                    conn.execute(
                        sa_text("SELECT COUNT(*) FROM stock_daily WHERE trade_date >= :d"),
                        {"d": date(2016, 1, 1)},
                    ).scalar()
                    or 0
                )
            checks["postgres"] = {
                "ok": True,
                "ml_train_daily_exists": bool(exists),
                "ml_train_daily_rows": n,
                "stock_daily_since_2016": daily_n,
            }
            if not exists:
                ok = False
                blockers.append("ml_train_daily 表不存在，请先执行 SchemaManager.ensure_all_tables")
        except Exception as e:
            ok = False
            checks["postgres"] = {"ok": False, "error": str(e)}
            blockers.append(f"PostgreSQL 不可用: {e}")

        # Redis（可选缓存）
        try:
            from data_layer.db_manager import storage_manager

            r = storage_manager.get_redis_client()
            if r:
                r.ping()
                checks["redis"] = {"ok": True}
            else:
                checks["redis"] = {"ok": False, "error": "client none"}
                warnings.append("Redis 未连接（日线采集仍可进行）")
        except Exception as e:
            checks["redis"] = {"ok": False, "error": str(e)}
            warnings.append(f"Redis 探测失败: {e}")

        # Datahubco client
        try:
            client = self.selective.client
            # 轻量探测：交易日历接口（失败不一定阻断，但给出警告）
            probe = client.query("trade_cal", exchange="SSE", start_date="20260101", end_date="20260105")
            checks["datahubco"] = {
                "ok": True,
                "probe_rows": int(len(probe)) if probe is not None else 0,
            }
        except Exception as e:
            checks["datahubco"] = {"ok": False, "error": str(e)[:200]}
            warnings.append(f"Datahubco 探测异常（ensure_range 时再试）: {e}")

        checks["strategy"] = {
            "mode": "one_symbol_loop",
            "upsert_chunk": _UPSERT_CHUNK,
            "note": "千万级靠「按股票循环 + 分块 UPSERT」，禁止一次加载全市场到内存",
            "feature_cols": len(FEATURE_COLS),
            "label_cols": LABEL_COLS,
            "aux_ohlc": AUX_COLS,
        }

        return {
            "ok": ok,
            "action": "check_env",
            "ready": ok,
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "next_hint": (
                "环境就绪后调用 fill-ml-train-daily action=fill_one，"
                "ts_code=000001.SZ，start_date=2016-01-01。"
                if ok
                else "请先解决 blockers 再采集。"
            ),
        }

    # ─── 元数据 ───────────────────────────────────────

    def _stock_meta(self, ts_code: str) -> Dict[str, Any]:
        name = market = industry = None
        list_date = None
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT name, market, industry, list_date FROM ref_stock_basic "
                    "WHERE ts_code = :c LIMIT 1"
                ),
                self.engine,
                params={"c": ts_code},
            )
            if not df.empty:
                row = df.iloc[0]
                name = row.get("name")
                market = row.get("market")
                industry = row.get("industry")
                ld = row.get("list_date")
                if pd.notna(ld):
                    list_date = pd.to_datetime(ld).date()
        except Exception:
            pass
        board = classify_board_type(ts_code, name=str(name) if name is not None else None, market=str(market) if market is not None else None)
        industry_code = None
        industry_name = str(industry) if industry is not None and pd.notna(industry) else None
        try:
            mem = pd.read_sql(
                sa_text(
                    """
                    SELECT m.ts_code AS industry_code, COALESCE(i.name, m.ts_code) AS industry_name
                    FROM sector_dc_member m
                    LEFT JOIN LATERAL (
                        SELECT name FROM sector_dc_index_daily
                        WHERE ts_code = m.ts_code
                        ORDER BY trade_date DESC LIMIT 1
                    ) i ON TRUE
                    WHERE m.con_code = :c
                    ORDER BY m.trade_date DESC NULLS LAST
                    LIMIT 1
                    """
                ),
                self.engine,
                params={"c": ts_code},
            )
            if not mem.empty:
                industry_code = str(mem.iloc[0]["industry_code"])
                industry_name = str(mem.iloc[0]["industry_name"] or industry_name or "")
        except Exception:
            pass
        return {
            "name": name,
            "market": market,
            "board_type": board,
            "industry_code": industry_code,
            "industry_name": industry_name,
            "industry_l1": industry_name,
            "list_date": list_date,
        }

    @staticmethod
    def _iter_year_chunks(start: date, end: date) -> Iterator[Tuple[date, date]]:
        cur = start
        while cur <= end:
            year_end = date(cur.year, 12, 31)
            ce = min(year_end, end)
            yield cur, ce
            cur = ce + timedelta(days=1)

    def _load_index_rets(self, start: date, end: date) -> pd.DataFrame:
        """沪深300 / 中证1000 收益，按 trade_date 索引（含预热起点）。"""
        codes = ["000300.SH", "000852.SH"]
        frames = []
        for code in codes:
            try:
                _with_retry(
                    lambda c=code: self.selective.ensure_range(
                        c, start.isoformat(), end.isoformat()
                    ),
                    what=f"ensure_range:{code}",
                )
            except Exception as e:
                logger.warning("指数补数失败 %s: %s", code, e)
            try:
                df = pd.read_sql(
                    sa_text(
                        "SELECT trade_date, close FROM index_daily "
                        "WHERE ts_code = :c AND trade_date BETWEEN :a AND :b ORDER BY trade_date"
                    ),
                    self.engine,
                    params={"c": code, "a": start, "b": end},
                )
            except Exception:
                df = pd.DataFrame()
            if df.empty:
                continue
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df = df.set_index("trade_date").sort_index()
            s = pd.to_numeric(df["close"], errors="coerce")
            tag = "hs300" if code.startswith("000300") else "zz1000"
            frames.append(pd.DataFrame({
                f"{tag}_ret_1": s.pct_change(1),
                f"{tag}_ret_5": s.pct_change(5),
            }))
        if not frames:
            return pd.DataFrame()
        out = frames[0]
        for f in frames[1:]:
            out = out.join(f, how="outer")
        return out

    def _load_daily_basic(self, ts_code: str, start: date, end: date) -> pd.DataFrame:
        """按年分段拉取 daily_basic，避免接口一次区间过长被截断。"""
        for cs, ce in self._iter_year_chunks(start, end):
            try:
                _with_retry(
                    lambda cs=cs, ce=ce: self.datahub.get_fund_data(
                        dataset="daily_basic",
                        ts_code=ts_code,
                        start_date=cs.strftime("%Y%m%d"),
                        end_date=ce.strftime("%Y%m%d"),
                        source="datahubco",
                        write_back=True,
                        limit=5000,
                    ),
                    what=f"daily_basic:{ts_code}:{cs}",
                )
            except Exception as e:
                logger.warning("daily_basic 分段补数失败 %s %s~%s: %s", ts_code, cs, ce, e)
        try:
            df = pd.read_sql(
                sa_text(
                    """
                    SELECT trade_date, turnover_rate, total_mv, circ_mv
                    FROM stock_daily_basic
                    WHERE ts_code = :c AND trade_date BETWEEN :a AND :b
                    ORDER BY trade_date
                    """
                ),
                self.engine,
                params={"c": ts_code, "a": start, "b": end},
            )
        except Exception:
            return pd.DataFrame()
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        return df

    def _panel_from_ohlc_amount(self, df: pd.DataFrame, prefix: str = "industry") -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        work = df.copy()
        work["trade_date"] = pd.to_datetime(work["trade_date"]).dt.date
        work = work.set_index("trade_date").sort_index()
        close = pd.to_numeric(work["close"], errors="coerce")
        amt = (
            pd.to_numeric(work["amount"], errors="coerce")
            if "amount" in work.columns
            else pd.Series(np.nan, index=work.index)
        )
        ret1 = close.pct_change(1)
        if "pct_change" in work.columns:
            pct = pd.to_numeric(work["pct_change"], errors="coerce")
            ret1 = ret1.fillna(pct / 100.0)
        if "pct_chg" in work.columns:
            pct = pd.to_numeric(work["pct_chg"], errors="coerce")
            ret1 = ret1.fillna(pct / 100.0)
        return pd.DataFrame({
            f"{prefix}_ret_1": ret1,
            f"{prefix}_ret_5": close.pct_change(5),
            f"{prefix}_ret_20": close.pct_change(20),
            f"{prefix}_vol_20": ret1.rolling(20).std(),
            f"{prefix}_amount_ratio": _safe_div(amt, amt.rolling(20).mean()),
        })

    def _load_index_ohlc(self, ts_code: str, start: date, end: date) -> pd.DataFrame:
        try:
            _with_retry(
                lambda: self.selective.ensure_range(
                    ts_code, start.isoformat(), end.isoformat()
                ),
                what=f"ensure_range:{ts_code}",
            )
        except Exception as e:
            logger.warning("指数补数失败 %s: %s", ts_code, e)
        try:
            return pd.read_sql(
                sa_text(
                    """
                    SELECT trade_date, open, high, low, close, vol, amount, pct_chg
                    FROM index_daily
                    WHERE ts_code = :c AND trade_date BETWEEN :a AND :b
                    ORDER BY trade_date
                    """
                ),
                self.engine,
                params={"c": ts_code, "a": start, "b": end},
            )
        except Exception:
            return pd.DataFrame()

    def _load_industry_panel(
        self,
        industry_code: Optional[str],
        start: date,
        end: date,
        *,
        industry_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """行业收益面板：优先东财板块日线；不足则用行业指数代理（如银行→399986.SZ）。"""
        df = pd.DataFrame()
        if industry_code:
            try:
                _with_retry(
                    lambda: self.datahub.get_bars(
                        ts_code=industry_code,
                        dataset="sector_dc_daily",
                        start_date=start.isoformat(),
                        end_date=end.isoformat(),
                        limit=2000,
                        source="auto",
                        write_back=True,
                    ),
                    what=f"sector_dc_daily:{industry_code}",
                )
            except Exception as e:
                logger.warning("行业日线补数跳过 %s: %s", industry_code, e)
            try:
                df = pd.read_sql(
                    sa_text(
                        """
                        SELECT trade_date, close, amount, pct_change
                        FROM sector_dc_daily
                        WHERE ts_code = :c AND trade_date BETWEEN :a AND :b
                        ORDER BY trade_date
                        """
                    ),
                    self.engine,
                    params={"c": industry_code, "a": start, "b": end},
                )
            except Exception:
                df = pd.DataFrame()

        panel = self._panel_from_ohlc_amount(df, "industry")
        # 覆盖不足时用行业指数代理
        need = panel.empty or int(panel["industry_ret_1"].notna().sum()) < 60
        if need:
            proxy = None
            name = (industry_name or "").strip()
            for key, idx in _INDUSTRY_PROXY_INDEX.items():
                if key and key in name:
                    proxy = idx
                    break
            if proxy is None and name.startswith("银行"):
                proxy = "399986.SZ"
            if proxy:
                idx_df = self._load_index_ohlc(proxy, start, end)
                panel = self._panel_from_ohlc_amount(idx_df, "industry")
                logger.info("行业面板改用指数代理 %s (%s)", proxy, name)
        return panel

    def _load_market_amount_panel(self, start: date, end: date) -> pd.Series:
        """全市场成交额代理：上证指数成交额 / 近20日均额。"""
        sh = self._load_index_ohlc("000001.SH", start, end)
        if sh.empty or "amount" not in sh.columns:
            return pd.Series(dtype=float)
        sh["trade_date"] = pd.to_datetime(sh["trade_date"]).dt.date
        sh = sh.set_index("trade_date").sort_index()
        amt = pd.to_numeric(sh["amount"], errors="coerce")
        return _safe_div(amt, amt.rolling(20).mean())

    def _query_daily_market(self, ymd: str) -> pd.DataFrame:
        """全市场 daily，带 3 次重试；优先分页，失败则一次拉满。超时临时提到 90s。"""

        def _once() -> pd.DataFrame:
            client = self.datahub.client
            old_timeout = getattr(client, "timeout", 30)
            try:
                client.timeout = max(int(old_timeout or 30), 90)
                frames: List[pd.DataFrame] = []
                offset = 0
                page = 5000
                use_offset = True
                while True:
                    try:
                        if use_offset:
                            part = client.query(
                                "daily", trade_date=ymd, limit=page, offset=offset
                            )
                        else:
                            part = client.query("daily", trade_date=ymd, limit=8000)
                            return part if part is not None else pd.DataFrame()
                    except Exception:
                        if use_offset and offset == 0:
                            use_offset = False
                            continue
                        raise
                    if part is None or part.empty:
                        break
                    frames.append(part)
                    if len(part) < page:
                        break
                    offset += page
                    if offset > 20000:
                        break
                if not frames:
                    return pd.DataFrame()
                return pd.concat(frames, ignore_index=True).drop_duplicates(
                    subset=["ts_code"], keep="last"
                )
            finally:
                client.timeout = old_timeout

        return _with_retry(_once, what=f"daily@{ymd}", sleep_s=2.0)

    def _fetch_market_cross_section_day(
        self,
        trade_date: date,
        member_codes: Optional[Sequence[str]] = None,
        *,
        focus_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        调用 Datahubco daily(trade_date) 拉全市场一日行情，计算截面字段（失败重试 3 次）。
        """
        ymd = trade_date.strftime("%Y%m%d")
        try:
            df = self._query_daily_market(ymd)
        except Exception as e:
            return {"ok": False, "error": str(e), "trade_date": trade_date.isoformat()}
        if df is None or df.empty:
            return {"ok": False, "error": "当日无全市场日线", "trade_date": trade_date.isoformat()}

        pct = pd.to_numeric(df.get("pct_chg"), errors="coerce")
        if pct.isna().all() and "change" in df.columns and "pre_close" in df.columns:
            pct = _safe_div(
                pd.to_numeric(df["change"], errors="coerce"),
                pd.to_numeric(df["pre_close"], errors="coerce"),
            ) * 100.0
        df = df.copy()
        df["pct_chg"] = pct
        amt = pd.to_numeric(df.get("amount"), errors="coerce")
        adv = int((pct > 0).sum())
        decl = int((pct < 0).sum())
        limit_up = int((pct >= 9.5).sum())
        limit_down = int((pct <= -9.5).sum())
        amount_sum = float(amt.fillna(0).sum())
        out: Dict[str, Any] = {
            "ok": True,
            "trade_date": trade_date.isoformat(),
            "market_adv_decl_ratio": (adv / decl) if decl > 0 else (float(adv) if adv else 0.0),
            "market_limit_up_count": limit_up,
            "market_limit_down_count": limit_down,
            "market_amount_sum": amount_sum,
            "universe": int(len(df)),
        }
        # 行业内广度 / 个股分位
        members = set(member_codes or [])
        if focus_code:
            members.add(focus_code)
        if members:
            sub = df.loc[df["ts_code"].astype(str).isin(members)].copy()
            if not sub.empty:
                sp = pd.to_numeric(sub["pct_chg"], errors="coerce")
                # 成分全跌时 breadth=0，仍算有效值
                out["industry_breadth"] = float((sp.fillna(-1) > 0).mean()) if len(sp) else 0.0
                member_pct = {
                    str(r.ts_code): float(r.pct_chg)
                    for r in sub.itertuples()
                    if pd.notna(getattr(r, "pct_chg", None))
                }
                out["member_pct"] = member_pct
                if focus_code and focus_code in member_pct and member_pct:
                    vals = sorted(member_pct.values())
                    my = member_pct[focus_code]
                    out["stock_rank_industry"] = float(
                        sum(1 for v in vals if v <= my) / max(1, len(vals))
                    )
                elif focus_code:
                    # 焦点股当日无行情时：用行业中位分位 0.5 兜底，避免 NULL
                    out["stock_rank_industry"] = 0.5
            else:
                out["industry_breadth"] = 0.0
                out["stock_rank_industry"] = 0.5
        else:
            out["industry_breadth"] = 0.0
            out["stock_rank_industry"] = 0.5
        return out

    def _load_stock_daily(self, ts_code: str, start: date, end: date) -> pd.DataFrame:
        df = pd.read_sql(
            sa_text(
                """
                SELECT trade_date, open, high, low, close, pre_close, vol, amount, pct_chg
                FROM stock_daily
                WHERE ts_code = :c AND trade_date BETWEEN :a AND :b
                ORDER BY trade_date
                """
            ),
            self.engine,
            params={"c": ts_code, "a": start, "b": end},
        )
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        for col in ("open", "high", "low", "close", "pre_close", "vol", "amount", "pct_chg"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    # ─── 特征计算（单票） ─────────────────────────────

    def compute_features_one(
        self,
        ts_code: str,
        daily: pd.DataFrame,
        *,
        meta: Optional[Dict[str, Any]] = None,
        basic: Optional[pd.DataFrame] = None,
        index_rets: Optional[pd.DataFrame] = None,
        industry_panel: Optional[pd.DataFrame] = None,
        market_amount_ratio: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        if daily is None or daily.empty:
            return pd.DataFrame()
        meta = meta or self._stock_meta(ts_code)
        df = daily.sort_values("trade_date").copy().reset_index(drop=True)
        o = pd.to_numeric(df["open"], errors="coerce")
        h = pd.to_numeric(df["high"], errors="coerce")
        l = pd.to_numeric(df["low"], errors="coerce")
        c = pd.to_numeric(df["close"], errors="coerce")
        # P08：OHLC 自相矛盾时夹紧，避免影线/位置算出 NaN
        ohlc = pd.concat([o, h, l, c], axis=1)
        h = ohlc.max(axis=1)
        l = ohlc.min(axis=1)
        df["open"], df["high"], df["low"], df["close"] = o, h, l, c
        prev = df["pre_close"] if "pre_close" in df.columns else c.shift(1)
        prev = pd.to_numeric(prev, errors="coerce").fillna(c.shift(1))
        vol = (
            pd.to_numeric(df["vol"], errors="coerce")
            if "vol" in df.columns
            else pd.Series(np.nan, index=df.index)
        )
        amt = (
            pd.to_numeric(df["amount"], errors="coerce")
            if "amount" in df.columns
            else pd.Series(np.nan, index=df.index)
        )

        ret = c.pct_change(1)
        out = pd.DataFrame({"trade_date": df["trade_date"]})
        out["symbol"] = ts_code
        out["industry_code"] = meta.get("industry_code")
        out["industry_name"] = meta.get("industry_name")
        out["industry_l1"] = meta.get("industry_l1")
        out["board_type"] = meta.get("board_type") or classify_board_type(ts_code)

        for n in (1, 3, 5, 10, 20, 60):
            out[f"ret_{n}"] = c.pct_change(n)

        ma5 = c.rolling(5).mean()
        ma10 = c.rolling(10).mean()
        ma20 = c.rolling(20).mean()
        out["close_ma5"] = _safe_div(c, ma5) - 1.0
        out["close_ma10"] = _safe_div(c, ma10) - 1.0
        out["close_ma20"] = _safe_div(c, ma20) - 1.0
        out["ma5_ma20"] = _safe_div(ma5, ma20) - 1.0

        tr = _true_range(h, l, prev)
        out["atr_5_pct"] = _safe_div(tr.rolling(5).mean(), c)
        out["atr_14_pct"] = _safe_div(tr.rolling(14).mean(), c)
        out["volatility_5"] = ret.rolling(5).std()
        out["volatility_10"] = ret.rolling(10).std()
        out["volatility_20"] = ret.rolling(20).std()

        rng = _safe_div(h - l, prev)
        out["range_1"] = rng
        out["range_mean_5"] = rng.rolling(5).mean()
        out["range_mean_20"] = rng.rolling(20).mean()

        out["upper_shadow"] = _safe_div(h - pd.concat([o, c], axis=1).max(axis=1), c)
        out["lower_shadow"] = _safe_div(pd.concat([o, c], axis=1).min(axis=1) - l, c)
        out["body_pct"] = _safe_div(c - o, o)
        out["close_location"] = close_location_filled(c, h, l)
        out["high_20_ratio"] = _safe_div(c, h.rolling(20).max())
        out["low_20_ratio"] = _safe_div(c, l.rolling(20).min())

        vol_ma5 = vol.rolling(5).mean()
        vol_ma20 = vol.rolling(20).mean()
        amt_ma20 = amt.rolling(20).mean()
        out["volume_ratio_5"] = _safe_div(vol, vol_ma5)
        out["volume_ratio_20"] = _safe_div(vol, vol_ma20)
        out["amount_ratio_20"] = _safe_div(amt, amt_ma20)

        # daily_basic
        turn = pd.Series(np.nan, index=df.index, dtype=float)
        float_mv = pd.Series(np.nan, index=df.index, dtype=float)
        total_mv = pd.Series(np.nan, index=df.index, dtype=float)
        if basic is not None and not basic.empty:
            b = basic.drop_duplicates("trade_date").copy()
            merged = df[["trade_date"]].merge(b, on="trade_date", how="left")
            if "turnover_rate" in merged.columns:
                turn = pd.to_numeric(merged["turnover_rate"], errors="coerce")
            if "circ_mv" in merged.columns:
                float_mv = pd.to_numeric(merged["circ_mv"], errors="coerce")
            if "total_mv" in merged.columns:
                total_mv = pd.to_numeric(merged["total_mv"], errors="coerce")

        out["turnover_rate"] = turn
        out["turnover_ma5"] = turn.rolling(5).mean()
        out["turnover_ma20"] = turn.rolling(20).mean()
        out["turnover_std20"] = turn.rolling(20).std()
        out["turnover_mean_60"] = turn.rolling(60).mean()

        vol_chg = vol.pct_change(1)
        out["price_volume_corr20"] = ret.rolling(20).corr(vol_chg)

        out["log_amount"] = np.log1p(amt.clip(lower=0))
        out["log_float_mv"] = np.log1p(float_mv.clip(lower=0))
        out["log_total_mv"] = np.log1p(total_mv.clip(lower=0))

        list_date = meta.get("list_date")
        if list_date:
            out["listing_age"] = df["trade_date"].map(
                lambda d: max(0.0, (d - list_date).days / 365.25) if isinstance(d, date) else np.nan
            )
        else:
            out["listing_age"] = np.nan

        # beta vs hs300
        if index_rets is not None and not index_rets.empty and "hs300_ret_1" in index_rets.columns:
            idx = index_rets.copy()
            hs_map = idx["hs300_ret_1"].to_dict()
            hs = pd.to_numeric(df["trade_date"].map(hs_map), errors="coerce")
            cov = ret.rolling(60).cov(hs)
            var = hs.rolling(60).var()
            out["beta_60"] = _safe_div(cov, var)
            out["hs300_ret_1"] = hs
            if "hs300_ret_5" in idx.columns:
                out["hs300_ret_5"] = pd.to_numeric(
                    df["trade_date"].map(idx["hs300_ret_5"].to_dict()), errors="coerce"
                )
            else:
                out["hs300_ret_5"] = np.nan
            if "zz1000_ret_5" in idx.columns:
                out["zz1000_ret_5"] = pd.to_numeric(
                    df["trade_date"].map(idx["zz1000_ret_5"].to_dict()), errors="coerce"
                )
            else:
                out["zz1000_ret_5"] = np.nan
        else:
            out["beta_60"] = np.nan
            out["hs300_ret_1"] = np.nan
            out["hs300_ret_5"] = np.nan
            out["zz1000_ret_5"] = np.nan

        out["volatility_mean_60"] = ret.rolling(60).std()
        # 涨跌停触及：|pct_chg| >= 9.5 近似
        pct = df["pct_chg"] if "pct_chg" in df.columns else ret * 100.0
        touch = pct.abs() >= 9.5
        out["limit_touch_count_20"] = touch.astype(int).rolling(20).sum()

        # 市场代理：沪深300波动 / 大小盘收益差 / 上证成交额相对均值
        if index_rets is not None and not index_rets.empty and "hs300_ret_1" in out.columns:
            out["market_volatility_20"] = pd.to_numeric(out["hs300_ret_1"], errors="coerce").rolling(20).std()
            if "zz1000_ret_5" in out.columns and "hs300_ret_5" in out.columns:
                out["size_style_spread"] = (
                    pd.to_numeric(out["hs300_ret_5"], errors="coerce")
                    - pd.to_numeric(out["zz1000_ret_5"], errors="coerce")
                )
            else:
                out["size_style_spread"] = np.nan
        else:
            out["market_volatility_20"] = np.nan
            out["size_style_spread"] = np.nan

        if market_amount_ratio is not None and len(market_amount_ratio) > 0:
            out["market_amount_ratio"] = pd.to_numeric(
                df["trade_date"].map(market_amount_ratio.to_dict()), errors="coerce"
            )
        else:
            out["market_amount_ratio"] = np.nan

        # 截面字段默认空：fill_complete / polish 用表内当日截面重算（P02），不打远端 API
        for col in (
            "market_adv_decl_ratio",
            "market_limit_up_count", "market_limit_down_count",
            "industry_breadth", "industry_rank_ret_5", "stock_rank_industry",
        ):
            out[col] = np.nan

        # 行业面板（有板块日线则可填）
        if industry_panel is not None and not industry_panel.empty:
            for col in (
                "industry_ret_1", "industry_ret_5", "industry_ret_20",
                "industry_vol_20", "industry_amount_ratio",
            ):
                if col in industry_panel.columns:
                    out[col] = pd.to_numeric(
                        df["trade_date"].map(industry_panel[col].to_dict()), errors="coerce"
                    )
                else:
                    out[col] = np.nan
            out["stock_excess_ret_5"] = out["ret_5"] - out["industry_ret_5"]
        else:
            for col in (
                "industry_ret_1", "industry_ret_5", "industry_ret_20",
                "industry_vol_20", "industry_amount_ratio", "stock_excess_ret_5",
            ):
                out[col] = np.nan

        # OHLC 采集列 + 次日标签（次日高低 / 当日收盘）
        out["open"] = o
        out["high"] = h
        out["low"] = l
        out["close"] = c
        out["y_high"], out["y_low"] = next_day_return_labels(c, h, l)

        one_word = (o == h) & (h == l) & (l == c)
        halted = (vol.fillna(-1) == 0) & (amt.fillna(0) <= 0)
        board = str(out["board_type"].iloc[0] if len(out) else "主板")
        dates = pd.to_datetime(df["trade_date"]).dt.date
        if board == "创业板":
            lim = np.where(dates >= _CHINEXT_20PCT_FROM, 0.20, 0.10)
        else:
            lim = np.full(len(df), limit_pct_for(board), dtype=float)
        chg = _safe_div(c, prev) - 1.0
        session = np.where(
            halted,
            SESSION_HALT,
            np.where(
                ~one_word,
                SESSION_NORMAL,
                np.where(
                    chg >= lim * 0.95,
                    SESSION_ONE_WORD_UP,
                    np.where(chg <= -lim * 0.95, SESSION_ONE_WORD_DOWN, SESSION_ONE_WORD_FLAT),
                ),
            ),
        )
        out["session_flag"] = session
        out["is_one_word"] = one_word.astype(int)
        out["is_halted"] = halted.astype(int)

        # P03/P09：预热窗与无次日标签的行不进训练集（P07 薄日在表级 polish）
        ready = pd.Series(1, index=out.index, dtype=int)
        ready.iloc[:_WARMUP_BARS] = 0
        ready = ready.where(out["y_high"].notna() & out["y_low"].notna(), 0)
        ready = ready.where(out["ret_60"].notna(), 0)
        ready = ready.where(~halted.fillna(False), 0)
        out["train_ready"] = ready.astype(int)
        return out

    def _upsert_features(self, feat: pd.DataFrame, chunk_size: int = _UPSERT_CHUNK) -> int:
        if feat is None or feat.empty:
            return 0
        cols = [
            "symbol", "trade_date", "industry_code", "industry_name",
            *FEATURE_COLS,
            *AUX_COLS,
            *LABEL_COLS,
            "train_ready",
        ]
        # FEATURE_COLS 已含 board_type / industry_l1，避免重复
        cols = list(dict.fromkeys(cols))
        use = feat.copy()
        for c in cols:
            if c not in use.columns:
                use[c] = None
        use = use[cols]
        # numpy → python
        records = []
        for row in use.itertuples(index=False):
            d = {}
            for i, c in enumerate(cols):
                v = row[i]
                if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                    d[c] = None
                elif isinstance(v, (np.floating,)):
                    d[c] = float(v)
                elif isinstance(v, (np.integer,)):
                    d[c] = int(v)
                elif isinstance(v, pd.Timestamp):
                    d[c] = v.date()
                else:
                    d[c] = v
            records.append(d)

        # 截面/行业分位常由 enrich_* 后写；fill_one 重跑时若新值为 NULL 则保留库内已有值
        preserve_if_null = {
            "market_adv_decl_ratio",
            "market_limit_up_count",
            "market_limit_down_count",
            "industry_breadth",
            "industry_rank_ret_5",
            "stock_rank_industry",
            "market_amount_ratio",
        }
        placeholders = ", ".join(f":{c}" for c in cols)
        update_parts = []
        for c in cols:
            if c in ("symbol", "trade_date"):
                continue
            if c in preserve_if_null:
                update_parts.append(
                    f"{c}=COALESCE(EXCLUDED.{c}, ml_train_daily.{c})"
                )
            else:
                update_parts.append(f"{c}=EXCLUDED.{c}")
        updates = ", ".join(update_parts)
        sql = f"""
            INSERT INTO ml_train_daily ({", ".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT (symbol, trade_date) DO UPDATE SET
            {updates},
            updated_at = CURRENT_TIMESTAMP
        """
        written = 0
        with self.engine.begin() as conn:
            for i in range(0, len(records), max(1, int(chunk_size))):
                batch = records[i : i + int(chunk_size)]
                conn.execute(sa_text(sql), batch)
                written += len(batch)
        return written

    # ─── 主流程 ───────────────────────────────────────

    def status(self, ts_code: Optional[str] = None) -> Dict[str, Any]:
        try:
            with self.engine.connect() as conn:
                total = int(conn.execute(sa_text("SELECT COUNT(*) FROM ml_train_daily")).scalar() or 0)
                symbols = int(
                    conn.execute(sa_text("SELECT COUNT(DISTINCT symbol) FROM ml_train_daily")).scalar() or 0
                )
                by_board = conn.execute(
                    sa_text(
                        "SELECT COALESCE(board_type,'未知') AS b, COUNT(*) "
                        "FROM ml_train_daily GROUP BY 1 ORDER BY 2 DESC"
                    )
                ).fetchall()
                span = conn.execute(
                    sa_text("SELECT MIN(trade_date), MAX(trade_date) FROM ml_train_daily")
                ).fetchone()
                one = None
                if ts_code:
                    code = self.selective.normalize_ts_code(ts_code)
                    one = conn.execute(
                        sa_text(
                            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date), "
                            "MAX(board_type) FROM ml_train_daily WHERE symbol=:c"
                        ),
                        {"c": code},
                    ).fetchone()
            return {
                "ok": True,
                "action": "status",
                "total_rows": total,
                "symbols": symbols,
                "date_min": span[0].isoformat() if span and span[0] else None,
                "date_max": span[1].isoformat() if span and span[1] else None,
                "by_board_type": [{"board_type": r[0], "rows": int(r[1])} for r in by_board],
                "symbol_stats": (
                    {
                        "ts_code": self.selective.normalize_ts_code(ts_code) if ts_code else None,
                        "rows": int(one[0] or 0) if one else 0,
                        "date_min": one[1].isoformat() if one and one[1] else None,
                        "date_max": one[2].isoformat() if one and one[2] else None,
                        "board_type": one[3] if one else None,
                    }
                    if ts_code
                    else None
                ),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _ensure_quality_schema(self) -> None:
        """保证 session/train_ready 等质量列存在（旧库兼容）。"""
        alters = [
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS session_flag VARCHAR(16)",
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS is_one_word SMALLINT",
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS is_halted SMALLINT",
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS train_ready SMALLINT",
        ]
        with self.engine.begin() as conn:
            for sql in alters:
                conn.execute(sa_text(sql))

    def _trade_calendar_open(self) -> List[date]:
        sql = """
            SELECT cal_date
            FROM ref_trade_cal
            WHERE is_open = 1
              AND exchange IN ('SSE', 'SZSE')
            ORDER BY cal_date
        """
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql)).fetchall()
        dates = sorted({r[0] if not isinstance(r[0], datetime) else r[0].date() for r in rows})
        if dates:
            return dates
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT DISTINCT trade_date FROM ml_train_daily ORDER BY 1"
            )).fetchall()
        return [r[0] if not isinstance(r[0], datetime) else r[0].date() for r in rows]

    def _fill_gaps_one(self, ts_code: str) -> Dict[str, Any]:
        """
        P06：个股首末日之间、交易日历上缺失的开市日插入停牌占位行（OHLC=前收）。
        避免 LEAD 跳日把标签变成「多日后高低 / 今日收盘」。
        """
        code = self.selective.normalize_ts_code(ts_code)
        cal = self._trade_calendar_open()
        cal_set = set(cal)
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, open, high, low, close,
                           industry_code, industry_name, board_type, industry_l1
                    FROM ml_train_daily
                    WHERE symbol = :c
                    ORDER BY trade_date
                    """
                ),
                {"c": code},
            ).mappings().fetchall()
        if len(rows) < 2:
            return {"ok": True, "symbol": code, "inserted": 0}
        have: Dict[date, Any] = {}
        for r in rows:
            d = r["trade_date"]
            if isinstance(d, datetime):
                d = d.date()
            have[d] = r
        dmin, dmax = min(have), max(have)
        missing = [d for d in cal if dmin < d < dmax and d in cal_set and d not in have]
        if not missing:
            return {"ok": True, "symbol": code, "inserted": 0}

        live: Dict[date, Any] = {}
        with self.engine.connect() as conn:
            live_rows = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, open, high, low, close, vol, amount
                    FROM stock_daily
                    WHERE ts_code = :c AND trade_date > :a AND trade_date < :b
                      AND COALESCE(vol, 0) > 0
                    """
                ),
                {"c": code, "a": dmin, "b": dmax},
            ).mappings().fetchall()
        for lr in live_rows:
            d = lr["trade_date"]
            if isinstance(d, datetime):
                d = d.date()
            live[d] = lr

        insert_sql = sa_text(
            """
            INSERT INTO ml_train_daily (
              symbol, trade_date, industry_code, industry_name,
              board_type, industry_l1,
              open, high, low, close,
              ret_1, range_1, body_pct, close_location,
              session_flag, is_one_word, is_halted,
              y_high, y_low, train_ready
            ) VALUES (
              :symbol, :trade_date, :industry_code, :industry_name,
              :board_type, :industry_l1,
              :open, :high, :low, :close,
              :ret_1, :range_1, :body_pct, :close_location,
              :session_flag, :is_one_word, :is_halted,
              NULL, NULL, 0
            )
            ON CONFLICT (symbol, trade_date) DO NOTHING
            """
        )
        last_px = None
        last_meta = None
        ordered_have = sorted(have)
        hi = 0
        payload: List[Dict[str, Any]] = []
        for d in missing:
            while hi < len(ordered_have) and ordered_have[hi] < d:
                last_meta = have[ordered_have[hi]]
                last_px = last_meta["close"]
                hi += 1
            if last_px in (None, 0) and d not in live:
                continue
            meta = last_meta or {}
            if d in live:
                lr = live[d]
                o = float(lr["open"] or last_px or 0)
                h = float(lr["high"] or o)
                l = float(lr["low"] or o)
                c = float(lr["close"] or o)
                row = {
                    "open": o, "high": h, "low": l, "close": c,
                    "ret_1": (c / float(last_px) - 1.0) if last_px else None,
                    "range_1": (h - l) / float(last_px) if last_px else None,
                    "body_pct": (c - o) / o if o else None,
                    "close_location": 0.5 if h == l else ((c - l) / (h - l) if h != l else 0.5),
                    "session_flag": SESSION_NORMAL,
                    "is_one_word": 1 if o == h == l == c else 0,
                    "is_halted": 0,
                }
            else:
                px = float(last_px)
                row = {
                    "open": px, "high": px, "low": px, "close": px,
                    "ret_1": 0.0, "range_1": 0.0, "body_pct": 0.0,
                    "close_location": 0.5,
                    "session_flag": SESSION_HALT,
                    "is_one_word": 1,
                    "is_halted": 1,
                }
            payload.append({
                "symbol": code,
                "trade_date": d,
                "industry_code": meta.get("industry_code") if meta else None,
                "industry_name": meta.get("industry_name") if meta else None,
                "board_type": meta.get("board_type") if meta else None,
                "industry_l1": meta.get("industry_l1") if meta else None,
                **row,
            })
        if payload:
            with self.engine.begin() as conn:
                conn.execute(insert_sql, payload)
        return {"ok": True, "symbol": code, "inserted": len(payload)}

    def _fix_labels_one(self, ts_code: str) -> Dict[str, Any]:
        """P01/P09：按该票有序 LEAD 重算次日标签；末日留空。"""
        code = self.selective.normalize_ts_code(ts_code)
        sql = """
            UPDATE ml_train_daily t SET
              y_high = y.y_high,
              y_low  = y.y_low,
              train_ready = CASE
                WHEN y.y_high IS NULL OR y.y_low IS NULL THEN 0
                ELSE COALESCE(t.train_ready, 0)
              END
            FROM (
              SELECT symbol, trade_date,
                     CASE WHEN close > 0 AND nh IS NOT NULL THEN nh / close - 1 END AS y_high,
                     CASE WHEN close > 0 AND nl IS NOT NULL THEN nl / close - 1 END AS y_low
              FROM (
                SELECT symbol, trade_date, close,
                       LEAD(high) OVER (PARTITION BY symbol ORDER BY trade_date) AS nh,
                       LEAD(low)  OVER (PARTITION BY symbol ORDER BY trade_date) AS nl
                FROM ml_train_daily
                WHERE symbol = :c
              ) s
            ) y
            WHERE t.symbol = y.symbol AND t.trade_date = y.trade_date
              AND t.symbol = :c
        """
        with self.engine.begin() as conn:
            res = conn.execute(sa_text(sql), {"c": code})
            n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else None
        return {"ok": True, "symbol": code, "updated": n}

    def fill_one(
        self,
        ts_code: str = "000001.SZ",
        start_date: str = _DEFAULT_START,
        end_date: Optional[str] = None,
        *,
        skip_env_check: bool = False,
        upsert_chunk: int = _UPSERT_CHUNK,
        fetch_index: bool = True,
    ) -> Dict[str, Any]:
        """
        拉取单只股票 [start, end] 日线（Datahubco 补缺）→ 计算特征 → 写入 ml_train_daily。
        内存策略：只保留这一只股票的日线与特征 DataFrame。
        采集时即遵守 P01/P03–P06/P08/P09（标签、不填 0、一字/停牌、停牌占位、OHLC 夹紧）。
        """
        if not skip_env_check:
            env = self.check_env()
            if not env.get("ready"):
                return {"ok": False, "error": "环境不足", "env": env}

        code = self.selective.normalize_ts_code(ts_code)
        if not code:
            return {"ok": False, "error": "无效 ts_code"}
        start = _parse_date(start_date) or date(2016, 1, 1)
        end = _parse_date(end_date) or date.today()
        # 预热：多拉一段历史，让 ret_60/ATR/均线在 start 当日即可非空
        warm_start = start - timedelta(days=_WARMUP_CAL_DAYS)

        # 1) Datahubco 补日线区间（含预热，失败重试 3 次）
        try:
            ens = _with_retry(
                lambda: self.selective.ensure_range(
                    code, warm_start.isoformat(), end.isoformat()
                ),
                what=f"ensure_range:{code}",
            )
        except Exception as e:
            return {"ok": False, "error": f"Datahubco 补日线失败: {e}"}
        if not ens.get("ok") and not ens.get("skipped"):
            if ens.get("error"):
                return {"ok": False, "error": f"Datahubco 补日线失败: {ens.get('error')}", "ensure": ens}

        # 2) 读本地日线（含预热）
        daily = self._load_stock_daily(code, warm_start, end)
        if daily.empty:
            return {
                "ok": False,
                "error": f"{code} 在 {warm_start}~{end} 无日线（Datahubco 补数后仍空）",
                "ensure": ens,
            }

        meta = self._stock_meta(code)
        basic = self._load_daily_basic(code, warm_start, end)
        index_rets = self._load_index_rets(warm_start, end) if fetch_index else pd.DataFrame()
        industry_panel = self._load_industry_panel(
            meta.get("industry_code"),
            warm_start,
            end,
            industry_name=meta.get("industry_name"),
        )
        market_amt = self._load_market_amount_panel(warm_start, end)

        feat = self.compute_features_one(
            code,
            daily,
            meta=meta,
            basic=basic,
            index_rets=index_rets,
            industry_panel=industry_panel,
            market_amount_ratio=market_amt,
        )
        # 只写 [start, end]，预热行仅用于滚动计算
        if not feat.empty:
            feat = feat.loc[
                (feat["trade_date"] >= start) & (feat["trade_date"] <= end)
            ].reset_index(drop=True)
            feat = self._coerce_local_feature_nulls(feat)
        before = len(feat)
        null_stats = {}
        if before > 0:
            for col in ("ret_1", "ret_60", "turnover_rate", "hs300_ret_1", "industry_ret_5", "beta_60", "y_high", "train_ready"):
                if col in feat.columns:
                    null_stats[col] = int(feat[col].isna().sum())
        self._ensure_quality_schema()
        written = self._upsert_features(feat, chunk_size=upsert_chunk)

        # P06：首末日之间缺开市日 → 停牌占位，避免 LEAD 跳日
        gaps = self._fill_gaps_one(code)
        # 占位行插入后按 LEAD 重算次日标签（P01/P09）
        labels = self._fix_labels_one(code)

        basic_n = int(len(basic)) if basic is not None else 0
        ind_n = int(len(industry_panel)) if industry_panel is not None else 0

        del daily, basic, feat, index_rets, industry_panel
        gc.collect()

        st = self.status(code)
        return {
            "ok": True,
            "action": "fill_one",
            "ts_code": code,
            "name": meta.get("name"),
            "board_type": meta.get("board_type"),
            "industry_code": meta.get("industry_code"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "warmup_start": warm_start.isoformat(),
            "bars_written": before,
            "rows_upserted": written,
            "halt_gaps_inserted": gaps.get("inserted", 0),
            "labels_refixed": labels.get("updated"),
            "daily_basic_rows": basic_n,
            "industry_panel_rows": ind_n,
            "null_counts_in_batch": null_stats,
            "ensure_range": {
                "ok": ens.get("ok"),
                "skipped": ens.get("skipped"),
                "fetched": ens.get("fetched"),
                "upserted": ens.get("upserted") or ens.get("stats"),
                "missing_before": (ens.get("before") or {}).get("missing") if isinstance(ens.get("before"), dict) else ens.get("missing"),
            },
            "symbol_stats": st.get("symbol_stats"),
            "note": (
                "已遵守 P01–P09/P11 采集约定（次日标签、一字/停牌标记、不填 0、停牌占位、train_ready）。"
                "截面/薄日/指数广播请用 action=fill_complete 做表内补全。"
            ),
            "memory_hint": "单票处理完毕已 gc；全市场请用 fill_batch 循环，勿一次加载多票。",
        }

    def _coerce_local_feature_nulls(self, feat: pd.DataFrame) -> pd.DataFrame:
        """
        本地可计算特征的空值兜底（不含需全市场截面的字段）。

        P03/P09：滚动收益、标签、指数/行业收益等禁止 fillna(0)；
        P04：close_location 空时填 0.5；字符串填「未知」。
        """
        if feat is None or feat.empty:
            return feat
        out = feat.copy()
        cross_deferred = {
            "market_adv_decl_ratio",
            "market_limit_up_count",
            "market_limit_down_count",
            "industry_breadth",
            "industry_rank_ret_5",
            "stock_rank_industry",
        }
        str_cols = {
            "board_type", "industry_l1", "industry_code", "industry_name",
            "symbol", "session_flag",
        }
        for col in out.columns:
            if col in ("trade_date",) or col in cross_deferred:
                continue
            # 标签 / 滚动特征 / train_ready：保留 NaN，禁止填 0
            if col in LABEL_COLS or col in _NEVER_ZERO_FILL_COLS:
                if col == "close_location":
                    s = pd.to_numeric(out[col], errors="coerce")
                    out[col] = s.fillna(0.5)
                continue
            if col in str_cols:
                fill_s = SESSION_NORMAL if col == "session_flag" else "未知"
                out[col] = out[col].ffill().bfill().fillna(fill_s)
                continue
            if pd.api.types.is_numeric_dtype(out[col]) or out[col].dtype == object:
                s = pd.to_numeric(out[col], errors="coerce")
                if s.notna().any() or col in FEATURE_COLS or col in AUX_COLS:
                    if col == "close_location":
                        out[col] = s.fillna(0.5)
                    elif col in ("is_one_word", "is_halted"):
                        out[col] = s.fillna(0).astype(int)
                    else:
                        # 仅对「0 无业务歧义」的技术比值做有限 ffill，仍不硬填 0
                        out[col] = s.ffill().bfill()
        return out

    def validate_no_nulls(
        self,
        ts_code: str = "000001.SZ",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        *,
        columns: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """检查指定区间 REQUIRED_COLS 是否仍有 NULL。"""
        code = self.selective.normalize_ts_code(ts_code)
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        cols = list(columns or REQUIRED_COLS)
        # 仅检查表中实际存在的列
        with self.engine.connect() as conn:
            existing = {
                str(r[0])
                for r in conn.execute(
                    sa_text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='ml_train_daily'
                        """
                    )
                ).fetchall()
            }
        cols = [c for c in cols if c in existing]
        if not cols:
            return {"ok": False, "error": "无可用校验列"}

        where = ["symbol = :c"]
        params: Dict[str, Any] = {"c": code}
        if start:
            where.append("trade_date >= :a")
            params["a"] = start
        if end:
            where.append("trade_date <= :b")
            params["b"] = end
        where_sql = " AND ".join(where)
        null_expr = " OR ".join(f"{c} IS NULL" for c in cols)
        sql = f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN {null_expr} THEN 1 ELSE 0 END) AS null_rows
            FROM ml_train_daily
            WHERE {where_sql}
        """
        per_col_sql = ", ".join(
            f"SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END) AS {c}" for c in cols
        )
        detail_sql = f"""
            SELECT {per_col_sql}
            FROM ml_train_daily
            WHERE {where_sql}
        """
        with self.engine.connect() as conn:
            row = conn.execute(sa_text(sql), params).mappings().fetchone()
            detail = conn.execute(sa_text(detail_sql), params).mappings().fetchone()
        total = int(row["total"] or 0) if row else 0
        null_rows = int(row["null_rows"] or 0) if row else 0
        null_counts = {
            c: int(detail[c] or 0)
            for c in cols
            if detail is not None and int(detail[c] or 0) > 0
        }
        return {
            "ok": total > 0 and null_rows == 0,
            "action": "validate_no_nulls",
            "ts_code": code,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "total_rows": total,
            "null_rows": null_rows,
            "null_counts": null_counts,
        }

    # ─── check_null：按行断点续查无 NULL 区间 ─────────

    def _check_null_progress_path(self) -> Path:
        path = _CHECK_NULL_PROGRESS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_check_null_progress(self) -> Dict[str, Any]:
        path = self._check_null_progress_path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning("读取 check_null 进度失败: %s", e)
            return {}

    def _save_check_null_progress(self, data: Dict[str, Any]) -> None:
        path = self._check_null_progress_path()
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _check_null_range_key(
        self,
        start: Optional[date],
        end: Optional[date],
    ) -> str:
        a = start.isoformat() if start else "*"
        b = end.isoformat() if end else "*"
        return f"{a}:{b}"

    def _required_cols_in_table(self) -> List[str]:
        with self.engine.connect() as conn:
            existing = {
                str(r[0])
                for r in conn.execute(
                    sa_text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='ml_train_daily'
                        """
                    )
                ).fetchall()
            }
        return [c for c in REQUIRED_COLS if c in existing]

    def _row_null_cols(self, row: Any, cols: Sequence[str]) -> List[str]:
        null_cols: List[str] = []
        for c in cols:
            if c not in getattr(row, "index", []):
                continue
            v = row[c]
            if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
                null_cols.append(c)
        return null_cols

    def _classify_null_cols(self, null_cols: Sequence[str]) -> Dict[str, List[str]]:
        cross = [c for c in null_cols if c in _CROSS_SECTION_COLS]
        rank = [c for c in null_cols if c in _INDUSTRY_RANK_COLS]
        local = [
            c
            for c in null_cols
            if c not in _CROSS_SECTION_COLS and c not in _INDUSTRY_RANK_COLS
        ]
        return {"cross_section": cross, "industry_rank": rank, "local": local}

    def _repair_nulls_for_dates(
        self,
        ts_code: str,
        null_cols: Sequence[str],
        date_from: date,
        date_to: date,
        *,
        allow_defaults: bool = True,
    ) -> Dict[str, Any]:
        """
        按缺失列类型调用对应补数：
        - 截面字段 → enrich_cross_section
        - industry_rank_ret_5 → enrich_industry_rank
        - 其余本地特征 → fill_one（含预热）
        仍失败时可 defaults 兜底。
        """
        code = self.selective.normalize_ts_code(ts_code)
        groups = self._classify_null_cols(null_cols)
        steps: Dict[str, Any] = {"groups": groups}
        d0 = date_from.isoformat()
        d1 = date_to.isoformat()

        if groups["local"]:
            try:
                fill = self.fill_one(
                    ts_code=code,
                    start_date=d0,
                    end_date=d1,
                    skip_env_check=True,
                    fetch_index=True,
                )
                steps["fill_one"] = {
                    "ok": fill.get("ok"),
                    "rows_upserted": fill.get("rows_upserted"),
                    "error": fill.get("error"),
                    "null_cols": groups["local"][:12],
                }
            except Exception as e:
                steps["fill_one"] = {"ok": False, "error": str(e)}

        if groups["industry_rank"]:
            try:
                rank = self.enrich_industry_rank(
                    ts_code=code,
                    start_date=d0,
                    end_date=d1,
                    skip_env_check=True,
                    fetch_sw_proxy=True,
                )
                steps["enrich_industry_rank"] = {
                    "ok": rank.get("ok"),
                    "updated": rank.get("updated"),
                    "error": rank.get("error"),
                }
            except Exception as e:
                steps["enrich_industry_rank"] = {"ok": False, "error": str(e)}

        if groups["cross_section"]:
            try:
                # 截面按日拉全市场，只补本批缺口日期
                xs = self.enrich_cross_section(
                    ts_code=code,
                    start_date=d0,
                    end_date=d1,
                    skip_env_check=True,
                )
                steps["enrich_cross_section"] = {
                    "ok": xs.get("ok"),
                    "updated": xs.get("updated"),
                    "failed": xs.get("failed"),
                    "dates_targeted": xs.get("dates_targeted"),
                    "error": xs.get("error"),
                }
                # 仍有失败时再试一轮（内部已有 3 次远端重试）
                if int(xs.get("failed") or 0) > 0:
                    xs2 = self.enrich_cross_section(
                        ts_code=code,
                        start_date=d0,
                        end_date=d1,
                        skip_env_check=True,
                    )
                    steps["enrich_cross_section_retry"] = {
                        "ok": xs2.get("ok"),
                        "updated": xs2.get("updated"),
                        "failed": xs2.get("failed"),
                    }
            except Exception as e:
                steps["enrich_cross_section"] = {"ok": False, "error": str(e)}

        patched = 0
        if allow_defaults:
            # 远端仍缺时兜底，保证本批可继续往下查
            need_cross = bool(groups["cross_section"] or groups["industry_rank"])
            if need_cross:
                patched += self._patch_cross_section_defaults(code, date_from, date_to)
            if groups["local"] or need_cross:
                # 复查仍有空再数值兜底
                left = self.validate_no_nulls(code, d0, d1)
                if not left.get("ok"):
                    patched += self._patch_remaining_feature_nulls(code, date_from, date_to)
            steps["patch_defaults"] = {"patched_rows": patched}

        after = self.validate_no_nulls(code, d0, d1)
        steps["validate_after"] = {
            "ok": after.get("ok"),
            "null_rows": after.get("null_rows"),
            "null_counts": after.get("null_counts"),
        }
        return {
            "ok": bool(after.get("ok")),
            "date_from": d0,
            "date_to": d1,
            "steps": steps,
        }

    def check_null(
        self,
        ts_code: str = "000001.SZ",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        *,
        limit: int = _CHECK_NULL_DEFAULT_ROWS,
        from_row: Optional[int] = None,
        reset: bool = False,
        auto_fill: bool = True,
        allow_defaults: bool = True,
    ) -> Dict[str, Any]:
        """
        按 trade_date 顺序检查训练表该票各行是否含 NULL。

        - 返回本批「无 NULL」的连续行号区间（1-based）
        - 发现 NULL 时（auto_fill=True）自动调用 fill_one /
          enrich_industry_rank / enrich_cross_section 补数后重检
        - 进度写入 data/ml_train_check_null_progress.json，下次从 next_row 续查
        """
        code = self.selective.normalize_ts_code(ts_code)
        if not code:
            return {"ok": False, "error": "无效 ts_code"}
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        range_key = self._check_null_range_key(start, end)
        cols = self._required_cols_in_table()
        if not cols:
            return {"ok": False, "error": "无可用校验列"}

        where = ["symbol = :c"]
        params: Dict[str, Any] = {"c": code}
        if start:
            where.append("trade_date >= :a")
            params["a"] = start
        if end:
            where.append("trade_date <= :b")
            params["b"] = end
        where_sql = " AND ".join(where)

        with self.engine.connect() as conn:
            total = int(
                conn.execute(
                    sa_text(f"SELECT COUNT(*) FROM ml_train_daily WHERE {where_sql}"),
                    params,
                ).scalar()
                or 0
            )
        if total <= 0:
            return {
                "ok": False,
                "action": "check_null",
                "ts_code": code,
                "error": "训练表无该股票（或指定区间）数据，请先 fill_complete / fill_one",
                "total_rows": 0,
            }

        progress_all = self._load_check_null_progress()
        prev = progress_all.get(code) if isinstance(progress_all.get(code), dict) else {}
        if reset or (prev and prev.get("range_key") != range_key):
            prev = {}

        if from_row is not None and int(from_row) > 0:
            start_row = int(from_row)
        else:
            start_row = int(prev.get("next_row") or 1)
        start_row = max(1, min(start_row, total + 1))
        batch = max(1, int(limit or _CHECK_NULL_DEFAULT_ROWS))
        end_row = min(total, start_row + batch - 1)

        if start_row > total:
            marker = {
                "range_key": range_key,
                "start_date": start.isoformat() if start else None,
                "end_date": end.isoformat() if end else None,
                "total_rows": total,
                "clean_from_row": int(prev.get("clean_from_row") or 1),
                "clean_to_row": int(prev.get("clean_to_row") or total),
                "next_row": total + 1,
                "done": True,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            progress_all[code] = marker
            self._save_check_null_progress(progress_all)
            return {
                "ok": True,
                "action": "check_null",
                "ts_code": code,
                "total_rows": total,
                "checked_from_row": start_row,
                "checked_to_row": total,
                "clean_from_row": marker["clean_from_row"],
                "clean_to_row": marker["clean_to_row"],
                "next_row": total + 1,
                "done": True,
                "has_null_in_batch": False,
                "repairs": [],
                "note": "已检查到末尾，全部覆盖完毕。若要重头查请 reset=true。",
                "progress_file": str(self._check_null_progress_path()),
            }

        select_cols = [c for c in cols if c != "trade_date"]
        col_sql = ", ".join(f"t.{c}" for c in select_cols)
        sql = f"""
            WITH ordered AS (
                SELECT
                    ROW_NUMBER() OVER (ORDER BY t.trade_date) AS rn,
                    t.trade_date,
                    {col_sql}
                FROM ml_train_daily t
                WHERE {where_sql}
            )
            SELECT * FROM ordered
            WHERE rn BETWEEN :from_rn AND :to_rn
            ORDER BY rn
        """

        def _load_batch(from_rn: int, to_rn: int) -> pd.DataFrame:
            qparams = dict(params)
            qparams["from_rn"] = from_rn
            qparams["to_rn"] = to_rn
            return pd.read_sql(sa_text(sql), self.engine, params=qparams)

        def _scan(df: pd.DataFrame) -> Tuple[
            Optional[int], Optional[str], List[str], Optional[int], int, List[Dict[str, Any]]
        ]:
            """返回 first_null_row/date/cols, clean_to, checked_rows, null_hits."""
            clean_to: Optional[int] = None
            first_null_row: Optional[int] = None
            first_null_date: Optional[str] = None
            first_null_cols: List[str] = []
            checked = 0
            hits: List[Dict[str, Any]] = []
            for _, row in df.iterrows():
                rn = int(row["rn"])
                checked += 1
                null_cols = self._row_null_cols(row, cols)
                if null_cols:
                    td = row.get("trade_date")
                    if hasattr(td, "date"):
                        td = td.date()
                    if hasattr(td, "isoformat"):
                        d_iso = td.isoformat()
                        d_obj = td if isinstance(td, date) else _parse_date(d_iso)
                    else:
                        d_iso = str(td)[:10] if td is not None else None
                        d_obj = _parse_date(d_iso)
                    hits.append(
                        {
                            "row": rn,
                            "trade_date": d_iso,
                            "trade_date_obj": d_obj,
                            "null_cols": null_cols,
                        }
                    )
                    if first_null_row is None:
                        first_null_row = rn
                        first_null_date = d_iso
                        first_null_cols = null_cols
                        clean_to = rn - 1 if rn > start_row else None
                else:
                    if first_null_row is None:
                        clean_to = rn
            return first_null_row, first_null_date, first_null_cols, clean_to, checked, hits

        df = _load_batch(start_row, end_row)
        if df.empty:
            return {
                "ok": False,
                "action": "check_null",
                "ts_code": code,
                "error": f"未读到行 {start_row}~{end_row}",
                "total_rows": total,
            }

        (
            first_null_row,
            first_null_date,
            first_null_cols,
            clean_to,
            checked_rows,
            null_hits,
        ) = _scan(df)

        repairs: List[Dict[str, Any]] = []
        # 发现 NULL → 对本批所有缺口日期一次性补数，再重扫
        if auto_fill and null_hits:
            all_null_cols: List[str] = []
            dates: List[date] = []
            for h in null_hits:
                all_null_cols.extend(h["null_cols"])
                if h.get("trade_date_obj"):
                    dates.append(h["trade_date_obj"])
            uniq_cols = list(dict.fromkeys(all_null_cols))
            if dates:
                d_from = min(dates)
                d_to = max(dates)
                logger.info(
                    "check_null 发现 %d 行含 NULL，自动补数 %s %s~%s cols=%s",
                    len(null_hits),
                    code,
                    d_from,
                    d_to,
                    uniq_cols[:12],
                )
                repair = self._repair_nulls_for_dates(
                    code,
                    uniq_cols,
                    d_from,
                    d_to,
                    allow_defaults=allow_defaults,
                )
                repairs.append(repair)
                # 补数后重扫本批
                df = _load_batch(start_row, end_row)
                (
                    first_null_row,
                    first_null_date,
                    first_null_cols,
                    clean_to,
                    checked_rows,
                    null_hits,
                ) = _scan(df)
                # 仍有缺口再补一轮（通常是截面单日失败）
                if null_hits and auto_fill:
                    dates2 = [h["trade_date_obj"] for h in null_hits if h.get("trade_date_obj")]
                    cols2 = list(
                        dict.fromkeys(c for h in null_hits for c in h["null_cols"])
                    )
                    if dates2:
                        repair2 = self._repair_nulls_for_dates(
                            code,
                            cols2,
                            min(dates2),
                            max(dates2),
                            allow_defaults=allow_defaults,
                        )
                        repairs.append(repair2)
                        df = _load_batch(start_row, end_row)
                        (
                            first_null_row,
                            first_null_date,
                            first_null_cols,
                            clean_to,
                            checked_rows,
                            null_hits,
                        ) = _scan(df)

        if first_null_row is None:
            clean_to = end_row
            next_row = end_row + 1
            done = next_row > total
        else:
            next_row = first_null_row
            done = False

        prev_clean_from = int(prev.get("clean_from_row") or 0)
        prev_clean_to = int(prev.get("clean_to_row") or 0)
        if start_row == 1:
            overall_from = 1 if clean_to and clean_to >= 1 else None
            overall_to = clean_to
        elif prev_clean_to >= start_row - 1 and prev_clean_from == 1:
            overall_from = 1
            if clean_to and clean_to >= start_row:
                overall_to = clean_to
            else:
                overall_to = prev_clean_to
        else:
            overall_from = prev_clean_from or None
            overall_to = prev_clean_to or None
            if clean_to and clean_to >= start_row and not overall_from:
                overall_from = start_row
                overall_to = clean_to

        marker = {
            "range_key": range_key,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "total_rows": total,
            "clean_from_row": overall_from,
            "clean_to_row": overall_to,
            "next_row": next_row,
            "last_batch_from": start_row,
            "last_batch_to": end_row,
            "last_batch_clean_to": clean_to,
            "first_null_row": first_null_row,
            "first_null_date": first_null_date,
            "first_null_cols": first_null_cols[:20],
            "repairs_count": len(repairs),
            "done": done and first_null_row is None,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        progress_all[code] = marker
        self._save_check_null_progress(progress_all)

        batch_clean = clean_to is not None and clean_to >= start_row and first_null_row is None
        repaired_ok = bool(repairs) and all(r.get("ok") for r in repairs)
        return {
            "ok": first_null_row is None,
            "action": "check_null",
            "ts_code": code,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "total_rows": total,
            "checked_from_row": start_row,
            "checked_to_row": end_row,
            "checked_rows": checked_rows,
            "batch_clean_from_row": start_row if (clean_to and clean_to >= start_row) else None,
            "batch_clean_to_row": clean_to,
            "clean_from_row": overall_from,
            "clean_to_row": overall_to,
            "next_row": next_row,
            "done": bool(marker["done"]),
            "has_null_in_batch": first_null_row is not None,
            "first_null_row": first_null_row,
            "first_null_date": first_null_date,
            "first_null_cols": first_null_cols,
            "auto_fill": bool(auto_fill),
            "repairs": repairs,
            "repaired_ok": repaired_ok if repairs else None,
            "reset_used": bool(reset),
            "progress_file": str(self._check_null_progress_path()),
            "next_hint": (
                f"第 {overall_from}~{overall_to} 行无 NULL；已到末尾。"
                if marker["done"] and overall_from and overall_to
                else (
                    f"本批第 {start_row}~{clean_to} 行无 NULL"
                    + (f"（已自动补数 {len(repairs)} 次）" if repairs else "")
                    + f"；下次从第 {next_row} 行继续。"
                    if batch_clean
                    else (
                        f"第 {first_null_row} 行（{first_null_date}）仍有 NULL：{first_null_cols[:8]}；"
                        f"已尝试自动补数 {len(repairs)} 次；下次仍从第 {next_row} 行重试。"
                        if first_null_row
                        else f"下次从第 {next_row} 行继续。"
                    )
                )
            ),
        }

    def _patch_cross_section_defaults(
        self,
        ts_code: str,
        start: Optional[date],
        end: Optional[date],
    ) -> int:
        """截面字段在 3 次重试仍失败时的最后兜底，保证无 NULL。"""
        code = self.selective.normalize_ts_code(ts_code)
        sql = """
            UPDATE ml_train_daily SET
              market_adv_decl_ratio = COALESCE(market_adv_decl_ratio, 1.0),
              market_limit_up_count = COALESCE(market_limit_up_count, 0),
              market_limit_down_count = COALESCE(market_limit_down_count, 0),
              industry_breadth = COALESCE(industry_breadth, 0.0),
              stock_rank_industry = COALESCE(stock_rank_industry, 0.5),
              industry_rank_ret_5 = COALESCE(industry_rank_ret_5, 0.5),
              market_amount_ratio = COALESCE(market_amount_ratio, 1.0),
              updated_at = CURRENT_TIMESTAMP
            WHERE symbol = :c
              AND (
                market_adv_decl_ratio IS NULL OR market_limit_up_count IS NULL
                OR market_limit_down_count IS NULL OR industry_breadth IS NULL
                OR stock_rank_industry IS NULL OR industry_rank_ret_5 IS NULL
                OR market_amount_ratio IS NULL
              )
        """
        params: Dict[str, Any] = {"c": code}
        if start:
            sql += " AND trade_date >= :a"
            params["a"] = start
        if end:
            sql += " AND trade_date <= :b"
            params["b"] = end
        with self.engine.begin() as conn:
            res = conn.execute(sa_text(sql), params)
            return int(res.rowcount or 0)

    def _local_polish_table(self) -> Dict[str, Any]:
        """
        表内本地 polish（P02/P07/P08/P11）：
        夹紧 OHLC、广播指数/行业、表内截面、session、train_ready。
        不打远端 daily 全市场 API。
        """
        from tools.ml_train_sanitize_toolkit import MlTrainSanitizeToolkit

        san = MlTrainSanitizeToolkit()
        out: Dict[str, Any] = {"ok": True}
        out["ensure_schema"] = san.ensure_schema()
        out["clamp_ohlc"] = san.clamp_ohlc()
        out["fix_close_location"] = san.fix_close_location()
        out["broadcast_market"] = san.broadcast_market()
        out["fill_cross_section"] = san.fill_cross_section()
        out["mark_session"] = san.mark_session()
        out["fix_labels"] = san.fix_labels()
        out["mark_train_ready"] = san.mark_train_ready()
        return out

    def _validate_train_ready_rows(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """校验 train_ready=1 的行核心列非空（允许预热/末日等 train_ready=0 有空）。"""
        code = self.selective.normalize_ts_code(ts_code)
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        need = [
            "open", "high", "low", "close",
            "y_high", "y_low",
            "ret_1", "ret_60",
            "session_flag", "close_location",
            "hs300_ret_1", "stock_rank_industry",
        ]
        with self.engine.connect() as conn:
            existing = {
                str(r[0])
                for r in conn.execute(
                    sa_text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='ml_train_daily'
                        """
                    )
                ).fetchall()
            }
        cols = [c for c in need if c in existing]
        where = ["symbol = :c", "train_ready = 1"]
        params: Dict[str, Any] = {"c": code}
        if start:
            where.append("trade_date >= :a")
            params["a"] = start
        if end:
            where.append("trade_date <= :b")
            params["b"] = end
        where_sql = " AND ".join(where)
        null_expr = " OR ".join(f"{c} IS NULL" for c in cols) if cols else "FALSE"
        sql = f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN ({null_expr}) THEN 1 ELSE 0 END) AS null_rows
            FROM ml_train_daily
            WHERE {where_sql}
        """
        null_counts: Dict[str, int] = {}
        with self.engine.connect() as conn:
            row = conn.execute(sa_text(sql), params).fetchone()
            total = int(row[0] or 0) if row else 0
            null_rows = int(row[1] or 0) if row else 0
            for c in cols:
                n = conn.execute(
                    sa_text(
                        f"SELECT COUNT(*) FROM ml_train_daily WHERE {where_sql} AND {c} IS NULL"
                    ),
                    params,
                ).scalar()
                null_counts[c] = int(n or 0)
            ready_all = conn.execute(
                sa_text(
                    f"""
                    SELECT
                      SUM(CASE WHEN train_ready = 1 THEN 1 ELSE 0 END),
                      COUNT(*)
                    FROM ml_train_daily
                    WHERE symbol = :c
                      {"AND trade_date >= :a" if start else ""}
                      {"AND trade_date <= :b" if end else ""}
                    """
                ),
                params,
            ).fetchone()
        return {
            "ok": null_rows == 0 and total >= 0,
            "ts_code": code,
            "train_ready_rows": total,
            "null_rows_among_ready": null_rows,
            "null_counts": null_counts,
            "symbol_ready": int(ready_all[0] or 0) if ready_all else 0,
            "symbol_total": int(ready_all[1] or 0) if ready_all else 0,
            "train_filter": "WHERE train_ready = 1",
        }

    def fill_complete(
        self,
        ts_code: str = "000001.SZ",
        start_date: str = _DEFAULT_START,
        end_date: Optional[str] = None,
        *,
        skip_env_check: bool = False,
        upsert_chunk: int = _UPSERT_CHUNK,
        allow_defaults: bool = True,
        use_remote_cross_section: bool = False,
    ) -> Dict[str, Any]:
        """
        完整采集（LightGBM 友好，P01–P11）：
          fill_one（次日标签 / 不填 0 / 一字停牌 / 停牌占位）
          → 表内本地 polish（截面、指数广播、train_ready）
          → 校验 train_ready=1 行核心列非空

        默认不再依赖远端 daily 全市场 API 补截面（P02）。
        use_remote_cross_section=True 时保留旧 enrich 路径作为可选补充。
        """
        if not skip_env_check:
            env = self.check_env()
            if not env.get("ready"):
                return {"ok": False, "error": "环境不足", "env": env}

        code = self.selective.normalize_ts_code(ts_code)
        start = _parse_date(start_date) or date(2016, 1, 1)
        end = _parse_date(end_date) or date.today()
        steps: Dict[str, Any] = {}

        fill = self.fill_one(
            ts_code=code,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            skip_env_check=True,
            upsert_chunk=upsert_chunk,
            fetch_index=True,
        )
        steps["fill_one"] = {
            "ok": fill.get("ok"),
            "rows_upserted": fill.get("rows_upserted"),
            "bars_written": fill.get("bars_written"),
            "halt_gaps_inserted": fill.get("halt_gaps_inserted"),
            "error": fill.get("error"),
        }
        if not fill.get("ok"):
            return {
                "ok": False,
                "action": "fill_complete",
                "ts_code": code,
                "steps": steps,
                "error": fill.get("error") or "fill_one 失败",
            }

        # P02/P07/P08/P11：表内本地补全（主路径）
        try:
            steps["local_polish"] = self._local_polish_table()
        except Exception as e:
            steps["local_polish"] = {"ok": False, "error": str(e)}

        if use_remote_cross_section:
            rank = self.enrich_industry_rank(
                ts_code=code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                skip_env_check=True,
                fetch_sw_proxy=True,
            )
            steps["enrich_industry_rank"] = {
                "ok": rank.get("ok"),
                "updated": rank.get("updated"),
                "error": rank.get("error"),
            }
            xs = self.enrich_cross_section(
                ts_code=code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                skip_env_check=True,
            )
            steps["enrich_cross_section_remote"] = {
                "ok": xs.get("ok"),
                "updated": xs.get("updated"),
                "failed": xs.get("failed"),
            }

        patched = 0
        validation = self._validate_train_ready_rows(code, start.isoformat(), end.isoformat())
        if (not validation.get("ok")) and allow_defaults:
            # 仅对截面类做温和默认；绝不对 ret_*/y_* 填 0
            patched = self._patch_cross_section_defaults(code, start, end)
            try:
                steps["local_polish_retry"] = self._local_polish_table()
            except Exception as e:
                steps["local_polish_retry"] = {"ok": False, "error": str(e)}
            validation = self._validate_train_ready_rows(code, start.isoformat(), end.isoformat())

        steps["patch_defaults"] = {"patched_rows": patched, "allow_defaults": allow_defaults}
        steps["validate_train_ready"] = validation

        ready_n = int(validation.get("train_ready_rows") or 0)
        return {
            "ok": bool(validation.get("ok")),
            "action": "fill_complete",
            "ts_code": code,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "train_ready_rows": ready_n,
            "symbol_ready": validation.get("symbol_ready"),
            "symbol_total": validation.get("symbol_total"),
            "null_rows_among_ready": validation.get("null_rows_among_ready"),
            "null_counts": validation.get("null_counts"),
            "steps": steps,
            "train_filter": "WHERE train_ready = 1",
            "p10_note": "industry_l1 可能等于 industry_name，训练时只用其中一个。",
            "next_hint": (
                f"train_ready=1 共 {ready_n} 行，可进 LightGBM。"
                if validation.get("ok")
                else "train_ready=1 行仍有空值，请查看 null_counts；全市场可再跑 sanitize fix。"
            ),
        }

    def _patch_remaining_feature_nulls(
        self,
        ts_code: str,
        start: Optional[date],
        end: Optional[date],
    ) -> int:
        """
        最后兜底：仅对「填 0 无业务歧义」的列补默认；
        P03/P09：绝不对 ret_*/y_*/滚动波动/指数行业收益 fillna(0)。
        """
        code = self.selective.normalize_ts_code(ts_code)
        with self.engine.connect() as conn:
            existing = {
                str(r[0])
                for r in conn.execute(
                    sa_text(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='ml_train_daily'
                        """
                    )
                ).fetchall()
            }
        skip = set(_NEVER_ZERO_FILL_COLS) | set(LABEL_COLS) | {
            "board_type", "industry_l1", "session_flag", "train_ready",
        }
        num_cols = [
            c
            for c in FEATURE_COLS + AUX_COLS
            if c in existing and c not in skip
        ]
        str_cols = [
            c for c in ("board_type", "industry_l1", "industry_code", "industry_name", "session_flag")
            if c in existing
        ]
        sets = []
        for c in num_cols:
            if c == "close_location":
                sets.append(f"{c} = COALESCE({c}, 0.5)")
            elif c in ("is_one_word", "is_halted"):
                sets.append(f"{c} = COALESCE({c}, 0)")
            else:
                sets.append(f"{c} = COALESCE({c}, 0)")
        for c in str_cols:
            default = SESSION_NORMAL if c == "session_flag" else "未知"
            sets.append(f"{c} = COALESCE({c}, '{default}')")
        if not sets:
            return 0
        sql = f"""
            UPDATE ml_train_daily SET
              {", ".join(sets)},
              updated_at = CURRENT_TIMESTAMP
            WHERE symbol = :c
        """
        params: Dict[str, Any] = {"c": code}
        if start:
            sql += " AND trade_date >= :a"
            params["a"] = start
        if end:
            sql += " AND trade_date <= :b"
            params["b"] = end
        with self.engine.begin() as conn:
            res = conn.execute(sa_text(sql), params)
            return int(res.rowcount or 0)

    def _build_l2_industry_ret5_panel(self, start: date, end: date) -> pd.DataFrame:
        """
        东财二级行业日涨跌 → 滚动 5 日复利收益面板（列=行业代码，行=交易日）。
        industry_rank_ret_5 = 该行业当日 ret_5 在全部二级行业中的分位。
        """
        df = pd.read_sql(
            sa_text(
                """
                SELECT ts_code, trade_date, pct_change
                FROM sector_dc_index_daily
                WHERE industry_level = '东财二级行业'
                  AND trade_date BETWEEN :a AND :b
                  AND pct_change IS NOT NULL
                ORDER BY ts_code, trade_date
                """
            ),
            self.engine,
            params={"a": start, "b": end},
        )
        if df.empty:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["pct"] = pd.to_numeric(df["pct_change"], errors="coerce") / 100.0
        wide = (
            df.pivot_table(index="trade_date", columns="ts_code", values="pct", aggfunc="last")
            .sort_index()
        )
        # 复利 5 日：prod(1+r) - 1
        ret5 = (1.0 + wide).rolling(5, min_periods=5).apply(lambda s: float(np.prod(s) - 1.0), raw=True)
        return ret5

    def enrich_industry_rank(
        self,
        ts_code: str = "000001.SZ",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        *,
        skip_env_check: bool = False,
        fetch_sw_proxy: bool = True,
    ) -> Dict[str, Any]:
        """
        补全 industry_rank_ret_5：
        1) 必要时用申万行业日线补目标板块历史（如银行Ⅱ→801780.SI）
        2) 用全市场东财二级行业 pct_change 算各行业 ret_5
        3) 对目标行业做横截面分位写回
        """
        if not skip_env_check:
            env = self.check_env()
            if not env.get("ready"):
                return {"ok": False, "error": "环境不足", "env": env}

        code = self.selective.normalize_ts_code(ts_code)
        meta = self._stock_meta(code)
        ind_code = meta.get("industry_code")
        ind_name = meta.get("industry_name") or ""
        if not ind_code:
            return {"ok": False, "error": f"{code} 无 industry_code，无法计算行业分位"}

        # 日期范围：默认覆盖该票训练表已有区间，并向前多取 10 天做 ret_5
        with self.engine.connect() as conn:
            span = conn.execute(
                sa_text(
                    "SELECT MIN(trade_date), MAX(trade_date) FROM ml_train_daily WHERE symbol=:c"
                ),
                {"c": code},
            ).fetchone()
        if not span or not span[0]:
            return {"ok": False, "error": "训练表尚无该股票行，请先 fill_one"}
        start = _parse_date(start_date) or span[0]
        end = _parse_date(end_date) or span[1]
        warm = start - timedelta(days=20)

        # 目标行业历史不足时，尝试申万代理写入 sector_dc_index_daily
        sw_proxy_used = None
        if fetch_sw_proxy:
            with self.engine.connect() as conn:
                n_hist = int(
                    conn.execute(
                        sa_text(
                            """
                            SELECT COUNT(*) FROM sector_dc_index_daily
                            WHERE ts_code=:c AND trade_date BETWEEN :a AND :b
                              AND pct_change IS NOT NULL
                            """
                        ),
                        {"c": ind_code, "a": warm, "b": end},
                    ).scalar()
                    or 0
                )
            if n_hist < 200:
                sw_code = None
                try:
                    sw_map = self.datahub.sector._sw_name_map(end)
                    for key in (ind_name, ind_name.replace("Ⅱ", "").replace("I", "").replace("Ⅰ", ""), "银行"):
                        key = (key or "").strip()
                        if key in sw_map:
                            sw_code = sw_map[key]
                            break
                    if sw_code is None and "银行" in ind_name:
                        sw_code = "801780.SI"
                except Exception as e:
                    logger.warning("申万名称映射失败: %s", e)
                    if "银行" in ind_name:
                        sw_code = "801780.SI"
                if sw_code:
                    try:
                        raw = _with_retry(
                            lambda: self.datahub.client.query(
                                "sw_daily",
                                ts_code=sw_code,
                                start_date=warm.strftime("%Y%m%d"),
                                end_date=end.strftime("%Y%m%d"),
                            ),
                            what=f"sw_daily:{sw_code}",
                        )
                        rows = []
                        if raw is not None and not raw.empty:
                            work = raw.copy()
                            work.columns = [str(c).lower() for c in work.columns]
                            for _, item in work.iterrows():
                                raw_day = str(item.get("trade_date") or "").replace("-", "")[:8]
                                pct = item.get("pct_change")
                                if len(raw_day) != 8 or pct is None or pd.isna(pct):
                                    continue
                                rows.append(
                                    {
                                        "ts_code": ind_code,
                                        "trade_date": datetime.strptime(raw_day, "%Y%m%d").date(),
                                        "name": ind_name or ind_code,
                                        "pct_change": float(pct),
                                        "idx_type": "行业板块",
                                        "industry_level": "东财二级行业",
                                    }
                                )
                        if rows:
                            sql = sa_text(
                                """
                                INSERT INTO sector_dc_index_daily
                                  (ts_code, trade_date, name, pct_change, idx_type, industry_level)
                                VALUES
                                  (:ts_code, :trade_date, :name, :pct_change, :idx_type, :industry_level)
                                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                                  pct_change = EXCLUDED.pct_change,
                                  name = EXCLUDED.name,
                                  industry_level = EXCLUDED.industry_level,
                                  updated_at = CURRENT_TIMESTAMP
                                """
                            )
                            with self.engine.begin() as conn:
                                for i in range(0, len(rows), 500):
                                    conn.execute(sql, rows[i : i + 500])
                            sw_proxy_used = {"sw_code": sw_code, "rows": len(rows)}
                    except Exception as e:
                        logger.warning("申万行业日线补数失败 %s: %s", sw_code, e)

        panel = self._build_l2_industry_ret5_panel(warm, end)
        if panel.empty or ind_code not in panel.columns:
            return {
                "ok": False,
                "error": f"行业收益面板缺少 {ind_code}，请先 fill_market_history 或检查板块代码",
                "sw_proxy_used": sw_proxy_used,
                "panel_cols": int(panel.shape[1]) if panel is not None else 0,
            }

        # 分位：行业当日 ret_5 在全部行业中的排名比例（含自身）
        target = panel[ind_code]
        ranks = panel.rank(axis=1, method="average", pct=True)[ind_code]

        # 写回训练表
        upd = sa_text(
            """
            UPDATE ml_train_daily SET
              industry_ret_5 = COALESCE(:industry_ret_5, industry_ret_5),
              industry_rank_ret_5 = :industry_rank_ret_5,
              updated_at = CURRENT_TIMESTAMP
            WHERE symbol = :symbol AND trade_date = :trade_date
            """
        )
        updated = 0
        samples = []
        with self.engine.begin() as conn:
            for d, rank_v in ranks.items():
                if d < start or d > end:
                    continue
                if pd.isna(rank_v):
                    continue
                try:
                    ret_v = target.loc[d] if d in target.index else None
                    ret_f = float(ret_v) if ret_v is not None and not pd.isna(ret_v) else None
                except Exception:
                    ret_f = None
                conn.execute(
                    upd,
                    {
                        "symbol": code,
                        "trade_date": d,
                        "industry_ret_5": ret_f,
                        "industry_rank_ret_5": float(rank_v),
                    },
                )
                updated += 1
                if len(samples) < 5:
                    samples.append(
                        {
                            "trade_date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                            "industry_ret_5": ret_f,
                            "industry_rank_ret_5": float(rank_v),
                        }
                    )

        with self.engine.connect() as conn:
            covered = int(
                conn.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM ml_train_daily "
                        "WHERE symbol=:c AND industry_rank_ret_5 IS NOT NULL"
                    ),
                    {"c": code},
                ).scalar()
                or 0
            )
            total = int(
                conn.execute(
                    sa_text("SELECT COUNT(*) FROM ml_train_daily WHERE symbol=:c"),
                    {"c": code},
                ).scalar()
                or 0
            )

        return {
            "ok": True,
            "action": "enrich_industry_rank",
            "ts_code": code,
            "industry_code": ind_code,
            "industry_name": ind_name,
            "sw_proxy_used": sw_proxy_used,
            "industries_in_panel": int(panel.shape[1]),
            "updated": updated,
            "covered": covered,
            "total_rows": total,
            "samples": samples,
            "note": "industry_rank_ret_5 = 该行业5日收益在全部东财二级行业中的分位（0~1）。",
        }

    def enrich_cross_section(
        self,
        ts_code: str = "000001.SZ",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        *,
        limit_days: Optional[int] = None,
        skip_env_check: bool = False,
    ) -> Dict[str, Any]:
        """
        调用 Datahubco `daily(trade_date=...)` 补全市场涨跌家数/涨跌停，
        并用板块成分计算 industry_breadth / stock_rank_industry。
        按交易日循环，注意耗时与限流；可用 limit_days 先补最近 N 天或指定日。
        """
        if not skip_env_check:
            env = self.check_env()
            if not env.get("ready"):
                return {"ok": False, "error": "环境不足", "env": env}

        code = self.selective.normalize_ts_code(ts_code)
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        meta = self._stock_meta(code)

        # 成分股（行业广度/分位）
        member_codes: List[str] = []
        ind_code = meta.get("industry_code")
        if ind_code:
            try:
                packed = self.datahub.get_sector_members(ts_code=ind_code, write_back=True)
                members = packed.get("members") or packed.get("data") or []
                for m in members:
                    if isinstance(m, dict):
                        c = m.get("ts_code") or m.get("con_code")
                        if c:
                            member_codes.append(str(c))
            except Exception as e:
                logger.warning("成分获取失败 %s: %s", ind_code, e)

        sql = """
            SELECT trade_date FROM ml_train_daily
            WHERE symbol = :c
              AND (market_adv_decl_ratio IS NULL OR market_limit_up_count IS NULL
                   OR industry_breadth IS NULL OR stock_rank_industry IS NULL)
        """
        params: Dict[str, Any] = {"c": code}
        if start:
            sql += " AND trade_date >= :a"
            params["a"] = start
        if end:
            sql += " AND trade_date <= :b"
            params["b"] = end
        sql += " ORDER BY trade_date"
        dates_df = pd.read_sql(sa_text(sql), self.engine, params=params)
        if dates_df.empty:
            return {"ok": True, "action": "enrich_cross_section", "updated": 0, "note": "无需补全"}
        dates = [pd.to_datetime(x).date() for x in dates_df["trade_date"].tolist()]
        if limit_days is not None and int(limit_days) > 0:
            dates = dates[: int(limit_days)]

        # 上证成交额代理（若仍空）
        if dates:
            amt_panel = self._load_market_amount_panel(min(dates) - timedelta(days=40), max(dates))
        else:
            amt_panel = pd.Series(dtype=float)

        updated = 0
        failed = 0
        samples = []
        upd_sql = sa_text(
            """
            UPDATE ml_train_daily SET
              market_amount_ratio = COALESCE(:market_amount_ratio, market_amount_ratio),
              market_adv_decl_ratio = COALESCE(:market_adv_decl_ratio, market_adv_decl_ratio),
              market_limit_up_count = COALESCE(:market_limit_up_count, market_limit_up_count),
              market_limit_down_count = COALESCE(:market_limit_down_count, market_limit_down_count),
              industry_breadth = COALESCE(:industry_breadth, industry_breadth),
              stock_rank_industry = COALESCE(:stock_rank_industry, stock_rank_industry),
              updated_at = CURRENT_TIMESTAMP
            WHERE symbol = :symbol AND trade_date = :trade_date
            """
        )
        for i, d in enumerate(dates):
            xs = {"ok": False, "error": "not attempted"}
            for attempt in range(_FETCH_RETRIES):
                xs = self._fetch_market_cross_section_day(
                    d, member_codes=member_codes, focus_code=code
                )
                if xs.get("ok"):
                    break
                logger.warning(
                    "截面补全 %s 失败 (%d/%d): %s",
                    d,
                    attempt + 1,
                    _FETCH_RETRIES,
                    xs.get("error"),
                )
                time.sleep(_FETCH_RETRY_SLEEP * (attempt + 1))
            if not xs.get("ok"):
                failed += 1
                if len(samples) < 8:
                    samples.append({"trade_date": d.isoformat(), "error": xs.get("error")})
                # 失败后稍作停顿，避免连续打爆限流
                time.sleep(1.0)
                continue
            rank = xs.get("stock_rank_industry")
            if rank is None:
                rank = 0.5
            amt_ratio = None
            if d in amt_panel.index:
                try:
                    amt_ratio = float(amt_panel.loc[d])
                    if amt_ratio != amt_ratio:  # nan
                        amt_ratio = None
                except Exception:
                    amt_ratio = None
            with self.engine.begin() as conn:
                conn.execute(
                    upd_sql,
                    {
                        "symbol": code,
                        "trade_date": d,
                        "market_amount_ratio": amt_ratio,
                        "market_adv_decl_ratio": xs.get("market_adv_decl_ratio"),
                        "market_limit_up_count": xs.get("market_limit_up_count"),
                        "market_limit_down_count": xs.get("market_limit_down_count"),
                        "industry_breadth": xs.get("industry_breadth"),
                        "stock_rank_industry": rank,
                    },
                )
            updated += 1
            if updated <= 3:
                samples.append({
                    "trade_date": d.isoformat(),
                    "adv_decl": xs.get("market_adv_decl_ratio"),
                    "limit_up": xs.get("market_limit_up_count"),
                    "breadth": xs.get("industry_breadth"),
                    "rank": rank,
                    "universe": xs.get("universe"),
                })
            if updated % 10 == 0:
                logger.info(
                    "截面补全进度 %s %d/%d updated=%d failed=%d",
                    code, i + 1, len(dates), updated, failed,
                )
                gc.collect()
            # 轻微限流，降低 429 / 超时概率
            time.sleep(0.35)

        return {
            "ok": failed == 0,
            "action": "enrich_cross_section",
            "ts_code": code,
            "dates_targeted": len(dates),
            "updated": updated,
            "failed": failed,
            "member_count": len(member_codes),
            "samples": samples,
            "next_hint": (
                "若 failed>0 请重跑 enrich_cross_section；"
                "完整流程请用 action=fill_complete。"
                if failed
                else "截面字段已补全。"
            ),
        }

    def fill_batch(
        self,
        ts_codes: Optional[Sequence[str]] = None,
        start_date: str = _DEFAULT_START,
        end_date: Optional[str] = None,
        *,
        limit: int = 1,
        offset: int = 0,
        only_mainboard: bool = False,
        skip_existing: bool = True,
        skip_env_check: bool = False,
    ) -> Dict[str, Any]:
        """按股票池循环采集；默认 limit=1 保护内存。"""
        if not skip_env_check:
            env = self.check_env()
            if not env.get("ready"):
                return {"ok": False, "error": "环境不足", "env": env}

        start = _parse_date(start_date) or date(2016, 1, 1)
        end = _parse_date(end_date) or date.today()

        if ts_codes:
            universe = [self.selective.normalize_ts_code(str(c)) for c in ts_codes]
            universe = [c for c in universe if c]
            pool_source = "ts_codes"
        else:
            # 优先全市场清单表 ref_market_universe（含 board_type）；空则回退 ref_stock_basic
            universe = []
            pool_source = "ref_market_universe"
            try:
                df = pd.read_sql(
                    sa_text(
                        """
                        SELECT ts_code, name, market, board_type
                        FROM ref_market_universe
                        WHERE list_status = 'L'
                        ORDER BY ts_code
                        """
                    ),
                    self.engine,
                )
            except Exception:
                df = pd.DataFrame()
            if df is None or df.empty:
                pool_source = "ref_stock_basic"
                df = pd.read_sql(
                    sa_text(
                        """
                        SELECT ts_code, name, market FROM ref_stock_basic
                        WHERE list_status = 'L'
                        ORDER BY ts_code
                        """
                    ),
                    self.engine,
                )
            for _, r in df.iterrows():
                code = str(r["ts_code"])
                board = str(r.get("board_type") or "") or classify_board_type(
                    code,
                    name=str(r.get("name") or ""),
                    market=str(r.get("market") or ""),
                )
                if only_mainboard and board not in ("主板", "ST"):
                    if board != "主板":
                        continue
                universe.append(code)
            if not universe:
                return {
                    "ok": False,
                    "action": "fill_batch",
                    "error": (
                        "股票池为空：请先调用 datahubco_data__fill_market_universe "
                        "action=fill 写入 ref_market_universe（约 5575 只）。"
                    ),
                    "universe_total": 0,
                    "pool_source": pool_source,
                }

        total = len(universe)
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 1), 1000))  # 单次最多 1000 只
        batch = universe[offset : offset + limit]

        results = []
        ok_n = fail_n = skip_n = 0
        for code in batch:
            if skip_existing:
                with self.engine.connect() as conn:
                    n = int(
                        conn.execute(
                            sa_text(
                                "SELECT COUNT(*) FROM ml_train_daily "
                                "WHERE symbol=:c AND trade_date BETWEEN :a AND :b"
                            ),
                            {"c": code, "a": start, "b": end},
                        ).scalar()
                        or 0
                    )
                if n >= 200:  # 已有较完整历史则跳过
                    skip_n += 1
                    results.append({"ts_code": code, "status": "skipped_existing", "rows": n})
                    continue
            try:
                packed = self.fill_one(
                    ts_code=code,
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    skip_env_check=True,
                )
                if packed.get("ok"):
                    ok_n += 1
                    results.append({
                        "ts_code": code,
                        "status": "ok",
                        "rows_upserted": packed.get("rows_upserted"),
                        "board_type": packed.get("board_type"),
                    })
                else:
                    fail_n += 1
                    results.append({"ts_code": code, "status": "failed", "error": packed.get("error")})
            except Exception as e:
                fail_n += 1
                results.append({"ts_code": code, "status": "failed", "error": str(e)[:200]})
            gc.collect()

        polish = None
        if ok_n > 0:
            try:
                polish = self._local_polish_table()
            except Exception as e:
                polish = {"ok": False, "error": str(e)}

        next_offset = offset + len(batch)
        return {
            "ok": True,
            "action": "fill_batch",
            "pool_source": pool_source,
            "universe_total": total,
            "batch_size": len(batch),
            "offset": offset,
            "next_offset": next_offset if next_offset < total else None,
            "has_more": next_offset < total,
            "ok_count": ok_n,
            "failed_count": fail_n,
            "skipped_count": skip_n,
            "local_polish": polish,
            "results": results,
            "train_filter": "WHERE train_ready = 1",
            "next_hint": (
                f"继续：offset={next_offset}, limit={limit}"
                if next_offset < total
                else "本批切片完成；已做表内截面/train_ready polish，训练只用 train_ready=1"
            ),
        }

    def run(
        self,
        action: str = "check_env",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        action = (action or "check_env").strip().lower()
        if action in ("check_env", "env", "check"):
            return self.check_env(
                require_ram_gb=float(kwargs.get("require_ram_gb") or _MIN_RAM_GB)
            )
        if action in ("status",):
            return self.status(kwargs.get("ts_code"))
        if action in ("fill_one", "fill-one", "one"):
            return self.fill_one(
                ts_code=str(kwargs.get("ts_code") or "000001.SZ"),
                start_date=str(kwargs.get("start_date") or _DEFAULT_START),
                end_date=kwargs.get("end_date"),
                skip_env_check=bool(kwargs.get("skip_env_check") or False),
                upsert_chunk=int(kwargs.get("upsert_chunk") or _UPSERT_CHUNK),
                fetch_index=False if kwargs.get("fetch_index") is False else True,
            )
        if action in ("fill_batch", "fill-batch", "batch"):
            return self.fill_batch(
                ts_codes=kwargs.get("ts_codes"),
                start_date=str(kwargs.get("start_date") or _DEFAULT_START),
                end_date=kwargs.get("end_date"),
                limit=int(kwargs.get("limit") or 1),
                offset=int(kwargs.get("offset") or 0),
                only_mainboard=bool(kwargs.get("only_mainboard") or False),
                skip_existing=False if kwargs.get("skip_existing") is False else True,
                skip_env_check=bool(kwargs.get("skip_env_check") or False),
            )
        if action in ("enrich_cross_section", "enrich", "enrich-cross-section"):
            return self.enrich_cross_section(
                ts_code=str(kwargs.get("ts_code") or "000001.SZ"),
                start_date=kwargs.get("start_date"),
                end_date=kwargs.get("end_date"),
                limit_days=int(kwargs["limit_days"]) if kwargs.get("limit_days") not in (None, "") else None,
                skip_env_check=bool(kwargs.get("skip_env_check") or False),
            )
        if action in ("enrich_industry_rank", "industry_rank", "enrich-industry-rank"):
            return self.enrich_industry_rank(
                ts_code=str(kwargs.get("ts_code") or "000001.SZ"),
                start_date=kwargs.get("start_date"),
                end_date=kwargs.get("end_date"),
                skip_env_check=bool(kwargs.get("skip_env_check") or False),
                fetch_sw_proxy=False if kwargs.get("fetch_sw_proxy") is False else True,
            )
        if action in ("fill_complete", "fill-complete", "complete"):
            return self.fill_complete(
                ts_code=str(kwargs.get("ts_code") or "000001.SZ"),
                start_date=str(kwargs.get("start_date") or _DEFAULT_START),
                end_date=kwargs.get("end_date"),
                skip_env_check=bool(kwargs.get("skip_env_check") or False),
                upsert_chunk=int(kwargs.get("upsert_chunk") or _UPSERT_CHUNK),
                allow_defaults=False if kwargs.get("allow_defaults") is False else True,
                use_remote_cross_section=bool(kwargs.get("use_remote_cross_section") or False),
            )
        if action in ("local_polish", "polish", "polish_table"):
            try:
                return {"ok": True, "action": "local_polish", **self._local_polish_table()}
            except Exception as e:
                return {"ok": False, "action": "local_polish", "error": str(e)}
        if action in ("validate_no_nulls", "validate"):
            return self.validate_no_nulls(
                ts_code=str(kwargs.get("ts_code") or "000001.SZ"),
                start_date=kwargs.get("start_date"),
                end_date=kwargs.get("end_date"),
            )
        if action in ("check_null", "check-null", "check_nulls"):
            raw_limit = kwargs.get("limit")
            # 面板批大小默认常为 1；对 check_null 表示「本批检查行数」，过小则用 200
            if raw_limit in (None, ""):
                check_limit = _CHECK_NULL_DEFAULT_ROWS
            else:
                check_limit = int(raw_limit)
                if check_limit <= 1 and kwargs.get("from_row") in (None, ""):
                    check_limit = _CHECK_NULL_DEFAULT_ROWS
            from_row = kwargs.get("from_row")
            reset_raw = kwargs.get("reset")
            if isinstance(reset_raw, bool):
                reset_flag = reset_raw
            else:
                reset_flag = str(reset_raw or "").strip().lower() in (
                    "1", "true", "yes", "y", "on",
                )
            auto_raw = kwargs.get("auto_fill")
            if auto_raw is None or auto_raw == "":
                auto_flag = True
            elif isinstance(auto_raw, bool):
                auto_flag = auto_raw
            else:
                auto_flag = str(auto_raw).strip().lower() not in (
                    "0", "false", "no", "n", "off",
                )
            allow_raw = kwargs.get("allow_defaults")
            if allow_raw is None or allow_raw == "":
                allow_flag = True
            elif isinstance(allow_raw, bool):
                allow_flag = allow_raw
            else:
                allow_flag = str(allow_raw).strip().lower() not in (
                    "0", "false", "no", "n", "off",
                )
            return self.check_null(
                ts_code=str(kwargs.get("ts_code") or "000001.SZ"),
                start_date=kwargs.get("start_date"),
                end_date=kwargs.get("end_date"),
                limit=check_limit,
                from_row=int(from_row) if from_row not in (None, "") else None,
                reset=reset_flag,
                auto_fill=auto_flag,
                allow_defaults=allow_flag,
            )
        return {
            "ok": False,
            "error": (
                "action 应为 check_env / fill_one / fill_complete / fill_batch / local_polish / "
                "enrich_cross_section / enrich_industry_rank / "
                "check_null / validate_no_nulls / status"
            ),
        }

    async def run_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.run, **kwargs)


ml_train_toolkit = MlTrainToolkit()
