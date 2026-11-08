# Security model

The security controls below describe the intended design. The offline parsing, validation, and
planning core is implemented; GitHub access and execution are not.

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
verified through the API. Never trust the commit author. Ignore bots (including actors ending in
`[bot]`), tags, deletes, and pushes to the default branch.

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
values. The offline core only transforms supplied values: its configuration, message, template,
and policy modules do not access the network, environment, filesystem, or subprocesses.

Missing permission or information means stop with a clear message. Pin actions by full commit SHA,
request minimal permissions, and never log credentials or the environment. These core guarantees
do not implement GitHub authorization or execution; network and endpoint permission details remain
to be verified before an executor is added.

Implementation must verify exactly which token permissions the branch rules and collaborator
permission endpoints require. Those endpoint permission details are not yet verified.
