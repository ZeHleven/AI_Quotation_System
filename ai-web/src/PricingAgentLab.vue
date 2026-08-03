<template>
  <div class="pricing-agent-page">
    <div class="content-heading">
      <div>
        <p class="eyebrow">旁路实验 · Pricing Agent v1.1</p>
        <h2>智能组价实验室</h2>
        <p class="page-description">独立验证存档数据、企业数据和行业数据；只有人工整单确认后才写入现有报价草稿。</p>
      </div>
      <el-tag type="warning" effect="plain">默认隔离</el-tag>
    </div>

    <el-alert
      v-if="featureDisabled"
      type="info"
      show-icon
      :closable="false"
      title="报价 Agent 第一版尚未开启"
      description="请在开发环境开启 FEATURE_PRICING_AGENT=true，现有报价流程不受影响。"
    />

    <template v-else>
      <el-alert
        class="boundary-alert"
        type="success"
        show-icon
        :closable="false"
        title="准确模式只做精准匹配；准确+近似使用关键词与向量混合召回，近似候选必须人工采用，行业数据统一标记为“行业数据·AI推算”。"
      />
      <el-alert
        v-if="capabilitiesLoaded && !correctedExactRulesAvailable"
        class="boundary-alert"
        type="warning"
        show-icon
        :closable="false"
        title="准确匹配修正规则等待后端重启，当前页面暂不执行组价。"
      />

      <section class="lab-card">
        <div class="section-heading">
          <div>
            <span class="step-index">1</span>
            <div>
              <h3>存档数据</h3>
              <p>上传历史带价清单，系统按固定字段自动识别，无需人工列映射。</p>
            </div>
          </div>
          <el-upload
            accept=".xlsx,.xlsm"
            :show-file-list="false"
            :http-request="uploadArchive"
          >
            <el-button type="primary" plain :loading="archiveUploading">导入带价清单</el-button>
          </el-upload>
        </div>

        <div class="storage-strip">
          <span>存储方式：{{ capabilities.archive_storage?.backend || '-' }}</span>
          <span>已用：{{ formatBytes(archiveStorage.used_bytes) }}</span>
          <span>账户上限：{{ formatBytes(archiveStorage.quota_bytes) }}</span>
        </div>
        <el-table :data="archives" empty-text="尚未导入存档报价文件" size="small">
          <el-table-column prop="original_filename" label="文件" min-width="220" />
          <el-table-column prop="indexed_row_count" label="可检索报价行" width="120" />
          <el-table-column label="解析状态" width="110">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ready' ? 'success' : 'info'" size="small">
                {{ row.status === 'ready' ? '可用' : row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="导入时间" width="180">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" align="right">
            <template #default="{ row }">
              <el-button text type="danger" @click="disableArchive(row)">停用</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="lab-card">
        <div class="section-heading">
          <div>
            <span class="step-index">2</span>
            <div>
              <h3>待套价清单与项目条件</h3>
              <p>地区、业态和装修程度作为查询上下文，仅用于软排序，不伪装成存档文件事实。</p>
            </div>
          </div>
          <el-upload
            accept=".xlsx,.xlsm"
            :show-file-list="false"
            :http-request="previewDemand"
          >
            <el-button plain :loading="demandUploading">导入需求清单</el-button>
          </el-upload>
        </div>

        <el-form class="context-form" label-position="top">
          <el-form-item label="地区（市）">
            <el-select v-model="form.context.city" filterable allow-create placeholder="例如：杭州市">
              <el-option v-for="item in cityOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="行业 / 业态">
            <el-select v-model="form.context.project_type" filterable allow-create placeholder="例如：写字楼">
              <el-option v-for="item in projectTypeOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="装修程度">
            <el-select v-model="form.context.decoration_level" filterable allow-create placeholder="例如：精装">
              <el-option v-for="item in decorationOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
        </el-form>

        <el-table :data="form.lines" empty-text="请导入待套价的 .xlsx / .xlsm 清单" max-height="360">
          <el-table-column prop="row_key" label="行" width="120" />
          <el-table-column prop="item_name" label="项目名称" min-width="200" />
          <el-table-column prop="specification" label="规格 / 特征" min-width="240" show-overflow-tooltip />
          <el-table-column prop="quantity" label="工程量" width="100" />
          <el-table-column prop="unit" label="单位" width="80" />
          <el-table-column label="操作" width="70" align="right">
            <template #default="{ $index }">
              <el-button text type="danger" @click="form.lines.splice($index, 1)">移除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="demandSummary.line_count" class="parse-summary">
          已识别 {{ demandSummary.line_count }} 条有效项目；
          自动忽略 {{ demandSummary.skipped_non_item_row_count || 0 }} 条汇总、序号或章节行。
        </div>
      </section>

      <section class="lab-card">
        <div class="section-heading">
          <div>
            <span class="step-index">3</span>
            <div>
              <h3>组价策略</h3>
              <p>来源可单选或多选。存档优先，其次企业，行业数据只补仍未命中的行。</p>
            </div>
          </div>
        </div>

        <div class="strategy-grid">
          <div>
            <span class="field-label">组价依据</span>
            <el-checkbox-group v-model="form.sources">
              <el-checkbox label="archive">存档数据</el-checkbox>
              <el-checkbox label="enterprise">企业数据</el-checkbox>
              <el-checkbox
                label="industry"
                :disabled="form.mode === 'exact' || !industryAvailable"
              >
                行业数据
              </el-checkbox>
            </el-checkbox-group>
          </div>
          <div>
            <span class="field-label">匹配形式</span>
            <el-radio-group v-model="form.mode" @change="handleModeChange">
              <el-radio value="exact">准确</el-radio>
              <el-radio value="expanded" :disabled="!expandedAvailable">准确+近似（可匹配更多项）</el-radio>
            </el-radio-group>
            <div v-if="form.mode === 'expanded'" class="hybrid-status">
              <el-tag
                :type="hybridVectorConfigured ? 'success' : 'warning'"
                size="small"
                effect="plain"
              >
                {{ hybridVectorConfigured ? '关键词＋向量＋RRF' : '向量不可用，当前降级关键词' }}
              </el-tag>
              <span>近似结果不会自动套价，必须人工采用。</span>
            </div>
          </div>
        </div>

        <div class="run-action">
          <el-button
            type="primary"
            size="large"
            :loading="running"
            :disabled="!correctedExactRulesAvailable"
            @click="runAgent"
          >
            开始旁路组价
          </el-button>
          <span>运行和候选复核阶段不会改动现有报价；确认生成草稿仍不会修改企业定额库</span>
        </div>
      </section>

      <section v-if="result.lines?.length" class="lab-card result-card">
        <div class="section-heading">
          <div>
            <span class="step-index done">4</span>
            <div>
              <h3>组价结果</h3>
              <p>
                已计价 {{ result.summary?.priced_count || 0 }} / {{ result.summary?.row_count || 0 }}，
                需复核 {{ result.summary?.requires_review_count || 0 }} 项。
              </p>
            </div>
          </div>
          <div class="result-actions">
            <el-button
              v-if="!confirmation.confirmed"
              type="primary"
              :loading="confirming"
              :disabled="!canConfirmToDraft"
              @click="confirmToQuoteDraft"
            >
              确认并生成报价草稿
            </el-button>
            <el-button
              v-else
              type="success"
              @click="openQuoteDraft"
            >
              进入报价草稿
            </el-button>
          </div>
        </div>
        <div v-if="confirmation.confirmed" class="confirmation-strip">
          <div>
            <strong>已写入现有报价草稿</strong>
            <span>
              草稿 {{ confirmation.preview_draft_id }} ·
              已计价 {{ confirmation.priced_row_count ?? result.summary?.priced_count ?? 0 }} 项 ·
              待补价 {{ confirmation.unpriced_row_count ?? unpricedResultCount }} 项 ·
              {{ formatDate(confirmation.confirmed_at) }}
            </span>
          </div>
          <el-tag
            :type="Number(confirmation.unpriced_row_count || 0) > 0 ? 'warning' : 'success'"
            effect="plain"
          >
            {{ Number(confirmation.unpriced_row_count || 0) > 0 ? '补齐后方可下发' : '可继续编辑' }}
          </el-tag>
        </div>
        <el-alert
          v-else-if="unpricedResultCount > 0"
          class="result-warning"
          type="warning"
          :closable="false"
          show-icon
          :title="`当前有 ${unpricedResultCount} 项待补价。可在此直接补价，也可带占位行生成报价草稿；未补齐前不能最终下发。`"
        />
        <el-table :data="result.lines" border>
          <el-table-column prop="item_name" label="项目名称" min-width="190" />
          <el-table-column prop="unit" label="单位" width="70" />
          <el-table-column prop="quantity" label="工程量" width="90" />
          <el-table-column label="依据" width="150">
            <template #default="{ row }">
              <el-tag :type="sourceTagType(row.selected_source)" size="small">
                {{ row.source_label || (row.candidates?.length ? '候选待确认' : '未匹配') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="匹配" width="120">
            <template #default="{ row }">
              {{ row.match_type ? matchLabel(row.match_type) : (row.candidates?.length ? '近似候选' : '未匹配') }}
            </template>
          </el-table-column>
          <el-table-column label="单价（元）" width="150" align="right">
            <template #default="{ row }">
              <el-input-number
                v-if="!row.unit_price || row.manual_price_entered"
                v-model="row.manual_price_input"
                :min="0.01"
                :precision="2"
                :controls="false"
                :disabled="confirmation.confirmed || savingManualPriceLineUuid === row.line_uuid"
                placeholder="人工补价"
                size="small"
                style="width: 125px"
                @change="saveManualPrice(row)"
              />
              <span v-else>{{ formatPrice(row.unit_price) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="合价（元）" width="130" align="right">
            <template #default="{ row }">{{ formatPrice(row.total_price) }}</template>
          </el-table-column>
          <el-table-column label="复核" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.manual_price_entered" type="warning" size="small">人工补价</el-tag>
              <el-tag v-else-if="row.manual_candidate_selected" type="success" size="small">已人工选择</el-tag>
              <el-tag v-else-if="row.requires_review && row.candidates?.length" type="warning" size="small">待选候选</el-tag>
              <el-tag v-else-if="row.requires_review" type="warning" size="small">需复核</el-tag>
              <span v-else-if="row.unit_price">精准</span>
              <span v-else>待补价</span>
            </template>
          </el-table-column>
          <el-table-column label="证据" width="80" align="right">
            <template #default="{ row }">
              <el-button text type="primary" @click="openEvidence(row)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <el-drawer v-model="evidenceDrawer.visible" title="组价证据链" size="48%">
      <template v-if="evidenceDrawer.row">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="项目">{{ evidenceDrawer.row.item_name }}</el-descriptions-item>
          <el-descriptions-item label="最终依据">
            {{ evidenceDrawer.row.source_label || (evidenceDrawer.row.candidates?.length ? '候选待确认' : '未匹配') }}
          </el-descriptions-item>
          <el-descriptions-item label="基础查询">{{ evidenceDrawer.row.query_plan?.base_query }}</el-descriptions-item>
          <el-descriptions-item label="上下文查询">{{ evidenceDrawer.row.query_plan?.context_query }}</el-descriptions-item>
          <el-descriptions-item label="上下文规则">仅软排序，不作硬过滤</el-descriptions-item>
          <el-descriptions-item label="检索通道">
            {{ (evidenceDrawer.row.query_plan?.channels || []).join(' + ') || '-' }}
          </el-descriptions-item>
        </el-descriptions>
        <h4>候选记录</h4>
        <el-table :data="evidenceDrawer.row.candidates || []" size="small">
          <el-table-column prop="source_label" label="来源" width="100" />
          <el-table-column prop="item_name" label="项目" min-width="180" />
          <el-table-column prop="specification" label="规格 / 特征" min-width="220" show-overflow-tooltip />
          <el-table-column prop="unit_price" label="单价" width="100" />
          <el-table-column label="召回方式" width="110">
            <template #default="{ row: candidate }">{{ matchLabel(candidate.match_type) }}</template>
          </el-table-column>
          <el-table-column label="综合评分" width="90">
            <template #default="{ row: candidate }">{{ formatScore(candidate.score) }}</template>
          </el-table-column>
          <el-table-column label="向量评分" width="90">
            <template #default="{ row: candidate }">{{ formatScore(candidate.vector_score) }}</template>
          </el-table-column>
          <el-table-column prop="archive_filename" label="原文件" min-width="180" />
          <el-table-column label="操作" width="120" align="right">
            <template #default="{ row: candidate }">
              <el-button
                v-if="candidate.unit_price && correctedExactRulesAvailable"
                text
                type="primary"
                :loading="selectingCandidateKey === candidateKey(candidate)"
                :disabled="confirmation.confirmed"
                @click="useCandidate(candidate)"
              >
                采用此价格
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  pricingAgentApi,
  pricingAgentErrorMessage,
  pricingAgentResponseData,
} from './pricingAgentApi'

const capabilities = reactive({})
const archives = ref([])
const archiveStorage = reactive({ used_bytes: 0, quota_bytes: 0 })
const featureDisabled = ref(false)
const capabilitiesLoaded = ref(false)
const archiveUploading = ref(false)
const demandUploading = ref(false)
const running = ref(false)
const confirming = ref(false)
const selectingCandidateKey = ref('')
const savingManualPriceLineUuid = ref('')
const currentRunUuid = ref('')
const result = reactive({ summary: null, lines: [] })
const confirmation = reactive({ confirmed: false })
const demandSummary = reactive({ line_count: 0, skipped_non_item_row_count: 0 })
const evidenceDrawer = reactive({ visible: false, row: null })
const form = reactive({
  context: { city: '', project_type: '', decoration_level: '' },
  sources: ['archive', 'enterprise'],
  mode: 'exact',
  lines: [],
})

const cityOptions = ['杭州市', '上海市', '南京市', '苏州市', '宁波市', '深圳市', '东莞市']
const projectTypeOptions = ['住宅', '餐厅', '写字楼', '商业空间', '酒店', '学校', '医院']
const decorationOptions = ['简易', '标准', '精装', '高端精装']
const expandedAvailable = computed(() =>
  capabilities.match_modes?.find((item) => item.value === 'expanded')?.available === true
)
const expandedCapability = computed(() =>
  capabilities.match_modes?.find((item) => item.value === 'expanded') || {}
)
const hybridVectorConfigured = computed(() =>
  expandedCapability.value.vector_status === 'configured'
)
const industryAvailable = computed(() =>
  capabilities.sources?.find((item) => item.value === 'industry')?.available === true
)
const correctedExactRulesAvailable = computed(() =>
  capabilities.rules_version === 'pricing-agent-exact-v1.1'
)
const canConfirmToDraft = computed(() =>
  Boolean(currentRunUuid.value)
  && !confirmation.confirmed
  && Boolean(result.lines?.length)
)
const unpricedResultCount = computed(() =>
  (result.lines || []).filter(
    (line) => Number(line.unit_price) <= 0 || Number(line.total_price) <= 0,
  ).length
)

function responseErrorCode(error) {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : detail?.code
}

async function loadCapabilities() {
  try {
    Object.assign(capabilities, pricingAgentResponseData(await pricingAgentApi.capabilities()))
    featureDisabled.value = false
  } catch (error) {
    if (error.response?.status === 403 && responseErrorCode(error) === 'FEATURE_DISABLED') {
      featureDisabled.value = true
      return
    }
    ElMessage.error(pricingAgentErrorMessage(error, '加载报价 Agent 能力失败'))
  } finally {
    capabilitiesLoaded.value = true
  }
}

async function loadArchives() {
  if (featureDisabled.value) return
  try {
    const data = pricingAgentResponseData(await pricingAgentApi.archives())
    archives.value = data?.items || []
    Object.assign(archiveStorage, data?.storage || {})
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '加载存档数据失败'))
  }
}

async function uploadArchive({ file }) {
  archiveUploading.value = true
  try {
    const body = new FormData()
    body.append('file', file)
    const response = await pricingAgentApi.uploadArchive(body)
    ElMessage.success(response?.data?.message || '存档数据已导入')
    await loadArchives()
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '存档数据导入失败'))
  } finally {
    archiveUploading.value = false
  }
}

async function disableArchive(row) {
  try {
    await ElMessageBox.confirm(
      `停用“${row.original_filename}”后不再参与新组价，原文件仍保留。`,
      '停用存档数据',
      { type: 'warning' },
    )
    await pricingAgentApi.disableArchive(row.archive_uuid)
    ElMessage.success('存档数据已停用')
    await loadArchives()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(pricingAgentErrorMessage(error, '停用失败'))
  }
}

async function previewDemand({ file }) {
  demandUploading.value = true
  try {
    const body = new FormData()
    body.append('file', file)
    const data = pricingAgentResponseData(await pricingAgentApi.previewDemand(body))
    form.lines = data?.lines || []
    Object.assign(demandSummary, data?.summary || {})
    const skipped = Number(demandSummary.skipped_non_item_row_count || 0)
    ElMessage.success(
      skipped
        ? `已识别 ${form.lines.length} 条待套价项目，自动忽略 ${skipped} 条非项目行`
        : `已自动识别 ${form.lines.length} 条待套价项目`,
    )
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '需求清单解析失败'))
  } finally {
    demandUploading.value = false
  }
}

