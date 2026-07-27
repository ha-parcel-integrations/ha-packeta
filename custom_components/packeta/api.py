"""Packeta public tracking API client.

Packeta exposes a keyless consumer tracking endpoint: the tracking code alone
keys the lookup — no account, no API key, no header, no postcode. It is a
**POST** whose path carries the number and a locale, and answers::

    HTTP 200  {"item": {"barcode", "packetStatusId", "trackingDetails": [...]}}
    HTTP 404  {"error": "notFound", ...}   # unknown / not yet scanned

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw ``item`` dict on success,
* returns ``None`` when Packeta reports the code as unknown/not-yet-scanned (a
  normal, expected state — the 404 above — never an error),
* raises :class:`PacketaApiError` for a malformed or unexpected body,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL, TRACKING_LOCALE

_LOGGER = logging.getLogger(__name__)


class PacketaApiError(Exception):
    """Raised when a Packeta API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the status code that triggered the error."""
        super().__init__(f"Packeta API request failed: {detail}")
        self.detail = detail


class PacketaApiClient:
    """Client for the keyless Packeta consumer tracking endpoint."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking ``item``.

        Returns the ``item`` dict for a known parcel, or ``None`` when Packeta
        reports the code as unknown (HTTP 404) — which is also what a
        not-yet-scanned parcel gets. A malformed body or an unexpected non-2xx
        status raises :class:`PacketaApiError`; network errors propagate as
        ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(
            tracking_code=tracking_code, locale=TRACKING_LOCALE
        )
        async with self._session.post(url) as response:
            if response.status == 404:
                # Well-formed "unknown number" — a normal, expected state.
                return None
            if response.status != 200:
                raise PacketaApiError(f"HTTP {response.status}")
            try:
                # content_type=None: the endpoint serves JSON without a reliable
                # JSON content-type, which aiohttp would otherwise refuse.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise PacketaApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise PacketaApiError("unexpected body (not a JSON object)")

        item = payload.get("item")
        if isinstance(item, dict):
            return item

        # A 200 that carries an error instead of an item is another way of
        # signalling an unknown code — treat it as not-found, not a crash.
        if payload.get("error"):
            return None
        raise PacketaApiError("unexpected body (no item)")
