import { fileURLToPath } from "url";
import { createStore } from "../../core-js/store.mjs";

const DATA_PATH = fileURLToPath(new URL("../data/history.json", import.meta.url));
const { loadStore, saveStore, upsertRun } = createStore(DATA_PATH);

export { loadStore, saveStore, upsertRun };
