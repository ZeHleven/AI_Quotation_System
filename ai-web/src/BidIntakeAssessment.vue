<template>
  <div class="bid-intake-workbench">
    <section class="runtime-strip">
      <div>
        <span>Agent Runtime</span>
        <strong>{{ readinessLabel }}</strong>
        <small>{{ readinessDetail }}</small>
      </div>
      <div>
        <span>证据清单</span>
        <strong>v{{ readiness?.evidence?.manifest_version || '-' }}</strong>
        <small>可用资料 {{ readiness?.evidence?.ready_document_count || 0 }} 份</small>
      </div>
      <div>
        <span>检索方式</span>
        <strong>{{ readiness?.evidence?.hybrid_ready ? '混合检索' : '词法兜底' }}</strong>
        <small>{{ readiness?.evidence?.index_status || '未建立混合索引' }}</small>
      </div>
      <div>
        <span>Worker</span>
        <strong>{{ readiness?.worker?.online_count || 0 }} 在线</strong>
        <small>{{ latestWorkerText }}</small>
      </div>
      <div>
        <span>总经办标准</span>
        <strong>{{ readiness?.policy?.configured ? '已装载' : '未装载' }}</strong>
        <small>{{ readiness?.policy?.active_version || '缺少 active policy' }}</small>
      </div>
    </section>

    <el-alert
      v-if="readinessBlockerText"
      type="warning"
      show-icon
      :closable="false"
      :title="readinessBlockerText"
    />

    <section class="evidence-intake-panel">
      <div class="evidence-intake-heading">
        <div>
          <span>项目资料入口</span>
          <strong>上传并解析招标资料</strong>
          <small>系统会读取文件名和解析内容，自动识别资料类型并生成新的证据清单版本。</small>
        </div>
        <el-tag :type="readiness?.evidence?.ready_document_count ? 'success' : 'warning'" effect="plain">
          {{ readiness?.evidence?.ready_document_count || 0 }} 份资料可用
        </el-tag>
      </div>

      <div class="evidence-upload-layout">
        <el-upload
          ref="evidenceUploadRef"
          v-model:file-list="evidenceUploadFiles"
          class="evidence-uploader"
          drag
          multiple
          :limit="10"
          :auto-upload="false"
          :accept="evidenceAccept"
          :disabled="evidenceUploading"
          :on-change="handleEvidenceFileChange"
          :on-exceed="handleEvidenceFileExceed"
        >
          <div class="evidence-upload-copy">
            <strong>拖入招标文件，或点击选择</strong>
            <span>支持 PDF、DOCX、XLSX、XLSM、TXT、MD；单次最多 10 份</span>
          </div>
          <template #tip>
            <div class="evidence-upload-tip">
              扫描版 PDF 请先完成 OCR；旧版 .doc / .xls 请另存为新版格式。
            </div>
          </template>
        </el-upload>

        <div class="evidence-upload-controls">
          <div class="evidence-auto-classify">
            <span>资料类型</span>
            <strong>系统自动识别</strong>
            <small>可识别招标文件、答疑、补遗、图纸和工程量清单；无法确定时归入其他资料。</small>
          </div>
          <div class="evidence-upload-selection">
            <strong>已选择 {{ evidenceUploadFiles.length }} 份</strong>
            <small>同一份文件重复提交会复用原任务，不会重复入库。</small>
          </div>
          <el-button
            type="primary"
            :loading="evidenceUploading"
            :disabled="!evidenceUploadFiles.length"
            @click="uploadEvidenceFiles"
          >
            上传并开始解析
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="activeEvidenceJobs.length"
        type="info"
        show-icon
        :closable="false"
        :title="`正在处理 ${activeEvidenceJobs.length} 份资料，完成后会自动刷新证据清单。`"
      />

      <div v-if="evidenceJobs.length" class="evidence-job-list">
        <div class="evidence-job-heading">
          <div>
            <strong>最近解析任务</strong>
            <small>{{ evidencePipelineSummary }}</small>
          </div>
          <el-button
            size="small"
            plain
            :loading="evidenceJobsLoading"
            @click="refreshEvidenceProgress"
          >
            刷新进度
          </el-button>
        </div>
        <el-table :data="evidenceJobs.slice(0, 8)" size="small" row-key="job_uuid">
          <el-table-column label="文件" min-width="230">
            <template #default="{ row }">
              <div class="evidence-file-cell">
                <strong>{{ row.original_filename }}</strong>
                <small>{{ evidenceFileTypeLabel(row.file_type) }} · {{ formatFileSize(row.size_bytes) }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag :type="evidenceJobStatusType(row.status)" effect="plain" size="small">
                {{ evidenceJobStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="当前环节" min-width="150">
            <template #default="{ row }">
              <span>{{ evidenceJobStageLabel(row.stage) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="结果" min-width="210">
            <template #default="{ row }">
              <div class="evidence-result-cell">
                <span v-if="row.evidence_document_uuid">已进入证据清单</span>
                <span v-else-if="row.error_message" class="evidence-error-text">
                  {{ evidenceJobErrorText(row) }}
                </span>
                <span v-else>等待后台处理</span>
                <small>{{ formatDate(row.updated_at || row.created_at) }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="92" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'retryable'"
                link
                type="primary"
                :loading="retryingEvidenceJobUuid === row.job_uuid"
                @click="retryEvidenceParseJob(row)"
              >
                重试
              </el-button>
              <span v-else class="evidence-no-action">—</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>

    <section class="workbench-actions">
      <div>
        <h3>报价资料研判与立项辅助</h3>
        <p>Agent 负责检索证据、形成建议并通过证据门；最终立项决定仍由人工确认。</p>
      </div>
      <div>
        <el-button :loading="loading" plain @click="refresh">刷新</el-button>
        <el-button
          type="primary"
          :disabled="!readiness?.ready_to_start"
          @click="createDialog.visible = true"
        >
          发起研判
        </el-button>
      </div>
    </section>

    <section v-loading="calibrationLoading" class="calibration-summary">
      <div class="calibration-heading">
        <div>
          <span>历史回放与标准校准</span>
          <strong>{{ calibrationGateLabel }}</strong>
          <small>影子评测不会修改当前active标准，也不会自动批准发布。</small>
        </div>
        <div class="calibration-actions">
          <el-select
            v-model="candidatePolicyVersion"
            size="small"
            placeholder="选择已发布文件版本"
            style="width: 250px"
            @change="loadCalibrationReport"
          >
            <el-option
              v-for="version in calibrationReport?.available_policy_versions || []"
              :key="version"
              :label="version"
              :value="version"
            />
          </el-select>
        </div>
      </div>
      <div class="calibration-metrics">
        <div>
          <span>金标样本</span>
          <strong>{{ calibrationReport?.dataset_case_count ?? 0 }}</strong>
          <small>最低发布门槛30个</small>
        </div>
        <div>
          <span>Holdout</span>
          <strong>{{ calibrationReport?.candidate?.holdout?.case_count ?? 0 }}</strong>
          <small>未参与调参的盲测样本</small>
        </div>
        <div>
          <span>金标一致率</span>
          <strong>{{ calibrationAccuracyText }}</strong>
          <small>发布门槛不低于80%</small>
        </div>
        <div>
          <span>危险报价</span>
          <strong>{{ calibrationMetricScope?.unsafe_quote_count ?? 0 }}</strong>
          <small>候选不得高于active版本</small>
        </div>
        <div>
          <span>硬红线召回</span>
          <strong>{{ calibrationHardRecallText }}</strong>
          <small>发布门槛100%</small>
        </div>
      </div>
      <el-alert
        v-if="calibrationReport?.warnings?.length"
        type="info"
        :closable="false"
        show-icon
        :title="calibrationReport.warnings.join('；')"
      />
      <section
        v-if="calibrationCanManage"
        v-loading="sampleOperationsLoading"
        class="dataset-operations"
      >
        <div class="candidate-proposal-heading">
          <div>
            <strong>真实金标样本运营</strong>
            <small>金标创建人与复核人必须分离；只有复核通过的样本进入数据集。</small>
          </div>
          <el-button
            type="primary"
            plain
            size="small"
            :disabled="!calibrationQuality?.ready_to_freeze"
            :loading="freezingDataset"
            @click="freezeCalibrationDataset"
          >
            冻结数据集
          </el-button>
        </div>
        <div class="dataset-quality-metrics">
          <div>
            <span>复核通过</span>
            <strong>{{ calibrationQuality?.approved_case_count ?? 0 }}</strong>
            <small>总门槛30</small>
          </div>
          <div>
            <span>Development</span>
            <strong>{{ calibrationQuality?.development_case_count ?? 0 }}</strong>
            <small>最低20</small>
          </div>
          <div>
            <span>Holdout</span>
            <strong>{{ calibrationQuality?.holdout_case_count ?? 0 }}</strong>
            <small>最低10</small>
          </div>
          <div>
            <span>待复核</span>
            <strong>{{ calibrationQuality?.pending_review_count ?? 0 }}</strong>
            <small>不进入冻结集</small>
          </div>
          <div>
            <span>质量门</span>
            <strong>{{ calibrationQuality?.ready_to_freeze ? '通过' : '未通过' }}</strong>
            <small>{{ calibrationQualityFailedText }}</small>
          </div>
        </div>
        <el-alert
          v-if="calibrationQualityFailedMessages.length"
          type="warning"
          :closable="false"
          show-icon
          :title="calibrationQualityFailedMessages.join('；')"
        />
        <div class="sample-filters">
          <el-select
            v-model="sampleFilters.reviewStatus"
            clearable
            size="small"
            placeholder="复核状态"
            @change="resetAndLoadSamples"
          >
            <el-option label="待复核" value="pending" />
            <el-option label="复核通过" value="approved" />
            <el-option label="复核退回" value="rejected" />
          </el-select>
          <el-select
            v-model="sampleFilters.datasetSplit"
            clearable
            size="small"
            placeholder="数据分层"
            @change="resetAndLoadSamples"
          >
            <el-option label="Development" value="development" />
            <el-option label="Holdout" value="holdout" />
          </el-select>
          <el-input
            v-model="sampleFilters.search"
            clearable
            size="small"
            placeholder="项目名称或编号"
            @keyup.enter="resetAndLoadSamples"
            @clear="resetAndLoadSamples"
          />
          <el-button size="small" @click="resetAndLoadSamples">查询</el-button>
        </div>
        <el-table
          :data="calibrationSamples"
          size="small"
          class="calibration-sample-table"
        >
          <el-table-column label="项目/研判" min-width="210">
            <template #default="{ row }">
              <strong>{{ row.project_name }}</strong>
              <small>{{ row.assessment_uuid }}</small>
            </template>
          </el-table-column>
          <el-table-column label="分层" width="115">
            <template #default="{ row }">
              {{ calibrationSplitLabel(row.dataset_split) }}
            </template>
          </el-table-column>
          <el-table-column label="金标结论" min-width="150">
            <template #default="{ row }">
              {{ recommendationLabel(row.expected_decision) }}
              <el-tag
                v-if="row.hard_stop_expected"
                type="danger"
                effect="plain"
                size="small"
              >
                硬红线
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="复核" width="110">
            <template #default="{ row }">
              <el-tag :type="reviewStatusType(row.review_status)" effect="plain">
                {{ reviewStatusLabel(row.review_status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="标注/复核人" width="125">
            <template #default="{ row }">
              <span>{{ row.created_by_username || `#${row.created_by}` }}</span>
              <small>
                / {{ row.review?.reviewed_by_username
                  || (row.review?.reviewed_by ? `#${row.review.reviewed_by}` : '待分配') }}
              </small>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <template v-if="row.can_review">
                <el-button type="success" link @click="reviewSample(row, 'approved')">
                  通过
                </el-button>
                <el-button type="danger" link @click="reviewSample(row, 'rejected')">
                  退回
                </el-button>
              </template>
              <span v-else class="candidate-frozen">
                {{ row.review_status === 'pending' ? '需他人复核' : '已冻结' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-if="calibrationSampleTotal > sampleFilters.pageSize"
          v-model:current-page="sampleFilters.page"
          :page-size="sampleFilters.pageSize"
          :total="calibrationSampleTotal"
          layout="prev, pager, next, total"
          small
          @current-change="loadCalibrationSamples"
        />
      </section>
      <section class="candidate-proposals">
        <div class="candidate-proposal-heading">
          <div>
            <strong>候选提案</strong>
            <small>只调分数阈值；权重、覆盖率与硬红线保持冻结。</small>
          </div>
          <div class="candidate-proposal-controls">
            <el-select
              v-model="selectedCalibrationDatasetUuid"
              size="small"
              placeholder="选择冻结数据集"
              style="width: 260px"
            >
              <el-option
                v-for="dataset in calibrationDatasets"
                :key="dataset.dataset_uuid"
                :label="dataset.dataset_version"
                :value="dataset.dataset_uuid"
              />
            </el-select>
            <el-button
              v-if="calibrationCanManage"
              type="primary"
              plain
              size="small"
              :disabled="!selectedCalibrationDatasetUuid"
              :loading="generatingCandidate"
              @click="generatePolicyCandidate"
            >
              生成候选标准
            </el-button>
          </div>
        </div>
        <div v-if="calibrationDatasets.length" class="frozen-dataset-strip">
          <span>已冻结数据集</span>
          <el-tag
            v-for="dataset in calibrationDatasets"
            :key="dataset.dataset_uuid"
            effect="plain"
            size="small"
          >
            {{ dataset.dataset_version }}
            · {{ dataset.quality_report?.approved_case_count || 0 }}个样本
          </el-tag>
        </div>
        <el-empty
          v-if="!candidateProposals.length"
          :image-size="54"
          description="暂无候选提案"
        />
        <el-table
          v-else
          :data="candidateProposals"
          size="small"
          class="candidate-proposal-table"
        >
          <el-table-column label="候选版本" min-width="210">
            <template #default="{ row }">
              <strong>{{ row.candidate_version }}</strong>
              <small>基于 {{ row.base_policy_version }}</small>
              <small>
                数据集 {{ row.calibration_dataset?.dataset_version || '旧版未绑定' }}
              </small>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="candidateStatusType(row.status)" effect="plain">
                {{ candidateStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="阈值变化" min-width="170">
            <template #default="{ row }">
              {{ candidateThresholdText(row) }}
            </template>
          </el-table-column>
          <el-table-column label="Development" width="130">
            <template #default="{ row }">
              {{ candidateDevelopmentAccuracy(row) }}
            </template>
          </el-table-column>
          <el-table-column label="Holdout盲测" width="150">
            <template #default="{ row }">
              {{ candidateBlindResult(row) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="calibrationCanManage && row.status === 'draft'"
                type="warning"
                link
                :loading="blindEvaluatingUuid === row.proposal_uuid"
                @click="blindEvaluateCandidate(row)"
              >
                执行一次盲测
              </el-button>
              <span v-else class="candidate-frozen">已冻结</span>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </section>

    <div class="assessment-layout">
      <section class="assessment-list">
        <div class="section-heading">
          <strong>研判记录</strong>
          <small>{{ assessments.length }} 条</small>
        </div>
        <el-empty v-if="!assessments.length && !loading" description="暂无研判记录" />
        <button
          v-for="item in assessments"
          :key="item.assessment_uuid"
          type="button"
          :class="['assessment-item', { active: selected?.assessment_uuid === item.assessment_uuid }]"
          @click="openAssessment(item)"
        >
          <div>
            <el-tag :type="statusTag(item.status)" effect="plain" size="small">
              {{ statusLabel(item.status) }}
            </el-tag>
            <span>报告 v{{ item.report_version }}</span>
          </div>
          <strong>{{ recommendationLabel(item.policy_evaluation?.decision || item.recommendation) }}</strong>
          <small>资料 v{{ item.manifest_version }} · 标准 {{ item.policy_version }} · {{ formatDate(item.created_at) }}</small>
        </button>
      </section>

      <section v-loading="detailLoading" class="assessment-detail">
        <el-empty v-if="!selected" description="选择一条研判记录查看详情" />
        <template v-else>
          <div class="detail-heading">
            <div>
              <span>当前结论</span>
              <h3>{{ recommendationLabel(selected.policy_evaluation?.decision || selected.recommendation) }}</h3>
              <small>
                {{ selected.assessment?.project_summary || selected.analysis_goal }}
                · 标准 {{ selected.policy_version }}
              </small>
            </div>
            <el-tag :type="statusTag(selected.status)" effect="plain">
              {{ statusLabel(selected.status) }}
            </el-tag>
          </div>

          <div class="decision-metrics">
            <div>
              <span>置信度</span>
              <strong>{{ confidenceText }}</strong>
            </div>
            <div>
              <span>证据门</span>
              <strong>{{ gateLabel(selected.gate_status) }}</strong>
            </div>
            <div>
              <span>ReAct 循环</span>
              <strong>{{ activeRun?.state_summary?.reasoning_loop_count ?? liveTraceStats.react }}</strong>
            </div>
            <div>
              <span>Tool 调用</span>
              <strong>{{ activeRun?.state_summary?.tool_call_count ?? liveTraceStats.tools }}</strong>
            </div>
            <div>
              <span>立项评分</span>
              <strong>{{ policyScoreText }}</strong>
            </div>
            <div>
              <span>信息覆盖率</span>
              <strong>{{ policyCoverageText }}</strong>
            </div>
          </div>

          <BidIntakeRunGraph
            v-if="activeRun"
            :run="activeRun"
          />

          <el-alert
            v-if="selected.policy_evaluation?.hard_rule_hits?.length"
            class="gate-alert"
            type="error"
            show-icon
            :closable="false"
            :title="`命中 ${selected.policy_evaluation.hard_rule_hits.length} 条总经办硬门槛`"
          >
            <ul>
              <li
                v-for="hit in selected.policy_evaluation.hard_rule_hits"
                :key="hit.rule_id"
              >
                {{ hit.message }}
              </li>
            </ul>
          </el-alert>

          <el-alert
            v-if="gateIssues.length"
            class="gate-alert"
            type="warning"
            show-icon
            :closable="false"
            :title="`证据门发现 ${gateIssues.length} 项问题`"
          >
            <ul>
              <li v-for="issue in gateIssues" :key="`${issue.code}-${issue.path || ''}`">
                {{ issue.message }}（{{ issue.code }}）
              </li>
            </ul>
          </el-alert>

          <el-tabs class="assessment-tabs">
            <el-tab-pane label="经营评分">
              <el-table
                :data="selected.policy_evaluation?.factor_results || []"
                row-key="factor_id"
                size="small"
                empty-text="暂无经营因素评分"
              >
                <el-table-column prop="name" label="因素" min-width="150" />
                <el-table-column label="评价" width="100">
                  <template #default="{ row }">
                    <el-tag :type="factorRatingTag(row.rating)" effect="plain" size="small">
                      {{ factorRatingLabel(row.rating) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="weight" label="权重" width="70">
                  <template #default="{ row }">{{ row.weight }}%</template>
                </el-table-column>
                <el-table-column prop="weighted_score" label="得分" width="70" />
                <el-table-column prop="summary" label="依据摘要" min-width="260" />
                <el-table-column label="来源" width="100">
                  <template #default="{ row }">{{ factorSourceLabel(row.source_type) }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="维度研判">
              <el-table
                :data="selected.assessment?.dimension_reviews || []"
                row-key="dimension"
                size="small"
                empty-text="暂无维度研判"
              >
                <el-table-column label="维度" width="150">
                  <template #default="{ row }">{{ dimensionLabel(row.dimension) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag :type="dimensionTag(row.status)" effect="plain" size="small">
                      {{ dimensionStatusLabel(row.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="summary" label="研判摘要" min-width="280" />
                <el-table-column label="证据" width="90">
                  <template #default="{ row }">{{ row.evidence_refs?.length || 0 }} 条</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane :label="`关键风险 ${riskRows.length}`">
              <el-table :data="riskRows" row-key="claim_id" size="small" empty-text="暂无关键风险">
                <el-table-column prop="title" label="风险" min-width="180" />
                <el-table-column label="等级" width="100">
                  <template #default="{ row }">
                    <el-tag :type="severityTag(row.severity)" effect="plain" size="small">
                      {{ severityLabel(row.severity) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="conclusion" label="结论" min-width="300" />
                <el-table-column label="证据" width="90">
                  <template #default="{ row }">{{ row.evidence_refs?.length || 0 }} 条</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="运行轨迹">
              <el-timeline>
                <el-timeline-item
                  v-for="event in auditRunEvents"
                  :key="event.event_uuid"
                  :timestamp="formatDate(event.created_at)"
                  placement="top"
                >
                  <strong>{{ eventLabel(event.event_type) }}</strong>
                  <p>{{ event.message || `${statusLabel(event.status)} · ${event.phase || '-'}` }}</p>
                </el-timeline-item>
              </el-timeline>
            </el-tab-pane>
          </el-tabs>

          <section class="calibration-label-panel">
            <div>
              <span>总经办校准金标</span>
              <strong>
                {{ calibrationLabel
                  ? calibrationLabel.masked
                    ? '已封存为盲测金标'
                    : recommendationLabel(calibrationLabel.expected_decision)
                  : '尚未独立标注' }}
              </strong>
              <small v-if="calibrationLabel">
                {{ calibrationSplitLabel(calibrationLabel.dataset_split) }}
                · v{{ calibrationLabel.label_version }}
                · {{ calibrationBasisLabel(calibrationLabel.label_basis) }}
              </small>
              <small v-else>
                金标用于历史回放，不能直接复制Agent建议。
              </small>
              <p v-if="calibrationLabel && !calibrationLabel.masked">{{ calibrationLabel.rationale }}</p>
            </div>
            <el-button
              v-if="calibrationCanManage"
              :disabled="!selected.assessment || !selected.policy_evaluation"
              @click="openCalibrationDialog"
            >
              {{ calibrationLabel ? '修订金标' : '记录金标' }}
            </el-button>
          </section>

          <section v-if="activeRun?.status === 'waiting_human'" class="human-review-panel">
            <div>
              <span>Human-in-the-loop</span>
              <strong>等待人工决策</strong>
              <small>决策会先持久化，再从当前 LangGraph Checkpoint 恢复。</small>
            </div>
            <el-button type="primary" @click="openDecisionDialog">开始审核</el-button>
          </section>

          <section v-else-if="activeRun?.status === 'failed'" class="human-review-panel error">
            <div>
              <span>运行异常</span>
              <strong>{{ activeRun.error_code || 'AGENT_EXECUTION_FAILED' }}</strong>
              <small>{{ activeRun.error_message || '可在重试预算内从最近 Checkpoint 恢复。' }}</small>
            </div>
            <el-button
              type="warning"
              :disabled="activeRun.attempt_count >= activeRun.max_attempts"
              :loading="retrying"
              @click="retryRun"
            >
              从断点重试
            </el-button>
          </section>
        </template>
      </section>
    </div>

    <el-dialog v-model="createDialog.visible" title="发起立项研判" width="620px">
      <el-form label-position="top">
        <el-form-item label="研判目标">
          <el-input
            v-model="createDialog.analysisGoal"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="异常重试上限">
          <el-input-number v-model="createDialog.maxAttempts" :min="1" :max="10" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!createDialog.analysisGoal.trim()"
          @click="createAssessment"
        >
          创建并排队
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="decisionDialog.visible" title="人工立项决策" width="680px">
      <el-alert
        v-if="approvalBlocked"
        type="warning"
        show-icon
        :closable="false"
        title="当前证据门存在审批阻断项，可选择驳回、补资料或重新研判。"
      />
      <el-form label-position="top">
        <el-form-item label="决策">
          <el-select v-model="decisionDialog.action" style="width: 100%">
            <el-option
              v-for="option in humanActionOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
              :disabled="option.approval && approvalBlocked"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="decisionDialog.note"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="填写决策依据、补充要求或驳回原因"
          />
        </el-form-item>
        <el-form-item v-if="decisionDialog.action === 'approved_with_conditions'" label="附加条件">
          <el-input
            v-model="decisionDialog.conditionsText"
            type="textarea"
            :rows="3"
            placeholder="每行填写一个条件"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="decisionDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="deciding" @click="submitDecision">
          确认并恢复 Agent
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="calibrationDialog.visible" title="记录总经办校准金标" width="720px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="请独立判断历史项目，不要直接复制上方Agent建议。Holdout样本不得参与后续逐项调参。"
      />
      <el-form label-position="top" class="calibration-form">
        <div class="calibration-form-grid">
          <el-form-item label="金标结论">
            <el-select v-model="calibrationDialog.expectedDecision" style="width: 100%">
              <el-option
                v-for="option in calibrationDecisionOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="数据分层">
            <el-select
              v-model="calibrationDialog.datasetSplit"
              :disabled="Boolean(calibrationLabel)"
              style="width: 100%"
            >
              <el-option label="开发集（可用于调参）" value="development" />
              <el-option label="Holdout（仅最终盲测）" value="holdout" />
            </el-select>
          </el-form-item>
          <el-form-item label="判断依据">
            <el-select v-model="calibrationDialog.labelBasis" style="width: 100%">
              <el-option label="投标前专家复核" value="pre_bid_expert_review" />
              <el-option label="项目实际结果" value="actual_project_outcome" />
              <el-option label="专家复核 + 实际结果" value="combined" />
            </el-select>
          </el-form-item>
          <el-form-item label="硬红线">
            <el-switch
              v-model="calibrationDialog.hardStopExpected"
              active-text="应触发硬红线"
              inactive-text="不要求硬红线"
            />
          </el-form-item>
        </div>
        <el-form-item label="独立判断理由">
          <el-input
            v-model="calibrationDialog.rationale"
            type="textarea"
            :rows="4"
            maxlength="4000"
            show-word-limit
            placeholder="填写总经办判断依据、红线或需要补充的关键信息"
          />
        </el-form-item>
        <template v-if="calibrationNeedsOutcome">
          <div class="calibration-form-grid">
            <el-form-item label="是否实际投标">
              <el-select v-model="calibrationDialog.bidSubmitted" clearable style="width: 100%">
                <el-option label="是" :value="true" />
                <el-option label="否" :value="false" />
              </el-select>
            </el-form-item>
            <el-form-item label="是否中标">
              <el-select v-model="calibrationDialog.wonBid" clearable style="width: 100%">
                <el-option label="是" :value="true" />
                <el-option label="否" :value="false" />
              </el-select>
            </el-form-item>
            <el-form-item label="实际利润率（%）">
              <el-input-number
                v-model="calibrationDialog.realizedMarginRate"
                :min="-100"
                :max="100"
                :precision="2"
                controls-position="right"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="是否发生逾期回款">
              <el-select v-model="calibrationDialog.paymentOverdue" clearable style="width: 100%">
                <el-option label="是" :value="true" />
                <el-option label="否" :value="false" />
              </el-select>
            </el-form-item>
            <el-form-item label="是否发生重大履约问题">
              <el-select v-model="calibrationDialog.majorDeliveryIssue" clearable style="width: 100%">
                <el-option label="是" :value="true" />
                <el-option label="否" :value="false" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="实际结果说明">
            <el-input
              v-model="calibrationDialog.outcomeNote"
              type="textarea"
              :rows="2"
              maxlength="2000"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="calibrationDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="labeling"
          :disabled="!calibrationDialog.rationale.trim()"
          @click="saveCalibrationLabel"
        >
          保存不可变金标快照
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, reactive, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import BidIntakeRunGraph from './BidIntakeRunGraph.vue'
import {
  bidIntakeApi,
  bidIntakeData,
  bidIntakeErrorMessage,
} from './bidIntakeApi'

const props = defineProps({
  projectUuid: { type: String, required: true },
  active: { type: Boolean, default: false },
})

const loading = ref(false)
const detailLoading = ref(false)
const creating = ref(false)
const deciding = ref(false)
const retrying = ref(false)
const calibrationLoading = ref(false)
const labeling = ref(false)
const generatingCandidate = ref(false)
const blindEvaluatingUuid = ref('')
const sampleOperationsLoading = ref(false)
const freezingDataset = ref(false)
const evidenceUploadRef = ref(null)
const evidenceUploadFiles = ref([])
const evidenceUploading = ref(false)
const evidenceJobsLoading = ref(false)
const retryingEvidenceJobUuid = ref('')
const evidenceJobs = ref([])
const readiness = ref(null)
const assessments = ref([])
const selected = ref(null)
const calibrationReport = ref(null)
const calibrationLabel = ref(null)
const calibrationCanManage = ref(false)
const candidatePolicyVersion = ref('')
const candidateProposals = ref([])
const calibrationSamples = ref([])
const calibrationSampleTotal = ref(0)
const calibrationQuality = ref(null)
const calibrationDatasets = ref([])
const selectedCalibrationDatasetUuid = ref('')
let pollTimer = null
let evidencePollTimer = null
const evidenceJobStatuses = new Map()

const evidenceAccept = '.pdf,.docx,.xlsx,.xlsm,.txt,.md'
const evidenceAllowedExtensions = new Set(
  evidenceAccept.split(',').map((item) => item.slice(1)),
)
const evidenceFileTypeOptions = [
  { value: 'auto', label: '等待自动识别' },
  { value: 'tender_document', label: '招标文件' },
  { value: 'clarification', label: '答疑/澄清文件' },
  { value: 'addendum', label: '补遗/变更文件' },
  { value: 'contract', label: '施工合同' },
  { value: 'drawing', label: '项目图纸' },
  { value: 'bill_of_quantities', label: '工程量清单' },
  { value: 'other', label: '其他项目资料' },
]
const createDialog = reactive({
  visible: false,
  analysisGoal: '判断该招标项目是否值得进入报价立项。',
  maxAttempts: 3,
})

const decisionDialog = reactive({
  visible: false,
  action: 'approved',
  note: '',
  conditionsText: '',
})

const calibrationDialog = reactive({
  visible: false,
  expectedDecision: 'need_supplement',
  datasetSplit: 'development',
  labelBasis: 'pre_bid_expert_review',
  hardStopExpected: false,
  rationale: '',
  bidSubmitted: null,
  wonBid: null,
  realizedMarginRate: null,
  paymentOverdue: null,
  majorDeliveryIssue: null,
  outcomeNote: '',
})

const sampleFilters = reactive({
  reviewStatus: '',
  datasetSplit: '',
  search: '',
  page: 1,
  pageSize: 10,
})

const humanActionOptions = [
  { value: 'approved', label: '批准立项', approval: true },
  { value: 'approved_with_conditions', label: '有条件批准', approval: true },
  { value: 'rejected', label: '不予立项' },
  { value: 'supplement_requested', label: '补充资料后再审' },
  { value: 'research_requested', label: '要求重新研判' },
]
const calibrationDecisionOptions = [
  { value: 'recommend_quote', label: '应进入报价立项' },
  { value: 'conditional_quote', label: '应有条件进入报价' },
  { value: 'recommend_no_quote', label: '应不参与报价' },
  { value: 'need_supplement', label: '应补充资料后再判断' },
]

const activeRun = computed(() => selected.value?.runs?.[0] || null)
const liveTraceStats = computed(() => {
  const latestSteps = new Map()
  ;(activeRun.value?.events || []).forEach((event) => {
    const payload = event?.payload
    if (
      payload?.trace_schema_version !== 'bid-intake-agent-trace/v1'
      || !payload.step_id
    ) return
    latestSteps.set(payload.step_id, payload)
  })
  const steps = [...latestSteps.values()]
  return {
    react: steps.filter((item) => item.kind === 'react').length,
    tools: steps.filter((item) => item.kind === 'tool').length,
  }
})
const auditRunEvents = computed(() => (
  (activeRun.value?.events || []).filter((event) => (
    event?.payload?.trace_schema_version !== 'bid-intake-agent-trace/v1'
  ))
))
const gateIssues = computed(() => selected.value?.gate_result?.issues || [])
const riskRows = computed(() => selected.value?.assessment?.risks || [])
const approvalBlocked = computed(() => {
  const blockers = new Set([
    'REQUIRED_DIMENSION_MISSING',
    'EVIDENCE_VALIDATION_UNAVAILABLE',
    'EVIDENCE_REF_INVALID',
    'HIGH_RISK_EVIDENCE_MISSING',
    'HIGH_RISK_CONTEXT_NOT_READ',
    'POLICY_FACTOR_EVIDENCE_MISSING',
    'POLICY_FACTOR_CONTEXT_NOT_READ',
    'POLICY_REQUIRES_MANUAL_REVIEW',
    'AGENT_TERMINATED_EARLY',
  ])
  return selected.value?.gate_status === 'supplement_required'
    || gateIssues.value.some((item) => blockers.has(item.code))
})
const confidenceText = computed(() => {
  const value = Number(selected.value?.assessment?.confidence)
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '-'
})
const policyScoreText = computed(() => {
  const value = Number(selected.value?.policy_evaluation?.score)
  return Number.isFinite(value) ? `${value.toFixed(2)}分` : '-'
})
const policyCoverageText = computed(() => {
  const value = Number(selected.value?.policy_evaluation?.coverage)
  return Number.isFinite(value) ? `${value.toFixed(0)}%` : '-'
})
const readinessLabel = computed(() => {
  if (!readiness.value?.runtime_enabled) return '未启用'
  return readiness.value?.ready_to_start ? '可以研判' : '尚未就绪'
})
const readinessDetail = computed(() => {
  if (!readiness.value) return '正在检查运行条件'
  return readiness.value.ready_to_start
    ? '模型、MCP、Worker 与资料均已就绪'
    : '请处理下方阻断项'
})
const readinessBlockerText = computed(() => (
  (readiness.value?.blockers || []).map(blockerLabel).join('；')
))
const latestWorkerText = computed(() => {
  const latest = readiness.value?.worker?.latest
  if (!latest) return '未发现在线 Worker'
  return `${workerStatusLabel(latest.status)} · ${formatDate(latest.last_seen_at)}`
})
const calibrationMetricScope = computed(() => {
  const candidate = calibrationReport.value?.candidate
  if (!candidate) return null
  return candidate.holdout?.case_count
    ? candidate.holdout
    : candidate.overall
})
const calibrationAccuracyText = computed(() => {
  const value = Number(calibrationMetricScope.value?.exact_accuracy)
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '-'
})
const calibrationHardRecallText = computed(() => {
  const value = Number(calibrationMetricScope.value?.hard_stop_recall)
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '-'
})
const calibrationGateLabel = computed(() => {
  if (!calibrationReport.value) return '正在检查'
  return calibrationReport.value.release_gate?.passed
    ? '候选标准达到发布评审门槛'
    : '正在积累金标样本'
})
const calibrationNeedsOutcome = computed(() => (
  ['actual_project_outcome', 'combined'].includes(calibrationDialog.labelBasis)
))
const calibrationQualityFailedText = computed(() => {
  const failed = (calibrationQuality.value?.checks || [])
    .filter((item) => !item.passed)
  return failed.length ? `还有${failed.length}项未满足` : '可以冻结'
})
const calibrationQualityFailedMessages = computed(() => (
  (calibrationQuality.value?.checks || [])
    .filter((item) => !item.passed)
    .map((item) => item.message)
))
const activeEvidenceJobs = computed(() => (
  evidenceJobs.value.filter((item) => (
    ['queued', 'running', 'retryable'].includes(item.status)
  ))
))
const evidencePipelineSummary = computed(() => {
  const completed = evidenceJobs.value.filter((item) => item.status === 'completed').length
  const failed = evidenceJobs.value.filter((item) => item.status === 'failed').length
  const parts = [`已完成 ${completed}`]
  if (activeEvidenceJobs.value.length) parts.push(`处理中 ${activeEvidenceJobs.value.length}`)
  if (failed) parts.push(`失败 ${failed}`)
  return parts.join(' · ')
})
const shouldPoll = computed(() => {
  const status = activeRun.value?.status
  return props.active && ['queued', 'running', 'resume_queued'].includes(status)
})
const shouldPollEvidence = computed(() => (
  props.active && activeEvidenceJobs.value.length > 0
))

watch(
  () => [props.projectUuid, props.active],
  async ([projectUuid, active]) => {
    stopPolling()
    stopEvidencePolling()
    evidenceJobStatuses.clear()
    evidenceJobs.value = []
    evidenceUploadFiles.value = []
    evidenceUploadRef.value?.clearFiles?.()
    selected.value = null
    assessments.value = []
    calibrationLabel.value = null
    calibrationCanManage.value = false
    candidateProposals.value = []
    calibrationSamples.value = []
    calibrationQuality.value = null
    calibrationDatasets.value = []
    selectedCalibrationDatasetUuid.value = ''
    if (projectUuid && active) await refresh()
  },
  { immediate: true },
)

watch(shouldPoll, (value) => {
  stopPolling()
  if (value) pollTimer = window.setInterval(refreshSelected, 1200)
})

watch(shouldPollEvidence, (value) => {
  stopEvidencePolling()
  if (value) {
    evidencePollTimer = window.setInterval(
      () => refreshEvidenceProgress(true),
      2500,
    )
  }
})

onBeforeUnmount(() => {
  stopPolling()
  stopEvidencePolling()
})

async function loadReadiness() {
  const response = await bidIntakeApi.readiness(props.projectUuid)
  readiness.value = bidIntakeData(response)
}

async function loadEvidenceJobs(silent = false) {
  if (!props.projectUuid || !props.active) return
  if (!silent) evidenceJobsLoading.value = true
  try {
    const response = await bidIntakeApi.evidenceParseJobs(props.projectUuid, { limit: 50 })
    const nextJobs = bidIntakeData(response) || []
    const newlyCompleted = nextJobs.filter((item) => (
      item.status === 'completed'
      && evidenceJobStatuses.has(item.job_uuid)
      && evidenceJobStatuses.get(item.job_uuid) !== 'completed'
    ))
    evidenceJobs.value = nextJobs
    nextJobs.forEach((item) => evidenceJobStatuses.set(item.job_uuid, item.status))
    if (newlyCompleted.length) {
      ElMessage.success(
        `${newlyCompleted.length} 份资料解析完成，证据清单已自动更新`,
      )
    }
  } catch (error) {
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '解析任务加载失败'))
    }
    throw error
  } finally {
    if (!silent) evidenceJobsLoading.value = false
  }
}

async function refresh() {
  if (!props.projectUuid || !props.active) return
  loading.value = true
  try {
    await Promise.all([
      loadReadiness(),
      loadEvidenceJobs(true),
    ])
    await loadCalibrationReport(true)
    await Promise.all([
      loadCalibrationCandidates(true),
      loadCalibrationDatasets(true),
      calibrationCanManage.value
        ? loadCalibrationOperations(true)
        : Promise.resolve(),
    ])
    if (!readiness.value?.runtime_enabled) {
      assessments.value = []
      selected.value = null
      return
    }
    const response = await bidIntakeApi.list(props.projectUuid, { limit: 50 })
    assessments.value = bidIntakeData(response) || []
    if (selected.value?.assessment_uuid) {
      await refreshSelected()
    } else if (assessments.value.length) {
      await openAssessment(assessments.value[0])
    }
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '研判工作台加载失败'))
  } finally {
    loading.value = false
  }
}

function removeEvidenceUploadFile(uploadFile) {
  evidenceUploadFiles.value = evidenceUploadFiles.value.filter(
    (item) => item.uid !== uploadFile?.uid,
  )
}

function handleEvidenceFileChange(uploadFile) {
  const raw = uploadFile?.raw
  const extension = String(uploadFile?.name || '')
    .split('.')
    .pop()
    ?.toLowerCase()
  if (!raw || !extension || !evidenceAllowedExtensions.has(extension)) {
    removeEvidenceUploadFile(uploadFile)
    ElMessage.warning('仅支持 PDF、DOCX、XLSX、XLSM、TXT、MD 文件')
    return
  }
  if (!raw.size) {
    removeEvidenceUploadFile(uploadFile)
    ElMessage.warning(`${uploadFile.name} 是空文件，无法解析`)
  }
}

function handleEvidenceFileExceed() {
  ElMessage.warning('单次最多选择 10 份资料，请分批上传')
}

async function uploadEvidenceFiles() {
  const projectUuid = props.projectUuid
  const queuedFiles = evidenceUploadFiles.value.filter((item) => item.raw)
  if (!projectUuid || !queuedFiles.length) return
  evidenceUploading.value = true
  const succeededUids = new Set()
  let createdCount = 0
  let reusedCount = 0
  const failures = []
  try {
    for (const uploadFile of queuedFiles) {
      const formData = new FormData()
      formData.append('file', uploadFile.raw, uploadFile.name)
      formData.append('file_type', 'auto')
      try {
        const response = await bidIntakeApi.createEvidenceParseJob(
          projectUuid,
          formData,
          { timeout: 120000 },
        )
        const created = bidIntakeData(response) || {}
        succeededUids.add(uploadFile.uid)
        if (created.idempotent) reusedCount += 1
        else createdCount += 1
      } catch (error) {
        failures.push({
          filename: uploadFile.name,
          message: bidIntakeErrorMessage(error, '上传或创建解析任务失败'),
        })
      }
    }
    evidenceUploadFiles.value = evidenceUploadFiles.value.filter(
      (item) => !succeededUids.has(item.uid),
    )
    if (!evidenceUploadFiles.value.length) {
      evidenceUploadRef.value?.clearFiles?.()
    }
    if (props.projectUuid === projectUuid && props.active) {
      try {
        await Promise.all([
          loadEvidenceJobs(true),
          loadReadiness(),
        ])
      } catch (error) {
        ElMessage.error(
          bidIntakeErrorMessage(error, '任务已提交，但最新进度刷新失败'),
        )
      }
    }
    if (createdCount || reusedCount) {
      const parts = []
      if (createdCount) parts.push(`${createdCount} 份已进入解析队列`)
      if (reusedCount) parts.push(`${reusedCount} 份复用已有任务`)
      ElMessage.success(parts.join('，'))
    }
    failures.forEach((item) => {
      ElMessage.error(`${item.filename}：${item.message}`)
    })
  } finally {
    evidenceUploading.value = false
  }
}

async function refreshEvidenceProgress(silent = false) {
  if (!props.projectUuid || !props.active || evidenceJobsLoading.value) return
  if (!silent) evidenceJobsLoading.value = true
  try {
    await loadEvidenceJobs(true)
    await loadReadiness()
  } catch (error) {
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '解析进度刷新失败'))
    }
  } finally {
    if (!silent) evidenceJobsLoading.value = false
  }
}

async function retryEvidenceParseJob(job) {
  if (!job?.job_uuid) return
  retryingEvidenceJobUuid.value = job.job_uuid
  try {
    await bidIntakeApi.retryEvidenceParseJob(
      props.projectUuid,
      job.job_uuid,
    )
    ElMessage.success('解析任务已重新进入后台队列')
    await refreshEvidenceProgress(true)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '解析任务重试失败'))
  } finally {
    retryingEvidenceJobUuid.value = ''
  }
}

async function openAssessment(item) {
  if (!item?.assessment_uuid) return
  detailLoading.value = true
  try {
    const response = await bidIntakeApi.detail(props.projectUuid, item.assessment_uuid)
    selected.value = bidIntakeData(response)
    await loadCalibrationLabel()
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '研判详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

async function refreshSelected() {
  const assessmentUuid = selected.value?.assessment_uuid
  if (!assessmentUuid || !props.active) return
  try {
    const response = await bidIntakeApi.detail(props.projectUuid, assessmentUuid)
    selected.value = bidIntakeData(response)
    const index = assessments.value.findIndex((item) => item.assessment_uuid === assessmentUuid)
    if (index >= 0) assessments.value[index] = { ...assessments.value[index], ...selected.value }
  } catch (error) {
    stopPolling()
    ElMessage.error(bidIntakeErrorMessage(error, '研判状态刷新失败'))
  }
}

async function loadCalibrationReport(silent = false) {
  calibrationLoading.value = true
  try {
    const response = await bidIntakeApi.calibrationReport(
      candidatePolicyVersion.value
        ? { candidate_policy_version: candidatePolicyVersion.value }
        : {},
    )
    calibrationReport.value = bidIntakeData(response)
    calibrationCanManage.value = Boolean(
      calibrationReport.value?.can_manage,
    )
    if (!candidatePolicyVersion.value) {
      candidatePolicyVersion.value = calibrationReport.value?.candidate_policy_version || ''
    }
  } catch (error) {
    calibrationReport.value = null
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '标准校准报告加载失败'))
    }
  } finally {
    calibrationLoading.value = false
  }
}

async function loadCalibrationCandidates(silent = false) {
  try {
    const response = await bidIntakeApi.calibrationCandidates({ limit: 20 })
    candidateProposals.value = bidIntakeData(response) || []
  } catch (error) {
    candidateProposals.value = []
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '候选标准提案加载失败'))
    }
  }
}

async function loadCalibrationDatasets(silent = false) {
  try {
    const response = await bidIntakeApi.calibrationDatasets({ limit: 20 })
    calibrationDatasets.value = bidIntakeData(response) || []
    if (
      !calibrationDatasets.value.some(
        (item) => item.dataset_uuid === selectedCalibrationDatasetUuid.value,
      )
    ) {
      selectedCalibrationDatasetUuid.value = (
        calibrationDatasets.value[0]?.dataset_uuid || ''
      )
    }
  } catch (error) {
    calibrationDatasets.value = []
    selectedCalibrationDatasetUuid.value = ''
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '冻结数据集加载失败'))
    }
  }
}

async function loadCalibrationOperations(silent = false) {
  sampleOperationsLoading.value = true
  try {
    await Promise.all([
      loadCalibrationQuality(true),
      loadCalibrationSamples(true),
    ])
  } catch (error) {
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '金标样本运营数据加载失败'))
    }
  } finally {
    sampleOperationsLoading.value = false
  }
}

async function loadCalibrationQuality(silent = false) {
  try {
    const response = await bidIntakeApi.calibrationQuality()
    calibrationQuality.value = bidIntakeData(response)
  } catch (error) {
    calibrationQuality.value = null
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '数据集质量门加载失败'))
    }
    throw error
  }
}

