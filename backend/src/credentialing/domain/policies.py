"""Domain policies: freshness windows, provider-to-record matching, record-to-NPI linking, the
verdict roll-up and the monitored provider's status.

Freshness numbers live only here. Matching is deterministic and explainable: every rule that
fires leaves a piece of evidence, and the explanation is built from that evidence. The verdict
is decided here once, so no screen ever derives a status of its own.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Final, NamedTuple, Self

from pydantic import Field, model_validator

from credentialing.domain.enums import (
    Attention,
    CredentialType,
    EntityKind,
    ExclusionAction,
    Finding,
    Freshness,
    IdentityState,
    MatchField,
    MatchLevel,
    MatchRelation,
    NpiStatus,
    Outcome,
    RecordStatus,
    RegistryStatus,
    RosterRole,
    Verdict,
    VerificationMethod,
)
from credentialing.domain.models import (
    Address,
    DomainModel,
    ExclusionRecord,
    License,
    LinkParty,
    MatchEvidence,
    MatchResult,
    NpiRecord,
    NpiTaxonomy,
    PersonName,
    Provider,
    ProviderStatus,
    ProviderVerdict,
    ResultChange,
    SourceSnapshot,
    VerificationResult,
)
from credentialing.domain.names import normalize_name, normalize_organization_name
from credentialing.domain.types import Npi, SourceId

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


def poll_anchor(
    latest: SourceSnapshot | None, completed: SourceSnapshot | None
) -> SourceSnapshot | None:
    """The fetch a source's next poll interval counts from; ``None`` when it is due now.

    Normally the latest completed fetch. A ``BLOCKED`` fetch newer than it (or with none
    completed) counts instead, so a source whose firewall refuses us is asked again one poll
    interval later, not on every scheduled run. ``FAILED`` and ``SOURCE_UNAVAILABLE`` do not
    count: the source stays due until a fetch completes.
    """
    if (
        latest is not None
        and latest.outcome is Outcome.BLOCKED
        and (completed is None or latest.fetched_at > completed.fetched_at)
    ):
        return latest
    return completed


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
        # The NPPES files are monthly (full, deactivations) and weekly; a week of slack each.
        (CredentialType.NPI, "nppes_monthly"): FreshnessPolicy(max_age=timedelta(days=35)),
        (CredentialType.NPI, "nppes_weekly"): FreshnessPolicy(max_age=timedelta(days=10)),
        (CredentialType.NPI, "nppes_deactivations"): FreshnessPolicy(max_age=timedelta(days=35)),
        # A live registry answer is live for its live window, then cached like any NPI result.
        (CredentialType.NPI, "nppes_api"): FreshnessPolicy(max_age=timedelta(days=30)),
        (CredentialType.STATE_LICENSE, None): FreshnessPolicy(max_age=timedelta(days=1)),
        (CredentialType.BOARD_CERTIFICATION, None): FreshnessPolicy(max_age=timedelta(days=30)),
    }
)
"""The single place freshness windows are declared."""


def live_check_needed(*, requested: bool, mirrored: bool, freshness: Freshness | None) -> bool:
    """Ask the live registry when the caller asks for it, when the mirror has no row (the only
    way to learn a new NPI), or when the mirror's load is STALE by the NPI policy.

    A mirror row with no file load known (``freshness`` None: rows the live registry wrote
    before any file was loaded) cannot be called fresh, so it is asked too. A fresh or cached
    mirror row is answered from the mirror, without a request.
    """
    return requested or not mirrored or freshness in (None, Freshness.STALE)


def registry_status(mirrored: NpiRecord | None, live_found: bool | None) -> RegistryStatus:
    """The one-word conclusion about an NPI from the mirror's row and the live answer.

    ``live_found`` is None when the live registry was not asked or did not answer. The live
    registry lists active NPIs only: found is ACTIVE whatever the mirror says (a reactivation
    the files have not shown yet); not found is DEACTIVATED when the mirror says so, NOT_FOUND
    without a mirror row, and MIRROR_ONLY_ACTIVE (a contradiction for a person) when the
    mirror lists it as active. Without a live answer the mirror decides, and with neither the
    answer is UNKNOWN, never "not found".
    """
    if live_found:
        return RegistryStatus.ACTIVE
    if mirrored is None:
        return RegistryStatus.UNKNOWN if live_found is None else RegistryStatus.NOT_FOUND
    if mirrored.status is NpiStatus.DEACTIVATED:
        return RegistryStatus.DEACTIVATED
    return RegistryStatus.ACTIVE if live_found is None else RegistryStatus.MIRROR_ONLY_ACTIVE


_CARRIED_FIELDS: Final = (
    "deactivation_date",
    "reactivation_date",
    "deactivation_reason",
    "replacement_npi",
)
"""Fields the live registry omits for an active NPI, kept from the mirror's row."""


def carry_forward(answer: NpiRecord, mirrored: NpiRecord | None) -> NpiRecord:
    """A live answer with the mirror's deactivation history kept: each field of
    :data:`_CARRIED_FIELDS` the answer lacks is taken from the mirror's row, so storing the
    answer loses nothing the files said."""
    if mirrored is None:
        return answer
    carried = {
        name: getattr(mirrored, name)
        for name in _CARRIED_FIELDS
        if getattr(answer, name) is None and getattr(mirrored, name) is not None
    }
    return answer.model_copy(update=carried) if carried else answer


