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
          <span>效率驾驶舱</span>
        </button>
        <button
          v-if="canViewExecution"
          :class="['nav-item', { active: routeName === 'execution' }]"
          type="button"
          @click="navigate('/admin/execution')"
        >
          <el-icon><Clock /></el-icon>
          <span>执行任务</span>
        </button>
        <button
          v-if="canViewBusinessLedger"
          :class="['nav-item', { active: routeName === 'businessLedger' }]"
          type="button"
          @click="navigate('/admin/business-ledger')"
        >
          <el-icon><Tickets /></el-icon>
          <span>商务台账</span>
        </button>
        <button
          v-if="canViewCostDb"
          :class="['nav-item', { active: routeName === 'costDb' }]"
          type="button"
          @click="navigate('/admin/cost-db')"
        >
          <el-icon><Document /></el-icon>
          <span>成本数据库</span>
        </button>
        <button
          v-if="canViewRequirementStandardization"
          :class="['nav-item', { active: routeName === 'requirementStandardization' }]"
          type="button"
          @click="navigate('/admin/requirement-standardization')"
        >
          <el-icon><Tickets /></el-icon>
          <span>需求单标准化</span>
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
          <p>驾驶舱看板开关尚未打开。</p>
        </div>

        <template v-else-if="routeName === 'dashboard'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">Phase 1-2</p>
              <h2>效率驾驶舱</h2>
            </div>
            <div class="heading-actions">
              <el-radio-group v-model="dashboardRange" size="small" @change="loadDashboards">
                <el-radio-button
                  v-for="option in rangeOptions"
                  :key="option.value"
                  :label="option.value"
                >
                  {{ option.label }}
                </el-radio-button>
              </el-radio-group>
              <el-button :icon="Refresh" plain @click="loadDashboards">刷新</el-button>
            </div>
          </div>

          <el-tabs v-model="dashboardTab" class="dashboard-tabs">
            <el-tab-pane label="报价速度" name="quote" :disabled="dashboardFeature.quoteDisabled">
              <el-alert
                v-if="dashboardFeature.quoteDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="报价速度看板开关尚未打开"
              />
              <template v-else>
                <el-alert
                  v-if="quoteDashboard?.empty_state"
                  class="dashboard-alert"
                  type="info"
                  show-icon
                  :closable="false"
                  title="暂无数据，数据从当前环境验证后开始统计"
                />
                <el-alert
                  v-else-if="quoteDashboard?.low_sample_warning"
                  class="dashboard-alert"
                  type="warning"
                  show-icon
                  :closable="false"
                  title="样本量较少，仅供参考"
                />

                <div class="metric-grid">
                  <div class="metric-card">
                    <span>报价任务</span>
                    <strong>{{ quoteDashboard?.sample_count ?? 0 }}</strong>
                    <small>已完成 {{ quoteDashboard?.completed_count ?? 0 }} · 已确认 {{ quoteDashboard?.confirmed_count ?? 0 }}</small>
                  </div>
                  <div class="metric-card">
                    <span>AI 生成耗时</span>
                    <strong>{{ formatMs(quoteDashboard?.ai_duration_avg_ms) }}</strong>
                    <small>来自成功任务 duration_ms</small>
                  </div>
                  <div class="metric-card">
                    <span>人工确认耗时</span>
                    <strong>{{ formatMs(quoteDashboard?.manual_confirm_duration_avg_ms) }}</strong>
                    <small>AI 完成到确认推送</small>
                  </div>
                  <div class="metric-card">
                    <span>总交付耗时</span>
                    <strong>{{ formatMs(quoteDashboard?.total_delivery_duration_avg_ms) }}</strong>
                    <small>任务创建到确认推送</small>
                  </div>
                  <div class="metric-card">
                    <span>AI 修改率</span>
                    <strong>{{ formatRate(quoteDashboard?.modified_rate) }}</strong>
                    <small>{{ quoteDashboard?.modified_count ?? 0 }} / {{ quoteDashboard?.feedback_sample_count ?? 0 }} 条反馈</small>
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
                        v-for="item in quoteDashboard?.status_distribution || []"
                        :key="item.status"
                        class="status-row"
                      >
                        <span>{{ statusLabel(item.status) }}</span>
                        <strong>{{ item.count }}</strong>
                      </div>
                      <el-empty v-if="!quoteDashboard?.status_distribution?.length" description="暂无状态数据" />
                    </div>
                  </section>
                </div>

              </template>
            </el-tab-pane>

            <el-tab-pane label="响应速度" name="response" :disabled="dashboardFeature.responseDisabled">
              <el-alert
                v-if="dashboardFeature.responseDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="响应速度看板开关尚未打开"
              />
              <template v-else>
                <el-alert
                  v-if="responseDashboard?.empty_state"
                  class="dashboard-alert"
                  type="info"
                  show-icon
                  :closable="false"
                  title="暂无数据，数据从当前环境验证后开始统计"
                />
                <el-alert
                  v-else-if="responseDashboard?.low_sample_warning"
                  class="dashboard-alert"
                  type="warning"
                  show-icon
                  :closable="false"
                  title="样本量较少，仅供参考"
                />

                <div class="metric-grid response-grid">
                  <div class="metric-card">
                    <span>咨询样本</span>
                    <strong>{{ responseDashboard?.sample_count_total ?? 0 }}</strong>
                    <small>纳入均值 {{ responseDashboard?.sample_count_in_avg ?? 0 }}</small>
                  </div>
                  <div class="metric-card">
                    <span>平均首次响应</span>
                    <strong>{{ formatMinutes(responseDashboard?.avg_first_response_minutes) }}</strong>
                    <small>默认时间样本不纳入均值</small>
                  </div>
                  <div class="metric-card">
                    <span>SLA 达标率</span>
                    <strong>{{ formatRate(responseDashboard?.sla_pass_rate) }}</strong>
                    <small>阈值 {{ responseDashboard?.sla_minutes ?? '-' }} 分钟</small>
                  </div>
                  <div class="metric-card">
                    <span>排除默认时间</span>
                    <strong>{{ responseDashboard?.sample_count_excluded_default_time ?? 0 }}</strong>
                    <small>仅计数量，不计平均响应</small>
                  </div>
                  <div class="metric-card">
                    <span>超过 SLA</span>
                    <strong>{{ responseDashboard?.overdue_count ?? 0 }}</strong>
                    <small>仅统计可信时间样本</small>
                  </div>
                </div>

                <div class="dashboard-split">
                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><TrendCharts /></el-icon>
                      <span>来源分布</span>
                    </div>
                    <el-table
                      :data="visibleResponseSources"
                      row-key="source"
                      class="users-table"
                      empty-text="暂无来源数据"
                    >
                      <el-table-column prop="source" label="需求来源" min-width="120" />
                      <el-table-column prop="sample_count_total" label="咨询" width="90" />
                      <el-table-column prop="sample_count_in_avg" label="计入均值" width="100" />
                      <el-table-column prop="sample_count_excluded_default_time" label="默认时间排除" width="130" />
                      <el-table-column label="平均响应" min-width="120">
                        <template #default="{ row }">{{ formatMinutes(row.avg_first_response_minutes) }}</template>
                      </el-table-column>
                    </el-table>
                  </section>

                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><Histogram /></el-icon>
                      <span>响应人员</span>
                    </div>
                    <div class="status-list">
                      <div
                        v-for="item in visibleResponseResponders"
                        :key="item.username"
                        class="status-row stacked"
                      >
                        <span>{{ item.username }}</span>
                        <strong>{{ formatMinutes(item.avg_first_response_minutes) }}</strong>
                        <small>{{ item.sample_count_in_avg }} / {{ item.sample_count_total }} 条可信样本</small>
                      </div>
                      <el-empty v-if="!visibleResponseResponders.length" description="暂无人员数据" />
                    </div>
                  </section>
                </div>

                <section class="dashboard-section">
                  <div class="section-title">
                    <el-icon><Tickets /></el-icon>
                    <span>咨询记录</span>
                    <small>近 {{ clientInquiryTotal }} 条</small>
                  </div>
                  <div class="inquiry-filters">
                    <el-select
                      v-model="clientInquiryFilters.source"
                      size="small"
                      placeholder="需求来源"
                      @change="applyClientInquiryFilters"
                    >
                      <el-option
                        v-for="option in clientInquirySourceOptions"
                        :key="option.value"
                        :label="option.label"
                        :value="option.value"
                      />
                    </el-select>
                    <el-input
                      v-model="clientInquiryFilters.keyword"
                      size="small"
                      clearable
                      placeholder="客户姓名或电话"
                      @keyup.enter="applyClientInquiryFilters"
                      @clear="applyClientInquiryFilters"
                    />
                    <el-checkbox
                      v-model="clientInquiryFilters.hasQuoteJob"
                      @change="applyClientInquiryFilters"
                    >
                      只看有报价
                    </el-checkbox>
                    <el-button size="small" type="primary" plain @click="applyClientInquiryFilters">查询</el-button>
                    <el-button size="small" :icon="Refresh" plain @click="loadClientInquiries">刷新</el-button>
                  </div>
                  <el-table
                    :data="clientInquiries"
                    row-key="inquiry_id"
                    class="users-table"
                    empty-text="暂无咨询记录"
                  >
                    <el-table-column prop="inquiry_time" label="咨询时间" min-width="150">
                      <template #default="{ row }">{{ formatDate(row.inquiry_time) }}</template>
                    </el-table-column>
                    <el-table-column prop="source" label="需求来源" width="110" />
                    <el-table-column prop="client_name" label="客户姓名" min-width="120" />
                    <el-table-column prop="client_phone" label="联系电话" min-width="130" />
                    <el-table-column prop="quote_job_count" label="报价" width="80" align="right" />
                    <el-table-column prop="time_source" label="时间来源" width="110" />
                    <el-table-column prop="notes" label="备注" min-width="160" show-overflow-tooltip />
                  </el-table>
                  <el-pagination
                    v-if="clientInquiryTotal > clientInquiryPageSize"
                    v-model:current-page="clientInquiryPage"
                    :page-size="clientInquiryPageSize"
                    :total="clientInquiryTotal"
                    layout="total, prev, pager, next"
                    small
                    @current-change="loadClientInquiries"
                  />
                </section>
              </template>
            </el-tab-pane>

            <el-tab-pane v-if="canViewQuoteOperations" label="报价运营" name="operations">
              <section class="dashboard-section">
                <div class="section-title">
                  <el-icon><Document /></el-icon>
                  <span>报价任务闭环</span>
                  <small>共 {{ quoteJobTotal }} 条</small>
                </div>
                <div class="operation-filters">
                  <el-select
                    v-model="quoteJobFilters.status"
                    size="small"
                    clearable
                    placeholder="任务状态"
                    @change="applyQuoteJobFilters"
                  >
                    <el-option
                      v-for="option in quoteJobStatusOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                  <el-select
                    v-model="quoteJobFilters.source"
                    size="small"
                    clearable
                    placeholder="需求来源"
                    @change="applyQuoteJobFilters"
                  >
                    <el-option
                      v-for="option in clientInquirySourceOptions.slice(1)"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                  <el-input
                    v-model="quoteJobFilters.keyword"
                    size="small"
                    clearable
                    placeholder="客户/电话/任务"
                    @keyup.enter="applyQuoteJobFilters"
                    @clear="applyQuoteJobFilters"
                  />
                  <el-input
                    v-model="quoteJobFilters.username"
                    size="small"
                    clearable
                    placeholder="提交人"
                    @keyup.enter="applyQuoteJobFilters"
                    @clear="applyQuoteJobFilters"
                  />
                  <el-button size="small" type="primary" plain @click="applyQuoteJobFilters">查询</el-button>
                  <el-button size="small" :icon="Refresh" plain @click="loadQuoteJobs">刷新</el-button>
                  <el-button size="small" :icon="Clock" plain @click="markQuoteTimeouts">标记超时</el-button>
                </div>
                <el-table
                  :data="quoteJobs"
                  row-key="job_id"
                  class="users-table"
                  empty-text="暂无报价任务"
                >
                  <el-table-column prop="created_at" label="提交时间" min-width="150">
                    <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
                  </el-table-column>
                  <el-table-column label="客户" min-width="190">
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.client_inquiry?.client_name || '-' }}</strong>
                        <small>{{ row.client_inquiry?.client_phone || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="需求" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <span>{{ row.request_summary || row.message_preview || '-' }}</span>
                        <small>{{ row.client_inquiry?.source || '未填写' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="username" label="提交人" width="110" />
                  <el-table-column label="状态" width="110">
                    <template #default="{ row }">
                      <el-tag :type="jobStatusTag(row.status)" effect="plain">{{ statusLabel(row.status) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="AI 耗时" width="110">
                    <template #default="{ row }">{{ formatMs(row.duration_ms) }}</template>
                  </el-table-column>
                  <el-table-column label="确认/推送" min-width="150">
                    <template #default="{ row }">
                      <div class="operation-client">
                        <span>{{ row.history ? formatAmount(row.history.total_amount) : '未确认' }}</span>
                        <small>{{ pushStatusLabel(row.history) }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="异常" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.error_message || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="250" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions">
                        <el-button size="small" :icon="Document" plain @click="openQuoteJobDetail(row)">详情</el-button>
                        <el-button
                          size="small"
                          :icon="Refresh"
                          plain
                          :disabled="!canRetryQuoteJob(row)"
                          @click="retryQuoteJob(row)"
                        >
                          重试
                        </el-button>
                        <el-button
                          size="small"
                          :icon="Delete"
                          type="danger"
                          plain
                          :disabled="!canCancelQuoteJob(row)"
                          @click="cancelQuoteJob(row)"
                        >
                          取消
                        </el-button>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="quoteJobTotal > quoteJobPageSize"
                  v-model:current-page="quoteJobPage"
                  :page-size="quoteJobPageSize"
                  :total="quoteJobTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadQuoteJobs"
                />
              </section>
            </el-tab-pane>

            <el-tab-pane label="执行速度" name="execution" :disabled="dashboardFeature.executionDisabled">
              <el-alert
                v-if="dashboardFeature.executionDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="执行速度看板开关尚未打开"
              />
              <template v-else>
                <el-alert
                  v-if="executionDashboard?.empty_state"
                  class="dashboard-alert"
                  type="info"
                  show-icon
                  :closable="false"
                  title="暂无执行任务数据"
                />
                <el-alert
                  v-else-if="executionDashboard?.low_sample_warning"
                  class="dashboard-alert"
                  type="warning"
                  show-icon
                  :closable="false"
                  title="样本量较少，仅供参考"
                />
                <div class="metric-grid response-grid">
                  <div class="metric-card">
                    <span>执行任务</span>
                    <strong>{{ executionDashboard?.task_count ?? 0 }}</strong>
                    <small>未完成 {{ executionDashboard?.open_count ?? 0 }}</small>
                  </div>
                  <div class="metric-card">
                    <span>已完成</span>
                    <strong>{{ executionDashboard?.done_count ?? 0 }}</strong>
                    <small>状态 done</small>
                  </div>
                  <div class="metric-card">
                    <span>逾期任务</span>
                    <strong>{{ executionDashboard?.overdue_count ?? 0 }}</strong>
                    <small>动态按截止时间计算</small>
                  </div>
                  <div class="metric-card">
                    <span>平均完成耗时</span>
                    <strong>{{ formatMs(executionDashboard?.avg_completion_duration_ms) }}</strong>
                    <small>创建到完成</small>
                  </div>
                  <div class="metric-card">
                    <span>已取消</span>
                    <strong>{{ executionDashboard?.cancelled_count ?? 0 }}</strong>
                    <small>终态任务</small>
                  </div>
                </div>

                <div class="dashboard-split">
                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><TrendCharts /></el-icon>
                      <span>执行趋势</span>
                    </div>
                    <el-table
                      :data="visibleExecutionTrends"
                      row-key="date"
                      class="users-table"
                      empty-text="暂无趋势数据"
                    >
                      <el-table-column prop="date" label="日期" min-width="120" />
                      <el-table-column prop="task_count" label="任务" width="90" />
                      <el-table-column prop="done_count" label="完成" width="90" />
                      <el-table-column prop="cancelled_count" label="取消" width="90" />
                      <el-table-column prop="overdue_count" label="逾期" width="90" />
                      <el-table-column label="平均完成" min-width="120">
                        <template #default="{ row }">{{ formatMs(row.avg_completion_duration_ms) }}</template>
                      </el-table-column>
                    </el-table>
                  </section>

                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><Histogram /></el-icon>
                      <span>负责人</span>
                    </div>
                    <div class="status-list">
                      <div
                        v-for="item in visibleExecutionAssignees"
                        :key="item.assignee_id"
                        class="status-row stacked"
                      >
                        <span>{{ item.username }}</span>
                        <strong>{{ item.done_count }} / {{ item.task_count }}</strong>
                        <small>逾期 {{ item.overdue_count }} · 平均 {{ formatMs(item.avg_completion_duration_ms) }}</small>
                      </div>
                      <el-empty v-if="!visibleExecutionAssignees.length" description="暂无负责人数据" />
                    </div>
                  </section>
                </div>
              </template>
            </el-tab-pane>
          </el-tabs>
        </template>

        <template v-else-if="routeName === 'execution'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">Phase 3 / 4a</p>
              <h2>执行系统</h2>
            </div>
            <div class="heading-actions">
              <el-button v-if="canCreateMeetingNote" :icon="Tickets" type="primary" plain @click="openMeetingCreate">
                录入纪要
              </el-button>
              <el-button v-if="canCreateExecutionTask" :icon="Plus" type="primary" @click="openExecutionCreate">
                新建任务
              </el-button>
              <el-button :icon="Refresh" plain @click="refreshExecutionPage">刷新</el-button>
            </div>
          </div>
          <el-tabs v-model="executionPageTab" class="dashboard-tabs">
            <el-tab-pane label="执行任务" name="tasks">
              <el-alert
                v-if="executionFeatureDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="执行任务功能尚未开启"
              />
              <template v-else>
                <div class="operation-filters">
                  <el-select
                    v-model="executionTaskFilters.status"
                    size="small"
                    clearable
                    placeholder="任务状态"
                    @change="applyExecutionTaskFilters"
                  >
                    <el-option
                      v-for="option in executionStatusOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                  <el-select
                    v-model="executionTaskFilters.source"
                    size="small"
                    clearable
                    placeholder="任务来源"
                    @change="applyExecutionTaskFilters"
                  >
                    <el-option
                      v-for="option in executionSourceOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                  <el-input
                    v-model="executionTaskFilters.keyword"
                    size="small"
                    clearable
                    placeholder="任务标题/备注"
                    @keyup.enter="applyExecutionTaskFilters"
                    @clear="applyExecutionTaskFilters"
                  />
                  <el-button size="small" type="primary" plain @click="applyExecutionTaskFilters">查询</el-button>
                </div>
                <el-table
                  :data="executionTasks"
                  row-key="id"
                  class="users-table"
                  empty-text="暂无执行任务"
                >
                  <el-table-column label="任务" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.title }}</strong>
                        <small>{{ executionSourceLabel(row.source) }} · {{ row.source_ref_id || '无来源编号' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="assignee_username" label="负责人" width="120" />
                  <el-table-column label="截止时间" min-width="150">
                    <template #default="{ row }">{{ formatDate(row.due_at) }}</template>
                  </el-table-column>
                  <el-table-column label="状态" width="110">
                    <template #default="{ row }">
                      <el-tag :type="executionStatusTag(row.status)" effect="plain">{{ executionStatusLabel(row.status) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="逾期" width="90">
                    <template #default="{ row }">
                      <el-tag :type="row.is_overdue ? 'danger' : 'info'" effect="plain">
                        {{ row.is_overdue ? '逾期' : '正常' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="notes" label="备注" min-width="180" show-overflow-tooltip />
                  <el-table-column label="操作" width="270" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions">
                        <el-button size="small" :icon="Document" plain @click="openExecutionDetail(row)">详情</el-button>
                        <el-button
                          size="small"
                          plain
                          :disabled="row.status !== 'pending'"
                          @click="updateExecutionTaskStatus(row, 'in_progress')"
                        >
                          开始
                        </el-button>
                        <el-button
                          size="small"
                          plain
                          :disabled="!['pending', 'in_progress'].includes(row.status)"
                          @click="updateExecutionTaskStatus(row, 'done')"
                        >
                          完成
                        </el-button>
                        <el-button
                          v-if="canCreateExecutionTask"
                          size="small"
                          type="danger"
                          plain
                          :disabled="!['pending', 'in_progress'].includes(row.status)"
                          @click="cancelExecutionTask(row)"
                        >
                          取消
                        </el-button>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="executionTaskTotal > executionTaskPageSize"
                  v-model:current-page="executionTaskPage"
                  :page-size="executionTaskPageSize"
                  :total="executionTaskTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadExecutionTasks"
                />
              </template>
            </el-tab-pane>

            <el-tab-pane label="会议纪要" name="meetings">
              <el-alert
                v-if="meetingFeatureDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="会议纪要功能尚未开启"
              />
              <template v-else>
                <div class="meeting-filters">
                  <el-select
                    v-model="meetingFilters.status"
                    size="small"
                    clearable
                    placeholder="纪要状态"
                    @change="applyMeetingFilters"
                  >
                    <el-option
                      v-for="option in meetingStatusOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                  <el-input
                    v-model="meetingFilters.keyword"
                    size="small"
                    clearable
                    placeholder="纪要内容/异常"
                    @keyup.enter="applyMeetingFilters"
                    @clear="applyMeetingFilters"
                  />
                  <el-button size="small" type="primary" plain @click="applyMeetingFilters">查询</el-button>
                  <el-button size="small" :icon="Refresh" plain @click="loadMeetings">刷新</el-button>
                </div>
                <el-table
                  :data="meetings"
                  row-key="id"
                  class="users-table"
                  empty-text="暂无会议纪要"
                >
                  <el-table-column label="纪要" min-width="260" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ meetingPreview(row) }}</strong>
                        <small>{{ row.created_by_username || '-' }} · {{ formatDate(row.created_at) }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="110">
                    <template #default="{ row }">
                      <el-tag :type="meetingStatusTag(row.status)" effect="plain">{{ meetingStatusLabel(row.status) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="提取" width="120">
                    <template #default="{ row }">
                      <el-tag effect="plain">{{ meetingAiStatusLabel(row.ai_status) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="草稿" width="120">
                    <template #default="{ row }">
                      {{ row.accepted_draft_count || 0 }} / {{ row.draft_count || 0 }}
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="190" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions">
                        <el-button size="small" :icon="Document" plain @click="openMeetingDetail(row)">详情</el-button>
                        <el-button
                          size="small"
                          type="danger"
                          plain
                          :disabled="row.status !== 'draft'"
                          @click="cancelMeeting(row)"
                        >
                          作废
                        </el-button>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="meetingTotal > meetingPageSize"
                  v-model:current-page="meetingPage"
                  :page-size="meetingPageSize"
                  :total="meetingTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadMeetings"
                />
              </template>
            </el-tab-pane>
          </el-tabs>
        </template>

        <template v-else-if="routeName === 'businessLedger'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">BIZ-1a</p>
              <h2>商务台账</h2>
            </div>
            <div class="heading-actions">
              <el-button :icon="Plus" type="primary" :disabled="businessLedgerFeatureDisabled" @click="openBusinessLedgerCreate">
                新建记录
              </el-button>
              <el-button :icon="Refresh" plain @click="loadBusinessLedgers">刷新</el-button>
            </div>
          </div>

          <el-alert
            v-if="businessLedgerFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="商务台账功能尚未开启"
          ></el-alert>
          <template v-else>
            <div class="business-ledger-filters">
              <el-select
                v-model="businessLedgerFilters.stage"
                size="small"
                multiple
                collapse-tags
                collapse-tags-tooltip
                clearable
                placeholder="阶段"
                @change="applyBusinessLedgerFilters"
              >
                <el-option
                  v-for="option in businessLedgerStageOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                ></el-option>
              </el-select>
              <el-select
                v-model="businessLedgerFilters.source"
                size="small"
                clearable
                placeholder="来源"
                @change="applyBusinessLedgerFilters"
              >
                <el-option
                  v-for="option in clientInquirySourceOptions.slice(1)"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                ></el-option>
              </el-select>
              <el-select
                v-if="canManageBusinessLedger"
                v-model="businessLedgerFilters.responder_id"
                size="small"
                filterable
                clearable
                placeholder="负责人"
                @change="applyBusinessLedgerFilters"
              >
                <el-option
                  v-for="user in businessLedgerResponderOptions"
                  :key="user.id"
                  :label="user.username"
                  :value="user.id"
                ></el-option>
              </el-select>
              <el-date-picker
                v-model="businessLedgerFilters.dateRange"
                size="small"
                type="datetimerange"
                value-format="YYYY-MM-DDTHH:mm:ss"
                format="YYYY-MM-DD HH:mm"
                start-placeholder="开始时间"
                end-placeholder="结束时间"
                @change="applyBusinessLedgerFilters"
              ></el-date-picker>
              <el-input
                v-model="businessLedgerFilters.keyword"
                size="small"
                clearable
                placeholder="客户/电话/备注"
                @keyup.enter="applyBusinessLedgerFilters"
                @clear="applyBusinessLedgerFilters"
              ></el-input>
              <el-checkbox
                v-model="businessLedgerFilters.overdue_only"
                @change="applyBusinessLedgerFilters"
              >
                只看逾期
              </el-checkbox>
              <el-button size="small" type="primary" plain @click="applyBusinessLedgerFilters">查询</el-button>
            </div>

            <el-table
              v-loading="businessLedgerLoading"
              :data="businessLedgers"
              row-key="inquiry_id"
              class="users-table business-ledger-table"
              empty-text="暂无商务台账"
              :row-class-name="businessLedgerRowClass"
            >
              <el-table-column label="客户/要点" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.client_name || '-' }}</strong>
                    <small>{{ businessLedgerPreview(row) }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="client_phone" label="联系方式" min-width="130" />
              <el-table-column prop="source" label="来源" width="110" />
              <el-table-column label="阶段" width="120">
                <template #default="{ row }">
                  <el-tag :type="businessStageTag(row.stage)" effect="plain">{{ row.stage || '-' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="下次跟进" min-width="160">
                <template #default="{ row }">
                  <div class="ledger-followup-cell">
                    <span>{{ formatDate(row.next_followup_at) }}</span>
                    <el-tag v-if="isBusinessLedgerOverdue(row)" type="danger" effect="plain" size="small">
                      逾期
                    </el-tag>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="responder_username" label="负责人" width="120" />
              <el-table-column prop="notes" label="备注" min-width="180" show-overflow-tooltip />
              <el-table-column label="操作" width="260" fixed="right">
                <template #default="{ row }">
                  <div class="row-actions">
                    <el-button size="small" :icon="Document" plain @click="openBusinessLedgerDetail(row)">详情</el-button>
                    <el-button
                      size="small"
                      plain
                      :disabled="!canEditBusinessLedger(row)"
                      @click="openBusinessLedgerEdit(row)"
                    >
                      编辑
                    </el-button>
                    <el-button
                      v-if="canManageBusinessLedger"
                      size="small"
                      type="danger"
                      plain
                      :disabled="!canCancelBusinessLedger(row)"
                      @click="cancelBusinessLedger(row)"
                    >
                      作废
                    </el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <el-pagination
              v-if="businessLedgerTotal > businessLedgerPageSize"
              v-model:current-page="businessLedgerPage"
              :page-size="businessLedgerPageSize"
              :total="businessLedgerTotal"
              layout="total, prev, pager, next"
              small
              @current-change="loadBusinessLedgers"
            ></el-pagination>
          </template>
        </template>

        <template v-else-if="routeName === 'costDb'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">BIZ-2a</p>
              <h2>成本数据库</h2>
            </div>
            <div class="heading-actions">
              <el-button
                v-if="canEditCostDb"
                :icon="Document"
                plain
                :disabled="costDbFeatureDisabled"
                @click="openCostImportDialog"
              >
                导入 Excel
              </el-button>
              <el-button
                v-if="canApproveCostDb"
                :icon="DataAnalysis"
                plain
                :loading="costRagSyncing"
                :disabled="costDbFeatureDisabled || costRagSyncing"
                @click="syncActiveCostItemsToRag"
              >
                同步 active 到 RAG
              </el-button>
              <el-button
                v-if="canViewCostDb"
                :icon="Clock"
                plain
                :disabled="costDbFeatureDisabled"
                @click="openCostRagSyncDialog"
              >
                同步记录
              </el-button>
              <el-button
                v-if="canExportCostDb"
                :icon="Download"
                plain
                :disabled="costDbFeatureDisabled || costDbLoading"
                @click="exportCostItems"
              >
                导出
              </el-button>
              <el-button
                v-if="canViewCostAudit"
                :icon="Search"
                plain
                :disabled="costDbFeatureDisabled"
                @click="openCostAuditDialog"
              >
                审计记录
              </el-button>
              <el-button
                :icon="TrendCharts"
                plain
                :disabled="costDbFeatureDisabled"
                @click="openCostLineageDrawer"
              >
                状态与流向
              </el-button>
              <el-button
                v-if="canApproveCostDb"
                :icon="Select"
                plain
                :loading="costAllSelecting"
                :disabled="costDbFeatureDisabled || costDbLoading || costAllSelecting || costItemTotal === 0"
                @click="toggleSelectAllCostItems"
              >
                {{ selectedCostItemIds.length ? '取消全选' : '全选全部' }}
              </el-button>
              <el-button
                v-if="canApproveCostDb"
                :icon="Tickets"
                type="success"
                plain
                :loading="costBulkSubmitting"
                :disabled="costDbFeatureDisabled || costBulkSubmitting || selectedDraftCostItemCount === 0"
                @click="bulkActivateCostItems"
              >
                批量核定 active
              </el-button>
              <el-button
                v-if="canApproveCostDb"
                :icon="Refresh"
                type="warning"
                plain
                :loading="costBulkSubmitting"
                :disabled="costDbFeatureDisabled || costBulkSubmitting || selectedActiveCostItemCount === 0"
                @click="bulkRestoreCostItemsToDraft"
              >
                批量恢复 draft
              </el-button>
              <el-button
                v-if="canApproveCostDb"
                :icon="Delete"
                type="danger"
                plain
                :loading="costBulkSubmitting"
                :disabled="costDbFeatureDisabled || costBulkSubmitting || selectedArchivableCostItemCount === 0"
                @click="bulkArchiveCostItems"
              >
                批量归档
              </el-button>
              <el-button
                v-if="canEditCostDb"
                :icon="Plus"
                type="primary"
                :disabled="costDbFeatureDisabled"
                @click="openCostItemCreate"
              >
                新建条目
              </el-button>
              <el-button :icon="Refresh" plain @click="loadCostItems">刷新</el-button>
            </div>
          </div>

          <el-alert
            v-if="costDbFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="成本数据库功能尚未开启"
          ></el-alert>
          <template v-else>
            <el-alert
              v-if="costRagSyncStatus"
              class="dashboard-alert"
              :type="costRagSyncSummaryAlertType(costRagSyncStatus.status)"
              show-icon
              :closable="false"
            >
              <template #title>
                <span>
                  RAG 同步状态：{{ costRagSyncStatus.status_label || costRagSyncSummaryLabel(costRagSyncStatus.status) }}
                  · active {{ costRagSyncStatus.active_count || 0 }} 条
                  · 最近成功 {{ formatShanghaiDate(costRagSyncStatus.latest_successful_run?.finished_at) }}
                </span>
              </template>
              <div>{{ costRagSyncStatus.message || '暂无同步状态' }}</div>
            </el-alert>
            <div class="cost-db-filters cost-item-filters">
              <el-input
                v-model="costItemFilters.category"
                size="small"
                clearable
                placeholder="类别/子类"
                @keyup.enter="applyCostItemFilters"
                @clear="applyCostItemFilters"
              ></el-input>
              <el-select
                v-model="costItemFilters.status"
                size="small"
                multiple
                collapse-tags
                collapse-tags-tooltip
                clearable
                placeholder="状态"
                @change="applyCostItemFilters"
              >
                <el-option
                  v-for="option in costStatusOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                ></el-option>
              </el-select>
              <el-select
                v-model="costItemFilters.price_type"
                size="small"
                clearable
                placeholder="价格类型"
                @change="applyCostItemFilters"
              >
                <el-option
                  v-for="option in costPriceTypeOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                ></el-option>
              </el-select>
              <el-select
                v-model="costItemFilters.source"
                size="small"
                clearable
                placeholder="来源"
                @change="applyCostItemFilters"
              >
                <el-option
                  v-for="option in costSourceOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                ></el-option>
              </el-select>
              <el-input
                v-model="costItemFilters.keyword"
                size="small"
                clearable
                placeholder="名称/特征/类别/备注"
                @keyup.enter="applyCostItemFilters"
                @clear="applyCostItemFilters"
              ></el-input>
              <el-button size="small" type="primary" plain @click="applyCostItemFilters">查询</el-button>
            </div>

            <el-table
              ref="costItemsTable"
              v-loading="costDbLoading"
              :data="costItems"
              row-key="id"
              class="users-table cost-db-table"
              empty-text="暂无成本条目"
              @selection-change="handleCostItemSelectionChange"
            >
              <el-table-column
                v-if="canApproveCostDb"
                type="selection"
                width="48"
                :selectable="costItemSelectable"
              ></el-table-column>
              <el-table-column label="成本项/特征" min-width="260" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.item_name || '-' }}</strong>
                    <small>{{ row.spec || '-' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="类别" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.category || '-' }}</strong>
                    <small>{{ row.subcategory || '-' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="unit" label="单位" width="80" />
              <el-table-column label="状态" width="96">
                <template #default="{ row }">
                  <el-tag :type="costStatusTag(row.status)" effect="plain">
                    {{ costStatusLabel(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="类型" width="96">
                <template #default="{ row }">{{ costPriceTypeLabel(row.price_type) }}</template>
              </el-table-column>
              <el-table-column label="价格" min-width="210">
                <template #default="{ row }">
                  <div class="price-stack">
                    <span>主参考：{{ formatPrice(row.price) }}</span>
                    <small>对甲：{{ formatPrice(row.client_tax_excluded_price) }}</small>
                    <small>劳务：{{ formatPrice(row.subcontract_composite_price) }}</small>
                    <small>班组：{{ formatPrice(row.crew_benchmark_price) }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="来源" width="100">
                <template #default="{ row }">{{ costSourceLabel(row.source) }}</template>
              </el-table-column>
              <el-table-column label="更新时间" min-width="160">
                <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="390" fixed="right">
                <template #default="{ row }">
                  <div class="row-actions">
                    <el-button size="small" :icon="Document" plain @click="openCostItemDetail(row)">详情</el-button>
                    <el-button
                      v-if="canEditCostDb"
                      size="small"
                      plain
                      :disabled="row.status === 'archived'"
                      @click="openCostItemEdit(row)"
                    >
                      编辑
                    </el-button>
                    <el-button
                      v-if="canApproveCostDb"
                      size="small"
                      type="success"
                      plain
                      :disabled="row.status !== 'draft'"
                      @click="activateCostItem(row)"
                    >
                      启用
                    </el-button>
                    <el-button
                      v-if="canApproveCostDb"
                      size="small"
                      type="warning"
                      plain
                      :disabled="row.status !== 'active'"
                      @click="withdrawCostItem(row)"
                    >
                      撤回启用
                    </el-button>
                    <el-button
                      v-if="canApproveCostDb"
                      size="small"
                      type="danger"
                      plain
                      :disabled="row.status === 'archived'"
                      @click="archiveCostItem(row)"
                    >
                      归档
                    </el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <el-pagination
              v-if="costItemTotal > costItemPageSize"
              v-model:current-page="costItemPage"
              :page-size="costItemPageSize"
              :total="costItemTotal"
              layout="total, prev, pager, next"
              small
              @current-change="loadCostItems"
            ></el-pagination>
          </template>
        </template>

        <template v-else-if="routeName === 'requirementStandardization'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">BIZ-2l-2 / BIZ-2l-3</p>
              <h2>需求单标准化确认</h2>
            </div>
            <div class="heading-actions">
              <el-upload
                :auto-upload="false"
                :show-file-list="true"
                :limit="1"
                accept=".xlsx,.xlsm"
                :on-change="handleRequirementFileChange"
                :on-remove="clearRequirementFile"
              >
                <el-button :icon="Document" plain>选择 Excel</el-button>
              </el-upload>
              <el-button
                type="primary"
                :icon="DataAnalysis"
                :loading="requirementLoading"
                :disabled="!requirementFile || requirementFeatureDisabled"
                @click="previewRequirementStandardization"
              >
                解析预览
              </el-button>
              <el-button :icon="Clock" plain @click="openRequirementHistory">历史解析记录</el-button>
              <el-button
                :icon="Select"
                plain
                :disabled="!requirementPreview"
                @click="saveRequirementProgress('手动保存进度')"
              >
                保存进度
              </el-button>
              <el-button :icon="Refresh" plain @click="resetRequirementStandardization">重置</el-button>
            </div>
          </div>

          <el-alert
            v-if="requirementFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="需求单标准化功能尚未开启"
          ></el-alert>

          <template v-else>
            <div v-if="requirementPreview" class="metric-grid">
              <div class="metric-card">
                <span>Sheet</span>
                <strong>{{ requirementSummary.sheet_count || 0 }}</strong>
                <small>映射候选 {{ visibleRequirementSheetMappings.length }}，已隐藏 {{ hiddenRequirementSheetCount }}</small>
              </div>
              <div class="metric-card">
                <span>标准行</span>
                <strong>{{ requirementSummary.standard_row_count || 0 }}</strong>
                <small>确认后可发起现有报价</small>
              </div>
              <div class="metric-card">
                <span>需确认</span>
                <strong>{{ requirementSummary.requires_confirmation_count || 0 }}</strong>
                <small>低置信度或价格列等风险</small>
              </div>
              <div class="metric-card">
                <span>已选择</span>
                <strong>{{ selectedRequirementRows.length }}</strong>
                <small>确认前仍可编辑</small>
              </div>
            </div>

            <section v-if="requirementPreview" class="dashboard-section requirement-section">
              <div class="section-title">
                <el-icon><Tickets /></el-icon>
                <span>人工列映射</span>
                <small>只显示有清单意义的 Sheet；总计 {{ requirementSummary.sheet_count || 0 }} 个，已隐藏 {{ hiddenRequirementSheetCount }} 个封面/说明/汇总类 Sheet</small>
              </div>
              <el-tabs v-model="requirementActiveSheet" class="dashboard-tabs">
                <el-tab-pane
                  v-for="sheet in visibleRequirementSheetMappings"
                  :key="sheet.sheet_name"
                  :label="sheet.sheet_name"
                  :name="sheet.sheet_name"
                  >
                  <el-table
                    :data="visibleRequirementColumns(sheet)"
                    row-key="column"
                    class="users-table"
                    empty-text="暂无列信息"
                  >
                    <el-table-column prop="column" label="列" width="70" />
                    <el-table-column label="原始表头" min-width="140" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.label || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="样例" min-width="220" show-overflow-tooltip>
                      <template #default="{ row }">{{ (row.sample_values || []).slice(0, 3).join(' / ') || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="标准字段" width="190">
                      <template #default="{ row }">
                        <el-select v-model="requirementMappings[sheet.sheet_name][row.column]" size="small">
                          <el-option
                            v-for="option in requirementFieldOptions"
                            :key="option.value"
                            :label="option.label"
                            :value="option.value"
                          ></el-option>
                        </el-select>
                      </template>
                    </el-table-column>
                  </el-table>
                </el-tab-pane>
              </el-tabs>
              <div class="section-actions">
                <el-button
                  type="primary"
                  plain
                  :loading="requirementLoading"
                  @click="remapRequirementStandardization"
                >
                  应用列映射
                </el-button>
              </div>
            </section>

            <section v-if="visibleRequirementRows.length" class="dashboard-section requirement-section">
              <div class="section-title">
                <el-icon><Document /></el-icon>
                <span>行确认</span>
                <small>按 Sheet 独立确认；“原始行”保留 Excel 原始列值，便于核对多层工程量</small>
              </div>
              <div class="requirement-filters">
                <el-input
                  v-model="requirementRowFilters.keyword"
                  clearable
                  :prefix-icon="Search"
                  placeholder="搜索项目名称、规格、备注、来源行号或原始行内容"
                  @keyup.enter="focusFirstRequirementMatch"
                />
                <el-select v-model="requirementRowFilters.status" class="requirement-filter-select">
                  <el-option
                    v-for="option in requirementRowFilterOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  ></el-option>
                </el-select>
                <el-button :icon="Search" plain @click="focusFirstRequirementMatch">定位首条</el-button>
                <span class="filter-count">匹配 {{ filteredRequirementRows.length }} / {{ visibleRequirementRows.length }} 行</span>
              </div>
              <div class="requirement-bulk-actions">
                <span>批量操作当前筛选结果</span>
                <el-button plain size="small" :disabled="!filteredRequirementRows.length" @click="bulkIncludeRequirementRows(true)">
                  全选
                </el-button>
                <el-button plain size="small" :disabled="!filteredRequirementRows.length" @click="bulkIncludeRequirementRows(false)">
                  取消选择
                </el-button>
                <el-button type="success" plain size="small" :disabled="!filteredRequirementRows.length" @click="bulkConfirmRequirementRows(true)">
                  批量确认
                </el-button>
                <el-button type="warning" plain size="small" :disabled="!filteredRequirementRows.length" @click="bulkConfirmRequirementRows(false)">
                  批量撤回确认
                </el-button>
                <small>已选 {{ selectedRequirementRows.length }} 行，当前筛选 {{ filteredRequirementRows.length }} 行</small>
              </div>
              <div v-if="requirementBlockedRows.length" class="requirement-validation-panel">
                <div class="requirement-validation-heading">
                  <div>
                    <el-icon><Warning /></el-icon>
                    <strong>校验问题</strong>
                    <small>{{ requirementBlockedRows.length }} 行需要处理，已自动切到问题行</small>
                  </div>
                  <el-button type="warning" plain size="small" @click="focusFirstRequirementBlockedRow">
                    定位首条问题
                  </el-button>
                </div>
                <el-table
                  :data="requirementBlockedRows"
                  row-key="blocked_row_key"
                  class="users-table requirement-validation-table"
                  empty-text="暂无校验问题"
                >
                  <el-table-column label="来源" width="140">
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.source_sheet || '-' }}</strong>
                        <small>原始行 {{ row.raw_row_index || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="原始行内容" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">
                      <span class="raw-cells-inline">{{ requirementRawCellsText(row) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="项目名称" min-width="190" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.item_name || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="数量/单位" width="150">
                    <template #default="{ row }">
                      {{ row.quantity === null || row.quantity === undefined || row.quantity === '' ? '-' : row.quantity }}{{ row.unit || '' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="错误原因" min-width="220">
                    <template #default="{ row }">
                      <div class="validation-error-list">
                        <span
                          v-for="message in requirementValidationMessages(row)"
                          :key="message"
                        >
                          {{ message }}
                        </span>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="120" fixed="right">
                    <template #default="{ row }">
                      <el-button type="warning" plain size="small" @click="locateRequirementBlockedRow(row)">
                        定位处理
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <el-tabs v-if="filteredRequirementRows.length" v-model="requirementActiveRowSheet" class="dashboard-tabs">
                <el-tab-pane
                  v-for="sheet in visibleRequirementRowSheets"
                  :key="sheet.sheet_name"
                  :label="`${sheet.sheet_name} (${visibleRequirementRowsForSheet(sheet.sheet_name).length})`"
                  :name="sheet.sheet_name"
                >
                  <el-alert
                    v-if="hiddenRequirementRowCountForSheet(sheet.sheet_name)"
                    class="dashboard-alert"
                    type="info"
                    show-icon
                    :closable="false"
                    :title="`本 Sheet 已自动隐藏 ${hiddenRequirementRowCountForSheet(sheet.sheet_name)} 条说明/汇总/空白行`"
                  ></el-alert>
                  <el-table
                    :data="visibleRequirementRowsForSheet(sheet.sheet_name)"
                    row-key="requirement_row_key"
                    :row-class-name="requirementRowClassName"
                    class="users-table requirement-table"
                    empty-text="暂无标准化行"
                  >
                    <el-table-column label="进入清单" width="92">
                      <template #default="{ row }">
                        <el-checkbox v-model="row.include"></el-checkbox>
                      </template>
                    </el-table-column>
                    <el-table-column label="来源" width="96">
                      <template #default="{ row }">
                        <div class="operation-client">
                          <strong>行 {{ row.raw_row_index }}</strong>
                          <small>{{ row.source_sheet || '-' }}</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="原始行" min-width="260" show-overflow-tooltip>
                      <template #default="{ row }">
                        <span class="raw-cells-inline">{{ requirementRawCellsText(row) }}</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="项目名称" min-width="220">
                      <template #default="{ row }">
                        <el-input v-model="row.item_name" size="small" />
                      </template>
                    </el-table-column>
                    <el-table-column label="规格/特征" min-width="220">
                      <template #default="{ row }">
                        <el-input v-model="row.spec" size="small" />
                      </template>
                    </el-table-column>
                    <el-table-column label="标准数量" width="130">
                      <template #default="{ row }">
                        <el-input v-model="row.quantity" size="small" />
                      </template>
                    </el-table-column>
                    <el-table-column label="数量来源" min-width="190">
                      <template #default="{ row }">
                        <el-select
                          v-if="row.quantity_candidates?.length"
                          v-model="row.quantity_source_key"
                          size="small"
                          @change="applyRequirementQuantitySource(row, $event)"
                        >
                          <el-option
                            v-for="candidate in row.quantity_candidates"
                            :key="candidate.key"
                            :label="requirementQuantityCandidateLabel(candidate)"
                            :value="candidate.key"
                          ></el-option>
                          <el-option label="手工填写" value="manual"></el-option>
                        </el-select>
                        <span v-else class="raw-cells-inline">{{ requirementQuantitySourceText(row) }}</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="原始工程量候选" min-width="230" show-overflow-tooltip>
                      <template #default="{ row }">
                        <span class="raw-cells-inline">{{ requirementQuantityCandidatesText(row) }}</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="单位" width="110">
                      <template #default="{ row }">
                        <el-input v-model="row.unit" size="small" />
                      </template>
                    </el-table-column>
                    <el-table-column label="备注" min-width="180">
                      <template #default="{ row }">
                        <el-input v-model="row.remark" size="small" />
                      </template>
                    </el-table-column>
                    <el-table-column label="风险" min-width="220">
                      <template #default="{ row }">
                        <div class="warning-stack">
                          <el-tag :type="requirementConfidenceType(row.confidence)" effect="plain">{{ row.confidence || '-' }}</el-tag>
                          <small>{{ (row.warnings || []).join(', ') || '无' }}</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="人工确认" width="110">
                      <template #default="{ row }">
                        <el-checkbox v-model="row.confirmed" :disabled="!row.include"></el-checkbox>
                      </template>
                    </el-table-column>
                  </el-table>
                </el-tab-pane>
              </el-tabs>
              <el-empty v-else description="没有匹配的行" />
              <div class="section-actions">
                <el-button
                  type="primary"
                  :loading="requirementConfirming"
                  :disabled="!selectedRequirementRows.length"
                  @click="confirmRequirementStandardization"
                >
                  生成确认清单
                </el-button>
              </div>
            </section>

            <section v-if="requirementConfirmed" class="dashboard-section requirement-section">
              <div class="section-title">
                <el-icon><Select /></el-icon>
                <span>已确认标准清单</span>
                <small>{{ requirementConfirmed.summary.confirmed_row_count }} 行</small>
              </div>
              <el-alert
                v-if="requirementConfirmed.summary.blocked_row_count"
                class="dashboard-alert"
                type="warning"
                show-icon
                :closable="false"
                :title="`有 ${requirementConfirmed.summary.blocked_row_count} 行未通过确认校验，下面已列出每一行原因`"
              ></el-alert>
              <div v-if="requirementBlockedRows.length" class="requirement-validation-panel requirement-validation-result">
                <div class="requirement-validation-heading">
                  <div>
                    <el-icon><Warning /></el-icon>
                    <strong>未通过确认校验明细</strong>
                    <small>{{ requirementBlockedRows.length }} 行，按原始 Sheet 和行号定位</small>
                  </div>
                  <el-button type="warning" plain size="small" @click="focusFirstRequirementBlockedRow">
                    定位首条问题
                  </el-button>
                </div>
                <el-table
                  :data="requirementBlockedRows"
                  row-key="blocked_row_key"
                  class="users-table requirement-validation-table"
                  empty-text="暂无未通过行"
                >
                  <el-table-column label="来源" width="140">
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.source_sheet || '-' }}</strong>
                        <small>原始行 {{ row.raw_row_index || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="项目名称" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.item_name || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="数量/单位" width="130">
                    <template #default="{ row }">
                      {{ row.quantity === null || row.quantity === undefined || row.quantity === '' ? '-' : row.quantity }}{{ row.unit || '' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="未通过原因" min-width="300">
                    <template #default="{ row }">
                      <div class="validation-error-list">
                        <span
                          v-for="message in requirementValidationMessages(row)"
                          :key="message"
                        >
                          {{ message }}
                        </span>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="原始行内容" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">
                      <span class="raw-cells-inline">{{ requirementRawCellsText(row) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="120" fixed="right">
                    <template #default="{ row }">
                      <el-button type="warning" plain size="small" @click="locateRequirementBlockedRow(row)">
                        定位处理
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <el-input
                v-model="requirementConfirmed.quote_text"
                type="textarea"
                :rows="8"
                readonly
              ></el-input>
              <div class="requirement-quote-actions">
                <div class="requirement-quote-note">
                  <strong>接入报价链路</strong>
                  <small>只发送已确认标准行，剔除行和未通过校验行不会进入报价。</small>
                </div>
                <el-button
                  type="primary"
                  :icon="DataAnalysis"
                  :loading="requirementQuoting"
                  :disabled="!selectedRequirementRows.length"
                  @click="startRequirementQuoteJob"
                >
                  发起报价
                </el-button>
              </div>
              <el-alert
                v-if="requirementQuoteJob"
                class="dashboard-alert"
                type="success"
                show-icon
                :closable="false"
                :title="`已创建报价任务：${requirementQuoteJob.job_id}`"
              >
                <template #default>
                  <div class="requirement-quote-result">
                    <span>状态：{{ jobStatusLabel(requirementQuoteJob.status) }}</span>
                    <span>阶段：{{ requirementQuoteJob.stage || '-' }}</span>
                    <el-button size="small" plain @click="openQuoteJobDetail(requirementQuoteJob)">查看任务详情</el-button>
                  </div>
                </template>
              </el-alert>
            </section>
          </template>
        </template>

        <template v-else>
          <div class="content-heading">
            <div>
              <p class="eyebrow">Phase 0</p>
              <h2>用户角色</h2>
            </div>
            <div class="row-actions">
              <el-button :icon="Refresh" plain @click="loadUsers">刷新</el-button>
              <el-button v-if="canMutateRoles" type="primary" :icon="Plus" @click="openCreateUser">新建用户</el-button>
            </div>
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

    <el-dialog v-model="businessLedgerDialog.visible" :title="businessLedgerDialogTitle" width="620px">
      <el-form label-position="top" :model="businessLedgerDialog.form">
        <div class="ledger-form-grid">
          <el-form-item label="客户">
            <el-input
              v-model="businessLedgerDialog.form.client_name"
              maxlength="128"
              :disabled="businessLedgerDialog.mode === 'edit' && !canManageBusinessLedger"
            ></el-input>
          </el-form-item>
          <el-form-item label="联系方式">
            <el-input v-model="businessLedgerDialog.form.client_phone" maxlength="64"></el-input>
          </el-form-item>
          <el-form-item label="来源">
            <el-select
              v-model="businessLedgerDialog.form.source"
              class="full-width"
              clearable
              :disabled="businessLedgerDialog.mode === 'edit' && !canManageBusinessLedger"
            >
              <el-option
                v-for="option in clientInquirySourceOptions.slice(1)"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              ></el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="阶段">
            <el-select v-model="businessLedgerDialog.form.stage" class="full-width">
              <el-option
                v-for="option in businessLedgerStageOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              ></el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="下次跟进">
            <el-date-picker
              v-model="businessLedgerDialog.form.next_followup_at"
              class="full-width"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              format="YYYY-MM-DD HH:mm"
            ></el-date-picker>
          </el-form-item>
          <el-form-item v-if="canManageBusinessLedger" label="负责人">
            <el-select v-model="businessLedgerDialog.form.responder_id" class="full-width" filterable>
              <el-option
                v-for="user in businessLedgerResponderOptions"
                :key="user.id"
                :label="user.username"
                :value="user.id"
              ></el-option>
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="备注">
          <el-input
            v-model="businessLedgerDialog.form.notes"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
          ></el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="businessLedgerDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="submitBusinessLedger">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="businessLedgerDrawer.visible" size="620px" title="商务台账详情">
      <div v-if="businessLedgerDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="businessLedgerDrawer.ledger">
        <div class="detail-grid">
          <div>
            <small>客户</small>
            <strong>{{ businessLedgerDrawer.ledger.client_name || '-' }}</strong>
          </div>
          <div>
            <small>联系方式</small>
            <strong>{{ businessLedgerDrawer.ledger.client_phone || '-' }}</strong>
          </div>
          <div>
            <small>来源</small>
            <strong>{{ businessLedgerDrawer.ledger.source || '-' }}</strong>
          </div>
          <div>
            <small>阶段</small>
            <strong>{{ businessLedgerDrawer.ledger.stage || '-' }}</strong>
          </div>
          <div>
            <small>负责人</small>
            <strong>{{ businessLedgerDrawer.ledger.responder_username || '-' }}</strong>
          </div>
          <div>
            <small>下次跟进</small>
            <strong>{{ formatDate(businessLedgerDrawer.ledger.next_followup_at) }}</strong>
          </div>
          <div>
            <small>创建时间</small>
            <strong>{{ formatDate(businessLedgerDrawer.ledger.created_at) }}</strong>
          </div>
          <div>
            <small>更新时间</small>
            <strong>{{ formatDate(businessLedgerDrawer.ledger.updated_at) }}</strong>
          </div>
        </div>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>备注</span>
          </div>
          <p class="detail-text">{{ businessLedgerDrawer.ledger.notes || '-' }}</p>
        </section>
        <section v-if="businessLedgerDrawer.ledger.cancelled_at" class="drawer-section">
          <div class="section-title">
            <el-icon><Delete /></el-icon>
            <span>作废记录</span>
          </div>
          <p class="detail-text">
            {{ formatDate(businessLedgerDrawer.ledger.cancelled_at) }} ·
            {{ businessLedgerDrawer.ledger.cancelled_by_username || '-' }} ·
            {{ businessLedgerDrawer.ledger.cancel_reason || '-' }}
          </p>
        </section>
      </template>
    </el-drawer>

    <el-dialog v-model="costItemDialog.visible" :title="costItemDialogTitle" width="720px">
      <el-form label-position="top" :model="costItemDialog.form">
        <div class="ledger-form-grid">
          <el-form-item label="类别">
            <el-input v-model="costItemDialog.form.category" maxlength="128"></el-input>
          </el-form-item>
          <el-form-item label="子类">
            <el-input v-model="costItemDialog.form.subcategory" maxlength="128"></el-input>
          </el-form-item>
          <el-form-item label="项目名称">
            <el-input v-model="costItemDialog.form.item_name" maxlength="255"></el-input>
          </el-form-item>
          <el-form-item label="计量单位">
            <el-input v-model="costItemDialog.form.unit" maxlength="32"></el-input>
          </el-form-item>
          <el-form-item label="价格类型">
            <el-select v-model="costItemDialog.form.price_type" class="full-width">
              <el-option
                v-for="option in costPriceTypeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              ></el-option>
            </el-select>
          </el-form-item>
          <el-form-item label="生效日期">
            <el-date-picker
              v-model="costItemDialog.form.effective_date"
              class="full-width"
              type="date"
              value-format="YYYY-MM-DD"
              format="YYYY-MM-DD"
            ></el-date-picker>
          </el-form-item>
          <el-form-item label="对甲税前综合单价">
            <el-input v-model="costItemDialog.form.client_tax_excluded_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="对甲人工费">
            <el-input v-model="costItemDialog.form.client_labor_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="对甲主材费">
            <el-input v-model="costItemDialog.form.client_main_material_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="对甲辅材费">
            <el-input v-model="costItemDialog.form.client_auxiliary_material_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="对甲直接费小计">
            <el-input v-model="costItemDialog.form.client_direct_fee" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="对甲管理费利润">
            <el-input v-model="costItemDialog.form.client_management_profit" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="劳务发包综合单价">
            <el-input v-model="costItemDialog.form.subcontract_composite_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="劳务人工费">
            <el-input v-model="costItemDialog.form.subcontract_labor_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="劳务主材费">
            <el-input v-model="costItemDialog.form.subcontract_main_material_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="劳务辅材费">
            <el-input v-model="costItemDialog.form.subcontract_auxiliary_material_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="班组标底税前价">
            <el-input v-model="costItemDialog.form.crew_benchmark_price" inputmode="decimal"></el-input>
          </el-form-item>
          <el-form-item label="主参考价">
            <el-input v-model="costItemDialog.form.price" inputmode="decimal" placeholder="留空时按劳务、班组、对甲顺序自动取值"></el-input>
          </el-form-item>
        </div>
        <el-form-item label="项目特征">
          <el-input
            v-model="costItemDialog.form.spec"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
          ></el-input>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="costItemDialog.form.notes"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
          ></el-input>
        </el-form-item>
        <el-form-item v-if="costItemDialog.mode === 'edit'" label="变更原因">
          <el-input
            v-model="costItemDialog.form.change_reason"
            maxlength="500"
            placeholder="价格调整、资料修正等"
          ></el-input>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="costItemDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="submitCostItem">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="costImportDialog.visible" title="导入成本 Excel" width="720px">
      <div class="cost-import-panel">
        <el-upload
          action="#"
          :auto-upload="false"
          :limit="1"
          :on-change="handleCostImportFile"
          :on-remove="clearCostImportFile"
        >
          <el-button :icon="Document" plain>选择 Excel</el-button>
        </el-upload>
        <div v-if="costImportDialog.preview" class="cost-import-summary">
          <div>
            <small>批次</small>
            <strong>{{ costImportDialog.preview.batch_id }}</strong>
          </div>
          <div>
            <small>可导入</small>
            <strong>{{ costImportDialog.preview.item_count }}</strong>
          </div>
          <div>
            <small>跳过行</small>
            <strong>{{ costImportDialog.preview.skipped_rows?.length || 0 }}</strong>
          </div>
          <div>
            <small>重复提示</small>
            <strong>{{ costImportDialog.preview.duplicate_warnings?.length || 0 }}</strong>
          </div>
        </div>
        <el-alert
          v-if="costImportDialog.preview?.duplicate_warnings?.length"
          type="warning"
          show-icon
          :closable="false"
          class="dashboard-alert"
          :title="`发现 ${costImportDialog.preview.duplicate_warnings.length} 条重复提示，确认导入时现有启用记录会跳过，草稿记录会更新。`"
        ></el-alert>
        <el-table
          v-if="costImportDialog.preview"
          :data="(costImportDialog.preview.items || []).slice(0, 8)"
          class="users-table"
          size="small"
          empty-text="暂无可导入条目"
        >
          <el-table-column prop="item_name" label="项目名称" min-width="180" show-overflow-tooltip />
          <el-table-column prop="unit" label="单位" width="72" />
          <el-table-column prop="category" label="类别" min-width="150" show-overflow-tooltip />
          <el-table-column label="主参考价" width="120">
            <template #default="{ row }">{{ formatPrice(row.price) }}</template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button @click="costImportDialog.visible = false">关闭</el-button>
        <el-button plain :loading="costImportDialog.loading" @click="previewCostImport">预览</el-button>
        <el-button
          type="primary"
          :disabled="!costImportDialog.preview?.batch_id"
          :loading="state.submitting"
          @click="confirmCostImport"
        >
          确认导入
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="costItemDrawer.visible" size="680px" title="成本条目详情">
      <div v-if="costItemDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="costItemDrawer.item">
        <div class="detail-grid">
          <div>
            <small>项目名称</small>
            <strong>{{ costItemDrawer.item.item_name || '-' }}</strong>
          </div>
          <div>
            <small>类别</small>
            <strong>{{ costItemDrawer.item.category || '-' }}</strong>
          </div>
          <div>
            <small>状态</small>
            <strong>{{ costStatusLabel(costItemDrawer.item.status) }}</strong>
          </div>
          <div>
            <small>价格类型</small>
            <strong>{{ costPriceTypeLabel(costItemDrawer.item.price_type) }}</strong>
          </div>
          <div>
            <small>主参考价</small>
            <strong>{{ formatPrice(costItemDrawer.item.price) }}</strong>
          </div>
          <div>
            <small>单位</small>
            <strong>{{ costItemDrawer.item.unit || '-' }}</strong>
          </div>
          <div>
            <small>来源</small>
            <strong>{{ costSourceLabel(costItemDrawer.item.source) }}</strong>
          </div>
          <div>
            <small>更新时间</small>
            <strong>{{ formatDate(costItemDrawer.item.updated_at) }}</strong>
          </div>
        </div>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>项目特征</span>
          </div>
          <p class="detail-text">{{ costItemDrawer.item.spec || '-' }}</p>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Histogram /></el-icon>
            <span>价格明细</span>
          </div>
          <div class="detail-grid">
            <div>
              <small>对甲税前综合单价</small>
              <strong>{{ formatPrice(costItemDrawer.item.client_tax_excluded_price) }}</strong>
            </div>
            <div>
              <small>对甲人工费</small>
              <strong>{{ formatPrice(costItemDrawer.item.client_labor_price) }}</strong>
            </div>
            <div>
              <small>对甲主材费</small>
              <strong>{{ formatPrice(costItemDrawer.item.client_main_material_price) }}</strong>
            </div>
            <div>
              <small>对甲辅材费</small>
              <strong>{{ formatPrice(costItemDrawer.item.client_auxiliary_material_price) }}</strong>
            </div>
            <div>
              <small>对甲直接费小计</small>
              <strong>{{ formatPrice(costItemDrawer.item.client_direct_fee) }}</strong>
            </div>
            <div>
              <small>对甲管理费利润</small>
              <strong>{{ formatPrice(costItemDrawer.item.client_management_profit) }}</strong>
            </div>
            <div>
              <small>劳务发包综合单价</small>
              <strong>{{ formatPrice(costItemDrawer.item.subcontract_composite_price) }}</strong>
            </div>
            <div>
              <small>劳务人工费</small>
              <strong>{{ formatPrice(costItemDrawer.item.subcontract_labor_price) }}</strong>
            </div>
            <div>
              <small>劳务主材费</small>
              <strong>{{ formatPrice(costItemDrawer.item.subcontract_main_material_price) }}</strong>
            </div>
            <div>
              <small>劳务辅材费</small>
              <strong>{{ formatPrice(costItemDrawer.item.subcontract_auxiliary_material_price) }}</strong>
            </div>
            <div>
              <small>班组标底税前价</small>
              <strong>{{ formatPrice(costItemDrawer.item.crew_benchmark_price) }}</strong>
            </div>
            <div>
              <small>生效日期</small>
              <strong>{{ costItemDrawer.item.effective_date || '-' }}</strong>
            </div>
          </div>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Tickets /></el-icon>
            <span>备注</span>
          </div>
          <p class="detail-text">{{ costItemDrawer.item.notes || '-' }}</p>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Clock /></el-icon>
            <span>变更历史</span>
          </div>
          <el-timeline>
            <el-timeline-item
              v-for="event in visibleCostHistory(costItemDrawer.item.history)"
              :key="event.id"
              :timestamp="formatDate(event.changed_at)"
              placement="top"
            >
              <div class="event-row">
                <strong>{{ costHistoryTypeLabel(event.change_type) }}</strong>
                <el-tag size="small" effect="plain">{{ event.changed_by_username || '-' }}</el-tag>
              </div>
              <p>{{ costHistoryText(event) }}</p>
              <small>{{ event.change_reason || '-' }}</small>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-if="!visibleCostHistory(costItemDrawer.item.history).length" description="暂无变更历史" />
        </section>
      </template>
    </el-drawer>

    <el-drawer v-model="costLineageDrawer.visible" size="1100px" title="成本库状态与流向">
      <el-tabs v-model="costLineageDrawer.activeTab" class="dashboard-tabs" @tab-click="handleCostLineageTabClick">
        <el-tab-pane label="总览" name="summary">
          <div v-loading="costLineageDrawer.summaryLoading">
            <div class="detail-grid lineage-summary-grid">
              <div>
                <small>草稿 draft</small>
                <strong>{{ costLineageSummary.by_status?.draft || 0 }}</strong>
              </div>
              <div>
                <small>启用 active</small>
                <strong>{{ costLineageSummary.by_status?.active || 0 }}</strong>
              </div>
              <div>
                <small>归档 archived</small>
                <strong>{{ costLineageSummary.by_status?.archived || 0 }}</strong>
              </div>
              <div>
                <small>AI 建议草稿</small>
                <strong>{{ costLineageSummary.ai_suggested_draft_count || 0 }}</strong>
              </div>
              <div>
                <small>被报价引用条目</small>
                <strong>{{ costLineageSummary.quote_used_count || 0 }}</strong>
              </div>
              <div>
                <small>active 且已引用</small>
                <strong>{{ costLineageSummary.active_quote_used_count || 0 }}</strong>
              </div>
              <div>
                <small>active RAG 范围</small>
                <strong>{{ costLineageSummary.active_rag_scope_count || 0 }}</strong>
              </div>
              <div>
                <small>最近 RAG 同步</small>
                <strong>{{ formatDate(costLineageSummary.latest_successful_rag_sync?.finished_at) }}</strong>
              </div>
            </div>
            <section class="drawer-section">
              <div class="section-title">
                <el-icon><DataAnalysis /></el-icon>
                <span>来源分布</span>
              </div>
              <div class="lineage-source-row">
                <el-tag effect="plain">人工 {{ costLineageSummary.by_source?.manual || 0 }}</el-tag>
                <el-tag effect="plain">导入 {{ costLineageSummary.by_source?.imported || 0 }}</el-tag>
                <el-tag effect="plain">AI 建议 {{ costLineageSummary.by_source?.ai_suggested || 0 }}</el-tag>
              </div>
            </section>
          </div>
        </el-tab-pane>
        <el-tab-pane label="新增 draft" name="draft"></el-tab-pane>
        <el-tab-pane label="active 记录" name="active"></el-tab-pane>
        <el-tab-pane label="归档记录" name="archived"></el-tab-pane>
      </el-tabs>

      <template v-if="costLineageDrawer.activeTab !== 'summary'">
        <div class="cost-db-filters lineage-filters">
          <el-select
            v-model="costLineageFilters.source"
            size="small"
            clearable
            placeholder="来源"
            @change="applyCostLineageFilters"
          >
            <el-option
              v-for="option in costSourceOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-select
            v-model="costLineageFilters.has_quote_usage"
            size="small"
            clearable
            placeholder="报价引用"
            @change="applyCostLineageFilters"
          >
            <el-option label="已被引用" value="true" />
            <el-option label="未被引用" value="false" />
          </el-select>
          <el-input
            v-model="costLineageFilters.keyword"
            size="small"
            clearable
            placeholder="名称/特征/来源备注"
            @keyup.enter="applyCostLineageFilters"
            @clear="applyCostLineageFilters"
          />
          <el-button size="small" type="primary" plain @click="applyCostLineageFilters">查询</el-button>
          <el-button size="small" :icon="Refresh" plain :loading="costLineageDrawer.loading" @click="loadCostLineageRows">刷新</el-button>
        </div>
        <div class="lineage-layout">
          <div>
            <el-table
              v-loading="costLineageDrawer.loading"
              :data="costLineageRows"
              class="users-table"
              row-key="id"
              empty-text="暂无流向记录"
              @row-click="openCostLineageDetail"
            >
              <el-table-column label="成本项/来源" min-width="260" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.item_name || '-' }}</strong>
                    <small>{{ costSourceLabel(row.source) }} · {{ row.origin?.quote_job_id || row.origin?.created_by_username || '-' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="96">
                <template #default="{ row }">
                  <el-tag :type="costStatusTag(row.status)" effect="plain">{{ costStatusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="unit" label="单位" width="70" />
              <el-table-column label="价格" width="120">
                <template #default="{ row }">{{ formatPrice(row.price) }}</template>
              </el-table-column>
              <el-table-column label="引用" width="90">
                <template #default="{ row }">{{ row.quote_usage?.count || 0 }}</template>
              </el-table-column>
              <el-table-column label="去向" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">{{ row.destination?.status_text || '-' }}</template>
              </el-table-column>
              <el-table-column label="更新时间" min-width="150">
                <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
              </el-table-column>
            </el-table>
            <el-pagination
              v-if="costLineageTotal > costLineagePageSize"
              v-model:current-page="costLineagePage"
              :page-size="costLineagePageSize"
              :total="costLineageTotal"
              layout="total, prev, pager, next"
              small
              @current-change="loadCostLineageRows"
            />
          </div>
          <aside class="lineage-detail">
            <div v-if="costLineageDrawer.detailLoading" class="center-state">
              <el-icon class="spin"><Refresh /></el-icon>
              <span>加载中</span>
            </div>
            <template v-else-if="costLineageDrawer.detail">
              <div class="drawer-user">{{ costLineageDrawer.detail.item_name }}</div>
              <div class="detail-grid">
                <div>
                  <small>来源</small>
                  <strong>{{ costSourceLabel(costLineageDrawer.detail.source) }}</strong>
                </div>
                <div>
                  <small>当前状态</small>
                  <strong>{{ costStatusLabel(costLineageDrawer.detail.status) }}</strong>
                </div>
                <div>
                  <small>来源报价任务</small>
                  <strong>{{ costLineageDrawer.detail.origin?.quote_job_id || '-' }}</strong>
                </div>
                <div>
                  <small>来源历史 ID</small>
                  <strong>{{ costLineageDrawer.detail.origin?.quote_history_id || '-' }}</strong>
                </div>
                <div>
                  <small>来源行号</small>
                  <strong>{{ costLineageDrawer.detail.origin?.line_no || '-' }}</strong>
                </div>
                <div>
                  <small>价格动作</small>
                  <strong>{{ costLineageDrawer.detail.origin?.price_confirmation_label || costPriceActionLabel(costLineageDrawer.detail.origin?.manual_price_action) }}</strong>
                </div>
                <div>
                  <small>报价引用次数</small>
                  <strong>{{ costLineageDrawer.detail.quote_usage?.count || 0 }}</strong>
                </div>
              </div>
              <section class="drawer-section">
                <div class="section-title">
                  <el-icon><TrendCharts /></el-icon>
                  <span>当前去向</span>
                </div>
                <p class="detail-text">{{ costLineageDrawer.detail.destination?.status_text || '-' }}</p>
                <p class="detail-text">{{ costLineageDrawer.detail.destination?.rag_sync_note || '-' }}</p>
              </section>
              <section class="drawer-section">
                <div class="section-title">
                  <el-icon><Clock /></el-icon>
                  <span>生命周期</span>
                </div>
                <el-timeline>
                  <el-timeline-item
                    v-for="event in visibleCostHistory(costLineageDrawer.detail.history)"
                    :key="event.id"
                    :timestamp="formatDate(event.changed_at)"
                    placement="top"
                  >
                    <div class="event-row">
                      <strong>{{ costHistoryTypeLabel(event.change_type) }}</strong>
                      <el-tag size="small" effect="plain">{{ event.changed_by_username || '-' }}</el-tag>
                    </div>
                    <p>{{ costHistoryText(event) }}</p>
                    <small>{{ event.change_reason || '-' }}</small>
                  </el-timeline-item>
                </el-timeline>
                <el-empty v-if="!visibleCostHistory(costLineageDrawer.detail.history).length" description="暂无生命周期记录" />
              </section>
              <section class="drawer-section">
                <div class="section-title">
                  <el-icon><Document /></el-icon>
                  <span>报价引用</span>
                </div>
                <div v-for="usage in costLineageDrawer.detail.quote_usages" :key="usage.id" class="lineage-usage-item">
                  <strong>{{ usage.project_name || '-' }}</strong>
                  <span>{{ usage.quote_job_id || '-' }} · 历史 #{{ usage.quote_history_id || '-' }}</span>
                  <span>参考 {{ formatPrice(usage.reference_price) }}，最终 {{ formatPrice(usage.final_unit_price) }}</span>
                  <small>{{ formatDate(usage.confirmed_at || usage.created_at) }}</small>
                </div>
                <el-empty v-if="!costLineageDrawer.detail.quote_usages?.length" description="暂无报价引用" />
              </section>
            </template>
            <el-empty v-else description="点击左侧条目查看来源和去向" />
          </aside>
        </div>
      </template>
    </el-drawer>

    <el-dialog v-model="costRagSyncDialog.visible" title="RAG 同步记录" width="920px">
      <div class="dialog-toolbar">
        <span>记录每次 active 成本库同步到 RAG 的时间、数量和结果</span>
        <el-button :icon="Refresh" plain :loading="costRagSyncDialog.loading" @click="loadCostRagSyncRuns">
          刷新
        </el-button>
      </div>
      <el-table
        v-loading="costRagSyncDialog.loading"
        :data="costRagSyncRuns"
        class="users-table"
        empty-text="暂无同步记录"
      >
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="costRagSyncStatusTag(row.status)" effect="light">
              {{ costRagSyncStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="开始时间（北京时间）" min-width="180">
          <template #default="{ row }">{{ formatShanghaiDate(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="结束时间（北京时间）" min-width="180">
          <template #default="{ row }">{{ formatShanghaiDate(row.finished_at) }}</template>
        </el-table-column>
        <el-table-column label="数量" width="120">
          <template #default="{ row }">{{ row.synced_count || 0 }} / {{ row.requested_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ formatMs(row.duration_ms) }}</template>
        </el-table-column>
        <el-table-column prop="triggered_by_username" label="操作人" width="110" />
        <el-table-column label="结果" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.error || row.message || '-' }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="costRagSyncTotal > costRagSyncPageSize"
        v-model:current-page="costRagSyncPage"
        :page-size="costRagSyncPageSize"
        :total="costRagSyncTotal"
        layout="total, prev, pager, next"
        small
        @current-change="loadCostRagSyncRuns"
      ></el-pagination>
    </el-dialog>

    <el-dialog v-model="costAuditDialog.visible" title="成本库审计记录" width="1120px">
      <div class="cost-db-filters">
        <el-select
          v-model="costAuditFilters.action"
          size="small"
          clearable
          placeholder="动作"
          @change="applyCostAuditFilters"
        >
          <el-option
            v-for="option in costAuditActionOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-input
          v-model="costAuditFilters.username"
          size="small"
          clearable
          placeholder="用户"
          @keyup.enter="applyCostAuditFilters"
          @clear="applyCostAuditFilters"
        />
        <el-input
          v-model="costAuditFilters.resource_id"
          size="small"
          clearable
          placeholder="条目ID"
          @keyup.enter="applyCostAuditFilters"
          @clear="applyCostAuditFilters"
        />
        <el-select
          v-model="costAuditFilters.status"
          size="small"
          clearable
          placeholder="结果"
          @change="applyCostAuditFilters"
        >
          <el-option label="成功" value="success" />
          <el-option label="失败" value="failed" />
        </el-select>
        <el-button size="small" :icon="Refresh" plain :loading="costAuditDialog.loading" @click="loadCostAuditLogs">
          刷新
        </el-button>
      </div>
      <el-table
        v-loading="costAuditDialog.loading"
        :data="costAuditLogs"
        class="users-table"
        empty-text="暂无审计记录"
      >
        <el-table-column label="时间（北京时间）" min-width="170">
          <template #default="{ row }">{{ formatShanghaiDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="动作" min-width="160">
          <template #default="{ row }">{{ costAuditActionLabel(row.action) }}</template>
        </el-table-column>
        <el-table-column prop="username" label="用户" width="120" />
        <el-table-column label="对象" min-width="140">
          <template #default="{ row }">{{ row.resource_type }} #{{ row.resource_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : 'danger'" effect="light">
              {{ row.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="result_count" label="数量" width="90" />
        <el-table-column prop="client_ip" label="IP" width="130" />
        <el-table-column label="说明" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.message || costAuditFilterSummary(row.filters) || '-' }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="costAuditTotal > costAuditPageSize"
        v-model:current-page="costAuditPage"
        :page-size="costAuditPageSize"
        :total="costAuditTotal"
        layout="total, prev, pager, next"
        small
        @current-change="loadCostAuditLogs"
      ></el-pagination>
    </el-dialog>

    <el-dialog v-model="createUserDialog.visible" title="新建用户" width="460px">
      <el-form label-position="top" :model="createUserDialog">
        <el-form-item label="账号">
          <el-input v-model="createUserDialog.username" maxlength="64" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="createUserDialog.password" type="password" show-password maxlength="128" />
        </el-form-item>
        <el-form-item label="初始额度">
          <el-input-number v-model="createUserDialog.quota" :min="0" :max="9999" />
        </el-form-item>
        <el-form-item label="初始角色">
          <el-select v-model="createUserDialog.roles" class="full-width" multiple>
            <el-option
              v-for="role in roleOptions"
              :key="role.value"
              :label="role.label"
              :value="role.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="createUserDialog.note" type="textarea" :rows="3" maxlength="120" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createUserDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createUser">创建</el-button>
      </template>
    </el-dialog>

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

    <el-drawer v-model="quoteJobDrawer.visible" size="640px" title="报价任务详情">
      <div v-if="quoteJobDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="quoteJobDrawer.job">
        <div class="detail-grid">
          <div>
            <small>任务号</small>
            <strong>{{ quoteJobDrawer.job.job_id }}</strong>
          </div>
          <div>
            <small>状态</small>
            <strong>{{ statusLabel(quoteJobDrawer.job.status) }}</strong>
          </div>
          <div>
            <small>客户</small>
            <strong>{{ quoteJobDrawer.job.client_inquiry?.client_name || '-' }}</strong>
          </div>
          <div>
            <small>联系电话</small>
            <strong>{{ quoteJobDrawer.job.client_inquiry?.client_phone || '-' }}</strong>
          </div>
          <div>
            <small>需求来源</small>
            <strong>{{ quoteJobDrawer.job.client_inquiry?.source || '-' }}</strong>
          </div>
          <div>
            <small>咨询时间</small>
            <strong>{{ formatDate(quoteJobDrawer.job.client_inquiry?.inquiry_time) }}</strong>
          </div>
          <div>
            <small>提交人</small>
            <strong>{{ quoteJobDrawer.job.username }}</strong>
          </div>
          <div>
            <small>钉钉推送</small>
            <strong>{{ pushStatusLabel(quoteJobDrawer.job.history) }}</strong>
          </div>
        </div>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>需求摘要</span>
          </div>
          <p class="detail-text">{{ quoteJobDrawer.job.request_summary || quoteJobDrawer.job.message_preview || '-' }}</p>
        </section>
        <section class="drawer-section" v-if="quoteJobDrawer.reviewDetail">
          <div class="section-title">
            <el-icon><DataAnalysis /></el-icon>
            <span>预审条目与风险检查</span>
            <small>
              确认 {{ quoteJobDrawer.reviewDetail.summary?.requirement_row_count || 0 }} 行 /
              预审 {{ quoteJobDrawer.reviewDetail.summary?.preview_row_count || 0 }} 行
            </small>
          </div>
          <el-alert
            v-if="quoteJobDrawer.reviewDetail.summary?.integrity_status === 'incomplete'"
            class="dashboard-alert"
            :title="quoteJobDrawer.reviewDetail.summary?.message || 'AI 预审不完整，存在确认清单行未匹配到预审报价'"
            type="error"
            show-icon
            :closable="false"
          />
          <el-alert
            v-if="quoteJobDrawer.reviewDetail.summary?.placeholder_count"
            class="dashboard-alert"
            :title="`AI 未返回 ${quoteJobDrawer.reviewDetail.summary.placeholder_count} 行确认清单，系统已生成占位行，需人工补价后再下发。`"
            type="error"
            show-icon
            :closable="false"
          />
          <div class="review-summary-grid">
            <div>
              <small>疑似未报价</small>
              <strong>{{ quoteJobDrawer.reviewDetail.summary?.missing_count || 0 }}</strong>
            </div>
            <div>
              <small>无底价参考</small>
              <strong>{{ quoteJobDrawer.reviewDetail.summary?.no_cost_reference_count || 0 }}</strong>
            </div>
            <div>
              <small>高风险</small>
              <strong>{{ quoteJobDrawer.reviewDetail.summary?.high_risk_count || 0 }}</strong>
            </div>
            <div>
              <small>需复核</small>
              <strong>{{ quoteJobDrawer.reviewDetail.summary?.review_required_count || 0 }}</strong>
            </div>
          </div>
          <el-table
            :data="quoteJobDrawer.reviewDetail.preview_rows || []"
            row-key="index"
            class="users-table"
            empty-text="暂无预审条目"
          >
            <el-table-column label="风险" width="96">
              <template #default="{ row }">
                <el-tag :type="row.risk?.type || 'info'" effect="plain">{{ row.risk?.label || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="施工项目" min-width="170" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ row.project_name || '-' }}</strong>
                  <small>{{ row.quantity ?? '-' }} {{ row.unit || '' }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="AI单价/合计" width="140">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.ai_unit_price) }}</strong>
                  <small>合计 {{ formatPrice(row.system_total_price) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="风险原因" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">{{ (row.risk?.reasons || []).join('、') || '无' }}</template>
            </el-table-column>
            <el-table-column label="检查项" min-width="280">
              <template #default="{ row }">
                <div class="review-check-tags">
                  <el-tag
                    v-for="check in reviewCheckItems(row)"
                    :key="check.key"
                    :type="reviewCheckTagType(check)"
                    effect="plain"
                    size="small"
                  >
                    {{ check.label }}
                  </el-tag>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </section>
        <section class="drawer-section" v-if="quoteJobDrawer.reviewDetail?.missing_requirement_rows?.length">
          <div class="section-title">
            <el-icon><Warning /></el-icon>
            <span>确认清单对账</span>
            <small>{{ quoteJobDrawer.reviewDetail.missing_requirement_rows.length }} 行疑似未进入预审单</small>
          </div>
          <el-table
            :data="quoteJobDrawer.reviewDetail.missing_requirement_rows"
            row-key="requirement_index"
            class="users-table"
            empty-text="暂无疑似漏报价行"
          >
            <el-table-column label="来源" width="150">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ row.requirement_row?.source_sheet || '-' }}</strong>
                  <small>第 {{ row.requirement_row?.raw_row_index || '-' }} 行</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="确认项目" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.requirement_row?.item_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="数量/单位" width="120">
              <template #default="{ row }">{{ row.requirement_row?.quantity ?? '-' }}{{ row.requirement_row?.unit || '' }}</template>
            </el-table-column>
            <el-table-column label="原始行" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">{{ row.requirement_row?.raw_text || '-' }}</template>
            </el-table-column>
          </el-table>
        </section>
        <section
          class="drawer-section"
          v-if="quoteJobDrawer.job.feedback?.rejected || quoteJobDrawer.job.feedback?.rejection_reason"
        >
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>预审打回</span>
          </div>
          <div class="detail-grid">
            <div>
              <small>打回人</small>
              <strong>{{ quoteJobDrawer.job.feedback?.reviewed_by || quoteJobDrawer.job.username || '-' }}</strong>
            </div>
            <div>
              <small>打回时间</small>
              <strong>{{ formatDate(quoteJobDrawer.job.feedback?.rejected_at) }}</strong>
            </div>
          </div>
          <p class="detail-text">
            {{ quoteJobDrawer.job.feedback?.rejection_reason || quoteJobDrawer.job.feedback?.change_summary || '-' }}
          </p>
        </section>
        <section class="drawer-section" v-if="quoteJobDrawer.costEvidence?.length">
          <div class="section-title">
            <el-icon><DataAnalysis /></el-icon>
            <span>成本证据</span>
          </div>
          <el-table
            :data="quoteJobDrawer.costEvidence"
            row-key="id"
            class="users-table"
            empty-text="暂无成本证据"
          >
            <el-table-column label="施工项目" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ row.project_name || '-' }}</strong>
                  <small>{{ row.quantity ?? '-' }} {{ row.unit || '' }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="AI 单价" width="96">
              <template #default="{ row }">{{ formatPrice(row.ai_unit_price) }}</template>
            </el-table-column>
            <el-table-column label="行合计" width="130">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.line_total_price ?? row.ai_total_price) }}</strong>
                  <small>{{ row.line_total_source_label || totalSourceLabel(row.line_total_source) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="成本参考" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.reference_price) }}</strong>
                  <small>参考合计 {{ formatPrice(row.reference_total) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="整单合计" width="150">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.quote_total_price) }}</strong>
                  <small>{{ row.quote_total_source_label || totalSourceLabel(row.quote_total_source) }}</small>
                  <small>参考合计 {{ formatPrice(row.quote_reference_total_price) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="偏差" width="110">
              <template #default="{ row }">
                <span>{{ formatPrice(row.price_delta) }}</span>
                <small class="muted-inline">{{ formatRate(row.price_delta_rate) }}</small>
              </template>
            </el-table-column>
            <el-table-column label="AI来源" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ row.ai_price_source_label || '-' }}</strong>
                  <small>{{ row.ai_price_source_reason || '-' }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="依据" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">{{ row.comparison || row.match_reason || '-' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-button
                  size="small"
                  :icon="Document"
                  plain
                  :disabled="!row.cost_item_id"
                  @click="openCostEvidenceItem(row)"
                >
                  成本条目
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>
        <section class="drawer-section" v-if="quoteJobDrawer.job.error_message">
          <div class="section-title">
            <el-icon><Clock /></el-icon>
            <span>异常信息</span>
          </div>
          <p class="detail-text">{{ quoteJobDrawer.job.error_message }}</p>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Histogram /></el-icon>
            <span>进度事件</span>
          </div>
          <el-timeline>
            <el-timeline-item
              v-for="event in quoteJobDrawer.job.events || []"
              :key="`${event.event_index || event.status}-${event.created_at || event.message}`"
              :timestamp="formatDate(event.created_at)"
              placement="top"
            >
              <div class="event-row">
                <strong>{{ event.stage || event.status || event.event_type }}</strong>
                <el-tag size="small" effect="plain">{{ event.event_type || event.status }}</el-tag>
              </div>
              <p>{{ event.message || '-' }}</p>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-if="!quoteJobDrawer.job.events?.length" description="暂无进度事件" />
        </section>
      </template>
    </el-drawer>

    <el-dialog v-model="meetingDialog.visible" title="录入会议纪要" width="680px">
      <el-form label-position="top" :model="meetingDialog.form">
        <el-form-item label="会议纪要">
          <el-input
            v-model="meetingDialog.form.content"
            type="textarea"
            :rows="10"
            maxlength="10000"
            show-word-limit
            placeholder="粘贴手动整理后的会议纪要，系统会生成待确认任务草稿"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="meetingDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createMeetingNote">生成草稿</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="manualDraftDialog.visible" title="人工补充任务草稿" width="520px">
      <el-form label-position="top" :model="manualDraftDialog.form">
        <el-form-item label="任务标题">
          <el-input v-model="manualDraftDialog.form.title" maxlength="120" show-word-limit />
        </el-form-item>
        <el-form-item label="负责人">
          <el-select v-model="manualDraftDialog.form.assignee_id" class="full-width" filterable>
            <el-option
              v-for="user in executionAssigneeOptions"
              :key="user.id"
              :label="user.username"
              :value="user.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker
            v-model="manualDraftDialog.form.due_at"
            class="full-width"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            format="YYYY-MM-DD HH:mm"
          />
        </el-form-item>
        <el-form-item label="依据">
          <el-input v-model="manualDraftDialog.form.source_sentence" maxlength="500" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="manualDraftDialog.form.notes" type="textarea" :rows="3" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="manualDraftDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="addManualDraft">保存草稿</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="executionDialog.visible" title="新建执行任务" width="520px">
      <el-form label-position="top" :model="executionDialog.form">
        <el-form-item label="任务标题">
          <el-input v-model="executionDialog.form.title" maxlength="120" show-word-limit />
        </el-form-item>
        <el-form-item label="负责人">
          <el-select v-model="executionDialog.form.assignee_id" class="full-width" filterable>
            <el-option
              v-for="user in executionAssigneeOptions"
              :key="user.id"
              :label="user.username"
              :value="user.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker
            v-model="executionDialog.form.due_at"
            class="full-width"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            format="YYYY-MM-DD HH:mm"
          />
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="executionDialog.form.source" class="full-width">
            <el-option
              v-for="option in executionSourceOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="来源编号">
          <el-input v-model="executionDialog.form.source_ref_id" maxlength="64" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="executionDialog.form.notes" type="textarea" :rows="3" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="executionDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createExecutionTask">创建任务</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="executionDrawer.visible" size="620px" title="执行任务详情">
      <div v-if="executionDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="executionDrawer.task">
        <div class="detail-grid">
          <div>
            <small>任务</small>
            <strong>{{ executionDrawer.task.title }}</strong>
          </div>
          <div>
            <small>状态</small>
            <strong>{{ executionStatusLabel(executionDrawer.task.status) }}</strong>
          </div>
          <div>
            <small>负责人</small>
            <strong>{{ executionDrawer.task.assignee_username || '-' }}</strong>
          </div>
          <div>
            <small>截止时间</small>
            <strong>{{ formatDate(executionDrawer.task.due_at) }}</strong>
          </div>
          <div>
            <small>完成时间</small>
            <strong>{{ formatDate(executionDrawer.task.completed_at) }}</strong>
          </div>
          <div>
            <small>来源</small>
            <strong>{{ executionSourceLabel(executionDrawer.task.source) }}</strong>
          </div>
        </div>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>备注</span>
          </div>
          <p class="detail-text">{{ executionDrawer.task.notes || '-' }}</p>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Clock /></el-icon>
            <span>事件记录</span>
          </div>
          <el-timeline>
            <el-timeline-item
              v-for="event in executionDrawer.task.events || []"
              :key="event.id"
              :timestamp="formatDate(event.created_at)"
              placement="top"
            >
              <div class="event-row">
                <strong>{{ event.event_type }}</strong>
                <el-tag size="small" effect="plain">{{ event.from_status || '-' }} -> {{ event.to_status || '-' }}</el-tag>
              </div>
              <p>{{ event.reason || '无备注' }}</p>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-if="!executionDrawer.task.events?.length" description="暂无事件" />
        </section>
      </template>
    </el-drawer>

    <el-drawer v-model="meetingDrawer.visible" size="760px" title="会议纪要详情">
      <div v-if="meetingDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="meetingDrawer.note">
        <div class="detail-grid">
          <div>
            <small>状态</small>
            <strong>{{ meetingStatusLabel(meetingDrawer.note.status) }}</strong>
          </div>
          <div>
            <small>提取状态</small>
            <strong>{{ meetingAiStatusLabel(meetingDrawer.note.ai_status) }}</strong>
          </div>
          <div>
            <small>录入人</small>
            <strong>{{ meetingDrawer.note.created_by_username || '-' }}</strong>
          </div>
          <div>
            <small>确认时间</small>
            <strong>{{ formatDate(meetingDrawer.note.confirmed_at) }}</strong>
          </div>
        </div>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Tickets /></el-icon>
            <span>会议纪要</span>
          </div>
          <p class="detail-text">{{ meetingDrawer.note.content || '-' }}</p>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>任务草稿</span>
            <small>{{ meetingDrawer.note.pending_draft_count || 0 }} 条待确认</small>
            <el-button size="small" type="primary" plain @click="openManualDraft">人工补充</el-button>
          </div>
          <div class="draft-list">
            <div
              v-for="draft in meetingDrawer.note.drafts || []"
              :key="draft.id"
              class="draft-item"
            >
              <div class="draft-source">
                <el-tag size="small" :type="draftStatusTag(draft.status)" effect="plain">
                  {{ draftStatusLabel(draft.status) }}
                </el-tag>
                <span>{{ draft.source_sentence }}</span>
              </div>
              <div v-if="draft.status === 'pending_review'" class="draft-confirm-grid">
                <el-input v-model="draft.confirm_title" size="small" maxlength="120" />
                <el-select v-model="draft.confirm_assignee_id" size="small" filterable placeholder="负责人">
                  <el-option
                    v-for="user in executionAssigneeOptions"
                    :key="user.id"
                    :label="user.username"
                    :value="user.id"
                  />
                </el-select>
                <el-date-picker
                  v-model="draft.confirm_due_at"
                  size="small"
                  type="datetime"
                  value-format="YYYY-MM-DDTHH:mm:ss"
                  format="YYYY-MM-DD HH:mm"
                  placeholder="截止时间"
                />
                <el-input v-model="draft.confirm_notes" size="small" placeholder="确认备注" />
                <div class="row-actions">
                  <el-button size="small" type="primary" plain @click="confirmDraft(draft)">确认</el-button>
                  <el-button size="small" type="danger" plain @click="rejectDraft(draft)">驳回</el-button>
                </div>
              </div>
              <div v-else class="draft-result">
                <span>负责人：{{ draft.confirmed_assignee_username || draft.suggested_assignee_username || '-' }}</span>
                <span>截止：{{ formatDate(draft.confirmed_due_at || draft.suggested_due_at) }}</span>
                <span v-if="draft.accepted_task_id">任务 #{{ draft.accepted_task_id }}</span>
                <span v-if="draft.rejection_reason">原因：{{ draft.rejection_reason }}</span>
              </div>
            </div>
            <el-empty v-if="!meetingDrawer.note.drafts?.length" description="暂无任务草稿，可人工补充" />
          </div>
        </section>
      </template>
    </el-drawer>

    <el-drawer v-model="requirementHistoryDrawer.visible" size="760px" title="历史解析记录">
      <div class="history-toolbar">
        <el-button :icon="Refresh" plain :loading="requirementHistoryDrawer.loading" @click="loadRequirementHistoryRecords">
          刷新
        </el-button>
        <span class="filter-count">本地浏览器历史，最多保留 {{ REQUIREMENT_HISTORY_RECORD_LIMIT }} 条</span>
      </div>
      <el-table
        :data="requirementHistoryRecords"
        row-key="id"
        class="users-table"
        empty-text="暂无历史解析记录"
      >
        <el-table-column label="文件" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="operation-client">
              <strong>{{ row.file_name || '-' }}</strong>
              <small>{{ requirementHistoryStatusLabel(row.status) }} · {{ formatLocalDate(row.updated_at) }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="180">
          <template #default="{ row }">
            <div class="operation-client">
              <strong>{{ row.selected_row_count || 0 }} / {{ row.standard_row_count || 0 }} 行已选</strong>
              <small>确认 {{ row.confirmed_row_count || 0 }} 行 · Sheet {{ row.sheet_count || 0 }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="version_count" label="版本" width="80" />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" type="primary" plain @click="restoreRequirementRecord(row)">继续</el-button>
              <el-button size="small" plain @click="openRequirementVersions(row)">版本</el-button>
              <el-button size="small" type="danger" plain @click="deleteRequirementRecord(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <el-drawer v-model="requirementVersionDrawer.visible" size="720px" :title="`版本回滚 - ${requirementVersionDrawer.record?.file_name || ''}`">
      <div class="history-toolbar">
        <el-button :icon="Refresh" plain :loading="requirementVersionDrawer.loading" @click="loadRequirementVersions(requirementVersionDrawer.record?.id)">
          刷新
        </el-button>
        <span class="filter-count">回滚会生成一个新版本，原历史不会被覆盖</span>
      </div>
      <el-table
        :data="requirementVersions"
        row-key="id"
        class="users-table"
        empty-text="暂无版本"
      >
        <el-table-column label="版本" width="100">
          <template #default="{ row }">
            <strong>v{{ row.version_no }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="动作" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="operation-client">
              <strong>{{ row.action }}</strong>
              <small>{{ formatLocalDate(row.created_at) }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="摘要" min-width="190">
          <template #default="{ row }">
            <span>{{ requirementVersionSummary(row.snapshot) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="rollbackRequirementVersion(row)">回滚</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Clock,
  DataAnalysis,
  Delete,
  Document,
  Download,
  Histogram,
  Lock,
  Plus,
  Refresh,
  Search,
  Select,
  Setting,
  SwitchButton,
  Tickets,
  TrendCharts,
  User,
  Warning,
} from '@element-plus/icons-vue'

const TOKEN_KEY = 'ai_token'
const REQUIREMENT_HISTORY_DB_NAME = 'ai_requirement_standardization_history'
const REQUIREMENT_HISTORY_DB_VERSION = 1
const REQUIREMENT_HISTORY_RECORD_STORE = 'records'
const REQUIREMENT_HISTORY_VERSION_STORE = 'versions'
const REQUIREMENT_HISTORY_RECORD_LIMIT = 30
const REQUIREMENT_HISTORY_VERSION_LIMIT = 20
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
  { value: 'cost_viewer', label: 'cost_viewer', hint: '完整成本库只读' },
  { value: 'cost_editor', label: 'cost_editor', hint: '维护成本库 draft' },
  { value: 'cost_approver', label: 'cost_approver', hint: '启用/归档成本价' },
  { value: 'cost_exporter', label: 'cost_exporter', hint: '成本数据导出预留' },
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

const clientInquirySourceOptions = [
  { value: '', label: '全部来源' },
  { value: '系统提交', label: '系统提交' },
  { value: '微信', label: '微信' },
  { value: '电话', label: '电话' },
  { value: '钉钉', label: '钉钉' },
  { value: '门店', label: '门店' },
  { value: '其他', label: '其他' },
]

const quoteJobStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '处理中' },
  { value: 'succeeded', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'canceled', label: '已取消' },
  { value: 'timed_out', label: '已超时' },
  { value: 'failed,canceled,timed_out', label: '异常状态' },
]

const executionStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待处理' },
  { value: 'in_progress', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

const executionSourceOptions = [
  { value: 'manual', label: '手动创建' },
  { value: 'quote', label: '报价跟进' },
  { value: 'meeting', label: '会议纪要' },
]

const meetingStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'confirmed', label: '已确认' },
  { value: 'revised', label: '有更正' },
  { value: 'cancelled', label: '已作废' },
]

const businessLedgerStageOptions = [
  { value: '初步接触', label: '初步接触' },
  { value: '需求确认', label: '需求确认' },
  { value: '报价中', label: '报价中' },
  { value: '跟进议价', label: '跟进议价' },
  { value: '成单', label: '成单' },
  { value: '丢单', label: '丢单' },
]
const businessLedgerTerminalStages = new Set(['成单', '丢单'])

const costStatusOptions = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '启用' },
  { value: 'archived', label: '归档' },
]

const costPriceTypeOptions = [
  { value: 'labor', label: '人工' },
  { value: 'material', label: '材料' },
  { value: 'combined', label: '综合' },
]

const costSourceOptions = [
  { value: 'manual', label: '人工' },
  { value: 'imported', label: '导入' },
  { value: 'ai_suggested', label: 'AI 建议' },
]

const costAuditActionOptions = [
  { value: 'cost_item.list', label: '查看列表' },
  { value: 'cost_item.detail', label: '查看详情' },
  { value: 'cost_item.export', label: '导出' },
  { value: 'cost_item.create', label: '新建' },
  { value: 'cost_item.update', label: '编辑' },
  { value: 'cost_item.activate', label: '启用 active' },
  { value: 'cost_item.withdraw', label: '撤回启用' },
  { value: 'cost_item.archive', label: '归档' },
  { value: 'cost_item.bulk_status', label: '批量状态变更' },
  { value: 'cost_rag.sync', label: '同步 RAG' },
]

const requirementFieldOptions = [
  { value: 'ignore', label: '忽略' },
  { value: 'item_name', label: '项目名称' },
  { value: 'spec', label: '规格/特征' },
  { value: 'quantity', label: '数量' },
  { value: 'unit', label: '单位' },
  { value: 'remark', label: '备注' },
  { value: 'location', label: '区域/位置' },
  { value: 'price_ignored', label: '价格列（只读）' },
]

const requirementRowFilterOptions = [
  { value: 'all', label: '全部候选行' },
  { value: 'included', label: '已选行' },
  { value: 'excluded', label: '未选行' },
  { value: 'blocked', label: '未通过校验' },
  { value: 'requires_confirmation', label: '需人工确认' },
  { value: 'low_confidence', label: '低置信度' },
  { value: 'with_warnings', label: '有风险提示' },
  { value: 'multi_quantity', label: '多工程量候选' },
  { value: 'quantity_missing', label: '数量来源不明' },
]

const loginForm = reactive({ username: '', password: '' })
const session = reactive({ user: null })
const users = ref([])
const roleEvents = ref([])
const quoteDashboard = ref(null)
const responseDashboard = ref(null)
const executionDashboard = ref(null)
const clientInquiries = ref([])
const clientInquiryTotal = ref(0)
const clientInquiryPage = ref(1)
const clientInquiryPageSize = 20
const quoteJobs = ref([])
const quoteJobTotal = ref(0)
const quoteJobPage = ref(1)
const quoteJobPageSize = 15
const executionTasks = ref([])
const executionTaskTotal = ref(0)
const executionTaskPage = ref(1)
const executionTaskPageSize = 20
const meetings = ref([])
const meetingTotal = ref(0)
const meetingPage = ref(1)
const meetingPageSize = 20
const businessLedgers = ref([])
const businessLedgerTotal = ref(0)
const businessLedgerPage = ref(1)
const businessLedgerPageSize = 20
const businessLedgerLoading = ref(false)
const costItems = ref([])
const costItemTotal = ref(0)
const costItemPage = ref(1)
const costItemPageSize = 20
const costDbLoading = ref(false)
const costRagSyncing = ref(false)
const costBulkSubmitting = ref(false)
const costAllSelecting = ref(false)
const costSelectionSyncing = ref(false)
const costItemsTable = ref(null)
const selectedCostItems = ref([])
const costRagSyncRuns = ref([])
const costRagSyncTotal = ref(0)
const costRagSyncPage = ref(1)
const costRagSyncPageSize = 10
const costRagSyncStatus = ref(null)
const costRagSyncStatusLoading = ref(false)
const costAuditLogs = ref([])
const costAuditTotal = ref(0)
const costAuditPage = ref(1)
const costAuditPageSize = 10
const costLineageRows = ref([])
const costLineageTotal = ref(0)
const costLineagePage = ref(1)
const costLineagePageSize = 20
const costLineageSummary = ref({})
const requirementFile = ref(null)
const requirementPreview = ref(null)
const requirementRows = ref([])
const requirementActiveSheet = ref('')
const requirementActiveRowSheet = ref('')
const requirementActiveBlockedRowKey = ref('')
const requirementConfirmed = ref(null)
const requirementQuoteJob = ref(null)
const requirementLoading = ref(false)
const requirementConfirming = ref(false)
const requirementQuoting = ref(false)
const requirementFeatureDisabled = ref(false)
const requirementMappings = reactive({})
const requirementCurrentRecordId = ref('')
const requirementHistoryRecords = ref([])
const requirementVersions = ref([])
const requirementHistoryDrawer = reactive({
  visible: false,
  loading: false,
})
const requirementVersionDrawer = reactive({
  visible: false,
  loading: false,
  record: null,
})
const requirementRowFilters = reactive({
  keyword: '',
  status: 'all',
})
const clientInquiryFilters = reactive({
  source: '',
  keyword: '',
  hasQuoteJob: true,
})
const quoteJobFilters = reactive({
  status: '',
  source: '',
  keyword: '',
  username: '',
})
const executionTaskFilters = reactive({
  status: '',
  source: '',
  keyword: '',
})
const meetingFilters = reactive({
  status: '',
  keyword: '',
})
const businessLedgerFilters = reactive({
  stage: [],
  source: '',
  responder_id: null,
  dateRange: [],
  keyword: '',
  overdue_only: false,
})
const costItemFilters = reactive({
  category: '',
  status: [],
  price_type: '',
  source: '',
  keyword: '',
})
const costLineageFilters = reactive({
  source: '',
  keyword: '',
  has_quote_usage: '',
})
const dashboardRange = ref('last_30_days')
const dashboardTab = ref('quote')
const executionPageTab = ref('tasks')
const dashboardFeature = reactive({ quoteDisabled: false, responseDisabled: false, executionDisabled: false })
const executionFeatureDisabled = ref(false)
const meetingFeatureDisabled = ref(false)
const businessLedgerFeatureDisabled = ref(false)
const costDbFeatureDisabled = ref(false)
const state = reactive({ loading: false, submitting: false, error: '' })
const routeName = ref(routeFromPath(window.location.pathname))

const grantDialog = reactive({
  visible: false,
  user: null,
  role: 'staff',
  note: '',
})

const createUserDialog = reactive({
  visible: false,
  username: '',
  password: '',
  quota: 5,
  roles: ['staff'],
  note: '',
})

const eventsDrawer = reactive({
  visible: false,
  user: null,
  revokeRole: '',
  revokeNote: '',
})

const quoteJobDrawer = reactive({
  visible: false,
  loading: false,
  job: null,
  costEvidence: [],
  reviewDetail: null,
})

const executionDialog = reactive({
  visible: false,
  form: {
    title: '',
    assignee_id: null,
    due_at: '',
    source: 'manual',
    source_ref_id: '',
    notes: '',
  },
})

const meetingDialog = reactive({
  visible: false,
  form: {
    content: '',
  },
})

const manualDraftDialog = reactive({
  visible: false,
  form: {
    title: '',
    assignee_id: null,
    due_at: '',
    source_sentence: '',
    notes: '',
  },
})

const executionDrawer = reactive({
  visible: false,
  loading: false,
  task: null,
})

const meetingDrawer = reactive({
  visible: false,
  loading: false,
  note: null,
})

const businessLedgerDialog = reactive({
  visible: false,
  mode: 'create',
  inquiryId: '',
  form: {
    source: '',
    client_name: '',
    client_phone: '',
    stage: '初步接触',
    next_followup_at: '',
    responder_id: null,
    notes: '',
  },
})

const businessLedgerDrawer = reactive({
  visible: false,
  loading: false,
  ledger: null,
})

const costItemDialog = reactive({
  visible: false,
  mode: 'create',
  itemId: null,
  form: {
    category: '',
    subcategory: '',
    item_name: '',
    spec: '',
    unit: '',
    price: '',
    client_tax_excluded_price: '',
    client_labor_price: '',
    client_main_material_price: '',
    client_auxiliary_material_price: '',
    client_direct_fee: '',
    client_management_profit: '',
    subcontract_composite_price: '',
    subcontract_labor_price: '',
    subcontract_main_material_price: '',
    subcontract_auxiliary_material_price: '',
    crew_benchmark_price: '',
    price_type: 'combined',
    effective_date: '',
    notes: '',
    change_reason: '',
  },
})

const costImportDialog = reactive({
  visible: false,
  file: null,
  preview: null,
  loading: false,
})

const costItemDrawer = reactive({
  visible: false,
  loading: false,
  item: null,
})

const costLineageDrawer = reactive({
  visible: false,
  loading: false,
  summaryLoading: false,
  detailLoading: false,
  activeTab: 'summary',
  detail: null,
})

const costRagSyncDialog = reactive({
  visible: false,
  loading: false,
})

const costAuditDialog = reactive({
  visible: false,
  loading: false,
})

const costAuditFilters = reactive({
  action: '',
  username: '',
  resource_id: '',
  status: '',
})

const roles = computed(() => session.user?.roles || [])
const hasRole = (...roleNames) => roles.value.some((role) => roleNames.includes(role))
const canMutateRoles = computed(() => roles.value.includes('system_admin'))
const canAccessPermissions = computed(() => roles.value.includes('system_admin') || roles.value.includes('admin'))
const canViewDashboard = computed(() => canAccessPermissions.value || roles.value.includes('viewer'))
const canViewQuoteOperations = computed(() => canAccessPermissions.value)
const canViewExecution = computed(() => canAccessPermissions.value || roles.value.includes('staff') || roles.value.includes('manager'))
const canCreateExecutionTask = computed(() => canAccessPermissions.value)
const canCreateMeetingNote = computed(() => canViewExecution.value)
const canViewBusinessLedger = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canManageBusinessLedger = computed(() => canAccessPermissions.value)
const canViewCostDb = computed(() => canAccessPermissions.value || hasRole('cost_viewer', 'cost_editor', 'cost_approver', 'cost_exporter'))
const canEditCostDb = computed(() => canAccessPermissions.value || hasRole('cost_editor', 'cost_approver'))
const canApproveCostDb = computed(() => canAccessPermissions.value || roles.value.includes('cost_approver'))
const canExportCostDb = computed(() => canAccessPermissions.value || roles.value.includes('cost_exporter'))
const canViewCostAudit = computed(() => canAccessPermissions.value || roles.value.includes('cost_approver'))
const canViewRequirementStandardization = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canOpenLegacyQuote = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canOpenLegacyAdmin = computed(() => canAccessPermissions.value)
const selectedCostItemIds = computed(() => selectedCostItems.value.map((item) => item.id).filter(Boolean))
const selectableCostItems = computed(() => costItems.value.filter((item) => costItemSelectable(item)))
const selectedDraftCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'draft').length)
const selectedActiveCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'active').length)
const selectedArchivableCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'draft' || item.status === 'active').length)
const visibleDailyTrends = computed(() => (quoteDashboard.value?.daily_trends || []).filter((item) => item.sample_count > 0).slice(-12))
const visibleResponseSources = computed(() => (responseDashboard.value?.by_source || []).slice(0, 12))
const visibleResponseResponders = computed(() => (responseDashboard.value?.by_responder || []).slice(0, 12))
const visibleExecutionTrends = computed(() => (executionDashboard.value?.daily_trends || []).filter((item) => item.task_count > 0).slice(-12))
const visibleExecutionAssignees = computed(() => (executionDashboard.value?.by_assignee || []).slice(0, 12))
const requirementSheetMappings = computed(() => requirementPreview.value?.sheet_mappings || [])
const requirementSummary = computed(() => requirementPreview.value?.summary || {})
const selectedRequirementRows = computed(() => requirementRows.value.filter((row) => row.include))
const visibleRequirementRows = computed(() => (
  requirementRows.value.filter((row) => row.row_type === 'data_row' || row.include || row.confirmed)
))
const requirementBlockedRows = computed(() => {
  const blockedRows = requirementConfirmed.value?.blocked_rows || []
  if (!blockedRows.length) return []
  const currentRowsByKey = new Map()
  for (const row of requirementRows.value) {
    for (const key of requirementLookupKeys(row)) {
      if (!currentRowsByKey.has(key)) currentRowsByKey.set(key, row)
    }
  }
  return blockedRows.map((blockedRow, index) => {
    const currentRow = requirementLookupKeys(blockedRow)
      .map((key) => currentRowsByKey.get(key))
      .find(Boolean)
    const blockedKey = requirementPrimaryLookupKey(blockedRow) || requirementPrimaryLookupKey(currentRow) || `blocked:${index}`
    return {
      ...blockedRow,
      blocked_row_key: blockedKey,
      requirement_row_key: blockedRow.requirement_row_key || currentRow?.requirement_row_key || blockedKey,
      source_sheet: currentRow?.source_sheet ?? blockedRow.source_sheet,
      raw_row_index: currentRow?.raw_row_index ?? blockedRow.raw_row_index,
      item_name: currentRow?.item_name ?? blockedRow.item_name,
      spec: currentRow?.spec ?? blockedRow.spec,
      quantity: currentRow?.quantity ?? blockedRow.quantity,
      unit: currentRow?.unit ?? blockedRow.unit,
      remark: currentRow?.remark ?? blockedRow.remark,
      raw_text: currentRow?.raw_text ?? blockedRow.raw_text,
      raw_cells: currentRow?.raw_cells ?? blockedRow.raw_cells ?? [],
      errors: blockedRow.errors || [],
      error_messages: blockedRow.error_messages || [],
      error_summary: blockedRow.error_summary || '',
      warnings: blockedRow.warnings || currentRow?.warnings || [],
    }
  })
})
const requirementBlockedRowKeySet = computed(() => {
  const keys = new Set()
  for (const row of requirementBlockedRows.value) {
    for (const key of requirementLookupKeys(row)) keys.add(key)
    if (row.blocked_row_key) keys.add(row.blocked_row_key)
  }
  return keys
})
const filteredRequirementRows = computed(() => visibleRequirementRows.value.filter((row) => requirementRowMatchesFilters(row)))
const hiddenRequirementRowCount = computed(() => Math.max(0, requirementRows.value.length - visibleRequirementRows.value.length))
const visibleRequirementRowSheets = computed(() => {
  const sheetNames = new Set(filteredRequirementRows.value.map((row) => row.source_sheet).filter(Boolean))
  const fromMappings = requirementSheetMappings.value.filter((sheet) => sheetNames.has(sheet.sheet_name))
  if (fromMappings.length) return fromMappings
  return Array.from(sheetNames).map((sheetName) => ({ sheet_name: sheetName }))
})
const visibleRequirementSheetMappings = computed(() => {
  const filtered = requirementSheetMappings.value.filter((sheet) => {
    const sheetRows = requirementRows.value.filter((row) => row.source_sheet === sheet.sheet_name)
    const hasDataRows = sheetRows.some((row) => row.row_type === 'data_row')
    const mappedFields = Object.values(sheet.field_mapping || {})
    const hasCoreMapping = mappedFields.some((field) => ['item_name', 'spec', 'quantity', 'unit'].includes(field))
    return hasDataRows || hasCoreMapping
  })
  return filtered.length ? filtered : requirementSheetMappings.value
})
const hiddenRequirementSheetCount = computed(() => (
  Math.max(0, requirementSheetMappings.value.length - visibleRequirementSheetMappings.value.length)
))
const executionAssigneeOptions = computed(() => {
  const source = users.value.length ? users.value : (session.user ? [session.user] : [])
  return source.filter((user) => {
    if (user.is_active === false) return false
    if (!user.roles?.length) return user.id === session.user?.id
    return user.roles.some((role) => ['system_admin', 'admin', 'staff', 'manager'].includes(role))
  })
})
const businessLedgerResponderOptions = computed(() => {
  const source = users.value.length ? users.value : (session.user ? [session.user] : [])
  return source.filter((user) => user.is_active !== false)
})
const businessLedgerDialogTitle = computed(() => (
  businessLedgerDialog.mode === 'edit' ? '编辑商务台账' : '新建商务台账'
))
const costItemDialogTitle = computed(() => (
  costItemDialog.mode === 'edit' ? '编辑成本条目' : '新建成本条目'
))

function routeFromPath(path) {
  if (path === '/login') return 'login'
  if (path === '/admin/dashboard') return 'dashboard'
  if (path === '/admin/execution') return 'execution'
  if (path === '/admin/business-ledger') return 'businessLedger'
  if (path === '/admin/cost-db') return 'costDb'
  if (path === '/admin/requirement-standardization') return 'requirementStandardization'
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
  if (role?.startsWith('cost_')) return 'warning'
  if (role === 'staff') return 'success'
  if (role === 'manager') return 'primary'
  return 'info'
}

function formatDate(value) {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 19)
}

function formatShanghaiDate(value) {
  if (!value) return '-'
  const text = String(value)
  const utcText = /(?:Z|[+-]\d{2}:\d{2})$/.test(text) ? text : `${text}Z`
  const date = new Date(utcText)
  if (Number.isNaN(date.getTime())) return formatDate(value)
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`
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

function formatMinutes(value) {
  if (value === null || value === undefined) return '-'
  if (value < 60) return `${value.toFixed(1)} 分钟`
  return `${(value / 60).toFixed(1)} 小时`
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

function jobStatusTag(status) {
  if (status === 'succeeded') return 'success'
  if (status === 'failed' || status === 'timed_out') return 'danger'
  if (status === 'canceled') return 'info'
  if (status === 'running') return 'warning'
  return 'primary'
}

function executionStatusLabel(status) {
  const labels = {
    pending: '待处理',
    in_progress: '进行中',
    done: '已完成',
    cancelled: '已取消',
  }
  return labels[status] || status
}

function executionSourceLabel(source) {
  const option = executionSourceOptions.find((item) => item.value === source)
  return option?.label || source || '-'
}

function executionStatusTag(status) {
  if (status === 'done') return 'success'
  if (status === 'cancelled') return 'info'
  if (status === 'in_progress') return 'warning'
  return 'primary'
}

function meetingStatusLabel(status) {
  const labels = {
    draft: '草稿',
    confirmed: '已确认',
    revised: '有更正',
    cancelled: '已作废',
  }
  return labels[status] || status
}

function meetingStatusTag(status) {
  if (status === 'confirmed') return 'success'
  if (status === 'cancelled') return 'info'
  if (status === 'revised') return 'warning'
  return 'primary'
}

function meetingAiStatusLabel(status) {
  const labels = {
    pending: '待提取',
    extracted: '已生成草稿',
    no_tasks: '无明确任务',
    failed: '提取失败',
  }
  return labels[status] || status || '-'
}

function draftStatusLabel(status) {
  const labels = {
    pending_review: '待确认',
    accepted: '已确认',
    rejected: '已驳回',
  }
  return labels[status] || status
}

function draftStatusTag(status) {
  if (status === 'accepted') return 'success'
  if (status === 'rejected') return 'info'
  return 'warning'
}

function meetingPreview(row) {
  const content = row.content || row.extraction_error || `会议纪要 #${row.id}`
  return content.length > 48 ? `${content.slice(0, 48)}...` : content
}

function toDatePickerValue(value) {
  if (!value) return ''
  return value.replace(' ', 'T').slice(0, 19)
}

function toTimestamp(value) {
  if (!value) return null
  const parsed = new Date(String(value).replace(' ', 'T'))
  const time = parsed.getTime()
  return Number.isNaN(time) ? null : time
}

function formatAmount(value) {
  if (value === null || value === undefined) return '-'
  return Number(value).toLocaleString('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 0,
  })
}

function formatPrice(value) {
  if (value === null || value === undefined || value === '') return '-'
  return Number(value).toLocaleString('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function costStatusLabel(status) {
  const option = costStatusOptions.find((item) => item.value === status)
  return option?.label || status || '-'
}

function costStatusTag(status) {
  if (status === 'active') return 'success'
  if (status === 'archived') return 'info'
  return 'warning'
}

function costPriceTypeLabel(type) {
  const option = costPriceTypeOptions.find((item) => item.value === type)
  return option?.label || type || '-'
}

function costSourceLabel(source) {
  const option = costSourceOptions.find((item) => item.value === source)
  return option?.label || source || '-'
}

function costAuditActionLabel(action) {
  const option = costAuditActionOptions.find((item) => item.value === action)
  return option?.label || action || '-'
}

function costAuditFilterSummary(filters) {
  if (!filters) return ''
  const parts = []
  if (filters.status) parts.push(`状态:${filters.status}`)
  if (filters.source) parts.push(`来源:${filters.source}`)
  if (filters.keyword) parts.push(`关键词:${filters.keyword}`)
  if (filters.exported_count !== undefined) parts.push(`导出:${filters.exported_count}`)
  if (filters.target_status) parts.push(`目标:${filters.target_status}`)
  return parts.join('，')
}

function costPriceActionLabel(action) {
  const labels = {
    manual_override: '人工改价',
    manual_existing: '人工确认价',
    accepted_ai_suggestion: '人工采纳 AI 建议',
    untouched: '默认确认',
  }
  return labels[action] || action || '-'
}

function costRagSyncStatusLabel(status) {
  const labels = {
    running: '同步中',
    success: '成功',
    failed: '失败',
  }
  return labels[status] || status || '-'
}

function costRagSyncStatusTag(status) {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function costRagSyncSummaryLabel(status) {
  const labels = {
    synced: '已同步',
    stale: '需同步',
    failed: '同步失败',
    never_synced: '未同步',
    empty_active: '无 active 条目',
  }
  return labels[status] || status || '-'
}

function costRagSyncSummaryAlertType(status) {
  if (status === 'synced') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'stale' || status === 'never_synced') return 'warning'
  return 'info'
}

function costHistoryTypeLabel(type) {
  if (type === 'price_change') return '价格变更'
  if (type === 'status_change') return '状态变更'
  return type || '-'
}

const costHistoryPriceFields = [
  { key: 'price', label: '主参考' },
  { key: 'client_tax_excluded_price', label: '对甲' },
  { key: 'client_labor_price', label: '对甲人工' },
  { key: 'client_main_material_price', label: '对甲主材' },
  { key: 'client_auxiliary_material_price', label: '对甲辅材' },
  { key: 'client_direct_fee', label: '对甲直接费' },
  { key: 'client_management_profit', label: '对甲管理费利润' },
  { key: 'subcontract_composite_price', label: '劳务' },
  { key: 'subcontract_labor_price', label: '劳务人工' },
  { key: 'subcontract_main_material_price', label: '劳务主材' },
  { key: 'subcontract_auxiliary_material_price', label: '劳务辅材' },
  { key: 'crew_benchmark_price', label: '班组' },
]

function sameCostHistoryValue(left, right) {
  if ((left === null || left === undefined || left === '') && (right === null || right === undefined || right === '')) return true
  const leftNumber = Number(left)
  const rightNumber = Number(right)
  if (!Number.isNaN(leftNumber) && !Number.isNaN(rightNumber)) {
    return Math.round(leftNumber * 1000000) === Math.round(rightNumber * 1000000)
  }
  return left === right
}

function costHistoryChangedFields(event) {
  if (Array.isArray(event.changed_fields) && event.changed_fields.length) {
    return costHistoryPriceFields.filter((field) => event.changed_fields.includes(field.key))
  }
  return costHistoryPriceFields.filter(
    (field) => !sameCostHistoryValue(event[`old_${field.key}`], event[`new_${field.key}`]),
  )
}

function visibleCostHistory(history = []) {
  return history.filter((event) => event.change_type !== 'price_change' || costHistoryChangedFields(event).length > 0)
}

function costHistoryText(event) {
  if (event.change_type === 'status_change') {
    return `${costStatusLabel(event.old_status)} -> ${costStatusLabel(event.new_status)}`
  }
  const changedFields = costHistoryChangedFields(event)
  if (!changedFields.length) return '无有效价格变化'
  return changedFields
    .map((field) => `${field.label} ${formatPrice(event[`old_${field.key}`])} -> ${formatPrice(event[`new_${field.key}`])}`)
    .join('；')
}

function totalSourceLabel(source) {
  const labels = {
    ai_quote: 'AI报价计算',
    cost_reference_fallback: '成本库兜底计算',
    manual_final: '人工确认价',
    mixed: '混合来源',
  }
  return labels[source] || '-'
}

function parseCostNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isNaN(parsed) ? null : parsed
}

function pushStatusLabel(history) {
  if (!history) return '未确认'
  return history.pushed_to_dingtalk ? '已推送钉钉' : '已确认未推送'
}

function reviewCheckItems(row) {
  const items = Object.entries(row?.checks || {}).map(([key, value]) => ({
    key,
    ...(value || {}),
  }))
  if (row?.requirement_placeholder && !items.some((item) => item.key === 'ai_returned_requirement_row')) {
    items.unshift({
      key: 'ai_returned_requirement_row',
      passed: false,
      skipped: false,
      severity: 'danger',
      label: 'AI 未返回该确认行，需人工补价',
    })
  }
  return items
}

function reviewCheckTagType(check) {
  if (check?.skipped) return 'info'
  if (check?.passed) return 'success'
  return check?.severity === 'danger' ? 'danger' : 'warning'
}

function canRetryQuoteJob(row) {
  return ['failed', 'canceled', 'timed_out'].includes(row.status)
}

function canCancelQuoteJob(row) {
  return ['queued', 'running'].includes(row.status)
}

function businessStageTag(stage) {
  if (stage === '成单') return 'success'
  if (stage === '丢单') return 'danger'
  if (stage === '报价中' || stage === '跟进议价') return 'warning'
  return 'primary'
}

function isBusinessTerminal(stage) {
  return businessLedgerTerminalStages.has(stage)
}

function isBusinessLedgerOverdue(row) {
  const followupTime = toTimestamp(row.next_followup_at)
  if (!followupTime || row.cancelled_at || isBusinessTerminal(row.stage)) return false
  return followupTime < Date.now()
}

function businessLedgerRowClass({ row }) {
  return isBusinessLedgerOverdue(row) ? 'ledger-overdue-row' : ''
}

function businessLedgerPreview(row) {
  const content = row.notes || row.source || row.inquiry_id || ''
  return content.length > 42 ? `${content.slice(0, 42)}...` : content || '-'
}

function canEditBusinessLedger(row) {
  if (!row || row.cancelled_at || isBusinessTerminal(row.stage)) return false
  if (canManageBusinessLedger.value) return true
  return row.responder_id === session.user?.id
}

function canCancelBusinessLedger(row) {
  return canManageBusinessLedger.value && row && !row.cancelled_at && !isBusinessTerminal(row.stage)
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

function isFeatureDisabled(error) {
  return error.response?.data?.detail === 'FEATURE_DISABLED'
}

function cloneRequirementState(value) {
  return value === undefined ? null : JSON.parse(JSON.stringify(value))
}

function requirementNewId(prefix = 'req') {
  if (window.crypto?.randomUUID) return `${prefix}_${window.crypto.randomUUID()}`
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`
}

function openRequirementHistoryDb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error('当前浏览器不支持 IndexedDB，无法保存本地历史'))
      return
    }
    const request = window.indexedDB.open(REQUIREMENT_HISTORY_DB_NAME, REQUIREMENT_HISTORY_DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(REQUIREMENT_HISTORY_RECORD_STORE)) {
        db.createObjectStore(REQUIREMENT_HISTORY_RECORD_STORE, { keyPath: 'id' })
      }
      if (!db.objectStoreNames.contains(REQUIREMENT_HISTORY_VERSION_STORE)) {
        const store = db.createObjectStore(REQUIREMENT_HISTORY_VERSION_STORE, { keyPath: 'id' })
        store.createIndex('record_id', 'record_id', { unique: false })
      }
    }
    request.onerror = () => reject(request.error)
    request.onsuccess = () => resolve(request.result)
  })
}

function requirementStoreRequest(storeName, mode, operation) {
  return openRequirementHistoryDb().then((db) => new Promise((resolve, reject) => {
    const transaction = db.transaction(storeName, mode)
    const store = transaction.objectStore(storeName)
    const request = operation(store)
    request.onerror = () => reject(request.error)
    request.onsuccess = () => resolve(request.result)
    transaction.oncomplete = () => db.close()
    transaction.onerror = () => {
      db.close()
      reject(transaction.error)
    }
  }))
}

function requirementGetAllRecords() {
  return requirementStoreRequest(REQUIREMENT_HISTORY_RECORD_STORE, 'readonly', (store) => store.getAll())
}

function requirementGetRecord(recordId) {
  return requirementStoreRequest(REQUIREMENT_HISTORY_RECORD_STORE, 'readonly', (store) => store.get(recordId))
}

function requirementPutRecord(record) {
  return requirementStoreRequest(REQUIREMENT_HISTORY_RECORD_STORE, 'readwrite', (store) => store.put(record))
}

function requirementDeleteRecordStoreItem(recordId) {
  return requirementStoreRequest(REQUIREMENT_HISTORY_RECORD_STORE, 'readwrite', (store) => store.delete(recordId))
}

function requirementPutVersion(version) {
  return requirementStoreRequest(REQUIREMENT_HISTORY_VERSION_STORE, 'readwrite', (store) => store.put(version))
}

function requirementGetAllVersions() {
  return requirementStoreRequest(REQUIREMENT_HISTORY_VERSION_STORE, 'readonly', (store) => store.getAll())
}

function requirementDeleteVersionStoreItem(versionId) {
  return requirementStoreRequest(REQUIREMENT_HISTORY_VERSION_STORE, 'readwrite', (store) => store.delete(versionId))
}

function buildRequirementSnapshot() {
  return {
    requirementPreview: cloneRequirementState(requirementPreview.value),
    requirementRows: cloneRequirementState(requirementRows.value),
    requirementMappings: cloneRequirementState(requirementMappings),
    requirementConfirmed: cloneRequirementState(requirementConfirmed.value),
    requirementQuoteJob: cloneRequirementState(requirementQuoteJob.value),
    requirementActiveSheet: requirementActiveSheet.value,
    requirementActiveRowSheet: requirementActiveRowSheet.value,
    requirementActiveBlockedRowKey: requirementActiveBlockedRowKey.value,
    requirementRowFilters: cloneRequirementState(requirementRowFilters),
  }
}

function restoreRequirementSnapshot(snapshot) {
  requirementPreview.value = cloneRequirementState(snapshot?.requirementPreview)
  requirementRows.value = (cloneRequirementState(snapshot?.requirementRows) || []).map((row, index) => ({
    ...row,
    requirement_row_key: row.requirement_row_key || `${row.source_sheet || 'sheet'}:${row.raw_row_index || index}:${index}`,
    quantity_source_key: row.quantity_source_key || requirementInitialQuantitySourceKey(row),
  }))
  Object.keys(requirementMappings).forEach((key) => delete requirementMappings[key])
  Object.entries(snapshot?.requirementMappings || {}).forEach(([sheetName, mapping]) => {
    requirementMappings[sheetName] = { ...(mapping || {}) }
  })
  requirementConfirmed.value = cloneRequirementState(snapshot?.requirementConfirmed)
  requirementQuoteJob.value = cloneRequirementState(snapshot?.requirementQuoteJob)
  requirementActiveSheet.value = snapshot?.requirementActiveSheet || visibleRequirementSheetMappings.value[0]?.sheet_name || ''
  requirementActiveRowSheet.value = snapshot?.requirementActiveRowSheet || visibleRequirementRowSheets.value[0]?.sheet_name || requirementActiveSheet.value
  requirementActiveBlockedRowKey.value = snapshot?.requirementActiveBlockedRowKey || ''
  requirementRowFilters.keyword = snapshot?.requirementRowFilters?.keyword || ''
  requirementRowFilters.status = snapshot?.requirementRowFilters?.status || 'all'
}

function requirementProgressStatus() {
  if (requirementQuoteJob.value?.job_id) return 'quoted'
  if (requirementConfirmed.value?.summary?.confirmed_row_count) return 'confirmed'
  if (requirementPreview.value) return selectedRequirementRows.value.length ? 'reviewing' : 'parsed'
  return 'empty'
}

function requirementHistoryStatusLabel(status) {
  return {
    parsed: '已解析',
    reviewing: '确认中',
    confirmed: '已生成确认清单',
    quoted: '已发起报价',
  }[status] || '未知'
}

function formatLocalDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

function requirementSnapshotSummary(snapshot) {
  const rows = snapshot?.requirementRows || []
  const confirmed = snapshot?.requirementConfirmed?.summary?.confirmed_row_count || 0
  return {
    sheet_count: snapshot?.requirementPreview?.summary?.sheet_count || 0,
    standard_row_count: snapshot?.requirementPreview?.summary?.standard_row_count || 0,
    selected_row_count: rows.filter((row) => row.include).length,
    confirmed_row_count: confirmed,
  }
}

function requirementVersionSummary(snapshot) {
  const summary = requirementSnapshotSummary(snapshot)
  return `已选 ${summary.selected_row_count}/${summary.standard_row_count}，确认 ${summary.confirmed_row_count}`
}

async function loadRequirementHistoryRecords() {
  requirementHistoryDrawer.loading = true
  try {
    const records = await requirementGetAllRecords()
    requirementHistoryRecords.value = (records || []).sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
  } catch (error) {
    ElMessage.error(error.message || '历史记录读取失败')
  } finally {
    requirementHistoryDrawer.loading = false
  }
}

async function loadRequirementVersions(recordId) {
  if (!recordId) return
  requirementVersionDrawer.loading = true
  try {
    const versions = await requirementGetAllVersions()
    requirementVersions.value = (versions || [])
      .filter((item) => item.record_id === recordId)
      .sort((a, b) => (b.version_no || 0) - (a.version_no || 0))
  } catch (error) {
    ElMessage.error(error.message || '版本记录读取失败')
  } finally {
    requirementVersionDrawer.loading = false
  }
}

async function saveRequirementProgress(action, options = {}) {
  if (!requirementPreview.value) {
    if (!options.quiet) ElMessage.warning('暂无可保存的解析进度')
    return null
  }
  try {
    const now = new Date().toISOString()
    const recordId = requirementCurrentRecordId.value || requirementNewId('req_record')
    const existingRecord = await requirementGetRecord(recordId)
    const allVersions = await requirementGetAllVersions()
    const versions = (allVersions || []).filter((item) => item.record_id === recordId)
    const versionNo = versions.reduce((max, item) => Math.max(max, item.version_no || 0), 0) + 1
    const snapshot = buildRequirementSnapshot()
    const summary = requirementSnapshotSummary(snapshot)
    const version = {
      id: requirementNewId('req_version'),
      record_id: recordId,
      version_no: versionNo,
      action,
      created_at: now,
      snapshot,
    }
    const record = {
      ...(existingRecord || {}),
      id: recordId,
      file_name: requirementPreview.value?.source?.file_name || existingRecord?.file_name || 'uploaded.xlsx',
      created_at: existingRecord?.created_at || now,
      updated_at: now,
      active_version_id: version.id,
      version_count: versionNo,
      status: requirementProgressStatus(),
      ...summary,
    }
    await requirementPutVersion(version)
    await requirementPutRecord(record)
    requirementCurrentRecordId.value = recordId
    await pruneRequirementHistory()
    if (requirementHistoryDrawer.visible) await loadRequirementHistoryRecords()
    if (requirementVersionDrawer.visible && requirementVersionDrawer.record?.id === recordId) {
      requirementVersionDrawer.record = record
      await loadRequirementVersions(recordId)
    }
    if (!options.quiet) ElMessage.success('进度已保存')
    return record
  } catch (error) {
    if (!options.quiet) ElMessage.error(error.message || '进度保存失败')
    return null
  }
}

async function pruneRequirementHistory() {
  const [records, versions] = await Promise.all([requirementGetAllRecords(), requirementGetAllVersions()])
  const sortedRecords = (records || []).sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
  for (const record of sortedRecords.slice(REQUIREMENT_HISTORY_RECORD_LIMIT)) {
    await requirementDeleteRecordWithVersions(record.id)
  }
  const remainingRecords = sortedRecords.slice(0, REQUIREMENT_HISTORY_RECORD_LIMIT)
  for (const record of remainingRecords) {
    const recordVersions = (versions || [])
      .filter((item) => item.record_id === record.id)
      .sort((a, b) => (b.version_no || 0) - (a.version_no || 0))
    for (const oldVersion of recordVersions.slice(REQUIREMENT_HISTORY_VERSION_LIMIT)) {
      await requirementDeleteVersionStoreItem(oldVersion.id)
    }
  }
}

async function requirementDeleteRecordWithVersions(recordId) {
  const versions = await requirementGetAllVersions()
  for (const version of (versions || []).filter((item) => item.record_id === recordId)) {
    await requirementDeleteVersionStoreItem(version.id)
  }
  await requirementDeleteRecordStoreItem(recordId)
}

async function openRequirementHistory() {
  requirementHistoryDrawer.visible = true
  await loadRequirementHistoryRecords()
}

async function openRequirementVersions(record) {
  requirementVersionDrawer.record = record
  requirementVersionDrawer.visible = true
  await loadRequirementVersions(record.id)
}

async function restoreRequirementRecord(record) {
  try {
    const versions = await requirementGetAllVersions()
    const activeVersion = (versions || []).find((item) => item.id === record.active_version_id)
      || (versions || []).filter((item) => item.record_id === record.id).sort((a, b) => (b.version_no || 0) - (a.version_no || 0))[0]
    if (!activeVersion) {
      ElMessage.warning('该记录没有可恢复版本')
      return
    }
    restoreRequirementSnapshot(activeVersion.snapshot)
    requirementCurrentRecordId.value = record.id
    requirementHistoryDrawer.visible = false
    ElMessage.success('已恢复历史进度')
  } catch (error) {
    ElMessage.error(error.message || '历史进度恢复失败')
  }
}

async function rollbackRequirementVersion(version) {
  try {
    await ElMessageBox.confirm(`确认回滚到 v${version.version_no}？当前状态会作为新的回滚版本保存。`, '版本回滚', {
      confirmButtonText: '确认回滚',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  restoreRequirementSnapshot(version.snapshot)
  requirementCurrentRecordId.value = version.record_id
  const record = await saveRequirementProgress(`回滚到版本 ${version.version_no}`, { quiet: true })
  if (record) requirementVersionDrawer.record = record
  await loadRequirementVersions(version.record_id)
  await loadRequirementHistoryRecords()
  ElMessage.success(`已回滚到 v${version.version_no}`)
}

async function deleteRequirementRecord(record) {
  try {
    await ElMessageBox.confirm(`确认删除“${record.file_name || '该解析记录'}”及其全部版本？`, '删除历史解析记录', {
      confirmButtonText: '确认删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await requirementDeleteRecordWithVersions(record.id)
    if (requirementCurrentRecordId.value === record.id) requirementCurrentRecordId.value = ''
    if (requirementVersionDrawer.record?.id === record.id) {
      requirementVersionDrawer.visible = false
      requirementVersionDrawer.record = null
      requirementVersions.value = []
    }
    await loadRequirementHistoryRecords()
    ElMessage.success('历史记录已删除')
  } catch (error) {
    ElMessage.error(error.message || '历史记录删除失败')
  }
}

function handleRequirementFileChange(file) {
  requirementFile.value = file.raw || file
  requirementConfirmed.value = null
  requirementQuoteJob.value = null
  requirementActiveBlockedRowKey.value = ''
  requirementCurrentRecordId.value = ''
}

function clearRequirementFile() {
  requirementFile.value = null
}

function resetRequirementStandardization() {
  requirementFile.value = null
  requirementPreview.value = null
  requirementRows.value = []
  requirementActiveSheet.value = ''
  requirementActiveRowSheet.value = ''
  requirementActiveBlockedRowKey.value = ''
  requirementConfirmed.value = null
  requirementQuoteJob.value = null
  requirementRowFilters.keyword = ''
  requirementRowFilters.status = 'all'
  requirementCurrentRecordId.value = ''
  Object.keys(requirementMappings).forEach((key) => delete requirementMappings[key])
}

function defaultRequirementInclude(row) {
  return row.row_type === 'data_row' && row.confidence !== 'low'
}

function hydrateRequirementPreview(preview) {
  requirementPreview.value = preview
  requirementRowFilters.keyword = ''
  requirementRowFilters.status = 'all'
  requirementRows.value = (preview.rows || []).map((row, index) => ({
    ...row,
    requirement_row_key: `${row.source_sheet || 'sheet'}:${row.raw_row_index || index}:${index}`,
    quantity_source_key: requirementInitialQuantitySourceKey(row),
    include: defaultRequirementInclude(row),
    confirmed: false,
  }))
  Object.keys(requirementMappings).forEach((key) => delete requirementMappings[key])
  for (const sheet of preview.sheet_mappings || []) {
    requirementMappings[sheet.sheet_name] = { ...(sheet.field_mapping || {}) }
    for (const column of sheet.columns || []) {
      if (!requirementMappings[sheet.sheet_name][column.column]) {
        requirementMappings[sheet.sheet_name][column.column] = column.detected_field || 'ignore'
      }
    }
  }
  requirementActiveSheet.value = visibleRequirementSheetMappings.value[0]?.sheet_name || preview.sheet_mappings?.[0]?.sheet_name || ''
  requirementActiveRowSheet.value = visibleRequirementRowSheets.value[0]?.sheet_name || requirementActiveSheet.value
  requirementActiveBlockedRowKey.value = ''
  requirementConfirmed.value = null
  requirementQuoteJob.value = null
}

function requirementPayloadMappings() {
  return requirementSheetMappings.value.map((sheet) => ({
    sheet_name: sheet.sheet_name,
    field_mapping: requirementMappings[sheet.sheet_name] || {},
  }))
}

function requirementConfidenceType(confidence) {
  if (confidence === 'high') return 'success'
  if (confidence === 'medium') return 'warning'
  return 'danger'
}

function requirementInitialQuantitySourceKey(row) {
  if (row.quantity_source?.key) return row.quantity_source.key
  const selected = (row.quantity_candidates || []).find((candidate) => candidate.selected)
  return selected?.key || (row.quantity_candidates?.length ? row.quantity_candidates[0].key : 'manual')
}

function applyRequirementQuantitySource(row, key) {
  if (key === 'manual') {
    row.quantity_source = { key: 'manual', label: '手工填写', method: 'manual' }
    return
  }
  const candidate = (row.quantity_candidates || []).find((item) => item.key === key)
  if (!candidate) return
  row.quantity = candidate.quantity
  row.quantity_source = { ...candidate, selected: true, method: 'manual_candidate' }
}

function requirementQuantityCandidateLabel(candidate) {
  const label = candidate.label && candidate.label !== candidate.column ? ` ${candidate.label}` : ''
  const group = candidate.group_label && candidate.group_label !== candidate.label ? ` / ${candidate.group_label}` : ''
  return `${candidate.column}${label}${group}: ${candidate.raw_value}`
}

function requirementQuantitySourceText(row) {
  if (row.quantity_source?.key === 'manual') return '手工填写'
  if (row.quantity_source?.column) return requirementQuantityCandidateLabel(row.quantity_source)
  if (row.quantity_source?.label) return row.quantity_source.label
  return row.quantity === null || row.quantity === undefined || row.quantity === '' ? '未识别' : '行内识别'
}

function requirementQuantityCandidatesText(row) {
  const candidates = row.quantity_candidates || []
  if (!candidates.length) return '无其他工程量候选'
  return candidates.map((candidate) => requirementQuantityCandidateLabel(candidate)).join(' / ')
}

function requirementLookupKeys(row) {
  if (!row) return []
  const keys = []
  if (row.requirement_row_key) keys.push(String(row.requirement_row_key))
  if (row.source_sheet && row.raw_row_index !== undefined && row.raw_row_index !== null) {
    keys.push(`${row.source_sheet}:${row.raw_row_index}`)
  }
  return Array.from(new Set(keys))
}

function requirementPrimaryLookupKey(row) {
  return requirementLookupKeys(row)[0] || ''
}

function requirementRowIsBlocked(row) {
  return requirementLookupKeys(row).some((key) => requirementBlockedRowKeySet.value.has(key))
}

function requirementValidationErrorLabel(code) {
  return {
    CONFIRMATION_REQUIRED: '需要勾选人工确认',
    MISSING_ITEM_NAME: '缺少项目名称，不能自动进入报价',
    MISSING_QUANTITY: '缺少数量，不能自动进入报价',
    INVALID_QUANTITY: '数量为 0、负数或非法文本，不能自动进入报价',
    RANGE_QUANTITY: '数量为范围值，需要人工确认',
    APPROXIMATE_QUANTITY: '数量为约数，需要人工确认',
    MISSING_UNIT: '缺少单位，需要人工确认',
    MULTIPLE_NUMBERS: '同一行存在多个数字，需要确认哪个是工程量',
    MULTIPLE_QUANTITY_CANDIDATES: '同一行存在多个工程量候选，需确认采用哪一个数量',
    PRICE_COLUMN_PRESENT: '原表包含价格列，系统不会采用该价格',
    LOW_CONFIDENCE: '低置信度，必须人工确认',
    AMBIGUOUS_HEADER: '表头不清晰，需要人工确认列映射',
    NOT_DATA_ROW: '该行不是有效清单数据行，可能是说明、汇总或空白行',
  }[code] || code || '未通过校验'
}

function requirementValidationErrorText(errors) {
  const labels = (errors || []).map((code) => requirementValidationErrorLabel(code))
  return labels.join('、') || '未通过校验'
}

function requirementValidationMessages(row) {
  if (row?.error_messages?.length) return row.error_messages
  const messages = []
  for (const code of row?.errors || []) {
    if (code === 'CONFIRMATION_REQUIRED') {
      messages.push(...requirementConfirmationRiskMessages(row))
    } else {
      messages.push(requirementValidationErrorLabel(code))
    }
  }
  return Array.from(new Set(messages.filter(Boolean))).length
    ? Array.from(new Set(messages.filter(Boolean)))
    : ['未通过确认校验']
}

function requirementConfirmationRiskMessages(row) {
  const warningCodes = new Set((row?.warnings || []).filter((code) => code && code !== 'MULTI_SHEET_DETECTED'))
  const riskCodes = [
    'PRICE_COLUMN_PRESENT',
    'LOW_CONFIDENCE',
    'MULTIPLE_QUANTITY_CANDIDATES',
    'MULTIPLE_NUMBERS',
    'RANGE_QUANTITY',
    'APPROXIMATE_QUANTITY',
    'AMBIGUOUS_HEADER',
    'MISSING_ITEM_NAME',
    'MISSING_QUANTITY',
    'INVALID_QUANTITY',
    'MISSING_UNIT',
  ].filter((code) => warningCodes.has(code))
  if (row?.confidence === 'low' && !warningCodes.has('LOW_CONFIDENCE')) {
    riskCodes.push('LOW_CONFIDENCE')
  }
  return riskCodes.length
    ? riskCodes.map((code) => requirementValidationErrorLabel(code))
    : ['该行被标记为需人工确认，请核对项目名称、数量、单位、风险提示和原始行内容']
}

function requirementRowClassName({ row }) {
  const classes = []
  if (requirementRowIsBlocked(row)) classes.push('requirement-row-blocked')
  if (requirementActiveBlockedRowKey.value && requirementLookupKeys(row).includes(requirementActiveBlockedRowKey.value)) {
    classes.push('requirement-row-focused')
  }
  return classes.join(' ')
}

async function locateRequirementBlockedRow(row) {
  const key = requirementPrimaryLookupKey(row)
  requirementActiveBlockedRowKey.value = key
  requirementRowFilters.status = 'blocked'
  requirementActiveRowSheet.value = row.source_sheet || requirementActiveRowSheet.value
  await nextTick()
  const focusedRow = document.querySelector('.requirement-row-focused')
  focusedRow?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

async function focusFirstRequirementBlockedRow() {
  const first = requirementBlockedRows.value[0]
  if (!first) return
  await locateRequirementBlockedRow(first)
}

function markRequirementConfirmationDirty() {
  requirementConfirmed.value = null
  requirementQuoteJob.value = null
  requirementActiveBlockedRowKey.value = ''
}

function bulkIncludeRequirementRows(include) {
  for (const row of filteredRequirementRows.value) {
    row.include = include
    if (!include) row.confirmed = false
  }
  markRequirementConfirmationDirty()
  ElMessage.success(include ? '已全选当前筛选结果' : '已取消选择当前筛选结果')
}

function bulkConfirmRequirementRows(confirmed) {
  for (const row of filteredRequirementRows.value) {
    if (confirmed) row.include = true
    row.confirmed = confirmed
  }
  markRequirementConfirmationDirty()
  ElMessage.success(confirmed ? '已批量确认当前筛选结果' : '已批量撤回当前筛选结果的确认')
}

function requirementRowMatchesFilters(row) {
  const keyword = requirementRowFilters.keyword.trim().toLowerCase()
  if (keyword && !requirementRowSearchText(row).includes(keyword)) return false
  if (requirementRowFilters.status === 'included') return Boolean(row.include)
  if (requirementRowFilters.status === 'excluded') return !row.include
  if (requirementRowFilters.status === 'blocked') return requirementRowIsBlocked(row)
  if (requirementRowFilters.status === 'requires_confirmation') return Boolean(row.requires_confirmation)
  if (requirementRowFilters.status === 'low_confidence') return row.confidence === 'low'
  if (requirementRowFilters.status === 'with_warnings') return Boolean(row.warnings?.length)
  if (requirementRowFilters.status === 'multi_quantity') return (row.quantity_candidates || []).length > 1
  if (requirementRowFilters.status === 'quantity_missing') {
    return row.quantity === null || row.quantity === undefined || row.quantity === '' || !row.quantity_source?.key
  }
  return true
}

function requirementRowSearchText(row) {
  const rawCells = (row.raw_cells || []).map((cell) => `${cell.column} ${cell.value}`).join(' ')
  const quantityCandidates = (row.quantity_candidates || [])
    .map((candidate) => `${candidate.column} ${candidate.label} ${candidate.group_label} ${candidate.raw_value} ${candidate.quantity}`)
    .join(' ')
  const blockedRow = requirementBlockedRows.value.find((item) => (
    requirementLookupKeys(row).some((key) => requirementLookupKeys(item).includes(key))
  ))
  return [
    row.source_sheet,
    row.raw_row_index,
    row.row_type,
    row.item_name,
    row.spec,
    row.quantity,
    row.unit,
    row.remark,
    row.location,
    row.work_area,
    row.raw_text,
    rawCells,
    row.quantity_source?.column,
    row.quantity_source?.label,
    row.quantity_source?.group_label,
    row.quantity_source?.raw_value,
    quantityCandidates,
    ...(row.warnings || []),
    ...(blockedRow?.errors || []).map((code) => requirementValidationErrorLabel(code)),
  ].filter((value) => value !== undefined && value !== null).join(' ').toLowerCase()
}

async function focusFirstRequirementMatch() {
  const first = filteredRequirementRows.value[0]
  if (!first) {
    ElMessage.warning('没有匹配的行')
    return
  }
  requirementActiveBlockedRowKey.value = requirementPrimaryLookupKey(first)
  requirementActiveRowSheet.value = first.source_sheet || requirementActiveRowSheet.value
  await nextTick()
  const focusedRow = document.querySelector('.requirement-row-focused')
  focusedRow?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

function visibleRequirementRowsForSheet(sheetName) {
  return filteredRequirementRows.value.filter((row) => row.source_sheet === sheetName)
}

function hiddenRequirementRowCountForSheet(sheetName) {
  const total = requirementRows.value.filter((row) => row.source_sheet === sheetName).length
  const candidateCount = visibleRequirementRows.value.filter((row) => row.source_sheet === sheetName).length
  return Math.max(0, total - candidateCount)
}

function requirementRawCellsText(row) {
  const cells = (row.raw_cells || [])
    .filter((cell) => visibleRequirementRawCell(cell.value))
    .map((cell) => `${cell.column}: ${cell.value}`)
  return cells.join(' / ') || row.raw_text || '-'
}

function visibleRequirementRawCell(value) {
  const text = String(value || '').trim()
  return Boolean(text && !['-', '--', '—', '–', '/', '#REF!'].includes(text.toUpperCase()))
}

function visibleRequirementColumns(sheet) {
  return (sheet.columns || []).filter((column) => {
    if (column.detected_field === 'price_ignored') return false
    if (ignoredRequirementColumnLabel(column.label)) return false
    if (column.detected_field && column.detected_field !== 'ignore') return true
    if ((column.sample_values || []).some((value) => meaningfulRequirementCell(value))) return true
    return column.label && column.label !== column.column && meaningfulRequirementCell(column.label)
  })
}

function ignoredRequirementColumnLabel(value) {
  const text = String(value || '').trim()
  if (!text) return false
  if (/^[一二三四五六七八九十0-9]+[-—–~至到]+[一二三四五六七八九十0-9]+楼$/.test(text)) return true
  return ['人工费', '管理费', '主材费', '辅材费', '材料费', '机械费', '利润', '税金', '规费'].some((term) => text.includes(term))
}

function meaningfulRequirementCell(value) {
  const text = String(value || '').trim()
  if (!text || ['-', '--', '—', '–', '/', '#REF!'].includes(text.toUpperCase())) return false
  if (/^[A-Z]{1,3}$/.test(text)) return false
  if (/^\d+(\.\d+)?$/.test(text)) return false
  return true
}

async function previewRequirementStandardization() {
  if (!requirementFile.value) {
    ElMessage.warning('请先选择 .xlsx/.xlsm 需求单')
    return
  }
  requirementLoading.value = true
  requirementFeatureDisabled.value = false
  try {
    const form = new FormData()
    form.append('file', requirementFile.value)
    const response = await api.post('/admin/requirement-standardization/preview', form)
    hydrateRequirementPreview(responseData(response))
    await saveRequirementProgress('解析预览', { quiet: true })
    ElMessage.success('已生成标准化预览')
  } catch (error) {
    if (isFeatureDisabled(error)) {
      requirementFeatureDisabled.value = true
      return
    }
    ElMessage.error(apiErrorMessage(error, '需求单解析失败'))
  } finally {
    requirementLoading.value = false
  }
}

async function remapRequirementStandardization() {
  if (!requirementPreview.value) return
  requirementLoading.value = true
  try {
    const response = await api.post('/admin/requirement-standardization/remap', {
      preview: requirementPreview.value,
      sheet_mappings: requirementPayloadMappings(),
    })
    hydrateRequirementPreview(responseData(response))
    await saveRequirementProgress('应用列映射', { quiet: true })
    ElMessage.success('已应用人工列映射')
  } catch (error) {
    if (isFeatureDisabled(error)) {
      requirementFeatureDisabled.value = true
      return
    }
    ElMessage.error(apiErrorMessage(error, '列映射应用失败'))
  } finally {
    requirementLoading.value = false
  }
}

async function submitRequirementConfirmation(saveReason = '生成确认清单') {
  const response = await api.post('/admin/requirement-standardization/confirm', {
    rows: requirementRows.value,
  })
  requirementConfirmed.value = responseData(response)
  await saveRequirementProgress(saveReason, { quiet: true })
  return requirementConfirmed.value
}

async function confirmRequirementStandardization() {
  requirementConfirming.value = true
  try {
    const confirmed = await submitRequirementConfirmation('生成确认清单')
    requirementQuoteJob.value = null
    if (confirmed.summary.blocked_row_count) {
      ElMessage.warning(`有 ${confirmed.summary.blocked_row_count} 行未通过确认校验，已列出每行原因`)
      await focusFirstRequirementBlockedRow()
    } else {
      requirementActiveBlockedRowKey.value = ''
      if (requirementRowFilters.status === 'blocked') requirementRowFilters.status = 'included'
      ElMessage.success('已生成确认清单')
    }
  } catch (error) {
    if (isFeatureDisabled(error)) {
      requirementFeatureDisabled.value = true
      return
    }
    ElMessage.error(apiErrorMessage(error, '确认清单生成失败'))
  } finally {
    requirementConfirming.value = false
  }
}

async function startRequirementQuoteJob() {
  if (!selectedRequirementRows.value.length) {
    ElMessage.warning('请先选择要进入报价的标准行')
    return
  }
  try {
    await ElMessageBox.confirm(
      '系统会重新校验当前行确认结果，只将已确认且通过校验的标准行发送到现有报价任务。',
      '发起报价',
      {
        confirmButtonText: '发起报价',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }

  requirementQuoting.value = true
  requirementQuoteJob.value = null
  try {
    const confirmed = await submitRequirementConfirmation('发起报价前确认清单')
    const summary = confirmed.summary || {}
    if (summary.blocked_row_count) {
      ElMessage.warning(`有 ${summary.blocked_row_count} 行未通过确认校验，请处理后再发起报价`)
      await focusFirstRequirementBlockedRow()
      return
    }
    if (!summary.confirmed_row_count || !(confirmed.quote_text || '').trim()) {
      ElMessage.warning('没有可发起报价的确认行')
      return
    }

    const form = new FormData()
    const sourceFile = requirementPreview.value?.source?.file_name || ''
    form.append('message', `【来源：需求单标准化确认清单】\n${confirmed.quote_text}`)
    form.append('source', '需求单标准化')
    form.append('notes', `来自需求单标准化确认清单${sourceFile ? `：${sourceFile}` : ''}；确认行数：${summary.confirmed_row_count}`)
    form.append('requirement_rows_json', JSON.stringify(confirmed.rows || []))
    const response = await api.post('/quote/jobs', form)
    requirementQuoteJob.value = responseData(response)
    await saveRequirementProgress('发起报价', { quiet: true })
    if (canViewQuoteOperations.value) loadQuoteJobs()
    handoffRequirementQuoteJob(requirementQuoteJob.value)
  } catch (error) {
    if (isFeatureDisabled(error)) {
      requirementFeatureDisabled.value = true
      return
    }
    ElMessage.error(apiErrorMessage(error, '报价任务创建失败'))
  } finally {
    requirementQuoting.value = false
  }
}

function handoffRequirementQuoteJob(job) {
  if (!job?.job_id) {
    ElMessage.success('已创建报价任务')
    return
  }
  try {
    window.sessionStorage.setItem('aimo_quote_job_handoff', JSON.stringify({
      quote_job_id: job.job_id,
      trace_id: job.trace_id || '',
      source: 'requirement_standardization',
      source_file_name: requirementPreview.value?.source?.file_name || '',
      created_at: new Date().toISOString(),
    }))
  } catch (error) {
    console.warn('quote job handoff storage failed', error)
  }
  const params = new URLSearchParams({
    quote_job_id: job.job_id,
    from: 'requirement_standardization',
  })
  if (job.trace_id) params.set('trace_id', job.trace_id)
  window.location.href = `/index.html?${params.toString()}`
}

function isBusinessLedgerDisabled(error) {
  return error.response?.status === 404 && error.response?.data?.detail === 'NOT_FOUND'
}

async function loadClientInquiries() {
  clientInquiries.value = []
  clientInquiryTotal.value = 0
  if (!responseDashboard.value) return
  const params = {
    page: clientInquiryPage.value,
    page_size: clientInquiryPageSize,
    has_client_info: true,
    sort: 'created_at_desc',
  }
  if (clientInquiryFilters.hasQuoteJob) params.has_quote_job = true
  if (clientInquiryFilters.source) params.source = clientInquiryFilters.source
  const keyword = clientInquiryFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  if (responseDashboard.value.range_start) params.date_from = responseDashboard.value.range_start
  if (responseDashboard.value.range_end) params.date_to = responseDashboard.value.range_end

  try {
    const response = await api.get('/client-inquiries', { params })
    clientInquiries.value = responseData(response) || []
    clientInquiryTotal.value = response.data?.total ?? clientInquiries.value.length
  } catch (error) {
    if (isFeatureDisabled(error) || error.response?.status === 403) return
    throw error
  }
}

function applyClientInquiryFilters() {
  clientInquiryPage.value = 1
  loadClientInquiries()
}

async function loadQuoteJobs() {
  if (!canViewQuoteOperations.value) return
  const params = {
    page: quoteJobPage.value,
    page_size: quoteJobPageSize,
  }
  if (quoteJobFilters.status) params.status = quoteJobFilters.status
  if (quoteJobFilters.source) params.source = quoteJobFilters.source
  const keyword = quoteJobFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  const username = quoteJobFilters.username.trim()
  if (username) params.username = username
  const rangeSource = quoteDashboard.value || responseDashboard.value
  if (rangeSource?.range_start) params.date_from = rangeSource.range_start
  if (rangeSource?.range_end) params.date_to = rangeSource.range_end

  try {
    const response = await api.get('/quote/jobs', { params })
    quoteJobs.value = responseData(response) || []
    quoteJobTotal.value = response.data?.total ?? quoteJobs.value.length
  } catch (error) {
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '报价任务加载失败'))
  }
}

function applyQuoteJobFilters() {
  quoteJobPage.value = 1
  loadQuoteJobs()
}

async function openQuoteJobDetail(row) {
  quoteJobDrawer.visible = true
  quoteJobDrawer.loading = true
  quoteJobDrawer.job = null
  quoteJobDrawer.costEvidence = []
  quoteJobDrawer.reviewDetail = null
  try {
    const response = await api.get(`/quote/jobs/${row.job_id}`)
    quoteJobDrawer.job = responseData(response)
    try {
      const reviewResponse = await api.get(`/quote/jobs/${row.job_id}/review-detail`)
      quoteJobDrawer.reviewDetail = responseData(reviewResponse)
    } catch (error) {
      if (error.response?.status !== 404) {
        ElMessage.warning(apiErrorMessage(error, '预审条目加载失败'))
      }
    }
    const evidenceCount = Math.max(
      Number(row.cost_evidence_count || 0),
      Number(quoteJobDrawer.job?.cost_evidence_count || 0),
    )
    if (canAccessPermissions.value && evidenceCount > 0) {
      try {
        const evidenceResponse = await api.get('/admin/quote-cost-evidence', {
          params: { quote_job_id: row.job_id, page_size: 100 },
        })
        quoteJobDrawer.costEvidence = responseData(evidenceResponse) || []
      } catch (error) {
        if (error.response?.status !== 404) {
          ElMessage.warning(apiErrorMessage(error, '成本证据加载失败'))
        }
      }
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '任务详情加载失败'))
  } finally {
    quoteJobDrawer.loading = false
  }
}

function openCostEvidenceItem(row) {
  if (!row?.cost_item_id) return
  openCostItemDetail({ id: row.cost_item_id })
}

async function retryQuoteJob(row) {
  if (!canRetryQuoteJob(row)) return
  state.submitting = true
  try {
    await api.post(`/quote/jobs/${row.job_id}/retry`)
    ElMessage.success('已创建重试任务')
    await loadQuoteJobs()
    await loadDashboards()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '重试失败'))
  } finally {
    state.submitting = false
  }
}

async function cancelQuoteJob(row) {
  if (!canCancelQuoteJob(row)) return
  try {
    await ElMessageBox.confirm('确认取消这条报价任务？', '取消任务', {
      type: 'warning',
      confirmButtonText: '确认取消',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/quote/jobs/${row.job_id}/cancel`)
    ElMessage.success('已取消任务')
    await loadQuoteJobs()
    await loadDashboards()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '取消失败'))
  } finally {
    state.submitting = false
  }
}

async function markQuoteTimeouts() {
  state.submitting = true
  try {
    const response = await api.post('/admin/quote/jobs/mark_timeouts', null, {
      params: { timeout_minutes: 30 },
    })
    ElMessage.success(`已标记 ${response.data?.marked_count ?? 0} 条超时任务`)
    await loadQuoteJobs()
    await loadDashboards()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '标记超时失败'))
  } finally {
    state.submitting = false
  }
}

async function loadExecutionUsers() {
  if (!canCreateExecutionTask.value || users.value.length) return
  try {
    const response = await api.get('/admin/users')
    users.value = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '负责人加载失败'))
  }
}

async function loadBusinessLedgerUsers() {
  if (!canManageBusinessLedger.value || users.value.length) return
  try {
    const response = await api.get('/admin/users')
    users.value = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '负责人加载失败'))
  }
}

async function loadBusinessLedgers() {
  if (!canViewBusinessLedger.value) return
  businessLedgerFeatureDisabled.value = false
  businessLedgerLoading.value = true
  const params = {
    page: businessLedgerPage.value,
    page_size: businessLedgerPageSize,
  }
  if (businessLedgerFilters.stage.length) params.stage = businessLedgerFilters.stage.join(',')
  if (businessLedgerFilters.source) params.source = businessLedgerFilters.source
  if (canManageBusinessLedger.value && businessLedgerFilters.responder_id) {
    params.responder_id = businessLedgerFilters.responder_id
  }
  const [dateFrom, dateTo] = businessLedgerFilters.dateRange || []
  if (dateFrom) params.date_from = dateFrom
  if (dateTo) params.date_to = dateTo
  const keyword = businessLedgerFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  if (businessLedgerFilters.overdue_only) params.overdue_only = true
  try {
    const response = await api.get('/business-ledger', { params })
    businessLedgers.value = responseData(response) || []
    businessLedgerTotal.value = response.data?.total ?? businessLedgers.value.length
  } catch (error) {
    businessLedgers.value = []
    businessLedgerTotal.value = 0
    if (isBusinessLedgerDisabled(error)) {
      businessLedgerFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '商务台账加载失败'))
  } finally {
    businessLedgerLoading.value = false
  }
}

function applyBusinessLedgerFilters() {
  businessLedgerPage.value = 1
  loadBusinessLedgers()
}

async function loadCostItems() {
  if (!canViewCostDb.value) return
  costDbFeatureDisabled.value = false
  costDbLoading.value = true
  const params = buildCostItemQueryParams(costItemPage.value, costItemPageSize)
  try {
    const response = await api.get('/admin/cost-items', { params })
    costSelectionSyncing.value = true
    costItems.value = responseData(response) || []
    costItemTotal.value = response.data?.total ?? costItems.value.length
    await syncCurrentCostPageSelection()
    await loadCostRagSyncStatus({ silent: true })
  } catch (error) {
    costItems.value = []
    costItemTotal.value = 0
    selectedCostItems.value = []
    if (isFeatureDisabled(error)) {
      costDbFeatureDisabled.value = true
      costRagSyncStatus.value = null
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '成本数据库加载失败'))
  } finally {
    costDbLoading.value = false
  }
}

function buildCostItemQueryParams(page, pageSize) {
  const params = {
    page,
    page_size: pageSize,
  }
  const category = costItemFilters.category.trim()
  if (category) params.category = category
  if (costItemFilters.status.length) params.status = costItemFilters.status.join(',')
  if (costItemFilters.price_type) params.price_type = costItemFilters.price_type
  if (costItemFilters.source) params.source = costItemFilters.source
  const keyword = costItemFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  return params
}

function dedupeCostItems(items) {
  const itemMap = new Map()
  items.forEach((item) => {
    if (item?.id) itemMap.set(item.id, item)
  })
  return Array.from(itemMap.values())
}

function handleCostItemSelectionChange(selection) {
  if (costSelectionSyncing.value) return
  const currentPageIds = new Set(costItems.value.map((item) => item.id))
  const offPageSelection = selectedCostItems.value.filter((item) => !currentPageIds.has(item.id))
  selectedCostItems.value = dedupeCostItems([...offPageSelection, ...(selection || [])])
}

function costItemSelectable(row) {
  return row.status !== 'archived'
}

function clearCostItemSelection() {
  selectedCostItems.value = []
  costItemsTable.value?.clearSelection?.()
}

async function syncCurrentCostPageSelection() {
  await nextTick()
  const table = costItemsTable.value
  if (!table) {
    costSelectionSyncing.value = false
    return
  }
  const selectedIds = new Set(selectedCostItemIds.value)
  costSelectionSyncing.value = true
  try {
    table.clearSelection()
    selectableCostItems.value.forEach((item) => {
      if (selectedIds.has(item.id)) table.toggleRowSelection(item, true)
    })
    await nextTick()
  } finally {
    costSelectionSyncing.value = false
  }
}

async function fetchAllSelectableCostItems() {
  const pageSize = 100
  let page = 1
  let total = 0
  const allItems = []

  do {
    const response = await api.get('/admin/cost-items', {
      params: buildCostItemQueryParams(page, pageSize),
    })
    const pageItems = responseData(response) || []
    total = response.data?.total ?? pageItems.length
    if (!pageItems.length) break
    allItems.push(...pageItems)
    page += 1
  } while (allItems.length < total)

  return dedupeCostItems(allItems.filter(costItemSelectable))
}

async function toggleSelectAllCostItems() {
  if (selectedCostItemIds.value.length) {
    clearCostItemSelection()
    return
  }

  costAllSelecting.value = true
  try {
    const allSelectableItems = await fetchAllSelectableCostItems()
    selectedCostItems.value = allSelectableItems
    await syncCurrentCostPageSelection()
    if (!allSelectableItems.length) {
      ElMessage.info('没有可选择的成本条目')
    } else {
      ElMessage.success(`已全选 ${allSelectableItems.length} 条可操作成本条目`)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '全选成本条目失败'))
  } finally {
    costAllSelecting.value = false
  }
}

function costBulkResultMessage(actionLabel, data) {
  const parts = [`${actionLabel}：已更新 ${data.changed_count || 0} 条`]
  if (data.skipped_count) parts.push(`跳过 ${data.skipped_count} 条`)
  if (data.conflict_count) parts.push(`冻结 ${data.conflict_count} 条`)
  if (data.not_found_count) parts.push(`未找到 ${data.not_found_count} 条`)
  return parts.join('，')
}

async function loadCostRagSyncStatus(options = {}) {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  costRagSyncStatusLoading.value = true
  try {
    const response = await api.get('/admin/cost-items/sync-rag/status')
    costRagSyncStatus.value = responseData(response)
  } catch (error) {
    costRagSyncStatus.value = null
    if (!options.silent) {
      ElMessage.error(apiErrorMessage(error, 'RAG 同步状态加载失败'))
    }
  } finally {
    costRagSyncStatusLoading.value = false
  }
}

async function loadCostRagSyncRuns() {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  costRagSyncDialog.loading = true
  try {
    const response = await api.get('/admin/cost-items/sync-rag/runs', {
      params: {
        page: costRagSyncPage.value,
        page_size: costRagSyncPageSize,
      },
    })
    costRagSyncRuns.value = responseData(response) || []
    costRagSyncTotal.value = response.data?.total ?? costRagSyncRuns.value.length
  } catch (error) {
    costRagSyncRuns.value = []
    costRagSyncTotal.value = 0
    ElMessage.error(apiErrorMessage(error, '同步记录加载失败'))
  } finally {
    costRagSyncDialog.loading = false
  }
}

function costItemExportParams() {
  const params = buildCostItemQueryParams(1, costItemPageSize)
  delete params.page
  delete params.page_size
  return params
}

async function exportCostItems() {
  if (!canExportCostDb.value || costDbFeatureDisabled.value) return
  try {
    await ElMessageBox.confirm('确认按当前筛选条件导出成本数据？导出动作会写入审计记录。', '导出成本数据', {
      type: 'warning',
      confirmButtonText: '确认导出',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  try {
    const response = await api.get('/admin/cost-items/export', {
      params: costItemExportParams(),
      responseType: 'blob',
    })
    const disposition = response.headers?.['content-disposition'] || ''
    const match = disposition.match(/filename="?([^"]+)"?/i)
    const filename = match?.[1] || `cost_items_${Date.now()}.csv`
    const blob = new Blob([response.data], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('已生成成本库导出文件')
    if (costAuditDialog.visible) await loadCostAuditLogs()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '导出成本数据失败'))
  }
}

function costAuditQueryParams() {
  const params = {
    page: costAuditPage.value,
    page_size: costAuditPageSize,
  }
  if (costAuditFilters.action) params.action = costAuditFilters.action
  if (costAuditFilters.username.trim()) params.username = costAuditFilters.username.trim()
  if (costAuditFilters.resource_id.trim()) params.resource_id = costAuditFilters.resource_id.trim()
  if (costAuditFilters.status) params.status = costAuditFilters.status
  return params
}

async function loadCostAuditLogs() {
  if (!canViewCostAudit.value || costDbFeatureDisabled.value) return
  costAuditDialog.loading = true
  try {
    const response = await api.get('/admin/cost-items/audit-logs', { params: costAuditQueryParams() })
    costAuditLogs.value = responseData(response) || []
    costAuditTotal.value = response.data?.total ?? costAuditLogs.value.length
  } catch (error) {
    costAuditLogs.value = []
    costAuditTotal.value = 0
    ElMessage.error(apiErrorMessage(error, '审计记录加载失败'))
  } finally {
    costAuditDialog.loading = false
  }
}

function openCostAuditDialog() {
  if (!canViewCostAudit.value || costDbFeatureDisabled.value) return
  costAuditDialog.visible = true
  costAuditPage.value = 1
  loadCostAuditLogs()
}

function applyCostAuditFilters() {
  costAuditPage.value = 1
  loadCostAuditLogs()
}

function openCostRagSyncDialog() {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  costRagSyncDialog.visible = true
  costRagSyncPage.value = 1
  loadCostRagSyncRuns()
}

function costLineageStatusForTab() {
  return ['draft', 'active', 'archived'].includes(costLineageDrawer.activeTab)
    ? costLineageDrawer.activeTab
    : ''
}

async function loadCostLineageSummary() {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  costLineageDrawer.summaryLoading = true
  try {
    const response = await api.get('/admin/cost-items/lineage/summary')
    costLineageSummary.value = responseData(response) || {}
  } catch (error) {
    costLineageSummary.value = {}
    ElMessage.error(apiErrorMessage(error, '状态与流向汇总加载失败'))
  } finally {
    costLineageDrawer.summaryLoading = false
  }
}

function costLineageQueryParams() {
  const params = {
    page: costLineagePage.value,
    page_size: costLineagePageSize,
  }
  const status = costLineageStatusForTab()
  if (status) params.status = status
  if (costLineageFilters.source) params.source = costLineageFilters.source
  if (costLineageFilters.keyword.trim()) params.keyword = costLineageFilters.keyword.trim()
  if (costLineageFilters.has_quote_usage) params.has_quote_usage = costLineageFilters.has_quote_usage
  return params
}

async function loadCostLineageRows() {
  if (!canViewCostDb.value || costDbFeatureDisabled.value || costLineageDrawer.activeTab === 'summary') return
  costLineageDrawer.loading = true
  try {
    const response = await api.get('/admin/cost-items/lineage', { params: costLineageQueryParams() })
    costLineageRows.value = responseData(response) || []
    costLineageTotal.value = response.data?.total ?? costLineageRows.value.length
    if (!costLineageDrawer.detail && costLineageRows.value.length) {
      await openCostLineageDetail(costLineageRows.value[0])
    }
  } catch (error) {
    costLineageRows.value = []
    costLineageTotal.value = 0
    ElMessage.error(apiErrorMessage(error, '状态与流向列表加载失败'))
  } finally {
    costLineageDrawer.loading = false
  }
}

function openCostLineageDrawer() {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  costLineageDrawer.visible = true
  costLineageDrawer.activeTab = 'summary'
  costLineageDrawer.detail = null
  costLineageRows.value = []
  costLineageTotal.value = 0
  loadCostLineageSummary()
}

function handleCostLineageTabClick(tab) {
  const tabName = tab?.paneName || tab?.props?.name || costLineageDrawer.activeTab
  costLineageDrawer.activeTab = tabName
  costLineagePage.value = 1
  costLineageDrawer.detail = null
  if (tabName === 'summary') {
    loadCostLineageSummary()
  } else {
    loadCostLineageRows()
  }
}

function applyCostLineageFilters() {
  costLineagePage.value = 1
  costLineageDrawer.detail = null
  loadCostLineageRows()
}

async function openCostLineageDetail(row) {
  if (!row?.id) return
  costLineageDrawer.detailLoading = true
  try {
    const response = await api.get(`/admin/cost-items/${row.id}/lineage`)
    costLineageDrawer.detail = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '成本条目流向详情加载失败'))
  } finally {
    costLineageDrawer.detailLoading = false
  }
}

async function syncActiveCostItemsToRag() {
  if (!canApproveCostDb.value || costDbFeatureDisabled.value) return
  try {
    await ElMessageBox.confirm('确认将 active 成本条目同步到 RAG？这会让成本数据库成为报价检索主源。', '同步成本库到 RAG', {
      type: 'warning',
      confirmButtonText: '确认同步',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  costRagSyncing.value = true
  try {
    const response = await api.post('/admin/cost-items/sync-rag')
    const data = responseData(response) || {}
    ElMessage.success(response.data?.message || `已同步 ${data.synced_count || 0} 条 active 成本条目`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '同步 active 成本条目失败'))
  } finally {
    costRagSyncing.value = false
    if (costRagSyncDialog.visible) await loadCostRagSyncRuns()
    await loadCostRagSyncStatus({ silent: true })
  }
}

async function bulkActivateCostItems() {
  if (!canApproveCostDb.value || !selectedDraftCostItemCount.value) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      `确认将选中条目中的 ${selectedDraftCostItemCount.value} 条 draft 批量核定为 active？请输入核定原因`,
      '批量核定 active',
      {
        inputPattern: /\S+/,
        inputErrorMessage: '核定原因不能为空',
        type: 'warning',
        confirmButtonText: '确认核定',
        cancelButtonText: '返回',
      },
    )
    reason = result.value
  } catch {
    return
  }
  costBulkSubmitting.value = true
  try {
    const response = await api.post('/admin/cost-items/bulk-status', {
      item_ids: selectedCostItemIds.value,
      target_status: 'active',
      reason,
    })
    ElMessage.success(costBulkResultMessage('批量核定完成', responseData(response) || {}))
    clearCostItemSelection()
    await loadCostItems()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '批量核定失败'))
  } finally {
    costBulkSubmitting.value = false
  }
}

async function bulkRestoreCostItemsToDraft() {
  if (!canApproveCostDb.value || !selectedActiveCostItemCount.value) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      `确认将选中条目中的 ${selectedActiveCostItemCount.value} 条 active 批量恢复为 draft？请输入恢复原因`,
      '批量恢复 draft',
      {
        inputPattern: /\S+/,
        inputErrorMessage: '恢复原因不能为空',
        confirmButtonText: '确认恢复',
        cancelButtonText: '返回',
        type: 'warning',
      },
    )
    reason = result.value
  } catch {
    return
  }
  costBulkSubmitting.value = true
  try {
    const response = await api.post('/admin/cost-items/bulk-status', {
      item_ids: selectedCostItemIds.value,
      target_status: 'draft',
      reason,
    })
    ElMessage.success(costBulkResultMessage('批量恢复完成', responseData(response) || {}))
    clearCostItemSelection()
    await loadCostItems()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '批量恢复失败'))
  } finally {
    costBulkSubmitting.value = false
  }
}

