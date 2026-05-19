"""
高级数据采集器 - 新闻、公告、研报、董秘互动、政策法规、资金流等
按照 architecture/02-agent-data-access.md v1.0 实现

Tushare Pro 接口映射:
  - 新闻: news / major_news (TUSHARE_TOKEN_NEWS)
  - 公告: anns (TUSHARE_TOKEN_NEWS)
  - 研报: broker_reports (TUSHARE_TOKEN_REPORT)
  - 资金流向: moneyflow (TUSHARE_TOKEN_FLOW)
  - 龙虎榜: top_list (TUSHARE_TOKEN_FLOW)
  - 融资融券: margin / margin_detail (TUSHARE_TOKEN_FLOW)
  - 基金净值: fund_nav (TUSHARE_TOKEN_FUND)
  - 指数日线: index_daily (TUSHARE_TOKEN_INDEX)

所有 Token 必须从 .env 读取，Token 未配置时 gracefully 返回 None（不崩溃）。
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import pandas as pd

from dotenv import load_dotenv
load_dotenv()

from ..config import config

logger = logging.getLogger("collector.advanced")


class AdvancedCollector:
    """高级数据采集器 - 非行情类数据全覆盖"""

    def __init__(self):
        # 统一 Token（当前所有组共用同一 Token）
        token = config.get_tushare_token("news") or config.get_tushare_token("default")
        self._configured = bool(token)
        self._pro = None  # 统一 API 实例，懒初始化

        if token:
            import tushare as ts
            ts.set_token(token)
            self._pro = ts.pro_api()
            self._pro._DataApi__http_url = config.tushare_api_url
            logger.info(f"AdvancedCollector 已就绪 -> {config.tushare_api_url}")
        else:
            logger.warning(
                "AdvancedCollector Token 未配置 — "
                "新闻/公告/研报/资金/基金/指数采集将全部跳过。"
            )

    # ─── 新闻资讯 ──────────────────────────────────────

    def get_news(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        ts_code: Optional[str] = None,
        src: Optional[str] = None,
        limit: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        获取即时新闻 (Tushare news 接口)
        token: TUSHARE_TOKEN_NEWS
        """
        if not self._pro:
            logger.debug("News Token 未配置，跳过 news 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if src:
                kwargs["src"] = src

            df = self._pro.news(**kwargs)
            if df is not None and not df.empty:
                # 限制返回量
                df = df.head(limit)
                logger.info(f"获取新闻 {len(df)} 条 (news)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取新闻失败: {e}")
            return None

    def get_major_news(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        src: Optional[str] = None,
        limit: int = 50
    ) -> Optional[pd.DataFrame]:
        """
        获取要闻精选 (Tushare major_news 接口)
        token: TUSHARE_TOKEN_NEWS
        """
        if not self._pro:
            logger.debug("News Token 未配置，跳过 major_news 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if src:
                kwargs["src"] = src

            df = self._pro.major_news(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取要闻 {len(df)} 条 (major_news)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取要闻失败: {e}")
            return None

    # ─── 公司公告 ──────────────────────────────────────

    def get_announcements(
        self,
        ts_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        ann_type: Optional[str] = None,
        limit: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        获取公司公告 (Tushare anns 接口)
        token: TUSHARE_TOKEN_NEWS
        """
        if not self._pro:
            logger.debug("News Token 未配置，跳过 anns 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if ann_type:
                kwargs["ann_type"] = ann_type

            df = self._pro.anns(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取公告 {len(df)} 条 (anns)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取公告失败: {e}")
            return None

    # ─── 券商研报 ──────────────────────────────────────

    def get_broker_reports(
        self,
        ts_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100
    ) -> Optional[pd.DataFrame]:
        """
        获取券商研报 (Tushare broker_reports 接口)
        token: TUSHARE_TOKEN_REPORT
        """
        pro = self._pro
        if not pro:
            logger.debug("Report Token 未配置，跳过 broker_reports 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.broker_reports(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取研报 {len(df)} 条 (broker_reports)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取研报失败: {e}")
            return None

    # ─── 资金流向 ──────────────────────────────────────

    def get_moneyflow(
        self,
        ts_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        获取个股资金流向 (Tushare moneyflow 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 moneyflow 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.moneyflow(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取资金流向 {len(df)} 条 (moneyflow)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取资金流向失败: {e}")
            return None

    def get_hsgt_moneyflow(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        获取沪深港通资金流向 (Tushare moneyflow_hsgt 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 moneyflow_hsgt 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            df = pro.moneyflow_hsgt(start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取沪深港通资金 {len(df)} 条 (moneyflow_hsgt)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取沪深港通资金失败: {e}")
            return None

    # ─── 龙虎榜 ────────────────────────────────────────

    def get_top_list(
        self,
        trade_date: Optional[str] = None,
        ts_code: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取龙虎榜 (Tushare top_list 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 top_list 采集")
            return None
        try:
            if not trade_date:
                trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

            kwargs = {"trade_date": trade_date}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.top_list(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取龙虎榜 {len(df)} 条 (top_list)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取龙虎榜失败: {e}")
            return None

    def get_top_inst(
        self,
        trade_date: Optional[str] = None,
        ts_code: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取机构龙虎榜 (Tushare top_inst 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 top_inst 采集")
            return None
        try:
            if not trade_date:
                trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

            kwargs = {"trade_date": trade_date}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.top_inst(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取机构龙虎榜 {len(df)} 条 (top_inst)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取机构龙虎榜失败: {e}")
            return None

    # ─── 融资融券 ──────────────────────────────────────

    def get_margin(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        获取两融汇总 (Tushare margin 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 margin 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            df = pro.margin(start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取两融汇总 {len(df)} 条 (margin)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取两融汇总失败: {e}")
            return None

    def get_margin_detail(
        self,
        trade_date: Optional[str] = None,
        ts_code: Optional[str] = None,
        limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        获取两融明细 (Tushare margin_detail 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 margin_detail 采集")
            return None
        try:
            if not trade_date:
                trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

            kwargs = {"trade_date": trade_date}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.margin_detail(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取两融明细 {len(df)} 条 (margin_detail)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取两融明细失败: {e}")
            return None

    # ─── 大宗交易 ──────────────────────────────────────

    def get_block_trade(
        self,
        ts_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取大宗交易 (Tushare block_trade 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 block_trade 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.block_trade(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取大宗交易 {len(df)} 条 (block_trade)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取大宗交易失败: {e}")
            return None

    def get_pledge_stat(
        self,
        ts_code: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取股权质押统计 (Tushare pledge_stat 接口)
        token: TUSHARE_TOKEN_FLOW
        """
        pro = self._pro
        if not pro:
            logger.debug("Flow Token 未配置，跳过 pledge_stat 采集")
            return None
        try:
            kwargs = {}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.pledge_stat(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取质押统计 {len(df)} 条 (pledge_stat)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取质押统计失败: {e}")
            return None

    # ─── 基金/ETF ──────────────────────────────────────

    def get_fund_basic(
        self,
        market: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取基金基本信息 (Tushare fund_basic 接口)
        token: TUSHARE_TOKEN_FUND
        """
        pro = self._pro
        if not pro:
            logger.debug("Fund Token 未配置，跳过 fund_basic 采集")
            return None
        try:
            kwargs = {}
            if market:
                kwargs["market"] = market
            df = pro.fund_basic(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取基金基本信息 {len(df)} 条 (fund_basic)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取基金基本信息失败: {e}")
            return None

    def get_fund_nav(
        self,
        ts_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        获取基金净值 (Tushare fund_nav 接口)
        token: TUSHARE_TOKEN_FUND
        """
        pro = self._pro
        if not pro:
            logger.debug("Fund Token 未配置，跳过 fund_nav 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.fund_nav(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取基金净值 {len(df)} 条 (fund_nav)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取基金净值失败: {e}")
            return None

    def get_fund_portfolio(
        self,
        ts_code: str,
        end_date: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取基金持仓 (Tushare fund_portfolio 接口)
        token: TUSHARE_TOKEN_FUND
        """
        pro = self._pro
        if not pro:
            logger.debug("Fund Token 未配置，跳过 fund_portfolio 采集")
            return None
        try:
            kwargs = {"ts_code": ts_code}
            if end_date:
                kwargs["end_date"] = end_date

            df = pro.fund_portfolio(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取基金持仓 {len(df)} 条 (fund_portfolio)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取基金持仓失败: {e}")
            return None

    # ─── 指数 ──────────────────────────────────────────

    def get_index_basic(
        self,
        market: Optional[str] = None,
        limit: int = 200
    ) -> Optional[pd.DataFrame]:
        """
        获取指数基本信息 (Tushare index_basic 接口)
        token: TUSHARE_TOKEN_INDEX
        """
        pro = self._pro
        if not pro:
            logger.debug("Index Token 未配置，跳过 index_basic 采集")
            return None
        try:
            kwargs = {}
            if market:
                kwargs["market"] = market

            df = pro.index_basic(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取指数基本信息 {len(df)} 条 (index_basic)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取指数基本信息失败: {e}")
            return None

    def get_index_daily(
        self,
        ts_code: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 3000
    ) -> Optional[pd.DataFrame]:
        """
        获取指数日线 (Tushare index_daily 接口)
        token: TUSHARE_TOKEN_INDEX
        """
        pro = self._pro
        if not pro:
            logger.debug("Index Token 未配置，跳过 index_daily 采集")
            return None
        try:
            if not start:
                start = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
            if not end:
                end = datetime.now().strftime("%Y%m%d")

            kwargs = {"start_date": start, "end_date": end}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = pro.index_daily(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取指数日线 {len(df)} 条 (index_daily)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取指数日线失败: {e}")
            return None

    def get_index_weight(
        self,
        index_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        limit: int = 500
    ) -> Optional[pd.DataFrame]:
        """
        获取指数成分权重 (Tushare index_weight 接口)
        token: TUSHARE_TOKEN_INDEX
        """
        pro = self._pro
        if not pro:
            logger.debug("Index Token 未配置，跳过 index_weight 采集")
            return None
        try:
            if not trade_date:
                trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

            kwargs = {"trade_date": trade_date}
            if index_code:
                kwargs["index_code"] = index_code

            df = pro.index_weight(**kwargs)
            if df is not None and not df.empty:
                df = df.head(limit)
                logger.info(f"获取指数权重 {len(df)} 条 (index_weight)")
                return df
            return None
        except Exception as e:
            logger.error(f"获取指数权重失败: {e}")
            return None

    # ─── 诊断 ──────────────────────────────────────────

    def status(self) -> Dict:
        """返回采集器状态摘要"""
        return {
            "configured": self._configured,
        }


# 单例
advanced_collector = AdvancedCollector()
