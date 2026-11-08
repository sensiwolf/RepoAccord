# Architecture

## Purpose and form

RepoAccord automates repetitive Git and GitHub housekeeping while following each repository's
rules. It is planned as a GitHub composite action calling a small Python package. It will not be
published to PyPI.

## Implemented flow

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
- `github_api.py`: call GitHub through an injectable transport.
- `run.py`: map push events to facts, plans, output, and approved API operations.

Planned, not implemented:

- additional execution and recovery behavior beyond the current supported operations.

Policy is pure logic; network access belongs only in `github_api.py`. The client and runner are
covered using a fake transport and do not require live GitHub access in tests.

## Phase 1 non-goals

No pushing commits, review approvals, editing existing pull requests, forks, YAML issue forms, or
hosted service.

## Future phases

Later phases may add code checks, merge-conflict handling, and an optional hosted GitHub App.