async function bulkArchiveCostItems() {
  if (!canApproveCostDb.value || !selectedArchivableCostItemCount.value) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(
      `确认将选中的 ${selectedArchivableCostItemCount.value} 条成本数据批量归档？归档后不可撤回，请输入归档原因`,
      '批量归档成本条目',
      {
        inputPattern: /\S+/,
        inputErrorMessage: '归档原因不能为空',
        confirmButtonText: '确认归档',
        cancelButtonText: '返回',
        type: 'warning',
      },
    )
    reason = result.value
  } catch {
    return
  }
  costBulkSubmitting.value = true
  try {
    const response = await api.post('/admin/cost-items/bulk-status', {
      item_ids: selectedCostItemIds.value,
      target_status: 'archived',
      reason,
    })
    ElMessage.success(costBulkResultMessage('批量归档完成', responseData(response) || {}))
    clearCostItemSelection()
    await loadCostItems()
    await loadCostRagSyncStatus({ silent: true })
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '批量归档失败'))
  } finally {
    costBulkSubmitting.value = false
  }
}

function applyCostItemFilters() {
  costItemPage.value = 1
  clearCostItemSelection()
  loadCostItems()
}

function resetCostItemForm(mode = 'create') {
  costItemDialog.mode = mode
  costItemDialog.itemId = null
  costItemDialog.form.category = ''
  costItemDialog.form.subcategory = ''
  costItemDialog.form.item_name = ''
  costItemDialog.form.spec = ''
  costItemDialog.form.unit = ''
  costItemDialog.form.price = ''
  costItemDialog.form.client_tax_excluded_price = ''
  costItemDialog.form.client_labor_price = ''
  costItemDialog.form.client_main_material_price = ''
  costItemDialog.form.client_auxiliary_material_price = ''
  costItemDialog.form.client_direct_fee = ''
  costItemDialog.form.client_management_profit = ''
  costItemDialog.form.subcontract_composite_price = ''
  costItemDialog.form.subcontract_labor_price = ''
  costItemDialog.form.subcontract_main_material_price = ''
  costItemDialog.form.subcontract_auxiliary_material_price = ''
  costItemDialog.form.crew_benchmark_price = ''
  costItemDialog.form.price_type = 'combined'
  costItemDialog.form.effective_date = ''
  costItemDialog.form.notes = ''
  costItemDialog.form.change_reason = ''
}

