"""User-facing errors for RepoAccord."""


class RepoAccordError(Exception):
    """Base class for expected RepoAccord errors."""


class ConfigError(RepoAccordError):
    """Invalid configuration text."""


class MessageError(RepoAccordError):
    """Invalid commit message."""


class TemplateError(RepoAccordError):
    """Invalid or unsupported repository template."""
