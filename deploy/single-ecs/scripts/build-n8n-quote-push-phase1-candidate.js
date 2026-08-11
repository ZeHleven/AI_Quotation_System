const fs = require("fs");

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error("usage: node build-n8n-quote-push-phase1-candidate.js INPUT OUTPUT");
}

const SOURCE_WORKFLOW_ID = "UPGK6O16kr0xtO9z";
const CANDIDATE_WORKFLOW_ID = "QpP1Cand20260808";
const CANDIDATE_WEBHOOK_PATH = "budget-push-phase1-candidate";

const inputText = fs.readFileSync(inputPath, "utf8").replace(/^\uFEFF/, "");
const raw = JSON.parse(inputText);
const workflows = Array.isArray(raw) ? raw : Array.isArray(raw.data) ? raw.data : [raw];
const source = workflows.find((workflow) => workflow.id === SOURCE_WORKFLOW_ID);
if (!source) throw new Error(`missing source workflow: ${SOURCE_WORKFLOW_ID}`);
if (!source.active) throw new Error("source quote-push workflow is not active");

const candidate = JSON.parse(JSON.stringify(source));
candidate.id = CANDIDATE_WORKFLOW_ID;
candidate.name = "[Candidate] Quote Push Consistency Phase 1";
candidate.active = false;
candidate.isArchived = false;
candidate.pinData = {};
candidate.staticData = null;

for (const field of [
  "createdAt",
  "updatedAt",
  "versionId",
  "activeVersionId",
  "activeVersion",
  "shared",
  "triggerCount",
  "meta",
]) {
  delete candidate[field];
}

const nodes = candidate.nodes || [];
const byName = new Map(nodes.map((node) => [node.name, node]));
const webhook = nodes.find((node) => node.type === "n8n-nodes-base.webhook");
if (!webhook) throw new Error("source webhook node not found");
if (!webhook.credentials?.httpHeaderAuth) {
  throw new Error("source webhook does not use reusable httpHeaderAuth credentials");
}

const firstTarget = candidate.connections?.[webhook.name]?.main?.[0]?.[0]?.node;
const firstNode = firstTarget && byName.get(firstTarget);
if (!firstNode) throw new Error("source webhook first node not found");

let fanoutNode = firstNode;
const linearVisited = new Set();
while (true) {
  if (linearVisited.has(fanoutNode.name)) throw new Error("source pre-delivery chain contains a cycle");
  linearVisited.add(fanoutNode.name);
  const outgoing = candidate.connections?.[fanoutNode.name]?.main?.[0] || [];
  if (fanoutNode.type === "n8n-nodes-base.code" && outgoing.length >= 2) break;
  if (outgoing.length !== 1) throw new Error("source fanout code node not found");
  fanoutNode = byName.get(outgoing[0].node);
  if (!fanoutNode) throw new Error("source pre-delivery target not found");
}

const fanoutTargets = (candidate.connections?.[fanoutNode.name]?.main?.[0] || [])
  .map((edge) => byName.get(edge.node))
  .filter(Boolean);
const textDelivery = fanoutTargets.find((node) => node.type === "n8n-nodes-base.httpRequest");
const fileBranchStart = fanoutTargets.find((node) => node.type === "n8n-nodes-base.code");
if (!textDelivery || !fileBranchStart) {
  throw new Error("source text/file delivery branches not found");
}

let fileDelivery = fileBranchStart;
const visited = new Set();
while (candidate.connections?.[fileDelivery.name]?.main?.[0]?.length) {
  if (visited.has(fileDelivery.name)) throw new Error("file delivery branch contains a cycle");
  visited.add(fileDelivery.name);
  const edges = candidate.connections[fileDelivery.name].main[0];
  if (edges.length !== 1) throw new Error("file delivery branch is not linear");
  fileDelivery = byName.get(edges[0].node);
  if (!fileDelivery) throw new Error("file delivery branch target not found");
}
if (fileDelivery.type !== "n8n-nodes-base.httpRequest") {
  throw new Error("file delivery terminal node is not HTTP Request");
}

webhook.parameters = {
  ...webhook.parameters,
  path: CANDIDATE_WEBHOOK_PATH,
  responseMode: "responseNode",
};
webhook.webhookId = "a6d8d0c8-9c59-4cbe-9442-45a28c216a38";

const callbackCredentials = JSON.parse(JSON.stringify(webhook.credentials));
const callbackBody = `={{ {
  idempotency_key: $('${webhook.name}').first().json.body.idempotency_key,
  quote_job_id: $('${webhook.name}').first().json.body.quote_job_id || $('${webhook.name}').first().json.body.job_id || null,
  execution_id: $execution.id
} }}`;

