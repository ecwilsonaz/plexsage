/**
 * MediaSage - Frontend Application
 */

// =============================================================================
// State Management
// =============================================================================

const state = {
    // Current view and mode
    view: 'create', // 'create' | 'settings'
    mode: 'prompt', // 'prompt' | 'seed'
    step: 'input',  // 'input' | 'dimensions' | 'filters' | 'results'

    // Prompt flow
    prompt: '',

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
};

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
        throw new Error(error.detail || error.error || 'Request failed');
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

async function fetchHealth() {
    return apiCall('/health');
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

async function generatePlaylist(request) {
    return apiCall('/generate', {
        method: 'POST',
        body: JSON.stringify(request),
    });
}

// Progress message queue for smooth display
const progressQueue = {
    messages: [],
    currentStep: null,
    isProcessing: false,
    minDisplayTime: 500,
    onDisplay: null,
    onComplete: null,
    completeData: null,
    aiCycleInterval: null,
    aiCycleIndex: 0,
    aiMessages: [
        'AI is understanding your request...',
        'AI is analyzing the vibe...',
        'AI is scanning your library...',
        'AI is browsing through artists...',
        'AI is exploring albums...',
        'AI is discovering hidden gems...',
        'AI is evaluating track moods...',
        'AI is considering tempo and energy...',
        'AI is finding thematic connections...',
        'AI is looking for complementary sounds...',
        'AI is balancing familiar and fresh picks...',
        'AI is thinking about playlist flow...',
        'AI is ensuring variety across artists...',
        'AI is checking for smooth transitions...',
        'AI is refining the selection...',
        'AI is curating the perfect mix...',
        'AI is adding finishing touches...',
        'AI is reviewing the final picks...',
        'AI is almost there...',
        'AI is wrapping up...',
    ],

    enqueue(step, message) {
        // If we get a new step while on AI, stop the cycle
        if (this.currentStep === 'ai_working' && step !== 'ai_working') {
            this.stopAiCycle();
        }

        this.messages.push({ step, message });
        if (!this.isProcessing) {
            this.processNext();
        }
    },

    // Mark as complete - will fire callback after queue drains
    markComplete(data, callback) {
        this.completeData = data;
        this.onComplete = callback;
        // If not processing, finish immediately
        if (!this.isProcessing && this.messages.length === 0) {
            this.finish();
        }
    },

    processNext() {
        if (this.messages.length === 0) {
            this.isProcessing = false;
            // If we have pending complete data, finish now
            if (this.completeData && this.onComplete) {
                this.finish();
            }
            return;
        }

        this.isProcessing = true;
        const { step, message } = this.messages.shift();
        this.currentStep = step;

        if (this.onDisplay) {
            this.onDisplay(message);
        }

        // Start AI message cycling if we're on the AI step
        if (step === 'ai_working') {
            this.startAiCycle();
        }

        // Wait minimum time before processing next
        setTimeout(() => {
            this.processNext();
        }, this.minDisplayTime);
    },

    finish() {
        console.log('[MediaSage] progressQueue.finish() called, hasCallback:', !!this.onComplete, 'hasData:', !!this.completeData);
        const callback = this.onComplete;
        const data = this.completeData;
        this.reset();
        if (callback && data) {
            console.log('[MediaSage] Calling onComplete callback with', data.tracks?.length || 0, 'tracks');
            callback(data);
        }
    },

    startAiCycle() {
        this.aiCycleIndex = 0;
        this.aiCycleInterval = setInterval(() => {
            // Stop cycling when we reach the last message
            if (this.aiCycleIndex >= this.aiMessages.length - 1) {
                this.stopAiCycle();
                return;
            }
            this.aiCycleIndex++;
            if (this.onDisplay && this.currentStep === 'ai_working') {
                this.onDisplay(this.aiMessages[this.aiCycleIndex]);
            }
        }, 4000);
    },

    stopAiCycle() {
        if (this.aiCycleInterval) {
            clearInterval(this.aiCycleInterval);
            this.aiCycleInterval = null;
        }
    },

    reset() {
        this.messages = [];
        this.currentStep = null;
        this.isProcessing = false;
        this.completeData = null;
        this.onComplete = null;
        this.stopAiCycle();
    }
};

function generatePlaylistStream(request, onProgress, onComplete, onError) {
    // Reset and configure progress queue
    progressQueue.reset();
    progressQueue.onDisplay = (message) => {
        const substepEl = document.getElementById('loading-substep');
        if (substepEl) {
            substepEl.textContent = message;
        }
    };

    // Timeout handling - 10 minutes for local providers, 5 minutes for cloud
    let timeoutId = null;
    let abortController = new AbortController();
    const isLocalProvider = state.config?.is_local_provider ?? false;
    const TIMEOUT_MS = isLocalProvider ? 600000 : 300000;  // 10 min vs 5 min

    function resetTimeout() {
        if (timeoutId) clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            abortController.abort();
            progressQueue.reset();
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
        signal: abortController.signal,
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
                if (done) {
                    clearTimeoutHandler();
                    return;
                }

                // Reset timeout on each chunk received
                resetTimeout();

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                let currentEvent = null;
                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        currentEvent = line.slice(7);
                    } else if (line.startsWith('data: ') && currentEvent) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            if (currentEvent === 'progress') {
                                progressQueue.enqueue(data.step, data.message);
                            } else if (currentEvent === 'complete') {
                                console.log('[MediaSage] Complete event received:', JSON.stringify(data).substring(0, 200));
                                clearTimeoutHandler();
                                // Wait for queue to drain before completing
                                progressQueue.markComplete(data, onComplete);
                            } else if (currentEvent === 'error') {
                                clearTimeoutHandler();
                                progressQueue.reset();
                                onError(new Error(data.message));
                            }
                        } catch {
                            // Ignore parse errors
                        }
                        currentEvent = null;
                    }
                }

                processStream();
            }).catch(err => {
                clearTimeoutHandler();
                progressQueue.reset();
                if (err.name !== 'AbortError') {
                    onError(err);
                }
            });
        }

        processStream();
    }).catch(err => {
        clearTimeoutHandler();
        progressQueue.reset();
        if (err.name !== 'AbortError') {
            onError(err);
        }
    });
}

