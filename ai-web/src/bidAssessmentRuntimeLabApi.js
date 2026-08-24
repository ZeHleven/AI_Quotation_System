import axios from 'axios'
import {
  clearAuth,
  getToken,
  isPasswordChangeRequiredError,
  redirectToPasswordChange,
} from './authStorage'

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

export const bidAssessmentRuntimeLabApi = Object.freeze({
  capabilities: () => client.get('/bid-assessment-runtime-lab/capabilities'),
  executePreflight: () => client.get('/bid-assessment-runtime-lab/execute-preflight'),
  enterpriseSnapshot: () => client.get('/bid-assessment-runtime-lab/enterprise-snapshot'),
  validateEnterpriseBaseline: (body) => client.post(
    '/bid-assessment-runtime-lab/enterprise-baseline/validate',
    body,
  ),
  createEnterpriseSnapshot: (body, idempotencyKey, candidateHash = '') => client.post(
    '/bid-assessment-runtime-lab/enterprise-snapshots',
    body,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        ...(candidateHash ? { 'X-Enterprise-Candidate-Hash': candidateHash } : {}),
      },
    },
  ),
  enterpriseBusinessBaseline: (snapshotId = '') => client.get(
    '/bid-assessment-runtime-lab/enterprise-business-baseline',
    { params: snapshotId ? { snapshot_id: snapshotId } : {} },
  ),
  validateEnterpriseBusinessBaseline: (body) => client.post(
    '/bid-assessment-runtime-lab/enterprise-business-baselines/validate',
    body,
  ),
  createEnterpriseBusinessBaseline: (body, idempotencyKey, candidateHash) => client.post(
    '/bid-assessment-runtime-lab/enterprise-business-baselines',
    body,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-Enterprise-Business-Candidate-Hash': candidateHash,
      },
    },
  ),
  hardGateComparisonDraft: (assessmentId, sourceRunId, businessBaselineId) => client.get(
    '/bid-assessment-runtime-lab/hard-gate-comparison-draft',
    {
      params: {
        assessment_id: assessmentId,
        source_run_id: sourceRunId,
        business_baseline_id: businessBaselineId,
      },
    },
  ),
  hardGateComparisonBaseline: (assessmentId = '') => client.get(
    '/bid-assessment-runtime-lab/hard-gate-comparison-baseline',
    { params: assessmentId ? { assessment_id: assessmentId } : {} },
  ),
  validateHardGateComparisonBaseline: (body) => client.post(
    '/bid-assessment-runtime-lab/hard-gate-comparison-baselines/validate',
    body,
  ),
  createHardGateComparisonBaseline: (body, idempotencyKey, candidateHash) => client.post(
    '/bid-assessment-runtime-lab/hard-gate-comparison-baselines',
    body,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-Hard-Gate-Comparison-Candidate-Hash': candidateHash,
      },
    },
  ),
  enterpriseEvidenceItems: () => client.get(
    '/bid-assessment-runtime-lab/enterprise-evidence-items',
  ),
  enterpriseEvidencePackage: () => client.get(
    '/bid-assessment-runtime-lab/enterprise-evidence-package',
  ),
  uploadEnterpriseEvidenceItem: (formData, sha256, idempotencyKey) => client.post(
    '/bid-assessment-runtime-lab/enterprise-evidence-items',
    formData,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-Content-SHA256': sha256,
      },
    },
  ),
  validateEnterpriseEvidencePackage: (body) => client.post(
    '/bid-assessment-runtime-lab/enterprise-evidence-packages/validate',
    body,
  ),
  createEnterpriseEvidencePackage: (body, idempotencyKey, candidateHash) => client.post(
    '/bid-assessment-runtime-lab/enterprise-evidence-packages',
    body,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-Enterprise-Evidence-Candidate-Hash': candidateHash,
      },
    },
  ),
  releaseCandidate: (runId) => client.get(
    '/bid-assessment-runtime-lab/release-candidate',
    { params: { run_id: runId } },
  ),
  validateReleaseCandidate: (body) => client.post(
    '/bid-assessment-runtime-lab/release-candidates/validate',
    body,
  ),
  createReleaseCandidate: (body, idempotencyKey, candidateHash) => client.post(
    '/bid-assessment-runtime-lab/release-candidates',
    body,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-MVP-RC-Candidate-Hash': candidateHash,
      },
    },
  ),
  runs: (limit = 20) => client.get('/bid-assessment-runtime-lab/runs', { params: { limit } }),
  trace: (runId) => client.get(`/bid-assessment-runtime-lab/runs/${encodeURIComponent(runId)}/trace`),
  reports: (assessmentId) => client.get(`/bid-assessments/${encodeURIComponent(assessmentId)}/reports`),
  report: (reportId) => client.get(`/bid-reports/${encodeURIComponent(reportId)}`),
  createAssessment: (body, idempotencyKey) => client.post('/bid-assessments', body, {
    headers: { 'Idempotency-Key': idempotencyKey },
  }),
  assessment: (assessmentId) => client.get(`/bid-assessments/${encodeURIComponent(assessmentId)}`),
  createUploadBatch: (assessmentId, body, etag, idempotencyKey) => client.post(
    `/bid-assessments/${encodeURIComponent(assessmentId)}/upload-batches`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': etag } },
  ),
  uploadFile: (batchId, formData, sha256, idempotencyKey) => client.post(
    `/bid-upload-batches/${encodeURIComponent(batchId)}/files`,
    formData,
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
        'X-Content-SHA256': sha256,
      },
    },
  ),
  commitUploadBatch: (batchId, body, etag, idempotencyKey) => client.post(
    `/bid-upload-batches/${encodeURIComponent(batchId)}/commit`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': etag } },
  ),
  lots: (assessmentId) => client.get(`/bid-assessments/${encodeURIComponent(assessmentId)}/lots`),
  selectLot: (assessmentId, body, etag, idempotencyKey) => client.post(
    `/bid-assessments/${encodeURIComponent(assessmentId)}/lot-selection`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': etag } },
  ),
  runSnapshot: (assessmentId, runId) => client.get(
    `/bid-assessments/${encodeURIComponent(assessmentId)}/runs/${encodeURIComponent(runId)}`,
  ),
  cancelRun: (assessmentId, runId, body, etag, idempotencyKey) => client.post(
    `/bid-assessments/${encodeURIComponent(assessmentId)}/runs/${encodeURIComponent(runId)}/cancel`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': etag } },
  ),
  retryRun: (assessmentId, runId, body, etag, idempotencyKey) => client.post(
    `/bid-assessments/${encodeURIComponent(assessmentId)}/runs/${encodeURIComponent(runId)}/retry`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': etag } },
  ),
})

