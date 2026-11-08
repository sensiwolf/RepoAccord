# Security model

The offline parsing, validation, and planning core, GitHub client, and push-event runner are
implemented. Client and runner behavior is tested with a fake transport. The issue/PR,
GitHub-App-identity, and required-check auto-merge flows have been exercised end to end.

## Credentials and workflow trust

The default `GITHUB_TOKEN` does not trigger workflows on pull requests it opens. Adopters must
supply their own credential: a fine-grained personal access token limited to one repository with
Issues read/write, Pull requests read/write, Contents read, and Metadata read, or their own GitHub
App. Never grant admin permission.

A push-triggered workflow runs the workflow file from the pushed commit. Anyone with write access
can change it and read the supplied credential. This is acceptable only when writers are trusted.
Limit the credential to one repository and rely on branch protection.

## Untrusted input and authorization

Branch names, commit messages, and titles are untrusted text. Never interpolate them into shell
scripts; pass values through environment variables or the API. Branches must match
`^[A-Za-z0-9._/-]{1,200}$`, contain no `..` or `//`, not start or end with `/` or `.`, and not end
with `.lock`.

Read configuration and templates from the default branch, never from the pushed branch. Reviewer,
assignee, and label values also come only from the default branch's trusted configuration file.
Require an allowed branch prefix and an actor check through an explicit allowlist or write
permission verified through the API. The runner checks the event repository against
`GITHUB_REPOSITORY` and authorizes only the event sender, never the pusher or commit author. Ignore
bots (including actors ending in `[bot]`), tags, deletes, and pushes to the default branch.

Forbidden trailer detection is case-insensitive and rejects a line that starts, after optional
whitespace, with a configured phrase; a phrase in the middle of a line is allowed. The default
forbids no phrases. A supported final trailer paragraph is separated from the last block's body
after the configured whole-message forbidden check.

## Pull requests and auto-merge

Auto-merge is off by default. Enable it only after proving that required status checks exist for the
base branch using the branch rules API, and use the configured merge method. Never use an admin
override, force push, or bypass. If required checks cannot be proven, skip the auto-merge action and
explain why.

Use an idempotency marker in issue and pull request bodies and allow one pull request per branch.
If a pull request already exists for that branch, do nothing. Before creating a requested issue,
the runner looks for an open issue whose body contains the exact branch marker on its own line and
reuses the first match after a partial failure.

Pull request metadata is best-effort: a refused reviewer, assignee, or label request is reported as
a note and does not prevent the remaining metadata operations or auto-merge. The closed write-action
set is `CreateIssue`, `CreatePullRequest`, `AddPullRequestMetadata`, and `EnableAutoMerge`.

## Fail-safe operation

Configuration uses a safe YAML loader and rejects duplicate keys, unknown keys, and invalid values.
The configuration, message, template, and policy modules do not access the network, environment,
filesystem, or subprocesses.

Missing permission or information means stop with a clear message. Pin actions by full commit SHA,
request minimal permissions, and never log credentials or the environment. The permissions below
were verified in the sandbox.

Workflow-command output escapes percent signs as `%25`, carriage returns as `%0D`, line feeds as
`%0A`, and neutralizes `::` at the start of a line. Refusal annotations use only the escaped
reason. Markdown summaries escape Markdown-significant characters. The runner never prints the
token or the environment.

The client retries eligible GET requests only. POSTs are never retried. Its closed set of write
operations is creating issues, creating pull requests, requesting reviewers, adding assignees and
labels, and enabling pull request auto-merge. It has no operation for merging, approvals, pushes,
branch deletion, force pushes, or settings changes. The token is sent only in the authorization
header. It is redacted from client representations and API errors and is never included in a URL,
exception, log, or printed output.

| Operation | Verified fine-grained token permission |
| --- | --- |
| Issue and pull request creation | Issues read/write, Pull requests read/write, Contents read, Metadata read |
| Request reviewers: `POST /repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers` | Pull requests read/write |
| Add assignees: `POST /repos/{owner}/{repo}/issues/{issue_number}/assignees` | Issues read/write |
| Add labels: `POST /repos/{owner}/{repo}/issues/{issue_number}/labels` | Issues read/write |
| Auto-merge | Additionally Contents read/write (the GraphQL call was rejected with Contents read and succeeded with Contents read/write; GitHub does not document this permission for the call) |

For non-success API responses, GitHub's message and up to three error details are included when
available. Error details are whitespace-collapsed, truncated to 300 characters, and token-redacted;
response and request headers are never included.

A token that can write contents is a larger grant. Keep it scoped to one repository and protect the
default branch.