async function loadCalibrationSamples(silent = false) {
  try {
    const params = {
      page: sampleFilters.page,
      page_size: sampleFilters.pageSize,
    }
    if (sampleFilters.reviewStatus) params.review_status = sampleFilters.reviewStatus
    if (sampleFilters.datasetSplit) params.dataset_split = sampleFilters.datasetSplit
    if (sampleFilters.search.trim()) params.search = sampleFilters.search.trim()
    const response = await bidIntakeApi.calibrationSamples(params)
    calibrationSamples.value = bidIntakeData(response) || []
    calibrationSampleTotal.value = Number(
      response?.data?.total ?? calibrationSamples.value.length,
    )
  } catch (error) {
    calibrationSamples.value = []
    calibrationSampleTotal.value = 0
    if (!silent) {
      ElMessage.error(bidIntakeErrorMessage(error, '金标样本池加载失败'))
    }
    throw error
  }
}

async function resetAndLoadSamples() {
  sampleFilters.page = 1
  try {
    await loadCalibrationSamples()
  } catch {
    // Error message is handled by loadCalibrationSamples.
  }
}

async function loadCalibrationLabel() {
  const assessmentUuid = selected.value?.assessment_uuid
  if (!assessmentUuid) {
    calibrationLabel.value = null
    return
  }
  try {
    const response = await bidIntakeApi.calibrationLabel(
      props.projectUuid,
      assessmentUuid,
    )
    const data = bidIntakeData(response) || {}
    calibrationLabel.value = data.label || null
    if (typeof data.can_manage === 'boolean') {
      calibrationCanManage.value = data.can_manage
    }
  } catch (error) {
    calibrationLabel.value = null
    ElMessage.error(bidIntakeErrorMessage(error, '校准金标加载失败'))
  }
}

