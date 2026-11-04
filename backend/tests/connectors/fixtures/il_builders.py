"""Synthetic Illinois HFS sanctions page and export in the live shape of 2026-11-03, made up.

The page carries the stamp and count as server-rendered text and the grid's view layouts as
HTML-escaped base64 JSON, with a fake secure configuration and export path. The export is
one sheet "Active Sanctions" with the header on row 1, Excel dates, and a stylesheet whose
fill is an empty element, as Power Pages writes it (openpyxl cannot read that unrepaired).
No real row, token or configuration is copied here.
"""

import base64
import html
import json
import re
import zipfile
from collections.abc import Sequence
from datetime import date
from io import BytesIO

from credentialing.connectors.framework.http import Response
from tests.connectors.fixtures.builders import NPI_A, NPI_B, NPI_C, Cell, workbook

COLUMNS = (
    "Provider Name",
    "License #",
    "NPI",
    "Provider Type",
    "Affiliation",
    "Action Date",
    "Action Type",
    "Address",
    "Address 2",
    "City",
    "State",
    "Zip Code",
)

PAGE_URL = "https://ilhfspartner3.dynamics365portals.us/sanctions/"
EXPORT_PATH = "/_services/download-as-excel/00000000-0000-4000-8000-000000000001"
EXPORT_URL = f"https://ilhfspartner3.dynamics365portals.us{EXPORT_PATH}"
STAMP = "10/13/2026 12:34:33 PM"
COUNT = 3273
SECURE_CONFIGURATION = "RkFLRS1TRUNVUkUtQ09ORklHVVJBVElPTg=="
"""A made-up stand-in for the page's encrypted view configuration."""
ANTI_FORGERY_VALUE = "fake-anti-forgery-value-0001"
SESSION_KEY = "2026-11-03T21:13:51|00000000-0000-4000-8000-000000000002|Active Sanctions.xlsx"
WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ROWS: Sequence[Sequence[Cell]] = (
    COLUMNS,
    ("TESTPERSON AVERY Q", "036123456", NPI_A, "PHYSICIAN", None, date(2019, 3, 4),
     "TERMINATED", "100 SAMPLE ST", None, "CHICAGO", "IL", "60601"),
    ("MOCKDOC M.D. JORDAN", "125.012345", None, "PHYSICIAN", None, date(2018, 5, 6),
     "VOLUNTARY WITHDRAWAL", "2 EXAMPLE AVE", "STE 300", "PEORIA", "IL", "61602 - 1234"),
    ("SAMPLE-PERSON RILEY", None, None, "WAIVER SERVICE PROVIDER", None, date(2024, 1, 2),
     "SUSPENDED", "3 MOCK RD", None, "SPRINGFIELD", "IL", "627011234"),
    ("EXAMPLETON JR CASEY", "MD99999", None, None, None, date(2015, 7, 8), "EXCLUSION",
     None, None, None, None, None),
    ("MOCKRIDGE APOTHECARY", None, NPI_B, "PHARMACY", None, date(2020, 9, 10), "TERMINATED",
     "5 SAMPLE PLZ", None, "ST LOUIS", "MO", "63101"),
    ("SAMPLE CARE SERVICES INC", None, None, None, None, date(2012, 11, 12), "TERMINATED",
     None, None, None, None, None),
    ("AVERY Q MOCKPERSON PSYD PC", None, NPI_C, "GROUP", None, date(2021, 2, 3),
     "TERMINATED", None, None, None, None, None),
    ("BARREDMOCK DANA", None, None, None, "MOCK TRANSPORT CO", date(2010, 4, 5), "Barred",
     "7 EXAMPLE CT", None, "JOLIET", "?", "?"),
    ("BARREDMOCK DANA", None, None, None, "SAMPLE MEDICAR INC", date(2010, 4, 5), "Barred",
     "8 EXAMPLE CT", None, "JOLIET", "IL", "60432"),
    ("SELFMOCK CLINIC", None, None, None, "SELFMOCK CLINIC", date(2011, 6, 7), "Barred",
     None, None, None, None, None),
    ("MOCK HOLDINGS INC. D/B/A/ MOCK RIDES", None, None, "MEDICAR PROVIDER", None,
     date(2016, 8, 9), "TERMINATED", None, None, None, None, None),
    ("DENTMOCK II TAYLOR", None, None, "DENTAL", None, date(2013, 10, 11), "TERMINATED",
     None, None, None, None, None),
    ("DO MINH Q", None, None, "PHYSICIAN", None, date(2022, 12, 1), "TERMINATED", None,
     None, None, None, None),
    ("BADDATEMOCK ALEX", None, None, "PODIATRIST", None, "13/45/2020", "TERMINATED", None,
     None, None, None, None),
    (None, "123456789", None, "PHYSICIAN", None, date(2017, 1, 1), "TERMINATED", None, None,
     None, None, None),
)  # fmt: skip
"""Fifteen rows: fourteen named parties (one person barred twice on one date, once per
business) and one row without a name, which is skipped."""

