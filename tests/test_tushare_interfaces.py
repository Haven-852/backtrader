# -*- coding: utf-8 -*-
"""Tushare API availability test - rate limited"""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_layer.config import config
from data_layer.collectors.tushare_collector import TushareCollector
import pandas as pd

print("=" * 60)
print("Tushare Interface Test")
print(f"API URL: {config.tushare_api_url}")
print(f"Token groups: {config.list_configured_groups()}")
print("=" * 60)

collector = TushareCollector()
print(f"Status: {collector.status()}")
print()

TESTS = [
    ("daily",       "get_daily",       {"ts_code": "000001.SZ", "start_date": "20260501", "end_date": "20260509"}, "daily bars"),
    ("daily_basic", "get_daily_basic", {"trade_date": "20260509"}, "daily_basic"),
    ("adj_factor",  "get_adj_factor",  {"ts_code": "000001.SZ", "trade_date": "20260509"}, "adj_factor"),
    ("stk_limit",   "get_stk_limit",   {"ts_code": "000001.SZ", "trade_date": "20260509"}, "stk_limit"),
    ("suspend_d",   "get_suspend_d",   {"ts_code": "000001.SZ"}, "suspend_d"),
    ("fina_indicator","get_fina_indicator", {"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250501"}, "fina_indicator"),
    ("income",      "get_income",      {"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250501"}, "income"),
    ("balancesheet","get_balancesheet",{"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250501"}, "balancesheet"),
    ("cashflow",    "get_cashflow",    {"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250501"}, "cashflow"),
    ("forecast",    "get_forecast",    {"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250501"}, "forecast"),
    ("express",     "get_express",     {"ts_code": "000001.SZ", "start_date": "20250101", "end_date": "20250501"}, "express"),
    ("dividend",    "get_dividend",    {"ts_code": "000001.SZ", "start_date": "20200101", "end_date": "20250501"}, "dividend"),
    ("moneyflow",   "get_moneyflow",   {"ts_code": "000001.SZ", "start_date": "20260501", "end_date": "20260509"}, "moneyflow"),
    ("moneyflow_hsgt","get_moneyflow_hsgt", {"start_date": "20260501", "end_date": "20260509"}, "moneyflow_hsgt"),
    ("stk_mins_1min", "get_mins",      {"ts_code": "000001.SZ", "freq": "1min", "trade_date": "20260509"}, "stk_mins 1min"),
    ("stk_mins_auction","get_auction", {"ts_code": "000001.SZ", "trade_date": "20260509"}, "stk_auction"),
]

results = {}
for api_name, method_name, params, desc in TESTS:
    time.sleep(0.6)
    try:
        method = getattr(collector, method_name, None)
        if method:
            df = method(**params)
            if df is not None and not df.empty:
                cols = list(df.columns)[:8]
                results[api_name] = {"status": "OK", "rows": len(df), "columns": cols}
                print(f"  OK  {api_name:20s} {desc:18s} -> {len(df):>5} rows  {cols}")
            elif df is not None:
                results[api_name] = {"status": "EMPTY"}
                print(f"  EMPTY {api_name:20s} {desc:18s}")
            else:
                results[api_name] = {"status": "NONE"}
                print(f"  NONE {api_name:20s} {desc:18s}")
        else:
            results[api_name] = {"status": "NO_METHOD"}
            print(f"  NO_METHOD {api_name:20s} {desc:18s}")
    except Exception as e:
        msg = str(e)[:120]
        results[api_name] = {"status": "ERROR", "error": msg}
        print(f"  ERROR {api_name:20s} {desc:18s} -> {msg}")

# Extra: index, fund via raw API call
print("\n--- Index / Fund / News tests ---")
for interface, params, desc in [
    ("index_basic", {"market": "SSE"}, "index_basic"),
    ("fund_basic", {}, "fund_basic"),
    ("fund_nav", {"ts_code": "510050.SH", "start_date": "20260501", "end_date": "20260509"}, "fund_nav"),
]:
    time.sleep(0.6)
    try:
        api = collector._get_api(interface)
        if api:
            method = getattr(api, interface)
            df = method(**params)
            if df is not None and not df.empty:
                results[interface] = {"status": "OK", "rows": len(df)}
                print(f"  OK  {interface:20s} {desc:18s} -> {len(df):>5} rows")
            elif df is not None:
                results[interface] = {"status": "EMPTY"}
                print(f"  EMPTY {interface:20s} {desc:18s}")
            else:
                results[interface] = {"status": "NONE"}
                print(f"  NONE {interface:20s} {desc:18s}")
        else:
            print(f"  NO_API {interface:20s} {desc:18s}")
    except Exception as e:
        msg = str(e)[:120]
        results[interface] = {"status": "ERROR", "error": msg}
        print(f"  ERROR {interface:20s} {desc:18s} -> {msg}")

# News/announcements/broker reports
print()
from data_layer.collectors.advanced_collector import AdvancedCollector
adv = AdvancedCollector()

for method_name, params, desc in [
    ("get_news", {"start": "20260501", "end": "20260510", "limit": 10}, "news"),
    ("get_announcements", {"ts_code": "000001.SZ", "start": "20260401", "end": "20260510", "limit": 10}, "announcements"),
    ("get_broker_reports", {"ts_code": "000001.SZ", "start": "20260401", "end": "20260510", "limit": 10}, "broker_reports"),
    ("get_top_list", {"trade_date": "20260509"}, "top_list"),
    ("get_margin", {"start": "20260501", "end": "20260509"}, "margin"),
    ("get_block_trade", {"ts_code": "000001.SZ", "start": "20260401", "end": "20260510"}, "block_trade"),
    ("get_fund_nav", {"ts_code": "510050.SH", "start": "20260501", "end": "20260509"}, "fund_nav_adv"),
    ("get_index_daily", {"ts_code": "000001.SH", "start": "20260501", "end": "20260509"}, "index_daily"),
]:
    time.sleep(0.6)
    try:
        method = getattr(adv, method_name)
        df = method(**params)
        key = f"adv_{method_name}"
        if df is not None and not df.empty:
            results[key] = {"status": "OK", "rows": len(df)}
            print(f"  OK  {method_name:25s} {desc:18s} -> {len(df):>5} rows")
        elif df is not None:
            results[key] = {"status": "EMPTY"}
            print(f"  EMPTY {method_name:25s} {desc:18s}")
        else:
            results[key] = {"status": "NONE"}
            print(f"  NONE {method_name:25s} {desc:18s}")
    except Exception as e:
        msg = str(e)[:120]
        results[key] = {"status": "ERROR", "error": msg}
        print(f"  ERROR {method_name:25s} {desc:18s} -> {msg}")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
ok = {k: v for k, v in results.items() if v["status"] == "OK"}
fail = {k: v for k, v in results.items() if v["status"] != "OK"}
print(f"\nWorking interfaces ({len(ok)}):")
for k, v in ok.items():
    print(f"  {k:<30s} {v.get('rows', '?'):>6} rows")
print(f"\nFailed/Skipped ({len(fail)}):")
for k, v in fail.items():
    print(f"  {k:<30s} {v['status']:8s} {v.get('error', '')}")

with open("test_tushare_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print(f"\nResults saved to test_tushare_results.json")
