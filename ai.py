import os
import json
from dotenv import load_dotenv
from anthropic import Anthropic
from pydantic import BaseModel, ValidationError
from typing import Literal

load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT_BASE = """You are an expert LLM integration engineer. Your job is to \
analyse prompts and AI configurations for common failure patterns that cause \
unreliable, unsafe, or low-quality outputs in production systems.
        
You will receive a prompt to diagnose and a test input to run against.

Analyse the prompt against these failure patterns:
- MISSING_SYSTEM_PROMPT: No system prompt defined, or system prompt is empty
- AMBIGUOUS_INSTRUCTION: The task instruction is vague or open to multiple interpretations
- NO_OUTPUT_FORMAT: No output format specified - the model will return unpredictable structures
- TASK_TOO_BROAD: The prompt asks for too much in a single call
- NO_EXAMPLES: Few-shot examples would significantly improve reliability but none are provided
- CONFLICTING_INSTRUCTIONS: The prompt contains instructions that contradict each other
- NO_ERROR_HANDLING: No instruction for what to do when the model is uncertain
- CONTEXT_OVERLOAD: The prompt includes excessive context that buries the actual instruction
- PROMPT_INJECTION_RISK: User input is interpolated without sanitisation or instruction barriers
- TEMPERATURE_MISMATCH: The task requires deterministic output but no format constraints enforce it

For each issue you identify:
- Explain specifically why it is a problem in THIS prompt, not generically
- Provide a specific fix - rewrite the relevant section or give the exact instruction to add

After analysis, simulate running the prompt against the test input and show \
what output it would likely produce given its current issues.
    
Then rewrite the entire prompt with all issues fixed.

You must respond with valid JSON only. No prose before or after. \
No markdown code fences. The JSON must match this exact schema:

{
    "issues": [
        {
            "type": "MISSING_SYSTEM_PROMPT",
            "severity": "high",
            "explanation": "specific exmplanation for this prompt",
            "fix": "exact rewrite or instruction to add"
        }
    ],
    "test_output": "what this prompt would likely return for the test input, showing the failure",
    "score": 34,
    "summary": "one sentence verdict — maximum 120 characters, plain English, no technical jargon",
    "suggested_prompt": "your complete rewritten version of the prompt with all issues fixed"
}

Severity: high = will cause failures in production, medium = degrades quality, \
low = best practice violation.
Only include issues that actually apply. Do not fabricate issues that aren't present.
Score 0-100 where 100 is production-ready. Derive the score mathematically: start at 100 and deduct based on severity of issues found (high severity: deduct 15-25 points each, medium: deduct 5-10 points each, low: deduct 1-3 points each). The score must be consistent with the issues list — a prompt with 3 high severity issues cannot score above 55. Avoid anchoring to round numbers; a score of 73 is more honest than 70."""

SYSTEM_PROMPT_MODE_SUFFIX = {
    "prompt": """
suggested_prompt must be a complete, usable prompt - not a description of changes.""",

    "n8n_workflow": """
The input is an n8n workflow, given to you as JSON. It contains one or more nodes; \
the prompt you must diagnose is the text inside an AI/agent node's `parameters.prompt` \
field (e.g. a `@n8n/n8n-nodes-langchain.agent` node) - not the JSON structure itself. \
Evaluate that embedded prompt text against the failure patterns above, taking into \
account how it's used in the workflow (for example, values interpolated via \
`{{ $json.fieldName }}` expressions are equivalent to untrusted user input and should \
be assessed for PROMPT_INJECTION_RISK just like any other user-supplied text).

test_output must simulate what the AI/agent node would output for the given test input, \
given the current issues in its prompt - not a description of the workflow.

suggested_prompt must be ONLY the corrected prompt text meant to replace the value of \
`parameters.prompt` in that node - a complete, usable prompt on its own, not the \
surrounding JSON structure and not a description of changes. Someone using this tool \
will copy suggested_prompt directly into their n8n node.""",
}


def build_system_prompt(mode: str) -> str:
    """
    Build the system prompt for a given input mode. Falls back to the
    plain-prompt suffix for any unrecognised mode.
    """
    suffix = SYSTEM_PROMPT_MODE_SUFFIX.get(mode, SYSTEM_PROMPT_MODE_SUFFIX["prompt"])
    return SYSTEM_PROMPT_BASE + "\n" + suffix


