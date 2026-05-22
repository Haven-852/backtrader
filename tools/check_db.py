import psycopg2

conn = psycopg2.connect(
    host='127.0.0.1', port=15432,
    user='quant', password='backtrader123',
    dbname='backtrader'
)
cur = conn.cursor()

# List all tables
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
tables = [t[0] for t in cur.fetchall()]
print(f"Tables in quant_db ({len(tables)}):")
for t in tables:
    cur.execute(f'SELECT count(*) FROM "{t}"')
    cnt = cur.fetchone()[0]
    print(f"\n  [{t}]  ({cnt} rows)")
    
    if cnt > 0:
        # Show columns
        cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='{t}' ORDER BY ordinal_position")
        cols = cur.fetchall()
        print(f"    Columns: {', '.join(c[0] + '(' + c[1] + ')' for c in cols)}")
        
        # Show sample rows
        cur.execute(f'SELECT * FROM "{t}" LIMIT 5')
        rows = cur.fetchall()
        for r in rows:
            print(f"    {r}")

cur.close()
conn.close()
