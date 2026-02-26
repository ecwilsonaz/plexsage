/**
 * MediaSage - Frontend Application
 */

// =============================================================================
// Focus Management (Accessibility)
// =============================================================================

const focusManager = {
    _stack: [],

    /** Open a modal: save focus, move into modal, trap Tab within it */
    openModal(modalEl) {
        const previousFocus = document.activeElement;

        // Find focusable elements inside the modal
        const focusable = this._getFocusable(modalEl);
        if (focusable.length) {
            const closeBtn = modalEl.querySelector('.modal-close, .bottom-sheet-close');
            requestAnimationFrame(() => (closeBtn || focusable[0]).focus());
        }

        // Trap Tab within modal
        const trapHandler = (e) => {
            if (e.key !== 'Tab') return;
            const els = this._getFocusable(modalEl);
            if (!els.length) return;
            const first = els[0];
            const last = els[els.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        };
        document.addEventListener('keydown', trapHandler);
        this._stack.push({ modalEl, previousFocus, trapHandler });
    },

    /** Close a modal: remove trap, restore previous focus.
     *  Accepts an optional modalEl to find the matching entry (safe for non-LIFO order).
     *  Falls back to popping the top entry when called without arguments. */
    closeModal(modalEl) {
        let idx = this._stack.length - 1;
        if (modalEl) {
            idx = this._stack.findLastIndex(e => e.modalEl === modalEl);
        }
        if (idx < 0) return;
        const [entry] = this._stack.splice(idx, 1);
        document.removeEventListener('keydown', entry.trapHandler);
        if (entry.previousFocus && typeof entry.previousFocus.focus === 'function') {
            entry.previousFocus.focus();
        }
    },

    _getFocusable(el) {
        return [...el.querySelectorAll(
            'a[href], button:not([disabled]), textarea, input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )].filter(e => !e.closest('.hidden') && e.offsetParent !== null);
    }
};

// =============================================================================
// State Management
// =============================================================================

const state = {
    // Current view and mode
    view: 'home', // 'home' | 'create' | 'recommend' | 'settings'
    mode: 'prompt', // 'prompt' | 'seed'
    step: 'input',  // 'input' | 'refine' | 'dimensions' | 'filters' | 'results'

    // Prompt flow
    prompt: '',

    // Refine questions (prompt mode)
    questions: [],          // ClarifyingQuestion[] from /recommend/questions
    questionAnswers: [],    // (string|null)[] — selected option per question
    questionTexts: [],      // string[] — free-text additions per question
    filterAnalysisPromise: null,  // cached promise from parallel filter analysis

    // Seed track flow
    seedTrack: null,
    dimensions: [],
    selectedDimensions: [],
    additionalNotes: '',

    // Filters
    availableGenres: [],
    availableDecades: [],
    selectedGenres: [],
    selectedDecades: [],
    trackCount: 25,
    excludeLive: true,
    maxTracksToAI: 500,  // 0 = no limit
    minRating: 0,  // 0 = any, 2/4/6/8 = 1/2/3/4 stars minimum

    // Results
    playlist: [],
    playlistName: '',
    tokenCount: 0,
    estimatedCost: 0,

    // Curator narrative
    playlistTitle: '',      // Generated title with date
    narrative: '',          // 2-3 sentence curator note
    trackReasons: {},       // { rating_key: "reason string" }
    userRequest: '',        // Original user prompt for display

    // Cost tracking (accumulated across analysis + generation)
    sessionTokens: 0,
    sessionCost: 0,

    // UI state
    loading: false,
    error: null,

    // Config
    config: null,

    // Cached filter preview (for local cost recalculation)
    lastFilterPreview: null,  // { matching_tracks, tracks_to_send }

    // Results UX — selection
    selectedTrackKey: null,    // Currently selected track in detail panel

    // Instant Queue (005) — Play Now
    plexClients: [],           // Never cached — fetched fresh each time (FR-016)
    _pendingClientId: null,    // Client ID awaiting play choice modal selection

    // Instant Queue (005) — Update Existing
    saveMode: 'new',           // 'new' | 'replace' | 'append'
    selectedPlaylistId: null,
    plexPlaylists: [],         // Cached after first fetch (FR-017)

    // Recommendation (006)
    rec: {
        mode: 'library',       // 'library' | 'discovery'
        step: 'prompt',        // 'prompt' | 'refine' | 'setup' | 'results'
        prompt: '',
        selectedGenres: [],
        selectedDecades: [],
        familiarityPref: 'any', // 'any' | 'comfort' | 'rediscover' | 'hidden_gems'
        questions: [],
        answers: [],           // Selected option per question (null = skipped)
        answerTexts: [],       // Free-text additions per question
        sessionId: null,
        recommendations: [],
        tokenCount: 0,
        estimatedCost: 0,
        researchWarning: null,
        resultId: null,
        maxAlbumsToAI: 2500,
        loading: false,
        filterAnalysisPromise: null,
    },

    // Setup wizard
    setup: {
        active: false,
        status: null,
        syncPollInterval: null,
    },
};

// =============================================================================
// Filter Helpers
// =============================================================================

function allGenresSelected() {
    return state.availableGenres.length > 0 &&
        state.selectedGenres.length === state.availableGenres.length;
}

function allDecadesSelected() {
    return state.availableDecades.length > 0 &&
        state.selectedDecades.length === state.availableDecades.length;
}

// =============================================================================
// API Calls
// =============================================================================

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function artistHue(name) {
    if (!name) return -1;
    let h = 5381;
    for (let i = 0; i < name.length; i++) h = ((h << 5) + h + name.charCodeAt(i)) >>> 0;
    return h % 360;
}

function artPlaceholderHtml(artist, large = false) {
    const hue = artistHue(artist);
    const letter = artist ? artist.charAt(0).toUpperCase() : '\u266B';
    const bg = hue >= 0 ? `hsl(${hue},30%,20%)` : 'hsl(0,0%,20%)';
    const fg = hue >= 0 ? `hsl(${hue},40%,60%)` : 'hsl(0,0%,55%)';
    const glow = large && hue >= 0 ? `background-image:radial-gradient(circle,hsl(${hue},40%,35%) 0%,transparent 70%);` : '';
    return `<div class="art-placeholder" style="background-color:${bg};color:${fg};${glow}">${escapeHtml(letter)}</div>`;
}

function trackArtHtml(track) {
    if (track.art_url) {
        return `<img class="track-art" src="${escapeHtml(track.art_url)}"
                     alt="${escapeHtml(track.album)}" loading="lazy"
                     data-artist="${escapeHtml(track.artist || '')}"
                     onerror="this.outerHTML=artPlaceholderHtml(this.dataset.artist)">`;
    }
    return artPlaceholderHtml(track.artist);
}

async function apiCall(endpoint, options = {}) {
    const response = await fetch(`/api${endpoint}`, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
        ...options,
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        const detail = Array.isArray(error.detail) ? error.detail.map(e => e.msg).join('; ') : error.detail;
        throw new Error(detail || error.error || 'Request failed');
    }

    return response.json();
}

async function fetchConfig() {
    return apiCall('/config');
}

async function updateConfig(updates) {
    return apiCall('/config', {
        method: 'POST',
        body: JSON.stringify(updates),
    });
}

// =============================================================================
// Ollama API Calls
// =============================================================================

async function fetchOllamaStatus(url) {
    return apiCall(`/ollama/status?url=${encodeURIComponent(url)}`);
}

async function fetchOllamaModels(url) {
    return apiCall(`/ollama/models?url=${encodeURIComponent(url)}`);
}

async function fetchOllamaModelInfo(url, modelName) {
    return apiCall(`/ollama/model-info?url=${encodeURIComponent(url)}&model=${encodeURIComponent(modelName)}`);
}

// =============================================================================
// Setup Wizard API Calls
// =============================================================================

async function fetchSetupStatus() {
    return apiCall('/setup/status');
}

async function validatePlex(url, token, library) {
    return apiCall('/setup/validate-plex', {
        method: 'POST',
        body: JSON.stringify({ plex_url: url, plex_token: token, music_library: library }),
    });
}

async function validateJellyfin(url, token, library) {
    return apiCall('/setup/validate-jellyfin', {
        method: 'POST',
        body: JSON.stringify({ jellyfin_url: url, jellyfin_token: token, music_library: library }),
    });
}

async function validateAI(provider, apiKey, ollamaUrl, customUrl) {
    return apiCall('/setup/validate-ai', {
        method: 'POST',
        body: JSON.stringify({
            provider,
            api_key: apiKey || '',
            ollama_url: ollamaUrl || '',
            custom_url: customUrl || '',
        }),
    });
}

async function completeSetup() {
    return apiCall('/setup/complete', { method: 'POST' });
}

async function analyzePrompt(prompt) {
    return apiCall('/analyze/prompt', {
        method: 'POST',
        body: JSON.stringify({ prompt }),
    });
}

async function searchTracks(query) {
    return apiCall(`/library/search?q=${encodeURIComponent(query)}`);
}

async function analyzeTrack(ratingKey) {
    return apiCall('/analyze/track', {
        method: 'POST',
        body: JSON.stringify({ rating_key: ratingKey }),
    });
}

// Module-level abort controller for SSE requests
// Allows aborting previous request when starting a new one
let currentAbortController = null;
let pendingNavHash = null;  // stored when mid-flow modal intercepts navigation


function generatePlaylistStream(request, onProgress, onComplete, onError) {
    // Abort any previous in-flight request
    if (currentAbortController) {
        currentAbortController.abort();
    }

    // Timeout handling - 10 minutes for local providers, 5 minutes for cloud
    let timeoutId = null;
    let completed = false;
    currentAbortController = new AbortController();
    const isLocalProvider = state.config?.is_local_provider ?? false;
    const TIMEOUT_MS = isLocalProvider ? 600000 : 300000;  // 10 min vs 5 min

    function resetTimeout() {
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            currentAbortController.abort();
            onError(new Error('Request timed out. Try selecting some filters to reduce the library size.'));
        }, TIMEOUT_MS);
    }

    function clearTimeoutHandler() {
        if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
        }
    }

    resetTimeout();

    // Use fetch with streaming for SSE (EventSource doesn't support POST)
    fetch('/api/generate/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: currentAbortController.signal,
    }).then(response => {
        if (!response.ok) {
            clearTimeoutHandler();
            throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        function processStream() {
            reader.read().then(({ done, value }) => {
                // Reset timeout on each chunk received
                if (!done) {
                    resetTimeout();
                }

                // Decode and add to buffer (even if done, to flush any remaining)
                buffer += decoder.decode(value, { stream: !done });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                // SSE parsing: accumulate data until blank line signals end of event.
                // This prevents failures when large data lines are split across chunks.
                // See: https://html.spec.whatwg.org/multipage/server-sent-events.html
                let currentEvent = null;
                let currentData = '';
                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        currentEvent = line.slice(7);
                        currentData = '';
                    } else if (line.startsWith('data: ')) {
                        // Accumulate data (SSE can have multiple data: lines per event)
                        currentData += line.slice(6);
                    } else if (line === '' && currentEvent && currentData) {
                        // Blank line = end of SSE event, now parse complete data
                        try {
                            const data = JSON.parse(currentData);
                            if (currentEvent === 'progress') {
                                onProgress(data);
                            } else if (currentEvent === 'narrative') {
                                // Store narrative data in state
                                state.playlistTitle = data.playlist_title || '';
                                state.narrative = data.narrative || '';
                                state.trackReasons = data.track_reasons || {};
                                state.userRequest = data.user_request || '';
                                // Initialize tracks array for batched receiving
                                state.pendingTracks = [];
                                console.log('[MediaSage] Narrative received:', state.playlistTitle);
                            } else if (currentEvent === 'tracks') {
                                // Accumulate track batches
                                if (data.batch && Array.isArray(data.batch)) {
                                    state.pendingTracks = state.pendingTracks || [];
                                    state.pendingTracks.push(...data.batch);
                                    console.log('[MediaSage] Track batch received, total:', state.pendingTracks.length);
                                }
                            } else if (currentEvent === 'complete') {
                                console.log('[MediaSage] Complete event received, pending tracks:', state.pendingTracks?.length || 0);
                                clearTimeoutHandler();
                                completed = true;
                                // Merge accumulated tracks into complete data
                                const completeData = {
                                    ...data,
                                    tracks: state.pendingTracks || data.tracks || [],
                                };
                                state.pendingTracks = [];
                                onComplete(completeData);
                            } else if (currentEvent === 'error') {
                                clearTimeoutHandler();
                                onError(new Error(data.message));
                            }
                        } catch (e) {
                            console.error('[MediaSage] Failed to parse SSE event:', currentEvent, e);
                        }
                        currentEvent = null;
                        currentData = '';
                    }
                }

                if (done) {
                    clearTimeoutHandler();
                    if (buffer.trim().length > 0) {
                        console.warn('[MediaSage] Stream ended with unparsed buffer:', buffer);
                    }
                    // iOS Safari fallback: if stream ended without complete event but we have tracks
                    if (state.pendingTracks && state.pendingTracks.length > 0 && !completed) {
                        console.warn('[MediaSage] Stream ended without complete event, synthesizing completion with', state.pendingTracks.length, 'tracks');
                        const syntheticComplete = {
                            tracks: state.pendingTracks,
                            track_count: state.pendingTracks.length,
                            playlist_title: state.playlistTitle || 'Playlist',
                            narrative: state.narrative || '',
                        };
                        state.pendingTracks = [];
                        onComplete(syntheticComplete);
                    }
                    return;
                }

                processStream();
            }).catch(err => {
                clearTimeoutHandler();
                if (err.name !== 'AbortError') {
                    onError(err);
                }
            });
        }

        processStream();
    }).catch(err => {
        clearTimeoutHandler();
        if (err.name !== 'AbortError') {
            onError(err);
        }
    });
}

async function savePlaylist(name, ratingKeys, description = '') {
    return apiCall('/playlist', {
        method: 'POST',
        body: JSON.stringify({ name, rating_keys: ratingKeys, description }),
    });
}

// =============================================================================
// Instant Queue API Calls (005)
// =============================================================================

async function fetchPlexClients() {
    return apiCall('/plex/clients');
}

async function createPlayQueue(ratingKeys, clientId, mode) {
    return apiCall('/play-queue', {
        method: 'POST',
        body: JSON.stringify({ rating_keys: ratingKeys, client_id: clientId, mode }),
    });
}

async function fetchPlexPlaylists() {
    return apiCall('/plex/playlists');
}

async function sendPlaylistUpdate(playlistId, ratingKeys, mode, description = '') {
    return apiCall('/playlist/update', {
        method: 'POST',
        body: JSON.stringify({
            playlist_id: playlistId,
            rating_keys: ratingKeys,
            mode,
            description,
        }),
    });
}

async function fetchLibraryStats() {
    return apiCall('/library/stats');
}

async function fetchLibraryStatus() {
    return apiCall('/library/status');
}

async function triggerLibrarySync() {
    return apiCall('/library/sync', { method: 'POST' });
}

// =============================================================================
// UI Updates
// =============================================================================

const HASH_TO_VIEW = {
    'home': 'home',
    'playlist-prompt': 'create',
    'playlist-seed': 'create',
    'recommend-album': 'recommend',
    'settings': 'settings',
    'result': 'result',
    // Backward compat
    'make-playlist': 'create',
};
const HASH_TO_MODE = {
    'playlist-prompt': 'prompt',
    'playlist-seed': 'seed',
    'make-playlist': 'prompt',
};
const VIEW_TO_HASH = {
    'home': 'home',
    'create': null,  // determined by mode
    'recommend': 'recommend-album',
    'settings': 'settings',
};

function hashForCurrentState() {
    if (state.view === 'create') {
        return state.mode === 'seed' ? 'playlist-seed' : 'playlist-prompt';
    }
    return VIEW_TO_HASH[state.view] || 'home';
}

function viewFromHash() {
    const hash = location.hash.slice(1).split('/')[0]; // prefix-match for future deep links
    return HASH_TO_VIEW[hash] || 'home';
}

function modeFromHash() {
    const hash = location.hash.slice(1).split('/')[0];
    return HASH_TO_MODE[hash] || 'prompt';
}

function navigateTo(view, mode) {
    // During setup wizard, only allow navigation to settings
    if (state.setup.active && view !== 'settings' && view !== 'home') return;
    const viewChanged = state.view !== view;
    const modeChanged = mode && state.mode !== mode;
    if (!viewChanged && !modeChanged) return;
    if (mode) state.mode = mode;
    state.view = view;
    updateView();
    // Reset results-specific layout when leaving a results view
    if (viewChanged) {
        const appEl = document.querySelector('.app');
        if (appEl) appEl.classList.remove('app--wide');
        const appFooter = document.querySelector('.app-footer');
        if (appFooter) appFooter.classList.remove('app-footer--results');
        // Reset stale state when arriving at a feature view from elsewhere
        if (view === 'create' && state.step !== 'input') {
            resetPlaylistState();
        }
        if (view === 'recommend' && state.rec.step !== 'prompt') {
            resetRecState();
        }
    }
    if (modeChanged) {
        state.step = 'input';
        updateMode();
        updateStep();
    }
    if (view === 'settings') {
        loadSettings();
    } else if (view === 'recommend') {
        initRecommendView();
    } else if (view === 'home') {
        renderHistoryFeed();
    }
}

async function loadSavedResult(resultId) {
    try {
        const data = await apiCall(`/results/${encodeURIComponent(resultId)}`);

        if (data.type === 'album_recommendation') {
            // Populate recommend state from snapshot
            state.view = 'recommend';
            state.rec.recommendations = data.snapshot.recommendations || [];
            state.rec.tokenCount = data.snapshot.token_count || 0;
            state.rec.estimatedCost = data.snapshot.estimated_cost || 0;
            state.rec.researchWarning = data.snapshot.research_warning || null;
            state.rec.prompt = data.prompt;
            state.rec.step = 'results';
            state.rec.loading = false;

            updateView();
            updateRecStep();
            renderRecResults();
        } else {
            // prompt_playlist or seed_playlist — populate playlist state
            state.view = 'create';
            state.mode = data.type === 'seed_playlist' ? 'seed' : 'prompt';
            state.step = 'results';

            const snapshot = data.snapshot;
            state.playlist = snapshot.tracks || [];
            state.playlistTitle = snapshot.playlist_title || data.title;
            state.narrative = snapshot.narrative || '';
            state.trackReasons = snapshot.track_reasons || {};
            state.playlistName = snapshot.playlist_title || data.title;
            state.tokenCount = snapshot.token_count || 0;
            state.estimatedCost = snapshot.estimated_cost || 0;
            state.selectedTrackKey = null;

            updateView();
            updateMode();
            updateStep();
            updatePlaylist();
        }

        window.scrollTo(0, 0);
    } catch (e) {
        // Result not found or deleted — show home with message
        console.warn('Failed to load saved result:', e);
        state.view = 'home';
        history.replaceState(null, '', '#home');
        updateView();
        showError('This result is no longer available.');
    }
}

// =============================================================================
// History Feed
// =============================================================================

/** In-memory cache of history items + pagination metadata */
let _historyCache = { items: [], total: 0, loaded: false, stale: true };
let _historyFilter = 'all'; // 'all' | 'playlists' | 'albums'
let _historyDeleteConfirm = null; // { id, el, timeout }

/** Mark history as needing re-fetch (called after a result is saved) */
function markHistoryStale() {
    _historyCache.stale = true;
}

/** Relative timestamp for history cards */
function relativeTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    const diffDay = Math.floor(diffMs / 86400000);

    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    if (diffDay === 1) return 'Yesterday';
    if (diffDay < 7) {
        return date.toLocaleDateString(undefined, { weekday: 'long' });
    }
    // Same year → "Feb 12"; different year → "Feb 2025"
    if (date.getFullYear() === now.getFullYear()) {
        return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }
    return date.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
}

/** Date group label for a given ISO timestamp */
function dateGroupLabel(isoString) {
    const date = new Date(isoString);
    const now = new Date();

    // Today
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (date >= todayStart) return 'Today';

    // Yesterday
    const yesterdayStart = new Date(todayStart);
    yesterdayStart.setDate(yesterdayStart.getDate() - 1);
    if (date >= yesterdayStart) return 'Yesterday';

    // Earlier this week (same ISO week)
    const dayOfWeek = now.getDay() || 7; // Monday = 1
    const weekStart = new Date(todayStart);
    weekStart.setDate(weekStart.getDate() - dayOfWeek + 1);
    if (date >= weekStart) return 'Earlier this week';

    // Same year → "Month Day"; different year → "Month Year"
    if (date.getFullYear() === now.getFullYear()) {
        return date.toLocaleDateString(undefined, { month: 'long', day: 'numeric' });
    }
    return date.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
}

/** Icon for result type (inline SVGs at 16x16, matching Lucide home-card icons) */
function historyIcon(type) {
    const attrs = 'xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
    if (type === 'album_recommendation') return `<svg ${attrs}><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="2"/></svg>`;
    if (type === 'seed_playlist') return `<svg ${attrs}><path d="M14 9.536V7a4 4 0 0 1 4-4h1.5a.5.5 0 0 1 .5.5V5a4 4 0 0 1-4 4 4 4 0 0 0-4 4c0 2 1 3 1 5a5 5 0 0 1-1 3"/><path d="M4 9a5 5 0 0 1 8 4 5 5 0 0 1-8-4"/><path d="M5 21h14"/></svg>`;
    return `<svg ${attrs}><path d="M12 18V5"/><path d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4"/><path d="M17.598 6.5A3 3 0 1 0 12 5a3 3 0 1 0-5.598 1.5"/><path d="M17.997 5.125a4 4 0 0 1 2.526 5.77"/><path d="M18 18a4 4 0 0 0 2-7.464"/><path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517"/><path d="M6 18a4 4 0 0 1-2-7.464"/><path d="M6.003 5.125a4 4 0 0 0-2.526 5.77"/></svg>`;
}

/** Icon title for result type */
function historyIconTitle(type) {
    if (type === 'album_recommendation') return 'Album recommendation';
    if (type === 'seed_playlist') return 'Playlist from seed';
    return 'Playlist from prompt';
}

/** Scrub date suffix from playlist titles (e.g., "Title - Feb 2026") */
function scrubDateSuffix(title) {
    return title.replace(/ - (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}$/, '');
}

/** Check if item passes the current filter */
function passesHistoryFilter(item) {
    if (_historyFilter === 'all') return true;
    if (_historyFilter === 'playlists') return item.type !== 'album_recommendation';
    if (_historyFilter === 'albums') return item.type === 'album_recommendation';
    return true;
}

