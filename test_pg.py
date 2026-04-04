import psycopg2
import sys

try:
    conn = psycopg2.connect("dbname=production user=postgres password=2lfFO74FFWQvS2NChyeK host=46.62.220.230 port=5433")
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'apartment'")
        print("apartment:", [r[0] for r in cur.fetchall()])
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'cottage'")
        print("cottage:", [r[0] for r in cur.fetchall()])
except Exception as e:
    print("ERR:", e)
