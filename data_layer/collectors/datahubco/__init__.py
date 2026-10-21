"""
Datahubco 采集包
- selective: 挑选式（自选 / 看板 / 策略）→ PostgreSQL
- continuous: 持续获取骨架（板块 / ETF / 指数）
- fundamentals: 资金 / 基本面 / 板块资金
"""

from .client import DatahubcoClient
from .selective import SelectiveDataFetcher, selective_fetcher
from .continuous import ContinuousDataFetcher, continuous_fetcher
from .sectors import SectorDataFetcher, sector_fetcher
from .intraday import IntradayFetcher, intraday_fetcher
from .fundamentals import FundDataFetcher, fund_data_fetcher, FUND_DATASETS

__all__ = [
    "DatahubcoClient",
    "SelectiveDataFetcher",
    "selective_fetcher",
    "ContinuousDataFetcher",
    "continuous_fetcher",
    "SectorDataFetcher",
    "sector_fetcher",
    "IntradayFetcher",
    "intraday_fetcher",
    "FundDataFetcher",
    "fund_data_fetcher",
    "FUND_DATASETS",
]
