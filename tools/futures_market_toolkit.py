"""
期货行情中心工具：读主连报价 / 检查覆盖 / 补数入库 / 日周月K线
供大模型对话 function calling。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def _codes(v: Any) -> Optional[List[str]]:
    if v is None or v == "":
        return None
    if isinstance(v, list):
        return [str(x).strip().upper() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return None
    return [p.strip().upper() for p in s.replace("，", ",").split(",") if p.strip()]


class FuturesMarketToolkit:
    def _svc(self):
        from server.futures_market_service import futures_market_service

        return futures_market_service

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def list_catalog(self) -> Dict[str, Any]:
        return await self._run(self._svc().catalog)

    async def get_board(
        self,
        category: Optional[str] = None,
        ensure: bool = True,
        force: bool = False,
    ) -> Dict[str, Any]:
        """读取行情中心看板；默认先对齐上一交易日。"""
        if ensure:
            return await self._run(
                self._svc().ensure_prev_day_board,
                category=category or None,
                force=bool(force),
            )
        return await self._run(
            self._svc().get_board,
            category=category or None,
            force_refresh=bool(force),
        )

    async def get_quotes(
        self,
        ts_codes: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        ensure: bool = True,
    ) -> Dict[str, Any]:
        return await self._run(
            self._svc().get_quotes,
            ts_codes=_codes(ts_codes),
            category=category or None,
            ensure=bool(ensure),
        )

    async def get_kline(
        self,
        ts_code: str,
        freq: str = "daily",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 120,
        ensure: bool = True,
    ) -> Dict[str, Any]:
        code = (ts_code or "").strip().upper()
        if not code:
            return {"ok": False, "error": "缺少 ts_code"}
        if ensure:
            await self._run(
                self._svc().sync_quotes,
                ts_codes=[code],
                force=False,
            )
        return await self._run(
            self._svc().get_kline,
            ts_code=code,
            freq=freq or "daily",
            start=start,
            end=end,
            limit=int(limit or 120),
        )

    async def check_coverage(
        self,
        ts_codes: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        asof: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._run(
            self._svc().check_coverage,
            ts_codes=_codes(ts_codes),
            category=category or None,
            asof=asof,
        )

    async def sync_quotes(
        self,
        ts_codes: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        asof: Optional[str] = None,
        lookback_days: int = 5,
        force: bool = False,
    ) -> Dict[str, Any]:
        """发现缺失或日期不对时补拉 Datahubco 并写入 fut_daily。"""
        return await self._run(
            self._svc().sync_quotes,
            ts_codes=_codes(ts_codes),
            category=category or None,
            asof=asof,
            lookback_days=int(lookback_days or 5),
            force=bool(force),
        )

    async def ensure_and_get(
        self,
        ts_codes: Optional[Union[str, List[str]]] = None,
        category: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """一站式：检查 → 补数入库 → 返回最新报价。"""
        sync = await self.sync_quotes(
            ts_codes=ts_codes, category=category, force=force
        )
        quotes = await self.get_quotes(
            ts_codes=ts_codes, category=category, ensure=False
        )
        return {
            "ok": True,
            "asof": sync.get("asof") or quotes.get("asof"),
            "sync": sync,
            "quotes": quotes.get("quotes") or [],
            "count": quotes.get("count") or 0,
            "note": "已补数入库并返回行情中心最新主连报价",
        }


futures_market_toolkit = FuturesMarketToolkit()
