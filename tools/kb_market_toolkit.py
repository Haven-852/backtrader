"""知识库-市场记忆-今日：从 Datahubco 取全市场事实，生成草稿，不直接写入。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
_SH = ZoneInfo("Asia/Shanghai")

_INDEXES = [
    ("000001.SH", "上证"),
    ("399001.SZ", "深证"),
    ("399006.SZ", "创业板"),
    ("000688.SH", "科创50"),
    ("899050.BJ", "北证"),
]
_MAIN = ("上证", "深证", "创业板")
_ETFS = ("510300.SH", "510500.SH", "510050.SH")


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


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _f(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(n):
        return None
    return n


def _clock(v: Any) -> str:
    n = _f(v)
    if n is None:
        return ""
    s = str(int(n)).zfill(6)
    if len(s) < 4:
        return ""
    return f"{s[:2]}:{s[2:4]}"


def _qian_to_trillion(amount: Optional[float]) -> Optional[float]:
    """指数成交额单位是千元。两市 1 万亿 = 1e9 千元。"""
    if amount is None or amount <= 0:
        return None
    return round(amount / 1e9, 2)


class KbMarketToolkit:
    def build_today(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        try:
            from data_layer.collectors.datahubco.client import DatahubcoClient
            client = DatahubcoClient()
            client.timeout = 60
        except Exception as e:
            return {"ok": False, "error": f"Datahubco 未就绪: {e}"}

        requested = _parse_day(trade_date) or datetime.now(_SH).date()
        used, notes = self._resolve_trade_day(client, requested)

        indexes = self._indexes(client, used)
        if not any(row.get("change_pct") is not None for row in indexes):
            # 再兜底：若指定日 PG 无数据，回退到库内最近有指数的交易日重试一次
            fallback = self._pg_latest_index_day(before=used)
            if fallback and fallback != used:
                notes.append(
                    f"{used.isoformat()} 指数行情不足，改用库内最近交易日 {fallback.isoformat()}。"
                )
                used = fallback
                indexes = self._indexes(client, used)
        if not any(row.get("change_pct") is not None for row in indexes):
            return {
                "ok": False,
                "error": (
                    f"本地 index_daily / Datahubco 均没有 {used.isoformat()} 的指数行情。"
                    "请先用 datahubco_data__get_bars(index_daily) 确认入库。"
                ),
            }

        # 供板块连板/资金/假设的日历：含本地有指数的日期 + 开市日（不越过上海今日）
        opens = sorted(set(self._open_days(client, used)) | set(self._pg_index_days(used)))
        if used not in opens:
            opens.append(used)
            opens = sorted(set(opens))

        daily = self._daily(client, used)
        limits = self._limits(client, used)
        if limits["missing"]:
            notes.append("涨停、跌停、炸板明细这一天还没有发布，这三项留空，不用涨幅近似代替。")
        industries = self._industries(client)
        sectors = self._sectors(daily, industries, limits, client, used, opens)
        breadth = self._breadth(daily, limits)
        turnover = self._turnover(indexes)
        funds = self._funds(client, used, opens)
        vol = self._volatility(indexes)

        regime_label, confidence, evidence = self._regime(indexes, breadth, turnover)
        summary = self._summary(used, indexes, breadth, turnover, regime_label, notes)
        events = self._events(limits, sectors)
        hypo = self._hypothesis(client, used, opens, sectors, regime_label)
        prev_map = {row["name"]: row.get("prev_change_pct") for row in indexes}

        facts = {
            "temperature": breadth.get("temperature"),
            "turnover_trillion": turnover.get("amount_trillion"),
            "turnover_change_pct": turnover.get("change_pct"),
            "advancers": breadth.get("advancers"),
            "decliners": breadth.get("decliners"),
            "limit_up": breadth.get("limit_up"),
            "limit_down": breadth.get("limit_down"),
            "limit_break": breadth.get("limit_break"),
            "streak": breadth.get("streak"),
            "up_down_ratio": breadth.get("up_down_ratio"),
            "volume_ratio": turnover.get("volume_ratio"),
            "turnover_rate": turnover.get("turnover_rate"),
            "amplitude": vol.get("amplitude"),
            "volatility": vol.get("volatility"),
            "change_pct": next((r.get("change_pct") for r in indexes if r["name"] == "上证"), None),
            "index_name": "上证",
            "indices": [
                {
                    "name": r["name"],
                    "change_pct": r.get("change_pct"),
                    "prev_change_pct": r.get("prev_change_pct"),
                }
                for r in indexes
            ],
            "fact_note": summary,
            "source": "datahubco",
        }
        structure = {
            "indices": facts["indices"],
            "breadth": {
                "advancers": breadth.get("advancers"),
                "decliners": breadth.get("decliners"),
                "limit_up": breadth.get("limit_up"),
                "limit_down": breadth.get("limit_down"),
                "limit_break": breadth.get("limit_break"),
                "streak": breadth.get("streak"),
            },
            "turnover": turnover,
            "funds": funds,
            "volatility": vol,
            "sectors": sectors,
            "note": "指数、涨跌家数、成交额来自 Datahubco。热点涨跌幅按行业内个股当日涨跌幅平均。涨停时间来自涨停明细，不是新闻。",
        }
        draft = {
            "trade_date": used.isoformat(),
            "facts": facts,
            "regime": {"label": regime_label, "confidence": confidence, "evidence": evidence},
            "structure": structure,
            "events": events,
            "summary": {"text": summary, "evidence": evidence, "confidence": confidence},
            "hypotheses": hypo,
            "narrative": " ".join(notes),
        }
        return {
            "ok": True,
            "tool": "知识库-市场记忆-今日",
            "saved": False,
            "source": "datahubco",
            "trade_date": used.isoformat(),
            "prev_indexes": prev_map,
            "memory_draft": draft,
            "preview": self._preview(draft),
        }

    def _open_days(self, client, day: date) -> List[date]:
        """交易日历开市日。只保留 <= 上海今日，避免远端日历把未来交易日当成 used。"""
        today = datetime.now(_SH).date()
        start = day - timedelta(days=40)
        end = min(day + timedelta(days=20), today)
        try:
            df = client.query(
                "trade_cal",
                exchange="SSE",
                start_date=_ymd(start),
                end_date=_ymd(end),
            )
        except Exception as e:
            logger.warning("trade_cal 失败: %s", e)
            return []
        if df is None or df.empty:
            return []
        df.columns = [str(c).lower() for c in df.columns]
        out = []
        for _, row in df.iterrows():
            flag = str(row.get("is_open"))
            if flag not in ("1", "1.0", "true", "True"):
                continue
            d = _parse_day(str(row.get("cal_date")))
            if d and d <= today:
                out.append(d)
        return sorted(set(out))

    def _pg_index_days(self, around: date, lookback: int = 40) -> List[date]:
        """以本地 index_daily 为准：列出有行情的交易日。"""
        try:
            from sqlalchemy import text as sa_text
            from data_layer.db_manager import storage_manager

            eng = storage_manager.get_postgres_engine()
            if not eng:
                return []
            codes = [c for c, _ in _INDEXES]
            with eng.connect() as conn:
                rows = conn.execute(
                    sa_text(
                        """
                        SELECT DISTINCT trade_date
                        FROM index_daily
                        WHERE ts_code = ANY(:codes)
                          AND trade_date >= :s
                          AND trade_date <= :e
                        ORDER BY trade_date
                        """
                    ),
                    {
                        "codes": codes,
                        "s": around - timedelta(days=lookback),
                        "e": around + timedelta(days=2),
                    },
                ).fetchall()
            out = []
            for r in rows:
                d = r[0]
                if isinstance(d, datetime):
                    d = d.date()
                elif not isinstance(d, date):
                    d = _parse_day(str(d))
                if d:
                    out.append(d)
            return out
        except Exception as e:
            logger.warning("pg index_daily days 失败: %s", e)
            return []

    def _pg_latest_index_day(self, before: Optional[date] = None) -> Optional[date]:
        try:
            from sqlalchemy import text as sa_text
            from data_layer.db_manager import storage_manager

            eng = storage_manager.get_postgres_engine()
            if not eng:
                return None
            codes = [c for c, _ in _INDEXES]
            sql = """
                SELECT MAX(trade_date) AS d
                FROM index_daily
                WHERE ts_code = ANY(:codes)
            """
            params: Dict[str, Any] = {"codes": codes}
            if before is not None:
                sql += " AND trade_date <= :e"
                params["e"] = before
            with eng.connect() as conn:
                row = conn.execute(sa_text(sql), params).fetchone()
            if not row or row[0] is None:
                return None
            d = row[0]
            if isinstance(d, datetime):
                return d.date()
            if isinstance(d, date):
                return d
            return _parse_day(str(d))
        except Exception as e:
            logger.warning("pg latest index day 失败: %s", e)
            return None

    def _resolve_trade_day(self, client, requested: date) -> tuple:
        """
        解析实际使用的交易日：
        1) 本地 index_daily 已有该日 → 直接用（与 get_bars 口径一致）
        2) 否则取 <= requested 的最近开市日 / 最近有指数日
        禁止回退到未来日历日。
        """
        notes: List[str] = []
        today = datetime.now(_SH).date()
        req = min(requested, today)
        pg_days = self._pg_index_days(req)
        if req in pg_days:
            return req, notes

        opens = self._open_days(client, req)
        past_opens = [d for d in opens if d <= req]
        past_pg = [d for d in pg_days if d <= req]

        candidates = sorted(set(past_opens) | set(past_pg))
        if req in opens and not past_pg:
            # 日历说开市但库里还没有：仍先用 req，让 _indexes 再尝试远端/回退
            return req, notes
        if candidates:
            used = candidates[-1]
            if used != requested:
                notes.append(
                    f"{requested.isoformat()} 不是可用交易日或库内无指数，"
                    f"草稿用最近交易日 {used.isoformat()}。"
                )
            return used, notes

        latest = self._pg_latest_index_day(before=req)
        if latest:
            if latest != requested:
                notes.append(
                    f"交易日历不可用，改用本地 index_daily 最近日 {latest.isoformat()}。"
                )
            return latest, notes
        return req, notes

    def _query(self, client, api: str, **params):
        try:
            df = client.query(api, **params)
        except Exception as e:
            logger.info("%s 不可用: %s", api, e)
            return pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        return df

    def _index_frame(self, client, code: str, day: date) -> pd.DataFrame:
        """
        指数日线：优先本地 PG index_daily（与 datahubco_data__get_bars 同口径），
        不足再回退 Datahubco 远端。
        """
        start_d = day - timedelta(days=30)
        # 1) PG
        try:
            from sqlalchemy import text as sa_text
            from data_layer.db_manager import storage_manager

            eng = storage_manager.get_postgres_engine()
            if eng:
                df = pd.read_sql(
                    sa_text(
                        """
                        SELECT *
                        FROM index_daily
                        WHERE ts_code = :c
                          AND trade_date >= :s
                          AND trade_date <= :e
                        ORDER BY trade_date
                        """
                    ),
                    eng,
                    params={"c": code, "s": start_d, "e": day},
                )
                if df is not None and not df.empty:
                    df = df.copy()
                    df.columns = [str(c).lower() for c in df.columns]
                    if "trade_date" in df.columns:
                        df["trade_date"] = df["trade_date"].map(
                            lambda v: v.date() if isinstance(v, datetime) else (
                                v if isinstance(v, date) else _parse_day(str(v))
                            )
                        )
                        if (df["trade_date"] == day).any():
                            return df
        except Exception as e:
            logger.info("index_daily PG 读取失败 %s: %s", code, e)

        # 2) toolkit get_bars(auto) —— 与覆盖率/get_bars 工具一致
        try:
            from tools.datahubco_toolkit import datahubco_toolkit

            packed = datahubco_toolkit.get_bars(
                ts_code=code,
                dataset="index_daily",
                start_date=start_d.isoformat(),
                end_date=day.isoformat(),
                limit=80,
                source="auto",
                write_back=True,
            )
            bars = packed.get("data") or packed.get("bars") or packed.get("items") or []
            if bars:
                df = pd.DataFrame(bars)
                df.columns = [str(c).lower() for c in df.columns]
                if "trade_date" in df.columns:
                    df["trade_date"] = df["trade_date"].map(lambda v: _parse_day(str(v)))
                return df
        except Exception as e:
            logger.info("index_daily get_bars 失败 %s: %s", code, e)

        # 3) 远端直查
        df = self._query(
            client,
            "index_daily",
            ts_code=code,
            start_date=_ymd(start_d),
            end_date=_ymd(day),
        )
        if not df.empty and "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].map(lambda v: _parse_day(str(v)))
        return df

    def _indexes(self, client, day: date) -> List[Dict[str, Any]]:
        rows = []
        for code, name in _INDEXES:
            df = self._index_frame(client, code, day)
            if df.empty or "trade_date" not in df.columns:
                rows.append({"name": name, "ts_code": code})
                continue
            df = df.dropna(subset=["trade_date"]).sort_values("trade_date")
            today = df[df["trade_date"] == day]
            prev = df[df["trade_date"] < day]
            cur = today.iloc[-1] if len(today) else None
            pre = prev.iloc[-1] if len(prev) else None
            amount = _f(cur.get("amount")) if cur is not None else None
            vol = _f(cur.get("vol")) if cur is not None else None
            pct = _f(cur.get("pct_chg")) if cur is not None else None
            prev_pct = _f(pre.get("pct_chg")) if pre is not None else None
            prev_vols = []
            if "vol" in prev.columns:
                prev_vols = [_f(v) for v in prev["vol"].tail(5).tolist()]
                prev_vols = [v for v in prev_vols if v]
            amplitude = None
            if cur is not None and _f(cur.get("pre_close")):
                pre_close = _f(cur.get("pre_close"))
                high = _f(cur.get("high"))
                low = _f(cur.get("low"))
                if pre_close and high is not None and low is not None:
                    amplitude = round((high - low) / pre_close * 100, 2)
            rows.append({
                "name": name,
                "ts_code": code,
                "change_pct": None if pct is None else round(pct, 2),
                "prev_change_pct": None if prev_pct is None else round(prev_pct, 2),
                "amount_qian": amount,
                "prev_amount_qian": None if pre is None else _f(pre.get("amount")),
                "vol": vol,
                "vol_ma5": round(sum(prev_vols) / len(prev_vols), 2) if prev_vols else None,
                "amplitude": amplitude,
            })
        return rows

    def _daily(self, client, day: date) -> pd.DataFrame:
        frames = []
        offset = 0
        page = 2000
        ymd = _ymd(day)
        while offset < 12000:
            df = self._query(client, "daily", trade_date=ymd, limit=page, offset=offset)
            if df.empty:
                break
            keep = [c for c in ("ts_code", "pct_chg", "amount", "vol") if c in df.columns]
            frames.append(df[keep])
            if len(df) < page:
                break
            offset += page
        if not frames:
            return pd.DataFrame(columns=["ts_code", "pct_chg"])
        out = pd.concat(frames, ignore_index=True)
        out = out.drop_duplicates(subset=["ts_code"])
        out["pct_chg"] = pd.to_numeric(out["pct_chg"], errors="coerce")
        return out

    def _limits(self, client, day: date) -> Dict[str, Any]:
        ymd = _ymd(day)
        frames = []
        missing = True
        for kind in ("U", "D", "Z"):
            df = self._query(client, "limit_list_d", trade_date=ymd, limit_type=kind)
            if df.empty:
                continue
            missing = False
            df["limit_type"] = kind
            frames.append(df)
        if not frames:
            return {"missing": True, "up": pd.DataFrame(), "down": pd.DataFrame(), "broken": pd.DataFrame(), "all": pd.DataFrame()}
        all_df = pd.concat(frames, ignore_index=True)
        return {
            "missing": missing,
            "up": all_df[all_df["limit_type"] == "U"],
            "down": all_df[all_df["limit_type"] == "D"],
            "broken": all_df[all_df["limit_type"] == "Z"],
            "all": all_df,
        }

    def _industries(self, client) -> Dict[str, str]:
        df = self._query(client, "stock_basic", list_status="L")
        if df.empty or "ts_code" not in df.columns or "industry" not in df.columns:
            return {}
        return {
            str(r.ts_code): str(r.industry)
            for r in df.itertuples(index=False)
            if getattr(r, "industry", None) and str(r.industry) not in ("None", "nan")
        }

    def _breadth(self, daily: pd.DataFrame, limits: Dict[str, Any]) -> Dict[str, Any]:
        up = down = None
        if not daily.empty and "pct_chg" in daily.columns:
            pct = daily["pct_chg"].dropna()
            up = int((pct > 0).sum())
            down = int((pct < 0).sum())
        ratio = round(up / down, 2) if up is not None and down else None
        temperature = round(up / (up + down) * 100) if up is not None and down is not None and up + down else None
        limit_up = limit_down = limit_break = streak = None
        if not limits.get("missing"):
            limit_up = int(len(limits["up"]))
            limit_down = int(len(limits["down"]))
            limit_break = int(len(limits["broken"]))
            if not limits["up"].empty and "limit_times" in limits["up"].columns:
                times = pd.to_numeric(limits["up"]["limit_times"], errors="coerce")
                if times.notna().any():
                    streak = int(times.max())
        return {
            "advancers": up,
            "decliners": down,
            "up_down_ratio": ratio,
            "temperature": temperature,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "limit_break": limit_break,
            "streak": streak,
            "sample": 0 if daily.empty else int(len(daily)),
        }

    def _turnover(self, indexes: List[Dict[str, Any]]) -> Dict[str, Any]:
        sh = next((r for r in indexes if r["name"] == "上证"), {})
        sz = next((r for r in indexes if r["name"] == "深证"), {})
        amount = None
        if sh.get("amount_qian") and sz.get("amount_qian"):
            amount = _qian_to_trillion(sh["amount_qian"] + sz["amount_qian"])
        # 前一日成交额用指数历史里的上一根，已在 amount 之外单独再取不到，这里用 vol 比
        prev_amount = None
        if sh.get("prev_amount_qian") and sz.get("prev_amount_qian"):
            prev_amount = _qian_to_trillion(sh["prev_amount_qian"] + sz["prev_amount_qian"])
        change_pct = None
        if amount is not None and prev_amount:
            change_pct = round((amount - prev_amount) / prev_amount * 100, 2)
        vol_ratio = None
        vols = [r.get("vol") for r in (sh, sz) if r.get("vol")]
        mas = [r.get("vol_ma5") for r in (sh, sz) if r.get("vol_ma5")]
        if vols and mas and sum(mas):
            vol_ratio = round(sum(vols) / sum(mas), 2)
        return {
            "amount_trillion": amount,
            "change_pct": change_pct,
            "volume_ratio": vol_ratio,
            "turnover_rate": None,
            "sh_amount_qian": sh.get("amount_qian"),
            "sz_amount_qian": sz.get("amount_qian"),
        }

    def _funds(self, client, day: date, opens: List[date]) -> Dict[str, Any]:
        etf_today = 0.0
        etf_prev = 0.0
        etf_names = 0
        start = _ymd(day - timedelta(days=10))
        for code in _ETFS:
            df = self._query(client, "fund_daily", ts_code=code, start_date=start, end_date=_ymd(day))
            if df.empty or "amount" not in df.columns:
                continue
            df["trade_date"] = df["trade_date"].map(lambda v: _parse_day(str(v)))
            df = df.dropna(subset=["trade_date"]).sort_values("trade_date")
            cur = df[df["trade_date"] == day]
            prev = df[df["trade_date"] < day]
            if len(cur) and _f(cur.iloc[-1]["amount"]):
                etf_today += _f(cur.iloc[-1]["amount"]) or 0
                etf_names += 1
            if len(prev) and _f(prev.iloc[-1]["amount"]):
                etf_prev += _f(prev.iloc[-1]["amount"]) or 0
        etf = None
        if etf_names and etf_prev:
            etf = {
                "names": "沪深300+中证500+上证50",
                "amount_trillion": _qian_to_trillion(etf_today),
                "change_pct": round((etf_today - etf_prev) / etf_prev * 100, 2),
            }
        margin = self._margin(client, day, opens)
        return {
            "main": None,
            "main_note": "Datahubco 没有全市场主力净额接口，这项留空。",
            "etf": etf,
            "north": None,
            "north_note": "北向资金接口没有返回当天数据。",
            "margin": margin,
        }

    def _margin(self, client, day: date, opens: List[date]) -> Optional[Dict[str, Any]]:
        candidates = [d for d in opens if d <= day]
        for d in reversed(candidates[-6:]):
            parts = []
            for ex in ("SSE", "SZSE"):
                df = self._query(client, "margin", trade_date=_ymd(d), exchange_id=ex)
                if not df.empty:
                    parts.append(df.iloc[0])
            if not parts:
                continue
            rzmre = sum(_f(p.get("rzmre")) or 0 for p in parts)
            rzrqye = sum(_f(p.get("rzrqye")) or 0 for p in parts)
            return {
                "trade_date": d.isoformat(),
                "rzmre": round(rzmre, 2),
                "rzrqye": round(rzrqye, 2),
                "lag": d != day,
            }
        return None

    def _volatility(self, indexes: List[Dict[str, Any]]) -> Dict[str, Any]:
        sh = next((r for r in indexes if r["name"] == "上证"), {})
        main = [r.get("change_pct") for r in indexes if r["name"] in _MAIN and r.get("change_pct") is not None]
        swing = None
        if len(main) >= 2:
            swing = round(max(main) - min(main), 2)
        return {
            "volatility": sh.get("amplitude"),
            "amplitude": sh.get("amplitude"),
            "index_swing": swing,
        }

    def _sectors(self, daily, industries, limits, client, day, opens) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[float]] = {}
        if not daily.empty and industries:
            for row in daily.itertuples(index=False):
                code = str(getattr(row, "ts_code", ""))
                industry = industries.get(code)
                pct = _f(getattr(row, "pct_chg", None))
                if not industry or pct is None:
                    continue
                grouped.setdefault(industry, []).append(pct)
        rows = []
        for name, pcts in grouped.items():
            if len(pcts) < 8:
                continue
            avg = sum(pcts) / len(pcts)
            up_ratio = sum(1 for p in pcts if p > 0) / len(pcts)
            rows.append({"name": name, "change_pct": round(avg, 2), "up_ratio": round(up_ratio, 2), "n": len(pcts)})
        rows.sort(key=lambda r: r["change_pct"], reverse=True)
        rows = rows[:5]
        streaks = self._hot_streaks(client, day, opens, [r["name"] for r in rows])
        limit_up = limits.get("up") if not limits.get("missing") else pd.DataFrame()
        for row in rows:
            limit_n = 0
            leader_gap = None
            if limit_up is not None and not limit_up.empty and "industry" in limit_up.columns:
                hit = limit_up[limit_up["industry"].astype(str) == row["name"]]
                limit_n = int(len(hit))
            pcts = grouped.get(row["name"]) or []
            if pcts:
                leader_gap = max(pcts) - (sum(pcts) / len(pcts))
            row["strength"] = self._strength(row["change_pct"], row["up_ratio"])
            row["streak_days"] = streaks.get(row["name"])
            row["concentration"] = "高" if leader_gap is not None and leader_gap >= 3 else "中"
            row["phase"] = self._phase(row["change_pct"], row["up_ratio"], row.get("streak_days") or 0, limit_n)
            row["limit_up"] = limit_n
        return rows

    def _strength(self, pct: float, up_ratio: Optional[float]) -> int:
        score = 50 + pct * 8
        if up_ratio is not None:
            score += (up_ratio - 0.5) * 20
        return int(max(0, min(100, round(score))))

    def _phase(self, pct: float, up_ratio: Optional[float], streak: int, limit_n: int) -> str:
        ratio = up_ratio if up_ratio is not None else 0.5
        if pct <= -1 or ratio < 0.35:
            return "退潮"
        if pct >= 3 and ratio >= 0.7:
            return "高潮"
        if streak >= 2 and pct >= 1:
            return "发酵"
        if pct >= 0.8 and ratio >= 0.55:
            return "启动"
        if abs(pct) < 1:
            return "分歧"
        return "启动" if pct > 0 else "退潮"

    def _hot_streaks(self, client, day: date, opens: List[date], names: List[str]) -> Dict[str, int]:
        if not names:
            return {}
        past = [d for d in opens if d <= day][-5:]
        presence = {name: [] for name in names}
        for d in past:
            df = self._query(client, "limit_list_d", trade_date=_ymd(d), limit_type="U")
            counts: Dict[str, int] = {}
            if not df.empty and "industry" in df.columns:
                for industry, n in df["industry"].astype(str).value_counts().items():
                    counts[industry] = int(n)
            for name in names:
                presence[name].append(counts.get(name, 0) >= 2)
        streaks = {}
        for name, flags in presence.items():
            n = 0
            for flag in reversed(flags):
                if not flag:
                    break
                n += 1
            streaks[name] = n
        return streaks

    def _events(self, limits: Dict[str, Any], sectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        up = limits.get("up")
        if up is None or up.empty or "first_time" not in up.columns:
            return []
        hot = {s["name"] for s in sectors}
        df = up.copy()
        df["clock"] = df["first_time"].map(_clock)
        if hot and "industry" in df.columns:
            prefer = df[df["industry"].astype(str).isin(hot)]
            if not prefer.empty:
                df = prefer
        df = df[df["clock"] != ""].sort_values("first_time").head(6)
        events = []
        for row in df.itertuples(index=False):
            industry = str(getattr(row, "industry", "") or "板块")
            name = str(getattr(row, "name", "") or "")
            events.append({
                "time": row.clock,
                "title": f"{industry} {name} 涨停".strip(),
                "detail": "来自涨停明细的首次封板时间",
            })
        return events

    def _regime(self, indexes, breadth, turnover):
        main = [r for r in indexes if r["name"] in _MAIN and r.get("change_pct") is not None]
        today = [r["change_pct"] for r in main]
        prev = [r["prev_change_pct"] for r in main if r.get("prev_change_pct") is not None]
        avg = sum(today) / len(today) if today else 0
        prev_avg = sum(prev) / len(prev) if prev else None
        up = breadth.get("advancers")
        down = breadth.get("decliners")
        evidence = []
        for r in main:
            prev_txt = "无前值" if r.get("prev_change_pct") is None else f"前一日 {r['prev_change_pct']:+.2f}%"
            evidence.append(f"{r['name']} 今日 {r['change_pct']:+.2f}% ，{prev_txt}")
        if up is not None and down is not None:
            evidence.append(f"上涨 {up} 家，下跌 {down} 家")
        if turnover.get("amount_trillion") is not None:
            evidence.append(f"两市成交额 {turnover['amount_trillion']} 万亿")
        if breadth.get("limit_up") is not None:
            evidence.append(f"涨停 {breadth['limit_up']}，跌停 {breadth['limit_down']}，炸板 {breadth['limit_break']}，连板高度 {breadth.get('streak')}")
        label = "震荡"
        if prev_avg is not None and prev_avg <= -0.8 and avg > prev_avg and avg >= -0.5:
            label = "修复"
        elif avg >= 1 and (up is None or (down is not None and up >= down)):
            label = "牛"
        elif avg <= -1 and (down is None or (up is not None and down > up)):
            label = "熊"
        confidence = 0.7 if up is not None and turnover.get("amount_trillion") is not None else 0.55
        return label, confidence, evidence

    def _summary(self, day, indexes, breadth, turnover, label, notes) -> str:
        bits = [f"{day.isoformat()} 市场状态判断为「{label}」。"]
        for r in indexes:
            if r["name"] not in _MAIN or r.get("change_pct") is None:
                continue
            prev = r.get("prev_change_pct")
            if prev is None:
                bits.append(f"{r['name']}当日 {r['change_pct']:+.2f}%。")
                continue
            if prev < 0 <= r["change_pct"]:
                move = "由跌转涨"
            elif prev > 0 >= r["change_pct"]:
                move = "由涨转跌"
            elif r["change_pct"] > prev:
                move = "跌幅收敛或涨幅扩大"
            elif r["change_pct"] < prev:
                move = "涨幅缩小或跌幅扩大"
            else:
                move = "与前一日持平"
            bits.append(f"{r['name']}当日 {r['change_pct']:+.2f}%，前一交易日 {prev:+.2f}%，{move}。")
        amount = turnover.get("amount_trillion")
        if amount is not None:
            bits.append(f"沪深两市成交额 {amount} 万亿。")
            chg = turnover.get("change_pct")
            if chg is not None:
                bits.append(f"成交额较前一交易日 {chg:+.2f}%。")
        else:
            bits.append("两市成交额缺少上证或深证成交额，这项留空。")
        if breadth.get("advancers") is not None:
            bits.append(f"上涨 {breadth['advancers']} 家，下跌 {breadth['decliners']} 家。")
        if breadth.get("limit_up") is None:
            bits.append("涨停明细未发布。")
        bits.extend(notes)
        bits.append("状态只在牛、熊、震荡、修复里取一个。这是判断，不是事实。")
        return "".join(bits)

    def _hypothesis(self, client, day, opens, sectors, label) -> List[Dict[str, Any]]:
        future = [d for d in opens if d > day][:3]
        verify = future[-1].isoformat() if future else (day + timedelta(days=5)).isoformat()
        if not sectors:
            statement = f"未来 3 个交易日，市场维持「{label}」这一状态。"
            basis = [f"当日状态判断为{label}"]
        else:
            top = sectors[0]
            statement = f"未来 3 个交易日，{top['name']} 相对上证的强弱，会沿当日「{top['phase']}」这个阶段延续或转向。"
            basis = [
                f"{top['name']} 当日行业平均涨跌 {top['change_pct']}%",
                f"今日强度 {top['strength']}",
                f"生命周期判断为{top['phase']}",
            ]
        return [{
            "statement": statement,
            "basis": basis,
            "conditions": [
                "上证、深证、创业板没有同步转成相反方向",
                "该判断对应的板块或市场状态，在验证日仍能用同样的涨跌家数和指数复核",
            ],
            "verify_on": verify,
        }]

    def _preview(self, draft: Dict[str, Any]) -> str:
        facts = draft["facts"]
        indexes = "  ".join(
            f"{r['name']} {r['change_pct']:+.2f}%"
            for r in facts.get("indices") or []
            if r.get("change_pct") is not None and r["name"] in _MAIN
        )
        hot = "、".join(
            f"{s['name']} {s.get('change_pct')}% 强度{s.get('strength')} {s.get('phase')}"
            for s in (draft["structure"].get("sectors") or [])[:3]
        ) or "无"
        return "\n".join([
            f"交易日 {draft['trade_date']} · 来源 Datahubco",
            f"状态：{draft['regime']['label']}",
            f"指数：{indexes}",
            f"成交额：{facts.get('turnover_trillion') or '—'} 万亿",
            f"上涨 {facts.get('advancers')} / 下跌 {facts.get('decliners')}",
            f"涨停 {facts.get('limit_up')} / 跌停 {facts.get('limit_down')} / 炸板 {facts.get('limit_break')} / 连板 {facts.get('streak')}",
            f"热点：{hot}",
            draft["summary"]["text"],
            "还没有写入知识库。确认后点「填入市场记忆」。",
        ])


kb_market_toolkit = KbMarketToolkit()
