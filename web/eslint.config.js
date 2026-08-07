/**
 * What the type checker does not catch.
 *
 * `tsc --noEmit` runs with `strict` on and already covers a lot, so this is deliberately
 * not a style linter: formatting arguments are not worth a build step, and nothing here
 * reformats anybody's code. What it adds is the three classes `tsc` cannot see. A hook
 * whose dependency array is wrong, which is the bug that looks like a caching problem for
 * a day. An element that a keyboard cannot reach, since the interface already cares about
 * that and had nothing checking it. And an `eslint-disable` for a rule that does not
 * exist, which is what `dashboard.ts` carried while no linter was installed at all.
 */

import js from "@eslint/js";
import a11y from "eslint-plugin-jsx-a11y";
import hooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },

  js.configs.recommended,
  tseslint.configs.recommended,

  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "react-hooks": hooks, "jsx-a11y": a11y },
    rules: {
      ...hooks.configs.recommended.rules,
      ...a11y.flatConfigs.recommended.rules,

      // A disable comment for a rule nothing is enforcing is a comment that lies. This is
      // the rule that would have caught the one in dashboard.ts.
      "@typescript-eslint/ban-ts-comment": "error",

      // A leading underscore is the code saying it meant to discard this. That covers an
      // unused argument, and `const { limit_by: _dropped, ...query } = ...`, which is how
      // `spec.ts` drops a key without mutating: the binding exists so the rest can leave
      // without it, and never having a reader is the point.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", ignoreRestSiblings: true },
      ],

      // These three are real observations about working code rather than defects, so they
      // are visible without being a gate. `set-state-in-effect` in particular is asking
      // for a restructure of a component that behaves correctly today, and pairing a
      // linter's arrival with that refactor is how a linter gets reverted. They stay
      // warnings until somebody takes them on deliberately.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/use-memo": "warn",
      "react-hooks/exhaustive-deps": "warn",

      // `Chart.tsx` writes a ref during render on purpose, so a new handler or a new
      // result set does not tear the chart down and rebuild it. The rule is right in
      // general and wrong about this instance: the ref is only ever read inside the click
      // handler, which ECharts cannot call until the effect that attaches it has run, so
      // there is no render that can read a stale one. Warned rather than disabled, so the
      // next write of a ref during render still gets mentioned.
      "react-hooks/refs": "warn",
    },
  },

  {
    files: ["**/*.test.{ts,tsx}"],
    languageOptions: { globals: { ...globals.node } },
  },
);
