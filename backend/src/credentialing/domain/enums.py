"""Closed vocabularies of the domain, defined once and shared by every layer."""

from enum import StrEnum


class CredentialType(StrEnum):
    """The kind of credential a check verifies."""

    EXCLUSION = "exclusion"
    NPI = "npi"
    STATE_LICENSE = "state_license"
    BOARD_CERTIFICATION = "board_certification"


class Outcome(StrEnum):
    """What happened when a source was consulted.

    Only ``COMPLETED`` means the source answered; every other value is "we could not check" and
    must never be read as a clean result.
    """

    COMPLETED = "completed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    BLOCKED = "blocked"
    MANUAL_REQUIRED = "manual_required"
    FAILED = "failed"


class Finding(StrEnum):
    """What a completed check found.

    ``RELATED_PARTY`` is a record of another party that names the provider with confirming
    identity (an owner or worker of a listed business, the business of a listed person): the
    provider is flagged, not listed.
    """

    NO_RECORD = "no_record"
    RECORD_FOUND = "record_found"
    RELATED_PARTY = "related_party"
    AMBIGUOUS = "ambiguous"


class Verdict(StrEnum):
    """A provider's answer across sources, most serious first.

    ``FLAGGED`` is a confirmed tie to a listed party (the provider is named on another party's
    record); ``NEEDS_REVIEW`` an unresolved possible listing. ``COULD_NOT_CHECK`` is never
    clean: at least one source did not answer.
    """

    LISTED = "listed"
    FLAGGED = "flagged"
    NEEDS_REVIEW = "needs_review"
    COULD_NOT_CHECK = "could_not_check"
    NO_RECORDS = "no_records"
    NOT_CHECKED = "not_checked"


class MatchLevel(StrEnum):
    """How strongly a source record is tied to a provider, from strongest to none."""

    EXACT = "exact"
    HIGH = "high"
    POTENTIAL = "potential"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


class MatchRelation(StrEnum):
    """Which role on a record the provider was matched as; the confidence is the match level.

    ``PARTY`` is the listed party itself. ``RELATED_PARTY`` is another party the row names: an
    owner, a worker or an affiliated business of the listed party.
    """

    PARTY = "party"
    RELATED_PARTY = "related_party"


class MatchField(StrEnum):
    """An identifying field that matching compared."""

    NPI = "npi"
    LAST_NAME = "last_name"
    FIRST_NAME = "first_name"
    MIDDLE_NAME = "middle_name"
    DATE_OF_BIRTH = "date_of_birth"
    LICENSE = "license"
    ADDRESS = "address"
    ORGANIZATION_NAME = "organization_name"


class Freshness(StrEnum):
    """How current a result is; always shown together with its timestamp."""

    LIVE = "live"
    CACHED = "cached"
    STALE = "stale"
    MANUAL = "manual"


class VerificationMethod(StrEnum):
    """How a result was obtained from its source."""

    BULK_DATASET = "bulk_dataset"
    OFFICIAL_API = "official_api"
    HTTP = "http"
    BROWSER = "browser"
    MANUAL = "manual"


class AccessPolicy(StrEnum):
    """How a source may be reached. Only ``HTTP`` and ``BROWSER`` sources get a fetcher."""

    HTTP = "http"
    BROWSER = "browser"
    MANUAL = "manual"
    ROBOTS_DISALLOWED = "robots_disallowed"
    NETWORK_BLOCKED = "network_blocked"


class ChangeSignal(StrEnum):
    """How a connector detects that its source changed, so a poll costs one request."""

    API_METADATA = "api_metadata"
    CONDITIONAL_GET = "conditional_get"
    HEADER_COMPARE = "header_compare"
    LINK_DISCOVERY = "link_discovery"
    CONTENT_HASH = "content_hash"


class EntityKind(StrEnum):
    """Whether a party is a person or an organization; ``UNKNOWN`` when a source does not say."""

    INDIVIDUAL = "individual"
    ORGANIZATION = "organization"
    UNKNOWN = "unknown"


class ExclusionAction(StrEnum):
    """The normalized action an exclusion list records; the source's wording is kept as well."""

    EXCLUSION = "exclusion"
    TERMINATION = "termination"
    SUSPENSION = "suspension"
    DEBARMENT = "debarment"
    PRECLUSION = "preclusion"
    SANCTION = "sanction"
    DISQUALIFICATION = "disqualification"
    REINSTATEMENT = "reinstatement"
    OTHER = "other"


class RecordStatus(StrEnum):
    """Whether a matched exclusion record still stands, has ended (end date passed), or was
    removed from the published list."""

    ACTIVE = "active"
    ENDED = "ended"
    REMOVED = "removed"
    """No longer on the published list; reinstatement is not confirmed."""


class RecordLink(StrEnum):
    """How another exclusion record is tied to the one being viewed.

    ``BY_NPI`` is the same party (same NPI); ``BY_LICENSE`` very likely is (same licence
    number and state); ``SAME_NAME`` only shares the name, so a person decides. ``LISTED_AS``
    is a record whose own NPI is one of this record's related NPIs, and ``NAMED_ALONGSIDE`` a
    record that names one of this record's NPIs (its own or a related one) among its related
    NPIs.
    """

    BY_NPI = "by_npi"
    BY_LICENSE = "by_license"
    SAME_NAME = "same_name"
    LISTED_AS = "listed_as"
    NAMED_ALONGSIDE = "named_alongside"
