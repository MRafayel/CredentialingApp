"""Connector for the New Jersey Treasury (DORES) medical debarment list.

Source facts (survey of 2026-10-16, first sync 2026-10-30):

- File: ``https://www.nj.gov/treasury/treasfiles/debarment/Debarment-MEDICAL.txt``, linked
  from https://www.nj.gov/treasury/revenue/debarment/debarsearch-medical.shtml, which also
  documents the columns and codes. ``text/plain``, about 520 KB, ASCII, CRLF, tab-delimited,
  never quoted, no header row: 25 fields per line, the 22 documented ones and 3 empty.
- Last-Modified and ETag are sent. ``If-Modified-Since`` alone gives ``304``, but with both
  conditional headers the server answers ``200`` (the ETag seems to differ between
  load-balancer pool members), and the shared fetcher always sends both when it has them. So
  the change signal is ``CONTENT_HASH``: a plain GET every day, compared by hash (520 KB a day
  is acceptable). A Last-Modified-only conditional GET would be the refinement.
- Columns, as documented: firm name, individual name, vendor ID, firm street, city, state,
  ZIP, plus-4, NPI number, individual street, city, state, ZIP, plus-4, category, action
  (DEBARMENT, SUSPENSION, DISQUALIFICATION), reason code A-K (legend in
  :data:`REASON_CODES`), debarring department (54 = DHS), debarring agency (7540 = Medicaid),
  effective date, expiration date, permanent debarment (Y/N). No DOB or licence.
- The vendor ID can embed a tax id (FEIN, or a person's SSN, plus a two-digit suffix; 11
  digits on some individuals' rows), so it is never stored, not even in ``raw``.
- Firms and individuals share a row. A row with an individual name is that individual's,
  and the firm name (85 rows have both) becomes a related party; a row with only a firm name
  is the firm's. The row's own address block is stored, or the other one when it is empty
  (individual street addresses are published). An NPI cell can hold several NPIs ("/", " / "
  or ";" between them): the first is the record's, the others related NPIs.
- Quality (2026-10-30, 4,090 lines): one stray line holding only a firm name and one row
  holding only a name and the firm-address columns (both skipped, counted); the last line is
  tabs only. Names "LAST, FIRST M. (LPN)": the parentheses hold the profession or role
  ("CHHA" on 633 rows, "PHARM TECH", "COUNSELOR", "OWNER"), not an alias, so only known
  credentials are kept (as the suffix) and the rest is dropped from the name
  (:func:`without_profession`); AKA names are aliases. Dates M/D/YYYY with errors
  ("5/222024", year 4014, "DECEASED"), counted as unreadable. The expiration date holds
  dates and the words "PERMANENT", "RETIRED" or "DECEASED" (open-ended, no issue); a
  permanent debarment has no end. DEBARMENT, SUSPENSION and DISQUALIFICATION map to their
  own actions (DISQUALIFICATION added by owner decision, 2026-10-30); the word is kept as
  ``action_raw``.
- First sync 2026-10-30: 520,639 bytes (Last-Modified 2026-10-01), 4,089 rows, 2 skipped,
  4,087 records (4,082 distinct: five rows share another row's identity); 3,880 individuals and
  207 organizations; NPI on 1,183, a related NPI on 30, the firm as a related party on 83,
  aliases on 186; an end date on 405; 3 effective dates unreadable. Actions:
  disqualification 2,641, suspension 873, debarment 573.
"""

import re
from collections.abc import Mapping
from datetime import date, timedelta
from functools import partial
from typing import Final
from uuid import uuid4

from credentialing.connectors.framework.base import BulkListConnectorBase
from credentialing.connectors.framework.delimited import read_delimited
from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.names import (
    ParsedName,
    holds_only_credentials,
    parse_name_field,
)
from credentialing.connectors.framework.rows import ParsedList, RowIssues, build_records
from credentialing.connectors.framework.values import (
    OPEN_ENDED_AFTER_YEAR,
    clean,
    collapse,
    open_ended,
    parse_npi_list,
    parse_state,
    parse_us_zip,
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
    Address,
    ConnectorCapabilities,
    ExclusionRecord,
    SourceSnapshot,
)

COLUMNS: Final = (
    "Firm Name",
    "Individual Name",
    "Vendor ID",
    "Firm Street",
    "Firm City",
    "Firm State",
    "Firm ZIP",
    "Firm Plus4",
    "NPI Number",
    "Individual Street",
    "Individual City",
    "Individual State",
    "Individual ZIP",
    "Individual Plus4",
    "Category",
    "Action",
    "Reason",
    "Debarring Department",
    "Debarring Agency",
    "Effective Date",
    "Expiration Date",
    "Permanent Debarment",
    "Unused 23",
    "Unused 24",
    "Unused 25",
)
"""The file's 25 fields: the 22 the landing page documents, by its labels, then 3 empty."""

NOT_STORED: Final = frozenset({"Vendor ID", "Unused 23", "Unused 24", "Unused 25"})
"""Fields left out of ``raw``: the vendor ID can embed an SSN; the last three are empty."""

ACTIONS: Final = {
    "DEBARMENT": ExclusionAction.DEBARMENT,
    "SUSPENSION": ExclusionAction.SUSPENSION,
    "DISQUALIFICATION": ExclusionAction.DISQUALIFICATION,
}
"""The documented action words; a row with any other action is skipped."""

REASON_CODES: Final = {
    "A": "Criminal Offense",
    "B": "Organized Crime Contract",
    "C": "Antitrust / Anti-Kickback",
    "D": "Election Law Offense",
    "E": "Discrimination Law",
    "F": "Wage & Hour Violation",
    "G": "Industry Law Violation",
    "H": "Failure To Perform",
    "I": "Poor Performance",
    "J": "Contingent Fees",
    "K": "Other",
}
"""The reason legend from the landing page, word for word; stored as the record's reason."""

