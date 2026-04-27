"""
数据收集管理器 - 支持工作日定时导入
按照 architecture/02-agent-data-access.md 实现数据分层存储：
- 10年A股完整历史数据（日线）-> TimescaleDB/PostgreSQL
- 工作日分钟级实时数据 -> InfluxDB (高频)
"""
import logging
from datetime import datetime, timedelta
import pandas as pd
from typing import Optional, List, Dict
import schedule
import time
import threading
import os

from .tushare_collector import TushareCollector
from .akshare_collector import AkshareCollector
from .fred_collector import FredCollector
from ..db_manager import storage_manager


class DataCollectorManager:
    """数据收集统一管理器，支持工作日定时运行"""
    
    def __init__(self):
        self.tushare = TushareCollector()
        self.akshare = AkshareCollector()
        self.fred = FredCollector()
        self.logger = logging.getLogger("collector.manager")
        self.is_running = False
        self.logger.info("DataCollectorManager 初始化完成")
    
    def import_stock_data(self, symbol: str, start_date: str = None, end_date: str = None, source: str = "tushare") -> Optional[pd.DataFrame]:
        """导入股票量价数据"""
        if source == "tushare":
            df = self.tushare.get_daily(symbol, start_date, end_date)
        else:
            df = self.akshare.get_daily(symbol, start_date, end_date)
        
        if df is not None and not df.empty:
            storage_manager.save_market_data(symbol, df, timeframe="daily")
            self.logger.info(f"成功导入 {symbol} {len(df)} 条数据到存储层")
            return df
        return None
    
    def import_fundamental_data(self, symbol: str):
        """导入基本面数据"""
        df = self.tushare.get_fundamental(symbol)
        if df is not None:
            # 保存到 PostgreSQL
            self.logger.info(f"成功导入 {symbol} 基本面数据")
            return df
        return None

    def import_macro_data(self):
        """导入宏观数据"""
        df = self.fred.get_key_indicators()
        if df is not None:
            self.logger.info("宏观数据导入完成")
            return df
        return None

    def load_10years_a_shares(self, max_stocks: int = 50, use_akshare: bool = True) -> Dict:
        """加载最近10年A股股票完整信息（核心功能）
        按照 architecture/02-agent-data-access.md 写入：
        - 日线数据保存到 TimescaleDB (通过 storage_manager)
        - 返回处理统计信息
        """
        self.logger.info(f"开始加载最近10年A股数据，最大处理{max_stocks}只股票...")
        
        if use_akshare:
            stocks = self.akshare.get_all_a_stocks()[:max_stocks]
            collector = self.akshare
            source = "akshare"
        else:
            stocks = self.akshare.get_all_a_stocks()[:max_stocks]  # 仍然用akshare获取列表
            collector = self.tushare
            source = "tushare"
        
        stats = {
            "total_stocks": len(stocks),
            "successful": 0,
            "failed": 0,
            "total_records": 0,
            "start_time": datetime.now()
        }
        
        for i, stock in enumerate(stocks):
            symbol = stock['symbol']
            name = stock.get('name', 'Unknown')
            
            self.logger.info(f"[{i+1}/{len(stocks)}] 处理 {symbol} {name}")
            
            try:
                # 获取10年日线数据
                df = collector.get_daily(symbol)
                if df is not None and not df.empty:
                    # 按照架构写入存储层 (TimescaleDB优先用于日线)
                    success = storage_manager.save_market_data(symbol, df, timeframe="daily")
                    if success:
                        stats["successful"] += 1
                        stats["total_records"] += len(df)
                        self.logger.info(f"✅ {symbol} {len(df)}条10年数据已写入存储层")
                    else:
                        stats["failed"] += 1
                else:
                    stats["failed"] += 1
                    self.logger.warning(f"⚠️ {symbol} 无数据返回")
            except Exception as e:
                stats["failed"] += 1
                self.logger.error(f"❌ 处理 {symbol} 失败: {e}")
            
            # 避免请求过快
            if i % 5 == 0 and i > 0:
                time.sleep(2)
        
        stats["end_time"] = datetime.now()
        stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()
        
        self.logger.info(f"10年A股数据加载完成! 成功: {stats['successful']}/{stats['total_stocks']}, "
                        f"总记录: {stats['total_records']}, 耗时: {stats['duration']:.1f}秒")
        return stats

    def load_minute_data_for_trading_days(self, symbol: str = None, days: int = 5) -> Dict:
        """实现每个工作日的分钟实时数据写入 (高频数据 -> InfluxDB)
        按照 architecture/02-agent-data-access.md 设计
        """
        self.logger.info(f"开始加载工作日分钟级数据 (最近{days}个交易日)...")
        
        stats = {
            "symbols_processed": 0,
            "total_minute_records": 0,
            "successful": 0,
            "failed": 0,
            "start_time": datetime.now()
        }
        
        symbols = [symbol] if symbol else ["000001", "000300", "600519", "601398"]
        
        for sym in symbols:
            try:
                # 获取分钟数据 (1分钟或5分钟)
                df_1m = self.akshare.get_minute(sym, period="1")
                if df_1m is not None and not df_1m.empty:
                    success = storage_manager.save_market_data(sym, df_1m, timeframe="1m")
                    if success:
                        stats["successful"] += 1
                        stats["total_minute_records"] += len(df_1m)
                        self.logger.info(f"✅ {sym} 分钟数据({len(df_1m)}条)已写入InfluxDB")
                    else:
                        stats["failed"] += 1
                else:
                    stats["failed"] += 1
            except Exception as e:
                stats["failed"] += 1
                self.logger.error(f"处理 {sym} 分钟数据失败: {e}")
            
            stats["symbols_processed"] += 1
        
        stats["end_time"] = datetime.now()
        stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()
        self.logger.info(f"分钟数据写入完成! 成功: {stats['successful']}, 总分钟记录: {stats['total_minute_records']}")
        return stats
    
    def run_daily_import(self):
        """工作日每日导入任务"""
        today = datetime.now()
        if today.weekday() >= 5:  # 周六周日跳过
            self.logger.info("周末跳过数据导入")
            return
        
        self.logger.info(f"开始工作日数据导入任务 - {today.date()}")
        
        # 导入主流指数和股票
        symbols = ["000001", "000300", "AAPL", "SPY"]
        for symbol in symbols:
            self.import_stock_data(symbol, start_date=(today - timedelta(days=30)).strftime("%Y%m%d"))
        
        self.import_macro_data()
        self.logger.info("每日数据导入任务完成")
    
    def start_scheduler(self):
        """启动定时任务（工作日 9:00 执行）"""
        if self.is_running:
            return
        
        schedule.every().day.at("09:00").do(self.run_daily_import)
        
        self.is_running = True
        self.logger.info("数据导入定时任务已启动（工作日 09:00 执行）")
        
        def run_scheduler():
            while self.is_running:
                schedule.run_pending()
                time.sleep(60)
        
        thread = threading.Thread(target=run_scheduler, daemon=True)
        thread.start()
    
    def stop(self):
        """停止定时任务"""
        self.is_running = False
        self.logger.info("数据导入定时任务已停止")
