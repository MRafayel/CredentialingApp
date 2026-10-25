"""Domain errors."""


class DomainError(Exception):
    """Base class for errors raised by domain rules."""


class InvalidProviderError(DomainError, ValueError):
    """A provider's data breaks a domain rule, such as an individual without a name.

    It is also a ``ValueError`` so pydantic validators can raise it and report it as a
    validation error.
    """