function fillCostItemForm(row) {
  costItemDialog.itemId = row.id
  costItemDialog.form.category = row.category || ''
  costItemDialog.form.subcategory = row.subcategory || ''
  costItemDialog.form.item_name = row.item_name || ''
  costItemDialog.form.spec = row.spec || ''
  costItemDialog.form.unit = row.unit || ''
  costItemDialog.form.price = row.price ?? ''
  costItemDialog.form.client_tax_excluded_price = row.client_tax_excluded_price ?? ''
  costItemDialog.form.client_labor_price = row.client_labor_price ?? ''
  costItemDialog.form.client_main_material_price = row.client_main_material_price ?? ''
  costItemDialog.form.client_auxiliary_material_price = row.client_auxiliary_material_price ?? ''
  costItemDialog.form.client_direct_fee = row.client_direct_fee ?? ''
  costItemDialog.form.client_management_profit = row.client_management_profit ?? ''
  costItemDialog.form.subcontract_composite_price = row.subcontract_composite_price ?? ''
  costItemDialog.form.subcontract_labor_price = row.subcontract_labor_price ?? ''
  costItemDialog.form.subcontract_main_material_price = row.subcontract_main_material_price ?? ''
  costItemDialog.form.subcontract_auxiliary_material_price = row.subcontract_auxiliary_material_price ?? ''
  costItemDialog.form.crew_benchmark_price = row.crew_benchmark_price ?? ''
  costItemDialog.form.price_type = row.price_type || 'combined'
  costItemDialog.form.effective_date = row.effective_date || ''
  costItemDialog.form.notes = row.notes || ''
  costItemDialog.form.change_reason = ''
}

