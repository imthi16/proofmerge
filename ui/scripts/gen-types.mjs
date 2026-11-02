// Regenerate src/api/generated.ts from the Python API models (dashboard_models.py), so
// the client's types never drift from the server's. Needs the proofmerge package:
//   python -m proofmerge.dashboard_models > src/api/schema.json && npm run gen:types
import { readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const schema = JSON.parse(readFileSync(new URL("../src/api/schema.json", import.meta.url), "utf8"));

// Pydantic titles every field; without this each would become its own named alias.
// Model titles ($defs entries) stay, so each model is one interface.
// Keys of "properties" and "$defs" are names, not keywords: a field called "title" stays.
function untitle(node, keep) {
  if (Array.isArray(node)) return node.forEach((item) => untitle(item, false));
  if (!node || typeof node !== "object") return;
  if (!keep) delete node.title;
  for (const [key, value] of Object.entries(node)) {
    if (key === "$defs") Object.values(value).forEach((def) => untitle(def, true));
    else if (key === "properties") Object.values(value).forEach((prop) => untitle(prop, false));
    else untitle(value, false);
  }
}
untitle(schema, true);
const banner =
  "/* Generated from proofmerge.dashboard_models by scripts/gen-types.mjs. Do not edit. */";
const ts = await compile(schema, "ProofMergeApi", {
  bannerComment: banner,
  additionalProperties: false,
  unreachableDefinitions: true,
  style: { semi: true, singleQuote: false, printWidth: 100 },
});
writeFileSync(new URL("../src/api/generated.ts", import.meta.url), ts);
