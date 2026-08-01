import axios from 'axios'
import { getToken } from './authStorage'

const unifiedQuoteClient = axios.create({ baseURL: '/api/v1' })

unifiedQuoteClient.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

function responseData(response, fallback = null) {
  return response?.data?.data ?? fallback
}

function pageResult(response) {
  return {
    items: responseData(response, []),
    total: Number(response?.data?.total || 0),
  }
}

export async function listQuoteJobs(params = {}) {
  return pageResult(await unifiedQuoteClient.get('/quote/jobs', { params }))
}

export async function listQuoteHistory(params = {}) {
  return pageResult(await unifiedQuoteClient.get('/history', { params }))
}

export async function listBudgetProjects(params = {}) {
  return pageResult(await unifiedQuoteClient.get('/admin/budget-projects', { params }))
}

export async function createQuoteJob({ message, file, projectName, clientName }) {
  const form = new FormData()
  const context = [
    projectName ? `项目名称：${projectName}` : '',
    clientName ? `客户名称：${clientName}` : '',
    message?.trim() || '',
  ].filter(Boolean).join('\n')
  form.append('message', context)
  form.append('source', '项目报价')
  form.append('notes', '从 /quotes 项目报价入口创建')
  if (file) form.append('file', file)
  return responseData(await unifiedQuoteClient.post('/quote/jobs', form))
}

export async function createBudgetProject(payload) {
  return responseData(await unifiedQuoteClient.post('/admin/budget-projects', payload))
}

export async function uploadBudgetProjectWorkbook(projectId, file) {
  const form = new FormData()
  form.append('file', file)
  return responseData(await unifiedQuoteClient.post(`/admin/budget-projects/${projectId}/imports`, form))
}
