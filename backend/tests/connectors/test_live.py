"""Smoke tests against the real sources (``make test-live``): catch a schema change early.

Text files stream only their first line and then close the connection. Workbooks cannot be
read from their first bytes (an .xlsx is a zip archive), so each one, at most a few hundred
kilobytes, is downloaded once and only its header row is checked.
"""

import asyncio
import csv
import io
from collections.abc import Sequence

import httpx2
import pytest

from credentialing.bootstrap import create_http_client
from credentialing.connectors.ca_dhcs.connector import COLUMNS as CA_COLUMNS
from credentialing.connectors.ca_dhcs.connector import CaDhcsConnector
from credentialing.connectors.framework.html_table import read_html_table
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.ga_dch.connector import COLUMNS as GA_COLUMNS
from credentialing.connectors.ga_dch.connector import GaDchConnector
from credentialing.connectors.ia_medicaid.connector import COLUMNS as IA_COLUMNS
from credentialing.connectors.ia_medicaid.connector import IaMedicaidConnector
from credentialing.connectors.in_fssa.connector import COLUMNS as IN_COLUMNS
from credentialing.connectors.in_fssa.connector import InFssaConnector
from credentialing.connectors.md_medicaid.connector import COLUMNS as MD_COLUMNS
from credentialing.connectors.md_medicaid.connector import MdMedicaidConnector
from credentialing.connectors.mn_dhs_individuals.connector import COLUMNS as MN_IND_COLUMNS
from credentialing.connectors.mn_dhs_individuals.connector import MnDhsIndividualsConnector
from credentialing.connectors.mn_dhs_organizations.connector import COLUMNS as MN_ORG_COLUMNS
from credentialing.connectors.mn_dhs_organizations.connector import (
    MnDhsOrganizationsConnector,
)
from credentialing.connectors.mo_mmac.connector import COLUMNS as MO_COLUMNS
from credentialing.connectors.mo_mmac.connector import MoMmacConnector
from credentialing.connectors.mt_dphhs.connector import COLUMNS as MT_COLUMNS
from credentialing.connectors.mt_dphhs.connector import HEADER as MT_HEADER
from credentialing.connectors.mt_dphhs.connector import MtDphhsConnector
from credentialing.connectors.nc_medicaid.connector import COLUMNS as NC_COLUMNS
from credentialing.connectors.nc_medicaid.connector import NcMedicaidConnector
from credentialing.connectors.nd_medicaid.connector import COLUMNS as ND_COLUMNS
from credentialing.connectors.nd_medicaid.connector import NdMedicaidConnector
from credentialing.connectors.nj_treasury.connector import ACTIONS as NJ_ACTIONS
from credentialing.connectors.nj_treasury.connector import COLUMNS as NJ_COLUMNS
from credentialing.connectors.nj_treasury.connector import NjTreasuryConnector
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
from tests.fakes import ScriptedFetcher

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


async def test_nj_first_line_has_every_field() -> None:
    async with (
        create_http_client(get_settings()) as client,
        client.stream("GET", NjTreasuryConnector.download_url) as response,
    ):
        assert response.status_code == 200
        assert response.headers.get("last-modified")
        buffer = b""
        async for chunk in response.aiter_bytes():
            buffer += chunk
            if b"\n" in buffer:
                break
    # Not _first_line: its strip would drop the leading and trailing empty fields.
    fields = buffer.split(b"\n", 1)[0].decode().rstrip("\r").split("\t")
    assert len(fields) == len(NJ_COLUMNS)
    assert fields[NJ_COLUMNS.index("Action")] in NJ_ACTIONS


async def test_mt_page_table_header() -> None:
    async with create_http_client(get_settings()) as client:
        response = await client.get(MtDphhsConnector.download_url)
    assert response.status_code == 200
    assert response.headers.get("etag") or response.headers.get("last-modified")
    # The header is found (or ParseError is raised) before the first data row is read.
    rows = read_html_table(response.content, header=MT_HEADER, columns=MT_COLUMNS)
    first = next(rows)
    assert isinstance(first, dict)
    assert set(first) == set(MT_COLUMNS)


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


@pytest.mark.parametrize(
    ("connector", "columns"),
    [(MoMmacConnector, MO_COLUMNS), (GaDchConnector, GA_COLUMNS)],
)
async def test_linked_workbook_header(
    connector: type[MoMmacConnector | GaDchConnector], columns: Sequence[str]
) -> None:
    assert connector.landing_url is not None
    async with create_http_client(get_settings()) as client:
        page = await client.get(connector.landing_url)
        assert page.status_code == 200
        link = str(page.url.join(connector(ScriptedFetcher()).find_link(page.text)))
        response = await client.get(link)
    assert response.status_code == 200
    assert response.headers.get("etag") or response.headers.get("last-modified")
    rows = read_sheet(response.content, expected_columns=columns)
    assert set(next(rows)) == set(columns)


async def test_ca_metadata_hash_and_csv_header() -> None:
    connector = CaDhcsConnector(ScriptedFetcher())
    assert connector.metadata_url is not None
    async with create_http_client(get_settings()) as client:
        answer = await client.get(connector.metadata_url)
        assert answer.status_code == 200
        metadata = connector.read_metadata(answer.content)
        await asyncio.sleep(connector.capabilities.crawl_delay.total_seconds())
        async with client.stream("GET", metadata.url) as response:
            assert response.status_code == 200
            header = b""
            async for chunk in response.aiter_bytes():
                header += chunk
                if header.count(b"\n") >= 2:  # the header has a line break inside a cell
                    break
    rows = csv.reader(io.StringIO(header.decode("utf-8-sig"), newline=""))
    assert tuple(cell.strip() for cell in next(rows)) == CA_COLUMNS
    assert len(metadata.hash) == 32


@pytest.mark.parametrize(
    ("connector", "columns"),
    [
        (MnDhsIndividualsConnector, MN_IND_COLUMNS),
        (MnDhsOrganizationsConnector, MN_ORG_COLUMNS),
    ],
)
async def test_mn_workbook_header(
    connector: type[MnDhsIndividualsConnector | MnDhsOrganizationsConnector],
    columns: Sequence[str],
) -> None:
    # Crawl-delay: 5 in robots.txt; waited before the request, as the fetcher does.
    await asyncio.sleep(connector(ScriptedFetcher()).capabilities.crawl_delay.total_seconds())
    async with create_http_client(get_settings()) as client:
        response = await client.get(connector.download_url)
    if response.url.host != httpx2.URL(connector.download_url).host:
        pytest.skip(f"blocked: redirected to {response.url.host}")
    assert response.status_code == 200
    rows = read_sheet(response.content, expected_columns=columns)
    assert set(next(rows)) == set(columns)
