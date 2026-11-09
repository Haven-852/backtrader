#!/usr/bin/env python3
"""
ml_train_daily 上训练 LightGBM 回归：y_high / y_low（次日高低相对当日收盘）。

用法（在 backtrader 根目录，已配置 PostgreSQL）：
  python scripts/train_ml_daily_lgbm.py --dry-run
  python scripts/train_ml_daily_lgbm.py --start 2023-01-01 --end 2025-09-30
  python scripts/train_ml_daily_lgbm.py --limit-days 180

学习要点见 doc/guidelines/LightGBM-日内高低点训练方案.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.ml_train_toolkit import FEATURE_COLS  # noqa: E402

SESSION_MAP = {"正常": 0, "一字涨停": 1, "一字跌停": 2, "平盘一字": 3, "停牌": 4}
BOARD_MAP = {"主板": 0, "创业板": 1, "科创板": 2, "北交所": 3, "ST": 4}

LGB_PARAMS = {
    "objective": "huber",
    "alpha": 0.9,
    "metric": "huber",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 500,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": 42,
    "num_threads": 0,
}

OPTIONAL_VAL_COLS = ("pe_ttm", "pb")


def _engine():
    from server.storage_service import storage_manager

    return storage_manager.get_postgres_engine()


def _table_columns(eng) -> set[str]:
    from sqlalchemy import text

    rows = pd.read_sql(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'ml_train_daily'
            """
        ),
        eng,
    )
    return set(rows["column_name"].astype(str))


def load_frame(start: str | None, end: str | None, limit_days: int | None) -> pd.DataFrame:
    from sqlalchemy import text

    eng = _engine()
    available = _table_columns(eng)
    cols = [
        "symbol",
        "trade_date",
        "y_high",
        "y_low",
        "train_ready",
        *FEATURE_COLS,
        *[c for c in OPTIONAL_VAL_COLS if c in available],
    ]
    # 去重且保持顺序
    seen = set()
    col_list = []
    for c in cols:
        if c in available and c not in seen:
            seen.add(c)
            col_list.append(c)
    col_sql = ", ".join(col_list)

    clauses = [
        "train_ready = 1",
        "y_high IS NOT NULL",
        "y_low IS NOT NULL",
    ]
    params: dict = {}
    if start:
        clauses.append("trade_date >= :start")
        params["start"] = start
    if end:
        clauses.append("trade_date <= :end")
        params["end"] = end
    where = " AND ".join(clauses)

    if limit_days:
        q = text(
            f"""
            SELECT {col_sql}
            FROM ml_train_daily
            WHERE {where}
              AND trade_date IN (
                SELECT DISTINCT trade_date FROM ml_train_daily
                WHERE {where}
                ORDER BY trade_date DESC
                LIMIT :n_days
              )
            ORDER BY trade_date, symbol
            """
        )
        params["n_days"] = int(limit_days)
    else:
        q = text(f"SELECT {col_sql} FROM ml_train_daily WHERE {where} ORDER BY trade_date, symbol")

    return pd.read_sql(q, eng, params=params)


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "session_flag" in out.columns:
        out["session_flag"] = out["session_flag"].map(SESSION_MAP)
    if "board_type" in out.columns:
        out["board_type"] = out["board_type"].map(BOARD_MAP)
    if "industry_l1" in out.columns:
        out["industry_l1"] = out["industry_l1"].astype("category").cat.codes.replace(-1, np.nan)
    return out


def sanitize_valuation(train: pd.DataFrame, apply_to: pd.DataFrame) -> pd.DataFrame:
    """PE/PB：负值 NaN；分位数仅在 train 上估计。"""
    out = apply_to.copy()
    for col in OPTIONAL_VAL_COLS:
        if col not in out.columns:
            continue
        tr = train[col].where(train[col] > 0)
        lo, hi = tr.quantile(0.01), tr.quantile(0.99)
        s = out[col].where(out[col] > 0)
        out[col] = s.clip(lo, hi)
    return out


def feature_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    use = [c for c in FEATURE_COLS if c in df.columns]
    for c in OPTIONAL_VAL_COLS:
        if c in df.columns and c not in use:
            use.append(c)
    x = df[use].copy()
    return x, use


