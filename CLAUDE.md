# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Contents

This workspace is a personal configuration and scripts directory, not a traditional software project. It contains:

- **Pine Script trading strategies** (`.txt` files with Pine Script V5 code for TradingView)
- **AI assistant setup scripts** (`package.sh.json` — a bash script for installing and configuring Claude Code with custom API endpoints)
- **OpenCode configuration** (`.opencode.json` — API key and model settings for OpenCode AI)

## Pine Script Files

Pine Script V5 files (`.txt`) are TradingView indicators/strategies. They use:
- `//@version=5` syntax
- Built-in functions from the `ta.*` namespace (e.g., `ta.kc`, `ta.crossover`, `ta.crossunder`)
- Plotting functions like `plot()`, `plotshape()`, `label.new()`
- Alert system via `alert()`

There is no local build, test, or lint process for Pine Script — code is pasted directly into TradingView's Pine Editor.

## Quantitative Trading

- **Self-positioning**: Act as a senior trader with deep market experience.
- **Market belief**: Trust the market and trust the trend.
- **Reversal awareness**: Never ignore the possibility of a reversal after extreme market moves.

## Setup Script

`package.sh.json` is a bash script (not a Node.js package file) that:
- Installs system dependencies (Node.js, jq, Python3) via Homebrew (macOS) or apt/yum/pacman (Linux)
- Installs Claude Code via npm (`npm install -g @anthropic-ai/claude-code`)
- Writes Claude Code settings to `~/.claude/settings.json` with custom `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL`

The script is also present at `node_modules/opencode-ai/package.sh.json`.

## Workflow Orchestration

### 1. Plan Node Default

- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately - don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy

- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One tack per subagent for focused execution

### 3. Self-Improvement Loop

- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done

- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)

- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes - don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing

- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests - then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

- **Plan First**: Write plan to `tasks/todo.md` with checkable items
- **Verify Plan**: Check in before starting implementation
- **Track Progress**: Mark items complete as you go
- **Explain Changes**: High-level summary at each step
- **Document Results**: Add review section to `tasks/todo.md`
- **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

## Important Notes

- No `package.json`, `pyproject.toml`, or build system is present
- There are no tests, linters, or CI configuration
- The `.opencode.json` and `package.sh.json` files contain API keys — do not commit or share them
