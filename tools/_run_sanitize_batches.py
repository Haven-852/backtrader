"""Continue ml_train_daily sanitize from row 200, 10000 rows per batch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ml_train_sanitize_toolkit import ml_train_sanitize_toolkit

OUT = ROOT / "data" / "ml_train_sanitize_batch_result.json"


def main() -> None:
    result = ml_train_sanitize_toolkit.run(
        "fix_batches",
        from_row=200,
        limit_rows=10000,
        skip_gaps=True,
        fill_gaps_after=True,
        reset=False,
    )
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("DONE", json.dumps({
        "ok": result.get("ok"),
        "status": result.get("status"),
        "batch_no": result.get("batch_no"),
        "done_rows": result.get("done_rows"),
        "total_rows": result.get("total_rows"),
        "elapsed_s": result.get("elapsed_s"),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
