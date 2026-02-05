# MediaSage Development Guidelines

Auto-generated from feature plans. Last updated: 2026-02-04

## Project Overview

MediaSage is a self-hosted web application that generates Plex music playlists using LLMs with library awareness. It uses a filter-first approach to ensure 100% of suggested tracks are playable.

## Active Technologies
- Python 3.11+ (backend), Vanilla JavaScript ES6+ (frontend) + FastAPI, python-plexapi, sqlite3 (stdlib), Pydantic (003-local-library-cache)
- SQLite file at `data/library_cache.db` (003-local-library-cache)

- **Backend**: Python 3.11+, FastAPI, python-plexapi, anthropic SDK, openai SDK, pydantic, uvicorn, rapidfuzz, unidecode
- **Frontend**: Vanilla HTML/CSS/JS (no build step)
- **Config**: YAML + environment variables
- **Deployment**: Docker

## Project Structure

```text
backend/
├── main.py              # FastAPI app, routes, static file serving
├── config.py            # Config loading (YAML + env vars)
├── plex_client.py       # Plex connection, queries, playlist creation
├── llm_client.py        # Claude/OpenAI abstraction
├── analyzer.py          # Prompt analysis + seed track dimensions
├── generator.py         # Playlist generation
└── models.py            # Pydantic models

frontend/
├── index.html           # Single page app
├── style.css            # Dark theme (Plexamp aesthetic)
└── app.js               # UI logic

tests/
└── test_*.py            # pytest tests
```

## Commands

```bash
# Development
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 5765

# Testing
pytest

# Linting
ruff check .

# Docker
docker-compose up -d
```

## Code Style

- **Python**: PEP 8, type hints, Pydantic models for all API contracts
- **JavaScript**: ES6+, no framework, simple state object
- **CSS**: BEM-style naming, CSS custom properties for theming

## Constitution Principles

1. **Library-First**: All playlist tracks MUST exist in user's library
2. **Simplicity**: No build steps, no frameworks, single container
3. **User Agency**: Users control filters and can remove/regenerate
4. **Cost Transparency**: Display token counts and estimated costs
5. **Plexamp Aesthetic**: Dark theme (#1a1a1a), amber accent (#e5a00d)

## Environment Variables

```bash
PLEX_URL=http://your-plex-server:32400
PLEX_TOKEN=your-plex-token
LLM_PROVIDER=anthropic  # anthropic, openai, gemini, ollama, or custom
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
# For local providers
OLLAMA_URL=http://localhost:11434
CUSTOM_LLM_URL=http://localhost:5000/v1
CUSTOM_CONTEXT_WINDOW=4096
```

## Key Design Decisions

- **Filter-first**: Apply genre/decade filters before sending to LLM (handles 50k+ track libraries)
- **No database**: Library data fetched from Plex on demand; config in YAML
- **No auth**: Rely on network security (home LAN, VPN, reverse proxy)
- **Album art proxy**: Backend proxies art to avoid exposing Plex token to browser
- **Two-model strategy**: Smart model for analysis, cheap model for generation
- **Fuzzy track matching**: Use rapidfuzz (threshold ~60) to match LLM responses to library
- **Live version filtering**: Exclude tracks with "live", "concert", dates in title/album

## LLM Models

| Task | Anthropic | OpenAI | Gemini |
|------|-----------|--------|--------|
| Analysis | `claude-sonnet-4-5` | `gpt-4.1` | `gemini-2.5-flash` |
| Generation | `claude-haiku-4-5` | `gpt-4.1-mini` | `gemini-2.5-flash` |
| Context Limit | 200K tokens | 128K tokens | **1M tokens** |

Gemini's 1M context allows sending ~18,000 tracks to the AI, vs ~3,500 for Anthropic/OpenAI.

Option: `smart_generation: true` uses analysis model for both (higher quality, ~3-5x cost)

### Local LLM Providers

**Ollama** (`LLM_PROVIDER=ollama`):
- Auto-discovers installed models via `/api/tags`
- Auto-detects context window via `/api/show`
- Uses native `/api/generate` endpoint for completions
- 10-minute timeout for slow hardware
- Zero cost (local inference)

**Custom** (`LLM_PROVIDER=custom`):
- Any OpenAI-compatible endpoint (LM Studio, text-generation-webui, etc.)
- Manual configuration: URL, model name, context window
- Uses openai SDK with custom `base_url`
- Zero cost (local inference)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->

## Recent Changes
- 003-local-library-cache: Added Python 3.11+ (backend), Vanilla JavaScript ES6+ (frontend) + FastAPI, python-plexapi, sqlite3 (stdlib), Pydantic
