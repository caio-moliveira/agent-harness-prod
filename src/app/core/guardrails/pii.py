"""PII detection and handling utilities.

Provides deterministic PII detection using regex patterns for common PII types
including emails, credit cards, IP addresses, and more. Supports multiple
handling strategies following the LangChain guardrails pattern:
redact, mask, hash, and block.
"""

import hashlib
import re
from enum import Enum

from src.app.core.common.logging import logger


class PIIType(str, Enum):
    """Supported PII types for detection."""

    EMAIL = "email"
    CREDIT_CARD = "credit_card"
    IP = "ip"
    URL = "url"
    MAC_ADDRESS = "mac_address"
    API_KEY = "api_key"
    PHONE = "phone"
    SSN = "ssn"
    # Brazilian identifiers (LGPD): the granted folder routinely holds spreadsheets and contracts
    # with these, so they must be detectable to be redacted/blocked.
    CPF = "cpf"
    CNPJ = "cnpj"
    PHONE_BR = "phone_br"


class PIIStrategy(str, Enum):
    """Strategies for handling detected PII."""

    REDACT = "redact"
    MASK = "mask"
    HASH = "hash"
    BLOCK = "block"


PII_PATTERNS: dict[PIIType, str] = {
    PIIType.EMAIL: r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    PIIType.CREDIT_CARD: r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    PIIType.IP: r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)){3}\b",
    PIIType.URL: r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w.?&=%-]*",
    PIIType.MAC_ADDRESS: r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b",
    PIIType.API_KEY: r"\b(?:sk|pk|api[_-]?key)[_-]?[a-zA-Z0-9]{20,}\b",
    PIIType.PHONE: r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    PIIType.SSN: r"\b\d{3}-\d{2}-\d{4}\b",
    # CPF/CNPJ are matched loosely here (with or without the usual punctuation) and then confirmed
    # by their check digits below — a plain 11-digit number is far too common to redact blindly.
    PIIType.CPF: r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
    PIIType.CNPJ: r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
    # BR phone: optional +55, optional 2-digit area code in parens, 8 or 9 digits with optional
    # separator. Anchored on the country/area-code shape to avoid swallowing ordinary numbers.
    PIIType.PHONE_BR: r"(?:\+55[-.\s]?)?\(?\d{2}\)?[-.\s]?9?\d{4}[-.\s]?\d{4}\b",
}

# Types confirmed by a checksum rather than by the regex alone. Keeps precision high: without this
# every 11-digit id in a spreadsheet would be redacted as a CPF.
_CHECKSUM_VALIDATORS = {}


def _digits(value: str) -> str:
    """Only the digits of a value (strips the usual CPF/CNPJ punctuation)."""
    return re.sub(r"\D", "", value)


