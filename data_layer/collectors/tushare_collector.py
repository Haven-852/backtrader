"""
Tushare 数据收集器 v1.0 - 多 Token 分级采集
按照 architecture/02-agent-data-access.md v1.0 实现

Token 分级:
  TUSHARE_TOKEN_BASIC    → daily, daily_basic, adj_factor, stk_limit, suspend_d
  TUSHARE_TOKEN_MINS     → stk_mins (分钟行情, 含集合竞价)
  TUSHARE_TOKEN_FINANCIAL → income, balancesheet, cashflow, fina_indicator, forecast, express, dividend
  TUSHARE_TOKEN_FLOW     → moneyflow, top_list, top_inst, margin, block_trade
  TUSHARE_TOKEN_INDEX    → index_basic, index_daily, index_weight
  TUSHARE_TOKEN_FUND     → fund_basic, fund_nav, fund_daily, fund_portfolio
  TUSHARE_TOKEN_NEWS     → news, major_news, anns
  TUSHARE_TOKEN_REPORT   → broker_reports

所有 Token 从 .env 读取，未配置时返回空 DataFrame + warning 日志（不崩溃）。
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import pandas as pd

from dotenv import load_dotenv
load_dotenv()

# 尝试导入 config
try:
    from ..config import config
except ImportError:
    from config import config  # type: ignore

logger = logging.getLogger("collector.tushare")


class TushareCollector:
    """Tushare Pro 多Token分级采集器 v1.0

    每个 Tushare 接口按积分权限自动分配对应 Token。
    Token 未配置时 gracefully 返回空 DataFrame，不阻塞程序运行。
    """

    # ─── Token 分组映射 ───────────────────────────────
    TOKEN_GROUP_MAP = {
        "basic":      "TUSHARE_TOKEN_BASIC",
        "mins":       "TUSHARE_TOKEN_MINS",
        "financial":  "TUSHARE_TOKEN_FINANCIAL",
        "flow":       "TUSHARE_TOKEN_FLOW",
        "index":      "TUSHARE_TOKEN_INDEX",
        "fund":       "TUSHARE_TOKEN_FUND",
        "news":       "TUSHARE_TOKEN_NEWS",
        "report":     "TUSHARE_TOKEN_REPORT",
    }

    # ─── 接口 → Token 组映射 ──────────────────────────
    INTERFACE_GROUP_MAP = {
        # 基础行情
        "daily":            "basic",
        "weekly":           "basic",
        "monthly":          "basic",
        "daily_basic":      "basic",
        "adj_factor":       "basic",
        "stk_limit":        "basic",
        "suspend_d":        "basic",
        "stk_managers":     "basic",
        "trade_cal":        "basic",
        "new_share":        "basic",
        "namechange":       "basic",
        "stk_factor":       "basic",
        # 分钟行情 (高频)
        "stk_mins":         "mins",
        # 财务基本面
        "income":           "financial",
        "balancesheet":     "financial",
        "cashflow":         "financial",
        "fina_indicator":   "financial",
        "forecast":         "financial",
        "express":          "financial",
        "dividend":         "financial",
        "fina_audit":       "financial",
        # 资金行为
        "moneyflow":        "flow",
        "top_list":         "flow",
        "top_inst":         "flow",
        "margin":           "flow",
        "margin_detail":    "flow",
        "block_trade":      "flow",
        "pledge_stat":      "flow",
        "pledge_detail":    "flow",
        "repurchase":       "flow",
        # 指数
        "index_basic":      "index",
        "index_daily":      "index",
        "index_weight":     "index",
        "index_classify":   "index",
        # 基金ETF
        "fund_basic":       "fund",
        "fund_nav":         "fund",
        "fund_daily":       "fund",
        "fund_portfolio":   "fund",
        "fund_share":       "fund",
        "fund_div":         "fund",
        # 新闻公告
        "news":             "news",
        "major_news":       "news",
        "anns":             "news",
        "board_secretary":  "news",
        # 研报
        "broker_reports":   "report",
    }

    def __init__(self):
        """初始化多 Token 采集器，从 .env 加载所有 Token"""
        # Token 池
        self.tokens: Dict[str, Optional[str]] = {}
        self._api_instances: Dict[str, object] = {}
        self._configured_groups: List[str] = []
        self._missing_groups: List[str] = []

        # 加载所有 Token
        for group_name, env_var in self.TOKEN_GROUP_MAP.items():
            token = config.get_tushare_token(group_name)
            if token:
                self.tokens[group_name] = token
                self._configured_groups.append(group_name)
            else:
                self.tokens[group_name] = None
                self._missing_groups.append(group_name)

        # 日志报告
        if self._configured_groups:
            logger.info(
                f"TushareCollector v1.0 就绪 — "
                f"已配置 {len(self._configured_groups)} 组 Token: "
                f"{', '.join(self._configured_groups)}"
            )
        if self._missing_groups:
            logger.warning(
                f"TushareCollector v1.0 — 未配置 Token 分组: "
                f"{', '.join(self._missing_groups)}。"
                f"相关接口调用将跳过。"
            )

    # ─── Token 管理 ───────────────────────────────────

    def _get_token(self, group: str) -> Optional[str]:
        """获取指定分组的 Token"""
        return self.tokens.get(group)

    def _get_api(self, interface: str) -> Optional[object]:
        """
        根据接口名获取对应的 Tushare Pro API 实例。
        每个 Token 组共享一个 API 实例（懒初始化）。
        """
        group = self.INTERFACE_GROUP_MAP.get(interface)
        if not group:
            logger.warning(f"未找到接口 {interface} 的 Token 分组映射")
            return None

        token = self._get_token(group)
        if not token:
            logger.debug(f"Token 组 [{group}] 未配置，{interface} 接口跳过")
            return None

        # 懒初始化 API 实例
        if group not in self._api_instances:
            import tushare as ts
            ts.set_token(token)
            pro = ts.pro_api()
            pro._DataApi__http_url = config.tushare_api_url
            self._api_instances[group] = pro
            logger.info(f"Tushare API [{group}] 已初始化 -> {config.tushare_api_url}")

        return self._api_instances[group]

    def _call_api(
        self,
        interface: str,
        method_name: str,
        **kwargs
    ) -> Optional[pd.DataFrame]:
        """
        统一的 API 调用封装。
        自动选择对应 Token，Token 未配置时返回 None（不崩溃）。
        """
        api = self._get_api(interface)
        if not api:
            return None

        try:
            method = getattr(api, method_name)
            df = method(**kwargs)
            if df is not None and not df.empty:
                logger.info(f"Tushare [{interface}] 获取 {len(df)} 条数据")
                return df
            logger.info(f"Tushare [{interface}] 返回空数据")
            return None
        except Exception as e:
            logger.error(f"Tushare [{interface}] 调用失败: {e}")
            return None

    # ─── 基础行情接口 ─────────────────────────────────

    def get_daily(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取日线数据（10年历史范围）
        token: TUSHARE_TOKEN_BASIC
        """
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365 * 10 + 100)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        return self._call_api("daily", "daily",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
        )

    def get_daily_basic(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取每日指标/盘前股本（总股本、流通股本、PE、PB 等）
        token: TUSHARE_TOKEN_BASIC
        """
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        return self._call_api("daily_basic", "daily_basic", **kwargs)

    def get_adj_factor(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取复权因子（前/后复权因子）
        token: TUSHARE_TOKEN_BASIC
        """
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        return self._call_api("adj_factor", "adj_factor", **kwargs)

    def get_suspend_d(
        self,
        ts_code: Optional[str] = None,
        suspend_date: Optional[str] = None,
        resume_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取停复牌信息
        token: TUSHARE_TOKEN_BASIC
        """
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if suspend_date:
            kwargs["suspend_date"] = suspend_date
        if resume_date:
            kwargs["resume_date"] = resume_date

        return self._call_api("suspend_d", "suspend_d", **kwargs)

    def get_stk_limit(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取每日涨跌停价格
        token: TUSHARE_TOKEN_BASIC
        """
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        return self._call_api("stk_limit", "stk_limit", **kwargs)

    # ─── 分钟行情（高频，含集合竞价）──────────────────

    def get_mins(
        self,
        ts_code: str,
        freq: str = "1min",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取分钟K线（含盘前集合竞价数据，freq='auction'）
        token: TUSHARE_TOKEN_MINS
        支持 freq: 1min, 5min, 15min, 30min, 60min, auction
        """
        if not start_date and not end_date and not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        kwargs = {"ts_code": ts_code, "freq": freq}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if trade_date:
            kwargs["trade_date"] = trade_date

        return self._call_api("stk_mins", "stk_mins", **kwargs)

    def get_auction(
        self,
        ts_code: str,
        trade_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取集合竞价数据（freq='auction'）
        token: TUSHARE_TOKEN_MINS
        """
        return self.get_mins(ts_code=ts_code, freq="auction", trade_date=trade_date)

    # ─── 财务基本面接口 ─────────────────────────────────

    def get_income(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """利润表 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("income", "income", **kwargs)

    def get_balancesheet(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """资产负债表 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("balancesheet", "balancesheet", **kwargs)

    def get_cashflow(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """现金流量表 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("cashflow", "cashflow", **kwargs)

    def get_fina_indicator(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """财务指标（ROE/ROA/毛利率等）token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("fina_indicator", "fina_indicator", **kwargs)

    def get_forecast(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """业绩预告 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("forecast", "forecast", **kwargs)

    def get_express(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """业绩快报 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("express", "express", **kwargs)

    def get_dividend(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """分红送股 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("dividend", "dividend", **kwargs)

    def get_fina_audit(
        self,
        ts_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """财务审计意见 token: TUSHARE_TOKEN_FINANCIAL"""
        kwargs = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("fina_audit", "fina_audit", **kwargs)

    # ─── 资金行为接口 ─────────────────────────────────

    def get_moneyflow(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """个股资金流向 token: TUSHARE_TOKEN_FLOW"""
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("moneyflow", "moneyflow", **kwargs)

    def get_moneyflow_hsgt(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """沪深港通资金流向 token: TUSHARE_TOKEN_FLOW"""
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        return self._call_api("moneyflow", "moneyflow_hsgt",
            start_date=start_date, end_date=end_date)

    def get_stk_factor(
        self,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """股票技术因子 token: TUSHARE_TOKEN_BASIC"""
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return self._call_api("daily_basic", "stk_factor", **kwargs)

    # ─── 兼容旧版接口 ─────────────────────────────────

    def get_fundamental(self, symbol: str) -> Optional[pd.DataFrame]:
        """兼容旧版：查询基本面数据（财务指标）"""
        return self.get_fina_indicator(symbol)

    # ─── 诊断 ─────────────────────────────────────────

    def status(self) -> Dict:
        """返回采集器状态摘要"""
        return {
            "configured_groups": self._configured_groups,
            "missing_groups": self._missing_groups,
            "total_groups": len(self.TOKEN_GROUP_MAP),
            "ready": len(self._configured_groups) > 0,
        }

    def is_interface_available(self, interface: str) -> bool:
        """检查指定接口的 Token 是否已配置"""
        group = self.INTERFACE_GROUP_MAP.get(interface)
        if not group:
            return False
        return group in self._configured_groups
