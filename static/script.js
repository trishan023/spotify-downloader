(() => {
  const form         = document.getElementById('download-form');
  const urlInput     = document.getElementById('url-input');
  const outputInput  = document.getElementById('output-input');
  const downloadBtn  = document.getElementById('download-btn');
  const cancelBtn    = document.getElementById('cancel-btn');
  const browseBtn    = document.getElementById('browse-btn');
  const btnText      = downloadBtn.querySelector('.btn-text');

  const progressSection    = document.getElementById('progress-section');
  const progressBar        = document.getElementById('progress-bar');
  const statusText         = document.getElementById('status-text');
  const trackCounter       = document.getElementById('track-counter');
  const trackQueue         = document.getElementById('track-queue');
  const collectionInfo     = document.getElementById('collection-info');
  const collectionBadge    = document.getElementById('collection-type-badge');
  const collectionNameText = document.getElementById('collection-name-text');
  const collectionFolder   = document.getElementById('collection-folder');
  const outcomeBanner      = document.getElementById('outcome-banner');

  const historySection = document.getElementById('history-section');
  const historyList    = document.getElementById('history-list');

  // ── Spotify connect UI elements ─────────────────────────────────────────────
  const disconnectedView  = document.getElementById('spotify-disconnected');
  const connectedView     = document.getElementById('spotify-connected');
  const connectBtn        = document.getElementById('connect-spotify-btn');
  const likedSongsBtn     = document.getElementById('liked-songs-btn');
  const disconnectBtn     = document.getElementById('disconnect-spotify-btn');
  const accountName       = document.getElementById('spotify-account-name');
  const authErrorBanner   = document.getElementById('auth-error-banner');
  const authErrorDismiss  = document.getElementById('auth-error-dismiss');

  let activeSource    = null;
  let currentJobId    = null;
  let totalTracks     = 0;
  let completedTracks = 0;
  let failedTracks    = 0;
  let lastDownloadedName = null;

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function setProgress(pct, msg) {
    progressBar.style.width = `${pct}%`;
    if (msg !== null) statusText.textContent = msg || '';
  }

  function setLoading(loading) {
    downloadBtn.disabled = loading;
    btnText.textContent  = loading ? 'Downloading…' : 'Download';
    if (loading) {
      cancelBtn.classList.remove('hidden');
      cancelBtn.disabled = false;
      cancelBtn.querySelector('.cancel-text').textContent = 'Cancel';
    } else {
      cancelBtn.classList.add('hidden');
    }
  }

  function showOutcome(type, icon, msg) {
    outcomeBanner.className = `outcome-banner ${type}`;
    outcomeBanner.innerHTML = `<span class="outcome-icon">${icon}</span><span class="outcome-msg">${escapeHtml(msg)}</span>`;
    outcomeBanner.classList.remove('hidden');
  }

  function updateCounter() {
    if (!totalTracks) { trackCounter.textContent = ''; return; }
    const done = completedTracks + failedTracks;
    trackCounter.textContent = `${done} / ${totalTracks}`;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderQueue(tracks) {
    trackQueue.innerHTML = '';
    trackQueue.classList.remove('hidden');
    tracks.forEach((t, i) => {
      const li = document.createElement('li');
      li.className = 'track-item';
      li.dataset.index = i;
      li.dataset.state = 'pending';
      li.style.setProperty('--i', i);
      li.innerHTML = `
        <span class="track-dot"></span>
        <span class="track-info">
          <span class="track-name">${escapeHtml(t.name)}</span>
          ${t.artist ? `<span class="track-artist">${escapeHtml(t.artist)}</span>` : ''}
          <span class="track-error"></span>
        </span>
      `;
      trackQueue.appendChild(li);
    });
  }

  function setTrackState(index, state) {
    const item = trackQueue.querySelector(`[data-index="${index}"]`);
    if (item) item.dataset.state = state;
  }

  function addHistoryItem(status, message, outputPath) {
    historySection.classList.remove('hidden');
    const labels = { done: '✓ Done', error: '✗ Failed', cancelled: '⊘ Cancelled' };
    const li = document.createElement('li');
    li.className = 'history-item';
    li.innerHTML = `
      <span class="badge ${status}">${labels[status] || status}</span>
      <span class="path" title="${escapeHtml(outputPath || message)}">${escapeHtml(outputPath || message)}</span>
    `;
    historyList.prepend(li);
  }

  function resetUiState() {
    if (activeSource) { activeSource.close(); activeSource = null; }
    currentJobId = null;
    totalTracks = 0;
    completedTracks = 0;
    failedTracks = 0;
    lastDownloadedName = null;
    trackQueue.innerHTML = '';
    trackQueue.classList.add('hidden');
    trackCounter.textContent = '';
    collectionInfo.classList.add('hidden');
    outcomeBanner.classList.add('hidden');
  }

  // ── SSE subscription (shared by form submit + liked songs) ──────────────────

  function subscribeToJob(jobId) {
    const sse = new EventSource(`/progress/${jobId}`);
    activeSource = sse;

    function handleProgress(e) {
      const d = JSON.parse(e.data);
      setProgress(d.progress ?? 0, d.message ?? '');
    }

    sse.addEventListener('progress',    handleProgress);
    sse.addEventListener('searching',   handleProgress);
    sse.addEventListener('downloading', handleProgress);

    sse.addEventListener('tracks', (e) => {
      const d = JSON.parse(e.data);
      totalTracks = d.tracks.length;
      completedTracks = 0;
      failedTracks = 0;
      setProgress(d.progress ?? 25, d.message ?? '');
      updateCounter();
      renderQueue(d.tracks);

      if (d.collection_type && d.collection_name) {
        collectionBadge.textContent    = d.collection_type.charAt(0).toUpperCase() + d.collection_type.slice(1);
        collectionBadge.className      = `collection-badge ${d.collection_type}`;
        collectionNameText.textContent = d.collection_name;
        collectionFolder.textContent   = d.output || '';
        collectionInfo.classList.remove('hidden');
      } else {
        collectionInfo.classList.add('hidden');
      }
    });

    sse.addEventListener('track_start', (e) => {
      const d = JSON.parse(e.data);
      setProgress(d.progress ?? 0, null);
      setTrackState(d.index, 'active');
      const item = trackQueue.querySelector(`[data-index="${d.index}"]`);
      if (item) item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });

    sse.addEventListener('track_done', (e) => {
      const d = JSON.parse(e.data);
      if (d.success) { completedTracks++; lastDownloadedName = d.name || null; }
      else failedTracks++;
      setProgress(d.progress ?? 0, null);
      setTrackState(d.index, d.success ? 'done' : 'failed');
      if (!d.success) {
        const item = trackQueue.querySelector(`[data-index="${d.index}"]`);
        if (item) {
          const reason = d.reason || 'Download failed';
          const errEl  = item.querySelector('.track-error');
          if (errEl) errEl.textContent = reason;
          item.title = reason;
        }
      }
      updateCounter();
    });

    sse.addEventListener('done', (e) => {
      const d = JSON.parse(e.data);
      const msg = failedTracks > 0
        ? `${completedTracks} downloaded, ${failedTracks} failed`
        : `Downloaded ${completedTracks} track${completedTracks !== 1 ? 's' : ''}`;
      setProgress(100, '');
      setLoading(false);
      likedSongsBtn.disabled    = false;
      likedSongsBtn.textContent = 'Download Liked Songs';
      showOutcome('done', '✓', msg);
      addHistoryItem('done', d.message, lastDownloadedName || d.output);
      sse.close();
      activeSource = null;
    });

    sse.addEventListener('error', (e) => {
      let msg = 'An error occurred.';
      try { msg = JSON.parse(e.data).message || msg; } catch (_) {}
      setProgress(0, '');
      setLoading(false);
      likedSongsBtn.disabled    = false;
      likedSongsBtn.textContent = 'Download Liked Songs';
      showOutcome('error', '✕', msg);
      addHistoryItem('error', msg, null);
      sse.close();
      activeSource = null;
    });

    sse.addEventListener('cancelled', (e) => {
      const d = JSON.parse(e.data);
      const msg = d.message || 'Download cancelled';
      setProgress(d.progress ?? 0, '');
      setLoading(false);
      likedSongsBtn.disabled    = false;
      likedSongsBtn.textContent = 'Download Liked Songs';
      showOutcome('cancelled', '⊘', msg);
      addHistoryItem('cancelled', msg, null);
      sse.close();
      activeSource = null;
    });

    sse.onerror = () => {
      if (sse.readyState === EventSource.CLOSED) return;
      sse.close();
      activeSource = null;
      pollStatus(jobId);
    };
  }

  // ── Spotify auth UI helpers ─────────────────────────────────────────────────

  function setSpotifyConnected(displayName) {
    accountName.textContent = displayName ? `Connected as ${displayName}` : 'Connected';
    disconnectedView.classList.add('hidden');
    connectedView.classList.remove('hidden');
  }

  function setSpotifyDisconnected() {
    disconnectedView.classList.remove('hidden');
    connectedView.classList.add('hidden');
    accountName.textContent = '';
  }

  // On page load: check auth status + handle ?auth=error redirect ──────────────
  (async () => {
    if (new URLSearchParams(window.location.search).get('auth') === 'error') {
      authErrorBanner.classList.remove('hidden');
      history.replaceState({}, '', '/');
    }
    try {
      const res  = await fetch('/auth/status');
      const data = await res.json();
      data.connected ? setSpotifyConnected(data.display_name) : setSpotifyDisconnected();
    } catch {
      setSpotifyDisconnected();
    }
  })();

  // ── Auth event handlers ─────────────────────────────────────────────────────

  authErrorDismiss.addEventListener('click', () => {
    authErrorBanner.classList.add('hidden');
  });

  connectBtn.addEventListener('click', () => {
    window.location.href = '/auth/login';
  });

  disconnectBtn.addEventListener('click', async () => {
    disconnectBtn.textContent = '...';
    disconnectBtn.disabled    = true;
    try {
      await fetch('/auth/logout', { method: 'POST' });
    } finally {
      setSpotifyDisconnected();
      disconnectBtn.textContent = 'Disconnect';
      disconnectBtn.disabled    = false;
    }
  });

  likedSongsBtn.addEventListener('click', async () => {
    const output = outputInput.value.trim() || '~/Music';

    resetUiState();
    likedSongsBtn.disabled    = true;
    likedSongsBtn.textContent = 'Fetching…';
    setLoading(true);
    progressSection.classList.remove('hidden');
    setProgress(0, 'Fetching liked songs from Spotify…');

    let jobId;
    try {
      const res = await fetch('/download/liked-songs', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ output }),
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Server error');
      jobId = data.job_id;
      currentJobId = jobId;
    } catch (err) {
      setProgress(0, `Error: ${err.message}`);
      setLoading(false);
      likedSongsBtn.disabled    = false;
      likedSongsBtn.textContent = 'Download Liked Songs';
      showOutcome('error', '✕', err.message);
      addHistoryItem('error', err.message, null);
      return;
    }

    subscribeToJob(jobId);
  });

  // ── Cancel handler ──────────────────────────────────────────────────────────

  cancelBtn.addEventListener('click', async () => {
    if (!currentJobId) return;
    cancelBtn.disabled = true;
    cancelBtn.querySelector('.cancel-text').textContent = 'Cancelling…';
    try {
      await fetch(`/cancel/${currentJobId}`, { method: 'POST' });
    } catch (_) {}
  });

  // ── Browse handler ──────────────────────────────────────────────────────────

  browseBtn.addEventListener('click', async () => {
    browseBtn.textContent = '...';
    browseBtn.disabled = true;
    try {
      const res = await fetch('/pick-folder', { method: 'POST' });
      if (res.status === 204) return;
      const data = await res.json();
      if (data.path) outputInput.value = data.path;
    } finally {
      browseBtn.textContent = 'Browse';
      browseBtn.disabled = false;
    }
  });

  // ── Form submit handler ─────────────────────────────────────────────────────

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const url    = urlInput.value.trim();
    const output = outputInput.value.trim() || '~/Music';

    if (!url) return;

    resetUiState();
    setLoading(true);
    progressSection.classList.remove('hidden');
    setProgress(0, 'Sending request…');

    let jobId;
    try {
      const res = await fetch('/download', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ url, output }),
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Server error');
      jobId = data.job_id;
      currentJobId = jobId;
    } catch (err) {
      setProgress(0, `Error: ${err.message}`);
      setLoading(false);
      addHistoryItem('error', err.message, null);
      return;
    }

    subscribeToJob(jobId);
  });

  // ── Polling fallback ────────────────────────────────────────────────────────

  async function pollStatus(jobId) {
    const interval = setInterval(async () => {
      try {
        const res  = await fetch(`/status/${jobId}`);
        const data = await res.json();
        setProgress(data.progress ?? 0, data.message ?? '');
        if (data.status === 'done' || data.status === 'error') {
          clearInterval(interval);
          setLoading(false);
          addHistoryItem(data.status === 'done' ? 'done' : 'error', data.message, data.output);
        }
      } catch {
        clearInterval(interval);
        setLoading(false);
      }
    }, 1500);
  }
})();