def validate_cpf(value: str) -> bool:
    """True when ``value`` is a CPF with valid check digits (11 digits, not all repeated)."""
    cpf = _digits(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for length in (9, 10):
        weights = range(length + 1, 1, -1)
        total = sum(int(d) * w for d, w in zip(cpf[:length], weights, strict=True))
        check = (total * 10) % 11 % 10
        if check != int(cpf[length]):
            return False
    return True


def validate_cnpj(value: str) -> bool:
    """True when ``value`` is a CNPJ with valid check digits (14 digits, not all repeated)."""
    cnpj = _digits(value)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    for length, weights in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        total = sum(int(d) * w for d, w in zip(cnpj[:length], weights, strict=True))
        remainder = total % 11
        check = 0 if remainder < 2 else 11 - remainder
        if check != int(cnpj[length]):
            return False
    return True


_CHECKSUM_VALIDATORS.update({PIIType.CPF: validate_cpf, PIIType.CNPJ: validate_cnpj})


def _matches_for(text: str, pii_type: PIIType) -> list[dict]:
    """Detector in the shape LangChain's ``PIIMiddleware`` expects (``type/value/start/end``)."""
    return [
        {"type": pii_type.value, "value": f["value"], "start": f["start"], "end": f["end"]}
        for f in detect_pii(text, pii_types=[pii_type])
    ]


def detect_cpf_matches(text: str) -> list[dict]:
    """Check-digit-validated CPF matches, for ``PIIMiddleware(detector=...)``."""
    return _matches_for(text, PIIType.CPF)


def detect_cnpj_matches(text: str) -> list[dict]:
    """Check-digit-validated CNPJ matches, for ``PIIMiddleware(detector=...)``."""
    return _matches_for(text, PIIType.CNPJ)


def detect_pii(text: str, pii_types: list[PIIType] | None = None) -> list[dict]:
    """Detect PII in text using regex patterns.

    Args:
        text: The text to scan for PII.
        pii_types: Specific PII types to check. Defaults to all types.

    Returns:
        list[dict]: List of findings with type, value, start, and end positions.
    """
    if not text:
        return []

    types_to_check = pii_types if pii_types is not None else list(PIIType)
    findings = []

    for pii_type in types_to_check:
        pattern = PII_PATTERNS.get(pii_type)
        if not pattern:
            continue
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if pii_type == PIIType.CREDIT_CARD and not _luhn_check(match.group()):
                continue
            validator = _CHECKSUM_VALIDATORS.get(pii_type)
            if validator is not None and not validator(match.group()):
                continue
            findings.append({
                "type": pii_type,
                "value": match.group(),
                "start": match.start(),
                "end": match.end(),
            })

    if findings:
        detected_types = list({f["type"].value for f in findings})
        logger.info("pii_detected", pii_types=detected_types, count=len(findings))

    return findings


def apply_pii_strategy(text: str, findings: list[dict], strategy: PIIStrategy) -> str | None:
    """Apply a PII handling strategy to the text.

    Args:
        text: The original text containing PII.
        findings: PII findings from detect_pii().
        strategy: The strategy to apply (redact, mask, hash, block).

    Returns:
        The processed text, or None if strategy is BLOCK.
    """
    if not findings:
        return text

    if strategy == PIIStrategy.BLOCK:
        return None

    sorted_findings = sorted(findings, key=lambda f: f["start"], reverse=True)
    result = text

    for finding in sorted_findings:
        original = finding["value"]
        pii_type = finding["type"]

        if strategy == PIIStrategy.REDACT:
            replacement = f"[REDACTED_{pii_type.value.upper()}]"
        elif strategy == PIIStrategy.MASK:
            replacement = _mask_value(original, pii_type)
        elif strategy == PIIStrategy.HASH:
            replacement = hashlib.sha256(original.encode()).hexdigest()[:12]
        else:
            replacement = original

        result = result[:finding["start"]] + replacement + result[finding["end"]:]

    return result


def _luhn_check(card_number: str) -> bool:
    """Validate a credit card number using the Luhn algorithm."""
    digits = re.sub(r"[\s-]", "", card_number)
    if not digits.isdigit() or len(digits) < 13:
        return False

    total = 0
    for i, digit in enumerate(reversed(digits)):
        n = int(digit)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _mask_value(value: str, pii_type: PIIType) -> str:
    """Partially mask a PII value based on its type."""
    if pii_type == PIIType.EMAIL:
        parts = value.split("@")
        if len(parts) == 2:
            return f"{parts[0][:2]}****@{parts[1]}"
    elif pii_type == PIIType.CREDIT_CARD:
        digits = re.sub(r"[\s-]", "", value)
        return f"****-****-****-{digits[-4:]}"
    elif pii_type == PIIType.PHONE:
        digits = re.sub(r"\D", "", value)
        return f"****-****-{digits[-4:]}"
    elif pii_type == PIIType.SSN:
        return f"***-**-{value[-4:]}"

    if len(value) > 4:
        return "*" * (len(value) - 4) + value[-4:]
    return "*" * len(value)
