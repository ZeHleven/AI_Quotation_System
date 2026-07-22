<template>
  <section class="budget-panel pricing-panel">
    <div class="budget-title">
      <div>
        <strong>项目成本计价</strong>
        <small>先在账号隔离的双模式草稿中调整价格；P2-1 不可变计价版本继续作为历史结果保留</small>
      </div>
      <div v-if="pricingAvailable && canViewPricing" class="pricing-actions">
        <el-button plain :loading="loading" @click="refreshPricing">刷新计价</el-button>
        <el-button
          v-if="readinessCapability('can_create_pricing_run')"
          type="primary"
          :loading="creating"
          :disabled="!canCreatePricingRun"
          @click="createPricingRun"
        >
          生成不可变计价版本（P2-1）
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="!pricingAvailable"
      type="info"
      show-icon
      :closable="false"
      title="成本计价功能尚未开启"
      description="清单导入与确认仍可使用；当前不会创建计价任务。"
    />
    <el-alert
      v-else-if="!canViewPricing"
      type="warning"
      show-icon
      :closable="false"
      title="当前账号无项目成本计价权限"
      description="请让管理员授予 cost_viewer、cost_editor 或 cost_approver 等成本角色；清单导入与确认仍可继续使用。"
    />
    <template v-else>
      <el-alert
        v-if="projectArchived"
        class="pricing-alert"
        type="warning"
        show-icon
        :closable="false"
        title="项目已归档，仅可查看历史计价结果"
      />
      <el-alert
        v-else-if="readiness && !readinessEligible"
        class="pricing-alert"
        type="warning"
        show-icon
        :closable="false"
        :title="readinessMessage"
      />
      <el-alert
        v-else-if="readiness && !formalPointersMatch"
        class="pricing-alert"
        type="error"
        show-icon
        :closable="false"
        title="正式清单指针已变化，请刷新后再创建计价"
      />

      <div v-if="readiness" class="pricing-context">
        <div>
          <span>正式清单</span>
          <strong>批次 {{ project?.active_import_batch_id || '—' }} / 修订 {{ project?.active_import_revision_id || '—' }}</strong>
        </div>
        <div>
          <span>当前可用企业定额</span>
          <strong>{{ quotaVersionLabel(readinessQuotaVersion) }}</strong>
        </div>
        <div>
          <span>计价准备状态</span>
          <el-tag :type="readinessEligible && formalPointersMatch ? 'success' : 'warning'" effect="plain">
            {{ readinessEligible && formalPointersMatch ? '可以创建计价' : '暂不可创建' }}
          </el-tag>
        </div>
      </div>

      <div class="pricing-draft-workspace">
        <div class="budget-title">
          <div>
            <strong>双模式计价草稿</strong>
            <small>草稿只属于当前账号；基础定额和账户定额会分别保留，切换模式只切换查看范围，不覆盖另一种模式</small>
          </div>
          <div class="draft-actions">
            <el-button v-if="draft" plain :disabled="!canManageDraft" @click="openAccountQuotaSync">同步到账户定额</el-button>
            <el-button
              type="success"
              plain
              :loading="draftQuoteJobStarting"
              :disabled="!canStartDraftQuoteJob"
              @click="startDraftQuoteJob"
            >
              一键生成报价
            </el-button>
            <el-button
              v-if="draftQuoteJobRunning"
              type="danger"
              plain
              :loading="draftQuoteJobCancelling"
              :disabled="!canManageDraft"
              @click="cancelDraftQuoteJob"
            >
              取消生成
            </el-button>
            <el-button type="primary" :loading="draftSaving" :disabled="!canManageDraft" @click="saveDraft">
              {{ draftActionLabel }}
            </el-button>
          </div>
        </div>

        <el-radio-group v-model="selectedDraftMode" class="draft-mode-selector" :disabled="!canManageDraft">
          <el-radio-button value="enterprise_ai">基础定额</el-radio-button>
          <el-radio-button value="account_strict">账户定额</el-radio-button>
        </el-radio-group>
        <div class="draft-mode-help">
          <span>两种模式的草稿与报价结果会分别保留；你可以先看基础定额报价，再切到账户定额报价做对比。</span>
          <template v-if="selectedDraftMode === 'enterprise_ai'">
            <strong>企业定额匹配 → 未匹配项自动 AI 估价</strong>
            <span>基础定额模式会先匹配企业 active 定额；未匹配行可通过“一键生成报价”后台任务自动进入 AI 估价队列。</span>
          </template>
          <template v-else>
            <strong>只匹配当前账号已启用的账户定额</strong>
            <span>账户定额模式只读取当前账号 active 条目；按定额编码或项目名称与单位严格匹配，项目特征仅用于同名消歧。未匹配行保持空价，不读取企业定额，也不会自动调用 AI。</span>
          </template>
        </div>

        <div v-if="draftQuoteJob" class="draft-quote-job-card">
          <div class="draft-quote-job-head">
            <div>
              <strong>一键生成报价进度</strong>
              <small>{{ draftQuoteJob.current_message || draftQuoteJobStatusLabel(draftQuoteJob.status) }}</small>
            </div>
            <el-tag :type="draftQuoteJobStatusTag(draftQuoteJob.status)" effect="plain">{{ draftQuoteJobStatusLabel(draftQuoteJob.status) }}</el-tag>
          </div>
          <el-progress :percentage="draftQuoteJobPercent" :status="draftQuoteJob.status === 'succeeded' ? 'success' : (draftQuoteJob.status === 'failed' ? 'exception' : undefined)" />
          <div class="draft-quote-job-stats">
            <span>总行数 {{ draftQuoteJob.total_line_count || 0 }}</span>
            <span>企业定额 {{ draftQuoteJob.enterprise_priced_count || 0 }}</span>
            <span>AI完成 {{ draftQuoteJob.ai_completed_count || 0 }}/{{ draftQuoteJob.ai_total_count || 0 }}</span>
            <span v-if="draftQuoteJob.ai_failed_count">AI失败 {{ draftQuoteJob.ai_failed_count }}</span>
            <span v-if="draftQuoteJob.skipped_count">跳过 {{ draftQuoteJob.skipped_count }}</span>
          </div>
        </div>

        <el-alert v-if="projectArchived" class="pricing-alert" type="warning" show-icon :closable="false" title="项目已归档，计价草稿仅可查看" />
        <el-skeleton v-if="draftLoading" :rows="5" animated />
        <el-empty v-else-if="!draft" description="尚未创建计价草稿，请选择模式后创建" />
        <template v-else>
          <div class="draft-meta">
            <div><span>当前模式</span><strong>{{ draftModeLabel(draftModeOf(draft)) }}</strong></div>
            <div><span>草稿修订</span><strong>Revision {{ draftRevision }}</strong></div>
            <div><span>正式清单快照</span><strong>批次 {{ draft.source_import_batch_id || project?.active_import_batch_id || '—' }} / 修订 {{ draft.source_import_revision_id || project?.active_import_revision_id || '—' }}</strong></div>
            <div><span>最近更新</span><strong>{{ formatDate(draft.updated_at || draft.created_at) }}</strong></div>
          </div>
          <div class="pricing-metrics draft-metrics">
            <div><span>草稿行数</span><strong>{{ draftSummaryCount('line_count', 'total_count', 'standard_item_count') }}</strong></div>
            <div><span>已匹配</span><strong>{{ draftSummaryCount('matched_count') }}</strong></div>
            <div><span>待补价</span><strong>{{ draftSummaryCount('unmatched_count', 'pending_count', 'unpriced_count') }}</strong></div>
            <div><span>人工改价</span><strong>{{ draftSummaryCount('manual_price_count', 'manual_priced_count', 'manual_count') }}</strong></div>
            <div><span>已计价小计</span><strong>{{ formatMoney(draft.priced_subtotal ?? draftSummary.priced_subtotal) }}</strong></div>
          </div>
          <div class="quote-stat-strip">
            <span class="quote-stat-title">统计信息</span>
            <div v-for="item in quoteStatCards" :key="item.key" class="quote-stat-card">
              <span>{{ item.label }}:</span>
              <strong>{{ formatMoney(item.value ?? 0) }}</strong>
            </div>
            <el-button text @click="totalsExpanded = !totalsExpanded">{{ totalsExpanded ? '收起' : '展开' }}</el-button>
          </div>
          <div v-if="totalsExpanded" class="quote-totals-panel">
            <el-table :data="quoteTotalsRows" class="users-table" size="small" empty-text="暂无统计明细">
              <el-table-column prop="order" label="序号" width="70" />
              <el-table-column prop="name" label="中文名" width="150" />
              <el-table-column label="合计" width="145" align="right"><template #default="{ row }">{{ row.is_rate ? formatRate(row.amount || 0) : formatMoney(row.amount) }}</template></el-table-column>
              <el-table-column label="默认标段" width="145" align="right"><template #default="{ row }">{{ row.is_rate ? formatRate(row.default_amount || 0) : formatMoney(row.default_amount) }}</template></el-table-column>
              <el-table-column prop="formula" label="公式" min-width="260" />
              <el-table-column prop="remark" label="备注" min-width="220" />
              <el-table-column prop="edit_type" label="编辑类型" width="120" />
            </el-table>
            <div class="quote-rate-editor">
              <label><span>措施费率(%)</span><el-input-number v-model="totalsConfigInputs.measures_rate" :min="0" :controls="false" size="small" /></label>
              <label><span>管理费率(%)</span><el-input-number v-model="totalsConfigInputs.management_rate" :min="0" :controls="false" size="small" /></label>
              <label><span>其它费用</span><el-input-number v-model="totalsConfigInputs.other_fee" :min="0" :controls="false" size="small" /></label>
              <label><span>暂列金额</span><el-input-number v-model="totalsConfigInputs.suspended_amount" :min="0" :controls="false" size="small" /></label>
              <label><span>面积</span><el-input-number v-model="totalsConfigInputs.area" :min="0" :controls="false" size="small" /></label>
              <label><span>报价上下浮(%)</span><el-input-number v-model="totalsConfigInputs.quote_adjustment_percent" :min="-100" :controls="false" size="small" /></label>
              <el-button type="primary" plain :loading="totalsSaving" :disabled="!canManageDraft" @click="saveDraftTotalsConfig">保存费率</el-button>
            </div>
          </div>
          <div class="draft-boundary-note">
            当前是可变草稿，不是正式计价结果；人工改价可同步为账户定额草稿，但不会立即重算当前草稿；{{ draftModeOf(draft) === 'enterprise_ai' ? '一键生成报价会自动处理企业定额未匹配行。' : '账户定额仅匹配当前账号 active 条目；未匹配行保持空价，可由用户手动触发 AI 估价。' }}
          </div>
          <el-alert
            v-if="draftQuoteJobRunning"
            class="pricing-alert"
            type="info"
            show-icon
            :closable="false"
            title="后台正在生成报价"
            description="系统会先使用企业定额 active 命中价格，未命中行自动进入 AI 估价队列；全部行完成后再刷新展示报价单。"
          />
          <template v-else>
          <div class="pricing-filters draft-filters">
            <el-input v-model="draftFilters.keyword" clearable placeholder="搜索项目名称、特征或来源行" @keyup.enter="searchDraftLines" @clear="searchDraftLines" />
            <el-select v-model="draftFilters.match_status" clearable placeholder="全部匹配状态" @change="searchDraftLines">
              <el-option v-for="item in matchStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="draftFilters.pricing_status" clearable placeholder="全部计价状态" @change="searchDraftLines">
              <el-option v-for="item in pricingStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-button type="primary" plain :loading="draftLinesLoading" @click="searchDraftLines">查询</el-button>
          </div>
          <el-table v-loading="draftLinesLoading" :data="draftLines" :row-key="lineIdOf" class="users-table quote-line-table" max-height="620" empty-text="当前筛选条件下暂无草稿行">
            <el-table-column type="selection" width="46" fixed="left" />
            <el-table-column label="项目名称" width="190" fixed="left"><template #default="{ row }">{{ row.item_name || row.project_name || '—' }}</template></el-table-column>
            <el-table-column label="特征描述" min-width="260" show-overflow-tooltip><template #default="{ row }">{{ row.spec || row.project_feature || '无项目特征' }}</template></el-table-column>
            <el-table-column label="区域" width="150"><template #default="{ row }"><div class="pricing-source"><span>区域：{{ rowRegion(row) }}</span><small>部位：{{ rowWorkArea(row) }}</small></div></template></el-table-column>
            <el-table-column label="主材采购方式" width="130">
              <template #default="{ row }">
                <div class="breakdown-edit-cell">
                  <el-select v-if="isDraftBreakdownEditing(row, 'material_supply_mode')" :model-value="draftBreakdownInputValue(row, 'material_supply_mode')" size="small" clearable :disabled="!canManageDraft || draftLineSaving[lineIdOf(row)]" @update:model-value="setDraftBreakdownInput(row, 'material_supply_mode', $event)">
                    <el-option label="乙供" value="乙供" />
                    <el-option label="甲供" value="甲供" />
                    <el-option label="无主材" value="无主材" />
                  </el-select>
                  <template v-else>
                    <span>{{ materialSupplyMode(row) }}</span>
                    <el-button v-if="canManageDraft" :icon="Edit" circle text size="small" title="编辑主材采购方式" @click="beginDraftBreakdownEdit(row, 'material_supply_mode')" />
                  </template>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="工程量" width="135" align="right"><template #default="{ row }"><strong class="quote-quantity">{{ formatQuantity(row.quantity ?? row.calculation_quantity) }}{{ row.unit ? ` (${row.unit})` : '' }}</strong></template></el-table-column>
            <el-table-column label="不含税综合单价" width="135" align="right"><template #default="{ row }"><strong>{{ formatMoney(draftPreviewUnitPrice(row) ?? quoteUnitPrice(row)) }}</strong></template></el-table-column>
            <el-table-column label="不含税综合合价" width="145" align="right"><template #default="{ row }">{{ formatMoney(draftPreviewLineTotal(row) ?? lineTotalCost(row)) }}</template></el-table-column>
            <el-table-column v-for="column in draftBreakdownColumns" :key="column.key" :label="column.label" :width="column.width" align="right">
              <template #default="{ row }">
                <div class="breakdown-edit-cell breakdown-edit-cell-right">
                  <span v-if="column.readonly" title="税率固定 9%，由不含税综合单价自动计算">{{ formatMoney(draftPreviewTaxAmount(row) ?? taxAmount(row) ?? 0) }}</span>
                  <el-input
                    v-else-if="isDraftBreakdownEditing(row, column.key)"
                    :model-value="draftBreakdownInputValue(row, column.key)"
                    size="small"
                    inputmode="decimal"
                    clearable
                    class="breakdown-input"
                    :disabled="!canManageDraft || draftLineSaving[lineIdOf(row)]"
                    @update:model-value="setDraftBreakdownInput(row, column.key, $event)"
                    @keyup.enter="saveDraftLinePrice(row)"
                  />
                  <template v-else>
                    <span>{{ formatBreakdownDisplay(row, column) }}</span>
                    <el-button v-if="canManageDraft" :icon="Edit" circle text size="small" :title="`编辑${column.label}`" @click="beginDraftBreakdownEdit(row, column.key)" />
                  </template>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="价格来源" width="170"><template #default="{ row }"><div class="draft-status-stack"><el-tag size="small" effect="plain">{{ draftPriceSourceLabel(row) }}</el-tag><el-tag :type="pricingStatusTag(row.pricing_status)" size="small" effect="plain">{{ pricingStatusLabel(row.pricing_status) }}</el-tag></div></template></el-table-column>
            <el-table-column label="备注" min-width="180">
              <template #default="{ row }">
                <div class="breakdown-edit-cell">
                  <el-input v-if="isDraftBreakdownEditing(row, 'remark')" :model-value="draftBreakdownInputValue(row, 'remark')" size="small" clearable :disabled="!canManageDraft || draftLineSaving[lineIdOf(row)]" @update:model-value="setDraftBreakdownInput(row, 'remark', $event)" @keyup.enter="saveDraftLinePrice(row)" />
                  <template v-else>
                    <span>{{ rowRemark(row) }}</span>
                    <el-button v-if="canManageDraft" :icon="Edit" circle text size="small" title="编辑备注" @click="beginDraftBreakdownEdit(row, 'remark')" />
                  </template>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="调整" width="330" fixed="right">
              <template #default="{ row }">
                <div class="draft-price-editor">
                  <el-input v-model="draftPriceInputs[lineIdOf(row)]" inputmode="decimal" clearable placeholder="直接单价" :disabled="!canManageDraft || draftLineSaving[lineIdOf(row)]" @keyup.enter="saveDraftLinePrice(row)" />
                  <el-button size="small" type="primary" plain :loading="draftLineSaving[lineIdOf(row)]" :disabled="!canManageDraft" @click="saveDraftLinePrice(row)">保存</el-button>
                  <el-button size="small" type="success" plain :loading="draftLineAiEstimating[lineIdOf(row)]" :disabled="!canManageDraft || !canAiEstimateDraftLine(row)" @click="estimateDraftLine(row)">AI估价</el-button>
                  <el-button size="small" :loading="draftLineSaving[lineIdOf(row)]" :disabled="!canManageDraft || !hasManualPrice(row)" @click="clearDraftLinePrice(row)">清空</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination v-if="draftLineTotal > draftLinePageSize" v-model:current-page="draftLinePage" :page-size="draftLinePageSize" :total="draftLineTotal" layout="total, prev, pager, next" @current-change="loadDraftLines" />
          </template>
        </template>
      </div>

      <el-divider content-position="left">P2-1 不可变计价历史</el-divider>
      <el-alert class="pricing-alert" type="info" :closable="false" title="以下是已生成的不可变计价版本；上方草稿的创建、切换和改价不会改写这些历史结果。" />

      <el-skeleton v-if="loading && !selectedRun" :rows="5" animated />
      <el-empty v-else-if="!runs.length" description="尚未生成项目计价任务" />
      <template v-else>
        <div class="pricing-run-toolbar">
          <el-select v-model="selectedRunId" placeholder="选择计价版本" @change="selectRun">
            <el-option
              v-for="run in runs"
              :key="runIdOf(run)"
              :label="runOptionLabel(run)"
              :value="runIdOf(run)"
            />
          </el-select>
          <div v-if="selectedRun" class="pricing-run-meta">
            <el-tag :type="coverageStatusTag(coverageStatus)" effect="plain">{{ coverageStatusLabel(coverageStatus) }}</el-tag>
            <span>绑定定额：{{ quotaVersionLabel(runQuotaVersion(selectedRun)) }}</span>
            <span>生成时间：{{ formatDate(selectedRun.created_at) }}</span>
          </div>
        </div>

        <div v-if="selectedRun" class="pricing-metrics">
          <div>
            <span>计价覆盖率</span>
            <strong>{{ formatPercent(coveragePercent) }}</strong>
            <el-progress :percentage="coveragePercent" :status="coverageStatus === 'complete' ? 'success' : undefined" />
          </div>
          <div><span>已计价小计</span><strong>{{ formatMoney(selectedRun.priced_subtotal) }}</strong></div>
          <div><span>完整总成本</span><strong>{{ formatMoney(selectedRun.total_cost) }}</strong></div>
          <div><span>已匹配</span><strong>{{ summaryCount('matched_count') }}</strong></div>
          <div><span>多候选</span><strong>{{ summaryCount('ambiguous_count') }}</strong></div>
          <div><span>未匹配</span><strong>{{ summaryCount('unmatched_count') }}</strong></div>
          <div><span>单位冲突</span><strong>{{ summaryCount('unit_conflict_count') }}</strong></div>
          <div><span>工程量待解决</span><strong>{{ summaryCount('quantity_unresolved_count') }}</strong></div>
          <div><span>定额单价缺失</span><strong>{{ summaryCount('missing_price_count') }}</strong></div>
          <div><span>数值超限</span><strong>{{ summaryCount('numeric_overflow_count') }}</strong></div>
        </div>

        <el-alert
          v-if="selectedRun && coverageStatus === 'partial'"
          class="pricing-alert"
          type="warning"
          show-icon
          :closable="false"
          title="当前为部分计价，成本尚不完整"
          description="未匹配项不会按 0 元伪装为完整成本。已匹配部分可查看成本；多候选、未匹配、单位冲突、工程量待解决、定额单价缺失和数值超限行未计入完整项目成本。"
        />

        <div v-if="selectedRun" class="pricing-filters">
          <el-input
            v-model="lineFilters.keyword"
            clearable
            placeholder="搜索项目名称、特征或定额编码"
            @keyup.enter="searchLines"
            @clear="searchLines"
          />
          <el-select v-model="lineFilters.match_status" clearable placeholder="全部匹配状态" @change="searchLines">
            <el-option v-for="item in matchStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-model="lineFilters.pricing_status" clearable placeholder="全部计价状态" @change="searchLines">
            <el-option v-for="item in pricingStatusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-button type="primary" plain :loading="linesLoading" @click="searchLines">查询</el-button>
        </div>

        <el-table
          v-if="selectedRun"
          v-loading="linesLoading"
          :data="lines"
          :row-key="lineIdOf"
          class="users-table quote-line-table"
          max-height="620"
          empty-text="当前筛选条件下暂无计价行"
        >
          <el-table-column type="selection" width="46" fixed="left" />
          <el-table-column label="项目名称" width="190" fixed="left"><template #default="{ row }">{{ row.item_name || row.project_name || '—' }}</template></el-table-column>
          <el-table-column label="特征描述" min-width="260" show-overflow-tooltip><template #default="{ row }">{{ row.spec || row.project_feature || '无项目特征' }}</template></el-table-column>
          <el-table-column label="区域" width="150"><template #default="{ row }"><div class="pricing-source"><span>区域：{{ rowRegion(row) }}</span><small>部位：{{ rowWorkArea(row) }}</small></div></template></el-table-column>
          <el-table-column label="主材采购方式" width="120"><template #default="{ row }">{{ materialSupplyMode(row) }}</template></el-table-column>
          <el-table-column label="工程量" width="135" align="right"><template #default="{ row }"><strong class="quote-quantity">{{ formatQuantity(row.quantity ?? row.calculation_quantity) }}{{ row.unit ? ` (${row.unit})` : '' }}</strong></template></el-table-column>
          <el-table-column label="不含税综合单价" width="135" align="right"><template #default="{ row }"><strong>{{ formatMoney(quoteUnitPrice(row)) }}</strong></template></el-table-column>
          <el-table-column label="不含税综合合价" width="145" align="right"><template #default="{ row }">{{ formatMoney(lineTotalCost(row)) }}</template></el-table-column>
          <el-table-column label="人工费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'labor')) }}</template></el-table-column>
          <el-table-column label="主材费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'main_material')) }}</template></el-table-column>
          <el-table-column label="辅材费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'auxiliary_material')) }}</template></el-table-column>
          <el-table-column label="税金" width="95" align="right"><template #default="{ row }">{{ formatMoney(taxAmount(row)) }}</template></el-table-column>
          <el-table-column label="主材费不含损耗" width="140" align="right"><template #default="{ row }">{{ formatMoney(mainMaterialWithoutLoss(row)) }}</template></el-table-column>
          <el-table-column label="损耗率" width="95" align="right"><template #default="{ row }">{{ formatRate(lossRate(row)) }}</template></el-table-column>
          <el-table-column label="机械费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'machinery')) }}</template></el-table-column>
          <el-table-column label="综合费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'comprehensive')) }}</template></el-table-column>
          <el-table-column label="管理费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'management')) }}</template></el-table-column>
          <el-table-column label="利润费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'profit')) }}</template></el-table-column>
          <el-table-column label="措施费" width="105" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'measure')) }}</template></el-table-column>
          <el-table-column label="甲供材单价" width="120" align="right"><template #default="{ row }">{{ formatMoney(ownerMaterialUnitPrice(row)) }}</template></el-table-column>
          <el-table-column label="甲供材损耗金" width="130" align="right"><template #default="{ row }">{{ formatMoney(ownerMaterialLossAmount(row)) }}</template></el-table-column>
          <el-table-column label="价格来源" width="180"><template #default="{ row }"><div class="draft-status-stack"><el-tag :type="matchStatusTag(row.match_status)" size="small" effect="plain">{{ matchStatusLabel(row.match_status) }}</el-tag><el-tag :type="pricingStatusTag(row.pricing_status)" size="small" effect="plain">{{ pricingStatusLabel(row.pricing_status) }}</el-tag></div></template></el-table-column>
          <el-table-column label="备注" min-width="180" show-overflow-tooltip><template #default="{ row }">{{ rowRemark(row) }}</template></el-table-column>
          <el-table-column label="候选/证据" width="130" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="readinessCapability('can_view_pricing')"
                size="small"
                plain
                @click="openCandidates(row)"
              >
                查看 {{ candidateCount(row) ? '(' + candidateCount(row) + ')' : '' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-if="lineTotal > linePageSize"
          v-model:current-page="linePage"
          :page-size="linePageSize"
          :total="lineTotal"
          layout="total, prev, pager, next"
          @current-change="loadLines"
        />
      </template>
    </template>
  </section>

  <el-dialog v-model="quotaSync.visible" title="同步报价草稿到账户定额" width="min(1120px, 94vw)" destroy-on-close>
    <el-alert type="info" :closable="false" show-icon class="quota-sync-alert"
      title="仅同步人工改价行，目标一律先保存为账户定额草稿"
      description="同步不会改变当前计价草稿、企业定额主库或任何正式计价版本。命中已有账户定额时，请明确选择跳过或更新；更新已启用条目会撤回为草稿。" />
    <el-skeleton v-if="quotaSync.loading" :rows="8" animated />
    <template v-else>
      <el-empty v-if="!quotaSync.items.length" description="当前草稿没有可预览的有效价格行" />
      <template v-else>
        <div class="quota-sync-summary">
          <span>预览 {{ quotaSync.items.length }} 行</span>
          <span>可同步 {{ quotaSyncEligibleCount }} 条</span>
          <span>已选择 {{ quotaSyncSelectedCount }} 条</span>
          <span>将创建 {{ quotaSyncCreateCount }} 条</span>
          <span>将更新 {{ quotaSyncUpdateCount }} 条</span>
          <span>跳过/阻断 {{ quotaSyncSkipCount }} 条</span>
        </div>
        <div class="quota-sync-bulk-actions">
          <el-button size="small" plain :disabled="!quotaSyncEligibleCount" @click="quotaSyncSelectRows('all')">全选可同步</el-button>
          <el-button size="small" plain :disabled="!quotaSyncSelectedCount" @click="quotaSyncSelectRows('none')">取消全选</el-button>
          <el-button size="small" plain :disabled="!quotaSyncCreatableCount" @click="quotaSyncSelectRows('create')">只选新增</el-button>
          <el-button size="small" plain :disabled="!quotaSyncUpdatableCount" @click="quotaSyncSelectRows('update')">只选更新</el-button>
        </div>
        <el-table :data="quotaSync.items" class="users-table" max-height="440" :row-key="quotaSyncRowKey">
          <el-table-column label="同步" width="72" align="center">
            <template #default="{ row }"><el-checkbox v-model="row.selected" :disabled="!row.eligible" @change="handleQuotaSyncSelection(row)" /></template>
          </el-table-column>
          <el-table-column label="草稿项目" min-width="240">
            <template #default="{ row }"><div class="pricing-source"><strong>{{ row.item_name || '—' }}</strong><small>{{ row.spec || '无规格/特征' }}</small></div></template>
          </el-table-column>
          <el-table-column label="同步单价" width="138" align="right"><template #default="{ row }"><div class="pricing-source"><strong>{{ formatMoney(quotaSyncUnitPrice(row)) }}</strong><small>{{ quotaSyncPriceSourceLabel(row) }}</small></div></template></el-table-column>
          <el-table-column label="已有账户定额" min-width="190">
            <template #default="{ row }"><div v-if="row.existing_item" class="pricing-source"><strong>{{ row.existing_item.item_name }}</strong><small>{{ row.existing_item.status }} · R{{ row.existing_item.revision }} · {{ formatMoney(row.existing_item.unit_price) }}</small></div><span v-else>新增账户定额</span></template>
          </el-table-column>
          <el-table-column label="处理方式" width="180">
            <template #default="{ row }"><el-select v-model="row.action" size="small" :disabled="!row.eligible || !row.selected"><el-option v-for="option in quotaSyncActionOptions(row)" :key="option.value" :label="option.label" :value="option.value" /></el-select></template>
          </el-table-column>
          <el-table-column label="提示" min-width="180"><template #default="{ row }">{{ quotaSyncHint(row) }}</template></el-table-column>
        </el-table>
        <el-form label-position="top" class="quota-sync-form">
          <el-form-item label="同步说明" required>
            <el-input v-model="quotaSync.reason" maxlength="2000" show-word-limit placeholder="说明这批价格被账户认可的依据" />
          </el-form-item>
        </el-form>
      </template>
    </template>
    <template #footer>
      <el-button @click="quotaSync.visible = false">取消</el-button>
      <el-button type="primary" :loading="quotaSync.confirming" :disabled="!quotaSyncCanConfirm" @click="confirmAccountQuotaSync">确认同步为账户定额草稿</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="candidateDrawer.visible" size="760px" title="定额候选与匹配证据">
    <el-skeleton v-if="candidateDrawer.loading" :rows="8" animated />
    <template v-else>
      <el-descriptions v-if="candidateDrawer.line" :column="2" border>
        <el-descriptions-item label="清单项目">{{ candidateDrawer.line.item_name || candidateDrawer.line.project_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="工程量">{{ formatQuantity(candidateDrawer.line.quantity ?? candidateDrawer.line.calculation_quantity) }} {{ candidateDrawer.line.unit || '' }}</el-descriptions-item>
        <el-descriptions-item label="项目特征" :span="2">{{ candidateDrawer.line.spec || candidateDrawer.line.project_feature || '—' }}</el-descriptions-item>
        <el-descriptions-item label="匹配状态"><el-tag :type="matchStatusTag(candidateDrawer.line.match_status)" effect="plain">{{ matchStatusLabel(candidateDrawer.line.match_status) }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="提示">{{ candidateDrawer.line.status_message || candidateDrawer.line.issue_message || '—' }}</el-descriptions-item>
      </el-descriptions>

      <div class="drawer-section">
        <div class="budget-title"><div><strong>候选企业定额</strong><small>P2-1 仅供核对，不能在此选定或修改候选</small></div></div>
        <el-table :data="candidateDrawer.candidates" class="users-table" empty-text="当前行没有定额候选">
          <el-table-column label="排名" width="70" align="center"><template #default="{ row, $index }">{{ row.rank || $index + 1 }}</template></el-table-column>
          <el-table-column label="定额" min-width="260">
            <template #default="{ row }"><div class="pricing-source"><strong>{{ candidateQuotaLabel(row) }}</strong><small>{{ candidateQuotaMeta(row) }}</small></div></template>
          </el-table-column>
          <el-table-column label="匹配分" width="100" align="right"><template #default="{ row }">{{ candidateScore(row) }}</template></el-table-column>
          <el-table-column label="单位价" width="110" align="right"><template #default="{ row }">{{ formatMoney(candidateUnitPrice(row)) }}</template></el-table-column>
          <el-table-column label="依据" min-width="180"><template #default="{ row }">{{ candidateReason(row) }}</template></el-table-column>
        </el-table>
      </div>

      <div class="drawer-section">
        <div class="budget-title"><div><strong>匹配证据</strong><small>保存的是本次计价快照，不跟随后续企业定额版本变化</small></div></div>
        <pre class="pricing-evidence">{{ candidateEvidenceText }}</pre>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Edit } from '@element-plus/icons-vue'
import { budgetApiErrorMessage, budgetProjectApi, budgetResponseData, budgetResponseItems } from './budgetProjectApi'

const props = defineProps({
  project: { type: Object, default: null },
  featureAvailable: { type: Boolean, default: false },
})

const loading = ref(false)
const creating = ref(false)
const linesLoading = ref(false)
const serverFeatureDisabled = ref(false)
const readiness = ref(null)
const runs = ref([])
const selectedRunId = ref(null)
const selectedRun = computed(() => runs.value.find((run) => runIdOf(run) === selectedRunId.value) || null)
const lines = ref([])
const linePage = ref(1)
const linePageSize = 50
const lineTotal = ref(0)
const lineFilters = reactive({ keyword: '', match_status: '', pricing_status: '' })
const candidateDrawer = reactive({ visible: false, loading: false, line: null, candidates: [], evidence: null })
const draftLoading = ref(false)
const draftSaving = ref(false)
const draftLinesLoading = ref(false)
const draft = ref(null)
const selectedDraftMode = ref('enterprise_ai')
const draftLines = ref([])
const draftLinePage = ref(1)
const draftLinePageSize = 50
const draftLineTotal = ref(0)
const draftFilters = reactive({ keyword: '', match_status: '', pricing_status: '' })
const draftPriceInputs = reactive({})
const draftBreakdownInputs = reactive({})
const draftBreakdownEditing = reactive({})
const draftLineSaving = reactive({})
const draftLineAiEstimating = reactive({})
const totalsExpanded = ref(false)
const totalsSaving = ref(false)
const totalsConfigInputs = reactive({
  measures_rate: 0,
  management_rate: 0,
  other_fee: 0,
  suspended_amount: 0,
  area: 0,
  quote_adjustment_percent: 0,
})
const draftQuoteJobCancelling = ref(false)
const draftQuoteJob = ref(null)
const draftQuoteJobStarting = ref(false)
let draftQuoteJobPollTimer = null
const quotaSync = reactive({ visible: false, loading: false, confirming: false, items: [], reason: '' })

const matchStatusOptions = [
  { value: 'auto_matched', label: '自动匹配' },
  { value: 'manual_matched', label: '人工匹配' },
  { value: 'ambiguous', label: '多候选待复核' },
  { value: 'unmatched', label: '未匹配' },
  { value: 'unit_conflict', label: '单位冲突' },
]

const pricingStatusOptions = [
  { value: 'priced', label: '完成计价' },
  { value: 'quantity_unresolved', label: '工程量待解决' },
  { value: 'missing_unit_price', label: '定额单价缺失' },
  { value: 'pending_match', label: '待匹配' },
  { value: 'unit_conflict', label: '单位冲突' },
  { value: 'numeric_overflow', label: '数值超限' },
]

const hasOwn = (value, key) => Boolean(value && Object.prototype.hasOwnProperty.call(value, key))
const projectCapability = (key) => hasOwn(props.project?.capabilities, key) && props.project.capabilities[key] === true
const readinessCapability = (key) => hasOwn(readiness.value?.capabilities, key) && readiness.value.capabilities[key] === true
const projectId = computed(() => Number(props.project?.id ?? props.project?.project_id ?? 0))
const projectArchived = computed(() => (props.project?.workspace_status ?? props.project?.status) === 'archived')
const pricingAvailable = computed(() => props.featureAvailable === true && !serverFeatureDisabled.value)
const canViewPricing = computed(() => pricingAvailable.value && projectCapability('can_view_pricing'))
const canManageDraft = computed(() => (
  canViewPricing.value
  && !projectArchived.value
  && (
    projectCapability('can_manage_pricing_draft')
    || projectCapability('can_create_pricing_draft')
    || projectCapability('can_create_pricing_run')
    || readinessCapability('can_manage_pricing_draft')
    || readinessCapability('can_create_pricing_draft')
  )
))
const readinessQuotaVersion = computed(() => readiness.value?.active_quota_version || null)
const readinessEligible = computed(() => readiness.value?.eligible === true || readiness.value?.ready === true)
const readinessBatchId = computed(() => readiness.value?.active_import_batch_id ?? readiness.value?.active_import?.batch_id ?? null)
const readinessRevisionId = computed(() => readiness.value?.active_import_revision_id ?? readiness.value?.active_import?.revision_id ?? null)
const formalPointersMatch = computed(() => (
  Number(props.project?.active_import_batch_id || 0) > 0
  && Number(props.project?.active_import_revision_id || 0) > 0
  && Number(readinessBatchId.value || 0) === Number(props.project?.active_import_batch_id)
  && Number(readinessRevisionId.value || 0) === Number(props.project?.active_import_revision_id)
))
const canCreatePricingRun = computed(() => (
  pricingAvailable.value
  && !projectArchived.value
  && projectCapability('can_create_pricing_run')
  && readinessCapability('can_create_pricing_run')
  && readinessEligible.value
  && formalPointersMatch.value
  && Number(readinessQuotaVersion.value?.id || 0) > 0
))
const readinessMessage = computed(() => readiness.value?.message || readiness.value?.reason?.message || readiness.value?.reason_message || '当前项目尚不具备计价条件')
const runSummary = computed(() => selectedRun.value?.summary || selectedRun.value?.coverage_summary || {})
const coverageStatus = computed(() => selectedRun.value?.completeness_status || selectedRun.value?.coverage_status || runSummary.value.completeness_status || runSummary.value.coverage_status || 'pending')
const coveragePercent = computed(() => {
  const explicitValue = runSummary.value.coverage_percent ?? selectedRun.value?.coverage_percent
  if (explicitValue !== null && explicitValue !== undefined && explicitValue !== '') {
    const value = Number(explicitValue)
    if (Number.isFinite(value)) return Math.min(100, Math.max(0, Number(value.toFixed(2))))
  }
  const priced = Number(selectedRun.value?.amount_priced_count ?? runSummary.value.amount_priced_count)
  const total = Number(selectedRun.value?.standard_item_count ?? runSummary.value.standard_item_count)
  if (!Number.isFinite(priced) || !Number.isFinite(total) || total <= 0) return 0
  return Math.min(100, Math.max(0, Number(((priced / total) * 100).toFixed(2))))
})
const candidateEvidenceText = computed(() => {
  const evidence = candidateDrawer.evidence
  if (evidence === null || evidence === undefined || evidence === '') return '暂无匹配证据'
  if (typeof evidence === 'string') return evidence
  try { return JSON.stringify(evidence, null, 2) } catch { return String(evidence) }
})
const draftSummary = computed(() => draft.value?.summary || draft.value?.pricing_summary || {})
const draftTotals = computed(() => draftSummary.value?.totals || {})
const draftTotalsConfig = computed(() => draftSummary.value?.totals_config || {})
const quoteStatCards = computed(() => [
  { key: 'main_material_total', label: '主材', value: draftTotals.value.main_material_total },
  { key: 'auxiliary_material_total', label: '辅材', value: draftTotals.value.auxiliary_material_total },
  { key: 'labor_total', label: '人工费', value: draftTotals.value.labor_total },
  { key: 'subcontract_total', label: '分包', value: draftTotals.value.subcontract_total },
  { key: 'tax_excluded_total', label: '不含税', value: draftTotals.value.tax_excluded_total },
  { key: 'tax_included_total', label: '含税', value: draftTotals.value.tax_included_total },
])
const quoteTotalsRows = computed(() => [
  { order: '一', name: '直接费小计', amount: draftTotals.value.direct_subtotal, default_amount: draftTotals.value.direct_subtotal, formula: '人工费+主材费+辅材费+分包', remark: '', edit_type: '按明细' },
  { order: '1.1', name: '人工费', amount: draftTotals.value.labor_total, default_amount: draftTotals.value.labor_total, formula: '清单人工费汇总', remark: '', edit_type: '按明细' },
  { order: '1.2', name: '主材费', amount: draftTotals.value.main_material_total, default_amount: draftTotals.value.main_material_total, formula: '清单主材费汇总', remark: '', edit_type: '按明细' },
  { order: '1.3', name: '辅材费', amount: draftTotals.value.auxiliary_material_total, default_amount: draftTotals.value.auxiliary_material_total, formula: '清单辅材费汇总', remark: '', edit_type: '按明细' },
  { order: '1.4', name: '分包', amount: draftTotals.value.subcontract_total, default_amount: draftTotals.value.subcontract_total, formula: '清单分包费汇总', remark: '', edit_type: '按明细' },
  { order: '二', name: '措施费', amount: draftTotals.value.measures_fee, default_amount: draftTotals.value.measures_fee, formula: '直接费小计×措施费率', remark: `当前 ${formatRate(draftTotalsConfig.value.measures_rate || 0)}`, edit_type: '按费率' },
  { order: '三', name: '管理费', amount: draftTotals.value.management_fee, default_amount: draftTotals.value.management_fee, formula: '直接费小计×管理费率', remark: `当前 ${formatRate(draftTotalsConfig.value.management_rate || 0)}`, edit_type: '按费率' },
  { order: '四', name: '其它费用', amount: draftTotals.value.other_fee, default_amount: draftTotals.value.other_fee, formula: '人工录入', remark: '', edit_type: '按金额' },
  { order: '五', name: '税金', amount: draftTotals.value.tax_total, default_amount: draftTotals.value.tax_total, formula: '不含税综合价×9%', remark: '税率固定 9%', edit_type: '自动' },
  { order: '六', name: '暂列金额', amount: draftTotals.value.suspended_amount, default_amount: draftTotals.value.suspended_amount, formula: '人工录入', remark: '', edit_type: '按金额' },
  { order: '七', name: '成本合计', amount: draftTotals.value.cost_total, default_amount: draftTotals.value.cost_total, formula: '不含税+措施费+管理费+其它费用+暂列金额', remark: '', edit_type: '自动' },
  { order: '八', name: '单方成本', amount: draftTotals.value.unit_cost, default_amount: draftTotals.value.unit_cost, formula: '成本合计/面积', remark: '', edit_type: '自动' },
  { order: '九', name: '报价上下浮百分比', amount: draftTotalsConfig.value.quote_adjustment_percent, default_amount: draftTotalsConfig.value.quote_adjustment_percent, formula: '报价金额=成本合计×(1+上下浮/100)', remark: '可输入负数下浮', edit_type: '按费率', is_rate: true },
  { order: '十', name: '报价金额', amount: draftTotals.value.quote_amount, default_amount: draftTotals.value.quote_amount, formula: '成本合计×(1+报价上下浮百分比/100)', remark: '', edit_type: '自动' },
])
const draftRevision = computed(() => Number(draft.value?.revision ?? draft.value?.draft_revision ?? 0))
const draftQuoteJobRunning = computed(() => ['queued', 'running'].includes(draftQuoteJob.value?.status))
const draftQuoteJobTerminal = computed(() => Boolean(draftQuoteJob.value?.terminal || ['succeeded', 'partial_failed', 'failed', 'canceled'].includes(draftQuoteJob.value?.status)))
const draftQuoteJobPercent = computed(() => Math.min(100, Math.max(0, Number(draftQuoteJob.value?.progress_percent || 0))))
const canStartDraftQuoteJob = computed(() => (
  canManageDraft.value
  && selectedDraftMode.value === 'enterprise_ai'
  && readinessEligible.value
  && formalPointersMatch.value
  && Number(readinessQuotaVersion.value?.id || 0) > 0
  && !draftQuoteJobRunning.value
))
const draftActionLabel = computed(() => {
  const label = draftModeLabel(selectedDraftMode.value)
  if (!draft.value) return `创建${label}草稿`
  return `重建${label}草稿`
})
const quotaSyncCreateCount = computed(() => quotaSync.items.filter((row) => row.selected && row.action === 'create').length)
const quotaSyncUpdateCount = computed(() => quotaSync.items.filter((row) => row.selected && row.action === 'update_existing').length)
const quotaSyncSkipCount = computed(() => quotaSync.items.length - quotaSyncCreateCount.value - quotaSyncUpdateCount.value)
const quotaSyncEligibleCount = computed(() => quotaSync.items.filter((row) => row.eligible).length)
const quotaSyncSelectedCount = computed(() => quotaSync.items.filter((row) => row.selected).length)
const quotaSyncCreatableCount = computed(() => quotaSync.items.filter((row) => row.eligible && (row.allowed_actions || []).includes('create')).length)
const quotaSyncUpdatableCount = computed(() => quotaSync.items.filter((row) => row.eligible && (row.allowed_actions || []).includes('update_existing')).length)
const quotaSyncCanConfirm = computed(() => (
  quotaSync.items.length > 0
  && (quotaSyncCreateCount.value + quotaSyncUpdateCount.value) > 0
  && quotaSync.reason.trim().length >= 2
  && !quotaSync.loading
))

const runIdOf = (run) => run?.id ?? run?.run_id
const lineIdOf = (line) => line?.id ?? line?.line_id ?? line?.line_uuid ?? line?.row_key
const draftModeOf = (value) => value?.pricing_mode || value?.mode || 'enterprise_ai'
const draftModeLabel = (value) => ({ enterprise_ai: '基础定额', account_strict: '账户定额' })[value] || value || '未知模式'
const formatDate = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const formatMoney = (value) => {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
const formatQuantity = (value) => {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) : '—'
}
const formatPercent = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(1) + '%' : '—'
const quotaVersionLabel = (version) => {
  if (!version) return '—'
  const code = version.version_code || version.code || ('#' + (version.id || '—'))
  return code + (version.version_name || version.name ? ' · ' + (version.version_name || version.name) : '')
}
const runQuotaVersion = (run) => {
  const nested = run?.quota_version || run?.enterprise_quota_version || run?.quota_version_snapshot
  if (nested) return nested
  if (!run?.quota_version_id) return null
  return {
    id: run.quota_version_id,
    version_code: run.quota_version_code,
    version_name: run.quota_version_name,
  }
}
const runOptionLabel = (run) => (run?.run_no || (run?.run_number ? '计价版本 ' + run.run_number : null) || run?.run_uuid || ('计价 #' + runIdOf(run))) + ' · ' + coverageStatusLabel(run?.completeness_status || run?.coverage_status || run?.summary?.completeness_status || run?.summary?.coverage_status || 'pending')
const coverageStatusLabel = (value) => ({ complete: '完整计价', partial: '部分计价', pending: '计价处理中', none: '尚未计价', empty: '尚未计价' })[value] || value || '未知状态'
const coverageStatusTag = (value) => ({ complete: 'success', partial: 'warning', pending: 'info', none: 'info', empty: 'info' })[value] || 'info'
const matchStatusLabel = (value) => ({ auto_matched: '自动匹配', manual_matched: '人工匹配', ambiguous: '多候选待复核', unmatched: '未匹配', unit_conflict: '单位冲突' })[value] || value || '未知'
const matchStatusTag = (value) => ({ auto_matched: 'success', manual_matched: 'success', ambiguous: 'warning', unmatched: 'info', unit_conflict: 'danger' })[value] || 'info'
const pricingStatusLabel = (value) => ({ priced: '完成计价', quantity_unresolved: '工程量待解决', missing_unit_price: '定额单价缺失', pending_match: '待匹配', unit_conflict: '单位冲突', numeric_overflow: '数值超限' })[value] || value || '未知'
const pricingStatusTag = (value) => ({ priced: 'success', quantity_unresolved: 'warning', missing_unit_price: 'warning', pending_match: 'info', unit_conflict: 'danger', numeric_overflow: 'danger' })[value] || 'info'
const draftQuoteJobStatusLabel = (value) => ({ queued: '排队中', running: '生成中', succeeded: '已完成', partial_failed: '部分失败', failed: '失败', canceled: '已取消' })[value] || value || '未知'
const draftQuoteJobStatusTag = (value) => ({ queued: 'info', running: 'warning', succeeded: 'success', partial_failed: 'warning', failed: 'danger', canceled: 'info' })[value] || 'info'
const draftBreakdownColumns = [
  { key: 'labor_unit_cost', label: '人工费', width: 110 },
  { key: 'main_material_unit_cost', label: '主材费', width: 110 },
  { key: 'auxiliary_material_unit_cost', label: '辅材费', width: 110 },
  { key: 'tax_amount', label: '税金', width: 100, readonly: true },
  { key: 'main_material_without_loss', label: '主材费不含损耗', width: 145 },
  { key: 'loss_rate', label: '损耗率', width: 105 },
  { key: 'machinery_unit_cost', label: '机械费', width: 110 },
  { key: 'comprehensive_unit_cost', label: '综合费', width: 110 },
  { key: 'management_unit_cost', label: '管理费', width: 110 },
  { key: 'profit_unit_cost', label: '利润费', width: 110 },
  { key: 'measure_unit_cost', label: '措施费', width: 110 },
  { key: 'owner_material_unit_price', label: '甲供材单价', width: 125 },
  { key: 'owner_material_loss_amount', label: '甲供材损耗金', width: 135 },
]
const breakdownUnitPriceKeys = [
  'labor_unit_cost',
  'main_material_unit_cost',
  'auxiliary_material_unit_cost',
  'machinery_unit_cost',
  'comprehensive_unit_cost',
  'management_unit_cost',
  'profit_unit_cost',
  'measure_unit_cost',
]
const summaryCount = (key) => Number(selectedRun.value?.[key] ?? runSummary.value?.[key] ?? 0)
const matchedQuota = (row) => row?.selected_quota || row?.matched_quota || row?.selected_candidate || row?.quota_item || row?.selected_source || row?.selected_quota_snapshot || row?.selected_quota_item_snapshot || null
const matchedQuotaLabel = (row) => {
  const quota = matchedQuota(row)
  if (!quota) return '—'
  return (quota.quota_code ? quota.quota_code + ' · ' : '') + (quota.item_name || quota.name || '未命名定额')
}
const matchedQuotaMeta = (row) => {
  const quota = matchedQuota(row)
  if (!quota) return row?.status_message || row?.issue_message || '未形成唯一匹配'
  return (quota.unit || '无单位') + (quota.match_type ? ' · ' + quota.match_type : '')
}
const lineUnitCost = (row) => row?.effective_unit_cost ?? row?.quota_unit_price ?? row?.unit_cost ?? row?.unit_price ?? matchedQuota(row)?.unit_price ?? null
const lineTotalCost = (row) => row?.line_total ?? row?.line_cost ?? row?.total_cost ?? row?.amount ?? null
const draftLineUnitPrice = (row) => row?.effective_unit_price ?? row?.final_unit_price ?? row?.manual_unit_price ?? row?.base_unit_price ?? row?.unit_price ?? null
const quoteSource = (row) => row?.selected_source || matchedQuota(row) || {}
const sourceContext = (row) => row?.source_context || row?.source_row_context || {}
const pricingBreakdown = (row) => row?.pricing_breakdown || row?.cost_breakdown || {}
const quoteUnitPrice = (row) => draftLineUnitPrice(row) ?? lineUnitCost(row)
const firstValue = (sources, keys) => {
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue
    for (const key of keys) {
      const value = source[key]
      if (value !== null && value !== undefined && value !== '') return value
    }
  }
  return null
}
const rowRegion = (row) => firstValue([row, sourceContext(row), sourceContext(row).raw_fields], ['region', 'area', 'work_region']) || '—'
const rowWorkArea = (row) => firstValue([row, sourceContext(row), sourceContext(row).raw_fields], ['work_area', 'location', 'part', 'position']) || '—'
const rowRemark = (row) => firstValue([pricingBreakdown(row), row, sourceContext(row), sourceContext(row).raw_fields], ['remark', 'notes', 'comment']) || '—'
const feeKeyMap = {
  labor: ['labor_unit_cost', 'labor_fee', 'labor_unit_price', 'labor_cost'],
  main_material: ['main_material_unit_cost', 'main_material_fee', 'main_material_unit_price', 'main_material_cost', 'material_fee'],
  auxiliary_material: ['auxiliary_material_unit_cost', 'auxiliary_material_fee', 'auxiliary_material_unit_price', 'auxiliary_material_cost'],
  machinery: ['machinery_unit_cost', 'machinery_fee', 'machinery_unit_price', 'machinery_cost', 'machine_fee'],
  comprehensive: ['comprehensive_unit_cost', 'comprehensive_fee', 'composite_fee', 'overhead_fee'],
  management: ['management_unit_cost', 'management_fee', 'management_cost'],
  profit: ['profit_unit_cost', 'profit_fee', 'profit_cost'],
  measure: ['measure_unit_cost', 'measure_fee', 'measure_cost'],
}
const breakdownSources = (row) => [pricingBreakdown(row), row, row?.cost_breakdown, quoteSource(row), quoteSource(row)?.cost_breakdown]
const feeUnitValue = (row, bucket) => firstValue(breakdownSources(row), feeKeyMap[bucket] || [])
const lossRate = (row) => firstValue(breakdownSources(row), ['loss_rate', 'material_loss_rate', 'main_material_loss_rate'])
const mainMaterialWithoutLoss = (row) => firstValue(breakdownSources(row), ['main_material_without_loss', 'main_material_fee_without_loss', 'main_material_no_loss_fee'])
const ownerMaterialUnitPrice = (row) => firstValue(breakdownSources(row), ['owner_material_unit_price', 'client_material_unit_price', 'jia_material_unit_price'])
const ownerMaterialLossAmount = (row) => firstValue(breakdownSources(row), ['owner_material_loss_amount', 'client_material_loss_amount', 'jia_material_loss_amount'])
const taxAmount = (row) => firstValue(breakdownSources(row), ['tax_amount', 'tax_fee'])
const formatRate = (value) => {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return (Math.abs(number) <= 1 ? number * 100 : number).toFixed(2) + '%'
}
const materialSupplyMode = (row) => {
  const explicit = firstValue([pricingBreakdown(row), row, sourceContext(row), quoteSource(row), quoteSource(row)?.cost_breakdown], ['material_supply_mode', 'main_material_supply_mode', 'purchase_mode'])
  if (explicit) return explicit
  if (ownerMaterialUnitPrice(row) !== null || ownerMaterialLossAmount(row) !== null) return '甲供'
  if (feeUnitValue(row, 'main_material') !== null || mainMaterialWithoutLoss(row) !== null) return '乙供'
  return '—'
}
const draftBreakdownKeys = [...draftBreakdownColumns.map((column) => column.key), 'material_supply_mode', 'remark']
const draftBreakdownInitialValue = (row, key) => {
  const value = firstValue([pricingBreakdown(row), row, sourceContext(row), quoteSource(row), quoteSource(row)?.cost_breakdown], [key])
  return value === null || value === undefined ? '' : String(value)
}
const ensureDraftBreakdownInputs = (row) => {
  const lineId = lineIdOf(row)
  if (!lineId) return
  if (!draftBreakdownInputs[lineId]) draftBreakdownInputs[lineId] = {}
  for (const key of draftBreakdownKeys) {
    if (draftBreakdownInputs[lineId][key] === undefined) draftBreakdownInputs[lineId][key] = draftBreakdownInitialValue(row, key)
  }
}
const draftBreakdownInputValue = (row, key) => {
  ensureDraftBreakdownInputs(row)
  return draftBreakdownInputs[lineIdOf(row)]?.[key] ?? ''
}
const setDraftBreakdownInput = (row, key, value) => {
  ensureDraftBreakdownInputs(row)
  draftBreakdownInputs[lineIdOf(row)][key] = value
}
const draftBreakdownEditKey = (row, key) => `${lineIdOf(row)}:${key}`
const isDraftBreakdownEditing = (row, key) => draftBreakdownEditing[draftBreakdownEditKey(row, key)] === true
const beginDraftBreakdownEdit = (row, key) => {
  ensureDraftBreakdownInputs(row)
  const lineId = lineIdOf(row)
  if (draftBreakdownInputs[lineId][key] === '') {
    draftBreakdownInputs[lineId][key] = key === 'material_supply_mode' || key === 'remark' ? '' : '0'
  }
  draftBreakdownEditing[draftBreakdownEditKey(row, key)] = true
}
const parseDraftDecimal = (value) => {
  const raw = String(value ?? '').replace(/,/g, '').trim()
  if (!raw) return null
  const number = Number(raw)
  return Number.isFinite(number) && number >= 0 ? number : NaN
}
const draftBreakdownPayload = (row) => {
  ensureDraftBreakdownInputs(row)
  const values = draftBreakdownInputs[lineIdOf(row)] || {}
  const payload = {}
  for (const column of draftBreakdownColumns) {
    const number = parseDraftDecimal(values[column.key])
    if (Number.isNaN(number)) return null
    if (number !== null) payload[column.key] = number
  }
  for (const key of ['material_supply_mode', 'remark']) {
    const value = String(values[key] ?? '').trim()
    if (value) payload[key] = value
  }
  return payload
}
const draftBreakdownCompositePrice = (payload) => {
  if (!payload) return null
  const total = breakdownUnitPriceKeys.reduce((sum, key) => sum + Number(payload[key] || 0), 0)
  return total > 0 ? Number(total.toFixed(6)) : null
}
const draftPreviewUnitPrice = (row) => draftBreakdownCompositePrice(draftBreakdownPayload(row))
const draftPreviewLineTotal = (row) => {
  const unitPrice = draftPreviewUnitPrice(row)
  if (unitPrice === null) return null
  const quantity = Number(row?.quantity ?? row?.calculation_quantity)
  return Number.isFinite(quantity) && quantity > 0 ? Number((quantity * unitPrice).toFixed(6)) : 0
}
const draftPreviewTaxAmount = (row) => {
  const unitPrice = draftPreviewUnitPrice(row) ?? quoteUnitPrice(row)
  return unitPrice === null || unitPrice === undefined ? null : Number((Number(unitPrice) * 0.09).toFixed(6))
}
const formatBreakdownDisplay = (row, column) => {
  const value = column.key === 'loss_rate'
    ? lossRate(row)
    : firstValue(breakdownSources(row), [column.key])
  if (column.key === 'loss_rate') return formatRate(value ?? 0)
  return formatMoney(value ?? 0)
}
const hasManualPrice = (row) => row?.manual_unit_price !== null && row?.manual_unit_price !== undefined && row?.manual_unit_price !== ''
const hasBasePrice = (row) => row?.base_unit_price !== null && row?.base_unit_price !== undefined && row?.base_unit_price !== ''
const canAiEstimateDraftLine = (row) => !hasManualPrice(row) && !hasBasePrice(row)
const draftPriceSourceLabel = (row) => ({
  enterprise_quota: '企业定额', enterprise: '企业定额', account_quota: '账户定额', account: '账户定额',
  manual: '人工调整', manual_breakdown: '拆分计价', manual_adjusted: '人工调整', llm: 'AI 估价', ai_estimate: 'AI 估价',
  unmatched: '暂无价格', pending: '暂无价格',
})[row?.price_source || row?.pricing_source || row?.unit_price_source] || (hasManualPrice(row) ? '人工调整' : (draftLineUnitPrice(row) === null ? '暂无价格' : '草稿价格'))
const draftPriceSourceMeta = (row) => {
  if ((row?.price_source || row?.pricing_source || row?.unit_price_source) === 'ai_estimate') {
    const estimate = row?.ai_estimate?.estimate || row?.ai_estimate || {}
    const confidence = estimate.confidence !== null && estimate.confidence !== undefined ? `可信度 ${(Number(estimate.confidence) * 100).toFixed(0)}%` : '需人工确认'
    return estimate.basis || confidence
  }
  return matchedQuotaLabel(row)
}
const quotaSyncUnitPrice = (row) => row?.sync_unit_price ?? row?.effective_unit_price ?? row?.manual_unit_price ?? null
const quotaSyncPriceSourceLabel = (row) => draftPriceSourceLabel({
  price_source: row?.sync_price_source || row?.price_source,
  manual_unit_price: row?.manual_unit_price,
  effective_unit_price: quotaSyncUnitPrice(row),
})
const draftSummaryCount = (...keys) => {
  for (const key of keys) {
    const value = draft.value?.[key] ?? draftSummary.value?.[key]
    if (value !== null && value !== undefined && value !== '') return Number(value) || 0
  }
  return 0
}
const syncTotalsConfigInputs = () => {
  const config = draftTotalsConfig.value || {}
  for (const key of Object.keys(totalsConfigInputs)) {
    totalsConfigInputs[key] = Number(config[key] ?? 0) || 0
  }
}
async function saveDraftTotalsConfig() {
  if (!canManageDraft.value || !draft.value) return
  totalsSaving.value = true
  try {
    const response = await budgetProjectApi.updatePricingDraftTotalsConfig(projectId.value, {
      pricing_mode: selectedDraftMode.value,
      expected_revision: draftRevision.value,
      measures_rate: totalsConfigInputs.measures_rate,
      management_rate: totalsConfigInputs.management_rate,
      other_fee: totalsConfigInputs.other_fee,
      suspended_amount: totalsConfigInputs.suspended_amount,
      area: totalsConfigInputs.area,
      quote_adjustment_percent: totalsConfigInputs.quote_adjustment_percent,
      reason: 'quote_totals_config_edit',
    })
    draft.value = budgetResponseData(response)
    syncTotalsConfigInputs()
    ElMessage.success('统计费率已保存')
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已被其他操作更新，已重新加载最新内容，请再次保存费率')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '统计费率保存失败'))
    }
  } finally {
    totalsSaving.value = false
  }
}
const candidateCount = (row) => Number(row?.candidate_count ?? row?.candidates?.length ?? 0)
const candidateQuota = (row) => row?.quota_item || row?.quota_snapshot || row?.quota_item_snapshot || row?.quota || row
const candidateQuotaLabel = (row) => {
  const quota = candidateQuota(row)
  return (quota?.quota_code ? quota.quota_code + ' · ' : '') + (quota?.item_name || quota?.name || '未命名定额')
}
const candidateQuotaMeta = (row) => {
  const quota = candidateQuota(row)
  return (quota?.unit || '无单位') + (row?.match_type ? ' · ' + row.match_type : '')
}
const candidateUnitPrice = (row) => row?.unit_price ?? candidateQuota(row)?.unit_price ?? null
const candidateScore = (row) => {
  const value = row?.score_percent ?? row?.candidate_score ?? row?.match_score
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  const percent = row?.score_percent !== null && row?.score_percent !== undefined ? number : (number <= 1 ? number * 100 : number)
  return percent.toFixed(1)
}
const candidateReason = (row) => {
  const reasons = row?.reasons || row?.match_reasons
  if (Array.isArray(reasons)) return reasons.join('；') || '—'
  return reasons || row?.match_reason || row?.reason || '—'
}

function isFeatureDisabledError(error) {
  const detail = error?.response?.data?.detail
  const code = typeof detail === 'string' ? detail : detail?.code
  return ['FEATURE_DISABLED', 'BUDGET_PRICING_DISABLED'].includes(code) || error?.response?.status === 404
}

function resetPricing() {
  readiness.value = null
  runs.value = []
  selectedRunId.value = null
  lines.value = []
  lineTotal.value = 0
  resetDraft()
}

function clearDraftLinesState() {
  draftLines.value = []
  draftLineTotal.value = 0
  draftLinePage.value = 1
  for (const key of Object.keys(draftPriceInputs)) delete draftPriceInputs[key]
  for (const key of Object.keys(draftBreakdownInputs)) delete draftBreakdownInputs[key]
  for (const key of Object.keys(draftBreakdownEditing)) delete draftBreakdownEditing[key]
}

function resetDraft(resetMode = true) {
  stopDraftQuoteJobPolling()
  draftQuoteJob.value = null
  draft.value = null
  clearDraftLinesState()
  if (resetMode) selectedDraftMode.value = 'enterprise_ai'
}

function stopDraftQuoteJobPolling() {
  if (draftQuoteJobPollTimer) {
    window.clearTimeout(draftQuoteJobPollTimer)
    draftQuoteJobPollTimer = null
  }
}

function scheduleDraftQuoteJobPolling(jobId) {
  stopDraftQuoteJobPolling()
  if (!jobId || !projectId.value) return
  draftQuoteJobPollTimer = window.setTimeout(() => pollDraftQuoteJob(jobId), 1500)
}

async function loadCurrentDraftQuoteJob(silent = false) {
  if (!projectId.value || !canViewPricing.value) return
  if (selectedDraftMode.value !== 'enterprise_ai') {
    draftQuoteJob.value = null
    return
  }
  try {
    const response = await budgetProjectApi.currentPricingDraftQuoteJob(projectId.value, { pricing_mode: selectedDraftMode.value })
    const job = budgetResponseData(response)
    draftQuoteJob.value = job && (job.id || job.job_uuid) ? job : null
    if (draftQuoteJobRunning.value) scheduleDraftQuoteJobPolling(draftQuoteJob.value.job_uuid || draftQuoteJob.value.id)
  } catch (error) {
    if (!silent && error?.response?.status !== 404) {
      ElMessage.error(budgetApiErrorMessage(error, '一键生成报价任务加载失败'))
    }
  }
}

async function pollDraftQuoteJob(jobId) {
  if (!jobId || !projectId.value) return
  try {
    const response = await budgetProjectApi.pricingDraftQuoteJob(projectId.value, jobId)
    const job = budgetResponseData(response)
    draftQuoteJob.value = job
    if (job?.terminal || ['succeeded', 'partial_failed', 'failed', 'canceled'].includes(job?.status)) {
      stopDraftQuoteJobPolling()
      if (job.status === 'succeeded') ElMessage.success('一键生成报价已完成')
      if (job.status === 'partial_failed') ElMessage.warning('报价已生成，但有部分 AI 估价失败，请人工补价')
      if (job.status === 'failed') ElMessage.error('一键生成报价失败，请查看任务提示')
      if (job.status === 'canceled') ElMessage.info('一键生成报价已取消')
      await loadDraft(true)
      return
    }
    scheduleDraftQuoteJobPolling(jobId)
  } catch (error) {
    stopDraftQuoteJobPolling()
    ElMessage.error(budgetApiErrorMessage(error, '一键生成报价进度刷新失败'))
  }
}

async function refreshPricing() {
  if (!pricingAvailable.value || !canViewPricing.value || !projectId.value) {
    resetPricing()
    return
  }
  loading.value = true
  try {
    const [readinessResponse, runsResponse] = await Promise.all([
      budgetProjectApi.pricingReadiness(projectId.value),
      budgetProjectApi.listPricingRuns(projectId.value, { page: 1, page_size: 100 }),
    ])
    readiness.value = budgetResponseData(readinessResponse) || null
    runs.value = budgetResponseItems(runsResponse)
    const currentId = readiness.value?.current_pricing_run_id ?? props.project?.current_pricing_run_id
    const retainedId = runs.value.some((run) => runIdOf(run) === selectedRunId.value) ? selectedRunId.value : null
    selectedRunId.value = retainedId ?? (runs.value.find((run) => runIdOf(run) === currentId) ? currentId : runIdOf(runs.value[0]))
    linePage.value = 1
    await Promise.all([loadLines(), loadDraft()])
  } catch (error) {
    if (isFeatureDisabledError(error)) {
      serverFeatureDisabled.value = true
      resetPricing()
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '项目计价加载失败'))
    }
  } finally {
    loading.value = false
  }
}

