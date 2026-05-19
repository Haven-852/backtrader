"""
Agent Service - 智能体编排服务层
负责：智能体注册、创建、编排、执行流水线

参考 Yuxi 项目 package/yuxi/services/agent_run_service.py
"""

import sys
import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


class AgentService:
    """智能体编排服务"""

    # 内置智能体定义
    BUILTIN_AGENTS = [
        {
            "id": "deepseek",
            "name": "DeepSeek Agent",
            "type": "llm",
            "category": "对话分析",
            "description": "基于 DeepSeek 大模型的深度量化分析、策略生成、代码审查",
            "status": "online",
            "features": ["市场分析", "策略生成", "代码审查", "历史数据查询"],
        },
        {
            "id": "signal_generator",
            "name": "Signal Generator",
            "type": "quant",
            "category": "量化信号",
            "description": "技术指标与交易信号生成，支持 MA/RSI/MACD/Bollinger/KDJ 等",
            "status": "online",
            "features": ["技术指标", "信号生成", "多周期分析"],
        },
        {
            "id": "risk_manager",
            "name": "Risk Manager",
            "type": "quant",
            "category": "风控管理",
            "description": "仓位控制、止损止盈、最大回撤监控、VaR 计算",
            "status": "online",
            "features": ["仓位管理", "止损止盈", "VaR", "回撤监控"],
        },
        {
            "id": "portfolio_optimizer",
            "name": "Portfolio Optimizer",
            "type": "quant",
            "category": "组合优化",
            "description": "Markowitz 均值方差优化、Black-Litterman、风险平价",
            "status": "online",
            "features": ["均值方差", "Black-Litterman", "风险平价", "再平衡"],
        },
        {
            "id": "backtester",
            "name": "Backtester",
            "type": "quant",
            "category": "回测引擎",
            "description": "Backtrader 策略回测，支持多品种多周期、参数优化",
            "status": "online",
            "features": ["策略回测", "参数优化", "绩效分析", "可视化"],
        },
        {
            "id": "executor",
            "name": "Executor",
            "type": "quant",
            "category": "执行引擎",
            "description": "交易执行与订单管理",
            "status": "online",
            "features": ["订单执行", "滑点控制", "成交分析"],
        },
        {
            "id": "quant_research",
            "name": "Quant Research",
            "type": "quant",
            "category": "量化研究",
            "description": "因子研究、统计套利、Alpha 挖掘",
            "status": "online",
            "features": ["因子分析", "统计套利", "Alpha 模型"],
        },
        {
            "id": "crewai",
            "name": "CrewAI Team",
            "type": "multi_agent",
            "category": "多智能体",
            "description": "研究-分析-执行多角色协作团队",
            "status": "online",
            "features": ["多角色协作", "任务分解", "流水线执行"],
        },
        {
            "id": "swarm",
            "name": "OpenAI Swarm",
            "type": "multi_agent",
            "category": "智能体群",
            "description": "轻量级多智能体协调，适合复杂量化任务分解",
            "status": "online",
            "features": ["任务分解", "并行执行", "结果聚合"],
        },
    ]

    def __init__(self):
        self._agent_factory = None

    @property
    def agent_factory(self):
        if self._agent_factory is None:
            try:
                from agents.agent_factory import AgentFactory
                self._agent_factory = AgentFactory()
            except Exception as e:
                logger.warning(f"无法加载 AgentFactory: {e}")
        return self._agent_factory

    def get_agents(self, include_status: bool = True) -> List[Dict]:
        """获取所有智能体列表"""
        agents = []
        for agent in self.BUILTIN_AGENTS:
            agent_info = dict(agent)
            if include_status and self.agent_factory:
                try:
                    agent_info["loaded"] = agent["id"] in self.agent_factory._registry
                except Exception:
                    agent_info["loaded"] = False
            agents.append(agent_info)

        return agents

    def get_agent(self, agent_id: str) -> Optional[Dict]:
        """获取指定智能体的详细信息"""
        for agent in self.BUILTIN_AGENTS:
            if agent["id"] == agent_id:
                agent_info = dict(agent)
                if self.agent_factory:
                    try:
                        agent_info["loaded"] = agent_id in self.agent_factory._registry
                        if agent_info["loaded"]:
                            inst = self.agent_factory.create_agent(agent_id)
                            agent_info["instance_status"] = inst.get_status()
                    except Exception as e:
                        agent_info["loaded"] = False
                        agent_info["load_error"] = str(e)
                return agent_info
        return None

    def get_agent_categories(self) -> Dict[str, List[Dict]]:
        """按类别分组获取智能体"""
        categories = {}
        for agent in self.BUILTIN_AGENTS:
            cat = agent["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({
                "id": agent["id"],
                "name": agent["name"],
                "status": agent["status"],
                "description": agent["description"],
            })
        return categories

    async def execute_agent(self, agent_id: str, task: str, context: Dict = None) -> Dict[str, Any]:
        """执行指定智能体"""
        if not self.agent_factory:
            return {
                "success": False,
                "error": "AgentFactory 未加载",
                "agent_id": agent_id,
            }

        try:
            agent = self.agent_factory.create_agent(agent_id)
            result = agent.run(task, context or {})
            return {
                "success": result.get("success", True),
                "agent_id": agent_id,
                "agent_name": agent.name,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            }
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
                "agent_id": agent_id,
                "available": list(self.agent_factory._registry.keys()) if self.agent_factory else [],
            }
        except Exception as e:
            logger.error(f"智能体执行失败 [{agent_id}]: {e}")
            return {
                "success": False,
                "error": str(e),
                "agent_id": agent_id,
            }


# 全局实例
agent_service = AgentService()
