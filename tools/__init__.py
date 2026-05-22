"""
Skill Registry - 技能注册中心
所有 skill 在此注册后，前端通过 /skills API 自动发现
"""

from typing import Dict, List, Any

SKILL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "sql-toolkit": {
        "id": "sql-toolkit",
        "name": "SQL 数据库工具箱",
        "icon": "database",
        "category": "数据分析",
        "desc": "基于 LangChain SQLDatabaseToolkit，提供 PostgreSQL 关系型数据库的智能查询能力。支持查看表结构、列出所有表、SQL 校验和自然语言转 SQL 执行。",
        "enabled": True,
        "tools": [
            {
                "id": "list-tables",
                "name": "📋 列出所有表",
                "desc": "获取数据库中所有用户表的名称列表",
                "params": [],
                "returns": "表名列表（逗号分隔）",
            },
            {
                "id": "info-table",
                "name": "📊 查看表信息",
                "desc": "获取指定表的完整 schema：列名、类型、是否可空、主键",
                "params": [
                    {"name": "table_names", "label": "表名", "type": "text", "required": True,
                     "placeholder": "逗号分隔的表名，如：stock_daily, ref_trade_cal"}
                ],
                "returns": "各表的 CREATE TABLE 语句",
            },
            {
                "id": "query-check",
                "name": "✅ SQL 校验",
                "desc": "检查 SQL 语句是否语法正确，并给出修正建议",
                "params": [
                    {"name": "query", "label": "SQL 语句", "type": "textarea", "required": True,
                     "placeholder": "SELECT * FROM stock_daily LIMIT 5"}
                ],
                "returns": "校验结果：是否合法 + 修正建议",
            },
            {
                "id": "query-exec",
                "name": "⚡ 执行 SQL",
                "desc": "在 PostgreSQL 数据库中执行 SELECT 查询并返回结果",
                "params": [
                    {"name": "query", "label": "SQL 语句", "type": "textarea", "required": True,
                     "placeholder": "SELECT code, trade_date, open, close FROM stock_daily WHERE code='000001.SZ' ORDER BY trade_date DESC LIMIT 10"}
                ],
                "returns": "查询结果（表格形式）",
            },
            {
                "id": "query-nl",
                "name": "💬 自然语言查询",
                "desc": "输入自然语言问题，自动生成 SQL 并执行",
                "params": [
                    {"name": "question", "label": "你的问题", "type": "textarea", "required": True,
                     "placeholder": "比如：最近5天成交量最高的10只股票是什么？"}
                ],
                "returns": "SQL + 查询结果",
            },
        ],
        "instructions": """
**SQL 数据库工具箱** 提供对 PostgreSQL 的完整交互能力：

- **列出所有表** → 看看有什么数据可用
- **查看表信息** → 了解表的字段和结构
- **SQL 校验** → 写 SQL 前先检查语法
- **执行 SQL** → 直接执行 SELECT 查询
- **自然语言查询** → 用中文描述需求，自动转 SQL

⚠️ 安全限制：仅允许 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP 等写操作。
""",
    }
}


def get_skill(skill_id: str) -> Dict[str, Any] | None:
    """获取单个 skill 配置（不含实现细节）"""
    return SKILL_REGISTRY.get(skill_id)


def list_skills(category: str = None) -> List[Dict[str, Any]]:
    """列出所有已注册的 skill"""
    skills = []
    for skill in SKILL_REGISTRY.values():
        if not skill.get("enabled", True):
            continue
        if category and skill.get("category") != category:
            continue
        skills.append({
            "id": skill["id"],
            "name": skill["name"],
            "icon": skill["icon"],
            "category": skill["category"],
            "desc": skill["desc"],
            "tools_count": len(skill.get("tools", [])),
            "tools": skill.get("tools", []),
        })
    return skills
