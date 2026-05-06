(function () {
  const OWNER = 'sspam1189-stack';
  const REPO = 'Model';
  const REF = 'main';
  const TOKEN_KEY = 'gh_pat';
  const POLL_INTERVAL_MS = 6000;
  const POLL_MAX_MS = 10 * 60 * 1000;

  const statusEl = document.getElementById('run-status');
  const buttons = Array.from(document.querySelectorAll('.run-btn'));
  const resetBtn = document.getElementById('run-reset-token');

  if (!statusEl || buttons.length === 0) return;

  injectStyles();

  function injectStyles() {
    const css = `
      .run-btn {
        padding: 7px 16px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: var(--card);
        color: var(--text);
        font-size: 0.78rem;
        font-weight: 600;
        font-family: inherit;
        cursor: pointer;
        transition: all 0.2s ease;
      }
      .run-btn:hover:not(:disabled) {
        border-color: var(--accent);
        background: var(--card-hover);
        transform: translateY(-1px);
      }
      .run-btn:disabled { opacity: 0.55; cursor: progress; }
      .run-btn.running {
        background: linear-gradient(135deg, var(--accent), #5b4cd4);
        border-color: transparent;
        color: #fff;
      }
    `;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
  }

  function getToken() {
    let token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      token = window.prompt(
        'Paste a GitHub fine-grained PAT with Actions: write on this repo. ' +
        'Stored only in this browser (localStorage).'
      );
      if (token) {
        token = token.trim();
        localStorage.setItem(TOKEN_KEY, token);
      }
    }
    return token;
  }

  function setStatus(text, color) {
    statusEl.textContent = text;
    statusEl.style.color = color || 'var(--muted)';
  }

  async function dispatchWorkflow(workflowFile, token) {
    const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${workflowFile}/dispatches`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: REF }),
    });
    if (res.status === 204) return;
    let detail = '';
    try { detail = (await res.json()).message || ''; } catch (_) {}
    throw new Error(`HTTP ${res.status}${detail ? ': ' + detail : ''}`);
  }

  async function fetchLatestRun(workflowFile, token, sinceIso) {
    const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${workflowFile}/runs?event=workflow_dispatch&per_page=5`;
    const res = await fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const runs = data.workflow_runs || [];
    const since = new Date(sinceIso).getTime();
    return runs.find(r => new Date(r.created_at).getTime() >= since - 5000) || null;
  }

  async function pollRun(workflowFile, label, token, dispatchedAtIso, btn) {
    const start = Date.now();
    let runUrl = null;
    while (Date.now() - start < POLL_MAX_MS) {
      await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
      let run;
      try {
        run = await fetchLatestRun(workflowFile, token, dispatchedAtIso);
      } catch (err) {
        setStatus(`${label}: poll error (${err.message})`, 'var(--yellow)');
        continue;
      }
      if (!run) {
        setStatus(`${label}: queued…`, 'var(--muted)');
        continue;
      }
      runUrl = run.html_url;
      if (run.status === 'completed') {
        const ok = run.conclusion === 'success';
        setStatus(
          `${label}: ${run.conclusion} — ${runUrl}`,
          ok ? 'var(--green)' : 'var(--red)'
        );
        btn.classList.remove('running');
        btn.disabled = false;
        return;
      }
      setStatus(`${label}: ${run.status}…`, 'var(--accent-light)');
    }
    setStatus(`${label}: still running after 10m, check ${runUrl || 'GitHub Actions'}`, 'var(--yellow)');
    btn.classList.remove('running');
    btn.disabled = false;
  }

  async function handleClick(btn) {
    const workflowFile = btn.dataset.workflow;
    const label = btn.dataset.label || workflowFile;
    const token = getToken();
    if (!token) { setStatus('No token — cancelled.', 'var(--yellow)'); return; }

    buttons.forEach(b => b.disabled = true);
    btn.classList.add('running');
    setStatus(`${label}: dispatching…`, 'var(--accent-light)');
    const dispatchedAt = new Date().toISOString();

    try {
      await dispatchWorkflow(workflowFile, token);
    } catch (err) {
      setStatus(`${label}: dispatch failed — ${err.message}`, 'var(--red)');
      btn.classList.remove('running');
      buttons.forEach(b => b.disabled = false);
      if (/401|403/.test(err.message)) {
        localStorage.removeItem(TOKEN_KEY);
        setStatus(`${label}: token rejected — cleared. Tap again to re-enter.`, 'var(--red)');
      }
      return;
    }

    setStatus(`${label}: dispatched, waiting for run…`, 'var(--accent-light)');
    buttons.forEach(b => { if (b !== btn) b.disabled = false; });
    pollRun(workflowFile, label, token, dispatchedAt, btn);
  }

  buttons.forEach(btn => btn.addEventListener('click', () => handleClick(btn)));

  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      localStorage.removeItem(TOKEN_KEY);
      setStatus('Token cleared.', 'var(--muted)');
    });
  }
})();
