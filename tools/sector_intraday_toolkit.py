"""
东财二级行业分时情绪

策略：先日线排序（快）→ 仅对 Top 板块做成分分时等权合成（快）
近 3 日：sector_dc_index_daily
今日分时：成分股 mins 合成相对昨收涨跌曲线（降采样）
说明：纯规则模板
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")
LEVEL = "东财二级行业"
MAX_WORKERS = 6


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


def _hhmm(bt) -> Optional[str]:
    if bt is None:
        return None
    if isinstance(bt, datetime):
        dt = bt.astimezone(SH) if bt.tzinfo else bt
        return f"{dt.hour:02d}:{dt.minute:02d}"
    try:
        ts = pd.to_datetime(bt)
        if getattr(ts, "tzinfo", None):
            ts = ts.tz_convert(SH)
        dt = ts.to_pydatetime()
        return f"{dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return None


class SectorIntradayToolkit:
    def l2_intraday_tone(
        self,
        trade_date: Optional[str] = None,
        top_n: int = 10,
        member_limit: int = 15,
        sample_every_min: int = 5,
    ) -> Dict[str, Any]:
        try:
            from data_layer.db_manager import storage_manager

            eng = storage_manager.get_postgres_engine()
            if eng is None:
                return {"ok": False, "error": "PostgreSQL 不可用"}
        except Exception as e:
            return {"ok": False, "error": f"数据库未就绪: {e}"}

        asof = _parse_day(trade_date) or self._session_trade_date()
        bootstrap_notes: List[str] = []
        day_rows = self._load_l2_daily(eng, asof)
        if not day_rows:
            # 缺数：Datahubco 补当日二级行业日线
            boot = self._ensure_sector_daily(asof)
            bootstrap_notes.extend(boot)
            day_rows = self._load_l2_daily(eng, asof)
        if not day_rows:
            latest = self._latest_l2_day(eng, asof)
            if latest and latest != asof:
                bootstrap_notes.append(f"改用最近有数据日 {latest.isoformat()}")
                asof = latest
                day_rows = self._load_l2_daily(eng, asof)
        if not day_rows:
            return {
                "ok": False,
                "error": (
                    f"无 {LEVEL} 日线。补数尝试：{'；'.join(bootstrap_notes) or '无'}。"
                    "请调用 datahubco_data__fill_sector_gap。"
                ),
                "asof": asof.isoformat(),
                "bootstrap": bootstrap_notes,
            }
        # 近3日也尽量补齐，便于 prev_3d
        for d in self._recent_trade_days(eng, asof, n=3):
            if d == asof:
                continue
            if not self._load_l2_daily(eng, d):
                bootstrap_notes.extend(self._ensure_sector_daily(d))

        # 按涨跌幅绝对值排序，兼顾领涨/领跌
        ranked = sorted(day_rows, key=lambda r: abs(_f(r.get("pct_change"))), reverse=True)
        top_n = max(1, min(int(top_n or 10), 30))
        member_limit = max(5, min(int(member_limit or 15), 40))
        sample_every_min = max(1, min(int(sample_every_min or 5), 30))
        top = ranked[:top_n]

        sectors_out: List[Dict[str, Any]] = []
        missing_members: List[str] = []
        missing_mins: List[str] = []
        empty_intraday_sectors: List[str] = []

        for row in top:
            code = str(row["ts_code"])
            hist3 = self._sector_prev_n(eng, code, asof, n=3)
            members = self._sector_members(eng, code, asof, limit=member_limit)
            if not members:
                mnote = self._ensure_sector_members(code, asof)
                if mnote:
                    bootstrap_notes.append(mnote)
                members = self._sector_members(eng, code, asof, limit=member_limit)
            if not members:
                missing_members.append(code)
            curve, curve_meta = self._synthesize_intraday(
                members, asof, sample_every_min=sample_every_min
            )
            used = int(curve_meta.get("member_used") or 0)
            if members and used == 0:
                empty_intraday_sectors.append(code)
                missing_mins.extend(members[:member_limit])
            elif members and used < max(3, len(members) // 3):
                # 部分缺失：记录未成功合成的成分（简化：整表再补）
                missing_mins.extend(members[:member_limit])
            last_pct = curve[-1]["pct"] if curve else _f(row.get("pct_change"))
            shape = self._shape_label(curve)
            tone = self._sector_tone(_f(row.get("pct_change")), shape, hist3)
            sectors_out.append(
                {
                    "ts_code": code,
                    "name": row.get("name") or code,
                    "pct_change_daily": round(_f(row.get("pct_change")), 3),
                    "up_num": row.get("up_num"),
                    "down_num": row.get("down_num"),
                    "leading_name": row.get("leading_name"),
                    "prev_3d": hist3,
                    "intraday_last_pct": round(last_pct, 3) if last_pct is not None else None,
                    "intraday_shape": shape,
                    "intraday_points": curve,
                    "member_used": used,
                    "member_total": len(members),
                    "intraday_source": curve_meta.get("source"),
                    "reason": tone,
                }
            )

        # 去重并限制补数列表长度
        missing_mins = list(dict.fromkeys(missing_mins))[:30]
        missing_members = list(dict.fromkeys(missing_members))
        incomplete = bool(missing_mins or missing_members or empty_intraday_sectors)
        gaps_payload = self._sector_data_gaps(
            asof=asof,
            missing_members=missing_members,
            missing_mins=missing_mins,
            empty_intraday_sectors=empty_intraday_sectors,
            incomplete=incomplete,
        )

        market = self._market_tone(day_rows, sectors_out)
        out = {
            "ok": True,
            "incomplete": incomplete,
            "tool": "二级行业分时情绪",
            "asof": asof.isoformat(),
            "level": LEVEL,
            "universe_count": len(day_rows),
            "top_n": top_n,
            "market_tone": market,
            "sectors": sectors_out,
            "bootstrap": bootstrap_notes,
            "note": (
                "先日线按|涨跌幅|排序取 Top，再对 Top 板块用成分股分时等权合成；"
                "近3日来自 sector_dc_index_daily。规则描述，非投资建议。"
            ),
        }
        if incomplete:
            out.update(gaps_payload)
            out["message"] = (
                "日线可用，但 Top 板块分时合成数据不足。"
                "请先按 suggested_tool_calls 用 Datahubco 补成分/分时，再重跑本工具。"
            )
        return out

    def _sector_data_gaps(
        self,
        *,
        asof: date,
        missing_members: List[str],
        missing_mins: List[str],
        empty_intraday_sectors: List[str],
        incomplete: bool,
    ) -> Dict[str, Any]:
        if not incomplete:
            return {
                "data_gaps": [],
                "suggested_tool_calls": [],
                "agent_instruction": "",
            }
        ymd = asof.isoformat()
        gaps: List[Dict[str, Any]] = []
        calls: List[Dict[str, Any]] = [
            {
                "tool": "datahubco_data__fill_sector_gap",
                "arguments": {"trade_date": ymd, "level": LEVEL},
                "why": "确保二级行业日线完整",
            }
        ]
        if missing_members or empty_intraday_sectors:
            gaps.append(
                {
                    "kind": "sector_members",
                    "table": "sector_dc_member",
                    "sector_codes": (missing_members or empty_intraday_sectors)[:20],
                    "message": "Top 板块缺少成分股，需 get_sector_members",
                }
            )
            for sc in (missing_members or empty_intraday_sectors)[:8]:
                calls.append(
                    {
                        "tool": "datahubco_data__get_sector_members",
                        "arguments": {
                            "ts_code": sc,
                            "trade_date": ymd,
                            "write_back": True,
                            "source": "auto",
                        },
                        "why": f"补板块 {sc} 成分",
                    }
                )
        if missing_mins:
            gaps.append(
                {
                    "kind": "stock_mins",
                    "table": "stock_intraday_mins / Redis",
                    "missing_count": len(missing_mins),
                    "ts_codes": missing_mins[:40],
                    "message": (
                        f"{ymd} Top 板块成分分时缺失 {len(missing_mins)} 只，"
                        "需 datahubco_data__get_mins 写入后才能合成分时曲线。"
                    ),
                }
            )
            for code in missing_mins[:15]:
                calls.append(
                    {
                        "tool": "datahubco_data__get_mins",
                        "arguments": {
                            "ts_code": code,
                            "trade_date": ymd,
                            "include_auction": False,
                            "source": "auto",
                            "write_back": True,
                            "limit": 500,
                        },
                        "why": f"补 {code} 当日1分钟分时",
                    }
                )
        return {
            "data_gaps": gaps,
            "suggested_tool_calls": calls,
            "agent_instruction": (
                "分时数据不完整。请立即按 suggested_tool_calls 调用 Datahubco 补数；"
                "完成后重新调用 sector_intraday__l2_tone（同一 trade_date）。"
                "在分时补齐前，可以简述日线强弱，但必须标明「分时未就绪」，"
                "不要把日线涨跌说成分时形态。"
            ),
        }

    def _ensure_sector_daily(self, asof: date) -> List[str]:
        notes: List[str] = []
        try:
            from tools.datahubco_toolkit import datahubco_toolkit

            res = datahubco_toolkit.fill_sector_gap(
                trade_date=asof.isoformat(),
                level=LEVEL,
            )
            notes.append(
                f"fill_sector_gap {asof}: ok={res.get('ok')} "
                f"upserted={res.get('upserted') or res.get('count') or 0} "
                f"msg={str(res.get('message') or res.get('error') or '')[:120]}"
            )
        except Exception as e:
            notes.append(f"fill_sector_gap 失败: {e}")
        return notes

    def _ensure_sector_members(self, sector_code: str, asof: date) -> str:
        try:
            from tools.datahubco_toolkit import datahubco_toolkit

            res = datahubco_toolkit.get_sector_members(
                ts_code=sector_code,
                trade_date=asof.isoformat(),
                write_back=True,
            )
            return (
                f"get_sector_members {sector_code}: ok={res.get('ok')} "
                f"count={res.get('count') or 0}"
            )
        except Exception as e:
            return f"get_sector_members {sector_code} 失败: {e}"

    def _recent_trade_days(self, eng, asof: date, n: int = 3) -> List[date]:
        with eng.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT DISTINCT trade_date FROM sector_dc_index_daily
                    WHERE trade_date <= :d
                    ORDER BY trade_date DESC
                    LIMIT :n
                    """
                ),
                {"d": asof, "n": n},
            ).fetchall()
        out = []
        for r in rows:
            d = r[0]
            out.append(d.date() if isinstance(d, datetime) else d)
        if not out:
            out = [asof - timedelta(days=i) for i in range(n)]
        return out

    def _session_trade_date(self) -> date:
        try:
            from data_layer.collectors.datahubco.intraday import intraday_fetcher

            return intraday_fetcher.session_phase().get("trade_date") or datetime.now(SH).date()
        except Exception:
            return datetime.now(SH).date()

    def _latest_l2_day(self, eng, asof: date) -> Optional[date]:
        with eng.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT MAX(trade_date) FROM sector_dc_index_daily
                    WHERE industry_level = :lv AND trade_date <= :d
                    """
                ),
                {"lv": LEVEL, "d": asof},
            ).fetchone()
        if row and row[0]:
            d = row[0]
            return d.date() if isinstance(d, datetime) else d
        return None

    def _load_l2_daily(self, eng, asof: date) -> List[Dict[str, Any]]:
        with eng.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT ts_code, name, pct_change, up_num, down_num,
                           leading_name, leading_code, turnover_rate
                    FROM sector_dc_index_daily
                    WHERE trade_date = :d AND industry_level = :lv
                    """
                ),
                {"d": asof, "lv": LEVEL},
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "ts_code": str(r.ts_code),
                    "name": r.name,
                    "pct_change": _f(r.pct_change),
                    "up_num": int(r.up_num) if r.up_num is not None else None,
                    "down_num": int(r.down_num) if r.down_num is not None else None,
                    "leading_name": r.leading_name,
                    "leading_code": r.leading_code,
                    "turnover_rate": _f(r.turnover_rate) if r.turnover_rate is not None else None,
                }
            )
        return out

    def _sector_prev_n(self, eng, ts_code: str, asof: date, n: int = 3) -> List[Dict[str, Any]]:
        with eng.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, pct_change, up_num, down_num
                    FROM sector_dc_index_daily
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
                    "pct_change": round(_f(r.pct_change), 3),
                    "up_num": int(r.up_num) if r.up_num is not None else None,
                    "down_num": int(r.down_num) if r.down_num is not None else None,
                }
            )
        return items

    def _sector_members(self, eng, sector_code: str, asof: date, limit: int) -> List[str]:
        with eng.connect() as conn:
            # 优先当日成分；否则最近一日
            rows = conn.execute(
                sa_text(
                    """
                    SELECT con_code FROM sector_dc_member
                    WHERE ts_code = :c AND trade_date = (
                        SELECT MAX(trade_date) FROM sector_dc_member
                        WHERE ts_code = :c AND trade_date <= :d
                    )
                    ORDER BY con_code
                    LIMIT :lim
                    """
                ),
                {"c": sector_code, "d": asof, "lim": limit},
            ).fetchall()
        return [str(r.con_code).upper() for r in rows if r.con_code]

    def _load_stock_mins(self, ts_code: str, asof: date) -> pd.DataFrame:
        try:
            from tools.datahubco_toolkit import datahubco_toolkit

            packed = datahubco_toolkit.get_mins(
                ts_code=ts_code,
                trade_date=asof.isoformat(),
                limit=500,
                source="auto",
                write_back=True,
                include_auction=False,
            )
            rows = packed.get("data") or []
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            df.columns = [str(c).lower() for c in df.columns]
            return df
        except Exception as e:
            logger.debug("mins %s: %s", ts_code, e)
            return pd.DataFrame()

    def _pct_series(self, df: pd.DataFrame) -> Dict[str, float]:
        """bar hhmm -> pct from first/pre_close proxy."""
        if df is None or df.empty or "close" not in df.columns:
            return {}
        work = df.copy()
        # 尽量去掉集合竞价
        if "bar_time" in work.columns:
            work["_hm"] = work["bar_time"].map(_hhmm)
        elif "clock" in work.columns:
            work["_hm"] = work["clock"].astype(str).str.slice(0, 5)
        else:
            return {}
        work = work.dropna(subset=["_hm"])
        work = work[work["_hm"].map(lambda s: s >= "09:30" and s <= "15:00")]
        if work.empty:
            return {}
        closes = pd.to_numeric(work["close"], errors="coerce")
        base = None
        if "pre_close" in work.columns:
            base = _f(work["pre_close"].iloc[0])
        if not base:
            base = _f(closes.iloc[0])
        if base <= 0:
            return {}
        out: Dict[str, float] = {}
        for hm, px in zip(work["_hm"].tolist(), closes.tolist()):
            if px is None or (isinstance(px, float) and pd.isna(px)):
                continue
            out[str(hm)] = (float(px) / base - 1.0) * 100.0
        return out

    def _synthesize_intraday(
        self,
        members: List[str],
        asof: date,
        *,
        sample_every_min: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not members:
            return [], {"member_used": 0, "source": "none"}

        series_list: List[Dict[str, float]] = []

        def one(code: str) -> Optional[Dict[str, float]]:
            df = self._load_stock_mins(code, asof)
            s = self._pct_series(df)
            return s or None

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(one, c): c for c in members}
            for fut in as_completed(futs):
                s = fut.result()
                if s:
                    series_list.append(s)

        if not series_list:
            return [], {"member_used": 0, "source": "empty"}

        # 对齐所有时间戳
        all_t = sorted({t for s in series_list for t in s.keys()})
        # 降采样：每 N 分钟取一根（按分钟数）
        sampled = []
        for t in all_t:
            try:
                hh, mm = t.split(":")
                minute_idx = int(hh) * 60 + int(mm)
            except Exception:
                continue
            if minute_idx % sample_every_min != 0 and t != all_t[-1]:
                continue
            vals = [s[t] for s in series_list if t in s]
            if not vals:
                continue
            sampled.append({"time": t, "pct": round(sum(vals) / len(vals), 3)})
        return sampled, {
            "member_used": len(series_list),
            "source": "member_mins_equal_weight",
        }

    def _shape_label(self, curve: List[Dict[str, Any]]) -> str:
        if not curve or len(curve) < 3:
            return "数据不足"
        vals = [_f(p.get("pct")) for p in curve]
        first, last = vals[0], vals[-1]
        peak, trough = max(vals), min(vals)
        # 简单形态
        if last - first >= 1.0 and last >= peak - 0.3:
            return "震荡抬升"
        if peak - last >= 1.0 and peak > first + 0.5:
            return "冲高回落"
        if first - last >= 1.0 and last <= trough + 0.3:
            return "单边下挫"
        if abs(last - first) < 0.4 and (peak - trough) < 1.0:
            return "窄幅震荡"
        if last > first:
            return "偏强震荡"
        return "偏弱震荡"

    def _sector_tone(
        self,
        daily_pct: float,
        shape: str,
        hist3: List[Dict[str, Any]],
    ) -> str:
        hist_txt = "，".join(
            f"{h['trade_date']} {h['pct_change']:+.2f}%" for h in hist3
        ) or "近3日暂缺"
        cont = ""
        if hist3:
            prev = _f(hist3[-1].get("pct_change"))
            if daily_pct * prev > 0 and abs(daily_pct) >= 0.5:
                cont = "与昨方向一致，偏延续。"
            elif daily_pct * prev < 0 and abs(daily_pct) >= 0.5:
                cont = "与昨方向相反，偏短线纠偏/切换。"
            else:
                cont = "较昨日变化不大。"
        return (
            f"日线涨跌 {daily_pct:+.2f}%；分时形态「{shape}」。"
            f"近3日：{hist_txt}。{cont}"
            f"以上为规则描述，不构成投资建议。"
        )

    def _market_tone(
        self,
        all_rows: List[Dict[str, Any]],
        top_sectors: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        pcts = [_f(r.get("pct_change")) for r in all_rows]
        if not pcts:
            return {"label": "未知", "text": "无板块日线"}
        up = sum(1 for x in pcts if x > 0)
        down = sum(1 for x in pcts if x < 0)
        med = float(pd.Series(pcts).median())
        if up / max(1, len(pcts)) >= 0.65 and med >= 0.3:
            label = "普涨偏强"
        elif down / max(1, len(pcts)) >= 0.65 and med <= -0.3:
            label = "普跌偏弱"
        elif abs(med) < 0.2:
            label = "分化震荡"
        else:
            label = "结构分化"
        leaders = "、".join(
            f"{s['name']}({s['pct_change_daily']:+.2f}%)" for s in top_sectors[:3]
        )
        return {
            "label": label,
            "up_sectors": up,
            "down_sectors": down,
            "median_pct": round(med, 3),
            "text": (
                f"二级行业情绪「{label}」：上涨板块 {up} / 下跌 {down}，"
                f"涨跌幅中位数 {med:+.2f}%。关注 Top：{leaders}。"
            ),
        }


sector_intraday_toolkit = SectorIntradayToolkit()