/** Render the history feed from cached data */
function renderHistoryFeedFromCache() {
    const container = document.getElementById('history-feed');
    if (!container) return;

    const items = _historyCache.items;

    // Empty state
    if (items.length === 0) {
        container.innerHTML = '<div class="history-empty">Your playlist and album history will appear here</div>';
        return;
    }

    // Count by type for filter chips
    const playlistCount = items.filter(i => i.type !== 'album_recommendation').length;
    const albumCount = items.filter(i => i.type === 'album_recommendation').length;

    // Build HTML
    let html = '';

    // Filter chips
    html += '<div class="history-filters" role="group" aria-label="Filter history by type">';
    html += `<button class="filter-chip${_historyFilter === 'all' ? ' selected' : ''}" data-hfilter="all">All <span class="filter-chip-count">${items.length}</span></button>`;
    html += `<button class="filter-chip${_historyFilter === 'playlists' ? ' selected' : ''}" data-hfilter="playlists">Playlists <span class="filter-chip-count">${playlistCount}</span></button>`;
    html += `<button class="filter-chip${_historyFilter === 'albums' ? ' selected' : ''}" data-hfilter="albums">Albums <span class="filter-chip-count">${albumCount}</span></button>`;
    html += '</div>';

    // Group by date
    let lastGroup = null;
    let idx = 0;
    for (const item of items) {
        const visible = passesHistoryFilter(item);
        const display = visible ? '' : ' style="display:none"';
        const group = dateGroupLabel(item.created_at);

        if (group !== lastGroup) {
            // Check if this group has any visible items
            const groupHasVisible = items.some(
                i => dateGroupLabel(i.created_at) === group && passesHistoryFilter(i)
            );
            const headerStyle = groupHasVisible
                ? `animation-delay:${idx * 30}ms`
                : `display:none;animation-delay:${idx * 30}ms`;
            html += `<div class="date-group-header" style="${headerStyle}">${escapeHtml(group)}</div>`;
            lastGroup = group;
        }

        const title = item.type === 'album_recommendation'
            ? escapeHtml(item.title)
            : escapeHtml(scrubDateSuffix(item.title));

        const artistSpan = item.artist && item.type !== 'album_recommendation'
            ? ` <span class="history-card-artist">${escapeHtml(item.artist)}</span>`
            : '';

        const subtitle = item.subtitle
            ? escapeHtml(item.subtitle)
            : (item.prompt ? escapeHtml(item.prompt) : '');

        html += `<div class="history-card" data-result-id="${escapeHtml(item.id)}" data-type="${escapeHtml(item.type)}"${display} style="animation-delay:${idx * 30}ms">`;
        html += `  <div class="history-card-icon" title="${historyIconTitle(item.type)}">${historyIcon(item.type)}</div>`;
        html += `  <div class="history-card-body">`;
        html += `    <div class="history-card-title">${title}${artistSpan}</div>`;
        html += `    <div class="history-card-subtitle">${subtitle}</div>`;
        html += `  </div>`;
        html += `  <span class="history-card-time">${relativeTime(item.created_at)}</span>`;
        html += `  <button class="history-card-delete" aria-label="Delete" title="Delete">&times;</button>`;
        html += '</div>';
        idx++;
    }

    // Load more button
    if (_historyCache.items.length < _historyCache.total) {
        html += '<div class="history-load-more"><button class="load-more-btn">Load more</button></div>';
    }

    container.innerHTML = html;
}

/** Fetch and render the history feed */
async function renderHistoryFeed() {
    const container = document.getElementById('history-feed');
    if (!container) return;

    // Use cache if fresh
    if (_historyCache.loaded && !_historyCache.stale) {
        renderHistoryFeedFromCache();
        return;
    }

    try {
        const data = await apiCall('/results?limit=20');
        _historyCache.items = data.results || [];
        _historyCache.total = data.total || 0;
        _historyCache.loaded = true;
        _historyCache.stale = false;
        _historyFilter = 'all';

        renderHistoryFeedFromCache();
    } catch (e) {
        console.warn('Failed to load history:', e);
        container.innerHTML = '<div class="history-empty">Could not load history</div>';
    }
}

/** Load more history items */
async function loadMoreHistory() {
    try {
        const offset = _historyCache.items.length;
        const data = await apiCall(`/results?limit=20&offset=${offset}`);
        const newItems = data.results || [];
        _historyCache.items.push(...newItems);
        _historyCache.total = data.total || _historyCache.total;
        renderHistoryFeedFromCache();
    } catch (e) {
        console.warn('Failed to load more history:', e);
    }
}

/** Handle filter chip clicks */
function handleHistoryFilterClick(filter) {
    _historyFilter = filter;
    renderHistoryFeedFromCache();
}

/** Reset a delete button from confirming state back to × */
function resetDeleteConfirm() {
    if (!_historyDeleteConfirm) return;
    clearTimeout(_historyDeleteConfirm.timeout);
    const btn = _historyDeleteConfirm.el;
    btn.classList.remove('confirming');
    btn.textContent = '×';
    btn.setAttribute('aria-label', 'Delete');
    _historyDeleteConfirm = null;
}

/** Handle two-step inline delete confirmation */
function handleHistoryDelete(resultId, deleteBtn) {
    // If this button is already confirming → execute the delete
    if (_historyDeleteConfirm && _historyDeleteConfirm.id === resultId) {
        resetDeleteConfirm();
        finalizeHistoryDelete(resultId);
        return;
    }

    // Reset any other card's confirming state
    resetDeleteConfirm();

    // Enter confirming state on this button
    deleteBtn.classList.add('confirming');
    deleteBtn.textContent = 'Delete?';
    deleteBtn.setAttribute('aria-label', 'Confirm delete');

    const timeout = setTimeout(() => {
        if (_historyDeleteConfirm && _historyDeleteConfirm.id === resultId) {
            resetDeleteConfirm();
        }
    }, 3000);

    _historyDeleteConfirm = { id: resultId, el: deleteBtn, timeout };
}

/** Actually delete the result via API and remove from cache */
function finalizeHistoryDelete(resultId) {
    // Optimistically remove from cache and re-render
    _historyCache.items = _historyCache.items.filter(i => i.id !== resultId);
    _historyCache.total = Math.max(0, _historyCache.total - 1);
    _historyDeleteConfirm = null;
    renderHistoryFeedFromCache();

    // Fire the server delete; restore cache on failure
    fetch(`/api/results/${encodeURIComponent(resultId)}`, { method: 'DELETE' })
        .then(resp => {
            if (!resp.ok && resp.status !== 404) {
                showError('Failed to delete item');
                _historyCache.stale = true;
                _historyCache.items = [];
                renderHistoryFeed();
            }
        })
        .catch(() => {
            showError('Failed to delete item');
            _historyCache.stale = true;
            _historyCache.items = [];
            renderHistoryFeed();
        });
}


/** Set up event delegation for history feed clicks */
function setupHistoryEventListeners() {
    const container = document.getElementById('history-feed');
    if (!container) return;

    container.addEventListener('click', (e) => {
        // Filter chip
        const chip = e.target.closest('.filter-chip');
        if (chip) {
            handleHistoryFilterClick(chip.dataset.hfilter);
            return;
        }

        // Delete button (two-step confirm)
        const deleteBtn = e.target.closest('.history-card-delete');
        if (deleteBtn) {
            e.stopPropagation();
            const card = deleteBtn.closest('.history-card');
            if (card) handleHistoryDelete(card.dataset.resultId, deleteBtn);
            return;
        }

        // Load more
        const loadMore = e.target.closest('.load-more-btn');
        if (loadMore) {
            loadMoreHistory();
            return;
        }

        // Card click → navigate to result
        const card = e.target.closest('.history-card');
        if (card && card.dataset.resultId) {
            location.hash = `#result/${card.dataset.resultId}`;
        }
    });
}

function updateView() {
    // Update views
    document.querySelectorAll('.view').forEach(view => {
        view.classList.toggle('active', view.id === `${state.view}-view`);
    });

    // Update nav active states
    const trigger = document.querySelector('.nav-dropdown-trigger');
    const isPlaylist = state.view === 'create';
    trigger.classList.toggle('active', isPlaylist);

    // Update dropdown checkmarks
    document.querySelectorAll('.nav-dropdown-item').forEach(item => {
        const hash = item.dataset.nav;
        const isSelected = isPlaylist && (
            (hash === 'playlist-prompt' && state.mode === 'prompt') ||
            (hash === 'playlist-seed' && state.mode === 'seed')
        );
        item.querySelector('.nav-check').textContent = isSelected ? '\u2713' : '';
        item.classList.toggle('selected', isSelected);
    });

    // Update flat nav buttons
    document.querySelectorAll('.nav-btn[data-nav]').forEach(btn => {
        const hash = btn.dataset.nav;
        const isActive = (hash === 'recommend-album' && state.view === 'recommend') ||
                         (hash === 'settings' && state.view === 'settings');
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-current', isActive ? 'true' : 'false');
    });
}

function updateMode() {
    // Update step panels visibility
    const inputPrompt = document.getElementById('step-input-prompt');
    const inputSeed = document.getElementById('step-input-seed');

    if (state.step === 'input') {
        inputPrompt.classList.toggle('active', state.mode === 'prompt');
        inputSeed.classList.toggle('active', state.mode === 'seed');
    }

    // Update step progress - show refine for prompt mode, dimensions for seed mode
    const refineStep = document.querySelector('#playlist-steps .step[data-step="refine"]');
    const refineConnector = refineStep?.previousElementSibling;
    const dimensionsStep = document.querySelector('#playlist-steps .step[data-step="dimensions"]');
    const dimensionsConnector = dimensionsStep?.previousElementSibling;
    if (state.mode === 'prompt') {
        refineStep?.classList.remove('hidden');
        refineConnector?.classList.remove('hidden');
        dimensionsStep?.classList.add('hidden');
        dimensionsConnector?.classList.add('hidden');
    } else {
        refineStep?.classList.add('hidden');
        refineConnector?.classList.add('hidden');
        dimensionsStep?.classList.remove('hidden');
        dimensionsConnector?.classList.remove('hidden');
    }

    // Renumber visible steps
    let stepNumber = 1;
    document.querySelectorAll('#playlist-steps .step').forEach(step => {
        if (!step.classList.contains('hidden')) {
            step.querySelector('.step-circle').textContent = stepNumber++;
        }
    });

    // Update first step label based on mode
    const inputLabel = document.getElementById('step-label-input');
    if (inputLabel) {
        inputLabel.textContent = state.mode === 'seed' ? 'Seed' : 'Prompt';
    }
}

function updateStep() {
    window.scrollTo(0, 0);

    const isResults = state.step === 'results';

    // Hide step progress on results step
    const stepProgress = document.getElementById('playlist-steps');
    if (stepProgress) stepProgress.style.display = isResults ? 'none' : '';

    // Toggle wide layout for results
    const appEl = document.querySelector('.app');
    if (appEl) appEl.classList.toggle('app--wide', isResults);

    // Toggle footer content for results vs other screens
    const appFooter = document.querySelector('.app-footer');
    if (appFooter) appFooter.classList.toggle('app-footer--results', isResults);

    // Clear any inline hide from album view so CSS class can control visibility
    const regenBtn = document.getElementById('regenerate-btn');
    if (regenBtn) regenBtn.style.display = '';

    // Steps array is mode-dependent: prompt uses refine, seed uses dimensions
    const steps = state.mode === 'prompt'
        ? ['input', 'refine', 'filters', 'results']
        : ['input', 'dimensions', 'filters', 'results'];
    const currentIndex = steps.indexOf(state.step);

    document.querySelectorAll('#playlist-steps .step').forEach((stepEl, index) => {
        const stepName = stepEl.dataset.step;
        const stepIndex = steps.indexOf(stepName);
        const isActive = stepName === state.step;

        stepEl.classList.toggle('active', isActive);
        stepEl.classList.toggle('completed', stepIndex >= 0 && stepIndex < currentIndex);

        // Update ARIA state for screen readers
        if (isActive) {
            stepEl.setAttribute('aria-current', 'step');
        } else {
            stepEl.removeAttribute('aria-current');
        }
    });

    // Update connectors — only count visible ones
    let connectorIdx = 0;
    document.querySelectorAll('#playlist-steps .step-connector').forEach(connector => {
        if (!connector.classList.contains('hidden')) {
            connector.classList.toggle('completed', connectorIdx < currentIndex);
            connectorIdx++;
        }
    });

    // Update step panels
    document.querySelectorAll('.step-panel').forEach(panel => {
        panel.classList.remove('active');
    });

    if (state.step === 'input') {
        if (state.mode === 'prompt') {
            document.getElementById('step-input-prompt').classList.add('active');
        } else {
            document.getElementById('step-input-seed').classList.add('active');
        }
    } else if (state.step === 'refine') {
        document.getElementById('step-refine').classList.add('active');
    } else if (state.step === 'dimensions') {
        document.getElementById('step-dimensions').classList.add('active');
    } else if (state.step === 'filters') {
        document.getElementById('step-filters').classList.add('active');
    } else if (state.step === 'results') {
        document.getElementById('step-results').classList.add('active');
    }
}

function updateFilters() {
    // Remember which chip had focus so we can restore it after re-render
    const focused = document.activeElement;
    const focusedGenre = focused?.dataset?.genre;
    const focusedDecade = focused?.dataset?.decade;

    // Update genre chips
    const genreContainer = document.getElementById('genre-chips');
    genreContainer.innerHTML = state.availableGenres.map(genre => {
        const isSelected = state.selectedGenres.includes(genre.name);
        return `
        <button class="chip ${isSelected ? 'selected' : ''}"
                data-genre="${escapeHtml(genre.name)}"
                aria-pressed="${isSelected}">
            ${escapeHtml(genre.name)}
            ${genre.count != null ? `<span class="chip-count">${genre.count}</span>` : ''}
        </button>
    `}).join('');

    // Sync genre toggle label
    const genreToggle = document.getElementById('genre-toggle-all');
    if (genreToggle) {
        const allSelected = allGenresSelected();
        genreToggle.textContent = allSelected ? 'Deselect All' : 'Select All';
        genreToggle.setAttribute('aria-label',
            allSelected ? 'Deselect all genres' : 'Select all genres');
    }

    // Update decade chips
    const decadeContainer = document.getElementById('decade-chips');
    decadeContainer.innerHTML = state.availableDecades.map(decade => {
        const isSelected = state.selectedDecades.includes(decade.name);
        return `
        <button class="chip ${isSelected ? 'selected' : ''}"
                data-decade="${escapeHtml(decade.name)}"
                aria-pressed="${isSelected}">
            ${escapeHtml(decade.name)}
            ${decade.count != null ? `<span class="chip-count">${decade.count}</span>` : ''}
        </button>
    `}).join('');

    // Sync decade toggle label
    const decadeToggle = document.getElementById('decade-toggle-all');
    if (decadeToggle) {
        const allSelected = allDecadesSelected();
        decadeToggle.textContent = allSelected ? 'Deselect All' : 'Select All';
        decadeToggle.setAttribute('aria-label',
            allSelected ? 'Deselect all decades' : 'Select all decades');
    }

    // Restore focus to the chip that was active before re-render
    if (focusedGenre) {
        genreContainer.querySelector(`[data-genre="${CSS.escape(focusedGenre)}"]`)?.focus();
    } else if (focusedDecade) {
        decadeContainer.querySelector(`[data-decade="${CSS.escape(focusedDecade)}"]`)?.focus();
    }

    // Update track count buttons
    document.querySelectorAll('.count-btn').forEach(btn => {
        const isActive = parseInt(btn.dataset.count) === state.trackCount;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });

    // Update max tracks to AI buttons
    const maxAllowed = state.config?.max_tracks_to_ai || 3500;
    document.querySelectorAll('.limit-btn').forEach(btn => {
        const limit = parseInt(btn.dataset.limit);
        const isActive = limit === state.maxTracksToAI ||
            (limit === 0 && state.maxTracksToAI >= maxAllowed);
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });

    // Update checkboxes
    document.getElementById('exclude-live').checked = state.excludeLive;

    // Update rating buttons — hide entirely for Jellyfin (no rating support)
    const ratingSection = document.getElementById('filter-rating-section');
    const isJellyfin = state.config?.media_server === 'jellyfin';
    if (ratingSection) ratingSection.classList.toggle('hidden', isJellyfin);
    if (isJellyfin) {
        state.minRating = 0;
    } else {
        document.querySelectorAll('.rating-btn').forEach(btn => {
            const isActive = parseInt(btn.dataset.rating) === state.minRating;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        });
    }
}

function updateModelSuggestion() {
    const suggestion = document.getElementById('gemini-suggestion');
    if (!suggestion || !state.config) return;

    const provider = state.config.llm_provider;
    const maxTracks = state.config.max_tracks_to_ai || 3500;
    const isLocalProvider = state.config.is_local_provider;

    // Cloud provider baselines for comparison
    const ANTHROPIC_MAX = 3500;  // ~200K context
    const GEMINI_MAX = 18000;    // ~1M context

    if (isLocalProvider && maxTracks < ANTHROPIC_MAX) {
        // Local model with small context - suggest a more powerful model
        suggestion.textContent = 'Switch to a model with a larger context window in Settings for higher track limits.';
        suggestion.classList.remove('hidden');
    } else if (!isLocalProvider && provider !== 'gemini') {
        // Cloud provider that isn't Gemini - suggest Gemini specifically
        const multiplier = provider === 'openai' ? '8x' : '5x';
        suggestion.textContent = `Switch to Gemini in Settings for ${multiplier} higher track limits.`;
        suggestion.classList.remove('hidden');
    } else {
        // Using Gemini or a local model with large context - no suggestion needed
        suggestion.classList.add('hidden');
    }
}

function updateTrackLimitButtons() {
    const container = document.querySelector('.track-limit-selector');
    if (!container || !state.config) return;

    updateModelSuggestion();

    const maxAllowed = state.config.max_tracks_to_ai || 3500;

    // Generate sensible limit options based on model capacity
    const options = [];

    // Always include some standard options that are below the max
    const standardOptions = [100, 250, 500, 1000, 2000, 5000, 10000, 18000];
    for (const opt of standardOptions) {
        if (opt <= maxAllowed) {
            options.push(opt);
        }
    }

    // Add "No limit" option (which means use model's max)
    options.push(0);

    // Render buttons
    container.innerHTML = options.map(limit => {
        const isActive = limit === state.maxTracksToAI ||
            (limit === 0 && state.maxTracksToAI >= maxAllowed);
        const label = limit === 0 ? `Max (${maxAllowed.toLocaleString()})` : limit.toLocaleString();
        return `<button class="limit-btn ${isActive ? 'active' : ''}" data-limit="${limit}">${label}</button>`;
    }).join('');

    // Re-attach event listeners (local recalculation - no API call needed)
    container.querySelectorAll('.limit-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            // Update active state visually
            container.querySelectorAll('.limit-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const limit = parseInt(btn.dataset.limit);
            state.maxTracksToAI = limit === 0 ? maxAllowed : limit;
            updateFilters();
            recalculateCostDisplay();
        });
    });
}

function updateAlbumLimitButtons() {
    const container = document.querySelector('.album-limit-selector');
    if (!container || !state.config) return;

    updateRecModelSuggestion();

    const maxAllowed = state.config.max_albums_to_ai || 2500;

    // Generate options filtered by model capacity
    const options = [];
    const standardOptions = [1000, 2500, 5000, 10000, 35000];
    for (const opt of standardOptions) {
        if (opt <= maxAllowed) {
            options.push(opt);
        }
    }

    // Add "Max" option (uses model's max)
    options.push(0);

    // Render buttons
    container.innerHTML = options.map(limit => {
        const isActive = limit === state.rec.maxAlbumsToAI ||
            (limit === 0 && state.rec.maxAlbumsToAI >= maxAllowed);
        const label = limit === 0 ? `Max (${maxAllowed.toLocaleString()})` : limit.toLocaleString();
        return `<button class="limit-btn ${isActive ? 'active' : ''}" data-limit="${limit}">${label}</button>`;
    }).join('');

    // Clamp current selection to what the model supports
    if (state.rec.maxAlbumsToAI > maxAllowed) {
        state.rec.maxAlbumsToAI = maxAllowed;
    }

    // Re-attach event listeners
    container.querySelectorAll('.limit-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.limit-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const limit = parseInt(btn.dataset.limit);
            state.rec.maxAlbumsToAI = limit === 0 ? maxAllowed : limit;
            updateRecAlbumPreview();
        });
    });
}

function updateRecModelSuggestion() {
    const suggestion = document.getElementById('rec-gemini-suggestion');
    if (!suggestion || !state.config) return;

    const provider = state.config.llm_provider;
    const maxAlbums = state.config.max_albums_to_ai || 2500;
    const isLocalProvider = state.config.is_local_provider;

    const ANTHROPIC_MAX_ALBUMS = 7100;
    const GEMINI_MAX_ALBUMS = 35900;

    if (isLocalProvider && maxAlbums < ANTHROPIC_MAX_ALBUMS) {
        suggestion.textContent = 'Switch to a model with a larger context window in Settings for higher album limits.';
        suggestion.classList.remove('hidden');
    } else if (!isLocalProvider && provider !== 'gemini') {
        const multiplier = provider === 'openai' ? '8x' : '5x';
        suggestion.textContent = `Switch to Gemini in Settings for ${multiplier} higher album limits.`;
        suggestion.classList.remove('hidden');
    } else {
        suggestion.classList.add('hidden');
    }
}

// AbortController for cancelling in-flight filter preview requests
let filterPreviewController = null;
let filterPreviewLoadingTimeout = null;

async function updateFilterPreview() {
    console.log('[MediaSage] updateFilterPreview called');
    const previewTracks = document.getElementById('preview-tracks');
    const previewCost = document.getElementById('preview-cost');

    // Cancel any in-flight request
    if (filterPreviewController) {
        filterPreviewController.abort();
    }
    filterPreviewController = new AbortController();

    // Clear any pending loading timeout
    if (filterPreviewLoadingTimeout) {
        clearTimeout(filterPreviewLoadingTimeout);
    }

    // Only show loading state if request takes longer than 150ms
    filterPreviewLoadingTimeout = setTimeout(() => {
        previewTracks.innerHTML = '<span class="preview-spinner"></span> Counting...';
        previewCost.textContent = '';
    }, 150);

    try {
        // All selected = no filter (avoids excluding untagged tracks)
        const requestBody = {
            genres: allGenresSelected() ? [] : state.selectedGenres,
            decades: allDecadesSelected() ? [] : state.selectedDecades,
            track_count: state.trackCount,
            max_tracks_to_ai: state.maxTracksToAI,
            min_rating: state.minRating,
            exclude_live: state.excludeLive,
        };
        console.log('[MediaSage] Filter preview request:', requestBody);

        const response = await fetch('/api/filter/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
            signal: filterPreviewController.signal,
        });

        if (!response.ok) {
            throw new Error('Failed to get filter preview');
        }

        const data = await response.json();
        console.log('[MediaSage] Filter preview response:', data);

        // Clear loading timeout - response arrived fast
        clearTimeout(filterPreviewLoadingTimeout);

        // Cache the matching_tracks for local recalculation
        state.lastFilterPreview = {
            matching_tracks: data.matching_tracks,
        };

        // Update display
        updateFilterPreviewDisplay(data.matching_tracks, data.tracks_to_send, data.estimated_cost);
    } catch (error) {
        // Clear loading timeout on error too
        clearTimeout(filterPreviewLoadingTimeout);

        // Ignore abort errors - they're expected when cancelling
        if (error.name === 'AbortError') {
            console.log('[MediaSage] Filter preview request cancelled');
            return;
        }
        console.error('Filter preview error:', error);
        previewTracks.textContent = '-- matching tracks';
        previewCost.textContent = 'Est. cost: --';
    }
}

function updateFilterPreviewDisplay(matchingTracks, tracksToSend, estimatedCost) {
    const previewTracks = document.getElementById('preview-tracks');
    const previewCost = document.getElementById('preview-cost');

    // Update track count display
    let trackText;
    if (matchingTracks >= 0) {
        if (tracksToSend < matchingTracks) {
            trackText = `${matchingTracks.toLocaleString()} tracks (sending ${tracksToSend.toLocaleString()} to AI, selected randomly)`;
        } else {
            trackText = `${matchingTracks.toLocaleString()} tracks`;
        }
    } else {
        trackText = 'Unknown';
    }
    previewTracks.textContent = trackText;

    // For local providers, hide cost estimate (show tokens only)
    const isLocalProvider = state.config?.is_local_provider ?? false;
    if (matchingTracks < 0) {
        previewCost.textContent = isLocalProvider ? '' : 'Est. cost: --';
    } else if (isLocalProvider) {
        // Don't show cost for local providers
        previewCost.textContent = '';
    } else {
        previewCost.textContent = `Est. cost: $${estimatedCost.toFixed(4)}`;
    }

    // Update "All/Max" button label based on whether filtered tracks fit in context
    const maxBtn = document.querySelector('.limit-btn[data-limit="0"]');
    if (maxBtn && state.config) {
        const maxAllowed = state.config.max_tracks_to_ai || 3500;
        maxBtn.textContent = matchingTracks <= maxAllowed ? 'All' : `Max (${maxAllowed.toLocaleString()})`;
    }
}

