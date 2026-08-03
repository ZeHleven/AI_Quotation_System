<template>
  <div class="bid-intake-executive-workbench">
    <section class="project-hero">
      <div>
        <p class="hero-kicker">总经办项目研判</p>
        <h3>{{ project?.project_name || '当前投标项目' }}</h3>
        <div class="project-meta">
          <span>甲方：{{ project?.tenderer_name || '待识别' }}</span>
          <span>地点：{{ project?.project_location || '待确认' }}</span>
          <span>截止：{{ formatDate(project?.tender_deadline_at, true) }}</span>
        </div>
      </div>
      <div class="hero-actions">
        <el-tag :type="stateMeta.type" effect="light" round>
          {{ stateMeta.label }}
        </el-tag>
        <el-button plain :loading="loading" @click="refresh">刷新</el-button>
        <el-button v-if="assessments.length" plain @click="historyDrawerVisible = true">
          历史研判
        </el-button>
      </div>
    </section>

    <el-alert
      v-if="readinessBlockerText && workflowState === 'intake'"
      type="warning"
      show-icon
      :closable="false"
      :title="readinessBlockerText"
    />

    <el-alert
      v-if="runFailureText"
      type="error"
      show-icon
      :closable="false"
      :title="runFailureText"
    >
      <template #default>
        <el-button
          v-if="canRetryRun"
          size="small"
          type="danger"
          plain
          :loading="retrying"
          @click="retryRun"
        >
          从断点重试
        </el-button>
      </template>
    </el-alert>

    <section v-if="workflowState === 'intake'" class="state-panel intake-state">
      <div class="state-copy">
        <span class="state-number">01</span>
        <div>
          <p class="section-kicker">尚未研判</p>
          <h3>把现有项目资料交给 Agent</h3>
          <p>
            系统会自动识别资料类型，核对项目条件、风险和缺失信息，
            最后给出是否值得参与报价的建议。
          </p>
        </div>
      </div>
      <div class="readiness-summary">
        <div>
          <strong>{{ readyDocumentCount }}</strong>
          <span>份资料已就绪</span>
        </div>
        <div>
          <strong>{{ readiness?.policy?.configured ? '已装载' : '未装载' }}</strong>
          <span>总经办立项标准</span>
        </div>
        <el-button
          type="primary"
          size="large"
          :loading="creating"
          :disabled="!readiness?.ready_to_start || activeEvidenceJobs.length > 0"
          @click="createAssessment"
        >
          开始项目研判
        </el-button>
      </div>
    </section>

    <section v-else-if="workflowState === 'running'" class="state-panel running-state">
      <div class="running-heading">
        <div>
          <p class="section-kicker">正在研判</p>
          <h3>Agent 正在核对项目是否值得参与</h3>
          <p>{{ currentActivity.summary }}</p>
        </div>
        <div class="running-actions">
          <span class="live-indicator"><i></i>动态更新中</span>
          <el-button type="primary" plain @click="traceDrawerVisible = true">
            查看详细研判过程
          </el-button>
          <el-button
            v-if="canCancelRun"
            type="danger"
            plain
            :loading="cancelling"
            @click="cancelRun"
          >
            终止研判
          </el-button>
        </div>
      </div>

      <div class="business-progress">
        <div
          v-for="(stage, index) in businessStages"
          :key="stage.key"
          :class="[
            'business-stage',
            {
              completed: index < currentStageIndex,
              current: index === currentStageIndex,
            },
          ]"
        >
          <div class="stage-marker">
            <span>{{ index < currentStageIndex ? '✓' : index + 1 }}</span>
          </div>
          <div>
            <strong>{{ stage.label }}</strong>
            <small>{{ stage.description }}</small>
          </div>
        </div>
      </div>

      <div class="activity-card">
        <div class="activity-icon">AI</div>
        <div>
          <span>当前处理</span>
          <strong>{{ currentActivity.title }}</strong>
          <small>最近更新：{{ formatDate(currentActivity.updatedAt) }}</small>
        </div>
      </div>

      <el-alert
        v-if="isProgressStalled"
        type="warning"
        show-icon
        :closable="false"
        title="超过 90 秒没有收到新的运行事件"
        description="任务可能仍在等待模型或资料服务返回。可以先刷新状态；如果随后进入失败状态，系统会提供断点重试。"
      />
    </section>

    <section v-else-if="workflowState === 'cancelled'" class="state-panel cancelled-state">
      <div class="cancelled-symbol">■</div>
      <div class="cancelled-copy">
        <p class="section-kicker">研判已终止</p>
        <h3>本次运行已经停止</h3>
        <p>已产生的工具调用和运行轨迹继续保留，不会生成或覆盖项目研判结论。</p>
        <small>终止时间：{{ formatDate(activeRun?.finished_at || activeRun?.updated_at) }}</small>
      </div>
      <div class="cancelled-actions">
        <el-button plain @click="traceDrawerVisible = true">查看已保留的运行过程</el-button>
        <el-button
          type="primary"
          :loading="creating"
          :disabled="!readiness?.ready_to_start"
          @click="createAssessment"
        >
          重新发起研判
        </el-button>
      </div>
    </section>

    <template v-else>
      <el-alert
        v-if="workflowState === 'incomplete'"
        class="incomplete-run-alert"
        type="warning"
        show-icon
        :closable="false"
        :title="incompleteAlert.title"
        :description="incompleteAlert.description"
      />
      <section class="decision-hero" :class="recommendationTone">
        <div class="decision-main">
          <p class="section-kicker">
            {{
              workflowState === 'supplement'
                ? '补充资料后复判'
                : workflowState === 'incomplete'
                  ? '研判未完整完成'
                  : '研判完成'
            }}
          </p>
          <span class="decision-caption">Agent 建议</span>
          <h3>{{ recommendationLabel(recommendation) }}</h3>
          <p>{{ assessmentDraft?.project_summary || '研判报告已经生成，请结合下方依据进行人工确认。' }}</p>
          <div class="decision-metrics">
            <span>判断信心 {{ confidenceText }}</span>
            <span>证据核验 {{ gateLabel(selected?.gate_status) }}</span>
            <span>资料版本 v{{ selected?.manifest_version || '-' }}</span>
          </div>
        </div>
        <div class="decision-actions">
          <el-button plain @click="traceDrawerVisible = true">查看详细研判过程</el-button>
          <el-button
            v-if="workflowState === 'incomplete'"
            type="primary"
            :loading="creating"
            :disabled="!readiness?.ready_to_start || activeEvidenceJobs.length > 0"
            @click="createAssessment"
          >
            使用已有资料重新研判
          </el-button>
          <el-button
            v-if="canUploadSupplement"
            type="warning"
            plain
            @click="openSupplementUpload"
          >
            上传补充资料
          </el-button>
        </div>
      </section>

      <section class="result-grid">
        <article class="result-card reasons-card">
          <div class="card-heading">
            <div>
              <p class="section-kicker">判断依据</p>
              <h4>为什么这样建议</h4>
            </div>
            <el-tag effect="plain">{{ keyFindings.length }} 项</el-tag>
          </div>
          <div v-if="keyFindings.length" class="finding-list">
            <div v-for="item in keyFindings" :key="item.claim_id || item.title" class="finding-item">
              <i :class="severityClass(item.severity)"></i>
              <div>
                <strong>{{ item.title }}</strong>
                <p>{{ item.conclusion }}</p>
              </div>
            </div>
          </div>
          <el-empty v-else :image-size="52" description="暂无可展示的关键判断依据" />
        </article>

        <article class="result-card missing-card">
          <div class="card-heading">
            <div>
              <p class="section-kicker">下一步资料</p>
              <h4>还需要向甲方索取什么</h4>
            </div>
            <el-button
              v-if="missingMaterials.length"
              type="primary"
              link
              @click="copyMissingMaterialRequest"
            >
              复制索取清单
            </el-button>
          </div>
          <div v-if="missingMaterials.length" class="missing-list">
            <div
              v-for="(item, index) in missingMaterials"
              :key="`${item.document_type}-${index}`"
              class="missing-item"
            >
              <span class="missing-index">{{ index + 1 }}</span>
              <div>
                <strong>{{ item.document_type }}</strong>
                <p>{{ item.reason }}</p>
              </div>
              <el-tag :type="item.blocks_decision ? 'danger' : 'warning'" effect="plain" size="small">
                {{ item.blocks_decision ? '影响决策' : '建议补充' }}
              </el-tag>
            </div>
          </div>
          <div v-else-if="missingListUnavailable" class="missing-list-unavailable">
            <span>!</span>
            <div>
              <strong>缺失资料清单尚未形成</strong>
              <p>本次研判没有形成可验证的缺失清单，请先查看运行异常或由总经办人工核对。</p>
            </div>
          </div>
          <div v-else class="all-materials-ready">
            <span>✓</span>
            <div>
              <strong>当前没有必须补充的资料</strong>
              <p>仍请总经办结合项目实际情况完成最终确认。</p>
            </div>
          </div>
        </article>
      </section>

      <section v-if="risks.length || unresolvedQuestions.length" class="result-card risk-card">
        <div class="card-heading">
          <div>
            <p class="section-kicker">风险与待确认项</p>
            <h4>人工决策前请重点关注</h4>
          </div>
        </div>
        <div class="risk-layout">
          <div v-if="risks.length" class="risk-list">
            <div v-for="item in risks" :key="item.claim_id || item.title" class="risk-item">
              <el-tag :type="severityTag(item.severity)" effect="plain" size="small">
                {{ severityLabel(item.severity) }}
              </el-tag>
              <div>
                <strong>{{ item.title }}</strong>
                <p>{{ item.conclusion }}</p>
              </div>
            </div>
          </div>
          <div v-if="unresolvedQuestions.length" class="question-list">
            <strong>仍需人工确认</strong>
            <ol>
              <li v-for="question in unresolvedQuestions" :key="question">{{ question }}</li>
            </ol>
          </div>
        </div>
      </section>
    </template>

    <section
      v-if="workflowState === 'intake' || isSupplementMode"
      ref="evidencePanelRef"
      class="evidence-panel"
    >
      <div class="card-heading">
        <div>
          <p class="section-kicker">
            {{ isSupplementMode ? '补充项目资料' : '项目资料' }}
          </p>
          <h4>
            {{ isSupplementMode ? '上传向甲方补取的资料' : '上传招标相关资料' }}
          </h4>
          <p>
            系统会根据文件名和内容自动识别资料类型，不需要人工选择分类。
          </p>
        </div>
        <el-tag :type="readyDocumentCount ? 'success' : 'warning'" effect="plain">
          {{ readyDocumentCount }} 份可用
        </el-tag>
      </div>

      <div class="upload-layout">
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
          <div class="upload-copy">
            <strong>拖入资料，或点击选择文件</strong>
            <span>支持 PDF、DOCX、XLSX、XLSM、TXT、MD；单次最多 10 份</span>
          </div>
          <template #tip>
            <div class="upload-tip">扫描版 PDF 请先完成 OCR；旧版 .doc / .xls 请另存为新版格式。</div>
          </template>
        </el-upload>
        <div class="upload-actions">
          <strong>已选择 {{ evidenceUploadFiles.length }} 份</strong>
          <small>重复文件会自动复用原任务，不会重复入库。</small>
          <el-button
            type="primary"
            :loading="evidenceUploading"
            :disabled="!evidenceUploadFiles.length"
            @click="uploadEvidenceFiles"
          >
            上传并解析
          </el-button>
          <el-button
            v-if="isSupplementMode"
            type="success"
            :loading="creating"
            :disabled="!canStartSupplementReview"
            @click="createAssessment"
          >
            使用补充资料重新研判
          </el-button>
          <small v-if="isSupplementMode && !hasNewManifest">
            上传完成后，系统会生成新的资料版本，届时即可重新研判。
          </small>
        </div>
      </div>

      <el-alert
        v-if="activeEvidenceJobs.length"
        type="info"
        show-icon
        :closable="false"
        :title="`正在处理 ${activeEvidenceJobs.length} 份资料，完成后会自动更新。`"
      />

      <div v-if="evidenceJobs.length" class="parse-jobs">
        <div class="parse-heading">
          <strong>最近资料处理进度</strong>
          <el-button
            size="small"
            plain
            :loading="evidenceJobsLoading"
            @click="refreshEvidenceProgress"
          >
            刷新
          </el-button>
        </div>
        <div
          v-for="job in evidenceJobs.slice(0, 6)"
          :key="job.job_uuid"
          class="parse-job"
        >
          <div class="parse-file">
            <strong>{{ job.original_filename }}</strong>
            <small>{{ evidenceJobStageLabel(job.stage) }}</small>
          </div>
          <el-tag :type="evidenceJobStatusType(job.status)" effect="plain" size="small">
            {{ evidenceJobStatusLabel(job.status) }}
          </el-tag>
          <span v-if="job.error_message" class="parse-error">{{ evidenceJobErrorText(job) }}</span>
          <el-button
            v-if="job.status === 'retryable'"
            link
            type="primary"
            :loading="retryingEvidenceJobUuid === job.job_uuid"
            @click="retryEvidenceParseJob(job)"
          >
            重试
          </el-button>
        </div>
      </div>
    </section>

    <el-drawer
      v-model="traceDrawerVisible"
      title="Agent 详细研判过程"
      size="94%"
      class="trace-drawer"
    >
      <div class="trace-drawer-copy">
        <strong>这里展示 Agent 如何规划、调用工具、读取结果并继续研判。</strong>
        <span>展示的是可审计的执行摘要，不展示模型私有思维链。</span>
      </div>
      <BidIntakeRunGraph :run="activeRun" />
    </el-drawer>

    <el-drawer v-model="historyDrawerVisible" title="历史研判记录" size="480px">
      <div class="history-list">
        <button
          v-for="item in assessments"
          :key="item.assessment_uuid"
          type="button"
          :class="{ active: selected?.assessment_uuid === item.assessment_uuid }"
          @click="selectHistory(item)"
        >
          <div>
            <strong>{{ recommendationLabel(item.recommendation) }}</strong>
            <small>资料版本 v{{ item.manifest_version }} · {{ formatDate(item.created_at) }}</small>
          </div>
          <el-tag :type="statusTag(item.status)" effect="plain" size="small">
            {{ statusLabel(item.status) }}
          </el-tag>
        </button>
      </div>
    </el-drawer>

  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import BidIntakeRunGraph from './BidIntakeRunGraph.vue'
