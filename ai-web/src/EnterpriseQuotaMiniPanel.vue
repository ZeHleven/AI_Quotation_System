<template>
  <aside v-show="modelValue" class="enterprise-quota-mini" aria-label="悬浮企业定额库">
    <header class="enterprise-quota-mini__header">
      <div>
        <strong>{{ title }}</strong>
        <small>当前生效：{{ activeVersionLabel }}</small>
      </div>
      <div class="enterprise-quota-mini__header-actions">
        <el-button text aria-label="关闭企业定额库" @click.stop="close">关闭</el-button>
      </div>
    </header>

    <div class="enterprise-quota-mini__toolbar">
      <el-input
        v-model="keyword"
        clearable
        size="small"
        placeholder="搜索编码、名称、工作内容或规格"
        @keyup.enter="search"
        @clear="search"
      />
      <el-button size="small" type="primary" plain :loading="loading" @click="search">查询</el-button>
    </div>

    <el-alert
      v-if="versionMismatch"
      class="enterprise-quota-mini__alert"
      type="warning"
      :closable="false"
      show-icon
      title="当前生效企业定额版本读取异常，暂不能选择"
    />

    <el-table
      v-loading="loading"
      :data="items"
      size="small"
      class="enterprise-quota-mini__table"
      max-height="410"
      empty-text="当前生效企业定额库没有匹配条目"
      highlight-current-row
      @row-click="previewItem"
    >
      <el-table-column v-if="selectable" label="选" width="42" align="center">
        <template #default="{ row }">
          <el-checkbox
            :model-value="isSelected(row)"
            :disabled="!canSelect(row)"
            :aria-label="`选择定额 ${row.quota_code || row.item_name}`"
            @click.stop
            @change="(checked) => choose(row, checked)"
          />
        </template>
      </el-table-column>
      <el-table-column label="定额" min-width="150">
        <template #default="{ row }">
          <div class="enterprise-quota-mini__primary">
            <strong>{{ row.quota_code || '无编码' }} · {{ row.item_name || '未命名定额' }}</strong>
            <small>{{ row.section_name || '未分类' }}</small>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="工作内容 / 规格" min-width="150">
        <template #default="{ row }">
          <div class="enterprise-quota-mini__compact-text" :title="quotaDescription(row)">
            {{ quotaDescription(row) || '—' }}
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="unit" label="单位" width="54" align="center" />
      <el-table-column label="单价" width="82" align="right">
        <template #default="{ row }"><strong>{{ formatMoney(row.unit_price) }}</strong></template>
      </el-table-column>
    </el-table>

    <footer class="enterprise-quota-mini__footer">
      <span>共 {{ total }} 条<span v-if="multiple"> · 已选 {{ selectedRows.length }} 条</span></span>
      <el-pagination
        v-if="total > pageSize"
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        size="small"
        @current-change="loadItems"
      />
      <el-button
        v-if="selectable"
        size="small"
        type="primary"
        :disabled="!hasSelection || versionMismatch"
        @click="confirmSelection"
      >
        {{ confirmLabel }}<template v-if="multiple && selectedRows.length">（{{ selectedRows.length }}）</template>
      </el-button>
    </footer>
  </aside>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  enterpriseQuotaV2Api,
  quotaV2Data,
  quotaV2ErrorMessage,
  quotaV2Items,
} from './enterpriseQuotaV2Api'
import { budgetProjectApi } from './budgetProjectApi'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  selectable: { type: Boolean, default: false },
  multiple: { type: Boolean, default: false },
  selectedItemId: { type: [Number, String], default: null },
  selectedItemIds: { type: Array, default: () => [] },
  disabledItemIds: { type: Array, default: () => [] },
  expectedVersionId: { type: [Number, String], default: null },
  initialKeyword: { type: String, default: '' },
  title: { type: String, default: '企业定额库' },
  confirmLabel: { type: String, default: '勾选替换' },
  projectId: { type: [Number, String], default: null },
  pricingMode: { type: String, default: 'enterprise_ai' },
})
const emit = defineEmits(['update:modelValue', 'select'])

const loading = ref(false)
const keyword = ref('')
const items = ref([])
const activeVersion = ref(null)
const total = ref(0)
const page = ref(1)
const pageSize = 12
const selectedId = ref(null)
const selectedRows = ref([])
const selectedItem = computed(() => (
  selectedRows.value.find((item) => Number(item.id) === Number(selectedId.value))
  || items.value.find((item) => Number(item.id) === Number(selectedId.value))
  || null
))
const selectedIdSet = computed(() => new Set(selectedRows.value.map((item) => Number(item.id))))
const disabledIdSet = computed(() => new Set((props.disabledItemIds || []).map((value) => Number(value))))
const hasSelection = computed(() => (props.multiple ? selectedRows.value.length > 0 : Boolean(selectedItem.value)))
const activeVersionId = computed(() => Number(activeVersion.value?.id || items.value[0]?.version_id || 0) || null)
const activeVersionLabel = computed(() => (
  activeVersion.value?.version_name
  || activeVersion.value?.version_code
  || (activeVersionId.value ? `版本 ${activeVersionId.value}` : '读取中')
))
const versionMismatch = computed(() => Boolean(
  props.selectable
  && props.expectedVersionId
  && activeVersionId.value
  && Number(props.expectedVersionId) !== Number(activeVersionId.value)
))

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    keyword.value = props.initialKeyword || ''
    selectedId.value = props.selectedItemId || null
    selectedRows.value = props.multiple
      ? (props.selectedItemIds || []).map((id) => ({ id }))
      : []
    page.value = 1
    loadItems()
  },
  { immediate: true },
)

