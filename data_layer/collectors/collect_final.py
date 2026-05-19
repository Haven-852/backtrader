"""攻克剩余 table + 扩充 etf_daily"""
import os, sys
os.chdir(r'E:\demo\backtrader')
sys.path.insert(0, '.'); sys.path.insert(0, 'data_layer')

import tushare as ts
from data_layer.config import config as cfg
from sqlalchemy import create_engine
import pandas as pd
from datetime import datetime, timedelta
import time

API_URL = cfg.tushare_api_url

def get_engine():
    return create_engine(cfg.get_postgres_url(), pool_size=5, max_overflow=10)

def safe_save(engine, table_name, df):
    if df is None or df.empty:
        return 0
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    existing = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 0", engine)
    table_cols = [c.lower() for c in existing.columns]
    extra = [c for c in df.columns if c not in table_cols]
    if extra:
        df = df.drop(columns=extra, errors='ignore')
    missing = [c for c in table_cols if c not in df.columns]
    for m in missing:
        df[m] = None
    df = df[table_cols]
    for col in df.columns:
        if 'date' in col and df[col].dtype == 'object':
            df[col] = pd.to_datetime(df[col], format='%Y%m%d', errors='coerce')
    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = df[col].fillna("")
    try:
        df.to_sql(table_name, engine, if_exists="append", index=False, method="multi", chunksize=500)
    except Exception:
        # row by row fallback
        cnt = 0
        for _, row in df.iterrows():
            try:
                pd.DataFrame([row]).to_sql(table_name, engine, if_exists="append", index=False)
                cnt += 1
            except Exception:
                pass
        return cnt
    return len(df)


def get_etf_list(pro_fund):
    """获取 ETF 基金列表"""
    df = pro_fund.fund_basic(market="E")
    if df is None or df.empty:
        return []
    return df["ts_code"].tolist()


def collect_etf_daily(engine):
    """逐只采集 ETF 日线数据"""
    print("=" * 60)
    print(">>> etf_daily: 逐只 ETF 采集 ...")

    ts.set_token(cfg.get_tushare_token("fund"))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL

    etf_codes = get_etf_list(pro)
    print(f"  获取到 {len(etf_codes)} 只 ETF")

    # 获取已采集的 ts_code 避免重复
    try:
        existing = pd.read_sql("SELECT DISTINCT ts_code FROM etf_daily", engine)
        existing_codes = set(existing["ts_code"].tolist()) if not existing.empty else set()
    except Exception:
        existing_codes = set()

    to_collect = [c for c in etf_codes if c not in existing_codes]
    print(f"  已有: {len(existing_codes)}, 待采集: {len(to_collect)}")

    end_date = datetime.now().strftime("%Y%m%d")
    total = 0
    for i, code in enumerate(to_collect):
        try:
            df = pro.fund_daily(ts_code=code, start_date="20200101", end_date=end_date)
            if df is not None and not df.empty:
                n = safe_save(engine, "etf_daily", df)
                total += n
            time.sleep(0.35)
        except Exception as e:
            err = str(e)[:60]
            if "校验" in err:
                print(f"  [{i+1}/{len(to_collect)}] {code}: API不支持fund_daily")
                break
            print(f"  [{i+1}/{len(to_collect)}] {code}: FAIL - {err}")

        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(to_collect)}, total rows: {total}")

    print(f"  etf_daily total new: {total} rows")
    return total


