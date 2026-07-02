from pydantic import BaseModel, field_validator
from typing import Any, Optional
import re

class WebhookPayload(BaseModel):
    source: str
    event_type: str
    data: dict[str, Any]
    metadata: Optional[dict[str, Any]] = None

    @field_validator("source")
    @classmethod
    def source_must_be_valid(cls, v):
        allowed = ["n8n", "postman", "stripe", "github", "custom"]
        if v not in allowed:
            raise ValueError(f"source must be one of {allowed}")
        return v
    
    @field_validator("event_type")
    @classmethod
    def event_type_format(cls, v):
        if not re.match(r'^[a-z][a-z0-9_]*$', v):
            raise ValueError("event_type must be snake_case (e.g. 'user_created')")
        return v