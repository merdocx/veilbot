import js from "@eslint/js";
import globals from "globals";

export default [
  {
    files: ["admin/static/js/**/*.js"],
    ignores: ["admin/static/js/**/*.min.js"],
    languageOptions: {
      sourceType: "module",
      ecmaVersion: 2022,
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
];

