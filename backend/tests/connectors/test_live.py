"""Smoke tests against the real sources (``make test-live``): catch a schema change early.

Text files stream only their first line and then close the connection. Workbooks cannot be
read from their first bytes (an .xlsx is a zip archive), so each one, at most a few hundred
kilobytes, is downloaded once and only its header row is checked.
"""

import asyncio
import csv
import io
from collections.abc import Sequence
from typing import Final

import httpx2
import pytest

from credentialing.bootstrap import create_http_client
from credentialing.connectors.ca_dhcs.connector import COLUMNS as CA_COLUMNS
from credentialing.connectors.ca_dhcs.connector import CaDhcsConnector
from credentialing.connectors.co_hcpf.connector import COLUMNS as CO_COLUMNS
from credentialing.connectors.co_hcpf.connector import CoHcpfConnector
from credentialing.connectors.de_dmap.connector import COLUMNS as DE_COLUMNS
from credentialing.connectors.de_dmap.connector import DeDmapConnector
from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.browser import is_challenge
from credentialing.connectors.framework.html_table import read_html_table
from credentialing.connectors.framework.pdf_table import read_pdf_table
from credentialing.connectors.framework.spreadsheet import read_sheet
from credentialing.connectors.ga_dch.connector import COLUMNS as GA_COLUMNS
from credentialing.connectors.ga_dch.connector import GaDchConnector
from credentialing.connectors.ia_medicaid.connector import COLUMNS as IA_COLUMNS
from credentialing.connectors.ia_medicaid.connector import IaMedicaidConnector
from credentialing.connectors.il_hfs.connector import IlHfsConnector
from credentialing.connectors.in_fssa.connector import COLUMNS as IN_COLUMNS
from credentialing.connectors.in_fssa.connector import InFssaConnector
from credentialing.connectors.ks_kmap.connector import COLUMNS as KS_COLUMNS
from credentialing.connectors.ks_kmap.connector import KsKmapConnector
from credentialing.connectors.ma_masshealth.connector import COLUMNS as MA_COLUMNS
from credentialing.connectors.ma_masshealth.connector import (
    REINSTATED_COLUMNS as MA_REINSTATED_COLUMNS,
)
from credentialing.connectors.ma_masshealth.connector import MaMassHealthConnector
from credentialing.connectors.md_medicaid.connector import COLUMNS as MD_COLUMNS
from credentialing.connectors.md_medicaid.connector import MdMedicaidConnector
from credentialing.connectors.mi_mdhhs.connector import COLUMNS as MI_COLUMNS
from credentialing.connectors.mi_mdhhs.connector import MiMdhhsConnector
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
from credentialing.connectors.nh_dhhs.connector import COLUMNS as NH_COLUMNS
from credentialing.connectors.nh_dhhs.connector import NhDhhsConnector
from credentialing.connectors.nj_treasury.connector import ACTIONS as NJ_ACTIONS
from credentialing.connectors.nj_treasury.connector import COLUMNS as NJ_COLUMNS
from credentialing.connectors.nj_treasury.connector import NjTreasuryConnector
from credentialing.connectors.nv_omig.connector import COLUMNS as NV_COLUMNS
from credentialing.connectors.nv_omig.connector import NvOmigConnector
from credentialing.connectors.ny_omig.connector import COLUMNS as NY_COLUMNS
from credentialing.connectors.ny_omig.connector import NyOmigConnector
from credentialing.connectors.oh_medicaid.connector import INDIVIDUAL_COLUMNS as OH_IND_COLUMNS
from credentialing.connectors.oh_medicaid.connector import (
    ORGANIZATION_COLUMNS as OH_ORG_COLUMNS,
)
from credentialing.connectors.oh_medicaid.connector import OhMedicaidConnector
from credentialing.connectors.oig_leie.connector import COLUMNS as OIG_COLUMNS
from credentialing.connectors.oig_leie.connector import OigLeieConnector
from credentialing.connectors.pa_medicheck.connector import COLUMNS as PA_COLUMNS
from credentialing.connectors.pa_medicheck.connector import PaMedicheckConnector
from credentialing.connectors.vt_medicaid.connector import COLUMNS as VT_COLUMNS
from credentialing.connectors.vt_medicaid.connector import VtMedicaidConnector
from credentialing.connectors.wa_hca.connector import COLUMNS as WA_COLUMNS
from credentialing.connectors.wa_hca.connector import WaHcaConnector
from credentialing.connectors.wv_bms.connector import COLUMNS as WV_COLUMNS
from credentialing.connectors.wv_bms.connector import WvBmsConnector
from credentialing.connectors.wy_wdh.connector import COLUMNS as WY_COLUMNS
from credentialing.connectors.wy_wdh.connector import WyWdhConnector
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
    assert metadata.hash is not None
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


