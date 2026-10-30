"""Reading of HTML tables with the standard library's ``html.parser``, as rows of cell text.

Some sources publish their list only as a table on a web page (Montana). :func:`read_table`
returns one ``<table>`` as rows of cell texts: entities decoded, whitespace (non-breaking
spaces and ``<br>`` included) collapsed, markup inside a cell dropped. A cell spanning several
columns (``colspan``) fills the first with its text and the others with ``""``, so positions
stay aligned; ``rowspan`` is not expanded. A table nested in a cell adds its text to that cell.
:func:`read_html_table` finds the header row and keys the data rows by column name, like the
delimited and spreadsheet readers.
"""

from collections.abc import Iterator, Sequence
from html.parser import HTMLParser
from typing import Final

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import UnreadableRow
from credentialing.connectors.framework.values import collapse

MAX_COLSPAN: Final = 50
"""Largest ``colspan`` honoured; a bigger or unreadable value counts as one column."""

_CELL_TAGS: Final = frozenset({"td", "th"})


class _TableParser(HTMLParser):
    """Collects the rows of the ``index``-th top-level ``<table>`` of a document."""

    def __init__(self, index: int) -> None:
        super().__init__(convert_charrefs=True)
        self._index = index
        self._seen = 0
        self._depth = 0
        self._selected = False
        self.found = False
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._span = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._selected = self._seen == self._index
                self.found = self.found or self._selected
                self._seen += 1
            return
        if not self._selected or self._depth != 1:
            if self._cell is not None and tag == "br":
                self._cell.append(" ")
            return
        if tag == "tr":
            self._end_row()
            self._row = []
        elif tag in _CELL_TAGS:
            self._end_cell()
            if self._row is None:
                self._row = []
            self._cell = []
            self._span = _colspan(dict(attrs).get("colspan"))
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._depth == 1 and self._selected:
                self._end_row()
                self._selected = False
            self._depth = max(0, self._depth - 1)
            return
        if not self._selected or self._depth != 1:
            return
        if tag == "tr":
            self._end_row()
        elif tag in _CELL_TAGS:
            self._end_cell()

    def handle_data(self, data: str) -> None:
        if self._selected and self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:
        """Finish the document, keeping the last row of a table that is never closed."""
        super().close()
        if self._selected:
            self._end_row()

    def _end_cell(self) -> None:
        if self._cell is None or self._row is None:
            return
        self._row.append(collapse("".join(self._cell)) or "")
        self._row.extend([""] * (self._span - 1))
        self._cell = None
        self._span = 1

    def _end_row(self) -> None:
        self._end_cell()
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None


def _colspan(value: str | None) -> int:
    try:
        span = int((value or "1").strip())
    except ValueError:
        return 1
    return span if 1 <= span <= MAX_COLSPAN else 1


def read_table(body: bytes, *, encoding: str = "utf-8", index: int = 0) -> list[list[str]]:
    """The rows of the ``index``-th top-level ``<table>`` (0 is the first), as cell texts.

    Rows without cells (an empty ``<tr>``) are kept as empty lists; callers skip them.

    Raises:
        ParseError: The body cannot be decoded, or the document has no such table.
    """
    try:
        text = body.decode(encoding)
    except UnicodeDecodeError as error:
        raise ParseError(f"not valid {encoding} at byte {error.start}") from error
    parser = _TableParser(index)
    parser.feed(text)
    parser.close()
    if not parser.found:
        raise ParseError(f"the page has no table {index}")
    return parser.rows


def read_html_table(
    body: bytes,
    *,
    header: Sequence[str],
    columns: Sequence[str] | None = None,
    encoding: str = "utf-8",
    index: int = 0,
) -> Iterator[dict[str, str] | UnreadableRow]:
    """Yield each data row below the header as ``{column: text}``.

    The header is the first row whose cells equal ``header``, compared case-insensitively
    with whitespace collapsed and trailing empty cells dropped (a ``colspan`` header leaves
    one). Rows are keyed by ``columns``, which may name more columns than the header labels
    (an unlabelled source column); it defaults to ``header``. A row with fewer cells is padded
    with ``""``; a row with more non-empty cells than ``columns`` is an :class:`UnreadableRow`.
    Rows with no text are skipped.

    Raises:
        ParseError: As :func:`read_table`, or no row is the expected header.
    """
    names = list(columns if columns is not None else header)
    wanted = [_key(name) for name in header]
    rows = iter(read_table(body, encoding=encoding, index=index))
    for row in rows:
        keys = [_key(cell) for cell in row]
        while keys and not keys[-1]:
            keys.pop()
        if keys == wanted:
            break
    else:
        raise ParseError(f"no header row with {', '.join(header)}")
    number = 0
    for row in rows:
        if not any(row):
            continue
        number += 1
        cells = list(row)
        while len(cells) > len(names) and not cells[-1]:
            cells.pop()
        if len(cells) > len(names):
            yield UnreadableRow(f"table row {number} has {len(cells)} cells, expected {len(names)}")
            continue
        cells.extend([""] * (len(names) - len(cells)))
        yield dict(zip(names, cells, strict=True))


def _key(text: str) -> str:
    return (collapse(text) or "").casefold()
