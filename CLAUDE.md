# Working in this repository

This is a Home Assistant custom integration for **Packeta** parcel
tracking. Distributed via HACS; not part of HA core. It is one carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite and
publishes the same canonical parcel shape, statuses and events as the others,
so the aggregator and cross-carrier dashboards can read every carrier
identically.

It was generated from **ha-carrier-template**. Everything outside the
*Carrier-specific notes* section is suite-wide; when in doubt, check the
template or a sibling repo rather than inventing something new.

## Always consult HA developer documentation

Home Assistant's integration patterns evolve continuously. **Do not rely on
memory of past patterns** — fetch the canonical page before changing a topic
area, and check the developer blog before introducing anything you only "know"
from training data.

| When you change | Fetch first |
|---|---|
| Entity properties, naming, lifecycle, attributes | https://developers.home-assistant.io/docs/core/entity/ |
| Sensor specifics (state/device classes, units) | https://developers.home-assistant.io/docs/core/entity/sensor |
| Config flow, options flow, reauth, reconfigure | https://developers.home-assistant.io/docs/config_entries_config_flow_handler |
| DataUpdateCoordinator pattern | https://developers.home-assistant.io/docs/integration_fetching_data |
| Quality scale rules | https://developers.home-assistant.io/docs/core/integration-quality-scale |
| Diagnostics | https://developers.home-assistant.io/docs/core/integration/diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |

Recent developer-facing changes worth checking before introducing a pattern
from training data:

- https://developers.home-assistant.io/blog — API deprecations, new patterns,
  breaking changes. Recent posts trump older recollection.
- https://github.com/home-assistant/architecture/discussions — design decisions
  in flight that have not reached stable docs yet.

Branding is handled by the local `custom_components/packeta/brand/`
folder (HACS reads `icon.png` from it). The official `home-assistant/brands`
repo is for HA Core integrations and does not apply here.

## Carrier-specific notes

**Status: unverified against a real parcel.** The endpoint and its 404
"not found" shape were verified live (2026-07-26), but the *success* payload
shape and the `packetStatusId` map are reconstructed from the maintained client
[`itsvic-dev/deliveries`](https://github.com/itsvic-dev/deliveries)
(`PacketaDeliveryService.kt`). Treat `parcels.py`, `api.py` and
`tests/payloads.py` as best-effort until a real `Z`-number confirms them — the
one open item is flagged with `TODO(carrier)`.

### Endpoint & auth

- **Keyless, POST, code-only.** `TRACKING_API_URL` is
  `POST https://tracking.packeta.com/api/getPacketById/{code}/{locale}` — no
  key, no header, no postcode. `{locale}` (fixed `en`, `TRACKING_LOCALE`) only
  affects the human event text; status comes from a numeric code, so locale is
  not load-bearing. Packeta is a **pickup-point / locker network — 100%
  parcels**, no mail surface.
- **Response**: HTTP 200 `{"item": {"barcode", "packetStatusId",
  "trackingDetails": [{"text", "time"}]}}`. `api.py` returns the `item` dict.
- **"Unknown code" signalling**: **HTTP 404** `{"error": "notFound", ...}` is
  the normal not-found / not-yet-scanned state → `async_get_parcel` returns
  `None`. A 200 that carries `error` instead of an `item` is treated the same.
  Any other non-2xx raises `PacketaApiError`. Rate limiting is unmeasured —
  revisit `--interval fixed` if it throttles. Do **not** touch the
  merchant API (`docs.packeta.com`, password-protected) — different surface.

### Status map (`packetStatusId` → `ParcelStatus`)

Numeric code (as a string) → canonical status, in `_STATUS_MAP`. Verified codes
from the itsvic client: `997` → registered (to-be-processed), `1`/`31` →
in-transit (in warehouse / on the way), `2` → at-pickup-point (ready for
pickup), `3` → delivered, `21` → problem (lost). Two deliberately use our more
granular enum vs itsvic's (`997`→registered not in-transit; `21`→problem not
unknown). The map is **incomplete by design** — the itsvic client already logs
unknown ids, so a real parcel will surface more, which land as `unknown` + a
one-shot warning.

### Timestamps & fields

- **Per-event only.** `trackingDetails` events carry `text` (human, localised)
  and `time` (a **string, format unconfirmed**). There is no per-event status
  code, so every history entry keeps `status = None`, `raw_status = text`.
  `build_history` keeps events whose `time` does not parse (rather than dropping
  them) until the format is known.
