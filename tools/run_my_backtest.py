"""
我的第一个回测脚本 — SMA 双均线交叉策略
用 AkShare 拉取 A 股真实日线数据，backtrader 回测 + 画图

用法：
    python tools/run_my_backtest.py                   # 默认: 平安银行 2024年
    python tools/run_my_backtest.py --stock 600519    # 茅台
    python tools/run_my_backtest.py --fast 5 --slow 20  # 调整均线周期
"""

import argparse
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtrader as bt
import pandas as pd
import akshare as ak


# ============================================================
#  策略定义
# ============================================================

class SmaCross(bt.Strategy):
    """双均线交叉策略: 快线上穿慢线买入, 下穿卖出"""
    params = dict(
        fast=10,   # 快线周期
        slow=30,   # 慢线周期
    )

    def __init__(self):
        sma_fast = bt.ind.SMA(period=self.p.fast)
        sma_slow = bt.ind.SMA(period=self.p.slow)
        self.crossover = bt.ind.CrossOver(sma_fast, sma_slow)

        # 记录买卖点用于画图
        self.buy_signals = []
        self.sell_signals = []

    def next(self):
        if not self.position:
            if self.crossover > 0:
                self.buy()
        elif self.crossover < 0:
            self.close()

    def notify_trade(self, trade):
        if trade.isclosed:
            print(f"  Trade: {'WIN' if trade.pnl > 0 else 'LOSS'} pnl={trade.pnl:.2f}, net={trade.pnlcomm:.2f}")


# ============================================================
#  数据获取  (AkShare → pandas → backtrader)
# ============================================================

def get_akshare_data(symbol, start, end):
    """从 AkShare 取 A 股历史日线, 转为 backtrader 兼容格式"""
    # 绕过系统代理 (Clash/V2Ray 等)
    import os as _os
    for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
               'ALL_PROXY', 'all_proxy', 'REQUESTS_CA_BUNDLE'):
        _os.environ.pop(_k, None)
    _os.environ['NO_PROXY'] = '*'

    import requests
    _orig_get = requests.get
    def _no_proxy_get(url, **kw):
        kw.setdefault('proxies', {'http': None, 'https': None})
        return _orig_get(url, **kw)
    requests.get = _no_proxy_get

    print(f"\n[Fetching] AkShare: {symbol} ({start} ~ {end})...")

    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period='daily',
        start_date=start,
        end_date=end,
        adjust='qfq',   # 前复权
    )

    if df.empty:
        raise RuntimeError(f"未获取到 {symbol} 数据")

    # AkShare 中文列 → backtrader 英文列
    df = df.rename(columns={
        '日期':     'datetime',
        '开盘':     'open',
        '最高':     'high',
        '最低':     'low',
        '收盘':     'close',
        '成交量':   'volume',
        '成交额':   'amount',
    })

    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime')
    df = df.sort_index()
    df['openinterest'] = 0   # backtrader 要求有这个列

    print(f"  [OK] Got {len(df)} daily bars")
    print(f"       Date range: {df.index[0].date()} ~ {df.index[-1].date()}")
    print(f"       Price range: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    return df


# ============================================================
#  主回测流程
# ============================================================

def run_backtest(stock_code, fast, slow, start, end, cash):
    print("=" * 60)
    print(f"[Backtest Config]")
    print(f"   Stock: {stock_code}")
    print(f"   Strategy: SMA({fast}) x SMA({slow}) crossover")
    print(f"   Period: {start} ~ {end}")
    print(f"   Initial Cash: {cash:,.0f}")
    print(f"   Commission: 0.025% (buy+sell)")
    print("=" * 60)

    # 1. 拉数据
    df = get_akshare_data(stock_code, start, end)
    data = bt.feeds.PandasData(dataname=df)

    # 2. 组装回测引擎
    cerebro = bt.Cerebro()
    cerebro.addstrategy(SmaCross, fast=fast, slow=slow)
    cerebro.adddata(data)
    cerebro.broker.setcash(cash)

    # A 股佣金: 万2.5, 印花税 千1(仅卖出)
    cerebro.broker.setcommission(commission=0.00025)
    # 印花税简化处理在 commission 中
    cerebro.addsizer(bt.sizers.PercentSizer, percents=95)  # 仓位 95%

    # 添加分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')

    # 3. 跑!
    print(f"\n[Running] Backtest...")
    start_value = cerebro.broker.getvalue()
    results = cerebro.run()
    final_value = cerebro.broker.getvalue()

    # 4. 结果
    strat = results[0]
    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(f"  Initial:     {start_value:>12,.2f}")
    print(f"  Final:       {final_value:>12,.2f}")
    print(f"  Return:      {(final_value/start_value - 1)*100:>11.2f}%")

    # Sharpe
    sharpe = strat.analyzers.sharpe.get_analysis()
    sharpe_val = sharpe.get('sharperatio', None)
    if sharpe_val:
        print(f"  Sharpe:      {sharpe_val:>12.2f}")
    else:
        print(f"  Sharpe:      N/A (not enough trades)")

    # 最大回撤
    dd = strat.analyzers.drawdown.get_analysis()
    print(f"  Max Drawdown: {dd.max.drawdown:>11.2f}%")

    # 交易统计
    trade_analysis = strat.analyzers.trades.get_analysis()
    total_trades = trade_analysis.get('total', {}).get('total', 0)
    won = trade_analysis.get('won', {}).get('total', 0)
    lost = trade_analysis.get('lost', {}).get('total', 0)
    print(f"  Total Trades: {total_trades}")
    print(f"  Won:          {won}")
    print(f"  Lost:         {lost}")
    win_rate = (won / total_trades * 100) if total_trades > 0 else 0
    print(f"  Win Rate:     {win_rate:.1f}%")
    print("=" * 60)

    # 5. 画图
    print("\n[Plotting] Generating chart...")
    fig = cerebro.plot(style='candlestick', volume=True)[0][0]
    fig.savefig('backtest_result.png', dpi=150)
    print("  Chart saved: backtest_result.png")
    print("\n[Done] Backtest complete!")


# ============================================================
#  命令行入口
# ============================================================

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A股双均线回测')
    parser.add_argument('--stock', default='000001', help='股票代码 (默认 000001 平安银行)')
    parser.add_argument('--fast',  type=int, default=10, help='快线周期')
    parser.add_argument('--slow',  type=int, default=30, help='慢线周期')
    parser.add_argument('--start', default='2023-01-01', help='起始日期')
    parser.add_argument('--end',   default='2025-05-19', help='结束日期')
    parser.add_argument('--cash',  type=float, default=100000, help='初始资金')
    args = parser.parse_args()

    run_backtest(
        stock_code=args.stock,
        fast=args.fast,
        slow=args.slow,
        start=args.start,
        end=args.end,
        cash=args.cash,
    )
