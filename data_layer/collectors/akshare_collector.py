"""
AkShare 数据收集器 (免费开源)
补充 Tushare 的免费数据源
支持最近10年A股完整数据 + 工作日分钟级实时数据
按照 architecture/02-agent-data-access.md 架构设计
- 日线数据 -> TimescaleDB
- 分钟级高频数据 -> InfluxDB
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import logging
from typing import Optional, List


class AkshareCollector:
    """AkShare 免费数据收集器
    按照 architecture/02-agent-data-access.md 实现：
    - 支持最近10年A股完整历史数据
    - 支持工作日分钟级实时数据采集
    - 数据写入规则：日线->TimescaleDB, 分钟级->InfluxDB
    """
    
    def __init__(self):
        self.logger = logging.getLogger("collector.akshare")
        self.logger.info("AkshareCollector 初始化完成 (支持10年历史+分钟数据)")
    
    def get_daily(self, symbol: str, start_date: str = None, end_date: str = None) -> Optional[pd.DataFrame]:
        """获取 A 股日线数据 - 支持10年历史"""
        try:
            # 默认获取最近10年数据
            if not start_date:
                start_date = (datetime.now() - timedelta(days=365*10 + 100)).strftime("%Y%m%d")  # 10年+缓冲
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            
            df = ak.stock_zh_a_hist(
                symbol=symbol.zfill(6) if symbol.isdigit() else symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            if not df.empty:
                df = df.rename(columns={
                    '日期': 'trade_date',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '成交额': 'amount',
                    '换手率': 'turnover'
                })
                # 严格选择标准英文列，避免中文列名导致SQL参数绑定失败 (修复TimescaleDB写入错误)
                # 同时添加symbol列，匹配DB表结构
                standard_columns = ['trade_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover']
                for col in standard_columns:
                    if col not in df.columns:
                        df[col] = None if col in ['turnover'] else 0.0
                df = df[standard_columns].copy()
                df['symbol'] = symbol
                df['trade_date'] = pd.to_datetime(df['trade_date'])
                df = df.sort_values('trade_date')
                self.logger.info(f"AkShare 获取 {symbol} {len(df)} 条日线数据 (10年范围: {start_date}-{end_date})")
                return df
            return None
        except Exception as e:
            self.logger.error(f"AkShare 获取 {symbol} 日线数据失败: {e}")
            return None
    
    def get_minute(self, symbol: str, period: str = "1", start_date: str = None, end_date: str = None) -> Optional[pd.DataFrame]:
        """获取 A 股分钟级实时数据 - 用于工作日高频数据写入 InfluxDB"""
        try:
            # 使用东财分钟数据接口 (更稳定)
            if not start_date:
                start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")  # 最近5天分钟数据
            if not end_date:
                end_date = datetime.now().strftime("%Y%m%d")
            
            df = ak.stock_zh_a_hist_min_em(
                symbol=symbol.zfill(6) if symbol.isdigit() else symbol,
                period=period,  # 1, 5, 15, 30, 60 分钟
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )
            
            if not df.empty:
                df = df.rename(columns={
                    '时间': 'timestamp',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '成交额': 'amount'
                })
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df = df.sort_values('timestamp')
                self.logger.info(f"AkShare 获取 {symbol} {len(df)} 条 {period}分钟数据")
                return df
            return None
        except Exception as e:
            self.logger.error(f"AkShare 获取 {symbol} {period}分钟数据失败: {e}")
            # 回退到另一个接口
            try:
                df = ak.stock_zh_a_minute(
                    symbol=symbol.zfill(6) if symbol.isdigit() else symbol,
                    period=period
                )
                if not df.empty:
                    self.logger.info(f"AkShare 使用备用接口获取 {symbol} {len(df)} 条分钟数据")
                    return df
            except:
                pass
            return None
    
    def get_all_a_stocks(self) -> List[dict]:
        """获取所有A股股票列表 - 用于批量加载10年历史数据"""
        try:
            df = ak.stock_info_a_code_name()
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    'symbol': row['code'],
                    'name': row['name'],
                    'type': 'A股'
                })
            self.logger.info(f"获取到 {len(stocks)} 只A股股票信息")
            return stocks
        except Exception as e:
            self.logger.error(f"获取A股股票列表失败: {e}")
            # 返回部分主流股票作为后备
            return [
                {'symbol': '000001', 'name': '上证指数', 'type': 'A股'},
                {'symbol': '000300', 'name': '沪深300', 'type': 'A股'},
                {'symbol': '600519', 'name': '贵州茅台', 'type': 'A股'},
                {'symbol': '601398', 'name': '工商银行', 'type': 'A股'}
            ]
