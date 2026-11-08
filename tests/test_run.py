import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from repoaccord.github_api import GitHubClient, Response, UrllibTransport
from repoaccord.run import (
    RunResult,
    _emit,
    _escape_workflow,
    _write_summary,
    run,
    run_from_environment,
)

FAKE_CREDENTIAL = "test-token-123"
PR_TEMPLATE = "## Summary\n## Testing"
PR_BODY = "## Summary\nChange details.\n## Testing\nNot run."
ISSUE_TEMPLATE = "## Details"
ISSUE_BODY = "## Details\nIssue details."


class RoutingTransport:
    def __init__(
        self,
        *,
        issue: bool = False,
        auto_merge: bool = False,
        fail_pr: bool = False,
        fail_graphql: bool = False,
        default_branch: str = "main",
        config_text: str | None = None,
    ) -> None:
        self.issue = issue
        self.auto_merge = auto_merge
        self.fail_pr = fail_pr
        self.fail_graphql = fail_graphql
        self.default_branch = default_branch
        self.config_text = config_text
        self.requests: list[tuple[str, str, dict[str, str], bytes | None, int]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: int,
    ) -> Response:
        self.requests.append((method, url, dict(headers), body, timeout))
        parsed = urlsplit(url)
        path = parsed.path
        if method == "POST":
            payload = json.loads(body or b"{}")
            if path.endswith("/issues"):
                return Response(
                    201,
                    {},
                    json.dumps(
                        {"number": 17, "html_url": "https://github.test/issues/17"}
                    ).encode(),
                )
            if path.endswith("/pulls"):
                status = 500 if self.fail_pr else 201
                return Response(
                    status,
                    {},
                    json.dumps(
                        {
                            "number": 21,
                            "html_url": "https://github.test/pulls/21",
                            "node_id": "PR_node",
                        }
                    ).encode(),
                )
            if path.endswith("/graphql"):
                if self.fail_graphql:
                    return Response(200, {}, b'{"errors":[{"message":"failure"}]}')
                return Response(200, {}, b'{"data":{}}')
            raise AssertionError(f"unexpected POST path {path}: {payload}")
        if path.endswith("/repos/owner/repo"):
            return _json(200, {"default_branch": self.default_branch})
        if "/contents/" in path and path.endswith("/repoaccord.yml"):
            return (
                Response(200, {}, self.config_text.encode())
                if self.config_text is not None
                else Response(404, {}, b"")
            )
        if "/collaborators/" in path:
            return _json(200, {"permission": "write"})
        if path.endswith("/pulls"):
            return _json(200, [])
        if path.endswith("/rules/branches/main"):
            if self.auto_merge:
                return _json(
                    200,
                    [
                        {
                            "type": "required_status_checks",
                            "parameters": {"required_status_checks": [{"context": "ci"}]},
                        }
                    ],
                )
            raise AssertionError("rules queried when auto-merge is disabled")
        if path.endswith("/contents"):
            return _json(
                200,
                [
                    {"type": "file", "name": "README.md"},
                    {"type": "dir", "name": ".github"},
                    {"type": "dir", "name": "docs"},
                ],
            )
        if path.endswith("/contents/.github"):
            entries = [{"type": "file", "name": "pull_request_template.md"}]
            if self.issue:
                entries.append({"type": "dir", "name": "ISSUE_TEMPLATE"})
            return _json(200, entries)
        if path.endswith("/contents/.github/ISSUE_TEMPLATE"):
            return _json(
                200,
                [{"type": "file", "name": "bug.md"}] if self.issue else [],
            )
        if path.endswith("/contents/docs"):
            return _json(404, {})
        if path.endswith("/contents/.github/pull_request_template.md"):
            return Response(200, {}, PR_TEMPLATE.encode())
        if path.endswith("/contents/.github/ISSUE_TEMPLATE/bug.md"):
            return Response(200, {}, ISSUE_TEMPLATE.encode())
        raise AssertionError(f"unexpected request {method} {url}")


def _json(status: int, value: object) -> Response:
    return Response(status, {}, json.dumps(value).encode())