import {
  bidIntakeApi,
  bidIntakeData,
  bidIntakeErrorMessage,
} from './bidIntakeApi'

const props = defineProps({
  projectUuid: { type: String, required: true },
  project: { type: Object, default: null },
  active: { type: Boolean, default: false },
})

const DEFAULT_ANALYSIS_GOAL = '判断该招标项目是否值得进入报价立项，并指出仍需向甲方索取的资料。'
const evidenceAccept = '.pdf,.docx,.xlsx,.xlsm,.txt,.md'
const evidenceAllowedExtensions = new Set(
  evidenceAccept.split(',').map((item) => item.slice(1)),
)
const liveRunStatuses = new Set(['queued', 'running', 'resume_queued'])
const retryableRunStatuses = new Set(['failed', 'blocked_stale_manifest'])
const businessStages = [
  { key: 'prepare', label: '整理资料', description: '确认资料版本与可用范围' },
  { key: 'research', label: '核对条件与风险', description: '读取证据并调用检索工具' },
  { key: 'synthesis', label: '形成研判意见', description: '汇总项目事实与风险' },
  { key: 'policy', label: '对照立项标准', description: '应用总经办判断标准' },
  { key: 'gate', label: '核验证据完整性', description: '检查结论是否有证据支撑' },
  { key: 'human', label: '等待人工确认', description: '由总经办作出最终决定' },
]