async function loadDraft(silent = false) {
  if (!pricingAvailable.value || !canViewPricing.value || !projectId.value) {
    resetDraft()
    return
  }
  draftLoading.value = true
  try {
    const response = await budgetProjectApi.currentPricingDraft(projectId.value, { pricing_mode: selectedDraftMode.value })
    const data = budgetResponseData(response)
    const current = data?.draft ?? data?.current_draft ?? data
    draft.value = current && (current.id || current.draft_id || current.draft_uuid || current.pricing_mode) ? current : null
    syncTotalsConfigInputs()
    if (!draft.value) {
      draftLines.value = []
      draftLineTotal.value = 0
      syncTotalsConfigInputs()
      return
    }
    await loadCurrentDraftQuoteJob(true)
    await loadDraftLines()
  } catch (error) {
    if (error?.response?.status === 404) {
      draft.value = null
      draftLines.value = []
      draftLineTotal.value = 0
    } else if (!silent) {
      ElMessage.error(budgetApiErrorMessage(error, '计价草稿加载失败'))
    }
  } finally {
    draftLoading.value = false
  }
}

async function saveDraft() {
  if (!canManageDraft.value) return ElMessage.warning('当前账号无权编辑计价草稿')
  if (!formalPointersMatch.value) return ElMessage.warning('正式清单指针已变化，请刷新后再创建草稿')
  if (draft.value) {
    try {
      await ElMessageBox.confirm(
        `重建会重新读取当前正式清单，并清除${draftModeLabel(selectedDraftMode.value)}草稿行上的人工调整；另一种模式的草稿不会变化，P2-1 历史版本也不会变化。`,
        `确认重建${draftModeLabel(selectedDraftMode.value)}草稿`,
        { type: 'warning', confirmButtonText: '确认重建', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  draftSaving.value = true
  try {
    const payload = {
      pricing_mode: selectedDraftMode.value,
      source_import_batch_id: Number(props.project.active_import_batch_id),
      source_import_revision_id: Number(props.project.active_import_revision_id),
      ...(draft.value && draftModeOf(draft.value) === selectedDraftMode.value ? { expected_revision: draftRevision.value } : {}),
      ...(selectedDraftMode.value === 'enterprise_ai' && readinessQuotaVersion.value?.id
        ? { expected_active_quota_version_id: Number(readinessQuotaVersion.value.id) }
        : {}),
    }
    await budgetProjectApi.savePricingDraft(projectId.value, payload)
    ElMessage.success(draft.value ? '计价草稿已重建' : '计价草稿已创建')
    draftLinePage.value = 1
    await loadDraft()
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已被其他操作更新，已重新加载最新内容')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '计价草稿保存失败'))
    }
  } finally {
    draftSaving.value = false
  }
}

async function startDraftQuoteJob() {
  if (!canStartDraftQuoteJob.value) {
    return ElMessage.warning('当前只能在基础定额模式、正式清单和企业定额均就绪时一键生成报价')
  }
  draftQuoteJobStarting.value = true
  try {
    const response = await budgetProjectApi.createPricingDraftQuoteJob(projectId.value, {
      pricing_mode: 'enterprise_ai',
      source_import_batch_id: Number(props.project.active_import_batch_id),
      source_import_revision_id: Number(props.project.active_import_revision_id),
      expected_active_quota_version_id: Number(readinessQuotaVersion.value.id),
      ...(draft.value && draftModeOf(draft.value) === 'enterprise_ai' ? { expected_revision: draftRevision.value } : {}),
      ai_concurrency: 3,
      ai_batch_size: 6,
      reason: 'one_click_enterprise_ai_quote',
    })
    const job = budgetResponseData(response)
    draftQuoteJob.value = job
    selectedDraftMode.value = 'enterprise_ai'
    ElMessage.success('一键生成报价任务已启动')
    await loadDraft(true)
    const activeJob = draftQuoteJob.value || job
    if (activeJob?.terminal) {
      await loadDraft(true)
    } else {
      scheduleDraftQuoteJobPolling(activeJob?.job_uuid || activeJob?.id)
    }
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已被其他操作更新，已重新加载最新内容')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '一键生成报价启动失败'))
    }
  } finally {
    draftQuoteJobStarting.value = false
  }
}

async function cancelDraftQuoteJob() {
  const jobId = draftQuoteJob.value?.job_uuid || draftQuoteJob.value?.id
  if (!jobId || !projectId.value || !draftQuoteJobRunning.value) return
  try {
    await ElMessageBox.confirm(
      '取消后会停止未开始和正在等待的 AI 估价，已完成的报价行会保留。',
      '确认取消生成报价',
      { type: 'warning', confirmButtonText: '确认取消', cancelButtonText: '继续生成' },
    )
  } catch {
    return
  }
  draftQuoteJobCancelling.value = true
  try {
    const response = await budgetProjectApi.cancelPricingDraftQuoteJob(projectId.value, jobId)
    draftQuoteJob.value = budgetResponseData(response)
    stopDraftQuoteJobPolling()
    ElMessage.success('已取消报价生成')
    await loadDraft(true)
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '取消报价生成失败'))
  } finally {
    draftQuoteJobCancelling.value = false
  }
}

