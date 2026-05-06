# Workflow Proxy Worker

Tiny Cloudflare Worker that lets the public PythonDashboard trigger
GitHub Actions workflows without exposing a PAT in client-side JS.

## One-time setup

1. **Create a GitHub fine-grained PAT**
   - https://github.com/settings/personal-access-tokens/new
   - Resource owner: `sspam1189-stack`
   - Repository access: Only `sspam1189-stack/Model`
   - Permissions: **Actions: Read and write**, **Metadata: Read**
   - Copy the token (starts with `github_pat_`).

2. **Install wrangler & login** (once per machine)
   ```bash
   npm i -g wrangler
   wrangler login
   ```

3. **Deploy + set secrets** (from this `cloudflare-worker/` directory)
   ```bash
   wrangler deploy
   wrangler secret put GH_TOKEN     # paste the PAT
   wrangler secret put ACCESS_KEY   # pick any passphrase to share with the 2nd user
   ```

4. **Grab the Worker URL** that `wrangler deploy` printed
   (e.g. `https://pydashboard-workflow-proxy.<your-subdomain>.workers.dev`)
   and put it in `PythonDashboard/js/workflow-trigger.js` as `WORKER_URL`.

## Routes

| Method | Path                | Purpose                         |
|--------|---------------------|---------------------------------|
| POST   | `/dispatch/python`  | Fires `py-run-daily.yml`        |
| POST   | `/dispatch/mlb`     | Fires `mlb-run-daily.yml`       |
| GET    | `/status/python`    | Latest run state for python wf  |
| GET    | `/status/mlb`       | Latest run state for mlb wf     |

All routes require header `X-Access-Key: <ACCESS_KEY>`.
Status routes accept `?since=<ISO timestamp>` to find the run created at/after dispatch.
CORS is locked to `https://sspam1189-stack.github.io`.
