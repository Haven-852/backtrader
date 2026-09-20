"""
QuantSkills 外部技能包运行时（SKILL.md 型 Agent Skill）

目录约定（位于 tools/ 下）:
  - skill-market-daily-review
  - skill-futures-deepview-analyst
  - ill-a-share-stock-dossier   # 上游仓库名 skill-a-share-stock-dossier

提供：读取 SKILL.md / 参考文档 / 构建任务提示 / 运行内置脚本
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).resolve().parent

# skill_id → 本地目录名
PACKAGE_DIRS: Dict[str, str] = {
    "skill-market-daily-review": "skill-market-daily-review",
    "skill-futures-deepview-analyst": "skill-futures-deepview-analyst",
    "ill-a-share-stock-dossier": "ill-a-share-stock-dossier",
}

# 展示元数据（中文名以目录语义为准，修正用户描述中的交叉笔误）
PACKAGE_META: Dict[str, Dict[str, Any]] = {
    "skill-market-daily-review": {
        "name": "市场每日复盘",
        "icon": "newspaper",
        "category": "市场分析",
        "desc": (
            "A 股收盘复盘 Agent Skill（QuantSkills）。"
            "覆盖指数估值、市场宽度、涨跌停、行业概念、龙虎榜、大宗、两融、北向；"
            "依赖 Pandadata，数字须可溯源。源码目录：tools/skill-market-daily-review"
        ),
        "upstream": "https://github.com/quantskills/skill-market-daily-review",
    },
    "skill-futures-deepview-analyst": {
        "name": "期货 DeepView 分析",
        "icon": "chart-line",
        "category": "期货分析",
        "desc": (
            "期货 DeepView 综合研判 Agent Skill（QuantSkills）。"
            "席位持仓、基差、期限结构、仓单库存、虚实盘比、现货利润、跨期套利。"
            "源码目录：tools/skill-futures-deepview-analyst"
        ),
        "upstream": "https://github.com/quantskills/skill-futures-deepview-analyst",
    },
    "ill-a-share-stock-dossier": {
        "name": "A股个股档案",
        "icon": "folder-open",
        "category": "个股研究",
        "desc": (
            "A 股个股尽调档案 Agent Skill（QuantSkills，上游 skill-a-share-stock-dossier）。"
            "公司画像、财务、分红资本运作、股东行为、质押解禁减持、资金面。"
            "源码目录：tools/ill-a-share-stock-dossier"
        ),
        "upstream": "https://github.com/quantskills/skill-a-share-stock-dossier",
    },
}


def _root(skill_id: str) -> Path:
    dirname = PACKAGE_DIRS.get(skill_id)
    if not dirname:
        raise ValueError(f"未知 QuantSkill: {skill_id}")
    path = _TOOLS_DIR / dirname
    if not path.is_dir():
        raise FileNotFoundError(f"技能目录不存在: {path}")
    return path


def _safe_relpath(root: Path, rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("非法相对路径")
    target = (root / rel).resolve()
    root_r = root.resolve()
    if not str(target).startswith(str(root_r)):
        raise ValueError("路径越界")
    return target


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if not text.startswith("---"):
        return meta
    parts = text.split("---", 2)
    if len(parts) < 3:
        return meta
    block = parts[1]
    # 轻量解析常用字段
    for key in ("name", "description", "license"):
        m = re.search(rf"^{key}:\s*(.+)$", block, re.M)
        if m:
            meta[key] = m.group(1).strip().strip('"')
    req = re.findall(r"^\s*-\s*(skill-[\w-]+)\s*$", block, re.M)
    if req:
        meta["requires"] = req
    # 多行 description
    m2 = re.search(r"^description:\s*(.+?)(?=\n\w|\nlicense:)", block, re.S | re.M)
    if m2:
        desc = re.sub(r"\n\s+", " ", m2.group(1)).strip()
        meta["description"] = desc
    return meta


def _extract_qsh_form(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"```json\s+qsh-form\s*\n(.*?)```", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception as e:
        logger.warning("qsh-form JSON 解析失败: %s", e)
        return None


def _render_mustache_lite(template: str, ctx: Dict[str, Any]) -> str:
    """极简 {{var}} / {{#var}}...{{/var}} 渲染"""
    out = template or ""

    def section(match: re.Match) -> str:
        key = match.group(1)
        body = match.group(2)
        val = ctx.get(key)
        if not val:
            return ""
        return body.replace("{{" + key + "}}", str(val))

    out = re.sub(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", section, out, flags=re.S)
    for k, v in ctx.items():
        if v is None:
            continue
        out = out.replace("{{" + k + "}}", str(v))
    # 清掉未替换的 section
    out = re.sub(r"\{\{#\w+\}\}.*?\{\{/\w+\}\}", "", out, flags=re.S)
    out = re.sub(r"\{\{\w+\}\}", "", out)
    return out.strip()


class QuantSkillRuntime:
    """外部 QuantSkills 包适配器"""

    def package_root(self, skill_id: str) -> str:
        return str(_root(skill_id))

    def load_skill(self, skill_id: str, max_chars: int = 20000) -> Dict[str, Any]:
        root = _root(skill_id)
        skill_path = root / "SKILL.md"
        if not skill_path.exists():
            return {"ok": False, "error": f"缺少 SKILL.md: {skill_path}"}
        text = skill_path.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        form = _extract_qsh_form(text)
        body = text
        if len(body) > max_chars:
            body = body[:max_chars] + "\n\n...（SKILL.md 已截断）"
        pkg = PACKAGE_META.get(skill_id, {})
        return {
            "ok": True,
            "skill_id": skill_id,
            "name": pkg.get("name") or meta.get("name"),
            "path": str(skill_path),
            "root": str(root),
            "upstream": pkg.get("upstream"),
            "frontmatter": meta,
            "form_fields": (form or {}).get("fields") or [],
            "prompt_template": (form or {}).get("prompt_template"),
            "requires": meta.get("requires") or ["skill-pandadata-api"],
            "content": body,
            "note": (
                "这是 Agent Skill 工作流说明。实际拉数需 Pandadata（skill-pandadata-api / panda_data）；"
                "本平台也可用 datahubco-data / sql-toolkit 作为数据替代来源，但须在报告中标注。"
            ),
        }

    def list_resources(self, skill_id: str) -> Dict[str, Any]:
        root = _root(skill_id)
        items: List[Dict[str, Any]] = []
        for dir_name in ("references", "scripts", "agents"):
            d = root / dir_name
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if p.is_file() and ".git" not in p.parts:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                    items.append({
                        "path": rel,
                        "size": p.stat().st_size,
                        "kind": dir_name,
                    })
        # 顶层文档
        for name in ("SKILL.md", "README.md", "README.en.md", "LICENSE"):
            p = root / name
            if p.is_file():
                items.insert(0, {"path": name, "size": p.stat().st_size, "kind": "root"})
        return {
            "ok": True,
            "skill_id": skill_id,
            "root": str(root),
            "count": len(items),
            "resources": items,
        }

    def read_resource(self, skill_id: str, path: str, max_chars: int = 30000) -> Dict[str, Any]:
        root = _root(skill_id)
        try:
            target = _safe_relpath(root, path)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not target.is_file():
            return {"ok": False, "error": f"文件不存在: {path}"}
        text = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n\n...（已截断）"
        return {
            "ok": True,
            "skill_id": skill_id,
            "path": path.replace("\\", "/"),
            "chars": len(text),
            "truncated": truncated,
            "content": text,
        }

    def build_prompt(
        self,
        skill_id: str,
        *,
        task: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        include_skill: bool = True,
        include_loader: bool = True,
        max_skill_chars: int = 12000,
    ) -> Dict[str, Any]:
        """按 qsh-form 模板拼装可直接交给大模型的任务提示"""
        loaded = self.load_skill(skill_id, max_chars=max_skill_chars)
        if not loaded.get("ok"):
            return loaded
        root = _root(skill_id)
        form = _extract_qsh_form(
            (root / "SKILL.md").read_text(encoding="utf-8")
        ) or {}
        template = form.get("prompt_template") or ""
        ctx: Dict[str, Any] = dict(fields or {})
        if task:
            ctx["task"] = task
        # 兼容常见字段别名
        if "date" in ctx and not ctx.get("trade_date"):
            pass
        if "symbol" in ctx and not ctx.get("ts_code"):
            pass
        rendered = _render_mustache_lite(template, ctx) if template else ""

        parts: List[str] = []
        if include_loader:
            loader = root / "agents" / "portable-loader.md"
            if loader.exists():
                parts.append(loader.read_text(encoding="utf-8"))
        if include_skill:
            parts.append("# SKILL.md\n\n" + loaded["content"])
        if rendered:
            parts.append("# 本次任务\n\n" + rendered)

        prompt = "\n\n---\n\n".join(parts)
        return {
            "ok": True,
            "skill_id": skill_id,
            "name": loaded.get("name"),
            "fields_used": ctx,
            "task_prompt": rendered,
            "full_prompt": prompt,
            "requires": loaded.get("requires"),
            "hint": (
                "将 full_prompt 作为系统/用户指令执行；拉数优先 pandadata-api，"
                "本仓库也可用 datahubco_data__* / sql_toolkit__* 替代并标注来源。"
            ),
        }

    def run_script(
        self,
        skill_id: str,
        script: str,
        args: Optional[List[str]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        root = _root(skill_id)
        try:
            script_path = _safe_relpath(root, script)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if not script_path.is_file():
            return {"ok": False, "error": f"脚本不存在: {script}"}
        cmd = [sys.executable, str(script_path), *(args or [])]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=max(5, min(int(timeout or 60), 300)),
                encoding="utf-8",
                errors="replace",
            )
            return {
                "ok": proc.returncode == 0,
                "skill_id": skill_id,
                "script": script.replace("\\", "/"),
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-8000:],
                "stderr": (proc.stderr or "")[-4000:],
                "cmd": cmd,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "脚本超时", "script": script}
        except Exception as e:
            return {"ok": False, "error": str(e), "script": script}

    def validate_report(self, skill_id: str, report_path: str) -> Dict[str, Any]:
        """市场复盘专用：调用 scripts/validate_report.py"""
        if skill_id != "skill-market-daily-review":
            return {"ok": False, "error": "仅 skill-market-daily-review 支持 validate-report"}
        root = _root(skill_id)
        # 允许绝对路径或相对项目/技能目录
        path = Path(report_path)
        if not path.is_file():
            cand = root / report_path
            if cand.is_file():
                path = cand
            else:
                cand2 = Path.cwd() / report_path
                if cand2.is_file():
                    path = cand2
                else:
                    return {"ok": False, "error": f"报告文件不存在: {report_path}"}
        return self.run_script(
            skill_id,
            "scripts/validate_report.py",
            args=[str(path.resolve())],
        )

    # async
    async def load_skill_async(self, **kw) -> Dict[str, Any]:
        return await asyncio.to_thread(self.load_skill, **kw)

    async def list_resources_async(self, **kw) -> Dict[str, Any]:
        return await asyncio.to_thread(self.list_resources, **kw)

    async def read_resource_async(self, **kw) -> Dict[str, Any]:
        return await asyncio.to_thread(self.read_resource, **kw)

    async def build_prompt_async(self, **kw) -> Dict[str, Any]:
        return await asyncio.to_thread(self.build_prompt, **kw)

    async def run_script_async(self, **kw) -> Dict[str, Any]:
        return await asyncio.to_thread(self.run_script, **kw)

    async def validate_report_async(self, **kw) -> Dict[str, Any]:
        return await asyncio.to_thread(self.validate_report, **kw)


quantskill_runtime = QuantSkillRuntime()


def _common_tools(extra: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    tools = [
        {
            "id": "load-skill",
            "name": "📖 加载技能说明",
            "desc": "读取该技能包 SKILL.md（工作流、约束、接口映射），开始任务前应先调用",
            "params": [],
            "returns": "content / form_fields / requires",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    "max_chars": {
                        "type": "integer",
                        "description": "返回正文最大字符，默认 20000",
                        "default": 20000,
                    }
                },
                "required": [],
            },
        },
        {
            "id": "list-resources",
            "name": "📂 列出资源文件",
            "desc": "列出 references/ scripts/ agents/ 及顶层文档",
            "params": [],
            "returns": "resources",
            "openai_parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "id": "read-resource",
            "name": "📄 读取资源",
            "desc": "读取技能包内相对路径文件，如 references/report-template.md",
            "params": [
                {"name": "path", "label": "相对路径", "type": "text", "required": True, "placeholder": "references/..."},
            ],
            "returns": "content",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对技能根目录的路径"},
                    "max_chars": {"type": "integer", "description": "最大字符，默认 30000", "default": 30000},
                },
                "required": ["path"],
            },
        },
        {
            "id": "build-prompt",
            "name": "🧩 构建任务提示",
            "desc": "按 SKILL.md 内 qsh-form 模板拼装本次任务提示（含 portable-loader）",
            "params": [
                {"name": "task", "label": "补充要求", "type": "textarea", "required": False},
            ],
            "returns": "full_prompt / task_prompt",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "可选补充说明"},
                    "fields": {
                        "type": "object",
                        "description": (
                            "表单字段：复盘可用 {\"date\":\"20260918\",\"focus\":\"北向\"}；"
                            "个股档案 {\"symbol\":\"600519.SH\"}；"
                            "期货 {\"variety\":\"RB\",\"focus\":\"broker\"}"
                        ),
                    },
                    "include_skill": {"type": "boolean", "default": True},
                    "include_loader": {"type": "boolean", "default": True},
                },
                "required": [],
            },
        },
    ]
    if extra:
        tools.extend(extra)
    return tools