function searchDraftLines() {
  draftLinePage.value = 1
  loadDraftLines()
}

async function loadDraftLines() {
  if (!draft.value || !projectId.value) {
    draftLines.value = []
    draftLineTotal.value = 0
    return
  }
  draftLinesLoading.value = true
  try {
    const params = {
      pricing_mode: selectedDraftMode.value,
      page: draftLinePage.value,
      page_size: draftLinePageSize,
      ...(draftFilters.keyword.trim() ? { keyword: draftFilters.keyword.trim() } : {}),
      ...(draftFilters.match_status ? { match_status: draftFilters.match_status } : {}),
      ...(draftFilters.pricing_status ? { pricing_status: draftFilters.pricing_status } : {}),
    }
    const response = await budgetProjectApi.pricingDraftLines(projectId.value, params)
    draftLines.value = budgetResponseItems(response)
    const data = budgetResponseData(response)
    draftLineTotal.value = Number(response.data?.total ?? (!Array.isArray(data) ? data?.total : null) ?? draftLines.value.length)
    for (const row of draftLines.value) {
      draftPriceInputs[lineIdOf(row)] = hasManualPrice(row) ? String(row.manual_unit_price) : ''
      ensureDraftBreakdownInputs(row)
    }
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '计价草稿明细加载失败'))
  } finally {
    draftLinesLoading.value = false
  }
}

