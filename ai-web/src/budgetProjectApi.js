import axios from 'axios'

const TOKEN_KEY = 'ai_token'

const budgetApiClient = axios.create({ baseURL: '/api/v1' })

budgetApiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

budgetApiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem('app_user_info')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export const budgetProjectApi = Object.freeze({
  list: (params) => budgetApiClient.get('/admin/budget-projects', { params }),
  create: (payload) => budgetApiClient.post('/admin/budget-projects', payload),
  detail: (projectId) => budgetApiClient.get(`/admin/budget-projects/${projectId}`),
  update: (projectId, payload) => budgetApiClient.patch(`/admin/budget-projects/${projectId}`, payload),
  archive: (projectId) => budgetApiClient.patch(`/admin/budget-projects/${projectId}/archive`, {}),
  listImports: (projectId, params) => budgetApiClient.get(`/admin/budget-projects/${projectId}/imports`, { params }),
  activeImport: (projectId) => budgetApiClient.get(`/admin/budget-projects/${projectId}/active-import`),
  uploadImport: (projectId, formData) => budgetApiClient.post(`/admin/budget-projects/${projectId}/imports`, formData),
  importDetail: (batchId) => budgetApiClient.get(`/admin/budget-projects/imports/${batchId}`),
  importRows: (batchId, params) => budgetApiClient.get(`/admin/budget-projects/imports/${batchId}/rows`, { params }),
  listImportRevisions: (batchId, params) => budgetApiClient.get(`/admin/budget-projects/imports/${batchId}/revisions`, { params }),
  importRevision: (batchId, revisionId) => budgetApiClient.get(`/admin/budget-projects/imports/${batchId}/revisions/${revisionId}`),
  updateSheetMappings: (batchId, payload) => budgetApiClient.post(`/admin/budget-projects/imports/${batchId}/remap`, payload),
  confirmImport: (batchId) => budgetApiClient.post(`/admin/budget-projects/imports/${batchId}/confirm`),
  activateImport: (batchId) => budgetApiClient.post(`/admin/budget-projects/imports/${batchId}/activate`),
  pricingReadiness: (projectId) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-readiness'),
  listPricingRuns: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-runs', { params }),
  createPricingRun: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-runs', payload),
  pricingRunLines: (runId, params) => budgetApiClient.get('/admin/budget-projects/pricing-runs/' + runId + '/lines', { params }),
  pricingLineCandidates: (runId, lineId) => budgetApiClient.get('/admin/budget-projects/pricing-runs/' + runId + '/lines/' + lineId + '/candidates'),
  currentPricingDraft: (projectId) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/current'),
  savePricingDraft: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft', payload),
  createPricingDraftQuoteJob: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/quote-job', payload),
  currentPricingDraftQuoteJob: (projectId) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/quote-job/current'),
  pricingDraftQuoteJob: (projectId, jobId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/quote-jobs/' + jobId, { params }),
  pricingDraftLines: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/lines', { params }),
  updatePricingDraftLine: (projectId, lineId, payload) => budgetApiClient.patch('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId, payload),
  estimatePricingDraftLine: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/ai-estimate', payload),
  previewAccountQuotaSync: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/account-quota-sync/preview', payload),
  confirmAccountQuotaSync: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/account-quota-sync/confirm', payload),
})

export function budgetResponseData(response) {
  return response?.data?.data ?? response?.data
}

export function budgetResponseItems(response) {
  const data = budgetResponseData(response)
  if (Array.isArray(data)) return data
  return data?.items || data?.records || data?.rows || []
}

export function budgetApiErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message || detail?.code) {
    const sheets = Array.isArray(detail.sheets) && detail.sheets.length ? `（Sheet：${detail.sheets.join('、')}）` : ''
    return `${detail.message || detail.code}${sheets}`
  }
  return error?.response?.data?.message || fallback
}
