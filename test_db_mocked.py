from ai import DiagnosticIssue, DiagnosticResult
from unittest.mock import patch, MagicMock
import db

def test_save_session_calls():
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch("db.get_connection", return_value=fake_conn):

        fake_issue = DiagnosticIssue(
            type="MISSING_SYSTEM_PROMPT",
            severity="high",
            explanation="test issue",
            fix="test fix"
        )
        fake_result = DiagnosticResult(
            issues=[fake_issue],
            test_output="test_output",
            score=50,
            summary="test_summary",
            suggested_prompt="test_prompt"
        )
        session_id = db.save_session(
        result=fake_result,
        improved_output=None,
        original_prompt="test prompt",
        test_input="test input",
        input_mode="prompt",
        session_label="mocked test",
        response_time_ms=100
    )

    assert fake_cursor.execute.called