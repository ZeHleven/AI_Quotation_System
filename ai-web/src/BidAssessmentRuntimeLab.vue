<template>
  <div class="runtime-lab-page">
    <header class="lab-hero">
      <div>
        <div class="hero-kicker"><span></span> Phase 4 · Runnable MVP-1</div>
        <h2>旗胜投标机会研判 Agent · 研判工作台</h2>
        <p>从资料上传、解析与标段选择，到 Evidence MCP、事实权威、硬门禁、初筛报告和完整运行轨迹，均在新数据域内闭环。</p>
      </div>
      <div class="hero-status">
        <el-tag :type="canExecute ? 'success' : 'info'" effect="dark">
          {{ runtimeModeLabel }}
        </el-tag>
        <small>{{ isPreview ? '协议预览' : '真实运行轨迹' }}</small>
        <span class="connection"><i :class="{ live: sseConnected }"></i>{{ sseLabel }}</span>
      </div>
    </header>

    <el-alert
      v-if="!capabilities.enabled"
      class="lab-alert"
      type="info"
      :closable="false"
      show-icon
      title="当前展示协议预览，不代表发生过真实模型或工具调用"
      description="在独立本地开发环境仅开启 FEATURE_BID_ASSESSMENT_PHASE4_MVP0_TRACE=true 即可选择真实 Run；需要 SSE 时再开启 V1 总开关，执行器子开关可以保持关闭。"
    />
    <el-alert
      v-else-if="isViewOnly"
      class="lab-alert"
      type="warning"
      :closable="false"
      show-icon
      title="Runtime Lab 当前为 view-only：历史结果可读，所有写操作已由服务端硬阻断"
      :description="`Worker ${capabilities.worker_running ? '运行中' : '未启动'}；模型调用 ${capabilities.model_calls_enabled ? '已允许' : '已禁止'}；不会上传资料、创建 Run、重试或取消任务。`"
    />

    <section class="mvp1-launch-card">
      <div class="launch-copy">
        <small>MVP-1 VERTICAL SLICE</small>
        <h3>资料进，带引用的初筛报告出</h3>
        <p>支持 PDF、Word、Excel、图片与文本资料。系统只使用当前 Manifest 和 ParseHead；检索片段必须经 evidence.read 后才能成为事实证据。</p>
        <div class="launch-state">
          <el-tag :type="canExecute ? 'success' : 'info'" effect="plain">
            {{ canExecute ? '执行链已启用' : '当前为只读展示' }}
          </el-tag>
          <span v-if="workspace.assessmentId">研判 {{ shortId(workspace.assessmentId) }} · {{ assessmentStatusLabel }}</span>
          <span v-else>尚未创建本次研判</span>
        </div>
        <div class="runtime-readiness">
          <span>Access <b>{{ accessMode }}</b></span>
          <span>Worker <b>{{ capabilities.worker_running ? 'ready' : 'off' }}</b></span>
          <span>Model <b>{{ capabilities.model_provider || '-' }}</b></span>
          <span>Retrieval <b>{{ capabilities.retrieval_mode || '-' }}</b></span>
        </div>
      </div>
      <div class="launch-actions">
        <el-button
          type="primary"
          size="large"
          :disabled="!canExecute"
          @click="openIntake"
        >新建研判并上传资料</el-button>
        <el-button
          v-if="workspace.assessmentId"
          size="large"
          :loading="intakeBusy"
          @click="refreshAssessmentStatus"
        >刷新处理状态</el-button>
      </div>
    </section>

    <section class="readiness-card">
      <div class="readiness-heading">
        <div>
          <small>{{ preflight.schema || 'bid.runtime.execute-preflight.v2' }}</small>
          <h3>Execute Preflight</h3>
          <p>只显示能力状态和阻断码；不返回密钥值、绝对路径或模型文件内容。</p>
        </div>
        <el-tag :type="preflightTagType" effect="dark">
          {{ preflightSummary }}
        </el-tag>
      </div>
      <div class="readiness-grid" v-loading="preflightLoading">
        <article v-for="item in preflight.checks || []" :key="item.code" :class="`readiness-${item.status}`">
          <div>
            <small>{{ item.code }}</small>
            <el-tag size="small" :type="preflightCheckTagType(item.status)" effect="plain">
              {{ preflightCheckLabel(item.status) }}
            </el-tag>
          </div>
          <strong>{{ item.label }}</strong>
          <p>{{ item.detail }}</p>
        </article>
      </div>
      <el-alert
        v-if="preflight.restart_required"
        type="info"
        :closable="false"
        title="权限不能在网页内提升"
        description="如需执行，请停止当前 view-only 进程，再使用本地启动脚本显式选择 execute；启动器会重新校验模型凭据和冻结依赖。"
      />
    </section>

    <section v-if="capabilities.enterprise_capability_enabled" class="enterprise-card">
      <div class="readiness-heading">
        <div>
          <small>PHASE 4C-2 · I01—I11</small>
          <h3>真实企业能力基线</h3>
          <p>先校验来源、有效期、覆盖状态和版本差异，再以候选 Hash 冻结；Run 只使用已经冻结的企业基线。</p>
        </div>
        <div class="enterprise-actions">
          <el-tag :type="enterpriseSnapshot?.complete ? 'success' : 'warning'" effect="plain">
            {{ enterpriseSnapshot?.complete ? '已冻结' : '待配置' }}
          </el-tag>
          <el-button type="primary" plain :disabled="!canConfigureEnterprise" @click="openEnterpriseBaseline">
            {{ enterpriseSnapshot ? '创建新版本' : '配置企业能力' }}
          </el-button>
        </div>
      </div>
      <div v-if="enterpriseSnapshot" class="enterprise-summary">
        <span><small>VERSION</small><b>{{ enterpriseSnapshot.version }}</b></span>
        <span><small>AS OF</small><b>{{ formatTime(enterpriseSnapshot.as_of) }}</b></span>
        <span><small>RECORDS</small><b>{{ enterpriseSnapshot.record_count }}/11</b></span>
        <span><small>HASH</small><b>{{ shortHash(enterpriseSnapshot.snapshot_hash) }}</b></span>
      </div>
      <div v-if="enterpriseSnapshot" class="baseline-coverage">
        <el-tag type="success" effect="plain">SUPPORTED {{ enterpriseCoverage.supported }}</el-tag>
        <el-tag type="warning" effect="plain">PARTIAL {{ enterpriseCoverage.partial }}</el-tag>
        <el-tag type="info" effect="plain">UNKNOWN {{ enterpriseCoverage.unknown }}</el-tag>
      </div>
      <div v-if="capabilities.enterprise_evidence_import_enabled" class="business-baseline-strip evidence-package-strip">
        <div>
          <small>PHASE 4D-2 · EVIDENCE PACKAGE</small>
          <strong>{{ enterpriseEvidencePackage ? enterpriseEvidencePackage.version : '尚未冻结真实企业资料包' }}</strong>
          <span v-if="enterpriseEvidencePackage">{{ enterpriseEvidencePackage.package_label }} · {{ shortHash(enterpriseEvidencePackage.package_hash) }}</span>
          <span v-else>上传原始资料，以文件 SHA-256 固化，并人工映射到 I01—I11；系统不会根据文件名或 MIME 猜测能力。</span>
        </div>
        <el-tag v-if="enterpriseEvidencePackage" type="success" effect="dark">资料包已冻结</el-tag>
        <el-button type="primary" plain :disabled="!canConfigureEvidenceImport" @click="openEvidenceImport">
          {{ enterpriseEvidencePackage ? '创建新资料包' : '导入企业资料' }}
        </el-button>
      </div>
      <div v-if="capabilities.business_baseline_enabled && enterpriseSnapshot" class="business-baseline-strip">
        <div>
          <small>PHASE 4D-1 · BUSINESS BASELINE</small>
          <strong>{{ enterpriseBusinessBaseline ? enterpriseBusinessBaseline.version : '尚未完成真实来源核验' }}</strong>
          <span v-if="enterpriseBusinessBaseline">{{ enterpriseBusinessBaseline.verification_outcome }} · {{ shortHash(enterpriseBusinessBaseline.baseline_hash) }}</span>
          <span v-else>逐项绑定来源凭证或明确 unknown，核验后新 Run 才能用于业务决策复验。</span>
        </div>
        <el-tag v-if="enterpriseBusinessBaseline" type="success" effect="dark">真实基线已冻结</el-tag>
        <el-button v-else type="primary" plain :disabled="!canConfigureBusinessBaseline" @click="openBusinessBaselineReview">核验真实企业基线</el-button>
      </div>
      <div v-if="capabilities.fact_verification_enabled" class="business-baseline-strip">
        <div>
          <small>PHASE 4D-3 · COMPARABLE FACTS</small>
          <strong>{{ hardGateComparisonBaseline ? hardGateComparisonBaseline.version : '尚未冻结硬门可比事实' }}</strong>
          <span v-if="hardGateComparisonBaseline">
            {{ hardGateComparisonBaseline.verification_outcome }} · {{ shortHash(hardGateComparisonBaseline.baseline_hash) }}
            <template v-if="hardGateComparisonBaseline.current === false"> · 已失效，需重新核验</template>
          </span>
          <span v-else>逐项核验 5 个招标事实和 11 个企业事实；只有 supported 才能进入七项硬门比较。</span>
        </div>
        <el-tag v-if="hardGateComparisonBaseline" :type="hardGateComparisonBaseline.current === false ? 'danger' : 'success'" effect="dark">
          {{ hardGateComparisonBaseline.current === false ? '基线已失效' : '可比基线已冻结' }}
        </el-tag>
        <el-button type="primary" plain :disabled="!canConfigureFactVerification" @click="openFactVerification">
          {{ hardGateComparisonBaseline ? '创建新核验版本' : '核验 16 项事实' }}
        </el-button>
      </div>
      <el-alert
        v-if="!enterpriseSnapshot"
        type="warning"
        :closable="false"
        title="尚无受治理企业能力快照"
        description="这会阻断新 Run，但不会阻止你先在 execute 模式配置快照；空值会明确保存为 unknown。"
      />
    </section>

    <section v-if="workspace.assessmentId" class="assessment-status-card">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="旧研判 Workflow 已从执行主链移除"
        description="当前页面只保留历史 Run、证据、报告与校验结果的只读追溯；固定 P0—P4、Plan Revision、Task DAG 和 Continuation 不再作为新 Agent 的执行入口。"
      />
    </section>
    <el-alert
      v-else-if="!runs.length && !loading"
      class="lab-alert"
      type="warning"
      :closable="false"
      show-icon
      title="只读轨迹已开启，但当前账号暂无可见 Run"
      description="页面继续展示协议预览；它只解释工程结构，不会伪造执行状态。"
    />

    <section class="control-strip">
      <div class="run-picker">
        <label>观察对象</label>
        <el-select
          v-model="selectedRunId"
          filterable
          clearable
          :loading="loading"
          placeholder="选择真实 Run；留空查看协议预览"
        >
          <el-option
            v-for="run in runs"
            :key="run.run_id"
            :label="`${run.assessment_title} · Run #${run.run_sequence} · ${run.status}`"
            :value="run.run_id"
          >
            <div class="run-option">
              <strong>{{ run.assessment_title }}</strong>
              <span>#{{ run.run_sequence }} · {{ run.run_kind }} · {{ run.status }}</span>
            </div>
          </el-option>
        </el-select>
      </div>
      <div class="control-actions">
        <el-switch v-model="autoRefresh" :disabled="isPreview || !capabilities.live_sse_enabled" active-text="事件后刷新" />
        <el-button
          v-if="!isPreview"
          type="danger"
          plain
          :disabled="!canCancelRun"
          :loading="lifecycleBusy"
          @click="cancelSelectedRun"
        >安全取消</el-button>
        <el-button
          v-if="!isPreview"
          type="warning"
          plain
          :disabled="!canRetryRun"
          :loading="lifecycleBusy"
          @click="retrySelectedRun"
        >从 Checkpoint 重试</el-button>
        <el-button :loading="loading" @click="refresh">刷新轨迹</el-button>
        <el-button v-if="!isPreview" plain @click="showPreview">协议预览</el-button>
      </div>
    </section>

    <section class="brain-rail" aria-label="Agent 运行大脑主链路">
      <template v-for="(step, index) in brainRail" :key="step.code">
        <article :class="['brain-step', { active: activeRailCodes.has(step.code) }]">
          <span>{{ step.index }}</span>
          <div><small>{{ step.code }}</small><strong>{{ step.label }}</strong></div>
        </article>
        <b v-if="index < brainRail.length - 1">→</b>
      </template>
    </section>

    <section class="metric-grid">
      <article><small>RUN</small><strong>{{ trace.run?.status || '-' }}</strong><span>{{ trace.run?.current_stage || 'planning' }}</span></article>
      <article><small>TASK DAG</small><strong>{{ trace.summary?.task_count || 0 }}</strong><span>确定性任务</span></article>
      <article><small>ATTEMPT</small><strong>{{ trace.summary?.attempt_count || 0 }}</strong><span>Lease / Fencing</span></article>
      <article><small>MODEL</small><strong>{{ trace.summary?.model_call_count || 0 }}</strong><span>Gateway 调用</span></article>
      <article><small>TOOL</small><strong>{{ trace.summary?.tool_call_count || 0 }}</strong><span>受控 Adapter</span></article>
      <article><small>CHECKPOINT</small><strong>{{ trace.summary?.checkpoint_count || 0 }}</strong><span>不可变续跑点</span></article>
    </section>

    <section class="trace-card">
      <div class="card-heading">
        <div>
          <small>{{ trace.schema }}</small>
          <h3>端到端运行血缘</h3>
          <p>{{ trace.trace_hash ? `Trace ${shortHash(trace.trace_hash)}` : '协议预览' }} · {{ trace.summary?.node_count || 0 }} nodes · {{ trace.summary?.edge_count || 0 }} edges</p>
        </div>
        <el-tag type="info" effect="plain">控制平面元数据 · 正文已脱敏</el-tag>
      </div>
      <el-tabs v-model="activeTab">
        <el-tab-pane label="运行图谱" name="graph">
          <BidAssessmentRuntimeGraph :trace="trace" />
        </el-tab-pane>
        <el-tab-pane label="初筛报告" name="report">
          <div v-if="reportDetail" class="report-sheet">
            <div class="report-heading">
              <div>
                <small>REPORT V{{ reportDetail.version }} · {{ shortHash(reportDetail.report_hash) }}</small>
                <h3>{{ reportDetail.title }}</h3>
                <p>{{ reportDetail.executive_summary }}</p>
              </div>
              <el-tag :type="decisionTagType(reportDetail.decision)" effect="dark">
                {{ decisionLabel(reportDetail.decision) }}
              </el-tag>
            </div>
            <section v-if="capabilities.mvp_release_candidate_enabled" class="release-card">
              <div>
                <small>PHASE 4C-3 · BUSINESS ACCEPTANCE</small>
                <strong>MVP Release Candidate</strong>
                <p v-if="releaseCandidate">
                  {{ releaseCandidate.version }} · {{ releaseOutcomeLabel(releaseCandidate.acceptance_outcome) }} · {{ shortHash(releaseCandidate.release_hash) }}
                </p>
                <p v-else>逐项确认七项硬门与报告质量，再把当前 Run、企业快照、报告和验证结果冻结为不可变验收版本。</p>
              </div>
              <div class="release-actions">
                <el-tag v-if="releaseCandidate" type="success" effect="dark">已冻结 RC</el-tag>
                <el-button v-else type="primary" plain :disabled="!canConfigureRelease" @click="openReleaseAcceptance">业务验收并冻结 RC</el-button>
              </div>
            </section>
            <div class="gate-grid">
              <article v-for="gate in reportDetail.report?.hard_gates || []" :key="gate.gate_code" :class="`gate-${gate.status}`">
                <small>{{ gate.gate_code }} · {{ gate.acceptance?.label || '硬门' }}</small><strong>{{ gateStatusLabel(gate.status) }}</strong>
                <span v-if="gate.acceptance?.explanation">{{ gate.acceptance.explanation }}</span>
                <span>{{ (gate.reason_codes || []).join(' · ') }}</span>
                <span v-if="gate.comparison?.comparison_mode">{{ gate.comparison.comparison_mode }} · 比较 {{ gate.comparison.compared_item_count || 0 }} 项</span>
                <span v-if="gate.acceptance?.unresolved_fact_slots?.length">待补：{{ gate.acceptance.unresolved_fact_slots.join('、') }}</span>
                <span v-for="action in gate.acceptance?.next_actions || []" :key="action">下一步：{{ action }}</span>
              </article>
            </div>
            <div class="claim-list">
              <article v-for="claim in reportDetail.report?.claims || []" :key="claim.claim_id">
                <div><el-tag size="small" effect="plain">{{ claim.claim_type }}</el-tag><strong>{{ claim.text }}</strong></div>
                <p v-if="claim.premise_or_trigger">前提：{{ claim.premise_or_trigger }}</p>
                <details v-for="citation in claim.citations || []" :key="citation.evidence_id">
                  <summary>证据 {{ shortId(citation.evidence_id) }} · {{ locatorLabel(citation.locator) }}</summary>
                  <blockquote>{{ citation.excerpt }}</blockquote>
                </details>
              </article>
            </div>
            <el-alert v-if="reportDetail.status === 'stale'" type="warning" :closable="false" title="该报告对应的资料版本已被新 Manifest 替代，仅供历史审计。" />
          </div>
          <el-empty v-else :description="selectedRunId ? '该 Run 尚未生成通过验证的初筛报告' : '选择 Run 后查看报告'" />
        </el-tab-pane>
        <el-tab-pane label="Checkpoint" name="checkpoint">
          <div class="checkpoint-board">
            <article v-for="item in checkpointNodes" :key="item.id">
              <div class="checkpoint-index">{{ item.details?.action_seq ?? 0 }}</div>
              <div>
                <small>{{ item.attempt_id || 'Task Attempt' }}</small>
                <strong>{{ item.label }} → {{ item.details?.next_state || item.status }}</strong>
                <p>Fence {{ item.details?.fencing_token || '-' }} · State {{ shortHash(item.hashes?.state_hash) }}</p>
              </div>
            </article>
            <el-empty v-if="!checkpointNodes.length" description="该 Run 尚无 Checkpoint" />
          </div>
        </el-tab-pane>
        <el-tab-pane :label="`事件时间线 ${liveEvents.length ? `+${liveEvents.length}` : ''}`" name="timeline">
          <div class="timeline-list">
            <article v-for="item in timeline" :key="`${item.source}:${item.id}`">
              <time>{{ formatTime(item.occurred_at) }}</time>
              <i :class="statusClass(item.status)"></i>
              <div>
                <strong>{{ item.type }}</strong>
                <p>{{ item.source }} · {{ item.resource_type }} · {{ shortId(item.resource_id) }}</p>
              </div>
              <el-tag size="small" effect="plain">{{ item.status }}</el-tag>
            </article>
          </div>
        </el-tab-pane>
        <el-tab-pane label="安全边界" name="boundary">
          <div class="boundary-grid">
            <article>
              <small>页面能看见</small>
              <strong>状态、版本、预算、哈希、Lease、Fencing、Checkpoint 和依赖</strong>
            </article>
            <article>
              <small>页面永不返回</small>
              <strong>{{ (trace.redaction?.omitted || []).join(' · ') }}</strong>
            </article>
            <article>
              <small>数据来源</small>
              <strong>Phase 2/3/4 新权威表的只读投影，不读取旧 bid_intake_* 权威数据</strong>
            </article>
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-dialog v-model="enterpriseVisible" title="企业能力基线验收（I01—I11）" width="min(980px, 96vw)" :close-on-click-modal="false">
      <el-alert type="info" :closable="false" title="先校验，后冻结" description="本页只接收本地人工确认或导入的数据，不读取生产系统。未核实项必须保持 unknown；冻结后不可修改，只能创建新版本。" />
      <el-form class="enterprise-form" label-position="top" :disabled="!canConfigureEnterprise">
        <div class="form-grid">
          <el-form-item label="数据来源名称"><el-input v-model="enterpriseForm.sourceLabel" maxlength="300" placeholder="例如：企业资质及资源台账（负责人复核）" /></el-form-item>
          <el-form-item label="来源版本"><el-input v-model="enterpriseForm.sourceVersion" maxlength="64" placeholder="例如：manual-2026-08-v1" /></el-form-item>
          <el-form-item label="来源状态">
            <el-select v-model="enterpriseForm.sourceStatus"><el-option label="负责人已核验" value="verified" /><el-option label="企业自报" value="self_reported" /><el-option label="本地导入" value="imported" /></el-select>
          </el-form-item>
          <el-form-item label="部分覆盖槽位">
            <el-select v-model="enterpriseForm.partialSlots" multiple collapse-tags placeholder="未完整核实的 I 槽">
              <el-option v-for="item in enterpriseSlotOptions" :key="item.code" :label="`${item.code} ${item.label}`" :value="item.code" />
            </el-select>
          </el-form-item>
          <el-form-item label="统一有效起始时间"><el-date-picker v-model="enterpriseForm.validFrom" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable /></el-form-item>
          <el-form-item label="统一有效截止时间"><el-date-picker v-model="enterpriseForm.validTo" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item label="企业法定名称"><el-input v-model="enterpriseForm.legalName" placeholder="I01" /></el-form-item>
          <el-form-item label="安全生产许可证"><el-input v-model="enterpriseForm.safetyLicense" placeholder="I03：许可证编号" /></el-form-item>
          <el-form-item label="可用现金（元）"><el-switch v-model="enterpriseForm.financialKnown" active-text="已核实" /><el-input-number v-model="enterpriseForm.availableCash" :disabled="!enterpriseForm.financialKnown" :min="0" :step="100000" controls-position="right" /></el-form-item>
          <el-form-item label="单笔保函/保证金上限（元）"><el-switch v-model="enterpriseForm.guaranteeKnown" active-text="已核实" /><el-input-number v-model="enterpriseForm.maxBond" :disabled="!enterpriseForm.guaranteeKnown" :min="0" :step="100000" controls-position="right" /></el-form-item>
          <el-form-item label="支持的保证形式"><el-input v-model="enterpriseForm.bondForms" placeholder="银行保函, 现金" /></el-form-item>
          <el-form-item label="可用投标准备人天"><el-switch v-model="enterpriseForm.bidCapacityKnown" active-text="已核实" /><el-input-number v-model="enterpriseForm.personDays" :disabled="!enterpriseForm.bidCapacityKnown" :min="0" :step="1" controls-position="right" /></el-form-item>
          <el-form-item label="合规状态">
            <el-select v-model="enterpriseForm.complianceStatus"><el-option label="未知" value="unknown" /><el-option label="合规/正常" value="clear" /><el-option label="存在阻断" value="blocked" /></el-select>
          </el-form-item>
          <el-form-item label="禁投风险确认">
            <el-select v-model="enterpriseForm.riskStatus"><el-option label="尚未核实" value="unknown" /><el-option label="已核实无全局禁投风险" value="clear" /><el-option label="存在全局禁投风险" value="triggered" /></el-select>
          </el-form-item>
        </div>
        <el-form-item label="有效资质（每行一项）"><el-input v-model="enterpriseForm.qualifications" type="textarea" :rows="3" placeholder="建筑装修装饰工程专业承包一级" /></el-form-item>
        <el-form-item label="相似业绩（每行一项）"><el-input v-model="enterpriseForm.performance" type="textarea" :rows="3" placeholder="某商业综合体精装修工程" /></el-form-item>
        <el-form-item label="可用人员/岗位（每行一项）"><el-input v-model="enterpriseForm.personnel" type="textarea" :rows="3" placeholder="一级建造师（建筑工程）" /></el-form-item>
        <el-form-item label="客户风险记录（每行：客户名称 | clear/high/blocked）"><el-input v-model="enterpriseForm.clientRisks" type="textarea" :rows="3" placeholder="某客户 | high" /></el-form-item>
        <el-form-item label="变更说明"><el-input v-model="enterpriseForm.changeNote" maxlength="1000" placeholder="例如：用于本地 MVP 演示的企业能力基线" /></el-form-item>
      </el-form>
      <section v-if="enterpriseValidation" class="baseline-validation">
        <div class="baseline-validation-heading">
          <strong>{{ enterpriseValidation.acceptance_ready ? '七项硬门企业侧输入已就绪' : '仍有企业数据待补齐' }}</strong>
          <span>变化 {{ enterpriseValidation.changed_slot_count }} 项 · Candidate {{ shortHash(enterpriseValidation.candidate_snapshot_hash) }}</span>
        </div>
        <div class="baseline-slot-grid">
          <article v-for="item in enterpriseValidation.slots || []" :key="item.slot_code" :class="`slot-${item.validation_status}`">
            <small>{{ item.slot_code }}</small><strong>{{ item.label }}</strong>
            <span>{{ item.effective_status }} · {{ item.change_type }}</span>
          </article>
        </div>
        <div class="baseline-gate-readiness">
          <el-tag v-for="gate in enterpriseValidation.hard_gate_readiness || []" :key="gate.gate_code" :type="gate.status === 'ready' ? 'success' : (gate.status === 'deferred_tender' ? 'info' : 'warning')" effect="plain">
            {{ gate.gate_code }} {{ gate.status }}<template v-if="gate.unresolved_slot_codes?.length"> · {{ gate.unresolved_slot_codes.join('/') }}</template>
          </el-tag>
        </div>
      </section>
      <template #footer>
        <el-button @click="enterpriseVisible = false">取消</el-button>
        <el-button :disabled="!canConfigureEnterprise" :loading="enterpriseValidationBusy" @click="validateEnterpriseBaseline">校验与预览差异</el-button>
        <el-button type="primary" :disabled="!canFreezeEnterprise" :loading="enterpriseBusy" @click="freezeEnterpriseSnapshot">按候选 Hash 冻结</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="evidenceImportVisible" title="真实企业能力资料导入（Phase 4D-2）" width="min(1120px, 96vw)" :close-on-click-modal="false">
      <el-alert type="info" :closable="false" title="文件不等于能力事实" description="上传只固化原文件、来源身份、有效期和 SHA-256；I01—I11 必须人工显式映射。此步骤不运行 OCR、视觉、Embedding 或生成模型。" />
      <section class="evidence-import-grid">
        <div class="evidence-upload-panel">
          <h4>1. 上传不可变 Evidence Item</h4>
          <el-form label-position="top" :disabled="!canConfigureEvidenceImport">
            <div class="form-grid">
              <el-form-item label="来源类别">
                <el-select v-model="evidenceImportForm.evidenceClass"><el-option label="正式文件" value="official_document" /><el-option label="内部系统导出" value="internal_system" /><el-option label="审计/复核记录" value="audited_record" /></el-select>
              </el-form-item>
              <el-form-item label="来源名称"><el-input v-model="evidenceImportForm.sourceLabel" maxlength="300" placeholder="例如：建筑业企业资质证书" /></el-form-item>
              <el-form-item label="逻辑来源编号"><el-input v-model="evidenceImportForm.sourceRecordId" maxlength="128" placeholder="例如：qualification-certificate" /></el-form-item>
              <el-form-item label="来源版本"><el-input v-model="evidenceImportForm.sourceVersion" maxlength="64" placeholder="例如：2026-08-v1" /></el-form-item>
              <el-form-item label="有效起始时间"><el-date-picker v-model="evidenceImportForm.validFrom" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable /></el-form-item>
              <el-form-item label="有效截止时间"><el-date-picker v-model="evidenceImportForm.validTo" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" clearable /></el-form-item>
            </div>
            <el-form-item label="企业资料文件">
              <input class="native-file-input" type="file" accept=".pdf,.docx,.xlsx,.xlsm,.png,.jpg,.jpeg,.txt,.md" @change="selectEvidenceFile" />
              <small v-if="evidenceImportFile">{{ evidenceImportFile.name }} · {{ formatBytes(evidenceImportFile.size) }}</small>
            </el-form-item>
            <el-button type="primary" :disabled="!evidenceImportFile || !canConfigureEvidenceImport" :loading="evidenceItemBusy" @click="uploadEvidenceItem">上传并校验 Hash</el-button>
          </el-form>
        </div>
        <div class="evidence-item-panel">
          <h4>已导入 Evidence Item（{{ enterpriseEvidenceItems.length }}）</h4>
          <div class="evidence-item-list">
            <article v-for="item in enterpriseEvidenceItems" :key="item.evidence_item_id">
              <div><strong>{{ item.source_label }}</strong><el-tag size="small" effect="plain">{{ item.evidence_class }}</el-tag></div>
              <span>{{ item.source_record_id }}@{{ item.source_version }} · {{ item.original_filename }}</span>
              <small>{{ formatBytes(item.size_bytes) }} · {{ shortHash(item.content_sha256) }}</small>
            </article>
            <el-empty v-if="!enterpriseEvidenceItems.length" description="尚未上传企业资料" :image-size="64" />
          </div>
        </div>
      </section>
      <section class="evidence-package-builder">
        <h4>2. 显式映射 I01—I11 并冻结资料包</h4>
        <div class="business-slot-grid">
          <article v-for="slot in enterpriseSlotOptions" :key="slot.code">
            <div><small>{{ slot.code }}</small><strong>{{ slot.label }}</strong></div>
            <el-select v-model="evidencePackageForm.itemIds[slot.code]" multiple collapse-tags placeholder="选择适用于该槽位的 Evidence Item">
              <el-option v-for="item in enterpriseEvidenceItems" :key="item.evidence_item_id" :label="`${item.source_label} · ${item.source_version}`" :value="item.evidence_item_id" />
            </el-select>
            <el-input v-model="evidencePackageForm.notes[slot.code]" maxlength="1000" placeholder="无资料时必须明确说明；有资料时可写适用范围" />
          </article>
        </div>
        <el-form label-position="top">
          <div class="form-grid">
            <el-form-item label="资料包名称"><el-input v-model="evidencePackageForm.packageLabel" maxlength="300" /></el-form-item>
            <el-form-item label="变更说明"><el-input v-model="evidencePackageForm.changeNote" maxlength="2000" /></el-form-item>
          </div>
        </el-form>
        <section v-if="evidencePackageValidation" class="release-validation">
          <div><strong>{{ evidencePackageValidation.can_freeze ? '资料包可以冻结' : '仍有资料或映射阻断项' }}</strong><span>{{ shortHash(evidencePackageValidation.candidate_hash) }}</span></div>
          <el-alert v-if="evidencePackageValidation.blocking_codes?.length" type="warning" :closable="false" :title="evidencePackageValidation.blocking_codes.join(' · ')" />
          <div class="baseline-gate-readiness"><el-tag v-for="code in evidencePackageValidation.follow_up_codes || []" :key="code" type="warning" effect="plain">{{ code }}</el-tag></div>
        </section>
      </section>
      <template #footer>
        <el-button @click="evidenceImportVisible = false">关闭</el-button>
        <el-button :disabled="!canConfigureEvidenceImport" :loading="evidencePackageValidationBusy" @click="validateEvidencePackage">校验资料包</el-button>
        <el-button type="primary" :disabled="!canFreezeEvidencePackage" :loading="evidencePackageBusy" @click="freezeEvidencePackage">按 Candidate Hash 冻结</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="businessBaselineVisible" title="真实企业能力基线核验（Phase 4D-1）" width="min(1080px, 96vw)" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" title="真实不是页面标签，而是服务端权威" description="每个 I 槽必须确认来源。官方文件、内部系统和审计记录必须填写 SHA-256；管理层确认或 unknown 会保留 follow-up，不会被包装成完全核验。" />
      <section class="business-slot-grid">
        <article v-for="item in enterpriseSlotOptions" :key="item.code">
          <div><small>{{ item.code }}</small><strong>{{ item.label }}</strong><el-tag size="small" effect="plain">{{ businessRecordStatus(item.code) }}</el-tag></div>
          <el-select v-model="businessBaselineForm.dispositions[item.code]">
            <el-option label="已确认" value="confirmed" /><el-option label="需要修正" value="correction_required" /><el-option label="尚未复核" value="not_reviewed" />
          </el-select>
          <el-select v-model="businessBaselineForm.evidenceClasses[item.code]">
            <el-option v-for="option in businessEvidenceClassOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-select v-if="capabilities.enterprise_evidence_import_enabled" v-model="businessBaselineForm.evidenceItemIds[item.code]" clearable placeholder="选择资料包内的权威 Evidence Item" @change="selectBusinessEvidenceItem(item.code, $event)">
            <el-option v-for="source in packageItemsForSlot(item.code)" :key="source.evidence_item_id" :label="`${source.source_label} · ${source.source_version}`" :value="source.evidence_item_id" />
          </el-select>
          <el-input v-model="businessBaselineForm.evidenceRefs[item.code]" maxlength="300" placeholder="逻辑来源编号；禁止绝对路径或 URL" />
          <el-input v-model="businessBaselineForm.evidenceHashes[item.code]" maxlength="64" placeholder="来源文件/记录 SHA-256（适用时必填）" />
          <el-input v-model="businessBaselineForm.notes[item.code]" maxlength="1000" placeholder="partial、unknown 或管理层确认必须说明" />
        </article>
      </section>
      <el-form label-position="top"><el-form-item label="负责人核验说明"><el-input v-model="businessBaselineForm.reviewNote" type="textarea" :rows="3" maxlength="2000" placeholder="说明数据范围、核验日期、遗留项与使用边界" /></el-form-item></el-form>
      <section v-if="businessBaselineValidation" class="release-validation">
        <div><strong>{{ businessBaselineValidation.can_freeze ? '真实企业基线可以冻结' : '仍有核验阻断项' }}</strong><span>{{ businessBaselineValidation.verification_outcome }} · {{ shortHash(businessBaselineValidation.candidate_hash) }}</span></div>
        <el-alert v-if="businessBaselineValidation.blocking_codes?.length" type="warning" :closable="false" :title="businessBaselineValidation.blocking_codes.join(' · ')" />
      </section>
      <template #footer>
        <el-button @click="businessBaselineVisible = false">取消</el-button>
        <el-button :disabled="!canConfigureBusinessBaseline" :loading="businessBaselineValidationBusy" @click="validateBusinessBaseline">校验真实来源</el-button>
        <el-button type="primary" :disabled="!canFreezeBusinessBaseline" :loading="businessBaselineBusy" @click="freezeBusinessBaseline">按 Candidate Hash 冻结</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="factVerificationVisible" title="真实事实核验与硬门可比化（Phase 4D-3）" width="min(1180px, 97vw)" :close-on-click-modal="false">
      <el-alert type="info" :closable="false" title="候选事实不会自动成为硬门结论" description="系统仅从已完成 Run 投影待核验候选；你确认 supported 后仍必须绑定当前 ParseHead 的 Atom 或同一企业资料包的 Evidence Item。partial 与 unknown 均不会被判为通过。" />
      <div class="fact-verification-toolbar">
        <el-select v-model="factVerificationForm.sourceRunId" filterable placeholder="选择已完成的真实资料 Run" style="min-width: 360px" @change="resetFactVerificationDraft">
          <el-option v-for="run in completedRuns" :key="run.run_id" :label="`${shortId(run.run_id)} · ${run.assessment_title || run.assessment_id}`" :value="run.run_id" />
        </el-select>
        <el-button :disabled="!canConfigureFactVerification || !factVerificationForm.sourceRunId" :loading="factDraftBusy" @click="loadFactVerificationDraft">载入治理事实候选</el-button>
      </div>
      <el-form v-if="factVerificationForm.facts.length" label-position="top" :disabled="!canConfigureFactVerification">
        <el-form-item label="负责人核验说明"><el-input v-model="factVerificationForm.reviewNote" maxlength="2000" show-word-limit /></el-form-item>
        <div class="comparison-fact-grid">
          <article v-for="fact in factVerificationForm.facts" :key="fact.fact_slot">
            <header>
              <div><small>{{ fact.source_side.toUpperCase() }}</small><strong>{{ comparableFactLabel(fact.fact_slot) }}</strong><code>{{ fact.fact_slot }}</code></div>
              <el-select v-model="fact.verification_status" style="width: 140px">
                <el-option label="已核验 supported" value="supported" />
                <el-option label="部分 partial" value="partial" />
                <el-option label="未知 unknown" value="unknown" />
              </el-select>
            </header>
            <template v-if="fact.verification_status !== 'unknown'">
              <el-form-item label="机器值类型"><el-input v-model="fact.value_type" /></el-form-item>
              <el-form-item label="规范化 JSON 值"><el-input v-model="fact.valueText" type="textarea" :rows="4" /></el-form-item>
              <el-form-item :label="fact.source_side === 'tender' ? 'Atom ID（逗号分隔）' : 'Evidence Item ID（逗号分隔）'">
                <el-input v-model="fact.evidenceText" type="textarea" :rows="2" />
              </el-form-item>
            </template>
            <el-form-item label="核验备注"><el-input v-model="fact.note" type="textarea" :rows="2" /></el-form-item>
          </article>
        </div>
      </el-form>
      <el-empty v-else description="先选择一个 succeeded Run 并载入候选" :image-size="72" />
      <section v-if="factVerificationValidation" class="baseline-validation">
        <div class="baseline-validation-heading">
          <strong>{{ factVerificationValidation.verification_outcome }}</strong>
          <span>supported {{ factVerificationValidation.status_counts?.supported || 0 }} · partial {{ factVerificationValidation.status_counts?.partial || 0 }} · unknown {{ factVerificationValidation.status_counts?.unknown || 0 }} · Candidate {{ shortHash(factVerificationValidation.candidate_hash) }}</span>
        </div>
        <div class="baseline-gate-readiness">
          <el-tag v-for="code in factVerificationValidation.follow_up_codes || []" :key="code" type="warning" effect="plain">{{ code }}</el-tag>
        </div>
      </section>
      <template #footer>
        <el-button @click="factVerificationVisible = false">取消</el-button>
        <el-button :disabled="!canConfigureFactVerification || !factVerificationForm.facts.length" :loading="factVerificationValidationBusy" @click="validateFactVerification">校验可比性</el-button>
        <el-button type="primary" :disabled="!canFreezeFactVerification" :loading="factVerificationBusy" @click="freezeFactVerification">按 Candidate Hash 冻结</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="releaseVisible" title="真实业务验收与 MVP Release Candidate" width="min(1080px, 96vw)" :close-on-click-modal="false">
      <el-alert
        type="info"
        :closable="false"
        title="验收的是结果质量，不要求七项硬门全部通过"
        description="pass、fail 和 unknown 都可以被业务确认；但 unknown/fail 必须写明复核说明。任一 correction_required 或 not_reviewed 都不能冻结 RC。"
      />
      <section class="release-review-section">
        <h4>七项硬门逐项复核</h4>
        <div class="release-gate-grid">
          <article v-for="gate in reportDetail?.report?.hard_gates || []" :key="gate.gate_code">
            <div>
              <small>{{ gate.gate_code }}</small>
              <strong>{{ gate.acceptance?.label || '硬门' }}</strong>
              <el-tag size="small" :type="gate.status === 'pass' ? 'success' : (gate.status === 'fail' ? 'danger' : 'warning')" effect="plain">{{ gateStatusLabel(gate.status) }}</el-tag>
            </div>
            <p>{{ gate.acceptance?.explanation || (gate.reason_codes || []).join(' · ') }}</p>
            <el-select v-model="releaseForm.gateDispositions[gate.gate_code]" placeholder="请选择复核结论">
              <el-option label="确认 Agent 结果正确" value="confirmed" />
              <el-option label="需要修正" value="correction_required" />
              <el-option label="尚未复核" value="not_reviewed" />
            </el-select>
            <el-input v-model="releaseForm.gateNotes[gate.gate_code]" maxlength="1000" placeholder="unknown/fail 必须说明；需要修正时说明原因" />
          </article>
        </div>
      </section>
      <section class="release-review-section">
        <h4>报告质量复核</h4>
        <div class="release-quality-grid">
          <article v-for="item in releaseQualityOptions" :key="item.code">
            <strong>{{ item.label }}</strong>
            <el-select v-model="releaseForm.qualityDispositions[item.code]">
              <el-option label="已确认" value="confirmed" />
              <el-option label="需要修正" value="correction_required" />
              <el-option label="尚未复核" value="not_reviewed" />
            </el-select>
            <el-input v-model="releaseForm.qualityNotes[item.code]" maxlength="1000" placeholder="可选复核说明" />
          </article>
        </div>
        <el-form label-position="top">
          <el-form-item label="总体验收说明"><el-input v-model="releaseForm.reviewNote" type="textarea" :rows="3" maxlength="2000" placeholder="说明本次验收范围、遗留项和使用边界" /></el-form-item>
        </el-form>
      </section>
      <section v-if="releaseValidation" class="release-validation">
        <div>
          <strong>{{ releaseValidation.can_freeze ? '可以冻结 MVP Release Candidate' : '仍有阻断项' }}</strong>
          <span>Candidate {{ shortHash(releaseValidation.candidate_hash) }} · {{ releaseOutcomeLabel(releaseValidation.acceptance_outcome) }}</span>
        </div>
        <div class="release-check-grid">
          <article v-for="check in releaseValidation.system_checks || []" :key="check.code" :class="`release-${check.status}`">
            <small>{{ check.code }}</small><strong>{{ check.label }}</strong><span>{{ check.status }}</span>
          </article>
        </div>
        <el-alert v-if="releaseValidation.blocking_codes?.length || releaseValidation.review_blocking_codes?.length" type="warning" :closable="false" :title="[...(releaseValidation.blocking_codes || []), ...(releaseValidation.review_blocking_codes || [])].join(' · ')" />
        <div v-if="releaseValidation.revalidation" class="decision-revalidation">
          <strong>业务决策复验</strong>
          <span>{{ releaseValidation.revalidation.source_decision || '-' }} → {{ releaseValidation.revalidation.target_decision || '-' }}</span>
          <el-tag :type="releaseValidation.revalidation.decision_changed ? 'warning' : 'success'" effect="plain">{{ releaseValidation.revalidation.decision_changed ? '决策发生变化' : '决策保持一致' }}</el-tag>
          <small>来源 RC {{ shortId(releaseValidation.revalidation.source_release_candidate_id) }} · 真实基线 {{ shortHash(releaseValidation.revalidation.business_baseline_hash) }}</small>
        </div>
      </section>
      <template #footer>
        <el-button @click="releaseVisible = false">取消</el-button>
        <el-button :disabled="!canConfigureRelease" :loading="releaseValidationBusy" @click="validateReleaseAcceptance">校验业务验收</el-button>
        <el-button type="primary" :disabled="!canFreezeRelease" :loading="releaseBusy" @click="freezeReleaseCandidate">按 Candidate Hash 冻结 RC</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="intakeVisible" title="新建投标机会研判" width="min(720px, 92vw)" :close-on-click-modal="false">
      <el-form label-position="top" :disabled="!canExecute">
        <div class="form-grid">
          <el-form-item label="项目名称"><el-input v-model="intakeForm.title" maxlength="300" placeholder="例如：某园区装饰工程投标机会" /></el-form-item>
          <el-form-item label="客户/招标人"><el-input v-model="intakeForm.clientName" maxlength="300" placeholder="请输入客户或招标人" /></el-form-item>
        </div>
        <el-form-item label="研判备注"><el-input v-model="intakeForm.note" type="textarea" :rows="2" maxlength="2000" placeholder="可选：关注资格、工期、保证金等" /></el-form-item>
        <el-form-item label="招标资料">
          <input class="native-file-input" type="file" multiple :disabled="!canExecute" accept=".pdf,.docx,.xlsx,.xlsm,.png,.jpg,.jpeg,.txt,.md" @change="onFilesSelected" />
          <div v-if="intakeFiles.length" class="selected-files">
            <span v-for="file in intakeFiles" :key="`${file.name}:${file.size}`">{{ file.name }} · {{ formatBytes(file.size) }}</span>
          </div>
        </el-form-item>
      </el-form>
      <el-alert
        :type="canExecute ? 'info' : 'warning'"
        :closable="false"
        :title="canExecute ? '提交后解析和标段识别在 Worker 中异步完成；页面会保留 Assessment ID，刷新即可继续。' : '当前为 view-only，服务端不会接受任何创建或上传请求。'"
      />
      <template #footer>
        <el-button @click="intakeVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!canExecute" :loading="intakeBusy" @click="createAssessmentAndUpload">创建并提交资料</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import BidAssessmentRuntimeGraph from './BidAssessmentRuntimeGraph.vue'
