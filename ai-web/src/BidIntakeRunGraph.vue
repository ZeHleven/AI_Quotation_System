<template>
  <section class="agent-trace-card">
    <header class="trace-header">
      <div>
        <div class="trace-eyebrow">
          <span class="live-dot" :class="{ active: isLive }"></span>
          Agent Runtime Graph
        </div>
        <h3>实时运行图谱</h3>
        <p>节点会随 LangGraph 执行逐步展开，展示 ReAct、Tool、Observation 与受控决策门。</p>
      </div>
      <div class="trace-header-actions">
        <div class="trace-stats">
          <span><strong>{{ graph.nodes.length }}</strong> 节点</span>
          <span><strong>{{ traceStats.react }}</strong> ReAct</span>
          <span><strong>{{ traceStats.tools }}</strong> Tool</span>
          <span><strong>{{ traceStats.observations }}</strong> Observation</span>
        </div>
        <el-button size="small" @click="fitGraph">适应画布</el-button>
        <el-button
          size="small"
          :type="autoFollow ? 'primary' : ''"
          plain
          @click="autoFollow = !autoFollow"
        >
          自动跟随
        </el-button>
      </div>
    </header>

    <div class="trace-workspace">
      <div ref="containerRef" class="trace-canvas">
        <svg ref="svgRef" aria-label="Agent 实时运行图谱"></svg>
        <div class="trace-legend">
          <span v-for="item in legend" :key="item.kind">
            <i :style="{ background: item.color }"></i>{{ item.label }}
          </span>
        </div>
        <div v-if="!graph.nodes.length" class="trace-empty">
          等待 Agent 生成第一个运行节点
        </div>
      </div>

      <aside class="trace-inspector">
        <template v-if="displayedStep">
          <div class="inspector-heading">
            <span
              class="step-kind-dot"
              :style="{ background: colorFor(displayedStep.kind) }"
            ></span>
            <div>
              <small>{{ kindLabel(displayedStep.kind) }}</small>
              <strong>{{ displayedStep.title }}</strong>
            </div>
            <span :class="['step-state', displayedStep.state]">
              {{ stateLabel(displayedStep.state) }}
            </span>
          </div>
          <p class="step-summary">{{ displayedStep.summary }}</p>
          <dl class="step-meta">
            <template v-if="displayedStep.iteration">
              <dt>ReAct 回合</dt>
              <dd>#{{ displayedStep.iteration }}</dd>
            </template>
            <template v-if="displayedStep.durationMs !== null">
              <dt>耗时</dt>
              <dd>{{ durationText(displayedStep.durationMs) }}</dd>
            </template>
            <template v-if="displayedStep.updatedAt">
              <dt>更新时间</dt>
              <dd>{{ formatTime(displayedStep.updatedAt) }}</dd>
            </template>
          </dl>
          <div
            v-if="detailRows(displayedStep).length"
            class="step-details"
          >
            <div
              v-for="row in detailRows(displayedStep)"
              :key="row.label"
              class="detail-row"
            >
              <span>{{ row.label }}</span>
              <code>{{ row.value }}</code>
            </div>
          </div>
        </template>

        <div class="recent-heading">
          <strong>最近活动</strong>
          <small>点击节点或记录查看详情</small>
        </div>
        <button
          v-for="step in recentSteps"
          :key="step.id"
          type="button"
          :class="['recent-step', { active: displayedStep?.id === step.id }]"
          @click="selectedStepId = step.id"
        >
          <i :style="{ background: colorFor(step.kind) }"></i>
          <span>
            <strong>{{ step.title }}</strong>
            <small>{{ step.summary }}</small>
          </span>
          <em :class="step.state">{{ stateLabel(step.state) }}</em>
        </button>

        <p class="trace-privacy-note">
          展示的是可审计执行摘要、工具输入与结果摘要，不展示模型私有思维链。
        </p>
      </aside>
    </div>
  </section>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'
import * as d3 from 'd3'

const props = defineProps({
  run: { type: Object, default: null },
})

