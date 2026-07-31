"""DTOs for the token-usage/budget endpoint (#73)."""

from datetime import datetime

from pydantic import BaseModel, Field


class UsageResponse(BaseModel):
    """The authenticated user's token consumption and budget standing for the current UTC day."""

    input_tokens: int = Field(description="Input tokens consumed today")
    output_tokens: int = Field(description="Output tokens consumed today")
    total_tokens: int = Field(description="Input + output — the number checked against the budget")
    turns: int = Field(description="Agent turns that contributed to today's usage")
    limit: int = Field(description="Daily token budget in effect; 0 means unlimited")
    remaining: int = Field(description="Tokens left today; 0 when unlimited or exhausted")
    exceeded: bool = Field(description="Whether new turns are currently refused")
    resets_at: datetime = Field(description="When the daily window rolls over (UTC midnight)")
