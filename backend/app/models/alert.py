"""
models/alert.py — Pydantic Schemas for Alerts and Alert Rules

Alert Rules: "notify me when auth-service produces > 100 ERRORs in 5 minutes"
Alert Events: A record that a rule was triggered at a specific time
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AlertConditionType(str, Enum):
    """Types of conditions that can trigger an alert."""
    ERROR_COUNT_EXCEEDS = "error_count_exceeds"     # Count of errors in window
    ANOMALY_RATE_EXCEEDS = "anomaly_rate_exceeds"   # % of logs that are anomalies
    KEYWORD_MATCH = "keyword_match"                  # Message contains keyword


class AlertSeverity(str, Enum):
    """Alert severity level (different from log level)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """Current state of an alert event."""
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AlertRuleCreate(BaseModel):
    """Schema for creating a new alert rule (client input)."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable name for this rule",
        examples=["High Error Rate — Auth Service"],
    )
    condition_type: AlertConditionType = Field(
        description="What kind of condition to evaluate",
        examples=["error_count_exceeds"],
    )
    threshold: float = Field(
        gt=0,
        description="Threshold value. Meaning depends on condition_type.",
        examples=[100],
    )
    window_minutes: int = Field(
        default=5,
        ge=1,
        le=1440,  # Max 24 hours
        description="Time window in minutes over which the condition is evaluated",
    )
    service_filter: Optional[str] = Field(
        default=None,
        description="Only evaluate logs from this service (null = all services)",
        examples=["auth-service"],
    )
    level_filter: Optional[str] = Field(
        default=None,
        description="Only count logs at this severity or above",
        examples=["ERROR"],
    )
    severity: AlertSeverity = Field(
        default=AlertSeverity.MEDIUM,
        description="How severe this alert is when triggered",
    )
    notify_email: Optional[str] = Field(
        default=None,
        description="Email address to notify when rule triggers",
        examples=["ops@company.com"],
    )
    is_active: bool = Field(
        default=True,
        description="Whether this rule is currently being evaluated",
    )

    @field_validator("notify_email", mode="before")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v and "@" not in v:
            raise ValueError("notify_email must be a valid email address")
        return v


class AlertRuleResponse(AlertRuleCreate):
    """Alert rule as returned by the API (includes server-generated fields)."""
    id: int
    created_at: datetime
    updated_at: datetime


class AlertEventResponse(BaseModel):
    """An alert event — a record that a rule was triggered."""
    id: int
    rule_id: int
    rule_name: str
    triggered_at: datetime
    log_count: int
    summary: str
    severity: AlertSeverity
    status: AlertStatus = AlertStatus.OPEN

    model_config = {"from_attributes": True}
