<template>
  <div class="pure-agent-page">
    <header class="pure-agent-hero">
      <div>
        <p class="pure-agent-eyebrow">Bid Assessment · Pure Agent</p>
        <h2>投标机会研判 Agent</h2>
        <p>直接提问或引用招标资料。Agent 会根据问题自主决定理解、检索、规划、澄清与回答方式。</p>
      </div>
      <div class="pure-agent-hero-actions">
        <span class="pure-agent-mode">开放对话</span>
        <el-button plain @click="startNewConversation">新对话</el-button>
        <el-button
          :loading="loadingConversation"
          :disabled="!conversationRef"
          plain
          @click="refreshConversation"
        >
          刷新
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="pageError"
      class="pure-agent-alert"
      type="error"
      show-icon
      :closable="true"
      :title="pageError"
      @close="pageError = ''"
    />

    <el-alert
      v-if="!runtimeReady"
      class="pure-agent-alert"
      :type="runtimeAlertType"
      show-icon
      :closable="false"
      :title="runtimeAlertTitle"
      :description="runtimeAlertDescription"
    />

    <main class="pure-agent-layout">
      <section class="pure-agent-chat-card">
        <header class="conversation-header">
          <div>
            <strong>{{ conversation?.title || '新的开放对话' }}</strong>
            <small v-if="conversationRef">对话 {{ compactRef(conversationRef) }}</small>
            <small v-else>无需先填写固定表单</small>
          </div>
          <div class="conversation-meta">
            <el-tag v-if="conversation?.assessment_ref" effect="plain" type="info">
              已绑定研判项目 {{ compactRef(conversation.assessment_ref) }}
            </el-tag>
            <el-tag v-if="activeTask" :type="taskStatusTagType(activeTask.status)" effect="light">
              {{ taskStatusLabel(activeTask.status) }}
            </el-tag>
          </div>
        </header>

        <div ref="transcriptEl" class="conversation-transcript" aria-live="polite">
          <div v-if="loadingConversation && !messages.length" class="conversation-empty">
            <span class="agent-orbit" aria-hidden="true"></span>
            <strong>正在读取对话</strong>
            <p>只读取已发布消息和安全事件。</p>
          </div>

          <div v-else-if="!messages.length" class="conversation-empty">
            <span class="agent-orbit" aria-hidden="true"></span>
            <strong>从一个真实问题开始</strong>
            <p>例如：这个项目最关键的投标风险是什么？还缺哪些信息才能判断？</p>
          </div>

          <article
            v-for="message in messages"
            :key="message.message_ref"
            :class="['conversation-message', `is-${message.role}`]"
          >
            <div class="message-avatar" aria-hidden="true">
              {{ message.role === 'assistant' ? 'A' : '你' }}
            </div>
            <div class="message-body">
              <header>
                <strong>{{ message.role === 'assistant' ? '研判 Agent' : '你' }}</strong>
                <time>{{ formatTime(message.created_at) }}</time>
              </header>

              <template v-if="isAnswerMessage(message)">
                <div class="answer-blocks">
                  <section
                    v-for="block in message.content.blocks || []"
                    :key="block.block_ref"
                    :class="['answer-block', `is-${block.block_type}`]"
                  >
                    <span v-if="block.block_type === 'limitation'" class="answer-block-label">限制与未知</span>
                    <span v-else-if="block.block_type === 'interaction'" class="answer-block-label">建议下一步</span>
                    <p>{{ block.text }}</p>
                    <div v-if="block.citation_refs?.length" class="answer-markers">
                      <span
                        v-for="citationRef in block.citation_refs"
                        :key="citationRef"
                      >
                        {{ citationMarker(message.content, citationRef) }}
                      </span>
                    </div>
                  </section>
                  <p v-if="!(message.content.blocks || []).length" class="message-text">
                    {{ message.content.text }}
                  </p>
                </div>
                <details v-if="message.content.citations?.length" class="citation-panel">
                  <summary>查看 {{ message.content.citations.length }} 条依据</summary>
                  <ol>
                    <li
                      v-for="citation in message.content.citations"
                      :key="citation.citation_ref"
                    >
                      <span>{{ citation.marker }}</span>
                      <p>{{ citation.text }}</p>
                      <small v-if="citation.controlled_access_ref">受控来源，可按权限继续查看</small>
                    </li>
                  </ol>
                </details>
              </template>

              <template v-else-if="isSlotMessage(message)">
                <p class="message-text">{{ displayCandidate(message.content.candidate) }}</p>
                <small class="message-note">补充信息 · {{ compactRef(message.content.slot_ref) }}</small>
              </template>

              <template v-else>
                <p v-if="message.content.text" class="message-text">{{ message.content.text }}</p>
                <div v-if="message.content.resources?.length" class="message-resources">
                  <span
                    v-for="resource in message.content.resources"
                    :key="`${resource.kind}:${resource.ref}`"
                  >
                    {{ resourceKindLabel(resource.kind) }} · {{ compactRef(resource.ref) }}
                  </span>
                </div>
              </template>
            </div>
          </article>

          <section v-if="pendingSlot" class="pending-input-card">
            <div class="pending-icon" aria-hidden="true">?</div>
            <div class="pending-content">
              <span class="pending-kicker">Agent 需要补充信息</span>
              <strong>{{ pendingSlot.request_message }}</strong>
              <div v-if="pendingSlot.issues?.length" class="slot-issues">
                <article v-for="issue in pendingSlot.issues" :key="`${issue.code}:${issue.field || ''}`">
                  <b>{{ issue.message }}</b>
                  <p>{{ issue.guidance }}</p>
                </article>
              </div>
              <el-input
                v-model="slotCandidateText"
                type="textarea"
                :rows="3"
                resize="vertical"
                placeholder="输入补充内容；如提示需要多个字段，也可以输入 JSON 对象"
                @keydown.ctrl.enter.prevent="submitSlotInput"
                @keydown.meta.enter.prevent="submitSlotInput"
              />
              <div class="pending-actions">
                <small>提交后将从原挂起位置继续，不会新建 Task。</small>
                <el-button
                  type="primary"
                  :loading="submittingSlot"
                  :disabled="!runtimeReady || !slotCandidateText.trim()"
                  @click="submitSlotInput"
                >
                  提交并继续
                </el-button>
              </div>
            </div>
          </section>
        </div>

        <footer class="conversation-composer">
          <div v-if="resourceReferences.length" class="composer-resources">
            <span
              v-for="resource in resourceReferences"
              :key="`${resource.kind}:${resource.ref}`"
            >
              {{ resourceKindLabel(resource.kind) }} · {{ compactRef(resource.ref) }}
              <button type="button" aria-label="移除资料引用" @click="removeResource(resource)">×</button>
            </span>
          </div>

          <el-input
            v-model="composerText"
            class="composer-input"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 9 }"
            resize="none"
            maxlength="131072"
            placeholder="输入问题、判断要求或补充说明……"
            @keydown.ctrl.enter.prevent="submitMessage"
            @keydown.meta.enter.prevent="submitMessage"
          />

          <div class="composer-toolbar">
            <div class="resource-control">
              <el-popover placement="top-start" :width="390" trigger="click">
                <template #reference>
                  <el-button text>＋ 引用资料</el-button>
                </template>
                <div class="resource-popover">
                  <strong>添加受控资料引用</strong>
                  <p>这里只填写系统内引用，不接受本地路径、对象键或外部 URL。</p>
                  <el-select v-model="resourceDraft.kind" aria-label="资料类型">
                    <el-option label="研判项目" value="assessment" />
                    <el-option label="招标文档版本" value="bid_document_version" />
                  </el-select>
                  <el-input v-model="resourceDraft.ref" placeholder="输入系统引用 ID" @keyup.enter="addResource" />
                  <el-button type="primary" plain @click="addResource">添加</el-button>
                </div>
              </el-popover>
              <small>Ctrl / ⌘ + Enter 发送</small>
            </div>
            <el-button
              class="send-button"
              type="primary"
              :loading="submittingMessage"
              :disabled="!canSubmitMessage"
              @click="submitMessage"
            >
              {{ activeTask && !isTerminalStatus(activeTask.status) ? '发送补充' : '发送' }}
            </el-button>
          </div>
        </footer>
      </section>

      <aside class="pure-agent-side">
        <section class="agent-state-card">
          <header>
            <div>
              <small>当前 Task</small>
              <strong>{{ activeTask ? taskStatusLabel(activeTask.status) : '尚未开始' }}</strong>
            </div>
            <span :class="['stream-indicator', `is-${streamState}`]">
              {{ streamStateLabel }}
            </span>
          </header>

          <template v-if="activeTask">
            <dl>
              <div>
                <dt>执行方式</dt>
                <dd>{{ executionModeLabel(activeTask.execution_mode) }}</dd>
              </div>
              <div>
                <dt>状态版本</dt>
                <dd>v{{ activeTask.state_version }}</dd>
              </div>
              <div>
                <dt>Task</dt>
                <dd>{{ compactRef(activeTask.task_ref) }}</dd>
              </div>
            </dl>
            <el-alert
              v-if="activeTask.dispatch_status === 'disabled' && activeTask.status === 'running'"
              type="info"
              :closable="false"
              title="Task 已受理，本地 Runtime 当前关闭"
              description="Conversation API 可用，但独立执行开关、Continuation Secret 或本地 Controller 装配尚未全部启用。"
            />
            <el-button
              v-if="!isTerminalStatus(activeTask.status)"
              class="cancel-task-button"
              type="danger"
              plain
              :loading="cancellingTask"
              @click="cancelTask"
            >
              取消当前 Task
            </el-button>
          </template>
          <p v-else class="side-empty">发送消息后，页面会显示安全的实时状态。</p>
        </section>

        <section class="agent-plan-card">
          <header>
            <div>
              <small>动态计划</small>
              <strong>{{ planProjection ? `版本 ${planProjection.plan_version}` : '按需生成' }}</strong>
            </div>
            <el-tag v-if="planProjection?.revised" type="warning" effect="plain">已调整</el-tag>
          </header>
          <template v-if="planProjection">
            <p>{{ planProjection.summary }}</p>
            <ol class="plan-steps">
              <li v-for="step in planProjection.steps" :key="step.step_id">
                <span>{{ step.step_id }}</span>
                <strong>{{ step.title }}</strong>
              </li>
            </ol>
          </template>
          <p v-else class="side-empty">简单问题可直接回答；达到复杂度条件时才展示计划。</p>
        </section>

        <section class="agent-progress-card">
          <header>
            <div>
              <small>安全进度</small>
              <strong>只展示用户可见事件</strong>
            </div>
          </header>
          <div v-if="progressItems.length" class="progress-list">
            <article v-for="item in progressItems" :key="item.event_id">
              <i :class="{ terminal: item.terminal }"></i>
              <div>
                <strong>{{ item.message }}</strong>
                <time>{{ formatTime(item.occurred_at) }}</time>
              </div>
            </article>
          </div>
          <p v-else class="side-empty">不会展示思维链、Prompt、工具参数、内部异常或内部控制数据。</p>
        </section>
      </aside>
    </main>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  bidAssessmentPureAgentApi,
  createPureAgentIdempotencyKey,
  eventCursor,
  pureAgentErrorMessage,
  rememberEventCursor,
  responseData,
  streamPureAgentTaskEvents,
} from './bidAssessmentPureAgentApi'

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])
const REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/