def build_quantskill_registry_entry(skill_id: str) -> Dict[str, Any]:
    meta = PACKAGE_META[skill_id]
    extra: List[Dict[str, Any]] = []
    if skill_id == "skill-market-daily-review":
        extra.append({
            "id": "validate-report",
            "name": "✅ 校验复盘报告",
            "desc": "运行 scripts/validate_report.py，检查章节完整性、来源标注与免责声明",
            "params": [
                {"name": "report_path", "label": "报告路径", "type": "text", "required": True, "placeholder": "reports/daily/20260918.md"},
            ],
            "returns": "stdout / returncode",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    "report_path": {"type": "string", "description": "复盘报告 md 路径"},
                },
                "required": ["report_path"],
            },
        })
    return {
        "id": skill_id,
        "name": meta["name"],
        "icon": meta["icon"],
        "category": meta["category"],
        "desc": meta["desc"],
        "enabled": True,
        "tools": _common_tools(extra),
        "instructions": f"""
**{meta['name']}**（QuantSkills，目录 `tools/{PACKAGE_DIRS[skill_id]}`）

1. 先 `load-skill` 阅读工作流与约束。
2. 需要模板/接口映射时 `list-resources` → `read-resource`。
3. 用 `build-prompt` 生成带字段的任务提示，再按提示收集数据并写中文报告。
4. 依赖上游 `skill-pandadata-api`；本平台可改用 datahubco-data / sql-toolkit，但必须标注数据来源，禁止编造数字。
5. 上游: {meta['upstream']}
""",
    }


