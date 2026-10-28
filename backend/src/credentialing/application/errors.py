"""Application errors: a use case could not do what was asked."""

from uuid import UUID


class ApplicationError(Exception):
    """Base class for errors raised by use cases."""


class UnknownSourceError(ApplicationError):
    """No bulk-list connector is registered under this source id."""

    def __init__(self, source_id: str) -> None:
        super().__init__(f"no bulk-list source is registered as {source_id!r}")
        self.source_id = source_id


class ProviderNotFoundError(ApplicationError):
    """The organization has no provider with this id."""

    def __init__(self, provider_id: UUID) -> None:
        super().__init__(f"provider {provider_id} not found")
        self.provider_id = provider_id


class InvalidProviderInputError(ApplicationError):
    """The provider data breaks a domain rule; ``message`` says which, without the values."""


class RecordNotFoundError(ApplicationError):
    """No exclusion record has this id."""

    def __init__(self, record_id: UUID) -> None:
        super().__init__(f"exclusion record {record_id} not found")
        self.record_id = record_id