async function generatePolicyCandidate() {
  if (!selectedCalibrationDatasetUuid.value) {
    ElMessage.warning('请先选择一个已冻结的校准数据集')
    return
  }
  generatingCandidate.value = true
  try {
    const response = await bidIntakeApi.generateCalibrationCandidate({
      dataset_uuid: selectedCalibrationDatasetUuid.value,
    })
    const candidate = bidIntakeData(response)
    ElMessage.success(
      candidate?.status === 'draft'
        ? '候选标准已冻结为待审提案'
        : '相同数据集的候选提案已经存在',
    )
    await loadCalibrationCandidates(true)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '候选标准生成失败'))
  } finally {
    generatingCandidate.value = false
  }
}

async function reviewSample(sample, action) {
  let note = ''
  try {
    const result = await ElMessageBox.prompt(
      action === 'approved'
        ? '请填写独立复核依据。复核通过后该版本不能覆盖修改。'
        : '请填写退回原因。标注人应修订形成新的金标版本。',
      action === 'approved' ? '复核通过' : '复核退回',
      {
        confirmButtonText: action === 'approved' ? '确认通过' : '确认退回',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputValidator: (value) => Boolean(String(value || '').trim()) || '复核意见不能为空',
      },
    )
    note = String(result.value || '').trim()
  } catch {
    return
  }
  try {
    await bidIntakeApi.reviewCalibrationLabel(
      sample.label_uuid,
      { action, note },
    )
    ElMessage.success(action === 'approved' ? '金标复核已通过' : '金标已退回复核')
    await loadCalibrationOperations(true)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '金标复核失败'))
  }
}

