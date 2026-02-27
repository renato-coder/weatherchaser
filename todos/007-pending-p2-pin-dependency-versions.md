---
status: pending
priority: p2
issue_id: "007"
tags: [code-review, security, dependencies]
dependencies: []
---

# Pin dependency versions in requirements.txt

## Problem Statement

`requirements.txt` uses `>=` version specifiers which allow any future version. A malicious or breaking update could affect the application without notice.

## Findings

- **File:** `requirements.txt`
- **Evidence:** `requests>=2.31.0`, `shapely>=2.0.0`, `python-dotenv>=1.0.0`, `rich>=13.0.0`

## Proposed Solutions

### Solution A: Pin to exact versions currently installed
- Use `pip freeze` to get exact versions, pin with `==`
- **Pros:** Reproducible builds, no surprise updates
- **Effort:** Small
- **Risk:** Low

## Acceptance Criteria

- [ ] All dependencies pinned to exact versions
- [ ] `pip install -r requirements.txt` installs identical versions every time

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-02-27 | Created from code review | Pin deps for reproducibility |
