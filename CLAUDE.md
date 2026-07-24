# CLAUDE.md

@AGENTS.md

## Claude Code

This repo is developed with **Claude Code**. `AGENTS.md` (imported above) is the shared,
tool-agnostic source of truth for stack, repo map, commands, and conventions — edit *that* file
for anything that isn't specific to Claude Code, so other tools reading `AGENTS.md` never drift
out of sync with this one again. This file should only ever hold the Claude-Code-only content
below.

- Windows host: the primary shell is PowerShell; a Bash tool is also available.
- Scaffolding slash commands: `/new-agent` (new agent + route + DTO + rate limit), `/frontend`,
  `/db`, `/run`.

### Planning workflow

For any non-trivial task, write a plan first to `.claude/plans/<task-name>.md` as GitHub-style
checkboxes (`- [ ] step`), with a complexity marker (✅ Simple / ⚠️ Medium / 🔴 Complex) and at
least one validation step per item. Update `- [ ]` → `- [x]` as you complete each step. The
`/plan` slash command does this.