async def test_nv_linked_pdf_header() -> None:
    # robots.txt disallows the site; the owner accepted fetching it politely (2026-10-31),
    # so each request waits the connector's own 10-second delay, as the fetcher does.
    connector = NvOmigConnector(ScriptedFetcher())
    assert connector.landing_url is not None
    delay = connector.capabilities.crawl_delay.total_seconds()
    async with create_http_client(get_settings()) as client:
        await asyncio.sleep(delay)
        page = await client.get(connector.landing_url)
        assert page.status_code == 200
        link = str(page.url.join(connector.find_link(page.text)))
        await asyncio.sleep(delay)
        response = await client.get(link)
    assert response.status_code == 200
    assert response.headers.get("etag") or response.headers.get("last-modified")
    # The header is found (or ParseError is raised) before the first data row is read.
    rows = read_pdf_table(response.content, expected_columns=NV_COLUMNS)
    first = next(rows)
    assert isinstance(first, dict)
    assert set(first) == set(NV_COLUMNS)


async def test_de_pdf_header() -> None:
    # The stable link redirects to the file on the same host; its ETag never changes.
    async with create_http_client(get_settings()) as client:
        response = await client.get(DeDmapConnector.download_url)
    assert response.status_code == 200
    assert response.url.host == httpx2.URL(DeDmapConnector.download_url).host
    assert response.content.startswith(b"%PDF")
    rows = read_pdf_table(response.content, expected_columns=DE_COLUMNS)
    first = next(rows)
    assert isinstance(first, dict)
    assert set(first) == set(DE_COLUMNS)


@pytest.mark.parametrize(
    ("connector", "columns"),
    [(WvBmsConnector, WV_COLUMNS), (WyWdhConnector, WY_COLUMNS)],
)
async def test_wv_wy_linked_pdf_header(
    connector: type[WvBmsConnector | WyWdhConnector], columns: Sequence[str]
) -> None:
    # West Virginia's file has no validators; Wyoming asks a 10-second crawl delay, waited
    # before each request as the fetcher does.
    located = connector(ScriptedFetcher())
    assert connector.landing_url is not None
    delay = located.capabilities.crawl_delay.total_seconds()
    async with create_http_client(get_settings()) as client:
        await asyncio.sleep(delay)
        page = await client.get(connector.landing_url)
        assert page.status_code == 200
        link = str(page.url.join(located.find_link(page.text)))
        await asyncio.sleep(delay)
        response = await client.get(link)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    first = next(read_pdf_table(response.content, expected_columns=columns))
    assert isinstance(first, dict)
    assert set(first) == set(columns)


# --- Browser-fallback states (2026-11-02): firewalls that have refused scripts before -------
# Each test makes at most two requests and waits the connector's crawl delay (10 s) before
# each, as the fetcher does. A refusal is a skip, not a failure: the connector then repeats
# the request in the headed browser, which these smoke tests do not use.

_REFUSED: Final = frozenset({401, 403, 404, 429})
"""Statuses these firewalls answer a script with (Ohio's CloudFront: a static 404 page)."""


def _skip_if_refused(response: httpx2.Response, what: str) -> None:
    """Skip when the firewall refused the script: a refusal status or a challenge page."""
    status = response.status_code
    if status in _REFUSED or response.headers.get("cf-mitigated") == "challenge":
        pytest.skip(f"blocked: {what} answered HTTP {status} to a script")
    if "html" in response.headers.get("content-type", "").lower() and is_challenge(
        status=status, url=str(response.url), html=response.text
    ):
        pytest.skip(f"blocked: {what} is a challenge page")


async def _stable_file(connector: BulkListConnectorBase) -> httpx2.Response:
    """One GET of the connector's stable file, after its crawl delay."""
    await asyncio.sleep(connector.capabilities.crawl_delay.total_seconds())
    async with create_http_client(get_settings()) as client:
        response = await client.get(connector.download_url)
    _skip_if_refused(response, "the file")
    assert response.status_code == 200
    return response


