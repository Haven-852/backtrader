"""
挑选式数据服务：封装 Datahubco SelectiveDataFetcher，供路由与 dash/strategy 调用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)


class SelectiveDataService:
    def __init__(self):
        self._fetcher = None

    @property
    def fetcher(self):
        if self._fetcher is None:
            from data_layer.collectors.datahubco import selective_fetcher

            self._fetcher = selective_fetcher
        return self._fetcher

    async def ensure(
        self,
        ts_code: str,
        mode: str = "fresh",
        before_date: Optional[str] = None,
        extend_days: int = 60,
    ) -> Dict[str, Any]:
        """异步包装，避免阻塞事件循环"""
        return await asyncio.to_thread(
            self.fetcher.ensure,
            ts_code,
            mode,
            before_date,
            extend_days,
        )

    async def ensure_fresh_if_needed(self, ts_code: str) -> Dict[str, Any]:
        """看板/策略静默补数：失败不抛，只记日志"""
        try:
            # 指数/ETF 暂不走 stock_* 挑选（后续 continuous）
            code = (ts_code or "").upper()
            if code.startswith(("000001.SH", "399")) and code.endswith((".SH", ".SZ")):
                # 部分指数码与个股冲突，仅跳过明显指数前缀中的核心指数表逻辑由 dash 自行切表
                pass
            result = await self.ensure(ts_code, mode="fresh")
            if result.get("skipped"):
                logger.debug("ensure_fresh skip %s", ts_code)
            elif result.get("ok"):
                logger.info(
                    "ensure_fresh %s upserted=%s",
                    ts_code,
                    result.get("upserted"),
                )
            else:
                logger.warning("ensure_fresh 失败 %s: %s", ts_code, result.get("error"))
            return result
        except Exception as e:
            logger.warning("ensure_fresh 异常 %s: %s", ts_code, e)
            return {"ok": False, "error": str(e), "ts_code": ts_code}

    async def ensure_range(
        self,
        ts_code: str,
        start: str,
        end: str,
        extra_codes: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """对照交易日历检查区间覆盖，缺口则 Datahubco 补入 PostgreSQL"""
        return await asyncio.to_thread(
            self.fetcher.ensure_range,
            ts_code,
            start,
            end,
            extra_codes,
        )

    async def ensure_recent(self, ts_code: str, trading_days: int = 30) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fetcher.ensure_recent, ts_code, trading_days)

    async def watchlist_add(
        self,
        ts_code: str,
        name: Optional[str] = None,
        short_name: Optional[str] = None,
        kind: str = "stock",
        industry: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.fetcher.watchlist_add, ts_code, name, short_name, kind, industry
        )

    async def watchlist_remove(self, ts_code: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fetcher.watchlist_remove, ts_code)

    async def watchlist_list(self) -> list:
        return await asyncio.to_thread(self.fetcher.watchlist_list)

    async def sync_watchlist_continuous(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fetcher.sync_watchlist_continuous)


selective_data_service = SelectiveDataService()


async def watchlist_continuous_loop(interval_sec: int = 300, first_delay: int = 15):
    """后台持续同步自选股日线；仅交易时段（09:15–15:00）运行。"""
    await asyncio.sleep(first_delay)
    last_skip_reason: Optional[str] = None
    while True:
        try:
            def _session_ctx():
                fetcher = selective_data_service.fetcher
                fetcher.ensure_trade_cal()
                return fetcher.session_context()

            ctx = await asyncio.to_thread(_session_ctx)
            if not ctx.get("in_trading_session"):
                reason = str(ctx.get("reason") or "not_in_session")
                if reason != last_skip_reason:
                    logger.info(
                        "非交易时段，暂停自选持续同步（%s · asof=%s）",
                        reason,
                        ctx.get("asof"),
                    )
                    last_skip_reason = reason
                await asyncio.sleep(interval_sec)
                continue
            last_skip_reason = None
            result = await selective_data_service.sync_watchlist_continuous()
            if result.get("skipped"):
                logger.info(
                    "非交易时段，跳过自选持续同步（%s · asof=%s）",
                    result.get("reason"),
                    result.get("asof"),
                )
            else:
                n = len(result.get("synced") or [])
                if n:
                    logger.info("自选持续同步完成 %d 只", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("自选持续同步循环异常")
        await asyncio.sleep(interval_sec)