function recalculateCostDisplay() {
    // Recalculate cost locally without API call (for track_count/max_tracks changes)
    if (!state.lastFilterPreview || !state.config) return;

    // If cost rates aren't available (old config), fall back to API call
    if (state.config.cost_per_million_input === undefined) {
        updateFilterPreview();
        return;
    }

    const { matching_tracks } = state.lastFilterPreview;
    const maxAllowed = state.config.max_tracks_to_ai || 3500;

    // Calculate tracks_to_send
    let tracks_to_send;
    if (matching_tracks <= 0) {
        tracks_to_send = 0;
    } else if (state.maxTracksToAI === 0 || state.maxTracksToAI >= maxAllowed) {
        // "Max" mode - send up to model's limit
        tracks_to_send = Math.min(matching_tracks, maxAllowed);
    } else {
        tracks_to_send = Math.min(matching_tracks, state.maxTracksToAI);
    }

    // Cost formula (matches backend: separate rates for analysis + generation models)
    const analysis_input = 1100;
    const analysis_output = 300;
    const gen_input = tracks_to_send * 40;
    const gen_output = state.trackCount * 60;

    // Analysis model cost (e.g. Sonnet)
    const analysis_in_rate = state.config.analysis_cost_per_million_input ?? state.config.cost_per_million_input;
    const analysis_out_rate = state.config.analysis_cost_per_million_output ?? state.config.cost_per_million_output;
    const analysis_cost = (analysis_input / 1_000_000) * analysis_in_rate + (analysis_output / 1_000_000) * analysis_out_rate;

    // Generation model cost (e.g. Haiku)
    const gen_cost = (gen_input / 1_000_000) * state.config.cost_per_million_input + (gen_output / 1_000_000) * state.config.cost_per_million_output;

    const estimated_cost = analysis_cost + gen_cost;

    updateFilterPreviewDisplay(matching_tracks, tracks_to_send, estimated_cost);
}

function renderNarrativeBox() {
    const container = document.getElementById('narrative-box');
    if (!container) return;

    if (!state.narrative) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');

    container.innerHTML = `
        <p class="narrative-text">${escapeHtml(state.narrative)}</p>
    `;

    // Update prompt pill
    const promptPill = document.getElementById('results-prompt-pill');
    if (promptPill) {
        if (state.userRequest) {
            promptPill.textContent = `\u{1F4AC} "${state.userRequest}"`;
            promptPill.classList.remove('hidden');
        } else {
            promptPill.classList.add('hidden');
        }
    }
}

function showTrackReason(ratingKey) {
    const panel = document.getElementById('track-reason-panel');
    if (!panel) return;

    const placeholder = panel.querySelector('.reason-placeholder');
    const content = panel.querySelector('.reason-content');

    if (!ratingKey) {
        // Show placeholder
        placeholder.classList.remove('hidden');
        content.classList.add('hidden');
        return;
    }

    // Find track in playlist
    const track = state.playlist.find(t => t.rating_key === ratingKey);
    if (!track) return;

    // Get reason for this track
    const reason = state.trackReasons[ratingKey] || 'Selected for this playlist';

    // Update album art
    const artContainer = panel.querySelector('.reason-album-art-container');
    if (artContainer) {
        if (track.art_url) {
            artContainer.innerHTML = `<img class="reason-album-art" src="${escapeHtml(track.art_url)}" alt="${escapeHtml(track.album)} album art" data-artist="${escapeHtml(track.artist || '')}" onerror="this.outerHTML=artPlaceholderHtml(this.dataset.artist,true)">`;
        } else {
            artContainer.innerHTML = artPlaceholderHtml(track.artist, true);
        }
        artContainer.style.display = '';
    }

    // Update panel content
    panel.querySelector('.reason-track-title').textContent = track.title;
    panel.querySelector('.reason-track-artist').textContent = `${track.artist} - ${track.album}`;
    panel.querySelector('.reason-text').textContent = reason;

    // Show content, hide placeholder
    placeholder.classList.add('hidden');
    content.classList.remove('hidden');
}

function selectTrack(ratingKey) {
    state.selectedTrackKey = ratingKey;

    // Toggle .selected class on track rows
    document.querySelectorAll('.playlist-track').forEach(el => {
        const isSelected = el.dataset.ratingKey === ratingKey;
        el.classList.toggle('selected', isSelected);
        el.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    });

    // Update detail panel
    showTrackReason(ratingKey);
}

function isMobileView() {
    return window.innerWidth <= 768;
}

function openBottomSheet(ratingKey) {
    const sheet = document.getElementById('bottom-sheet');
    if (!sheet) return;

    // Find track in playlist
    const track = state.playlist.find(t => t.rating_key === ratingKey);
    if (!track) return;

    // Get reason for this track
    const reason = state.trackReasons[ratingKey] || 'Selected for this playlist';

    // Update content
    sheet.querySelector('.bottom-sheet-track-title').textContent = track.title;
    sheet.querySelector('.bottom-sheet-track-artist').textContent = `${track.artist} - ${track.album}`;
    sheet.querySelector('.bottom-sheet-reason').textContent = reason;

    // Show sheet
    sheet.classList.remove('hidden');
    focusManager.openModal(sheet);
    lockScroll();
}

function closeBottomSheet() {
    const sheet = document.getElementById('bottom-sheet');
    if (!sheet) return;

    sheet.classList.add('hidden');
    removeNoScrollIfNoModals();
    focusManager.closeModal(sheet);
}

function updatePlaylist() {
    // Render narrative box
    renderNarrativeBox();

    const container = document.getElementById('playlist-tracks');
    container.innerHTML = state.playlist.map((track, index) => `
        <div class="playlist-track" role="option" tabindex="0"
             data-rating-key="${escapeHtml(track.rating_key)}"
             aria-selected="false"
             aria-label="${escapeHtml(track.title)} by ${escapeHtml(track.artist)}">
            <span class="track-number">${index + 1}</span>
            ${trackArtHtml(track)}
            <div class="track-info">
                <div class="track-title">${escapeHtml(track.title)}</div>
                <div class="track-artist">${escapeHtml(track.artist)} - ${escapeHtml(track.album)}</div>
            </div>
            <button class="track-remove" tabindex="0" data-rating-key="${escapeHtml(track.rating_key)}"
                    aria-label="Remove ${escapeHtml(track.title)}">&times;</button>
        </div>
    `).join('');

    // Click handlers: desktop = select track, mobile = open bottom sheet
    container.querySelectorAll('.playlist-track').forEach(trackEl => {
        trackEl.addEventListener('click', (e) => {
            if (e.target.closest('.track-remove')) return;
            if (isMobileView()) {
                openBottomSheet(trackEl.dataset.ratingKey);
            } else {
                selectTrack(trackEl.dataset.ratingKey);
            }
        });

        // Keyboard: Enter/Space to select
        trackEl.addEventListener('keydown', (e) => {
            if (e.target.closest('.track-remove')) return;
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                if (isMobileView()) {
                    openBottomSheet(trackEl.dataset.ratingKey);
                } else {
                    selectTrack(trackEl.dataset.ratingKey);
                }
            }
        });
    });

    // Auto-select: restore previous selection or pick first track (desktop)
    if (!isMobileView() && state.playlist.length > 0) {
        const hasSelected = state.selectedTrackKey &&
            state.playlist.some(t => t.rating_key === state.selectedTrackKey);
        if (hasSelected) {
            selectTrack(state.selectedTrackKey);
        } else {
            selectTrack(state.playlist[0].rating_key);
        }
    } else if (state.playlist.length === 0) {
        state.selectedTrackKey = null;
        showTrackReason(null);
    }

    // Update footer
    updateResultsFooter();

    // Update playlist name input
    document.getElementById('playlist-name-input').value = state.playlistName;
}

function updateResultsFooter() {
    const headerTrackCountEl = document.getElementById('results-track-count');
    const costDisplay = document.getElementById('cost-display');

    const count = state.playlist.length;

    // Update header track count
    const trackText = `\u266B ${count} track${count !== 1 ? 's' : ''}`;
    if (headerTrackCountEl) headerTrackCountEl.textContent = trackText;

    // Update cost display in app footer
    const isLocalProvider = state.config?.is_local_provider ?? false;
    if (costDisplay) {
        if (isLocalProvider) {
            costDisplay.textContent = `${state.tokenCount.toLocaleString()} tokens`;
        } else {
            costDisplay.textContent = `${state.tokenCount.toLocaleString()} tokens ($${state.estimatedCost.toFixed(4)})`;
        }
    }

    // Keep append track count in sync
    updateAppendTrackCount();
}

function updateSettings() {
    if (!state.config) return;

    // Media server selector
    const mediaServerSelect = document.getElementById('settings-media-server');
    if (mediaServerSelect) {
        mediaServerSelect.value = state.config.media_server || 'plex';
        _showMediaServerFields(state.config.media_server || 'plex');
    }

    document.getElementById('plex-url').value = state.config.plex_url || '';
    document.getElementById('music-library').value = state.config.media_server === 'jellyfin'
        ? (state.config.jellyfin_music_library || 'Music')
        : (state.config.music_library || 'Music');
    document.getElementById('llm-provider').value = state.config.llm_provider || 'gemini';

    // Jellyfin fields
    const jellyfinUrl = document.getElementById('jellyfin-url');
    if (jellyfinUrl) jellyfinUrl.value = state.config.jellyfin_url || '';
    const jellyfinLib = document.getElementById('jellyfin-music-library');
    if (jellyfinLib) jellyfinLib.value = state.config.jellyfin_music_library || 'Music';
    const jellyfinToken = document.getElementById('jellyfin-token');
    if (jellyfinToken) {
        jellyfinToken.placeholder = state.config.jellyfin_token_set
            ? '•••••••••••••••• (configured)'
            : 'Your Jellyfin API key';
    }

    // Show warning if provider is set by environment variable
    const providerEnvWarning = document.getElementById('provider-env-warning');
    if (providerEnvWarning) {
        providerEnvWarning.classList.toggle('hidden', !state.config.provider_from_env);
    }

    // Update token/key placeholders to indicate if configured
    const plexTokenInput = document.getElementById('plex-token');
    plexTokenInput.placeholder = state.config.plex_token_set
        ? '••••••••••••••••  (configured)'
        : 'Your Plex token';

    const llmApiKeyInput = document.getElementById('llm-api-key');
    llmApiKeyInput.placeholder = state.config.llm_api_key_set
        ? '••••••••••••••••  (configured)'
        : 'Your API key';

    // Update Ollama settings
    const ollamaUrl = document.getElementById('ollama-url');
    ollamaUrl.value = state.config.ollama_url || 'http://localhost:11434';

    // Update Custom provider settings
    const customUrl = document.getElementById('custom-url');
    const customApiKey = document.getElementById('custom-api-key');
    const customModel = document.getElementById('custom-model');
    const customContext = document.getElementById('custom-context-window');
    customUrl.value = state.config.custom_url || '';
    customApiKey.value = '';  // Never show actual key
    customApiKey.placeholder = state.config.llm_api_key_set && state.config.llm_provider === 'custom'
        ? '••••••••••••• (key saved)'
        : 'sk-... (optional)';
    customModel.value = state.config.model_analysis || '';  // Custom uses same model for both
    customContext.value = state.config.custom_context_window || 32768;

    // Update status indicators
    const plexStatus = document.getElementById('plex-status');
    if (plexStatus) {
        plexStatus.classList.toggle('connected', state.config.plex_connected);
        plexStatus.querySelector('.status-text').textContent =
            state.config.plex_connected ? 'Connected' : 'Not connected';
    }

    const jellyfinStatus = document.getElementById('jellyfin-status');
    if (jellyfinStatus) {
        const jellyfinConnected = !!(state.config.jellyfin_url && state.config.jellyfin_token_set);
        jellyfinStatus.classList.toggle('connected', jellyfinConnected);
        jellyfinStatus.querySelector('.status-text').textContent =
            jellyfinConnected ? 'Connected' : 'Not connected';
    }

    const llmStatus = document.getElementById('llm-status');
    if (llmStatus) {
        llmStatus.classList.toggle('connected', state.config.llm_configured);
        llmStatus.querySelector('.status-text').textContent =
            state.config.llm_configured ? 'Configured' : 'Not configured';
    }

    // Update Save button label
    const saveLabelLong = document.getElementById('save-playlist-btn-label');
    if (saveLabelLong) {
        saveLabelLong.textContent = state.config.media_server === 'jellyfin' ? 'Save to Jellyfin' : 'Save to Plex';
    }

    // Show/hide Play Now button (Plex-only feature)
    const playNowBtn = document.getElementById('play-now-btn');
    if (playNowBtn) {
        playNowBtn.style.display = state.config.media_server === 'jellyfin' ? 'none' : '';
    }

    // Show provider-specific settings
    showProviderSettings(state.config.llm_provider);
}

function _showMediaServerFields(server) {
    const plexFields = document.getElementById('settings-plex-fields');
    const jellyfinFields = document.getElementById('settings-jellyfin-fields');
    if (plexFields) plexFields.classList.toggle('hidden', server === 'jellyfin');
    if (jellyfinFields) jellyfinFields.classList.toggle('hidden', server !== 'jellyfin');
}

function _updateSetupServerFields(server) {
    const plexFields = document.getElementById('setup-plex-fields');
    const jellyfinFields = document.getElementById('setup-jellyfin-fields');
    if (plexFields) plexFields.classList.toggle('hidden', server === 'jellyfin');
    if (jellyfinFields) jellyfinFields.classList.toggle('hidden', server !== 'jellyfin');
}

function showProviderSettings(provider) {
    // Hide all provider-specific settings
    const cloudSettings = document.getElementById('cloud-provider-settings');
    const ollamaSettings = document.getElementById('ollama-settings');
    const customSettings = document.getElementById('custom-settings');

    cloudSettings.classList.add('hidden');
    ollamaSettings.classList.add('hidden');
    customSettings.classList.add('hidden');

    // Show the appropriate settings
    if (provider === 'ollama') {
        ollamaSettings.classList.remove('hidden');
        // Trigger Ollama status check if URL is set
        const ollamaUrl = document.getElementById('ollama-url').value.trim();
        if (ollamaUrl) {
            checkOllamaStatus(ollamaUrl);
        }
    } else if (provider === 'custom') {
        customSettings.classList.remove('hidden');
        updateCustomMaxTracks();
    } else {
        // Cloud providers (anthropic, openai, gemini)
        cloudSettings.classList.remove('hidden');
    }
}

async function checkOllamaStatus(url) {
    const statusEl = document.getElementById('ollama-status');
    const statusDot = statusEl.querySelector('.status-dot');
    const statusText = statusEl.querySelector('.status-text');

    statusText.textContent = 'Checking...';
    statusEl.classList.remove('connected', 'error');

    try {
        const status = await fetchOllamaStatus(url);
        if (status.connected) {
            statusEl.classList.add('connected');
            if (status.model_count > 0) {
                statusText.textContent = `Connected (${status.model_count} models)`;
                await populateOllamaModelDropdowns(url);
            } else {
                statusEl.classList.remove('connected');
                statusEl.classList.add('error');
                statusText.textContent = 'No models installed';
            }
        } else {
            statusEl.classList.add('error');
            statusText.textContent = status.error || 'Connection failed';
        }
    } catch (error) {
        statusEl.classList.add('error');
        statusText.textContent = 'Connection failed';
    }
}