function handleModeChange(mode) {
  if (mode === 'exact') {
    form.sources = form.sources.filter((source) => source !== 'industry')
  }
}

function validateRun() {
  if (!correctedExactRulesAvailable.value) return '准确匹配修正规则尚未加载，请先重启后端服务'
  if (!form.lines.length) return '请先导入待套价需求清单'
  if (!form.context.city || !form.context.project_type || !form.context.decoration_level) {
    return '请选择地区、行业 / 业态和装修程度'
  }
  if (!form.sources.length) return '请至少选择一个组价依据'
  return ''
}

async function runAgent() {
  const issue = validateRun()
  if (issue) {
    ElMessage.warning(issue)
    return
  }
  running.value = true
  try {
    const data = pricingAgentResponseData(await pricingAgentApi.createRun({
      mode: form.mode,
      sources: form.sources,
      context: form.context,
      lines: form.lines.map((line) => ({
        row_key: line.row_key,
        item_code: line.item_code || null,
        item_name: line.item_name,
        specification: line.specification || null,
        quantity: line.quantity || null,
        unit: line.unit || null,
      })),
    }))
    applyRunData(data)
    ElMessage.success('旁路组价已完成')
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '旁路组价失败'))
  } finally {
    running.value = false
  }
}

function openEvidence(row) {
  evidenceDrawer.row = row
  evidenceDrawer.visible = true
}