import {
  bidAssessmentRuntimeLabApi,
  responseData,
  responseEtag,
  responseHeader,
  runtimeLabErrorMessage,
  streamAssessmentEvents,
} from './bidAssessmentRuntimeLabApi'

const capabilities = ref({ enabled: false })
const preflight = ref({ schema: 'bid.runtime.execute-preflight.v2', checks: [] })
const runs = ref([])
const selectedRunId = ref('')
const trace = ref(buildPreviewTrace())
const loading = ref(false)
const autoRefresh = ref(true)
const activeTab = ref('graph')
const liveEvents = ref([])
const sseConnected = ref(false)
const intakeVisible = ref(false)
const intakeBusy = ref(false)
const lifecycleBusy = ref(false)
const preflightLoading = ref(false)
const enterpriseSnapshot = ref(null)
const enterpriseVisible = ref(false)
const enterpriseBusy = ref(false)
const enterpriseValidation = ref(null)
const enterpriseValidationBusy = ref(false)
const enterpriseDraftAsOf = ref('')
const enterpriseBusinessBaseline = ref(null)
const businessBaselineVisible = ref(false)
const businessBaselineBusy = ref(false)
const businessBaselineValidation = ref(null)
const businessBaselineValidationBusy = ref(false)
const hardGateComparisonBaseline = ref(null)
const factVerificationVisible = ref(false)
const factDraftBusy = ref(false)
const factVerificationBusy = ref(false)
const factVerificationValidation = ref(null)
const factVerificationValidationBusy = ref(false)
const enterpriseEvidenceItems = ref([])
const enterpriseEvidencePackage = ref(null)
const evidenceImportVisible = ref(false)
const evidenceImportFile = ref(null)
const evidenceItemBusy = ref(false)
const evidencePackageBusy = ref(false)
const evidencePackageValidation = ref(null)
const evidencePackageValidationBusy = ref(false)
const evidencePackageAsOf = ref('')
const releaseCandidate = ref(null)
const releaseVisible = ref(false)
const releaseBusy = ref(false)
const releaseValidation = ref(null)
const releaseValidationBusy = ref(false)
const intakeFiles = ref([])
const intakeAuthorityFingerprint = ref('')
const intakeForm = reactive({ title: '', clientName: '', note: '' })
const enterpriseForm = reactive({
  sourceLabel: '企业能力台账（本地人工确认）', sourceVersion: 'manual-v1',
  sourceStatus: 'self_reported', partialSlots: [], validFrom: null, validTo: null,
  legalName: '', safetyLicense: '', availableCash: 0, maxBond: 0,
  financialKnown: false, guaranteeKnown: false,
  bondForms: '', personDays: 0, complianceStatus: 'unknown', riskStatus: 'unknown',
  bidCapacityKnown: false,
  qualifications: '', performance: '', personnel: '', clientRisks: '',
  changeNote: '建立完全隔离本地企业能力基线',
})
const enterpriseSlotOptions = [
  { code: 'I01', label: '企业法定主体' },
  { code: 'I02', label: '有效资质' },
  { code: 'I03', label: '安全生产许可证' },
  { code: 'I04', label: '相似项目业绩' },
  { code: 'I05', label: '可用人员与证书' },
  { code: 'I06', label: '资金能力' },
  { code: 'I07', label: '保证金与保函能力' },
  { code: 'I08', label: '投标准备能力' },
  { code: 'I09', label: '企业禁投规则' },
  { code: 'I10', label: '当前合规状态' },
  { code: 'I11', label: '客户风险记录' },
]
const businessEvidenceClassOptions = [
  { value: 'official_document', label: '正式文件' },
  { value: 'internal_system', label: '内部系统记录' },
  { value: 'audited_record', label: '审计记录' },
  { value: 'management_attestation', label: '负责人确认' },
  { value: 'not_available', label: '暂无可用来源' },
]
const businessBaselineForm = reactive({
  reviewedAsOf: '',
  reviewNote: '',
  dispositions: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, 'confirmed'])),
  evidenceClasses: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, 'management_attestation'])),
  evidenceRefs: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, ''])),
  evidenceHashes: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, ''])),
  evidenceItemIds: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, ''])),
  notes: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, ''])),
})
const factVerificationForm = reactive({
  assessmentId: '',
  sourceRunId: '',
  businessBaselineId: '',
  reviewedAsOf: '',
  reviewNote: '',
  facts: [],
})
const comparableFactLabels = Object.freeze({
  'tender.overview': '项目与招标范围',
  'tender.submission.deadline': '投标截止时间',
  'tender.qualification.requirements': '资质、业绩与人员要求',
  'tender.guarantee.requirements': '保证金与保函要求',
  'tender.schedule.site_constraints': '工期与现场约束',
  'enterprise.identity.legal_name': '企业法定主体',
  'enterprise.qualifications.active_records': '有效资质',
  'enterprise.safety_license.active_record': '安全生产许可证',
  'enterprise.performance.records': '近年相似业绩',
  'enterprise.personnel.available_records': '可用人员与证书',
  'enterprise.financial.capacity': '资金能力',
  'enterprise.guarantee.capacity': '保证金与保函能力',
  'enterprise.bid_preparation.capacity': '投标准备能力',
  'enterprise.prohibited_risk.rules': '企业禁投规则',
  'enterprise.compliance.current_records': '当前合规状态',
  'enterprise.client_risk.current_records': '客户风险记录',
})
const evidenceImportForm = reactive({
  evidenceClass: 'official_document',
  sourceRecordId: '',
  sourceVersion: '',
  sourceLabel: '',
  validFrom: null,
  validTo: null,
})
const evidencePackageForm = reactive({
  packageLabel: '旗胜企业能力资料包',
  changeNote: '导入经业务负责人确认的真实企业能力资料',
  itemIds: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, []])),
  notes: Object.fromEntries(enterpriseSlotOptions.map((item) => [item.code, '当前未提供适用资料，保持 unknown 并进入跟进项。'])),
})
const releaseGateCodes = ['HG01', 'HG02', 'HG03', 'HG04', 'HG05', 'HG06', 'HG07']
const releaseQualityOptions = [
  { code: 'REPORT_BUSINESS_READABLE', label: '报告表达可供业务负责人阅读' },
  { code: 'CITATIONS_TRACEABLE', label: '关键结论的原文引用可定位、可复核' },
  { code: 'UNKNOWNS_EXPLICIT', label: '缺失与未知项被明确披露，没有伪装成通过' },
  { code: 'DECISION_REASONABLE', label: '投/不投/待确认建议与硬门结果一致' },
  { code: 'PARSE_LIMITATIONS_REVIEWED', label: '已阅读解析质量、OCR/视觉缺口等限制' },
]
const releaseForm = reactive({
  reviewNote: '',
  gateDispositions: Object.fromEntries(releaseGateCodes.map((code) => [code, 'not_reviewed'])),
  gateNotes: Object.fromEntries(releaseGateCodes.map((code) => [code, ''])),
  qualityDispositions: Object.fromEntries(releaseQualityOptions.map((item) => [item.code, 'not_reviewed'])),
  qualityNotes: Object.fromEntries(releaseQualityOptions.map((item) => [item.code, ''])),
})
const reports = ref([])
const reportDetail = ref(null)
const workspace = reactive({
  assessmentId: window.sessionStorage.getItem('bid-mvp1-assessment-id') || '',
  assessmentEtag: '',
  batchId: '',
  batchEtag: '',
  manifestId: '',
  lots: [],
  selectedLotId: '',
  statusMessage: '准备开始',
  businessStatus: '',
})
let stopStream = null
let refreshTimer = null
let statusRefreshTimer = null

