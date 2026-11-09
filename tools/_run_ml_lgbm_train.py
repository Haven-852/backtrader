"""
全市场 LightGBM 次日高低点训练入口。

阶段一览：
  取数(train_ready=1) → 清洗编码 → 按日切分 → 训 y_high/y_low
  → Valid early stop → Test 评估(IC) → 可选小网格调参 → 落盘

示例::

  # 小样本打通流程（推荐先跑）
  python tools/_run_ml_lgbm_train.py --mode smoke

  # 带简易调参
  python tools/_run_ml_lgbm_train.py --mode smoke --tune

  # 全市场（内存要够，可能需 16G+）
  python tools/_run_ml_lgbm_train.py --mode full

  # 单票推理（需先训练）
  python tools/_run_ml_lgbm_train.py --predict 000001.SZ
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ml_lgbm_pipeline import (  # noqa: E402
    DEFAULT_OUT_DIR,
    SplitConfig,
    TrainConfig,
    predict_symbol,
    run_pipeline,
)


def main() -> None:
    logging_setup = __import__("logging")
    logging_setup.basicConfig(
        level=logging_setup.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
        handlers=[
            logging_setup.StreamHandler(sys.stdout),
            logging_setup.FileHandler(
                ROOT / "data" / "ml_models" / "lgbm_full_train.log",
                encoding="utf-8",
                mode="a",
            ),
        ],
        force=True,
    )
    p = argparse.ArgumentParser(description="全市场 LightGBM 次日高低点训练")
    p.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    p.add_argument("--smoke-start", default="2023-01-01")
    p.add_argument("--smoke-end", default="2024-12-31")
    p.add_argument("--train-end", default="2023-06-30")
    p.add_argument("--valid-end", default="2024-06-30")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--tune", action="store_true", help="Valid 上小网格调参")
    p.add_argument("--keep-halt", action="store_true", help="保留停牌日样本")
    p.add_argument("--predict", default="", help="对单票推理，如 000001.SZ")
    p.add_argument("--predict-date", default="", help="指定交易日 YYYY-MM-DD")
    args = p.parse_args()

    if args.predict:
        res = predict_symbol(
            args.predict,
            model_dir=Path(args.out_dir),
            trade_date=args.predict_date or None,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        sys.exit(0 if res.get("ok") else 1)

    cfg = TrainConfig(
        mode=args.mode,
        smoke_start=args.smoke_start,
        smoke_end=args.smoke_end,
        out_dir=args.out_dir,
        split=SplitConfig(train_end=args.train_end, valid_end=args.valid_end),
        drop_halt_session=not args.keep_halt,
        tune=bool(args.tune),
    )
    report = run_pipeline(cfg)
    summary = {
        "ok": report.get("ok"),
        "mode": report.get("mode"),
        "rows": report.get("rows"),
        "valid": {
            k: {
                "mae": v.get("mae"),
                "rmse": v.get("rmse"),
                "ic_mean": v.get("ic_mean"),
                "best_iteration": v.get("best_iteration"),
            }
            for k, v in (report.get("valid") or {}).items()
            if isinstance(v, dict)
        },
        "test": {
            k: {
                "mae": v.get("mae"),
                "rmse": v.get("rmse"),
                "ic_mean": v.get("ic_mean"),
                "ic_days": v.get("ic_days"),
            }
            for k, v in (report.get("test") or {}).items()
            if isinstance(v, dict)
        },
        "out_dir": report.get("out_dir"),
        "elapsed_s": report.get("elapsed_s"),
        "top5_y_high": (report.get("top_features") or {}).get("y_high", [])[:5],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(f"\n完整报告: {Path(args.out_dir) / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
