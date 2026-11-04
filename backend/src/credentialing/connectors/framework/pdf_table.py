"""Reading of lists published as ruled PDF tables, with pdfplumber's table finder.

Several states publish their exclusion list only as a PDF with a drawn grid (Delaware, West
Virginia, Wyoming, Nevada). pdfplumber's table finder follows that ruling, so a wrapped cell
stays in its cell and the columns never shift when a cell is empty, which plain text
extraction cannot guarantee. Where a row's ruling is incomplete (Wyoming draws some rows
without the rule left of the first cell) the finder sees no cell there; that cell's text is
then read from the column's span on the page. A row drawn without its inner rules (Alaska:
one cell across the whole row, the others missing) is split into the table's columns by the
position of its text the same way, or is an :class:`UnreadableRow` when a run of its text
crosses a column boundary. :func:`read_pdf_table` finds the header row and keys the data rows
by column name, like the delimited, spreadsheet and HTML table readers.
"""

import io
import logging
from collections.abc import Generator, Iterable, Iterator, Sequence
from typing import Any, Final

import pdfplumber
from pdfminer.psexceptions import PSException
from pdfplumber.page import Page
from pdfplumber.table import Table as PdfTable
from pdfplumber.table import TableSettings
from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
from pdfplumber.utils.text import extract_text, extract_words

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import UnreadableRow

_PDF_ERRORS: Final = (PdfminerException, MalformedPDFException, PSException)
"""What pdfplumber and pdfminer raise on a body that is not a readable PDF.

pdfplumber wraps pdfminer's errors while opening (``b"not a pdf"``, a truncated file) in
``PdfminerException``; pdfminer's own ``PSException`` family can still surface while pages are
read lazily.
"""

type Table = Sequence[Sequence[str | None] | UnreadableRow]

# pdfminer's warnings and errors on a damaged file quote raw content-stream bytes (list text);
# every failure that matters still reaches read_pdf_table as an exception.
logging.getLogger("pdfminer").setLevel(logging.CRITICAL)


def read_pdf_table(
    body: bytes, *, expected_columns: Sequence[str]
) -> Iterator[dict[str, str] | UnreadableRow]:
    """Yield each data row of the PDF's tables as ``{column: text}``, in document order.

    Every table of every page is read at pdfplumber's default settings. The header is a row
    with one cell per expected column whose letters and digits equal the column's, case
    ignored: "Exclusion\\nDate" matches "Exclusion Date", and West Virginia's later pages
    spell "Reason for Exclusion / Termination" without the slash. Rows before the first header
    (a title row) and later repeats of it (one per page) are skipped, and so are rows without
    text. Cells are stripped but keep their internal line breaks as ``"\\n"``, because the
    source's wrapping can be content (several NPIs, one per line). A row with another cell
    count, or a row in one cell that cannot be split into the columns, is an
    :class:`UnreadableRow`.

    Raises:
        ParseError: The body is not a readable PDF, has no page, or no row is the header.
    """
    try:
        with pdfplumber.open(io.BytesIO(body)) as document:
            if not document.pages:
                raise ParseError("the document has no pages")
            found = yield from _rows(_tables(document.pages), expected_columns)
    except _PDF_ERRORS as error:
        raise ParseError("the document is not a readable PDF") from error
    if not found:
        raise ParseError(f"no header row with {', '.join(expected_columns)}")


def _tables(pages: Iterable[Page]) -> Iterator[Table]:
    settings = TableSettings.resolve(None)
    text_settings = settings.text_settings or {}
    for page in pages:
        tables = [
            _fill_missing_cells(table, table.extract(**text_settings), text_settings)
            for table in page.find_tables(settings)
        ]
        page.close()
        yield from tables


type _Box = tuple[float, float, float, float]


