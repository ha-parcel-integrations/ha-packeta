"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific parts are :data:`_STATUS_MAP`, :func:`build_history` and
:func:`normalize_parcel` (the Packeta ``item`` field lookups). Everything else
— the sort contract, the delivered filter, the one-shot warning for unmapped
statuses — is suite-wide machinery and should be left alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DIRECTION,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_DIRECTION,
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
# These six come from a third-party client; the semantic meaning is in the
# trailing comment. Two deliberately use our more
# granular enum: ``997`` (to-be-processed) → REGISTERED rather than in-transit,
# and ``21`` (lost/unknown) → PROBLEM rather than unknown, since a lost parcel
# is an exception worth surfacing. ``3`` is confirmed live: a real delivered
# parcel carried ``packetStatus: "The package has been delivered"`` alongside
# ``packetStatusId: "3"``. The other five remain reconstructed only. The map
# stays incomplete by design: unmapped ids land as ``unknown`` plus a one-shot
# warning that asks the user to report them.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "997": ParcelStatus.REGISTERED,  # TO_BE_PROCESSED
    "1": ParcelStatus.IN_TRANSIT,  # WAITING_FOR_DELIVERY (in warehouse)
    "31": ParcelStatus.IN_TRANSIT,  # ON_THE_WAY
    "2": ParcelStatus.AT_PICKUP_POINT,  # READY_FOR_PICKUP
    "3": ParcelStatus.DELIVERED,  # ISSUED_AND_ACCOUNTED
    "21": ParcelStatus.PROBLEM,  # LOST_OR_UNKNOWN
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# Packeta's ``trackingDetails[].time`` was format-unconfirmed until a real
# parcel confirmed it: a naive, space-separated string with no offset, e.g.
# ``"2026-08-13 14:02:30"``. Packeta is domestic to Czechia — the depots in
# that same real payload were all Czech (Rudná, Nučice, Praha, Ostrava) — so a
# naive value is anchored to Europe/Prague rather than UTC, the same choice
# ha-planzer/ha-quickpac make for their own domestic carriers: reading it as
# UTC would shift every event by one or two hours depending on DST.
_PRAGUE = ZoneInfo("Europe/Prague")


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. A string that parses but carries no offset
    (Packeta's confirmed ``trackingDetails[].time`` shape) is anchored to
    :data:`_PRAGUE`; a string that already carries an offset, or does not
    parse at all, passes through untouched — their consumers are guarded by
    :func:`parse_iso`.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        return text
    return parsed.replace(tzinfo=_PRAGUE).isoformat()


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
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
    }


# No per-event status code, only a sentence — but real parcels confirmed the
# sentences are canned templates, and locale is hardcoded to English
# (``TRACKING_LOCALE``), so a fixed substring match is safe. Also covers
# Zásilkovna's cross-border handoff to a local carrier (DHL NL, ACS Courier,
# FAN Courier RO, Orlen Paczka seen) — "handed over" appears in both the
# announced and completed sentences, matched on the differing wording.
_EVENT_TEXT_MAP: tuple[tuple[str, ParcelStatus], ...] = (
    ("aware of your parcel and are waiting for the sender", ParcelStatus.REGISTERED),
    ("assigned a tracking number", ParcelStatus.REGISTERED),
    ("successfully received the parcel for transport", ParcelStatus.IN_TRANSIT),
    ("on its way to the depot", ParcelStatus.IN_TRANSIT),
    ("arrived at the depot", ParcelStatus.IN_TRANSIT),
    ("has been handed over to the carrier", ParcelStatus.IN_TRANSIT),
    ("on its way to you", ParcelStatus.OUT_FOR_DELIVERY),
    ("ready for pickup", ParcelStatus.AT_PICKUP_POINT),
    ("the parcel is with you", ParcelStatus.DELIVERED),
    ("investigating the status of the parcel", ParcelStatus.PROBLEM),
)

# One-shot log dedup, keyed on the text itself (no code to key on here).
_unmapped_event_texts_logged: set[str] = set()


def _map_event_text(text: str | None) -> ParcelStatus | None:
    """Map a Packeta history sentence to a canonical status, or ``None``.

    An unrecognised sentence keeps ``status: null`` (same contract as
    :func:`map_event_status`) and warns once per distinct sentence.
    """
    if not text:
        return None
    lowered = text.lower()
    for needle, status in _EVENT_TEXT_MAP:
        if needle in lowered:
            return status
    if text not in _unmapped_event_texts_logged:
        _unmapped_event_texts_logged.add(text)
        _LOGGER.warning(
            "Unrecognised Packeta history text — help us map it. Open an "
            "issue and paste this line: %s\n  text=%r → history status stays "
            "'null'",
            NEW_ISSUE_URL,
            text,
        )
    return None


# Packeta's event ``time`` format is confirmed (see ``_PRAGUE`` above), so a
# value that still doesn't parse is a genuine anomaly rather than an expected
# gap — it means ``delivered_at`` and event ordering silently fall back for
# that entry. Logged once so a tester can report the real shape. See
# NEW_ISSUE_URL.
_unparsed_time_logged = False


