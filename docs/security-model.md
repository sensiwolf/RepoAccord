# Security model

The offline parsing, validation, and planning core, GitHub client, and push-event runner are
implemented. Client and runner behavior is tested with a fake transport; live GitHub permissions
and end-to-end operation have not been verified.

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

Read configuration and templates from the default branch, never from the pushed branch. Require
an allowed branch prefix and an actor check through an explicit allowlist or write permission
verified through the API. The runner checks the event repository against `GITHUB_REPOSITORY` and
authorizes only the event sender, never the pusher or commit author. Ignore bots (including actors
ending in `[bot]`), tags, deletes, and pushes to the default branch.

Forbidden trailer detection is case-insensitive and rejects a line that starts, after optional
whitespace, with a configured phrase; a phrase in the middle of a line is allowed.

## Pull requests and auto-merge

Auto-merge is off by default. Enable it only after proving that required status checks exist for
the base branch using the branch rules API, and use the configured merge method. Never use an
admin override, force push, or bypass. If required checks cannot be proven, refuse and explain why.

Use an idempotency marker in issue and pull request bodies and allow one pull request per branch.
If a pull request already exists for that branch, do nothing.

## Fail-safe operation

Configuration uses a safe YAML loader and rejects duplicate keys, unknown keys, and invalid
values. The configuration, message, template, and policy modules do not access the network,
environment, filesystem, or subprocesses.

Missing permission or information means stop with a clear message. Pin actions by full commit SHA,
request minimal permissions, and never log credentials or the environment. The client and runner
apply these controls, but endpoint permission details still require sandbox verification.

Workflow-command output escapes percent signs as `%25`, carriage returns as `%0D`, line feeds as
`%0A`, and neutralizes `::` at the start of a line. Refusal annotations use only the escaped
reason. Markdown summaries escape Markdown-significant characters. The runner never prints the
token or the environment.

The client retries eligible GET requests only. POSTs are never retried. Its closed set of write
operations is creating issues, creating pull requests, and enabling pull request auto-merge. It has
no operation for merging, approvals, pushes, branch deletion, force pushes, or settings changes.
The token is sent only in the authorization header. It is redacted from client representations and
API errors and is never included in a URL, exception, log, or printed output.

| API operation | Intended fine-grained token permission |
| --- | --- |
| Read repository metadata, paths, files, and default branch | Contents read; Metadata read |
| Read collaborator permissions and branch rules/protection | Contents read; Metadata read |
| List and create pull requests; enable pull request auto-merge | Pull requests read/write |
| Create issues | Issues read/write |

Exact permission requirements for branch rules, legacy protection, collaborator-permission, and
GraphQL calls have **not** been verified against real GitHub. They will be confirmed in a sandbox
test before release.
