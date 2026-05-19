"""逐个测试 Tushare API 接口"""
import os, sys
os.chdir(r'E:\demo\backtrader')
sys.path.insert(0, '.')
sys.path.insert(0, 'data_layer')

import tushare as ts
from data_layer.config import config as cfg
from datetime import datetime, timedelta

API_URL = cfg.tushare_api_url
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

results = {}

def test(token_group, api_name, params=None):
    token = cfg.get_tushare_token(token_group)
    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = API_URL
    fn = getattr(pro, api_name)
    try:
        df = fn(**(params or {}))
        if df is not None and not df.empty:
            n = len(df)
            cols = list(df.columns)[:5]
            print(f"  [OK] {api_name}: {n} rows, cols: {cols}")
            results[api_name] = f"OK ({n} rows)"
            return df
        else:
            print(f"  [EMPTY] {api_name}: API ok but no data")
            results[api_name] = "EMPTY"
            return None
    except Exception as e:
        msg = str(e)[:120]
        print(f"  [FAIL] {api_name}: {msg}")
        results[api_name] = f"FAIL: {msg}"
        return None

# === 阶段1: 参考数据 ===
print("=" * 60)
print("Test 1: ref_suspend_d (suspend_d)")
test("basic", "suspend_d")

print("Test 2: ref_new_share (new_share)")
test("basic", "new_share")

print("Test 3: ref_stock_namechange (namechange)")
test("basic", "namechange")

print("Test 4: ref_trade_cal (trade_cal) - verify already collected")
test("basic", "trade_cal", {"exchange": "SSE", "start_date": "20000101", "end_date": end_date})

# === 阶段2: 基金/ETF ===
print("=" * 60)
print("Test 5: fund_basic (market=E)")
df_etf = test("fund", "fund_basic", {"market": "E"})

print("Test 6: fund_nav")
test("fund", "fund_nav", {"start_date": start_date, "end_date": end_date})

print("Test 7: fund_daily (all)")
test("fund", "fund_daily", {"start_date": start_date, "end_date": end_date})

if df_etf is not None and not df_etf.empty:
    code = df_etf["ts_code"].iloc[0]
    print(f"Test 8: fund_daily for single ETF: {code}")
    test("fund", "fund_daily", {"ts_code": code, "start_date": "20200101", "end_date": end_date})

# === 阶段3: 两融+竞价 ===
print("=" * 60)
print("Test 9: margin (flow token)")
test("flow", "margin", {"start_date": "20260501", "end_date": "20260515"})

print("Test 10: stk_mins auction (mins token)")
test("mins", "stk_mins", {"ts_code": "000001.SZ", "freq": "auction", "trade_date": "20260515"})

# === Summary ===
print("=" * 60)
print("SUMMARY:")
for name, status in sorted(results.items()):
    print(f"  {name:25s} -> {status}")
