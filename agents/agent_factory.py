"""
智能体工厂 - 支持动态创建主流和量化专用智能体
"""

from typing import Dict, Type
from .base_agent import BaseAgent
from .mainstream_agents import (
    LangChainAgent, AutoGPTAgent, BabyAGIAgent, 
    MetaGPTAgent, CrewAIAgent, SwarmAgent
)
from .quant_agents import (
    SignalGeneratorAgent, RiskManagerAgent, PortfolioOptimizerAgent,
    BacktesterAgent, ExecutorAgent, QuantResearchAgent
)
from .deepseek_agent import DeepSeekAgent


class AgentFactory:
    """智能体工厂类"""
    
    def __init__(self):
        self._registry: Dict[str, Type[BaseAgent]] = {}
        self._register_all_agents()
    
    def _register_all_agents(self):
        """注册所有支持的智能体"""
        # 主流智能体
        self.register("langchain", LangChainAgent)
        self.register("autogpt", AutoGPTAgent)
        self.register("babyagi", BabyAGIAgent)
        self.register("metagpt", MetaGPTAgent)
        self.register("crewai", CrewAIAgent)
        self.register("swarm", SwarmAgent)
        
        # 量化交易优异智能体
        self.register("signal_generator", SignalGeneratorAgent)
        self.register("risk_manager", RiskManagerAgent)
        self.register("portfolio_optimizer", PortfolioOptimizerAgent)
        self.register("backtester", BacktesterAgent)
        self.register("executor", ExecutorAgent)
        self.register("quant_research", QuantResearchAgent)
        self.register("deepseek", DeepSeekAgent)  # 新增 DeepSeek 智能体
    
    def register(self, agent_type: str, agent_class: Type[BaseAgent]):
        """注册智能体类型"""
        self._registry[agent_type.lower()] = agent_class
    
    def create_agent(self, agent_type: str, **kwargs) -> BaseAgent:
        """创建指定类型的智能体"""
        agent_type = agent_type.lower()
        if agent_type not in self._registry:
            raise ValueError(f"未知智能体类型: {agent_type}. 支持类型: {list(self._registry.keys())}")
        
        agent_class = self._registry[agent_type]
        return agent_class(**kwargs)
    
    def list_agents(self) -> Dict:
        """列出所有可用智能体"""
        return {
            "mainstream": ["langchain", "autogpt", "babyagi", "metagpt", "crewai", "swarm"],
            "quant_excellent": [
                "signal_generator", "risk_manager", "portfolio_optimizer", 
                "backtester", "executor", "quant_research"
            ],
            "all": list(self._registry.keys())
        }
