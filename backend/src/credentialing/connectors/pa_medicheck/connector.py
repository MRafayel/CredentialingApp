"""Connector for the Pennsylvania DHS Medicheck list of precluded providers.

Source facts (survey of 2026-10-19, first sync 2026-10-27):

- File: the Medicheck app's "Download Text File", a plain GET of
  ``https://www.humanservices.dhs.pa.gov/Medchk/MedchkSearch/Medchk?target=_blank`` with no
  token or session (Azure Application Gateway, no robots.txt), described at
  https://www.pa.gov/agencies/dhs/report-fraud/medicheck-list ("updated daily"). The VERIFY
  links confirm a match by SSN or FEIN digits, which we never use.
- No Last-Modified or ETag, and a conditional GET gives ``200``; two downloads minutes apart
  were byte-identical, so change signal ``CONTENT_HASH``. Polled daily.
- CSV, every value quoted, CRLF, 18 fields on every row, about 6,750 rows (1 MB):
  ``ProviderName, LicenseNumber, Status, BeginDate, EndDate, CAO, ListDate, IND_CHGD,
  DTE_CHANGE_LAST, NAM_LAST_PROVR, NAM_FIRST_PROVR, NAM_MIDDLE_PROVR, NAM_TITLE_PROVR,
  NAM_SUFFIX_PROVR, NAM_PROVR_ALT, NAM_BUSNS_MP, IDN_NPI, NBR_FEIN``. Dates are
  ``MM/DD/YYYY 0:00:00``.
- It is a history table: one provider can have several rows (up to eight), one per action.
  A record's identity includes its action and begin date, so the rows stay distinct.
- Names come from the structured columns: a person when ``NAM_LAST_PROVR`` is set (with
  ``NAM_BUSNS_MP`` as an alias), otherwise the organization ``NAM_BUSNS_MP``, otherwise
  ``ProviderName`` split by the name parser (6 rows). ``NAM_TITLE_PROVR`` holds the
  credential (RN, MD, LPN, PCA, RPh, ...) and joins ``NAM_SUFFIX_PROVR`` in the suffix;
  ``NAM_BUSNS_MP`` can carry "a/k/a" aliases. ``NAM_PROVR_ALT`` is an alias. NPI on about a
  quarter of the rows (all valid). ``CAO`` (the county assistance office) and ``NBR_FEIN``
  stay in ``raw``. Licences are Pennsylvania licences.
- ``Status`` is mapped by :data:`STATUS_ACTIONS` and kept as ``action_raw``; about 470 rows
  have no status (most with an end date) and are ``OTHER``. An ``EndDate`` in year 2299 (and
  one in 2229) is a placeholder for "no end". About 1,800 rows have an end date in the past
  and 78 are reinstatements: those records have ended.
- First sync 2026-10-27: 1,070,349 bytes (``text/plain``, UTF-8), 6,753 rows, none skipped,
  6,734 distinct records: 6,454 individuals and 280 organizations, NPI on 1,554, licence on
  4,400, an end date on 2,253.
"""

from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.delimited import read_delimited
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import parse_name_field
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.values import (
    classify_action,
    clean,
    collapse,
    open_ended,
    parse_npi,
)
from credentialing.domain.enums import (
    AccessPolicy,
    ChangeSignal,
    CredentialType,
    EntityKind,
    ExclusionAction,
    VerificationMethod,
)
from credentialing.domain.models import (
    ConnectorCapabilities,
    ExclusionRecord,
    License,
    PersonName,
    SourceSnapshot,
)

COLUMNS: Final = (
    "ProviderName",
    "LicenseNumber",
    "Status",
    "BeginDate",
    "EndDate",
    "CAO",
    "ListDate",
    "IND_CHGD",
    "DTE_CHANGE_LAST",
    "NAM_LAST_PROVR",
    "NAM_FIRST_PROVR",
    "NAM_MIDDLE_PROVR",
    "NAM_TITLE_PROVR",
    "NAM_SUFFIX_PROVR",
    "NAM_PROVR_ALT",
    "NAM_BUSNS_MP",
    "IDN_NPI",
    "NBR_FEIN",
)
_DATE_FORMATS: Final = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y")
_STATE: Final = "PA"

STATUS_ACTIONS: Final = (
    ("preclu", ExclusionAction.PRECLUSION),
    ("reinstat", ExclusionAction.REINSTATEMENT),
    ("terminat", ExclusionAction.TERMINATION),
    ("suspen", ExclusionAction.SUSPENSION),
    ("withdraw", ExclusionAction.OTHER),
)
"""Keywords of ``Status``, first match wins ("Permanent Preclusion" is a preclusion,
"Terminated-No Cause" a termination); a blank or unknown status is ``OTHER``."""

DOWNLOAD_URL: Final = (
    "https://www.humanservices.dhs.pa.gov/Medchk/MedchkSearch/Medchk?target=_blank"
)
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="pa_medicheck",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=1),
    source_url="https://www.pa.gov/agencies/dhs/report-fraud/medicheck-list",
    display_name="Pennsylvania Medicheck Precluded Providers",
    download_url=DOWNLOAD_URL,
)


class PaMedicheckConnector(BulkListConnectorBase):
    """Downloads the Medicheck text file and maps each row (one action) into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every row; county, FEIN and change markers stay in ``raw``."""
        rows = read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS)
        return build_records(rows, partial(_record, snapshot))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    last = collapse(row["NAM_LAST_PROVR"])
    business = collapse(row["NAM_BUSNS_MP"])
    alternate = collapse(row["NAM_PROVR_ALT"])
    if last is not None:
        entity_kind = EntityKind.INDIVIDUAL
        person: PersonName | None = PersonName(
            last=last,
            first=collapse(row["NAM_FIRST_PROVR"]) or "",
            middle=collapse(row["NAM_MIDDLE_PROVR"]),
            suffix=collapse(f"{row['NAM_SUFFIX_PROVR']} {row['NAM_TITLE_PROVR']}"),
        )
        organization = None
        aliases: tuple[str, ...] = (business,) if business else ()
    elif business is not None:
        listed = parse_name_field(business, organization_hint=True)
        entity_kind, person = EntityKind.ORGANIZATION, None
        organization = listed.organization or business
        aliases = listed.aliases if listed.organization else ()
    else:
        name = parse_name_field(row["ProviderName"])
        entity_kind, person, organization = name.entity_kind, name.person, name.organization
        aliases = name.aliases
    if entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no provider name")
    if alternate and alternate not in aliases:
        aliases = (*aliases, alternate)
    license_number = clean(row["LicenseNumber"])
    status = clean(row["Status"])
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=entity_kind,
        person_name=person,
        organization_name=organization,
        aliases=aliases,
        npi=parse_npi(row["IDN_NPI"]),
        license=License(number=license_number, state=_STATE) if license_number else None,
        action=classify_action(status, STATUS_ACTIONS, default=ExclusionAction.OTHER),
        action_raw=status,
        effective_date=issues.date("BeginDate", row["BeginDate"], _DATE_FORMATS),
        end_date=open_ended(issues.date("EndDate", row["EndDate"], _DATE_FORMATS)),
        raw=dict(row),
        valid_from=snapshot.fetched_at,
    )