async function saveDraftLinePrice(row, clear = false) {
  if (!canManageDraft.value || !draft.value) return
  const lineId = lineIdOf(row)
  const raw = clear ? '' : String(draftPriceInputs[lineId] ?? '').replace(/,/g, '').trim()
  const breakdown = clear ? {} : draftBreakdownPayload(row)
  if (breakdown === null) return ElMessage.warning('拆分费用只能填写非负数字')
  const breakdownPrice = draftBreakdownCompositePrice(breakdown)
  let price = null
  if (breakdownPrice !== null) {
    price = breakdownPrice
  } else if (raw !== '') {
    price = Number(raw)
    if (!Number.isFinite(price) || price <= 0) return ElMessage.warning('请输入大于 0 的有效人工单价，或留空后清除')
  }
  draftLineSaving[lineId] = true
  try {
    await budgetProjectApi.updatePricingDraftLine(projectId.value, lineId, {
      pricing_mode: selectedDraftMode.value,
      expected_revision: draftRevision.value,
      expected_line_revision: Number(row.line_revision ?? row.revision ?? 0),
      manual_unit_price: price,
      pricing_breakdown: breakdown,
      reason: clear ? 'clear_manual_price' : (breakdownPrice !== null ? 'pricing_breakdown_edit' : 'manual_price_edit'),
    })
    ElMessage.success(price === null ? '人工单价已清除' : (breakdownPrice !== null ? '拆分费用已保存' : '人工单价已保存'))
    await loadDraft(true)
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已被其他操作更新，已重新加载最新内容，请再次确认后保存')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '人工单价保存失败'))
    }
  } finally {
    draftLineSaving[lineId] = false
  }
}

