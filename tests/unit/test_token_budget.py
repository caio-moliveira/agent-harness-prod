"""Unit tests for the daily token budget policy (#73).

The policy decisions worth pinning: disabled by default, per-account override beats the global
default (including an explicit "unlimited for this account"), and the window resets at UTC
midnight rather than on a client-controlled boundary.
"""

from datetime import UTC, datetime

import pytest

from src.app.core.usage import budget


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Default to budgets disabled unless a test opts in."""
    monkeypatch.setattr(budget.settings, "TOKEN_BUDGET_DAILY", 0, raising=False)


def test_effective_limit_falls_back_to_global(monkeypatch):
    """No per-user override → the global default applies."""
    monkeypatch.setattr(budget.settings, "TOKEN_BUDGET_DAILY", 500_000, raising=False)
    assert budget.effective_limit(None) == 500_000


def test_user_override_beats_global(monkeypatch):
    """A per-account limit wins over the global default."""
    monkeypatch.setattr(budget.settings, "TOKEN_BUDGET_DAILY", 500_000, raising=False)
    assert budget.effective_limit(1_000_000) == 1_000_000


def test_explicit_zero_means_unlimited_for_that_account(monkeypatch):
    """An explicit 0 on the user exempts that account even when a global cap is set."""
    monkeypatch.setattr(budget.settings, "TOKEN_BUDGET_DAILY", 500_000, raising=False)
    assert budget.effective_limit(0) == 0


async def test_disabled_budget_never_blocks(monkeypatch):
    """With TOKEN_BUDGET_DAILY=0 the status is never exceeded, whatever was consumed."""
    monkeypatch.setattr(budget._repo, "total_for_day", _fake_total(10_000_000))
    status = await budget.get_status(user_id=1)
    assert status.enabled is False
    assert status.exceeded is False


async def test_budget_exhausted_when_usage_reaches_limit(monkeypatch):
    """Usage at or above the limit exceeds it; remaining clamps at zero."""
    monkeypatch.setattr(budget.settings, "TOKEN_BUDGET_DAILY", 1_000, raising=False)
    monkeypatch.setattr(budget._repo, "total_for_day", _fake_total(1_000))
    status = await budget.get_status(user_id=1)
    assert status.exceeded is True
    assert status.remaining == 0


async def test_remaining_is_reported_below_the_limit(monkeypatch):
    """Under the limit, the user is told exactly how much is left."""
    monkeypatch.setattr(budget.settings, "TOKEN_BUDGET_DAILY", 1_000, raising=False)
    monkeypatch.setattr(budget._repo, "total_for_day", _fake_total(400))
    status = await budget.get_status(user_id=1)
    assert (status.exceeded, status.remaining, status.used) == (False, 600, 400)


async def test_reset_is_at_utc_midnight(monkeypatch):
    """The window boundary is UTC midnight — not a client-influenced local time."""
    monkeypatch.setattr(budget._repo, "total_for_day", _fake_total(0))
    status = await budget.get_status(user_id=1)
    assert (status.resets_at.hour, status.resets_at.minute) == (0, 0)
    assert status.resets_at > datetime.now(UTC)


async def test_record_turn_usage_ignores_empty_turns(monkeypatch):
    """A turn that reported no usage does not touch the database."""
    called = {"n": 0}

    async def _add_usage(*_args, **_kwargs):
        called["n"] += 1

    monkeypatch.setattr(budget._repo, "add_usage", _add_usage)
    await budget.record_turn_usage(user_id=1, input_tokens=0, output_tokens=0)
    assert called["n"] == 0


async def test_record_turn_usage_never_raises(monkeypatch):
    """Accounting failure must never break the turn it is billing."""

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(budget._repo, "add_usage", _boom)
    await budget.record_turn_usage(user_id=1, input_tokens=10, output_tokens=5)  # no raise


def _fake_total(value: int):
    async def _total(*_args, **_kwargs):
        return value

    return _total
