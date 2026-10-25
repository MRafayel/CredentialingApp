import pytest
from pydantic import TypeAdapter, ValidationError

from credentialing.domain.types import Npi, SourceId, StateCode, validate_npi
from tests.synthetic import make_npi


def test_documented_cms_example_passes_the_check_digit() -> None:
    assert validate_npi("1234567893")


@pytest.mark.parametrize("first_nine", ["100000001", "198765432", "555500000"])
def test_generated_npis_pass_and_any_altered_digit_fails(first_nine: str) -> None:
    npi = make_npi(first_nine)
    altered = npi[:-1] + str((int(npi[-1]) + 1) % 10)

    assert validate_npi(npi)
    assert not validate_npi(altered)


@pytest.mark.parametrize("value", ["", "123456789", "12345678930", "12345678a3", "\uff11234567893"])
def test_malformed_npis_fail(value: str) -> None:
    assert not validate_npi(value)


@pytest.mark.parametrize(
    ("annotation", "good", "bad"),
    [
        (SourceId, "ny_omig", "NY-OMIG"),
        (SourceId, "oig_leie", "oi"),
        (StateCode, "NY", "ny"),
        (StateCode, "US", "USA"),
        (Npi, "1234567893", "123456789"),
    ],
)
def test_value_types_constrain_their_strings(annotation: object, good: str, bad: str) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(annotation)

    assert adapter.validate_python(good) == good
    with pytest.raises(ValidationError):
        adapter.validate_python(bad)
