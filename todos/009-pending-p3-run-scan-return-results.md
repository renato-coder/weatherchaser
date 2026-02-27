---
status: pending
priority: p3
issue_id: "009"
tags: [code-review, architecture]
dependencies: []
---

# Make run_scan return results instead of rendering directly

## Problem Statement

`main.py:run_scan()` calls `render_console()` directly, coupling the scan logic to console output. When Slack output (Step 6) or CSV export (Step 9) are added, the function can't be reused without duplicating the scan pipeline.

## Findings

- **File:** `main.py:34-60`, `run_scan` function
- **Evidence:** Calls `render_console(results)` directly instead of returning results

## Proposed Solutions

### Solution A: Return results, render in main()
- `run_scan()` returns `(results, data_available)` tuple
- `main()` handles rendering based on output flags (--slack, --csv, etc.)
- **Pros:** Clean separation, enables multi-output in future steps
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] `run_scan` returns results instead of rendering
- [ ] `main()` orchestrates output selection
- [ ] Console output unchanged from user perspective

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Separate data production from presentation |
