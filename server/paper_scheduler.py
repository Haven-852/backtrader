"""
纸面交易定时器（进程内常驻）

- 交易日前一晚 21:00：A/B Top5 选股入 watchlist
- 交易日 09:30：对绑定账户跑一键日课（T+1 日切 + 风控 + 一次分时做T）
- 交易日 13:00：判断持仓明天上涨概率，大于 60% 满仓，否则留出 20% 现金
- 交易日 09:30–11:30、13:00–15:00：每 5 分钟对 watchlist 再扫一次分时做T
- 同频：若开启 alert_auto_enabled，对绑定账户做持仓告警扫描并推送（微信/企微等）
- 交易日周五 15:10：周复盘（可关）

依赖后端进程一直运行。晚间选股、开市日课、周复盘每个日历日只跑一次；盘中做T/告警每个 5 分钟格子跑一次。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager
from server.paper_cycle_service import paper_cycle_service

logger = logging.getLogger(__name__)

SH = ZoneInfo("Asia/Shanghai")


class PaperScheduler:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.last_error: Optional[str] = None
        self.last_tick: Optional[str] = None
        self.busy = False

    def status(self) -> Dict[str, Any]:
        cfg = {}
        try:
            cfg = paper_cycle_service.get_config().get("config") or {}
        except Exception as e:
            self.last_error = str(e)
        now = datetime.now(SH)
        return {
            "ok": True,
            "running": self._running and self._task is not None and not self._task.done(),
            "busy": self.busy,
            "now_sh": now.isoformat(timespec="seconds"),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "account_id": cfg.get("scheduler_account_id"),
            "use_llm": bool(cfg.get("scheduler_use_llm")),
            "enabled": bool(cfg.get("scheduler_enabled")),
            "evening": f"{int(cfg.get('scheduler_evening_hour') or 21):02d}:{int(cfg.get('scheduler_evening_minute') or 0):02d}",
            "open": f"{int(cfg.get('scheduler_open_hour') or 9):02d}:{int(cfg.get('scheduler_open_minute') or 30):02d}",
            "weekly": bool(cfg.get("scheduler_weekly", True)),
            "last_evening": cfg.get("scheduler_last_evening"),
            "last_open": cfg.get("scheduler_last_open"),
            "last_weekly": cfg.get("scheduler_last_weekly"),
            "last_intraday": cfg.get("scheduler_last_intraday"),
            "last_exposure": cfg.get("scheduler_last_exposure"),
            "exposure_cap": cfg.get("exposure_cap"),
            "exposure_prob": cfg.get("exposure_prob"),
            "t_scan_minutes": int(cfg.get("t_scan_minutes") or 5),
            "note": "后端进程需保持运行。交易日盘中每 5 分钟对 watchlist 做一次分时做T。",
        }

    def is_trading_day(self, d: date) -> bool:
        eng = storage_manager.get_postgres_engine()
        if not eng:
            return d.weekday() < 5
        try:
            with eng.connect() as conn:
                row = conn.execute(
                    sa_text(
                        """
                        SELECT is_open FROM ref_trade_cal
                        WHERE cal_date = :d AND exchange = 'SSE'
                        LIMIT 1
                        """
                    ),
                    {"d": d},
                ).fetchone()
            if row is None:
                return d.weekday() < 5
            return int(row.is_open) == 1
        except Exception as e:
            logger.warning("交易日历查询失败，按工作日判断: %s", e)
            return d.weekday() < 5

    def next_trading_day(self, d: date) -> Optional[date]:
        for i in range(1, 12):
            cand = d + timedelta(days=i)
            if self.is_trading_day(cand):
                return cand
        return None

    def should_evening(self, now: datetime, cfg: Dict[str, Any]) -> bool:
        """当晚 21:00 起、午夜前：若下一交易日紧挨当前休市段，则选股一次。"""
        hh = int(cfg.get("scheduler_evening_hour") or 21)
        mm = int(cfg.get("scheduler_evening_minute") or 0)
        if (now.hour, now.minute) < (hh, mm):
            return False
        today = now.date()
        if str(cfg.get("scheduler_last_evening") or "") == today.isoformat():
            return False
        nxt = self.next_trading_day(today)
        if not nxt:
            return False
        # 只在「下一交易日之前的那个晚上」跑，避免连续多晚重复
        # 例如周五晚→周一；节前最后一晚→节后首个交易日
        tomorrow = today + timedelta(days=1)
        # 下一交易日就是明天，或中间只有休市（周末/假期的前一晚与假期中的晚上都会刷新名单）
        return nxt == tomorrow or (tomorrow < nxt and (nxt - today).days <= 10)

    def _missed_evening(self, now: datetime, cfg: Dict[str, Any]) -> bool:
        """开盘前若昨晚没跑过选股，且今天开市，则补跑一次。"""
        if now.hour > 9 or (now.hour == 9 and now.minute >= 25):
            return False
        today = now.date()
        yday = (today - timedelta(days=1)).isoformat()
        last = str(cfg.get("scheduler_last_evening") or "")
        if last in (yday, today.isoformat()):
            return False
        return self.is_trading_day(today)

    def should_open(self, now: datetime, cfg: Dict[str, Any]) -> bool:
        hh = int(cfg.get("scheduler_open_hour") or 9)
        mm = int(cfg.get("scheduler_open_minute") or 30)
        start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        end = start + timedelta(minutes=40)
        if not (start <= now < end):
            return False
        today = now.date()
        if str(cfg.get("scheduler_last_open") or "") == today.isoformat():
            return False
        return self.is_trading_day(today)

    def should_weekly(self, now: datetime, cfg: Dict[str, Any]) -> bool:
        if not bool(cfg.get("scheduler_weekly", True)):
            return False
        # 周五收盘后 15:10–15:50
        if now.weekday() != 4:
            return False
        start = now.replace(hour=15, minute=10, second=0, microsecond=0)
        end = start + timedelta(minutes=40)
        if not (start <= now < end):
            return False
        today = now.date()
        if str(cfg.get("scheduler_last_weekly") or "")[:10] == today.isoformat():
            return False
        return self.is_trading_day(today)

    def session_bucket(self, now: datetime, minutes: int = 5) -> Optional[str]:
        """交易时段内，把当前时间收成 N 分钟一格。非交易时段返回空。"""
        if not self.is_trading_day(now.date()):
            return None
        step = max(1, int(minutes or 5))
        hm = now.hour * 60 + now.minute
        morning = 9 * 60 + 30 <= hm <= 11 * 60 + 30
        afternoon = 13 * 60 <= hm <= 15 * 60
        if not (morning or afternoon):
            return None
        floored = (now.minute // step) * step
        return f"{now.date().isoformat()}T{now.hour:02d}:{floored:02d}"

    def should_exposure(self, now: datetime, cfg: Dict[str, Any]) -> bool:
        """13:00 之后、收盘前，每个交易日只判一次仓位。"""
        if str(cfg.get("scheduler_last_exposure") or "")[:10] == now.date().isoformat():
            return False
        if not self.is_trading_day(now.date()):
            return False
        hm = now.hour * 60 + now.minute
        return 13 * 60 <= hm < 15 * 60

    async def _tick(self) -> None:
        cfg = paper_cycle_service.get_config().get("config") or {}
        if not cfg.get("scheduler_enabled"):
            return
        now = datetime.now(SH)
        self.last_tick = now.isoformat(timespec="seconds")

        if self.should_evening(now, cfg):
            logger.info("定时器：晚间选股 %s", now.date())
            # fill=True：确保「当天」日线齐，按当日收盘条件选 A/B
            result = await asyncio.to_thread(paper_cycle_service.evening_screen, None, fill=True, limit=200)
            paper_cycle_service.update_config({"scheduler_last_evening": now.date().isoformat()})
            logger.info(
                "晚间选股完成 ok=%s asof=%s count=%s",
                result.get("ok"),
                result.get("asof"),
                result.get("count"),
            )
            if not result.get("ok"):
                self.last_error = str(result.get("error"))
        elif self._missed_evening(now, cfg):
            yday = (now.date() - timedelta(days=1)).isoformat()
            logger.info("定时器：补跑昨晚选股（标记 %s）", yday)
            result = await asyncio.to_thread(paper_cycle_service.evening_screen, None, fill=True, limit=200)
            paper_cycle_service.update_config({"scheduler_last_evening": yday})
            if not result.get("ok"):
                self.last_error = str(result.get("error"))

        cfg = paper_cycle_service.get_config().get("config") or {}
        aid = cfg.get("scheduler_account_id")
        use_llm = bool(cfg.get("scheduler_use_llm"))

        if self.should_open(now, cfg):
            if not aid:
                self.last_error = "未绑定账户，跳过 09:30 日课"
                logger.warning(self.last_error)
                paper_cycle_service.update_config({"scheduler_last_open": now.date().isoformat()})
            else:
                logger.info("定时器：开市日课 account=%s llm=%s", aid, use_llm)
                result = await paper_cycle_service.run_trading_day(
                    int(aid), use_llm=use_llm, trigger_source="scheduler_daily_lesson"
                )
                bucket = self.session_bucket(now, int(cfg.get("t_scan_minutes") or 5))
                paper_cycle_service.update_config({
                    "scheduler_last_open": now.date().isoformat(),
                    "scheduler_last_intraday": bucket or now.strftime("%Y-%m-%dT%H:%M"),
                })
                logger.info("日课完成 ok=%s", result.get("ok"))

        cfg = paper_cycle_service.get_config().get("config") or {}
        aid = cfg.get("scheduler_account_id")
        if self.should_exposure(now, cfg) and aid:
            logger.info("定时器：13:00 仓位 account=%s", aid)
            result = await paper_cycle_service.afternoon_exposure(
                int(aid),
                use_llm=bool(cfg.get("scheduler_use_llm")),
                trigger_source="scheduler_exposure",
            )
            logger.info(
                "仓位完成 ok=%s cap=%s prob=%s",
                result.get("ok"),
                result.get("exposure_cap"),
                result.get("portfolio_probability"),
            )
            if not result.get("ok"):
                self.last_error = str(result.get("error"))

        cfg = paper_cycle_service.get_config().get("config") or {}
        aid = cfg.get("scheduler_account_id")
        bucket = self.session_bucket(now, int(cfg.get("t_scan_minutes") or 5))
        last_bucket = str(cfg.get("scheduler_last_intraday") or "")
        if bucket and bucket != last_bucket and aid and not self.should_open(now, cfg):
            logger.info("定时器：盘中做T %s account=%s", bucket, aid)
            result = await paper_cycle_service.intraday_t(
                int(aid),
                use_llm=bool(cfg.get("scheduler_use_llm")),
                trigger_source="price_scan",
            )
            paper_cycle_service.update_config({"scheduler_last_intraday": bucket})
            logger.info("盘中做T完成 ok=%s count=%s", result.get("ok"), result.get("count"))
            if not result.get("ok"):
                self.last_error = str(result.get("error"))

        # 持仓告警：盘中与做T同频；也可在仅开启告警时扫描
        cfg = paper_cycle_service.get_config().get("config") or {}
        aid = cfg.get("scheduler_account_id")
        alert_on = bool(cfg.get("alert_auto_enabled"))
        bucket = self.session_bucket(now, int(cfg.get("t_scan_minutes") or 5))
        last_alert = str(cfg.get("alert_last_scan_bucket") or "")
        if aid and alert_on and bucket and bucket != last_alert:
            try:
                from server.paper_alert_service import paper_alert_service
                import asyncio

                logger.info("定时器：持仓告警扫描 %s account=%s", bucket, aid)
                result = await asyncio.to_thread(
                    paper_alert_service.scan_account, int(aid), notify=True, force=False
                )
                paper_cycle_service.update_config({"alert_last_scan_bucket": bucket})
                logger.info(
                    "告警扫描完成 fired=%s channels=%s",
                    result.get("fired_count"),
                    result.get("channels"),
                )
            except Exception as e:
                self.last_error = f"告警扫描失败: {e}"
                logger.exception("告警扫描失败")

        # 现实自选监控：不依赖账户，盘中同频扫描 + LLM Markdown 推送
        cfg = paper_cycle_service.get_config().get("config") or {}
        mon_on = bool(cfg.get("monitor_auto_enabled"))
        bucket = self.session_bucket(now, int(cfg.get("t_scan_minutes") or 5))
        last_mon = str(cfg.get("monitor_last_scan_bucket") or "")
        if mon_on and bucket and bucket != last_mon:
            try:
                from server.paper_monitor_service import paper_monitor_service

                logger.info("定时器：自选监控扫描 %s", bucket)
                result = await paper_monitor_service.scan(notify=True)
                paper_cycle_service.update_config({"monitor_last_scan_bucket": bucket})
                logger.info(
                    "自选监控完成 fired=%s report=%s",
                    result.get("fired_count"),
                    result.get("report_id"),
                )
            except Exception as e:
                self.last_error = f"自选监控失败: {e}"
                logger.exception("自选监控失败")

        cfg = paper_cycle_service.get_config().get("config") or {}
        if self.should_weekly(now, cfg) and cfg.get("scheduler_account_id"):
            aid = int(cfg["scheduler_account_id"])
            logger.info("定时器：周复盘 account=%s", aid)
            await paper_cycle_service.weekly_review(
                aid, use_llm=bool(cfg.get("scheduler_use_llm")), auto_apply=True
            )
            paper_cycle_service.update_config({"scheduler_last_weekly": now.date().isoformat()})

    async def _loop(self) -> None:
        logger.info("纸面交易定时器已启动（Asia/Shanghai）")
        while self._running:
            try:
                self.busy = True
                await self._tick()
                self.busy = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.busy = False
                self.last_error = str(e)
                logger.exception("定时器 tick 失败")
            await asyncio.sleep(20)

    def start(self) -> Dict[str, Any]:
        if self._task and not self._task.done():
            self._running = True
            return self.status()
        self._running = True
        self._task = asyncio.get_running_loop().create_task(self._loop())
        return self.status()

    async def stop(self) -> Dict[str, Any]:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        return self.status()


paper_scheduler = PaperScheduler()
