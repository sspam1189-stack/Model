import { createModelEngine } from "../model_engine.mjs";

export function createNcaaEngine({
  DEFAULT_STATS,
  DEFAULT_W,
  DEFAULT_W_VAR,
  BAYES_HYPER,
  isTournament,
}) {
  return createModelEngine({
    DEFAULT_STATS,
    DEFAULT_W,
    DEFAULT_W_VAR,
    BAYES_HYPER,
    resolver: "ncaa",
    minGames: 5,
    enableTeamHCA: true,
    isNeutralSite: (g) => isTournament(g._date),
    bayes: {
      spread: {
        mode: "fixed",
        threshold: 0.60,
        favLineCap: 8,
        requireLineNonZero: true,
        useSDiff: false,
        absLineCap: null,
      },
      totals: { enabled: false },
    },
    legacy: {
      spread: {
        minDiffFloor: 3,
        diffCap: 9,
        absLineCap: 10,
        absLineCapInclusive: true,
      },
    },
  });
}
