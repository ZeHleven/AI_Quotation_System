<template>
  <div class="app-container">
    <el-container>
      <el-header class="header">
        <div class="logo">🚀 内部 AI 智能体业务中台</div>
        <div class="user-info" v-if="isLoggedIn">
          <el-tag type="success" effect="dark" round>当前登录：{{ currentUser }}</el-tag>
          <el-button type="danger" size="small" plain v-on:click="logout" style="margin-left: 15px">退出</el-button>
        </div>
      </el-header>

      <el-main class="main-content">
        <div v-if="!isLoggedIn" class="auth-panel">
          <el-card class="box-card" shadow="hover">
            <el-tabs v-model="activeTab">
              <el-tab-pane label="安全登录" name="login">
                <el-form :model="authForm" label-width="70px" v-on:submit.prevent="handleLogin">
                  <el-form-item label="账号">
                    <el-input v-model="authForm.username" placeholder="请输入员工账号"></el-input>
                  </el-form-item>
                  <el-form-item label="密码">
                    <el-input v-model="authForm.password" type="password" placeholder="请输入密码" show-password></el-input>
                  </el-form-item>
                  <el-button type="primary" class="full-btn" :loading="loading" v-on:click="handleLogin">登录系统中台</el-button>
                </el-form>
              </el-tab-pane>

              <el-tab-pane label="新员工注册" name="register">
                <el-form :model="authForm" label-width="70px" v-on:submit.prevent="handleRegister">
                  <el-form-item label="账号">
                    <el-input v-model="authForm.username" placeholder="设置员工账号"></el-input>
                  </el-form-item>
                  <el-form-item label="密码">
                    <el-input v-model="authForm.password" type="password" placeholder="设置登录密码" show-password></el-input>
                  </el-form-item>
                  <el-button type="success" class="full-btn" :loading="loading" v-on:click="handleRegister">注册并领取额度</el-button>
                </el-form>
              </el-tab-pane>
            </el-tabs>
          </el-card>
        </div>

        <div v-else class="chat-panel">
          <el-card class="chat-card" shadow="never">
            <el-scrollbar class="message-list" ref="scrollbarRef">
              <div v-for="(msg, index) in messageHistory" :key="index" :class="['message-item', msg.role]">
                <div class="avatar">{{ msg.role === 'user' ? '🧑‍💻' : '🤖' }}</div>
                <div class="bubble">
                  <div v-if="msg.fileUrl" class="file-preview">
                    <el-image :src="msg.fileUrl" fit="cover" class="preview-img" :preview-src-list="[msg.fileUrl]" />
                  </div>
                  <div class="text-content">
                    {{ msg.content }}<span v-if="index === messageHistory.length - 1 && isTyping" class="cursor"></span>
                  </div>
                </div>
              </div>
            </el-scrollbar>

            <div class="input-area">
              <div v-if="previewData" class="confirm-bar">
                <span class="confirm-tip">📋 报价单已生成，确认无误后推送至钉钉</span>
                <el-button type="success" size="small" v-on:click="confirmPush">确认推送钉钉</el-button>
                <el-button size="small" plain v-on:click="previewData = null">取消</el-button>
              </div>
              <div v-if="selectedFile" class="upload-preview">
                <el-tag closable v-on:close="selectedFile = null">📄 {{ selectedFile.name }}</el-tag>
              </div>

              <div class="input-container">
                <el-upload
                  class="upload-trigger"
                  action="#"
                  :auto-upload="false"
                  :show-file-list="false"
                  :on-change="handleFileChange"
                >
                  <el-button circle><el-icon><Paperclip /></el-icon></el-button>
                </el-upload>

                <el-input
                  v-model="inputText"
                  type="textarea"
                  :rows="2"
                  placeholder="描述需求或上传图片/PDF清单..."
                  resize="none"
                  v-on:keyup.enter.exact="sendMessage"
                ></el-input>
              </div>

              <div class="action-bar">
                <span class="tip-text">支持识别手写清单、图片和 PDF 户型图</span>
                <el-button type="primary" :loading="sending" v-on:click="sendMessage">发送业务指令</el-button>
              </div>
            </div>
          </el-card>
        </div>
      </el-main>
    </el-container>
  </div>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Paperclip } from '@element-plus/icons-vue'