const TRACE_SCHEMA_VERSION = 'bid-intake-agent-trace/v1'
const colors = Object.freeze({
  start: '#64748b',
  preparation: '#3b82f6',
  llm_input: '#2563eb',
  react: '#7c3aed',
  plan: '#8b5cf6',
  loop: '#6366f1',
  guard: '#d97706',
  tool: '#0891b2',
  observation: '#059669',
  synthesis: '#4f46e5',
  policy: '#ea580c',
  gate: '#dc2626',
  repair: '#db2777',
  human: '#ca8a04',
  control: '#475569',
  lifecycle: '#94a3b8',
  error: '#dc2626',
})
const legend = [
  { kind: 'llm_input', label: 'LLM 输入', color: colors.llm_input },
  { kind: 'react', label: 'LLM 研判', color: colors.react },
  { kind: 'plan', label: '行动计划', color: colors.plan },
  { kind: 'tool', label: 'Tool', color: colors.tool },
  { kind: 'observation', label: 'Observation', color: colors.observation },
  { kind: 'loop', label: '循环判断', color: colors.loop },
  { kind: 'gate', label: '证据/策略门', color: colors.gate },
  { kind: 'human', label: '人工节点', color: colors.human },
]

const containerRef = ref(null)
const svgRef = ref(null)
const selectedStepId = ref('')
const autoFollow = ref(true)
const positions = new Map()
let simulation = null
let resizeObserver = null
let zoomBehavior = null
let currentTransform = d3.zoomIdentity
let renderedNodeCount = 0

const graph = computed(() => buildGraph(props.run))
const isLive = computed(() => (
  ['queued', 'running', 'resume_queued'].includes(props.run?.status)
))
const traceStats = computed(() => ({
  react: graph.value.nodes.filter((item) => item.kind === 'react').length,
  tools: graph.value.nodes.filter((item) => item.kind === 'tool').length,
  observations: graph.value.nodes.filter((item) => item.kind === 'observation').length,
}))
const currentStep = computed(() => {
  const nodes = graph.value.nodes
  return [...nodes].reverse().find((item) => item.state === 'running')
    || [...nodes].reverse().find((item) => item.state === 'waiting')
    || nodes[nodes.length - 1]
    || null
})
const displayedStep = computed(() => (
  graph.value.nodes.find((item) => item.id === selectedStepId.value)
  || currentStep.value
))
const recentSteps = computed(() => (
  [...graph.value.nodes]
    .filter((item) => item.kind !== 'start')
    .sort((a, b) => b.sequence - a.sequence)
    .slice(0, 7)
))
const graphSignature = computed(() => (
  graph.value.nodes
    .map((item) => `${item.id}:${item.state}:${item.sequence}`)
    .join('|')
))

watch(
  graphSignature,
  async () => {
    await nextTick()
    renderGraph()
  },
)

onMounted(() => {
  resizeObserver = new ResizeObserver(() => renderGraph())
  if (containerRef.value) resizeObserver.observe(containerRef.value)
  renderGraph()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  simulation?.stop()
})

