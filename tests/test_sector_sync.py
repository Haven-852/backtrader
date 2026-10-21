"""板块同步：dc_index 必填 idx_type、400 详情可 JSON 序列化。"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime

import pandas as pd
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException
from starlette.responses import JSONResponse

from data_layer.collectors.datahubco.sectors import (
    IDX_TYPE_INDUSTRY,
    SectorDataFetcher,
    _jsonable,
)
from server.safe_json import SafeJSONResponse


class _FakeClient:
    def __init__(self, require_idx_type: bool = True):
        self.require_idx_type = require_idx_type
        self.calls = []

    def query(self, api: str, **params):
        self.calls.append((api, dict(params)))
        if api == "dc_index" and self.require_idx_type and not params.get("idx_type"):
            raise RuntimeError("Datahubco dc_index HTTP 400: required parameter: idx_type")
        return pd.DataFrame(
            [
                {
                    "ts_code": "BK1035",
                    "trade_date": "20261020",
                    "name": "电子",
                    "idx_type": params.get("idx_type") or IDX_TYPE_INDUSTRY,
                    "pct_change": 1.25,
                    "level": "东财一级行业",
                }
            ]
        )


class FetchDcIndexTest(unittest.TestCase):
    def test_passes_idx_type(self):
        client = _FakeClient(require_idx_type=True)
        fetcher = SectorDataFetcher(client=client)
        df = fetcher.fetch_dc_index("20261020")
        self.assertEqual(client.calls[0][0], "dc_index")
        self.assertEqual(client.calls[0][1].get("idx_type"), IDX_TYPE_INDUSTRY)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["ts_code"], "BK1035.DC")

    def test_old_gateway_without_idx_type_still_works(self):
        client = _FakeClient(require_idx_type=False)

        def query(api, **params):
            client.calls.append((api, dict(params)))
            if params.get("idx_type"):
                raise RuntimeError("Datahubco dc_index HTTP 400: unknown parameter idx_type")
            return pd.DataFrame(
                [
                    {
                        "ts_code": "BK1035.DC",
                        "trade_date": "20261020",
                        "name": "电子",
                        "idx_type": "行业板块",
                        "pct_change": 0.5,
                    }
                ]
            )

        client.query = query
        fetcher = SectorDataFetcher(client=client)
        df = fetcher.fetch_dc_index("20261020")
        self.assertEqual(len(df), 1)
        self.assertTrue(any("idx_type" not in c[1] for c in client.calls))


class ApiUnavailableTest(unittest.TestCase):
    def test_required_parameter_is_not_dead(self):
        fetcher = SectorDataFetcher(client=_FakeClient())
        err = RuntimeError("Datahubco dc_index HTTP 400: required parameter: idx_type")
        self.assertFalse(fetcher._api_unavailable(err))

    def test_unknown_api_is_dead(self):
        fetcher = SectorDataFetcher(client=_FakeClient())
        err = RuntimeError("Datahubco foo HTTP 400: unknown api_name")
        self.assertTrue(fetcher._api_unavailable(err))


class JsonableTest(unittest.TestCase):
    def test_session_dates_are_strings(self):
        payload = {
            "ok": False,
            "session": {
                "asof": "2026-10-20",
                "asof_date": date(2026, 10, 20),
                "ymd": "20261020",
                "reason": "after_close_include_today",
            },
            "counts": {"sector_dc_index_daily": 0},
        }
        out = _jsonable(payload)
        self.assertEqual(out["session"]["asof_date"], "2026-10-20")
        json.dumps(out)

    def test_http_exception_detail_serializes(self):
        result = {
            "ok": False,
            "session": {
                "asof_date": date(2026, 10, 20),
                "when": datetime(2026, 10, 20, 16, 1, 12),
            },
        }
        exc = HTTPException(status_code=400, detail=jsonable_encoder(result))
        body = JSONResponse(status_code=exc.status_code, content=exc.detail).body
        parsed = json.loads(body)
        self.assertEqual(parsed["session"]["asof_date"], "2026-10-20")

    def test_safe_json_response_serializes_raw_date(self):
        body = SafeJSONResponse(
            status_code=400,
            content={"detail": {"asof_date": date(2026, 10, 20)}},
        ).body
        parsed = json.loads(body)
        self.assertEqual(parsed["detail"]["asof_date"], "2026-10-20")


class SectorKlineAndMembersTest(unittest.TestCase):
    def test_is_sector_code(self):
        from server.dash_service import DashService

        svc = DashService()
        self.assertTrue(svc._is_sector_code("BK0475.DC"))
        self.assertTrue(svc._is_sector_code("BK0475"))
        self.assertFalse(svc._is_sector_code("000001.SZ"))
        self.assertFalse(svc._is_sector_code("000001.SH"))

    def test_fetch_members_pins_requested_date(self):
        client = _FakeClient()

        def query(api, **params):
            client.calls.append((api, dict(params)))
            if api != "dc_member":
                return pd.DataFrame()
            return pd.DataFrame(
                [
                    {
                        "ts_code": "BK1035.DC",
                        "trade_date": "20260101",
                        "con_code": "000001.SZ",
                        "name": "平安银行",
                    }
                ]
            )

        client.query = query
        fetcher = SectorDataFetcher(client=client)
        fetcher._dc_dead = False
        df = fetcher.fetch_members("BK1035", "20261021")
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["trade_date"], date(2026, 10, 21))
        self.assertEqual(df.iloc[0]["ts_code"], "BK1035.DC")
        self.assertEqual(df.iloc[0]["con_code"], "000001.SZ")

    def test_eastmoney_members_include_all_and_quotes(self):
        captured = []

        class _Fetcher(SectorDataFetcher):
            def _em_http_json(self, url, params, retries=3, timeout=15.0):
                captured.append(params)
                return {
                    "data": {
                        "total": 1,
                        "diff": [
                            {
                                "f12": "000001",
                                "f13": "0",
                                "f14": "平安银行",
                                "f2": 10.25,
                                "f3": 1.5,
                                "f6": 123456789,
                            }
                        ],
                    }
                }

        df = _Fetcher(client=_FakeClient()).fetch_eastmoney_members("BK1035", "20261021")
        self.assertEqual(len(df), 1)
        self.assertEqual(captured[0]["fs"], "b:BK1035")
        self.assertNotIn("f:!50", captured[0]["fs"])
        self.assertEqual(df.iloc[0]["close"], 10.25)
        self.assertEqual(df.iloc[0]["pct_chg"], 1.5)
        self.assertEqual(df.iloc[0]["amount"], 123456789)
        self.assertEqual(df.iloc[0]["trade_date"], date(2026, 10, 21))


if __name__ == "__main__":
    unittest.main()
