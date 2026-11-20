"""Tests for the pure history logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest
from yunoheat import UsageEvent

from custom_components.yunoheat.history import (
    daily_rows,
    is_daily_event,
    running_sums,
    window_start,
)

FIXTURE = Path(__file__).parent / "fixtures" / "usage_events.json"


def _events() -> list[UsageEvent]:
    return [UsageEvent.model_validate(raw) for raw in json.loads(FIXTURE.read_text())]


def test_billing_rollups_are_excluded() -> None:
    """The +70/-70 bill-time events must not count as daily consumption."""
    kept = [e for e in _events() if is_daily_event(e)]
    assert [e.quantity for e in kept] == [5.0, 4.0, 7.0]


def test_daily_rows_oldest_first_and_summed() -> None:
    rows = daily_rows(_events())
    assert [r.usage_kwh for r in rows] == [5.0, 4.0, 7.0]
    assert [r.meter_kwh for r in rows] == [834.0, 837.0, 843.0]
    assert rows[0].read_at == datetime(2026, 11, 16, tzinfo=UTC)
    assert sum(r.cost_eur for r in rows) == pytest.approx(6.15959)


def test_duplicate_event_ids_ignored() -> None:
    events = _events()
    assert len(daily_rows(events + events)) == 3


def test_same_reading_time_is_summed() -> None:
    first = _events()[0]
    twin = first.model_copy(update={"event_id": first.event_id + 1000})
    rows = daily_rows([first, twin])
    assert len(rows) == 1
    assert rows[0].usage_kwh == 10.0


def test_attribution_is_previous_day_noon() -> None:
    """A midnight reading belongs to the previous day, at 12:00 UTC."""
    row = daily_rows(_events())[0]
    assert row.start == datetime(2026, 11, 15, 12, tzinfo=UTC)


def test_running_sums_continue_from_baseline() -> None:
    rows = daily_rows(_events())
    kwh, eur = running_sums(rows, base_kwh=100.0, base_eur=10.0)
    assert [p.sum for p in kwh] == [105.0, 109.0, 116.0]
    assert [p.state for p in kwh] == [834.0, 837.0, 843.0]
    assert eur[-1].sum == pytest.approx(16.15959)


def test_window_start() -> None:
    now = datetime(2026, 11, 20, tzinfo=UTC)
    assert window_start(30, now=now) == now - timedelta(days=30)
    last = datetime(2026, 11, 18, 12, tzinfo=UTC)
    assert window_start(30, now=now, last_start=last) == last - timedelta(days=3)