def push_event(
    *,
    ref: str = "refs/heads/feat/report",
    deleted: bool = False,
    sender_type: str = "User",
    commit_message: str | None = None,
    repository: str = "owner/repo",
    event_default_branch: str = "main",
) -> dict[str, object]:
    if commit_message is None:
        commit_message = f"Add report\n\n=== PR ===\n{PR_BODY}"
    return {
        "ref": ref,
        "deleted": deleted,
        "head_commit": {"message": commit_message, "author": {"name": "untrusted"}},
        "pusher": {"name": "untrusted"},
        "sender": {"login": "sender-login", "type": sender_type},
        "repository": {
            "full_name": repository,
            "default_branch": event_default_branch,
        },
    }


def event_file(data: object) -> tempfile.TemporaryDirectory[str]:
    directory = tempfile.TemporaryDirectory()
    Path(directory.name, "event.json").write_text(json.dumps(data), encoding="utf-8")
    return directory


def environment(directory: tempfile.TemporaryDirectory[str], **values: str) -> dict[str, str]:
    result = {
        "GITHUB_EVENT_PATH": str(Path(directory.name, "event.json")),
        "GITHUB_REPOSITORY": "owner/repo",
        "REPOACCORD_TOKEN": FAKE_CREDENTIAL,
    }
    result.update(values)
    return result


