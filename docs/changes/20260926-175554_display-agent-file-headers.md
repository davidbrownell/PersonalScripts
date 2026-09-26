---
type: Change
title: Display agent file headers in FindAgentVersions
description: FindAgentVersions.py now reports the optional header name that precedes the version in AGENTS.md comments; the repo's CLAUDE.md was renamed to AGENTS.md.
resource: Scripts/FindAgentVersions.py
tags:
  - FindAgentVersions
  - AGENTS.md
generated: { by: claude-code/claude-opus-5-5, at: 2026-09-26T17:55:54-04:00 }
status: stable
---

# Summary

- `Scripts/FindAgentVersions.py` parses version comments of the form `<!-- <header> Version: <version> -->` and adds a `Header` column to its output table. Comments without a header (`<!-- Version: 0.6.0 -->`) still parse, with an empty header.
- `_DisplayTable` builds column widths generically from a header list and row values, replacing per-column width and format code.
- `CLAUDE.md` was renamed to `AGENTS.md` and updated to the `python_development` 0.8.0 template, which uses the header-prefixed version comment and adds a `uv` usage rule.

# Motivation

Agent instruction files are now generated from named templates (for example `python_development`), so the version number alone no longer identifies which template a file was derived from. Reporting the header distinguishes files built from different templates. Renaming the repo's own instructions to `AGENTS.md` uses the vendor-neutral filename and makes the file discoverable by `FindAgentVersions.py`.

# Compatibility

The previous regex used `search`, matching `version:` anywhere in a comment. The new regex uses `match` and captures the text before `version:` as the header, so any leading text in the comment is now reported as the header rather than ignored.
