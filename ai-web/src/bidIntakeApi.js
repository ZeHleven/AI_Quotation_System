import axios from 'axios'
import { clearAuth, getToken } from './authStorage'

const client = axios.create({ baseURL: '/api/v1' })

client.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearAuth()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

function base(projectUuid) {
  return `/admin/bidding/projects/${projectUuid}/bid-intake`
}

function evidenceBase(projectUuid) {
  return `/admin/bidding/projects/${projectUuid}/evidence`
}

export const bidIntakeApi = Object.freeze({
  readiness: (projectUuid) => client.get(`${base(projectUuid)}/readiness`),
  list: (projectUuid, params = {}) => client.get(`${base(projectUuid)}/assessments`, { params }),
  create: (projectUuid, payload) => client.post(`${base(projectUuid)}/assessments`, payload),
  detail: (projectUuid, assessmentUuid) => client.get(`${base(projectUuid)}/assessments/${assessmentUuid}`),
  createEvidenceParseJob: (projectUuid, formData, config = {}) => (
    client.post(`${evidenceBase(projectUuid)}/parse-jobs`, formData, config)
  ),
  evidenceParseJobs: (projectUuid, params = {}) => (
    client.get(`${evidenceBase(projectUuid)}/parse-jobs`, { params })
  ),
  evidenceParseJob: (projectUuid, jobUuid) => (
    client.get(`${evidenceBase(projectUuid)}/parse-jobs/${jobUuid}`)
  ),
  retryEvidenceParseJob: (projectUuid, jobUuid) => (
    client.post(`${evidenceBase(projectUuid)}/parse-jobs/${jobUuid}/retry`)
  ),
  evidenceIndexStatus: (projectUuid) => (
    client.get(`${evidenceBase(projectUuid)}/index-status`)
  ),
  decide: (projectUuid, assessmentUuid, runUuid, payload) => (
    client.post(`${base(projectUuid)}/assessments/${assessmentUuid}/runs/${runUuid}/decision`, payload)
  ),
  retry: (projectUuid, assessmentUuid, runUuid) => (
    client.post(`${base(projectUuid)}/assessments/${assessmentUuid}/runs/${runUuid}/retry`)
  ),
  cancel: (projectUuid, assessmentUuid, runUuid) => (
    client.post(`${base(projectUuid)}/assessments/${assessmentUuid}/runs/${runUuid}/cancel`)
  ),
  calibrationReport: (params = {}) => (
    client.get('/admin/bidding/bid-intake/calibration/report', { params })
  ),
  calibrationCandidates: (params = {}) => (
    client.get('/admin/bidding/bid-intake/calibration/candidates', { params })
  ),
  calibrationSamples: (params = {}) => (
    client.get('/admin/bidding/bid-intake/calibration/samples', { params })
  ),
  reviewCalibrationLabel: (labelUuid, payload) => (
    client.post(`/admin/bidding/bid-intake/calibration/labels/${labelUuid}/review`, payload)
  ),
  calibrationQuality: () => (
    client.get('/admin/bidding/bid-intake/calibration/quality')
  ),
  calibrationDatasets: (params = {}) => (
    client.get('/admin/bidding/bid-intake/calibration/datasets', { params })
  ),
  freezeCalibrationDataset: (payload = {}) => (
    client.post('/admin/bidding/bid-intake/calibration/datasets', payload)
  ),
  generateCalibrationCandidate: (payload) => (
    client.post('/admin/bidding/bid-intake/calibration/candidates', payload)
  ),
  blindEvaluateCalibrationCandidate: (proposalUuid) => (
    client.post(
      `/admin/bidding/bid-intake/calibration/candidates/${proposalUuid}/blind-evaluate`,
    )
  ),
  calibrationLabel: (projectUuid, assessmentUuid) => (
    client.get(`${base(projectUuid)}/assessments/${assessmentUuid}/calibration-label`)
  ),
  saveCalibrationLabel: (projectUuid, assessmentUuid, payload) => (
    client.post(`${base(projectUuid)}/assessments/${assessmentUuid}/calibration-label`, payload)
  ),
})

export function bidIntakeData(response) {
  return response?.data?.data ?? response?.data
}

