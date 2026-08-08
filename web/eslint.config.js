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

      // Both of these were warnings, for code that behaved correctly and wanted a
      // restructure to say so. Both restructures are done — `TileChart` derives what it is
      // waiting for from the spec instead of clearing two pieces of state at the top of an
      // effect, and `Chart.tsx` refreshes its click ref in an effect rather than in the
      // render body — so they are errors again, which is what the CI step gates on.
      //
      // A warning that is always present is a warning nobody reads, and the next one to
      // arrive lands in a list that already looks normal. The steady state of this command
      // is no output.
      "react-hooks/set-state-in-effect": "error",
      "react-hooks/refs": "error",
    },
  },

  {
    files: ["**/*.test.{ts,tsx}"],
    languageOptions: { globals: { ...globals.node } },
  },
);
