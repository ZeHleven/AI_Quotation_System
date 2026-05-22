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
                v-if="canManageCostDb"
                :icon="Document"
                plain
                :disabled="costDbFeatureDisabled"
                @click="openCostImportDialog"
              >
                导入 Excel
              </el-button>
              <el-button
                v-if="canManageCostDb"
                :icon="DataAnalysis"
                plain
                :loading="costRagSyncing"
                :disabled="costDbFeatureDisabled || costRagSyncing"
                @click="syncActiveCostItemsToRag"
              >
                同步 active 到 RAG
              </el-button>
              <el-button
                v-if="canManageCostDb"
                :icon="Clock"
                plain
                :disabled="costDbFeatureDisabled"
                @click="openCostRagSyncDialog"
              >
                同步记录
              </el-button>
              <el-button
                v-if="canManageCostDb"
                :icon="Select"
                plain
                :loading="costAllSelecting"
                :disabled="costDbFeatureDisabled || costDbLoading || costAllSelecting || costItemTotal === 0"
                @click="toggleSelectAllCostItems"
              >
                {{ selectedCostItemIds.length ? '取消全选' : '全选全部' }}
              </el-button>
              <el-button
                v-if="canManageCostDb"
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
                v-if="canManageCostDb"
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
                v-if="canManageCostDb"
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
            <div class="cost-db-filters">
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
                v-if="canManageCostDb"
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
                      v-if="canManageCostDb"
                      size="small"
                      plain
                      :disabled="row.status === 'archived'"
                      @click="openCostItemEdit(row)"
                    >
                      编辑
                    </el-button>
                    <el-button
                      v-if="canManageCostDb"
                      size="small"
                      type="success"
                      plain
                      :disabled="row.status !== 'draft'"
                      @click="activateCostItem(row)"
                    >
                      启用
                    </el-button>
                    <el-button
                      v-if="canManageCostDb"
                      size="small"
                      type="warning"
                      plain
                      :disabled="row.status !== 'active'"
                      @click="withdrawCostItem(row)"
                    >
                      撤回启用
                    </el-button>
                    <el-button
                      v-if="canManageCostDb"
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
                  <small>{{ totalSourceLabel(row.line_total_source) }}</small>
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
                  <small>{{ totalSourceLabel(row.quote_total_source) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="偏差" width="110">
              <template #default="{ row }">
                <span>{{ formatPrice(row.price_delta) }}</span>
                <small class="muted-inline">{{ formatRate(row.price_delta_rate) }}</small>
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
  Histogram,
  Lock,
  Plus,
  Refresh,
  Select,
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
  { value: 'manual', label: '手工' },
  { value: 'imported', label: '导入' },
  { value: 'ai_suggested', label: 'AI 建议' },
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
  keyword: '',
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

const costRagSyncDialog = reactive({
  visible: false,
  loading: false,
})

const roles = computed(() => session.user?.roles || [])
const canMutateRoles = computed(() => roles.value.includes('system_admin'))
const canAccessPermissions = computed(() => roles.value.includes('system_admin') || roles.value.includes('admin'))
const canViewDashboard = computed(() => canAccessPermissions.value || roles.value.includes('viewer'))
const canViewQuoteOperations = computed(() => canAccessPermissions.value)
const canViewExecution = computed(() => canAccessPermissions.value || roles.value.includes('staff') || roles.value.includes('manager'))
const canCreateExecutionTask = computed(() => canAccessPermissions.value)
const canCreateMeetingNote = computed(() => canViewExecution.value)
const canViewBusinessLedger = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canManageBusinessLedger = computed(() => canAccessPermissions.value)
const canViewCostDb = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canManageCostDb = computed(() => canAccessPermissions.value)
const canOpenLegacyQuote = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canOpenLegacyAdmin = computed(() => canAccessPermissions.value)
const selectedCostItemIds = computed(() => selectedCostItems.value.map((item) => item.id).filter(Boolean))
const selectableCostItems = computed(() => costItems.value.filter((item) => costItemSelectable(item)))
const selectedDraftCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'draft').length)
const selectedActiveCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'active').length)
const visibleDailyTrends = computed(() => (quoteDashboard.value?.daily_trends || []).filter((item) => item.sample_count > 0).slice(-12))
const visibleResponseSources = computed(() => (responseDashboard.value?.by_source || []).slice(0, 12))
const visibleResponseResponders = computed(() => (responseDashboard.value?.by_responder || []).slice(0, 12))
const visibleExecutionTrends = computed(() => (executionDashboard.value?.daily_trends || []).filter((item) => item.task_count > 0).slice(-12))
const visibleExecutionAssignees = computed(() => (executionDashboard.value?.by_assignee || []).slice(0, 12))
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
  try {
    const response = await api.get(`/quote/jobs/${row.job_id}`)
    quoteJobDrawer.job = responseData(response)
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
  } catch (error) {
    costItems.value = []
    costItemTotal.value = 0
    selectedCostItems.value = []
    if (isFeatureDisabled(error)) {
      costDbFeatureDisabled.value = true
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

async function loadCostRagSyncRuns() {
  if (!canManageCostDb.value || costDbFeatureDisabled.value) return
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

function openCostRagSyncDialog() {
  costRagSyncDialog.visible = true
  costRagSyncPage.value = 1
  loadCostRagSyncRuns()
}

async function syncActiveCostItemsToRag() {
  if (!canManageCostDb.value || costDbFeatureDisabled.value) return
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
  }
}

async function bulkActivateCostItems() {
  if (!canManageCostDb.value || !selectedDraftCostItemCount.value) return
  try {
    await ElMessageBox.confirm(
      `确认将选中条目中的 ${selectedDraftCostItemCount.value} 条 draft 批量核定为 active？`,
      '批量核定 active',
      {
        type: 'warning',
        confirmButtonText: '确认核定',
        cancelButtonText: '返回',
      },
    )
  } catch {
    return
  }
  costBulkSubmitting.value = true
  try {
    const response = await api.post('/admin/cost-items/bulk-status', {
      item_ids: selectedCostItemIds.value,
      target_status: 'active',
      reason: '批量核定',
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
  if (!canManageCostDb.value || !selectedActiveCostItemCount.value) return
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
  if (!canManageCostDb.value) return
  resetCostItemForm('create')
  costItemDialog.visible = true
}

async function openCostItemEdit(row) {
  if (!canManageCostDb.value || row.status === 'archived') return
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
  if (!canManageCostDb.value || row.status !== 'draft') return
  try {
    await ElMessageBox.confirm('确认启用这条成本数据？', '启用成本条目', {
      type: 'warning',
      confirmButtonText: '确认启用',
      cancelButtonText: '返回',
    })
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/cost-items/${row.id}/activate`)
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
  if (!canManageCostDb.value || row.status !== 'active') return
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
  if (!canManageCostDb.value || row.status === 'archived') return
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
  if (!canManageCostDb.value) return
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