async function freezeCalibrationDataset() {
  try {
    await ElMessageBox.confirm(
      '冻结后该数据集的样本、分层和金标答案都不可变化。是否继续？',
      '冻结校准数据集',
      {
        confirmButtonText: '确认冻结',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  freezingDataset.value = true
  try {
    const response = await bidIntakeApi.freezeCalibrationDataset({
      freeze_note: '由总经办在样本运营页确认冻结。',
    })
    const dataset = bidIntakeData(response)
    ElMessage.success('校准数据集已形成不可变版本')
    await loadCalibrationDatasets(true)
    selectedCalibrationDatasetUuid.value = dataset?.dataset_uuid || ''
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '数据集冻结失败'))
  } finally {
    freezingDataset.value = false
  }
}

async function blindEvaluateCandidate(candidate) {
  try {
    await ElMessageBox.confirm(
      'Holdout只允许执行一次聚合盲测，结果形成后不可重跑。是否继续？',
      '执行Holdout盲测',
      {
        confirmButtonText: '确认盲测',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  blindEvaluatingUuid.value = candidate.proposal_uuid
  try {
    const response = await bidIntakeApi.blindEvaluateCalibrationCandidate(
      candidate.proposal_uuid,
    )
    const evaluated = bidIntakeData(response)
    const index = candidateProposals.value.findIndex(
      (item) => item.proposal_uuid === evaluated?.proposal_uuid,
    )
    if (index >= 0) candidateProposals.value[index] = evaluated
    ElMessage.success(
      evaluated?.status === 'blind_passed'
        ? '盲测达到发布评审门槛；仍需总经办独立审批'
        : '盲测未达到发布评审门槛，候选已冻结',
    )
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, 'Holdout盲测失败'))
  } finally {
    blindEvaluatingUuid.value = ''
  }
}

async function createAssessment() {
  creating.value = true
  try {
    const response = await bidIntakeApi.create(props.projectUuid, {
      analysis_goal: createDialog.analysisGoal.trim(),
      max_attempts: createDialog.maxAttempts,
    })
    const created = bidIntakeData(response)
    createDialog.visible = false
    ElMessage.success('研判任务已创建')
    await refresh()
    if (created?.assessment) await openAssessment(created.assessment)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '创建研判任务失败'))
  } finally {
    creating.value = false
  }
}

