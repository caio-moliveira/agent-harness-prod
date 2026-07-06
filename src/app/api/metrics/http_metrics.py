"""Prometheus metrics configuration for the application.

This module sets up and configures Prometheus metrics for monitoring the application.
"""

from prometheus_client import Counter, Histogram
from starlette_prometheus import metrics, PrometheusMiddleware

from src.app.core.metrics.success_metrics import register_success_metrics

def setup_metrics(app):
    """Set up Prometheus metrics middleware and endpoints.

    Args:
        app: FastAPI application instance
    """
    # Add Prometheus middleware
    app.add_middleware(PrometheusMiddleware)

    # Add metrics endpoint
    app.add_route("/metrics", metrics)

    # Expose the PRD success metrics (recomputed from the DB on each scrape).
    register_success_metrics()