async function populateOllamaModelDropdowns(url) {
    const analysisSelect = document.getElementById('ollama-model-analysis');
    const generationSelect = document.getElementById('ollama-model-generation');

    try {
        const response = await fetchOllamaModels(url);
        if (response.error) {
            console.error('Failed to fetch Ollama models:', response.error);
            return;
        }

        const models = response.models || [];
        const options = models.map(m => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)}</option>`).join('');
        const defaultOption = '<option value="">-- Select model --</option>';

        analysisSelect.innerHTML = defaultOption + options;
        generationSelect.innerHTML = defaultOption + options;

        // Enable the dropdowns
        analysisSelect.disabled = false;
        generationSelect.disabled = false;

        // Restore saved model selections from config
        if (state.config?.model_analysis) {
            analysisSelect.value = state.config.model_analysis;
        }
        if (state.config?.model_generation) {
            generationSelect.value = state.config.model_generation;
        }

        // If neither model is configured and models are available, default both to first model
        if (!analysisSelect.value && !generationSelect.value && models.length > 0) {
            const firstModel = models[0].name;
            analysisSelect.value = firstModel;
            generationSelect.value = firstModel;
        }

        // If a model is selected, fetch its context info
        if (analysisSelect.value) {
            await updateOllamaContextDisplay(url, analysisSelect.value);
        }
    } catch (error) {
        console.error('Error populating Ollama models:', error);
    }
}

async function updateOllamaContextDisplay(url, modelName) {
    const contextEl = document.getElementById('ollama-context-window');
    const maxTracksEl = document.getElementById('ollama-max-tracks');

    if (!modelName) {
        contextEl.textContent = '-- tokens';
        maxTracksEl.textContent = '(~-- tracks)';
        return;
    }

    try {
        const info = await fetchOllamaModelInfo(url, modelName);
        if (info && info.context_window) {
            // Show context window with note if using default
            const isDefault = info.context_detected === false;
            const defaultNote = isDefault ? ' (default - not detected)' : '';
            contextEl.textContent = `${info.context_window.toLocaleString()} tokens${defaultNote}`;

            // Calculate max tracks: (context - 1000 buffer) / 50 tokens per track
            const maxTracks = Math.max(100, Math.floor((info.context_window * 0.9 - 1000) / 50));
            maxTracksEl.textContent = `(~${maxTracks.toLocaleString()} tracks)`;

            // Save the context window to config so backend can calculate max_tracks_to_ai
            try {
                await updateConfig({ ollama_context_window: info.context_window });
                // Refresh config state to get updated max_tracks_to_ai
                state.config = await fetchConfig();
            } catch (saveError) {
                console.error('Failed to save Ollama context window:', saveError);
            }
        } else {
            contextEl.textContent = '32,768 tokens (default)';
            maxTracksEl.textContent = '(~556 tracks)';
        }
    } catch (error) {
        contextEl.textContent = '-- tokens';
        maxTracksEl.textContent = '(~-- tracks)';
    }
}

function updateCustomMaxTracks() {
    const contextInput = document.getElementById('custom-context-window');
    const maxTracksEl = document.getElementById('custom-max-tracks');

    const contextWindow = parseInt(contextInput.value) || 32768;
    // Calculate max tracks: (context - 1000 buffer) / 50 tokens per track
    const maxTracks = Math.max(100, Math.floor((contextWindow * 0.9 - 1000) / 50));
    maxTracksEl.textContent = `(~${maxTracks.toLocaleString()} tracks)`;
}

function validateCustomProviderInputs() {
    const customUrl = document.getElementById('custom-url').value.trim();
    const customModel = document.getElementById('custom-model').value.trim();
    const customContext = parseInt(document.getElementById('custom-context-window').value);

    const errors = [];

    // Validate URL
    if (customUrl) {
        try {
            const url = new URL(customUrl);
            if (!['http:', 'https:'].includes(url.protocol)) {
                errors.push('Custom URL must use http or https protocol');
            }
        } catch {
            errors.push('Custom URL is not a valid URL');
        }
    }

    // Validate context window
    if (isNaN(customContext) || customContext < 512) {
        errors.push('Context window must be at least 512 tokens');
    } else if (customContext > 2000000) {
        errors.push('Context window seems too large (max 2M tokens)');
    }

    return errors;
}

function validateCustomUrlInline() {
    const customUrl = document.getElementById('custom-url').value.trim();
    const errorEl = document.getElementById('custom-url-error');

    if (!customUrl) {
        errorEl.textContent = '';
        errorEl.classList.add('hidden');
        return;
    }

    try {
        const url = new URL(customUrl);
        if (!['http:', 'https:'].includes(url.protocol)) {
            errorEl.textContent = 'Must use http or https protocol';
            errorEl.classList.remove('hidden');
        } else {
            errorEl.textContent = '';
            errorEl.classList.add('hidden');
        }
    } catch {
        errorEl.textContent = 'Invalid URL format';
        errorEl.classList.remove('hidden');
    }
}

function validateCustomContextInline() {
    const customContext = parseInt(document.getElementById('custom-context-window').value);
    const errorEl = document.getElementById('custom-context-error');

    if (isNaN(customContext) || customContext < 512) {
        errorEl.textContent = 'Must be at least 512 tokens';
        errorEl.classList.remove('hidden');
    } else if (customContext > 2000000) {
        errorEl.textContent = 'Cannot exceed 2,000,000 tokens';
        errorEl.classList.remove('hidden');
    } else {
        errorEl.textContent = '';
        errorEl.classList.add('hidden');
    }
}

function updateConfigRequiredUI() {
    const mediaServer = state.config?.media_server || 'plex';
    const serverConnected = mediaServer === 'jellyfin'
        ? !!(state.config?.jellyfin_url && state.config?.jellyfin_token_set)
        : (state.config?.plex_connected ?? false);
    const llmConfigured = state.config?.llm_configured ?? false;

    // Elements that require configuration
    const analyzeBtn = document.getElementById('analyze-prompt-btn');
    const continueBtn = document.getElementById('continue-to-filters-btn');
    const searchBtn = document.getElementById('search-tracks-btn');
    const searchInput = document.getElementById('track-search-input');
    const promptTextarea = document.querySelector('.prompt-textarea');

    // Hints
    const hintPrompt = document.getElementById('llm-required-hint-prompt');
    const hintDimensions = document.getElementById('llm-required-hint-dimensions');
    const hintSeed = document.getElementById('llm-required-hint-seed');

    // Determine what's missing
    const needsServer = !serverConnected;
    const needsLLM = !llmConfigured;
    const needsConfig = needsServer || needsLLM;

    // Update button/input states
    if (analyzeBtn) analyzeBtn.disabled = needsConfig;
    if (continueBtn) continueBtn.disabled = needsLLM; // Only needs LLM at this point
    if (searchBtn) searchBtn.disabled = needsServer;
    if (searchInput) searchInput.disabled = needsServer;
    if (promptTextarea) promptTextarea.disabled = needsServer;

    // Build hint message based on what's missing
    const serverLabel = mediaServer === 'jellyfin' ? 'Jellyfin' : 'Plex';
    let hintMessage = '';
    if (needsServer && needsLLM) {
        hintMessage = `<a href="#" data-view="settings">Configure ${serverLabel} and an LLM provider</a> to continue`;
    } else if (needsServer) {
        hintMessage = `<a href="#" data-view="settings">Connect to ${serverLabel}</a> to continue`;
    } else if (needsLLM) {
        hintMessage = '<a href="#" data-view="settings">Configure an LLM provider</a> to continue';
    }

    // Update hint content and visibility
    [hintPrompt, hintSeed].forEach(hint => {
        if (hint) {
            hint.innerHTML = hintMessage;
            hint.hidden = !needsConfig;
        }
    });

    // Dimensions hint only needs LLM (Plex is already connected at this step)
    if (hintDimensions) {
        hintDimensions.innerHTML = needsLLM ? '<a href="#" data-view="settings">Configure an LLM provider</a> to continue' : '';
        hintDimensions.hidden = !needsLLM;
    }
}

function updateFooter() {
    const footerVersion = document.getElementById('footer-version');
    if (footerVersion && state.config?.version) {
        footerVersion.textContent = `v${state.config.version}`;
    }

    const footerModel = document.getElementById('footer-model');
    if (footerModel && state.config) {
        let modelText;
        if (state.config.llm_configured) {
            const analysis = state.config.model_analysis;
            const generation = state.config.model_generation;

            if (analysis && generation && analysis !== generation) {
                // Two different models - show both
                modelText = `${analysis} / ${generation}`;
            } else if (generation) {
                // Same model or only generation set
                modelText = generation;
            } else if (analysis) {
                modelText = analysis;
            } else {
                modelText = state.config.llm_provider;
            }
        } else {
            // Not configured - show "not configured" regardless of provider selection
            modelText = 'llm not configured';
        }
        footerModel.textContent = modelText;
        footerModel.title = modelText; // Tooltip for truncated names
    }
}

let loadingIntervalId = null;

function setLoading(loading, message = 'Loading...', substeps = null) {
    state.loading = loading;
    const overlay = document.getElementById('loading-overlay');
    const messageEl = document.getElementById('loading-message');
    const substepEl = document.getElementById('loading-substep');

    // Clear any existing substep interval
    if (loadingIntervalId) {
        clearInterval(loadingIntervalId);
        loadingIntervalId = null;
    }

    overlay.classList.toggle('hidden', !loading);
    if (loading) { lockScroll(); } else { removeNoScrollIfNoModals(); }
    messageEl.textContent = message;

    const contentEl = overlay.querySelector('.loading-modal-content');
    if (substepEl) {
        if (loading) {
            // Pre-measure the widest possible text to prevent layout shifts
            if (contentEl && substeps && substeps.length > 0) {
                const allTexts = [message, ...substeps];
                substepEl.style.visibility = 'hidden';
                let maxWidth = contentEl.offsetWidth;
                for (const text of allTexts) {
                    substepEl.textContent = text;
                    maxWidth = Math.max(maxWidth, contentEl.scrollWidth);
                }
                contentEl.style.minWidth = maxWidth + 'px';
                substepEl.style.visibility = '';
            }

            if (substeps && substeps.length > 0) {
                // Show progressive substeps
                let stepIndex = 0;
                substepEl.textContent = substeps[0];

                loadingIntervalId = setInterval(() => {
                    stepIndex++;
                    if (stepIndex < substeps.length) {
                        substepEl.textContent = substeps[stepIndex];
                    }
                    // Stay on last step until done
                }, 2000); // Change message every 2 seconds
            } else {
                substepEl.textContent = '';
            }
        } else {
            substepEl.textContent = '';
            if (contentEl) contentEl.style.minWidth = '';
        }
    }
}

function showError(message) {
    const toast = document.getElementById('error-toast');
    const messageEl = document.getElementById('error-message');

    messageEl.textContent = message;
    toast.classList.remove('hidden');

    setTimeout(() => hideError(), 5000);
}

function hideError() {
    document.getElementById('error-toast').classList.add('hidden');
}

function showSuccess(message) {
    const toast = document.getElementById('success-toast');
    const messageEl = document.getElementById('success-message');

    messageEl.textContent = message;
    toast.classList.remove('hidden');

    setTimeout(() => hideSuccess(), 3000);
}

function hideSuccess() {
    document.getElementById('success-toast').classList.add('hidden');
}

function showSuccessModal(name, trackCount, playlistUrl) {
    const modal = document.getElementById('success-modal');
    const summary = document.getElementById('success-modal-summary');
    const openBtn = document.getElementById('open-in-plex-btn');

    summary.textContent = `"${name}" with ${trackCount} track${trackCount !== 1 ? 's' : ''} has been added to your Plex library.`;

    if (playlistUrl) {
        openBtn.href = playlistUrl;
        openBtn.style.display = '';
    } else {
        openBtn.style.display = 'none';
    }

    modal.classList.remove('hidden');
    lockScroll();
    focusManager.openModal(modal);
}

function dismissSuccessModal() {
    dismissModal('success-modal');
}

function resetPlaylistState() {
    state.step = 'input';
    state.prompt = '';
    state.questions = [];
    state.questionAnswers = [];
    state.questionTexts = [];
    state.filterAnalysisPromise = null;
    state.seedTrack = null;
    state.dimensions = [];
    state.selectedDimensions = [];
    state.additionalNotes = '';
    state.selectedGenres = [];
    state.selectedDecades = [];
    state.playlist = [];
    state.playlistName = '';
    state.tokenCount = 0;
    state.estimatedCost = 0;
    state.sessionTokens = 0;
    state.sessionCost = 0;
    state.playlistTitle = '';
    state.narrative = '';
    state.trackReasons = {};
    state.userRequest = '';
    state.selectedTrackKey = null;
    document.getElementById('prompt-input').value = '';
    updateStep();
}

function hideSuccessModal() {
    const modal = document.getElementById('success-modal');
    modal.classList.add('hidden');
    removeNoScrollIfNoModals();
    focusManager.closeModal(modal);
    resetPlaylistState();
}

// =============================================================================
// Library Cache Management
// =============================================================================

let syncPollInterval = null;

function showSyncModal(reason = 'first-time') {
    const modal = document.getElementById('sync-modal');
    const title = document.getElementById('sync-modal-title');
    const desc = document.getElementById('sync-modal-description');

    if (reason === 'upgrade') {
        title.textContent = 'Updating Your Library';
        desc.textContent = 'A new version needs to refresh your track cache. This may take a minute...';
    } else {
        title.textContent = 'Syncing Your Library';
        desc.textContent = 'Building your local track cache for faster access...';
    }

    modal.classList.remove('hidden');
    lockScroll();
    focusManager.openModal(modal);
}

function hideSyncModal() {
    const modal = document.getElementById('sync-modal');
    modal.classList.add('hidden');
    removeNoScrollIfNoModals();
    focusManager.closeModal(modal);
}

function updateSyncProgress(phase, current, total) {
    const fill = document.getElementById('sync-progress-fill');
    const text = document.getElementById('sync-progress-text');
    const bar = fill?.parentElement;

    if (phase === 'fetching_albums') {
        // Indeterminate state - fetching album genres
        fill.style.width = '0%';
        text.textContent = 'Fetching album genres...';
        if (bar) bar.setAttribute('aria-valuenow', '0');
    } else if (phase === 'fetching') {
        // Indeterminate state - fetching tracks from Plex
        fill.style.width = '0%';
        text.textContent = 'Fetching tracks from library...';
        if (bar) bar.setAttribute('aria-valuenow', '0');
    } else if (phase === 'processing') {
        // Processing phase - show progress
        const percent = total > 0 ? (current / total) * 100 : 0;
        fill.style.width = `${percent}%`;
        text.textContent = `${current.toLocaleString()} / ${total.toLocaleString()} tracks`;
        if (bar) bar.setAttribute('aria-valuenow', Math.round(percent).toString());
    } else {
        // Unknown or null phase - show generic message
        fill.style.width = '0%';
        text.textContent = 'Syncing...';
        if (bar) bar.setAttribute('aria-valuenow', '0');
    }
}

function formatRelativeTime(isoString) {
    if (!isoString) return 'Never';

    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} min${diffMins !== 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
    if (diffDays < 7) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;

    return date.toLocaleDateString();
}

function updateFooterLibraryStatus(status) {
    const container = document.getElementById('footer-library-status');
    const trackCount = document.getElementById('footer-track-count');
    const trackSeparator = document.getElementById('footer-track-separator');
    const syncTime = document.getElementById('footer-sync-time');

    if (!status || (status.track_count === 0 && !status.is_syncing)) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    // Show track count, or hide it during sync when count is 0
    if (status.track_count > 0) {
        trackCount.textContent = `${status.track_count.toLocaleString()} tracks`;
        trackCount.style.display = '';
        trackSeparator.style.display = '';
    } else if (status.is_syncing) {
        trackCount.style.display = 'none';
        trackSeparator.style.display = 'none';
    }

    if (status.is_syncing) {
        // Show percentage if we have progress on processing phase
        if (status.sync_progress?.phase === 'processing' && status.sync_progress.total > 0) {
            const pct = Math.round((status.sync_progress.current / status.sync_progress.total) * 100);
            syncTime.textContent = `Syncing ${pct}%`;
        } else {
            syncTime.textContent = 'Syncing...';
        }
    } else {
        syncTime.textContent = formatRelativeTime(status.synced_at);
    }
}

async function checkLibraryStatus() {
    try {
        const status = await fetchLibraryStatus();

        // Update footer status
        updateFooterLibraryStatus(status);

        // Upgrade resync: schema migration requires re-sync — blocking
        if (status.needs_resync && status.plex_connected) {
            showSyncModal('upgrade');
            if (status.is_syncing && status.sync_progress) {
                updateSyncProgress(status.sync_progress.phase, status.sync_progress.current, status.sync_progress.total);
            } else if (!status.is_syncing) {
                // Backend auto-resync hasn't started yet — trigger it
                updateSyncProgress('fetching_albums', 0, 0);
                try {
                    await triggerLibrarySync();
                } catch { /* sync may already be in progress (409) */ }
            }
            startSyncPolling();
        // First-time sync: no tracks ever — blocking
        } else if (status.track_count === 0 && status.plex_connected && !status.is_syncing && !status.synced_at) {
            await startFirstTimeSync();
        // Any other sync in progress (manual refresh, stale re-sync) — background only
        } else if (status.is_syncing) {
            startSyncPolling();
        // Cache empty after a previous sync (error, etc.) — trigger silently
        } else if (status.track_count === 0 && status.plex_connected && status.synced_at) {
            try {
                await triggerLibrarySync();
            } catch { /* sync may already be in progress (409) */ }
            startSyncPolling();
        }

        return status;
    } catch (error) {
        console.error('Failed to check library status:', error);
        return null;
    }
}

async function startFirstTimeSync() {
    showSyncModal();
    updateSyncProgress('fetching_albums', 0, 0);

    try {
        await triggerLibrarySync();
        // Always poll for progress
        startSyncPolling();
    } catch (error) {
        console.error('Sync failed:', error);
        hideSyncModal();
        showError('Failed to sync library: ' + error.message);
    }
}

function startSyncPolling() {
    if (syncPollInterval) return;

    syncPollInterval = setInterval(async () => {
        try {
            const status = await fetchLibraryStatus();

            if (status.is_syncing && status.sync_progress) {
                updateSyncProgress(status.sync_progress.phase, status.sync_progress.current, status.sync_progress.total);
                // Update footer with progress percentage for background syncs
                updateFooterLibraryStatus(status);
            } else if (!status.is_syncing) {
                // Sync completed
                stopSyncPolling();
                hideSyncModal();
                updateFooterLibraryStatus(status);

                if (status.error) {
                    showError('Sync failed: ' + status.error);
                }
            }
        } catch (error) {
            console.error('Error polling sync status:', error);
        }
    }, 1000);
}

function stopSyncPolling() {
    if (syncPollInterval) {
        clearInterval(syncPollInterval);
        syncPollInterval = null;
    }
}

async function handleRefreshLibrary() {
    try {
        const status = await fetchLibraryStatus();

        if (status.is_syncing) {
            showSuccess('Sync already in progress');
            return;
        }

        await triggerLibrarySync();
        startSyncPolling();

        // Update footer to show syncing
        const syncTime = document.getElementById('footer-sync-time');
        if (syncTime) {
            syncTime.textContent = 'Syncing...';
        }
    } catch (error) {
        if (error.message.includes('409')) {
            showSuccess('Sync already in progress');
        } else {
            showError('Failed to start sync: ' + error.message);
        }
    }
}

// =============================================================================
// Event Handlers
// =============================================================================

function setupEventListeners() {
    // Unified navigation via data-nav attributes (header nav + home cards)
    document.querySelectorAll('[data-nav]').forEach(el => {
        el.addEventListener('click', () => {
            const hash = el.dataset.nav;
            // Special case: clicking Recommend Album while already there
            if (hash === 'recommend-album' && state.view === 'recommend') {
                if (state.rec.step !== 'prompt' || state.rec.loading) {
                    if (!state.rec.sessionId) {
                        // Saved result — nothing to lose, go straight to step 1
                        resetRecState();
                        history.replaceState(null, '', '#recommend-album');
                    } else {
                        openRecRestartModal();
                    }
                }
                return;
            }
            // Special case: clicking a playlist flow while already in that flow
            if (state.view === 'create') {
                const isCurrentMode = (hash === 'playlist-prompt' && state.mode === 'prompt') ||
                                      (hash === 'playlist-seed' && state.mode === 'seed');
                if (isCurrentMode && (state.step !== 'input' || state.loading)) {
                    if (state.step === 'results') {
                        // Results page — playlist already generated, nothing to lose
                        resetPlaylistState();
                        location.hash = '#' + hash;
                    } else {
                        openPlaylistRestartModal();
                    }
                    return;
                }
            }
            // Warn if navigating away from a mid-flow state
            if (state.view === 'create' && ((state.step !== 'input' && state.step !== 'results') || state.loading)) {
                pendingNavHash = hash;
                openPlaylistRestartModal();
                return;
            }
            if (state.view === 'recommend' && ((state.rec.step !== 'prompt' && state.rec.step !== 'results') || state.rec.loading)) {
                pendingNavHash = hash;
                openRecRestartModal();
                return;
            }
            location.hash = '#' + hash;
            // Close dropdown if open
            const dropdown = document.querySelector('.nav-dropdown');
            dropdown?.classList.remove('open');
            dropdown?.querySelector('.nav-dropdown-trigger')?.setAttribute('aria-expanded', 'false');
        });
    });

    // Dropdown toggle
    document.querySelector('.nav-dropdown-trigger')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const dropdown = e.target.closest('.nav-dropdown');
        const isOpen = dropdown.classList.contains('open');
        dropdown.classList.toggle('open', !isOpen);
        e.target.closest('.nav-dropdown-trigger').setAttribute('aria-expanded', !isOpen);
    });

    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.nav-dropdown')) {
            const dropdown = document.querySelector('.nav-dropdown');
            dropdown?.classList.remove('open');
            dropdown?.querySelector('.nav-dropdown-trigger')?.setAttribute('aria-expanded', 'false');
        }
    });

    // Close dropdown on Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const dropdown = document.querySelector('.nav-dropdown');
            if (dropdown?.classList.contains('open')) {
                dropdown.classList.remove('open');
                dropdown.querySelector('.nav-dropdown-trigger')?.setAttribute('aria-expanded', 'false');
                dropdown.querySelector('.nav-dropdown-trigger')?.focus();
            }
        }
    });

    // Settings links in hints (use event delegation for dynamically inserted links)
    document.body.addEventListener('click', e => {
        const link = e.target.closest('.llm-required-hint a[data-view]');
        if (link) {
            e.preventDefault();
            const hash = link.dataset.view === 'settings' ? 'settings' : null;
            if (hash) location.hash = '#' + hash;
        }
    });

    // Hash-based routing for top-level views
    window.addEventListener('hashchange', () => {
        const hash = location.hash.slice(1);
        if (hash.startsWith('result/')) {
            const resultId = hash.split('/')[1];
            if (resultId) {
                loadSavedResult(resultId);
                return;
            }
        }
        const view = viewFromHash();
        const mode = modeFromHash();
        navigateTo(view, mode);
    });

    // Playlist prompt pills
    const playlistPillContainer = document.getElementById('playlist-prompt-pills');
    if (playlistPillContainer) {
        playlistPillContainer.addEventListener('click', e => {
            const pill = e.target.closest('.prompt-pill');
            if (!pill) return;
            document.getElementById('prompt-input').value = pill.textContent.trim();
        });
    }
    const playlistShuffleBtn = document.getElementById('playlist-prompt-shuffle');
    if (playlistShuffleBtn) {
        playlistShuffleBtn.addEventListener('click', () => shufflePromptPills('playlist-prompt-pills', PLAYLIST_PROMPT_GROUPS));
    }

    // Prompt analysis
    document.getElementById('analyze-prompt-btn').addEventListener('click', handleAnalyzePrompt);

    // Refine step (prompt mode)
    document.getElementById('refine-next-btn')?.addEventListener('click', handlePlaylistRefineNext);
    const playlistQuestionsContainer = document.getElementById('playlist-questions-container');
    if (playlistQuestionsContainer) {
        const playlistQState = {
            get questions() { return state.questions; },
            get answers() { return state.questionAnswers; },
            get answerTexts() { return state.questionTexts; },
        };
        setupQuestionEventHandlers(playlistQuestionsContainer, playlistQState, renderPlaylistQuestions);
    }

    // Track search
    document.getElementById('search-tracks-btn').addEventListener('click', handleSearchTracks);
    document.getElementById('track-search-input').addEventListener('keypress', e => {
        if (e.key === 'Enter') handleSearchTracks();
    });

    // Continue to filters
    document.getElementById('continue-to-filters-btn').addEventListener('click', handleContinueToFilters);

    // Genre toggle all
    document.getElementById('genre-toggle-all').addEventListener('click', () => {
        state.selectedGenres = allGenresSelected() ? [] : state.availableGenres.map(g => g.name);
        updateFilters();
        updateFilterPreview();
    });

    // Genre chips
    document.getElementById('genre-chips').addEventListener('click', e => {
        const chip = e.target.closest('.chip');
        if (!chip) return;

        const genre = chip.dataset.genre;
        if (state.selectedGenres.includes(genre)) {
            state.selectedGenres = state.selectedGenres.filter(g => g !== genre);
        } else {
            state.selectedGenres.push(genre);
        }
        updateFilters();
        updateFilterPreview();
    });

    // Decade toggle all
    document.getElementById('decade-toggle-all').addEventListener('click', () => {
        state.selectedDecades = allDecadesSelected() ? [] : state.availableDecades.map(d => d.name);
        updateFilters();
        updateFilterPreview();
    });

    // Decade chips
    document.getElementById('decade-chips').addEventListener('click', e => {
        const chip = e.target.closest('.chip');
        if (!chip) return;

        const decade = chip.dataset.decade;
        if (state.selectedDecades.includes(decade)) {
            state.selectedDecades = state.selectedDecades.filter(d => d !== decade);
        } else {
            state.selectedDecades.push(decade);
        }
        updateFilters();
        updateFilterPreview();
    });

    // Track count (local recalculation - no API call needed)
    document.querySelectorAll('.count-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.trackCount = parseInt(btn.dataset.count);
            updateFilters();
            recalculateCostDisplay();
        });
    });

    // Note: limit-btn listeners are set up dynamically in updateTrackLimitButtons()

    // Exclude live checkbox
    document.getElementById('exclude-live').addEventListener('change', e => {
        state.excludeLive = e.target.checked;
        updateFilterPreview();
    });

    // Minimum rating buttons
    document.querySelectorAll('.rating-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.minRating = parseInt(btn.dataset.rating);
            updateFilters();
            updateFilterPreview();
        });
    });

    // Generate playlist
    document.getElementById('generate-btn').addEventListener('click', handleGenerate);

    // Regenerate
    document.getElementById('regenerate-btn').addEventListener('click', handleGenerate);

    // Back to filters
    document.getElementById('back-to-filters-btn').addEventListener('click', () => {
        state.step = 'filters';
        updateStep();
    });

    // Remove track (with selection management)
    document.getElementById('playlist-tracks').addEventListener('click', e => {
        const removeBtn = e.target.closest('.track-remove');
        if (!removeBtn) return;

        const ratingKey = removeBtn.dataset.ratingKey;
        const removedIndex = state.playlist.findIndex(t => t.rating_key === ratingKey);
        state.playlist = state.playlist.filter(t => t.rating_key !== ratingKey);

        // If removed track was selected, auto-select next or first
        if (state.selectedTrackKey === ratingKey) {
            if (state.playlist.length > 0) {
                const nextIndex = Math.min(removedIndex, state.playlist.length - 1);
                state.selectedTrackKey = state.playlist[nextIndex].rating_key;
            } else {
                state.selectedTrackKey = null;
            }
        }

        updatePlaylist();
    });

    // Save playlist
    document.getElementById('save-playlist-btn').addEventListener('click', handleSavePlaylist);

    // Save settings
    document.getElementById('save-settings-btn').addEventListener('click', handleSaveSettings);

    // Success modal - Start New Playlist
    document.getElementById('new-playlist-btn').addEventListener('click', hideSuccessModal);

    // Media server selection change
    const mediaServerSelect = document.getElementById('settings-media-server');
    if (mediaServerSelect) {
        mediaServerSelect.addEventListener('change', (e) => {
            _showMediaServerFields(e.target.value);
        });
    }

    // Provider selection change
    document.getElementById('llm-provider').addEventListener('change', (e) => {
        showProviderSettings(e.target.value);
    });

    // Library refresh link
    const refreshLink = document.getElementById('footer-refresh-link');
    if (refreshLink) {
        refreshLink.addEventListener('click', (e) => {
            e.preventDefault();
            handleRefreshLibrary();
        });
    }

    // Ollama URL change - trigger status check
    let ollamaUrlTimeout = null;
    document.getElementById('ollama-url').addEventListener('input', (e) => {
        // Debounce the status check
        if (ollamaUrlTimeout) clearTimeout(ollamaUrlTimeout);
        ollamaUrlTimeout = setTimeout(() => {
            const url = e.target.value.trim();
            if (url) {
                checkOllamaStatus(url);
            }
        }, 500);
    });

    // Ollama model selection change - update context display
    document.getElementById('ollama-model-analysis').addEventListener('change', async (e) => {
        const url = document.getElementById('ollama-url').value.trim();
        const model = e.target.value;
        if (url && model) {
            await updateOllamaContextDisplay(url, model);
        }
    });

    // Custom context window change - update max tracks display and validate inline
    document.getElementById('custom-context-window').addEventListener('input', () => {
        updateCustomMaxTracks();
        validateCustomContextInline();
    });

    // Custom URL validation on blur
    document.getElementById('custom-url').addEventListener('blur', () => {
        validateCustomUrlInline();
    });

    // Play Now button
    document.getElementById('play-now-btn').addEventListener('click', handlePlayNow);

    // Playlist Start Over link
    document.getElementById('playlist-start-over')?.addEventListener('click', resetPlaylistState);

    // Refresh clients in client picker modal
    document.getElementById('refresh-clients-btn').addEventListener('click', refreshClientList);

    // Replace Queue / Play Next choice modal buttons
    document.getElementById('replace-queue-btn').addEventListener('click', () => {
        executePlayQueue(state._pendingClientId, 'replace');
    });
    document.getElementById('play-next-btn').addEventListener('click', () => {
        executePlayQueue(state._pendingClientId, 'play_next');
    });

    // Play success modal — Start New Playlist
    document.getElementById('play-success-new-btn').addEventListener('click', handlePlaySuccessNewPlaylist);

    // Save mode dropdown toggle
    document.getElementById('save-mode-dropdown-btn').addEventListener('click', toggleSaveModeDropdown);

    // Save mode option selection (Create / Replace / Append)
    document.querySelectorAll('.save-mode-option').forEach(opt => {
        opt.addEventListener('click', () => setSaveMode(opt.dataset.mode));
    });

    // Playlist picker change
    document.getElementById('playlist-picker').addEventListener('change', (e) => {
        state.selectedPlaylistId = e.target.value;
    });

    // Update success modal — Start New Playlist
    document.getElementById('update-new-playlist-btn').addEventListener('click', handleUpdateSuccessNewPlaylist);

    // Bottom sheet close handlers
    const bottomSheet = document.getElementById('bottom-sheet');
    if (bottomSheet) {
        // Close on backdrop tap
        bottomSheet.querySelector('.bottom-sheet-backdrop').addEventListener('click', closeBottomSheet);

        // Close on swipe down (simple implementation)
        let touchStartY = 0;
        const content = bottomSheet.querySelector('.bottom-sheet-content');
        content.addEventListener('touchstart', (e) => {
            touchStartY = e.touches[0].clientY;
        });
        content.addEventListener('touchend', (e) => {
            const touchEndY = e.changedTouches[0].clientY;
            if (touchEndY - touchStartY > 50) {
                closeBottomSheet();
            }
        });
    }

    // Escape key dismisses the topmost visible modal
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const modals = [
            { id: 'playlist-restart-modal', dismiss: dismissPlaylistRestartModal },
            { id: 'rec-restart-modal', dismiss: dismissRecRestartModal },
            { id: 'play-choice-modal', dismiss: dismissPlayChoice },
            { id: 'client-picker-modal', dismiss: dismissClientPicker },
            { id: 'play-success-modal', dismiss: dismissPlaySuccess },
            { id: 'update-success-modal', dismiss: dismissUpdateSuccess },
            { id: 'success-modal', dismiss: dismissSuccessModal },
            { id: 'bottom-sheet', dismiss: closeBottomSheet },
        ];
        for (const { id, dismiss } of modals) {
            const el = document.getElementById(id);
            if (el && !el.classList.contains('hidden')) {
                dismiss();
                break;
            }
        }
    });
}

async function handleAnalyzePrompt() {
    const prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) {
        showError('Please enter a prompt');
        return;
    }

    state.prompt = prompt;
    // Reset session costs for new flow
    state.sessionTokens = 0;
    state.sessionCost = 0;

    const stepLoader = showTimedStepLoading([
        { id: 'parsing', text: 'Parsing your request...', status: 'active' },
        { id: 'questions', text: 'Crafting questions...', status: 'pending' },
        { id: 'matching', text: 'Matching to your library...', status: 'pending' },
    ]);

    // Fire filter analysis in parallel (cached as a promise for the refine→filters transition)
    state.filterAnalysisPromise = analyzePrompt(prompt).catch(() => null);

    try {
        // Fire question generation (reuse recommend endpoint)
        const data = await apiCall('/recommend/questions', {
            method: 'POST',
            body: JSON.stringify({ prompt }),
        });

        state.questions = data.questions;
        state.questionAnswers = data.questions.map(() => null);
        state.questionTexts = data.questions.map(() => '');

        renderPlaylistQuestions();
        state.step = 'refine';
        updateStep();
    } catch (error) {
        showError(error.message);
    } finally {
        stepLoader.finish();
    }
}

async function handleSearchTracks() {
    const query = document.getElementById('track-search-input').value.trim();
    if (!query) {
        showError('Please enter a search query');
        return;
    }

    setLoading(true, 'Searching tracks...');

    try {
        const tracks = await searchTracks(query);
        renderSearchResults(tracks);
    } catch (error) {
        showError(error.message);
    } finally {
        setLoading(false);
    }
}

function renderSearchResults(tracks) {
    const container = document.getElementById('search-results');

    if (!tracks.length) {
        container.innerHTML = '<p class="text-muted">No tracks found</p>';
        return;
    }

    container.innerHTML = tracks.map(track => `
        <div class="search-result-item" data-rating-key="${escapeHtml(track.rating_key)}"
             role="option" tabindex="0"
             aria-label="${escapeHtml(track.title)} by ${escapeHtml(track.artist)}">
            ${trackArtHtml(track)}
            <div class="track-info">
                <div class="track-title">${escapeHtml(track.title)}</div>
                <div class="track-artist">${escapeHtml(track.artist)} - ${escapeHtml(track.album)}</div>
            </div>
        </div>
    `).join('');

    // Add click and keyboard handlers
    container.querySelectorAll('.search-result-item').forEach(item => {
        item.addEventListener('click', () => selectSeedTrack(item.dataset.ratingKey, tracks));
        item.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                selectSeedTrack(item.dataset.ratingKey, tracks);
            }
        });
    });
}

async function selectSeedTrack(ratingKey, tracks) {
    // Check if services are configured before proceeding
    const _isServerConnected = state.config?.media_server === 'jellyfin'
        ? !!(state.config?.jellyfin_url && state.config?.jellyfin_token_set)
        : state.config?.plex_connected;
    if (!_isServerConnected) {
        const _label = state.config?.media_server === 'jellyfin' ? 'Jellyfin' : 'Plex';
        showError(`Connect to ${_label} in Settings first`);
        return;
    }
    if (!state.config?.llm_configured) {
        showError('Configure an LLM provider in Settings to analyze tracks');
        return;
    }

    const track = tracks.find(t => t.rating_key === ratingKey);
    if (!track) return;

    state.seedTrack = track;
    // Reset session costs for new flow
    state.sessionTokens = 0;
    state.sessionCost = 0;

    const stepLoader = showTimedStepLoading([
        { id: 'metadata', text: 'Loading track metadata...', status: 'active' },
        { id: 'analyzing', text: 'Analyzing musical characteristics...', status: 'pending' },
        { id: 'dimensions', text: 'Generating exploration dimensions...', status: 'pending' },
    ]);

    try {
        const response = await analyzeTrack(ratingKey);

        // Track analysis costs
        state.sessionTokens += response.token_count || 0;
        state.sessionCost += response.estimated_cost || 0;

        state.dimensions = response.dimensions;
        state.selectedDimensions = [];

        renderSeedTrack();
        renderDimensions();

        state.step = 'dimensions';
        updateStep();
    } catch (error) {
        showError(error.message);
    } finally {
        stepLoader.finish();
    }
}

function renderSeedTrack() {
    const container = document.getElementById('selected-track');
    const track = state.seedTrack;

    container.innerHTML = `
        ${trackArtHtml(track)}
        <div class="track-info">
            <div class="track-title">${escapeHtml(track.title)}</div>
            <div class="track-artist">${escapeHtml(track.artist)} - ${escapeHtml(track.album)}</div>
        </div>
    `;
}

function renderDimensions() {
    const container = document.getElementById('dimensions-list');
    const focusedId = document.activeElement?.dataset?.dimensionId;

    container.innerHTML = state.dimensions.map(dim => {
        const isSelected = state.selectedDimensions.includes(dim.id);
        return `
        <div class="dimension-card ${isSelected ? 'selected' : ''}"
             data-dimension-id="${escapeHtml(dim.id)}"
             role="checkbox" tabindex="0"
             aria-checked="${isSelected}"
             aria-label="${escapeHtml(dim.label)}: ${escapeHtml(dim.description)}">
            <div class="dimension-label">${escapeHtml(dim.label)}</div>
            <div class="dimension-description">${escapeHtml(dim.description)}</div>
        </div>
    `}).join('');

    // Add click and keyboard handlers
    container.querySelectorAll('.dimension-card').forEach(card => {
        const toggle = () => {
            const dimId = card.dataset.dimensionId;
            if (state.selectedDimensions.includes(dimId)) {
                state.selectedDimensions = state.selectedDimensions.filter(d => d !== dimId);
            } else {
                state.selectedDimensions.push(dimId);
            }
            renderDimensions();
        };
        card.addEventListener('click', toggle);
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle();
            }
        });
    });

    if (focusedId) {
        container.querySelector(`[data-dimension-id="${CSS.escape(focusedId)}"]`)?.focus();
    }
}

async function handleContinueToFilters() {
    if (!state.selectedDimensions.length) {
        showError('Please select at least one dimension');
        return;
    }

    state.additionalNotes = document.getElementById('additional-notes-input').value.trim();
    setLoading(true, 'Loading library data...');

    try {
        const stats = await fetchLibraryStats();
        state.availableGenres = stats.genres;
        state.availableDecades = stats.decades;
        state.selectedGenres = stats.genres.map(g => g.name);
        state.selectedDecades = stats.decades.map(d => d.name);

        state.step = 'filters';
        updateStep();
        updateFilters();
        updateFilterPreview();
    } catch (error) {
        showError(error.message);
    } finally {
        setLoading(false);
    }
}

async function handleGenerate() {
    // All selected = no filter (avoids excluding untagged tracks)
    const request = {
        genres: allGenresSelected() ? [] : state.selectedGenres,
        decades: allDecadesSelected() ? [] : state.selectedDecades,
        track_count: state.trackCount,
        exclude_live: state.excludeLive,
        min_rating: state.minRating,
        max_tracks_to_ai: state.maxTracksToAI,
    };

    if (state.mode === 'prompt') {
        request.prompt = state.prompt;
        if (state.questionAnswers?.length) {
            request.refinement_answers = state.questionAnswers.map((ans, i) => {
                const text = state.questionTexts[i]?.trim();
                if (ans && text) return `${ans} (${text})`;
                if (ans) return ans;
                if (text) return text;
                return null;
            });
        }
    } else {
        request.seed_track = {
            rating_key: state.seedTrack.rating_key,
            selected_dimensions: state.selectedDimensions,
        };
        if (state.additionalNotes) {
            request.additional_notes = state.additionalNotes;
        }
    }

    showStepLoading(PLAYLIST_STEPS.map(s => ({ ...s })));

    generatePlaylistStream(
        request,
        // onProgress — map SSE step to consolidated visible step
        (data) => {
            const mapped = PLAYLIST_STEP_MAP[data.step];
            if (mapped) updateStepProgress(mapped);
        },
        // onComplete
        (response) => {
            // Mark final step complete before hiding
            updateStepProgress('__done__');

            // Add generation costs to session totals
            state.sessionTokens += response.token_count || 0;
            state.sessionCost += response.estimated_cost || 0;

            state.playlist = response.tracks;
            state.tokenCount = state.sessionTokens;
            state.estimatedCost = state.sessionCost;

            // Use generated title from response, or from state if already set via SSE
            if (response.playlist_title) {
                state.playlistTitle = response.playlist_title;
            }
            if (response.narrative) {
                state.narrative = response.narrative;
            }
            if (response.track_reasons) {
                state.trackReasons = response.track_reasons;
            }

            // Use generated title for playlist name, fallback to old method
            state.playlistName = state.playlistTitle || generatePlaylistName();

            // Reset selection so auto-select picks first new track
            state.selectedTrackKey = null;

            state.step = 'results';
            updateStep();
            updatePlaylist();
            window.scrollTo(0, 0);
            hideStepLoading();

            // Update URL to deep link for this result
            if (response.result_id) {
                history.replaceState(null, '', `#result/${response.result_id}`);
                markHistoryStale();
            }
        },
        // onError
        (error) => {
            showError(error.message);
            hideStepLoading();
        }
    );
}

