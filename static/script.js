(() => {
  const form         = document.getElementById('download-form');
  const urlInput     = document.getElementById('url-input');
  const outputInput  = document.getElementById('output-input');
  const downloadBtn  = document.getElementById('download-btn');
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

  const historySection = document.getElementById('history-section');
  const historyList    = document.getElementById('history-list');

  let activeSource   = null;
  let totalTracks    = 0;
  let completedTracks = 0;
  let failedTracks   = 0;

  function setProgress(pct, msg) {
    progressBar.style.width = `${pct}%`;
    if (msg !== null) statusText.textContent = msg || '';
  }

  function setLoading(loading) {
    downloadBtn.disabled = loading;
    btnText.textContent  = loading ? 'Downloading…' : 'Download';
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
    const li = document.createElement('li');
    li.className = 'history-item';
    li.innerHTML = `
      <span class="badge ${status}">${status === 'done' ? '✓ Done' : '✗ Failed'}</span>
      <span class="path" title="${escapeHtml(outputPath || message)}">${escapeHtml(outputPath || message)}</span>
    `;
    historyList.prepend(li);
  }

  browseBtn.addEventListener('click', async () => {
    browseBtn.textContent = '...';
    browseBtn.disabled = true;
    try {
      const res = await fetch('/pick-folder');
      if (res.status === 204) return;
      const data = await res.json();
      if (data.path) outputInput.value = data.path;
    } finally {
      browseBtn.textContent = 'Browse';
      browseBtn.disabled = false;
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const url    = urlInput.value.trim();
    const output = outputInput.value.trim() || '~/Music';

    if (!url) return;

    if (activeSource) {
      activeSource.close();
      activeSource = null;
    }

    // Reset state
    totalTracks = 0;
    completedTracks = 0;
    failedTracks = 0;
    trackQueue.innerHTML = '';
    trackQueue.classList.add('hidden');
    trackCounter.textContent = '';
    collectionInfo.classList.add('hidden');

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
    } catch (err) {
      setProgress(0, `Error: ${err.message}`);
      setLoading(false);
      addHistoryItem('error', err.message, null);
      return;
    }

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
        collectionBadge.textContent = d.collection_type.charAt(0).toUpperCase() + d.collection_type.slice(1);
        collectionBadge.className = `collection-badge ${d.collection_type}`;
        collectionNameText.textContent = d.collection_name;
        collectionFolder.textContent = d.output || '';
        collectionInfo.classList.remove('hidden');
      } else {
        collectionInfo.classList.add('hidden');
      }
    });

    sse.addEventListener('track_start', (e) => {
      const d = JSON.parse(e.data);
      setProgress(d.progress ?? 0, null);
      setTrackState(d.index, 'active');
    });

    sse.addEventListener('track_done', (e) => {
      const d = JSON.parse(e.data);
      if (d.success) completedTracks++;
      else failedTracks++;
      setProgress(d.progress ?? 0, null);
      setTrackState(d.index, d.success ? 'done' : 'failed');
      updateCounter();
    });

    sse.addEventListener('done', (e) => {
      const d = JSON.parse(e.data);
      const msg = failedTracks > 0
        ? `${completedTracks} downloaded, ${failedTracks} failed`
        : `Downloaded ${completedTracks} track${completedTracks !== 1 ? 's' : ''}`;
      setProgress(100, msg);
      setLoading(false);
      addHistoryItem('done', d.message, d.output);
      sse.close();
      activeSource = null;
    });

    sse.addEventListener('error', (e) => {
      let msg = 'An error occurred.';
      try { msg = JSON.parse(e.data).message || msg; } catch (_) {}
      setProgress(0, `Error: ${msg}`);
      setLoading(false);
      addHistoryItem('error', msg, null);
      sse.close();
      activeSource = null;
    });

    sse.onerror = () => {
      if (sse.readyState === EventSource.CLOSED) return;
      sse.close();
      activeSource = null;
      pollStatus(jobId);
    };
  });

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
