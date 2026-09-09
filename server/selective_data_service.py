"""
挑选式数据服务：封装 Datahubco SelectiveDataFetcher，供路由与 dash/strategy 调用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

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


selective_data_service = SelectiveDataService()
