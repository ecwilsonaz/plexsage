# PlexSage

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ghcr.io%2Fecwilsonaz%2Fplexsage-blue)](https://ghcr.io/ecwilsonaz/plexsage)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**AI-powered playlist generation for Plex—using only tracks you actually own.**

PlexSage is a self-hosted web app that creates music playlists by combining LLM intelligence with your Plex library. Every track it suggests is guaranteed playable because it only considers music you have.

![PlexSage Screenshot](docs/images/screenshot-results.png)

## Demo

### Prompt-Based Flow
Describe what you want in natural language, refine filters, and generate a playlist:

![Prompt-based flow demo](https://github.com/user-attachments/assets/ae605d5a-676b-49ea-ae3a-3da79b01a97f)

### Seed-Based Flow
Start from a song you love and explore its musical dimensions:

![Seed-based flow demo](https://github.com/user-attachments/assets/0812a847-7d4a-4bf6-8d45-581a137fd71f)

---

## Quick Start

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

Open **http://localhost:8765** and start creating playlists.

**Requirements:** Docker, a Plex server with music, a [Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/), and an API key from Google, Anthropic, or OpenAI.

---

## Contents

- [Demo](#demo)
- [Why PlexSage?](#why-plexsage)
- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [How It Works](#how-it-works)
- [Development](#development)
- [API Reference](#api-reference)

---

## Why PlexSage?

**Plex users with personal music libraries have few good options for AI playlists.**

Plexamp's built-in Sonic Sage used ChatGPT to generate playlists, but it was designed around Tidal streaming. The AI recommended tracks from an unlimited catalog, and Tidal made them playable. The "limit to library" setting just hid results you didn't own—so if you asked for 25 tracks and only 4 existed in your library, you got a 4-track playlist.

When [Tidal integration ended in October 2024](https://forums.plex.tv/t/tidal-integration-with-plex-ending-october-28-2024/885728), Sonic Sage lost its foundation. Generic tools like ChatGPT have the same problem: they recommend from an infinite catalog with no awareness of what you actually own.

**PlexSage inverts the approach:**

| Filter-Last (Sonic Sage, ChatGPT) | Filter-First (PlexSage) |
|-----------------------------------|-------------------------|
| AI recommends from infinite catalog | AI only sees your library |
| Hide missing tracks after | No missing tracks possible |
| Near-empty playlists | Full playlists, every time |

The result: every track in every playlist exists in your Plex library and plays immediately.

---

## Features

### Two Ways to Start

**Describe what you want** — Natural language prompts like:
- "Melancholy 90s alternative for a rainy day"
- "Upbeat instrumental jazz for a dinner party"
- "Late night electronic, nothing too aggressive"

**Start from a song** — Pick a track you love, then explore musical dimensions: mood, era, instrumentation, genre, production style. Select which qualities you want more of.

### Smart Filtering

Before the AI sees anything, you control the pool:
- **Genres** — Select from your library's actual genre tags
- **Decades** — Filter by era
- **Minimum rating** — Only tracks rated 3+, 4+, etc.
- **Exclude live versions** — Skip concert recordings automatically

Real-time track counts show exactly how your filters narrow results.

### Cost Control

Choose how many tracks to send to the AI:

| Track Count | Use Case | Typical Cost |
|-------------|----------|--------------|
| 100–500 | Focused playlists, quick generation | Pennies |
| 1,000–5,000 | Broad requests, more variety | Cents |
| Up to 18,000 | Full library exploration (Gemini only) | Under $0.50 |

Estimated cost displays before you generate. No surprises.

### Multi-Provider Support

Bring your own API key:

| Provider | Max Tracks | Best For |
|----------|------------|----------|
| **Google Gemini** | ~18,000 | Large libraries, lowest cost |
| **Anthropic Claude** | ~3,500 | Nuanced recommendations |
| **OpenAI GPT** | ~2,300 | Solid all-around |

PlexSage auto-detects your provider based on which key you configure.

### Review and Save

- Preview tracks with album art before saving
- Remove tracks you don't want
- Rename the playlist
- See actual token usage and cost
- One-click save to Plex

---

## Installation

### Docker Compose (Recommended)

```bash
mkdir plexsage && cd plexsage
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/.env.example
mv .env.example .env
```

Edit `.env`:

```bash
PLEX_URL=http://your-plex-server:32400
PLEX_TOKEN=your-plex-token

# Choose ONE provider:
GEMINI_API_KEY=your-gemini-key
# ANTHROPIC_API_KEY=sk-ant-your-key
# OPENAI_API_KEY=sk-your-key
```

Start:

```bash
docker compose up -d
```

### NAS Platforms

<details>
<summary><strong>Synology (Container Manager)</strong></summary>

**GUI:**
1. **Container Manager** → **Registry** → Search `ghcr.io/ecwilsonaz/plexsage`
2. Download `latest` tag
3. **Container** → **Create**
4. Port: 8765 → 8765
5. Add environment variables: `PLEX_URL`, `PLEX_TOKEN`, `GEMINI_API_KEY`

**Docker Compose:**
```bash
mkdir -p /volume1/docker/plexsage && cd /volume1/docker/plexsage
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/ecwilsonaz/plexsage/main/.env.example
mv .env.example .env && nano .env
```
Then in **Container Manager** → **Project** → **Create**, point to `/volume1/docker/plexsage`.

</details>

<details>
<summary><strong>Unraid</strong></summary>

1. **Docker** → **Add Container**
2. Repository: `ghcr.io/ecwilsonaz/plexsage:latest`
3. Port: 8765 → 8765
4. Add variables: `PLEX_URL`, `PLEX_TOKEN`, `GEMINI_API_KEY`

</details>

<details>
<summary><strong>TrueNAS SCALE</strong></summary>

1. **Apps** → **Discover Apps** → **Custom App**
2. Image: `ghcr.io/ecwilsonaz/plexsage`, Tag: `latest`
3. Port: 8765
4. Add environment variables

</details>

<details>
<summary><strong>Portainer</strong></summary>

**Stacks** → **Add Stack**:

```yaml
services:
  plexsage:
    image: ghcr.io/ecwilsonaz/plexsage:latest
    ports:
      - "8765:8765"
    environment:
      - PLEX_URL=http://your-server:32400
      - PLEX_TOKEN=your-token
      - GEMINI_API_KEY=your-key
    restart: unless-stopped
```

</details>

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PLEX_URL` | Yes | Plex server URL (e.g., `http://192.168.1.100:32400`) |
| `PLEX_TOKEN` | Yes | [Plex authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |
| `GEMINI_API_KEY` | One required | Google Gemini API key |
| `ANTHROPIC_API_KEY` | One required | Anthropic API key |
| `OPENAI_API_KEY` | One required | OpenAI API key |
| `PLEX_MUSIC_LIBRARY` | No | Library name if not "Music" |
| `LLM_PROVIDER` | No | Force provider (auto-detected from API key) |

### Advanced: config.yaml

Mount a config file for additional options:

```yaml
plex:
  music_library: "Music"

llm:
  provider: "gemini"
  model_analysis: "gemini-2.5-flash"
  model_generation: "gemini-2.5-flash"
  smart_generation: false  # true = use smarter model for both (higher quality, ~3-5x cost)

defaults:
  track_count: 25
```

### Model Selection

PlexSage uses a two-model strategy by default:

| Role | Purpose | Models Used |
|------|---------|-------------|
| **Analysis** | Interpret prompts, suggest filters, analyze seed tracks | claude-sonnet-4-5 / gpt-4.1 / gemini-2.5-flash |
| **Generation** | Select tracks from filtered list | claude-haiku-4-5 / gpt-4.1-mini / gemini-2.5-flash |

This balances quality with cost. Enable `smart_generation: true` to use the analysis model for everything.

---

## How It Works

PlexSage uses a **filter-first architecture** designed for large libraries (50,000+ tracks):

```
┌─────────────────────────────────────────────────────────────────┐
│  1. ANALYZE                                                      │
│     LLM interprets your prompt → suggests genre/decade filters   │
├─────────────────────────────────────────────────────────────────┤
│  2. FILTER                                                       │
│     Plex library narrowed to matching tracks                     │
│     "90s Alternative" → 2,000 tracks                             │
├─────────────────────────────────────────────────────────────────┤
│  3. SAMPLE                                                       │
│     If too large for context, randomly sample                    │
│     Fits within model's token limits                             │
├─────────────────────────────────────────────────────────────────┤
│  4. GENERATE                                                     │
│     Filtered track list + prompt sent to LLM                     │
│     LLM selects best matches from available tracks               │
├─────────────────────────────────────────────────────────────────┤
│  5. MATCH                                                        │
│     Fuzzy matching links LLM selections to library               │
│     Handles minor spelling/formatting differences                │
├─────────────────────────────────────────────────────────────────┤
│  6. SAVE                                                         │
│     Playlist created in Plex                                     │
│     Ready in Plexamp or any Plex client                          │
└─────────────────────────────────────────────────────────────────┘
```

This ensures every track exists in your library while keeping API costs manageable.

---

## Development

### Local Setup

```bash
git clone https://github.com/ecwilsonaz/plexsage.git
cd plexsage
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export PLEX_URL=http://your-plex-server:32400
export PLEX_TOKEN=your-plex-token
export GEMINI_API_KEY=your-key

uvicorn backend.main:app --reload --port 8765
```

### Testing

```bash
pytest tests/ -v
```

### Tech Stack

- **Backend:** Python 3.11+, FastAPI, python-plexapi, rapidfuzz
- **Frontend:** Vanilla HTML/CSS/JS (no build step)
- **LLM SDKs:** anthropic, openai, google-generativeai
- **Deployment:** Docker

---

## API Reference

Interactive documentation available at `/docs` when running.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/config` | GET | Current configuration |
| `/api/library/stats` | GET | Library statistics |
| `/api/analyze/prompt` | POST | Analyze natural language prompt |
| `/api/generate` | POST | Generate playlist |
| `/api/playlist` | POST | Save playlist to Plex |

---

## License

MIT
