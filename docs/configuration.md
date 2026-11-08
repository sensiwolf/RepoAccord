# Configuration

**Draft until the first release.** This version 1 schema is validated strictly; unknown keys at
any level are errors.

Safe defaults are shown below.

```yaml
version: 1
branches:
  prefixes: [feat/, fix/, docs/, chore/] # Prefix starts with an ASCII letter or digit.
actors:
  allow: [] # Empty means only actors with write permission are allowed.
issues:
  label: null # No label.
  template: null # Optional Markdown template file name.
pull_request:
  placeholder: "#<issue number>" # Replaced with the newly created issue number.
trailers:
  forbid: [Co-authored-by, Generated with]
auto_merge:
  enabled: false
  method: merge # One of merge, squash, or rebase.
```

Issue template labels are used when `issues.label` is null. A configured issue label overrides
labels from the selected template.

The base branch is always the repository's default branch, read from the API; it is never
hardcoded.