const isPreview = computed(() => !selectedRunId.value)
const accessMode = computed(() => capabilities.value.access_mode
  || (capabilities.value.assessment_intake_enabled ? 'execute' : 'view-only'))
const isViewOnly = computed(() => accessMode.value === 'view-only')
const canExecute = computed(() => accessMode.value === 'execute'
  && capabilities.value.execution_enabled === true
  && capabilities.value.write_enabled === true
  && capabilities.value.worker_running === true
  && capabilities.value.assessment_intake_enabled === true
  && preflight.value.current_process_ready === true)
const canConfigureEnterprise = computed(() => accessMode.value === 'execute'
  && capabilities.value.write_enabled === true
  && capabilities.value.enterprise_snapshot_configurable === true)
const enterpriseCoverage = computed(() => {
  const counts = { supported: 0, partial: 0, unknown: 0 }
  for (const record of enterpriseSnapshot.value?.records || []) {
    const status = String(record.coverage_status || 'unknown')
    if (Object.prototype.hasOwnProperty.call(counts, status)) counts[status] += 1
  }
  return counts
})
const canFreezeEnterprise = computed(() => canConfigureEnterprise.value
  && enterpriseValidation.value?.can_freeze === true
  && /^[0-9a-f]{64}$/.test(String(enterpriseValidation.value?.candidate_snapshot_hash || '')))
