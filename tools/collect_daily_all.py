"""
全市场行情与参照数据采集调度器 v2.0
====================================
用途：按「日 / 周 / 月 / 季」频率从 Tushare 拉取数据写入 PostgreSQL（Timescale 表）。

---------------------------------------------------------------------------
一、表与采集频率（与当前 public schema 对齐）
---------------------------------------------------------------------------

【每个交易日】（收盘后建议 15:30 之后跑）
  - stock_daily              全市场日线（pro.daily）
  - stock_daily_basic        每日指标（pro.daily_basic）
  - stock_adj_factor         复权因子（pro.adj_factor）
  - stock_moneyflow_daily    资金流向（pro.moneyflow）
  - stock_top_list_daily     龙虎榜（pro.top_list）
  - stock_block_trade        大宗（pro.block_trade）
  - margin_summary_daily     两融汇总（pro.margin × SSE/SZSE）
  - margin_detail_daily      两融明细（pro.margin_detail）
  - ref_stk_limit_daily      涨跌停价（pro.stk_limit）
  - ref_suspend_d            停复牌（pro.suspend_d）
  - index_daily              核心指数日线（pro.index_daily）
  - etf_daily                ETF 日线（pro.etf_daily，需 fund 权限）
  - fund_daily               场内基金日线（pro.fund_daily）
  - fund_nav                 基金净值（pro.fund_nav，nav_date=当日）
  - bars_daily               若与日线同源，可由 stock_daily 派生/另管道写入；本脚本不单独写 bars_daily

【每周一次】（建议周一盘后，与 --schedule weekly 或 --with-weekly 联用）
  - stock_weekly             周线（pro.weekly；若接口不支持单日全市场则退化为分批 ts_code）
  - ref_stock_basic          证券主档刷新（pro.stock_basic）
  - ref_stock_namechange     更名（pro.namechange）
  - ref_new_share            新股（pro.new_share）
  - broker_report_consensus  研报一致预期（若有接口与 Token；否则跳过）

【每月一次】（建议每月最后一个交易日，--schedule monthly）
  - stock_monthly            月线（pro.monthly）
  - index_weight             指数成分权重（pro.index_weight，按核心指数 + 指定 trade_date）

【每季度 / 财报披露期】（季报密集披露后，--schedule quarterly）
  - fin_income / fin_balancesheet / fin_cashflow / fin_fina_indicator
  - fin_forecast / fin_express / fin_dividend
  说明：财报接口多为「按 ts_code + 报告期」拉取，全市场一次性调用成本极高；
        本脚本在季度任务中仅拉取「公告日期 = 当前采集日」的快报/预告（bulk by ann_date），
        完整三大表回补请使用 data_layer/collectors/pull_5year_data.py 等离线任务。

---------------------------------------------------------------------------
用法
---------------------------------------------------------------------------
    python tools/collect_daily_all.py                         # 默认：仅「交易日全量日频包」
    python tools/collect_daily_all.py --date 20260115
    python tools/collect_daily_all.py --dry-run
    python tools/collect_daily_all.py --schedule weekly       # 仅周任务
    python tools/collect_daily_all.py --schedule monthly
    python tools/collect_daily_all.py --schedule quarterly
    python tools/collect_daily_all.py --schedule full         # 日+周+月+季（慎用 API 额度）
    python tools/collect_daily_all.py --with-weekly           # 在默认日频后追加周任务
    python tools/collect_daily_all.py --indices-only
    python tools/collect_daily_all.py --stocks-only           # 仅 stock_daily，不跑扩展日频

环境变量（.env）：
  - TUSHARE_TOKEN_BASIC / TUSHARE_TOKEN / TUSHARE_TOKEN_FLOW / TUSHARE_TOKEN_INDEX
  - TUSHARE_TOKEN_FUND / TUSHARE_TOKEN_FINANCIAL（按需）
"""

import sys
import os
import logging
import argparse
from datetime import datetime, timedelta
import time
import pandas as pd

