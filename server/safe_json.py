"""JSON 响应：date/datetime 可序列化，避免 HTTPException 二次 500。"""

from __future__ import annotations

import json
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            jsonable_encoder(content),
            ensure_ascii=False,
            default=str,
            allow_nan=False,
        ).encode("utf-8")
