import time
from ai import analyse, AnalysisError
import db

def test_full_flow():
    """
    Run a diagnostic, save it, retrieve it, verify everything matches.
    """
    print("\nTest 1: Full flow - analyse → save → retrieve")

    prompt = "You are an assistant. Help the user."
    test_input = "Summarize this document for me."

    # Step 1 - run the AI
    print("  Running analysis...")
    start = time.time()
    result, improved_output = analyse(prompt, test_input)
    elapsed = int((time.time() - start) * 1000)
    print(f"  Analysis complete in {elapsed}ms - score: {result.score}, issues: {len(result.issues)}")

    # Step 2 - save to database
    print("  Saving to database...")
    session_id = db.save_session(
        result=result,
        improved_output=improved_output,
        original_prompt=prompt,
        test_input=test_input,
        input_mode="prompt",
        session_label="Test session - full flow",
        response_time_ms=elapsed
    )
    print(f"  Saved. session_id: {session_id}")

    # Step 3 - retrieve and verify
    print("  Retrieving from database...")
    session = db.get_session(session_id)

    assert session is not None, "Session not found after insert"
    assert session["score"] == result.score, f"Score mismatch: {session['score']} != {result.score}"
    assert len(session["issues"]) == len(result.issues), \
        f"Issue count mismatch: {len(session['issues'])} != {len(result.issues)}"
    assert session["original_prompt"] == prompt, "Prompt mismatch"

    print(f"  Retrieved: score={session['score']}, issues={len(session['issues'])}")
    print(f"  First issue type: {session['issues'][0]['type']}")
    print("  PASS: data round-trips correctly")

    return session_id


def test_list_sessions():
    """
    Verify list_sessions returns sessions and includes issue_count.
    """
    print("\nTest 2: List sessions")

    sessions = db.list_sessions(limit=5)
    assert len(sessions) > 0, "No sessions found - run test_full_flow_first"

    first = sessions[0]
    assert "issue_count" in first, "issue_count missing from list result"
    assert "score" in first, "score missing from list result"

    print(f"  Found {len(sessions)} sessions")
    print(f"  Most recent: score={first['score']}, issues={first['issue_count']}")
    print("  PASS")


def test_mode_filter():
    """
    Verify mode filtering works - m8m sessions don't appear in prompt filter.
    """
    print("\nTest 3: Mode filter")

    # Save an n8n session
    from ai import DiagnosticResult, DiagnosticIssue
    fake_result =DiagnosticResult(
        issues=[DiagnosticIssue(
            type="MISSING_SYSTEM_PROMPT",
            severity="high",
            explanation="test",
            fix="test fix"
        )],
        test_output="test output",
        score=20,
        summary="test summary",
        suggested_prompt="improved prompt"
    )
    db.save_session(
        result=fake_result,
        improved_output=None,
        original_prompt="n8n workflow json",
        test_input="test input",
        input_mode="n8n_workflow",
        session_label="Test n8n session"
    )

    # Filter by prompt mode only
    prompt_sessions = db.list_sessions(mode="prompt")
    n8n_sessions = db.list_sessions(mode="n8n_workflow")

    for s in prompt_sessions:
        assert s["input_mode"] == "prompt", f"Mode filter broken: got {s['input_mode']}"

    for s in n8n_sessions:
        assert s["input_mode"] == "n8n_workflow", f"Mode filter broken: got {s['input_mode']}"

    print(f"  prompt sessions: {len(prompt_sessions)}")
    print(f"  n8n sessions: {len(n8n_sessions)}")
    print("  PASS")



def test_stats():
    """
    Verify stats returns expected shape.
    """
    print("\nTest 4: Stats")

    stats = db.get_stats()

    assert "total_sessions" in stats
    assert "avg_score" in stats
    assert "top_issues" in stats
    assert isinstance(stats["top_issues"], list)

    print(f"  Total sessions: {stats['total_sessions']}")
    print(f"  Average score: {stats['avg_score']}")
    print(f"  Top issue types:")
    for issue in stats["top_issues"][:3]:
        print(f"    {issue['type']}: {issue['count']} ({issue['pct_of_sessions']}%)")
    print("  PASS")



def test_missing_session():
    """
    Verify get_session returns None for nonexistent ID.
    """
    print("\nTest 5: Missing session returns None")

    result = db.get_session("00000000-0000-0000-0000-000000000000")
    assert result is None, f"Expected None, got {result}"
    print("  PASS")



if __name__ == "__main__":
    tests = [
        test_full_flow,
        test_list_sessions,
        test_mode_filter,
        test_stats,
        test_missing_session,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"RESULTS: {passed}/{passed+failed} passed")