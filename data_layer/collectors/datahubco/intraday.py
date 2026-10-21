"""
个股分时（1 分钟）：
  - 当日/盘中：优先读 Redis；缺失再拉源，写 Redis，并同步 Timescale `stock_intraday_mins`
  - 回看：先 Redis（TTL 内）→ Timescale → 远端补数入库；历史默认不强制回填 Redis
  - 当日日线：由分时聚合后 upsert 到已有 `stock_daily`
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .client import DatahubcoClient
from .selective import SelectiveDataFetcher, _none_if_na, selective_fetcher

logger = logging.getLogger("collector.datahubco.intraday")

TZ_SH = ZoneInfo("Asia/Shanghai")
REDIS_KEY = "intraday:{ts_code}:{ymd}"
REDIS_TTL_SEC = 48 * 3600
TABLE = "stock_intraday_mins"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS stock_intraday_mins (
    ts_code        VARCHAR(16)    NOT NULL,
    bar_time       TIMESTAMPTZ    NOT NULL,
    trade_date     DATE           NOT NULL,
    freq           VARCHAR(8)     NOT NULL DEFAULT '1min',
    open           NUMERIC(12,4),
    high           NUMERIC(12,4),
    low            NUMERIC(12,4),
    close          NUMERIC(12,4),
    vol            BIGINT,
    amount         NUMERIC(20,4),
    avg_price      NUMERIC(12,4),
    PRIMARY KEY (bar_time, ts_code)
)
"""

HYPER_SQL = (
    "SELECT create_hypertable('stock_intraday_mins', 'bar_time', if_not_exists => TRUE)"
)
INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_intraday_mins_code_date "
    "ON stock_intraday_mins (ts_code, trade_date, bar_time)"
)

AUCTION_DAILY_SQL = """
CREATE TABLE IF NOT EXISTS stock_auction_daily (
    ts_code         VARCHAR(16)  NOT NULL,
    trade_date      DATE         NOT NULL,
    open_price      NUMERIC(12,4),
    preclose_price  NUMERIC(12,4),
    auction_vol     BIGINT,
    auction_amount  NUMERIC(20,4),
    bid_vol_total   BIGINT,
    ask_vol_total   BIGINT,
    gap_pct         NUMERIC(12,4),
    PRIMARY KEY (ts_code, trade_date)
)
"""

UPSERT_AUCTION_DAILY_SQL = """
INSERT INTO stock_auction_daily (
    ts_code, trade_date, open_price, preclose_price,
    auction_vol, auction_amount, bid_vol_total, ask_vol_total, gap_pct
) VALUES (
    :ts_code, :trade_date, :open_price, :preclose_price,
    :auction_vol, :auction_amount, :bid_vol_total, :ask_vol_total, :gap_pct
)
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
    open_price = EXCLUDED.open_price,
    preclose_price = EXCLUDED.preclose_price,
    auction_vol = EXCLUDED.auction_vol,
    auction_amount = EXCLUDED.auction_amount,
    gap_pct = EXCLUDED.gap_pct
"""

UPSERT_SQL = f"""
INSERT INTO {TABLE} (
    ts_code, bar_time, trade_date, freq,
    open, high, low, close, vol, amount, avg_price
) VALUES (
    :ts_code, :bar_time, :trade_date, :freq,
    :open, :high, :low, :close, :vol, :amount, :avg_price
)
ON CONFLICT (bar_time, ts_code) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    vol = EXCLUDED.vol,
    amount = EXCLUDED.amount,
    avg_price = EXCLUDED.avg_price,
    freq = EXCLUDED.freq
"""


