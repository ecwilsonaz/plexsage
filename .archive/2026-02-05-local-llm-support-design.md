# Local LLM Support (Ollama + Custom Providers)

## Overview

Add support for local LLMs via Ollama and any OpenAI-compatible endpoint. This enables users to run PlexSage without cloud API costs, using their own hardware.

## Providers

### Ollama Provider

Dedicated provider with auto-detection features:

- **Base URL**: User-configurable (default: `http://localhost:11434`)
- **Model discovery**: Query `/api/tags` to populate dropdowns
- **Context detection**: Query `/api/show` to get `num_ctx` for selected model
- **Two model dropdowns**: Analysis and Generation (default to same model)

### Custom Provider

Generic OpenAI-compatible provider for LM Studio, text-generation-webui, OpenWebUI, etc:

- **Base URL**: Required (e.g., `http://localhost:5000/v1`)
- **Model name**: Required text field
- **Context window**: Required number field (no auto-detection)

## Configuration

### YAML Config

```yaml
llm:
  provider: ollama  # anthropic | openai | gemini | ollama | custom

  # Ollama-specific
  ollama_url: http://localhost:11434

  # Custom-specific
  custom_url: http://localhost:5000/v1
  custom_context_window: 4096

  # Shared
  model_analysis: llama3:8b
  model_generation: llama3:8b
```

### Environment Variables

```bash
OLLAMA_URL=http://localhost:11434
CUSTOM_LLM_URL=http://localhost:5000/v1
CUSTOM_CONTEXT_WINDOW=4096
```

## Settings UI

### Ollama Selected

```
┌─────────────────────────────────────────────────────┐
│ Ollama URL: [http://localhost:11434    ]            │
│                                                     │
│ Status: ✓ Connected - 3 models available            │
│                                                     │
│ Analysis Model:   [▼ llama3:8b         ]            │
│ Generation Model: [▼ llama3:8b         ]            │
│                                                     │
│ Context Window: 8,192 tokens (auto-detected)        │
│ Max Tracks to AI: ~140 tracks                       │
└─────────────────────────────────────────────────────┘
```

### Status Messages

Display near Ollama URL field:

- ✓ "Connected - 3 models available"
- ⚠ "Cannot reach Ollama at localhost:11434"
- ⚠ "Connected but no models installed. Run `ollama pull llama3`"
- ⚠ "Selected model not found. Available: llama3, mistral"

### Custom Selected

```
┌─────────────────────────────────────────────────────┐
│ API Base URL:    [http://localhost:5000/v1]         │
│ Model Name:      [my-local-model        ]           │
│ Context Window:  [4096                  ] tokens    │
│                                                     │
│ Max Tracks to AI: ~70 tracks                        │
└─────────────────────────────────────────────────────┘
```

## Timeout Handling

Frontend timeout based on provider:

| Provider | Timeout |
|----------|---------|
| anthropic, openai, gemini | 60 seconds |
| ollama, custom | 10 minutes |

Timeout resets on each progress event (existing behavior). The 10-minute timeout accommodates slow local models on modest hardware.

## Cost Display

Cloud providers show tokens and estimated cost:
```
Generated 25 tracks • 12,450 tokens • ~$0.02
```

Local providers show tokens only (no dollar amount):
```
Generated 25 tracks • 12,450 tokens
```

## API Endpoints

### New Endpoints

**GET /api/ollama/models**

Returns available models from Ollama.

```json
{
  "models": ["llama3:8b", "mistral:7b", "phi3:mini"]
}
```

**GET /api/ollama/model-info?model=llama3:8b**

Returns model metadata including context window.

```json
{
  "name": "llama3:8b",
  "context_window": 8192,
  "parameter_size": "8B"
}
```

## Backend Changes

### models.py

```python
class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "gemini", "ollama", "custom"]
    api_key: str = ""  # Optional for local providers
    model_analysis: str
    model_generation: str
    smart_generation: bool = False
    ollama_url: str = "http://localhost:11434"
    custom_url: str = ""
    custom_context_window: int = 4096
```

### llm_client.py

- Add `_complete_ollama()` using Ollama's native `/api/generate` endpoint
- Add `_complete_custom()` using OpenAI-compatible `/v1/chat/completions`
- Modify `MODEL_CONTEXT_LIMITS` lookup to use config value for ollama/custom
- Return zero cost for local providers in `MODEL_COSTS`

### config.py

- Add `OLLAMA_URL`, `CUSTOM_LLM_URL`, `CUSTOM_CONTEXT_WINDOW` env var handling
- Update model defaults to handle new providers

## Error Handling

### Settings Page

Connection check on URL change:

1. Attempt `GET {ollama_url}/api/tags`
2. Success → populate dropdowns, show ✓ status
3. Connection refused → show warning with URL
4. Empty model list → suggest `ollama pull`

### Main App

- Unconfigured local provider → existing "LLM not configured" state
- Generation errors → "Generation failed - check Ollama settings"
- Detailed diagnostics live in settings, main UI stays clean

## Context Window Handling

Existing architecture unchanged:

- `get_max_tracks_for_model()` calculates track limit from context window
- Filter preview shows "Sending X of Y tracks"
- Random sampling when tracks exceed limit
- Strong filter UI already exists

Users with large VRAM get large context limits. Users with modest hardware get smaller limits. Same UX either way.

## Files to Modify

| File | Changes |
|------|---------|
| `backend/models.py` | LLMConfig fields, ConfigResponse |
| `backend/config.py` | New env vars, provider defaults |
| `backend/llm_client.py` | `_complete_ollama()`, `_complete_custom()`, context/cost lookup |
| `backend/main.py` | `/api/ollama/models`, `/api/ollama/model-info` endpoints |
| `frontend/app.js` | Provider-aware timeout, cost display logic |
| `frontend/index.html` | Settings UI for ollama/custom fields |
| `frontend/style.css` | Status message styling (if needed) |