class TestRun(unittest.TestCase):
    def test_module_without_arguments_calls_runner_with_real_transport(self) -> None:
        with patch("repoaccord.run.run_from_environment", return_value=0) as runner:
            with patch.object(sys, "argv", ["repoaccord"]):
                with self.assertRaises(SystemExit) as caught:
                    runpy.run_module("repoaccord.__main__", run_name="__main__")
        self.assertEqual(caught.exception.code, 0)
        runner.assert_called_once()
        arguments = runner.call_args.args
        self.assertIsNone(arguments[0])
        self.assertIsInstance(arguments[1], UrllibTransport)

    def test_sender_is_used_instead_of_pusher_or_author(self) -> None:
        transport = RoutingTransport()
        output = StringIO()
        with redirect_stdout(output):
            result = run(
                push_event(),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                True,
            )
        self.assertEqual(result.decision, "act")
        collaborator_urls = [
            request[1] for request in transport.requests if "/collaborators/" in request[1]
        ]
        self.assertEqual(len(collaborator_urls), 1)
        self.assertIn("/collaborators/sender-login/permission", collaborator_urls[0])
        self.assertNotIn("untrusted", collaborator_urls[0])

    def test_bot_sender_is_skipped_with_zero_api_calls(self) -> None:
        transport = RoutingTransport()
        output = StringIO()
        with redirect_stdout(output):
            result = run(
                push_event(sender_type="Bot"),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo"),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "skip")
        self.assertEqual(transport.requests, [])

    def test_tags_deleted_and_default_branch_use_zero_api_calls(self) -> None:
        events = (
            push_event(ref="refs/tags/v1"),
            push_event(deleted=True),
            push_event(ref="refs/heads/main"),
        )
        for pushed in events:
            with self.subTest(ref=pushed["ref"], deleted=pushed["deleted"]):
                transport = RoutingTransport()
                with redirect_stdout(StringIO()):
                    result = run(
                        pushed,
                        GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo"),
                        ".github/repoaccord.yml",
                        False,
                    )
                self.assertEqual(result.decision, "skip")
                self.assertEqual(transport.requests, [])

    def test_event_repository_mismatch_is_refused_without_api(self) -> None:
        transport = RoutingTransport()
        with redirect_stdout(StringIO()):
            result = run(
                push_event(repository="other/repo"),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo"),
                ".github/repoaccord.yml",
                True,
            )
        self.assertEqual(result.decision, "refuse")
        self.assertIn("does not match", result.reason)
        self.assertEqual(transport.requests, [])

    def test_event_default_branch_mismatch_uses_api_and_adds_note(self) -> None:
        transport = RoutingTransport()
        with redirect_stdout(StringIO()):
            result = run(
                push_event(event_default_branch="trunk"),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo"),
                ".github/repoaccord.yml",
                True,
            )
        self.assertEqual(result.decision, "act")
        self.assertTrue(
            any("event default branch differed from API" in note for note in result.notes)
        )

    def test_missing_event_fields_refuse(self) -> None:
        for pushed in (
            {},
            {"repository": {"full_name": "owner/repo"}, "sender": {}},
            {
                **push_event(),
                "head_commit": None,
            },
        ):
            with self.subTest(pushed=pushed), redirect_stdout(StringIO()):
                result = run(
                    pushed,
                    GitHubClient(RoutingTransport(), FAKE_CREDENTIAL, "owner/repo"),
                    ".github/repoaccord.yml",
                    True,
                )
            self.assertEqual(result.decision, "refuse")

    def test_dry_run_values_never_post(self) -> None:
        values = (None, "", "yes", "true")
        for value in values:
            with self.subTest(value=value):
                directory = event_file(push_event())
                try:
                    transport = RoutingTransport()
                    env = environment(directory)
                    if value is not None:
                        env["REPOACCORD_DRY_RUN"] = value
                    with redirect_stdout(StringIO()):
                        result_code = run_from_environment(
                            env,
                            transport,
                            sleep=lambda _: None,
                        )
                    self.assertEqual(result_code, 0)
                    self.assertFalse(any(request[0] == "POST" for request in transport.requests))
                finally:
                    directory.cleanup()

    def test_false_dry_run_executes_single_pull_request(self) -> None:
        for value in ("false", " FALSE "):
            with self.subTest(value=value):
                directory = event_file(push_event())
                try:
                    transport = RoutingTransport()
                    with redirect_stdout(StringIO()):
                        code = run_from_environment(
                            environment(directory, REPOACCORD_DRY_RUN=value),
                            transport,
                            sleep=lambda _: None,
                        )
                    self.assertEqual(code, 0)
                    posts = [request for request in transport.requests if request[0] == "POST"]
                    self.assertEqual(
                        [urlsplit(request[1]).path.rsplit("/", 1)[-1] for request in posts],
                        ["pulls"],
                    )
                finally:
                    directory.cleanup()

    def test_issue_pr_and_graphql_execute_in_order_with_marker(self) -> None:
        message = (
            "Add report\n\n=== ISSUE ===\nBug title\n## Details\nIssue text\n=== PR ===\n"
            "## Summary\nDetails.\n## Testing\nDone\n#<issue number>"
        )
        config = "auto_merge:\n  enabled: true\n  method: squash"
        transport = RoutingTransport(issue=True, auto_merge=True, config_text=config)
        with redirect_stdout(StringIO()):
            result = run(
                push_event(commit_message=message),
                GitHubClient(
                    transport,
                    FAKE_CREDENTIAL,
                    "owner/repo",
                    sleep=lambda _: None,
                ),
                ".github/repoaccord.yml",
                False,
            )
        posts = [request for request in transport.requests if request[0] == "POST"]
        self.assertEqual(
            [urlsplit(request[1]).path.rsplit("/", 1)[-1] for request in posts],
            ["issues", "pulls", "graphql"],
        )
        issue_payload = json.loads(posts[0][3] or b"")
        pr_payload = json.loads(posts[1][3] or b"")
        graph_payload = json.loads(posts[2][3] or b"")
        marker = "<!-- repoaccord:branch=feat/report -->"
        self.assertTrue(issue_payload["body"].endswith(marker))
        self.assertIn("#17", pr_payload["body"])
        self.assertTrue(pr_payload["body"].endswith(marker))
        self.assertEqual(graph_payload["variables"], {"id": "PR_node", "method": "SQUASH"})
        self.assertEqual(result.issue.number if result.issue else None, 17)
        self.assertEqual(result.pull_request.number if result.pull_request else None, 21)

    def test_missing_issue_placeholder_refuses_without_posts(self) -> None:
        message = f"Add report\n\n=== ISSUE ===\nBug title\n## Details\nBody\n=== PR ===\n{PR_BODY}"
        transport = RoutingTransport(issue=False)
        with redirect_stdout(StringIO()):
            result = run(
                push_event(commit_message=message),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "refuse")
        self.assertFalse(any(request[0] == "POST" for request in transport.requests))

    def test_failure_after_issue_creation_reports_recovery_url(self) -> None:
        message = (
            "Add report\n\n=== ISSUE ===\nBug title\n## Details\nIssue text\n=== PR ===\n"
            "## Summary\nDetails.\n## Testing\nDone\n#<issue number>"
        )
        transport = RoutingTransport(issue=True, fail_pr=True)
        output = StringIO()
        directory = event_file(push_event(commit_message=message))
        try:
            with redirect_stdout(output):
                code = run_from_environment(
                    environment(directory, REPOACCORD_DRY_RUN="false"),
                    transport,
                    sleep=lambda _: None,
                )
        finally:
            directory.cleanup()
        self.assertEqual(code, 1)
        self.assertIn("issue #17", output.getvalue())
        self.assertIn("https://github.test/issues/17", output.getvalue())
        posts = [request for request in transport.requests if request[0] == "POST"]
        self.assertEqual(len(posts), 2)

    def test_graphql_failure_after_issue_reports_issue_reference(self) -> None:
        message = (
            "Add report\n\n=== ISSUE ===\nBug title\n## Details\nIssue text\n=== PR ===\n"
            "## Summary\nDetails.\n## Testing\nDone\n#<issue number>"
        )
        transport = RoutingTransport(
            issue=True,
            auto_merge=True,
            fail_graphql=True,
            config_text="auto_merge:\n  enabled: true\n  method: merge",
        )
        output = StringIO()
        with redirect_stdout(output):
            result = run(
                push_event(commit_message=message),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "error")
        self.assertIn("issue #17", output.getvalue())
        self.assertIn("https://github.test/issues/17", output.getvalue())
        self.assertIn("pull request #21", output.getvalue())

    def test_missing_token_refuses_even_in_dry_run(self) -> None:
        directory = event_file(push_event())
        try:
            transport = RoutingTransport()
            output = StringIO()
            with redirect_stdout(output):
                code = run_from_environment(
                    {
                        "GITHUB_EVENT_PATH": str(Path(directory.name, "event.json")),
                        "GITHUB_REPOSITORY": "owner/repo",
                        "REPOACCORD_DRY_RUN": "true",
                    },
                    transport,
                )
            self.assertEqual(code, 1)
            self.assertEqual(transport.requests, [])
            self.assertIn("REPOACCORD_TOKEN is required", output.getvalue())
        finally:
            directory.cleanup()

    def test_event_file_errors_and_invalid_json_refuse(self) -> None:
        for content in (None, "{"):
            with self.subTest(content=content):
                directory = tempfile.TemporaryDirectory()
                path = Path(directory.name, "event.json")
                if content is not None:
                    path.write_text(content, encoding="utf-8")
                output = StringIO()
                with redirect_stdout(output):
                    code = run_from_environment(
                        {
                            "GITHUB_EVENT_PATH": str(path),
                            "GITHUB_REPOSITORY": "owner/repo",
                            "REPOACCORD_TOKEN": FAKE_CREDENTIAL,
                        },
                        RoutingTransport(),
                    )
                self.assertEqual(code, 1)
                self.assertIn("event file could not be read", output.getvalue())
                directory.cleanup()

    def test_api_url_and_config_path_are_passed(self) -> None:
        directory = event_file(push_event())
        try:
            transport = RoutingTransport()
            env = environment(
                directory,
                GITHUB_API_URL="https://github.enterprise/api/v3",
                REPOACCORD_CONFIG_PATH="custom/config.yml",
            )
            with redirect_stdout(StringIO()):
                run_from_environment(env, transport, sleep=lambda _: None)
            self.assertTrue(
                all(
                    request[1].startswith("https://github.enterprise/api/v3/")
                    for request in transport.requests
                )
            )
            self.assertTrue(
                any("contents/custom/config.yml" in request[1] for request in transport.requests)
            )
        finally:
            directory.cleanup()

    def test_action_output_escape_sequences(self) -> None:
        self.assertEqual(
            _escape_workflow("%\r\n::error::x"),
            "%25%0D%0A%3A%3Aerror::x",
        )

    def test_notes_are_escaped_before_printing(self) -> None:
        output = StringIO()
        result = RunResult("skip", "reason", ("%\r\n::error::fake",))
        with redirect_stdout(output):
            _emit(result)
        self.assertIn("note: %25%0D%0A%3A%3Aerror::fake", output.getvalue())
        self.assertNotIn("\n::error::fake", output.getvalue())

    def test_summary_is_markdown_escaped_and_omits_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory, "summary.md")
            result = RunResult(
                "act",
                "reason *with* `markup`",
                ("line\nnext",),
                actions=("CreatePullRequest",),
            )
            _write_summary(result, str(summary_path))
            content = summary_path.read_text(encoding="utf-8")
            self.assertIn(r"reason \*with\* \`markup\`", content)
            self.assertIn(r"line next", content)
            self.assertNotIn(FAKE_CREDENTIAL, content)

    def test_summary_file_from_environment_is_written(self) -> None:
        directory = event_file(push_event())
        try:
            summary = Path(directory.name, "summary.md")
            env = environment(directory, GITHUB_STEP_SUMMARY=str(summary))
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    run_from_environment(env, RoutingTransport(), sleep=lambda _: None),
                    0,
                )
            self.assertIn("## RepoAccord", summary.read_text(encoding="utf-8"))
        finally:
            directory.cleanup()

    def test_auto_merge_disabled_makes_no_rules_or_protection_calls(self) -> None:
        transport = RoutingTransport(auto_merge=False)
        with redirect_stdout(StringIO()):
            run(
                push_event(),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                True,
            )
        self.assertFalse(
            any(
                "/rules/" in request[1] or "/protection/" in request[1]
                for request in transport.requests
            )
        )

    def test_token_not_present_in_printed_output(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            run_from_environment(
                {"REPOACCORD_TOKEN": FAKE_CREDENTIAL},
                RoutingTransport(),
            )
        self.assertNotIn(FAKE_CREDENTIAL, output.getvalue())

    def test_default_branch_mismatch_never_changes_api_base(self) -> None:
        transport = RoutingTransport(default_branch="trunk")
        with redirect_stdout(StringIO()):
            result = run(
                push_event(event_default_branch="main"),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                True,
            )
        self.assertEqual(result.decision, "act")
        self.assertTrue(
            any("event default branch differed from API" in note for note in result.notes)
        )

    def test_skip_and_act_exit_codes(self) -> None:
        for pushed, expected in (
            (push_event(ref="refs/tags/v2"), 0),
            (push_event(commit_message="bad"), 1),
        ):
            directory = event_file(pushed)
            try:
                output = StringIO()
                with redirect_stdout(output):
                    code = run_from_environment(
                        environment(directory, REPOACCORD_DRY_RUN="false"),
                        RoutingTransport(),
                        sleep=lambda _: None,
                    )
                self.assertEqual(code, expected)
            finally:
                directory.cleanup()

    def test_refusal_annotation_is_escaped(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            _emit(RunResult("refuse", "bad%\r\n::warning::"))
        self.assertIn("::error::bad%25%0D%0A%3A%3Awarning::", output.getvalue())

    def test_bot_suffix_is_skipped_before_api_calls(self) -> None:
        pushed = push_event()
        pushed["sender"] = {"login": "automation[bot]", "type": "User"}
        transport = RoutingTransport()
        with redirect_stdout(StringIO()):
            result = run(
                pushed,
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo"),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "skip")
        self.assertEqual(transport.requests, [])

    def test_missing_environment_settings_refuse_without_transport(self) -> None:
        for values in (
            {},
            {"REPOACCORD_TOKEN": FAKE_CREDENTIAL},
            {"GITHUB_EVENT_PATH": "missing", "REPOACCORD_TOKEN": FAKE_CREDENTIAL},
        ):
            with self.subTest(values=values):
                transport = RoutingTransport()
                with redirect_stdout(StringIO()):
                    code = run_from_environment(values, transport)
                self.assertEqual(code, 1)
                self.assertEqual(transport.requests, [])

    def test_non_mapping_event_json_is_refused(self) -> None:
        directory = tempfile.TemporaryDirectory()
        try:
            path = Path(directory.name, "event.json")
            path.write_text("[]", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = run_from_environment(
                    {
                        "GITHUB_EVENT_PATH": str(path),
                        "GITHUB_REPOSITORY": "owner/repo",
                        "REPOACCORD_TOKEN": FAKE_CREDENTIAL,
                    },
                    RoutingTransport(),
                )
            self.assertEqual(code, 1)
            self.assertIn("event file is invalid", output.getvalue())
        finally:
            directory.cleanup()

    def test_event_sender_fields_are_required(self) -> None:
        for sender in ({}, {"login": "alice"}, {"login": "alice", "type": 1}):
            pushed = push_event()
            pushed["sender"] = sender
            transport = RoutingTransport()
            with self.subTest(sender=sender), redirect_stdout(StringIO()):
                result = run(
                    pushed,
                    GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo"),
                    ".github/repoaccord.yml",
                    True,
                )
            self.assertEqual(result.decision, "refuse")
            self.assertEqual(transport.requests, [])

    def test_permission_unverified_refuses_without_posts(self) -> None:
        class NoPermissionTransport(RoutingTransport):
            def request(self, method, url, headers, body, timeout):
                if "/collaborators/" in url:
                    self.requests.append((method, url, dict(headers), body, timeout))
                    return _json(403, {})
                return super().request(method, url, headers, body, timeout)

        transport = NoPermissionTransport()
        with redirect_stdout(StringIO()):
            result = run(
                push_event(),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "refuse")
        self.assertIn("permission", result.reason)
        self.assertFalse(any(request[0] == "POST" for request in transport.requests))

    def test_invalid_config_refuses_before_other_repository_reads(self) -> None:
        transport = RoutingTransport(config_text="unknown: true")
        with redirect_stdout(StringIO()):
            result = run(
                push_event(),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "refuse")
        paths = [urlsplit(request[1]).path for request in transport.requests]
        self.assertTrue(any(path.endswith("/contents/.github/repoaccord.yml") for path in paths))
        self.assertFalse(any("/collaborators/" in path for path in paths))

    def test_required_checks_unknown_skips_auto_merge_action(self) -> None:
        config = "auto_merge:\n  enabled: true"

        class UnknownChecksTransport(RoutingTransport):
            def request(self, method, url, headers, body, timeout):
                if "/rules/branches/" in url:
                    self.requests.append((method, url, dict(headers), body, timeout))
                    return _json(403, {})
                if "/protection/" in url:
                    self.requests.append((method, url, dict(headers), body, timeout))
                    return _json(500, {})
                return super().request(method, url, headers, body, timeout)

        transport = UnknownChecksTransport(config_text=config)
        with redirect_stdout(StringIO()):
            result = run(
                push_event(),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                True,
            )
        self.assertEqual(result.decision, "act")
        self.assertIn(
            "auto-merge skipped because required checks could not be proven", result.notes
        )
        self.assertNotIn("EnableAutoMerge", result.actions)

    def test_issue_create_failure_has_no_fake_recovery_reference(self) -> None:
        class FailIssueTransport(RoutingTransport):
            def request(self, method, url, headers, body, timeout):
                if method == "POST" and url.endswith("/issues"):
                    self.requests.append((method, url, dict(headers), body, timeout))
                    return _json(500, {})
                return super().request(method, url, headers, body, timeout)

        message = (
            "Add report\n\n=== ISSUE ===\nBug\n## Details\nText\n=== PR ===\n"
            "## Summary\nDetails\n## Testing\nDone\n#<issue number>"
        )
        transport = FailIssueTransport(issue=True)
        output = StringIO()
        with redirect_stdout(output):
            result = run(
                push_event(commit_message=message),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        self.assertEqual(result.decision, "error")
        self.assertIsNone(result.issue)
        self.assertNotIn("issue #", output.getvalue())
        self.assertEqual(sum(request[0] == "POST" for request in transport.requests), 1)

    def test_act_without_issue_appends_marker_to_pr_body(self) -> None:
        transport = RoutingTransport()
        with redirect_stdout(StringIO()):
            run(
                push_event(),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        pr_request = next(
            request
            for request in transport.requests
            if request[0] == "POST" and request[1].endswith("/pulls")
        )
        body = json.loads(pr_request[3] or b"")["body"]
        self.assertTrue(body.endswith("<!-- repoaccord:branch=feat/report -->"))
        self.assertNotIn("#<issue number>", body)

    def test_default_dry_run_does_reads_and_no_writes(self) -> None:
        directory = event_file(push_event())
        try:
            transport = RoutingTransport()
            with redirect_stdout(StringIO()):
                code = run_from_environment(
                    environment(directory),
                    transport,
                    sleep=lambda _: None,
                )
            self.assertEqual(code, 0)
            self.assertTrue(any(request[0] == "GET" for request in transport.requests))
            self.assertFalse(any(request[0] == "POST" for request in transport.requests))
        finally:
            directory.cleanup()

    def test_config_path_read_uses_default_branch_ref(self) -> None:
        directory = event_file(push_event())
        try:
            transport = RoutingTransport()
            with redirect_stdout(StringIO()):
                run_from_environment(
                    environment(directory, REPOACCORD_CONFIG_PATH="custom/config.yml"),
                    transport,
                    sleep=lambda _: None,
                )
            config_request = next(
                request for request in transport.requests if "custom/config.yml" in request[1]
            )
            self.assertIn("ref=main", config_request[1])
        finally:
            directory.cleanup()

    def test_summary_appends_to_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "summary.md")
            path.write_text("Existing\n", encoding="utf-8")
            _write_summary(RunResult("skip", "reason"), str(path))
            content = path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("Existing\n"))
            self.assertIn("## RepoAccord", content)

    def test_markdown_summary_escapes_significant_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "summary.md")
            _write_summary(
                RunResult("refuse", "*_[]()#`!|"),
                str(path),
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn(r"\*\_\[\]\(\)\#\`\!\|", content)

    def test_summary_path_empty_is_not_opened(self) -> None:
        _write_summary(RunResult("skip", "reason"), "")

    def test_skip_output_has_no_error_annotation(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            _emit(RunResult("skip", "branch ignored"))
        self.assertNotIn("::error::", output.getvalue())

    def test_issue_body_empty_still_receives_marker(self) -> None:
        message = (
            "Add report\n\n=== ISSUE ===\nBug title\n=== PR ===\n"
            "## Summary\nDetails\n## Testing\nDone\n#<issue number>"
        )
        transport = RoutingTransport(issue=False)
        with redirect_stdout(StringIO()):
            run(
                push_event(commit_message=message),
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                False,
            )
        issue_request = next(request for request in transport.requests if request[0] == "POST")
        payload = json.loads(issue_request[3] or b"")
        self.assertEqual(payload["body"], "<!-- repoaccord:branch=feat/report -->")

    def test_fake_transport_is_the_only_transport_used_by_runner(self) -> None:
        directory = event_file(push_event(ref="refs/tags/v3"))
        try:
            transport = RoutingTransport()
            with redirect_stdout(StringIO()):
                result = run_from_environment(
                    environment(directory),
                    transport,
                    sleep=lambda _: None,
                )
            self.assertEqual(result, 0)
            self.assertEqual(transport.requests, [])
        finally:
            directory.cleanup()

    def test_api_url_rejection_prints_no_credential_or_environment(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            result = run_from_environment(
                {
                    "REPOACCORD_TOKEN": FAKE_CREDENTIAL,
                    "GITHUB_REPOSITORY": "owner/repo",
                    "GITHUB_EVENT_PATH": "unused",
                    "GITHUB_API_URL": "http://invalid",
                },
                RoutingTransport(),
            )
        self.assertEqual(result, 1)
        self.assertNotIn(FAKE_CREDENTIAL, output.getvalue())
        self.assertNotIn("http://invalid", output.getvalue())

    def test_event_sender_not_commit_author_is_the_only_actor_value(self) -> None:
        pushed = push_event()
        pushed["head_commit"] = {
            "message": f"Add report\n\n=== PR ===\n{PR_BODY}",
            "author": {"name": "commit-author", "username": "commit-user"},
        }
        pushed["pusher"] = {"name": "pusher-user"}
        pushed["sender"] = {"login": "trusted-sender", "type": "User"}
        transport = RoutingTransport()
        with redirect_stdout(StringIO()):
            run(
                pushed,
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", sleep=lambda _: None),
                ".github/repoaccord.yml",
                True,
            )
        permission_url = next(
            request[1] for request in transport.requests if "/collaborators/" in request[1]
        )
        self.assertIn("/collaborators/trusted-sender/", permission_url)
        self.assertNotIn("commit-author", permission_url)
        self.assertNotIn("pusher-user", permission_url)


if __name__ == "__main__":
    unittest.main()
