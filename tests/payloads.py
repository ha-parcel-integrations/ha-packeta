"""Sample Packeta API payloads shared by the test modules.

These reproduce the ``item`` object the keyless ``getPacketById`` endpoint
returns on success. Confirmed live against real delivered parcels 2026-08-19
(values below are fictional placeholders, not the real payload — see
carrier-research/packeta/ for that): ``barcode``, ``packetStatusId``,
``packetStatus``, ``sender``, ``branchAddress`` and
``trackingDetails: [{text, time}]``.
``trackingDetails`` events carry only human ``text`` and a naive,
space-separated ``time`` string (``"YYYY-MM-DD HH:MM:SS"``, no offset) — and
there is no per-event status code. The other five ``packetStatusId`` values
remain reconstructed from the maintained client ``itsvic-dev/deliveries``.
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
        "packetStatus": "The package has been delivered",
        "sender": "Example Sender s.r.o.",
        "branchAddress": "Example Pickup Point, Example Street 1",
        "trackingDetails": [
            event("Parcel data received", "2026-04-27 23:03:58"),
            event("On the way", "2026-04-28 15:52:17"),
            event("Ready for pickup", "2026-04-29 08:46:00"),
            event("Delivered to the recipient", "2026-04-29 13:12:42"),
        ],
    }


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An on-the-way parcel (packetStatusId 31 → in transit)."""
    sample = delivered_sample(code)
    sample["packetStatusId"] = "31"
    sample["packetStatus"] = "The package is on its way"
    sample["trackingDetails"] = sample["trackingDetails"][:2]
    return sample


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel ready for collection at a pickup point (packetStatusId 2)."""
    sample = delivered_sample(code)
    sample["packetStatusId"] = "2"
    sample["packetStatus"] = "Ready for pickup at the branch"
    sample["trackingDetails"] = sample["trackingDetails"][:3]
    return sample