const loading = ref(false)
const detailLoading = ref(false)
const creating = ref(false)
const retrying = ref(false)
const cancelling = ref(false)
const readiness = ref(null)
const assessments = ref([])
const selected = ref(null)
const evidenceUploadRef = ref(null)
const evidencePanelRef = ref(null)
const evidenceUploadFiles = ref([])
const evidenceUploading = ref(false)
const supplementUploadOpen = ref(false)
const evidenceJobsLoading = ref(false)
const retryingEvidenceJobUuid = ref('')
const evidenceJobs = ref([])
const traceDrawerVisible = ref(false)
const historyDrawerVisible = ref(false)
const nowTick = ref(Date.now())
const evidenceJobStatuses = new Map()
let runPollTimer = null
let evidencePollTimer = null
let clockTimer = null

const activeRun = computed(() => selected.value?.runs?.[0] || null)
const assessmentDraft = computed(() => selected.value?.assessment || null)
const recommendation = computed(() => (
  selected.value?.policy_evaluation?.decision
  || selected.value?.recommendation
  || assessmentDraft.value?.recommendation
  || null
))
const keyFindings = computed(() => assessmentDraft.value?.key_findings || [])
const risks = computed(() => assessmentDraft.value?.risks || [])
const missingMaterials = computed(() => assessmentDraft.value?.missing_materials || [])
const unresolvedQuestions = computed(() => assessmentDraft.value?.unresolved_questions || [])
const missingListUnavailable = computed(() => (
  missingMaterials.value.length === 0
  && (
    ['need_supplement', 'manual_review'].includes(recommendation.value)
    || (selected.value?.gate_status && selected.value.gate_status !== 'passed')
  )
))
const readyDocumentCount = computed(() => readiness.value?.evidence?.ready_document_count || 0)
const activeEvidenceJobs = computed(() => evidenceJobs.value.filter(
  (item) => ['queued', 'running', 'retryable'].includes(item.status),
))
const latestDecision = computed(() => {
  const decisions = activeRun.value?.decisions || []
  return decisions[decisions.length - 1] || null
})
const isIncompleteAssessment = computed(() => {
  const terminationReason = assessmentDraft.value?.termination_reason
  if (terminationReason && terminationReason !== 'analysis_complete') return true
  return (selected.value?.gate_result?.issues || []).some(
    (item) => item?.code === 'AGENT_TERMINATED_EARLY',
  )
})
const isSupplementMode = computed(() => (
  workflowState.value === 'supplement' || supplementUploadOpen.value
))
const incompleteAlert = computed(() => {
  const terminationReason = assessmentDraft.value?.termination_reason
  if (terminationReason === 'model_invocation_failed') {
    const modelError = (activeRun.value?.state_summary?.errors || []).find(
      (item) => item?.code === 'MODEL_INVOCATION_FAILED',
    )
    const paymentRequired = /402|payment required|余额|额度/i.test(
      modelError?.message || '',
    )
    return {
      title: paymentRequired
        ? '模型服务额度异常，本次尚未开始研判'
        : '模型服务调用失败，本次尚未开始研判',
      description: paymentRequired
        ? '该历史运行在读取招标资料前被主模型的付费或额度状态阻断。系统现已支持备用模型自动接管，请重新发起研判。'
        : '该历史运行在读取招标资料前调用模型失败，并非资料不足。请查看详细过程；模型服务恢复后重新发起研判。',
    }
  }
  return {
    title: '本次研判未完整完成',
    description: 'Agent已保留并汇总预算范围内取得的证据，但仍有内容未完成核验。请查看详细过程，并结合缺失资料和待确认项人工复核。',
  }
})
const workflowState = computed(() => {
  if (liveRunStatuses.has(activeRun.value?.status)) return 'running'
  if (activeRun.value?.status === 'cancelled') return 'cancelled'
  if (
    selected.value?.status === 'waiting_supplement'
    || latestDecision.value?.action === 'supplement_requested'
  ) return 'supplement'
  if (assessmentDraft.value && isIncompleteAssessment.value) return 'incomplete'
  if (assessmentDraft.value) return 'completed'
  return 'intake'
})
const stateMeta = computed(() => ({
  intake: { label: '尚未研判', type: 'info' },
  running: { label: '正在研判', type: 'primary' },
  cancelled: { label: '研判已终止', type: 'info' },
  incomplete: { label: '研判未完整完成', type: 'warning' },
  completed: { label: '研判完成', type: 'success' },
  supplement: { label: '补充资料后复判', type: 'warning' },
}[workflowState.value]))
const latestRunEvent = computed(() => {
  const events = activeRun.value?.events || []
  return events[events.length - 1] || null
})
const latestTraceEvent = computed(() => {
  const events = activeRun.value?.events || []
  return [...events].reverse().find(
    (event) => event?.payload?.trace_schema_version === 'bid-intake-agent-trace/v1',
  ) || latestRunEvent.value
})
const currentActivity = computed(() => {
  const event = latestTraceEvent.value
  return {
    title: event?.payload?.title || event?.message || statusLabel(activeRun.value?.status),
    summary: event?.payload?.summary || event?.message || '等待 Agent 返回新的运行进展。',
    updatedAt: event?.created_at || activeRun.value?.updated_at,
  }
})
const currentStageIndex = computed(() => {
  if (activeRun.value?.status === 'waiting_human') return 5
  const event = latestTraceEvent.value
  const signal = [
    event?.event_type,
    event?.phase,
    event?.payload?.kind,
    event?.payload?.node_name,
    event?.payload?.title,
  ].filter(Boolean).join(' ').toLowerCase()
  if (/human|人工|review/.test(signal)) return 5
  if (/gate|validate|证据门|核验/.test(signal)) return 4
  if (/policy|标准|策略/.test(signal)) return 3
  if (/synth|draft|形成|汇总/.test(signal)) return 2
  if (/react|tool|observation|search|read|llm|检索|工具/.test(signal)) return 1
  return 0
})
const isProgressStalled = computed(() => {
  if (workflowState.value !== 'running') return false
  const timestamp = new Date(
    latestRunEvent.value?.created_at || activeRun.value?.updated_at || 0,
  ).getTime()
  return Number.isFinite(timestamp) && timestamp > 0 && nowTick.value - timestamp > 90000
})
const canUploadSupplement = computed(() => (
  activeRun.value?.status === 'waiting_human'
))
const canRetryRun = computed(() => retryableRunStatuses.has(activeRun.value?.status))
const canCancelRun = computed(() => (
  ['queued', 'running', 'resume_queued'].includes(activeRun.value?.status)
))
const confidenceText = computed(() => {
  const value = Number(assessmentDraft.value?.confidence)
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : '-'
})
const recommendationTone = computed(() => {
  if (recommendation.value === 'recommend_no_quote') return 'negative'
  if (['need_supplement', 'manual_review', 'conditional_quote'].includes(recommendation.value)) {
    return 'caution'
  }
  return 'positive'
})
const readinessBlockerText = computed(() => (
  (readiness.value?.blockers || []).map(blockerLabel).join('；')
))
const hasNewManifest = computed(() => {
  const current = Number(readiness.value?.evidence?.manifest_version || 0)
  const previous = Number(selected.value?.manifest_version || 0)
  return current > previous
})
const canStartSupplementReview = computed(() => (
  readiness.value?.ready_to_start
  && hasNewManifest.value
  && activeEvidenceJobs.value.length === 0
))
const runFailureText = computed(() => {
  if (!canRetryRun.value) return ''
  if (activeRun.value?.status === 'blocked_stale_manifest') {
    return '项目资料版本已经变化，这次研判已停止。请基于最新资料重新发起研判。'
  }
  return activeRun.value?.error_message || '研判运行失败，可以从最近断点重试。'
})
watch(
  () => [props.projectUuid, props.active],
  async ([projectUuid, active]) => {
    stopPolling()
    stopEvidencePolling()
    evidenceJobStatuses.clear()
    evidenceJobs.value = []
    evidenceUploadFiles.value = []
    supplementUploadOpen.value = false
    evidenceUploadRef.value?.clearFiles?.()
    selected.value = null
    assessments.value = []
    if (projectUuid && active) await refresh()
  },
  { immediate: true },
)

