"""
用户自定义表：用大模型根据自然语言设计 DDL，并在 PostgreSQL 中创建。
元数据写入 user_custom_table，供存储层展示与 LLM 工具发现。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = {
    "SMALLINT", "INTEGER", "INT", "BIGINT",
    "REAL", "DOUBLE PRECISION", "NUMERIC", "DECIMAL",
    "BOOLEAN", "BOOL",
    "TEXT", "VARCHAR", "CHAR",
    "DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIME",
    "JSONB", "JSON",
    "UUID",
    "VECTOR",  # pgvector；无扩展时建表前会尝试 CREATE EXTENSION
}

_SYSTEM_PROMPT = """你是 PostgreSQL 表结构设计助手。根据用户需求设计一张业务表。

【输出格式——必须严格遵守】
- 回复的第一个字符必须是 {
- 回复的最后一个字符必须是 }
- 只输出一个 JSON 对象，禁止 Markdown 代码块，禁止任何解释、前言、后记、思考过程
- 不要输出 <think> 或其它标签

JSON 字段：
{
  "table_name": "u_英文蛇形名",
  "label": "中文短名",
  "category": "用户自定义",
  "desc": "一句话说明用途，方便后续大模型选用",
  "columns": [
    {"name": "列名蛇形", "type": "VARCHAR(64)|INTEGER|BIGINT|NUMERIC(20,4)|TEXT|DATE|TIMESTAMPTZ|BOOLEAN|JSONB", "nullable": true, "comment": "中文含义"}
  ],
  "primary_key": ["id"]
}

