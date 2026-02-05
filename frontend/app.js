/**
 * PlexSage - Frontend Application
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
    // Update nav buttons
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === state.view);
    });

    // Update views
    document.querySelectorAll('.view').forEach(view => {
        view.classList.toggle('active', view.id === `${state.view}-view`);
    });
}

function updateMode() {
    // Update mode tabs
    document.querySelectorAll('.mode-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.mode === state.mode);
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

        stepEl.classList.toggle('active', stepName === state.step);
        stepEl.classList.toggle('completed', stepIndex < currentIndex);
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
        btn.classList.toggle('active', parseInt(btn.dataset.count) === state.trackCount);
    });

    // Update max tracks to AI buttons
    document.querySelectorAll('.limit-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.limit) === state.maxTracksToAI);
    });

    // Update checkboxes
    document.getElementById('exclude-live').checked = state.excludeLive;

    // Update rating buttons
    document.querySelectorAll('.rating-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.rating) === state.minRating);
    });
}

function updateTrackLimitButtons() {
    const container = document.querySelector('.track-limit-selector');
    if (!container || !state.config) return;

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
        const label = limit === 0 ? `All (${maxAllowed.toLocaleString()})` : limit.toLocaleString();
        return `<button class="limit-btn ${isActive ? 'active' : ''}" data-limit="${limit}">${label}</button>`;
    }).join('');

    // Re-attach event listeners
    container.querySelectorAll('.limit-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const limit = parseInt(btn.dataset.limit);
            state.maxTracksToAI = limit === 0 ? maxAllowed : limit;
            updateFilters();
            updateFilterPreview();
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
            }),
        });

        if (!response.ok) {
            throw new Error('Failed to get filter preview');
        }

        const data = await response.json();

        // Update both displays
        let trackText;
        if (data.matching_tracks >= 0) {
            if (data.tracks_to_send < data.matching_tracks) {
                trackText = `${data.matching_tracks.toLocaleString()} tracks (sending ${data.tracks_to_send.toLocaleString()} to AI)`;
            } else {
                trackText = `${data.matching_tracks.toLocaleString()} tracks`;
            }
        } else {
            trackText = 'Unknown';
        }
        const costText = `Est. cost: $${data.estimated_cost.toFixed(4)}`;

        previewTracks.textContent = trackText;
        previewCost.textContent = costText;
    } catch (error) {
        console.error('Filter preview error:', error);
        previewTracks.textContent = '-- matching tracks';
        previewCost.textContent = 'Est. cost: --';
    }
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
    const costDisplay = document.getElementById('cost-display');
    costDisplay.textContent = `${state.tokenCount.toLocaleString()} tokens ($${state.estimatedCost.toFixed(4)})`;

    // Update playlist name input
    document.getElementById('playlist-name-input').value = state.playlistName;
}

function updateSettings() {
    if (!state.config) return;

    document.getElementById('plex-url').value = state.config.plex_url || '';
    document.getElementById('music-library').value = state.config.music_library || 'Music';
    document.getElementById('llm-provider').value = state.config.llm_provider || 'anthropic';

    // Update token/key placeholders to indicate if configured
    const plexTokenInput = document.getElementById('plex-token');
    plexTokenInput.placeholder = state.config.plex_token_set
        ? '••••••••••••••••  (configured)'
        : 'Your Plex token';

    const llmApiKeyInput = document.getElementById('llm-api-key');
    llmApiKeyInput.placeholder = state.config.llm_api_key_set
        ? '••••••••••••••••  (configured)'
        : 'Your API key';

    // Update status indicators
    const plexStatus = document.getElementById('plex-status');
    plexStatus.classList.toggle('connected', state.config.plex_connected);
    plexStatus.querySelector('.status-text').textContent =
        state.config.plex_connected ? 'Connected' : 'Not connected';

    const llmStatus = document.getElementById('llm-status');
    llmStatus.classList.toggle('connected', state.config.llm_configured);
    llmStatus.querySelector('.status-text').textContent =
        state.config.llm_configured ? 'Configured' : 'Not configured';
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
        } else if (state.config.llm_provider) {
            footerModel.textContent = state.config.llm_provider;
        } else {
            footerModel.textContent = 'not configured';
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
            }, 2500); // Change message every 2.5 seconds
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

    // Track count
    document.querySelectorAll('.count-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.trackCount = parseInt(btn.dataset.count);
            updateFilters();
            updateFilterPreview();
        });
    });

    // Max tracks to AI limit
    document.querySelectorAll('.limit-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            state.maxTracksToAI = parseInt(btn.dataset.limit);
            updateFilters();
            updateFilterPreview();
        });
    });

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

    const generationSteps = [
        'Fetching tracks from library...',
        'Applying filters...',
        'Sending tracks to AI...',
        'AI is curating your playlist...',
        'Processing selections...',
    ];
    setLoading(true, 'Generating playlist...', generationSteps);

    try {
        const response = await generatePlaylist(request);

        // Add generation costs to session totals
        state.sessionTokens += response.token_count || 0;
        state.sessionCost += response.estimated_cost || 0;

        state.playlist = response.tracks;
        state.tokenCount = state.sessionTokens;  // Total tokens for display
        state.estimatedCost = state.sessionCost;  // Total cost for display
        state.playlistName = generatePlaylistName();

        state.step = 'results';
        updateStep();
        updatePlaylist();
    } catch (error) {
        showError(error.message);
    } finally {
        setLoading(false);
    }
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
            if (response.tracks_skipped > 0) {
                showSuccess(`Playlist saved to Plex! (${response.tracks_added} tracks added, ${response.tracks_skipped} skipped)`);
            } else {
                showSuccess('Playlist saved to Plex!');
            }
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

    if (plexUrl) updates.plex_url = plexUrl;
    if (plexToken) updates.plex_token = plexToken;
    if (musicLibrary) updates.music_library = musicLibrary;
    if (llmProvider) updates.llm_provider = llmProvider;
    if (llmApiKey) updates.llm_api_key = llmApiKey;

    if (Object.keys(updates).length === 0) {
        showError('No settings to update');
        return;
    }

    setLoading(true, 'Saving settings...');

    try {
        state.config = await updateConfig(updates);
        updateSettings();
        updateFooter();
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
