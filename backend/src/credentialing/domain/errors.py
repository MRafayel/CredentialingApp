"""Domain errors."""

from uuid import UUID


class DomainError(Exception):
    """Base class for errors raised by domain rules."""


class InvalidProviderError(DomainError, ValueError):
    """A provider's data breaks a domain rule, such as an individual without a name.

    It is also a ``ValueError`` so pydantic validators can raise it and report it as a
    validation error.
    """


class DuplicateNpiError(DomainError):
    """Another provider of the organization's roster has this NPI: a roster holds each NPI
    once. ``existing_provider_id`` names that provider, or is None when the database refused
    the write and the holder is not known. The message carries no NPI."""

    def __init__(self, organization_id: UUID, existing_provider_id: UUID | None = None) -> None:
        super().__init__("a provider with this NPI is already on the roster")
        self.organization_id = organization_id
        self.existing_provider_id = existing_provider_id


class DatasetFormatError(DomainError, ValueError):
    """A dataset file does not have the expected shape (a missing column, an unreadable row).

    Messages name columns, line numbers and counts only, never values, so they are safe to
    store in a snapshot note and to log: the files hold real people.
    """


class DatasetShrankError(DomainError):
    """A full dataset file has far fewer rows than the last one loaded; it was not applied."""

    def __init__(self, rows: int, minimum: int) -> None:
        super().__init__(f"the file has {rows} rows, fewer than the {minimum} required")
        self.rows = rows
        self.minimum = minimum