BACKTRADER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKTRADER_ROOT)
sys.path.insert(0, os.path.join(BACKTRADER_ROOT, "data_layer"))
os.chdir(BACKTRADER_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("collect_daily")


def get_engine():
    from sqlalchemy import create_engine
    from data_layer.config import config as cfg

    url = cfg.get_postgres_url()
    return create_engine(url, pool_size=10, max_overflow=20)


def is_trade_day(engine, date_str: str) -> bool:
    try:
        sql = """
        SELECT is_open FROM ref_trade_cal
        WHERE cal_date = :date AND exchange = 'SSE'
        LIMIT 1
        """
        df = pd.read_sql(sql, engine, params={"date": date_str})
        if not df.empty:
            return int(df["is_open"].iloc[0]) == 1
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.weekday() < 5
    except Exception:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return dt.weekday() < 5


def is_last_open_day_of_month(engine, date_str: str) -> bool:
    """date_str(YYYYMMDD) 是否为当月最后一个「开市日」"""
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
        ym = d.strftime("%Y-%m")
        sql = """
        SELECT MAX(cal_date) AS mx
        FROM ref_trade_cal
        WHERE exchange = 'SSE' AND is_open = 1 AND to_char(cal_date, 'YYYY-MM') = :ym
        """
        df = pd.read_sql(sql, engine, params={"ym": ym})
        if df.empty or pd.isna(df["mx"].iloc[0]):
            return False
        mx = pd.to_datetime(df["mx"].iloc[0]).strftime("%Y%m%d")
        return mx == date_str
    except Exception as e:
        logger.warning(f"判断月末交易日失败: {e}")
        return False


def save_df(engine, table_name, df):
    if df is None or df.empty:
        return 0
    try:
        df = df.copy()
        df = df.loc[:, ~df.columns.duplicated()]
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].fillna("")
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        if "nav_date" in df.columns:
            df["nav_date"] = pd.to_datetime(df["nav_date"]).dt.date
        if "ann_date" in df.columns:
            df["ann_date"] = pd.to_datetime(df["ann_date"]).dt.date
        if "end_date" in df.columns:
            df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
        df.to_sql(
            table_name, engine, if_exists="append", index=False, method="multi", chunksize=1000
        )
        return len(df)
    except Exception as e:
        logger.warning(f"写入 {table_name} 可能部分重复或列不匹配，尝试逐行: {e}")
        inserted = 0
        for _, row in df.iterrows():
            try:
                pd.DataFrame([row]).to_sql(
                    table_name, engine, if_exists="append", index=False, method=None
                )
                inserted += 1
            except Exception:
                pass
        return inserted


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    return df


def _prepare_suspend(df: pd.DataFrame) -> pd.DataFrame:
    df = _norm_cols(df)
    if "suspend_timing" in df.columns and "suspend_reason" not in df.columns:
        df = df.rename(columns={"suspend_timing": "suspend_reason"})
    return df


def get_tushare_pro(token_group: str):
    import tushare as ts
    from data_layer.config import config as cfg

    token = cfg.get_tushare_token(token_group) or cfg.get_tushare_token("default")
    if not token:
        return None, None
    ts.set_token(token)
    pro = ts.pro_api()
    pro._DataApi__http_url = cfg.tushare_api_url
    return cfg, pro


def get_stock_list(engine):
    try:
        sql = (
            "SELECT ts_code, symbol, name FROM ref_stock_basic "
            "WHERE list_status = 'L' ORDER BY ts_code"
        )
        df = pd.read_sql(sql, engine)
        logger.info(f"获取到 {len(df)} 只上市股票")
        return df
    except Exception as e:
        logger.error(f"获取股票列表失败: {e}")
        return pd.DataFrame()


def collect_stocks_daily(engine, trade_date: str, dry_run: bool = False) -> int:
    logger.info("=" * 60)
    logger.info(f"📈 [日] 全市场 stock_daily: {trade_date}")
    pair = get_tushare_pro("basic")
    if pair[1] is None:
        logger.error("❌ TUSHARE_TOKEN_BASIC / default 未配置，跳过 stock_daily")
        return 0
    _, pro = pair

    if dry_run:
        try:
            df = pro.daily(trade_date=trade_date)
            logger.info(f"[DRY RUN] daily 返回 {len(df) if df is not None else 0} 行")
        except Exception as e:
            logger.error(f"[DRY RUN] daily 失败: {e}")
        return 0

    total = 0
    for attempt in range(3):
        try:
            df = pro.daily(trade_date=trade_date)
            if df is not None and not df.empty:
                df = _norm_cols(df)
                total = save_df(engine, "stock_daily", df)
                logger.info(f"  ✅ stock_daily 写入 {total} 行")
                break
            logger.warning(f"  daily 空数据 (尝试 {attempt + 1}/3)")
            time.sleep(5)
        except Exception as e:
            logger.error(f"  daily 失败 (尝试 {attempt + 1}/3): {e}")
            time.sleep(10)
    return total


