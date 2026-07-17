<template>
  <div class="account-quota-page">
    <div class="content-heading">
      <div>
        <p class="eyebrow">账户成本资产</p>
        <h2>账户定额库</h2>
      </div>
      <div v-if="!moduleUnavailable" class="heading-actions">
        <el-button :icon="Plus" type="primary" @click="openCreate">新建定额</el-button>
        <el-button :icon="Refresh" plain :loading="loading" @click="loadItems">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="moduleUnavailable"
      type="info"
      show-icon
      :closable="false"
      title="账户定额库尚未开启"
      description="当前环境未开放账户定额模块，不会读取或修改企业定额主库。"
    />

    <template v-else>
      <section class="quota-boundary-card">
        <div>
          <strong>仅沉淀当前账户认可的成本价格</strong>
          <span>人工改价可从项目计价草稿同步进来，并且一律先保存为账户草稿；active 只为下一阶段账户定额匹配做准备。企业定额 active 主库始终保持独立。</span>
        </div>
        <el-tag type="primary" effect="plain">账户隔离</el-tag>
      </section>

      <div class="quota-filters">
        <el-input
          v-model="filters.keyword"
          :prefix-icon="Search"
          clearable
          placeholder="搜索定额编码、项目名称或项目特征"
          @keyup.enter="searchItems"
          @clear="searchItems"
        />
        <el-select v-model="filters.status" clearable placeholder="全部状态" @change="searchItems">
          <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-button type="primary" plain @click="searchItems">查询</el-button>
        <el-button plain @click="resetFilters">重置</el-button>
      </div>

      <section class="quota-panel">
        <div class="quota-title">
          <div>
            <strong>定额明细</strong>
            <small>价格以 6 位小数保存，列表同时展示业务友好的 2 位金额</small>
          </div>
          <span>共 {{ total }} 条</span>
        </div>

        <el-table
          v-loading="loading"
          :data="items"
          :row-key="quotaIdentifier"
          class="users-table"
          empty-text="当前账户暂无定额"
        >
          <el-table-column label="定额编码" width="150">
            <template #default="{ row }"><strong>{{ row.quota_code || '—' }}</strong></template>
          </el-table-column>
          <el-table-column label="项目名称 / 特征" min-width="290">
            <template #default="{ row }">
              <div class="quota-name">
                <strong>{{ row.item_name || '未命名项目' }}</strong>
                <small>{{ row.item_features || '未填写项目特征' }}</small>
                <small v-if="row.spec">规格：{{ row.spec }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="unit" label="单位" width="85" />
          <el-table-column label="账户单价" width="175" align="right">
            <template #default="{ row }">
              <div class="quota-price">
                <strong>¥{{ formatPriceFriendly(row.unit_price) }}</strong>
                <small>精确值 {{ formatPriceExact(row.unit_price) }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusTag(row.status)" effect="plain">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="修订" width="90" align="center">
            <template #default="{ row }">R{{ rowRevision(row) }}</template>
          </el-table-column>
          <el-table-column label="更新时间" width="170">
            <template #default="{ row }">{{ formatDate(row.updated_at || row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="330" fixed="right">
            <template #default="{ row }">
              <div class="quota-actions">
                <el-button size="small" plain @click="openHistory(row)">历史</el-button>
                <el-button v-if="row.status !== 'archived'" size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
                <el-button v-if="row.status === 'draft'" size="small" type="success" plain @click="changeStatus(row, 'active')">启用</el-button>
                <el-button v-if="row.status === 'active'" size="small" type="warning" plain @click="changeStatus(row, 'draft')">撤回</el-button>
                <el-button v-if="row.status !== 'archived'" size="small" type="danger" plain @click="changeStatus(row, 'archived')">归档</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="total > pageSize"
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="loadItems"
        />
      </section>
    </template>

    <el-dialog v-model="dialog.visible" :title="dialog.mode === 'edit' ? '编辑账户定额' : '新建账户定额'" width="640px" destroy-on-close>
      <el-alert
        v-if="dialog.conflict"
        class="quota-dialog-alert"
        type="warning"
        show-icon
        :closable="false"
        title="这条定额已被其他操作更新"
        description="当前表单不会覆盖最新数据。请保留需要的内容，关闭后重新打开定额再编辑。"
      />
      <el-form label-position="top" :model="dialog.form">
        <div class="quota-form-grid">
          <el-form-item label="定额编码（可选）">
            <el-input v-model="dialog.form.quota_code" maxlength="64" placeholder="例如 AC-QS-001" />
          </el-form-item>
          <el-form-item label="单位" required>
            <el-input v-model="dialog.form.unit" maxlength="24" placeholder="例如 ㎡、m、项" />
          </el-form-item>
        </div>
        <el-form-item label="项目名称" required>
          <el-input v-model="dialog.form.item_name" maxlength="255" placeholder="输入可识别、可复用的项目名称" />
        </el-form-item>
        <el-form-item label="项目特征">
          <el-input v-model="dialog.form.item_features" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="填写工艺、材质、规格、施工条件等匹配特征" />
        </el-form-item>
        <el-form-item label="规格（可选）">
          <el-input v-model="dialog.form.spec" type="textarea" :rows="2" maxlength="10000" show-word-limit placeholder="填写型号、尺寸或其他独立规格信息" />
        </el-form-item>
        <el-form-item label="账户单价（元）" required>
          <el-input v-model="dialog.form.unit_price" inputmode="decimal" maxlength="24" placeholder="最多 6 位小数">
            <template #append>元</template>
          </el-input>
          <small class="quota-form-tip">保存后精确值：{{ formPricePreview }}</small>
        </el-form-item>
        <el-form-item label="变更说明" required>
          <el-input v-model="dialog.form.reason" maxlength="255" placeholder="说明本次新增或调整依据" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.loading" :disabled="dialog.conflict" @click="saveItem">{{ dialogSaveLabel }}</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="history.visible" title="账户定额修订历史" size="760px">
      <div v-if="history.item" class="history-heading">
        <div>
          <strong>{{ history.item.item_name || '未命名项目' }}</strong>
          <span>{{ history.item.quota_code || '—' }} · 当前 R{{ rowRevision(history.item) }}</span>
        </div>
        <el-tag :type="statusTag(history.item.status)" effect="plain">{{ statusLabel(history.item.status) }}</el-tag>
      </div>
      <el-table v-loading="history.loading" :data="history.entries" :row-key="historyRowKey" class="users-table" empty-text="暂无修订历史">
        <el-table-column label="修订" width="80" align="center"><template #default="{ row }">R{{ historyRevision(row) }}</template></el-table-column>
        <el-table-column label="操作" width="105"><template #default="{ row }">{{ historyAction(row) }}</template></el-table-column>
        <el-table-column label="状态变化" width="145"><template #default="{ row }">{{ historyStatus(row) }}</template></el-table-column>
        <el-table-column label="单价" width="120" align="right"><template #default="{ row }">{{ historyPrice(row) }}</template></el-table-column>
        <el-table-column label="说明" min-width="170"><template #default="{ row }">{{ historyReason(row) }}</template></el-table-column>
        <el-table-column label="操作人" width="105"><template #default="{ row }">{{ historyActor(row) }}</template></el-table-column>
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ formatDate(row.created_at || row.occurred_at) }}</template></el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import {
  accountQuotaApi,
  accountQuotaApiErrorMessage,
  accountQuotaResponseData,
  accountQuotaResponseItems,
  accountQuotaResponseTotal,
} from './accountQuotaApi'

