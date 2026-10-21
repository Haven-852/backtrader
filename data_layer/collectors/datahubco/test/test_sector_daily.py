"""
板块当日行情测试（Datahubco）

拉取东方财富概念/行业/地域板块当日（或最近已收盘交易日）数据：
  - dc_index          板块涨跌幅、领涨股、上涨/下跌家数
  - dc_daily          板块 OHLC / 振幅 / 换手
  - moneyflow_ind_dc  板块资金流向

结果写入本目录（data_layer/collectors/datahubco/test/）下的 CSV。

用法（项目根目录）:
  python -m data_layer.collectors.datahubco.test.test_sector_daily
  python -m data_layer.collectors.datahubco.test.test_sector_daily --trade-date 20260909
  python -m data_layer.collectors.datahubco.test.test_sector_daily --types 概念板块,行业板块
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

# 保证从任意 cwd 可导入项目包
_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data_layer.collectors.datahubco.client import DatahubcoClient
from data_layer.collectors.datahubco.selective import SelectiveDataFetcher

logger = logging.getLogger("collector.datahubco.test.sector")

OUTPUT_DIR = Path(__file__).resolve().parent

# 东财板块类型（dc_index / dc_daily）
DC_IDX_TYPES = ("概念板块", "行业板块", "地域板块")
# 资金流向 content_type
MF_CONTENT_TYPES = ("概念", "行业", "地域")

# CSV 文件名用英文，避免 Windows 控制台乱码
TYPE_FILE_TAG = {
    "概念板块": "concept",
    "行业板块": "industry",
    "地域板块": "region",
    "概念": "concept",
    "行业": "industry",
    "地域": "region",
    "全部": "all",
}


def resolve_trade_date(ymd: Optional[str] = None) -> str:
    """
    解析交易日 YYYYMMDD。
    未指定时：用挑选式会话规则（盘中取上一交易日，收盘后取最近已收盘日）。
    """
    if ymd:
        s = ymd.strip().replace("-", "")
        if len(s) != 8 or not s.isdigit():
            raise ValueError(f"非法 trade_date: {ymd}")
        return s

    fetcher = SelectiveDataFetcher()
    try:
        fetcher.ensure_trade_cal()
        ctx = fetcher.session_context()
        asof: date = ctx["asof_date"]
        logger.info(
            "会话 asof=%s phase=%s is_trading_day=%s",
            asof.isoformat(),
            ctx.get("phase"),
            ctx.get("is_trading_day"),
        )
        return asof.strftime("%Y%m%d")
    except Exception as e:
        logger.warning("交易日历不可用，回退今天: %s", e)
        return datetime.now().strftime("%Y%m%d")


def _safe_name(s: str) -> str:
    if s in TYPE_FILE_TAG:
        return TYPE_FILE_TAG[s]
    return (
        s.replace("板块", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "")
    )


def _save_csv(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("已写入 %s (%d 行, %d 列)", path, len(df), len(df.columns))
    return path


def fetch_dc_index(
    client: DatahubcoClient,
    trade_date: str,
    idx_types: Sequence[str] = DC_IDX_TYPES,
) -> Dict[str, pd.DataFrame]:
    """东财板块指数：涨跌幅 / 领涨股 / 涨跌家数。"""
    out: Dict[str, pd.DataFrame] = {}

    def _sort_part(part: pd.DataFrame) -> pd.DataFrame:
        if "pct_change" in part.columns:
            part = part.sort_values("pct_change", ascending=False)
        return part.reset_index(drop=True)

    # 网关现要求 idx_type；全日拉取仅作兜底。
    all_df = pd.DataFrame()
    try:
        all_df = client.query("dc_index", trade_date=trade_date)
    except Exception as e:
        logger.warning("dc_index 全日拉取失败，改按类型请求: %s", e)

    if not all_df.empty:
        if "idx_type" not in all_df.columns:
            out["全部"] = all_df
            return out
        for t in idx_types:
            part = all_df.loc[all_df["idx_type"] == t].copy()
            if part.empty:
                continue
            out[t] = _sort_part(part)
        if out:
            return out

    for t in idx_types:
        try:
            part = client.query("dc_index", trade_date=trade_date, idx_type=t)
        except Exception as e:
            logger.warning("dc_index(%s) 失败: %s", t, e)
            continue
        if part is None or part.empty:
            logger.warning("dc_index 无类型 %s", t)
            continue
        out[t] = _sort_part(part)
    if not out:
        logger.warning("dc_index 空结果 trade_date=%s", trade_date)
    return out


def fetch_dc_daily(
    client: DatahubcoClient,
    trade_date: str,
    idx_types: Sequence[str] = DC_IDX_TYPES,
) -> Dict[str, pd.DataFrame]:
    """东财板块日行情：开高低收 / 涨跌幅 / 成交额。"""
    out: Dict[str, pd.DataFrame] = {}
    all_df = client.query("dc_daily", trade_date=trade_date)
    if all_df.empty:
        return out

    cat_col = "category" if "category" in all_df.columns else None
    if not cat_col:
        out["全部"] = all_df
        return out

    # category 通常与概念/行业/地域对应
    for t in idx_types:
        # 兼容「概念板块」与「概念」
        keys = {t, t.replace("板块", ""), _safe_name(t)}
        mask = all_df[cat_col].astype(str).isin(keys) | all_df[cat_col].astype(str).str.contains(
            t.replace("板块", ""), na=False
        )
        part = all_df.loc[mask].copy()
        if part.empty:
            continue
        if "pct_change" in part.columns:
            part = part.sort_values("pct_change", ascending=False)
        out[t] = part.reset_index(drop=True)
    if not out:
        out["全部"] = all_df
    return out


def fetch_moneyflow(
    client: DatahubcoClient,
    trade_date: str,
    content_types: Sequence[str] = MF_CONTENT_TYPES,
) -> Dict[str, pd.DataFrame]:
    """东财板块资金流向（含涨跌幅 + 主力净流入）。"""
    out: Dict[str, pd.DataFrame] = {}
    for t in content_types:
        try:
            df = client.query("moneyflow_ind_dc", trade_date=trade_date, content_type=t)
        except Exception as e:
            logger.warning("moneyflow_ind_dc(%s) 失败: %s", t, e)
            continue
        if df.empty:
            continue
        if "pct_change" in df.columns:
            df = df.sort_values("pct_change", ascending=False)
        out[t] = df.reset_index(drop=True)
    return out


def fetch_ths_daily_sample(
    client: DatahubcoClient,
    trade_date: str,
    limit_codes: int = 50,
) -> pd.DataFrame:
    """
    同花顺板块指数日行情（可选）。
    先取 ths_index 清单，再按日拉 ths_daily（可能较大，默认只保留涨跌幅前列）。
    """
    daily = client.query("ths_daily", trade_date=trade_date)
    if daily.empty:
        return daily
    if "pct_change" in daily.columns:
        daily = daily.sort_values("pct_change", ascending=False)
    # 尝试挂名称
    try:
        basic = client.query("ths_index")
        if not basic.empty and "ts_code" in basic.columns and "name" in basic.columns:
            daily = daily.merge(
                basic[["ts_code", "name", "type"]].drop_duplicates("ts_code"),
                on="ts_code",
                how="left",
            )
    except Exception as e:
        logger.warning("合并 ths_index 名称失败: %s", e)
    return daily.head(limit_codes).reset_index(drop=True)


def run_sector_daily_test(
    trade_date: Optional[str] = None,
    types: Optional[Sequence[str]] = None,
    include_ths: bool = True,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    执行测试：拉板块数据 → 写 CSV → 返回摘要。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    ymd = resolve_trade_date(trade_date)
    idx_types = list(types) if types else list(DC_IDX_TYPES)
    client = DatahubcoClient()

    written: List[str] = []
    summary: Dict[str, Any] = {
        "trade_date": ymd,
        "idx_types": idx_types,
        "files": written,
        "counts": {},
    }

    # 1) dc_index —— 当天各板块涨跌幅
    index_map = fetch_dc_index(client, ymd, idx_types)
    frames_index = []
    for t, df in index_map.items():
        path = out_dir / f"dc_index_{_safe_name(t)}_{ymd}.csv"
        _save_csv(df, path)
        written.append(str(path))
        summary["counts"][f"dc_index:{t}"] = len(df)
        frames_index.append(df.assign(source_type=t))
        # 打印涨跌幅 Top5
        if "pct_change" in df.columns and "name" in df.columns:
            top = df.head(5)[["name", "pct_change"]]
            logger.info("[%s] 涨幅 Top5:\n%s", t, top.to_string(index=False))

    if frames_index:
        all_index = pd.concat(frames_index, ignore_index=True)
        path = out_dir / f"dc_index_{_safe_name('全部')}_{ymd}.csv"
        _save_csv(all_index, path)
        written.append(str(path))

    # 2) dc_daily —— OHLC
    daily_map = fetch_dc_daily(client, ymd, idx_types)
    for t, df in daily_map.items():
        path = out_dir / f"dc_daily_{_safe_name(t)}_{ymd}.csv"
        _save_csv(df, path)
        written.append(str(path))
        summary["counts"][f"dc_daily:{t}"] = len(df)

    # 3) 资金流向
    mf_types = []
    for t in idx_types:
        short = t.replace("板块", "")
        if short in MF_CONTENT_TYPES:
            mf_types.append(short)
    if not mf_types:
        mf_types = list(MF_CONTENT_TYPES)
    mf_map = fetch_moneyflow(client, ymd, mf_types)
    for t, df in mf_map.items():
        path = out_dir / f"moneyflow_{_safe_name(t)}_{ymd}.csv"
        _save_csv(df, path)
        written.append(str(path))
        summary["counts"][f"moneyflow:{t}"] = len(df)

    # 4) 同花顺板块（可选）
    if include_ths:
        try:
            ths = fetch_ths_daily_sample(client, ymd)
            if not ths.empty:
                path = out_dir / f"ths_daily_top_{ymd}.csv"
                _save_csv(ths, path)
                written.append(str(path))
                summary["counts"]["ths_daily_top"] = len(ths)
        except Exception as e:
            logger.warning("ths_daily 跳过: %s", e)
            summary["ths_error"] = str(e)

    summary["ok"] = bool(written)
    summary["output_dir"] = str(out_dir)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Datahubco 板块当日涨跌测试 → CSV")
    parser.add_argument(
        "--trade-date",
        default=None,
        help="交易日 YYYYMMDD / YYYY-MM-DD；默认按会话 asof",
    )
    parser.add_argument(
        "--types",
        default="概念板块,行业板块,地域板块",
        help="逗号分隔：概念板块,行业板块,地域板块",
    )
    parser.add_argument("--no-ths", action="store_true", help="跳过同花顺 ths_daily")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="CSV 输出目录（默认与本脚本同目录 test/）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    types = [x.strip() for x in args.types.split(",") if x.strip()]
    out = Path(args.output_dir) if args.output_dir else OUTPUT_DIR

    summary = run_sector_daily_test(
        trade_date=args.trade_date,
        types=types,
        include_ths=not args.no_ths,
        output_dir=out,
    )

    print("\n===== 板块当日测试摘要 =====")
    print(f"trade_date : {summary.get('trade_date')}")
    print(f"output_dir : {summary.get('output_dir')}")
    print("counts:")
    for k, v in (summary.get("counts") or {}).items():
        print(f"  {k}: {v}")
    print("files:")
    for f in summary.get("files") or []:
        print(f"  {f}")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
