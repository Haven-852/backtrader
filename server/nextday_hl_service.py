"""
次日高低点 · 日频 LightGBM 推理服务。

预测前可自动：datahubco fill_complete（含预热）→ 表内截面 polish → 推理。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
SH = ZoneInfo("Asia/Shanghai")
WARMUP_CAL_DAYS = 180


class NextdayHlService:
    def status(self) -> Dict[str, Any]:
        from tools.ml_lgbm_pipeline import model_status

        st = model_status()
        try:
            from server.nextday_hl_scheduler import nextday_hl_scheduler

            st["scheduler"] = nextday_hl_scheduler.status()
        except Exception as e:
            st["scheduler"] = {"ok": False, "error": str(e)}
        return st

    def _engine(self):
        from data_layer.db_manager import storage_manager

        return storage_manager.get_postgres_engine()

    def is_trading_day(self, d: date) -> bool:
        eng = self._engine()
        if not eng:
            return d.weekday() < 5
        try:
            with eng.connect() as conn:
                row = conn.execute(
                    sa_text(
                        """
                        SELECT is_open FROM ref_trade_cal
                        WHERE cal_date = :d AND exchange = 'SSE'
                        LIMIT 1
                        """
                    ),
                    {"d": d},
                ).fetchone()
            if row is None:
                return d.weekday() < 5
            return int(row[0]) == 1
        except Exception:
            return d.weekday() < 5

    def resolve_asof(self, trade_date: Optional[str] = None) -> date:
        """特征日：指定日，或「已收盘的最近交易日」。"""
        if trade_date:
            return date.fromisoformat(str(trade_date)[:10])
        now = datetime.now(SH)
        d = now.date()
        # 未到 15:05 视为当日未收盘，用上一交易日
        if now.hour < 15 or (now.hour == 15 and now.minute < 5):
            d = d - timedelta(days=1)
        for _ in range(15):
            if self.is_trading_day(d):
                return d
            d -= timedelta(days=1)
        return now.date()

    def has_feature_row(self, ts_code: str, asof: date) -> Tuple[bool, Dict[str, Any]]:
        eng = self._engine()
        if not eng:
            return False, {"error": "无数据库连接"}
        with eng.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, close, ret_60, session_flag, train_ready,
                           hs300_ret_1, industry_ret_5
                    FROM ml_train_daily
                    WHERE symbol = :c AND trade_date = :d
                    LIMIT 1
                    """
                ),
                {"c": ts_code, "d": asof},
            ).fetchone()
        if not row:
            return False, {"reason": "missing_row"}
        close = row[1]
        ret60 = row[2]
        ok = close is not None and float(close) > 0 and ret60 is not None
        meta = {
            "trade_date": str(row[0])[:10],
            "close": float(close) if close is not None else None,
            "ret_60": float(ret60) if ret60 is not None else None,
            "session_flag": row[3],
            "train_ready": int(row[4] or 0),
            "hs300_ret_1": float(row[5]) if row[5] is not None else None,
            "industry_ret_5": float(row[6]) if row[6] is not None else None,
            "reason": None if ok else "features_incomplete",
        }
        return ok, meta

    def ensure_features(
        self,
        ts_code: str,
        asof: date,
        *,
        warmup_days: int = WARMUP_CAL_DAYS,
    ) -> Dict[str, Any]:
        """
        若 asof 日无可用特征行：datahubco fill_complete（预热窗口）+ 截面 polish。
        """
        from tools.ml_train_toolkit import MlTrainToolkit
        from tools.ml_train_sanitize_toolkit import MlTrainSanitizeToolkit

        code = ts_code.strip().upper()
        ok, meta = self.has_feature_row(code, asof)
        if ok:
            return {
                "ok": True,
                "filled": False,
                "asof": asof.isoformat(),
                "row": meta,
                "message": "特征行已存在，跳过补数",
            }

        start = asof - timedelta(days=max(60, int(warmup_days)))
        toolkit = MlTrainToolkit()
        steps: Dict[str, Any] = {}
        logger.info("nextday_hl ensure_features fill_complete %s %s..%s", code, start, asof)
        fill = toolkit.fill_complete(
            ts_code=code,
            start_date=start.isoformat(),
            end_date=asof.isoformat(),
            skip_env_check=False,
            allow_defaults=True,
            use_remote_cross_section=False,
        )
        steps["fill_complete"] = {
            "ok": fill.get("ok"),
            "error": fill.get("error"),
            "rows_upserted": (fill.get("steps") or {}).get("fill_one", {}).get("rows_upserted"),
            "train_ready_rows": fill.get("train_ready_rows"),
            "local_polish": (fill.get("steps") or {}).get("local_polish"),
        }

        # 再补近期截面（只跑少量批次，避免拖垮请求）
        try:
            san = MlTrainSanitizeToolkit()
            xs = san.fill_cross_section_batched(
                dates_per_batch=3,
                null_only=True,
                max_batches=3,
                auto_cap_batches=False,
                reset=False,
            )
            steps["fill_cross_section_batched"] = {
                "ok": xs.get("status") in ("completed", "paused", "running"),
                "status": xs.get("status"),
                "batches_this_run": xs.get("batches_this_run"),
                "updated_day_total": xs.get("updated_day_total"),
            }
        except Exception as e:
            steps["fill_cross_section_batched"] = {"ok": False, "error": str(e)}

        ok2, meta2 = self.has_feature_row(code, asof)
        return {
            "ok": ok2,
            "filled": True,
            "asof": asof.isoformat(),
            "row": meta2,
            "steps": steps,
            "message": (
                "已补数并 polish" if ok2
                else (
                    fill.get("error")
                    or meta2.get("reason")
                    or "补数后仍无可用特征行（可能当日行情尚未入库）"
                )
            ),
            "error": None if ok2 else (
                fill.get("error") or meta2.get("reason") or "补数后仍无可用特征行"
            ),
        }

    def predict(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        model_dir: Optional[str] = None,
        *,
        auto_fill: bool = True,
    ) -> Dict[str, Any]:
        from tools.ml_lgbm_pipeline import predict_symbol

        code = (ts_code or "").strip().upper()
        if not code:
            return {"ok": False, "error": "请输入股票代码，如 000001.SZ"}
        if "." not in code and code.isdigit() and len(code) == 6:
            return {
                "ok": False,
                "error": "请带交易所后缀，例如 000001.SZ / 600000.SH / 300001.SZ",
                "symbol": code,
            }

        asof = self.resolve_asof(trade_date)
        fill_info: Optional[Dict[str, Any]] = None
        if auto_fill:
            try:
                fill_info = self.ensure_features(code, asof)
                if fill_info.get("filled") and not fill_info.get("ok"):
                    # 仍尝试用库内最新行预测（可能 asof 行情未出，但有昨收特征）
                    logger.warning(
                        "ensure_features incomplete for %s@%s: %s",
                        code, asof, fill_info.get("error"),
                    )
            except Exception as e:
                logger.exception("auto_fill failed")
                fill_info = {"ok": False, "filled": True, "error": str(e), "asof": asof.isoformat()}

        md = Path(model_dir) if model_dir else None
        try:
            result = predict_symbol(
                code,
                model_dir=md,
                trade_date=asof.isoformat(),
                allow_unlabeled=True,
            )
        except Exception as e:
            logger.exception("nextday_hl predict failed")
            return {"ok": False, "error": str(e), "symbol": code, "auto_fill": fill_info}

        # 指定 asof 仍无行时，回退库内最新可用特征日（仅未手填日期时）
        if not result.get("ok") and not trade_date:
            try:
                result = predict_symbol(
                    code, model_dir=md, trade_date=None, allow_unlabeled=True,
                )
                if isinstance(result, dict) and result.get("ok"):
                    result["fallback_latest"] = True
            except Exception as e:
                return {"ok": False, "error": str(e), "symbol": code, "auto_fill": fill_info}

        if isinstance(result, dict):
            result["requested_asof"] = asof.isoformat()
            result["auto_fill"] = fill_info
        return result


nextday_hl_service = NextdayHlService()
