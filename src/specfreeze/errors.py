"""Errors raised by specfreeze. CLI maps these to a non-zero exit."""

from __future__ import annotations


class SpecfreezeError(Exception):
    """Base error. The message is safe to print: callers must already redact secrets."""


class ProviderError(SpecfreezeError):
    """The model endpoint failed, or a recorded provider has no reply."""


class CatalogError(SpecfreezeError):
    """A question catalog could not be loaded or applied."""


class SpecError(SpecfreezeError):
    """A specification file is missing a required piece or is ambiguous."""


class ContractError(SpecfreezeError):
    """The interface contract is invalid, or a change would break a freeze."""


class PipelineError(SpecfreezeError):
    """A stage cannot continue with the artifacts it was given."""