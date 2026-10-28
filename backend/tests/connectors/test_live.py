"""Smoke tests against the real sources (``make test-live``): catch a schema change early.

Text files stream only their first line and then close the connection. Workbooks cannot be
read from their first bytes (an .xlsx is a zip archive), so each one, at most a few hundred
kilobytes, is downloaded once and only its header row is checked.
"""

from collections.abc import Sequence

import httpx2
import pytest

from credentialing.bootstrap import create_http_client
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.ia_medicaid.connector import COLUMNS as IA_COLUMNS
from credentialing.connectors.ia_medicaid.connector import IaMedicaidConnector
from credentialing.connectors.in_fssa.connector import COLUMNS as IN_COLUMNS
from credentialing.connectors.in_fssa.connector import InFssaConnector
from credentialing.connectors.md_medicaid.connector import COLUMNS as MD_COLUMNS
from credentialing.connectors.md_medicaid.connector import MdMedicaidConnector
from credentialing.connectors.nc_medicaid.connector import COLUMNS as NC_COLUMNS
from credentialing.connectors.nc_medicaid.connector import NcMedicaidConnector
from credentialing.connectors.nd_medicaid.connector import COLUMNS as ND_COLUMNS
from credentialing.connectors.nd_medicaid.connector import NdMedicaidConnector
from credentialing.connectors.ny_omig.connector import COLUMNS as NY_COLUMNS
from credentialing.connectors.ny_omig.connector import NyOmigConnector
from credentialing.connectors.oig_leie.connector import COLUMNS as OIG_COLUMNS
from credentialing.connectors.oig_leie.connector import OigLeieConnector
from credentialing.connectors.pa_medicheck.connector import COLUMNS as PA_COLUMNS
from credentialing.connectors.pa_medicheck.connector import PaMedicheckConnector
from credentialing.connectors.vt_medicaid.connector import COLUMNS as VT_COLUMNS
from credentialing.connectors.vt_medicaid.connector import VtMedicaidConnector
from credentialing.connectors.wa_hca.connector import COLUMNS as WA_COLUMNS
from credentialing.connectors.wa_hca.connector import WaHcaConnector
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


async def test_pa_medicheck_header() -> None:
    async with (
        create_http_client(get_settings()) as client,
        client.stream("GET", PaMedicheckConnector.download_url) as response,
    ):
        assert response.status_code == 200
        header = await _first_line(response)
    assert tuple(cell.strip().strip('"') for cell in header.split(",")) == PA_COLUMNS


@pytest.mark.parametrize(
    ("url", "columns"),
    [
        (NcMedicaidConnector.download_url, NC_COLUMNS),
        (MdMedicaidConnector.download_url, MD_COLUMNS),
        (VtMedicaidConnector.download_url, VT_COLUMNS),
        (NdMedicaidConnector.download_url, ND_COLUMNS),
        (WaHcaConnector.download_url, WA_COLUMNS),
        (IaMedicaidConnector.download_url, IA_COLUMNS),
        (InFssaConnector.download_url, IN_COLUMNS),
    ],
)
async def test_workbook_header(url: str, columns: Sequence[str]) -> None:
    async with create_http_client(get_settings()) as client:
        response = await client.get(url)
    assert response.status_code == 200
    assert response.headers.get("etag") or response.headers.get("last-modified")
    # The header is found (or ParseError is raised) before the first data row is read.
    rows = read_sheet(response.content, expected_columns=columns)
    assert set(next(rows)) == set(columns)
