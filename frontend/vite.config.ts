import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import csvSamples from "./csv-samples.mjs";

export default defineConfig({
  plugins: [react(), csvSamples],
  test: {
    environment: "jsdom",
  },
});