const canConfigureBusinessBaseline = computed(() => accessMode.value === 'execute'
  && capabilities.value.write_enabled === true
  && capabilities.value.business_baseline_configurable === true
  && Boolean(enterpriseSnapshot.value?.snapshot_id)
  && (!capabilities.value.enterprise_evidence_import_enabled || Boolean(enterpriseEvidencePackage.value?.evidence_package_id))
  && !enterpriseBusinessBaseline.value)
const canFreezeBusinessBaseline = computed(() => canConfigureBusinessBaseline.value
  && businessBaselineValidation.value?.can_freeze === true
  && /^[0-9a-f]{64}$/.test(String(businessBaselineValidation.value?.candidate_hash || '')))
const completedRuns = computed(() => runs.value.filter((run) => run.status === 'succeeded'))
const canConfigureFactVerification = computed(() => accessMode.value === 'execute'
  && capabilities.value.write_enabled === true
  && capabilities.value.fact_verification_configurable === true
  && Boolean(enterpriseBusinessBaseline.value?.business_baseline_id)
  && completedRuns.value.length > 0)
const canFreezeFactVerification = computed(() => canConfigureFactVerification.value
  && factVerificationValidation.value?.can_freeze === true
  && /^[0-9a-f]{64}$/.test(String(factVerificationValidation.value?.candidate_hash || '')))
const canConfigureEvidenceImport = computed(() => accessMode.value === 'execute'
  && capabilities.value.write_enabled === true
  && capabilities.value.enterprise_evidence_import_configurable === true)
const canFreezeEvidencePackage = computed(() => canConfigureEvidenceImport.value
  && evidencePackageValidation.value?.can_freeze === true
  && /^[0-9a-f]{64}$/.test(String(evidencePackageValidation.value?.candidate_hash || '')))
const runtimeModeLabel = computed(() => {
  if (canExecute.value) return 'EXECUTE · 可运行'
  return accessMode.value === 'execute' ? 'EXECUTE · 存在阻断' : 'VIEW-ONLY · 只读'
})
const selectedRun = computed(() => runs.value.find((run) => run.run_id === selectedRunId.value) || null)
const authorityFingerprint = computed(() => String(preflight.value.authority_fingerprint || ''))
const selectedRunStatus = computed(() => trace.value.run?.status || selectedRun.value?.status || '')
const canConfigureRelease = computed(() => accessMode.value === 'execute'
  && capabilities.value.write_enabled === true
  && capabilities.value.mvp_release_candidate_configurable === true
  && selectedRunStatus.value === 'succeeded'
  && reportDetail.value?.status === 'ready'
  && !releaseCandidate.value)
const canFreezeRelease = computed(() => canConfigureRelease.value
  && releaseValidation.value?.can_freeze === true
  && /^[0-9a-f]{64}$/.test(String(releaseValidation.value?.candidate_hash || '')))
const canCancelRun = computed(() => canExecute.value
  && (
    ['created', 'planning', 'queued', 'running', 'waiting_input', 'waiting_operation', 'validating'].includes(selectedRunStatus.value)
    || (selectedRunStatus.value === 'failed' && Boolean(trace.value.run?.retryable ?? selectedRun.value?.retryable))
  )
  && !selectedRun.value?.cancel_requested_at)
const canRetryRun = computed(() => canExecute.value
  && selectedRunStatus.value === 'failed'
  && Boolean(trace.value.run?.retryable ?? selectedRun.value?.retryable))
const preflightSummary = computed(() => {
  if (preflight.value.current_process_ready) return '当前进程可执行'
  if (preflight.value.launch_ready && preflight.value.restart_required) return '依赖就绪 · 需重启切换'
  if (preflight.value.launch_ready) return '等待运行态就绪'
  return `阻断 ${preflight.value.blocking_codes?.length || 0} 项`
})
const preflightTagType = computed(() => {
  if (preflight.value.current_process_ready) return 'success'
  if (preflight.value.launch_ready) return 'warning'
  return 'danger'
})
const checkpointNodes = computed(() => (trace.value.nodes || []).filter((item) => item.kind === 'checkpoint'))
const assessmentStatusLabel = computed(() => {
  if (reportDetail.value) return reportDetail.value.status === 'stale' ? '历史报告' : '报告已生成'
  if (selectedRunId.value) return `Run ${trace.value.run?.status || '处理中'}`
  if (workspace.lots.length) return '等待选择标段'
  return workspace.statusMessage
})
const timeline = computed(() => {
  const persisted = Array.isArray(trace.value.timeline) ? trace.value.timeline : []
  return [...persisted, ...liveEvents.value].sort((a, b) => String(b.occurred_at || '').localeCompare(String(a.occurred_at || ''))).slice(0, 160)
})
const sseLabel = computed(() => {
  if (isPreview.value) return 'SSE 未连接'
  if (!capabilities.value.live_sse_enabled) return '只读快照'
  return sseConnected.value ? 'SSE 已连接' : 'SSE 重连/等待'
})
const activeRailCodes = computed(() => new Set((trace.value.nodes || []).map((node) => ({
  plan: 'PLAN', task: 'DAG', task_attempt: 'EXEC', context: 'CTX', checkpoint: 'CP',
  model_call: 'MODEL', tool_invocation: 'TOOL', validation: 'VALIDATE',
}[node.kind])).filter(Boolean)))
const brainRail = [
  { index: '01', code: 'PLAN', label: 'Planner' },
  { index: '02', code: 'DAG', label: 'SkillBinding' },
  { index: '03', code: 'EXEC', label: 'LangGraph' },
  { index: '04', code: 'CTX', label: 'Context' },
  { index: '05', code: 'MODEL', label: 'Model Gateway' },
  { index: '06', code: 'TOOL', label: 'Tool Router' },
  { index: '07', code: 'CP', label: 'Checkpoint' },
  { index: '08', code: 'VALIDATE', label: 'Convergence' },
]

watch(selectedRunId, async (runId) => {
  stopSse()
  liveEvents.value = []
  if (!runId) {
    trace.value = buildPreviewTrace()
    releaseCandidate.value = null
    releaseVisible.value = false
    return
  }
  await loadTrace()
  await loadReports(selectedRun.value?.assessment_id || workspace.assessmentId, runId)
  startSse()
})

watch(canExecute, (enabled, wasEnabled) => {
  if (enabled || !wasEnabled) return
  intakeVisible.value = false
  intakeFiles.value = []
  intakeAuthorityFingerprint.value = ''
  if (statusRefreshTimer) clearTimeout(statusRefreshTimer)
  stopSse()
  ElMessage.warning('Runtime Lab 执行权限已变化，未提交操作已取消')
})

watch(canConfigureEnterprise, (enabled, wasEnabled) => {
  if (enabled || !wasEnabled) return
  enterpriseVisible.value = false
  enterpriseValidation.value = null
  enterpriseDraftAsOf.value = ''
})

watch(canConfigureBusinessBaseline, (enabled, wasEnabled) => {
  if (enabled || !wasEnabled) return
  businessBaselineVisible.value = false
  businessBaselineValidation.value = null
})

watch(canConfigureFactVerification, (enabled, wasEnabled) => {
  if (enabled || !wasEnabled) return
  factVerificationVisible.value = false
  factVerificationValidation.value = null
  factVerificationForm.facts = []
})

watch(canConfigureEvidenceImport, (enabled, wasEnabled) => {
  if (enabled || !wasEnabled) return
  evidenceImportVisible.value = false
  evidenceImportFile.value = null
  evidencePackageValidation.value = null
})

watch(canConfigureRelease, (enabled, wasEnabled) => {
  if (enabled || !wasEnabled) return
  releaseVisible.value = false
  releaseValidation.value = null
})

watch(enterpriseForm, () => {
  enterpriseValidation.value = null
}, { deep: true })

watch(businessBaselineForm, () => {
  businessBaselineValidation.value = null
}, { deep: true })

watch(factVerificationForm, () => {
  factVerificationValidation.value = null
}, { deep: true })

watch(evidencePackageForm, () => {
  evidencePackageValidation.value = null
}, { deep: true })

watch(releaseForm, () => {
  releaseValidation.value = null
}, { deep: true })

onMounted(async () => {
  await loadLab()
  if (workspace.assessmentId && capabilities.value.enabled) await refreshAssessmentStatus()
})
onBeforeUnmount(() => {
  stopSse()
  if (refreshTimer) clearTimeout(refreshTimer)
  if (statusRefreshTimer) clearTimeout(statusRefreshTimer)
})

