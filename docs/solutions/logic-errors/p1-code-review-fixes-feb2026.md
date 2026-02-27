---
title: "P1 Code Review Fixes: Label Boundary Bug, Bare Exception Handlers, and Unbounded Downloads"
date: "2026-02-27"
category: "logic-errors"
tags:
  - code-review
  - spc-parsing
  - error-handling
  - resource-management
  - data-validation
  - security
modules:
  - sources/spc.py
  - geo/counties.py
  - geo/matcher.py
severity: p1
symptoms:
  - "SPC risk label '1' incorrectly classified as 100% probability instead of 1%"
  - "Programming errors silently swallowed by bare except Exception clauses"
  - "Unbounded county GeoJSON download with no size guard"
root_causes:
  - "Boundary condition in _label_to_risk_level(): 0 < val <= 1.0 includes val=1.0, causing label '1' to return 100 instead of 1"
  - "Overly broad except Exception handlers in 3 files catching all errors including programming bugs"
  - "Missing Content-Length validation and streaming byte counter in county GeoJSON download"
resolution_status: fixed
---

# P1 Code Review Fixes: Label Boundary, Exception Handling, Unbounded Downloads

Three critical findings from a multi-agent code review of the Remi CAT Event Tracker core pipeline (Steps 1-5). All discovered 2026-02-27, fixed and verified same day.

## Fix 1: Label Boundary Bug

**File:** `sources/spc.py:180` — `_label_to_risk_level()`

### Problem

The condition `0 < val <= 1.0` treated `val == 1.0` as a decimal fraction, returning `int(1.0 * 100) = 100`. But SPC uses `"1"` to mean 1% probability, not 100%.

### Root Cause

SPC probabilistic labels come in two formats: decimal strings (`"0.15"` = 15%) and integer strings (`"15"` = 15%). The parser used `<= 1.0` to detect decimals, but `1.0` sits on the boundary — it could mean either "100% as a fraction" or "1% as an integer". SPC never uses 100% probability, so `"1"` always means 1%.

### Before

```python
if 0 < val <= 1.0:
    return int(val * 100)  # "1" → 100 (WRONG)
if val > 1:
    return int(val)
```

### After

```python
if 0 < val < 1.0:
    return int(val * 100)  # "0.15" → 15 (correct)
if val >= 1:
    return int(val)        # "1" → 1, "15" → 15 (correct)
```

### Why It Works

Changing `<=` to `<` excludes `1.0` from the decimal-fraction branch. It falls through to `>= 1` and returns `int(1)` = 1.

### Key Values

| Label | Before | After |
|-------|--------|-------|
| `"0.15"` | 15 | 15 |
| `"1"` | 100 | 1 |
| `"15"` | 15 | 15 |
| `"30"` | 30 | 30 |

---

## Fix 2: Bare except Exception

**Files:** `sources/spc.py:124`, `geo/counties.py:92`, `geo/matcher.py:49`

### Problem

Three locations used `except Exception` which catches everything — including `KeyError`, `AttributeError`, and other programming bugs — silently skipping them with `continue`. This makes debugging nearly impossible.

### Root Cause

During initial implementation, broad exception handlers were used as a safety net for unknown failure modes in Shapely geometry parsing and spatial indexing. They should have been narrowed once the expected exceptions were known.

### Before / After

**`sources/spc.py` — shape() call:**
```python
# Before
except Exception:
    continue

# After
except (ValueError, TypeError):
    continue
```

**`geo/counties.py` — shape() + centroid:**
```python
# Before
except Exception:
    skipped += 1
    continue

# After
except (ValueError, TypeError):
    skipped += 1
    continue
```

**`geo/matcher.py` — STRtree.query():**
```python
# Before
from shapely.geometry import MultiPolygon

except Exception:
    continue

# After
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon

except GEOSException:
    continue
```

### Why It Works

- `shape()` raises `ValueError` for invalid geometry data and `TypeError` for wrong argument types — these are the only expected exceptions from malformed GeoJSON
- `STRtree.query()` raises `GEOSException` for geometry operation failures — the only expected spatial indexing error
- Programming bugs (`KeyError`, `AttributeError`, etc.) now propagate with full tracebacks

---

## Fix 3: Unbounded Download Size

**File:** `geo/counties.py:29-59` — `_download_counties()`

### Problem

The county boundaries download (~25 MB) had no size limit. A redirect to unexpected content or a compromised URL could consume unbounded disk space and memory.

### Root Cause

The original implementation streamed chunks but never checked total bytes written. No `Content-Length` validation was performed.

### Before

```python
def _download_counties(dest: str) -> None:
    # ... setup, request ...
    total = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            total += len(chunk)
    # No size check anywhere
```

### After

```python
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB safety limit

def _download_counties(dest: str) -> None:
    # ... setup, request ...

    # Defense 1: Check Content-Length header
    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > _MAX_DOWNLOAD_BYTES:
        print(f"\nFATAL: County file too large ({int(content_length)} bytes)", file=sys.stderr)
        sys.exit(1)

    # Defense 2: Track bytes during streaming
    total = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > _MAX_DOWNLOAD_BYTES:
                f.close()
                os.remove(dest)  # Clean up partial file
                print(f"\nFATAL: Download exceeded {_MAX_DOWNLOAD_BYTES // (1024*1024)} MB limit", file=sys.stderr)
                sys.exit(1)
            f.write(chunk)
```

### Why It Works

Two layers of defense:
1. **Pre-flight:** Reject immediately if `Content-Length` header exceeds 50 MB
2. **Streaming:** Running byte counter aborts mid-download if limit exceeded, with cleanup of the partial file

The 50 MB limit is 2x the expected ~25 MB file, giving headroom for natural growth while catching anomalies.

---

## Prevention Strategies

### For Boundary Bugs in Data Parsing

- **Prefer explicit lookup tables** over range-based float comparisons for categorical data
- **Test boundary values explicitly**: 0, 0.99, 1.0, 1.01, 100
- **Document the data contract**: what formats does the external source actually use?

### For Exception Handling

- **Narrow the try block** to only the code that can raise the expected exception
- **Catch specific types**: `ValueError`, `TypeError` for data parsing; `GEOSException` for Shapely operations
- **Never catch `Exception`** unless you log and re-raise

### For External Downloads

- **Always set a size limit** on HTTP downloads from external sources
- **Check both header and streaming size** — headers can lie
- **Clean up partial files** on abort to prevent corrupt cache state

## Code Review Checklist

- [ ] No bare `except Exception` — all catch blocks name specific types
- [ ] Float comparisons on external data tested at boundary values
- [ ] HTTP downloads have size limits with cleanup on abort
- [ ] External data format assumptions documented in comments

## Related References

- **Implementation plan:** `docs/plans/2026-02-27-feat-core-weather-pipeline-steps-1-5-plan.md`
- **CLAUDE.md gotcha:** "SPC GeoJSON geometry may be MultiPolygon — handle both Polygon and MultiPolygon"
- **CLAUDE.md gotcha:** "County boundaries GeoJSON is ~25MB — download once, cache in data/ directory"
- **Todo files:** `todos/001-pending-p1-label-boundary-bug.md`, `todos/002-pending-p1-bare-except-exception.md`, `todos/003-pending-p1-unbounded-download-size.md`
