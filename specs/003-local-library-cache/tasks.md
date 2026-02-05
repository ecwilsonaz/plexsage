# Tasks: Local Library Cache

**Input**: Design documents from `/specs/003-local-library-cache/`
**Prerequisites**: plan.md, spec.md, data-model.md, contracts/api.yaml, research.md, quickstart.md

**Tests**: Tests are included per constitution requirement for critical path coverage.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

- **Backend**: `backend/` (Python/FastAPI)
- **Frontend**: `frontend/` (Vanilla JS)
- **Tests**: `tests/`
- **Data**: `data/` (SQLite database)

---

## Phase 1: Setup

**Purpose**: Project initialization and data directory structure

- [X] T001 Create `data/` directory and add `data/*.db` to `.gitignore`
- [X] T002 [P] Add Pydantic models for sync status in `backend/models.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core SQLite infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 Create `backend/library_cache.py` with SQLite schema initialization (tracks, sync_state tables)
- [X] T004 Implement database connection management with WAL mode in `backend/library_cache.py`
- [X] T005 Add batch track fetching helper to `backend/plex_client.py` (container_start/container_size pagination)
- [X] T006 [P] Create `tests/test_library_cache.py` with schema creation tests

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Fast Library Access (Priority: P1) 🎯 MVP

**Goal**: Users can access cached track data within 2 seconds of opening the app

**Independent Test**: Open app after initial sync, verify track data loads in under 2 seconds

### Implementation for User Story 1

- [X] T007 [US1] Implement `get_cached_tracks()` function in `backend/library_cache.py`
- [X] T008 [US1] Implement `get_tracks_by_filters()` using cache in `backend/library_cache.py` (genre, decade, rating, live exclusion)
- [X] T009 [US1] Implement `get_sync_state()` function in `backend/library_cache.py`
- [X] T010 [US1] Add GET `/api/library/status` endpoint in `backend/main.py`
- [X] T011 [US1] Modify existing track query endpoints to use cache instead of live Plex queries in `backend/main.py`
- [X] T012 [P] [US1] Add cache read tests in `tests/test_library_cache.py`

**Checkpoint**: At this point, User Story 1 should be fully functional - cached data loads instantly

---

## Phase 4: User Story 2 - First-Time Library Sync (Priority: P1)

**Goal**: New users see a blocking sync modal with progress during first-time setup

**Independent Test**: Delete cache database, reload app, verify blocking modal with progress appears

### Implementation for User Story 2

- [X] T013 [US2] Implement `sync_library()` async function with batch fetching in `backend/library_cache.py`
- [X] T014 [US2] Implement progress tracking (in-memory state) in `backend/library_cache.py`
- [X] T015 [US2] Implement `is_live_version()` computation during sync in `backend/library_cache.py`
- [X] T016 [US2] Add POST `/api/library/sync` endpoint in `backend/main.py`
- [X] T017 [US2] Add sync progress modal component in `frontend/index.html`
- [X] T018 [US2] Implement sync progress polling (1-second interval) in `frontend/app.js`
- [X] T019 [US2] Style sync modal with Plexamp aesthetic in `frontend/style.css`
- [X] T020 [US2] Add blocking behavior detection (empty cache → show modal) in `frontend/app.js`
- [ ] T021 [P] [US2] Add sync tests (success, progress tracking) in `tests/test_library_cache.py`

**Checkpoint**: At this point, User Stories 1 AND 2 should both work - new users can sync, returning users load instantly

---

## Phase 5: User Story 3 - Manual Library Refresh (Priority: P2)

**Goal**: Users can click a refresh link to trigger background sync

**Independent Test**: Click refresh link, verify background sync starts without blocking UI

### Implementation for User Story 3

- [X] T022 [US3] Implement background sync (asyncio.create_task) in `backend/library_cache.py`
- [X] T023 [US3] Add "already syncing" detection (409 response) in `backend/main.py`
- [X] T024 [US3] Add refresh link to footer in `frontend/index.html`
- [X] T025 [US3] Implement refresh click handler (POST /api/library/sync) in `frontend/app.js`
- [X] T026 [US3] Show "Syncing..." state in footer during background sync in `frontend/app.js`

**Checkpoint**: Manual refresh works without interrupting user workflow

---

## Phase 6: User Story 4 - Sync Status Visibility (Priority: P2)

**Goal**: Users see track count and last sync time in the footer

**Independent Test**: After any sync, verify footer shows accurate track count and relative time

### Implementation for User Story 4

- [X] T027 [US4] Add footer status section (track count, sync time, refresh link) in `frontend/index.html`
- [X] T028 [US4] Implement relative time formatting ("2 hours ago") in `frontend/app.js`
- [X] T029 [US4] Poll `/api/library/status` on page load and after sync in `frontend/app.js`
- [X] T030 [US4] Style footer status with Plexamp aesthetic in `frontend/style.css`
- [X] T031 [US4] Handle error state display ("Sync failed · Retry") in `frontend/app.js`

**Checkpoint**: Users always know cache status at a glance

---

## Phase 7: User Story 5 - Automatic Background Refresh (Priority: P3)

**Goal**: App auto-syncs when cache is >24 hours old

**Independent Test**: Set last_sync_at to >24 hours ago, reload app, verify background sync triggers

### Implementation for User Story 5

- [ ] T032 [US5] Implement staleness check (>24 hours) in `backend/library_cache.py`
- [ ] T033 [US5] Add startup sync hook to check staleness on app load in `backend/main.py`
- [ ] T034 [US5] Trigger background sync from frontend if stale in `frontend/app.js`
- [ ] T035 [P] [US5] Add staleness check tests in `tests/test_library_cache.py`

**Checkpoint**: Users get fresh data automatically without manual intervention

---

## Phase 8: User Story 6 - Plex Server Change Detection (Priority: P3)

**Goal**: Switching Plex servers clears old cache and re-syncs

**Independent Test**: Change Plex server config, verify old cache cleared and new sync begins

### Implementation for User Story 6

- [ ] T036 [US6] Implement server ID comparison in `backend/library_cache.py`
- [ ] T037 [US6] Implement `clear_cache()` function in `backend/library_cache.py`
- [ ] T038 [US6] Add server change detection to sync endpoint in `backend/main.py`
- [ ] T039 [P] [US6] Add server change detection tests in `tests/test_library_cache.py`

**Checkpoint**: Users can safely switch Plex servers without data integrity issues

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Error handling, edge cases, and documentation

- [ ] T040 [P] Add error handling for sync failures (transaction rollback, preserve existing data) in `backend/library_cache.py`
- [ ] T041 [P] Add timeout and retry logic for batch fetches in `backend/library_cache.py`
- [ ] T042 [P] Handle Plex offline gracefully (use cache, show warning) in `backend/main.py`
- [ ] T043 [P] Add sync endpoint tests in `tests/test_api.py`
- [ ] T044 Update quickstart.md with manual testing instructions
- [ ] T045 Run full manual test per quickstart.md validation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-8)**: All depend on Foundational phase completion
- **Polish (Phase 9)**: Depends on core user stories (US1-US4) being complete

### User Story Dependencies

- **User Story 1 (P1)**: Foundational only - reads from cache
- **User Story 2 (P1)**: Foundational only - writes to cache (creates it)
- **User Story 3 (P2)**: Depends on US2 (sync infrastructure)
- **User Story 4 (P2)**: Depends on US1 (status endpoint)
- **User Story 5 (P3)**: Depends on US2 (sync) and US4 (status display)
- **User Story 6 (P3)**: Depends on US2 (sync infrastructure)

### Parallel Opportunities

Within each user story, tasks marked [P] can run in parallel:
- T002, T006: Models and test setup
- T012, T021, T035, T039: Test files (different test modules)
- T040, T041, T042, T043: Polish tasks (different concerns)

---

## Parallel Example: Foundational Phase

```bash
# These can run in parallel after T003-T005 complete:
Task: "Create tests/test_library_cache.py with schema creation tests"
```

## Parallel Example: User Story 2

```bash
# After T013-T016 (backend complete), frontend tasks can parallelize:
Task: "Add sync progress modal component in frontend/index.html"
Task: "Style sync modal with Plexamp aesthetic in frontend/style.css"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (fast cache reads)
4. Complete Phase 4: User Story 2 (initial sync)
5. **STOP and VALIDATE**: Test both stories together
6. Deploy/demo: Users can sync and use cached data

### Incremental Delivery

1. **MVP**: Setup + Foundation + US1 + US2 → Core value delivered
2. **v1.1**: Add US3 + US4 → Manual refresh and status visibility
3. **v1.2**: Add US5 + US6 → Automatic refresh and server change detection
4. **v1.3**: Polish phase → Error handling and edge cases

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- US1 and US2 are both P1 - implement together for MVP
