"""Parse the text protocol carried by a commit message."""

from collections.abc import Sequence
from dataclasses import dataclass

from repoaccord.errors import MessageError

ISSUE_MARKER = "=== ISSUE ==="
PR_MARKER = "=== PR ==="
MAX_BODY_LENGTH = 65_000


@dataclass(frozen=True)
class IssueDraft:
    """An issue title and body parsed from a commit message."""

    title: str
    body: str


@dataclass(frozen=True)
class ParsedMessage:
    """The pull request title, optional issue, and pull request body."""

    title: str
    issue: IssueDraft | None
    pr_body: str


def _block(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start + 1 : end]).strip("\n")


def parse_message(text: str, forbidden_trailers: Sequence[str]) -> ParsedMessage:
    """Parse a commit message without interpreting its contents."""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    title = lines[0]
    if not title.strip() or len(title) > 256:
        raise MessageError("commit subject must be 1..256 characters")

    for line in lines:
        stripped = line.lstrip().casefold()
        for phrase in forbidden_trailers:
            if phrase and stripped.startswith(phrase.casefold()):
                raise MessageError("commit message contains a forbidden trailer")

    marker_positions: list[tuple[int, str]] = []
    counts = {ISSUE_MARKER: 0, PR_MARKER: 0}
    for index, line in enumerate(lines[1:], start=1):
        if line in counts:
            counts[line] += 1
            marker_positions.append((index, line))
    if any(count > 1 for count in counts.values()):
        raise MessageError("each message block marker may appear only once")
    if counts[PR_MARKER] != 1:
        raise MessageError("required === PR === block is missing")
    if marker_positions and any(line.strip() for line in lines[1 : marker_positions[0][0]]):
        raise MessageError("text outside message blocks is not allowed")

    issue: IssueDraft | None = None
    pr_body: str | None = None
    for position, (marker_index, marker) in enumerate(marker_positions):
        end = (
            marker_positions[position + 1][0]
            if position + 1 < len(marker_positions)
            else len(lines)
        )
        content = _block(lines, marker_index, end)
        if marker == PR_MARKER:
            if not content.strip():
                raise MessageError("pull request body must not be blank")
            if len(content) > MAX_BODY_LENGTH:
                raise MessageError("pull request body exceeds 65000 characters")
            pr_body = content
        else:
            issue_lines = content.split("\n")
            title_index = next(
                (index for index, line in enumerate(issue_lines) if line.strip()),
                None,
            )
            if title_index is None:
                raise MessageError("issue title must not be blank")
            issue_title = issue_lines[title_index]
            if len(issue_title) > 256:
                raise MessageError("issue title must be 1..256 characters")
            issue_body = "\n".join(issue_lines[title_index + 1 :]).strip("\n")
            if len(issue_body) > MAX_BODY_LENGTH:
                raise MessageError("issue body exceeds 65000 characters")
            issue = IssueDraft(title=issue_title, body=issue_body)

    if pr_body is None:
        raise MessageError("required === PR === block is missing")
    return ParsedMessage(title=title, issue=issue, pr_body=pr_body)
