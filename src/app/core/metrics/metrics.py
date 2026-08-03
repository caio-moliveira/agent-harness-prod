"""Prometheus metrics configuration for the application.

This module sets up and configures Prometheus metrics for monitoring the application.
"""

from prometheus_client import Counter, Histogram, Gauge
from starlette_prometheus import metrics, PrometheusMiddleware

# Request metrics
http_requests_total = Counter("http_requests_total", "Total number of HTTP requests", ["method", "endpoint", "status"])

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "endpoint"]
)


# Database metrics
db_connections = Gauge("db_connections", "Number of active database connections")

# Custom business metrics
orders_processed = Counter("orders_processed_total", "Total number of orders processed")

llm_inference_duration_seconds = Histogram(
    "llm_inference_duration_seconds",
    "Time spent processing LLM inference",
    ["model", "agent_name"],
    buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 5.0]
)

llm_stream_duration_seconds = Histogram(
    "llm_stream_duration_seconds",
    "Time spent processing LLM stream inference",
    ["model", "agent_name"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)


tokens_in_counter = Counter(
    "llm_tokens_in", "Number of input tokens", ["agent_name"]
)

tokens_out_counter = Counter(
    "llm_tokens_out", "Number of output tokens", ["agent_name"]
)

error_counter = Counter(
    "llm_errors", "Number of errors during LLM execution", ["agent_name"]
)

tool_executions_total = Counter(
    "tool_executions_total",
    "Total tool executions",
    ["tool_name", "status"]
)

# How agent turns end: completed | call_limit | timeout | recursion_backstop | error. A rising
# call_limit/timeout share is the signal to retune MODEL_CALL_LIMIT / TURN_TIMEOUT_SECONDS;
# recursion_backstop should stay at zero (the derived recursion limit makes it unreachable).
agent_turn_terminations_total = Counter(
    "agent_turn_terminations_total",
    "Agent turn terminations by reason",
    ["agent", "reason"],
)

# A rising count means budgets are too tight for real usage (or someone is abusing the product) —
# either way an operator needs to see it, not discover it through support tickets.
user_token_budget_exhausted_total = Counter(
    "user_token_budget_exhausted_total",
    "Turns refused because the user's daily token budget was exhausted",
)

# End-to-end wall clock of ONE agent turn (what the user actually waits), as opposed to
# llm_inference_duration_seconds which times a single model call. This is the series behind the
# p95-latency SLO; buckets span a quick answer (seconds) to a long multi-deliverable turn.
agent_turn_duration_seconds = Histogram(
    "agent_turn_duration_seconds",
    "Wall-clock duration of one agent turn, by termination reason",
    ["agent", "reason"],
    buckets=(1, 2.5, 5, 10, 20, 30, 60, 120, 180, 300, 600),
)

# Automatic resumptions of a capped turn within one request (#77). Observed ONCE per request,
# including the zero, so this is the instrument that calibrates MODEL_CALL_LIMIT: a median above 0
# means the cap is too low for real work (legitimate turns routinely need a second wind), while a
# p99 pinned at MAX_AUTO_CONTINUES means requests are giving up before finishing.
agent_turn_auto_continues = Histogram(
    "agent_turn_auto_continues",
    "Automatic turn resumptions used within one request",
    ["agent"],
    buckets=(0, 1, 2, 3, 5),
)

# Guardrail metrics
guardrail_checks_total = Counter(
    "guardrail_checks_total",
    "Total guardrail checks executed",
    ["guardrail_type", "check_type", "result"],
)

guardrail_check_duration_seconds = Histogram(
    "guardrail_check_duration_seconds",
    "Duration of guardrail checks in seconds",
    ["guardrail_type", "check_type"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)

guardrail_pii_detections_total = Counter(
    "guardrail_pii_detections_total",
    "Total PII detections by type",
    ["guardrail_type", "pii_type"],
)

guardrail_requests_blocked_total = Counter(
    "guardrail_requests_blocked_total",
    "Total requests blocked or modified by guardrails",
    ["guardrail_type", "reason"],
)
