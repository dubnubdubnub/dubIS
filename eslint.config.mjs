import globals from "globals";

export default [
  {
    files: ["js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["warn", { vars: "local", args: "none" }],
      "eqeqeq": ["error", "always"],
      "no-throw-literal": "error",
      "prefer-const": "warn",
    },
  },
  {
    // The MV3 extension (extension/jlc-bridge). Same rules as js/, plus the
    // WebExtension globals — without this block eslint matches no config for
    // these files and lints them with zero rules, i.e. `npx eslint extension/`
    // passes over nothing at all.
    files: ["extension/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.webextensions,
      },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["warn", { vars: "local", args: "none" }],
      "eqeqeq": ["error", "always"],
      "no-throw-literal": "error",
      "prefer-const": "warn",
    },
  },
];