function httpCallbackNode(name, id, path, position) {
  return {
    parameters: {
      method: "POST",
      url: `http://api:9000/api/v1/internal/n8n/quote-push/${path}`,
      authentication: "genericCredentialType",
      genericAuthType: "httpHeaderAuth",
      sendBody: true,
      specifyBody: "json",
      jsonBody: callbackBody,
      options: {},
    },
    id,
    name,
    type: "n8n-nodes-base.httpRequest",
    typeVersion: 4.3,
    position,
    credentials: JSON.parse(JSON.stringify(callbackCredentials)),
  };
}

const validateNode = {
  parameters: {
    mode: "runOnceForAllItems",
    jsCode: `const body = $('${webhook.name}').first().json.body || {};
const key = String(body.idempotency_key || '').trim();
if (!/^[0-9a-f]{64}$/.test(key)) throw new Error('INVALID_IDEMPOTENCY_KEY');
return [{ json: { idempotency_key: key, quote_job_id: body.quote_job_id || body.job_id || null } }];`,
  },
  id: "6a66bb08-e71e-4dda-a92b-e7550c7e2e74",
  name: "Phase1 Validate Idempotency Key",
  type: "n8n-nodes-base.code",
  typeVersion: 2,
  position: [webhook.position[0] + 240, webhook.position[1]],
};
const claimNode = httpCallbackNode(
  "Phase1 Claim Push",
  "848f613c-e089-4ec2-9872-724203cb7aa2",
  "claim",
  [webhook.position[0] + 480, webhook.position[1]],
);
const claimedIfNode = {
  parameters: {
    conditions: {
      options: { caseSensitive: true, leftValue: "", typeValidation: "strict", version: 2 },
      conditions: [
        {
          id: "c4378700-a095-432e-b9bd-7e74c7a65a54",
          leftValue: "={{ $json.action }}",
          rightValue: "claimed",
          operator: { type: "string", operation: "equals" },
        },
      ],
      combinator: "and",
    },
    options: {},
  },
  id: "e01174f1-bc99-41aa-b4cf-6425c383f92c",
  name: "Phase1 Is Newly Claimed",
  type: "n8n-nodes-base.if",
  typeVersion: 2.2,
  position: [webhook.position[0] + 720, webhook.position[1]],
};
const existingResponseNode = {
  parameters: {
    respondWith: "json",
    responseBody: "={{ $json }}",
    options: { responseCode: 409 },
  },
  id: "e69e0637-3274-4352-9a69-082253365c15",
  name: "Phase1 Respond Existing State",
  type: "n8n-nodes-base.respondToWebhook",
  typeVersion: 1.4,
  position: [webhook.position[0] + 960, webhook.position[1] + 260],
};
const dispatchNode = httpCallbackNode(
  "Phase1 Mark Dispatching",
  "616ab4bd-5a20-4227-a153-08950f67c083",
  "dispatch-start",
  [fanoutNode.position[0] + 240, fanoutNode.position[1]],
);
const restoreNode = {
  parameters: {
    mode: "runOnceForAllItems",
    jsCode: `return $('${fanoutNode.name}').all();`,
  },
  id: "cd848d78-bb3d-4c73-9040-48fba42d0bed",
  name: "Phase1 Restore Delivery Items",
  type: "n8n-nodes-base.code",
  typeVersion: 2,
  position: [fanoutNode.position[0] + 480, fanoutNode.position[1]],
};

function deliveryValidationNode(name, id, position) {
  return {
    parameters: {
      mode: "runOnceForAllItems",
      jsCode: `const items = $input.all();
for (const item of items) {
  const value = item.json || {};
  const code = value.errcode ?? value.errorCode ?? value.code;
  const accepted = code === undefined || [0, '0', 200, '200', 'OK', 'ok'].includes(code);
  if (!accepted || value.success === false) throw new Error('DINGTALK_DELIVERY_REJECTED');
}
return items;`,
    },
    id,
    name,
    type: "n8n-nodes-base.code",
    typeVersion: 2,
    position,
  };
}

