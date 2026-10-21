"""
模拟交易 · 持仓监控告警

规则（默认可配）：
  - stop_loss: 相对成本亏损达阈值
  - take_profit: 相对成本浮盈达阈值
  - peak_pullback: 相对当日监控最高价回撤达阈值（冲高回落）
  - daily_drop / daily_rise: 相对昨收（pre）日内涨跌幅

推送通道（.env，任选）：
  WECOM_BOT_WEBHOOK / PUSHPLUS_TOKEN / ALERT_WEBHOOK_URL

默认只通知、不自动下单。可选 alert_auto_sell_stop 对止损自动挂卖单。
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

def _inline_md(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _markdown_to_html(source: str) -> str:
    """把简报 markdown 转成 PushPlus 能显示的 HTML，不再把 #、** 原样发出去。"""
    parts: List[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw_line in (source or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line in ("---", "***"):
            close_list()
            continue
        if line.startswith("### "):
            close_list()
            parts.append(f"<h3>{_inline_md(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            parts.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list()
            parts.append(f"<h1>{_inline_md(line[2:])}</h1>")
        elif line.startswith("- ") or line.startswith("* "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_inline_md(line[2:])}</li>")
        else:
            close_list()
            parts.append(f"<p>{_inline_md(line)}</p>")
    close_list()
    return "\n".join(parts)


def _req_id() -> str:
    import uuid
    return uuid.uuid4().hex


def _aibot_subscribe(bot_id: str, secret: str) -> Dict[str, Any]:
    return {
        "cmd": "aibot_subscribe",
        "headers": {"req_id": _req_id()},
        "body": {"bot_id": bot_id, "secret": secret},
    }


def _aibot_roundtrip(payloads: List[Dict[str, Any]], timeout: float = 12) -> List[Dict[str, Any]]:
    import asyncio
    import concurrent.futures
    import websockets

    async def _run() -> List[Dict[str, Any]]:
        replies: List[Dict[str, Any]] = []
        async with websockets.connect(
            "wss://openws.work.weixin.qq.com",
            open_timeout=timeout,
            ping_interval=None,
            ping_timeout=None,
        ) as ws:
            for payload in payloads:
                await ws.send(json.dumps(payload, ensure_ascii=False))
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                replies.append(json.loads(raw))
        return replies

    def _run_sync() -> List[Dict[str, Any]]:
        return asyncio.run(_run())

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_sync()
    # 强制日报走 FastAPI 的事件循环，不能在同一线程再 asyncio.run。
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run_sync).result(timeout=timeout * (len(payloads) + 1) + 8)


logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")

ALERT_DDL = [
    """
    CREATE TABLE IF NOT EXISTS paper_alert_event (
        id              SERIAL PRIMARY KEY,
        account_id      INT NOT NULL,
        ts_code         VARCHAR(24),
        name            VARCHAR(64),
        rule_id         VARCHAR(32) NOT NULL,
        severity        VARCHAR(16) DEFAULT 'info',
        title           VARCHAR(160),
        message         TEXT,
        price           NUMERIC(16,4),
        ref_price       NUMERIC(16,4),
        pct             NUMERIC(12,6),
        payload         JSONB,
        notified        BOOLEAN DEFAULT FALSE,
        notify_result   TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_alert_dedupe (
        account_id      INT NOT NULL,
        ts_code         VARCHAR(24) NOT NULL,
        rule_id         VARCHAR(32) NOT NULL,
        trade_date      DATE NOT NULL,
        last_fired_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (account_id, ts_code, rule_id, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_alert_state (
        account_id      INT NOT NULL,
        ts_code         VARCHAR(24) NOT NULL,
        trade_date      DATE NOT NULL,
        day_high        NUMERIC(16,4),
        day_low         NUMERIC(16,4),
        last_price      NUMERIC(16,4),
        updated_at      TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (account_id, ts_code, trade_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_paper_alert_event_acct ON paper_alert_event (account_id, created_at DESC)",
]

# 写入 paper_cycle_config 的键
ALERT_CONFIG_DEFAULTS: Dict[str, Any] = {
    "alert_auto_enabled": False,
    "alert_stop_loss_pct": -0.05,
    "alert_take_profit_pct": 0.08,
    "alert_peak_pullback_pct": -0.03,
    "alert_daily_drop_pct": -0.04,
    "alert_daily_rise_pct": 0.06,
    "alert_cooldown_minutes": 30,
    "alert_enable_stop_loss": True,
    "alert_enable_take_profit": True,
    "alert_enable_peak_pullback": True,
    "alert_enable_daily_drop": True,
    "alert_enable_daily_rise": True,
    "alert_auto_sell_stop": False,
    "alert_last_scan": None,
    "alert_last_scan_bucket": None,
    "alert_channels": "auto",  # auto | wecom,pushplus,webhook
}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        if x != x:
            return default
        return x
    except (TypeError, ValueError):
        return default


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace("/", "-")
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _http_json(url: str, payload: Dict[str, Any], timeout: int = 12) -> Tuple[bool, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, body[:500]
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = str(e)
        return False, f"HTTP {e.code}: {detail}"
    except Exception as e:
        return False, str(e)[:300]


class PaperAlertService:
    def __init__(self) -> None:
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            from server.storage_service import storage_manager

            self._engine = storage_manager.get_postgres_engine()
            if self._engine is None:
                raise RuntimeError("PostgreSQL 不可用")
        return self._engine

    def _ensure(self) -> None:
        if self._ready:
            return
        with self.engine.begin() as conn:
            for ddl in ALERT_DDL:
                conn.execute(sa_text(ddl))
        # 把默认告警键并入 cycle config
        from server.paper_cycle_service import paper_cycle_service

        paper_cycle_service._ensure()
        cfg = paper_cycle_service.get_config().get("config") or {}
        patch = {k: v for k, v in ALERT_CONFIG_DEFAULTS.items() if k not in cfg}
        if patch:
            # update_config 只接受 DEFAULT_CONFIG 里的键；先扩展 DEFAULT_CONFIG
            from server import paper_cycle_service as pcs

            for k, v in ALERT_CONFIG_DEFAULTS.items():
                if k not in pcs.DEFAULT_CONFIG:
                    pcs.DEFAULT_CONFIG[k] = v
            paper_cycle_service.update_config(patch)
        else:
            from server import paper_cycle_service as pcs

            for k, v in ALERT_CONFIG_DEFAULTS.items():
                if k not in pcs.DEFAULT_CONFIG:
                    pcs.DEFAULT_CONFIG[k] = v
        self._ready = True

    def get_settings(self) -> Dict[str, Any]:
        self._ensure()
        from server.paper_cycle_service import paper_cycle_service

        cfg = paper_cycle_service.get_config().get("config") or {}
        out = {k: cfg.get(k, ALERT_CONFIG_DEFAULTS.get(k)) for k in ALERT_CONFIG_DEFAULTS}
        channels = self.available_channels()
        out["channels_ready"] = channels
        out["channels_configured"] = [c["id"] for c in channels if c.get("configured")]
        return {"ok": True, "settings": out}

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure()
        from server import paper_cycle_service as pcs
        from server.paper_cycle_service import paper_cycle_service

        for k, v in ALERT_CONFIG_DEFAULTS.items():
            if k not in pcs.DEFAULT_CONFIG:
                pcs.DEFAULT_CONFIG[k] = v
        clean = {k: patch[k] for k in patch if k in ALERT_CONFIG_DEFAULTS}
        if clean:
            paper_cycle_service.update_config(clean)
        return self.get_settings()

    def _ensure_env(self) -> None:
        """确保推送相关环境变量已从 .env 加载（兼容未重启/工作目录不对）。"""
        try:
            from pathlib import Path
            from dotenv import load_dotenv

            root = Path(__file__).resolve().parents[1]
            load_dotenv(root / ".env", override=False)
            load_dotenv(root / "backend" / ".env", override=False)
            # 若进程里仍是空字符串，用文件值覆盖空值
            for key in (
                "WECOM_BOT_WEBHOOK",
                "WECOM_AIBOT_ID",
                "WECOM_AIBOT_SECRET",
                "WECOM_AIBOT_CHATID",
                "WECOM_AIBOT_CHAT_TYPE",
                "PUSHPLUS_TOKEN",
                "PUSHPLUS_TOPIC",
                "ALERT_WEBHOOK_URL",
            ):
                cur = (os.getenv(key) or "").strip()
                if cur:
                    continue
                for env_path in (root / ".env", root / "backend" / ".env"):
                    if not env_path.exists():
                        continue
                    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                        s = line.strip()
                        if not s or s.startswith("#") or "=" not in s:
                            continue
                        k, _, v = s.partition("=")
                        if k.strip() != key:
                            continue
                        val = v.strip().strip('"').strip("'")
                        if val:
                            os.environ[key] = val
                        break
        except Exception as e:
            logger.debug("alert env reload: %s", e)

    def available_channels(self) -> List[Dict[str, Any]]:
        self._ensure_env()
        wecom = (os.getenv("WECOM_BOT_WEBHOOK") or "").strip()
        aibot = bool((os.getenv("WECOM_AIBOT_ID") or "").strip() and (os.getenv("WECOM_AIBOT_SECRET") or "").strip())
        pushplus = (os.getenv("PUSHPLUS_TOKEN") or "").strip()
        webhook = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
        return [
            {
                "id": "wecom",
                "name": "企业微信智能机器人",
                "configured": bool(wecom) or aibot,
                "hint": "长连接填 WECOM_AIBOT_ID 和 WECOM_AIBOT_SECRET；群机器人填 WECOM_BOT_WEBHOOK",
            },
            {
                "id": "pushplus",
                "name": "PushPlus（可转微信）",
                "configured": bool(pushplus),
                "hint": "环境变量 PUSHPLUS_TOKEN，可选 PUSHPLUS_TOPIC",
            },
            {
                "id": "webhook",
                "name": "通用 Webhook",
                "configured": bool(webhook),
                "hint": "环境变量 ALERT_WEBHOOK_URL",
            },
        ]

    def _cfg(self) -> Dict[str, Any]:
        from server.paper_cycle_service import paper_cycle_service

        cfg = paper_cycle_service.get_config().get("config") or {}
        out = dict(ALERT_CONFIG_DEFAULTS)
        out.update({k: cfg[k] for k in ALERT_CONFIG_DEFAULTS if k in cfg})
        return out

    def _active_channels(self, cfg: Dict[str, Any]) -> List[str]:
        want = str(cfg.get("alert_channels") or "auto").strip().lower()
        ready = {c["id"] for c in self.available_channels() if c.get("configured")}
        if want in ("", "auto", "*"):
            return sorted(ready)
        picked = [x.strip() for x in want.replace("，", ",").split(",") if x.strip()]
        return [x for x in picked if x in ready]

    # ------------------------------------------------------------------ #
    # notify
    # ------------------------------------------------------------------ #
    def send_notify(self, title: str, content: str, channels: Optional[List[str]] = None) -> Dict[str, Any]:
        chans = channels or self._active_channels(self._cfg())
        results = []
        if not chans:
            return {
                "ok": False,
                "error": "未配置推送通道。请在 .env 设置 WECOM_BOT_WEBHOOK / PUSHPLUS_TOKEN / ALERT_WEBHOOK_URL",
                "results": [],
            }
        for ch in chans:
            ok, detail = False, ""
            try:
                if ch == "wecom":
                    ok, detail = self._push_wecom(title, content)
                elif ch == "pushplus":
                    ok, detail = self._push_pushplus(title, content)
                elif ch == "webhook":
                    ok, detail = self._push_webhook(title, content)
                else:
                    detail = f"未知通道 {ch}"
            except Exception as e:
                detail = str(e)
            results.append({"channel": ch, "ok": ok, "detail": detail[:400]})
        return {"ok": any(r["ok"] for r in results), "results": results}

    def probe_aibot(self) -> Dict[str, Any]:
        """只验证长连接和身份，不发业务消息。"""
        self._ensure_env()
        bot_id = (os.getenv("WECOM_AIBOT_ID") or "").strip()
        secret = (os.getenv("WECOM_AIBOT_SECRET") or "").strip()
        if not bot_id or not secret:
            return {"ok": False, "error": "未配置 WECOM_AIBOT_ID 或 WECOM_AIBOT_SECRET"}
        try:
            frame = _aibot_roundtrip([_aibot_subscribe(bot_id, secret)])[0]
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        ok = frame.get("errcode") == 0
        return {
            "ok": ok,
            "errcode": frame.get("errcode"),
            "errmsg": frame.get("errmsg"),
            "chatid_ready": bool((os.getenv("WECOM_AIBOT_CHATID") or "").strip()),
        }

    def _push_wecom(self, title: str, content: str) -> Tuple[bool, str]:
        self._ensure_env()
        bot_id = (os.getenv("WECOM_AIBOT_ID") or "").strip()
        secret = (os.getenv("WECOM_AIBOT_SECRET") or "").strip()
        if bot_id and secret:
            return self._push_aibot(title, content, bot_id, secret)
        url = (os.getenv("WECOM_BOT_WEBHOOK") or "").strip()
        if not url:
            return False, "未配置企业微信机器人"
        text = f"**{title}**\n{content}"
        return _http_json(url, {"msgtype": "markdown", "markdown": {"content": text}})

    def _push_aibot(self, title: str, content: str, bot_id: str, secret: str) -> Tuple[bool, str]:
        chatid = (os.getenv("WECOM_AIBOT_CHATID") or "").strip()
        if not chatid:
            return False, "机器人身份已配置，但还没有会话。请先在企业微信里给这个机器人发一句话。"
        try:
            chat_type = int((os.getenv("WECOM_AIBOT_CHAT_TYPE") or "1").strip() or "1")
        except ValueError:
            chat_type = 1
        body = f"**{title}**\n{content}"[:6000]
        try:
            frames = _aibot_roundtrip([
                _aibot_subscribe(bot_id, secret),
                {
                    "cmd": "aibot_send_msg",
                    "headers": {"req_id": _req_id()},
                    "body": {
                        "chatid": chatid,
                        "chat_type": chat_type,
                        "msgtype": "markdown",
                        "markdown": {"content": body},
                    },
                },
            ])
        except Exception as exc:
            return False, str(exc)
        sent = frames[-1] if frames else {}
        if sent.get("errcode") == 0:
            return True, "ok"
        return False, str(sent.get("errmsg") or sent)[:300]

    def _push_pushplus(self, title: str, content: str) -> Tuple[bool, str]:
        token = (os.getenv("PUSHPLUS_TOKEN") or "").strip()
        if not token:
            return False, "未配置 PUSHPLUS_TOKEN"
        payload: Dict[str, Any] = {
            "token": token,
            "title": title[:100],
            "content": _markdown_to_html(content),
            "template": "html",
        }
        topic = (os.getenv("PUSHPLUS_TOPIC") or "").strip()
        if topic:
            payload["topic"] = topic
        return _http_json("http://www.pushplus.plus/send", payload)

    def _push_webhook(self, title: str, content: str) -> Tuple[bool, str]:
        url = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
        if not url:
            return False, "未配置 ALERT_WEBHOOK_URL"
        return _http_json(
            url,
            {
                "title": title,
                "content": content,
                "source": "paper_alert",
                "ts": datetime.now(SH).isoformat(timespec="seconds"),
            },
        )

    def test_notify(self, message: Optional[str] = None) -> Dict[str, Any]:
        title = "模拟交易告警 · 测试"
        content = message or (
            f"这是一条测试推送。\n时间：{datetime.now(SH).strftime('%Y-%m-%d %H:%M:%S')}\n"
            "若收到本消息，说明推送通道可用。"
        )
        return self.send_notify(title, content)

    # ------------------------------------------------------------------ #
    # state / dedupe
    # ------------------------------------------------------------------ #
    def _update_day_state(
        self,
        account_id: int,
        ts_code: str,
        trade_date: date,
        price: float,
    ) -> Dict[str, float]:
        self._ensure()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT day_high, day_low, last_price FROM paper_alert_state
                    WHERE account_id=:a AND ts_code=:c AND trade_date=:d
                    """
                ),
                {"a": account_id, "c": ts_code, "d": trade_date},
            ).fetchone()
            if row:
                hi = max(_f(row.day_high, price), price)
                lo = min(_f(row.day_low, price) if _f(row.day_low) > 0 else price, price)
            else:
                hi = lo = price
            conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_alert_state
                        (account_id, ts_code, trade_date, day_high, day_low, last_price, updated_at)
                    VALUES (:a, :c, :d, :hi, :lo, :px, NOW())
                    ON CONFLICT (account_id, ts_code, trade_date) DO UPDATE SET
                        day_high = EXCLUDED.day_high,
                        day_low = EXCLUDED.day_low,
                        last_price = EXCLUDED.last_price,
                        updated_at = NOW()
                    """
                ),
                {"a": account_id, "c": ts_code, "d": trade_date, "hi": hi, "lo": lo, "px": price},
            )
        return {"day_high": hi, "day_low": lo, "last_price": price}

    def _cooldown_ok(
        self,
        account_id: int,
        ts_code: str,
        rule_id: str,
        trade_date: date,
        cooldown_minutes: int,
    ) -> bool:
        self._ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT last_fired_at FROM paper_alert_dedupe
                    WHERE account_id=:a AND ts_code=:c AND rule_id=:r AND trade_date=:d
                    """
                ),
                {"a": account_id, "c": ts_code, "r": rule_id, "d": trade_date},
            ).fetchone()
        if not row:
            return True
        last = row.last_fired_at
        if last is None:
            return True
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=SH)
        now = datetime.now(SH)
        return (now - last.astimezone(SH)) >= timedelta(minutes=max(1, int(cooldown_minutes or 30)))

    def _mark_fired(
        self,
        account_id: int,
        ts_code: str,
        rule_id: str,
        trade_date: date,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_alert_dedupe
                        (account_id, ts_code, rule_id, trade_date, last_fired_at)
                    VALUES (:a, :c, :r, :d, NOW())
                    ON CONFLICT (account_id, ts_code, rule_id, trade_date) DO UPDATE SET
                        last_fired_at = NOW()
                    """
                ),
                {"a": account_id, "c": ts_code, "r": rule_id, "d": trade_date},
            )

    def _save_event(self, ev: Dict[str, Any]) -> int:
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_alert_event
                        (account_id, ts_code, name, rule_id, severity, title, message,
                         price, ref_price, pct, payload, notified, notify_result)
                    VALUES
                        (:account_id, :ts_code, :name, :rule_id, :severity, :title, :message,
                         :price, :ref_price, :pct, CAST(:payload AS jsonb), :notified, :notify_result)
                    RETURNING id
                    """
                ),
                {
                    **ev,
                    "payload": json.dumps(ev.get("payload") or {}, ensure_ascii=False),
                },
            ).fetchone()
            return int(row.id)

    # ------------------------------------------------------------------ #
    # evaluate
    # ------------------------------------------------------------------ #
    def _eval_position(
        self,
        account_id: int,
        pos: Dict[str, Any],
        cfg: Dict[str, Any],
        asof: date,
    ) -> List[Dict[str, Any]]:
        if pos.get("pnl_ready") is False:
            return []
        code = str(pos.get("ts_code") or "").upper()
        if not code:
            return []
        px = _f(pos.get("market_price"))
        avg = _f(pos.get("avg_cost"))
        if px <= 0 or avg <= 0:
            return []
        name = str(pos.get("name") or code)
        qty = _f(pos.get("quantity"))
        pnl_pct = _f(pos.get("pnl_pct"))
        if abs(pnl_pct) < 1e-12 and avg > 0:
            pnl_pct = px / avg - 1.0

        state = self._update_day_state(account_id, code, asof, px)
        day_high = _f(state.get("day_high"), px)
        pullback = (px / day_high - 1.0) if day_high > 0 else 0.0

        hits: List[Dict[str, Any]] = []

        def maybe(rule_id: str, enabled_key: str, cond: bool, thr: float, severity: str, title: str, ref: float, pct: float):
            if not cfg.get(enabled_key, True) or not cond:
                return
            if not self._cooldown_ok(account_id, code, rule_id, asof, int(cfg.get("alert_cooldown_minutes") or 30)):
                return
            hits.append(
                {
                    "rule_id": rule_id,
                    "severity": severity,
                    "title": title,
                    "ts_code": code,
                    "name": name,
                    "price": round(px, 4),
                    "ref_price": round(ref, 4),
                    "pct": round(pct, 6),
                    "threshold": thr,
                    "quantity": qty,
                    "avg_cost": round(avg, 4),
                    "day_high": round(day_high, 4),
                    "message": (
                        f"账户#{account_id} {name}({code})\n"
                        f"{title}\n"
                        f"现价 {px:.3f} · 成本 {avg:.3f} · 浮动 {pnl_pct*100:.2f}%\n"
                        f"数量 {int(qty)} · 触发阈值 {thr*100:.1f}% · 日期 {asof.isoformat()}"
                    ),
                }
            )

        stop_thr = _f(cfg.get("alert_stop_loss_pct"), -0.05)
        take_thr = _f(cfg.get("alert_take_profit_pct"), 0.08)
        pull_thr = _f(cfg.get("alert_peak_pullback_pct"), -0.03)
        drop_thr = _f(cfg.get("alert_daily_drop_pct"), -0.04)
        rise_thr = _f(cfg.get("alert_daily_rise_pct"), 0.06)

        maybe(
            "stop_loss",
            "alert_enable_stop_loss",
            pnl_pct <= stop_thr,
            stop_thr,
            "critical",
            f"止损提醒：亏损已达 {pnl_pct*100:.2f}%",
            avg,
            pnl_pct,
        )
        maybe(
            "take_profit",
            "alert_enable_take_profit",
            pnl_pct >= take_thr,
            take_thr,
            "good",
            f"止盈提醒：浮盈已达 {pnl_pct*100:.2f}%",
            avg,
            pnl_pct,
        )
        maybe(
            "peak_pullback",
            "alert_enable_peak_pullback",
            day_high > 0 and pullback <= pull_thr and px < day_high,
            pull_thr,
            "warn",
            f"冲高回落：自日内高点 {day_high:.3f} 回撤 {pullback*100:.2f}%",
            day_high,
            pullback,
        )
        # 大跌/大涨：优先相对昨收；无昨收时回退相对成本
        pre = _f(pos.get("pre_close"))
        if pre > 0:
            day_chg = px / pre - 1.0
            day_ref = pre
            day_label = "昨收"
        else:
            day_chg = pnl_pct
            day_ref = avg
            day_label = "成本"
        maybe(
            "daily_drop",
            "alert_enable_daily_drop",
            day_chg <= drop_thr,
            drop_thr,
            "warn",
            f"大跌提醒：相对{day_label} {day_chg*100:.2f}%",
            day_ref,
            day_chg,
        )
        maybe(
            "daily_rise",
            "alert_enable_daily_rise",
            day_chg >= rise_thr,
            rise_thr,
            "info",
            f"大涨提醒：相对{day_label} {day_chg*100:.2f}%",
            day_ref,
            day_chg,
        )
        return hits

    def scan_account(
        self,
        account_id: int,
        *,
        notify: bool = True,
        force: bool = False,
    ) -> Dict[str, Any]:
        self._ensure()
        from server.paper_broker_service import paper_broker_service

        cfg = self._cfg()
        marked = paper_broker_service.mark_to_market(account_id, write_equity=False)
        if not marked.get("ok"):
            return {"ok": False, "error": marked.get("error") or "盯市失败"}

        asof = _to_date(marked.get("asof")) or datetime.now(SH).date()
        positions = marked.get("positions") or []
        fired: List[Dict[str, Any]] = []
        skipped_cooldown = 0

        for pos in positions:
            hits = self._eval_position(account_id, pos, cfg, asof)
            for h in hits:
                # 同票同日 stop_loss 与 daily_drop 可能同时触发：都发，但文案不同
                notify_res = {"ok": False, "results": []}
                if notify:
                    notify_res = self.send_notify(h["title"], h["message"])
                eid = self._save_event(
                    {
                        "account_id": account_id,
                        "ts_code": h["ts_code"],
                        "name": h["name"],
                        "rule_id": h["rule_id"],
                        "severity": h["severity"],
                        "title": h["title"],
                        "message": h["message"],
                        "price": h["price"],
                        "ref_price": h["ref_price"],
                        "pct": h["pct"],
                        "payload": h,
                        "notified": bool(notify_res.get("ok")),
                        "notify_result": json.dumps(notify_res, ensure_ascii=False)[:1000],
                    }
                )
                self._mark_fired(account_id, h["ts_code"], h["rule_id"], asof)
                h["event_id"] = eid
                h["notify"] = notify_res
                fired.append(h)

                # 可选：止损自动卖出（默认关）
                if (
                    h["rule_id"] == "stop_loss"
                    and cfg.get("alert_auto_sell_stop")
                    and _f(pos.get("sellable_qty")) > 0
                ):
                    try:
                        sell_qty = _f(pos.get("sellable_qty"))
                        paper_broker_service.place_order(
                            account_id=account_id,
                            ts_code=h["ts_code"],
                            side="sell",
                            quantity=sell_qty,
                            lookback_guard=False,
                            reason=f"告警自动止损 event#{eid}",
                        )
                        h["auto_sell"] = {"ok": True, "qty": sell_qty}
                    except Exception as e:
                        h["auto_sell"] = {"ok": False, "error": str(e)[:200]}

        from server.paper_cycle_service import paper_cycle_service

        paper_cycle_service.update_config(
            {"alert_last_scan": datetime.now(SH).isoformat(timespec="seconds")}
        )

        return {
            "ok": True,
            "account_id": account_id,
            "asof": asof.isoformat(),
            "positions": len(positions),
            "fired_count": len(fired),
            "fired": fired,
            "channels": self._active_channels(cfg),
            "settings": {k: cfg.get(k) for k in ALERT_CONFIG_DEFAULTS},
            "note": (
                "仅提醒，不自动下单"
                if not cfg.get("alert_auto_sell_stop")
                else "止损规则开启自动卖出"
            ),
        }

    def list_events(
        self,
        account_id: Optional[int] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        self._ensure()
        sql = "SELECT * FROM paper_alert_event"
        params: Dict[str, Any] = {"lim": limit}
        if account_id is not None:
            sql += " WHERE account_id = :aid"
            params["aid"] = int(account_id)
        sql += " ORDER BY id DESC LIMIT :lim"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        items = []
        for r in rows:
            d = dict(r._mapping)
            for k, v in list(d.items()):
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            items.append(d)
        return {"ok": True, "count": len(items), "events": items}


paper_alert_service = PaperAlertService()
