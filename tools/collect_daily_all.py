"""
全量股票日线 + 大盘指数行情采集器 v1.0
===========================================
用途：每个工作日自动采集全市场 5000+ 股票的当日日线数据 + 核心指数行情
运行位置: E:\demo\backtrader 目录下
触发方式: 工作日上午自动运行 (由 OpenClaw cron 调度)

功能：
  1. 判断今日是否为交易日（从 ref_trade_cal 表查询）
  2. 从 ref_stock_basic 获取所有上市股票列表
  3. 使用 Tushare daily 接口批量拉取当日全量日线数据
  4. 使用 Tushare index_daily 接口拉取核心指数日线数据
  5. 数据写入 stock_daily / index_daily 表（ON CONFLICT 跳过重复）
  6. 同步更新 index_daily 中最近一个月的指数行情（确保前端大盘可见）

用法:
    python tools/collect_daily_all.py                    # 采集今天
    python tools/collect_daily_all.py --date 20260519    # 采集指定日期
    python tools/collect_daily_all.py --dry-run          # 试运行（不写库）
    python tools/collect_daily_all.py --indices-only     # 只采集指数
    python tools/collect_daily_all.py --stocks-only      # 只采集个股

注意：
  - 需要 .env 中配置 TUSHARE_TOKEN_BASIC 和 TUSHARE_TOKEN_INDEX
  - 全量日线拉取约消耗 5000 积分/次（basic token）
  - 建议在工作日 15:30 之后运行（确保当天收盘数据已生成）
"""

import sys
import os
import logging
import argparse
from datetime import datetime, timedelta
import time
import pandas as pd

# 确保 backtrader 项目在 import 路径中
BACKTRADER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKTRADER_ROOT)
sys.path.insert(0, os.path.join(BACKTRADER_ROOT, "data_layer"))
os.chdir(BACKTRADER_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("collect_daily")


def get_engine():
    """获取共享的 PostgreSQL 引擎"""
    from sqlalchemy import create_engine
    from data_layer.config import config as cfg
    url = cfg.get_postgres_url()
    return create_engine(url, pool_size=10, max_overflow=20)


def is_trade_day(engine, date_str: str) -> bool:
    """判断指定日期是否为交易日"""
    try:
        # 先检查 ref_trade_cal
        sql = """
        SELECT is_open FROM ref_trade_cal
        WHERE cal_date = :date AND exchange = 'SSE'
        LIMIT 1
        """
        df = pd.read_sql(sql, engine, params={"date": date_str})
        if not df.empty:
            return int(df["is_open"].iloc[0]) == 1
        # fallback: 周六日不是交易日
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.weekday() < 5
    except Exception:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.weekday() < 5


def save_df(engine, table_name, df):
    """保存 DataFrame 到 PostgreSQL，使用 ON CONFLICT 跳过重复"""
    if df is None or df.empty:
        return 0
    try:
        df = df.copy()
        # 去除可能的重复列
        df = df.loc[:, ~df.columns.duplicated()]
        # 统一字符串列中的 NaN
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].fillna("")
        # 确保 trade_date 格式正确
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date

        df.to_sql(table_name, engine, if_exists="append",
                  index=False, method="multi", chunksize=1000)
        return len(df)
    except Exception as e:
        logger.warning(f"写入 {table_name} 可能部分重复，尝试逐批插入: {e}")
        # 如果批量插入失败（主键冲突），逐条插入跳过重复
        inserted = 0
        for _, row in df.iterrows():
            try:
                row_df = pd.DataFrame([row])
                row_df.to_sql(table_name, engine, if_exists="append",
                              index=False, method=None)
                inserted += 1
            except Exception:
                pass  # 主键冲突，跳过
        return inserted


def get_stock_list(engine):
    """获取所有上市股票列表"""
    try:
        sql = "SELECT ts_code, symbol, name FROM ref_stock_basic WHERE list_status = 'L' ORDER BY ts_code"
        df = pd.read_sql(sql, engine)
        logger.info(f"获取到 {len(df)} 只上市股票")
        return df
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return pd.DataFrame()


