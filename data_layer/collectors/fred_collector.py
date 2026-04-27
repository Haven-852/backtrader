"""
FRED 宏观数据收集器
"""
import pandas as pd
from datetime import datetime
import logging
from typing import Optional
from fredapi import Fred


class FredCollector:
    """FRED 宏观数据收集器"""
    
    def __init__(self, api_key: str = None):
        import os
        from dotenv import load_dotenv
        load_dotenv()
        self.api_key = api_key or os.getenv("FRED_API_KEY", "your_fred_api_key")
        self.fred = Fred(api_key=self.api_key) if self.api_key != "your_fred_api_key" else None
        self.logger = logging.getLogger("collector.fred")
        self.logger.info(f"FredCollector 初始化完成 (API Key: {'已配置' if self.api_key != 'your_fred_api_key' else '请配置'})")
    
    def get_key_indicators(self) -> Optional[pd.DataFrame]:
        """获取关键宏观指标"""
        try:
            if not self.fred:
                # 模拟数据
                dates = pd.date_range(end=datetime.now(), periods=100, freq='M')
                df = pd.DataFrame({
                    'date': dates,
                    'gdp_growth': pd.np.random.uniform(1, 5, 100),
                    'cpi': pd.np.random.uniform(2, 6, 100),
                    'interest_rate': pd.np.random.uniform(1, 5, 100)
                })
                self.logger.info("FRED 使用模拟宏观数据")
                return df
            
            # 真实 FRED 数据
            gdp = self.fred.get_series('GDP')
            cpi = self.fred.get_series('CPIAUCSL')
            rate = self.fred.get_series('FEDFUNDS')
            
            df = pd.DataFrame({
                'gdp': gdp,
                'cpi': cpi,
                'fed_rate': rate
            }).dropna()
            
            self.logger.info(f"FRED 获取宏观数据 {len(df)} 条")
            return df
        except Exception as e:
            self.logger.error(f"FRED 数据获取失败: {e}")
            return None