function openDecisionDialog() {
  decisionDialog.action = approvalBlocked.value ? 'supplement_requested' : 'approved'
  decisionDialog.note = ''
  decisionDialog.conditionsText = ''
  decisionDialog.visible = true
}

async function submitDecision() {
  if (!selected.value || !activeRun.value) return
  deciding.value = true
  try {
    await bidIntakeApi.decide(
      props.projectUuid,
      selected.value.assessment_uuid,
      activeRun.value.run_uuid,
      {
        decision_uuid: crypto.randomUUID(),
        action: decisionDialog.action,
        report_version: selected.value.report_version,
        manifest_version: selected.value.manifest_version,
        note: decisionDialog.note.trim() || null,
        conditions: decisionDialog.conditionsText
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean),
      },
    )
    decisionDialog.visible = false
    ElMessage.success('人工决策已保存，Agent 将从暂停点恢复')
    await refreshSelected()
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '提交人工决策失败'))
  } finally {
    deciding.value = false
  }
}

function openCalibrationDialog() {
  const label = calibrationLabel.value
  calibrationDialog.expectedDecision = (
    label?.expected_decision
    || selected.value?.policy_evaluation?.decision
    || 'need_supplement'
  )
  calibrationDialog.datasetSplit = label?.dataset_split || 'development'
  calibrationDialog.labelBasis = label?.label_basis || 'pre_bid_expert_review'
  calibrationDialog.hardStopExpected = Boolean(label?.hard_stop_expected)
  calibrationDialog.rationale = label?.rationale || ''
  calibrationDialog.bidSubmitted = label?.actual_outcome?.bid_submitted ?? null
  calibrationDialog.wonBid = label?.actual_outcome?.won_bid ?? null
  calibrationDialog.realizedMarginRate = label?.actual_outcome?.realized_margin_rate ?? null
  calibrationDialog.paymentOverdue = label?.actual_outcome?.payment_overdue ?? null
  calibrationDialog.majorDeliveryIssue = label?.actual_outcome?.major_delivery_issue ?? null
  calibrationDialog.outcomeNote = label?.actual_outcome?.note || ''
  calibrationDialog.visible = true
}