async function savePlaylist(name, ratingKeys) {
    return apiCall('/playlist', {
        method: 'POST',
        body: JSON.stringify({ name, rating_keys: ratingKeys }),
    });
}

async function fetchLibraryStats() {
    return apiCall('/library/stats');
}

// =============================================================================
// UI Updates
// =============================================================================

function updateView() {
    // Update nav buttons (class and ARIA state)
    document.querySelectorAll('.nav-btn').forEach(btn => {
        const isActive = btn.dataset.view === state.view;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    // Update views
    document.querySelectorAll('.view').forEach(view => {
        view.classList.toggle('active', view.id === `${state.view}-view`);
    });
}

function updateMode() {
    // Update mode tabs (class and ARIA state)
    document.querySelectorAll('.mode-tab').forEach(tab => {
        const isActive = tab.dataset.mode === state.mode;
        tab.classList.toggle('active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    // Update step panels visibility
    const inputPrompt = document.getElementById('step-input-prompt');
    const inputSeed = document.getElementById('step-input-seed');

    if (state.step === 'input') {
        inputPrompt.classList.toggle('active', state.mode === 'prompt');
        inputSeed.classList.toggle('active', state.mode === 'seed');
    }

    // Update step progress - hide dimensions step for prompt mode and renumber
    const dimensionsStep = document.querySelector('.step[data-step="dimensions"]');
    const dimensionsConnector = dimensionsStep?.previousElementSibling;
    if (state.mode === 'prompt') {
        dimensionsStep?.classList.add('hidden');
        dimensionsConnector?.classList.add('hidden');
    } else {
        dimensionsStep?.classList.remove('hidden');
        dimensionsConnector?.classList.remove('hidden');
    }

    // Renumber visible steps
    let stepNumber = 1;
    document.querySelectorAll('.step').forEach(step => {
        if (!step.classList.contains('hidden')) {
            step.querySelector('.step-number').textContent = stepNumber++;
        }
    });
}

function updateStep() {
    // Update step progress indicators
    const steps = ['input', 'dimensions', 'filters', 'results'];
    const currentIndex = steps.indexOf(state.step);

    document.querySelectorAll('.step').forEach((stepEl, index) => {
        const stepName = stepEl.dataset.step;
        const stepIndex = steps.indexOf(stepName);
        const isActive = stepName === state.step;

        stepEl.classList.toggle('active', isActive);
        stepEl.classList.toggle('completed', stepIndex < currentIndex);

        // Update ARIA state for screen readers
        if (isActive) {
            stepEl.setAttribute('aria-current', 'step');
        } else {
            stepEl.removeAttribute('aria-current');
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
    } else if (state.step === 'dimensions') {
        document.getElementById('step-dimensions').classList.add('active');
    } else if (state.step === 'filters') {
        document.getElementById('step-filters').classList.add('active');
    } else if (state.step === 'results') {
        document.getElementById('step-results').classList.add('active');
    }
}

function updateFilters() {
    // Update genre chips
    const genreContainer = document.getElementById('genre-chips');
    genreContainer.innerHTML = state.availableGenres.map(genre => `
        <button class="chip ${state.selectedGenres.includes(genre.name) ? 'selected' : ''}"
                data-genre="${escapeHtml(genre.name)}">
            ${escapeHtml(genre.name)}
            ${genre.count != null ? `<span class="chip-count">${genre.count}</span>` : ''}
        </button>
    `).join('');

    // Update decade chips
    const decadeContainer = document.getElementById('decade-chips');
    decadeContainer.innerHTML = state.availableDecades.map(decade => `
        <button class="chip ${state.selectedDecades.includes(decade.name) ? 'selected' : ''}"
                data-decade="${escapeHtml(decade.name)}">
            ${escapeHtml(decade.name)}
            ${decade.count != null ? `<span class="chip-count">${decade.count}</span>` : ''}
        </button>
    `).join('');

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

    // Update rating buttons
    document.querySelectorAll('.rating-btn').forEach(btn => {
        const isActive = parseInt(btn.dataset.rating) === state.minRating;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
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

async function updateFilterPreview() {
    const previewTracks = document.getElementById('preview-tracks');
    const previewCost = document.getElementById('preview-cost');

    // Show loading state immediately
    previewTracks.innerHTML = '<span class="preview-spinner"></span> Counting...';
    previewCost.textContent = '';

    try {
        const response = await fetch('/api/filter/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                genres: state.selectedGenres,
                decades: state.selectedDecades,
                track_count: state.trackCount,
                max_tracks_to_ai: state.maxTracksToAI,
                min_rating: state.minRating,
                exclude_live: state.excludeLive,
            }),
        });

        if (!response.ok) {
            throw new Error('Failed to get filter preview');
        }

        const data = await response.json();

        // Cache the matching_tracks for local recalculation
        state.lastFilterPreview = {
            matching_tracks: data.matching_tracks,
        };

        // Update display
        updateFilterPreviewDisplay(data.matching_tracks, data.tracks_to_send, data.estimated_cost);
    } catch (error) {
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

    // Cost formula (same as backend)
    const input_tokens = 500 + (tracks_to_send * 50);
    const output_tokens = state.trackCount * 30;

    // Use cost rates from config
    const input_cost = (input_tokens / 1_000_000) * state.config.cost_per_million_input;
    const output_cost = (output_tokens / 1_000_000) * state.config.cost_per_million_output;
    const estimated_cost = input_cost + output_cost;

    updateFilterPreviewDisplay(matching_tracks, tracks_to_send, estimated_cost);
}

function updatePlaylist() {
    const container = document.getElementById('playlist-tracks');
    container.innerHTML = state.playlist.map((track, index) => `
        <div class="playlist-track" data-rating-key="${escapeHtml(track.rating_key)}">
            <span class="track-number">${index + 1}</span>
            <img class="track-art" src="${escapeHtml(track.art_url || '/static/placeholder.png')}"
                 alt="${escapeHtml(track.album)}" onerror="this.style.display='none'">
            <div class="track-info">
                <div class="track-title">${escapeHtml(track.title)}</div>
                <div class="track-artist">${escapeHtml(track.artist)} - ${escapeHtml(track.album)}</div>
            </div>
            <button class="track-remove" data-rating-key="${escapeHtml(track.rating_key)}">&times;</button>
        </div>
    `).join('');

    // Update cost display (actual costs from API responses)
    // For local providers, show tokens only (no dollar cost)
    const costDisplay = document.getElementById('cost-display');
    const isLocalProvider = state.config?.is_local_provider ?? false;
    if (isLocalProvider) {
        costDisplay.textContent = `${state.tokenCount.toLocaleString()} tokens`;
    } else {
        costDisplay.textContent = `${state.tokenCount.toLocaleString()} tokens ($${state.estimatedCost.toFixed(4)})`;
    }

    // Update playlist name input
    document.getElementById('playlist-name-input').value = state.playlistName;
}

function updateSettings() {
    if (!state.config) return;

    document.getElementById('plex-url').value = state.config.plex_url || '';
    document.getElementById('music-library').value = state.config.music_library || 'Music';
    document.getElementById('llm-provider').value = state.config.llm_provider || 'gemini';

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
    plexStatus.classList.toggle('connected', state.config.plex_connected);
    plexStatus.querySelector('.status-text').textContent =
        state.config.plex_connected ? 'Connected' : 'Not connected';

    const llmStatus = document.getElementById('llm-status');
    llmStatus.classList.toggle('connected', state.config.llm_configured);
    llmStatus.querySelector('.status-text').textContent =
        state.config.llm_configured ? 'Configured' : 'Not configured';

    // Show provider-specific settings
    showProviderSettings(state.config.llm_provider);
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
    const plexConnected = state.config?.plex_connected ?? false;
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
    const needsPlex = !plexConnected;
    const needsLLM = !llmConfigured;
    const needsConfig = needsPlex || needsLLM;

    // Update button/input states
    if (analyzeBtn) analyzeBtn.disabled = needsConfig;
    if (continueBtn) continueBtn.disabled = needsLLM; // Only needs LLM at this point
    if (searchBtn) searchBtn.disabled = needsPlex;
    if (searchInput) searchInput.disabled = needsPlex;
    if (promptTextarea) promptTextarea.disabled = needsPlex;

    // Build hint message based on what's missing
    let hintMessage = '';
    if (needsPlex && needsLLM) {
        hintMessage = '<a href="#" data-view="settings">Configure Plex and an LLM provider</a> to continue';
    } else if (needsPlex) {
        hintMessage = '<a href="#" data-view="settings">Connect to Plex</a> to continue';
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
    const footerModel = document.getElementById('footer-model');
    if (footerModel && state.config) {
        if (state.config.llm_configured) {
            const analysis = state.config.model_analysis;
            const generation = state.config.model_generation;

            if (analysis && generation && analysis !== generation) {
                // Two different models - show both
                footerModel.textContent = `${analysis} / ${generation}`;
            } else if (generation) {
                // Same model or only generation set
                footerModel.textContent = generation;
            } else if (analysis) {
                footerModel.textContent = analysis;
            } else {
                footerModel.textContent = state.config.llm_provider;
            }
        } else {
            // Not configured - show "not configured" regardless of provider selection
            footerModel.textContent = 'llm not configured';
        }
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
    document.body.classList.toggle('no-scroll', loading);
    messageEl.textContent = message;

    if (substepEl) {
        if (loading && substeps && substeps.length > 0) {
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
    document.body.classList.add('no-scroll');
}

function hideSuccessModal() {
    document.getElementById('success-modal').classList.add('hidden');
    document.body.classList.remove('no-scroll');

    // Reset state for next playlist
    state.step = 'input';
    state.prompt = '';
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
    document.getElementById('prompt-input').value = '';
    updateStep();
}

// =============================================================================
// Event Handlers
// =============================================================================

function setupEventListeners() {
    // Navigation
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.view = btn.dataset.view;
            updateView();
            if (state.view === 'settings') {
                loadSettings();
            }
        });
    });

    // Settings links in hints (use event delegation for dynamically inserted links)
    document.body.addEventListener('click', e => {
        const link = e.target.closest('.llm-required-hint a[data-view]');
        if (link) {
            e.preventDefault();
            state.view = link.dataset.view;
            updateView();
            loadSettings();
        }
    });

    // Mode tabs
    document.querySelectorAll('.mode-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            state.mode = tab.dataset.mode;
            state.step = 'input';
            updateMode();
            updateStep();
        });
    });

    // Prompt analysis
    document.getElementById('analyze-prompt-btn').addEventListener('click', handleAnalyzePrompt);

    // Track search
    document.getElementById('search-tracks-btn').addEventListener('click', handleSearchTracks);
    document.getElementById('track-search-input').addEventListener('keypress', e => {
        if (e.key === 'Enter') handleSearchTracks();
    });

    // Continue to filters
    document.getElementById('continue-to-filters-btn').addEventListener('click', handleContinueToFilters);

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

    // Remove track
    document.getElementById('playlist-tracks').addEventListener('click', e => {
        const removeBtn = e.target.closest('.track-remove');
        if (!removeBtn) return;

        const ratingKey = removeBtn.dataset.ratingKey;
        state.playlist = state.playlist.filter(t => t.rating_key !== ratingKey);
        updatePlaylist();
    });

    // Save playlist
    document.getElementById('save-playlist-btn').addEventListener('click', handleSavePlaylist);

    // Save settings
    document.getElementById('save-settings-btn').addEventListener('click', handleSaveSettings);

    // Success modal - Start New Playlist
    document.getElementById('new-playlist-btn').addEventListener('click', hideSuccessModal);

    // Provider selection change
    document.getElementById('llm-provider').addEventListener('change', (e) => {
        showProviderSettings(e.target.value);
    });

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

    const analyzeSteps = [
        'Parsing your request...',
        'Identifying genres and eras...',
        'Matching to your library...',
    ];
    setLoading(true, 'Analyzing your prompt...', analyzeSteps);

    try {
        const response = await analyzePrompt(prompt);

        // Track analysis costs
        state.sessionTokens += response.token_count || 0;
        state.sessionCost += response.estimated_cost || 0;

        state.availableGenres = response.available_genres;
        state.availableDecades = response.available_decades;
        state.selectedGenres = response.suggested_genres;
        state.selectedDecades = response.suggested_decades;

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
        <div class="search-result-item" data-rating-key="${escapeHtml(track.rating_key)}">
            <img class="track-art" src="${escapeHtml(track.art_url || '')}"
                 alt="${escapeHtml(track.album)}" onerror="this.style.display='none'">
            <div class="track-info">
                <div class="track-title">${escapeHtml(track.title)}</div>
                <div class="track-artist">${escapeHtml(track.artist)} - ${escapeHtml(track.album)}</div>
            </div>
        </div>
    `).join('');

    // Add click handlers
    container.querySelectorAll('.search-result-item').forEach(item => {
        item.addEventListener('click', () => selectSeedTrack(item.dataset.ratingKey, tracks));
    });
}

async function selectSeedTrack(ratingKey, tracks) {
    // Check if services are configured before proceeding
    if (!state.config?.plex_connected) {
        showError('Connect to Plex in Settings first');
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

    const analyzeTrackSteps = [
        'Loading track metadata...',
        'Analyzing musical characteristics...',
        'Generating exploration dimensions...',
    ];
    setLoading(true, 'Analyzing track dimensions...', analyzeTrackSteps);

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
        setLoading(false);
    }
}

function renderSeedTrack() {
    const container = document.getElementById('selected-track');
    const track = state.seedTrack;

    container.innerHTML = `
        <img class="track-art" src="${escapeHtml(track.art_url || '')}"
             alt="${escapeHtml(track.album)}" onerror="this.style.display='none'">
        <div class="track-info">
            <div class="track-title">${escapeHtml(track.title)}</div>
            <div class="track-artist">${escapeHtml(track.artist)} - ${escapeHtml(track.album)}</div>
        </div>
    `;
}

function renderDimensions() {
    const container = document.getElementById('dimensions-list');

    container.innerHTML = state.dimensions.map(dim => `
        <div class="dimension-card ${state.selectedDimensions.includes(dim.id) ? 'selected' : ''}"
             data-dimension-id="${escapeHtml(dim.id)}">
            <div class="dimension-label">${escapeHtml(dim.label)}</div>
            <div class="dimension-description">${escapeHtml(dim.description)}</div>
        </div>
    `).join('');

    // Add click handlers
    container.querySelectorAll('.dimension-card').forEach(card => {
        card.addEventListener('click', () => {
            const dimId = card.dataset.dimensionId;
            if (state.selectedDimensions.includes(dimId)) {
                state.selectedDimensions = state.selectedDimensions.filter(d => d !== dimId);
            } else {
                state.selectedDimensions.push(dimId);
            }
            renderDimensions();
        });
    });
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
        state.selectedGenres = [];
        state.selectedDecades = [];

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
    const request = {
        genres: state.selectedGenres,
        decades: state.selectedDecades,
        track_count: state.trackCount,
        exclude_live: state.excludeLive,
        min_rating: state.minRating,
        max_tracks_to_ai: state.maxTracksToAI,
    };

    if (state.mode === 'prompt') {
        request.prompt = state.prompt;
    } else {
        request.seed_track = {
            rating_key: state.seedTrack.rating_key,
            selected_dimensions: state.selectedDimensions,
        };
        if (state.additionalNotes) {
            request.additional_notes = state.additionalNotes;
        }
    }

    setLoading(true, 'Generating playlist...');
    const substepEl = document.getElementById('loading-substep');

    generatePlaylistStream(
        request,
        // onProgress
        (data) => {
            if (substepEl && data.message) {
                substepEl.textContent = data.message;
            }
        },
        // onComplete
        (response) => {
            // Add generation costs to session totals
            state.sessionTokens += response.token_count || 0;
            state.sessionCost += response.estimated_cost || 0;

            state.playlist = response.tracks;
            state.tokenCount = state.sessionTokens;
            state.estimatedCost = state.sessionCost;
            state.playlistName = generatePlaylistName();

            state.step = 'results';
            updateStep();
            updatePlaylist();
            window.scrollTo(0, 0);
            setLoading(false);
        },
        // onError
        (error) => {
            showError(error.message);
            setLoading(false);
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
    const name = document.getElementById('playlist-name-input').value.trim();
    if (!name) {
        showError('Please enter a playlist name');
        return;
    }

    if (!state.playlist.length) {
        showError('Playlist is empty');
        return;
    }

    const saveSteps = [
        'Connecting to Plex server...',
        'Creating playlist...',
        'Adding tracks...',
    ];
    setLoading(true, 'Saving to Plex...', saveSteps);

    try {
        const ratingKeys = state.playlist.map(t => t.rating_key);
        const response = await savePlaylist(name, ratingKeys);

        if (response.success) {
            const trackCount = response.tracks_added || state.playlist.length;
            showSuccessModal(name, trackCount, response.playlist_url);
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

        // Set max tracks to AI based on model's context limit
        if (state.config.max_tracks_to_ai) {
            state.maxTracksToAI = Math.min(state.maxTracksToAI, state.config.max_tracks_to_ai);
            updateTrackLimitButtons();
        }

        updateSettings();
        updateFooter();
        updateConfigRequiredUI();

        // Show library stats if connected
        if (state.config.plex_connected) {
            const statsSection = document.getElementById('library-stats-section');
            statsSection.style.display = 'block';

            try {
                const stats = await fetchLibraryStats();
                document.getElementById('library-stats').innerHTML = `
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

    const plexUrl = document.getElementById('plex-url').value.trim();
    const plexToken = document.getElementById('plex-token').value.trim();
    const musicLibrary = document.getElementById('music-library').value.trim();
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

    if (plexUrl) updates.plex_url = plexUrl;
    if (plexToken) updates.plex_token = plexToken;
    if (musicLibrary) updates.music_library = musicLibrary;
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
        showSuccess('Settings saved!');

        // Clear password fields after save
        document.getElementById('plex-token').value = '';
        document.getElementById('llm-api-key').value = '';

        // Reload library stats
        if (state.config.plex_connected) {
            loadSettings();
        }
    } catch (error) {
        showError('Failed to save settings: ' + error.message);
    } finally {
        setLoading(false);
    }
}

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    updateView();
    updateMode();
    updateStep();

    // Load initial config
    loadSettings().catch(() => {
        // Settings will show as not configured
    });
});

// Export for global access
window.hideError = hideError;
window.hideSuccess = hideSuccess;
window.hideSuccessModal = hideSuccessModal;
