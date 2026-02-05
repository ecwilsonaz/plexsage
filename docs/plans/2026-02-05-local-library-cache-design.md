# Local Library Cache

Cache Plex track metadata in SQLite to eliminate 2-minute cold start queries.

## Problem

Querying 18k tracks from Plex takes ~2 minutes. This happens on every cold start or cache miss. Users wait too long before they can generate playlists.

## Solution

Sync track metadata to a local SQLite database. Query locally instead of hitting Plex for every request. Plex is still required for playlist creation.

## Data Model

**Database location:** `data/library_cache.db`

```sql
CREATE TABLE tracks (
    rating_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    duration_ms INTEGER,
    year INTEGER,
    genres TEXT,          -- JSON array
    user_rating INTEGER,  -- 0-10, NULL if unrated
    is_live BOOLEAN,      -- Pre-computed from title/album patterns
    updated_at TIMESTAMP
);

CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Single row
    plex_server_id TEXT,      -- Detect server changes
    last_sync_at TIMESTAMP,
    track_count INTEGER,
    sync_duration_ms INTEGER
);

CREATE INDEX idx_tracks_artist ON tracks(artist);
CREATE INDEX idx_tracks_year ON tracks(year);
```

The `is_live` field is computed at sync time using existing detection logic (title/album patterns for "live", "concert", date strings). This avoids regex on every query.

## Sync Behavior

### When sync triggers

1. **First run** - Database empty, must sync before proceeding
2. **Startup** - If last sync was >24 hours ago, background sync
3. **Server change** - Different `machineIdentifier` detected, wipe and re-sync
4. **Manual** - User clicks refresh link in footer

### Sync process

1. Get total track count via `library.totalViewSize()` (instant)
2. Fetch tracks in batches of 1000 using `container_start`/`container_size`
3. For each batch:
   - Compute `is_live` for each track
   - Upsert into `tracks` table
   - Update progress state
4. Delete tracks in local DB not present in Plex (handles removals)
5. Update `sync_state` with timestamp

Entire sync runs in a transaction. Failure rolls back; existing data preserved.

### Blocking vs background

- **Empty database:** Blocking modal with progress bar. User must wait.
- **Existing data:** Background sync. UI works with current data, refreshes when done.

## Progress Tracking

Sync progress stored in memory (not DB):

```python
{
    "is_syncing": True,
    "current": 5000,
    "total": 18432
}
```

Frontend polls `/api/library/status` every second during sync. Displays: "Syncing... 5,000 / 18,432 tracks"

## API Endpoints

```
GET  /api/library/status
Response: {
    "track_count": 18432,
    "synced_at": "2026-02-05T10:30:00Z",  // null if never synced
    "is_syncing": false,
    "sync_progress": null,  // or { "current": 5000, "total": 18432 }
    "error": null  // or error message string
}

POST /api/library/sync
Response: { "started": true }
Triggers background sync. Returns immediately.
```

## UI Changes

### Footer status

Add to existing footer:

```
Library: 18,432 tracks · Synced 2 hours ago · [Refresh]
```

States:
- **Normal:** "Synced 2 hours ago"
- **Syncing:** "Syncing..." with spinner
- **Never synced:** "Not synced · [Sync now]"
- **Error:** "Sync failed · [Retry]"

### First-run modal

Blocking overlay when database is empty:

```
┌─────────────────────────────────┐
│                                 │
│   Syncing your Plex library...  │
│                                 │
│   ████████████░░░░░░  5,000     │
│                      / 18,432   │
│                                 │
└─────────────────────────────────┘
```

Auto-dismisses when sync completes.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Plex unreachable during sync | Abort, keep existing data, show error |
| Batch timeout | Retry once, then abort |
| SQLite write failure | Rollback transaction, report error |
| Cache empty + Plex offline | Show error, cannot proceed |
| Cache exists + Plex offline | Serve cached data, warn in footer |
| Plex server changed | Wipe cache, prompt user, full re-sync |

## File Changes

**New files:**
- `backend/library_cache.py` - Cache operations, sync logic
- `data/library_cache.db` - Auto-created

**Modified files:**
- `backend/main.py` - New endpoints, startup sync hook
- `backend/plex_client.py` - Expose batch fetch helper
- `frontend/index.html` - Footer status section
- `frontend/app.js` - Poll status, progress modal
- `frontend/style.css` - Footer and modal styling
- `.gitignore` - Add `data/*.db`

**Unchanged:**
- `models.py` - Cache is internal
- `generator.py` - Same interface, different source
- `llm_client.py` - Unaffected

## Dependencies

None. SQLite is built into Python.

## Future Considerations

Not in scope, but the SQLite foundation enables:
- Full-text search across metadata
- Custom user tags
- Play history tracking
- Smarter incremental sync using Plex's recently-added endpoints

These are explicitly deferred. Ship the speed improvement first.