def records_to_store(
    answers: Sequence[NpiRecord], mirrored: Mapping[Npi, NpiRecord]
) -> list[NpiRecord]:
    """The live answers to write into the mirror: newer wins, nothing is lost.

    An answer is stored when the mirror has no row for its NPI, or when its ``last_updated``
    is later than the mirror row's (a row without one counts as older), with the mirror's
    deactivation history carried forward (:func:`carry_forward`). An answer the mirror is at
    least as current as is not written: it confirms the mirror. An NPI the answers repeat is
    stored once, the first time.
    """
    stored: dict[Npi, NpiRecord] = {}
    for answer in answers:
        if answer.npi in stored:
            continue
        current = mirrored.get(answer.npi)
        if current is None or _newer(answer.last_updated, current.last_updated):
            stored[answer.npi] = carry_forward(answer, current)
    return list(stored.values())


def _newer(answer: date | None, mirrored: date | None) -> bool:
    return answer is not None and (mirrored is None or answer > mirrored)


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
    """A licence number as matching compares it: letters and digits only, upper case.

    The lists and NPPES write one licence differently ("036123456" against "036-123456" or
    "036.123456"), so punctuation and spaces do not count (owner, 2026-11-08). The SQL
    function ``license_key`` is the same rule, used on the database side of every comparison.
    """
    return "".join(char for char in number if char.isalnum()).upper()


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


# --- NPI linking ---------------------------------------------------------------------------

CANDIDATE_CAP: Final = 50
"""The most mirror rows linking weighs for one exclusion record. Discovery reports
``CANDIDATE_CAP + 1`` found when there are more ("more than 50 namesakes"), and returns the
rows sharing the strongest evidence (ZIP, licence, city) first."""

SAME_ZIP: Final = "same ZIP"
SAME_CITY: Final = "same city"
SAME_STATE: Final = "same state"

_ZIP5: Final = re.compile(r"[0-9]{5}")

_NO_CANDIDATE: Final = "no candidate with this name"
_NO_MATCHING_CANDIDATE: Final = "no candidate matches the name"
_UNKNOWN_PUBLISHED_NPI: Final = "published NPI not in the registry"
_NO_FIRST_NAME: Final = "no first name to search with"
_NO_NAME: Final = "no name to search with"
_TOO_MANY: Final = f"more than {CANDIDATE_CAP} namesakes"
_NAME_DIFFERS: Final = "name differs from the registry"


def level_rank(level: MatchLevel) -> int:
    """0 for ``EXACT`` up to 4 for ``NONE``: sorts links best first."""
    return _LEVEL_ORDER[level]


_LEVEL_ORDER: Final = {level: rank for rank, level in enumerate(MatchLevel)}


def party_of_record(record: ExclusionRecord) -> LinkParty:
    """What the linking rules see of an exclusion record: its names, aliases and published
    NPI, and its one licence and address (none when the list gives none)."""
    return LinkParty(
        entity_kind=record.entity_kind,
        person_name=record.person_name,
        organization_name=record.organization_name,
        aliases=record.aliases,
        npi=record.npi,
        licenses=(record.license,) if record.license else (),
        addresses=(record.address,) if record.address else (),
    )


def party_of_provider(provider: Provider) -> LinkParty:
    """What the linking rules see of a roster provider: its names, its NPI, every licence and
    every address. No aliases (the roster keeps none), and never the date of birth (the
    registry publishes none)."""
    return LinkParty(
        entity_kind=provider.entity_kind,
        person_name=provider.person_name,
        organization_name=provider.organization_name,
        npi=provider.npi,
        licenses=provider.licenses,
        addresses=provider.addresses,
    )


def match_record_to_npi(
    record: ExclusionRecord, candidate: NpiRecord, *, candidates: int
) -> MatchResult:
    """Decide how strongly an exclusion record points at an NPI of the mirror:
    :func:`match_party_to_npi` on the record's party (:func:`party_of_record`)."""
    return match_party_to_npi(party_of_record(record), candidate, candidates=candidates)


