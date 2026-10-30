"""Reading of comma- and tab-delimited source files with the standard ``csv`` module."""

import csv
import io
from collections.abc import Iterator, Sequence
from typing import Literal, overload

from credentialing.connectors.framework.errors import ParseError
from credentialing.connectors.framework.rows import UnreadableRow


@overload
def read_delimited(
    body: bytes,
    *,
    delimiter: str,
    encoding: str,
    has_header: bool = True,
    expected_columns: Sequence[str] | None = None,
    quoted: bool = True,
    ragged: Literal[False] = False,
) -> Iterator[dict[str, str]]: ...


@overload
def read_delimited(
    body: bytes,
    *,
    delimiter: str,
    encoding: str,
    has_header: bool = True,
    expected_columns: Sequence[str] | None = None,
    quoted: bool = True,
    ragged: Literal[True],
) -> Iterator[dict[str, str] | UnreadableRow]: ...


def read_delimited(
    body: bytes,
    *,
    delimiter: str,
    encoding: str,
    has_header: bool = True,
    expected_columns: Sequence[str] | None = None,
    quoted: bool = True,
    ragged: bool = False,
) -> Iterator[dict[str, str] | UnreadableRow]:
    """Yield each data row as ``{column: value}`` with values stripped of surrounding space.

    A UTF-8 byte order mark is removed and CRLF line endings are handled. When
    ``expected_columns`` is given the header must equal it exactly (after trimming); without a
    header those names are used as the columns. Blank lines are skipped. ``quoted=False``
    reads quote characters as data, for tab-delimited exports that never quote.

    ``ragged=True`` is for sources known to carry the odd broken line (New Jersey): a row with
    a different number of fields is yielded as an :class:`UnreadableRow`, which
    ``build_records`` counts as skipped (and rejects the file when there are too many).

    Raises:
        ParseError: The body cannot be decoded, the header differs from ``expected_columns``,
            or, unless ``ragged``, a row has a different number of fields than the header. A
            wrong field count means the file's structure changed, so the whole file is
            rejected rather than loaded partially (missing rows would read as removals).
    """
    try:
        text = body.decode(encoding)
    except UnicodeDecodeError as error:
        raise ParseError(f"not valid {encoding} at byte {error.start}") from error
    reader = csv.reader(
        io.StringIO(text.removeprefix("﻿"), newline=""),
        delimiter=delimiter,
        quoting=csv.QUOTE_MINIMAL if quoted else csv.QUOTE_NONE,
        strict=True,
    )
    try:
        yield from _rows(
            reader, has_header=has_header, expected_columns=expected_columns, ragged=ragged
        )
    except csv.Error as error:
        raise ParseError(f"malformed delimited data at line {reader.line_num}") from error


def _rows(
    reader: Iterator[list[str]],
    *,
    has_header: bool,
    expected_columns: Sequence[str] | None,
    ragged: bool,
) -> Iterator[dict[str, str] | UnreadableRow]:
    columns: list[str] | None = None
    if has_header:
        header = next((row for row in reader if any(cell.strip() for cell in row)), None)
        if header is None:
            raise ParseError("the file is empty")
        columns = [cell.strip() for cell in header]
        if expected_columns is not None and columns != list(expected_columns):
            raise ParseError(
                f"unexpected header: got {', '.join(columns)}; "
                f"expected {', '.join(expected_columns)}"
            )
    elif expected_columns is not None:
        columns = list(expected_columns)
    else:
        raise ParseError("a file without a header needs expected_columns")

    number = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        number += 1
        if len(row) != len(columns):
            reason = f"data row {number} has {len(row)} fields, expected {len(columns)}"
            if not ragged:
                raise ParseError(reason)
            yield UnreadableRow(reason)
            continue
        yield {column: cell.strip() for column, cell in zip(columns, row, strict=True)}
