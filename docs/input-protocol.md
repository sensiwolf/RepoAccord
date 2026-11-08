# Input protocol

The tip commit message carries the request:

- The subject line becomes the pull request title.
- An optional `=== ISSUE ===` block creates an issue. Its first line is the issue title and its
  remaining lines are the issue body. Without this block, no issue is created.
- The required `=== PR ===` block contains the pull request body, following the repository's pull
  request template.

RepoAccord validates the pull request body against the headings in the default branch's Markdown
pull request template. Missing required headings stop the run with a clear message. Phase 1
supports Markdown templates only. Forbidden commit trailers are rejected.

Example:

```text
Add a report export

=== ISSUE ===
Export investigation reports
Let contributors download a report.

=== PR ===
## Summary
Adds a report export.

## Testing
Not run.
```