def match_party_to_npi(party: LinkParty, candidate: NpiRecord, *, candidates: int) -> MatchResult:
    """Decide how strongly a party (an exclusion record, a roster provider) points at an NPI
    of the mirror.

    ``candidates`` is how many mirror rows the discovery found for the party; a unique
    namesake counts for more than one of many. Deterministic and explainable like provider
    matching, and never EXACT by name (invariant 4).

    A party with an NPI (a record's published NPI) is compared with that NPI's row only:
    EXACT when the last name (or the organization name) also matches, HIGH "name differs from
    the registry" when it does not, since the list may carry a typo and a person should look.
    Otherwise the kinds must agree (a person against an individual NPI, a business against an
    organization NPI), and:

    - Individuals: the last name and the first name must match (on the party's own name, or
      every word of the candidate's in one of its aliases); a single initial starting the
      other counts as "initial only". The middle name (own name only), the licence (number by
      :func:`license_key` in the same state, against the candidate's taxonomy licences) and
      the address (5-digit ZIP, else city and state, else state, against the practice and
      mailing addresses) add evidence. With several licences or addresses each is compared
      and the best kept: a match over a difference, the strongest match (ZIP, city, state),
      else the first comparison that could be made. HIGH: full first name, licence or ZIP
      matched, middle name not contradicted. POTENTIAL: full first name and (same city,
      middle name matched, or the only namesake) with no contradiction; or an initial with
      licence or ZIP matched.
      AMBIGUOUS otherwise: several namesakes, or a contradiction (middle name, licence or
      address differs).
    - Organizations: the names without legal designators must match (own name or an alias,
      against the candidate's name or one of its other names). HIGH with the ZIP matched;
      POTENTIAL with the same city or as the only candidate, the address not contradicting;
      AMBIGUOUS otherwise.

    Nicknames are not resolved. A deactivated NPI is weighed like any other, and the
    explanation says it is deactivated.
    """
    if party.npi is not None:
        if candidate.npi != party.npi:
            evidence = (MatchEvidence(field=MatchField.NPI, matched=False),)
            return _conclude(_result(MatchLevel.NONE, evidence))
        decision = _published_npi(party, candidate)
    elif party.entity_kind is EntityKind.UNKNOWN or candidate.entity_kind is not party.entity_kind:
        return MatchResult(
            level=MatchLevel.NONE, evidence=(), explanation="No match: the kinds differ."
        )
    elif party.entity_kind is EntityKind.INDIVIDUAL:
        decision = _link_individual(party, candidate, candidates)
    else:
        decision = _link_organization(party, candidate, candidates)
    if decision.level is MatchLevel.NONE:
        return _conclude(decision)
    notes = [decision.note] if decision.note else []
    if party.npi is None and candidates > 1:
        many = f"more than {CANDIDATE_CAP}" if candidates > CANDIDATE_CAP else str(candidates)
        notes.append(f"one of {many} namesakes in the registry")
    if candidate.status is NpiStatus.DEACTIVATED:
        notes.append("the registry shows this NPI deactivated")
    return _conclude(decision._replace(note="; ".join(notes) or None))


def link_record_to_npis(
    record: ExclusionRecord, candidates: Sequence[NpiRecord], found: int
) -> tuple[tuple[Npi | None, MatchResult], ...]:
    """The links to store for one record: :func:`suggest_npis` on the record's party."""
    return suggest_npis(party_of_record(record), candidates, found)


def suggest_npis(
    party: LinkParty, candidates: Sequence[NpiRecord], found: int
) -> tuple[tuple[Npi | None, MatchResult], ...]:
    """The NPIs a party points at: ``(npi, match)`` pairs, best first.

    Every candidate is scored with :func:`match_party_to_npi` and NONE pairs are dropped.
    When the discovery found more than :data:`CANDIDATE_CAP` namesakes, AMBIGUOUS pairs are
    dropped too: the strongest are scored first, and an ambiguous one among so many says
    nothing a person could act on. A party left without a link gets one ``(None, NONE)``
    pair whose explanation says why, so "not linked" never reads as "never checked".
    """
    scored = [(c.npi, match_party_to_npi(party, c, candidates=found)) for c in candidates]
    kept = [(npi, match) for npi, match in scored if match.level is not MatchLevel.NONE]
    if found > CANDIDATE_CAP:
        kept = [(npi, match) for npi, match in kept if match.level in _CONFIRMED]
    if kept:
        ordered = sorted(kept, key=lambda pair: (level_rank(pair[1].level), pair[0] or ""))
        return tuple((npi, match) for npi, match in ordered)
    reason = _link_gap(party, found, scored=bool(scored))
    return (
        (None, MatchResult(level=MatchLevel.NONE, evidence=(), explanation=f"No match: {reason}.")),
    )


def _link_gap(party: LinkParty, found: int, *, scored: bool) -> str:
    """Why a party has no link, most basic reason first."""
    if party.npi is not None:
        return _UNKNOWN_PUBLISHED_NPI if not found else _NO_MATCHING_CANDIDATE
    if party.entity_kind is EntityKind.INDIVIDUAL:
        if party.person_name is None or not normalize_name(party.person_name.last):
            return _NO_NAME
        if not normalize_name(party.person_name.first):
            return _NO_FIRST_NAME
    elif not party.organization_name or not normalize_organization_name(party.organization_name):
        return _NO_NAME
    if found > CANDIDATE_CAP:
        return _TOO_MANY
    return _NO_MATCHING_CANDIDATE if scored else _NO_CANDIDATE


def _published_npi(party: LinkParty, candidate: NpiRecord) -> _Decision:
    """The party's own NPI is the candidate's: EXACT with a matching name, else HIGH."""
    npi = MatchEvidence(field=MatchField.NPI, matched=True, detail="published by the list")
    field = (
        MatchField.LAST_NAME
        if party.entity_kind is EntityKind.INDIVIDUAL
        else MatchField.ORGANIZATION_NAME
    )
    if candidate.entity_kind is EntityKind.UNKNOWN:
        detail = "the registry publishes no name for this NPI"
        return _result(
            MatchLevel.HIGH, (npi, MatchEvidence(field=field, matched=False, detail=detail))
        )
    if candidate.entity_kind is not party.entity_kind:
        other = "a person" if candidate.entity_kind is EntityKind.INDIVIDUAL else "an organization"
        detail = f"{_NAME_DIFFERS}: it lists {other} under this NPI"
        return _result(
            MatchLevel.HIGH, (npi, MatchEvidence(field=field, matched=False, detail=detail))
        )
    if party.entity_kind is EntityKind.INDIVIDUAL:
        names = _published_person(party, candidate)
    else:
        names = _organization_names(party, candidate)
    if names is None:
        differs = MatchEvidence(field=field, matched=False, detail=_NAME_DIFFERS)
        return _result(MatchLevel.HIGH, (npi, differs))
    return _result(MatchLevel.EXACT, (npi, *names.evidence))