function clearDraftLinePrice(row) {
  const lineId = lineIdOf(row)
  draftPriceInputs[lineId] = ''
  draftBreakdownInputs[lineId] = {}
  for (const key of Object.keys(draftBreakdownEditing)) {
    if (key.startsWith(`${lineId}:`)) delete draftBreakdownEditing[key]
  }
  return saveDraftLinePrice(row, true)
}

async function estimateDraftLine(row) {
  if (!canManageDraft.value || !draft.value) return
  if (!canAiEstimateDraftLine(row)) return ElMessage.warning('已有人工价或定额价的行不需要 AI 估价')
  const lineId = lineIdOf(row)
  draftLineAiEstimating[lineId] = true
  try {
    await budgetProjectApi.estimatePricingDraftLine(projectId.value, lineId, {
      pricing_mode: selectedDraftMode.value,
      expected_revision: draftRevision.value,
      expected_line_revision: Number(row.line_revision ?? row.revision ?? 0),
      reason: 'manual_ai_estimate',
    })
    ElMessage.success('AI 估价已写入草稿，请人工确认')
    await loadDraft(true)
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已发生变化，已重新加载最新内容')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, 'AI 估价失败'))
    }
  } finally {
    draftLineAiEstimating[lineId] = false
  }
}

function quotaSyncRowKey(row) {
  return row.line_identifier || row.draft_line_id
}

