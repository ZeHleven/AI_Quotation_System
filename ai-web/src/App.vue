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
              <p class="eyebrow">Phase 3</p>
              <h2>执行任务</h2>
            </div>
            <div class="heading-actions">
              <el-button v-if="canCreateExecutionTask" :icon="Plus" type="primary" @click="openExecutionCreate">
                新建任务
              </el-button>
              <el-button :icon="Refresh" plain @click="loadExecutionTasks">刷新</el-button>
            </div>
          </div>
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
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
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
const dashboardRange = ref('last_30_days')
const dashboardTab = ref('quote')
const dashboardFeature = reactive({ quoteDisabled: false, responseDisabled: false, executionDisabled: false })
const executionFeatureDisabled = ref(false)
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

const executionDrawer = reactive({
  visible: false,
  loading: false,
  task: null,
})

const roles = computed(() => session.user?.roles || [])
const canMutateRoles = computed(() => roles.value.includes('system_admin'))
const canAccessPermissions = computed(() => roles.value.includes('system_admin') || roles.value.includes('admin'))
const canViewDashboard = computed(() => canAccessPermissions.value || roles.value.includes('viewer'))
const canViewQuoteOperations = computed(() => canAccessPermissions.value)
const canViewExecution = computed(() => canAccessPermissions.value || roles.value.includes('staff') || roles.value.includes('manager'))
const canCreateExecutionTask = computed(() => canAccessPermissions.value)
const canOpenLegacyQuote = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canOpenLegacyAdmin = computed(() => canAccessPermissions.value)
const visibleDailyTrends = computed(() => (quoteDashboard.value?.daily_trends || []).filter((item) => item.sample_count > 0).slice(-12))
const visibleResponseSources = computed(() => (responseDashboard.value?.by_source || []).slice(0, 12))
const visibleResponseResponders = computed(() => (responseDashboard.value?.by_responder || []).slice(0, 12))
const visibleExecutionTrends = computed(() => (executionDashboard.value?.daily_trends || []).filter((item) => item.task_count > 0).slice(-12))
const visibleExecutionAssignees = computed(() => (executionDashboard.value?.by_assignee || []).slice(0, 12))
const executionAssigneeOptions = computed(() =>
  users.value.filter((user) => user.is_active !== false && user.roles?.some((role) => ['system_admin', 'admin', 'staff', 'manager'].includes(role))),
)

function routeFromPath(path) {
  if (path === '/login') return 'login'
  if (path === '/admin/dashboard') return 'dashboard'
  if (path === '/admin/execution') return 'execution'
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

function formatAmount(value) {
  if (value === null || value === undefined) return '-'
  return Number(value).toLocaleString('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 0,
  })
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
  try {
    const response = await api.get(`/quote/jobs/${row.job_id}`)
    quoteJobDrawer.job = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '任务详情加载失败'))
  } finally {
    quoteJobDrawer.loading = false
  }
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
