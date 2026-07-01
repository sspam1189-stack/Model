// Workflow trigger buttons + polling modal.
// Hits the Cloudflare Worker proxy (set WORKER_URL below after deploy).

(function () {
  const WORKER_URL = "https://pydashboard-workflow-proxy.sspam1189.workers.dev";
  const POLL_INTERVAL_MS = 10_000;
  const ACTIVE_RUN_LS = "pydash.activeRun";

  // Last data file each workflow updates (the one written last in the serial
  // commit chain). Polled after the workflow completes to wait until Pages
  // actually deploys the new content before reloading.
  const TARGET_FILE_BY_WORKFLOW = {
    python: "data/nba-props.json",
    mlb: "data/mlb-props.json",
    wnba: "data/wnba-props.json",
  };
  const PAGES_DEPLOY_TIMEOUT_MS = 90_000;
  const PAGES_POLL_INTERVAL_MS = 4_000;

  // Baseline Last-Modified per data file, captured at page load. Used after a
  // workflow completes to detect when Pages has actually deployed new content.
  const baselineLastModified = {};
  async function captureBaseline(file) {
    try {
      const res = await fetch(`${file}?probe=${Date.now()}`, { method: "HEAD", cache: "no-store" });
      baselineLastModified[file] = res.headers.get("last-modified") || null;
    } catch {
      baselineLastModified[file] = null;
    }
  }

  async function isFileDeployed(file, since) {
    try {
      const res = await fetch(`${file}?probe=${Date.now()}`, { method: "HEAD", cache: "no-store" });
      const lm = res.headers.get("last-modified");
      if (!lm) return false;
      const fileMs = new Date(lm).getTime();
      const sinceMs = new Date(since).getTime();
      // Deployed if Last-Modified moved forward beyond when we triggered the
      // run AND differs from what we saw at page load.
      return fileMs > sinceMs && lm !== baselineLastModified[file];
    } catch {
      return false;
    }
  }

  async function api(path, opts = {}) {
    const res = await fetch(`${WORKER_URL}${path}`, {
      ...opts,
      headers: { "X-Access-Key": "nfl", ...(opts.headers || {}) },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ---- Modal ----
  function ensureModal() {
    let modal = document.getElementById("wf-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "wf-modal";
    modal.className = "wf-modal";
    modal.innerHTML = `
      <div class="wf-modal-card">
        <div class="wf-modal-title" id="wf-title">Running…</div>
        <div class="wf-spinner"></div>
        <div class="wf-modal-status" id="wf-status">Dispatching…</div>
        <div class="wf-modal-elapsed" id="wf-elapsed">0s elapsed</div>
        <div class="wf-modal-actions">
          <button type="button" id="wf-hide">Hide (keep running)</button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    document.getElementById("wf-hide").addEventListener("click", () => {
      modal.classList.remove("open");
    });
    return modal;
  }

  function openModal(title) {
    const modal = ensureModal();
    document.getElementById("wf-title").textContent = title;
    document.getElementById("wf-status").textContent = "Dispatching…";
    document.getElementById("wf-elapsed").textContent = "0s elapsed";
    modal.classList.add("open");
  }

  function updateModal({ status, conclusion, elapsedSec }) {
    const statusEl = document.getElementById("wf-status");
    const elapsedEl = document.getElementById("wf-elapsed");
    if (statusEl) {
      const label = conclusion ? `${status} (${conclusion})` : status;
      statusEl.textContent = label;
    }
    if (elapsedEl && elapsedSec !== undefined) elapsedEl.textContent = `${elapsedSec}s elapsed`;
  }

  // ---- Run loop ----
  let dispatchInFlight = false;
  async function dispatch(workflow) {
    // Gate first: prompt() doesn't block queued click events, so a fast
    // double-click would otherwise reach a second prompt and trigger b2b runs.
    if (dispatchInFlight) return;
    dispatchInFlight = true;
    setButtonsDisabled(true);

    const titleByKey = {
      python: "NBA Run Daily (NBA + Fullseason + Props)",
      mlb: "MLB Run Daily",
      wnba: "WNBA Run Daily (Fullseason + Props)",
    };
    const release = () => {
      dispatchInFlight = false;
      setButtonsDisabled(false);
    };

    // Block if any workflow is already running.
    try {
      const ac = await api(`/active`);
      if (ac.active && ac.active.length > 0) {
        const list = ac.active.map(r => `• ${r.workflow} (${r.status}, run #${r.runNumber})`).join("\n");
        alert(`A workflow is already running. Wait for it to finish first:\n\n${list}`);
        release();
        return;
      }
    } catch (err) {
      release();
      alert(`Could not check active runs: ${err.message}`);
      return;
    }

    openModal(titleByKey[workflow] || workflow);
    let dispatched;
    try {
      dispatched = await api(`/dispatch/${workflow}`, { method: "POST" });
    } catch (err) {
      document.getElementById("wf-status").textContent = `Dispatch failed: ${err.message}`;
      setTimeout(() => {
        document.getElementById("wf-modal")?.classList.remove("open");
        release();
      }, 3000);
      return;
    }
    const since = dispatched.dispatchedAt;
    localStorage.setItem(ACTIVE_RUN_LS, JSON.stringify({ workflow, since }));
    pollUntilDone(workflow, since);
  }

  function setButtonsDisabled(disabled) {
    document.querySelectorAll(".wf-btn").forEach(b => { b.disabled = disabled; });
  }

  function pollUntilDone(workflow, since) {
    const startMs = new Date(since).getTime();
    const tick = async () => {
      const elapsedSec = Math.round((Date.now() - startMs) / 1000);
      let st;
      try {
        st = await api(`/status/${workflow}?since=${encodeURIComponent(since)}`);
      } catch (err) {
        updateModal({ status: `Poll error: ${err.message}`, elapsedSec });
        return setTimeout(tick, POLL_INTERVAL_MS);
      }
      if (!st.found) {
        updateModal({ status: "queued (waiting for run to register)", elapsedSec });
        return setTimeout(tick, POLL_INTERVAL_MS);
      }
      updateModal({
        status: st.status,
        conclusion: st.conclusion,
        htmlUrl: st.htmlUrl,
        elapsedSec,
      });
      if (st.status === "completed") {
        localStorage.removeItem(ACTIVE_RUN_LS);
        waitForPagesDeploy(workflow, since);
        return;
      }
      setTimeout(tick, POLL_INTERVAL_MS);
    };
    tick();
  }

  function waitForPagesDeploy(workflow, since) {
    const file = TARGET_FILE_BY_WORKFLOW[workflow];
    if (!file) {
      reloadWithCacheBuster();
      return;
    }
    const startMs = Date.now();
    const tick = async () => {
      const waitedSec = Math.round((Date.now() - startMs) / 1000);
      if (Date.now() - startMs > PAGES_DEPLOY_TIMEOUT_MS) {
        // Fallback: reload anyway after timeout — Pages may already be live.
        reloadWithCacheBuster();
        return;
      }
      const deployed = await isFileDeployed(file, since);
      if (deployed) {
        reloadWithCacheBuster();
        return;
      }
      updateModal({ status: `deploying to Pages (${waitedSec}s)`, elapsedSec: undefined });
      setTimeout(tick, PAGES_POLL_INTERVAL_MS);
    };
    tick();
  }

  function reloadWithCacheBuster() {
    const u = new URL(window.location.href);
    u.searchParams.set("v", Date.now().toString());
    setTimeout(() => { window.location.href = u.toString(); }, 800);
  }

  // ---- Resume in-flight run on page load ----
  function resumeIfActive() {
    const raw = localStorage.getItem(ACTIVE_RUN_LS);
    if (!raw) return;
    try {
      const { workflow, since } = JSON.parse(raw);
      if (!workflow || !since) return;
      const titleByKey = {
        python: "NBA Run Daily (NBA + Fullseason + Props)",
        mlb: "MLB Run Daily",
        wnba: "WNBA Run Daily (Fullseason + Props)",
      };
      dispatchInFlight = true;
      setButtonsDisabled(true);
      openModal(titleByKey[workflow] || workflow);
      pollUntilDone(workflow, since);
    } catch {}
  }

  // ---- Buttons ----
  function injectButtons() {
    const subtitle = document.querySelector(".header .subtitle");
    if (!subtitle) return;
    const bar = document.createElement("div");
    bar.className = "wf-trigger-bar";
    bar.innerHTML = `
      <button type="button" class="wf-btn" data-wf="python">Run NBA Daily</button>
      <button type="button" class="wf-btn" data-wf="wnba">Run WNBA Daily</button>
      <button type="button" class="wf-btn" data-wf="mlb">Run MLB Daily</button>`;
    subtitle.insertAdjacentElement("afterend", bar);
    bar.querySelectorAll(".wf-btn").forEach(btn => {
      btn.addEventListener("click", () => dispatch(btn.dataset.wf));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    injectButtons();
    resumeIfActive();
    // Capture baseline Last-Modified for each tracked data file so we can
    // detect when Pages serves an updated version after a workflow run.
    Object.values(TARGET_FILE_BY_WORKFLOW).forEach(captureBaseline);
  });
})();
