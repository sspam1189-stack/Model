export function normKey(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function clamp(x, min, max) {
  return Math.max(min, Math.min(max, x));
}

export function roundTo(x, digits = 1) {
  const factor = 10 ** digits;
  return Math.round(x * factor) / factor;
}

export function r3(x) { return roundTo(x, 3); }
export function r4(x) { return roundTo(x, 4); }

export function isFiniteNumber(x) {
  return Number.isFinite(x);
}
