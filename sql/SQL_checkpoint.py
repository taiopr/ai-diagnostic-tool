import psycopg2, psycopg2.extras, os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# 1. How many sessions do you have?
cur.execute("SELECT COUNT(*) as total FROM sessions")
print(cur.fetchone())

# 2. What are the scores of your 5 most recent sessions?
cur.execute("""
    SELECT id, score, summary, created_at
    FROM sessions
    ORDER BY created_at DESC
    LIMIT 5
""")
for row in cur.fetchall():
    print(dict(row))

# 3. What are the most common issue types?
cur.execute("""
    SELECT type, COUNT(*) as count
    FROM issues
    GROUP BY type
    ORDER BY count DESC
""")
for row in cur.fetchall():
    print(dict(row))

# 4. Show sessions with their issue count (JOIN)
cur.execute("""
    SELECT s.id, s.score, COUNT(i.id) as issue_count
    FROM sessions s
    LEFT JOIN issues i ON i.session_id = s.id
    GROUP BY s.id, s.score
    ORDER BY issue_count DESC
""")
for row in cur.fetchall():
    print(dict(row))

# 5. What issues does the lowest-scoring session have?
cur.execute("""
    SELECT i.type, i.severity, i.explanation
    FROM issues i
    JOIN sessions s ON i.session_id = s.id
    WHERE s.score = (SELECT MIN(score) FROM sessions)
    ORDER BY i.severity
""")
for row in cur.fetchall():
    print(dict(row))

conn.close()