const props = defineProps({
  featureAvailable: { type: Boolean, default: false },
})

const statusOptions = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '已启用' },
  { value: 'archived', label: '已归档' },
]
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const featureDisabled = ref(false)
const filters = reactive({ keyword: '', status: '' })
const dialog = reactive({
  visible: false,
  loading: false,
  conflict: false,
  mode: 'create',
  identifier: null,
  form: emptyForm(),
})
const history = reactive({ visible: false, loading: false, item: null, entries: [] })

const moduleUnavailable = computed(() => !props.featureAvailable || featureDisabled.value)
const formPricePreview = computed(() => normalizePrice(dialog.form.unit_price) || '—')
const dialogSaveLabel = computed(() => dialog.mode === 'edit' ? '保存修改' : '保存为草稿')

function emptyForm() {
  return {
    quota_code: '',
    item_name: '',
    item_features: '',
    spec: '',
    unit: '',
    unit_price: '',
    reason: '',
    expected_revision: null,
  }
}

function quotaIdentifier(row) {
  return row?.id ?? row?.item_uuid ?? row?.uuid ?? row?.item_id ?? row?.account_quota_id
}

function rowRevision(row) {
  return Number(row?.revision ?? row?.item_revision ?? 1)
}

function statusLabel(value) {
  return ({ draft: '草稿', active: '已启用', archived: '已归档' })[value] || value || '—'
}

