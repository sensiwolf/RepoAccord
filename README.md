# RepoAccord

Repository automation, aligned with your rules.

## What it is

RepoAccord is an autonomous repository contributor.
It handles repetitive Git and GitHub housekeeping.
It follows each repository's own rules.

**Status: pre-alpha, not usable yet**

## Planned for the first release

- React to a push on an allowed branch.
- Read the repository's issue and pull request templates.
- Create an issue when asked.
- Open a pull request in the repository's format with `Closes #N`.
- Enable auto-merge only when required checks are proven.

## Principles

- Follows your repository's rules.
- Fails safe.
- Never bypasses checks or branch protection.
- Adds no unwanted commit trailers.
- Makes no hardcoded repository assumptions.

## Documentation

- [Architecture](docs/architecture.md)
- [Security model](docs/security-model.md)
- [Configuration](docs/configuration.md)
- [Input protocol](docs/input-protocol.md)
- [Contributing](CONTRIBUTING.md)
- [Security reporting](SECURITY.md)
