"""
Datahubco OpenAPI HTTP 客户端
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("collector.datahubco")

DEFAULT_BASE = "http://datahubco.com/app-api/openapi/v1/tushare"


class DatahubcoClient:
    """仅通过 Datahubco 网关拉取 Tushare 兼容接口。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
    ):
        # 卖家连通性测试常要求绕过代理
        os.environ.setdefault("NO_PROXY", "*")
        os.environ.setdefault("no_proxy", "*")

        self.api_key = (api_key or os.getenv("DATAHUBCO_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("DATAHUBCO_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise ValueError(
                "未配置 DATAHUBCO_API_KEY。请在 .env 中设置，或构造时传入 api_key。"
            )
        logger.info("DatahubcoClient 就绪 · base=%s", self.base_url)

    @staticmethod
    def _error_msg(resp: requests.Response) -> str:
        try:
            payload = resp.json()
            return str(payload.get("msg") or resp.text[:300])
        except Exception:
            return (resp.text or "")[:300]

    def query(self, api: str, **params: Any) -> pd.DataFrame:
        """
        GET /{api} → DataFrame
        返回格式: {code, msg, data: {fields, items}}
        """
        api = api.strip().strip("/")
        url = f"{self.base_url}/{api}"
        # 去掉 None，避免污染查询
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        headers = {
            "X-API-Key": self.api_key,
            "X-Request-Id": f"backtrader-{api}",
        }
        resp = requests.get(url, headers=headers, params=clean, timeout=self.timeout)
        if resp.status_code == 400:
            msg = self._error_msg(resp)
            # GET 查询串有时吃掉中文必填项（如 idx_type=行业板块），改 POST JSON 再试一次。
            if "required parameter" in msg.lower() and clean:
                logger.warning(
                    "%s GET 缺参，改 POST json · keys=%s · %s",
                    api,
                    sorted(clean.keys()),
                    msg,
                )
                resp = requests.post(
                    url,
                    headers=headers,
                    json=clean,
                    timeout=self.timeout,
                )
        if not resp.ok:
            msg = self._error_msg(resp)
            raise RuntimeError(
                f"Datahubco {api} HTTP {resp.status_code}: {msg} · params={sorted(clean.keys())}"
            )
        payload = resp.json()
        code = payload.get("code")
        if code not in (0, "0", None):
            raise RuntimeError(f"Datahubco {api} 失败: code={code} msg={payload.get('msg')}")

        data = payload.get("data") or {}
        fields: List[str] = list(data.get("fields") or [])
        items: List[List[Any]] = list(data.get("items") or [])
        if not fields:
            logger.warning("%s 返回无 fields，items=%s", api, len(items))
            return pd.DataFrame()
        df = pd.DataFrame(items, columns=fields)
        src = resp.headers.get("X-Data-Source") or resp.headers.get("X-Cache") or ""
        logger.info("%s 拉取 %d 行 · source=%s", api, len(df), src)
        return df

    def ping(self, limit: int = 3) -> Dict[str, Any]:
        """连通性：stock-basic / stock_basic"""
        for name in ("stock_basic", "stock-basic"):
            try:
                df = self.query(name, limit=limit)
                return {"ok": True, "api": name, "rows": len(df), "columns": list(df.columns)}
            except Exception as e:
                last = e
        return {"ok": False, "error": str(last)}
