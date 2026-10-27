"""Errors raised by specforge. CLI maps these to a non-zero exit."""

from __future__ import annotations


class SpecforgeError(Exception):
    """Base error. The message is safe to print: callers must already redact secrets."""


class ProviderError(SpecforgeError):
    """The model endpoint failed, or a recorded provider has no reply."""


class CatalogError(SpecforgeError):
    """A question catalog could not be loaded or applied."""


class SpecError(SpecforgeError):
    """A specification file is missing a required piece or is ambiguous."""


class ContractError(SpecforgeError):
    """The interface contract is invalid, or a change would break a freeze."""


class PipelineError(SpecforgeError):
    """A stage cannot continue with the artifacts it was given."""