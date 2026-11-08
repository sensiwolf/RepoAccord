# Architecture

## Purpose and form

RepoAccord automates repetitive Git and GitHub housekeeping while following each repository's
rules. It is planned as a GitHub composite action calling a small Python package. It will not be
published to PyPI.

## Planned flow

Push event -> authorize -> read configuration and templates from the default branch -> parse the
tip commit message -> let policy decide a plan -> execute idempotently -> optionally enable
auto-merge.

## Module map

Implemented offline modules:

- `config.py`: load and strictly validate configuration.
- `message.py`: parse the tip commit message.
- `templates.py`: read and validate repository issue and pull request templates.
- `policy.py`: decide an action plan without network access.
- `errors.py`: typed, user-facing validation errors.
- `cli.py`: load JSON plan input and serialize the offline plan.

Planned, not implemented:

- `github_api.py`: contain GitHub API calls.
- `run.py`: coordinate GitHub access and execution.

Policy is pure logic; network access belongs only in `github_api.py`. The implemented core is
testable offline and does not execute its plans.

## Phase 1 non-goals

No pushing commits, review approvals, editing existing pull requests, forks, YAML issue forms, or
hosted service.

## Future phases

Later phases may add code checks, merge-conflict handling, and an optional hosted GitHub App.
