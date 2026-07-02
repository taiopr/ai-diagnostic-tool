import psycopg2, os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
with open("sql/webhook_schema.sql", "r") as f:
    cur.execute(f.read())
conn.commit()
print("webhook_events table created")
cur.close()
conn.close()