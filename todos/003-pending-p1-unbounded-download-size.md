---
status: complete
priority: p1
issue_id: "003"
tags: [code-review, security]
dependencies: []
---

# Add download size limit to county GeoJSON fetch

## Problem Statement

In `geo/counties.py`, the county boundaries download has no size limit. If the URL returned unexpected content (e.g., redirect to a large file), it would consume unbounded memory and disk space. The expected file is ~25MB, but there's no guard against anomalous responses.

## Findings

- **File:** `geo/counties.py`, `_download_counties` function
- **Evidence:** Uses `response.iter_content(chunk_size=8192)` with no total size check
- **Impact:** Potential resource exhaustion if URL serves unexpected content

## Proposed Solutions

### Solution A: Add Content-Length check and running total
- Check `Content-Length` header before downloading, reject if > 50MB
- Track bytes written during streaming, abort if exceeds limit
- **Pros:** Simple, effective defense-in-depth
- **Cons:** Some servers don't send Content-Length
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] Download aborts if response exceeds 50MB
- [ ] Normal ~25MB county file downloads successfully
- [ ] Clear error message if size limit exceeded

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Always bound external downloads |
