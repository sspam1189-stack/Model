// Cloudflare Worker: GitHub workflow dispatch proxy for PythonDashboard.
// Holds GH_TOKEN + ACCESS_KEY as Worker secrets so the dashboard can trigger
// workflows without exposing credentials.

const REPO = "sspam1189-stack/Model";
const ALLOWED_ORIGIN = "https://sspam1189-stack.github.io";

const WORKFLOWS = {
  python: "py-run-daily.yml",
  mlb: "mlb-run-daily.yml",
};

function corsHeaders(extra = {}) {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Access-Key",
    "Access-Control-Max-Age": "86400",
    ...extra,
  };
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: corsHeaders({ "Content-Type": "application/json" }),
  });
}

function checkAuth(request, env) {
  const key = request.headers.get("X-Access-Key");
  if (!key || key !== env.ACCESS_KEY) {
    return json({ error: "unauthorized" }, 401);
  }
  return null;
}

async function gh(env, path, init = {}) {
  return fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "pydashboard-worker",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

async function dispatchWorkflow(env, key) {
  const file = WORKFLOWS[key];
  if (!file) return json({ error: "unknown workflow" }, 400);
  const dispatchedAt = new Date().toISOString();
  const res = await gh(env, `/repos/${REPO}/actions/workflows/${file}/dispatches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!res.ok) {
    const text = await res.text();
    return json({ error: "dispatch failed", status: res.status, detail: text }, 502);
  }
  return json({ ok: true, workflow: key, file, dispatchedAt });
}

async function getActive(env) {
  // Returns any non-completed runs across both workflows.
  const results = [];
  for (const [name, file] of Object.entries(WORKFLOWS)) {
    const res = await gh(env, `/repos/${REPO}/actions/workflows/${file}/runs?per_page=5`);
    if (!res.ok) continue;
    const data = await res.json();
    for (const run of data.workflow_runs || []) {
      if (run.status === "queued" || run.status === "in_progress" || run.status === "waiting") {
        results.push({ workflow: name, status: run.status, htmlUrl: run.html_url, runNumber: run.run_number });
      }
    }
  }
  return json({ active: results });
}

async function getStatus(env, key, sinceISO) {
  const file = WORKFLOWS[key];
  if (!file) return json({ error: "unknown workflow" }, 400);
  // Pull most recent runs and pick the first one created at/after `since`.
  const url = `/repos/${REPO}/actions/workflows/${file}/runs?per_page=10&event=workflow_dispatch`;
  const res = await gh(env, url);
  if (!res.ok) {
    const text = await res.text();
    return json({ error: "status failed", status: res.status, detail: text }, 502);
  }
  const data = await res.json();
  const since = sinceISO ? new Date(sinceISO).getTime() : 0;
  // GitHub returns runs newest-first. Find the oldest run whose created_at >= since.
  let match = null;
  for (const run of data.workflow_runs || []) {
    if (new Date(run.created_at).getTime() + 1000 >= since) {
      match = run; // keep walking; we want the oldest >= since (closest to dispatch)
    } else {
      break;
    }
  }
  if (!match) return json({ found: false });
  return json({
    found: true,
    id: match.id,
    status: match.status,        // queued | in_progress | completed
    conclusion: match.conclusion, // success | failure | cancelled | null
    htmlUrl: match.html_url,
    createdAt: match.created_at,
    updatedAt: match.updated_at,
    runNumber: match.run_number,
  });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);

    // /dispatch/{python|mlb}
    if (parts[0] === "dispatch" && request.method === "POST") {
      const auth = checkAuth(request, env);
      if (auth) return auth;
      return dispatchWorkflow(env, parts[1]);
    }

    // /status/{python|mlb}?since=ISO
    if (parts[0] === "status" && request.method === "GET") {
      const auth = checkAuth(request, env);
      if (auth) return auth;
      return getStatus(env, parts[1], url.searchParams.get("since"));
    }

    // /active — list any non-completed runs across all workflows
    if (parts[0] === "active" && request.method === "GET") {
      const auth = checkAuth(request, env);
      if (auth) return auth;
      return getActive(env);
    }

    return json({ error: "not found" }, 404);
  },
};
