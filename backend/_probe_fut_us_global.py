# -*- coding: utf-8 -*-
"""Probe Datahubco for metal/oil futures + US/global index/equity APIs."""
from __future__ import annotations

import json
import time
from pathlib import Path

from data_layer.collectors.datahubco.client import DatahubcoClient

client = DatahubcoClient()
out = []


def probe(name: str, cat: str, **params):
    t0 = time.time()
    try:
        df = client.query(name, **params)
        cols = list(df.columns)[:14]
        sample = []
        if len(df):
            row = df.iloc[0].to_dict()
            for k in list(row)[:10]:
                sample.append({k: row[k]})
        item = {
            "cat": cat,
            "api": name,
            "params": {k: v for k, v in params.items()},
            "ok": True,
            "rows": int(len(df)),
            "cols": cols,
            "sample": sample,
            "sec": round(time.time() - t0, 2),
        }
        print(f"OK  {cat:12} {name:18} rows={len(df):5} sec={item['sec']}")
    except Exception as e:
        item = {
            "cat": cat,
            "api": name,
            "params": {k: v for k, v in params.items()},
            "ok": False,
            "rows": 0,
            "cols": [],
            "error": str(e)[:320],
            "sec": round(time.time() - t0, 2),
        }
        print(f"FAIL {cat:12} {name:18} {item['error'][:140]} sec={item['sec']}")
    out.append(item)
    return item


# --- futures basic by exchange ---
for ex in ("SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX"):
    probe("fut_basic", f"期货主档-{ex}", exchange=ex, limit=30)

# metal / oil contracts
for code, label in [
    ("AU2506.SHF", "黄金"),
    ("AG2506.SHF", "白银"),
    ("CU2506.SHF", "铜"),
    ("SC2506.INE", "原油INE"),
    ("FU2505.SHF", "燃油"),
    ("LU2506.INE", "低硫燃油"),
    ("BC2506.INE", "国际铜"),
]:
    probe(
        "fut_daily",
        f"期货日线-{label}",
        ts_code=code,
        start_date="20250901",
        end_date="20251002",
        limit=10,
    )

probe("fut_daily", "期货日线-SHFE某日", trade_date="20250930", exchange="SHFE", limit=40)
probe("fut_daily", "期货日线-INE某日", trade_date="20250930", exchange="INE", limit=40)

for api, params in [
    ("fut_holding", {"trade_date": "20250930", "exchange": "SHFE", "limit": 10}),
    ("fut_settle", {"trade_date": "20250930", "exchange": "SHFE", "limit": 10}),
    ("fut_mapping", {"ts_code": "AU.SHF", "limit": 10}),
    ("fut_wsr", {"trade_date": "20250930", "exchange": "SHFE", "limit": 10}),
]:
    probe(api, "期货扩展", **params)

# global indices
for code, label in [
    ("IXIC", "纳斯达克"),
    ("SPX", "标普500"),
    ("DJI", "道琼斯"),
    ("HSI", "恒生"),
    ("N225", "日经225"),
    ("XIN9", "富时A50"),
]:
    probe(
        "index_global",
        f"全球指数-{label}",
        ts_code=code,
        start_date="20250901",
        end_date="20251002",
        limit=10,
    )

# US equities / calendar
probe("us_basic", "美股主档", limit=20)
probe("us_daily", "美股日线", ts_code="AAPL", start_date="20250901", end_date="20251002", limit=10)
probe("us_daily_adj", "美股复权", ts_code="AAPL", start_date="20250901", end_date="20251002", limit=10)
probe("us_tradecal", "美股日历", start_date="20250901", end_date="20251002", limit=20)

# FX / Shanghai Gold Exchange
probe("fx_daily", "外汇", ts_code="USDCNH.FXCM", start_date="20250901", end_date="20251002", limit=10)
probe("fx_obasic", "外汇主档", limit=20)
probe("sge_basic", "上金所主档", limit=20)
probe("sge_daily", "上金所日线", ts_code="Au99.99", start_date="20250901", end_date="20251002", limit=10)

# index_basic markets that sometimes include overseas
for mkt in ("CSI", "MSCI", "OTH"):
    probe("index_basic", f"指数主档-{mkt}", market=mkt, limit=10)

path = Path(__file__).with_name("_probe_fut_us_global.json")
path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
ok = sum(1 for x in out if x.get("ok") and (x.get("rows") or 0) > 0)
print(f"\nDONE ok_with_rows={ok}/{len(out)} -> {path}")