def match_provider_to_npi(provider: Provider, record: NpiRecord) -> MatchResult:
    """How the registry's record of the provider's own NPI agrees with the roster.

    The NPI is the identity, so this never blocks on a contradiction; it says whether the name
    agrees, as :func:`match_record_to_npi` does for a published NPI. EXACT when the last name
    (a person) or the organization name without legal designators (or one of the registry's
    other names) matches; HIGH "name differs from the registry" otherwise, and HIGH when the
    registry publishes no name or lists the other kind of party under the NPI. The first and
    middle names and the licences are supporting evidence only. NONE when the NPIs differ.
    """
    if provider.npi != record.npi:
        return _conclude(
            _result(MatchLevel.NONE, (MatchEvidence(field=MatchField.NPI, matched=False),))
        )
    npi = MatchEvidence(field=MatchField.NPI, matched=True, detail="on the roster")
    kind = _provider_kind(provider)
    field = MatchField.LAST_NAME if kind is EntityKind.INDIVIDUAL else MatchField.ORGANIZATION_NAME
    if record.entity_kind is EntityKind.UNKNOWN:
        detail = "the registry publishes no name for this NPI"
        return _conclude(
            _result(
                MatchLevel.HIGH, (npi, MatchEvidence(field=field, matched=False, detail=detail))
            )
        )
    if record.entity_kind is not kind:
        other = "a person" if record.entity_kind is EntityKind.INDIVIDUAL else "an organization"
        detail = f"{_NAME_DIFFERS}: it lists {other} under this NPI"
        return _conclude(
            _result(
                MatchLevel.HIGH, (npi, MatchEvidence(field=field, matched=False, detail=detail))
            )
        )
    names = (
        _roster_person(provider, record)
        if kind is EntityKind.INDIVIDUAL
        else _roster_organization(provider, record)
    )
    if names is None:
        differs = MatchEvidence(field=field, matched=False, detail=_NAME_DIFFERS)
        return _conclude(_result(MatchLevel.HIGH, (npi, differs)))
    licence = _roster_license(provider, record)
    evidence = (npi, *names, *((licence,) if licence else ()))
    return _conclude(_result(MatchLevel.EXACT, evidence))


def _roster_person(provider: Provider, record: NpiRecord) -> tuple[MatchEvidence, ...] | None:
    """The last name must match; first and middle names are reported whatever they say."""
    own, theirs = provider.person_name, record.person_name
    if own is None or theirs is None:
        return None
    their_last = normalize_name(theirs.last)
    if not their_last or normalize_name(own.last) != their_last:
        return None
    evidence = [MatchEvidence(field=MatchField.LAST_NAME, matched=True)]
    first, their_first = normalize_name(own.first), normalize_name(theirs.first)
    if first and their_first:
        same = first == their_first
        initial = not same and _initial_of(first, their_first)
        evidence.append(
            MatchEvidence(
                field=MatchField.FIRST_NAME,
                matched=same or initial,
                detail="initial only" if initial else None,
            )
        )
    middle = _compare_middle(own.middle, theirs.middle)
    if middle:
        evidence.append(middle)
    return tuple(evidence)


def _roster_organization(provider: Provider, record: NpiRecord) -> tuple[MatchEvidence, ...] | None:
    """The roster's organization name against the registry's name or other names, without
    legal designators."""
    wanted = normalize_organization_name(provider.organization_name or "")
    if not wanted:
        return None
    theirs: list[tuple[str, str | None]] = (
        [(record.organization_name, None)] if record.organization_name else []
    )
    theirs += [(other, f"other name {other!r}") for other in record.other_names]
    for name, via in theirs:
        if normalize_organization_name(name) != wanted:
            continue
        notes = [via] if via else []
        if normalize_name(provider.organization_name or "") != normalize_name(name):
            notes.append("legal suffix ignored")
        detail = "; ".join(notes) or None
        return (MatchEvidence(field=MatchField.ORGANIZATION_NAME, matched=True, detail=detail),)
    return None


def _roster_license(provider: Provider, record: NpiRecord) -> MatchEvidence | None:
    """The roster's licences against the registry's taxonomy licences: matched when any one
    agrees, else the first comparison that could be made; None when none could."""
    return _license_evidence(provider.licenses, record)


def _published_person(party: LinkParty, candidate: NpiRecord) -> _PersonNames | None:
    """Name evidence under a published NPI: the last name is enough, the first name is
    reported whatever it says; an alias naming the candidate counts too."""
    own, theirs = party.person_name, candidate.person_name
    if own is None or theirs is None:
        return None
    their_last, their_first = normalize_name(theirs.last), normalize_name(theirs.first)
    if their_last and normalize_name(own.last) == their_last:
        first = normalize_name(own.first)
        evidence = [MatchEvidence(field=MatchField.LAST_NAME, matched=True)]
        if first and their_first:
            same = first == their_first
            initial = not same and _initial_of(first, their_first)
            evidence.append(
                MatchEvidence(
                    field=MatchField.FIRST_NAME,
                    matched=same or initial,
                    detail="initial only" if initial else None,
                )
            )
        return _PersonNames(tuple(evidence), full_first=True, middle=None)
    return _person_names(party, candidate)


