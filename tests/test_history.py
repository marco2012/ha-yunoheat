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
    assert [r.billed_kwh for r in rows] == [5.0, 4.0, 7.0]
    assert [r.meter_kwh for r in rows] == [834.0, 837.0, 843.0]
    assert rows[0].read_at == datetime(2026, 11, 16, tzinfo=UTC)
    assert sum(r.cost_eur for r in rows) == pytest.approx(6.15959)


def test_usage_follows_the_meter_register_not_the_billed_quantity() -> None:
    """Yuno bills the register change plus 1 kWh, so use the register change."""
    rows = daily_rows(_events())
    # 834 -> 837 -> 843: changes of 3 and 6, billed as 4 and 7.
    # The first reading has no predecessor, so it falls back to the billed 5.
    assert [r.usage_kwh for r in rows] == [5.0, 3.0, 6.0]
    assert [r.billed_kwh for r in rows] == [5.0, 4.0, 7.0]


def test_backwards_register_falls_back_to_billed_quantity() -> None:
    """A replaced meter resets the register; do not produce negative usage."""
    events = _events()
    events[2].fields.meter_value = "10"  # 837 -> 10
    rows = daily_rows(events)
    assert rows[2].usage_kwh == 7.0
    assert rows[2].meter_kwh == 10.0


def test_missing_register_falls_back_to_billed_quantity() -> None:
    events = _events()
    events[1].fields.meter_value = None
    rows = daily_rows(events)
    assert rows[1].usage_kwh == 4.0


def test_duplicate_event_ids_ignored() -> None:
    events = _events()
    assert len(daily_rows(events + events)) == 3


def test_same_reading_time_is_summed() -> None:
    first = _events()[0]
    twin = first.model_copy(update={"event_id": first.event_id + 1000})
    rows = daily_rows([first, twin])
    assert len(rows) == 1
    assert rows[0].usage_kwh == 10.0  # no predecessor: billed quantity, summed
    assert rows[0].billed_kwh == 10.0


def test_attribution_is_previous_day_noon() -> None:
    """A midnight reading belongs to the previous day, at 12:00 UTC."""
    row = daily_rows(_events())[0]
    assert row.start == datetime(2026, 11, 15, 12, tzinfo=UTC)


def test_running_sums_continue_from_baseline() -> None:
    rows = daily_rows(_events())
    kwh, eur = running_sums(rows, base_kwh=100.0, base_eur=10.0)
    assert [p.sum for p in kwh] == [105.0, 108.0, 114.0]
    assert [p.state for p in kwh] == [834.0, 837.0, 843.0]
    assert eur[-1].sum == pytest.approx(16.15959)


def test_window_start() -> None:
    now = datetime(2026, 11, 20, tzinfo=UTC)
    assert window_start(30, now=now) == now - timedelta(days=30)
    last = datetime(2026, 11, 18, 12, tzinfo=UTC)
    assert window_start(30, now=now, last_start=last) == last - timedelta(days=3)
