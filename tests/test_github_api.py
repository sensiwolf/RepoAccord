import json
import unittest
from unittest import mock
from urllib.request import Request

from repoaccord import __version__
from repoaccord.github_api import (
    GitHubClient,
    GitHubError,
    IssueRef,
    PullRequestRef,
    Response,
    UrllibTransport,
    _SameHostRedirectHandler,
)

FAKE_CREDENTIAL = "test-token-123"


class FakeTransport:
    def __init__(self, *responses: Response | Exception) -> None:
        self.responses = list(responses)
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
        if not self.responses:
            raise AssertionError("fake transport has no response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def json_response(status: int, value: object, headers: dict[str, str] | None = None) -> Response:
    return Response(
        status=status,
        headers=headers or {},
        body=json.dumps(value).encode("utf-8"),
    )


def client(transport: FakeTransport, sleeps: list[float] | None = None) -> GitHubClient:
    return GitHubClient(
        transport,
        FAKE_CREDENTIAL,
        "owner/repo",
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


class TestGitHubClient(unittest.TestCase):
    def test_headers_timeout_and_url_encoding(self) -> None:
        transport = FakeTransport(
            json_response(200, {"permission": "write"}),
            json_response(200, {"default_branch": "main"}),
        )
        api = client(transport)
        self.assertEqual(api.get_actor_permission("a b/ü"), "write")
        self.assertEqual(api.get_default_branch(), "main")
        method, url, headers, _, timeout = transport.requests[0]
        self.assertEqual(method, "GET")
        self.assertIn("collaborators/a%20b%2F%C3%BC/permission", url)
        self.assertEqual(headers["Authorization"], f"Bearer {FAKE_CREDENTIAL}")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")
        self.assertEqual(headers["User-Agent"], f"repoaccord/{__version__}")
        self.assertEqual(timeout, 20)
        self.assertEqual(transport.requests[1][1], "https://api.github.com/repos/owner/repo")

    def test_repository_and_api_url_validation(self) -> None:
        transport = FakeTransport()
        for repository in ("owner", "owner/repo/path", "owner/re po", "owner/repo "):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                GitHubClient(transport, FAKE_CREDENTIAL, repository)
        for url in ("http://api.example", "ftp://api.example"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                GitHubClient(transport, FAKE_CREDENTIAL, "owner/repo", api_url=url)

    def test_token_not_in_client_string_or_repr_or_url(self) -> None:
        transport = FakeTransport()
        api = client(transport)
        self.assertNotIn(FAKE_CREDENTIAL, str(api))
        self.assertNotIn(FAKE_CREDENTIAL, repr(api))
        with self.assertRaises(GitHubError):
            api.has_pull_request_for_branch(FAKE_CREDENTIAL)
        self.assertEqual(transport.requests, [])
        with self.assertRaisesRegex(ValueError, "must not contain"):
            GitHubClient(
                transport,
                FAKE_CREDENTIAL,
                "owner/repo",
                f"https://{FAKE_CREDENTIAL}.example",
            )

    def test_transport_error_text_is_redacted(self) -> None:
        transport = FakeTransport(GitHubError(500, f"bad {FAKE_CREDENTIAL} response"))
        with self.assertRaises(GitHubError) as caught:
            client(transport).get_default_branch()
        self.assertNotIn(FAKE_CREDENTIAL, str(caught.exception))
        self.assertNotIn(FAKE_CREDENTIAL, repr(caught.exception))

    def test_get_retries_503_then_succeeds(self) -> None:
        transport = FakeTransport(
            json_response(503, {}),
            json_response(503, {}),
            json_response(200, {"default_branch": "main"}),
        )
        sleeps: list[float] = []
        self.assertEqual(client(transport, sleeps).get_default_branch(), "main")
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(sleeps, [])

    def test_get_retries_at_most_twice(self) -> None:
        transport = FakeTransport(*(json_response(502, {}) for _ in range(4)))
        with self.assertRaisesRegex(GitHubError, "HTTP 502"):
            client(transport).get_default_branch()
        self.assertEqual(len(transport.requests), 3)

    def test_post_is_never_retried_on_503(self) -> None:
        transport = FakeTransport(json_response(503, {}), json_response(201, {}))
        with self.assertRaisesRegex(GitHubError, "HTTP 503"):
            client(transport).create_issue("title", "body", ())
        self.assertEqual(len(transport.requests), 1)

    def test_429_wait_of_five_seconds_retries(self) -> None:
        transport = FakeTransport(
            json_response(429, {}, {"Retry-After": "5"}),
            json_response(200, {"default_branch": "main"}),
        )
        waits: list[float] = []
        self.assertEqual(client(transport, waits).get_default_branch(), "main")
        self.assertEqual(waits, [5.0])
        self.assertEqual(len(transport.requests), 2)

    def test_403_rate_limit_reset_retries_once(self) -> None:
        transport = FakeTransport(
            json_response(403, {}, {"X-RateLimit-Reset": "1005"}),
            json_response(200, {"default_branch": "main"}),
        )
        waits: list[float] = []
        api = GitHubClient(
            transport,
            FAKE_CREDENTIAL,
            "owner/repo",
            sleep=waits.append,
        )
        with mock.patch("repoaccord.github_api.time.time", return_value=1000):
            self.assertEqual(api.get_default_branch(), "main")
        self.assertEqual(waits, [5.0])

    def test_retry_after_over_thirty_is_not_retried(self) -> None:
        transport = FakeTransport(json_response(429, {}, {"Retry-After": "120"}))
        with self.assertRaisesRegex(GitHubError, "HTTP 429"):
            client(transport).get_default_branch()
        self.assertEqual(len(transport.requests), 1)

    def test_permission_and_file_404_handling(self) -> None:
        transport = FakeTransport(
            json_response(404, {}),
            json_response(403, {}),
            json_response(200, {"default_branch": "main"}),
            Response(404, {}, b""),
        )
        api = client(transport)
        self.assertIsNone(api.get_actor_permission("missing"))
        self.assertIsNone(api.get_actor_permission("forbidden"))
        self.assertIsNone(api.read_file("missing.md"))
        self.assertEqual(len(transport.requests), 4)

    def test_directory_404_means_empty_and_paths_are_full(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "main"}),
            json_response(
                200,
                [
                    {"type": "file", "name": "README.md"},
                    {"type": "dir", "name": ".github"},
                    {"type": "dir", "name": "docs"},
                ],
            ),
            json_response(200, [{"type": "dir", "name": "ISSUE_TEMPLATE"}]),
            json_response(200, [{"type": "file", "name": "bug.md"}]),
            json_response(404, {}),
        )
        api = client(transport)
        paths = api.list_paths()
        self.assertEqual(
            paths,
            (
                ".github",
                ".github/ISSUE_TEMPLATE",
                ".github/ISSUE_TEMPLATE/bug.md",
                "README.md",
                "docs",
            ),
        )
        self.assertTrue(all("ref=main" in request[1] for request in transport.requests[1:]))

    def test_default_branch_is_used_for_read_file(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "trunk"}),
            Response(200, {}, b"template text"),
        )
        api = client(transport)
        self.assertEqual(api.read_file(".github/pull request.md"), "template text")
        self.assertIn("pull%20request.md?ref=trunk", transport.requests[-1][1])
        self.assertEqual(
            transport.requests[-1][2]["Accept"],
            "application/vnd.github.raw+json",
        )

    def test_permission_mapping(self) -> None:
        for value in ("admin", "maintain", "write", "read"):
            with self.subTest(value=value):
                api = client(FakeTransport(json_response(200, {"permission": value})))
                self.assertEqual(api.get_actor_permission("actor"), value)

    def test_has_pull_request_any_state(self) -> None:
        for returned, expected in (
            ([{"state": "open"}], True),
            ([{"state": "closed"}], True),
            ([{"state": "merged"}], True),
            ([], False),
        ):
            with self.subTest(returned=returned):
                transport = FakeTransport(json_response(200, returned))
                self.assertEqual(
                    client(transport).has_pull_request_for_branch("feat/a b/ü"), expected
                )
                self.assertIn(
                    "head=owner%3Afeat%2Fa+b%2F%C3%BC",
                    transport.requests[0][1],
                )
                self.assertIn("state=all", transport.requests[0][1])

    def test_required_checks_from_branch_rules(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                [
                    {
                        "type": "required_status_checks",
                        "parameters": {"required_status_checks": [{"context": "ci"}]},
                    }
                ],
            )
        )
        self.assertIs(client(transport).required_checks_proven("feat/a b/ü"), True)
        self.assertIn("feat%2Fa%20b%2F%C3%BC", transport.requests[0][1])
        self.assertEqual(len(transport.requests), 1)

    def test_required_checks_fall_back_to_legacy(self) -> None:
        transport = FakeTransport(
            json_response(200, [{"type": "required_status_checks", "parameters": {"checks": []}}]),
            json_response(200, {"contexts": ["ci"], "checks": []}),
        )
        self.assertIs(client(transport).required_checks_proven("main"), True)
        self.assertEqual(len(transport.requests), 2)

    def test_legacy_checks_and_contexts_are_recognized(self) -> None:
        for value in ({"contexts": ["ci"]}, {"checks": [{"context": "ci"}]}):
            with self.subTest(value=value):
                transport = FakeTransport(json_response(404, {}), json_response(200, value))
                self.assertIs(client(transport).required_checks_proven("main"), True)

    def test_legacy_404_is_false(self) -> None:
        transport = FakeTransport(json_response(404, {}), json_response(404, {}))
        self.assertIs(client(transport).required_checks_proven("main"), False)

    def test_required_checks_403_and_double_failure_are_unknown(self) -> None:
        for responses in (
            (json_response(403, {}), json_response(403, {})),
            (json_response(500, {}), json_response(500, {})),
        ):
            with self.subTest(responses=responses):
                self.assertIs(
                    client(FakeTransport(*responses)).required_checks_proven("main"), None
                )

    def test_empty_success_is_never_proven_true(self) -> None:
        transport = FakeTransport(
            json_response(200, []), json_response(200, {"contexts": [], "checks": []})
        )
        self.assertIs(client(transport).required_checks_proven("main"), False)

    def test_create_issue_payload_and_reference(self) -> None:
        transport = FakeTransport(
            json_response(201, {"number": 12, "html_url": "https://github.test/i/12"})
        )
        reference = client(transport).create_issue("title", "body", ("bug",))
        self.assertEqual(reference, IssueRef(12, "https://github.test/i/12"))
        request = transport.requests[0]
        self.assertEqual(request[0], "POST")
        self.assertEqual(request[1].endswith("/issues"), True)
        self.assertEqual(
            json.loads(request[3] or b""), {"title": "title", "body": "body", "labels": ["bug"]}
        )

    def test_create_pull_request_payload_and_reference(self) -> None:
        transport = FakeTransport(
            json_response(
                201,
                {"number": 3, "html_url": "https://github.test/p/3", "node_id": "PR_node"},
            )
        )
        reference = client(transport).create_pull_request("title", "body", "feat/thing", "main")
        self.assertEqual(reference, PullRequestRef(3, "https://github.test/p/3", "PR_node"))
        request = transport.requests[0]
        self.assertTrue(request[1].endswith("/pulls"))
        self.assertEqual(
            json.loads(request[3] or b""),
            {"title": "title", "body": "body", "head": "feat/thing", "base": "main"},
        )

    def test_enable_auto_merge_variables_and_enums(self) -> None:
        for method, enum in (("merge", "MERGE"), ("squash", "SQUASH"), ("rebase", "REBASE")):
            with self.subTest(method=method):
                transport = FakeTransport(json_response(200, {"data": {}}))
                client(transport).enable_auto_merge("node-1", method)
                request = transport.requests[0]
                self.assertEqual(request[0], "POST")
                self.assertEqual(request[1], "https://api.github.com/graphql")
                payload = json.loads(request[3] or b"")
                self.assertEqual(payload["variables"], {"id": "node-1", "method": enum})
                self.assertIn("$id", payload["query"])
                self.assertNotIn("node-1", payload["query"])

    def test_graphql_errors_raise_short_error(self) -> None:
        transport = FakeTransport(json_response(200, {"errors": [{"message": "details"}]}))
        with self.assertRaises(GitHubError) as caught:
            client(transport).enable_auto_merge("node-1", "merge")
        self.assertIn("GraphQL request returned errors", str(caught.exception))
        self.assertIn("details", str(caught.exception))

    def test_bad_json_and_non_success_raise(self) -> None:
        for response, text in (
            (Response(200, {}, b"not-json"), "invalid JSON"),
            (json_response(500, {"message": "ignored"}), "HTTP 500"),
        ):
            with self.subTest(response=response), self.assertRaisesRegex(GitHubError, text):
                client(FakeTransport(response)).get_default_branch()

    def test_api_error_includes_message_and_field_details(self) -> None:
        response = json_response(
            422,
            {
                "message": "Validation Failed",
                "errors": [
                    {"resource": "Issue", "field": "title", "code": "already_exists"},
                    {"field": "labels", "message": "invalid"},
                    {"message": "other"},
                    {"message": "must not be included"},
                ],
            },
        )
        with self.assertRaises(GitHubError) as caught:
            client(FakeTransport(response)).create_issue("title", "body", ())
        self.assertIn("Validation Failed", str(caught.exception))
        self.assertIn("title: already_exists", str(caught.exception))
        self.assertIn("labels: invalid", str(caught.exception))
        self.assertNotIn("must not be included", str(caught.exception))

    def test_api_error_redacts_token_and_truncates_details(self) -> None:
        response = json_response(
            422,
            {
                "message": f"Rejected {FAKE_CREDENTIAL}",
                "errors": [{"message": "x" * 500}],
            },
        )
        with self.assertRaises(GitHubError) as caught:
            client(FakeTransport(response)).create_issue("title", "body", ())
        self.assertNotIn(FAKE_CREDENTIAL, str(caught.exception))
        self.assertLessEqual(len(str(caught.exception)), 300)

    def test_non_json_error_body_is_ignored(self) -> None:
        with self.assertRaises(GitHubError) as caught:
            client(FakeTransport(Response(422, {}, b"private server detail"))).get_default_branch()
        self.assertEqual(str(caught.exception), "GitHub API returned HTTP 422")

    def test_graphql_error_includes_and_redacts_first_message(self) -> None:
        response = json_response(
            200,
            {"errors": [{"message": f"Cannot update {FAKE_CREDENTIAL}"}, {"message": "ignored"}]},
        )
        with self.assertRaises(GitHubError) as caught:
            client(FakeTransport(response)).enable_auto_merge("node-1", "merge")
        self.assertIn("Cannot update", str(caught.exception))
        self.assertNotIn(FAKE_CREDENTIAL, str(caught.exception))

    def test_find_open_issue_by_marker_requires_exact_own_line_match(self) -> None:
        marker = "<!-- repoaccord:branch=feat/report -->"
        transport = FakeTransport(
            json_response(
                200,
                [
                    {
                        "number": 1,
                        "html_url": "https://github.test/issues/1",
                        "body": "before\n" + marker + "\nafter",
                        "pull_request": {"url": "https://api.github.test/pulls/1"},
                    },
                    {
                        "number": 2,
                        "html_url": "https://github.test/issues/2",
                        "body": "before\n " + marker + "\nafter",
                    },
                    {
                        "number": 3,
                        "html_url": "https://github.test/issues/3",
                        "body": "before\n" + marker + "\nafter",
                    },
                ],
            ),
        )
        api = client(transport)
        self.assertEqual(
            api.find_open_issue_by_marker(marker),
            IssueRef(3, "https://github.test/issues/3"),
        )
        self.assertIn("/issues?state=open&per_page=100", transport.requests[0][1])

    def test_find_open_issue_by_marker_returns_none_when_no_exact_match(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                [{"number": 2, "html_url": "https://github.test/issues/2", "body": "other"}],
            ),
        )
        self.assertIsNone(client(transport).find_open_issue_by_marker("exact marker"))

    def test_urllib_transport_rejects_non_https_without_network(self) -> None:
        with self.assertRaisesRegex(GitHubError, "HTTPS"):
            UrllibTransport().request("GET", "http://127.0.0.1", {}, None, 1)

    def test_redirect_handler_rejects_other_hosts(self) -> None:
        handler = _SameHostRedirectHandler()
        request = Request("https://api.github.com/path")
        self.assertIsNone(
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/path")
        )
        self.assertIsNone(
            handler.redirect_request(request, None, 302, "Found", {}, "http://api.github.com/path")
        )

    def test_redirect_handler_accepts_same_https_host(self) -> None:
        handler = _SameHostRedirectHandler()
        request = Request("https://api.github.com/path")
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://api.github.com/other",
        )
        self.assertIsNotNone(redirected)
        self.assertEqual(redirected.full_url, "https://api.github.com/other")

    def test_get_403_without_wait_header_is_not_retried(self) -> None:
        transport = FakeTransport(
            json_response(403, {}), json_response(200, {"default_branch": "main"})
        )
        with self.assertRaisesRegex(GitHubError, "HTTP 403"):
            client(transport).get_default_branch()
        self.assertEqual(len(transport.requests), 1)

    def test_get_429_with_unparseable_wait_is_not_retried(self) -> None:
        transport = FakeTransport(json_response(429, {}, {"Retry-After": "later"}))
        with self.assertRaisesRegex(GitHubError, "HTTP 429"):
            client(transport).get_default_branch()
        self.assertEqual(len(transport.requests), 1)

    def test_retry_after_thirty_seconds_is_allowed(self) -> None:
        waits: list[float] = []
        transport = FakeTransport(
            json_response(429, {}, {"Retry-After": "30"}),
            json_response(200, {"default_branch": "main"}),
        )
        self.assertEqual(client(transport, waits).get_default_branch(), "main")
        self.assertEqual(waits, [30.0])

    def test_retry_after_negative_wait_is_not_retried(self) -> None:
        transport = FakeTransport(json_response(403, {}, {"Retry-After": "-1"}))
        with self.assertRaises(GitHubError):
            client(transport).get_default_branch()
        self.assertEqual(len(transport.requests), 1)

    def test_get_bad_branch_response_is_short_error(self) -> None:
        transport = FakeTransport(json_response(200, {"default_branch": ""}))
        with self.assertRaisesRegex(GitHubError, "no default branch"):
            client(transport).get_default_branch()

    def test_permission_missing_or_invalid_field_is_none(self) -> None:
        for value in (None, 42, {}):
            with self.subTest(value=value):
                transport = FakeTransport(json_response(200, {"permission": value}))
                self.assertIsNone(client(transport).get_actor_permission("alice"))

    def test_directory_contents_wrong_shape_is_error(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "main"}),
            json_response(200, {"name": "not-a-list"}),
        )
        with self.assertRaisesRegex(GitHubError, "not a directory listing"):
            client(transport).list_paths()

    def test_contents_requests_are_limited_to_four_directories(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "main"}),
            json_response(404, {}),
            json_response(404, {}),
            json_response(404, {}),
            json_response(404, {}),
        )
        self.assertEqual(client(transport).list_paths(), ())
        requested = [request[1].split("?", 1)[0] for request in transport.requests]
        self.assertEqual(
            requested[1:],
            [
                "https://api.github.com/repos/owner/repo/contents",
                "https://api.github.com/repos/owner/repo/contents/.github",
                "https://api.github.com/repos/owner/repo/contents/.github/ISSUE_TEMPLATE",
                "https://api.github.com/repos/owner/repo/contents/docs",
            ],
        )

    def test_default_branch_is_cached_between_content_calls(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "main"}),
            json_response(404, {}),
            json_response(404, {}),
            json_response(404, {}),
            json_response(404, {}),
        )
        client(transport).list_paths()
        self.assertEqual(
            sum(request[1].endswith("/repos/owner/repo") for request in transport.requests), 1
        )

    def test_read_file_rejects_invalid_utf8(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "main"}),
            Response(200, {}, b"\xff"),
        )
        with self.assertRaisesRegex(GitHubError, "not UTF-8"):
            client(transport).read_file("README.md")

    def test_non_success_file_read_includes_api_message(self) -> None:
        transport = FakeTransport(
            json_response(200, {"default_branch": "main"}),
            json_response(403, {"message": "sensitive server detail"}),
        )
        with self.assertRaises(GitHubError) as caught:
            client(transport).read_file("README.md")
        self.assertEqual(
            str(caught.exception),
            "GitHub API returned HTTP 403; sensitive server detail",
        )

    def test_rules_short_circuit_only_for_nonempty_required_checks(self) -> None:
        cases = (
            (
                [{"type": "required_status_checks", "parameters": {"required_status_checks": []}}],
                False,
            ),
            (
                [
                    {
                        "type": "required_status_checks",
                        "parameters": {"required_status_checks": [{}]},
                    }
                ],
                True,
            ),
            ([{"type": "other", "parameters": {"required_status_checks": [{}]}}], False),
        )
        for rules, expected in cases:
            with self.subTest(rules=rules):
                responses = [json_response(200, rules)]
                if not expected:
                    responses.append(json_response(404, {}))
                transport = FakeTransport(*responses)
                self.assertIs(client(transport).required_checks_proven("main"), expected)

    def test_legacy_empty_success_is_false_and_error_is_unknown(self) -> None:
        for response, expected in (
            (json_response(200, {"contexts": [], "checks": []}), False),
            (json_response(403, {}), None),
            (json_response(500, {}), None),
        ):
            with self.subTest(expected=expected):
                transport = FakeTransport(json_response(404, {}), response)
                self.assertIs(client(transport).required_checks_proven("main"), expected)

    def test_pull_request_response_shape_is_checked(self) -> None:
        for value in ({}, {"number": "1", "html_url": "url", "node_id": "id"}):
            with self.subTest(value=value), self.assertRaises(GitHubError):
                client(FakeTransport(json_response(201, value))).create_pull_request(
                    "title", "body", "head", "base"
                )

    def test_issue_response_shape_is_checked(self) -> None:
        with self.assertRaisesRegex(GitHubError, "missing its reference"):
            client(FakeTransport(json_response(201, {}))).create_issue("title", "body", ())

    def test_auto_merge_rejects_unknown_method_without_request(self) -> None:
        transport = FakeTransport()
        with self.assertRaisesRegex(ValueError, "method must be"):
            client(transport).enable_auto_merge("node", "admin")
        self.assertEqual(transport.requests, [])

    def test_graphql_post_is_not_retried(self) -> None:
        transport = FakeTransport(
            json_response(503, {}),
            json_response(200, {"data": {}}),
        )
        with self.assertRaises(GitHubError):
            client(transport).enable_auto_merge("node", "merge")
        self.assertEqual(len(transport.requests), 1)

    def test_write_urls_never_contain_credential(self) -> None:
        transport = FakeTransport(
            json_response(201, {"number": 1, "html_url": "https://g.test/i/1"})
        )
        client(transport).create_issue("title", "body", ())
        self.assertNotIn(FAKE_CREDENTIAL, transport.requests[0][1])

    def test_write_url_with_credential_from_response_is_rejected(self) -> None:
        response_url = f"https://github.test/{FAKE_CREDENTIAL}"
        transport = FakeTransport(json_response(201, {"number": 1, "html_url": response_url}))
        with self.assertRaisesRegex(GitHubError, "forbidden credential"):
            client(transport).create_issue("title", "body", ())

    def test_api_class_has_no_forbidden_write_methods(self) -> None:
        forbidden = {"merge", "approve", "push", "delete", "force"}
        methods = {name for name in GitHubClient.__dict__ if callable(getattr(GitHubClient, name))}
        self.assertFalse(methods & forbidden)

    def test_read_and_write_methods_use_only_expected_verbs(self) -> None:
        transport = FakeTransport(
            json_response(201, {"number": 1, "html_url": "https://g.test/i/1"}),
            json_response(201, {"number": 2, "html_url": "https://g.test/p/2", "node_id": "id"}),
            json_response(200, {"data": {}}),
        )
        api = client(transport)
        api.create_issue("a", "b", ())
        api.create_pull_request("a", "b", "head", "base")
        api.enable_auto_merge("id", "merge")
        self.assertEqual([request[0] for request in transport.requests], ["POST", "POST", "POST"])