function applyRunData(data = {}, { updateUrl = true } = {}) {
  const runResult = data?.result || {}
  currentRunUuid.value = data?.run_uuid || currentRunUuid.value
  result.summary = runResult.summary || data?.summary || {}
  result.lines = (runResult.lines || []).map((line) => ({
    ...line,
    manual_price_input: line.manual_price_entered && Number(line.unit_price) > 0
      ? Number(line.unit_price)
      : null,
  }))
  Object.keys(confirmation).forEach((key) => delete confirmation[key])
  Object.assign(confirmation, data?.confirmation || { confirmed: false })
  if (evidenceDrawer.row) {
    evidenceDrawer.row = result.lines.find(
      (line) => line.line_uuid === evidenceDrawer.row.line_uuid
        || line.row_key === evidenceDrawer.row.row_key,
    ) || null
  }
  if (updateUrl && currentRunUuid.value) {
    const url = new URL(window.location.href)
    url.searchParams.set('run_uuid', currentRunUuid.value)
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
  }
}

function candidateKey(candidate = {}) {
  return `${candidate.source || ''}:${candidate.source_record_id || ''}`
}

async function useCandidate(candidate) {
  const row = evidenceDrawer.row
  if (
    !row?.line_uuid
    || !currentRunUuid.value
    || candidate.unit_price === null
    || candidate.unit_price === undefined
  ) return
  selectingCandidateKey.value = candidateKey(candidate)
  try {
    const data = pricingAgentResponseData(await pricingAgentApi.selectCandidate(
      currentRunUuid.value,
      row.line_uuid,
      {
        source: candidate.source,
        source_record_id: String(candidate.source_record_id),
      },
    ))
    applyRunData(data)
    ElMessage.success(`已采用 ${formatPrice(candidate.unit_price)} 元，选择结果已持久化`)
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '保存人工候选失败'))
  } finally {
    selectingCandidateKey.value = ''
  }
}

