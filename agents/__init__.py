"""
Backtrader 多智能体系统 (Multi-Agent System)

支持主流智能体 + 量化交易优异智能体
可动态创建、连接、协作多个智能体完成量化交易任务

主要功能：
- AgentFactory：工厂模式创建不同类型智能体
- MultiAgentOrchestrator：协调多个智能体协作
- 内置主流 Agent + 量化专用 Agent
"""

from .agent_factory import AgentFactory
from .orchestrator import MultiAgentOrchestrator
from .base_agent import BaseAgent

__all__ = [
    'AgentFactory',
    'MultiAgentOrchestrator',
    'BaseAgent',
    'create_quant_team'
]

def create_quant_team():
    """快速创建一套量化交易智能体团队"""
    factory = AgentFactory()
    team = {
        "signal_generator": factory.create_agent("signal_generator"),
        "risk_manager": factory.create_agent("risk_manager"),
        "portfolio_optimizer": factory.create_agent("portfolio_optimizer"),
        "backtester": factory.create_agent("backtester"),
        "executor": factory.create_agent("executor"),
    }
    orchestrator = MultiAgentOrchestrator(team)
    return orchestrator
