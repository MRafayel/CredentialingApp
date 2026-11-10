"""Name normalization shared by matching and record hashing, so both compare the same way."""

import re
import unicodedata
from collections.abc import Sequence
from typing import Final, NamedTuple

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


MIN_LAST_NAME_PREFIX: Final = 2
"""The fewest characters a typed word needs to be searched as the start of a last name: a
single letter would range over a whole letter of the registry."""


class NameTerm(NamedTuple):
    """One name part of a search: a name key word (or words), whole or as the start."""

    text: str
    prefix: bool = False


class NameSearchShape(NamedTuple):
    """One reading of a typed name: the last name, the first name (none: any) and the middle
    name's words (none: any middle name)."""

    last: NameTerm
    first: NameTerm | None = None
    middle: tuple[NameTerm, ...] = ()


def name_search_shapes(words: Sequence[str]) -> tuple[NameSearchShape, ...]:
    """The readings of a typed name's key words (:func:`normalize_name`, split), best first.

    The last word is still being typed, so it is matched as the start of its name part; as a
    last name only with :data:`MIN_LAST_NAME_PREFIX` characters (one letter is a first-name
    initial or the start of a middle name). One word: a last name. Two words: "last first"
    and "first last". Three or more: "first middle... last" and "last first middle...", every
    word used, so a word that fits no name part finds nothing. A one-letter middle word is an
    initial, the start of that middle word.
    """
    if not words:
        return ()
    *head, typed = words
    long_enough = len(typed) >= MIN_LAST_NAME_PREFIX
    if not head:
        return (NameSearchShape(last=NameTerm(typed, prefix=True)),) if long_enough else ()
    if len(head) == 1:
        (other,) = head
        last_first = NameSearchShape(last=NameTerm(other), first=NameTerm(typed, prefix=True))
        if not long_enough:
            return (last_first,)
        return (
            last_first,
            NameSearchShape(last=NameTerm(typed, prefix=True), first=NameTerm(other)),
        )
    first, *middle = head
    shapes: list[NameSearchShape] = []
    if long_enough:
        shapes.append(
            NameSearchShape(
                last=NameTerm(typed, prefix=True),
                first=NameTerm(first),
                middle=tuple(_middle_term(word) for word in middle),
            )
        )
    given, *rest = middle
    shapes.append(
        NameSearchShape(
            last=NameTerm(first),
            first=NameTerm(given),
            middle=(*(_middle_term(word) for word in rest), NameTerm(typed, prefix=True)),
        )
    )
    return tuple(shapes)


def _middle_term(word: str) -> NameTerm:
    """A middle word: whole, or the start of the word when it is an initial."""
    return NameTerm(word, prefix=len(word) == 1)
