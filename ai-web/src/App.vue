<template>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">旗胜智能装饰</p>
        <h1>AI 平台中台</h1>
      </div>
      <div class="topbar-actions" v-if="session.user">
        <el-tag effect="plain">{{ session.user.username }}</el-tag>
        <el-button :icon="SwitchButton" plain @click="logout">退出</el-button>
      </div>
    </header>

    <main v-if="routeName === 'login'" class="login-layout">
      <section class="login-panel">
        <div class="panel-heading">
          <el-icon><Lock /></el-icon>
          <span>账号登录</span>
        </div>
        <el-form label-position="top" :model="loginForm" @submit.prevent="login">
          <el-form-item label="用户名">
            <el-input
              v-model="loginForm.username"
              :prefix-icon="User"
              autocomplete="username"
              placeholder="请输入用户名"
              @keyup.enter="login"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="loginForm.password"
              :prefix-icon="Lock"
              type="password"
              autocomplete="current-password"
              placeholder="请输入密码"
              show-password
              @keyup.enter="login"
            />
          </el-form-item>
          <el-button
            class="primary-action"
            type="primary"
            :loading="state.loading"
            @click="login"
          >
            登录
          </el-button>
        </el-form>
      </section>
    </main>

    <main v-else class="workspace">
      <aside class="sidebar">
        <button
          v-if="canAccessPermissions"
          :class="['nav-item', { active: routeName === 'permissions' }]"
          type="button"
          @click="navigate('/admin/permissions')"
        >
          <el-icon><Tickets /></el-icon>
          <span>权限管理</span>
        </button>
        <button
          v-if="canViewDashboard"
          :class="['nav-item', { active: routeName === 'dashboard' }]"
          type="button"
          @click="navigate('/admin/dashboard')"
        >
          <el-icon><DataAnalysis /></el-icon>
          <span>报价速度</span>
        </button>
        <button v-if="canOpenLegacyQuote" class="nav-item" type="button" @click="openLegacy('/index.html')">
          <el-icon><Document /></el-icon>
          <span>旧报价工作台</span>
        </button>
        <button v-if="canOpenLegacyAdmin" class="nav-item" type="button" @click="openLegacy('/admin.html')">
          <el-icon><Setting /></el-icon>
          <span>旧知识库管理</span>
        </button>
      </aside>

      <section class="content-panel">
        <div v-if="state.loading" class="center-state">
          <el-icon class="spin"><Refresh /></el-icon>
          <span>加载中</span>
        </div>

        <div v-else-if="state.error === 'unauthorized'" class="center-state">
          <h2>未登录</h2>
          <el-button type="primary" @click="navigate('/login')">返回登录</el-button>
        </div>

        <div v-else-if="state.error === 'forbidden'" class="center-state">
          <h2>403</h2>
          <p>无权限访问</p>
        </div>

        <div v-else-if="state.error === 'feature_disabled'" class="center-state">
          <el-icon><DataAnalysis /></el-icon>
          <h2>功能未开启</h2>
          <p>报价速度看板开关尚未打开。</p>
        </div>

        <template v-else-if="routeName === 'dashboard'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">Phase 1</p>
              <h2>报价速度看板</h2>
            </div>
            <div class="heading-actions">
              <el-radio-group v-model="dashboardRange" size="small" @change="loadQuoteDashboard">
                <el-radio-button
                  v-for="option in rangeOptions"
                  :key="option.value"
                  :label="option.value"
                >
                  {{ option.label }}
                </el-radio-button>
              </el-radio-group>
              <el-button :icon="Refresh" plain @click="loadQuoteDashboard">刷新</el-button>
            </div>
          </div>

          <el-alert
            v-if="dashboard?.empty_state"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="暂无数据，数据从当前环境验证后开始统计"
          />
          <el-alert
            v-else-if="dashboard?.low_sample_warning"
            class="dashboard-alert"
            type="warning"
            show-icon
            :closable="false"
            title="样本量较少，仅供参考"
          />

          <div class="metric-grid">
            <div class="metric-card">
              <span>报价任务</span>
              <strong>{{ dashboard?.sample_count ?? 0 }}</strong>
              <small>已完成 {{ dashboard?.completed_count ?? 0 }} · 已确认 {{ dashboard?.confirmed_count ?? 0 }}</small>
            </div>
            <div class="metric-card">
              <span>AI 生成耗时</span>
              <strong>{{ formatMs(dashboard?.ai_duration_avg_ms) }}</strong>
              <small>来自成功任务 duration_ms</small>
            </div>
            <div class="metric-card">
              <span>人工确认耗时</span>
              <strong>{{ formatMs(dashboard?.manual_confirm_duration_avg_ms) }}</strong>
              <small>AI 完成到确认推送</small>
            </div>
            <div class="metric-card">
              <span>总交付耗时</span>
              <strong>{{ formatMs(dashboard?.total_delivery_duration_avg_ms) }}</strong>
              <small>任务创建到确认推送</small>
            </div>
            <div class="metric-card">
              <span>AI 修改率</span>
              <strong>{{ formatRate(dashboard?.modified_rate) }}</strong>
              <small>{{ dashboard?.modified_count ?? 0 }} / {{ dashboard?.feedback_sample_count ?? 0 }} 条反馈</small>
            </div>
          </div>

          <div class="dashboard-split">
            <section class="dashboard-section">
              <div class="section-title">
                <el-icon><TrendCharts /></el-icon>
                <span>每日趋势</span>
              </div>
              <el-table
                :data="visibleDailyTrends"
                row-key="date"
                class="users-table"
                empty-text="暂无趋势数据"
              >
                <el-table-column prop="date" label="日期" min-width="120" />
                <el-table-column prop="sample_count" label="任务" width="90" />
                <el-table-column prop="confirmed_count" label="确认" width="90" />
                <el-table-column label="AI 耗时" min-width="120">
                  <template #default="{ row }">{{ formatMs(row.ai_duration_avg_ms) }}</template>
                </el-table-column>
                <el-table-column label="总交付" min-width="120">
                  <template #default="{ row }">{{ formatMs(row.total_delivery_duration_avg_ms) }}</template>
                </el-table-column>
                <el-table-column label="修改率" width="100">
                  <template #default="{ row }">{{ formatRate(row.modified_rate) }}</template>
                </el-table-column>
              </el-table>
            </section>

            <section class="dashboard-section">
              <div class="section-title">
                <el-icon><Histogram /></el-icon>
                <span>状态分布</span>
              </div>
              <div class="status-list">
                <div
                  v-for="item in dashboard?.status_distribution || []"
                  :key="item.status"
                  class="status-row"
                >
                  <span>{{ statusLabel(item.status) }}</span>
                  <strong>{{ item.count }}</strong>
                </div>
                <el-empty v-if="!dashboard?.status_distribution?.length" description="暂无状态数据" />
              </div>
            </section>
          </div>
        </template>

        <template v-else>
          <div class="content-heading">
            <div>
              <p class="eyebrow">Phase 0</p>
              <h2>用户角色</h2>
            </div>
            <el-button :icon="Refresh" plain @click="loadUsers">刷新</el-button>
          </div>

          <div class="role-hints">
            <div v-for="role in roleOptions" :key="role.value" class="role-hint">
              <strong>{{ role.label }}</strong>
              <span>{{ role.hint }}</span>
            </div>
          </div>

          <el-table
            :data="users"
            row-key="id"
            class="users-table"
            empty-text="暂无用户"
          >
            <el-table-column prop="username" label="用户" min-width="150" />
            <el-table-column label="角色" min-width="240">
              <template #default="{ row }">
                <div class="role-tags">
                  <el-tag
                    v-for="role in row.roles"
                    :key="role"
                    :type="roleTagType(role)"
                    effect="light"
                  >
                    {{ role }}
                  </el-tag>
                  <el-tag v-if="!row.roles?.length" type="info" effect="plain">未分配</el-tag>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="role_version" label="版本" width="90" />
            <el-table-column label="钉钉" width="90">
              <template #default="{ row }">
                <el-tag :type="row.dingtalk_bound ? 'success' : 'info'" effect="plain">
                  {{ row.dingtalk_bound ? '已绑定' : '未绑定' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="当前模块" min-width="220">
              <template #default="{ row }">
                <div class="module-list">
                  <span
                    v-for="module in row.available_modules"
                    :key="module.key"
                    :class="['module-pill', module.status]"
                  >
                    {{ module.name }}
                  </span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="260" fixed="right">
              <template #default="{ row }">
                <div class="row-actions">
                  <el-button :icon="Plus" plain @click="openGrant(row)" :disabled="!canMutateRoles">
                    授权
                  </el-button>
                  <el-button :icon="Clock" plain @click="openEvents(row)">历史</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </section>
    </main>

    <el-dialog v-model="grantDialog.visible" title="授予角色" width="420px">
      <el-form label-position="top" :model="grantDialog">
        <el-form-item label="用户">
          <el-input :model-value="grantDialog.user?.username" disabled />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="grantDialog.role" class="full-width">
            <el-option
              v-for="role in roleOptions"
              :key="role.value"
              :label="role.label"
              :value="role.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="grantDialog.note" type="textarea" :rows="3" maxlength="120" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="grantDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="grantSelectedRole">确认授权</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="eventsDrawer.visible" size="520px" title="授权历史">
      <div v-if="eventsDrawer.user" class="drawer-user">
        {{ eventsDrawer.user.username }}
      </div>
      <el-timeline>
        <el-timeline-item
          v-for="event in roleEvents"
          :key="event.id"
          :timestamp="formatDate(event.created_at)"
          placement="top"
        >
          <div class="event-row">
            <strong>{{ event.action }}</strong>
            <el-tag size="small" effect="plain">{{ event.role }}</el-tag>
          </div>
          <p>{{ event.note || '无备注' }}</p>
          <small>{{ event.ip_address || '-' }} · {{ event.trace_id || '-' }}</small>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!roleEvents.length" description="暂无历史" />

      <template v-if="eventsDrawer.user && canMutateRoles">
        <div class="revoke-panel">
          <el-select v-model="eventsDrawer.revokeRole" placeholder="选择要撤销的角色" class="full-width">
            <el-option
              v-for="role in eventsDrawer.user.roles"
              :key="role"
              :label="role"
              :value="role"
            />
          </el-select>
          <el-input
            v-model="eventsDrawer.revokeNote"
            type="textarea"
            :rows="2"
            maxlength="120"
            show-word-limit
            placeholder="撤权备注"
          />
          <el-button
            :icon="Delete"
            type="danger"
            plain
            :loading="state.submitting"
            @click="revokeSelectedRole"
          >
            撤销角色
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import {
  Clock,
  DataAnalysis,
  Delete,
  Document,
  Histogram,
  Lock,
  Plus,
  Refresh,
  Setting,
  SwitchButton,
  Tickets,
  TrendCharts,
  User,
} from '@element-plus/icons-vue'

const TOKEN_KEY = 'ai_token'
const api = axios.create({ baseURL: '/api/v1' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
    }
    return Promise.reject(error)
  },
)

const roleOptions = [
  { value: 'system_admin', label: 'system_admin', hint: '权限与系统配置' },
  { value: 'admin', label: 'admin', hint: '报价与知识库管理' },
  { value: 'staff', label: 'staff', hint: '旧报价工作台' },
  { value: 'manager', label: 'manager', hint: '执行任务上线后生效' },
  { value: 'viewer', label: 'viewer', hint: '看板开启后生效' },
]

const rangeOptions = [
  { value: 'today', label: '今日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
  { value: 'last_30_days', label: '近 30 天' },
]

const loginForm = reactive({ username: '', password: '' })
const session = reactive({ user: null })
const users = ref([])
const roleEvents = ref([])
const dashboard = ref(null)
const dashboardRange = ref('last_30_days')
const state = reactive({ loading: false, submitting: false, error: '' })
const routeName = ref(routeFromPath(window.location.pathname))

const grantDialog = reactive({
  visible: false,
  user: null,
  role: 'staff',
  note: '',
})

const eventsDrawer = reactive({
  visible: false,
  user: null,
  revokeRole: '',
  revokeNote: '',
})

const roles = computed(() => session.user?.roles || [])
const canMutateRoles = computed(() => roles.value.includes('system_admin'))
const canAccessPermissions = computed(() => roles.value.includes('system_admin') || roles.value.includes('admin'))
const canViewDashboard = computed(() => canAccessPermissions.value || roles.value.includes('viewer'))
const canOpenLegacyQuote = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canOpenLegacyAdmin = computed(() => canAccessPermissions.value)
const visibleDailyTrends = computed(() => (dashboard.value?.daily_trends || []).filter((item) => item.sample_count > 0).slice(-12))

function routeFromPath(path) {
  if (path === '/login') return 'login'
  if (path === '/admin/dashboard') return 'dashboard'
  return 'permissions'
}

function responseData(response) {
  return response.data?.data ?? response.data
}

function apiErrorMessage(error, fallback = '请求失败') {
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (error.response?.data?.message) return error.response.data.message
  return fallback
}

function navigate(path) {
  window.history.pushState({}, '', path)
  routeName.value = routeFromPath(path)
  if (path !== '/login') {
    bootstrap()
  }
}

function openLegacy(path) {
  window.location.href = path
}

function roleTagType(role) {
  if (role === 'system_admin') return 'danger'
  if (role === 'admin') return 'warning'
  if (role === 'staff') return 'success'
  if (role === 'manager') return 'primary'
  return 'info'
}

function formatDate(value) {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 19)
}

function formatMs(value) {
  if (value === null || value === undefined) return '-'
  const seconds = value / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  const minutes = seconds / 60
  if (minutes < 60) return `${minutes.toFixed(1)} 分钟`
  return `${(minutes / 60).toFixed(1)} 小时`
}

function formatRate(value) {
  if (value === null || value === undefined) return '-'
  return `${(value * 100).toFixed(1)}%`
}

function statusLabel(status) {
  const labels = {
    queued: '排队中',
    running: '处理中',
    succeeded: '已完成',
    failed: '失败',
    canceled: '已取消',
    timed_out: '已超时',
  }
  return labels[status] || status
}

function landingPath(user) {
  const redirect = new URLSearchParams(window.location.search).get('redirect')
  if (redirect?.startsWith('/')) return redirect
  if (user.roles?.includes('system_admin') || user.roles?.includes('admin')) return '/admin/permissions'
  if (user.roles?.includes('staff')) return '/index.html'
  const firstModule = user.available_modules?.find((item) => item.status === 'available')
  return firstModule?.path || '/admin/permissions'
}

async function login() {
  if (!loginForm.username || !loginForm.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  state.loading = true
  try {
    const params = new URLSearchParams()
    params.append('username', loginForm.username)
    params.append('password', loginForm.password)
    const response = await api.post('/auth/login', params)
    const data = responseData(response)
    localStorage.setItem(TOKEN_KEY, data.access_token)
    const me = await loadMe()
    window.location.href = landingPath(me)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '登录失败'))
  } finally {
    state.loading = false
  }
}

