---
status: complete
priority: p1
issue_id: "001"
tags: [code-review, bug, data-integrity]
dependencies: []
---

# Fix _label_to_risk_level boundary bug at val == 1.0

## Problem Statement

In `sources/spc.py`, the `_label_to_risk_level` function has a logic error when parsing probabilistic labels. When `val == 1.0`, it falls through to the `val > 1` branch and gets treated as a percentage (divided by 100 = 0.01 = rounded to 0%). But a label of "1" should mean 1% probability, not 100%.

The condition `if val <= 1.0` captures values 0-1 (decimal fractions like 0.15 = 15%), but `val == 1.0` is ambiguous — it could mean 100% (as a decimal fraction) or 1% (as a bare integer). SPC data uses "1" to mean 1%, not 100%.

## Findings

- **File:** `sources/spc.py`, `_label_to_risk_level` function
- **Evidence:** `if val <= 1.0: return int(val * 100)` — when val is 1.0, returns 100 (wrong for "1" meaning 1%)
- **Impact:** Could misclassify risk levels, potentially flagging counties as HIGH risk when they should be minimal

## Proposed Solutions

### Solution A: Use strict less-than for decimal detection
- Check `if val < 1.0` instead of `if val <= 1.0`
- Values like 0.15, 0.30 are treated as decimals (15%, 30%)
- Value 1.0 treated as integer 1 → 1%
- **Pros:** Simple fix, handles SPC data correctly
- **Cons:** Loses the ability to represent 100% as 1.0 (but SPC never uses this)
- **Effort:** Small
- **Risk:** Low

### Solution B: Threshold-based heuristic
- If val > 0 and val < 1.0, treat as decimal fraction
- If val >= 1.0, treat as integer percentage
- **Pros:** Clear separation, no ambiguity
- **Cons:** Slightly more code
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] Label "1" returns 1 (1%), not 100
- [ ] Label "0.15" still returns 15
- [ ] Label "15" still returns 15
- [ ] Label "0.02" returns 2
- [ ] Label "30" returns 30

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Boundary condition in float comparison |
