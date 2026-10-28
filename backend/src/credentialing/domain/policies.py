"""Domain policies: freshness windows, provider-to-record matching and the verdict roll-up.

Freshness numbers live only here. Matching is deterministic and explainable: every rule that
fires leaves a piece of evidence, and the explanation is built from that evidence. The verdict
is decided here once, so no screen ever derives a status of its own.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Final, NamedTuple, Self

from pydantic import Field, model_validator

from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    ExclusionAction,
    Finding,
    Freshness,
    MatchField,
    MatchLevel,
    MatchRelation,
    Outcome,
    RecordStatus,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    DomainModel,
    ExclusionRecord,
    License,
    MatchEvidence,
    MatchResult,
    PersonName,
    Provider,
    ProviderVerdict,
    VerificationResult,
)
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.types import SourceId

# --- Freshness -----------------------------------------------------------------------------


class FreshnessPolicy(DomainModel):
    """How long a result may be served before it is stale.

    ``live_window`` is how recent a result must be to count as fetched during this request.
    """

    max_age: timedelta = Field(gt=timedelta(0))
    live_window: timedelta = Field(default=timedelta(minutes=1), ge=timedelta(0))

    def evaluate(
        self, verified_at: datetime, now: datetime, method: VerificationMethod
    ) -> Freshness:
        """Classify a result verified at ``verified_at`` as seen at ``now``."""
        if method is VerificationMethod.MANUAL:
            return Freshness.MANUAL
        age = now - verified_at
        if age <= self.live_window:
            return Freshness.LIVE
        if age <= self.max_age:
            return Freshness.CACHED
        return Freshness.STALE


type PolicyKey = tuple[CredentialType, SourceId | None]


class FreshnessPolicyTable(DomainModel):
    """Freshness policies by ``(credential type, source)``, with a per-credential-type default.

    A ``None`` source is the default for its credential type; every credential type must have
    one, so a lookup always succeeds.
    """

    policies: Mapping[PolicyKey, FreshnessPolicy]

    @model_validator(mode="after")
    def _every_credential_type_has_a_default(self) -> Self:
        missing = [kind for kind in CredentialType if (kind, None) not in self.policies]
        if missing:
            raise ValueError(f"no default freshness policy for {', '.join(missing)}")
        return self

    def policy_for(self, credential_type: CredentialType, source_id: str) -> FreshnessPolicy:
        """Return the source's own policy, or the credential type's default."""
        return self.policies.get(
            (credential_type, source_id), self.policies[(credential_type, None)]
        )


DEFAULT_FRESHNESS: Final = FreshnessPolicyTable(
    policies={
        (CredentialType.EXCLUSION, None): FreshnessPolicy(max_age=timedelta(days=7)),
        (CredentialType.NPI, None): FreshnessPolicy(max_age=timedelta(days=30)),
        (CredentialType.STATE_LICENSE, None): FreshnessPolicy(max_age=timedelta(days=1)),
        (CredentialType.BOARD_CERTIFICATION, None): FreshnessPolicy(max_age=timedelta(days=30)),
    }
)
"""The single place freshness windows are declared."""


# --- Matching ------------------------------------------------------------------------------

_FIELD_LABELS: Final = {
    MatchField.NPI: "NPI",
    MatchField.LAST_NAME: "last name",
    MatchField.FIRST_NAME: "first name",
    MatchField.MIDDLE_NAME: "middle name",
    MatchField.DATE_OF_BIRTH: "date of birth",
    MatchField.LICENSE: "license",
    MatchField.ADDRESS: "address",
    MatchField.ORGANIZATION_NAME: "organization name",
}

_LEVEL_LABELS: Final = {
    MatchLevel.EXACT: "Exact match",
    MatchLevel.HIGH: "High-confidence match",
    MatchLevel.POTENTIAL: "Potential match",
    MatchLevel.AMBIGUOUS: "Ambiguous match",
    MatchLevel.NONE: "No match",
}


_CONFIRMED: Final = frozenset({MatchLevel.EXACT, MatchLevel.HIGH, MatchLevel.POTENTIAL})
"""Name levels that confirm a related NPI."""

_RELATED_NPI_NOTE: Final = (
    "the row publishes several NPIs without saying whose each is, so a person must confirm"
)
_RELATED_PARTY_NOTE: Final = (
    "the name is of another party on the row, not the listed party, so a person must confirm"
)
_OTHER_KIND_NPI: Final = "the NPIs of a person and of an organization never match"
_SHARED_ROW_NPI: Final = "first of several NPIs on the row, listed for another kind of party"


class _Decision(NamedTuple):
    level: MatchLevel
    evidence: tuple[MatchEvidence, ...]
    note: str | None = None
    relation: MatchRelation = MatchRelation.PARTY


def match_provider_to_record(provider: Provider, record: ExclusionRecord) -> MatchResult:
    """Decide how strongly ``record`` points at ``provider``, with the evidence for it.

    NPIs first, strongest rule first:

    1. The provider's NPI is the record's own NPI: EXACT. Except on a row that publishes
       several NPIs, when the provider is the other kind of party than the record: such rows
       list the NPIs in no reliable order (Washington writes a person's row with the
       business's NPI first), so the record's NPI may be the other party's, and rule 2 applies.
    2. The provider's NPI is one of the record's ``related_npis`` (other NPIs on the same
       row, which never says whose each is): HIGH when the names below confirm the provider
       (at least POTENTIAL, through the party, an alias or a related party), otherwise
       POTENTIAL ("name not confirmed"). Never EXACT: a related NPI without matching identity
       information goes to a person. The relation is that of the confirming name, never of
       the NPI, since the row does not say whose NPI is whose: PARTY through the party or an
       alias, RELATED_PARTY through a related party. HIGH with RELATED_PARTY becomes the
       "flagged" finding, never "listed".
    3. Both NPIs present and different, for the same kind of party (a provider with a person's
       name counts as an individual): NONE, names are not consulted.
    4. Otherwise, including NPIs of different kinds of party (a person's NPI never equals an
       organization's) and an NPI missing on either side, the names decide; with NPIs of
       different kinds the evidence says so.

    Names: the record's party name and its aliases (other names of the same party) are the
    party's names. For organizations, equal names are POTENTIAL, compared without legal
    designators such as "Inc" or "LLC". For individuals, equal last names where one first
    name is only the other's initial is AMBIGUOUS, and equal last and first names are
    POTENTIAL, then settled in this order, strongest identifier first:

    1. Date of birth (both present): different is NONE; equal is HIGH, even when the middle
       names conflict (the conflict stays in the evidence).
    2. Middle name (both present, compared on the record's own name, not an alias):
       conflicting full names or initials are AMBIGUOUS, because lists contain data-entry
       errors and a human decides; equal names, or an initial equal to the other's first
       letter, are HIGH.
    3. License (same number and state): HIGH.

    A licence that matches when the names do not (a licence-only candidate) is AMBIGUOUS, never
    NONE and never a finding on its own; a date of birth that differs still rules it out. The
    New York list showed why: licence numbers are unique only within a profession, not within
    a state. 46 numbers were shared by different people, 42 of them across professions (a
    chiropractor and a massage therapist with the same number), and 4 of those groups carried
    two different NPIs, so a licence number alone cannot identify a person. ``License.kind``
    is not compared yet because the sources name professions in different vocabularies;
    comparing it once they are normalized is the refinement that can settle these cases.

    A name that matches only one of the record's ``related_parties`` (another party named on
    the row: an owner, an affiliated business) is at most POTENTIAL, and no date of birth,
    middle name or licence raises it, because those belong to the listed party. Such a match
    carries ``MatchRelation.RELATED_PARTY``; every other match is ``PARTY``.

    A name-only match is never EXACT.
    """
    if provider.npi and provider.npi == record.npi:
        if record.related_npis and _other_kind(provider, record):
            return _conclude(_match_related_npi(provider, record, _SHARED_ROW_NPI))
        evidence = (MatchEvidence(field=MatchField.NPI, matched=True),)
        return _conclude(_result(MatchLevel.EXACT, evidence))
    if provider.npi and provider.npi in record.related_npis:
        return _conclude(_match_related_npi(provider, record, "related NPI"))
    if provider.npi and record.npi:
        if _same_kind(provider, record):
            evidence = (MatchEvidence(field=MatchField.NPI, matched=False),)
            return _conclude(_result(MatchLevel.NONE, evidence))
        by_name = _match_by_name(provider, record)
        npi = MatchEvidence(field=MatchField.NPI, matched=False, detail=_OTHER_KIND_NPI)
        return _conclude(by_name._replace(evidence=(npi, *by_name.evidence)))
    return _conclude(_match_by_name(provider, record))


def candidate_keys(record: ExclusionRecord) -> frozenset[str]:
    """Normalized names under which ``record`` can be found as a candidate.

    The last name, the organization name without its legal designators, and each alias and
    related party whole, without legal designators and word by word, so a lookup by
    normalized last name or organization name finds every record the matcher could match by
    name.
    """
    keys: set[str] = set()
    if record.person_name:
        keys.add(normalize_name(record.person_name.last))
    if record.organization_name:
        keys.add(normalize_organization_name(record.organization_name))
    for alias in (*record.aliases, *record.related_parties):
        normalized = normalize_name(alias)
        keys.add(normalized)
        keys.add(normalize_organization_name(alias))
        keys.update(normalized.split())
    keys.discard("")
    return frozenset(keys)


def party_key(record: ExclusionRecord) -> str | None:
    """The record's own party under :func:`candidate_keys`: the normalized last name of a
    person, or the organization name without legal designators; None when it has neither."""
    if record.person_name:
        return normalize_name(record.person_name.last) or None
    if record.organization_name:
        return normalize_organization_name(record.organization_name) or None
    return None


def shares_party_name(record: ExclusionRecord, other: ExclusionRecord) -> bool:
    """Whether ``other`` names the party of ``record``, by name alone (a person must decide).

    For a person: the same last name and first name (or one first name only the other's
    initial) on ``other``'s own name, or every word of them in one of its aliases or related
    parties; a person listed without a first name only matches another without one. For an
    organization: the same name without legal designators, on ``other``'s own name, an alias
    or a related party.
    """
    others = (*other.aliases, *other.related_parties)
    if record.person_name:
        last = normalize_name(record.person_name.last)
        first = normalize_name(record.person_name.first)
        if not last:
            return False
        if other.person_name and normalize_name(other.person_name.last) == last:
            other_first = normalize_name(other.person_name.first)
            if first == other_first or (first and other_first and _initial_of(first, other_first)):
                return True
        return bool(first) and _matching_alias(others, last, first) is not None
    if record.organization_name:
        wanted = normalize_organization_name(record.organization_name)
        names = (other.organization_name, *others) if other.organization_name else others
        return bool(wanted) and any(normalize_organization_name(n) == wanted for n in names)
    return False


def _provider_kind(provider: Provider) -> EntityKind:
    """The kind of party matching treats the provider as, following which name it compares."""
    if provider.entity_kind is not EntityKind.ORGANIZATION and provider.person_name:
        return EntityKind.INDIVIDUAL
    if provider.organization_name:
        return EntityKind.ORGANIZATION
    return EntityKind.UNKNOWN


def _same_kind(provider: Provider, record: ExclusionRecord) -> bool:
    kind = _provider_kind(provider)
    return kind is not EntityKind.UNKNOWN and kind is record.entity_kind


def _other_kind(provider: Provider, record: ExclusionRecord) -> bool:
    """Both kinds are known and differ (a person against a business, or the reverse)."""
    kind = _provider_kind(provider)
    known = {kind, record.entity_kind}.isdisjoint({EntityKind.UNKNOWN})
    return known and kind is not record.entity_kind


def _match_by_name(provider: Provider, record: ExclusionRecord) -> _Decision:
    if _provider_kind(provider) is EntityKind.INDIVIDUAL and provider.person_name:
        return _match_individual(provider, provider.person_name, record)
    if provider.organization_name:
        return _match_organization(provider, provider.organization_name, record)
    return _result(MatchLevel.NONE, ())


def _match_related_npi(provider: Provider, record: ExclusionRecord, detail: str) -> _Decision:
    """Rule 2: an NPI whose owner the row does not say is HIGH with a confirming name,
    otherwise POTENTIAL; ``detail`` says which of the row's NPIs it was. The relation is the
    name's, since the NPI alone does not say which party it belongs to."""
    by_name = _match_by_name(provider, record)
    if by_name.level in _CONFIRMED:
        npi = MatchEvidence(field=MatchField.NPI, matched=True, detail=detail)
        return _result(MatchLevel.HIGH, (npi, *by_name.evidence), relation=by_name.relation)
    npi = MatchEvidence(field=MatchField.NPI, matched=True, detail=f"{detail}; name not confirmed")
    return _result(
        MatchLevel.POTENTIAL,
        (npi, *by_name.evidence),
        note=_RELATED_NPI_NOTE,
        relation=by_name.relation,
    )


def _match_individual(provider: Provider, name: PersonName, record: ExclusionRecord) -> _Decision:
    last = normalize_name(name.last)
    first = normalize_name(name.first)
    if not last or not first:
        return _result(MatchLevel.NONE, ())

    level, evidence = _compare_person(last, first, record.person_name)
    middle = None
    if level is MatchLevel.POTENTIAL and record.person_name:
        middle = _compare_middle(name.middle, record.person_name.middle)
        if middle:
            evidence.append(middle)
    if level is MatchLevel.NONE:
        alias = _matching_alias(record.aliases, last, first)
        if alias is not None:
            level = MatchLevel.POTENTIAL
            evidence = [
                MatchEvidence(field=MatchField.LAST_NAME, matched=True, detail=f"alias {alias!r}"),
                MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail=f"alias {alias!r}"),
            ]
    if level is MatchLevel.NONE:
        party = _matching_alias(record.related_parties, last, first)
        if party is not None:
            detail = f"related party {party!r}"
            related = (
                MatchEvidence(field=MatchField.LAST_NAME, matched=True, detail=detail),
                MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail=detail),
            )
            return _result(
                MatchLevel.POTENTIAL,
                related,
                note=_RELATED_PARTY_NOTE,
                relation=MatchRelation.RELATED_PARTY,
            )
    if level is MatchLevel.NONE:
        return _license_only(provider, record, evidence, MatchField.LAST_NAME)

    same_birth_date = False
    if provider.date_of_birth and record.date_of_birth:
        same_birth_date = provider.date_of_birth == record.date_of_birth
        evidence.append(MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=same_birth_date))
        if not same_birth_date:
            return _result(MatchLevel.NONE, tuple(evidence))

    listed = record.license
    same_license = listed is not None and any(
        _same_license(own, listed) for own in provider.licenses
    )
    if same_license:
        evidence.append(MatchEvidence(field=MatchField.LICENSE, matched=True))

    if level is MatchLevel.POTENTIAL:
        if same_birth_date:
            level = MatchLevel.HIGH
        elif middle and not middle.matched:
            level = MatchLevel.AMBIGUOUS
        elif (middle and middle.matched) or same_license:
            level = MatchLevel.HIGH
    return _result(level, tuple(evidence))


