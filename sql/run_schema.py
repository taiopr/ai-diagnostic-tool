import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(SCRIPT_DIR, "schema.sql"), "r") as f:
    schema = f.read()

cur.execute(schema)
conn.commit()
print("Schema created successfully")

cur.close()
conn.close()