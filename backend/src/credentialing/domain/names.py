"""Name normalization shared by matching and record hashing, so both compare the same way."""

import re
import unicodedata
from typing import Final

_REMOVED: Final = re.compile("['.\\u2019]")  # apostrophes (straight and curly), periods
_SEPARATORS: Final = re.compile(r"[^0-9a-z]+")

CREDENTIAL_SUFFIXES: Final = frozenset(
    {"md", "do", "rn", "lpn", "pa", "np", "dds", "dmd", "pt", "phd", "jr", "sr", "ii", "iii"}
)
"""Trailing credentials and generational suffixes that sources mix into name fields."""


def normalize_name(text: str) -> str:
    """Reduce a name to a comparable form: ``"O'Brien, Mary-Ann M.D."`` -> ``"obrien mary ann"``.

    Case and accents are folded, apostrophes and periods are dropped, other punctuation becomes
    a space, and trailing credential suffixes are removed. A suffix is never removed when it is
    the only word left, so surnames such as "Do" or "Pa" survive.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    tokens = _SEPARATORS.sub(" ", _REMOVED.sub("", folded)).split()
    while len(tokens) > 1 and tokens[-1] in CREDENTIAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


LEGAL_DESIGNATORS: Final = frozenset(
    {
        "inc", "incorporated", "llc", "llp", "lp", "pllc", "pc", "ltd", "limited", "co",
        "corp", "corporation", "company",
    }
)  # fmt: skip
"""Trailing words that state a business's legal form, not its name ("Acme Corp" is Acme)."""


def normalize_organization_name(text: str) -> str:
    """Reduce an organization name to a comparable form: "The Acme Group, LLC" -> "acme group".

    :func:`normalize_name`, then a leading "the" and trailing legal designators (repeatedly:
    "Acme Co Inc") are dropped, so the legal form a source happens to publish does not block a
    match. A name that is nothing but those words keeps its normalized form, never empty.
    """
    normalized = normalize_name(text)
    tokens = normalized.split()
    if tokens and tokens[0] == "the":
        tokens.pop(0)
    while tokens and tokens[-1] in LEGAL_DESIGNATORS:
        tokens.pop()
    return " ".join(tokens) or normalized
