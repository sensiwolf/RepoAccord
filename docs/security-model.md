# Security model

The security controls below describe the intended design. They are not implemented in the
pre-alpha skeleton.

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
scripts; pass values through environment variables or the API. Validate branch names against a
strict regular expression.

Read configuration and templates from the default branch, never from the pushed branch. Require
an allowed branch prefix and an actor check through an explicit allowlist or write permission
verified through the API. Never trust the commit author. Ignore bots, tags, deletes, and pushes to
the default branch.

## Pull requests and auto-merge

Auto-merge is off by default. Enable it only after proving that required status checks exist for
the base branch using the branch rules API, and use the configured merge method. Never use an
admin override, force push, or bypass. If required checks cannot be proven, refuse and explain why.

Use an idempotency marker in issue and pull request bodies and allow one pull request per branch.
If a pull request already exists for that branch, do nothing.

## Fail-safe operation

Missing permission or information means stop with a clear message. Pin actions by full commit SHA,
request minimal permissions, and never log credentials or the environment.

Implementation must verify exactly which token permissions the branch rules and collaborator
permission endpoints require. Those endpoint permission details are not yet verified.