const conversation = ref(null)
const messages = ref([])
const activeTask = ref(null)
const planProjection = ref(null)
const progressItems = ref([])
const pendingSlot = ref(null)
const pageError = ref('')
const runtimeStatus = ref(null)
const runtimeStatusLoading = ref(true)
const loadingConversation = ref(false)
const submittingMessage = ref(false)
const submittingSlot = ref(false)
const cancellingTask = ref(false)
const composerText = ref('')
const slotCandidateText = ref('')
const resourceReferences = ref([])
const transcriptEl = ref(null)
const streamState = ref('idle')
const resourceDraft = reactive({ kind: 'assessment', ref: '' })
const seenEventIds = new Set()

let stopStream = null
let reconnectTimer = null
let disposed = false
let trackedTaskRef = ''
let reconnectAttempts = 0

const initialParams = new URLSearchParams(window.location.search)
const assessmentSeed = ref(initialParams.get('assessment') || '')

const conversationRef = computed(() => conversation.value?.conversation_ref || '')
const runtimeReady = computed(() => runtimeStatus.value?.runtime_available === true)
const runtimeAlertType = computed(() => (
  runtimeStatusLoading.value ? 'info' : 'warning'
))
const runtimeAlertTitle = computed(() => {
  if (runtimeStatusLoading.value) return '正在确认本地 Runtime 状态'
  if (runtimeStatus.value?.startup_status === 'preflight_blocked') return '本地环境 Preflight 未通过'
  return '本地 Runtime 尚未就绪'
})
const runtimeAlertDescription = computed(() => {
  if (runtimeStatusLoading.value) return '状态确认完成前不会提交新的 Agent Task。'
  if (runtimeStatus.value?.startup_status === 'preflight_blocked') {
    const codes = runtimeStatus.value?.reason_codes || []
    return codes.length
      ? `请先修复启动检查：${codes.join('、')}`
      : '请先运行本地 Preflight 并修复未通过项。'
  }
  return '请通过 Pure Agent 专用本地启动入口显式完成 Preflight 与 Runtime 装配。'
})
const canSubmitMessage = computed(() => (
  runtimeReady.value
  && !submittingMessage.value
  && (composerText.value.trim().length > 0 || resourceReferences.value.length > 0)
))
const streamStateLabel = computed(() => ({
  idle: '未连接',
  connecting: '连接中',
  live: '实时',
  retrying: '重连中',
  closed: '已结束',
  error: '连接中断',
}[streamState.value] || '未连接'))

