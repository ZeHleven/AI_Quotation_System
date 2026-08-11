<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <span class="brand-mark" aria-hidden="true">QS</span>
        <div>
          <p class="eyebrow">旗胜智能装饰</p>
          <h1>旗胜智价</h1>
        </div>
      </div>
    </header>

    <main class="login-layout">
      <section class="login-hero">
        <span class="login-hero-mark">旗胜智价</span>
        <h2>内部报价与项目运营中台</h2>
        <p>清爽、可信、可追溯的企业工作台。</p>
        <div class="login-hero-meta">
          <span>AI 报价</span>
          <span>企业定额主库</span>
          <span>项目进度</span>
        </div>
      </section>
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

    <el-dialog
      v-model="state.passwordDialogVisible"
      title="首次登录，请修改密码"
      width="460px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <el-alert
        class="password-change-alert"
        title="完成密码修改前，账号只能访问账户信息和改密接口。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" :model="passwordForm" @submit.prevent="changePassword">
        <el-form-item label="当前密码">
          <el-input
            v-model="passwordForm.oldPassword"
            type="password"
            autocomplete="current-password"
            show-password
          />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input
            v-model="passwordForm.newPassword"
            type="password"
            autocomplete="new-password"
            placeholder="不少于 6 位"
            show-password
          />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input
            v-model="passwordForm.confirmPassword"
            type="password"
            autocomplete="new-password"
            show-password
            @keyup.enter="changePassword"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button
          type="primary"
          :loading="state.changingPassword"
          @click="changePassword"
        >
          修改密码并继续
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive } from 'vue'
import axios from 'axios'
import { Lock, User } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import {
  cleanupSharedAuthStorage,
  clearAuth,
  getToken,
  setToken,
  setUserInfo,
} from './authStorage'

const api = axios.create({ baseURL: '/api/v1' })
const loginForm = reactive({ username: '', password: '' })
const passwordForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })
const state = reactive({ loading: false, changingPassword: false, passwordDialogVisible: false })

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) clearAuth()
    return Promise.reject(error)
  },
)

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

function safeRedirectPath(value) {
  if (typeof value !== 'string') return ''
  const candidate = value.trim()
  if (!candidate.startsWith('/') || candidate.startsWith('//') || candidate.includes('\\')) return ''

  let decoded = candidate
  try {
    decoded = decodeURIComponent(candidate)
  } catch (_error) {
    return ''
  }
  if (!decoded.startsWith('/') || decoded.startsWith('//') || decoded.includes('\\')) return ''

  try {
    const target = new URL(candidate, window.location.origin)
    if (target.origin !== window.location.origin) return ''
    if (['/login', '/app.html'].includes(target.pathname)) return ''
    return `${target.pathname}${target.search}${target.hash}`
  } catch (_error) {
    return ''
  }
}

const ROLE_DEFAULT_HOME_RULES = [
  { roles: ['system_admin', 'admin'], path: '/admin/dashboard' },
  { roles: ['quote_operator', 'viewer'], path: '/admin/dashboard' },
  { roles: ['manager', 'project_manager'], path: '/admin/projects' },
  { roles: ['project_member'], path: '/admin/project-tasks/my' },
  { roles: ['project_viewer'], path: '/admin/projects' },
  { roles: ['cost_viewer', 'cost_editor', 'cost_approver', 'cost_exporter'], path: '/admin/cost-db' },
  { roles: ['enterprise_profile_viewer', 'enterprise_profile_editor', 'enterprise_profile_approver'], path: '/admin/enterprise-profile' },
  { roles: ['staff', 'quote_user'], path: '/quote/new' },
]

function roleDefaultHomePath(user) {
  const userRoles = Array.isArray(user?.roles) ? user.roles : []
  return ROLE_DEFAULT_HOME_RULES.find((rule) => rule.roles.some((role) => userRoles.includes(role)))?.path || ''
}

function canUsePostLoginPath(user, path) {
  const availablePaths = new Set(
    (user?.available_modules || [])
      .filter((item) => item.status === 'available')
      .map((item) => item.path),
  )
  const pathname = new URL(path, window.location.origin).pathname
  if (pathname === '/quote/new') return availablePaths.has('/index.html')
  if (availablePaths.has(pathname)) return true
  if (/^\/admin\/budget-projects\/\d+$/.test(pathname)) {
    return availablePaths.has('/admin/budget-projects')
  }
  if (pathname === '/admin/project-tasks/my' || /^\/admin\/projects\/\d+$/.test(pathname)) {
    return availablePaths.has('/admin/projects')
  }
  return false
}

function landingPath(user) {
  const redirect = safeRedirectPath(new URLSearchParams(window.location.search).get('redirect'))
  if (redirect && canUsePostLoginPath(user, redirect)) return redirect
  const serverDefault = safeRedirectPath(user?.default_home_path)
  if (serverDefault && canUsePostLoginPath(user, serverDefault)) return serverDefault
  const roleDefault = roleDefaultHomePath(user)
  if (roleDefault && canUsePostLoginPath(user, roleDefault)) return roleDefault
  const firstModule = user.available_modules?.find((item) => item.status === 'available')
  return firstModule?.path || '/no-access'
}

async function loadMe() {
  const response = await api.get('/auth/me')
  const user = responseData(response)
  setUserInfo({
    username: user.username,
    role: user.role,
    roles: Array.isArray(user.roles) ? user.roles : [],
  })
  return user
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
    setToken(data.access_token)
    if (data.must_change_password) {
      passwordForm.oldPassword = loginForm.password
      loginForm.password = ''
      state.passwordDialogVisible = true
      return
    }
    const me = await loadMe()
    window.location.replace(landingPath(me))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '登录失败'))
  } finally {
    state.loading = false
  }
}

async function changePassword() {
  if (!passwordForm.oldPassword) {
    ElMessage.warning('请输入当前密码')
    return
  }
  if (passwordForm.newPassword.length < 6) {
    ElMessage.warning('新密码不能少于 6 位')
    return
  }
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }

  state.changingPassword = true
  try {
    const response = await api.post('/auth/change_password', {
      old_password: passwordForm.oldPassword,
      new_password: passwordForm.newPassword,
    })
    const data = responseData(response)
    setToken(data.access_token)
    passwordForm.oldPassword = ''
    passwordForm.newPassword = ''
    passwordForm.confirmPassword = ''
    state.passwordDialogVisible = false
    const me = await loadMe()
    ElMessage.success('密码修改成功')
    window.location.replace(landingPath(me))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '密码修改失败'))
  } finally {
    state.changingPassword = false
  }
}

onMounted(async () => {
  cleanupSharedAuthStorage()
  if (!getToken()) return
  state.loading = true
  try {
    const me = await loadMe()
    if (me.must_change_password) {
      state.passwordDialogVisible = true
      return
    }
    window.location.replace(landingPath(me))
  } catch (error) {
    if ([401, 403].includes(error.response?.status)) {
      clearAuth()
    } else {
      ElMessage.warning('暂时无法验证已有登录状态，请稍后重试')
    }
  } finally {
    state.loading = false
  }
})
</script>
