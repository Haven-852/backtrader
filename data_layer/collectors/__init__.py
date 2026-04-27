"""
数据收集器包
支持从 Tushare, AkShare, FRED 等来源自动导入数据到存储层
支持工作日定时运行
"""

from .collector_manager import DataCollectorManager

__all__ = ['DataCollectorManager']