def collect_stocks_daily(engine, trade_date: str, dry_run: bool = False):
    """
    采集全量个股日线数据
    使用 Tushare daily 接口按 trade_date 批量拉取
    """
    logger.info("=" * 60)
    logger.info(f"📈 采集全量个股日线: {trade_date}")

    import tushare as ts
    from data_layer.config import config as cfg

    token = cfg.get_tushare_token("basic")
    if not token:
        logger.error("❌ TUSHARE_TOKEN_BASIC 未配置，跳过个股日线采集")
        return 0

    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = cfg.tushare_api_url

    if dry_run:
        logger.info("[DRY RUN] 尝试调用 daily 接口...")
        try:
            df = pro.daily(trade_date=trade_date)
            if df is not None:
                logger.info(f"[DRY RUN] daily 接口可获取 {len(df)} 条数据，跳过写入")
            else:
                logger.warning(f"[DRY RUN] daily 接口返回 None (可能当日数据尚未生成)")
        except Exception as e:
            logger.error(f"[DRY RUN] daily 接口调用失败: {e}")
        return 0

    # 实际采集：拉取全量日线
    total = 0
    retries = 3
    for attempt in range(retries):
        try:
            logger.info(f"  调用 daily(trade_date={trade_date}) 第 {attempt+1}/{retries} 次 ...")
            df = pro.daily(trade_date=trade_date)
            if df is not None and not df.empty:
                df.columns = [c.lower() for c in df.columns]
                logger.info(f"  获取到 {len(df)} 条日线数据")
                n = save_df(engine, "stock_daily", df)
                total = n
                logger.info(f"  ✅ stock_daily 写入 {n} 行 (共 {len(df)} 条来自 Tushare)")
                break
            else:
                logger.warning(f"  daily 接口返回空数据 (尝试 {attempt+1}/{retries})")
                if attempt < retries - 1:
                    time.sleep(5)
        except Exception as e:
            logger.error(f"  daily 接口调用失败 (尝试 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(10)

    return total


def collect_index_daily(engine, trade_date: str, dry_run: bool = False):
    """
    采集核心指数日线数据（最近30个交易日，确保大盘K线可见）
    """
    logger.info("=" * 60)
    logger.info(f"📊 采集核心指数日线: {trade_date} (含近30日数据)")

    import tushare as ts
    from data_layer.config import config as cfg

    token = cfg.get_tushare_token("index")
    if not token:
        logger.error("❌ TUSHARE_TOKEN_INDEX 未配置，跳过指数日线采集")
        return 0

    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = cfg.tushare_api_url

    # 核心指数列表
    core_indexes = [
        "000001.SH", "000016.SH", "000300.SH", "000688.SH",
        "000905.SH", "000852.SH", "399001.SZ", "399006.SZ",
        "399005.SZ", "399673.SZ",
    ]

    if dry_run:
        logger.info("[DRY RUN] 尝试调用指数数据接口...")
        for idx_code in core_indexes[:2]:  # 只试2个
            try:
                df = pro.index_daily(ts_code=idx_code, trade_date=trade_date)
                if df is not None and not df.empty:
                    logger.info(f"[DRY RUN] {idx_code}: 可获取 {len(df)} 条数据")
                else:
                    logger.warning(f"[DRY RUN] {idx_code}: 返回空数据")
            except Exception as e:
                logger.error(f"[DRY RUN] {idx_code}: 接口调用失败: {e}")
        return 0

    # 计算30天范围
    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=60)  # 取60天覆盖周末节假日
    start_date = start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")

    total = 0
    for idx_code in core_indexes:
        try:
            logger.info(f"  拉取指数日线: {idx_code} ({start_date} ~ {end_date})")
            df = pro.index_daily(
                ts_code=idx_code,
                start_date=start_date,
                end_date=end_date
            )
            if df is not None and not df.empty:
                df.columns = [c.lower() for c in df.columns]
                n = save_df(engine, "index_daily", df)
                total += n
                logger.info(f"    ✅ {idx_code}: 写入 {n} 行")
                time.sleep(0.5)  # API 限流
            else:
                logger.warning(f"    ⚠️ {idx_code}: 返回空数据")
        except Exception as e:
            logger.error(f"    ❌ {idx_code}: 采集失败: {e}")
            time.sleep(0.5)

    logger.info(f"  指数日线总计: {total} 行")
    return total


