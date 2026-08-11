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

export const enterpriseQuotaV2Api = Object.freeze({
  previewImport: (formData) => client.post('/admin/enterprise-quota-v2/import/preview', formData),
  importDraft: (formData) => client.post('/admin/enterprise-quota-v2/import', formData),
  versions: () => client.get('/admin/enterprise-quota-v2/versions'),
  version: (versionId) => client.get(`/admin/enterprise-quota-v2/versions/${versionId}`),
  activeItem: (itemId) => client.get(`/admin/cost-master/quota-items/${itemId}`),
  masterItems: (params) => client.get('/admin/cost-master/quota-items', { params }),
  rows: (versionId, params) =>
    client.get(`/admin/enterprise-quota-v2/versions/${versionId}/rows`, { params }),
  updateResource: (versionId, resourceId, payload) =>
    client.patch(`/admin/enterprise-quota-v2/versions/${versionId}/resources/${resourceId}`, payload),
  createResource: (versionId, payload) =>
    client.post(`/admin/enterprise-quota-v2/versions/${versionId}/resources`, payload),
  cloneVersion: (versionId, payload) =>
    client.post(`/admin/enterprise-quota-v2/versions/${versionId}/clone`, payload),
  recalculate: (versionId, payload) =>
    client.post(`/admin/enterprise-quota-v2/versions/${versionId}/recalculate`, payload),
  activate: (versionId, payload) =>
    client.post(`/admin/enterprise-quota-v2/versions/${versionId}/activate`, payload),
  events: (versionId, params) =>
    client.get(`/admin/enterprise-quota-v2/versions/${versionId}/events`, { params }),
})

export function quotaV2Data(response) {
  return response?.data?.data ?? response?.data
}

export function quotaV2Items(response) {
  const data = quotaV2Data(response)
  if (Array.isArray(data)) return data
  return data?.items || data?.rows || []
}

export function quotaV2ErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return detail?.message || detail?.code || error?.response?.data?.message || fallback
}
