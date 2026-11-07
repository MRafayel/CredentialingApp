"""Domain errors."""


class DomainError(Exception):
    """Base class for errors raised by domain rules."""


class InvalidProviderError(DomainError, ValueError):
    """A provider's data breaks a domain rule, such as an individual without a name.

    It is also a ``ValueError`` so pydantic validators can raise it and report it as a
    validation error.
    """


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