async function saveCalibrationLabel() {
  if (!selected.value) return
  if (
    calibrationDialog.hardStopExpected
    && calibrationDialog.expectedDecision !== 'recommend_no_quote'
  ) {
    ElMessage.warning('硬红线金标必须选择“应不参与报价”')
    return
  }
  labeling.value = true
  const actualOutcome = calibrationNeedsOutcome.value
    ? {
        bid_submitted: calibrationDialog.bidSubmitted,
        won_bid: calibrationDialog.wonBid,
        realized_margin_rate: calibrationDialog.realizedMarginRate,
        payment_overdue: calibrationDialog.paymentOverdue,
        major_delivery_issue: calibrationDialog.majorDeliveryIssue,
        note: calibrationDialog.outcomeNote.trim() || null,
      }
    : null
  try {
    const response = await bidIntakeApi.saveCalibrationLabel(
      props.projectUuid,
      selected.value.assessment_uuid,
      {
        expected_current_label_version: calibrationLabel.value?.label_version || 0,
        dataset_split: calibrationDialog.datasetSplit,
        label_basis: calibrationDialog.labelBasis,
        expected_decision: calibrationDialog.expectedDecision,
        hard_stop_expected: calibrationDialog.hardStopExpected,
        rationale: calibrationDialog.rationale.trim(),
        actual_outcome: actualOutcome,
      },
    )
    calibrationLabel.value = bidIntakeData(response)?.label || null
    calibrationDialog.visible = false
    ElMessage.success('总经办金标已保存为不可变历史快照')
    await loadCalibrationReport(true)
    if (calibrationCanManage.value) {
      await loadCalibrationOperations(true)
    }
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '校准金标保存失败'))
  } finally {
    labeling.value = false
  }
}

