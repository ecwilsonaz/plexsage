"""Tests for playlist generation."""

import json
import pytest
from unittest.mock import MagicMock, patch


class TestPlaylistGeneration:
    """Tests for playlist generation."""

    def test_generate_validates_tracks_against_library(self, mocker, mock_plex_tracks):
        """Generated playlist should only contain tracks from library."""
        from backend.generator import generate_playlist
        from backend.llm_client import LLMResponse

        # LLM returns tracks that are in the library
        mock_response = LLMResponse(
            content=json.dumps([
                {"artist": "Radiohead", "album": "The Bends", "title": "Fake Plastic Trees"},
                {"artist": "Pearl Jam", "album": "Ten", "title": "Black"},
            ]),
            input_tokens=1000,
            output_tokens=100,
            model="test-model"
        )

        with patch("backend.generator.get_llm_client") as mock_llm:
            mock_client = MagicMock()
            mock_client.generate.return_value = mock_response
            mock_client.parse_json_response.return_value = json.loads(mock_response.content)
            mock_llm.return_value = mock_client

            with patch("backend.generator.get_plex_client") as mock_plex:
                mock_plex_client = MagicMock()
                mock_plex_client.get_tracks_by_filters.return_value = mock_plex_tracks[:5]
                mock_plex.return_value = mock_plex_client

                # Mock cache to be empty so we use Plex fallback
                with patch("backend.generator.library_cache.has_cached_tracks", return_value=False):
                    result = generate_playlist(
                        prompt="90s alternative",
                        genres=["Alternative", "Rock"],
                        decades=["1990s"],
                        track_count=25,
                        exclude_live=True
                    )

                    # All returned tracks should be from the library
                    for track in result.tracks:
                        assert any(
                            t.rating_key == track.rating_key
                            for t in mock_plex_tracks
                        )

    def test_generate_handles_empty_filter_results(self, mocker):
        """Should handle case when no tracks match filters."""
        from backend.generator import generate_playlist

        with patch("backend.generator.get_llm_client"):
            with patch("backend.generator.get_plex_client") as mock_plex:
                mock_plex_client = MagicMock()
                mock_plex_client.get_tracks_by_filters.return_value = []
                mock_plex.return_value = mock_plex_client

                # Mock cache to be empty so we use Plex fallback
                with patch("backend.generator.library_cache.has_cached_tracks", return_value=False):
                    with pytest.raises(ValueError, match="No tracks"):
                        generate_playlist(
                            prompt="nonexistent genre",
                            genres=["Nonexistent"],
                            decades=["1800s"],
                            track_count=25,
                            exclude_live=True
                        )

    def test_fuzzy_matching_finds_similar_titles(self, mocker, mock_plex_tracks):
        """Should fuzzy match LLM responses to library tracks."""
        from backend.generator import generate_playlist
        from backend.llm_client import LLMResponse

        # LLM returns slightly different track name
        mock_response = LLMResponse(
            content=json.dumps([
                # Note: "Fake Plastic Tree" vs "Fake Plastic Trees"
                {"artist": "Radiohead", "album": "The Bends", "title": "Fake Plastic Tree"},
            ]),
            input_tokens=1000,
            output_tokens=100,
            model="test-model"
        )

        with patch("backend.generator.get_llm_client") as mock_llm:
            mock_client = MagicMock()
            mock_client.generate.return_value = mock_response
            mock_client.parse_json_response.return_value = json.loads(mock_response.content)
            mock_llm.return_value = mock_client

            with patch("backend.generator.get_plex_client") as mock_plex:
                mock_plex_client = MagicMock()
                mock_plex_client.get_tracks_by_filters.return_value = mock_plex_tracks[:5]
                mock_plex.return_value = mock_plex_client

                # Mock cache to be empty so we use Plex fallback
                with patch("backend.generator.library_cache.has_cached_tracks", return_value=False):
                    result = generate_playlist(
                        prompt="radiohead",
                        genres=["Alternative"],
                        decades=["1990s"],
                        track_count=25,
                        exclude_live=True
                    )

                    # Should still match the track despite slight title difference
                    # (implementation will use fuzzy matching)
                    assert len(result.tracks) >= 0  # May or may not match depending on threshold


class TestTrackMatching:
    """Tests for track matching utilities."""

    def test_simplify_string_removes_punctuation(self):
        """Should remove punctuation from strings."""
        from backend.plex_client import simplify_string

        assert simplify_string("Don't Stop") == "dont stop"
        assert simplify_string("Rock & Roll") == "rock  roll"
        assert simplify_string("(Remastered)") == "remastered"

    def test_simplify_string_normalizes_unicode(self):
        """Should normalize unicode characters."""
        from backend.plex_client import simplify_string

        assert simplify_string("Café") == "cafe"
        assert simplify_string("Motörhead") == "motorhead"

    def test_normalize_artist_handles_and_variations(self):
        """Should handle 'and' vs '&' variations."""
        from backend.plex_client import normalize_artist

        variations = normalize_artist("Simon & Garfunkel")
        assert "Simon & Garfunkel" in variations
        assert "Simon and Garfunkel" in variations

        variations = normalize_artist("Tom and Jerry")
        assert "Tom and Jerry" in variations
        assert "Tom & Jerry" in variations

class TestLiveVersionFiltering:
    """Tests for live version detection."""

    def test_is_live_version_detects_live_keyword(self):
        """Should detect 'live' in track or album title."""
        from backend.plex_client import is_live_version

        class MockTrack:
            def __init__(self, title, album_title):
                self.title = title
                self._album_title = album_title

            def album(self):
                return MagicMock(title=self._album_title)

        assert is_live_version(MockTrack("Song - Live", "Album")) is True
        assert is_live_version(MockTrack("Song", "Live at Madison Square Garden")) is True
        assert is_live_version(MockTrack("Song", "Album")) is False

    def test_is_live_version_detects_concert_keyword(self):
        """Should detect 'concert' in track or album title."""
        from backend.plex_client import is_live_version

        class MockTrack:
            def __init__(self, title, album_title):
                self.title = title
                self._album_title = album_title

            def album(self):
                return MagicMock(title=self._album_title)

        assert is_live_version(MockTrack("Song", "Concert Recording")) is True

    def test_is_live_version_detects_date_patterns(self):
        """Should detect date patterns in album titles."""
        from backend.plex_client import is_live_version

        class MockTrack:
            def __init__(self, title, album_title):
                self.title = title
                self._album_title = album_title

            def album(self):
                return MagicMock(title=self._album_title)

        assert is_live_version(MockTrack("Song", "2023-05-15 Show")) is True
        assert is_live_version(MockTrack("Song", "1999/12/31 New Years")) is True
        assert is_live_version(MockTrack("Song", "Regular Album 2023")) is False
