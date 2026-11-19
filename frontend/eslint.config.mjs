import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  {
    ignores: ["src/generated/**", "src/vendor/**"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    // fx-core is host-agnostic: the same code draws in Chrome and under deno on the render worker, so it may not
    // touch the DOM, Node or React. Tests and the node-only golden helpers are exempt.
    files: ["src/lib/fx/core/**/*.{ts,tsx,js,mjs}"],
    ignores: ["src/lib/fx/core/**/*.test.{ts,tsx}", "src/lib/fx/core/test-utils/**"],
    rules: {
      "no-restricted-globals": [
        "error",
        { name: "window", message: "fx-core runs outside the browser; take what you need from the FxHost." },
        { name: "document", message: "fx-core runs outside the browser; draw through FxHost / FxContext2D." },
      ],
      "no-restricted-imports": [
        "error",
        {
          paths: [
            { name: "fs", message: "fx-core may not use Node APIs." },
            { name: "react", message: "fx-core may not depend on React." },
          ],
          patterns: [
            { group: ["node:*"], message: "fx-core may not use Node APIs." },
            { group: ["react/*", "react-dom", "react-dom/*"], message: "fx-core may not depend on React." },
          ],
        },
      ],
    },
  },
];

export default eslintConfig;
