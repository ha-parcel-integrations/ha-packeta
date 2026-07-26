"""Tests for the Packeta API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.packeta.api import (
    PacketaApiClient,
    PacketaApiError,
)

CODE = "Z9998887776"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_item_on_success():
    session = _session_returning(200, {"item": {"barcode": CODE}})
    client = PacketaApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["barcode"] == CODE
    # the tracking code ends up in the URL
    assert CODE in session.post.call_args[0][0]


async def test_get_parcel_returns_none_on_404():
    """A 404 is Packeta's "unknown / not-yet-scanned" signal, not an error."""
    client = PacketaApiClient(_session_returning(404, {"error": "notFound"}))
    assert await client.async_get_parcel("Z0000000000") is None


async def test_get_parcel_returns_none_on_200_error_without_item():
    """A 200 carrying an error instead of an item is also "unknown"."""
    client = PacketaApiClient(_session_returning(200, {"error": "notFound"}))
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_raises_on_200_without_item():
    client = PacketaApiClient(_session_returning(200, {}))
    with pytest.raises(PacketaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_error_status():
    client = PacketaApiClient(_session_returning(500, {}))
    with pytest.raises(PacketaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_body():
    client = PacketaApiClient(_session_returning(200, "not json"))
    with pytest.raises(PacketaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = PacketaApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(PacketaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.post = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = PacketaApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