function quotaSyncActionOptions(row) {
  const labels = { create: '新建账户定额草稿', update_existing: '更新已有定额并撤回为草稿', skip: '跳过本行' }
  return (row.allowed_actions || ['skip']).map((value) => ({ value, label: labels[value] || value }))
}

function quotaSyncHint(row) {
  if (row.block_code) return ({
    ACCOUNT_QUOTA_SYNC_MANUAL_PRICE_REQUIRED: '未填写人工单价，不能沉淀',
    ACCOUNT_QUOTA_SYNC_PRICE_REQUIRED: '当前行没有有效同步单价，不能沉淀',
    ACCOUNT_QUOTA_SYNC_ARCHIVED_TARGET: '同身份账户定额已归档并冻结，请人工处理',
    ACCOUNT_QUOTA_SYNC_DUPLICATE_SELECTION: '本次选择存在同身份重复行，请保留一行',
    ACCOUNT_QUOTA_SYNC_ITEM_NAME_REQUIRED: '缺少项目名称',
    ACCOUNT_QUOTA_SYNC_UNIT_REQUIRED: '缺少单位',
  })[row.block_code] || row.block_code
  if (row.existing_item) return '需由你决定是否用当前同步价更新已有定额'
  return '将创建为账户定额草稿，不会自动启用'
}

