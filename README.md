# RepoAccord

Repository automation, aligned with your rules.

## What it does

- Turns a structured commit message into an issue and a pull request that match the repository's own templates.
- Refuses input that breaks those templates or the repository's rules.
- Opens issues and pull requests under the identity of the token it receives; a GitHub App token shows the app as author.
- Optionally requests reviewers, assignees, and labels.
- Optionally enables auto-merge only after required checks are proven.

## What it does not do

- It does not write the issue or pull request text; the commit message supplies it.
- It does not review code.
- It does not merge anything itself; it can ask GitHub to auto-merge after required checks are proven.

**Status: early release, tested end to end in a sandbox repository and in one real repository.**

## Quick start

1. Create a fine-grained token limited to one repository with Issues read/write, Pull requests
   read/write, Contents read, and Metadata read. Save it as the `REPOACCORD_TOKEN` secret.
2. Copy [examples/caller-workflow.yml](examples/caller-workflow.yml) to
   `.github/workflows/repoaccord.yml`.
3. Push a branch whose last commit message follows [docs/input-protocol.md](docs/input-protocol.md).
   RepoAccord starts in dry-run; set repository variable `REPOACCORD_DRY_RUN` to `false` to go live.

## Bot identity (recommended)

A personal token makes issues and pull requests appear under its owner's account. Register a GitHub
App with Contents read, Issues read/write, and Pull requests read/write permissions, no webhook, and
install it on the repository to have them appear as the app. Each adopter registers their own app.

Use these two steps in the caller workflow instead of passing a personal token:

```yaml
steps:
  - id: app-token
    uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3.3.0
    with:
      client-id: ${{ vars.REPOACCORD_CLIENT_ID }}
      private-key: ${{ secrets.REPOACCORD_APP_PRIVATE_KEY }}
  - uses: sensiwolf/repoaccord@v0.2.0
    with:
      token: ${{ steps.app-token.outputs.token }}
      dry-run: ${{ vars.REPOACCORD_DRY_RUN || 'true' }}
```

## Optional: reviewers, assignees, and labels

Set `issues.assignees`, `pull_request.reviewers`, `pull_request.assignees`, or
`pull_request.labels` in `.github/repoaccord.yml`. All default to `[]` and are off unless configured.
See [configuration](docs/configuration.md).

## Optional: auto-merge

Set `auto_merge.enabled`, enable **Allow auto-merge**, and require at least one status check on the
default branch. Auto-merge requires Contents read/write; a GitHub App needs both read and write
Contents permission.

## Known limitations

- Markdown templates only; YAML issue forms are not supported.
- One pull request per branch; repeat pushes are skipped.
- If auto-merge fails after the pull request is created, it is not retried.
- GitHub's delete-head-branch setting may not delete a branch merged by auto-merge.
- Only pushes by authorized users on configured branch prefixes are handled.
- Issue reuse searches the 100 most recent open issues.
- GitHub can refuse reviewer requests, for example when the reviewer authored the pull request;
  these failures are reported as notes and do not stop other metadata requests.

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Input protocol](docs/input-protocol.md)
- [Security model](docs/security-model.md)
