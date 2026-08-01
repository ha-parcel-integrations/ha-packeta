# Working in this repository

Home Assistant custom integration for **Packeta** (Zásilkovna) parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
Account-less (`track_parcel` / `untrack_parcel` services). No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unverified against a real parcel) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client) | *Deliberate skill divergences* |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific notes

**Status: unverified against a real parcel.** The endpoint + its 404 "not found"
shape were verified live (2026-07-26), but the *success* payload and the
`packetStatusId` map are reconstructed from `itsvic-dev/deliveries`
(`PacketaDeliveryService.kt`). Treat `parcels.py`, `api.py`, `tests/payloads.py`
as best-effort until a real `Z`-number confirms them — the open item is flagged
`TODO(carrier)`.

### Endpoint & auth
- **Keyless, POST, code-only.** `TRACKING_API_URL` =
  `POST https://tracking.packeta.com/api/getPacketById/{code}/{locale}` (no key,
  header or postcode). `{locale}` (fixed `en`) only affects human event text;
  status comes from a numeric code, so locale isn't load-bearing. Packeta is a
  **pickup-point / locker network — 100% parcels**, no mail surface.
- **Response**: HTTP 200 `{"item": {"barcode", "packetStatusId",
  "trackingDetails": [{"text","time"}]}}` — `api.py` returns the `item` dict.
- **"Unknown code": HTTP 404** `{"error":"notFound",...}` (also a 200 carrying
  `error` instead of `item`) → `async_get_parcel` returns `None`. Other non-2xx
  raises `PacketaApiError`. Do **not** touch the merchant API (`docs.packeta.com`,
  password-protected) — different surface. Rate limiting unmeasured.

### Status map (`packetStatusId` → `ParcelStatus`)
Numeric code (as string) → canonical, in `_STATUS_MAP`. Verified from the itsvic
client: `997` → registered, `1`/`31` → in-transit, `2` → at-pickup-point, `3` →
delivered, `21` → problem (lost). Two deliberately use our more granular enum vs
itsvic's (`997`→registered not in-transit; `21`→problem not unknown). The map is
**incomplete by design** — unknown ids land as `unknown` + one-shot warning.

### Timestamps & fields
- **Per-event only.** `trackingDetails` events carry `text` (human, localised)
  and `time` (a **string, format unconfirmed**). There is no per-event status
  code, so every history entry keeps `status = None`, `raw_status = text`.
  `build_history` keeps events whose `time` doesn't parse (rather than dropping)
  until the format is known.
- **`raw_status` is the numeric `packetStatusId`** (the carrier's status token);
  the human text lives in `history`. **`delivered_at`** = newest parseable event
  `time` (no dedicated field).
- **No ETA** — `planned_from`/`planned_to` always `None`, so the next-delivery
  sensor and calendar stay empty and `packeta_parcel_delivery_time_changed` never
  fires (machinery stays for suite parity, exercised white-box).
- **`None` on purpose**: `sender`, `receiver`, `weight`, `dimensions` (not in the
  minimal payload) and `pickup_point` (a fuller response likely names the branch —
  `TODO(carrier)`).
- **Tracking-code regex stays generous** (`^[A-Z0-9]{6,30}$`) — Packeta "Z"
  numbers (`Z` + ~10 digits, often spaced); `normalize_tracking_code` strips
  spaces. A false negative is worse than a code that returns "not found".

## Options and reloads — account-less model

The options flow is one sectioned form; changes apply without a restart.
Account-less carriers (this one) use the **update-listener** model (retunes
`coordinator.update_interval` + `async_request_refresh()`). Account-based carriers
instead call `async_schedule_reload` with **no** listener (combining the two is
deprecated, error in HA 2026.12+). The user-tunable poll interval is a deliberate
HACS divergence (see CONVENTIONS.md).

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`) | no |

`parcels.py` is free of I/O and HA objects so the per-carrier part stays
unit-testable. Config: `ConfigEntry.runtime_data` (typed, no `hass.data`),
`PARALLEL_UPDATES = 0`, coordinator takes `config_entry=entry`.
`aiohttp.ClientError` is caught **per parcel** in the gather loop (one bad parcel
doesn't fail the poll) but **not** around the whole update (coordinator wraps
that). Entities: `has_entity_name` + `translation_key`, `icons.json`, translated
units, `_attr_attribution`, `_unrecorded_attributes` on anything with a parcel
list or `raw`. Over-redact diagnostics.

## Tests on Windows

`tests/conftest.py` carries two Windows-only shims (no-ops elsewhere):
`disable_socket` is neutralised (Windows event loops need AF_INET socketpairs;
the 127.0.0.1 allowlist stays) and HA's `AsyncResolver` is swapped for
`ThreadedResolver` (aiodns refuses the Proactor loop). Do not remove them
"because CI passes" — CI is Linux, development is Windows.

## Running tests

```
python -m pytest tests/ --cov=custom_components.packeta
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; `docs/api/` is gitignored (local reverse-engineering notes).
