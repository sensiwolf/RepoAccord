# Changelog

## Unreleased

- Reuse open issues by exact branch marker after partial failures and improve safe API error details.
- Load branch-prefix policy before deciding whether a non-default branch is allowed.
- Correct the action's GitHub API URL handling and compare repository names case-insensitively.
- Add a GitHub client, push-event runner, and fake-transport coverage.
- Tighten message marker/title parsing and branch-prefix validation.
- Implement the offline configuration, message, template, policy, and plan CLI core.
- Add the pre-alpha project skeleton, documentation, examples, and CI.
