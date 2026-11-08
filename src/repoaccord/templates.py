"""Select and validate repository Markdown templates."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

import yaml

from repoaccord.errors import TemplateError

_PR_PRIORITY = (
    ".github/pull_request_template.md",
    "pull_request_template.md",
    "docs/pull_request_template.md",
)
_ISSUE_DIRECTORY = ".github/issue_template/"
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class Heading:
    """A normalized Markdown heading and its level."""

    text: str
    level: int


@dataclass(frozen=True)
class Template:
    """Headings and labels declared by a Markdown template."""

    headings: tuple[Heading, ...]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class BodyCheck:
    """Required headings absent from or empty in a submitted body."""

    missing: tuple[str, ...]
    empty: tuple[str, ...]


def _path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def find_pr_template(paths: Sequence[str]) -> str | None:
    """Select a supported pull request template by documented priority."""
    indexed = {_path(path).casefold(): path for path in paths}
    for candidate in _PR_PRIORITY:
        match = indexed.get(candidate.casefold())
        if match is not None:
            return match
    directory = ".github/pull_request_template"
    if any(
        _path(path).casefold() == directory or _path(path).casefold().startswith(directory + "/")
        for path in paths
    ):
        raise TemplateError("multiple PR templates are not supported")
    return None


def find_issue_template(paths: Sequence[str], wanted: str | None) -> str | None:
    """Select one Markdown issue template directly in the issue-template directory."""
    markdown: list[str] = []
    forms: list[str] = []
    for path in paths:
        normalized = _path(path)
        lower = normalized.casefold()
        if not lower.startswith(_ISSUE_DIRECTORY):
            continue
        name = normalized[len(_ISSUE_DIRECTORY) :]
        if not name or "/" in name:
            continue
        if name.casefold().endswith(".md"):
            markdown.append(path)
        elif name.casefold().endswith((".yml", ".yaml")):
            forms.append(path)

    if wanted is not None:
        for path in markdown:
            if path.replace("\\", "/").rsplit("/", 1)[-1].casefold() == wanted.casefold():
                return path
        if not markdown and forms:
            raise TemplateError("YAML issue forms are not supported")
        raise TemplateError(f"issue template not found: {wanted}")
    if len(markdown) == 1:
        return markdown[0]
    if len(markdown) > 1:
        raise TemplateError("multiple issue templates found; set issues.template")
    if forms:
        raise TemplateError("YAML issue forms are not supported")
    return None


def _without_comments(text: str) -> str:
    return _COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def _normalise_heading(text: str) -> str:
    text = re.sub(r"[ \t]+#+[ \t]*$", "", text).strip()
    return re.sub(r"\s+", " ", text).casefold()


def _headings(text: str) -> tuple[Heading, ...]:
    result: list[Heading] = []
    fence_character: str | None = None
    fence_length = 0
    for line in _without_comments(text).splitlines():
        fence = _FENCE.match(line)
        if fence:
            token = fence.group(1)
            character = token[0]
            if fence_character is None:
                fence_character = character
                fence_length = len(token)
            elif (
                character == fence_character
                and len(token) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue
        match = _HEADING.match(line)
        if match:
            heading_text = _normalise_heading(match.group(2))
            if heading_text:
                result.append(Heading(text=heading_text, level=len(match.group(1))))
    return tuple(result)


def parse_template(text: str) -> Template:
    """Parse optional YAML labels and Markdown headings from template text."""
    normalized = text.replace("\r\n", "\n")
    labels: tuple[str, ...] = ()
    markdown = normalized
    if normalized.startswith("---\n") or normalized == "---":
        lines = normalized.split("\n")
        closing = next((index for index in range(1, len(lines)) if lines[index] == "---"), None)
        if closing is None:
            raise TemplateError("malformed template front matter")
        front_matter = "\n".join(lines[1:closing])
        try:
            metadata = yaml.safe_load(front_matter)
        except yaml.YAMLError as exc:
            raise TemplateError("malformed template front matter") from exc
        if metadata is not None and not isinstance(metadata, dict):
            raise TemplateError("template front matter must be a mapping")
        if isinstance(metadata, dict) and "labels" in metadata:
            raw_labels = metadata["labels"]
            if isinstance(raw_labels, str):
                labels = tuple(label.strip() for label in raw_labels.split(",") if label.strip())
            elif isinstance(raw_labels, list) and all(
                isinstance(label, str) and label.strip() for label in raw_labels
            ):
                labels = tuple(label.strip() for label in raw_labels)
            else:
                raise TemplateError("template labels must be a string or list of strings")
        markdown = "\n".join(lines[closing + 1 :])
    return Template(headings=_headings(markdown), labels=labels)


def check_body(body: str, template: Template) -> BodyCheck:
    """Check required headings and whether their sections contain visible text."""
    body_without_comments = _without_comments(body)
    body_headings = _headings(body_without_comments)
    present = {heading.text for heading in body_headings}
    missing = tuple(
        dict.fromkeys(heading.text for heading in template.headings if heading.text not in present)
    )
    lines = body_without_comments.splitlines()
    positions: list[tuple[int, Heading]] = []
    fence_character: str | None = None
    fence_length = 0
    for index, line in enumerate(lines):
        fence = _FENCE.match(line)
        if fence:
            token = fence.group(1)
            character = token[0]
            if fence_character is None:
                fence_character = character
                fence_length = len(token)
            elif (
                character == fence_character
                and len(token) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue
        match = _HEADING.match(line)
        if match:
            value = _normalise_heading(match.group(2))
            if value:
                positions.append((index, Heading(value, len(match.group(1)))))
    empty: list[str] = []
    for required in template.headings:
        target_positions = [
            position
            for position, heading in enumerate(positions)
            if heading[1].text == required.text
        ]
        if not target_positions:
            continue
        for target in target_positions:
            start, heading = positions[target]
            end = len(lines)
            for next_index, next_heading in positions[target + 1 :]:
                if next_heading.level <= heading.level:
                    end = next_index
                    break
            content_lines = [line for line in lines[start + 1 : end] if not _HEADING.match(line)]
            if not any(line.strip() for line in content_lines):
                empty.append(required.text)
                break
    return BodyCheck(missing=missing, empty=tuple(dict.fromkeys(empty)))
