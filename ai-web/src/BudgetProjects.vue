<template>
  <div v-if="moduleUnavailable">
    <div class="content-heading">
      <div><p class="eyebrow">预算工作台</p><h2>预算项目</h2></div>
    </div>
    <el-alert type="info" show-icon :closable="false" title="预算项目功能尚未开启" description="当前环境未开放预算项目模块，页面入口和写入操作均已停用。" />
  </div>

  <div v-else-if="!detailMode">
    <div class="content-heading">
      <div><p class="eyebrow">预算工作台</p><h2>预算项目</h2></div>
      <div class="heading-actions">
        <el-button v-if="canCreateProject" :icon="Plus" type="primary" @click="openProjectDialog()">新建项目</el-button>
        <el-button :icon="Refresh" plain :loading="loading" @click="loadProjects">刷新</el-button>
      </div>
    </div>
    <el-alert v-if="featureDisabled" type="info" show-icon :closable="false" title="预算项目功能尚未开启" />
    <template v-else>
      <div class="budget-filters">
        <el-input v-model="filters.keyword" clearable :prefix-icon="Search" placeholder="搜索项目名称、编号或客户" @keyup.enter="searchProjects" @clear="searchProjects" />
        <el-select v-model="filters.status" clearable placeholder="全部状态" @change="searchProjects">
          <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-button type="primary" plain @click="searchProjects">查询</el-button>
      </div>
      <section class="budget-panel">
        <div class="budget-title"><div><strong>项目列表</strong><small>先建立预算项目，再导入甲方清单</small></div><span>共 {{ total }} 个</span></div>
        <el-table v-loading="loading" :data="projects" :row-key="projectIdOf" class="users-table" empty-text="暂无预算项目">
          <el-table-column label="预算项目" min-width="250">
            <template #default="{ row }"><div class="budget-name"><strong>{{ projectName(row) }}</strong><small>{{ projectCode(row) }} · {{ row.client_name || '未填写客户' }}</small></div></template>
          </el-table-column>
          <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="statusTag(projectStatus(row))" effect="plain">{{ statusLabel(projectStatus(row)) }}</el-tag></template></el-table-column>
          <el-table-column label="导入批次" width="110" align="right"><template #default="{ row }">{{ row.import_count ?? row.import_batch_count ?? 0 }}</template></el-table-column>
          <el-table-column label="识别条目" width="110" align="right"><template #default="{ row }">{{ row.standard_item_count ?? row.standard_row_count ?? row.row_count ?? 0 }}</template></el-table-column>
          <el-table-column label="更新时间" width="170"><template #default="{ row }">{{ formatDate(row.updated_at || row.created_at) }}</template></el-table-column>
          <el-table-column label="操作" width="250" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" plain @click="emit('navigate', `/admin/budget-projects/${projectIdOf(row)}`)">进入项目</el-button>
              <el-button v-if="canUpdateProject(row)" size="small" plain @click="openProjectDialog(row)">修改</el-button>
              <el-button v-if="canArchiveProject(row)" size="small" type="warning" plain @click="archiveProject(row)">归档</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-if="total > pageSize" v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="loadProjects" />
      </section>
    </template>
  </div>

  <div v-else>
    <div class="content-heading">
      <div><p class="eyebrow">预算项目 / {{ projectCode(detail) }}</p><h2>{{ projectName(detail) }}</h2></div>
      <div class="heading-actions">
        <el-button :icon="ArrowLeft" plain @click="emit('navigate', '/admin/budget-projects')">返回列表</el-button>
        <el-button v-if="canUpdateCurrentProject" plain @click="openProjectDialog(detail)">修改项目</el-button>
        <el-button :icon="Refresh" plain :loading="loading" @click="loadDetail">刷新</el-button>
      </div>
    </div>
    <el-alert
      v-if="!pricingFeatureAvailable"
      class="budget-mode-alert"
      type="info"
      show-icon
      :closable="false"
      title="当前为成本计价准备模式"
      description="已完成 Excel 清单导入、表头映射、工程量标准化和异常标记；成本计价功能当前未开启。"
    />
    <el-alert
      v-else
      class="budget-mode-alert"
      type="info"
      show-icon
      :closable="false"
      title="企业定额部分计价已启用"
      description="计价只绑定当前正式清单和当次企业定额版本；未匹配项不会按 0 元计入完整项目成本。"
    />
    <div class="budget-metrics">
      <div><span>项目状态</span><strong>{{ statusLabel(projectStatus(detail)) }}</strong></div>
      <div><span>导入批次</span><strong>{{ imports.length }}</strong></div>
      <div><span>当前 Sheet</span><strong>{{ selectedImport?.summary?.sheet_count || mappingSheets.length }}</strong></div>
      <div><span>识别条目</span><strong>{{ selectedImport?.summary?.standard_item_count ?? standardRows.length }}</strong><small>有效工程量 {{ selectedImport?.summary?.valid_quantity_count ?? validQuantityCount }} 行，异常 {{ abnormalCount }} 行</small></div>
    </div>
    <BudgetProjectPricing :project="detail" :feature-available="pricingFeatureAvailable" />
    <section class="budget-panel">
      <div class="budget-title"><div><strong>导入甲方清单</strong><small>仅支持 .xlsx / .xlsm；保留文件名、SHA256 及解析快照，第一阶段暂不提供原件下载</small></div></div>
      <el-alert v-if="detailArchived" type="warning" show-icon :closable="false" title="项目已归档，仅可查看历史导入和标准清单" />
      <div v-else-if="canUploadCurrent" class="budget-upload">
        <el-upload ref="uploadRef" :auto-upload="false" :limit="1" accept=".xlsx,.xlsm" :on-change="fileChanged" :on-remove="clearUpload"><el-button :icon="Upload" plain>选择 Excel</el-button></el-upload>
        <el-button type="primary" :loading="uploading" :disabled="!uploadFile" @click="uploadImport">导入并标准化</el-button>
      </div>
    </section>
    <section class="budget-panel">
      <div class="budget-title"><div><strong>导入批次</strong><small>选择批次查看 Sheet、表头映射及标准清单</small></div><span>{{ imports.length }} 个</span></div>
      <el-table :data="imports" :row-key="batchIdOf" :row-class-name="importRowClassName" class="users-table" empty-text="尚未导入清单">
        <el-table-column label="批次" min-width="260"><template #default="{ row }"><div class="budget-name"><strong>{{ row.batch_no || row.import_code || `批次 ${batchIdOf(row)}` }} <el-tag v-if="isSelectedImport(row)" size="small" type="primary" effect="light">当前查看</el-tag></strong><small>{{ sourceFileName(row) }}</small></div></template></el-table-column>
        <el-table-column label="Sheet" width="90" align="right"><template #default="{ row }">{{ row.sheet_count ?? row.summary?.sheet_count ?? 0 }}</template></el-table-column>
        <el-table-column label="识别条目" width="100" align="right"><template #default="{ row }">{{ row.summary?.standard_item_count ?? row.standard_item_count ?? row.row_count ?? row.standard_row_count ?? 0 }}</template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="batchStatusTag(row)" effect="plain">{{ batchStatusLabel(row) }}</el-tag></template></el-table-column>
        <el-table-column label="导入时间" width="170"><template #default="{ row }">{{ formatDate(row.created_at || row.imported_at) }}</template></el-table-column>
        <el-table-column label="操作" width="260"><template #default="{ row }"><el-button size="small" :type="isSelectedImport(row) ? 'primary' : ''" :plain="!isSelectedImport(row)" @click="selectImport(row)">{{ isSelectedImport(row) ? '正在查看' : '查看批次' }}</el-button><el-button v-if="canConfirmImport(row)" size="small" plain @click="confirmImport(row)">确认清单</el-button><el-button v-if="canActivateImport(row)" size="small" type="success" plain @click="activateImport(row)">设为当前批次</el-button></template></el-table-column>
      </el-table>
    </section>
    <template v-if="selectedImport">
      <section class="budget-panel">
        <div class="budget-title"><div><strong>Sheet 表头映射</strong><small>可选择项目名称、项目特征、单位、工程量或忽略；重新生成只影响当前批次 · 映射修订 {{ selectedImport.remap_revision ?? 0 }}</small></div></div>
        <el-tabs v-if="mappingSheets.length" v-model="activeMappingSheet">
          <el-tab-pane v-for="sheet in mappingSheets" :key="sheet.sheet_name" :label="sheet.sheet_name" :name="sheet.sheet_name">
            <div class="mapping-summary">
              <el-tag :type="quantityMappingColumn(sheet) ? 'success' : 'warning'" effect="light">计算工程量列：{{ quantityMappingColumn(sheet) || '未指定' }}</el-tag>
              <span>每个 Sheet 只允许一个合计工程量列；分层工程量及价格/金额列固定忽略。</span>
            </div>
            <el-table :data="sheet.columns" row-key="column" class="users-table">
              <el-table-column prop="column" label="列" width="75" />
              <el-table-column label="原始表头" min-width="160"><template #default="{ row }">{{ row.label || row.header || '-' }}</template></el-table-column>
              <el-table-column label="样例" min-width="230"><template #default="{ row }">{{ samples(row) }}</template></el-table-column>
              <el-table-column label="标准字段" width="235"><template #default="{ row }"><div class="mapping-field"><el-select v-model="mappings[sheet.sheet_name][row.column]" size="small" :disabled="mappingColumnDisabled(row)" @change="mappingChanged(sheet, row, $event)"><el-option v-for="option in mappingOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select><small v-if="isLockedPriceColumn(row)">价格/金额列已锁定忽略</small><small v-else-if="isLockedLayerQuantityColumn(row)">分层工程量列已锁定忽略</small><small v-else-if="mappings[sheet.sheet_name]?.[row.column] === 'quantity'">当前合计工程量列</small><small v-else-if="!canRemapCurrent">当前项目仅可查看</small></div></template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
        <el-empty v-else description="当前批次未返回表头映射" />
        <div v-if="canRemapCurrent" class="budget-actions"><el-button type="primary" :loading="mappingLoading" :disabled="!mappingSheets.length" @click="regenerate">应用映射并重新生成当前批次</el-button></div>
      </section>
      <section class="budget-panel">
        <div class="budget-title"><div><strong>标准清单</strong><small>清单项目参与工程量统计和报价；编制说明、计算规则等参考信息仅展示，不参与计算</small></div></div>
        <el-tabs v-if="sheetNames.length" v-model="activeRowSheet">
          <el-tab-pane v-for="sheet in sheetNames" :key="sheet" :label="`${sheet} (${rowsBySheet(sheet).length})`" :name="sheet">
            <el-table :data="rowsBySheet(sheet)" row-key="__key" :row-class-name="standardRowClassName" class="users-table" max-height="620">
              <el-table-column label="来源" width="100"><template #default="{ row }">行 {{ row.raw_row_index || row.source_row_number || '-' }}</template></el-table-column>
              <el-table-column label="项目名称 / 参考内容" min-width="280"><template #default="{ row }">{{ standardRowTitle(row) }}</template></el-table-column>
              <el-table-column label="项目特征" min-width="240"><template #default="{ row }">{{ isReferenceRow(row) ? '-' : (row.spec || row.project_feature || '-') }}</template></el-table-column>
              <el-table-column label="单位" width="85"><template #default="{ row }">{{ isReferenceRow(row) ? '-' : (row.unit || '-') }}</template></el-table-column>
              <el-table-column label="原始工程量" width="135" align="right"><template #default="{ row }">{{ isReferenceRow(row) ? '-' : originalQuantity(row) }}</template></el-table-column>
              <el-table-column label="计算工程量" width="135" align="right"><template #default="{ row }"><strong>{{ isReferenceRow(row) ? '-' : calculatedQuantity(row) }}</strong></template></el-table-column>
              <el-table-column label="数量状态" width="125"><template #default="{ row }"><el-tag v-if="isReferenceRow(row)" type="info" effect="plain">参考信息</el-tag><el-tag v-else :type="quantityValid(row.quantity_status) ? 'success' : 'warning'" effect="plain">{{ quantityLabel(row.quantity_status) }}</el-tag></template></el-table-column>
              <el-table-column label="原因" min-width="220"><template #default="{ row }">{{ isReferenceRow(row) ? '仅作为估价上下文，不参与工程量和报价计算' : quantityReason(row) }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
        <el-empty v-else description="当前批次暂无标准清单行" />
      </section>
    </template>
  </div>
  <el-dialog v-model="dialog.visible" :title="dialog.id ? '修改预算项目' : '新建预算项目'" width="560px">
    <el-form label-position="top" :model="dialog.form">
      <el-form-item label="项目名称" required><el-input v-model="dialog.form.name" maxlength="120" /></el-form-item>
      <el-form-item label="客户名称"><el-input v-model="dialog.form.client_name" maxlength="80" /></el-form-item>
      <el-form-item label="项目说明"><el-input v-model="dialog.form.description" type="textarea" :rows="3" maxlength="800" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dialog.visible = false">取消</el-button><el-button type="primary" :loading="dialog.loading" :disabled="!dialogWriteAllowed" @click="saveProject">保存</el-button></template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import BudgetProjectPricing from './BudgetProjectPricing.vue'
