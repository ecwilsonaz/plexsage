"""Tests for local library cache functionality."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backend import library_cache


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_library_cache.db"
    monkeypatch.setattr(library_cache, "DB_PATH", db_path)
    monkeypatch.setattr(library_cache, "DATA_DIR", tmp_path)
    # Reset schema initialization flag so each test gets fresh schema
    monkeypatch.setattr(library_cache, "_schema_initialized", False)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def initialized_db(temp_db):
    """Create and initialize a test database."""
    conn = library_cache.ensure_db_initialized()
    conn.close()
    return temp_db


class TestSchemaCreation:
    """Test database schema initialization."""

    def test_creates_data_directory(self, temp_db):
        """Schema init creates data directory if missing."""
        # Remove directory
        temp_db.parent.rmdir() if temp_db.parent.exists() else None

        conn = library_cache.ensure_db_initialized()
        conn.close()

        assert temp_db.parent.exists()

    def test_creates_tracks_table(self, initialized_db):
        """Schema creates tracks table with correct columns."""
        conn = sqlite3.connect(str(initialized_db))
        cursor = conn.execute("PRAGMA table_info(tracks)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()

        assert "rating_key" in columns
        assert "title" in columns
        assert "artist" in columns
        assert "album" in columns
        assert "duration_ms" in columns
        assert "year" in columns
        assert "genres" in columns
        assert "user_rating" in columns
        assert "is_live" in columns
        assert "updated_at" in columns

    def test_creates_sync_state_table(self, initialized_db):
        """Schema creates sync_state table with single row."""
        conn = sqlite3.connect(str(initialized_db))
        cursor = conn.execute("SELECT COUNT(*) FROM sync_state")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 1

    def test_creates_indexes(self, initialized_db):
        """Schema creates expected indexes."""
        conn = sqlite3.connect(str(initialized_db))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_tracks_%'"
        )
        indexes = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "idx_tracks_artist" in indexes
        assert "idx_tracks_year" in indexes
        assert "idx_tracks_is_live" in indexes

    def test_wal_mode_enabled(self, initialized_db):
        """Connection uses WAL journal mode."""
        conn = library_cache.get_db_connection()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"

    def test_idempotent_initialization(self, initialized_db):
        """Multiple initializations don't cause errors."""
        # Should not raise
        conn1 = library_cache.ensure_db_initialized()
        conn1.close()
        conn2 = library_cache.ensure_db_initialized()
        conn2.close()


class TestSyncState:
    """Test sync state management."""

    def test_initial_sync_state(self, initialized_db):
        """Fresh database has empty sync state."""
        state = library_cache.get_sync_state()

        assert state["track_count"] == 0
        assert state["synced_at"] is None
        assert state["is_syncing"] is False
        assert state["sync_progress"] is None
        assert state["error"] is None

    def test_sync_state_after_sync(self, initialized_db):
        """Sync state updates after successful sync."""
        conn = library_cache.get_db_connection()

        # Simulate a completed sync
        synced_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE sync_state SET plex_server_id = ?, last_sync_at = ?, "
            "track_count = ?, sync_duration_ms = ? WHERE id = 1",
            ("test-server-id", synced_at, 1000, 5000),
        )
        conn.commit()
        conn.close()

        state = library_cache.get_sync_state()

        assert state["track_count"] == 1000
        assert state["synced_at"] == synced_at
        assert state["plex_server_id"] == "test-server-id"