_FILLS = re.compile(rb"<fills[^>]*>.*?</fills>", re.DOTALL)


def il_hfs() -> bytes:
    """The export of the "Active Sanctions" view, with Power Pages' empty fill."""
    return with_empty_fill(workbook(ROWS, title="Active Sanctions"))


def with_empty_fill(body: bytes) -> bytes:
    """An openpyxl workbook with its fills replaced by one empty ``<fill/>`` element."""
    source = zipfile.ZipFile(BytesIO(body))
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as rebuilt:
        for entry in source.infolist():
            data = source.read(entry)
            if entry.filename == "xl/styles.xml":
                data = _FILLS.sub(b'<fills count="1"><fill/></fills>', data)
            rebuilt.writestr(entry, data)
    return output.getvalue()


def _columns() -> list[dict[str, object]]:
    return [
        {"WidthAsPercent": 8.33, "Type": 0, "LogicalName": f"mock_{index}", "Name": name}
        for index, name in enumerate(COLUMNS)
    ]


def layout(view_name: str, *, export_type: int = 8) -> dict[str, object]:
    """One grid view layout, as the page's ``data-view-layouts`` lists it."""
    return {
        "Configuration": {
            "EntityName": "mock_sanctions",
            "ViewName": view_name,
            "ViewActionLinks": [
                {
                    "Type": export_type,
                    "Label": "Export Data",
                    "URL": {"Path": EXPORT_PATH, "PathWithQueryString": EXPORT_PATH},
                }
            ],
        },
        "Base64SecureConfiguration": SECURE_CONFIGURATION,
        "ViewName": view_name,
        "Columns": _columns(),
        "ColumnsTotalWidth": 100,
        "SortExpression": "mock_0 ASC",
        "Id": "00000000-0000-4000-8000-000000000003",
    }


def il_hfs_page(
    *,
    stamp: str | None = STAMP,
    count: int | None = COUNT,
    layouts: Sequence[dict[str, object]] | None = None,
) -> bytes:
    """The sanctions page; ``None`` leaves the stamp or the count out."""
    views = (
        layouts if layouts is not None else (layout("Active Sanctions"), layout("Recent Additions"))
    )
    encoded = html.escape(base64.b64encode(json.dumps(list(views)).encode()).decode())
    count_text = f"Active Sanctions Count: {count} &nbsp;<br>\n" if count is not None else ""
    stamp_text = f"Sanctions Last Updated: {stamp}<br>\n" if stamp is not None else ""
    return f"""<!DOCTYPE html>
<html><head><title>Sanctions</title></head><body>
<div><strong><span style="color:#3366ff;"><strong> {count_text} Recent Additions Count: 6
</strong></span></strong></div>
&nbsp;<br>
<!--fetchxml displays the last created on date-->
<div style="color:#3366FF"><strong>  {stamp_text}<br>
 </strong></div>
<!--end fetchxml-->
<div class="entity-grid entitylist" data-id = "1"
 data-get-url="/_services/entity-grid-data.json/mock"
 data-selected-view="00000000-0000-4000-8000-000000000003" data-view-layouts='{encoded}'>
</div>
</body></html>
""".encode()


def token_html(value: str = ANTI_FORGERY_VALUE) -> bytes:
    """The portal's ``/_layout/tokenhtml`` answer."""
    return f'<input name="__RequestVerificationToken" type="hidden" value="{value}" />'.encode()


def answer(
    body: bytes,
    *,
    content_type: str | None = "text/html; charset=utf-8",
    url: str = EXPORT_URL,
    status: int = 200,
) -> Response:
    """One answered session request."""
    return Response(
        status=status,
        body=body,
        etag=None,
        last_modified=None,
        content_type=content_type,
        final_url=url,
    )


def export_answers(body: bytes) -> tuple[Response, ...]:
    """The three answers of a successful export: the token, the export key, the file."""
    return (
        answer(token_html(), url="https://ilhfspartner3.dynamics365portals.us/_layout/tokenhtml"),
        answer(
            json.dumps({"success": True, "sessionKey": SESSION_KEY}).encode(),
            content_type="application/json; charset=utf-8",
        ),
        answer(body, content_type=WORKBOOK_TYPE, url=f"{EXPORT_URL}?key=mock"),
    )
