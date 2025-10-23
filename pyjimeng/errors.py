from __future__ import annotations


class JimengError(Exception):
    """Base error thrown by the Python Jimeng client."""


class JimengAPIError(JimengError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class JimengPollingTimeout(JimengError):
    """Raised when polling for a generation result times out."""