def _compare_person(
    last: str, first: str, other: PersonName | None
) -> tuple[MatchLevel, list[MatchEvidence]]:
    if other is None or normalize_name(other.last) != last:
        return MatchLevel.NONE, []
    evidence = [MatchEvidence(field=MatchField.LAST_NAME, matched=True)]
    other_first = normalize_name(other.first)
    if other_first == first:
        evidence.append(MatchEvidence(field=MatchField.FIRST_NAME, matched=True))
        return MatchLevel.POTENTIAL, evidence
    if other_first and _initial_of(first, other_first):
        evidence.append(
            MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail="initial only")
        )
        return MatchLevel.AMBIGUOUS, evidence
    evidence.append(MatchEvidence(field=MatchField.FIRST_NAME, matched=False))
    return MatchLevel.NONE, evidence


def _compare_middle(own: str | None, other: str | None) -> MatchEvidence | None:
    """Middle-name evidence, or None when either side has no middle name."""
    own_middle = normalize_name(own) if own else ""
    other_middle = normalize_name(other) if other else ""
    if not own_middle or not other_middle:
        return None
    if own_middle == other_middle:
        return MatchEvidence(field=MatchField.MIDDLE_NAME, matched=True)
    if _initial_of(own_middle, other_middle):
        return MatchEvidence(field=MatchField.MIDDLE_NAME, matched=True, detail="initial only")
    return MatchEvidence(field=MatchField.MIDDLE_NAME, matched=False)