- **`raw_status` is the numeric `packetStatusId`** (the carrier's own status
  token); the human text lives in `history`.
- **`delivered_at`** = the newest parseable event `time` (no dedicated field).
- **No ETA.** `planned_from` / `planned_to` are always `None`, so the
  next-delivery sensor and calendar stay empty and
  `packeta_parcel_delivery_time_changed` never fires (the machinery stays for
  suite parity, exercised white-box in tests).
- **`None` on purpose:** `sender`, `receiver`, `weight`, `dimensions` (not in
  the minimal payload) and `pickup_point` (a fuller response likely names the
  branch — `TODO(carrier)` in `normalize_parcel`).

### Tracking-code format

Packeta "Z" numbers (`Z` + ~10 digits, often shown spaced). The endpoint
accepts the `Z`-prefixed form as-is, and `normalize_tracking_code` strips
spaces, so `_TRACKING_CODE_RE` stays generous (`^[A-Z0-9]{6,30}$`) rather than
tight — a false negative is worse than a code that simply returns "not found".

## The canonical parcel contract

Every carrier publishes parcels through `normalize_parcel` in `parcels.py`
with **exactly** these top-level keys, in this order:

`carrier`, `barcode`, `sender`, `receiver`, `status`, `raw_status`,
`delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`,
`pickup_point`, `url`, `weight`, `dimensions`, `history`, `raw`.

- A key the carrier does not expose is `None` — **never omitted**. Consumers
  read the key unconditionally.
- Carrier-specific extras live under `raw`. The aggregator strips `raw`, so
  anything that must survive aggregation has to be top-level.
- `status` is the canonical `ParcelStatus` enum; `raw_status` is the carrier's
  own text. Do not put the carrier's string on `status`.
- **Units**: `weight` in kilograms (float); `dimensions` in centimetres as
  `{length, width, height, text}` where `text` is `"L x W x H cm"` (integers,
  lowercase `x`). Convert before normalising if the carrier reports grams or
  millimetres.
- **Sort contract**: incoming ascending on `planned_from`, delivered descending
  on `delivered_at`, missing timestamps always last (`sort_parcels_by_ts`).
- Summary sensors expose the list under the `parcels` attribute — never
  `shipments`.

`test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards
the key set. Changing it is a suite-wide change: every carrier plus the
aggregator, together.

## Events

Fired on the HA bus by the coordinator, and exposed as no-code device triggers
via `device_trigger.py`:

| Event | When |
|---|---|
| `packeta_parcel_registered` | A new, not-yet-delivered barcode appears |
| `packeta_parcel_status_changed` | Canonical status changed (carries `old_status` / `new_status`) |
| `packeta_parcel_delivered` | A parcel reached `delivered` |
| `packeta_parcel_delivery_time_changed` | `planned_from` / `planned_to` changed — **never fires for Packeta** (no ETA); the machinery stays for suite parity |

Rules that are easy to break and must not be:

- **Events are suppressed on the very first refresh** (`_known_state is None`).
  Without this, every HA restart floods users with "registered" events for
  parcels that already existed.
- Events run over the **active + delivered set combined**, so the terminal hop
  is visible in one pass.
- The hop **to** `delivered` fires only `_parcel_delivered`, never also
  `_parcel_status_changed`. A barcode first seen already-delivered fires
  nothing.
- An ETA going `value → null` is **intentionally silent** — the carrier merely
  lost the window; not worth waking someone up for.
- Every payload is the full normalised parcel plus `device_id` (resolved once
  and cached in `_cached_device_id`). `device_id` is what lets device triggers
  filter per hub.

## Architecture rules

- **`ConfigEntry.runtime_data`** with a typed dataclass; no `hass.data`.
- **The first refresh runs in `__init__.py`, before
  `async_forward_entry_setups`.** Raising `ConfigEntryNotReady` from a
  *forwarded* platform is too late for HA to catch: it logs a warning and
  half-sets-up the entry, and users end up with some platforms and no sensors.
  Never move the first refresh into a platform.
- **`PARALLEL_UPDATES = 0`** in every platform — the coordinator already
  handles fan-out.
- The coordinator takes `config_entry=entry`, so `self.config_entry` works.
- `aiohttp.ClientError` is deliberately **not** caught around the whole update
  — `DataUpdateCoordinator` wraps it into `UpdateFailed` already. It *is*
  caught per parcel in the gather loop, so one bad parcel does not fail the
  whole poll.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove(entity_id)` when a barcode drops out of the
  coordinator data. Self-removal races with coordinator-listener cleanup and
  leaves ghost entities behind.
