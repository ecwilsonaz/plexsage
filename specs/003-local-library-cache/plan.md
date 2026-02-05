# Implementation Plan: Local Library Cache

**Branch**: `003-local-library-cache` | **Date**: 2026-02-05 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-local-library-cache/spec.md`

## Summary

Cache Plex track metadata in a local SQLite database to eliminate the 2-minute cold start query time for large libraries. Users sync once, then enjoy instant library access on subsequent visits. The feature adds a new `library_cache.py` module, footer status display, sync progress modal, and API endpoints for sync control.

## Technical Context

**Language/Version**: Python 3.11+ (backend), Vanilla JavaScript ES6+ (frontend)
**Primary Dependencies**: FastAPI, python-plexapi, sqlite3 (stdlib), Pydantic
**Storage**: SQLite file at `data/library_cache.db`
**Testing**: pytest with mocked Plex/SQLite interactions
**Target Platform**: Linux server (Docker), macOS/Windows for development
**Project Type**: Web application (backend API + static frontend)
**Performance Goals**: <2 seconds library load from cache, <1 second filter operations
**Constraints**: Must run on NAS hardware (4GB RAM), sync 50k+ tracks without memory exhaustion
**Scale/Scope**: Libraries up to 100,000+ tracks, single user per instance

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Library-First Guarantee | ✅ Pass | Cache contains only user's actual library tracks |
| II. Simplicity Over Features | ✅ Pass | Single SQLite file, no new dependencies, simple sync model |
| III. User Agency in Curation | ✅ Pass | Manual refresh option, visible sync status, no hidden operations |
| IV. Transparent Cost Awareness | ✅ Pass | No LLM calls involved; sync is free |
| V. Plexamp-Native Experience | ✅ Pass | Footer status follows existing design language |

**Design Standards Compliance:**
- Dark theme: Footer status uses existing CSS variables
- Performance: Meets NAS hardware constraints (SQLite is lightweight)
- Data Handling: Cache stored locally, no external data transmission

## Project Structure

### Documentation (this feature)

```text
specs/003-local-library-cache/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── library_cache.py     # NEW: SQLite cache operations, sync logic
├── main.py              # MODIFY: Add sync endpoints, startup hook
├── plex_client.py       # MODIFY: Expose batch fetch helper
├── models.py            # MODIFY: Add sync status models
└── config.py            # No changes needed

frontend/
├── index.html           # MODIFY: Add footer status section
├── app.js               # MODIFY: Add sync polling, progress modal
└── style.css            # MODIFY: Footer and modal styling

data/
└── library_cache.db     # NEW: Auto-created SQLite database

tests/
├── test_library_cache.py # NEW: Cache and sync tests
└── test_api.py          # MODIFY: Add sync endpoint tests
```

**Structure Decision**: Follows existing web application layout. New `library_cache.py` module encapsulates all cache logic, keeping plex_client.py focused on Plex API interactions.

## Complexity Tracking

No constitution violations requiring justification. Feature uses:
- Built-in SQLite (no new dependencies)
- Existing project structure patterns
- Simple sync model (full refresh, not incremental)
