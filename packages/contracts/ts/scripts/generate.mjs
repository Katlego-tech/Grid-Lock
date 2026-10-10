// Compile packages/contracts/schema/contracts.json into src/generated.d.ts.
//
//   node scripts/generate.mjs           write src/generated.d.ts
//   node scripts/generate.mjs --check   exit 1 if src/generated.d.ts is not what the schema gives
//
// The schema comes from the Pydantic models (packages/contracts/scripts/export_schema.py), so
// the chain is models -> schema -> types, with no hand-written step a field could drift in.
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { compile } from "json-schema-to-typescript";
import prettier from "prettier";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const schemaPath = here("../../schema/contracts.json");
const outPath = here("../src/generated.d.ts");

const banner = `/**
 * GridLock contracts, generated from packages/contracts/gridlock_contracts. Do not edit.
 *
 * Change the Pydantic model, then regenerate from the repo root:
 *   uv run python -m packages.contracts.scripts.export_schema
 *   npm run build -w packages/contracts/ts
 */`;

const schema = JSON.parse(await readFile(schemaPath, "utf8"));
const raw = await compile(schema, "GridLockContract", {
  bannerComment: banner,
  additionalProperties: false,
  format: false,
});
const options = (await prettier.resolveConfig(outPath)) ?? {};
const generated = await prettier.format(raw, { ...options, filepath: outPath });

if (process.argv.includes("--check")) {
  const committed = await readFile(outPath, "utf8").catch(() => "");
  if (committed.replace(/\r\n/g, "\n") !== generated) {
    console.error(
      "src/generated.d.ts does not match schema/contracts.json. Regenerate it:\n" +
        "  npm run build -w packages/contracts/ts",
    );
    process.exit(1);
  }
  console.log("src/generated.d.ts matches schema/contracts.json");
} else {
  await writeFile(outPath, generated);
  console.log("wrote src/generated.d.ts");
}