function generatePlaylistName() {
    const date = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

    if (state.mode === 'prompt') {
        const words = state.prompt.split(' ').slice(0, 3).join(' ');
        return `${words}... (${date})`;
    } else {
        return `Like ${state.seedTrack.title} (${date})`;
    }
}

async function handleSavePlaylist() {
    // Route to update handler when in replace/append mode
    if (state.saveMode === 'replace' || state.saveMode === 'append') {
        await handleUpdatePlaylist();
        return;
    }

    const name = document.getElementById('playlist-name-input').value.trim();
    if (!name) {
        showError('Please enter a playlist name');
        return;
    }

    if (!state.playlist.length) {
        showError('Playlist is empty');
        return;
    }

    const _serverLabel = state.config?.media_server === 'jellyfin' ? 'Jellyfin' : 'Plex';
    const saveSteps = [
        `Connecting to ${_serverLabel} server...`,
        'Creating playlist...',
        'Adding tracks...',
    ];
    setLoading(true, `Saving to ${_serverLabel}...`, saveSteps);

    try {
        const ratingKeys = state.playlist.map(t => t.rating_key);
        const response = await savePlaylist(name, ratingKeys, state.narrative);

        if (response.success) {
            const trackCount = response.tracks_added || state.playlist.length;
            showSuccessModal(name, trackCount, response.playlist_url);
            // Invalidate playlist cache so newly created playlist shows in Update Existing picker
            state.plexPlaylists = [];
        } else {
            showError(response.error || 'Failed to save playlist');
        }
    } catch (error) {
        showError(error.message);
    } finally {
        setLoading(false);
    }
}

async function loadSettings() {
    try {
        state.config = await fetchConfig();

        // Set max tracks/albums to AI based on model's context limit
        if (state.config.max_tracks_to_ai) {
            state.maxTracksToAI = Math.min(state.maxTracksToAI, state.config.max_tracks_to_ai);
            updateTrackLimitButtons();
        }
        if (state.config.max_albums_to_ai) {
            state.rec.maxAlbumsToAI = Math.min(state.rec.maxAlbumsToAI, state.config.max_albums_to_ai);
            updateAlbumLimitButtons();
        }

        updateSettings();
        updateFooter();
        updateConfigRequiredUI();

        // Show library stats if connected (Plex or Jellyfin)
        const isMediaConnected = state.config.media_server === 'jellyfin'
            ? !!(state.config.jellyfin_url && state.config.jellyfin_token_set)
            : state.config.plex_connected;
        if (isMediaConnected) {
            const statsSection = document.getElementById('library-stats-section');
            if (statsSection) statsSection.style.display = 'block';

            try {
                const stats = await fetchLibraryStats();
                // Cache genre/decade data so other views don't need a separate fetch
                state.availableGenres = stats.genres;
                state.availableDecades = stats.decades;
                const statsEl = document.getElementById('library-stats');
                if (statsEl) statsEl.innerHTML = `
                    <p><strong>Total Tracks:</strong> ${stats.total_tracks.toLocaleString()}</p>
                    <p><strong>Genres:</strong> ${stats.genres.length}</p>
                    <p><strong>Decades:</strong> ${stats.decades.map(d => d.name).join(', ')}</p>
                `;
            } catch {
                // Ignore library stats errors
            }
        }
    } catch (error) {
        showError('Failed to load settings: ' + error.message);
    }
}

async function handleSaveSettings() {
    const updates = {};

    const mediaServer = document.getElementById('settings-media-server')?.value || 'plex';
    const plexUrl = document.getElementById('plex-url').value.trim();
    const plexToken = document.getElementById('plex-token').value.trim();
    const musicLibrary = document.getElementById('music-library').value.trim();
    const jellyfinUrl = document.getElementById('jellyfin-url')?.value.trim() || '';
    const jellyfinToken = document.getElementById('jellyfin-token')?.value.trim() || '';
    const jellyfinMusicLibrary = document.getElementById('jellyfin-music-library')?.value.trim() || 'Music';
    const llmProvider = document.getElementById('llm-provider').value;
    const llmApiKey = document.getElementById('llm-api-key').value.trim();

    // Ollama settings
    const ollamaUrl = document.getElementById('ollama-url').value.trim();
    const ollamaModelAnalysis = document.getElementById('ollama-model-analysis').value;
    const ollamaModelGeneration = document.getElementById('ollama-model-generation').value;

    // Custom provider settings
    const customUrl = document.getElementById('custom-url').value.trim();
    const customApiKey = document.getElementById('custom-api-key').value.trim();
    const customModel = document.getElementById('custom-model').value.trim();
    const customContextWindow = parseInt(document.getElementById('custom-context-window').value) || 32768;

    if (mediaServer) updates.media_server = mediaServer;
    if (plexUrl) updates.plex_url = plexUrl;
    if (plexToken) updates.plex_token = plexToken;
    if (musicLibrary && mediaServer !== 'jellyfin') updates.music_library = musicLibrary;
    if (jellyfinUrl) updates.jellyfin_url = jellyfinUrl;
    if (jellyfinToken) updates.jellyfin_token = jellyfinToken;
    if (jellyfinMusicLibrary) updates.jellyfin_music_library = jellyfinMusicLibrary;
    if (llmProvider) updates.llm_provider = llmProvider;

    // Set provider-specific settings
    if (llmProvider === 'ollama') {
        if (ollamaUrl) updates.ollama_url = ollamaUrl;
        if (ollamaModelAnalysis) updates.model_analysis = ollamaModelAnalysis;
        if (ollamaModelGeneration) updates.model_generation = ollamaModelGeneration;
    } else if (llmProvider === 'custom') {
        // Validate custom provider inputs
        const validationErrors = validateCustomProviderInputs();
        if (validationErrors.length > 0) {
            showError(validationErrors.join('. '));
            return;
        }
        if (customUrl) updates.custom_url = customUrl;
        if (customApiKey) updates.llm_api_key = customApiKey;
        if (customModel) {
            updates.model_analysis = customModel;
            updates.model_generation = customModel;  // Same model for both
        }
        updates.custom_context_window = customContextWindow;
    } else {
        // Cloud providers need API key
        if (llmApiKey) updates.llm_api_key = llmApiKey;
    }

    if (Object.keys(updates).length === 0) {
        showError('No settings to update');
        return;
    }

    setLoading(true, 'Saving settings...');

    try {
        state.config = await updateConfig(updates);
        updateSettings();
        updateFooter();
        updateConfigRequiredUI();
        updateTrackLimitButtons();  // Refresh track limits based on new model
        updateAlbumLimitButtons();  // Refresh album limits based on new model
        showSuccess('Settings saved!');

        // Clear password fields after save
        document.getElementById('plex-token').value = '';
        const jellyfinTokenEl = document.getElementById('jellyfin-token');
        if (jellyfinTokenEl) jellyfinTokenEl.value = '';
        document.getElementById('llm-api-key').value = '';

        // Reload library stats
        const isConnectedAfterSave = state.config.media_server === 'jellyfin'
            ? !!(state.config.jellyfin_url && state.config.jellyfin_token_set)
            : state.config.plex_connected;
        if (isConnectedAfterSave) {
            loadSettings();
        }
    } catch (error) {
        showError('Failed to save settings: ' + error.message);
    } finally {
        setLoading(false);
    }
}

// =============================================================================
// Instant Queue — Play Now Handlers (005)
// =============================================================================

function lockScroll() {
    if (document.body.classList.contains('no-scroll')) return;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.paddingRight = scrollbarWidth + 'px';
    document.body.classList.add('no-scroll');
}

function unlockScroll() {
    document.body.classList.remove('no-scroll');
    document.body.style.paddingRight = '';
}

function removeNoScrollIfNoModals() {
    const openModal = document.querySelector(
        '.modal-overlay:not(.hidden), .success-modal:not(.hidden), .sync-modal:not(.hidden), .bottom-sheet:not(.hidden), .loading-overlay:not(.hidden), .step-loading-overlay:not(.hidden)'
    );
    if (!openModal) {
        unlockScroll();
    }
}

function dismissModal(id, afterDismiss) {
    const modal = document.getElementById(id);
    modal.classList.add('hidden');
    removeNoScrollIfNoModals();
    focusManager.closeModal(modal);
    if (afterDismiss) afterDismiss();
}

function dismissClientPicker() { dismissModal('client-picker-modal'); }
function dismissPlayChoice() { dismissModal('play-choice-modal', () => { state._pendingClientId = null; }); }
function dismissPlaySuccess() { dismissModal('play-success-modal'); }
function dismissUpdateSuccess() { dismissModal('update-success-modal'); }
function dismissRecRestartModal() { pendingNavHash = null; dismissModal('rec-restart-modal'); }
function dismissPlaylistRestartModal() { pendingNavHash = null; dismissModal('playlist-restart-modal'); }

function openRecRestartModal() {
    const modal = document.getElementById('rec-restart-modal');
    modal.classList.remove('hidden');
    lockScroll();
    focusManager.openModal(modal);
}

function openPlaylistRestartModal() {
    const modal = document.getElementById('playlist-restart-modal');
    modal.classList.remove('hidden');
    lockScroll();
    focusManager.openModal(modal);
}

function getClientStatusText(client) {
    if (client.is_playing) {
        return { text: 'Playing', cls: 'status-playing' };
    }
    if (client.is_mobile) {
        return { text: 'Idle — start playing on device first', cls: 'status-mobile' };
    }
    return { text: 'Idle — may be slow to respond', cls: 'status-idle' };
}

function populateClientList(clients) {
    const listEl = document.getElementById('client-list');
    const emptyState = document.getElementById('client-empty-state');

    const hintEl = document.getElementById('client-picker-hint');

    if (!clients.length) {
        listEl.innerHTML = '';
        emptyState.classList.remove('hidden');
        hintEl.classList.add('hidden');
        return;
    }

    emptyState.classList.add('hidden');
    hintEl.classList.remove('hidden');
    listEl.innerHTML = clients.map(client => {
        const status = getClientStatusText(client);
        return `
        <div class="client-item" data-client-id="${escapeHtml(client.client_id)}"
             role="option" tabindex="0"
             aria-label="${escapeHtml(client.name)} — ${escapeHtml(client.product)} on ${escapeHtml(client.platform)} — ${status.text}">
            <div class="client-status-dot ${client.is_playing ? 'playing' : (client.is_mobile ? 'mobile' : 'idle')}" aria-hidden="true"></div>
            <div class="client-info">
                <div class="client-name">${escapeHtml(client.name)}</div>
                <span class="client-product-badge">${escapeHtml(client.product)}</span>
                <span class="client-platform">${escapeHtml(client.platform)}</span>
                <div class="client-status-text ${status.cls}">${status.text}</div>
            </div>
        </div>`;
    }).join('');

    listEl.querySelectorAll('.client-item').forEach(item => {
        item.addEventListener('click', () => handleClientSelect(item.dataset.clientId));
        item.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleClientSelect(item.dataset.clientId);
            }
        });
    });
}

async function refreshClientList() {
    const listEl = document.getElementById('client-list');
    const emptyState = document.getElementById('client-empty-state');
    emptyState.querySelector('p').textContent = 'No Plex clients active. Open Plexamp or Plex first.';
    emptyState.classList.add('hidden');
    listEl.innerHTML = '<div class="client-loading"><div class="spinner"></div><p>Finding devices...</p></div>';

    try {
        const clients = await fetchPlexClients();
        state.plexClients = clients;
        populateClientList(clients);
    } catch (error) {
        // Show error inline in the picker so user can retry with refresh button
        listEl.innerHTML = '';
        emptyState.querySelector('p').textContent = 'Failed to find devices. Check that Plex is running.';
        emptyState.classList.remove('hidden');
    }
}

async function handlePlayNow() {
    if (!state.playlist.length) {
        showError('No tracks to play');
        return;
    }

    // Show client picker modal with loading spinner while fetching
    const modal = document.getElementById('client-picker-modal');
    modal.classList.remove('hidden');
    lockScroll();
    focusManager.openModal(modal);

    await refreshClientList();
}

function handleClientSelect(clientId) {
    const client = state.plexClients.find(c => c.client_id === clientId);
    if (!client) return;

    dismissClientPicker();

    if (client.is_playing) {
        // Store pending client ID for choice modal callbacks
        state._pendingClientId = clientId;
        const choiceModal = document.getElementById('play-choice-modal');
        choiceModal.classList.remove('hidden');
        lockScroll();
        focusManager.openModal(choiceModal);
    } else {
        executePlayQueue(clientId, 'replace');
    }
}

