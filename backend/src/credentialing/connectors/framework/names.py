"""Splitting of single "name" cells into a person's name parts or an organization name.

Several sources (New York OMIG among them) publish individuals and organizations in one
column, with credentials and aliases mixed in: ``"LAST FIRST M  MD"``,
``"LAST FIRST AKA OTHER NAME"``, ``"SMITH JOHN DBA SMITH CLINIC"``, ``"ACME PHARMACY INC"``.
The heuristics here are deterministic and documented so a wrong split can be explained:

1. Aliases: parenthesized parts are taken out first. One that holds only credentials
   ("SMITH, MARY (LPN)") counts as written without the parentheses; any other is an alias,
   without a leading alias marker ("ACME LLC (DBA: ACME CARE)") and split at commas when it
   lists single words ("(DECOTEAU, MARTINEZ)"). Placed right after the last name of a
   "LAST, FIRST" cell it is another last name, so the alias is a full name: "ANDERSON (AKA
   FRUGE), REBECCA" gives the alias "FRUGE, REBECCA" (North Dakota); elsewhere it stays as
   written ("PATRICIA RICHARDS (MOSDEN)", a maiden name in North Carolina, gives "MOSDEN").
   Then the text is cut at every alias marker (AKA, A/K/A, DBA, D/B/A, FKA, F/K/A, T/A, with
   or without periods). The first part is the primary name; the others become aliases.
   A text that starts with a marker ("DBA SMITH CLINIC") is a business named by its first
   alias.
2. Professional corporation of one person: the trailing run of corporate designators (PC,
   P.C., PLLC, LLC, LLP, LP, INC, CORP, LTD), credentials and DENTAL, MEDICAL or PRACTICE
   words is set aside, in either order ("SAMPLEPERSON AVERY MD PC", "MOCK JAMIE DDS DENTAL
   PC", and New York's padded "SAMPLEPERSON AVERY PC   MD"). If that run holds both a
   designator and a credential, and what remains is a last and a first name with no business
   word, the cell is that person: the run becomes the suffix ("MD PC") and the corporation
   name is added as an alias, so it stays searchable. Without a credential ("ACME MEDICAL PC",
   "SAMPLEPERSON AVERY PLLC") the cell stays an organization.
3. Business or person: the primary name is an organization when it contains a business word
   (INC, LLC, CORP, PC, PLLC, LTD, CO, PHARMACY, HOSPITAL, CENTER, CLINIC, SERVICES, GROUP,
   ASSOCIATES, CARE, AND, COUNTY, ...) or an ampersand, or when the caller passes an
   organization hint (from a provider-type column) and the name carries no personal
   credential.
4. Person: with a comma, the part before it is the last name ("SMITH, JOHN M"); without one,
   the first word is the last name, joined with any leading surname particles ("DE LA CRUZ
   MARIA" has last name "DE LA CRUZ"). Trailing credentials and generational suffixes (MD, RN,
   JR, ...) become the suffix as long as a last and a first name remain. The next word is the
   first name and the rest the middle name.
5. First name first (``first_name_first=True``, for lists that write "FIRST MIDDLE LAST"):
   without a comma, the last word is the last name, joined with the surname particles
   before it ("MARIA DE LA CRUZ" has last name "DE LA CRUZ"), the first word is the first
   name and the words between are the middle name. A cell with a comma is still read as
   "LAST, FIRST".

Known limit: a two-word surname without a particle ("GARCIA LOPEZ MARIA") is read as last
name "GARCIA", first name "LOPEZ" (first name first: "MARIA GARCIA LOPEZ" has last name
"LOPEZ" and middle name "GARCIA"); the original cell always stays in the record's raw row.
"""

import re
from dataclasses import dataclass
from typing import Final

from credentialing.connectors.framework.values import collapse
from credentialing.domain.enums import EntityKind
from credentialing.domain.models import PersonName
from credentialing.domain.names import CREDENTIAL_SUFFIXES

_ALIAS_MARKER: Final = re.compile(
    r"(?<![A-Z0-9])(?:A\.?K\.?A\.?|A/K/A|D\.?B\.?A\.?|D/B/A|F\.?K\.?A\.?|F/K/A|T/A)(?![A-Z0-9])",
    re.IGNORECASE,
)
_EDGE_PUNCTUATION: Final = " ,;:-()[]"
_PARENTHESIZED: Final = re.compile(r"\(([^()]*)\)")
_LEADING_ALIAS_MARKER: Final = re.compile(rf"^\s*(?:{_ALIAS_MARKER.pattern})\s*:?", re.IGNORECASE)

