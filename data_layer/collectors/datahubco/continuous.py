"""
持续获取骨架：板块 / ETF / 大盘指数等全市场或清单式定时拉取。
当前仅定义入口与计划；具体调度后续接入（cron / start-all）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("collector.datahubco.continuous")

# 默认定时关注的核心指数（可持续扩展）
DEFAULT_INDICES = [
    "000001.SH",  # 上证
    "399001.SZ",  # 深成
    "399006.SZ",  # 创业板
    "000300.SH",  # 沪深300
    "000905.SH",  # 中证500
    "000852.SH",  # 中证1000
]


class ContinuousDataFetcher:
    """
    持续获取（全市场/板块/ETF/指数）。
    与挑选式分离：本类面向批量与定时，不因单票搜索触发。
    """

    def __init__(self):
        self.enabled = False  # 显式开启后再跑

    def plan(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.enabled,
            "targets": {
                "indices": DEFAULT_INDICES,
                "etf": "etf_daily / fund_daily（待接 datahubco）",
                "sectors": "申万行业成分（待接）",
            },
            "note": "持续获取尚未启用调度；挑选式请用 SelectiveDataFetcher",
        }

    def run_once(self, targets: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "continuous fetcher 未启用", "plan": self.plan()}
        # TODO: 调用 DatahubcoClient 批量写 index_daily / etf_daily
        logger.warning("ContinuousDataFetcher.run_once 尚未实现")
        return {"ok": False, "error": "not_implemented"}


continuous_fetcher = ContinuousDataFetcher()
