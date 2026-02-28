(() => {
  const form         = document.getElementById('download-form');
  const urlInput     = document.getElementById('url-input');
  const outputInput  = document.getElementById('output-input');
  const downloadBtn  = document.getElementById('download-btn');
  const browseBtn    = document.getElementById('browse-btn');
  const btnText      = downloadBtn.querySelector('.btn-text');

  const progressSection = document.getElementById('progress-section');
  const progressBar     = document.getElementById('progress-bar');
  const statusText      = document.getElementById('status-text');
  const logBox          = document.getElementById('log-box');

  const historySection = document.getElementById('history-section');
  const historyList    = document.getElementById('history-list');

  let activeSource = null; // current EventSource

  function setProgress(pct, msg) {
    progressBar.style.width = `${pct}%`;
    statusText.textContent  = msg || '';
  }

  function setLoading(loading) {
    downloadBtn.disabled = loading;
    btnText.textContent  = loading ? 'Downloading…' : 'Download';
  }

  function appendLog(msg, level = 'info') {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const line = document.createElement('div');
    line.className = `log-line ${level}`;
    line.textContent = `[${now}] ${msg}`;
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
  }

  function addHistoryItem(status, message, outputPath) {
    historySection.classList.remove('hidden');
    const li = document.createElement('li');
    li.className = 'history-item';
    li.innerHTML = `
      <span class="badge ${status}">${status === 'done' ? '✓ Done' : '✗ Failed'}</span>
      <span class="path" title="${outputPath || message}">${outputPath || message}</span>
    `;
    historyList.prepend(li);
  }

  browseBtn.addEventListener('click', async () => {
    browseBtn.textContent = '...';
    browseBtn.disabled = true;
    try {
      const res = await fetch('/pick-folder');
      if (res.status === 204) return; // user cancelled
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

    // Close any existing SSE connection
    if (activeSource) {
      activeSource.close();
      activeSource = null;
    }

    setLoading(true);
    logBox.innerHTML = '';
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
      if (!res.ok || data.error) {
        throw new Error(data.error || 'Server error');
      }
      jobId = data.job_id;
    } catch (err) {
      setProgress(0, `Error: ${err.message}`);
      setLoading(false);
      addHistoryItem('error', err.message, null);
      return;
    }

    // Open SSE stream
    const sse = new EventSource(`/progress/${jobId}`);
    activeSource = sse;

    function handleEvent(e) {
      const d = JSON.parse(e.data);
      setProgress(d.progress ?? 0, d.message ?? '');
    }

    sse.addEventListener('progress',    handleEvent);
    sse.addEventListener('searching',   handleEvent);
    sse.addEventListener('downloading', handleEvent);

    sse.addEventListener('log', (e) => {
      const d = JSON.parse(e.data);
      appendLog(d.msg, d.level || 'info');
    });

    sse.addEventListener('done', (e) => {
      const d = JSON.parse(e.data);
      setProgress(100, d.message || 'Done!');
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

    // Network-level SSE error (connection dropped)
    sse.onerror = () => {
      if (sse.readyState === EventSource.CLOSED) return; // already closed normally
      // Fall back to polling
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