def _initial_of(first: str, other: str) -> bool:
    """Whether one name is a single initial that starts the other."""
    shorter, longer = sorted((first, other), key=len)
    return len(shorter) == 1 and longer.startswith(shorter)


def _matching_alias(aliases: Sequence[str], last: str, first: str) -> str | None:
    """The first alias containing every word of the last and the first name, if any."""
    wanted = set(last.split()) | set(first.split())
    for alias in aliases:
        if wanted <= set(normalize_name(alias).split()):
            return alias
    return None


def _match_organization(
    provider: Provider, organization_name: str, record: ExclusionRecord
) -> _Decision:
    """Equal names after dropping legal designators are POTENTIAL; the evidence says so."""
    wanted = normalize_organization_name(organization_name)
    if not wanted:
        return _result(MatchLevel.NONE, ())
    plain = normalize_name(organization_name)
    names: list[tuple[str, str | None, bool]] = (
        [(record.organization_name, None, False)] if record.organization_name else []
    )
    names += [(alias, f"alias {alias!r}", False) for alias in record.aliases]
    names += [(party, f"related party {party!r}", True) for party in record.related_parties]
    for name, via, related in names:
        if normalize_organization_name(name) != wanted:
            continue
        notes = [via] if via else []
        if normalize_name(name) != plain:
            notes.append("legal suffix ignored")
        evidence = MatchEvidence(
            field=MatchField.ORGANIZATION_NAME, matched=True, detail="; ".join(notes) or None
        )
        if related:
            return _result(
                MatchLevel.POTENTIAL,
                (evidence,),
                note=_RELATED_PARTY_NOTE,
                relation=MatchRelation.RELATED_PARTY,
            )
        return _result(MatchLevel.POTENTIAL, (evidence,))
    return _license_only(provider, record, [], MatchField.ORGANIZATION_NAME)


