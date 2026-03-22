import { createSelfTune } from "../../core-js/self_tune.mjs";
import { DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER } from "./defaults.mjs";

const { tuneWeights, computeResidualVar } = createSelfTune({
  DEFAULT_W,
  DEFAULT_W_VAR,
  BAYES_HYPER,
});

export { tuneWeights, computeResidualVar };