async function loadMe() {
  const response = await api.get('/auth/me')
  session.user = responseData(response)
  return session.user
}

async function loadUsers() {
  state.loading = true
  state.error = ''
  try {
    const response = await api.get('/admin/users')
    users.value = responseData(response)
  } catch (error) {
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error))
  } finally {
    state.loading = false
  }
}

async function loadQuoteDashboard() {
  state.loading = true
  state.error = ''
  try {
    const response = await api.get('/admin/dashboard/quote-speed', {
      params: { range: dashboardRange.value },
    })
    dashboard.value = responseData(response)
  } catch (error) {
    dashboard.value = null
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.data?.detail === 'FEATURE_DISABLED') state.error = 'feature_disabled'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '看板加载失败'))
  } finally {
    state.loading = false
  }
}

async function bootstrap() {
  if (routeName.value === 'login') return
  state.loading = true
  state.error = ''
  try {
    await loadMe()
    if (routeName.value === 'dashboard') {
      if (!canViewDashboard.value) {
        state.error = 'forbidden'
        return
      }
      await loadQuoteDashboard()
      return
    }
    if (!canAccessPermissions.value) {
      state.error = 'forbidden'
      return
    }
    await loadUsers()
  } catch (error) {
    state.error = error.response?.status === 403 ? 'forbidden' : 'unauthorized'
  } finally {
    state.loading = false
  }
}

