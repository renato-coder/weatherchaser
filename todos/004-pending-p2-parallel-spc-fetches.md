---
status: pending
priority: p2
issue_id: "004"
tags: [code-review, performance]
dependencies: []
---

# Parallelize SPC outlook HTTP fetches

## Problem Statement

`sources/spc.py` fetches 15 SPC URLs sequentially. Each request has a 30-second timeout, meaning worst case is 7.5 minutes. Even in normal operation, sequential fetches add unnecessary latency since these are independent requests.

## Findings

- **File:** `sources/spc.py`, `fetch_spc_outlooks` function
- **Evidence:** Simple `for` loop over `SPC_URLS` with sequential `_fetch_geojson` calls
- **Impact:** ~2-5 seconds total fetch time could be reduced to ~0.5-1 second with parallelism

## Proposed Solutions

### Solution A: ThreadPoolExecutor
- Use `concurrent.futures.ThreadPoolExecutor(max_workers=5)`
- Submit all 15 fetches, collect results
- **Pros:** Standard library, simple, I/O-bound task perfect for threads
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] All 15 URLs fetched concurrently (max 5 workers)
- [ ] Results identical to sequential fetch
- [ ] Error handling preserved per-URL
- [ ] Total fetch time measurably reduced

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | I/O-bound tasks benefit from threading |