_LICENSE_ONLY_NOTE: Final = (
    "licence numbers are unique only within a profession, so a person must confirm"
)


def _license_only(
    provider: Provider,
    record: ExclusionRecord,
    name_evidence: list[MatchEvidence],
    name_field: MatchField,
) -> _Decision:
    """The names did not match: a shared licence makes it AMBIGUOUS, otherwise NONE.

    ``name_evidence`` is what the name comparison found; when it found nothing, the evidence
    records ``name_field`` as not matched so the explanation says why a person must look.
    """
    listed = record.license
    if listed is None or not any(_same_license(own, listed) for own in provider.licenses):
        return _result(MatchLevel.NONE, tuple(name_evidence))
    evidence = [
        MatchEvidence(field=MatchField.LICENSE, matched=True, detail="same state and number"),
        *(name_evidence or [MatchEvidence(field=name_field, matched=False)]),
    ]
    if provider.date_of_birth and record.date_of_birth:
        same_birth_date = provider.date_of_birth == record.date_of_birth
        evidence.append(MatchEvidence(field=MatchField.DATE_OF_BIRTH, matched=same_birth_date))
        if not same_birth_date:
            return _result(MatchLevel.NONE, tuple(evidence))
    return _result(MatchLevel.AMBIGUOUS, tuple(evidence), note=_LICENSE_ONLY_NOTE)