function buildGraph(run) {
  if (!run) return { nodes: [], edges: [] }
  const events = Array.isArray(run.events) ? run.events : []
  const traceEvents = events.filter((event) => (
    event?.payload?.trace_schema_version === TRACE_SCHEMA_VERSION
  ))
  const root = {
    id: `run:${run.run_uuid || 'current'}`,
    kind: 'start',
    title: '发起研判',
    state: traceEvents.length ? 'completed' : normalizeRunState(run.status),
    summary: `运行 ${run.run_uuid || '-'} 已进入 Agent 执行队列。`,
    details: {
      run_uuid: run.run_uuid,
      trigger_source: run.trigger_source,
      attempt_count: run.attempt_count,
    },
    iteration: null,
    durationMs: null,
    sequence: 0,
    createdAt: run.created_at,
    updatedAt: run.started_at || run.created_at,
    parentIds: [],
  }
  const stepMap = new Map()
  traceEvents.forEach((event, index) => {
    const payload = event.payload || {}
    const id = String(payload.step_id || event.event_uuid || `trace:${index}`)
    const previous = stepMap.get(id)
    stepMap.set(id, {
      id,
      kind: payload.kind || previous?.kind || 'control',
      title: payload.title || previous?.title || payload.node_name || '运行节点',
      state: payload.state || previous?.state || 'completed',
      summary: payload.summary || event.message || previous?.summary || '',
      details: {
        ...(previous?.details || {}),
        ...(payload.details || {}),
      },
      iteration: payload.iteration ?? previous?.iteration ?? null,
      durationMs: payload.duration_ms ?? previous?.durationMs ?? null,
      sequence: Number(payload.sequence || previous?.sequence || index + 1),
      createdAt: previous?.createdAt || event.created_at,
      updatedAt: event.created_at || previous?.updatedAt,
      parentIds: Array.isArray(payload.parent_step_ids)
        ? payload.parent_step_ids.map(String)
        : previous?.parentIds || [],
    })
  })

  let nodes = [root, ...[...stepMap.values()].sort((a, b) => a.sequence - b.sequence)]
  if (!traceEvents.length) {
    const lifecycleEvents = events
      .filter((event) => event.event_type !== 'run_queued')
    const lifecycleNodes = lifecycleEvents
      .map((event, index) => ({
        id: `event:${event.event_uuid || index}`,
        kind: event.event_type === 'run_failed' ? 'error' : 'lifecycle',
        title: lifecycleTitle(event.event_type),
        state: event.event_type === 'run_failed' ? 'failed' : 'completed',
        summary: event.message || `${event.status || '-'} · ${event.phase || '-'}`,
        details: event.payload || {},
        iteration: null,
        durationMs: null,
        sequence: index + 1,
        createdAt: event.created_at,
        updatedAt: event.created_at,
        parentIds: index
          ? [`event:${lifecycleEvents[index - 1]?.event_uuid || index - 1}`]
          : [root.id],
      }))
    nodes = [root, ...lifecycleNodes]
  }

  const ids = new Set(nodes.map((item) => item.id))
  const nodeById = new Map(nodes.map((item) => [item.id, item]))
  const edges = []
  nodes.forEach((node, index) => {
    if (!index) return
    const parents = node.parentIds.length
      ? node.parentIds.filter((id) => ids.has(id))
      : [root.id]
    ;(parents.length ? parents : [root.id]).forEach((parentId) => {
      edges.push({
        id: `${parentId}->${node.id}`,
        source: parentId,
        target: node.id,
        active: node.state === 'running',
        label: edgeLabel(nodeById.get(parentId), node),
      })
    })
  })
  return { nodes, edges }
}