def time_split(dates: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    u = sorted(pd.to_datetime(dates.dropna().unique()))
    n = len(u)
    if n < 10:
        raise ValueError(f"交易日过少: {n}")
    i1 = min(max(1, int(n * 0.70)), n - 2)
    i2 = min(max(i1 + 1, int(n * 0.85)), n - 1)
    return pd.Timestamp(u[i1]), pd.Timestamp(u[i2])


def assign_split(df: pd.DataFrame, t_val: pd.Timestamp, t_test: pd.Timestamp) -> pd.Series:
    td = pd.to_datetime(df["trade_date"])
    return np.where(
        td < t_val,
        "train",
        np.where(td < t_test, "validation", "test"),
    )


def rmse(y_true, y_pred) -> float:
    e = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    return float(np.sqrt(np.mean(e ** 2)))


def median_baseline(y_train: np.ndarray, y_eval: np.ndarray) -> float:
    med = float(np.median(y_train))
    return rmse(y_eval, np.full_like(y_eval, med, dtype=float))


def train_one(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    names: list[str],
):
    import lightgbm as lgb

    dtrain = lgb.Dataset(x_train, label=y_train, feature_name=names, free_raw_data=False)
    valid = [dtrain]
    callbacks = []
    if len(y_val) >= 1000:
        dval = lgb.Dataset(x_val, label=y_val, feature_name=names, reference=dtrain, free_raw_data=False)
        valid = [dtrain, dval]
        callbacks = [lgb.early_stopping(50, verbose=False)]
    return lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=800,
        valid_sets=valid,
        valid_names=["train", "validation"] if len(valid) > 1 else ["train"],
        callbacks=callbacks or None,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Train LightGBM on ml_train_daily (y_high / y_low)")
    ap.add_argument("--start", default=None, help="trade_date >= YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="trade_date <= YYYY-MM-DD")
    ap.add_argument("--limit-days", type=int, default=None, help="仅取最近 N 个交易日（调试）")
    ap.add_argument("--dry-run", action="store_true", help="只加载、切分、打印统计，不训练")
    ap.add_argument("--out-dir", default=str(_ROOT / "data" / "lgbm_daily"), help="模型输出目录")
    args = ap.parse_args()

    print("加载 ml_train_daily ...")
    df = load_frame(args.start, args.end, args.limit_days)
    if df.empty:
        print("无数据，请检查 train_ready 与数据库连接。")
        return 1

    df = encode_categoricals(df)
    t_val, t_test = time_split(df["trade_date"])
    df["split"] = assign_split(df, t_val, t_test)

    train_df = df[df["split"] == "train"]
    val_df = df[df["split"] == "validation"]
    test_df = df[df["split"] == "test"]

    train_df = sanitize_valuation(train_df, train_df)
    val_df = sanitize_valuation(train_df, val_df)
    test_df = sanitize_valuation(train_df, test_df)

    x_all, feat_names = feature_matrix(df)
    print(f"行数: total={len(df)} train={len(train_df)} val={len(val_df)} test={len(test_df)}")
    print(f"日期切分: val_start={t_val.date()} test_start={t_test.date()}")
    print(f"特征数: {len(feat_names)}")

    if args.dry_run:
        print(json.dumps({"feature_names": feat_names, "split_bounds": {"val": str(t_val), "test": str(t_test)}}, ensure_ascii=False, indent=2))
        return 0

    x_tr, y_h_tr, y_l_tr = (
        x_all.loc[train_df.index].to_numpy(dtype=float),
        train_df["y_high"].to_numpy(dtype=float),
        train_df["y_low"].to_numpy(dtype=float),
    )
    x_va = x_all.loc[val_df.index].to_numpy(dtype=float)
    y_h_va, y_l_va = val_df["y_high"].to_numpy(dtype=float), val_df["y_low"].to_numpy(dtype=float)
    x_te = x_all.loc[test_df.index].to_numpy(dtype=float)
    y_h_te, y_l_te = test_df["y_high"].to_numpy(dtype=float), test_df["y_low"].to_numpy(dtype=float)

    print("训练 y_high ...")
    m_h = train_one(x_tr, y_h_tr, x_va, y_h_va, feat_names)
    print("训练 y_low ...")
    m_l = train_one(x_tr, y_l_tr, x_va, y_l_va, feat_names)

    p_h_va, p_l_va = m_h.predict(x_va), m_l.predict(x_va)
    p_h_te, p_l_te = m_h.predict(x_te), m_l.predict(x_te)

    report = {
        "val": {
            "y_high_rmse": rmse(y_h_va, p_h_va),
            "y_high_baseline_rmse": median_baseline(y_h_tr, y_h_va),
            "y_low_rmse": rmse(y_l_va, p_l_va),
            "y_low_baseline_rmse": median_baseline(y_l_tr, y_l_va),
        },
        "test": {
            "y_high_rmse": rmse(y_h_te, p_h_te),
            "y_high_baseline_rmse": median_baseline(y_h_tr, y_h_te),
            "y_low_rmse": rmse(y_l_te, p_l_te),
            "y_low_baseline_rmse": median_baseline(y_l_tr, y_l_te),
        },
        "split": {"val_start": str(t_val.date()), "test_start": str(t_test.date())},
        "n_features": len(feat_names),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m_h.save_model(str(out / "y_high.txt"))
    m_l.save_model(str(out / "y_low.txt"))
    (out / "feature_names.json").write_text(json.dumps(feat_names, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"模型已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
