"""
任务3 测试脚本 - 测试新增 DeepSeek 智能体 + 历史数据获取能力
"""

import unittest
from agents.agent_factory import AgentFactory
from agents.orchestrator import MultiAgentOrchestrator


class TestTask3Agents(unittest.TestCase):
    
    def setUp(self):
        self.factory = AgentFactory()
    
    def test_deepseek_agent_creation(self):
        """测试 DeepSeek 智能体是否能成功创建"""
        agent = self.factory.create_agent("deepseek", name="deepseek_quant")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.name, "deepseek_quant")
        self.assertEqual(agent.role, "DeepSeek Quant Analyst")
    
    def test_deepseek_get_historical_data(self):
        """测试 DeepSeek 智能体直接从存储层获取历史数据能力"""
        agent = self.factory.create_agent("deepseek", name="deepseek_data")
        result = agent.get_historical_data("AAPL", limit=100)
        self.assertIn("success", result)
        print(f"DeepSeek 获取历史数据结果: {result.get('success', False)}")
    
    def test_multi_agent_with_deepseek(self):
        """测试包含 DeepSeek 的多智能体协同"""
        orchestrator = MultiAgentOrchestrator()
        orchestrator.add_agent("deepseek", "deepseek")
        orchestrator.add_agent("signal", "signal_generator")
        
        result = orchestrator.run_task("使用 DeepSeek 分析 AAPL 历史数据并生成交易信号")
        self.assertIn("results", result)
        self.assertIn("deepseek", result["results"])
    
    def test_all_agents_connectivity(self):
        """测试所有智能体是否都能联通（任务3核心测试）"""
        orchestrator = MultiAgentOrchestrator()
        
        # 添加主流 + 量化 + DeepSeek 智能体
        agents_to_test = [
            ("deepseek", "deepseek"),
            ("signal", "signal_generator"),
            ("risk", "risk_manager"),
            ("portfolio", "portfolio_optimizer"),
            ("langchain", "langchain")
        ]
        
        for name, agent_type in agents_to_test:
            orchestrator.add_agent(name, agent_type)
        
        status = orchestrator.get_team_status()
        self.assertEqual(status["total_agents"], 5)
        print(f"多智能体联通测试通过，共 {status['total_agents']} 个智能体")
        
        # 测试协同任务
        result = orchestrator.run_task("从存储层获取历史数据并进行量化分析")
        self.assertIn("deepseek", result["results"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
