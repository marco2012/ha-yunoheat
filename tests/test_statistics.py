"""Tests for writing history into the recorder (real recorder, stubbed client)."""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from yunoheat import UsageEvent, UsageEventsResponse

from custom_components.yunoheat.const import BACKFILL_PAGE_SIZE
from custom_components.yunoheat.statistics import YunoHeatHistoryImporter, statistic_ids
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

FIXTURE = Path(__file__).parent / "fixtures" / "usage_events.json"
USAGE_ID, COST_ID = statistic_ids("00000000")


def _raw() -> list[dict]:
    return json.loads(FIXTURE.read_text())


class FakeClient:
    """Serves usage events the way the API does: asc order, paged."""

    def __init__(self, raw: list[dict]) -> None:
        self.raw = raw
        self.calls: list[dict] = []

    async def get_usage_events(self, *, date_from, date_to, page, count, order):
        self.calls.append({"page": page, "count": count, "order": order})
        assert order == "asc"
        inside = sorted(
            (
                e
                for e in self.raw
                if date_from.timestamp() * 1000
                <= int(e["fields"]["time_of_read"])
                <= date_to.timestamp() * 1000
            ),
            key=lambda e: int(e["fields"]["time_of_read"]),
        )
        chunk = inside[(page - 1) * count : page * count]
        return UsageEventsResponse(
            objects=[UsageEvent.model_validate(e) for e in chunk],
            total_num=len(inside),
        )


def _importer(hass: HomeAssistant, raw: list[dict]) -> tuple[YunoHeatHistoryImporter, FakeClient]:
    client = FakeClient(raw)
    coordinator = SimpleNamespace(
        client=client, context=SimpleNamespace(meter_identifier="00000000")
    )
    return YunoHeatHistoryImporter(hass, coordinator), client


async def _stats(hass: HomeAssistant) -> dict:
    await async_wait_recording_done(hass)
    return await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 1, 1, tzinfo=UTC),
        None,
        {USAGE_ID, COST_ID},
        "hour",
        None,
        {"sum", "state"},
    )


@pytest.fixture(autouse=True)
def frozen_now(freezer):
    freezer.move_to("2026-11-20 15:00:00+00:00")


async def test_full_import_writes_running_totals(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    importer, _ = _importer(hass, _raw())
    result = await importer.async_import(days=60)

    assert result.days == 3
    assert result.usage_kwh == 14.0  # register change; +70/-70 billing events ignored
    stats = await _stats(hass)
    usage = stats[USAGE_ID]
    assert [round(r["sum"], 3) for r in usage] == [5.0, 8.0, 14.0]
    assert [r["state"] for r in usage] == [834.0, 837.0, 843.0]
    # attributed to the previous day at 12:00 UTC
    assert dt_util.utc_from_timestamp(usage[0]["start"]) == datetime(
        2026, 11, 15, 12, tzinfo=UTC
    )
    assert stats[COST_ID][-1]["sum"] == pytest.approx(6.15959)


async def test_reimport_is_idempotent(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    importer, _ = _importer(hass, _raw())
    await importer.async_import(days=60)
    await _stats(hass)
    await importer.async_import(days=60)
    usage = (await _stats(hass))[USAGE_ID]
    assert [round(r["sum"], 3) for r in usage] == [5.0, 8.0, 14.0]


async def test_incremental_continues_the_same_series(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    raw = _raw()
    importer, client = _importer(hass, raw)
    await importer.async_import(days=60)
    await _stats(hass)

    # A new daily reading arrives (19 Nov, 3 kWh).
    template = next(e for e in raw if e["quantity"] == 7.0)
    new = json.loads(json.dumps(template))
    new["event_id"] += 1000
    new["quantity"] = 3.0
    new["amount_with_discount"] = 1.5
    new["fields"]["meter_value"] = "846"
    new["fields"]["time_of_read"] = str(1794960000000 + 86_400_000)
    new["fields"]["session_id"] = "00000000-1795046400000"
    client.raw.append(new)

    result = await importer.async_import()  # no days: incremental
    assert result.days >= 1
    usage = (await _stats(hass))[USAGE_ID]
    assert [round(r["sum"], 3) for r in usage] == [5.0, 8.0, 14.0, 17.0]
    assert usage[-1]["state"] == 846.0


async def test_incremental_with_nothing_new_changes_nothing(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    importer, _ = _importer(hass, _raw())
    await importer.async_import(days=60)
    await _stats(hass)
    await importer.async_import()
    usage = (await _stats(hass))[USAGE_ID]
    assert [round(r["sum"], 3) for r in usage] == [5.0, 8.0, 14.0]


async def test_pagination_collects_every_page(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    base = next(e for e in _raw() if e["quantity"] == 4.0)
    raw = []
    for i in range(BACKFILL_PAGE_SIZE + 20):  # forces a second page
        e = json.loads(json.dumps(base))
        e["event_id"] = 5_000_000 + i
        e["quantity"] = 1.0
        e["amount_with_discount"] = 0.5
        # one reading per day, ending 19 Nov 2026 00:00 UTC (before the frozen now)
        total = BACKFILL_PAGE_SIZE + 20
        ts = 1_794_960_000_000 - (total - 1 - i) * 86_400_000
        e["fields"]["time_of_read"] = str(ts)
        e["fields"]["session_id"] = f"00000000-{ts}"
        e["fields"]["meter_value"] = str(1000 + i)
        raw.append(e)

    importer, client = _importer(hass, raw)
    result = await importer.async_import(days=400)

    assert result.days == BACKFILL_PAGE_SIZE + 20
    assert [c["page"] for c in client.calls] == [1, 2]
    usage = (await _stats(hass))[USAGE_ID]
    assert usage[-1]["sum"] == BACKFILL_PAGE_SIZE + 20


async def test_start_date_replaces_history_and_restarts_the_series(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    importer, client = _importer(hass, _raw())
    await importer.async_import(days=60)
    assert [round(r["sum"], 3) for r in (await _stats(hass))[USAGE_ID]] == [
        5.0,
        8.0,
        14.0,
    ]

    # Keep consumption from 16 Nov on: the readings stamped 17 and 18 Nov.
    result = await importer.async_import(start_date=date(2026, 11, 16))

    assert result.days == 2
    stats = await _stats(hass)
    usage = stats[USAGE_ID]
    # old rows are gone, totals restart at zero, deltas use the 16 Nov register
    assert [round(r["sum"], 3) for r in usage] == [3.0, 9.0]
    assert dt_util.utc_from_timestamp(usage[0]["start"]) == datetime(
        2026, 11, 16, 12, tzinfo=UTC
    )
    assert stats[COST_ID][-1]["sum"] == pytest.approx(1.73637 + 2.44923)

    # a later incremental import must not bring the old history back
    await importer.async_import()
    usage = (await _stats(hass))[USAGE_ID]
    assert [round(r["sum"], 3) for r in usage] == [3.0, 9.0]


async def test_start_date_with_nothing_to_import_keeps_existing_history(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    importer, _ = _importer(hass, _raw())
    await importer.async_import(days=60)
    await _stats(hass)

    result = await importer.async_import(start_date=date(2027, 1, 1))

    assert result.days == 0
    assert [round(r["sum"], 3) for r in (await _stats(hass))[USAGE_ID]] == [
        5.0,
        8.0,
        14.0,
    ]