function costItemSubmitPayload() {
  const form = costItemDialog.form
  const payload = {
    category: form.category.trim(),
    subcategory: form.subcategory.trim() || null,
    item_name: form.item_name.trim(),
    spec: form.spec.trim() || null,
    unit: form.unit.trim(),
    price: parseCostNumber(form.price),
    client_tax_excluded_price: parseCostNumber(form.client_tax_excluded_price),
    client_labor_price: parseCostNumber(form.client_labor_price),
    client_main_material_price: parseCostNumber(form.client_main_material_price),
    client_auxiliary_material_price: parseCostNumber(form.client_auxiliary_material_price),
    client_direct_fee: parseCostNumber(form.client_direct_fee),
    client_management_profit: parseCostNumber(form.client_management_profit),
    subcontract_composite_price: parseCostNumber(form.subcontract_composite_price),
    subcontract_labor_price: parseCostNumber(form.subcontract_labor_price),
    subcontract_main_material_price: parseCostNumber(form.subcontract_main_material_price),
    subcontract_auxiliary_material_price: parseCostNumber(form.subcontract_auxiliary_material_price),
    crew_benchmark_price: parseCostNumber(form.crew_benchmark_price),
    price_type: form.price_type,
    effective_date: form.effective_date || null,
    notes: form.notes.trim() || null,
  }
  if (costItemDialog.mode === 'edit') {
    payload.change_reason = form.change_reason.trim() || null
  }
  return payload
}

