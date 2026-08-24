import axios from 'axios'
import {
  clearAuth,
  getToken,
  isPasswordChangeRequiredError,
  redirectToPasswordChange,
} from './authStorage'

const API_PREFIX = '/bid-assessment-pure-agent/admin/diagnostics'
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

export function diagnosticResponseData(response) {
  return response?.data?.data ?? response?.data
}

export function diagnosticErrorMessage(error, fallback = 'Pure Agent 诊断视图暂时不可用') {
  const body = error?.response?.data
  if (error?.response?.status === 403) return '仅管理员可以查看 Pure Agent 诊断信息'
  if (error?.response?.status === 404) return '诊断 Task 不存在，或 Pure Agent 功能尚未开启'
  return body?.error?.guidance || body?.message || fallback
}

export const bidAssessmentPureAgentDiagnosticsApi = Object.freeze({
  tasks: (params = {}) => client.get(`${API_PREFIX}/tasks`, { params }),
  snapshot: (taskRef) => client.get(`${API_PREFIX}/tasks/${pathSegment(taskRef)}`),
})