OPEN_ENDED_WORDS: Final = frozenset({"PERMANENT", "RETIRED", "DECEASED"})
"""Words the expiration column holds instead of a date; the listing has no end."""

_DATE_FORMATS: Final = ("%m/%d/%Y",)
_EARLIEST_YEAR: Final = 1900
_STATE: Final = "NJ"
_PLUS_FOUR: Final = re.compile(r"^[0-9]{4}$")
_TRAILING_PARENTHESES: Final = re.compile(r"\(([^()]*)\)\)?\s*$")

DOWNLOAD_URL: Final = "https://www.nj.gov/treasury/treasfiles/debarment/Debarment-MEDICAL.txt"
"""The file itself, as the connector fetches it."""

CAPABILITIES: Final = ConnectorCapabilities(
    source_id="nj_treasury",
    credential_type=CredentialType.EXCLUSION,
    jurisdictions=frozenset({_STATE}),
    access_policy=AccessPolicy.HTTP,
    change_signal=ChangeSignal.CONTENT_HASH,
    method=VerificationMethod.BULK_DATASET,
    poll_interval=timedelta(days=1),
    source_url="https://www.nj.gov/treasury/revenue/debarment/debarsearch-medical.shtml",
    display_name="New Jersey Treasury Medical Debarment List",
    download_url=DOWNLOAD_URL,
)


class NjTreasuryConnector(BulkListConnectorBase):
    """Downloads the New Jersey tab-delimited file and maps each line into a record."""

    download_url = DOWNLOAD_URL

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """What the connector covers and how it reaches its source."""
        return CAPABILITIES

    def parse(self, body: bytes, snapshot: SourceSnapshot) -> ParsedList:
        """Map every line; a line that does not have the 25 fields is skipped and counted."""
        rows = read_delimited(
            body,
            delimiter="\t",
            encoding="utf-8",
            has_header=False,
            expected_columns=COLUMNS,
            quoted=False,
            ragged=True,
        )
        return build_records(rows, partial(_record, snapshot))


def _party(row: Mapping[str, str]) -> tuple[ParsedName, tuple[str, ...], str]:
    """The row's party, the other parties it names, and which address block is its own."""
    individual = collapse(row["Individual Name"])
    firm = collapse(row["Firm Name"])
    if individual:
        related = (firm,) if firm and firm != individual else ()
        return parse_name_field(without_profession(individual)), related, "Individual"
    return parse_name_field(firm, organization_hint=True), (), "Firm"


def without_profession(name: str) -> str:
    """The individual's name without the trailing parenthesized profession or role.

    New Jersey writes the profession after the name: "(MD)", "(CHHA)", "(PHARM TECH)",
    "(OWNER)". Credentials the name parser knows stay and become the suffix; anything else
    would be read as an alias, so it is dropped here and stays in ``raw``.
    """
    while (found := _TRAILING_PARENTHESES.search(name)) and not holds_only_credentials(found[1]):
        name = name[: found.start()].rstrip()
    return name


def _address(row: Mapping[str, str], block: str) -> Address | None:
    postal_code = parse_us_zip(row[f"{block} ZIP"])
    plus_four = row[f"{block} Plus4"].strip()
    if postal_code and len(postal_code) == 5 and _PLUS_FOUR.match(plus_four):
        postal_code = f"{postal_code}-{plus_four}"
    address = Address(
        line1=clean(row[f"{block} Street"]),
        city=clean(row[f"{block} City"]),
        state=parse_state(row[f"{block} State"]),
        postal_code=postal_code,
    )
    return address if address != Address() else None


def _effective_date(row: Mapping[str, str], issues: RowIssues) -> date | None:
    column = "Effective Date"
    found = issues.date(column, row[column], _DATE_FORMATS)
    if found is not None and not _EARLIEST_YEAR <= found.year <= OPEN_ENDED_AFTER_YEAR:
        issues.unreadable[column] += 1
        return None
    return found


def _end_date(row: Mapping[str, str], issues: RowIssues) -> date | None:
    column = "Expiration Date"
    text = (clean(row[column]) or "").upper()
    if row["Permanent Debarment"].strip().upper() == "Y" or text in OPEN_ENDED_WORDS:
        return None
    return open_ended(issues.date(column, text, _DATE_FORMATS))


def _record(snapshot: SourceSnapshot, row: Mapping[str, str], issues: RowIssues) -> ExclusionRecord:
    name, related_parties, own_block = _party(row)
    if name.entity_kind is EntityKind.UNKNOWN:
        raise ParseError("row has no firm or individual name")
    action_text = clean(row["Action"])
    action = ACTIONS.get((action_text or "").upper())
    if action is None:
        raise ParseError("row has no documented action")
    other_block = "Firm" if own_block == "Individual" else "Individual"
    npis = parse_npi_list(row["NPI Number"])
    reason_code = (clean(row["Reason"]) or "").upper()
    return ExclusionRecord.create(
        id=uuid4(),
        source_id=CAPABILITIES.source_id,
        snapshot_id=snapshot.id,
        entity_kind=name.entity_kind,
        person_name=name.person,
        organization_name=name.organization,
        aliases=name.aliases,
        related_parties=related_parties,
        npi=npis[0] if npis else None,
        related_npis=npis[1:],
        address=_address(row, own_block) or _address(row, other_block),
        action=action,
        action_raw=action_text,
        effective_date=_effective_date(row, issues),
        end_date=_end_date(row, issues),
        reason=REASON_CODES.get(reason_code, reason_code or None),
        raw={column: value for column, value in row.items() if column not in NOT_STORED},
        valid_from=snapshot.fetched_at,
    )