- **The setup-time stale-entity sweep in `sensor.py` is scoped to
  `entity_entry.domain == "sensor"`** and skips every unique_id in
  `non_parcel_unique_ids`. Without the domain check it deletes the refresh
  button; without the exclusion set it deletes the summary and diagnostic
  sensors. When you add a non-parcel sensor, add its unique_id to that set.
- **`has_entity_name = True` + `translation_key`** on every entity. Names come
  from `strings.json` and the translation files — no `_attr_name`. Icons come
  from `icons.json` — no `_attr_icon`. Units come from
  `entity.sensor.<key>.unit_of_measurement` — no
  `_attr_native_unit_of_measurement`.
- **`_unrecorded_attributes`** on anything carrying a parcel list or a `raw`
  payload, so the recorder's long-term tables stay small.
- `_attr_attribution` on every entity.
- **Unmapped statuses log a one-shot WARNING** per distinct value with a
  copy-paste `issues/new` link; users report them through the *Unrecognised
  parcel status* issue template. That is how the status map grows.
- Diagnostics redact every identifying field — they get pasted into public
  issues. Over-redact rather than under-redact.
- Network calls return raw JSON dicts; there is no DTO layer.

## Options and reloads

The options flow is **one sectioned form** (`data_entry_flow.section`), and
changes apply without a restart. Two models, do not mix them:

- **Account-less carriers** (this one) apply changes live: an update listener
  retunes `coordinator.update_interval` and calls `async_request_refresh()`, so
  added and removed parcel sensors appear immediately.
- **Account-based carriers** call `async_schedule_reload` on submit and
  register **no** update listener. Combining an update listener with a
  reload-on-update flow is deprecated today and an error in HA 2026.12+ — see
  the [config_entry_listener deprecation](https://developers.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/).

A user-tunable polling interval is a **deliberate divergence** from the HA Core
rule that polling intervals are not configurable: that rule targets core
integrations, and in a HACS parcel tracker a tunable cadence is a wanted
feature. Carriers that throttle or soft-ban unusual traffic are generated with
a fixed cadence instead and have no polling option at all.

## Module layout

| File | Contains | Carrier-specific? |
|---|---|---|
| `api.py` | HTTP client, error types | **yes** |
| `const.py` | Domain, URLs, `ParcelStatus`, option keys | **partly** (URLs) |
| `parcels.py` | Status map, `normalize_parcel`, history, sort, filters — pure functions | **partly** (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` | Fetching, caching, event firing | mostly not |
| `config_flow.py` | Setup + options flow | **partly** (code validation) |
| `sensor.py` / `button.py` / `calendar.py` | Entities | no |
| `device_trigger.py` | Device triggers | no |
| `diagnostics.py` | Redacted diagnostics | **partly** (`TO_REDACT`) |
| `services.py` | `track_parcel` / `untrack_parcel` (account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects: the part you rewrite
per carrier stays unit-testable without spinning up Home Assistant.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (both no-ops elsewhere):
pytest-homeassistant-custom-component's `disable_socket` is neutralised
(Windows event loops need AF_INET socketpairs; the connect-time 127.0.0.1
allowlist stays), and HA's hardcoded aiohttp `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development happens on Windows.

## Docs and README

- The README stays **lean and installer-first** (suite house style): no
  per-entity `## Buttons` / `## Calendar` sections; the device-trigger option
  is one sentence folded into **Events**. This file documents everything else.
- **A code change updates the docs in the same commit** where behaviour
  changes — README, this file, and `docs/`.
- `docs/api/` is gitignored: reverse-engineering notes stay local.

## Workflow, commits, releases

See `ha-parcel-integrations/.github/CONVENTIONS.md` for the shared rules
(single-line commit messages, no `v` prefix on tags, semver, maintainer-only
merges, user-facing release notes). Not repeated here.

## Running tests

```
python -m pytest tests/ --cov=custom_components.packeta
```

Coverage must stay **above 95%** (the silver `test-coverage` rule). Run before
committing.