class _PersonNames(NamedTuple):
    """How a party's person name compares with a candidate's."""

    evidence: tuple[MatchEvidence, ...]
    full_first: bool
    middle: MatchEvidence | None


def _person_names(party: LinkParty, candidate: NpiRecord) -> _PersonNames | None:
    """Last and first name evidence, or None when the names do not match.

    The party's own name first (first names equal, or one an initial starting the other),
    then its aliases (every word of the candidate's last and first name in one alias).
    """
    own, theirs = party.person_name, candidate.person_name
    if own is None or theirs is None:
        return None
    their_last, their_first = normalize_name(theirs.last), normalize_name(theirs.first)
    if not their_last or not their_first:
        return None
    last, first = normalize_name(own.last), normalize_name(own.first)
    if last == their_last and first and (first == their_first or _initial_of(first, their_first)):
        full = first == their_first and len(first) > 1
        evidence = [
            MatchEvidence(field=MatchField.LAST_NAME, matched=True),
            MatchEvidence(
                field=MatchField.FIRST_NAME, matched=True, detail=None if full else "initial only"
            ),
        ]
        middle = _compare_middle(own.middle, theirs.middle)
        if middle:
            evidence.append(middle)
        return _PersonNames(tuple(evidence), full_first=full, middle=middle)
    alias = _matching_alias(party.aliases, their_last, their_first)
    if alias is None:
        return None
    detail = f"alias {alias!r}"
    evidence = [
        MatchEvidence(field=MatchField.LAST_NAME, matched=True, detail=detail),
        MatchEvidence(field=MatchField.FIRST_NAME, matched=True, detail=detail),
    ]
    return _PersonNames(tuple(evidence), full_first=True, middle=None)


def _organization_names(party: LinkParty, candidate: NpiRecord) -> _PersonNames | None:
    """Organization-name evidence (in the :class:`_PersonNames` shape), or None.

    The party's name or one of its aliases against the candidate's name or one of its other
    names, compared without legal designators; the detail says which names and whether a
    designator was ignored.
    """
    ours: list[tuple[str, str | None]] = (
        [(party.organization_name, None)] if party.organization_name else []
    )
    ours += [(alias, f"alias {alias!r}") for alias in party.aliases]
    theirs: list[tuple[str, str | None]] = (
        [(candidate.organization_name, None)] if candidate.organization_name else []
    )
    theirs += [(other, f"other name {other!r}") for other in candidate.other_names]
    keyed = [(normalize_organization_name(name), name, via) for name, via in theirs]
    for name, via in ours:
        wanted = normalize_organization_name(name)
        if not wanted:
            continue
        for key, their_name, their_via in keyed:
            if key != wanted:
                continue
            notes: list[str] = [note for note in (via, their_via) if note]
            if normalize_name(name) != normalize_name(their_name):
                notes.append("legal suffix ignored")
            evidence = MatchEvidence(
                field=MatchField.ORGANIZATION_NAME, matched=True, detail="; ".join(notes) or None
            )
            return _PersonNames((evidence,), full_first=True, middle=None)
    return None


def _link_individual(party: LinkParty, candidate: NpiRecord, candidates: int) -> _Decision:
    names = _person_names(party, candidate)
    if names is None:
        return _result(MatchLevel.NONE, ())
    license = _license_evidence(party.licenses, candidate)
    address = _address_evidence(party.addresses, candidate)
    evidence = (*names.evidence, *(item for item in (license, address) if item))
    middle = names.middle
    middle_differs = middle is not None and not middle.matched
    contradiction = any(
        item is not None and not item.matched for item in (middle, license, address)
    )
    strong = (license is not None and license.matched) or _same(address, SAME_ZIP)
    if strong and not middle_differs:
        level = MatchLevel.HIGH if names.full_first else MatchLevel.POTENTIAL
    elif (
        names.full_first
        and not contradiction
        and (
            _same(address, SAME_ZIP, SAME_CITY)
            or (middle is not None and middle.matched)
            or candidates == 1
        )
    ):
        level = MatchLevel.POTENTIAL
    else:
        level = MatchLevel.AMBIGUOUS
    return _result(level, evidence)


def _link_organization(party: LinkParty, candidate: NpiRecord, candidates: int) -> _Decision:
    names = _organization_names(party, candidate)
    if names is None:
        return _result(MatchLevel.NONE, ())
    address = _address_evidence(party.addresses, candidate)
    evidence = (*names.evidence, *((address,) if address else ()))
    if _same(address, SAME_ZIP):
        level = MatchLevel.HIGH
    elif (address is None or address.matched) and (_same(address, SAME_CITY) or candidates == 1):
        level = MatchLevel.POTENTIAL
    else:
        level = MatchLevel.AMBIGUOUS
    return _result(level, evidence)


def _same(address: MatchEvidence | None, *details: str) -> bool:
    return address is not None and address.matched and address.detail in details


