"""Errors raised by the connector framework."""


class ParseError(ValueError):
    """A source document or row does not have the expected shape.

    Messages name columns, line numbers and counts only, never cell contents, so they are safe
    to store in a snapshot note and to log.
    """
