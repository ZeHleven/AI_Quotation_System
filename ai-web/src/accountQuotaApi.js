import axios from 'axios'

const TOKEN_KEY = 'ai_token'

const accountQuotaApiClient = axios.create({ baseURL: '/api/v1' })

accountQuotaApiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

accountQuotaApiClient.interceptors.response.use(
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

export const accountQuotaApi = Object.freeze({
  list: (params) => accountQuotaApiClient.get('/admin/account-quotas', { params }),
  create: (payload) => accountQuotaApiClient.post('/admin/account-quotas', payload),
  detail: (identifier) => accountQuotaApiClient.get(`/admin/account-quotas/${identifier}`),
  update: (identifier, payload) => accountQuotaApiClient.patch(`/admin/account-quotas/${identifier}`, payload),
  changeStatus: (identifier, payload) => accountQuotaApiClient.post(`/admin/account-quotas/${identifier}/status`, payload),
  history: (identifier, params) => accountQuotaApiClient.get(`/admin/account-quotas/${identifier}/history`, { params }),
})

export function accountQuotaResponseData(response) {
  return response?.data?.data ?? response?.data
}

export function accountQuotaResponseItems(response) {
  const data = accountQuotaResponseData(response)
  if (Array.isArray(data)) return data
  return data?.items || data?.records || data?.rows || data?.history || []
}

export function accountQuotaResponseTotal(response, fallback = 0) {
  const data = accountQuotaResponseData(response)
  return response?.data?.total ?? data?.total ?? data?.count ?? fallback
}

export function accountQuotaApiErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (detail?.code) return detail.code
  return error?.response?.data?.message || fallback
}
