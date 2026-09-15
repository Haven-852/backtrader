"""Datahubco 采集测试包：板块当日行情等。"""

__all__ = ["run_sector_daily_test", "resolve_trade_date"]


def __getattr__(name: str):
    if name in __all__:
        from .test_sector_daily import resolve_trade_date, run_sector_daily_test

        return {
            "run_sector_daily_test": run_sector_daily_test,
            "resolve_trade_date": resolve_trade_date,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
