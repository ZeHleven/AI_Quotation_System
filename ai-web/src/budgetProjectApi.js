import axios from 'axios'
import { clearAuth, getToken, isPasswordChangeRequiredError, redirectToPasswordChange } from './authStorage'

const budgetApiClient = axios.create({ baseURL: '/api/v1' })

budgetApiClient.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

budgetApiClient.interceptors.response.use(
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
  activatePricingRun: (projectId, runId) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-runs/' + runId + '/activate'),
  archivePricingRun: (projectId, runId) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-runs/' + runId + '/archive'),
  pricingRunLines: (runId, params) => budgetApiClient.get('/admin/budget-projects/pricing-runs/' + runId + '/lines', { params }),
  pricingLineCandidates: (runId, lineId) => budgetApiClient.get('/admin/budget-projects/pricing-runs/' + runId + '/lines/' + lineId + '/candidates'),
  currentPricingDraft: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/current', { params }),
  exportOriginalFormatPricingDraft: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/original-format-export', { params, responseType: 'blob' }),
  savePricingDraft: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft', payload),
  updatePricingDraftTotalsConfig: (projectId, payload) => budgetApiClient.patch('/admin/budget-projects/' + projectId + '/pricing-draft/totals-config', payload),
  createPricingDraftQuoteJob: (projectId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/quote-job', payload),
  currentPricingDraftQuoteJob: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/quote-job/current', { params }),
  pricingDraftQuoteJob: (projectId, jobId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/quote-jobs/' + jobId, { params }),
  cancelPricingDraftQuoteJob: (projectId, jobId) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/quote-jobs/' + jobId + '/cancel'),
  pricingDraftLines: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/lines', { params }),
  projectEnterpriseQuotaItems: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/enterprise-quota-items', { params }),
  pricingDraftResourceDetails: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/resource-details', { params }),
  exportPricingDraftStatistics: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/statistics-export', { params, responseType: 'blob' }),
  pricingDraftProcurementStatistics: (projectId, params) => budgetApiClient.get('/admin/budget-projects/' + projectId + '/pricing-draft/procurement-statistics', { params }),
  materializeProjectQuota: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota', payload),
  addProjectQuota: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota/add', payload),
  replaceProjectQuota: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota/replace', payload),
  deleteProjectQuota: (projectId, lineId, payload) => budgetApiClient.delete('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota', { data: payload }),
  createProjectQuotaResource: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota/resources', payload),
  updateProjectQuotaResource: (projectId, lineId, resourceId, payload) => budgetApiClient.patch('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota/resources/' + resourceId, payload),
  deleteProjectQuotaResource: (projectId, lineId, resourceId, payload) => budgetApiClient.delete('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota/resources/' + resourceId, { data: payload }),
  syncProjectQuotaToEnterprise: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/project-quota/sync-enterprise', payload),
  updatePricingDraftLine: (projectId, lineId, payload) => budgetApiClient.patch('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId, payload),
  updatePricingDraftLineConstructionNote: (projectId, lineId, payload) => budgetApiClient.patch('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/construction-note', payload),
  estimatePricingDraftLine: (projectId, lineId, payload) => budgetApiClient.post('/admin/budget-projects/' + projectId + '/pricing-draft/lines/' + lineId + '/ai-estimate', payload),
  enterpriseQuotaItemDetail: (itemId) => budgetApiClient.get('/admin/cost-master/quota-items/' + itemId),
  accountQuotaItemDetail: (itemId) => budgetApiClient.get('/admin/account-quotas/' + itemId),
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

function budgetSheetLimitText(sheet) {
  if (typeof sheet === 'string') return sheet
  if (!sheet || typeof sheet !== 'object') return ''
  const name = sheet.sheet_name || sheet.name || sheet.source_sheet || '未命名 Sheet'
  const rows = sheet.row_count != null ? `${sheet.row_count} 行` : ''
  const columns = sheet.column_count != null ? `${sheet.column_count} 列` : ''
  const size = [rows, columns].filter(Boolean).join(' / ')
  return size ? `${name}（${size}）` : name
}

const budgetErrorMessages = Object.freeze({
  BUDGET_PRICING_EXPORT_IMPORT_BATCH_NOT_FOUND: '当前报价草稿关联的原始导入批次不存在，请重新导入清单',
  BUDGET_PRICING_EXPORT_SOURCE_FILE_NOT_RETAINED: '原始清单文件未保留，请重新上传清单后再导出',
  BUDGET_PRICING_EXPORT_LOCAL_SOURCE_NOT_FOUND: '本地验收环境找不到原始清单文件，请重新初始化测试数据',
  BUDGET_PRICING_EXPORT_LOCAL_SOURCE_PATH_INVALID: '本地验收原始清单路径无效，请重新初始化测试数据',
  BUDGET_PRICING_EXPORT_LOCAL_SOURCE_HASH_MISMATCH: '本地原始清单与导入记录不一致，请重新导入清单',
  BUDGET_PRICING_EXPORT_WORKBOOK_INVALID: '原始清单文件损坏或格式无法读取，请重新上传有效的 Excel 文件',
})

const budgetErrorText = (value) => budgetErrorMessages[value] || value

export function budgetApiErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return budgetErrorText(detail)
  if (detail?.message || detail?.code) {
    const sheets = Array.isArray(detail.sheets) && detail.sheets.length
      ? `（Sheet：${detail.sheets.map(budgetSheetLimitText).filter(Boolean).join('、')}）`
      : ''
    const limits = detail.code === 'BUDGET_IMPORT_WORKBOOK_LIMIT_EXCEEDED'
      ? `；单个 Sheet 上限：${detail.max_rows_per_sheet ?? '-'} 行 / ${detail.max_columns_per_sheet ?? '-'} 列`
      : ''
    return `${detail.message || budgetErrorText(detail.code)}${sheets}${limits}`
  }
  return error?.response?.data?.message || fallback
}

export async function budgetBlobErrorMessage(error, fallback = '请求失败') {
  const data = error?.response?.data
  if (!data || typeof data.text !== 'function') return budgetApiErrorMessage(error, fallback)
  try {
    const text = await data.text()
    const parsed = JSON.parse(text)
    return budgetApiErrorMessage({
      response: { ...error.response, data: parsed },
    }, fallback)
  } catch {
    return fallback
  }
}
