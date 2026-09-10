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
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| ship anything while below 1.0.0 (unverified against a real parcel) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client) | *Deliberate skill divergences* |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**API mechanics live in `carrier-research/packeta/api/` (private research repo)** — the keyless
`getPacketById` POST endpoint, the 404 "not found" signalling, the
`packetStatusId` vocabulary and the `trackingDetails` payload. Do not duplicate
them here.

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
Where this repo diverges from it, that is recorded below under
*Divergences from the scaffold*.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).

## Carrier-specific decisions (integration only)

Packeta is a **pickup-point / locker network — 100% parcels**, no mail surface.
It also acts as a **cross-border reshipper**, handing parcels to a downstream
final-mile carrier (DHL Parcel NL, ACS Courier, FAN Courier RO, Orlen Paczka
all seen) — `trackingDetails` events then name that carrier and embed an
`<a href>` to its own tracking page, while Packeta's own `packetStatusId`/
top-level `status` keep tracking the parcel through the handover.
**Status: confirmed against real delivered parcels (2026-08-19).** The success
payload shape, the `packetStatusId: "3"` (delivered) mapping, the naive
space-separated `trackingDetails[].time` shape, `sender` and `branchAddress`
are all now seen live — see `carrier-research/packeta/api/`. The other five
`packetStatusId` values are still reconstructed from a third-party client; the
map stays incomplete by design (unknown ids → `unknown` + one-shot warning).

- **Do not touch the merchant API** (`docs.packeta.com`, password-protected) — a
  different surface.
- **No ETA** — `planned_from`/`planned_to` always `None`, so the next-delivery
  sensor and calendar stay inert and `packeta_parcel_delivery_time_changed` never
  fires (machinery kept for parity, exercised white-box). There is no per-event
  status code — history sentences are matched against `_EVENT_TEXT_MAP` in
  `parcels.py` instead (they're canned templates); an unrecognised sentence
  keeps `status = None` and warns once. Safe to match on a fixed English
  substring regardless of the account holder's language, since
  `TRACKING_LOCALE` is hardcoded to `"en"`. **`None` on purpose:**
  `receiver`, `weight`, `dimensions` — not in the payload. `sender` and
  `pickup_point` are now populated (`item.sender` / `item.branchAddress`).
  `trackingDetails[].time` is naive and anchored to `Europe/Prague` (Packeta is
  domestic to Czechia) rather than UTC — see `_PRAGUE` in `parcels.py`.
  Reflected in `const.py`'s `CAPABILITIES` (feeds the docs site's comparison
  table) — keep
  the two in agreement if that ever changes.

## Divergences from the scaffold

Everything not listed here follows the scaffold exactly.

*Dynamic polling* — Packeta reports neither `out_for_delivery` nor an ETA on
any real payload (see *Carrier-specific decisions*), so the hot tier is moot
in practice: parcels sit on the mid tier until they go delivered.

## Running tests

```
python -m pytest tests/ --cov=custom_components.packeta
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file in the same commit;
the API reference now lives in the private `carrier-research/packeta/api/`,
not in this repo.