function openGrant(user) {
  grantDialog.user = user
  grantDialog.role = 'staff'
  grantDialog.note = ''
  grantDialog.visible = true
}

async function grantSelectedRole() {
  if (!grantDialog.user || !grantDialog.note.trim()) {
    ElMessage.warning('请填写授权备注')
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/users/${grantDialog.user.id}/roles`, {
      role: grantDialog.role,
      note: grantDialog.note,
    })
    grantDialog.visible = false
    await loadUsers()
    ElMessage.success('已授权')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '授权失败'))
  } finally {
    state.submitting = false
  }
}

async function openEvents(user) {
  eventsDrawer.visible = true
  eventsDrawer.user = user
  eventsDrawer.revokeRole = user.roles?.[0] || ''
  eventsDrawer.revokeNote = ''
  roleEvents.value = []
  try {
    const response = await api.get(`/admin/users/${user.id}/role-events`)
    roleEvents.value = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '授权历史加载失败'))
  }
}

async function revokeSelectedRole() {
  if (!eventsDrawer.user || !eventsDrawer.revokeRole || !eventsDrawer.revokeNote.trim()) {
    ElMessage.warning('请选择角色并填写撤权备注')
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/users/${eventsDrawer.user.id}/roles/${eventsDrawer.revokeRole}/revoke`, {
      note: eventsDrawer.revokeNote,
      trace_id: crypto.randomUUID?.() || String(Date.now()),
    })
    await loadUsers()
    const refreshedUser = users.value.find((item) => item.id === eventsDrawer.user.id)
    if (refreshedUser) {
      await openEvents(refreshedUser)
    }
    ElMessage.success('已撤销')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '撤权失败'))
  } finally {
    state.submitting = false
  }
}

function logout() {
  localStorage.removeItem(TOKEN_KEY)
  session.user = null
  window.location.href = '/login'
}

window.addEventListener('popstate', () => {
  routeName.value = routeFromPath(window.location.pathname)
  bootstrap()
})

onMounted(() => {
  bootstrap()
})
</script>
