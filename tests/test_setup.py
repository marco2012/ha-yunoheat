"""End-to-end: set up the integration, import history through the service."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from yunoheat import EntityContext, UsageEvent, UsageEventsResponse
from yunoheat.models import OpenBillDue

from custom_components.yunoheat.const import DOMAIN
from custom_components.yunoheat.statistics import statistic_ids
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

FIXTURE = Path(__file__).parent / "fixtures" / "usage_events.json"
USAGE_ID, COST_ID = statistic_ids("00000000")


class FakeClient:
    """Stands in for YunoHeatClient."""

    def __init__(self) -> None:
        self.raw = json.loads(FIXTURE.read_text())

    async def get_context(self) -> EntityContext:
        return EntityContext(
            person_customer_id=1,
            person_customer_code="code",
            payment_group_id=2,
            property_customer_id=3,
            meter_identifier="00000000",
        )

    async def get_open_bill_due(self) -> OpenBillDue:
        return OpenBillDue(open_bill_due=150.0)

    async def get_usage_events(self, *, date_from, date_to, page=1, count=50, order="desc"):
        lo, hi = date_from.timestamp() * 1000, date_to.timestamp() * 1000
        inside = [
            e for e in self.raw if lo <= int(e["fields"]["time_of_read"]) <= hi
        ]
        inside.sort(key=lambda e: int(e["fields"]["time_of_read"]), reverse=order == "desc")
        chunk = inside[(page - 1) * count : page * count]
        return UsageEventsResponse(
            objects=[UsageEvent.model_validate(e) for e in chunk],
            total_num=len(inside),
        )


@pytest.fixture(autouse=True)
def frozen_now(freezer):
    freezer.move_to("2026-11-20 15:00:00+00:00")


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="person-code",
        title="user@example.com",
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.yunoheat.YunoHeatClient.login",
        return_value=FakeClient(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        # the history import runs as a background task
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _usage(hass: HomeAssistant) -> list[float]:
    await async_wait_recording_done(hass)
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        {USAGE_ID},
        "hour",
        None,
        {"sum"},
    )
    return [round(r["sum"], 3) for r in stats.get(USAGE_ID, [])]


async def test_history_is_imported_automatically_on_setup(
    recorder_mock: Recorder, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert await _usage(hass) == [5.0, 9.0, 16.0]


async def test_backfill_service_returns_a_summary(
    recorder_mock: Recorder, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    await _setup(hass)
    response = await hass.services.async_call(
        DOMAIN, "backfill_history", {"days": 30}, blocking=True, return_response=True
    )
    summary = response["user@example.com"]
    assert summary["days_imported"] == 3
    assert summary["usage_kwh"] == 16.0
    assert summary["first"].startswith("2026-11-15T12:00")
    assert await _usage(hass) == [5.0, 9.0, 16.0]


async def test_service_rejects_out_of_range_days(
    recorder_mock: Recorder, enable_custom_integrations: None, hass: HomeAssistant
) -> None:
    await _setup(hass)
    with pytest.raises(Exception, match="days"):
        await hass.services.async_call(
            DOMAIN, "backfill_history", {"days": 0}, blocking=True, return_response=True
        )