def verify_collection(engine, trade_date: str):
    """验证采集结果"""
    logger.info("=" * 60)
    logger.info("📋 验证采集结果")

    # 检查 stock_daily 当日数据
    try:
        sql_stock = "SELECT count(*) as cnt FROM stock_daily WHERE trade_date = :date"
        df_stock = pd.read_sql(sql_stock, engine, params={"date": trade_date})
        stock_cnt = df_stock["cnt"].iloc[0]
        logger.info(f"  stock_daily ({trade_date}): {stock_cnt} 条")
    except Exception as e:
        logger.error(f"  查询 stock_daily 失败: {e}")
        stock_cnt = 0

    # 检查 index_daily 当日数据
    try:
        sql_idx = "SELECT count(*) as cnt FROM index_daily WHERE trade_date = :date"
        df_idx = pd.read_sql(sql_idx, engine, params={"date": trade_date})
        idx_cnt = df_idx["cnt"].iloc[0]
        logger.info(f"  index_daily ({trade_date}): {idx_cnt} 条")

        # 列出具体指数
        sql_detail = """
        SELECT ts_code, trade_date, close, pct_chg
        FROM index_daily WHERE trade_date = :date ORDER BY ts_code
        """
        df_detail = pd.read_sql(sql_detail, engine, params={"date": trade_date})
        if not df_detail.empty:
            logger.info("  --- 指数详情 ---")
            for _, row in df_detail.iterrows():
                logger.info(f"    {row['ts_code']}: close={row['close']}, chg={row['pct_chg']}%")
    except Exception as e:
        logger.error(f"  查询 index_daily 失败: {e}")
        idx_cnt = 0

    # 统计汇总
    logger.info(f"  总计: 个股 {stock_cnt} 条 + 指数 {idx_cnt} 条")
    return stock_cnt, idx_cnt


def main():
    parser = argparse.ArgumentParser(description="全量股票日线 + 大盘指数采集")
    parser.add_argument("--date", default=None, help="采集日期 (YYYYMMDD)，默认今天")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式，不写入数据库")
    parser.add_argument("--indices-only", action="store_true", help="只采集指数")
    parser.add_argument("--stocks-only", action="store_true", help="只采集个股")
    parser.add_argument("--skip-trade-day-check", action="store_true", help="跳过交易日检查")
    args = parser.parse_args()

    # 确定采集日期
    if args.date:
        trade_date = args.date
    else:
        # 如果当前时间在15:30之前，采集上一个交易日
        now = datetime.now()
        if now.hour < 15 or (now.hour == 15 and now.minute < 30):
            # 盘中，默认采集昨天
            trade_date = (now - timedelta(days=1)).strftime("%Y%m%d")
            logger.info(f"当前时间早于 15:30，默认采集上一个交易日: {trade_date}")
        else:
            trade_date = now.strftime("%Y%m%d")

    logger.info("=" * 60)
    logger.info(f"🚀 全量日线采集启动: {trade_date}")
    logger.info("=" * 60)

    engine = get_engine()
    if not engine:
        logger.error("❌ 无法连接 PostgreSQL，退出")
        return

    # 检查交易日
    if not args.skip_trade_day_check and not is_trade_day(engine, trade_date):
        logger.warning(f"⚠️ {trade_date} 非交易日，跳过采集")
        engine.dispose()
        return

    logger.info(f"✅ {trade_date} 是交易日，开始采集")

    total_stocks = 0
    total_indices = 0

    # 采集个股日线
    if not args.indices_only:
        total_stocks = collect_stocks_daily(engine, trade_date, dry_run=args.dry_run)

    # 采集指数日线
    if not args.stocks_only:
        total_indices = collect_index_daily(engine, trade_date, dry_run=args.dry_run)

    # 验证结果
    if not args.dry_run:
        verify_collection(engine, trade_date)

    engine.dispose()
    logger.info("=" * 60)
    logger.info(f"✅ 采集完成: 个股 {total_stocks} 行, 指数 {total_indices} 行")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
