<template>
  <section class="project-quotes-shell">
    <template v-if="createMode">
      <header class="project-quotes-heading">
        <div>
          <p class="project-quotes-eyebrow">Project Quotation</p>
          <h2>新建项目报价</h2>
          <p>调试阶段保留项目报价与对话报价两个入口，提交后仍复用现有报价和预审能力。</p>
        </div>
        <el-button plain @click="emit('navigate', '/quotes')">返回项目报价</el-button>
      </header>

      <div class="entry-mode-notice">
        <div>
          <strong>项目报价入口</strong>
          <span>Excel 且具备项目计价权限时进入正式预算项目；文字、图片或其他情况进入异步报价预审。</span>
        </div>
        <el-button text type="primary" @click="emit('open-chat')">改用对话报价</el-button>
      </div>

      <el-form class="project-quote-form" label-position="top" @submit.prevent>
        <div class="project-quote-form-grid">
          <el-form-item label="项目名称">
            <el-input
              v-model="form.projectName"
              maxlength="255"
              placeholder="例如：南山办公楼装饰工程"
              show-word-limit
            />
          </el-form-item>
          <el-form-item label="客户名称（选填）">
            <el-input v-model="form.clientName" maxlength="128" placeholder="请输入客户或甲方名称" />
          </el-form-item>
        </div>

        <el-form-item label="报价需求">
          <el-input
            v-model="form.message"
            type="textarea"
            :rows="8"
            maxlength="8000"
            show-word-limit
            placeholder="可以直接粘贴施工项目、规格、工程量和单位；也可以只上传需求文件。"
          />
        </el-form-item>

        <el-form-item label="需求文件（选填）">
          <label class="project-quote-upload">
            <input
              ref="fileInput"
              type="file"
              accept="image/*,.xlsx,.xlsm"
              @change="selectFile"
            />
            <span class="project-quote-upload-icon">＋</span>
            <span>
              <strong>{{ form.file ? form.file.name : '选择图片或 Excel' }}</strong>
              <small>支持图片、.xlsx、.xlsm；旧 .xls 请先另存为 .xlsx</small>
            </span>
          </label>
        </el-form-item>

        <el-alert
          v-if="routeHint"
          :title="routeHint.title"
          :description="routeHint.description"
          :type="routeHint.type"
          show-icon
          :closable="false"
        />

        <div class="project-quote-actions">
          <el-button plain @click="resetForm">清空</el-button>
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="submitQuote"
          >
            {{ submitButtonText }}
          </el-button>
        </div>
      </el-form>
    </template>

    <template v-else>
      <header class="project-quotes-heading">
        <div>
          <p class="project-quotes-eyebrow">Project Quotation</p>
          <h2>项目报价</h2>
          <p>集中查看报价任务、预审草稿、已确认报价和正式预算项目。</p>
        </div>
        <div class="project-quotes-heading-actions">
          <el-button plain @click="emit('open-chat')">进入对话报价</el-button>
          <el-button type="primary" @click="emit('navigate', '/quotes/new')">新建项目报价</el-button>
        </div>
      </header>

      <div class="dual-entry-banner">
        <span class="dual-entry-dot"></span>
        <div>
          <strong>当前为双入口调试阶段</strong>
          <p>项目报价与对话报价均保留；流程全部验收后再隐藏其中一个入口。</p>
        </div>
      </div>

      <div class="project-quote-stats">
        <article>
          <span>处理中</span>
          <strong>{{ summary.processing }}</strong>
        </article>
        <article>
          <span>待预审/待补充</span>
          <strong>{{ summary.review }}</strong>
        </article>
        <article>
          <span>已完成</span>
          <strong>{{ summary.completed }}</strong>
        </article>
        <article>
          <span>异常</span>
          <strong>{{ summary.failed }}</strong>
        </article>
      </div>

      <div class="project-quote-toolbar">
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索项目、客户、任务号或文件名"
          @keyup.enter="loadRows"
        />
        <el-select v-model="sourceFilter" placeholder="全部来源">
          <el-option label="全部来源" value="" />
          <el-option label="报价任务" value="job" />
          <el-option label="报价记录" value="history" />
          <el-option v-if="canViewBudgetProjects" label="预算项目" value="budget" />
        </el-select>
        <el-button :loading="loading" @click="loadRows">刷新</el-button>
      </div>

      <el-table
        v-loading="loading"
        :data="visibleRows"
        class="project-quote-table"
        empty-text="暂无项目报价记录"
      >
        <el-table-column label="项目/报价" min-width="250">
          <template #default="{ row }">
            <div class="project-quote-title">
              <strong>{{ row.title }}</strong>
              <span>{{ row.subtitle || '—' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="clientName" label="客户" min-width="130" />
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag effect="plain" :type="sourceTagType(row.source)">{{ row.sourceLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="light">{{ row.statusLabel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="项目数" width="90" align="right">
          <template #default="{ row }">{{ row.itemCount ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="金额" width="140" align="right">
          <template #default="{ row }">{{ formatAmount(row.totalAmount) }}</template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" width="165" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openRow(row)">{{ row.actionLabel }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createBudgetProject,
  createQuoteJob,
  listBudgetProjects,
  listQuoteHistory,
  listQuoteJobs,
  uploadBudgetProjectWorkbook,
} from './unifiedQuoteApi'

const props = defineProps({
  createMode: { type: Boolean, default: false },
  canViewBudgetProjects: { type: Boolean, default: false },
  canEditBudgetProjects: { type: Boolean, default: false },
})

const emit = defineEmits(['navigate', 'open-chat'])
const loading = ref(false)
const submitting = ref(false)
const keyword = ref('')
const sourceFilter = ref('')
const rows = ref([])
const fileInput = ref(null)
const form = reactive({
  projectName: '',
  clientName: '',
  message: '',
  file: null,
})

const isExcel = computed(() => /\.(xlsx|xlsm)$/i.test(form.file?.name || ''))
const useBudgetProjectFlow = computed(() => Boolean(
  isExcel.value && props.canEditBudgetProjects && form.projectName.trim(),
))
const canSubmit = computed(() => Boolean(
  (form.message.trim() || form.file) && (!isExcel.value || !props.canEditBudgetProjects || form.projectName.trim()),
))
const submitButtonText = computed(() => (
  useBudgetProjectFlow.value ? '创建预算项目并导入' : '开始生成报价'
))
const routeHint = computed(() => {
  if (!form.file) return null
  if (/\.xls$/i.test(form.file.name)) {
    return {
      type: 'warning',
      title: '暂不支持旧 .xls 文件',
      description: '请先在 Excel 中另存为 .xlsx 后重新上传。',
    }
  }
  if (isExcel.value && props.canEditBudgetProjects && !form.projectName.trim()) {
    return {
      type: 'warning',
      title: '请填写项目名称',
      description: '当前账号具备项目计价权限，Excel 将创建正式预算项目并导入清单。',
    }
  }
  if (useBudgetProjectFlow.value) {
    return {
      type: 'success',
      title: '将进入正式预算项目流程',
      description: '系统会创建项目、导入 Excel，并进入字段映射、清单确认和项目计价。',
    }
  }
  return {
    type: 'info',
    title: '将进入异步报价预审',
    description: '系统会自动生成标准清单，并按账户定额、企业定额、AI 估价顺序逐行计价。',
  }
})

const visibleRows = computed(() => {
  const source = sourceFilter.value
  const search = keyword.value.trim().toLowerCase()
  return rows.value.filter((row) => {
    if (source && row.source !== source) return false
    if (!search) return true
    return [
      row.title,
      row.subtitle,
      row.clientName,
      row.identifier,
      row.sourceFileName,
    ].some((value) => String(value || '').toLowerCase().includes(search))
  })
})

const summary = computed(() => visibleRows.value.reduce((result, row) => {
  if (['queued', 'running', 'processing'].includes(row.status)) result.processing += 1
  else if (['draft', 'review', 'rejected', 'succeeded'].includes(row.status)) result.review += 1
  else if (['failed', 'canceled', 'timed_out'].includes(row.status)) result.failed += 1
  else result.completed += 1
  return result
}, { processing: 0, review: 0, completed: 0, failed: 0 }))

function apiErrorMessage(error, fallback) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return error?.message || fallback
}

function quoteStatus(status, history = null) {
  if (history) return { status: 'completed', label: '已确认' }
  const labels = {
    queued: '排队中',
    running: '处理中',
    succeeded: '待预审',
    failed: '失败',
    canceled: '已取消',
    timed_out: '已超时',
  }
  return { status: status || 'processing', label: labels[status] || '处理中' }
}

function historyStatus(row) {
  if (row.record_type === 'preview_draft') return { status: 'draft', label: '预审草稿' }
  if (row.record_type === 'rejected') return { status: 'rejected', label: '待补充' }
  return { status: 'completed', label: row.pushed_to_dingtalk ? '已下发' : '已确认' }
}

function budgetStatus(row) {
  const importStatus = row.latest_import?.status
  if (!row.import_count) return { status: 'draft', label: '待导入清单' }
  if (['uploaded', 'parsing', 'parsed'].includes(importStatus)) return { status: 'review', label: '待确认清单' }
  if (['failed'].includes(importStatus)) return { status: 'failed', label: '导入失败' }
  if (row.workspace_status === 'archived') return { status: 'archived', label: '已归档' }
  return { status: 'completed', label: '项目计价中' }
}

function normalizeJob(row) {
  const mapped = quoteStatus(row.status, row.history)
  return {
    key: `job-${row.job_id}`,
    source: 'job',
    sourceLabel: '报价任务',
    identifier: row.job_number || row.job_id,
    title: row.request_summary || row.message_preview || row.source_file_name || '未命名报价',
    subtitle: [row.job_number || row.job_id, row.source_file_name].filter(Boolean).join(' · '),
    clientName: row.client_inquiry?.client_name || '—',
    status: mapped.status,
    statusLabel: mapped.label,
    itemCount: row.result_item_count,
    totalAmount: row.result_total_amount,
    updatedAt: row.updated_at || row.created_at || '—',
    sourceFileName: row.source_file_name,
    jobId: row.job_id,
    jobNumber: row.job_number,
    traceId: row.trace_id,
    actionLabel: ['failed', 'canceled', 'timed_out'].includes(row.status) ? '查看任务' : '继续处理',
  }
}

function normalizeHistory(row) {
  const mapped = historyStatus(row)
  return {
    key: `history-${row.id}`,
    source: 'history',
    sourceLabel: '报价记录',
    identifier: row.quote_job_number || row.quote_id || row.id,
    title: row.display_title || row.project_summary || '历史报价',
    subtitle: [row.quote_job_number, row.source_file_name].filter(Boolean).join(' · '),
    clientName: row.client_inquiry?.client_name || '—',
    status: mapped.status,
    statusLabel: mapped.label,
    itemCount: row.item_count,
    totalAmount: row.total_amount,
    updatedAt: row.updated_at || row.created_at || '—',
    sourceFileName: row.source_file_name,
    jobId: row.quote_job_id,
    jobNumber: row.quote_job_number,
    traceId: row.trace_id,
    actionLabel: row.can_edit_preview_draft ? '继续预审' : '查看报价',
  }
}

function normalizeBudget(row) {
  const mapped = budgetStatus(row)
  return {
    key: `budget-${row.project_id}`,
    source: 'budget',
    sourceLabel: '预算项目',
    identifier: row.project_code || row.project_id,
    title: row.name || '未命名项目',
    subtitle: [row.project_code, row.latest_import?.source_file_name].filter(Boolean).join(' · '),
    clientName: row.client_name || '—',
    status: mapped.status,
    statusLabel: mapped.label,
    itemCount: row.standard_item_count,
    totalAmount: null,
    updatedAt: row.updated_at || row.created_at || '—',
    sourceFileName: row.latest_import?.source_file_name,
    projectId: row.project_id,
    actionLabel: '进入项目',
  }
}

async function loadRows() {
  loading.value = true
  try {
    const requests = [
      listQuoteJobs({ page: 1, page_size: 50 }),
      listQuoteHistory({ page: 1, page_size: 50 }),
    ]
    if (props.canViewBudgetProjects) {
      requests.push(listBudgetProjects({ page: 1, page_size: 50 }))
    }
    const [jobs, history, budgets] = await Promise.all(requests)
    const historyJobIds = new Set((history.items || []).map((row) => row.quote_job_id).filter(Boolean))
    const jobRows = (jobs.items || [])
      .filter((row) => !historyJobIds.has(row.job_id))
      .map(normalizeJob)
    const historyRows = (history.items || []).map(normalizeHistory)
    const budgetRows = (budgets?.items || []).map(normalizeBudget)
    rows.value = [...jobRows, ...historyRows, ...budgetRows].sort(
      (left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')),
    )
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目报价列表加载失败'))
  } finally {
    loading.value = false
  }
}

function selectFile(event) {
  form.file = event.target.files?.[0] || null
}

function resetForm() {
  form.projectName = ''
  form.clientName = ''
  form.message = ''
  form.file = null
  if (fileInput.value) fileInput.value.value = ''
}

function handoffJob(job) {
  try {
    window.sessionStorage.setItem('aimo_quote_job_handoff', JSON.stringify({
      quote_job_id: job.job_id,
      quote_job_number: job.job_number || '',
      trace_id: job.trace_id || '',
      source: 'project_quotes',
      source_file_name: form.file?.name || '',
      created_at: new Date().toISOString(),
    }))
  } catch (error) {
    console.warn('quote job handoff storage failed', error)
  }
  const params = new URLSearchParams({
    entry: 'new-quote',
    mode: 'quick',
    quote_job_id: job.job_id,
    quote_job_number: job.job_number || '',
    from: 'project_quotes',
  })
  if (job.trace_id) params.set('trace_id', job.trace_id)
  window.location.href = `/index.html?${params.toString()}`
}

async function submitQuote() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  try {
    if (useBudgetProjectFlow.value) {
      const project = await createBudgetProject({
        name: form.projectName.trim(),
        client_name: form.clientName.trim() || null,
        description: form.message.trim() || null,
      })
      try {
        await uploadBudgetProjectWorkbook(project.project_id, form.file)
      } catch (error) {
        ElMessage.warning(`项目已创建，但 Excel 导入失败：${apiErrorMessage(error, '请进入项目后重试')}`)
      }
      emit('navigate', `/admin/budget-projects/${project.project_id}`)
      return
    }
    const job = await createQuoteJob({
      message: form.message,
      file: form.file,
      projectName: form.projectName,
      clientName: form.clientName,
    })
    ElMessage.success('报价任务已创建，正在进入预审工作台')
    handoffJob(job)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目报价创建失败'))
  } finally {
    submitting.value = false
  }
}

function openRow(row) {
  if (row.source === 'budget' && row.projectId) {
    emit('navigate', `/admin/budget-projects/${row.projectId}`)
    return
  }
  if (row.jobId) {
    handoffJob({
      job_id: row.jobId,
      job_number: row.jobNumber || '',
      trace_id: row.traceId || '',
    })
    return
  }
  emit('open-chat')
}

function formatAmount(value) {
  if (value === null || value === undefined || value === '') return '—'
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '—'
  return `¥${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function statusTagType(status) {
  if (['failed', 'timed_out'].includes(status)) return 'danger'
  if (['draft', 'review', 'rejected', 'succeeded'].includes(status)) return 'warning'
  if (['queued', 'running', 'processing'].includes(status)) return 'primary'
  if (status === 'canceled' || status === 'archived') return 'info'
  return 'success'
}

function sourceTagType(source) {
  if (source === 'budget') return 'success'
  if (source === 'history') return 'info'
  return 'primary'
}

watch(() => props.createMode, (createMode) => {
  if (!createMode) loadRows()
})

onMounted(() => {
  if (!props.createMode) loadRows()
})
</script>

<style scoped>
.project-quotes-shell {
  display: grid;
  gap: 20px;
}

.project-quotes-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.project-quotes-heading h2 {
  margin: 4px 0 8px;
  color: #172033;
  font-size: 28px;
  letter-spacing: -0.02em;
}

.project-quotes-heading p {
  margin: 0;
  color: #667085;
}

.project-quotes-eyebrow {
  color: #3568d4 !important;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.project-quotes-heading-actions {
  display: flex;
  gap: 10px;
}

.dual-entry-banner,
.entry-mode-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 18px;
  border: 1px solid rgba(68, 110, 205, 0.2);
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(243, 247, 255, 0.96), rgba(255, 255, 255, 0.96));
}

.dual-entry-banner {
  justify-content: flex-start;
}

.dual-entry-banner p,
.entry-mode-notice span {
  display: block;
  margin: 4px 0 0;
  color: #667085;
  font-size: 13px;
}

.dual-entry-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #4d76d7;
  box-shadow: 0 0 0 6px rgba(77, 118, 215, 0.12);
}

.project-quote-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.project-quote-stats article {
  padding: 18px;
  border: 1px solid #e7eaf0;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(36, 48, 74, 0.05);
}

.project-quote-stats span {
  display: block;
  color: #7a8498;
  font-size: 13px;
}

.project-quote-stats strong {
  display: block;
  margin-top: 6px;
  color: #172033;
  font-size: 26px;
}

.project-quote-toolbar {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) 170px auto;
  gap: 12px;
  padding: 14px;
  border: 1px solid #e7eaf0;
  border-radius: 16px;
  background: #fff;
}

.project-quote-table,
.project-quote-form {
  padding: 18px;
  border: 1px solid #e7eaf0;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 12px 34px rgba(36, 48, 74, 0.05);
}

.project-quote-title {
  display: grid;
  gap: 5px;
}

.project-quote-title strong {
  color: #26324a;
}

.project-quote-title span {
  color: #8a93a5;
  font-size: 12px;
}

.project-quote-form {
  max-width: 920px;
}

.project-quote-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.project-quote-upload {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 76px;
  padding: 14px 16px;
  border: 1px dashed #aebbd8;
  border-radius: 14px;
  background: #f8faff;
  cursor: pointer;
}

.project-quote-upload input {
  display: none;
}

.project-quote-upload-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: #eaf0ff;
  color: #3f68ca;
  font-size: 24px;
}

.project-quote-upload strong,
.project-quote-upload small {
  display: block;
}

.project-quote-upload small {
  margin-top: 4px;
  color: #7c879b;
}

.project-quote-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}

@media (max-width: 900px) {
  .project-quotes-heading,
  .entry-mode-notice {
    flex-direction: column;
  }

  .project-quote-stats,
  .project-quote-form-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .project-quote-toolbar {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .project-quote-stats,
  .project-quote-form-grid {
    grid-template-columns: 1fr;
  }

  .project-quotes-heading-actions {
    width: 100%;
    flex-direction: column;
  }
}
</style>