def license_key(number: str) -> str:
    """A licence number as matching compares it: whitespace removed, upper case."""
    return "".join(number.split()).upper()


def _same_license(own: License, other: License) -> bool:
    return own.state == other.state and license_key(own.number) == license_key(other.number)


def _result(
    level: MatchLevel,
    evidence: tuple[MatchEvidence, ...],
    *,
    note: str | None = None,
    relation: MatchRelation = MatchRelation.PARTY,
) -> _Decision:
    return _Decision(level, evidence, note, relation)


def _conclude(decision: _Decision) -> MatchResult:
    level, evidence, note, relation = decision
    return MatchResult(
        level=level,
        evidence=evidence,
        explanation=_explain(level, evidence, note),
        relation=relation,
    )


def _explain(level: MatchLevel, evidence: tuple[MatchEvidence, ...], note: str | None) -> str:
    if not evidence:
        return f"{_LEVEL_LABELS[level]}: no identifying field matches."
    parts = []
    for item in evidence:
        part = f"{_FIELD_LABELS[item.field]} {'matches' if item.matched else 'differs'}"
        parts.append(f"{part} ({item.detail})" if item.detail else part)
    ending = f"; {note}" if note else ""
    return f"{_LEVEL_LABELS[level]}: {', '.join(parts)}{ending}."


