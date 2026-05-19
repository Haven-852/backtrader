"""完整修复采集: 处理 schema 不匹配问题, 写入4张之前失败的表"""
import os, sys
os.chdir(r'E:\demo\backtrader')
sys.path.insert(0, '.'); sys.path.insert(0, 'data_layer')

import tushare as ts
from data_layer.config import config as cfg
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime, timedelta
import time

API_URL = cfg.tushare_api_url

def get_engine():
    return create_engine(cfg.get_postgres_url(), pool_size=5, max_overflow=10)

def safe_save(engine, table_name, df):
    """Smart save: align columns with existing table, drop extras, fill missing."""
    if df is None or df.empty:
        print(f"  [{table_name}] SKIP: empty")
        return 0
    try:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]

        # Get existing table columns
        existing = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 0", engine)
        table_cols = [c.lower() for c in existing.columns]

        # Drop columns not in table
        extra = [c for c in df.columns if c not in table_cols]
        if extra:
            print(f"  [{table_name}] Dropping extra cols: {extra}")
            df = df.drop(columns=extra, errors='ignore')

        # Add missing columns as None
        missing = [c for c in table_cols if c not in df.columns]
        for m in missing:
            df[m] = None

        # Reorder to match table
        df = df[table_cols]

        # Convert date columns
        for col in df.columns:
            if 'date' in col and df[col].dtype == 'object':
                df[col] = pd.to_datetime(df[col], format='%Y%m%d', errors='coerce')

        # Fill NaN in string cols
        for col in df.select_dtypes(include=["object", "string"]).columns:
            df[col] = df[col].fillna("")

        # Write
        df.to_sql(table_name, engine, if_exists="append", index=False, method="multi", chunksize=500)
        print(f"  [{table_name}] OK: {len(df)} rows")
        return len(df)
    except Exception as e:
        print(f"  [{table_name}] FAIL: {str(e)[:200]}")
        # Fallback: try one row at a time
        try:
            print(f"  [{table_name}] Trying row-by-row...")
            cnt = 0
            for _, row in df.iterrows():
                try:
                    row_df = pd.DataFrame([row])
                    row_df.to_sql(table_name, engine, if_exists="append", index=False)
                    cnt += 1
                except Exception:
                    pass
            print(f"  [{table_name}] Row-by-row: {cnt} rows")
            return cnt
        except Exception as e2:
            print(f"  [{table_name}] Row-by-row also FAIL: {str(e2)[:200]}")
            return 0

def main():
    engine = get_engine()

    # === 1. ref_suspend_d ===
    # API returns: ts_code, trade_date, suspend_timing, suspend_type
    # Table has:   ts_code, trade_date, suspend_type, suspend_reason
    print("=" * 60)
    print(">>> ref_suspend_d ...")
    ts.set_token(cfg.get_tushare_token("basic"))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL
    df = pro.suspend_d()
    if df is not None and not df.empty:
        # Map suspend_timing -> suspend_reason (API doesn't return suspend_reason separately)
        df.columns = [c.lower() for c in df.columns]
        # Rename suspend_timing to suspend_reason to match table
        if 'suspend_timing' in df.columns and 'suspend_reason' not in df.columns:
            df = df.rename(columns={'suspend_timing': 'suspend_reason'})
        safe_save(engine, "ref_suspend_d", df)
    else:
        print("  [ref_suspend_d] API returned empty")

    # === 2. ref_new_share ===
    print("=" * 60)
    print(">>> ref_new_share ...")
    df = pro.new_share()
    if df is not None and not df.empty:
        safe_save(engine, "ref_new_share", df)
    else:
        print("  [ref_new_share] API returned empty")

    # === 3. ref_stock_namechange ===
    print("=" * 60)
    print(">>> ref_stock_namechange ...")
    df = pro.namechange()
    if df is not None and not df.empty:
        safe_save(engine, "ref_stock_namechange", df)
    else:
        print("  [ref_stock_namechange] API returned empty")

    # === 4. margin_summary_daily (逐月, 去掉rqyl) ===
    print("=" * 60)
    print(">>> margin_summary_daily (两融汇总, 逐月采集) ...")
    ts.set_token(cfg.get_tushare_token("flow"))
    pro_flow = ts.pro_api(); pro_flow._DataApi__http_url = API_URL

    end_dt = datetime.now()
    start_dt = end_dt.replace(year=end_dt.year - 5)
    current = end_dt
    total = 0
    while current > start_dt:
        month_start = current.replace(day=1)
        ms = month_start.strftime("%Y%m%d")
        me = current.strftime("%Y%m%d")
        try:
            df = pro_flow.margin(start_date=ms, end_date=me)
            if df is not None and not df.empty:
                n = safe_save(engine, "margin_summary_daily", df)
                total += n
            else:
                print(f"  margin {ms}-{me}: empty")
            time.sleep(0.3)
        except Exception as e:
            print(f"  margin {ms}-{me}: API FAIL - {str(e)[:80]}")
        current = month_start - timedelta(days=1)
    print(f"  margin_summary_daily total: {total} rows")

    engine.dispose()

    # === 最终汇总 ===
    print("\n" + "=" * 60)
    print("FINAL STATUS:")
    engine2 = get_engine()
    tables = [
        "ref_suspend_d", "ref_new_share", "ref_stock_namechange",
        "etf_daily", "index_daily", "ref_trade_cal",
        "fund_daily", "fund_nav", "margin_summary_daily", "stock_auction_daily"
    ]
    for t in tables:
        try:
            df = pd.read_sql(f"SELECT count(*) as cnt FROM {t}", engine2)
            cnt = df["cnt"].iloc[0]
            status = "OK" if cnt > 0 else "EMPTY"
            print(f"  {t:30s}: {cnt:>8d}  [{status}]")
        except Exception as e:
            print(f"  {t:30s}: ERROR - {str(e)[:60]}")
    engine2.dispose()

if __name__ == "__main__":
    main()