import axios from 'axios'

const API_BASE_URL = 'http://127.0.0.1:9000/api/v1'

const isLoggedIn = ref(false)
const currentUser = ref('')
const activeTab = ref('login')
const loading = ref(false)
const sending = ref(false)
const isTyping = ref(false)
const previewData = ref(null)

const authForm = ref({ username: '', password: '' })
const inputText = ref('')
const selectedFile = ref(null)
const messageHistory = ref([
  { role: 'assistant', content: '您好，多模态业务中台 V2.0 已连接。您可以直接输入测算指令，或者上传施工清单图片。' }
])
const scrollbarRef = ref(null)

const handleRegister = async () => {
  if (!authForm.value.username || !authForm.value.password) {
    return ElMessage.warning('账号和密码不能为空！')
  }
  loading.value = true
  try {
    const res = await axios.post(`${API_BASE_URL}/auth/register`, {
      username: authForm.value.username,
      password: authForm.value.password
    })
    ElMessage.success(res.data.message || '注册成功！')
    activeTab.value = 'login'
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '注册失败')
  } finally {
    loading.value = false
  }
}

const handleLogin = async () => {
  if (!authForm.value.username || !authForm.value.password) {
    return ElMessage.warning('账号和密码不能为空！')
  }
  loading.value = true
  try {
    const params = new URLSearchParams()
    params.append('username', authForm.value.username)
    params.append('password', authForm.value.password)

    const res = await axios.post(`${API_BASE_URL}/auth/login`, params)
    localStorage.setItem('ai_token', res.data.access_token)
    currentUser.value = authForm.value.username
    isLoggedIn.value = true
    ElMessage.success(`欢迎回来，${currentUser.value}！`)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}

const logout = () => {
  localStorage.removeItem('ai_token')
  isLoggedIn.value = false
  authForm.value.password = ''
  messageHistory.value = [{ role: 'assistant', content: '您好，多模态业务中台已连接。' }]
}

const handleFileChange = (file) => {
  selectedFile.value = file.raw
  ElMessage.info(`已选中文件: ${file.name}`)
}

const sendMessage = async () => {
  const text = inputText.value.trim()
  if (!text && !selectedFile.value) return

  const formData = new FormData()
  if (text) formData.append('message', text)
  if (selectedFile.value) formData.append('file', selectedFile.value)

  const userMsg = { role: 'user', content: text || '发起多模态文件解析请求' }
  if (selectedFile.value && selectedFile.value.type.startsWith('image/')) {
    userMsg.fileUrl = URL.createObjectURL(selectedFile.value)
  }
  messageHistory.value.push(userMsg)

  // 🚀 核心修复1：Vue3响应式陷阱。必须获取 push 后产生的 Proxy 代理对象
  messageHistory.value.push({ role: 'assistant', content: '' })
  const currentAssistantMsg = messageHistory.value[messageHistory.value.length - 1]

  inputText.value = ''
  selectedFile.value = null
  sending.value = true
  isTyping.value = true
  scrollToBottom()

  let charQueue = Array.from('⏳ 正在唤醒网关层，建立长连接...\n\n')
  let streamEnded = false

  // 自适应打字机循环
  const typeNextChar = () => {
    if (charQueue.length > 0) {
      const charsToProcess = charQueue.length > 60 ? 4 : (charQueue.length > 20 ? 2 : 1)
      for(let i = 0; i < charsToProcess; i++) {
          if(charQueue.length > 0) {
              // 修改 Proxy 对象才会触发 Vue DOM 实时更新！
              currentAssistantMsg.content += charQueue.shift()
          }
      }
      scrollToBottom()
      setTimeout(typeNextChar, 25)
    } else {
      if (streamEnded) {
        isTyping.value = false
        sending.value = false
      } else {
        setTimeout(typeNextChar, 50)
      }
    }
  }

  typeNextChar()

  try {
    const token = localStorage.getItem('ai_token')
    const response = await fetch(`${API_BASE_URL}/chat`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: formData
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      charQueue.push(...(`\n❌ [网关级拦截] ${errorData.detail || '网络连接异常'}`).split(''))
      streamEnded = true
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let done = false

    while (!done) {
      const { value, done: readerDone } = await reader.read()
      done = readerDone
      if (value) {
        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.substring(6))
              if (data.status === 'processing') {
                charQueue.push(...(data.message + '\n\n').split(''))
              } else if (data.status === 'preview') {
                charQueue.push(...(data.message + '\n\n').split(''))
                previewData.value = data.data
              } else if (data.status === 'success' || data.status === 'error') {
                charQueue.push(...(data.message).split(''))
              }
            } catch(e) { console.error("流式数据解析出错:", e) }
          }
        }
      }
    }
    streamEnded = true
  } catch (error) {
    charQueue.push(...(`\n❌ 严重异常: ${error.message}`).split(''))
    streamEnded = true
    if (error.response?.status === 401) logout()
  }
}