def _one_shot_by_trade_date(
    pro,
    fn_name: str,
    trade_date: str,
    engine,
    table: str,
    dry_run: bool,
    extra_kw=None,
    post=None,
) -> int:
    """调用 pro.<fn>(trade_date=...) 一类全市场单日接口并入库。"""
    extra_kw = extra_kw or {}
    if dry_run:
        try:
            fn = getattr(pro, fn_name)
            df = fn(trade_date=trade_date, **extra_kw)
            n = len(df) if df is not None else 0
            logger.info(f"[DRY RUN] {fn_name} -> {n} 行 -> {table}")
        except Exception as e:
            logger.warning(f"[DRY RUN] {fn_name} 不可用: {e}")
        return 0
    try:
        fn = getattr(pro, fn_name)
        df = fn(trade_date=trade_date, **extra_kw)
        if df is None or df.empty:
            logger.warning(f"  {fn_name}({trade_date}) 返回空 -> {table}")
            return 0
        df = _norm_cols(df)
        if post:
            df = post(df)
        n = save_df(engine, table, df)
        logger.info(f"  ✅ {table} <- {fn_name}: {n} 行")
        return n
    except Exception as e:
        logger.warning(f"  ⚠️ {fn_name} -> {table} 失败: {e}")
        return 0


def collect_daily_market_extensions(engine, trade_date: str, dry_run: bool) -> dict:
    """
    交易日全市场「日频」扩展：daily_basic / adj / moneyflow / top_list / block /
    margin / stk_limit / suspend；基金/ETF 在 token 具备时拉取。
    """
    stats = {}
    pair = get_tushare_pro("basic")
    if pair[1] is None:
        logger.error("❌ basic token 缺失，跳过日频扩展")
        return stats
    _, pro = pair

    logger.info("=" * 60)
    logger.info(f"📌 [日] 全市场扩展行情/资金/两融/涨跌停/停牌: {trade_date}")

    stats["stock_daily_basic"] = _one_shot_by_trade_date(
        pro, "daily_basic", trade_date, engine, "stock_daily_basic", dry_run
    )
    time.sleep(0.35)
    stats["stock_adj_factor"] = _one_shot_by_trade_date(
        pro, "adj_factor", trade_date, engine, "stock_adj_factor", dry_run
    )
    time.sleep(0.35)
    stats["ref_stk_limit_daily"] = _one_shot_by_trade_date(
        pro, "stk_limit", trade_date, engine, "ref_stk_limit_daily", dry_run
    )
    time.sleep(0.35)
    stats["ref_suspend_d"] = _one_shot_by_trade_date(
        pro,
        "suspend_d",
        trade_date,
        engine,
        "ref_suspend_d",
        dry_run,
        post=_prepare_suspend,
    )
    time.sleep(0.35)

    flow_pair = get_tushare_pro("flow")
    if flow_pair[1] is not None:
        _, prof = flow_pair
        stats["stock_moneyflow_daily"] = _one_shot_by_trade_date(
            prof, "moneyflow", trade_date, engine, "stock_moneyflow_daily", dry_run
        )
        time.sleep(0.35)
        stats["stock_top_list_daily"] = _one_shot_by_trade_date(
            prof, "top_list", trade_date, engine, "stock_top_list_daily", dry_run
        )
        time.sleep(0.35)
        stats["stock_block_trade"] = _one_shot_by_trade_date(
            prof, "block_trade", trade_date, engine, "stock_block_trade", dry_run
        )
        time.sleep(0.35)

        if not dry_run:
            msum = 0
            for ex in ("SSE", "SZSE"):
                try:
                    df = prof.margin(trade_date=trade_date, exchange_id=ex)
                    if df is not None and not df.empty:
                        df = _norm_cols(df)
                        msum += save_df(engine, "margin_summary_daily", df)
                except Exception as e:
                    logger.warning(f"  margin {ex}: {e}")
                time.sleep(0.3)
            stats["margin_summary_daily"] = msum
            stats["margin_detail_daily"] = _one_shot_by_trade_date(
                prof, "margin_detail", trade_date, engine, "margin_detail_daily", dry_run
            )
    else:
        logger.warning("⚠️ TUSHARE_TOKEN_FLOW 未配置，跳过 moneyflow/top_list/block/margin")

    fund_pair = get_tushare_pro("fund")
    if fund_pair[1] is not None:
        _, prof = fund_pair
        stats["etf_daily"] = _one_shot_by_trade_date(
            prof, "etf_daily", trade_date, engine, "etf_daily", dry_run
        )
        time.sleep(0.35)
        stats["fund_daily"] = _one_shot_by_trade_date(
            prof, "fund_daily", trade_date, engine, "fund_daily", dry_run
        )
        time.sleep(0.35)
        if not dry_run:
            try:
                df = prof.fund_nav(nav_date=trade_date)
                if df is not None and not df.empty:
                    df = _norm_cols(df)
                    stats["fund_nav"] = save_df(engine, "fund_nav", df)
                    logger.info(f"  ✅ fund_nav: {stats['fund_nav']} 行")
                else:
                    stats["fund_nav"] = 0
            except Exception as e:
                logger.warning(f"  fund_nav: {e}")
                stats["fund_nav"] = 0
    else:
        logger.info("（可选）TUSHARE_TOKEN_FUND 未配置，跳过 etf_daily/fund_daily/fund_nav")

    return stats


