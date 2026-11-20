"""Import Yuno heat history into Home Assistant's long-term statistics.

The sensors only know about readings from the day the integration was installed,
so the Energy dashboard starts empty. This module writes the account's past daily
readings as *external statistics*, which do not touch the sensors' own statistics:

- ``yunoheat:meter_<id>_usage`` in kWh (use it as a gas/heat source)
- ``yunoheat:meter_<id>_cost`` in EUR (use it as that source's cost)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import TYPE_CHECKING

from yunoheat import (
    APIConnectionError,
    APIError,
    AuthError,
    ConfigEntryAuthFailed as YunoHeatAuthFailed,
    EntityDiscoveryError,
    UsageEvent,
)

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import BACKFILL_MAX_PAGES, BACKFILL_PAGE_SIZE, DEFAULT_BACKFILL_DAYS, DOMAIN
from .history import SumPoint, daily_rows, running_sums, window_start

if TYPE_CHECKING:
    from .coordinator import YunoHeatDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Statistics older than this are never needed as a baseline lookup start.
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Summary of one history import."""

    days: int
    first: datetime | None
    last: datetime | None
    usage_kwh: float
    cost_eur: float


def statistic_ids(meter_identifier: str | None) -> tuple[str, str]:
    """Return the (usage, cost) external statistic ids for a meter."""
    slug = "".join(c if c.isalnum() else "_" for c in (meter_identifier or "heat"))
    base = f"{DOMAIN}:meter_{slug.lower()}"
    return f"{base}_usage", f"{base}_cost"


def _metadata(statistic_id: str, name: str, *, energy: bool) -> StatisticMetaData:
    """Build recorder metadata for one external statistic."""
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=EnergyConverter.UNIT_CLASS if energy else None,
        unit_of_measurement="kWh" if energy else CURRENCY_EURO,
    )


def _to_statistics(points: list[SumPoint]) -> list[StatisticData]:
    """Convert running-total points into recorder rows."""
    rows: list[StatisticData] = []
    for point in points:
        row = StatisticData(start=point.start, sum=point.sum)
        if point.state is not None:
            row["state"] = point.state
        rows.append(row)
    return rows


class YunoHeatHistoryImporter:
    """Fetches past readings and writes them as long-term statistics."""

    def __init__(
        self, hass: HomeAssistant, coordinator: YunoHeatDataUpdateCoordinator
    ) -> None:
        """Initialize the importer."""
        self._hass = hass
        self._coordinator = coordinator
        self._lock = asyncio.Lock()

    async def async_import(self, days: int | None = None) -> ImportResult:
        """Import history.

        With ``days`` it re-imports that many days from scratch. Without it, it
        continues from the last stored point (or does a full import on first use).
        """
        async with self._lock:
            return await self._async_import(days)

    async def _async_import(self, days: int | None) -> ImportResult:
        usage_id, cost_id = statistic_ids(self._coordinator.context.meter_identifier)

        last_start = None
        if days is None:
            last_start = await self._async_last_start(usage_id)

        now = dt_util.utcnow()
        date_from = window_start(
            days or DEFAULT_BACKFILL_DAYS, now=now, last_start=last_start
        )
        # Fetch one extra day so the first row has a previous register reading to
        # take its change from, then keep only the rows inside the window.
        events = await self._async_fetch_events(date_from - timedelta(days=1), now)
        rows = [r for r in daily_rows(events) if r.read_at >= date_from]
        if not rows:
            _LOGGER.debug("No daily readings between %s and %s", date_from, now)
            return ImportResult(0, None, None, 0.0, 0.0)

        base_kwh, base_eur = await self._async_baseline(
            usage_id, cost_id, rows[0].start
        )
        kwh_points, eur_points = running_sums(
            rows, base_kwh=base_kwh, base_eur=base_eur
        )

        async_add_external_statistics(
            self._hass,
            _metadata(usage_id, "Yuno Heat usage", energy=True),
            _to_statistics(kwh_points),
        )
        async_add_external_statistics(
            self._hass,
            _metadata(cost_id, "Yuno Heat cost", energy=False),
            _to_statistics(eur_points),
        )

        _LOGGER.debug(
            "Imported %d daily readings (%s to %s)",
            len(rows),
            rows[0].start,
            rows[-1].start,
        )
        return ImportResult(
            days=len(rows),
            first=rows[0].start,
            last=rows[-1].start,
            usage_kwh=sum(r.usage_kwh for r in rows),
            cost_eur=sum(r.cost_eur for r in rows),
        )

    async def _async_fetch_events(
        self, date_from: datetime, date_to: datetime
    ) -> list[UsageEvent]:
        """Page through usage events, oldest first."""
        events: list[UsageEvent] = []
        try:
            for page in range(1, BACKFILL_MAX_PAGES + 1):
                response = await self._coordinator.client.get_usage_events(
                    date_from=date_from,
                    date_to=date_to,
                    page=page,
                    count=BACKFILL_PAGE_SIZE,
                    order="asc",
                )
                events.extend(response.objects)
                if (
                    len(response.objects) < BACKFILL_PAGE_SIZE
                    or len(events) >= response.total_num
                ):
                    break
            else:
                _LOGGER.warning(
                    "Stopped after %d pages of usage events; older history may be "
                    "missing",
                    BACKFILL_MAX_PAGES,
                )
        except (YunoHeatAuthFailed, AuthError) as err:
            raise HomeAssistantError(f"Yuno authentication failed: {err}") from err
        except (APIError, APIConnectionError, EntityDiscoveryError) as err:
            raise HomeAssistantError(
                f"Could not fetch Yuno usage history: {err}"
            ) from err
        return events

    async def _async_last_start(self, usage_id: str) -> datetime | None:
        """Return the start of the newest stored usage point, if any."""
        last = await get_instance(self._hass).async_add_executor_job(
            get_last_statistics, self._hass, 1, usage_id, True, {"sum"}
        )
        if not (rows := last.get(usage_id)):
            return None
        return dt_util.utc_from_timestamp(rows[0]["start"])

    async def _async_baseline(
        self, usage_id: str, cost_id: str, first_start: datetime
    ) -> tuple[float, float]:
        """Return the stored running totals just before ``first_start``."""
        found = await get_instance(self._hass).async_add_executor_job(
            statistics_during_period,
            self._hass,
            _EPOCH,
            first_start,
            {usage_id, cost_id},
            "hour",
            None,
            {"sum"},
        )

        def last_sum(statistic_id: str) -> float:
            rows = found.get(statistic_id) or []
            return float(rows[-1]["sum"] or 0.0) if rows else 0.0

        return last_sum(usage_id), last_sum(cost_id)
