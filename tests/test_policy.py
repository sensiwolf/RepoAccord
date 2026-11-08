import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from repoaccord.policy import (
    AddPullRequestMetadata,
    CreateIssue,
    CreatePullRequest,
    EnableAutoMerge,
    PushEvent,
    RepoFacts,
    decide,
    parse_template,
    precheck,
    render_pr_body,
)

ROOT = Path(__file__).parents[1]
PR_PATH = ".github/pull_request_template.md"
ISSUE_PATH = ".github/ISSUE_TEMPLATE/bug.md"
PR_TEMPLATE = "## Summary\n## Testing"
ISSUE_TEMPLATE = "## Details"
PR_BODY = "## Summary\nChange details.\n## Testing\nNot run."


def event(
    *,
    ref: str = "refs/heads/feat/example",
    actor: str = "contributor",
    bot: bool = False,
    deleted: bool = False,
    message: str = f"Implement example\n\n=== PR ===\n{PR_BODY}",
) -> PushEvent:
    return PushEvent(actor, bot, ref, deleted, message)


def facts(**changes: object) -> RepoFacts:
    values: dict[str, object] = {
        "default_branch": "main",
        "actor_permission": "write",
        "existing_pr": False,
        "paths": (PR_PATH,),
        "pr_template_text": PR_TEMPLATE,
        "issue_templates": {},
        "required_checks_proven": None,
    }
    values.update(changes)
    return RepoFacts(**values)  # type: ignore[arg-type]


def issue_message(pr_body: str = PR_BODY + "\n#<issue number>") -> str:
    return (
        "Implement example\n\n=== ISSUE ===\nBug title\n## Details\nIssue text\n=== PR ===\n"
        f"{pr_body}"
    )


