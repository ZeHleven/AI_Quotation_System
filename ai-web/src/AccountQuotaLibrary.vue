<template>
  <div class="account-quota-page">
    <div class="content-heading">
      <div>
        <p class="eyebrow">账户成本资产</p>
        <h2>账户定额库</h2>
      </div>
      <div v-if="!moduleUnavailable" class="heading-actions">
        <el-button :icon="Plus" type="primary" @click="openCreate">新增{{ activeTabConfig.shortLabel }}</el-button>
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
          <span>人工改价可从项目计价草稿同步进来，并且一律先保存为账户草稿；草稿经人工启用后才会参与账户定额模式匹配。企业定额 active 主库始终保持独立。</span>
        </div>
        <el-tag type="primary" effect="plain">账户隔离</el-tag>
      </section>

      <section class="quota-panel">
        <el-tabs v-model="activeDetailType" class="quota-tabs" @tab-change="switchDetailType">
          <el-tab-pane v-for="tab in detailTabs" :key="tab.value" :label="tab.label" :name="tab.value" />
        </el-tabs>

        <div class="quota-filters">
          <el-input
            v-model="filters.keyword"
            :prefix-icon="Search"
            clearable
            :placeholder="activeTabConfig.searchPlaceholder"
            @keyup.enter="searchItems"
            @clear="searchItems"
          />
          <el-select v-if="activeDetailType === 'material'" v-model="filters.materialType" clearable placeholder="材料类型" @change="searchItems">
            <el-option label="主材" value="主材" />
            <el-option label="辅材" value="辅材" />
          </el-select>
          <el-select v-model="filters.status" clearable placeholder="全部状态" @change="searchItems">
            <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-button type="primary" plain @click="searchItems">搜索</el-button>
          <el-button plain @click="resetFilters">重置</el-button>
        </div>

        <div class="quota-toolbar-line">
          <el-button :icon="Plus" type="primary" @click="openCreate">新增{{ activeTabConfig.shortLabel }}</el-button>
          <el-button type="warning" plain :disabled="!selectedArchivableQuotaRows.length" @click="batchChangeStatus('active')">同步选中到基础定额</el-button>
          <span>共 {{ total }} 条</span>
        </div>

        <div class="quota-bulk-toolbar">
          <span>已选 {{ selectedQuotaRows.length }} 条</span>
          <el-button size="small" plain @click="selectQuotaRows('all')">选择本页可操作</el-button>
          <el-button size="small" plain @click="selectQuotaRows('draft')">只选草稿</el-button>
          <el-button size="small" plain @click="selectQuotaRows('none')">取消选择</el-button>
          <el-button size="small" type="success" plain :disabled="!selectedDraftQuotaRows.length" @click="batchChangeStatus('active')">批量启用草稿</el-button>
          <el-button size="small" type="danger" plain :disabled="!selectedArchivableQuotaRows.length" @click="batchChangeStatus('archived')">批量归档</el-button>
        </div>

        <el-table
          v-loading="loading"
          :data="visibleItems"
          :row-key="quotaIdentifier"
          class="users-table quota-detail-table"
          empty-text="当前账户暂无明细"
        >
          <el-table-column label="选择" width="58" align="center" fixed="left">
            <template #default="{ row }">
              <el-checkbox v-model="row._bulk_selected" :disabled="row.status === 'archived'" />
            </template>
          </el-table-column>

          <template v-if="activeDetailType === 'process'">
            <el-table-column label="工序名" min-width="210" fixed="left">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.item_name || '未命名工序' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="采购单价" width="150" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span class="money-text">{{ formatPriceFriendly(row.unit_price) }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="工序单位" width="120">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.unit || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="调价系数" width="130" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span>{{ detailValue(row, 'adjustment_factor', '1') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="真实工序含量" width="150" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span>{{ detailValue(row, 'real_content', '1') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
          </template>

          <template v-else-if="activeDetailType === 'material'">
            <el-table-column label="材料名" min-width="150" fixed="left">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.item_name || '未命名材料' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="材料编码" width="135">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.quota_code || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="材料类型" width="115">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ detailValue(row, 'material_type', '辅材') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="损耗率" width="105" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span>{{ detailValue(row, 'loss_rate', '0') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="材料单价" width="135" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span class="money-text">{{ formatPriceFriendly(row.unit_price) }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="单位" width="95">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.unit || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="调价系数" width="120" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span>{{ detailValue(row, 'adjustment_factor', '1') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="规格型号" min-width="150">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ detailValue(row, 'spec_model') || row.spec || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column v-for="column in resourceColumns" :key="column.key" :label="column.label" :width="column.width">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ detailValue(row, column.key) }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
          </template>

          <template v-else>
            <el-table-column label="分包名" min-width="150" fixed="left">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.item_name || '未命名分包' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="分包编码" width="135">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.quota_code || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="损耗率" width="105" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span>{{ detailValue(row, 'loss_rate', '0') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="分包明细" width="175">
              <template #default="{ row }">
                <div class="editable-cell">
                  <div class="split-fees">
                    <span>人工费：{{ detailValue(row, 'labor_fee', '0') }}</span>
                    <span>主材费：{{ detailValue(row, 'main_material_fee', '0') }}</span>
                    <span>辅材费：{{ detailValue(row, 'auxiliary_material_fee', '0') }}</span>
                    <small v-if="detailValue(row, 'subcontract_breakdown_source', '')">{{ subcontractBreakdownSourceLabel(row) }}</small>
                  </div>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="分包单价" width="135" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span class="money-text">{{ formatPriceFriendly(row.unit_price) }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="单位" width="95">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ row.unit || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="调价系数" width="120" align="right">
              <template #default="{ row }">
                <div class="editable-cell align-right">
                  <span>{{ detailValue(row, 'adjustment_factor', '1') }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="规格型号" min-width="150">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ detailValue(row, 'spec_model') || row.spec || '—' }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
            <el-table-column v-for="column in resourceColumns" :key="column.key" :label="column.label" :width="column.width">
              <template #default="{ row }">
                <div class="editable-cell">
                  <span>{{ detailValue(row, column.key) }}</span>
                  <el-button :icon="Edit" link type="primary" :disabled="!canEditRow(row)" @click="openEdit(row)" />
                </div>
              </template>
            </el-table-column>
          </template>

          <el-table-column label="状态" width="95">
            <template #default="{ row }"><el-tag :type="statusTag(row.status)" effect="plain">{{ statusLabel(row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="操作" width="285" fixed="right">
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

    <el-dialog v-model="dialog.visible" :title="dialog.mode === 'edit' ? `编辑${dialogTabConfig.shortLabel}` : `新增${dialogTabConfig.shortLabel}`" width="760px" destroy-on-close>
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
          <el-form-item label="明细类型" required>
            <el-select v-model="dialog.form.detail_type" @change="syncDialogDefaults">
              <el-option v-for="tab in detailTabs" :key="tab.value" :label="tab.label" :value="tab.value" />
            </el-select>
          </el-form-item>
          <el-form-item :label="dialogTabConfig.codeLabel">
            <el-input v-model="dialog.form.quota_code" maxlength="64" :placeholder="dialogTabConfig.codePlaceholder" />
          </el-form-item>
        </div>
        <div class="quota-form-grid">
          <el-form-item :label="dialogTabConfig.nameLabel" required>
            <el-input v-model="dialog.form.item_name" maxlength="255" :placeholder="dialogTabConfig.namePlaceholder" />
          </el-form-item>
          <el-form-item :label="dialogTabConfig.unitLabel" required>
            <el-input v-model="dialog.form.unit" maxlength="24" placeholder="例如 ㎡、m、项" />
          </el-form-item>
        </div>
        <div class="quota-form-grid">
          <el-form-item :label="dialogTabConfig.priceLabel" required>
            <el-input v-model="dialog.form.unit_price" inputmode="decimal" maxlength="24" placeholder="最多 6 位小数">
              <template #append>元</template>
            </el-input>
            <small class="quota-form-tip">保存后精确值：{{ formPricePreview }}</small>
          </el-form-item>
          <el-form-item label="调价系数">
            <el-input v-model="dialog.form.adjustment_factor" inputmode="decimal" maxlength="24" placeholder="默认 1" />
          </el-form-item>
        </div>

        <template v-if="dialog.form.detail_type === 'process'">
          <el-form-item label="真实工序含量">
            <el-input v-model="dialog.form.real_content" inputmode="decimal" maxlength="24" placeholder="默认 1" />
          </el-form-item>
        </template>

        <template v-else>
          <div class="quota-form-grid">
            <el-form-item v-if="dialog.form.detail_type === 'material'" label="材料类型">
              <el-select v-model="dialog.form.material_type" clearable placeholder="选择材料类型">
                <el-option label="主材" value="主材" />
                <el-option label="辅材" value="辅材" />
              </el-select>
            </el-form-item>
            <el-form-item label="损耗率">
              <el-input v-model="dialog.form.loss_rate" inputmode="decimal" maxlength="24" placeholder="默认 0" />
            </el-form-item>
          </div>
          <div v-if="dialog.form.detail_type === 'subcontract'" class="quota-form-grid">
            <el-form-item label="人工费">
              <el-input v-model="dialog.form.labor_fee" inputmode="decimal" maxlength="24" placeholder="默认 0" />
            </el-form-item>
            <el-form-item label="主材费">
              <el-input v-model="dialog.form.main_material_fee" inputmode="decimal" maxlength="24" placeholder="默认 0" />
            </el-form-item>
            <el-form-item label="辅材费">
              <el-input v-model="dialog.form.auxiliary_material_fee" inputmode="decimal" maxlength="24" placeholder="默认 0" />
            </el-form-item>
          </div>
          <el-form-item label="规格型号">
            <el-input v-model="dialog.form.spec_model" maxlength="255" placeholder="例如 600*600、15厚、单开" />
          </el-form-item>
          <div class="quota-extra-grid">
            <el-form-item v-for="column in resourceColumns" :key="column.key" :label="column.label">
              <el-input v-model="dialog.form[column.key]" maxlength="255" />
            </el-form-item>
          </div>
        </template>

        <el-form-item label="项目特征">
          <el-input v-model="dialog.form.item_features" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="填写工艺、材质、规格、施工条件等匹配特征" />
        </el-form-item>
        <el-form-item label="规格（原始备注，可选）">
          <el-input v-model="dialog.form.spec" type="textarea" :rows="2" maxlength="10000" show-word-limit placeholder="保留原始型号、尺寸或其他说明" />
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
import { Edit, Plus, Refresh, Search } from '@element-plus/icons-vue'
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

const detailTabs = [
  { value: 'process', label: '工序明细', shortLabel: '工序', nameLabel: '工序名', codeLabel: '工序编码（可选）', unitLabel: '工序单位', priceLabel: '采购单价（元）', searchPlaceholder: '工序名', namePlaceholder: '输入工序名称', codePlaceholder: '例如 GX-001' },
  { value: 'material', label: '材料明细', shortLabel: '材料', nameLabel: '材料名', codeLabel: '材料编码', unitLabel: '单位', priceLabel: '材料单价（元）', searchPlaceholder: '材料名、材料编码或规格型号', namePlaceholder: '输入材料名称', codePlaceholder: '例如 CL-001' },
  { value: 'subcontract', label: '专业分包明细', shortLabel: '分包', nameLabel: '分包名', codeLabel: '分包编码', unitLabel: '单位', priceLabel: '分包单价（元）', searchPlaceholder: '分包名、分包编码或规格型号', namePlaceholder: '输入分包名称', codePlaceholder: '例如 FB-001' },
]
const resourceColumns = [
  { key: 'code_name', label: '代号', width: 90 },
  { key: 'layer_count', label: '层数', width: 90 },
  { key: 'color', label: '颜色', width: 100 },
  { key: 'thickness_mm', label: '厚度 mm', width: 105 },
  { key: 'width_mm', label: '宽度 mm', width: 105 },
  { key: 'height', label: '高度', width: 90 },
  { key: 'volume', label: '体积', width: 90 },
  { key: 'area', label: '面积', width: 90 },
  { key: 'unfold', label: '展开', width: 90 },
  { key: 'material', label: '材质', width: 115 },
  { key: 'position', label: '位置', width: 115 },
  { key: 'brand', label: '品牌', width: 115 },
]
const extraFieldKeys = [
  'material_type',
  'loss_rate',
  'adjustment_factor',
  'real_content',
  'labor_fee',
  'main_material_fee',
  'auxiliary_material_fee',
  'spec_model',
  ...resourceColumns.map((column) => column.key),
]
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
const activeDetailType = ref('process')
const filters = reactive({ keyword: '', status: '', materialType: '' })
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
const activeTabConfig = computed(() => detailTabs.find((tab) => tab.value === activeDetailType.value) || detailTabs[0])
const dialogTabConfig = computed(() => detailTabs.find((tab) => tab.value === dialog.form.detail_type) || activeTabConfig.value)
const formPricePreview = computed(() => normalizePrice(dialog.form.unit_price) || '—')
const dialogSaveLabel = computed(() => dialog.mode === 'edit' ? '保存修改' : '保存为草稿')
const selectedQuotaRows = computed(() => items.value.filter((row) => row._bulk_selected))
const selectedDraftQuotaRows = computed(() => selectedQuotaRows.value.filter((row) => row.status === 'draft'))
const selectedArchivableQuotaRows = computed(() => selectedQuotaRows.value.filter((row) => row.status !== 'archived'))
const visibleItems = computed(() => {
  if (activeDetailType.value !== 'material' || !filters.materialType) return items.value
  return items.value.filter((row) => detailValue(row, 'material_type', '辅材') === filters.materialType)
})

function emptyForm() {
  return {
    detail_type: activeDetailType.value || 'process',
    quota_code: '',
    item_name: '',
    item_features: '',
    spec: '',
    unit: '',
    unit_price: '',
    reason: '',
    expected_revision: null,
    material_type: '',
    loss_rate: '0',
    adjustment_factor: '1',
    real_content: '1',
    labor_fee: '0',
    main_material_fee: '0',
    auxiliary_material_fee: '0',
    spec_model: '',
    code_name: '',
    layer_count: '',
    color: '',
    thickness_mm: '',
    width_mm: '',
    height: '',
    volume: '',
    area: '',
    unfold: '',
    material: '',
    position: '',
    brand: '',
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

function normalizeFreeNumber(value, fallback = '') {
  const text = String(value ?? '').trim()
  if (!text) return fallback
  return text
}

function parseNotes(value) {
  if (!value) return {}
  if (typeof value === 'object') return value
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function detailExtra(row) {
  if (row?.detail_extra && typeof row.detail_extra === 'object') return row.detail_extra
  const parsed = parseNotes(row?.notes)
  return parsed && typeof parsed === 'object' ? parsed : {}
}

function rowDetailType(row) {
  const parsedType = row?.detail_type || detailExtra(row).detail_type
  return ['process', 'material', 'subcontract'].includes(parsedType) ? parsedType : 'process'
}

function detailValue(row, key, fallback = '—') {
  const value = detailExtra(row)[key]
  if (value === 0) return '0'
  return value == null || String(value).trim() === '' ? fallback : value
}

function canEditRow(row) {
  return row?.status !== 'archived'
}

function subcontractBreakdownSourceLabel(row) {
  const source = detailValue(row, 'subcontract_breakdown_source', '')
  const labels = {
    pricing_breakdown: '来源：费用拆分',
    pricing_breakdown_calibrated: '来源：费用拆分已校准',
    rule_estimate_pending_llm: '来源：规则估算，待LLM/人工复核',
    unavailable: '来源：待补充',
  }
  return labels[source] || `来源：${source}`
}

function buildNotesPayload(form) {
  const detail = {
    schema: 'account_quota_detail_v1',
    detail_type: form.detail_type || 'process',
  }
  extraFieldKeys.forEach((key) => {
    const value = normalizeFreeNumber(form[key])
    if (value !== '') detail[key] = value
  })
  return JSON.stringify(detail)
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
    const params = { page: page.value, page_size: pageSize, detail_type: activeDetailType.value }
    if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
    if (filters.status) params.status = filters.status
    const response = await accountQuotaApi.list(params)
    items.value = accountQuotaResponseItems(response).map((row) => ({ ...row, detail_type: rowDetailType(row), _bulk_selected: false }))
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
  filters.materialType = ''
  searchItems()
}

function switchDetailType() {
  filters.keyword = ''
  filters.materialType = ''
  page.value = 1
  loadItems()
}

function syncDialogDefaults() {
  if (dialog.form.detail_type === 'process') {
    dialog.form.loss_rate = '0'
    dialog.form.material_type = ''
  } else if (dialog.form.detail_type === 'material' && !dialog.form.material_type) {
    dialog.form.material_type = '辅材'
  }
}

function openCreate() {
  dialog.mode = 'create'
  dialog.identifier = null
  dialog.conflict = false
  dialog.form = { ...emptyForm(), detail_type: activeDetailType.value, reason: `新增${activeTabConfig.value.shortLabel}` }
  syncDialogDefaults()
  dialog.visible = true
}

function fillEditForm(item) {
  const extra = detailExtra(item)
  const form = {
    ...emptyForm(),
    detail_type: rowDetailType(item),
    quota_code: item.quota_code || '',
    item_name: item.item_name || '',
    item_features: item.item_features || '',
    spec: item.spec || '',
    unit: item.unit || '',
    unit_price: formatPriceExact(item.unit_price) === '—' ? '' : formatPriceExact(item.unit_price),
    reason: '',
    expected_revision: rowRevision(item),
  }
  extraFieldKeys.forEach((key) => {
    if (extra[key] != null) form[key] = String(extra[key])
  })
  dialog.mode = 'edit'
  dialog.identifier = quotaIdentifier(item)
  dialog.conflict = false
  dialog.form = form
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
  if (!form.detail_type || !form.item_name.trim() || !form.unit.trim()) {
    ElMessage.warning('请填写明细类型、名称和单位')
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
    notes: buildNotesPayload(dialog.form),
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
      ElMessage.success(`${dialogTabConfig.value.shortLabel}草稿已创建`)
    }
    dialog.visible = false
    activeDetailType.value = dialog.form.detail_type
    await loadItems()
  } catch (error) {
    if (!(await handleEditConflict(error))) {
      ElMessage.error(accountQuotaApiErrorMessage(error, '账户定额保存失败'))
    }
  } finally {
    dialog.loading = false
  }
}

function selectQuotaRows(mode) {
  items.value.forEach((row) => {
    if (mode === 'none' || row.status === 'archived') {
      row._bulk_selected = false
      return
    }
    if (mode === 'draft') {
      row._bulk_selected = row.status === 'draft'
      return
    }
    row._bulk_selected = row.status !== 'archived'
  })
}

async function batchChangeStatus(targetStatus) {
  const rows = targetStatus === 'active' ? selectedDraftQuotaRows.value : selectedArchivableQuotaRows.value
  if (!rows.length) {
    ElMessage.warning(targetStatus === 'active' ? '请先勾选草稿定额' : '请先勾选可归档定额')
    return
  }
  const action = targetStatus === 'active' ? '批量启用' : '批量归档'
  const hint = targetStatus === 'active'
    ? `将启用已选中的 ${rows.length} 条草稿定额。启用后仅进入账户定额 active 状态，不会修改企业定额主库。请填写启用依据。`
    : `将归档已选中的 ${rows.length} 条账户定额。归档后记录冻结且不再参与匹配。请填写归档原因。`
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
    await accountQuotaApi.batchStatus({
      target_status: targetStatus,
      reason,
      items: rows.map((row) => ({
        item_identifier: String(quotaIdentifier(row)),
        expected_revision: rowRevision(row),
      })),
    })
    ElMessage.success(`已${action.replace('批量', '')} ${rows.length} 条账户定额`)
    await loadItems()
  } catch (error) {
    if (error?.response?.status === 409) {
      await loadItems()
      ElMessage.warning('部分定额已产生新修订或状态变化，列表已刷新；请核对后重新批量操作')
      return
    }
    ElMessage.error(accountQuotaApiErrorMessage(error, `${action}失败`))
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
.quota-boundary-card>div,.history-heading>div,.split-fees{display:flex;flex-direction:column;gap:5px}.quota-boundary-card span,.quota-title small,.quota-title>span,.history-heading span,.quota-form-tip{color:#64748b}.quota-panel{padding:18px 20px;border:1px solid rgba(148,163,184,.22);border-radius:20px;background:rgba(255,255,255,.92);box-shadow:0 14px 34px rgba(15,23,42,.06)}.quota-tabs{margin-bottom:14px}.quota-tabs :deep(.el-tabs__header){margin:0}.quota-tabs :deep(.el-tabs__item){font-size:15px}.quota-tabs :deep(.el-tabs__item.is-active){font-weight:700}.quota-filters{display:grid;grid-template-columns:minmax(240px,360px) 160px 150px auto auto;gap:12px;margin-bottom:12px}.quota-toolbar-line{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}.quota-toolbar-line span{margin-left:auto;color:#64748b}.quota-bulk-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:16px;padding:10px 12px;border:1px solid rgba(148,163,184,.18);border-radius:14px;background:rgba(248,250,252,.92)}.quota-bulk-toolbar span{font-size:13px;color:#475569}.quota-bulk-toolbar :deep(.el-button+.el-button),.quota-actions :deep(.el-button+.el-button){margin-left:0}.quota-detail-table :deep(.el-table__cell){vertical-align:middle}.editable-cell{display:flex;align-items:center;gap:4px;min-width:0}.editable-cell>span,.editable-cell>.split-fees{min-width:0;overflow:hidden;text-overflow:ellipsis}.editable-cell.align-right{justify-content:flex-end}.money-text{font-variant-numeric:tabular-nums;color:#0f172a}.split-fees{font-size:12px;line-height:1.45;color:#475569}.quota-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.el-pagination{justify-content:flex-end;margin-top:16px}.quota-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.quota-extra-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.quota-form-tip{display:block;margin-top:6px;font-size:12px;font-variant-numeric:tabular-nums}.quota-dialog-alert{margin-bottom:16px}.history-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;padding:16px 18px;border:1px solid rgba(148,163,184,.2);border-radius:16px;background:#f8fafc}
@media(max-width:1100px){.quota-filters{grid-template-columns:1fr 150px}.quota-extra-grid{grid-template-columns:1fr 1fr}.quota-toolbar-line span{margin-left:0}}
@media(max-width:720px){.quota-form-grid,.quota-extra-grid,.quota-filters{grid-template-columns:1fr}.quota-boundary-card{align-items:flex-start;flex-direction:column}.quota-panel{padding:14px}.quota-bulk-toolbar{align-items:flex-start;flex-direction:column}}
</style>
