"""
Paper Broker Service - 纸面账户 / 模拟交易

向量撮合语义（对齐 strategy_cycle_service）：
- lookback_guard=True  → exec_price_mode=next_open：信号日收盘下单，次日开盘成交
- lookback_guard=False → exec_price_mode=same_close：信号日收盘价立即成交（存在前视）
- exec_price 有值 → exec_price_mode=intraday：按给出的分钟价立即成交
- 佣金万二、最低 5 元；卖出另收印花税万五

能力：开户、查仓、下单、结算 pending、按分时末价盯市（缺分时不计盘中盈亏）、成交统计
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

LOT_SIZE = 100
DEFAULT_CASH = 1_000_000.0

PAPER_DDL = [
    """
    CREATE TABLE IF NOT EXISTS paper_account (
        id              SERIAL PRIMARY KEY,
        name            VARCHAR(64)   NOT NULL,
        initial_cash    NUMERIC(20,4) NOT NULL,
        cash            NUMERIC(20,4) NOT NULL,
        equity          NUMERIC(20,4) NOT NULL,
        currency        VARCHAR(8)    DEFAULT 'CNY',
        status          VARCHAR(16)   DEFAULT 'active',
        lookback_guard  BOOLEAN       DEFAULT TRUE,
        t1_asof         DATE,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        updated_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_position (
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16)   NOT NULL,
        quantity        NUMERIC(20,4) NOT NULL DEFAULT 0,
        sellable_qty    NUMERIC(20,4) NOT NULL DEFAULT 0,
        frozen_qty      NUMERIC(20,4) NOT NULL DEFAULT 0,
        avg_cost        NUMERIC(16,4) NOT NULL DEFAULT 0,
        market_price    NUMERIC(16,4),
        market_value    NUMERIC(20,4),
        unrealized_pnl  NUMERIC(20,4),
        quote_source    VARCHAR(16),
        quote_time      TIMESTAMPTZ,
        quote_trade_date DATE,
        updated_at      TIMESTAMPTZ   DEFAULT NOW(),
        PRIMARY KEY (account_id, ts_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_order (
        id              SERIAL PRIMARY KEY,
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16)   NOT NULL,
        side            VARCHAR(8)    NOT NULL,
        order_type      VARCHAR(16)   DEFAULT 'market',
        quantity        NUMERIC(20,4) NOT NULL,
        filled_qty      NUMERIC(20,4) DEFAULT 0,
        status          VARCHAR(16)   NOT NULL DEFAULT 'pending',
        lookback_guard  BOOLEAN       NOT NULL DEFAULT TRUE,
        exec_price_mode VARCHAR(16)   NOT NULL DEFAULT 'next_open',
        signal_date     DATE,
        exec_date       DATE,
        limit_price     NUMERIC(16,4),
        reason          TEXT,
        reject_reason   TEXT,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        updated_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_fill (
        id              SERIAL PRIMARY KEY,
        order_id        INT           NOT NULL REFERENCES paper_order(id) ON DELETE CASCADE,
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16)   NOT NULL,
        side            VARCHAR(8)    NOT NULL,
        quantity        NUMERIC(20,4) NOT NULL,
        price           NUMERIC(16,4) NOT NULL,
        amount          NUMERIC(20,4) NOT NULL,
        commission      NUMERIC(16,4) DEFAULT 0,
        stamp_tax       NUMERIC(16,4) DEFAULT 0,
        trade_date      DATE          NOT NULL,
        created_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_equity_daily (
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        trade_date      DATE          NOT NULL,
        cash            NUMERIC(20,4) NOT NULL,
        market_value    NUMERIC(20,4) NOT NULL,
        equity          NUMERIC(20,4) NOT NULL,
        daily_return    NUMERIC(12,6),
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        PRIMARY KEY (account_id, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_decision_log (
        id                  SERIAL PRIMARY KEY,
        account_id          INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        session_id          VARCHAR(36),
        user_query          TEXT,
        assistant_summary   TEXT,
        tool_trace          JSONB,
        order_ids           INT[],
        meta                JSONB,
        created_at          TIMESTAMPTZ   DEFAULT NOW()
    )
    """,
]

# A 股纸面费用：佣金万二、最低 5 元；卖出另收印花税万五。无滑点。
COMMISSION_RATE = 0.0002
COMMISSION_MIN = 5.0
STAMP_TAX_RATE = 0.0005


def calc_trade_fees(side: str, amount: float) -> Tuple[float, float]:
    """返回 (佣金, 印花税)。买入印花税为 0。"""
    amt = max(float(amount or 0), 0.0)
    if amt <= 0:
        return 0.0, 0.0
    commission = max(round(amt * COMMISSION_RATE, 4), COMMISSION_MIN)
    stamp = round(amt * STAMP_TAX_RATE, 4) if (side or "").lower() == "sell" else 0.0
    return commission, stamp


PAPER_MIGRATE_DDL = [
    "ALTER TABLE paper_account ADD COLUMN IF NOT EXISTS t1_asof DATE",
    "ALTER TABLE paper_fill ADD COLUMN IF NOT EXISTS stamp_tax NUMERIC(16,4) DEFAULT 0",
    "ALTER TABLE paper_position ADD COLUMN IF NOT EXISTS sellable_qty NUMERIC(20,4) NOT NULL DEFAULT 0",
    "ALTER TABLE paper_position ADD COLUMN IF NOT EXISTS frozen_qty NUMERIC(20,4) NOT NULL DEFAULT 0",
    "ALTER TABLE paper_position ADD COLUMN IF NOT EXISTS quote_source VARCHAR(16)",
    "ALTER TABLE paper_position ADD COLUMN IF NOT EXISTS quote_time TIMESTAMPTZ",
    "ALTER TABLE paper_position ADD COLUMN IF NOT EXISTS quote_trade_date DATE",
    # 兼容旧数据：若可卖+冻结为 0 但有持仓，视为全部可卖（历史仓）
    """
    UPDATE paper_position
    SET sellable_qty = quantity, frozen_qty = 0
    WHERE quantity > 0
      AND COALESCE(sellable_qty, 0) = 0
      AND COALESCE(frozen_qty, 0) = 0
    """,
]

PAPER_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_paper_account_status ON paper_account (status)",
    "CREATE INDEX IF NOT EXISTS idx_paper_position_code ON paper_position (ts_code)",
    "CREATE INDEX IF NOT EXISTS idx_paper_order_account ON paper_order (account_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_order_pending ON paper_order (status, exec_date)",
    "CREATE INDEX IF NOT EXISTS idx_paper_fill_account ON paper_fill (account_id, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_equity_account ON paper_equity_daily (account_id, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_decision_account ON paper_decision_log (account_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_decision_session ON paper_decision_log (session_id)",
]


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()[:10]
    return date.fromisoformat(s)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, date):
            d[k] = v.isoformat()
    return d


class PaperBrokerService:
    def __init__(self):
        self._engine = None
        self._tables_ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        with self.engine.begin() as conn:
            for ddl in PAPER_DDL:
                conn.execute(sa_text(ddl))
            for mig in PAPER_MIGRATE_DDL:
                try:
                    conn.execute(sa_text(mig))
                except Exception as e:
                    logger.warning(f"纸面交易迁移跳过: {e}")
            for idx in PAPER_INDEX_DDL:
                try:
                    conn.execute(sa_text(idx))
                except Exception as e:
                    logger.warning(f"纸面交易索引创建失败: {e}")
        self._tables_ready = True

    # ------------------------------------------------------------------ #
    # 行情辅助
    # ------------------------------------------------------------------ #
    def _bar_table(self, ts_code: str) -> str:
        if ts_code in (
            "000001.SH",
            "000300.SH",
            "000016.SH",
            "000905.SH",
            "000852.SH",
            "000688.SH",
        ) or ts_code.startswith("399"):
            return "index_daily"
        return "stock_daily"

    def _get_bar(
        self,
        conn,
        ts_code: str,
        trade_date: Optional[date] = None,
        *,
        mode: str = "exact",
    ) -> Optional[Dict[str, Any]]:
        """mode: exact | latest | next"""
        table = self._bar_table(ts_code)
        params: Dict[str, Any] = {"code": ts_code}
        if mode == "exact":
            if not trade_date:
                return None
            sql = f"""
                SELECT trade_date, open, high, low, close, vol, amount
                FROM {table}
                WHERE ts_code = :code AND trade_date = :d
                LIMIT 1
            """
            params["d"] = trade_date
        elif mode == "latest":
            if trade_date:
                sql = f"""
                    SELECT trade_date, open, high, low, close, vol, amount
                    FROM {table}
                    WHERE ts_code = :code AND trade_date <= :d
                    ORDER BY trade_date DESC LIMIT 1
                """
                params["d"] = trade_date
            else:
                sql = f"""
                    SELECT trade_date, open, high, low, close, vol, amount
                    FROM {table}
                    WHERE ts_code = :code
                    ORDER BY trade_date DESC LIMIT 1
                """
        elif mode == "next":
            if not trade_date:
                return None
            sql = f"""
                SELECT trade_date, open, high, low, close, vol, amount
                FROM {table}
                WHERE ts_code = :code AND trade_date > :d
                ORDER BY trade_date ASC LIMIT 1
            """
            params["d"] = trade_date
        else:
            return None

        row = conn.execute(sa_text(sql), params).fetchone()
        if not row:
            return None
        return {
            "trade_date": _to_date(row.trade_date),
            "open": _f(row.open),
            "high": _f(row.high),
            "low": _f(row.low),
            "close": _f(row.close),
            "vol": _f(row.vol),
            "amount": _f(row.amount),
        }

    # ------------------------------------------------------------------ #
    # 开户 / 查询
    # ------------------------------------------------------------------ #
    def create_account(
        self,
        name: str,
        initial_cash: float = DEFAULT_CASH,
        lookback_guard: bool = True,
    ) -> Dict[str, Any]:
        self._ensure_tables()
        name = (name or "").strip() or "默认纸面账户"
        cash = float(initial_cash)
        if cash <= 0:
            return {"ok": False, "error": "initial_cash 必须 > 0"}

        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_account
                        (name, initial_cash, cash, equity, lookback_guard)
                    VALUES
                        (:name, :cash, :cash, :cash, :guard)
                    RETURNING *
                    """
                ),
                {"name": name, "cash": cash, "guard": bool(lookback_guard)},
            ).fetchone()
            acct = _row_to_dict(row)
            # 首日净值快照
            today = date.today()
            conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_equity_daily
                        (account_id, trade_date, cash, market_value, equity, daily_return)
                    VALUES
                        (:aid, :d, :cash, 0, :cash, 0)
                    ON CONFLICT (account_id, trade_date) DO UPDATE SET
                        cash = EXCLUDED.cash,
                        market_value = EXCLUDED.market_value,
                        equity = EXCLUDED.equity,
                        daily_return = EXCLUDED.daily_return
                    """
                ),
                {"aid": acct["id"], "d": today, "cash": cash},
            )
        return {"ok": True, "account": acct}

    def delete_account(self, account_id: int, *, hard: bool = True) -> Dict[str, Any]:
        """
        删除纸面账户。
        hard=True：物理删除（级联清理持仓/委托/成交/净值/决策日志）
        hard=False：软关闭 status=closed
        """
        self._ensure_tables()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM paper_account WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"账户不存在: {account_id}"}

            acct = _row_to_dict(row)
            if hard:
                conn.execute(
                    sa_text("DELETE FROM paper_account WHERE id = :id"),
                    {"id": account_id},
                )
                return {
                    "ok": True,
                    "deleted": True,
                    "hard": True,
                    "account_id": account_id,
                    "account": acct,
                    "message": f"已删除账户 #{account_id} 及其关联数据",
                }

            conn.execute(
                sa_text(
                    """
                    UPDATE paper_account
                    SET status = 'closed', updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": account_id},
            )
            out = conn.execute(
                sa_text("SELECT * FROM paper_account WHERE id = :id"),
                {"id": account_id},
            ).fetchone()
            return {
                "ok": True,
                "deleted": False,
                "hard": False,
                "account_id": account_id,
                "account": _row_to_dict(out),
                "message": f"已关闭账户 #{account_id}",
            }

    def roll_t1(
        self,
        account_id: Optional[int] = None,
        asof: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        T+1 日切：将 frozen_qty 转入 sellable_qty。
        同一账户同一 asof 默认只执行一次（可用 force=True 强制）。
        """
        self._ensure_tables()
        asof_d = _to_date(asof) if asof else date.today()
        rolled: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []

        with self.engine.begin() as conn:
            sql = "SELECT * FROM paper_account WHERE status = 'active'"
            params: Dict[str, Any] = {}
            if account_id is not None:
                sql += " AND id = :aid"
                params["aid"] = account_id
            sql += " ORDER BY id ASC FOR UPDATE"
            accounts = conn.execute(sa_text(sql), params).fetchall()

            for acct in accounts:
                aid = int(acct.id)
                prev = _to_date(getattr(acct, "t1_asof", None))
                if prev and prev >= asof_d and not force:
                    skipped.append(
                        {
                            "account_id": aid,
                            "reason": f"已在 {prev} 日切过，跳过",
                            "t1_asof": prev.isoformat(),
                        }
                    )
                    continue

                pos_rows = conn.execute(
                    sa_text(
                        """
                        SELECT ts_code, quantity, sellable_qty, frozen_qty
                        FROM paper_position
                        WHERE account_id = :aid AND quantity > 0
                        FOR UPDATE
                        """
                    ),
                    {"aid": aid},
                ).fetchall()

                moved = 0.0
                details = []
                for p in pos_rows:
                    qty = _f(p.quantity)
                    sellable = _f(p.sellable_qty)
                    frozen = _f(p.frozen_qty)
                    if sellable <= 0 and frozen <= 0 and qty > 0:
                        # 旧数据：视为已全部可卖
                        sellable, frozen = qty, 0.0
                    if frozen <= 1e-9:
                        continue
                    new_sellable = sellable + frozen
                    conn.execute(
                        sa_text(
                            """
                            UPDATE paper_position SET
                                sellable_qty = :sellable,
                                frozen_qty = 0,
                                updated_at = NOW()
                            WHERE account_id = :aid AND ts_code = :code
                            """
                        ),
                        {"aid": aid, "code": p.ts_code, "sellable": new_sellable},
                    )
                    moved += frozen
                    details.append(
                        {
                            "ts_code": p.ts_code,
                            "unfrozen": frozen,
                            "sellable_qty": new_sellable,
                        }
                    )

                conn.execute(
                    sa_text(
                        """
                        UPDATE paper_account SET
                            t1_asof = :d,
                            updated_at = NOW()
                        WHERE id = :aid
                        """
                    ),
                    {"aid": aid, "d": asof_d},
                )
                rolled.append(
                    {
                        "account_id": aid,
                        "asof": asof_d.isoformat(),
                        "unfrozen_shares": moved,
                        "positions": details,
                    }
                )

        return {
            "ok": True,
            "asof": asof_d.isoformat(),
            "rolled_count": len(rolled),
            "skipped_count": len(skipped),
            "rolled": rolled,
            "skipped": skipped,
        }

    def list_accounts(self, status: Optional[str] = "active") -> Dict[str, Any]:
        self._ensure_tables()
        sql = "SELECT * FROM paper_account"
        params: Dict[str, Any] = {}
        if status:
            sql += " WHERE status = :st"
            params["st"] = status
        sql += " ORDER BY id DESC"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {"ok": True, "count": len(rows), "accounts": [_row_to_dict(r) for r in rows]}

    def get_account(self, account_id: int, mark: bool = True) -> Dict[str, Any]:
        self._ensure_tables()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM paper_account WHERE id = :id"),
                {"id": account_id},
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"账户不存在: {account_id}"}
            acct = _row_to_dict(row)
            positions = self._list_positions_conn(conn, account_id)

        extra: Dict[str, Any] = {}
        if mark and positions:
            marked = self.mark_to_market(account_id, write_equity=False)
            if marked.get("ok"):
                acct = marked.get("account") or acct
                positions = marked.get("positions") or positions
                extra = {
                    "mark_source": marked.get("mark_source"),
                    "session_phase": marked.get("session_phase"),
                    "intraday_complete": marked.get("intraday_complete"),
                    "missing_quotes": marked.get("missing_quotes") or [],
                    "note": marked.get("note"),
                    "asof": marked.get("asof"),
                }

        equity = _f((acct or {}).get("equity"))
        positions = self._decorate_positions(positions, equity=equity)
        return {"ok": True, "account": acct, "positions": positions, **extra}

    def _stock_names_conn(self, conn, codes: List[str]) -> Dict[str, str]:
        codes = [c for c in codes if c]
        if not codes:
            return {}
        try:
            placeholders = ",".join(f":c{i}" for i in range(len(codes)))
            params = {f"c{i}": c for i, c in enumerate(codes)}
            rows = conn.execute(
                sa_text(
                    f"SELECT ts_code, name FROM ref_stock_basic WHERE ts_code IN ({placeholders})"
                ),
                params,
            ).fetchall()
            return {str(r.ts_code): str(r.name or "") for r in rows}
        except Exception as e:
            logger.warning("持仓名称查询失败: %s", e)
            return {}

    def _decorate_positions(
        self,
        positions: List[Dict[str, Any]],
        *,
        equity: float = 0.0,
        names: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        names = names or {}
        missing = [
            p.get("ts_code")
            for p in positions
            if p.get("ts_code") and not (p.get("name") or names.get(p.get("ts_code") or ""))
        ]
        if missing:
            try:
                with self.engine.connect() as conn:
                    names = {**self._stock_names_conn(conn, missing), **names}
            except Exception:
                pass
        out: List[Dict[str, Any]] = []
        for p in positions:
            d = dict(p)
            qty = _f(d.get("quantity"))
            avg = _f(d.get("avg_cost"))
            quoted = str(d.get("quote_source") or "") == "intraday"
            raw_px = d.get("market_price")
            px = _f(raw_px) if quoted and raw_px is not None else (_f(raw_px) if quoted else 0.0)
            if not quoted:
                d["name"] = d.get("name") or names.get(d.get("ts_code") or "", "") or ""
                d["market_price"] = None
                d["market_value"] = None
                d["unrealized_pnl"] = None
                d["cost_amount"] = round(qty * avg, 4)
                d["pnl_pct"] = None
                d["weight"] = None
                d["pnl_ready"] = False
                d["quote_clock"] = None
                out.append(d)
                continue
            if d.get("market_value") is not None:
                mv = _f(d.get("market_value"))
            else:
                mv = qty * px
            if d.get("unrealized_pnl") is not None:
                pnl = _f(d.get("unrealized_pnl"))
            else:
                pnl = (px - avg) * qty
            d["name"] = d.get("name") or names.get(d.get("ts_code") or "", "") or ""
            d["market_price"] = round(px, 4)
            d["market_value"] = round(mv, 4)
            d["unrealized_pnl"] = round(pnl, 4)
            d["cost_amount"] = round(qty * avg, 4)
            d["pnl_pct"] = round(px / avg - 1.0, 6) if avg > 0 else None
            d["weight"] = round(mv / equity, 6) if equity and equity > 0 else None
            d["pnl_ready"] = True
            qt = d.get("quote_clock") or d.get("quote_time")
            if not d.get("quote_clock"):
                d["quote_clock"] = (
                    str(qt)[11:16] if isinstance(qt, str) and len(str(qt)) >= 16 else None
                )
            out.append(d)
        return out

    def _list_positions_conn(self, conn, account_id: int) -> List[Dict[str, Any]]:
        try:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT p.*, COALESCE(b.name, '') AS name
                    FROM paper_position p
                    LEFT JOIN ref_stock_basic b ON b.ts_code = p.ts_code
                    WHERE p.account_id = :aid AND p.quantity > 0
                    ORDER BY p.ts_code
                    """
                ),
                {"aid": account_id},
            ).fetchall()
        except Exception:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_position
                    WHERE account_id = :aid AND quantity > 0
                    ORDER BY ts_code
                    """
                ),
                {"aid": account_id},
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_positions(self, account_id: int) -> Dict[str, Any]:
        marked = self.mark_to_market(account_id, write_equity=False)
        if not marked.get("ok"):
            return marked
        return {
            "ok": True,
            "account_id": account_id,
            "count": len(marked.get("positions") or []),
            "positions": marked.get("positions") or [],
            "mark_source": marked.get("mark_source"),
            "session_phase": marked.get("session_phase"),
            "intraday_complete": marked.get("intraday_complete"),
            "missing_quotes": marked.get("missing_quotes") or [],
            "note": marked.get("note"),
        }

    # ------------------------------------------------------------------ #
    # 下单
    # ------------------------------------------------------------------ #
    def place_order(
        self,
        account_id: int,
        ts_code: str,
        side: str,
        quantity: float,
        lookback_guard: Optional[bool] = None,
        signal_date: Optional[str] = None,
        reason: Optional[str] = None,
        order_type: str = "market",
        exec_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._ensure_tables()
        ts_code = (ts_code or "").strip().upper()
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            return {"ok": False, "error": "side 必须为 buy 或 sell"}
        qty = float(quantity)
        if qty <= 0:
            return {"ok": False, "error": "quantity 必须 > 0"}
        if side == "buy" and abs(qty % LOT_SIZE) > 1e-9:
            return {"ok": False, "error": f"买入数量须为 {LOT_SIZE} 的整数倍"}

        with self.engine.begin() as conn:
            acct_row = conn.execute(
                sa_text("SELECT * FROM paper_account WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            ).fetchone()
            if not acct_row:
                return {"ok": False, "error": f"账户不存在: {account_id}"}
            if acct_row.status != "active":
                return {"ok": False, "error": "账户已关闭"}

            guard = bool(acct_row.lookback_guard) if lookback_guard is None else bool(lookback_guard)
            exec_mode = "next_open" if guard else "same_close"
            live_px = _f(exec_price) if exec_price is not None else None
            if exec_price is not None and (live_px <= 0 or math.isnan(live_px)):
                return {"ok": False, "error": f"无效分时成交价: {exec_price}"}
            if live_px:
                # 盘中做T：按当前分钟价立即成交，不用日线收盘，也不挂次日开盘
                guard = False
                exec_mode = "intraday"

            sig_d = _to_date(signal_date) if signal_date else None
            bar = None
            if live_px:
                if sig_d is None:
                    from zoneinfo import ZoneInfo

                    sig_d = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            elif sig_d is None:
                bar = self._get_bar(conn, ts_code, mode="latest")
                if not bar:
                    return {"ok": False, "error": f"无行情: {ts_code}，请先补日线"}
                sig_d = bar["trade_date"]
            else:
                bar = self._get_bar(conn, ts_code, sig_d, mode="latest")
                if not bar:
                    return {"ok": False, "error": f"无行情: {ts_code} @ {sig_d}"}
                # 若传入日期无精确 bar，用最近可用日作为信号日
                if bar["trade_date"] != sig_d and signal_date:
                    sig_d = bar["trade_date"]

            if side == "sell":
                pos = conn.execute(
                    sa_text(
                        """
                        SELECT quantity, sellable_qty, frozen_qty FROM paper_position
                        WHERE account_id = :aid AND ts_code = :code
                        """
                    ),
                    {"aid": account_id, "code": ts_code},
                ).fetchone()
                held = _f(pos.quantity) if pos else 0.0
                sellable = _f(pos.sellable_qty) if pos else 0.0
                frozen = _f(pos.frozen_qty) if pos else 0.0
                # 兼容未迁移旧行
                if pos and sellable <= 0 and frozen <= 0 and held > 0:
                    sellable = held
                if qty > sellable + 1e-9:
                    return {
                        "ok": False,
                        "error": (
                            f"T+1 可卖不足: 可卖 {sellable}，冻结(当日买入) {frozen}，"
                            f"总持仓 {held}，卖出请求 {qty}"
                        ),
                    }

            exec_date: Optional[date] = None
            if guard:
                nxt = self._get_bar(conn, ts_code, sig_d, mode="next")
                if nxt:
                    exec_date = nxt["trade_date"]
                # 若次日尚无数据，仍挂 pending，等 settle 时再撮合
            else:
                exec_date = sig_d

            order_row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_order (
                        account_id, ts_code, side, order_type, quantity,
                        status, lookback_guard, exec_price_mode,
                        signal_date, exec_date, reason
                    ) VALUES (
                        :aid, :code, :side, :otype, :qty,
                        'pending', :guard, :emode,
                        :sig, :exd, :reason
                    )
                    RETURNING *
                    """
                ),
                {
                    "aid": account_id,
                    "code": ts_code,
                    "side": side,
                    "otype": order_type or "market",
                    "qty": qty,
                    "guard": guard,
                    "emode": exec_mode,
                    "sig": sig_d,
                    "exd": exec_date,
                    "reason": reason,
                },
            ).fetchone()
            order = _row_to_dict(order_row)

            # same_close / intraday：立即撮合。分时价优先，否则用信号日收盘价。
            if not guard:
                fill_px = live_px if live_px else _f((bar or {}).get("close"))
                fill_result = self._fill_order_conn(
                    conn,
                    order_id=int(order["id"]),
                    account_id=account_id,
                    force_price=fill_px,
                    force_date=sig_d,
                )
                if not fill_result.get("ok"):
                    conn.execute(
                        sa_text(
                            """
                            UPDATE paper_order
                            SET status = 'rejected',
                                reject_reason = :rr,
                                updated_at = NOW()
                            WHERE id = :oid
                            """
                        ),
                        {
                            "oid": order["id"],
                            "rr": fill_result.get("error") or "成交失败",
                        },
                    )
                    order["status"] = "rejected"
                    order["reject_reason"] = fill_result.get("error")
                    return {
                        "ok": False,
                        "error": fill_result.get("error"),
                        "order": order,
                    }
                order = fill_result.get("order") or order
                return {
                    "ok": True,
                    "order": order,
                    "fill": fill_result.get("fill"),
                    "account": fill_result.get("account"),
                    "exec_price_mode": exec_mode,
                }

        return {
            "ok": True,
            "order": order,
            "fill": None,
            "message": (
                f"已挂单，待 {exec_date or '下一交易日'} 开盘成交"
                if exec_date
                else "已挂单，等待次日行情入库后结算"
            ),
            "exec_price_mode": exec_mode,
        }

    # ------------------------------------------------------------------ #
    # 成交内部逻辑
    # ------------------------------------------------------------------ #
    def _fill_order_conn(
        self,
        conn,
        order_id: int,
        account_id: int,
        force_price: Optional[float] = None,
        force_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        order_row = conn.execute(
            sa_text("SELECT * FROM paper_order WHERE id = :oid FOR UPDATE"),
            {"oid": order_id},
        ).fetchone()
        if not order_row:
            return {"ok": False, "error": f"订单不存在: {order_id}"}
        if order_row.status != "pending":
            return {"ok": False, "error": f"订单状态不可成交: {order_row.status}"}

        acct = conn.execute(
            sa_text("SELECT * FROM paper_account WHERE id = :id FOR UPDATE"),
            {"id": account_id},
        ).fetchone()
        if not acct:
            return {"ok": False, "error": "账户不存在"}

        ts_code = order_row.ts_code
        side = order_row.side
        qty = _f(order_row.quantity)
        mode = order_row.exec_price_mode

        trade_date = force_date or _to_date(order_row.exec_date)
        px = force_price

        if px is None or trade_date is None:
            if mode == "next_open":
                sig = _to_date(order_row.signal_date)
                bar = self._get_bar(conn, ts_code, sig, mode="next")
                if not bar:
                    return {"ok": False, "error": "次日行情尚未就绪", "retry": True}
                trade_date = bar["trade_date"]
                px = _f(bar["open"]) or _f(bar["close"])
            else:
                sig = _to_date(order_row.signal_date) or trade_date
                bar = self._get_bar(conn, ts_code, sig, mode="exact") or self._get_bar(
                    conn, ts_code, sig, mode="latest"
                )
                if not bar:
                    return {"ok": False, "error": "无收盘行情", "retry": True}
                trade_date = bar["trade_date"]
                px = _f(bar["close"])

        if not px or px <= 0 or math.isnan(px):
            return {"ok": False, "error": f"无效成交价: {px}"}

        cash = _f(acct.cash)
        amount = qty * px
        commission, stamp_tax = calc_trade_fees(side, amount)
        fee_total = commission + stamp_tax

        if side == "buy":
            cost = amount + fee_total
            if cash + 1e-6 < cost:
                return {"ok": False, "error": f"现金不足: 需要 {cost:.2f}，可用 {cash:.2f}"}
            new_cash = cash - cost
            pos = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_position
                    WHERE account_id = :aid AND ts_code = :code
                    FOR UPDATE
                    """
                ),
                {"aid": account_id, "code": ts_code},
            ).fetchone()
            if pos and _f(pos.quantity) > 0:
                old_qty = _f(pos.quantity)
                old_cost = _f(pos.avg_cost)
                old_sellable = _f(getattr(pos, "sellable_qty", 0))
                old_frozen = _f(getattr(pos, "frozen_qty", 0))
                if old_sellable <= 0 and old_frozen <= 0 and old_qty > 0:
                    old_sellable = old_qty
                new_qty = old_qty + qty
                avg = (old_qty * old_cost + amount + fee_total) / new_qty if new_qty else px
                # T+1：当日买入进入冻结，不可卖
                new_sellable = old_sellable
                new_frozen = old_frozen + qty
            else:
                new_qty = qty
                avg = (amount + fee_total) / qty if qty else px
                new_sellable = 0.0
                new_frozen = qty
            conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_position
                        (account_id, ts_code, quantity, sellable_qty, frozen_qty,
                         avg_cost, market_price, market_value, unrealized_pnl, updated_at)
                    VALUES
                        (:aid, :code, :qty, :sellable, :frozen,
                         :avg, :px, :mv, :upnl, NOW())
                    ON CONFLICT (account_id, ts_code) DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        sellable_qty = EXCLUDED.sellable_qty,
                        frozen_qty = EXCLUDED.frozen_qty,
                        avg_cost = EXCLUDED.avg_cost,
                        market_price = EXCLUDED.market_price,
                        market_value = EXCLUDED.market_value,
                        unrealized_pnl = EXCLUDED.unrealized_pnl,
                        updated_at = NOW()
                    """
                ),
                {
                    "aid": account_id,
                    "code": ts_code,
                    "qty": new_qty,
                    "sellable": new_sellable,
                    "frozen": new_frozen,
                    "avg": avg,
                    "px": px,
                    "mv": new_qty * px,
                    "upnl": (px - avg) * new_qty,
                },
            )
        else:
            pos = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_position
                    WHERE account_id = :aid AND ts_code = :code
                    FOR UPDATE
                    """
                ),
                {"aid": account_id, "code": ts_code},
            ).fetchone()
            held = _f(pos.quantity) if pos else 0.0
            sellable = _f(getattr(pos, "sellable_qty", 0)) if pos else 0.0
            frozen = _f(getattr(pos, "frozen_qty", 0)) if pos else 0.0
            if pos and sellable <= 0 and frozen <= 0 and held > 0:
                sellable = held
            if qty > sellable + 1e-9:
                return {
                    "ok": False,
                    "error": (
                        f"T+1 可卖不足: 可卖 {sellable}，冻结 {frozen}，"
                        f"总持仓 {held}，卖出请求 {qty}"
                    ),
                }
            new_cash = cash + amount - fee_total
            new_qty = held - qty
            new_sellable = sellable - qty
            new_frozen = frozen
            avg = _f(pos.avg_cost) if pos else 0.0
            if new_qty <= 1e-9:
                conn.execute(
                    sa_text(
                        """
                        DELETE FROM paper_position
                        WHERE account_id = :aid AND ts_code = :code
                        """
                    ),
                    {"aid": account_id, "code": ts_code},
                )
            else:
                conn.execute(
                    sa_text(
                        """
                        UPDATE paper_position SET
                            quantity = :qty,
                            sellable_qty = :sellable,
                            frozen_qty = :frozen,
                            market_price = :px,
                            market_value = :mv,
                            unrealized_pnl = :upnl,
                            updated_at = NOW()
                        WHERE account_id = :aid AND ts_code = :code
                        """
                    ),
                    {
                        "aid": account_id,
                        "code": ts_code,
                        "qty": new_qty,
                        "sellable": max(0.0, new_sellable),
                        "frozen": max(0.0, new_frozen),
                        "px": px,
                        "mv": new_qty * px,
                        "upnl": (px - avg) * new_qty,
                    },
                )

        fill_row = conn.execute(
            sa_text(
                """
                INSERT INTO paper_fill (
                    order_id, account_id, ts_code, side, quantity,
                    price, amount, commission, stamp_tax, trade_date
                ) VALUES (
                    :oid, :aid, :code, :side, :qty,
                    :px, :amt, :comm, :stamp, :td
                )
                RETURNING *
                """
            ),
            {
                "oid": order_id,
                "aid": account_id,
                "code": ts_code,
                "side": side,
                "qty": qty,
                "px": px,
                "amt": amount,
                "comm": commission,
                "stamp": stamp_tax,
                "td": trade_date,
            },
        ).fetchone()

        conn.execute(
            sa_text(
                """
                UPDATE paper_order SET
                    status = 'filled',
                    filled_qty = :qty,
                    exec_date = :td,
                    updated_at = NOW()
                WHERE id = :oid
                """
            ),
            {"oid": order_id, "qty": qty, "td": trade_date},
        )

        # 更新现金与盯市权益
        mv = self._positions_market_value(conn, account_id)
        equity = new_cash + mv
        conn.execute(
            sa_text(
                """
                UPDATE paper_account SET
                    cash = :cash,
                    equity = :eq,
                    updated_at = NOW()
                WHERE id = :aid
                """
            ),
            {"aid": account_id, "cash": new_cash, "eq": equity},
        )

        acct_out = conn.execute(
            sa_text("SELECT * FROM paper_account WHERE id = :id"),
            {"id": account_id},
        ).fetchone()
        order_out = conn.execute(
            sa_text("SELECT * FROM paper_order WHERE id = :oid"),
            {"oid": order_id},
        ).fetchone()

        return {
            "ok": True,
            "fill": _row_to_dict(fill_row),
            "order": _row_to_dict(order_out),
            "account": _row_to_dict(acct_out),
        }

    def _positions_market_value(self, conn, account_id: int) -> float:
        rows = conn.execute(
            sa_text(
                """
                SELECT ts_code, quantity, market_price, avg_cost
                FROM paper_position
                WHERE account_id = :aid AND quantity > 0
                """
            ),
            {"aid": account_id},
        ).fetchall()
        total = 0.0
        for r in rows:
            px = _f(r.market_price) or _f(r.avg_cost)
            bar = self._get_bar(conn, r.ts_code, mode="latest")
            if bar and bar["close"] > 0:
                px = bar["close"]
            total += _f(r.quantity) * px
        return total

    # ------------------------------------------------------------------ #
    # 结算 pending
    # ------------------------------------------------------------------ #
    def settle_pending(
        self,
        account_id: Optional[int] = None,
        asof: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        结算 lookback_guard 挂单：
        - 若指定 asof，仅成交 exec_date <= asof（或 next bar 日期 <= asof）的 pending
        - 否则尽可能成交所有已有次日行情的 pending
        """
        self._ensure_tables()
        asof_d = _to_date(asof) if asof else None
        filled: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []

        with self.engine.begin() as conn:
            sql = """
                SELECT * FROM paper_order
                WHERE status = 'pending' AND lookback_guard = TRUE
            """
            params: Dict[str, Any] = {}
            if account_id is not None:
                sql += " AND account_id = :aid"
                params["aid"] = account_id
            sql += " ORDER BY id ASC"
            orders = conn.execute(sa_text(sql), params).fetchall()

            for o in orders:
                sig = _to_date(o.signal_date)
                bar = self._get_bar(conn, o.ts_code, sig, mode="next")
                if not bar:
                    skipped.append(
                        {
                            "order_id": o.id,
                            "reason": "次日行情未就绪",
                            "ts_code": o.ts_code,
                            "signal_date": str(sig) if sig else None,
                        }
                    )
                    continue
                if asof_d and bar["trade_date"] > asof_d:
                    skipped.append(
                        {
                            "order_id": o.id,
                            "reason": f"exec_date {bar['trade_date']} > asof {asof_d}",
                            "ts_code": o.ts_code,
                        }
                    )
                    continue

                # 回写预计 exec_date
                conn.execute(
                    sa_text(
                        """
                        UPDATE paper_order SET exec_date = :ed, updated_at = NOW()
                        WHERE id = :oid
                        """
                    ),
                    {"oid": o.id, "ed": bar["trade_date"]},
                )

                result = self._fill_order_conn(
                    conn,
                    order_id=int(o.id),
                    account_id=int(o.account_id),
                    force_price=_f(bar["open"]) or _f(bar["close"]),
                    force_date=bar["trade_date"],
                )
                if result.get("ok"):
                    filled.append(
                        {
                            "order_id": o.id,
                            "fill": result.get("fill"),
                            "account_id": o.account_id,
                        }
                    )
                elif result.get("retry"):
                    skipped.append(
                        {
                            "order_id": o.id,
                            "reason": result.get("error"),
                            "ts_code": o.ts_code,
                        }
                    )
                else:
                    conn.execute(
                        sa_text(
                            """
                            UPDATE paper_order SET
                                status = 'rejected',
                                reject_reason = :rr,
                                updated_at = NOW()
                            WHERE id = :oid
                            """
                        ),
                        {"oid": o.id, "rr": result.get("error") or "结算失败"},
                    )
                    rejected.append(
                        {
                            "order_id": o.id,
                            "reason": result.get("error"),
                            "ts_code": o.ts_code,
                        }
                    )

        # 结算后盯市
        accounts_touched = sorted(
            {int(x["account_id"]) for x in filled if x.get("account_id")}
        )
        if account_id is not None and account_id not in accounts_touched:
            # 仍可可选盯市
            pass
        for aid in accounts_touched:
            try:
                self.mark_to_market(aid, asof=asof, write_equity=True)
            except Exception as e:
                logger.warning(f"结算后盯市失败 account={aid}: {e}")

        return {
            "ok": True,
            "filled_count": len(filled),
            "rejected_count": len(rejected),
            "skipped_count": len(skipped),
            "filled": filled,
            "rejected": rejected,
            "skipped": skipped,
            "asof": asof_d.isoformat() if asof_d else None,
        }

    # ------------------------------------------------------------------ #
    # 盯市 / 净值
    # ------------------------------------------------------------------ #
    def mark_to_market(
        self,
        account_id: int,
        asof: Optional[str] = None,
        write_equity: bool = True,
    ) -> Dict[str, Any]:
        """用真实 1 分钟分时末价盯市。没有分时的持仓不计盘中盈亏，不用日线/成本价冒充。"""
        self._ensure_tables()
        from server.paper_quote import last_intraday_quotes, session_context

        ctx = session_context()
        asof_d = _to_date(asof) if asof else ctx.get("trade_date")
        quote_asof = asof_d.isoformat() if isinstance(asof_d, date) else None

        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT id FROM paper_account WHERE id = :id"),
                {"id": account_id},
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"账户不存在: {account_id}"}
            codes = [
                str(p.ts_code)
                for p in conn.execute(
                    sa_text(
                        "SELECT ts_code FROM paper_position "
                        "WHERE account_id = :aid AND quantity > 0"
                    ),
                    {"aid": account_id},
                ).fetchall()
            ]

        quotes = last_intraday_quotes(codes, trade_date=quote_asof) if codes else {}

        with self.engine.begin() as conn:
            acct = conn.execute(
                sa_text("SELECT * FROM paper_account WHERE id = :id FOR UPDATE"),
                {"id": account_id},
            ).fetchone()
            if not acct:
                return {"ok": False, "error": f"账户不存在: {account_id}"}

            try:
                positions = conn.execute(
                    sa_text(
                        """
                        SELECT p.*, COALESCE(b.name, '') AS name
                        FROM paper_position p
                        LEFT JOIN ref_stock_basic b ON b.ts_code = p.ts_code
                        WHERE p.account_id = :aid AND p.quantity > 0
                        FOR UPDATE OF p
                        """
                    ),
                    {"aid": account_id},
                ).fetchall()
            except Exception:
                positions = conn.execute(
                    sa_text(
                        """
                        SELECT * FROM paper_position
                        WHERE account_id = :aid AND quantity > 0
                        FOR UPDATE
                        """
                    ),
                    {"aid": account_id},
                ).fetchall()

            market_value = 0.0
            out_positions: List[Dict[str, Any]] = []
            missing: List[str] = []
            snap_date = asof_d or date.today()

            for p in positions:
                qty = _f(p.quantity)
                avg = _f(p.avg_cost)
                q = quotes.get(str(p.ts_code).upper()) or {}
                if q.get("ok") and q.get("price"):
                    px = float(q["price"])
                    mv = qty * px
                    upnl = (px - avg) * qty
                    market_value += mv
                    q_src = "intraday"
                    q_time = str(q.get("bar_time") or "")
                    if len(q_time) < 16:
                        q_time = None
                    q_day = _to_date(q.get("trade_date")) or asof_d
                    if q_day:
                        snap_date = q_day
                else:
                    px = None
                    mv = None
                    upnl = None
                    q_src = "missing"
                    q_time = None
                    q_day = asof_d
                    missing.append(str(p.ts_code))
                conn.execute(
                    sa_text(
                        """
                        UPDATE paper_position SET
                            market_price = :px,
                            market_value = :mv,
                            unrealized_pnl = :upnl,
                            quote_source = :qsrc,
                            quote_time = CAST(:qtime AS timestamptz),
                            quote_trade_date = :qday,
                            updated_at = NOW()
                        WHERE account_id = :aid AND ts_code = :code
                        """
                    ),
                    {
                        "aid": account_id,
                        "code": p.ts_code,
                        "px": px,
                        "mv": mv,
                        "upnl": upnl,
                        "qsrc": q_src,
                        "qtime": q_time,
                        "qday": q_day,
                    },
                )
                out_positions.append(
                    {
                        "account_id": account_id,
                        "ts_code": p.ts_code,
                        "name": str(getattr(p, "name", "") or ""),
                        "quantity": qty,
                        "sellable_qty": _f(getattr(p, "sellable_qty", 0)),
                        "frozen_qty": _f(getattr(p, "frozen_qty", 0)),
                        "avg_cost": avg,
                        "market_price": px,
                        "market_value": None if mv is None else round(mv, 4),
                        "unrealized_pnl": None if upnl is None else round(upnl, 4),
                        "quote_source": q_src,
                        "quote_time": q_time,
                        "quote_trade_date": q_day.isoformat() if isinstance(q_day, date) else q_day,
                        "quote_clock": q.get("clock") if q.get("ok") else None,
                        "quote_error": None if q.get("ok") else (q.get("error") or "无分时"),
                    }
                )

            if asof_d:
                snap_date = asof_d

            cash = _f(acct.cash)
            equity = cash + market_value
            complete = not missing
            conn.execute(
                sa_text(
                    """
                    UPDATE paper_account SET
                        equity = :eq,
                        updated_at = NOW()
                    WHERE id = :aid
                    """
                ),
                {"aid": account_id, "eq": equity},
            )

            daily_return = None
            if write_equity and complete:
                prev = conn.execute(
                    sa_text(
                        """
                        SELECT equity FROM paper_equity_daily
                        WHERE account_id = :aid AND trade_date < :d
                        ORDER BY trade_date DESC LIMIT 1
                        """
                    ),
                    {"aid": account_id, "d": snap_date},
                ).fetchone()
                if prev and _f(prev.equity) > 0:
                    daily_return = equity / _f(prev.equity) - 1.0
                else:
                    daily_return = 0.0

                conn.execute(
                    sa_text(
                        """
                        INSERT INTO paper_equity_daily
                            (account_id, trade_date, cash, market_value, equity, daily_return)
                        VALUES
                            (:aid, :d, :cash, :mv, :eq, :ret)
                        ON CONFLICT (account_id, trade_date) DO UPDATE SET
                            cash = EXCLUDED.cash,
                            market_value = EXCLUDED.market_value,
                            equity = EXCLUDED.equity,
                            daily_return = EXCLUDED.daily_return
                        """
                    ),
                    {
                        "aid": account_id,
                        "d": snap_date,
                        "cash": cash,
                        "mv": market_value,
                        "eq": equity,
                        "ret": daily_return,
                    },
                )

            acct_out = _row_to_dict(
                conn.execute(
                    sa_text("SELECT * FROM paper_account WHERE id = :id"),
                    {"id": account_id},
                ).fetchone()
            )

        return {
            "ok": True,
            "account": acct_out,
            "positions": self._decorate_positions(out_positions, equity=equity),
            "cash": cash,
            "market_value": round(market_value, 4),
            "equity": round(equity, 4),
            "asof": snap_date.isoformat() if isinstance(snap_date, date) else str(snap_date),
            "daily_return": None if daily_return is None else round(daily_return, 6),
            "mark_source": "intraday",
            "session_phase": ctx.get("phase"),
            "intraday_complete": complete,
            "missing_quotes": missing,
            "note": (
                None
                if complete
                else "以下持仓没有对应分时，盘中盈亏未计算（未用日线估算）：" + "、".join(missing)
            ),
        }

    # ------------------------------------------------------------------ #
    # 查询订单 / 成交 / 净值 / 统计
    # ------------------------------------------------------------------ #
    def list_orders(
        self,
        account_id: int,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        self._ensure_tables()
        sql = "SELECT * FROM paper_order WHERE account_id = :aid"
        params: Dict[str, Any] = {"aid": account_id, "lim": limit}
        if status:
            sql += " AND status = :st"
            params["st"] = status
        sql += " ORDER BY id DESC LIMIT :lim"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {
            "ok": True,
            "account_id": account_id,
            "count": len(rows),
            "orders": [_row_to_dict(r) for r in rows],
        }

    def list_fills(self, account_id: int, limit: int = 200) -> Dict[str, Any]:
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_fill
                    WHERE account_id = :aid
                    ORDER BY trade_date DESC, id DESC
                    LIMIT :lim
                    """
                ),
                {"aid": account_id, "lim": limit},
            ).fetchall()
        return {
            "ok": True,
            "account_id": account_id,
            "count": len(rows),
            "fills": [_row_to_dict(r) for r in rows],
        }

    def get_equity_curve(self, account_id: int, limit: int = 500) -> Dict[str, Any]:
        self._ensure_tables()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_equity_daily
                    WHERE account_id = :aid
                    ORDER BY trade_date ASC
                    LIMIT :lim
                    """
                ),
                {"aid": account_id, "lim": limit},
            ).fetchall()
        curve = [_row_to_dict(r) for r in rows]
        return {
            "ok": True,
            "account_id": account_id,
            "count": len(curve),
            "equity": curve,
        }

    def get_stats(self, account_id: int) -> Dict[str, Any]:
        self._ensure_tables()
        marked = self.mark_to_market(account_id, write_equity=False)
        if not marked.get("ok"):
            return marked

        with self.engine.connect() as conn:
            acct = conn.execute(
                sa_text("SELECT * FROM paper_account WHERE id = :id"),
                {"id": account_id},
            ).fetchone()
            fills = conn.execute(
                sa_text(
                    """
                    SELECT side, quantity, price, amount, trade_date, ts_code
                    FROM paper_fill WHERE account_id = :aid
                    ORDER BY trade_date ASC, id ASC
                    """
                ),
                {"aid": account_id},
            ).fetchall()
            equity_rows = conn.execute(
                sa_text(
                    """
                    SELECT equity, trade_date, daily_return
                    FROM paper_equity_daily
                    WHERE account_id = :aid
                    ORDER BY trade_date ASC
                    """
                ),
                {"aid": account_id},
            ).fetchall()
            pending_n = conn.execute(
                sa_text(
                    """
                    SELECT COUNT(*) FROM paper_order
                    WHERE account_id = :aid AND status = 'pending'
                    """
                ),
                {"aid": account_id},
            ).scalar()

        initial = _f(acct.initial_cash)
        equity = _f(marked.get("equity"), initial)
        total_return = (equity / initial - 1.0) if initial > 0 else 0.0

        # 简化已实现盈亏：按卖出 FIFO 近似（用成交序列）
        lots: Dict[str, List[Tuple[float, float]]] = {}  # ts_code -> [(qty, cost)]
        realized = 0.0
        wins = 0
        closed = 0
        buy_count = 0
        sell_count = 0

        for f in fills:
            code = f.ts_code
            qty = _f(f.quantity)
            px = _f(f.price)
            if f.side == "buy":
                buy_count += 1
                lots.setdefault(code, []).append([qty, px])
            else:
                sell_count += 1
                remain = qty
                pnl = 0.0
                queue = lots.setdefault(code, [])
                while remain > 1e-9 and queue:
                    lot_qty, lot_cost = queue[0]
                    take = min(remain, lot_qty)
                    pnl += (px - lot_cost) * take
                    lot_qty -= take
                    remain -= take
                    if lot_qty <= 1e-9:
                        queue.pop(0)
                    else:
                        queue[0][0] = lot_qty
                realized += pnl
                closed += 1
                if pnl > 0:
                    wins += 1

        # 最大回撤
        max_dd = 0.0
        if equity_rows:
            peak = _f(equity_rows[0].equity)
            for r in equity_rows:
                eq = _f(r.equity)
                if eq > peak:
                    peak = eq
                if peak > 0:
                    dd = eq / peak - 1.0
                    if dd < max_dd:
                        max_dd = dd

        return {
            "ok": True,
            "account_id": account_id,
            "account": marked.get("account"),
            "positions": marked.get("positions") or [],
            "stats": {
                "initial_cash": initial,
                "cash": _f(marked.get("cash")),
                "market_value": _f(marked.get("market_value")),
                "equity": equity,
                "total_return": round(total_return, 6),
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": round(
                    sum(_f(p.get("unrealized_pnl")) for p in (marked.get("positions") or [])),
                    2,
                ),
                "max_drawdown": round(max_dd, 6),
                "fill_count": len(fills),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "closed_trades": closed,
                "win_rate": round(wins / closed, 4) if closed else 0.0,
                "pending_orders": int(pending_n or 0),
                "equity_points": len(equity_rows),
            },
        }

    def cancel_order(self, account_id: int, order_id: int) -> Dict[str, Any]:
        self._ensure_tables()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_order
                    WHERE id = :oid AND account_id = :aid
                    FOR UPDATE
                    """
                ),
                {"oid": order_id, "aid": account_id},
            ).fetchone()
            if not row:
                return {"ok": False, "error": "订单不存在"}
            if row.status != "pending":
                return {"ok": False, "error": f"仅 pending 可取消，当前: {row.status}"}
            conn.execute(
                sa_text(
                    """
                    UPDATE paper_order SET
                        status = 'cancelled',
                        updated_at = NOW()
                    WHERE id = :oid
                    """
                ),
                {"oid": order_id},
            )
            out = conn.execute(
                sa_text("SELECT * FROM paper_order WHERE id = :oid"),
                {"oid": order_id},
            ).fetchone()
        return {"ok": True, "order": _row_to_dict(out)}

    # ------------------------------------------------------------------ #
    # 决策审计
    # ------------------------------------------------------------------ #
    def log_decision(
        self,
        account_id: int,
        *,
        session_id: Optional[str] = None,
        user_query: Optional[str] = None,
        assistant_summary: Optional[str] = None,
        tool_trace: Optional[List[Any]] = None,
        order_ids: Optional[List[int]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_tables()
        import json

        with self.engine.begin() as conn:
            acct = conn.execute(
                sa_text("SELECT id FROM paper_account WHERE id = :id"),
                {"id": account_id},
            ).fetchone()
            if not acct:
                return {"ok": False, "error": f"账户不存在: {account_id}"}

            ids = [int(x) for x in (order_ids or []) if x is not None]
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_decision_log (
                        account_id, session_id, user_query, assistant_summary,
                        tool_trace, order_ids, meta
                    ) VALUES (
                        :aid, :sid, :uq, :asum,
                        CAST(:trace AS jsonb), :oids, CAST(:meta AS jsonb)
                    )
                    RETURNING *
                    """
                ),
                {
                    "aid": account_id,
                    "sid": session_id,
                    "uq": user_query,
                    "asum": (assistant_summary or "")[:8000],
                    "trace": json.dumps(tool_trace or [], ensure_ascii=False, default=str),
                    "oids": ids or None,
                    "meta": json.dumps(meta or {}, ensure_ascii=False, default=str),
                },
            ).fetchone()
        return {"ok": True, "decision": _row_to_dict(row)}

    def list_decisions(
        self,
        account_id: int,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_tables()
        sql = """
            SELECT * FROM paper_decision_log
            WHERE account_id = :aid
        """
        params: Dict[str, Any] = {"aid": account_id, "lim": limit}
        if session_id:
            sql += " AND session_id = :sid"
            params["sid"] = session_id
        sql += " ORDER BY id DESC LIMIT :lim"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {
            "ok": True,
            "account_id": account_id,
            "count": len(rows),
            "decisions": [_row_to_dict(r) for r in rows],
        }


paper_broker_service = PaperBrokerService()
