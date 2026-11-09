"""
离线补全 ml_train_daily 截面特征（绕开对话超时 / sql_toolkit 只读）。

默认：每批 5 个交易日，只更新仍为 NULL 的行，进度可断点续跑。

用法（在仓库根目录、已配置 DB 的 venv 下）::

    python tools/_run_fill_cross_section.py
    python tools/_run_fill_cross_section.py --dates-per-batch 5 --reset
    python tools/_run_fill_cross_section.py --max-batches 50
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ml_train_sanitize_toolkit import ml_train_sanitize_toolkit

OUT = ROOT / "data" / "ml_train_cross_section_result.json"


def main() -> None:
    p = argparse.ArgumentParser(description="分批补 ml_train_daily 截面特征")
    p.add_argument("--dates-per-batch", type=int, default=5)
    p.add_argument("--max-batches", type=int, default=None, help="本轮最多批数；默认不限制")
    p.add_argument("--reset", action="store_true", help="忽略进度文件从头跑")
    p.add_argument(
        "--all-rows",
        action="store_true",
        help="不只补 NULL，强制重写本批全部行（默认只补 NULL）",
    )
    args = p.parse_args()

    result = ml_train_sanitize_toolkit.run(
        "fill_cross_section_batched",
        dates_per_batch=args.dates_per_batch,
        max_batches=args.max_batches,
        reset=args.reset,
        null_only=not args.all_rows,
        auto_cap_batches=False,  # 离线不封顶，一次可跑完全量
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(
        "DONE",
        json.dumps(
            {
                "ok": result.get("ok"),
                "status": result.get("status"),
                "batch_no": result.get("batch_no"),
                "last_trade_date": result.get("last_trade_date"),
                "pending_left_estimate": result.get("pending_left_estimate"),
                "updated_rank_total": result.get("updated_rank_total"),
                "elapsed_s": result.get("elapsed_s"),
                "result_file": str(OUT),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
