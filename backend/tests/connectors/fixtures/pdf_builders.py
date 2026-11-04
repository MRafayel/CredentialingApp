"""Synthetic PDFs with ruled tables, written with the standard library at test time.

Several states publish their exclusion list as a PDF table drawn with a grid. This builder
writes that shape (one ruled table per page, wrapped cell text, an optional title above the
grid) with made-up rows, so PDF fixtures are built in code and no binary is committed. It
writes an uncompressed PDF 1.4 with the standard Helvetica font; cell text must be ASCII and
short enough to fit its column.
"""

from collections.abc import Collection, Sequence
from typing import Final

MARGIN: Final = 36.0
"""Page margin on every side, in points."""

FONT_SIZE: Final = 8.0
TITLE_SIZE: Final = 12.0
LINE_HEIGHT: Final = 10.0
INSET: Final = 2.0
"""Space between a cell's ruling and its text, in points."""

LINE_WIDTH: Final = 0.5

type Row = Sequence[str]
type Page = Sequence[Row]


def build_table_pdf(
    pages: Sequence[Page],
    *,
    title: str | None = None,
    page_size: tuple[float, float] = (792, 612),
    column_widths: Sequence[float] | None = None,
    open_left_rows: Collection[tuple[int, int]] = (),
    spanning_rows: Collection[tuple[int, int]] = (),
) -> bytes:
    """A PDF with one ruled table per page: ``pages`` → rows → cells.

    A cell may hold ``"\\n"`` for a wrapped line; a row is as tall as its tallest cell. Each
    cell is drawn as its own rectangle. Without ``column_widths`` a row's cells share the
    usable width equally, so rows with the same cell count line up. ``title`` is drawn above
    the grid on every page, outside it. ``open_left_rows`` holds ``(page, row)`` positions,
    counted from 0, whose first cell is drawn without its left rule (as Wyoming does);
    ``spanning_rows`` those drawn as one rectangle across the row, without inner rules, the
    text still at each cell's position (as Alaska does).

    Raises:
        ValueError: A page's rows do not fit on it, or a row has more cells than
            ``column_widths``.
    """
    width, height = page_size
    streams = [
        _page_stream(
            rows,
            title,
            width,
            height,
            column_widths,
            {row for page, row in open_left_rows if page == index},
            {row for page, row in spanning_rows if page == index},
        )
        for index, rows in enumerate(pages)
    ]
    return _document(streams, width, height)


def _page_stream(
    rows: Sequence[Row],
    title: str | None,
    width: float,
    height: float,
    column_widths: Sequence[float] | None,
    open_left: Collection[int],
    spanning: Collection[int],
) -> bytes:
    commands = [f"{LINE_WIDTH} w"]
    top = height - MARGIN
    if title is not None:
        commands.append(_text(title, MARGIN, top - TITLE_SIZE, TITLE_SIZE))
        top -= TITLE_SIZE + LINE_HEIGHT
    for number, row in enumerate(rows):
        lines = max((cell.count("\n") + 1 for cell in row), default=1)
        row_height = lines * LINE_HEIGHT + 2 * INSET
        bottom = top - row_height
        if bottom < MARGIN:
            raise ValueError("the rows do not fit on the page")
        x = MARGIN
        widths = _widths(row, width, column_widths)
        if number in spanning:
            commands.append(f"{x:.2f} {bottom:.2f} {sum(widths):.2f} {row_height:.2f} re S")
        for position, (cell, cell_width) in enumerate(zip(row, widths, strict=True)):
            if position == 0 and number in open_left:
                commands.append(_open_left(x, bottom, cell_width, row_height))
            elif number not in spanning:
                commands.append(f"{x:.2f} {bottom:.2f} {cell_width:.2f} {row_height:.2f} re S")
            baseline = top - INSET - FONT_SIZE
            for line in cell.split("\n"):
                if line:
                    commands.append(_text(line, x + INSET, baseline, FONT_SIZE))
                baseline -= LINE_HEIGHT
            x += cell_width
        top = bottom
    return "\n".join(commands).encode("latin-1")


def _widths(row: Row, width: float, column_widths: Sequence[float] | None) -> Sequence[float]:
    if column_widths is None:
        return [(width - 2 * MARGIN) / max(len(row), 1)] * len(row)
    if len(row) > len(column_widths):
        raise ValueError("a row has more cells than column_widths")
    return column_widths[: len(row)]


def _open_left(x: float, bottom: float, cell_width: float, row_height: float) -> str:
    """A cell's top, right and bottom rules, without the left one."""
    right, top = x + cell_width, bottom + row_height
    return (
        f"{x:.2f} {top:.2f} m {right:.2f} {top:.2f} l {right:.2f} {bottom:.2f} l "
        f"{x:.2f} {bottom:.2f} l S"
    )


def _text(text: str, x: float, y: float, size: float) -> str:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 {size:g} Tf {x:.2f} {y:.2f} Td ({escaped}) Tj ET"


def _document(streams: Sequence[bytes], width: float, height: float) -> bytes:
    """Catalog (1), pages tree (2), font (3), then a page and its content per page."""
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(streams)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(streams)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for index, stream in enumerate(streams):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:g} {height:g}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode("ascii")
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(output)