function validateCostItemForm() {
  const form = costItemDialog.form
  if (!form.category.trim() || !form.item_name.trim() || !form.unit.trim()) {
    ElMessage.warning('请填写类别、项目名称和计量单位')
    return false
  }
  const priceFields = [
    ['主参考价', form.price],
    ['对甲税前综合单价', form.client_tax_excluded_price],
    ['对甲人工费', form.client_labor_price],
    ['对甲主材费', form.client_main_material_price],
    ['对甲辅材费', form.client_auxiliary_material_price],
    ['对甲直接费小计', form.client_direct_fee],
    ['对甲管理费利润', form.client_management_profit],
    ['劳务发包综合单价', form.subcontract_composite_price],
    ['劳务人工费', form.subcontract_labor_price],
    ['劳务主材费', form.subcontract_main_material_price],
    ['劳务辅材费', form.subcontract_auxiliary_material_price],
    ['班组标底税前价', form.crew_benchmark_price],
  ]
  for (const [label, value] of priceFields) {
    if (value !== '' && value !== null && value !== undefined && Number.isNaN(Number(value))) {
      ElMessage.warning(`${label} 必须是数字`)
      return false
    }
  }
  return true
}

function openCostItemCreate() {
  if (!canEditCostDb.value) return
  resetCostItemForm('create')
  costItemDialog.visible = true
}

