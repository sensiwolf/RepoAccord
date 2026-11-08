# Architecture

## Purpose and form

RepoAccord automates repetitive Git and GitHub housekeeping while following each repository's
rules. It is a GitHub composite action calling a small Python package. It is not published to PyPI.

## Implemented flow

Push event -> authorize -> read configuration and templates from the default branch -> parse the
tip commit message -> let policy decide a plan -> execute idempotently -> optionally request pull
request metadata -> optionally enable auto-merge.

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

The closed action set is `CreateIssue`, `CreatePullRequest`, `AddPullRequestMetadata`, and
`EnableAutoMerge`. When planned, execution order is issue creation, pull request creation, optional
pull request metadata, then optional auto-merge. Metadata is omitted when all metadata lists are
empty. Policy is pure logic; network access belongs only in `github_api.py`. Client and runner
behavior is tested offline with a fake transport.

## Phase 1 non-goals

No pushing commits, review approvals, editing existing pull requests, forks, YAML issue forms, or
hosted service.

## Future phases

Later phases may add code checks, merge-conflict handling, and an optional hosted GitHub App.
