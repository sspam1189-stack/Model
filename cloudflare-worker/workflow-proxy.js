// Cloudflare Worker: GitHub workflow dispatch proxy for PythonDashboard.
// Holds GH_TOKEN + ACCESS_KEY as Worker secrets so the dashboard can trigger
// workflows without exposing credentials.

const REPO = "sspam1189-stack/Model";
const ALLOWED_ORIGIN = "https://sspam1189-stack.github.io";

const WORKFLOWS = {
  python: "py-run-daily.yml",
  // Renamed from mlb-run-daily.yml 2026-08-27: that workflow entity stopped
  // scheduling jobs after a disable/enable toggle (dispatches accepted, run
  // records created, zero jobs ever materialized -- while WNBA on the same
  // self-hosted runners picked up in 4s). A new path = a new workflow id =
  // a clean registration.
  mlb: "mlb-daily.yml",
  wnba: "wnba-run-daily.yml",
  // Dispatched with no inputs, so the workflow runs its default stage ("all").
  nfl: "nfl-weekly.yml",
};

// Scheduled runs. These replaced the workflows' own `on: schedule` blocks on
// 2026-10-20: GitHub fired those 2.5-5.5 HOURS late (a 5:45 PM CT NBA run
// landed after tip-off), while Cloudflare cron triggers fire on the minute.
// The worker has ONE cron trigger ("*/15 * * * *" in wrangler.toml -- the free
// plan allows only a handful) and dispatches whatever in this table matches
// the tick. Same 5-field syntax and UTC times the workflows used; each minute
// must be a multiple of 15. Supported per field: "*", a number, or a comma list.
// An entry with tz is matched against that time zone's wall clock instead of
// UTC, so it keeps its local time across daylight-saving changes.
const SCHEDULE = [
  // NBA full season + props (py-run-daily.yml), Central time year-round
  { cron: "45 10 * * *", wf: "python", tz: "America/Chicago" },  // 10:45 AM CT
  { cron: "15 13 * * *", wf: "python", tz: "America/Chicago" },  //  1:15 PM CT
  { cron: "15 17 * * *", wf: "python", tz: "America/Chicago" },  //  5:15 PM CT
  // WNBA full season + props (wnba-run-daily.yml)
  { cron: "45 15 * * *", wf: "wnba" },    // 10:45 AM CDT
  { cron: "45 21 * * *", wf: "wnba" },    //  4:45 PM CDT
  // NFL weekly pipeline, stage "all" (nfl-weekly.yml)
  { cron: "0 15 * * 3", wf: "nfl" },      // Wed mid-week fetch + project
  { cron: "0 23 * * 4", wf: "nfl" },      // Thu pre-game refresh
  { cron: "0 23 * * 5", wf: "nfl" },      // Fri pre-game refresh
  { cron: "0 15 * * 0", wf: "nfl" },      // Sun morning full pipeline
  { cron: "30 4 * * 1", wf: "nfl" },      // post-SNF grade
  { cron: "30 4 * * 2", wf: "nfl" },      // post-MNF grade
];

function cronFieldMatches(field, value) {
  if (field === "*") return true;
  return field.split(",").some(v => Number(v) === value);
}

const WEEKDAY = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

// Wall-clock fields of Date d in time zone tz (UTC when tz is not given).
function clockParts(d, tz) {
  if (!tz) {
    return { min: d.getUTCMinutes(), hour: d.getUTCHours(), dom: d.getUTCDate(),
             mon: d.getUTCMonth() + 1, dow: d.getUTCDay() };
  }
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: tz, hourCycle: "h23", weekday: "short",
    year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
  }).formatToParts(d).map(x => [x.type, x.value]));
  return { min: Number(p.minute), hour: Number(p.hour), dom: Number(p.day),
           mon: Number(p.month), dow: WEEKDAY[p.weekday] };
}

// true when the 5-field cron expression matches Date d (to the minute) on the
// wall clock of tz (UTC when tz is not given).
function cronMatches(expr, d, tz) {
  const [min, hour, dom, mon, dow] = expr.trim().split(/\s+/);
  const c = clockParts(d, tz);
  return cronFieldMatches(min, c.min)
    && cronFieldMatches(hour, c.hour)
    && cronFieldMatches(dom, c.dom)
    && cronFieldMatches(mon, c.mon)
    && cronFieldMatches(dow, c.dow);
}

// A run older than this that is still queued/in_progress is treated as STALE
// and no longer blocks a new dispatch. GitHub can strand a run permanently:
// one parked by a `concurrency` group is never handed to a runner, so it
// reports "queued" forever and cannot be cancelled, force-cancelled, or
// deleted (cancel/force-cancel 409 "has not been queued yet", delete 403
// "could not delete"). Run #1506 has sat this way since 2026-08-26 and was
// disabling every Run button on the dashboard. Real runs finish in 5-19
// minutes, so two hours is a wide margin over the slowest observed run.
const STALE_RUN_MS = 2 * 60 * 60 * 1000;

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
  const key = (request.headers.get("X-Access-Key") || "").trim();
  const expected = (env.ACCESS_KEY || "").trim();
  if (key !== expected) return json({ error: "unauthorized" }, 401);
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
      if (run.status !== "queued" && run.status !== "in_progress" && run.status !== "waiting") continue;
      const ageMs = Date.now() - new Date(run.created_at).getTime();
      if (ageMs > STALE_RUN_MS) continue;  // stranded run — do not block dispatch
      results.push({
        workflow: name, status: run.status, htmlUrl: run.html_url,
        runNumber: run.run_number, ageSec: Math.round(ageMs / 1000),
      });
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
  // Cron trigger: dispatch every SCHEDULE entry that matches this tick.
  async scheduled(controller, env, ctx) {
    const tick = new Date(controller.scheduledTime);
    const due = [...new Set(SCHEDULE.filter(s => cronMatches(s.cron, tick, s.tz)).map(s => s.wf))];
    for (const wf of due) {
      ctx.waitUntil(
        dispatchWorkflow(env, wf).then(async res => {
          const body = await res.text();
          console.log(`[cron ${tick.toISOString()}] ${wf}: HTTP ${res.status} ${body}`);
        })
      );
    }
  },

  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);

    // /dispatch/{python|mlb|wnba|nfl}
    if (parts[0] === "dispatch" && request.method === "POST") {
      const auth = checkAuth(request, env);
      if (auth) return auth;
      return dispatchWorkflow(env, parts[1]);
    }

    // /status/{python|mlb|wnba|nfl}?since=ISO
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
