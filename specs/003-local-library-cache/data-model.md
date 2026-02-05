# Data Model: Local Library Cache

**Feature**: 003-local-library-cache
**Date**: 2026-02-05

## Entities

### Track (Cached)

Local copy of track metadata from Plex.

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| rating_key | TEXT | PRIMARY KEY | Plex unique identifier |
| title | TEXT | NOT NULL | Track title |
| artist | TEXT | NOT NULL | Artist name (grandparentTitle in Plex) |
| album | TEXT | NOT NULL | Album name (parentTitle in Plex) |
| duration_ms | INTEGER | | Track duration in milliseconds |
| year | INTEGER | NULLABLE | Release year |
| genres | TEXT | | JSON array of genre strings |
| user_rating | INTEGER | 0-10, NULLABLE | Plex user rating |
| is_live | BOOLEAN | | Pre-computed from title/album patterns |
| updated_at | TIMESTAMP | | When this record was last synced |

**Indexes:**
- `idx_tracks_artist` on `artist` (for future artist-based queries)
- `idx_tracks_year` on `year` (for decade filtering)
- `idx_tracks_is_live` on `is_live` (for live exclusion filtering)

### SyncState

Metadata about the cache state.

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | INTEGER | PRIMARY KEY, CHECK (id = 1) | Single-row table |
| plex_server_id | TEXT | | Plex machineIdentifier |
| last_sync_at | TIMESTAMP | NULLABLE | When last successful sync completed |
| track_count | INTEGER | | Number of tracks in cache |
| sync_duration_ms | INTEGER | | How long last sync took |

**Design Note:** Single-row table enforced by CHECK constraint. Simplifies queries (no WHERE clause needed).

## Relationships

```text
┌─────────────────────┐
│     SyncState       │
│  (singleton row)    │
├─────────────────────┤
│ plex_server_id      │──┐
│ last_sync_at        │  │
│ track_count         │  │  Tracks belong to
│ sync_duration_ms    │  │  one Plex server
└─────────────────────┘  │
                         │
                         ▼
┌─────────────────────┐
│       Track         │
│   (many rows)       │
├─────────────────────┤
│ rating_key (PK)     │
│ title               │
│ artist              │
│ album               │
│ ...                 │
└─────────────────────┘
```

No foreign key relationship - when server changes, all tracks are deleted and re-synced.

## State Transitions

### Sync State Machine

```text
                    ┌─────────────┐
                    │  UNSYNCED   │
                    │ (no cache)  │
                    └──────┬──────┘
                           │ First connection
                           ▼
                    ┌─────────────┐
        ┌──────────│   SYNCING   │──────────┐
        │          │ (blocking)  │          │
        │          └──────┬──────┘          │
        │ Error           │ Success         │
        │                 ▼                 │
        │          ┌─────────────┐          │
        │          │   SYNCED    │──────────┤
        │          │ (ready)     │          │
        │          └──────┬──────┘          │
        │                 │                 │
        │    ┌────────────┼────────────┐    │
        │    │            │            │    │
        │    │ Manual     │ Auto       │ Server
        │    │ refresh    │ (>24h)     │ change
        │    ▼            ▼            ▼    │
        │   ┌─────────────────────────┐     │
        └──▶│   SYNCING (background)  │─────┘
            │   (non-blocking)        │
            └─────────────────────────┘
```

### Sync States

| State | DB Empty | is_syncing | last_sync_at | User Experience |
|-------|----------|------------|--------------|-----------------|
| UNSYNCED | Yes | No | NULL | Cannot proceed, must sync |
| SYNCING (blocking) | Yes | Yes | NULL | Modal with progress bar |
| SYNCED | No | No | Timestamp | Normal operation |
| SYNCING (background) | No | Yes | Timestamp | Normal + footer "Syncing..." |
| ERROR | Maybe | No | Maybe | Error message, retry option |

## Validation Rules

### Track Validation

- `rating_key`: Must be non-empty string
- `title`, `artist`, `album`: Must be non-empty strings
- `duration_ms`: Must be non-negative integer
- `year`: If present, must be 4-digit year (1900-2100)
- `user_rating`: If present, must be 0-10
- `genres`: Must be valid JSON array (can be empty `[]`)
- `is_live`: Computed, not user-provided

### SyncState Validation

- `plex_server_id`: Must match connected Plex server
- `track_count`: Must equal actual row count in tracks table
- `sync_duration_ms`: Must be positive integer

## Data Volume Assumptions

| Library Size | Tracks Table | Estimated DB Size |
|--------------|--------------|-------------------|
| Small | 5,000 tracks | ~2 MB |
| Medium | 20,000 tracks | ~8 MB |
| Large | 50,000 tracks | ~20 MB |
| Very Large | 100,000 tracks | ~40 MB |

SQLite handles these sizes trivially. The `genres` JSON field is the largest per-row (~200 bytes average), but still well within SQLite's capabilities.
