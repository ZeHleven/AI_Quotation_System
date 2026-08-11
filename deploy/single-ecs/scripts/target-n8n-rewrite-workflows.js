const fs = require("fs");

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error("usage: node target-n8n-rewrite-workflows.js INPUT OUTPUT");
}

const parsed = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const workflows = Array.isArray(parsed) ? parsed : [parsed];
const targetIds = new Set([
  "kHbeaP65zPcFmvZs",
  "sc27NkNq3dgOH5L8",
  "ryHRy69WhkvelvRQ",
  "jiXOrZ7NZgl2Megd",
]);

const replacements = [
  {
    name: "rag_192_168_88_128",
    from: "http://192.168.88.128:8001",
    to: "http://rag-service:8001",
    expected: 1,
  },
  {
    name: "rag_192_168_1_21",
    from: "http://192.168.1.21:8001",
    to: "http://rag-service:8001",
    expected: 1,
  },
  {
    name: "dify_192_168_88_128",
    from: "http://192.168.88.128",
    to: "http://dify-nginx",
    expected: 4,
  },
];

function rewrite(value, counts) {
  if (typeof value === "string") {
    let result = value;
    for (const rule of replacements) {
      const matches = result.split(rule.from).length - 1;
      if (matches > 0) {
        counts[rule.name] += matches;
        result = result.split(rule.from).join(rule.to);
      }
    }
    return result;
  }
  if (Array.isArray(value)) return value.map((item) => rewrite(item, counts));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, rewrite(item, counts)])
    );
  }
  return value;
}

const selected = workflows.filter((workflow) => targetIds.has(workflow.id));
const selectedIds = new Set(selected.map((workflow) => workflow.id));
const missing = [...targetIds].filter((id) => !selectedIds.has(id));
if (missing.length) throw new Error(`missing target workflows: ${missing.join(",")}`);
if (selected.length !== targetIds.size) {
  throw new Error(`unexpected selected workflow count: ${selected.length}`);
}

const counts = Object.fromEntries(replacements.map((rule) => [rule.name, 0]));
const rewritten = selected.map((workflow) => ({
  ...workflow,
  nodes: rewrite(workflow.nodes || [], counts),
}));

for (const rule of replacements) {
  if (counts[rule.name] !== rule.expected) {
    throw new Error(
      `replacement count mismatch for ${rule.name}: expected ${rule.expected}, got ${counts[rule.name]}`
    );
  }
}

const serialized = JSON.stringify(rewritten, null, 2) + "\n";
for (const rule of replacements) {
  if (serialized.includes(rule.from)) {
    throw new Error(`old endpoint remains after rewrite: ${rule.name}`);
  }
}
fs.writeFileSync(outputPath, serialized, { mode: 0o600 });

console.log(`RESULT|n8n_workflows_selected=${rewritten.length}`);
for (const rule of replacements) {
  console.log(`RESULT|n8n_rewrite|rule=${rule.name}|count=${counts[rule.name]}`);
}
