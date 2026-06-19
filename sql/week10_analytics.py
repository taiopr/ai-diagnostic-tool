import psycopg2, psycopg2.extras, os
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Query 1: Most common issue types
print("\n--- Query 1: Most common issue types ---")
cur.execute("""
    SELECT type, COUNT(*) as occurrences,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM sessions), 1) as pct_of_sessions
    FROM issues
            GROUP BY type
            ORDER BY occurrences DESC;
""")
for row in cur.fetchall():
    print(dict(row))

# Query 2: Failure rate by hour of day
print("\n--- Query 2: Failure rate by hour of day ---")
cur.execute("""
    SELECT EXTRACT (HOUR FROM created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Madrid') as hour_of_day,
            COUNT(*) as total_sessions,
            COUNT(*) FILTER (WHERE score < 40) as low_score_sessions,
            ROUND(COUNT(*) FILTER (WHERE score < 40) * 100.0 / COUNT(*), 1) as low_score_pct
    FROM sessions
    GROUP BY hour_of_day
    ORDER BY hour_of_day;
""")
for row in cur.fetchall():
    print(dict(row))

# Query 3: Avg response time by input mode
print("\n--- Query 3: Avg response time by input mode ---")
cur.execute("""
    SELECT input_mode,
            ROUND(AVG(response_time_ms)::numeric, 0) as avg_response_time_ms,
            ROUND(MIN(response_time_ms)::numeric, 0) as fastest_ms,
            ROUND(MAX(response_time_ms)::numeric, 0) as slowest_ms,
            COUNT(*) as sample_size
    FROM sessions
    WHERE response_time_ms IS NOT NULL
    GROUP BY input_mode;
""")
for row in cur.fetchall():
    print(dict(row))

# Query 4: Score trend over time
print("\n--- Query 4: Score trend over time ---")
cur.execute("""
    SELECT DATE(created_at) as day,
            COUNT(*) as sessions_run,
            ROUND(AVG(score)::numeric, 1) as avg_score
    FROM sessions
    GROUP BY DATE(created_at)
    ORDER BY day;
""")
for row in cur.fetchall():
    print(dict(row))

# Query 5: Severity vs avg score
print("\n--- Query 5: Severity vs avg score ---")
cur.execute("""
    SELECT i.severity,
            COUNT(*) as issue_count,
            ROUND(AVG(s.score)::numeric, 1) as avg_score
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