"""
全市场 LightGBM 训练流水线（次日高低点 y_high / y_low）。

阶段：
  1) 取数：train_ready=1
  2) 清洗：类别编码；排除标识/标签/绝对价
  3) 按 trade_date 切 Train / Valid / Test（禁止随机打乱）
  4) 分别训练 y_high、y_low 两个回归器 + early stopping
  5) 评估：RMSE/MAE + 日度横截面 IC
  6) 可选简易调参（小网格）
  7) 落盘模型与元数据，支持单票推理

用法见 tools/_run_ml_lgbm_train.py
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "data" / "ml_models" / "lgbm_nextday_hl"

SESSION_MAP = {
    "正常": 0,
    "一字涨停": 1,
    "一字跌停": 2,
    "平盘一字": 3,
    "停牌": 4,
}
BOARD_MAP = {
    "主板": 0,
    "创业板": 1,
    "科创板": 2,
    "北交所": 3,
    "ST": 4,
}

# 禁止入模
EXCLUDE_COLS = {
    "symbol",
    "trade_date",
    "industry_code",
    "industry_name",
    "industry_l1",
    "open",
    "high",
    "low",
    "close",
    "y_high",
    "y_low",
    "train_ready",
    "updated_at",
    "is_one_word",
    "is_halted",
}

# 优先特征（表内存在的才会用）；industry_amount_ratio 稀疏，默认不进
PREFERRED_FEATURES = [
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "ret_60",
    "close_ma5", "close_ma10", "close_ma20", "ma5_ma20",
    "atr_5_pct", "atr_14_pct",
    "volatility_5", "volatility_10", "volatility_20",
    "range_1", "range_mean_5", "range_mean_20",
    "upper_shadow", "lower_shadow", "body_pct", "close_location",
    "high_20_ratio", "low_20_ratio",
    "volume_ratio_5", "volume_ratio_20", "amount_ratio_20",
    "turnover_rate", "turnover_ma5", "turnover_ma20", "turnover_std20",
    "price_volume_corr20",
    "log_amount", "log_float_mv", "log_total_mv",
    "listing_age", "board_type",
    "beta_60", "turnover_mean_60", "volatility_mean_60", "limit_touch_count_20",
    "hs300_ret_1", "hs300_ret_5", "zz1000_ret_5",
    "market_volatility_20", "market_amount_ratio", "market_adv_decl_ratio",
    "market_limit_up_count", "market_limit_down_count", "size_style_spread",
    "industry_ret_1", "industry_ret_5", "industry_ret_20", "industry_vol_20",
    "industry_breadth", "stock_excess_ret_5",
    "industry_rank_ret_5", "stock_rank_industry",
    "session_flag",
]


@dataclass
class SplitConfig:
    train_end: str = "2023-06-30"
    valid_end: str = "2024-06-30"
    # test = trade_date > valid_end


@dataclass
class TrainConfig:
    mode: str = "smoke"  # smoke | full
    smoke_start: str = "2023-01-01"
    smoke_end: str = "2024-12-31"
    out_dir: str = str(DEFAULT_OUT_DIR)
    split: SplitConfig = field(default_factory=SplitConfig)
    drop_halt_session: bool = True
    tune: bool = False
    random_seed: int = 42
    # LightGBM 默认超参（可被 tune 覆盖）
    params: Dict[str, Any] = field(default_factory=lambda: {
        "objective": "regression",
        "metric": "mae",
        "learning_rate": 0.05,
        "num_leaves": 64,
        "max_depth": -1,
        "min_data_in_leaf": 200,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "verbosity": -1,
        "n_jobs": -1,
    })
    num_boost_round: int = 2000
    early_stopping_rounds: int = 50


def _engine():
    from data_layer.db_manager import storage_manager

    return storage_manager.get_postgres_engine()


def _select_columns_sql() -> str:
    """只拉训练需要的列，避免 SELECT * 撑爆内存。"""
    cols = ["symbol", "trade_date", "y_high", "y_low", "session_flag", "board_type"]
    cols.extend(PREFERRED_FEATURES)
    # 去重保序
    seen = set()
    ordered: List[str] = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ", ".join(ordered)


def _downcast_frame(df: pd.DataFrame) -> pd.DataFrame:
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for c in df.columns:
        if c in ("symbol", "trade_date", "session_flag", "board_type"):
            continue
        if pd.api.types.is_float_dtype(df[c]) or pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(np.float32)
    return df


def load_train_frame(cfg: TrainConfig) -> pd.DataFrame:
    """
    从 PostgreSQL 拉取可训练行。
    full 模式按年分块拉取再 concat，避免一次 SELECT 把内存打爆。
    """
    eng = _engine()
    cols_sql = _select_columns_sql()
    t0 = time.time()
    frames: List[pd.DataFrame] = []

    if cfg.mode == "smoke":
        ranges = [(cfg.smoke_start, cfg.smoke_end)]
    else:
        # 表覆盖约 2016→当年
        y0, y1 = 2016, datetime.now().year
        ranges = [(f"{y}-01-01", f"{y}-12-31") for y in range(y0, y1 + 1)]

    logger.info("loading ml_train_daily mode=%s chunks=%s ...", cfg.mode, len(ranges))
    for i, (d0, d1) in enumerate(ranges, 1):
        sql = f"""
            SELECT {cols_sql}
            FROM ml_train_daily
            WHERE train_ready = 1
              AND y_high IS NOT NULL AND y_low IS NOT NULL
              AND trade_date >= :d0 AND trade_date <= :d1
            ORDER BY trade_date, symbol
        """
        with eng.connect() as conn:
            part = pd.read_sql(sa_text(sql), conn, params={"d0": d0, "d1": d1})
        part = _downcast_frame(part)
        logger.info("  chunk %s/%s %s..%s rows=%s", i, len(ranges), d0, d1, len(part))
        if len(part):
            frames.append(part)

    if not frames:
        raise ValueError("未读到任何 train_ready=1 样本")
    df = pd.concat(frames, ignore_index=True)
    del frames
    logger.info("loaded rows=%s cols=%s in %.1fs", len(df), df.shape[1], time.time() - t0)
    return df


def clean_features(df: pd.DataFrame, cfg: TrainConfig) -> Tuple[pd.DataFrame, List[str]]:
    """清洗 + 选出特征列。"""
    out = df.copy()
    if cfg.drop_halt_session and "session_flag" in out.columns:
        out = out[out["session_flag"].fillna("") != "停牌"].copy()

    if "session_flag" in out.columns:
        out["session_flag"] = (
            out["session_flag"].map(SESSION_MAP).fillna(-1).astype(np.int16)
        )
    if "board_type" in out.columns:
        out["board_type"] = (
            out["board_type"].map(BOARD_MAP).fillna(-1).astype(np.int16)
        )

    feature_cols = [c for c in PREFERRED_FEATURES if c in out.columns and c not in EXCLUDE_COLS]
    # 数值化
    for c in feature_cols:
        if c in ("session_flag", "board_type"):
            continue
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out["y_high"] = pd.to_numeric(out["y_high"], errors="coerce")
    out["y_low"] = pd.to_numeric(out["y_low"], errors="coerce")
    out = out.dropna(subset=["y_high", "y_low"])
    logger.info("after clean rows=%s features=%s", len(out), len(feature_cols))
    return out, feature_cols


def split_by_date(
    df: pd.DataFrame, cfg: TrainConfig
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_end = date.fromisoformat(cfg.split.train_end)
    valid_end = date.fromisoformat(cfg.split.valid_end)
    d = df["trade_date"]
    train = df[d <= train_end]
    valid = df[(d > train_end) & (d <= valid_end)]
    test = df[d > valid_end]
    logger.info(
        "split train=%s valid=%s test=%s | ends %s / %s",
        len(train), len(valid), len(test), train_end, valid_end,
    )
    if len(train) == 0 or len(valid) == 0:
        raise ValueError(
            "Train/Valid 为空：请检查 mode 日期区间与 split 配置是否重叠。"
            f" smoke=[{cfg.smoke_start},{cfg.smoke_end}] "
            f"split train_end={cfg.split.train_end} valid_end={cfg.split.valid_end}"
        )
    return train, valid, test


def _xy(df: pd.DataFrame, features: Sequence[str], label: str):
    X = df.loc[:, list(features)]
    y = df[label].astype(np.float64)
    return X, y


def daily_ic(dates: Sequence[date], y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """按日横截面 Spearman IC。"""
    frame = pd.DataFrame({"d": list(dates), "y": y_true, "p": y_pred})
    ics: List[float] = []
    for _, g in frame.groupby("d", sort=False):
        if len(g) < 30:
            continue
        ic = g["y"].corr(g["p"], method="spearman")
        if pd.notna(ic):
            ics.append(float(ic))
    if not ics:
        return {"ic_mean": float("nan"), "ic_std": float("nan"), "ic_ir": float("nan"), "ic_days": 0}
    arr = np.asarray(ics, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) > 1 else float("nan")
    ir = mean / std if std and std > 1e-12 else float("nan")
    return {"ic_mean": mean, "ic_std": std, "ic_ir": ir, "ic_days": int(len(arr))}


def eval_regression(
    dates: Sequence[date],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    label: str,
) -> Dict[str, Any]:
    from sklearn.metrics import mean_absolute_error, mean_squared_error

    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae = float(mean_absolute_error(y_true, y_pred))
    ic = daily_ic(dates, y_true, y_pred)
    return {"label": label, "rmse": rmse, "mae": mae, **ic}


def train_one_target(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: Sequence[str],
    label: str,
    cfg: TrainConfig,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    import lightgbm as lgb

    X_tr, y_tr = _xy(train, features, label)
    X_va, y_va = _xy(valid, features, label)
    cat_cols = [c for c in ("session_flag", "board_type") if c in features]
    dtrain = lgb.Dataset(
        X_tr,
        label=y_tr,
        feature_name=list(features),
        categorical_feature=cat_cols or "auto",
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        X_va,
        label=y_va,
        reference=dtrain,
        feature_name=list(features),
        categorical_feature=cat_cols or "auto",
        free_raw_data=False,
    )

    p = dict(cfg.params)
    p.pop("categorical_feature", None)
    if params:
        p.update(params)
        p.pop("categorical_feature", None)
    p["seed"] = cfg.random_seed

    callbacks = [
        lgb.early_stopping(cfg.early_stopping_rounds, verbose=False),
        lgb.log_evaluation(period=0),
    ]
    t0 = time.time()
    booster = lgb.train(
        p,
        dtrain,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[dtrain, dvalid],
        valid_names=["train", "valid"],
        callbacks=callbacks,
    )
    elapsed = round(time.time() - t0, 2)
    best_iter = int(booster.best_iteration or booster.current_iteration())
    pred_va = booster.predict(X_va, num_iteration=best_iter)
    metrics = eval_regression(list(valid["trade_date"]), y_va.to_numpy(), pred_va, label)
    metrics["best_iteration"] = best_iter
    metrics["train_seconds"] = elapsed
    metrics["params"] = {k: p[k] for k in p if k not in ("verbosity",)}
    logger.info(
        "trained %s iter=%s mae=%.5f ic=%.4f (%.1fs)",
        label, best_iter, metrics["mae"], metrics.get("ic_mean", float("nan")), elapsed,
    )
    return booster, metrics


def simple_tune(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    features: Sequence[str],
    label: str,
    cfg: TrainConfig,
) -> Dict[str, Any]:
    """小网格：只调 num_leaves / learning_rate / min_data_in_leaf，以 valid MAE 选优。"""
    grid = [
        {"num_leaves": 31, "learning_rate": 0.05, "min_data_in_leaf": 200},
        {"num_leaves": 64, "learning_rate": 0.05, "min_data_in_leaf": 200},
        {"num_leaves": 96, "learning_rate": 0.03, "min_data_in_leaf": 300},
        {"num_leaves": 64, "learning_rate": 0.03, "min_data_in_leaf": 500},
    ]
    best: Optional[Dict[str, Any]] = None
    for g in grid:
        _, m = train_one_target(train, valid, features, label, cfg, params=g)
        score = float(m["mae"])
        rec = {"params": g, "valid_mae": score, "valid_ic": m.get("ic_mean"), "best_iteration": m["best_iteration"]}
        logger.info("tune %s %s", label, rec)
        if best is None or score < best["valid_mae"]:
            best = rec
    assert best is not None
    return best


def evaluate_test(
    booster: Any,
    test: pd.DataFrame,
    features: Sequence[str],
    label: str,
) -> Dict[str, Any]:
    if len(test) == 0:
        return {"label": label, "note": "test empty"}
    X, y = _xy(test, features, label)
    pred = booster.predict(X, num_iteration=booster.best_iteration or -1)
    return eval_regression(list(test["trade_date"]), y.to_numpy(), pred, label)


def save_artifacts(
    out_dir: Path,
    boosters: Dict[str, Any],
    features: Sequence[str],
    cfg: TrainConfig,
    report: Dict[str, Any],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, model in boosters.items():
        path = out_dir / f"{name}.txt"
        model.save_model(str(path))
    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "features": list(features),
        "session_map": SESSION_MAP,
        "board_map": BOARD_MAP,
        "config": {
            "mode": cfg.mode,
            "smoke_start": cfg.smoke_start,
            "smoke_end": cfg.smoke_end,
            "split": asdict(cfg.split),
            "drop_halt_session": cfg.drop_halt_session,
            "tune": cfg.tune,
            "params": cfg.params,
            "num_boost_round": cfg.num_boost_round,
            "early_stopping_rounds": cfg.early_stopping_rounds,
        },
        "report": report,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return out_dir


def run_pipeline(cfg: TrainConfig) -> Dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    t_all = time.time()

    raw = load_train_frame(cfg)
    df, features = clean_features(raw, cfg)
    del raw
    train, valid, test = split_by_date(df, cfg)

    cfg.params = dict(cfg.params)

    tune_result: Dict[str, Any] = {}
    best_params: Dict[str, Dict[str, Any]] = {"y_high": {}, "y_low": {}}
    if cfg.tune:
        for lab in ("y_high", "y_low"):
            tune_result[lab] = simple_tune(train, valid, features, lab, cfg)
            best_params[lab] = dict(tune_result[lab]["params"])

    boosters: Dict[str, Any] = {}
    valid_metrics: Dict[str, Any] = {}
    for lab in ("y_high", "y_low"):
        model, metrics = train_one_target(
            train, valid, features, lab, cfg, params=best_params.get(lab) or None
        )
        boosters[lab] = model
        valid_metrics[lab] = metrics

    test_metrics = {
        lab: evaluate_test(boosters[lab], test, features, lab) for lab in ("y_high", "y_low")
    }

    # 特征重要性（gain）
    importance = {}
    for lab, model in boosters.items():
        gain = model.feature_importance(importance_type="gain")
        pairs = sorted(
            zip(features, [float(x) for x in gain]),
            key=lambda x: x[1],
            reverse=True,
        )
        importance[lab] = [{"feature": a, "gain": b} for a, b in pairs[:30]]

    report = {
        "ok": True,
        "mode": cfg.mode,
        "rows": {"train": len(train), "valid": len(valid), "test": len(test), "features": len(features)},
        "split": asdict(cfg.split),
        "valid": valid_metrics,
        "test": test_metrics,
        "tune": tune_result,
        "top_features": importance,
        "elapsed_s": round(time.time() - t_all, 1),
        "how_to_predict_one_stock": (
            "加载 meta.json + y_high.txt/y_low.txt；"
            "取该股最新 train_ready=1 行的同一套 features；"
            "booster.predict(X) 得到次日高低点相对当日收盘的预测幅度。"
        ),
    }
    out = save_artifacts(Path(cfg.out_dir), boosters, features, cfg, report)
    report["out_dir"] = str(out)
    logger.info("saved to %s (%.1fs)", out, report["elapsed_s"])
    return report


FULL_OUT_DIR = ROOT / "data" / "ml_models" / "lgbm_nextday_hl_full"


def resolve_model_dir(model_dir: Optional[Path] = None) -> Path:
    """优先用全量模型目录，否则回退 smoke 目录。"""
    if model_dir is not None:
        return Path(model_dir)
    if (FULL_OUT_DIR / "meta.json").exists() and (FULL_OUT_DIR / "y_high.txt").exists():
        return FULL_OUT_DIR
    return Path(DEFAULT_OUT_DIR)


def _prepare_infer_row(df: pd.DataFrame) -> pd.DataFrame:
    """推理清洗：编码类别列，不因缺少次日标签而丢行。"""
    out = df.copy()
    if "session_flag" in out.columns:
        out["session_flag"] = (
            out["session_flag"].map(SESSION_MAP).fillna(-1).astype(np.int16)
        )
    if "board_type" in out.columns:
        out["board_type"] = (
            out["board_type"].map(BOARD_MAP).fillna(-1).astype(np.int16)
        )
    skip = {
        "symbol", "trade_date", "session_flag", "board_type",
        "industry_code", "industry_name", "industry_l1",
    }
    for c in out.columns:
        if c in skip:
            continue
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def build_nextday_advice(
    close: float,
    pred_y_high: float,
    pred_y_low: float,
    *,
    mae_high: float = 0.017,
    mae_low: float = 0.014,
    session_flag: Any = None,
) -> Dict[str, Any]:
    """
    把相对幅度转成次日参考价位与操作建议。

    模型输出是排序信号，建议是「参考区间」，不是买卖指令。
    """
    pred_high = close * (1.0 + pred_y_high)
    pred_low = close * (1.0 + pred_y_low)
    upside = max(pred_y_high, 0.0)
    downside = max(-pred_y_low, 0.0)
    span = pred_y_high - pred_y_low
    if upside >= downside * 1.35 and upside >= 0.012:
        bias = "偏多"
        bias_code = "bullish"
    elif downside >= upside * 1.35 and downside >= 0.012:
        bias = "偏空"
        bias_code = "bearish"
    elif span < 0.015:
        bias = "窄幅"
        bias_code = "narrow"
    else:
        bias = "震荡"
        bias_code = "range"

    buy_zone_lo = close * (1.0 + pred_y_low - mae_low)
    buy_zone_hi = close * (1.0 + pred_y_low + mae_low * 0.5)
    sell_zone_lo = close * (1.0 + pred_y_high - mae_high * 0.5)
    sell_zone_hi = close * (1.0 + pred_y_high + mae_high)

    halt_like = str(session_flag) in ("一字涨停", "一字跌停", "平盘一字", "停牌", "1", "2", "3", "4")
    if halt_like:
        action = "观望"
        summary = "当日为一字板或停牌类会话，次日高低点模型参考价值下降，建议观望。"
    elif bias_code == "bullish":
        action = "低吸观察"
        summary = (
            f"模型看次日上冲空间（约 {upside*100:.1f}%）大于下探（约 {downside*100:.1f}%）。"
            f"可把 {buy_zone_lo:.2f}–{buy_zone_hi:.2f} 当作回踩观察区，"
            f"{sell_zone_lo:.2f}–{sell_zone_hi:.2f} 当作冲高减仓参考；勿追高。"
        )
    elif bias_code == "bearish":
        action = "控制回撤"
        summary = (
            f"模型看次日下探风险（约 {downside*100:.1f}%）大于上冲（约 {upside*100:.1f}%）。"
            f"不宜追涨；若持仓可把 {sell_zone_lo:.2f} 附近当作反弹减仓参考，"
            f"跌破 {pred_low:.2f} 需警惕继续走弱。"
        )
    elif bias_code == "narrow":
        action = "轻仓或跳过"
        summary = (
            f"预测振幅偏窄（约 {span*100:.1f}%），更像缩量整理日。"
            f"参考区间 {pred_low:.2f}–{pred_high:.2f}，性价比一般，可轻仓或跳过。"
        )
    else:
        action = "高抛低吸"
        summary = (
            f"预测次日高低落差约 {span*100:.1f}%，偏震荡。"
            f"可在 {buy_zone_lo:.2f}–{buy_zone_hi:.2f} 附近低吸观察，"
            f"在 {sell_zone_lo:.2f}–{sell_zone_hi:.2f} 附近高抛；中轴附近不追。"
        )

    return {
        "bias": bias,
        "bias_code": bias_code,
        "action": action,
        "summary": summary,
        "pred_high_price": round(pred_high, 4),
        "pred_low_price": round(pred_low, 4),
        "buy_zone": [round(buy_zone_lo, 4), round(buy_zone_hi, 4)],
        "sell_zone": [round(sell_zone_lo, 4), round(sell_zone_hi, 4)],
        "upside_pct": round(upside * 100, 3),
        "downside_pct": round(downside * 100, 3),
        "span_pct": round(span * 100, 3),
        "mae_band": {"y_high": mae_high, "y_low": mae_low},
        "disclaimer": "相对排序信号转成的参考价位，含约 1.5pp MAE 误差带；非投资建议。",
    }


def predict_symbol(
    ts_code: str,
    model_dir: Optional[Path] = None,
    trade_date: Optional[str] = None,
    *,
    allow_unlabeled: bool = True,
) -> Dict[str, Any]:
    """
    对单只股票做次日高低点推理。

    allow_unlabeled=True（默认）：可用「最新收盘日」行，即使尚无次日标签
    （train_ready 常为 0）。历史回放指定 trade_date 时也可对照 actual。
    """
    import lightgbm as lgb

    model_dir = resolve_model_dir(model_dir)
    meta_path = model_dir / "meta.json"
    if not meta_path.exists() or not (model_dir / "y_high.txt").exists():
        return {
            "ok": False,
            "error": f"模型不存在：{model_dir}。请先跑全量训练。",
            "model_dir": str(model_dir),
        }

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    features: List[str] = list(meta["features"])
    test_metrics = ((meta.get("report") or {}).get("test") or {})
    mae_high = float((test_metrics.get("y_high") or {}).get("mae") or 0.017)
    mae_low = float((test_metrics.get("y_low") or {}).get("mae") or 0.014)

    eng = _engine()
    code = str(ts_code).strip().upper()
    params: Dict[str, Any] = {"c": code}
    date_sql = "AND trade_date = :d" if trade_date else ""
    if trade_date:
        params["d"] = trade_date

    # 实时预测：不要强制 train_ready=1 / y 非空（末日常无次日标签）
    if allow_unlabeled:
        ready_filter = "AND COALESCE(is_halted, 0) = 0 AND ret_60 IS NOT NULL"
    else:
        ready_filter = "AND train_ready = 1 AND y_high IS NOT NULL AND y_low IS NOT NULL"
    order = "ORDER BY trade_date DESC LIMIT 1" if not trade_date else ""
    sql = f"""
        SELECT * FROM ml_train_daily
        WHERE symbol = :c
          {ready_filter}
          {date_sql}
        {order}
    """
    with eng.connect() as conn:
        df = pd.read_sql(sa_text(sql), conn, params=params)
    if df.empty:
        # 回退：有标签的最近一行（便于对照）
        sql2 = f"""
            SELECT * FROM ml_train_daily
            WHERE symbol = :c AND train_ready = 1
              AND y_high IS NOT NULL AND y_low IS NOT NULL
              {date_sql}
            ORDER BY trade_date DESC LIMIT 1
        """
        with eng.connect() as conn:
            df = pd.read_sql(sa_text(sql2), conn, params=params)
    if df.empty:
        return {
            "ok": False,
            "error": (
                f"库中无 {code} 可用特征行。请先保证 ml_train_daily 有该股日线特征"
                "（收盘后 fill_complete / 截面补齐），并确认代码含后缀如 000001.SZ。"
            ),
            "symbol": code,
            "required_inputs": required_predict_inputs(),
        }

    out_df = _prepare_infer_row(df)
    row = out_df.iloc[[0]]
    close_raw = row["close"].iloc[0] if "close" in row.columns else None
    if close_raw is None or pd.isna(close_raw) or float(close_raw) <= 0:
        return {"ok": False, "error": "该行缺少有效收盘价 close，无法换算绝对高低点"}

    close = float(close_raw)
    X = row.reindex(columns=features)
    missing = [c for c in features if c not in row.columns or pd.isna(X[c].iloc[0])]
    missing_ratio = len(missing) / max(len(features), 1)

    pred: Dict[str, float] = {}
    for lab in ("y_high", "y_low"):
        booster = lgb.Booster(model_file=str(model_dir / f"{lab}.txt"))
        pred[lab] = float(booster.predict(X)[0])

    # 物理约束：高不应低于低
    if pred["y_high"] < pred["y_low"]:
        mid = 0.5 * (pred["y_high"] + pred["y_low"])
        pred["y_high"], pred["y_low"] = mid, mid

    session_raw = df["session_flag"].iloc[0] if "session_flag" in df.columns else None
    advice = build_nextday_advice(
        close, pred["y_high"], pred["y_low"],
        mae_high=mae_high, mae_low=mae_low, session_flag=session_raw,
    )

    actual_h = row["y_high"].iloc[0] if "y_high" in row.columns else None
    actual_l = row["y_low"].iloc[0] if "y_low" in row.columns else None
    has_actual = (
        actual_h is not None and pd.notna(actual_h)
        and actual_l is not None and pd.notna(actual_l)
    )

    return {
        "ok": True,
        "symbol": code,
        "trade_date": str(row["trade_date"].iloc[0])[:10],
        "asof_close": round(close, 4),
        "session_flag": None if session_raw is None or (isinstance(session_raw, float) and pd.isna(session_raw)) else str(session_raw),
        "model_dir": str(model_dir),
        "model_mode": (meta.get("config") or {}).get("mode"),
        "pred": {
            "y_high": round(pred["y_high"], 6),
            "y_low": round(pred["y_low"], 6),
            "y_high_pct": round(pred["y_high"] * 100, 3),
            "y_low_pct": round(pred["y_low"] * 100, 3),
        },
        "actual": {
            "y_high": float(actual_h) if has_actual else None,
            "y_low": float(actual_l) if has_actual else None,
        } if has_actual else None,
        "levels": {
            "pred_high": advice["pred_high_price"],
            "pred_low": advice["pred_low_price"],
            "buy_zone": advice["buy_zone"],
            "sell_zone": advice["sell_zone"],
        },
        "advice": advice,
        "feature_quality": {
            "n_features": len(features),
            "missing_count": len(missing),
            "missing_ratio": round(missing_ratio, 4),
            "missing_top": missing[:12],
            "warn": missing_ratio > 0.15,
        },
        "required_inputs": required_predict_inputs(),
        "test_metrics": {
            "y_high_mae": mae_high,
            "y_low_mae": mae_low,
            "y_high_ic": (test_metrics.get("y_high") or {}).get("ic_mean"),
            "y_low_ic": (test_metrics.get("y_low") or {}).get("ic_mean"),
        },
    }


def required_predict_inputs() -> Dict[str, Any]:
    """说明收盘后做次日预测需要哪些数据（用户不必手填，应已在 ml_train_daily）。"""
    return {
        "user_enters": ["股票代码（如 000001.SZ）", "可选：作为特征日的 trade_date"],
        "must_exist_after_close": [
            "当日 OHLCV / 成交额（用于 close 与量价特征）",
            "滚动收益 ret_1…ret_60、均线比、ATR%、振幅、影线、换手",
            "市值/流通市值对数、板块 board_type、session_flag",
            "市场截面：沪深300/中证1000 收益、涨跌家数、涨跌停家数等",
            "行业截面：行业收益、广度、个股行业内排名",
        ],
        "label_note": (
            "y_high/y_low 是训练标签（次日高低/当日收盘-1），"
            "预测当日尚无次日时可以为空；模型只吃特征列。"
        ),
        "output": [
            "pred y_high / y_low（相对当日收盘）",
            "换算的次日参考最高/最低价",
            "低吸区 / 冲高减仓区 + 偏多/偏空/震荡建议",
        ],
    }


def model_status(model_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = resolve_model_dir(model_dir)
    meta_ok = (path / "meta.json").exists()
    high_ok = (path / "y_high.txt").exists()
    low_ok = (path / "y_low.txt").exists()
    report: Dict[str, Any] = {}
    features: List[str] = []
    if meta_ok:
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        features = list(meta.get("features") or [])
        report = meta.get("report") or {}
    return {
        "ok": meta_ok and high_ok and low_ok,
        "model_dir": str(path),
        "files": {"meta": meta_ok, "y_high": high_ok, "y_low": low_ok},
        "n_features": len(features),
        "mode": (report.get("mode") if report else None),
        "test": report.get("test") if report else None,
        "required_inputs": required_predict_inputs(),
    }