def _note_unparsed_time(value: Any) -> None:
    """One-shot: flag an event time that didn't match the confirmed shape."""
    # Module-level flag by design: one warning per process, not per poll.
    global _unparsed_time_logged  # noqa: PLW0603
    if _unparsed_time_logged:
        return
    _unparsed_time_logged = True
    _LOGGER.warning(
        "Packeta event time %r did not parse — it doesn't match the confirmed "
        "'YYYY-MM-DD HH:MM:SS' shape, so delivered-at and event ordering may "
        "be off. Please report it (a diagnostics file is ideal): %s",
        value,
        NEW_ISSUE_URL,
    )


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Packeta's ``trackingDetails`` events carry
    only ``text`` (human, localised) and ``time`` (a naive, space-separated
    string — confirmed against a real parcel, see ``_PRAGUE``) — there is no
    per-event status code, so ``status`` is derived from the sentence itself
    via :func:`_map_event_text` (``None`` for a sentence we don't recognise).
    Sorted oldest → newest; events whose ``time`` does not match the confirmed
    shape are kept last rather than dropped.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("time"))
        if not timestamp:
            continue
        text = event.get("text") or None
        entry = {
            "timestamp": timestamp,
            "status": _map_event_text(text),
            "raw_status": text,
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            _note_unparsed_time(event.get("time"))
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def _delivery_event_iso(events: list | None) -> str | None:
    """Return the ISO time of the newest event that says the parcel was handed over.

    Packeta gives no delivered-at field, so the delivery moment is the event
    whose sentence maps to ``DELIVERED`` (for a pickup point, the collection).
    Unparseable times yield ``None`` until the format is known.
    """
    latest: datetime | None = None
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if _map_event_text(event.get("text")) is not ParcelStatus.DELIVERED:
            continue
        iso = to_iso_timestamp(event.get("time"))
        parsed = parse_iso(iso)
        if parsed is None:
            if iso:
                _note_unparsed_time(event.get("time"))
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest.isoformat() if latest is not None else None


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key the carrier does not expose is
    ``None`` — never omitted.

    A real parcel confirmed the full ``item`` shape carries ``sender`` (the
    merchant name) and ``branchAddress`` (the pickup-point name) alongside the
    minimal fields the reconstructed model assumed. ``receiver`` is still not
    in the payload, and there's still no weight / dimensions / ETA window
    (``planned_from`` / ``planned_to`` stay ``None``).
    """
    # ``barcode`` is the item's own number; fall back to the code the
    # coordinator asked for (it injects ``trackingNumber`` on the pending
    # placeholder for a not-yet-scanned parcel).
    barcode = raw.get("barcode") or raw.get("trackingNumber")
    # ``branchAddress`` names the assigned pickup point and is present as
    # soon as a parcel is routed there — regardless of status, not just once
    # it arrives (``at_pickup_point`` covers arrival). Confirmed against real
    # parcels: every sample seen went through a pickup point (``courierId:
    # "0"``); a courier-delivered parcel is expected to omit it, but that
    # shape is still unconfirmed.
    status_code = raw.get("packetStatusId")
    status_code = str(status_code) if status_code is not None else None
    status = map_parcel_status(status_code)
    delivered = status is ParcelStatus.DELIVERED

    events = raw.get("trackingDetails") or []
    delivered_at = _delivery_event_iso(events) if delivered else None
    # The structured branch field first; a parcel that reached a pickup point
    # stays a pickup parcel after collection.
    pickup = is_pickup(
        bool(raw.get("branchAddress")),
        status,
        (_map_event_text(e.get("text")) for e in events if isinstance(e, dict)),
    )

    return {
        "carrier": "Packeta",
        "barcode": barcode,
        # Confirmed against a real parcel: ``item.sender`` is the merchant
        # name. Packeta never names the recipient.
        "sender": raw.get("sender") or None,
        "receiver": None,
        "contents": None,
        "status": status,
        # The carrier's own status text, e.g. "The package is on its way" —
        # ``packetStatusId`` (the numeric code) drives the ``status`` mapping
        # above but is not itself carrier-facing text.
        "raw_status": raw.get("packetStatus") or status_code,
        "delivered": delivered,
        "delivered_at": delivered_at,
        # Packeta's consumer endpoint carries no delivery-window estimate.
        "planned_from": None,
        "planned_to": None,
        "pickup": pickup,
        # Confirmed against a real parcel: ``item.branchAddress`` names the
        # assigned pickup point (e.g. a Z-BOX or partner shop) and is present
        # regardless of status, not just while AT_PICKUP_POINT.
        "pickup_point": raw.get("branchAddress") or None if pickup else None,
        "url": tracking_url(barcode),
        # Not exposed by the consumer endpoint.
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def tracked_direction(item: dict) -> str:
    """Return the declared direction of one tracked-parcel options entry.

    Entries stored before the option existed carry no ``direction`` key, so
    they default to incoming rather than forcing an options migration for a
    single field.
    """
    return item.get(CONF_DIRECTION) or DEFAULT_DIRECTION


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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