async function openCostItemEdit(row) {
  if (!canEditCostDb.value || row.status === 'archived') return
  resetCostItemForm('edit')
  costItemDialog.mode = 'edit'
  try {
    const response = await api.get(`/admin/cost-items/${row.id}`)
    fillCostItemForm(responseData(response))
    costItemDialog.visible = true
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '成本条目详情加载失败'))
  }
}

async function openCostItemDetail(row) {
  costItemDrawer.visible = true
  costItemDrawer.loading = true
  costItemDrawer.item = null
  try {
    const response = await api.get(`/admin/cost-items/${row.id}`)
    costItemDrawer.item = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '成本条目详情加载失败'))
  } finally {
    costItemDrawer.loading = false
  }
}

async function submitCostItem() {
  if (!canEditCostDb.value) return
  if (!validateCostItemForm()) return
  state.submitting = true
  try {
    if (costItemDialog.mode === 'edit') {
      await api.patch(`/admin/cost-items/${costItemDialog.itemId}`, costItemSubmitPayload())
      ElMessage.success('已更新成本条目')
    } else {
      await api.post('/admin/cost-items', costItemSubmitPayload())
      ElMessage.success('已创建成本条目')
    }
    costItemDialog.visible = false
    clearCostItemSelection()
    await loadCostItems()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '保存失败'))
  } finally {
    state.submitting = false
  }
}

