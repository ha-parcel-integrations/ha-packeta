"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific parts are :data:`_STATUS_MAP`, :func:`build_history` and
:func:`normalize_parcel` (the Packeta ``item`` field lookups). Everything else
— the sort contract, the delivered filter, the one-shot warning for unmapped
statuses — is suite-wide machinery and should be left alone. The remaining
``TODO(carrier)`` marker flags the one bit still unverified against a real
parcel (whether a fuller response names the pickup branch).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-packeta/issues/new"
    "?template=unrecognised_status.yml"
)

# Packeta ``packetStatusId`` (a numeric code, as a string) → canonical status.
#
# These six come from the maintained client ``itsvic-dev/deliveries``; the
# semantic meaning is in the trailing comment. Two deliberately use our more
# granular enum: ``997`` (to-be-processed) → REGISTERED rather than in-transit,
# and ``21`` (lost/unknown) → PROBLEM rather than unknown, since a lost parcel
# is an exception worth surfacing. The client already logs unmapped ids, so
# this map is incomplete by design: a real parcel will surface more, which land
# as ``unknown`` plus a one-shot warning that asks the user to report them.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "997": ParcelStatus.REGISTERED,        # TO_BE_PROCESSED
    "1": ParcelStatus.IN_TRANSIT,          # WAITING_FOR_DELIVERY (in warehouse)
    "31": ParcelStatus.IN_TRANSIT,         # ON_THE_WAY
    "2": ParcelStatus.AT_PICKUP_POINT,     # READY_FOR_PICKUP
    "3": ParcelStatus.DELIVERED,           # ISSUED_AND_ACCOUNTED
    "21": ParcelStatus.PROBLEM,            # LOST_OR_UNKNOWN
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Packeta status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Packeta's ``trackingDetails`` events carry
    only ``text`` (human, localised) and ``time`` (a string, format
    unconfirmed) — there is no per-event status code, so every entry keeps
    ``status = None`` and ``raw_status = text``. Sorted oldest → newest;
    events whose ``time`` does not parse are kept last rather than dropped
    (until the format is confirmed against a real parcel).
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("time"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": None,
            "raw_status": event.get("text") or None,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _latest_event_iso(events: list | None) -> str | None:
    """Return the ISO timestamp of the most recent parseable event, or ``None``.

    Packeta gives no delivered-at field, so the delivery moment is the newest
    event's time. Unparseable times yield ``None`` until the format is known.
    """
    latest: datetime | None = None
    for event in events or []:
        if not isinstance(event, dict):
            continue
        parsed = parse_iso(to_iso_timestamp(event.get("time")))
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest.isoformat() if latest is not None else None


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key the carrier does not expose is
    ``None`` — never omitted.

    Packeta's minimal consumer payload exposes only the barcode, a numeric
    status and an event timeline, so most fields are ``None``: no sender /
    receiver, no weight / dimensions, and no ETA window (``planned_from`` /
    ``planned_to`` stay ``None``).
    """
    # ``barcode`` is the item's own number; fall back to the code the
    # coordinator asked for (it injects ``trackingNumber`` on the pending
    # placeholder for a not-yet-scanned parcel).
    barcode = raw.get("barcode") or raw.get("trackingNumber")

    status_code = raw.get("packetStatusId")
    status_code = str(status_code) if status_code is not None else None
    status = map_parcel_status(status_code)
    delivered = status is ParcelStatus.DELIVERED
    at_pickup = status is ParcelStatus.AT_PICKUP_POINT

    events = raw.get("trackingDetails") or []
    # No delivered-at field; the delivery moment is the newest event's time.
    delivered_at = _latest_event_iso(events) if delivered else None

    return {
        "carrier": "Packeta",
        "barcode": barcode,
        # Packeta's minimal consumer payload names neither party.
        "sender": None,
        "receiver": None,
        "status": status,
        # No top-level status text; the carrier's own status token is the
        # numeric ``packetStatusId`` (the human, localised text lives in
        # ``history``).
        "raw_status": status_code,
        "delivered": delivered,
        "delivered_at": delivered_at,
        # Packeta's consumer endpoint carries no delivery-window estimate.
        "planned_from": None,
        "planned_to": None,
        "pickup": at_pickup,
        # TODO(carrier): a fuller response likely names the pickup branch for an
        # AT_PICKUP_POINT parcel; the minimal model does not carry it, so None
        # until a real payload shows the field.
        "pickup_point": None,
        "url": tracking_url(barcode),
        # Not exposed by the consumer endpoint.
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