def collect_index_daily(engine, trade_date: str, dry_run: bool = False) -> int:
    logger.info("=" * 60)
    logger.info(f"📊 [日] 核心指数 index_daily: {trade_date}")
    pair = get_tushare_pro("index")
    if pair[1] is None:
        pair = get_tushare_pro("basic")
    if pair[1] is None:
        logger.error("❌ index/basic token 均缺失，跳过指数")
        return 0
    _, pro = pair

    core_indexes = [
        "000001.SH",
        "000016.SH",
        "000300.SH",
        "000688.SH",
        "000905.SH",
        "000852.SH",
        "399001.SZ",
        "399006.SZ",
        "399005.SZ",
        "399673.SZ",
    ]

    if dry_run:
        logger.info("[DRY RUN] 跳过指数写入")
        return 0

    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=60)
    start_date = start_dt.strftime("%Y%m%d")
    end_date = end_dt.strftime("%Y%m%d")
    total = 0
    for idx_code in core_indexes:
        try:
            df = pro.index_daily(ts_code=idx_code, start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                df = _norm_cols(df)
                total += save_df(engine, "index_daily", df)
            time.sleep(0.45)
        except Exception as e:
            logger.error(f"  {idx_code}: {e}")
    logger.info(f"  指数共写入约 {total} 行")
    return total


def collect_weekly_reference_and_kline(engine, trade_date: str, dry_run: bool) -> None:
    """每周：主档/更名/新股 + 尝试全市场周线。"""
    logger.info("=" * 60)
    logger.info(f"📅 [周] ref_stock_basic / namechange / new_share + stock_weekly: {trade_date}")
    pair = get_tushare_pro("basic")
    if pair[1] is None:
        return
    _, pro = pair

    def pull_save(api, tbl, **kw):
        if dry_run:
            logger.info(f"[DRY RUN] {api}")
            return
        try:
            fn = getattr(pro, api)
            df = fn(**kw)
            if df is not None and not df.empty:
                save_df(engine, tbl, _norm_cols(df))
                logger.info(f"  ✅ {tbl} <- {api}: {len(df)} 行")
        except Exception as e:
            logger.warning(f"  {api} -> {tbl}: {e}")

    pull_save("stock_basic", "ref_stock_basic", exchange="", list_status="L")
    time.sleep(0.5)
    pull_save("namechange", "ref_stock_namechange")
    time.sleep(0.5)
    pull_save("new_share", "ref_new_share")
    time.sleep(0.5)

    if dry_run:
        return
    try:
        dfw = pro.weekly(trade_date=trade_date)
        if dfw is not None and not dfw.empty:
            save_df(engine, "stock_weekly", _norm_cols(dfw))
            logger.info(f"  ✅ stock_weekly 全市场单日接口: {len(dfw)} 行")
            return
    except Exception as e:
        logger.info(f"  weekly(trade_date) 不可用，改分批: {e}")

    stocks = get_stock_list(engine)
    if stocks.empty:
        return
    batch = 80
    codes = stocks["ts_code"].tolist()
    total = 0
    for i in range(0, len(codes), batch):
        chunk = ",".join(codes[i : i + batch])
        try:
            dfw = pro.weekly(ts_code=chunk, trade_date=trade_date)
            if dfw is not None and not dfw.empty:
                total += save_df(engine, "stock_weekly", _norm_cols(dfw))
        except Exception as e:
            logger.warning(f"  weekly 分批 {i}-{i+batch}: {e}")
        time.sleep(0.5)
    logger.info(f"  ✅ stock_weekly 分批累计约 {total} 行")


def collect_monthly_kline_and_weights(engine, trade_date: str, dry_run: bool) -> None:
    """每月：月线 + 核心指数成分权重。"""
    logger.info("=" * 60)
    logger.info(f"📆 [月] stock_monthly + index_weight: {trade_date}")
    pair = get_tushare_pro("basic")
    if pair[1] is None:
        return
    _, pro = pair

    if not dry_run:
        try:
            dfm = pro.monthly(trade_date=trade_date)
            if dfm is not None and not dfm.empty:
                save_df(engine, "stock_monthly", _norm_cols(dfm))
                logger.info(f"  ✅ stock_monthly 全市场: {len(dfm)} 行")
            else:
                raise ValueError("empty")
        except Exception as e:
            logger.info(f"  monthly(trade_date) 不可用，改分批: {e}")
            stocks = get_stock_list(engine)
            batch = 80
            codes = stocks["ts_code"].tolist()
            for i in range(0, len(codes), batch):
                chunk = ",".join(codes[i : i + batch])
                try:
                    dfm = pro.monthly(ts_code=chunk, trade_date=trade_date)
                    if dfm is not None and not dfm.empty:
                        save_df(engine, "stock_monthly", _norm_cols(dfm))
                except Exception as ex:
                    logger.warning(f"  monthly 分批: {ex}")
                time.sleep(0.5)

    pair2 = get_tushare_pro("index")
    if pair2[1] is None:
        pair2 = pair
    _, proix = pair2
    for index_code in ("000300.SH", "000905.SH", "399006.SZ"):
        if dry_run:
            continue
        try:
            dfw = proix.index_weight(index_code=index_code, trade_date=trade_date)
            if dfw is not None and not dfw.empty:
                dfw = _norm_cols(dfw)
                save_df(engine, "index_weight", dfw)
                logger.info(f"  ✅ index_weight {index_code}: {len(dfw)} 行")
        except Exception as e:
            logger.warning(f"  index_weight {index_code}: {e}")
        time.sleep(0.45)


def collect_quarterly_fin_announcements(engine, trade_date: str, dry_run: bool) -> None:
    """季报期：按公告日拉取预告/快报（全市场有则入库）；完整三大表请用离线脚本。"""
    logger.info("=" * 60)
    logger.info(f"📒 [季] fin_forecast / fin_express (ann_date={trade_date})")
    pair = get_tushare_pro("financial")
    if pair[1] is None:
        pair = get_tushare_pro("basic")
    if pair[1] is None:
        logger.warning("⚠️ financial token 缺失，跳过季报快报/预告")
        return
    _, pro = pair
    if dry_run:
        return
    for api, tbl in (("forecast", "fin_forecast"), ("express", "fin_express")):
        try:
            fn = getattr(pro, api)
            df = fn(ann_date=trade_date)
            if df is not None and not df.empty:
                save_df(engine, tbl, _norm_cols(df))
                logger.info(f"  ✅ {tbl}: {len(df)} 行")
        except Exception as e:
            logger.warning(f"  {api}(ann_date): {e}")
        time.sleep(0.45)


def verify_collection(engine, trade_date: str) -> None:
    logger.info("=" * 60)
    logger.info("📋 验证采集结果（抽样行数）")
    from sqlalchemy import text

    checks = [
        ("stock_daily", "trade_date", trade_date),
        ("stock_daily_basic", "trade_date", trade_date),
        ("index_daily", "trade_date", trade_date),
        ("stock_moneyflow_daily", "trade_date", trade_date),
        ("margin_summary_daily", "trade_date", trade_date),
    ]
    for tbl, col, val in checks:
        try:
            q = text(f'SELECT COUNT(*) AS c FROM "{tbl}" WHERE {col} = :d')
            df = pd.read_sql(q, engine, params={"d": val})
            logger.info(f"  {tbl}: {int(df['c'].iloc[0])} 行 ({col}={val})")
        except Exception as e:
            logger.warning(f"  {tbl}: 查询失败 {e}")


def main():
    parser = argparse.ArgumentParser(description="按日/周/月/季调度 Tushare 入库")
    parser.add_argument("--date", default=None, help="采集日期 YYYYMMDD，默认今天或上一交易日逻辑")
    parser.add_argument("--dry-run", action="store_true", help="试运行不写库")
    parser.add_argument("--indices-only", action="store_true")
    parser.add_argument("--stocks-only", action="store_true", help="仅 stock_daily，不跑日频扩展")
    parser.add_argument("--skip-trade-day-check", action="store_true")
    parser.add_argument(
        "--schedule",
        choices=("daily", "weekly", "monthly", "quarterly", "full"),
        default="daily",
        help="daily=默认日频全包; weekly/monthly/quarterly=只跑该档; full=全部",
    )
    parser.add_argument(
        "--with-weekly", action="store_true", help="在 daily 调度末尾追加周任务"
    )
    parser.add_argument(
        "--with-monthly", action="store_true", help="在 daily 调度末尾追加月任务（或月末自动）"
    )
    parser.add_argument(
        "--with-quarterly", action="store_true", help="在 daily 调度末尾追加季任务"
    )
    args = parser.parse_args()

    if args.date:
        trade_date = args.date
    else:
        now = datetime.now()
        if now.hour < 15 or (now.hour == 15 and now.minute < 30):
            trade_date = (now - timedelta(days=1)).strftime("%Y%m%d")
            logger.info(f"当前早于 15:30，默认交易日: {trade_date}")
        else:
            trade_date = now.strftime("%Y%m%d")

    logger.info("=" * 60)
    logger.info(f"🚀 collect_daily_all 启动 | schedule={args.schedule} | date={trade_date}")
    logger.info("=" * 60)

    engine = get_engine()
    if not engine:
        logger.error("无法连接 PostgreSQL")
        return

    if not args.skip_trade_day_check and args.schedule == "daily":
        if not is_trade_day(engine, trade_date):
            logger.warning(f"{trade_date} 非交易日，跳过日频采集")
            engine.dispose()
            return

    def run_daily_bundle():
        if not args.indices_only:
            collect_stocks_daily(engine, trade_date, dry_run=args.dry_run)
            if not args.stocks_only:
                collect_daily_market_extensions(engine, trade_date, args.dry_run)
        if not args.stocks_only:
            collect_index_daily(engine, trade_date, dry_run=args.dry_run)

    if args.schedule == "daily":
        run_daily_bundle()
        if args.with_weekly:
            collect_weekly_reference_and_kline(engine, trade_date, args.dry_run)
        if args.with_monthly or is_last_open_day_of_month(engine, trade_date):
            collect_monthly_kline_and_weights(engine, trade_date, args.dry_run)
        if args.with_quarterly:
            collect_quarterly_fin_announcements(engine, trade_date, args.dry_run)
    elif args.schedule == "weekly":
        collect_weekly_reference_and_kline(engine, trade_date, args.dry_run)
    elif args.schedule == "monthly":
        collect_monthly_kline_and_weights(engine, trade_date, args.dry_run)
    elif args.schedule == "quarterly":
        collect_quarterly_fin_announcements(engine, trade_date, args.dry_run)
    elif args.schedule == "full":
        run_daily_bundle()
        collect_weekly_reference_and_kline(engine, trade_date, args.dry_run)
        collect_monthly_kline_and_weights(engine, trade_date, args.dry_run)
        collect_quarterly_fin_announcements(engine, trade_date, args.dry_run)

    if not args.dry_run and args.schedule == "daily":
        verify_collection(engine, trade_date)

    engine.dispose()
    logger.info("✅ 任务结束")


if __name__ == "__main__":
    main()
