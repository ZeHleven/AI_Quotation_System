<template>
  <div class="runtime-graph-shell">
    <div class="graph-toolbar">
      <div class="graph-legend">
        <span v-for="item in legend" :key="item.kind">
          <i :style="{ background: colorFor(item.kind) }"></i>{{ item.label }}
        </span>
      </div>
      <span class="graph-hint">点击节点查看只读血缘</span>
    </div>
    <div class="graph-stage">
      <svg
        class="runtime-graph"
        :viewBox="`0 0 ${layout.width} ${layout.height}`"
        role="img"
        aria-label="报价资料研判 Agent 运行 DAG"
      >
        <defs>
          <marker id="runtime-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
          </marker>
          <filter id="runtime-shadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#0f172a" flood-opacity="0.14" />
          </filter>
        </defs>
        <g class="lane-layer">
          <g v-for="lane in layout.lanes" :key="lane.index">
            <rect :x="lane.x" y="16" :width="lane.width" :height="layout.height - 32" rx="16" />
            <text :x="lane.x + 14" y="42">{{ lane.label }}</text>
          </g>
        </g>
        <g class="edge-layer">
          <path
            v-for="edge in layout.edges"
            :key="edge.id"
            :d="edge.path"
            :class="['graph-edge', `edge-${edge.kind}`]"
            marker-end="url(#runtime-arrow)"
          />
        </g>
        <g class="node-layer">
          <g
            v-for="node in layout.nodes"
            :key="node.id"
            :transform="`translate(${node.x}, ${node.y})`"
            :class="['graph-node', { selected: selected?.id === node.id }]"
            tabindex="0"
            role="button"
            @click="selectNode(node)"
            @keyup.enter="selectNode(node)"
          >
            <rect :width="node.width" :height="node.height" rx="12" filter="url(#runtime-shadow)" />
            <rect class="node-accent" width="5" :height="node.height" rx="3" :fill="colorFor(node.kind)" />
            <circle :cx="node.width - 17" cy="17" r="5" :fill="statusColor(node.status)" />
            <text class="node-kind" x="15" y="20">{{ kindLabel(node.kind) }}</text>
            <text class="node-label" x="15" y="41">{{ truncate(node.label, 23) }}</text>
            <text class="node-status" x="15" y="59">{{ node.status || 'recorded' }}</text>
          </g>
        </g>
      </svg>
    </div>
    <aside v-if="selected" class="node-inspector">
      <div class="inspector-title">
        <i :style="{ background: colorFor(selected.kind) }"></i>
        <div>
          <small>{{ kindLabel(selected.kind) }}</small>
          <strong>{{ selected.label }}</strong>
        </div>
        <el-tag size="small" effect="plain">{{ selected.status }}</el-tag>
      </div>
      <dl>
        <template v-for="(value, key) in selected.details" :key="key">
          <dt>{{ key }}</dt><dd>{{ value ?? '-' }}</dd>
        </template>
        <template v-for="(value, key) in selected.hashes" :key="`hash-${key}`">
          <dt>{{ key }}</dt><dd><code>{{ value }}</code></dd>
        </template>
      </dl>
    </aside>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  trace: { type: Object, required: true },
})

const selected = ref(null)
const columnKinds = [
  ['run'],
  ['plan'],
  ['task'],
  ['task_attempt'],
  ['context', 'checkpoint'],
  ['model_call', 'tool_invocation'],
  ['model_attempt', 'tool_dispatch', 'async_operation'],
  ['model_result', 'dispatch_attempt', 'tool_result'],
  ['validation', 'validation_attempt'],
]
const laneLabels = ['Run', 'Planner', 'DAG / Skill', 'Lease / Fence', 'Context / Checkpoint', 'Gateway', 'Executor', 'Result Store', 'Convergence']
const colors = {
  run: '#0f766e', plan: '#7c3aed', task: '#2563eb', task_attempt: '#4f46e5',
  context: '#0891b2', checkpoint: '#d97706', model_call: '#9333ea', model_attempt: '#a855f7',
  model_result: '#c026d3', tool_invocation: '#0284c7', tool_dispatch: '#0369a1',
  dispatch_attempt: '#0e7490', tool_result: '#059669', async_operation: '#475569',
  validation: '#dc2626', validation_attempt: '#e11d48',
}
const legend = [
  { kind: 'task', label: 'Task / Skill' },
  { kind: 'context', label: 'Context' },
  { kind: 'model_call', label: 'Model' },
  { kind: 'tool_invocation', label: 'Tool' },
  { kind: 'checkpoint', label: 'Checkpoint' },
  { kind: 'validation', label: 'Validation' },
]