async function saveManualPrice(row) {
  if (
    !row?.line_uuid
    || !currentRunUuid.value
    || confirmation.confirmed
  ) return
  const unitPrice = Number(row.manual_price_input)
  if (!Number.isFinite(unitPrice) || unitPrice <= 0) {
    ElMessage.warning('人工补价必须大于 0')
    return
  }
  savingManualPriceLineUuid.value = row.line_uuid
  try {
    const data = pricingAgentResponseData(await pricingAgentApi.setManualPrice(
      currentRunUuid.value,
      row.line_uuid,
      {
        unit_price: unitPrice,
        reason: '未匹配项目人工补价',
      },
    ))
    applyRunData(data)
    ElMessage.success(`人工补价 ${formatPrice(unitPrice)} 元已保存`)
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '保存人工补价失败'))
  } finally {
    savingManualPriceLineUuid.value = ''
  }
}

async function confirmToQuoteDraft() {
  if (!canConfirmToDraft.value) return
  const unpricedNotice = unpricedResultCount.value > 0
    ? `其中 ${unpricedResultCount.value} 项将作为待补价占位行进入草稿，补齐前不能最终下发。`
    : ''
  try {
    await ElMessageBox.confirm(
      `将当前 ${result.lines.length} 项组价结果写入现有报价草稿。${unpricedNotice}生成后，本次 Agent 结果将冻结，仍可在报价草稿中继续修改。`,
      '确认生成报价草稿',
      {
        type: 'warning',
        confirmButtonText: '确认生成',
        cancelButtonText: '继续复核',
      },
    )
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    throw error
  }

  confirming.value = true
  try {
    const data = pricingAgentResponseData(
      await pricingAgentApi.confirmToQuoteDraft(currentRunUuid.value),
    )
    Object.keys(confirmation).forEach((key) => delete confirmation[key])
    Object.assign(confirmation, data || { confirmed: true })
    ElMessage.success(
      Number(data?.unpriced_row_count || 0) > 0
        ? `已写入报价草稿，仍有 ${data.unpriced_row_count} 项待补价`
        : '已写入现有报价草稿，可继续编辑和保存',
    )
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '生成报价草稿失败'))
  } finally {
    confirming.value = false
  }
}

