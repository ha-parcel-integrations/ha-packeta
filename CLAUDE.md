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
  `entity_registry.async_remove` (self-removal races and leaves ghosts). The
  barcode set it compares against spans **both** directions (`_active_parcels`
  in `sensor.py`) — scope it to `coordinator.data` and every outgoing parcel's
  sensor is swept away on the next refresh.

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

- **Direction is declared by the user, never inferred.** Packeta is
  account-less *and* its payload cannot separate the two directions: a C2C
  parcel the user sends and one they receive are identical (`sender: "C2C"`,
  `courierId: "0"`), and there is no account identity to compare a party
  against. So each tracked code is filed under an `incoming` or `outgoing`
  options list (`CONF_DIRECTION` on the `CONF_PARCELS` dicts, defaulting to
  incoming so pre-existing entries need no migration), and the coordinator
  splits on that, not on the payload. **Do not build direction inference on
  the handover event text** — the "We have successfully received the parcel
  for transport. <Z-BOX>" sentence does name the drop-off box on an outgoing
  parcel, but it is free text with no structured field behind it, it says
  nothing before handover, and `_EVENT_TEXT_MAP` matching it would make the
  split silently locale- and wording-dependent. Re-submitting a code in the
  other list (or calling `track_parcel` again with the other direction) moves
  it rather than erroring — that is the correction path, so the flow has no
  duplicate error. The rest is the suite's normal outgoing shape: two summary
  sensors, the `_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`
  pair with no `registered` and no delivery-time event, per-parcel sensors for
  both directions, and `awaiting_pickup` / `next_delivery` / the calendar
  deliberately incoming-only. This makes Packeta the suite's reference for a
  **user-declared** direction; every other outgoing carrier (DPD, DHL NL,
  PPL CZ, …) derives it from an account feed.

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