export function responseData(response) {
  return response?.data?.data ?? response?.data
}

export function runtimeLabErrorMessage(error, fallback = '研判服务暂时不可用') {
  const detail = error?.response?.data?.detail
  const code = error?.response?.data?.error?.code
    || error?.response?.data?.error?.error_code
    || (typeof detail === 'object' ? detail?.code : detail)
  if (code === 'BID_RESOURCE_NOT_FOUND' || error?.response?.status === 404) {
    return 'MVP-0 只读轨迹尚未开启，或当前 Run 不可见'
  }
  if (code === 'BID_MVP1_VIEW_ONLY') return 'Runtime Lab 当前为 view-only，写操作已被服务端阻断'
  if (code === 'BID_STORAGE_UNAVAILABLE') return '运行轨迹存储暂时不可用'
  if (code === 'BID_RESOURCE_VERSION_MISMATCH') return 'Run 状态已变化，请刷新后重新确认操作'
  if (code === 'BID_PRECONDITION_REQUIRED') return '缺少最新 Run ETag，请刷新状态后重试'
  if (code === 'BID_RUN_NOT_RETRYABLE') return '当前 Run 不满足 Checkpoint 重试条件'
  return error?.response?.data?.message || fallback
}

export function responseEtag(response, fallback = '') {
  return response?.headers?.etag || fallback
}

export function responseHeader(response, name, fallback = '') {
  return response?.headers?.[String(name).toLowerCase()] || fallback
}

export function streamAssessmentEvents(assessmentId, handlers = {}) {
  const controller = new AbortController()
  let lastEventId = String(handlers.lastEventId || '')

  const consume = async () => {
    const token = getToken()
    const headers = { Accept: 'text/event-stream' }
    if (token) headers.Authorization = `Bearer ${token}`
    if (lastEventId) headers['Last-Event-ID'] = lastEventId
    const response = await fetch(
      `/api/v1/bid-assessments/${encodeURIComponent(assessmentId)}/events`,
      { headers, signal: controller.signal },
    )
    if (!response.ok || !response.body) {
      throw new Error(`SSE_HTTP_${response.status}`)
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
        const parsed = { id: '', event: 'message', data: '' }
        frame.split(/\r?\n/).forEach((line) => {
          const separator = line.indexOf(':')
          const field = separator >= 0 ? line.slice(0, separator) : line
          const valueText = separator >= 0 ? line.slice(separator + 1).replace(/^ /, '') : ''
          if (field === 'data') parsed.data += `${valueText}\n`
          else if (field === 'id') parsed.id = valueText
          else if (field === 'event') parsed.event = valueText
        })
        if (parsed.id) lastEventId = parsed.id
        let data = parsed.data.trimEnd()
        try { data = data ? JSON.parse(data) : null } catch (_error) { /* keep text */ }
        handlers.onEvent?.({ ...parsed, data })
      }
    }
  }

  consume().catch((error) => {
    if (!controller.signal.aborted) handlers.onError?.(error)
  })
  return () => controller.abort()
}
