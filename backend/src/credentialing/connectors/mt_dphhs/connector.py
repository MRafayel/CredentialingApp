"""Connector for the Montana DPHHS list of excluded or terminated Montana Medicaid providers.

Source facts (survey of 2026-10-16, first sync 2026-10-30):

- Page: https://dphhs.mt.gov/montanahealthcareprograms/terminatedexcludedproviders, about
  59 KB of UTF-8 HTML. The page itself holds the whole list as its only ``<table>`` and links
  a dated workbook (``...Providers7.27.26.xlsx``) with the same rows. The page is read, so the
  URL never changes. robots.txt allows everything.
- The page sends an ETag and Last-Modified and answers conditional requests with ``304``:
  change signal ``CONDITIONAL_GET``. The IIS ETag differs between the servers behind the
  load balancer ("4a23..." and "0152..." for the same page), and server redeploys move the
  timestamps, so a conditional request sometimes gets ``200``; the fetcher's body hash
  comparison then reports the page as unchanged. No cadence is stated; polled weekly.
- The page says: "The following list is not all-inclusive for all excluded or terminated
  providers and should not be used solely to determine or verify a provider's enrollment
  status or ability to provider services under the Montana Medicaid program."
- Header row: ``Terminated/Excluded Provider(s) | Healthcare Profession | NPI | Effective
  Date``, the last spanning two columns; the second of them, unlabelled, says where the
  listing comes from ("OIG Exclusion" 144 rows, "State Termination" 21, a few "OIG
  Termination" and combinations such as "OIG Exclusion/State Termination"). It is read as
  ``Source``, classified by keyword (an exclusion wins over a termination) and kept as
  ``action_raw``. Most rows mirror the OIG list.
- 179 table rows. Names are "Last, First M."; a business has no comma ("Sample Medical
  Supply", "Mock Center for Surgery"), but a name without one is a person when the
  profession is "Individual" ("Mockname Casey D."). A maiden name may follow the last name
  in parentheses.
  Six rows hold only "a.k.a. Other Name": aliases of the row above, folded into it (kept in
  ``raw`` under ``Aliases``). Dates M/D/YYYY.
- First sync 2026-10-30: 58,797 bytes (ETag and Last-Modified of 2026-08-14), 179 table
  rows, 6 of them "a.k.a." rows folded into 3 providers: 173 rows and records, none
  skipped; 169 individuals, 4 organizations; NPI on 46; aliases on 6 records (3 from
  "a.k.a." rows, 3 maiden names); actions exclusion 150, termination 23. A second poll a
  minute later got ``200`` from the other server and was reported unchanged by hash.
"""

from collections.abc import Iterable, Iterator, Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.html_table import read_html_table
from credentialing.connectors.framework.names import is_alias_line, parse_name_lines
from credentialing.connectors.framework.rows import (
    ParsedList,
    RowIssues,
    UnreadableRow,
    build_records,
)
from credentialing.connectors.framework.values import classify_action, clean, parse_npi_list
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import ConnectorCapabilities, ExclusionRecord, SourceSnapshot

HEADER: Final = (
    "Terminated/Excluded Provider(s)",
    "Healthcare Profession",
    "NPI",
    "Effective Date",
)
"""The labels of the page's header row."""

COLUMNS: Final = (*HEADER, "Source")
"""The row keys: the header's labels plus the unlabelled source column."""

ALIASES: Final = "Aliases"
"""The ``raw`` key holding the "a.k.a." rows folded into the row above, one per line."""

ACTION_KEYWORDS: Final = (
    ("exclu", ExclusionAction.EXCLUSION),
    ("terminat", ExclusionAction.TERMINATION),
)
"""Keywords of the source column, first match wins; anything else is ``OTHER``."""

PERSON_PROFESSION: Final = "individual"
"""The profession the page gives people whose name it writes without a comma."""

_NAME: Final = HEADER[0]
_PROFESSION: Final = HEADER[1]
_DATE_FORMATS: Final = ("%m/%d/%Y",)
_STATE: Final = "MT"

DOWNLOAD_URL: Final = "https://dphhs.mt.gov/montanahealthcareprograms/terminatedexcludedproviders"
"""The page itself: it holds the table the connector reads."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="mt_dphhs",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONDITIONAL_GET,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=7),
    source_url=DOWNLOAD_URL,
    display_name="Montana DPHHS Excluded or Terminated Medicaid Providers",
    download_url=DOWNLOAD_URL,
    provider_type_fields=(_PROFESSION,),
)


class MtDphhsConnector(BulkListConnectorBase):
    """Reads the table on the Montana page and maps each provider row into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every provider row, with the "a.k.a." rows below it as its aliases."""
        rows = read_html_table(body, header=HEADER, columns=COLUMNS)
        return build_records(fold_alias_rows(rows), partial(_record, snapshot))


def fold_alias_rows(
    rows: Iterable[Mapping[str, str] | UnreadableRow],
) -> Iterator[Mapping[str, str] | UnreadableRow]:
    """Attach each row holding only an "a.k.a." name to the provider row above it.

    An alias row with no provider row above it is passed on unchanged and skipped later.
    """
    pending: dict[str, str] | None = None
    for row in rows:
        if (
            pending is not None
            and not isinstance(row, UnreadableRow)
            and is_alias_line(row[_NAME])
            and not any(row[column] for column in COLUMNS if column != _NAME)
        ):
            known = pending.get(ALIASES)
            pending[ALIASES] = f"{known}\n{row[_NAME]}" if known else row[_NAME]
            continue
        if pending is not None:
            yield pending
            pending = None
        if isinstance(row, UnreadableRow):
            yield row
        else:
            pending = dict(row)
    if pending is not None:
        yield pending


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    if is_alias_line(row[_NAME]):
        raise ParseError("an alias row without a provider row above it")
    profession = row[_PROFESSION]
    name = parse_name_lines(
        [row[_NAME], *row.get(ALIASES, "").splitlines()],
        organization_hint=lambda line: (
            "," not in line and profession.casefold() != PERSON_PROFESSION
        ),
    )
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    npis = parse_npi_list(row["NPI"])
    source = clean(row["Source"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        related_parties=name.related_parties,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        action=classify_action(source, ACTION_KEYWORDS, default=ExclusionAction.OTHER),
        action_raw=source,
        effective_date=issues.date("Effective Date", row["Effective Date"], _DATE_FORMATS),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
