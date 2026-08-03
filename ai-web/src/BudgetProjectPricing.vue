<template>
  <section class="budget-panel pricing-panel">
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

      <div class="pricing-draft-workspace">
        <div class="budget-title">
          <div>
            <strong>报价草稿</strong>
            <small>快速审核与专业全字段共用同一份草稿；系统自动按账户定额、企业定额、AI估价顺序完成计价</small>
          </div>
          <div class="draft-actions">
            <el-button v-if="draft" plain :disabled="!canManageDraft" @click="openAccountQuotaSync">同步至账户定额</el-button>
            <el-button v-if="draft" plain :loading="originalExporting" :disabled="draftQuoteJobRunning" @click="exportOriginalFormatPricingDraft">导出</el-button>
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
          </div>
        </div>

        <div class="quote-workbench-flow" aria-label="报价流程">
          <div v-for="(step, index) in quoteWorkflowSteps" :key="step" :class="{ active: index <= quoteWorkflowActiveStep }">
            <span>{{ index + 1 }}</span>
            <strong>{{ step }}</strong>
          </div>
        </div>

        <el-radio-group v-model="pricingWorkspaceView" class="workspace-view-tabs">
          <el-radio-button v-for="item in pricingWorkspaceViews" :key="item.value" :value="item.value">
            {{ item.label }}
          </el-radio-button>
        </el-radio-group>

        <div v-if="draftQuoteJob" class="draft-quote-job-card">
          <div class="draft-quote-job-head">
            <div>
              <strong>报价生成进度</strong>
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
        <el-empty v-else-if="!draft" description="尚未创建报价草稿" />
        <template v-else>
          <div class="quote-workbench-hero">
            <div class="quote-workbench-total">
              <span>当前报价合计</span>
              <strong>¥ {{ formatMoney(draftTotals.quote_amount ?? draftTotals.tax_included_total ?? draft.priced_subtotal ?? 0) }}</strong>
              <small>账户定额 → 企业定额 → AI估价</small>
            </div>
            <div class="quote-workbench-kpis">
              <div><span>施工项目</span><strong>{{ draftSummaryCount('line_count', 'row_count', 'standard_item_count') }}</strong></div>
              <div class="source-kpi">
                <span>账户定额</span>
                <strong>{{ draftAccountQuotaCount }}</strong>
                <small>占项目数 {{ draftSourcePercent(draftAccountQuotaCount) }}</small>
              </div>
              <div class="source-kpi">
                <span>企业定额</span>
                <strong>{{ draftEnterpriseQuotaCount }}</strong>
                <small>占项目数 {{ draftSourcePercent(draftEnterpriseQuotaCount) }}</small>
              </div>
              <div class="source-kpi">
                <span>AI估价</span>
                <strong>{{ draftSummaryCount('ai_estimate_count') }}</strong>
                <small>占项目数 {{ draftSourcePercent(draftSummaryCount('ai_estimate_count')) }}</small>
              </div>
              <div v-if="draftManualChangeCount > 0"><span>人工修改</span><strong>{{ draftManualChangeCount }}</strong></div>
              <div v-if="draftAttentionCount > 0" class="warning">
                <span>待处理</span>
                <strong>{{ draftAttentionCount }}</strong>
              </div>
            </div>
          </div>
          <div class="quote-stat-strip">
            <span class="quote-stat-title">统计信息</span>
            <div
              v-for="item in quoteStatCards"
              :key="item.key"
              class="quote-stat-card"
              :class="{ clickable: item.detailBucket }"
              :role="item.detailBucket ? 'button' : undefined"
              :tabindex="item.detailBucket ? 0 : undefined"
              :title="item.detailBucket ? `点击查看${item.label}明细` : undefined"
              @click="openResourceDetail(item)"
              @keyup.enter="openResourceDetail(item)"
              @keyup.space.prevent="openResourceDetail(item)"
            >
              <span>{{ item.label }}:</span>
              <strong>{{ formatMoney(item.value ?? 0) }}</strong>
            </div>
            <div class="quote-stat-actions">
              <el-button type="success" plain size="small" :loading="statisticsExporting" @click="openStatisticsExportDialog">导出统计</el-button>
              <el-button type="primary" plain size="small" @click="openProcurementStatistics">采购统计</el-button>
              <el-button text @click="totalsExpanded = !totalsExpanded">{{ totalsExpanded ? '收起' : '展开' }}</el-button>
            </div>
          </div>
          <div v-if="pricingWorkspaceView === 'summary' || totalsExpanded" class="quote-totals-panel">
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
            当前是可变草稿，不是正式计价结果；人工改价可同步为账户定额草稿，但不会立即重算当前草稿；{{ draftModeOf(draft) === 'enterprise_ai' ? '企业定额未匹配行保留现有 AI 估价结果。' : '账户定额仅匹配当前账号 active 条目；未匹配行保持空价。' }}
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
          <template v-else-if="['quick', 'professional'].includes(pricingWorkspaceView)">
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
          <el-table
            v-if="pricingWorkspaceView === 'quick'"
            v-loading="draftLinesLoading"
            :data="draftLines"
            :row-key="lineIdOf"
            class="users-table quote-line-table quick-review-table"
            max-height="620"
            empty-text="当前筛选条件下暂无草稿行"
          >
            <el-table-column label="施工项目" min-width="210" fixed="left">
              <template #default="{ row }">
                <div class="quick-item-cell">
                  <strong>{{ row.item_name || row.project_name || '—' }}</strong>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="项目特征" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">{{ row.spec || row.project_feature || '无项目特征' }}</template>
            </el-table-column>
            <el-table-column label="单位" width="82" align="center">
              <template #default="{ row }">{{ row.unit || '—' }}</template>
            </el-table-column>
            <el-table-column label="工程量" width="110" align="right">
              <template #default="{ row }"><strong class="quote-quantity">{{ formatQuantity(row.quantity ?? row.calculation_quantity) }}</strong></template>
            </el-table-column>
            <el-table-column label="不含税单价" width="125" align="right">
              <template #default="{ row }">{{ formatMoney(draftPreviewUnitPrice(row) ?? draftLineUnitPrice(row)) }}</template>
            </el-table-column>
            <el-table-column label="不含税合价" width="135" align="right">
              <template #default="{ row }">{{ formatMoney(draftPreviewLineTotal(row) ?? lineTotalCost(row)) }}</template>
            </el-table-column>
            <el-table-column label="报价来源" min-width="160">
              <template #default="{ row }">
                <div class="draft-status-stack">
                  <el-tag
                    class="source-basis-tag"
                    size="small"
                    effect="plain"
                    role="button"
                    tabindex="0"
                    title="点击查看成本依据"
                    @click="openCostBasis(row)"
                    @keyup.enter="openCostBasis(row)"
                  >
                    {{ draftPriceSourceLabel(row) }}
                  </el-tag>
                  <small>{{ draftPriceSourceMeta(row) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="风险" min-width="210">
              <template #default="{ row }">
                <div class="quick-risk-cell">
                  <el-tag :type="quickReviewTag(row)" effect="plain" size="small">{{ quickReviewLabel(row) }}</el-tag>
                  <small v-if="quickReviewReason(row)">{{ quickReviewReason(row) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag :type="pricingStatusTag(row.pricing_status)" size="small" effect="plain">{{ pricingStatusLabel(row.pricing_status) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-table
            v-else-if="pricingWorkspaceView === 'professional'"
            ref="professionalTableRef"
            v-loading="draftLinesLoading"
            :data="draftLines"
            :row-key="lineIdOf"
            class="users-table quote-line-table professional-fields-table"
            max-height="620"
            empty-text="当前筛选条件下暂无草稿行"
            @row-click="toggleProfessionalQuotaRow"
          >
            <el-table-column type="expand" width="52" fixed="left" class-name="project-quota-expand-cell">
              <template #expand="{ expanded }">
                <span class="project-quota-expand-symbol" aria-hidden="true">{{ expanded ? '−' : '+' }}</span>
              </template>
              <template #default="{ row }">
                <div class="project-quota-relation" @click.stop>
                  <span class="project-quota-relation-branch" aria-hidden="true"></span>
                  <el-table
                    :data="[projectQuotaMainItem(row)]"
                    :show-header="false"
                    class="matched-quota-aligned-table"
                    :row-class-name="() => projectQuotaRowClass(row)"
                    @row-click="selectProjectQuota(row)"
                  >
                    <el-table-column
                      v-for="column in projectQuotaMainColumns"
                      :key="column.key"
                      :width="column.width"
                      :min-width="column.minWidth"
                      :align="column.align || 'left'"
                    >
                      <template #default="{ row: quotaRow }">{{ projectQuotaMainFieldValue(quotaRow, column) }}</template>
                    </el-table-column>
                  </el-table>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="序号" width="72" fixed="left" align="center">
              <template #default="{ $index }">{{ draftRowSequence($index) }}</template>
            </el-table-column>
            <el-table-column label="名称" width="190" fixed="left"><template #default="{ row }">{{ row.item_name || row.project_name || '—' }}</template></el-table-column>
            <el-table-column label="项目特征" min-width="260" show-overflow-tooltip><template #default="{ row }">{{ row.spec || row.project_feature || '无项目特征' }}</template></el-table-column>
            <el-table-column label="单位" width="80"><template #default="{ row }">{{ row.unit || '—' }}</template></el-table-column>
            <el-table-column label="工程量" width="120" align="right"><template #default="{ row }"><strong class="quote-quantity">{{ formatQuantity(row.quantity ?? row.calculation_quantity) }}</strong></template></el-table-column>
            <el-table-column label="不含税综合单价" width="135" align="right"><template #default="{ row }"><strong>{{ formatMoney(draftPreviewUnitPrice(row) ?? quoteUnitPrice(row)) }}</strong></template></el-table-column>
            <el-table-column label="不含税综合合价" width="145" align="right"><template #default="{ row }">{{ formatMoney(draftPreviewLineTotal(row) ?? lineTotalCost(row)) }}</template></el-table-column>
            <el-table-column label="人工费" width="110" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'labor')) }}</template></el-table-column>
            <el-table-column label="主材费" width="110" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'main_material')) }}</template></el-table-column>
            <el-table-column label="辅材费" width="110" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'auxiliary_material')) }}</template></el-table-column>
            <el-table-column label="机械费" width="110" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'machinery')) }}</template></el-table-column>
            <el-table-column label="措施费" width="110" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'measure')) }}</template></el-table-column>
            <el-table-column label="管理费" width="110" align="right"><template #default="{ row }">{{ formatMoney(feeUnitValue(row, 'management')) }}</template></el-table-column>
            <el-table-column label="税费" width="110" align="right"><template #default="{ row }">{{ formatMoney(draftPreviewTaxAmount(row) ?? taxAmount(row) ?? 0) }}</template></el-table-column>
          </el-table>
          <el-pagination v-if="draftLineTotal > draftLinePageSize" v-model:current-page="draftLinePage" :page-size="draftLinePageSize" :total="draftLineTotal" layout="total, prev, pager, next" @current-change="loadDraftLines" />
          </template>
        </template>
      </div>

      <template v-if="pricingWorkspaceView === 'versions'">
        <div class="budget-title version-history-heading">
          <div>
            <strong>版本记录</strong>
            <small>每次只显示一个版本，可切换后归档或启用。</small>
          </div>
        </div>
        <el-skeleton v-if="loading" :rows="2" animated />
        <el-empty v-else-if="!runs.length" description="暂无报价版本" />
        <div v-else class="pricing-version-card">
          <label>
            <span>版本号</span>
            <el-select v-model="selectedRunId" placeholder="选择版本">
              <el-option
                v-for="run in runs"
                :key="runIdOf(run)"
                :label="pricingRunVersionLabel(run)"
                :value="runIdOf(run)"
              />
            </el-select>
          </label>
          <el-tag :type="pricingRunStatusTag(selectedRun)" effect="plain">
            {{ pricingRunStatusLabel(selectedRun) }}
          </el-tag>
          <div class="pricing-version-actions">
            <el-button
              type="warning"
              plain
              :loading="versionArchiving"
              :disabled="!selectedRun || pricingRunArchived(selectedRun)"
              @click="archiveSelectedPricingRun"
            >
              归档
            </el-button>
            <el-button
              type="primary"
              :loading="versionActivating"
              :disabled="!selectedRun || pricingRunActive(selectedRun) || !pricingRunHasSnapshot(selectedRun)"
              @click="activateSelectedPricingRun"
            >
              启用
            </el-button>
          </div>
        </div>
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

  <el-drawer
    v-model="constructionNoteDrawer.visible"
    size="min(560px, 94vw)"
    title="施工提示"
    append-to-body
    destroy-on-close
  >
    <template v-if="constructionNoteDrawer.row">
      <div class="construction-note-drawer">
        <div class="construction-note-hero">
          <strong>{{ constructionNoteDrawer.row.item_name || constructionNoteDrawer.row.project_name || '未命名施工项目' }}</strong>
        </div>

        <section class="construction-note-section">
          <div class="construction-note-section-title">
            <strong>工艺做法与施工避坑</strong>
            <span>这里保留预审单的完整备注，导出时仍写入“工艺与避坑备注”列。</span>
          </div>
          <el-input
            :model-value="draftBreakdownInputValue(constructionNoteDrawer.row, 'remark')"
            type="textarea"
            :autosize="{ minRows: 8, maxRows: 16 }"
            maxlength="2000"
            show-word-limit
            placeholder="例如：施工步骤、材料要求、成品保护、交叉作业风险及容易漏报的内容"
            :disabled="!canManageDraft || draftLineSaving[lineIdOf(constructionNoteDrawer.row)]"
            @update:model-value="setDraftBreakdownInput(constructionNoteDrawer.row, 'remark', $event)"
          />
        </section>
      </div>
    </template>
    <template #footer>
      <div class="construction-note-footer">
        <el-button @click="constructionNoteDrawer.visible = false">关闭</el-button>
        <el-button
          type="primary"
          :loading="constructionNoteDrawer.row && draftLineSaving[lineIdOf(constructionNoteDrawer.row)]"
          :disabled="!canManageDraft || !constructionNoteDrawer.row"
          @click="saveConstructionNote"
        >
          保存施工提示
        </el-button>
      </div>
    </template>
  </el-drawer>

  <el-dialog
    v-model="statisticsExportDialog.visible"
    title="导出统计"
    width="min(560px, 92vw)"
    destroy-on-close
  >
    <div class="statistics-export-dialog">
      <p>请选择需要导出的内容，可单选或多选。</p>
      <el-checkbox-group v-model="statisticsExportDialog.sections" class="statistics-export-options">
        <el-checkbox
          v-for="item in statisticsExportOptions"
          :key="item.value"
          :value="item.value"
          class="statistics-export-option"
        >
          <strong>{{ item.label }}</strong>
          <small>{{ item.description }}</small>
        </el-checkbox>
      </el-checkbox-group>
    </div>
    <template #footer>
      <el-button @click="statisticsExportDialog.visible = false">取消</el-button>
      <el-button
        type="primary"
        :loading="statisticsExporting"
        :disabled="statisticsExportDialog.sections.length === 0"
        @click="confirmStatisticsExport"
      >
        导出
      </el-button>
    </template>
  </el-dialog>

  <el-drawer
    v-model="resourceDetailDrawer.visible"
    size="98vw"
    :title="resourceDetailDrawerTitle"
    append-to-body
    destroy-on-close
  >
    <el-skeleton v-if="resourceDetailDrawer.loading" :rows="10" animated />
    <template v-else>
      <div class="resource-detail-summary">
        <div>
          <span>明细行数</span>
          <strong>{{ resourceDetailDrawer.rowCount }}</strong>
        </div>
        <div>
          <span>明细合计</span>
          <strong>{{ formatMoney(resourceDetailDrawer.totalAmount) }}</strong>
        </div>
        <small v-if="resourceDetailDrawer.derivedRowCount > 0">
          其中 {{ resourceDetailDrawer.derivedRowCount }} 行为非定额报价拆分或定额资源与当前报价的费用差额。
        </small>
        <el-button
          class="resource-detail-export"
          type="primary"
          plain
          :loading="statisticsExporting"
          @click="exportCurrentResourceDetail"
        >
          导出
        </el-button>
      </div>

      <el-table
        v-if="resourceDetailDrawer.bucket === 'labor'"
        :data="resourceDetailDrawer.rows"
        border
        stripe
        max-height="calc(100vh - 185px)"
        class="resource-detail-table"
        empty-text="暂无人工明细"
      >
        <el-table-column prop="resource_code" label="编码" min-width="100" />
        <el-table-column prop="resource_type" label="类型" min-width="75" />
        <el-table-column prop="resource_name" label="项目名称" min-width="150" show-overflow-tooltip />
        <el-table-column prop="work_content" label="工作内容" min-width="180" show-overflow-tooltip />
        <el-table-column prop="calculation_rule" label="计算规则" min-width="150" show-overflow-tooltip />
        <el-table-column prop="unit" label="单位" width="70" />
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">{{ formatQuantity(row.quantity) }}</template>
        </el-table-column>
        <el-table-column label="不含税人工单价" width="120" align="right">
          <template #default="{ row }">{{ formatMoney(row.price) }}</template>
        </el-table-column>
        <el-table-column label="人工总价" width="115" align="right">
          <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
        </el-table-column>
      </el-table>

      <el-table
        v-else
        :data="resourceDetailDrawer.rows"
        border
        stripe
        max-height="calc(100vh - 185px)"
        class="resource-detail-table"
        empty-text="暂无材料明细"
      >
        <el-table-column prop="category" label="分类" min-width="95" />
        <el-table-column prop="resource_code" label="材料编码" min-width="105" />
        <el-table-column prop="resource_type" label="类型" min-width="70" />
        <el-table-column prop="resource_name" label="材料名称" min-width="140" show-overflow-tooltip />
        <el-table-column prop="specification" label="规格" min-width="120" show-overflow-tooltip />
        <el-table-column prop="brand" label="品牌" min-width="110" show-overflow-tooltip />
        <el-table-column prop="unit" label="单位" width="65" />
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">{{ formatQuantity(row.quantity) }}</template>
        </el-table-column>
        <el-table-column label="除税单价" width="105" align="right">
          <template #default="{ row }">{{ formatMoney(row.price) }}</template>
        </el-table-column>
        <el-table-column label="总价" width="105" align="right">
          <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
        </el-table-column>
      </el-table>
    </template>
  </el-drawer>

  <el-drawer
    v-model="procurementDrawer.visible"
    size="98vw"
    title="采购与用工统计"
    append-to-body
    destroy-on-close
  >
    <el-skeleton v-if="procurementDrawer.loading" :rows="11" animated />
    <template v-else>
      <div class="procurement-summary">
        <div>
          <span>材料种类</span>
          <strong>{{ procurementDrawer.materialKindCount }}</strong>
        </div>
        <div>
          <span>工种数量</span>
          <strong>{{ procurementDrawer.laborTradeCount }}</strong>
        </div>
        <div :class="{ warning: procurementDrawer.unresolvedLineCount > 0 }">
          <span>待补资源项目</span>
          <strong>{{ procurementDrawer.unresolvedLineCount }}</strong>
        </div>
      </div>

      <el-alert
        v-if="procurementDrawer.unresolvedLineCount > 0"
        type="warning"
        show-icon
        :closable="false"
        title="部分报价项目没有可核验的人工或材料组成"
        description="这些项目不会被虚构为采购量，已放入“待补资源”标签页，补齐定额组成后会自动进入汇总。"
      />

      <el-tabs v-model="procurementDrawer.activeTab" class="procurement-tabs">
        <el-tab-pane :label="`材料采购（${procurementDrawer.materialKindCount}种）`" name="materials">
          <div class="procurement-unit-summary">
            <span>按单位汇总：</span>
            <el-tag
              v-for="item in procurementDrawer.materialUnitTotals"
              :key="`material-${item.unit || 'none'}`"
              effect="plain"
            >
              {{ item.quantity }} {{ item.unit || '无单位' }}
            </el-tag>
          </div>
          <el-table
            :data="procurementDrawer.materialRows"
            border
            stripe
            max-height="calc(100vh - 245px)"
            class="resource-detail-table"
            empty-text="暂无可核验的材料采购明细"
          >
            <el-table-column prop="category" label="分类" min-width="95" />
            <el-table-column prop="resource_code" label="材料编码" min-width="105" />
            <el-table-column prop="resource_type" label="类型" min-width="70" />
            <el-table-column prop="resource_name" label="材料名称" min-width="140" show-overflow-tooltip />
            <el-table-column prop="specification" label="规格" min-width="120" show-overflow-tooltip />
            <el-table-column prop="brand" label="品牌" min-width="110" show-overflow-tooltip />
            <el-table-column prop="unit" label="单位" width="65" />
            <el-table-column label="采购数量" width="100" align="right">
              <template #default="{ row }">{{ formatQuantity(row.quantity) }}</template>
            </el-table-column>
            <el-table-column label="除税单价" width="105" align="right">
              <template #default="{ row }">{{ formatMoney(row.price) }}</template>
            </el-table-column>
            <el-table-column label="总价" width="105" align="right">
              <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`人工工种（${procurementDrawer.laborTradeCount}种）`" name="labor">
          <div class="procurement-unit-summary">
            <span>按单位汇总：</span>
            <el-tag
              v-for="item in procurementDrawer.laborUnitTotals"
              :key="`labor-${item.unit || 'none'}`"
              effect="plain"
            >
              {{ item.quantity }} {{ item.unit || '无单位' }}
            </el-tag>
          </div>
          <el-table
            :data="procurementDrawer.laborRows"
            border
            stripe
            max-height="calc(100vh - 245px)"
            class="resource-detail-table"
            empty-text="暂无可核验的人工工种明细"
          >
            <el-table-column prop="resource_code" label="编码" min-width="100" />
            <el-table-column prop="resource_type" label="类型" min-width="75" />
            <el-table-column prop="resource_name" label="工种/项目名称" min-width="150" show-overflow-tooltip />
            <el-table-column prop="work_content" label="工作内容" min-width="180" show-overflow-tooltip />
            <el-table-column prop="calculation_rule" label="计算规则" min-width="150" show-overflow-tooltip />
            <el-table-column prop="unit" label="单位" width="70" />
            <el-table-column label="用工数量" width="100" align="right">
              <template #default="{ row }">{{ formatQuantity(row.quantity) }}</template>
            </el-table-column>
            <el-table-column label="不含税人工单价" width="120" align="right">
              <template #default="{ row }">{{ formatMoney(row.price) }}</template>
            </el-table-column>
            <el-table-column label="人工总价" width="115" align="right">
              <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`待补资源（${procurementDrawer.unresolvedLineCount}项）`" name="unresolved">
          <el-table
            :data="procurementDrawer.unresolvedRows"
            border
            stripe
            max-height="calc(100vh - 215px)"
            empty-text="所有项目均已有可核验的人工与材料组成"
          >
            <el-table-column prop="item_name" label="报价项目" min-width="190" show-overflow-tooltip />
            <el-table-column prop="specification" label="项目特征" min-width="230" show-overflow-tooltip />
            <el-table-column prop="unit" label="清单单位" width="100" />
            <el-table-column label="清单数量" width="120" align="right">
              <template #default="{ row }">{{ formatQuantity(row.quantity) }}</template>
            </el-table-column>
            <el-table-column label="缺少组成" width="130">
              <template #default="{ row }"><el-tag type="warning" effect="plain">{{ row.missing_kinds_text }}</el-tag></template>
            </el-table-column>
            <el-table-column label="报价来源" width="130">
              <template #default="{ row }">{{ draftPriceSourceLabel(row) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-drawer>

  <el-drawer
    v-model="costBasisDrawer.visible"
    size="min(640px, 94vw)"
    :title="costBasisDrawerTitle"
    append-to-body
    destroy-on-close
  >
    <el-skeleton v-if="costBasisDrawer.loading" :rows="9" animated />
    <template v-else-if="costBasisDrawer.row">
      <div class="cost-basis-drawer">
        <div class="cost-basis-hero">
          <div>
            <span>当前报价项目</span>
            <strong>{{ costBasisDrawer.row.item_name || costBasisDrawer.row.project_name || '未命名项目' }}</strong>
            <small>{{ costBasisDrawer.row.spec || costBasisDrawer.row.project_feature || '无项目特征' }}</small>
          </div>
          <el-tag effect="plain">{{ draftPriceSourceLabel(costBasisDrawer.row) }}</el-tag>
        </div>

        <el-alert
          v-if="costBasisDrawer.error"
          type="warning"
          show-icon
          :closable="false"
          title="完整条目读取失败，当前显示报价时保存的来源快照"
          :description="costBasisDrawer.error"
        />

        <template v-if="costBasisDrawer.type === 'enterprise_quota'">
          <section class="cost-basis-section">
            <div class="construction-note-section-title">
              <strong>匹配到的企业定额项目</strong>
              <span>展示企业定额主库中的对应主项及本次报价采用的价格。</span>
            </div>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="定额编码">{{ costBasisItemValue('quota_code') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="项目名称">{{ costBasisItemValue('item_name', 'name') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="工作内容">{{ costBasisItemValue('work_content', 'spec') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="规格/特征">{{ costBasisItemValue('specification', 'item_features') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="单位">{{ costBasisItemValue('unit') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="定额单价">{{ formatMoney(costBasisItemValue('unit_price', 'price')) }}</el-descriptions-item>
              <el-descriptions-item label="所属分部">{{ costBasisItemValue('section_name') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="企业定额版本">{{ costBasisEnterpriseVersionLabel }}</el-descriptions-item>
              <el-descriptions-item label="Excel来源">{{ costBasisSourceLocation }}</el-descriptions-item>
            </el-descriptions>
          </section>

          <section class="cost-basis-section">
            <div class="construction-note-section-title">
              <strong>单价组成</strong>
              <span>用于核对本条企业定额的人工、材料及机械费用。</span>
            </div>
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="人工费">{{ formatMoney(costBasisItemValue('labor_fee', 'labor_unit_cost')) }}</el-descriptions-item>
              <el-descriptions-item label="主材费">{{ formatMoney(costBasisItemValue('main_material_fee', 'main_material_unit_cost')) }}</el-descriptions-item>
              <el-descriptions-item label="辅材费">{{ formatMoney(costBasisItemValue('auxiliary_material_fee', 'auxiliary_material_unit_cost')) }}</el-descriptions-item>
              <el-descriptions-item label="机械费">{{ formatMoney(costBasisItemValue('machinery_fee', 'machinery_unit_cost')) }}</el-descriptions-item>
            </el-descriptions>
          </section>

          <section v-if="costBasisComponents.length" class="cost-basis-section">
            <div class="construction-note-section-title">
              <strong>定额组成明细</strong>
              <span>共 {{ costBasisComponents.length }} 条人工、材料或机械组成。</span>
            </div>
            <el-table :data="costBasisComponents" class="users-table" max-height="300" empty-text="暂无组成明细">
              <el-table-column label="类型" width="90"><template #default="{ row }">{{ row.component_type || row.fee_bucket || '—' }}</template></el-table-column>
              <el-table-column label="资源编码" width="120"><template #default="{ row }">{{ row.resource_code || '—' }}</template></el-table-column>
              <el-table-column label="名称" min-width="170"><template #default="{ row }">{{ row.resource_name || row.worker_or_subtype || '—' }}</template></el-table-column>
              <el-table-column label="数量" width="90" align="right"><template #default="{ row }">{{ formatQuantity(row.quantity) }}</template></el-table-column>
              <el-table-column label="单价" width="105" align="right"><template #default="{ row }">{{ formatMoney(row.unit_price) }}</template></el-table-column>
              <el-table-column label="合价" width="105" align="right"><template #default="{ row }">{{ formatMoney(row.amount) }}</template></el-table-column>
            </el-table>
          </section>
        </template>

        <template v-else-if="costBasisDrawer.type === 'account_quota'">
          <section class="cost-basis-section">
            <div class="construction-note-section-title">
              <strong>匹配到的账户定额项目</strong>
              <span>展示当前账户已启用并被本行采用的定额条目。</span>
            </div>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="定额编码">{{ costBasisItemValue('quota_code') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="项目名称">{{ costBasisItemValue('item_name', 'name') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="项目特征">{{ costBasisItemValue('item_features', 'spec') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="单位">{{ costBasisItemValue('unit') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="账户定额单价">{{ formatMoney(costBasisItemValue('unit_price', 'price')) }}</el-descriptions-item>
              <el-descriptions-item label="状态">{{ costBasisItemValue('status') || '—' }}</el-descriptions-item>
              <el-descriptions-item label="修订版本">R{{ costBasisItemValue('revision') || 1 }}</el-descriptions-item>
            </el-descriptions>
          </section>
        </template>

        <template v-else>
          <section class="cost-basis-section">
            <div class="construction-note-section-title">
              <strong>AI 估价依据</strong>
              <span>本行没有命中企业定额或账户定额，因此没有可索引的定额条目。</span>
            </div>
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="系统建议价">{{ formatMoney(draftSuggestedUnitPrice(costBasisDrawer.row)) }}</el-descriptions-item>
              <el-descriptions-item label="可信度">{{ costBasisAiConfidence }}</el-descriptions-item>
              <el-descriptions-item label="估价说明">{{ costBasisAiBasis }}</el-descriptions-item>
            </el-descriptions>
          </section>
        </template>
      </div>
    </template>
    <template #footer>
      <div class="cost-basis-footer">
        <el-button @click="costBasisDrawer.visible = false">关闭</el-button>
        <el-button
          v-if="costBasisDrawer.type === 'enterprise_quota'"
          type="primary"
          :disabled="!costBasisDrawer.activeEnterpriseItemId || !costBasisDrawer.activeEnterpriseVersionId"
          @click="openEnterpriseQuotaLibraryItem"
        >
          在当前生效企业定额库中查看
        </el-button>
      </div>
    </template>
  </el-drawer>

  <section
    v-if="pricingAvailable && canViewPricing && pricingWorkspaceView === 'professional'"
    class="budget-panel project-quota-resource-workbench"
    aria-labelledby="project-quota-resource-title"
  >
    <div class="budget-title project-quota-workbench-heading">
      <div>
        <strong id="project-quota-resource-title">工料机明细</strong>
        <small v-if="!projectQuotaWorkbench.row">请先在上方“专业全字段”中展开项目，再点击定额项的任意位置。</small>
      </div>
      <el-tag v-if="projectQuotaWorkbench.snapshot" type="primary" effect="plain">
        定额编码：{{ projectQuotaWorkbench.snapshot.quota?.quota_code || '项目补充定额' }}
      </el-tag>
    </div>
    <el-skeleton v-if="projectQuotaWorkbench.loading" :rows="10" animated />
    <el-empty v-else-if="!draft" description="创建报价草稿后，可在这里查看定额工料机明细" />
    <el-empty v-else-if="!projectQuotaWorkbench.snapshot" description="点击上方定额项后，在这里显示对应工料机明细" />
    <template v-else>
      <div class="project-quota-summary">
        <div><span>项目定额单价</span><strong>¥ {{ formatMoney(projectQuotaWorkbench.snapshot.quota?.unit_price) }}</strong></div>
        <div><span>人工</span><strong>{{ formatMoney(projectQuotaWorkbench.snapshot.quota?.labor_fee) }}</strong></div>
        <div><span>主材</span><strong>{{ formatMoney(projectQuotaWorkbench.snapshot.quota?.main_material_fee) }}</strong></div>
        <div><span>辅材</span><strong>{{ formatMoney(projectQuotaWorkbench.snapshot.quota?.auxiliary_material_fee) }}</strong></div>
        <div><span>机械</span><strong>{{ formatMoney(projectQuotaWorkbench.snapshot.quota?.machinery_fee) }}</strong></div>
      </div>

      <div class="budget-title project-quota-resource-heading">
        <div>
          <strong>明细列表</strong>
          <small>点击一行可编辑全部业务字段；含量、单价或金额变化后，项目定额和报价草稿会同步重算。</small>
        </div>
        <el-button
          type="primary"
          plain
          :disabled="!projectQuotaCanEdit"
          @click="startCreateProjectQuotaResource"
        >
          新增工料机
        </el-button>
      </div>
      <el-table
        :data="projectQuotaWorkbench.snapshot.resources || []"
        class="users-table project-quota-resource-table"
        max-height="330"
        empty-text="暂无工料机明细"
        highlight-current-row
        @row-click="startEditProjectQuotaResource"
      >
        <el-table-column prop="fee_bucket_label" label="费用类别" width="100" />
        <el-table-column prop="resource_code" label="编码" width="120" />
        <el-table-column prop="component_type" label="类型" width="100" />
        <el-table-column prop="resource_name" label="名称" min-width="190" />
        <el-table-column prop="specification" label="规格" min-width="150" show-overflow-tooltip />
        <el-table-column prop="brand" label="品牌" width="120" show-overflow-tooltip />
        <el-table-column prop="unit" label="单位" width="80" />
        <el-table-column label="含量" width="115" align="right"><template #default="{ row }">{{ formatQuantity(row.quantity) }}</template></el-table-column>
        <el-table-column label="单价" width="120" align="right"><template #default="{ row }">{{ formatMoney(row.unit_price) }}</template></el-table-column>
        <el-table-column label="金额" width="120" align="right"><template #default="{ row }"><strong>{{ formatMoney(row.amount) }}</strong></template></el-table-column>
        <el-table-column label="操作" width="95" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click.stop="startEditProjectQuotaResource(row)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>

      <section v-if="projectQuotaEditor.visible" class="project-quota-editor">
        <div class="budget-title">
          <div>
            <strong>{{ projectQuotaEditor.mode === 'create' ? '新增工料机' : '编辑工料机' }}</strong>
            <small>来源主键、创建人和审计字段由系统维护；以下业务字段均可修改。</small>
          </div>
          <el-button text @click="projectQuotaEditor.visible = false">收起编辑区</el-button>
        </div>
        <el-form label-position="top" class="project-quota-form">
          <el-form-item label="费用类别">
            <el-select v-model="projectQuotaEditor.form.fee_bucket">
              <el-option label="人工" value="labor" />
              <el-option label="主材" value="main_material" />
              <el-option label="辅材" value="auxiliary_material" />
              <el-option label="机械" value="machinery" />
            </el-select>
          </el-form-item>
          <el-form-item label="价格库类型">
            <el-select v-model="projectQuotaEditor.form.library_kind" clearable>
              <el-option label="人工价格库" value="labor" />
              <el-option label="材料价格库" value="material" />
            </el-select>
          </el-form-item>
          <el-form-item label="工料机编码"><el-input v-model="projectQuotaEditor.form.resource_code" clearable /></el-form-item>
          <el-form-item label="工料机类型"><el-input v-model="projectQuotaEditor.form.component_type" clearable /></el-form-item>
          <el-form-item label="工料机名称" class="span-2"><el-input v-model="projectQuotaEditor.form.resource_name" clearable /></el-form-item>
          <el-form-item label="工种/子类型"><el-input v-model="projectQuotaEditor.form.worker_or_subtype" clearable /></el-form-item>
          <el-form-item label="材料类别"><el-input v-model="projectQuotaEditor.form.category" clearable /></el-form-item>
          <el-form-item label="规格"><el-input v-model="projectQuotaEditor.form.specification" clearable /></el-form-item>
          <el-form-item label="品牌"><el-input v-model="projectQuotaEditor.form.brand" clearable /></el-form-item>
          <el-form-item label="单位"><el-input v-model="projectQuotaEditor.form.unit" clearable /></el-form-item>
          <el-form-item label="含量">
            <el-input-number v-model="projectQuotaEditor.form.quantity" :min="0" :precision="6" :controls="false" @change="recalculateProjectQuotaEditorAmount" />
          </el-form-item>
          <el-form-item label="不含税单价">
            <el-input-number v-model="projectQuotaEditor.form.unit_price" :min="0" :precision="6" :controls="false" @change="recalculateProjectQuotaEditorAmount" />
          </el-form-item>
          <el-form-item label="金额">
            <el-input-number v-model="projectQuotaEditor.form.amount" :min="0" :precision="6" :controls="false" />
          </el-form-item>
          <el-form-item label="税率(%)">
            <el-input-number v-model="projectQuotaEditor.form.tax_rate" :min="0" :precision="6" :controls="false" />
          </el-form-item>
          <el-form-item label="工作内容" class="span-2"><el-input v-model="projectQuotaEditor.form.work_content" type="textarea" :rows="2" /></el-form-item>
          <el-form-item label="计算规则" class="span-2"><el-input v-model="projectQuotaEditor.form.calculation_rule" type="textarea" :rows="2" /></el-form-item>
        </el-form>
        <div class="project-quota-editor-tools">
          <el-button plain @click="recalculateProjectQuotaEditorAmount">按含量 × 单价重算金额</el-button>
          <el-input v-model="projectQuotaEditor.reason" maxlength="500" show-word-limit placeholder="本次修改说明（同步企业定额时至少 4 个字符）" />
        </div>
        <div class="project-quota-enterprise-option">
          <el-checkbox
            v-model="projectQuotaEditor.syncToEnterprise"
            :disabled="!projectQuotaEnterpriseEligible"
            @change="guardProjectQuotaEnterpriseSync"
          >
            保存后同步到企业定额库
          </el-checkbox>
          <span v-if="!projectQuotaEnterpriseEligible">当前定额不是由企业定额匹配形成，不能直接回写。</span>
          <span v-else-if="!projectQuotaCanSyncEnterprise">当前账号可以编辑项目工料机，但没有企业定额同步权限。</span>
          <span v-else>同步会写入企业定额草稿版本，仍需在企业定额版本中心审核启用。</span>
        </div>
        <div class="project-quota-editor-actions">
          <el-button
            v-if="projectQuotaEditor.mode === 'edit'"
            type="danger"
            plain
            :loading="projectQuotaEditor.deleting"
            :disabled="!projectQuotaCanEdit"
            @click="deleteProjectQuotaResourceRow"
          >
            删除工料机
          </el-button>
          <span />
          <el-button @click="projectQuotaEditor.visible = false">取消</el-button>
          <el-button
            type="primary"
            :loading="projectQuotaEditor.saving"
            :disabled="!projectQuotaCanEdit"
            @click="saveProjectQuotaResource"
          >
            保存并重算项目定额
          </el-button>
        </div>
      </section>

      <div class="project-quota-sync-status">
        <div>
          <strong>企业定额同步状态</strong>
          <span v-if="projectQuotaWorkbench.snapshot.enterprise_sync?.target_version_id">
            最近已同步到企业定额草稿版本 #{{ projectQuotaWorkbench.snapshot.enterprise_sync.target_version_id }}；当前 active 版本未被直接修改。
          </span>
          <span v-else>尚未同步，企业定额库保持不变。</span>
        </div>
        <el-button
          v-if="projectQuotaEnterpriseEligible"
          plain
          :loading="projectQuotaEditor.syncing"
          @click="syncCurrentProjectQuotaToEnterprise"
        >
          同步当前项目定额
        </el-button>
      </div>
    </template>
  </section>

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
const emit = defineEmits(['version-activated'])

const loading = ref(false)
const creating = ref(false)
const versionActivating = ref(false)
const versionArchiving = ref(false)
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
const originalExporting = ref(false)
const statisticsExporting = ref(false)
const statisticsExportOptions = [
  { value: 'summary', label: '统计汇总', description: '报价费用汇总与合计' },
  { value: 'main_material', label: '主材明细', description: '主材编码、规格、数量与价格' },
  { value: 'auxiliary_material', label: '辅材明细', description: '辅材编码、规格、数量与价格' },
  { value: 'labor', label: '人工明细', description: '人工项目、用工数量与价格' },
]
const statisticsExportDialog = reactive({
  visible: false,
  sections: statisticsExportOptions.map((item) => item.value),
})
const draftLinesLoading = ref(false)
const draft = ref(null)
const selectedDraftMode = ref('enterprise_ai')
const pricingWorkspaceView = ref('quick')
const professionalTableRef = ref(null)
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
const constructionNoteDrawer = reactive({ visible: false, row: null })
const resourceDetailDrawer = reactive({
  visible: false,
  loading: false,
  bucket: '',
  rows: [],
  rowCount: 0,
  totalAmount: 0,
  derivedRowCount: 0,
})
const procurementDrawer = reactive({
  visible: false,
  loading: false,
  activeTab: 'materials',
  materialRows: [],
  laborRows: [],
  unresolvedRows: [],
  materialUnitTotals: [],
  laborUnitTotals: [],
  materialKindCount: 0,
  laborTradeCount: 0,
  unresolvedLineCount: 0,
})
const costBasisDrawer = reactive({
  visible: false,
  loading: false,
  row: null,
  type: 'ai_estimate',
  itemId: null,
  activeEnterpriseItemId: null,
  activeEnterpriseVersionId: null,
  item: null,
  error: '',
})
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
const projectQuotaWorkbench = reactive({
  loading: false,
  row: null,
  snapshot: null,
})
const projectQuotaEditor = reactive({
  visible: false,
  mode: 'create',
  saving: false,
  deleting: false,
  syncing: false,
  resource: null,
  form: emptyProjectQuotaResourceForm(),
  reason: '',
  syncToEnterprise: false,
})

const pricingWorkspaceViews = [
  { value: 'quick', label: '快速审核' },
  { value: 'professional', label: '专业全字段' },
  { value: 'summary', label: '费用汇总' },
  { value: 'versions', label: '版本记录' },
]
const quoteWorkflowSteps = ['提交需求', '标准清单', '自动计价', '人工复核', '费用汇总', '导出下发']

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
const projectQuotaCanEdit = computed(() => (
  canManageDraft.value
  && projectQuotaWorkbench.snapshot?.capabilities?.can_edit_resources === true
))
const projectQuotaEnterpriseEligible = computed(() => (
  projectQuotaWorkbench.snapshot?.enterprise_sync?.eligible === true
))
const projectQuotaCanSyncEnterprise = computed(() => (
  projectQuotaEnterpriseEligible.value
  && projectQuotaWorkbench.snapshot?.capabilities?.can_sync_enterprise === true
  && (
    projectCapability('can_sync_enterprise_quota')
    || readinessCapability('can_sync_enterprise_quota')
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
const draftTierCount = (summaryKeys, priceSources, selectedItemKey) => {
  for (const key of summaryKeys) {
    const value = draft.value?.[key] ?? draftSummary.value?.[key]
    if (value !== null && value !== undefined && value !== '') return Number(value) || 0
  }
  return draftLines.value.filter((row) => (
    priceSources.includes(row?.price_source || row?.pricing_source || row?.unit_price_source)
    || Boolean(row?.[selectedItemKey])
  )).length
}
const draftAccountQuotaCount = computed(() => draftTierCount(
  ['account_quota_matched_count', 'account_matched_count'],
  ['account_quota', 'account'],
  'selected_account_quota_item_id',
))
const draftEnterpriseQuotaCount = computed(() => draftTierCount(
  ['enterprise_quota_matched_count', 'enterprise_matched_count'],
  ['enterprise_quota', 'enterprise'],
  'selected_enterprise_quota_item_id',
))
const draftManualChangeCount = computed(() => draftSummaryCount('manual_price_count', 'manual_priced_count', 'manual_count'))
const draftAttentionCount = computed(() => {
  const explicit = draft.value?.attention_count ?? draftSummary.value?.attention_count
  if (explicit !== null && explicit !== undefined && explicit !== '') return Number(explicit) || 0
  return Math.max(
    draftSummaryCount('unmatched_count', 'pending_count', 'unpriced_count'),
    draftSummaryCount('quantity_unresolved_count'),
  )
})
const quoteWorkflowActiveStep = computed(() => {
  if (!draft.value) return 1
  if (draftQuoteJobRunning.value) return 2
  if (draftAttentionCount.value > 0) return 3
  if (Number(draftTotals.value.quote_amount || draftTotals.value.tax_included_total || 0) > 0) return 4
  return 3
})
const quoteStatCards = computed(() => [
  { key: 'main_material_total', label: '主材', value: draftTotals.value.main_material_total, detailBucket: 'main_material' },
  { key: 'auxiliary_material_total', label: '辅材', value: draftTotals.value.auxiliary_material_total, detailBucket: 'auxiliary_material' },
  { key: 'labor_total', label: '人工', value: draftTotals.value.labor_total, detailBucket: 'labor' },
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
const pricingRunVersionLabel = (run) => `R${Number(run?.run_number || 0) || runIdOf(run) || '—'}`
const pricingRunActive = (run) => Boolean(
  run
  && (
    run.is_active === true
    || Number(readiness.value?.current_pricing_run_id || props.project?.active_pricing_run_id || 0) === Number(runIdOf(run))
  )
)
const pricingRunArchived = (run) => run?.status === 'superseded'
const pricingRunHasSnapshot = (run) => run?.has_draft_snapshot === true
const pricingRunStatusLabel = (run) => {
  if (pricingRunActive(run)) return '已启用'
  if (!pricingRunHasSnapshot(run)) return '缺少完整快照'
  return ({
    ready: '待启用',
    confirmed: '待启用',
    superseded: '已归档',
    processing: '生成中',
    failed: '生成失败',
  })[run?.status] || run?.status || '未知'
}
const pricingRunStatusTag = (run) => {
  if (pricingRunActive(run)) return 'success'
  if (!pricingRunHasSnapshot(run)) return 'danger'
  return ({
    ready: 'primary',
    confirmed: 'primary',
    superseded: 'info',
    processing: 'warning',
    failed: 'danger',
  })[run?.status] || 'info'
}
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
const pricingOnlyNotePatterns = [
  /^(?:报价|价格|取价|计价)来源\s*[：:]/,
  /^按[“"]?账户定额.*企业定额.*AI\s*估价.*命中/,
  /账户定额与企业定额均未命中.*AI\s*估价/,
  /^(?:已使用|采用|使用)\s*AI\s*(?:估价|报价)/,
  /^未连接真实模型时的保守规则估价/,
  /^缺少真实模型推理依据/,
  /^未读取外部市场价或客户认可价格/,
]
const sanitizeConstructionNote = (value, pricingPhrases = []) => {
  const text = String(value || '').trim()
  if (!text || text === '—') return ''
  const clauseKey = (item) => String(item || '').replace(/\s+/g, '').replace(/^[。；;，,:：]+|[。；;，,:：]+$/g, '')
  const pricingClauseKeys = new Set(
    pricingPhrases
      .flatMap((item) => String(item || '').split(/[。；;\n]+/))
      .map(clauseKey)
      .filter(Boolean),
  )
  const clauses = text
    .split(/[。；;\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => !pricingOnlyNotePatterns.some((pattern) => pattern.test(item)))
    .filter((item) => !pricingClauseKeys.has(clauseKey(item)))
  return clauses.length ? `${clauses.join('；')}。` : ''
}
const constructionPricingPhrases = (row) => {
  const estimate = row?.ai_estimate?.estimate || row?.ai_estimate || {}
  const explanation = row?.quote_explanation || row?.pricing_explanation || {}
  const reference = row?.cost_reference || {}
  const snapshot = row?.pricing_source_snapshot || {}
  return [
    estimate?.basis,
    explanation?.ai_basis,
    explanation?.ai_price_source_reason,
    explanation?.cost_context_basis,
    reference?.ai_price_source_reason,
    reference?.price_source_reason,
    reference?.message,
    snapshot?.basis,
  ]
}
const constructionRemark = (row) => sanitizeConstructionNote(rowRemark(row), constructionPricingPhrases(row))
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
  if (key === 'remark') return sanitizeConstructionNote(value, constructionPricingPhrases(row))
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
const draftSuggestedUnitPrice = (row) => (
  row?.base_unit_price
  ?? row?.ai_estimated_unit_price
  ?? row?.quota_unit_price
  ?? row?.effective_unit_price
  ?? row?.unit_price
  ?? null
)
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
const costBasisDrawerTitle = computed(() => ({
  enterprise_quota: '企业定额成本依据',
  account_quota: '账户定额成本依据',
  ai_estimate: 'AI 估价依据',
})[costBasisDrawer.type] || '成本依据')
const costBasisItem = computed(() => costBasisDrawer.item || matchedQuota(costBasisDrawer.row) || {})
const costBasisComponents = computed(() => Array.isArray(costBasisItem.value?.components) ? costBasisItem.value.components : [])
const costBasisItemValue = (...keys) => firstValue([costBasisItem.value, costBasisDrawer.row?.pricing_breakdown], keys)
const costBasisEnterpriseVersionLabel = computed(() => {
  const version = costBasisItem.value?.active_version || {}
  return version.version_name || version.version_code || version.name || version.code || '报价时匹配版本'
})
const costBasisSourceLocation = computed(() => {
  const sheet = costBasisItemValue('source_sheet')
  const rowIndex = costBasisItemValue('source_row_index')
  if (!sheet && !rowIndex) return '—'
  return [sheet, rowIndex ? `第 ${rowIndex} 行` : ''].filter(Boolean).join(' · ')
})
const costBasisAiEstimate = computed(() => {
  const row = costBasisDrawer.row || {}
  const estimate = row?.ai_estimate?.estimate || row?.ai_estimate || row?.pricing_source_snapshot || {}
  return estimate && typeof estimate === 'object' ? estimate : {}
})
const costBasisAiBasis = computed(() => {
  const row = costBasisDrawer.row || {}
  const explanation = row?.quote_explanation || {}
  return costBasisAiEstimate.value?.basis || explanation?.ai_basis || explanation?.ai_price_source_reason || '本行由 AI 根据项目名称、特征、单位及工程量综合估价。'
})
const costBasisAiConfidence = computed(() => {
  const value = Number(costBasisAiEstimate.value?.confidence)
  if (!Number.isFinite(value)) return '需人工确认'
  return `${Math.round(Math.abs(value) <= 1 ? value * 100 : value)}%`
})
const positiveSourceId = (...values) => {
  for (const value of values) {
    const number = Number(value)
    if (Number.isInteger(number) && number > 0) return number
  }
  return null
}
const costBasisSource = (row) => {
  const source = matchedQuota(row) || {}
  const reference = row?.cost_reference || {}
  const evidence = row?.match_evidence || {}
  const evidenceReference = evidence?.cost_reference || {}
  const sourceKey = String(row?.price_source || row?.pricing_source || row?.unit_price_source || '').toLowerCase()
  const accountQuotaItemId = positiveSourceId(
    row?.selected_account_quota_item_id,
    row?.account_quota_item_id,
    reference?.account_quota_item_id,
    evidenceReference?.account_quota_item_id,
    source?.account_quota_item_id,
    source?.source_type === 'account_quota_item' ? source?.id : null,
    ['account', 'account_quota'].includes(sourceKey) ? source?.id : null,
  )
  if (accountQuotaItemId) {
    return { type: 'account_quota', itemId: accountQuotaItemId }
  }
  const enterpriseQuotaItemId = positiveSourceId(
    row?.selected_enterprise_quota_item_id,
    row?.enterprise_quota_item_id,
    reference?.enterprise_quota_item_id,
    evidenceReference?.enterprise_quota_item_id,
    source?.enterprise_quota_item_id,
    source?.source_type === 'enterprise_quota_item' ? source?.id : null,
    ['enterprise', 'enterprise_quota'].includes(sourceKey) ? source?.id : null,
  )
  if (enterpriseQuotaItemId) {
    return { type: 'enterprise_quota', itemId: enterpriseQuotaItemId }
  }
  if (['account', 'account_quota'].includes(sourceKey)) {
    return { type: 'account_quota', itemId: null }
  }
  if (['enterprise', 'enterprise_quota'].includes(sourceKey)) {
    return { type: 'enterprise_quota', itemId: null }
  }
  return { type: 'ai_estimate', itemId: null }
}
const openCostBasis = async (row) => {
  const basis = costBasisSource(row)
  costBasisDrawer.visible = true
  costBasisDrawer.loading = false
  costBasisDrawer.row = row
  costBasisDrawer.type = basis.type
  costBasisDrawer.itemId = basis.itemId
  costBasisDrawer.activeEnterpriseItemId = null
  costBasisDrawer.activeEnterpriseVersionId = null
  costBasisDrawer.item = matchedQuota(row) || null
  costBasisDrawer.error = ''
  if (basis.type === 'ai_estimate') return
  if (!basis.itemId) {
    costBasisDrawer.error = '当前报价记录缺少定额条目编号。'
    return
  }
  costBasisDrawer.loading = true
  try {
    const response = basis.type === 'account_quota'
      ? await budgetProjectApi.accountQuotaItemDetail(basis.itemId)
      : await budgetProjectApi.enterpriseQuotaItemDetail(basis.itemId)
    const item = budgetResponseData(response)
    costBasisDrawer.item = item || costBasisDrawer.item
    if (
      basis.type === 'enterprise_quota'
      && item?.active_version
      && positiveSourceId(item?.id)
    ) {
      costBasisDrawer.activeEnterpriseItemId = positiveSourceId(item.id)
      costBasisDrawer.activeEnterpriseVersionId = positiveSourceId(item.active_version.id)
    }
  } catch (error) {
    costBasisDrawer.error = basis.type === 'enterprise_quota' && error?.response?.status === 404
      ? '该报价引用的条目不属于当前生效的企业定额版本，已阻止跳转。'
      : budgetApiErrorMessage(error, '成本依据读取失败')
  } finally {
    costBasisDrawer.loading = false
  }
}
const openEnterpriseQuotaLibraryItem = () => {
  if (!costBasisDrawer.activeEnterpriseItemId || !costBasisDrawer.activeEnterpriseVersionId) {
    ElMessage.warning('当前条目不属于生效中的企业定额库，无法跳转')
    return
  }
  const url = new URL('/admin/cost-db', window.location.origin)
  url.searchParams.set('enterprise_quota_item_id', String(costBasisDrawer.activeEnterpriseItemId))
  url.searchParams.set('enterprise_quota_version', 'active')
  url.searchParams.set('enterprise_quota_version_id', String(costBasisDrawer.activeEnterpriseVersionId))
  const newPage = window.open('', '_blank')
  if (!newPage) {
    ElMessage.warning('浏览器阻止了新页面，请允许本站打开新标签页后重试')
    return
  }
  newPage.opener = null
  newPage.location.href = url.toString()
}
const quickReviewLabel = (row) => {
  const quantity = Number(row?.quantity ?? row?.calculation_quantity)
  if (!Number.isFinite(quantity) || quantity <= 0 || row?.pricing_status === 'quantity_unresolved') return '工程量'
  if (draftSuggestedUnitPrice(row) === null || row?.pricing_status === 'missing_unit_price' || row?.pricing_status === 'pending_match') return '待补价'
  if (['ai_estimate', 'llm'].includes(row?.price_source || row?.pricing_source || row?.unit_price_source)) return '需确认'
  if (['unit_conflict', 'numeric_overflow'].includes(row?.pricing_status)) return '需复核'
  return '正常'
}
const quickReviewTag = (row) => ({
  正常: 'success',
  需确认: 'warning',
  待补价: 'danger',
  工程量: 'danger',
  需复核: 'warning',
})[quickReviewLabel(row)] || 'info'
const briefRiskText = (value, limit = 48) => {
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}
const quickReviewReason = (row) => {
  const quantity = Number(row?.quantity ?? row?.calculation_quantity)
  if (!Number.isFinite(quantity) || quantity <= 0 || row?.pricing_status === 'quantity_unresolved') {
    return '工程量缺失、为 0 或尚未解析'
  }
  if (draftSuggestedUnitPrice(row) === null || row?.pricing_status === 'missing_unit_price' || row?.pricing_status === 'pending_match') {
    return '尚未匹配到有效的不含税单价'
  }
  if (['ai_estimate', 'llm'].includes(row?.price_source || row?.pricing_source || row?.unit_price_source)) {
    const estimate = row?.ai_estimate?.estimate || row?.ai_estimate || {}
    const aiRisk = Array.isArray(estimate?.risks) ? estimate.risks.find((item) => String(item || '').trim()) : ''
    return briefRiskText(aiRisk) || '单价来自 AI 估算，需人工确认'
  }
  if (row?.pricing_status === 'unit_conflict') return '清单单位与匹配定额单位不一致'
  if (row?.pricing_status === 'numeric_overflow') return '单价或合价数值超出系统安全范围'
  return ''
}
const constructionNoteSummary = (row, limit = 52) => {
  const text = constructionRemark(row).replace(/\s+/g, ' ').trim()
  if (!text) return '暂无施工提示，建议补充施工做法、避坑要点和报价边界'
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}
const openConstructionNoteDrawer = (row) => {
  ensureDraftBreakdownInputs(row)
  setDraftBreakdownInput(row, 'remark', constructionRemark(row))
  constructionNoteDrawer.row = row
  constructionNoteDrawer.visible = true
}
function emptyProjectQuotaResourceForm() {
  return {
    fee_bucket: 'auxiliary_material',
    library_kind: 'material',
    resource_code: '',
    component_type: '辅材',
    resource_name: '',
    worker_or_subtype: '',
    category: '',
    specification: '',
    brand: '',
    unit: '',
    quantity: 1,
    unit_price: 0,
    amount: 0,
    tax_rate: null,
    work_content: '',
    calculation_rule: '',
  }
}
const projectQuotaMainColumns = [
  { key: '__expand_spacer', width: 52 },
  { key: 'quota_code', label: '定额编码', width: 72, align: 'center' },
  { key: 'item_name', label: '名称', width: 190 },
  { key: 'project_feature', label: '项目特征', minWidth: 260 },
  { key: 'unit', label: '单位', width: 80 },
  { key: 'quantity', label: '工程量', width: 120, align: 'right', quantity: true },
  { key: 'unit_price', label: '不含税综合单价', width: 135, align: 'right', money: true },
  { key: 'line_total', label: '不含税综合合价', width: 145, align: 'right', money: true },
  { key: 'labor_fee', label: '人工费', width: 110, align: 'right', money: true },
  { key: 'main_material_fee', label: '主材费', width: 110, align: 'right', money: true },
  { key: 'auxiliary_material_fee', label: '辅材费', width: 110, align: 'right', money: true },
  { key: 'machinery_fee', label: '机械费', width: 110, align: 'right', money: true },
  { key: 'measure_fee', label: '措施费', width: 110, align: 'right', money: true },
  { key: 'management_fee', label: '管理费', width: 110, align: 'right', money: true },
  { key: 'tax_fee', label: '税费', width: 110, align: 'right', money: true },
]
const projectQuotaMainFieldValue = (item, column) => {
  if (column.key === '__expand_spacer') return ''
  const value = item?.[column.key]
  if (column.money) return formatMoney(value)
  if (column.quantity) return formatQuantity(value)
  return value === null || value === undefined || value === '' ? '—' : value
}
const projectQuotaMainItem = (row) => {
  const selected = row?.selected_source || row?.selected_quota || {}
  const breakdown = row?.pricing_breakdown || row?.cost_breakdown || {}
  const sourceSheet = selected?.source_sheet || row?.source_sheet
  const sourceRow = selected?.source_row_index ?? row?.source_raw_row_index
  const quantity = row?.quantity ?? row?.calculation_quantity
  const unitPrice = selected?.unit_price ?? row?.base_unit_price ?? row?.effective_unit_price ?? null
  const numericQuantity = Number(quantity)
  const numericUnitPrice = Number(unitPrice)
  return {
    quota_code: selected?.quota_code || selected?.code || null,
    item_name: selected?.item_name || selected?.name || row?.item_name || '项目补充定额',
    project_feature: selected?.work_content || selected?.specification || row?.spec || row?.project_feature || '',
    work_content: selected?.work_content || row?.spec || row?.project_feature || '',
    unit: selected?.unit || row?.unit || null,
    quantity,
    unit_price: unitPrice,
    line_total: Number.isFinite(numericQuantity) && Number.isFinite(numericUnitPrice)
      ? Number((numericQuantity * numericUnitPrice).toFixed(6))
      : null,
    labor_fee: selected?.labor_fee ?? breakdown?.labor_unit_cost ?? 0,
    main_material_fee: selected?.main_material_fee ?? breakdown?.main_material_unit_cost ?? 0,
    auxiliary_material_fee: selected?.auxiliary_material_fee ?? breakdown?.auxiliary_material_unit_cost ?? 0,
    machinery_fee: selected?.machinery_fee ?? breakdown?.machinery_unit_cost ?? 0,
    measure_fee: selected?.measure_fee ?? selected?.measure_unit_cost ?? breakdown?.measure_unit_cost ?? 0,
    management_fee: selected?.management_fee ?? selected?.management_unit_cost ?? breakdown?.management_unit_cost ?? 0,
    tax_fee: selected?.tax_fee ?? selected?.tax_amount ?? breakdown?.tax_amount ?? 0,
    source_location: sourceSheet
      ? `${sourceSheet}${sourceRow ? ` · 第 ${sourceRow} 行` : ''}`
      : '当前项目报价草稿',
  }
}
const projectQuotaRowClass = (row) => (
  String(lineIdOf(projectQuotaWorkbench.row) || '') === String(lineIdOf(row) || '')
    ? 'is-selected-project-quota'
    : ''
)
function toggleProfessionalQuotaRow(row) {
  professionalTableRef.value?.toggleRowExpansion(row)
}
const draftRowSequence = (index) => ((draftLinePage.value - 1) * draftLinePageSize) + index + 1
const projectQuotaResourcePayload = () => ({
  fee_bucket: projectQuotaEditor.form.fee_bucket,
  library_kind: projectQuotaEditor.form.library_kind || null,
  resource_code: projectQuotaEditor.form.resource_code || null,
  component_type: projectQuotaEditor.form.component_type || null,
  resource_name: projectQuotaEditor.form.resource_name?.trim() || '',
  worker_or_subtype: projectQuotaEditor.form.worker_or_subtype || null,
  category: projectQuotaEditor.form.category || null,
  specification: projectQuotaEditor.form.specification || null,
  brand: projectQuotaEditor.form.brand || null,
  unit: projectQuotaEditor.form.unit || null,
  quantity: Number(projectQuotaEditor.form.quantity ?? 0),
  unit_price: Number(projectQuotaEditor.form.unit_price ?? 0),
  amount: Number(projectQuotaEditor.form.amount ?? 0),
  tax_rate: projectQuotaEditor.form.tax_rate === null || projectQuotaEditor.form.tax_rate === ''
    ? null
    : Number(projectQuotaEditor.form.tax_rate),
  work_content: projectQuotaEditor.form.work_content || null,
  calculation_rule: projectQuotaEditor.form.calculation_rule || null,
})
const setProjectQuotaSnapshot = (snapshot) => {
  if (!snapshot) return
  projectQuotaWorkbench.snapshot = snapshot
  if (projectQuotaEditor.resource) {
    projectQuotaEditor.resource = (snapshot.resources || []).find(
      (row) => row.resource_uuid === projectQuotaEditor.resource.resource_uuid,
    ) || null
  }
}
async function selectProjectQuota(row) {
  if (!row || !projectId.value) return
  projectQuotaWorkbench.loading = true
  projectQuotaWorkbench.row = row
  projectQuotaWorkbench.snapshot = null
  projectQuotaEditor.visible = false
  projectQuotaEditor.syncToEnterprise = false
  try {
    const response = await budgetProjectApi.materializeProjectQuota(
      projectId.value,
      lineIdOf(row),
      { pricing_mode: selectedDraftMode.value },
    )
    setProjectQuotaSnapshot(budgetResponseData(response))
  } catch (error) {
    projectQuotaWorkbench.snapshot = null
    ElMessage.error(budgetApiErrorMessage(error, '项目定额与工料机明细读取失败'))
  } finally {
    projectQuotaWorkbench.loading = false
  }
}
function startCreateProjectQuotaResource() {
  if (!projectQuotaCanEdit.value) return ElMessage.warning('当前账号无权编辑项目工料机明细')
  projectQuotaEditor.mode = 'create'
  projectQuotaEditor.resource = null
  projectQuotaEditor.form = emptyProjectQuotaResourceForm()
  projectQuotaEditor.reason = ''
  projectQuotaEditor.syncToEnterprise = false
  projectQuotaEditor.visible = true
}
function startEditProjectQuotaResource(resource) {
  if (!resource) return
  const isCurrentResource = projectQuotaEditor.resource === resource
    || (
      projectQuotaEditor.resource?.resource_uuid
      && projectQuotaEditor.resource.resource_uuid === resource.resource_uuid
    )
  if (projectQuotaEditor.visible && projectQuotaEditor.mode === 'edit' && isCurrentResource) {
    projectQuotaEditor.visible = false
    projectQuotaEditor.syncToEnterprise = false
    return
  }
  projectQuotaEditor.mode = 'edit'
  projectQuotaEditor.resource = resource
  projectQuotaEditor.form = {
    fee_bucket: resource.fee_bucket || 'auxiliary_material',
    library_kind: resource.library_kind || (resource.fee_bucket === 'labor' ? 'labor' : 'material'),
    resource_code: resource.resource_code || '',
    component_type: resource.component_type || '',
    resource_name: resource.resource_name || '',
    worker_or_subtype: resource.worker_or_subtype || '',
    category: resource.category || '',
    specification: resource.specification || '',
    brand: resource.brand || '',
    unit: resource.unit || '',
    quantity: Number(resource.quantity ?? 0),
    unit_price: Number(resource.unit_price ?? 0),
    amount: Number(resource.amount ?? 0),
    tax_rate: resource.tax_rate === null || resource.tax_rate === undefined ? null : Number(resource.tax_rate),
    work_content: resource.work_content || '',
    calculation_rule: resource.calculation_rule || '',
  }
  projectQuotaEditor.reason = ''
  projectQuotaEditor.syncToEnterprise = false
  projectQuotaEditor.visible = true
}
function recalculateProjectQuotaEditorAmount() {
  const quantity = Number(projectQuotaEditor.form.quantity ?? 0)
  const unitPrice = Number(projectQuotaEditor.form.unit_price ?? 0)
  projectQuotaEditor.form.amount = Number.isFinite(quantity) && Number.isFinite(unitPrice)
    ? Number((quantity * unitPrice).toFixed(6))
    : 0
}
function guardProjectQuotaEnterpriseSync(checked) {
  if (!checked) return
  if (!projectQuotaCanSyncEnterprise.value) {
    projectQuotaEditor.syncToEnterprise = false
    ElMessage.warning('无权限同步企业定额，请联系管理员授予成本核定权限')
  }
}
async function performProjectQuotaEnterpriseSync(reason) {
  if (!projectQuotaWorkbench.snapshot || !projectQuotaWorkbench.row) return false
  if (!projectQuotaCanSyncEnterprise.value) {
    ElMessage.warning('无权限同步企业定额，请联系管理员授予成本核定权限')
    return false
  }
  const syncReason = String(reason || '').trim()
  if (syncReason.length < 4) {
    ElMessage.warning('同步企业定额时请填写至少 4 个字符的修改原因')
    return false
  }
  projectQuotaEditor.syncing = true
  try {
    const response = await budgetProjectApi.syncProjectQuotaToEnterprise(
      projectId.value,
      lineIdOf(projectQuotaWorkbench.row),
      {
        expected_snapshot_revision: Number(projectQuotaWorkbench.snapshot.revision),
        sync_to_enterprise: true,
        reason: syncReason,
      },
    )
    const data = budgetResponseData(response) || {}
    setProjectQuotaSnapshot(data.snapshot)
    ElMessage.success(data.message || '已同步到企业定额草稿版本，待审核启用')
    return true
  } catch (error) {
    if (error?.response?.status === 403) {
      ElMessage.warning('无权限同步企业定额，请联系管理员授予成本核定权限')
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '企业定额同步失败；项目定额修改仍已保留'))
    }
    return false
  } finally {
    projectQuotaEditor.syncing = false
  }
}
async function saveProjectQuotaResource() {
  if (!projectQuotaCanEdit.value || !projectQuotaWorkbench.snapshot || !projectQuotaWorkbench.row) return
  if (!projectQuotaEditor.form.resource_name?.trim()) {
    return ElMessage.warning('请填写工料机名称')
  }
  if (projectQuotaEditor.syncToEnterprise && !projectQuotaCanSyncEnterprise.value) {
    return ElMessage.warning('无权限同步企业定额，请取消勾选后仅保存到项目，或联系管理员授权')
  }
  if (projectQuotaEditor.syncToEnterprise && projectQuotaEditor.reason.trim().length < 4) {
    return ElMessage.warning('同步企业定额时请填写至少 4 个字符的修改原因')
  }
  projectQuotaEditor.saving = true
  try {
    const payload = {
      expected_snapshot_revision: Number(projectQuotaWorkbench.snapshot.revision),
      ...projectQuotaResourcePayload(),
      reason: projectQuotaEditor.reason.trim() || '项目工料机明细调整',
    }
    const response = projectQuotaEditor.mode === 'create'
      ? await budgetProjectApi.createProjectQuotaResource(
        projectId.value,
        lineIdOf(projectQuotaWorkbench.row),
        payload,
      )
      : await budgetProjectApi.updateProjectQuotaResource(
        projectId.value,
        lineIdOf(projectQuotaWorkbench.row),
        projectQuotaEditor.resource.resource_uuid,
        {
          ...payload,
          expected_resource_revision: Number(projectQuotaEditor.resource.revision),
        },
      )
    setProjectQuotaSnapshot(budgetResponseData(response))
    ElMessage.success(projectQuotaEditor.mode === 'create' ? '工料机已新增，项目定额已重算' : '工料机已更新，项目定额已重算')
    if (projectQuotaEditor.syncToEnterprise) {
      await performProjectQuotaEnterpriseSync(projectQuotaEditor.reason)
    }
    projectQuotaEditor.visible = false
    projectQuotaEditor.syncToEnterprise = false
    await loadDraft(true)
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning(budgetApiErrorMessage(error, '项目定额已被其他操作更新，请关闭后重新打开'))
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '工料机明细保存失败'))
    }
  } finally {
    projectQuotaEditor.saving = false
  }
}
async function deleteProjectQuotaResourceRow() {
  if (!projectQuotaCanEdit.value || !projectQuotaEditor.resource || !projectQuotaWorkbench.snapshot) return
  if (projectQuotaEditor.syncToEnterprise && !projectQuotaCanSyncEnterprise.value) {
    return ElMessage.warning('无权限同步企业定额，请取消勾选后仅删除项目明细，或联系管理员授权')
  }
  if (projectQuotaEditor.syncToEnterprise && projectQuotaEditor.reason.trim().length < 4) {
    return ElMessage.warning('同步企业定额时请填写至少 4 个字符的修改原因')
  }
  try {
    await ElMessageBox.confirm(
      `确定删除“${projectQuotaEditor.resource.resource_name}”吗？项目定额会立即重算。`,
      '删除工料机',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  projectQuotaEditor.deleting = true
  try {
    const response = await budgetProjectApi.deleteProjectQuotaResource(
      projectId.value,
      lineIdOf(projectQuotaWorkbench.row),
      projectQuotaEditor.resource.resource_uuid,
      {
        expected_snapshot_revision: Number(projectQuotaWorkbench.snapshot.revision),
        expected_resource_revision: Number(projectQuotaEditor.resource.revision),
        reason: projectQuotaEditor.reason.trim() || '删除项目工料机明细',
      },
    )
    setProjectQuotaSnapshot(budgetResponseData(response))
    ElMessage.success('工料机已删除，项目定额已重算')
    if (projectQuotaEditor.syncToEnterprise) {
      await performProjectQuotaEnterpriseSync(projectQuotaEditor.reason)
    }
    projectQuotaEditor.visible = false
    projectQuotaEditor.syncToEnterprise = false
    await loadDraft(true)
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '工料机删除失败'))
  } finally {
    projectQuotaEditor.deleting = false
  }
}
async function syncCurrentProjectQuotaToEnterprise() {
  if (!projectQuotaCanSyncEnterprise.value) {
    return ElMessage.warning('无权限同步企业定额，请联系管理员授予成本核定权限')
  }
  try {
    const result = await ElMessageBox.prompt(
      '本操作会把当前项目定额写入一个企业定额草稿版本，不会直接修改 active 版本。请输入同步原因。',
      '同步到企业定额库',
      {
        confirmButtonText: '确认同步',
        cancelButtonText: '取消',
        inputPlaceholder: '例如：项目复核后修正人工含量和材料单价',
        inputValidator: (value) => String(value || '').trim().length >= 4 || '请至少填写 4 个字符',
      },
    )
    await performProjectQuotaEnterpriseSync(result.value)
  } catch {
    // User canceled the explicit enterprise synchronization.
  }
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
const draftSourcePercent = (count) => {
  const total = draftSummaryCount('line_count', 'row_count', 'standard_item_count')
  if (total <= 0) return '0.0%'
  const sourceCount = Math.max(0, Number(count) || 0)
  return `${((sourceCount / total) * 100).toFixed(1)}%`
}
const resourceDetailDrawerTitle = computed(() => ({
  labor: '人工明细',
  main_material: '主材明细',
  auxiliary_material: '辅材明细',
})[resourceDetailDrawer.bucket] || '费用明细')
const openStatisticsExportDialog = () => {
  if (!draft.value || !projectId.value) return ElMessage.warning('请先创建计价草稿')
  statisticsExportDialog.sections = statisticsExportOptions.map((item) => item.value)
  statisticsExportDialog.visible = true
}
const openResourceDetail = async (item) => {
  const bucket = item?.detailBucket
  if (!bucket || !draft.value || !projectId.value) return
  resourceDetailDrawer.visible = true
  resourceDetailDrawer.loading = true
  resourceDetailDrawer.bucket = bucket
  resourceDetailDrawer.rows = []
  resourceDetailDrawer.rowCount = 0
  resourceDetailDrawer.totalAmount = 0
  resourceDetailDrawer.derivedRowCount = 0
  try {
    const response = await budgetProjectApi.pricingDraftResourceDetails(projectId.value, {
      pricing_mode: selectedDraftMode.value,
      bucket,
    })
    const data = budgetResponseData(response) || {}
    resourceDetailDrawer.rows = Array.isArray(data.rows) ? data.rows : []
    resourceDetailDrawer.rowCount = Number(data.row_count ?? resourceDetailDrawer.rows.length) || 0
    resourceDetailDrawer.totalAmount = Number(data.total_amount) || 0
    resourceDetailDrawer.derivedRowCount = Number(data.derived_row_count) || 0
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, `${resourceDetailDrawerTitle.value}读取失败`))
  } finally {
    resourceDetailDrawer.loading = false
  }
}
const openProcurementStatistics = async () => {
  if (!draft.value || !projectId.value) return
  procurementDrawer.visible = true
  procurementDrawer.loading = true
  procurementDrawer.activeTab = 'materials'
  procurementDrawer.materialRows = []
  procurementDrawer.laborRows = []
  procurementDrawer.unresolvedRows = []
  procurementDrawer.materialUnitTotals = []
  procurementDrawer.laborUnitTotals = []
  procurementDrawer.materialKindCount = 0
  procurementDrawer.laborTradeCount = 0
  procurementDrawer.unresolvedLineCount = 0
  try {
    const response = await budgetProjectApi.pricingDraftProcurementStatistics(projectId.value, {
      pricing_mode: selectedDraftMode.value,
    })
    const data = budgetResponseData(response) || {}
    procurementDrawer.materialRows = Array.isArray(data.material_rows) ? data.material_rows : []
    procurementDrawer.laborRows = Array.isArray(data.labor_rows) ? data.labor_rows : []
    procurementDrawer.unresolvedRows = Array.isArray(data.unresolved_rows) ? data.unresolved_rows : []
    procurementDrawer.materialUnitTotals = Array.isArray(data.material_unit_totals) ? data.material_unit_totals : []
    procurementDrawer.laborUnitTotals = Array.isArray(data.labor_unit_totals) ? data.labor_unit_totals : []
    procurementDrawer.materialKindCount = Number(data.material_kind_count ?? procurementDrawer.materialRows.length) || 0
    procurementDrawer.laborTradeCount = Number(data.labor_trade_count ?? procurementDrawer.laborRows.length) || 0
    procurementDrawer.unresolvedLineCount = Number(data.unresolved_line_count ?? procurementDrawer.unresolvedRows.length) || 0
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '采购与用工统计读取失败'))
  } finally {
    procurementDrawer.loading = false
  }
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
  statisticsExportDialog.visible = false
  resourceDetailDrawer.visible = false
  procurementDrawer.visible = false
  projectQuotaWorkbench.loading = false
  projectQuotaWorkbench.row = null
  projectQuotaWorkbench.snapshot = null
  projectQuotaEditor.visible = false
  projectQuotaEditor.resource = null
  projectQuotaEditor.syncToEnterprise = false
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
        '重新生成会读取当前正式清单，并清除报价草稿中的人工调整；已经保存的报价版本不会变化。',
        '确认重新生成报价草稿',
        { type: 'warning', confirmButtonText: '确认重新生成', cancelButtonText: '取消' },
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
    ElMessage.success(draft.value ? '报价草稿已重新生成' : '报价草稿已创建')
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

async function exportOriginalFormatPricingDraft() {
  if (!draft.value || !projectId.value) return ElMessage.warning('请先创建计价草稿')
  originalExporting.value = true
  try {
    const response = await budgetProjectApi.exportOriginalFormatPricingDraft(projectId.value, {
      pricing_mode: selectedDraftMode.value,
    })
    const contentDisposition = response.headers?.['content-disposition'] || ''
    const filenameMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
    const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : '原格式报价.xlsx'
    const objectUrl = URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(objectUrl)
    const pendingCount = Number(draft.value?.summary?.pending_count ?? draft.value?.summary?.missing_price_count ?? 0)
    if (pendingCount > 0) {
      ElMessage.warning(`已导出，仍有 ${pendingCount} 行待人工补价`)
    } else {
      ElMessage.success('已导出原格式报价 Excel')
    }
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '原格式报价 Excel 导出失败'))
  } finally {
    originalExporting.value = false
  }
}

async function exportPricingStatistics(sections = statisticsExportDialog.sections, closeDialog = false) {
  if (!draft.value || !projectId.value) return ElMessage.warning('请先创建计价草稿')
  const selectedSections = [...new Set(
    (sections || [])
      .filter((section) => statisticsExportOptions.some((item) => item.value === section)),
  )]
  if (!selectedSections.length) return ElMessage.warning('请至少选择一项导出内容')
  statisticsExporting.value = true
  try {
    const response = await budgetProjectApi.exportPricingDraftStatistics(projectId.value, {
      pricing_mode: selectedDraftMode.value,
      sections: selectedSections.join(','),
    })
    const contentDisposition = response.headers?.['content-disposition'] || ''
    const filenameMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
    const filename = filenameMatch ? decodeURIComponent(filenameMatch[1]) : '报价统计.xlsx'
    const objectUrl = URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(objectUrl)
    if (closeDialog) statisticsExportDialog.visible = false
    const selectedLabels = statisticsExportOptions
      .filter((item) => selectedSections.includes(item.value))
      .map((item) => item.label)
    ElMessage.success(`已导出：${selectedLabels.join('、')}`)
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '统计导出失败'))
  } finally {
    statisticsExporting.value = false
  }
}

const confirmStatisticsExport = () => exportPricingStatistics(statisticsExportDialog.sections, true)
const exportCurrentResourceDetail = () => exportPricingStatistics([resourceDetailDrawer.bucket])

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
  if (!canManageDraft.value || !draft.value) return false
  const lineId = lineIdOf(row)
  const raw = clear ? '' : String(draftPriceInputs[lineId] ?? '').replace(/,/g, '').trim()
  const breakdown = clear ? {} : draftBreakdownPayload(row)
  if (breakdown === null) {
    ElMessage.warning('拆分费用只能填写非负数字')
    return false
  }
  const breakdownPrice = draftBreakdownCompositePrice(breakdown)
  let price = null
  if (breakdownPrice !== null) {
    price = breakdownPrice
  } else if (raw !== '') {
    price = Number(raw)
    if (!Number.isFinite(price) || price <= 0) {
      ElMessage.warning('请输入大于 0 的有效人工单价，或留空后清除')
      return false
    }
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
    return true
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已被其他操作更新，已重新加载最新内容，请再次确认后保存')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '人工单价保存失败'))
    }
    return false
  } finally {
    draftLineSaving[lineId] = false
  }
}

async function saveConstructionNote() {
  const row = constructionNoteDrawer.row
  if (!row || !draft.value || !canManageDraft.value) return
  const lineId = lineIdOf(row)
  const remark = sanitizeConstructionNote(draftBreakdownInputValue(row, 'remark'), constructionPricingPhrases(row))
  if (!remark) return ElMessage.warning('请填写工艺做法或施工避坑后再保存')
  draftLineSaving[lineId] = true
  try {
    await budgetProjectApi.updatePricingDraftLineConstructionNote(projectId.value, lineId, {
      pricing_mode: selectedDraftMode.value,
      expected_revision: draftRevision.value,
      expected_line_revision: Number(row.line_revision ?? row.revision ?? 0),
      remark,
      reason: 'construction_note_edit',
    })
    ElMessage.success('施工提示已保存')
    await loadDraft(true)
    constructionNoteDrawer.visible = false
    constructionNoteDrawer.row = null
  } catch (error) {
    if (error?.response?.status === 409) {
      ElMessage.warning('草稿已被其他操作更新，已重新加载最新内容，请再次确认后保存')
      await loadDraft(true)
    } else {
      ElMessage.error(budgetApiErrorMessage(error, '施工提示保存失败'))
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

async function activateSelectedPricingRun() {
  if (!selectedRun.value || !projectId.value || pricingRunActive(selectedRun.value) || !pricingRunHasSnapshot(selectedRun.value)) return
  versionActivating.value = true
  try {
    await budgetProjectApi.activatePricingRun(projectId.value, runIdOf(selectedRun.value))
    selectedDraftMode.value = 'enterprise_ai'
    pricingWorkspaceView.value = 'quick'
    await refreshPricing()
    emit('version-activated')
    ElMessage.success(`${pricingRunVersionLabel(selectedRun.value)} 已启用，报价页面已更新`)
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '报价版本启用失败'))
  } finally {
    versionActivating.value = false
  }
}

async function archiveSelectedPricingRun() {
  if (!selectedRun.value || !projectId.value || pricingRunArchived(selectedRun.value)) return
  const versionLabel = pricingRunVersionLabel(selectedRun.value)
  try {
    await ElMessageBox.confirm(
      `确认归档报价版本 ${versionLabel}？归档后仍可重新启用。`,
      '归档报价版本',
      {
        type: 'warning',
        confirmButtonText: '确认归档',
        cancelButtonText: '取消',
      },
    )
  } catch (error) {
    if (['cancel', 'close'].includes(error)) return
    throw error
  }
  versionArchiving.value = true
  try {
    await budgetProjectApi.archivePricingRun(projectId.value, runIdOf(selectedRun.value))
    await refreshPricing()
    emit('version-activated')
    ElMessage.success(`${versionLabel} 已归档`)
  } catch (error) {
    ElMessage.error(budgetApiErrorMessage(error, '报价版本归档失败'))
  } finally {
    versionArchiving.value = false
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
.quote-workbench-flow{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin:4px 0 14px;padding:12px;border:1px solid rgba(148,163,184,.2);border-radius:14px;background:rgba(255,255,255,.88)}
.quote-workbench-flow>div{position:relative;display:flex;align-items:center;gap:8px;min-width:0;color:#94a3b8}
.quote-workbench-flow>div:not(:last-child)::after{position:absolute;right:2px;width:18px;height:1px;background:#dbe2ea;content:""}
.quote-workbench-flow span{display:grid;flex:0 0 24px;width:24px;height:24px;place-items:center;border-radius:50%;background:#e2e8f0;font-size:12px;font-weight:700}
.quote-workbench-flow strong{overflow:hidden;font-size:13px;text-overflow:ellipsis;white-space:nowrap}
.quote-workbench-flow>div.active{color:#1d4ed8}.quote-workbench-flow>div.active span{background:#2563eb;color:#fff}
.workspace-view-tabs{display:flex;margin-bottom:14px}.workspace-view-tabs:deep(.el-radio-button){flex:1}.workspace-view-tabs:deep(.el-radio-button__inner){width:100%;padding:11px 16px}
.pricing-version-card{display:grid;grid-template-columns:minmax(240px,420px) auto 1fr;align-items:end;gap:14px;padding:18px;border:1px solid rgba(37,99,235,.2);border-radius:16px;background:linear-gradient(135deg,#eff6ff,#fff)}.pricing-version-card>label{display:flex;flex-direction:column;gap:7px;color:#64748b;font-size:13px}.pricing-version-card>label .el-select{width:100%}.pricing-version-card>.el-tag{align-self:end;margin-bottom:5px}.pricing-version-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px}.pricing-version-actions .el-button+.el-button{margin-left:0}@media(max-width:760px){.pricing-version-card{grid-template-columns:1fr;align-items:stretch}.pricing-version-card>.el-tag{justify-self:start;margin-bottom:0}.pricing-version-actions{justify-content:flex-start}}
.draft-strategy-panel{margin-bottom:14px;padding:14px;border:1px solid rgba(245,158,11,.24);border-radius:14px;background:#fffbeb}
.draft-strategy-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:10px}.draft-strategy-heading>div{display:flex;flex-direction:column;gap:4px}
.draft-strategy-heading span,.quick-item-cell span,.draft-status-stack small{color:#64748b;font-size:12px}
.quote-workbench-hero{display:grid;grid-template-columns:minmax(280px,1.2fr) minmax(560px,2fr);gap:12px;margin-bottom:14px}
.quote-workbench-total{display:flex;flex-direction:column;justify-content:center;min-height:112px;padding:20px 24px;border-radius:16px;background:linear-gradient(135deg,#0f172a,#172554);color:#fff}
.quote-workbench-total span,.quote-workbench-total small{color:rgba(255,255,255,.7)}.quote-workbench-total strong{margin:7px 0;font-size:clamp(24px,2vw,34px);letter-spacing:-.02em}
.quote-workbench-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px}.quote-workbench-kpis>div{display:flex;flex-direction:column;justify-content:center;min-width:0;padding:14px;border:1px solid rgba(148,163,184,.22);border-radius:14px;background:#fff}
.quote-workbench-kpis span{color:#64748b;font-size:12px}.quote-workbench-kpis strong{margin-top:6px;color:#0f172a;font-size:24px}.quote-workbench-kpis>div.warning{border-color:rgba(245,158,11,.42);background:#fffaf0}.quote-workbench-kpis>div.warning strong{color:#c2410c}
.quote-workbench-kpis .source-kpi small{margin-top:4px;color:#94a3b8;font-size:12px;line-height:1.35}
.quick-item-cell{display:flex;flex-direction:column;gap:5px}.quick-item-cell span{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2}.quick-risk-cell{display:flex;align-items:flex-start;flex-direction:column;gap:6px}.quick-risk-cell small{color:#64748b;font-size:12px;line-height:1.45}.quick-review-table:deep(.el-table__cell){padding:10px 0}.quick-review-table:deep(.cell){padding-right:11px;padding-left:11px;line-height:1.45}.quick-review-table:deep(th.el-table__cell .cell){white-space:nowrap}
.construction-note-cell{display:grid;gap:6px;min-width:0}.construction-note-cell-head{display:flex;align-items:center;justify-content:flex-end;gap:8px}.construction-note-cell-head .el-button{margin-left:0}.construction-note-cell>span,.professional-note-preview{display:-webkit-box;overflow:hidden;color:#475569;font-size:12px;line-height:1.5;-webkit-box-orient:vertical;-webkit-line-clamp:2}.construction-note-cell>span.empty{color:#94a3b8}.construction-note-drawer{display:grid;gap:16px}.construction-note-hero{padding:16px;border:1px solid rgba(148,163,184,.24);border-radius:16px;background:#f8fafc}.construction-note-hero strong{color:#0f172a;font-size:17px;line-height:1.5}.construction-note-section-title{display:flex;flex-direction:column;gap:5px}.construction-note-section-title span{color:#64748b;font-size:12px;line-height:1.5}.construction-note-section{display:grid;gap:10px;padding:15px;border:1px solid rgba(148,163,184,.22);border-radius:14px;background:#fff}.construction-note-footer{display:flex;justify-content:flex-end;gap:8px}
.source-basis-tag{cursor:pointer;user-select:none;transition:transform .16s ease,box-shadow .16s ease}.source-basis-tag:hover{box-shadow:0 3px 10px rgba(37,99,235,.18);transform:translateY(-1px)}.source-basis-tag:focus-visible{outline:2px solid rgba(37,99,235,.42);outline-offset:2px}.cost-basis-drawer{display:grid;gap:16px}.cost-basis-hero{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:17px 18px;border:1px solid rgba(37,99,235,.2);border-radius:16px;background:linear-gradient(135deg,#eff6ff,#fff)}.cost-basis-hero>div{display:flex;min-width:0;flex-direction:column;gap:5px}.cost-basis-hero span,.cost-basis-hero small{color:#64748b;font-size:12px}.cost-basis-hero strong{color:#0f172a;font-size:17px;line-height:1.5}.cost-basis-section{display:grid;gap:11px;padding:15px;border:1px solid rgba(148,163,184,.22);border-radius:14px;background:#fff}.cost-basis-footer{display:flex;justify-content:flex-end;gap:8px}
.quick-row-actions{display:flex;gap:6px}.quick-row-actions .el-button+.el-button{margin-left:0}.professional-fields-table{border-top:3px solid #2563eb}.quick-review-table{border-top:3px solid #22c55e}
@media(max-width:1200px){.quote-workbench-flow{grid-template-columns:repeat(3,minmax(0,1fr))}.quote-workbench-hero{grid-template-columns:1fr}}
@media(max-width:760px){.quote-workbench-flow,.quote-workbench-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.workspace-view-tabs{display:grid;grid-template-columns:1fr 1fr}.draft-strategy-heading{align-items:flex-start;flex-direction:column}}
.budget-panel{padding:20px;border:1px solid rgba(148,163,184,.22);border-radius:20px;background:rgba(255,255,255,.9);box-shadow:0 14px 34px rgba(15,23,42,.06);margin-bottom:18px}.budget-title{display:flex;justify-content:space-between;gap:16px;margin-bottom:16px}.budget-title>div,.pricing-source{display:flex;flex-direction:column;gap:4px}.budget-title small,.pricing-source small,.pricing-context span,.pricing-metrics span,.pricing-run-meta{color:#64748b}.pricing-actions{align-items:flex-end;flex-direction:row!important}.pricing-alert{margin-bottom:14px}.pricing-context{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px}.pricing-context>div,.pricing-metrics>div{padding:14px;border:1px solid rgba(148,163,184,.2);border-radius:16px;background:#fff}.pricing-context strong{display:block;margin-top:7px}.pricing-run-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px}.pricing-run-toolbar>.el-select{width:min(420px,100%)}.pricing-run-meta{display:flex;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:wrap;font-size:13px}.pricing-metrics{display:grid;grid-template-columns:1.4fr repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}.pricing-metrics strong{display:block;margin:7px 0;font-size:22px}.pricing-filters{display:grid;grid-template-columns:minmax(260px,1fr) 190px 190px auto;gap:12px;margin-bottom:14px}.drawer-section{margin-top:22px}.pricing-evidence{max-height:360px;overflow:auto;margin:0;padding:16px;border-radius:14px;background:#0f172a;color:#e2e8f0;font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word}.el-pagination{margin-top:16px;justify-content:flex-end}@media(max-width:1100px){.pricing-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:760px){.pricing-context,.pricing-metrics,.pricing-filters{grid-template-columns:1fr}.pricing-run-toolbar{align-items:stretch;flex-direction:column}.pricing-run-meta{justify-content:flex-start}.budget-title{align-items:flex-start;flex-direction:column}.pricing-actions{align-items:flex-start!important}}
.pricing-draft-workspace{margin:20px 0 24px;padding:18px;border:1px solid rgba(37,99,235,.18);border-radius:18px;background:linear-gradient(180deg,rgba(239,246,255,.72),rgba(255,255,255,.9))}.draft-mode-selector{margin-bottom:10px}.draft-mode-help{display:flex;flex-direction:column;gap:5px;margin-bottom:16px;padding:13px 15px;border-radius:14px;background:#fff;border:1px solid rgba(148,163,184,.2)}.draft-mode-help span,.draft-meta span{color:#64748b;font-size:13px}.draft-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:14px}.draft-meta>div{padding:14px;border:1px solid rgba(148,163,184,.2);border-radius:16px;background:#fff}.draft-meta strong{display:block;margin-top:7px}.draft-metrics{grid-template-columns:repeat(5,minmax(0,1fr))}.quote-stat-strip{display:grid;grid-template-columns:auto repeat(6,minmax(140px,1fr)) auto;gap:4px;margin:-2px 0 14px;align-items:center}.quote-stat-title,.quote-stat-card{height:36px;display:flex;align-items:center;justify-content:center;border:1px solid rgba(148,163,184,.2);background:#fff}.quote-stat-title{padding:0 14px;color:#475569;font-size:13px}.quote-stat-card{gap:6px;border-radius:8px}.quote-stat-card span{color:#64748b}.quote-stat-card strong{font-size:16px}.quote-totals-panel{margin:-4px 0 14px;padding:12px;border:1px solid rgba(148,163,184,.22);border-radius:12px;background:#fff}.quote-rate-editor{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr)) auto;gap:10px;margin-top:12px;align-items:end}.quote-rate-editor label{display:flex;flex-direction:column;gap:5px;color:#64748b;font-size:12px}.draft-boundary-note{margin:-2px 0 14px;padding:10px 13px;border-radius:12px;background:#f1f5f9;color:#475569;font-size:13px}.draft-quote-job-card{margin:0 0 14px;padding:14px;border:1px solid rgba(34,197,94,.22);border-radius:16px;background:#fff}.draft-quote-job-head{display:flex;justify-content:space-between;gap:12px;margin-bottom:10px}.draft-quote-job-head>div{display:flex;flex-direction:column;gap:4px}.draft-quote-job-head small,.draft-quote-job-stats{color:#64748b;font-size:13px}.draft-quote-job-stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}.draft-filters{margin-top:4px}.draft-status-stack{display:flex;align-items:flex-start;flex-direction:column;gap:5px}.draft-price-editor{display:grid;grid-template-columns:minmax(92px,1fr) auto auto auto;gap:5px}.draft-price-editor .el-button+.el-button{margin-left:0}.quote-quantity{color:#2563eb}.quote-line-table:deep(.el-table__cell){vertical-align:top}.breakdown-input:deep(.el-input__inner){text-align:right}.breakdown-edit-cell{display:flex;align-items:center;gap:4px;min-height:24px}.breakdown-edit-cell-right{justify-content:flex-end}.quota-sync-summary,.quota-sync-bulk-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;margin-bottom:10px}.quota-sync-summary span{font-size:13px;color:#475569}.quota-sync-bulk-actions .el-button+.el-button{margin-left:0}@media(max-width:1100px){.draft-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.draft-meta{grid-template-columns:repeat(2,minmax(0,1fr))}.quote-stat-strip{grid-template-columns:1fr 1fr}.quote-rate-editor{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:760px){.draft-meta{grid-template-columns:1fr}.pricing-draft-workspace{padding:14px}.draft-price-editor{grid-template-columns:1fr auto}.draft-price-editor .el-button:last-child{grid-column:2}.quote-stat-strip,.quote-rate-editor{grid-template-columns:1fr}}
.quote-stat-card.clickable{cursor:pointer;transition:border-color .16s ease,box-shadow .16s ease,transform .16s ease}.quote-stat-card.clickable:hover{border-color:rgba(37,99,235,.45);box-shadow:0 4px 12px rgba(37,99,235,.12);transform:translateY(-1px)}.quote-stat-card.clickable:focus-visible{outline:2px solid rgba(37,99,235,.42);outline-offset:2px}.statistics-export-dialog>p{margin:0 0 14px;color:#64748b;font-size:13px}.statistics-export-options{display:grid;grid-template-columns:1fr 1fr;gap:10px}.statistics-export-option{display:flex!important;align-items:flex-start!important;height:auto!important;margin:0!important;padding:13px 14px;border:1px solid rgba(148,163,184,.26);border-radius:12px;background:#fff}.statistics-export-option:deep(.el-checkbox__label){display:flex;min-width:0;flex-direction:column;gap:4px;white-space:normal}.statistics-export-option small{color:#64748b;font-size:12px;line-height:1.4}.statistics-export-option.is-checked{border-color:#60a5fa;background:#eff6ff}.resource-detail-summary{display:flex;align-items:center;gap:14px;margin-bottom:14px;padding:13px 15px;border:1px solid rgba(37,99,235,.18);border-radius:14px;background:linear-gradient(135deg,#eff6ff,#fff)}.resource-detail-summary>div{display:flex;align-items:baseline;gap:7px}.resource-detail-summary span,.resource-detail-summary small{color:#64748b;font-size:13px}.resource-detail-summary strong{color:#0f172a;font-size:18px}.resource-detail-summary small{flex:1;min-width:180px}.resource-detail-export{margin-left:auto}.resource-detail-table{border-top:3px solid #2563eb}@media(max-width:1100px){.resource-detail-summary{align-items:flex-start;flex-direction:column}.resource-detail-summary small{min-width:0}.resource-detail-export{align-self:flex-end;margin-left:0}}@media(max-width:600px){.statistics-export-options{grid-template-columns:1fr}}
.quote-stat-actions{display:flex;align-items:center;justify-content:flex-end;gap:2px}.quote-stat-actions .el-button+.el-button{margin-left:0}.procurement-summary{display:grid;grid-template-columns:repeat(3,minmax(150px,1fr));gap:10px;margin-bottom:14px}.procurement-summary>div{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:14px 16px;border:1px solid rgba(37,99,235,.18);border-radius:14px;background:#f8fbff}.procurement-summary>div.warning{border-color:rgba(245,158,11,.4);background:#fffaf0}.procurement-summary span{color:#64748b;font-size:13px}.procurement-summary strong{color:#0f172a;font-size:24px}.procurement-summary>div.warning strong{color:#c2410c}.procurement-tabs{margin-top:14px}.procurement-unit-summary{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px;padding:10px 12px;border-radius:12px;background:#f8fafc}.procurement-unit-summary>span{color:#64748b;font-size:13px}@media(max-width:760px){.procurement-summary{grid-template-columns:1fr}.quote-stat-actions{justify-content:center}}
.professional-fields-table:deep(.project-quota-expand-cell .cell){display:flex;align-items:center;justify-content:center;padding:0}
.professional-fields-table:deep(.project-quota-expand-cell .el-table__expand-icon){display:inline-grid;width:22px;height:22px;padding:0;place-items:center;border:1px solid #93c5fd;border-radius:4px;background:#eff6ff;color:#1d4ed8;transform:none!important;transition:border-color .16s ease,background .16s ease,box-shadow .16s ease}
.professional-fields-table:deep(.project-quota-expand-cell .el-table__expand-icon:hover){border-color:#3b82f6;background:#dbeafe;box-shadow:0 2px 8px rgba(37,99,235,.18)}
.project-quota-expand-symbol{font-size:16px;font-weight:700;line-height:1}
.professional-fields-table:deep(.el-table__expanded-cell){padding:0!important;background:#f8fbff}
.project-quota-relation{position:relative}
.project-quota-relation-branch{position:absolute;z-index:3;top:0;left:25px;width:27px;height:25px;border-bottom:2px solid #60a5fa;border-left:2px solid #60a5fa;border-bottom-left-radius:4px;pointer-events:none}
.matched-quota-aligned-table{width:100%;cursor:pointer;--el-table-row-hover-bg-color:#eff6ff}
.matched-quota-aligned-table:deep(.el-table__inner-wrapper::before){display:none}
.matched-quota-aligned-table:deep(td.el-table__cell){height:48px;border-bottom:0;background:#f8fbff;vertical-align:middle}
.matched-quota-aligned-table:deep(.el-table__row:hover>td.el-table__cell){background:#eff6ff!important}
.matched-quota-aligned-table:deep(.is-selected-project-quota>td.el-table__cell){background:#dbeafe!important;color:#1d4ed8}
.project-quota-resource-workbench{border-color:rgba(37,99,235,.28);background:linear-gradient(180deg,rgba(239,246,255,.55),rgba(255,255,255,.96))}
.project-quota-workbench-heading{align-items:center}
.project-quota-summary{display:grid;grid-template-columns:1.35fr repeat(4,minmax(100px,1fr));gap:10px;margin-bottom:18px}
.project-quota-summary>div{display:flex;flex-direction:column;gap:6px;padding:14px;border:1px solid rgba(148,163,184,.22);border-radius:14px;background:#fff}
.project-quota-summary span{color:#64748b;font-size:12px}.project-quota-summary strong{color:#0f172a;font-size:20px}
.project-quota-resource-heading{align-items:flex-end;margin-top:6px}.project-quota-resource-table{border-top:3px solid #2563eb}
.project-quota-editor{margin-top:18px;padding:17px;border:1px solid rgba(37,99,235,.22);border-radius:16px;background:#f8fbff}
.project-quota-form{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:0 12px}.project-quota-form .span-2{grid-column:span 2}.project-quota-form:deep(.el-input-number),.project-quota-form:deep(.el-select){width:100%}
.project-quota-editor-tools{display:grid;grid-template-columns:auto minmax(280px,1fr);gap:10px;margin-top:2px}
.project-quota-enterprise-option{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:13px;padding:12px 14px;border:1px solid rgba(245,158,11,.3);border-radius:12px;background:#fffbeb}.project-quota-enterprise-option span{color:#92400e;font-size:12px}
.project-quota-editor-actions{display:grid;grid-template-columns:auto 1fr auto auto;gap:8px;margin-top:14px}
.project-quota-sync-status{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:16px;padding:14px 16px;border:1px solid rgba(148,163,184,.22);border-radius:14px;background:#f8fafc}.project-quota-sync-status>div{display:flex;flex-direction:column;gap:5px}.project-quota-sync-status span{color:#64748b;font-size:12px}
.professional-fields-table .draft-price-editor{grid-template-columns:minmax(92px,1fr) auto auto auto}
@media(max-width:1100px){.project-quota-summary{grid-template-columns:repeat(3,minmax(120px,1fr))}.project-quota-form{grid-template-columns:repeat(2,minmax(150px,1fr))}}
@media(max-width:760px){.project-quota-workbench-heading{align-items:flex-start}.project-quota-summary,.project-quota-form,.project-quota-editor-tools{grid-template-columns:1fr}.project-quota-form .span-2{grid-column:span 1}.project-quota-editor-actions{grid-template-columns:1fr}.project-quota-editor-actions>span{display:none}.project-quota-sync-status{align-items:flex-start;flex-direction:column}}
</style>