watch(
  () => workflowState.value === 'running' && props.active,
  (shouldPoll) => {
    stopPolling()
    if (shouldPoll) runPollTimer = window.setInterval(refreshSelected, 1200)
  },
)

watch(
  () => activeEvidenceJobs.value.length > 0 && props.active,
  (shouldPoll) => {
    stopEvidencePolling()
    if (shouldPoll) {
      evidencePollTimer = window.setInterval(() => refreshEvidenceProgress(true), 2500)
    }
  },
)

clockTimer = window.setInterval(() => {
  nowTick.value = Date.now()
}, 5000)

onBeforeUnmount(() => {
  stopPolling()
  stopEvidencePolling()
  if (clockTimer) window.clearInterval(clockTimer)
})

async function refresh() {
  if (!props.projectUuid || !props.active) return
  loading.value = true
  try {
    await Promise.all([loadReadiness(), loadEvidenceJobs(true)])
    if (!readiness.value?.runtime_enabled) {
      assessments.value = []
      selected.value = null
      return
    }
    const response = await bidIntakeApi.list(props.projectUuid, { limit: 50 })
    assessments.value = bidIntakeData(response) || []
    const preferredUuid = selected.value?.assessment_uuid || assessments.value[0]?.assessment_uuid
    const preferred = assessments.value.find((item) => item.assessment_uuid === preferredUuid)
    if (preferred) await openAssessment(preferred)
    else selected.value = null
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '研判工作台加载失败'))
  } finally {
    loading.value = false
  }
}

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
      ElMessage.success(`${newlyCompleted.length} 份资料解析完成，资料版本已更新`)
    }
  } catch (error) {
    if (!silent) ElMessage.error(bidIntakeErrorMessage(error, '资料进度加载失败'))
    throw error
  } finally {
    if (!silent) evidenceJobsLoading.value = false
  }
}