def _license_evidence(own: Sequence[License], candidate: NpiRecord) -> MatchEvidence | None:
    """The party's licences against the candidate's taxonomy licences; None when either side
    has none. Matched when any one agrees, else the first licence's difference. A licence
    only in other states is "not matched" too, the detail says so."""
    theirs = [t for t in candidate.taxonomies if t.license_number]
    if not own or not theirs:
        return None
    compared = [_one_license(licence, theirs) for licence in own]
    return next((item for item in compared if item.matched), compared[0])


def _one_license(own: License, theirs: Sequence[NpiTaxonomy]) -> MatchEvidence:
    number = license_key(own.number)
    if any(
        t.license_state == own.state and license_key(t.license_number or "") == number
        for t in theirs
    ):
        return MatchEvidence(field=MatchField.LICENSE, matched=True, detail="same state and number")
    if any(t.license_state == own.state for t in theirs):
        detail = f"another number in {own.state}"
    else:
        detail = f"no licence in {own.state} in the registry"
    return MatchEvidence(field=MatchField.LICENSE, matched=False, detail=detail)


_PLACE_STRENGTH: Final = {SAME_ZIP: 0, SAME_CITY: 1, SAME_STATE: 2}
"""Matched address evidence, strongest first: which of several addresses speaks for a party."""


def _address_evidence(own: Sequence[Address], candidate: NpiRecord) -> MatchEvidence | None:
    """The party's addresses against the candidate's practice and mailing addresses: the same
    5-digit ZIP, else the same city and state, else the same state; "differs" when both sides
    have something to compare and share nothing; None otherwise. Never the street. With
    several addresses the strongest match is kept, else the first difference."""
    places = [a for a in (candidate.practice_address, candidate.mailing_address) if a is not None]
    compared = [item for item in (_one_address(a, places) for a in own) if item is not None]
    matched = [item for item in compared if item.matched]
    if matched:
        return min(matched, key=lambda item: _PLACE_STRENGTH.get(item.detail or "", 3))
    return compared[0] if compared else None


def _one_address(own: Address, places: Sequence[Address]) -> MatchEvidence | None:
    zip_code = _zip5(own.postal_code)
    if not (zip_code or own.state) or not any(_zip5(a.postal_code) or a.state for a in places):
        return None
    if zip_code and any(_zip5(a.postal_code) == zip_code for a in places):
        return MatchEvidence(field=MatchField.ADDRESS, matched=True, detail=SAME_ZIP)
    city = normalize_name(own.city) if own.city else ""
    if (
        city
        and own.state
        and any(a.state == own.state and a.city and normalize_name(a.city) == city for a in places)
    ):
        return MatchEvidence(field=MatchField.ADDRESS, matched=True, detail=SAME_CITY)
    if own.state and any(a.state == own.state for a in places):
        return MatchEvidence(field=MatchField.ADDRESS, matched=True, detail=SAME_STATE)
    return MatchEvidence(field=MatchField.ADDRESS, matched=False)


def _zip5(postal_code: str | None) -> str | None:
    """The 5-digit ZIP a postal code starts with (``12345-6789``, ``123456789``), or None."""
    found = _ZIP5.match(postal_code.strip()) if postal_code else None
    return found.group() if found else None


# --- Record versions -----------------------------------------------------------------------

type RewriteKey = tuple[Npi | None, date | None]
"""A row's NPI and effective date: what a row the list rewrote keeps across the rewrite."""


def pair_rewritten_rows(
    vanished: Mapping[str, RewriteKey], added: Mapping[str, RewriteKey]
) -> dict[str, str]:
    """Pair rows the list rewrote in one sync: ``{added record_hash: vanished record_hash}``.

    A vanished identity and a new one are the same row with an identity field rewritten (a
    spelling, a licence number) when they share NPI and effective date and are the only
    vanished and the only new row with that pair. Rows without an NPI, and pairs shared by
    more than one row on either side, are not paired: they stay removed and added, for a
    person to weigh (owner decision 2026-10-30).
    """

    def by_key(rows: Mapping[str, RewriteKey]) -> dict[RewriteKey, list[str]]:
        grouped: dict[RewriteKey, list[str]] = {}
        for record_hash, key in rows.items():
            if key[0] is not None:
                grouped.setdefault(key, []).append(record_hash)
        return grouped

    old, new = by_key(vanished), by_key(added)
    return {
        new[key][0]: hashes[0]
        for key, hashes in sorted(old.items(), key=lambda item: item[1])
        if len(hashes) == 1 and len(new.get(key, ())) == 1
    }


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


def record_is_removed(record: ExclusionRecord) -> bool:
    """Whether the record left its published list (its newest version was closed). A list
    dropping a row does not say why, so this never counts as a reinstatement."""
    return record.valid_to is not None


def record_status(records: Sequence[ExclusionRecord], today: date) -> RecordStatus:
    """``ENDED`` when every record has ended; otherwise ``REMOVED`` when every record that has
    not ended was removed from its list; otherwise ``ACTIVE`` (one standing, listed record)."""
    if not records:
        return RecordStatus.ACTIVE
    standing = [record for record in records if not record_has_ended(record, today)]
    if not standing:
        return RecordStatus.ENDED
    if all(record_is_removed(record) for record in standing):
        return RecordStatus.REMOVED
    return RecordStatus.ACTIVE


# --- Verdict -------------------------------------------------------------------------------

