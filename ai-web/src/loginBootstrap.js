import { createApp } from 'vue'
import { ElButton } from 'element-plus/es/components/button/index.mjs'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index.mjs'
import { ElIcon } from 'element-plus/es/components/icon/index.mjs'
import { ElInput } from 'element-plus/es/components/input/index.mjs'
import 'element-plus/theme-chalk/base.css'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-form-item.css'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-message.css'
import LoginApp from './LoginApp.vue'
import './login.css'

const loginComponents = [ElButton, ElForm, ElFormItem, ElIcon, ElInput]

export function mount(selector) {
  const app = createApp(LoginApp)
  loginComponents.forEach((component) => app.use(component))
  app.mount(selector)
}
