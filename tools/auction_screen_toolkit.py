"""
主板集合竞价强买筛选

宇宙：昨日表现偏强主板股 ∪ 昨日涨停池（非整主板全扫）
窗口：09:20–09:25
阈值：窗内成交额达到宇宙 P95，且竞价价升（强买代理）
说明：纯规则模板，不含 LLM
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")

# 昨日「表现强」：涨幅阈值 + 名额上限，避免宇宙过大
STRONG_PCT = 5.0
STRONG_TOP_N = 80
LIMIT_UP_TYPES = ("U", "Z")  # 涨停；炸板也纳入观察池
AUCTION_START = time(9, 20)
AUCTION_END = time(9, 25)
MAX_WORKERS = 8


def _parse_day(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    s = str(raw).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        x = float(v)
        if x != x:
            return default
        return x
    except (TypeError, ValueError):
        return default


def _is_mainboard(ts_code: str) -> bool:
    """沪主板 60xxxx.SH + 深主板 00xxxx.SZ（不含 300/688/8/4 北证）。"""
    code = (ts_code or "").strip().upper()
    if code.endswith(".BJ"):
        return False
    num, _, exch = code.partition(".")
    if len(num) != 6 or not num.isdigit():
        return False
    if exch == "SH":
        return num.startswith("60")
    if exch == "SZ":
        return num.startswith("00")
    return False


def _is_st_name(name: Optional[str]) -> bool:
    n = str(name or "")
    return "ST" in n.upper()


class AuctionScreenToolkit:
    def screen_mainboard_buy(
        self,
        trade_date: Optional[str] = None,
        top_n: int = 20,
        strong_pct: float = STRONG_PCT,
        strong_top_n: int = STRONG_TOP_N,
        fetch_missing: bool = True,
    ) -> Dict[str, Any]:
        """
        筛选 09:20–09:25 竞价量能达宇宙 P95、且价升的主板票。
        """
        try:
            from data_layer.db_manager import storage_manager

            eng = storage_manager.get_postgres_engine()
            if eng is None:
                return {"ok": False, "error": "PostgreSQL 不可用"}
        except Exception as e:
            return {"ok": False, "error": f"数据库未就绪: {e}"}

        asof = _parse_day(trade_date) or self._session_trade_date()
        bootstrap_notes: List[str] = []
        prev = self._prev_trade_day(eng, asof)
        if not prev:
            return {"ok": False, "error": f"找不到 {asof.isoformat()} 的上一交易日"}

        # 缺日线/涨停池时先从 Datahubco 补到 PG
        boot = self._ensure_universe_data(eng, prev)
        bootstrap_notes.extend(boot)

        universe = self._build_universe(
            eng, prev, strong_pct=float(strong_pct), strong_top_n=int(strong_top_n)
        )
        if not universe:
            return {
                "ok": False,
                "error": (
                    f"宇宙为空：{prev.isoformat()} 无昨日强势/涨停主板样本。"
                    f"补数尝试：{'；'.join(bootstrap_notes) or '无'}。"
                    "可先对主板热门票跑 datahubco_data__get_bars(stock_daily)，"
                    "并用 get_fund_data(limit_list) 补涨停榜。"
                ),
                "asof": asof.isoformat(),
                "prev_trade_date": prev.isoformat(),
                "bootstrap": bootstrap_notes,
            }

        codes = list(universe.keys())
        features = self._collect_auction_features(
            eng, asof, codes, fetch_missing=bool(fetch_missing)
        )
        scored = [f for f in features if f.get("amount") is not None and _f(f.get("amount")) > 0]
        missing_auction = [
            f["ts_code"] for f in features if not f.get("amount") or _f(f.get("amount")) <= 0
        ]
        # 优先让模型补：昨涨停 > 昨强 > 其余
        missing_auction = self._prioritize_codes(missing_auction, universe)

        coverage_pct = round(100.0 * len(scored) / max(1, len(codes)), 2)
        # 覆盖不足时：结论不可信，返回明确补数指令给大模型
        min_cover = max(15, int(len(codes) * 0.25))
        incomplete = len(scored) < min_cover

        gaps_payload = self._auction_data_gaps(
            asof=asof,
            prev=prev,
            missing_auction=missing_auction,
            incomplete=incomplete,
            with_auction=len(scored),
            universe_count=len(codes),
        )

        if not scored:
            return {
                "ok": True,
                "incomplete": True,
                "asof": asof.isoformat(),
                "prev_trade_date": prev.isoformat(),
                "universe_count": len(codes),
                "scanned": len(features),
                "with_auction": 0,
                "coverage_pct": 0.0,
                "p95_amount": None,
                "candidates": [],
                "count": 0,
                "message": "宇宙内暂无 09:20–09:25 竞价数据，结论不可用。",
                "bootstrap": bootstrap_notes,
                **gaps_payload,
                "note": "强买为量能代理指标，非 Level-2 主动买入方向。",
            }

        amounts = sorted(_f(f["amount"]) for f in scored)
        p95 = self._percentile(amounts, 95)
        picks = []
        for f in scored:
            amt = _f(f["amount"])
            lift = _f(f.get("lift_pct"))
            if amt < p95:
                continue
            if lift < 0:
                continue
            code = f["ts_code"]
            meta = universe.get(code) or {}
            hist = self._prev_n_days(eng, code, asof, n=3)
            reason = self._reason_template(f, meta, hist, p95, prev)
            score = round(amt / p95 * (1.0 + max(0.0, lift) / 100.0), 4)
            picks.append(
                {
                    "ts_code": code,
                    "name": meta.get("name") or f.get("name") or code,
                    "score": score,
                    "auction_amount": round(amt, 2),
                    "auction_vol": int(_f(f.get("vol"))),
                    "lift_pct": round(lift, 3),
                    "gap_pct": None if f.get("gap_pct") is None else round(_f(f.get("gap_pct")), 3),
                    "open_price": f.get("open_price"),
                    "close_price": f.get("close_price"),
                    "source_tags": meta.get("tags") or [],
                    "prev_day_pct": meta.get("prev_pct"),
                    "prev_3d": hist,
                    "reason": reason,
                    "auction_source": f.get("source"),
                }
            )

        picks.sort(key=lambda x: (-_f(x.get("score")), -_f(x.get("auction_amount"))))
        top_n = max(1, min(int(top_n or 20), 50))
        out = {
            "ok": True,
            "incomplete": incomplete,
            "tool": "主板集合竞价强买",
            "asof": asof.isoformat(),
            "prev_trade_date": prev.isoformat(),
            "window": "09:20-09:25",
            "universe_count": len(codes),
            "scanned": len(features),
            "with_auction": len(scored),
            "coverage_pct": coverage_pct,
            "p95_amount": round(p95, 2),
            "threshold": "auction_amount >= P95 且窗内 lift_pct >= 0",
            "candidates": picks[:top_n],
            "count": min(len(picks), top_n),
            "bootstrap": bootstrap_notes,
            "note": (
                "宇宙=昨日涨幅偏强主板 ∪ 昨日涨停/炸板池；"
                "强买用竞价成交额分位代理，不是交易所买卖方向字段。"
            ),
        }
        if incomplete:
            out.update(gaps_payload)
            out["message"] = (
                f"竞价覆盖仅 {coverage_pct}%（{len(scored)}/{len(codes)}），"
                "P95 结论不可信。请先按 suggested_tool_calls 补数后重跑本工具。"
            )
        return out

    # ------------------------------------------------------------------ #
    def _prioritize_codes(
        self, codes: List[str], universe: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        def key(c: str):
            meta = universe.get(c) or {}
            tags = meta.get("tags") or []
            rank = 0
            if "昨涨停" in tags:
                rank += 100
            if "昨炸板" in tags:
                rank += 50
            if "昨强" in tags:
                rank += 20
            return (-rank, -_f(meta.get("prev_pct")), c)

        return sorted(codes, key=key)

    def _auction_data_gaps(
        self,
        *,
        asof: date,
        prev: date,
        missing_auction: List[str],
        incomplete: bool,
        with_auction: int,
        universe_count: int,
    ) -> Dict[str, Any]:
        if not incomplete and not missing_auction:
            return {
                "incomplete": False,
                "data_gaps": [],
                "suggested_tool_calls": [],
                "agent_instruction": "",
            }
        ymd = asof.isoformat()
        gaps = [
            {
                "kind": "auction_mins",
                "table": "stock_intraday_mins",
                "window": "09:20-09:25",
                "missing_count": len(missing_auction),
                "with_auction": with_auction,
                "universe_count": universe_count,
                "ts_codes": missing_auction[:40],
                "message": (
                    f"{asof.isoformat()} 竞价分钟覆盖不足："
                    f"有数据 {with_auction}/{universe_count}。"
                    "请用 datahubco_data__get_mins 补缺失标的（include_auction=true）。"
                ),
            }
        ]
        calls: List[Dict[str, Any]] = [
            {
                "tool": "datahubco_data__get_fund_data",
                "arguments": {
                    "dataset": "limit_list",
                    "trade_date": prev.strftime("%Y%m%d"),
                    "source": "datahubco",
                    "write_back": True,
                    "limit": 5000,
                },
                "why": "确保昨日涨停池完整",
            }
        ]
        # 限制调用数量，避免一轮打爆；优先前 12 只
        for code in missing_auction[:12]:
            calls.append(
                {
                    "tool": "datahubco_data__get_mins",
                    "arguments": {
                        "ts_code": code,
                        "trade_date": ymd,
                        "include_auction": True,
                        "source": "auto",
                        "write_back": True,
                        "limit": 500,
                    },
                    "why": f"补 {code} 的竞价/分时分钟到 PG/Redis",
                }
            )
        return {
            "data_gaps": gaps,
            "suggested_tool_calls": calls,
            "agent_instruction": (
                "数据不完整。请立即按 suggested_tool_calls 顺序调用 Datahubco 工具补数；"
                "全部成功后重新调用 auction_screen__mainboard_buy（同一 trade_date）。"
                "在补数完成前不要给出「今日无强买」的最终结论。"
            ),
        }

    def _ensure_universe_data(self, eng, prev: date) -> List[str]:
        """确保昨日日线与涨停榜可用；缺则 Datahubco 补入库。"""
        notes: List[str] = []
        ymd = prev.strftime("%Y%m%d")
        with eng.connect() as conn:
            n_daily = conn.execute(
                sa_text("SELECT COUNT(1) FROM stock_daily WHERE trade_date = :d"),
                {"d": prev},
            ).scalar() or 0
            try:
                n_lim = conn.execute(
                    sa_text("SELECT COUNT(1) FROM stock_limit_list_daily WHERE trade_date = :d"),
                    {"d": prev},
                ).scalar() or 0
            except Exception:
                n_lim = 0

        if int(n_daily) < 200:
            notes.append(f"stock_daily@{prev} 仅 {n_daily} 行，尝试批量补数")
            try:
                from tools.datahubco_toolkit import datahubco_toolkit
                # 用指数成分不够；改为拉涨停榜 + 部分热门日线由 limit_list 触发
                # 先补涨停榜，日线用 get_bars 对涨停/强势候选补
            except Exception as e:
                notes.append(f"toolkit 不可用: {e}")

        if int(n_lim) < 1:
            try:
                from tools.datahubco_toolkit import datahubco_toolkit

                res = datahubco_toolkit.get_fund_data(
                    dataset="limit_list",
                    trade_date=ymd,
                    source="datahubco",
                    write_back=True,
                    limit=5000,
                )
                notes.append(
                    f"补涨停榜 limit_list {ymd}: ok={res.get('ok')} "
                    f"rows={res.get('count') or res.get('upserted') or 0} "
                    f"src={res.get('source_used')}"
                )
            except Exception as e:
                notes.append(f"补涨停榜失败: {e}")

        # 对涨停池个股补昨日日线（供昨强/近3日）
        try:
            with eng.connect() as conn:
                lim_codes = [
                    str(r[0]).upper()
                    for r in conn.execute(
                        sa_text(
                            """
                            SELECT DISTINCT ts_code FROM stock_limit_list_daily
                            WHERE trade_date = :d AND limit_type = ANY(:k)
                            LIMIT 120
                            """
                        ),
                        {"d": prev, "k": list(LIMIT_UP_TYPES)},
                    ).fetchall()
                    if r[0]
                ]
            if lim_codes:
                from tools.datahubco_toolkit import datahubco_toolkit

                filled = 0
                for code in lim_codes[:80]:
                    if not _is_mainboard(code):
                        continue
                    try:
                        r = datahubco_toolkit.get_bars(
                            ts_code=code,
                            dataset="stock_daily",
                            start_date=(prev - timedelta(days=10)).isoformat(),
                            end_date=prev.isoformat(),
                            limit=20,
                            source="auto",
                            write_back=True,
                        )
                        if r.get("ok") and (r.get("count") or 0) > 0:
                            filled += 1
                    except Exception:
                        continue
                notes.append(f"为涨停池补日线 {filled}/{len(lim_codes)} 只")
        except Exception as e:
            notes.append(f"补日线跳过: {e}")
        return notes

    def _session_trade_date(self) -> date:
        try:
            from data_layer.collectors.datahubco.intraday import intraday_fetcher

            return intraday_fetcher.session_phase().get("trade_date") or datetime.now(SH).date()
        except Exception:
            return datetime.now(SH).date()

    def _prev_trade_day(self, eng, asof: date) -> Optional[date]:
        with eng.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT MAX(trade_date) AS d FROM stock_daily
                    WHERE trade_date < :d
                    """
                ),
                {"d": asof},
            ).fetchone()
        if row and row[0]:
            d = row[0]
            return d.date() if isinstance(d, datetime) else d
        return asof - timedelta(days=1)

    def _build_universe(
        self,
        eng,
        prev: date,
        *,
        strong_pct: float,
        strong_top_n: int,
    ) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        with eng.connect() as conn:
            # 昨日强势
            strong_rows = conn.execute(
                sa_text(
                    """
                    SELECT d.ts_code, COALESCE(b.name, '') AS name, d.pct_chg, d.amount, d.close
                    FROM stock_daily d
                    LEFT JOIN ref_stock_basic b ON b.ts_code = d.ts_code
                    WHERE d.trade_date = :d
                      AND d.pct_chg IS NOT NULL
                    ORDER BY d.pct_chg DESC
                    LIMIT 800
                    """
                ),
                {"d": prev},
            ).fetchall()
            main_strong: List[Any] = []
            for r in strong_rows:
                code = str(r.ts_code or "").upper()
                name = str(r.name or "")
                if not _is_mainboard(code) or _is_st_name(name):
                    continue
                main_strong.append(r)

            # 涨幅 >= 阈值的全收；不足再用涨幅 TopN 补齐
            picked = 0
            for r in main_strong:
                pct = _f(r.pct_chg)
                if pct < strong_pct:
                    continue
                code = str(r.ts_code).upper()
                out[code] = {
                    "name": str(r.name or code),
                    "prev_pct": round(pct, 3),
                    "prev_amount": _f(r.amount),
                    "prev_close": _f(r.close),
                    "tags": ["昨强"],
                }
                picked += 1
            if picked < strong_top_n:
                for r in main_strong:
                    code = str(r.ts_code).upper()
                    if code in out:
                        continue
                    pct = _f(r.pct_chg)
                    out[code] = {
                        "name": str(r.name or code),
                        "prev_pct": round(pct, 3),
                        "prev_amount": _f(r.amount),
                        "prev_close": _f(r.close),
                        "tags": ["昨强"],
                    }
                    picked += 1
                    if picked >= strong_top_n:
                        break

            # 昨日涨停 / 炸板
            try:
                lim_rows = conn.execute(
                    sa_text(
                        """
                        SELECT ts_code, COALESCE(name, '') AS name, limit_type, pct_chg
                        FROM stock_limit_list_daily
                        WHERE trade_date = :d AND limit_type = ANY(:kinds)
                        """
                    ),
                    {"d": prev, "kinds": list(LIMIT_UP_TYPES)},
                ).fetchall()
            except Exception as e:
                logger.warning("涨停池读取失败: %s", e)
                lim_rows = []
            for r in lim_rows:
                code = str(r.ts_code or "").upper()
                name = str(r.name or "")
                if not _is_mainboard(code) or _is_st_name(name):
                    continue
                tags = out.get(code, {}).get("tags") or []
                tag = "昨涨停" if str(r.limit_type) == "U" else "昨炸板"
                if tag not in tags:
                    tags = list(tags) + [tag]
                base = out.get(code) or {}
                out[code] = {
                    "name": name or base.get("name") or code,
                    "prev_pct": base.get("prev_pct")
                    if base.get("prev_pct") is not None
                    else round(_f(r.pct_chg), 3),
                    "prev_amount": base.get("prev_amount"),
                    "prev_close": base.get("prev_close"),
                    "tags": tags,
                }
        return out

    def _collect_auction_features(
        self,
        eng,
        asof: date,
        codes: List[str],
        *,
        fetch_missing: bool,
    ) -> List[Dict[str, Any]]:
        # 批量先读 PG
        pg_map = self._load_auction_from_pg(eng, asof, codes)
        missing = [c for c in codes if c not in pg_map]
        remote_map: Dict[str, Dict[str, Any]] = {}
        today = datetime.now(SH).date()
        if fetch_missing and missing and asof == today:
            remote_map = self._fetch_auction_remote(missing, asof)

        out = []
        for code in codes:
            feat = pg_map.get(code) or remote_map.get(code)
            if feat:
                out.append(feat)
            else:
                out.append({"ts_code": code, "amount": None, "source": "missing"})
        return out

    def _load_auction_from_pg(
        self, eng, asof: date, codes: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        if not codes:
            return {}
        try:
            df = pd.read_sql(
                sa_text(
                    """
                    SELECT ts_code, bar_time, open, high, low, close, vol, amount, freq
                    FROM stock_intraday_mins
                    WHERE trade_date = :d AND ts_code = ANY(:codes)
                    ORDER BY ts_code, bar_time
                    """
                ),
                eng,
                params={"d": asof, "codes": codes},
            )
        except Exception as e:
            logger.warning("读竞价分钟失败: %s", e)
            return {}
        if df is None or df.empty:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for code, g in df.groupby("ts_code"):
            feat = self._window_feature(str(code), g, source="pg")
            if feat and _f(feat.get("amount")) > 0:
                out[str(code).upper()] = feat
        return out

    def _fetch_auction_remote(self, codes: List[str], asof: date) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        try:
            from data_layer.collectors.datahubco.intraday import intraday_fetcher
        except Exception as e:
            logger.warning("intraday_fetcher 不可用: %s", e)
            return out

        def one(code: str) -> Optional[Dict[str, Any]]:
            try:
                df = intraday_fetcher.fetch_eastmoney_auction(code, asof)
                if df is None or df.empty:
                    return None
                try:
                    intraday_fetcher.upsert_mins(df)
                    intraday_fetcher.upsert_auction_daily(code, asof, df)
                except Exception:
                    pass
                return self._window_feature(code, df, source="eastmoney")
            except Exception as e:
                logger.debug("竞价拉取失败 %s: %s", code, e)
                return None

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(one, c): c for c in codes}
            for fut in as_completed(futs):
                feat = fut.result()
                if feat and _f(feat.get("amount")) > 0:
                    out[str(feat["ts_code"]).upper()] = feat
        return out

    def _bar_clock(self, bt) -> Optional[time]:
        if bt is None or (isinstance(bt, float) and pd.isna(bt)):
            return None
        if isinstance(bt, datetime):
            dt = bt
            if dt.tzinfo:
                dt = dt.astimezone(SH)
            return dt.timetz().replace(tzinfo=None) if False else dt.time()
        if isinstance(bt, time):
            return bt
        try:
            ts = pd.to_datetime(bt)
            if getattr(ts, "tzinfo", None):
                ts = ts.tz_convert(SH) if hasattr(ts, "tz_convert") else ts
            return ts.to_pydatetime().time()
        except Exception:
            return None

    def _window_feature(self, code: str, df: pd.DataFrame, source: str) -> Optional[Dict[str, Any]]:
        if df is None or df.empty:
            return None
        work = df.copy()
        work.columns = [str(c).lower() for c in work.columns]
        if "bar_time" not in work.columns:
            return None
        clocks = work["bar_time"].map(self._bar_clock)
        mask = clocks.map(
            lambda t: bool(t and AUCTION_START <= t <= AUCTION_END)
        )
        # 若无精确窗，但标记了 auction，也纳入 9:15-9:25 再裁剪
        if not mask.any() and "freq" in work.columns:
            mask = work["freq"].astype(str).str.lower().eq("auction")
            clocks = work["bar_time"].map(self._bar_clock)
            mask = mask & clocks.map(lambda t: bool(t and AUCTION_START <= t <= AUCTION_END))
        win = work.loc[mask].sort_values("bar_time")
        if win.empty:
            return None
        vol = float(pd.to_numeric(win.get("vol"), errors="coerce").fillna(0).sum())
        amount = float(pd.to_numeric(win.get("amount"), errors="coerce").fillna(0).sum())
        o = _f(win.iloc[0].get("open") or win.iloc[0].get("close"))
        c = _f(win.iloc[-1].get("close"))
        lift = ((c / o) - 1.0) * 100.0 if o > 0 and c > 0 else 0.0
        return {
            "ts_code": code.upper(),
            "amount": amount,
            "vol": vol,
            "open_price": round(o, 4) if o else None,
            "close_price": round(c, 4) if c else None,
            "lift_pct": lift,
            "gap_pct": None,
            "source": source,
        }

    def _prev_n_days(self, eng, ts_code: str, asof: date, n: int = 3) -> List[Dict[str, Any]]:
        with eng.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, open, high, low, close, pct_chg, amount
                    FROM stock_daily
                    WHERE ts_code = :c AND trade_date < :d
                    ORDER BY trade_date DESC
                    LIMIT :n
                    """
                ),
                {"c": ts_code, "d": asof, "n": n},
            ).fetchall()
        items = []
        for r in reversed(list(rows)):
            d = r.trade_date
            items.append(
                {
                    "trade_date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                    "open": round(_f(r.open), 4),
                    "high": round(_f(r.high), 4),
                    "low": round(_f(r.low), 4),
                    "close": round(_f(r.close), 4),
                    "pct_chg": round(_f(r.pct_chg), 3),
                    "amount": round(_f(r.amount), 2),
                }
            )
        return items

    def _percentile(self, sorted_vals: List[float], q: float) -> float:
        if not sorted_vals:
            return 0.0
        if len(sorted_vals) == 1:
            return float(sorted_vals[0])
        pos = (len(sorted_vals) - 1) * (q / 100.0)
        lo = int(pos)
        hi = min(lo + 1, len(sorted_vals) - 1)
        w = pos - lo
        return float(sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w)

    def _reason_template(
        self,
        feat: Dict[str, Any],
        meta: Dict[str, Any],
        hist: List[Dict[str, Any]],
        p95: float,
        prev: date,
    ) -> str:
        tags = "、".join(meta.get("tags") or []) or "观察池"
        amt = _f(feat.get("amount"))
        lift = _f(feat.get("lift_pct"))
        prev_pct = meta.get("prev_pct")
        hist_txt = "；".join(
            f"{h['trade_date']} 收{h['close']}({h['pct_chg']:+.2f}%)" for h in hist
        ) or "近3日日线暂缺"
        return (
            f"入选原因：来自{prev.isoformat()}的「{tags}」。"
            f"今日 09:20–09:25 竞价成交额 {amt:,.0f}，达到本轮宇宙 P95（{p95:,.0f}）及以上；"
            f"窗内价格变化 {lift:+.2f}%（价升视为买盘偏强代理）。"
            f"昨收涨跌 {prev_pct if prev_pct is not None else '—'}%。"
            f"近3日：{hist_txt}。"
            f"说明：这是规则筛选结果，不是投资建议。"
        )


auction_screen_toolkit = AuctionScreenToolkit()