async function openAssessment(item) {
  if (!item?.assessment_uuid) return
  detailLoading.value = true
  try {
    const response = await bidIntakeApi.detail(props.projectUuid, item.assessment_uuid)
    selected.value = bidIntakeData(response)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '研判详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

async function selectHistory(item) {
  await openAssessment(item)
  historyDrawerVisible.value = false
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

async function createAssessment() {
  const isReassessment = isSupplementMode.value
  creating.value = true
  try {
    const response = await bidIntakeApi.create(props.projectUuid, {
      analysis_goal: DEFAULT_ANALYSIS_GOAL,
      max_attempts: 3,
    })
    const created = bidIntakeData(response)
    supplementUploadOpen.value = false
    ElMessage.success(isReassessment ? '复判任务已创建' : '研判任务已创建')
    await refresh()
    if (created?.assessment) await openAssessment(created.assessment)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '创建研判任务失败'))
  } finally {
    creating.value = false
  }
}

async function openSupplementUpload() {
  supplementUploadOpen.value = true
  await nextTick()
  evidencePanelRef.value?.scrollIntoView?.({
    behavior: 'smooth',
    block: 'start',
  })
  const fileInput = evidenceUploadRef.value?.$el?.querySelector?.(
    'input[type="file"]',
  )
  if (fileInput) fileInput.click()
}

function handleEvidenceFileChange(uploadFile) {
  const raw = uploadFile?.raw
  const extension = String(uploadFile?.name || '').split('.').pop()?.toLowerCase()
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

function removeEvidenceUploadFile(uploadFile) {
  evidenceUploadFiles.value = evidenceUploadFiles.value.filter(
    (item) => item.uid !== uploadFile?.uid,
  )
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
    if (!evidenceUploadFiles.value.length) evidenceUploadRef.value?.clearFiles?.()
    await Promise.all([loadEvidenceJobs(true), loadReadiness()])
    if (createdCount || reusedCount) {
      const parts = []
      if (createdCount) parts.push(`${createdCount} 份已进入解析队列`)
      if (reusedCount) parts.push(`${reusedCount} 份复用已有任务`)
      ElMessage.success(parts.join('，'))
    }
    failures.forEach((item) => ElMessage.error(`${item.filename}：${item.message}`))
  } finally {
    evidenceUploading.value = false
  }
}

async function refreshEvidenceProgress(silent = false) {
  if (!props.projectUuid || !props.active || evidenceJobsLoading.value) return
  if (!silent) evidenceJobsLoading.value = true
  try {
    await Promise.all([loadEvidenceJobs(true), loadReadiness()])
  } catch (error) {
    if (!silent) ElMessage.error(bidIntakeErrorMessage(error, '资料进度刷新失败'))
  } finally {
    if (!silent) evidenceJobsLoading.value = false
  }
}

async function retryEvidenceParseJob(job) {
  if (!job?.job_uuid) return
  retryingEvidenceJobUuid.value = job.job_uuid
  try {
    await bidIntakeApi.retryEvidenceParseJob(props.projectUuid, job.job_uuid)
    ElMessage.success('资料已重新进入解析队列')
    await refreshEvidenceProgress(true)
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '资料解析重试失败'))
  } finally {
    retryingEvidenceJobUuid.value = ''
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
    ElMessage.success('任务已从最近断点重新进入队列')
    await refreshSelected()
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '重试失败'))
  } finally {
    retrying.value = false
  }
}

