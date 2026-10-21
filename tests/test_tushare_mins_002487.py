#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
探测已配置的 Tushare 能否取到 002487.SZ 在 2026-09-30 的历史 1 分钟线。

Token 与接口地址从项目 .env 读取，不在代码里写密钥。
运行（项目根目录）:
    python -m unittest tests.test_tushare_mins_002487 -v
"""
import json
import sys
import unittest
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TS_CODE = "002487.SZ"
START = "2026-09-30 09:00:00"
END = "2026-09-30 15:00:00"


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
    load_dotenv(_ROOT / "backend" / ".env", override=False)


def _post(url: str, token: str, api_name: str, params: dict):
    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": "",
    }
    res = requests.post(url, json=payload, timeout=30)
    body = res.text or ""
    if token:
        body = body.replace(token, "***")
    parsed = None
    try:
        parsed = json.loads(res.text or "{}")
    except json.JSONDecodeError:
        parsed = None
    return res.status_code, body[:300], parsed


class TestTushareHistoricalMins(unittest.TestCase):
    """002487.SZ 2026-09-30 历史分时。"""

    def test_stk_mins_one_day(self):
        _load_env()
        from data_layer.config import DatabaseConfig

        cfg = DatabaseConfig()
        token = cfg.get_tushare_token("mins")
        if not token:
            self.fail("未配置 TUSHARE_TOKEN_MINS 或 TUSHARE_TOKEN，无法测试分钟接口")

        params = {"ts_code": TS_CODE, "freq": "1min", "start_date": START, "end_date": END}
        base = cfg.tushare_api_url.rstrip("/")
        status, preview, parsed = _post(f"{base}/stk_mins", token, "stk_mins", params)
        official_status, official_preview, _official = _post(
            "http://api.waditu.com/dataapi/stk_mins", token, "stk_mins", params
        )
        rows = 0
        msg = ""
        if isinstance(parsed, dict):
            msg = str(parsed.get("msg") or "")
            data = parsed.get("data") or {}
            items = data.get("items") if isinstance(data, dict) else None
            rows = len(items or [])

        self.assertEqual(
            status,
            200,
            f"项目配置的 Tushare 地址 {base} 没有正常返回。"
            f"HTTP {status}。官方接口 HTTP {official_status}：{official_preview}。"
            f"当前不能取到 {TS_CODE} {START[:10]} 的历史分时。代理响应: {preview}",
        )
        self.assertGreater(
            rows,
            0,
            f"stk_mins 已响应但没有数据行。code={None if not isinstance(parsed, dict) else parsed.get('code')} "
            f"msg={msg or '空'}。{TS_CODE} 在 2026-09-30 的历史 1 分钟线没有取到。",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
