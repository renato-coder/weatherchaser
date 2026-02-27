---
status: pending
priority: p3
issue_id: "008"
tags: [code-review, cleanup, quality]
dependencies: []
---

# Remove unused imports and dead code

## Problem Statement

Several files have unused imports and unused code (YAGNI). This adds noise and potential confusion.

## Findings

- **`geo/matcher.py`**: `import time` only used in `__main__` block but imported at module level
- **`classifier.py`**: `from collections import defaultdict` only used in `__main__` block
- **`config.py`**: `REMI_MARKETS` dict is defined but never used (Step 8 not yet implemented)
- **`config.py`**: `RiskPolygon.stroke` and `RiskPolygon.fill` fields are stored but never read
- **`config.py`**: `CAT_THRESHOLDS["any_severe_prob_min"]` is defined but never checked in classifier
- **`output/console.py:_has_prob_risk`**: Lazy import of `CAT_THRESHOLDS` is unnecessary (already imported at module level in other functions)

## Proposed Solutions

### Solution A: Clean up all at once
- Move `import time` and `from collections import defaultdict` into their `__main__` blocks
- Remove `REMI_MARKETS` (add back when Step 8 is implemented)
- Remove `stroke`/`fill` from `RiskPolygon` (not used for any logic)
- Either use `any_severe_prob_min` in classifier or remove it
- Move `CAT_THRESHOLDS` import to module level in console.py
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] No unused imports at module level
- [ ] No defined-but-never-used constants or fields
- [ ] All `__main__` block imports are local to that block

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Keep codebase minimal |
