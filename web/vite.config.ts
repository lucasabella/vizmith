import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    // The warning this raises past is about the renderer chunk, and its advice — split it
    // out with a dynamic import — is exactly what `chart/Deferred.tsx` does. What is left
    // is 565 kB of ECharts that is 70% of everything this ships and is no longer in front
    // of the first paint, which is a recorded decision rather than an oversight, and a
    // warning printed on every build for a decision already made is one nobody reads.
    //
    // Set just above that chunk rather than switched off, so the signal survives: a second
    // oversized chunk still warns, and so does this one if the renderer grows another 6%,
    // which is a number worth being told about.
    chunkSizeWarningLimit: 600,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
