"""Small GitHub REST and GraphQL client with an injectable transport."""

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from repoaccord import __version__

MAX_RESPONSE_SIZE = 5 * 1024 * 1024
REQUEST_TIMEOUT = 20
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_AUTO_MERGE_METHODS = {"merge": "MERGE", "squash": "SQUASH", "rebase": "REBASE"}


def _redact(value: str, secret: str) -> str:
    if not secret:
        return value
    for candidate in {secret, quote(secret, safe="")}:
        value = value.replace(candidate, "[redacted]")
    return value


@dataclass(frozen=True)
class Response:
    """HTTP response returned by a transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    """HTTP transport interface used by the GitHub client."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: int,
    ) -> Response: ...


class GitHubError(Exception):
    """Short API or transport error that never exposes request headers."""

    def __init__(self, status: int | None, message: str, secret: str = "") -> None:
        self.status = status
        self.message = _redact(message, secret)
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"GitHubError(status={self.status!r}, message={self.message!r})"


@dataclass(frozen=True)
class IssueRef:
    """Created issue reference."""

    number: int
    url: str


@dataclass(frozen=True)
class PullRequestRef:
    """Created pull request reference."""

    number: int
    url: str
    node_id: str


class _SameHostRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        original = urlsplit(request.full_url)
        destination = urlsplit(new_url)
        if (
            destination.scheme != "https"
            or destination.hostname != original.hostname
            or destination.port != original.port
        ):
            return None
        return super().redirect_request(request, file, code, message, headers, new_url)


