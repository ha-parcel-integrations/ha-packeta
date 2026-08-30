# Packeta Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-packeta.svg)](https://github.com/ha-parcel-integrations/ha-packeta/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Packeta](https://www.packeta.com) (Zásilkovna) parcels — the Central-European pickup-point and locker network (CZ, SK, HU, PL, RO). No account is needed: you enter the "Z" tracking number yourself, just like on the Packeta tracking page.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

> ### ⚠️ The status map is still growing
>
> The endpoint is live and keyless, and the success payload shape has been
> confirmed against real delivered parcels. Six `packetStatusId` values are
> mapped so far (one of them, `3`/delivered, seen live; the rest reconstructed
> from a maintained open-source client). Anything unmapped reports
> **`unknown`** (never a wrong status) and logs a one-shot warning with a
> ready-made issue link — please
> [report it](https://github.com/ha-parcel-integrations/ha-packeta/issues/new?template=unrecognised_status.yml)
> so the mapping can be completed.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Dynamic polling](#dynamic-polling)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Packeta parcels by tracking number — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `at_pickup_point` / `delivered` / …) and the localised event history behind it
- Summary sensors: incoming parcels, recently delivered parcels
- `packeta.track_parcel` / `packeta.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

> **Note:** Packeta's public tracking does not expose an expected delivery time. The next-delivery sensor and the Deliveries calendar are still present (for parity with the other carriers) but stay empty, and the `delivery_time_changed` event never fires.

## Requirements

- Home Assistant 2024.12 or newer
- A Packeta parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-packeta` as an **Integration**.
3. Install **Packeta** and restart Home Assistant.

### Manual

Copy `custom_components/packeta` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Packeta**. There is nothing to fill in: the hub is created immediately (Packeta tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`packeta.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

## Dynamic polling

Instead of polling Packeta at the same rate around the clock, the
integration adjusts its own cadence to what your tracked parcels are
actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM).
- **Hot (every 15 minutes)** — as soon as a tracked parcel is
  `out_for_delivery`, starting an hour before its expected delivery time (or
  immediately if no time is known).
- **Mid (every 45 minutes)** — any other in-progress parcel.
- **Fully stopped** — nothing is tracked, or every tracked parcel has been
  delivered. Adding a parcel back (via the options dialog, the
  `packeta.track_parcel` service, or a dashboard button) resumes polling
  immediately.
- A small, fixed per-hub offset is added on top, so not every Packeta hub out
  there polls at exactly the same second.

This is not user-configurable — it is the only polling behaviour this
integration has.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Packeta → ⋮ → Delete**. Nothing is stored on Packeta's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.packeta_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.packeta_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.packeta_next_delivery` | Earliest expected delivery moment across all active parcels (stays empty — Packeta exposes no ETA) |
| `sensor.packeta_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.packeta_last_successful_update` | Diagnostic: when Packeta was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. The statuses Packeta currently maps:

| Status | Meaning |
|---|---|
| `registered` | Data received / to be processed |
| `in_transit` | In the warehouse or on the way |
| `at_pickup_point` | Ready for collection at a pickup point |
| `delivered` | Handed over |
| `problem` | Reported lost |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

Packeta reports its status as a numeric code, kept on `raw_status`; the localised human text is in the parcel's history. The shared enum also defines `out_for_delivery` and `returning`; no verified Packeta code maps to those yet, so an unmapped status surfaces as `unknown` and asks you to [report it](https://github.com/ha-parcel-integrations/ha-packeta/issues/new?template=unrecognised_status.yml) — that is how the map grows.

## Events

The integration fires these on the event bus (also available as device triggers on the Packeta device):

| Event | When |
|---|---|
| `packeta_parcel_registered` | A new parcel appears in the active list |
| `packeta_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `packeta_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `packeta.track_parcel` | `tracking_code` | Start tracking a parcel |
| `packeta.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.packeta: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Packeta has no trace for it yet (their API answers a 404 *not found* until the first scan), or the number is wrong. It will pick up automatically once scanned.
- **A status logs "Unrecognised Packeta status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-packeta/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the Packeta consumer website. It is not affiliated with, endorsed by, or supported by Packeta.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
