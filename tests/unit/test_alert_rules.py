"""Unit tests for the Prometheus SLO alert rules (issue #71).

Alert rules rot silently: a metric gets renamed, a rule keeps evaluating to nothing, and the alert
that should page you never fires again. These tests bind the rules to reality — every metric an
alert references must exist in the app's registry, and every alert must point at a runbook section
that exists.
"""

import re
from pathlib import Path

import pytest
import yaml
from prometheus_client import REGISTRY

# Imported for its side effect: declaring the metrics registers them, which is what makes
# REGISTRY.collect() below report the series the app really exposes.
from src.app.core.metrics import metrics  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALERTS_PATH = _REPO_ROOT / "observability" / "prometheus" / "alerts.yml"
_PROMETHEUS_PATH = _REPO_ROOT / "observability" / "prometheus" / "prometheus.yml"
_RUNBOOKS_PATH = _REPO_ROOT / "docs" / "runbooks.md"

# Series names that come from outside our registry (Prometheus/exporter built-ins).
_EXTERNAL_SERIES = {"up"}
# The series a metric family exposes, per type. This is what an alert must actually query:
# prometheus_client appends `_total` to counters (llm_errors → llm_errors_total), so an alert on
# the bare declared name silently matches nothing — a dead alert.
_SERIES_BY_TYPE = {
    "counter": ("_total", "_created"),
    "histogram": ("_bucket", "_count", "_sum", "_created"),
    "summary": ("_count", "_sum", "_created"),
    "gauge": ("",),
    "info": ("",),
    "unknown": ("",),
}


@pytest.fixture(scope="module")
def rules() -> list[dict]:
    """Every alert rule across all groups."""
    data = yaml.safe_load(_ALERTS_PATH.read_text())
    return [rule for group in data["groups"] for rule in group["rules"]]


def _exposed_series_names() -> set[str]:
    """Every series name the app's Prometheus registry actually exposes.

    Derived from the live registry (family name + type), not from the declaration, so a rule that
    queries a name Prometheus never sees fails this test instead of silently never firing.
    """
    names = set()
    for family in REGISTRY.collect():
        for suffix in _SERIES_BY_TYPE.get(family.type, ("",)):
            names.add(f"{family.name}{suffix}")
    return names


def _referenced_metrics(expr: str) -> set[str]:
    """Metric names referenced by a PromQL expression.

    Label matchers are stripped first — otherwise a label VALUE (``up{job="fastapi"}``) or a label
    NAME would be mistaken for a metric. What remains are identifiers not followed by ``(``
    (functions like ``rate``/``sum`` are), minus PromQL keywords.
    """
    cleaned = re.sub(r"\{[^}]*\}", "", expr)
    # ...and so are grouping clauses: `sum by (agent)` names labels, not metrics.
    cleaned = re.sub(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", cleaned)
    candidates = set(re.findall(r"\b([a-z_][a-z0-9_]*)\b(?!\s*\()", cleaned))
    keywords = {"by", "le", "on", "and", "or", "unless", "without", "group_left", "group_right"}
    return candidates - keywords


def test_alerts_file_is_valid_and_wired(rules):
    """The rules parse, are non-empty, and prometheus.yml actually loads them."""
    assert rules, "no alert rules defined"
    prom_config = yaml.safe_load(_PROMETHEUS_PATH.read_text())
    assert any("alerts.yml" in f for f in prom_config.get("rule_files", [])), (
        "prometheus.yml does not load alerts.yml — the rules would never evaluate"
    )


def test_every_alert_has_severity_summary_and_runbook(rules):
    """Each alert carries the metadata an on-call person needs to act."""
    for rule in rules:
        name = rule.get("alert")
        assert name, f"rule without an alert name: {rule}"
        assert rule["labels"].get("severity") in {"info", "warning", "critical"}, f"{name}: bad severity"
        assert rule["annotations"].get("summary"), f"{name}: missing summary"
        assert rule["annotations"].get("description"), f"{name}: missing description"
        assert rule["annotations"].get("runbook"), f"{name}: missing runbook link"


def test_runbook_anchors_exist(rules):
    """Every alert's runbook anchor resolves to a real section in docs/runbooks.md."""
    runbooks = _RUNBOOKS_PATH.read_text()
    headings = {h.strip().lower().replace(" ", "") for h in re.findall(r"^##\s+(.+)$", runbooks, re.M)}
    for rule in rules:
        anchor = rule["annotations"]["runbook"].split("#", 1)[1]
        assert anchor in headings, f"{rule['alert']}: no runbook section for anchor #{anchor}"


def test_alert_expressions_query_exposed_series(rules):
    """Every series an alert queries is one the app actually exposes.

    Catches both a renamed/removed metric and the subtler `_total` trap: ``Counter("llm_errors")``
    is exposed as ``llm_errors_total``, so ``rate(llm_errors[10m])`` would match nothing and the
    alert would never fire.
    """
    exposed = _exposed_series_names() | _EXTERNAL_SERIES
    for rule in rules:
        for referenced in _referenced_metrics(rule["expr"]):
            assert referenced in exposed, (
                f"{rule['alert']}: queries {referenced!r}, which the app never exposes. "
                f"Closest exposed: {sorted(n for n in exposed if n.startswith(referenced[:12]))}"
            )


def test_turn_slo_alerts_cover_every_termination_reason(rules):
    """The turn-limit taxonomy (#66) is fully covered: each non-completed reason has an alert."""
    expressions = " ".join(r["expr"] for r in rules)
    for reason in ("error", "timeout", "call_limit", "recursion_backstop"):
        assert f'reason="{reason}"' in expressions, f"no alert watches termination reason {reason!r}"


def test_recursion_backstop_alert_is_zero_tolerance(rules):
    """The backstop invariant alerts on ANY occurrence — it must never be a rate threshold."""
    rule = next(r for r in rules if r["alert"] == "RecursionBackstopFired")
    assert "> 0" in rule["expr"], "the backstop alert must fire on any occurrence"
    assert rule["for"] == "0m", "the backstop alert must not wait for a sustained window"