def collect_fund_daily(engine):
    """逐只采集基金日线 (fund_daily 表)"""
    print("=" * 60)
    print(">>> fund_daily: 逐只基金采集 ...")

    ts.set_token(cfg.get_tushare_token("fund"))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL

    # 获取基金列表 (含开放式基金)
    all_codes = []
    for market in ["E", "O"]:  # ETF + 开放式
        try:
            df = pro.fund_basic(market=market)
            if df is not None and not df.empty:
                codes = df["ts_code"].tolist()
                all_codes.extend(codes)
                print(f"  fund_basic market={market}: {len(codes)} 只")
        except Exception as e:
            print(f"  fund_basic market={market}: FAIL - {str(e)[:60]}")

    # 取前200只尝试
    to_collect = all_codes[:200]
    print(f"  尝试采集 {len(to_collect)} 只基金日线")

    end_date = datetime.now().strftime("%Y%m%d")
    total = 0
    success = 0
    for i, code in enumerate(to_collect):
        try:
            df = pro.fund_daily(ts_code=code, start_date="20200101", end_date=end_date)
            if df is not None and not df.empty:
                n = safe_save(engine, "fund_daily", df)
                total += n
                success += 1
            time.sleep(0.35)
        except Exception as e:
            err = str(e)[:80]
            if "校验" in err:
                print(f"  [{i+1}] {code}: API不支持fund_daily单code查询")
                break
            # silently skip other errors
        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(to_collect)}, success: {success}, rows: {total}")

    print(f"  fund_daily total: {total} rows from {success} funds")
    return total


def collect_fund_nav(engine):
    """逐只采集基金净值"""
    print("=" * 60)
    print(">>> fund_nav: 逐只基金净值采集 ...")

    ts.set_token(cfg.get_tushare_token("fund"))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL

    all_codes = []
    for market in ["E", "O"]:
        try:
            df = pro.fund_basic(market=market)
            if df is not None and not df.empty:
                codes = df["ts_code"].tolist()
                all_codes.extend(codes)
        except Exception:
            pass

    to_collect = all_codes[:100]
    print(f"  尝试采集 {len(to_collect)} 只基金净值")

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=365*3)).strftime("%Y%m%d")
    total = 0
    for i, code in enumerate(to_collect):
        try:
            df = pro.fund_nav(ts_code=code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                n = safe_save(engine, "fund_nav", df)
                total += n
            time.sleep(0.35)
        except Exception as e:
            err = str(e)[:80]
            if "校验" in err:
                print(f"  [{i+1}] {code}: API不支持fund_nav单code查询")
                break
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(to_collect)}, rows: {total}")

    print(f"  fund_nav total: {total} rows")
    return total


def test_stk_mins(engine):
    """测试集合竞价 API"""
    print("=" * 60)
    print(">>> stock_auction_daily: 测试 stk_mins 接口 ...")

    ts.set_token(cfg.get_tushare_token("mins"))
    pro = ts.pro_api(); pro._DataApi__http_url = API_URL

    # 试几种不同的参数组合
    tests = [
        ("000001.SZ", "20260515"),
        ("000001.SZ", "20260514"),
        ("600000.SH", "20260515"),
    ]
    for ts_code, td in tests:
        try:
            df = pro.stk_mins(ts_code=ts_code, freq="auction", trade_date=td)
            if df is not None and not df.empty:
                print(f"  {ts_code} {td}: OK! {len(df)} rows, cols: {list(df.columns)[:5]}")
                return True
            else:
                print(f"  {ts_code} {td}: empty response")
        except Exception as e:
            print(f"  {ts_code} {td}: FAIL - {str(e)[:100]}")

    # Try without auction freq
    print("  Trying stk_mins without auction freq...")
    try:
        df = pro.stk_mins(ts_code="000001.SZ", freq="1min", trade_date="20260515")
        if df is not None and not df.empty:
            print(f"  stk_mins 1min: OK! {len(df)} rows")
        else:
            print(f"  stk_mins 1min: empty")
    except Exception as e:
        print(f"  stk_mins 1min: FAIL - {str(e)[:100]}")

    return False


def main():
    engine = get_engine()

    # 1. 扩充 etf_daily
    collect_etf_daily(engine)

    # 2. fund_daily
    collect_fund_daily(engine)

    # 3. fund_nav
    collect_fund_nav(engine)

    # 4. stock_auction_daily 测试
    test_stk_mins(engine)

    engine.dispose()

    # 最终汇总
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
