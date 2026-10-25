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
