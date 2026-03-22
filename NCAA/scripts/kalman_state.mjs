import { fileURLToPath } from "url";
import { createKalmanState } from "../../core-js/kalman_state.mjs";

const DATA_DIR = fileURLToPath(new URL("../data", import.meta.url));

const {
  KALMAN_DEFAULTS,
  loadKalmanState,
  saveKalmanState,
  initializeKalman,
  applyDailyDrift,
  updateFromGame,
  batchUpdate,
  getTeamAdj,
  kalmanSummary,
  pruneProcessedGames,
} = createKalmanState({
  dataDir: DATA_DIR,
  initialVar: 20,
  gameNoise: 196,
  maxVar: 40,
});

export {
  KALMAN_DEFAULTS,
  loadKalmanState,
  saveKalmanState,
  initializeKalman,
  applyDailyDrift,
  updateFromGame,
  batchUpdate,
  getTeamAdj,
  kalmanSummary,
  pruneProcessedGames,
};
