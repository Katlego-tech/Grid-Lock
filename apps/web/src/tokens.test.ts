// @vitest-environment node
// Reads files from disk, so it runs in Node, not the browser-like jsdom environment.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// The console reads the one token set in docs/design/assets rather than a copy of it.
// If that import ever changes to a local copy, this test makes sure it is still complete.
const tokensPath = fileURLToPath(
  new URL("../../../docs/design/assets/tokens.css", import.meta.url),
);
const mainPath = fileURLToPath(new URL("./main.tsx", import.meta.url));
const cssPath = fileURLToPath(new URL("./app.css", import.meta.url));

describe("design tokens", () => {
  it("are imported from docs/design/assets, not copied", () => {
    expect(readFileSync(mainPath, "utf8")).toContain(
      'import "../../../docs/design/assets/tokens.css";',
    );
  });

  it("define every variable the console's stylesheet uses", () => {
    const defined = new Set(readFileSync(tokensPath, "utf8").match(/--gl-[a-z0-9-]+(?=\s*:)/g));
    const used = new Set(readFileSync(cssPath, "utf8").match(/--gl-[a-z0-9-]+/g));
    const missing = [...used].filter((name) => !defined.has(name));
    expect(missing).toEqual([]);
  });
});