import { budgetApiErrorMessage, budgetProjectApi, budgetResponseData, budgetResponseItems } from './budgetProjectApi'

const props = defineProps({
  detailMode: Boolean,
  canEdit: Boolean,
  featureAvailable: { type: Boolean, default: true },
  pricingFeatureAvailable: { type: Boolean, default: false },
})
const emit = defineEmits(['navigate'])
const statusOptions = [{ value: 'active', label: '进行中' }, { value: 'archived', label: '已归档' }]
const mappingOptions = [{ value: 'ignore', label: '忽略' }, { value: 'item_name', label: '项目名称' }, { value: 'spec', label: '项目特征' }, { value: 'unit', label: '单位' }, { value: 'quantity', label: '工程量' }]
const projects = ref([]), total = ref(0), page = ref(1), loading = ref(false), featureDisabled = ref(false)
const pageSize = 20
const filters = reactive({ keyword: '', status: 'active' })
const detail = ref(null), imports = ref([]), selectedImport = ref(null), importRows = ref([]), uploadFile = ref(null)
const uploadRef = ref(null)
const uploading = ref(false), mappingLoading = ref(false), activeMappingSheet = ref(''), activeRowSheet = ref('')
const mappings = reactive({})
const dialog = reactive({ visible: false, loading: false, id: null, target: null, form: { name: '', client_name: '', description: '' } })
const referenceSheetRoles = new Set(['metadata', 'calculation_rule', 'loss_reference', 'material_reference', 'optional_backup', 'summary_analysis'])
const mappingSheets = computed(() => normalizeSheets(selectedImport.value))
const standardRows = computed(() => importRows.value.filter((row) => (
  !isReferenceRow(row) && (row.row_type === 'data_row' || row.is_standard_item === true)
)))
const displayRows = computed(() => importRows.value.filter((row) => (
  isReferenceRow(row) || row.is_standard_item === true || row.row_type === 'data_row'
)))
const sheetNames = computed(() => Array.from(new Set(displayRows.value.map((row) => row.source_sheet || row.sheet_name || '默认 Sheet'))))
const abnormalCount = computed(() => selectedImport.value?.summary?.invalid_quantity_count ?? standardRows.value.filter((row) => !quantityValid(row.quantity_status)).length)
const validQuantityCount = computed(() => standardRows.value.filter((row) => quantityValid(row.quantity_status)).length)
const detailArchived = computed(() => projectStatus(detail.value) === 'archived')
const moduleUnavailable = computed(() => !props.featureAvailable || featureDisabled.value)
const canCreateProject = computed(() => !moduleUnavailable.value && props.canEdit)
const canUpdateCurrentProject = computed(() => canUpdateProject(detail.value))
const canUploadCurrent = computed(() => !detailArchived.value && projectCapability(detail.value, 'can_upload'))
const canRemapCurrent = computed(() => !detailArchived.value && projectCapability(detail.value, 'can_remap') && batchCapability(selectedImport.value, 'can_remap'))
const dialogWriteAllowed = computed(() => dialog.id ? canUpdateProject(dialog.target) : canCreateProject.value)