async function loadLab() {
  loading.value = true
  try {
    await refreshAuthority()
    if (capabilities.value.enabled) runs.value = responseData(await bidAssessmentRuntimeLabApi.runs()) || []
    const params = new URLSearchParams(window.location.search)
    const requestedRun = params.get('run_id')
    if (requestedRun && runs.value.some((run) => run.run_id === requestedRun)) selectedRunId.value = requestedRun
  } catch (error) {
    ElMessage.warning(runtimeLabErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function refreshAuthority() {
  preflightLoading.value = true
  try {
    const capabilityResponse = await bidAssessmentRuntimeLabApi.capabilities()
    capabilities.value = responseData(capabilityResponse) || { enabled: false }
    if (capabilities.value.enterprise_capability_enabled) {
      try {
        const enterpriseResponse = responseData(await bidAssessmentRuntimeLabApi.enterpriseSnapshot()) || {}
        enterpriseSnapshot.value = enterpriseResponse.snapshot || null
      } catch (error) {
        if (error?.response?.status !== 404) throw error
        enterpriseSnapshot.value = null
      }
    } else {
      enterpriseSnapshot.value = null
    }
    if (capabilities.value.enterprise_evidence_import_enabled) {
      try {
        const [itemsResponse, packageResponse] = await Promise.all([
          bidAssessmentRuntimeLabApi.enterpriseEvidenceItems(),
          bidAssessmentRuntimeLabApi.enterpriseEvidencePackage(),
        ])
        enterpriseEvidenceItems.value = responseData(itemsResponse)?.items || []
        enterpriseEvidencePackage.value = responseData(packageResponse)?.evidence_package || null
      } catch (error) {
        if (error?.response?.status !== 404) throw error
        enterpriseEvidenceItems.value = []
        enterpriseEvidencePackage.value = null
      }
    } else {
      enterpriseEvidenceItems.value = []
      enterpriseEvidencePackage.value = null
    }
    if (capabilities.value.business_baseline_enabled && enterpriseSnapshot.value?.snapshot_id) {
      try {
        const businessResponse = responseData(
          await bidAssessmentRuntimeLabApi.enterpriseBusinessBaseline(enterpriseSnapshot.value.snapshot_id),
        ) || {}
        enterpriseBusinessBaseline.value = businessResponse.business_baseline || null
      } catch (error) {
        if (error?.response?.status !== 404) throw error
        enterpriseBusinessBaseline.value = null
      }
    } else {
      enterpriseBusinessBaseline.value = null
    }
    if (capabilities.value.fact_verification_enabled) {
      try {
        const comparisonResponse = responseData(
          await bidAssessmentRuntimeLabApi.hardGateComparisonBaseline(
            workspace.assessmentId || '',
          ),
        ) || {}
        hardGateComparisonBaseline.value = comparisonResponse.comparison_baseline || null
      } catch (error) {
        if (error?.response?.status !== 404) throw error
        hardGateComparisonBaseline.value = null
      }
    } else {
      hardGateComparisonBaseline.value = null
    }
    try {
      preflight.value = responseData(await bidAssessmentRuntimeLabApi.executePreflight()) || unavailablePreflight()
    } catch (error) {
      if (error?.response?.status !== 404) throw error
      preflight.value = unavailablePreflight()
    }
    return String(preflight.value.authority_fingerprint || '')
  } finally {
    preflightLoading.value = false
  }
}

function unavailablePreflight() {
  return {
    schema: 'bid.runtime.execute-preflight.v2',
    local_lab: Boolean(capabilities.value.local_lab),
    access_mode: accessMode.value,
    model_provider: capabilities.value.model_provider || 'configured_gateway',
    retrieval_mode: capabilities.value.retrieval_mode || 'configured',
    launch_ready: false,
    current_process_ready: false,
    restart_required: true,
    blocking_codes: ['PREFLIGHT_UNAVAILABLE'],
    deferred_codes: [],
    authority_fingerprint: '',
    checks: [{
      code: 'PREFLIGHT_UNAVAILABLE', label: 'Execute Preflight', status: 'blocked', required: true,
      detail: '当前后端尚未加载 Phase 4B-5；保持只读并等待安全重启',
    }],
  }
}

async function loadTrace({ quiet = false } = {}) {
  if (!selectedRunId.value) return
  if (!quiet) loading.value = true
  try {
    trace.value = responseData(await bidAssessmentRuntimeLabApi.trace(selectedRunId.value))
  } catch (error) {
    if (!quiet) ElMessage.error(runtimeLabErrorMessage(error))
  } finally {
    if (!quiet) loading.value = false
  }
}

async function refresh() {
  if (isPreview.value) {
    await loadLab()
    trace.value = buildPreviewTrace()
    return
  }
  await loadTrace()
  await loadReports(selectedRun.value?.assessment_id || workspace.assessmentId, selectedRunId.value)
}

function idempotencyKey(scope) {
  return `mvp1-${scope}-${window.crypto.randomUUID()}`
}

async function sha256Hex(file) {
  const digest = await window.crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

function nonEmptyLines(value) {
  return String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function enterpriseRecord(slotCode, value, supported, stamp) {
  const coverageStatus = !supported
    ? 'unknown'
    : (enterpriseForm.partialSlots.includes(slotCode) ? 'partial' : 'supported')
  return {
    slot_code: slotCode,
    coverage_status: coverageStatus,
    value: supported ? value : null,
    source_record_id: `runtime-lab:${slotCode}`,
    source_version: stamp,
    source_status: supported ? enterpriseForm.sourceStatus : 'unknown',
    source_label: enterpriseForm.sourceLabel.trim(),
    valid_from: supported ? enterpriseForm.validFrom : null,
    valid_to: supported ? enterpriseForm.validTo : null,
    checked_at: supported ? enterpriseDraftAsOf.value : null,
  }
}

function buildEnterpriseSnapshotCommand() {
  const stamp = enterpriseForm.sourceVersion.trim()
  const qualifications = nonEmptyLines(enterpriseForm.qualifications)
  const projects = nonEmptyLines(enterpriseForm.performance)
  const people = nonEmptyLines(enterpriseForm.personnel)
  const forms = String(enterpriseForm.bondForms || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean)
  const clientRisks = nonEmptyLines(enterpriseForm.clientRisks).map((line) => {
    const [counterparty, riskLevel = 'unknown'] = line.split('|').map((item) => item.trim())
    return { counterparty, risk_level: riskLevel.toLowerCase(), active: true }
  }).filter((item) => item.counterparty)
  const riskKnown = enterpriseForm.riskStatus !== 'unknown'
  return {
    as_of: enterpriseDraftAsOf.value,
    change_note: enterpriseForm.changeNote.trim(),
    records: [
      enterpriseRecord('I01', { legal_name: enterpriseForm.legalName.trim() }, Boolean(enterpriseForm.legalName.trim()), stamp),
      enterpriseRecord('I02', { records: qualifications.map((name) => ({ name, code: name, status: 'active' })) }, qualifications.length > 0, stamp),
      enterpriseRecord('I03', { license_no: enterpriseForm.safetyLicense.trim(), status: 'active' }, Boolean(enterpriseForm.safetyLicense.trim()), stamp),
      enterpriseRecord('I04', { projects: projects.map((name) => ({ name, code: name, status: 'completed' })) }, projects.length > 0, stamp),
      enterpriseRecord('I05', { people: people.map((role) => ({ role, code: role, available: true })) }, people.length > 0, stamp),
      enterpriseRecord('I06', { available_cash_cny: Number(enterpriseForm.availableCash || 0) }, enterpriseForm.financialKnown, stamp),
      enterpriseRecord('I07', { max_bond_cny: Number(enterpriseForm.maxBond || 0), available_cash_cny: Number(enterpriseForm.availableCash || 0), supported_forms: forms }, enterpriseForm.guaranteeKnown, stamp),
      enterpriseRecord('I08', { available_person_days: Number(enterpriseForm.personDays || 0) }, enterpriseForm.bidCapacityKnown, stamp),
      enterpriseRecord('I09', { rules: enterpriseForm.riskStatus === 'triggered' ? [{ rule_code: 'manual-global-risk', scope: 'global', triggered: true }] : [] }, riskKnown, stamp),
      enterpriseRecord('I10', { status: enterpriseForm.complianceStatus }, enterpriseForm.complianceStatus !== 'unknown', stamp),
      enterpriseRecord('I11', { records: clientRisks }, riskKnown || clientRisks.length > 0, stamp),
    ],
  }
}

function recordMap(snapshot) {
  return new Map((snapshot?.records || []).map((record) => [record.slot_code, record]))
}

function joinRecordValues(items, field) {
  return (items || []).map((item) => String(item?.[field] || item?.name || '').trim()).filter(Boolean).join('\n')
}

function hydrateEnterpriseFormFromSnapshot() {
  const records = recordMap(enterpriseSnapshot.value)
  const value = (slotCode) => records.get(slotCode)?.value || {}
  const known = (slotCode) => ['supported', 'partial'].includes(records.get(slotCode)?.coverage_status)
  const firstKnown = [...records.values()].find((record) => known(record.slot_code))
  enterpriseForm.sourceLabel = firstKnown?.source_label || '企业能力台账（本地人工确认）'
  enterpriseForm.sourceVersion = firstKnown?.source_version || 'manual-v1'
  enterpriseForm.sourceStatus = ['verified', 'self_reported', 'imported'].includes(firstKnown?.source_status)
    ? firstKnown.source_status : 'self_reported'
  enterpriseForm.partialSlots = [...records.values()].filter((record) => record.coverage_status === 'partial').map((record) => record.slot_code)
  enterpriseForm.validFrom = firstKnown?.valid_from || null
  enterpriseForm.validTo = firstKnown?.valid_to || null
  enterpriseForm.legalName = String(value('I01').legal_name || '')
  enterpriseForm.qualifications = joinRecordValues(value('I02').records, 'name')
  enterpriseForm.safetyLicense = String(value('I03').license_no || '')
  enterpriseForm.performance = joinRecordValues(value('I04').projects, 'name')
  enterpriseForm.personnel = joinRecordValues(value('I05').people, 'role')
  enterpriseForm.financialKnown = known('I06')
  enterpriseForm.availableCash = Number(value('I06').available_cash_cny || value('I07').available_cash_cny || 0)
  enterpriseForm.guaranteeKnown = known('I07')
  enterpriseForm.maxBond = Number(value('I07').max_bond_cny || 0)
  enterpriseForm.bondForms = (value('I07').supported_forms || []).join(', ')
  enterpriseForm.bidCapacityKnown = known('I08')
  enterpriseForm.personDays = Number(value('I08').available_person_days || 0)
  const globalRisk = (value('I09').rules || []).some((rule) => rule?.scope === 'global' && rule?.triggered === true)
  enterpriseForm.riskStatus = known('I09') ? (globalRisk ? 'triggered' : 'clear') : 'unknown'
  enterpriseForm.complianceStatus = known('I10') ? String(value('I10').status || 'unknown') : 'unknown'
  enterpriseForm.clientRisks = (value('I11').records || []).map((record) => `${record.counterparty} | ${record.risk_level || 'unknown'}`).join('\n')
  enterpriseForm.changeNote = enterpriseSnapshot.value
    ? `基于 ${enterpriseSnapshot.value.version} 创建新版本`
    : '建立完全隔离本地企业能力基线'
}

async function openEnterpriseBaseline() {
  try {
    await refreshAuthority()
    if (!canConfigureEnterprise.value) {
      ElMessage.warning('当前进程无权配置企业能力基线')
      return
    }
    enterpriseValidation.value = null
    enterpriseDraftAsOf.value = new Date().toISOString()
    hydrateEnterpriseFormFromSnapshot()
    enterpriseVisible.value = true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法确认企业能力配置权限'))
  }
}

function validateEnterpriseDraftFields() {
  if (!enterpriseForm.sourceLabel.trim()) return '请填写数据来源名称'
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/.test(enterpriseForm.sourceVersion.trim())) return '来源版本只能使用字母、数字、点、下划线、冒号和短横线'
  if (!enterpriseForm.changeNote.trim()) return '请填写本次企业能力基线的变更说明'
  if (enterpriseForm.validFrom && enterpriseForm.validTo && new Date(enterpriseForm.validFrom) > new Date(enterpriseForm.validTo)) return '有效起始时间不能晚于截止时间'
  return ''
}

async function validateEnterpriseBaseline() {
  if (!canConfigureEnterprise.value) {
    ElMessage.warning('当前进程无权校验企业能力基线')
    return
  }
  const errorMessage = validateEnterpriseDraftFields()
  if (errorMessage) {
    ElMessage.warning(errorMessage)
    return
  }
  if (!enterpriseDraftAsOf.value) enterpriseDraftAsOf.value = new Date().toISOString()
  enterpriseValidationBusy.value = true
  try {
    enterpriseValidation.value = responseData(
      await bidAssessmentRuntimeLabApi.validateEnterpriseBaseline(buildEnterpriseSnapshotCommand()),
    )
    ElMessage.success(enterpriseValidation.value.acceptance_ready
      ? '候选基线通过七项硬门企业侧验收'
      : '候选基线可冻结，但仍有企业侧输入待补齐')
  } catch (error) {
    enterpriseValidation.value = null
    ElMessage.error(runtimeLabErrorMessage(error, '企业能力基线校验失败'))
  } finally {
    enterpriseValidationBusy.value = false
  }
}

async function freezeEnterpriseSnapshot() {
  if (!canConfigureEnterprise.value) {
    ElMessage.warning('当前进程无权配置企业能力快照')
    return
  }
  if (!canFreezeEnterprise.value) {
    ElMessage.warning('请先校验当前候选基线，再按候选 Hash 冻结')
    return
  }
  const candidateHash = enterpriseValidation.value.candidate_snapshot_hash
  enterpriseBusy.value = true
  try {
    const response = await bidAssessmentRuntimeLabApi.createEnterpriseSnapshot(
      buildEnterpriseSnapshotCommand(),
      idempotencyKey('enterprise-snapshot'),
      candidateHash,
    )
    const frozen = responseData(response)
    if (frozen?.snapshot_hash !== candidateHash) throw new Error('candidate snapshot hash mismatch')
    enterpriseSnapshot.value = frozen
    enterpriseBusinessBaseline.value = null
    enterpriseValidation.value = null
    enterpriseDraftAsOf.value = ''
    enterpriseVisible.value = false
    await refreshAuthority()
    ElMessage.success('I01—I11 企业能力快照已冻结，新 Run 将固定使用该版本')
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '企业能力快照冻结失败'))
  } finally {
    enterpriseBusy.value = false
  }
}

function selectEvidenceFile(event) {
  evidenceImportFile.value = event.target?.files?.[0] || null
}

function hydrateEvidencePackageForm() {
  const slotMap = new Map(
    (enterpriseEvidencePackage.value?.slots || []).map((slot) => [slot.slot_code, slot]),
  )
  for (const slot of enterpriseSlotOptions) {
    const current = slotMap.get(slot.code)
    evidencePackageForm.itemIds[slot.code] = (current?.evidence_items || []).map((item) => item.evidence_item_id)
    evidencePackageForm.notes[slot.code] = current?.note
      || (current?.evidence_items?.length ? `资料适用于 ${slot.code} ${slot.label}` : '当前未提供适用资料，保持 unknown 并进入跟进项。')
  }
  evidencePackageForm.packageLabel = enterpriseEvidencePackage.value
    ? `${enterpriseEvidencePackage.value.package_label} 新版本`
    : '旗胜企业能力资料包'
  evidencePackageForm.changeNote = enterpriseEvidencePackage.value
    ? `基于 ${enterpriseEvidencePackage.value.version} 补充或替换企业资料`
    : '导入经业务负责人确认的真实企业能力资料'
}

async function openEvidenceImport() {
  try {
    await refreshAuthority()
    if (!canConfigureEvidenceImport.value) {
      ElMessage.warning('当前进程无权导入真实企业能力资料')
      return
    }
    evidencePackageAsOf.value = new Date().toISOString()
    evidencePackageValidation.value = null
    hydrateEvidencePackageForm()
    evidenceImportVisible.value = true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法进入企业资料导入'))
  }
}

function validateEvidenceItemFields() {
  if (!evidenceImportFile.value) return '请选择企业资料文件'
  if (!evidenceImportForm.sourceLabel.trim()) return '请填写来源名称'
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(evidenceImportForm.sourceRecordId.trim())) return '逻辑来源编号格式无效'
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/.test(evidenceImportForm.sourceVersion.trim())) return '来源版本格式无效'
  if (evidenceImportForm.validFrom && evidenceImportForm.validTo && new Date(evidenceImportForm.validFrom) > new Date(evidenceImportForm.validTo)) return '有效起始时间不能晚于截止时间'
  return ''
}

async function uploadEvidenceItem() {
  if (!canConfigureEvidenceImport.value) {
    ElMessage.warning('当前进程无权上传企业资料')
    return
  }
  const errorMessage = validateEvidenceItemFields()
  if (errorMessage) {
    ElMessage.warning(errorMessage)
    return
  }
  evidenceItemBusy.value = true
  try {
    const hash = await sha256Hex(evidenceImportFile.value)
    const formData = new FormData()
    formData.append('file', evidenceImportFile.value)
    formData.append('evidence_class', evidenceImportForm.evidenceClass)
    formData.append('source_record_id', evidenceImportForm.sourceRecordId.trim())
    formData.append('source_version', evidenceImportForm.sourceVersion.trim())
    formData.append('source_label', evidenceImportForm.sourceLabel.trim())
    if (evidenceImportForm.validFrom) formData.append('valid_from', evidenceImportForm.validFrom)
    if (evidenceImportForm.validTo) formData.append('valid_to', evidenceImportForm.validTo)
    const uploaded = responseData(await bidAssessmentRuntimeLabApi.uploadEnterpriseEvidenceItem(
      formData,
      hash,
      idempotencyKey('enterprise-evidence-item'),
    ))
    if (uploaded?.content_sha256 !== hash) throw new Error('enterprise evidence hash mismatch')
    evidenceImportFile.value = null
    await refreshAuthority()
    ElMessage.success(uploaded.created ? '企业资料已按 SHA-256 不可变导入' : '相同企业资料已存在，已安全重放')
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '企业资料导入失败'))
  } finally {
    evidenceItemBusy.value = false
  }
}

function buildEvidencePackageCommand() {
  return {
    package_label: evidencePackageForm.packageLabel.trim(),
    as_of: evidencePackageAsOf.value,
    change_note: evidencePackageForm.changeNote.trim(),
    slots: enterpriseSlotOptions.map((slot) => ({
      slot_code: slot.code,
      evidence_item_ids: [...evidencePackageForm.itemIds[slot.code]],
      note: evidencePackageForm.notes[slot.code].trim() || null,
    })),
  }
}

async function validateEvidencePackage() {
  if (!canConfigureEvidenceImport.value) {
    ElMessage.warning('当前进程无权校验企业资料包')
    return
  }
  if (!evidencePackageForm.packageLabel.trim() || !evidencePackageForm.changeNote.trim()) {
    ElMessage.warning('请填写资料包名称和变更说明')
    return
  }
  if (!evidencePackageAsOf.value) evidencePackageAsOf.value = new Date().toISOString()
  evidencePackageValidationBusy.value = true
  try {
    evidencePackageValidation.value = responseData(
      await bidAssessmentRuntimeLabApi.validateEnterpriseEvidencePackage(buildEvidencePackageCommand()),
    )
    ElMessage[evidencePackageValidation.value.can_freeze ? 'success' : 'warning'](
      evidencePackageValidation.value.can_freeze ? '企业资料包校验通过，可以冻结' : '企业资料包仍有文件或映射阻断项',
    )
  } catch (error) {
    evidencePackageValidation.value = null
    ElMessage.error(runtimeLabErrorMessage(error, '企业资料包校验失败'))
  } finally {
    evidencePackageValidationBusy.value = false
  }
}

async function freezeEvidencePackage() {
  if (!canFreezeEvidencePackage.value) {
    ElMessage.warning('请先校验当前企业资料包，再按 Candidate Hash 冻结')
    return
  }
  evidencePackageBusy.value = true
  try {
    const candidateHash = evidencePackageValidation.value.candidate_hash
    const frozen = responseData(await bidAssessmentRuntimeLabApi.createEnterpriseEvidencePackage(
      buildEvidencePackageCommand(),
      idempotencyKey('enterprise-evidence-package'),
      candidateHash,
    ))
    if (frozen?.candidate_hash !== candidateHash) throw new Error('enterprise evidence package candidate hash mismatch')
    enterpriseEvidencePackage.value = frozen
    evidencePackageValidation.value = null
    evidenceImportVisible.value = false
    await refreshAuthority()
    ElMessage.success('真实企业资料包已不可变冻结；下一步可建立绑定该 Package Hash 的业务基线')
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '企业资料包冻结失败'))
  } finally {
    evidencePackageBusy.value = false
  }
}

function businessRecordStatus(slotCode) {
  return recordMap(enterpriseSnapshot.value).get(slotCode)?.coverage_status || 'missing'
}

function packageItemsForSlot(slotCode) {
  return (enterpriseEvidencePackage.value?.slots || [])
    .find((slot) => slot.slot_code === slotCode)?.evidence_items || []
}

function selectBusinessEvidenceItem(slotCode, evidenceItemId) {
  const item = packageItemsForSlot(slotCode)
    .find((candidate) => candidate.evidence_item_id === evidenceItemId)
  if (!item) {
    businessBaselineForm.evidenceRefs[slotCode] = ''
    businessBaselineForm.evidenceHashes[slotCode] = ''
    return
  }
  businessBaselineForm.evidenceClasses[slotCode] = item.evidence_class
  businessBaselineForm.evidenceRefs[slotCode] = item.evidence_ref
  businessBaselineForm.evidenceHashes[slotCode] = item.content_sha256
}

