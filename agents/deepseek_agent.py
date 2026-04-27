"""
DeepSeek 专用智能体
支持调用 DeepSeek 大模型进行量化分析、代码生成、策略优化等
"""

from .base_agent import BaseAgent
from typing import Dict, Any
import logging
import json
import requests
from datetime import datetime


class DeepSeekAgent(BaseAgent):
    """DeepSeek 智能体 - 专注于量化分析和代码生成"""
    
    def __init__(self, name: str = "deepseek", api_key: str = None, model: str = "deepseek-r1"):
        super().__init__(
            name=name,
            role="DeepSeek Quant Analyst",
            description="使用 DeepSeek 大模型进行深度量化分析、策略生成、代码审查和市场预测"
        )
        import os
        from dotenv import load_dotenv
        load_dotenv()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "ollama")
        self.model = model
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "http://localhost:11434/api")
    
    def run(self, task: str, context: Dict = None) -> Dict:
        """使用 DeepSeek 模型执行量化相关任务
        如果任务涉及历史数据，会自动调用 get_historical_data 从存储层获取
        """
        self.logger.info(f"DeepSeekAgent 执行任务: {task[:60]}...")
        
        try:
            # 如果任务涉及历史数据，自动获取
            if any(k in task.lower() for k in ["历史", "行情", "数据", "回测", "k线"]):
                symbol = context.get("symbol", "AAPL") if context else "AAPL"
                data = self.get_historical_data(symbol, limit=2000)
                if context is None:
                    context = {}
                context["historical_data"] = data
                self.logger.info(f"已自动从存储层获取 {symbol} 历史数据")

            # 构造提示词
            prompt = f"""你是一个专业的量化交易专家。
当前任务: {task}

上下文信息: {json.dumps(context, ensure_ascii=False, indent=2) if context else '无'}

请提供专业、详细、可执行的量化分析、交易信号或代码方案。"""

            # 调用本地 Ollama 或 DeepSeek API
            response = self._call_model(prompt)
            
            result = {
                "agent": self.name,
                "model": self.model,
                "task": task,
                "response": response,
                "context_used": bool(context),
                "success": True
            }
            
            self.update_metrics(True, 3.2)
            return result
            
        except Exception as e:
            self.logger.error(f"DeepSeekAgent 执行失败: {e}")
            self.update_metrics(False, 6.5)
            return {
                "agent": self.name,
                "error": str(e),
                "success": False
            }
    
    def _call_model(self, prompt: str) -> str:
        """调用 DeepSeek / Ollama 模型 - 增加超时处理和重试机制"""
        import time
        for attempt in range(3):  # 最多重试3次
            try:
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.3
                }

                # 增加超时时间到60秒，并添加更详细的错误处理
                response = requests.post(
                    f"{self.base_url}/generate",
                    json=payload,
                    timeout=60,  # 从30秒增加到60秒解决请求超时问题
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "DeepSeek 未返回有效内容")
                else:
                    if attempt < 2:
                        time.sleep(1)  # 重试前等待1秒
                        continue
                    return f"API 调用失败: {response.status_code}"

            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(2 ** attempt)  # 指数退避
                    continue
                return f"请求超时(60s)，请检查Ollama服务是否在 http://localhost:11434 运行"
            except requests.exceptions.ConnectionError:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return f"连接失败，请启动Ollama服务或检查网络: {self.base_url}"
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                return f"调用 DeepSeek 模型失败: {str(e)}"
        return "所有重试均失败，请检查后端服务状态"
    
    def get_historical_data(self, symbol: str, start: datetime = None, end: datetime = None, limit: int = 1000) -> Dict:
        """直接从存储层获取历史交易数据
        这是 DeepSeekAgent 的核心能力：智能调用 storage_manager 从 InfluxDB 或 TimescaleDB 获取数据
        """
        from data_layer.db_manager import storage_manager
        try:
            df = storage_manager.query_historical_data(
                symbol=symbol,
                start=start,
                end=end,
                limit=limit
            )
            data_count = len(df) if df is not None and not df.empty else 0
            sample = df.head(5).to_dict('records') if data_count > 0 else None
            
            self.logger.info(f"成功获取 {symbol} 历史数据 {data_count} 条")
            return {
                "symbol": symbol,
                "data_count": data_count,
                "sample": sample,
                "dataframe_shape": df.shape if df is not None else (0, 0),
                "success": True,
                "source": "InfluxDB/TimescaleDB"
            }
        except Exception as e:
            self.logger.error(f"获取历史数据失败: {e}")
            return {"error": str(e), "success": False, "symbol": symbol}
