import fs from "fs";
import path from "path";

const DATA = path.join(process.cwd(), "data", "history.json");

const EMPTY_STORE = { runs: [], weights: {} };

export function loadStore() {
  try {
    const raw = fs.readFileSync(DATA, "utf8");
    const store = JSON.parse(raw);

    // If any weight is null/missing, wipe weights so defaults kick in
    const w = store.weights || {};
    const hasNulls = Object.values(w).some(v => v === null || v === undefined);
    if (hasNulls || Object.keys(w).length === 0) {
      console.warn("store.mjs: weights null or missing — resetting to defaults");
      store.weights = null;
    }

    if (!Array.isArray(store.runs)) store.runs = [];
    return store;
  } catch (e) {
    // File doesn't exist or bad JSON — start fresh
    console.warn("store.mjs: history.json not found or invalid — starting fresh");
    fs.mkdirSync(path.dirname(DATA), { recursive: true });
    fs.writeFileSync(DATA, JSON.stringify(EMPTY_STORE, null, 2));
    return { ...EMPTY_STORE, runs: [] };
  }
}

export function saveStore(store) {
  fs.mkdirSync(path.dirname(DATA), { recursive: true });
  fs.writeFileSync(DATA, JSON.stringify(store, null, 2));
}

export function upsertRun(store, run) {
  if (!Array.isArray(store.runs)) store.runs = [];
  run.ranAt = new Date().toLocaleString("en-US", { timeZone: "America/Chicago" });
  const idx = store.runs.findIndex(r => r.date === run.date);
  if (idx >= 0) store.runs[idx] = run;
  else store.runs.push(run);
  store.runs.sort((a, b) => a.date.localeCompare(b.date));
}