function hydrateBusinessBaselineForm() {
  const records = recordMap(enterpriseSnapshot.value)
  const packageSlots = new Map(
    (enterpriseEvidencePackage.value?.slots || []).map((slot) => [slot.slot_code, slot]),
  )
  businessBaselineForm.reviewedAsOf = new Date().toISOString()
  businessBaselineForm.reviewNote = `复核企业能力快照 ${enterpriseSnapshot.value?.version || ''} 的 I01—I11 来源、有效期和业务使用边界`
  for (const item of enterpriseSlotOptions) {
    const record = records.get(item.code) || {}
    const coverage = String(record.coverage_status || 'unknown')
    const sourceLabel = String(record.source_label || '企业能力来源').trim()
    const sourceVersion = String(record.source_version || 'unknown').trim()
    businessBaselineForm.dispositions[item.code] = 'confirmed'
    businessBaselineForm.evidenceHashes[item.code] = ''
    businessBaselineForm.evidenceItemIds[item.code] = ''
    if (coverage === 'unknown') {
      businessBaselineForm.evidenceClasses[item.code] = 'not_available'
      businessBaselineForm.evidenceRefs[item.code] = ''
      businessBaselineForm.notes[item.code] = '已确认当前没有可用权威来源，保持 unknown，待业务负责人补充。'
    } else if (capabilities.value.enterprise_evidence_import_enabled) {
      const evidenceItem = packageSlots.get(item.code)?.evidence_items?.[0]
      if (evidenceItem) {
        businessBaselineForm.evidenceClasses[item.code] = evidenceItem.evidence_class
        businessBaselineForm.evidenceRefs[item.code] = evidenceItem.evidence_ref
        businessBaselineForm.evidenceHashes[item.code] = evidenceItem.content_sha256
        businessBaselineForm.evidenceItemIds[item.code] = evidenceItem.evidence_item_id
        businessBaselineForm.notes[item.code] = `已核对资料包 ${enterpriseEvidencePackage.value?.version || ''} 中映射到 ${item.code} 的原始资料。`
      } else {
        businessBaselineForm.evidenceClasses[item.code] = 'not_available'
        businessBaselineForm.evidenceRefs[item.code] = ''
        businessBaselineForm.notes[item.code] = '当前资料包没有映射到该槽位的权威资料，业务基线将保持阻断。'
      }
    } else {
      businessBaselineForm.evidenceClasses[item.code] = 'management_attestation'
      businessBaselineForm.evidenceRefs[item.code] = `${item.code}:${sourceLabel}:${sourceVersion}`.slice(0, 300)
      businessBaselineForm.notes[item.code] = `业务负责人已复核 ${item.code} 的当前记录与适用边界；正式来源接入前按跟进项管理。`
    }
  }
}

function buildBusinessBaselineCommand() {
  return {
    snapshot_id: enterpriseSnapshot.value?.snapshot_id || '',
    evidence_package_id: enterpriseEvidencePackage.value?.evidence_package_id || null,
    reviewed_as_of: businessBaselineForm.reviewedAsOf,
    review_note: businessBaselineForm.reviewNote.trim(),
    slot_reviews: enterpriseSlotOptions.map((item) => ({
      slot_code: item.code,
      disposition: businessBaselineForm.dispositions[item.code],
      evidence_class: businessBaselineForm.evidenceClasses[item.code],
      evidence_ref: businessBaselineForm.evidenceRefs[item.code].trim() || null,
      evidence_hash: businessBaselineForm.evidenceHashes[item.code].trim().toLowerCase() || null,
      evidence_item_id: businessBaselineForm.evidenceItemIds[item.code] || null,
      note: businessBaselineForm.notes[item.code].trim() || null,
    })),
  }
}

async function openBusinessBaselineReview() {
  try {
    await refreshAuthority()
    if (!canConfigureBusinessBaseline.value) {
      ElMessage.warning('当前企业快照、运行模式或权限不允许建立真实企业能力基线')
      return
    }
    hydrateBusinessBaselineForm()
    businessBaselineValidation.value = null
    businessBaselineVisible.value = true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法进入真实企业能力核验'))
  }
}

async function validateBusinessBaseline() {
  if (!canConfigureBusinessBaseline.value) {
    ElMessage.warning('当前进程无权校验真实企业能力基线')
    return
  }
  if (!businessBaselineForm.reviewNote.trim()) {
    ElMessage.warning('请填写负责人核验说明')
    return
  }
  businessBaselineValidationBusy.value = true
  try {
    businessBaselineValidation.value = responseData(
      await bidAssessmentRuntimeLabApi.validateEnterpriseBusinessBaseline(buildBusinessBaselineCommand()),
    )
    ElMessage[businessBaselineValidation.value.can_freeze ? 'success' : 'warning'](
      businessBaselineValidation.value.can_freeze ? '真实来源核验通过，可以冻结企业业务基线' : '仍有来源、有效期或复核阻断项',
    )
  } catch (error) {
    businessBaselineValidation.value = null
    ElMessage.error(runtimeLabErrorMessage(error, '真实企业能力基线校验失败'))
  } finally {
    businessBaselineValidationBusy.value = false
  }
}

async function freezeBusinessBaseline() {
  if (!canFreezeBusinessBaseline.value) {
    ElMessage.warning('请先校验当前真实企业能力候选，再按 Candidate Hash 冻结')
    return
  }
  businessBaselineBusy.value = true
  try {
    const candidateHash = businessBaselineValidation.value.candidate_hash
    const response = await bidAssessmentRuntimeLabApi.createEnterpriseBusinessBaseline(
      buildBusinessBaselineCommand(),
      idempotencyKey('enterprise-business-baseline'),
      candidateHash,
    )
    const frozen = responseData(response)
    if (frozen?.candidate_hash !== candidateHash) throw new Error('enterprise business baseline candidate hash mismatch')
    enterpriseBusinessBaseline.value = frozen
    businessBaselineValidation.value = null
    businessBaselineVisible.value = false
    await refreshAuthority()
    ElMessage.success('真实企业能力基线已不可变冻结；下一次 Run 将绑定该 Baseline Hash')
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '真实企业能力基线冻结失败'))
  } finally {
    businessBaselineBusy.value = false
  }
}

function comparableFactLabel(factSlot) {
  return comparableFactLabels[factSlot] || factSlot
}

function resetFactVerificationDraft() {
  factVerificationForm.facts = []
  factVerificationValidation.value = null
}

async function openFactVerification() {
  try {
    await refreshAuthority()
    if (!canConfigureFactVerification.value) {
      ElMessage.warning('当前真实企业基线、已完成 Run 或写入权限尚未满足事实核验条件')
      return
    }
    factVerificationForm.sourceRunId = selectedRunStatus.value === 'succeeded'
      ? selectedRunId.value
      : (completedRuns.value[0]?.run_id || '')
    resetFactVerificationDraft()
    factVerificationVisible.value = true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法进入真实事实核验'))
  }
}

async function loadFactVerificationDraft() {
  const sourceRun = completedRuns.value.find(
    (run) => run.run_id === factVerificationForm.sourceRunId,
  )
  if (!sourceRun || !enterpriseBusinessBaseline.value?.business_baseline_id) {
    ElMessage.warning('请选择一个已完成 Run，并确认真实企业能力基线已冻结')
    return
  }
  factDraftBusy.value = true
  try {
    const draft = responseData(await bidAssessmentRuntimeLabApi.hardGateComparisonDraft(
      sourceRun.assessment_id,
      sourceRun.run_id,
      enterpriseBusinessBaseline.value.business_baseline_id,
    ))
    factVerificationForm.assessmentId = draft.assessment_id
    factVerificationForm.businessBaselineId = draft.business_baseline_id
    factVerificationForm.reviewedAsOf = draft.reviewed_as_of
    factVerificationForm.reviewNote = draft.review_note
    factVerificationForm.facts = (draft.facts || []).map((fact) => ({
      ...fact,
      valueText: fact.canonical_value == null
        ? ''
        : JSON.stringify(fact.canonical_value, null, 2),
      evidenceText: (
        fact.source_side === 'tender'
          ? fact.evidence_atom_ids
          : fact.evidence_item_ids
      ).join(', '),
    }))
    ElMessage.info('已载入治理事实候选；系统默认标为 partial，请逐项确认后再改为 supported')
  } catch (error) {
    resetFactVerificationDraft()
    ElMessage.error(runtimeLabErrorMessage(error, '硬门事实候选不可用'))
  } finally {
    factDraftBusy.value = false
  }
}

function governedIdList(value) {
  return [...new Set(String(value || '').split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean))].sort()
}

function buildFactVerificationCommand() {
  const facts = factVerificationForm.facts.map((fact) => {
    if (fact.verification_status === 'unknown') {
      return {
        fact_slot: fact.fact_slot,
        source_side: fact.source_side,
        verification_status: 'unknown',
        value_type: null,
        canonical_value: null,
        evidence_item_ids: [],
        evidence_atom_ids: [],
        note: fact.note.trim(),
      }
    }
    const evidenceIds = governedIdList(fact.evidenceText)
    return {
      fact_slot: fact.fact_slot,
      source_side: fact.source_side,
      verification_status: fact.verification_status,
      value_type: fact.value_type.trim(),
      canonical_value: JSON.parse(fact.valueText),
      evidence_item_ids: fact.source_side === 'enterprise' ? evidenceIds : [],
      evidence_atom_ids: fact.source_side === 'tender' ? evidenceIds : [],
      note: fact.note.trim(),
    }
  })
  return {
    assessment_id: factVerificationForm.assessmentId,
    source_run_id: factVerificationForm.sourceRunId,
    business_baseline_id: factVerificationForm.businessBaselineId,
    reviewed_as_of: factVerificationForm.reviewedAsOf,
    review_note: factVerificationForm.reviewNote.trim(),
    facts,
  }
}

function validateFactVerificationFields() {
  if (!factVerificationForm.reviewNote.trim()) return '请填写负责人核验说明'
  if (factVerificationForm.facts.length !== 16) return '必须逐项核验全部 16 个可比较事实'
  for (const fact of factVerificationForm.facts) {
    if (!fact.note.trim()) return `${comparableFactLabel(fact.fact_slot)} 缺少核验备注`
    if (fact.verification_status === 'unknown') continue
    if (!fact.value_type.trim() || !fact.valueText.trim()) return `${comparableFactLabel(fact.fact_slot)} 缺少机器值`
    if (!governedIdList(fact.evidenceText).length) return `${comparableFactLabel(fact.fact_slot)} 缺少权威证据 ID`
    try {
      JSON.parse(fact.valueText)
    } catch (_error) {
      return `${comparableFactLabel(fact.fact_slot)} 的规范化值不是合法 JSON`
    }
  }
  return ''
}

async function validateFactVerification() {
  if (!canConfigureFactVerification.value) {
    ElMessage.warning('当前进程无权校验硬门可比事实')
    return
  }
  const message = validateFactVerificationFields()
  if (message) {
    ElMessage.warning(message)
    return
  }
  factVerificationForm.reviewedAsOf = new Date().toISOString()
  factVerificationValidationBusy.value = true
  try {
    factVerificationValidation.value = responseData(
      await bidAssessmentRuntimeLabApi.validateHardGateComparisonBaseline(
        buildFactVerificationCommand(),
      ),
    )
    ElMessage[factVerificationValidation.value.follow_up_codes?.length ? 'warning' : 'success'](
      factVerificationValidation.value.follow_up_codes?.length
        ? '可冻结，但 partial/unknown 项仍会使对应硬门保持 unknown'
        : '16 项事实均具备可比较权威证据，可以冻结',
    )
  } catch (error) {
    factVerificationValidation.value = null
    ElMessage.error(runtimeLabErrorMessage(error, '硬门可比事实校验失败'))
  } finally {
    factVerificationValidationBusy.value = false
  }
}

async function freezeFactVerification() {
  if (!canFreezeFactVerification.value) {
    ElMessage.warning('请先校验当前 16 项事实，再按 Candidate Hash 冻结')
    return
  }
  factVerificationBusy.value = true
  try {
    const candidateHash = factVerificationValidation.value.candidate_hash
    const frozen = responseData(await bidAssessmentRuntimeLabApi.createHardGateComparisonBaseline(
      buildFactVerificationCommand(),
      idempotencyKey('hard-gate-comparison-baseline'),
      candidateHash,
    ))
    if (frozen?.candidate_hash !== candidateHash) throw new Error('hard-gate comparison candidate hash mismatch')
    hardGateComparisonBaseline.value = frozen
    factVerificationValidation.value = null
    factVerificationVisible.value = false
    await refreshAuthority()
    ElMessage.success('硬门可比事实已不可变冻结；下一次 reanalysis Run 将绑定该 Baseline Hash')
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '硬门可比事实冻结失败'))
  } finally {
    factVerificationBusy.value = false
  }
}

function resetReleaseForm() {
  releaseForm.reviewNote = ''
  for (const code of releaseGateCodes) {
    releaseForm.gateDispositions[code] = 'not_reviewed'
    releaseForm.gateNotes[code] = ''
  }
  for (const item of releaseQualityOptions) {
    releaseForm.qualityDispositions[item.code] = 'not_reviewed'
    releaseForm.qualityNotes[item.code] = ''
  }
  releaseValidation.value = null
}

function buildReleaseCandidateCommand() {
  return {
    run_id: selectedRunId.value,
    review_note: releaseForm.reviewNote.trim(),
    gate_reviews: releaseGateCodes.map((gateCode) => ({
      gate_code: gateCode,
      disposition: releaseForm.gateDispositions[gateCode],
      note: releaseForm.gateNotes[gateCode].trim() || null,
    })),
    quality_reviews: releaseQualityOptions.map((item) => ({
      code: item.code,
      disposition: releaseForm.qualityDispositions[item.code],
      note: releaseForm.qualityNotes[item.code].trim() || null,
    })),
  }
}

async function loadReleaseCandidate(runId = selectedRunId.value) {
  releaseCandidate.value = null
  if (!runId || !capabilities.value.mvp_release_candidate_enabled) return
  try {
    const payload = responseData(await bidAssessmentRuntimeLabApi.releaseCandidate(runId)) || {}
    releaseCandidate.value = payload.release_candidate || null
  } catch (error) {
    if (error?.response?.status !== 404) throw error
  }
}

async function openReleaseAcceptance() {
  try {
    await refreshAuthority()
    if (!canConfigureRelease.value) {
      ElMessage.warning('当前 Run、报告或 Runtime 权限尚未满足业务验收条件')
      return
    }
    resetReleaseForm()
    releaseForm.reviewNote = `复核 ${reportDetail.value?.title || selectedRunId.value} 的七项硬门、报告与引用`
    releaseVisible.value = true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法进入业务验收'))
  }
}

function validateReleaseReviewFields() {
  if (!releaseForm.reviewNote.trim()) return '请填写总体验收说明'
  const gates = new Map((reportDetail.value?.report?.hard_gates || []).map((gate) => [gate.gate_code, gate]))
  for (const code of releaseGateCodes) {
    const disposition = releaseForm.gateDispositions[code]
    if (disposition !== 'confirmed') return `${code} 尚未确认或需要修正`
    if (['fail', 'unknown'].includes(gates.get(code)?.status) && !releaseForm.gateNotes[code].trim()) {
      return `${code} 为 ${gates.get(code)?.status}，请填写复核说明`
    }
  }
  for (const item of releaseQualityOptions) {
    if (releaseForm.qualityDispositions[item.code] !== 'confirmed') return `${item.label} 尚未确认`
  }
  return ''
}

async function validateReleaseAcceptance() {
  if (!canConfigureRelease.value) {
    ElMessage.warning('当前进程无权校验 MVP Release Candidate')
    return
  }
  const message = validateReleaseReviewFields()
  if (message) {
    ElMessage.warning(message)
    return
  }
  releaseValidationBusy.value = true
  try {
    releaseValidation.value = responseData(
      await bidAssessmentRuntimeLabApi.validateReleaseCandidate(buildReleaseCandidateCommand()),
    )
    ElMessage[releaseValidation.value.can_freeze ? 'success' : 'warning'](
      releaseValidation.value.can_freeze ? '业务验收已通过，可以冻结 RC' : '业务验收仍存在阻断项',
    )
  } catch (error) {
    releaseValidation.value = null
    ElMessage.error(runtimeLabErrorMessage(error, '业务验收校验失败'))
  } finally {
    releaseValidationBusy.value = false
  }
}

async function freezeReleaseCandidate() {
  if (!canFreezeRelease.value) {
    ElMessage.warning('请先完成当前业务验收校验')
    return
  }
  releaseBusy.value = true
  try {
    const candidateHash = releaseValidation.value.candidate_hash
    const response = await bidAssessmentRuntimeLabApi.createReleaseCandidate(
      buildReleaseCandidateCommand(),
      idempotencyKey('mvp-release-candidate'),
      candidateHash,
    )
    const frozen = responseData(response)
    if (frozen?.candidate_hash !== candidateHash) throw new Error('MVP RC candidate hash mismatch')
    releaseCandidate.value = frozen
    releaseValidation.value = null
    releaseVisible.value = false
    ElMessage.success('MVP Release Candidate 已不可变冻结')
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, 'MVP Release Candidate 冻结失败'))
  } finally {
    releaseBusy.value = false
  }
}

function onFilesSelected(event) {
  if (!requireExecute('选择上传资料')) {
    event.target.value = ''
    return
  }
  intakeFiles.value = Array.from(event.target?.files || [])
}

function requireExecute(action = '执行该操作') {
  if (canExecute.value) return true
  ElMessage.warning(`当前 Runtime Lab 为 view-only，不能${action}`)
  return false
}

async function openIntake() {
  try {
    const fingerprint = await refreshAuthority()
    if (!requireExecute('新建研判或上传资料')) return
    intakeAuthorityFingerprint.value = fingerprint
    intakeVisible.value = true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法确认 Execute 就绪状态'))
  }
}

