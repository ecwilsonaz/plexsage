"""Version detection from git tags or environment."""

import os
import subprocess
from functools import lru_cache

# Fallback version when git is unavailable (e.g., Docker without .git)
FALLBACK_VERSION = "dev"


@lru_cache(maxsize=1)
def get_version() -> str:
    """Get version from environment, git tags, or fallback.

    Priority:
    1. APP_VERSION env var (set by Docker build)
    2. Git describe (for local development)
    3. FALLBACK_VERSION

    Returns version in format:
    - "0.1.0" for exact tag match
    - "0.1.0-5-gabcdef" for commits after tag (5 commits, short hash)
    - "dev" if nothing available
    """
    # Check for Docker-injected version first
    env_version = os.environ.get("APP_VERSION")
    if env_version and env_version != "dev":
        return env_version

    # Try git describe for local development
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