function renderGraph() {
  const container = containerRef.value
  const element = svgRef.value
  const model = graph.value
  if (!container || !element || !model.nodes.length) return

  const width = Math.max(container.clientWidth, 560)
  const height = Math.max(container.clientHeight, 520)
  const svg = d3.select(element)
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('width', width)
    .attr('height', height)

  simulation?.stop()
  svg.selectAll('*').remove()
  const defs = svg.append('defs')
  defs.append('marker')
    .attr('id', 'trace-arrow')
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 27)
    .attr('refY', 0)
    .attr('markerWidth', 6)
    .attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-5L10,0L0,5')
    .attr('fill', '#94a3b8')

  const stage = svg.append('g').attr('class', 'trace-stage')
  zoomBehavior = d3.zoom()
    .scaleExtent([0.35, 2.4])
    .on('zoom', (event) => {
      currentTransform = event.transform
      stage.attr('transform', currentTransform)
    })
  svg.call(zoomBehavior).call(zoomBehavior.transform, currentTransform)

  const nodes = model.nodes.map((item, index) => {
    const prior = positions.get(item.id)
    const angle = index * 0.9
    return {
      ...item,
      x: prior?.x ?? width / 2 + Math.cos(angle) * Math.min(180, index * 20),
      y: prior?.y ?? height / 2 + Math.sin(angle) * Math.min(150, index * 16),
    }
  })
  const edges = model.edges.map((item) => ({ ...item }))

  const linkLayer = stage.append('g')
    .attr('class', 'trace-links')
  const link = linkLayer
    .selectAll('line')
    .data(edges)
    .join('line')
    .attr('class', (item) => item.active ? 'trace-link active' : 'trace-link')
    .attr('marker-end', 'url(#trace-arrow)')
  const linkLabel = linkLayer
    .selectAll('text')
    .data(edges)
    .join('text')
    .attr('class', 'trace-link-label')
    .attr('text-anchor', 'middle')
    .text((item) => item.label)

  const node = stage.append('g')
    .attr('class', 'trace-nodes')
    .selectAll('g')
    .data(nodes, (item) => item.id)
    .join('g')
    .attr('class', (item) => `trace-node ${item.state} kind-${item.kind}`)
    .attr('role', 'button')
    .attr('tabindex', 0)
    .style('cursor', 'pointer')
    .style('opacity', 0)
    .on('click', (event, item) => {
      event.stopPropagation()
      selectedStepId.value = item.id
    })
    .call(
      d3.drag()
        .on('start', (event, item) => {
          if (!event.active) simulation.alphaTarget(0.28).restart()
          item.fx = item.x
          item.fy = item.y
        })
        .on('drag', (event, item) => {
          item.fx = event.x
          item.fy = event.y
        })
        .on('end', (event, item) => {
          if (!event.active) simulation.alphaTarget(0)
          item.fx = null
          item.fy = null
        }),
    )

  node.append('circle')
    .attr('class', 'node-halo')
    .attr('r', 24)
    .attr('fill', 'none')
    .attr('stroke', (item) => colorFor(item.kind))

  node.append('circle')
    .attr('class', 'node-core')
    .attr('r', 18)
    .attr('fill', (item) => colorFor(item.kind))
    .attr('stroke', '#ffffff')
    .attr('stroke-width', 3)

  node.append('text')
    .attr('class', 'node-glyph')
    .attr('text-anchor', 'middle')
    .attr('dy', '0.35em')
    .text((item) => glyphFor(item.kind))

  node.append('text')
    .attr('class', 'node-title')
    .attr('text-anchor', 'middle')
    .attr('y', 32)
    .text((item) => truncate(item.title, 13))

  node.append('text')
    .attr('class', 'node-state-label')
    .attr('text-anchor', 'middle')
    .attr('y', 44)
    .text((item) => stateLabel(item.state))

  node.append('title')
    .text((item) => `${item.title}\n${item.summary}`)

  node.transition().duration(360).style('opacity', 1)
  svg.on('click', () => { selectedStepId.value = '' })

  simulation = d3.forceSimulation(nodes)
    .force(
      'link',
      d3.forceLink(edges)
        .id((item) => item.id)
        .distance((item) => item.active ? 128 : 108)
        .strength(0.75),
    )
    .force('charge', d3.forceManyBody().strength(-330))
    .force('collide', d3.forceCollide(44))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('x', d3.forceX(width / 2).strength(0.025))
    .force('y', d3.forceY(height / 2).strength(0.035))
    .on('tick', () => {
      link
        .attr('x1', (item) => item.source.x)
        .attr('y1', (item) => item.source.y)
        .attr('x2', (item) => item.target.x)
        .attr('y2', (item) => item.target.y)
      linkLabel
        .attr('x', (item) => (item.source.x + item.target.x) / 2)
        .attr('y', (item) => (item.source.y + item.target.y) / 2 - 5)
      node.attr('transform', (item) => `translate(${item.x},${item.y})`)
      nodes.forEach((item) => positions.set(item.id, { x: item.x, y: item.y }))
    })

  const grew = nodes.length > renderedNodeCount
  renderedNodeCount = nodes.length
  if (autoFollow.value && grew) {
    window.setTimeout(() => fitGraph(), 520)
  }
}

function fitGraph() {
  const element = svgRef.value
  const container = containerRef.value
  if (!element || !container || !zoomBehavior) return
  // Use simulation coordinates rather than SVG getBBox(). In Chromium,
  // getBBox() on a zoomed nested <g> can be reported in the transformed
  // coordinate space and compound the previous auto-fit translation.
  const coordinates = graph.value.nodes
    .map((item) => positions.get(item.id))
    .filter(Boolean)
  if (!coordinates.length) return
  const padding = 58
  const minX = Math.min(...coordinates.map((item) => item.x)) - padding
  const maxX = Math.max(...coordinates.map((item) => item.x)) + padding
  const minY = Math.min(...coordinates.map((item) => item.y)) - padding
  const maxY = Math.max(...coordinates.map((item) => item.y)) + padding
  const bounds = {
    x: minX,
    y: minY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  }
  const width = Math.max(container.clientWidth, 560)
  const height = Math.max(container.clientHeight, 520)
  const scale = Math.min(
    1,
    0.88 / Math.max(bounds.width / width, bounds.height / height),
  )
  const translateX = width / 2 - scale * (bounds.x + bounds.width / 2)
  const translateY = height / 2 - scale * (bounds.y + bounds.height / 2)
  currentTransform = d3.zoomIdentity.translate(translateX, translateY).scale(scale)
  d3.select(element)
    .transition()
    .duration(420)
    .call(zoomBehavior.transform, currentTransform)
}

