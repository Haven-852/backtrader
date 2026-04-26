"""
多智能体编排器 - 支持多个智能体协同工作
"""

from typing import Dict, List, Any
from .base_agent import BaseAgent
from .agent_factory import AgentFactory
import logging


class MultiAgentOrchestrator:
    """多智能体协同编排器"""
    
    def __init__(self, agents: Dict[str, BaseAgent] = None):
        self.agents = agents or {}
        self.factory = AgentFactory()
        self.logger = logging.getLogger("orchestrator")
        self.logger.info(f"多智能体编排器初始化完成，当前智能体数量: {len(self.agents)}")
    
    def add_agent(self, name: str, agent_type: str, **kwargs):
        """动态添加智能体"""
        agent = self.factory.create_agent(agent_type, name=name, **kwargs)
        self.agents[name] = agent
        self.logger.info(f"添加智能体: {name} ({agent_type})")
        return agent
    
    def run_task(self, task: str, assigned_agents: List[str] = None) -> Dict:
        """分配任务给多个智能体协同完成"""
        if not assigned_agents:
            assigned_agents = list(self.agents.keys())
        
        results = {}
        self.logger.info(f"开始执行任务: {task}，参与智能体: {assigned_agents}")
        
        for agent_name in assigned_agents:
            if agent_name in self.agents:
                agent = self.agents[agent_name]
                try:
                    result = agent.run(task, context=results)
                    results[agent_name] = result
                    self.logger.info(f"智能体 {agent_name} 完成任务")
                except Exception as e:
                    results[agent_name] = {"error": str(e)}
                    self.logger.error(f"智能体 {agent_name} 执行失败: {e}")
        
        return {
            "task": task,
            "results": results,
            "summary": f"多智能体协同完成任务，共 {len(results)} 个智能体参与"
        }
    
    def get_team_status(self) -> Dict:
        """获取整个团队状态"""
        return {
            "total_agents": len(self.agents),
            "agents": {name: agent.get_status() for name, agent in self.agents.items()}
        }
