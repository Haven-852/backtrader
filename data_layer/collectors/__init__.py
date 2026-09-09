"""
数据收集器包
支持从 Tushare, AkShare, FRED、Datahubco 等来源导入数据
"""

# 避免在 import 子模块时强依赖 akshare 等可选包；按需导入 DataCollectorManager。

def __getattr__(name: str):
    if name == "DataCollectorManager":
        from .collector_manager import DataCollectorManager

        return DataCollectorManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DataCollectorManager"]
