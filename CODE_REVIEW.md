# PlexSage Code Review Issues

Generated: 2024-02-04

## Critical

- [x] **C1**: Unhandled None for model names (`llm_client.py:171-172`) - Fixed: changed type to required `str`
- [x] **C2**: Potential injection via LLM responses (`plex_client.py:245-259`) - Not an issue: whitelist validation at analyzer.py:102-109 prevents this

## High

- [x] **H1**: Token exposure risk in error paths (`main.py:383`) - Fixed: moved token from URL param to HTTP header
- [x] **H2**: Missing rating_key validation (`main.py:364`) - Fixed: added explicit validation with 400 response
- [x] **H3**: Private attribute access `_server` (`main.py:381`) - Fixed: added `get_thumb_path()` method to PlexClient
- [x] **H4**: Race condition with global clients (`plex_client.py:460-464`) - Not an issue: single-user app, config changes rare
- [x] **H5**: API keys visible in DOM (`app.js:928-938`) - Not an issue: backend only sends boolean flags, not actual keys

## Medium

- [x] **M1**: Silent exception swallowing (`plex_client.py:269-270`) - Fixed: logs exception and raises PlexQueryError
- [x] **M2**: Silent playlist failures (`plex_client.py:410-412`) - Fixed: logs skipped tracks and reports count to UI
- [x] **M3**: Negative value in cost calculation (`main.py:265-268`) - Not an issue: -1 sentinel handled correctly, cost uses tracks_to_send
- [x] **M4**: Duplicate track matching logic (`generator.py` vs `plex_client.py`) - Fixed: removed dead `match_track` function
- [x] **M5**: Malformed markdown parsing (`llm_client.py:207-216`) - Fixed: use regex to properly extract code block content
- [x] **M6**: "None" rendered in LLM prompts (`analyzer.py:85-86`) - Fixed: conditionally show count only when not None
- [x] **M7**: Interval memory leak (`app.js:430-436`) - Not an issue: interval properly cleared in setLoading()
- [x] **M8**: XSS vulnerability in track rendering (`app.js:664-672`) - Fixed: added escapeHtml() and applied to all user data in innerHTML
- [x] **M9**: Deprecated @app.on_event (`main.py:48`) - Fixed: migrated to lifespan context manager
- [x] **M10**: Blocking I/O in async endpoints (`plex_client.py:122`) - Fixed: wrapped blocking Plex calls with asyncio.to_thread()

## Low

- [x] **L1**: Debug print statements left in code - Fixed: converted to proper logging
- [x] **L2**: Unnecessary f-strings - Fixed: removed f-prefix from string without variables
- [x] **L3**: Incomplete state reset after save (`app.js:875-879`) - Fixed: full state reset after saving playlist
- [x] **L4**: Undefined CSS variables (`style.css:411-413, 443-446, 467-469`) - Fixed: added --bg-elevated, --border, --radius to :root
- [x] **L5**: Unused `match_track` import (`plex_client.py`) - Fixed: removed with M4
- [x] **L6**: Missing ARIA attributes (`index.html`) - Fixed: comprehensive ARIA for navigation, tabs, forms, toasts, loading
- [x] **L7**: Missing response_model annotation (`main.py:364`) - Not an issue: binary image endpoint correctly uses Response, not Pydantic model
- [x] **L8**: No timeout on filter preview queries (`plex_client.py:322`) - Fixed: added 30s timeout to PlexServer connection
