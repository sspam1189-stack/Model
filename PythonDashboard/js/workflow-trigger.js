// Workflow trigger buttons + polling modal.
// Hits the Cloudflare Worker proxy (set WORKER_URL below after deploy).

(function () {
  const WORKER_URL = "https://pydashboard-workflow-proxy.sspam1189.workers.dev";
  const POLL_INTERVAL_MS = 10_000;
  const ACTIVE_RUN_LS = "pydash.activeRun"; // {workflow, since, accessKey}

  // In-memory access key for the current run only (cleared on reload unless
  // resumed from ACTIVE_RUN_LS). Never written to plain localStorage on its own.
  let sessionAccessKey = null;

  async function api(path, opts = {}) {
    if (!sessionAccessKey) throw new Error("no access key");
    const res = await fetch(`${WORKER_URL}${path}`, {
      ...opts,
      headers: { "X-Access-Key": sessionAccessKey, ...(opts.headers || {}) },
    });
    if (res.status === 401) {
      sessionAccessKey = null;
      throw new Error("Wrong password");
    }
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
    if (elapsedEl) elapsedEl.textContent = `${elapsedSec}s elapsed`;
  }

  // ---- Run loop ----
  async function dispatch(workflow) {
    const titleByKey = {
      python: "NBA Run Daily (NBA + Fullseason + Props)",
      mlb: "MLB Run Daily",
    };
    // Always require password before dispatching — never auto-saved.
    const pw = prompt("Enter password to run this workflow:");
    if (!pw || !pw.trim()) return;
    sessionAccessKey = pw.trim();

    // Block if any workflow is already running.
    try {
      const ac = await api(`/active`);
      if (ac.active && ac.active.length > 0) {
        const list = ac.active.map(r => `• ${r.workflow} (${r.status}, run #${r.runNumber})`).join("\n");
        alert(`A workflow is already running. Wait for it to finish first:\n\n${list}`);
        sessionAccessKey = null;
        return;
      }
    } catch (err) {
      alert(`Could not check active runs: ${err.message}`);
      sessionAccessKey = null;
      return;
    }

    setButtonsDisabled(true);
    openModal(titleByKey[workflow] || workflow);
    let dispatched;
    try {
      dispatched = await api(`/dispatch/${workflow}`, { method: "POST" });
    } catch (err) {
      document.getElementById("wf-status").textContent = `Dispatch failed: ${err.message}`;
      sessionAccessKey = null;
      // Re-enable + close modal after a brief delay so user can retry.
      setTimeout(() => {
        document.getElementById("wf-modal")?.classList.remove("open");
        setButtonsDisabled(false);
      }, 3000);
      return;
    }
    const since = dispatched.dispatchedAt;
    // Stash key alongside run so polling survives a reload without re-prompting.
    localStorage.setItem(ACTIVE_RUN_LS, JSON.stringify({ workflow, since, accessKey: sessionAccessKey }));
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
        // Hard-reload with cache-buster so fresh data files load.
        const u = new URL(window.location.href);
        u.searchParams.set("v", Date.now().toString());
        setTimeout(() => { window.location.href = u.toString(); }, 1500);
        return;
      }
      setTimeout(tick, POLL_INTERVAL_MS);
    };
    tick();
  }

  // ---- Resume in-flight run on page load ----
  function resumeIfActive() {
    const raw = localStorage.getItem(ACTIVE_RUN_LS);
    if (!raw) return;
    try {
      const { workflow, since, accessKey } = JSON.parse(raw);
      if (!workflow || !since || !accessKey) return;
      sessionAccessKey = accessKey;
      const titleByKey = {
        python: "NBA Run Daily (NBA + Fullseason + Props)",
        mlb: "MLB Run Daily",
      };
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
      <button type="button" class="wf-btn" data-wf="mlb">Run MLB Daily</button>`;
    subtitle.insertAdjacentElement("afterend", bar);
    bar.querySelectorAll(".wf-btn").forEach(btn => {
      btn.addEventListener("click", () => dispatch(btn.dataset.wf));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    injectButtons();
    resumeIfActive();
  });
})();
