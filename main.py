import os
import time
import json
import httpx
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Security, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import Literal
from webhook_models import WebhookPayload
from db import get_connection

from ai import analyse, AnalysisError
import db

load_dotenv()

# ── API key dependency ───────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-KEY")
API_KEY = os.getenv("API_KEY")

def verify_api_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# ── App ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("AI Diagnostic Tool starting up...")
    yield
    print("Shutting down...")

app = FastAPI(
    title="AI Diagnostic Tool ",
    description="Diagnose why your LLM prompt or n8n workflow is producing bad output.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ─────────────────────────────────────────────────

class DiagnosticRequest(BaseModel):
    original_prompt: str = Field(
        min_length=10,
        max_length=8000,
        description="The prompt or n8n workflow JSON to diagnose"
    )
    test_input: str = Field(
        min_length=3,
        max_length=4000,
        description="Sample input to run against the prompt"
    )
    input_mode: Literal["prompt", "n8n_workflow"] = Field(
        default="prompt",
        description="Whether this is a raw prompt or n8n workflow JSON"
    )
    model_used: str = Field(
        default="claude-sonnet-4-6",
        description="The model to use for analysis"
    )
    session_label: str | None = Field(
        default=None,
        max_length=200,
        description="Optional label for this diagnostic session"
    )

# ── Response models ─────────────────────────────────────────────────

class IssueResponse(BaseModel):
    type: str
    severity: str
    explanation: str
    fix: str

class DiagnosticResponse(BaseModel):
    session_id: str
    score: int
    summary: str
    issues: list[IssueResponse]
    original_output: str
    suggested_prompt: str
    improved_output: str | None = None
    response_time_ms: int
    saved: bool  # False if DB write failed - client knows session wasn't persisted

class SessionListItem(BaseModel):
    session_id: str
    session_label: str | None
    created_at: str
    input_mode: str
    score: int
    summary: str
    issue_count: int

class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]

class StatsResponse(BaseModel):
    total_sessions: int
    avg_score: float | None
    avg_response_time_ms: float | None
    top_issues: list[dict]
    avg_score_by_mode: dict


# ── POST/diagnostics ─────────────────────────────────────────────────

@app.post("/diagnostics", response_model=DiagnosticResponse, dependencies=[Depends(verify_api_key)])
def run_diagnostic(request: DiagnosticRequest):
    """
    Run a full diagnostic on a prompt or n8n workflow.
    Two Claude calls: diagnosis + validation of the improved prompt.
    """
    start = time.time()

    # ── Step 1: Run the AI pipeline ────────────────────────────────
    try:
        result, improved_output = analyse(
            original_prompt=request.original_prompt,
            test_input=request.test_input,
            mode=request.input_mode,
            model=request.model_used
        )
    except AnalysisError as e:
        print(f"Analysis failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Diagnostic service temporarily unavailable. Please try again."
        )
    
    elapsed_ms = int((time.time() - start) * 1000)

    # ── Step 2: Save to database ───────────────────────────────────
    saved = True
    session_id = None

    try:
        session_id = db.save_session(
            result=result,
            improved_output=improved_output,
            original_prompt=request.original_prompt,
            test_input=request.test_input,
            input_mode=request.input_mode,
            model_used=request.model_used,
            session_label=request.session_label,
            response_time_ms=elapsed_ms
        )
    except Exception as e:
        print(f"DB save failed (non-fatal): {e}")
        saved = False
        # Generate a temporary ID so the response is still usable
        import uuid
        session_id = str(uuid.uuid4())

     # ── Step 3: Return result ──────────────────────────────────────
    return DiagnosticResponse(
        session_id=session_id,
        score=result.score,
        summary=result.summary,
        issues=[IssueResponse(**issue.model_dump()) for issue in result.issues],
        original_output=result.test_output,
        suggested_prompt=result.suggested_prompt,
        improved_output=improved_output,
        response_time_ms=elapsed_ms,
        saved=saved
    )


# ── GET/diagnostics/stats ─────────────────────────────────────────────────

@app.get("/diagnostics/stats", response_model=StatsResponse, dependencies=[Depends(verify_api_key)])
def get_stats():
    """
    Aggregate analytics across all sessions.
    """
    try:
        stats = db.get_stats()
        return StatsResponse(**stats)
    except Exception as e:
        print(f"Stats query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve stats.")
    

# ── GET/diagnostics/{session_id} ──────────────────────────────────────────

@app.get("/diagnostics/{session_id}", dependencies=[Depends(verify_api_key)])
def get_session(session_id: str):
    """
    Retrieve a complete past session with all issues.
    """
    try:
        session = db.get_session(session_id)
    except Exception as e:
        print(f"DB read failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve session.")
    
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id} not found."
        )
    
    # Convert datetime to string for JSON serialisation
    if session.get("created_at"):
        session["created_at"] = session["created_at"].isoformat()

    return session


# ── GET/diagnostics ─────────────────────────────────────────────────

@app.get("/diagnostics", response_model=SessionListResponse, dependencies=[Depends(verify_api_key)])
def list_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    mode: str | None = Query(default=None)
):
    """
    List past diagnostic sessions.
    """
    # Validate mode if provided
    if mode and mode not in ("prompt", "n8n_workflow"):
        raise HTTPException(
            status_code=400,
            detail="mode must be 'prompt' or 'n8n_workflow'"
        )
    
    try:
        sessions = db.list_sessions(limit=limit, mode=mode)
    except Exception as e:
        print(f"DB list failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve sessions.")
    
    # Convert datetimes to strings
    for s in sessions:
        if s.get("created_at"):
            s["created_at"] = s["created_at"].isoformat()
        s["session_id"] = s.pop("id")  # rename id → session_id for API consistency

    return SessionListResponse(sessions=sessions)

# ── Health check ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "AI Integration Diagnostic Tool"
    }


# ── Webhook ───────────────────────────────────────────────────

N8N_WEBHOOK_URL = "http://localhost:5678/webhook/fastapi-events" 

@app.post("/webhook")
async def receive_webhook(payload: WebhookPayload):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO webhook_events (source, event_type, payload, status)
            VALUES (%s, %s, %s, %s)
            RETURNING id, received_at
            """,
            (
                payload.source,
                payload.event_type,
                json.dumps(payload.model_dump()),
                "received"
            )
        )
        row = cur.fetchone()
        event_id = row[0]
        received_at = row[1]
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    n8n_triggered = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            n8n_response = await client.post(N8N_WEBHOOK_URL, json=payload.model_dump())
            n8n_triggered = n8n_response.status_code == 200
        print(f"n8n response status: {n8n_response.status_code}")
    except Exception as e:
        print(f"n8n forward failed: {e}")

    return {
        "status": "accepted",
        "event_id": event_id,
        "received_at": received_at.isoformat(),
        "n8n_triggered": n8n_triggered
    }