class TestPolicy(unittest.TestCase):
    def test_precheck_only_skips_the_four_early_conditions(self) -> None:
        cases = (
            (event(ref="refs/tags/v1"), "not a branch"),
            (event(deleted=True), "deleted"),
            (event(ref="refs/heads/main"), "default branch"),
            (event(bot=True), "bot"),
            (event(actor="automation[bot]"), "bot"),
        )
        for pushed, reason in cases:
            with self.subTest(reason=reason):
                result = precheck(pushed, "main")
                self.assertIsNotNone(result)
                self.assertEqual(result.decision, "skip")
                self.assertIn(reason, result.reason)
        self.assertIsNone(precheck(event(), "main"))

    def test_custom_branch_prefix_is_loaded_before_policy_decides(self) -> None:
        custom = "branches:\n  prefixes: [claude/]"
        accepted = decide(
            event(ref="refs/heads/claude/task"),
            facts(),
            custom,
        )
        ignored = decide(
            event(ref="refs/heads/feat/x"),
            facts(),
            custom,
        )
        self.assertEqual(accepted.decision, "act")
        self.assertEqual(ignored.decision, "skip")

    def test_initial_skip_conditions(self) -> None:
        cases = (
            (event(ref="refs/tags/v1", deleted=True, bot=True), facts(), "not a branch"),
            (event(deleted=True), facts(), "deleted"),
            (event(ref="refs/heads/main"), facts(), "default branch"),
            (event(bot=True), facts(), "bot"),
            (event(actor="dependabot[bot]"), facts(), "bot"),
        )
        for pushed, repository, reason in cases:
            with self.subTest(reason=reason):
                plan = decide(pushed, repository, None)
                self.assertEqual(plan.decision, "skip")
                self.assertIn(reason, plan.reason)

    def test_invalid_branch_names_refuse_before_configuration(self) -> None:
        for branch in (
            "feat/$(rm -rf)",
            "feat/a;b",
            "feat/../x",
            "feat/x.lock",
            "feat//x",
            "/feat/x",
            "feat/x/",
            "feat/x.",
            "feat/" + "x" * 197,
        ):
            with self.subTest(branch=branch):
                result = decide(event(ref=f"refs/heads/{branch}"), facts(), "not: [valid")
                self.assertEqual(result.decision, "refuse")
                self.assertIn("branch name", result.reason)

    def test_invalid_configuration_refuses(self) -> None:
        plan = decide(event(), facts(), "unknown: true")
        self.assertEqual(plan.decision, "refuse")
        self.assertIn("unknown configuration key", plan.reason)

    def test_prefix_mismatch_skips(self) -> None:
        plan = decide(event(ref="refs/heads/topic/thing"), facts(), None)
        self.assertEqual(plan.decision, "skip")
        self.assertIn("allowed prefix", plan.reason)

    def test_allowlist_is_case_insensitive_and_authoritative(self) -> None:
        config_text = "actors:\n  allow: [alice]"
        allowed = decide(event(actor="Alice"), facts(actor_permission="read"), config_text)
        denied = decide(event(actor="Mallory"), facts(actor_permission="admin"), config_text)
        self.assertEqual(allowed.decision, "act")
        self.assertEqual(denied.decision, "refuse")
        self.assertIn("allowlist", denied.reason)

    def test_each_write_permission_is_accepted(self) -> None:
        for permission in ("admin", "maintain", "write"):
            with self.subTest(permission=permission):
                plan = decide(event(), facts(actor_permission=permission), None)
                self.assertEqual(plan.decision, "act")

    def test_missing_and_insufficient_permissions_refuse(self) -> None:
        for permission, reason in ((None, "could not be verified"), ("read", "write permission")):
            with self.subTest(permission=permission):
                plan = decide(event(), facts(actor_permission=permission), None)
                self.assertEqual(plan.decision, "refuse")
                self.assertIn(reason, plan.reason)

    def test_existing_pull_request_skips_before_parsing(self) -> None:
        plan = decide(
            event(message="bad message"),
            facts(existing_pr=True),
            None,
        )
        self.assertEqual(plan.decision, "skip")
        self.assertIn("already exists", plan.reason)

    def test_invalid_message_refuses(self) -> None:
        plan = decide(event(message="missing PR block"), facts(), None)
        self.assertEqual(plan.decision, "refuse")
        self.assertIn("PR", plan.reason)

    def test_pr_template_selection_and_validation_failures(self) -> None:
        cases = (
            (facts(paths=(PR_PATH,), pr_template_text=None), "could not be read"),
            (
                facts(paths=(PR_PATH,), pr_template_text=PR_TEMPLATE),
                "missing headings",
            ),
            (
                facts(paths=(".github/PULL_REQUEST_TEMPLATE/one.md",)),
                "multiple PR templates",
            ),
        )
        for repository, reason in cases:
            with self.subTest(reason=reason):
                message = event().commit_message
                if reason == "missing headings":
                    message = "Implement example\n\n=== PR ===\n## Summary\nDone"
                plan = decide(event(message=message), repository, None)
                self.assertEqual(plan.decision, "refuse")
                self.assertIn(reason, plan.reason)

    def test_no_pr_template_adds_note_and_uses_default_branch(self) -> None:
        plan = decide(event(), facts(paths=(), pr_template_text=None), None)
        self.assertEqual(plan.decision, "act")
        self.assertIn("no PR template found; body not validated", plan.notes)
        action = plan.actions[0]
        self.assertIsInstance(action, CreatePullRequest)
        self.assertEqual(action.base, "main")
        self.assertEqual(action.head, "feat/example")

    def test_issue_template_is_validated_and_labels_follow_precedence(self) -> None:
        repository = facts(
            paths=(PR_PATH, ISSUE_PATH),
            issue_templates={ISSUE_PATH: "---\nlabels: [bug, urgent]\n---\n## Details"},
        )
        valid = decide(
            event(message=issue_message()),
            repository,
            None,
        )
        self.assertEqual(valid.decision, "act")
        self.assertIsInstance(valid.actions[0], CreateIssue)
        self.assertEqual(valid.actions[0].labels, ("bug", "urgent"))
        configured = decide(
            event(message=issue_message()),
            repository,
            "issues:\n  label: triage",
        )
        self.assertEqual(configured.actions[0].labels, ("triage",))

    def test_issue_template_is_parsed_once(self) -> None:
        repository = facts(
            paths=(PR_PATH, ISSUE_PATH),
            issue_templates={ISSUE_PATH: ISSUE_TEMPLATE},
        )
        with patch("repoaccord.policy.parse_template", wraps=parse_template) as parser:
            plan = decide(event(message=issue_message()), repository, None)
        self.assertEqual(plan.decision, "act")
        self.assertEqual(parser.call_count, 2)

    def test_issue_missing_template_adds_note_and_no_labels(self) -> None:
        message = issue_message()
        plan = decide(event(message=message), facts(paths=(PR_PATH,)), None)
        self.assertEqual(plan.decision, "act")
        self.assertIsInstance(plan.actions[0], CreateIssue)
        self.assertEqual(plan.actions[0].labels, ())
        self.assertIn("no issue template found; issue body not validated", plan.notes)

    def test_issue_selection_and_validation_refuse(self) -> None:
        cases = (
            (
                facts(
                    paths=(PR_PATH, ISSUE_PATH),
                    issue_templates={},
                ),
                "contents could not be read",
                None,
            ),
            (
                facts(
                    paths=(
                        PR_PATH,
                        ".github/ISSUE_TEMPLATE/one.md",
                        ".github/ISSUE_TEMPLATE/two.md",
                    ),
                    issue_templates={},
                ),
                "set issues.template",
                None,
            ),
            (
                facts(
                    paths=(PR_PATH, ISSUE_PATH),
                    issue_templates={ISSUE_PATH: "## Details"},
                ),
                "missing headings",
                (
                    "Implement example\n\n=== ISSUE ===\nBug title\nNo required section\n"
                    "=== PR ===\n" + PR_BODY + "\n#<issue number>"
                ),
            ),
        )
        for repository, reason, custom_message in cases:
            with self.subTest(reason=reason):
                plan = decide(
                    event(message=custom_message or issue_message()),
                    repository,
                    None,
                )
                self.assertEqual(plan.decision, "refuse")
                self.assertIn(reason, plan.reason)

    def test_issue_requires_one_placeholder(self) -> None:
        for body in (
            PR_BODY,
            PR_BODY.replace("Change details.", "PLACEHOLDER PLACEHOLDER"),
        ):
            with self.subTest(body=body):
                plan = decide(event(message=issue_message(body)), facts(), None)
                self.assertEqual(plan.decision, "refuse")
                self.assertIn("exactly once", plan.reason)

    def test_no_issue_forbids_placeholder(self) -> None:
        message = f"Implement example\n\n=== PR ===\n{PR_BODY}\n#<issue number>"
        plan = decide(event(message=message), facts(), None)
        self.assertEqual(plan.decision, "refuse")
        self.assertIn("without an issue", plan.reason)

    def test_actions_are_exactly_the_supported_action_types(self) -> None:
        self.assertEqual(
            {
                CreateIssue.__name__,
                CreatePullRequest.__name__,
                AddPullRequestMetadata.__name__,
                EnableAutoMerge.__name__,
            },
            {
                "CreateIssue",
                "CreatePullRequest",
                "AddPullRequestMetadata",
                "EnableAutoMerge",
            },
        )
        issue_plan = decide(
            event(message=issue_message()),
            facts(paths=(PR_PATH,)),
            None,
        )
        self.assertEqual(
            tuple(type(action) for action in issue_plan.actions),
            (CreateIssue, CreatePullRequest),
        )

    def test_default_plan_is_unchanged_and_metadata_follows_pull_request(self) -> None:
        baseline = decide(
            event(message=issue_message()),
            facts(paths=(PR_PATH, ISSUE_PATH), issue_templates={ISSUE_PATH: ISSUE_TEMPLATE}),
            None,
        )
        self.assertEqual(
            baseline.actions,
            (
                CreateIssue("Bug title", "## Details\nIssue text", ()),
                CreatePullRequest(
                    "Implement example",
                    PR_BODY + "\n#<issue number>",
                    "main",
                    "feat/example",
                    "#<issue number>",
                ),
            ),
        )
        configured = decide(
            event(message=issue_message()),
            facts(
                paths=(PR_PATH, ISSUE_PATH),
                issue_templates={ISSUE_PATH: ISSUE_TEMPLATE},
                required_checks_proven=True,
            ),
            "issues:\n  assignees: [issue-owner]\n"
            "pull_request:\n  reviewers: [reviewer]\n  assignees: [pr-owner]\n"
            "  labels: [triage]\nauto_merge:\n  enabled: true",
        )
        self.assertEqual(configured.decision, "act")
        self.assertEqual(
            tuple(type(action) for action in configured.actions),
            (CreateIssue, CreatePullRequest, AddPullRequestMetadata, EnableAutoMerge),
        )
        self.assertEqual(configured.actions[0].assignees, ("issue-owner",))
        metadata = configured.actions[2]
        self.assertIsInstance(metadata, AddPullRequestMetadata)
        if isinstance(metadata, AddPullRequestMetadata):
            self.assertEqual(metadata.reviewers, ("reviewer",))
            self.assertEqual(metadata.assignees, ("pr-owner",))
            self.assertEqual(metadata.labels, ("triage",))

    def test_metadata_is_planned_for_labels_only_and_omitted_when_empty(self) -> None:
        labels_only = decide(event(), facts(), "pull_request:\n  labels: [triage]")
        self.assertEqual(
            tuple(type(action) for action in labels_only.actions),
            (CreatePullRequest, AddPullRequestMetadata),
        )
        metadata = labels_only.actions[1]
        self.assertEqual(metadata, AddPullRequestMetadata((), (), ("triage",)))
        default = decide(
            event(), facts(), "pull_request:\n  reviewers: []\n  assignees: []\n  labels: []"
        )
        self.assertEqual(tuple(type(action) for action in default.actions), (CreatePullRequest,))

    def test_auto_merge_check_states_and_method(self) -> None:
        for proof, expected, note in (
            (True, True, False),
            (False, False, True),
            (None, False, True),
        ):
            with self.subTest(proof=proof):
                plan = decide(
                    event(),
                    facts(required_checks_proven=proof),
                    "auto_merge:\n  enabled: true\n  method: rebase",
                )
                merge_actions = [a for a in plan.actions if isinstance(a, EnableAutoMerge)]
                self.assertEqual(bool(merge_actions), expected)
                self.assertEqual(
                    any("auto-merge skipped" in value for value in plan.notes),
                    note,
                )
                if merge_actions:
                    self.assertEqual(merge_actions[0].method, "rebase")

    def test_all_merge_methods_propagate(self) -> None:
        for method in ("merge", "squash", "rebase"):
            with self.subTest(method=method):
                plan = decide(
                    event(),
                    facts(required_checks_proven=True),
                    f"auto_merge:\n  enabled: true\n  method: {method}",
                )
                action = plan.actions[-1]
                self.assertEqual(action, EnableAutoMerge(method))

    def test_render_pr_body(self) -> None:
        self.assertEqual(render_pr_body("Fix #<n>", "#<n>", 42), "Fix #42")
        self.assertEqual(render_pr_body("No issue", "#<n>", None), "No issue")
        for body, placeholder, number in (
            ("#<n> #<n>", "#<n>", 1),
            ("none", "#<n>", 1),
            ("has #<n>", "#<n>", None),
        ):
            with self.subTest(body=body, number=number), self.assertRaises(ValueError):
                render_pr_body(body, placeholder, number)

    def test_pure_modules_import_only_allowed_modules(self) -> None:
        pure_allowed = {
            "collections.abc",
            "dataclasses",
            "re",
            "typing",
            "yaml",
            "yaml.constructor",
            "yaml.nodes",
            "repoaccord.config",
            "repoaccord.errors",
            "repoaccord.message",
            "repoaccord.templates",
        }
        source = ROOT / "src" / "repoaccord"
        all_imports: dict[str, set[str]] = {}
        for path in source.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            all_imports[path.name] = imported

        for name in ("config.py", "message.py", "templates.py", "policy.py"):
            imported = all_imports[name]
            with self.subTest(module=name):
                self.assertLessEqual(imported, pure_allowed)
                self.assertFalse(
                    imported & {"repoaccord.github_api", "repoaccord.run", "repoaccord.cli"}
                )
                self.assertFalse(
                    any(
                        module == forbidden or module.startswith(forbidden + ".")
                        for module in imported
                        for forbidden in (
                            "socket",
                            "ssl",
                            "http",
                            "urllib",
                            "subprocess",
                            "requests",
                            "os",
                            "pathlib",
                        )
                    )
                )

        urllib_users = {
            name
            for name, imported in all_imports.items()
            if any(module == "urllib" or module.startswith("urllib.") for module in imported)
        }
        self.assertEqual(urllib_users, {"github_api.py"})
        for name, imported in all_imports.items():
            if name not in {"cli.py", "run.py"}:
                self.assertFalse(imported & {"os", "pathlib"}, name)

        io_methods = {"open", "read_text", "read_bytes", "write_text", "write_bytes"}
        for name in (
            "config.py",
            "message.py",
            "templates.py",
            "policy.py",
            "github_api.py",
            "__main__.py",
        ):
            tree = ast.parse((source / name).read_text(encoding="utf-8"))
            calls: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    calls.add("open")
                elif isinstance(node.func, ast.Attribute) and node.func.attr in io_methods:
                    value = node.func.value
                    if not (
                        node.func.attr == "open"
                        and isinstance(value, ast.Name)
                        and value.id == "opener"
                    ):
                        calls.add(node.func.attr)
            self.assertFalse(calls & io_methods, name)

        file_readers = {"open", "read_text", "read_bytes", "write_text", "write_bytes"}
        for path in source.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    calls.add("open")
                elif isinstance(node.func, ast.Attribute) and node.func.attr in file_readers:
                    value = node.func.value
                    if not (
                        node.func.attr == "open"
                        and isinstance(value, ast.Name)
                        and value.id == "opener"
                    ):
                        calls.add(node.func.attr)
            if path.name not in {"cli.py", "run.py"}:
                self.assertFalse(calls & file_readers, path.name)
            environment_reads = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and node.attr == "environ"
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ]
            if path.name != "run.py":
                self.assertFalse(environment_reads, path.name)

    def test_api_module_has_no_prohibited_write_methods(self) -> None:
        tree = ast.parse(
            (ROOT / "src" / "repoaccord" / "github_api.py").read_text(encoding="utf-8")
        )
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertFalse(defined & {"merge", "approve", "push", "delete", "force"})


if __name__ == "__main__":
    unittest.main()
