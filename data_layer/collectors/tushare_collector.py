"""
Tushare 数据收集器
从 https://tushare.pro 获取国内股票、期货、基本面数据
"""
import tushare as ts
import pandas as pd
from datetime import datetime
import logging
from typing import Optional


class TushareCollector:
    """Tushare Pro 数据收集器"""
    
    def __init__(self, token: str = None):
        import os
        from dotenv import load_dotenv
        load_dotenv()
        self.token = token or os.getenv("TUSHARE_TOKEN", "your_tushare_token_here")
        self.logger = logging.getLogger("collector.tushare")
        
        if self.token == "your_tushare_token_here":
            self.logger.warning("⚠️ TUSHARE_TOKEN 未配置! 请在 .env 中设置真实 Token (https://tushare.pro)")
            self.pro = None
        else:
            self.pro = ts.pro_api(self.token)
            self.logger.info("TushareCollector 初始化完成 (Token 已配置)")
        
        self.logger.info(f"TushareCollector 初始化完成 (Token: {'已配置' if self.token != 'your_tushare_token_here' else '未配置 - 将使用AkShare后备'})")
    
    def get_daily(self, symbol: str, start_date: str = None, end_date: str = None) -> Optional[pd.DataFrame]:
        """获取日线数据 - 支持10年历史，如果Token未配置则返回None（使用AkShare后备）"""
        if not self.pro:
            self.logger.info(f"Tushare Token未配置，跳过 {symbol} Tushare数据获取，使用AkShare")
            return None
            
        try:
            if not start_date:
                start_date = (datetime.now() - pd.Timedelta(days=365*10 + 100)).strftime("%Y%m%d")  # 10年历史
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            
            df = self.pro.daily(
                ts_code=symbol if '.' in symbol else f"{symbol}.SZ",
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
            )
            
            if not df.empty:
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                df = df.sort_values('trade_date')
                self.logger.info(f"Tushare 获取 {symbol} {len(df)} 条日线数据 (10年范围)")
                return df
            return None
        except Exception as e:
            self.logger.error(f"Tushare 获取 {symbol} 数据失败: {e}。建议配置TUSHARE_TOKEN或使用AkShare。")
            return None
    
    def get_fundamental(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取基本面数据"""
        try:
            df = self.pro.fina_indicator(ts_code=symbol if '.' in symbol else f"{symbol}.SZ")
            self.logger.info(f"Tushare 获取 {symbol} 基本面数据成功")
            return df
        except Exception as e:
            self.logger.error(f"Tushare 基本面获取失败: {e}")
            return None
