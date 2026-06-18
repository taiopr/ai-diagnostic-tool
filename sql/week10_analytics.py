import psycopg2, psycopg2.extras, os
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Query 1: Most common issue types
cur.execute("""
                SELECT i.severity,
                    COUNT(*) as issue_count,
                    ROUND(AVG(s.score)::numeric, 1) as avg_session_score
                FROM issues i
                JOIN sessions s ON i.session_id = s.id
                GROUP BY i.severity
                ORDER BY 
                    CASE i.severity 
                        WHEN 'high' THEN 1 
                        WHEN 'medium' THEN 2 
                        WHEN 'low' THEN 3 
                    END;
""")
for row in cur.fetchall():
    print(dict(row))

conn.close()