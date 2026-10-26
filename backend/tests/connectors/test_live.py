"""Smoke tests against the real sources (``make test-live``): catch a schema change early.

Each test streams only the first line of the file and then closes the connection.
"""

import httpx2
import pytest

from credentialing.bootstrap import create_http_client
from credentialing.connectors.ny_omig.connector import COLUMNS as NY_COLUMNS
from credentialing.connectors.ny_omig.connector import NyOmigConnector
from credentialing.connectors.oig_leie.connector import COLUMNS as OIG_COLUMNS
from credentialing.connectors.oig_leie.connector import OigLeieConnector
from credentialing.infrastructure.config import get_settings

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def _first_line(response: httpx2.Response) -> str:
    buffer = b""
    async for chunk in response.aiter_bytes():
        buffer += chunk
        if b"\n" in buffer:
            break
    return buffer.split(b"\n", 1)[0].decode().removeprefix("﻿").strip()


async def test_oig_file_header_and_validators() -> None:
    async with (
        create_http_client(get_settings()) as client,
        client.stream("GET", OigLeieConnector.download_url) as response,
    ):
        assert response.status_code == 200
        assert response.headers.get("last-modified")
        header = await _first_line(response)
    assert tuple(cell.strip() for cell in header.split(",")) == OIG_COLUMNS


async def test_ny_tab_delimited_header() -> None:
    async with (
        create_http_client(get_settings()) as client,
        client.stream("GET", NyOmigConnector.download_url) as response,
    ):
        assert response.status_code == 200
        header = await _first_line(response)
    assert tuple(cell.strip() for cell in header.split("\t")) == NY_COLUMNS
