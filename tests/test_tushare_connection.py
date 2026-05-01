#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tushare 连通性测试

- 校验 tushare 包可导入
- 校验 TushareCollector 在无 Token 时的行为
- TUSHARE_TOKEN **仅从**项目根目录 `.env` 文件读取（不读系统环境变量中的同名变量，避免与 `.env` 不一致）
- 若 `.env` 中配置了有效 TUSHARE_TOKEN，则调用 Pro 接口做最小请求验证

运行（在项目根目录 E:\\demo\\backtrader 下）:
    python -m unittest tests.test_tushare_connection -v
"""
import sys
import unittest
from pathlib import Path

# 项目根目录加入路径
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_ENV_FILE = _ROOT / ".env"


def read_tushare_token_from_dotenv() -> str:
    """
    从项目根目录 `.env` 读取 TUSHARE_TOKEN（仅解析该文件，不使用系统环境变量）。
    支持 `TUSHARE_TOKEN=xxx`、`export TUSHARE_TOKEN=xxx`，忽略空行与 `#` 注释行。
    """
    if not _ENV_FILE.is_file():
        return ""
    text = _ENV_FILE.read_text(encoding="utf-8", errors="ignore")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() != "TUSHARE_TOKEN":
            continue
        return val.strip().strip('"').strip("'")
    return ""


def _tushare_token_configured() -> bool:
    token = read_tushare_token_from_dotenv()
    return bool(token) and token != "your_tushare_token_here"


class TestTushareConnection(unittest.TestCase):
    """Tushare 安装与 API 连通性"""

    def test_tushare_package_importable(self):
        """tushare 包应可正常导入"""
        import tushare as ts  # noqa: F401

        self.assertTrue(hasattr(ts, "pro_api"))

    def test_tushare_collector_without_real_token(self):
        """未配置真实 Token 时，Collector 不应抛异常，且 pro 为 None"""
        from data_layer.collectors.tushare_collector import TushareCollector

        # 强制使用占位 token，避免本机 .env 影响本用例语义
        c = TushareCollector(token="your_tushare_token_here")
        self.assertIsNone(c.pro)
        out = c.get_daily("000001.SZ", start_date="20240101", end_date="20240110")
        self.assertIsNone(out)

    @unittest.skipUnless(
        _tushare_token_configured(),
        "Set TUSHARE_TOKEN in .env (not placeholder) to run live API tests",
    )
    def test_tushare_pro_api_daily(self):
        """配置 Token 后，Pro API 应能返回日线数据（daily 接口权限要求通常低于 trade_cal）"""
        import tushare as ts

        token = read_tushare_token_from_dotenv()
        pro = ts.pro_api(token)
        df = pro.daily(
            ts_code="000001.SZ",
            start_date="20251201",
            end_date="20251205",
            fields="ts_code,trade_date,open,high,low,close,vol",
        )
        self.assertIsNotNone(df)
        self.assertGreater(len(df), 0, "daily 应至少返回一行")
        self.assertIn("trade_date", df.columns)

    @unittest.skipUnless(
        _tushare_token_configured(),
        "Set TUSHARE_TOKEN in .env (not placeholder) to run live API tests",
    )
    def test_tushare_collector_get_daily_smoke(self):
        """通过项目内 TushareCollector 拉取少量日线（冒烟）"""
        from data_layer.collectors.tushare_collector import TushareCollector

        c = TushareCollector()
        self.assertIsNotNone(c.pro)
        df = c.get_daily("000001.SZ", start_date="20251201", end_date="20251231")
        self.assertIsNotNone(df)
        self.assertFalse(df.empty)
        self.assertIn("close", df.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
