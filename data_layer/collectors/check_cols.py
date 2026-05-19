"""检查 API 返回的列 vs 表结构"""
import os, sys
os.chdir(r'E:\demo\backtrader')
sys.path.insert(0, '.'); sys.path.insert(0, 'data_layer')

import tushare as ts
from data_layer.config import config as cfg
from datetime import datetime

API_URL = cfg.tushare_api_url

def show_cols(label, token_group, api_name, params=None):
    ts.set_token(cfg.get_tushare_token(token_group))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL
    fn = getattr(pro, api_name)
    try:
        df = fn(**(params or {}))
        if df is not None and not df.empty:
            print(f"\n{label} ({api_name}): {len(df)} rows")
            print(f"  Columns: {list(df.columns)}")
            print(f"  Dtypes:\n{df.dtypes}")
            return df
        else:
            print(f"\n{label} ({api_name}): EMPTY or None")
            return None
    except Exception as e:
        print(f"\n{label} ({api_name}): FAIL - {str(e)[:100]}")
        return None

end_date = datetime.now().strftime('%Y%m%d')

# Check each problem table
show_cols("ref_suspend_d", "basic", "suspend_d")
show_cols("ref_new_share", "basic", "new_share")
show_cols("ref_stock_namechange", "basic", "namechange")
show_cols("margin_summary", "flow", "margin", {"start_date": "20260501", "end_date": "20260515"})
