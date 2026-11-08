"""Load a push event, gather repository facts, and execute a safe plan."""

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import sleep as default_sleep
from typing import Any

from repoaccord.config import ConfigError, load_config
from repoaccord.errors import TemplateError
from repoaccord.github_api import (
    GitHubClient,
    GitHubError,
    IssueRef,
    PullRequestRef,
    Transport,
    UrllibTransport,
)
from repoaccord.policy import (
    Action,
    CreateIssue,
    CreatePullRequest,
    EnableAutoMerge,
    PushEvent,
    RepoFacts,
    decide,
    precheck,
    render_pr_body,
)
from repoaccord.templates import find_pr_template

_WORKFLOW_ESCAPE = re.compile(r"(?m)^::")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>])")


@dataclass(frozen=True)
class RunResult:
    """Outcome of one run, including any created references."""

    decision: str
    reason: str
    notes: tuple[str, ...] = ()
    issue: IssueRef | None = None
    pull_request: PullRequestRef | None = None
    actions: tuple[str, ...] = ()


def _escape_workflow(value: str) -> str:
    escaped = value.replace("%", "%25").replace("\r", "%0D")
    escaped = _WORKFLOW_ESCAPE.sub("%3A%3A", escaped)
    return escaped.replace("\n", "%0A")


def _escape_markdown(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value.replace("\r", " ").replace("\n", " "))


def _emit(result: RunResult) -> None:
    print(f"decision: {_escape_workflow(result.decision)}")
    print(f"reason: {_escape_workflow(result.reason)}")
    for note in result.notes:
        print(f"note: {_escape_workflow(note)}")
    if result.issue is not None:
        print(f"issue: {_escape_workflow(result.issue.url)}")
    if result.pull_request is not None:
        print(f"pull request: {_escape_workflow(result.pull_request.url)}")
    if result.decision in {"refuse", "error"}:
        print(f"::error::{_escape_workflow(result.reason)}")


def _write_summary(result: RunResult, path: str | None) -> None:
    if not path:
        return
    actions = ", ".join(_escape_markdown(action) for action in result.actions) or "None"
    lines = [
        "## RepoAccord",
        "",
        f"- **Decision:** {_escape_markdown(result.decision)}",
        f"- **Reason:** {_escape_markdown(result.reason)}",
    ]
    lines.extend(f"- **Note:** {_escape_markdown(note)}" for note in result.notes)
    lines.append(f"- **Actions:** {actions}")
    if result.issue is not None:
        lines.append(f"- **Issue:** {_escape_markdown(result.issue.url)}")
    if result.pull_request is not None:
        lines.append(f"- **Pull request:** {_escape_markdown(result.pull_request.url)}")
    with Path(path).open("a", encoding="utf-8", newline="\n") as summary:
        summary.write("\n".join(lines) + "\n")


def _result(
    decision: str,
    reason: str,
    notes: tuple[str, ...] = (),
    issue: IssueRef | None = None,
    pull_request: PullRequestRef | None = None,
    actions: tuple[str, ...] = (),
) -> RunResult:
    result = RunResult(decision, reason, notes, issue, pull_request, actions)
    _emit(result)
    return result


def _string(mapping: Mapping[str, Any], key: str, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}.{key} is missing or invalid")
    return value


def _event_values(event: Mapping[str, Any]) -> tuple[PushEvent, str, str]:
    repository = event.get("repository")
    sender = event.get("sender")
    if not isinstance(repository, Mapping) or not isinstance(sender, Mapping):
        raise ValueError("push event is missing repository or sender")
    full_name = _string(repository, "full_name", "repository")
    default_branch = _string(repository, "default_branch", "repository")
    actor = _string(sender, "login", "sender")
    sender_type = sender.get("type")
    if not isinstance(sender_type, str):
        raise ValueError("push event sender.type is missing or invalid")
    ref = _string(event, "ref", "event")
    deleted = event.get("deleted")
    if not isinstance(deleted, bool):
        raise ValueError("push event deleted is missing or invalid")
    head_commit = event.get("head_commit")
    if not isinstance(head_commit, Mapping):
        raise ValueError("push event head_commit is missing or invalid")
    message = _string(head_commit, "message", "head_commit")
    return (
        PushEvent(
            actor=actor,
            actor_is_bot=sender_type.casefold() == "bot",
            ref=ref,
            deleted=deleted,
            commit_message=message,
        ),
        full_name,
        default_branch,
    )


def _marker(branch: str) -> str:
    return f"<!-- repoaccord:branch={branch} -->"


def _append_marker(body: str, marker: str) -> str:
    return f"{body}\n{marker}" if body else marker


def _issue_templates(
    client: GitHubClient,
    paths: tuple[str, ...],
) -> dict[str, str]:
    templates: dict[str, str] = {}
    for path in paths:
        normalized = path.replace("\\", "/")
        if not normalized.casefold().startswith(".github/issue_template/"):
            continue
        if not normalized.casefold().endswith(".md"):
            continue
        content = client.read_file(path)
        if content is not None:
            templates[path] = content
    return templates


def _action_names(actions: tuple[Action, ...]) -> tuple[str, ...]:
    return tuple(type(action).__name__ for action in actions)


def _failure(
    reason: str,
    issue: IssueRef | None,
    pr: PullRequestRef | None,
    notes: tuple[str, ...] = (),
) -> RunResult:
    if issue is not None:
        reason = f"{reason}; recover issue #{issue.number} at {issue.url}"
    if pr is not None:
        reason = f"{reason}; pull request #{pr.number} is at {pr.url}"
    return _result("error", reason, notes, issue=issue, pull_request=pr)


