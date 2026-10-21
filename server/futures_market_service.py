"""
期货行情中心服务：主连看板 + 日/周/月 K 线。
数据源优先 Datahubco fut_daily，写入 PG fut_daily 做缓存；周/月由日线聚合。
A 股指数（上证/深成等）仍由行情看板负责，本服务不重复。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

FUT_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS fut_daily (
    ts_code      VARCHAR(24)   NOT NULL,
    trade_date   DATE          NOT NULL,
    pre_close    NUMERIC(16,4),
    pre_settle   NUMERIC(16,4),
    open         NUMERIC(16,4),
    high         NUMERIC(16,4),
    low          NUMERIC(16,4),
    close        NUMERIC(16,4),
    settle       NUMERIC(16,4),
    change1      NUMERIC(16,4),
    change2      NUMERIC(16,4),
    vol          NUMERIC(20,4),
    amount       NUMERIC(24,4),
    oi           NUMERIC(20,4),
    oi_chg       NUMERIC(20,4),
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date)
)
"""

# 主连看板：分类 → [(ts_code, 中文名)]
FUTURES_BOARD: Dict[str, List[Tuple[str, str]]] = {
    "贵金属": [
        ("AU.SHF", "沪金主连"),
        ("AG.SHF", "沪银主连"),
    ],
    "有色金属": [
        ("CU.SHF", "沪铜主连"),
        ("AL.SHF", "沪铝主连"),
        ("ZN.SHF", "沪锌主连"),
        ("NI.SHF", "沪镍主连"),
        ("SN.SHF", "沪锡主连"),
        ("PB.SHF", "沪铅主连"),
        ("AO.SHF", "氧化铝主连"),
        ("BC.INE", "国际铜主连"),
        ("SS.SHF", "不锈钢主连"),
    ],
    "能源化工": [
        ("SC.INE", "原油主连"),
        ("FU.SHF", "燃油主连"),
        ("LU.INE", "低硫燃油主连"),
        ("PG.DCE", "LPG主连"),
        ("MA.ZCE", "甲醇主连"),
        ("TA.ZCE", "PTA主连"),
        ("EG.DCE", "乙二醇主连"),
        ("PP.DCE", "聚丙烯主连"),
        ("V.DCE", "PVC主连"),
        ("EB.DCE", "苯乙烯主连"),
        ("BU.SHF", "沥青主连"),
        ("RU.SHF", "橡胶主连"),
        ("NR.INE", "20号胶主连"),
        ("BR.SHF", "合成橡胶主连"),
    ],
    "黑色建材": [
        ("I.DCE", "铁矿石主连"),
        ("J.DCE", "焦炭主连"),
        ("JM.DCE", "焦煤主连"),
        ("RB.SHF", "螺纹钢主连"),
        ("HC.SHF", "热卷主连"),
        ("SF.ZCE", "硅铁主连"),
        ("SM.ZCE", "锰硅主连"),
        ("FG.ZCE", "玻璃主连"),
    ],
    "农产品": [
        ("A.DCE", "豆一主连"),
        ("B.DCE", "豆二主连"),
        ("M.DCE", "豆粕主连"),
        ("Y.DCE", "豆油主连"),
        ("P.DCE", "棕榈油主连"),
        ("OI.ZCE", "菜油主连"),
        ("RM.ZCE", "菜粕主连"),
        ("CF.ZCE", "棉花主连"),
        ("SR.ZCE", "白糖主连"),
        ("CJ.ZCE", "红枣主连"),
        ("PK.ZCE", "花生主连"),
        ("LH.DCE", "生猪主连"),
        ("C.DCE", "玉米主连"),
        ("CS.DCE", "玉米淀粉主连"),
        ("RR.DCE", "粳米主连"),
    ],
    "股指国债": [
        ("IF.CFX", "沪深300主连"),
        ("IH.CFX", "上证50主连"),
        ("IC.CFX", "中证500主连"),
        ("IM.CFX", "中证1000主连"),
        ("T.CFX", "十年国债主连"),
        ("TF.CFX", "五年国债主连"),
        ("TS.CFX", "二年国债主连"),
    ],
    "广期所": [
        ("SI.GFE", "工业硅主连"),
        ("LC.GFE", "碳酸锂主连"),
        ("PS.GFE", "多晶硅主连"),
    ],
    "国际贵金属": [
        ("XAUUSD.FXCM", "伦敦金"),
        ("XAGUSD.FXCM", "伦敦银"),
    ],
}