function statusTag(value) {
  return ({ draft: 'warning', active: 'success', archived: 'info' })[value] || 'info'
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function normalizePrice(value) {
  const text = String(value ?? '').trim()
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(text)) return ''
  const [integer, fraction = ''] = text.split('.')
  return `${integer}.${fraction.padEnd(6, '0')}`
}

function formatPriceExact(value) {
  return normalizePrice(value) || '—'
}

function formatPriceFriendly(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function isFeatureDisabled(error) {
  if (![404, 503].includes(error?.response?.status)) return false
  const detail = error?.response?.data?.detail
  const code = String(detail?.code || detail || error?.response?.data?.code || '').toLowerCase()
  return code.includes('feature') || code.includes('disabled') || code.includes('unavailable')
}

async function loadItems() {
  if (moduleUnavailable.value) return
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize }
    if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
    if (filters.status) params.status = filters.status
    const response = await accountQuotaApi.list(params)
    items.value = accountQuotaResponseItems(response)
    total.value = accountQuotaResponseTotal(response, items.value.length)
  } catch (error) {
    items.value = []
    total.value = 0
    if (isFeatureDisabled(error)) {
      featureDisabled.value = true
      return
    }
    ElMessage.error(accountQuotaApiErrorMessage(error, '账户定额加载失败'))
  } finally {
    loading.value = false
  }
}

function searchItems() {
  page.value = 1
  loadItems()
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  searchItems()
}

function openCreate() {
  dialog.mode = 'create'
  dialog.identifier = null
  dialog.conflict = false
  dialog.form = { ...emptyForm(), reason: '新建账户定额' }
  dialog.visible = true
}

function fillEditForm(item) {
  dialog.mode = 'edit'
  dialog.identifier = quotaIdentifier(item)
  dialog.conflict = false
  dialog.form = {
    quota_code: item.quota_code || '',
    item_name: item.item_name || '',
    item_features: item.item_features || '',
    spec: item.spec || '',
    unit: item.unit || '',
    unit_price: formatPriceExact(item.unit_price) === '—' ? '' : formatPriceExact(item.unit_price),
    reason: '',
    expected_revision: rowRevision(item),
  }
}

async function openEdit(row) {
  try {
    const response = await accountQuotaApi.detail(quotaIdentifier(row))
    fillEditForm(accountQuotaResponseData(response) || row)
    dialog.visible = true
  } catch (error) {
    ElMessage.error(accountQuotaApiErrorMessage(error, '账户定额详情加载失败'))
  }
}

function validateForm() {
  const form = dialog.form
  if (!form.item_name.trim() || !form.unit.trim()) {
    ElMessage.warning('请填写项目名称和单位')
    return false
  }
  const normalized = normalizePrice(form.unit_price)
  if (!normalized || Number(normalized) <= 0) {
    ElMessage.warning('账户单价必须大于 0，且最多保留 6 位小数')
    return false
  }
  if (!form.reason.trim()) {
    ElMessage.warning('请填写本次变更说明')
    return false
  }
  return true
}