watch(() => props.selectedItemId, (value) => { selectedId.value = value || null })
watch(
  () => props.selectedItemIds,
  (values) => {
    if (!props.multiple || !props.modelValue) return
    selectedRows.value = (values || []).map((id) => (
      selectedRows.value.find((item) => Number(item.id) === Number(id)) || { id }
    ))
  },
  { deep: true },
)

watch(
  () => props.initialKeyword,
  (value) => {
    if (!props.modelValue) return
    keyword.value = value || ''
    page.value = 1
    loadItems()
  },
)

async function loadItems() {
  if (!props.modelValue) return
  loading.value = true
  try {
    const params = {
      keyword: keyword.value || undefined,
      page: page.value,
      page_size: pageSize,
    }
    const response = props.projectId
      ? await budgetProjectApi.projectEnterpriseQuotaItems(props.projectId, {
        ...params,
        pricing_mode: props.pricingMode,
      })
      : await enterpriseQuotaV2Api.masterItems(params)
    const data = quotaV2Data(response) || {}
    items.value = quotaV2Items(response)
    activeVersion.value = response?.data?.active_version || data?.active_version || null
    total.value = Number(response?.data?.total ?? data.total ?? items.value.length) || 0
  } catch (error) {
    items.value = []
    activeVersion.value = null
    total.value = 0
    ElMessage.error(quotaV2ErrorMessage(error, '企业定额库读取失败'))
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  loadItems()
}

function close() {
  emit('update:modelValue', false)
}

function canSelect(row) {
  return !disabledIdSet.value.has(Number(row.id))
    && (!props.expectedVersionId || Number(row.version_id) === Number(props.expectedVersionId))
}

function choose(row, checked) {
  if (props.multiple) {
    selectedRows.value = checked
      ? [...selectedRows.value.filter((item) => Number(item.id) !== Number(row.id)), row]
      : selectedRows.value.filter((item) => Number(item.id) !== Number(row.id))
    return
  }
  selectedId.value = checked ? row.id : null
}

function previewItem(row) {
  if (!props.selectable || !canSelect(row)) return
  if (props.multiple) {
    choose(row, !selectedIdSet.value.has(Number(row.id)))
    return
  }
  selectedId.value = row.id
}

function confirmSelection() {
  if (!hasSelection.value || versionMismatch.value) return
  emit('select', props.multiple ? selectedRows.value : selectedItem.value)
}

function isSelected(row) {
  return props.multiple
    ? selectedIdSet.value.has(Number(row.id))
    : Number(selectedId.value) === Number(row.id)
}

function quotaDescription(row) {
  return [row.work_content, row.specification, row.worker_or_subtype].filter(Boolean).join(' · ')
}

function formatMoney(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
</script>

<style scoped>
.enterprise-quota-mini{position:fixed;z-index:2100;top:88px;right:20px;width:min(660px,calc(100vw - 40px));max-height:calc(100vh - 112px);padding:12px;border:1px solid rgba(37,99,235,.28);border-radius:16px;background:rgba(255,255,255,.98);box-shadow:0 24px 70px rgba(15,23,42,.2);backdrop-filter:blur(16px)}
.enterprise-quota-mini__header,.enterprise-quota-mini__header-actions,.enterprise-quota-mini__toolbar,.enterprise-quota-mini__footer{display:flex;align-items:center;gap:8px}.enterprise-quota-mini__header{justify-content:space-between;margin-bottom:10px}.enterprise-quota-mini__header>div:first-child,.enterprise-quota-mini__primary{display:flex;min-width:0;flex-direction:column;gap:2px}.enterprise-quota-mini__header small,.enterprise-quota-mini__primary small,.enterprise-quota-mini__footer{color:#64748b;font-size:11px}.enterprise-quota-mini__toolbar{margin-bottom:8px}.enterprise-quota-mini__alert{margin-bottom:8px}.enterprise-quota-mini__table{width:100%}.enterprise-quota-mini__table:deep(.el-table__cell){padding:4px 0;font-size:11px}.enterprise-quota-mini__primary strong,.enterprise-quota-mini__compact-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.enterprise-quota-mini__primary strong{font-size:11px}.enterprise-quota-mini__compact-text{line-height:1.35}.enterprise-quota-mini__footer{justify-content:space-between;margin-top:8px}.enterprise-quota-mini__footer .el-button{margin-left:auto}@media(max-width:760px){.enterprise-quota-mini{top:68px;right:8px;width:calc(100vw - 16px);max-height:calc(100vh - 80px)}}
</style>
