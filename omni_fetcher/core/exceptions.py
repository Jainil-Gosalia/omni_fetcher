"""Core exceptions for OmniFetcher."""

from typing import Any, Optional


class OmniFetcherError(Exception):
    """Base exception for OmniFetcher."""

    pass


class SourceNotFoundError(OmniFetcherError):
    """Raised when no suitable source handler is found."""

    def __init__(self, uri: str):
        self.uri = uri
        super().__init__(f"No handler found for URI: {uri}")


class FetchError(OmniFetcherError):
    """Raised when fetching fails."""

    def __init__(self, uri: str, reason: str):
        self.uri = uri
        self.reason = reason
        super().__init__(f"Failed to fetch {uri}: {reason}")


class ValidationError(OmniFetcherError):
    """Raised when data validation fails."""

    def __init__(self, message: str, errors: Optional[list] = None):
        self.errors = errors or []
        super().__init__(message)


class SourceRegistrationError(OmniFetcherError):
    """Raised when source registration fails."""

    pass


class SchemaError(OmniFetcherError):
    """Raised when schema validation or creation fails."""

    pass