EXCHANGE_MAP = {
    "SHF": "SHFE",
    "SHFE": "SHFE",
    "INE": "INE",
    "DCE": "DCE",
    "ZCE": "CZCE",
    "CZCE": "CZCE",
    "CFX": "CFFEX",
    "CFFEX": "CFFEX",
    "GFE": "GFEX",
    "GFEX": "GFEX",
}

_BOARD_CACHE_TTL = 90.0


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace("/", "-")
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        try:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        except Exception:
            return None


def _ymd(d: Optional[date]) -> Optional[str]:
    return d.strftime("%Y%m%d") if d else None


class FuturesMarketService:
    def __init__(self) -> None:
        self._engine = None
        self._tables_ready = False
        self._board_cache: Optional[Dict[str, Any]] = None
        self._board_cache_at = 0.0
        self._name_map = {
            code: name
            for items in FUTURES_BOARD.values()
            for code, name in items
        }

    @property
    def engine(self):
        if self._engine is None:
            from server.storage_service import storage_manager

            self._engine = storage_manager.get_postgres_engine()
            if self._engine is None:
                raise RuntimeError("PostgreSQL 引擎不可用")
        return self._engine

    def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        with self.engine.begin() as conn:
            conn.execute(sa_text(FUT_DAILY_DDL))
            conn.execute(
                sa_text(
                    "CREATE INDEX IF NOT EXISTS idx_fut_daily_date ON fut_daily (trade_date DESC)"
                )
            )
        self._tables_ready = True

    def catalog(self) -> Dict[str, Any]:
        cats = []
        for cat, items in FUTURES_BOARD.items():
            cats.append(
                {
                    "id": cat,
                    "name": cat,
                    "count": len(items),
                    "items": [{"ts_code": c, "name": n} for c, n in items],
                }
            )
        return {
            "ok": True,
            "categories": cats,
            "total": sum(len(v) for v in FUTURES_BOARD.values()),
            "note": "A股指数请在「行情看板」查看；本页为期货日/周/月线中心",
        }

    # ------------------------------------------------------------------ #
    # Datahubco / PG
    # ------------------------------------------------------------------ #
    def _client(self):
        from data_layer.collectors.datahubco.client import DatahubcoClient

        return DatahubcoClient(timeout=45)

    def _upsert_daily(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        self._ensure_tables()
        cols = [
            "ts_code",
            "trade_date",
            "pre_close",
            "pre_settle",
            "open",
            "high",
            "low",
            "close",
            "settle",
            "change1",
            "change2",
            "vol",
            "amount",
            "oi",
            "oi_chg",
        ]
        use = df.copy()
        for c in cols:
            if c not in use.columns:
                use[c] = None
        use = use[cols]
        use["ts_code"] = use["ts_code"].astype(str).str.upper()
        use["trade_date"] = pd.to_datetime(use["trade_date"], errors="coerce").dt.date
        use = use.dropna(subset=["ts_code", "trade_date"])
        if use.empty:
            return 0
        sql = sa_text(
            """
            INSERT INTO fut_daily (
                ts_code, trade_date, pre_close, pre_settle, open, high, low,
                close, settle, change1, change2, vol, amount, oi, oi_chg, updated_at
            ) VALUES (
                :ts_code, :trade_date, :pre_close, :pre_settle, :open, :high, :low,
                :close, :settle, :change1, :change2, :vol, :amount, :oi, :oi_chg, NOW()
            )
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                pre_close = EXCLUDED.pre_close,
                pre_settle = EXCLUDED.pre_settle,
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                settle = EXCLUDED.settle,
                change1 = EXCLUDED.change1,
                change2 = EXCLUDED.change2,
                vol = EXCLUDED.vol,
                amount = EXCLUDED.amount,
                oi = EXCLUDED.oi,
                oi_chg = EXCLUDED.oi_chg,
                updated_at = NOW()
            """
        )
        rows = use.to_dict(orient="records")
        with self.engine.begin() as conn:
            conn.execute(sql, rows)
        return len(rows)

    def _read_pg_daily(
        self,
        ts_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        self._ensure_tables()
        sql = """
            SELECT ts_code, trade_date, pre_close, pre_settle, open, high, low,
                   close, settle, change1, change2, vol, amount, oi, oi_chg
            FROM fut_daily
            WHERE ts_code = :code
        """
        params: Dict[str, Any] = {"code": ts_code.upper(), "lim": limit}
        if start:
            sql += " AND trade_date >= :start"
            params["start"] = start
        if end:
            sql += " AND trade_date <= :end"
            params["end"] = end
        sql += " ORDER BY trade_date DESC LIMIT :lim"
        with self.engine.connect() as conn:
            df = pd.read_sql(sa_text(sql), conn, params=params)
        if not df.empty:
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    def _fetch_remote_daily(
        self,
        ts_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        limit: int = 800,
    ) -> pd.DataFrame:
        client = self._client()
        end = end or date.today()
        start = start or (end - timedelta(days=420))
        df = client.query(
            "fut_daily",
            ts_code=ts_code.upper(),
            start_date=_ymd(start),
            end_date=_ymd(end),
            limit=limit,
        )
        return df if df is not None else pd.DataFrame()

    def _fetch_fx_daily(
        self,
        ts_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        limit: int = 800,
    ) -> pd.DataFrame:
        client = self._client()
        end = end or date.today()
        start = start or (end - timedelta(days=420))
        raw = client.query(
            "fx_daily",
            ts_code=ts_code.upper(),
            start_date=_ymd(start),
            end_date=_ymd(end),
            limit=limit,
        )
        if raw is None or raw.empty:
            return pd.DataFrame()
        df = pd.DataFrame(
            {
                "ts_code": raw.get("ts_code", ts_code),
                "trade_date": raw["trade_date"],
                "open": raw.get("bid_open"),
                "high": raw.get("bid_high"),
                "low": raw.get("bid_low"),
                "close": raw.get("bid_close"),
                "settle": raw.get("bid_close"),
                "pre_close": None,
                "pre_settle": None,
                "change1": None,
                "change2": None,
                "vol": raw.get("tick_qty"),
                "amount": None,
                "oi": None,
                "oi_chg": None,
            }
        )
        return df

    def _is_fx(self, ts_code: str) -> bool:
        return str(ts_code).upper().endswith(".FXCM")

    def _load_daily_series(
        self,
        ts_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        limit: int = 500,
        write_back: bool = True,
    ) -> Tuple[pd.DataFrame, str]:
        code = ts_code.upper()
        if self._is_fx(code):
            remote = self._fetch_fx_daily(code, start=start, end=end, limit=limit)
            return remote, "datahubco-fx"

        local = pd.DataFrame()
        try:
            local = self._read_pg_daily(code, start=start, end=end, limit=limit)
        except Exception as e:
            logger.debug("read fut_daily pg %s: %s", code, e)

        need_remote = local.empty
        if not local.empty and end:
            last = _to_date(local["trade_date"].iloc[-1])
            if last and last < end - timedelta(days=5):
                need_remote = True
        if not need_remote and len(local) >= min(limit, 60):
            return local, "pg"

        try:
            remote = self._fetch_remote_daily(code, start=start, end=end, limit=max(limit, 300))
        except Exception as e:
            logger.warning("fut_daily remote %s: %s", code, e)
            return local, "pg" if not local.empty else "empty"

        if remote is not None and not remote.empty and write_back and not self._is_fx(code):
            try:
                self._upsert_daily(remote)
                local2 = self._read_pg_daily(code, start=start, end=end, limit=limit)
                if not local2.empty:
                    return local2, "datahubco+pg"
            except Exception as e:
                logger.warning("upsert fut_daily %s: %s", code, e)
            return remote.sort_values("trade_date").reset_index(drop=True), "datahubco"
        if remote is not None and not remote.empty:
            return remote.sort_values("trade_date").reset_index(drop=True), "datahubco"
        return local, "pg" if not local.empty else "empty"

    # ------------------------------------------------------------------ #
    # Board snapshot
    # ------------------------------------------------------------------ #
    def _row_metrics(self, row: Dict[str, Any], name: str) -> Dict[str, Any]:
        close = _f(row.get("close") if row.get("close") is not None else row.get("settle"))
        pre = _f(
            row.get("pre_settle")
            if row.get("pre_settle") is not None
            else row.get("pre_close"),
            default=0.0,
        )
        if pre <= 0 and row.get("change1") is not None:
            pre = close - _f(row.get("change1"))
        change = close - pre if pre > 0 else _f(row.get("change1"))
        pct = (change / pre * 100.0) if pre > 0 else 0.0
        high = _f(row.get("high"))
        low = _f(row.get("low"))
        amp = ((high - low) / pre * 100.0) if pre > 0 and high > 0 and low > 0 else 0.0
        return {
            "ts_code": str(row.get("ts_code") or "").upper(),
            "name": name,
            "trade_date": str(row.get("trade_date"))[:10] if row.get("trade_date") else None,
            "close": round(close, 4),
            "settle": round(_f(row.get("settle"), close), 4),
            "change": round(change, 4),
            "pct_chg": round(pct, 4),
            "open": round(_f(row.get("open")), 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "vol": round(_f(row.get("vol")), 2),
            "amount": round(_f(row.get("amount")), 2),
            "oi": round(_f(row.get("oi")), 2),
            "oi_chg": round(_f(row.get("oi_chg")), 2),
            "amplitude": round(amp, 4),
        }

    def _latest_exchange_day(self, exchange: str) -> Optional[date]:
        """从某交易所主连试探最近交易日。"""
        probes = {
            "SHFE": "AU.SHF",
            "INE": "SC.INE",
            "DCE": "I.DCE",
            "CZCE": "MA.ZCE",
            "CFFEX": "IF.CFX",
            "GFEX": "SI.GFE",
        }
        code = probes.get(exchange)
        if not code:
            return None
        df, _ = self._load_daily_series(
            code, start=date.today() - timedelta(days=21), end=date.today(), limit=10
        )
        if df.empty:
            return None
        return _to_date(df["trade_date"].iloc[-1])

    def get_board(
        self,
        category: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        now = time.time()
        if (
            not force_refresh
            and self._board_cache
            and now - self._board_cache_at < _BOARD_CACHE_TTL
            and not category
        ):
            return self._board_cache

        cats = (
            {category: FUTURES_BOARD[category]}
            if category and category in FUTURES_BOARD
            else FUTURES_BOARD
        )

        sections: List[Dict[str, Any]] = []
        asof: Optional[str] = None
        for cat, items in cats.items():
            rows_out: List[Dict[str, Any]] = []
            for code, name in items:
                try:
                    df, src = self._load_daily_series(
                        code,
                        start=date.today() - timedelta(days=40),
                        end=date.today(),
                        limit=5,
                        write_back=not self._is_fx(code),
                    )
                    if df.empty:
                        rows_out.append(
                            {
                                "ts_code": code,
                                "name": name,
                                "trade_date": None,
                                "close": 0,
                                "settle": 0,
                                "change": 0,
                                "pct_chg": 0,
                                "open": 0,
                                "high": 0,
                                "low": 0,
                                "vol": 0,
                                "amount": 0,
                                "oi": 0,
                                "oi_chg": 0,
                                "amplitude": 0,
                                "missing": True,
                                "source": src,
                            }
                        )
                        continue
                    last = df.iloc[-1].to_dict()
                    # 补 pre_close（fx / 缺字段）
                    if last.get("pre_close") is None and len(df) >= 2:
                        last["pre_close"] = df.iloc[-2].get("close")
                        last["pre_settle"] = df.iloc[-2].get("settle") or df.iloc[-2].get("close")
                    item = self._row_metrics(last, name)
                    item["missing"] = False
                    item["source"] = src
                    rows_out.append(item)
                    if item.get("trade_date"):
                        asof = item["trade_date"]
                except Exception as e:
                    logger.warning("board row %s: %s", code, e)
                    rows_out.append(
                        {
                            "ts_code": code,
                            "name": name,
                            "missing": True,
                            "error": str(e)[:120],
                            "pct_chg": 0,
                            "change": 0,
                            "close": 0,
                        }
                    )
            # 按涨幅降序，贴近行情看板
            rows_out.sort(key=lambda x: _f(x.get("pct_chg")), reverse=True)
            sections.append(
                {
                    "category": cat,
                    "count": len(rows_out),
                    "ready": sum(1 for r in rows_out if not r.get("missing")),
                    "items": rows_out,
                }
            )

        result = {
            "ok": True,
            "asof": asof,
            "sections": sections,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "note": "主连日线快照；点击合约查看日/周/月K线。A股指数见行情看板。",
        }
        if not category:
            self._board_cache = result
            self._board_cache_at = now
        return result

    # ------------------------------------------------------------------ #
    # K-line day / week / month
    # ------------------------------------------------------------------ #
    def _aggregate(self, df: pd.DataFrame, freq: str) -> pd.DataFrame:
        if df.empty or freq == "daily":
            return df
        use = df.copy()
        use["trade_date"] = pd.to_datetime(use["trade_date"], errors="coerce")
        use = use.dropna(subset=["trade_date"]).sort_values("trade_date")
        rule = "W-FRI" if freq == "weekly" else "M"
        try:
            # pandas>=2.2 推荐 ME；旧版用 M
            if freq == "monthly":
                pd.tseries.frequencies.to_offset("ME")
                rule = "ME"
        except Exception:
            rule = "M" if freq == "monthly" else "W-FRI"
        agg = (
            use.set_index("trade_date")
            .resample(rule)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "settle": "last",
                    "vol": "sum",
                    "amount": "sum",
                    "oi": "last",
                    "pre_close": "first",
                    "pre_settle": "first",
                }
            )
            .dropna(subset=["close"])
            .reset_index()
        )
        if agg.empty:
            return agg
        # 周/月涨跌相对该周期前收
        prev = agg["close"].shift(1)
        agg["pre_close"] = prev
        agg["pre_settle"] = prev
        agg["change1"] = agg["close"] - prev
        agg["change2"] = agg["settle"].fillna(agg["close"]) - prev
        agg["oi_chg"] = None
        return agg

    def get_kline(
        self,
        ts_code: str,
        freq: str = "daily",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 300,
    ) -> Dict[str, Any]:
        freq = (freq or "daily").lower()
        if freq not in ("daily", "weekly", "monthly"):
            return {"ok": False, "error": "freq 应为 daily|weekly|monthly"}
        code = (ts_code or "").strip().upper()
        if not code:
            return {"ok": False, "error": "缺少 ts_code"}

        end_d = _to_date(end) or date.today()
        start_d = _to_date(start)
        if start_d is None:
            days = 420 if freq == "daily" else (900 if freq == "weekly" else 2000)
            start_d = end_d - timedelta(days=days)

        raw_limit = limit if freq == "daily" else max(limit * 6, 400)
        df, source = self._load_daily_series(
            code, start=start_d, end=end_d, limit=raw_limit, write_back=not self._is_fx(code)
        )
        if df.empty:
            return {
                "ok": False,
                "error": f"无行情: {code}",
                "ts_code": code,
                "freq": freq,
                "bars": [],
            }

        bars_df = self._aggregate(df, freq)
        if freq != "daily" and len(bars_df) > limit:
            bars_df = bars_df.tail(limit)
        elif freq == "daily" and len(bars_df) > limit:
            bars_df = bars_df.tail(limit)

        bars: List[Dict[str, Any]] = []
        for _, r in bars_df.iterrows():
            d = _to_date(r.get("trade_date"))
            close = _f(r.get("close"))
            pre = _f(r.get("pre_close") if r.get("pre_close") is not None else r.get("pre_settle"))
            if pre <= 0 and bars:
                pre = bars[-1]["close"]
            change = close - pre if pre > 0 else _f(r.get("change1"))
            pct = (change / pre * 100.0) if pre > 0 else 0.0
            bars.append(
                {
                    "trade_date": d.isoformat() if d else None,
                    "open": round(_f(r.get("open")), 4),
                    "high": round(_f(r.get("high")), 4),
                    "low": round(_f(r.get("low")), 4),
                    "close": round(close, 4),
                    "settle": round(_f(r.get("settle"), close), 4),
                    "vol": round(_f(r.get("vol")), 2),
                    "amount": round(_f(r.get("amount")), 2),
                    "oi": round(_f(r.get("oi")), 2),
                    "change": round(change, 4),
                    "pct_chg": round(pct, 4),
                }
            )

        name = self._name_map.get(code, code)
        stats = None
        if bars:
            first, last = bars[0], bars[-1]
            stats = {
                "period_start": first["trade_date"],
                "period_end": last["trade_date"],
                "first_close": first["close"],
                "last_close": last["close"],
                "change_pct": round(
                    (last["close"] / first["close"] - 1) * 100 if first["close"] else 0, 4
                ),
                "high_max": max(b["high"] for b in bars),
                "low_min": min(b["low"] for b in bars if b["low"] > 0) if any(b["low"] > 0 for b in bars) else 0,
                "bar_count": len(bars),
                "vol_total": round(sum(b["vol"] for b in bars), 2),
            }

        return {
            "ok": True,
            "ts_code": code,
            "name": name,
            "freq": freq,
            "source": source,
            "count": len(bars),
            "bars": bars,
            "stats": stats,
        }

    # ------------------------------------------------------------------ #
    # 上一交易日解析 / 覆盖检查 / 补数入库
    # ------------------------------------------------------------------ #
    def all_main_codes(self, category: Optional[str] = None) -> List[Tuple[str, str]]:
        if category and category in FUTURES_BOARD:
            return list(FUTURES_BOARD[category])
        out: List[Tuple[str, str]] = []
        for items in FUTURES_BOARD.values():
            out.extend(items)
        return out

    def resolve_prev_trade_date(self, prefer: Optional[str] = None) -> Optional[date]:
        """解析可用的上一交易日（以主连远端日线为准，默认沪金）。"""
        probe = (prefer or "AU.SHF").upper()
        # 先看库
        try:
            local = self._read_pg_daily(
                probe, start=date.today() - timedelta(days=30), end=date.today(), limit=10
            )
            if not local.empty:
                d = _to_date(local["trade_date"].iloc[-1])
                if d:
                    # 若库已是较近交易日，仍尝试远端确认是否有更新
                    pass
        except Exception:
            local = pd.DataFrame()

        try:
            remote = self._fetch_remote_daily(
                probe, start=date.today() - timedelta(days=30), end=date.today(), limit=15
            )
            if remote is not None and not remote.empty:
                remote = remote.sort_values("trade_date")
                d = _to_date(remote["trade_date"].iloc[-1])
                if d and not self._is_fx(probe):
                    try:
                        self._upsert_daily(remote.tail(20))
                    except Exception:
                        pass
                return d
        except Exception as e:
            logger.warning("resolve_prev_trade_date remote: %s", e)

        if not local.empty:
            return _to_date(local["trade_date"].iloc[-1])
        return self._latest_exchange_day("SHFE")

    def _pg_dates_for_codes(self, codes: List[str]) -> Dict[str, Optional[date]]:
        self._ensure_tables()
        out = {c.upper(): None for c in codes}
        if not codes:
            return out
        # 用 IN 列表，避免驱动对 ARRAY/ANY 兼容问题
        uniq = sorted({c.upper() for c in codes})
        placeholders = ", ".join(f":c{i}" for i in range(len(uniq)))
        params = {f"c{i}": code for i, code in enumerate(uniq)}
        sql = sa_text(
            f"""
            SELECT ts_code, MAX(trade_date) AS d
            FROM fut_daily
            WHERE ts_code IN ({placeholders})
            GROUP BY ts_code
            """
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            for r in rows:
                out[str(r.ts_code).upper()] = _to_date(r.d)
        except Exception as e:
            logger.debug("pg dates batch failed: %s", e)
            for c in uniq:
                try:
                    df = self._read_pg_daily(c, limit=1)
                    if not df.empty:
                        out[c] = _to_date(df["trade_date"].iloc[-1])
                except Exception:
                    pass
        return out

    def _suffix_exchange(self, ts_code: str) -> Optional[str]:
        code = ts_code.upper()
        if "." not in code:
            return None
        suf = code.rsplit(".", 1)[-1]
        return EXCHANGE_MAP.get(suf)

    def _sync_exchange_day(self, exchange: str, trade_date: date) -> int:
        """按交易所+交易日批量拉日线并入库，返回写入行数。"""
        client = self._client()
        try:
            df = client.query(
                "fut_daily",
                trade_date=_ymd(trade_date),
                exchange=exchange,
                limit=5000,
            )
        except Exception as e:
            logger.warning("sync exchange %s %s: %s", exchange, trade_date, e)
            return 0
        if df is None or df.empty:
            return 0
        mains = {c for c, _ in self.all_main_codes()}
        use = df[df["ts_code"].astype(str).str.upper().isin(mains)].copy()
        if use.empty:
            return 0
        return self._upsert_daily(use)

    def sync_quotes(
        self,
        ts_codes: Optional[List[str]] = None,
        category: Optional[str] = None,
        asof: Optional[str] = None,
        lookback_days: int = 5,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        补齐主连日线到上一交易日（或指定 asof），写入 fut_daily。
        缺数 / 日期过旧时从 Datahubco 拉取。
        """
        target = _to_date(asof) or self.resolve_prev_trade_date()
        if not target:
            return {"ok": False, "error": "无法解析上一交易日"}

        pairs = self.all_main_codes(category)
        if ts_codes:
            want = {str(c).strip().upper() for c in ts_codes if c}
            pairs = [(c, n) for c, n in pairs if c in want] or [
                (c, self._name_map.get(c, c)) for c in want
            ]

        fut_pairs = [(c, n) for c, n in pairs if not self._is_fx(c)]
        fx_pairs = [(c, n) for c, n in pairs if self._is_fx(c)]

        dates_map = self._pg_dates_for_codes([c for c, _ in fut_pairs])
        stale = [
            c
            for c, _ in fut_pairs
            if force or dates_map.get(c) is None or (dates_map.get(c) or date.min) < target
        ]

        upserted = 0
        # 先按交易所批量补目标日
        exchanges = sorted(
            {ex for c in stale if (ex := self._suffix_exchange(c))}
        )
        for ex in exchanges:
            upserted += self._sync_exchange_day(ex, target)

        # 再复查仍缺的，按合约拉一段日线
        dates_map = self._pg_dates_for_codes([c for c, _ in fut_pairs])
        still = [
            c
            for c, _ in fut_pairs
            if force or dates_map.get(c) is None or (dates_map.get(c) or date.min) < target
        ]
        filled: List[str] = []
        missing: List[str] = []
        start = target - timedelta(days=max(int(lookback_days or 5), 3) + 10)
        for code in still:
            try:
                df, src = self._load_daily_series(
                    code, start=start, end=target, limit=80, write_back=True
                )
                if df.empty:
                    missing.append(code)
                    continue
                last_d = _to_date(df["trade_date"].iloc[-1])
                if last_d and last_d >= target:
                    filled.append(code)
                    upserted += min(len(df), 20)
                elif last_d:
                    # 有数据但未到目标日：仍算部分成功
                    filled.append(code)
                else:
                    missing.append(code)
            except Exception as e:
                logger.warning("sync %s: %s", code, e)
                missing.append(code)

        fx_filled: List[str] = []
        for code, _ in fx_pairs:
            try:
                df, _ = self._load_daily_series(
                    code,
                    start=target - timedelta(days=20),
                    end=target,
                    limit=30,
                    write_back=False,
                )
                if not df.empty:
                    fx_filled.append(code)
                else:
                    missing.append(code)
            except Exception:
                missing.append(code)

        # 清看板缓存
        self._board_cache = None
        self._board_cache_at = 0.0

        return {
            "ok": True,
            "asof": target.isoformat(),
            "requested": len(pairs),
            "stale_before": len(stale),
            "filled": filled + fx_filled,
            "missing": missing,
            "upserted_rows": upserted,
            "exchanges_synced": exchanges,
            "note": f"已对齐上一交易日 {target.isoformat()}；缺失 {len(missing)} 只",
        }

    def check_coverage(
        self,
        ts_codes: Optional[List[str]] = None,
        category: Optional[str] = None,
        asof: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = _to_date(asof) or self.resolve_prev_trade_date()
        pairs = self.all_main_codes(category)
        if ts_codes:
            want = {str(c).strip().upper() for c in ts_codes if c}
            pairs = [(c, n) for c, n in pairs if c in want] or [
                (c, self._name_map.get(c, c)) for c in want
            ]
        fut_codes = [c for c, _ in pairs if not self._is_fx(c)]
        dates_map = self._pg_dates_for_codes(fut_codes)
        ok_rows: List[Dict[str, Any]] = []
        stale_rows: List[Dict[str, Any]] = []
        missing_rows: List[Dict[str, Any]] = []
        for code, name in pairs:
            if self._is_fx(code):
                df, _ = self._load_daily_series(
                    code,
                    start=(target or date.today()) - timedelta(days=20),
                    end=target or date.today(),
                    limit=5,
                    write_back=False,
                )
                if df.empty:
                    missing_rows.append({"ts_code": code, "name": name, "last_date": None})
                else:
                    ld = _to_date(df["trade_date"].iloc[-1])
                    row = {"ts_code": code, "name": name, "last_date": ld.isoformat() if ld else None}
                    if target and ld and ld < target:
                        stale_rows.append(row)
                    else:
                        ok_rows.append(row)
                continue
            ld = dates_map.get(code)
            row = {
                "ts_code": code,
                "name": name,
                "last_date": ld.isoformat() if ld else None,
            }
            if ld is None:
                missing_rows.append(row)
            elif target and ld < target:
                stale_rows.append(row)
            else:
                ok_rows.append(row)

        return {
            "ok": True,
            "asof": target.isoformat() if target else None,
            "total": len(pairs),
            "ready": len(ok_rows),
            "stale": stale_rows,
            "missing": missing_rows,
            "ready_items": ok_rows[:30],
            "needs_sync": bool(stale_rows or missing_rows),
            "note": (
                None
                if not (stale_rows or missing_rows)
                else "存在缺失或日期落后，请调用 futures_market__sync_quotes 补数入库"
            ),
        }

    def get_quotes(
        self,
        ts_codes: Optional[List[str]] = None,
        category: Optional[str] = None,
        ensure: bool = True,
    ) -> Dict[str, Any]:
        """读取行情中心主连最新价；ensure=True 时先补齐上一交易日。"""
        if ensure:
            sync = self.sync_quotes(ts_codes=ts_codes, category=category, force=False)
        else:
            asof_d = self.resolve_prev_trade_date()
            sync = {
                "asof": asof_d.isoformat() if asof_d else None,
                "filled": [],
                "missing": [],
            }

        # 指定合约时直接读序列，避免拉全市场看板
        if ts_codes:
            items: List[Dict[str, Any]] = []
            target = _to_date(sync.get("asof"))
            for code in [str(c).strip().upper() for c in ts_codes if c]:
                name = self._name_map.get(code, code)
                try:
                    df, src = self._load_daily_series(
                        code,
                        start=(target or date.today()) - timedelta(days=40),
                        end=target or date.today(),
                        limit=5,
                        write_back=not self._is_fx(code),
                    )
                    if df.empty:
                        items.append(
                            {
                                "ts_code": code,
                                "name": name,
                                "missing": True,
                                "close": 0,
                                "pct_chg": 0,
                                "change": 0,
                                "source": src,
                            }
                        )
                        continue
                    last = df.iloc[-1].to_dict()
                    if last.get("pre_close") is None and len(df) >= 2:
                        last["pre_close"] = df.iloc[-2].get("close")
                        last["pre_settle"] = df.iloc[-2].get("settle") or df.iloc[-2].get("close")
                    item = self._row_metrics(last, name)
                    item["missing"] = False
                    item["source"] = src
                    items.append(item)
                except Exception as e:
                    items.append(
                        {
                            "ts_code": code,
                            "name": name,
                            "missing": True,
                            "error": str(e)[:120],
                            "close": 0,
                            "pct_chg": 0,
                            "change": 0,
                        }
                    )
            return {
                "ok": True,
                "asof": sync.get("asof"),
                "count": len(items),
                "quotes": items,
                "sync": {
                    "filled": sync.get("filled") or [],
                    "missing": sync.get("missing") or [],
                    "upserted_rows": sync.get("upserted_rows"),
                },
            }

        board = self.get_board(category=category, force_refresh=True)
        items = [it for sec in board.get("sections") or [] for it in (sec.get("items") or [])]
        return {
            "ok": True,
            "asof": board.get("asof") or sync.get("asof"),
            "count": len(items),
            "quotes": items,
            "sync": {
                "filled": sync.get("filled") or [],
                "missing": sync.get("missing") or [],
                "upserted_rows": sync.get("upserted_rows"),
            },
            "note": board.get("note"),
        }

    def ensure_prev_day_board(
        self,
        category: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """自动对齐上一交易日并返回看板（供行情中心定时刷新）。"""
        sync = self.sync_quotes(category=category, force=force)
        board = self.get_board(category=category, force_refresh=True)
        board["sync"] = sync
        board["prev_trade_date"] = sync.get("asof")
        board["note"] = (
            f"已对齐上一交易日 {sync.get('asof')}；"
            f"补数 {len(sync.get('filled') or [])} 只，缺失 {len(sync.get('missing') or [])} 只。"
            "A股指数见行情看板。"
        )
        return board


futures_market_service = FuturesMarketService()
