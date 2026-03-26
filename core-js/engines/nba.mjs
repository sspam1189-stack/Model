import { createModelEngine } from "../model_engine.mjs";

export function createNbaEngine({ DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER }) {
  return createModelEngine({
    DEFAULT_STATS,
    DEFAULT_W,
    DEFAULT_W_VAR,
    BAYES_HYPER,
    bayes: {
      spread: { sDiffCap: null, useSDiff: false, absLineCap: 12, absLineCapInclusive: false },
      totals: { enabled: false },
    },
  });
}
