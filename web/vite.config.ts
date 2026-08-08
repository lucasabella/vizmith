import react from "@vitejs/plugin-react";
// From vitest rather than vite, because the `test` block below is vitest's and vite's
// own `defineConfig` does not know about it. It is the same function, widened.
import { defineConfig } from "vitest/config";

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

  test: {
    // A DOM, so a test can press something. Every frontend test used to render to a string
    // and assert on the markup, which reaches the first frame and stops: a click, a drop, a
    // select and an effect were all out of reach, and the only place they could be tested
    // was the Playwright suite in the other language — which is deliberately a handful of
    // flows and should stay that way. This is the tier between the two.
    //
    // `renderToStaticMarkup` stays the right tool wherever the question really is "what
    // does this draw", and most of the existing tests still use it.
    environment: "jsdom",
    setupFiles: ["./src/setup.ts"],
    coverage: {
      // The files, rather than the files a test happened to import. `App.tsx` was absent
      // from the report entirely rather than scored at zero, because nothing imported it,
      // so the number described the tests instead of the code. This moves it down before
      // it moves up, which is the honest direction.
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/main.tsx", "src/setup.ts"],
    },
  },
});