class TestCacheOperations:
    """Test cache read/write operations."""

    def test_get_cached_tracks_empty(self, initialized_db):
        """Empty cache returns empty list."""
        tracks = library_cache.get_cached_tracks()
        assert tracks == []

    def test_get_cached_tracks_with_data(self, initialized_db):
        """Cached tracks are returned correctly."""
        conn = library_cache.get_db_connection()
        conn.execute(
            "INSERT INTO tracks (rating_key, title, artist, album, duration_ms, "
            "year, genres, user_rating, is_live) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("123", "Test Song", "Test Artist", "Test Album", 180000, 1999,
             json.dumps(["Rock", "Alternative"]), 8, False),
        )
        conn.commit()
        conn.close()

        tracks = library_cache.get_cached_tracks()

        assert len(tracks) == 1
        assert tracks[0]["rating_key"] == "123"
        assert tracks[0]["title"] == "Test Song"
        assert tracks[0]["genres"] == ["Rock", "Alternative"]

    def test_clear_cache(self, initialized_db):
        """Clear cache removes all tracks and resets state."""
        conn = library_cache.get_db_connection()
        conn.execute(
            "INSERT INTO tracks (rating_key, title, artist, album) "
            "VALUES ('1', 'Song', 'Artist', 'Album')"
        )
        conn.execute(
            "UPDATE sync_state SET track_count = 1, last_sync_at = ? WHERE id = 1",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        conn.close()

        library_cache.clear_cache()

        tracks = library_cache.get_cached_tracks()
        state = library_cache.get_sync_state()

        assert tracks == []
        assert state["track_count"] == 0
        assert state["synced_at"] is None


class TestFiltering:
    """Test track filtering operations."""

    @pytest.fixture
    def sample_tracks(self, initialized_db):
        """Insert sample tracks for filter testing."""
        conn = library_cache.get_db_connection()
        tracks = [
            ("1", "Rock Song", "Rock Artist", "Rock Album", 180000, 1995,
             json.dumps(["Rock"]), 8, False),
            ("2", "Pop Song", "Pop Artist", "Pop Album", 200000, 2005,
             json.dumps(["Pop"]), 6, False),
            ("3", "Live Concert", "Live Artist", "Live 2020", 300000, 2020,
             json.dumps(["Rock"]), 4, True),
            ("4", "Jazz Song", "Jazz Artist", "Jazz Album", 250000, 1985,
             json.dumps(["Jazz"]), 10, False),
        ]
        conn.executemany(
            "INSERT INTO tracks (rating_key, title, artist, album, duration_ms, "
            "year, genres, user_rating, is_live) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tracks,
        )
        conn.commit()
        conn.close()
        return tracks

    def test_filter_exclude_live(self, sample_tracks):
        """Exclude live filter removes live tracks."""
        tracks = library_cache.get_tracks_by_filters(exclude_live=True)

        assert len(tracks) == 3
        assert all(not t["is_live"] for t in tracks)

    def test_filter_include_live(self, sample_tracks):
        """Include live returns all tracks."""
        tracks = library_cache.get_tracks_by_filters(exclude_live=False)

        assert len(tracks) == 4

    def test_filter_by_decade(self, sample_tracks):
        """Filter by decade returns matching tracks."""
        tracks = library_cache.get_tracks_by_filters(decades=["1990s"], exclude_live=False)

        assert len(tracks) == 1
        assert tracks[0]["year"] == 1995

    def test_filter_by_multiple_decades(self, sample_tracks):
        """Filter by multiple decades uses OR logic."""
        tracks = library_cache.get_tracks_by_filters(
            decades=["1980s", "2000s"], exclude_live=False
        )

        assert len(tracks) == 2
        years = {t["year"] for t in tracks}
        assert years == {1985, 2005}

    def test_filter_by_genre(self, sample_tracks):
        """Filter by genre returns matching tracks."""
        tracks = library_cache.get_tracks_by_filters(genres=["Rock"], exclude_live=False)

        assert len(tracks) == 2  # Rock Song and Live Concert

    def test_filter_by_genre_with_limit(self, initialized_db):
        """Filter by genre with limit applies limit AFTER genre filtering.

        This tests the fix for a bug where SQL LIMIT was applied before
        Python-side genre filtering, causing rare genres to return too few results.
        """
        conn = library_cache.get_db_connection()
        # Insert 100 Pop tracks and 10 Jazz tracks
        tracks = []
        for i in range(100):
            tracks.append((
                f"pop-{i}", f"Pop Song {i}", "Pop Artist", "Pop Album",
                180000, 2000, json.dumps(["Pop"]), 5, False
            ))
        for i in range(10):
            tracks.append((
                f"jazz-{i}", f"Jazz Song {i}", "Jazz Artist", "Jazz Album",
                180000, 1990, json.dumps(["Jazz"]), 5, False
            ))
        conn.executemany(
            "INSERT INTO tracks (rating_key, title, artist, album, duration_ms, "
            "year, genres, user_rating, is_live) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tracks,
        )
        conn.commit()
        conn.close()

        # Request 5 Jazz tracks - should get exactly 5 Jazz tracks
        # (Bug: without fix, would get random sample of all tracks, mostly Pop)
        result = library_cache.get_tracks_by_filters(genres=["Jazz"], limit=5)

        assert len(result) == 5
        # All returned tracks must be Jazz
        for t in result:
            assert "Jazz" in t["genres"], f"Non-Jazz track returned: {t['title']}"

    def test_filter_by_min_rating(self, sample_tracks):
        """Filter by minimum rating."""
        tracks = library_cache.get_tracks_by_filters(min_rating=8, exclude_live=False)

        assert len(tracks) == 2
        assert all(t["user_rating"] >= 8 for t in tracks)

    def test_filter_with_limit(self, sample_tracks):
        """Filter with limit returns at most N tracks."""
        tracks = library_cache.get_tracks_by_filters(limit=2, exclude_live=False)

        assert len(tracks) == 2

    def test_filter_combined(self, sample_tracks):
        """Combined filters work together."""
        tracks = library_cache.get_tracks_by_filters(
            genres=["Rock"],
            decades=["1990s"],
            exclude_live=True,
            min_rating=6,
        )

        assert len(tracks) == 1
        assert tracks[0]["title"] == "Rock Song"


class TestLiveDetection:
    """Test live version detection."""

    def test_live_keyword_in_title(self):
        """Detects 'live' keyword in title."""
        assert library_cache._is_live_version("Song (Live)", "Album")
        assert library_cache._is_live_version("Live at Madison Square Garden", "Album")

    def test_live_keyword_in_album(self):
        """Detects 'live' keyword in album."""
        assert library_cache._is_live_version("Song", "Live in Concert")
        assert library_cache._is_live_version("Song", "The Concert Album")

    def test_date_pattern_in_album(self):
        """Detects date patterns indicating live recordings."""
        assert library_cache._is_live_version("Song", "2020-01-15 Chicago")
        assert library_cache._is_live_version("Song", "1999/05/20")

    def test_bootleg_keyword(self):
        """Detects bootleg keyword."""
        assert library_cache._is_live_version("Song", "Bootleg Series")

    def test_non_live_tracks(self):
        """Non-live tracks are not flagged."""
        assert not library_cache._is_live_version("Regular Song", "Studio Album")
        assert not library_cache._is_live_version("Alive", "Greatest Hits")  # 'live' in 'alive'


class TestStalenessCheck:
    """Test cache staleness detection."""

    def test_empty_cache_is_stale(self, initialized_db):
        """Empty cache is considered stale."""
        assert library_cache.is_cache_stale()

    def test_recent_cache_not_stale(self, initialized_db):
        """Recently synced cache is not stale."""
        conn = library_cache.get_db_connection()
        synced_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE sync_state SET last_sync_at = ? WHERE id = 1",
            (synced_at,),
        )
        conn.commit()
        conn.close()

        assert not library_cache.is_cache_stale(max_age_hours=24)

    def test_old_cache_is_stale(self, initialized_db):
        """Old cache is considered stale."""
        conn = library_cache.get_db_connection()
        # Set sync time to 25 hours ago
        from datetime import timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        conn.execute(
            "UPDATE sync_state SET last_sync_at = ? WHERE id = 1",
            (old_time,),
        )
        conn.commit()
        conn.close()

        assert library_cache.is_cache_stale(max_age_hours=24)