const projectName = (row) => row?.name || row?.project_name || '未命名预算项目'
const projectIdOf = (row) => row?.id ?? row?.project_id
const projectCode = (row) => row?.project_code || row?.budget_project_code || `#${projectIdOf(row) || '-'}`
const projectStatus = (row) => row?.workspace_status ?? row?.status
const batchIdOf = (row) => row?.id ?? row?.batch_id
const hasOwn = (value, key) => Boolean(value && Object.prototype.hasOwnProperty.call(value, key))
const projectCapability = (row, key) => hasOwn(row?.capabilities, key) && row.capabilities[key] === true
const batchCapability = (row, key) => hasOwn(row?.capabilities, key) && row.capabilities[key] === true
const canUpdateProject = (row) => !moduleUnavailable.value && projectStatus(row) !== 'archived' && projectCapability(row, 'can_edit')
const canArchiveProject = (row) => !moduleUnavailable.value && projectStatus(row) !== 'archived' && projectCapability(row, 'can_archive')
const sourceFileName = (row) => row?.source_file?.filename || row?.file_name || row?.source_file_name || '-'
const currentProjectId = () => Number(window.location.pathname.match(/^\/admin\/budget-projects\/(\d+)$/)?.[1] || 0)
const statusLabel = (value) => ({ active: '进行中', draft: '草稿', archived: '已归档' })[value] || value || '-'
const statusTag = (value) => ({ active: 'success', draft: 'warning', archived: 'info' })[value] || 'info'
const batchStatusLabel = (row) => row?.is_active ? '正式批次' : ({ parsed: '待确认', confirmed: '已确认', active: '正式批次', superseded: '已替代' })[row?.status] || row?.status || '-'
const batchStatusTag = (row) => row?.is_active ? 'success' : ({ parsed: 'warning', confirmed: 'primary', active: 'success', superseded: 'info' })[row?.status] || 'info'
const formatDate = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
const quantityValid = (value) => ['valid', 'ok', 'normalized'].includes(String(value || '').toLowerCase())
const quantityLabel = (value) => ({ valid: '有效', ok: '有效', normalized: '已标准化', missing: '缺失', invalid: '格式异常', abnormal: '异常', non_numeric: '非数值', non_positive: '非正数', zero: '零值', sequence_column: '序号列', suspected_sequence_column: '疑似序号列', precision_underflow: '低于支持精度', unsupported_precision: '精度超限', not_applicable: '不适用' })[value] || value || '待确认'
const quantityReasonLabel = (reason) => ({
  VALID_SOURCE_QUANTITY: '原始工程量有效，可用于计算',
  MISSING: '原始工程量缺失',
  NO_QUANTITY_COLUMN: '未指定工程量列，计算工程量按 0 处理',
  MULTIPLE_QUANTITY_COLUMNS: '存在多个工程量列，需保留唯一合计工程量列',
  EMPTY_QUANTITY: '原始工程量为空，计算工程量按 0 处理',
  SEQUENCE_COLUMN: '疑似序号列，不作为工程量',
  RANGE_OR_APPROXIMATE: '区间值或约数，需人工确认',
  MULTIPLE_NUMBERS: '同一单元格包含多个数值，计算工程量按 0 处理',
  INVALID_QUANTITY: '原始工程量不是有效数值，计算工程量按 0 处理',
  OUT_OF_RANGE: '工程量为负数、过大或超出支持范围，计算工程量按 0 处理',
  EXPLICIT_ZERO: '原始工程量明确为 0',
  UNSUPPORTED_QUANTITY_PRECISION: '工程量小数精度暂不支持',
  BELOW_SUPPORTED_PRECISION: '工程量低于当前支持精度',
  NON_NUMERIC: '原始工程量不是有效数值',
  NOT_APPLICABLE: '该行不适用工程量计算',
})[reason] || reason
const quantityReason = (row) => {
  const reason = row.quantity_reason || row.quantity_source?.budget?.reason || row.quantity_status_reason || row.quantity_error
  return quantityReasonLabel(reason) || (quantityValid(row.quantity_status) ? '数量可用于计算' : '原始工程量不可直接用于计算')
}
const originalQuantity = (row) => row.original_quantity ?? row.raw_quantity ?? row.source_quantity ?? row.quantity_original ?? '-'
const calculatedQuantity = (row) => row.calculation_quantity ?? row.calculated_quantity ?? row.normalized_quantity ?? (quantityValid(row.quantity_status) ? (row.quantity ?? 0) : 0)
function isReferenceRow(row) {
  if (row?.row_type === 'reference_row') return true
  if (!referenceSheetRoles.has(row?.sheet_role) || row?.row_type === 'empty_row') return false
  return Boolean(String(row?.raw_text || row?.item_name || row?.remark || '').trim())
}
const standardRowTitle = (row) => isReferenceRow(row)
  ? (row.raw_text || row.item_name || row.project_name || row.remark || '-')
  : (row.item_name || row.project_name || '-')
