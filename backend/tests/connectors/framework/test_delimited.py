import pytest

from credentialing.connectors.framework.delimited import read_delimited
from credentialing.connectors.framework.errors import ParseError

COLUMNS = ("NAME", "CODE")


def test_reads_rows_with_bom_crlf_and_padding() -> None:
    body = "﻿NAME , CODE\r\n Testperson Avery ,A1 \r\n\r\nSampleton,B2\r\n".encode()

    rows = list(read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS))

    assert rows == [
        {"NAME": "Testperson Avery", "CODE": "A1"},
        {"NAME": "Sampleton", "CODE": "B2"},
    ]


def test_quoted_cells_may_contain_the_delimiter() -> None:
    body = b'NAME,CODE\r\n"MEDICAL PRACTICE, MD",X\r\n'

    rows = list(read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS))

    assert rows == [{"NAME": "MEDICAL PRACTICE, MD", "CODE": "X"}]


def test_unquoted_tab_files_keep_quote_characters_as_data() -> None:
    body = b'NAME\tCODE\r\nO"BRIEN "AL\t1\r\n'

    rows = list(
        read_delimited(
            body, delimiter="\t", encoding="utf-8", expected_columns=COLUMNS, quoted=False
        )
    )

    assert rows == [{"NAME": 'O"BRIEN "AL', "CODE": "1"}]


def test_header_mismatch_is_a_parse_error() -> None:
    body = b"NAME,STATUS\r\nTestperson,A\r\n"

    with pytest.raises(ParseError, match="unexpected header"):
        list(read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS))


def test_a_row_with_the_wrong_field_count_rejects_the_file() -> None:
    body = b"NAME,CODE\r\nTestperson,A\r\nSampleton,B,extra\r\n"

    with pytest.raises(ParseError, match="data row 2 has 3 fields"):
        list(read_delimited(body, delimiter=",", encoding="utf-8", expected_columns=COLUMNS))


def test_undecodable_bytes_are_a_parse_error() -> None:
    with pytest.raises(ParseError, match="not valid utf-8"):
        list(read_delimited(b"NAME,CODE\r\n\xff,1\r\n", delimiter=",", encoding="utf-8"))


def test_empty_body_is_a_parse_error() -> None:
    with pytest.raises(ParseError, match="empty"):
        list(read_delimited(b"\r\n", delimiter=",", encoding="utf-8"))


def test_headerless_files_use_the_expected_columns() -> None:
    body = b"Testperson,A\r\n"

    rows = list(
        read_delimited(
            body, delimiter=",", encoding="utf-8", has_header=False, expected_columns=COLUMNS
        )
    )

    assert rows == [{"NAME": "Testperson", "CODE": "A"}]
