"""Reading of Excel workbooks (.xlsx) with openpyxl, as rows of text like the delimited reader.

openpyxl ships no type information, so it is used only here, and every cell leaves this module
as a ``str``. State lists put title rows, logos and merged cells above the header, change the
sheet name every month, repeat column names and end with footnotes; :func:`read_sheet` finds
the header by its expected names and handles the rest.
"""

import zipfile
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import date, datetime, time
from io import BytesIO
from typing import Any, Final

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.values import collapse

DEFAULT_HEADER_SEARCH_ROWS: Final = 10
"""Rows searched for the header: enough for every title block observed (North Carolina: 6)."""


def read_sheet(
    body: bytes,
    *,
    sheet: int | str = 0,
    expected_columns: Sequence[str],
    header_search_rows: int = DEFAULT_HEADER_SEARCH_ROWS,
    stop_at_blank_row: bool = False,
) -> Iterator[dict[str, str]]:
    """Yield each data row of one sheet as ``{expected column: text}``.

    The header is the first of the first ``header_search_rows`` rows whose names equal
    ``expected_columns`` in order, compared case-insensitively with whitespace (non-breaking
    spaces and line breaks included) collapsed, so a trailing space in a source's header does
    not matter. A name repeated in the header is numbered from its second occurrence:
    ``State``, ``State (2)``. Empty cells after the last expected column are ignored. Rows are
    keyed by the ``expected_columns`` spelling.

    Fully empty rows are skipped, or end the data when ``stop_at_blank_row`` is set (for
    sheets with footnotes below the table). Cells become text: dates and date-times give
    ``YYYY-MM-DD``, whole numbers their digits (``1234567890.0`` gives ``1234567890``), empty
    cells ``""``, anything else ``str(value).strip()``. Dates typed as text stay as written.
    Excel date serials in date-formatted cells already arrive as dates.

    Raises:
        ParseError: The body is not a workbook, the sheet does not exist, or no row in the
            search range is the expected header.
    """
    try:
        workbook = load_workbook(BytesIO(body), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError) as error:
        raise ParseError(f"not a readable xlsx workbook ({type(error).__name__})") from error
    try:
        yield from _rows(
            _worksheet(workbook, sheet),
            expected_columns=expected_columns,
            header_search_rows=header_search_rows,
            stop_at_blank_row=stop_at_blank_row,
        )
    finally:
        workbook.close()


# openpyxl is untyped: its workbook and worksheet objects are ``Any`` inside this module only.
type _Workbook = Any
type _Worksheet = Any


def _worksheet(workbook: _Workbook, sheet: int | str) -> _Worksheet:
    names = [str(name) for name in workbook.sheetnames]
    if isinstance(sheet, int):
        if not 0 <= sheet < len(names):
            raise ParseError(f"the workbook has {len(names)} sheets, no sheet {sheet}")
        name = names[sheet]
    elif sheet in names:
        name = sheet
    else:
        raise ParseError(f"the workbook has no sheet named {sheet!r}")
    worksheet = workbook[name]
    # Read-only mode trusts the dimensions the writing program declared, and some programs
    # declare them wrongly (``A1:A1``); resetting makes openpyxl read every stored cell.
    worksheet.reset_dimensions()
    return worksheet


def _rows(
    worksheet: _Worksheet,
    *,
    expected_columns: Sequence[str],
    header_search_rows: int,
    stop_at_blank_row: bool,
) -> Iterator[dict[str, str]]:
    rows: Iterator[tuple[object, ...]] = iter(worksheet.iter_rows(values_only=True))
    wanted = [_key(column) for column in expected_columns]
    width = len(wanted)
    for _ in range(header_search_rows):
        header = next(rows, None)
        if header is None:
            break
        if _header_keys(header) == wanted:
            break
    else:
        header = None
    if header is None:
        raise ParseError(
            f"no header row with {', '.join(expected_columns)} "
            f"in the first {header_search_rows} rows"
        )

    for row in rows:
        cells = [_text(row[index]) if index < len(row) else "" for index in range(width)]
        if not any(cells):
            if stop_at_blank_row:
                return
            continue
        yield dict(zip(expected_columns, cells, strict=True))


def _header_keys(row: Sequence[object]) -> list[str]:
    """The row's names as compared: normalized, repeats numbered, trailing empties dropped."""
    names = [collapse(_text(cell)) or "" for cell in row]
    while names and not names[-1]:
        names.pop()
    seen: Counter[str] = Counter()
    keys: list[str] = []
    for name in names:
        key = _key(name)
        seen[key] += 1
        keys.append(key if seen[key] == 1 or not key else _key(f"{name} ({seen[key]})"))
    return keys


def _key(name: str) -> str:
    return (collapse(name) or "").casefold()


def _text(value: object) -> str:
    """One cell as text (see :func:`read_sheet`)."""
    match value:
        case None:
            return ""
        case datetime():
            return value.date().isoformat()
        case date():
            return value.isoformat()
        case time():
            return value.isoformat()
        case bool():
            return str(value).upper()
        case int():
            return str(value)
        case float() if value.is_integer():
            return str(int(value))
        case _:
            return str(value).strip()
