---
status: pending
priority: p2
issue_id: "006"
tags: [code-review, quality, typing]
dependencies: []
---

# Add proper type annotation to DayResult.state_summaries

## Problem Statement

`DayResult.state_summaries` is typed as bare `dict` instead of a proper typed structure. This loses type safety and makes the interface unclear for consumers.

## Findings

- **File:** `config.py`, `DayResult` dataclass
- **Evidence:** `state_summaries: dict = field(default_factory=dict)` — no key/value types
- **File:** `geo/matcher.py:90-104`, `aggregate_by_state` returns `dict[str, dict]` — inner dict has `count`, `highest_risk`, `counties` keys

## Proposed Solutions

### Solution A: TypedDict for state summary
- Create `StateSummary = TypedDict('StateSummary', count=int, highest_risk=int, counties=list[CountyRisk])`
- Type `state_summaries: dict[str, StateSummary]`
- **Pros:** Full type safety, IDE support
- **Effort:** Small
- **Risk:** Low

### Solution B: Dataclass for state summary
- Create `@dataclass class StateSummary` with typed fields
- **Pros:** Consistent with other dataclasses in config.py
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] `state_summaries` has explicit type annotation with key and value types
- [ ] Type checker (mypy/pyright) can validate usage

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Always type dict fields explicitly |
