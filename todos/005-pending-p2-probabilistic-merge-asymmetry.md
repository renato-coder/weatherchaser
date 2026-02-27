---
status: pending
priority: p2
issue_id: "005"
tags: [code-review, bug, data-integrity]
dependencies: []
---

# Fix probabilistic merge asymmetry in _merge_risk

## Problem Statement

In `geo/matcher.py`, `_merge_risk` handles `outlook_type == "probabilistic"` (Day 3-8 combined probability) by setting `hail_prob` and `wind_prob` but NOT `tornado_prob`. This creates an asymmetry where Day 4-8 probabilistic risk never triggers the tornado probability threshold in the classifier, even though the probability represents "any severe" including tornadoes.

## Findings

- **File:** `geo/matcher.py:83-88`, `_merge_risk` function
- **Evidence:** `existing.hail_prob = max(...)` and `existing.wind_prob = max(...)` but no `tornado_prob`
- **Impact:** Counties with Day 4-8 probabilistic risk won't trigger tornado-based CAT thresholds

## Proposed Solutions

### Solution A: Include tornado_prob in probabilistic merge
- Add `existing.tornado_prob = max(existing.tornado_prob, polygon.risk_level)` for probabilistic type
- **Pros:** Consistent with "any severe" semantics
- **Cons:** May over-flag tornado risk when the probability is really "any severe"
- **Effort:** Small
- **Risk:** Low

### Solution B: Add separate any_severe field to CountyRisk
- Add `any_severe_prob: int = 0` field to CountyRisk
- Store Day 4-8 probability there instead of splitting across hazard fields
- Update classifier to check this field against `any_severe_prob_min`
- **Pros:** More accurate data model, cleaner semantics
- **Cons:** More changes across files
- **Effort:** Medium
- **Risk:** Low

## Acceptance Criteria

- [ ] Day 4-8 probabilistic risk treated consistently across all hazard types
- [ ] Classifier threshold checks account for probabilistic data

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Asymmetric data merging causes threshold gaps |
