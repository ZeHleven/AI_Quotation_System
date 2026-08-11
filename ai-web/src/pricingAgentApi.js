import axios from 'axios'
import { clearAuth, getToken, isPasswordChangeRequiredError, redirectToPasswordChange } from './authStorage'

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

export const pricingAgentApi = Object.freeze({
  capabilities: () => client.get('/pricing-agent/capabilities'),
  archives: (params) => client.get('/pricing-agent/archives', { params }),
  uploadArchive: (formData) => client.post('/pricing-agent/archives', formData),
  disableArchive: (archiveUuid) => client.post(`/pricing-agent/archives/${archiveUuid}/disable`),
  previewDemand: (formData) => client.post('/pricing-agent/demand-preview', formData),
  createRun: (payload) => client.post('/pricing-agent/runs', payload),
  listRuns: (params) => client.get('/pricing-agent/runs', { params }),
  runDetail: (runUuid) => client.get(`/pricing-agent/runs/${runUuid}`),
  selectCandidate: (runUuid, lineUuid, payload) =>
    client.put(`/pricing-agent/runs/${runUuid}/lines/${lineUuid}/selection`, payload),
  setManualPrice: (runUuid, lineUuid, payload) =>
    client.put(`/pricing-agent/runs/${runUuid}/lines/${lineUuid}/manual-price`, payload),
  confirmToQuoteDraft: (runUuid) =>
    client.post(`/pricing-agent/runs/${runUuid}/confirm-to-quote-draft`),
})

export function pricingAgentResponseData(response) {
  return response?.data?.data ?? response?.data
}

export function pricingAgentErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  const labels = {
    FEATURE_DISABLED: '报价 Agent 第一版尚未开启',
    PRICING_AGENT_EXPANDED_MATCH_DISABLED: '“准确+近似”模式尚未开启',
    PRICING_AGENT_INDUSTRY_ESTIMATE_DISABLED: '行业数据 AI 估价尚未开启',
    PRICING_AGENT_RUN_ALREADY_CONFIRMED: '该组价结果已经确认，不能再更换候选或补价',
    PRICING_AGENT_RUN_LINE_NOT_FOUND: '该组价项目不存在或已经失效',
    PRICING_AGENT_CANDIDATE_NOT_FOUND: '候选记录不存在或已经失效',
    PRICING_AGENT_CANDIDATE_PRICE_INVALID: '候选价格无效，无法采用',
    PRICING_AGENT_MANUAL_PRICE_ONLY_FOR_UNPRICED: '该项目已有自动组价结果，不能直接覆盖；请从候选中改选',
    PRICING_AGENT_MANUAL_PRICE_INVALID: '人工补价必须大于 0',
    PRICING_AGENT_CONFIRMATION_TARGET_MISSING: '已确认的报价草稿不存在，请联系管理员检查',
  }
  const code = typeof detail === 'string' ? detail : detail?.code
  if (code && labels[code]) return labels[code]
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (detail?.code) return detail.code
  return error?.response?.data?.message || fallback
}
