"""
次日高低点运维定时器（进程内）

- 每个交易日收盘后：按股票池分批灌当日（含预热）特征到 ml_train_daily，批末 polish
- 每季度：全量重训 LightGBM，覆盖 lgbm_nextday_hl_full

环境变量（backend/.env）：
  NEXTDAY_HL_SCHEDULER_ENABLED=true
  NEXTDAY_HL_DAILY_HOUR=16
  NEXTDAY_HL_DAILY_MINUTE=40
  NEXTDAY_HL_BATCH_LIMIT=40
  NEXTDAY_HL_WARMUP_DAYS=180
  NEXTDAY_HL_QUARTERLY_ENABLED=true
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
_STATE_PATH = _ROOT / "data" / "ml_models" / "nextday_hl_scheduler_state.json"
SH = ZoneInfo("Asia/Shanghai")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _quarter_key(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


class NextdayHlScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.busy = False
        self.last_error: Optional[str] = None
        self.last_tick: Optional[str] = None
        self.last_daily: Optional[Dict[str, Any]] = None
        self.last_train: Optional[Dict[str, Any]] = None

    def _load_state(self) -> Dict[str, Any]:
        if not _STATE_PATH.exists():
            return {"daily": {}, "quarterly": {}}
        try:
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"daily": {}, "quarterly": {}}

    def _save_state(self, state: Dict[str, Any]) -> None:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def enabled(self) -> bool:
        return _env_bool("NEXTDAY_HL_SCHEDULER_ENABLED", False)

    def status(self) -> Dict[str, Any]:
        state = self._load_state()
        now = datetime.now(SH)
        return {
            "ok": True,
            "enabled": self.enabled(),
            "running": self._running and self._task is not None and not self._task.done(),
            "busy": self.busy,
            "now_sh": now.isoformat(timespec="seconds"),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "daily_at": f"{_env_int('NEXTDAY_HL_DAILY_HOUR', 16):02d}:{_env_int('NEXTDAY_HL_DAILY_MINUTE', 40):02d}",
            "batch_limit": _env_int("NEXTDAY_HL_BATCH_LIMIT", 40),
            "warmup_days": _env_int("NEXTDAY_HL_WARMUP_DAYS", 180),
            "quarterly_enabled": _env_bool("NEXTDAY_HL_QUARTERLY_ENABLED", True),
            "state": state,
            "last_daily": self.last_daily,
            "last_train": self.last_train,
            "state_file": str(_STATE_PATH),
            "note": (
                "交易日收盘后分批灌全市场当日特征；"
                "每季度自动全量重训（需 NEXTDAY_HL_SCHEDULER_ENABLED=true）。"
            ),
        }

    def resolve_asof(self) -> date:
        from server.nextday_hl_service import nextday_hl_service

        return nextday_hl_service.resolve_asof(None)

    def should_run_daily(self, now: Optional[datetime] = None) -> bool:
        if not self.enabled():
            return False
        now = now or datetime.now(SH)
        hh = _env_int("NEXTDAY_HL_DAILY_HOUR", 16)
        mm = _env_int("NEXTDAY_HL_DAILY_MINUTE", 40)
        if (now.hour, now.minute) < (hh, mm):
            return False
        from server.nextday_hl_service import nextday_hl_service

        asof = self.resolve_asof()
        if not nextday_hl_service.is_trading_day(asof):
            return False
        state = self._load_state()
        daily = state.get("daily") or {}
        if daily.get("asof") == asof.isoformat() and daily.get("done"):
            return False
        return True

    def should_run_quarterly(self, now: Optional[datetime] = None) -> bool:
        if not self.enabled() or not _env_bool("NEXTDAY_HL_QUARTERLY_ENABLED", True):
            return False
        now = now or datetime.now(SH)
        # 季度首月 15 日之后、凌晨 02:00–05:59 跑一次（避开日更高峰）
        if now.month not in (1, 4, 7, 10):
            return False
        if now.day < 15:
            return False
        if not (2 <= now.hour <= 5):
            return False
        q = _quarter_key(now.date())
        state = self._load_state()
        last = (state.get("quarterly") or {}).get("last_quarter")
        return last != q

    def run_daily_batch(self, *, force: bool = False) -> Dict[str, Any]:
        """灌一批股票的 asof 特征；可多次调用直到 done。"""
        from tools.ml_train_toolkit import MlTrainToolkit

        if self.busy and not force:
            return {"ok": False, "error": "调度器忙", "busy": True}

        asof = self.resolve_asof()
        warmup = _env_int("NEXTDAY_HL_WARMUP_DAYS", 180)
        limit = _env_int("NEXTDAY_HL_BATCH_LIMIT", 40)
        start = asof - timedelta(days=warmup)
        state = self._load_state()
        daily = state.get("daily") or {}
        if daily.get("asof") != asof.isoformat():
            daily = {
                "asof": asof.isoformat(),
                "offset": 0,
                "done": False,
                "ok_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
            }

        if daily.get("done") and not force:
            return {"ok": True, "done": True, "asof": asof.isoformat(), "message": "当日已完成"}

        if force and daily.get("done"):
            daily["offset"] = 0
            daily["done"] = False

        self.busy = True
        try:
            toolkit = MlTrainToolkit()
            offset = int(daily.get("offset") or 0)
            result = toolkit.fill_batch(
                start_date=start.isoformat(),
                end_date=asof.isoformat(),
                limit=limit,
                offset=offset,
                skip_existing=False,
                skip_env_check=False,
            )
            if not result.get("ok"):
                self.last_error = result.get("error")
                daily["last_error"] = result.get("error")
                state["daily"] = daily
                self._save_state(state)
                return result

            daily["offset"] = int(result.get("next_offset") or offset)
            daily["universe_total"] = result.get("universe_total")
            daily["ok_count"] = int(daily.get("ok_count") or 0) + int(result.get("ok_count") or 0)
            daily["failed_count"] = int(daily.get("failed_count") or 0) + int(result.get("failed_count") or 0)
            daily["skipped_count"] = int(daily.get("skipped_count") or 0) + int(result.get("skipped_count") or 0)
            daily["done"] = not bool(result.get("has_more"))
            daily["updated_at"] = datetime.now(SH).isoformat(timespec="seconds")
            daily["last_batch"] = {
                "offset": offset,
                "batch_size": result.get("batch_size"),
                "ok_count": result.get("ok_count"),
                "failed_count": result.get("failed_count"),
            }
            if daily["done"]:
                # 收尾：截面补洞
                try:
                    from tools.ml_train_sanitize_toolkit import MlTrainSanitizeToolkit

                    xs = MlTrainSanitizeToolkit().fill_cross_section_batched(
                        dates_per_batch=5,
                        null_only=True,
                        max_batches=10,
                        auto_cap_batches=False,
                    )
                    daily["cross_section"] = {
                        "status": xs.get("status"),
                        "batches_this_run": xs.get("batches_this_run"),
                    }
                except Exception as e:
                    daily["cross_section"] = {"ok": False, "error": str(e)}

            state["daily"] = daily
            self._save_state(state)
            out = {
                "ok": True,
                "asof": asof.isoformat(),
                "done": daily["done"],
                "offset": daily["offset"],
                "universe_total": daily.get("universe_total"),
                "progress": daily,
                "batch": result,
            }
            self.last_daily = out
            self.last_error = None
            return out
        except Exception as e:
            logger.exception("daily fill batch failed")
            self.last_error = str(e)
            return {"ok": False, "error": str(e)}
        finally:
            self.busy = False

    def run_quarterly_train(self, *, force: bool = False) -> Dict[str, Any]:
        from tools.ml_lgbm_pipeline import FULL_OUT_DIR, SplitConfig, TrainConfig, run_pipeline

        if self.busy and not force:
            return {"ok": False, "error": "调度器忙", "busy": True}
        now = datetime.now(SH)
        q = _quarter_key(now.date())
        state = self._load_state()
        if not force and (state.get("quarterly") or {}).get("last_quarter") == q:
            return {"ok": True, "skipped": True, "quarter": q, "message": "本季度已训练"}

        self.busy = True
        try:
            # 时间切分：Valid 约近 1 年，Test 约近半年相对 Valid
            # 用「今天往前」动态切，避免写死 2023/2024
            today = now.date()
            valid_end = (today - timedelta(days=180)).isoformat()
            train_end = (today - timedelta(days=180 + 365)).isoformat()
            cfg = TrainConfig(
                mode="full",
                out_dir=str(FULL_OUT_DIR),
                split=SplitConfig(train_end=train_end, valid_end=valid_end),
                drop_halt_session=True,
                tune=False,
            )
            logger.info("quarterly LGBM train start quarter=%s train_end=%s valid_end=%s", q, train_end, valid_end)
            report = run_pipeline(cfg)
            state["quarterly"] = {
                "last_quarter": q,
                "last_train_at": now.isoformat(timespec="seconds"),
                "train_end": train_end,
                "valid_end": valid_end,
                "out_dir": str(FULL_OUT_DIR),
                "rows": report.get("rows"),
                "test": report.get("test"),
                "elapsed_s": report.get("elapsed_s"),
            }
            self._save_state(state)
            out = {"ok": True, "quarter": q, "report_summary": state["quarterly"]}
            self.last_train = out
            self.last_error = None
            return out
        except Exception as e:
            logger.exception("quarterly train failed")
            self.last_error = str(e)
            return {"ok": False, "error": str(e), "quarter": q}
        finally:
            self.busy = False

    async def _loop(self) -> None:
        logger.info("nextday_hl scheduler loop started")
        while self._running:
            try:
                self.last_tick = datetime.now(SH).isoformat(timespec="seconds")
                if self.should_run_daily() and not self.busy:
                    await asyncio.to_thread(self.run_daily_batch)
                if self.should_run_quarterly() and not self.busy:
                    # 日更未完成时先不抢重训
                    state = self._load_state()
                    daily = state.get("daily") or {}
                    if daily.get("done") or not self.should_run_daily():
                        await asyncio.to_thread(self.run_quarterly_train)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)
                logger.exception("nextday_hl scheduler tick failed")
            await asyncio.sleep(60)
        logger.info("nextday_hl scheduler loop stopped")

    def start(self) -> Dict[str, Any]:
        if self._task and not self._task.done():
            return {"ok": True, "message": "已在运行"}
        self._running = True
        self._task = asyncio.create_task(self._loop())
        return {"ok": True, "message": "已启动", "enabled": self.enabled()}

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


nextday_hl_scheduler = NextdayHlScheduler()
