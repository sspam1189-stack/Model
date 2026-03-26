import { createModelEngine } from "../model_engine.mjs";

export function createFullseasonEngine({ DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER }) {
  return createModelEngine({
    DEFAULT_STATS,
    DEFAULT_W,
    DEFAULT_W_VAR,
    BAYES_HYPER,
    enableH2H: true,
    bayes: {
      spread: { sDiffCap: null, useSDiff: false },
      totals: { enabled: false },
    },
  });
}
