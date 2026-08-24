import axios from 'axios'
import {
  clearAuth,
  getToken,
  isPasswordChangeRequiredError,
  redirectToPasswordChange,
} from './authStorage'

const API_PREFIX = '/bid-assessment-pure-agent'
const CURSOR_PREFIX = 'bid-pa:event-cursor:'
const client = axios.create({ baseURL: '/api/v1' })

client.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (isPasswordChangeRequiredError(error)) {
      redirectToPasswordChange()
      return Promise.reject(error)
    }
    if (error.response?.status === 401) {
      clearAuth()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

function pathSegment(value) {
  return encodeURIComponent(String(value || ''))
}

export function createPureAgentIdempotencyKey(scope = 'ui') {
  const random = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `pa-${scope}:${random}`.slice(0, 128)
}

export function responseData(response) {
  return response?.data?.data ?? response?.data
}

export function pureAgentErrorMessage(error, fallback = '研判 Agent 暂时不可用') {
  const body = error?.response?.data
  const detail = body?.detail
  const code = body?.error?.code
    || (typeof detail === 'object' ? detail?.code : detail)
  if (code === 'PURE_AGENT_STATE_CONFLICT' || error?.response?.status === 409) {
    return body?.error?.guidance || '任务状态已变化，请刷新后重试'
  }
  if (code === 'PURE_AGENT_RESOURCE_NOT_FOUND' || error?.response?.status === 404) {
    return '对话不存在、不可见，或 Pure Agent 功能尚未开启'
  }
  if (code === 'PURE_AGENT_STORAGE_UNAVAILABLE' || error?.response?.status === 503) {
    return body?.error?.guidance || '对话服务暂时不可用，请稍后重试'
  }
  if (error?.response?.status === 422) return '输入格式不符合要求，请检查后重试'
  return body?.message || body?.error?.guidance || fallback
}

export const bidAssessmentPureAgentApi = Object.freeze({
  runtimeStatus: () => client.get(`${API_PREFIX}/runtime-status`),
  createConversation: (body, idempotencyKey) => client.post(
    `${API_PREFIX}/conversations`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  ),
  conversation: (conversationRef) => client.get(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}`,
  ),
  messages: (conversationRef, afterSequence = 0, limit = 100) => client.get(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}/messages`,
    { params: { after_sequence: afterSequence, limit } },
  ),
  submitMessage: (conversationRef, body, idempotencyKey) => client.post(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}/messages`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  ),
  task: (conversationRef, taskRef) => client.get(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}/tasks/${pathSegment(taskRef)}`,
  ),
  events: (conversationRef, taskRef, afterVersion = 0, limit = 100) => client.get(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}/tasks/${pathSegment(taskRef)}/events`,
    { params: { after_version: afterVersion, limit } },
  ),
  submitSlot: (conversationRef, taskRef, slotRef, body, idempotencyKey) => client.post(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}/tasks/${pathSegment(taskRef)}/slots/${pathSegment(slotRef)}/responses`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  ),
  cancelTask: (conversationRef, taskRef, body, idempotencyKey) => client.post(
    `${API_PREFIX}/conversations/${pathSegment(conversationRef)}/tasks/${pathSegment(taskRef)}/cancel`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  ),
})

export function eventCursor(taskRef) {
  return window.sessionStorage.getItem(`${CURSOR_PREFIX}${String(taskRef || '')}`) || ''
}

export function rememberEventCursor(taskRef, eventId) {
  if (!taskRef || !eventId) return
  window.sessionStorage.setItem(`${CURSOR_PREFIX}${String(taskRef)}`, String(eventId))
}

function parseSseFrame(frame) {
  const parsed = { id: '', event: 'message', data: '' }
  frame.split(/\r?\n/).forEach((line) => {
    if (!line || line.startsWith(':')) return
    const separator = line.indexOf(':')
    const field = separator >= 0 ? line.slice(0, separator) : line
    const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, '') : ''
    if (field === 'data') parsed.data += `${value}\n`
    else if (field === 'id') parsed.id = value
    else if (field === 'event') parsed.event = value
  })
  const dataText = parsed.data.trimEnd()
  try {
    parsed.data = dataText ? JSON.parse(dataText) : null
  } catch (_error) {
    parsed.data = dataText
  }
  return parsed
}

/**
 * Authenticated fetch-stream transport for the B05-5 safe SSE contract.
 * This parser only transports server-projected public events; it does not
 * drive, schedule, or infer an Agent path.
 */
export function streamPureAgentTaskEvents(
  conversationRef,
  taskRef,
  handlers = {},
) {
  const controller = new AbortController()
  let lastEventId = String(handlers.lastEventId || eventCursor(taskRef) || '')

  const consume = async () => {
    const token = getToken()
    const headers = { Accept: 'text/event-stream' }
    if (token) headers.Authorization = `Bearer ${token}`
    if (lastEventId) headers['Last-Event-ID'] = lastEventId
    const response = await fetch(
      `/api/v1${API_PREFIX}/conversations/${pathSegment(conversationRef)}/tasks/${pathSegment(taskRef)}/events/stream`,
      { headers, signal: controller.signal },
    )
    if (response.status === 401) {
      clearAuth()
      window.location.href = '/login'
      return
    }
    if (!response.ok || !response.body) {
      const error = new Error(`PURE_AGENT_SSE_HTTP_${response.status}`)
      error.status = response.status
      throw error
    }

    handlers.onOpen?.()
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (!controller.signal.aborted) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      for (const frame of frames) {
        if (!frame.trim() || frame.trimStart().startsWith(':')) continue
        const parsed = parseSseFrame(frame)
        if (parsed.id) {
          lastEventId = parsed.id
          rememberEventCursor(taskRef, parsed.id)
        }
        handlers.onEvent?.(parsed)
      }
    }
    if (!controller.signal.aborted) handlers.onClosed?.()
  }

  consume().catch((error) => {
    if (!controller.signal.aborted) handlers.onError?.(error)
  })
  return () => controller.abort()
}