class DiagnosticIssue(BaseModel):
    type: str
    severity: Literal["high", "medium", "low"]
    explanation: str
    fix: str
    

class DiagnosticResult(BaseModel):
    issues: list[DiagnosticIssue]
    test_output: str
    score:int
    summary: str
    suggested_prompt: str


class AnalysisError(Exception):
    """Raised when Claude fails to return valid structured output after retries."""
    pass

# ── Job 1 - Build the messages ─────────────────────────────────────────────────

def build_messages(
        original_prompt: str,
        test_input: str,
        mode: str = "prompt"
) -> list[dict]:
    """
    Construct the messages list for the Claude API call.
    User input always lands in the user turn - never in the system prompt.
    """
    if mode == "n8n_workflow":
        user_content = f"""N8N WORKFLOW JSON TO DIAGNOSE:
---
{original_prompt}
---

TEST INPUT (what the workflow receives):
{test_input}

Analyse this n8n AI node configuration and return your diagnosis as JSON."""
    else:
        user_content = f"""PROMPT TO DIAGNOSE:
---
{original_prompt}
---

TEST INPUT (what a user would send to this prompt):
{test_input}

Analyse this prompt and return your diagnosis as JSON."""
        
    return [
        {"role": "user", "content": user_content}
    ]

# ── Job 2 - Call Claude ───────────────────────────────────────────────────────

def call_claude(messages: list[dict], system: str, model: str = MODEL) -> str:
    """
    Send messages to Claude and return the raw text response.
    Raises AnalysisError if the API call fails.
    """
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=system,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        raise AnalysisError(f"Claude API call failed: {e}") from e
    

# ── Job 3 - Parse and validate ────────────────────────────────────────────────

def parse_response(raw: str) -> DiagnosticResult:
    """
    Parse Claude's raw text response into a validated DiagnosticResult.
    Raises ValueError if the JSON is malformed or doesn't match the schema.
    """
    # Strip markdown code fences if Claude adds them despite instructions
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1])

    data = json.loads(text)             # raises json.JSONDecodeError if malformed
    return DiagnosticResult(**data)     # raises ValidationError if schema mismatch


# ── Main function - wires all thee jobs together ───────────────────────────────

def analyse(
        original_prompt: str,
        test_input: str,
        mode: str = "prompt",
        model: str = MODEL
) -> tuple[DiagnosticResult, str | None]:
    """
    Full diagnostic pipeline.
    Retries once if Claude returns malformed output.
    Raises AnalysisError if both attempts fail.
    Returns (DiagnosticResult, improved_output).
    improved_output is None if the validation call fails.
    """
    messages = build_messages(original_prompt, test_input, mode)
    system = build_system_prompt(mode)

    for attempt in range(2):
        raw = call_claude(messages, system, model)

        try:
            result = parse_response(raw)

            # Second call - run the improved prompt against the same test input
            improved_output = run_improved_prompt(result.suggested_prompt, test_input, model)

            return result, improved_output
        
        except (json.JSONDecodeError,ValidationError, KeyError) as e:
            if attempt == 0:
                # First failure: add a correction message and retry
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            f"Your response was not valid JSON matching the required schema. "
                            f"Error: {e}. "
                            f"Return only the JSON object, nothing else. "
                            f"No markdown, no explanation."
                        )
                    }
                ]
            else:
                raise AnalysisError(
                    f"Claude failed to return valid JSON after 2 attempts. "
                    f"Last error: {e}. Last response: {raw[:200]}"
                ) from e
            
    # This line is unreachable but satisfies type checkers
    raise AnalysisError("Unexpected exit from retry loop")


def run_improved_prompt(
        suggested_prompt: str,
        test_input: str,
        model: str = MODEL
) -> str | None:
    """
    Run the suggested (improved) prompt against the same test input.
    Returns the output string, or None if the call fails.
    This is the second Claude call - failure here does NOT fail the whole session.
    """
    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=suggested_prompt,
            messages=[{"role": "user", "content": test_input}]
        )
        return response.content[0].text
    except Exception as e:
        print(f"  Validation call failed (non-fatal): {e}")
        return None