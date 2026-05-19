"""修复采集：写入之前 API 正常但未能入库的 5 张表"""
import os, sys
os.chdir(r'E:\demo\backtrader')
sys.path.insert(0, '.')
sys.path.insert(0, 'data_layer')

import tushare as ts
from data_layer.config import config as cfg
from sqlalchemy import create_engine
import pandas as pd
from datetime import datetime
from pathlib import Path

TABLE_DIR = Path("E:/demo/backtrader/data_layer/collectors/collected")
TABLE_DIR.mkdir(parents=True, exist_ok=True)

def get_engine():
    url = cfg.get_postgres_url()
    return create_engine(url, pool_size=5, max_overflow=10)

def save_df(engine, table_name, df):
    if df is None or df.empty:
        print(f"  [{table_name}] SKIP: empty")
        return 0
    try:
        df = df.copy()
        df = df.loc[:, ~df.columns.duplicated()]
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].fillna("")
        # Also save CSV backup
        csv_path = TABLE_DIR / f"{table_name}.csv"
        df.to_csv(csv_path, index=False)
        # Write to PG
        df.to_sql(table_name, engine, if_exists="append", index=False, method="multi", chunksize=500)
        print(f"  [{table_name}] OK: {len(df)} rows written")
        return len(df)
    except Exception as e:
        print(f"  [{table_name}] FAIL: {e}")
        return 0

def main():
    engine = get_engine()
    API_URL = cfg.tushare_api_url

    # === 1. ref_suspend_d ===
    print(">>> ref_suspend_d ...")
    ts.set_token(cfg.get_tushare_token("basic"))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL
    df = pro.suspend_d()
    if df is not None and not df.empty:
        df.columns = [c.lower() for c in df.columns]
        save_df(engine, "ref_suspend_d", df)

    # === 2. ref_new_share ===
    print(">>> ref_new_share ...")
    df = pro.new_share()
    if df is not None and not df.empty:
        df.columns = [c.lower() for c in df.columns]
        save_df(engine, "ref_new_share", df)

    # === 3. ref_stock_namechange ===
    print(">>> ref_stock_namechange ...")
    df = pro.namechange()
    if df is not None and not df.empty:
        df.columns = [c.lower() for c in df.columns]
        save_df(engine, "ref_stock_namechange", df)

    # === 4. margin_summary_daily (两融汇总) ===
    print(">>> margin_summary_daily (两融汇总) ...")
    import time
    ts.set_token(cfg.get_tushare_token("flow"))
    pro_flow = ts.pro_api(); pro_flow._DataApi__http_url = API_URL

    # 逐月拉5年数据
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
                df.columns = [c.lower() for c in df.columns]
                n = save_df(engine, "margin_summary_daily", df)
                total += n
            time.sleep(0.3)
        except Exception as e:
            print(f"  margin {ms} failed: {e}")
        current = month_start - pd.Timedelta(days=1)
    print(f"  margin_summary_daily total: {total} rows")

    engine.dispose()

    # === Final check ===
    print()
    print("=" * 60)
    print("FINAL STATUS:")
    engine2 = get_engine()
    tables = ["ref_suspend_d", "ref_new_share", "ref_stock_namechange",
              "etf_daily", "index_daily", "ref_trade_cal",
              "fund_daily", "fund_nav", "margin_summary_daily", "stock_auction_daily"]
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