function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(String(status || ''))
}

function taskStatusLabel(status) {
  return {
    running: '运行中',
    pending: '等待补充',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[status] || '未知状态'
}

function taskStatusTagType(status) {
  return {
    running: 'primary',
    pending: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info',
  }[status] || 'info'
}

function executionModeLabel(mode) {
  return mode === 'planned' ? '按需规划' : mode === 'direct' ? '直接处理' : '待判断'
}

function compactRef(value) {
  const text = String(value || '')
  if (text.length <= 18) return text || '-'
  return `${text.slice(0, 8)}…${text.slice(-6)}`
}

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function resourceKindLabel(kind) {
  return kind === 'assessment' ? '研判项目' : '文档版本'
}

function isAnswerMessage(message) {
  return message?.content?.schema_name === 'bid.answer.message.v1'
}

function isSlotMessage(message) {
  return message?.content?.schema_name === 'bid.slot-input.message.v1'
}

function displayCandidate(candidate) {
  if (typeof candidate === 'string') return candidate
  try { return JSON.stringify(candidate, null, 2) } catch (_error) { return String(candidate ?? '') }
}

function citationMarker(content, citationRef) {
  return content?.citations?.find((item) => item.citation_ref === citationRef)?.marker || '[依据]'
}

function setConversationUrl(refValue) {
  const url = new URL(window.location.href)
  if (refValue) url.searchParams.set('conversation', refValue)
  else url.searchParams.delete('conversation')
  if (assessmentSeed.value) url.searchParams.set('assessment', assessmentSeed.value)
  else url.searchParams.delete('assessment')
  window.history.replaceState({}, '', `${url.pathname}${url.search}`)
}

function scrollTranscript() {
  nextTick(() => {
    if (transcriptEl.value) transcriptEl.value.scrollTop = transcriptEl.value.scrollHeight
  })
}

function appendMessage(message) {
  if (!message?.message_ref) return
  const index = messages.value.findIndex((item) => item.message_ref === message.message_ref)
  if (index >= 0) messages.value[index] = message
  else messages.value.push(message)
  messages.value.sort((left, right) => left.sequence - right.sequence)
  scrollTranscript()
}

async function loadMessages() {
  if (!conversationRef.value) return
  const loaded = []
  let afterSequence = 0
  for (let pageCount = 0; pageCount < 20; pageCount += 1) {
    const page = responseData(await bidAssessmentPureAgentApi.messages(
      conversationRef.value,
      afterSequence,
      100,
    ))
    loaded.push(...(page?.items || []))
    if (!page?.has_more || page.next_after_sequence <= afterSequence) break
    afterSequence = page.next_after_sequence
  }
  messages.value = loaded
  scrollTranscript()
}

function safeEventMessage(event) {
  const payload = event?.payload || {}
  if (payload.kind === 'task_started') return payload.message
  if (payload.kind === 'progress') return payload.message
  if (payload.kind === 'input_request') return '等待你补充必要信息'
  if (payload.kind === 'input_validation') return payload.message
  if (payload.kind === 'answer') return '回答已完成'
  if (payload.kind === 'terminal') return payload.message
  if (payload.kind === 'plan_projection') return payload.revised ? '计划已调整' : '已形成处理计划'
  return '任务状态已更新'
}

function applySafeEvent(event) {
  if (!event?.event_id || seenEventIds.has(event.event_id)) return
  seenEventIds.add(event.event_id)
  rememberEventCursor(event.task_ref, event.event_id)
  if (activeTask.value?.task_ref === event.task_ref) {
    activeTask.value = {
      ...activeTask.value,
      status: event.status,
      state_version: event.state_version,
    }
  }
  const payload = event.payload || {}
  if (payload.kind === 'plan_projection') planProjection.value = payload
  if (payload.kind === 'input_request') {
    pendingSlot.value = {
      slot_ref: payload.slot_ref,
      phase: payload.phase,
      request_message: payload.request_message,
      issues: [],
    }
    if (activeTask.value) activeTask.value.pending = pendingSlot.value
  }
  if (payload.kind === 'input_validation' && pendingSlot.value?.slot_ref === payload.slot_ref) {
    pendingSlot.value = { ...pendingSlot.value, issues: payload.issues || [] }
    if (payload.result === 'accepted') {
      pendingSlot.value = null
      if (activeTask.value) activeTask.value.pending = null
    }
  }
  progressItems.value = [
    ...progressItems.value,
    {
      event_id: event.event_id,
      occurred_at: event.occurred_at,
      message: safeEventMessage(event),
      terminal: Boolean(event.terminal),
    },
  ].slice(-30)
  if (payload.kind === 'answer') loadMessages().catch(() => {})
  if (event.terminal) {
    streamState.value = 'closed'
    closeEventStream()
  }
}

async function hydrateEvents(taskView) {
  let afterVersion = 0
  for (let pageCount = 0; pageCount < 20; pageCount += 1) {
    const page = responseData(await bidAssessmentPureAgentApi.events(
      conversationRef.value,
      taskView.task_ref,
      afterVersion,
      100,
    ))
    ;(page?.events || []).forEach(applySafeEvent)
    if (!page?.has_more || page.next_after_version <= afterVersion) break
    afterVersion = page.next_after_version
  }
}

function closeEventStream() {
  if (reconnectTimer) window.clearTimeout(reconnectTimer)
  reconnectTimer = null
  if (stopStream) stopStream()
  stopStream = null
}

function scheduleReconnect(taskRef) {
  if (disposed || activeTask.value?.task_ref !== taskRef || isTerminalStatus(activeTask.value?.status)) return
  if (reconnectAttempts >= 5) {
    streamState.value = 'error'
    return
  }
  streamState.value = 'retrying'
  reconnectAttempts += 1
  if (reconnectTimer) window.clearTimeout(reconnectTimer)
  const delay = Math.min(800 * (2 ** (reconnectAttempts - 1)), 8000)
  reconnectTimer = window.setTimeout(() => connectEventStream(taskRef), delay)
}

function connectEventStream(taskRef) {
  closeEventStream()
  if (!taskRef || disposed || isTerminalStatus(activeTask.value?.status)) {
    streamState.value = isTerminalStatus(activeTask.value?.status) ? 'closed' : 'idle'
    return
  }
  streamState.value = 'connecting'
  stopStream = streamPureAgentTaskEvents(conversationRef.value, taskRef, {
    lastEventId: eventCursor(taskRef),
    onOpen: () => {
      reconnectAttempts = 0
      streamState.value = 'live'
    },
    onEvent: ({ data }) => applySafeEvent(data),
    onClosed: () => scheduleReconnect(taskRef),
    onError: (error) => {
      streamState.value = 'error'
      if ([401, 403, 404].includes(Number(error?.status))) return
      scheduleReconnect(taskRef)
    },
  })
}

async function trackTask(taskView) {
  if (!taskView?.task_ref) {
    activeTask.value = null
    pendingSlot.value = null
    planProjection.value = null
    progressItems.value = []
    trackedTaskRef = ''
    closeEventStream()
    streamState.value = 'idle'
    return
  }
  const changedTask = trackedTaskRef !== taskView.task_ref
  trackedTaskRef = taskView.task_ref
  activeTask.value = taskView
  pendingSlot.value = taskView.pending || null
  if (changedTask) {
    planProjection.value = null
    progressItems.value = []
    seenEventIds.clear()
    reconnectAttempts = 0
  }
  await hydrateEvents(taskView)
  connectEventStream(taskView.task_ref)
}

async function loadConversation(refValue) {
  loadingConversation.value = true
  pageError.value = ''
  try {
    const snapshot = responseData(await bidAssessmentPureAgentApi.conversation(refValue))
    conversation.value = snapshot
    assessmentSeed.value = snapshot.assessment_ref || assessmentSeed.value
    setConversationUrl(snapshot.conversation_ref)
    await loadMessages()
    await trackTask(snapshot.active_task || snapshot.latest_task)
  } catch (error) {
    pageError.value = pureAgentErrorMessage(error, '无法读取该对话')
  } finally {
    loadingConversation.value = false
  }
}

async function refreshConversation() {
  if (conversationRef.value) await loadConversation(conversationRef.value)
}

async function ensureConversation() {
  if (conversation.value) return conversation.value
  const firstLine = composerText.value.trim().split(/\r?\n/)[0]
  const body = {
    ...(assessmentSeed.value ? { assessment_ref: assessmentSeed.value } : {}),
    ...(firstLine ? { title: firstLine.slice(0, 80) } : {}),
  }
  const created = responseData(await bidAssessmentPureAgentApi.createConversation(
    body,
    createPureAgentIdempotencyKey('conversation'),
  ))
  conversation.value = created
  setConversationUrl(created.conversation_ref)
  return created
}

async function submitMessage() {
  if (!canSubmitMessage.value) return
  submittingMessage.value = true
  pageError.value = ''
  try {
    const current = await ensureConversation()
    const body = {
      text: composerText.value.trim() || null,
      resources: resourceReferences.value.map((item) => ({ kind: item.kind, ref: item.ref })),
      reply_to_message_ref: null,
    }
    const admission = responseData(await bidAssessmentPureAgentApi.submitMessage(
      current.conversation_ref,
      body,
      createPureAgentIdempotencyKey('message'),
    ))
    appendMessage(admission.message)
    composerText.value = ''
    resourceReferences.value = []
    await trackTask(admission.task)
    ElMessage.success(admission.admission === 'steering_candidate' ? '补充信息已发送' : '问题已提交')
  } catch (error) {
    pageError.value = pureAgentErrorMessage(error, '消息发送失败')
  } finally {
    submittingMessage.value = false
  }
}

function parseSlotCandidate(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  try { return JSON.parse(text) } catch (_error) { return text }
}

async function submitSlotInput() {
  if (!runtimeReady.value || !activeTask.value || !pendingSlot.value || !slotCandidateText.value.trim()) return
  submittingSlot.value = true
  pageError.value = ''
  try {
    const result = responseData(await bidAssessmentPureAgentApi.submitSlot(
      conversationRef.value,
      activeTask.value.task_ref,
      pendingSlot.value.slot_ref,
      {
        expected_state_version: activeTask.value.state_version,
        candidate: parseSlotCandidate(slotCandidateText.value),
      },
      createPureAgentIdempotencyKey('slot'),
    ))
    appendMessage(result.message)
    activeTask.value = result.task
    pendingSlot.value = result.task.pending
      ? { ...result.task.pending, issues: result.issues || result.task.pending.issues || [] }
      : null
    if (result.accepted) {
      slotCandidateText.value = ''
      ElMessage.success('补充信息已通过校验，Task 将从原位置继续')
    } else {
      ElMessage.warning('补充信息未通过校验，请按提示修改')
    }
    await trackTask(result.task)
  } catch (error) {
    pageError.value = pureAgentErrorMessage(error, '补充信息提交失败')
    await refreshConversation()
  } finally {
    submittingSlot.value = false
  }
}

async function cancelTask() {
  if (!activeTask.value || isTerminalStatus(activeTask.value.status)) return
  try {
    await ElMessageBox.confirm(
      '取消后不会继续发起新的 Agent Action，已发布消息仍会保留。',
      '取消当前 Task？',
      { confirmButtonText: '确认取消', cancelButtonText: '继续运行', type: 'warning' },
    )
  } catch (_error) {
    return
  }
  cancellingTask.value = true
  pageError.value = ''
  try {
    const result = responseData(await bidAssessmentPureAgentApi.cancelTask(
      conversationRef.value,
      activeTask.value.task_ref,
      { expected_state_version: activeTask.value.state_version },
      createPureAgentIdempotencyKey('cancel'),
    ))
    activeTask.value = result.task
    pendingSlot.value = null
    closeEventStream()
    streamState.value = 'closed'
    await hydrateEvents(result.task)
    ElMessage.success('当前 Task 已取消')
  } catch (error) {
    pageError.value = pureAgentErrorMessage(error, '取消失败')
    await refreshConversation()
  } finally {
    cancellingTask.value = false
  }
}

function addResource() {
  const refValue = resourceDraft.ref.trim()
  if (!REFERENCE_PATTERN.test(refValue)) {
    ElMessage.warning('请输入有效的系统引用 ID')
    return
  }
  if (resourceDraft.kind === 'assessment') {
    if (conversation.value?.assessment_ref && conversation.value.assessment_ref !== refValue) {
      ElMessage.warning('当前对话已绑定另一研判项目，请新建对话')
      return
    }
    if (conversation.value && !conversation.value.assessment_ref) {
      ElMessage.warning('当前对话未绑定研判项目，请新建对话后再引用')
      return
    }
    assessmentSeed.value = refValue
  }
  if (resourceDraft.kind === 'bid_document_version' && !(conversation.value?.assessment_ref || assessmentSeed.value)) {
    ElMessage.warning('引用文档版本前，需要先绑定研判项目')
    return
  }
  if (resourceReferences.value.some((item) => item.kind === resourceDraft.kind && item.ref === refValue)) {
    ElMessage.info('该资料已经引用')
    return
  }
  resourceReferences.value.push({ kind: resourceDraft.kind, ref: refValue })
  resourceDraft.ref = ''
  setConversationUrl(conversationRef.value)
}

function removeResource(resource) {
  resourceReferences.value = resourceReferences.value.filter(
    (item) => !(item.kind === resource.kind && item.ref === resource.ref),
  )
}

function startNewConversation() {
  closeEventStream()
  conversation.value = null
  messages.value = []
  activeTask.value = null
  pendingSlot.value = null
  planProjection.value = null
  progressItems.value = []
  composerText.value = ''
  slotCandidateText.value = ''
  resourceReferences.value = []
  pageError.value = ''
  trackedTaskRef = ''
  reconnectAttempts = 0
  seenEventIds.clear()
  streamState.value = 'idle'
  setConversationUrl('')
}

async function loadRuntimeStatus() {
  runtimeStatusLoading.value = true
  try {
    runtimeStatus.value = responseData(await bidAssessmentPureAgentApi.runtimeStatus())
  } catch (error) {
    runtimeStatus.value = null
    pageError.value = pureAgentErrorMessage(error, '无法确认本地 Runtime 状态')
  } finally {
    runtimeStatusLoading.value = false
  }
}

onMounted(async () => {
  await loadRuntimeStatus()
  const requestedConversation = initialParams.get('conversation')
  if (requestedConversation) await loadConversation(requestedConversation)
})

onBeforeUnmount(() => {
  disposed = true
  closeEventStream()
})
</script>

<style scoped>
.pure-agent-page {
  display: grid;
  gap: 18px;
  min-height: calc(100vh - 120px);
  padding: 2px;
  color: #172033;
}

.pure-agent-hero {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 26px;
  overflow: hidden;
  border: 1px solid #dbe4ef;
  border-radius: 18px;
  background:
    radial-gradient(circle at 87% 10%, rgba(14, 165, 233, .16), transparent 30%),
    linear-gradient(135deg, #ffffff 0%, #f4faff 62%, #f2fbf8 100%);
  box-shadow: 0 18px 42px rgba(28, 45, 72, .07);
}

.pure-agent-eyebrow {
  margin: 0 0 7px;
  color: #04748e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .13em;
  text-transform: uppercase;
}

.pure-agent-hero h2 { margin: 0; font-size: 26px; letter-spacing: -.02em; }
.pure-agent-hero p:not(.pure-agent-eyebrow) { max-width: 720px; margin: 9px 0 0; color: #667085; line-height: 1.65; }
.pure-agent-hero-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.pure-agent-mode { padding: 7px 11px; border: 1px solid #a5e3d4; border-radius: 999px; color: #0f766e; background: #effcf8; font-size: 12px; font-weight: 750; }
.pure-agent-alert { border-radius: 12px; }

.pure-agent-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 18px;
  min-height: 690px;
}

.pure-agent-chat-card,
.agent-state-card,
.agent-plan-card,
.agent-progress-card {
  border: 1px solid #dfe6ee;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 12px 34px rgba(31, 45, 68, .055);
}

.pure-agent-chat-card { display: grid; grid-template-rows: auto minmax(360px, 1fr) auto; min-width: 0; overflow: hidden; }
.conversation-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 17px 20px; border-bottom: 1px solid #edf0f4; }
.conversation-header > div:first-child { display: grid; gap: 4px; min-width: 0; }
.conversation-header strong { font-size: 15px; }
.conversation-header small { color: #8a94a4; font-size: 11px; }
.conversation-meta { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }

.conversation-transcript { min-height: 0; padding: 22px clamp(16px, 3vw, 38px); overflow-y: auto; background: linear-gradient(#fbfcfe, #fff 24%); }
.conversation-empty { display: grid; justify-items: center; align-content: center; min-height: 330px; text-align: center; }
.conversation-empty strong { margin-top: 17px; font-size: 17px; }
.conversation-empty p { max-width: 440px; margin: 8px 0 0; color: #7a8494; line-height: 1.65; }
.agent-orbit { position: relative; width: 54px; height: 54px; border: 1px solid #9ed7e8; border-radius: 50%; background: radial-gradient(circle, #0d8298 0 14%, #e5f8fb 15% 34%, transparent 35%); }
.agent-orbit::after { content: ''; position: absolute; inset: 8px -8px; border: 1px solid #8dd7c7; border-radius: 50%; transform: rotate(-35deg); }

.conversation-message { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 11px; max-width: 880px; margin: 0 auto 22px; }
.conversation-message.is-user { grid-template-columns: minmax(0, 1fr) 34px; }
.conversation-message.is-user .message-avatar { grid-column: 2; background: #172033; }
.conversation-message.is-user .message-body { grid-column: 1; grid-row: 1; justify-self: end; max-width: min(82%, 720px); background: #eef7ff; border-color: #d5e9fa; }
.message-avatar { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 11px; color: #fff; background: linear-gradient(145deg, #087c94, #159b83); font-size: 12px; font-weight: 800; }
.message-body { min-width: 0; padding: 14px 16px; border: 1px solid #e2e8f0; border-radius: 5px 16px 16px 16px; background: #fff; }
.is-user .message-body { border-radius: 16px 5px 16px 16px; }
.message-body > header { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-bottom: 9px; }
.message-body > header strong { font-size: 12px; }
.message-body time { color: #99a2b0; font-size: 10px; }
.message-text, .answer-block p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #344054; font-size: 14px; line-height: 1.8; }
.message-note { display: block; margin-top: 8px; color: #8b95a5; }
.message-resources, .composer-resources { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.message-resources span, .composer-resources span { padding: 6px 9px; border: 1px solid #d9e7f1; border-radius: 8px; color: #426477; background: #f5faff; font-size: 11px; }

.answer-blocks { display: grid; gap: 12px; }
.answer-block { position: relative; display: grid; gap: 7px; }
.answer-block + .answer-block { padding-top: 12px; border-top: 1px solid #eef1f5; }
.answer-block.is-limitation, .answer-block.is-interaction { padding: 11px 12px; border: 1px solid #f4dfb0; border-radius: 11px; background: #fffaf0; }
.answer-block.is-interaction { border-color: #cce8e1; background: #f3fbf8; }
.answer-block-label { color: #9a6700; font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.answer-block.is-interaction .answer-block-label { color: #0f766e; }
.answer-markers { display: flex; flex-wrap: wrap; gap: 4px; color: #087b93; font-size: 10px; font-weight: 800; }
.citation-panel { margin-top: 13px; padding-top: 11px; border-top: 1px dashed #d9e0e8; }
.citation-panel summary { cursor: pointer; color: #087b93; font-size: 12px; font-weight: 750; }
.citation-panel ol { display: grid; gap: 8px; margin: 11px 0 0; padding: 0; list-style: none; }
.citation-panel li { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px; padding: 9px 10px; border-radius: 9px; background: #f7f9fb; }
.citation-panel li > span { color: #087b93; font-size: 11px; font-weight: 800; }
.citation-panel li p { margin: 0; color: #526071; font-size: 12px; line-height: 1.6; }
.citation-panel li small { grid-column: 2; color: #8b95a5; }

.pending-input-card { display: grid; grid-template-columns: 38px minmax(0, 1fr); gap: 13px; max-width: 880px; margin: 4px auto 24px; padding: 16px; border: 1px solid #f2cb80; border-radius: 15px; background: #fffaf0; }
.pending-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 12px; color: #fff; background: #c57a08; font-weight: 850; }
.pending-content { display: grid; gap: 10px; }
.pending-kicker { color: #a45f00; font-size: 10px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; }
.pending-content > strong { color: #4c3a1f; font-size: 14px; line-height: 1.65; }
.slot-issues { display: grid; gap: 7px; }
.slot-issues article { padding: 9px 10px; border-left: 3px solid #d14343; border-radius: 7px; background: #fff; }
.slot-issues b { color: #a22d2d; font-size: 12px; }
.slot-issues p { margin: 3px 0 0; color: #6e5d50; font-size: 11px; }
.pending-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.pending-actions small { color: #8a6a3c; line-height: 1.5; }

.conversation-composer { padding: 14px 18px 16px; border-top: 1px solid #e9edf2; background: #fff; box-shadow: 0 -12px 30px rgba(32, 44, 65, .035); }
.composer-resources { margin: 0 0 9px; }
.composer-resources span { display: inline-flex; align-items: center; gap: 6px; }
.composer-resources button { padding: 0; border: 0; color: #798595; background: transparent; cursor: pointer; font-size: 16px; line-height: 1; }
.composer-input :deep(.el-textarea__inner) { padding: 13px 14px; border: 0; border-radius: 13px; background: #f7f9fc; box-shadow: inset 0 0 0 1px #dfe6ee; line-height: 1.65; }
.composer-input :deep(.el-textarea__inner:focus) { box-shadow: inset 0 0 0 1px #0a84ff, 0 0 0 3px rgba(10, 132, 255, .1); }
.composer-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 10px; }
.resource-control { display: flex; align-items: center; gap: 8px; }
.resource-control small { color: #9aa3b1; font-size: 10px; }
.send-button { min-width: 92px; }
.resource-popover { display: grid; grid-template-columns: 145px minmax(0, 1fr) auto; gap: 9px; }
.resource-popover strong, .resource-popover p { grid-column: 1 / -1; }
.resource-popover p { margin: -3px 0 3px; color: #7b8492; font-size: 11px; line-height: 1.5; }

.pure-agent-side { display: grid; align-content: start; gap: 14px; }
.agent-state-card, .agent-plan-card, .agent-progress-card { padding: 17px; }
.pure-agent-side section > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 13px; }
.pure-agent-side section > header > div { display: grid; gap: 4px; }
.pure-agent-side header small { color: #7c8797; font-size: 10px; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; }
.pure-agent-side header strong { font-size: 14px; }
.stream-indicator { display: inline-flex; align-items: center; gap: 5px; color: #7a8493; font-size: 10px; font-weight: 750; }
.stream-indicator::before { content: ''; width: 7px; height: 7px; border-radius: 50%; background: #aab1bb; }
.stream-indicator.is-live::before { background: #20a36a; box-shadow: 0 0 0 4px rgba(32, 163, 106, .11); }
.stream-indicator.is-connecting::before, .stream-indicator.is-retrying::before { background: #e49a19; }
.stream-indicator.is-error::before { background: #d14343; }
.agent-state-card dl { display: grid; gap: 8px; margin: 0 0 13px; }
.agent-state-card dl div { display: flex; justify-content: space-between; gap: 12px; padding-bottom: 7px; border-bottom: 1px solid #eff2f5; }
.agent-state-card dt { color: #8993a2; font-size: 11px; }
.agent-state-card dd { margin: 0; color: #39465a; font-size: 11px; font-weight: 700; }
.agent-state-card :deep(.el-alert) { margin-top: 11px; padding: 10px; }
.agent-state-card :deep(.el-alert__title) { font-size: 11px; }
.agent-state-card :deep(.el-alert__description) { font-size: 10px; line-height: 1.5; }
.cancel-task-button { width: 100%; margin-top: 12px; }
.agent-plan-card > p, .side-empty { margin: 0; color: #788393; font-size: 11px; line-height: 1.65; }
.plan-steps { display: grid; gap: 8px; margin: 13px 0 0; padding: 0; list-style: none; }
.plan-steps li { display: grid; grid-template-columns: 34px minmax(0, 1fr); align-items: center; gap: 9px; }
.plan-steps span { display: grid; place-items: center; min-height: 27px; padding: 0 5px; border-radius: 8px; color: #087c94; background: #eaf8fb; font-size: 9px; font-weight: 800; }
.plan-steps strong { font-size: 11px; line-height: 1.45; }
.progress-list { display: grid; max-height: 330px; overflow-y: auto; }
.progress-list article { display: grid; grid-template-columns: 9px minmax(0, 1fr); gap: 10px; padding: 9px 0; border-bottom: 1px solid #eff2f5; }
.progress-list i { width: 8px; height: 8px; margin-top: 3px; border: 2px solid #53b7c9; border-radius: 50%; }
.progress-list i.terminal { border-color: #28a56b; background: #28a56b; }
.progress-list article div { display: grid; gap: 3px; }
.progress-list strong { color: #435066; font-size: 11px; line-height: 1.45; }
.progress-list time { color: #9aa3b0; font-size: 9px; }

@media (max-width: 1180px) {
  .pure-agent-layout { grid-template-columns: minmax(0, 1fr) 300px; }
}

@media (max-width: 920px) {
  .pure-agent-hero { align-items: flex-start; flex-direction: column; }
  .pure-agent-layout { grid-template-columns: 1fr; }
  .pure-agent-side { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .agent-progress-card { grid-column: 1 / -1; }
}

@media (max-width: 640px) {
  .pure-agent-hero { padding: 20px; }
  .pure-agent-hero-actions { width: 100%; flex-wrap: wrap; }
  .conversation-header { align-items: flex-start; flex-direction: column; }
  .conversation-meta { justify-content: flex-start; }
  .conversation-transcript { padding: 18px 12px; }
  .conversation-message.is-user .message-body { max-width: 100%; }
  .pending-input-card { grid-template-columns: 1fr; }
  .pending-actions, .composer-toolbar { align-items: stretch; flex-direction: column; }
  .resource-control { justify-content: space-between; }
  .send-button { width: 100%; }
  .pure-agent-side { grid-template-columns: 1fr; }
  .agent-progress-card { grid-column: auto; }
  .resource-popover { grid-template-columns: 1fr; }
  .resource-popover strong, .resource-popover p { grid-column: auto; }
}
</style>
