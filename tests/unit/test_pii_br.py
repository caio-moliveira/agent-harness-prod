"""Unit tests for Brazilian PII detection (issue #72).

The point of the check digits is PRECISION: the granted folder is full of numeric ids (pedidos,
matrículas, códigos), and redacting every 11-digit number as a CPF would mangle the user's own
data. These tests pin both directions — real documents are caught, look-alikes are not.
"""

import pytest

from src.app.core.guardrails.pii import (
    PIIStrategy,
    PIIType,
    apply_pii_strategy,
    detect_cnpj_matches,
    detect_cpf_matches,
    detect_pii,
    validate_cnpj,
    validate_cpf,
)

# Valid documents (check digits computed correctly), in the formats users actually paste.
VALID_CPFS = ["529.982.247-25", "52998224725", "111.444.777-35"]
VALID_CNPJS = ["11.222.333/0001-81", "11222333000181"]


@pytest.mark.parametrize("cpf", VALID_CPFS)
def test_valid_cpf_is_accepted(cpf):
    """A CPF with correct check digits validates, formatted or bare."""
    assert validate_cpf(cpf) is True


@pytest.mark.parametrize(
    "cpf",
    [
        "123.456.789-00",  # wrong check digits
        "111.111.111-11",  # repeated digits: structurally valid, never issued
        "529.982.247-24",  # off by one on the last digit
        "5299822472",  # too short
    ],
)
def test_invalid_cpf_is_rejected(cpf):
    """Look-alikes and malformed values are rejected."""
    assert validate_cpf(cpf) is False


@pytest.mark.parametrize("cnpj", VALID_CNPJS)
def test_valid_cnpj_is_accepted(cnpj):
    """A CNPJ with correct check digits validates, formatted or bare."""
    assert validate_cnpj(cnpj) is True


@pytest.mark.parametrize("cnpj", ["11.222.333/0001-00", "00.000.000/0000-00", "1122233300018"])
def test_invalid_cnpj_is_rejected(cnpj):
    """Wrong check digits, all-zeros and short values are rejected."""
    assert validate_cnpj(cnpj) is False


def test_detection_ignores_plain_numeric_ids():
    """A plain 11-digit id in the user's data is NOT redacted as a CPF (precision guard)."""
    text = "Pedido 12345678901 do cliente, nota 98765432109"
    assert detect_pii(text, pii_types=[PIIType.CPF]) == []


def test_detection_finds_documents_in_a_realistic_line():
    """Both documents are found in a line that looks like a real spreadsheet row."""
    text = "Mercado Silva;CPF 529.982.247-25;CNPJ 11.222.333/0001-81;R$ 1.500,00"
    found = {(f["type"], f["value"]) for f in detect_pii(text, pii_types=[PIIType.CPF, PIIType.CNPJ])}
    assert (PIIType.CPF, "529.982.247-25") in found
    assert (PIIType.CNPJ, "11.222.333/0001-81") in found


def test_redaction_replaces_documents_and_keeps_the_rest():
    """Redaction removes the document but leaves the surrounding record readable."""
    text = "Cliente Ana, CPF 529.982.247-25, compra de R$ 320,50"
    findings = detect_pii(text, pii_types=[PIIType.CPF])
    redacted = apply_pii_strategy(text, findings, PIIStrategy.REDACT)
    assert "529.982.247-25" not in redacted
    assert "[REDACTED_CPF]" in redacted
    assert "Cliente Ana" in redacted and "R$ 320,50" in redacted


def test_middleware_detectors_return_langchain_match_shape():
    """The PIIMiddleware detectors return type/value/start/end, as LangChain expects."""
    text = "doc 529.982.247-25 e 11.222.333/0001-81"
    cpf_matches = detect_cpf_matches(text)
    cnpj_matches = detect_cnpj_matches(text)
    assert cpf_matches and set(cpf_matches[0]) == {"type", "value", "start", "end"}
    assert cpf_matches[0]["type"] == "cpf" and cpf_matches[0]["value"] == "529.982.247-25"
    assert cnpj_matches and cnpj_matches[0]["type"] == "cnpj"
    # Offsets must point at the real span so redaction replaces exactly the document.
    assert text[cpf_matches[0]["start"] : cpf_matches[0]["end"]] == "529.982.247-25"
