"""Version detection from git tags."""

import subprocess
from functools import lru_cache

# Fallback version when git is unavailable (e.g., Docker without .git)
FALLBACK_VERSION = "0.1.0"


@lru_cache(maxsize=1)
def get_version() -> str:
    """Get version from git tags.

    Returns version in format:
    - "0.1.0" for exact tag match
    - "0.1.0-5-gabcdef" for commits after tag (5 commits, short hash)
    - FALLBACK_VERSION if git unavailable
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=__file__.rsplit("/", 1)[0],  # Run from backend dir
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            # Remove 'v' prefix if present (v0.1.0 -> 0.1.0)
            if version.startswith("v"):
                version = version[1:]
            return version
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return FALLBACK_VERSION