const layout = computed(() => {
  const rawNodes = Array.isArray(props.trace?.nodes) ? props.trace.nodes : []
  const rawEdges = Array.isArray(props.trace?.edges) ? props.trace.edges : []
  const columnWidth = 206
  const gapX = 22
  const nodeWidth = 174
  const nodeHeight = 70
  const top = 62
  const gapY = 18
  const groups = columnKinds.map((kinds) => rawNodes.filter((node) => kinds.includes(node.kind)))
  const known = new Set(columnKinds.flat())
  const unknown = rawNodes.filter((node) => !known.has(node.kind))
  if (unknown.length) groups[groups.length - 1].push(...unknown)
  const maxRows = Math.max(1, ...groups.map((group) => group.length))
  const width = columnKinds.length * (columnWidth + gapX) + 28
  const height = Math.max(430, top + maxRows * (nodeHeight + gapY) + 42)
  const nodes = []
  const positions = new Map()
  groups.forEach((group, columnIndex) => {
    group.forEach((node, rowIndex) => {
      const placed = {
        ...node,
        x: 28 + columnIndex * (columnWidth + gapX) + 16,
        y: top + rowIndex * (nodeHeight + gapY),
        width: nodeWidth,
        height: nodeHeight,
      }
      nodes.push(placed)
      positions.set(node.id, placed)
    })
  })
  const edges = rawEdges.flatMap((edge, index) => {
    const source = positions.get(edge.source)
    const target = positions.get(edge.target)
    if (!source || !target) return []
    const sx = source.x + source.width
    const sy = source.y + source.height / 2
    const tx = target.x
    const ty = target.y + target.height / 2
    const direction = tx >= sx ? 1 : -1
    const bend = Math.max(28, Math.abs(tx - sx) * 0.42) * direction
    return [{
      ...edge,
      id: `${edge.source}:${edge.target}:${edge.kind}:${index}`,
      path: `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`,
    }]
  })
  const lanes = columnKinds.map((_kinds, index) => ({
    index,
    label: laneLabels[index],
    x: 28 + index * (columnWidth + gapX),
    width: columnWidth,
  }))
  return { nodes, edges, lanes, width, height }
})

watch(() => props.trace, () => { selected.value = null })

function selectNode(node) { selected.value = node }
function colorFor(kind) { return colors[kind] || '#64748b' }
function kindLabel(kind) { return String(kind || 'node').replaceAll('_', ' ').toUpperCase() }
function truncate(value, limit) {
  const text = String(value || '')
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}
function statusColor(status) {
  if (['succeeded', 'passed', 'immutable', 'frozen', 'ok', 'completed'].includes(status)) return '#22c55e'
  if (['failed', 'stale', 'dead_letter', 'cancelled'].includes(status)) return '#ef4444'
  if (['running', 'leased', 'sending', 'accepted', 'queued', 'ready'].includes(status)) return '#f59e0b'
  return '#94a3b8'
}
</script>

<style scoped>
.runtime-graph-shell { display: grid; gap: 12px; }
.graph-toolbar { display: flex; justify-content: space-between; gap: 16px; align-items: center; }
.graph-legend { display: flex; flex-wrap: wrap; gap: 13px; color: #475569; font-size: 12px; }
.graph-legend span { display: inline-flex; gap: 6px; align-items: center; }
.graph-legend i { width: 8px; height: 8px; border-radius: 99px; }
.graph-hint { color: #94a3b8; font-size: 12px; }
.graph-stage { overflow: auto; min-height: 430px; border: 1px solid #dbe5ef; border-radius: 16px; background: radial-gradient(circle at 12% 10%, #eff6ff 0, transparent 28%), #f8fafc; }
.runtime-graph { display: block; width: max(100%, 1840px); min-height: 430px; }
.lane-layer rect { fill: rgba(255, 255, 255, 0.62); stroke: #e2e8f0; stroke-dasharray: 4 5; }
.lane-layer text { fill: #64748b; font-size: 11px; font-weight: 700; letter-spacing: .06em; }
.graph-edge { fill: none; stroke: #94a3b8; stroke-width: 1.3; opacity: .62; }
.edge-depends_on { stroke: #2563eb; stroke-width: 1.7; }
.edge-lineage { stroke-dasharray: 4 4; }
.graph-node { cursor: pointer; outline: none; }
.graph-node > rect:first-of-type { fill: #fff; stroke: #dbe5ef; stroke-width: 1; }
.graph-node:hover > rect:first-of-type, .graph-node.selected > rect:first-of-type { stroke: #2563eb; stroke-width: 2; }
.node-kind { fill: #64748b; font-size: 9px; font-weight: 800; letter-spacing: .08em; }
.node-label { fill: #0f172a; font-size: 12px; font-weight: 700; }
.node-status { fill: #64748b; font-size: 10px; }
.node-inspector { border: 1px solid #dbe5ef; background: #fff; border-radius: 14px; padding: 14px; }
.inspector-title { display: flex; align-items: center; gap: 10px; }
.inspector-title > i { width: 10px; height: 36px; border-radius: 8px; }
.inspector-title > div { display: grid; gap: 3px; flex: 1; }
.inspector-title small { color: #64748b; font-size: 10px; letter-spacing: .08em; }
.inspector-title strong { color: #0f172a; }
.node-inspector dl { display: grid; grid-template-columns: 160px minmax(0, 1fr); margin: 14px 0 0; font-size: 12px; }
.node-inspector dt, .node-inspector dd { padding: 7px 8px; border-top: 1px solid #eef2f7; margin: 0; overflow-wrap: anywhere; }
.node-inspector dt { color: #64748b; }
.node-inspector dd { color: #0f172a; }
.node-inspector code { font-size: 11px; }
@media (max-width: 800px) { .graph-toolbar { align-items: flex-start; flex-direction: column; } }
</style>