def build_all_quantskill_entries() -> Dict[str, Dict[str, Any]]:
    return {sid: build_quantskill_registry_entry(sid) for sid in PACKAGE_DIRS}


async def execute_quantskill(skill_id: str, tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if skill_id not in PACKAGE_DIRS:
        return {"ok": False, "error": f"未知 QuantSkill: {skill_id}"}
    rt = quantskill_runtime
    payload = payload or {}

    if tool_id == "load-skill":
        return await rt.load_skill_async(
            skill_id=skill_id,
            max_chars=int(payload.get("max_chars") or 20000),
        )
    if tool_id == "list-resources":
        return await rt.list_resources_async(skill_id=skill_id)
    if tool_id == "read-resource":
        path = payload.get("path") or ""
        if not path:
            return {"ok": False, "error": "缺少 path"}
        return await rt.read_resource_async(
            skill_id=skill_id,
            path=str(path),
            max_chars=int(payload.get("max_chars") or 30000),
        )
    if tool_id == "build-prompt":
        fields = payload.get("fields")
        if isinstance(fields, str):
            try:
                fields = json.loads(fields) if fields.strip() else {}
            except Exception:
                return {"ok": False, "error": "fields 不是合法 JSON 对象"}
        # 允许扁平字段直接传入
        if not fields:
            fields = {
                k: v for k, v in payload.items()
                if k not in ("task", "include_skill", "include_loader", "fields")
                and v not in (None, "")
            }
        include_skill = payload.get("include_skill")
        if include_skill is None:
            include_skill = True
        include_loader = payload.get("include_loader")
        if include_loader is None:
            include_loader = True
        return await rt.build_prompt_async(
            skill_id=skill_id,
            task=payload.get("task"),
            fields=fields or {},
            include_skill=bool(include_skill),
            include_loader=bool(include_loader),
        )
    if tool_id == "validate-report":
        report_path = payload.get("report_path") or payload.get("path") or ""
        if not report_path:
            return {"ok": False, "error": "缺少 report_path"}
        return await rt.validate_report_async(skill_id=skill_id, report_path=str(report_path))
    if tool_id == "run-script":
        script = payload.get("script") or ""
        if not script:
            return {"ok": False, "error": "缺少 script"}
        args = payload.get("args") or []
        if isinstance(args, str):
            args = [a for a in args.split(" ") if a]
        return await rt.run_script_async(
            skill_id=skill_id,
            script=str(script),
            args=list(args),
            timeout=int(payload.get("timeout") or 60),
        )
    return {"ok": False, "error": f"未知工具: {tool_id}"}