async function activateCostItem(row) {
  if (!canApproveCostDb.value || row.status !== 'draft') return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('确认启用这条成本数据？请输入核定原因', '启用成本条目', {
      inputPattern: /\S+/,
      inputErrorMessage: '核定原因不能为空',
      type: 'warning',
      confirmButtonText: '确认启用',
      cancelButtonText: '返回',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/cost-items/${row.id}/activate`, { reason })
    ElMessage.success('已启用成本条目')
    clearCostItemSelection()
    await loadCostItems()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '启用失败'))
  } finally {
    state.submitting = false
  }
}

async function withdrawCostItem(row) {
  if (!canApproveCostDb.value || row.status !== 'active') return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请输入撤回启用原因', '撤回启用', {
      inputPattern: /\S+/,
      inputErrorMessage: '撤回原因不能为空',
      confirmButtonText: '确认撤回',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/cost-items/${row.id}/withdraw`, { reason })
    ElMessage.success('已撤回启用，条目回到待核定')
    clearCostItemSelection()
    await loadCostItems()
    if (costItemDrawer.item?.id === row.id) {
      const response = await api.get(`/admin/cost-items/${row.id}`)
      costItemDrawer.item = responseData(response)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '撤回启用失败'))
  } finally {
    state.submitting = false
  }
}