# --- Record status -------------------------------------------------------------------------

RECORD_STATUS_DETAIL: Final = "record_status"
"""The result detail holding the :class:`RecordStatus` of the records a check matched."""


def record_has_ended(record: ExclusionRecord, today: date) -> bool:
    """Whether the record no longer stands: its end date is before ``today``, or it is itself a
    reinstatement (Pennsylvania keeps one row per action, so a reinstatement is a row of its
    own). An end date of today or later, or none, means the record stands."""
    if record.action is ExclusionAction.REINSTATEMENT:
        return True
    return record.end_date is not None and record.end_date < today


def record_status(records: Sequence[ExclusionRecord], today: date) -> RecordStatus:
    """``ENDED`` only when every record has ended; one standing record makes it ``ACTIVE``."""
    if records and all(record_has_ended(record, today) for record in records):
        return RecordStatus.ENDED
    return RecordStatus.ACTIVE


# --- Verdict -------------------------------------------------------------------------------

_FOUND_FINDINGS: Final = frozenset({Finding.RECORD_FOUND, Finding.RELATED_PARTY})
"""Findings of a record that names the provider; once it has ended it goes to review."""


def derive_verdict(latest_per_source: Sequence[VerificationResult]) -> ProviderVerdict:
    """Roll the latest result of each source up into the provider's verdict.

    The most serious answer wins: a found record that still stands is LISTED; otherwise a
    standing record of another party that names the provider (``Finding.RELATED_PARTY``) is
    FLAGGED; otherwise an ambiguous match, or a found or related-party record that has ended
    ("previously listed" or "previously flagged", see :func:`record_status`), is NEEDS_REVIEW;
    otherwise a source that could not be checked is COULD_NOT_CHECK, never clean; NO_RECORDS
    only when every source answered with none; NOT_CHECKED without results. Should a source
    appear twice, its newest result counts. A result without a record status (stored before it
    existed) counts as standing.
    """
    newest: dict[str, VerificationResult] = {}
    for result in latest_per_source:
        kept = newest.get(result.source_id)
        if kept is None or (result.created_at, str(result.id)) > (kept.created_at, str(kept.id)):
            newest[result.source_id] = result
    results = tuple(newest[source_id] for source_id in sorted(newest))

    listed_in = tuple(
        r.source_id for r in results if r.finding is Finding.RECORD_FOUND and not _ended(r)
    )
    flagged_in = tuple(
        r.source_id for r in results if r.finding is Finding.RELATED_PARTY and not _ended(r)
    )
    review_in = tuple(
        r.source_id
        for r in results
        if r.finding is Finding.AMBIGUOUS or (r.finding in _FOUND_FINDINGS and _ended(r))
    )
    unchecked = tuple(r.source_id for r in results if r.outcome is not Outcome.COMPLETED)
    if listed_in:
        verdict = Verdict.LISTED
    elif flagged_in:
        verdict = Verdict.FLAGGED
    elif review_in:
        verdict = Verdict.NEEDS_REVIEW
    elif unchecked:
        verdict = Verdict.COULD_NOT_CHECK
    elif results:
        verdict = Verdict.NO_RECORDS
    else:
        verdict = Verdict.NOT_CHECKED

    list_dates = [
        r.provenance.source_updated_at
        for r in results
        if r.outcome is Outcome.COMPLETED and r.provenance.source_updated_at is not None
    ]
    return ProviderVerdict(
        verdict=verdict,
        sources_checked=len(results),
        listed_in=listed_in,
        flagged_in=flagged_in,
        review_in=review_in,
        unchecked=unchecked,
        oldest_list_updated_at=min(list_dates, default=None),
        last_checked_at=max((r.created_at for r in results), default=None),
        results=results,
    )


def _ended(result: VerificationResult) -> bool:
    return result.details.get(RECORD_STATUS_DETAIL) == RecordStatus.ENDED.value