SUFFIXES: Final = CREDENTIAL_SUFFIXES | frozenset(
    {
        "iv", "esq", "lcsw", "lmsw", "csw", "lmhc", "lmft", "lpc", "rph", "pharmd", "cna",
        "crna", "fnp", "anp", "dpm", "od", "dc", "psyd", "rpa", "otr", "slp", "dvm", "facs",
        "lcmhc", "licsw", "ladc", "arnp", "pa-c", "pac", "ld", "mw", "lm", "sudp", "aud", "ot",
        "aac", "ml", "psy", "lmp", "mhc", "sw",
    }
)  # fmt: skip
"""Credentials and generational suffixes recognized at the end of a person's name: the
domain's list plus credentials that state lists add (social workers, counsellors, nurse
practitioners, denturists, midwives, pharmacists, ...)."""

BUSINESS_WORDS: Final = frozenset(
    {
        "INC", "INCORPORATED", "LLC", "LLP", "LP", "PLLC", "PC", "PSC", "LTD", "LIMITED", "CO",
        "CORP", "CORPORATION", "COMPANY", "PHARMACY", "PHARMACIES", "DRUG", "DRUGS",
        "HOSPITAL", "CENTER", "CENTRE", "CLINIC", "CLINICS", "SERVICE", "SERVICES", "GROUP",
        "ASSOCIATES", "ASSOC", "MEDICAL", "HEALTH", "HEALTHCARE", "LABORATORY",
        "LABORATORIES", "LAB", "LABS", "SUPPLY", "SUPPLIES", "AMBULANCE", "AMBULETTE",
        "TRANSPORTATION", "TRANSPORT", "AGENCY", "NURSING", "REHABILITATION", "DENTAL",
        "OPTICAL", "SURGICAL", "EQUIPMENT", "ENTERPRISES", "SOLUTIONS", "SYSTEMS",
        "MANAGEMENT", "FOUNDATION", "INSTITUTE", "PRACTICE", "PARTNERS", "CHIROPRACTIC",
        "THERAPY", "COUNSELING", "PHARMACEUTICAL", "PHARMACEUTICALS", "CARE", "HOME",
        "HOMECARE", "HOSPICE", "ASSOCIATION", "PHYSICIANS", "THE", "OF", "AND", "COUNTY",
        "HOMES", "BROS", "BROTHERS",
    }
)  # fmt: skip
"""Words that mark a name as an organization's. "PA" is left out: it is also a credential.
"AND" counts like "&": no person's name in the lists has it, but practices such as
"... OBSTETRICS AND GYNECOLOGY" have no other business word."""

CORPORATE_DESIGNATORS: Final = frozenset(
    {"PC", "PSC", "PLLC", "LLC", "LLP", "LP", "INC", "CORP", "LTD"}
)
"""Legal-form words that end a corporation's name, compared without periods."""

PRACTICE_WORDS: Final = frozenset({"DENTAL", "MEDICAL", "PRACTICE"})
"""Words a professional corporation puts between the credential and its designator."""

SURNAME_PARTICLES: Final = frozenset(
    {
        "DE", "DEL", "DELA", "DELLA", "DA", "DI", "DOS", "DAS", "LA", "LE", "VAN", "VON", "ST",
        "MAC", "MC",
    }
)  # fmt: skip
"""Words that start a multi-word surname and are joined with the next word."""


@dataclass(frozen=True, slots=True)
class ParsedName:
    """A name cell split into a person, an organization (at most one of them) and aliases."""

    person: PersonName | None
    organization: str | None
    aliases: tuple[str, ...]

    @property
    def entity_kind(self) -> EntityKind:
        """Individual for a person, organization for a business, unknown for an empty cell."""
        if self.person is not None:
            return EntityKind.INDIVIDUAL
        if self.organization is not None:
            return EntityKind.ORGANIZATION
        return EntityKind.UNKNOWN


def parse_name_field(
    text: str | None, *, organization_hint: bool = False, first_name_first: bool = False
) -> ParsedName:
    """Split one name cell into a person or an organization, plus aliases (module rules)."""
    collapsed = collapse(text)
    if collapsed is None:
        return ParsedName(person=None, organization=None, aliases=())
    remaining, parenthesized = _without_parentheses(collapsed)
    first, *others = _ALIAS_MARKER.split(remaining)
    primary = _trim(first)
    aliases = tuple(part for part in (*parenthesized, *(_trim(o) for o in others)) if part)
    if not primary:
        if not aliases:
            return ParsedName(person=None, organization=None, aliases=())
        return ParsedName(person=None, organization=aliases[0], aliases=aliases[1:])
    corporate = _professional_corporation(primary, first_name_first=first_name_first)
    if corporate is not None:
        return ParsedName(person=corporate, organization=None, aliases=(primary, *aliases))
    if looks_like_business(primary) or (organization_hint and not _has_suffix(primary)):
        return ParsedName(person=None, organization=primary, aliases=aliases)
    person = _split_person(primary, first_name_first=first_name_first)
    return ParsedName(person=person, organization=None, aliases=aliases)