async function executePlayQueue(clientId, mode) {
    const choiceModal = document.getElementById('play-choice-modal');
    if (!choiceModal.classList.contains('hidden')) {
        dismissPlayChoice();
    }
    state._pendingClientId = null;
    if (!clientId) {
        showError('No device selected');
        return;
    }
    setLoading(true, 'Sending to device...');

    try {
        const ratingKeys = state._pendingRatingKeys || state.playlist.map(t => t.rating_key);
        state._pendingRatingKeys = null;
        const response = await createPlayQueue(ratingKeys, clientId, mode);

        setLoading(false);
        if (response.success) {
            const message = `${response.tracks_queued} tracks sent to ${response.client_name}`;
            document.getElementById('play-success-message').textContent = message;
            const playSuccessModal = document.getElementById('play-success-modal');
            playSuccessModal.classList.remove('hidden');
            lockScroll();
            focusManager.openModal(playSuccessModal);
        } else {
            let errorMsg = response.error || 'Failed to start playback';
            if (response.error_code === 'not_found') {
                errorMsg = "Device couldn't be reached. Try starting playback on the device first, then re-open the picker.";
            }
            showError(errorMsg);
        }
    } catch (error) {
        setLoading(false);
        showError(error.message);
    }
}

function handlePlaySuccessNewPlaylist() {
    dismissPlaySuccess();
    resetPlaylistState();
}

function toggleSaveModeDropdown() {
    const dropdown = document.getElementById('save-mode-dropdown');
    const btn = document.getElementById('save-mode-dropdown-btn');
    const isHidden = dropdown.classList.contains('hidden');

    dropdown.classList.toggle('hidden');
    btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');

    if (isHidden) {
        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                dropdown.classList.add('hidden');
                btn.setAttribute('aria-expanded', 'false');
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    }
}

// =============================================================================
// Instant Queue — Update Existing Handlers (005)
// =============================================================================

async function fetchAndPopulatePlaylists() {
    const picker = document.getElementById('playlist-picker');

    // Only fetch if cache is empty
    if (!state.plexPlaylists.length) {
        // Show loading state in picker
        picker.innerHTML = '<option value="" disabled>Loading playlists...</option>';

        try {
            state.plexPlaylists = await fetchPlexPlaylists();
        } catch (error) {
            showError('Failed to load playlists: ' + error.message);
            picker.innerHTML = '<option value="__scratch__">MediaSage - Now Playing</option>';
            return;
        }
    }

    // Rebuild picker options: fixed scratch option first, then server playlists
    picker.innerHTML = '<option value="__scratch__">MediaSage - Now Playing</option>';
    for (const pl of state.plexPlaylists) {
        // Skip if it's the same as the scratch playlist title (avoid duplicate)
        if (pl.title === 'MediaSage - Now Playing') continue;
        const option = document.createElement('option');
        option.value = pl.rating_key;
        option.textContent = `${pl.title} (${pl.track_count} tracks)`;
        picker.appendChild(option);
    }

    // Restore previous selection if available
    if (state.selectedPlaylistId) {
        picker.value = state.selectedPlaylistId;
    }
}

function updateAppendTrackCount() {
    if (state.saveMode !== 'append') return;

    const count = state.playlist.length;
    const saveBtn = document.getElementById('save-playlist-btn');
    if (saveBtn) saveBtn.innerHTML = `<span class="btn-label-long">Add ${count} track${count !== 1 ? 's' : ''}</span><span class="btn-label-short">Append</span>`;
}

function setSaveMode(mode) {
    state.saveMode = mode;

    // Update dropdown active states
    const dropdown = document.getElementById('save-mode-dropdown');
    dropdown.classList.add('hidden');
    document.getElementById('save-mode-dropdown-btn').setAttribute('aria-expanded', 'false');

    dropdown.querySelectorAll('.save-mode-option').forEach(opt => {
        const isActive = opt.dataset.mode === mode;
        opt.classList.toggle('active', isActive);
        opt.querySelector('.save-mode-check').innerHTML = isActive ? '&#10003;' : '';
    });

    // Toggle UI elements
    const saveBtn = document.getElementById('save-playlist-btn');
    const nameContainer = document.querySelector('.playlist-name-container');
    const pickerContainer = document.getElementById('playlist-picker-container');

    if (mode === 'new') {
        const _saveLabel = state.config?.media_server === 'jellyfin' ? 'Jellyfin' : 'Plex';
        saveBtn.innerHTML = `<span class="btn-label-long">Save to ${_saveLabel}</span><span class="btn-label-short">Save</span>`;
        nameContainer.classList.remove('hidden');
        pickerContainer.classList.add('hidden');
    } else if (mode === 'replace') {
        saveBtn.innerHTML = '<span class="btn-label-long">Replace all tracks</span><span class="btn-label-short">Replace</span>';
        nameContainer.classList.add('hidden');
        pickerContainer.classList.remove('hidden');
        if (state.playlist.length > 0) fetchAndPopulatePlaylists();
    } else if (mode === 'append') {
        const count = state.playlist.length;
        saveBtn.innerHTML = `<span class="btn-label-long">Add ${count} track${count !== 1 ? 's' : ''}</span><span class="btn-label-short">Append</span>`;
        nameContainer.classList.add('hidden');
        pickerContainer.classList.remove('hidden');
        if (state.playlist.length > 0) fetchAndPopulatePlaylists();
    }

    // Persist to localStorage (US3 — T017)
    try { localStorage.setItem('mediasage-save-mode', mode); } catch (e) { /* private browsing */ }
}

async function handleUpdatePlaylist() {
    const picker = document.getElementById('playlist-picker');
    const playlistId = picker.value;
    const matchedPlaylist = state.plexPlaylists.find(p => p.rating_key === playlistId);
    const playlistTitle = matchedPlaylist?.title || picker.options[picker.selectedIndex]?.textContent || 'Playlist';

    if (!playlistId) {
        showError('Please select a playlist');
        return;
    }

    if (!state.playlist.length) {
        showError('Playlist is empty');
        return;
    }

    setLoading(true, 'Updating playlist...');

    try {
        const ratingKeys = state.playlist.map(t => t.rating_key);
        const response = await sendPlaylistUpdate(
            playlistId,
            ratingKeys,
            state.saveMode,
            state.narrative,
        );

        setLoading(false);
        if (response.success) {
            // Show update success modal with mode-aware message
            let message;
            if (state.saveMode === 'append') {
                message = `Updated ${playlistTitle} — Added ${response.tracks_added} tracks`;
                if (response.duplicates_skipped > 0) {
                    message += ` (${response.duplicates_skipped} duplicates skipped)`;
                }
            } else {
                message = `Updated ${playlistTitle} — Replaced with ${response.tracks_added} tracks`;
            }

            if (response.warning) {
                message += ` ⚠ ${response.warning}`;
            }

            document.getElementById('update-success-message').textContent = message;

            const openBtn = document.getElementById('update-open-in-plex-btn');
            if (response.playlist_url) {
                openBtn.href = response.playlist_url;
                openBtn.style.display = '';
            } else {
                openBtn.style.display = 'none';
            }

            const updateModal = document.getElementById('update-success-modal');
            updateModal.classList.remove('hidden');
            lockScroll();
            focusManager.openModal(updateModal);

            // Invalidate playlist cache so newly created scratch playlist appears next time
            state.plexPlaylists = [];
        } else {
            showError(response.error || 'Failed to update playlist');
        }
    } catch (error) {
        setLoading(false);
        showError(error.message);
    }
}

function handleUpdateSuccessNewPlaylist() {
    dismissUpdateSuccess();
    resetPlaylistState();
}

// =============================================================================
// Recommendation View (006)
// =============================================================================

const PLAYLIST_PROMPT_GROUPS = [
    /* Mood / Energy */
    [
        "Happy but not annoying about it",
        "Sad in a way that feels good",
        "Angry, the productive kind",
        "Euphoric, peak of a good night",
        "Dreamy and slightly out of focus",
        "Quiet devastation, keep moving",
        "Wistful, not wallowing",
        "Warm like the end of something good",
        "Tense, something's about to happen",
        "Bittersweet and okay with it",
        "Dark but not hopeless",
        "Hopeful, first day of something",
        "Numb, just need the room filled",
        "Giddy, almost embarrassingly so",
        "Restless, needs to match the mood",
        "Nostalgic for something nameless",
        "Melancholy with good bones",
        "Calm but not sleepy",
        "Raw and unpolished",
        "The comedown after something great",
    ],
    /* Activity / Context */
    [
        "Cooking slowly, no one's waiting",
        "Late night drive, no destination",
        "Last hour before a deadline",
        "Long run, needs to pull you forward",
        "Pre-game, getting the nerve up",
        "Dinner party winding down well",
        "Highway, windows cracked",
        "Deep work, two hours, no surfacing",
        "Getting ready, building confidence",
        "Decompressing after a hard week",
        "Solo night in, no explanation",
        "Long flight, trying not to think",
        "Cleaning like you mean it",
        "Slow afternoon in the garden",
        "Walk home after something big",
        "After the party, just the dishes",
        "Slow dance, no one watching",
        "First coffee, easing in gently",
        "Walking a city you don't know",
        "BBQ that peaked an hour ago",
    ],
    /* Era / Decade */
    [
        "1970s soul, windows down",
        "Classic rock with actual grit",
        "1983, in the best possible way",
        "Early 90s indie, four-track raw",
        "Late 90s, before it all sped up",
        "1967, before psychedelia curdled",
        "2004 indie rock, blog-era peak",
        "Motown, hits and deep cuts both",
        "80s R&B, lush and unhurried",
        "90s hip hop, NY and LA both",
        "1972, recorded in someone's house",
        "Early 2010s, last of guitar bands",
        "British Invasion, no novelty acts",
        "1957 jazz, smoke and late hours",
        "2003 pop-punk, embarrassingly good",
        "Outlaw country, pre-mainstream",
        "80s post-punk, angular and cold",
        "90s rave, before it was a brand",
        "Krautrock, motorik and meditative",
        "Late 80s hip hop, the invention",
    ],
    /* Genre / Style */
    [
        "Jazz, late and smoky, no hurry",
        "Ambient, no pulse, just texture",
        "Punk, fast and under two minutes",
        "Soul with real weight behind it",
        "Metal that means what it says",
        "Acoustic folk, campfire honest",
        "Reggae, slow afternoon, no agenda",
        "Electronic, precise and cold",
        "Blues that invented everything else",
        "Country that earns the emotion",
        "Afrobeat, propulsive and communal",
        "Gospel with real conviction",
        "Indie pop, bright and aching",
        "Hardcore with something to say",
        "Bossa nova, unhurried and warm",
        "Lo-fi hip hop, dusty and patient",
        "Post-rock that earns its ending",
        "Disco, uncut and unapologetic",
        "Americana with dirt on it",
        "Shoegaze, loud and interior",
    ],
    /* Tempo / Danceability */
    [
        "Slow, nothing is rushing anywhere",
        "Midtempo groove, head nodding only",
        "Full energy, don't let up",
        "Dance floor, 120 BPM minimum",
        "Half-tempo, barely moving",
        "Builds slow, earns the drop",
        "Upbeat without being relentless",
        "Shuffling, laidback swing feel",
        "Relentless, no room to breathe",
        "Hypnotic, same thing, slight shifts",
        "Short and punchy, keep moving",
        "Slow burn that actually pays off",
        "Danceable, room for conversation",
        "Fast and slightly out of control",
        "Gentle pulse, background presence",
        "Syncopated and playful",
        "Doom tempo, slow and heavy",
        "Bouncy and major key, unashamed",
        "Sparse, lots of space in it",
        "Peaks and valleys, earns the quiet",
    ],
];

const REC_PROMPT_GROUPS = [
    /* Mood / Vibe */
    [
        "Melancholy I want to sit inside",
        "Warm and analog, like vinyl sounds",
        "Bleak and beautiful at once",
        "Joyful with no irony in it",
        "Unsettling in a way I can't name",
        "Tender without being soft",
        "Cold and a little industrial",
        "Romantic but not embarrassing",
        "Restless and searching",
        "Nostalgic for a time before me",
        "Built for a real release",
        "Dense and patient, rewards time",
        "Strange and slightly off-kilter",
        "Cinematic, feels like a place",
        "Austere, almost nothing there",
        "Deeply sad, don't soften it",
        "Euphoric and earned, not cheap",
    ],
    /* Sounds-Like */
    [
        "Radiohead, but room to breathe",
        "Nick Cave with some hope left",
        "Early Springsteen, less polish",
        "Joni Mitchell making a jazz record",
        "Prince stripped to the bones",
        "Arcade Fire, quieter ambition",
        "Kendrick but more internal",
        "Tom Waits went fully ambient",
        "D'Angelo but tighter",
        "Velvet Underground energy",
        "PJ Harvey, more acoustic",
        "Late Miles Davis, electric",
        "Talking Heads but darker",
        "Coltrane went electric",
        "Portishead but less cold",
        "Neil Young without the dust",
        "Massive Attack but warmer",
    ],
    /* Genre Exploration */
    [
        "First jazz album, where to start",
        "Introduce me to krautrock",
        "Best entry point for ambient",
        "Soul that invented the form",
        "Metal without prior loyalty needed",
        "Country with actual grit in it",
        "Electronic that feels something",
        "Folk that doesn't lose me",
        "Hip hop with real patience in it",
        "Reggae beyond the obvious three",
        "Post-punk, angular, still alive",
        "Classical with a clear narrative",
        "Afrobeat with real propulsion",
        "Gospel with conviction, not comfort",
        "Experimental but I can stay",
        "Brazilian music beyond bossa",
        "Blues that explains what came after",
    ],
    /* Era / Era-Adjacent */
    [
        "Timeless, no decade owns it",
        "Pure 1970s warmth, room sound",
        "Sounds like 1983, best way",
        "Late 60s psychedelia, still intact",
        "Early 90s indie, lo-fi earnest",
        "1970s jazz fusion at its peak",
        "Mid-90s hip hop, NY and hungry",
        "80s synth that aged well",
        "Late 90s slowcore, unsparing",
        "2001\u20132005 indie rock landmark",
        "1960s soul, Detroit or Memphis",
        "70s singer-songwriter, confessional",
        "80s post-punk, cold and correct",
        "90s electronic, pre-mainstream",
        "Early 2000s R&B, sophisticated",
        "Recorded in the 70s, sounds eternal",
        "1960s modal jazz, serious",
    ],
    /* Emotional Occasion */
    [
        "Breakup, raw and recent",
        "Something ended well",
        "First listen back after time away",
        "Celebrating quietly, just yourself",
        "Heavy with no explanation",
        "The week before everything changes",
        "Feeling invisible, fine with it",
        "Early stage of falling for someone",
        "Grieving, need company in it",
        "Long Sunday, nowhere to be",
        "Proud and exhausted equally",
        "3am, completely awake",
        "The last day of something",
        "Ready to start over, actually ready",
        "Complicated happy",
        "Homesick for somewhere unreachable",
        "Tired of holding it together",
    ],
    /* Deep Cuts / Underrated */
    [
        "A masterpiece nobody talks about",
        "Criminally overlooked",
        "Best album, not their famous one",
        "Too weird for radio, too good",
        "One great record, then gone",
        "Cult classic, devoted few",
        "Critics loved it, world moved on",
        "Ahead of its time",
        "The album that got away",
        "Debut that deserved a career",
        "Side project better than the main",
        "Reissued, finally getting its due",
        "Sounds like nothing else here",
        "Famous producer, album outshines",
        "The one even fans missed",
    ],
];

async function initRecommendView() {
    if (state.config?.plex_connected) {
        loadRecommendFilters();
    }
    renderPromptPills('rec-prompt-pills', 'rec-prompt-shuffle', REC_PROMPT_GROUPS);
    updateRecStep();
}

async function loadRecommendFilters() {
    // Reuse genres/decades already fetched by loadSettings() if available
    if (state.availableGenres.length === 0) {
        try {
            const stats = await apiCall('/library/stats');
            state.availableGenres = stats.genres.map(g => ({ name: g.name, count: g.count }));
            state.availableDecades = stats.decades.map(d => ({ name: d.name, count: d.count }));
        } catch (e) {
            console.error('Failed to load recommend filters:', e);
            return;
        }
    }
    // No chips selected = no filter (all albums included)
    renderRecFilterChips();
    updateAlbumLimitButtons();
    updateRecAlbumPreview();
}

function renderRecFilterChips() {
    const genreContainer = document.getElementById('rec-genre-chips');
    const decadeContainer = document.getElementById('rec-decade-chips');
    if (!genreContainer || !decadeContainer) return;

    genreContainer.innerHTML = state.availableGenres.map(genre => {
        const isSelected = state.rec.selectedGenres.includes(genre.name);
        return `<button class="chip ${isSelected ? 'selected' : ''}"
                data-genre="${escapeHtml(genre.name)}"
                aria-pressed="${isSelected}">
            ${escapeHtml(genre.name)}
        </button>`;
    }).join('');

    decadeContainer.innerHTML = state.availableDecades.map(decade => {
        const isSelected = state.rec.selectedDecades.includes(decade.name);
        return `<button class="chip ${isSelected ? 'selected' : ''}"
                data-decade="${escapeHtml(decade.name)}"
                aria-pressed="${isSelected}">
            ${escapeHtml(decade.name)}
        </button>`;
    }).join('');

    // Sync toggle labels
    const genreToggle = document.getElementById('rec-genre-toggle-all');
    if (genreToggle) {
        const allSelected = state.availableGenres.length > 0 &&
            state.rec.selectedGenres.length === state.availableGenres.length;
        genreToggle.textContent = allSelected ? 'Deselect All' : 'Select All';
    }
    const decadeToggle = document.getElementById('rec-decade-toggle-all');
    if (decadeToggle) {
        const allSelected = state.availableDecades.length > 0 &&
            state.rec.selectedDecades.length === state.availableDecades.length;
        decadeToggle.textContent = allSelected ? 'Deselect All' : 'Select All';
    }
}

function pickOnePerGroup(groups) {
    return groups.map(g => g[Math.floor(Math.random() * g.length)]);
}

function renderPromptPills(containerId, shuffleBtnId, groups) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const selected = pickOnePerGroup(groups);
    container.innerHTML = selected.map(p =>
        `<button class="prompt-pill">${escapeHtml(p)}</button>`
    ).join('');
    const btn = document.getElementById(shuffleBtnId);
    if (btn) btn.hidden = groups.some(g => g.length <= 1);
}

function shufflePromptPills(containerId, groups) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const currentTexts = new Set(
        [...container.querySelectorAll('.prompt-pill')].map(p => p.textContent)
    );
    const selected = groups.map(group => {
        const available = group.filter(p => !currentTexts.has(p));
        const pool = available.length > 0 ? available : group;
        return pool[Math.floor(Math.random() * pool.length)];
    });
    const pills = container.querySelectorAll('.prompt-pill');
    pills.forEach(p => { p.style.opacity = '0'; });
    setTimeout(() => {
        pills.forEach((p, i) => {
            if (i < selected.length) p.textContent = selected[i];
        });
        pills.forEach(p => { p.style.opacity = '1'; });
    }, 150);
}

function updateRecStep() {
    window.scrollTo(0, 0);

    const steps = ['prompt', 'refine', 'setup', 'results'];
    const currentIndex = steps.indexOf(state.rec.step);

    // Update panels
    document.querySelectorAll('.rec-panel').forEach(panel => {
        const panelStep = panel.id.replace('rec-step-', '');
        panel.classList.toggle('active', panelStep === state.rec.step);
    });

    // Update progress bar
    document.querySelectorAll('#rec-steps .step').forEach(stepEl => {
        const stepName = stepEl.dataset.step;
        const stepIndex = steps.indexOf(stepName);
        stepEl.classList.toggle('active', stepName === state.rec.step);
        stepEl.classList.toggle('completed', stepIndex < currentIndex);
    });

    // Update connectors
    document.querySelectorAll('#rec-steps .step-connector').forEach((connector, i) => {
        connector.classList.toggle('completed', i < currentIndex);
    });

    // Hide progress bar on results
    const isResults = state.rec.step === 'results';
    const recProgress = document.getElementById('rec-steps');
    if (recProgress) {
        recProgress.style.display = isResults ? 'none' : '';
    }

    // Toggle footer content for results vs other screens
    const appFooter = document.querySelector('.app-footer');
    if (appFooter) appFooter.classList.toggle('app-footer--results', isResults);

    // Hide regenerate button — it's playlist-only
    const regenBtn = document.getElementById('regenerate-btn');
    if (regenBtn) regenBtn.style.display = 'none';
}

function setRecStep(step) {
    state.rec.step = step;
    updateRecStep();
}

// AbortController for cancelling in-flight recommend preview requests
let recPreviewController = null;
let recPreviewLoadingTimeout = null;

async function updateRecAlbumPreview() {
    const countEl = document.getElementById('rec-preview-count');
    const costEl = document.getElementById('rec-preview-cost');
    if (!countEl) return;

    // Cancel any in-flight request
    if (recPreviewController) {
        recPreviewController.abort();
    }
    recPreviewController = new AbortController();

    // Clear any pending loading timeout
    if (recPreviewLoadingTimeout) {
        clearTimeout(recPreviewLoadingTimeout);
    }

    // Only show loading state if request takes longer than 150ms
    recPreviewLoadingTimeout = setTimeout(() => {
        countEl.innerHTML = '<span class="preview-spinner"></span> Counting...';
        costEl.textContent = '';
    }, 150);

    try {
        // All selected = no filter (avoids excluding untagged albums)
        const allGenres = state.availableGenres.length > 0 &&
            state.rec.selectedGenres.length === state.availableGenres.length;
        const allDecades = state.availableDecades.length > 0 &&
            state.rec.selectedDecades.length === state.availableDecades.length;
        const params = new URLSearchParams();
        if (!allGenres && state.rec.selectedGenres.length) {
            params.set('genres', state.rec.selectedGenres.join(','));
        }
        if (!allDecades && state.rec.selectedDecades.length) {
            params.set('decades', state.rec.selectedDecades.join(','));
        }
        params.set('max_albums', state.rec.maxAlbumsToAI);

        const response = await fetch(`/api/recommend/albums/preview?${params}`, {
            signal: recPreviewController.signal,
        });

        if (!response.ok) {
            throw new Error('Failed to get album preview');
        }

        const data = await response.json();

        // Clear loading timeout - response arrived fast
        clearTimeout(recPreviewLoadingTimeout);

        updateRecPreviewDisplay(data.matching_albums, data.albums_to_send, data.estimated_cost);
    } catch (error) {
        // Clear loading timeout on error too
        clearTimeout(recPreviewLoadingTimeout);

        // Ignore abort errors - they're expected when cancelling
        if (error.name === 'AbortError') {
            return;
        }
        console.error('Album preview error:', error);
        countEl.textContent = '-- albums';
        costEl.textContent = 'Est. cost: --';
    }
}

function updateRecPreviewDisplay(matchingAlbums, albumsToSend, estimatedCost) {
    const countEl = document.getElementById('rec-preview-count');
    const costEl = document.getElementById('rec-preview-cost');

    // Update album count display
    if (albumsToSend < matchingAlbums) {
        countEl.textContent = `${matchingAlbums.toLocaleString()} albums (sending ${albumsToSend.toLocaleString()} to AI)`;
    } else {
        countEl.textContent = `${matchingAlbums.toLocaleString()} albums`;
    }

    // For local providers, hide cost estimate
    const isLocalProvider = state.config?.is_local_provider ?? false;
    if (isLocalProvider) {
        costEl.textContent = '';
    } else if (estimatedCost > 0) {
        costEl.textContent = `Est. cost: $${estimatedCost.toFixed(4)}`;
    } else {
        costEl.textContent = 'Est. cost: --';
    }

    // Update "All/Max" button label based on whether filtered albums fit in context
    const maxBtn = document.querySelector('.album-limit-selector .limit-btn[data-limit="0"]');
    if (maxBtn && state.config) {
        const maxAllowed = state.config.max_albums_to_ai || 2500;
        maxBtn.textContent = matchingAlbums <= maxAllowed ? 'All' : `Max (${maxAllowed.toLocaleString()})`;
    }
}