async function requireFreshExecute(action, expectedFingerprint = authorityFingerprint.value) {
  try {
    const currentFingerprint = await refreshAuthority()
    if (!requireExecute(action)) return false
    if (!expectedFingerprint || currentFingerprint !== expectedFingerprint) {
      intakeVisible.value = false
      intakeFiles.value = []
      intakeAuthorityFingerprint.value = ''
      ElMessage.warning('Runtime Lab 权限或依赖状态已变化，请确认后重新操作')
      return false
    }
    return true
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '无法重新确认 Execute 权限'))
    return false
  }
}

async function createAssessmentAndUpload() {
  if (!requireExecute('创建研判或上传资料')) return
  if (!intakeForm.title.trim() || !intakeForm.clientName.trim()) {
    ElMessage.warning('请填写项目名称和客户/招标人')
    return
  }
  if (!intakeFiles.value.length) {
    ElMessage.warning('请至少选择一份招标资料')
    return
  }
  if (!(await requireFreshExecute('创建研判或上传资料', intakeAuthorityFingerprint.value))) return
  intakeBusy.value = true
  try {
    const assessmentResponse = await bidAssessmentRuntimeLabApi.createAssessment({
      title: intakeForm.title.trim(),
      client_name: intakeForm.clientName.trim(),
      internal_note: intakeForm.note.trim() || null,
      external_ref: null,
    }, idempotencyKey('assessment'))
    const assessment = responseData(assessmentResponse)
    workspace.assessmentId = assessment.assessment_id
    workspace.assessmentEtag = responseEtag(assessmentResponse)
    workspace.businessStatus = assessment.business_status
    workspace.statusMessage = '正在上传资料'
    window.sessionStorage.setItem('bid-mvp1-assessment-id', workspace.assessmentId)

    const batchResponse = await bidAssessmentRuntimeLabApi.createUploadBatch(
      workspace.assessmentId,
      { purpose: 'initial', base_manifest_id: null },
      workspace.assessmentEtag,
      idempotencyKey('batch'),
    )
    const batch = responseData(batchResponse)
    workspace.batchId = batch.batch_id
    workspace.batchEtag = responseEtag(batchResponse)

    for (const file of intakeFiles.value) {
      workspace.statusMessage = `上传 ${file.name}`
      const form = new FormData()
      form.append('client_file_id', `file-${window.crypto.randomUUID()}`)
      form.append('operation', 'add')
      if (file.webkitRelativePath) form.append('relative_path', file.webkitRelativePath)
      form.append('file', file, file.name)
      const uploadResponse = await bidAssessmentRuntimeLabApi.uploadFile(
        workspace.batchId,
        form,
        await sha256Hex(file),
        idempotencyKey('file'),
      )
      workspace.batchEtag = responseHeader(uploadResponse, 'x-batch-etag', workspace.batchEtag)
    }

    workspace.statusMessage = '提交不可变 Manifest'
    const commitResponse = await bidAssessmentRuntimeLabApi.commitUploadBatch(
      workspace.batchId,
      {
        expected_file_count: intakeFiles.value.length,
        expected_deactivation_count: 0,
        change_note: intakeForm.note.trim() || null,
        confirm_start_analysis: true,
      },
      workspace.batchEtag,
      idempotencyKey('commit'),
    )
    const committed = responseData(commitResponse)
    workspace.manifestId = committed.manifest?.manifest_id || ''
    workspace.assessmentEtag = responseEtag(commitResponse, workspace.assessmentEtag)
    workspace.businessStatus = committed.assessment?.business_status || 'preparing'
    workspace.statusMessage = '资料已提交，等待解析与标段识别'
    intakeVisible.value = false
    intakeFiles.value = []
    intakeAuthorityFingerprint.value = ''
    ElMessage.success('资料已提交，Agent 正在准备研判')
    await refreshAssessmentStatus()
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '创建研判或上传资料失败'))
  } finally {
    intakeBusy.value = false
  }
}

async function refreshAssessmentStatus() {
  if (!workspace.assessmentId || !capabilities.value.enabled) return
  intakeBusy.value = true
  if (statusRefreshTimer) clearTimeout(statusRefreshTimer)
  try {
    const assessmentResponse = await bidAssessmentRuntimeLabApi.assessment(workspace.assessmentId)
    const assessment = responseData(assessmentResponse)
    workspace.assessmentEtag = responseEtag(assessmentResponse, workspace.assessmentEtag)
    workspace.businessStatus = assessment.business_status || ''
    workspace.manifestId = assessment.current_manifest?.manifest_id || workspace.manifestId

    const lotPage = responseData(await bidAssessmentRuntimeLabApi.lots(workspace.assessmentId)) || {}
    workspace.manifestId = lotPage.manifest?.manifest_id || workspace.manifestId
    workspace.lots = lotPage.selection_required ? (lotPage.candidates || []) : []
    if (workspace.lots.length && !workspace.selectedLotId) {
      workspace.selectedLotId = workspace.lots[0]?.lot_id || ''
    }
    const generation = lotPage.generation?.status
    const statusLabels = {
      pending: '等待解析完成', running: '正在识别正文标段', succeeded: '标段识别完成',
      failed: '标段识别失败', blocked: '等待文档解析',
    }
    workspace.statusMessage = lotPage.selection_required
      ? '请选择研判标段'
      : (statusLabels[generation] || assessment.blocking_reason?.message || assessment.business_status)

    await loadLab()
    const activeRunId = assessment.active_run?.run_id
    if (activeRunId && runs.value.some((run) => run.run_id === activeRunId)) {
      selectedRunId.value = activeRunId
    }
    await loadReports(workspace.assessmentId, activeRunId || selectedRunId.value)

    const shouldPoll = canExecute.value
      && !reportDetail.value
      && !['failed', 'cancelled', 'stale_input'].includes(workspace.businessStatus)
    if (shouldPoll && !workspace.lots.length) {
      statusRefreshTimer = setTimeout(refreshAssessmentStatus, 4000)
    }
  } catch (error) {
    ElMessage.warning(runtimeLabErrorMessage(error, '暂时无法刷新研判状态'))
  } finally {
    intakeBusy.value = false
  }
}

async function confirmLot() {
  if (!requireExecute('启动研判 Run')) return
  if (!workspace.selectedLotId || !workspace.manifestId || !workspace.assessmentEtag) return
  if (!(await requireFreshExecute('启动研判 Run'))) return
  intakeBusy.value = true
  try {
    const response = await bidAssessmentRuntimeLabApi.selectLot(
      workspace.assessmentId,
      {
        manifest_id: workspace.manifestId,
        lot_id: workspace.selectedLotId,
        selection_note: intakeForm.note.trim() || null,
      },
      workspace.assessmentEtag,
      idempotencyKey('lot'),
    )
    const result = responseData(response)
    workspace.assessmentEtag = responseEtag(response, workspace.assessmentEtag)
    workspace.businessStatus = result.assessment?.business_status || 'preliminary_analyzing'
    workspace.lots = []
    workspace.statusMessage = '标段已冻结，等待 Agent 启动'
    ElMessage.success('标段已确认，Agent Run 已受理')
    statusRefreshTimer = setTimeout(refreshAssessmentStatus, 1000)
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '标段选择失败'))
  } finally {
    intakeBusy.value = false
  }
}

async function currentRunCommandContext(action) {
  const run = selectedRun.value
  if (!run?.assessment_id || !run?.run_id) throw new Error('RUN_NOT_SELECTED')
  const expectedFingerprint = authorityFingerprint.value
  if (!(await requireFreshExecute(action, expectedFingerprint))) return null
  const snapshotResponse = await bidAssessmentRuntimeLabApi.runSnapshot(run.assessment_id, run.run_id)
  const etag = responseEtag(snapshotResponse)
  if (!etag) throw new Error('RUN_ETAG_MISSING')
  return { run, etag }
}

async function cancelSelectedRun() {
  if (!canCancelRun.value) return
  try {
    await ElMessageBox.confirm(
      '取消会先持久化取消意图，再由 Worker 围栏活跃 Attempt、Model/Tool 调用并收敛 Run。是否继续？',
      '安全取消 Run',
      { confirmButtonText: '确认取消', cancelButtonText: '返回', type: 'warning' },
    )
  } catch (_error) {
    return
  }
  lifecycleBusy.value = true
  try {
    const context = await currentRunCommandContext('取消 Run')
    if (!context) return
    await bidAssessmentRuntimeLabApi.cancelRun(
      context.run.assessment_id,
      context.run.run_id,
      { reason: 'Runtime Lab 用户请求安全取消' },
      context.etag,
      idempotencyKey('run-cancel'),
    )
    ElMessage.success('取消请求已受理，Worker 正在执行围栏与终态收敛')
    await loadLab()
    await loadTrace()
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, '取消 Run 失败，请刷新状态后重试'))
  } finally {
    lifecycleBusy.value = false
  }
}

async function retrySelectedRun() {
  if (!canRetryRun.value) return
  try {
    await ElMessageBox.confirm(
      '系统会在同一 Run 内创建更高 fencing token 的新 Attempt，并从最近不可变 Checkpoint 恢复。是否继续？',
      '从 Checkpoint 重试',
      { confirmButtonText: '确认重试', cancelButtonText: '返回', type: 'warning' },
    )
  } catch (_error) {
    return
  }
  lifecycleBusy.value = true
  try {
    const context = await currentRunCommandContext('从 Checkpoint 重试 Run')
    if (!context) return
    await bidAssessmentRuntimeLabApi.retryRun(
      context.run.assessment_id,
      context.run.run_id,
      { retry_mode: 'from_latest_checkpoint', note: 'Runtime Lab 用户请求从最近 Checkpoint 恢复' },
      context.etag,
      idempotencyKey('run-retry'),
    )
    ElMessage.success('Checkpoint 重试已受理，旧执行已被 fencing')
    await loadLab()
    await loadTrace()
  } catch (error) {
    ElMessage.error(runtimeLabErrorMessage(error, 'Checkpoint 重试失败，请刷新状态后重试'))
  } finally {
    lifecycleBusy.value = false
  }
}

async function loadReports(assessmentId, preferredRunId = '') {
  if (!assessmentId || !capabilities.value.preliminary_report_enabled) {
    reports.value = []
    reportDetail.value = null
    releaseCandidate.value = null
    return
  }
  try {
    const page = responseData(await bidAssessmentRuntimeLabApi.reports(assessmentId)) || {}
    reports.value = page.items || []
    const selected = reports.value.find((item) => item.run_id === preferredRunId) || reports.value[0]
    reportDetail.value = selected
      ? responseData(await bidAssessmentRuntimeLabApi.report(selected.report_id))
      : null
    await loadReleaseCandidate(preferredRunId || selected?.run_id || '')
    if (reportDetail.value && preferredRunId === selectedRunId.value) activeTab.value = 'report'
  } catch (_error) {
    reports.value = []
    reportDetail.value = null
    releaseCandidate.value = null
  }
}

function showPreview() { selectedRunId.value = '' }

function startSse() {
  if (!capabilities.value.live_sse_enabled || !selectedRun.value?.assessment_id) return
  stopStream = streamAssessmentEvents(selectedRun.value.assessment_id, {
    onOpen: () => { sseConnected.value = true },
    onEvent: (event) => {
      const payload = event.data?.data || event.data || {}
      liveEvents.value.unshift({
        id: event.id || `live:${Date.now()}`,
        type: event.event || payload.event_type || 'message',
        source: 'live_sse',
        resource_type: payload.resource_type || 'assessment',
        resource_id: payload.resource_id || selectedRun.value?.assessment_id,
        status: 'published',
        occurred_at: payload.occurred_at || new Date().toISOString(),
      })
      liveEvents.value = liveEvents.value.slice(0, 50)
      if (autoRefresh.value) {
        if (refreshTimer) clearTimeout(refreshTimer)
        refreshTimer = setTimeout(() => loadTrace({ quiet: true }), 350)
      }
    },
    onError: () => { sseConnected.value = false },
  })
}

function stopSse() {
  stopStream?.()
  stopStream = null
  sseConnected.value = false
}

function preflightCheckLabel(status) {
  return ({ ready: '就绪', blocked: '阻断', deferred: '切换时校验', inactive: '未启用' })[status] || status
}
function preflightCheckTagType(status) {
  return ({ ready: 'success', blocked: 'danger', deferred: 'warning', inactive: 'info' })[status] || 'info'
}
function shortHash(value) { return value ? `${String(value).slice(0, 10)}…` : '-' }
function shortId(value) { return value ? `${String(value).slice(0, 14)}${String(value).length > 14 ? '…' : ''}` : '-' }
function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
function decisionLabel(value) {
  return ({ bid: '建议参与', conditional: '有条件继续', hold: '暂缓', no_bid: '不建议参与', insufficient: '证据不足' })[value] || value || '待研判'
}
function decisionTagType(value) {
  if (value === 'bid') return 'success'
  if (['no_bid', 'hold'].includes(value)) return 'danger'
  return 'warning'
}
function gateStatusLabel(value) { return ({ pass: '通过', fail: '未通过', unknown: '待核实', not_applicable: '不适用' })[value] || value }
function releaseOutcomeLabel(value) { return ({ accepted: '业务验收通过', accepted_with_follow_up: '验收通过，保留跟进项' })[value] || value || '待验收' }
function locatorLabel(locator) {
  const value = locator || {}
  if (value.page_no || value.page_number) return `第 ${value.page_no || value.page_number} 页`
  if (value.sheet_name) return `Sheet ${value.sheet_name}`
  if (value.section_path) return Array.isArray(value.section_path) ? value.section_path.join(' / ') : value.section_path
  return '正文定位'
}
function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
function statusClass(status) {
  if (['failed', 'stale', 'cancelled'].includes(status)) return 'danger'
  if (['succeeded', 'passed', 'immutable', 'frozen', 'ok', 'published'].includes(status)) return 'success'
  return 'active'
}

function buildPreviewTrace() {
  const at = '2026-08-13T09:00:00.000000Z'
  const nodes = [
    previewNode('runtime:retired', 'run', 'Legacy Workflow Removed', 'retired', at, {
      read_only: true,
      removed: ['P0-P4', 'PlanRevision', 'Task DAG', 'Plan Continuation'],
    }),
  ]
  const edges = []
  return {
    schema: 'bid.runtime.trace.v1',
    run: { run_id: 'retired-workflow', status: 'retired', current_stage: null },
    summary: { node_count: nodes.length, edge_count: 0, task_count: 0, attempt_count: 0, checkpoint_count: 0, model_call_count: 0, tool_call_count: 0 },
    redaction: { policy: 'control_plane_metadata_only', omitted: ['prompt_body', 'context_body', 'model_action_body', 'tool_arguments', 'tool_result_body', 'chain_of_thought'] },
    nodes, edges,
    timeline: nodes.map((node, index) => ({ id: node.id, type: node.kind, source: 'protocol_preview', resource_type: node.kind, resource_id: node.id, status: node.status, occurred_at: new Date(Date.parse(at) + index * 1000).toISOString() })),
  }
}

function previewNode(id, kind, label, status, createdAt, details) {
  return { id, kind, label, status, created_at: createdAt, updated_at: createdAt, details, hashes: { preview_hash: 'preview-not-authoritative' } }
}
</script>

