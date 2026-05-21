import os
import uuid
from datetime import datetime
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from ai import DiagnosticResult

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# ── Connection helper ──────────────────────────────────────────────

def get_connection():
    """
    Open and return a database connection.
    Caller is responsible for closing it.
    """
    return psycopg2.connect(DATABASE_URL)

# ── Save a session ─────────────────────────────────────────────────

def save_session(
        result: DiagnosticResult,
        improved_output: str | None,
        original_prompt: str,
        test_input: str,
        input_mode: str = "prompt",
        model_used: str = "claude-sonnet-4-20250514",
        session_label: str | None = None,
        response_time_ms: int | None = None
) -> str:
    """
    Save a diagnostic session and its issues to the database.
    Returns the session_id (UUID string).
    Raises on database error - caller decides how to handle.
    """
    session_id = str(uuid.uuid4())

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Insert the session row
        cur.execute("""
                    INSERT INTO sessions (
                    id, session_label, original_prompt, test_input,
                    input_mode, model_used, score, summary,
                    original_output, suggested_prompt, improved_output,
                    response_time_ms
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s
                )
            """, (
                session_id, session_label, original_prompt, test_input,
                input_mode, model_used, result.score, result.summary,
                result.test_output, result.suggested_prompt, improved_output,
                response_time_ms
            ))
        
        # Insert one row per issue
        for issue in result.issues:
            cur.execute("""
                INSERT INTO issues (
                    id, session_id, type, severity, explanation, fix
                ) VALUES (
                        %s, %s, %s, %s, %s, %s
                )
            """, (
                str(uuid.uuid4()),
                session_id,
                issue.type,
                issue.severity,
                issue.explanation,
                issue.fix
            ))
            
        conn.commit()
        return session_id
        
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

# ── Get one session ─────────────────────────────────────────────────

def get_session(session_id: str) -> dict | None:
    """
    Retrieve a complete session with all its issues.
    Returns a dict with session fields + issues list, or None if not found.
    """
    conn = get_connection()
    try:
        # RealDictCursor returns rows as dicts instead of tuples
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get the session
        cur.execute("""
             SELECT * FROM sessions WHERE id = %s
        """, (session_id,))
        
        session = cur.fetchone()
        
        if session is None:
            return None
        
        # Get all issues for this session
        cur.execute("""
            SELECT id, type, severity, explanation, fix
            FROM issues
            WHERE session_id = %s
            ORDER BY
                CASE severity
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END
        """, (session_id,))

        issues = cur.fetchall()

        # Combine into one object
        result = dict(session)
        result["issues"] = [dict(i) for i in issues]

        return result
    
    finally:
        conn.close()

# ── List sessions ─────────────────────────────────────────────────

def list_sessions(limit: int = 20, mode: str | None = None) -> list[dict]:
    """
    List past sessions for the history view.
    Includes issue_count from a JOIN - on issue detail.
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        if mode:
            cur.execute("""
                SELECT
                    s.id, s.session_label, s.created_at,
                    s.input_mode, s.score, s.summary,
                    COUNT(i.id) as issue_count
                FROM sessions s
                LEFT JOIN issues i ON i.session_id = s.id
                WHERE s.input_mode = %s
                GROUP BY s.id
                ORDER BY s.created_at DESC
                LIMIT %s
            """, (mode, limit))
        else:
            cur.execute("""
                SELECT
                    s.id, s.session_label, s.created_at,
                    s.input_mode, s.score, s.summary,
                    COUNT(i.id) as issue_count
                FROM sessions s
                LEFT JOIN issues i ON i.session_id = s.id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                LIMIT %s
            """, (limit,))

        return [dict(row) for row in cur.fetchall()]
    
    finally:
        conn.close()

# ── Get stats ─────────────────────────────────────────────────

def get_stats() -> dict:
    """
    Aggregate analytics across all sessions.
    Powers the stats endpoint.
    """
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Total sessions and average score
        cur.execute("""
            SELECT
                COUNT(*) as total_sessions,
                ROUND(AVG(score)::numeric, 1) as avg_score,
                ROUND(AVG(response_time_ms)::numeric, 0) as avg_response_time_ms
            FROM sessions
        """)
        totals = dict(cur.fetchone())

        # Top issue types
        cur.execute("""
            SELECT
                type,
                COUNT(*) as count
            FROM issues
            GROUP BY type
            ORDER BY count DESC
            LIMIT 10
        """)
        top_issues_raw = cur.fetchall()

        total_sessions = totals["total_sessions"] or 1 # avoid division by zero
        top_issues = []
        for row in top_issues_raw:
            top_issues.append({
                "type": row["type"],
                "count": row["count"],
                "pct_of_sessions": round(row["count"] / total_sessions * 100, 1)
            })

        # Average score by mode
        cur.execute("""
            SELECT input_mode, ROUND(AVG(score)::numeric, 1) as avg_score
            FROM sessions
            GROUP BY input_mode
        """)
        score_by_mode = {row["input_mode"]: row["avg_score"] for row in cur.fetchall()}

        return {
            "total_sessions": totals["total_sessions"],
            "avg_score": totals["avg_score"],
            "avg_response_time_ms": totals["avg_response_time_ms"],
            "top_issues": top_issues,
            "avg_score_by_mode": score_by_mode
        }
    
    finally:
        conn.close()