---
status: pending
priority: p3
issue_id: "010"
tags: [code-review, architecture]
dependencies: []
---

# Move aggregate_by_state from matcher.py to classifier.py

## Problem Statement

`aggregate_by_state()` in `geo/matcher.py` is a presentation/classification concern, not a geographic matching concern. It's only called from `classifier.py`, creating an awkward dependency direction.

## Findings

- **File:** `geo/matcher.py:90-104`, `aggregate_by_state` function
- **Called from:** `classifier.py:34`
- **Evidence:** Function groups county risks by state for display — this is post-matching logic

## Proposed Solutions

### Solution A: Move to classifier.py
- Move the function to `classifier.py`
- Remove import from `classifier.py`
- **Pros:** Better module cohesion, cleaner dependency graph
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] `aggregate_by_state` lives in `classifier.py`
- [ ] No circular imports introduced
- [ ] All callers updated

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Keep related logic in same module |