const confirmPush = async () => {
  if (!previewData.value) return
  try {
    const token = localStorage.getItem('ai_token')
    const res = await axios.post(`${API_BASE_URL}/confirm_push`, previewData.value, {
      headers: { Authorization: `Bearer ${token}` }
    })
    messageHistory.value.push({ role: 'assistant', content: res.data.message || '✅ 已推送至钉钉' })
    previewData.value = null
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '推送失败')
  }
}

const scrollToBottom = () => {
  nextTick(() => {
    if (scrollbarRef.value) {
      const wrap = scrollbarRef.value.wrapRef
      wrap.scrollTop = wrap.scrollHeight
    }
  })
}
</script>

<style scoped>
.app-container {
  height: 100vh;
  background-color: #f5f7fa;
}
.header {
  background-color: #1e1e2f;
  color: #fff;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
}
.logo {
  font-size: 18px;
  font-weight: bold;
}
.main-content {
  display: flex;
  justify-content: center;
  align-items: center;
  height: calc(100vh - 60px);
}
.auth-panel {
  width: 400px;
}
.full-btn {
  width: 100%;
  margin-top: 10px;
}
.chat-panel {
  width: 100%;
  max-width: 900px;
  height: 100%;
  padding: 20px 0;
}
.chat-card {
  height: 100%;
  display: flex;
  flex-direction: column;
  border-radius: 12px;
}
:deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.message-list {
  flex: 1;
  padding: 20px;
  background-color: #fafafa;
}
.message-item {
  display: flex;
  margin-bottom: 20px;
  align-items: flex-start;
}
.message-item.user {
  flex-direction: row-reverse;
}
.avatar {
  font-size: 24px;
  margin: 0 10px;
}
.bubble {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.message-item.assistant .bubble {
  background-color: #fff;
  border: 1px solid #ebeef5;
}
.message-item.user .bubble {
  background-color: #409eff;
  color: #fff;
}
.file-preview {
  margin-bottom: 8px;
}
.preview-img {
  width: 150px;
  height: 150px;
  border-radius: 4px;
}
.input-area {
  padding: 15px;
  background-color: #fff;
  border-top: 1px solid #ebeef5;
}
.confirm-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  margin-bottom: 10px;
  background-color: #f0f9eb;
  border: 1px solid #b3e19d;
  border-radius: 6px;
}
.confirm-tip {
  flex: 1;
  font-size: 13px;
  color: #529b2e;
}
.input-container {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}
.upload-preview {
  margin-bottom: 10px;
}
.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
.tip-text {
  font-size: 12px;
  color: #909399;
}

/* 🚀 核心架构升级：ChatGPT 式闪烁光标特效 */
.cursor {
    display: inline-block;
    width: 7px;
    height: 16px;
    background-color: #409eff;
    vertical-align: text-bottom;
    margin-left: 4px;
    animation: blink 1s step-end infinite;
    border-radius: 2px;
}
@keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
}
</style>