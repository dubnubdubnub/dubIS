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
];
