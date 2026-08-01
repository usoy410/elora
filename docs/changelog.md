# Changelog

All notable changes to the Elora project will be documented in this file.

## [Unreleased]

### Added
- **Persistent Workspace Memory:** Elora now strictly saves all generated projects to `~/Documents/EloraWorkspace/Projects/` and Classroom drafts to `~/Documents/EloraWorkspace/Classroom/`.
- **Memory Tagging:** Elora automatically generates `.elora_meta.md` tags for projects, allowing her to recall past work instantly via local filesystem searches (`ls -lt`).
- **Auto-Git Versioning:** Elora's brain is now hardcoded to automatically `git init` and commit changes when generating or modifying workspace projects.
- **Smart Voice Truncation:** The `voice.py` TTS engine now actively filters out Markdown code blocks and URLs to prevent jarring auditory experiences when Elora reads technical drafts.
- **Multi-Profile Google Workspace Integration:** Completely removed legacy IMAP logic in favor of the `gws` CLI tool. Elora can now seamlessly switch between `gws-personal` and `gws-work` profiles.

### Changed
- Refactored `elora/core/brain.py` and `elora/core/agent.py` to route all Google ecosystem queries (Drive, Gmail, Calendar) exclusively through the new `workspace_query` tool.
- Formatted and optimized the entire codebase using `ruff`, auto-fixing 280 linting/typing issues and removing dead imports.

### Removed
- `elora/skills/email.py` and old IMAP polling modules have been entirely deleted to reduce tech debt.