async function cancelRun() {
  if (!selected.value || !activeRun.value || !canCancelRun.value) return
  try {
    await ElMessageBox.confirm(
      '终止后，本次研判不会继续生成结果；已产生的运行轨迹会保留。是否确认终止？',
      '终止本次研判',
      {
        confirmButtonText: '确认终止',
        cancelButtonText: '继续研判',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  cancelling.value = true
  try {
    await bidIntakeApi.cancel(
      props.projectUuid,
      selected.value.assessment_uuid,
      activeRun.value.run_uuid,
    )
    ElMessage.success('本次研判已终止，运行轨迹已保留')
    await refreshSelected()
  } catch (error) {
    ElMessage.error(bidIntakeErrorMessage(error, '终止研判失败'))
  } finally {
    cancelling.value = false
  }
}

async function copyMissingMaterialRequest() {
  const lines = missingMaterials.value.map((item, index) => (
    `${index + 1}. ${item.document_type}：${item.reason}`
  ))
  const text = [
    `关于“${props.project?.project_name || '本项目'}”的补充资料请求：`,
    ...lines,
    '',
    '烦请协助提供以上资料，以便我方进一步完成项目评估，谢谢。',
  ].join('\n')
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制向甲方索取的资料清单')
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    textarea.remove()
    ElMessage.success('已复制向甲方索取的资料清单')
  }
}

function stopPolling() {
  if (runPollTimer) window.clearInterval(runPollTimer)
  runPollTimer = null
}

function stopEvidencePolling() {
  if (evidencePollTimer) window.clearInterval(evidencePollTimer)
  evidencePollTimer = null
}

function recommendationLabel(value) {
  return {
    recommend_quote: '建议参与报价',
    recommend_no_quote: '建议不参与报价',
    conditional_quote: '满足条件后可参与',
    need_supplement: '补充资料后再判断',
    manual_review: '需要总经办重点复核',
  }[value] || '尚未形成结论'
}

function gateLabel(value) {
  return {
    passed: '已通过',
    repair_required: '需要修复',
    supplement_required: '需要补资料',
    manual_review_required: '需要人工复核',
    research_restart_required: '需要重新研判',
  }[value] || '待核验'
}

function blockerLabel(code) {
  return {
    RUNTIME_DISABLED: '研判服务尚未启用',
    ACTIVE_MANIFEST_REQUIRED: '请先上传并完成项目资料解析',
    READY_EVIDENCE_REQUIRED: '当前没有解析完成的项目资料',
    WORKER_OFFLINE: 'Agent 服务暂时离线',
    MCP_NOT_CONFIGURED: '项目资料读取服务尚未配置',
    MODEL_NOT_CONFIGURED: '研判模型尚未配置',
    WORKER_CAPABILITY_MISMATCH: 'Agent 运行能力配置不一致',
    POLICY_NOT_CONFIGURED: '总经办立项标准尚未装载',
    WORKER_POLICY_NOT_CONFIGURED: 'Agent 尚未装载总经办立项标准',
    POLICY_VERSION_MISMATCH: 'Agent 使用的立项标准版本已过期',
  }[code] || code
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
    dispatch_failed: '进入队列失败',
    fetching_source: '读取并校验原件',
    parsing: '提取文件内容',
    evidence_ingestion: '生成证据清单',
    completed: '资料已就绪',
    failed: '处理失败',
  }[value] || value || '-'
}

function evidenceJobErrorText(job) {
  return {
    SOURCE_STORAGE_UNAVAILABLE: '原件存储暂不可用',
    UNSUPPORTED_OR_UNREADABLE_FILE: '文件格式不支持、已损坏或无法提取文字',
    SOURCE_HASH_MISMATCH: '原件完整性校验失败',
    EVIDENCE_INGEST_REJECTED: '资料入库未通过校验',
    PARSE_PIPELINE_ERROR: '解析处理失败',
    ATTEMPTS_EXHAUSTED: '解析重试次数已用完',
    DISPATCH_FAILED: '任务未能进入后台队列',
  }[job?.error_code] || job?.error_message || '解析失败，请重试'
}

function severityLabel(value) {
  return { low: '低风险', medium: '中风险', high: '高风险', critical: '重大风险' }[value] || value
}

function severityTag(value) {
  return ['critical', 'high'].includes(value) ? 'danger' : value === 'medium' ? 'warning' : 'info'
}

function severityClass(value) {
  return ['critical', 'high'].includes(value) ? 'high' : value === 'medium' ? 'medium' : 'low'
}

function statusLabel(value) {
  return {
    queued: '排队中',
    running: '研判中',
    waiting_human: '待总经办确认',
    resume_queued: '正在保存决策',
    completed: '已完成',
    approved: '已确认参与',
    approved_with_conditions: '有条件参与',
    rejected: '已确认不参与',
    waiting_supplement: '待补资料',
    research_requested: '待重新研判',
    cancelled: '已终止',
    failed: '运行失败',
    blocked_stale_manifest: '资料版本已变化',
  }[value] || value || '-'
}

function statusTag(value) {
  if (['approved', 'approved_with_conditions', 'completed'].includes(value)) return 'success'
  if (['failed', 'rejected', 'blocked_stale_manifest'].includes(value)) return 'danger'
  if (value === 'cancelled') return 'info'
  if (['waiting_human', 'waiting_supplement', 'research_requested'].includes(value)) return 'warning'
  return 'info'
}

function formatDate(value, compact = false) {
  if (!value) return '待确认'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', compact
    ? { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
    : { hour12: false })
}
</script>

<style scoped>
.bid-intake-executive-workbench {
  display: grid;
  gap: 18px;
  color: #172033;
}

.project-hero,
.state-panel,
.decision-hero,
.result-card,
.evidence-panel {
  border: 1px solid #e2e8f2;
  border-radius: 20px;
  background: rgb(255 255 255 / 92%);
  box-shadow: 0 14px 35px rgb(40 69 111 / 7%);
}

.project-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 26px;
  background:
    radial-gradient(circle at 88% 20%, rgb(77 125 255 / 10%), transparent 34%),
    linear-gradient(135deg, #fff, #f8faff);
}

.project-hero h3,
.state-panel h3,
.decision-hero h3 {
  margin: 4px 0 8px;
  color: #121a2b;
  font-size: 24px;
  letter-spacing: -0.02em;
}

.hero-kicker,
.section-kicker {
  margin: 0;
  color: #4d74bd;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.project-meta,
.hero-actions,
.decision-metrics {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 18px;
}

.project-meta span,
.decision-metrics span {
  color: #6b7485;
  font-size: 13px;
}

.state-panel {
  padding: 28px;
}

.cancelled-state {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 18px;
  align-items: center;
  border-color: #d9dfe9;
  background: linear-gradient(135deg, #fff, #f6f7fa);
}

.cancelled-symbol {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  border-radius: 14px;
  background: #e9edf3;
  color: #6d7788;
  font-size: 15px;
}

.cancelled-copy {
  display: grid;
  gap: 5px;
}

.cancelled-copy h3 {
  margin: 0;
}

.cancelled-copy p:not(.section-kicker),
.cancelled-copy small {
  margin: 0;
  color: #717b8e;
  line-height: 1.55;
}

.cancelled-actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.intake-state {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 0.7fr);
  gap: 30px;
  align-items: center;
}

.state-copy {
  display: flex;
  gap: 18px;
  align-items: flex-start;
}

.state-number {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  place-items: center;
  border-radius: 14px;
  background: #edf3ff;
  color: #3465b5;
  font-weight: 800;
}

.state-copy p:last-child,
.running-heading p,
.decision-main > p,
.card-heading p,
.finding-item p,
.missing-item p,
.risk-item p,
.all-materials-ready p {
  margin: 0;
  color: #657086;
  line-height: 1.65;
}

.readiness-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 18px;
  border-radius: 16px;
  background: #f6f8fc;
}

.readiness-summary > div {
  display: grid;
  gap: 4px;
}

.readiness-summary strong {
  font-size: 18px;
}

.readiness-summary span {
  color: #788196;
  font-size: 12px;
}

.readiness-summary .el-button {
  grid-column: 1 / -1;
}

.running-state {
  display: grid;
  gap: 24px;
}

.running-heading {
  display: flex;
  justify-content: space-between;
  gap: 24px;
}

.running-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.live-indicator {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #227256;
  font-size: 13px;
  font-weight: 600;
}

.live-indicator i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #34b27b;
  box-shadow: 0 0 0 6px rgb(52 178 123 / 12%);
  animation: live-pulse 1.6s ease-in-out infinite;
}

