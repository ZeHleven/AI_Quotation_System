<template>
  <section class="quota-v2-shell">
    <header class="quota-v2-header">
      <div>
        <p class="quota-v2-eyebrow">企业定额 2.0 · Excel 镜像工作台</p>
        <h3>企业定额主库</h3>
        <span>保留原表头、分级、行样式与公式链接；人工或材料调价后自动重算定额。</span>
      </div>
      <div class="quota-v2-actions">
        <el-select
          v-model="selectedVersionId"
          class="quota-version-select"
          placeholder="选择版本"
          @change="handleVersionChange"
        >
          <el-option
            v-for="version in versions"
            :key="version.id"
            :label="versionOptionLabel(version)"
            :value="version.id"
          />
        </el-select>
        <input
          ref="fileInput"
          class="quota-file-input"
          type="file"
          accept=".xlsx,.xlsm"
          @change="handleFileSelected"
        />
        <el-button v-if="canEdit" type="primary" :loading="previewLoading" @click="openFilePicker">
          导入 2.0 Excel
        </el-button>
        <el-button
          v-if="canEdit && selectedVersion && selectedVersion.status !== 'draft'"
          :loading="actionLoading"
          @click="cloneSelectedVersion"
        >
          创建编辑草稿
        </el-button>
        <el-button
          v-if="canEdit && selectedVersion?.status === 'draft'"
          :loading="actionLoading"
          @click="recalculateSelectedVersion"
        >
          全量重算
        </el-button>
        <el-button
          v-if="canApprove && selectedVersion?.status === 'draft'"
          type="success"
          :disabled="qualityBlockerCount > 0"
          :loading="actionLoading"
          @click="activateSelectedVersion"
        >
          启用为主库
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="moduleError"
      class="quota-v2-alert"
      type="info"
      show-icon
      :closable="false"
      :title="moduleError"
    />

    <template v-else>
      <div v-if="selectedVersion" class="quota-v2-status">
        <div class="quota-version-identity">
          <el-tag :type="versionTagType(selectedVersion.status)" effect="dark">
            {{ versionStatusLabel(selectedVersion.status) }}
          </el-tag>
          <div>
            <strong>{{ selectedVersion.version_name }}</strong>
            <small>
              {{ selectedVersion.version_code }} · 修订 {{ selectedVersion.revision }}
              · {{ formatDate(selectedVersion.last_recalculated_at || selectedVersion.created_at) }}
            </small>
          </div>
        </div>
        <div class="quota-status-metrics">
          <button type="button" class="quota-metric quality" @click="qualityDialogVisible = true">
            <span>质量状态</span>
            <strong :class="`quality-${selectedVersion.quality_status || 'unknown'}`">
              {{ qualityLabel(selectedVersion.quality_status) }}
            </strong>
          </button>
          <div class="quota-metric">
            <span>定额主项</span>
            <strong>{{ selectedVersion.counts?.items || 0 }}</strong>
          </div>
          <div class="quota-metric">
            <span>组成明细</span>
            <strong>{{ selectedVersion.counts?.components || 0 }}</strong>
          </div>
          <div class="quota-metric">
            <span>价格资源</span>
            <strong>{{ selectedVersion.counts?.resources || 0 }}</strong>
          </div>
          <div class="quota-metric">
            <span>公式链接</span>
            <strong>{{ selectedVersion.formula_count || 0 }}</strong>
          </div>
        </div>
      </div>

      <el-alert
        v-if="selectedVersion?.schema_version && selectedVersion.schema_version !== 'enterprise-quota-v2'"
        class="quota-v2-alert"
        type="warning"
        show-icon
        :closable="false"
        title="该历史版本没有企业定额 2.0 的 Excel 镜像"
        description="请导入 2.0 工作簿，或选择一个 2.0 版本后查看四张原表。"
      />

      <el-alert
        v-if="qualityBlockerCount > 0"
        class="quota-v2-alert"
        type="error"
        show-icon
        :closable="false"
      >
        <template #title>
          当前草稿有 {{ qualityBlockerCount }} 个启用阻断项，补齐缺失的人工/材料价格后才可切换主库。
        </template>
        <el-button size="small" type="danger" plain @click="qualityDialogVisible = true">查看并处理</el-button>
      </el-alert>

      <el-tabs v-model="activeSheet" class="quota-v2-tabs" @tab-change="handleSheetChange">
        <el-tab-pane
          v-for="tab in sheetTabs"
          :key="tab.key"
          :name="tab.key"
          :label="tab.label"
        />
      </el-tabs>

      <template v-if="activeSheet !== 'versions'">
        <div class="quota-grid-toolbar">
          <div class="quota-grid-filter-area">
            <div v-if="activeSheet === 'enterprise'" class="quota-category-filters">
              <label class="quota-category-filter">
                <span>大类</span>
                <el-select
                  v-model="selectedMajorSectionId"
                  clearable
                  filterable
                  placeholder="全部大类"
                  @change="handleMajorSectionChange"
                >
                  <el-option
                    v-for="option in classification.majorSections"
                    :key="option.id"
                    :label="option.label"
                    :value="option.id"
                  />
                </el-select>
              </label>
              <label class="quota-category-filter">
                <span>小类</span>
                <el-select
                  v-model="selectedChapterId"
                  clearable
                  filterable
                  :disabled="!selectedMajorSectionId"
                  :placeholder="selectedMajorSectionId ? '全部小类' : '请先选择大类'"
                  @change="handleChapterChange"
                >
                  <el-option
                    v-for="option in availableChapterOptions"
                    :key="option.id"
                    :label="option.label"
                    :value="option.id"
                  />
                </el-select>
              </label>
              <el-button
                v-if="selectedMajorSectionId || selectedChapterId"
                plain
                @click="clearClassificationFilters"
              >
                清除分类
              </el-button>
            </div>
            <div class="quota-grid-search">
              <el-input
                v-model="keyword"
                clearable
                placeholder="在当前工作表中查找"
                @keyup.enter="searchRows"
                @clear="searchRows"
              />
              <el-button type="primary" plain @click="searchRows">查找</el-button>
            </div>
          </div>
          <div class="quota-grid-tools">
            <span v-if="grid.editable" class="excel-hint">双击价格库任一行可编辑，Enter 保存，Esc 取消</span>
            <el-button
              v-if="grid.editable && canEdit && ['labor', 'material'].includes(activeSheet)"
              type="primary"
              plain
              @click="openCreateResource(activeSheet)"
            >
              新增{{ activeSheet === 'labor' ? '人工' : '材料' }}
            </el-button>
            <el-button :loading="gridLoading" @click="loadRows">刷新本表</el-button>
          </div>
        </div>

        <div v-loading="gridLoading" class="excel-grid-wrap">
          <table v-if="grid.headers.length" class="excel-grid">
            <colgroup>
              <col class="row-number-column" />
              <col
                v-for="(header, index) in grid.headers"
                :key="`col-${index}`"
                :style="{ width: columnWidth(activeSheet, index) }"
              />
            </colgroup>
            <thead>
              <tr>
                <th class="row-number-head"></th>
                <th v-for="(header, index) in grid.headers" :key="header">
                  <small>{{ columnLetter(index) }}</small>
                  <span>{{ header }}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!visibleRows.length">
                <td :colspan="grid.headers.length + 1" class="empty-grid-cell">
                  {{ selectedVersion ? '当前工作表暂无数据' : '请先导入企业定额 2.0 工作簿' }}
                </td>
              </tr>
              <tr
                v-for="row in visibleRows"
                :key="row.id"
                :class="rowClass(row)"
                @dblclick="startRowEdit(row)"
              >
                <th class="row-number-cell">{{ row.row_number }}</th>
                <template v-if="isSectionRow(row)">
                  <td :colspan="grid.headers.length" class="section-cell">
                    <button
                      v-if="row.row_kind === 'quota_item'"
                      type="button"
                      class="outline-toggle"
                      @click.stop="toggleRow(row)"
                    >
                      {{ collapsedRows.has(row.row_number) ? '+' : '−' }}
                    </button>
                    <span :style="{ paddingLeft: `${Math.max(0, row.outline_level) * 16}px` }">
                      {{ row.values.A || row.values.C || '-' }}
                    </span>
                    <small v-if="row.row_kind === 'quota_item'">
                      {{ row.values.C || '' }} · {{ row.values.G || '' }}
                    </small>
                  </td>
                </template>
                <template v-else>
                  <td
                    v-for="(header, index) in grid.headers"
                    :key="`${row.id}-${columnLetter(index)}`"
                    :class="cellClass(row, columnLetter(index))"
                    :title="cellTitle(row, columnLetter(index))"
                  >
                    <input
                      v-if="editingRowId === row.id"
                      v-model="editValues[columnLetter(index)]"
                      class="excel-cell-input"
                      :aria-label="header"
                      @keyup.enter="saveEditedRow(row)"
                      @keyup.esc="cancelRowEdit"
                    />
                    <template v-else>
                      <span
                        v-if="activeSheet === 'enterprise' && row.row_kind === 'quota_item' && index === 0"
                        class="outline-indent"
                      >
                        <button
                          type="button"
                          class="outline-toggle"
                          @click.stop="toggleRow(row)"
                        >
                          {{ collapsedRows.has(row.row_number) ? '+' : '−' }}
                        </button>
                        {{ displayCell(row.values[columnLetter(index)]) }}
                      </span>
                      <span
                        v-else-if="activeSheet === 'enterprise' && row.row_kind === 'component' && index === 2"
                        class="outline-indent"
                        :style="{ paddingLeft: `${Math.max(1, row.outline_level) * 16}px` }"
                      >
                        {{ displayCell(row.values[columnLetter(index)]) }}
                      </span>
                      <span v-else>{{ displayCell(row.values[columnLetter(index)]) }}</span>
                      <sup v-if="row.formulas?.[columnLetter(index)]" class="formula-badge">fx</sup>
                    </template>
                  </td>
                </template>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="quota-grid-footer">
          <span>
            当前显示 {{ visibleRows.length }} 行，共 {{ grid.total || 0 }} 行
            <template v-if="editingRowId"> · 正在编辑，Enter 保存</template>
          </span>
          <el-pagination
            v-if="grid.total > grid.pageSize"
            v-model:current-page="grid.page"
            :page-size="grid.pageSize"
            :total="grid.total"
            layout="prev, pager, next"
            size="small"
            @current-change="loadRows"
          />
        </div>
      </template>

      <section v-else class="version-center">
        <div class="version-center-heading">
          <div>
            <strong>版本中心</strong>
            <span>生效版本只读；所有修改先在草稿中完成并通过质量门禁。</span>
          </div>
          <el-button :loading="versionsLoading" @click="loadVersions">刷新版本</el-button>
        </div>
        <el-table :data="versions" row-key="id" class="version-table" empty-text="暂无版本">
          <el-table-column label="版本" min-width="260">
            <template #default="{ row }">
              <div class="version-name-cell">
                <strong>{{ row.version_name }}</strong>
                <small>{{ row.version_code }} · revision {{ row.revision }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="105">
            <template #default="{ row }">
              <el-tag :type="versionTagType(row.status)">{{ versionStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="质量" width="105">
            <template #default="{ row }">
              <span :class="`quality-${row.quality_status || 'unknown'}`">{{ qualityLabel(row.quality_status) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="主项/组成/资源" min-width="170">
            <template #default="{ row }">
              {{ row.counts?.items || 0 }} / {{ row.counts?.components || 0 }} / {{ row.counts?.resources || 0 }}
            </template>
          </el-table-column>
          <el-table-column label="公式" width="105">
            <template #default="{ row }">{{ row.formula_count || 0 }}</template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="170">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" min-width="220" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="selectVersion(row)">查看</el-button>
              <el-button size="small" @click="openEvents(row)">记录</el-button>
              <el-button
                v-if="canEdit && row.status !== 'draft'"
                size="small"
                type="primary"
                plain
                @click="cloneVersion(row)"
              >
                建草稿
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <el-dialog v-model="previewDialogVisible" title="导入前结构与质量检查" width="760px">
      <template v-if="previewResult">
        <div class="preview-title">
          <div>
            <strong>{{ previewResult.workbook_title || pendingFile?.name }}</strong>
            <span>{{ pendingFile?.name }}</span>
          </div>
          <el-tag :type="qualityTagType(previewResult.quality?.status)" effect="dark">
            {{ qualityLabel(previewResult.quality?.status) }}
          </el-tag>
        </div>
        <div class="preview-metrics">
          <div><span>分部/章节</span><strong>{{ previewResult.summary?.section_count || 0 }}</strong></div>
          <div><span>定额主项</span><strong>{{ previewResult.summary?.quota_item_count || 0 }}</strong></div>
          <div><span>组成明细</span><strong>{{ previewResult.summary?.component_count || 0 }}</strong></div>
          <div><span>价格资源</span><strong>{{ previewResult.summary?.resource_count || 0 }}</strong></div>
          <div><span>公式链接</span><strong>{{ previewResult.summary?.formula_count || 0 }}</strong></div>
        </div>
        <el-alert
          v-if="previewResult.quality?.blocker_count"
          type="warning"
          show-icon
          :closable="false"
          title="允许导入为草稿，但在阻断项修复前不会替换生效主库。"
        />
        <div class="preview-issues">
          <div v-for="(issue, index) in previewResult.quality?.issues || []" :key="`${issue.code}-${index}`">
            <el-tag :type="issue.severity === 'error' ? 'danger' : 'warning'" size="small">
              {{ issue.severity === 'error' ? '阻断' : '警告' }}
            </el-tag>
            <span>{{ issue.message }}</span>
          </div>
        </div>
      </template>
      <template #footer>
        <el-button @click="previewDialogVisible = false">取消</el-button>
        <el-button v-if="canEdit" type="primary" :loading="importLoading" @click="confirmImport">
          导入为编辑草稿
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qualityDialogVisible" title="数据质量与启用门禁" width="880px">
      <div class="quality-summary-line">
        <el-tag :type="qualityTagType(selectedVersion?.quality_status)" effect="dark">
          {{ qualityLabel(selectedVersion?.quality_status) }}
        </el-tag>
        <span>阻断 {{ qualityBlockerCount }} 项 · 警告 {{ qualityWarningCount }} 项</span>
      </div>
      <el-table :data="qualityIssues" max-height="480" empty-text="当前没有质量问题">
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="row.severity === 'error' ? 'danger' : 'warning'" size="small">
              {{ row.severity === 'error' ? '阻断' : '警告' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="code" label="问题码" min-width="190" />
        <el-table-column prop="message" label="说明" min-width="310" />
        <el-table-column label="位置" width="130">
          <template #default="{ row }">{{ row.sheet || '-' }}<template v-if="row.row"> 第{{ row.row }}行</template></template>
        </el-table-column>
        <el-table-column label="操作" width="105" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canEdit && selectedVersion?.status === 'draft' && row.code === 'FORMULA_RESOURCE_UNRESOLVED'"
              size="small"
              type="primary"
              link
              @click="repairMissingResource(row)"
            >
              补充价格
            </el-button>
            <el-button v-else size="small" link @click="showIssueEvidence(row)">依据</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog
      v-model="resourceDialogVisible"
      :title="`新增${resourceForm.library_kind === 'labor' ? '人工' : '材料'}价格`"
      width="680px"
    >
      <el-form label-position="top" :model="resourceForm">
        <div class="resource-form-grid">
          <el-form-item label="价格库">
            <el-radio-group v-model="resourceForm.library_kind">
              <el-radio-button value="labor">人工价格库</el-radio-button>
              <el-radio-button value="material">材料价格库</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="类型">
            <el-select v-model="resourceForm.resource_type">
              <template v-if="resourceForm.library_kind === 'labor'">
                <el-option label="人工" value="人工" />
              </template>
              <template v-else>
                <el-option label="主材" value="主材" />
                <el-option label="辅材" value="辅材" />
              </template>
            </el-select>
          </el-form-item>
          <el-form-item label="编码">
            <el-input v-model="resourceForm.resource_code" />
          </el-form-item>
          <el-form-item :label="resourceForm.library_kind === 'labor' ? '项目名称' : '材料名称'">
            <el-input v-model="resourceForm.resource_name" />
          </el-form-item>
          <el-form-item label="单位">
            <el-input v-model="resourceForm.unit" />
          </el-form-item>
          <el-form-item :label="resourceForm.library_kind === 'labor' ? '不含税人工费' : '除税单价'">
            <el-input-number v-model="resourceForm.price" :min="0" :precision="4" controls-position="right" />
          </el-form-item>
          <template v-if="resourceForm.library_kind === 'labor'">
            <el-form-item label="工作内容">
              <el-input v-model="resourceForm.work_content" />
            </el-form-item>
            <el-form-item label="计算规则">
              <el-input v-model="resourceForm.calculation_rule" />
            </el-form-item>
            <el-form-item label="含量">
              <el-input-number v-model="resourceForm.default_quantity" :min="0" :precision="4" controls-position="right" />
            </el-form-item>
          </template>
          <template v-else>
            <el-form-item label="材料类别">
              <el-input v-model="resourceForm.category" />
            </el-form-item>
            <el-form-item label="规格/型号">
              <el-input v-model="resourceForm.specification" />
            </el-form-item>
            <el-form-item label="厂家/品牌">
              <el-input v-model="resourceForm.brand" />
            </el-form-item>
          </template>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="resourceDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="actionLoading" @click="createResource">保存并重算</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="eventDialogVisible" title="版本操作记录" width="800px">
      <el-table :data="events" max-height="480" empty-text="暂无操作记录">
        <el-table-column prop="event_type" label="动作" width="190" />
        <el-table-column prop="reason" label="原因" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作者" width="100">
          <template #default="{ row }">{{ row.actor_id || '系统' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  enterpriseQuotaV2Api,
  quotaV2Data,
  quotaV2ErrorMessage,
  quotaV2Items,
} from './enterpriseQuotaV2Api'

const props = defineProps({
  canEdit: { type: Boolean, default: false },
  canApprove: { type: Boolean, default: false },
})

const sheetTabs = [
  { key: 'enterprise', label: '企业定额库' },
  { key: 'labor', label: '人工价格库' },
  { key: 'material', label: '材料价格库' },
  { key: 'validation', label: '消耗量校验报告' },
  { key: 'versions', label: '版本中心' },
]

const versions = ref([])
const selectedVersionId = ref(null)
const activeSheet = ref('enterprise')
const keyword = ref('')
const selectedMajorSectionId = ref(null)
const selectedChapterId = ref(null)
const routeParams = new URLSearchParams(window.location.search)
const routeRequiresActiveVersion = routeParams.get('enterprise_quota_version') === 'active'
const routeActiveVersionId = Number(routeParams.get('enterprise_quota_version_id')) || null
const routeQuotaItemId = Number(routeParams.get('enterprise_quota_item_id')) || null
let routeQuotaItemApplied = false
const versionsLoading = ref(false)
const gridLoading = ref(false)
const previewLoading = ref(false)
const importLoading = ref(false)
const actionLoading = ref(false)
const moduleError = ref('')
const fileInput = ref(null)
const pendingFile = ref(null)
const previewResult = ref(null)
const previewDialogVisible = ref(false)
const qualityDialogVisible = ref(false)
const resourceDialogVisible = ref(false)
const eventDialogVisible = ref(false)
const events = ref([])
const editingRowId = ref(null)
const editValues = ref({})
const collapsedRows = reactive(new Set())
const grid = reactive({
  headers: [],
  rows: [],
  total: 0,
  page: 1,
  pageSize: 120,
  editable: false,
})
const classification = reactive({
  majorSections: [],
  chapters: [],
})
const resourceForm = reactive(emptyResourceForm('labor'))

const selectedVersion = computed(
  () => versions.value.find((version) => version.id === selectedVersionId.value) || null,
)
const qualityIssues = computed(() => selectedVersion.value?.quality?.issues || [])
const qualityBlockerCount = computed(
  () => selectedVersion.value?.quality?.blocker_count ?? qualityIssues.value.filter((issue) => issue.severity === 'error').length,
)
const qualityWarningCount = computed(
  () => selectedVersion.value?.quality?.warning_count ?? qualityIssues.value.filter((issue) => issue.severity === 'warning').length,
)
const visibleRows = computed(() => {
  const rows = grid.rows.filter((row) => !['title', 'header'].includes(row.row_kind))
  if (activeSheet.value !== 'enterprise' || !collapsedRows.size) return rows
  return rows.filter(
    (row) => row.row_kind !== 'component' || !collapsedRows.has(row.parent_row_number),
  )
})
const availableChapterOptions = computed(() => {
  if (!selectedMajorSectionId.value) return []
  return classification.chapters.filter(
    (option) => Number(option.parent_section_id) === Number(selectedMajorSectionId.value),
  )
})

onMounted(loadVersions)

const isActiveVersion = (version) => Boolean(version?.is_active || version?.status === 'active')

async function applyActiveQuotaItemRoute() {
  if (
    routeQuotaItemApplied
    || !routeRequiresActiveVersion
    || !routeQuotaItemId
    || !isActiveVersion(selectedVersion.value)
  ) return
  routeQuotaItemApplied = true
  activeSheet.value = 'enterprise'
  try {
    const response = await enterpriseQuotaV2Api.activeItem(routeQuotaItemId)
    const item = quotaV2Data(response) || {}
    keyword.value = item.quota_code || item.item_name || ''
  } catch {
    keyword.value = ''
  }
}

async function loadVersions(preferredId = null) {
  versionsLoading.value = true
  moduleError.value = ''
  try {
    const response = await enterpriseQuotaV2Api.versions()
    versions.value = quotaV2Data(response) || []
    const routeActiveVersion =
      versions.value.find((item) => item.id === routeActiveVersionId && isActiveVersion(item)) ||
      versions.value.find((item) => isActiveVersion(item))
    const preferred =
      versions.value.find((item) => item.id === preferredId) ||
      (routeRequiresActiveVersion ? routeActiveVersion : null) ||
      versions.value.find((item) => item.id === selectedVersionId.value) ||
      versions.value.find((item) => isActiveVersion(item)) ||
      versions.value.find((item) => item.schema_version === 'enterprise-quota-v2' && item.status === 'draft') ||
      versions.value[0]
    selectedVersionId.value = preferred?.id || null
    if (activeSheet.value !== 'versions') {
      await applyActiveQuotaItemRoute()
      await loadRows()
    }
  } catch (error) {
    if ([403, 404].includes(error.response?.status)) {
      moduleError.value = '企业定额 2.0 工作台尚未开启或当前账号没有查看权限。'
      return
    }
    ElMessage.error(quotaV2ErrorMessage(error, '加载企业定额版本失败'))
  } finally {
    versionsLoading.value = false
  }
}

async function loadRows() {
  cancelRowEdit()
  if (!selectedVersionId.value || activeSheet.value === 'versions') {
    Object.assign(grid, { headers: [], rows: [], total: 0, editable: false })
    return
  }
  gridLoading.value = true
  try {
    const response = await enterpriseQuotaV2Api.rows(selectedVersionId.value, {
      sheet: activeSheet.value,
      keyword: keyword.value || undefined,
      major_section_id:
        activeSheet.value === 'enterprise' ? selectedMajorSectionId.value || undefined : undefined,
      chapter_id:
        activeSheet.value === 'enterprise' ? selectedChapterId.value || undefined : undefined,
      page: grid.page,
      page_size: grid.pageSize,
    })
    const data = quotaV2Data(response) || {}
    grid.headers = data.headers || []
    grid.rows = data.rows || []
    grid.total = data.total || 0
    grid.editable = Boolean(data.editable && props.canEdit)
    if (activeSheet.value === 'enterprise') {
      classification.majorSections = data.classification?.major_sections || []
      classification.chapters = data.classification?.chapters || []
      selectedMajorSectionId.value = data.classification?.selected_major_section_id || null
      selectedChapterId.value = data.classification?.selected_chapter_id || null
    }
    if (data.version) replaceVersion(data.version)
  } catch (error) {
    ElMessage.error(quotaV2ErrorMessage(error, '加载工作表失败'))
  } finally {
    gridLoading.value = false
  }
}

function handleVersionChange() {
  grid.page = 1
  keyword.value = ''
  selectedMajorSectionId.value = null
  selectedChapterId.value = null
  classification.majorSections = []
  classification.chapters = []
  collapsedRows.clear()
  loadRows()
}

function handleSheetChange() {
  grid.page = 1
  keyword.value = ''
  collapsedRows.clear()
  if (activeSheet.value !== 'versions') loadRows()
}

function searchRows() {
  grid.page = 1
  loadRows()
}

function handleMajorSectionChange() {
  if (
    selectedChapterId.value
    && !availableChapterOptions.value.some((option) => option.id === selectedChapterId.value)
  ) {
    selectedChapterId.value = null
  }
  grid.page = 1
  collapsedRows.clear()
  loadRows()
}

function handleChapterChange() {
  grid.page = 1
  collapsedRows.clear()
  loadRows()
}

function clearClassificationFilters() {
  selectedMajorSectionId.value = null
  selectedChapterId.value = null
  grid.page = 1
  collapsedRows.clear()
  loadRows()
}

function selectVersion(version) {
  selectedVersionId.value = version.id
  activeSheet.value = 'enterprise'
  handleVersionChange()
}

function replaceVersion(version) {
  const index = versions.value.findIndex((item) => item.id === version.id)
  if (index >= 0) versions.value.splice(index, 1, version)
  else versions.value.unshift(version)
}

function openFilePicker() {
  fileInput.value?.click()
}

async function handleFileSelected(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  pendingFile.value = file
  previewLoading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await enterpriseQuotaV2Api.previewImport(formData)
    previewResult.value = quotaV2Data(response)
    previewDialogVisible.value = true
  } catch (error) {
    pendingFile.value = null
    ElMessage.error(quotaV2ErrorMessage(error, 'Excel 结构检查失败'))
  } finally {
    previewLoading.value = false
  }
}

async function confirmImport() {
  if (!pendingFile.value) return
  importLoading.value = true
  try {
    const formData = new FormData()
    formData.append('file', pendingFile.value)
    const response = await enterpriseQuotaV2Api.importDraft(formData)
    const version = quotaV2Data(response)
    previewDialogVisible.value = false
    pendingFile.value = null
    previewResult.value = null
    activeSheet.value = 'enterprise'
    ElMessage.success('企业定额 2.0 已导入为草稿，生效主库未被替换')
    await loadVersions(version?.id)
  } catch (error) {
    ElMessage.error(quotaV2ErrorMessage(error, '导入企业定额 2.0 失败'))
  } finally {
    importLoading.value = false
  }
}

function startRowEdit(row) {
  if (!grid.editable || row.entity_type !== 'resource' || !row.entity_id) return
  editingRowId.value = row.id
  editValues.value = { ...row.values }
}

function cancelRowEdit() {
  editingRowId.value = null
  editValues.value = {}
}

async function saveEditedRow(row) {
  if (!selectedVersion.value || !row.entity_id || actionLoading.value) return
  const payload = resourcePayloadFromCells(activeSheet.value, editValues.value)
  payload.expected_revision = selectedVersion.value.revision
  payload.reason = `${activeSheet.value === 'labor' ? '人工' : '材料'}价格库第 ${row.row_number} 行编辑`
  actionLoading.value = true
  try {
    const response = await enterpriseQuotaV2Api.updateResource(
      selectedVersion.value.id,
      row.entity_id,
      payload,
    )
    const data = quotaV2Data(response) || {}
    if (data.version) replaceVersion(data.version)
    cancelRowEdit()
    ElMessage.success(`保存成功，已联动重算 ${data.recalculation?.recalculated_item_count || 0} 条企业定额`)
    await loadRows()
  } catch (error) {
    ElMessage.error(quotaV2ErrorMessage(error, '保存价格并重算失败'))
  } finally {
    actionLoading.value = false
  }
}

function openCreateResource(libraryKind, issue = null) {
  Object.assign(resourceForm, emptyResourceForm(libraryKind))
  const evidence = issue?.evidence || {}
  if (issue) {
    resourceForm.resource_name = evidence.resource_name || ''
    resourceForm.library_kind = evidence.library_kind || libraryKind || 'material'
    resourceForm.resource_type = resourceForm.library_kind === 'labor' ? '人工' : '辅材'
  }
  resourceDialogVisible.value = true
}

async function createResource() {
  if (!selectedVersion.value) return
  if (!resourceForm.resource_name.trim() || !resourceForm.unit.trim() || resourceForm.price === null) {
    ElMessage.warning('名称、单位和价格为必填项')
    return
  }
  actionLoading.value = true
  try {
    const payload = {
      ...resourceForm,
      expected_revision: selectedVersion.value.revision,
      reason: '在企业定额 2.0 工作台补充价格资源',
    }
    const response = await enterpriseQuotaV2Api.createResource(selectedVersion.value.id, payload)
    const data = quotaV2Data(response) || {}
    if (data.version) replaceVersion(data.version)
    resourceDialogVisible.value = false
    qualityDialogVisible.value = false
    activeSheet.value = resourceForm.library_kind
    ElMessage.success(`新增成功，已联动重算 ${data.recalculation?.recalculated_item_count || 0} 条企业定额`)
    await loadRows()
  } catch (error) {
    ElMessage.error(quotaV2ErrorMessage(error, '新增价格记录失败'))
  } finally {
    actionLoading.value = false
  }
}

function repairMissingResource(issue) {
  openCreateResource(issue.evidence?.library_kind || 'material', issue)
}

async function recalculateSelectedVersion() {
  if (!selectedVersion.value) return
  actionLoading.value = true
  try {
    const response = await enterpriseQuotaV2Api.recalculate(selectedVersion.value.id, {
      expected_revision: selectedVersion.value.revision,
      reason: '企业定额 2.0 工作台手工全量重算',
    })
    const data = quotaV2Data(response) || {}
    if (data.version) replaceVersion(data.version)
    ElMessage.success(`重算完成，共更新 ${data.recalculation?.recalculated_item_count || 0} 条定额`)
    await loadRows()
  } catch (error) {
    ElMessage.error(quotaV2ErrorMessage(error, '全量重算失败'))
  } finally {
    actionLoading.value = false
  }
}

async function cloneSelectedVersion() {
  if (selectedVersion.value) await cloneVersion(selectedVersion.value)
}

async function cloneVersion(version) {
  try {
    const { value } = await ElMessageBox.prompt('请输入新草稿名称', '创建编辑草稿', {
      inputValue: `${version.version_name} - 编辑草稿`,
      inputValidator: (text) => Boolean(String(text || '').trim()) || '草稿名称不能为空',
      confirmButtonText: '创建',
      cancelButtonText: '取消',
    })
    actionLoading.value = true
    const response = await enterpriseQuotaV2Api.cloneVersion(version.id, {
      version_name: value.trim(),
      reason: '从只读版本创建编辑草稿',
    })
    const draft = quotaV2Data(response)
    ElMessage.success('编辑草稿已创建')
    await loadVersions(draft?.id)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(quotaV2ErrorMessage(error, '创建草稿失败'))
  } finally {
    actionLoading.value = false
  }
}

async function activateSelectedVersion() {
  if (!selectedVersion.value || qualityBlockerCount.value) return
  try {
    const warningText = qualityWarningCount.value
      ? `当前还有 ${qualityWarningCount.value} 个警告。请输入启用原因，提交即表示已核对并接受这些警告。`
      : '请输入本次启用原因。启用后原主库会自动归档。'
    const { value } = await ElMessageBox.prompt(warningText, '启用企业定额主库', {
      inputPlaceholder: '至少填写 4 个字',
      inputValidator: (text) => String(text || '').trim().length >= 4 || '启用原因至少填写 4 个字',
      confirmButtonText: '确认启用',
      cancelButtonText: '取消',
      type: 'warning',
    })
    actionLoading.value = true
    const response = await enterpriseQuotaV2Api.activate(selectedVersion.value.id, {
      expected_revision: selectedVersion.value.revision,
      reason: value.trim(),
      acknowledge_warnings: qualityWarningCount.value > 0,
    })
    const active = quotaV2Data(response)
    ElMessage.success('企业定额主库已安全切换')
    await loadVersions(active?.id)
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(quotaV2ErrorMessage(error, '启用版本失败'))
  } finally {
    actionLoading.value = false
  }
}

async function openEvents(version) {
  try {
    const response = await enterpriseQuotaV2Api.events(version.id, { page: 1, page_size: 100 })
    events.value = quotaV2Items(response)
    eventDialogVisible.value = true
  } catch (error) {
    ElMessage.error(quotaV2ErrorMessage(error, '加载操作记录失败'))
  }
}

function showIssueEvidence(issue) {
  ElMessageBox.alert(`<pre>${escapeHtml(JSON.stringify(issue.evidence || {}, null, 2))}</pre>`, issue.code, {
    dangerouslyUseHTMLString: true,
    customClass: 'quota-v2-evidence-dialog',
  })
}

function toggleRow(row) {
  if (collapsedRows.has(row.row_number)) collapsedRows.delete(row.row_number)
  else collapsedRows.add(row.row_number)
}

function isSectionRow(row) {
  return ['major_section', 'chapter'].includes(row.row_kind)
}

function rowClass(row) {
  return [
    `excel-row-${row.row_kind}`,
    {
      'excel-row-editable': grid.editable && row.entity_type === 'resource',
      'excel-row-editing': editingRowId.value === row.id,
    },
  ]
}

function cellClass(row, column) {
  return {
    'excel-formula-cell': Boolean(row.formulas?.[column]),
    'excel-price-cell': column === 'H' && ['labor', 'material'].includes(activeSheet.value),
  }
}

function cellTitle(row, column) {
  const formula = row.formulas?.[column]
  return formula ? `公式：${formula}` : String(row.values?.[column] ?? '')
}

function columnLetter(index) {
  return String.fromCharCode(65 + index)
}

function columnWidth(sheet, index) {
  if (sheet === 'enterprise') {
    return ['126px', '84px', '220px', '280px', '170px', '150px', '82px', '90px', '105px', '105px', '105px', '105px', '105px'][index] || '120px'
  }
  if (sheet === 'labor') {
    return ['120px', '90px', '210px', '250px', '230px', '90px', '100px', '140px'][index] || '130px'
  }
  if (sheet === 'material') {
    return ['125px', '125px', '90px', '220px', '190px', '170px', '90px', '140px'][index] || '130px'
  }
  return ['125px', '130px', '170px', '220px', '90px', '110px', '110px', '110px', '110px', '120px'][index] || '130px'
}

function displayCell(value) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
  }
  return String(value)
}

function resourcePayloadFromCells(sheet, cells) {
  const numeric = (value) => {
    if (value === '' || value === null || value === undefined) return null
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : value
  }
  if (sheet === 'labor') {
    return {
      resource_code: cells.A,
      resource_type: cells.B,
      resource_name: cells.C,
      work_content: cells.D,
      calculation_rule: cells.E,
      unit: cells.F,
      default_quantity: numeric(cells.G),
      price: numeric(cells.H),
    }
  }
  return {
    category: cells.A,
    resource_code: cells.B,
    resource_type: cells.C,
    resource_name: cells.D,
    specification: cells.E,
    brand: cells.F,
    unit: cells.G,
    price: numeric(cells.H),
  }
}

function emptyResourceForm(libraryKind) {
  return {
    library_kind: libraryKind,
    category: '',
    resource_code: '',
    resource_type: libraryKind === 'labor' ? '人工' : '辅材',
    resource_name: '',
    work_content: '',
    calculation_rule: '',
    specification: '',
    brand: '',
    unit: '',
    default_quantity: libraryKind === 'labor' ? 1 : null,
    price: 0,
  }
}

function versionOptionLabel(version) {
  return `${version.is_active ? '● ' : ''}${version.version_name}（${versionStatusLabel(version.status)}）`
}

function versionStatusLabel(status) {
  return { active: '当前生效', draft: '编辑草稿', archived: '历史归档' }[status] || status || '未知'
}

function versionTagType(status) {
  return { active: 'success', draft: 'primary', archived: 'info' }[status] || 'info'
}

function qualityLabel(status) {
  return { ready: '可启用', warning: '有警告', blocked: '有阻断' }[status] || '待检查'
}

function qualityTagType(status) {
  return { ready: 'success', warning: 'warning', blocked: 'danger' }[status] || 'info'
}

function formatDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}
</script>

<style scoped>
.quota-v2-shell {
  margin: 18px 0 26px;
  padding: 22px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.07);
}

.quota-v2-header,
.quota-v2-status,
.quota-grid-toolbar,
.quota-grid-footer,
.version-center-heading,
.preview-title,
.quality-summary-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.quota-v2-header h3 {
  margin: 2px 0 5px;
  color: #0f172a;
  font-size: 24px;
}

.quota-v2-header span,
.version-center-heading span {
  color: #64748b;
  font-size: 13px;
}

.quota-v2-eyebrow {
  margin: 0;
  color: #2563eb;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.quota-v2-actions,
.quota-grid-search,
.quota-grid-tools,
.quota-version-identity {
  display: flex;
  align-items: center;
  gap: 10px;
}

.quota-v2-actions {
  justify-content: flex-end;
  flex-wrap: wrap;
}

.quota-version-select {
  width: 290px;
}

.quota-file-input {
  display: none;
}

.quota-v2-status {
  margin-top: 18px;
  padding: 14px 16px;
  border-radius: 16px;
  background: linear-gradient(135deg, #f8fbff, #f4f7fb);
}

.quota-version-identity strong,
.quota-version-identity small,
.version-name-cell strong,
.version-name-cell small,
.preview-title strong,
.preview-title span {
  display: block;
}

.quota-version-identity small,
.version-name-cell small,
.preview-title span {
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
}

.quota-status-metrics {
  display: flex;
  gap: 8px;
}

.quota-metric {
  min-width: 90px;
  padding: 8px 12px;
  border: 0;
  border-left: 1px solid #dbe5f0;
  background: transparent;
  text-align: left;
}

button.quota-metric {
  cursor: pointer;
}

.quota-metric span,
.quota-metric strong {
  display: block;
}

.quota-metric span {
  color: #64748b;
  font-size: 11px;
}

.quota-metric strong {
  margin-top: 2px;
  color: #0f172a;
  font-size: 17px;
}

.quality-ready {
  color: #15803d !important;
}

.quality-warning {
  color: #b45309 !important;
}

.quality-blocked {
  color: #dc2626 !important;
}

.quality-unknown {
  color: #64748b !important;
}

.quota-v2-alert {
  margin-top: 14px;
}

.quota-v2-tabs {
  margin-top: 18px;
}

.quota-grid-toolbar {
  align-items: flex-end;
  margin: 2px 0 10px;
}

.quota-grid-filter-area,
.quota-category-filters {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.quota-grid-filter-area {
  min-width: 0;
}

.quota-category-filter {
  display: grid;
  gap: 5px;
  width: 220px;
}

.quota-category-filter span {
  color: #475569;
  font-size: 12px;
  font-weight: 700;
}

.quota-grid-search {
  width: 340px;
}

.excel-hint {
  color: #64748b;
  font-size: 12px;
}

.excel-grid-wrap {
  max-height: 650px;
  overflow: auto;
  border: 1px solid #b9c5d4;
  border-radius: 10px;
  background: #fff;
}

.excel-grid {
  width: max-content;
  min-width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
  color: #1f2937;
  font-size: 12px;
}

.row-number-column {
  width: 48px;
}

.excel-grid th,
.excel-grid td {
  height: 34px;
  padding: 5px 7px;
  overflow: hidden;
  border-right: 1px solid #cbd5e1;
  border-bottom: 1px solid #cbd5e1;
  background: #fff;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.excel-grid thead th {
  position: sticky;
  z-index: 4;
  top: 0;
  height: 46px;
  color: #fff;
  background: #4472c4;
  text-align: center;
}

.excel-grid thead th small,
.excel-grid thead th span {
  display: block;
}

.excel-grid thead th small {
  margin-bottom: 2px;
  color: #dbeafe;
  font-size: 9px;
}

.row-number-head,
.row-number-cell {
  position: sticky;
  z-index: 3;
  left: 0;
  color: #64748b;
  background: #f1f5f9 !important;
  text-align: center;
}

.row-number-head {
  z-index: 6 !important;
}

.excel-row-major_section .section-cell {
  color: #fff;
  background: #2f5597;
  font-size: 14px;
  font-weight: 800;
}

.excel-row-chapter .section-cell {
  color: #17365d;
  background: #d9eaf7;
  font-size: 13px;
  font-weight: 800;
}

.excel-row-quota_item td {
  color: #17365d;
  background: #eaf3f8;
  font-weight: 700;
}

.excel-row-component:hover td,
.excel-row-editable:hover td {
  background: #f0f7ff;
}

.excel-row-editable {
  cursor: cell;
}

.excel-row-editing td {
  background: #fff7d6 !important;
}

.section-cell small {
  margin-left: 12px;
  opacity: 0.74;
  font-weight: 500;
}

.outline-toggle {
  width: 20px;
  height: 20px;
  margin-right: 6px;
  padding: 0;
  border: 1px solid currentColor;
  border-radius: 4px;
  color: inherit;
  background: transparent;
  line-height: 17px;
  cursor: pointer;
}

.outline-indent {
  display: inline-block;
}

.excel-formula-cell {
  background: #f8fbff !important;
}

.excel-price-cell {
  color: #0f5ca8;
  font-weight: 700;
}

.formula-badge {
  margin-left: 4px;
  padding: 0 3px;
  border-radius: 3px;
  color: #fff;
  background: #2563eb;
  font-size: 8px;
}

.excel-cell-input {
  width: 100%;
  height: 26px;
  box-sizing: border-box;
  padding: 2px 5px;
  border: 1px solid #2563eb;
  border-radius: 3px;
  outline: none;
  color: #111827;
  background: #fff;
  font: inherit;
}

.empty-grid-cell {
  height: 150px !important;
  color: #94a3b8;
  text-align: center;
}

.quota-grid-footer {
  margin-top: 10px;
  color: #64748b;
  font-size: 12px;
}

.version-center {
  padding-top: 4px;
}

.version-center-heading {
  margin-bottom: 12px;
}

.version-center-heading strong {
  display: block;
  margin-bottom: 4px;
}

.preview-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin: 18px 0;
}

.preview-metrics div {
  padding: 12px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
}

.preview-metrics span,
.preview-metrics strong {
  display: block;
}

.preview-metrics span {
  color: #64748b;
  font-size: 11px;
}

.preview-metrics strong {
  margin-top: 4px;
  color: #0f172a;
  font-size: 21px;
}

.preview-issues {
  max-height: 300px;
  margin-top: 14px;
  overflow: auto;
}

.preview-issues > div {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid #eef2f7;
}

.resource-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}

@media (max-width: 1180px) {
  .quota-v2-header,
  .quota-v2-status,
  .quota-grid-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .quota-status-metrics {
    width: 100%;
    overflow-x: auto;
  }

  .quota-grid-search {
    width: 100%;
  }

  .quota-grid-filter-area {
    width: 100%;
  }

  .quota-category-filters {
    width: 100%;
  }

  .quota-v2-actions,
  .quota-grid-tools {
    justify-content: flex-start;
  }
}
</style>
