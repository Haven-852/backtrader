"""
策略选股：用最近 15 日日线判断前一交易日状态。
A 类：沿下降 MA5 下滑 3–12 日后止跌（阳线/锤子/十字/缩量小阴），观察随后 1–2 周反弹；下跌越短，预期上涨越短。两日阴跌不算。
B 类：升势回踩 MA5（反转候选）。
放量下跌直接抛弃。缺数则从 Datahubco 拉取日线写入 stock_daily。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.collectors.datahubco.client import DatahubcoClient
from data_layer.collectors.datahubco.selective import (
    BAR_COLS,
    SelectiveDataFetcher,
    _none_if_na,
    selective_fetcher,
)
from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

BATCH_CODES = 20
SLEEP = 0.16
LOOKBACK_BARS = 15
FILL_BARS = 20
MA_PERIOD = 5
SCORE_A_MIN = 55.0
SCORE_B_MIN = 60.0


class ScreenerService:
    def __init__(self):
        self._engine = None
        self._client = None
        self._selective: Optional[SelectiveDataFetcher] = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @property
    def client(self) -> DatahubcoClient:
        if self._client is None:
            # 全日线按交易日拉取，单日可能上万行，超时放宽
            self._client = DatahubcoClient(timeout=90)
        return self._client

    @property
    def selective(self) -> SelectiveDataFetcher:
        if self._selective is None:
            self._selective = selective_fetcher
        return self._selective

    def _ensure_tables(self) -> None:
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS strategy_screener_run (
                id           SERIAL PRIMARY KEY,
                asof         DATE NOT NULL,
                ma_period    INT DEFAULT 5,
                below_pct    NUMERIC(8,4) DEFAULT 0,
                filters      JSONB,
                hit_count    INT,
                filled_rows  INT,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS strategy_screener_hit (
                run_id       INT NOT NULL,
                ts_code      VARCHAR(16) NOT NULL,
                name         VARCHAR(64),
                market       VARCHAR(16),
                industry     VARCHAR(50),
                is_st        BOOLEAN,
                trade_date   DATE,
                close        NUMERIC(12,4),
                ma5          NUMERIC(12,4),
                below_pct    NUMERIC(12,4),
                setup_type   VARCHAR(8),
                score        NUMERIC(8,2),
                label        VARCHAR(32),
                pct_chg      NUMERIC(12,4),
                reason       TEXT,
                decline_days INT,
                expect_up_days INT,
                PRIMARY KEY (run_id, ts_code)
            )
            """,
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS setup_type VARCHAR(8)",
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS score NUMERIC(8,2)",
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS label VARCHAR(32)",
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS pct_chg NUMERIC(12,4)",
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS reason TEXT",
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS decline_days INT",
            "ALTER TABLE strategy_screener_hit ADD COLUMN IF NOT EXISTS expect_up_days INT",
            "CREATE INDEX IF NOT EXISTS idx_screener_hit_asof ON strategy_screener_hit (trade_date, below_pct DESC)",
            "CREATE INDEX IF NOT EXISTS idx_screener_hit_setup ON strategy_screener_hit (setup_type, score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_stock_daily_date ON stock_daily (trade_date)",
        ]
        with self.engine.begin() as conn:
            for sql in ddl:
                conn.execute(sa_text(sql))

    def _ensure_stock_basic(self) -> int:
        if not self.engine:
            return 0
        try:
            df = pd.read_sql(sa_text("SELECT COUNT(*) AS n FROM ref_stock_basic"), self.engine)
            n = int(df.iloc[0]["n"] or 0)
            if n > 100:
                return n
        except Exception:
            n = 0
        frames = []
        for ex in ("SSE", "SZSE", "BSE"):
            try:
                raw = self.client.query("stock_basic", exchange=ex, list_status="L")
            except Exception as e:
                logger.warning("stock_basic %s 失败: %s", ex, e)
                continue
            if raw is None or raw.empty:
                continue
            raw = raw.copy()
            raw.columns = [str(c).lower() for c in raw.columns]
            raw["list_status"] = raw.get("list_status", "L")
            frames.append(raw)
            time.sleep(SLEEP)
        if not frames:
            return n
        all_df = pd.concat(frames, ignore_index=True)
        sql = """
            INSERT INTO ref_stock_basic (
                ts_code, symbol, name, area, industry, cnspell, market,
                list_date, list_status
            ) VALUES (
                :ts_code, :symbol, :name, :area, :industry, :cnspell, :market,
                :list_date, :list_status
            )
            ON CONFLICT (ts_code) DO UPDATE SET
                name = EXCLUDED.name,
                market = COALESCE(EXCLUDED.market, ref_stock_basic.market),
                industry = COALESCE(EXCLUDED.industry, ref_stock_basic.industry),
                list_status = EXCLUDED.list_status
        """
        rows = []
        for _, r in all_df.iterrows():
            code = str(r.get("ts_code") or "").upper()
            if not code:
                continue
            list_date = r.get("list_date")
            if pd.notna(list_date):
                list_date = pd.to_datetime(str(list_date), errors="coerce")
                list_date = list_date.date() if pd.notna(list_date) else None
            else:
                list_date = None
            rows.append(
                {
                    "ts_code": code,
                    "symbol": str(r.get("symbol") or "")[:10],
                    "name": r.get("name"),
                    "area": r.get("area"),
                    "industry": r.get("industry"),
                    "cnspell": r.get("cnspell"),
                    "market": r.get("market"),
                    "list_date": list_date,
                    "list_status": str(r.get("list_status") or "L")[:1],
                }
            )
        if rows:
            with self.engine.begin() as conn:
                conn.execute(sa_text(sql), rows)
        return len(rows)

    def _universe_codes(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        df = pd.read_sql(
            sa_text(
                "SELECT ts_code, name, market, industry, list_status "
                "FROM ref_stock_basic WHERE COALESCE(list_status, 'L') = 'L'"
            ),
            self.engine,
        )
        if df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            code = str(r["ts_code"]).upper()
            name = str(r["name"] or "")
            market = str(r["market"] or "")
            inferred = self._infer_market(code, market)
            is_st = "ST" in name.upper() or "ST" in name
            if not filters.get("include_st", False) and is_st:
                continue
            if inferred == "主板" and not filters.get("include_main", True):
                continue
            if inferred == "科创板" and not filters.get("include_star", True):
                continue
            if inferred == "创业板" and not filters.get("include_chinext", True):
                continue
            if inferred == "北交所" and not filters.get("include_bse", False):
                continue
            if inferred == "CDR" and not filters.get("include_cdr", False):
                continue
            if inferred not in ("主板", "科创板", "创业板", "北交所", "CDR"):
                if not filters.get("include_main", True):
                    continue
            rows.append(
                {
                    "ts_code": code,
                    "name": name,
                    "market": inferred,
                    "industry": r["industry"] if pd.notna(r["industry"]) else "",
                    "is_st": is_st,
                }
            )
        return rows

    @staticmethod
    def _infer_market(ts_code: str, market: str) -> str:
        m = (market or "").strip()
        if m in ("主板", "科创板", "创业板", "北交所", "CDR"):
            return m
        code, _, exch = ts_code.partition(".")
        if exch == "BJ" or code.startswith(("8", "4")) and exch == "BJ":
            return "北交所"
        if code.startswith("688"):
            return "科创板"
        if code.startswith("300") or code.startswith("301"):
            return "创业板"
        if code.startswith("8") and exch == "SH":
            return "CDR"
        return m or "主板"

    def _session_days(self, asof: date, n: int) -> List[date]:
        self.selective.ensure_trade_cal(cover_from=asof - timedelta(days=n * 3))
        days = self.selective._expected_open_dates(asof - timedelta(days=n * 3), asof)
        if len(days) >= n:
            return days[-n:]
        return days

    def _day_count(self, d: date) -> int:
        df = pd.read_sql(
            sa_text("SELECT COUNT(*) AS n FROM stock_daily WHERE trade_date = :d"),
            self.engine,
            params={"d": d},
        )
        return int(df.iloc[0]["n"] or 0)

    def _normalize_daily(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=BAR_COLS)
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "ts_code" not in df.columns or "trade_date" not in df.columns:
            return pd.DataFrame(columns=BAR_COLS)
        df["ts_code"] = df["ts_code"].astype(str).str.upper()
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), errors="coerce").dt.date
        for col in ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = None
        if "vol" in df.columns:
            df["vol"] = pd.to_numeric(df["vol"], errors="coerce").round().astype("Int64")
        else:
            df["vol"] = None
        df = df.dropna(subset=["ts_code", "trade_date"])
        keep = [c for c in BAR_COLS if c in df.columns]
        return df[keep].drop_duplicates(subset=["ts_code", "trade_date"], keep="last")

    def _counts_by_day(self, start: date, end: date) -> Dict[date, int]:
        df = pd.read_sql(
            sa_text(
                "SELECT trade_date, COUNT(*) AS n FROM stock_daily "
                "WHERE trade_date BETWEEN :s AND :e GROUP BY trade_date"
            ),
            self.engine,
            params={"s": start, "e": end},
        )
        if df.empty:
            return {}
        out: Dict[date, int] = {}
        for _, r in df.iterrows():
            d = r["trade_date"]
            if hasattr(d, "date") and not isinstance(d, date):
                d = d.date()
            try:
                d = pd.Timestamp(d).date()
            except Exception:
                continue
            out[d] = int(r["n"] or 0)
        return out

    def _fill_window(self, days: Sequence[date], codes: Sequence[str]) -> Dict[str, Any]:
        """窗口内每个交易日覆盖不足时，按日从 Datahubco 拉全日线写入 stock_daily。"""
        report: Dict[str, Any] = {
            "source": "datahubco",
            "window_start": days[0].isoformat() if days else None,
            "window_end": days[-1].isoformat() if days else None,
            "universe": len(codes),
            "need_per_day": 0,
            "days_fetched": [],
            "days_skipped": [],
            "coverage": [],
            "errors": [],
            "rows": 0,
        }
        if not days or not codes:
            return report
        n_codes = max(1, len(codes))
        need = max(80, int(n_codes * 0.85))
        report["need_per_day"] = need
        counts = self._counts_by_day(days[0], days[-1])

        for d in days:
            before = int(counts.get(d, 0) or self._day_count(d))
            item: Dict[str, Any] = {
                "date": d.isoformat(),
                "before": before,
                "after": before,
                "fetched": False,
                "upserted": 0,
            }
            if before >= need:
                report["days_skipped"].append(d.isoformat())
                report["coverage"].append(item)
                continue
            ymd = d.strftime("%Y%m%d")
            try:
                raw = self.client.query("daily", trade_date=ymd)
                nd = self._normalize_daily(raw)
                n = 0
                if not nd.empty:
                    n = int(self.selective.upsert_bars("daily", nd))
                after = self._day_count(d)
                item.update({"after": after, "fetched": True, "upserted": n})
                report["days_fetched"].append(d.isoformat())
                report["rows"] += n
                counts[d] = after
                logger.info(
                    "screener Datahubco 补日线 %s：库内 %d → %d，写入 %d",
                    d,
                    before,
                    after,
                    n,
                )
            except Exception as e:
                logger.warning("Datahubco daily(%s) 失败: %s", ymd, e)
                report["errors"].append(f"{d.isoformat()}: {e}")
            report["coverage"].append(item)
            time.sleep(SLEEP)

        asof_n = counts.get(days[-1], self._day_count(days[-1]))
        report["asof_rows"] = int(asof_n)
        report["asof_enough"] = int(asof_n) >= need
        return report

    def _missing_codes(
        self, start: date, end: date, need_days: int, codes: Sequence[str]
    ) -> List[str]:
        if not codes:
            return []
        df = pd.read_sql(
            sa_text(
                "SELECT ts_code, COUNT(*) AS n FROM stock_daily "
                "WHERE trade_date BETWEEN :s AND :e "
                "GROUP BY ts_code"
            ),
            self.engine,
            params={"s": start, "e": end},
        )
        have = {str(r["ts_code"]): int(r["n"]) for _, r in df.iterrows()} if not df.empty else {}
        return [c for c in codes if have.get(c, 0) < need_days]

    @staticmethod
    def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return lo
        if v != v:
            return lo
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v

    @staticmethod
    def _finite(*vals: Any) -> bool:
        for v in vals:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return False
            if f != f:
                return False
        return True

    @staticmethod
    def _count_down_before(c) -> int:
        """D-1 往前连续收盘下跌的天数（不含 D0）。"""
        n = 0
        i = len(c) - 2
        while i >= 1:
            prev, cur = float(c[i - 1]), float(c[i])
            if prev <= 0 or cur <= 0 or prev != prev or cur != cur:
                break
            if cur < prev:
                n += 1
                i -= 1
            else:
                break
        return n

    @staticmethod
    def _recent_slide(c) -> int:
        """
        D-1 往前的本轮下滑天数（不含 D0）。
        允许中间夹 1 根弱反弹，以匹配沿均线下滑、中间偶有阳线的形态。
        """
        n = 0
        bounce = 0
        i = len(c) - 2
        while i >= 1:
            prev, cur = float(c[i - 1]), float(c[i])
            if prev <= 0 or cur <= 0 or prev != prev or cur != cur:
                break
            if cur < prev:
                n += 1
                i -= 1
                continue
            # 沿均线下滑中间允许 1 根弱反弹（必须是实际上涨，平盘不算）
            if bounce < 1 and prev < cur <= prev * 1.025:
                bounce += 1
                n += 1
                i -= 1
                continue
            break
        return n

    @staticmethod
    def _expect_up_days(n_down: int) -> int:
        """下跌越短，预期反弹越短；最长约两周（10 个交易日）。"""
        n = max(0, int(n_down))
        if n <= 3:
            return 4
        if n == 4:
            return 5
        if n == 5:
            return 7
        if n == 6:
            return 8
        return 10

    @classmethod
    def _is_hammer(cls, o: float, h: float, low: float, c: float) -> bool:
        if c <= 0:
            return False
        rng = max(h - low, abs(c) * 0.01)
        if rng / c < 0.012:
            return False
        lower_sh = (min(o, c) - low) / rng
        upper_sh = (h - max(o, c)) / rng
        body = abs(c - o) / rng
        return lower_sh >= 0.40 and body <= 0.45 and upper_sh <= 0.25

    @classmethod
    def _is_exhaustion(
        cls,
        o: float,
        h: float,
        low: float,
        c: float,
        vol: float,
        vol_ma: float,
        prev_ranges: Sequence[float],
    ) -> bool:
        """缩量小阴/小阳：实体和振幅比前几日明显变小。"""
        if c <= 0 or vol_ma <= 0:
            return False
        rng = max(h - low, abs(c) * 0.01)
        avg = float(sum(prev_ranges) / len(prev_ranges)) if prev_ranges else rng
        if avg <= 0:
            return False
        body = abs(c - o)
        return rng <= 0.75 * avg and body <= 0.65 * avg and vol <= 1.15 * vol_ma

    @classmethod
    def _is_up_yang(cls, o: float, h: float, low: float, c: float, prev: float) -> bool:
        """明确上涨阳线：收盘高于昨收和开盘，收在振幅上半区，不是十字。"""
        if prev <= 0 or c <= prev or c < o:
            return False
        rng = max(h - low, abs(c) * 0.01)
        body_close = abs(c - o) / c
        close_loc = (c - low) / rng
        return body_close >= 0.008 and close_loc >= 0.55 and (c / prev - 1.0) >= 0.005

    @classmethod
    def _is_bottom_doji(
        cls, o: float, h: float, low: float, c: float, vol: float, vol_ma: float
    ) -> bool:
        """底部十字星：小实体、长下影、缩量。"""
        if c <= 0 or vol_ma <= 0:
            return False
        rng = max(h - low, abs(c) * 0.01)
        body = abs(c - o) / rng
        body_close = abs(c - o) / c
        lower_sh = (min(o, c) - low) / rng
        upper_sh = (h - max(o, c)) / rng
        return (
            body <= 0.30
            and body_close <= 0.012
            and lower_sh >= 0.35
            and upper_sh <= lower_sh
            and rng / c >= 0.012
            and vol <= 1.0 * vol_ma
        )

    def _classify_one(self, g: pd.DataFrame, asof: date) -> Optional[Dict[str, Any]]:
        """判断单只股票 D0=asof 属于 A 底部观察、B 升势回踩，或抛弃。"""
        g = g.sort_values("trade_date").copy()
        for col in ("open", "high", "low", "close", "vol"):
            g[col] = pd.to_numeric(g[col], errors="coerce")
        if len(g) < LOOKBACK_BARS:
            return None
        g["ma5"] = g["close"].rolling(MA_PERIOD, min_periods=MA_PERIOD).mean()
        g["vol_ma5"] = g["vol"].rolling(MA_PERIOD, min_periods=MA_PERIOD).mean()
        g15 = g.tail(LOOKBACK_BARS)
        last = g15.iloc[-1]
        try:
            last_day = pd.Timestamp(last["trade_date"]).date()
        except Exception:
            return None
        if last_day != asof:
            return None
        if not self._finite(
            last["open"], last["high"], last["low"], last["close"], last["vol"], last["ma5"], last["vol_ma5"]
        ):
            return None

        o = g15["open"].to_numpy(dtype="float64")
        h = g15["high"].to_numpy(dtype="float64")
        low = g15["low"].to_numpy(dtype="float64")
        c = g15["close"].to_numpy(dtype="float64")
        v = g15["vol"].to_numpy(dtype="float64")
        ma = g15["ma5"].to_numpy(dtype="float64")
        vma = g15["vol_ma5"].to_numpy(dtype="float64")

        if c[-2] <= 0 or c[0] <= 0:
            return None
        if ma[-1] <= 0 or vma[-1] <= 0:
            return None
        if h[-1] < low[-1]:
            return None

        pct0 = c[-1] / c[-2] - 1.0
        below = (ma[-1] - c[-1]) / ma[-1]

        # 放量下跌：直接抛弃
        if (
            pct0 <= -0.02
            and c[-1] < o[-1]
            and v[-1] >= 1.5 * vma[-1]
            and c[-1] < ma[-1]
            and self._finite(ma[-3])
            and ma[-1] < ma[-3]
        ):
            return None

        ret_pre = c[-4] / c[0] - 1.0
        ma_up = self._finite(ma[-4], ma[-9]) and ma[-4] > ma[-9]
        above_pre = 0
        for i in range(0, 12):
            if self._finite(c[i], ma[i]) and c[i] > ma[i]:
                above_pre += 1
        uptrend_votes = int(ret_pre >= 0.05) + int(bool(ma_up)) + int(above_pre >= 8)
        is_uptrend = uptrend_votes >= 2

        # B：升势回踩 MA5（可推荐）
        if is_uptrend and c[-1] < ma[-1] and self._finite(ma[-2], ma[-3], c[-2], c[-3]):
            one_day = c[-2] >= ma[-2]
            two_day = c[-2] < ma[-2] and c[-3] >= ma[-3]
            if (
                (one_day or two_day)
                and 0.002 <= below <= 0.03
                and pct0 > -0.04
                and v[-1] <= 1.3 * vma[-1]
            ):
                trend = self._clip((ret_pre - 0.05) / 0.15)
                ma_slope = self._clip((ma[-4] / ma[-9] - 1.0) / 0.04) if ma[-9] > 0 else 0.0
                proximity = 1.0 - self._clip((below - 0.002) / 0.028)
                vol_ok = self._clip((1.3 - v[-1] / vma[-1]) / 0.8)
                score = 35 * trend + 20 * ma_slope + 30 * proximity + 15 * vol_ok
                if score >= SCORE_B_MIN:
                    return {
                        "setup_type": "B",
                        "score": round(float(score), 2),
                        "label": "升势回踩",
                        "reason": "两日回踩MA5" if two_day and not one_day else "一日回踩MA5",
                        "close": float(c[-1]),
                        "ma5": float(ma[-1]),
                        "below_pct": round(float(below * 100), 3),
                        "pct_chg": round(float(pct0 * 100), 3),
                    }

        # A：沿下降 MA5 下滑止跌，捕获随后 1–2 周反弹的起点。下跌越短，预期上涨越短。
        n_down = self._recent_slide(c)
        if n_down >= 3 and n_down <= 12 and c[-1] < ma[-1]:
            start_idx = -2 - n_down
            if start_idx >= -len(c) and float(c[start_idx]) > 0:
                drop = float(c[start_idx]) / float(c[-2]) - 1.0
                first_down = -1 - n_down
                ma_start = ma[first_down] if self._finite(ma[first_down]) else ma[-4]
                ma_falling = self._finite(ma_start) and ma[-1] <= ma_start
                swing_low = float(min(low[first_down:]))
                near_low = low[-1] <= swing_low * 1.02 or c[-1] <= swing_low * 1.04
                yang = self._is_up_yang(
                    float(o[-1]), float(h[-1]), float(low[-1]), float(c[-1]), float(c[-2])
                )
                doji = self._is_bottom_doji(
                    float(o[-1]), float(h[-1]), float(low[-1]), float(c[-1]), float(v[-1]), float(vma[-1])
                )
                hammer = self._is_hammer(float(o[-1]), float(h[-1]), float(low[-1]), float(c[-1]))
                prev_ranges = [
                    float(max(h[i] - low[i], abs(c[i]) * 0.01))
                    for i in range(max(0, len(c) - 4), len(c) - 1)
                    if self._finite(h[i], low[i], c[i])
                ]
                exhaust = self._is_exhaustion(
                    float(o[-1]), float(h[-1]), float(low[-1]), float(c[-1]),
                    float(v[-1]), float(vma[-1]), prev_ranges,
                )
                ok_shape = yang or doji or hammer or exhaust
                drop_ok = 0.035 <= drop <= 0.28
                vol_ok_a = v[-1] <= (1.4 * vma[-1] if yang else 1.2 * vma[-1])
                if ok_shape and drop_ok and near_low and ma_falling and vol_ok_a:
                    expect = self._expect_up_days(n_down)
                    timing = 1.0 if n_down in (3, 4, 6, 7) else 0.85
                    drop_score = self._clip((drop - 0.035) / 0.12)
                    rng = max(float(h[-1] - low[-1]), abs(float(c[-1])) * 0.01)
                    if yang:
                        rev_q = self._clip((c[-1] - low[-1]) / rng)
                        kind = "上涨"
                    elif hammer:
                        rev_q = self._clip(
                            (min(float(o[-1]), float(c[-1])) - float(low[-1])) / rng / 0.55
                        )
                        kind = "锤子止跌"
                    elif doji:
                        rev_q = self._clip(
                            (min(float(o[-1]), float(c[-1])) - float(low[-1])) / rng / 0.55
                        )
                        kind = "十字止跌"
                    else:
                        avg_rng = sum(prev_ranges) / max(len(prev_ranges), 1)
                        rev_q = self._clip(1.0 - rng / max(avg_rng, rng))
                        kind = "缩量止跌"
                    vol_s = self._clip((1.3 - v[-1] / vma[-1]) / 0.8)
                    score = 20 * timing + 20 * drop_score + 30 * rev_q + 15 * vol_s + 15
                    if score >= SCORE_A_MIN:
                        reason = f"沿MA5下滑连跌{n_down}日{kind}，预期反弹{expect}日"
                        return {
                            "setup_type": "A",
                            "score": round(float(score), 2),
                            "label": "底部观察",
                            "reason": reason,
                            "close": float(c[-1]),
                            "ma5": float(ma[-1]),
                            "below_pct": round(float(below * 100), 3),
                            "pct_chg": round(float(pct0 * 100), 3),
                            "decline_days": int(n_down),
                            "expect_up_days": int(expect),
                        }
        return None

    def _screen_setups(
        self,
        asof: date,
        universe: List[Dict[str, Any]],
        include_a: bool,
        include_b: bool,
        limit: int,
    ) -> pd.DataFrame:
        if not universe:
            return pd.DataFrame()
        info = {u["ts_code"]: u for u in universe}
        lookback = asof - timedelta(days=FILL_BARS * 3)
        raw = pd.read_sql(
            sa_text(
                "SELECT ts_code, trade_date, open, high, low, close, vol "
                "FROM stock_daily WHERE trade_date BETWEEN :s AND :e"
            ),
            self.engine,
            params={"s": lookback, "e": asof},
        )
        if raw.empty:
            return pd.DataFrame()
        code_set = set(info)
        raw = raw[raw["ts_code"].astype(str).str.upper().isin(code_set)].copy()
        if raw.empty:
            return pd.DataFrame()
        raw["ts_code"] = raw["ts_code"].astype(str).str.upper()
        raw["trade_date"] = pd.to_datetime(raw["trade_date"]).dt.date

        hits_b: List[Dict[str, Any]] = []
        hits_a: List[Dict[str, Any]] = []
        for code, g in raw.groupby("ts_code", sort=False):
            classified = self._classify_one(g, asof)
            if not classified:
                continue
            setup = classified["setup_type"]
            if setup == "B" and not include_b:
                continue
            if setup == "A" and not include_a:
                continue
            meta = info.get(code) or {}
            row = {
                "ts_code": code,
                "name": meta.get("name") or code,
                "market": meta.get("market") or "",
                "industry": meta.get("industry") or "",
                "is_st": bool(meta.get("is_st")),
                "trade_date": asof.isoformat(),
                **classified,
            }
            if setup == "B":
                hits_b.append(row)
            else:
                hits_a.append(row)

        hits_b.sort(key=lambda r: float(r["score"]), reverse=True)
        hits_a.sort(key=lambda r: float(r["score"]), reverse=True)
        limit = max(1, int(limit))
        if include_b and include_a:
            take_b = min(len(hits_b), max(1, int(limit * 0.67)))
            take_a = min(len(hits_a), max(0, limit - take_b))
        elif include_b:
            take_b, take_a = min(len(hits_b), limit), 0
        else:
            take_b, take_a = 0, min(len(hits_a), limit)
        picked = hits_b[:take_b] + hits_a[:take_a]
        if not picked:
            return pd.DataFrame()
        return pd.DataFrame(picked).reset_index(drop=True)

    def _save_run(
        self,
        asof: date,
        filters: Dict[str, Any],
        hits: pd.DataFrame,
        filled_rows: int,
    ) -> int:
        self._ensure_tables()
        with self.engine.begin() as conn:
            run_id = conn.execute(
                sa_text(
                    "INSERT INTO strategy_screener_run "
                    "(asof, ma_period, below_pct, filters, hit_count, filled_rows) "
                    "VALUES (:asof, :ma, :bp, CAST(:flt AS jsonb), :hc, :fr) RETURNING id"
                ),
                {
                    "asof": asof,
                    "ma": MA_PERIOD,
                    "bp": 0,
                    "flt": json.dumps(filters, ensure_ascii=False, default=str),
                    "hc": int(len(hits)),
                    "fr": int(filled_rows),
                },
            ).scalar()
            if hits is not None and not hits.empty:
                rows = []
                for _, r in hits.iterrows():
                    rows.append(
                        {
                            "run_id": int(run_id),
                            "ts_code": str(r["ts_code"]),
                            "name": r.get("name"),
                            "market": r.get("market"),
                            "industry": r.get("industry"),
                            "is_st": bool(r.get("is_st")),
                            "trade_date": r["trade_date"],
                            "close": _none_if_na(r["close"]),
                            "ma5": _none_if_na(r["ma5"]),
                            "below_pct": _none_if_na(r["below_pct"]),
                            "setup_type": r.get("setup_type"),
                            "score": _none_if_na(r["score"]) if "score" in r else None,
                            "label": r.get("label"),
                            "pct_chg": _none_if_na(r["pct_chg"]) if "pct_chg" in r else None,
                            "reason": r.get("reason"),
                            "decline_days": _none_if_na(r["decline_days"]) if "decline_days" in r else None,
                            "expect_up_days": _none_if_na(r["expect_up_days"]) if "expect_up_days" in r else None,
                        }
                    )
                conn.execute(
                    sa_text(
                        "INSERT INTO strategy_screener_hit "
                        "(run_id, ts_code, name, market, industry, is_st, trade_date, "
                        "close, ma5, below_pct, setup_type, score, label, pct_chg, reason, "
                        "decline_days, expect_up_days) "
                        "VALUES (:run_id, :ts_code, :name, :market, :industry, :is_st, :trade_date, "
                        ":close, :ma5, :below_pct, :setup_type, :score, :label, :pct_chg, :reason, "
                        ":decline_days, :expect_up_days) "
                        "ON CONFLICT (run_id, ts_code) DO UPDATE SET "
                        "close = EXCLUDED.close, ma5 = EXCLUDED.ma5, below_pct = EXCLUDED.below_pct, "
                        "setup_type = EXCLUDED.setup_type, score = EXCLUDED.score, "
                        "label = EXCLUDED.label, pct_chg = EXCLUDED.pct_chg, reason = EXCLUDED.reason, "
                        "decline_days = EXCLUDED.decline_days, expect_up_days = EXCLUDED.expect_up_days"
                    ),
                    rows,
                )
        return int(run_id)

    def screen_setups(
        self,
        asof: Optional[str] = None,
        include_st: bool = False,
        include_main: bool = True,
        include_star: bool = True,
        include_chinext: bool = True,
        include_bse: bool = False,
        include_cdr: bool = False,
        include_a: bool = True,
        include_b: bool = True,
        limit: int = 60,
        fill: bool = True,
    ) -> Dict[str, Any]:
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接"}
        if not include_a and not include_b:
            return {"ok": False, "error": "请至少选择 A 类底部观察或 B 类升势回踩"}
        self._ensure_tables()
        self.selective.ensure_trade_cal()
        ctx = self.selective.session_context()
        asof_d = ctx["asof_date"]
        if asof:
            try:
                asof_d = date.fromisoformat(str(asof)[:10])
            except Exception:
                return {"ok": False, "error": "asof 日期无效"}
        asof_d = min(asof_d, ctx["asof_date"])
        limit = max(10, min(int(limit or 60), 1000))

        filters = {
            "include_st": include_st,
            "include_main": include_main,
            "include_star": include_star,
            "include_chinext": include_chinext,
            "include_bse": include_bse,
            "include_cdr": include_cdr,
            "include_a": include_a,
            "include_b": include_b,
            "lookback": LOOKBACK_BARS,
        }
        basic_n = self._ensure_stock_basic()
        universe = self._universe_codes(filters)
        if not universe:
            return {"ok": False, "error": "股票池为空，请检查过滤条件或证券主档"}

        days = self._session_days(asof_d, FILL_BARS)
        fill_report: Dict[str, Any] = {
            "source": "datahubco",
            "rows": 0,
            "days_fetched": [],
            "days_skipped": [],
            "errors": [],
            "enabled": bool(fill),
        }
        if fill:
            fill_report = self._fill_window(days, [u["ts_code"] for u in universe])
            fill_report["enabled"] = True
        filled = int(fill_report.get("rows") or 0)

        hits = self._screen_setups(asof_d, universe, include_a, include_b, limit)
        run_id = self._save_run(asof_d, {**filters, "fill": fill_report}, hits, filled)
        records: List[Dict[str, Any]] = []
        if not hits.empty:
            for _, r in hits.iterrows():
                records.append(
                    {
                        "ts_code": str(r["ts_code"]),
                        "name": r["name"],
                        "market": r["market"],
                        "industry": r["industry"],
                        "is_st": bool(r["is_st"]),
                        "trade_date": r["trade_date"],
                        "setup_type": r["setup_type"],
                        "label": r["label"],
                        "reason": r["reason"],
                        "score": None if pd.isna(r["score"]) else round(float(r["score"]), 2),
                        "close": None if pd.isna(r["close"]) else round(float(r["close"]), 4),
                        "ma5": None if pd.isna(r["ma5"]) else round(float(r["ma5"]), 4),
                        "below_pct": None if pd.isna(r["below_pct"]) else round(float(r["below_pct"]), 3),
                        "pct_chg": None if pd.isna(r["pct_chg"]) else round(float(r["pct_chg"]), 3),
                        "decline_days": None if "decline_days" not in r or pd.isna(r["decline_days"]) else int(r["decline_days"]),
                        "expect_up_days": None if "expect_up_days" not in r or pd.isna(r["expect_up_days"]) else int(r["expect_up_days"]),
                    }
                )
        hits_b = [x for x in records if x["setup_type"] == "B"]
        hits_a = [x for x in records if x["setup_type"] == "A"]
        fill_errors = fill_report.get("errors") or []
        note = "B 升势回踩可推荐；A 观察沿MA5下滑止跌后的反弹起点，下跌越短预期上涨越短（约4–10个交易日）。两日阴跌不算底部。"
        if fill:
            fetched_n = len(fill_report.get("days_fetched") or [])
            if fetched_n:
                note += f" 已从 Datahubco 补齐 {fetched_n} 个交易日、写入 {filled} 行。"
            elif fill_report.get("days_skipped"):
                note += " 15 日窗口日线已齐，无需补拉。"
            if fill_errors:
                note += " 部分交易日补数失败，请看 fill_report.errors。"
            if fill_report.get("asof_enough") is False:
                note += " 所选交易日覆盖仍不足，分类结果可能偏少。"
        return {
            "ok": True,
            "run_id": run_id,
            "asof": asof_d.isoformat(),
            "lookback": LOOKBACK_BARS,
            "ma_period": MA_PERIOD,
            "filters": filters,
            "universe": len(universe),
            "stock_basic": basic_n,
            "window": [d.isoformat() for d in days],
            "filled_rows": filled,
            "fill_report": fill_report,
            "hit_count": len(records),
            "hit_count_b": len(hits_b),
            "hit_count_a": len(hits_a),
            "hits": records,
            "hits_b": hits_b,
            "hits_a": hits_a,
            "source": "datahubco",
            "table": "stock_daily",
            "note": note,
        }


screener_service = ScreenerService()