function handleQuotaSyncSelection(row) {
  if (row.selected && row.action === 'skip') {
    row.action = (row.allowed_actions || []).find((value) => value !== 'skip') || 'skip'
  }
}

function quotaSyncSelectRows(mode) {
  for (const row of quotaSync.items) {
    if (!row.eligible) {
      row.selected = false
      row.action = 'skip'
      continue
    }
    const allowed = row.allowed_actions || ['skip']
    const defaultAction = allowed.find((value) => value !== 'skip') || 'skip'
    if (mode === 'none') {
      row.selected = false
      row.action = 'skip'
    } else if (mode === 'create') {
      row.selected = allowed.includes('create')
      row.action = row.selected ? 'create' : 'skip'
    } else if (mode === 'update') {
      row.selected = allowed.includes('update_existing')
      row.action = row.selected ? 'update_existing' : 'skip'
    } else {
      row.selected = defaultAction !== 'skip'
      row.action = defaultAction
    }
  }
}

async function openAccountQuotaSync() {
  if (!draft.value || !canManageDraft.value) return
  quotaSync.visible = true
  quotaSync.loading = true
  quotaSync.confirming = false
  quotaSync.items = []
  quotaSync.reason = '从项目计价草稿同步账户认可的有效价格'
  try {
    const response = await budgetProjectApi.previewAccountQuotaSync(projectId.value, {
      pricing_mode: selectedDraftMode.value,
      expected_revision: draftRevision.value,
    })
    const data = budgetResponseData(response) || {}
    quotaSync.items = (data.items || []).map((row) => ({
      ...row,
      selected: Boolean(row.eligible && row.suggested_action && row.suggested_action !== 'skip'),
      action: row.suggested_action || 'skip',
    }))
  } catch (error) {
    quotaSync.visible = false
    ElMessage.error(budgetApiErrorMessage(error, '账户定额同步预览失败'))
  } finally {
    quotaSync.loading = false
  }
}