async function archiveCostItem(row) {
  if (!canApproveCostDb.value || row.status === 'archived') return
  let reason = ''
  if (row.status === 'active') {
    try {
      const result = await ElMessageBox.prompt('请输入归档原因', '归档成本条目', {
        inputPattern: /\S+/,
        inputErrorMessage: '启用记录归档原因不能为空',
        confirmButtonText: '确认归档',
        cancelButtonText: '返回',
        type: 'warning',
      })
      reason = result.value
    } catch {
      return
    }
  } else {
    try {
      await ElMessageBox.confirm('确认归档这条草稿成本数据？', '归档成本条目', {
        type: 'warning',
        confirmButtonText: '确认归档',
        cancelButtonText: '返回',
      })
    } catch {
      return
    }
  }
  state.submitting = true
  try {
    await api.post(`/admin/cost-items/${row.id}/archive`, { reason })
    ElMessage.success('已归档成本条目')
    clearCostItemSelection()
    await loadCostItems()
    if (costItemDrawer.item?.id === row.id) costItemDrawer.visible = false
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '归档失败'))
  } finally {
    state.submitting = false
  }
}

function openCostImportDialog() {
  if (!canEditCostDb.value) return
  costImportDialog.file = null
  costImportDialog.preview = null
  costImportDialog.loading = false
  costImportDialog.visible = true
}

function handleCostImportFile(uploadFile) {
  costImportDialog.file = uploadFile.raw
  costImportDialog.preview = null
}

function clearCostImportFile() {
  costImportDialog.file = null
  costImportDialog.preview = null
}

async function previewCostImport() {
  if (!canEditCostDb.value) return
  if (!costImportDialog.file) {
    ElMessage.warning('请选择 Excel 文件')
    return
  }
  costImportDialog.loading = true
  costImportDialog.preview = null
  try {
    const formData = new FormData()
    formData.append('file', costImportDialog.file)
    const response = await api.post('/admin/cost-items/import/preview', formData)
    costImportDialog.preview = responseData(response)
    ElMessage.success(`已解析 ${costImportDialog.preview.item_count || 0} 条成本数据`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '导入预览失败'))
  } finally {
    costImportDialog.loading = false
  }
}

async function confirmCostImport() {
  if (!canEditCostDb.value) return
  if (!costImportDialog.preview?.batch_id) return
  state.submitting = true
  try {
    const response = await api.post('/admin/cost-items/import/confirm', {
      batch_id: costImportDialog.preview.batch_id,
    })
    const data = responseData(response)
    ElMessage.success(`导入完成：新增 ${data.created_count || 0}，更新 ${data.updated_count || 0}，跳过 ${data.skipped_count || 0}`)
    costImportDialog.visible = false
    await loadCostItems()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '确认导入失败'))
  } finally {
    state.submitting = false
  }
}

async function refreshExecutionPage() {
  await loadExecutionTasks()
  await loadMeetings()
}

async function loadExecutionTasks() {
  executionFeatureDisabled.value = false
  const params = {
    page: executionTaskPage.value,
    page_size: executionTaskPageSize,
  }
  if (executionTaskFilters.status) params.status = executionTaskFilters.status
  if (executionTaskFilters.source) params.source = executionTaskFilters.source
  const keyword = executionTaskFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  try {
    const response = await api.get('/execution-tasks', { params })
    executionTasks.value = responseData(response) || []
    executionTaskTotal.value = response.data?.total ?? executionTasks.value.length
  } catch (error) {
    executionTasks.value = []
    executionTaskTotal.value = 0
    if (isFeatureDisabled(error)) {
      executionFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '执行任务加载失败'))
  }
}

function applyExecutionTaskFilters() {
  executionTaskPage.value = 1
  loadExecutionTasks()
}

async function loadMeetings() {
  meetingFeatureDisabled.value = false
  const params = {
    page: meetingPage.value,
    page_size: meetingPageSize,
  }
  if (meetingFilters.status) params.status = meetingFilters.status
  const keyword = meetingFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  try {
    const response = await api.get('/meetings', { params })
    meetings.value = responseData(response) || []
    meetingTotal.value = response.data?.total ?? meetings.value.length
  } catch (error) {
    meetings.value = []
    meetingTotal.value = 0
    if (isFeatureDisabled(error)) {
      meetingFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '会议纪要加载失败'))
  }
}

function applyMeetingFilters() {
  meetingPage.value = 1
  loadMeetings()
}

function resetBusinessLedgerForm(mode = 'create') {
  businessLedgerDialog.mode = mode
  businessLedgerDialog.inquiryId = ''
  businessLedgerDialog.form.source = ''
  businessLedgerDialog.form.client_name = ''
  businessLedgerDialog.form.client_phone = ''
  businessLedgerDialog.form.stage = '初步接触'
  businessLedgerDialog.form.next_followup_at = ''
  businessLedgerDialog.form.responder_id = session.user?.id ?? null
  businessLedgerDialog.form.notes = ''
}

function fillBusinessLedgerForm(row) {
  businessLedgerDialog.inquiryId = row.inquiry_id
  businessLedgerDialog.form.source = row.source || ''
  businessLedgerDialog.form.client_name = row.client_name || ''
  businessLedgerDialog.form.client_phone = row.client_phone || ''
  businessLedgerDialog.form.stage = row.stage || '初步接触'
  businessLedgerDialog.form.next_followup_at = toDatePickerValue(row.next_followup_at)
  businessLedgerDialog.form.responder_id = row.responder_id || session.user?.id || null
  businessLedgerDialog.form.notes = row.notes || ''
}

async function openBusinessLedgerCreate() {
  await loadBusinessLedgerUsers()
  resetBusinessLedgerForm('create')
  businessLedgerDialog.visible = true
}

async function openBusinessLedgerEdit(row) {
  if (!canEditBusinessLedger(row)) return
  await loadBusinessLedgerUsers()
  businessLedgerDialog.mode = 'edit'
  try {
    const response = await api.get(`/business-ledger/${row.inquiry_id}`)
    fillBusinessLedgerForm(responseData(response))
    businessLedgerDialog.visible = true
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '台账详情加载失败'))
  }
}

async function openBusinessLedgerDetail(row) {
  businessLedgerDrawer.visible = true
  businessLedgerDrawer.loading = true
  businessLedgerDrawer.ledger = null
  try {
    const response = await api.get(`/business-ledger/${row.inquiry_id}`)
    businessLedgerDrawer.ledger = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '台账详情加载失败'))
  } finally {
    businessLedgerDrawer.loading = false
  }
}

function businessLedgerSubmitPayload() {
  const form = businessLedgerDialog.form
  const payload = {
    client_phone: form.client_phone,
    stage: form.stage,
    next_followup_at: form.next_followup_at || null,
    notes: form.notes,
  }
  if (businessLedgerDialog.mode === 'create' || canManageBusinessLedger.value) {
    payload.source = form.source
    payload.client_name = form.client_name
  }
  if (canManageBusinessLedger.value && form.responder_id) {
    payload.responder_id = form.responder_id
  }
  return payload
}

async function submitBusinessLedger() {
  state.submitting = true
  try {
    if (businessLedgerDialog.mode === 'edit') {
      await api.patch(`/business-ledger/${businessLedgerDialog.inquiryId}`, businessLedgerSubmitPayload())
      ElMessage.success('已更新商务台账')
    } else {
      await api.post('/business-ledger', businessLedgerSubmitPayload())
      ElMessage.success('已创建商务台账')
    }
    businessLedgerDialog.visible = false
    await loadBusinessLedgers()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '保存失败'))
  } finally {
    state.submitting = false
  }
}

async function cancelBusinessLedger(row) {
  if (!canCancelBusinessLedger(row)) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请输入作废原因', '作废商务台账', {
      inputPattern: /\S+/,
      inputErrorMessage: '作废原因不能为空',
      confirmButtonText: '确认作废',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/business-ledger/${row.inquiry_id}/cancel`, { reason })
    ElMessage.success('已作废商务台账')
    await loadBusinessLedgers()
    if (businessLedgerDrawer.ledger?.inquiry_id === row.inquiry_id) {
      businessLedgerDrawer.visible = false
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '作废失败'))
  } finally {
    state.submitting = false
  }
}

async function openExecutionCreate() {
  await loadExecutionUsers()
  executionDialog.form.title = ''
  executionDialog.form.assignee_id = executionAssigneeOptions.value[0]?.id ?? null
  executionDialog.form.due_at = ''
  executionDialog.form.source = 'manual'
  executionDialog.form.source_ref_id = ''
  executionDialog.form.notes = ''
  executionDialog.visible = true
}

function openMeetingCreate() {
  meetingDialog.form.content = ''
  meetingDialog.visible = true
}

async function createExecutionTask() {
  if (!executionDialog.form.title.trim() || !executionDialog.form.assignee_id || !executionDialog.form.due_at) {
    ElMessage.warning('请填写任务标题、负责人和截止时间')
    return
  }
  state.submitting = true
  try {
    await api.post('/execution-tasks', {
      title: executionDialog.form.title,
      assignee_id: executionDialog.form.assignee_id,
      due_at: executionDialog.form.due_at,
      source: executionDialog.form.source,
      source_ref_id: executionDialog.form.source_ref_id,
      notes: executionDialog.form.notes,
    })
    executionDialog.visible = false
    ElMessage.success('已创建执行任务')
    await loadExecutionTasks()
    if (routeName.value === 'dashboard') await loadDashboards()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '创建任务失败'))
  } finally {
    state.submitting = false
  }
}

async function createMeetingNote() {
  if (!meetingDialog.form.content.trim()) {
    ElMessage.warning('请填写会议纪要')
    return
  }
  state.submitting = true
  try {
    const response = await api.post('/meetings', { content: meetingDialog.form.content })
    meetingDialog.visible = false
    ElMessage.success('已生成任务草稿')
    await loadMeetings()
    await openMeetingDetail(responseData(response))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '会议纪要保存失败'))
  } finally {
    state.submitting = false
  }
}

async function updateExecutionTaskStatus(row, nextStatus) {
  state.submitting = true
  try {
    await api.patch(`/execution-tasks/${row.id}`, { status: nextStatus })
    ElMessage.success(nextStatus === 'done' ? '任务已完成' : '任务已更新')
    await loadExecutionTasks()
    if (routeName.value === 'dashboard') await loadDashboards()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '更新任务失败'))
  } finally {
    state.submitting = false
  }
}

async function cancelExecutionTask(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请输入取消原因', '取消执行任务', {
      inputPattern: /\S+/,
      inputErrorMessage: '取消原因不能为空',
      confirmButtonText: '确认取消',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/execution-tasks/${row.id}/cancel`, { reason })
    ElMessage.success('已取消执行任务')
    await loadExecutionTasks()
    if (routeName.value === 'dashboard') await loadDashboards()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '取消任务失败'))
  } finally {
    state.submitting = false
  }
}

async function openExecutionDetail(row) {
  executionDrawer.visible = true
  executionDrawer.loading = true
  executionDrawer.task = null
  try {
    const response = await api.get(`/execution-tasks/${row.id}`)
    executionDrawer.task = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '任务详情加载失败'))
  } finally {
    executionDrawer.loading = false
  }
}

function prepareMeetingDetail(note) {
  const fallbackAssignee = executionAssigneeOptions.value[0]?.id ?? session.user?.id ?? null
  return {
    ...note,
    drafts: (note.drafts || []).map((draft) => ({
      ...draft,
      confirm_title: draft.title,
      confirm_assignee_id: draft.confirmed_assignee_id || draft.suggested_assignee_id || fallbackAssignee,
      confirm_due_at: toDatePickerValue(draft.confirmed_due_at || draft.suggested_due_at),
      confirm_notes: draft.notes || '',
    })),
  }
}

async function openMeetingDetail(row) {
  await loadExecutionUsers()
  meetingDrawer.visible = true
  meetingDrawer.loading = true
  meetingDrawer.note = null
  try {
    const response = await api.get(`/meetings/${row.id}`)
    meetingDrawer.note = prepareMeetingDetail(responseData(response))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '会议纪要详情加载失败'))
  } finally {
    meetingDrawer.loading = false
  }
}

function openManualDraft() {
  if (!meetingDrawer.note) return
  const fallbackAssignee = executionAssigneeOptions.value[0]?.id ?? session.user?.id ?? null
  manualDraftDialog.form.title = ''
  manualDraftDialog.form.assignee_id = fallbackAssignee
  manualDraftDialog.form.due_at = ''
  manualDraftDialog.form.source_sentence = '人工补充'
  manualDraftDialog.form.notes = ''
  manualDraftDialog.visible = true
}

async function addManualDraft() {
  if (!meetingDrawer.note || !manualDraftDialog.form.title.trim()) {
    ElMessage.warning('请填写任务标题')
    return
  }
  state.submitting = true
  try {
    await api.post(`/meetings/${meetingDrawer.note.id}/drafts`, {
      title: manualDraftDialog.form.title,
      assignee_id: manualDraftDialog.form.assignee_id,
      due_at: manualDraftDialog.form.due_at,
      source_sentence: manualDraftDialog.form.source_sentence,
      notes: manualDraftDialog.form.notes,
    })
    manualDraftDialog.visible = false
    ElMessage.success('已补充任务草稿')
    await openMeetingDetail(meetingDrawer.note)
    await loadMeetings()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '补充草稿失败'))
  } finally {
    state.submitting = false
  }
}

async function confirmDraft(draft) {
  if (!meetingDrawer.note) return
  if (!draft.confirm_title?.trim() || !draft.confirm_assignee_id || !draft.confirm_due_at) {
    ElMessage.warning('请补齐标题、负责人和截止时间')
    return
  }
  state.submitting = true
  try {
    await api.post(`/meetings/${meetingDrawer.note.id}/confirm-tasks`, {
      drafts: [
        {
          draft_id: draft.id,
          action: 'accept',
          title: draft.confirm_title,
          assignee_id: draft.confirm_assignee_id,
          due_at: draft.confirm_due_at,
          notes: draft.confirm_notes,
        },
      ],
    })
    ElMessage.success('已写入执行任务')
    await openMeetingDetail(meetingDrawer.note)
    await loadMeetings()
    await loadExecutionTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '确认草稿失败'))
  } finally {
    state.submitting = false
  }
}

async function rejectDraft(draft) {
  if (!meetingDrawer.note) return
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请输入驳回原因', '驳回任务草稿', {
      inputPattern: /\S+/,
      inputErrorMessage: '驳回原因不能为空',
      confirmButtonText: '确认驳回',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/meetings/${meetingDrawer.note.id}/confirm-tasks`, {
      drafts: [{ draft_id: draft.id, action: 'reject', rejection_reason: reason }],
    })
    ElMessage.success('已驳回草稿')
    await openMeetingDetail(meetingDrawer.note)
    await loadMeetings()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '驳回草稿失败'))
  } finally {
    state.submitting = false
  }
}

async function cancelMeeting(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请输入作废原因', '作废会议纪要', {
      inputPattern: /\S+/,
      inputErrorMessage: '作废原因不能为空',
      confirmButtonText: '确认作废',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/meetings/${row.id}/cancel`, { reason })
    ElMessage.success('已作废会议纪要')
    await loadMeetings()
    if (meetingDrawer.note?.id === row.id) await openMeetingDetail(row)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '作废失败'))
  } finally {
    state.submitting = false
  }
}

async function loadDashboards() {
  state.loading = true
  state.error = ''
  dashboardFeature.quoteDisabled = false
  dashboardFeature.responseDisabled = false
  dashboardFeature.executionDisabled = false
  clientInquiryPage.value = 1
  quoteJobPage.value = 1
  let loadedCount = 0
  try {
    try {
      const response = await api.get('/admin/dashboard/quote-speed', {
        params: { range: dashboardRange.value },
      })
      quoteDashboard.value = responseData(response)
      loadedCount += 1
    } catch (error) {
      quoteDashboard.value = null
      if (isFeatureDisabled(error)) dashboardFeature.quoteDisabled = true
      else throw error
    }

    try {
      const response = await api.get('/admin/dashboard/response-speed', {
        params: { range: dashboardRange.value },
      })
      responseDashboard.value = responseData(response)
      await loadClientInquiries()
      loadedCount += 1
    } catch (error) {
      responseDashboard.value = null
      clientInquiries.value = []
      clientInquiryTotal.value = 0
      if (isFeatureDisabled(error)) dashboardFeature.responseDisabled = true
      else throw error
    }

    try {
      const response = await api.get('/admin/dashboard/execution-speed', {
        params: { range: dashboardRange.value },
      })
      executionDashboard.value = responseData(response)
      loadedCount += 1
    } catch (error) {
      executionDashboard.value = null
      if (isFeatureDisabled(error)) dashboardFeature.executionDisabled = true
      else throw error
    }

    if (canViewQuoteOperations.value) {
      await loadQuoteJobs()
      loadedCount += 1
    }
    if (loadedCount === 0) {
      state.error = 'feature_disabled'
      return
    }
    const availableTabs = []
    if (!dashboardFeature.quoteDisabled) availableTabs.push('quote')
    if (!dashboardFeature.responseDisabled) availableTabs.push('response')
    if (canViewQuoteOperations.value) availableTabs.push('operations')
    if (!dashboardFeature.executionDisabled) availableTabs.push('execution')
    if (!availableTabs.includes(dashboardTab.value)) {
      dashboardTab.value = availableTabs[0] || 'quote'
    }
  } catch (error) {
    quoteDashboard.value = null
    responseDashboard.value = null
    executionDashboard.value = null
    clientInquiries.value = []
    clientInquiryTotal.value = 0
    quoteJobs.value = []
    quoteJobTotal.value = 0
    if (error.response?.status === 401) state.error = 'unauthorized'
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
      await loadDashboards()
      return
    }
    if (routeName.value === 'execution') {
      if (!canViewExecution.value) {
        state.error = 'forbidden'
        return
      }
      await loadExecutionTasks()
      await loadExecutionUsers()
      await loadMeetings()
      if (executionFeatureDisabled.value && !meetingFeatureDisabled.value) {
        executionPageTab.value = 'meetings'
      }
      return
    }
    if (routeName.value === 'businessLedger') {
      if (!canViewBusinessLedger.value) {
        state.error = 'forbidden'
        return
      }
      await loadBusinessLedgerUsers()
      await loadBusinessLedgers()
      return
    }
    if (routeName.value === 'costDb') {
      if (!canViewCostDb.value) {
        state.error = 'forbidden'
        return
      }
      await loadCostItems()
      const costItemId = Number(new URLSearchParams(window.location.search).get('cost_item_id'))
      if (Number.isFinite(costItemId) && costItemId > 0) {
        await openCostItemDetail({ id: costItemId })
      }
      return
    }
    if (routeName.value === 'requirementStandardization') {
      if (!canViewRequirementStandardization.value) {
        state.error = 'forbidden'
        return
      }
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

function openCreateUser() {
  createUserDialog.username = ''
  createUserDialog.password = ''
  createUserDialog.quota = 5
  createUserDialog.roles = ['staff']
  createUserDialog.note = '管理员创建账号'
  createUserDialog.visible = true
}

async function createUser() {
  if (!createUserDialog.username.trim() || !createUserDialog.password) {
    ElMessage.warning('请填写账号和初始密码')
    return
  }
  if (!createUserDialog.roles.length || !createUserDialog.note.trim()) {
    ElMessage.warning('请选择角色并填写备注')
    return
  }
  state.submitting = true
  try {
    await api.post('/admin/users', {
      username: createUserDialog.username.trim(),
      password: createUserDialog.password,
      quota: createUserDialog.quota,
      roles: createUserDialog.roles,
      note: createUserDialog.note,
    })
    createUserDialog.visible = false
    await loadUsers()
    ElMessage.success('用户已创建')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '创建用户失败'))
  } finally {
    state.submitting = false
  }
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