const standardRowClassName = ({ row }) => isReferenceRow(row) ? 'budget-reference-row' : ''
const rowsBySheet = (sheet) => displayRows.value.filter((row) => (row.source_sheet || row.sheet_name || '默认 Sheet') === sheet)
const samples = (column) => Array.isArray(column.sample_values || column.samples) ? ((column.sample_values || column.samples).slice(0, 3).join(' / ') || '-') : '-'
const priceHeaderPattern = /(单价|合价|金额|造价|成本价|投标价|税前价|含税价|税率|费率|人工费|材料费|主材费|辅材费|机械费|管理费|利润)/
const layerQuantityPattern = /((地下|负?\d+|[一二三四五六七八九十百]+)层.*(工程量|数量)|(工程量|数量).*(地下|负?\d+|[一二三四五六七八九十百]+)层)/
const columnLabel = (column) => String(column?.label || column?.header || '').trim()
const isLockedPriceColumn = (column) => column?.is_price === true || column?.lock_reason === 'PRICE_AMOUNT_COLUMN' || column?.locked_reason === 'PRICE_AMOUNT_COLUMN' || column?.detected_field === 'price_ignored' || priceHeaderPattern.test(columnLabel(column))
const isLockedLayerQuantityColumn = (column) => column?.lock_reason === 'LAYER_QUANTITY_COLUMN' || column?.locked_reason === 'LAYER_QUANTITY_COLUMN' || column?.is_layer_quantity === true || layerQuantityPattern.test(columnLabel(column))
const isLockedIgnoreColumn = (column) => column?.locked_ignore === true || isLockedPriceColumn(column) || isLockedLayerQuantityColumn(column)
const mappingColumnDisabled = (column) => !canRemapCurrent.value || isLockedIgnoreColumn(column)
const quantityMappingColumn = (sheet) => {
  const column = sheet.columns.find((item) => mappings[sheet.sheet_name]?.[item.column] === 'quantity')
  return column ? `${column.column} · ${columnLabel(column) || '未命名列'}` : ''
}

