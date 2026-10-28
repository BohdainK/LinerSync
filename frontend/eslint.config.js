import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "playwright-report", "test-results"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended, reactHooks.configs.flat.recommended],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { "react-refresh": reactRefresh },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      // The shadcn registry currently ships `import { cn } from "cn"` (an unrelated npm package).
      // After every `shadcn add`, run `npm run ui:fix`.
      "no-restricted-imports": [
        "error",
        { paths: [{ name: "cn", message: 'Import cn from "@/lib/utils". Run `npm run ui:fix` after `shadcn add`.' }] },
      ],
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
  {
    // shadcn-generated components and test helpers export non-components too.
    files: ["src/components/ui/**", "src/test/**"],
    rules: { "react-refresh/only-export-components": "off" },
  },
);