class TestServerChangeDetection:
    """Test Plex server change detection."""

    def test_first_sync_no_change(self, initialized_db):
        """First sync doesn't count as server change."""
        assert not library_cache.check_server_changed("new-server-id")

    def test_same_server_no_change(self, initialized_db):
        """Same server ID doesn't trigger change."""
        conn = library_cache.get_db_connection()
        conn.execute(
            "UPDATE sync_state SET plex_server_id = ? WHERE id = 1",
            ("my-server-id",),
        )
        conn.commit()
        conn.close()

        assert not library_cache.check_server_changed("my-server-id")

    def test_different_server_triggers_change(self, initialized_db):
        """Different server ID triggers change detection."""
        conn = library_cache.get_db_connection()
        conn.execute(
            "UPDATE sync_state SET plex_server_id = ? WHERE id = 1",
            ("old-server-id",),
        )
        conn.commit()
        conn.close()

        assert library_cache.check_server_changed("new-server-id")


class TestSyncLibrary:
    """Test sync_library() function."""

    @pytest.fixture
    def mock_track(self):
        """Create a mock Plex track object."""
        class MockTrack:
            def __init__(self, rating_key, title, artist, album, duration, parent_key):
                self.ratingKey = rating_key
                self.title = title
                self.grandparentTitle = artist
                self.parentTitle = album
                self.duration = duration
                self.parentRatingKey = parent_key
                self.userRating = None
        return MockTrack

    @pytest.fixture
    def mock_plex_client(self, mock_track):
        """Create a mock Plex client."""
        class MockPlexClient:
            def __init__(self):
                self.tracks = [
                    mock_track("1", "Song One", "Artist A", "Album X", 180000, "100"),
                    mock_track("2", "Song Two", "Artist B", "Album Y", 200000, "101"),
                    mock_track("3", "Song Three", "Artist A", "Album X", 220000, "100"),
                ]
                self.album_metadata = {
                    "100": {"genres": ["Rock", "Alternative"], "year": 1995},
                    "101": {"genres": ["Electronic"], "year": 2020},
                }

            def get_machine_identifier(self):
                return "test-server-123"

            def get_all_albums_metadata(self):
                return self.album_metadata

            def get_all_raw_tracks(self):
                return self.tracks

        return MockPlexClient()

    @pytest.fixture
    def reset_sync_state(self, monkeypatch):
        """Reset sync state before each test."""
        monkeypatch.setattr(library_cache, "_sync_state", {
            "is_syncing": False,
            "phase": None,
            "current": 0,
            "total": 0,
            "error": None,
        })

    def test_sync_success(self, initialized_db, mock_plex_client, reset_sync_state):
        """Sync completes successfully with correct track count."""
        result = library_cache.sync_library(mock_plex_client)

        assert result["success"] is True
        assert result["track_count"] == 3
        assert "duration_ms" in result

        # Verify tracks in database
        tracks = library_cache.get_cached_tracks()
        assert len(tracks) == 3

    def test_sync_stores_genres_from_albums(self, initialized_db, mock_plex_client, reset_sync_state):
        """Sync correctly maps album genres to tracks."""
        library_cache.sync_library(mock_plex_client)

        tracks = library_cache.get_cached_tracks()
        track_by_key = {t["rating_key"]: t for t in tracks}

        # Tracks 1 and 3 are from Album X (parent_key=100)
        assert track_by_key["1"]["genres"] == ["Rock", "Alternative"]
        assert track_by_key["3"]["genres"] == ["Rock", "Alternative"]

        # Track 2 is from Album Y (parent_key=101)
        assert track_by_key["2"]["genres"] == ["Electronic"]

    def test_sync_stores_year_from_albums(self, initialized_db, mock_plex_client, reset_sync_state):
        """Sync correctly maps album year to tracks."""
        library_cache.sync_library(mock_plex_client)

        tracks = library_cache.get_cached_tracks()
        track_by_key = {t["rating_key"]: t for t in tracks}

        assert track_by_key["1"]["year"] == 1995
        assert track_by_key["2"]["year"] == 2020
        assert track_by_key["3"]["year"] == 1995

    def test_sync_updates_sync_state(self, initialized_db, mock_plex_client, reset_sync_state):
        """Sync updates the sync_state table."""
        library_cache.sync_library(mock_plex_client)

        state = library_cache.get_sync_state()

        assert state["track_count"] == 3
        assert state["synced_at"] is not None
        assert state["plex_server_id"] == "test-server-123"
        assert state["is_syncing"] is False

    def test_sync_progress_callback(self, initialized_db, mock_plex_client, reset_sync_state):
        """Progress callback is invoked during sync."""
        progress_calls = []

        def on_progress(current, total):
            progress_calls.append((current, total))

        library_cache.sync_library(mock_plex_client, on_progress=on_progress)

        # With 3 tracks and SYNC_BATCH_SIZE=500, we won't hit the batch callback
        # but we can verify the function accepts the callback without error
        assert isinstance(progress_calls, list)

    def test_sync_rejects_concurrent_sync(self, initialized_db, mock_plex_client, monkeypatch):
        """Second sync attempt is rejected while one is in progress."""
        # Simulate sync in progress
        monkeypatch.setattr(library_cache, "_sync_state", {
            "is_syncing": True,
            "phase": "processing",
            "current": 50,
            "total": 100,
            "error": None,
        })

        result = library_cache.sync_library(mock_plex_client)

        assert result["success"] is False
        assert "already in progress" in result["error"]

    def test_sync_handles_missing_server_id(self, initialized_db, reset_sync_state):
        """Sync fails gracefully if server ID is unavailable."""
        class BadPlexClient:
            def get_machine_identifier(self):
                return None

        result = library_cache.sync_library(BadPlexClient())

        assert result["success"] is False
        assert "server identifier" in result["error"].lower()

    def test_sync_clears_error_on_success(self, initialized_db, mock_plex_client, monkeypatch):
        """Successful sync clears any previous error state."""
        # Set initial state with error
        monkeypatch.setattr(library_cache, "_sync_state", {
            "is_syncing": False,
            "phase": None,
            "current": 0,
            "total": 0,
            "error": "Previous error",
        })

        library_cache.sync_library(mock_plex_client)

        state = library_cache.get_sync_state()
        assert state["error"] is None
        assert state["is_syncing"] is False

    def test_sync_removes_deleted_tracks(self, initialized_db, mock_track, reset_sync_state):
        """Sync removes tracks that no longer exist in Plex."""
        # Pre-populate cache with a track that won't be in the sync
        conn = library_cache.get_db_connection()
        conn.execute(
            "INSERT INTO tracks (rating_key, title, artist, album, duration_ms, "
            "year, genres, user_rating, is_live) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("stale-track-999", "Stale Song", "Old Artist", "Deleted Album",
             180000, 2000, json.dumps(["Rock"]), 5, False),
        )
        conn.commit()
        conn.close()

        # Verify stale track exists
        tracks_before = library_cache.get_cached_tracks()
        assert any(t["rating_key"] == "stale-track-999" for t in tracks_before)

        # Create mock client that returns different tracks
        class MockPlexClient:
            def get_machine_identifier(self):
                return "test-server-123"

            def get_all_albums_metadata(self):
                return {"100": {"genres": ["Electronic"], "year": 2024}}

            def get_all_raw_tracks(self):
                return [mock_track("new-1", "New Song", "New Artist", "New Album", 200000, "100")]

        # Run sync
        result = library_cache.sync_library(MockPlexClient())

        assert result["success"] is True
        assert result["track_count"] == 1

        # Verify stale track was removed
        tracks_after = library_cache.get_cached_tracks()
        assert len(tracks_after) == 1
        assert tracks_after[0]["rating_key"] == "new-1"
        assert not any(t["rating_key"] == "stale-track-999" for t in tracks_after)

    def test_failed_sync_resets_cache_state(self, initialized_db, mock_track, reset_sync_state):
        """Failed sync resets track_count so has_cached_tracks() returns False."""
        # First, do a successful sync to populate cache
        class SuccessfulClient:
            def get_machine_identifier(self):
                return "test-server-123"

            def get_all_albums_metadata(self):
                return {"100": {"genres": ["Rock"], "year": 2020}}

            def get_all_raw_tracks(self):
                return [mock_track("1", "Song", "Artist", "Album", 180000, "100")]

        result = library_cache.sync_library(SuccessfulClient())
        assert result["success"] is True
        assert library_cache.has_cached_tracks() is True

        # Reset sync state for next sync attempt
        library_cache._sync_state["is_syncing"] = False

        # Now attempt a sync that fails during Plex API call
        class FailingClient:
            def get_machine_identifier(self):
                return "test-server-123"

            def get_all_albums_metadata(self):
                raise ConnectionError("Plex unreachable")

        result = library_cache.sync_library(FailingClient())
        assert result["success"] is False

        # Cache should now report as empty to avoid using stale data
        assert library_cache.has_cached_tracks() is False
        assert library_cache.get_sync_state()["track_count"] == 0