def _fill_missing_cells(
    table: PdfTable, extracted: list[list[str | None]], text_settings: dict[str, Any]
) -> list[list[str | None] | UnreadableRow]:
    """Read the text of cells the finder could not close from the page itself.

    A missing cell spans its column, narrowed to the gap between the row's present
    neighbours, so a column inside a merged cell stays empty instead of repeating its text.
    A row whose only cell spans several columns is split by :func:`_split_spanning`.
    Characters are selected by their midpoint, as pdfplumber does for a found cell.
    """
    left, _, right, _ = table.bbox
    columns = sorted({cell[0] for cell in table.cells})
    edges = [*columns[1:], right]
    rows: list[list[str | None] | UnreadableRow] = []
    for row, texts in zip(table.rows, extracted, strict=True):
        cells: Sequence[_Box | None] = row.cells
        rows.append(texts)
        present = [cell for cell in cells if cell is not None]
        if len(present) == 1 and _covered(present[0], columns, edges) > 1:
            rows[-1] = _split_spanning(table.page, row.bbox, columns, edges, text_settings)
            continue
        for index, cell in enumerate(cells):
            if cell is not None:
                continue
            before = [box[2] for box in cells[:index] if box is not None]
            after = [box[0] for box in cells[index + 1 :] if box is not None]
            x0 = max(columns[index], before[-1] if before else left)
            x1 = min(edges[index], after[0] if after else right)
            if x1 > x0:
                box = (x0, row.bbox[1], x1, row.bbox[3])
                chars = [char for char in table.page.chars if _inside(char, box)]
                texts[index] = extract_text(chars, **text_settings) if chars else ""
    return rows


_EDGE_TOLERANCE: Final = 1.0
"""Points by which a cell's ruling may miss a column edge and still cover that column."""


def _covered(box: _Box, columns: Sequence[float], edges: Sequence[float]) -> int:
    """How many of the table's columns the cell ``box`` covers."""
    x0, _, x1, _ = box
    return sum(
        1
        for start, end in zip(columns, edges, strict=True)
        if x0 <= start + _EDGE_TOLERANCE and x1 >= end - _EDGE_TOLERANCE
    )


def _split_spanning(
    page: Page,
    row: _Box,
    columns: Sequence[float],
    edges: Sequence[float],
    text_settings: dict[str, Any],
) -> list[str | None] | UnreadableRow:
    """A row drawn as one cell across several columns, its text split by column position.

    The row's text is read by the columns' spans on the page, as for a missing cell. A run of
    text (characters as pdfplumber's text settings join them, spaces included) that crosses a
    column boundary means the text does not sit in the columns, as when it overflows its cell,
    so the row is unreadable rather than split wrongly. Text in separate cells is apart by
    the cells' padding, more than the joining tolerance.
    """
    _, top, _, bottom = row
    chars = [char for char in page.chars if _inside(char, row)]
    boundaries = columns[1:]
    for word in extract_words(chars, keep_blank_chars=True, **text_settings):
        if any(float(word["x0"]) < edge < float(word["x1"]) for edge in boundaries):
            return UnreadableRow("a row drawn as one cell does not split into the columns")
    texts: list[str | None] = []
    for start, end in zip(columns, edges, strict=True):
        inside = [char for char in chars if _inside(char, (start, top, end, bottom))]
        texts.append(extract_text(inside, **text_settings) if inside else "")
    return texts


def _inside(char: dict[str, Any], box: _Box) -> bool:
    x0, top, x1, bottom = box
    middle = (float(char["x0"]) + float(char["x1"])) / 2
    centre = (float(char["top"]) + float(char["bottom"])) / 2
    return x0 <= middle < x1 and top <= centre < bottom


def _rows(
    tables: Iterable[Table], expected_columns: Sequence[str]
) -> Generator[dict[str, str] | UnreadableRow, None, bool]:
    """Key the rows below the first header; returns whether a header was found."""
    names = list(expected_columns)
    wanted = [_key(name) for name in names]
    found = False
    for table in tables:
        for raw in table:
            if isinstance(raw, UnreadableRow):
                if found:
                    yield raw
                continue
            cells = [(cell or "").strip() for cell in raw]
            if not any(cells):
                continue
            if [_key(cell) for cell in cells] == wanted:
                found = True
                continue
            if not found:
                continue
            if len(cells) != len(names):
                yield UnreadableRow(f"{len(cells)} cells instead of {len(names)}")
                continue
            yield dict(zip(names, cells, strict=True))
    return found


def _key(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())
