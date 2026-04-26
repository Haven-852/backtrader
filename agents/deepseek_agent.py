"""
DeepSeek 专用智能体
支持调用 DeepSeek 大模型进行量化分析、代码生成、策略优化等
"""

from .base_agent import BaseAgent
from typing import Dict, Any
import logging
import json
import requests


class DeepSeekAgent(BaseAgent):
    """DeepSeek 智能体 - 专注于量化分析和代码生成"""
    
    def __init__(self, name: str = "deepseek", api_key: str = None, model: str = "deepseek-r1"):
        super().__init__(
            name=name,
            role="DeepSeek Quant Analyst",
            description="使用 DeepSeek 大模型进行深度量化分析、策略生成、代码审查和市场预测"
        )
        self.api_key = api_key or "ollama"  # 默认使用本地 Ollama 或配置的 Key
        self.model = model
        self.base_url = "http://localhost:11434/api"  # 默认使用 Ollama
    
    def run(self, task: str, context: Dict = None) -> Dict:
        """使用 DeepSeek 模型执行量化相关任务"""
        self.logger.info(f"DeepSeekAgent 执行任务: {task[:60]}...")
        
        try:
            # 构造提示词
            prompt = f"""你是一个专业的量化交易专家。
当前任务: {task}

上下文信息: {json.dumps(context, ensure_ascii=False) if context else '无'}

请提供专业、详细、可执行的量化分析或代码方案。"""

            # 调用本地 Ollama 或 DeepSeek API
            response = self._call_model(prompt)
            
            result = {
                "agent": self.name,
                "model": self.model,
                "task": task,
                "response": response,
                "success": True
            }
            
            self.update_metrics(True, 2.5)
            return result
            
        except Exception as e:
            self.logger.error(f"DeepSeekAgent 执行失败: {e}")
            self.update_metrics(False, 5.0)
            return {
                "agent": self.name,
                "error": str(e),
                "success": False
            }
    
    def _call_model(self, prompt: str) -> str:
        """调用 DeepSeek / Ollama 模型"""
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "temperature": 0.3
            }
            
            response = requests.post(
                f"{self.base_url}/generate",
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("response", "DeepSeek 未返回有效内容")
            else:
                return f"API 调用失败: {response.status_code}"
                
        except Exception as e:
            return f"调用 DeepSeek 模型失败: {str(e)}"
    
    def get_historical_data(self, symbol: str, limit: int = 1000) -> Dict:
        """直接从存储层获取历史交易数据"""
        from data_layer.db_manager import storage_manager
        try:
            # 从存储层查询历史数据
            df = storage_manager.query_historical_data(
                symbol=symbol, 
                start=None, 
                limit=limit
            )
            return {
                "symbol": symbol,
                "data_count": len(df) if df is not None else 0,
                "data": df.head(5).to_dict() if df is not None else None,
                "success": True
            }
        except Exception as e:
            return {"error": str(e), "success": False}
