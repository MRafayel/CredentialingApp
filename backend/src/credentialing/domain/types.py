"""Constrained value types shared by the domain models."""

from typing import Annotated, Final

from pydantic import StringConstraints

type SourceId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
"""Stable identifier of a source, such as ``oig_leie`` or ``ny_omig``."""

type StateCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
"""Two-letter U.S. state or territory code, or ``US`` for a federal source."""

type Npi = Annotated[str, StringConstraints(pattern=r"^[0-9]{10}$")]
"""A National Provider Identifier: ten digits. :func:`validate_npi` checks the check digit."""

FEDERAL: Final = "US"
"""The jurisdiction code of a federal source; such a source applies to every state."""

_NPI_LUHN_PREFIX: Final = "80840"


def validate_npi(value: str) -> bool:
    """Return whether ``value`` is a well-formed NPI with a correct check digit.

    Per the CMS NPI standard, the tenth digit is a Luhn check digit computed over the first nine
    digits prefixed with ``80840`` (the ISO card issuer prefix for U.S. health identifiers), so
    the full 15-digit string must pass the Luhn check.
    """
    if len(value) != 10 or not all("0" <= char <= "9" for char in value):
        return False
    total = 0
    for position, char in enumerate(reversed(_NPI_LUHN_PREFIX + value)):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
