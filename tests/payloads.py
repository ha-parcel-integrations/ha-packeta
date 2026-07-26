"""Sample Packeta API payloads shared by the test modules.

These reproduce the ``item`` object the keyless ``getPacketById`` endpoint
returns on success: ``{barcode, packetStatusId, trackingDetails:[{text, time}]}``.
``trackingDetails`` events carry only human ``text`` and a ``time`` string, and
there is no per-event status code.

NOTE: the endpoint and its 404 "not found" shape were verified live, but this
*success* shape and the ``packetStatusId`` values are **reconstructed** from the
maintained client ``itsvic-dev/deliveries`` and are unverified against a real
parcel. When a real ``Z``-number arrives, this is the one module to correct —
see TODO.md. (Event ``time`` here is ISO so the tests can order it; the real
format is unconfirmed.)
"""
from __future__ import annotations

# Realistic-shaped Packeta "Z" numbers, distinct per sample.
ACTIVE_CODE = "Z1112223334"
DELIVERED_CODE = "Z9998887776"


def event(text: str, timestamp: str) -> dict:
    """One Packeta ``trackingDetails`` entry (human text + time string)."""
    return {"text": text, "time": timestamp}


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative ``item`` for a delivered parcel (packetStatusId 3)."""
    return {
        "barcode": code,
        "packetStatusId": "3",
        "trackingDetails": [
            event("Parcel data received", "2026-04-27T23:03:58Z"),
            event("On the way", "2026-04-28T15:52:17Z"),
            event("Ready for pickup", "2026-04-29T08:46:00Z"),
            event("Delivered to the recipient", "2026-04-29T13:12:42Z"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An on-the-way parcel (packetStatusId 31 → in transit)."""
    sample = delivered_sample(code)
    sample["packetStatusId"] = "31"
    sample["trackingDetails"] = sample["trackingDetails"][:2]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel ready for collection at a pickup point (packetStatusId 2)."""
    sample = delivered_sample(code)
    sample["packetStatusId"] = "2"
    sample["trackingDetails"] = sample["trackingDetails"][:3]
    return sample
