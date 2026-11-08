"""Load offline plan input and serialize the resulting plan."""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from repoaccord.policy import (
    CreateIssue,
    CreatePullRequest,
    EnableAutoMerge,
    Plan,
    PushEvent,
    RepoFacts,
    decide,
)


def _object(value: Any, path: str, required: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{path} must contain exactly: {', '.join(sorted(required))}")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{path} must be a string or null")
    return value


def _load_input(path: Path) -> tuple[PushEvent, RepoFacts, str | None]:
    data = json.loads(path.read_text(encoding="utf-8"))
    root = _object(data, "input", {"event", "facts", "config_text"})
    event_data = _object(
        root["event"],
        "event",
        {"actor", "actor_is_bot", "ref", "deleted", "commit_message"},
    )
    event = PushEvent(
        actor=_string(event_data["actor"], "event.actor"),
        actor_is_bot=_boolean(event_data["actor_is_bot"], "event.actor_is_bot"),
        ref=_string(event_data["ref"], "event.ref"),
        deleted=_boolean(event_data["deleted"], "event.deleted"),
        commit_message=_string(event_data["commit_message"], "event.commit_message"),
    )
    facts_data = _object(
        root["facts"],
        "facts",
        {
            "default_branch",
            "actor_permission",
            "existing_pr",
            "paths",
            "pr_template_text",
            "issue_templates",
            "required_checks_proven",
        },
    )
    raw_paths = facts_data["paths"]
    if not isinstance(raw_paths, list) or any(not isinstance(item, str) for item in raw_paths):
        raise ValueError("facts.paths must be a list of strings")
    raw_templates = facts_data["issue_templates"]
    if not isinstance(raw_templates, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in raw_templates.items()
    ):
        raise ValueError("facts.issue_templates must map strings to strings")
    actor_permission = _optional_string(
        facts_data["actor_permission"],
        "facts.actor_permission",
    )
    pr_template_text = _optional_string(
        facts_data["pr_template_text"],
        "facts.pr_template_text",
    )
    required_checks_proven = facts_data["required_checks_proven"]
    if required_checks_proven is not None and not isinstance(required_checks_proven, bool):
        raise ValueError("facts.required_checks_proven must be a boolean or null")
    facts = RepoFacts(
        default_branch=_string(facts_data["default_branch"], "facts.default_branch"),
        actor_permission=actor_permission,
        existing_pr=_boolean(facts_data["existing_pr"], "facts.existing_pr"),
        paths=tuple(raw_paths),
        pr_template_text=pr_template_text,
        issue_templates=raw_templates,
        required_checks_proven=required_checks_proven,
    )
    config_text = _optional_string(root["config_text"], "config_text")
    return event, facts, config_text


def _plan_data(plan: Plan) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for action in plan.actions:
        if isinstance(action, (CreateIssue, CreatePullRequest, EnableAutoMerge)):
            data = vars(action).copy()
            data["type"] = type(action).__name__
            actions.append(data)
    return {
        "decision": plan.decision,
        "reason": plan.reason,
        "actions": actions,
        "notes": list(plan.notes),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline plan subcommand."""
    parser = argparse.ArgumentParser(prog="repoaccord")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--input", required=True)
    arguments = parser.parse_args(argv)
    try:
        event, facts, config_text = _load_input(Path(arguments.input))
        plan = decide(event, facts, config_text)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"RepoAccord: invalid plan input ({type(exc).__name__})\n")
        return 2
    sys.stdout.write(json.dumps(_plan_data(plan), sort_keys=True) + "\n")
    return 1 if plan.decision == "refuse" else 0
