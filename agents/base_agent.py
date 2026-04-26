"""
多智能体系统 - 基础 Agent 类
所有智能体均继承此类
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime


class BaseAgent(ABC):
    """所有智能体的基类"""
    
    def __init__(self, name: str, role: str, description: str):
        self.name = name
        self.role = role
        self.description = description
        self.logger = logging.getLogger(f"agent.{name}")
        self.created_at = datetime.now()
        self.metrics = {
            "tasks_completed": 0,
            "success_rate": 0.0,
            "avg_response_time": 0.0
        }
        self.logger.info(f"Agent {name} ({role}) 初始化完成")
    
    @abstractmethod
    def run(self, task: str, context: Dict = None) -> Dict:
        """执行任务"""
        pass
    
    def get_status(self) -> Dict:
        """获取智能体状态"""
        return {
            "name": self.name,
            "role": self.role,
            "status": "active",
            "metrics": self.metrics,
            "created_at": self.created_at.isoformat()
        }
    
    def update_metrics(self, success: bool, response_time: float):
        """更新性能指标"""
        self.metrics["tasks_completed"] += 1
        if success:
            self.metrics["success_rate"] = (
                (self.metrics["success_rate"] * (self.metrics["tasks_completed"] - 1) + 1) 
                / self.metrics["tasks_completed"]
            )
        self.metrics["avg_response_time"] = (
            (self.metrics["avg_response_time"] * (self.metrics["tasks_completed"] - 1) + response_time) 
            / self.metrics["tasks_completed"]
        )
