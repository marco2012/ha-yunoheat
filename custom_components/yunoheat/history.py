"""Turn Yuno usage events into daily history rows (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re

from yunoheat import UsageEvent

# Daily meter events carry a session_id of "<meter>-<time_of_read_ms>". At bill
# time the platform also posts roll-up events ("...-m-<ms>") and reversals
# ("...-r-<ms>") that restate a whole billing period (for example +70 kWh and
# -70 kWh), so counting them would double-count consumption.
_BILLING_SESSION = re.compile(r"-[a-z]-\d+$")

# A reading is stamped at midnight UTC and holds the consumption of the day
# before. Attribute it to the middle of that day so that the Energy dashboard
# shows it on the right local day in both winter and summer time.
_ATTRIBUTION_OFFSET = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class DailyRow:
    """Consumption for one day, taken from the daily meter reading events."""

    read_at: datetime  # when the meter was read (UTC)
    usage_kwh: float  # kWh the meter register advanced in the period ending at read_at
    billed_kwh: float  # kWh Yuno billed for that period (register change + 1)
    cost_eur: float  # cost of that consumption, as billed
    meter_kwh: float | None  # cumulative meter register at read_at

    @property
    def start(self) -> datetime:
        """Return the hour the consumption is attributed to (UTC, on the hour)."""
        return (self.read_at - _ATTRIBUTION_OFFSET).replace(
            minute=0, second=0, microsecond=0
        )


def is_daily_event(event: UsageEvent) -> bool:
    """Return True for a plain daily reading, False for billing roll-ups."""
    fields = event.fields
    if fields is None or fields.time_of_read is None:
        return False
    if fields.session_id and _BILLING_SESSION.search(fields.session_id):
        return False
    return event.quantity is not None


def daily_rows(events: Iterable[UsageEvent]) -> list[DailyRow]:
    """Collapse usage events into one row per meter reading, oldest first.

    Duplicate events (same ``event_id``) are ignored. Several daily events for
    the same reading time are summed.

    Yuno bills ``quantity`` = the change in the meter register plus 1 kWh per
    reading, so ``quantity`` overstates the heat actually used. ``usage_kwh`` is
    therefore the register change since the previous reading. It falls back to
    the billed quantity for the first reading, when the register is missing, or
    when it went backwards (a replaced meter). Cost is always the billed amount.
    """
    seen: set[int] = set()
    usage: dict[datetime, float] = {}
    cost: dict[datetime, float] = {}
    meter: dict[datetime, float | None] = {}

    for event in events:
        if event.event_id in seen or not is_daily_event(event) or event.fields is None:
            continue
        read_at = event.fields.time_of_read_dt
        if read_at is None:
            continue
        seen.add(event.event_id)
        usage[read_at] = usage.get(read_at, 0.0) + (event.quantity or 0.0)
        cost[read_at] = cost.get(read_at, 0.0) + (event.amount_with_discount or 0.0)
        reading = event.fields.meter_value_kwh
        if reading is not None:
            previous = meter.get(read_at)
            meter[read_at] = reading if previous is None else max(previous, reading)
        else:
            meter.setdefault(read_at, None)

    rows: list[DailyRow] = []
    previous_meter: float | None = None
    for read_at in sorted(usage):
        reading = meter[read_at]
        used = usage[read_at]  # billed quantity, the fallback
        if reading is not None and previous_meter is not None:
            delta = reading - previous_meter
            if delta >= 0:
                used = delta
        rows.append(
            DailyRow(
                read_at=read_at,
                usage_kwh=used,
                billed_kwh=usage[read_at],
                cost_eur=cost[read_at],
                meter_kwh=reading,
            )
        )
        if reading is not None:
            previous_meter = reading
    return rows


@dataclass(frozen=True, slots=True)
class SumPoint:
    """One statistics point: when, its running total, and the raw state."""

    start: datetime
    sum: float
    state: float | None


def running_sums(
    rows: Iterable[DailyRow], *, base_kwh: float = 0.0, base_eur: float = 0.0
) -> tuple[list[SumPoint], list[SumPoint]]:
    """Build running-total points for usage (kWh) and cost (EUR).

    ``base_kwh`` and ``base_eur`` are the totals already stored before the first
    row, so that an incremental import continues the same series.
    """
    kwh_points: list[SumPoint] = []
    eur_points: list[SumPoint] = []
    total_kwh = base_kwh
    total_eur = base_eur
    for row in rows:
        total_kwh += row.usage_kwh
        total_eur += row.cost_eur
        kwh_points.append(SumPoint(row.start, total_kwh, row.meter_kwh))
        eur_points.append(SumPoint(row.start, total_eur, total_eur))
    return kwh_points, eur_points


def window_start(
    days: int, *, now: datetime | None = None, last_start: datetime | None = None
) -> datetime:
    """Return where to start fetching events.

    A full import looks back ``days``. An incremental one restarts a few days
    before the last stored point to pick up late or corrected readings.
    """
    now = now or datetime.now(UTC)
    if last_start is not None:
        return last_start - timedelta(days=3)
    return now - timedelta(days=days)