function openQuoteDraft() {
  if (!confirmation.draft_url) {
    ElMessage.warning('报价草稿入口不存在')
    return
  }
  window.location.assign(confirmation.draft_url)
}

async function restoreRunFromUrl() {
  const runUuid = new URLSearchParams(window.location.search || '').get('run_uuid')
  if (!runUuid || featureDisabled.value) return
  try {
    const data = pricingAgentResponseData(await pricingAgentApi.runDetail(runUuid))
    applyRunData(data, { updateUrl: false })
    ElMessage.success('已恢复上次组价结果和人工候选选择')
  } catch (error) {
    ElMessage.error(pricingAgentErrorMessage(error, '恢复组价结果失败'))
  }
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

function formatPrice(value) {
  if (value === null || value === undefined || value === '') return '-'
  return Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatScore(value) {
  if (value === null || value === undefined || value === '') return '-'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '-'
  return parsed.toFixed(3)
}

function sourceTagType(source) {
  return {
    archive: 'success',
    enterprise: 'primary',
    industry: 'warning',
    manual: 'warning',
  }[source] || 'info'
}

function matchLabel(value) {
  return {
    code_exact: '编码精准',
    name_exact: '名称精准',
    lexical_similar: '近似候选',
    keyword_similar: '关键词近似',
    semantic_similar: '向量近似',
    hybrid_similar: '混合近似',
    ai_estimate: 'AI 推算',
    manual_price: '人工补价',
  }[value] || '未匹配'
}

onMounted(async () => {
  await loadCapabilities()
  await loadArchives()
  await restoreRunFromUrl()
})
</script>

<style scoped>
.pricing-agent-page {
  display: grid;
  gap: 18px;
}

.content-heading,
.section-heading,
.section-heading > div,
.storage-strip,
.run-action,
.confirmation-strip,
.confirmation-strip > div {
  display: flex;
  align-items: center;
}

.parse-summary {
  margin-top: 10px;
  color: #667085;
  font-size: 13px;
}

.hybrid-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: #667085;
  font-size: 12px;
}

.content-heading,
.section-heading {
  justify-content: space-between;
  gap: 16px;
}

.content-heading h2,
.section-heading h3 {
  margin: 0;
}

.eyebrow {
  margin: 0 0 6px;
  color: #667085;
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.page-description,
.section-heading p {
  margin: 6px 0 0;
  color: #667085;
}

.boundary-alert {
  border-radius: 16px;
}

.lab-card {
  padding: 22px;
  border: 1px solid rgba(15, 23, 42, .08);
  border-radius: 20px;
  background: rgba(255, 255, 255, .92);
  box-shadow: 0 14px 34px rgba(15, 23, 42, .06);
}

.step-index {
  display: inline-grid;
  width: 32px;
  height: 32px;
  margin-right: 12px;
  place-items: center;
  border-radius: 10px;
  color: #2563eb;
  background: #eff6ff;
  font-weight: 700;
}

.step-index.done {
  color: #047857;
  background: #ecfdf5;
}

.storage-strip {
  flex-wrap: wrap;
  gap: 8px 22px;
  margin: 18px 0 12px;
  padding: 10px 14px;
  border-radius: 12px;
  color: #475467;
  background: #f8fafc;
  font-size: 13px;
}

.context-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 18px;
}

.context-form :deep(.el-select) {
  width: 100%;
}

.strategy-grid {
  display: grid;
  grid-template-columns: 1fr 1.35fr;
  gap: 24px;
  margin-top: 18px;
}

.field-label {
  display: block;
  margin-bottom: 12px;
  color: #344054;
  font-size: 14px;
  font-weight: 600;
}

.run-action {
  gap: 16px;
  margin-top: 24px;
  color: #667085;
  font-size: 13px;
}

.result-actions {
  display: flex;
  flex: none;
  gap: 10px;
}

.confirmation-strip {
  justify-content: space-between;
  gap: 16px;
  margin: 18px 0 14px;
  padding: 12px 14px;
  border: 1px solid #a7f3d0;
  border-radius: 12px;
  color: #065f46;
  background: #ecfdf5;
}

.confirmation-strip > div {
  align-items: flex-start;
  flex-direction: column;
  gap: 3px;
}

.confirmation-strip span {
  color: #047857;
  font-size: 13px;
}

.result-warning {
  margin: 18px 0 14px;
}

.result-card {
  border-color: rgba(37, 99, 235, .16);
}

h4 {
  margin: 24px 0 10px;
}

@media (max-width: 900px) {
  .context-form,
  .strategy-grid {
    grid-template-columns: 1fr;
  }

  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
