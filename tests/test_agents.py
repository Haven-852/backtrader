"""
多智能体系统单元测试
测试主流智能体和量化智能体的创建、执行、协同功能
"""

import unittest
from agents.agent_factory import AgentFactory
from agents.orchestrator import MultiAgentOrchestrator


class TestAgents(unittest.TestCase):
    
    def setUp(self):
        self.factory = AgentFactory()
    
    def test_factory_creation(self):
        """测试工厂能否创建所有智能体"""
        agent_types = [
            "signal_generator", "risk_manager", "portfolio_optimizer",
            "langchain", "crewai", "swarm"
        ]
        
        for agent_type in agent_types:
            agent = self.factory.create_agent(agent_type, name=f"test_{agent_type}")
            self.assertIsNotNone(agent)
            self.assertEqual(agent.name, f"test_{agent_type}")
    
    def test_quant_team_creation(self):
        """测试量化交易团队快速创建"""
        orchestrator = MultiAgentOrchestrator()
        team = {
            "signal": orchestrator.add_agent("signal_generator", "signal_generator"),
            "risk": orchestrator.add_agent("risk_manager", "risk_manager"),
        }
        self.assertEqual(len(orchestrator.agents), 2)
    
    def test_orchestrator_run(self):
        """测试编排器协同执行"""
        orchestrator = MultiAgentOrchestrator()
        orchestrator.add_agent("signal_generator", "signal_generator")
        
        result = orchestrator.run_task("生成 AAPL 的交易信号")
        self.assertIn("results", result)
        self.assertIn("summary", result)
    
    def test_list_agents(self):
        """测试列出所有可用智能体"""
        agents = self.factory.list_agents()
        self.assertIn("mainstream", agents)
        self.assertIn("quant_excellent", agents)
        self.assertGreater(len(agents["all"]), 8)


if __name__ == '__main__':
    unittest.main()
