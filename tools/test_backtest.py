"""
回测引擎验证脚本 — 测试 backtrader 完整流程
使用生成的模拟数据 (A股典型形态: 3000-4000点震荡上行)

Run:
    python tools/test_backtest.py
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtrader as bt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ============================
# 1. 生成模拟 A 股日线数据
# ============================

def generate_mock_data(n_days=500, start_price=3000, seed=42):
    """生成带趋势+波动的模拟 OHLCV 数据 (仿A股大盘)"""
    np.random.seed(seed)
    dates = pd.date_range('2023-01-01', periods=n_days, freq='B')
    returns = np.random.normal(0.0003, 0.015, n_days)  # 正偏 + 1.5%日波动

    closes = start_price * np.exp(np.cumsum(returns))
    highs = closes * (1 + np.abs(np.random.normal(0, 0.01, n_days)))
    lows = closes * (1 - np.abs(np.random.normal(0, 0.01, n_days)))
    opens = closes * (1 + np.random.normal(0, 0.005, n_days))
    volumes = np.random.randint(1e6, 5e7, n_days).astype(float)

    df = pd.DataFrame({
        'datetime': dates,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
        'openinterest': 0,
    })
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    print(f"   Generated {len(df)} days OHLCV data "
          f"(price: {closes[0]:.1f} ~ {closes[-1]:.1f})")
    return df


# ============================
# 2. 策略: 布林带突破 + 双均线过滤
# ============================

class BollingerMACross(bt.Strategy):
    """
    进场: 价格突破上轨 + 快线>慢线
    出场: 价格跌破中轨 OR 快线下穿慢线
    """
    params = dict(
        period=20,      # 布林带周期
        devfactor=2.0,  # 标准差倍数
        fast=10,        # 快线
        slow=30,        # 慢线
        stop_loss=0.05, # 止损 5%
    )

    def __init__(self):
        self.boll = bt.indicators.BollingerBands(
            self.data.close, period=self.p.period, devfactor=self.p.devfactor
        )
        self.sma_fast = bt.ind.SMA(period=self.p.fast)
        self.sma_slow = bt.ind.SMA(period=self.p.slow)
        self.cross = bt.ind.CrossOver(self.sma_fast, self.sma_slow)

    def next(self):
        if not self.position:
            if (self.data.close > self.boll.lines.top and
                self.cross > 0):
                self.buy()
        else:
            # 出场: 跌破中轨 或 均线死叉
            if (self.data.close < self.boll.lines.mid or
                self.cross < 0):
                self.close()

    def notify_trade(self, trade):
        if trade.isclosed:
            sign = "+" if trade.pnl > 0 else "-"
            print(f"  [{sign}] {trade.tradeid}: pnl={trade.pnl:+.2f} "
                  f"net={trade.pnlcomm:+.2f} size={trade.size}")


# ============================
# 3. 回测主流程
# ============================

def run():
    print("=" * 60)
    print("  BACKTRADER TEST: Bollinger + MACross Strategy")
    print("=" * 60)

    # 生成数据
    print("\n[1] Generating mock data...")
    df = generate_mock_data(n_days=500)
    data = bt.feeds.PandasData(dataname=df)

    # 组装引擎
    cerebro = bt.Cerebro()
    cerebro.addstrategy(BollingerMACross)
    cerebro.adddata(data)
    cerebro.broker.setcash(1_000_000)
    cerebro.broker.setcommission(commission=0.00025)

    # 仓位管理: 每次 80%
    cerebro.addsizer(bt.sizers.PercentSizer, percents=80)

    # 分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe',
                        timeframe=bt.TimeFrame.Days, riskfreerate=0.03)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.SQN, _name='sqn')

    # 跑
    print("\n[2] Running backtest...\n")
    start_val = cerebro.broker.getvalue()
    results = cerebro.run()
    end_val = cerebro.broker.getvalue()
    strat = results[0]

    # 输出
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    total_return = (end_val / start_val - 1) * 100
    print(f"  Initial Value:   {start_val:>14,.2f}")
    print(f"  Final Value:     {end_val:>14,.2f}")
    print(f"  Total Return:    {total_return:>13.2f}%")

    sharpe = strat.analyzers.sharpe.get_analysis()
    sr = sharpe.get('sharperatio', None)
    print(f"  Sharpe Ratio:    {sr:>13.2f}" if sr else
          f"  Sharpe Ratio:    {'N/A':>13}")

    dd = strat.analyzers.dd.get_analysis()
    print(f"  Max Drawdown:    {dd.max.drawdown:>12.2f}%")
    print(f"  Max DD Length:   {dd.max.len:>13} days")

    sqn = strat.analyzers.sqn.get_analysis()
    print(f"  SQN Score:       {sqn.get('sqn', 'N/A')}")

    ta = strat.analyzers.trades.get_analysis()
    total = ta.get('total', {}).get('total', 0)
    won = ta.get('won', {}).get('total', 0)
    lost = ta.get('lost', {}).get('total', 0)
    print(f"  Total Trades:    {total}")
    print(f"  Won / Lost:      {won} / {lost}")
    if total > 0:
        print(f"  Win Rate:        {won/total*100:>13.1f}%")
    print("=" * 60)

    # Plot
    print("\n[3] Plotting...")
    fig = cerebro.plot(style='candlestick', volume=True)[0][0]
    out = 'backtest_result.png'
    fig.savefig(out, dpi=150, bbox_inches='tight')
    print(f"  Chart saved: {os.path.abspath(out)}")
    print("\n  SUCCESS! backtrader pipeline is working.\n")
    print("  Next: replace generate_mock_data() with AkShare/Tushare")
    print("  once your proxy allows direct connections.\n")


if __name__ == '__main__':
    run()
