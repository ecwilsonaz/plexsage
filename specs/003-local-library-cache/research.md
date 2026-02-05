# Research: Local Library Cache

**Feature**: 003-local-library-cache
**Date**: 2026-02-05

## Summary

No significant unknowns in the technical context. The stack is well-defined (Python/FastAPI/SQLite) and we have direct experience from the existing codebase. This document captures best practices and design decisions.

## Research Items

### 1. SQLite Concurrency for Background Sync

**Decision**: Use WAL (Write-Ahead Logging) mode with connection pooling

**Rationale**:
- WAL mode allows concurrent reads while writing (sync can happen while queries run)
- Single writer, multiple readers - perfect fit for our use case
- Built into SQLite, no additional dependencies

**Alternatives Considered**:
- Default journal mode: Would block reads during writes
- Separate read/write connections: More complex, WAL handles this automatically

**Implementation Note**:
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")  # 5 second timeout for lock contention
```

### 2. Batch Fetching from Plex

**Decision**: Use explicit pagination with `container_start` and `container_size` parameters

**Rationale**:
- Allows accurate progress tracking (know total upfront, track batch completion)
- Memory-efficient for large libraries (process 1000 tracks at a time)
- Already supported by python-plexapi

**Alternatives Considered**:
- Single fetch: Memory issues with 100k+ tracks
- Iterator/generator: Can't report progress accurately

**Implementation Note**:
```python
total = library.totalViewSize(libtype="track")
batch_size = 1000
for offset in range(0, total, batch_size):
    batch = library.search(libtype="track", container_start=offset, container_size=batch_size)
    # Process batch, update progress
```

### 3. Background Sync Without Blocking

**Decision**: Use `asyncio.create_task()` with in-memory progress state

**Rationale**:
- FastAPI is async-native, task runs in same event loop
- No need for threading/multiprocessing complexity
- Progress stored in module-level dict, polled by status endpoint

**Alternatives Considered**:
- Threading: Complicates SQLite access (need thread-local connections)
- Celery/background workers: Overkill for single-user app

**Implementation Note**:
```python
# Module-level state
_sync_state = {"is_syncing": False, "current": 0, "total": 0, "error": None}

async def start_sync():
    asyncio.create_task(_run_sync())  # Fire and forget
    return {"started": True}
```

### 4. Sync Progress Polling vs WebSocket

**Decision**: Polling via GET endpoint (1-second interval)

**Rationale**:
- Simpler implementation, no WebSocket infrastructure needed
- Sync takes 1-2 minutes; 1-second polling is 60-120 requests (negligible)
- Aligns with constitution principle of simplicity

**Alternatives Considered**:
- WebSocket: More responsive but adds complexity
- Server-Sent Events: Good middle ground but still more complex than polling

### 5. Transaction Strategy for Sync

**Decision**: Single transaction wrapping entire sync, with batch commits every 1000 tracks

**Rationale**:
- Atomic: If sync fails, no partial state
- Performance: Batch commits avoid per-row commit overhead
- Recovery: On failure, existing data preserved

**Alternatives Considered**:
- Per-track commits: Too slow (100k+ transactions)
- No transaction: Partial state on failure

**Implementation Note**:
```python
with conn:
    for offset in range(0, total, batch_size):
        # Insert batch
        if offset % 10000 == 0:  # Intermediate commit every 10k
            conn.commit()
    # Final commit happens on context exit
```

### 6. Data Directory Location

**Decision**: `data/` directory at repository root, sibling to `backend/` and `frontend/`

**Rationale**:
- Consistent with existing config.yaml location
- Easy to volume mount in Docker
- Clear separation from code

**Alternatives Considered**:
- Inside `backend/`: Mixes code and data
- `/var/lib/mediasage/`: Linux-specific, complicates dev setup

## Dependencies Confirmed

All dependencies are already in the project:
- `sqlite3`: Python stdlib
- `asyncio`: Python stdlib
- `FastAPI`: Already used
- `python-plexapi`: Already used
- `Pydantic`: Already used

**No new dependencies required.**

## Open Questions Resolved

None. All technical decisions are clear.