const ERROR_LABELS = Object.freeze({
  ACTIVE_EVIDENCE_MANIFEST_REQUIRED: '请先完成招标资料解析并生成有效证据清单。',
  INVALID_BID_FILE_TYPE: '资料类型不受支持，请重新选择。',
  TENDER_FILE_TOO_LARGE: '文件超过当前允许的上传大小。',
  TENDER_SOURCE_STORAGE_UNAVAILABLE: '招标原件存储暂不可用，请稍后重试。',
  TENDER_PARSE_DISPATCH_FAILED: '解析任务未能进入后台队列，请在任务列表中重试。',
  TENDER_PARSE_JOB_NOT_FOUND: '解析任务不存在或已不可访问。',
  ACTIVE_BID_POLICY_REQUIRED: '请先装载有效的总经办立项标准。',
  RUN_NOT_WAITING_FOR_HUMAN: '该运行当前不在等待人工审核状态。',
  STALE_REPORT: '研判报告已更新，请刷新后重新提交。',
  STALE_MANIFEST: '招标资料版本已变化，请重新发起研判。',
  RUN_DECISION_ALREADY_EXISTS: '该运行已经提交过人工决策。',
  DECISION_IDEMPOTENCY_CONFLICT: '同一决策编号对应了不同内容，请刷新后重试。',
  RUN_NOT_RETRYABLE: '该运行当前不能重试。',
  RUN_NOT_CANCELLABLE: '该研判已经结束，当前不能再终止。',
  RUN_ATTEMPT_BUDGET_EXHAUSTED: '运行重试次数已用完。',
  APPROVAL_BLOCKED_BY_EVIDENCE_GATE: '证据门仍有阻断项，当前不能批准。',
  APPROVAL_BLOCKED_BY_POLICY: '总经办立项规则要求补充信息或特别审批，当前不能普通批准。',
  APPROVAL_BLOCKED_PENDING_SUPPLEMENT: '资料待补充，当前不能批准。',
  STALE_CALIBRATION_LABEL_VERSION: '金标已被其他人更新，请刷新后重新提交。',
  CALIBRATION_DATASET_SPLIT_FROZEN: '该样本的数据分层已经冻结，不能在开发集与Holdout之间移动。',
  CALIBRATION_PROJECT_SPLIT_FROZEN: '同一项目的其他研判已经确定数据分层，不能跨Development与Holdout。',
  ASSESSMENT_NOT_READY_FOR_CALIBRATION: '研判尚未形成完整报告，暂时不能记录金标。',
  BOUND_MANIFEST_NOT_AVAILABLE: '研判绑定的资料快照已不可用。',
  INVALID_CALIBRATION_LABEL: '金标内容与校准规则不一致，请检查后重试。',
  CALIBRATION_LABEL_PERMISSION_DENIED: '当前账号没有维护总经办金标的权限。',
  CALIBRATION_MANAGE_PERMISSION_DENIED: '当前账号没有维护校准金标或候选标准的权限。',
  CALIBRATION_DEVELOPMENT_SAMPLE_INSUFFICIENT: 'Development金标不足20个，暂时不能生成候选标准。',
  CALIBRATION_DEVELOPMENT_CLASS_COVERAGE_INSUFFICIENT: 'Development样本类型不足：至少需要3个不报价样本和3个报价/有条件报价样本。',
  CALIBRATION_DEVELOPMENT_EVALUATION_ERRORS: 'Development样本存在回放错误，修复前不会生成候选标准。',
  NO_BETTER_CANDIDATE_FOUND: '当前样本下未找到安全性与一致率更好的阈值方案。',
  NO_VALID_CANDIDATE_SEARCH_SPACE: '当前标准没有可用的安全阈值搜索空间。',
  CALIBRATION_DATASET_INVALID: '校准数据中存在无效金标快照，请先处理数据质量问题。',
  CALIBRATION_LABEL_NOT_FOUND: '待复核金标不存在或已经被新版本替代。',
  CALIBRATION_REVIEWER_MUST_DIFFER: '金标创建人与复核人必须是不同账号。',
  INVALID_CALIBRATION_REVIEW_ACTION: '复核动作无效。',
  CALIBRATION_REVIEW_NOTE_REQUIRED: '复核意见不能为空。',
  CALIBRATION_LABEL_ALREADY_REVIEWED: '该版本金标已经完成复核，不能覆盖原复核记录。',
  CALIBRATION_DATASET_NOT_READY: '复核样本尚未达到数据集冻结质量门。',
  CALIBRATION_DATASET_NOT_FOUND: '冻结校准数据集不存在。',
  CALIBRATION_DATASET_SNAPSHOT_INVALID: '冻结校准数据集快照不可读取。',
  CALIBRATION_DATASET_SNAPSHOT_MISMATCH: '冻结校准数据集指纹校验失败。',
  CALIBRATION_CANDIDATE_NOT_FOUND: '候选标准提案不存在。',
  CALIBRATION_CANDIDATE_NOT_EVALUABLE: '该候选标准当前不能执行盲测。',
  CALIBRATION_CANDIDATE_SNAPSHOT_MISMATCH: '候选提案的数据集快照校验失败，已阻止盲测。',
  CALIBRATION_CANDIDATE_SNAPSHOT_INVALID: '候选提案的数据集快照不可读取，已阻止盲测。',
  CALIBRATION_CANDIDATE_POLICY_INVALID: '候选标准快照不可读取，已阻止盲测。',
  BID_POLICY_VERSION_NOT_FOUND: '候选立项标准版本不存在。',
})

export function bidIntakeErrorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return ERROR_LABELS[detail] || detail
  if (detail?.code && ERROR_LABELS[detail.code]) return ERROR_LABELS[detail.code]
  if (detail?.code === 'BID_INTAKE_RUNTIME_NOT_READY') {
    return '当前运行条件未就绪，请刷新后处理阻断项。'
  }
  return error?.response?.data?.message || fallback
}
