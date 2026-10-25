"""Domain policies: freshness windows and provider-to-record matching.

Freshness numbers live only here. Matching is deterministic and explainable: every rule that
fires leaves a piece of evidence, and the explanation is built from that evidence.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Final, Self

from pydantic import Field, model_validator

from credentialing.domain.enums import (
    CredentialType,
    EntityKind,
    Freshness,
    MatchField,
    MatchLevel,
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
)
from credentialing.domain.names import normalize_name
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


def match_provider_to_record(provider: Provider, record: ExclusionRecord) -> MatchResult:
    """Decide how strongly ``record`` points at ``provider``, with the evidence for it.

    Both NPIs present decides alone: equal is EXACT, different is NONE. Otherwise names
    decide (the record's aliases count as names). For organizations, equal names are
    POTENTIAL. For individuals, equal last names where one first name is only the other's
    initial is AMBIGUOUS, and equal last and first names are POTENTIAL, then settled in this
    order, strongest identifier first:

    1. Date of birth (both present): different is NONE; equal is HIGH, even when the middle
       names conflict (the conflict stays in the evidence).
    2. Middle name (both present, compared on the record's own name, not an alias):
       conflicting full names or initials are AMBIGUOUS, because lists contain data-entry
       errors and a human decides; equal names, or an initial equal to the other's first
       letter, are HIGH.
    3. License (same number and state): HIGH.

    A name-only match is never EXACT.
    """
    if provider.npi and record.npi:
        matched = provider.npi == record.npi
        evidence = (MatchEvidence(field=MatchField.NPI, matched=matched),)
        return _result(MatchLevel.EXACT if matched else MatchLevel.NONE, evidence)

    if provider.entity_kind is not EntityKind.ORGANIZATION and provider.person_name:
        return _match_individual(provider, provider.person_name, record)
    if provider.organization_name:
        return _match_organization(provider.organization_name, record)
    return _result(MatchLevel.NONE, ())


def candidate_keys(record: ExclusionRecord) -> frozenset[str]:
    """Normalized names under which ``record`` can be found as a candidate.

    The last name, the organization name, and each alias both whole and word by word, so a
    lookup by normalized last name or organization name finds every record the matcher could
    match by name.
    """
    keys: set[str] = set()
    if record.person_name:
        keys.add(normalize_name(record.person_name.last))
    if record.organization_name:
        keys.add(normalize_name(record.organization_name))
    for alias in record.aliases:
        normalized = normalize_name(alias)
        keys.add(normalized)
        keys.update(normalized.split())
    keys.discard("")
    return frozenset(keys)


def _match_individual(provider: Provider, name: PersonName, record: ExclusionRecord) -> MatchResult:
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
        return _result(MatchLevel.NONE, tuple(evidence))

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


def _matching_alias(aliases: tuple[str, ...], last: str, first: str) -> str | None:
    """The first alias containing every word of the last and the first name, if any."""
    wanted = set(last.split()) | set(first.split())
    for alias in aliases:
        if wanted <= set(normalize_name(alias).split()):
            return alias
    return None


def _match_organization(organization_name: str, record: ExclusionRecord) -> MatchResult:
    wanted = normalize_name(organization_name)
    if not wanted:
        return _result(MatchLevel.NONE, ())
    if record.organization_name and normalize_name(record.organization_name) == wanted:
        evidence = MatchEvidence(field=MatchField.ORGANIZATION_NAME, matched=True)
        return _result(MatchLevel.POTENTIAL, (evidence,))
    for alias in record.aliases:
        if normalize_name(alias) == wanted:
            evidence = MatchEvidence(
                field=MatchField.ORGANIZATION_NAME, matched=True, detail=f"alias {alias!r}"
            )
            return _result(MatchLevel.POTENTIAL, (evidence,))
    return _result(MatchLevel.NONE, ())


def _same_license(own: License, other: License) -> bool:
    def number(value: str) -> str:
        return "".join(value.split()).upper()

    return own.state == other.state and number(own.number) == number(other.number)


def _result(level: MatchLevel, evidence: tuple[MatchEvidence, ...]) -> MatchResult:
    return MatchResult(level=level, evidence=evidence, explanation=_explain(level, evidence))


def _explain(level: MatchLevel, evidence: tuple[MatchEvidence, ...]) -> str:
    if not evidence:
        return f"{_LEVEL_LABELS[level]}: no identifying field matches."
    parts = []
    for item in evidence:
        part = f"{_FIELD_LABELS[item.field]} {'matches' if item.matched else 'differs'}"
        parts.append(f"{part} ({item.detail})" if item.detail else part)
    return f"{_LEVEL_LABELS[level]}: {', '.join(parts)}."