function formPayload() {
  return {
    quota_code: dialog.form.quota_code.trim() || null,
    item_name: dialog.form.item_name.trim(),
    item_features: dialog.form.item_features.trim() || null,
    spec: dialog.form.spec.trim() || null,
    unit: dialog.form.unit.trim(),
    unit_price: normalizePrice(dialog.form.unit_price),
  }
}

async function handleEditConflict(error) {
  if (error?.response?.status !== 409) return false
  dialog.conflict = true
  await loadItems()
  await ElMessageBox.alert(
    '服务器上的定额已经产生新修订。为避免覆盖他人修改，本次保存已被阻止；当前表单内容仍会保留。',
    '修订冲突',
    { confirmButtonText: '知道了', type: 'warning' },
  )
  return true
}

async function saveItem() {
  if (!validateForm() || dialog.conflict) return
  dialog.loading = true
  try {
    if (dialog.mode === 'edit') {
      await accountQuotaApi.update(dialog.identifier, {
        ...formPayload(),
        expected_revision: dialog.form.expected_revision,
        reason: dialog.form.reason.trim(),
      })
      ElMessage.success('账户定额已更新并记录修订历史')
    } else {
      await accountQuotaApi.create({
        ...formPayload(),
        source: 'manual',
        reason: dialog.form.reason.trim(),
      })
      ElMessage.success('账户定额草稿已创建')
    }
    dialog.visible = false
    await loadItems()
  } catch (error) {
    if (!(await handleEditConflict(error))) {
      ElMessage.error(accountQuotaApiErrorMessage(error, '账户定额保存失败'))
    }
  } finally {
    dialog.loading = false
  }
}

async function changeStatus(row, targetStatus) {
  const action = ({ active: '启用', draft: '撤回为草稿', archived: '归档' })[targetStatus]
  const hint = targetStatus === 'active'
    ? '本阶段启用仅将定额标记为 active，为下一阶段账户定额匹配做准备；当前不会写入或影响计价草稿。请输入启用依据。'
    : targetStatus === 'archived'
      ? '归档后定额将冻结且不再参与匹配。请输入归档原因。'
      : '撤回后定额不再参与匹配。请输入撤回原因。'
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(hint, `${action}账户定额`, {
      inputPattern: /\S{2,}/,
      inputErrorMessage: '请填写至少 2 个字符的操作说明',
      confirmButtonText: `确认${action}`,
      cancelButtonText: '取消',
      type: targetStatus === 'archived' ? 'warning' : 'info',
    })
    reason = result.value.trim()
  } catch {
    return
  }
  try {
    await accountQuotaApi.changeStatus(quotaIdentifier(row), {
      target_status: targetStatus,
      expected_revision: rowRevision(row),
      reason,
    })
    ElMessage.success(`账户定额已${action}`)
    await loadItems()
  } catch (error) {
    if (error?.response?.status === 409) {
      await loadItems()
      ElMessage.warning('定额已产生新修订，列表已刷新；请核对最新状态后重试')
      return
    }
    ElMessage.error(accountQuotaApiErrorMessage(error, `${action}失败`))
  }
}

async function openHistory(row) {
  history.visible = true
  history.loading = true
  history.item = row
  history.entries = []
  try {
    const [detailResponse, historyResponse] = await Promise.all([
      accountQuotaApi.detail(quotaIdentifier(row)),
      accountQuotaApi.history(quotaIdentifier(row), { page: 1, page_size: 100 }),
    ])
    history.item = accountQuotaResponseData(detailResponse) || row
    history.entries = accountQuotaResponseItems(historyResponse)
  } catch (error) {
    ElMessage.error(accountQuotaApiErrorMessage(error, '修订历史加载失败'))
  } finally {
    history.loading = false
  }
}

