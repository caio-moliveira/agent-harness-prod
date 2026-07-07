"""PRD success metrics (#21), computed from the persisted record and exposed to Prometheus/Grafana.

The five PRD metrics are derived from data the harness already stores:
  - **adoption** — total sessions (proxy for active usage);
  - **rework rate** — correction signals (#20) per generated artifact;
  - **traceability** — fraction of artifacts whose claims were all sourced (from the #10 event log);
  - **skill evolution** — approved skills that were refined (version > 1);
  - **artifact production time** — a histogram populated by ``generate_artifact`` (#18) via
    ``observe_artifact_seconds`` (hook; degrades to no observations until wired).

A custom collector recomputes on each Prometheus scrape so Grafana always sees current values.
"""

from typing import Iterable

from prometheus_client import Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import REGISTRY, Collector
from sqlmodel import select

from src.app.core.common.logging import logger
from src.app.core.db.database import session_scope
from src.app.core.learning.models import CorrectionSignal
from src.app.core.session.event_model import SessionEvent, SessionEventType
from src.app.core.session.session_model import Session
from src.app.core.skill.skill_model import Skill

# Artifact production time — observed by #18's generate_artifact when instrumented.
ARTIFACT_SECONDS = Histogram("harness_artifact_seconds", "Wall-clock seconds to produce an artifact")


def observe_artifact_seconds(seconds: float) -> None:
    """Record how long one artifact took to produce (called by the artifact layer)."""
    ARTIFACT_SECONDS.observe(seconds)


def compute_success_metrics() -> dict:
    """Compute the PRD success metrics from the persisted record."""
    with session_scope() as session:
        artifact_events = list(
            session.exec(
                select(SessionEvent).where(SessionEvent.event_type == SessionEventType.ARTIFACT_GENERATED)
            ).all()
        )
        artifacts = len(artifact_events)
        fully_traceable = sum(1 for e in artifact_events if (e.payload or {}).get("unsourced", 0) == 0)
        corrections = len(list(session.exec(select(CorrectionSignal)).all()))
        refined_approved = len(
            list(session.exec(select(Skill).where(Skill.status == "approved", Skill.version > 1)).all())
        )
        sessions = len(list(session.exec(select(Session)).all()))

    return {
        "artifacts_total": artifacts,
        "fully_traceable_ratio": (fully_traceable / artifacts) if artifacts else 0.0,
        "rework_rate": (corrections / artifacts) if artifacts else 0.0,
        "approved_refined_skills": refined_approved,
        "active_sessions_total": sessions,
    }


class SuccessMetricsCollector(Collector):
    """Prometheus collector that recomputes the PRD metrics on each scrape."""

    def collect(self) -> Iterable[GaugeMetricFamily]:
        """Yield one gauge family per PRD metric."""
        try:
            m = compute_success_metrics()
        except Exception:  # noqa: BLE001 - a metrics scrape must never take the app down
            logger.exception("success_metrics_compute_failed")
            return
        yield GaugeMetricFamily("harness_artifacts_total", "Artifacts generated", value=m["artifacts_total"])
        yield GaugeMetricFamily(
            "harness_fully_traceable_ratio", "Fraction of artifacts fully sourced", value=m["fully_traceable_ratio"]
        )
        yield GaugeMetricFamily("harness_rework_rate", "Correction signals per artifact", value=m["rework_rate"])
        yield GaugeMetricFamily(
            "harness_approved_refined_skills", "Approved refined skills", value=m["approved_refined_skills"]
        )
        yield GaugeMetricFamily(
            "harness_active_sessions_total", "Total sessions", value=m["active_sessions_total"]
        )


_registered = False


def register_success_metrics() -> None:
    """Register the PRD-metrics collector with the default Prometheus registry (idempotent)."""
    global _registered
    if _registered:
        return
    REGISTRY.register(SuccessMetricsCollector())
    _registered = True
    logger.info("success_metrics_registered")