async def _linked_file(connector: BulkListConnectorBase) -> httpx2.Response:
    """The landing page, then the file it links: two GETs, the crawl delay before each."""
    assert connector.landing_url is not None
    delay = connector.capabilities.crawl_delay.total_seconds()
    async with create_http_client(get_settings()) as client:
        await asyncio.sleep(delay)
        page = await client.get(connector.landing_url)
        _skip_if_refused(page, "the landing page")
        assert page.status_code == 200
        link = str(page.url.join(connector.find_link(page.text)))
        await asyncio.sleep(delay)
        response = await client.get(link)
    _skip_if_refused(response, "the file")
    assert response.status_code == 200
    return response


async def test_ks_workbook_header() -> None:
    # Cloudflare answered the file 200 and then 403 on 2026-11-02. No validator is asserted:
    # the file has no ETag and its Last-Modified is the request time.
    response = await _stable_file(KsKmapConnector(ScriptedFetcher()))
    rows = read_sheet(response.content, sheet="Terminations", expected_columns=KS_COLUMNS)
    assert set(next(rows)) == set(KS_COLUMNS)


async def test_co_linked_workbook_header() -> None:
    # CloudFront's WAF refused scripts 2026-10-16..22 and let them through on 2026-11-02.
    response = await _linked_file(CoHcpfConnector(ScriptedFetcher()))
    assert response.headers.get("last-modified")
    rows = read_sheet(response.content, expected_columns=CO_COLUMNS)
    assert set(next(rows)) == set(CO_COLUMNS)


async def test_ma_linked_workbook_headers() -> None:
    # Akamai refused scripts in the survey and let them through on 2026-11-02.
    connector = MaMassHealthConnector(ScriptedFetcher())
    assert connector.landing_url is not None
    response = await _linked_file(connector)
    assert response.url.host == httpx2.URL(connector.landing_url).host
    assert response.headers.get("etag") or response.headers.get("last-modified")
    listed = read_sheet(response.content, sheet=0, expected_columns=MA_COLUMNS)
    assert set(next(listed)) == set(MA_COLUMNS)
    # read_sheet raises ParseError on a wrong header; the reinstated sheet may be empty.
    reinstated = list(read_sheet(response.content, sheet=1, expected_columns=MA_REINSTATED_COLUMNS))
    assert all(set(row) == set(MA_REINSTATED_COLUMNS) for row in reinstated)


async def test_mi_linked_workbook_header() -> None:
    # Akamai refused scripts in October 2026 and let them through on 2026-11-02.
    response = await _linked_file(MiMdhhsConnector(ScriptedFetcher()))
    assert response.headers.get("etag") or response.headers.get("last-modified")
    rows = read_sheet(response.content, expected_columns=MI_COLUMNS)
    assert set(next(rows)) == set(MI_COLUMNS)


async def test_nh_workbook_header_and_last_modified() -> None:
    # Akamai answered scripts 403 in October 2026 and 200 on 2026-11-02.
    response = await _stable_file(NhDhhsConnector(ScriptedFetcher()))
    assert response.headers.get("last-modified")
    rows = read_sheet(response.content, expected_columns=NH_COLUMNS)
    assert set(next(rows)) == set(NH_COLUMNS)


async def test_oh_linked_workbook_headers() -> None:
    # CloudFront answered scripts a static 404 page in October 2026 (a disguised block).
    response = await _linked_file(OhMedicaidConnector(ScriptedFetcher()))
    assert response.headers.get("etag") or response.headers.get("last-modified")
    # Each header is found (or ParseError is raised) before the first data row is read.
    for sheet, columns in (("Individuals", OH_IND_COLUMNS), ("Organizations", OH_ORG_COLUMNS)):
        rows = read_sheet(response.content, sheet=sheet, expected_columns=columns)
        assert set(next(rows)) == set(columns)


async def test_il_page_stamp_count_and_export_configuration() -> None:
    # One page GET only: the export is a POST that prepares a file on the server, which a
    # smoke test should not cause. The page has no robots.txt; the crawl delay is waited.
    connector = IlHfsConnector(ScriptedFetcher())
    assert connector.metadata_url is not None
    await asyncio.sleep(connector.capabilities.crawl_delay.total_seconds())
    async with create_http_client(get_settings()) as client:
        page = await client.get(connector.metadata_url)
    _skip_if_refused(page, "the sanctions page")
    assert page.status_code == 200
    metadata = connector.read_metadata(page.content)
    assert metadata.version is not None
    stamp, count = metadata.version.split("|")
    assert stamp
    assert int(count) > 0
    assert metadata.last_modified is not None
    assert metadata.url.startswith("https://ilhfspartner3.dynamics365portals.us/_services/")
