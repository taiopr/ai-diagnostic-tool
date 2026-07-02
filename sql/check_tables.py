import psycopg2, os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_Schema = 'public'
            ORDER BY table_name;
""")
print(cur.fetchall())
conn.close()