# RepoAccord

Repository automation, aligned with your rules.

## What it does

RepoAccord is an autonomous repository contributor that handles repetitive Git and GitHub
housekeeping.
It reads the repository's rules and templates before planning an action.
It can create a requested issue and open a correctly formatted pull request.

**Status: early release (v0.1), tested end to end in a sandbox repository**

## Quick start

1. Create a fine-grained token limited to **one repository**, with Issues read/write, Pull requests
   read/write, Contents read, and Metadata read. Save it as the repository secret
   `REPOACCORD_TOKEN`; never commit it.
2. Copy [examples/caller-workflow.yml](examples/caller-workflow.yml) to
   `.github/workflows/repoaccord.yml`.
3. Push a branch whose last commit message follows [docs/input-protocol.md](docs/input-protocol.md).
   It starts in dry-run. Set the repository **variable** `REPOACCORD_DRY_RUN` to `false` to go live.

## Optional: auto-merge

Set `auto_merge.enabled` in `.github/repoaccord.yml`, turn on **Allow auto-merge** in repository
settings, and require at least one status check on the default branch with a branch ruleset or
branch protection. Add Contents read/write to the token; GitHub requires it for the auto-merge
call.

## Known limitations

- Markdown templates only; YAML issue forms are not supported.
- One pull request per branch; a repeat push is skipped.
- If auto-merge fails after the pull request is created, enable it on the pull request by hand; it
  is not retried.
- GitHub's setting to delete head branches may not delete a branch merged by auto-merge.
- Only pushes by users with write access to branches matching configured prefixes are handled.
- Issue reuse searches the 100 most recent open issues.

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Input protocol](docs/input-protocol.md)
- [Security model](docs/security-model.md)
