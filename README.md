# PlexSage

A self-hosted web application that generates Plex music playlists using LLMs with library awareness. PlexSage uses a filter-first approach to ensure 100% of suggested tracks are playable from your library.

## Features

- **Prompt-Based Playlists**: Describe what you want ("melancholy 90s alternative for a rainy day") and get a curated playlist
- **Seed Track Discovery**: Start from a song you like and explore similar music across selectable dimensions (mood, era, instrumentation, etc.)
- **Library-First Guarantee**: Every track in generated playlists exists in your Plex library
- **Smart Filtering**: Refine by genre, decade, minimum rating, and more before generation
- **Cost Transparency**: See actual token usage and costs for each request
- **Context-Aware Limits**: Automatically adjusts track limits based on your LLM's context window
- **Multi-Provider Support**: Works with Anthropic Claude, OpenAI GPT, or Google Gemini

## Quick Start

### Prerequisites

- Docker
- A Plex server with a music library
- [Plex authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
- API key from Anthropic, OpenAI, or Google

### Docker Run

```bash
docker run -d \
  --name plexsage \
  -p 8765:8765 \
  -e PLEX_URL=http://your-plex-server:32400 \
  -e PLEX_TOKEN=your-plex-token \
  -e GEMINI_API_KEY=your-gemini-key \
  --restart unless-stopped \
  ghcr.io/ecwilsonaz/plexsage:latest
```

Then open http://localhost:8765

### Docker Compose

1. Create a directory and download the compose file:
```bash
mkdir plexsage && cd plexsage
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/.env.example
mv .env.example .env
```

2. Edit `.env` with your credentials:
```bash
PLEX_URL=http://your-plex-server:32400
PLEX_TOKEN=your-plex-token

# Choose ONE provider:
GEMINI_API_KEY=your-gemini-key
# ANTHROPIC_API_KEY=sk-ant-your-key
# OPENAI_API_KEY=sk-your-key
```

3. Start:
```bash
docker compose up -d
```

## NAS Deployment

### Synology (Container Manager)

1. SSH into your Synology or use Task Scheduler to run:
```bash
mkdir -p /volume1/docker/plexsage
cd /volume1/docker/plexsage
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/docker-compose.yml
```

2. In **Container Manager** → **Project** → **Create**:
   - Set path to `/volume1/docker/plexsage`
   - Add environment variables (PLEX_URL, PLEX_TOKEN, your LLM API key)

3. Or use the GUI to create a container directly:
   - Image: `ghcr.io/ecwilsonaz/plexsage:latest`
   - Port: 8765 → 8765
   - Environment: Add your credentials

### Unraid

Use Community Apps or add container manually:
- Repository: `ghcr.io/ecwilsonaz/plexsage:latest`
- Port mapping: 8765
- Add environment variables for PLEX_URL, PLEX_TOKEN, and your LLM API key

### Portainer

**Stacks** → **Add Stack** → paste docker-compose.yml contents, add environment variables.

## LLM Providers

PlexSage auto-detects your provider based on which API key is set.

| Provider | Models | Max Tracks | Cost | Notes |
|----------|--------|------------|------|-------|
| **Gemini** | gemini-2.5-flash | ~18,000 | Lowest | Great for large libraries |
| **Anthropic** | claude-sonnet-4-5, claude-haiku-4-5 | ~3,500 | Medium | Nuanced recommendations |
| **OpenAI** | gpt-4.1, gpt-4.1-mini | ~2,300 | Medium | Solid all-around choice |

### Two-Model Strategy

PlexSage uses two models by default:
- **Analysis model** (smarter): Understands your prompt, suggests filters, analyzes seed tracks
- **Generation model** (cheaper): Selects tracks from the filtered list

This balances quality and cost. Set `smart_generation: true` in config to use the analysis model for everything (higher quality, ~3-5x cost).

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `PLEX_URL` | Your Plex server URL |
| `PLEX_TOKEN` | Plex authentication token |
| `PLEX_MUSIC_LIBRARY` | Music library name (default: "Music") |
| `LLM_PROVIDER` | anthropic, openai, or gemini (auto-detected if not set) |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` | Google Gemini API key |

### Optional: config.yaml

For additional customization, mount a config file:

```yaml
plex:
  music_library: "Music"

llm:
  provider: "gemini"
  model_analysis: "gemini-2.5-flash"
  model_generation: "gemini-2.5-flash"
  smart_generation: false

defaults:
  track_count: 25
```

## How It Works

PlexSage uses a **filter-first architecture** to handle large libraries:

1. **Analyze**: LLM interprets your prompt and suggests genre/decade filters
2. **Filter**: Library is narrowed to matching tracks (e.g., "90s Alternative" → 2,000 tracks)
3. **Sample**: If still too large, randomly samples tracks to fit context limits
4. **Generate**: Filtered track list sent to LLM for curation
5. **Match**: LLM selections are fuzzy-matched back to your library
6. **Save**: Playlist is created in Plex

This ensures every track exists in your library while keeping costs manageable for 50,000+ track libraries.

## Development

### Local Setup

```bash
git clone https://github.com/ecwilsonaz/plexsage.git
cd plexsage

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt

# Set environment variables (or use .env file)
export PLEX_URL=http://your-plex-server:32400
export PLEX_TOKEN=your-plex-token
export GEMINI_API_KEY=your-key

uvicorn backend.main:app --reload --port 8765
```

### Running Tests

```bash
pytest tests/ -v
```

## API

Interactive API documentation available at `/docs` when running.

Key endpoints:
- `GET /api/health` - Health check
- `GET /api/config` - Current configuration
- `GET /api/library/stats` - Library statistics
- `POST /api/analyze/prompt` - Analyze natural language prompt
- `POST /api/generate` - Generate playlist
- `POST /api/playlist` - Save playlist to Plex

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, python-plexapi, rapidfuzz
- **Frontend**: Vanilla HTML/CSS/JS (no build step)
- **LLM SDKs**: anthropic, openai, google-generativeai
- **Deployment**: Docker

## License

MIT