function edgeLabel(source, target) {
  if (!target) return ''
  if (target.kind === 'llm_input') {
    return source?.kind === 'loop' ? '携带观察' : '组装输入'
  }
  if (target.kind === 'react') return '发送 LLM'
  if (target.kind === 'plan') return '规划动作'
  if (target.kind === 'loop') {
    return target.details?.continue_react === false ? '停止循环' : '继续循环'
  }
  if (target.kind === 'guard') return '安全校验'
  if (target.kind === 'tool') return '调用'
  if (target.kind === 'observation') return '结果返回'
  if (target.kind === 'synthesis') return '整理结论'
  if (target.kind === 'policy') return '规则评估'
  if (target.kind === 'gate') return '证据校验'
  if (target.kind === 'human') return '交给人工'
  if (target.kind === 'repair') return '定向修复'
  return source?.kind === 'start' ? '开始' : '进入'
}

function detailRows(step) {
  const details = step?.details || {}
  const rows = []
  const mappings = [
    ['tool_name', 'Tool'],
    ['llm_input', '发送给 LLM'],
    ['planned_actions', '本轮行动计划'],
    ['decision', '循环决策'],
    ['continue_react', '是否继续 ReAct'],
    ['next_action', '下一步动作'],
    ['next_iteration', '下一轮'],
    ['message_count', '历史消息'],
    ['observation_count', 'Observation'],
    ['available_tools', '可用工具'],
    ['requested_tools', '请求工具'],
    ['checks', '授权检查'],
    ['input', '输入'],
    ['result_status', '结果状态'],
    ['result_count', '结果数量'],
    ['evidence_ids', '证据编号'],
    ['decision', '策略结论'],
    ['score', '立项评分'],
    ['coverage', '信息覆盖'],
    ['gate_status', '证据门'],
    ['issue_count', '问题数量'],
    ['issue_codes', '问题代码'],
    ['action', '人工动作'],
    ['returns_to_llm', '返回 LLM'],
    ['reasoning_visibility', '研判可见性'],
    ['termination_reason', '终止原因'],
    ['error', '错误'],
  ]
  mappings.forEach(([key, label]) => {
    const value = details[key]
    if (value === undefined || value === null || value === '') return
    rows.push({ label, value: displayValue(value) })
  })
  return rows
}

function displayValue(value) {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) {
    return value.some((item) => typeof item === 'object')
      ? JSON.stringify(value, null, 2)
      : value.join('、') || '-'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value, null, 2)
  }
  return String(value)
}

function normalizeRunState(status) {
  if (status === 'failed' || status === 'blocked_stale_manifest') return 'failed'
  if (status === 'waiting_human') return 'waiting'
  if (status === 'queued' || status === 'resume_queued' || status === 'running') return 'running'
  return 'completed'
}

function lifecycleTitle(value) {
  return {
    run_claimed: 'Worker 领取任务',
    run_recovered: '从 Checkpoint 恢复',
    human_review_paused: '等待人工审核',
    human_decision_queued: '人工决定已提交',
    run_completed: '运行完成',
    run_cancelled: '运行已终止',
    run_failed: '运行失败',
    run_retry_queued: '重新进入队列',
    run_blocked_stale_manifest: '资料版本阻断',
  }[value] || value || '运行事件'
}

function kindLabel(kind) {
  return {
    start: '运行入口',
    preparation: '上下文准备',
    llm_input: 'LLM 输入',
    react: 'ReAct 研判',
    plan: '行动计划',
    loop: '循环判断',
    guard: '确定性控制',
    tool: 'Tool 调用',
    observation: '工具观察',
    synthesis: '结果整理',
    policy: '立项标准',
    gate: '证据门',
    repair: '定向修复',
    human: '人工节点',
    control: '运行控制',
    lifecycle: '生命周期',
    error: '运行异常',
  }[kind] || kind
}

