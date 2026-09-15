"use strict";

// Verify reconstructed TypeScript without launching Electron or Python.
// Optional argument: packaged resources/app directory to compare against.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const ts = require("typescript");
const root = path.resolve(__dirname, "..");
const baseline = process.argv[2] ? path.resolve(process.argv[2]) : root;
const config = ts.readConfigFile(path.join(root, "tsconfig.json"), ts.sys.readFile);
assert(!config.error, "Cannot read tsconfig.json");
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
assert.equal(parsed.errors.length, 0, "Invalid TypeScript configuration");

function tokens(text) {
  const original = ts.createSourceFile("baseline.js", text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(original.parseDiagnostics.length, 0, "Invalid generated JavaScript");
  // The printer removes comments and optional trailing argument commas;
  // AST leaves then ignore formatting without confusing regex/template tokens.
  const printed = ts.createPrinter({ removeComments: true }).printFile(original);
  const source = ts.createSourceFile("baseline.js", printed, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  assert.equal(source.parseDiagnostics.length, 0, "Invalid generated JavaScript");
  const result = [];
  function visit(node) {
    const children = node.getChildren(source);
    if (children.length) children.forEach(visit);
    else if (node.kind !== ts.SyntaxKind.EndOfFileToken && node.kind !== ts.SyntaxKind.SyntaxList) {
      result.push([node.kind, node.getText(source)]);
    }
  }
  visit(source);
  return result;
}

for (const name of fs.readdirSync(path.join(root, "src")).filter(n => n.endsWith(".ts"))) {
  const output = ts.transpileModule(fs.readFileSync(path.join(root, "src", name), "utf8"), {
    compilerOptions: parsed.options,
    fileName: name,
    reportDiagnostics: true,
  });
  assert.equal(output.diagnostics.length, 0, name + ": syntax diagnostics");
  const js = name.replace(/\.ts$/, ".js");
  assert.deepStrictEqual(tokens(output.outputText), tokens(fs.readFileSync(path.join(baseline, js), "utf8")), js + ": generated code differs");
  console.log(js + ": generated tokens match");
}

if (baseline !== root) {
  // These are all runtime data/code folders present in the source baseline.
  const folders = ["app", "assets", "css", "csv", "js", "pages", "py", "python"];
  let checked = 0;
  function compare(relative) {
    for (const entry of fs.readdirSync(path.join(baseline, relative), { withFileTypes: true })) {
      if (entry.name === "__pycache__") continue;
      const file = path.join(relative, entry.name);
      if (entry.isDirectory()) compare(file);
      else {
        assert(fs.readFileSync(path.join(root, file)).equals(fs.readFileSync(path.join(baseline, file))), file + ": packaged content differs");
        checked++;
      }
    }
  }
  for (const folder of folders) if (fs.existsSync(path.join(baseline, folder))) compare(folder);
  console.log(checked + " packaged runtime files match byte for byte (Python caches excluded)");
}
