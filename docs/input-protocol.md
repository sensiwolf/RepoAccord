# Input protocol

The tip commit message is parsed as text, not evaluated. Its first line is the subject and becomes
the pull request title; the subject must contain 1..256 characters.

- The line `=== ISSUE ===` starts an optional issue block. Trailing whitespace after the marker is
  allowed. Its first non-blank line is stripped and becomes the 1..256 character issue title;
  remaining lines are the issue body, which may be empty.
- The line `=== PR ===` starts the required pull request block. Trailing whitespace after the
  marker is allowed. The stripped subject becomes the pull request title; it must contain
  1..256 characters. The PR body must be non-blank.
- Each marker may appear at most once, in either order. Text between the subject and first marker
  must be blank; all other text must belong to a block.
- Pull request and issue bodies may each contain at most 65,000 characters.

RepoAccord validates the pull request body against the headings in the default branch's Markdown
pull request template. Missing headings and empty required sections stop the run. Phase 1 supports
Markdown templates only. A line beginning, after optional whitespace, with a configured forbidden
trailer phrase (case-insensitive) is rejected; the phrase mid-line is allowed.

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
