"""The NPPES Data Dissemination files: the monthly full file, the weekly incremental file and
the monthly deactivation file (CMS), one connector each, all loaded into the NPI mirror.

Observed 2026-11-06: the files page ``https://download.cms.gov/nppes/NPI_Files.html`` is a
static page on Akamai NetStorage (6.8 KB; the host has no robots.txt, it answers 404). It
links each current file with a relative, single-quoted ``href`` whose name carries the
release date; only V.2 files exist since V.1 ended on 2026-03-03, and the ``id`` attributes
are stable (``DDSMTH.ZIP.D``, ``DDSWKLY.ZIP.D``, ``DDSDEACTIVATEMTH.ZIP.D``). Links are
recognized by their path, as for the other link-discovery sources:

- monthly: ``NPPES_Data_Dissemination_November_2026_V2.zip``, 1,130,479,129 bytes,
  ``Last-Modified`` Tue, 03 Nov 2026 03:29:06 GMT, an ``ETag``, ``Accept-Ranges: bytes``;
  the main CSV, its header file, three reference files (other names, practice locations,
  endpoints; not read in this stage) and two PDFs (code values, readme). See ``reader.py``.
- weekly: ``NPPES_Data_Dissemination_102626_110126_Weekly_V2.zip`` (6.03 MB per the page),
  the same layout for one week's new and changed NPIs. On 2026-11-06 this link answered
  HTTP 404 ("Not found.", 10 bytes): a normal ``FAILED`` fetch, due again on the next run.
- deactivations: ``NPPES_Deactivated_NPI_Report_110226_V2.zip``, 2,690,499 bytes; see
  ``deactivations.py``.

Every poll is one GET of the files page (polled weekly, as every list is: owner decision
2026-11-07, after a day of daily polls); a file is
downloaded only when its link changed since the last completed fetch, streamed to
``<dataset dir>/<source_id>/`` and deleted once loaded. No crawl delay: there is no
robots.txt. Records are counted in the mirror, which all three feed.
"""

import re
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Final

from credentialing.connectors.framework.base import first_href
from credentialing.connectors.framework.dataset import DatasetConnectorBase
from credentialing.connectors.nppes.deactivations import NppesDeactivationDataset
from credentialing.connectors.nppes.reader import NppesCsvDataset
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    DatasetLoadMode,
    VerificationMethod,
)
from credentialing.domain.models import ConnectorCapabilities, SourceSnapshot
from credentialing.domain.ports import Dataset
from credentialing.domain.types import FEDERAL

FILES_PAGE: Final = "https://download.cms.gov/nppes/NPI_Files.html"
"""The page that links the current NPPES files."""

POLL_INTERVAL: Final = timedelta(days=7)
"""Weekly, like the exclusion lists (owner, 2026-11-07); a new monthly file is noticed up to six
days later than a daily check would, and a file is downloaded only when its link moves."""


def _capabilities(source_id: str, display_name: str) -> ConnectorCapabilities:
    return ConnectorCapabilities(
        source_id=source_id,
        credential_type=CredentialType.NPI,
        jurisdictions=frozenset({FEDERAL}),
        access_policy=AccessPolicy.HTTP,
        change_signal=ChangeSignal.LINK_DISCOVERY,
        method=VerificationMethod.BULK_DATASET,
        poll_interval=POLL_INTERVAL,
        source_url=FILES_PAGE,
        display_name=display_name,
    )


class _NppesFileConnector(DatasetConnectorBase):
    """An NPPES file linked from the files page; subclasses name the link and the reader."""

    landing_url = FILES_PAGE
    link: ClassVar[re.Pattern[str]]
    CAPABILITIES: ClassVar[ConnectorCapabilities]

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return self.CAPABILITIES

    def find_link(self, html: str) -> str:
        """The current file's link on the files page."""
        return first_href(html, self.link)


class NppesMonthlyConnector(_NppesFileConnector):
    """The monthly full replacement file: every NPI, loaded as a diff against the mirror."""

    CAPABILITIES = _capabilities("nppes_monthly", "NPPES monthly full file")
    mode = DatasetLoadMode.FULL
    link = re.compile(r"NPPES_Data_Dissemination_[A-Za-z]+_\d{4}_V2\.zip$")

    def open_dataset(self, path: Path, snapshot: SourceSnapshot) -> Dataset:
        """The main CSV of the zip, deleted once loaded."""
        return NppesCsvDataset(path, snapshot.id, delete_on_close=True)


class NppesWeeklyConnector(_NppesFileConnector):
    """The weekly incremental file: the NPIs added or changed that week."""

    CAPABILITIES = _capabilities("nppes_weekly", "NPPES weekly incremental file")
    mode = DatasetLoadMode.INCREMENTAL
    link = re.compile(r"NPPES_Data_Dissemination_\d{6}_\d{6}_Weekly_V2\.zip$")

    def open_dataset(self, path: Path, snapshot: SourceSnapshot) -> Dataset:
        """The main CSV of the zip, deleted once loaded."""
        return NppesCsvDataset(path, snapshot.id, delete_on_close=True)


class NppesDeactivationsConnector(_NppesFileConnector):
    """The monthly deactivation file: every deactivated NPI and its deactivation date."""

    CAPABILITIES = _capabilities("nppes_deactivations", "NPPES deactivated NPI file")
    mode = DatasetLoadMode.DEACTIVATIONS
    link = re.compile(r"NPPES_Deactivated_NPI_Report_\d{6}_V2\.zip$")

    def open_dataset(self, path: Path, snapshot: SourceSnapshot) -> Dataset:
        """The workbook of the zip, deleted once loaded."""
        return NppesDeactivationDataset(path, snapshot.id, delete_on_close=True)
