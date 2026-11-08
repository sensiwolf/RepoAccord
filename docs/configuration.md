# Configuration

**Draft until the first release.** This proposed version 1 schema is not implemented. Validation
will be strict: unknown keys are errors.

Safe defaults are shown below.

```yaml
version: 1
branches:
  prefixes: [feat/, fix/, docs/, chore/]
actors:
  allow: [] # Empty means only actors with write permission are allowed.
issues:
  label: null # No label.
pull_request:
  placeholder: "#<issue number>" # Replaced with the newly created issue number.
trailers:
  forbid: [Co-authored-by, Generated with]
auto_merge:
  enabled: false
  method: merge # One of merge, squash, or rebase.
```

The base branch is always the repository's default branch, read from the API; it is never
hardcoded.
