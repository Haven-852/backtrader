"""
ml_train_daily 质量检查与修复工具。

目的：把训练表修到「连续、可用、标签正确」，让 LightGBM 能学，而不是被空洞特征/错误标签带偏。
不一次把全表装进内存；截面从本表统计，不再逐日打远端 API。

典型用法：
  action=audit     只检查，给出问题清单和数量
  action=catalog   问题 → 解决办法对照
  action=fix       按顺序执行全部本地修复
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text as sa_text

from tools.ml_train_toolkit import (
    SESSION_HALT,
    SESSION_NORMAL,
    SESSION_ONE_WORD_DOWN,
    SESSION_ONE_WORD_FLAT,
    SESSION_ONE_WORD_UP,
)

logger = logging.getLogger(__name__)

_WARMUP_BARS = 60
_BATCH_PROGRESS_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "ml_train_sanitize_batch_progress.json"
)


def _parse_iso_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
_SLICE_PREVIEW_COLS = [
    "symbol", "trade_date", "open", "high", "low", "close",
    "y_high", "y_low", "close_location",
    "session_flag", "is_one_word", "is_halted",
    "ret_1", "hs300_ret_1",
    "stock_rank_industry", "industry_rank_ret_5", "market_adv_decl_ratio",
    "train_ready",
]

# 训练时用 WHERE train_ready = 1
PROBLEMS: List[Dict[str, str]] = [
    {
        "id": "P01_LABEL_WRONG",
        "title": "y_high / y_low 用了当日 K 线，不是次日收益",
        "why_lgbm": (
            "旧公式 y_high=(high-open)/open、y_low=(low-close)/close 是当天振幅，"
            "模型会把「已知的当日高低」当成预测目标，等于标签泄漏，上线后完全失效。"
        ),
        "fix": (
            "改为 y_high=次日最高/当日收盘-1，y_low=次日最低/当日收盘-1。"
            "每只股票最后一天没有次日，标签留空，train_ready=0。"
        ),
        "action": "fix_labels",
    },
    {
        "id": "P02_CROSS_SECTION_EMPTY",
        "title": "截面特征几乎全空（行业分位/涨跌家数）",
        "why_lgbm": (
            "stock_rank_industry 等列全市场只有约 1 只股票有值。"
            "LightGBM 会把「谁被填过」当成特征，或整列当缺失，截面信息学不到。"
        ),
        "fix": "用表内当日截面重算分位、涨跌家数、行业广度，不打远端 API。",
        "action": "fill_cross_section",
    },
    {
        "id": "P03_WARMUP_OR_NULL_ROLLING",
        "title": "滚动特征在序列开头为空，或被 0 填掉",
        "why_lgbm": (
            "ret_60/均线需要 60 根历史。空值还好；若被填成 0，模型会当成「60 日收益恰好为 0」。"
        ),
        "fix": "每只股票前 60 根 train_ready=0，禁止进训练集。剩余 ret_1 空值用 OHLC 回填。",
        "action": "mark_train_ready",
    },
    {
        "id": "P04_ONE_WORD_CLOSE_LOCATION",
        "title": "一字板 close_location 为 NULL 或被填成 0",
        "why_lgbm": "高低相同除以 0；填 0 会被当成「收在最低」，和一字板不是一回事。",
        "fix": "一字/停牌 close_location=0.5，同时用 session_flag 标明现象。",
        "action": "fix_close_location",
    },
    {
        "id": "P05_NO_SESSION_FLAG",
        "title": "没有一字/停牌标记，模型无法学习这类交易日",
        "why_lgbm": "一字涨停、一字跌停、停牌的收益分布和正常日完全不同，不标出来会被当成噪声。",
        "fix": "写入 session_flag / is_one_word / is_halted，作为 LightGBM 输入特征。",
        "action": "mark_session",
    },
    {
        "id": "P06_HALT_GAPS",
        "title": "停牌日从序列里消失，表不连续，次日标签会跳日",
        "why_lgbm": (
            "LEAD(次日) 会跳过停牌，标签变成「多日后的高低/今日收盘」，"
            "不是真正的次日收益。"
        ),
        "fix": "在个股首末日之间、交易日历上缺失的日子插入停牌占位行（OHLC=前收）。",
        "action": "fill_gaps",
    },
    {
        "id": "P07_THIN_LAST_DAY",
        "title": "最近一个交易日覆盖过薄",
        "why_lgbm": "用只有几十行的日子做验证集会严重偏科。",
        "fix": "当日行数低于中位数一半时 train_ready=0。",
        "action": "mark_train_ready",
    },
    {
        "id": "P08_OHLC_INCONSISTENT",
        "title": "OHLC 自相矛盾（开收超出高低）",
        "why_lgbm": "振幅、影线、close_location 会算出 Inf/NaN 或荒谬值。",
        "fix": "high=GREATEST(OHLC)，low=LEAST(OHLC)。",
        "action": "clamp_ohlc",
    },
    {
        "id": "P09_LAST_BAR_NO_LABEL",
        "title": "每只股票最后一根没有次日，不能当训练样本",
        "why_lgbm": "若把 y 填 0，等于教模型「明天高低都等于今天收盘」。",
        "fix": "y_high/y_low 留空，train_ready=0。",
        "action": "fix_labels",
    },
    {
        "id": "P10_INDUSTRY_L1_DUP",
        "title": "industry_l1 与 industry_name 完全相同",
        "why_lgbm": "两个类别列重复，不报错但浪费分裂，也可能让重要性失真。",
        "fix": "暂保留；训练时可只选用 industry_name。本工具不改行业分类源。",
        "action": "catalog",
    },
    {
        "id": "P11_MARKET_FEATURE_HOLES",
        "title": "少数行缺少 hs300 / 行业收益，缺失与代码相关",
        "why_lgbm": "系统性按股票缺失比随机 NaN 更伤，验证按票切分时偏差更大。",
        "fix": "按日广播已有的指数收益；行业收益用同业个股当日均值补。",
        "action": "broadcast_market",
    },
]


class MlTrainSanitizeToolkit:
    def __init__(self) -> None:
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from data_layer.db_manager import storage_manager

            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def catalog(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "action": "catalog",
            "label_definition": {
                "y_high": "次日最高价 / 当日收盘价 - 1",
                "y_low": "次日最低价 / 当日收盘价 - 1",
            },
            "train_filter": "WHERE train_ready = 1",
            "session_flag_values": [
                SESSION_NORMAL,
                SESSION_ONE_WORD_UP,
                SESSION_ONE_WORD_DOWN,
                SESSION_ONE_WORD_FLAT,
                SESSION_HALT,
            ],
            "problems": PROBLEMS,
            "fix_order": [
                "ensure_schema",
                "clamp_ohlc",
                "fix_close_location",
                "broadcast_market",
                "fill_cross_section",
                "fill_gaps",
                "mark_session",
                "fix_labels",
                "mark_train_ready",
            ],
        }

    def ensure_schema(self) -> Dict[str, Any]:
        alters = [
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS session_flag VARCHAR(16)",
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS is_one_word SMALLINT",
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS is_halted SMALLINT",
            "ALTER TABLE ml_train_daily ADD COLUMN IF NOT EXISTS train_ready SMALLINT",
            "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_ready ON ml_train_daily (train_ready, trade_date DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_date_symbol ON ml_train_daily (trade_date, symbol)",
            "COMMENT ON COLUMN ml_train_daily.y_high IS '次日最高价相对当日收盘的收益 (next_high/close-1)'",
            "COMMENT ON COLUMN ml_train_daily.y_low IS '次日最低价相对当日收盘的收益 (next_low/close-1)'",
            "COMMENT ON COLUMN ml_train_daily.session_flag IS '当日现象：正常/一字涨停/一字跌停/平盘一字/停牌'",
            "COMMENT ON COLUMN ml_train_daily.train_ready IS '1=可进 LightGBM 训练集'",
        ]
        with self.engine.begin() as conn:
            for sql in alters:
                conn.execute(sa_text(sql))
        return {"ok": True, "action": "ensure_schema", "columns_ensured": [
            "session_flag", "is_one_word", "is_halted", "train_ready"
        ]}

    def _slice_select_sql(self, limit_rows: int, from_row: int = 0) -> str:
        cols = ", ".join(_SLICE_PREVIEW_COLS)
        off = max(0, int(from_row))
        n = max(1, int(limit_rows))
        return f"""
            SELECT {cols}
            FROM ml_train_daily
            ORDER BY trade_date, symbol
            LIMIT {n} OFFSET {off}
        """

    def _rows_to_preview(self, rows: Sequence[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for k, v in list(d.items()):
                if v is None:
                    continue
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif isinstance(v, float):
                    d[k] = round(v, 6)
                else:
                    try:
                        d[k] = float(v) if k not in ("symbol", "trade_date", "session_flag") else v
                        if k not in ("symbol", "trade_date", "session_flag") and isinstance(d[k], float):
                            d[k] = round(d[k], 6)
                    except (TypeError, ValueError):
                        d[k] = str(v)
            out.append(d)
        return out

    def preview_rows(self, limit_rows: int = 200, from_row: int = 0) -> Dict[str, Any]:
        self.ensure_schema()
        sql = self._slice_select_sql(limit_rows, from_row)
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql)).mappings().fetchall()
        return {
            "ok": True,
            "action": "preview_rows",
            "limit_rows": int(limit_rows),
            "from_row": int(from_row),
            "n": len(rows),
            "rows": self._rows_to_preview(rows),
        }

    def audit(self) -> Dict[str, Any]:
        self.ensure_schema()
        sql_overview = """
            SELECT COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols,
                   MIN(trade_date)::text AS dmin, MAX(trade_date)::text AS dmax
            FROM ml_train_daily
        """
        sql_nulls = """
            SELECT
              SUM(CASE WHEN ret_1 IS NULL THEN 1 ELSE 0 END) AS ret_1_null,
              SUM(CASE WHEN ret_60 IS NULL THEN 1 ELSE 0 END) AS ret_60_null,
              SUM(CASE WHEN close_location IS NULL THEN 1 ELSE 0 END) AS close_location_null,
              SUM(CASE WHEN hs300_ret_1 IS NULL THEN 1 ELSE 0 END) AS hs300_ret_1_null,
              SUM(CASE WHEN stock_rank_industry IS NULL THEN 1 ELSE 0 END) AS stock_rank_industry_null,
              SUM(CASE WHEN industry_rank_ret_5 IS NULL THEN 1 ELSE 0 END) AS industry_rank_ret_5_null,
              SUM(CASE WHEN market_adv_decl_ratio IS NULL THEN 1 ELSE 0 END) AS market_adv_decl_ratio_null,
              SUM(CASE WHEN y_high IS NULL THEN 1 ELSE 0 END) AS y_high_null,
              SUM(CASE WHEN y_low IS NULL THEN 1 ELSE 0 END) AS y_low_null,
              SUM(CASE WHEN high < low THEN 1 ELSE 0 END) AS high_lt_low,
              SUM(CASE WHEN open > high OR open < low OR close > high OR close < low THEN 1 ELSE 0 END) AS ohlc_inconsistent,
              SUM(CASE WHEN high = low AND open = close AND high = open THEN 1 ELSE 0 END) AS one_word,
              SUM(CASE WHEN session_flag IS NULL THEN 1 ELSE 0 END) AS session_flag_null,
              SUM(CASE WHEN train_ready = 1 THEN 1 ELSE 0 END) AS train_ready_rows
            FROM ml_train_daily
        """
        sql_coverage = """
            SELECT MIN(n) AS min_day, MAX(n) AS max_day,
                   AVG(n)::int AS avg_day,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY n)::int AS median_day
            FROM (SELECT trade_date, COUNT(*) AS n FROM ml_train_daily GROUP BY trade_date) d
        """
        sql_thin = """
            SELECT trade_date::text AS trade_date, n
            FROM (SELECT trade_date, COUNT(*) AS n FROM ml_train_daily GROUP BY trade_date) d
            WHERE n < 500
            ORDER BY trade_date DESC
            LIMIT 10
        """
        sql_rank = """
            SELECT COUNT(*) FILTER (WHERE stock_rank_industry IS NOT NULL) AS rank_ok,
                   COUNT(DISTINCT symbol) FILTER (WHERE stock_rank_industry IS NOT NULL) AS symbols_with_rank
            FROM ml_train_daily
        """
        sql_label = """
            SELECT
              SUM(CASE WHEN ABS(y_high - ((high - open) / NULLIF(open,0))) < 1e-6 THEN 1 ELSE 0 END) AS still_intraday,
              SUM(CASE WHEN ABS(y_high - ((nh / NULLIF(close,0)) - 1)) < 1e-6 THEN 1 ELSE 0 END) AS already_nextday,
              COUNT(*) AS n
            FROM (
              SELECT y_high, high, open, close,
                     LEAD(high) OVER (PARTITION BY symbol ORDER BY trade_date) AS nh
              FROM ml_train_daily
              WHERE symbol IN (
                SELECT symbol FROM ml_train_daily GROUP BY symbol ORDER BY COUNT(*) DESC LIMIT 3
              )
            ) s
            WHERE y_high IS NOT NULL AND nh IS NOT NULL AND open > 0 AND close > 0
        """
        with self.engine.connect() as conn:
            conn.execute(sa_text("SET statement_timeout = '300s'"))
            overview = dict(conn.execute(sa_text(sql_overview)).mappings().one())
            nulls = dict(conn.execute(sa_text(sql_nulls)).mappings().one())
            coverage = dict(conn.execute(sa_text(sql_coverage)).mappings().one())
            thin = [dict(r) for r in conn.execute(sa_text(sql_thin)).mappings()]
            rank = dict(conn.execute(sa_text(sql_rank)).mappings().one())
            try:
                label = dict(conn.execute(sa_text(sql_label)).mappings().one())
            except Exception as e:
                label = {"error": str(e)}

        rows = int(overview.get("rows") or 0)
        findings = []
        if label and not label.get("error"):
            still = int(label.get("still_intraday") or 0)
            nxt = int(label.get("already_nextday") or 0)
            if still > nxt:
                findings.append("P01_LABEL_WRONG")
        if int(nulls.get("stock_rank_industry_null") or 0) > rows * 0.2:
            findings.append("P02_CROSS_SECTION_EMPTY")
        if int(nulls.get("ret_60_null") or 0) > 0:
            findings.append("P03_WARMUP_OR_NULL_ROLLING")
        if int(nulls.get("one_word") or 0) > 0:
            findings.append("P04_ONE_WORD_CLOSE_LOCATION")
            findings.append("P05_NO_SESSION_FLAG")
        if thin:
            findings.append("P07_THIN_LAST_DAY")
        if int(nulls.get("ohlc_inconsistent") or 0) > 0 or int(nulls.get("high_lt_low") or 0) > 0:
            findings.append("P08_OHLC_INCONSISTENT")
        if int(nulls.get("y_high_null") or 0) >= 0:
            findings.append("P09_LAST_BAR_NO_LABEL")
        findings.append("P10_INDUSTRY_L1_DUP")
        if int(nulls.get("hs300_ret_1_null") or 0) > 0:
            findings.append("P11_MARKET_FEATURE_HOLES")
        findings.append("P06_HALT_GAPS")

        return {
            "ok": True,
            "action": "audit",
            "overview": overview,
            "nulls": {k: int(v) if v is not None and not isinstance(v, str) else v for k, v in nulls.items()},
            "coverage": coverage,
            "thin_days": thin,
            "rank_coverage": rank,
            "label_check": label,
            "active_problems": list(dict.fromkeys(findings)),
            "next_hint": "先看 catalog 了解每条对应修复；再 action=fix 按顺序落地。训练只用 train_ready=1。",
        }

    def clamp_ohlc(self) -> Dict[str, Any]:
        sql = """
            UPDATE ml_train_daily SET
              high = GREATEST(open, high, low, close),
              low  = LEAST(open, high, low, close)
            WHERE open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
              AND (high < low OR open > high OR open < low OR close > high OR close < low)
        """
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            res = conn.execute(sa_text(sql))
            n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else 0
        return {"ok": True, "action": "clamp_ohlc", "updated": int(n)}

    def fix_close_location(self) -> Dict[str, Any]:
        sql = """
            UPDATE ml_train_daily SET
              close_location = CASE
                WHEN high = low THEN 0.5
                ELSE (close - low) / NULLIF(high - low, 0)
              END
            WHERE close IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
              AND (
                    close_location IS NULL
                    OR (high = low AND (close_location IS DISTINCT FROM 0.5))
                  )
        """
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            res = conn.execute(sa_text(sql))
            n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else 0
        return {"ok": True, "action": "fix_close_location", "updated": int(n)}

    def broadcast_market(self) -> Dict[str, Any]:
        sql_mkt = """
            UPDATE ml_train_daily t SET
              hs300_ret_1 = COALESCE(t.hs300_ret_1, m.hs300_ret_1),
              hs300_ret_5 = COALESCE(t.hs300_ret_5, m.hs300_ret_5),
              zz1000_ret_5 = COALESCE(t.zz1000_ret_5, m.zz1000_ret_5),
              market_volatility_20 = COALESCE(t.market_volatility_20, m.market_volatility_20),
              size_style_spread = COALESCE(t.size_style_spread, m.size_style_spread),
              market_amount_ratio = COALESCE(t.market_amount_ratio, m.market_amount_ratio)
            FROM (
              SELECT trade_date,
                     MAX(hs300_ret_1) AS hs300_ret_1,
                     MAX(hs300_ret_5) AS hs300_ret_5,
                     MAX(zz1000_ret_5) AS zz1000_ret_5,
                     MAX(market_volatility_20) AS market_volatility_20,
                     MAX(size_style_spread) AS size_style_spread,
                     MAX(market_amount_ratio) AS market_amount_ratio
              FROM ml_train_daily
              GROUP BY trade_date
            ) m
            WHERE t.trade_date = m.trade_date
              AND (t.hs300_ret_1 IS NULL OR t.hs300_ret_5 IS NULL
                   OR t.zz1000_ret_5 IS NULL OR t.market_volatility_20 IS NULL
                   OR t.size_style_spread IS NULL OR t.market_amount_ratio IS NULL)
        """
        sql_ind = """
            UPDATE ml_train_daily t SET
              industry_ret_1 = COALESCE(t.industry_ret_1, p.industry_ret_1),
              industry_ret_5 = COALESCE(t.industry_ret_5, p.industry_ret_5),
              industry_ret_20 = COALESCE(t.industry_ret_20, p.industry_ret_20),
              industry_vol_20 = COALESCE(t.industry_vol_20, p.industry_vol_20),
              stock_excess_ret_5 = COALESCE(
                  t.stock_excess_ret_5,
                  t.ret_5 - COALESCE(t.industry_ret_5, p.industry_ret_5)
              )
            FROM (
              SELECT trade_date, COALESCE(industry_code, '') AS industry_code,
                     AVG(ret_1) AS industry_ret_1,
                     AVG(ret_5) AS industry_ret_5,
                     AVG(ret_20) AS industry_ret_20,
                     STDDEV(ret_5) AS industry_vol_20
              FROM ml_train_daily
              GROUP BY 1, 2
            ) p
            WHERE t.trade_date = p.trade_date
              AND COALESCE(t.industry_code, '') = p.industry_code
              AND (t.industry_ret_1 IS NULL OR t.industry_ret_5 IS NULL
                   OR t.industry_ret_20 IS NULL OR t.stock_excess_ret_5 IS NULL)
        """
        updated = {}
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            r1 = conn.execute(sa_text(sql_mkt))
            updated["market"] = int(r1.rowcount) if r1.rowcount is not None and r1.rowcount >= 0 else 0
            r2 = conn.execute(sa_text(sql_ind))
            updated["industry"] = int(r2.rowcount) if r2.rowcount is not None and r2.rowcount >= 0 else 0
        return {"ok": True, "action": "broadcast_market", "updated": updated}

    def fill_cross_section(self) -> Dict[str, Any]:
        """用本表当日截面补分位 / 涨跌家数 / 行业广度。"""
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_day"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_ind"))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_day AS
                SELECT trade_date,
                       SUM(CASE WHEN ret_1 > 0 THEN 1 ELSE 0 END)::double precision
                         / NULLIF(SUM(CASE WHEN ret_1 < 0 THEN 1 ELSE 0 END), 0)
                         AS market_adv_decl_ratio,
                       SUM(CASE WHEN ret_1 >= 0.095 THEN 1 ELSE 0 END)::int AS market_limit_up_count,
                       SUM(CASE WHEN ret_1 <= -0.095 THEN 1 ELSE 0 END)::int AS market_limit_down_count
                FROM ml_train_daily
                GROUP BY trade_date
            """))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_ind AS
                SELECT trade_date, COALESCE(industry_code, '') AS industry_code,
                       AVG(CASE WHEN ret_1 > 0 THEN 1.0 ELSE 0.0 END) AS industry_breadth,
                       PERCENT_RANK() OVER (
                         PARTITION BY trade_date ORDER BY AVG(ret_5)
                       ) AS industry_rank_ret_5
                FROM ml_train_daily
                GROUP BY trade_date, COALESCE(industry_code, '')
            """))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_day (trade_date)"))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_ind (trade_date, industry_code)"))
            r_day = conn.execute(sa_text("""
                UPDATE ml_train_daily t SET
                  market_adv_decl_ratio = d.market_adv_decl_ratio,
                  market_limit_up_count = d.market_limit_up_count,
                  market_limit_down_count = d.market_limit_down_count
                FROM _ml_xs_day d
                WHERE t.trade_date = d.trade_date
            """))
            r_ind = conn.execute(sa_text("""
                UPDATE ml_train_daily t SET
                  industry_breadth = i.industry_breadth,
                  industry_rank_ret_5 = i.industry_rank_ret_5
                FROM _ml_xs_ind i
                WHERE t.trade_date = i.trade_date
                  AND COALESCE(t.industry_code, '') = i.industry_code
            """))
            r_rank = conn.execute(sa_text("""
                UPDATE ml_train_daily t SET
                  stock_rank_industry = r.rk
                FROM (
                  SELECT symbol, trade_date,
                         PERCENT_RANK() OVER (
                           PARTITION BY trade_date, COALESCE(industry_code, '')
                           ORDER BY ret_1
                         ) AS rk
                  FROM ml_train_daily
                  WHERE ret_1 IS NOT NULL
                ) r
                WHERE t.symbol = r.symbol AND t.trade_date = r.trade_date
            """))
        return {
            "ok": True,
            "action": "fill_cross_section",
            "updated_day_stats": int(r_day.rowcount) if r_day.rowcount is not None and r_day.rowcount >= 0 else None,
            "updated_industry": int(r_ind.rowcount) if r_ind.rowcount is not None and r_ind.rowcount >= 0 else None,
            "updated_stock_rank": int(r_rank.rowcount) if r_rank.rowcount is not None and r_rank.rowcount >= 0 else None,
        }

    def _trade_calendar(self) -> List[date]:
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
        # 日历缺失时退回训练表已出现的日期
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT DISTINCT trade_date FROM ml_train_daily ORDER BY 1"
            )).fetchall()
        return [r[0] if not isinstance(r[0], datetime) else r[0].date() for r in rows]

    def fill_gaps(
        self,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """个股首末日之间、开市日缺失的行按停牌占位插入，保证 LEAD 是真正的下一交易日。"""
        cal = self._trade_calendar()
        cal_set = set(cal)
        with self.engine.connect() as conn:
            symbols = [
                str(r[0])
                for r in conn.execute(
                    sa_text(
                        "SELECT DISTINCT symbol FROM ml_train_daily ORDER BY 1"
                    )
                ).fetchall()
            ]
        if offset:
            symbols = symbols[int(offset):]
        if limit is not None and int(limit) > 0:
            symbols = symbols[: int(limit)]

        insert_sql = sa_text(
            """
            INSERT INTO ml_train_daily (
              symbol, trade_date, industry_code, industry_name,
              board_type, industry_l1,
              open, high, low, close,
              ret_1, range_1, body_pct, close_location,
              session_flag, is_one_word, is_halted,
              y_high, y_low
            ) VALUES (
              :symbol, :trade_date, :industry_code, :industry_name,
              :board_type, :industry_l1,
              :open, :high, :low, :close,
              :ret_1, :range_1, :body_pct, :close_location,
              :session_flag, :is_one_word, :is_halted,
              NULL, NULL
            )
            ON CONFLICT (symbol, trade_date) DO NOTHING
            """
        )
        inserted = 0
        scanned = 0
        samples: List[Dict[str, Any]] = []
        for code in symbols:
            scanned += 1
            df_sql = sa_text(
                """
                SELECT trade_date, open, high, low, close,
                       industry_code, industry_name, board_type, industry_l1
                FROM ml_train_daily
                WHERE symbol = :c
                ORDER BY trade_date
                """
            )
            with self.engine.connect() as conn:
                rows = conn.execute(df_sql, {"c": code}).mappings().fetchall()
            if len(rows) < 2:
                continue
            have = {}
            for r in rows:
                d = r["trade_date"]
                if isinstance(d, datetime):
                    d = d.date()
                have[d] = r
            dmin = min(have)
            dmax = max(have)
            missing = [d for d in cal if dmin < d < dmax and d in cal_set and d not in have]
            if not missing:
                continue
            # 真实成交日（vol>0）不当停牌，从 stock_daily 抄 OHLC
            live = {}
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

            last_px = None
            last_meta = None
            ordered_have = sorted(have)
            hi = 0
            payload = []
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
                        "ret_1": (c / float(last_px) - 1.0) if last_px else 0.0,
                        "range_1": (h - l) / float(last_px) if last_px else 0.0,
                        "body_pct": (c - o) / o if o else 0.0,
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
            if dry_run:
                inserted += len(payload)
                if len(samples) < 8 and payload:
                    samples.append({"symbol": code, "missing": len(payload),
                                    "from": payload[0]["trade_date"].isoformat(),
                                    "to": payload[-1]["trade_date"].isoformat()})
                continue
            if payload:
                with self.engine.begin() as conn:
                    conn.execute(insert_sql, payload)
                inserted += len(payload)
                if len(samples) < 8:
                    samples.append({"symbol": code, "inserted": len(payload)})
            if scanned % 200 == 0:
                logger.info("fill_gaps %d/%d inserted=%d", scanned, len(symbols), inserted)
                gc.collect()
        return {
            "ok": True,
            "action": "fill_gaps",
            "symbols": len(symbols),
            "inserted": inserted,
            "dry_run": dry_run,
            "samples": samples,
            "note": "停牌占位行 OHLC=前收，session_flag=停牌；真实成交缺失日不在这里补，请 fill_one。",
        }

    def mark_session(self) -> Dict[str, Any]:
        sql_one = """
            UPDATE ml_train_daily SET
              is_one_word = CASE
                WHEN open = high AND high = low AND low = close THEN 1 ELSE 0 END
            WHERE open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL AND close IS NOT NULL
        """
        sql_halt_suspend = """
            UPDATE ml_train_daily t SET is_halted = 1
            FROM ref_suspend_d s
            WHERE s.ts_code = t.symbol AND s.trade_date = t.trade_date
              AND COALESCE(s.suspend_type, 'S') IN ('S', 's')
        """
        sql_halt_vol = """
            UPDATE ml_train_daily t SET is_halted = 1
            FROM stock_daily d
            WHERE d.ts_code = t.symbol AND d.trade_date = t.trade_date
              AND COALESCE(d.vol, 0) = 0
        """
        sql_halt_flag = """
            UPDATE ml_train_daily SET is_halted = COALESCE(is_halted, 0)
        """
        sql_flag = """
            UPDATE ml_train_daily SET
              session_flag = CASE
                WHEN COALESCE(is_halted, 0) = 1 THEN :halt
                WHEN COALESCE(is_one_word, 0) = 0 THEN :normal
                WHEN ret_1 >= (
                  CASE board_type
                    WHEN 'ST' THEN 0.0475
                    WHEN '科创板' THEN 0.19
                    WHEN '北交所' THEN 0.285
                    WHEN '创业板' THEN CASE WHEN trade_date >= DATE '2020-08-24' THEN 0.19 ELSE 0.095 END
                    ELSE 0.095
                  END
                ) THEN :up
                WHEN ret_1 <= -(
                  CASE board_type
                    WHEN 'ST' THEN 0.0475
                    WHEN '科创板' THEN 0.19
                    WHEN '北交所' THEN 0.285
                    WHEN '创业板' THEN CASE WHEN trade_date >= DATE '2020-08-24' THEN 0.19 ELSE 0.095 END
                    ELSE 0.095
                  END
                ) THEN :down
                WHEN COALESCE(is_one_word, 0) = 1 THEN :flat
                ELSE :normal
              END
        """
        counts: Dict[str, Any] = {}
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            r = conn.execute(sa_text(sql_one))
            counts["one_word"] = r.rowcount
            try:
                r = conn.execute(sa_text(sql_halt_suspend))
                counts["halt_suspend"] = r.rowcount
            except Exception as e:
                counts["halt_suspend"] = str(e)
            try:
                r = conn.execute(sa_text(sql_halt_vol))
                counts["halt_vol0"] = r.rowcount
            except Exception as e:
                counts["halt_vol0"] = str(e)
            conn.execute(sa_text(sql_halt_flag))
            r = conn.execute(
                sa_text(sql_flag),
                {
                    "halt": SESSION_HALT,
                    "normal": SESSION_NORMAL,
                    "up": SESSION_ONE_WORD_UP,
                    "down": SESSION_ONE_WORD_DOWN,
                    "flat": SESSION_ONE_WORD_FLAT,
                },
            )
            counts["session_flag"] = r.rowcount
        return {"ok": True, "action": "mark_session", "updated": counts}

    def fix_labels(self) -> Dict[str, Any]:
        """次日最高/最低 相对 当日收盘。"""
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_ylabels"))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_ylabels AS
                SELECT symbol, trade_date,
                       CASE WHEN close > 0 AND nh IS NOT NULL THEN nh / close - 1 END AS y_high,
                       CASE WHEN close > 0 AND nl IS NOT NULL THEN nl / close - 1 END AS y_low
                FROM (
                  SELECT symbol, trade_date, close,
                         LEAD(high) OVER (PARTITION BY symbol ORDER BY trade_date) AS nh,
                         LEAD(low)  OVER (PARTITION BY symbol ORDER BY trade_date) AS nl
                  FROM ml_train_daily
                ) s
            """))
            conn.execute(sa_text("CREATE INDEX ON _ml_ylabels (symbol, trade_date)"))
            res = conn.execute(sa_text("""
                UPDATE ml_train_daily t SET
                  y_high = y.y_high,
                  y_low  = y.y_low
                FROM _ml_ylabels y
                WHERE t.symbol = y.symbol AND t.trade_date = y.trade_date
            """))
            n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else None
        return {
            "ok": True,
            "action": "fix_labels",
            "updated": n,
            "definition": {
                "y_high": "LEAD(high)/close - 1",
                "y_low": "LEAD(low)/close - 1",
            },
        }

    def mark_train_ready(self) -> Dict[str, Any]:
        sql_reset = "UPDATE ml_train_daily SET train_ready = 1"
        sql_warmup = """
            UPDATE ml_train_daily t SET train_ready = 0
            FROM (
              SELECT symbol, trade_date,
                     ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date) AS rn
              FROM ml_train_daily
            ) w
            WHERE t.symbol = w.symbol AND t.trade_date = w.trade_date
              AND w.rn <= :warmup
        """
        sql_core = """
            UPDATE ml_train_daily SET train_ready = 0
            WHERE train_ready = 1 AND (
                 y_high IS NULL OR y_low IS NULL
              OR ret_1 IS NULL
              OR hs300_ret_1 IS NULL
              OR stock_rank_industry IS NULL
              OR close IS NULL OR close <= 0
              OR session_flag IS NULL
            )
        """
        sql_thin = """
            UPDATE ml_train_daily t SET train_ready = 0
            FROM (
              SELECT trade_date, COUNT(*) AS n
              FROM ml_train_daily
              GROUP BY trade_date
            ) d,
            (
              SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY n) AS med
              FROM (SELECT COUNT(*) AS n FROM ml_train_daily GROUP BY trade_date) x
            ) m
            WHERE t.trade_date = d.trade_date
              AND d.n < m.med * 0.5
        """
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            conn.execute(sa_text(sql_reset))
            conn.execute(sa_text(sql_warmup), {"warmup": _WARMUP_BARS})
            conn.execute(sa_text(sql_core))
            conn.execute(sa_text(sql_thin))
            ready = conn.execute(
                sa_text("SELECT SUM(CASE WHEN train_ready = 1 THEN 1 ELSE 0 END), COUNT(*) FROM ml_train_daily")
            ).first()
        ready_n = int(ready[0] or 0) if ready else 0
        total = int(ready[1] or 0) if ready else 0
        return {
            "ok": True,
            "action": "mark_train_ready",
            "train_ready_rows": ready_n,
            "total_rows": total,
            "train_filter": "WHERE train_ready = 1",
        }

    def _row_key(self, from_row: int) -> Optional[Dict[str, Any]]:
        """1-based 第 from_row 行的 (trade_date, symbol)；from_row=0 表示从头。"""
        if from_row <= 0:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, symbol
                    FROM ml_train_daily
                    ORDER BY trade_date, symbol
                    LIMIT 1 OFFSET :off
                    """
                ),
                {"off": int(from_row) - 1},
            ).mappings().fetchone()
        if not row:
            return None
        d = row["trade_date"]
        if isinstance(d, datetime):
            d = d.date()
        return {"trade_date": d, "symbol": str(row["symbol"])}

    def fix_slice(
        self,
        limit_rows: int = 200,
        from_row: int = 0,
        *,
        skip_gaps: bool = True,
        include_preview: bool = True,
        after_date: Optional[date] = None,
        after_symbol: Optional[str] = None,
        skip_ensure: bool = False,
        skip_train_ready: bool = False,
        skip_halt_join: bool = False,
    ) -> Dict[str, Any]:
        """修复一段行。标签用该股下一交易日高低/当日收盘。大批量请用 after_date/after_symbol 翻页。"""
        if not skip_ensure:
            self.ensure_schema()
        n = max(1, int(limit_rows))
        off = max(0, int(from_row))
        before: Dict[str, Any] = {"rows": []}
        if include_preview and after_date is None:
            before = self.preview_rows(n, off)
        steps: List[Dict[str, Any]] = []
        last_key: Optional[Dict[str, Any]] = None
        first_key: Optional[Dict[str, Any]] = None
        with self.engine.begin() as conn:
            conn.execute(sa_text("SET LOCAL statement_timeout = '600s'"))
            conn.execute(sa_text("SET LOCAL lock_timeout = '30s'"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_fix_keys"))
            if after_date is not None and after_symbol:
                conn.execute(
                    sa_text(
                        """
                        CREATE TEMP TABLE _ml_fix_keys AS
                        SELECT symbol, trade_date
                        FROM ml_train_daily
                        WHERE (trade_date, symbol) > (:d, :s)
                        ORDER BY trade_date, symbol
                        LIMIT :n
                        """
                    ),
                    {"d": after_date, "s": after_symbol, "n": n},
                )
            else:
                conn.execute(sa_text(f"""
                    CREATE TEMP TABLE _ml_fix_keys AS
                    SELECT symbol, trade_date
                    FROM ml_train_daily
                    ORDER BY trade_date, symbol
                    LIMIT {n} OFFSET {off}
                """))
            conn.execute(sa_text("CREATE INDEX ON _ml_fix_keys (symbol, trade_date)"))
            key_n = int(conn.execute(sa_text("SELECT COUNT(*) FROM _ml_fix_keys")).scalar() or 0)
            if key_n == 0:
                return {
                    "ok": True,
                    "action": "fix",
                    "scope": "slice",
                    "done": True,
                    "limit_rows": n,
                    "rows_targeted": 0,
                    "last_key": None,
                }
            first_row = conn.execute(sa_text(
                "SELECT trade_date, symbol FROM _ml_fix_keys ORDER BY trade_date, symbol LIMIT 1"
            )).mappings().one()
            last_row = conn.execute(sa_text(
                "SELECT trade_date, symbol FROM _ml_fix_keys ORDER BY trade_date DESC, symbol DESC LIMIT 1"
            )).mappings().one()
            first_key = {
                "trade_date": first_row["trade_date"].isoformat() if first_row["trade_date"] else None,
                "symbol": first_row["symbol"],
            }
            last_key = {
                "trade_date": last_row["trade_date"].isoformat() if last_row["trade_date"] else None,
                "symbol": last_row["symbol"],
            }
            last_date_obj = last_row["trade_date"]
            last_symbol_obj = last_row["symbol"]

            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_day"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_ind"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_mkt"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_peer"))
            conn.execute(sa_text("DROP TABLE IF EXISTS _ml_xs_rank"))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_mkt AS
                SELECT trade_date,
                       MAX(hs300_ret_1) AS hs300_ret_1,
                       MAX(hs300_ret_5) AS hs300_ret_5,
                       MAX(zz1000_ret_5) AS zz1000_ret_5,
                       MAX(market_volatility_20) AS market_volatility_20,
                       MAX(size_style_spread) AS size_style_spread,
                       MAX(market_amount_ratio) AS market_amount_ratio
                FROM ml_train_daily
                WHERE trade_date IN (SELECT DISTINCT trade_date FROM _ml_fix_keys)
                GROUP BY trade_date
            """))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_day AS
                SELECT trade_date,
                       SUM(CASE WHEN ret_1 > 0 THEN 1 ELSE 0 END)::double precision
                         / NULLIF(SUM(CASE WHEN ret_1 < 0 THEN 1 ELSE 0 END), 0)
                         AS market_adv_decl_ratio,
                       SUM(CASE WHEN ret_1 >= 0.095 THEN 1 ELSE 0 END)::int AS market_limit_up_count,
                       SUM(CASE WHEN ret_1 <= -0.095 THEN 1 ELSE 0 END)::int AS market_limit_down_count
                FROM ml_train_daily
                WHERE trade_date IN (SELECT DISTINCT trade_date FROM _ml_fix_keys)
                GROUP BY trade_date
            """))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_peer AS
                SELECT trade_date, COALESCE(industry_code, '') AS industry_code,
                       AVG(ret_1) AS industry_ret_1,
                       AVG(ret_5) AS industry_ret_5,
                       AVG(ret_20) AS industry_ret_20,
                       STDDEV(ret_5) AS industry_vol_20
                FROM ml_train_daily
                WHERE trade_date IN (SELECT DISTINCT trade_date FROM _ml_fix_keys)
                GROUP BY 1, 2
            """))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_ind AS
                SELECT trade_date, COALESCE(industry_code, '') AS industry_code,
                       AVG(CASE WHEN ret_1 > 0 THEN 1.0 ELSE 0.0 END) AS industry_breadth,
                       PERCENT_RANK() OVER (
                         PARTITION BY trade_date ORDER BY AVG(ret_5)
                       ) AS industry_rank_ret_5
                FROM ml_train_daily
                WHERE trade_date IN (SELECT DISTINCT trade_date FROM _ml_fix_keys)
                GROUP BY trade_date, COALESCE(industry_code, '')
            """))
            conn.execute(sa_text("""
                CREATE TEMP TABLE _ml_xs_rank AS
                SELECT symbol, trade_date,
                       PERCENT_RANK() OVER (
                         PARTITION BY trade_date, COALESCE(industry_code, '')
                         ORDER BY ret_1
                       ) AS rk
                FROM ml_train_daily
                WHERE trade_date IN (SELECT DISTINCT trade_date FROM _ml_fix_keys)
                  AND ret_1 IS NOT NULL
            """))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_mkt (trade_date)"))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_day (trade_date)"))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_peer (trade_date, industry_code)"))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_ind (trade_date, industry_code)"))
            conn.execute(sa_text("CREATE INDEX ON _ml_xs_rank (symbol, trade_date)"))
            r = conn.execute(sa_text("""
                UPDATE ml_train_daily t SET
                  high = CASE
                    WHEN t.open IS NOT NULL AND t.high IS NOT NULL
                         AND t.low IS NOT NULL AND t.close IS NOT NULL
                    THEN GREATEST(t.open, t.high, t.low, t.close) ELSE t.high END,
                  low = CASE
                    WHEN t.open IS NOT NULL AND t.high IS NOT NULL
                         AND t.low IS NOT NULL AND t.close IS NOT NULL
                    THEN LEAST(t.open, t.high, t.low, t.close) ELSE t.low END,
                  close_location = CASE
                    WHEN t.high = t.low THEN 0.5
                    WHEN t.high IS NOT NULL AND t.low IS NOT NULL AND t.close IS NOT NULL
                    THEN (t.close - t.low) / NULLIF(t.high - t.low, 0)
                    ELSE t.close_location END,
                  hs300_ret_1 = COALESCE(t.hs300_ret_1, m.hs300_ret_1),
                  hs300_ret_5 = COALESCE(t.hs300_ret_5, m.hs300_ret_5),
                  zz1000_ret_5 = COALESCE(t.zz1000_ret_5, m.zz1000_ret_5),
                  market_volatility_20 = COALESCE(t.market_volatility_20, m.market_volatility_20),
                  size_style_spread = COALESCE(t.size_style_spread, m.size_style_spread),
                  market_amount_ratio = COALESCE(t.market_amount_ratio, m.market_amount_ratio),
                  industry_ret_1 = COALESCE(t.industry_ret_1, p.industry_ret_1),
                  industry_ret_5 = COALESCE(t.industry_ret_5, p.industry_ret_5),
                  industry_ret_20 = COALESCE(t.industry_ret_20, p.industry_ret_20),
                  industry_vol_20 = COALESCE(t.industry_vol_20, p.industry_vol_20),
                  stock_excess_ret_5 = COALESCE(
                      t.stock_excess_ret_5,
                      t.ret_5 - COALESCE(t.industry_ret_5, p.industry_ret_5)
                  ),
                  market_adv_decl_ratio = d.market_adv_decl_ratio,
                  market_limit_up_count = d.market_limit_up_count,
                  market_limit_down_count = d.market_limit_down_count,
                  industry_breadth = i.industry_breadth,
                  industry_rank_ret_5 = i.industry_rank_ret_5,
                  stock_rank_industry = COALESCE(rk.rk, t.stock_rank_industry),
                  is_one_word = CASE
                    WHEN t.open = t.high AND t.high = t.low AND t.low = t.close THEN 1 ELSE 0 END,
                  is_halted = COALESCE(t.is_halted, 0),
                  session_flag = CASE
                    WHEN COALESCE(t.is_halted, 0) = 1 THEN :halt
                    WHEN NOT (t.open = t.high AND t.high = t.low AND t.low = t.close) THEN :normal
                    WHEN t.ret_1 >= (
                      CASE t.board_type
                        WHEN 'ST' THEN 0.0475
                        WHEN '科创板' THEN 0.19
                        WHEN '北交所' THEN 0.285
                        WHEN '创业板' THEN CASE WHEN t.trade_date >= DATE '2020-08-24' THEN 0.19 ELSE 0.095 END
                        ELSE 0.095
                      END
                    ) THEN :up
                    WHEN t.ret_1 <= -(
                      CASE t.board_type
                        WHEN 'ST' THEN 0.0475
                        WHEN '科创板' THEN 0.19
                        WHEN '北交所' THEN 0.285
                        WHEN '创业板' THEN CASE WHEN t.trade_date >= DATE '2020-08-24' THEN 0.19 ELSE 0.095 END
                        ELSE 0.095
                      END
                    ) THEN :down
                    WHEN t.open = t.high AND t.high = t.low AND t.low = t.close THEN :flat
                    ELSE :normal
                  END,
                  y_high = CASE WHEN t.close > 0 AND n.high IS NOT NULL
                                THEN n.high / t.close - 1 END,
                  y_low  = CASE WHEN t.close > 0 AND n.low IS NOT NULL
                                THEN n.low / t.close - 1 END
                FROM _ml_fix_keys k
                LEFT JOIN _ml_xs_mkt m ON m.trade_date = k.trade_date
                LEFT JOIN _ml_xs_day d ON d.trade_date = k.trade_date
                LEFT JOIN ml_train_daily tt
                  ON tt.symbol = k.symbol AND tt.trade_date = k.trade_date
                LEFT JOIN _ml_xs_peer p
                  ON p.trade_date = k.trade_date
                 AND p.industry_code = COALESCE(tt.industry_code, '')
                LEFT JOIN _ml_xs_ind i
                  ON i.trade_date = k.trade_date
                 AND i.industry_code = COALESCE(tt.industry_code, '')
                LEFT JOIN _ml_xs_rank rk
                  ON rk.symbol = k.symbol AND rk.trade_date = k.trade_date
                LEFT JOIN LATERAL (
                  SELECT x.high, x.low
                  FROM ml_train_daily x
                  WHERE x.symbol = k.symbol AND x.trade_date > k.trade_date
                  ORDER BY x.trade_date
                  LIMIT 1
                ) n ON TRUE
                WHERE t.symbol = k.symbol AND t.trade_date = k.trade_date
            """), {
                "halt": SESSION_HALT,
                "normal": SESSION_NORMAL,
                "up": SESSION_ONE_WORD_UP,
                "down": SESSION_ONE_WORD_DOWN,
                "flat": SESSION_ONE_WORD_FLAT,
            })
            steps.append({"step": "slice_update", "updated": r.rowcount})

            if not skip_train_ready:
                r = conn.execute(sa_text("""
                    UPDATE ml_train_daily t SET train_ready = CASE
                      WHEN (
                        SELECT COUNT(*) FROM (
                          SELECT 1 FROM ml_train_daily p
                          WHERE p.symbol = t.symbol AND p.trade_date < t.trade_date
                          LIMIT :warmup
                        ) w
                      ) < :warmup THEN 0
                      WHEN t.y_high IS NULL OR t.y_low IS NULL THEN 0
                      WHEN t.ret_1 IS NULL OR t.hs300_ret_1 IS NULL
                           OR t.stock_rank_industry IS NULL THEN 0
                      WHEN t.close IS NULL OR t.close <= 0 OR t.session_flag IS NULL THEN 0
                      ELSE 1
                    END
                    FROM _ml_fix_keys k
                    WHERE t.symbol = k.symbol AND t.trade_date = k.trade_date
                """), {"warmup": _WARMUP_BARS})
                steps.append({"step": "mark_train_ready", "updated": r.rowcount})

        gap_result = None
        if not skip_gaps:
            with self.engine.connect() as conn:
                symbols = [
                    str(r[0])
                    for r in conn.execute(sa_text(
                        "SELECT DISTINCT symbol FROM _ml_fix_keys"
                    )).fetchall()
                ]
            gap_result = self.fill_gaps(limit=len(symbols), offset=0)

        after: Dict[str, Any] = {"rows": []}
        changed: List[Dict[str, Any]] = []
        if include_preview and after_date is None:
            after = self.preview_rows(n, off)
            bmap = {(x["symbol"], x["trade_date"]): x for x in before.get("rows") or []}
            for row in after.get("rows") or []:
                old = bmap.get((row["symbol"], row["trade_date"])) or {}
                diff = {}
                for col in ("y_high", "y_low", "close_location", "session_flag",
                            "is_one_word", "is_halted", "hs300_ret_1",
                            "stock_rank_industry", "industry_rank_ret_5",
                            "market_adv_decl_ratio", "train_ready"):
                    if old.get(col) != row.get(col):
                        diff[col] = {"before": old.get(col), "after": row.get(col)}
                if diff:
                    changed.append({
                        "symbol": row["symbol"],
                        "trade_date": row["trade_date"],
                        "changes": diff,
                    })
        return {
            "ok": True,
            "action": "fix",
            "scope": "slice",
            "limit_rows": n,
            "from_row": off,
            "rows_targeted": key_n,
            "skip_gaps": bool(skip_gaps),
            "first_key": first_key,
            "last_key": last_key,
            "last_date": last_date_obj,
            "last_symbol": last_symbol_obj,
            "steps": steps,
            "fill_gaps": gap_result,
            "changed_rows": len(changed) if include_preview else key_n,
            "sample_before": (before.get("rows") or [])[:12],
            "sample_after": (after.get("rows") or [])[:12],
            "sample_changes": changed[:12],
            "note": (
                "只改本批行；y_high/y_low 用该股下一交易日高低/当日收盘。"
                "前 60 根 train_ready=0。一字/停牌保留并打 session_flag。"
            ),
        }

    def _load_batch_progress(self) -> Dict[str, Any]:
        path = _BATCH_PROGRESS_FILE
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning("读取分批进度失败: %s", e)
            return {}

    def _save_batch_progress(self, data: Dict[str, Any]) -> None:
        path = _BATCH_PROGRESS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def fix_batches(
        self,
        *,
        from_row: int = 200,
        limit_rows: int = 10000,
        skip_gaps: bool = True,
        fill_gaps_after: bool = True,
        reset: bool = False,
        max_batches: Optional[int] = None,
    ) -> Dict[str, Any]:
        """从指定行起按批修复，主键翻页，进度写入 data/ml_train_sanitize_batch_progress.json。"""
        self.ensure_schema()
        batch_size = max(1, int(limit_rows))
        start_row = max(0, int(from_row))
        progress = {} if reset else self._load_batch_progress()
        with self.engine.connect() as conn:
            total = int(conn.execute(sa_text("SELECT COUNT(*) FROM ml_train_daily")).scalar() or 0)

        after_date: Optional[date] = None
        after_symbol: Optional[str] = None
        done_rows = int(progress.get("done_rows") or start_row)
        batch_no = int(progress.get("batch_no") or 0)
        if progress.get("last_trade_date") and progress.get("last_symbol") and not reset:
            after_date = _parse_iso_date(progress["last_trade_date"])
            after_symbol = str(progress["last_symbol"])
        elif start_row > 0:
            key = self._row_key(start_row)
            if key:
                after_date = key["trade_date"]
                after_symbol = key["symbol"]
            done_rows = start_row

        started = time.time()
        batches: List[Dict[str, Any]] = list(progress.get("batches") or [])
        status = "running"
        while True:
            if max_batches is not None and batch_no >= int(max_batches):
                status = "paused"
                break
            t0 = time.time()
            result = self.fix_slice(
                limit_rows=batch_size,
                from_row=0,
                skip_gaps=True,
                include_preview=False,
                after_date=after_date,
                after_symbol=after_symbol,
                skip_ensure=True,
                skip_train_ready=True,
                skip_halt_join=True,
            )
            n = int(result.get("rows_targeted") or 0)
            if n <= 0:
                status = "completed"
                break
            batch_no += 1
            done_rows += n
            last_date = result.get("last_date")
            last_symbol = result.get("last_symbol")
            if isinstance(last_date, datetime):
                last_date = last_date.date()
            after_date = last_date if isinstance(last_date, date) else _parse_iso_date(last_date)
            after_symbol = str(last_symbol) if last_symbol else after_symbol
            elapsed = round(time.time() - t0, 2)
            rec = {
                "batch": batch_no,
                "rows": n,
                "elapsed_s": elapsed,
                "from": result.get("first_key"),
                "to": result.get("last_key"),
                "done_rows": done_rows,
                "total_rows": total,
            }
            batches.append(rec)
            # 进度文件只留最近 20 批，避免无限变大
            self._save_batch_progress({
                "status": "running",
                "batch_no": batch_no,
                "batch_size": batch_size,
                "done_rows": done_rows,
                "total_rows": total,
                "last_trade_date": after_date.isoformat() if after_date else None,
                "last_symbol": after_symbol,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "last_batch": rec,
                "batches": batches[-20:],
            })
            logger.info(
                "sanitize batch %d rows=%d done=%d/%d %.1fs key=%s %s",
                batch_no, n, done_rows, total, elapsed, after_date, after_symbol,
            )
            print(
                f"[sanitize] batch={batch_no} rows={n} done={done_rows}/{total} "
                f"{elapsed:.1f}s -> {after_date} {after_symbol}",
                flush=True,
            )
            gc.collect()

        after_steps: List[Dict[str, Any]] = []
        if status == "completed" and fill_gaps_after:
            print("[sanitize] batches done, fill_gaps + relabel", flush=True)
            after_steps.append(self.fill_gaps())
            after_steps.append(self.fix_labels())
            after_steps.append(self.mark_session())
            after_steps.append(self.mark_train_ready())
        elif status == "completed":
            after_steps.append(self.mark_train_ready())

        summary = {
            "ok": status in ("completed", "paused"),
            "action": "fix_batches",
            "status": status,
            "batch_no": batch_no,
            "batch_size": batch_size,
            "done_rows": done_rows,
            "total_rows": total,
            "elapsed_s": round(time.time() - started, 1),
            "last_trade_date": after_date.isoformat() if after_date else None,
            "last_symbol": after_symbol,
            "after_steps": after_steps,
            "recent_batches": batches[-8:],
        }
        self._save_batch_progress({**summary, "batches": batches[-20:]})
        return summary

    def fix(
        self,
        *,
        skip_gaps: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        limit_rows: Optional[int] = None,
        from_row: int = 0,
    ) -> Dict[str, Any]:
        if limit_rows not in (None, ""):
            skip = skip_gaps
            if skip is False and int(limit_rows) <= 500:
                skip = True
            return self.fix_slice(
                limit_rows=int(limit_rows),
                from_row=int(from_row or 0),
                skip_gaps=bool(skip),
            )
        steps = []
        steps.append(self.ensure_schema())
        steps.append(self.clamp_ohlc())
        steps.append(self.fix_close_location())
        steps.append(self.broadcast_market())
        steps.append(self.fill_cross_section())
        if not skip_gaps:
            steps.append(self.fill_gaps(limit=limit, offset=offset))
            # 插入停牌行后截面/标签都要重算
            steps.append(self.fill_cross_section())
        steps.append(self.mark_session())
        steps.append(self.fix_labels())
        steps.append(self.mark_train_ready())
        audit = self.audit()
        ok = all(s.get("ok") for s in steps if isinstance(s, dict))
        return {
            "ok": ok,
            "action": "fix",
            "steps": [{k: v for k, v in s.items() if k != "updated" or True} for s in steps],
            "audit_after": {
                "overview": audit.get("overview"),
                "nulls": audit.get("nulls"),
                "active_problems": audit.get("active_problems"),
                "rank_coverage": audit.get("rank_coverage"),
                "label_check": audit.get("label_check"),
            },
            "train_filter": "WHERE train_ready = 1",
        }

    def run(self, action: str = "audit", **kwargs: Any) -> Dict[str, Any]:
        action = (action or "audit").strip().lower().replace("-", "_")
        if action in ("catalog", "problems", "help"):
            return self.catalog()
        if action in ("audit", "check"):
            return self.audit()
        if action in ("ensure_schema", "schema"):
            return self.ensure_schema()
        if action in ("clamp_ohlc", "clamp"):
            self.ensure_schema()
            return self.clamp_ohlc()
        if action in ("fix_close_location", "close_location"):
            self.ensure_schema()
            return self.fix_close_location()
        if action in ("broadcast_market", "broadcast"):
            self.ensure_schema()
            return self.broadcast_market()
        if action in ("fill_cross_section", "cross_section"):
            self.ensure_schema()
            return self.fill_cross_section()
        if action in ("fill_gaps", "gaps"):
            self.ensure_schema()
            dry = kwargs.get("dry_run")
            if isinstance(dry, str):
                dry = dry.strip().lower() in ("1", "true", "yes", "y")
            return self.fill_gaps(
                limit=int(kwargs["limit"]) if kwargs.get("limit") not in (None, "") else None,
                offset=int(kwargs.get("offset") or 0),
                dry_run=bool(dry),
            )
        if action in ("mark_session", "session"):
            self.ensure_schema()
            return self.mark_session()
        if action in ("fix_labels", "labels"):
            self.ensure_schema()
            return self.fix_labels()
        if action in ("mark_train_ready", "train_ready"):
            self.ensure_schema()
            return self.mark_train_ready()
        if action in ("fix_batches", "fix_batch", "continue"):
            skip_gaps = kwargs.get("skip_gaps")
            if isinstance(skip_gaps, str):
                skip_gaps = skip_gaps.strip().lower() in ("1", "true", "yes", "y")
            fill_after = kwargs.get("fill_gaps_after")
            if fill_after is None or fill_after == "":
                fill_flag = True
            elif isinstance(fill_after, bool):
                fill_flag = fill_after
            else:
                fill_flag = str(fill_after).strip().lower() not in ("0", "false", "no", "n", "off")
            reset_raw = kwargs.get("reset")
            if isinstance(reset_raw, bool):
                reset_flag = reset_raw
            else:
                reset_flag = str(reset_raw or "").strip().lower() in ("1", "true", "yes", "y")
            max_b = kwargs.get("max_batches")
            return self.fix_batches(
                from_row=int(kwargs.get("from_row") or 200),
                limit_rows=int(kwargs.get("limit_rows") or kwargs.get("limit") or 10000),
                skip_gaps=True if skip_gaps is None else bool(skip_gaps),
                fill_gaps_after=fill_flag,
                reset=reset_flag,
                max_batches=int(max_b) if max_b not in (None, "") else None,
            )
        if action in ("preview_rows", "preview"):
            return self.preview_rows(
                limit_rows=int(kwargs["limit_rows"] or kwargs.get("limit") or 200),
                from_row=int(kwargs.get("from_row") or kwargs.get("offset") or 0),
            )
        if action in ("fix", "repair", "run_all", "sanitize"):
            skip_gaps = kwargs.get("skip_gaps")
            if isinstance(skip_gaps, str):
                skip_gaps = skip_gaps.strip().lower() in ("1", "true", "yes", "y")
            limit_rows = kwargs.get("limit_rows")
            if limit_rows in (None, "") and kwargs.get("limit") not in (None, "") and int(kwargs.get("offset") or 0) == 0:
                # 面板里 limit 既可表示股票数也可表示预览行数；超过 500 视为股票批大小
                lv = int(kwargs["limit"])
                if 1 <= lv <= 500:
                    limit_rows = lv
            return self.fix(
                skip_gaps=bool(skip_gaps) if skip_gaps is not None else False,
                limit=int(kwargs["limit"]) if kwargs.get("limit") not in (None, "") else None,
                offset=int(kwargs.get("offset") or 0),
                limit_rows=int(limit_rows) if limit_rows not in (None, "") else None,
                from_row=int(kwargs.get("from_row") or 0),
            )
        return {
            "ok": False,
            "error": (
                "action 应为 catalog / audit / ensure_schema / clamp_ohlc / "
                "fix_close_location / broadcast_market / fill_cross_section / "
                "fill_gaps / mark_session / fix_labels / mark_train_ready / "
                "fix / fix_batches"
            ),
        }

    async def run_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.run, **kwargs)


ml_train_sanitize_toolkit = MlTrainSanitizeToolkit()