.business-progress {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0;
}

.business-stage {
  position: relative;
  display: grid;
  gap: 10px;
  padding-right: 12px;
}

.business-stage::after {
  position: absolute;
  top: 17px;
  right: 4px;
  left: 42px;
  height: 2px;
  background: #e4e9f1;
  content: '';
}

.business-stage:last-child::after {
  display: none;
}

.stage-marker {
  position: relative;
  z-index: 1;
}

.stage-marker span {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 2px solid #d9e0eb;
  border-radius: 50%;
  background: #fff;
  color: #9aa4b4;
  font-size: 13px;
  font-weight: 700;
}

.business-stage.completed .stage-marker span,
.business-stage.current .stage-marker span {
  border-color: #4d7fe1;
  background: #4d7fe1;
  color: #fff;
}

.business-stage.completed::after {
  background: #4d7fe1;
}

.business-stage > div:last-child {
  display: grid;
  gap: 3px;
}

.business-stage strong {
  font-size: 13px;
}

.business-stage small {
  color: #8a94a7;
  font-size: 11px;
  line-height: 1.45;
}

.activity-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
  border: 1px solid #dce6f8;
  border-radius: 15px;
  background: #f7faff;
}

.activity-icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  border-radius: 13px;
  background: linear-gradient(135deg, #315fc2, #6b8fec);
  color: #fff;
  font-size: 12px;
  font-weight: 800;
}

