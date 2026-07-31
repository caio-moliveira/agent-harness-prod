"""Deterministic input screening applied to EVERY user turn, streaming included.

The input side of the guardrail policy (see AGENTS.md "Guardrails"): content filter (banned
keywords + prompt-injection patterns) and high-risk PII (credentials, card numbers, CPF/CNPJ).
Both are deterministic and cost microseconds, so they run *before* the agent does anything — the
only point where a guardrail can actually prevent harm rather than describe it after the fact.

This lives apart from ``GuardrailMiddleware`` because that middleware only wraps the non-streaming
``agent_invoke`` path; the streamed route calls :func:`screen_user_input` directly so both paths
share one policy and one set of messages.
"""

from dataclasses import dataclass
from typing import Optional

from src.app.core.common.logging import logger
from src.app.core.guardrails.content_filter import check_content_filter
from src.app.core.guardrails.pii import PIIType, detect_pii
from src.app.core.metrics.metrics import guardrail_requests_blocked_total

BLOCKED_INPUT_MESSAGE = (
    "Não consigo processar esta solicitação. Reformule a mensagem e tente novamente."
)
BLOCKED_PII_MESSAGE = (
    "Sua mensagem contém dados sensíveis (credenciais, cartão ou documentos como CPF/CNPJ). "
    "Remova esses dados e envie novamente — posso trabalhar com os arquivos da sua pasta sem "
    "que você cole esse tipo de informação no chat."
)

# PII that must never reach the model or the logs. Emails/phones are NOT here: they are ordinary
# content in the user's own documents and get redacted downstream instead of blocking the turn.
INPUT_BLOCK_PII_TYPES = [
    PIIType.API_KEY,
    PIIType.CREDIT_CARD,
    PIIType.SSN,
    PIIType.CPF,
    PIIType.CNPJ,
]


@dataclass(frozen=True)
class ScreeningResult:
    """Outcome of screening one user message."""

    blocked: bool
    message: str = ""
    reason: str = ""


def screen_user_input(text: str, session_id: Optional[str] = None) -> ScreeningResult:
    """Screen a user message before it reaches the agent.

    Returns a blocking result (with the user-facing message and a machine reason) when the content
    filter trips or high-risk PII is present; otherwise a pass-through result.
    """
    if not text or not text.strip():
        return ScreeningResult(blocked=False)

    filter_result = check_content_filter(text)
    if filter_result.is_blocked:
        logger.info(
            "input_guardrail_blocked",
            reason=filter_result.reason,
            session_id=session_id,
        )
        guardrail_requests_blocked_total.labels(guardrail_type="content_filter", reason=filter_result.reason).inc()
        return ScreeningResult(blocked=True, message=BLOCKED_INPUT_MESSAGE, reason=filter_result.reason)

    findings = detect_pii(text, pii_types=INPUT_BLOCK_PII_TYPES)
    if findings:
        detected = sorted({f["type"].value for f in findings})
        # Never log the value itself — only which types were seen.
        logger.info("input_guardrail_pii_blocked", pii_types=detected, session_id=session_id)
        guardrail_requests_blocked_total.labels(guardrail_type="pii", reason=",".join(detected)).inc()
        return ScreeningResult(blocked=True, message=BLOCKED_PII_MESSAGE, reason=f"pii:{','.join(detected)}")

    return ScreeningResult(blocked=False)
