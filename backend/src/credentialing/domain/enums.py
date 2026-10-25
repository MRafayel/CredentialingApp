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
    """What a completed check found."""

    NO_RECORD = "no_record"
    RECORD_FOUND = "record_found"
    AMBIGUOUS = "ambiguous"


class MatchLevel(StrEnum):
    """How strongly a source record is tied to a provider, from strongest to none."""

    EXACT = "exact"
    HIGH = "high"
    POTENTIAL = "potential"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


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
    REINSTATEMENT = "reinstatement"
    OTHER = "other"
