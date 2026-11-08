"""Parse and validate RepoAccord configuration text."""

from dataclasses import dataclass
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from repoaccord.errors import ConfigError

MAX_CONFIG_LENGTH = 64 * 1024


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None, "expected a mapping", node.start_mark)
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "unhashable mapping key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class Branches:
    """Allowed branch prefixes."""

    prefixes: tuple[str, ...] = ("feat/", "fix/", "docs/", "chore/")


@dataclass(frozen=True)
class Actors:
    """Explicitly allowed GitHub logins."""

    allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class Issues:
    """Issue creation configuration."""

    label: str | None = None
    template: str | None = None


@dataclass(frozen=True)
class PullRequest:
    """Pull request body configuration."""

    placeholder: str = "#<issue number>"


@dataclass(frozen=True)
class Trailers:
    """Forbidden commit message trailers."""

    forbid: tuple[str, ...] = ("Co-authored-by", "Generated with")


@dataclass(frozen=True)
class AutoMerge:
    """Optional auto-merge configuration."""

    enabled: bool = False
    method: str = "merge"


@dataclass(frozen=True)
class Config:
    """Validated RepoAccord configuration."""

    version: int = 1
    branches: Branches = Branches()
    actors: Actors = Actors()
    issues: Issues = Issues()
    pull_request: PullRequest = PullRequest()
    trailers: Trailers = Trailers()
    auto_merge: AutoMerge = AutoMerge()


def _mapping(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path or 'configuration'} must be a mapping")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConfigError(f"{path or 'configuration'} keys must be strings")
        key_path = f"{path}.{key}" if path else key
        if key not in keys:
            raise ConfigError(f"unknown configuration key: {key_path}")
        result[key] = item
    return result


def _strings(
    value: Any,
    path: str,
    minimum: int,
    maximum: int | None,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or (maximum is not None and len(value) > maximum)
    ):
        limit = f"{minimum}..{maximum}" if maximum is not None else f"at least {minimum}"
        raise ConfigError(f"{path} must be a list with {limit} entries")
    if any(not isinstance(item, str) or not item for item in value):
        raise ConfigError(f"{path} entries must be non-empty strings")
    return tuple(value)


def _optional_string(value: Any, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{path} must be a string or null")
    return value


def load_config(text: str | None) -> Config:
    """Load strict version 1 configuration from YAML text."""
    if text is not None:
        try:
            size = len(text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ConfigError("configuration must be valid UTF-8 text") from exc
        if size > MAX_CONFIG_LENGTH:
            raise ConfigError("configuration exceeds 64 KiB")
    if text is None or not text.strip():
        return Config()
    loader = _UniqueKeyLoader(text)
    try:
        data = loader.get_single_data()
    except yaml.YAMLError as exc:
        reason = "duplicate key" if "duplicate key" in str(exc) else "invalid YAML"
        raise ConfigError(f"configuration contains {reason}") from exc
    finally:
        loader.dispose()

    root = _mapping(
        data,
        "",
        {"version", "branches", "actors", "issues", "pull_request", "trailers", "auto_merge"},
    )
    version = root.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigError("version must be 1")

    branch_data = _mapping(root.get("branches", {}), "branches", {"prefixes"})
    prefixes = branch_data.get("prefixes", list(Branches().prefixes))
    prefixes = _strings(prefixes, "branches.prefixes", 1, 20)
    if any(
        not prefix.endswith("/")
        or any(character.isspace() for character in prefix)
        or ".." in prefix
        for prefix in prefixes
    ):
        raise ConfigError(
            "branches.prefixes entries must end with / and contain no whitespace or .."
        )

    actor_data = _mapping(root.get("actors", {}), "actors", {"allow"})
    allow = _strings(actor_data.get("allow", []), "actors.allow", 0, None)
    if any(any(character.isspace() for character in login) for login in allow):
        raise ConfigError("actors.allow entries must be logins without whitespace")

    issue_data = _mapping(root.get("issues", {}), "issues", {"label", "template"})
    label = _optional_string(issue_data.get("label"), "issues.label")
    if label is not None and (len(label) > 50 or "\n" in label or "\r" in label):
        raise ConfigError("issues.label must be at most 50 characters with no newline")
    template = _optional_string(issue_data.get("template"), "issues.template")
    if template is not None and (
        not template or "/" in template or "\\" in template or "\n" in template or "\r" in template
    ):
        raise ConfigError("issues.template must be a file name")

    pr_data = _mapping(root.get("pull_request", {}), "pull_request", {"placeholder"})
    placeholder = pr_data.get("placeholder", PullRequest().placeholder)
    if not isinstance(placeholder, str) or not 1 <= len(placeholder) <= 64:
        raise ConfigError("pull_request.placeholder must be a string of 1..64 characters")

    trailer_data = _mapping(root.get("trailers", {}), "trailers", {"forbid"})
    forbid = _strings(
        trailer_data.get("forbid", list(Trailers().forbid)),
        "trailers.forbid",
        0,
        None,
    )

    merge_data = _mapping(root.get("auto_merge", {}), "auto_merge", {"enabled", "method"})
    enabled = merge_data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("auto_merge.enabled must be a boolean")
    method = merge_data.get("method", "merge")
    if not isinstance(method, str) or method not in {"merge", "squash", "rebase"}:
        raise ConfigError("auto_merge.method must be merge, squash, or rebase")

    return Config(
        version=version,
        branches=Branches(prefixes=prefixes),
        actors=Actors(allow=allow),
        issues=Issues(label=label, template=template),
        pull_request=PullRequest(placeholder=placeholder),
        trailers=Trailers(forbid=forbid),
        auto_merge=AutoMerge(enabled=enabled, method=method),
    )