async function handlePromptSubmit() {
    const prompt = document.getElementById('rec-prompt-input')?.value || '';
    if (!prompt.trim()) {
        showError('Please enter a prompt');
        return;
    }
    state.rec.prompt = prompt;

    const btn = document.getElementById('rec-prompt-next');
    if (btn) btn.disabled = true;

    const stepLoader = showTimedStepLoading([
        { id: 'analyzing', text: 'Analyzing your request...', status: 'active' },
        { id: 'questions', text: 'Crafting questions...', status: 'pending' },
    ]);

    // Fire filter analysis in parallel (cached as a promise for the setup step)
    state.rec.filterAnalysisPromise = apiCall('/recommend/analyze-prompt', {
        method: 'POST',
        body: JSON.stringify({
            prompt: state.rec.prompt,
            genres: state.availableGenres.map(g => g.name),
            decades: state.availableDecades.map(d => d.name),
        }),
    }).catch(() => null);  // Swallow errors — fallback handled in handleRefineNext

    try {
        // Fire question generation (only needs the prompt)
        const data = await apiCall('/recommend/questions', {
            method: 'POST',
            body: JSON.stringify({ prompt: state.rec.prompt }),
        });

        state.rec.questions = data.questions;
        state.rec.sessionId = data.session_id;
        state.rec.answers = data.questions.map(() => null);
        state.rec.answerTexts = data.questions.map(() => '');

        renderRecQuestions();
        setRecStep('refine');
    } catch (e) {
        showError(e.message);
    } finally {
        stepLoader.finish();
        if (btn) btn.disabled = false;
    }
}

async function handleRefineNext() {
    const infoBanner = document.getElementById('rec-filter-info');

    // Await the cached filter analysis promise
    const filterData = await state.rec.filterAnalysisPromise;
    if (filterData) {
        state.rec.selectedGenres = filterData.genres || [];
        state.rec.selectedDecades = filterData.decades || [];
        if (infoBanner) infoBanner.classList.remove('hidden');
    } else {
        // Fallback: all included (empty = no filter)
        state.rec.selectedGenres = state.availableGenres.map(g => g.name);
        state.rec.selectedDecades = state.availableDecades.map(d => d.name);
        if (infoBanner) infoBanner.classList.add('hidden');
    }

    renderRecFilterChips();
    updateRecAlbumPreview();
    setRecStep('setup');
}


function renderQuestions(questions, answers, answerTexts, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = questions.map((q, qi) => `
        <div class="question-card" data-question-index="${qi}">
            <p class="question-text">${escapeHtml(q.question_text)}</p>
            <div class="question-options">
                ${q.options.map((opt, oi) => `
                    <button class="option-pill ${answers[qi] === opt ? 'selected' : ''}"
                            data-question="${qi}" data-option="${oi}">
                        ${escapeHtml(opt)}
                    </button>
                `).join('')}
            </div>
            <input type="text" class="question-freetext" placeholder="Add your own detail (optional)"
                   data-question="${qi}" value="${escapeHtml(answerTexts[qi] || '')}">
            <button class="question-skip" data-question="${qi}">Skip this question</button>
        </div>
    `).join('');
}

function renderRecQuestions() {
    renderQuestions(state.rec.questions, state.rec.answers, state.rec.answerTexts, 'rec-questions-container');
}

function renderPlaylistQuestions() {
    renderQuestions(state.questions, state.questionAnswers, state.questionTexts, 'playlist-questions-container');
}

function setupQuestionEventHandlers(container, stateObj, renderFn) {
    container.addEventListener('click', e => {
        const pill = e.target.closest('.option-pill');
        if (pill) {
            const qi = parseInt(pill.dataset.question);
            const oi = parseInt(pill.dataset.option);
            const option = stateObj.questions[qi]?.options[oi];
            if (stateObj.answers[qi] === option) {
                stateObj.answers[qi] = null;
            } else {
                stateObj.answers[qi] = option;
            }
            renderFn();
            return;
        }
        const skip = e.target.closest('.question-skip');
        if (skip) {
            const qi = parseInt(skip.dataset.question);
            stateObj.answers[qi] = null;
            stateObj.answerTexts[qi] = '';
            renderFn();
        }
    });

    container.addEventListener('input', e => {
        if (e.target.classList.contains('question-freetext')) {
            const qi = parseInt(e.target.dataset.question);
            stateObj.answerTexts[qi] = e.target.value;
        }
    });
}

async function handlePlaylistRefineNext() {
    // Await the cached filter analysis promise (fired in parallel during handleAnalyzePrompt)
    const response = await state.filterAnalysisPromise;
    if (response) {
        // Track analysis costs
        state.sessionTokens += response.token_count || 0;
        state.sessionCost += response.estimated_cost || 0;

        state.availableGenres = response.available_genres;
        state.availableDecades = response.available_decades;
        state.selectedGenres = response.suggested_genres;
        state.selectedDecades = response.suggested_decades;
    } else {
        // Fallback: fetch stats directly if analysis failed
        try {
            const stats = await fetchLibraryStats();
            state.availableGenres = stats.genres;
            state.availableDecades = stats.decades;
            state.selectedGenres = stats.genres.map(g => g.name);
            state.selectedDecades = stats.decades.map(d => d.name);
        } catch {
            // Last resort: empty filters
            state.selectedGenres = [];
            state.selectedDecades = [];
        }
    }

    state.step = 'filters';
    updateStep();
    updateFilters();
    updateFilterPreview();
}

async function handleRecSwitchToDiscovery() {
    state.rec.loading = true;
    const stepLoader = showTimedStepLoading([
        { id: 'switching', text: 'Switching to discovery mode...', status: 'active' },
    ]);

    try {
        const data = await apiCall('/recommend/switch-mode', {
            method: 'POST',
            body: JSON.stringify({
                session_id: state.rec.sessionId,
                mode: 'discovery',
            }),
        });

        state.rec.mode = 'discovery';
        state.rec.sessionId = data.session_id;
        document.querySelectorAll('.rec-mode-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.recMode === 'discovery');
            b.setAttribute('aria-pressed', b.dataset.recMode === 'discovery' ? 'true' : 'false');
        });

        stepLoader.finish();
        handleRecGenerate();
    } catch (e) {
        stepLoader.finish();
        showError(e.message);
        state.rec.loading = false;
    }
}

async function handleRecGenerate() {
    state.rec.loading = true;

    const progressSteps = [
        { id: 'selecting', text: 'Choosing albums from your library...', status: 'active' },
        { id: 'researching_primary', text: 'Researching an album...', status: 'pending' },
        { id: 'researching_secondary', text: 'Looking up additional picks...', status: 'pending' },
        { id: 'extracting_facts', text: 'Analyzing research sources...', status: 'pending' },
        { id: 'writing', text: 'Writing the pitch...', status: 'pending' },
        { id: 'validating', text: 'Fact-checking the pitch...', status: 'pending' },
        { id: 'rewriting', text: 'Refining the pitch...', status: 'pending' },
    ];
    showStepLoading(progressSteps);

    // Abort if no data arrives for 120 seconds (server hang, network loss)
    const controller = new AbortController();
    let staleTimer = setTimeout(() => controller.abort(), 120000);
    const resetStaleTimer = () => {
        clearTimeout(staleTimer);
        staleTimer = setTimeout(() => controller.abort(), 120000);
    };

    try {
        const response = await fetch('/api/recommend/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            body: JSON.stringify({
                session_id: state.rec.sessionId,
                answers: state.rec.answers,
                answer_texts: state.rec.answerTexts,
                mode: state.rec.mode,
                genres: (state.availableGenres.length > 0 && state.rec.selectedGenres.length === state.availableGenres.length) ? [] : state.rec.selectedGenres,
                decades: (state.availableDecades.length > 0 && state.rec.selectedDecades.length === state.availableDecades.length) ? [] : state.rec.selectedDecades,
                familiarity_pref: state.rec.familiarityPref,
                max_albums: state.rec.maxAlbumsToAI,
            }),
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: 'Request failed' }));
            throw new Error(err.detail || err.error || 'Generation failed');
        }

        // Read SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            resetStaleTimer();

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            let currentEventType = '';
            let currentData = '';
            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    currentEventType = line.slice(7).trim();
                    continue;
                }
                if (line.startsWith('data: ')) {
                    currentData += line.slice(6);
                    continue;
                }
                if (line === '' && currentData) {
                    let data;
                    try {
                        data = JSON.parse(currentData);
                    } catch (parseErr) {
                        console.warn('SSE parse error:', parseErr);
                        currentData = '';
                        currentEventType = '';
                        continue;
                    }
                    if (currentEventType === 'error' && data.message) {
                        throw new Error(data.message);
                    }
                    if (data.step) {
                        updateStepProgress(data.step);
                    }
                    if (data.recommendations) {
                        state.rec.recommendations = data.recommendations;
                        state.rec.tokenCount = data.token_count || 0;
                        state.rec.estimatedCost = data.estimated_cost || 0;
                        state.rec.researchWarning = data.research_warning;
                        if (data.result_id) {
                            state.rec.resultId = data.result_id;
                            markHistoryStale();
                        }
                    }
                    currentData = '';
                    currentEventType = '';
                }
            }
        }

        hideStepLoading();
        if (state.rec.recommendations.length === 0) {
            showError('No recommendations were received. Please try again.');
            return;
        }
        renderRecResults();
        setRecStep('results');

        // Update URL to deep link for this result
        if (state.rec.resultId) {
            history.replaceState(null, '', `#result/${state.rec.resultId}`);
        }
    } catch (e) {
        hideStepLoading();
        if (e.name === 'AbortError') {
            showError('Recommendation timed out — the server may be overloaded. Please try again.');
        } else {
            showError(e.message);
        }
    } finally {
        clearTimeout(staleTimer);
        state.rec.loading = false;
    }
}

// =============================================================================
// Step-Based Loading Overlay (shared by playlist + album flows)
// =============================================================================

/** Playlist generation: maps fine-grained SSE step IDs to consolidated visible steps */
const PLAYLIST_STEP_MAP = {
    fetching: 'preparing',
    filtering: 'preparing',
    preparing: 'preparing',
    ai_working: 'ai_working',
    parsing: 'matching',
    matching: 'matching',
    narrative: 'narrative',
};

const PLAYLIST_STEPS = [
    { id: 'preparing', text: 'Preparing your library...', status: 'active' },
    { id: 'ai_working', text: 'AI is curating your playlist...', status: 'pending' },
    { id: 'matching', text: 'Matching tracks to your library...', status: 'pending' },
    { id: 'narrative', text: 'Writing playlist story...', status: 'pending' },
];

// --- Step timing: enforce minimum dwell per step so they don't flash by ---
const _stepTiming = { lastStepTime: 0, queue: [], timer: null };
const MIN_STEP_MS = 500;

function _processStepQueue() {
    if (_stepTiming.timer) return;
    if (_stepTiming.queue.length === 0) return;

    const elapsed = Date.now() - _stepTiming.lastStepTime;
    if (elapsed < MIN_STEP_MS) {
        _stepTiming.timer = setTimeout(() => {
            _stepTiming.timer = null;
            _processStepQueue();
        }, MIN_STEP_MS - elapsed);
        return;
    }

    const next = _stepTiming.queue.shift();
    if (next.type === 'hide') {
        _applyHideStepLoading();
        _stepTiming.queue = [];
    } else {
        _applyStepUpdate(next.id);
        _stepTiming.lastStepTime = Date.now();
        if (_stepTiming.queue.length > 0) {
            _processStepQueue();
        }
    }
}

function _applyStepUpdate(activeStep) {
    const items = document.querySelectorAll('.step-progress-item');
    let foundActive = false;
    items.forEach(item => {
        const id = item.dataset.progressId;
        if (id === activeStep) {
            foundActive = true;
            item.className = 'step-progress-item active';
            item.querySelector('.step-progress-icon').innerHTML = '<div class="step-progress-spinner"></div>';
        } else if (!foundActive) {
            item.className = 'step-progress-item completed';
            item.querySelector('.step-progress-icon').innerHTML = '<span style="color:var(--success)">&#10003;</span>';
        }
    });
}

function _applyHideStepLoading() {
    const overlay = document.getElementById('step-loading-overlay');
    if (overlay) overlay.classList.add('hidden');
    removeNoScrollIfNoModals();
}

// --- Public API ---

function showTimedStepLoading(steps, intervalMs = 2000) {
    showStepLoading(steps);
    let stepIndex = 0;
    const stepIds = steps.map(s => s.id);
    const timerId = setInterval(() => {
        stepIndex++;
        if (stepIndex < stepIds.length) {
            updateStepProgress(stepIds[stepIndex]);
        } else {
            clearInterval(timerId);
        }
    }, intervalMs);
    return {
        finish() {
            clearInterval(timerId);
            for (let i = stepIndex + 1; i < stepIds.length; i++) {
                updateStepProgress(stepIds[i]);
            }
            hideStepLoading();
        }
    };
}

function showStepLoading(steps) {
    const overlay = document.getElementById('step-loading-overlay');
    const list = document.getElementById('step-progress-list');
    if (!overlay || !list) return;

    // Reset timing state for fresh overlay
    _stepTiming.lastStepTime = Date.now();
    _stepTiming.queue = [];
    clearTimeout(_stepTiming.timer);
    _stepTiming.timer = null;

    list.innerHTML = steps.map(s => `
        <div class="step-progress-item ${s.status}" data-progress-id="${s.id}">
            <div class="step-progress-icon">
                ${s.status === 'completed' ? '<span style="color:var(--success)">&#10003;</span>' :
                  s.status === 'active' ? '<div class="step-progress-spinner"></div>' :
                  '<span style="color:var(--text-muted)">&#9675;</span>'}
            </div>
            <span class="step-progress-text">${escapeHtml(s.text)}</span>
        </div>
    `).join('');

    overlay.classList.remove('hidden');
    lockScroll();
}

function updateStepProgress(activeStep) {
    _stepTiming.queue.push({ type: 'step', id: activeStep });
    _processStepQueue();
}

function hideStepLoading() {
    _stepTiming.queue.push({ type: 'hide' });
    _processStepQueue();
}

