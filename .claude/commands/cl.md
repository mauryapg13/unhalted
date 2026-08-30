---
description: Audit changelog entries before release
---
Audit changelog entries for all commits since the last release.

## Process

1. **Find the last release tag:**
   ```bash
   git tag --sort=-version:refname | head -1
   ```
   If the repository has no tags yet, audit all commits on `main`.

2. **List all commits since that tag:**
   ```bash
   git log <tag>..HEAD --oneline
   ```

3. **Read the `## [Unreleased]` section of `CHANGELOG.md`.**

4. **For each commit, check:**
   - Skip: changelog updates, doc-only changes, release housekeeping
   - Determine what the commit affects (use `git show <hash> --stat`)
   - Verify a changelog entry exists for it
   - For external contributions (PRs), verify format: `Description ([#N](url) by [@user](url))`

5. **Add New Features section after changelog fixes:**
   - Insert a `### New Features` section at the start of `## [Unreleased]` in `CHANGELOG.md`.
   - Propose the top new features to the user for confirmation before writing them.
   - Link to relevant docs and sections whenever possible.

6. **Report:**
   - List commits with missing entries
   - Add any missing entries directly

## Changelog Format Reference

Sections (in order):
- `### Breaking Changes` - API changes requiring migration
- `### Added` - New features
- `### Changed` - Changes to existing functionality
- `### Fixed` - Bug fixes
- `### Removed` - Removed features

Attribution (use this repository's own issue and PR URLs, resolved via `gh repo view --json nameWithOwner`):
- Internal: `Fixed foo ([#123](https://github.com/<owner>/<repo>/issues/123))`
- External: `Added bar ([#456](https://github.com/<owner>/<repo>/pull/456) by [@user](https://github.com/user))`
