# Feature Specification: Local Library Cache

**Feature Branch**: `001-local-library-cache`
**Created**: 2026-02-05
**Status**: Draft
**Input**: Cache Plex track metadata in SQLite to eliminate 2-minute cold start queries

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fast Library Access on Repeat Visits (Priority: P1)

A user opens MediaSage to create a playlist. Instead of waiting 2+ minutes for their 18,000-track library to load from Plex, the app instantly loads track data from the local cache, allowing them to start generating playlists immediately.

**Why this priority**: This is the core value proposition. Without fast library access, the entire feature is pointless. Every other story depends on having cached data available.

**Independent Test**: Can be fully tested by opening the app after an initial sync and verifying track data loads in under 2 seconds. Delivers immediate value by eliminating the cold-start wait.

**Acceptance Scenarios**:

1. **Given** the library has been synced previously, **When** the user opens MediaSage, **Then** track data is available for playlist generation within 2 seconds
2. **Given** the library has been synced previously, **When** the user applies genre/decade filters, **Then** results appear within 1 second using cached data
3. **Given** the library cache exists, **When** the user generates a playlist, **Then** track matching uses cached metadata without querying Plex

---

### User Story 2 - First-Time Library Sync (Priority: P1)

A new user connects MediaSage to their Plex server for the first time. The app syncs their entire music library, showing clear progress so they understand what's happening and how long it will take.

**Why this priority**: Without initial sync, there's no cached data. This is the prerequisite for all other functionality. Tied with P1 above as both are essential.

**Independent Test**: Can be tested by connecting a fresh MediaSage instance to a Plex server and verifying the sync completes with accurate progress indication.

**Acceptance Scenarios**:

1. **Given** no local cache exists, **When** the user connects to Plex, **Then** a blocking sync modal appears with progress indicator
2. **Given** sync is in progress, **When** the user views the progress modal, **Then** they see current count and total count updating in real-time
3. **Given** sync completes successfully, **When** the modal dismisses, **Then** the user can immediately use all app features
4. **Given** sync is in progress, **When** the user closes the browser, **Then** the partial sync is discarded and restarts on next visit

---

### User Story 3 - Manual Library Refresh (Priority: P2)

A user adds new albums to their Plex library and wants MediaSage to recognize them. They click a refresh link in the footer to trigger a new sync, which runs in the background while they continue using the app.

**Why this priority**: Users need control over when to update their cache. Important for usability but not blocking - stale data is functional data.

**Independent Test**: Can be tested by adding tracks to Plex, clicking refresh, and verifying new tracks appear after sync completes.

**Acceptance Scenarios**:

1. **Given** cached data exists, **When** the user clicks the refresh link, **Then** a background sync starts without interrupting current activity
2. **Given** a background sync is running, **When** the user views the footer, **Then** they see "Syncing..." with a progress indicator
3. **Given** a background sync completes, **When** the user applies filters, **Then** newly synced tracks are included in results

---

### User Story 4 - Sync Status Visibility (Priority: P2)

A user wants to know when their library was last synced and how many tracks are cached. The footer displays this information clearly, helping them decide if they need to refresh.

**Why this priority**: Transparency builds trust. Users should never wonder if their data is stale. Supports manual refresh decision-making.

**Independent Test**: Can be tested by verifying footer displays accurate track count and relative sync time after any sync operation.

**Acceptance Scenarios**:

1. **Given** library has been synced, **When** the user views the footer, **Then** they see track count and relative time since last sync (e.g., "18,432 tracks · Synced 2 hours ago")
2. **Given** library has never been synced, **When** the user views the footer, **Then** they see "Not synced" with a sync prompt
3. **Given** the last sync failed, **When** the user views the footer, **Then** they see "Sync failed" with a retry option

---

### User Story 5 - Automatic Background Refresh (Priority: P3)

When a user opens MediaSage and the cache is more than 24 hours old, the app automatically refreshes in the background. The user works with existing cached data while fresh data loads.

**Why this priority**: Nice-to-have automation. Users who forget to manually refresh still get reasonably fresh data. Lower priority because manual refresh covers this need.

**Independent Test**: Can be tested by setting system time forward 25 hours and verifying background sync triggers on app load.

**Acceptance Scenarios**:

1. **Given** last sync was more than 24 hours ago, **When** the user opens the app, **Then** a background sync starts automatically
2. **Given** last sync was less than 24 hours ago, **When** the user opens the app, **Then** no automatic sync occurs
3. **Given** automatic background sync is running, **When** the user interacts with the app, **Then** they use existing cached data without interruption

---

### User Story 6 - Plex Server Change Detection (Priority: P3)

A user switches their MediaSage instance to point at a different Plex server. The app detects this and automatically clears the old cache, then syncs the new library.

**Why this priority**: Edge case but important for data integrity. Prevents confusion from mixing libraries. Lower priority as most users have one Plex server.

**Independent Test**: Can be tested by changing Plex server configuration and verifying old cache is cleared and new sync begins.

**Acceptance Scenarios**:

1. **Given** cached data exists for Server A, **When** the user configures Server B, **Then** the old cache is cleared
2. **Given** server change is detected, **When** the app loads, **Then** a blocking sync for the new server begins
3. **Given** server change is detected, **When** the user views status, **Then** they see a message indicating new library sync

---

### Edge Cases

- What happens when Plex becomes unreachable mid-sync? Sync aborts, existing cache preserved, error displayed.
- What happens when a sync batch times out? Retry once, then abort with error if still failing.
- What happens when the local storage write fails? Transaction rollback, existing data preserved, error reported.
- What happens when cache exists but Plex is offline? App works with cached data, footer warns "Plex offline".
- What happens when cache is empty and Plex is offline? Error displayed, app cannot proceed until Plex is reachable.
- What happens when user has 100,000+ tracks? Sync takes longer but batching prevents memory issues.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST store track metadata locally to enable queries without Plex server access
- **FR-002**: System MUST sync all track metadata from Plex including: title, artist, album, duration, year, genres, user rating
- **FR-003**: System MUST pre-compute live recording detection during sync to enable fast filtering
- **FR-004**: System MUST display sync progress showing current count and total count during sync operations
- **FR-005**: System MUST block user interaction during first-time sync when no cached data exists
- **FR-006**: System MUST allow background sync when cached data already exists
- **FR-007**: System MUST display last sync time and track count in the application footer
- **FR-008**: System MUST provide a manual refresh option in the footer
- **FR-009**: System MUST automatically trigger background sync when cached data is older than 24 hours
- **FR-010**: System MUST detect Plex server changes and clear stale cache data
- **FR-011**: System MUST preserve existing cache data when a sync fails
- **FR-012**: System MUST handle track deletions in Plex by removing them from local cache during sync
- **FR-013**: System MUST support all existing filter operations (genre, decade, rating, live exclusion) using cached data

### Key Entities

- **Track Cache**: Local copy of track metadata including rating key, title, artist, album, duration, year, genres, user rating, and pre-computed live recording flag
- **Sync State**: Metadata about the cache including Plex server identifier, last sync timestamp, track count, and sync duration

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can access their library data within 2 seconds of opening the app (after initial sync)
- **SC-002**: Users can see accurate sync progress during initial library sync
- **SC-003**: Users can continue using the app while background sync refreshes data
- **SC-004**: Users can manually trigger a library refresh at any time
- **SC-005**: Users can see when their library was last synced and how many tracks are cached
- **SC-006**: System handles libraries of 50,000+ tracks without memory exhaustion
- **SC-007**: Cache data accurately reflects Plex library state after sync completes (no missing or phantom tracks)
