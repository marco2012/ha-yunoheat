<img src="custom_components/yunoheat/brand/icon.png" alt="Yuno Energy Heat icon" align="right" width="96">

# Yuno Energy Heat

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="custom_components/yunoheat/brand/dark_logo.png">
  <img alt="Yuno Energy Heat" src="custom_components/yunoheat/brand/logo.png" height="56">
</picture>

A [Home Assistant](https://www.home-assistant.io/) custom integration that brings your
[Yuno Energy Heat](https://yunoenergy.ie/) communal-heating account into Home Assistant as
sensors — your outstanding balance, latest usage and cost, the last meter reading, and a
cumulative total ready for the [Energy dashboard](https://www.home-assistant.io/docs/energy/).

[![Documentation](https://img.shields.io/badge/docs-irishsmurf.github.io-363086.svg)](https://irishsmurf.github.io/ha-yunoheat/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-363086.svg)](https://hacs.xyz/)
[![Validate](https://github.com/Irishsmurf/ha-yunoheat/actions/workflows/validate.yaml/badge.svg)](https://github.com/Irishsmurf/ha-yunoheat/actions/workflows/validate.yaml)
[![Hassfest](https://github.com/Irishsmurf/ha-yunoheat/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/Irishsmurf/ha-yunoheat/actions/workflows/hassfest.yaml)
[![Lint](https://github.com/Irishsmurf/ha-yunoheat/actions/workflows/lint.yaml/badge.svg)](https://github.com/Irishsmurf/ha-yunoheat/actions/workflows/lint.yaml)

> [!NOTE]
> This is an unofficial, community-built integration. It is **not** affiliated with,
> endorsed by, or supported by Yuno Energy.

> [!IMPORTANT]
> This is a fork of [Irishsmurf/ha-yunoheat](https://github.com/Irishsmurf/ha-yunoheat) that adds
> [heat history backfill](#heat-history-backfill) and a 24-hour poll interval. See
> [What's different in this fork](#whats-different-in-this-fork).

---

## Features

Yuno Energy Heat is an Irish communal-heating provider. Your account exposes a balance, a
running stream of metered usage "events", and a meter register. This integration polls that
account every 24 hours and surfaces it as a single Home Assistant **device** with the
following sensors:

| Sensor | Entity ID | Device class | Description |
| --- | --- | --- | --- |
| Outstanding balance | `sensor.yuno_heat_outstanding_balance` | Monetary (€) | The amount currently due on your open bill. |
| Latest usage | `sensor.yuno_heat_latest_usage` | Energy (kWh) | Energy consumed in the most recent reading period. |
| Latest cost | `sensor.yuno_heat_latest_cost` | Monetary (€) | Cost of the most recent reading period (after any discount). |
| Last reading | `sensor.yuno_heat_last_reading` | Timestamp | When the most recent meter reading was taken. |
| Meter reading | `sensor.yuno_heat_meter_reading` | Energy (kWh) | The cumulative register value from the most recent reading. |
| Cumulative usage | `sensor.yuno_heat_cumulative_usage` | Energy (kWh) | Client-side running total of usage events, suitable for the Energy dashboard. |

Because each metric carries the correct device class and state class, the energy sensors
plug straight into the Energy dashboard and long-term statistics.

In addition, the integration imports your **daily heat history** as two long-term statistics
(usage in kWh and cost in €), so the Energy dashboard shows readings from before you installed
it. See [Heat history (backfill)](#heat-history-backfill).

## What's different in this fork

Compared with the original integration:

- **History backfill.** Daily usage and cost are written as external statistics, imported
  automatically on first setup (up to two years) and kept current afterwards. A
  `yunoheat.backfill_history` action re-imports on demand.
- **Usage follows the meter.** The usage statistic is the change in the meter register, not the
  billed quantity, which is that change plus 1 kWh a day.
- **Billing roll-ups are ignored.** At bill time the platform posts roll-up and reversal events
  (for example +70 kWh and -70 kWh) that restate a whole billing period. They are excluded so
  consumption is not double-counted.
- **Polls every 24 hours** instead of every 6. The meter reports one reading per day, so more
  frequent polling only repeats requests that return nothing new.
- **Tests.** A pytest suite runs against a real Home Assistant recorder with a stubbed client.
- **Requires the `recorder` integration** (declared as a dependency; it is on by default).

The sensors and their entity IDs are unchanged.

## Installation

### HACS (recommended)

1. In Home Assistant, open **HACS → Integrations → ⋮ (top right) → Custom repositories**.
2. Add `https://github.com/marco2012/ha-yunoheat` with the category **Integration**.
3. Search for **Yuno Energy Heat** and click **Download**.
4. Restart Home Assistant.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=marco2012&repository=ha-yunoheat&category=integration)

If you previously installed the original `Irishsmurf/ha-yunoheat`, remove that entry from HACS
first so only one copy is listed. Removing it from HACS does not delete your configured
integration, but you may need to add the integration again after the swap.

### Manual

1. Copy the `custom_components/yunoheat` directory into your Home Assistant
   `config/custom_components` directory.
2. Restart Home Assistant.

## Configuration

Configuration is entirely UI-driven — no YAML required.

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Yuno Energy Heat**.
3. Enter the **email** and **password** you use to sign in to the Yuno Energy portal.

The integration logs in, discovers your account, and creates the device and sensors. Your
login tokens are stored inside the config entry so they survive restarts; if your password
ever changes, Home Assistant prompts you to re-authenticate.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=yunoheat)

## Adding to the Energy dashboard

There are two ways to add Yuno heat to the Energy dashboard. Pick **one** for a given source,
so consumption is not counted twice.

### Option 1: history statistics (recommended)

This shows your past readings as well as new ones.

1. Open **Settings → Dashboards → Energy → Edit**.
2. Under **Gas consumption**, click **Add gas source** and choose **Yuno Heat usage**
   (`yunoheat:meter_<meter id>_usage`).
3. Under cost, choose **Use an entity tracking the total costs** and pick **Yuno Heat cost**
   (`yunoheat:meter_<meter id>_cost`).

Use the statistic names above, not the sensors, for this option.

### Option 2: the Cumulative usage sensor

This only counts from the day you installed the integration:

1. Open **Settings → Dashboards → Energy**.
2. Under **Gas/Heating consumption** (or **Individual devices**), add
   `sensor.yuno_heat_cumulative_usage`.
3. Optionally attach the **Latest cost** sensor to track spend.

The cumulative sensor accumulates each new usage event client-side and restores its total
across restarts, so it keeps a monotonic increasing value even though the API only returns a
rolling window of recent events.

## Heat history (backfill)

The sensors only know about readings from the day you installed the integration. To get your
past readings into the Energy dashboard, the integration also writes your daily heat usage and
cost as long-term statistics. These are separate *external statistics*, so they never touch the
sensors' own history:

| Statistic | Unit | Name |
| --- | --- | --- |
| `yunoheat:meter_<meter id>_usage` | kWh | Yuno Heat usage |
| `yunoheat:meter_<meter id>_cost` | EUR | Yuno Heat cost |

You can find them under **Developer tools → Statistics**.

**When it runs**

- **First setup:** an import runs automatically and looks back up to two years.
- **After that:** it adds new readings after every poll (every 24 hours). It re-reads the last
  few days each time, so late or corrected readings are picked up.
- **On demand:** call the `yunoheat.backfill_history` action (**Developer tools → Actions**),
  optionally with `days` (1 to 1825, default 730). It re-imports that many days from scratch
  and returns a summary: days imported, first and last day, total kWh and cost.

**How the data is read**

- **Usage is the meter's actual change.** Yuno bills each day as the change in the meter
  register **plus 1 kWh**, so the billed quantity (which the *Latest usage* sensor shows) runs
  about 1 kWh a day above the heat actually used. The usage statistic uses the register change
  instead, so it matches your meter. The cost statistic is the amount **as billed**.
- A day's reading is stamped at midnight UTC and holds the previous day's consumption, so it is
  shown on the previous day.
- The meter reports one reading a day. It usually reaches Yuno's platform a few hours after
  midnight, so yesterday's usage normally appears in the morning.
- Bill-time roll-up and reversal events are ignored, because they restate a whole billing
  period and would double-count.
- How far back you get depends on how much history Yuno's server keeps.

## Documentation

📖 **Full documentation is published at <https://irishsmurf.github.io/ha-yunoheat/>** — a
user guide, the architecture overview, and contribution instructions. The sources live in
[`docs/`](docs/) and are built as a [MkDocs](https://www.mkdocs.org/) site. To preview it
locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve
```

Then open <http://127.0.0.1:8000>.

## Contributing

Issues and pull requests are welcome. The short version:

```bash
ruff check .   # lint
pytest         # needs pytest-homeassistant-custom-component and pyyunoheat installed
```

The tests run against a real Home Assistant recorder with a stubbed Yuno client, using
anonymised usage events in `tests/fixtures/`. CI runs Ruff, [hassfest](https://developers.home-assistant.io/docs/creating_component_index/),
and HACS validation on every push. See [`docs/contributing.md`](docs/contributing.md) for the
full development and release workflow.

## License

Released under the [MIT License](LICENSE).

## Credits

- All API access goes through the [`pyyunoheat`](https://pypi.org/project/pyyunoheat/) library.
- Built following modern Home Assistant integration patterns (config entries, data update
  coordinators, entity descriptions, and translation keys).