async function retryRun() {
  if (!selected.value || !activeRun.value) return
  retrying.value = true
  try {
    await bidIntakeApi.retry(
      props.projectUuid,
      selected.value.assessment_uuid,
      activeRun.value.run_uuid,
    )
    ElMessage.success('任务已从最近断点重新入队')
    await refreshSelected()
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '重试失败'))
  } finally {
    retrying.value = false
  }
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = null
}

function stopEvidencePolling() {
  if (evidencePollTimer) window.clearInterval(evidencePollTimer)
  evidencePollTimer = null
}

function candidateStatusLabel(value) {
  return {
    draft: '待盲测',
    blind_passed: '盲测通过',
    blind_failed: '盲测未通过',
  }[value] || value
}

function reviewStatusLabel(value) {
  return {
    pending: '待复核',
    approved: '已通过',
    rejected: '已退回',
  }[value] || value
}

function reviewStatusType(value) {
  return {
    pending: 'warning',
    approved: 'success',
    rejected: 'danger',
  }[value] || 'info'
}

function candidateStatusType(value) {
  return {
    draft: 'warning',
    blind_passed: 'success',
    blind_failed: 'danger',
  }[value] || 'info'
}

function candidateThresholdText(candidate) {
  const changes = candidate?.changed_fields || {}
  const quote = changes['decision_thresholds.recommend_quote_min']
  const conditional = changes['decision_thresholds.conditional_quote_min']
  if (!quote || !conditional) return '-'
  return `报价 ${quote.before}→${quote.after}；条件 ${conditional.before}→${conditional.after}`
}

function candidateDevelopmentAccuracy(candidate) {
  const value = Number(
    candidate?.development_report?.candidate?.development?.exact_accuracy,
  )
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '-'
}

function candidateBlindResult(candidate) {
  if (!candidate?.blind_report) return '尚未执行'
  const value = Number(
    candidate.blind_report?.candidate?.holdout?.exact_accuracy,
  )
  const accuracy = Number.isFinite(value) ? `${Math.round(value * 100)}%` : '-'
  return candidate.blind_report?.release_gate?.passed
    ? `${accuracy} · 通过`
    : `${accuracy} · 未通过`
}

function formatDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function formatFileSize(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function evidenceFileTypeLabel(value) {
  return evidenceFileTypeOptions.find((item) => item.value === value)?.label
    || value
    || '其他资料'
}

function evidenceJobStatusLabel(value) {
  return {
    queued: '排队中',
    running: '解析中',
    retryable: '等待重试',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[value] || value || '-'
}

function evidenceJobStatusType(value) {
  return {
    queued: 'info',
    running: 'primary',
    retryable: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info',
  }[value] || 'info'
}

function evidenceJobStageLabel(value) {
  return {
    queued: '等待后台处理',
    dispatch_failed: '入队失败',
    fetching_source: '读取并校验原件',
    parsing: '提取文件内容',
    evidence_ingestion: '生成证据清单',
    completed: '证据已就绪',
    failed: '处理失败',
  }[value] || value || '-'
}

function evidenceJobErrorText(job) {
  const labels = {
    SOURCE_STORAGE_UNAVAILABLE: '原件存储暂不可用',
    UNSUPPORTED_OR_UNREADABLE_FILE: '文件格式不支持、已损坏或无法提取文字',
    SOURCE_HASH_MISMATCH: '原件完整性校验失败',
    EVIDENCE_INGEST_REJECTED: '证据入库未通过校验',
    PARSE_PIPELINE_ERROR: '解析管线处理失败',
    ATTEMPTS_EXHAUSTED: '解析重试次数已用完',
    DISPATCH_FAILED: '任务未能进入后台队列',
  }
  return labels[job?.error_code]
    || job?.error_message
    || '解析失败，请重试'
}

function blockerLabel(code) {
  return {
    RUNTIME_DISABLED: '研判 Runtime 尚未启用',
    ACTIVE_MANIFEST_REQUIRED: '缺少有效证据清单',
    READY_EVIDENCE_REQUIRED: '没有解析完成的招标资料',
    WORKER_OFFLINE: 'Agent Worker 离线',
    MCP_NOT_CONFIGURED: 'Worker 尚未配置招标资料 MCP',
    MODEL_NOT_CONFIGURED: 'Worker 尚未配置研判模型',
    WORKER_CAPABILITY_MISMATCH: '在线 Worker 的 MCP 与模型能力不在同一进程',
    POLICY_NOT_CONFIGURED: '总经办立项标准尚未装载',
    WORKER_POLICY_NOT_CONFIGURED: '在线 Worker 尚未装载总经办立项标准',
    POLICY_VERSION_MISMATCH: 'Worker 装载的立项标准版本已过期',
  }[code] || code
}

function statusLabel(status) {
  return {
    queued: '排队中',
    running: '研判中',
    waiting_human: '待人工审核',
    resume_queued: '等待恢复',
    completed: '已完成',
    approved: '已批准',
    approved_with_conditions: '有条件批准',
    rejected: '不予立项',
    waiting_supplement: '待补资料',
    research_requested: '待重新研判',
    failed: '运行失败',
    blocked_stale_manifest: '资料版本已变化',
  }[status] || status || '-'
}

function statusTag(status) {
  if (['approved', 'approved_with_conditions', 'completed'].includes(status)) return 'success'
  if (['failed', 'rejected', 'blocked_stale_manifest'].includes(status)) return 'danger'
  if (['waiting_human', 'waiting_supplement', 'research_requested'].includes(status)) return 'warning'
  return 'info'
}

function recommendationLabel(value) {
  return {
    recommend_quote: '建议进入报价立项',
    recommend_no_quote: '建议不参与报价',
    conditional_quote: '建议有条件进入报价',
    need_supplement: '补充资料后再判断',
    manual_review: '需要人工研判',
  }[value] || '尚未形成结论'
}

function calibrationSplitLabel(value) {
  return value === 'holdout' ? 'Holdout盲测集' : '开发集'
}

function calibrationBasisLabel(value) {
  return {
    pre_bid_expert_review: '投标前专家复核',
    actual_project_outcome: '项目实际结果',
    combined: '专家复核 + 实际结果',
  }[value] || value
}

function gateLabel(value) {
  return {
    passed: '已通过',
    repair_required: '需要修复',
    supplement_required: '需要补资料',
    manual_review_required: '需要人工复核',
    research_restart_required: '需要重新研判',
  }[value] || '-'
}

function dimensionLabel(value) {
  return {
    project_basics: '项目概况',
    deadline: '截止时间',
    scope: '承包范围',
    qualification: '资格条件',
    schedule: '工期',
    payment: '付款条件',
    bond: '保证金',
    submission_requirements: '投标要求',
    document_completeness: '资料完整性',
    version_conflicts: '版本冲突',
  }[value] || value
}

function dimensionStatusLabel(value) {
  return {
    confirmed: '已确认',
    missing: '缺失',
    conflict: '冲突',
    unresolved: '未解决',
  }[value] || value
}

function dimensionTag(value) {
  return value === 'confirmed' ? 'success' : value === 'conflict' ? 'danger' : 'warning'
}

function severityLabel(value) {
  return { low: '低', medium: '中', high: '高', critical: '重大' }[value] || value
}

function severityTag(value) {
  return value === 'critical' || value === 'high' ? 'danger' : value === 'medium' ? 'warning' : 'info'
}

function factorRatingLabel(value) {
  return {
    favorable: '有利',
    acceptable: '可接受',
    adverse: '不利',
    critical: '红线',
    unknown: '未知',
  }[value] || value
}

function factorRatingTag(value) {
  return {
    favorable: 'success',
    acceptable: 'info',
    adverse: 'warning',
    critical: 'danger',
    unknown: 'info',
  }[value] || 'info'
}

function factorSourceLabel(value) {
  return {
    tender_evidence: '招标证据',
    internal_data: '内部数据',
    human_input: '人工补充',
    unknown: '待补充',
  }[value] || value
}

function workerStatusLabel(value) {
  return { online: '空闲', busy: '执行中', error: '异常', stopped: '已停止' }[value] || value
}

function eventLabel(value) {
  return {
    run_queued: '任务创建',
    run_claimed: 'Worker 领取',
    run_recovered: 'Worker 接管恢复',
    human_review_paused: '进入人工审核',
    human_decision_queued: '人工决策已保存',
    run_completed: '运行完成',
    run_failed: '运行失败',
    run_retry_queued: '重新入队',
    run_blocked_stale_manifest: '资料版本阻断',
  }[value] || value
}
</script>

<style scoped>
.bid-intake-workbench {
  display: grid;
  gap: 18px;
}

.evidence-intake-panel {
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid #dfe7f2;
  border-radius: 16px;
  background: linear-gradient(145deg, #f7faff, #fff);
}

.evidence-intake-heading,
.evidence-job-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.evidence-intake-heading > div,
.evidence-job-heading > div,
.evidence-file-cell,
.evidence-result-cell {
  display: grid;
  gap: 4px;
}

.evidence-intake-heading span {
  color: #4773b8;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.evidence-intake-heading strong {
  color: #172033;
  font-size: 18px;
}

.evidence-intake-heading small,
.evidence-job-heading small,
.evidence-file-cell small,
.evidence-result-cell small,
.evidence-upload-selection small {
  color: #697386;
}

.evidence-upload-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 14px;
}

.evidence-uploader {
  min-width: 0;
}

.evidence-uploader :deep(.el-upload) {
  width: 100%;
}

.evidence-uploader :deep(.el-upload-dragger) {
  width: 100%;
  min-height: 126px;
  padding: 30px 20px;
  border-color: #b8cae7;
  border-radius: 14px;
  background: rgb(255 255 255 / 72%);
}

.evidence-uploader :deep(.el-upload-dragger:hover) {
  border-color: #5b8ed9;
  background: #fff;
}

.evidence-upload-copy {
  display: grid;
  gap: 8px;
}

.evidence-upload-copy strong {
  color: #172033;
  font-size: 16px;
}

.evidence-upload-copy span,
.evidence-upload-tip {
  color: #697386;
  font-size: 12px;
}

.evidence-upload-controls {
  display: grid;
  align-content: start;
  gap: 14px;
  padding: 16px;
  border: 1px solid #e2e7ee;
  border-radius: 14px;
  background: rgb(255 255 255 / 82%);
}

.evidence-auto-classify,
.evidence-upload-selection {
  display: grid;
  gap: 7px;
}

.evidence-auto-classify > span {
  color: #4f5b6d;
  font-size: 13px;
  font-weight: 600;
}

.evidence-auto-classify > strong {
  color: #2f6fbe;
  font-size: 17px;
}

.evidence-auto-classify > small {
  color: #697386;
  line-height: 1.6;
}

.evidence-upload-selection strong,
.evidence-job-heading strong,
.evidence-file-cell strong {
  color: #273246;
}

.evidence-job-list {
  display: grid;
  gap: 10px;
  padding-top: 2px;
}

.evidence-job-list :deep(.el-table) {
  border: 1px solid #e6eaf0;
  border-radius: 12px;
  overflow: hidden;
}

.evidence-error-text {
  color: #c45656;
}

.evidence-no-action {
  color: #a3a9b3;
}

.runtime-strip,
.decision-metrics,
.calibration-metrics {
  display: grid;
  gap: 12px;
}

.runtime-strip {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.decision-metrics {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.calibration-summary {
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid #dfe7f2;
  border-radius: 16px;
  background: linear-gradient(145deg, #f7faff, #fff);
}

.calibration-heading,
.calibration-label-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.calibration-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.calibration-heading > div,
.calibration-label-panel > div {
  display: grid;
  gap: 4px;
}

.calibration-heading span,
.calibration-label-panel span,
.calibration-metrics span {
  color: #7b8493;
  font-size: 12px;
}

.calibration-heading small,
.calibration-label-panel small,
.calibration-metrics small {
  color: #697386;
}

.calibration-metrics {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.calibration-metrics > div {
  display: grid;
  gap: 4px;
  padding: 12px;
  border: 1px solid #e6eaf0;
  border-radius: 12px;
  background: rgb(255 255 255 / 76%);
}

.calibration-metrics strong {
  color: #172033;
  font-size: 18px;
}

.candidate-proposals {
  display: grid;
  gap: 10px;
  padding-top: 2px;
}

.dataset-operations {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid #dfe7f2;
  border-radius: 14px;
  background: rgb(255 255 255 / 72%);
}

.dataset-quality-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.dataset-quality-metrics > div {
  display: grid;
  gap: 3px;
  padding: 10px;
  border: 1px solid #e6eaf0;
  border-radius: 10px;
  background: #fff;
}

.dataset-quality-metrics span,
.dataset-quality-metrics small {
  color: #7b8493;
  font-size: 12px;
}

.dataset-quality-metrics strong {
  color: #172033;
  font-size: 17px;
}

.sample-filters {
  display: grid;
  grid-template-columns: 150px 150px minmax(190px, 1fr) auto;
  gap: 8px;
}

.candidate-proposal-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.candidate-proposal-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.frozen-dataset-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.frozen-dataset-strip > span {
  color: #7b8493;
  font-size: 12px;
}

.candidate-proposal-heading > div,
.candidate-proposal-table strong,
.candidate-proposal-table small,
.calibration-sample-table strong,
.calibration-sample-table small {
  display: block;
}

.candidate-proposal-heading small,
.candidate-proposal-table small,
.candidate-frozen {
  color: #7b8493;
  font-size: 12px;
}

.runtime-strip > div,
.decision-metrics > div {
  display: grid;
  gap: 5px;
  padding: 16px;
  border: 1px solid #e6eaf0;
  border-radius: 14px;
  background: linear-gradient(145deg, #fff, #f8fafc);
}

.runtime-strip span,
.decision-metrics span,
.detail-heading span,
.human-review-panel span {
  color: #7b8493;
  font-size: 12px;
}

.runtime-strip strong,
.decision-metrics strong {
  color: #172033;
  font-size: 19px;
}

.runtime-strip small,
.workbench-actions p,
.detail-heading small,
.human-review-panel small {
  color: #697386;
}

.workbench-actions,
.detail-heading,
.human-review-panel,
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.workbench-actions h3,
.detail-heading h3 {
  margin: 0 0 6px;
}

.workbench-actions p {
  margin: 0;
}

.assessment-layout {
  display: grid;
  grid-template-columns: minmax(230px, 0.3fr) minmax(0, 1fr);
  gap: 16px;
  min-height: 520px;
}

.assessment-list,
.assessment-detail {
  padding: 16px;
  border: 1px solid #e5e9f0;
  border-radius: 16px;
  background: #fff;
}

.assessment-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-heading {
  margin-bottom: 4px;
}

.section-heading small {
  color: #7b8493;
}

.assessment-item {
  display: grid;
  gap: 8px;
  width: 100%;
  padding: 13px;
  border: 1px solid #e5e9f0;
  border-radius: 12px;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.assessment-item:hover,
.assessment-item.active {
  border-color: #8ab4f8;
  background: #f5f8ff;
}

.assessment-item > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.assessment-item span,
.assessment-item small {
  color: #7b8493;
  font-size: 12px;
}

.assessment-detail {
  display: grid;
  align-content: start;
  gap: 18px;
  min-width: 0;
}

.gate-alert ul {
  margin: 8px 0 0;
  padding-left: 20px;
}

.assessment-tabs {
  min-width: 0;
}

.calibration-label-panel {
  padding: 16px;
  border: 1px solid #dfe7f2;
  border-radius: 14px;
  background: #f8faff;
}

.calibration-label-panel p {
  margin: 4px 0 0;
  color: #4f5b6d;
  font-size: 13px;
}

.calibration-form {
  margin-top: 16px;
}

.calibration-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.human-review-panel {
  padding: 16px;
  border: 1px solid #f1ce8a;
  border-radius: 14px;
  background: #fffaf0;
}

.human-review-panel.error {
  border-color: #f0aaaa;
  background: #fff6f6;
}

.human-review-panel > div {
  display: grid;
  gap: 4px;
}

@media (max-width: 1100px) {
  .runtime-strip,
  .decision-metrics,
  .calibration-metrics,
  .dataset-quality-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .evidence-upload-layout {
    grid-template-columns: 1fr;
  }

  .assessment-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .evidence-intake-heading,
  .evidence-job-heading,
  .calibration-heading,
  .calibration-label-panel,
  .candidate-proposal-heading,
  .calibration-actions,
  .candidate-proposal-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .sample-filters,
  .dataset-quality-metrics {
    grid-template-columns: 1fr;
  }

  .calibration-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
