# -*- coding: utf-8 -*-
"""Test which empty-table APIs have permissions - rate limited ~0.7s each"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()
from data_layer.collectors.tushare_collector import TushareCollector
from data_layer.collectors.advanced_collector import AdvancedCollector

collector = TushareCollector()
adv = AdvancedCollector()
DELAY = 0.7

# Map empty tables to their API calls and test params
EMPTY_TABLES = [
    # (table_name, test_desc, lambda to call)
    ("stock_adj_factor",        "复权因子",       lambda: collector.get_adj_factor(ts_code="000001.SZ")),
    ("ref_stk_limit_daily",     "涨跌停价格",      lambda: collector.get_stk_limit(ts_code="000001.SZ", trade_date="20260508")),
    ("ref_suspend_d",           "停复牌信息",      lambda: collector.get_suspend_d(ts_code="000001.SZ")),
    ("stock_moneyflow_daily",   "个股资金流向",     lambda: collector.get_moneyflow(ts_code="000001.SZ", start_date="20260501", end_date="20260509")),
    ("index_daily",             "指数日线",        lambda: collector._call_api("index_daily","index_daily",ts_code="000001.SH",start_date="20260501",end_date="20260509")),
    ("index_weight",            "指数权重",        lambda: collector._call_api("index_weight","index_weight",index_code="000300.SH",trade_date="20260509")),
    ("etf_daily",               "ETF日线",        lambda: collector._call_api("fund_daily","fund_daily",ts_code="510050.SH",start_date="20260501",end_date="20260509")),
    ("fund_daily",              "基金日线",        lambda: collector._call_api("fund_daily","fund_daily",ts_code="510300.SH",start_date="20260501",end_date="20260509")),
    ("fund_nav",                "基金净值",        lambda: collector._call_api("fund_nav","fund_nav",ts_code="510050.SH",start_date="20260501",end_date="20260509")),
    ("margin_summary_daily",    "融资融券汇总",     lambda: adv.get_margin(start="20260501", end="20260509")),
    ("margin_detail_daily",     "融资融券明细",     lambda: adv.get_margin_detail(ts_code="000001.SZ", start="20260501", end="20260509")),
    ("stock_monthly",           "月线行情",        lambda: collector._call_api("monthly","monthly",ts_code="000001.SZ",start_date="20210101",end_date="20260101")),
    ("stock_weekly",            "周线行情",        lambda: collector._call_api("weekly","weekly",ts_code="000001.SZ",start_date="20210101",end_date="20260101")),
    ("ref_trade_cal",           "交易日历",        lambda: collector._call_api("trade_cal","trade_cal",exchange="SSE",start_date="20210101",end_date="20251231")),
    ("ref_new_share",           "新股上市",        lambda: collector._call_api("new_share","new_share",start_date="20210101",end_date="20251231")),
    ("ref_stock_namechange",    "股票曾用名",       lambda: collector._call_api("namechange","namechange",ts_code="000001.SZ")),
    # Below: known or suspected no-permission
    ("stock_block_trade",       "大宗交易",        lambda: adv.get_block_trade(ts_code="000001.SZ",start="20260401",end="20260510")),
    ("stock_top_list_daily",    "龙虎榜",         lambda: adv.get_top_list(trade_date="20260509")),
    ("stock_auction_daily",     "集合竞价(分钟权限)", lambda: collector.get_mins(ts_code="000001.SZ",freq="1min",trade_date="20260509")),
    ("broker_report_consensus", "研报(独立权限)",   lambda: adv.get_broker_reports(ts_code="000001.SZ",start="20260401",end="20260510",limit=5)),
]

results = {"has_perm": [], "no_perm": [], "empty_response": [], "error": []}

for tbl, desc, fn in EMPTY_TABLES:
    time.sleep(DELAY)
    try:
        df = fn()
        if df is not None and not df.empty:
            results["has_perm"].append((tbl, desc, len(df)))
            print(f"  ✅ {tbl:<30s} {desc:<18s} -> {len(df):>5} rows  (有权限，未拉取)")
        elif df is not None:
            results["empty_response"].append((tbl, desc, "empty df"))
            print(f"  ⚠️  {tbl:<30s} {desc:<18s} -> 空 DataFrame (可能参数错误或无数据)")
        else:
            results["empty_response"].append((tbl, desc, "returned None"))
            print(f"  ⚠️  {tbl:<30s} {desc:<18s} -> None (接口返回空)")
    except Exception as e:
        msg = str(e)[:100]
        if "权限" in msg or "permission" in msg.lower() or "不允许" in msg or "not allowed" in msg.lower() or "无权限" in msg:
            results["no_perm"].append((tbl, desc, msg))
            print(f"  ❌ {tbl:<30s} {desc:<18s} -> 无权限: {msg}")
        elif "not found" in msg.lower() or "不存在" in msg or "does not exist" in msg.lower():
            results["no_perm"].append((tbl, desc, msg))
            print(f"  ❌ {tbl:<30s} {desc:<18s} -> 接口不存在: {msg}")
        else:
            results["error"].append((tbl, desc, msg))
            print(f"  🔴 {tbl:<30s} {desc:<18s} -> ERROR: {msg}")

print("\n" + "=" * 70)
print("分类汇总")
print("=" * 70)
print(f"\n✅ 有权限、可拉取 ({len(results['has_perm'])} 张表):")
for tbl, desc, n in results['has_perm']:
    print(f"  {tbl:<35s} {desc:<18s} (测试返回 {n} rows)")

print(f"\n❌ 无权限/接口不存在 ({len(results['no_perm'])} 张表):")
for tbl, desc, reason in results['no_perm']:
    print(f"  {tbl:<35s} {desc:<18s} {reason}")

print(f"\n⚠️  返回空数据 ({len(results['empty_response'])} 张表):")
for tbl, desc, reason in results['empty_response']:
    print(f"  {tbl:<35s} {desc:<18s} {reason}")

print(f"\n🔴 其他错误 ({len(results['error'])} 张表):")
for tbl, desc, reason in results['error']:
    print(f"  {tbl:<35s} {desc:<18s} {reason}")

print("\nDone!")
