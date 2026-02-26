"""Jellyfin media server client using REST API via httpx."""

import logging
import random
from typing import Any

import httpx

from backend.media_client import BaseMediaClient, is_live_track
from backend.models import PlexPlaylistInfo, Track

logger = logging.getLogger(__name__)

# Singleton instance
_jellyfin_client: "JellyfinClient | None" = None


def _decade_to_years(decade: str) -> list[int]:
    """Convert a decade string like '1980s' to a list of years [1980..1989]."""
    decade = decade.strip().rstrip("s")
    try:
        start = int(decade)
        return list(range(start, start + 10))
    except ValueError:
        return []


class JellyfinClient(BaseMediaClient):
    """Client for interacting with Jellyfin media server via REST API."""

    def __init__(self, url: str, token: str, music_library: str = "Music"):
        self.url = url.rstrip("/")
        self.token = token
        self.music_library_name = music_library

        self._connected = False
        self._error: str | None = None
        self._user_id: str | None = None
        self._library_id: str | None = None

        self._headers = {
            "Authorization": (
                f'MediaBrowser Client="MediaSage", Device="Server", '
                f'DeviceId="mediasage-server", Version="1.0.0", Token="{token}"'
            ),
            "Content-Type": "application/json",
        }

        self._connect()

    def _connect(self) -> None:
        """Attempt to connect and fetch user + library IDs."""
        if not self.url or not self.token:
            self._error = "Jellyfin URL and token are required"
            return

        try:
            with httpx.Client(headers=self._headers, timeout=15.0) as client:
                # API keys aren't tied to a specific user, so /Users/Me returns 400.
                # Instead, list all users and pick the first administrator.
                resp = client.get(f"{self.url}/Users")
                resp.raise_for_status()
                users = resp.json()
                admin = next((u for u in users if u.get("Policy", {}).get("IsAdministrator")), None)
                if admin:
                    self._user_id = admin["Id"]
                elif users:
                    self._user_id = users[0]["Id"]
                else:
                    self._error = "No users found in Jellyfin"
                    self._connected = False
                    return

                # Find the music library
                resp = client.get(f"{self.url}/Library/MediaFolders")
                resp.raise_for_status()
                folders = resp.json().get("Items", [])

                # Look for a library matching the configured name
                self._library_id = None
                for folder in folders:
                    if folder.get("Name", "").lower() == self.music_library_name.lower():
                        self._library_id = folder["Id"]
                        break

                # If not found by exact name, take first music collection type
                if not self._library_id:
                    for folder in folders:
                        if folder.get("CollectionType") == "music":
                            self._library_id = folder["Id"]
                            break

                if not self._library_id:
                    self._error = f"Music library '{self.music_library_name}' not found in Jellyfin"
                    self._connected = False
                    return

                self._connected = True
                self._error = None
                logger.info(
                    "Connected to Jellyfin: user_id=%s library_id=%s",
                    self._user_id,
                    self._library_id,
                )

        except httpx.ConnectError:
            self._error = f"Cannot connect to Jellyfin server at {self.url}"
            self._connected = False
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 401:
                self._error = "Invalid Jellyfin API key — unauthorized"
            else:
                self._error = f"Jellyfin returned HTTP {status}"
            self._connected = False
        except Exception as e:
            self._error = f"Jellyfin connection error: {str(e)}"
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_error(self) -> str | None:
        return self._error

    def get_music_libraries(self) -> list[str]:
        """Get list of music library names from Jellyfin."""
        if not self.url or not self.token:
            return []
        try:
            with httpx.Client(headers=self._headers, timeout=10.0) as client:
                resp = client.get(f"{self.url}/Library/MediaFolders")
                resp.raise_for_status()
                folders = resp.json().get("Items", [])
                return [
                    f["Name"]
                    for f in folders
                    if f.get("CollectionType") == "music"
                ]
        except Exception as e:
            logger.warning("Failed to get Jellyfin music libraries: %s", e)
            return []

    def get_library_stats(self) -> dict[str, Any]:
        """Get statistics about the Jellyfin music library."""
        if not self._connected or not self._library_id:
            return {"total_tracks": 0, "genres": [], "decades": []}

        try:
            with httpx.Client(headers=self._headers, timeout=30.0) as client:
                # Total track count
                resp = client.get(
                    f"{self.url}/Items",
                    params={
                        "IncludeItemTypes": "Audio",
                        "Recursive": "true",
                        "ParentId": self._library_id,
                        "Limit": 0,
                    },
                )
                resp.raise_for_status()
                total_tracks = resp.json().get("TotalRecordCount", 0)

                # Genres
                resp = client.get(
                    f"{self.url}/Genres",
                    params={
                        "IncludeItemTypes": "Audio",
                        "Recursive": "true",
                        "ParentId": self._library_id,
                    },
                )
                resp.raise_for_status()
                genre_items = resp.json().get("Items", [])
                genres = sorted(
                    [{"name": g["Name"], "count": None} for g in genre_items],
                    key=lambda x: x["name"],
                )

                # Decades: derive from tracks' production years
                resp = client.get(
                    f"{self.url}/Items",
                    params={
                        "IncludeItemTypes": "Audio",
                        "Recursive": "true",
                        "ParentId": self._library_id,
                        "Fields": "ProductionYear",
                        "Limit": 50000,
                    },
                )
                resp.raise_for_status()
                items = resp.json().get("Items", [])
                decade_set: set[str] = set()
                for item in items:
                    year = item.get("ProductionYear")
                    if year:
                        decade = f"{(year // 10) * 10}s"
                        decade_set.add(decade)
                decades = sorted([{"name": d, "count": None} for d in decade_set],
                                 key=lambda x: x["name"])

                return {
                    "total_tracks": total_tracks,
                    "genres": genres,
                    "decades": decades,
                }
        except Exception as e:
            logger.exception("Failed to get Jellyfin library stats: %s", e)
            return {"total_tracks": 0, "genres": [], "decades": [], "error": str(e)}

    def _fields_param(self) -> str:
        """Standard Fields parameter for all item fetches."""
        return "Genres,ProductionYear,RunTimeTicks,AlbumArtist,Album,Artists"

    def _item_to_track(self, item: dict) -> Track:
        """Convert a Jellyfin item dict to a Track model."""
        item_id = item["Id"]
        duration_ticks = item.get("RunTimeTicks") or 0
        duration_ms = duration_ticks // 10000

        return Track(
            rating_key=item_id,
            title=item.get("Name", ""),
            artist=item.get("AlbumArtist") or (item.get("Artists") or [""])[0],
            album=item.get("Album", ""),
            duration_ms=duration_ms,
            year=item.get("ProductionYear"),
            genres=item.get("Genres", []),
            art_url=f"/api/art/{item_id}",
        )

    def get_all_tracks(self) -> list[Track]:
        """Get all tracks from the Jellyfin library."""
        if not self._connected or not self._library_id:
            return []

        try:
            tracks = []
            start_index = 0
            page_size = 1000

            with httpx.Client(headers=self._headers, timeout=120.0) as client:
                while True:
                    resp = client.get(
                        f"{self.url}/Items",
                        params={
                            "IncludeItemTypes": "Audio",
                            "Recursive": "true",
                            "ParentId": self._library_id,
                            "Fields": self._fields_param(),
                            "StartIndex": start_index,
                            "Limit": page_size,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get("Items", [])
                    if not items:
                        break
                    tracks.extend(self._item_to_track(item) for item in items)
                    if len(tracks) >= data.get("TotalRecordCount", 0):
                        break
                    start_index += page_size

            return tracks
        except Exception as e:
            logger.exception("Failed to get all Jellyfin tracks: %s", e)
            return []

    def get_all_albums_metadata(self) -> dict[str, dict[str, Any]]:
        """Fetch all albums and return mapping of album_id -> metadata."""
        if not self._connected or not self._library_id:
            return {}

        try:
            result = {}
            start_index = 0
            page_size = 1000

            with httpx.Client(headers=self._headers, timeout=120.0) as client:
                while True:
                    resp = client.get(
                        f"{self.url}/Items",
                        params={
                            "IncludeItemTypes": "MusicAlbum",
                            "Recursive": "true",
                            "ParentId": self._library_id,
                            "Fields": "Genres,ProductionYear",
                            "StartIndex": start_index,
                            "Limit": page_size,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get("Items", [])
                    if not items:
                        break
                    for item in items:
                        result[item["Id"]] = {
                            "genres": item.get("Genres", []),
                            "year": item.get("ProductionYear"),
                        }
                    if len(result) >= data.get("TotalRecordCount", 0):
                        break
                    start_index += page_size

            return result
        except Exception as e:
            logger.exception("Failed to get Jellyfin album metadata: %s", e)
            return {}

    def get_tracks_by_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        exclude_live: bool = True,
        min_rating: int = 0,
        limit: int = 0,
    ) -> list[Track]:
        """Get tracks matching filter criteria from Jellyfin."""
        if not self._connected or not self._library_id:
            return []

        try:
            params: dict[str, Any] = {
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                "ParentId": self._library_id,
                "Fields": self._fields_param(),
            }

            if genres:
                params["Genres"] = "|".join(genres)

            if decades:
                years: list[int] = []
                for decade in decades:
                    years.extend(_decade_to_years(decade))
                if years:
                    params["Years"] = ",".join(str(y) for y in years)

            tracks = []
            start_index = 0
            page_size = 1000
            fetch_limit = limit if limit > 0 else None

            with httpx.Client(headers=self._headers, timeout=120.0) as client:
                while True:
                    page_params = dict(params)
                    page_params["StartIndex"] = start_index
                    page_params["Limit"] = page_size

                    resp = client.get(f"{self.url}/Items", params=page_params)
                    resp.raise_for_status()
                    data = resp.json()
                    items = data.get("Items", [])
                    if not items:
                        break

                    for item in items:
                        track = self._item_to_track(item)
                        if exclude_live and is_live_track(track.title, track.album):
                            continue
                        tracks.append(track)
                        if fetch_limit and len(tracks) >= fetch_limit:
                            return tracks

                    total = data.get("TotalRecordCount", 0)
                    start_index += page_size
                    if start_index >= total:
                        break

            return tracks
        except Exception as e:
            logger.exception("Failed to get Jellyfin filtered tracks: %s", e)
            return []

    def get_random_tracks(
        self,
        count: int,
        exclude_live: bool = True,
    ) -> list[Track]:
        """Get random tracks from the Jellyfin library."""
        if not self._connected or not self._library_id:
            return []

        try:
            with httpx.Client(headers=self._headers, timeout=30.0) as client:
                # Fetch a larger pool and randomly sample
                resp = client.get(
                    f"{self.url}/Items",
                    params={
                        "IncludeItemTypes": "Audio",
                        "Recursive": "true",
                        "ParentId": self._library_id,
                        "Fields": self._fields_param(),
                        "SortBy": "Random",
                        "Limit": count * 3 if exclude_live else count,
                    },
                )
                resp.raise_for_status()
                items = resp.json().get("Items", [])

                tracks = []
                for item in items:
                    track = self._item_to_track(item)
                    if exclude_live and is_live_track(track.title, track.album):
                        continue
                    tracks.append(track)
                    if len(tracks) >= count:
                        break

                return tracks
        except Exception as e:
            logger.exception("Failed to get random Jellyfin tracks: %s", e)
            return []

    def get_track_by_key(self, rating_key: str) -> Track | None:
        """Get a single track by its Jellyfin item ID."""
        if not self._connected:
            return None

        try:
            with httpx.Client(headers=self._headers, timeout=10.0) as client:
                resp = client.get(
                    f"{self.url}/Items/{rating_key}",
                    params={"Fields": "Genres,ProductionYear,RunTimeTicks,AlbumArtist,Album,Artists"},
                )
                resp.raise_for_status()
                item = resp.json()
                return self._item_to_track(item)
        except Exception as e:
            logger.warning("Failed to get Jellyfin track %s: %s", rating_key, e)
            return None

    def search_tracks(self, query: str) -> list[Track]:
        """Search for tracks by title or artist in Jellyfin."""
        if not self._connected or not self._library_id:
            return []

        try:
            with httpx.Client(headers=self._headers, timeout=15.0) as client:
                resp = client.get(
                    f"{self.url}/Items",
                    params={
                        "IncludeItemTypes": "Audio",
                        "Recursive": "true",
                        "ParentId": self._library_id,
                        "SearchTerm": query,
                        "Fields": "Genres,ProductionYear,RunTimeTicks,AlbumArtist,Album,Artists",
                        "Limit": 50,
                    },
                )
                resp.raise_for_status()
                items = resp.json().get("Items", [])
                return [self._item_to_track(item) for item in items]
        except Exception as e:
            logger.warning("Failed to search Jellyfin tracks: %s", e)
            return []

    def count_tracks_by_filters(
        self,
        genres: list[str] | None = None,
        decades: list[str] | None = None,
        exclude_live: bool = True,
        min_rating: int = 0,
    ) -> int:
        """Count tracks matching filters. Uses full fetch when exclude_live is needed."""
        if not self._connected or not self._library_id:
            return 0

        if exclude_live:
            # No server-side live filter; must fetch and count
            tracks = self.get_tracks_by_filters(
                genres=genres,
                decades=decades,
                exclude_live=True,
                min_rating=min_rating,
                limit=0,
            )
            return len(tracks)

        try:
            params: dict[str, Any] = {
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                "ParentId": self._library_id,
                "Limit": 0,
            }
            if genres:
                params["Genres"] = "|".join(genres)
            if decades:
                years: list[int] = []
                for decade in decades:
                    years.extend(_decade_to_years(decade))
                if years:
                    params["Years"] = ",".join(str(y) for y in years)

            with httpx.Client(headers=self._headers, timeout=30.0) as client:
                resp = client.get(f"{self.url}/Items", params=params)
                resp.raise_for_status()
                return resp.json().get("TotalRecordCount", 0)
        except Exception as e:
            logger.warning("Failed to count Jellyfin tracks: %s", e)
            return 0

    def create_playlist(
        self,
        name: str,
        rating_keys: list[str],
        description: str = "",
    ) -> dict[str, Any]:
        """Create a playlist in Jellyfin."""
        if not self._connected or not self._user_id:
            return {"success": False, "error": "Not connected to Jellyfin"}

        try:
            with httpx.Client(headers=self._headers, timeout=30.0) as client:
                # Create playlist
                body = {
                    "Name": name,
                    "Ids": rating_keys,
                    "UserId": self._user_id,
                    "MediaType": "Audio",
                }
                resp = client.post(f"{self.url}/Playlists", json=body)
                resp.raise_for_status()
                playlist_data = resp.json()
                playlist_id = playlist_data.get("Id") or playlist_data.get("id")

                if not playlist_id:
                    return {"success": False, "error": "Playlist created but no ID returned"}

                # Note: Jellyfin doesn't have a built-in description field for playlists
                # in the same way Plex does; we skip setting it silently.

                return {
                    "success": True,
                    "playlist_id": playlist_id,
                    "playlist_url": None,
                    "tracks_added": len(rating_keys),
                    "tracks_skipped": 0,
                }
        except Exception as e:
            logger.exception("Failed to create Jellyfin playlist '%s'", name)
            return {"success": False, "error": str(e)}

    def update_playlist(
        self,
        playlist_id: str,
        rating_keys: list[str],
        mode: str = "replace",
        description: str = "",
    ) -> dict[str, Any]:
        """Update a Jellyfin playlist by replacing or appending tracks."""
        if not self._connected or not self._user_id:
            return {"success": False, "error": "Not connected to Jellyfin"}

        try:
            with httpx.Client(headers=self._headers, timeout=30.0) as client:
                if mode == "replace":
                    # Remove all existing items, then add new ones
                    items_resp = client.get(
                        f"{self.url}/Playlists/{playlist_id}/Items",
                        params={"UserId": self._user_id},
                    )
                    items_resp.raise_for_status()
                    existing_items = items_resp.json().get("Items", [])
                    existing_ids = [item["PlaylistItemId"] for item in existing_items if "PlaylistItemId" in item]

                    if existing_ids:
                        del_resp = client.delete(
                            f"{self.url}/Playlists/{playlist_id}/Items",
                            params={"EntryIds": ",".join(existing_ids)},
                        )
                        del_resp.raise_for_status()

                # Add new tracks
                add_resp = client.post(
                    f"{self.url}/Playlists/{playlist_id}/Items",
                    params={
                        "Ids": ",".join(rating_keys),
                        "UserId": self._user_id,
                    },
                )
                add_resp.raise_for_status()

                return {
                    "success": True,
                    "tracks_added": len(rating_keys),
                    "tracks_skipped": 0,
                    "duplicates_skipped": 0,
                    "playlist_url": None,
                }
        except Exception as e:
            logger.exception("Failed to update Jellyfin playlist %s", playlist_id)
            return {"success": False, "error": str(e)}

    def get_playlists(self) -> list[PlexPlaylistInfo]:
        """Get all audio playlists from Jellyfin."""
        if not self._connected or not self._user_id:
            return []

        try:
            with httpx.Client(headers=self._headers, timeout=15.0) as client:
                resp = client.get(
                    f"{self.url}/Items",
                    params={
                        "IncludeItemTypes": "Playlist",
                        "Recursive": "true",
                        "UserId": self._user_id,
                        "Fields": "ChildCount",
                    },
                )
                resp.raise_for_status()
                items = resp.json().get("Items", [])

                result = []
                for item in items:
                    # Filter to audio playlists (MediaType == Audio or no restriction)
                    media_type = item.get("MediaType", "")
                    if media_type and media_type.lower() not in ("audio", ""):
                        continue
                    result.append(PlexPlaylistInfo(
                        rating_key=item["Id"],
                        title=item["Name"],
                        track_count=item.get("ChildCount", 0),
                    ))
                return sorted(result, key=lambda p: p.title.lower())
        except Exception as e:
            logger.exception("Failed to get Jellyfin playlists: %s", e)
            return []

    def get_art_url(self, item_id: str) -> str | None:
        """Get the direct art URL for a Jellyfin item.

        Returns the full Jellyfin URL for the primary image, which main.py will proxy.
        """
        if not self._connected or not self.url:
            return None
        return f"{self.url}/Items/{item_id}/Images/Primary"

    def get_machine_identifier(self) -> str | None:
        """Get the Jellyfin server ID (for library cache server-change detection)."""
        try:
            with httpx.Client(headers=self._headers, timeout=10.0) as client:
                resp = client.get(f"{self.url}/System/Info")
                resp.raise_for_status()
                return resp.json().get("Id")
        except Exception:
            return None

    def get_server_name(self) -> str | None:
        """Get the Jellyfin server name."""
        try:
            with httpx.Client(headers=self._headers, timeout=10.0) as client:
                resp = client.get(f"{self.url}/System/Info")
                resp.raise_for_status()
                return resp.json().get("ServerName")
        except Exception:
            return None


def get_jellyfin_client() -> JellyfinClient | None:
    """Get the global Jellyfin client instance."""
    return _jellyfin_client


def init_jellyfin_client(url: str, token: str, music_library: str = "Music") -> JellyfinClient:
    """Initialize the global Jellyfin client."""
    global _jellyfin_client
    _jellyfin_client = JellyfinClient(url, token, music_library)
    return _jellyfin_client
