# Quickstart: Local Library Cache

**Feature**: 003-local-library-cache
**Date**: 2026-02-05

## Overview

This feature adds a local SQLite cache for Plex track metadata, eliminating the 2-minute cold start for large libraries.

## Key Files

| File | Purpose |
|------|---------|
| `backend/library_cache.py` | NEW - Core cache logic, sync operations |
| `backend/main.py` | MODIFY - Add `/api/library/*` endpoints |
| `backend/models.py` | MODIFY - Add Pydantic models for sync status |
| `frontend/app.js` | MODIFY - Add sync polling, progress modal |
| `frontend/index.html` | MODIFY - Add footer status section |
| `frontend/style.css` | MODIFY - Footer and modal styling |

## Development Setup

No additional setup required. SQLite is included in Python stdlib.

```bash
# Existing setup works
source .venv/bin/activate
uvicorn backend.main:app --reload --port 5765
```

The cache database (`data/library_cache.db`) is created automatically on first sync.

## Testing Locally

```bash
# Run tests
source .venv/bin/activate && python -m pytest tests/test_library_cache.py -v

# Manual testing
# 1. Start the server
# 2. Open http://localhost:5765
# 3. First load triggers blocking sync with progress modal
# 4. Subsequent loads use cached data (<2 seconds)
# 5. Footer shows "18,432 tracks · Synced 2 hours ago · Refresh"
```

## API Endpoints

### GET /api/library/status

Returns cache state for UI polling.

```json
{
  "track_count": 18432,
  "synced_at": "2026-02-05T10:30:00Z",
  "is_syncing": false,
  "sync_progress": null,
  "error": null,
  "plex_connected": true
}
```

During sync:
```json
{
  "track_count": 18432,
  "synced_at": "2026-02-05T10:30:00Z",
  "is_syncing": true,
  "sync_progress": {
    "current": 5000,
    "total": 18432
  },
  "error": null,
  "plex_connected": true
}
```

### POST /api/library/sync

Triggers a sync. Returns immediately for background sync.

```json
{
  "started": true,
  "blocking": false
}
```

## Architecture Notes

### Sync Flow

```
1. User opens app
2. Frontend calls GET /api/library/status
3. If track_count == 0:
   a. Show blocking modal
   b. POST /api/library/sync
   c. Poll status every 1s until is_syncing == false
   d. Dismiss modal
4. Else:
   a. Load cached data immediately
   b. If synced_at > 24h ago, POST /api/library/sync (background)
5. Footer shows status, refresh link triggers POST /api/library/sync
```

### SQLite Schema

```sql
CREATE TABLE tracks (
    rating_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    duration_ms INTEGER,
    year INTEGER,
    genres TEXT,
    user_rating INTEGER,
    is_live BOOLEAN,
    updated_at TIMESTAMP
);

CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    plex_server_id TEXT,
    last_sync_at TIMESTAMP,
    track_count INTEGER,
    sync_duration_ms INTEGER
);
```

### Module Responsibilities

- **library_cache.py**: All SQLite operations, sync orchestration, progress tracking
- **plex_client.py**: Provides batch track fetching (existing + new helper)
- **main.py**: Exposes endpoints, triggers startup sync check
- **app.js**: Polls status, renders progress modal and footer

## Common Tasks

### Reset Cache

Delete the database file:
```bash
rm data/library_cache.db
```

Next app load will trigger full re-sync.

### Debug Sync Issues

Check logs for sync progress:
```
INFO:backend.library_cache:Starting sync, total tracks: 18432
INFO:backend.library_cache:Synced 1000/18432 tracks
INFO:backend.library_cache:Synced 2000/18432 tracks
...
INFO:backend.library_cache:Sync complete in 95432ms
```

### Force Re-sync

POST to `/api/library/sync` or click "Refresh" in footer.
