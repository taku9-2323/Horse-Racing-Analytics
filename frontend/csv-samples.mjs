import { readFileSync } from "node:fs";

// Embed only these canonical examples at build time, never a requested filesystem path.
const samples = {
  "virtual:sample-race": new URL("../examples/sample-race.csv", import.meta.url),
  "virtual:sample-results": new URL("../examples/sample-results.csv", import.meta.url),
};
export default {
  name: "canonical-csv-samples",
  resolveId(id) { return Object.hasOwn(samples, id) ? `\0${id}` : undefined; },
  load(id) {
    const key = id.slice(1);
    if (id.startsWith("\0") && Object.hasOwn(samples, key)) {
      return `export default ${JSON.stringify(readFileSync(samples[key], "utf-8"))}`;
    }
  },
};
