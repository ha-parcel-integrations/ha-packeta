"""Tests for Packeta diagnostics."""
from unittest.mock import MagicMock

from custom_components.packeta.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "Z1112223334"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "Z1112223334",
            "sender": "Example Sender s.r.o.",
            "receiver": None,
            "status": "in_transit",
            "raw": {
                "barcode": "Z1112223334",
                "packetStatusId": "31",
                "sender": "Example Sender s.r.o.",
                "orderNumber": "123456789",
                "branchAddress": "Example Pickup Point, Example Street 1",
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    # tracking codes are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["barcode"] == "**REDACTED**"
    # confirmed against a real parcel: merchant name, order and branch address
    assert result["incoming"][0]["sender"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["orderNumber"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["branchAddress"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "in_transit"
    assert result["incoming"][0]["raw"]["packetStatusId"] == "31"
