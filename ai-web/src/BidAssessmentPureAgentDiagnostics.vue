<template>
  <div class="pa-diagnostics-page">
    <header class="diagnostics-hero">
      <div>
        <p class="diagnostics-eyebrow">Pure Agent · Control-plane Ledger</p>
        <h2>研判 Agent 管理与诊断</h2>
        <p>只读查看脱敏运行事实。此页面不能驱动 Agent、重试、恢复、取消或修改任何 Task。</p>
      </div>
      <div class="hero-actions">
        <el-tag type="warning" effect="plain">本地开发 · 默认关闭</el-tag>
        <el-button :loading="loadingTasks" plain @click="loadTasks">刷新</el-button>
      </div>
    </header>

    <el-alert
      class="diagnostic-boundary"
      type="info"
      show-icon
      :closable="false"
      title="诊断投影不包含思维链、Prompt、Tool 参数/结果正文、权限凭证、Resume Token、Effect Key、Provider 回执或原始异常。"
    />

    <el-alert
      v-if="pageError"
      class="diagnostic-boundary"
      type="error"
      show-icon
      :closable="true"
      :title="pageError"
      @close="pageError = ''"
    />

    <section class="status-grid">
      <article v-for="card in statusCards" :key="card.key" class="status-card">
        <span>{{ card.label }}</span>
        <strong>{{ card.value }}</strong>
      </article>
    </section>

    <section class="diagnostic-card task-ledger-card">
      <header class="section-heading">
        <div>
          <span>Task Ledger</span>
          <small>按更新时间倒序，只展示管理定位所需引用和计数</small>
        </div>
        <div class="task-filters">
          <el-select v-model="filters.status" clearable placeholder="全部状态" @change="applyFilters">
            <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-input
            v-model="filters.task_ref"
            clearable
            placeholder="精确 Task Ref"
            @keyup.enter="applyFilters"
            @clear="applyFilters"
          />
          <el-button type="primary" plain @click="applyFilters">查询</el-button>
        </div>
      </header>

      <el-table
        v-loading="loadingTasks"
        :data="taskPage.items"
        row-key="task_ref"
        highlight-current-row
        @row-click="selectTask"
      >
        <el-table-column label="Task" min-width="180">
          <template #default="{ row }">
            <button class="ref-button" type="button" @click.stop="selectTask(row)">{{ compactRef(row.task_ref) }}</button>
            <small class="cell-subline">对话 {{ compactRef(row.conversation_ref) }}</small>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" effect="light">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="execution_mode" label="模式" width="90" />
        <el-table-column prop="state_version" label="State V" width="88" />
        <el-table-column label="活动" min-width="180">
          <template #default="{ row }">
            <span>{{ row.action_count }} Action · {{ row.call_count }} Call</span>
            <small class="cell-subline">{{ row.checkpoint_count }} Checkpoint · {{ row.budget_account_count }} Budget</small>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="155">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-if="taskPage.total > taskPage.page_size"
        class="task-pagination"
        background
        layout="prev, pager, next, total"
        :current-page="taskPage.page"
        :page-size="taskPage.page_size"
        :total="taskPage.total"
        @current-change="changePage"
      />
    </section>

    <section v-if="selectedTaskRef" class="diagnostic-card snapshot-card" v-loading="loadingSnapshot">
      <template v-if="snapshot">
        <header class="section-heading snapshot-heading">
          <div>
            <span>诊断快照 · {{ compactRef(snapshot.task.task_ref) }}</span>
            <small>生成于 {{ formatTime(snapshot.generated_at) }}</small>
          </div>
          <div class="snapshot-tags">
            <el-tag :type="statusTagType(snapshot.task.status)">{{ statusLabel(snapshot.task.status) }}</el-tag>
            <el-tag effect="plain">State V{{ snapshot.task.state_version }}</el-tag>
            <el-tag v-if="snapshot.task.has_open_action" type="warning" effect="plain">存在运行中 Action</el-tag>
          </div>
        </header>

        <el-alert
          v-if="snapshot.integrity_warnings.length"
          class="diagnostic-boundary"
          type="warning"
          show-icon
          :closable="false"
          :title="`Ledger 完整性提示：${snapshot.integrity_warnings.join('、')}`"
        />

        <section class="snapshot-summary">
          <article><span>执行模式</span><strong>{{ snapshot.task.execution_mode }}</strong></article>
          <article><span>Observation</span><strong>{{ snapshot.task.observation_count }}</strong></article>
          <article><span>Plan 版本</span><strong>{{ snapshot.task.plan_version_count }}</strong></article>
          <article><span>Context 快照</span><strong>{{ snapshot.task.context_snapshot_count }}</strong></article>
          <article><span>回答版本</span><strong>{{ snapshot.task.response_version_count }}</strong></article>
        </section>

        <el-tabs v-model="activeTab" class="diagnostic-tabs">
          <el-tab-pane label="State" name="state">
            <el-table :data="snapshot.state_trace" size="small">
              <el-table-column prop="transition_no" label="#" width="56" />
              <el-table-column prop="activity" label="活动" min-width="180" />
              <el-table-column label="版本" width="125">
                <template #default="{ row }">{{ row.state_version_before }} → {{ row.state_version_after }}</template>
              </el-table-column>
              <el-table-column label="状态" min-width="155">
                <template #default="{ row }">{{ row.status_before || '—' }} → {{ row.status_after }}</template>
              </el-table-column>
              <el-table-column prop="action_no" label="Action" width="90">
                <template #default="{ row }">{{ row.action_no ? `A${row.action_no}` : '—' }}</template>
              </el-table-column>
              <el-table-column label="时间" min-width="155">
                <template #default="{ row }">{{ formatTime(row.occurred_at) }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`Calls (${snapshot.call_trace.length})`" name="calls">
            <el-empty v-if="!snapshot.call_trace.length" description="暂无已持久化 Call" />
            <el-table v-else :data="snapshot.call_trace" size="small">
              <el-table-column prop="call_no" label="#" width="56" />
              <el-table-column prop="kind" label="类型" width="80" />
              <el-table-column prop="operation" label="操作" min-width="165" />
              <el-table-column prop="status" label="状态" width="105" />
              <el-table-column prop="guard_outcome" label="Guard" width="115" />
              <el-table-column label="Token / 成本" min-width="160">
                <template #default="{ row }">
                  {{ valueOrDash(row.input_tokens) }} / {{ valueOrDash(row.output_tokens) }}
                  <small class="cell-subline">{{ valueOrDash(row.cost_micro_usd) }} μUSD</small>
                </template>
              </el-table-column>
              <el-table-column label="耗时" width="105">
                <template #default="{ row }">{{ row.duration_ms == null ? '—' : `${row.duration_ms} ms` }}</template>
              </el-table-column>
              <el-table-column prop="error_class" label="错误分类" min-width="140">
                <template #default="{ row }">{{ row.error_class || '—' }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`Budget (${snapshot.budget_trace.length})`" name="budget">
            <el-empty v-if="!snapshot.budget_trace.length" description="暂无预算账户" />
            <div v-else class="budget-grid">
              <article v-for="account in snapshot.budget_trace" :key="account.resource_type" class="budget-account">
                <header>
                  <div><strong>{{ account.resource_type }}</strong><small>{{ account.unit }}</small></div>
                  <b>{{ account.utilization_percent }}%</b>
                </header>
                <el-progress :percentage="account.utilization_percent" :stroke-width="8" />
                <div class="budget-values">
                  <span>上限 {{ account.limit_amount }}</span>
                  <span>预留 {{ account.reserved_amount }}</span>
                  <span>实际 {{ account.actual_amount }}</span>
                  <span>剩余 {{ account.remaining_amount }}</span>
                </div>
                <details v-if="account.entries.length">
                  <summary>{{ account.entries.length }} 条预算流水</summary>
                  <div v-for="entry in account.entries" :key="entry.entry_no" class="budget-entry">
                    <span>#{{ entry.entry_no }} {{ entry.kind }}</span>
                    <b>{{ entry.amount }}</b>
                    <small>reserved {{ entry.reserved_after }} · actual {{ entry.actual_after }}</small>
                  </div>
                </details>
              </article>
            </div>
          </el-tab-pane>

          <el-tab-pane label="Loop" name="loop">
            <el-alert
              type="info"
              :closable="false"
              title="首版只显示精确 Action/Result 指纹重复；语义无进展 Guard 决策未持久化时不会推测。"
            />
            <section class="loop-summary">
              <span>精确重复 <b>{{ snapshot.loop_trace.exact_repeat_count }}</b></span>
              <span>结果重复 <b>{{ snapshot.loop_trace.repeated_result_count }}</b></span>
              <span>迟到隔离 <b>{{ snapshot.loop_trace.ignored_late_count }}</b></span>
            </section>
            <el-table :data="snapshot.loop_trace.actions" size="small">
              <el-table-column prop="action_no" label="Action" width="80" />
              <el-table-column prop="action_type" label="类型" min-width="170" />
              <el-table-column prop="status" label="状态" width="110" />
              <el-table-column label="精确重复" min-width="130">
                <template #default="{ row }">{{ row.exact_repeat_of_action_no ? `A${row.exact_repeat_of_action_no}` : '—' }}</template>
              </el-table-column>
              <el-table-column label="结果信号" min-width="170">
                <template #default="{ row }">
                  {{ row.repeats_prior_result ? '重复既有结果' : (row.produced_new_result_fingerprint ? '新结果指纹' : '无结果指纹') }}
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="Cancel / Recovery" name="recovery">
            <section class="cancel-recovery-grid">
              <article class="boundary-panel">
                <span>Cancellation Fence</span>
                <strong>{{ snapshot.cancellation_trace.present ? '已建立' : '未建立' }}</strong>
                <p>取消版本：{{ valueOrDash(snapshot.cancellation_trace.cancellation_state_version) }}</p>
                <p>迟到结果隔离：{{ snapshot.cancellation_trace.ignored_late_result_count }}</p>
                <p>取消 Effect：{{ snapshot.cancellation_trace.cancelled_effect_count }}</p>
                <small>取消原因正文和操作者标识已脱敏。</small>
              </article>
              <article class="boundary-panel">
                <span>Continuation</span>
                <strong>{{ snapshot.recovery_trace.active_checkpoint_present ? '存在活动 Checkpoint' : '无活动 Checkpoint' }}</strong>
                <p>历史/当前记录：{{ snapshot.recovery_trace.checkpoints.length }}</p>
                <small>页面不会执行恢复或领取 Lease。</small>
              </article>
            </section>
            <el-table :data="snapshot.recovery_trace.checkpoints" size="small">
              <el-table-column prop="checkpoint_no" label="#" width="56" />
              <el-table-column prop="status" label="状态" width="100" />
              <el-table-column prop="suspended_state_version" label="挂起 State V" width="120" />
              <el-table-column prop="effect_status" label="Effect" width="105" />
              <el-table-column prop="replay_policy" label="Replay" min-width="145" />
              <el-table-column prop="lease_state" label="Lease" width="100" />
              <el-table-column prop="recovery_claim_count" label="领取次数" width="100" />
              <el-table-column prop="disposition" label="安全处置" min-width="135" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="Redaction" name="redaction">
            <div class="redaction-list">
              <span v-for="field in snapshot.redacted_fields" :key="field">{{ field }}</span>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
      <el-empty v-else-if="!loadingSnapshot" description="请选择一个 Task 查看诊断快照" />
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  bidAssessmentPureAgentDiagnosticsApi,
  diagnosticErrorMessage,
  diagnosticResponseData,
} from './bidAssessmentPureAgentDiagnosticsApi'