const validateTextNode = deliveryValidationNode(
  "Phase1 Validate Text Delivery",
  "cb17a0f8-b27b-4d9d-90d7-e55b50075f53",
  [textDelivery.position[0] + 240, textDelivery.position[1]],
);
const validateFileNode = deliveryValidationNode(
  "Phase1 Validate File Delivery",
  "e5ba0e8b-ce47-4d36-9d1e-f6c8e318c22c",
  [fileDelivery.position[0] + 240, fileDelivery.position[1]],
);
const mergeNode = {
  parameters: { mode: "append", numberInputs: 2 },
  id: "b93d853e-3693-4e3d-9b21-67f52a2b5cff",
  name: "Phase1 Wait Both Deliveries",
  type: "n8n-nodes-base.merge",
  typeVersion: 3.2,
  position: [Math.max(validateTextNode.position[0], validateFileNode.position[0]) + 260, fanoutNode.position[1]],
};
const deliveredNode = httpCallbackNode(
  "Phase1 Mark Delivered",
  "024107b4-98ac-4147-a363-ed94910f4281",
  "delivered",
  [mergeNode.position[0] + 260, mergeNode.position[1]],
);
const successResponseNode = {
  parameters: {
    respondWith: "json",
    responseBody: `={{ { ok: true, action: 'delivered', idempotency_key: $('${webhook.name}').first().json.body.idempotency_key } }}`,
    options: { responseCode: 200 },
  },
  id: "89374db9-26e2-4ac6-821b-da8885650f40",
  name: "Phase1 Respond Delivered",
  type: "n8n-nodes-base.respondToWebhook",
  typeVersion: 1.4,
  position: [deliveredNode.position[0] + 260, deliveredNode.position[1]],
};

const addedNodes = [
  validateNode,
  claimNode,
  claimedIfNode,
  existingResponseNode,
  dispatchNode,
  restoreNode,
  validateTextNode,
  validateFileNode,
  mergeNode,
  deliveredNode,
  successResponseNode,
];
const existingNames = new Set(nodes.map((node) => node.name));
for (const node of addedNodes) {
  if (existingNames.has(node.name)) throw new Error(`candidate node already exists: ${node.name}`);
}
candidate.nodes.push(...addedNodes);

function edge(node, index = 0) {
  return { node, type: "main", index };
}

candidate.connections[webhook.name] = { main: [[edge(validateNode.name)]] };
candidate.connections[validateNode.name] = { main: [[edge(claimNode.name)]] };
candidate.connections[claimNode.name] = { main: [[edge(claimedIfNode.name)]] };
candidate.connections[claimedIfNode.name] = {
  main: [[edge(firstNode.name)], [edge(existingResponseNode.name)]],
};
candidate.connections[existingResponseNode.name] = { main: [] };
candidate.connections[fanoutNode.name] = { main: [[edge(dispatchNode.name)]] };
candidate.connections[dispatchNode.name] = { main: [[edge(restoreNode.name)]] };
candidate.connections[restoreNode.name] = {
  main: [[edge(textDelivery.name), edge(fileBranchStart.name)]],
};
candidate.connections[textDelivery.name] = { main: [[edge(validateTextNode.name)]] };
candidate.connections[fileDelivery.name] = { main: [[edge(validateFileNode.name)]] };
candidate.connections[validateTextNode.name] = { main: [[edge(mergeNode.name, 0)]] };
candidate.connections[validateFileNode.name] = { main: [[edge(mergeNode.name, 1)]] };
candidate.connections[mergeNode.name] = { main: [[edge(deliveredNode.name)]] };
candidate.connections[deliveredNode.name] = { main: [[edge(successResponseNode.name)]] };
candidate.connections[successResponseNode.name] = { main: [] };

const serialized = JSON.stringify([candidate], null, 2) + "\n";
for (const forbidden of ["192.168.88.128", "192.168.1.21"]) {
  if (serialized.includes(forbidden)) throw new Error(`old endpoint remains in candidate: ${forbidden}`);
}
for (const required of [
  CANDIDATE_WEBHOOK_PATH,
  "http://api:9000/api/v1/internal/n8n/quote-push/claim",
  "http://api:9000/api/v1/internal/n8n/quote-push/dispatch-start",
  "http://api:9000/api/v1/internal/n8n/quote-push/delivered",
]) {
  if (!serialized.includes(required)) throw new Error(`candidate invariant missing: ${required}`);
}

fs.writeFileSync(outputPath, serialized, { mode: 0o600 });
console.log(`RESULT|candidate_workflow_id=${CANDIDATE_WORKFLOW_ID}`);
console.log(`RESULT|candidate_active=${candidate.active}`);
console.log(`RESULT|candidate_nodes=${candidate.nodes.length}`);
console.log(`RESULT|candidate_webhook=${CANDIDATE_WEBHOOK_PATH}`);