function parseSnapshot(value) {
  if (!value) return {}
  if (typeof value === 'object') return value
  try {
    return JSON.parse(value)
  } catch {
    return {}
  }
}

function historySnapshot(row) {
  return parseSnapshot(row?.snapshot || row?.after_snapshot || row?.after || row?.data || row?.after_snapshot_json)
}

function historyRowKey(row, index) {
  return row?.id ?? row?.uuid ?? `${historyRevision(row)}-${row?.created_at || index}`
}

function historyRevision(row) {
  return Number(row?.revision ?? row?.item_revision ?? historySnapshot(row)?.revision ?? 1)
}

function historyAction(row) {
  const action = row?.action || row?.event_type || row?.operation || row?.change_type
  return ({ create: '新建', created: '新建', update: '编辑', updated: '编辑', status_change: '状态变更', status_changed: '状态变更', activate: '启用', withdraw: '撤回', archive: '归档', pricing_draft_synced: '报价草稿同步' })[action] || action || '修订'
}

function historyStatus(row) {
  const beforeSnapshot = parseSnapshot(row?.before_snapshot || row?.before || row?.before_snapshot_json)
  const before = row?.from_status || row?.previous_status || beforeSnapshot.status
  const after = row?.to_status || row?.target_status || historySnapshot(row)?.status || row?.status
  if (before && after && before !== after) return `${statusLabel(before)} → ${statusLabel(after)}`
  return statusLabel(after || before)
}

function historyPrice(row) {
  const value = row?.unit_price ?? historySnapshot(row)?.unit_price
  return formatPriceExact(value)
}

function historyReason(row) {
  return row?.reason || row?.note || row?.description || '—'
}

function historyActor(row) {
  return row?.actor_username || row?.created_by_name || row?.actor?.username || row?.username || (row?.actor_id != null ? `用户 #${row.actor_id}` : '—')
}

onMounted(loadItems)
</script>

<style scoped>
.quota-boundary-card{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px;padding:18px 20px;border:1px solid rgba(59,130,246,.16);border-radius:18px;background:linear-gradient(135deg,rgba(239,246,255,.94),rgba(255,255,255,.96));box-shadow:0 14px 34px rgba(15,23,42,.05)}
.quota-boundary-card>div,.quota-name,.quota-price,.history-heading>div{display:flex;flex-direction:column;gap:5px}.quota-boundary-card span,.quota-title small,.quota-title>span,.quota-name small,.quota-price small,.history-heading span,.quota-form-tip{color:#64748b}.quota-filters{display:grid;grid-template-columns:minmax(300px,1fr) 170px auto auto;gap:12px;margin-bottom:16px}.quota-panel{padding:20px;border:1px solid rgba(148,163,184,.22);border-radius:20px;background:rgba(255,255,255,.92);box-shadow:0 14px 34px rgba(15,23,42,.06)}.quota-title{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:16px}.quota-title>div{display:flex;flex-direction:column;gap:4px}.quota-name strong{color:#0f172a}.quota-name small{line-height:1.45;white-space:normal}.quota-price{align-items:flex-end}.quota-price strong{font-variant-numeric:tabular-nums;color:#0f172a}.quota-price small{font-size:11px;font-variant-numeric:tabular-nums}.quota-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.quota-actions :deep(.el-button+.el-button){margin-left:0}.el-pagination{justify-content:flex-end;margin-top:16px}.quota-form-grid{display:grid;grid-template-columns:1fr 160px;gap:14px}.quota-form-tip{display:block;margin-top:6px;font-size:12px;font-variant-numeric:tabular-nums}.quota-dialog-alert{margin-bottom:16px}.history-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;padding:16px 18px;border:1px solid rgba(148,163,184,.2);border-radius:16px;background:#f8fafc}
@media(max-width:900px){.quota-filters,.quota-form-grid{grid-template-columns:1fr}.quota-boundary-card{align-items:flex-start;flex-direction:column}.quota-panel{padding:14px}}
</style>