_FOUND_FINDINGS: Final = frozenset({Finding.RECORD_FOUND, Finding.RELATED_PARTY})
"""Findings of a record that names the provider; once it has ended or was removed from its
list it goes to review."""

_NOT_STANDING: Final = frozenset({RecordStatus.ENDED.value, RecordStatus.REMOVED.value})
"""Record statuses of found records that no longer stand on a published list."""


def derive_verdict(latest_per_source: Sequence[VerificationResult]) -> ProviderVerdict:
    """Roll the latest result of each source up into the provider's verdict.

    The most serious answer wins: a found record that still stands is LISTED; otherwise a
    standing record of another party that names the provider (``Finding.RELATED_PARTY``) is
    FLAGGED; otherwise an ambiguous match, or a found or related-party record that has ended
    ("previously listed" or "previously flagged") or was removed from its published list
    (reinstatement not confirmed), see :func:`record_status`, is NEEDS_REVIEW;
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
        r.source_id for r in results if r.finding is Finding.RECORD_FOUND and not _not_standing(r)
    )
    flagged_in = tuple(
        r.source_id for r in results if r.finding is Finding.RELATED_PARTY and not _not_standing(r)
    )
    review_in = tuple(
        r.source_id
        for r in results
        if r.finding is Finding.AMBIGUOUS or (r.finding in _FOUND_FINDINGS and _not_standing(r))
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


def _not_standing(result: VerificationResult) -> bool:
    """Whether the result's records no longer stand: ended or removed from the list."""
    return result.details.get(RECORD_STATUS_DETAIL) in _NOT_STANDING


# --- Monitoring ----------------------------------------------------------------------------

MONITORING_INTERVAL: Final = timedelta(days=15)
"""How often every provider is rechecked against the exclusion lists and the NPI mirror."""

MONITORING_DUE_TOLERANCE: Final = timedelta(hours=1)
"""How early a recheck counts as due, so a daily job is not skipped because the previous one
finished a few minutes after this one started."""

HISTORY_RETENTION: Final = timedelta(days=183)
"""How far back a provider's verification history goes: six months (owner, 2026-11-12), so
the database does not grow without end. Older results are purged by the ``due`` and ``cli``
rechecks, except each provider's newest result per source and newest NPI result, whatever
their age: a verdict never disappears and change detection keeps its previous answer."""


def history_cutoff(now: datetime) -> datetime:
    """The instant before which results are purged: :data:`HISTORY_RETENTION` before ``now``."""
    return now - HISTORY_RETENTION


STATUS_RULES_VERSION: Final = 2
"""The version of :func:`derive_status`'s rules. Stored statuses of another version are
derived again before they are read; bump it whenever the rules change."""

NPI_STATUS_DETAIL: Final = "npi_status"
"""The NPI result detail holding the registry's one-word status (:class:`RegistryStatus`)."""

NO_NPI_REASON: Final = "no_npi"
"""The ``reason`` detail of an NPI result for a provider that had no NPI on file."""

_ATTENTION_RANK: Final = {attention: rank for rank, attention in enumerate(Attention)}

_VERDICT_ATTENTION: Final = {
    Verdict.LISTED: Attention.LISTED,
    Verdict.FLAGGED: Attention.FLAGGED,
    Verdict.NEEDS_REVIEW: Attention.NEEDS_REVIEW,
    Verdict.COULD_NOT_CHECK: Attention.COULD_NOT_CHECK,
    Verdict.NOT_CHECKED: Attention.NOT_CHECKED,
}

_IDENTITY_ATTENTION: Final = {
    IdentityState.NPI_DEACTIVATED: Attention.NPI_DEACTIVATED,
    IdentityState.NPI_UNCERTAIN: Attention.NPI_UNCERTAIN,
    IdentityState.NPI_MISSING: Attention.NPI_MISSING,
}


def attention_rank(attention: Attention | None) -> int:
    """The attention's priority, 0 the most serious (the enum's declared order); None (nothing
    needs a person) ranks after every reason. Storage orders the roster by this number."""
    return len(_ATTENTION_RANK) if attention is None else _ATTENTION_RANK[attention]


def result_change(
    previous: VerificationResult | None, new: VerificationResult
) -> ResultChange | None:
    """How the answer ``new`` differs from the previous answer ``previous``, described by it.

    Only answers are compared: a result that could not be checked (any outcome but
    ``COMPLETED``) never carries a change, and ``previous`` must be the previous *completed*
    result (of the same list; for NPI, of any NPPES source), so a change across a gap of
    could-not-check results is still caught (no record, unavailable, record found: changed from
    no record), while an outage or a list's first load is not a change. None for a first
    answer and when the finding, the matched records' status and the registry status (NPI
    results) are all the same.
    """
    if new.outcome is not Outcome.COMPLETED:
        return None
    if previous is None or previous.outcome is not Outcome.COMPLETED:
        return None
    before = _compared(previous)
    if before == _compared(new):
        return None
    finding, status, registry = before
    return ResultChange(
        outcome=previous.outcome,
        finding=finding,
        record_status=status,
        registry_status=registry,
        verified_at=previous.provenance.verified_at,
    )


