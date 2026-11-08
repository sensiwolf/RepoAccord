# Configuration

This version 1 schema is validated strictly; unknown keys at any level are errors.

Safe defaults are shown below. Optional metadata lists are empty, so no reviewers, assignees, or
pull request labels are requested unless a repository configures them.

```yaml
version: 1
branches:
  prefixes: [feat/, fix/, docs/, chore/] # Prefix starts with an ASCII letter or digit.
actors:
  allow: [] # Empty means only actors with write permission are allowed.
issues:
  label: null # No label.
  template: null # Optional Markdown template file name.
  assignees: []
pull_request:
  placeholder: "#<issue number>" # Replaced with the newly created issue number.
  reviewers: []
  assignees: []
  labels: []
trailers:
  forbid: [] # Default: do not forbid any trailer phrase.
auto_merge:
  enabled: false
  method: merge # One of merge, squash, or rebase.
```

For example, uncomment and adapt the optional metadata values below. GitHub logins must start with
an ASCII letter or digit and contain at most 39 ASCII letters, digits, or hyphens; duplicates are
removed case-insensitively while preserving order. Each assignee list allows at most 10 logins and
the reviewer list at most 15. Up to 20 labels are allowed; each is a non-empty name of at most 50
characters.

```yaml
# issues:
#   assignees: [release-manager]
# pull_request:
#   reviewers: [maintainer]
#   assignees: [release-manager]
#   labels: [needs-review, "release candidate"]
```

Issue template labels are used when `issues.label` is null. A configured issue label overrides
labels from the selected template. Reviewer requests and pull request metadata are best-effort.

The base branch is always the repository's default branch, read from the API; it is never
hardcoded.