def run(
    event: Mapping[str, Any],
    client: GitHubClient,
    config_path: str,
    dry_run: bool,
) -> RunResult:
    """Make a plan from an event and optionally execute its allowed actions."""
    issue_ref: IssueRef | None = None
    pull_request_ref: PullRequestRef | None = None
    try:
        push, event_repository, event_default_branch = _event_values(event)
    except ValueError as exc:
        return _result("refuse", str(exc))
    if event_repository.casefold() != client.repository.casefold():
        return _result("refuse", "event repository does not match configured repository")
    early = precheck(push, event_default_branch)
    if early is not None:
        return _result(early.decision, early.reason, early.notes)

    try:
        api_default_branch = client.get_default_branch()
        default_branch = api_default_branch
        notes: list[str] = []
        if event_default_branch != api_default_branch:
            notes.append("event default branch differed from API; using API value")
        config_text = client.read_file(config_path)
        try:
            config = load_config(config_text)
        except ConfigError as exc:
            return _result("refuse", str(exc), tuple(notes))
        permission = client.get_actor_permission(push.actor)
        existing_pr = client.has_pull_request_for_branch(push.ref.removeprefix("refs/heads/"))
        paths = client.list_paths()
        pr_template_text: str | None = None
        pr_template_path = find_pr_template(paths)
        if pr_template_path is not None:
            pr_template_text = client.read_file(pr_template_path)
        issue_templates = _issue_templates(client, paths)
        checks = (
            client.required_checks_proven(default_branch) if config.auto_merge.enabled else None
        )
        facts = RepoFacts(
            default_branch=default_branch,
            actor_permission=permission,
            existing_pr=existing_pr,
            paths=paths,
            pr_template_text=pr_template_text,
            issue_templates=issue_templates,
            required_checks_proven=checks,
        )
        plan = decide(push, facts, config_text)
        notes.extend(plan.notes)
    except (GitHubError, TemplateError, OSError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, (GitHubError, TemplateError))
            else "repository facts could not be read"
        )
        return _failure(reason, issue_ref, pull_request_ref)

    if plan.decision != "act" or dry_run:
        return _result(
            plan.decision,
            plan.reason,
            tuple(notes),
            actions=_action_names(plan.actions) if plan.decision == "act" else (),
        )

    branch = push.ref.removeprefix("refs/heads/")
    marker = _marker(branch)
    try:
        for action in plan.actions:
            if isinstance(action, CreateIssue):
                issue_ref = client.find_open_issue_by_marker(marker)
                if issue_ref is not None:
                    notes.append(f"reused open issue #{issue_ref.number}")
                else:
                    issue_ref = client.create_issue(
                        action.title,
                        _append_marker(action.body, marker),
                        action.labels,
                    )
            elif isinstance(action, CreatePullRequest):
                number = issue_ref.number if issue_ref is not None else None
                body = render_pr_body(action.body_template, action.placeholder, number)
                pull_request_ref = client.create_pull_request(
                    action.title,
                    _append_marker(body, marker),
                    action.head,
                    action.base,
                )
            elif isinstance(action, EnableAutoMerge):
                if pull_request_ref is None:
                    return _failure(
                        "auto-merge plan has no created pull request",
                        issue_ref,
                        None,
                        tuple(notes),
                    )
                client.enable_auto_merge(pull_request_ref.node_id, action.method)
    except (GitHubError, ValueError) as exc:
        return _failure(
            str(exc) if isinstance(exc, GitHubError) else "pull request body could not be rendered",
            issue_ref,
            pull_request_ref,
            tuple(notes),
        )

    return _result(
        "act",
        plan.reason,
        tuple(notes),
        issue_ref,
        pull_request_ref,
        _action_names(plan.actions),
    )


def run_from_environment(
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """Read the GitHub event file and run using supplied environment values."""
    environ = os.environ if environ is None else environ
    token = environ.get("REPOACCORD_TOKEN")
    if not token:
        result = _result("refuse", "REPOACCORD_TOKEN is required to read repository facts")
        _write_summary(result, environ.get("GITHUB_STEP_SUMMARY"))
        return 1
    event_path = environ.get("GITHUB_EVENT_PATH")
    repository = environ.get("GITHUB_REPOSITORY")
    if not event_path or not repository:
        result = _result("refuse", "GITHUB_EVENT_PATH and GITHUB_REPOSITORY are required")
        _write_summary(result, environ.get("GITHUB_STEP_SUMMARY"))
        return 1
    try:
        event_data = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result = _result("refuse", "GitHub event file could not be read")
        _write_summary(result, environ.get("GITHUB_STEP_SUMMARY"))
        return 1
    if not isinstance(event_data, Mapping):
        result = _result("refuse", "GitHub event file is invalid")
        _write_summary(result, environ.get("GITHUB_STEP_SUMMARY"))
        return 1
    api_url = environ.get("GITHUB_API_URL", "").strip() or "https://api.github.com"
    try:
        client = GitHubClient(
            transport or UrllibTransport(),
            token,
            repository,
            api_url,
            sleep=sleep or default_sleep,
        )
    except ValueError as exc:
        result = _result("refuse", str(exc))
        _write_summary(result, environ.get("GITHUB_STEP_SUMMARY"))
        return 1
    dry_run_value = environ.get("REPOACCORD_DRY_RUN", "")
    dry_run = dry_run_value.strip().casefold() != "false"
    config_path = environ.get("REPOACCORD_CONFIG_PATH", ".github/repoaccord.yml")
    result = run(event_data, client, config_path, dry_run)
    _write_summary(result, environ.get("GITHUB_STEP_SUMMARY"))
    return 0 if result.decision in {"act", "skip"} else 1
