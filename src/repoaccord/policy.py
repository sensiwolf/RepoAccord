"""Decide safe, offline plans for repository pushes."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from repoaccord.config import ConfigError, load_config
from repoaccord.errors import MessageError, TemplateError
from repoaccord.message import ParsedMessage, parse_message
from repoaccord.templates import (
    Template,
    check_body,
    find_issue_template,
    find_pr_template,
    parse_template,
)

_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_PERMISSIONS = {"admin", "maintain", "write"}


@dataclass(frozen=True)
class PushEvent:
    """Values supplied by a GitHub push event."""

    actor: str
    actor_is_bot: bool
    ref: str
    deleted: bool
    commit_message: str


@dataclass(frozen=True)
class RepoFacts:
    """Repository state already read by a caller."""

    default_branch: str
    actor_permission: str | None
    existing_pr: bool
    paths: tuple[str, ...]
    pr_template_text: str | None
    issue_templates: Mapping[str, str]
    required_checks_proven: bool | None


@dataclass(frozen=True)
class CreateIssue:
    """Create one issue from a parsed request."""

    title: str
    body: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class CreatePullRequest:
    """Create a pull request from a parsed request."""

    title: str
    body_template: str
    base: str
    head: str
    placeholder: str


@dataclass(frozen=True)
class EnableAutoMerge:
    """Enable auto-merge using the configured method."""

    method: str


Action: TypeAlias = CreateIssue | CreatePullRequest | EnableAutoMerge
Decision: TypeAlias = Literal["skip", "refuse", "act"]


@dataclass(frozen=True)
class Plan:
    """A safe, side-effect-free decision and its proposed actions."""

    decision: Decision
    reason: str
    actions: tuple[Action, ...]
    notes: tuple[str, ...]


def _skip(reason: str) -> Plan:
    return Plan("skip", reason, (), ())


def _refuse(reason: str) -> Plan:
    return Plan("refuse", reason, (), ())


def _template_text(facts: RepoFacts, selected: str) -> str | None:
    selected_name = selected.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    exact = facts.issue_templates.get(selected)
    if exact is not None:
        return exact
    matches = [
        text
        for path, text in facts.issue_templates.items()
        if path.replace("\\", "/").rsplit("/", 1)[-1].casefold() == selected_name
    ]
    return matches[0] if len(matches) == 1 else None


def _validate_body(body: str, template: Template, label: str) -> str | None:
    result = check_body(body, template)
    details: list[str] = []
    if result.missing:
        details.append("missing headings: " + ", ".join(result.missing))
    if result.empty:
        details.append("empty sections: " + ", ".join(result.empty))
    if details:
        return f"{label} body is invalid ({'; '.join(details)})"
    return None


def decide(event: PushEvent, facts: RepoFacts, config_text: str | None) -> Plan:
    """Return a deterministic plan without performing I/O or side effects."""
    if not event.ref.startswith("refs/heads/"):
        return _skip("event is not a branch push")
    if event.deleted:
        return _skip("deleted branches are ignored")
    branch = event.ref[len("refs/heads/") :]
    if branch == facts.default_branch:
        return _skip("push to the default branch is ignored")
    if event.actor_is_bot or event.actor.casefold().endswith("[bot]"):
        return _skip("bot actors are ignored")

    if (
        not _BRANCH.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.startswith(("/", "."))
        or branch.endswith(("/", "."))
        or branch.endswith(".lock")
    ):
        return _refuse("branch name is not allowed by the security rules")

    try:
        config = load_config(config_text)
    except ConfigError as exc:
        return _refuse(str(exc))
    if not any(branch.startswith(prefix) for prefix in config.branches.prefixes):
        return _skip("branch does not match an allowed prefix")

    if config.actors.allow:
        if not any(event.actor.casefold() == login.casefold() for login in config.actors.allow):
            return _refuse("actor is not in the configured allowlist")
    elif facts.actor_permission is None:
        return _refuse("permission could not be verified")
    elif facts.actor_permission.casefold() not in _PERMISSIONS:
        return _refuse("actor does not have write permission")

    if facts.existing_pr:
        return _skip("a pull request already exists for this branch")

    try:
        message: ParsedMessage = parse_message(event.commit_message, config.trailers.forbid)
    except MessageError as exc:
        return _refuse(str(exc))

    notes: list[str] = []
    try:
        pr_template_path = find_pr_template(facts.paths)
    except TemplateError as exc:
        return _refuse(str(exc))
    if pr_template_path is not None:
        if facts.pr_template_text is None:
            return _refuse("pull request template contents could not be read")
        try:
            pr_template = parse_template(facts.pr_template_text)
        except TemplateError as exc:
            return _refuse(f"pull request template is invalid: {exc}")
        problem = _validate_body(message.pr_body, pr_template, "pull request")
        if problem:
            return _refuse(problem)
    else:
        notes.append("no PR template found; body not validated")

    issue_action: CreateIssue | None = None
    if message.issue is not None:
        try:
            issue_template_path = find_issue_template(
                facts.paths,
                config.issues.template,
            )
        except TemplateError as exc:
            return _refuse(str(exc))
        issue_labels: tuple[str, ...] = ()
        if issue_template_path is None:
            notes.append("no issue template found; issue body not validated")
        else:
            issue_template_text = _template_text(facts, issue_template_path)
            if issue_template_text is None:
                return _refuse("issue template contents could not be read")
            try:
                issue_template = parse_template(issue_template_text)
            except TemplateError as exc:
                return _refuse(f"issue template is invalid: {exc}")
            problem = _validate_body(message.issue.body, issue_template, "issue")
            if problem:
                return _refuse(problem)
            issue_labels = (
                (config.issues.label,) if config.issues.label is not None else issue_template.labels
            )
        if message.pr_body.count(config.pull_request.placeholder) != 1:
            return _refuse("pull request body must contain the issue placeholder exactly once")
        issue_action = CreateIssue(
            title=message.issue.title,
            body=message.issue.body,
            labels=issue_labels,
        )
    elif config.pull_request.placeholder in message.pr_body:
        return _refuse("pull request body must not contain the issue placeholder without an issue")

    actions: list[Action] = []
    if issue_action is not None:
        actions.append(issue_action)
    actions.append(
        CreatePullRequest(
            title=message.title,
            body_template=message.pr_body,
            base=facts.default_branch,
            head=branch,
            placeholder=config.pull_request.placeholder,
        )
    )
    if config.auto_merge.enabled:
        if facts.required_checks_proven is True:
            actions.append(EnableAutoMerge(method=config.auto_merge.method))
        else:
            notes.append("auto-merge skipped because required checks could not be proven")
    return Plan("act", "request is authorized and valid", tuple(actions), tuple(notes))


def render_pr_body(body_template: str, placeholder: str, issue_number: int | None) -> str:
    """Replace one issue placeholder after issue creation, or validate its absence."""
    count = body_template.count(placeholder)
    if issue_number is None:
        if count:
            raise ValueError("placeholder must not appear without an issue number")
        return body_template
    if count != 1:
        raise ValueError("placeholder must appear exactly once with an issue number")
    return body_template.replace(placeholder, f"#{issue_number}", 1)
