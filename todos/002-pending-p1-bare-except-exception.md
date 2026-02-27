---
status: complete
priority: p1
issue_id: "002"
tags: [code-review, error-handling, quality]
dependencies: []
---

# Replace bare except Exception with specific exception types

## Problem Statement

Three files use bare `except Exception` blocks that silently swallow errors, making debugging difficult and potentially hiding real bugs. Errors like `KeyError`, `TypeError`, or `AttributeError` from code bugs get caught and treated as expected failures.

## Findings

- **`sources/spc.py:_fetch_geojson`** — catches all exceptions during HTTP fetch, should only catch network-related errors
- **`sources/spc.py:_parse_features`** — catches all exceptions during feature parsing, hides malformed data issues
- **`geo/matcher.py:match_counties`** — `except Exception: continue` in STRtree query silently skips geometry errors

## Proposed Solutions

### Solution A: Narrow exception types
- `_fetch_geojson`: catch `requests.RequestException`, `requests.Timeout`
- `_parse_features`: catch `KeyError`, `ValueError` for malformed GeoJSON
- `match_counties`: catch `shapely.errors.GEOSException` for geometry issues
- **Pros:** Only catches expected errors, programming bugs surface immediately
- **Cons:** May miss edge cases initially
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] No bare `except Exception` remains in codebase
- [ ] Each catch block specifies the exact exception types expected
- [ ] Unexpected errors propagate up with full traceback
- [ ] Network errors in spc.py still handled gracefully

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Bare except is an anti-pattern |
