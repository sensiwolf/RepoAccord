# Changelog

## 0.2.0

- Optionally request reviewers, assignees, and pull request labels; all are off by default.
- Treat metadata requests as best-effort and report individual failures as notes.
- Rewrite the README to explain capabilities, limits, and GitHub App identity.
- Document how adopters can use their own GitHub App as the bot identity.

## 0.1.0

- Add the offline configuration, message, template, policy, and plan core.
- Add a GitHub client, push-event runner, and composite action.
- Verify the issue, pull request, and required-check auto-merge flow in a sandbox repository.
- Stop forbidding commit message trailers by default and separate supported final trailer paragraphs
  from pull request and issue bodies.