function normalizeSheets(source) {
  return (source?.sheet_mappings || source?.mappings || source?.sheets || []).map((sheet, index) => {
    const name = sheet.sheet_name || sheet.name || `Sheet ${index + 1}`
    const lockedColumns = new Set((sheet.budget_locked_ignore_columns || sheet.locked_ignore_columns || []).map((column) => String(column).toUpperCase()))
    const columns = (sheet.current_columns || sheet.columns || sheet.headers || []).map((column, i) => {
      const key = String(column.column || column.key || column.column_letter || String(i + 1)).toUpperCase()
      return { ...column, column: key, locked_ignore: column.locked_ignore === true || lockedColumns.has(key) }
    })
    return { ...sheet, sheet_name: name, field_mapping: sheet.applied_field_mapping || sheet.field_mapping || sheet.mapping || {}, columns }
  })
}
function hydrateMappings() {
  Object.keys(mappings).forEach((key) => delete mappings[key])
  mappingSheets.value.forEach((sheet) => {
    mappings[sheet.sheet_name] = { ...sheet.field_mapping }
    sheet.columns.forEach((column) => {
      if (isLockedIgnoreColumn(column)) mappings[sheet.sheet_name][column.column] = 'ignore'
      else if (!mappings[sheet.sheet_name][column.column]) mappings[sheet.sheet_name][column.column] = column.detected_field || 'ignore'
    })
  })
  activeMappingSheet.value = mappingSheets.value[0]?.sheet_name || ''
}
async function loadProjects() {
  if (!props.featureAvailable) { featureDisabled.value = true; projects.value = []; total.value = 0; return }
  loading.value = true; featureDisabled.value = false
  try {
    const params = { page: page.value, page_size: pageSize, ...(filters.status ? { status: filters.status } : {}), ...(filters.keyword.trim() ? { keyword: filters.keyword.trim() } : {}) }
    const response = await budgetProjectApi.list(params)
    projects.value = budgetResponseItems(response); total.value = Number(response.data?.total ?? budgetResponseData(response)?.total ?? projects.value.length)
  } catch (error) { if (error.response?.status === 404) featureDisabled.value = true; else ElMessage.error(budgetApiErrorMessage(error, '预算项目加载失败')) }
  finally { loading.value = false }
}
function searchProjects() { page.value = 1; loadProjects() }
function openProjectDialog(row = null) {
  if (row && !canUpdateProject(row)) return ElMessage.warning('当前项目仅可查看，不能修改')
  if (!row && !canCreateProject.value) return ElMessage.warning('当前账号不能新建预算项目')
  dialog.id = projectIdOf(row) || null; dialog.target = row; Object.assign(dialog.form, { name: row ? projectName(row) : '', client_name: row?.client_name || '', description: row?.description || '' }); dialog.visible = true
}
async function saveProject() {
  if (!dialogWriteAllowed.value) return ElMessage.warning('当前操作权限已失效，请刷新页面')
  if (!dialog.form.name.trim()) return ElMessage.warning('请填写项目名称')
  dialog.loading = true
  try {
    const payload = { name: dialog.form.name.trim(), client_name: dialog.form.client_name.trim(), description: dialog.form.description.trim() }
    const response = dialog.id ? await budgetProjectApi.update(dialog.id, payload) : await budgetProjectApi.create(payload)
    const saved = budgetResponseData(response); dialog.visible = false; ElMessage.success('预算项目已保存')
    if (props.detailMode) await loadDetail(); else if (!dialog.id && projectIdOf(saved)) emit('navigate', `/admin/budget-projects/${projectIdOf(saved)}`); else await loadProjects()
  } catch (error) { ElMessage.error(budgetApiErrorMessage(error, '预算项目保存失败')) } finally { dialog.loading = false }
}
async function archiveProject(row) {
  if (!canArchiveProject(row)) return ElMessage.warning('当前项目不能归档')
  try { await ElMessageBox.confirm(`确认归档“${projectName(row)}”？`, '归档预算项目', { type: 'warning' }); await budgetProjectApi.archive(projectIdOf(row)); ElMessage.success('预算项目已归档'); await loadProjects() }
  catch (error) { if (!['cancel', 'close'].includes(error)) ElMessage.error(budgetApiErrorMessage(error, '归档失败')) }
}
async function loadDetail() {
  if (!props.featureAvailable) { featureDisabled.value = true; return }
  const activeProjectId = currentProjectId()
  if (!activeProjectId) return
  loading.value = true
  try {
    const detailResponse = await budgetProjectApi.detail(activeProjectId)
    const allImports = []
    const importPageSize = 100
    let importPage = 1
    while (true) {
      const importsResponse = await budgetProjectApi.listImports(activeProjectId, { page: importPage, page_size: importPageSize })
      const pageItems = budgetResponseItems(importsResponse)
      allImports.push(...pageItems)
      const responseBody = importsResponse.data
      const responseData = budgetResponseData(importsResponse)
      const totalCount = responseBody?.total ?? (!Array.isArray(responseData) ? responseData?.total : null)
      if ((totalCount !== null && totalCount !== undefined && allImports.length >= Number(totalCount)) || pageItems.length < importPageSize) break
      importPage += 1
    }
    detail.value = budgetResponseData(detailResponse); imports.value = allImports
    const activeImportResponse = await budgetProjectApi.activeImport(activeProjectId)
    const activeImport = budgetResponseData(activeImportResponse)
    const activeImportId = batchIdOf(activeImport) ?? detail.value?.active_import_batch_id ?? detail.value?.active_import_id ?? batchIdOf(detail.value?.active_import)
    const target = imports.value.find((row) => batchIdOf(row) === batchIdOf(selectedImport.value))
      || imports.value.find((row) => row.is_active === true || batchIdOf(row) === activeImportId)
      || imports.value[0]
    if (target) await selectImport(target); else { selectedImport.value = null; importRows.value = [] }
  } catch (error) { ElMessage.error(budgetApiErrorMessage(error, '预算项目详情加载失败')) } finally { loading.value = false }
}
function fileChanged(file) { uploadFile.value = file.raw || file }
function clearUpload() { uploadFile.value = null }
async function uploadImport() {
  if (!canUploadCurrent.value) return ElMessage.warning('当前项目不能导入清单')
  if (!uploadFile.value) return
  uploading.value = true
  try {
    const form = new FormData(); form.append('file', uploadFile.value)
    const response = await budgetProjectApi.uploadImport(currentProjectId(), form)
    const created = budgetResponseData(response)
    selectedImport.value = null
    uploadFile.value = null
    uploadRef.value?.clearFiles()
    ElMessage.success('清单已导入')
    await loadDetail()
    const createdId = batchIdOf(created)
    const createdBatch = imports.value.find((row) => batchIdOf(row) === createdId)
    if (createdBatch) await selectImport(createdBatch)
  }
  catch (error) { ElMessage.error(budgetApiErrorMessage(error, '清单导入失败')) } finally { uploading.value = false }
}
async function selectImport(batch) {
  try {
    const batchId = batchIdOf(batch)
    const detailResponse = await budgetProjectApi.importDetail(batchId)
    const allRows = []
    const rowPageSize = 500
    let rowPage = 1
    while (true) {
      const rowsResponse = await budgetProjectApi.importRows(batchId, { page: rowPage, page_size: rowPageSize })
      const pageItems = budgetResponseItems(rowsResponse)
      allRows.push(...pageItems)
      const responseBody = rowsResponse.data
      const responseData = budgetResponseData(rowsResponse)
      const totalCount = responseBody?.total ?? (!Array.isArray(responseData) ? responseData?.total : null)
      if ((totalCount !== null && totalCount !== undefined && allRows.length >= Number(totalCount)) || pageItems.length < rowPageSize) break
      rowPage += 1
    }
    selectedImport.value = { ...batch, ...(budgetResponseData(detailResponse) || {}) }
    importRows.value = allRows.map((row, index) => ({ ...row, __key: row.row_key || `${row.source_sheet || 'sheet'}:${row.raw_row_index || index}:${index}` }))
    hydrateMappings(); activeRowSheet.value = sheetNames.value[0] || ''
  } catch (error) { ElMessage.error(budgetApiErrorMessage(error, '导入批次加载失败')) }
}
const isSelectedImport = (row) => batchIdOf(row) === batchIdOf(selectedImport.value)
const importRowClassName = ({ row }) => isSelectedImport(row) ? 'current-import-row' : ''
const canConfirmImport = (row) => !detailArchived.value && batchCapability(row, 'can_confirm')
const canActivateImport = (row) => !detailArchived.value && projectCapability(detail.value, 'can_activate_import') && batchCapability(row, 'can_activate')
async function confirmImport(batch) {
  if (!canConfirmImport(batch)) return
  try {
    await ElMessageBox.confirm(`确认“${sourceFileName(batch)}”解析结果？确认后该批次将冻结映射；如需调整，必须重新上传为新批次。`, '确认清单批次', { type: 'warning' })
    await budgetProjectApi.confirmImport(batchIdOf(batch)); ElMessage.success('清单批次已确认'); await loadDetail()
  } catch (error) { if (!['cancel', 'close'].includes(error)) ElMessage.error(budgetApiErrorMessage(error, '确认批次失败')) }
}
async function activateImport(batch) {
  if (!canActivateImport(batch)) return
  try {
    await ElMessageBox.confirm(`设定“${sourceFileName(batch)}”为项目当前正式清单批次？原当前批次将被替代。`, '设为当前批次', { type: 'warning' })
    await budgetProjectApi.activateImport(batchIdOf(batch)); ElMessage.success('已设为当前批次'); await loadDetail()
  } catch (error) { if (!['cancel', 'close'].includes(error)) ElMessage.error(budgetApiErrorMessage(error, '启用批次失败')) }
}
function mappingChanged(sheet, column, value) {
  if (isLockedIgnoreColumn(column)) { mappings[sheet.sheet_name][column.column] = 'ignore'; return }
  if (value !== 'quantity') return
  Object.keys(mappings[sheet.sheet_name] || {}).forEach((key) => {
    if (key !== column.column && mappings[sheet.sheet_name][key] === 'quantity') mappings[sheet.sheet_name][key] = 'ignore'
  })
  ElMessage.info(`已将 ${column.column} 列设为该 Sheet 唯一的合计工程量列`)
}
async function regenerate() {
  if (!canRemapCurrent.value) return ElMessage.warning('当前批次不能修改映射')
  mappingLoading.value = true
  try {
    await budgetProjectApi.updateSheetMappings(batchIdOf(selectedImport.value), {
      expected_remap_revision: Number(selectedImport.value?.remap_revision ?? 0),
      sheet_mappings: mappingSheets.value.map((sheet) => ({ sheet_name: sheet.sheet_name, field_mapping: mappings[sheet.sheet_name] || {} })),
    })
    await selectImport(selectedImport.value); ElMessage.success('已按人工映射重新生成当前批次')
  } catch (error) { ElMessage.error(budgetApiErrorMessage(error, '重新生成失败')) } finally { mappingLoading.value = false }
}
async function initialize() {
  featureDisabled.value = !props.featureAvailable
  if (featureDisabled.value) return
  if (props.detailMode) await loadDetail(); else await loadProjects()
}
watch(() => props.detailMode, initialize)
watch(() => props.featureAvailable, initialize)
onMounted(initialize)
</script>