function stateLabel(state) {
  return {
    running: '运行中',
    waiting: '等待中',
    completed: '已完成',
    failed: '失败',
  }[state] || state || '-'
}

function colorFor(kind) {
  return colors[kind] || colors.control
}

function glyphFor(kind) {
  return {
    start: '▶',
    preparation: 'P',
    llm_input: 'I',
    react: 'R',
    plan: '↗',
    loop: '↻',
    guard: '✓',
    tool: 'T',
    observation: 'O',
    synthesis: 'S',
    policy: '§',
    gate: '◇',
    repair: '↻',
    human: 'H',
    error: '!',
  }[kind] || '·'
}

function durationText(value) {
  const milliseconds = Number(value)
  if (!Number.isFinite(milliseconds)) return '-'
  if (milliseconds < 1000) return `${milliseconds} ms`
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)} s`
}

function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString('zh-CN', { hour12: false })
}

function truncate(value, limit) {
  const text = String(value || '')
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}
</script>

<style scoped>
.agent-trace-card {
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 20px;
  background:
    radial-gradient(circle at 18% 0%, rgba(124, 58, 237, 0.08), transparent 30%),
    #fff;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.07);
}

.trace-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 20px 22px 16px;
  border-bottom: 1px solid #edf1f7;
}

.trace-eyebrow {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #64748b;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #94a3b8;
}

.live-dot.active {
  background: #22c55e;
  box-shadow: 0 0 0 5px rgba(34, 197, 94, 0.12);
  animation: live-pulse 1.8s ease-in-out infinite;
}

.trace-header h3 {
  margin: 7px 0 4px;
  color: #111827;
  font-size: 20px;
}

.trace-header p {
  margin: 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.55;
}

.trace-header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.trace-stats {
  display: flex;
  gap: 12px;
  margin-right: 4px;
  color: #64748b;
  font-size: 12px;
}

.trace-stats strong {
  color: #111827;
  font-variant-numeric: tabular-nums;
}

.trace-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  min-height: 560px;
}

.trace-canvas {
  position: relative;
  min-width: 0;
  min-height: 560px;
  overflow: hidden;
  border-right: 1px solid #edf1f7;
  background-color: #f8fafc;
  background-image: radial-gradient(rgba(100, 116, 139, 0.22) 1px, transparent 1px);
  background-size: 22px 22px;
}

.trace-canvas svg {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 560px;
}

.trace-legend {
  position: absolute;
  bottom: 14px;
  left: 14px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  max-width: calc(100% - 28px);
  padding: 8px 10px;
  border: 1px solid rgba(203, 213, 225, 0.8);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(10px);
  color: #64748b;
  font-size: 10px;
  pointer-events: none;
}

.trace-legend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.trace-legend i,
.step-kind-dot,
.recent-step > i {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.trace-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  color: #94a3b8;
  font-size: 13px;
  transform: translate(-50%, -50%);
}

.trace-inspector {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 18px;
  background: rgba(255, 255, 255, 0.94);
}

.inspector-heading {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
}

.inspector-heading > div {
  display: grid;
  gap: 2px;
}

.inspector-heading small,
.recent-heading small {
  color: #94a3b8;
  font-size: 10px;
}

.inspector-heading strong {
  color: #111827;
  font-size: 14px;
}

.step-state {
  padding: 3px 7px;
  border-radius: 999px;
  background: #eef2f7;
  color: #64748b;
  font-size: 10px;
  font-style: normal;
  white-space: nowrap;
}

.step-state.running {
  background: #ede9fe;
  color: #6d28d9;
}

.step-state.waiting {
  background: #fef3c7;
  color: #a16207;
}

.step-state.failed {
  background: #fee2e2;
  color: #b91c1c;
}

.step-state.completed {
  background: #dcfce7;
  color: #047857;
}

.step-summary {
  min-height: 42px;
  margin: 0;
  color: #475569;
  font-size: 13px;
  line-height: 1.65;
}

.step-meta {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 12px;
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f8fafc;
  font-size: 11px;
}

.step-meta dt {
  color: #94a3b8;
}

.step-meta dd {
  margin: 0;
  color: #334155;
  text-align: right;
}

.step-details {
  display: grid;
  gap: 7px;
  max-height: 190px;
  overflow: auto;
}

.detail-row {
  display: grid;
  gap: 4px;
}

.detail-row span {
  color: #94a3b8;
  font-size: 10px;
}

.detail-row code {
  overflow: auto;
  max-height: 90px;
  padding: 7px 8px;
  border: 1px solid #e7ebf1;
  border-radius: 7px;
  background: #fbfcfe;
  color: #334155;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 10px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}

.recent-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid #edf1f7;
}

.recent-heading strong {
  color: #334155;
  font-size: 12px;
}

.recent-step {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  align-items: start;
  gap: 8px;
  width: 100%;
  padding: 8px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.recent-step:hover,
.recent-step.active {
  border-color: #dce4ef;
  background: #f8fafc;
}

.recent-step > i {
  margin-top: 4px;
}

.recent-step > span {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.recent-step strong {
  overflow: hidden;
  color: #334155;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recent-step small {
  display: -webkit-box;
  overflow: hidden;
  color: #94a3b8;
  font-size: 10px;
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.recent-step em {
  color: #94a3b8;
  font-size: 9px;
  font-style: normal;
  white-space: nowrap;
}

.recent-step em.running {
  color: #7c3aed;
}

.recent-step em.waiting {
  color: #ca8a04;
}

.recent-step em.failed {
  color: #dc2626;
}

.trace-privacy-note {
  margin: auto 0 0;
  padding-top: 12px;
  border-top: 1px solid #edf1f7;
  color: #94a3b8;
  font-size: 10px;
  line-height: 1.5;
}

:deep(.trace-link) {
  stroke: #aeb9c8;
  stroke-width: 1.6;
  opacity: 0.72;
}

:deep(.trace-link.active) {
  stroke: #7c3aed;
  stroke-width: 2.2;
  stroke-dasharray: 7 5;
  animation: edge-flow 0.9s linear infinite;
}

:deep(.trace-link-label) {
  fill: #7b8798;
  font-family: Inter, "PingFang SC", sans-serif;
  font-size: 8px;
  font-weight: 600;
  paint-order: stroke;
  pointer-events: none;
  stroke: rgba(248, 250, 252, 0.96);
  stroke-width: 4px;
}

:deep(.node-halo) {
  opacity: 0.14;
  stroke-width: 2;
}

:deep(.trace-node.running .node-halo) {
  opacity: 0.68;
  stroke-width: 3;
  animation: node-pulse 1.5s ease-in-out infinite;
}

:deep(.trace-node.waiting .node-halo) {
  opacity: 0.54;
  stroke-dasharray: 4 4;
}

:deep(.trace-node.failed .node-core) {
  fill: #dc2626;
}

:deep(.node-glyph) {
  fill: #fff;
  font-family: Inter, "PingFang SC", sans-serif;
  font-size: 11px;
  font-weight: 800;
  pointer-events: none;
}

:deep(.node-title) {
  fill: #334155;
  font-family: Inter, "PingFang SC", sans-serif;
  font-size: 9px;
  font-weight: 650;
  paint-order: stroke;
  pointer-events: none;
  stroke: #f8fafc;
  stroke-width: 4px;
}

:deep(.node-state-label) {
  fill: #94a3b8;
  font-family: Inter, "PingFang SC", sans-serif;
  font-size: 8px;
  paint-order: stroke;
  pointer-events: none;
  stroke: #f8fafc;
  stroke-width: 3px;
}

@keyframes live-pulse {
  50% { opacity: 0.45; }
}

@keyframes node-pulse {
  0%, 100% { stroke-width: 2; transform: scale(0.96); }
  50% { stroke-width: 4; transform: scale(1.08); }
}

@keyframes edge-flow {
  to { stroke-dashoffset: -12; }
}

@media (max-width: 1180px) {
  .trace-workspace {
    grid-template-columns: 1fr;
  }

  .trace-canvas {
    border-right: 0;
    border-bottom: 1px solid #edf1f7;
  }
}

@media (max-width: 720px) {
  .trace-header {
    align-items: stretch;
    flex-direction: column;
  }

  .trace-header-actions {
    justify-content: flex-start;
  }

  .trace-workspace,
  .trace-canvas,
  .trace-canvas svg {
    min-height: 480px;
  }
}
</style>
