# -*- coding: utf-8 -*-
"""一次性重建拉取：Datahubco → PostgreSQL（最近一个交易日 + 参照表）。"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rebuild_pull")

from data_layer.config import config
from data_layer.collectors.datahubco.client import DatahubcoClient
from data_layer.collectors.datahubco.continuous import ContinuousDataFetcher
from sqlalchemy import create_engine

TRADE_DATE = "20260911"
BATCH = 20
SLEEP = 0.18

TABLE_COLS = {
    "ref_stock_basic": [
        "ts_code", "symbol", "name", "area", "industry", "cnspell", "market",
        "list_date", "list_status", "delist_date", "is_hs", "act_name", "act_ent_type",
    ],
    "ref_new_share": [
        "ts_code", "sub_code", "name", "ipo_date", "issue_date", "amount",
        "market_amount", "price", "pe", "limit_amount", "funds", "ballot",
    ],
    "stock_daily": [
        "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "change", "pct_chg", "vol", "amount",
    ],
    "stock_daily_basic": [
        "ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f",
        "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio",
        "dv_ttm", "total_share", "float_share", "free_share", "total_mv", "circ_mv",
    ],
    "stock_adj_factor": ["ts_code", "trade_date", "adj_factor"],
    "stock_moneyflow_daily": [
        "ts_code", "trade_date", "buy_sm_vol", "buy_sm_amount", "sell_sm_vol",
        "sell_sm_amount", "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
        "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
        "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
        "net_mf_vol", "net_mf_amount",
    ],
    "stock_top_list_daily": [
        "ts_code", "trade_date", "name", "close", "pct_change", "turnover_rate",
        "amount", "l_sell", "l_buy", "l_amount", "net_amount", "net_rate",
        "amount_rate", "float_values", "reason",
    ],
    "stock_block_trade": [
        "ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller",
    ],
    "ref_suspend_d": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
    "margin_summary_daily": [
        "trade_date", "exchange_id", "rzye", "rzmre", "rzche", "rqye",
        "rqmcl", "rzrqye", "rqyl",
    ],
}

DATE_COLS = {
    "list_date", "delist_date", "ipo_date", "issue_date", "trade_date",
    "cal_date", "pretrade_date", "ann_date", "start_date", "end_date",
}


def fit(df: pd.DataFrame, table: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if table == "ref_suspend_d" and "suspend_reason" not in df.columns and "suspend_timing" in df.columns:
        df = df.rename(columns={"suspend_timing": "suspend_reason"})
        TABLE_COLS.setdefault(table, [])
    cols = TABLE_COLS.get(table)
    if table == "ref_suspend_d":
        cols = ["ts_code", "trade_date", "suspend_reason", "suspend_type"]
    if cols:
        keep = [c for c in cols if c in df.columns]
        df = df.loc[:, keep]
    for col in df.columns:
        if col in DATE_COLS:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    pk_two = {"ts_code", "trade_date"}
    if pk_two.issubset(df.columns) and table == "stock_block_trade":
        df = df.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    return df


def save_df(engine, table: str, df: pd.DataFrame) -> int:
    df = fit(df, table)
    if df.empty:
        return 0
    df.to_sql(table, engine, if_exists="append", index=False, method="multi", chunksize=1000)
    return len(df)


def query_retry(client, api, **kw):
    last = None
    for attempt in range(3):
        try:
            return client.query(api, **kw)
        except Exception as e:
            last = e
            logger.warning("%s 失败 (%s/3): %s", api, attempt + 1, e)
            time.sleep(1.2 * (attempt + 1))
    raise last


def main():
    engine = create_engine(config.get_postgres_url())
    client = DatahubcoClient()
    stats = {}

    logger.info("=== 1) 证券主档 stock_basic ===")
    frames = []
    for ex in ("SSE", "SZSE", "BSE"):
        df = query_retry(client, "stock_basic", exchange=ex, list_status="L")
        if df is None or df.empty:
            logger.info("  %s 空", ex)
            continue
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        df["list_status"] = "L"
        frames.append(df)
        logger.info("  %s %d 行", ex, len(df))
        time.sleep(SLEEP)
    if frames:
        stats["ref_stock_basic"] = save_df(engine, "ref_stock_basic", pd.concat(frames, ignore_index=True))
        logger.info("写入 ref_stock_basic %d", stats["ref_stock_basic"])

    logger.info("=== 2) 新股 new_share ===")
    df = query_retry(client, "new_share", start_date="20200101", end_date=TRADE_DATE)
    stats["ref_new_share"] = save_df(engine, "ref_new_share", df)
    logger.info("写入 ref_new_share %d", stats["ref_new_share"])
    time.sleep(SLEEP)

    stocks = pd.read_sql("SELECT ts_code FROM ref_stock_basic ORDER BY ts_code", engine)
    codes = stocks["ts_code"].tolist()
    logger.info("待分批股票 %d 只", len(codes))

    batched_apis = [
        ("daily", "stock_daily"),
        ("daily_basic", "stock_daily_basic"),
        ("adj_factor", "stock_adj_factor"),
        ("moneyflow", "stock_moneyflow_daily"),
    ]
    for api, table in batched_apis:
        logger.info("=== 3) %s -> %s  交易日 %s ===", api, table, TRADE_DATE)
        total = 0
        n_batches = (len(codes) + BATCH - 1) // BATCH
        for i in range(0, len(codes), BATCH):
            chunk = codes[i : i + BATCH]
            bno = i // BATCH + 1
            try:
                df = query_retry(client, api, ts_code=",".join(chunk), trade_date=TRADE_DATE)
                total += save_df(engine, table, df)
            except Exception as e:
                logger.error("  batch %s/%s 失败: %s", bno, n_batches, e)
            if bno % 25 == 0 or bno == n_batches:
                logger.info("  进度 %s/%s 累计写入 %d", bno, n_batches, total)
            time.sleep(SLEEP)
        stats[table] = total
        logger.info("完成 %s = %d", table, total)

    logger.info("=== 4) 全市场小表 ===")
    oneshots = [
        ("top_list", "stock_top_list_daily", {"trade_date": TRADE_DATE}),
        ("suspend_d", "ref_suspend_d", {"trade_date": TRADE_DATE}),
        ("block_trade", "stock_block_trade", {"trade_date": TRADE_DATE}),
    ]
    for api, table, kw in oneshots:
        try:
            df = query_retry(client, api, **kw)
            stats[table] = save_df(engine, table, df)
            logger.info("  %s -> %s %d", api, table, stats[table])
        except Exception as e:
            logger.warning("  %s 失败: %s", api, e)
        time.sleep(SLEEP)

    msum = 0
    for ex in ("SSE", "SZSE"):
        try:
            df = query_retry(client, "margin", trade_date=TRADE_DATE, exchange_id=ex)
            msum += save_df(engine, "margin_summary_daily", df)
            logger.info("  margin %s %d", ex, 0 if df is None else len(df))
        except Exception as e:
            logger.warning("  margin %s 失败: %s", ex, e)
        time.sleep(SLEEP)
    stats["margin_summary_daily"] = msum

    logger.info("=== 5) 核心指数 + 东财板块 ===")
    cont = ContinuousDataFetcher(client=client)
    idx = cont.sync_indices(trade_date=TRADE_DATE, lookback_days=5)
    logger.info("index_daily: %s", {k: idx.get(k) for k in ("ok", "upserted", "errors")})
    stats["index_daily"] = idx.get("upserted", 0)
    sec = cont.sync_sectors(trade_date=TRADE_DATE, with_members=True, member_limit=20)
    logger.info("sectors: ok=%s counts=%s", sec.get("ok"), sec.get("counts") or sec.get("upserted"))
    stats["sectors"] = sec.get("counts") or sec.get("upserted")

    logger.info("=== 6) 库内行数 ===")
    with engine.connect() as conn:
        tables = [
            "ref_trade_cal", "ref_stock_basic", "ref_new_share",
            "stock_daily", "stock_daily_basic", "stock_adj_factor",
            "stock_moneyflow_daily", "stock_top_list_daily", "stock_block_trade",
            "ref_suspend_d", "margin_summary_daily", "index_daily",
            "sector_dc_index_daily", "sector_dc_daily", "sector_moneyflow_daily",
        ]
        print("STATS", stats)
        for t in tables:
            n = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
            print(f"  {t}: {n}")
    engine.dispose()
    logger.info("拉取结束")


if __name__ == "__main__":
    main()
