from credentialing.infrastructure.repositories.npi_registry import array_literal, copy_text_row


def test_a_plain_row_is_tab_separated_with_null_markers() -> None:
    assert copy_text_row(["1234567893", None, "SAMPLE", ""]) == "1234567893\t\\N\tSAMPLE\t\n"


def test_backslash_tab_and_line_breaks_inside_values_are_escaped() -> None:
    row = copy_text_row(["A\\B", "C\tD", "E\nF", "G\rH", None])

    assert row == "A\\\\B\tC\\tD\tE\\nF\tG\\rH\t\\N\n"


def test_an_array_literal_quotes_every_element() -> None:
    assert array_literal([]) == "{}"
    assert array_literal(["ONE", 'SAY "HI"', "BACK\\SLASH", "A,B {C}"]) == (
        '{"ONE","SAY \\"HI\\"","BACK\\\\SLASH","A,B {C}"}'
    )
