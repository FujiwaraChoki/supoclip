// Fails the build when a browser chunk contains syntax that older browsers
// cannot parse. Next.js compiles our code for its default browser targets but
// copies most of node_modules as published, and one chunk that fails to parse
// takes every route that loads it down with a ChunkLoadError.
//
// This is a list of known breakers, not a browser support guarantee: the
// bundle already needs optional chaining and logical assignment (Chrome 85).
// When it fails, add the package to `transpilePackages` in next.config.ts.
//
// Usage: node scripts/check-client-syntax.mjs [chunksDir]
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

// Feature -> first engines that parse it.
const ENGINES = {
  "class static block": "Chrome 94, Safari 16.4",
  "RegExp v flag": "Chrome 112, Safari 17",
  "using declaration": "Chrome 134",
};

export function findNewerSyntax(source) {
  const file = ts.createSourceFile("chunk.js", source, ts.ScriptTarget.Latest, false, ts.ScriptKind.JS);
  const found = [];
  const report = (feature, node) =>
    found.push({ feature, engines: ENGINES[feature], offset: node.getStart(file), excerpt: node.getText(file).slice(0, 80) });
  const visit = (node) => {
    if (ts.isClassStaticBlockDeclaration(node)) {
      report("class static block", node);
    } else if (node.kind === ts.SyntaxKind.RegularExpressionLiteral) {
      if (node.text.slice(node.text.lastIndexOf("/") + 1).includes("v")) report("RegExp v flag", node);
    } else if (ts.isVariableDeclarationList(node) && node.flags & ts.NodeFlags.Using) {
      report("using declaration", node);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

function chunkFiles(dir) {
  return readdirSync(dir, { withFileTypes: true, recursive: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".js"))
    .map((entry) => join(entry.parentPath, entry.name));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const dir = process.argv[2] ?? join(process.env.NEXT_BUILD_DIR || ".next", "static", "chunks");
  const files = chunkFiles(dir);
  let failures = 0;
  for (const path of files) {
    for (const hit of findNewerSyntax(readFileSync(path, "utf8"))) {
      failures++;
      console.error(`${relative(dir, path)}@${hit.offset}: ${hit.feature} (needs ${hit.engines}): ${hit.excerpt}`);
    }
  }
  if (failures) {
    console.error(`\n${failures} syntax error(s) for older browsers in ${dir}. Add the package to transpilePackages in next.config.ts.`);
    process.exit(1);
  }
  console.log(`Checked ${files.length} client chunks: no class static blocks, RegExp v flags or using declarations.`);
}