.activity-card > div:last-child {
  display: grid;
  gap: 3px;
}

.activity-card span,
.activity-card small {
  color: #768197;
  font-size: 12px;
}

.incomplete-run-alert {
  border-radius: 16px;
}

.decision-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 30px;
  padding: 28px;
  overflow: hidden;
}

.decision-hero.positive {
  border-color: #cfeade;
  background: linear-gradient(135deg, #fbfffd, #f0fbf6);
}

.decision-hero.caution {
  border-color: #f0ddba;
  background: linear-gradient(135deg, #fffefa, #fff8ea);
}

.decision-hero.negative {
  border-color: #f0d0d5;
  background: linear-gradient(135deg, #fffdfd, #fff3f4);
}

.decision-caption {
  display: block;
  margin-top: 18px;
  color: #7b8598;
  font-size: 12px;
}

.decision-main h3 {
  margin-top: 2px;
  font-size: 30px;
}

.decision-metrics {
  margin-top: 16px;
}

.decision-actions {
  display: flex;
  min-width: 180px;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.result-card,
.evidence-panel {
  padding: 22px;
}

.card-heading,
.parse-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.card-heading h4 {
  margin: 4px 0;
  font-size: 18px;
}

.finding-list,
.missing-list,
.risk-list,
.parse-jobs,
.question-list {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.finding-item,
.missing-item,
.risk-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 13px;
  border-radius: 13px;
  background: #f8f9fc;
}

.finding-item i {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  margin-top: 7px;
  border-radius: 50%;
  background: #4ca97d;
}

.finding-item i.medium {
  background: #df9d32;
}

.finding-item i.high {
  background: #db5a67;
}

.finding-item > div,
.missing-item > div,
.risk-item > div {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 4px;
}

.finding-item strong,
.missing-item strong,
.risk-item strong {
  font-size: 14px;
}

.finding-item p,
.missing-item p,
.risk-item p {
  font-size: 13px;
}

.missing-index {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  place-items: center;
  border-radius: 9px;
  background: #fff2dc;
  color: #a76713;
  font-size: 12px;
  font-weight: 800;
}

.all-materials-ready {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
  padding: 18px;
  border-radius: 14px;
  background: #f1faf6;
}

.missing-list-unavailable {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
  padding: 18px;
  border-radius: 14px;
  background: #fff7e8;
}

.missing-list-unavailable > span {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: 50%;
  background: #d99229;
  color: #fff;
  font-weight: 800;
}

.missing-list-unavailable > div {
  display: grid;
  gap: 4px;
}

.missing-list-unavailable p {
  margin: 0;
  color: #74634a;
  font-size: 13px;
  line-height: 1.55;
}

.all-materials-ready > span {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 50%;
  background: #3cac78;
  color: #fff;
}

.risk-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.6fr);
  gap: 18px;
}

.question-list {
  align-content: start;
  padding: 16px;
  border-radius: 14px;
  background: #fff8eb;
}

.question-list ol {
  display: grid;
  gap: 9px;
  margin: 0;
  padding-left: 22px;
  color: #65563f;
  font-size: 13px;
  line-height: 1.55;
}

.evidence-panel {
  display: grid;
  gap: 16px;
}

.upload-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 270px;
  gap: 14px;
}

.evidence-uploader :deep(.el-upload),
.evidence-uploader :deep(.el-upload-dragger) {
  width: 100%;
}

.evidence-uploader :deep(.el-upload-dragger) {
  min-height: 132px;
  padding: 32px 20px;
  border-color: #bdcde7;
  border-radius: 15px;
  background: #f9fbff;
}

.upload-copy,
.upload-actions,
.parse-file {
  display: grid;
  gap: 6px;
}

.upload-copy span,
.upload-actions small,
.upload-tip,
.parse-file small {
  color: #768197;
  font-size: 12px;
}

.upload-actions {
  align-content: center;
  padding: 16px;
  border-radius: 14px;
  background: #f6f8fc;
}

.parse-job {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(140px, 0.5fr) auto;
  gap: 12px;
  align-items: center;
  padding: 11px 13px;
  border: 1px solid #e7ebf2;
  border-radius: 12px;
}

.parse-error {
  color: #c84d5d;
  font-size: 12px;
}

.trace-drawer-copy {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 14px;
  padding: 12px 16px;
  border-radius: 12px;
  background: #f5f8fd;
  color: #5c687c;
  font-size: 13px;
}

.history-list {
  display: grid;
  gap: 10px;
}

.history-list button {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid #e3e8f0;
  border-radius: 13px;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.history-list button.active {
  border-color: #7ea0e4;
  background: #f4f7ff;
}

.history-list button > div {
  display: grid;
  gap: 5px;
}

.history-list small {
  color: #7b8597;
}

@keyframes live-pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(0.8);
    opacity: 0.65;
  }
}

@media (max-width: 1180px) {
  .business-progress {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 18px 0;
  }

  .business-stage:nth-child(3)::after {
    display: none;
  }
}

@media (max-width: 900px) {
  .project-hero,
  .running-heading,
  .decision-hero,
  .cancelled-state {
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .intake-state,
  .result-grid,
  .risk-layout,
  .upload-layout {
    grid-template-columns: 1fr;
  }

  .decision-actions {
    min-width: 0;
  }

  .business-progress {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .business-stage {
    grid-template-columns: 40px 1fr;
  }

  .business-stage::after {
    display: none;
  }

  .parse-job {
    grid-template-columns: 1fr auto;
  }
}
</style>