硬性规则：
1. table_name 必须以 u_ 开头，仅小写字母数字下划线，长度≤48。
2. 必须有主键；若无合适业务键，加 id（类型用 BIGSERIAL）。
3. 类型只能用常见 PG 类型；字符串优先 VARCHAR(n) 或 TEXT。
4. 不要外键、不要引用其它库表。
5. A 股相关：股票代码列名 ts_code VARCHAR(16)，板块/board 用 VARCHAR(16) 名 board_type。
"""


class CustomTableService:
    def __init__(self) -> None:
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def ensure(self) -> None:
        self._ensure()

    def _ensure(self) -> None:
        if self._ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS user_custom_table (
                id            SERIAL PRIMARY KEY,
                table_name    VARCHAR(64) NOT NULL UNIQUE,
                label         VARCHAR(80) NOT NULL,
                category      VARCHAR(40) NOT NULL DEFAULT '用户自定义',
                description   TEXT,
                columns_json  JSONB,
                ddl_sql       TEXT,
                prompt        TEXT,
                profile_id    INT,
                user_id       VARCHAR(64) NOT NULL DEFAULT 'default',
                for_llm       BOOLEAN NOT NULL DEFAULT TRUE,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_user_custom_table_user ON user_custom_table (user_id, created_at DESC)",
        ]
        with self.engine.begin() as conn:
            for sql in ddl:
                conn.execute(sa_text(sql))
        self._ready = True

    def list_meta(self, user_id: str = "default") -> List[Dict[str, Any]]:
        self._ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT id, table_name, label, category, description, columns_json,
                           for_llm, profile_id, created_at
                    FROM user_custom_table
                    WHERE user_id = :u OR user_id = 'default'
                    ORDER BY created_at DESC
                    """
                ),
                {"u": user_id},
            ).mappings().fetchall()
        out = []
        for r in rows:
            out.append({
                "id": int(r["id"]),
                "table_name": r["table_name"],
                "label": r["label"],
                "category": r["category"] or "用户自定义",
                "desc": r["description"] or "",
                "columns": r["columns_json"] if isinstance(r["columns_json"], list) else (r["columns_json"] or []),
                "for_llm": bool(r["for_llm"]),
                "profile_id": r["profile_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })
        return out

    def meta_map(self) -> Dict[str, Dict[str, str]]:
        """供存储层列表合并：table_name → {name, category, desc, source}"""
        self._ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    "SELECT table_name, label, category, description FROM user_custom_table"
                )
            ).mappings().fetchall()
        return {
            str(r["table_name"]): {
                "name": r["label"] or r["table_name"],
                "category": r["category"] or "用户自定义",
                "desc": r["description"] or "",
                "source": "用户自定义·大模型建表",
            }
            for r in rows
        }

    def _normalize_table_name(self, name: str) -> str:
        s = re.sub(r"[^a-z0-9_]", "_", (name or "").strip().lower())
        s = re.sub(r"_+", "_", s).strip("_")
        if not s:
            s = f"u_table_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        if not s.startswith("u_"):
            s = "u_" + s
        return s[:48]

    def _validate_type(self, typ: str) -> str:
        t = (typ or "TEXT").strip().upper()
        t = t.replace("SERIAL", "INTEGER")  # BIGSERIAL handled separately
        base = t.split("(", 1)[0].strip()
        if base in ("VARCHAR", "CHAR", "NUMERIC", "DECIMAL", "VECTOR"):
            if not re.fullmatch(
                r"(VARCHAR|CHAR|NUMERIC|DECIMAL|VECTOR)(\(\d+(,\s*\d+)?\))?", t
            ):
                raise ValueError(f"非法类型: {typ}")
            return t
        if base == "DOUBLE" and "PRECISION" in t:
            return "DOUBLE PRECISION"
        if base not in _ALLOWED_TYPES and t not in _ALLOWED_TYPES:
            raise ValueError(f"不允许的类型: {typ}")
        return t

    def _validate_ident(self, name: str, *, kind: str = "列") -> str:
        s = (name or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", s):
            raise ValueError(f"非法{kind}名: {name}")
        return s

    def build_ddl(self, design: Dict[str, Any]) -> Dict[str, Any]:
        table = self._normalize_table_name(str(design.get("table_name") or ""))
        label = str(design.get("label") or table).strip()[:80]
        category = str(design.get("category") or "用户自定义").strip()[:40] or "用户自定义"
        desc = str(design.get("desc") or design.get("description") or "").strip()[:500]
        cols_in = design.get("columns") or []
        if not isinstance(cols_in, list) or not cols_in:
            raise ValueError("至少需要一列")
        pk = design.get("primary_key") or []
        if isinstance(pk, str):
            pk = [pk]
        pk = [self._validate_ident(p, kind="主键") for p in pk]

        col_defs: List[str] = []
        col_meta: List[Dict[str, Any]] = []
        names: List[str] = []
        for c in cols_in:
            if not isinstance(c, dict):
                continue
            cname = self._validate_ident(str(c.get("name") or ""), kind="列")
            if cname in names:
                raise ValueError(f"重复列名: {cname}")
            names.append(cname)
            raw_type = str(c.get("type") or "TEXT")
            # 允许 BIGSERIAL / SERIAL 作自增主键
            tu = raw_type.strip().upper()
            if tu in ("BIGSERIAL", "SERIAL", "SMALLSERIAL"):
                ctype = tu
            else:
                ctype = self._validate_type(raw_type)
            nullable = bool(c.get("nullable", True))
            if cname in pk:
                nullable = False
            null_sql = "" if nullable else " NOT NULL"
            col_defs.append(f'  "{cname}" {ctype}{null_sql}')
            col_meta.append({
                "name": cname,
                "type": ctype,
                "nullable": nullable,
                "comment": str(c.get("comment") or c.get("label") or "")[:120],
            })

        if not pk:
            if "id" not in names:
                col_defs.insert(0, '  "id" BIGSERIAL NOT NULL')
                col_meta.insert(0, {"name": "id", "type": "BIGSERIAL", "nullable": False, "comment": "自增主键"})
                names.insert(0, "id")
            pk = ["id"]

        for p in pk:
            if p not in names:
                raise ValueError(f"主键列不存在: {p}")

        pk_sql = ", ".join(f'"{p}"' for p in pk)
        ddl = (
            f'CREATE TABLE IF NOT EXISTS "{table}" (\n'
            + ",\n".join(col_defs)
            + f',\n  PRIMARY KEY ({pk_sql})\n)'
        )
        return {
            "table_name": table,
            "label": label,
            "category": category,
            "desc": desc,
            "columns": col_meta,
            "primary_key": pk,
            "ddl": ddl,
        }

    def _extract_json(self, text: str) -> Dict[str, Any]:
        s = (text or "").strip()
        if not s:
            raise ValueError("模型返回为空，未能得到 JSON。请检查 API Key / 网络后重试。")

        # 去掉常见包裹：思考标签、Markdown 代码块
        s = re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.I)
        s = re.sub(r"<thinking>[\s\S]*?</thinking>", "", s, flags=re.I)
        s = re.sub(r"```(?:json)?\s*", "", s, flags=re.I)
        s = s.replace("```", "").strip()

        # 若整段就是 JSON
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

        # 从第一个 { 起做括号匹配，避免贪婪把后面废话吃进来
        start = s.find("{")
        if start < 0:
            preview = s[:240].replace("\n", " ")
            raise ValueError(
                "模型未返回合法 JSON（回复里没有 { ... } 对象）。"
                f" 实际开头：{preview or '(空)'}"
            )
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            preview = s[start : start + 240].replace("\n", " ")
            raise ValueError(f"模型 JSON 不完整（括号未闭合）。片段：{preview}")

        chunk = s[start : end + 1]
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError as e:
            raise ValueError(f"模型返回的 JSON 无法解析：{e}。片段：{chunk[:240]}")
        if not isinstance(obj, dict):
            raise ValueError("模型 JSON 根节点必须是对象")
        return obj

    async def design_with_llm(
        self,
        prompt: str,
        *,
        profile_id: Optional[int] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        from server.chat_service import ChatRequest, chat_service
        from server.llm_profile_service import llm_profile_service

        text = (prompt or "").strip()
        if len(text) < 4:
            raise ValueError("请用更具体的话描述你需要的表（至少几个字）")

        pid = profile_id
        if pid is None:
            profiles = llm_profile_service.list_profiles(user_id)
            active = next((p for p in profiles if p.get("is_active") and p.get("enabled")), None)
            if not active:
                active = next((p for p in profiles if p.get("enabled")), None)
            if not active:
                raise ValueError("请先在「大模型对话」保存并启用一套模型配置")
            pid = int(active["id"])

        user_msg = (
            f"用户需求：{text}\n\n"
            "请只输出符合系统要求的那个 JSON 对象，不要其它文字。"
            "若提到向量/embedding/vector，列类型用 VECTOR(1536)（可按需求改维度）。"
        )
        last_err: Optional[Exception] = None
        content = ""
        resp = None
        for attempt in range(2):
            req = ChatRequest(
                query=user_msg if attempt == 0 else (
                    user_msg
                    + "\n\n上次输出无法解析为 JSON，请重新输出，第一个字符必须是 {，最后一个字符必须是 }。"
                ),
                system_prompt=_SYSTEM_PROMPT,
                profile_id=int(pid),
                user_id=user_id,
                # 清空占位默认值，强制走 profile 里的 deepseek-chat 等真实模型名
                model="",
                provider="",
                temperature=0.1,
                max_tokens=2048,
            )
            resp = await chat_service.chat(req)
            content = (resp.content or "").strip()
            if resp.provider in ("fallback", "error"):
                preview = content[:300].replace("\n", " ")
                raise ValueError(
                    f"大模型调用未成功（provider={resp.provider}），无法建表。"
                    f" 请检查 DeepSeek API Key / 网络。详情：{preview}"
                )
            try:
                raw = self._extract_json(content)
                built = self.build_ddl(raw)
                built["profile_id"] = int(pid)
                built["model"] = resp.model
                built["provider"] = resp.provider
                built["raw_model_output"] = content[:2000]
                return built
            except Exception as e:
                last_err = e
                logger.warning("AI 建表 JSON 解析失败 attempt=%s: %s | preview=%s", attempt + 1, e, content[:200])

        assert last_err is not None
        raise ValueError(str(last_err))

    def create_from_design(
        self,
        design: Dict[str, Any],
        *,
        prompt: str = "",
        profile_id: Optional[int] = None,
        user_id: str = "default",
        for_llm: bool = True,
    ) -> Dict[str, Any]:
        self._ensure()
        built = self.build_ddl(design)
        table = built["table_name"]

        # 禁止覆盖系统表
        from server.storage_service import PG_TABLES_INFO

        if table in PG_TABLES_INFO or table in ("user_llm_profile", "user_custom_table"):
            raise ValueError(f"表名与系统表冲突: {table}")

        with self.engine.begin() as conn:
            exists = conn.execute(
                sa_text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            ).scalar()
            if exists:
                raise ValueError(f"表已存在: {table}，请换一个名字或先删除")

            # 含 vector 列时尽量启用 pgvector
            needs_vector = any(
                str(c.get("type") or "").upper().startswith("VECTOR")
                for c in built["columns"]
            )
            if needs_vector:
                try:
                    conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
                except Exception as e:
                    raise ValueError(
                        f"表含 vector 列，但启用 pgvector 失败: {e}。"
                        "可改用 JSONB/TEXT 存向量，或先在库中安装 vector 扩展。"
                    ) from e

            conn.execute(sa_text(built["ddl"]))
            conn.execute(
                sa_text(
                    """
                    INSERT INTO user_custom_table (
                      table_name, label, category, description, columns_json,
                      ddl_sql, prompt, profile_id, user_id, for_llm
                    ) VALUES (
                      :table_name, :label, :category, :description, CAST(:columns_json AS jsonb),
                      :ddl_sql, :prompt, :profile_id, :user_id, :for_llm
                    )
                    ON CONFLICT (table_name) DO UPDATE SET
                      label = EXCLUDED.label,
                      category = EXCLUDED.category,
                      description = EXCLUDED.description,
                      columns_json = EXCLUDED.columns_json,
                      ddl_sql = EXCLUDED.ddl_sql,
                      prompt = EXCLUDED.prompt,
                      profile_id = EXCLUDED.profile_id,
                      for_llm = EXCLUDED.for_llm
                    """
                ),
                {
                    "table_name": table,
                    "label": built["label"],
                    "category": built["category"],
                    "description": built["desc"],
                    "columns_json": json.dumps(built["columns"], ensure_ascii=False),
                    "ddl_sql": built["ddl"],
                    "prompt": (prompt or "")[:2000],
                    "profile_id": profile_id,
                    "user_id": user_id,
                    "for_llm": bool(for_llm),
                },
            )

        return {
            "ok": True,
            "table_name": table,
            "label": built["label"],
            "category": built["category"],
            "desc": built["desc"],
            "columns": built["columns"],
            "ddl": built["ddl"],
            "for_llm": bool(for_llm),
            "hint": f"已创建 {table}。大模型可通过 sql-toolkit list-tables / info-table / query-exec 使用；存储层可预览。",
        }

    async def ai_create(
        self,
        prompt: str,
        *,
        profile_id: Optional[int] = None,
        user_id: str = "default",
        dry_run: bool = False,
        for_llm: bool = True,
        table_name_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        design = await self.design_with_llm(prompt, profile_id=profile_id, user_id=user_id)
        if table_name_override:
            design["table_name"] = table_name_override
        if dry_run:
            return {"ok": True, "dry_run": True, "design": design}
        created = self.create_from_design(
            design,
            prompt=prompt,
            profile_id=design.get("profile_id") or profile_id,
            user_id=user_id,
            for_llm=for_llm,
        )
        created["design"] = {k: design.get(k) for k in ("model", "provider", "profile_id")}
        return created

    def llm_catalog(self) -> List[Dict[str, Any]]:
        """给大模型看的自定义表简表。"""
        self._ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT table_name, label, description, columns_json
                    FROM user_custom_table
                    WHERE for_llm = TRUE
                    ORDER BY created_at DESC
                    """
                )
            ).mappings().fetchall()
        catalog = []
        for r in rows:
            cols = r["columns_json"] if isinstance(r["columns_json"], list) else (r["columns_json"] or [])
            catalog.append({
                "table_name": r["table_name"],
                "label": r["label"],
                "desc": r["description"] or "",
                "columns": [
                    f"{c.get('name')}({c.get('type')})"
                    for c in cols if isinstance(c, dict)
                ],
            })
        return catalog

    def is_custom_table(self, table_name: str, user_id: Optional[str] = None) -> bool:
        """是否为用户通过 AI 建表登记的表（系统表永远返回 False）。"""
        self._ensure()
        name = (table_name or "").strip().strip('"').lower()
        if not name or not name.startswith("u_"):
            return False
        from server.storage_service import PG_TABLES_INFO
        from server.hidden_tables import is_hidden_pg_table

        if name in PG_TABLES_INFO or is_hidden_pg_table(name) or name == "user_custom_table":
            return False
        sql = "SELECT 1 FROM user_custom_table WHERE table_name = :t"
        params: Dict[str, Any] = {"t": name}
        if user_id:
            sql += " AND (user_id = :u OR user_id = 'default')"
            params["u"] = user_id
        with self.engine.connect() as conn:
            return bool(conn.execute(sa_text(sql), params).scalar())

    def _meta_for(self, table_name: str, user_id: str = "default") -> Dict[str, Any]:
        self._ensure()
        name = (table_name or "").strip().strip('"').lower()
        if not self.is_custom_table(name, user_id=user_id):
            raise ValueError(f"不是可编辑的自定义表: {table_name}")
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT table_name, columns_json
                    FROM user_custom_table
                    WHERE table_name = :t AND (user_id = :u OR user_id = 'default')
                    LIMIT 1
                    """
                ),
                {"t": name, "u": user_id},
            ).mappings().first()
        if not row:
            raise ValueError(f"自定义表元数据不存在: {name}")
        cols = row["columns_json"] if isinstance(row["columns_json"], list) else (row["columns_json"] or [])
        return {"table_name": name, "columns": cols}

    @staticmethod
    def _is_serial_type(typ: str) -> bool:
        return str(typ or "").strip().upper() in ("BIGSERIAL", "SERIAL", "SMALLSERIAL")

    def insert_row(
        self,
        table_name: str,
        values: Dict[str, Any],
        *,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """向用户自定义表插入一行（仅 u_* 登记表）。"""
        meta = self._meta_for(table_name, user_id=user_id)
        name = meta["table_name"]
        col_defs = {str(c.get("name")): c for c in meta["columns"] if isinstance(c, dict) and c.get("name")}
        payload: Dict[str, Any] = {}
        for key, raw in (values or {}).items():
            k = str(key).strip().lower()
            if k not in col_defs:
                continue
            if self._is_serial_type(str(col_defs[k].get("type") or "")) and (raw is None or raw == ""):
                continue
            if raw is None or raw == "":
                continue
            payload[k] = self._coerce_value(raw, str(col_defs[k].get("type") or "TEXT"))
        if not payload:
            # 允许纯自增主键空表插入
            serial_cols = [n for n, c in col_defs.items() if self._is_serial_type(str(c.get("type") or ""))]
            if not serial_cols:
                raise ValueError("没有可写入的列值")
            sql = f'INSERT INTO "{name}" DEFAULT VALUES RETURNING *'
            params: Dict[str, Any] = {}
        else:
            cols_sql = ", ".join(f'"{c}"' for c in payload)
            val_parts = []
            for c in payload:
                typ = str(col_defs[c].get("type") or "TEXT")
                if typ.upper().startswith("VECTOR"):
                    val_parts.append(f"CAST(:{c} AS vector)")
                elif typ.upper().startswith("JSONB") or typ.upper() == "JSON":
                    val_parts.append(f"CAST(:{c} AS jsonb)" if "JSONB" in typ.upper() else f"CAST(:{c} AS json)")
                else:
                    val_parts.append(f":{c}")
            vals_sql = ", ".join(val_parts)
            sql = f'INSERT INTO "{name}" ({cols_sql}) VALUES ({vals_sql}) RETURNING *'
            params = payload
        with self.engine.begin() as conn:
            row = conn.execute(sa_text(sql), params).mappings().first()
        return {"ok": True, "table_name": name, "row": dict(row) if row else {}}

    def update_row(
        self,
        table_name: str,
        pk_value: Any,
        values: Dict[str, Any],
        *,
        user_id: str = "default",
        pk_column: str = "id",
    ) -> Dict[str, Any]:
        """按主键更新自定义表一行。"""
        meta = self._meta_for(table_name, user_id=user_id)
        name = meta["table_name"]
        col_defs = {str(c.get("name")): c for c in meta["columns"] if isinstance(c, dict) and c.get("name")}
        pk = (pk_column or "id").strip().lower()
        if pk not in col_defs:
            raise ValueError(f"主键列不存在: {pk}")
        payload: Dict[str, Any] = {}
        for key, raw in (values or {}).items():
            k = str(key).strip().lower()
            if k not in col_defs or k == pk:
                continue
            if self._is_serial_type(str(col_defs[k].get("type") or "")):
                continue
            if raw is None or raw == "":
                payload[k] = None
            else:
                payload[k] = self._coerce_value(raw, str(col_defs[k].get("type") or "TEXT"))
        if not payload:
            raise ValueError("没有可更新的字段")
        set_parts = []
        for c in payload:
            typ = str(col_defs[c].get("type") or "TEXT")
            if payload[c] is None:
                set_parts.append(f'"{c}" = NULL')
            elif typ.upper().startswith("VECTOR"):
                set_parts.append(f'"{c}" = CAST(:{c} AS vector)')
            elif typ.upper().startswith("JSONB"):
                set_parts.append(f'"{c}" = CAST(:{c} AS jsonb)')
            elif typ.upper() == "JSON":
                set_parts.append(f'"{c}" = CAST(:{c} AS json)')
            else:
                set_parts.append(f'"{c}" = :{c}')
        sets = ", ".join(set_parts)
        params = {k: v for k, v in payload.items() if v is not None}
        params["_pk"] = pk_value
        sql = f'UPDATE "{name}" SET {sets} WHERE "{pk}" = :_pk RETURNING *'
        with self.engine.begin() as conn:
            row = conn.execute(sa_text(sql), params).mappings().first()
        if not row:
            raise ValueError(f"未找到主键 {pk}={pk_value} 的行")
        return {"ok": True, "table_name": name, "row": dict(row)}

    def _coerce_value(self, raw: Any, typ: str) -> Any:
        t = (typ or "TEXT").strip().upper()
        base = t.split("(", 1)[0].strip()
        if base == "VECTOR":
            if isinstance(raw, (list, tuple)):
                return "[" + ",".join(str(float(x)) for x in raw) + "]"
            s = str(raw).strip()
            if s.startswith("[") and s.endswith("]"):
                return s
            raise ValueError("vector 列请填如 [0.1,0.2,...] 的数组")
        if base in ("JSONB", "JSON"):
            if isinstance(raw, (dict, list)):
                return json.dumps(raw, ensure_ascii=False)
            return str(raw)
        if base in ("BOOLEAN", "BOOL"):
            if isinstance(raw, bool):
                return raw
            s = str(raw).strip().lower()
            if s in ("1", "true", "t", "yes", "y"):
                return True
            if s in ("0", "false", "f", "no", "n"):
                return False
            raise ValueError(f"无法解析布尔值: {raw}")
        if base in ("SMALLINT", "INTEGER", "INT", "BIGINT"):
            return int(raw)
        if base in ("REAL", "DOUBLE", "NUMERIC", "DECIMAL") or t.startswith("DOUBLE"):
            return float(raw)
        return raw

    def delete_table(self, table_name: str, *, user_id: str = "default") -> Dict[str, Any]:
        """
        仅允许删除本用户（或 default）登记过的自定义表。
        系统表 / 未登记表一律拒绝，无 DROP/ALTER 权限。
        """
        self._ensure()
        name = (table_name or "").strip().strip('"').lower()
        if not re.fullmatch(r"u_[a-z][a-z0-9_]{0,46}", name or ""):
            raise ValueError("只能删除 u_ 前缀的用户自定义表")

        from server.storage_service import PG_TABLES_INFO
        from server.hidden_tables import is_hidden_pg_table

        if name in PG_TABLES_INFO or is_hidden_pg_table(name):
            raise ValueError("禁止操作系统内置表")

        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT id, label, user_id FROM user_custom_table
                    WHERE table_name = :t
                      AND (user_id = :u OR user_id = 'default')
                    """
                ),
                {"t": name, "u": user_id},
            ).mappings().fetchone()
            if not row:
                raise ValueError(
                    f"表 {name} 不是你创建的自定义表，或不存在登记记录；系统表无法删除"
                )

            # 二次确认物理表仍是用户表（防止误删被抢占的名字）
            exists = conn.execute(
                sa_text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": name},
            ).scalar()
            if exists:
                conn.execute(sa_text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
            conn.execute(
                sa_text("DELETE FROM user_custom_table WHERE table_name = :t"),
                {"t": name},
            )

        return {
            "ok": True,
            "deleted": name,
            "label": row["label"],
            "hint": f"已删除自定义表 {name}。系统表不受影响。",
        }


custom_table_service = CustomTableService()