def split_single_name_field(text: str | None) -> tuple[PersonName | None, tuple[str, ...]]:
    """The person view of :func:`parse_name_field`: ``None`` when the cell names a business."""
    parsed = parse_name_field(text)
    return parsed.person, parsed.aliases


def looks_like_business(text: str | None) -> bool:
    """Whether the text contains a business word or an ampersand."""
    if not text:
        return False
    if "&" in text:
        return True
    words = {word.replace(".", "").strip(",;:()").upper() for word in text.split()}
    return not words.isdisjoint(BUSINESS_WORDS)


def _professional_corporation(primary: str, *, first_name_first: bool) -> PersonName | None:
    """The person behind "LAST FIRST MD PC"-style names (rule 2), or None for anything else."""
    tokens = primary.split()
    tail: list[str] = []
    while len(tokens) > 2 and (
        _bare(tokens[-1]) in CORPORATE_DESIGNATORS | PRACTICE_WORDS or _is_suffix(tokens[-1])
    ):
        tail.insert(0, tokens.pop().rstrip(","))
    has_designator = any(_bare(token) in CORPORATE_DESIGNATORS for token in tail)
    has_credential = any(_is_suffix(token) for token in tail)
    remaining = " ".join(tokens)
    if not (has_designator and has_credential) or looks_like_business(remaining):
        return None
    person = _split_person(remaining, first_name_first=first_name_first)
    if not person.first:
        return None
    return person.model_copy(update={"suffix": " ".join(tail)})


def _without_parentheses(text: str) -> tuple[str, list[str]]:
    """The text without its parenthesized parts, and the aliases those parts give (rule 1)."""
    found_aliases: list[tuple[str, bool]] = []
    masked = _PARENTHESIZED.sub(lambda found: " " * len(found[0]), text)
    before_comma = masked.split(",", 1)[0] if "," in masked else None

    def replace(found: re.Match[str]) -> str:
        inner = _trim(found[1])
        tokens = inner.replace(",", " ").split()
        if tokens and all(_is_suffix(token) for token in tokens):
            return f" {inner} "
        unmarked = _trim(_LEADING_ALIAS_MARKER.sub("", inner))
        names = [unmarked]
        if unmarked == inner and "," in inner:
            parts = [_trim(part) for part in inner.split(",")]
            if all(len(part.split()) == 1 for part in parts):
                names = parts
        after_last_name = before_comma is not None and found.end() <= len(before_comma)
        found_aliases.extend((name, after_last_name) for name in names if name)
        return " "

    remaining = collapse(_PARENTHESIZED.sub(replace, text)) or ""
    remaining = remaining.replace(" ,", ",")
    first_names = _trim(remaining.partition(",")[2]) if "," in remaining else ""
    aliases = [
        f"{name}, {first_names}" if after_last_name and first_names else name
        for name, after_last_name in found_aliases
    ]
    return remaining, aliases


def _bare(token: str) -> str:
    return token.replace(".", "").strip(",;:()").upper()


def _split_person(primary: str, *, first_name_first: bool = False) -> PersonName:
    if "," in primary:
        last_part, _, rest = primary.partition(",")
        last = _trim(last_part)
        tokens = rest.replace(",", " ").split()
        minimum = 1
    else:
        tokens = primary.split()
        last = ""
        minimum = 2
    suffixes: list[str] = []
    while len(tokens) > minimum and _is_suffix(tokens[-1]):
        suffixes.insert(0, tokens.pop())
    if not last and first_name_first:
        surname = [tokens.pop()]
        while len(tokens) > 1 and tokens[-1].upper() in SURNAME_PARTICLES:
            surname.insert(0, tokens.pop())
        last = " ".join(surname)
    if not last:
        particles: list[str] = []
        while len(tokens) > 2 and tokens[0].upper() in SURNAME_PARTICLES:
            particles.append(tokens.pop(0))
        last = " ".join([*particles, tokens.pop(0)])
    return PersonName(
        last=last,
        first=tokens[0] if tokens else "",
        middle=" ".join(tokens[1:]) or None,
        suffix=" ".join(suffixes) or None,
    )


def _has_suffix(text: str) -> bool:
    tokens = text.replace(",", " ").split()
    return len(tokens) > 1 and _is_suffix(tokens[-1])


def _is_suffix(token: str) -> bool:
    return token.replace(".", "").replace(",", "").strip(";/").lower() in SUFFIXES


def _trim(text: str) -> str:
    return text.strip(_EDGE_PUNCTUATION)