class UrllibTransport:
    """HTTPS-only stdlib transport with bounded response reads."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: int,
    ) -> Response:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise GitHubError(None, "only HTTPS requests are allowed")
        # URL schemes and cross-host redirects are checked before credentials are sent.
        request = Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
        opener = build_opener(_SameHostRedirectHandler())
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            return self._response(exc)
        except (URLError, TimeoutError, OSError) as exc:
            raise GitHubError(None, "GitHub request failed") from exc
        with response:
            result = self._response(response)
        if len(result.body) > MAX_RESPONSE_SIZE:
            raise GitHubError(result.status, "GitHub response exceeds 5 MiB")
        return result

    @staticmethod
    def _response(response: Any) -> Response:
        body = response.read(MAX_RESPONSE_SIZE + 1)
        if len(body) > MAX_RESPONSE_SIZE:
            raise GitHubError(response.status, "GitHub response exceeds 5 MiB")
        return Response(
            status=response.status,
            headers={str(key): str(value) for key, value in response.headers.items()},
            body=body,
        )


class GitHubClient:
    """GitHub API client; all HTTP behavior is delegated to a transport."""

    def __init__(
        self,
        transport: Transport,
        token: str,
        repository: str,
        api_url: str = "https://api.github.com",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be owner/name")
        if not api_url.startswith("https://"):
            raise ValueError("api_url must start with https://")
        if token and any(
            token in value or quote(token, safe="") in value for value in (api_url, repository)
        ):
            raise ValueError("api_url must not contain the token")
        self._transport = transport
        self._token = token
        self.repository = repository
        self.api_url = api_url.rstrip("/")
        self._sleep = sleep
        self._default_branch: str | None = None

    def __repr__(self) -> str:
        repository = _redact(self.repository, self._token)
        api_url = _redact(self.api_url, self._token)
        return f"GitHubClient(repository={repository!r}, api_url={api_url!r}, token='[redacted]')"

    __str__ = __repr__

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"repoaccord/{__version__}",
        }

    @staticmethod
    def _path(*segments: str) -> str:
        return "/".join(quote(segment, safe="") for segment in segments)

    def _url(self, *segments: str, query: Mapping[str, str] | None = None) -> str:
        path = self._path("repos", *self.repository.split("/"), *segments)
        url = f"{self.api_url}/{path}"
        if query:
            url += "?" + urlencode(query)
        return url

    def _retry_after(self, headers: Mapping[str, str]) -> float | None:
        values = {key.casefold(): value for key, value in headers.items()}
        retry = values.get("retry-after")
        if retry is not None:
            try:
                wait = float(retry)
            except ValueError:
                try:
                    date = datetime.strptime(retry, "%a, %d %b %Y %H:%M:%S GMT")
                    wait = (date.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
                except ValueError:
                    return None
            return wait if 0 <= wait <= 30 else None
        reset = values.get("x-ratelimit-reset")
        if reset is not None:
            try:
                wait = float(reset) - time.time()
            except ValueError:
                return None
            return wait if 0 <= wait <= 30 else None
        return None

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        accept: str | None = None,
    ) -> Response:
        if self._token and (self._token in url or quote(self._token, safe="") in url):
            raise GitHubError(None, "request URL contains a forbidden credential")
        headers = self._headers
        if accept:
            headers["Accept"] = accept
        retries = 0
        while True:
            try:
                response = self._transport.request(
                    method,
                    url,
                    headers,
                    body,
                    REQUEST_TIMEOUT,
                )
            except GitHubError as exc:
                sanitized = _redact(str(exc), self._token)
                if sanitized != str(exc):
                    raise GitHubError(exc.status, sanitized) from None
                raise
            except Exception:
                raise GitHubError(None, "GitHub request failed", self._token) from None
            retry_wait = None
            if method == "GET" and retries < 2:
                if response.status in {502, 503, 504}:
                    retry_wait = 0
                elif response.status in {403, 429}:
                    retry_wait = self._retry_after(response.headers)
            if retry_wait is None:
                return response
            retries += 1
            if retry_wait:
                self._sleep(retry_wait)

    def _json(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        body = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        response = self._request(method, url, body=body)
        if not 200 <= response.status < 300:
            raise self._response_error(response)
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubError(
                response.status,
                "GitHub API returned invalid JSON",
                self._token,
            ) from exc

    def _response_error(self, response: Response) -> GitHubError:
        details = [f"GitHub API returned HTTP {response.status}"]
        try:
            data = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            message = data.get("message")
            if isinstance(message, str) and message.strip():
                details.append(message)
            errors = data.get("errors")
            if isinstance(errors, list):
                for error in errors[:3]:
                    if not isinstance(error, dict):
                        continue
                    detail = error.get("message")
                    if not isinstance(detail, str) or not detail.strip():
                        detail = error.get("code")
                    field = error.get("field")
                    if isinstance(field, list):
                        field = ", ".join(item for item in field if isinstance(item, str))
                    if not isinstance(detail, str) or not detail.strip():
                        continue
                    details.append(
                        f"{field}: {detail}" if isinstance(field, str) and field else detail
                    )
        compact = " ".join("; ".join(details).split())[:300]
        return GitHubError(response.status, compact, self._token)

    def _default(self) -> str:
        if self._default_branch is None:
            self.get_default_branch()
        if self._default_branch is None:
            raise GitHubError(None, "default branch is unavailable", self._token)
        return self._default_branch

    def get_default_branch(self) -> str:
        """Return and cache the repository's default branch."""
        data = self._json("GET", self._url())
        branch = data.get("default_branch") if isinstance(data, dict) else None
        if not isinstance(branch, str) or not branch:
            raise GitHubError(200, "repository response has no default branch", self._token)
        self._default_branch = branch
        return branch

    def get_actor_permission(self, login: str) -> str | None:
        """Return the actor permission, or None when GitHub cannot verify it."""
        url = self._url("collaborators", login, "permission")
        try:
            data = self._json("GET", url)
        except GitHubError as exc:
            if exc.status in {403, 404}:
                return None
            raise
        permission = data.get("permission") if isinstance(data, dict) else None
        return permission if isinstance(permission, str) else None

    def _contents(self, path: str) -> list[dict[str, Any]] | None:
        segments = ("contents",) if not path else ("contents", *path.split("/"))
        url = self._url(*segments, query={"ref": self._default()})
        try:
            data = self._json("GET", url)
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(data, list):
            raise GitHubError(200, "contents response is not a directory listing", self._token)
        return [item for item in data if isinstance(item, dict)]

    def list_paths(self) -> tuple[str, ...]:
        """List files and directory entries in the selected repository locations."""
        results: set[str] = set()
        for directory in ("", ".github", ".github/ISSUE_TEMPLATE", "docs"):
            entries = self._contents(directory)
            if entries is None:
                continue
            for entry in entries:
                name = entry.get("name")
                if isinstance(name, str):
                    results.add(f"{directory}/{name}" if directory else name)
        return tuple(sorted(results))

    def read_file(self, path: str) -> str | None:
        """Read a file from the default branch, never from a pushed branch."""
        url = self._url(
            "contents",
            *path.split("/"),
            query={"ref": self._default()},
        )
        response = self._request(
            "GET",
            url,
            accept="application/vnd.github.raw+json",
        )
        if response.status == 404:
            return None
        if not 200 <= response.status < 300:
            raise self._response_error(response)
        try:
            return response.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubError(response.status, "repository file is not UTF-8", self._token) from exc

    def has_pull_request_for_branch(self, branch: str) -> bool:
        """Check for any pull request for a branch, including closed requests."""
        owner = self.repository.split("/", 1)[0]
        data = self._json(
            "GET",
            self._url(
                "pulls",
                query={"head": f"{owner}:{branch}", "state": "all"},
            ),
        )
        if not isinstance(data, list):
            raise GitHubError(200, "pull request response is not a list", self._token)
        return bool(data)

    def find_open_issue_by_marker(self, marker: str) -> IssueRef | None:
        """Find the first open issue containing the exact marker on its own line."""
        data = self._json(
            "GET",
            self._url("issues", query={"state": "open", "per_page": "100"}),
        )
        if not isinstance(data, list):
            raise GitHubError(200, "issues response is not a list", self._token)
        for item in data:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            body = item.get("body")
            number = item.get("number")
            url = item.get("html_url")
            if (
                isinstance(body, str)
                and marker in body.splitlines()
                and type(number) is int
                and isinstance(url, str)
            ):
                if _redact(url, self._token) != url:
                    raise GitHubError(200, "issue URL contains a forbidden credential", self._token)
                return IssueRef(number=number, url=url)
        return None

    @staticmethod
    def _has_required_checks(data: Any) -> bool:
        if not isinstance(data, list):
            return False
        for rule in data:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters")
            if not isinstance(parameters, dict):
                continue
            checks = parameters.get("required_status_checks", parameters.get("checks"))
            if isinstance(checks, list) and checks:
                return True
        return False

    def required_checks_proven(self, branch: str) -> bool | None:
        """Prove required checks from branch rules or legacy protection."""
        try:
            rules = self._json(
                "GET",
                self._url("rules", "branches", branch),
            )
            if self._has_required_checks(rules):
                return True
        except GitHubError as exc:
            if exc.status == 404:
                pass

        try:
            protection = self._json(
                "GET",
                self._url("branches", branch, "protection", "required_status_checks"),
            )
        except GitHubError as exc:
            if exc.status == 404:
                return False
            return None
        if not isinstance(protection, dict):
            return None
        contexts = protection.get("contexts")
        checks = protection.get("checks")
        if (isinstance(contexts, list) and contexts) or (isinstance(checks, list) and checks):
            return True
        return False

    def create_issue(
        self,
        title: str,
        body: str,
        labels: tuple[str, ...],
        assignees: tuple[str, ...] = (),
    ) -> IssueRef:
        """Create an issue without retrying its POST request."""
        payload: dict[str, Any] = {"title": title, "body": body, "labels": list(labels)}
        if assignees:
            payload["assignees"] = list(assignees)
        data = self._json("POST", self._url("issues"), payload)
        if not isinstance(data, dict):
            raise GitHubError(200, "issue response is invalid", self._token)
        number, url = data.get("number"), data.get("html_url")
        if not isinstance(number, int) or not isinstance(url, str):
            raise GitHubError(200, "issue response is missing its reference", self._token)
        if _redact(url, self._token) != url:
            raise GitHubError(200, "issue URL contains a forbidden credential", self._token)
        return IssueRef(number=number, url=url)

    def request_reviewers(self, pull_number: int, reviewers: tuple[str, ...]) -> None:
        """Request reviewers for a pull request without retrying its POST request."""
        self._json(
            "POST",
            self._url("pulls", str(pull_number), "requested_reviewers"),
            {"reviewers": list(reviewers)},
        )

    def add_assignees(self, number: int, assignees: tuple[str, ...]) -> None:
        """Add issue or pull request assignees without retrying its POST request."""
        self._json(
            "POST",
            self._url("issues", str(number), "assignees"),
            {"assignees": list(assignees)},
        )

    def add_labels(self, number: int, labels: tuple[str, ...]) -> None:
        """Add issue or pull request labels without retrying its POST request."""
        self._json(
            "POST",
            self._url("issues", str(number), "labels"),
            {"labels": list(labels)},
        )

    def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestRef:
        """Create a pull request without retrying its POST request."""
        data = self._json(
            "POST",
            self._url("pulls"),
            {"title": title, "body": body, "head": head, "base": base},
        )
        if not isinstance(data, dict):
            raise GitHubError(200, "pull request response is invalid", self._token)
        number, url, node_id = data.get("number"), data.get("html_url"), data.get("node_id")
        if not isinstance(number, int) or not isinstance(url, str) or not isinstance(node_id, str):
            raise GitHubError(200, "pull request response is missing its reference", self._token)
        if _redact(url, self._token) != url:
            raise GitHubError(200, "pull request URL contains a forbidden credential", self._token)
        return PullRequestRef(number=number, url=url, node_id=node_id)

    def enable_auto_merge(self, node_id: str, method: str) -> None:
        """Enable pull request auto-merge using a GraphQL variables payload."""
        try:
            merge_method = _AUTO_MERGE_METHODS[method]
        except KeyError as exc:
            raise ValueError("method must be merge, squash, or rebase") from exc
        query = (
            "mutation EnablePullRequestAutoMerge($id: ID!, $method: PullRequestMergeMethod!) "
            "{ enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: $method}) "
            "{ pullRequest { id } } }"
        )
        data = self._json(
            "POST",
            f"{self.api_url}/graphql",
            {"query": query, "variables": {"id": node_id, "method": merge_method}},
        )
        if isinstance(data, dict):
            errors = data.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0]
                message = first.get("message") if isinstance(first, dict) else None
                detail = (
                    "GitHub GraphQL request returned errors: " + message
                    if isinstance(message, str) and message.strip()
                    else "GitHub GraphQL request returned errors"
                )
                compact = " ".join(detail.split())[:300]
                raise GitHubError(200, compact, self._token)