<style scoped>
.runtime-lab-page { display: grid; gap: 18px; color: #0f172a; }
.lab-hero { position: relative; overflow: hidden; display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; padding: 28px; border-radius: 22px; color: #f8fafc; background: linear-gradient(125deg, #071a2d 0%, #0f3b46 52%, #155e75 100%); box-shadow: 0 18px 48px rgba(15, 23, 42, .18); }
.lab-hero::after { content: ''; position: absolute; width: 310px; height: 310px; right: -110px; top: -170px; border: 1px solid rgba(255,255,255,.18); border-radius: 50%; box-shadow: 0 0 0 55px rgba(255,255,255,.035), 0 0 0 110px rgba(255,255,255,.025); }
.hero-kicker { display: flex; align-items: center; gap: 8px; color: #67e8f9; font-size: 12px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.hero-kicker span { width: 24px; height: 2px; background: #22d3ee; }
.lab-hero h2 { margin: 10px 0 8px; font-size: 27px; }
.lab-hero p { max-width: 820px; margin: 0; color: #cbd5e1; line-height: 1.7; }
.hero-status { z-index: 1; display: grid; justify-items: end; gap: 12px; white-space: nowrap; }
.hero-status small { color: #a5f3fc; font-size: 11px; font-weight: 700; }
.connection { display: flex; align-items: center; gap: 7px; color: #cbd5e1; font-size: 12px; }
.connection i { width: 8px; height: 8px; border-radius: 50%; background: #64748b; }
.connection i.live { background: #22c55e; box-shadow: 0 0 0 5px rgba(34,197,94,.14); }
.lab-alert { border-radius: 14px; }
.mvp1-launch-card { display: flex; align-items: center; justify-content: space-between; gap: 28px; padding: 22px 24px; border: 1px solid #bae6fd; border-radius: 18px; background: linear-gradient(120deg, #f0fdfa, #f0f9ff); }
.launch-copy { display: grid; gap: 7px; }
.launch-copy small { color: #0e7490; font-weight: 900; letter-spacing: .12em; }
.launch-copy h3 { margin: 0; font-size: 20px; }
.launch-copy p { max-width: 850px; margin: 0; color: #475569; line-height: 1.65; }
.launch-state { display: flex; align-items: center; gap: 10px; margin-top: 4px; color: #64748b; font-size: 12px; }
.runtime-readiness { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 3px; }
.runtime-readiness span { padding: 5px 8px; border: 1px solid #bae6fd; border-radius: 8px; color: #64748b; background: rgba(255,255,255,.72); font-size: 10px; }
.runtime-readiness b { color: #0f766e; font-weight: 800; }
.launch-actions { display: flex; flex-shrink: 0; gap: 10px; }
.readiness-card { display: grid; gap: 15px; padding: 20px 22px; border: 1px solid #dbe5ef; border-radius: 18px; background: #fff; }
.readiness-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.readiness-heading small { color: #0891b2; font-size: 10px; font-weight: 800; letter-spacing: .1em; }
.readiness-heading h3 { margin: 5px 0; }
.readiness-heading p { margin: 0; color: #64748b; font-size: 12px; }
.readiness-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; min-height: 80px; }
.readiness-grid article { display: grid; align-content: start; gap: 7px; min-height: 112px; padding: 13px; border: 1px solid #e2e8f0; border-radius: 13px; background: #f8fafc; }
.readiness-grid article > div { display: flex; align-items: center; justify-content: space-between; gap: 7px; }
.readiness-grid small { overflow: hidden; color: #64748b; font-size: 9px; font-weight: 800; text-overflow: ellipsis; }
.readiness-grid strong { font-size: 13px; }
.readiness-grid p { margin: 0; color: #64748b; font-size: 11px; line-height: 1.55; }
.readiness-grid .readiness-ready { border-color: #bbf7d0; background: #f0fdf4; }
.readiness-grid .readiness-blocked { border-color: #fecaca; background: #fef2f2; }
.readiness-grid .readiness-deferred { border-color: #fde68a; background: #fffbeb; }
.enterprise-card { display: grid; gap: 15px; padding: 20px 22px; border: 1px solid #a7f3d0; border-radius: 18px; background: linear-gradient(120deg, #f0fdf4, #f0fdfa); }
.enterprise-actions { display: flex; align-items: center; gap: 10px; }
.enterprise-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.enterprise-summary span { display: grid; gap: 6px; min-width: 0; padding: 13px; border: 1px solid #bbf7d0; border-radius: 12px; background: rgba(255,255,255,.78); }
.enterprise-summary small { color: #059669; font-size: 9px; font-weight: 900; letter-spacing: .1em; }
.enterprise-summary b { overflow: hidden; color: #14532d; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.baseline-coverage { display: flex; flex-wrap: wrap; gap: 8px; }
.business-baseline-strip { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 15px 16px; border: 1px solid #99f6e4; border-radius: 13px; background: rgba(240, 253, 250, .92); }
.business-baseline-strip > div { display: grid; gap: 4px; }.business-baseline-strip small { color: #0f766e; font-size: 9px; font-weight: 900; letter-spacing: .1em; }.business-baseline-strip strong { color: #134e4a; }.business-baseline-strip span { color: #64748b; font-size: 11px; }
.evidence-package-strip { border-color: #93c5fd; background: rgba(239, 246, 255, .92); }.evidence-package-strip small { color: #1d4ed8; }.evidence-package-strip strong { color: #1e3a8a; }
.evidence-import-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; margin-top: 16px; }.evidence-upload-panel, .evidence-item-panel, .evidence-package-builder { padding: 16px; border: 1px solid #bfdbfe; border-radius: 14px; background: #f8fbff; }.evidence-upload-panel h4, .evidence-item-panel h4, .evidence-package-builder h4 { margin: 0 0 13px; color: #1e3a8a; }.evidence-item-list { display: grid; gap: 8px; max-height: 360px; overflow: auto; }.evidence-item-list article { display: grid; gap: 5px; padding: 11px; border: 1px solid #dbeafe; border-radius: 10px; background: #fff; }.evidence-item-list article > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.evidence-item-list span, .evidence-item-list small { overflow: hidden; color: #64748b; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.evidence-package-builder { display: grid; gap: 13px; margin-top: 16px; }
.enterprise-form { display: grid; gap: 2px; margin-top: 14px; }
.baseline-validation { display: grid; gap: 13px; margin-top: 16px; padding: 16px; border: 1px solid #a7f3d0; border-radius: 14px; background: #f0fdf4; }
.baseline-validation-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.baseline-validation-heading span { color: #64748b; font-size: 11px; }
.baseline-slot-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.baseline-slot-grid article { display: grid; gap: 4px; padding: 10px; border: 1px solid #bbf7d0; border-radius: 10px; background: rgba(255,255,255,.78); }
.baseline-slot-grid article.slot-review_required { border-color: #fde68a; background: #fffbeb; }
.baseline-slot-grid small { color: #059669; font-size: 9px; font-weight: 900; }
.baseline-slot-grid strong { font-size: 11px; }
.baseline-slot-grid span { color: #64748b; font-size: 10px; }
.baseline-gate-readiness { display: flex; flex-wrap: wrap; gap: 7px; }
.assessment-status-card { display: grid; gap: 20px; padding: 22px; border: 1px solid #dbe5ef; border-radius: 18px; background: #fff; }
.lot-picker { display: grid; gap: 13px; padding: 18px; border-radius: 15px; background: #f8fafc; }
.lot-picker p { margin: 4px 0 0; color: #64748b; font-size: 12px; }
.lot-picker .el-radio-group { display: flex; flex-wrap: wrap; gap: 8px; }
.report-sheet { display: grid; gap: 20px; padding: 8px 2px; }
.report-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 20px; color: #ecfeff; border-radius: 16px; background: linear-gradient(125deg, #0f3b46, #0e7490); }
.report-heading small { color: #67e8f9; font-weight: 800; letter-spacing: .08em; }
.report-heading h3 { margin: 6px 0; }
.report-heading p { margin: 0; color: #cffafe; line-height: 1.65; }
.release-card { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 17px 19px; border: 1px solid #c4b5fd; border-radius: 14px; background: linear-gradient(120deg, #f5f3ff, #faf5ff); }
.release-card > div:first-child { display: grid; gap: 5px; }.release-card small { color: #7c3aed; font-size: 9px; font-weight: 900; letter-spacing: .09em; }.release-card strong { color: #4c1d95; }.release-card p { margin: 0; color: #6b7280; font-size: 12px; line-height: 1.55; }
.release-actions { display: flex; align-items: center; gap: 9px; flex-shrink: 0; }
.release-review-section { display: grid; gap: 13px; margin-top: 18px; }.release-review-section h4 { margin: 0; color: #312e81; }
.release-gate-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }.release-gate-grid article { display: grid; gap: 9px; padding: 14px; border: 1px solid #ddd6fe; border-radius: 12px; background: #fafafa; }.release-gate-grid article > div { display: flex; align-items: center; gap: 8px; }.release-gate-grid small { color: #7c3aed; font-weight: 900; }.release-gate-grid p { min-height: 34px; margin: 0; color: #64748b; font-size: 11px; line-height: 1.5; }
.release-quality-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }.release-quality-grid article { display: grid; gap: 8px; padding: 13px; border: 1px solid #e2e8f0; border-radius: 12px; background: #f8fafc; }.release-quality-grid strong { font-size: 12px; }
.release-validation { display: grid; gap: 12px; margin-top: 18px; padding: 15px; border: 1px solid #a7f3d0; border-radius: 13px; background: #f0fdf4; }.release-validation > div:first-child { display: flex; justify-content: space-between; gap: 12px; }.release-validation span { color: #64748b; font-size: 11px; }
.business-slot-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; max-height: 55vh; overflow: auto; }
.business-slot-grid article { display: grid; gap: 8px; padding: 13px; border: 1px solid #99f6e4; border-radius: 12px; background: #f8fffe; }.business-slot-grid article > div { display: flex; align-items: center; gap: 8px; }.business-slot-grid small { color: #0f766e; font-weight: 900; }.business-slot-grid strong { flex: 1; font-size: 12px; }
.fact-verification-toolbar { display: flex; align-items: center; gap: 10px; margin: 16px 0; }.comparison-fact-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 11px; max-height: 58vh; overflow: auto; }.comparison-fact-grid article { display: grid; gap: 8px; padding: 14px; border: 1px solid #bae6fd; border-radius: 13px; background: #f8fcff; }.comparison-fact-grid article header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.comparison-fact-grid article header > div { display: grid; gap: 3px; min-width: 0; }.comparison-fact-grid small { color: #0284c7; font-size: 9px; font-weight: 900; }.comparison-fact-grid strong { font-size: 13px; }.comparison-fact-grid code { overflow: hidden; color: #64748b; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.decision-revalidation { display: grid; grid-template-columns: auto auto auto minmax(0, 1fr); align-items: center; gap: 9px; padding: 11px; border: 1px solid #99f6e4; border-radius: 10px; background: #f0fdfa; }.decision-revalidation small { color: #64748b; text-align: right; }
.release-check-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }.release-check-grid article { display: grid; gap: 4px; padding: 9px; border: 1px solid #bbf7d0; border-radius: 9px; background: #fff; }.release-check-grid article.release-blocked { border-color: #fecaca; background: #fef2f2; }.release-check-grid article.release-warning { border-color: #fde68a; background: #fffbeb; }.release-check-grid small { overflow: hidden; color: #64748b; font-size: 8px; font-weight: 800; text-overflow: ellipsis; }.release-check-grid strong { font-size: 10px; }
.gate-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 9px; }
.gate-grid article { display: grid; gap: 5px; min-height: 94px; padding: 13px; border-radius: 13px; background: #f8fafc; border: 1px solid #e2e8f0; }
.gate-grid small { color: #64748b; font-weight: 900; }.gate-grid span { color: #64748b; font-size: 10px; line-height: 1.4; }
.gate-grid .gate-pass { border-color: #bbf7d0; background: #f0fdf4; }.gate-grid .gate-fail { border-color: #fecaca; background: #fef2f2; }.gate-grid .gate-unknown { border-color: #fde68a; background: #fffbeb; }
.gate-grid .gate-not_applicable { border-color: #cbd5e1; background: #f8fafc; }
.claim-list { display: grid; gap: 12px; }
.claim-list > article { display: grid; gap: 10px; padding: 17px; border: 1px solid #e2e8f0; border-radius: 14px; }
.claim-list article > div { display: flex; align-items: flex-start; gap: 10px; }.claim-list p { margin: 0; color: #64748b; font-size: 12px; }
.claim-list details { padding: 10px 12px; border-radius: 10px; background: #f8fafc; }.claim-list summary { cursor: pointer; color: #0e7490; font-size: 11px; font-weight: 700; }
.claim-list blockquote { margin: 10px 0 0; padding-left: 12px; border-left: 3px solid #67e8f9; color: #475569; font-size: 12px; line-height: 1.65; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }.native-file-input { width: 100%; padding: 13px; border: 1px dashed #94a3b8; border-radius: 12px; background: #f8fafc; }
.selected-files { display: grid; gap: 5px; width: 100%; margin-top: 9px; color: #475569; font-size: 12px; }
.control-strip { display: flex; gap: 18px; align-items: end; justify-content: space-between; padding: 16px 18px; background: #fff; border: 1px solid #dbe5ef; border-radius: 16px; }
.run-picker { display: grid; gap: 7px; flex: 1; max-width: 760px; }
.run-picker label { color: #475569; font-size: 12px; font-weight: 700; }
.run-option { display: flex; align-items: center; justify-content: space-between; gap: 18px; width: 100%; }
.run-option span { color: #64748b; font-size: 12px; }
.control-actions { display: flex; align-items: center; gap: 12px; }
.brain-rail { display: grid; grid-template-columns: repeat(15, auto); align-items: center; gap: 7px; padding: 14px; overflow-x: auto; background: #f8fafc; border: 1px solid #dbe5ef; border-radius: 16px; }
.brain-rail b { color: #94a3b8; font-weight: 400; }
.brain-step { display: flex; align-items: center; gap: 8px; min-width: 118px; padding: 9px; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; opacity: .58; }
.brain-step.active { border-color: #67e8f9; box-shadow: inset 0 0 0 1px #cffafe; opacity: 1; }
.brain-step > span { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 8px; color: #0e7490; background: #ecfeff; font-size: 10px; font-weight: 800; }
.brain-step div { display: grid; gap: 2px; }
.brain-step small { color: #94a3b8; font-size: 9px; letter-spacing: .08em; }
.brain-step strong { font-size: 11px; white-space: nowrap; }
.metric-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }
.metric-grid article { display: grid; gap: 5px; padding: 16px; border: 1px solid #dbe5ef; border-radius: 15px; background: #fff; }
.metric-grid small { color: #64748b; font-size: 10px; font-weight: 800; letter-spacing: .1em; }
.metric-grid strong { overflow: hidden; color: #0f766e; font-size: 24px; text-overflow: ellipsis; }
.metric-grid span { color: #94a3b8; font-size: 11px; }
.trace-card { padding: 20px; border: 1px solid #dbe5ef; border-radius: 18px; background: #fff; box-shadow: 0 12px 34px rgba(15, 23, 42, .06); }
.card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 6px; }
.card-heading small { color: #0891b2; font-size: 10px; font-weight: 800; letter-spacing: .1em; }
.card-heading h3 { margin: 5px 0; }
.card-heading p { margin: 0; color: #64748b; font-size: 12px; }
.checkpoint-board { display: grid; gap: 12px; padding: 8px 0; }
.checkpoint-board article { display: flex; align-items: center; gap: 14px; padding: 15px; border: 1px solid #e2e8f0; border-radius: 14px; background: #f8fafc; }
.checkpoint-index { display: grid; place-items: center; min-width: 42px; height: 42px; border-radius: 13px; color: #fff; background: #d97706; font-weight: 800; }
.checkpoint-board article > div:last-child { display: grid; gap: 4px; }
.checkpoint-board small { color: #64748b; }
.checkpoint-board p { margin: 0; color: #94a3b8; font-size: 11px; }
.timeline-list { display: grid; max-height: 640px; overflow: auto; }
.timeline-list article { display: grid; grid-template-columns: 170px 12px minmax(0, 1fr) auto; align-items: center; gap: 13px; padding: 12px 6px; border-bottom: 1px solid #eef2f7; }
.timeline-list time { color: #64748b; font-size: 11px; }
.timeline-list i { width: 9px; height: 9px; border-radius: 50%; background: #f59e0b; }
.timeline-list i.success { background: #22c55e; }.timeline-list i.danger { background: #ef4444; }
.timeline-list p { margin: 3px 0 0; color: #94a3b8; font-size: 11px; }
.boundary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; padding: 8px 0; }
.boundary-grid article { display: grid; align-content: start; gap: 9px; min-height: 120px; padding: 18px; border-radius: 15px; background: #f8fafc; border: 1px solid #e2e8f0; }
.boundary-grid small { color: #0891b2; font-weight: 800; }.boundary-grid strong { line-height: 1.7; font-size: 13px; }
@media (max-width: 1100px) { .metric-grid { grid-template-columns: repeat(3, 1fr); }.readiness-grid { grid-template-columns: repeat(2, 1fr); }.boundary-grid { grid-template-columns: 1fr; }.gate-grid { grid-template-columns: repeat(4, 1fr); }.baseline-slot-grid { grid-template-columns: repeat(3, 1fr); }.release-check-grid { grid-template-columns: repeat(2, 1fr); }.decision-revalidation { grid-template-columns: auto auto auto; }.decision-revalidation small { grid-column: 1 / -1; text-align: left; } }
@media (max-width: 760px) { .lab-hero, .control-strip, .mvp1-launch-card, .readiness-heading, .baseline-validation-heading, .release-card, .business-baseline-strip, .fact-verification-toolbar { flex-direction: column; align-items: stretch; }.hero-status { justify-items: start; }.launch-actions, .launch-state, .enterprise-actions { flex-wrap: wrap; }.readiness-grid, .enterprise-summary, .baseline-slot-grid, .business-slot-grid, .comparison-fact-grid, .release-gate-grid, .release-quality-grid, .release-check-grid, .decision-revalidation, .evidence-import-grid { grid-template-columns: 1fr; }.form-grid { grid-template-columns: 1fr; }.gate-grid { grid-template-columns: repeat(2, 1fr); }.metric-grid { grid-template-columns: repeat(2, 1fr); }.control-actions { flex-wrap: wrap; }.timeline-list article { grid-template-columns: 1fr 12px minmax(0, 2fr); }.timeline-list article .el-tag { display: none; } }
</style>
