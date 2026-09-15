"""分时服务：盘中 Redis 出图 + 异步落 Timescale；历史只查 Timescale。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class IntradayService:
    def __init__(self):
        self._fetcher = None

    @property
    def fetcher(self):
        if self._fetcher is None:
            from data_layer.collectors.datahubco.intraday import IntradayFetcher

            self._fetcher = IntradayFetcher()
        return self._fetcher

    async def get(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        history: bool = False,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fetcher.get, ts_code, trade_date, history)


intraday_service = IntradayService()