class IntradayFetcher:
    def __init__(
        self,
        client: Optional[DatahubcoClient] = None,
        daily: Optional[SelectiveDataFetcher] = None,
    ):
        self._client = client
        self._daily = daily
        self._table_ready = False

    @property
    def client(self) -> DatahubcoClient:
        if self._client is None:
            self._client = DatahubcoClient()
        return self._client

    @property
    def daily(self) -> SelectiveDataFetcher:
        if self._daily is None:
            self._daily = selective_fetcher
        return self._daily

    @property
    def engine(self):
        return self.daily.engine

    def _ensure_table(self) -> None:
        if self._table_ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        with self.engine.begin() as conn:
            conn.execute(sa_text(CREATE_SQL))
            try:
                conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
                conn.execute(sa_text(HYPER_SQL))
            except Exception as e:
                logger.warning("stock_intraday_mins 超表转换跳过: %s", e)
            conn.execute(sa_text(INDEX_SQL))
            conn.execute(sa_text(AUCTION_DAILY_SQL))
        self._table_ready = True

    def session_phase(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        分时展示日（不含未来数据）：
          - 交易日 09:15 前 → 上一交易日
          - 交易日 09:15–15:00 → 当天实时
          - 交易日收盘后 → 当天完整分时，并回写当日日线
          - 非交易日 → 上一交易日
        """
        self.daily.ensure_trade_cal()
        now = now or datetime.now(TZ_SH)
        if now.tzinfo is None:
            now = now.replace(tzinfo=TZ_SH)
        else:
            now = now.astimezone(TZ_SH)
        today = now.date()
        t = now.time()
        trade_today = self.daily.is_trade_day(today)
        prev = self.daily.previous_trade_day(today)
        if not trade_today:
            return {"phase": "non_trading", "trade_date": prev, "today": today, "now": now}
        if t < time(9, 15):
            return {"phase": "before_open", "trade_date": prev, "today": today, "now": now}
        if t < time(15, 0):
            return {"phase": "live", "trade_date": today, "today": today, "now": now}
        return {"phase": "after_close", "trade_date": today, "today": today, "now": now}

    def session_trade_date(self) -> date:
        return self.session_phase()["trade_date"]

    def _redis(self):
        return storage_manager.get_redis_client()

    def _redis_key(self, ts_code: str, d: date) -> str:
        return REDIS_KEY.format(ts_code=ts_code, ymd=d.strftime("%Y%m%d"))

    def _redis_get(self, ts_code: str, d: date) -> Optional[Dict[str, Any]]:
        r = self._redis()
        if not r:
            return None
        try:
            raw = r.get(self._redis_key(ts_code, d))
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("bars"):
                return data
        except Exception as e:
            logger.warning("Redis 读分时失败 %s: %s", ts_code, e)
        return None

    def _redis_set(self, payload: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        r = self._redis()
        if not r:
            return False
        try:
            ts_code = payload["ts_code"]
            d = date.fromisoformat(str(payload["trade_date"])[:10])
            r.setex(
                self._redis_key(ts_code, d),
                ttl or REDIS_TTL_SEC,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
            return True
        except Exception as e:
            logger.warning("Redis 写分时失败: %s", e)
            return False

    def _em_secid(self, ts_code: str) -> str:
        code, _, exch = ts_code.partition(".")
        if exch == "SH":
            return f"1.{code}"
        if exch == "BJ":
            return f"0.{code}"
        return f"0.{code}"

    def _qq_symbol(self, ts_code: str) -> str:
        code, _, exch = ts_code.partition(".")
        prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(exch, "sz")
        return f"{prefix}{code}"

    def _http_json(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 12) -> Dict[str, Any]:
        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Connection": "close",
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}

    def _in_regular_session(self, hhmm: str) -> bool:
        try:
            t = int(hhmm)
        except (TypeError, ValueError):
            return False
        return (930 <= t <= 1130) or (1300 <= t <= 1500)

    def _in_auction_session(self, hhmm: str) -> bool:
        try:
            t = int(hhmm)
        except (TypeError, ValueError):
            return False
        return 915 <= t <= 925

    def _clock(self, bt) -> Optional[time]:
        dt = self._parse_bar_time(bt)
        if not dt:
            return None
        if dt.tzinfo:
            dt = dt.astimezone(TZ_SH)
        return dt.time()

    def _is_auction_bar_time(self, bt) -> bool:
        t = self._clock(bt)
        return bool(t and time(9, 15) <= t <= time(9, 25))

    def _bar_is_auction_payload(self, bar: Dict[str, Any]) -> bool:
        if not isinstance(bar, dict):
            return False
        if bar.get("phase") == "auction" or bar.get("freq") == "auction":
            return True
        return self._is_auction_bar_time(bar.get("time"))

    def _normalize_mins_df(self, ts_code: str, df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["ts_code"] = ts_code
        if "bar_time" not in df.columns:
            if "trade_time" in df.columns:
                df["bar_time"] = df["trade_time"].map(self._parse_bar_time)
            elif "time" in df.columns:
                df["bar_time"] = df["time"].map(self._parse_bar_time)
            else:
                return pd.DataFrame()
        df = df.dropna(subset=["bar_time"])
        df["trade_date"] = df["bar_time"].map(lambda x: x.date() if x else None)
        df = df[df["trade_date"] == trade_date]
        for col in ("open", "high", "low", "close", "vol", "amount", "avg_price"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = None
        df = df.sort_values("bar_time")
        if df.empty:
            return df
        if "avg_price" not in df.columns or df["avg_price"].isna().all():
            amt = df["amount"].fillna(0).cumsum()
            vol = df["vol"].fillna(0).cumsum()
            df["avg_price"] = (amt / vol.replace(0, pd.NA)).astype(float)
        if "freq" not in df.columns:
            df["freq"] = "1min"
        else:
            df["freq"] = df["freq"].fillna("1min")
        return df.reset_index(drop=True)

    def fetch_tencent_today(self, ts_code: str, trade_date: date) -> pd.DataFrame:
        """腾讯分时：盘中实时 1 分钟（含当天正在交易的未完成行情）。"""
        symbol = self._qq_symbol(ts_code)
        qs = urllib.parse.urlencode({"code": symbol, "_": int(datetime.now(TZ_SH).timestamp() * 1000)})
        url = f"http://ifzq.gtimg.cn/appstock/app/minute/query?{qs}"
        try:
            payload = self._http_json(url, headers={"Referer": "https://gu.qq.com/"})
        except Exception as e:
            logger.warning("腾讯分时失败 %s: %s", ts_code, e)
            return pd.DataFrame()
        block = ((payload.get("data") or {}).get(symbol) or {})
        inner = block.get("data") or {}
        src_date = str(inner.get("date") or block.get("date") or "").replace("-", "")[:8]
        want = trade_date.strftime("%Y%m%d")
        if src_date and src_date != want:
            logger.info("腾讯分时 %s 日期 %s ≠ 请求 %s", ts_code, src_date, want)
            return pd.DataFrame()
        items = inner.get("data") if isinstance(inner, dict) else None
        if not isinstance(items, list) or not items:
            return pd.DataFrame()
        rows = []
        prev_close = None
        prev_vol = 0
        prev_amt = 0.0
        for item in items:
            parts = str(item).split()
            if len(parts) < 4:
                continue
            hhmm = parts[0].zfill(4)
            if not self._in_regular_session(hhmm):
                continue
            try:
                close = float(parts[1])
                cum_vol = int(float(parts[2]))
                cum_amt = float(parts[3])
            except (TypeError, ValueError):
                continue
            hour, minute = int(hhmm[:2]), int(hhmm[2:])
            bar_time = datetime.combine(trade_date, time(hour, minute), tzinfo=TZ_SH)
            vol = max(0, cum_vol - prev_vol)
            amount = max(0.0, cum_amt - prev_amt)
            prev_vol, prev_amt = cum_vol, cum_amt
            open_px = prev_close if prev_close is not None else close
            prev_close = close
            avg = None
            if cum_vol > 0:
                avg_lots = cum_amt / cum_vol
                avg_shares = cum_amt / (cum_vol * 100.0)
                avg = avg_shares if abs(avg_shares - close) <= abs(avg_lots - close) else avg_lots
            rows.append(
                {
                    "ts_code": ts_code,
                    "bar_time": bar_time,
                    "open": open_px,
                    "close": close,
                    "high": max(open_px, close),
                    "low": min(open_px, close),
                    "vol": vol,
                    "amount": amount,
                    "avg_price": avg,
                }
            )
        if not rows:
            return pd.DataFrame()
        df = self._normalize_mins_df(ts_code, pd.DataFrame(rows), trade_date)
        df = self._merge_mkline_ohlc(ts_code, trade_date, df)
        if not df.empty:
            logger.info("腾讯分时 %s %s 拉取 %d 根", ts_code, trade_date, len(df))
        return df

    def _merge_mkline_ohlc(self, ts_code: str, trade_date: date, df: pd.DataFrame) -> pd.DataFrame:
        """用腾讯 1 分钟 K 线补真实 OHLC，便于按本分钟涨跌判断买卖量。"""
        if df is None or df.empty:
            return df
        symbol = self._qq_symbol(ts_code)
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},m1,,320"
        try:
            payload = self._http_json(url, headers={"Referer": "https://gu.qq.com/"})
        except Exception as e:
            logger.warning("腾讯分钟K失败 %s: %s", ts_code, e)
            return df
        m1 = ((payload.get("data") or {}).get(symbol) or {}).get("m1") or []
        want = trade_date.strftime("%Y%m%d")
        ohlc: Dict[datetime, Dict[str, float]] = {}
        for item in m1:
            if not isinstance(item, (list, tuple)) or len(item) < 6:
                continue
            stamp = str(item[0]).replace("-", "").replace(":", "").replace(" ", "")
            if not stamp.startswith(want) or len(stamp) < 12:
                continue
            try:
                bt = datetime.strptime(stamp[:12], "%Y%m%d%H%M").replace(tzinfo=TZ_SH)
                ohlc[bt] = {
                    "open": float(item[1]),
                    "close": float(item[2]),
                    "high": float(item[3]),
                    "low": float(item[4]),
                }
            except (TypeError, ValueError):
                continue
        if not ohlc:
            return df
        df = df.copy()
        df["bar_time"] = pd.to_datetime(df["bar_time"])
        patched = 0
        for i, bt in enumerate(df["bar_time"]):
            key = bt.to_pydatetime() if hasattr(bt, "to_pydatetime") else bt
            if getattr(key, "tzinfo", None) is None:
                key = key.replace(tzinfo=TZ_SH)
            else:
                key = key.astimezone(TZ_SH)
            key = key.replace(second=0, microsecond=0)
            row = ohlc.get(key)
            if not row:
                continue
            df.at[i, "open"] = row["open"]
            df.at[i, "high"] = row["high"]
            df.at[i, "low"] = row["low"]
            df.at[i, "close"] = row["close"]
            patched += 1
        if patched:
            logger.info("腾讯分钟K 补 OHLC %s %s %d 根", ts_code, trade_date, patched)
        return df

    def fetch_eastmoney_auction(self, ts_code: str, trade_date: date) -> pd.DataFrame:
        """东财分时成交：09:15–09:25 集合竞价虚拟匹配，聚成 1 分钟。仅当天源可用。"""
        if trade_date != datetime.now(TZ_SH).date():
            return pd.DataFrame()
        code, _, exch = ts_code.partition(".")
        market = 1 if exch == "SH" else 0
        qs = urllib.parse.urlencode(
            {
                "pagesize": 20000,
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wzfscj",
                "pageindex": 0,
                "id": f"{code}1",
                "sort": 1,
                "ft": 1,
                "code": code,
                "market": market,
                "_": int(datetime.now(TZ_SH).timestamp() * 1000),
            }
        )
        url = f"http://push2ex.eastmoney.com/getStockFenShi?{qs}"
        try:
            payload = self._http_json(url, headers={"Referer": "https://quote.eastmoney.com/"})
        except Exception as e:
            logger.warning("东财集合竞价失败 %s: %s", ts_code, e)
            return pd.DataFrame()
        ticks = ((payload.get("data") or {}).get("data") or [])
        if not isinstance(ticks, list) or not ticks:
            return pd.DataFrame()
        buckets: Dict[int, List[Dict[str, float]]] = {}
        for item in ticks:
            if not isinstance(item, dict):
                continue
            try:
                raw_t = int(item.get("t") or 0)
                hhmm = raw_t // 100
                if not self._in_auction_session(str(hhmm).zfill(4)):
                    continue
                price = float(item.get("p") or 0) / 1000.0
                vol = float(item.get("v") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            buckets.setdefault(hhmm, []).append({"price": price, "vol": vol})
        if not buckets:
            return pd.DataFrame()
        rows = []
        prev_snap = 0.0
        for hhmm in sorted(buckets):
            pts = buckets[hhmm]
            prices = [x["price"] for x in pts]
            snap = pts[-1]["vol"]
            delta = max(0.0, snap - prev_snap)
            prev_snap = snap
            close = prices[-1]
            hour, minute = divmod(hhmm, 100)
            bar_time = datetime.combine(trade_date, time(hour, minute), tzinfo=TZ_SH)
            amount = delta * close * 100.0
            rows.append(
                {
                    "ts_code": ts_code,
                    "bar_time": bar_time,
                    "open": prices[0],
                    "high": max(prices),
                    "low": min(prices),
                    "close": close,
                    "vol": delta,
                    "amount": amount,
                    "avg_price": close,
                    "freq": "auction",
                    "match_vol": snap,
                }
            )
        df = self._normalize_mins_df(ts_code, pd.DataFrame(rows), trade_date)
        if not df.empty:
            df["freq"] = "auction"
            logger.info("集合竞价 %s %s 拉取 %d 根", ts_code, trade_date, len(df))
        return df

    def _combine_auction_and_session(self, auction: pd.DataFrame, session: pd.DataFrame) -> pd.DataFrame:
        if (auction is None or auction.empty) and (session is None or session.empty):
            return pd.DataFrame()
        sess = session.copy() if session is not None and not session.empty else pd.DataFrame()
        if not sess.empty and auction is not None and not auction.empty:
            match_vol = 0
            if "match_vol" in auction.columns and pd.notna(auction.iloc[-1].get("match_vol")):
                match_vol = int(float(auction.iloc[-1]["match_vol"]))
            else:
                match_vol = int(pd.to_numeric(auction["vol"], errors="coerce").fillna(0).sum())
            times = sess["bar_time"].map(self._clock)
            mask_930 = times.map(lambda t: t is not None and t.hour == 9 and t.minute == 30)
            if mask_930.any() and match_vol > 0:
                idx = sess.index[mask_930][0]
                cur = float(sess.at[idx, "vol"] or 0)
                new_vol = max(0.0, cur - match_vol)
                sess.at[idx, "vol"] = new_vol
                if pd.notna(sess.at[idx, "amount"]) and cur > 0:
                    sess.at[idx, "amount"] = float(sess.at[idx, "amount"] or 0) * (new_vol / cur)
            sess = sess.copy()
            sess["freq"] = sess["freq"].fillna("1min") if "freq" in sess.columns else "1min"
        parts = [x for x in (auction, sess) if x is not None and not x.empty]
        df = pd.concat(parts, ignore_index=True)
        df = df.sort_values("bar_time").drop_duplicates(subset=["bar_time"], keep="first")
        return df.reset_index(drop=True)

    def fetch_eastmoney_today(self, ts_code: str, trade_date: date) -> pd.DataFrame:
        """东财分时：盘中/当日实时 1 分钟（Datahubco stk_mins 经常不含未完成当天）。"""
        secid = self._em_secid(ts_code)
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "iscr": "0",
            "ndays": "1",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        last_err = None
        payload = None
        for base in (
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
            "https://push2.eastmoney.com/api/qt/stock/trends2/get",
        ):
            url = f"{base}?{urllib.parse.urlencode(params)}"
            try:
                payload = self._http_json(url, headers={"Referer": "https://quote.eastmoney.com/"})
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
                last_err = e
                continue
        if payload is None:
            logger.warning("东财分时失败 %s: %s", ts_code, last_err)
            return pd.DataFrame()
        data = payload.get("data") or {}
        trends = data.get("trends") or []
        rows = []
        for item in trends:
            parts = str(item).split(",")
            if len(parts) < 8:
                continue
            rows.append(
                {
                    "ts_code": ts_code,
                    "trade_time": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "vol": parts[5],
                    "amount": parts[6],
                    "avg_price": parts[7],
                }
            )
        if not rows:
            return pd.DataFrame()
        df = self._normalize_mins_df(ts_code, pd.DataFrame(rows), trade_date)
        if not df.empty:
            logger.info("东财分时 %s %s 拉取 %d 根", ts_code, trade_date, len(df))
        return df

    def _parse_bar_time(self, v) -> Optional[datetime]:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, datetime):
            dt = v
        else:
            dt = pd.to_datetime(v, errors="coerce")
            if pd.isna(dt):
                return None
            dt = dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_SH)
        else:
            dt = dt.astimezone(TZ_SH)
        return dt

    def fetch_stk_mins(self, ts_code: str, trade_date: date) -> pd.DataFrame:
        start = trade_date.strftime("%Y%m%d")
        end = (trade_date + timedelta(days=1)).strftime("%Y%m%d")
        try:
            df = self.client.query(
                "stk_mins",
                ts_code=ts_code,
                freq="1min",
                start_date=start,
                end_date=end,
            )
        except Exception as e:
            logger.warning("stk_mins 失败 %s %s: %s", ts_code, trade_date, e)
            return pd.DataFrame()
        return self._normalize_mins_df(ts_code, df, trade_date)

    def fetch_remote(self, ts_code: str, trade_date: date, live: bool = False) -> pd.DataFrame:
        today = datetime.now(TZ_SH).date()
        session_df = pd.DataFrame()
        if trade_date == today:
            qq = self.fetch_tencent_today(ts_code, trade_date)
            if qq is not None and not qq.empty:
                session_df = qq
            else:
                em = self.fetch_eastmoney_today(ts_code, trade_date)
                if em is not None and not em.empty:
                    session_df = em
        if session_df is None or session_df.empty:
            session_df = self.fetch_stk_mins(ts_code, trade_date)
        auction_df = pd.DataFrame()
        if trade_date == today:
            auction_df = self.fetch_eastmoney_auction(ts_code, trade_date)
        return self._combine_auction_and_session(auction_df, session_df)

    def _rows_from_df(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        rows = []
        for _, r in df.iterrows():
            bt = r["bar_time"]
            rows.append(
                {
                    "ts_code": str(r["ts_code"]),
                    "bar_time": bt,
                    "trade_date": r["trade_date"],
                    "freq": str(r.get("freq") or "1min"),
                    "open": _none_if_na(r.get("open")),
                    "high": _none_if_na(r.get("high")),
                    "low": _none_if_na(r.get("low")),
                    "close": _none_if_na(r.get("close")),
                    "vol": int(r["vol"]) if pd.notna(r.get("vol")) else None,
                    "amount": _none_if_na(r.get("amount")),
                    "avg_price": _none_if_na(r.get("avg_price")),
                }
            )
        return rows

    def upsert_mins(self, df: pd.DataFrame) -> int:
        self._ensure_table()
        if df is None or df.empty:
            return 0
        rows = self._rows_from_df(df)
        if not rows:
            return 0
        with self.engine.begin() as conn:
            conn.execute(sa_text(UPSERT_SQL), rows)
        logger.info("upsert %s %d 行", TABLE, len(rows))
        return len(rows)

    def upsert_daily_from_mins(self, ts_code: str, trade_date: date, df: pd.DataFrame) -> int:
        """用当日连续竞价分时合成日线，写入 stock_daily（不含集合竞价虚拟匹配）。"""
        if df is None or df.empty:
            return 0
        work = df.copy()
        skip_auc = work["bar_time"].map(self._is_auction_bar_time)
        if "freq" in work.columns:
            skip_auc = skip_auc | work["freq"].fillna("1min").eq("auction")
        work = work.loc[~skip_auc]
        if work.empty:
            return 0
        first = work.iloc[0]
        last = work.iloc[-1]
        pre = self._pre_close(ts_code, trade_date)
        close = float(last["close"]) if pd.notna(last.get("close")) else None
        change = (close - pre) if close is not None and pre else None
        pct = (change / pre * 100) if change is not None and pre else None
        one = pd.DataFrame(
            [
                {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "open": first.get("open"),
                    "high": work["high"].max(),
                    "low": work["low"].min(),
                    "close": close,
                    "pre_close": pre,
                    "change": change,
                    "pct_chg": pct,
                    "vol": work["vol"].sum(min_count=1),
                    "amount": work["amount"].sum(min_count=1),
                }
            ]
        )
        return self.daily.upsert_bars("daily", one, table="stock_daily")

    def upsert_auction_daily(self, ts_code: str, trade_date: date, df: pd.DataFrame) -> int:
        """集合竞价日汇总 → stock_auction_daily。"""
        if df is None or df.empty:
            return 0
        auc = df.copy()
        is_auc = auc["bar_time"].map(self._is_auction_bar_time)
        if "freq" in auc.columns:
            is_auc = is_auc | auc["freq"].fillna("1min").eq("auction")
        auc = auc.loc[is_auc]
        if auc.empty:
            return 0
        last = auc.iloc[-1]
        pre = self._pre_close(ts_code, trade_date)
        price = float(last["close"]) if pd.notna(last.get("close")) else None
        if "match_vol" in auc.columns and pd.notna(last.get("match_vol")):
            vol = int(float(last["match_vol"]))
        else:
            vol = int(pd.to_numeric(auc["vol"], errors="coerce").fillna(0).sum())
        amount = vol * price * 100 if price is not None else _none_if_na(last.get("amount"))
        gap = ((price - pre) / pre * 100) if price is not None and pre else None
        self._ensure_table()
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(UPSERT_AUCTION_DAILY_SQL),
                {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "open_price": price,
                    "preclose_price": pre,
                    "auction_vol": vol,
                    "auction_amount": amount,
                    "bid_vol_total": None,
                    "ask_vol_total": None,
                    "gap_pct": round(gap, 4) if gap is not None else None,
                },
            )
        logger.info("upsert stock_auction_daily %s %s vol=%s", ts_code, trade_date, vol)
        return 1

    def query_pg(self, ts_code: str, trade_date: date) -> pd.DataFrame:
        self._ensure_table()
        df = pd.read_sql(
            sa_text(
                f"SELECT ts_code, bar_time, trade_date, freq, open, high, low, close, "
                f"vol, amount, avg_price FROM {TABLE} "
                f"WHERE ts_code = :c AND trade_date = :d ORDER BY bar_time"
            ),
            self.engine,
            params={"c": ts_code, "d": trade_date},
        )
        if df.empty:
            return df
        df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True).dt.tz_convert(TZ_SH)
        return df

    def _pre_close(self, ts_code: str, trade_date: date) -> Optional[float]:
        if not self.engine:
            return None
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT close FROM stock_daily WHERE ts_code = :c AND trade_date < :d "
                    "ORDER BY trade_date DESC LIMIT 1"
                ),
                self.engine,
                params={"c": ts_code, "d": trade_date},
            )
            if df.empty or pd.isna(df.iloc[0]["close"]):
                return None
            return float(df.iloc[0]["close"])
        except Exception:
            return None

    def _stock_name(self, ts_code: str) -> str:
        if not self.engine:
            return ts_code
        try:
            df = pd.read_sql(
                sa_text("SELECT name FROM ref_stock_basic WHERE ts_code = :c LIMIT 1"),
                self.engine,
                params={"c": ts_code},
            )
            if not df.empty and df.iloc[0]["name"]:
                return str(df.iloc[0]["name"])
        except Exception:
            pass
        return ts_code

    def _is_stale(self, payload: Dict[str, Any], trade_date: date) -> bool:
        now = datetime.now(TZ_SH)
        phase = self.session_phase(now)["phase"]
        if trade_date != now.date():
            return False
        fetched = self._parse_bar_time(payload.get("fetched_at"))
        if phase == "live":
            if fetched and (now - fetched) <= timedelta(seconds=45):
                return False
            return True
        bars = payload.get("bars") or []
        if not bars:
            return True
        if any(not isinstance(b, dict) or "side" not in b for b in bars):
            return True
        now_t = now.time()
        if now_t >= time(9, 25) and not any(self._bar_is_auction_payload(b) for b in bars):
            return True
        auc = payload.get("auction") or {}
        try:
            price, vol, amt = float(auc.get("price") or 0), float(auc.get("vol") or 0), float(auc.get("amount") or 0)
            if price > 0 and vol > 0 and amt > 0 and amt < vol * price * 10:
                return True
        except (TypeError, ValueError):
            pass
        last_t = self._parse_bar_time(bars[-1].get("time"))
        if not last_t:
            return True
        # 收盘后必须拿到 15:00 完整当日，不沿用盘中半截缓存
        if phase == "after_close":
            lt = last_t.astimezone(TZ_SH).time() if last_t.tzinfo else last_t.time()
            return lt < time(15, 0)
        return False

    def _bars_payload(self, df: pd.DataFrame, pre_close: Optional[float] = None) -> List[Dict[str, Any]]:
        bars = []
        prev_close = pre_close
        for _, r in df.iterrows():
            bt = r["bar_time"]
            if hasattr(bt, "isoformat"):
                t = bt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                t = str(bt)
            o = _none_if_na(r.get("open"))
            h = _none_if_na(r.get("high"))
            l = _none_if_na(r.get("low"))
            c = _none_if_na(r.get("close"))
            # 1=买盘推升, -1=卖盘打压, 0=平。只用截至本分钟的价格，不含未来。
            side = 0
            if o is not None and c is not None and c != o:
                side = 1 if c > o else -1
            elif prev_close is not None and c is not None:
                if c > prev_close:
                    side = 1
                elif c < prev_close:
                    side = -1
            if c is not None:
                prev_close = c
            freq = str(r.get("freq") or "1min")
            phase = "auction" if freq == "auction" or self._is_auction_bar_time(bt) else "session"
            bars.append(
                {
                    "time": t,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "vol": int(r["vol"]) if pd.notna(r.get("vol")) else 0,
                    "amount": _none_if_na(r.get("amount")),
                    "avg_price": _none_if_na(r.get("avg_price")),
                    "side": side,
                    "freq": freq,
                    "phase": phase,
                }
            )
        return bars

    def _pack(
        self,
        ts_code: str,
        trade_date: date,
        df: pd.DataFrame,
        source: str,
        redis_written: bool,
        pg_written: int,
        daily_written: int,
        session: bool,
    ) -> Dict[str, Any]:
        pre = self._pre_close(ts_code, trade_date)
        bars = self._bars_payload(df, pre_close=pre)
        last = bars[-1] if bars else None
        close = last["close"] if last else None
        change = (close - pre) if close is not None and pre else None
        pct = round(change / pre * 100, 4) if change is not None and pre else None
        now = datetime.now(TZ_SH)
        phase_info = self.session_phase(now)
        phase = phase_info["phase"] if trade_date == now.date() else "history"
        in_live_window = session and phase == "live" and trade_date == now.date()
        session_bars = [b for b in bars if b.get("phase") != "auction"]
        auction_bars = [b for b in bars if b.get("phase") == "auction"]
        stats_bars = session_bars or bars
        highs = [b["high"] if b.get("high") is not None else b.get("close") for b in stats_bars]
        lows = [b["low"] if b.get("low") is not None else b.get("close") for b in stats_bars]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        session_high = max(highs) if highs else None
        session_low = min(lows) if lows else None
        auc_last = auction_bars[-1] if auction_bars else None
        auc_price = auc_last["close"] if auc_last else None
        auc_vol = int(sum(int(b.get("vol") or 0) for b in auction_bars)) if auction_bars else None
        auc_amount = (auc_vol * auc_price * 100) if auc_vol is not None and auc_price is not None else None
        auc_gap = round((auc_price - pre) / pre * 100, 4) if auc_price is not None and pre else None
        return {
            "ok": True,
            "ts_code": ts_code,
            "name": self._stock_name(ts_code),
            "trade_date": trade_date.isoformat(),
            "timeframe": "intraday",
            "chart_type": "intraday",
            "session": session,
            "session_phase": phase,
            "source": source,
            "redis_written": redis_written,
            "pg_written": pg_written,
            "daily_written": daily_written,
            "pre_close": pre,
            "last": close,
            "change": round(change, 4) if change is not None else None,
            "pct_chg": pct,
            "session_open": (session_bars[0]["open"] if session_bars else None) or (auc_price),
            "session_high": session_high,
            "session_low": session_low,
            "session_vwap": (session_bars[-1].get("avg_price") if session_bars else None) or (last.get("avg_price") if last else None),
            "at_session_high": bool(close is not None and session_high is not None and close >= session_high),
            "at_session_low": bool(close is not None and session_low is not None and close <= session_low),
            "auction": {
                "price": auc_price,
                "vol": auc_vol,
                "amount": auc_amount,
                "gap_pct": auc_gap,
                "count": len(auction_bars),
            } if auction_bars else None,
            "bars": bars,
            "count": len(bars),
            "live": in_live_window,
            "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "indicators": {},
        }

    def redis_bars_count(self, ts_code: str, trade_date: date) -> int:
        cached = self._redis_get(ts_code, trade_date)
        if not cached or self._is_stale(cached, trade_date):
            return 0
        return len(cached.get("bars") or [])

    def peek_redis(
        self,
        ts_code: str,
        trade_date: date,
        *,
        allow_stale: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """只读 Redis 分时缓存；不过期或 allow_stale 时返回。"""
        cached = self._redis_get(ts_code, trade_date)
        if not cached:
            return None
        if (not allow_stale) and self._is_stale(cached, trade_date):
            return None
        out = dict(cached)
        out["source"] = "redis"
        out["redis_written"] = False
        out["pg_written"] = 0
        out["daily_written"] = 0
        out["ok"] = True
        return out

    def get(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        history: bool = False,
    ) -> Dict[str, Any]:
        ts_code = self.daily.normalize_ts_code(ts_code)
        if not ts_code:
            return {"ok": False, "error": "请填写标的代码", "bars": [], "count": 0}
        self.daily.ensure_trade_cal()
        phase_info = self.session_phase()
        session_d = phase_info["trade_date"]
        phase = phase_info["phase"]
        if trade_date:
            d = datetime.strptime(str(trade_date).replace("-", "")[:8], "%Y%m%d").date()
        else:
            d = session_d
        session = (not history) and d == session_d
        after_close = phase == "after_close" and d == session_d

        # 1) 任何日期都先查 Redis（盘中/当日主缓存；TTL 内历史也可命中）
        cached = self.peek_redis(ts_code, d, allow_stale=False)
        if cached:
            cached["session"] = session
            cached["session_phase"] = phase
            cached["live"] = phase == "live" and d == datetime.now(TZ_SH).date() and session
            return cached

        # 2) 非当日会话：再查 Timescale
        if not session:
            pg_df = self.query_pg(ts_code, d)
            if pg_df is not None and len(pg_df) >= 60:
                return self._pack(ts_code, d, pg_df, "timescale", False, 0, 0, False)

        # 3) 本地都没有 → Datahubco / 东财腾讯补数
        df = self.fetch_remote(ts_code, d, live=session)

        if df is None or df.empty:
            return {
                "ok": False,
                "error": f"{ts_code} {d.isoformat()} 无分时数据",
                "ts_code": ts_code,
                "trade_date": d.isoformat(),
                "session_phase": phase,
                "timeframe": "intraday",
                "chart_type": "intraday",
                "bars": [],
                "count": 0,
                "source": "empty",
            }

        # 当日：先 Redis 出图，再落 Timescale / 竞价日表
        packed = None
        redis_ok = False
        if session:
            packed = self._pack(ts_code, d, df, "redis", False, 0, 0, True)
            ttl = 60 if phase == "live" else REDIS_TTL_SEC
            redis_ok = self._redis_set(packed, ttl=ttl)
            packed["redis_written"] = redis_ok

        pg_n = self.upsert_mins(df)
        auction_n = self.upsert_auction_daily(ts_code, d, df)
        daily_n = 0
        if after_close:
            daily_n = self.upsert_daily_from_mins(ts_code, d, df)

        if session and packed is not None:
            packed["pg_written"] = pg_n
            packed["daily_written"] = daily_n
            packed["source"] = "live" if packed.get("live") else ("redis" if redis_ok else "timescale")
            if packed.get("live"):
                label = "盘中实时分时"
            elif after_close:
                label = "当日已收盘分时"
            elif phase == "before_open":
                label = "开盘前·上一交易日分时"
            else:
                label = "上一交易日分时"
            auc = packed.get("auction") or {}
            auc_msg = f"，集合竞价 {auc.get('count')} 根" if auc.get("count") else ""
            packed["message"] = (
                f"{label}"
                f"{'，Redis 已缓存' if redis_ok else ''}"
                f"{auc_msg}"
                f"，Timescale 写入 {pg_n} 根"
                + (f"，竞价日表 {auction_n} 行" if auction_n else "")
                + (f"，日线 {daily_n} 行" if daily_n else "")
            )
            return packed

        packed = self._pack(ts_code, d, df, "timescale", False, pg_n, daily_n, False)
        packed["message"] = f"历史只读 Timescale，已入库 {pg_n} 根分时，日线 {daily_n} 行（未回填 Redis）"
        return packed


intraday_fetcher = IntradayFetcher()