def _compared(
    result: VerificationResult,
) -> tuple[Finding | None, RecordStatus | None, RegistryStatus | None]:
    """What :func:`result_change` compares: unknown detail values (none stored today) read as
    absent rather than fail."""
    status = result.details.get(RECORD_STATUS_DETAIL)
    registry = (
        result.details.get(NPI_STATUS_DETAIL)
        if result.credential_type is CredentialType.NPI
        else None
    )
    return (
        result.finding,
        RecordStatus(status) if status is not None and status in RecordStatus else None,
        RegistryStatus(registry) if registry is not None and registry in RegistryStatus else None,
    )


def derive_status(
    provider: Provider,
    exclusion_latest: Sequence[VerificationResult],
    npi_latest: VerificationResult | None,
    *,
    lists: Collection[SourceId] | None = None,
    last_change_at: datetime | None = None,
) -> ProviderStatus:
    """The provider's monitoring status from the latest exclusion result of each list and the
    latest NPI result.

    The verdict is :func:`derive_verdict`'s; the identity comes from the role, the NPI on file
    and the latest NPI result (:func:`identity_state`). ``changed`` holds while a result that
    differs from the one before it (``changed_from``) is newer than ``provider.last_viewed_at``:
    ``last_change_at`` is the newest such result in the provider's whole history, so a later
    recheck that finds nothing new does not hide a change nobody has seen; ``changed_at`` is
    that newest change, seen or not. ``attention`` is
    the first reason in :class:`Attention` order that applies.

    ``last_checked_at`` is the newest result. The recheck is due (``next_check_at``)
    :data:`MONITORING_INTERVAL` after the *oldest* result it would renew: each list's (only the
    connected ``lists`` when any are given, so a disconnected list's last answer does not keep
    the provider due forever) and the NPI's when one is on file; None (due now) when one of them
    has no result yet. A recheck of one list after its sync renews that list only, so it never
    postpones the full recheck.
    """
    verdict = derive_verdict(exclusion_latest)
    identity = identity_state(provider, npi_latest)
    results = [*verdict.results, *([npi_latest] if npi_latest is not None else [])]
    change_times = [r.created_at for r in results if r.changed_from is not None]
    if last_change_at is not None:
        change_times.append(last_change_at)
    changed_at = max(change_times, default=None)
    changed = changed_at is not None and (
        provider.last_viewed_at is None or changed_at > provider.last_viewed_at
    )
    reasons = {
        _VERDICT_ATTENTION.get(verdict.verdict),
        Attention.CHANGED if changed else None,
        _IDENTITY_ATTENTION.get(identity),
    }
    attention = next((reason for reason in Attention if reason in reasons), None)
    last_checked_at = max((r.created_at for r in results), default=None)
    return ProviderStatus(
        provider_id=provider.id,
        verdict=verdict.verdict,
        identity=identity,
        changed=changed,
        attention=attention,
        last_checked_at=last_checked_at,
        next_check_at=_next_check(provider, verdict.results, npi_latest, lists),
        listed_in=verdict.listed_in,
        flagged_in=verdict.flagged_in,
        review_in=verdict.review_in,
        unchecked=verdict.unchecked,
        changed_at=changed_at,
    )


def _next_check(
    provider: Provider,
    exclusion_latest: Sequence[VerificationResult],
    npi_latest: VerificationResult | None,
    lists: Collection[SourceId] | None,
) -> datetime | None:
    """:data:`MONITORING_INTERVAL` after the oldest result a recheck renews; None when one of
    them is missing (or nothing was ever checked)."""
    by_list = {r.source_id: r.created_at for r in exclusion_latest}
    wanted = set(lists) if lists else set(by_list)
    if not by_list or not wanted <= set(by_list):
        return None
    renewed = [by_list[source_id] for source_id in wanted]
    if provider.npi is not None:
        if npi_latest is None:
            return None
        renewed.append(npi_latest.created_at)
    return min(renewed) + MONITORING_INTERVAL if renewed else None


def identity_state(provider: Provider, npi_latest: VerificationResult | None) -> IdentityState:
    """What the roster knows about the provider's NPI.

    Without an NPI: missing for a practitioner, not expected for a workforce member or vendor.
    With one: unverified until an NPI result checked it (a result from before the NPI was set
    does not count); active or deactivated as the registry's status says; uncertain for any
    other answer (a contradiction to review, not in NPPES, could not tell, or a check that
    could not run).
    """
    if provider.npi is None:
        if provider.role is RosterRole.PRACTITIONER:
            return IdentityState.NPI_MISSING
        return IdentityState.NOT_EXPECTED
    if npi_latest is None or npi_latest.details.get("reason") == NO_NPI_REASON:
        return IdentityState.NPI_UNVERIFIED
    if npi_latest.outcome is Outcome.COMPLETED:
        match npi_latest.details.get(NPI_STATUS_DETAIL):
            case RegistryStatus.ACTIVE.value:
                return IdentityState.NPI_ACTIVE
            case RegistryStatus.DEACTIVATED.value:
                return IdentityState.NPI_DEACTIVATED
    return IdentityState.NPI_UNCERTAIN


def recheck_due(next_check_at: datetime | None, now: datetime) -> bool:
    """Whether a provider whose recheck is due at ``next_check_at`` is due at ``now``: never
    checked (None), or due within :data:`MONITORING_DUE_TOLERANCE`."""
    return next_check_at is None or next_check_at - MONITORING_DUE_TOLERANCE <= now
