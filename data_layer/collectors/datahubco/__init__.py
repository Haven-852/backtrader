"""
Datahubco 采集包
- selective: 挑选式（自选 / 看板 / 策略）→ PostgreSQL
- continuous: 持续获取骨架（板块 / ETF / 指数）
"""

from .client import DatahubcoClient
from .selective import SelectiveDataFetcher, selective_fetcher
from .continuous import ContinuousDataFetcher, continuous_fetcher

__all__ = [
    "DatahubcoClient",
    "SelectiveDataFetcher",
    "selective_fetcher",
    "ContinuousDataFetcher",
    "continuous_fetcher",
]