function renderRecResults() {
    const primary = state.rec.recommendations.find(r => r.rank === 'primary');
    const secondaries = state.rec.recommendations.filter(r => r.rank === 'secondary');

    // Research warning
    const warningEl = document.getElementById('rec-research-warning');
    if (warningEl && state.rec.researchWarning) {
        warningEl.textContent = state.rec.researchWarning;
        warningEl.classList.remove('hidden');
    } else if (warningEl) {
        warningEl.classList.add('hidden');
    }

    // Primary recommendation
    const primaryContainer = document.getElementById('rec-primary-result');
    if (primaryContainer && primary) {
        const artHtml = primary.art_url
            ? `<img class="rec-primary-art" src="${escapeHtml(primary.art_url)}" alt="${escapeHtml(primary.album)}"
                    data-artist="${escapeHtml(primary.artist)}"
                    onerror="this.outerHTML=artPlaceholderHtml(this.dataset.artist, true)">`
            : artPlaceholderHtml(primary.artist, true).replace('art-placeholder', 'art-placeholder rec-primary-art');

        const pitch = primary.pitch || {};
        primaryContainer.innerHTML = `
            <div class="rec-primary-layout">
                ${artHtml}
                <div class="rec-primary-pitch">
                    <div class="rec-pitch-album-title">${escapeHtml(primary.album)}</div>
                    <div class="rec-pitch-artist">${escapeHtml(primary.artist)}${primary.year ? ` (${primary.year})` : ''}</div>
                    ${pitch.hook ? `<div class="rec-pitch-hook">${escapeHtml(pitch.hook)}</div>` : ''}
                    ${pitch.context ? `
                        <div class="rec-pitch-section">
                            <div class="rec-pitch-section-label">The Story</div>
                            ${escapeHtml(pitch.context)}
                        </div>` : ''}
                    ${pitch.listening_guide ? `
                        <div class="rec-pitch-section">
                            <div class="rec-pitch-section-label">How to Listen</div>
                            ${escapeHtml(pitch.listening_guide)}
                        </div>` : ''}
                    ${pitch.connection ? `
                        <div class="rec-pitch-section rec-pitch-section--connection">
                            <div class="rec-pitch-section-label">Why This Album</div>
                            ${escapeHtml(pitch.connection)}
                        </div>` : ''}
                    <div class="rec-primary-actions">
                        ${primary.track_rating_keys?.length ? `
                            <button class="btn btn-primary rec-play-btn" data-rating-keys="${escapeHtml(primary.track_rating_keys.join(','))}">&#9654; Play Now</button>
                            <button class="btn btn-secondary rec-save-btn" data-album="${escapeHtml(primary.album)}" data-artist="${escapeHtml(primary.artist)}" data-rating-keys="${escapeHtml(primary.track_rating_keys.join(','))}" data-pitch="${escapeHtml(pitch.full_text || '')}">Save to Playlist</button>
                        ` : ''}
                        ${state.rec.sessionId ? `
                            <button class="rec-action-link" id="rec-show-another">Show Me Another</button>
                            <button class="rec-action-link rec-action-link--subtle" id="rec-start-over">Start over</button>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    // Secondary recommendations
    const secondaryContainer = document.getElementById('rec-secondary-cards');
    if (secondaryContainer) {
        secondaryContainer.innerHTML = secondaries.map(rec => {
            const artHtml = rec.art_url
                ? `<img class="rec-secondary-art" src="${escapeHtml(rec.art_url)}" alt="${escapeHtml(rec.album)}"
                        data-artist="${escapeHtml(rec.artist)}"
                        onerror="this.outerHTML=artPlaceholderHtml(this.dataset.artist)">`
                : artPlaceholderHtml(rec.artist).replace('art-placeholder', 'art-placeholder rec-secondary-art');

            return `
                <div class="rec-secondary-card">
                    <div class="rec-secondary-header">
                        ${artHtml}
                        <div class="rec-secondary-info">
                            <div class="rec-secondary-title">${escapeHtml(rec.album)}</div>
                            <div class="rec-secondary-artist">${escapeHtml(rec.artist)}${rec.year ? ` (${rec.year})` : ''}</div>
                            ${rec.track_rating_keys?.length ? `
                                <div class="rec-secondary-actions">
                                    <button class="btn btn-secondary btn-sm rec-play-btn" data-rating-keys="${escapeHtml(rec.track_rating_keys.join(','))}">&#9654; Play</button>
                                    <button class="btn btn-secondary btn-sm rec-save-btn" data-album="${escapeHtml(rec.album)}" data-artist="${escapeHtml(rec.artist)}" data-rating-keys="${escapeHtml(rec.track_rating_keys.join(','))}" data-pitch="${escapeHtml(rec.pitch?.full_text || '')}">Save</button>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                    <div class="rec-secondary-pitch">${escapeHtml(rec.pitch?.short_pitch || rec.pitch?.full_text || '')}</div>
                </div>
            `;
        }).join('');
    }

    // Discovery bridge (show in library mode with active session only)
    const bridgeEl = document.getElementById('rec-discovery-bridge');
    if (bridgeEl) {
        bridgeEl.classList.toggle('hidden', state.rec.mode !== 'library' || !state.rec.sessionId);
    }

    // Update cost display in shared app footer
    const costDisplay = document.getElementById('cost-display');
    if (costDisplay) {
        if (state.rec.estimatedCost > 0) {
            costDisplay.textContent = `${state.rec.tokenCount.toLocaleString()} tokens ($${state.rec.estimatedCost.toFixed(4)})`;
        } else if (state.rec.tokenCount > 0) {
            costDisplay.textContent = `${state.rec.tokenCount.toLocaleString()} tokens`;
        } else {
            costDisplay.textContent = '';
        }
    }
}

function resetRecState() {
    state.rec.step = 'prompt';
    state.rec.prompt = '';
    state.rec.loading = false;
    state.rec.selectedGenres = [];
    state.rec.selectedDecades = [];
    state.rec.questions = [];
    state.rec.answers = [];
    state.rec.answerTexts = [];
    state.rec.sessionId = null;
    state.rec.recommendations = [];
    state.rec.tokenCount = 0;
    state.rec.estimatedCost = 0;
    state.rec.researchWarning = null;
    state.rec.resultId = null;
    state.rec.filterAnalysisPromise = null;
    // Preserve mode and familiarityPref; clear filter info banner
    const infoBanner = document.getElementById('rec-filter-info');
    if (infoBanner) infoBanner.classList.add('hidden');
    renderRecFilterChips();
    updateRecStep();
}

function setupRecEventListeners() {
    // Familiarity preference pills — restore from localStorage
    const familiarityPills = document.getElementById('rec-familiarity-pills');
    if (familiarityPills) {
        try {
            const saved = localStorage.getItem('mediasage-familiarity-pref');
            if (saved && ['any', 'comfort', 'rediscover', 'hidden_gems'].includes(saved)) {
                state.rec.familiarityPref = saved;
                familiarityPills.querySelectorAll('.chip').forEach(btn => {
                    const isSelected = btn.dataset.familiarity === saved;
                    btn.classList.toggle('selected', isSelected);
                    btn.setAttribute('aria-checked', isSelected ? 'true' : 'false');
                });
            }
        } catch (e) { /* private browsing */ }

        familiarityPills.addEventListener('click', (e) => {
            const btn = e.target.closest('.chip[data-familiarity]');
            if (!btn) return;
            state.rec.familiarityPref = btn.dataset.familiarity;
            familiarityPills.querySelectorAll('.chip').forEach(b => {
                const isSelected = b === btn;
                b.classList.toggle('selected', isSelected);
                b.setAttribute('aria-checked', isSelected ? 'true' : 'false');
            });
            try { localStorage.setItem('mediasage-familiarity-pref', state.rec.familiarityPref); } catch (e) { /* private browsing */ }
        });
    }

    // Mode buttons
    document.querySelectorAll('.rec-mode-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.rec.mode = btn.dataset.recMode;
            document.querySelectorAll('.rec-mode-btn').forEach(b => {
                b.classList.toggle('active', b === btn);
                b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
            });
            updateRecAlbumPreview();
        });
    });

    // Setup Next → generate
    const setupNext = document.getElementById('rec-setup-next');
    if (setupNext) {
        setupNext.addEventListener('click', () => handleRecGenerate());
    }

    // Refine Next → apply filter suggestions and go to setup
    const refineNext = document.getElementById('rec-refine-next');
    if (refineNext) {
        refineNext.addEventListener('click', () => handleRefineNext());
    }

    // Prompt pills
    const pillContainer = document.getElementById('rec-prompt-pills');
    if (pillContainer) {
        pillContainer.addEventListener('click', e => {
            const pill = e.target.closest('.prompt-pill');
            if (!pill) return;
            document.getElementById('rec-prompt-input').value = pill.textContent.trim();
            state.rec.prompt = pill.textContent.trim();
        });
    }

    // Shuffle button
    const shuffleBtn = document.getElementById('rec-prompt-shuffle');
    if (shuffleBtn) {
        shuffleBtn.addEventListener('click', () => shufflePromptPills('rec-prompt-pills', REC_PROMPT_GROUPS));
    }

    // Prompt Next
    const promptNext = document.getElementById('rec-prompt-next');
    if (promptNext) {
        promptNext.addEventListener('click', () => {
            handlePromptSubmit();
        });
    }

    // Questions - event delegation (recommend flow)
    const recQuestionsContainer = document.getElementById('rec-questions-container');
    if (recQuestionsContainer) {
        setupQuestionEventHandlers(recQuestionsContainer, state.rec, renderRecQuestions);
    }

    // Recommend filter chips - event delegation
    const recGenreChips = document.getElementById('rec-genre-chips');
    if (recGenreChips) {
        recGenreChips.addEventListener('click', e => {
            const chip = e.target.closest('.chip');
            if (!chip) return;
            const genre = chip.dataset.genre;
            if (state.rec.selectedGenres.includes(genre)) {
                state.rec.selectedGenres = state.rec.selectedGenres.filter(g => g !== genre);
            } else {
                state.rec.selectedGenres.push(genre);
            }
            renderRecFilterChips();
            updateRecAlbumPreview();
        });
    }

    const recDecadeChips = document.getElementById('rec-decade-chips');
    if (recDecadeChips) {
        recDecadeChips.addEventListener('click', e => {
            const chip = e.target.closest('.chip');
            if (!chip) return;
            const decade = chip.dataset.decade;
            if (state.rec.selectedDecades.includes(decade)) {
                state.rec.selectedDecades = state.rec.selectedDecades.filter(d => d !== decade);
            } else {
                state.rec.selectedDecades.push(decade);
            }
            renderRecFilterChips();
            updateRecAlbumPreview();
        });
    }

    // Genre/decade toggle all
    const recGenreToggle = document.getElementById('rec-genre-toggle-all');
    if (recGenreToggle) {
        recGenreToggle.addEventListener('click', () => {
            const allSelected = state.availableGenres.length > 0 &&
                state.rec.selectedGenres.length === state.availableGenres.length;
            state.rec.selectedGenres = allSelected ? [] : state.availableGenres.map(g => g.name);
            renderRecFilterChips();
            updateRecAlbumPreview();
        });
    }

    const recDecadeToggle = document.getElementById('rec-decade-toggle-all');
    if (recDecadeToggle) {
        recDecadeToggle.addEventListener('click', () => {
            const allSelected = state.availableDecades.length > 0 &&
                state.rec.selectedDecades.length === state.availableDecades.length;
            state.rec.selectedDecades = allSelected ? [] : state.availableDecades.map(d => d.name);
            renderRecFilterChips();
            updateRecAlbumPreview();
        });
    }

    // Step progress bar navigation (click completed steps to go back)
    document.querySelectorAll('#playlist-steps .step').forEach(stepEl => {
        stepEl.addEventListener('click', () => {
            if (stepEl.classList.contains('completed')) {
                state.step = stepEl.dataset.step;
                updateStep();
            }
        });
    });
    document.querySelectorAll('#rec-steps .step').forEach(stepEl => {
        stepEl.addEventListener('click', () => {
            if (stepEl.classList.contains('completed')) {
                setRecStep(stepEl.dataset.step);
            }
        });
    });

    // Results actions - event delegation
    document.getElementById('rec-primary-result')?.addEventListener('click', e => {
        handleRecResultAction(e);
    });
    document.getElementById('rec-secondary-cards')?.addEventListener('click', e => {
        handleRecResultAction(e);
    });

    // Show me another
    document.addEventListener('click', e => {
        if (e.target.id === 'rec-show-another') {
            handleRecGenerate();
        }
        if (e.target.id === 'rec-start-over') {
            resetRecState();
            history.replaceState(null, '', '#recommend-album');
        }
        if (e.target.id === 'rec-try-discovery') {
            handleRecSwitchToDiscovery();
        }
    });

    // Restart confirmation modal buttons
    document.getElementById('rec-restart-confirm')?.addEventListener('click', () => {
        const navHash = pendingNavHash;
        dismissRecRestartModal();
        hideStepLoading();
        resetRecState();
        if (navHash) {
            location.hash = '#' + navHash;
        } else {
            history.replaceState(null, '', '#recommend-album');
        }
    });
    document.getElementById('rec-restart-cancel')?.addEventListener('click', dismissRecRestartModal);
    document.getElementById('rec-restart-cancel-x')?.addEventListener('click', dismissRecRestartModal);

    // Playlist restart confirmation modal buttons
    document.getElementById('playlist-restart-confirm')?.addEventListener('click', () => {
        const navHash = pendingNavHash;
        dismissPlaylistRestartModal();
        setLoading(false);
        resetPlaylistState();
        if (navHash) {
            location.hash = '#' + navHash;
        } else {
            history.replaceState(null, '', '#' + hashForCurrentState());
        }
    });
    document.getElementById('playlist-restart-cancel')?.addEventListener('click', dismissPlaylistRestartModal);
    document.getElementById('playlist-restart-cancel-x')?.addEventListener('click', dismissPlaylistRestartModal);
}

function handleRecResultAction(e) {
    const playBtn = e.target.closest('.rec-play-btn');
    if (playBtn) {
        const keys = playBtn.dataset.ratingKeys.split(',');
        // Store rating keys for the play queue flow, then open client picker
        state._pendingRatingKeys = keys;
        const modal = document.getElementById('client-picker-modal');
        modal.classList.remove('hidden');
        lockScroll();
        focusManager.openModal(modal);
        refreshClientList();
        return;
    }

    const saveBtn = e.target.closest('.rec-save-btn');
    if (saveBtn) {
        const album = saveBtn.dataset.album;
        const artist = saveBtn.dataset.artist;
        const keys = saveBtn.dataset.ratingKeys.split(',');
        const pitch = saveBtn.dataset.pitch;
        handleRecSaveToPlaylist(album, artist, keys, pitch);
    }
}

async function handleRecSaveToPlaylist(album, artist, ratingKeys, pitch) {
    try {
        const response = await apiCall('/playlist', {
            method: 'POST',
            body: JSON.stringify({
                name: `Recommended: ${album} - ${artist}`,
                rating_keys: ratingKeys,
                description: pitch,
            }),
        });
        if (response.success) {
            showSuccess(`Saved "${album}" to playlist`);
        } else {
            showError(response.error || 'Failed to save playlist');
        }
    } catch (e) {
        showError(e.message);
    }
}

// =============================================================================
// Setup Wizard
// =============================================================================

const SETUP_AI_HINTS = {
    gemini: 'Get a free key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a>',
    anthropic: 'Get a key at <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com</a>',
    openai: 'Get a key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com</a>',
    ollama: 'Make sure Ollama is running on your network',
    custom: 'Any OpenAI-compatible API endpoint',
};

function enterSetupWizard(status) {
    state.setup.active = true;
    state.setup.status = status;
    document.getElementById('app-loading')?.remove();
    const wizard = document.getElementById('setup-wizard');
    const homeContent = document.querySelector('#home-view .home-content');
    wizard.classList.remove('hidden');
    if (homeContent) homeContent.classList.add('hidden');
    renderSetupState(status);
    setupWizardEventListeners();
}

function exitSetupWizard() {
    state.setup.active = false;
    if (state.setup.syncPollInterval) {
        clearInterval(state.setup.syncPollInterval);
        state.setup.syncPollInterval = null;
    }
    const wizard = document.getElementById('setup-wizard');
    const homeContent = document.querySelector('#home-view .home-content');
    wizard.classList.add('hidden');
    if (homeContent) {
        homeContent.classList.remove('hidden', 'home-content--loading');
    }
    document.querySelector('.app-footer')?.classList.remove('app-footer--loading');

    // Run normal init
    loadSettings().then(() => {
        const isConnected = state.config?.media_server === 'jellyfin'
            ? !!(state.config?.jellyfin_url && state.config?.jellyfin_token_set)
            : state.config?.plex_connected;
        if (isConnected) checkLibraryStatus();
    }).catch(() => {});
    renderHistoryFeed();
}

function renderSetupState(status) {
    // Data dir warning
    const dataWarning = document.getElementById('setup-data-warning');
    if (!status.data_dir_writable) {
        dataWarning.classList.remove('hidden');
        document.getElementById('setup-data-fix').textContent =
            `Run: sudo chown ${status.process_uid}:${status.process_gid} ${status.data_dir}`;
    } else {
        dataWarning.classList.add('hidden');
    }

    // Step 1: Media Server
    const isServerConnected = status.media_server === 'jellyfin'
        ? status.jellyfin_connected
        : status.plex_connected;
    if (isServerConnected) {
        const serverLabel = status.media_server === 'jellyfin' ? 'Jellyfin' : 'Plex';
        setStepDone('plex', `Connected to ${serverLabel} (${status.music_libraries.length} music ${status.music_libraries.length === 1 ? 'library' : 'libraries'})`);
    } else {
        setStepForm('plex');
        // Pre-select server type if known from env
        const radioButtons = document.querySelectorAll('input[name="setup-media-server"]');
        radioButtons.forEach(r => {
            r.checked = r.value === (status.media_server || 'plex');
        });
        _updateSetupServerFields(status.media_server || 'plex');
    }

    // Step 2: AI
    if (status.llm_configured) {
        const providerLabel = {
            gemini: 'Gemini', anthropic: 'Claude', openai: 'OpenAI',
            ollama: 'Ollama', custom: 'Custom',
        }[status.llm_provider] || status.llm_provider;
        setStepDone('ai', `Using ${providerLabel}`);
    } else {
        setStepForm('ai');
    }

    // Step 3: Sync
    if (status.library_synced && !status.is_syncing) {
        setStepDone('sync', `${status.track_count.toLocaleString()} tracks synced`);
    } else if (status.is_syncing) {
        showSyncProgress(status);
        startSetupSyncPolling();
    } else if (isServerConnected && status.llm_configured) {
        // Auto-trigger sync
        triggerSetupSync();
    } else {
        setStepForm('sync');
        document.getElementById('setup-sync-waiting').classList.remove('hidden');
        document.getElementById('setup-sync-progress-wrap').classList.add('hidden');
    }

    // Step 4: Get Started
    const allDone = isServerConnected && status.llm_configured &&
        status.library_synced && !status.is_syncing;
    const getStartedBtn = document.getElementById('setup-get-started-btn');
    getStartedBtn.disabled = !allDone;
    if (allDone) {
        document.getElementById('setup-step-ready').classList.add('setup-step--done');
        const num = document.querySelector('#setup-step-ready .setup-step-number');
        if (num) num.textContent = '\u2713';
    }
}

function setStepDone(stepName, text) {
    const step = document.getElementById(`setup-step-${stepName}`);
    const form = document.getElementById(`setup-${stepName}-form`);
    const done = document.getElementById(`setup-${stepName}-done`);
    const doneText = document.getElementById(`setup-${stepName}-done-text`);
    step.classList.add('setup-step--done');
    step.classList.remove('setup-step--error');
    if (form) form.classList.add('hidden');
    if (done) done.classList.remove('hidden');
    if (doneText) doneText.textContent = text;
    const num = step.querySelector('.setup-step-number');
    if (num) num.textContent = '\u2713';
}

function setStepForm(stepName) {
    const step = document.getElementById(`setup-step-${stepName}`);
    const form = document.getElementById(`setup-${stepName}-form`);
    const done = document.getElementById(`setup-${stepName}-done`);
    step.classList.remove('setup-step--done', 'setup-step--error');
    if (form) form.classList.remove('hidden');
    if (done) done.classList.add('hidden');
}

function setStepError(stepName, msg) {
    const step = document.getElementById(`setup-step-${stepName}`);
    step.classList.add('setup-step--error');
    step.classList.remove('setup-step--done');
    const errorEl = document.getElementById(`setup-${stepName}-error`);
    if (errorEl) {
        errorEl.textContent = msg;
        errorEl.classList.remove('hidden');
    }
}

function clearStepError(stepName) {
    const step = document.getElementById(`setup-step-${stepName}`);
    step.classList.remove('setup-step--error');
    const errorEl = document.getElementById(`setup-${stepName}-error`);
    if (errorEl) errorEl.classList.add('hidden');
}

function showSyncProgress(status) {
    setStepForm('sync');
    document.getElementById('setup-sync-waiting').classList.add('hidden');
    document.getElementById('setup-sync-progress-wrap').classList.remove('hidden');

    if (status.sync_progress) {
        const pct = status.sync_progress.total > 0
            ? Math.round((status.sync_progress.current / status.sync_progress.total) * 100) : 0;
        const fill = document.getElementById('setup-sync-progress-fill');
        fill.style.width = `${pct}%`;
        fill.parentElement.setAttribute('aria-valuenow', pct);
        const phaseLabel = status.sync_progress.phase === 'fetching_albums'
            ? 'Fetching albums' : status.sync_progress.phase === 'fetching'
            ? 'Fetching tracks' : 'Processing';
        document.getElementById('setup-sync-progress-text').textContent =
            `${phaseLabel}: ${status.sync_progress.current.toLocaleString()} / ${status.sync_progress.total.toLocaleString()}`;
        document.getElementById('setup-sync-message').textContent = 'Syncing your library...';
    }
}

async function triggerSetupSync() {
    try {
        await apiCall('/library/sync', { method: 'POST' });
    } catch (e) {
        // May already be syncing (409) — that's fine
        if (!e.message.includes('already in progress')) {
            setStepError('sync', e.message);
            return;
        }
    }
    startSetupSyncPolling();
}

function startSetupSyncPolling() {
    if (state.setup.syncPollInterval) return;
    // Show progress immediately
    document.getElementById('setup-sync-waiting').classList.add('hidden');
    document.getElementById('setup-sync-progress-wrap').classList.remove('hidden');

    state.setup.syncPollInterval = setInterval(async () => {
        try {
            const libStatus = await apiCall('/library/status');
            if (libStatus.is_syncing) {
                showSyncProgress({
                    sync_progress: libStatus.sync_progress,
                    is_syncing: true,
                });
            } else if (libStatus.track_count > 0) {
                // Sync complete
                clearInterval(state.setup.syncPollInterval);
                state.setup.syncPollInterval = null;
                setStepDone('sync', `${libStatus.track_count.toLocaleString()} tracks synced`);
                // Update status and re-render step 4
                state.setup.status.library_synced = true;
                state.setup.status.track_count = libStatus.track_count;
                state.setup.status.is_syncing = false;
                renderSetupState(state.setup.status);
            } else if (libStatus.error) {
                clearInterval(state.setup.syncPollInterval);
                state.setup.syncPollInterval = null;
                setStepError('sync', libStatus.error);
            }
        } catch (e) {
            // Network error — keep polling
        }
    }, 2000);
}

let _setupListenersAttached = false;

function setupWizardEventListeners() {
    if (_setupListenersAttached) return;
    _setupListenersAttached = true;

    // Media server radio buttons — show/hide fields
    document.querySelectorAll('input[name="setup-media-server"]').forEach(radio => {
        radio.addEventListener('change', () => {
            _updateSetupServerFields(radio.value);
        });
    });

    // Media server connect button — routes to Plex or Jellyfin based on radio selection
    document.getElementById('setup-plex-btn').addEventListener('click', async () => {
        const selectedServer = document.querySelector('input[name="setup-media-server"]:checked')?.value || 'plex';
        const btn = document.getElementById('setup-plex-btn');

        clearStepError('plex');
        btn.disabled = true;
        btn.textContent = 'Connecting...';

        try {
            let result;
            if (selectedServer === 'jellyfin') {
                const url = document.getElementById('setup-jellyfin-url')?.value.trim() || '';
                const token = document.getElementById('setup-jellyfin-token')?.value.trim() || '';
                const library = document.getElementById('setup-jellyfin-library')?.value.trim() || 'Music';
                if (!url || !token) {
                    setStepError('plex', 'URL and API key are required');
                    return;
                }
                result = await validateJellyfin(url, token, library);
                if (result.success) {
                    state.setup.status.jellyfin_connected = true;
                    state.setup.status.media_server = 'jellyfin';
                    state.setup.status.music_libraries = result.music_libraries || [];
                    setStepDone('plex', result.server_name
                        ? `Connected to ${result.server_name}` : 'Connected to Jellyfin');
                } else {
                    setStepError('plex', result.error || 'Connection failed');
                }
            } else {
                const url = document.getElementById('setup-plex-url').value.trim();
                const token = document.getElementById('setup-plex-token').value.trim();
                const library = document.getElementById('setup-plex-library').value.trim() || 'Music';
                if (!url || !token) {
                    setStepError('plex', 'URL and token are required');
                    return;
                }
                result = await validatePlex(url, token, library);
                if (result.success) {
                    state.setup.status.plex_connected = true;
                    state.setup.status.media_server = 'plex';
                    state.setup.status.music_libraries = result.music_libraries || [];
                    setStepDone('plex', result.server_name
                        ? `Connected to ${result.server_name}` : 'Connected to Plex');
                } else {
                    setStepError('plex', result.error || 'Connection failed');
                }
            }

            if (result.success) {
                // Auto-trigger sync if AI is also done
                if (state.setup.status.llm_configured && !state.setup.status.library_synced) {
                    state.setup.status.is_syncing = true;
                    triggerSetupSync();
                }
                renderSetupState(state.setup.status);
            }
        } catch (e) {
            setStepError('plex', e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Connect';
        }
    });

    // AI provider dropdown change
    document.getElementById('setup-ai-provider').addEventListener('change', () => {
        const provider = document.getElementById('setup-ai-provider').value;
        const keyGroup = document.getElementById('setup-ai-key-group');
        const ollamaGroup = document.getElementById('setup-ai-ollama-group');
        const customGroup = document.getElementById('setup-ai-custom-group');
        const hintEl = document.getElementById('setup-ai-hint');

        keyGroup.classList.toggle('hidden', provider === 'ollama' || provider === 'custom');
        ollamaGroup.classList.toggle('hidden', provider !== 'ollama');
        customGroup.classList.toggle('hidden', provider !== 'custom');
        if (hintEl) hintEl.innerHTML = SETUP_AI_HINTS[provider] || '';
    });

    // AI validation
    document.getElementById('setup-ai-btn').addEventListener('click', async () => {
        const provider = document.getElementById('setup-ai-provider').value;
        const apiKey = document.getElementById('setup-ai-key')?.value.trim() || '';
        const ollamaUrl = document.getElementById('setup-ai-ollama-url')?.value.trim() || '';
        const customUrl = document.getElementById('setup-ai-custom-url')?.value.trim() || '';

        // Basic client-side validation
        if (['gemini', 'anthropic', 'openai'].includes(provider) && !apiKey) {
            setStepError('ai', 'API key is required');
            return;
        }
        if (provider === 'custom' && !customUrl) {
            setStepError('ai', 'API URL is required');
            return;
        }

        clearStepError('ai');
        const btn = document.getElementById('setup-ai-btn');
        btn.disabled = true;
        btn.textContent = 'Validating...';

        try {
            const result = await validateAI(provider, apiKey, ollamaUrl, customUrl);
            if (result.success) {
                state.setup.status.llm_configured = true;
                state.setup.status.llm_provider = provider;
                setStepDone('ai', `Using ${result.provider_name || provider}`);
                // Auto-trigger sync if media server is also done
                const serverConnected = state.setup.status.media_server === 'jellyfin'
                    ? state.setup.status.jellyfin_connected
                    : state.setup.status.plex_connected;
                if (serverConnected && !state.setup.status.library_synced) {
                    state.setup.status.is_syncing = true;
                    triggerSetupSync();
                }
                renderSetupState(state.setup.status);
            } else {
                setStepError('ai', result.error || 'Validation failed');
            }
        } catch (e) {
            setStepError('ai', e.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Validate';
        }
    });

    // Get Started
    document.getElementById('setup-get-started-btn').addEventListener('click', async () => {
        await completeSetup();
        exitSetupWizard();
    });

    // Skip Setup
    document.getElementById('setup-skip-btn').addEventListener('click', () => {
        exitSetupWizard();
    });
}


// =============================================================================
// Initialization
// =============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    // macOS + Safari: by default, Tab only focuses form inputs and elements
    // with explicit tabindex — buttons, links, and other native controls are
    // skipped. Observe the DOM and add tabindex="0" to any button or link
    // that lacks one, making them keyboard-navigable regardless of the
    // system "Keyboard navigation" preference.
    const ensureTabIndex = (root) => {
        root.querySelectorAll('button:not([tabindex]), a[href]:not([tabindex])').forEach(el => {
            el.setAttribute('tabindex', '0');
        });
    };
    ensureTabIndex(document);
    let tabIndexPending = false;
    new MutationObserver(() => {
        if (!tabIndexPending) {
            tabIndexPending = true;
            requestAnimationFrame(() => {
                ensureTabIndex(document.body);
                tabIndexPending = false;
            });
        }
    }).observe(document.body, { childList: true, subtree: true });

    setupEventListeners();
    setupRecEventListeners();
    setupHistoryEventListeners();
    state.view = viewFromHash();
    state.mode = modeFromHash();
    if (!location.hash) {
        history.replaceState(null, '', '#home');
    }
    updateView();
    updateMode();
    updateStep();
    renderPromptPills('playlist-prompt-pills', 'playlist-prompt-shuffle', PLAYLIST_PROMPT_GROUPS);

    // Load initial config
    try {
        await loadSettings();

        // Check setup wizard status (only on home view with no deep link)
        const initHash = location.hash.slice(1);
        if (state.view === 'home' && !initHash.startsWith('result/')) {
            try {
                const setupStatus = await fetchSetupStatus();
                if (!setupStatus.setup_complete) {
                    enterSetupWizard(setupStatus);
                    return; // Wizard handles its own lifecycle
                }
            } catch (e) {
                // Setup endpoint unavailable — skip wizard, continue normally
                console.warn('Setup status check failed:', e);
            }
        }

        // Reveal home content + footer now that setup check is done
        document.querySelector('#home-view .home-content')?.classList.remove('home-content--loading');
        document.querySelector('.app-footer')?.classList.remove('app-footer--loading');

        // Check library cache status after config is loaded
        if (state.config?.plex_connected) {
            await checkLibraryStatus();
        }
    } catch (error) {
        // Settings will show as not configured
        console.error('Initialization error:', error);
    } finally {
        document.getElementById('app-loading')?.remove();
        // Don't reveal home content if setup wizard took over
        if (!state.setup.active) {
            document.querySelector('#home-view .home-content')?.classList.remove('home-content--loading');
            document.querySelector('.app-footer')?.classList.remove('app-footer--loading');
        }
    }

    // Initialize views AFTER config is loaded
    if (state.view === 'recommend') {
        initRecommendView();
    } else if (state.view === 'home') {
        renderHistoryFeed();
    }

    // Handle direct navigation to a saved result (e.g., bookmarked URL)
    const initHash = location.hash.slice(1);
    if (initHash.startsWith('result/')) {
        const resultId = initHash.split('/')[1];
        if (resultId) {
            loadSavedResult(resultId);
        }
    }

    // Restore save mode from localStorage AFTER config loads (US3 — T017)
    let initialMode = 'new';
    try {
        const savedMode = localStorage.getItem('mediasage-save-mode');
        if (savedMode === 'replace' || savedMode === 'append') {
            initialMode = savedMode;
        }
    } catch (e) { /* private browsing / storage disabled */ }
    setSaveMode(initialMode);
});

// Export for global access
window.artPlaceholderHtml = artPlaceholderHtml;
window.hideError = hideError;
window.hideSuccess = hideSuccess;
window.hideSuccessModal = hideSuccessModal;
window.dismissSuccessModal = dismissSuccessModal;
window.dismissClientPicker = dismissClientPicker;
window.dismissPlayChoice = dismissPlayChoice;
window.dismissPlaySuccess = dismissPlaySuccess;
window.dismissUpdateSuccess = dismissUpdateSuccess;
window.closeBottomSheet = closeBottomSheet;