<style scoped>
.budget-mode-alert,.budget-panel{margin-bottom:18px}.budget-filters{display:grid;grid-template-columns:minmax(260px,1fr) 180px auto;gap:12px;margin-bottom:16px}.budget-panel{padding:20px;border:1px solid rgba(148,163,184,.22);border-radius:20px;background:rgba(255,255,255,.9);box-shadow:0 14px 34px rgba(15,23,42,.06)}.budget-title{display:flex;justify-content:space-between;gap:16px;margin-bottom:16px}.budget-title div,.budget-name{display:flex;flex-direction:column;gap:4px}.budget-title small,.budget-title span,.budget-name small,.budget-metrics span,.budget-metrics small{color:#64748b}.budget-upload{display:flex;align-items:flex-start;gap:12px}.budget-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}.budget-metrics>div{padding:18px;border:1px solid rgba(148,163,184,.2);border-radius:18px;background:#fff}.budget-metrics strong{display:block;margin-top:8px;font-size:24px}.budget-actions{display:flex;justify-content:flex-end;margin-top:16px}.mapping-summary{display:flex;align-items:center;gap:10px;margin:0 0 12px;color:#64748b;font-size:13px}.mapping-field{display:flex;flex-direction:column;gap:4px}.mapping-field small{color:#64748b}.mapping-field small:first-of-type{color:#b45309}:deep(.current-import-row td.el-table__cell){background:#eff6ff!important}.el-pagination{margin-top:16px;justify-content:flex-end}@media(max-width:900px){.budget-filters{grid-template-columns:1fr}.budget-metrics{grid-template-columns:repeat(2,1fr)}.mapping-summary{align-items:flex-start;flex-direction:column}}
:deep(.budget-reference-row td.el-table__cell){background:#f8fafc;color:#475569}
</style>
