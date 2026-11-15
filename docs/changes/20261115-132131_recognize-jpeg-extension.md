---
type: Change
title: Recognize .jpeg files in BackupOneDrive
description: BackupOneDrive.py now treats files with the .jpeg extension as pictures alongside .jpg, .heic, and .png.
resource: Scripts/BackupOneDrive.py
tags:
  - BackupOneDrive
  - pictures
generated: { by: claude-code/claude-opus-5-5, at: 2026-11-15T13:21:33-05:00 }
status: stable
---

# Summary

- `_GetFilesToProcess` in `Scripts/BackupOneDrive.py` adds `.jpeg` to the set of picture file types, so those files are organized into the pictures output directory.

# Motivation

Files with a `.jpeg` extension were not matched by any file processor, so each one was reported as "not a recognized file type" and skipped during backup. `.jpeg` is a common alternate spelling of `.jpg`.

# Compatibility

No breaking changes. `.jpeg` files that previously produced errors are now backed up.