const PAGE_SIZE = 20
const loadingTasks = ref(false)
const loadingSnapshot = ref(false)
const pageError = ref('')
const selectedTaskRef = ref('')
const snapshot = ref(null)
const activeTab = ref('state')
const filters = reactive({ status: '', task_ref: '' })
const taskPage = reactive({ items: [], page: 1, page_size: PAGE_SIZE, total: 0, status_counts: {} })

const statusOptions = [
  { value: 'running', label: '运行中' },
  { value: 'pending', label: '等待输入' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]
const statusCards = computed(() => statusOptions.map((item) => ({
  key: item.value,
  label: item.label,
  value: taskPage.status_counts?.[item.value] || 0,
})))

function compactRef(value) {
  const text = String(value || '')
  if (text.length <= 18) return text || '—'
  return `${text.slice(0, 8)}…${text.slice(-6)}`
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

function valueOrDash(value) {
  return value == null ? '—' : value
}

function statusLabel(value) {
  return statusOptions.find((item) => item.value === value)?.label || value || '—'
}

function statusTagType(value) {
  return ({ running: 'primary', pending: 'warning', completed: 'success', failed: 'danger', cancelled: 'info' })[value] || 'info'
}

async function loadTasks() {
  loadingTasks.value = true
  pageError.value = ''
  try {
    const response = await bidAssessmentPureAgentDiagnosticsApi.tasks({
      status: filters.status || undefined,
      task_ref: filters.task_ref.trim() || undefined,
      page: taskPage.page,
      page_size: PAGE_SIZE,
    })
    const data = diagnosticResponseData(response) || {}
    Object.assign(taskPage, {
      items: data.items || [],
      page: data.page || 1,
      page_size: data.page_size || PAGE_SIZE,
      total: data.total || 0,
      status_counts: data.status_counts || {},
    })
    const selectedStillVisible = taskPage.items.some((item) => item.task_ref === selectedTaskRef.value)
    if (!selectedStillVisible && taskPage.items.length) await selectTask(taskPage.items[0])
    if (!taskPage.items.length) {
      selectedTaskRef.value = ''
      snapshot.value = null
    }
  } catch (error) {
    pageError.value = diagnosticErrorMessage(error)
  } finally {
    loadingTasks.value = false
  }
}

async function selectTask(row) {
  if (!row?.task_ref) return
  selectedTaskRef.value = row.task_ref
  loadingSnapshot.value = true
  pageError.value = ''
  try {
    const response = await bidAssessmentPureAgentDiagnosticsApi.snapshot(row.task_ref)
    snapshot.value = diagnosticResponseData(response)
  } catch (error) {
    snapshot.value = null
    pageError.value = diagnosticErrorMessage(error)
  } finally {
    loadingSnapshot.value = false
  }
}

function applyFilters() {
  taskPage.page = 1
  loadTasks()
}

function changePage(page) {
  taskPage.page = page
  loadTasks()
}

onMounted(loadTasks)
</script>

<style scoped>
.pa-diagnostics-page { display: grid; gap: 18px; color: #172033; }
.diagnostics-hero, .section-heading, .snapshot-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.diagnostics-hero { padding: 24px 26px; border: 1px solid #dfe6ef; border-radius: 22px; background: linear-gradient(135deg, #fff 0%, #f5f8fc 100%); }
.diagnostics-eyebrow { margin: 0 0 7px; color: #67758a; font-size: 12px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.diagnostics-hero h2 { margin: 0; font-size: 27px; }
.diagnostics-hero p:not(.diagnostics-eyebrow) { max-width: 760px; margin: 9px 0 0; color: #667085; line-height: 1.7; }
.hero-actions, .snapshot-tags, .task-filters { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.diagnostic-boundary { border-radius: 14px; }
.status-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.status-card, .diagnostic-card { border: 1px solid #e1e7ef; background: #fff; box-shadow: 0 10px 32px rgba(31, 45, 61, .05); }
.status-card { padding: 16px 18px; border-radius: 16px; }
.status-card span { color: #78869a; font-size: 13px; }
.status-card strong { display: block; margin-top: 6px; font-size: 26px; }
.diagnostic-card { padding: 20px; border-radius: 20px; }
.section-heading { margin-bottom: 16px; }
.section-heading > div:first-child { display: grid; gap: 4px; }
.section-heading span { font-weight: 700; }
.section-heading small, .cell-subline { color: #8793a5; }
.task-filters .el-select { width: 140px; }
.task-filters .el-input { width: 260px; }
.ref-button { padding: 0; border: 0; background: none; color: #315f98; font: inherit; font-weight: 700; cursor: pointer; }
.cell-subline { display: block; margin-top: 3px; font-size: 12px; }
.task-pagination { justify-content: flex-end; margin-top: 16px; }
.snapshot-summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin: 14px 0 4px; }
.snapshot-summary article { padding: 13px 14px; border-radius: 13px; background: #f6f8fb; }
.snapshot-summary span { color: #7a8799; font-size: 12px; }
.snapshot-summary strong { display: block; margin-top: 4px; }
.diagnostic-tabs { margin-top: 18px; }
.budget-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.budget-account, .boundary-panel { padding: 16px; border: 1px solid #e3e8ef; border-radius: 15px; background: #fafbfd; }
.budget-account header { display: flex; justify-content: space-between; margin-bottom: 10px; }
.budget-account header div { display: grid; }
.budget-account small { color: #8490a1; }
.budget-values { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin: 12px 0; color: #5e6a7b; font-size: 13px; }
.budget-entry { display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; padding: 8px 0; border-top: 1px solid #e5eaf0; }
.budget-entry small { grid-column: 1 / -1; }
.loop-summary { display: flex; gap: 24px; margin: 14px 0; color: #5f6d80; }
.cancel-recovery-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.boundary-panel { display: grid; gap: 6px; }
.boundary-panel span, .boundary-panel p, .boundary-panel small { margin: 0; color: #718095; }
.redaction-list { display: flex; flex-wrap: wrap; gap: 9px; }
.redaction-list span { padding: 7px 10px; border-radius: 999px; background: #f1f4f8; color: #5f6d7e; font-size: 12px; }
@media (max-width: 1050px) {
  .status-grid, .snapshot-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .budget-grid, .cancel-recovery-grid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .diagnostics-hero, .section-heading { flex-direction: column; }
  .status-grid, .snapshot-summary { grid-template-columns: 1fr; }
  .task-filters, .task-filters .el-select, .task-filters .el-input { width: 100%; }
}
</style>
