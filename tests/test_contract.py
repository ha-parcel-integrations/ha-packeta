"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.packeta.const import ParcelStatus
from custom_components.packeta.parcels import (
    _PRAGUE,
    normalize_parcel,
    parse_iso,
    planned_window,
)

from .payloads import active_sample, delivered_sample, pickup_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _with_status(code: str) -> dict:
    sample = pickup_sample()
    sample["packetStatusId"] = code
    return sample


def _collected_without_branch() -> dict:
    """Collected parcel whose payload no longer carries the branch field."""
    sample = delivered_sample()
    del sample["branchAddress"]
    return sample


def _no_branch_in_transit() -> dict:
    sample = active_sample()
    del sample["branchAddress"]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected_without_branch": _collected_without_branch,
    "problem": lambda: _with_status("21"),
    "no_branch": _no_branch_in_transit,
    "bare": lambda: {"trackingNumber": "Z0000000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Example Pickup Point, Example Street 1"


def test_pickup_survives_collection_without_the_branch_field():
    """The pickup-point scan in the events keeps ``pickup`` after collection."""
    parcel = normalize_parcel(_collected_without_branch())
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_pickup_follows_the_branch_field_before_arrival():
    assert normalize_parcel(active_sample())["pickup"] is True
    assert normalize_parcel(_no_branch_in_transit())["pickup"] is False


def test_delivered_at_is_the_delivery_event_not_the_newest_event():
    sample = delivered_sample()
    sample["trackingDetails"].append(
        {"text": "An administrative note.", "time": "2026-05-02 09:00:00"}
    )
    assert normalize_parcel(sample)["delivered_at"] == "2026-04-29T13:12:42+02:00"


def test_delivered_at_is_none_without_a_delivery_event():
    sample = delivered_sample()
    sample["trackingDetails"] = sample["trackingDetails"][:3]
    assert normalize_parcel(sample)["delivered_at"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=_PRAGUE)
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == parse_iso(start).astimezone(_PRAGUE).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=_PRAGUE)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()
