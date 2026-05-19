import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'data_layer')
from config import config as cfg
from sqlalchemy import create_engine, text

engine = create_engine(cfg.get_postgres_url())
tables = [
    'ref_suspend_d', 'ref_trade_cal', 'ref_new_share', 'ref_stock_namechange',
    'index_daily', 'etf_daily', 'fund_daily', 'fund_nav',
    'margin_summary_daily', 'stock_auction_daily'
]
print('=' * 70)
print(f'{"表名":<30} {"行数":>8}  {"状态"}')
print('=' * 70)
with engine.connect() as conn:
    for t in tables:
        try:
            r = conn.execute(text(f'SELECT count(*) FROM {t}'))
            cnt = r.scalar()
            status = 'OK' if cnt > 0 else 'EMPTY'
            print(f'{t:<30} {cnt:>8}  {status}')
        except Exception as e:
            print(f'{t:<30} {"N/A":>8}  ERR: {str(e)[:50]}')
print('=' * 70)
engine.dispose()