async function confirmAccountQuotaSync() {
  if (!quotaSyncCanConfirm.value || !draft.value) return
  quotaSync.confirming = true
  try {
    const response = await budgetProjectApi.confirmAccountQuotaSync(projectId.value, {
      pricing_mode: selectedDraftMode.value,
      expected_revision: draftRevision.value,
      reason: quotaSync.reason.trim(),
      items: quotaSync.items.map((row) => ({
        line_identifier: row.line_identifier,
        expected_line_revision: row.expected_line_revision,
        expected_target_revision: row.existing_item?.revision ?? null,
        action: row.selected ? row.action : 'skip',
      })),
    })
    const result = budgetResponseData(response) || {}
    ElMessage.success(`账户定额同步完成：新增 ${result.created_count || 0}，更新 ${result.updated_count || 0}，跳过 ${result.skipped_count || 0}`)
    quotaSync.visible = false
    await loadDraft(true)
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿或账户定额已发生更新，已阻止同步；请重新打开预览后确认')
      await loadDraft(true)
      return
    }
    ElMessage.error(budgetApiErrorMessage(error, '账户定额同步失败'))
  } finally {
    quotaSync.confirming = false
  }
}

async function createPricingRun() {
  if (!canCreatePricingRun.value) return ElMessage.warning('当前项目不具备创建计价任务的条件')
  creating.value = true
  try {
    const payload = {
      source_import_batch_id: Number(props.project.active_import_batch_id),
      source_import_revision_id: Number(props.project.active_import_revision_id),
      expected_enterprise_quota_version_id: Number(readiness.value.active_quota_version.id),
    }
    const response = await budgetProjectApi.createPricingRun(projectId.value, payload)
    const created = budgetResponseData(response)
    ElMessage.success(created?.coverage_status === 'partial' ? '计价已生成，当前为部分计价' : '计价任务已生成')
    await refreshPricing()
    const createdId = runIdOf(created)
    if (createdId && runs.value.some((run) => runIdOf(run) === createdId)) {
      selectedRunId.value = createdId
      await loadLines()
    }
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '创建计价任务失败'))
  } finally {
    creating.value = false
  }
}

async function selectRun() {
  linePage.value = 1
  await loadLines()
}

function searchLines() {
  linePage.value = 1
  loadLines()
}

async function loadLines() {
  if (!selectedRunId.value || !readinessCapability('can_view_pricing')) {
    lines.value = []
    lineTotal.value = 0
    return
  }
  linesLoading.value = true
  try {
    const params = {
      page: linePage.value,
      page_size: linePageSize,
      ...(lineFilters.keyword.trim() ? { keyword: lineFilters.keyword.trim() } : {}),
      ...(lineFilters.match_status ? { match_status: lineFilters.match_status } : {}),
      ...(lineFilters.pricing_status ? { pricing_status: lineFilters.pricing_status } : {}),
    }
    const response = await budgetProjectApi.pricingRunLines(selectedRunId.value, params)
    lines.value = budgetResponseItems(response)
    const data = budgetResponseData(response)
    lineTotal.value = Number(response.data?.total ?? (!Array.isArray(data) ? data?.total : null) ?? lines.value.length)
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '计价明细加载失败'))
  } finally {
    linesLoading.value = false
  }
}

async function openCandidates(row) {
  if (!readinessCapability('can_view_pricing') || !selectedRunId.value) return
  candidateDrawer.visible = true
  candidateDrawer.loading = true
  candidateDrawer.line = row
  candidateDrawer.candidates = []
  candidateDrawer.evidence = row?.evidence || row?.match_evidence || null
  try {
    const response = await budgetProjectApi.pricingLineCandidates(selectedRunId.value, lineIdOf(row))
    const data = budgetResponseData(response)
    candidateDrawer.line = data?.line || row
    candidateDrawer.candidates = Array.isArray(data) ? data : (data?.candidates || data?.items || [])
    candidateDrawer.evidence = data?.evidence || data?.match_evidence || candidateDrawer.evidence
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '候选与证据加载失败'))
  } finally {
    candidateDrawer.loading = false
  }
}

watch(
  () => [projectId.value, props.featureAvailable, props.project?.active_import_batch_id, props.project?.active_import_revision_id, projectCapability('can_view_pricing')],
  () => {
    serverFeatureDisabled.value = false
    resetDraft()
    refreshPricing()
  },
  { immediate: true },
)

watch(
  selectedDraftMode,
  () => {
    stopDraftQuoteJobPolling()
    draftQuoteJob.value = null
    draft.value = null
    clearDraftLinesState()
    if (pricingAvailable.value && canViewPricing.value && projectId.value) loadDraft(true)
  },
)

onBeforeUnmount(() => {
  stopDraftQuoteJobPolling()
})
</script>

<style scoped>
.budget-panel{padding:20px;border:1px solid rgba(148,163,184,.22);border-radius:20px;background:rgba(255,255,255,.9);box-shadow:0 14px 34px rgba(15,23,42,.06);margin-bottom:18px}.budget-title{display:flex;justify-content:space-between;gap:16px;margin-bottom:16px}.budget-title>div,.pricing-source{display:flex;flex-direction:column;gap:4px}.budget-title small,.pricing-source small,.pricing-context span,.pricing-metrics span,.pricing-run-meta{color:#64748b}.pricing-actions{align-items:flex-end;flex-direction:row!important}.pricing-alert{margin-bottom:14px}.pricing-context{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px}.pricing-context>div,.pricing-metrics>div{padding:14px;border:1px solid rgba(148,163,184,.2);border-radius:16px;background:#fff}.pricing-context strong{display:block;margin-top:7px}.pricing-run-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px}.pricing-run-toolbar>.el-select{width:min(420px,100%)}.pricing-run-meta{display:flex;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:wrap;font-size:13px}.pricing-metrics{display:grid;grid-template-columns:1.4fr repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}.pricing-metrics strong{display:block;margin:7px 0;font-size:22px}.pricing-filters{display:grid;grid-template-columns:minmax(260px,1fr) 190px 190px auto;gap:12px;margin-bottom:14px}.drawer-section{margin-top:22px}.pricing-evidence{max-height:360px;overflow:auto;margin:0;padding:16px;border-radius:14px;background:#0f172a;color:#e2e8f0;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word}.el-pagination{margin-top:16px;justify-content:flex-end}@media(max-width:1100px){.pricing-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:760px){.pricing-context,.pricing-metrics,.pricing-filters{grid-template-columns:1fr}.pricing-run-toolbar{align-items:stretch;flex-direction:column}.pricing-run-meta{justify-content:flex-start}.budget-title{align-items:flex-start;flex-direction:column}.pricing-actions{align-items:flex-start!important}}
.pricing-draft-workspace{margin:20px 0 24px;padding:18px;border:1px solid rgba(37,99,235,.18);border-radius:18px;background:linear-gradient(180deg,rgba(239,246,255,.72),rgba(255,255,255,.9))}.draft-mode-selector{margin-bottom:10px}.draft-mode-help{display:flex;flex-direction:column;gap:5px;margin-bottom:16px;padding:13px 15px;border-radius:14px;background:#fff;border:1px solid rgba(148,163,184,.2)}.draft-mode-help span,.draft-meta span{color:#64748b;font-size:13px}.draft-meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:14px}.draft-meta>div{padding:14px;border:1px solid rgba(148,163,184,.2);border-radius:16px;background:#fff}.draft-meta strong{display:block;margin-top:7px}.draft-metrics{grid-template-columns:repeat(5,minmax(0,1fr))}.quote-stat-strip{display:grid;grid-template-columns:auto repeat(6,minmax(140px,1fr)) auto;gap:4px;margin:-2px 0 14px;align-items:center}.quote-stat-title,.quote-stat-card{height:36px;display:flex;align-items:center;justify-content:center;border:1px solid rgba(148,163,184,.2);background:#fff}.quote-stat-title{padding:0 14px;color:#475569;font-size:13px}.quote-stat-card{gap:6px;border-radius:8px}.quote-stat-card span{color:#64748b}.quote-stat-card strong{font-size:16px}.quote-totals-panel{margin:-4px 0 14px;padding:12px;border:1px solid rgba(148,163,184,.22);border-radius:12px;background:#fff}.quote-rate-editor{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr)) auto;gap:10px;margin-top:12px;align-items:end}.quote-rate-editor label{display:flex;flex-direction:column;gap:5px;color:#64748b;font-size:12px}.draft-boundary-note{margin:-2px 0 14px;padding:10px 13px;border-radius:12px;background:#f1f5f9;color:#475569;font-size:13px}.draft-quote-job-card{margin:0 0 14px;padding:14px;border:1px solid rgba(34,197,94,.22);border-radius:16px;background:#fff}.draft-quote-job-head{display:flex;justify-content:space-between;gap:12px;margin-bottom:10px}.draft-quote-job-head>div{display:flex;flex-direction:column;gap:4px}.draft-quote-job-head small,.draft-quote-job-stats{color:#64748b;font-size:13px}.draft-quote-job-stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}.draft-filters{margin-top:4px}.draft-status-stack{display:flex;align-items:flex-start;flex-direction:column;gap:5px}.draft-price-editor{display:grid;grid-template-columns:minmax(92px,1fr) auto auto auto;gap:5px}.draft-price-editor .el-button+.el-button{margin-left:0}.quote-quantity{color:#2563eb}.quote-line-table:deep(.el-table__cell){vertical-align:top}.breakdown-input:deep(.el-input__inner){text-align:right}.breakdown-edit-cell{display:flex;align-items:center;gap:4px;min-height:24px}.breakdown-edit-cell-right{justify-content:flex-end}.quota-sync-summary,.quota-sync-bulk-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;margin-bottom:10px}.quota-sync-summary span{font-size:13px;color:#475569}.quota-sync-bulk-actions .el-button+.el-button{margin-left:0}@media(max-width:1100px){.draft-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.draft-meta{grid-template-columns:repeat(2,minmax(0,1fr))}.quote-stat-strip{grid-template-columns:1fr 1fr}.quote-rate-editor{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){.draft-meta{grid-template-columns:1fr}.pricing-draft-workspace{padding:14px}.draft-price-editor{grid-template-columns:1fr auto}.draft-price-editor .el-button:last-child{grid-column:2}.quote-stat-strip,.quote-rate-editor{grid-template-columns:1fr}}
</style>
