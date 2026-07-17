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
      <div class="topbar-actions" v-if="session.user">
        <el-tag effect="plain">{{ session.user.username }}</el-tag>
        <el-button :icon="SwitchButton" plain @click="logout">退出</el-button>
      </div>
    </header>

    <main v-if="routeName === 'login'" class="login-layout">
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

    <main v-else class="workspace">
      <aside class="sidebar">
        <div class="sidebar-header">
          <strong>管理工作台</strong>
          <span>按业务流程分组</span>
        </div>
        <nav class="sidebar-nav" aria-label="后台导航">
          <div
            class="nav-group"
            v-if="canOpenLegacyQuote || canViewBudgetProjects || canViewDashboard || canViewProjectProgress || canViewMyProjectTasks"
          >
            <p class="nav-group-label">核心工作台</p>
            <button
              v-if="canOpenLegacyQuote"
              :class="['nav-item', { active: routeName === 'quoteNew' }]"
              type="button"
              @click="navigate('/quote/new')"
            >
              <el-icon><Document /></el-icon>
              <span>新建报价</span>
            </button>
            <button
              v-if="canViewBudgetProjects"
              :class="['nav-item', { active: ['budgetProjects', 'budgetProjectDetail'].includes(routeName) }]"
              type="button"
              @click="navigate('/admin/budget-projects')"
            >
              <el-icon><Tickets /></el-icon>
              <span>预算项目</span>
            </button>
            <button
              v-if="canViewDashboard"
              :class="['nav-item', { active: routeName === 'dashboard' }]"
              type="button"
              @click="navigate('/admin/dashboard')"
            >
              <el-icon><DataAnalysis /></el-icon>
              <span>经营总览</span>
            </button>
            <button
              v-if="canViewProjectProgress"
              :class="['nav-item', { active: ['projects', 'projectDetail'].includes(routeName) }]"
              type="button"
              @click="navigate('/admin/projects')"
            >
              <el-icon><TrendCharts /></el-icon>
              <span>项目进度</span>
            </button>
            <button
              v-if="canViewMyProjectTasks"
              :class="['nav-item', { active: routeName === 'projectMyTasks' }]"
              type="button"
              @click="navigate('/admin/project-tasks/my')"
            >
              <el-icon><DocumentChecked /></el-icon>
              <span>我的项目任务</span>
            </button>
          </div>
          <div
            class="nav-group"
            v-if="canViewBusinessLedger || canViewExecution || canViewBidding"
          >
            <p class="nav-group-label">业务协同</p>
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
              v-if="canViewExecution"
              :class="['nav-item', { active: routeName === 'execution' }]"
              type="button"
              @click="navigate('/admin/execution')"
            >
              <el-icon><Clock /></el-icon>
              <span>执行任务</span>
            </button>
            <button
              v-if="canViewBidding"
              :class="['nav-item', { active: routeName === 'bidding' }]"
              type="button"
              @click="navigate('/admin/bidding')"
            >
              <el-icon><DocumentChecked /></el-icon>
              <span>智能投标</span>
            </button>
          </div>
          <div
            class="nav-group"
            v-if="canViewRequirementStandardization || canViewCostMeasurement || canViewCostDb || canViewAccountQuotas || canViewEnterpriseProfile"
          >
            <p class="nav-group-label">数据资产</p>
            <button
              v-if="canViewRequirementStandardization"
              :class="['nav-item', { active: routeName === 'requirementStandardization' }]"
              type="button"
              @click="navigate('/admin/requirement-standardization')"
            >
              <el-icon><Tickets /></el-icon>
              <span>需求单标准化</span>
            </button>
            <button
              v-if="canViewCostMeasurement"
              :class="['nav-item', { active: routeName === 'costMeasurement' }]"
              type="button"
              @click="navigate('/admin/cost-measurement')"
            >
              <el-icon><DataAnalysis /></el-icon>
              <span>&#25104;&#26412;&#27979;&#31639;</span>
            </button>
            <button
              v-if="canViewCostDb"
              :class="['nav-item', { active: routeName === 'costDb' }]"
              type="button"
              @click="navigate('/admin/cost-db')"
            >
              <el-icon><Document /></el-icon>
              <span>企业定额主库</span>
            </button>
            <button
              v-if="canViewAccountQuotas"
              :class="['nav-item', { active: routeName === 'accountQuotas' }]"
              type="button"
              @click="navigate('/admin/account-quotas')"
            >
              <el-icon><Document /></el-icon>
              <span>账户定额库</span>
            </button>
            <button
              v-if="canViewEnterpriseProfile"
              :class="['nav-item', { active: routeName === 'enterpriseProfile' }]"
              type="button"
              @click="navigate('/admin/enterprise-profile')"
            >
              <el-icon><Document /></el-icon>
              <span>企业资料库</span>
            </button>
          </div>
          <div class="nav-group" v-if="canViewDwgTrial || canViewAgentCenter">
            <p class="nav-group-label">智能工具</p>
            <button
              v-if="canViewDwgTrial"
              :class="['nav-item', { active: routeName === 'dwgTrial' }]"
              type="button"
              @click="navigate('/admin/dwg-trial')"
            >
              <el-icon><Upload /></el-icon>
              <span>图纸识图</span>
            </button>
            <button
              v-if="canViewAgentCenter"
              :class="['nav-item', { active: routeName === 'agentCenter' }]"
              type="button"
              @click="navigate('/admin/agent-center')"
            >
              <el-icon><DataAnalysis /></el-icon>
              <span>智能助手</span>
            </button>
          </div>
          <div class="nav-group" v-if="canAccessPermissions || canOpenLegacyAdmin">
            <p class="nav-group-label">系统管理</p>
            <button
              v-if="canAccessPermissions"
              :class="['nav-item', { active: routeName === 'permissions' }]"
              type="button"
              @click="navigate('/admin/permissions')"
            >
              <el-icon><Tickets /></el-icon>
              <span>账号与权限</span>
            </button>
            <button v-if="canOpenLegacyAdmin" class="nav-item" type="button" @click="openLegacy('/admin.html')">
              <el-icon><Setting /></el-icon>
              <span>管理设置</span>
            </button>
          </div>
        </nav>
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

        <div v-else-if="routeName === 'noAccess'" class="center-state">
          <el-icon><Lock /></el-icon>
          <h2>暂无可用功能</h2>
          <p>账号已登录，但尚未分配可用模块，请联系系统管理员。</p>
          <el-button type="primary" plain @click="logout">退出登录</el-button>
        </div>

        <div v-else-if="state.error === 'feature_disabled'" class="center-state">
          <el-icon><DataAnalysis /></el-icon>
          <h2>功能未开启</h2>
          <p>经营总览暂不可用，请联系管理员确认服务状态。</p>
        </div>

        <template v-else-if="routeName === 'agentCenter'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">智能审查</p>
              <h2>智能助手</h2>
            </div>
            <div class="heading-actions">
              <el-button :icon="Refresh" plain @click="refreshAgentCenter">刷新</el-button>
            </div>
          </div>

          <el-alert
            v-if="agentCenterFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="智能助手暂不可用"
            description="请联系管理员确认功能状态后再使用。"
          />
          <template v-else>
            <section v-if="canManageAgentDailyReview" class="dashboard-section agent-daily-panel">
              <div class="section-title">
                <el-icon><Clock /></el-icon>
                <span>每日自动后审计</span>
                <small>已下发报价单 · 定时扫描 · 不生成待办</small>
              </div>
              <div class="agent-daily-toolbar">
                <el-date-picker
                  v-model="agentDailyDate"
                  type="date"
                  value-format="YYYY-MM-DD"
                  placeholder="选择复核日期"
                  @change="refreshAgentDailyReview"
                />
                <el-button :icon="Refresh" plain :loading="agentDailyLoading" @click="refreshAgentDailyReview">
                  刷新概览
                </el-button>
                <el-button v-if="false" type="primary" plain :loading="agentDailyLoading" @click="runDailyQuoteReview(false)">
                  扫描当天已下发报价
                </el-button>
              </div>
              <el-alert
                v-if="agentDailyFeatureDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="每日自动后审计暂不可用"
                description="打开 FEATURE_AGENT_DAILY_REVIEW=true 后，系统会按定时任务扫描当天确认下发的报价单。"
              />
              <template v-else>
                <div
                  v-if="false && agentTodoSummary"
                  class="agent-todo-strip"
                  :class="{ urgent: agentTodoSummary.urgent_count > 0 }"
                >
                  <div class="agent-todo-main">
                    <span>今日后审计</span>
                    <strong>{{ agentTodoStatusLabel(agentTodoSummary.status) }}</strong>
                    <small>
                      待处理 {{ agentTodoSummary.todo_count || 0 }} 项 · 高风险 {{ agentTodoSummary.metrics?.high_risk_run_count || 0 }} 单 · 预计可省 {{ formatAmount(agentTodoSummary.metrics?.open_estimated_saving_amount || 0) }}
                    </small>
                  </div>
                  <div class="agent-todo-list">
                    <el-tag
                      v-for="todo in (agentTodoSummary.todos || []).slice(0, 3)"
                      :key="todo.key"
                      size="small"
                      :type="agentTodoSeverityTagType(todo.severity)"
                    >
                      {{ todo.title }}{{ todo.count ? ` ${todo.count}` : '' }}
                    </el-tag>
                    <span v-if="!(agentTodoSummary.todos || []).length">暂无待处理事项</span>
                  </div>
                  <el-button
                    size="small"
                    :type="agentTodoSummary.urgent_count > 0 ? 'danger' : 'primary'"
                    plain
                    :loading="agentDailyLoading"
                    @click="handleAgentTodoPrimaryAction"
                  >
                    {{ agentTodoPrimaryActionLabel(agentTodoSummary.primary_action) }}
                  </el-button>
                </div>
                <div v-if="false && agentClosureSummary" class="agent-closure-panel">
                  <div class="agent-closure-header">
                    <div>
                      <strong>闭环效果</strong>
                      <small>处理时限、闭环率和已确认节省金额。</small>
                    </div>
                    <div class="agent-closure-actions">
                      <el-radio-group
                        v-model="agentClosureDays"
                        size="small"
                        @change="refreshAgentClosureSummary"
                      >
                        <el-radio-button :label="7">7 天</el-radio-button>
                        <el-radio-button :label="30">30 天</el-radio-button>
                      </el-radio-group>
                      <el-button :icon="Refresh" size="small" plain :loading="agentClosureLoading" @click="loadAgentClosureSummary">
                        刷新
                      </el-button>
                    </div>
                  </div>
                  <div class="metric-grid agent-closure-metrics">
                    <div class="metric-card">
                      <span>闭环率</span>
                      <strong>{{ formatRate(agentClosureSummary.metrics?.closure_rate ?? 1) }}</strong>
                      <small>已处理 {{ agentClosureSummary.metrics?.handled_count || 0 }} / 总建议 {{ agentClosureSummary.metrics?.suggestion_count || 0 }}</small>
                    </div>
                    <div class="metric-card">
                      <span>未闭环</span>
                      <strong>{{ agentClosureSummary.metrics?.open_count || 0 }}</strong>
                      <small>超期 {{ agentClosureSummary.metrics?.overdue_count || 0 }} 条</small>
                    </div>
                    <div class="metric-card">
                      <span>已确认节省</span>
                      <strong>{{ formatAmount(agentClosureSummary.metrics?.confirmed_saving_amount || 0) }}</strong>
                      <small>仍可省 {{ formatAmount(agentClosureSummary.metrics?.estimated_open_saving_amount || 0) }}</small>
                    </div>
                    <div class="metric-card">
                      <span>处理结果</span>
                      <strong>{{ agentClosureSummary.metrics?.final_confirmed_count || 0 }}</strong>
                      <small>拒绝 {{ agentClosureSummary.metrics?.rejected_count || 0 }} · 人工另改 {{ agentClosureSummary.metrics?.human_modified_count || 0 }}</small>
                    </div>
                  </div>
                </div>
                <div v-if="false" class="agent-scheduler-status">
                  <div>
                    <span>调度状态</span>
                    <strong>{{ agentSchedulerStatusLabel(agentSchedulerStatus?.status) }}</strong>
                  </div>
                  <el-tag size="small" :type="agentSchedulerStatusTagType(agentSchedulerStatus?.status)">
                    {{ agentSchedulerStatusLabel(agentSchedulerStatus?.status) }}
                  </el-tag>
                  <small>
                    计划 {{ agentSchedulerStatus?.scheduled_at || '-' }} · 最近执行 {{ agentSchedulerStatus?.run?.started_at || '-' }}
                  </small>
                  <small v-if="agentSchedulerStatus?.run">
                    候选 {{ agentSchedulerStatus.run.candidate_count || 0 }} 单 · 新增 {{ agentSchedulerStatus.run.created_run_count || 0 }} 单 · 跳过 {{ agentSchedulerStatus.run.skipped_duplicate_count || 0 }} 单 · 失败 {{ agentSchedulerStatus.run.failed_count || 0 }} 单
                  </small>
                </div>
                <el-alert
                  v-if="agentSchedulerStatus?.status === 'failed' || agentSchedulerStatus?.status === 'missed'"
                  class="dashboard-alert"
                  type="warning"
                  show-icon
                  :closable="false"
                  :title="agentSchedulerStatus.status === 'failed' ? '今日自动复核执行失败' : '今日自动复核错过补跑窗口'"
                  :description="agentSchedulerStatus?.run?.error_message || '请查看调度记录和后端日志，确认自动扫描是否需要重新触发。'"
                />
                <div class="metric-grid agent-daily-metrics">
                  <div class="metric-card">
                    <span>当天已下发</span>
                    <strong>{{ agentDailySummary?.candidate_count ?? 0 }}</strong>
                    <small>来自报价历史</small>
                  </div>
                  <div class="metric-card">
                    <span>已复核</span>
                    <strong>{{ agentDailySummary?.run_count ?? 0 }}</strong>
                    <small>高风险 {{ agentDailySummary?.high_risk_run_count ?? 0 }} 单</small>
                  </div>
                  <div class="metric-card">
                    <span>风险记录</span>
                    <strong>{{ agentDailySummary?.audit_record_count ?? 0 }}</strong>
                    <small>高风险 {{ agentDailySummary?.audit_high_risk_record_count ?? 0 }} 条</small>
                  </div>
                  <div class="metric-card">
                    <span>人工改价</span>
                    <strong>{{ agentDailySummary?.audit_manual_modified_count ?? 0 }}</strong>
                    <small>记录修改前后</small>
                  </div>
                  <div class="metric-card">
                    <span>联网市场价参考</span>
                    <strong>{{ agentDailySummary?.audit_market_search_result_count ?? 0 }}</strong>
                    <small>覆盖 {{ agentDailySummary?.audit_market_search_covered_line_count ?? 0 }} 条</small>
                  </div>
                </div>
                <el-table
                  v-if="false"
                  v-loading="agentDailyLoading"
                  :data="agentPendingSuggestions"
                  size="small"
                  class="operations-table"
                  empty-text="暂无待处理建议"
                >
                  <el-table-column prop="created_at" label="时间" min-width="145">
                    <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
                  </el-table-column>
                  <el-table-column label="报价任务" min-width="150" show-overflow-tooltip>
                    <template #default="{ row }">{{ displayQuoteJobNumber(row.run?.target_number || row.target_id) }}</template>
                  </el-table-column>
                  <el-table-column label="建议" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.title }}</template>
                  </el-table-column>
                  <el-table-column label="预计节省" width="120">
                    <template #default="{ row }">{{ formatAmount(row.estimated_saving_amount || 0) }}</template>
                  </el-table-column>
                  <el-table-column label="状态" width="105">
                    <template #default="{ row }">
                      <el-tag size="small" :type="agentSuggestionStatusTagType(row.status)">
                        {{ agentSuggestionStatusLabel(row.status) }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="220" fixed="right">
                    <template #default="{ row }">
                      <div class="agent-pending-actions">
                        <el-button size="small" text type="primary" @click="openAgentRunAndFocus(row.run)">查看定位</el-button>
                        <template v-if="row.status === 'pending_review' || row.status === 'approved' || row.status === 'draft_generated'">
                          <el-button
                            v-if="isAgentActionableSuggestion(row)"
                            size="small"
                            text
                            type="success"
                            @click="adoptAgentSuggestionOneClick(row)"
                          >
                            一键采用
                          </el-button>
                          <el-button
                            v-else-if="row.status === 'pending_review'"
                            size="small"
                            text
                            type="primary"
                            @click="markAgentSuggestionReviewed(row)"
                          >
                            标记已处理
                          </el-button>
                          <el-button
                            v-if="row.status === 'pending_review' && isAgentActionableSuggestion(row)"
                            size="small"
                            text
                            @click="decideAgentSuggestion(row, 'reject')"
                          >
                            不采用
                          </el-button>
                        </template>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
                <div v-if="false" class="table-pagination">
                  <el-pagination
                    v-model:current-page="agentPendingSuggestionPage"
                    layout="prev, pager, next"
                    :page-size="agentPendingSuggestionPageSize"
                    :total="agentPendingSuggestionTotal"
                    @current-change="loadAgentPendingSuggestions"
                  />
                </div>
                <div v-if="false" class="agent-scheduler-history">
                  <div class="agent-scheduler-history-header">
                    <div>
                      <strong>最近调度记录</strong>
                      <small>自动复核执行留痕，可用于问题复查和失败补扫。</small>
                    </div>
                    <div class="agent-scheduler-history-actions">
                      <el-radio-group
                        v-model="agentSchedulerHistoryDays"
                        size="small"
                        @change="refreshAgentSchedulerHistory"
                      >
                        <el-radio-button :label="7">7 天</el-radio-button>
                        <el-radio-button :label="30">30 天</el-radio-button>
                      </el-radio-group>
                      <el-button :icon="Refresh" size="small" plain :loading="agentSchedulerHistoryLoading" @click="loadAgentSchedulerHistory">
                        刷新
                      </el-button>
                    </div>
                  </div>
                  <el-table
                    v-loading="agentSchedulerHistoryLoading"
                    :data="agentSchedulerHistory"
                    size="small"
                    class="operations-table"
                    empty-text="暂无调度记录"
                  >
                    <el-table-column prop="run_date" label="日期" width="110" />
                    <el-table-column label="状态" width="120">
                      <template #default="{ row }">
                        <el-tag size="small" :type="agentSchedulerStatusTagType(row.status)">
                          {{ agentSchedulerStatusLabel(row.status) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="调度结果" min-width="230" show-overflow-tooltip>
                      <template #default="{ row }">
                        候选 {{ row.candidate_count || 0 }} 单 · 新增 {{ row.created_run_count || 0 }} 单 · 跳过 {{ row.skipped_duplicate_count || 0 }} 单 · 失败 {{ row.failed_count || 0 }} 单
                      </template>
                    </el-table-column>
                    <el-table-column label="风险与建议" min-width="220" show-overflow-tooltip>
                      <template #default="{ row }">
                        已复核 {{ row.daily_summary?.run_count || 0 }} 单 · 高风险 {{ row.daily_summary?.high_risk_run_count || 0 }} 单 · 待处理 {{ row.daily_summary?.open_suggestion_count || 0 }} 条
                      </template>
                    </el-table-column>
                    <el-table-column label="预计可省" width="120">
                      <template #default="{ row }">{{ formatAmount(row.daily_summary?.open_estimated_saving_amount || 0) }}</template>
                    </el-table-column>
                    <el-table-column label="闭环 SLA" min-width="180" show-overflow-tooltip>
                      <template #default="{ row }">
                        闭环率 {{ formatRate(row.daily_summary?.closure_rate ?? 1) }} · 已处理 {{ row.daily_summary?.handled_count || 0 }} · 超期 {{ row.daily_summary?.overdue_count || 0 }}
                      </template>
                    </el-table-column>
                    <el-table-column label="操作" width="150" fixed="right">
                      <template #default="{ row }">
                        <el-button
                          v-if="false && row.manual_rescan_available"
                          size="small"
                          text
                          type="primary"
                          :loading="agentDailyLoading"
                          @click="rescanAgentSchedulerHistoryRow(row)"
                        >
                          查看结果
                        </el-button>
                        <span v-else>{{ agentSchedulerNextActionLabel(row.next_action) }}</span>
                      </template>
                    </el-table-column>
                  </el-table>
                  <div class="table-pagination">
                    <el-pagination
                      v-model:current-page="agentSchedulerHistoryPage"
                      layout="prev, pager, next"
                      :page-size="agentSchedulerHistoryPageSize"
                      :total="agentSchedulerHistoryTotal"
                      @current-change="loadAgentSchedulerHistory"
                    />
                  </div>
                </div>
              </template>
            </section>

            <section class="dashboard-section agent-run-panel">
              <div class="section-title">
                <el-icon><DataAnalysis /></el-icon>
                <span>手动后审计</span>
                <small>仅审计已确认下发报价 · 不自动改价 · 不自动下发</small>
              </div>
              <div class="agent-run-form">
                <el-input
                  v-model="agentQuoteJobId"
                  clearable
                  placeholder="输入已下发报价任务号 / ID"
                  @keyup.enter="runQuoteReviewAgent()"
                />
                <el-button
                  type="primary"
                  :icon="Search"
                  :loading="agentCenterLoading"
                  @click="runQuoteReviewAgent()"
                >
                  立即审计
                </el-button>
              </div>
            </section>

            <div class="dashboard-split agent-center-grid">
              <section class="dashboard-section agent-run-history-panel">
                <div class="section-title">
                  <el-icon><Document /></el-icon>
                  <span>运行记录</span>
                  <small>共 {{ agentRunTotal }} 条</small>
                </div>
                <div
                  v-loading="agentCenterLoading"
                  class="agent-run-compact-list"
                >
                  <button
                    v-for="row in agentRuns"
                    :key="row.run_id"
                    :class="['agent-run-compact-item', { active: row.run_id === agentRunDetail?.run_id }]"
                    type="button"
                    @click="openAgentRun(row)"
                  >
                    <span class="agent-run-compact-top">
                      <el-tag size="small" :type="agentRiskTagType(row.risk_level)">
                        {{ agentRiskLabel(row.risk_level) }}
                      </el-tag>
                      <small>{{ formatDate(row.created_at) }}</small>
                    </span>
                    <strong>{{ displayQuoteJobNumber(row.target_number || row.target_id) }}</strong>
                    <small>{{ agentRecommendationLabel(row.recommendation) }}</small>
                  </button>
                  <el-empty v-if="!agentRuns.length" description="暂无 Agent 运行记录" />
                </div>
                <div class="table-pagination">
                  <el-pagination
                    v-model:current-page="agentRunPage"
                    layout="prev, pager, next"
                    :page-size="agentRunPageSize"
                    :total="agentRunTotal"
                    @current-change="loadAgentRuns"
                  />
                </div>
              </section>

              <section class="dashboard-section agent-result-panel">
                <div class="section-title">
                  <el-icon><Warning /></el-icon>
                  <span>后审计结果</span>
                  <small>{{ agentRunDetail?.run_id || '未选择' }}</small>
                </div>
                <el-empty v-if="!agentRunDetail" description="选择一条运行记录查看后审计详情" />
                <template v-else>
                  <div v-if="false" class="metric-grid agent-metric-grid">
                    <div class="metric-card">
                      <span>风险等级</span>
                      <strong>{{ agentRiskLabel(agentRunDetail.risk_level) }}</strong>
                      <small>{{ agentRecommendationLabel(agentRunDetail.recommendation) }}</small>
                    </div>
                    <div class="metric-card">
                      <span>确认行</span>
                      <strong>{{ agentRunDetail.output?.metrics?.requirement_row_count ?? 0 }}</strong>
                      <small>预审 {{ agentRunDetail.output?.metrics?.preview_row_count ?? 0 }} 行</small>
                    </div>
                    <div class="metric-card">
                      <span>缺失/占位</span>
                      <strong>{{ agentRunDetail.output?.metrics?.missing_count ?? 0 }} / {{ agentRunDetail.output?.metrics?.placeholder_count ?? 0 }}</strong>
                      <small>下发前重点复核</small>
                    </div>
                  </div>
                  <p class="agent-summary">{{ agentRunDetail.summary }}</p>
                  <div v-if="agentIsAuditRun" class="agent-audit-kpi-grid">
                    <div class="agent-audit-kpi">
                      <span>审计记录</span>
                      <strong>{{ agentAuditSummary.audit_record_count ?? 0 }}</strong>
                    </div>
                    <div class="agent-audit-kpi">
                      <span>高风险</span>
                      <strong>{{ agentAuditSummary.high_risk_count ?? 0 }}</strong>
                    </div>
                    <div class="agent-audit-kpi">
                      <span>人工改价</span>
                      <strong>{{ agentAuditSummary.manual_modified_count ?? 0 }}</strong>
                    </div>
                    <div class="agent-audit-kpi">
                      <span>联网来源</span>
                      <strong>{{ agentAuditSummary.market_search_result_count ?? 0 }}</strong>
                    </div>
                  </div>
                  <div v-else class="agent-saving-strip">
                    <span>预计节省</span>
                    <strong>{{ formatAmount(agentRunDetail.output?.saving_summary?.estimated_total_saving_amount ?? 0) }}</strong>
                    <small>{{ agentRunDetail.output?.saving_summary?.saving_suggestion_count ?? 0 }} 条省钱建议</small>
                  </div>
                  <div class="agent-review-toolbar">
                    <el-button size="small" plain @click="toggleAgentExplanation">
                      {{ agentShowExplanation ? '收起 AI 解释' : '查看 AI 解释' }}
                    </el-button>
                  </div>

                  <div v-if="agentShowExplanation" class="agent-llm-panel" v-loading="agentLlmExplanationLoading">
                    <div class="agent-llm-header">
                      <div>
                        <strong>AI解释增强</strong>
                        <small>风险解释、修改前后与市场价辅助说明。</small>
                      </div>
                      <div class="agent-llm-actions">
                        <el-tag
                          v-if="agentLlmExplanation"
                          size="small"
                          :type="agentLlmSourceTagType(agentLlmExplanation)"
                        >
                          {{ agentLlmSourceLabel(agentLlmExplanation) }}
                        </el-tag>
                        <el-button
                          size="small"
                          plain
                          :loading="agentLlmExplanationLoadingMode === 'rule'"
                          :disabled="agentLlmExplanationLoadingMode === 'deepseek'"
                          @click="loadAgentLlmExplanation('rule')"
                        >
                          规则解释
                        </el-button>
                        <el-button
                          size="small"
                          type="primary"
                          plain
                          :loading="agentLlmExplanationLoadingMode === 'deepseek'"
                          :disabled="agentLlmExplanationLoadingMode === 'rule'"
                          @click="loadAgentLlmExplanation('deepseek')"
                        >
                          DeepSeek解释
                        </el-button>
                      </div>
                    </div>
                    <template v-if="agentLlmExplanation">
                      <section class="agent-llm-brief">
                        <strong>{{ agentLlmExplanation.headline }}</strong>
                        <small v-if="agentLlmExplanation.business_summary">
                          {{ agentLlmExplanation.business_summary }}
                        </small>
                      </section>

                      <div
                        v-if="(agentLlmExplanation.review_focus || []).length || (agentLlmExplanation.decision_checklist || []).length"
                        class="agent-llm-focus-grid"
                      >
                        <section v-if="(agentLlmExplanation.review_focus || []).length">
                          <strong>复核重点</strong>
                          <ul>
                            <li v-for="item in agentLlmExplanation.review_focus" :key="item">{{ item }}</li>
                          </ul>
                        </section>
                        <section v-if="(agentLlmExplanation.decision_checklist || []).length">
                          <strong>确认清单</strong>
                          <ul>
                            <li v-for="item in agentLlmExplanation.decision_checklist" :key="item">{{ item }}</li>
                          </ul>
                        </section>
                      </div>

                      <section v-if="agentLlmBeforeAfterRows.length" class="agent-llm-block">
                        <div class="agent-llm-section-title">
                          <strong>修改前后解释</strong>
                          <small>{{ agentLlmBeforeAfterRows.length }} 条</small>
                        </div>
                        <el-table
                          :data="agentLlmBeforeAfterRows"
                          size="small"
                          border
                          class="agent-llm-table"
                        >
                          <el-table-column label="条目" min-width="220">
                            <template #default="{ row }">
                              <div class="agent-table-main-cell">
                                <strong>{{ row.target_label || '未定位到具体报价行' }}</strong>
                                <el-tag size="small" :type="row.manual_modified ? 'warning' : 'info'">
                                  {{ row.manual_modified ? '人工改价' : '下发留痕' }}
                                </el-tag>
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="原预审风险" min-width="180">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.original_risk || '-' }}</span>
                            </template>
                          </el-table-column>
                          <el-table-column label="最终下发状态" min-width="180">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.confirmed_state || '-' }}</span>
                            </template>
                          </el-table-column>
                          <el-table-column label="解释" min-width="260">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.explanation || '-' }}</span>
                            </template>
                          </el-table-column>
                        </el-table>
                      </section>

                      <section v-if="agentMarketReferenceRows.length" class="agent-llm-block">
                        <div class="agent-llm-section-title">
                          <strong>联网价格来源</strong>
                          <small>按报价条目对应深圳 / 东莞搜索结果</small>
                        </div>
                        <el-table
                          :data="agentMarketReferenceRows"
                          size="small"
                          border
                          class="agent-llm-table agent-market-table"
                        >
                          <el-table-column type="expand" width="42">
                            <template #default="{ row }">
                              <div class="agent-audit-expand">
                                <section>
                                  <strong>联网摘要</strong>
                                  <p>{{ row.explanation || '暂无可用联网摘要。' }}</p>
                                </section>
                                <section v-if="row.sources.length">
                                  <strong>来源明细</strong>
                                  <div class="agent-market-source-table">
                                    <div
                                      v-for="source in row.sources"
                                      :key="`${row._row_key}-${source.url}`"
                                      class="agent-market-source-row"
                                    >
                                      <span>{{ source.city || '-' }}</span>
                                      <a :href="source.url" target="_blank" rel="noreferrer">
                                        {{ source.title || source.url }}
                                      </a>
                                      <small>{{ source.price_text || '未提取到明确价格' }}</small>
                                      <small>{{ source.date || '-' }}</small>
                                    </div>
                                  </div>
                                </section>
                                <el-empty v-else description="本条没有返回可点击来源" />
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="报价条目" min-width="220">
                            <template #default="{ row }">
                              <div class="agent-table-main-cell">
                                <strong>{{ row.item_name || '未命名报价条目' }}</strong>
                                <small v-if="row.target_label">{{ row.target_label }}</small>
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="下发单价" width="120" align="right">
                            <template #default="{ row }">{{ formatPrice(row.confirmed_unit_price) }}</template>
                          </el-table-column>
                          <el-table-column label="深圳搜索价" min-width="150">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.city_price_texts['深圳'] || '-' }}</span>
                            </template>
                          </el-table-column>
                          <el-table-column label="东莞搜索价" min-width="150">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.city_price_texts['东莞'] || '-' }}</span>
                            </template>
                          </el-table-column>
                          <el-table-column label="可信度/来源" width="130">
                            <template #default="{ row }">
                              <div class="agent-audit-status-cell">
                                <el-tag size="small" :type="agentMarketConfidenceTag(row.confidence)">
                                  {{ agentMarketConfidenceLabel(row.confidence) }}
                                </el-tag>
                                <small>{{ row.sources.length }} 条来源</small>
                              </div>
                            </template>
                          </el-table-column>
                        </el-table>
                      </section>

                      <section v-if="agentLlmRiskRows.length" class="agent-llm-block">
                        <div class="agent-llm-section-title">
                          <strong>风险解释</strong>
                          <small>{{ agentLlmRiskRows.length }} 条</small>
                        </div>
                        <el-table :data="agentLlmRiskRows" size="small" border class="agent-llm-table">
                          <el-table-column label="级别" width="86">
                            <template #default="{ row }">
                              <el-tag size="small" :type="agentSeverityTagType(row.severity)">
                                {{ row.severity_label || agentSeverityLabel(row.severity) }}
                              </el-tag>
                            </template>
                          </el-table-column>
                          <el-table-column label="风险" min-width="220">
                            <template #default="{ row }">
                              <div class="agent-table-main-cell">
                                <strong>{{ row.title }}</strong>
                                <small v-if="row.target_label">{{ row.target_label }}</small>
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="解释" min-width="260">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.explanation || '-' }}</span>
                            </template>
                          </el-table-column>
                          <el-table-column label="处理口径" min-width="220">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.handling_advice || '-' }}</span>
                            </template>
                          </el-table-column>
                        </el-table>
                      </section>

                      <section v-if="agentLlmSuggestionRows.length" class="agent-llm-block">
                        <div class="agent-llm-section-title">
                          <strong>建议优先级</strong>
                          <small>{{ agentLlmSuggestionRows.length }} 条</small>
                        </div>
                        <el-table :data="agentLlmSuggestionRows" size="small" border class="agent-llm-table">
                          <el-table-column label="优先级" width="96">
                            <template #default="{ row }">
                              <el-tag size="small" :type="agentPriorityTagType(row.priority)">
                                {{ row.priority_label || agentPriorityLabel(row.priority) }}
                              </el-tag>
                            </template>
                          </el-table-column>
                          <el-table-column label="建议" min-width="220">
                            <template #default="{ row }">
                              <div class="agent-table-main-cell">
                                <strong>{{ row.title }}</strong>
                                <small v-if="row.target_label">{{ row.target_label }}</small>
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="原因" min-width="260">
                            <template #default="{ row }">
                              <span class="agent-table-muted">{{ row.reason || '-' }}</span>
                            </template>
                          </el-table-column>
                          <el-table-column label="预计节省" width="120" align="right">
                            <template #default="{ row }">
                              {{ row.estimated_saving_amount ? formatAmount(row.estimated_saving_amount) : '-' }}
                            </template>
                          </el-table-column>
                        </el-table>
                      </section>

                      <section v-if="(agentLlmExplanation.uncertainties || []).length" class="agent-llm-block">
                        <div class="agent-llm-section-title">
                          <strong>不确定性</strong>
                        </div>
                        <div class="agent-llm-note-list">
                          <span v-for="item in agentLlmExplanation.uncertainties" :key="item">{{ item }}</span>
                        </div>
                      </section>
                    </template>
                    <el-empty v-else description="暂无解释增强" />
                  </div>

                  <div v-if="agentAuditTableRows.length" class="agent-result-subtitle">修改前后审计</div>
                  <div v-if="agentAuditTableRows.length" class="agent-audit-card-list">
                    <article
                      v-for="row in agentAuditTableRows"
                      :key="row._row_key"
                      :class="['agent-audit-card', `risk-${row.risk_level || 'low'}`]"
                    >
                      <div class="agent-audit-card-head">
                        <div>
                          <el-tag size="small" :type="agentRiskTagType(row.risk_level)">
                            {{ agentRiskLabel(row.risk_level) }}
                          </el-tag>
                          <strong>{{ row.project_name || '未命名报价条目' }}</strong>
                        </div>
                        <el-tag size="small" :type="row.confirmed_quote?.manual_modified ? 'warning' : 'info'">
                          {{ agentAuditConfirmedState(row) }}
                        </el-tag>
                      </div>
                      <small v-if="row.target_label" class="agent-audit-card-target">{{ row.target_label }}</small>

                      <div class="agent-audit-reason-tags">
                        <el-tag
                          v-for="reason in agentAuditRiskReasonItems(row)"
                          :key="`${row._row_key}-${reason.type || reason.label}`"
                          size="small"
                          :type="agentSeverityTagType(reason.severity)"
                        >
                          {{ reason.label }}
                        </el-tag>
                      </div>

                      <div class="agent-audit-compare-grid">
                        <div class="agent-audit-compare-card">
                          <span>工程量</span>
                          <strong>{{ agentAuditQuantity(row.original_preview?.quantity, row.unit) }}</strong>
                          <small>下发 {{ agentAuditQuantity(row.confirmed_quote?.quantity, row.unit) }}</small>
                          <el-tag
                            v-if="agentAuditDeltaText(row, 'quantity') !== '-'"
                            size="small"
                            :type="agentAuditDeltaTagType(row.price_change?.quantity_delta)"
                          >
                            {{ agentAuditDeltaText(row, 'quantity') }}
                          </el-tag>
                        </div>
                        <div class="agent-audit-compare-card">
                          <span>单价</span>
                          <strong>{{ formatPrice(row.original_preview?.unit_price) }}</strong>
                          <small>下发 {{ formatPrice(row.confirmed_quote?.unit_price) }}</small>
                          <el-tag
                            v-if="agentAuditDeltaText(row, 'unit_price') !== '-'"
                            size="small"
                            :type="agentAuditDeltaTagType(row.price_change?.unit_price_delta)"
                          >
                            {{ agentAuditDeltaText(row, 'unit_price') }}
                          </el-tag>
                        </div>
                        <div class="agent-audit-compare-card">
                          <span>合计</span>
                          <strong>{{ formatAmount(row.original_preview?.total_price) }}</strong>
                          <small>下发 {{ formatAmount(row.confirmed_quote?.total_price) }}</small>
                          <el-tag
                            v-if="agentAuditDeltaText(row, 'total_price') !== '-'"
                            size="small"
                            :type="agentAuditDeltaTagType(row.price_change?.total_price_delta)"
                          >
                            {{ agentAuditDeltaText(row, 'total_price') }}
                          </el-tag>
                        </div>
                      </div>

                      <div class="agent-audit-card-sections">
                        <section>
                          <strong>修改前后解释</strong>
                          <p>{{ row.before_after_summary || '-' }}</p>
                        </section>
                        <section>
                          <strong>联网市场价辅助</strong>
                          <p>{{ row.market_search_explanation || '本条暂无联网市场价说明。' }}</p>
                        </section>
                        <section v-if="agentAuditMarketCities(row).length">
                          <strong>深圳 / 东莞价格摘要</strong>
                          <div class="agent-audit-city-grid">
                            <span
                              v-for="city in agentAuditMarketCities(row)"
                              :key="`${row._row_key}-${city.name}`"
                            >
                              {{ city.name }}：{{ city.text }}
                            </span>
                          </div>
                        </section>
                        <section v-if="agentAuditMarketSources(row).length">
                          <strong>联网来源</strong>
                          <div class="agent-audit-source-list">
                            <a
                              v-for="source in agentAuditMarketSources(row)"
                              :key="`${source.city || ''}-${source.url || source.title}`"
                              :href="source.url"
                              target="_blank"
                              rel="noreferrer"
                            >
                              {{ source.city ? `${source.city} · ` : '' }}{{ source.title || source.url || '来源链接' }}
                            </a>
                          </div>
                        </section>
                      </div>
                    </article>
                  </div>

                  <div v-if="!agentIsAuditRun" class="agent-result-subtitle">需要执行的调价 / 省钱建议</div>
                  <div v-if="!agentIsAuditRun" class="agent-detail-list agent-suggestion-list">
                    <article
                      v-for="suggestion in agentActionableSuggestions"
                      :key="suggestion.suggestion_id"
                      class="agent-suggestion-row"
                    >
                      <div class="agent-suggestion-head">
                        <el-tag size="small" :type="agentPriorityTagType(suggestion.priority)">
                          {{ agentPriorityLabel(suggestion.priority) }}
                        </el-tag>
                        <el-tag size="small" type="info">{{ agentSuggestionTypeLabel(suggestion.suggestion_type) }}</el-tag>
                        <el-tag size="small" :type="agentSuggestionStatusTagType(suggestion.status)">
                          {{ agentSuggestionStatusLabel(suggestion.status) }}
                        </el-tag>
                      </div>
                      <div class="agent-suggestion-body">
                        <strong>{{ suggestion.title }}</strong>
                        <small v-if="suggestion.target_label" class="agent-target-label">
                          定位：{{ suggestion.target_label }}
                        </small>
                        <small>{{ suggestion.rationale }}</small>
                        <small v-if="suggestion.risk_note">风险控制：{{ suggestion.risk_note }}</small>
                        <div v-if="suggestion.estimated_saving_amount" class="agent-saving-line">
                          <span>预计节省 {{ formatAmount(suggestion.estimated_saving_amount) }}</span>
                          <span v-if="suggestion.estimated_saving_rate">降幅 {{ formatPercent(suggestion.estimated_saving_rate) }}</span>
                        </div>
                        <div v-if="suggestion.execution_result?.quote_line_patch" class="agent-draft-line">
                          <span>草案单价：{{ formatPrice(suggestion.execution_result.quote_line_patch.unit_price_before) }} → {{ formatPrice(suggestion.execution_result.quote_line_patch.unit_price_after) }}</span>
                          <span>草案合计：{{ formatAmount(suggestion.execution_result.quote_line_patch.total_price_before) }} → {{ formatAmount(suggestion.execution_result.quote_line_patch.total_price_after) }}</span>
                        </div>
                      </div>
                      <div class="agent-suggestion-actions">
                        <template v-if="suggestion.status === 'pending_review' || suggestion.status === 'approved' || suggestion.status === 'draft_generated'">
                          <el-button size="small" type="success" plain @click="adoptAgentSuggestionOneClick(suggestion)">
                            一键采用
                          </el-button>
                          <el-button
                            v-if="suggestion.status === 'pending_review'"
                            size="small"
                            plain
                            @click="decideAgentSuggestion(suggestion, 'reject')"
                          >
                            不采用
                          </el-button>
                        </template>
                      </div>
                    </article>
                    <el-empty v-if="!agentActionableSuggestions.length" description="暂无需要执行的调价 / 省钱建议" />
                  </div>

                  <div class="agent-result-subtitle">风险发现</div>
                  <el-table
                    v-if="(agentRunDetail.findings || []).length"
                    :data="agentRunDetail.findings || []"
                    class="agent-finding-table"
                    size="small"
                    border
                  >
                    <el-table-column label="级别" width="86">
                      <template #default="{ row }">
                        <el-tag size="small" :type="agentSeverityTagType(row.severity)">
                          {{ agentSeverityLabel(row.severity) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="风险" min-width="220">
                      <template #default="{ row }">
                        <div class="agent-table-main-cell">
                          <strong>{{ row.title }}</strong>
                          <small v-if="row.target_label">{{ row.target_label }}</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="审计建议" min-width="260">
                      <template #default="{ row }">
                        <span class="agent-table-muted">{{ row.suggestion || '-' }}</span>
                      </template>
                    </el-table-column>
                  </el-table>
                  <el-empty v-else description="暂无风险发现" />

                  <el-collapse v-if="false" class="agent-tool-collapse">
                    <el-collapse-item title="工具调用轨迹" name="tools">
                      <div
                        v-for="toolCall in agentRunDetail.tool_calls || []"
                        :key="toolCall.id"
                        class="agent-tool-row"
                      >
                        <span>{{ toolCall.tool_name }}</span>
                        <el-tag size="small" :type="toolCall.status === 'success' ? 'success' : 'danger'">
                          {{ toolCall.status }}
                        </el-tag>
                        <small>{{ toolCall.duration_ms ?? 0 }} ms</small>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </template>
              </section>
            </div>
          </template>
        </template>

        <template v-else-if="routeName === 'quoteNew'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">AI Quote</p>
              <h2>新建报价</h2>
              <p class="page-intro">选择合适的输入方式，系统会进入现有报价链路，不会在此页创建报价任务。</p>
            </div>
          </div>

          <section class="quote-entry-hero">
            <div>
              <el-tag type="primary" effect="plain">统一入口</el-tag>
              <h3>从需求到人工预审，一条报价链路</h3>
              <p>文本、图纸和简单 Excel 可直接报价；多 Sheet 或需要人工列映射的需求单，请先标准化确认。</p>
            </div>
          </section>

          <section class="quote-entry-grid" aria-label="选择报价创建方式">
            <article class="quote-entry-card quote-entry-card-primary">
              <div class="quote-entry-card-icon"><el-icon><DataAnalysis /></el-icon></div>
              <div>
                <h3>快速报价</h3>
                <p>直接描述施工需求，或上传图片、图纸截图和简单 Excel 需求单。</p>
              </div>
              <ul>
                <li>适合口述需求、图片和单 Sheet 清单</li>
                <li>进入报价工作台后再提交，支持人工预审</li>
              </ul>
              <el-button type="primary" @click="openQuickQuote('quick')">开始新建报价</el-button>
            </article>

            <article v-if="canViewRequirementStandardization" class="quote-entry-card">
              <div class="quote-entry-card-icon"><el-icon><Tickets /></el-icon></div>
              <div>
                <h3>标准需求单报价</h3>
                <p>上传 .xlsx / .xlsm，人工确认字段和报价行后，再发起报价。</p>
              </div>
              <ul>
                <li>适合多 Sheet、列不固定或需要逐行复核的清单</li>
                <li>未通过校验的行不会进入报价任务</li>
              </ul>
              <el-button plain type="primary" @click="openRequirementQuoteEntry">导入并标准化需求单</el-button>
            </article>
          </section>

          <section class="quote-entry-note">
            <el-icon><Clock /></el-icon>
            <span>已有报价草稿或正在运行的任务，请从报价工作台的历史记录、恢复提示或运营详情继续处理。</span>
          </section>
        </template>

        <template v-else-if="routeName === 'dashboard'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">经营分析</p>
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

          <section class="management-focus-panel" aria-label="管理重点">
            <div class="management-focus-header">
              <div class="management-focus-intro">
                <p class="eyebrow">本轮优先处理</p>
                <h3>管理重点</h3>
                <small>{{ managementFocusSummary }}</small>
              </div>
              <div class="management-focus-links">
                <el-button
                  v-for="link in managementFocusLinks"
                  :key="link.path"
                  size="small"
                  plain
                  @click="openBusinessTarget(link.path)"
                >
                  {{ link.label }}
                </el-button>
              </div>
            </div>

            <div v-if="managementFocusCards.length" class="management-focus-grid">
              <button
                v-for="card in managementFocusCards"
                :key="card.key"
                type="button"
                :class="['management-focus-card', `is-${card.tone}`]"
                @click="openBusinessTarget(card.targetPath)"
              >
                <span>{{ card.title }}</span>
                <strong>{{ card.value }}</strong>
                <small>{{ card.detail }}</small>
                <em>{{ card.action }}</em>
              </button>
            </div>
            <div v-else class="management-focus-empty">
              <strong>当前没有需要立即处理的项目或执行事项</strong>
              <small>可继续从下方模块查看整体进度、执行效率和经营数据。</small>
            </div>
          </section>

          <el-tabs v-model="dashboardTab" class="dashboard-tabs">
            <el-tab-pane label="经营总览" name="business" :disabled="dashboardFeature.businessDisabled">
              <el-alert
                v-if="dashboardFeature.businessDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="经营总览暂不可用"
              />
              <template v-else>
                <el-alert
                  v-if="businessDashboard?.empty_state"
                  class="dashboard-alert"
                  type="info"
                  show-icon
                  :closable="false"
                  title="暂无经营总览数据，业务数据产生后将自动显示"
                />
                <el-alert
                  v-if="businessSectionErrorCount > 0"
                  class="dashboard-alert"
                  type="warning"
                  show-icon
                  :closable="false"
                  :title="`经营总览有 ${businessSectionErrorCount} 个区块局部降级，其余数据仍可查看`"
                />

                <section class="business-overview-bar">
                  <div class="business-overview-main">
                    <el-tag :type="businessOverallTagType(businessDashboard?.environment?.overall_status)" effect="dark">
                      {{ businessOverallLabel(businessDashboard?.environment?.overall_status) }}
                    </el-tag>
                    <div>
                      <strong>经营总览</strong>
                      <small>只读汇总 · 不展示成本敏感明细</small>
                    </div>
                  </div>
                  <div class="business-overview-meta">
                    <span>更新时间：{{ formatDate(businessDashboard?.generated_at) }}</span>
                    <span>运行状态：{{ businessModeLabel(businessDashboard?.environment?.mode) }}</span>
                  </div>
                  <div class="business-overview-actions">
                    <el-button
                      v-for="link in businessQuickLinks"
                      :key="link.key"
                      size="small"
                      plain
                      @click="openBusinessTarget(link.path)"
                    >
                      {{ link.label }}
                    </el-button>
                  </div>
                </section>

                <section class="business-trial-rail">
                  <article
                    v-for="item in businessTrialReadinessCards"
                    :key="item.key"
                    :class="['business-trial-card', item.tone]"
                  >
                    <span>{{ item.label }}</span>
                    <strong>{{ item.value }}</strong>
                    <small>{{ item.detail }}</small>
                  </article>
                </section>

                <div class="metric-grid business-metric-grid">
                  <button
                    v-for="card in businessMetricCards"
                    :key="card.key"
                    type="button"
                    class="metric-card metric-card-button"
                    @click="openBusinessTarget(card.targetPath)"
                  >
                    <span>{{ card.title }}</span>
                    <strong>{{ card.value }}</strong>
                    <small>{{ card.subtitle }}</small>
                  </button>
                </div>

                <div class="dashboard-split business-trend-split">
                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><TrendCharts /></el-icon>
                      <span>报价趋势</span>
                      <small>近 {{ businessQuoteTrendRows.length }} 条</small>
                    </div>
                    <div v-if="businessQuoteTrendRows.length" class="business-trend-list">
                      <div
                        v-for="row in businessQuoteTrendRows"
                        :key="row.date"
                        class="business-trend-row"
                      >
                        <span>{{ row.date }}</span>
                        <div class="business-trend-bars">
                          <i class="bar total" :style="{ width: businessBarWidth(row.task_count, businessQuoteTrendMax) }"></i>
                          <i class="bar success" :style="{ width: businessBarWidth(row.success_count, businessQuoteTrendMax) }"></i>
                          <i class="bar warning" :style="{ width: businessBarWidth(row.failed_or_timeout_count, businessQuoteTrendMax) }"></i>
                          <i class="bar pushed" :style="{ width: businessBarWidth(row.pushed_count, businessQuoteTrendMax) }"></i>
                        </div>
                        <small>任务 {{ row.task_count }} · 成功 {{ row.success_count }} · 异常 {{ row.failed_or_timeout_count }} · 下发 {{ row.pushed_count }}</small>
                      </div>
                    </div>
                    <el-empty v-else description="暂无报价趋势数据" />
                  </section>

                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><TrendCharts /></el-icon>
                      <span>项目证据趋势</span>
                      <small>近 {{ businessProjectTrendRows.length }} 条</small>
                    </div>
                    <div v-if="businessProjectTrendRows.length" class="business-trend-list">
                      <div
                        v-for="row in businessProjectTrendRows"
                        :key="row.date"
                        class="business-trend-row"
                      >
                        <span>{{ row.date }}</span>
                        <div class="business-trend-bars">
                          <i class="bar total" :style="{ width: businessBarWidth(row.bypass_gate_event_count, businessProjectTrendMax) }"></i>
                          <i class="bar warning" :style="{ width: businessBarWidth(row.bypassed_missing_evidence_count, businessProjectTrendMax) }"></i>
                          <i class="bar pushed" :style="{ width: businessBarWidth(row.soft_reminder_event_count, businessProjectTrendMax) }"></i>
                        </div>
                        <small>放行 {{ row.bypass_gate_event_count }} · 未补证据 {{ row.bypassed_missing_evidence_count }} · 软提醒 {{ row.soft_reminder_event_count }}</small>
                      </div>
                    </div>
                    <el-empty v-else description="暂无项目证据趋势数据" />
                  </section>
                </div>

                <section class="dashboard-section business-distribution-section">
                  <div class="section-title">
                    <el-icon><Histogram /></el-icon>
                    <span>分布概览</span>
                  </div>
                  <div class="business-distribution-grid">
                    <div
                      v-for="group in businessDistributionGroups"
                      :key="group.key"
                      class="business-distribution-card"
                    >
                      <strong>{{ group.title }}</strong>
                      <div v-if="group.rows.length" class="business-distribution-list">
                        <div
                          v-for="row in group.rows"
                          :key="row.key"
                          class="business-distribution-row"
                        >
                          <span>{{ row.label }}</span>
                          <div class="business-distribution-track">
                            <i :style="{ width: `${row.percent}%` }"></i>
                          </div>
                          <small>{{ row.count }}</small>
                        </div>
                      </div>
                      <el-empty v-else description="暂无分布数据" />
                    </div>
                  </div>
                </section>

                <div class="dashboard-split business-dashboard-split">
                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><Warning /></el-icon>
                      <span>风险与待处理</span>
                      <small>{{ businessRisks.length }} 项</small>
                    </div>
                    <div class="status-list">
                      <div
                        v-for="risk in businessRisks"
                        :key="risk.key"
                        class="status-row stacked business-risk-row"
                      >
                        <span>
                          <el-tag size="small" :type="businessSeverityTag(risk.severity)" effect="plain">
                            {{ businessSeverityLabel(risk.severity) }}
                          </el-tag>
                          {{ risk.title }}
                        </span>
                        <strong>{{ risk.count }}</strong>
                        <small>{{ risk.action }}</small>
                        <el-button
                          v-if="risk.target_path"
                          size="small"
                          plain
                          @click="openBusinessTarget(risk.target_path)"
                        >
                          查看
                        </el-button>
                      </div>
                      <el-empty v-if="!businessRisks.length" description="暂无待处理风险" />
                    </div>
                  </section>

                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><DataAnalysis /></el-icon>
                      <span>运行摘要</span>
                    </div>
                    <div class="status-list">
                      <div
                        v-for="item in businessSummaryRows"
                        :key="item.key"
                        class="status-row stacked"
                      >
                        <span>{{ item.label }}</span>
                        <strong>{{ item.value }}</strong>
                        <small>{{ item.detail }}</small>
                      </div>
                    </div>
                  </section>
                </div>
              </template>
            </el-tab-pane>

            <el-tab-pane label="报价速度" name="quote" :disabled="dashboardFeature.quoteDisabled">
              <el-alert
                v-if="dashboardFeature.quoteDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="报价速度看板暂不可用"
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
                title="响应速度看板暂不可用"
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
                  <el-button v-if="canManageQuoteOperations" size="small" :icon="Clock" plain @click="markQuoteTimeouts">标记超时</el-button>
                </div>
                <el-table
                  :data="quoteJobs"
                  row-key="job_id"
                  class="users-table"
                  empty-text="暂无报价任务"
                >
                  <el-table-column label="任务号" min-width="160" show-overflow-tooltip>
                    <template #default="{ row }">{{ displayQuoteJobNumber(row) }}</template>
                  </el-table-column>
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
                  <el-table-column label="操作" width="320" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions">
                        <el-button size="small" :icon="Document" plain @click="openQuoteJobDetail(row)">详情</el-button>
                        <el-button
                          size="small"
                          :icon="DataAnalysis"
                          type="primary"
                          plain
                          :disabled="!canManualAuditQuoteJob(row)"
                          :loading="agentCenterLoading"
                          @click="manualAuditQuoteJob(row)"
                        >
                          审计
                        </el-button>
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
                title="执行速度看板暂不可用"
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

            <el-tab-pane label="项目进度" name="projects" :disabled="dashboardFeature.projectDisabled">
              <el-alert
                v-if="dashboardFeature.projectDisabled"
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="项目进度看板暂不可用"
              />
              <template v-else>
                <el-alert
                  v-if="projectDashboard?.empty_state"
                  class="dashboard-alert"
                  type="info"
                  show-icon
                  :closable="false"
                  title="暂无项目进度数据"
                />
                <div class="metric-grid response-grid">
                  <div class="metric-card">
                    <span>项目总数</span>
                    <strong>{{ projectDashboard?.project_count ?? 0 }}</strong>
                    <small>进行中 {{ projectDashboard?.active_count ?? 0 }}</small>
                  </div>
                  <div class="metric-card">
                    <span>平均进度</span>
                    <strong>{{ projectDashboard?.avg_progress_percent ?? 0 }}%</strong>
                    <small>按阶段权重计算</small>
                  </div>
                  <div class="metric-card">
                    <span>阻塞项目</span>
                    <strong>{{ projectDashboard?.blocked_count ?? 0 }}</strong>
                    <small>阻塞任务 {{ projectDashboard?.blocked_task_count ?? 0 }}</small>
                  </div>
                  <div class="metric-card">
                    <span>延期项目</span>
                    <strong>{{ projectDashboard?.delayed_count ?? 0 }}</strong>
                    <small>逾期任务 {{ projectDashboard?.overdue_task_count ?? 0 }}</small>
                  </div>
                  <div class="metric-card">
                    <span>任务闭环</span>
                    <strong>{{ projectDashboard?.done_task_count ?? 0 }} / {{ projectDashboard?.task_count ?? 0 }}</strong>
                    <small>未完成 {{ projectDashboard?.open_task_count ?? 0 }}</small>
                  </div>
                </div>

                <div class="dashboard-split">
                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><TrendCharts /></el-icon>
                      <span>当前阶段分布</span>
                    </div>
                    <el-table
                      :data="projectDashboard?.stage_distribution || []"
                      row-key="stage_name"
                      class="users-table"
                      empty-text="暂无阶段数据"
                    >
                      <el-table-column prop="stage_name" label="阶段" min-width="160" />
                      <el-table-column prop="count" label="项目数" width="120" />
                    </el-table>
                  </section>

                  <section class="dashboard-section">
                    <div class="section-title">
                      <el-icon><Histogram /></el-icon>
                      <span>项目经理</span>
                    </div>
                    <div class="status-list">
                      <div
                        v-for="item in visibleProjectManagers"
                        :key="item.project_manager_id"
                        class="status-row stacked"
                      >
                        <span>{{ item.username }}</span>
                        <strong>{{ item.project_count }}</strong>
                        <small>阻塞 {{ item.blocked_count }} · 延期 {{ item.delayed_count }}</small>
                      </div>
                      <el-empty v-if="!visibleProjectManagers.length" description="暂无项目经理数据" />
                    </div>
                  </section>
                </div>
              </template>
            </el-tab-pane>
          </el-tabs>
        </template>

        <template v-else-if="['budgetProjects', 'budgetProjectDetail'].includes(routeName)">
          <BudgetProjects
            :detail-mode="routeName === 'budgetProjectDetail'"
            :can-edit="canEditBudgetProjects"
            :feature-available="budgetProjectsFeatureAvailable"
            :pricing-feature-available="budgetPricingFeatureAvailable"
            @navigate="navigate"
          />
        </template>

        <template v-else-if="routeName === 'accountQuotas'">
          <AccountQuotaLibrary :feature-available="accountQuotasFeatureAvailable" />
        </template>

        <template v-else-if="['projects', 'projectDetail', 'projectMyTasks'].includes(routeName)">
          <div v-if="routeName === 'projects'">
            <div class="content-heading">
              <div>
                <p class="eyebrow">项目协同</p>
                <h2>项目进度</h2>
              </div>
              <div class="heading-actions">
                <el-button :icon="Tickets" plain @click="navigate('/admin/project-tasks/my')">我的任务</el-button>
                <el-button v-if="canManageProjectProgress" :icon="Plus" type="primary" @click="openProjectCreate">新建项目</el-button>
                <el-button v-if="canManageProjectProgress" :icon="Tickets" type="success" plain @click="openProjectTrialCreate">快速创建项目</el-button>
                <el-button v-if="canManageProjectProgress" :icon="TrendCharts" type="primary" plain @click="openProjectEpcCreate">EPC流程模板</el-button>
                <el-button :icon="Refresh" plain @click="loadProjects">刷新</el-button>
              </div>
            </div>
            <el-alert
              v-if="projectFeatureDisabled"
              class="dashboard-alert"
              type="info"
              show-icon
              :closable="false"
              title="项目进度功能尚未开启"
            />
            <template v-else>
              <section class="project-overview-strip">
                <article
                  v-for="card in projectListOverviewCards"
                  :key="card.key"
                  :class="['project-overview-card', card.tone]"
                >
                  <span>{{ card.title }}</span>
                  <strong>{{ card.value }}</strong>
                  <small>{{ card.detail }}</small>
                </article>
              </section>
              <div class="project-filters">
                <el-select v-model="projectFilters.status" size="small" clearable placeholder="项目状态" @change="applyProjectFilters">
                  <el-option v-for="option in projectStatusOptions" :key="option.value" :label="option.label" :value="option.value" />
                </el-select>
                <el-select v-model="projectFilters.risk_level" size="small" clearable placeholder="风险状态" @change="applyProjectFilters">
                  <el-option v-for="option in projectRiskOptions" :key="option.value" :label="option.label" :value="option.value" />
                </el-select>
                <el-input v-model="projectFilters.keyword" size="small" clearable placeholder="项目/客户/地址" @keyup.enter="applyProjectFilters" @clear="applyProjectFilters" />
                <el-button size="small" type="primary" plain @click="applyProjectFilters">查询</el-button>
              </div>
              <el-table :data="projects" row-key="id" class="users-table" :row-class-name="projectListRowClassName" empty-text="暂无项目">
                <el-table-column label="项目" min-width="240" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div class="operation-client">
                      <strong>{{ row.name }}</strong>
                      <small>{{ row.project_code }} · {{ row.client_name || '未填写客户' }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column prop="project_manager_username" label="项目经理" width="120" />
                <el-table-column label="当前阶段" min-width="120">
                  <template #default="{ row }">{{ row.current_stage_name || '-' }}</template>
                </el-table-column>
                <el-table-column label="总进度" min-width="170">
                  <template #default="{ row }">
                    <el-progress :percentage="row.progress_percent || 0" :stroke-width="8" />
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag :type="projectStatusTag(row.status)" effect="plain">{{ projectStatusLabel(row.status) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="风险" width="110">
                  <template #default="{ row }">
                    <el-tag :type="projectRiskTag(row.risk_level)" effect="plain">{{ projectRiskLabel(row.risk_level) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="推进重点" min-width="180" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div :class="['project-focus-hint', projectFocusTone(row)]">
                      <strong>{{ projectFocusLabel(row) }}</strong>
                      <small>{{ projectFocusDetail(row) }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="任务" width="130">
                  <template #default="{ row }">
                    {{ row.done_task_count || 0 }} / {{ row.task_count || 0 }}
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="120" fixed="right">
                  <template #default="{ row }">
                    <el-button size="small" :icon="Document" plain @click="navigate(`/admin/projects/${row.id}`)">详情</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-pagination
                v-if="projectTotal > projectPageSize"
                v-model:current-page="projectPage"
                :page-size="projectPageSize"
                :total="projectTotal"
                layout="total, prev, pager, next"
                small
                @current-change="loadProjects"
              />
            </template>
          </div>

          <div v-else-if="routeName === 'projectMyTasks'">
            <div class="content-heading">
              <div>
                <p class="eyebrow">项目管理</p>
                <h2>我的项目任务</h2>
              </div>
              <div class="heading-actions">
                <el-button :icon="Tickets" plain @click="navigate('/admin/projects')">项目列表</el-button>
                <el-button :icon="Refresh" plain @click="loadMyProjectTasks">刷新</el-button>
              </div>
            </div>
            <section class="project-overview-strip compact">
              <article
                v-for="card in myProjectTaskOverviewCards"
                :key="card.key"
                :class="['project-overview-card', card.tone]"
              >
                <span>{{ card.title }}</span>
                <strong>{{ card.value }}</strong>
                <small>{{ card.detail }}</small>
              </article>
            </section>
            <div class="project-filters compact">
              <el-select v-model="myProjectTaskFilters.status" size="small" clearable placeholder="任务状态" @change="applyMyProjectTaskFilters">
                <el-option v-for="option in projectTaskStatusOptions" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
              <el-input v-model="myProjectTaskFilters.keyword" size="small" clearable placeholder="任务/说明/下一步" @keyup.enter="applyMyProjectTaskFilters" @clear="applyMyProjectTaskFilters" />
              <el-button size="small" type="primary" plain @click="applyMyProjectTaskFilters">查询</el-button>
            </div>
            <el-table :data="myProjectTasks" row-key="id" class="users-table" :row-class-name="projectTaskRowClassName" empty-text="暂无我的项目任务">
              <el-table-column label="任务" min-width="250" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.title }}</strong>
                    <small>{{ row.project_name }} · {{ row.stage_name }}</small>
                    <span v-if="row.is_key_node || row.evidence_policy === 'complete_required'" class="project-task-badges">
                      <el-tag v-if="row.is_key_node" size="small" effect="plain">关键节点</el-tag>
                      <el-tag v-if="row.evidence_policy === 'complete_required'" size="small" type="danger" effect="plain">需证据</el-tag>
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="进度" min-width="140">
                <template #default="{ row }"><el-progress :percentage="row.progress_percent || 0" :stroke-width="8" /></template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }"><el-tag :type="projectTaskStatusTag(row.status)" effect="plain">{{ projectTaskStatusLabel(row.status) }}</el-tag></template>
              </el-table-column>
              <el-table-column label="证据" width="105">
                <template #default="{ row }">
                  <el-button size="small" :type="projectEvidenceButtonType(row)" plain @click="openProjectTaskEvidence(row)">
                    证据 {{ row.evidence_count || 0 }}
                  </el-button>
                </template>
              </el-table-column>
              <el-table-column label="岗位/成果" min-width="210" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.owner_role || '-' }}</strong>
                    <small>{{ row.epc_deliverable || row.epc_standard || row.description || '-' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="截止时间" min-width="150">
                <template #default="{ row }">{{ formatDate(row.due_at) }}</template>
              </el-table-column>
              <el-table-column label="阻塞/下一步" min-width="210" show-overflow-tooltip>
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong v-if="row.status === 'blocked'">{{ row.blocked_reason || '-' }}</strong>
                    <span v-else>{{ row.next_action || '-' }}</span>
                    <small v-if="row.status === 'blocked'">{{ row.next_action || '待解除阻塞' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="420" fixed="right">
                <template #default="{ row }">
                    <div class="row-actions project-task-actions">
                      <el-button size="small" type="primary" plain :disabled="row.status !== 'todo'" @click="advanceProjectTask(row, 'start')">开始</el-button>
                      <el-button size="small" type="primary" plain :disabled="!['todo', 'started'].includes(row.status)" @click="advanceProjectTask(row, 'progress')">推进</el-button>
                      <el-button size="small" type="warning" plain :disabled="!['todo', 'started', 'progressing'].includes(row.status)" @click="advanceProjectTask(row, 'submit')">提交</el-button>
                    <el-button size="small" plain :disabled="!canRollbackProjectTask(row)" @click="rollbackProjectTask(row)">回退</el-button>
                    <el-button size="small" type="success" plain :disabled="row.status !== 'blocked'" @click="unblockProjectTask(row)">解除</el-button>
                    <el-button size="small" type="warning" plain :disabled="['blocked', 'done', 'cancelled'].includes(row.status)" @click="blockProjectTask(row)">阻塞</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <el-pagination
              v-if="myProjectTaskTotal > myProjectTaskPageSize"
              v-model:current-page="myProjectTaskPage"
              :page-size="myProjectTaskPageSize"
              :total="myProjectTaskTotal"
              layout="total, prev, pager, next"
              small
              @current-change="loadMyProjectTasks"
            />
          </div>

          <div v-else>
            <div class="content-heading">
              <div>
                <p class="eyebrow">任务协同</p>
                <h2>{{ projectDetail?.name || '项目详情' }}</h2>
              </div>
              <div class="heading-actions">
                <el-button :icon="Tickets" plain @click="navigate('/admin/projects')">返回列表</el-button>
                <el-button v-if="canManageProjectProgress" :icon="Plus" type="primary" @click="openProjectTaskCreate()">新建任务</el-button>
                <el-button :icon="Refresh" plain @click="loadProjectDetail">刷新</el-button>
              </div>
            </div>
            <div v-if="!projectDetail" class="center-state">
              <el-icon class="spin"><Refresh /></el-icon>
              <span>加载中</span>
            </div>
            <template v-else>
              <section class="project-detail-focus-bar">
                <div class="project-detail-focus-main">
                  <el-tag :type="projectRiskTag(projectDetail.risk_level)" effect="plain">
                    {{ projectRiskLabel(projectDetail.risk_level) }}
                  </el-tag>
                  <div>
                    <strong>{{ projectDetail.project_code || '未设置编号' }}</strong>
                    <span>{{ projectDetail.client_name || '未填写客户' }} · {{ projectDetail.project_manager_username || '未设置项目经理' }}</span>
                  </div>
                </div>
                <div class="project-detail-focus-cards">
                  <article
                    v-for="card in projectDetailFocusCards"
                    :key="card.key"
                    :class="['project-overview-card', card.tone]"
                  >
                    <span>{{ card.title }}</span>
                    <strong>{{ card.value }}</strong>
                    <small>{{ card.detail }}</small>
                  </article>
                </div>
              </section>
              <div class="metric-grid response-grid">
                <div class="metric-card">
                  <span>总进度</span>
                  <strong>{{ projectDetail.progress_percent || 0 }}%</strong>
                  <small>{{ projectStatusLabel(projectDetail.status) }}</small>
                </div>
                <div class="metric-card">
                  <span>当前阶段</span>
                  <strong>{{ projectDetail.current_stage_name || '-' }}</strong>
                  <small>按首个未完成阶段判断</small>
                </div>
                <div class="metric-card">
                  <span>风险状态</span>
                  <strong>{{ projectRiskLabel(projectDetail.risk_level) }}</strong>
                  <small>阻塞优先，其次逾期</small>
                </div>
                <div class="metric-card">
                  <span>项目经理</span>
                  <strong>{{ projectDetail.project_manager_username || '-' }}</strong>
                  <small>{{ projectDetail.owner_department || '未设置部门' }}</small>
                </div>
                <div class="metric-card">
                  <span>任务闭环</span>
                  <strong>{{ projectDetail.done_task_count || 0 }} / {{ projectDetail.task_count || 0 }}</strong>
                  <small>阻塞 {{ projectDetail.blocked_task_count || 0 }} · 逾期 {{ projectDetail.overdue_task_count || 0 }}</small>
                </div>
              </div>

              <section class="dashboard-section project-section-gap">
                <div class="section-title">
                  <el-icon><Document /></el-icon>
                  <span>成果证据完整性</span>
                </div>
                <div class="metric-grid response-grid">
                  <button
                    type="button"
                    :class="['metric-card metric-card-button', { active: projectTaskEvidenceFilter === 'required' }]"
                    @click="setProjectTaskEvidenceFilter('required')"
                  >
                    <span>有成果要求节点</span>
                    <strong>{{ projectEvidenceSummary.required_task_count || 0 }}</strong>
                    <small>来自 EPC 成果文件要求</small>
                  </button>
                  <button
                    type="button"
                    :class="['metric-card metric-card-button', { active: projectTaskEvidenceFilter === 'evidenced' }]"
                    @click="setProjectTaskEvidenceFilter('evidenced')"
                  >
                    <span>已留证据节点</span>
                    <strong>{{ projectEvidenceSummary.evidenced_task_count || 0 }}</strong>
                    <small>完成度 {{ projectEvidenceSummary.evidence_completion_percent || 0 }}%</small>
                  </button>
                  <button
                    type="button"
                    :class="['metric-card metric-card-button warning', { active: projectTaskEvidenceFilter === 'missing' }]"
                    @click="setProjectTaskEvidenceFilter('missing')"
                  >
                    <span>缺证据节点</span>
                    <strong>{{ projectEvidenceSummary.missing_evidence_task_count || 0 }}</strong>
                    <small>点击只看缺证据任务</small>
                  </button>
                  <button
                    type="button"
                    :class="['metric-card metric-card-button danger', { active: projectTaskEvidenceFilter === 'done_missing' }]"
                    @click="setProjectTaskEvidenceFilter('done_missing')"
                  >
                    <span>无证据已完成</span>
                    <strong>{{ projectEvidenceSummary.done_without_evidence_task_count || 0 }}</strong>
                    <small>复盘时优先补齐</small>
                  </button>
                  <button
                    type="button"
                    :class="['metric-card metric-card-button', { active: projectTaskEvidenceFilter === 'open_missing' }]"
                    @click="setProjectTaskEvidenceFilter('open_missing')"
                  >
                    <span>未完成且缺证据</span>
                    <strong>{{ projectEvidenceSummary.open_missing_evidence_task_count || 0 }}</strong>
                    <small>推进前可先补充依据</small>
                  </button>
                </div>
              </section>

              <section class="dashboard-section">
                <div class="section-title">
                  <el-icon><TrendCharts /></el-icon>
                  <span>阶段进度</span>
                </div>
                <el-table :data="projectDetail.stages || []" row-key="id" class="users-table" empty-text="暂无阶段">
                  <el-table-column prop="stage_name" label="阶段" min-width="140" />
                  <el-table-column prop="owner_role" label="责任岗位" min-width="150" />
                  <el-table-column prop="weight_percent" label="权重" width="90">
                    <template #default="{ row }">{{ row.weight_percent }}%</template>
                  </el-table-column>
                  <el-table-column label="进度" min-width="180">
                    <template #default="{ row }"><el-progress :percentage="row.progress_percent || 0" :stroke-width="8" /></template>
                  </el-table-column>
                  <el-table-column label="任务" width="110">
                    <template #default="{ row }">{{ row.task_count || 0 }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="110">
                    <template #default="{ row }">
                      <el-button v-if="canManageProjectProgress" size="small" plain @click="openProjectTaskCreate(row)">加任务</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </section>

              <section class="dashboard-section project-section-gap">
                <div class="section-title">
                  <el-icon><Tickets /></el-icon>
                  <span>岗位任务</span>
                  <el-tag v-if="projectTaskEvidenceFilter !== 'all'" type="warning" effect="plain">{{ projectTaskEvidenceFilterLabel(projectTaskEvidenceFilter) }}</el-tag>
                  <el-button v-if="projectTaskEvidenceFilter !== 'all'" size="small" plain @click="setProjectTaskEvidenceFilter('all')">清除筛选</el-button>
                </div>
                <el-table :data="visibleProjectDetailTasks" row-key="id" class="users-table" :row-class-name="projectTaskRowClassName" empty-text="暂无任务">
                  <el-table-column label="任务" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.title }}</strong>
                        <small>{{ row.stage_name }} · {{ row.owner_username || '-' }}</small>
                        <span v-if="row.is_key_node || row.evidence_policy === 'complete_required'" class="project-task-badges">
                          <el-tag v-if="row.is_key_node" size="small" effect="plain">关键节点</el-tag>
                          <el-tag v-if="row.evidence_policy === 'complete_required'" size="small" type="danger" effect="plain">需证据</el-tag>
                        </span>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="进度" min-width="140">
                    <template #default="{ row }"><el-progress :percentage="row.progress_percent || 0" :stroke-width="8" /></template>
                  </el-table-column>
                  <el-table-column label="状态" width="110">
                    <template #default="{ row }"><el-tag :type="projectTaskStatusTag(row.status)" effect="plain">{{ projectTaskStatusLabel(row.status) }}</el-tag></template>
                  </el-table-column>
                  <el-table-column label="证据" width="105">
                    <template #default="{ row }">
                      <el-button size="small" :type="projectEvidenceButtonType(row)" plain @click="openProjectTaskEvidence(row)">
                        证据 {{ row.evidence_count || 0 }}
                      </el-button>
                    </template>
                  </el-table-column>
                  <el-table-column label="EPC要求" min-width="230" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.owner_role || '-' }}</strong>
                        <small>{{ row.epc_deliverable || row.epc_standard || row.description || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="截止时间" min-width="150">
                    <template #default="{ row }">{{ formatDate(row.due_at) }}</template>
                  </el-table-column>
                  <el-table-column label="阻塞/下一步" min-width="210" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong v-if="row.status === 'blocked'">{{ row.blocked_reason || '-' }}</strong>
                        <span v-else>{{ row.next_action || '-' }}</span>
                        <small v-if="row.status === 'blocked'">{{ row.next_action || '待解除阻塞' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="480" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions project-task-actions">
                        <el-button size="small" type="primary" plain :disabled="row.status !== 'todo'" @click="advanceProjectTask(row, 'start')">开始</el-button>
                        <el-button size="small" type="primary" plain :disabled="!['todo', 'started'].includes(row.status)" @click="advanceProjectTask(row, 'progress')">推进</el-button>
                        <el-button size="small" type="warning" plain :disabled="!['todo', 'started', 'progressing'].includes(row.status)" @click="advanceProjectTask(row, 'submit')">提交</el-button>
                        <el-button v-if="canManageProjectProgress" size="small" type="success" plain :disabled="row.status !== 'submitted'" @click="advanceProjectTask(row, 'complete')">完成</el-button>
                        <el-button size="small" plain :disabled="!canRollbackProjectTask(row)" @click="rollbackProjectTask(row)">回退</el-button>
                        <el-button size="small" type="success" plain :disabled="row.status !== 'blocked'" @click="unblockProjectTask(row)">解除</el-button>
                        <el-button size="small" type="warning" plain :disabled="['blocked', 'done', 'cancelled'].includes(row.status)" @click="blockProjectTask(row)">阻塞</el-button>
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
              </section>

              <section class="dashboard-section project-section-gap">
                <div class="section-title">
                  <el-icon><Clock /></el-icon>
                  <span>项目动态</span>
                </div>
                <div class="status-list">
                  <div v-for="event in projectEvents" :key="event.id" class="status-row stacked">
                    <span>{{ projectEventLabel(event.event_type) }}</span>
                    <strong>{{ event.actor_username || '-' }}</strong>
                    <small>{{ formatDate(event.created_at) }} · {{ event.message || '-' }}</small>
                  </div>
                  <el-empty v-if="!projectEvents.length" description="暂无动态" />
                </div>
              </section>
            </template>
          </div>
        </template>

        <template v-else-if="routeName === 'execution'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">执行协同</p>
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
                <section class="execution-overview-strip">
                  <article
                    v-for="card in executionTaskOverviewCards"
                    :key="card.key"
                    :class="['project-overview-card', card.tone]"
                  >
                    <span>{{ card.title }}</span>
                    <strong>{{ card.value }}</strong>
                    <small>{{ card.detail }}</small>
                  </article>
                </section>
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
                  :row-class-name="executionTaskRowClassName"
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
                  <el-table-column label="下一步" min-width="190" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div :class="['execution-next-step', executionTaskNextStepTone(row)]">
                        <strong>{{ executionTaskNextStepLabel(row) }}</strong>
                        <small>{{ executionTaskNextStepDetail(row) }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="notes" label="备注" min-width="180" show-overflow-tooltip />
                  <el-table-column label="操作" width="270" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions execution-task-actions">
                        <el-button size="small" :icon="Document" plain @click="openExecutionDetail(row)">详情</el-button>
                        <el-button
                          size="small"
                          type="primary"
                          plain
                          :disabled="row.status !== 'pending'"
                          @click="updateExecutionTaskStatus(row, 'in_progress')"
                        >
                          开始
                        </el-button>
                        <el-button
                          size="small"
                          type="success"
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
                <section class="execution-overview-strip">
                  <article
                    v-for="card in meetingOverviewCards"
                    :key="card.key"
                    :class="['project-overview-card', card.tone]"
                  >
                    <span>{{ card.title }}</span>
                    <strong>{{ card.value }}</strong>
                    <small>{{ card.detail }}</small>
                  </article>
                </section>
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
                  :row-class-name="meetingRowClassName"
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
                  <el-table-column label="任务草稿" min-width="150">
                    <template #default="{ row }">
                      <div :class="['meeting-draft-progress', meetingDraftProgressTone(row)]">
                        <strong>{{ row.accepted_draft_count || 0 }} / {{ row.draft_count || 0 }}</strong>
                        <small>{{ meetingDraftProgressLabel(row) }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="190" fixed="right">
                    <template #default="{ row }">
                      <div class="row-actions execution-task-actions">
                        <el-button size="small" :icon="Document" type="primary" plain @click="openMeetingDetail(row)">详情</el-button>
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
              <p class="eyebrow">客户经营</p>
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
            <section class="business-ledger-overview">
              <article
                v-for="card in businessLedgerOverviewCards"
                :key="card.key"
                :class="['project-overview-card', card.tone]"
              >
                <span>{{ card.title }}</span>
                <strong>{{ card.value }}</strong>
                <small>{{ card.detail }}</small>
              </article>
            </section>
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
              <el-table-column label="建议下一步" min-width="210" show-overflow-tooltip>
                <template #default="{ row }">
                  <div :class="['ledger-next-step', businessLedgerNextStepTone(row)]">
                    <strong>{{ businessLedgerNextStepLabel(row) }}</strong>
                    <small>{{ businessLedgerNextStepDetail(row) }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="responder_username" label="负责人" width="120" />
              <el-table-column prop="notes" label="备注" min-width="180" show-overflow-tooltip />
              <el-table-column label="操作" width="260" fixed="right">
                <template #default="{ row }">
                  <div class="row-actions business-ledger-actions">
                    <el-button size="small" :icon="Document" type="primary" plain @click="openBusinessLedgerDetail(row)">详情</el-button>
                    <el-button
                      size="small"
                      type="primary"
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

        <template v-else-if="routeName === 'bidding'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">投标工作台</p>
              <h2>智能投标解析</h2>
            </div>
            <div class="heading-actions">
              <el-button :icon="Upload" type="primary" :disabled="biddingFeatureDisabled" @click="openBiddingTenderUpload">
                上传招标文件
              </el-button>
              <el-button :icon="Refresh" plain @click="loadBiddingProjects">刷新</el-button>
            </div>
          </div>

          <el-alert
            v-if="biddingFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="智能投标暂不可用"
            description="请联系管理员确认功能状态后再使用。"
          ></el-alert>

          <template v-else>
            <div class="metric-grid">
              <div
                v-for="card in biddingOverviewCards"
                :key="card.key"
                class="metric-card"
              >
                <span>{{ card.title }}</span>
                <strong>{{ card.value }}</strong>
                <small>{{ card.detail }}</small>
              </div>
            </div>

            <section class="dashboard-section">
              <div class="section-title">
                <el-icon><DocumentChecked /></el-icon>
                <span>投标项目</span>
                <small>通过上传甲方 Word/PDF 招标文件创建项目，并生成要求清单、合同风险和废标风险</small>
              </div>
              <div class="cost-db-filters">
                <el-select
                  v-model="biddingFilters.status"
                  size="small"
                  clearable
                  placeholder="状态"
                  @change="applyBiddingFilters"
                >
                  <el-option
                    v-for="option in biddingStatusOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  ></el-option>
                </el-select>
                <el-input
                  v-model="biddingFilters.keyword"
                  size="small"
                  clearable
                  :prefix-icon="Search"
                  placeholder="项目、业主、代理、地点"
                  @keyup.enter="applyBiddingFilters"
                  @clear="applyBiddingFilters"
                ></el-input>
                <el-button size="small" type="primary" plain @click="applyBiddingFilters">查询</el-button>
              </div>

              <el-table
                v-loading="biddingLoading"
                :data="biddingProjects"
                row-key="project_uuid"
                class="users-table"
                empty-text="暂无投标项目"
              >
                <el-table-column label="项目" min-width="230" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div class="operation-client">
                      <strong>{{ row.project_name }}</strong>
                      <small>{{ row.tenderer_name || '未填写招标单位' }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column prop="project_type" label="类型" width="120" />
                <el-table-column prop="project_location" label="地点" min-width="130" show-overflow-tooltip />
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag :type="biddingStatusTag(row.status)" effect="plain">{{ biddingStatusLabel(row.status) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="解析结果" min-width="210">
                  <template #default="{ row }">
                    <div class="operation-client">
                      <strong>要求 {{ row.counts?.requirement_count || 0 }} · 风险 {{ row.counts?.risk_count || 0 }}</strong>
                      <small>高风险 {{ row.counts?.high_risk_count || 0 }} · 待复核 {{ row.counts?.pending_risk_count || 0 }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="截止时间" min-width="150">
                  <template #default="{ row }">{{ formatDate(row.tender_deadline_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="170" fixed="right">
                  <template #default="{ row }">
                    <div class="row-actions">
                      <el-button size="small" :icon="Document" plain @click="openBiddingProjectDetail(row)">详情</el-button>
                      <el-button size="small" type="primary" plain @click="openBiddingProjectDetail(row, 'risks')">风险</el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
              <el-pagination
                v-if="biddingProjectTotal > biddingProjectPageSize"
                v-model:current-page="biddingProjectPage"
                :page-size="biddingProjectPageSize"
                :total="biddingProjectTotal"
                layout="total, prev, pager, next"
                small
                @current-change="loadBiddingProjects"
              ></el-pagination>
            </section>

            <el-drawer
              v-model="biddingDrawer.visible"
              size="82%"
              :title="biddingDrawer.project?.project_name || '投标项目详情'"
              destroy-on-close
            >
              <div v-if="biddingDrawer.project" class="drawer-body">
                <div class="metric-grid">
                  <div class="metric-card">
                    <span>资料</span>
                    <strong>{{ biddingFiles.length }}</strong>
                    <small>支持 PDF、Word、Excel、文本</small>
                  </div>
                  <div class="metric-card">
                    <span>分析成果</span>
                    <strong>{{ biddingTenderAnalysisResultCount }}</strong>
                    <small>
                      摘要 {{ biddingTenderSummaryRows.length }} ·
                      评分 {{ biddingTenderScoringItemCount }} ·
                      风险 {{ biddingTenderRiskClauseRows.length }}
                    </small>
                  </div>
                  <div class="metric-card">
                    <span>风险</span>
                    <strong>{{ biddingRisksTotal }}</strong>
                    <small>待复核 {{ biddingPendingRiskCount }}</small>
                  </div>
                  <div class="metric-card">
                    <span>最近解析</span>
                    <strong>{{ biddingParseRuns[0]?.status ? biddingParseStatusLabel(biddingParseRuns[0].status) : '暂无' }}</strong>
                    <small>{{ formatDate(biddingParseRuns[0]?.finished_at || biddingParseRuns[0]?.created_at) }}</small>
                  </div>
                </div>

                <el-tabs v-model="biddingDrawer.activeTab" class="dashboard-tabs">
                  <el-tab-pane label="资料与解析" name="files">
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Upload /></el-icon>
                        <span>追加招标文件</span>
                        <small>支持 PDF、Word(.docx)，上传后先抽取文本，再点击解析生成要求和风险清单</small>
                      </div>
                      <div class="cost-db-filters">
                        <el-select v-model="biddingUpload.fileType" size="small">
                          <el-option
                            v-for="option in biddingFileTypeOptions"
                            :key="option.value"
                            :label="option.label"
                            :value="option.value"
                          ></el-option>
                        </el-select>
                        <el-upload
                          ref="biddingUploadRef"
                          :auto-upload="false"
                          :show-file-list="true"
                          :limit="1"
                          accept=".pdf,.docx"
                          :on-change="handleBiddingFileChange"
                          :on-remove="clearBiddingFile"
                        >
                          <el-button :icon="Document" plain>选择资料</el-button>
                        </el-upload>
                        <el-button
                          type="primary"
                          :icon="Upload"
                          :loading="biddingUpload.loading"
                          :disabled="!biddingUpload.file"
                          @click="uploadBiddingFile"
                        >
                          上传并抽取
                        </el-button>
                        <el-button
                          :icon="DataAnalysis"
                          type="success"
                          plain
                          :loading="biddingParsing"
                          :disabled="!biddingFiles.length"
                          @click="parseBiddingProject"
                        >
                          解析招标文件
                        </el-button>
                      </div>

                      <el-table
                        :data="biddingFiles"
                        row-key="file_uuid"
                        class="users-table"
                        empty-text="暂无资料"
                      >
                        <el-table-column prop="original_filename" label="文件名" min-width="240" show-overflow-tooltip />
                        <el-table-column label="类型" width="130">
                          <template #default="{ row }">{{ biddingFileTypeLabel(row.file_type) }}</template>
                        </el-table-column>
                        <el-table-column prop="section_count" label="段落" width="90" />
                        <el-table-column prop="page_count" label="页/表" width="90" />
                        <el-table-column label="状态" width="110">
                          <template #default="{ row }">
                            <el-tag :type="row.parser_status === 'parsed' ? 'success' : 'warning'" effect="plain">{{ row.parser_status }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="上传时间" min-width="150">
                          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
                        </el-table-column>
                      </el-table>
                    </section>

                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Clock /></el-icon>
                        <span>解析版本</span>
                        <small>每次解析会保留版本；风险条款由独立“风险分析”生成</small>
                      </div>
                      <div v-if="biddingDocumentStructureRows.length" class="bidding-structure-panel">
                        <div class="bidding-structure-summary">
                          <span>文档结构识别</span>
                          <strong>
                            已分析 {{ latestBiddingParseSummary.analyzed_segment_count || 0 }} / {{ latestBiddingParseSummary.segment_count || 0 }} 段
                          </strong>
                          <small>
                            继承章节 {{ latestBiddingParseSummary.inherited_segment_count || 0 }} 段 · 已过滤 {{ latestBiddingParseSummary.ignored_segment_count || 0 }} 段
                          </small>
                        </div>
                        <div class="bidding-structure-list">
                          <span
                            v-for="item in biddingDocumentStructureRows"
                            :key="item.section"
                            class="bidding-structure-item"
                          >
                            <em>{{ item.label }}</em>
                            <strong>{{ item.count }}</strong>
                          </span>
                        </div>
                      </div>
                      <el-table
                        :data="biddingParseRuns"
                        row-key="run_uuid"
                        class="users-table"
                        empty-text="暂无解析版本"
                      >
                        <el-table-column prop="run_uuid" label="版本" min-width="220" show-overflow-tooltip />
                        <el-table-column label="状态" width="110">
                          <template #default="{ row }">
                            <el-tag :type="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'" effect="plain">
                              {{ biddingParseStatusLabel(row.status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="摘要" min-width="260">
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>原文片段 {{ row.summary?.segment_count || 0 }} · 已分析 {{ row.summary?.analyzed_segment_count || 0 }} · 已过滤 {{ row.summary?.ignored_segment_count || 0 }}</strong>
                              <small v-if="row.summary?.risk_card_summary">
                                解析版本仅生成结构化摘要；风险条款请点击“风险分析”
                              </small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="完成时间" min-width="150">
                          <template #default="{ row }">{{ formatDate(row.finished_at || row.created_at) }}</template>
                        </el-table-column>
                      </el-table>

                    </section>
                  </el-tab-pane>

                  <el-tab-pane label="招标分析" name="analysis">
                    <section v-loading="biddingTenderAnalysisLoading" class="dashboard-section">
                      <div class="section-title">
                        <el-icon><DataAnalysis /></el-icon>
                        <span>招标文件分析成果表</span>
                        <small>解析生成结构化信息摘要表；风险条款清单需单独点击“风险分析”生成</small>
                        <el-button
                          class="section-title-action"
                          size="small"
                          type="primary"
                          plain
                          :icon="Download"
                          :loading="biddingTenderAnalysisExporting"
                          :disabled="!biddingTenderAnalysis"
                          @click="exportBiddingTenderAnalysis"
                        >
                          导出Word
                        </el-button>
                        <el-button
                          size="small"
                          type="warning"
                          plain
                          :icon="DataAnalysis"
                          :loading="biddingRiskClauseAnalyzing"
                          :disabled="!biddingTenderAnalysis"
                          @click="analyzeBiddingRiskClause"
                        >
                          风险分析
                        </el-button>
                        <el-button
                          size="small"
                          type="warning"
                          plain
                          :icon="Download"
                          :loading="biddingRiskClauseExporting"
                          :disabled="!biddingTenderRiskClauseRows.length"
                          @click="exportBiddingRiskClause"
                        >
                          导出风险Word
                        </el-button>
                      </div>

                      <el-empty
                        v-if="!biddingTenderAnalysis"
                        description="暂无成果表，请先上传并解析甲方招标文件"
                      />

                      <template v-else>
                        <div v-if="biddingImportantInfoProgress.visible" class="bidding-llm-progress-panel">
                          <div class="bidding-risk-detail-heading">
                            <div>
                              <strong>LLM结构化提取进度</strong>
                              <small>{{ biddingImportantInfoProgress.stage || '准备中' }} · {{ biddingImportantInfoProgress.detail || '等待系统返回结果' }}</small>
                            </div>
                            <el-tag :type="biddingImportantInfoProgress.status === 'success' ? 'success' : biddingImportantInfoProgress.status === 'error' ? 'danger' : 'warning'" effect="plain">
                              {{ biddingImportantInfoProgress.status === 'success' ? '已完成' : biddingImportantInfoProgress.status === 'error' ? '异常' : '进行中' }}
                            </el-tag>
                          </div>
                          <el-progress
                            :percentage="biddingImportantInfoProgress.percentage"
                            :status="biddingImportantInfoProgressBarStatus"
                          />
                        </div>
                        <div v-if="biddingRiskClauseProgress.visible" class="bidding-llm-progress-panel">
                          <div class="bidding-risk-detail-heading">
                            <div>
                              <strong>LLM风险分析进度</strong>
                              <small>{{ biddingRiskClauseProgress.stage || '准备中' }} · {{ biddingRiskClauseProgress.detail || '等待系统返回结果' }}</small>
                            </div>
                            <el-tag :type="biddingRiskClauseProgress.status === 'success' ? 'success' : biddingRiskClauseProgress.status === 'error' ? 'danger' : 'warning'" effect="plain">
                              {{ biddingRiskClauseProgress.status === 'success' ? '已完成' : biddingRiskClauseProgress.status === 'error' ? '异常' : '进行中' }}
                            </el-tag>
                          </div>
                          <el-progress
                            :percentage="biddingRiskClauseProgress.percentage"
                            :status="biddingRiskClauseProgressBarStatus"
                          />
                        </div>

                        <div class="bidding-risk-card-summary">
                          <div>
                            <span>结构化摘要</span>
                            <strong>{{ biddingImportantInfoFoundCount || 0 }}/{{ biddingImportantInfoFieldCount || 0 }}</strong>
                            <small>待澄清/未识别 {{ biddingImportantInfoIssueCount || 0 }} 项</small>
                          </div>
                          <div>
                            <span>评分细则</span>
                            <strong>{{ biddingTenderAnalysisQuality.scoring_item_count || biddingTenderScoringRows.length }}</strong>
                            <small>
                              商务 {{ biddingTenderAnalysisQuality.scoring_by_package?.business || 0 }} ·
                              技术 {{ biddingTenderAnalysisQuality.scoring_by_package?.technical || 0 }} ·
                              报价 {{ biddingTenderAnalysisQuality.scoring_by_package?.pricing || 0 }}
                            </small>
                          </div>
                          <div>
                            <span>风险条款</span>
                            <strong>{{ biddingRiskClauseRiskCount }}</strong>
                            <small>
                              高 {{ biddingRiskClauseHighCount }} ·
                              中 {{ biddingRiskClauseMediumCount }} ·
                              低 {{ biddingRiskClauseLowCount }}
                            </small>
                          </div>
                          <div>
                            <span>待复核</span>
                            <strong>{{ biddingTenderAnalysisReviewQueue.length }}</strong>
                            <small>{{ biddingTenderAnalysis.business_object_policy?.frontstage_note || '业务对象默认隐藏' }}</small>
                          </div>
                        </div>

                        <div class="bidding-review-workbench">
                          <div class="bidding-risk-detail-heading">
                            <div>
                              <strong>待复核项</strong>
                              <small>优先处理结构化摘要缺失、低置信度和评分细则复核项；风险条款请使用独立风险分析</small>
                            </div>
                            <div class="bidding-section-actions">
                              <el-tag :type="biddingTenderAnalysisReviewQueue.length ? 'warning' : 'success'" effect="plain">
                                {{ biddingTenderAnalysisReviewQueue.length ? `${biddingTenderAnalysisReviewQueue.length} 项待复核` : '暂无待复核' }}
                              </el-tag>
                              <el-button
                                size="small"
                                plain
                                :icon="biddingTenderReviewWorkbenchExpanded ? ArrowDown : ArrowRight"
                                @click="biddingTenderReviewWorkbenchExpanded = !biddingTenderReviewWorkbenchExpanded"
                              >
                                {{ biddingTenderReviewWorkbenchExpanded ? '收起' : '展开' }}
                              </el-button>
                            </div>
                          </div>
                          <el-table
                            v-show="biddingTenderReviewWorkbenchExpanded"
                            :data="biddingTenderReviewPreviewRows"
                            row-key="row_key"
                            :tree-props="{ children: 'children' }"
                            class="users-table"
                            empty-text="暂无待复核项"
                          >
                            <el-table-column prop="table_label" label="成果表" width="150" />
                            <el-table-column label="复核项" min-width="220" show-overflow-tooltip>
                              <template #default="{ row }">
                                <div class="operation-client">
                                  <strong>{{ row.title }}</strong>
                                  <small>
                                    {{ row.review_category_label || '待复核' }}
                                    <template v-if="row.item_count && row.item_count > 1"> · 合并 {{ row.item_count }} 项</template>
                                  </small>
                                </div>
                              </template>
                            </el-table-column>
                            <el-table-column label="原因" min-width="260">
                              <template #default="{ row }">
                                <div class="business-object-badges">
                                  <el-tag
                                    v-for="reason in row.reasons || []"
                                    :key="reason"
                                    size="small"
                                    type="warning"
                                    effect="plain"
                                  >
                                    {{ reason }}
                                  </el-tag>
                                </div>
                              </template>
                            </el-table-column>
                            <el-table-column label="来源" min-width="180" show-overflow-tooltip>
                              <template #default="{ row }">{{ row.source_file || '-' }} · {{ row.source_location || '-' }}</template>
                            </el-table-column>
                            <el-table-column label="操作" width="120">
                              <template #default="{ row }">
                                <el-button size="small" type="primary" plain @click.stop="openBiddingAnalysisTable(row.table_key)">查看对应表</el-button>
                              </template>
                            </el-table-column>
                          </el-table>
                        </div>

                        <el-tabs ref="biddingAnalysisTabsRef" v-model="biddingTenderAnalysisTab" class="bidding-analysis-tabs">
                          <el-tab-pane label="结构化信息摘要表" name="summary">
                            <div v-if="biddingImportantInfoSections.length" class="bidding-important-info-board">
                              <div class="bidding-important-info-toolbar">
                                <span>共 {{ biddingImportantInfoSections.length }} 个大项 · {{ biddingImportantInfoFoundCount }}/{{ biddingImportantInfoFieldCount }} 项已识别</span>
                                <div class="bidding-section-actions">
                                  <el-button
                                    size="small"
                                    plain
                                    :icon="ArrowDown"
                                    :disabled="biddingImportantInfoAllExpanded"
                                    @click="expandAllBiddingImportantInfoSections"
                                  >
                                    全部展开
                                  </el-button>
                                  <el-button
                                    size="small"
                                    plain
                                    :icon="ArrowRight"
                                    :disabled="!biddingImportantInfoExpandedKeys.length"
                                    @click="collapseAllBiddingImportantInfoSections"
                                  >
                                    全部收起
                                  </el-button>
                                </div>
                              </div>
                              <section
                                v-for="section in biddingImportantInfoSections"
                                :key="section.section_key"
                                class="bidding-important-info-section"
                              >
                                <div class="bidding-risk-detail-heading">
                                  <div>
                                    <strong>{{ section.title }}</strong>
                                    <small>
                                      已识别 {{ biddingImportantInfoSectionFoundCount(section) }} / {{ section.items.length }} 项
                                    </small>
                                  </div>
                                  <div class="bidding-section-actions">
                                    <el-tag effect="plain">{{ section.section_key }}</el-tag>
                                    <el-button
                                      size="small"
                                      plain
                                      :icon="isBiddingImportantInfoSectionExpanded(section) ? ArrowDown : ArrowRight"
                                      @click="toggleBiddingImportantInfoSection(section)"
                                    >
                                      {{ isBiddingImportantInfoSectionExpanded(section) ? '收起' : '展开' }}
                                    </el-button>
                                  </div>
                                </div>
                                <el-table
                                  v-show="isBiddingImportantInfoSectionExpanded(section)"
                                  :data="section.items"
                                  row-key="row_key"
                                  class="users-table bidding-important-info-table"
                                  empty-text="暂无字段"
                                >
                                  <el-table-column prop="field_name" label="字段" width="170" />
                                  <el-table-column label="识别结果" min-width="380">
                                    <template #default="{ row }">
                                      <div class="operation-client bidding-important-info-value">
                                        <strong>{{ row.value || '未识别到明确结果' }}</strong>
                                        <small v-if="row.note">{{ row.note }}</small>
                                      </div>
                                    </template>
                                  </el-table-column>
                                  <el-table-column label="状态" width="105">
                                    <template #default="{ row }">
                                      <el-tag :type="biddingImportantInfoStatusTag(row.status)" effect="plain">
                                        {{ biddingImportantInfoStatusLabel(row.status) }}
                                      </el-tag>
                                    </template>
                                  </el-table-column>
                                  <el-table-column label="来源" min-width="210" show-overflow-tooltip>
                                    <template #default="{ row }">{{ biddingImportantInfoSourceLabel(row) }}</template>
                                  </el-table-column>
                                </el-table>
                              </section>
                            </div>
                            <el-empty
                              v-else
                              :description="biddingImportantInfoEmptyText"
                            />
                          </el-tab-pane>

                          <el-tab-pane label="评分细则表" name="scoring">
                            <el-table
                              :data="biddingTenderScoringDisplayRows"
                              row-key="row_key"
                              class="users-table"
                              empty-text="暂无评分细则"
                            >
                              <el-table-column label="评分项" min-width="220" show-overflow-tooltip>
                                <template #default="{ row }">
                                  <div
                                    class="operation-client bidding-scoring-item"
                                    :class="{ 'is-child': row.__scoringChild }"
                                  >
                                    <div class="bidding-scoring-title-line">
                                      <el-button
                                        v-if="row.__scoringCanExpand"
                                        link
                                        type="primary"
                                        size="small"
                                        class="bidding-scoring-toggle"
                                        @click.stop="toggleBiddingScoringGroup(row)"
                                      >
                                        {{ row.__scoringExpanded ? '收起' : '展开' }} {{ row.__scoringChildrenCount }} 项
                                      </el-button>
                                      <span v-else-if="row.__scoringChild" class="bidding-scoring-child-marker">子项</span>
                                      <strong>{{ row.scoring_item }}</strong>
                                    </div>
                                    <small>
                                      {{ biddingAnalysisPackageLabel(row.package_type) }}
                                      <template v-if="row.full_score"> · {{ row.full_score }}</template>
                                      <template v-if="row.scoring_weight"> · 权重 {{ row.scoring_weight }}</template>
                                    </small>
                                    <div v-if="row.is_scoring_group || row.split_from_parent" class="business-object-badges">
                                      <el-tag v-if="row.is_scoring_group" size="small" type="success" effect="plain">
                                        汇总项 {{ row.child_count || (row.children || []).length }} 项
                                      </el-tag>
                                      <el-tag v-if="row.split_from_parent" size="small" type="primary" effect="plain">
                                        拆分项
                                      </el-tag>
                                    </div>
                                  </div>
                                </template>
                              </el-table-column>
                              <el-table-column prop="scoring_standard" label="评分标准说明" min-width="340" show-overflow-tooltip />
                              <el-table-column prop="gap_analysis" label="差距分析" min-width="240" show-overflow-tooltip />
                              <el-table-column prop="suggested_action" label="建议动作" min-width="240" show-overflow-tooltip />
                              <el-table-column prop="owner_role" label="责任" width="90" />
                              <el-table-column label="来源" min-width="180" show-overflow-tooltip>
                                <template #default="{ row }">{{ row.source_file || '-' }} · {{ row.source_location || '-' }}</template>
                              </el-table-column>
                            </el-table>
                          </el-tab-pane>

                          <el-tab-pane label="风险条款清单" name="risk_clause">
                            <div v-loading="biddingRiskClauseLoading || biddingRiskClauseAnalyzing" class="bidding-risk-clause-board">
                              <el-empty v-if="!biddingTenderRiskClauseRows.length" :description="biddingRiskClauseEmptyText">
                                <el-button
                                  type="warning"
                                  plain
                                  :icon="DataAnalysis"
                                  :loading="biddingRiskClauseAnalyzing"
                                  @click="analyzeBiddingRiskClause"
                                >
                                  风险分析
                                </el-button>
                              </el-empty>
                              <template v-else>
                                <section class="bidding-important-info-section">
                                  <div class="bidding-risk-detail-heading">
                                    <div>
                                      <strong>风险分析基本信息</strong>
                                      <small>{{ biddingRiskClauseRiskCount }} 条风险 · 高 {{ biddingRiskClauseHighCount }} · 中 {{ biddingRiskClauseMediumCount }} · 低 {{ biddingRiskClauseLowCount }}</small>
                                    </div>
                                    <el-tag effect="plain">{{ biddingRiskClause?.status || 'completed' }}</el-tag>
                                  </div>
                                  <el-table :data="biddingRiskClauseBasicRows" class="users-table" row-key="label">
                                    <el-table-column prop="label" label="项目" width="130" />
                                    <el-table-column prop="value" label="内容" min-width="460" show-overflow-tooltip />
                                  </el-table>
                                </section>

                                <section class="bidding-important-info-section">
                                  <div class="bidding-risk-detail-heading">
                                    <div>
                                      <strong>一、优先关注事项</strong>
                                      <small>优先谈判、优先管控、关键证据</small>
                                    </div>
                                  </div>
                                  <el-table :data="biddingRiskClausePriorityAttention" class="users-table" row-key="category">
                                    <el-table-column prop="category" label="类别" width="130" />
                                    <el-table-column prop="suggestion" label="建议" min-width="520" />
                                  </el-table>
                                </section>

                                <section class="bidding-important-info-section">
                                  <div class="bidding-risk-detail-heading">
                                    <div>
                                      <strong>二、风险清单概览</strong>
                                      <small>序号、等级、所在章节、风险说明</small>
                                    </div>
                                  </div>
                                  <el-table :data="biddingTenderRiskClauseRows" class="users-table" row-key="row_key">
                                    <el-table-column prop="risk_id" label="序号" width="85" />
                                    <el-table-column label="等级" width="90">
                                      <template #default="{ row }">
                                        <el-tag :type="biddingRiskLevelTag(row.risk_level)" effect="plain">{{ biddingRiskLevelLabel(row.risk_level) }}</el-tag>
                                      </template>
                                    </el-table-column>
                                    <el-table-column prop="source_location" label="所在章节" min-width="220" show-overflow-tooltip />
                                    <el-table-column prop="risk_explanation" label="风险说明" min-width="420" show-overflow-tooltip />
                                  </el-table>
                                </section>

                                <section class="bidding-important-info-section">
                                  <div class="bidding-risk-detail-heading">
                                    <div>
                                      <strong>三、风险条款明细</strong>
                                      <small>所在章节、风险等级、条款原文、风险说明、建议应对方式</small>
                                    </div>
                                  </div>
                                  <el-table :data="biddingTenderRiskClauseRows" class="users-table" row-key="row_key">
                                    <el-table-column prop="source_location" label="所在章节" width="210" show-overflow-tooltip />
                                    <el-table-column label="风险等级" width="100">
                                      <template #default="{ row }">
                                        <el-tag :type="biddingRiskLevelTag(row.risk_level)" effect="plain">{{ biddingRiskLevelLabel(row.risk_level) }}</el-tag>
                                      </template>
                                    </el-table-column>
                                    <el-table-column label="条款原文" min-width="330">
                                      <template #default="{ row }">
                                        <div class="operation-client bidding-important-info-value">
                                          <strong>{{ row.clause_original || '-' }}</strong>
                                          <small>{{ row.source_file || '-' }}</small>
                                        </div>
                                      </template>
                                    </el-table-column>
                                    <el-table-column prop="risk_explanation" label="风险说明" min-width="280" />
                                    <el-table-column prop="suggested_response" label="建议应对方式" min-width="300" />
                                  </el-table>
                                </section>
                              </template>
                            </div>
                          </el-tab-pane>

                          <el-tab-pane label="待复核队列" name="review_queue">
                            <el-table
                              :data="biddingTenderAnalysisReviewQueue"
                              row-key="row_key"
                              :tree-props="{ children: 'children' }"
                              class="users-table"
                              empty-text="暂无待复核项"
                            >
                              <el-table-column prop="table_label" label="成果表" width="150" />
                              <el-table-column label="复核项" min-width="240" show-overflow-tooltip>
                                <template #default="{ row }">
                                  <div class="operation-client">
                                    <strong>{{ row.title }}</strong>
                                    <small>
                                      {{ row.review_category_label || '待复核' }}
                                      <template v-if="row.item_count && row.item_count > 1"> · 合并 {{ row.item_count }} 项</template>
                                    </small>
                                  </div>
                                </template>
                              </el-table-column>
                              <el-table-column label="原因" min-width="260">
                                <template #default="{ row }">
                                  <div class="business-object-badges">
                                    <el-tag
                                      v-for="reason in row.reasons || []"
                                      :key="reason"
                                      size="small"
                                      type="warning"
                                      effect="plain"
                                    >
                                      {{ reason }}
                                    </el-tag>
                                  </div>
                                </template>
                              </el-table-column>
                              <el-table-column label="来源" min-width="180" show-overflow-tooltip>
                                <template #default="{ row }">{{ row.source_file || '-' }} · {{ row.source_location || '-' }}</template>
                              </el-table-column>
                            </el-table>
                          </el-tab-pane>
                        </el-tabs>
                      </template>
                    </section>

                    <el-collapse v-model="biddingBusinessObjectCollapse" class="bidding-debug-collapse">
                      <el-collapse-item name="businessObjects">
                        <template #title>
                          <div class="bidding-debug-collapse-title">
                            <strong>内部业务对象</strong>
                            <small>默认隐藏，用于查看系统识别出的待复核事项</small>
                          </div>
                        </template>
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Tickets /></el-icon>
                        <span>结构化投标业务对象</span>
                        <small>把招标文件要求归并为投标规则、资格审查、合同条款、报价约束和文件清单</small>
                        <el-button
                          class="section-title-action"
                          size="small"
                          type="primary"
                          plain
                          :loading="biddingBusinessObjectLlmReviewing"
                          @click="reviewBiddingBusinessObjectsWithLlm"
                        >
                          DeepSeek复核
                        </el-button>
                      </div>
                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>业务对象</span>
                          <strong>{{ biddingBusinessObjectsSummary.object_count || biddingBusinessObjectsTotal }}</strong>
                          <small>需响应 {{ biddingBusinessObjectsSummary.response_required_count || 0 }} · 报价预留 {{ biddingBusinessObjectsSummary.quote_allowance_count || 0 }} · 转答疑 {{ biddingBusinessObjectsSummary.clarification_count || 0 }} · 待复核 {{ biddingBusinessObjectsSummary.pending_count || 0 }}</small>
                        </div>
                        <div>
                          <span>对象分布</span>
                          <strong>{{ biddingBusinessObjectTypeRows.length }}</strong>
                          <small>{{ biddingBusinessObjectTypeRows.map((item) => `${item.label} ${item.count}`).join(' · ') || '暂无' }}</small>
                        </div>
                        <div>
                          <span>证据质量</span>
                          <strong>{{ biddingBusinessObjectsSummary.secondary_split_count || 0 }}</strong>
                          <small>已拆分 · 弱拆分 {{ biddingBusinessObjectsSummary.weak_split_count || 0 }} · 仍需二拆 {{ biddingBusinessObjectsSummary.needs_secondary_split_count || 0 }} · 需复核 {{ biddingBusinessObjectsSummary.needs_llm_review_count || 0 }}</small>
                        </div>
                        <div>
                          <span>DeepSeek建议</span>
                          <strong>{{ biddingBusinessObjectsSummary.llm_reviewed_count || 0 }}</strong>
                          <small>待确认 {{ biddingBusinessObjectsSummary.llm_pending_manual_count || 0 }} · 已采纳 {{ biddingBusinessObjectsSummary.llm_accepted_count || 0 }} · 已修改 {{ biddingBusinessObjectsSummary.llm_modified_count || 0 }} · 已驳回 {{ biddingBusinessObjectsSummary.llm_rejected_count || 0 }} · 异常 {{ biddingBusinessObjectsSummary.llm_error_count || 0 }}</small>
                        </div>
                      </div>

                      <div v-if="biddingBusinessObjectLlmProgress.visible" class="bidding-llm-progress-panel">
                        <div class="bidding-risk-detail-heading">
                          <div>
                            <strong>DeepSeek复核进度</strong>
                            <small>
                              第 {{ biddingBusinessObjectLlmProgress.current || 0 }} / {{ biddingBusinessObjectLlmProgress.total || 0 }} 条
                              · 当前 {{ biddingBusinessObjectLlmProgress.currentTitle || '准备中' }}
                            </small>
                          </div>
                          <el-tag :type="biddingBusinessObjectLlmReviewing ? 'warning' : 'success'" effect="plain">
                            {{ biddingBusinessObjectLlmReviewing ? '处理中' : '已结束' }}
                          </el-tag>
                        </div>
                        <el-progress
                          :percentage="biddingLlmProgressPercentage"
                          :status="biddingBusinessObjectLlmProgress.error ? 'warning' : undefined"
                        />
                        <small>
                          完成 {{ biddingBusinessObjectLlmProgress.completed }}
                          · 异常 {{ biddingBusinessObjectLlmProgress.error }}
                          · 跳过 {{ biddingBusinessObjectLlmProgress.skipped }}
                          · {{ biddingBusinessObjectLlmProgress.lastMessage || '等待开始' }}
                        </small>
                      </div>

                      <div v-if="biddingLlmReviewRows.length" class="bidding-llm-review-panel">
                        <div class="bidding-risk-detail-heading">
                          <div>
                            <strong>DeepSeek复核结果</strong>
                            <small>模型建议只作为人工确认依据，不会自动修改对象分类或复核状态</small>
                          </div>
                          <el-tag type="success" effect="plain">已返回 {{ biddingLlmReviewRows.length }} 条</el-tag>
                        </div>
                        <el-table
                          :data="biddingLlmReviewRows"
                          row-key="object_uuid"
                          class="users-table"
                          max-height="320"
                          empty-text="暂无 DeepSeek 复核结果"
                        >
                          <el-table-column label="对象" min-width="210" show-overflow-tooltip>
                            <template #default="{ row }">
                              <div class="operation-client">
                                <strong>{{ row.title }}</strong>
                                <small>{{ biddingBusinessObjectTypeLabel(row.object_type) }} · {{ row.object_subtype }}</small>
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="建议" width="150">
                            <template #default="{ row }">
                              <el-tag :type="biddingLlmDecisionTag(row.normalized?.llm_review?.decision)" effect="plain">
                                {{ biddingLlmDecisionLabel(row.normalized?.llm_review?.decision) }}
                              </el-tag>
                            </template>
                          </el-table-column>
                          <el-table-column label="置信度" width="95">
                            <template #default="{ row }">{{ biddingConfidenceLabel(row.normalized?.llm_review?.confidence) }}</template>
                          </el-table-column>
                          <el-table-column label="说明" min-width="320" show-overflow-tooltip>
                            <template #default="{ row }">
                              <div class="operation-client">
                                <span>{{ row.normalized?.llm_review?.reason || row.normalized?.llm_review_error?.error || '暂无说明' }}</span>
                                <small v-if="row.normalized?.llm_review?.suggested_reviewer_note">
                                  人工建议：{{ row.normalized.llm_review.suggested_reviewer_note }}
                                </small>
                              </div>
                            </template>
                          </el-table-column>
                          <el-table-column label="状态" width="130">
                            <template #default="{ row }">
                              <el-tag :type="biddingLlmReviewStatusTag(row.normalized?.llm_review_status)" effect="plain">
                                {{ biddingLlmReviewStatusLabel(row.normalized?.llm_review_status) }}
                              </el-tag>
                            </template>
                          </el-table-column>
                          <el-table-column label="操作" width="210" fixed="right">
                            <template #default="{ row }">
                              <div class="row-actions bidding-llm-actions">
                                <el-button
                                  size="small"
                                  type="success"
                                  plain
                                  :loading="biddingLlmDecisionSubmitting"
                                  :disabled="row.normalized?.llm_review_status !== 'pending_manual_confirm'"
                                  @click="acceptBiddingLlmReview(row)"
                                >
                                  采纳
                                </el-button>
                                <el-button
                                  size="small"
                                  type="info"
                                  plain
                                  :loading="biddingLlmDecisionSubmitting"
                                  :disabled="!['pending_manual_confirm', 'error'].includes(row.normalized?.llm_review_status)"
                                  @click="rejectBiddingLlmReview(row)"
                                >
                                  驳回
                                </el-button>
                                <el-button
                                  size="small"
                                  type="warning"
                                  plain
                                  :loading="biddingLlmDecisionSubmitting"
                                  :disabled="row.normalized?.llm_review_status !== 'pending_manual_confirm'"
                                  @click="openModifyBiddingLlmReview(row)"
                                >
                                  修改
                                </el-button>
                              </div>
                            </template>
                          </el-table-column>
                        </el-table>
                      </div>

                      <el-table
                        :data="biddingBusinessObjects"
                        row-key="object_uuid"
                        class="users-table"
                        empty-text="暂无业务对象"
                      >
                        <el-table-column label="对象" min-width="240" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.title }}</strong>
                              <small>{{ biddingBusinessObjectTypeLabel(row.object_type) }} · {{ row.normalized_value || '未抽取标准值' }}</small>
                              <div class="business-object-badges">
                                <el-tag v-if="row.normalized?.split_applied" size="small" type="success" effect="plain">已拆分</el-tag>
                                <el-tag v-if="row.normalized?.weak_split" size="small" type="warning" effect="plain">弱拆分</el-tag>
                                <el-tag v-if="row.normalized?.large_object" size="small" type="warning" effect="plain">大对象 {{ row.source_count || 0 }}</el-tag>
                                <el-tag v-if="row.normalized?.needs_llm_review" size="small" type="primary" effect="plain">需复核</el-tag>
                                <el-tag v-if="row.normalized?.llm_review_status === 'pending_manual_confirm'" size="small" type="success" effect="plain">DeepSeek已建议</el-tag>
                                <el-tag v-if="row.normalized?.llm_review_status === 'accepted'" size="small" type="success" effect="plain">建议已采纳</el-tag>
                                <el-tag v-if="row.normalized?.llm_review_status === 'modified'" size="small" type="warning" effect="plain">建议已修改</el-tag>
                                <el-tag v-if="row.normalized?.llm_review_status === 'rejected'" size="small" type="info" effect="plain">建议已驳回</el-tag>
                                <el-tag v-if="row.normalized?.llm_review_status === 'error'" size="small" type="danger" effect="plain">LLM异常</el-tag>
                                <el-tag v-if="row.normalized?.low_confidence_representative" size="small" type="danger" effect="plain">证据低置信</el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="用途" width="150">
                          <template #default="{ row }">
                            <div class="business-action-stack">
                              <el-tag :type="biddingBusinessObjectActionTag(row.normalized?.business_action)" effect="plain">
                                {{ biddingBusinessObjectActionLabel(row.normalized?.business_action) }}
                              </el-tag>
                              <small v-if="row.normalized?.secondary_business_actions?.length || row.normalized?.risk_secondary_actions?.length">
                                次级 {{ biddingBusinessObjectActionListLabel(row.normalized?.secondary_business_actions || row.normalized?.risk_secondary_actions) }}
                              </small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="责任" width="110">
                          <template #default="{ row }">{{ row.owner_role || '-' }}</template>
                        </el-table-column>
                        <el-table-column label="响应" width="95">
                          <template #default="{ row }">
                            <el-tag :type="row.response_required ? 'warning' : 'info'" effect="plain">
                              {{ row.response_required ? '需响应' : '可选' }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="来源" min-width="180" show-overflow-tooltip>
                          <template #default="{ row }">{{ row.source_file }} · {{ row.source_location }}</template>
                        </el-table-column>
                        <el-table-column label="证据" min-width="320" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.original_text }}</span>
                              <small>
                                {{ biddingBusinessObjectEvidenceQualityLabel(row.normalized?.representative_evidence_quality) }}
                                · 相关度 {{ row.normalized?.representative_evidence_relevance ?? '-' }}
                                · 上下文 {{ biddingBusinessObjectEvidenceContextLabel(row.normalized?.representative_evidence_context_quality) }}
                                · 样本 {{ row.normalized?.evidence_sample_count || row.evidence?.length || 0 }}/{{ row.normalized?.evidence_total_count || row.source_count || 0 }}
                              </small>
                              <small v-if="row.normalized?.llm_review">
                                DeepSeek {{ biddingLlmDecisionLabel(row.normalized.llm_review.decision) }}
                                · 置信度 {{ biddingConfidenceLabel(row.normalized.llm_review.confidence) }}
                                · {{ row.normalized.llm_review.reason || '待人工查看建议' }}
                              </small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="复核" width="110">
                          <template #default="{ row }">
                            <el-tag :type="biddingRiskReviewTag(row.review_status)" effect="plain">{{ biddingRiskReviewLabel(row.review_status) }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="操作" width="280" fixed="right">
                          <template #default="{ row }">
                            <div class="row-actions">
                              <el-button size="small" type="success" plain @click="reviewBiddingBusinessObject(row, 'confirmed')">确认</el-button>
                              <el-button size="small" type="warning" plain @click="reviewBiddingBusinessObject(row, 'to_clarify')">转答疑</el-button>
                              <el-button size="small" plain @click="reviewBiddingBusinessObject(row, 'to_quote_allowance')">报价预留</el-button>
                              <el-button size="small" type="info" plain @click="reviewBiddingBusinessObject(row, 'ignored')">忽略</el-button>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>
                      </el-collapse-item>
                    </el-collapse>
                  </el-tab-pane>

                  <el-tab-pane label="响应矩阵" name="responseMatrix">
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><DocumentChecked /></el-icon>
                        <span>投标响应矩阵</span>
                        <small>把业务对象、风险和关键要求转成可执行、可追踪的投标响应任务</small>
                        <el-button
                          class="section-title-action"
                          size="small"
                          type="primary"
                          plain
                          :loading="biddingResponseMatrixGenerating"
                          @click="generateBiddingResponseMatrix"
                        >
                          生成响应矩阵
                        </el-button>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>响应项</span>
                          <strong>{{ biddingResponseVisibleSummary.item_count || 0 }}</strong>
                          <small>待处理 {{ biddingResponseVisibleSummary.pending_count || 0 }} · 已完成 {{ biddingResponseVisibleSummary.done_count || 0 }} · 已忽略 {{ biddingResponseVisibleSummary.ignored_count || 0 }}</small>
                        </div>
                        <div>
                          <span>协同动作</span>
                          <strong>{{ biddingResponseWorkflowCount('clarification') + biddingResponseWorkflowCount('quote_allowance') + biddingResponseWorkflowCount('legal_review') }}</strong>
                          <small>答疑 {{ biddingResponseWorkflowCount('clarification') }} · 报价预留 {{ biddingResponseWorkflowCount('quote_allowance') }} · 法务 {{ biddingResponseWorkflowCount('legal_review') }}</small>
                        </div>
                        <div>
                          <span>风险响应</span>
                          <strong>{{ biddingResponseVisibleSummary.high_risk_count || 0 }}</strong>
                          <small>高风险响应项；覆盖风险 {{ biddingResponseVisibleSummary.covered_risk_count || 0 }} 条</small>
                        </div>
                        <div>
                          <span>覆盖解释</span>
                          <strong>{{ biddingResponseVisibleSummary.covered_requirement_count || 0 }}</strong>
                          <small>覆盖要求 · 技术聚类 {{ biddingResponseVisibleSummary.clustered_requirement_count || 0 }} 项 · 拆分 {{ biddingResponseVisibleSummary.split_item_count || 0 }} 项</small>
                        </div>
                      </div>

                      <div class="bidding-response-toolbar">
                        <div class="bidding-response-role-filter">
                          <span>复核视图</span>
                          <el-radio-group
                            v-model="biddingResponseReviewRole"
                            size="small"
                          >
                            <el-radio-button
                              v-for="option in biddingResponseReviewRoleOptions"
                              :key="option.value"
                              :label="option.value"
                              :value="option.value"
                            >
                              {{ option.label }}
                            </el-radio-button>
                          </el-radio-group>
                          <el-button
                            size="small"
                            plain
                            :disabled="!biddingResponseExpandableRows.length"
                            @click="expandAllBiddingResponseGroups"
                          >
                            展开分组
                          </el-button>
                          <el-button
                            size="small"
                            plain
                            :disabled="!biddingResponseExpandedKeys.length"
                            @click="collapseAllBiddingResponseGroups"
                          >
                            收起分组
                          </el-button>
                        </div>
                        <small>
                          质量标记 {{ biddingResponseVisibleSummary.quality_flag_count || 0 }} ·
                          第1波 {{ biddingResponseVisibleSummary.by_review_wave?.wave_1 || 0 }} ·
                          第2波 {{ biddingResponseVisibleSummary.by_review_wave?.wave_2 || 0 }} ·
                          第3波 {{ biddingResponseVisibleSummary.by_review_wave?.wave_3 || 0 }} ·
                          经营 {{ biddingResponseVisibleSummary.by_primary_review_role?.['经营'] || 0 }} ·
                          预算 {{ biddingResponseVisibleSummary.by_primary_review_role?.['预算'] || 0 }} ·
                          技术 {{ biddingResponseVisibleSummary.by_primary_review_role?.['技术'] || 0 }} ·
                          法务 {{ biddingResponseVisibleSummary.by_primary_review_role?.['法务'] || 0 }}
                        </small>
                      </div>

                      <el-table
                        :data="visibleBiddingResponseItems"
                        row-key="response_item_uuid"
                        :tree-props="{ children: 'children' }"
                        :expand-row-keys="biddingResponseExpandedKeys"
                        class="users-table"
                        empty-text="暂无响应矩阵，请先生成初稿"
                      >
                        <el-table-column label="响应项" min-width="250" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.response_title }}</strong>
                              <small>
                                {{ biddingResponseCreatedFromLabel(row.created_from) }}
                                · {{ biddingResponseCategoryLabel(row.response_category) }}
                                <template v-if="row.covered_requirement_count || row.covered_risk_count">
                                  · 覆盖 {{ row.covered_requirement_count || 0 }} 要求 / {{ row.covered_risk_count || 0 }} 风险
                                </template>
                                <template v-if="biddingResponsePrimaryRole(row)"> · 主责 {{ biddingResponsePrimaryRole(row) }}</template>
                                <template v-if="biddingResponseSupportingRoles(row).length"> · 协同 {{ biddingResponseSupportingRoles(row).join('/') }}</template>
                                <template v-if="row.business_object_title"> · {{ row.business_object_title }}</template>
                              </small>
                              <div v-if="biddingResponseQualityTags(row).length || biddingResponseTaskDisplayLabel(row) || row.review_wave_label" class="bidding-response-chips">
                                <el-tag
                                  v-if="biddingResponseTaskDisplayLabel(row)"
                                  size="small"
                                  :type="biddingResponseTaskDisplayType(row) === 'group_task' ? 'primary' : biddingResponseTaskDisplayType(row) === 'summary_task' ? 'warning' : 'info'"
                                  effect="plain"
                                >
                                  {{ biddingResponseTaskDisplayLabel(row) }}
                                  <template v-if="biddingResponseTaskDisplayType(row) === 'summary_task' && (row.task_group_child_count || biddingResponseGroupChildren(row).length)">
                                    · {{ row.task_group_child_count || biddingResponseGroupChildren(row).length }}组
                                  </template>
                                </el-tag>
                                <el-button
                                  v-if="biddingResponseGroupChildren(row).length"
                                  size="small"
                                  text
                                  type="primary"
                                  @click.stop="toggleBiddingResponseGroup(row)"
                                >
                                  {{ biddingResponseIsGroupExpanded(row) ? '收起分组' : `展开${biddingResponseGroupChildren(row).length}组` }}
                                </el-button>
                                <el-tag
                                  v-if="row.review_wave_label"
                                  size="small"
                                  :type="biddingResponsePriorityTag(row.review_priority)"
                                  effect="plain"
                                >
                                  {{ row.review_wave_label }} · {{ row.review_priority || '-' }}
                                </el-tag>
                                <el-tag
                                  v-for="tag in biddingResponseQualityTags(row)"
                                  :key="tag"
                                  size="small"
                                  type="warning"
                                  effect="plain"
                                >
                                  {{ biddingResponseQualityLabel(tag) }}
                                </el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="原文证据" min-width="320" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.source_text }}</span>
                              <small v-if="row.evidence?.length">
                                {{ row.evidence[0].source_file || '-' }} · {{ row.evidence[0].source_location || '-' }}
                              </small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="响应动作" width="200">
                          <template #default="{ row }">
                            <div v-if="row.is_virtual_group_parent" class="operation-client">
                              <el-tag type="warning" effect="plain">展开后处理子项</el-tag>
                            </div>
                            <div v-else class="operation-client">
                              <el-select
                                size="small"
                                :model-value="row.response_action"
                                :disabled="biddingResponseItemUpdating"
                                @change="(value) => updateBiddingResponseItem(row, { response_action: value })"
                              >
                                <el-option
                                  v-for="option in biddingResponseActionOptions"
                                  :key="option.value"
                                  :label="option.label"
                                  :value="option.value"
                                />
                              </el-select>
                              <div v-if="biddingResponseLinkedActions(row).length > 1" class="bidding-response-chips">
                                <el-tag
                                  v-for="action in biddingResponseLinkedActions(row).filter((item) => item.action !== row.response_action)"
                                  :key="action.action"
                                  size="small"
                                  :type="biddingResponseActionTag(action.action)"
                                  effect="plain"
                                >
                                  {{ biddingResponseActionLabel(action.action) }}
                                </el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="状态" width="150">
                          <template #default="{ row }">
                            <el-tag v-if="row.is_virtual_group_parent" type="info" effect="plain">汇总</el-tag>
                            <el-select
                              v-else
                              size="small"
                              :model-value="row.status"
                              :disabled="biddingResponseItemUpdating"
                              @change="(value) => updateBiddingResponseItem(row, { status: value })"
                            >
                              <el-option
                                v-for="option in biddingResponseStatusOptions"
                                :key="option.value"
                                :label="option.label"
                                :value="option.value"
                              />
                            </el-select>
                          </template>
                        </el-table-column>
                        <el-table-column label="风险" width="90">
                          <template #default="{ row }">
                            <el-tag :type="biddingRiskLevelTag(row.risk_level)" effect="plain">{{ biddingRiskLevelLabel(row.risk_level) }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="责任" width="110">
                          <template #default="{ row }">
                            <div class="operation-client">
                              <el-button size="small" text @click="editBiddingResponseItemOwner(row)">
                                {{ biddingResponsePrimaryRole(row) || row.owner_role || '未分配' }}
                              </el-button>
                              <small v-if="biddingResponseSupportingRoles(row).length">协同 {{ biddingResponseSupportingRoles(row).join('/') }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="说明/备注" min-width="230" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.response_note || '暂无响应说明' }}</span>
                              <small v-if="biddingResponseReviewActionText(row)">{{ biddingResponseReviewActionText(row) }}</small>
                              <small v-if="biddingResponseDoneText(row)">{{ biddingResponseDoneText(row) }}</small>
                              <small v-if="row.priority_reason">{{ row.review_priority_label }}：{{ row.priority_reason }}</small>
                              <small v-if="biddingResponseCoverageText(row)">{{ biddingResponseCoverageText(row) }}</small>
                              <small v-if="biddingResponseQualityText(row)">{{ biddingResponseQualityText(row) }}</small>
                              <small v-if="row.reviewer_note">复核：{{ row.reviewer_note }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="操作" width="220" fixed="right">
                          <template #default="{ row }">
                            <div v-if="row.is_virtual_group_parent" class="row-actions">
                              <el-button size="small" type="primary" plain @click.stop="toggleBiddingResponseGroup(row)">
                                {{ biddingResponseIsGroupExpanded(row) ? '收起分组' : `展开${biddingResponseGroupChildren(row).length}组` }}
                              </el-button>
                            </div>
                            <div v-else class="row-actions">
                              <el-button size="small" type="success" plain @click="updateBiddingResponseItem(row, { status: 'done' })">完成</el-button>
                              <el-button size="small" type="info" plain @click="updateBiddingResponseItem(row, { status: 'ignored' })">忽略</el-button>
                              <el-button size="small" plain @click="editBiddingResponseItemNote(row)">备注</el-button>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>

                  </el-tab-pane>

                  <el-tab-pane label="商务标草案" name="businessBidDraft">
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Document /></el-icon>
                        <span>投标文件格式确认</span>
                        <small>先确认本项目商务/技术分册和目录项归属，再分别进入商务标、技术标草案链路</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button
                            size="small"
                            plain
                            :loading="biddingFileFormatLoading"
                            @click="loadBiddingFileFormatPlan()"
                          >
                            刷新
                          </el-button>
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :loading="biddingFileFormatGenerating"
                            @click="generateBiddingFileFormatPlan"
                          >
                            生成格式表
                          </el-button>
                          <el-button
                            size="small"
                            plain
                            :disabled="!biddingFileFormatPackages.length"
                            @click="openBiddingFileFormatItemDialog"
                          >
                            新增目录项
                          </el-button>
                          <el-button
                            size="small"
                            type="success"
                            plain
                            :loading="biddingFileFormatConfirming"
                            :disabled="!biddingFileFormatPackages.length"
                            @click="confirmBiddingFileFormatPlan"
                          >
                            {{ biddingFileFormatPlan?.review_status === 'confirmed' ? '重新确认' : '确认格式' }}
                          </el-button>
                        </div>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>识别结论</span>
                          <strong>{{ biddingFileFormatPlan?.package_mode_label || '-' }}</strong>
                          <small>{{ biddingFileFormatPlan?.format_source_label || '等待识别' }}</small>
                        </div>
                        <div>
                          <span>文件包</span>
                          <strong>{{ biddingFileFormatSummary.package_count || 0 }}</strong>
                          <small>目录项 {{ biddingFileFormatSummary.item_count || 0 }} · 正文 {{ biddingFileFormatSummary.draft_section_count || 0 }}</small>
                        </div>
                        <div>
                          <span>固定表单</span>
                          <strong>{{ biddingFileFormatSummary.fixed_form_count || 0 }}</strong>
                          <small>报价表 {{ biddingFileFormatSummary.pricing_table_count || 0 }} · 附件 {{ biddingFileFormatSummary.attachment_count || 0 }}</small>
                        </div>
                        <div>
                          <span>确认状态</span>
                          <strong>{{ biddingFileFormatReviewStatusLabel(biddingFileFormatPlan?.review_status) }}</strong>
                          <small>{{ biddingFileFormatPlan?.confirmed_at ? `已确认 ${biddingFileFormatPlan.confirmed_at}` : '确认后再进入草稿生成' }}</small>
                        </div>
                      </div>

                      <el-alert
                        v-if="biddingFileFormatWarnings.length"
                        class="dashboard-alert"
                        type="warning"
                        show-icon
                        :closable="false"
                        :title="biddingFileFormatWarnings[0].message || '投标文件格式仍需人工确认'"
                      ></el-alert>

                      <el-table
                        v-loading="biddingFileFormatLoading || biddingFileFormatGenerating"
                        :data="biddingFileFormatRows"
                        row-key="item_key"
                        class="users-table"
                        empty-text="暂无投标文件格式识别结果，请先生成格式表"
                      >
                        <el-table-column label="文件包/目录项" min-width="300" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.item_title }}</strong>
                              <small>{{ row.package_title }}</small>
                              <div class="bidding-response-chips">
                                <el-tag size="small" effect="plain">{{ row.content_type_label || biddingFileFormatContentTypeLabel(row.content_type) }}</el-tag>
                                <el-tag v-if="row.requires_signature" size="small" type="warning" effect="plain">签章/签字</el-tag>
                                <el-tag v-if="row.requires_attachment" size="small" type="info" effect="plain">需附件</el-tag>
                                <el-tag v-if="row.conflict_status === 'cross_package_duplicate'" size="small" type="danger" effect="plain">跨包去重</el-tag>
                              </div>
                              <small v-if="row.conflict_note">{{ row.conflict_note }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="负责人" width="90">
                          <template #default="{ row }">{{ row.owner_role || '-' }}</template>
                        </el-table-column>
                        <el-table-column label="生成方式" width="130">
                          <template #default="{ row }">{{ biddingFileFormatGenerationStrategyLabel(row.generation_strategy) }}</template>
                        </el-table-column>
                        <el-table-column label="证据原文" min-width="360" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.evidence?.[0]?.original_text || '-' }}</span>
                              <small v-if="row.evidence?.[0]">{{ row.evidence[0].source_file }} · {{ row.evidence[0].source_location }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="操作" width="250" fixed="right">
                          <template #default="{ row }">
                            <div class="row-actions">
                              <el-button
                                v-if="row.package_key !== 'business' && biddingFileFormatHasPackage('business')"
                                size="small"
                                plain
                                @click="moveBiddingFileFormatItem(row, 'business')"
                              >
                                移到商务标
                              </el-button>
                              <el-button
                                v-if="row.package_key !== 'technical' && biddingFileFormatHasPackage('technical')"
                                size="small"
                                plain
                                @click="moveBiddingFileFormatItem(row, 'technical')"
                              >
                                移到技术标
                              </el-button>
                              <el-button size="small" type="danger" plain @click="removeBiddingFileFormatItem(row)">
                                删除
                              </el-button>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>

                      <el-table
                        v-if="biddingFileFormatPackagingRequirements.length"
                        :data="biddingFileFormatPackagingRequirements"
                        row-key="requirement_key"
                        class="users-table bidding-subtable"
                      >
                        <el-table-column label="装订/密封/电子标要求" min-width="260" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.requirement_title }}</strong>
                              <small>{{ row.evidence?.[0]?.source_file }} · {{ row.evidence?.[0]?.source_location }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="风险" width="90">
                          <template #default="{ row }">
                            <el-tag :type="biddingRiskLevelTag(row.risk_level)" effect="plain">{{ biddingRiskLevelLabel(row.risk_level) }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="证据原文" min-width="360" show-overflow-tooltip>
                          <template #default="{ row }">{{ row.evidence?.[0]?.original_text || '-' }}</template>
                        </el-table-column>
                      </el-table>

                      <el-table
                        v-if="biddingFileFormatAuditEvents.length"
                        :data="biddingFileFormatAuditEvents"
                        row-key="event_uuid"
                        class="users-table bidding-subtable"
                      >
                        <el-table-column label="编辑审计" min-width="220" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ biddingFileFormatEventTypeLabel(row.event_type) }}：{{ row.item_title || '-' }}</strong>
                              <small>
                                {{ row.pending ? '待确认保存' : `已保存 ${formatDate(row.created_at)}` }}
                              </small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="文件包变化" min-width="180">
                          <template #default="{ row }">
                            {{ biddingFileFormatPackageLabel(row.from_package_key) || '-' }}
                            <span v-if="row.from_package_key || row.to_package_key"> → </span>
                            {{ biddingFileFormatPackageLabel(row.to_package_key) || '-' }}
                          </template>
                        </el-table-column>
                        <el-table-column label="说明" min-width="260" show-overflow-tooltip>
                          <template #default="{ row }">{{ row.detail?.note || '-' }}</template>
                        </el-table-column>
                      </el-table>

                      <el-dialog
                        v-model="biddingFileFormatItemDialog.visible"
                        title="新增目录项"
                        width="520px"
                        append-to-body
                      >
                        <el-form label-position="top">
                          <div class="form-grid-2">
                            <el-form-item label="文件包">
                              <el-select v-model="biddingFileFormatItemDialog.package_key" placeholder="选择文件包">
                                <el-option
                                  v-for="pkg in biddingFileFormatPackages"
                                  :key="pkg.package_key"
                                  :label="pkg.package_title"
                                  :value="pkg.package_key"
                                ></el-option>
                              </el-select>
                            </el-form-item>
                            <el-form-item label="负责人">
                              <el-select v-model="biddingFileFormatItemDialog.owner_role" placeholder="选择负责人">
                                <el-option
                                  v-for="option in biddingFileFormatOwnerOptions"
                                  :key="option.value"
                                  :label="option.label"
                                  :value="option.value"
                                ></el-option>
                              </el-select>
                            </el-form-item>
                          </div>
                          <el-form-item label="目录项名称">
                            <el-input v-model="biddingFileFormatItemDialog.item_title" placeholder="例如：投标函、施工组织设计、材料品牌表" />
                          </el-form-item>
                          <div class="form-grid-2">
                            <el-form-item label="内容类型">
                              <el-select v-model="biddingFileFormatItemDialog.content_type" @change="syncBiddingFileFormatDialogStrategy">
                                <el-option
                                  v-for="option in biddingFileFormatContentTypeOptions"
                                  :key="option.value"
                                  :label="option.label"
                                  :value="option.value"
                                ></el-option>
                              </el-select>
                            </el-form-item>
                            <el-form-item label="生成方式">
                              <el-select v-model="biddingFileFormatItemDialog.generation_strategy">
                                <el-option
                                  v-for="option in biddingFileFormatGenerationOptions"
                                  :key="option.value"
                                  :label="option.label"
                                  :value="option.value"
                                ></el-option>
                              </el-select>
                            </el-form-item>
                          </div>
                          <div class="bidding-response-chips">
                            <el-checkbox v-model="biddingFileFormatItemDialog.requires_signature">需要签字/盖章</el-checkbox>
                            <el-checkbox v-model="biddingFileFormatItemDialog.requires_attachment">需要附件</el-checkbox>
                          </div>
                        </el-form>
                        <template #footer>
                          <el-button @click="biddingFileFormatItemDialog.visible = false">取消</el-button>
                          <el-button type="primary" @click="addBiddingFileFormatItem">新增</el-button>
                        </template>
                      </el-dialog>
                    </section>

                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Document /></el-icon>
                        <span>商务标资料需求与补齐清单</span>
                        <small>仅处理已分配到商务标的企业资料、附件、表单字段和报价数据，先补齐再生成商务标草稿</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button
                            size="small"
                            plain
                            :loading="biddingMaterialRequirementsLoading"
                            @click="loadBiddingMaterialRequirements(undefined, 'business')"
                          >
                            刷新
                          </el-button>
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :loading="biddingMaterialRequirementsGenerating"
                            @click="generateBiddingMaterialRequirements"
                          >
                            生成商务标补齐清单
                          </el-button>
                        </div>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>商务标资料项</span>
                          <strong>{{ biddingMaterialRequirementSummary.total || 0 }}</strong>
                          <small>企业资料 {{ biddingMaterialRequirementSummary.enterprise_profile_requirement_count || 0 }} · 上传 {{ biddingMaterialRequirementSummary.manual_upload_count || 0 }}</small>
                        </div>
                        <div>
                          <span>待补齐</span>
                          <strong>{{ biddingMaterialRequirementSummary.open_count || 0 }}</strong>
                          <small>缺失 {{ biddingMaterialRequirementSummary.missing_count || 0 }} · 候选 {{ biddingMaterialRequirementSummary.candidate_found_count || 0 }}</small>
                        </div>
                        <div>
                          <span>已提交</span>
                          <strong>{{ biddingMaterialRequirementSummary.submitted_count || 0 }}</strong>
                          <small>待确认可用</small>
                        </div>
                        <div>
                          <span>可用率</span>
                          <strong>{{ Math.round((biddingMaterialRequirementSummary.completion_rate || 0) * 100) }}%</strong>
                          <small>已确认 {{ biddingMaterialRequirementSummary.resolved_count || 0 }} · 高优先级待处理 {{ biddingMaterialRequirementSummary.high_priority_open_count || 0 }}</small>
                        </div>
                      </div>

                      <el-table
                        v-loading="biddingMaterialRequirementsLoading || biddingMaterialRequirementsGenerating"
                        :data="biddingMaterialRequirementRows"
                        row-key="requirement_uuid"
                        class="users-table"
                        empty-text="暂无资料需求清单，请先确认投标文件格式后点击生成补齐清单"
                      >
                        <el-table-column label="资料需求" min-width="320" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.title }}</strong>
                              <small>{{ row.description || row.item_title }}</small>
                              <div class="bidding-response-chips">
                                <el-tag size="small" effect="plain">{{ biddingMaterialRequirementTypeLabel(row.requirement_type) }}</el-tag>
                                <el-tag size="small" :type="biddingMaterialPriorityTag(row.priority)" effect="plain">{{ row.priority === 'high' ? '高优先级' : '普通' }}</el-tag>
                                <el-tag v-if="row.profile_category" size="small" type="info" effect="plain">{{ biddingEnterpriseProfileCategoryLabel(row.profile_category) }}</el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="来源目录" min-width="220" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.item_title }}</span>
                              <small>{{ row.package_title || row.package_key || '-' }}</small>
                              <small v-if="row.source_file">{{ row.source_file }} · {{ row.source_location }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="补齐方式" width="130">
                          <template #default="{ row }">{{ biddingMaterialFulfillmentModeLabel(row.fulfillment_mode) }}</template>
                        </el-table-column>
                        <el-table-column label="状态" width="120">
                          <template #default="{ row }">
                            <el-tag :type="biddingMaterialRequirementStatusTag(row.status)" effect="plain">
                              {{ biddingMaterialRequirementStatusLabel(row.status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="候选/提交" min-width="260" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.candidate_profile_item?.title || row.normalized?.candidate_profile_item?.title || row.submitted_value || '-' }}</span>
                              <small v-if="(row.submitted_profile_item_uuids?.length || 0) || (row.submitted_file_ids?.length || 0)">
                                已提交：企业资料 {{ row.submitted_profile_item_uuids?.length || 0 }} 份 / 补充文件 {{ row.submitted_file_ids?.length || 0 }} 份
                              </small>
                              <small v-else-if="row.submitted_profile_item_uuid">已绑定资料：{{ row.submitted_profile_item_uuid }}</small>
                              <small v-else-if="row.candidates?.length">候选 {{ row.candidates.length }} 条 · {{ row.candidate_profile_item?.summary || row.candidates?.[0]?.summary || '待人工确认' }}</small>
                              <small v-else>{{ row.notes || '暂无候选，需人工补充' }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="操作" width="260" fixed="right">
                          <template #default="{ row }">
                            <div class="row-actions">
                              <el-button
                                v-if="row.candidate_profile_item_uuid && !['approved', 'applied', 'not_applicable'].includes(row.status)"
                                size="small"
                                type="success"
                                plain
                                :loading="biddingMaterialRequirementUpdatingUuid === row.requirement_uuid"
                                @click="useBiddingMaterialCandidate(row)"
                              >
                                采用候选
                              </el-button>
                              <el-button
                                size="small"
                                plain
                                :loading="biddingMaterialRequirementUpdatingUuid === row.requirement_uuid"
                                @click="submitBiddingMaterialValue(row)"
                              >
                                填写
                              </el-button>
                              <el-button
                                v-if="['submitted', 'candidate_found'].includes(row.status) || row.submitted_value || row.submitted_profile_item_uuid || row.submitted_file_id || row.submitted_profile_item_uuids?.length || row.submitted_file_ids?.length"
                                size="small"
                                type="primary"
                                plain
                                :loading="biddingMaterialRequirementUpdatingUuid === row.requirement_uuid"
                                @click="approveBiddingMaterialRequirement(row)"
                              >
                                确认可用
                              </el-button>
                              <el-button
                                size="small"
                                type="info"
                                plain
                                :loading="biddingMaterialRequirementUpdatingUuid === row.requirement_uuid"
                                @click="markBiddingMaterialRequirementNotApplicable(row)"
                              >
                                不适用
                              </el-button>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>

                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Document /></el-icon>
                        <span>商务标目录骨架</span>
                        <small>仅生成商务标分册下的章节草稿，技术方案和施工组织设计不会进入这里</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button
                            size="small"
                            plain
                            :loading="biddingDraftOutlineLoading"
                            @click="loadBiddingDraftOutline()"
                          >
                            刷新
                          </el-button>
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :loading="biddingDraftOutlineGenerating"
                            @click="generateBiddingDraftOutline"
                          >
                            生成商务标目录
                          </el-button>
                        </div>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>章节</span>
                          <strong>{{ biddingDraftOutlineSummary.section_count || 0 }}</strong>
                          <small>一级 {{ biddingDraftOutlineSummary.parent_section_count || 0 }} · 任务 {{ biddingDraftOutlineSummary.task_section_count || 0 }}</small>
                        </div>
                        <div>
                          <span>可起草</span>
                          <strong>{{ biddingDraftOutlineSummary.placeholder_draft_count || biddingDraftOutlineSummary.can_generate_draft_count || 0 }}</strong>
                          <small>正式 {{ biddingDraftOutlineSummary.formal_draft_ready_count || 0 }} · 占位 {{ biddingDraftOutlineSummary.placeholder_draft_count || 0 }}</small>
                        </div>
                        <div>
                          <span>阻断</span>
                          <strong>{{ biddingDraftOutlineSummary.blocked_section_count || 0 }}</strong>
                          <small>法务高风险、缺少响应矩阵或需决策项先生成复核说明</small>
                        </div>
                        <div>
                          <span>来源</span>
                          <strong>{{ biddingDraftOutlineSourceText(biddingDraftOutlineSource) }}</strong>
                          <small>{{ biddingDraftOutlineSourceDetail(biddingDraftOutlineSource, biddingDraftOutlineSummary) }}</small>
                        </div>
                      </div>

                      <el-alert
                        v-if="biddingDraftOutlineWarnings.length"
                        class="dashboard-alert"
                        type="warning"
                        show-icon
                        :closable="false"
                        :title="biddingDraftOutlineWarnings[0].message || '目录骨架仍有待处理事项'"
                      ></el-alert>

                      <el-table
                        v-loading="biddingDraftOutlineLoading || biddingDraftOutlineGenerating || biddingDraftSectionsLoading"
                        :data="biddingDraftOutlineSections"
                        row-key="section_key"
                        class="users-table"
                        empty-text="暂无投标书目录骨架，请先生成响应矩阵后点击生成目录骨架"
                      >
                        <el-table-column label="章节" min-width="280" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client" :style="{ paddingLeft: `${Math.max(0, Number(row.level || 1) - 1) * 18}px` }">
                              <strong>{{ row.section_title }}</strong>
                              <small>{{ row.description || row.source_summary }}</small>
                              <small v-if="row.split_reason">{{ row.split_reason }}</small>
                              <div class="bidding-response-chips">
                                <el-tag size="small" :type="row.level === 1 ? 'primary' : 'info'" effect="plain">
                                  {{ row.level === 1 ? '一级章节' : '章节任务' }}
                                </el-tag>
                                <el-tag size="small" effect="plain">{{ biddingDraftOutlineSectionTypeLabel(row.section_type) }}</el-tag>
                                <el-tag v-if="row.package_title" size="small" type="success" effect="plain">
                                  {{ row.package_title }}
                                </el-tag>
                                <el-tag v-if="row.content_type_label || row.content_type" size="small" effect="plain">
                                  {{ row.content_type_label || biddingFileFormatContentTypeLabel(row.content_type) }}
                                </el-tag>
                                <el-tag v-if="row.generation_decision?.label" size="small" effect="plain">
                                  {{ row.generation_decision.label }}
                                </el-tag>
                                <el-tag v-if="row.format_plan_review_status" size="small" :type="row.format_plan_review_status === 'confirmed' ? 'success' : 'warning'" effect="plain">
                                  {{ biddingFileFormatReviewStatusLabel(row.format_plan_review_status) }}
                                </el-tag>
                                <el-tag v-if="row.source_mapping" size="small" :type="biddingDraftOutlineMappingTag(row.source_mapping)" effect="plain">
                                  {{ biddingDraftOutlineMappingLabel(row.source_mapping) }}
                                </el-tag>
                                <el-tag v-if="row.split_from_generic_title" size="small" type="warning" effect="plain">
                                  {{ biddingDraftGenericSplitLabel(row) }}
                                </el-tag>
                                <el-tag v-if="row.needs_secondary_split" size="small" type="danger" effect="plain">
                                  需二次拆分
                                </el-tag>
                                <el-tag v-if="biddingDraftNeedsUpgrade(row)" size="small" type="warning" effect="plain">
                                  需升级
                                </el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="主责" width="95">
                          <template #default="{ row }">{{ row.owner_role || '-' }}</template>
                        </el-table-column>
                        <el-table-column label="状态" width="120">
                          <template #default="{ row }">
                            <el-tag :type="biddingDraftOutlineStatusTag(row.draft_status)" effect="plain">
                              {{ biddingDraftOutlineStatusLabel(row.draft_status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="关联" width="170">
                          <template #default="{ row }">
                            <div v-if="row.outline_source === 'file_format_plan'" class="operation-client">
                              <span>响应 {{ row.response_item_count || 0 }} · 要求 {{ row.requirement_count || 0 }} · 风险 {{ row.risk_count || 0 }}</span>
                              <small>{{ row.generation_strategy ? biddingFileFormatGenerationStrategyLabel(row.generation_strategy) : '文件包汇总' }} · {{ biddingDraftOutlineMappingLabel(row.source_mapping) }} · 证据 {{ row.evidence_count || 0 }}</small>
                            </div>
                            <div v-else class="operation-client">
                              <span>响应 {{ row.response_item_count || 0 }} · 子项 {{ row.child_section_count || 0 }}</span>
                              <small>要求 {{ row.requirement_count || 0 }} · 风险 {{ row.risk_count || 0 }} · 证据 {{ row.evidence_count || 0 }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="缺口" min-width="260" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ biddingDraftOutlineListText(row.missing_inputs, '暂无明显缺口') }}</span>
                              <small>{{ biddingDraftOutlineDraftModeText(row) }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="风险/完成标准" min-width="300" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ biddingDraftOutlineListText(row.risk_warnings, '暂无高风险提示') }}</span>
                              <small>{{ biddingDraftOutlineListText(row.review_checklist, row.source_summary || '暂无完成标准') }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="草稿" width="190" fixed="right">
                          <template #default="{ row }">
                            <div v-if="row.level === 2" class="row-actions">
                              <el-button
                                size="small"
                                type="primary"
                                plain
                                :loading="biddingDraftSectionGeneratingKey === row.section_key"
                                :disabled="Boolean(biddingDraftSectionGeneratingKey)"
                                @click="generateBiddingDraftSection(row)"
                              >
                                {{ biddingDraftOutlineActionButtonText(row) }}
                              </el-button>
                              <el-button
                                v-if="biddingDraftForOutlineSection(row)"
                                size="small"
                                plain
                                @click="openBiddingDraftPreview(row)"
                              >
                                查看
                              </el-button>
                            </div>
                            <div v-else class="operation-client">
                              <el-tag size="small" type="info" effect="plain">汇总章节</el-tag>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>
                  </el-tab-pane>

                  <el-tab-pane label="技术标草案" name="technicalBidDraft">
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><DocumentChecked /></el-icon>
                        <span>投标文件组成识别</span>
                        <small>从招标文件“投标文件组成”出发，分清固定企业资料与项目专属抽取内容</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button
                            size="small"
                            plain
                            :loading="biddingTechnicalCompositionLoading"
                            @click="loadBiddingTechnicalComposition()"
                          >
                            刷新
                          </el-button>
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :loading="biddingTechnicalCompositionGenerating"
                            @click="generateBiddingTechnicalComposition"
                          >
                            LLM识别技术标组成
                          </el-button>
                        </div>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>组成项</span>
                          <strong>{{ biddingTechnicalCompositionSummary.component_count || 0 }}</strong>
                          <small>原文 {{ biddingTechnicalCompositionSummary.source_item_count || 0 }} 项 · LLM {{ biddingTechnicalCompositionSummary.llm_component_count || 0 }} 项</small>
                        </div>
                        <div>
                          <span>固定资料</span>
                          <strong>{{ biddingTechnicalCompositionSummary.enterprise_profile_need_count || 0 }}</strong>
                          <small>自动匹配 {{ biddingTechnicalCompositionSummary.auto_matched_profile_count || 0 }}</small>
                        </div>
                        <div>
                          <span>招标抽取</span>
                          <strong>{{ biddingTechnicalCompositionSummary.tender_document_need_count || 0 }}</strong>
                          <small>由 LLM 抽取并润色</small>
                        </div>
                        <div>
                          <span>待人工</span>
                          <strong>{{ biddingTechnicalCompositionSummary.manual_requirement_count || 0 }}</strong>
                          <small>修复漏项 {{ biddingTechnicalCompositionSummary.repaired_missing_count || 0 }} · 已同步到资料需求</small>
                        </div>
                      </div>

                      <el-alert
                        v-if="biddingTechnicalCompositionWarnings.length"
                        class="dashboard-alert"
                        type="warning"
                        show-icon
                        :closable="false"
                        :title="biddingTechnicalCompositionWarnings[0].message || '技术标组成识别仍有待处理事项'"
                      ></el-alert>

                      <el-table
                        v-loading="biddingTechnicalCompositionLoading || biddingTechnicalCompositionGenerating"
                        :data="biddingTechnicalCompositionComponents"
                        row-key="component_key"
                        class="users-table"
                        empty-text="暂无技术标组成识别结果，请点击 LLM识别技术标组成"
                      >
                        <el-table-column label="组成项" min-width="260" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.component_title }}</strong>
                              <small>{{ row.classification_reason || row.draft_instruction || '-' }}</small>
                              <div class="bidding-response-chips">
                                <el-tag v-if="row.source_item_no" size="small" effect="plain">{{ row.source_item_no }}</el-tag>
                                <el-tag size="small" effect="plain">{{ biddingTechnicalCompositionClassLabel(row.classification) }}</el-tag>
                                <el-tag v-if="row.coverage_repair" size="small" type="warning" effect="plain">待复核漏项</el-tag>
                                <el-tag size="small" type="info" effect="plain">技术标</el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="资料与信息来源" min-width="360">
                          <template #default="{ row }">
                            <div class="operation-client">
                              <div
                                v-for="need in row.information_needs || []"
                                :key="need.need_key"
                                class="bidding-source-need"
                              >
                                <el-tag size="small" :type="biddingTechnicalCompositionSourceTag(need.source_type)" effect="plain">
                                  {{ biddingTechnicalCompositionSourceLabel(need.source_type) }}
                                </el-tag>
                                <span>{{ need.need_title }}</span>
                                <small v-if="need.source_type === 'enterprise_profile'">
                                  {{
                                    biddingTechnicalCompositionRequirementFor(row, need)?.submitted_profile_item_uuid
                                      ? `已匹配企业资料，得分 ${biddingTechnicalCompositionRequirementFor(row, need)?.match_score || '-'}`
                                      : '未找到足够接近的企业资料，需人工填写'
                                  }}
                                </small>
                                <small v-else>{{ need.reason || need.query || '-' }}</small>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="招标文件抽取/润色" min-width="320" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <template v-for="need in row.information_needs || []" :key="`polished-${need.need_key}`">
                                <small v-if="need.source_type === 'tender_document'">{{ need.polished_text || need.query || '-' }}</small>
                              </template>
                              <span v-if="!(row.information_needs || []).some((need) => need.source_type === 'tender_document')">-</span>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="证据" min-width="240" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.source_evidence?.[0]?.source_file || '-' }}</span>
                              <small>{{ row.source_evidence?.[0]?.source_location || '-' }}</small>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>

                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Document /></el-icon>
                        <span>技术标资料需求与补齐清单</span>
                        <small>仅处理已分配到技术标的格式项、附件和正文素材，不影响商务标草案链路</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button
                            size="small"
                            plain
                            :loading="biddingMaterialRequirementsLoading"
                            @click="loadBiddingMaterialRequirements(undefined, 'technical')"
                          >
                            刷新
                          </el-button>
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :loading="biddingMaterialRequirementsGenerating || biddingTechnicalCompositionGenerating"
                            @click="generateBiddingMaterialRequirements"
                          >
                            LLM生成技术标补齐清单
                          </el-button>
                        </div>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>技术标资料项</span>
                          <strong>{{ biddingMaterialRequirementSummary.total || 0 }}</strong>
                          <small>企业资料 {{ biddingMaterialRequirementSummary.enterprise_profile_requirement_count || 0 }} · 上传 {{ biddingMaterialRequirementSummary.manual_upload_count || 0 }}</small>
                        </div>
                        <div>
                          <span>待补齐</span>
                          <strong>{{ biddingMaterialRequirementSummary.open_count || 0 }}</strong>
                          <small>缺失 {{ biddingMaterialRequirementSummary.missing_count || 0 }} · 候选 {{ biddingMaterialRequirementSummary.candidate_found_count || 0 }}</small>
                        </div>
                        <div>
                          <span>已提交</span>
                          <strong>{{ biddingMaterialRequirementSummary.submitted_count || 0 }}</strong>
                          <small>待确认可用</small>
                        </div>
                        <div>
                          <span>可用率</span>
                          <strong>{{ Math.round((biddingMaterialRequirementSummary.completion_rate || 0) * 100) }}%</strong>
                          <small>已确认 {{ biddingMaterialRequirementSummary.resolved_count || 0 }}</small>
                        </div>
                      </div>

                      <el-table
                        v-loading="biddingMaterialRequirementsLoading || biddingMaterialRequirementsGenerating"
                        :data="biddingMaterialRequirementRows"
                        row-key="requirement_uuid"
                        class="users-table"
                        empty-text="暂无技术标资料需求，请先确认格式并生成技术标补齐清单"
                      >
                        <el-table-column label="资料需求" min-width="300" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.title }}</strong>
                              <small>{{ row.description || row.item_title }}</small>
                              <div class="bidding-response-chips">
                                <el-tag size="small" effect="plain">{{ biddingMaterialRequirementTypeLabel(row.requirement_type) }}</el-tag>
                                <el-tag v-if="row.profile_category" size="small" type="info" effect="plain">{{ biddingEnterpriseProfileCategoryLabel(row.profile_category) }}</el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="来源目录" min-width="220" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.item_title }}</span>
                              <small>{{ row.package_title || '技术标' }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="状态" width="110">
                          <template #default="{ row }">
                            <el-tag :type="biddingMaterialRequirementStatusTag(row.status)" effect="plain">
                              {{ biddingMaterialRequirementStatusLabel(row.status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="候选/提交" min-width="240" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ row.candidate_profile_item?.title || row.normalized?.candidate_profile_item?.title || row.submitted_value || '-' }}</span>
                              <small v-if="(row.submitted_profile_item_uuids?.length || 0) || (row.submitted_file_ids?.length || 0)">
                                已提交：企业资料 {{ row.submitted_profile_item_uuids?.length || 0 }} 份 / 补充文件 {{ row.submitted_file_ids?.length || 0 }} 份
                              </small>
                              <small v-else-if="row.submitted_profile_item_uuid">已绑定资料：{{ row.submitted_profile_item_uuid }}</small>
                              <small v-else-if="row.candidates?.length">候选 {{ row.candidates.length }} 条</small>
                              <small v-else>{{ row.notes || '暂无候选，需人工补充' }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="操作" width="250" fixed="right">
                          <template #default="{ row }">
                            <div class="row-actions">
                              <el-button
                                v-if="row.candidate_profile_item_uuid && !['approved', 'applied', 'not_applicable'].includes(row.status)"
                                size="small"
                                type="success"
                                plain
                                :loading="biddingMaterialRequirementUpdatingUuid === row.requirement_uuid"
                                @click="useBiddingMaterialCandidate(row)"
                              >
                                采用候选
                              </el-button>
                              <el-button size="small" plain @click="submitBiddingMaterialValue(row)">填写</el-button>
                              <el-button
                                v-if="['submitted', 'candidate_found'].includes(row.status) || row.submitted_value || row.submitted_profile_item_uuid || row.submitted_file_id || row.submitted_profile_item_uuids?.length || row.submitted_file_ids?.length"
                                size="small"
                                type="primary"
                                plain
                                @click="approveBiddingMaterialRequirement(row)"
                              >
                                确认可用
                              </el-button>
                              <el-button size="small" type="info" plain @click="markBiddingMaterialRequirementNotApplicable(row)">不适用</el-button>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>

                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Document /></el-icon>
                        <span>技术标目录骨架</span>
                        <small>仅生成技术标分册下的章节草稿，商务标报价、资格和表单不会进入这里</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button size="small" plain :loading="biddingDraftOutlineLoading" @click="loadBiddingDraftOutline()">刷新</el-button>
                          <el-button size="small" type="primary" plain :loading="biddingDraftOutlineGenerating" @click="generateBiddingDraftOutline">
                            生成技术标目录
                          </el-button>
                          <el-button size="small" type="success" plain :loading="biddingTechnicalDraftGenerating" @click="generateBiddingTechnicalDraftMvp">
                            一键生成技术标草案
                          </el-button>
                        </div>
                      </div>

                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>技术标章节</span>
                          <strong>{{ biddingDraftOutlineSummary.section_count || 0 }}</strong>
                          <small>任务 {{ biddingDraftOutlineSummary.task_section_count || 0 }}</small>
                        </div>
                        <div>
                          <span>可起草</span>
                          <strong>{{ biddingDraftOutlineSummary.placeholder_draft_count || biddingDraftOutlineSummary.can_generate_draft_count || 0 }}</strong>
                          <small>正式 {{ biddingDraftOutlineSummary.formal_draft_ready_count || 0 }}</small>
                        </div>
                        <div>
                          <span>阻断</span>
                          <strong>{{ biddingDraftOutlineSummary.blocked_section_count || 0 }}</strong>
                          <small>缺资料或风险项需先处理</small>
                        </div>
                        <div>
                          <span>来源</span>
                          <strong>{{ biddingDraftOutlineSourceText(biddingDraftOutlineSource) }}</strong>
                          <small>{{ biddingDraftOutlineSourceDetail(biddingDraftOutlineSource, biddingDraftOutlineSummary) }}</small>
                        </div>
                      </div>

                      <el-alert
                        v-if="biddingDraftOutlineWarnings.length"
                        class="dashboard-alert"
                        type="warning"
                        show-icon
                        :closable="false"
                        :title="biddingDraftOutlineWarnings[0].message || '技术标目录骨架仍有待处理事项'"
                      ></el-alert>

                      <el-table
                        v-loading="biddingDraftOutlineLoading || biddingDraftOutlineGenerating || biddingDraftSectionsLoading"
                        :data="biddingDraftOutlineSections"
                        row-key="section_key"
                        class="users-table"
                        empty-text="暂无技术标目录骨架，请先生成技术标目录"
                      >
                        <el-table-column label="章节" min-width="300" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client" :style="{ paddingLeft: `${Math.max(0, Number(row.level || 1) - 1) * 18}px` }">
                              <strong>{{ row.section_title }}</strong>
                              <small>{{ row.description || row.source_summary }}</small>
                              <div class="bidding-response-chips">
                                <el-tag size="small" :type="row.level === 1 ? 'primary' : 'info'" effect="plain">
                                  {{ row.level === 1 ? '文件包' : '章节' }}
                                </el-tag>
                                <el-tag size="small" effect="plain">{{ biddingDraftOutlineSectionTypeLabel(row.section_type) }}</el-tag>
                              </div>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="状态" width="120">
                          <template #default="{ row }">
                            <el-tag :type="biddingDraftOutlineStatusTag(row.draft_status)" effect="plain">
                              {{ biddingDraftOutlineStatusLabel(row.draft_status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="缺口/风险" min-width="260" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <span>{{ biddingDraftOutlineListText(row.missing_inputs, '暂无明显缺口') }}</span>
                              <small>{{ biddingDraftOutlineListText(row.risk_warnings, row.source_summary || '暂无高风险提示') }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="草稿" width="190" fixed="right">
                          <template #default="{ row }">
                            <div v-if="row.level === 2" class="row-actions">
                              <el-button
                                size="small"
                                type="primary"
                                plain
                                :loading="biddingDraftSectionGeneratingKey === row.section_key"
                                :disabled="Boolean(biddingDraftSectionGeneratingKey)"
                                @click="generateBiddingDraftSection(row)"
                              >
                                {{ biddingDraftOutlineActionButtonText(row) }}
                              </el-button>
                              <el-button v-if="biddingDraftForOutlineSection(row)" size="small" plain @click="openBiddingDraftPreview(row)">查看</el-button>
                            </div>
                            <el-tag v-else size="small" type="info" effect="plain">汇总章节</el-tag>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>

                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><DocumentChecked /></el-icon>
                        <span>已生成技术标草案</span>
                        <small>来自“投标文件组成识别”的一键生成结果，可直接查看、编辑和复核</small>
                        <div class="section-title-action bidding-outline-actions">
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :loading="biddingTechnicalDraftExporting"
                            :disabled="!biddingTechnicalCompositionDraftSections.length || biddingTechnicalFinalExporting"
                            @click="exportBiddingTechnicalDraftWord"
                          >
                            导出 Word 草稿
                          </el-button>
                          <el-button
                            size="small"
                            type="success"
                            plain
                            :loading="biddingTechnicalFinalExporting"
                            :disabled="!biddingTechnicalCompositionDraftSections.length || biddingTechnicalDraftExporting || biddingTechnicalFinalQualityLoading"
                            @click="exportBiddingTechnicalFinalWord"
                          >
                            导出正式 Word
                          </el-button>
                          <el-button
                            size="small"
                            type="warning"
                            plain
                            :icon="DataAnalysis"
                            :loading="biddingTechnicalFinalQualityLoading"
                            :disabled="!biddingTechnicalCompositionDraftSections.length || biddingTechnicalDraftExporting || biddingTechnicalFinalExporting"
                            @click="openBiddingTechnicalFinalQualityReport"
                          >
                            正式导出质检
                          </el-button>
                        </div>
                      </div>
                      <el-table
                        v-loading="biddingDraftSectionsLoading || biddingTechnicalDraftGenerating"
                        :data="biddingTechnicalCompositionDraftSections"
                        row-key="draft_uuid"
                        class="users-table"
                        empty-text="暂无技术标草案，请点击一键生成技术标草案"
                      >
                        <el-table-column label="章节" min-width="300" show-overflow-tooltip>
                          <template #default="{ row }">
                            <div class="operation-client">
                              <strong>{{ row.section_title }}</strong>
                              <small>{{ row.generation_decision?.classification_label || row.generation_decision?.label || '-' }}</small>
                            </div>
                          </template>
                        </el-table-column>
                        <el-table-column label="生成状态" width="130">
                          <template #default="{ row }">
                            <el-tag :type="biddingDraftOutlineStatusTag(row.draft_status)" effect="plain">
                              {{ biddingDraftOutlineStatusLabel(row.draft_status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="复核" width="110">
                          <template #default="{ row }">
                            <el-tag :type="biddingDraftSectionReviewTag(row.review_status)" effect="plain">
                              {{ biddingDraftSectionReviewLabel(row.review_status) }}
                            </el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="版本" width="90">
                          <template #default="{ row }">v{{ row.content_version || 1 }}</template>
                        </el-table-column>
                        <el-table-column label="操作" width="120" fixed="right">
                          <template #default="{ row }">
                            <el-button size="small" type="primary" plain @click="openBiddingDraftPreview(row)">查看</el-button>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>
                  </el-tab-pane>

                  <el-tab-pane label="招标要求" name="requirements">
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Tickets /></el-icon>
                        <span>招标要求清单</span>
                        <small>确认后可从这里生成响应矩阵</small>
                      </div>
                      <el-table
                        :data="biddingRequirements"
                        row-key="requirement_uuid"
                        class="users-table"
                        empty-text="暂无招标要求"
                      >
                        <el-table-column label="类型" width="130">
                          <template #default="{ row }">{{ biddingRequirementTypeLabel(row.requirement_type) }}</template>
                        </el-table-column>
                        <el-table-column label="来源" min-width="170" show-overflow-tooltip>
                          <template #default="{ row }">{{ row.source_file }} · {{ row.source_location }}</template>
                        </el-table-column>
                        <el-table-column prop="parsed_requirement" label="系统提炼" min-width="260" show-overflow-tooltip />
                        <el-table-column prop="original_text" label="原文" min-width="320" show-overflow-tooltip />
                        <el-table-column label="风险" width="90">
                          <template #default="{ row }">
                            <el-tag :type="biddingRiskLevelTag(row.risk_level)" effect="plain">{{ biddingRiskLevelLabel(row.risk_level) }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column prop="owner_role" label="负责人" width="100" />
                        <el-table-column prop="output_section" label="输出章节" min-width="140" show-overflow-tooltip />
                      </el-table>
                    </section>
                  </el-tab-pane>

                  <el-tab-pane label="合同/废标风险" name="risks">
                    <section class="dashboard-section">
                      <div class="section-title">
                        <el-icon><Warning /></el-icon>
                        <span>合同风险与废标风险</span>
                        <small>优先按风险卡片复核；下方保留原始明细供追溯</small>
                      </div>
                      <div class="bidding-risk-card-summary">
                        <div>
                          <span>风险卡片</span>
                          <strong>{{ biddingRiskCardsSummary.card_count || biddingRiskCards.length }}</strong>
                          <small>由 {{ biddingRiskCardsSummary.risk_count || biddingRisksTotal }} 条风险聚类生成</small>
                        </div>
                        <div>
                          <span>v2 重大/阻断</span>
                          <strong>{{ (biddingRiskCardsSummary.critical_card_count || 0) + (biddingRiskCardsSummary.blocking_v2_card_count || 0) }}</strong>
                          <small>阻断 {{ biddingRiskCardsSummary.blocking_v2_card_count || 0 }} · 高 {{ biddingRiskCardsSummary.high_v2_card_count || 0 }} · 待复核 {{ biddingRiskCardsSummary.pending_card_count || 0 }}</small>
                        </div>
                      </div>
                      <div v-if="biddingRiskCards.length" class="bidding-risk-card-grid">
                        <article
                          v-for="card in biddingRiskCards"
                          :key="card.card_id"
                          :class="['bidding-risk-card', `risk-${card.risk_level || 'medium'}`, `grade-${card.risk_grade_v2 || 'medium'}`]"
                        >
                          <div class="bidding-risk-card-head">
                            <div>
                              <el-tag size="small" :type="biddingRiskGradeV2Tag(card.risk_grade_v2)" effect="dark">
                                {{ biddingRiskGradeV2Label(card.risk_grade_v2) }} · {{ card.risk_score || 0 }}
                              </el-tag>
                              <el-tag size="small" :type="biddingRiskLevelTag(card.risk_level)" effect="plain">
                                {{ biddingRiskLevelLabel(card.risk_level) }}
                              </el-tag>
                              <el-tag v-if="card.is_blocking" size="small" type="danger" effect="plain">阻断</el-tag>
                              <el-tag size="small" :type="biddingRiskReviewTag(card.review_status)" effect="plain">
                                {{ biddingRiskReviewLabel(card.review_status) }}
                              </el-tag>
                            </div>
                            <strong>{{ card.title }}</strong>
                            <small>{{ biddingRiskTypeLabel(card.risk_type) }} · {{ card.risk_count }} 条明细 · {{ card.source_count }} 个来源</small>
                          </div>
                          <p>{{ card.risk_explanation || '-' }}</p>
                          <div class="bidding-risk-card-action">
                            <span>建议</span>
                            <small>{{ card.suggested_action || '-' }}</small>
                          </div>
                          <div class="bidding-risk-card-action">
                            <span>等级依据</span>
                            <small>{{ card.grade_reason || '-' }}</small>
                          </div>
                          <div v-if="card.drivers?.length" class="bidding-risk-source-line">
                            <span>驱动因素</span>
                            <small>{{ card.drivers.join('、') }}</small>
                          </div>
                          <div v-if="card.review_roles?.length" class="bidding-risk-source-line">
                            <span>建议复核</span>
                            <small>{{ card.review_roles.join('、') }} · 主动作 {{ biddingRiskActionLabel(card.primary_action) }}{{ card.secondary_action ? ` · 备选 ${biddingRiskActionLabel(card.secondary_action)}` : '' }}</small>
                          </div>
                          <div v-if="card.source_locations?.length" class="bidding-risk-source-line">
                            <span>来源</span>
                            <small>{{ card.source_locations.slice(0, 5).join('、') }}</small>
                          </div>
                          <div v-if="card.evidence?.length" class="bidding-risk-evidence-list">
                            <span>证据</span>
                            <small
                              v-for="evidence in card.evidence.slice(0, 3)"
                              :key="evidence.risk_uuid"
                            >
                              {{ evidence.source_location }}：{{ evidence.original_text }}
                            </small>
                          </div>
                          <div class="bidding-risk-card-actions">
                            <el-button size="small" type="success" plain @click="reviewBiddingRiskCard(card, 'confirmed')">确认</el-button>
                            <el-button size="small" type="warning" plain @click="reviewBiddingRiskCard(card, 'to_clarify')">转答疑</el-button>
                            <el-button size="small" plain @click="reviewBiddingRiskCard(card, 'to_quote_allowance')">报价预留</el-button>
                            <el-button size="small" type="info" plain @click="reviewBiddingRiskCard(card, 'ignored')">忽略</el-button>
                          </div>
                        </article>
                      </div>
                      <el-empty v-else description="暂无风险卡片" />
                      <div class="bidding-risk-detail-heading">
                        <strong>风险明细</strong>
                        <small>{{ biddingRisksTotal }} 条原始规则识别结果，用于证据追溯</small>
                      </div>
                      <el-table
                        :data="biddingRisks"
                        row-key="risk_uuid"
                        class="users-table"
                        empty-text="暂无风险"
                      >
                        <el-table-column label="级别" width="90">
                          <template #default="{ row }">
                            <el-tag :type="biddingRiskLevelTag(row.risk_level)" effect="plain">{{ biddingRiskLevelLabel(row.risk_level) }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="风险类型" width="150">
                          <template #default="{ row }">{{ biddingRiskTypeLabel(row.risk_type) }}</template>
                        </el-table-column>
                        <el-table-column label="来源" min-width="180" show-overflow-tooltip>
                          <template #default="{ row }">{{ row.source_file }} · {{ row.source_location }}</template>
                        </el-table-column>
                        <el-table-column prop="risk_explanation" label="风险解释" min-width="280" show-overflow-tooltip />
                        <el-table-column prop="suggested_action" label="建议动作" min-width="260" show-overflow-tooltip />
                        <el-table-column label="复核" width="110">
                          <template #default="{ row }">
                            <el-tag :type="biddingRiskReviewTag(row.review_status)" effect="plain">{{ biddingRiskReviewLabel(row.review_status) }}</el-tag>
                          </template>
                        </el-table-column>
                        <el-table-column label="操作" width="310" fixed="right">
                          <template #default="{ row }">
                            <div class="row-actions">
                              <el-button size="small" type="success" plain @click="reviewBiddingRisk(row, 'confirmed')">确认</el-button>
                              <el-button size="small" type="warning" plain @click="reviewBiddingRisk(row, 'to_clarify')">转答疑</el-button>
                              <el-button size="small" plain @click="reviewBiddingRisk(row, 'to_quote_allowance')">报价预留</el-button>
                              <el-button size="small" type="info" plain @click="reviewBiddingRisk(row, 'ignored')">忽略</el-button>
                            </div>
                          </template>
                        </el-table-column>
                      </el-table>
                    </section>
                  </el-tab-pane>
                </el-tabs>
              </div>
            </el-drawer>

            <el-drawer
              v-model="biddingDraftPreviewDrawer.visible"
              size="64%"
              :title="biddingDraftPreviewDrawer.draft?.section_title || '章节草稿预览'"
              destroy-on-close
            >
              <div v-if="biddingDraftPreviewDrawer.draft" class="bidding-draft-preview">
                <div class="bidding-draft-preview-head">
                  <div>
                    <el-tag effect="plain">{{ biddingDraftOutlineSectionTypeLabel(biddingDraftPreviewDrawer.draft.section_type) }}</el-tag>
                    <el-tag :type="biddingDraftOutlineStatusTag(biddingDraftPreviewDrawer.draft.draft_status)" effect="plain">
                      {{ biddingDraftOutlineStatusLabel(biddingDraftPreviewDrawer.draft.draft_status) }}
                    </el-tag>
                    <el-tag :type="biddingDraftSectionReviewTag(biddingDraftPreviewDrawer.draft.review_status)" effect="plain">
                      {{ biddingDraftSectionReviewLabel(biddingDraftPreviewDrawer.draft.review_status) }}
                    </el-tag>
                    <el-tag v-if="biddingDraftPreviewDrawer.draft.generation_decision?.label" effect="plain">
                      {{ biddingDraftPreviewDrawer.draft.generation_decision.label }}
                    </el-tag>
                    <el-tag
                      v-if="biddingDraftPreviewDrawer.draft.quality_result?.status_label"
                      :type="biddingDraftQualityResultTag(biddingDraftPreviewDrawer.draft.quality_result.status)"
                      effect="plain"
                    >
                      质检：{{ biddingDraftPreviewDrawer.draft.quality_result.status_label }}
                    </el-tag>
                    <el-tag
                      v-if="biddingDraftPreviewDrawer.draft.llm_entry?.status_label"
                      :type="biddingDraftLlmEntryTag(biddingDraftPreviewDrawer.draft.llm_entry)"
                      effect="plain"
                    >
                      {{ biddingDraftPreviewDrawer.draft.llm_entry.status_label }}
                    </el-tag>
                  </div>
                  <small>
                    主责 {{ biddingDraftPreviewDrawer.draft.owner_role || '-' }} ·
                    内容版本 第 {{ biddingDraftPreviewDrawer.draft.content_version || 1 }} 版
                  </small>
                </div>

                <section v-if="!biddingDraftPreviewDrawer.draft.quality_profile?.quality_status" class="bidding-draft-panel bidding-draft-upgrade-panel">
                  <strong>章节质量画像尚未生成</strong>
                  <small>当前预览的是旧草稿或旧接口返回结果，请在目录行点击“重新生成/升级草稿”，或刷新前端后重新打开章节。</small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.quality_profile?.quality_status" class="bidding-draft-panel bidding-draft-quality-panel">
                  <strong>章节质量画像</strong>
                  <small>
                    状态：{{ biddingDraftPreviewDrawer.draft.quality_profile.quality_status_label || '-' }} ·
                    素材：响应 {{ biddingDraftPreviewDrawer.draft.quality_profile.response_item_count || 0 }} 项 /
                    要求 {{ biddingDraftPreviewDrawer.draft.quality_profile.requirement_count || 0 }} 条 /
                    风险 {{ biddingDraftPreviewDrawer.draft.quality_profile.risk_count || 0 }} 条 /
                    证据 {{ biddingDraftPreviewDrawer.draft.quality_profile.evidence_count || 0 }} 条
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.quality_profile.mapping_status">
                    映射：{{ biddingDraftPreviewDrawer.draft.quality_profile.mapping_status }} /
                    {{ biddingDraftPreviewDrawer.draft.quality_profile.mapping_confidence || '-' }}
                  </small>
                  <small
                    v-for="item in biddingDraftPreviewDrawer.draft.quality_profile.blockers || []"
                    :key="`blocker-${item}`"
                  >
                    阻断：{{ item }}
                  </small>
                  <small
                    v-for="item in biddingDraftPreviewDrawer.draft.quality_profile.material_gaps || []"
                    :key="`gap-${item}`"
                  >
                    缺口：{{ item }}
                  </small>
                  <small
                    v-for="item in biddingDraftPreviewDrawer.draft.quality_profile.warnings || []"
                    :key="`quality-warning-${item}`"
                  >
                    提醒：{{ item }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.writing_plan?.target_output" class="bidding-draft-panel bidding-draft-quality-panel">
                  <strong>写作计划</strong>
                  <small>目标产出：{{ biddingDraftPreviewDrawer.draft.writing_plan.target_output_label || '-' }}</small>
                  <small v-if="biddingDraftPreviewDrawer.draft.writing_plan.suggested_headings?.length">
                    建议小标题：{{ biddingDraftPreviewDrawer.draft.writing_plan.suggested_headings.slice(0, 8).join('、') }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.writing_plan.must_cover_requirements?.length">
                    必覆盖：{{ biddingDraftPlanListText(biddingDraftPreviewDrawer.draft.writing_plan.must_cover_requirements) }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.writing_plan.response_tasks?.length">
                    响应任务：{{ biddingDraftPlanListText(biddingDraftPreviewDrawer.draft.writing_plan.response_tasks) }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.writing_plan.review_focus?.length">
                    复核重点：{{ biddingDraftPreviewDrawer.draft.writing_plan.review_focus.slice(0, 4).join('；') }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.quality_result?.status" class="bidding-draft-panel bidding-draft-quality-panel">
                  <strong>质检结果</strong>
                  <small>
                    {{ biddingDraftPreviewDrawer.draft.quality_result.status_label || '-' }}：
                    {{ biddingDraftPreviewDrawer.draft.quality_result.summary || '-' }}
                  </small>
                  <small
                    v-for="check in biddingDraftPreviewDrawer.draft.quality_result.checks || []"
                    :key="check.code"
                    class="bidding-draft-quality-check"
                  >
                    <el-tag size="small" :type="biddingDraftQualityCheckTag(check.status)" effect="plain">
                      {{ check.label || check.code }}
                    </el-tag>
                    {{ check.message || '-' }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.llm_entry" class="bidding-draft-panel bidding-draft-llm-panel">
                  <strong>智能润色</strong>
                  <small>
                    {{ biddingDraftPreviewDrawer.draft.llm_entry.status_label || '-' }} ·
                    {{ biddingDraftPreviewDrawer.draft.llm_entry.action_label || '智能润色正文' }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.llm_entry.quality_gate_status_label">
                    质检门槛：{{ biddingDraftPreviewDrawer.draft.llm_entry.quality_gate_status_label }}
                  </small>
                  <small>{{ biddingDraftPreviewDrawer.draft.llm_entry.note || '智能润色仅用于优化正文，不补充事实、不替代人工复核。' }}</small>
                  <small
                    v-for="reason in biddingDraftPreviewDrawer.draft.llm_entry.blocked_reasons || []"
                    :key="`llm-blocked-${reason}`"
                  >
                    不可用：{{ reason }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.diff_summary?.base_change_type" class="bidding-draft-panel bidding-draft-quality-panel">
                  <strong>初稿与润色稿差异</strong>
                  <small>
                    基准版本：v{{ biddingDraftPreviewDrawer.draft.diff_summary.base_version_no || '-' }} ·
                    新增 {{ biddingDraftPreviewDrawer.draft.diff_summary.added_line_count || 0 }} 行 ·
                    删除 {{ biddingDraftPreviewDrawer.draft.diff_summary.removed_line_count || 0 }} 行 ·
                    保留标题 {{ biddingDraftPreviewDrawer.draft.diff_summary.preserved_heading_count || 0 }} 个
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.diff_summary.added_headings?.length">
                    新增标题：{{ biddingDraftSummaryListText(biddingDraftPreviewDrawer.draft.diff_summary.added_headings) }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.diff_summary.removed_headings?.length">
                    删除标题：{{ biddingDraftSummaryListText(biddingDraftPreviewDrawer.draft.diff_summary.removed_headings) }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.diff_summary.risk_removed">
                    风险提示：润色稿疑似删除了 {{ biddingDraftSummaryListText(biddingDraftPreviewDrawer.draft.diff_summary.removed_risk_markers) }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.semantic_quality?.status" class="bidding-draft-panel bidding-draft-quality-panel">
                  <strong>证据对齐质检</strong>
                  <small>
                    {{ biddingDraftPreviewDrawer.draft.semantic_quality.status_label || '-' }}：
                    {{ biddingDraftPreviewDrawer.draft.semantic_quality.summary || '-' }}
                  </small>
                  <small
                    v-for="item in biddingDraftPreviewDrawer.draft.semantic_quality.unsupported_claims || []"
                    :key="`unsupported-${item}`"
                  >
                    疑似无证据表达：{{ item }}
                  </small>
                  <small
                    v-for="item in (biddingDraftPreviewDrawer.draft.semantic_quality.missing_coverages || []).slice(0, 6)"
                    :key="`missing-coverage-${item}`"
                  >
                    疑似覆盖不足：{{ item }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.content_evidence?.status" class="bidding-draft-panel bidding-draft-evidence-panel">
                  <strong>段落证据追溯</strong>
                  <small>
                    {{ biddingDraftPreviewDrawer.draft.content_evidence.status_label || '-' }}：
                    {{ biddingDraftPreviewDrawer.draft.content_evidence.summary || '-' }}
                  </small>
                  <small v-if="biddingDraftPreviewDrawer.draft.content_evidence.coverage_summary">
                    覆盖：{{ biddingDraftPreviewDrawer.draft.content_evidence.coverage_summary.covered_count || 0 }} /
                    {{ biddingDraftPreviewDrawer.draft.content_evidence.coverage_summary.required_count || 0 }} ·
                    缺失 {{ biddingDraftPreviewDrawer.draft.content_evidence.coverage_summary.missing_count || 0 }}
                  </small>
                  <div
                    v-for="block in (biddingDraftPreviewDrawer.draft.content_evidence.blocks || []).slice(0, 8)"
                    :key="`content-evidence-${block.block_index}`"
                    class="bidding-draft-evidence-block"
                  >
                    <div>
                      <el-tag size="small" :type="biddingDraftEvidenceStatusTag(block.evidence_status)" effect="plain">
                        {{ block.evidence_status_label || block.evidence_status || '-' }}
                      </el-tag>
                      <span>第 {{ block.block_index }} 段</span>
                    </div>
                    <small>{{ block.block_text || '-' }}</small>
                    <small v-if="block.supporting_evidence">
                      来源：{{ block.supporting_evidence.source_type_label || '-' }} ·
                      {{ block.supporting_evidence.title || '-' }}
                      <template v-if="block.supporting_evidence.source_location">
                        · {{ block.supporting_evidence.source_location }}
                      </template>
                    </small>
                    <small
                      v-for="warning in block.warnings || []"
                      :key="`content-evidence-warning-${block.block_index}-${warning}`"
                    >
                      提醒：{{ warning }}
                    </small>
                  </div>
                  <small
                    v-for="item in (biddingDraftPreviewDrawer.draft.content_evidence.missing_coverages || []).slice(0, 6)"
                    :key="`content-evidence-missing-${item}`"
                  >
                    未覆盖：{{ item }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.acceptance_check?.status" class="bidding-draft-panel bidding-draft-acceptance-panel">
                  <strong>接受前检查</strong>
                  <small>
                    {{ biddingDraftPreviewDrawer.draft.acceptance_check.status_label || '-' }}：
                    {{ biddingDraftPreviewDrawer.draft.acceptance_check.summary || '-' }}
                  </small>
                  <small
                    v-for="item in biddingDraftPreviewDrawer.draft.acceptance_check.blockers || []"
                    :key="`accept-blocker-${item}`"
                  >
                    阻断：{{ item }}
                  </small>
                  <small
                    v-for="item in biddingDraftPreviewDrawer.draft.acceptance_check.warnings || []"
                    :key="`accept-warning-${item}`"
                  >
                    提醒：{{ item }}
                  </small>
                </section>

                <el-input
                  v-if="biddingDraftPreviewDrawer.editing"
                  v-model="biddingDraftPreviewDrawer.editContent"
                  type="textarea"
                  :autosize="{ minRows: 16, maxRows: 28 }"
                  class="bidding-draft-editor"
                  placeholder="编辑章节正文 Markdown"
                />
                <pre v-else class="bidding-draft-markdown">{{ biddingDraftMarkdownPreview(biddingDraftPreviewDrawer.draft.content_markdown) }}</pre>

                <section
                  v-if="biddingDraftPreviewDrawer.draft.upgrade_hint?.needs_upgrade"
                  class="bidding-draft-panel bidding-draft-upgrade-panel"
                >
                  <strong>旧草稿升级提示</strong>
                  <small>{{ biddingDraftPreviewDrawer.draft.upgrade_hint.message || '该章节建议重新生成后再复核。' }}</small>
                  <small
                    v-for="reason in biddingDraftPreviewDrawer.draft.upgrade_hint.reasons || []"
                    :key="reason.code"
                  >
                    {{ reason.message }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.generation_decision?.reason" class="bidding-draft-panel">
                  <strong>生成判定</strong>
                  <small>{{ biddingDraftPreviewDrawer.draft.generation_decision.reason }}</small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.placeholders?.length" class="bidding-draft-panel">
                  <strong>占位符</strong>
                  <small
                    v-for="placeholder in biddingDraftPreviewDrawer.draft.placeholders"
                    :key="placeholder.placeholder_key"
                  >
                    {{ placeholder.text }} · {{ placeholder.owner_role || '-' }}
                  </small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.warnings?.length" class="bidding-draft-panel">
                  <strong>风险/阻断提示</strong>
                  <small
                    v-for="warning in biddingDraftPreviewDrawer.draft.warnings"
                    :key="warning.message"
                  >
                    {{ warning.message }}
                  </small>
                </section>

                <section class="bidding-draft-panel">
                  <strong>来源证据</strong>
                  <small
                    v-for="evidence in (biddingDraftPreviewDrawer.draft.evidence || []).slice(0, 10)"
                    :key="`${evidence.source_file || '-'}-${evidence.source_location || '-'}-${evidence.original_text || '-'}`"
                  >
                    {{ evidence.source_file || '-' }} · {{ evidence.source_location || '-' }}：{{ evidence.original_text || '-' }}
                  </small>
                  <small v-if="!biddingDraftPreviewDrawer.draft.evidence?.length">暂无来源证据</small>
                </section>

                <section v-if="biddingDraftPreviewDrawer.draft.versions?.length" class="bidding-draft-panel">
                  <strong>版本记录</strong>
                  <small
                    v-for="version in biddingDraftPreviewDrawer.draft.versions"
                    :key="version.version_uuid"
                  >
                    v{{ version.version_no }} · {{ biddingDraftVersionTypeLabel(version.change_type) }} · {{ version.created_at || '-' }}
                    <template v-if="version.editor_note"> · {{ version.editor_note }}</template>
                  </small>
                </section>

                <div class="row-actions">
                  <el-button
                    v-if="!biddingDraftPreviewDrawer.editing"
                    plain
                    @click="startEditingBiddingDraftSection"
                  >
                    编辑正文
                  </el-button>
                  <el-button
                    v-if="biddingDraftPreviewDrawer.editing"
                    type="primary"
                    plain
                    :loading="biddingDraftPreviewDrawer.saving"
                    @click="saveBiddingDraftSectionContent"
                  >
                    保存版本
                  </el-button>
                  <el-button
                    v-if="biddingDraftPreviewDrawer.editing"
                    plain
                    @click="cancelEditingBiddingDraftSection"
                  >
                    取消编辑
                  </el-button>
                  <el-button
                    v-if="!biddingDraftPreviewDrawer.editing && biddingDraftCanLlmEnhance(biddingDraftPreviewDrawer.draft)"
                    type="primary"
                    plain
                    :loading="biddingDraftPreviewDrawer.llmGenerating"
                    @click="generateBiddingDraftSectionWithLlm"
                  >
                    智能润色正文
                  </el-button>
                  <el-button
                    type="success"
                    plain
                    :loading="biddingDraftSectionReviewing"
                    @click="reviewBiddingDraftSection('accepted')"
                  >
                    接受
                  </el-button>
                  <el-button
                    type="primary"
                    plain
                    :loading="biddingDraftSectionReviewing"
                    @click="reviewBiddingDraftSection('reviewed')"
                  >
                    已复核
                  </el-button>
                  <el-button
                    type="warning"
                    plain
                    :loading="biddingDraftSectionReviewing"
                    @click="reviewBiddingDraftSection('needs_revision')"
                  >
                    需修改
                  </el-button>
                </div>
              </div>
            </el-drawer>

            <el-drawer
              v-model="biddingTechnicalFinalQualityDrawer.visible"
              size="min(1120px, 96vw)"
              title="正式技术标导出质检"
              destroy-on-close
            >
              <div v-if="biddingTechnicalFinalQualityDrawer.report" class="drawer-body bidding-final-quality">
                <section class="drawer-section">
                  <div class="section-title compact">
                    <el-icon><DataAnalysis /></el-icon>
                    <span>导出质量概览</span>
                    <el-tag :type="biddingFinalQualityStatusTag(biddingTechnicalFinalQualityDrawer.report.status)" effect="plain">
                      {{ biddingFinalQualityStatusLabel(biddingTechnicalFinalQualityDrawer.report.status) }}
                    </el-tag>
                  </div>
                  <div class="bidding-structure-summary">
                    <span>
                      <small>阻断/问题</small>
                      <strong>{{ biddingTechnicalFinalQualityDrawer.report.issue_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>技术章节</small>
                      <strong>{{ biddingTechnicalFinalQualityDrawer.report.draft_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>组成项</small>
                      <strong>{{ biddingTechnicalFinalQualityDrawer.report.component_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>缺章节</small>
                      <strong>{{ biddingTechnicalFinalQualityDrawer.report.missing_draft_section_count || 0 }}</strong>
                    </span>
                  </div>
                </section>

                <section class="drawer-section" v-if="biddingTechnicalFinalQualityDrawer.report.issues?.length">
                  <div class="section-title compact">
                    <el-icon><Warning /></el-icon>
                    <span>解除阻断所需信息</span>
                    <small>逐项填写并确认可用；涉及章节资料的，确认后重新生成对应章节</small>
                  </div>
                  <el-table :data="biddingTechnicalFinalQualityDrawer.report.issues" class="users-table">
                    <el-table-column type="index" label="#" width="56" />
                    <el-table-column label="章节" min-width="190" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.section || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="具体需要补充" min-width="380">
                      <template #default="{ row }">
                        <div class="operation-client">
                          <strong>{{ row.required_information || row.issue || '-' }}</strong>
                          <small v-if="row.requirement_status">当前状态：{{ biddingMaterialRequirementStatusLabel(row.requirement_status) }}</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="处理方法" min-width="420">
                      <template #default="{ row }">{{ row.suggestion || '-' }}</template>
                    </el-table-column>
                  </el-table>
                </section>

                <section class="drawer-section" v-if="biddingFinalQualityTemplateReinforcement.version">
                  <div class="section-title compact">
                    <el-icon><DocumentChecked /></el-icon>
                    <span>章节模板深化</span>
                    <el-tag :type="biddingReinforcementStatusTag(biddingFinalQualityTemplateReinforcement.status)" effect="plain">
                      {{ biddingReinforcementStatusLabel(biddingFinalQualityTemplateReinforcement.status) }}
                    </el-tag>
                  </div>
                  <div class="bidding-structure-summary">
                    <span>
                      <small>深化章节</small>
                      <strong>{{ biddingFinalQualityTemplateReinforcement.reinforced_section_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>补充主题</small>
                      <strong>{{ biddingFinalQualityTemplateReinforcement.added_topic_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>扫描章节</small>
                      <strong>{{ biddingFinalQualityTemplateReinforcement.section_count || 0 }}</strong>
                    </span>
                  </div>
                  <el-table
                    v-if="biddingFinalQualityTemplateReinforcementSections.length"
                    :data="biddingFinalQualityTemplateReinforcementSections"
                    class="users-table"
                    row-key="section_key"
                  >
                    <el-table-column label="章节" width="130">
                      <template #default="{ row }">{{ row.section_no || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="深化类型" min-width="180" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.intent || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="段落变化" width="150">
                      <template #default="{ row }">{{ row.paragraph_count_before || 0 }} → {{ row.paragraph_count_after || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="字数变化" width="160">
                      <template #default="{ row }">{{ row.visible_length_before || 0 }} → {{ row.visible_length_after || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="补充主题" min-width="260" show-overflow-tooltip>
                      <template #default="{ row }">{{ (row.added_topics || []).slice(0, 6).join('、') || '-' }}</template>
                    </el-table-column>
                  </el-table>
                  <el-empty v-else description="暂无章节模板深化记录" />
                </section>

                <section class="drawer-section" v-if="biddingFinalQualityPlaybookReinforcement.version">
                  <div class="section-title compact">
                    <el-icon><DocumentChecked /></el-icon>
                    <span>专业工法清单</span>
                    <el-tag :type="biddingReinforcementStatusTag(biddingFinalQualityPlaybookReinforcement.status)" effect="plain">
                      {{ biddingReinforcementStatusLabel(biddingFinalQualityPlaybookReinforcement.status) }}
                    </el-tag>
                  </div>
                  <div class="bidding-structure-summary">
                    <span>
                      <small>深化章节</small>
                      <strong>{{ biddingFinalQualityPlaybookReinforcement.reinforced_section_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>新增表格</small>
                      <strong>{{ biddingFinalQualityPlaybookReinforcement.added_table_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>控制项</small>
                      <strong>{{ biddingFinalQualityPlaybookReinforcement.control_item_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>流程节点</small>
                      <strong>{{ biddingFinalQualityPlaybookReinforcement.process_node_count || 0 }}</strong>
                    </span>
                  </div>
                  <el-table
                    v-if="biddingFinalQualityPlaybookReinforcementSections.length"
                    :data="biddingFinalQualityPlaybookReinforcementSections"
                    class="users-table"
                    row-key="section_key"
                  >
                    <el-table-column label="章节" width="130">
                      <template #default="{ row }">{{ row.section_no || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="类型" min-width="180" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.intent || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="表格" width="90">
                      <template #default="{ row }">{{ row.added_table_count || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="控制项" width="90">
                      <template #default="{ row }">{{ row.control_item_count || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="流程节点" width="100">
                      <template #default="{ row }">{{ row.process_node_count || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="段落变化" width="150">
                      <template #default="{ row }">{{ row.paragraph_count_before || 0 }} → {{ row.paragraph_count_after || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="字数变化" width="160">
                      <template #default="{ row }">{{ row.visible_length_before || 0 }} → {{ row.visible_length_after || 0 }}</template>
                    </el-table-column>
                  </el-table>
                  <el-empty v-else description="暂无专业工法清单记录" />
                </section>

                <section class="drawer-section" v-if="biddingFinalQualityReviewFocusReinforcement.version">
                  <div class="section-title compact">
                    <el-icon><DocumentChecked /></el-icon>
                    <span>评审关注点响应</span>
                    <el-tag :type="biddingReinforcementStatusTag(biddingFinalQualityReviewFocusReinforcement.status)" effect="plain">
                      {{ biddingReinforcementStatusLabel(biddingFinalQualityReviewFocusReinforcement.status) }}
                    </el-tag>
                  </div>
                  <div class="bidding-structure-summary">
                    <span>
                      <small>深化章节</small>
                      <strong>{{ biddingFinalQualityReviewFocusReinforcement.reinforced_section_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>关注点</small>
                      <strong>{{ biddingFinalQualityReviewFocusReinforcement.added_focus_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>新增关键词</small>
                      <strong>{{ biddingFinalQualityReviewFocusReinforcement.added_keyword_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>响应表</small>
                      <strong>{{ biddingFinalQualityReviewFocusReinforcement.added_table_count || 0 }}</strong>
                    </span>
                  </div>
                  <el-table
                    v-if="biddingFinalQualityReviewFocusReinforcementSections.length"
                    :data="biddingFinalQualityReviewFocusReinforcementSections"
                    class="users-table"
                    row-key="section_key"
                  >
                    <el-table-column label="章节" width="130">
                      <template #default="{ row }">{{ row.section_no || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="类型" min-width="170" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.intent || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="关键词" width="120">
                      <template #default="{ row }">{{ row.matched_keyword_count_before || 0 }} → {{ row.matched_keyword_count_after || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="新增" width="80">
                      <template #default="{ row }">{{ row.added_keyword_count || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="关注点" width="90">
                      <template #default="{ row }">{{ row.added_focus_count || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="段落变化" width="150">
                      <template #default="{ row }">{{ row.paragraph_count_before || 0 }} → {{ row.paragraph_count_after || 0 }}</template>
                    </el-table-column>
                    <el-table-column label="仍缺关键词" min-width="260" show-overflow-tooltip>
                      <template #default="{ row }">{{ (row.missing_keywords_after || []).slice(0, 8).join('、') || '-' }}</template>
                    </el-table-column>
                  </el-table>
                  <el-empty v-else description="暂无评审关注点响应记录" />
                </section>

                <section class="drawer-section" v-if="biddingFinalQualityReinforcement.version">
                  <div class="section-title compact">
                    <el-icon><DocumentChecked /></el-icon>
                    <span>自动补强审计</span>
                    <el-tag :type="biddingReinforcementStatusTag(biddingFinalQualityReinforcement.status)" effect="plain">
                      {{ biddingReinforcementStatusLabel(biddingFinalQualityReinforcement.status) }}
                    </el-tag>
                  </div>
                  <div class="bidding-structure-summary">
                    <span>
                      <small>自动补强要求</small>
                      <strong>{{ biddingFinalQualityReinforcement.auto_reinforced_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>补强章节</small>
                      <strong>{{ biddingFinalQualityReinforcement.reinforced_section_count || 0 }}</strong>
                    </span>
                    <span>
                      <small>人工复核项</small>
                      <strong>{{ biddingFinalQualityReinforcement.manual_review_count || 0 }}</strong>
                    </span>
                  </div>
                  <el-table
                    v-if="biddingFinalQualityReinforcementTransitions.length"
                    :data="biddingFinalQualityReinforcementTransitions"
                    class="users-table"
                    row-key="requirement_key"
                  >
                    <el-table-column label="章节" width="130">
                      <template #default="{ row }">{{ row.section_no || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="补强要求" min-width="220" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.requirement_title || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="补强前" width="110">
                      <template #default="{ row }">
                        <el-tag :type="biddingCoverageStatusTag(row.before_status)" effect="plain">
                          {{ biddingCoverageStatusLabel(row.before_status) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="补强后" width="110">
                      <template #default="{ row }">
                        <el-tag :type="biddingCoverageStatusTag(row.after_status)" effect="plain">
                          {{ biddingCoverageStatusLabel(row.after_status) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="关键词" min-width="220" show-overflow-tooltip>
                      <template #default="{ row }">{{ (row.terms || row.matched_terms_after || []).slice(0, 6).join('、') || '-' }}</template>
                    </el-table-column>
                  </el-table>
                  <el-empty v-else description="暂无自动补强记录" />
                </section>

                <section class="drawer-section" v-if="biddingFinalQualityManualItems.length">
                  <div class="section-title compact">
                    <el-icon><Warning /></el-icon>
                    <span>仍需人工复核</span>
                    <small>证照、人员、业绩、奖项等硬事实不会自动编造</small>
                  </div>
                  <el-table :data="biddingFinalQualityManualItems" class="users-table" row-key="requirement_key">
                    <el-table-column label="章节" width="130">
                      <template #default="{ row }">{{ row.section_no || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="要求" min-width="240" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.requirement_title || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="状态" width="130">
                      <template #default="{ row }">
                        <el-tag :type="biddingCoverageStatusTag(row.coverage_status)" effect="plain">
                          {{ biddingCoverageStatusLabel(row.coverage_status) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="原因" width="180">
                      <template #default="{ row }">{{ biddingReinforcementSkipReasonLabel(row.reason) }}</template>
                    </el-table-column>
                    <el-table-column label="缺失关键词" min-width="220" show-overflow-tooltip>
                      <template #default="{ row }">{{ (row.missing_terms || []).slice(0, 6).join('、') || '-' }}</template>
                    </el-table-column>
                  </el-table>
                </section>

                <section class="drawer-section" v-if="biddingFinalQualityCoverageProblemItems.length">
                  <div class="section-title compact">
                    <el-icon><Tickets /></el-icon>
                    <span>逐条覆盖问题</span>
                    <small>来自正式导出质量报告</small>
                  </div>
                  <el-table :data="biddingFinalQualityCoverageProblemItems" class="users-table" row-key="requirement_key">
                    <el-table-column label="章节" width="130">
                      <template #default="{ row }">{{ row.section_no || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="要求" min-width="240" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.requirement_title || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="覆盖状态" width="130">
                      <template #default="{ row }">
                        <el-tag :type="biddingCoverageStatusTag(row.coverage_status)" effect="plain">
                          {{ biddingCoverageStatusLabel(row.coverage_status) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="缺失关键词" min-width="240" show-overflow-tooltip>
                      <template #default="{ row }">{{ (row.missing_terms || []).slice(0, 8).join('、') || '-' }}</template>
                    </el-table-column>
                  </el-table>
                </section>
              </div>
              <el-empty v-else description="暂无正式导出质检报告" />
            </el-drawer>

            <el-dialog
              v-model="biddingMaterialProfileDialog.visible"
              title="从企业资料库填写"
              width="860px"
              destroy-on-close
            >
              <el-alert
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="选择后会把该企业资料绑定到当前技术标资料需求，后续生成技术标章节草稿时会读取这条资料。"
              />
              <el-form class="filter-bar" :model="biddingMaterialProfileDialog.form" @submit.prevent>
                <el-form-item label="资料分类">
                  <el-select v-model="biddingMaterialProfileDialog.form.category" clearable placeholder="全部分类">
                    <el-option
                      v-for="option in enterpriseProfileCategoryOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="关键词">
                  <el-input
                    v-model="biddingMaterialProfileDialog.form.keyword"
                    placeholder="按资料名称、摘要、正文搜索"
                    clearable
                    @keyup.enter="loadBiddingMaterialProfileCandidates"
                  />
                </el-form-item>
                <el-button :icon="Search" plain :loading="biddingMaterialProfileDialog.loading" @click="loadBiddingMaterialProfileCandidates">
                  搜索
                </el-button>
              </el-form>
              <el-table
                v-loading="biddingMaterialProfileDialog.loading"
                :data="biddingMaterialProfileDialog.candidates"
                class="users-table"
                empty-text="暂无可用企业资料，请先在企业资料库新增并启用资料"
                @selection-change="handleBiddingMaterialProfileSelectionChange"
              >
                <el-table-column type="selection" width="48" />
                <el-table-column label="资料名称" min-width="240" show-overflow-tooltip>
                  <template #default="{ row }">
                    <div class="operation-client">
                      <strong>{{ row.title }}</strong>
                      <small>{{ row.summary || row.applicable_scope || '-' }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="分类" width="140">
                  <template #default="{ row }">{{ biddingEnterpriseProfileCategoryLabel(row.category) }}</template>
                </el-table-column>
                <el-table-column label="形式/附件" width="130">
                  <template #default="{ row }">
                    <div class="operation-client">
                      <span>{{ row.structured?.material_form === 'attachment' ? '附件形式' : '文本形式' }}</span>
                      <small>附件 {{ row.attachment_count || 0 }} 个</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="有效期" width="130">
                  <template #default="{ row }">{{ row.valid_until || '长期' }}</template>
                </el-table-column>
                <el-table-column label="操作" width="120" fixed="right">
                  <template #default="{ row }">
                    <el-button
                      size="small"
                      type="primary"
                      plain
                      :loading="biddingMaterialRequirementUpdatingUuid === biddingMaterialProfileDialog.row?.requirement_uuid"
                      @click="submitBiddingMaterialProfileCandidates([row])"
                    >
                      填入
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <section class="bidding-draft-panel">
                <div class="section-title">
                  <el-icon><Upload /></el-icon>
                  <span>补充上传文件</span>
                  <small>同一个技术标资料项可提交多份附件</small>
                </div>
                <el-upload
                  action="#"
                  :auto-upload="false"
                  :show-file-list="false"
                  multiple
                  :on-change="uploadBiddingMaterialRequirementFile"
                >
                  <el-button :icon="Upload" plain :loading="biddingMaterialProfileDialog.uploading">
                    上传补充文件
                  </el-button>
                </el-upload>
                <div class="bidding-response-chips">
                  <el-tag
                    v-for="file in biddingMaterialProfileDialog.uploadedFiles"
                    :key="file.file_id"
                    closable
                    effect="plain"
                    @close="removeBiddingMaterialRequirementUploadedFile(file.file_id)"
                  >
                    {{ file.original_filename || file.file_id }}
                  </el-tag>
                  <small v-if="!biddingMaterialProfileDialog.uploadedFiles.length">暂无已上传文件</small>
                </div>
              </section>
              <template #footer>
                <el-button @click="biddingMaterialProfileDialog.visible = false">关闭</el-button>
                <el-button plain @click="submitBiddingMaterialManualValue(biddingMaterialProfileDialog.row)">
                  手动填写
                </el-button>
                <el-button
                  type="primary"
                  :loading="biddingMaterialRequirementUpdatingUuid === biddingMaterialProfileDialog.row?.requirement_uuid"
                  @click="submitBiddingMaterialProfileCandidates(biddingMaterialProfileDialog.selectedProfiles)"
                >
                  提交已选/已上传
                </el-button>
              </template>
            </el-dialog>

            <el-dialog
              v-model="biddingDialog.visible"
              title="上传甲方招标文件"
              width="620px"
              destroy-on-close
            >
              <el-form label-position="top" :model="biddingDialog.form">
                <el-form-item label="招标文件">
                  <el-upload
                    ref="biddingProjectUploadRef"
                    :auto-upload="false"
                    :show-file-list="true"
                    :limit="1"
                    accept=".pdf,.docx"
                    :on-change="handleBiddingProjectFileChange"
                    :on-remove="clearBiddingProjectFile"
                  >
                    <el-button :icon="Upload" plain>选择 Word/PDF</el-button>
                    <template #tip>
                      <div class="upload-tip">支持甲方招标文件 PDF、Word(.docx)，上传后自动创建投标项目。</div>
                    </template>
                  </el-upload>
                </el-form-item>
                <el-form-item label="项目名称（可选）">
                  <el-input v-model="biddingDialog.form.project_name" placeholder="不填则使用招标文件名"></el-input>
                </el-form-item>
                <el-form-item label="招标单位（可选）">
                  <el-input v-model="biddingDialog.form.tenderer_name" placeholder="甲方/建设单位"></el-input>
                </el-form-item>
                <el-form-item label="招标代理（可选）">
                  <el-input v-model="biddingDialog.form.tender_agency" placeholder="可选"></el-input>
                </el-form-item>
                <div class="form-grid-2">
                  <el-form-item label="工程地点（可选）">
                    <el-input v-model="biddingDialog.form.project_location" placeholder="可选"></el-input>
                  </el-form-item>
                  <el-form-item label="工程类型（可选）">
                    <el-input v-model="biddingDialog.form.project_type" placeholder="办公楼装修/餐饮装修等"></el-input>
                  </el-form-item>
                </div>
                <el-form-item label="投标截止时间（可选）">
                  <el-date-picker
                    v-model="biddingDialog.form.tender_deadline_at"
                    type="datetime"
                    value-format="YYYY-MM-DDTHH:mm:ss"
                    format="YYYY-MM-DD HH:mm"
                    placeholder="可选"
                  ></el-date-picker>
                </el-form-item>
              </el-form>
              <template #footer>
                <el-button @click="biddingDialog.visible = false">取消</el-button>
                <el-button type="primary" :loading="biddingDialog.loading" :disabled="!biddingDialog.file" @click="saveBiddingProject">
                  上传并创建
                </el-button>
              </template>
            </el-dialog>

            <el-dialog
              v-model="biddingLlmEditDialog.visible"
              title="修改 DeepSeek 建议"
              width="680px"
              destroy-on-close
            >
              <el-form label-position="top" :model="biddingLlmEditDialog.form">
                <div class="form-grid-2">
                  <el-form-item label="建议类型">
                    <el-select v-model="biddingLlmEditDialog.form.decision" placeholder="选择建议类型">
                      <el-option
                        v-for="option in biddingLlmDecisionOptions"
                        :key="option.value"
                        :label="option.label"
                        :value="option.value"
                      />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="建议业务动作">
                    <el-select v-model="biddingLlmEditDialog.form.primary_business_action" clearable placeholder="选择动作">
                      <el-option
                        v-for="option in biddingBusinessObjectActionOptions"
                        :key="option.value"
                        :label="option.label"
                        :value="option.value"
                      />
                    </el-select>
                  </el-form-item>
                </div>
                <el-form-item label="建议标题">
                  <el-input v-model="biddingLlmEditDialog.form.suggested_title" maxlength="160" show-word-limit />
                </el-form-item>
                <el-form-item label="建议子类">
                  <el-input v-model="biddingLlmEditDialog.form.suggested_object_subtype" maxlength="120" placeholder="保持原子类或输入候选子类" />
                </el-form-item>
                <el-form-item label="判断说明">
                  <el-input
                    v-model="biddingLlmEditDialog.form.reason"
                    type="textarea"
                    :rows="3"
                    maxlength="800"
                    show-word-limit
                  />
                </el-form-item>
                <el-form-item label="人工建议">
                  <el-input
                    v-model="biddingLlmEditDialog.form.suggested_reviewer_note"
                    type="textarea"
                    :rows="3"
                    maxlength="800"
                    show-word-limit
                  />
                </el-form-item>
                <el-form-item label="处理备注">
                  <el-input
                    v-model="biddingLlmEditDialog.form.reviewer_note"
                    type="textarea"
                    :rows="2"
                    maxlength="4000"
                    show-word-limit
                    placeholder="说明为什么修改该建议，便于后续追溯"
                  />
                </el-form-item>
              </el-form>
              <template #footer>
                <el-button @click="biddingLlmEditDialog.visible = false">取消</el-button>
                <el-button type="primary" :loading="biddingLlmDecisionSubmitting" @click="submitModifyBiddingLlmReview">
                  保存修改
                </el-button>
              </template>
            </el-dialog>
          </template>
        </template>

        <template v-else-if="routeName === 'enterpriseProfile'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">企业资料</p>
              <h2>企业资料库</h2>
            </div>
            <div class="heading-actions">
              <el-button :icon="Refresh" plain :loading="enterpriseProfileLoading" @click="refreshEnterpriseProfile">
                刷新
              </el-button>
              <el-button
                v-if="canEditEnterpriseProfile"
                :icon="Plus"
                type="primary"
                :disabled="enterpriseProfileFeatureDisabled"
                @click="openEnterpriseProfileDialog('create')"
              >
                新建资料
              </el-button>
            </div>
          </div>

          <el-alert
            v-if="enterpriseProfileFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="企业资料库暂不可用"
            description="请联系管理员确认功能状态后再使用。"
          />
          <template v-else>
            <section class="dashboard-section">
              <div class="metric-grid">
                <article
                  v-for="card in enterpriseProfileOverviewCards"
                  :key="card.key"
                  :class="['metric-card', card.tone]"
                >
                  <span>{{ card.title }}</span>
                  <strong>{{ card.value }}</strong>
                  <small>{{ card.detail }}</small>
                </article>
              </div>
            </section>

            <section class="dashboard-section">
              <div class="cost-db-filters">
                <el-select v-model="enterpriseProfileFilters.category" placeholder="资料分类" clearable>
                  <el-option
                    v-for="option in enterpriseProfileCategoryOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <el-select v-model="enterpriseProfileFilters.status" placeholder="状态" clearable>
                  <el-option
                    v-for="option in enterpriseProfileStatusOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <el-input
                  v-model="enterpriseProfileFilters.keyword"
                  :prefix-icon="Search"
                  clearable
                  placeholder="搜索标题、摘要、标签"
                  @keyup.enter="loadEnterpriseProfileItems"
                />
                <el-button :icon="Search" plain :loading="enterpriseProfileLoading" @click="loadEnterpriseProfileItems">
                  查询
                </el-button>
              </div>

              <el-table
                v-loading="enterpriseProfileLoading"
                :data="enterpriseProfileItems"
                class="users-table"
                empty-text="暂无企业资料"
              >
                <el-table-column label="资料" min-width="260">
                  <template #default="{ row }">
                    <div class="operation-client">
                      <strong>{{ row.title }}</strong>
                      <small>{{ enterpriseProfileCategoryLabel(row.category) }} · {{ row.subcategory || '未分组' }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="110">
                  <template #default="{ row }">
                    <el-tag :type="enterpriseProfileStatusTag(row.status)" effect="plain">
                      {{ enterpriseProfileStatusLabel(row.status) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="有效期" width="140">
                  <template #default="{ row }">{{ row.valid_until || '-' }}</template>
                </el-table-column>
                <el-table-column label="附件" width="90">
                  <template #default="{ row }">{{ row.attachment_count || 0 }}</template>
                </el-table-column>
                <el-table-column label="资料体检" min-width="220">
                  <template #default="{ row }">
                    <div class="tag-list">
                      <el-tag
                        v-for="issue in row.quality_issues || []"
                        :key="issue.code"
                        :type="enterpriseProfileIssueTag(issue.code)"
                        effect="plain"
                      >
                        {{ enterpriseProfileIssueLabel(issue.code) }}
                      </el-tag>
                      <el-tag v-if="!(row.quality_issues || []).length" type="success" effect="plain">可用</el-tag>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="更新时间" width="170">
                  <template #default="{ row }">{{ formatDate(row.updated_at) }}</template>
                </el-table-column>
                <el-table-column label="操作" width="250" fixed="right">
                  <template #default="{ row }">
                    <div class="row-actions">
                      <el-button size="small" plain @click="openEnterpriseProfileDialog('view', row)">详情</el-button>
                      <el-button
                        v-if="canEditEnterpriseProfile && row.status !== 'archived'"
                        size="small"
                        type="primary"
                        plain
                        @click="openEnterpriseProfileDialog('edit', row)"
                      >
                        编辑
                      </el-button>
                      <el-button
                        v-if="canEditEnterpriseProfile && row.status !== 'archived'"
                        size="small"
                        plain
                        @click="openEnterpriseProfileAttachmentDialog(row)"
                      >
                        附件
                      </el-button>
                      <el-button
                        v-if="canApproveEnterpriseProfile && row.status !== 'active' && row.status !== 'archived'"
                        size="small"
                        type="success"
                        plain
                        @click="activateEnterpriseProfileItem(row)"
                      >
                        启用
                      </el-button>
                      <el-button
                        v-if="canApproveEnterpriseProfile && row.status !== 'archived'"
                        size="small"
                        type="danger"
                        plain
                        @click="archiveEnterpriseProfileItem(row)"
                      >
                        归档
                      </el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
              <el-pagination
                v-if="enterpriseProfileTotal > enterpriseProfilePageSize"
                v-model:current-page="enterpriseProfilePage"
                :page-size="enterpriseProfilePageSize"
                :total="enterpriseProfileTotal"
                layout="prev, pager, next, total"
                @current-change="loadEnterpriseProfileItems"
              />
            </section>
          </template>

          <el-dialog
            v-model="enterpriseProfileDialog.visible"
            :title="enterpriseProfileDialogTitle"
            width="820px"
          >
            <el-form label-position="top" :model="enterpriseProfileDialog.form">
              <el-form-item label="资料名称">
                <el-input
                  v-model="enterpriseProfileDialog.form.title"
                  :disabled="enterpriseProfileDialog.mode === 'view'"
                  maxlength="255"
                  placeholder="例如：公司简介、营业执照、类似项目业绩、施工组织设计通用措施"
                />
              </el-form-item>
              <el-form-item label="资料形式">
                <el-radio-group
                  v-model="enterpriseProfileDialog.form.material_form"
                  :disabled="enterpriseProfileDialog.mode === 'view'"
                >
                  <el-radio-button
                    v-for="option in enterpriseProfileMaterialFormOptions"
                    :key="option.value"
                    :label="option.value"
                  >
                    {{ option.label }}
                  </el-radio-button>
                </el-radio-group>
              </el-form-item>
              <div class="form-grid">
                <el-form-item label="分类">
                  <el-select
                    v-model="enterpriseProfileDialog.form.category"
                    class="full-width"
                    :disabled="enterpriseProfileDialog.mode === 'view'"
                  >
                    <el-option
                      v-for="option in enterpriseProfileCategoryOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="子类">
                  <el-input v-model="enterpriseProfileDialog.form.subcategory" :disabled="enterpriseProfileDialog.mode === 'view'" maxlength="128" />
                </el-form-item>
                <el-form-item label="资料键">
                  <el-input v-model="enterpriseProfileDialog.form.profile_key" :disabled="enterpriseProfileDialog.mode === 'view'" maxlength="128" />
                </el-form-item>
                <el-form-item label="有效期至">
                  <el-date-picker
                    v-model="enterpriseProfileDialog.form.valid_until"
                    class="full-width"
                    type="date"
                    value-format="YYYY-MM-DD"
                    :disabled="enterpriseProfileDialog.mode === 'view'"
                  />
                </el-form-item>
              </div>
              <el-form-item label="摘要">
                <el-input
                  v-model="enterpriseProfileDialog.form.summary"
                  :disabled="enterpriseProfileDialog.mode === 'view'"
                  maxlength="1000"
                  show-word-limit
                />
              </el-form-item>
              <el-form-item v-if="enterpriseProfileDialog.form.material_form === 'text'" label="文本内容">
                <el-input
                  v-model="enterpriseProfileDialog.form.content_text"
                  type="textarea"
                  :rows="6"
                  :disabled="enterpriseProfileDialog.mode === 'view'"
                  maxlength="12000"
                  show-word-limit
                />
              </el-form-item>
              <section v-else class="bidding-draft-panel">
                <el-alert
                  class="dashboard-alert"
                  type="info"
                  show-icon
                  :closable="false"
                  title="附件形式会先上传文件，再把附件绑定到这条企业资料。"
                />
                <el-upload
                  action="#"
                  :auto-upload="false"
                  :show-file-list="false"
                  :disabled="enterpriseProfileDialog.mode === 'view'"
                  :on-change="uploadEnterpriseProfileInlineAttachmentFile"
                >
                  <el-button :icon="Upload" plain :loading="enterpriseProfileDialog.uploading">
                    上传资料附件
                  </el-button>
                </el-upload>
                <div class="form-grid">
                  <el-form-item label="file_id">
                    <el-input
                      v-model="enterpriseProfileDialog.form.attachment_file_id"
                      :disabled="enterpriseProfileDialog.mode === 'view'"
                      placeholder="上传成功后自动填入"
                    />
                  </el-form-item>
                  <el-form-item label="附件类型">
                    <el-input
                      v-model="enterpriseProfileDialog.form.attachment_type"
                      :disabled="enterpriseProfileDialog.mode === 'view'"
                      maxlength="64"
                    />
                  </el-form-item>
                </div>
                <el-form-item label="附件说明">
                  <el-input
                    v-model="enterpriseProfileDialog.form.attachment_description"
                    type="textarea"
                    :rows="3"
                    :disabled="enterpriseProfileDialog.mode === 'view'"
                    maxlength="1000"
                    show-word-limit
                  />
                </el-form-item>
              </section>
              <div class="form-grid">
                <el-form-item label="标签">
                  <el-input v-model="enterpriseProfileDialog.form.tagsText" :disabled="enterpriseProfileDialog.mode === 'view'" placeholder="用逗号分隔" />
                </el-form-item>
                <el-form-item label="适用范围">
                  <el-input v-model="enterpriseProfileDialog.form.applicable_scope" :disabled="enterpriseProfileDialog.mode === 'view'" maxlength="1000" />
                </el-form-item>
              </div>
              <el-form-item v-if="enterpriseProfileDialog.mode === 'edit'" label="变更原因">
                <el-input v-model="enterpriseProfileDialog.form.change_reason" maxlength="500" />
              </el-form-item>

              <div v-if="enterpriseProfileDialog.mode !== 'create'" class="bidding-risk-card-summary">
                <article>
                  <strong>{{ enterpriseProfileDialog.detail?.attachment_count || 0 }}</strong>
                  <small>附件数量</small>
                </article>
                <article>
                  <strong>{{ enterpriseProfileDialog.detail?.events?.length || 0 }}</strong>
                  <small>审计事件</small>
                </article>
                <article>
                  <strong>{{ enterpriseProfileDialog.detail?.status || '-' }}</strong>
                  <small>当前状态</small>
                </article>
              </div>
              <el-table
                v-if="enterpriseProfileDialog.detail?.attachments?.length"
                :data="enterpriseProfileDialog.detail.attachments"
                class="users-table"
                size="small"
              >
                <el-table-column prop="original_filename" label="附件" min-width="220" show-overflow-tooltip />
                <el-table-column prop="attachment_type" label="类型" width="120" />
                <el-table-column prop="file_id" label="file_id" min-width="220" show-overflow-tooltip />
              </el-table>
            </el-form>
            <template #footer>
              <el-button @click="enterpriseProfileDialog.visible = false">关闭</el-button>
              <el-button
                v-if="enterpriseProfileDialog.mode !== 'view'"
                type="primary"
                :loading="state.submitting"
                @click="submitEnterpriseProfileItem"
              >
                保存
              </el-button>
            </template>
          </el-dialog>

          <el-dialog v-model="enterpriseProfileAttachmentDialog.visible" title="绑定资料附件" width="620px">
            <el-form label-position="top" :model="enterpriseProfileAttachmentDialog.form">
              <el-alert
                class="dashboard-alert"
                type="info"
                show-icon
                :closable="false"
                title="可直接上传文件，也可粘贴已有 file_id"
              />
              <el-upload
                action="#"
                :auto-upload="false"
                :show-file-list="false"
                :on-change="uploadEnterpriseProfileAttachmentFile"
              >
                <el-button :icon="Upload" plain :loading="enterpriseProfileAttachmentDialog.uploading">
                  上传附件
                </el-button>
              </el-upload>
              <el-form-item label="file_id">
                <el-input v-model="enterpriseProfileAttachmentDialog.form.file_id" placeholder="上传成功后自动填入" />
              </el-form-item>
              <el-form-item label="附件类型">
                <el-input v-model="enterpriseProfileAttachmentDialog.form.attachment_type" maxlength="64" />
              </el-form-item>
              <el-form-item label="说明">
                <el-input v-model="enterpriseProfileAttachmentDialog.form.description" type="textarea" :rows="3" maxlength="1000" />
              </el-form-item>
              <el-checkbox v-model="enterpriseProfileAttachmentDialog.form.is_primary">设为主附件</el-checkbox>
            </el-form>
            <template #footer>
              <el-button @click="enterpriseProfileAttachmentDialog.visible = false">取消</el-button>
              <el-button type="primary" :loading="state.submitting" @click="submitEnterpriseProfileAttachment">
                绑定
              </el-button>
            </template>
          </el-dialog>
        </template>

        <template v-else-if="routeName === 'costMeasurement'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">COST-MEASURE-1</p>
              <h2>&#25104;&#26412;&#27979;&#31639;&#38381;&#29615;</h2>
              <p>&#23548;&#20837;&#21382;&#21490;&#27979;&#31639; Excel&#65292;&#32479;&#19968;&#37325;&#31639;&#12289;&#26174;&#24335;&#22797;&#26680;&#12289;&#38145;&#23450;&#24182;&#23548;&#20986;&#25104;&#26524;&#12290;</p>
            </div>
            <div class="heading-actions">
              <input
                ref="costMeasurementFileInput"
                type="file"
                accept=".xlsx,.xlsm"
                hidden
                @change="handleCostMeasurementFile"
              />
              <el-button
                v-if="canEditCostMeasurement"
                type="primary"
                :icon="Upload"
                :disabled="costMeasurementFeatureDisabled"
                @click="costMeasurementFileInput?.click()"
              >
                &#23548;&#20837;&#27979;&#31639; Excel
              </el-button>
              <el-button :icon="Refresh" plain :loading="costMeasurementLoading" @click="loadCostMeasurements">
                &#21047;&#26032;
              </el-button>
            </div>
          </div>

          <el-alert
            v-if="costMeasurementFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="&#25104;&#26412;&#27979;&#31639;&#21151;&#33021;&#23578;&#26410;&#24320;&#21551;"
          />
          <template v-else>
            <div class="cost-workbench-cards">
              <article class="cost-workbench-card">
                <span>&#27979;&#31639;&#39033;&#30446;</span>
                <strong>{{ costMeasurementTotal }}</strong>
                <small>&#21382;&#21490;&#25104;&#26524;&#19982;&#26032;&#27979;&#31639;&#32479;&#19968;&#31649;&#29702;</small>
              </article>
              <article class="cost-workbench-card warning">
                <span>&#24453;&#22797;&#26680;&#34892;</span>
                <strong>{{ costMeasurements.reduce((sum, row) => sum + Number(row.review_line_count || 0), 0) }}</strong>
                <small>&#21382;&#21490;&#20844;&#24335;&#24046;&#24322;&#25110;&#20165;&#32508;&#21512;&#20215;</small>
              </article>
              <article class="cost-workbench-card success">
                <span>&#24050;&#38145;&#23450;&#29256;&#26412;</span>
                <strong>{{ costMeasurements.filter((row) => row.status === 'locked').length }}</strong>
                <small>&#21487;&#20316;&#20026;&#21518;&#32493;&#25237;&#26631;&#19982;&#25104;&#26412;&#21488;&#36134;&#20381;&#25454;</small>
              </article>
            </div>
            <el-table
              v-loading="costMeasurementLoading"
              :data="costMeasurements"
              row-key="id"
              class="users-table cost-db-table"
              empty-text="&#26242;&#26080;&#25104;&#26412;&#27979;&#31639;&#39033;&#30446;"
              @row-dblclick="openCostMeasurement"
            >
              <el-table-column label="&#27979;&#31639;&#39033;&#30446;" min-width="300">
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.measurement_code }} &#183; {{ row.name }}</strong>
                    <small>{{ row.project_name || row.source_filename || '-' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="&#29366;&#24577;" width="110">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'locked' ? 'success' : 'warning'" effect="plain">
                    {{ row.status === 'locked' ? '\u5df2\u9501\u5b9a' : '\u8349\u7a3f' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="line_count" label="&#28165;&#21333;&#34892;" width="90" />
              <el-table-column prop="matched_quota_count" label="&#23450;&#39069;&#21629;&#20013;" width="100" />
              <el-table-column label="&#24453;&#22797;&#26680;" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.review_line_count ? 'danger' : 'success'" effect="plain">{{ row.review_line_count || 0 }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="&#31246;&#21069;&#21512;&#35745;" width="150" align="right">
                <template #default="{ row }">{{ formatAmount(row.pretax_total) }}</template>
              </el-table-column>
              <el-table-column label="&#21547;&#31246;&#21512;&#35745;" width="150" align="right">
                <template #default="{ row }">{{ formatAmount(row.grand_total) }}</template>
              </el-table-column>
              <el-table-column label="&#25805;&#20316;" width="110" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" plain @click="openCostMeasurement(row)">&#26597;&#30475;</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>

          <el-dialog
            v-model="costMeasurementImportDialog.visible"
            title="&#23548;&#20837;&#25104;&#26412;&#27979;&#31639;"
            width="720px"
            destroy-on-close
          >
            <el-alert
              v-if="costMeasurementImportDialog.preview"
              type="info"
              :closable="false"
              show-icon
              :title="`\u5df2\u8bc6\u522b ${costMeasurementImportDialog.preview.line_count || 0} \u884c\uff0c\u5f85\u590d\u6838 ${costMeasurementImportDialog.preview.review_line_count || 0} \u884c`"
            />
            <el-form label-position="top">
              <el-form-item label="&#27979;&#31639;&#21517;&#31216;">
                <el-input v-model="costMeasurementImportDialog.name" />
              </el-form-item>
              <el-form-item label="&#39033;&#30446;&#21517;&#31216;">
                <el-input v-model="costMeasurementImportDialog.project_name" />
              </el-form-item>
              <el-form-item label="&#28304;&#25991;&#20214;">
                <el-input :model-value="costMeasurementImportDialog.file?.name || '-'" disabled />
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="costMeasurementImportDialog.visible = false">&#21462;&#28040;</el-button>
              <el-button type="primary" :loading="state.submitting" @click="commitCostMeasurementImport">
                &#21019;&#24314;&#27979;&#31639;&#33609;&#31295;
              </el-button>
            </template>
          </el-dialog>

          <el-drawer
            v-model="costMeasurementDrawer.visible"
            size="94%"
            :title="costMeasurementDetail?.name || '\u6210\u672c\u6d4b\u7b97\u8be6\u60c5'"
            destroy-on-close
          >
            <template v-if="costMeasurementDetail">
              <div class="heading-actions">
                <el-tag :type="costMeasurementDetail.status === 'locked' ? 'success' : 'warning'">
                  {{ costMeasurementDetail.status === 'locked' ? '\u5df2\u9501\u5b9a' : '\u8349\u7a3f' }}
                </el-tag>
                <span>&#31246;&#21069; {{ formatAmount(costMeasurementDetail.pretax_total) }}</span>
                <span>&#21547;&#31246; {{ formatAmount(costMeasurementDetail.grand_total) }}</span>
                <span>&#24453;&#22797;&#26680; {{ costMeasurementDetail.review_line_count || 0 }} &#34892;</span>
                <el-button
                  v-if="canEditCostMeasurement && costMeasurementDetail.status === 'draft'"
                  :loading="costMeasurementLoading"
                  @click="recalculateCostMeasurement"
                >&#32479;&#19968;&#37325;&#31639;</el-button>
                <el-button
                  v-if="canApproveCostMeasurement && costMeasurementDetail.status === 'draft'"
                  type="success"
                  plain
                  @click="lockCostMeasurement"
                >&#22797;&#26680;&#24182;&#38145;&#23450;</el-button>
                <el-button
                  v-if="canEditCostMeasurement && costMeasurementDetail.status === 'locked'"
                  type="primary"
                  plain
                  @click="previewCostMeasurementDrafts"
                >&#27785;&#28096;&#25104;&#26412;&#24211;</el-button>
                <el-button v-if="canExportCostMeasurement" :icon="Download" plain @click="exportCostMeasurement">
                  &#23548;&#20986; Excel
                </el-button>
              </div>
              <el-table :data="costMeasurementDetail.lines || []" row-key="id" max-height="680" class="users-table cost-db-table">
                <el-table-column label="&#39033;&#30446;" min-width="280" fixed="left">
                  <template #default="{ row }">
                    <div class="operation-client">
                      <strong>{{ row.sequence_no || row.sort_order }}. {{ row.item_name }}</strong>
                      <small>{{ row.section_name || '-' }} &#183; {{ row.unit || '-' }}</small>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="&#24037;&#31243;&#37327;" width="130">
                  <template #default="{ row }">
                    <el-input-number v-model="row.quantity" :min="0" :controls="false" size="small" :disabled="costMeasurementDetail.status !== 'draft' || !canEditCostMeasurement" />
                  </template>
                </el-table-column>
                <el-table-column label="&#20154;&#24037;" width="125">
                  <template #default="{ row }"><el-input-number v-model="row.labor_unit_price" :min="0" :controls="false" size="small" :disabled="costMeasurementDetail.status !== 'draft' || !canEditCostMeasurement" /></template>
                </el-table-column>
                <el-table-column label="&#20027;&#26448;" width="125">
                  <template #default="{ row }"><el-input-number v-model="row.main_material_unit_price" :min="0" :controls="false" size="small" :disabled="costMeasurementDetail.status !== 'draft' || !canEditCostMeasurement" /></template>
                </el-table-column>
                <el-table-column label="&#36741;&#26448;&#21450;&#26426;&#26800;" width="135">
                  <template #default="{ row }"><el-input-number v-model="row.auxiliary_machinery_unit_price" :min="0" :controls="false" size="small" :disabled="costMeasurementDetail.status !== 'draft' || !canEditCostMeasurement" /></template>
                </el-table-column>
                <el-table-column label="&#20998;&#21253;" width="125">
                  <template #default="{ row }"><el-input-number v-model="row.subcontract_unit_price" :min="0" :controls="false" size="small" :disabled="costMeasurementDetail.status !== 'draft' || !canEditCostMeasurement" /></template>
                </el-table-column>
                <el-table-column label="&#21382;&#21490;&#21333;&#20215;" width="120" align="right">
                  <template #default="{ row }">{{ formatAmount(row.source_unit_price) }}</template>
                </el-table-column>
                <el-table-column label="&#37325;&#31639;&#21333;&#20215;" width="120" align="right">
                  <template #default="{ row }">{{ formatAmount(row.calculated_unit_price) }}</template>
                </el-table-column>
                <el-table-column label="&#21512;&#35745;" width="130" align="right">
                  <template #default="{ row }">{{ formatAmount(row.calculated_total_price) }}</template>
                </el-table-column>
                <el-table-column label="&#22797;&#26680;" width="145" fixed="right">
                  <template #default="{ row }">
                    <div class="row-actions">
                      <el-tag :type="row.review_status === 'required' ? 'danger' : 'success'" effect="plain">
                        {{ row.review_status === 'required' ? '\u5f85\u590d\u6838' : '\u5df2\u590d\u6838' }}
                      </el-tag>
                      <el-button
                        v-if="canEditCostMeasurement && costMeasurementDetail.status === 'draft'"
                        size="small"
                        type="primary"
                        plain
                        @click="saveCostMeasurementLine(row)"
                      >&#20445;&#23384;</el-button>
                    </div>
                  </template>
                </el-table-column>
              </el-table>
            </template>
          </el-drawer>

          <el-dialog
            v-model="costMeasurementDraftDialog.visible"
            title="&#27785;&#28096;&#20225;&#19994;&#25104;&#26412;&#24211;"
            width="1120px"
            destroy-on-close
          >
            <el-alert
              type="info"
              show-icon
              :closable="false"
              title="&#21482;&#20174;&#24050;&#38145;&#23450;&#29256;&#26412;&#20013;&#25552;&#21462;&#24050;&#22797;&#26680;&#34892;&#65307;&#19981;&#33258;&#21160;&#21551;&#29992;&#65292;&#19981;&#35206;&#30422;&#29616;&#26377;&#24050;&#21551;&#29992;&#25110;&#24453;&#26680;&#23450;&#26465;&#30446;&#12290;"
            />
            <div v-if="costMeasurementDraftDialog.summary" class="cost-workbench-cards">
              <article class="cost-workbench-card">
                <span>&#26412;&#27425;&#20505;&#36873;</span>
                <strong>{{ costMeasurementDraftDialog.summary.selected_line_count || 0 }}</strong>
                <small>&#38145;&#23450;&#27979;&#31639;&#29256;&#26412;&#30340;&#26126;&#32454;&#34892;</small>
              </article>
              <article class="cost-workbench-card success">
                <span>&#21487;&#29983;&#25104;&#24453;&#26680;&#23450;&#26465;&#30446;</span>
                <strong>{{ costMeasurementDraftDialog.summary.eligible_count || 0 }}</strong>
                <small>&#37325;&#22797;&#20505;&#36873; {{ costMeasurementDraftDialog.summary.within_measurement_duplicate_count || 0 }} &#26465;&#65292;&#21487;&#20154;&#24037;&#25913;&#36873;</small>
              </article>
              <article class="cost-workbench-card warning">
                <span>&#24050;&#38459;&#26029;</span>
                <strong>{{ costMeasurementDraftDialog.summary.blocked_count || 0 }}</strong>
                <small>&#24453;&#22797;&#26680;&#12289;&#37325;&#22797;&#25110;&#20215;&#26684;&#26080;&#25928;</small>
              </article>
            </div>
            <el-form label-position="top">
              <el-form-item label="&#26412;&#25209;&#27425;&#35828;&#26126;&#65288;&#20889;&#20837;&#25104;&#26412;&#21382;&#21490;&#19982;&#27979;&#31639;&#20107;&#20214;&#65289;">
                <el-input
                  v-model="costMeasurementDraftDialog.note"
                  type="textarea"
                  :rows="2"
                  maxlength="2000"
                  show-word-limit
                  placeholder="&#20363;&#65306;&#24050;&#26680;&#23545;&#20449;&#36798;&#39033;&#30446;&#21382;&#21490;&#25104;&#26412;&#21475;&#24452;"
                />
              </el-form-item>
            </el-form>
            <el-table
              v-loading="costMeasurementDraftDialog.loading"
              :data="costMeasurementDraftDialog.candidates"
              row-key="line_id"
              max-height="520"
              class="users-table cost-db-table"
            >
              <el-table-column label="&#36873;&#25321;" width="70" align="center">
                <template #default="{ row }">
                  <el-checkbox v-model="row.selected" :disabled="!row.can_create" />
                </template>
              </el-table-column>
              <el-table-column label="&#27979;&#31639;&#26126;&#32454;" min-width="260">
                <template #default="{ row }">
                  <div class="operation-client">
                    <strong>{{ row.sequence_no || row.line_id }}. {{ row.item_name }}</strong>
                    <small>{{ row.feature || '-' }}</small>
                    <small>{{ row.source_sheet || '-' }} &#183; &#21407;&#34892; {{ row.source_row_index || '-' }}</small>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="unit" label="&#21333;&#20301;" width="80" />
              <el-table-column label="&#30452;&#25509;&#25104;&#26412;" width="110" align="right">
                <template #default="{ row }">{{ formatAmount(row.direct_unit_price) }}</template>
              </el-table-column>
              <el-table-column label="&#31649;&#29702;&#36153;&#21033;&#28070;" width="120" align="right">
                <template #default="{ row }">{{ formatAmount(row.management_profit_unit_price) }}</template>
              </el-table-column>
              <el-table-column label="&#31246;&#21069;&#32508;&#21512;&#25104;&#26412;" width="135" align="right">
                <template #default="{ row }">{{ formatAmount(row.calculated_unit_price) }}</template>
              </el-table-column>
              <el-table-column label="&#20505;&#36873;&#29366;&#24577;" min-width="220">
                <template #default="{ row }">
                  <div class="operation-client">
                    <el-tag :type="costMeasurementDraftStatusTag(row.candidate_status)" effect="plain">
                      {{ costMeasurementDraftStatusLabel(row.candidate_status) }}
                    </el-tag>
                    <small>{{ row.reason_message || '-' }}</small>
                    <small v-if="row.existing_cost_item">
                      &#24050;&#26377; #{{ row.existing_cost_item.id }} &#183; {{ row.existing_cost_item.status }} &#183; {{ formatAmount(row.existing_cost_item.price) }}
                    </small>
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <template #footer>
              <el-button @click="costMeasurementDraftDialog.visible = false">&#21462;&#28040;</el-button>
              <el-button
                type="primary"
                :loading="costMeasurementDraftDialog.submitting"
                :disabled="costMeasurementDraftSelectedCount === 0"
                @click="commitCostMeasurementDrafts"
              >&#25552;&#20132; {{ costMeasurementDraftSelectedCount }} &#26465;&#24453;&#26680;&#23450;&#26465;&#30446;</el-button>
            </template>
          </el-dialog>
        </template>


        <template v-else-if="routeName === 'costDb'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">成本参考</p>
              <h2>企业成本库</h2>
            </div>
            <div class="heading-actions">
              <el-button
                v-if="canApproveCostDb"
                :icon="DataAnalysis"
                plain
                :loading="costRagSyncing"
                :disabled="costDbFeatureDisabled || costRagSyncing"
                @click="syncActiveCostItemsToRag"
              >
                更新成本参考
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
              <el-button :icon="Refresh" plain :loading="costMasterLoading" @click="refreshCostMaster">刷新</el-button>
            </div>
          </div>

          <el-alert
            v-if="costDbFeatureDisabled"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="企业定额主库功能尚未开启"
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
                  成本参考更新状态：{{ costRagSyncStatus.status_label || costRagSyncSummaryLabel(costRagSyncStatus.status) }}
                  · 已启用 {{ costRagSyncStatus.active_count || 0 }} 条
                  · 最近成功 {{ formatShanghaiDate(costRagSyncStatus.latest_successful_run?.finished_at) }}
                </span>
              </template>
              <div>{{ costRagSyncStatus.message || '暂无同步状态' }}</div>
            </el-alert>
            <section class="cost-workbench-panel">
              <div class="cost-workbench-title">
                <el-tag type="success" effect="plain">当前主源</el-tag>
                <div>
                  <strong>企业成本库</strong>
                  <span>报价成本参考优先读取当前已启用的企业定额；旧成本条目仅保留历史维护和审计追溯。</span>
                </div>
              </div>
              <div class="cost-workbench-cards">
                <article
                  v-for="card in costMasterOverviewCards"
                  :key="card.key"
                  :class="['cost-workbench-card', card.tone]"
                >
                  <span>{{ card.title }}</span>
                  <strong>{{ card.value }}</strong>
                  <small>{{ card.detail }}</small>
                </article>
              </div>
            </section>

            <el-tabs
              v-model="costMasterActiveTab"
              class="cost-master-tabs"
              @tab-click="handleCostMasterTabClick"
            >
              <el-tab-pane label="定额主项" name="quotaItems">
                <div class="cost-db-filters cost-item-filters">
                  <el-input
                    v-model="costMasterFilters.keyword"
                    size="small"
                    clearable
                    placeholder="搜索编号/名称/工作内容/分部"
                    @keyup.enter="applyCostMasterFilters"
                    @clear="applyCostMasterFilters"
                  ></el-input>
                  <el-button size="small" type="primary" plain @click="applyCostMasterFilters">查询</el-button>
                </div>
                <el-table
                  v-loading="costMasterLoading && costMasterActiveTab === 'quotaItems'"
                  :data="enterpriseQuotaItems"
                  row-key="id"
                  class="users-table cost-db-table"
                  empty-text="暂无已启用企业定额主项"
                >
                  <el-table-column label="定额主项" min-width="280" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.quota_code || '-' }} {{ row.item_name || '-' }}</strong>
                        <small>{{ row.work_content || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="分部" min-width="160" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.section_code || '-' }}</strong>
                        <small>{{ row.section_name || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="unit" label="单位" width="80" />
                  <el-table-column label="工程量" width="100">
                    <template #default="{ row }">{{ row.quantity ?? '-' }}</template>
                  </el-table-column>
                  <el-table-column label="综合单价" width="120">
                    <template #default="{ row }">{{ formatPrice(row.unit_price) }}</template>
                  </el-table-column>
                  <el-table-column label="费用拆分" min-width="220">
                    <template #default="{ row }">
                      <div class="price-stack">
                        <span>人工：{{ formatPrice(row.labor_fee) }}</span>
                        <small>主材：{{ formatPrice(row.main_material_fee) }}</small>
                        <small>辅材：{{ formatPrice(row.auxiliary_material_fee) }}</small>
                        <small>机械：{{ formatPrice(row.machinery_fee) }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="组成" width="96">
                    <template #default="{ row }">{{ row.component_count || 0 }} 条</template>
                  </el-table-column>
                  <el-table-column label="操作" width="96" fixed="right">
                    <template #default="{ row }">
                      <el-button size="small" :icon="Document" plain @click="openEnterpriseQuotaItemDetail(row)">
                        详情
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="enterpriseQuotaItemTotal > costMasterPageSize"
                  v-model:current-page="costMasterPage"
                  :page-size="costMasterPageSize"
                  :total="enterpriseQuotaItemTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadEnterpriseQuotaItems"
                ></el-pagination>
              </el-tab-pane>

              <el-tab-pane label="组成明细" name="components">
                <div class="cost-db-filters cost-item-filters">
                  <el-input
                    v-model="costMasterFilters.keyword"
                    size="small"
                    clearable
                    placeholder="搜索父级编号/资源名称/组成类型"
                    @keyup.enter="applyCostMasterFilters"
                    @clear="applyCostMasterFilters"
                  ></el-input>
                  <el-input
                    v-model="costMasterFilters.fee_bucket"
                    size="small"
                    clearable
                    placeholder="费用桶"
                    @keyup.enter="applyCostMasterFilters"
                    @clear="applyCostMasterFilters"
                  ></el-input>
                  <el-button size="small" type="primary" plain @click="applyCostMasterFilters">查询</el-button>
                </div>
                <el-table
                  v-loading="costMasterLoading && costMasterActiveTab === 'components'"
                  :data="enterpriseQuotaComponents"
                  row-key="id"
                  class="users-table cost-db-table"
                  empty-text="暂无已启用企业定额组成明细"
                >
                  <el-table-column label="所属主项" min-width="220" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.quota_code || row.parent_quota_code || '-' }}</strong>
                        <small>{{ row.quota_item_name || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="资源" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.resource_name || '-' }}</strong>
                        <small>{{ row.resource_code || row.component_type || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="unit" label="单位" width="80" />
                  <el-table-column label="消耗量" width="100">
                    <template #default="{ row }">{{ row.quantity ?? '-' }}</template>
                  </el-table-column>
                  <el-table-column label="单价" width="120">
                    <template #default="{ row }">{{ formatPrice(row.unit_price) }}</template>
                  </el-table-column>
                  <el-table-column label="金额" width="120">
                    <template #default="{ row }">{{ formatPrice(row.amount) }}</template>
                  </el-table-column>
                  <el-table-column label="费用桶" width="120">
                    <template #default="{ row }">{{ row.fee_bucket || '-' }}</template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="enterpriseQuotaComponentTotal > costMasterPageSize"
                  v-model:current-page="costMasterComponentPage"
                  :page-size="costMasterPageSize"
                  :total="enterpriseQuotaComponentTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadEnterpriseQuotaComponents"
                ></el-pagination>
              </el-tab-pane>

              <el-tab-pane label="资源价格" name="resources">
                <div class="cost-db-filters cost-item-filters">
                  <el-input
                    v-model="costMasterFilters.keyword"
                    size="small"
                    clearable
                    placeholder="搜索资源编码/资源名称/价格块"
                    @keyup.enter="applyCostMasterFilters"
                    @clear="applyCostMasterFilters"
                  ></el-input>
                  <el-input
                    v-model="costMasterFilters.resource_type"
                    size="small"
                    clearable
                    placeholder="资源类型"
                    @keyup.enter="applyCostMasterFilters"
                    @clear="applyCostMasterFilters"
                  ></el-input>
                  <el-button size="small" type="primary" plain @click="applyCostMasterFilters">查询</el-button>
                </div>
                <el-table
                  v-loading="costMasterLoading && costMasterActiveTab === 'resources'"
                  :data="enterpriseQuotaResources"
                  row-key="id"
                  class="users-table cost-db-table"
                  empty-text="暂无已启用企业定额资源价格"
                >
                  <el-table-column label="资源" min-width="260" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.resource_name || '-' }}</strong>
                        <small>{{ row.resource_code || '-' }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="resource_type" label="类型" width="120" />
                  <el-table-column prop="unit" label="单位" width="80" />
                  <el-table-column label="原价" width="120">
                    <template #default="{ row }">{{ formatPrice(row.price) }}</template>
                  </el-table-column>
                  <el-table-column label="含税/计算价" width="140">
                    <template #default="{ row }">{{ formatPrice(row.computed_price) }}</template>
                  </el-table-column>
                  <el-table-column label="税率" width="96">
                    <template #default="{ row }">{{ row.tax_rate ?? '-' }}</template>
                  </el-table-column>
                  <el-table-column label="价格块" min-width="180" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.price_block_label || '-' }}</template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="enterpriseQuotaResourceTotal > costMasterPageSize"
                  v-model:current-page="costMasterResourcePage"
                  :page-size="costMasterPageSize"
                  :total="enterpriseQuotaResourceTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadEnterpriseQuotaResources"
                ></el-pagination>
              </el-tab-pane>

              <el-tab-pane label="项目采购入库" name="purchaseImports">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="采购资料先形成价格观察与待审核候选；只有审核通过后才能生成新的待核定企业定额，当前已启用版本不会被直接修改。"
                ></el-alert>
                <section class="cost-workbench-panel">
                  <div class="cost-workbench-title">
                    <el-tag type="primary" effect="plain">快速入库</el-tag>
                    <div>
                      <strong>上传采购单、订购单或 ZIP</strong>
                      <span>系统自动识别材料、品牌、规格、单位、采购价、供应商、税率与运费口径，并保留源文件与原始行号。</span>
                    </div>
                  </div>
                  <div class="cost-db-filters cost-item-filters">
                    <el-input v-model="projectCostImportProjectName" size="small" placeholder="项目名称（必填）"></el-input>
                    <input
                      ref="projectCostImportFileInput"
                      type="file"
                      multiple
                      accept=".xlsx,.xlsm,.zip"
                      style="display: none"
                      @change="selectProjectCostImportFiles"
                    />
                    <input
                      ref="projectCostImportFolderInput"
                      type="file"
                      multiple
                      webkitdirectory
                      directory
                      style="display: none"
                      @change="selectProjectCostImportFiles"
                    />
                    <el-button size="small" plain @click="projectCostImportFileInput?.click()">选择文件/ZIP</el-button>
                    <el-button size="small" plain @click="projectCostImportFolderInput?.click()">选择项目文件夹</el-button>
                    <el-button
                      v-if="canEditCostDb"
                      size="small"
                      type="primary"
                      :loading="projectCostImportUploading"
                      :disabled="!projectCostImportProjectName.trim() || !projectCostImportFiles.length"
                      @click="uploadProjectCostImport"
                    >
                      开始解析
                    </el-button>
                    <span class="filter-count">已选 {{ projectCostImportFiles.length }} 个文件</span>
                  </div>
                </section>

                <el-table
                  v-loading="projectCostImportLoading"
                  :data="projectCostImportBatches"
                  row-key="id"
                  class="users-table cost-db-table"
                  empty-text="暂无项目采购导入批次"
                >
                  <el-table-column label="项目/批次" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">
                      <div class="operation-client">
                        <strong>{{ row.project_name }}</strong>
                        <small>{{ row.batch_uuid }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="解析结果" min-width="220">
                    <template #default="{ row }">
                      <div class="price-stack">
                        <span>文件 {{ row.parsed_file_count }}/{{ row.file_count }}</span>
                        <small>观察 {{ row.observation_count }} · 候选 {{ row.candidate_count }}</small>
                        <small>高置信 {{ row.high_confidence_count }} · 已通过 {{ row.approved_count }}</small>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="120">
                    <template #default="{ row }">
                      <el-tag :type="row.status === 'draft_created' ? 'success' : 'warning'" effect="plain">
                        {{ row.status === 'draft_created' ? '已生成草稿' : '待审核' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="企业定额草稿" min-width="160">
                    <template #default="{ row }">{{ row.target_quota_version_id ? `#${row.target_quota_version_id}` : '-' }}</template>
                  </el-table-column>
                  <el-table-column label="创建时间" width="170">
                    <template #default="{ row }">{{ formatShanghaiDate(row.created_at) }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="120" fixed="right">
                    <template #default="{ row }">
                      <el-button size="small" type="primary" plain @click="openProjectCostImportBatch(row)">审核候选</el-button>
                    </template>
                  </el-table-column>
                </el-table>
                <el-pagination
                  v-if="projectCostImportTotal > projectCostImportPageSize"
                  v-model:current-page="projectCostImportPage"
                  :page-size="projectCostImportPageSize"
                  :total="projectCostImportTotal"
                  layout="total, prev, pager, next"
                  small
                  @current-change="loadProjectCostImportBatches"
                ></el-pagination>

                <template v-if="selectedProjectCostImportBatch">
                  <el-divider content-position="left">
                    候选审核 · {{ selectedProjectCostImportBatch.project_name }}
                  </el-divider>
                  <div class="cost-db-filters cost-item-filters">
                    <el-select v-model="projectCostCandidateFilters.status" size="small" clearable placeholder="审核状态" @change="applyProjectCostCandidateFilters">
                      <el-option label="待审核" value="pending"></el-option>
                      <el-option label="已通过" value="approved"></el-option>
                      <el-option label="已驳回" value="rejected"></el-option>
                    </el-select>
                    <el-select v-model="projectCostCandidateFilters.risk_level" size="small" clearable placeholder="风险等级" @change="applyProjectCostCandidateFilters">
                      <el-option label="低风险" value="low"></el-option>
                      <el-option label="中风险" value="medium"></el-option>
                      <el-option label="高风险" value="high"></el-option>
                    </el-select>
                    <el-input v-model="projectCostCandidateFilters.keyword" size="small" clearable placeholder="材料/品牌/规格" @keyup.enter="applyProjectCostCandidateFilters"></el-input>
                    <el-button size="small" plain @click="applyProjectCostCandidateFilters">查询</el-button>
                    <el-button
                      v-if="canApproveCostDb"
                      size="small"
                      type="success"
                      plain
                      :disabled="!selectedProjectCostCandidates.length"
                      @click="reviewSelectedProjectCostCandidates('approve')"
                    >批量通过</el-button>
                    <el-button
                      v-if="canApproveCostDb"
                      size="small"
                      type="danger"
                      plain
                      :disabled="!selectedProjectCostCandidates.length"
                      @click="reviewSelectedProjectCostCandidates('reject')"
                    >批量驳回</el-button>
                    <el-button
                      v-if="canApproveCostDb"
                      size="small"
                      type="primary"
                      :disabled="!selectedProjectCostImportBatch.approved_count || selectedProjectCostImportBatch.target_quota_version_id"
                      @click="createProjectCostDraftVersion"
                    >生成企业定额草稿</el-button>
                  </div>
                  <el-table
                    v-loading="projectCostCandidateLoading"
                    :data="projectCostCandidates"
                    row-key="id"
                    class="users-table cost-db-table"
                    @selection-change="selectedProjectCostCandidates = $event"
                  >
                    <el-table-column v-if="canApproveCostDb" type="selection" width="48"></el-table-column>
                    <el-table-column label="材料候选" min-width="260" show-overflow-tooltip>
                      <template #default="{ row }">
                        <div class="operation-client">
                          <strong>{{ row.normalized_item_name || '-' }}</strong>
                          <small>{{ row.brand || '-' }} · {{ row.spec || '-' }}</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column prop="unit" label="单位" width="80"></el-table-column>
                    <el-table-column label="价格区间" min-width="180">
                      <template #default="{ row }">
                        <div class="price-stack">
                          <span>建议 {{ formatPrice(row.recommended_price) }}</span>
                          <small>{{ formatPrice(row.min_price) }} ～ {{ formatPrice(row.max_price) }}</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="样本" width="100">
                      <template #default="{ row }">{{ row.observation_count }} 次 / {{ row.supplier_count }} 家</template>
                    </el-table-column>
                    <el-table-column label="质量" min-width="150">
                      <template #default="{ row }">
                        <div class="price-stack">
                          <el-tag :type="row.risk_level === 'high' ? 'danger' : row.risk_level === 'low' ? 'success' : 'warning'" effect="plain">
                            {{ row.risk_level }}
                          </el-tag>
                          <small>置信度 {{ Math.round((row.confidence_score || 0) * 100) }}%</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="主库匹配" min-width="190" show-overflow-tooltip>
                      <template #default="{ row }">
                        <div class="operation-client">
                          <strong>{{ row.matched_resource_name || '新增资源' }}</strong>
                          <small>{{ row.match_type || '-' }} · {{ Math.round((row.match_confidence || 0) * 100) }}%</small>
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="审核状态" width="100">
                      <template #default="{ row }">
                        <el-tag :type="row.status === 'approved' ? 'success' : row.status === 'rejected' ? 'danger' : 'warning'" effect="plain">
                          {{ row.status === 'approved' ? '已通过' : row.status === 'rejected' ? '已驳回' : '待审核' }}
                        </el-tag>
                      </template>
                    </el-table-column>
                  </el-table>
                  <el-pagination
                    v-if="projectCostCandidateTotal > projectCostCandidatePageSize"
                    v-model:current-page="projectCostCandidatePage"
                    :page-size="projectCostCandidatePageSize"
                    :total="projectCostCandidateTotal"
                    layout="total, prev, pager, next"
                    small
                    @current-change="loadProjectCostCandidates"
                  ></el-pagination>
                </template>
              </el-tab-pane>
            </el-tabs>

            <el-divider content-position="left">历史 cost_items 维护区</el-divider>
            <section class="cost-workbench-panel">
              <div class="cost-workbench-title">
                <el-tag type="info" effect="plain">历史维护</el-tag>
                <div>
                  <strong>旧 cost_items 维护工作台</strong>
                  <span>旧成本条目当前不再作为报价主源，仅保留导入、审计、状态流向等历史追溯能力。</span>
                </div>
              </div>
              <div class="cost-workbench-cards">
                <article
                  v-for="card in costDbOverviewCards"
                  :key="card.key"
                  :class="['cost-workbench-card', card.tone]"
                >
                  <span>{{ card.title }}</span>
                  <strong>{{ card.value }}</strong>
                  <small>{{ card.detail }}</small>
                </article>
              </div>
            </section>
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
            <div v-if="canApproveCostDb" class="cost-bulk-bar">
              <div class="cost-bulk-summary">
                <strong>批量操作</strong>
                <span>{{ costDbSelectionSummary }}</span>
              </div>
              <div class="cost-bulk-actions">
                <el-button
                  :icon="Select"
                  plain
                  :loading="costAllSelecting"
                  :disabled="costDbFeatureDisabled || costDbLoading || costAllSelecting || costItemTotal === 0"
                  @click="toggleSelectAllCostItems"
                >
                  {{ selectedCostItemIds.length ? '取消全选' : '全选全部' }}
                </el-button>
                <el-button
                  :icon="Tickets"
                  type="success"
                  plain
                  :loading="costBulkSubmitting"
                  :disabled="costDbFeatureDisabled || costBulkSubmitting || selectedDraftCostItemCount === 0"
                  @click="bulkActivateCostItems"
                >
                  批量核定为启用
                </el-button>
                <el-button
                  :icon="Refresh"
                  type="warning"
                  plain
                  :loading="costBulkSubmitting"
                  :disabled="costDbFeatureDisabled || costBulkSubmitting || selectedActiveCostItemCount === 0"
                  @click="bulkRestoreCostItemsToDraft"
                >
                  批量恢复为待核定
                </el-button>
                <el-button
                  :icon="Delete"
                  type="danger"
                  plain
                  :loading="costBulkSubmitting"
                  :disabled="costDbFeatureDisabled || costBulkSubmitting || selectedArchivableCostItemCount === 0"
                  @click="bulkArchiveCostItems"
                >
                  批量归档
                </el-button>
              </div>
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

        <template v-else-if="routeName === 'dwgTrial'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">图纸识别</p>
              <h2>图纸识别</h2>
            </div>
            <div class="heading-actions">
              <el-upload
                :auto-upload="false"
                :show-file-list="true"
                :limit="10"
                multiple
                accept=".dwg"
                :on-change="handleDwgTrialFileChange"
                :on-remove="clearDwgTrialFile"
              >
                <el-button :icon="Document" plain>选择 DWG 图纸</el-button>
              </el-upload>
              <el-upload
                :auto-upload="false"
                :show-file-list="true"
                :limit="10"
                multiple
                accept=".pdf"
                :on-change="handleDwgTrialPdfFileChange"
                :on-remove="clearDwgTrialPdfFile"
              >
                <el-button :icon="DocumentChecked" plain>选择正式 PDF</el-button>
              </el-upload>
              <el-button
                type="primary"
                :icon="DataAnalysis"
                :loading="dwgTrialLoading"
                :disabled="!dwgTrialUploadFiles.length && !dwgTrialPdfUploadFiles.length"
                @click="convertDwgTrial"
              >
                {{
                  dwgTrialUploadFiles.length && dwgTrialPdfUploadFiles.length
                    ? '上传 DWG+PDF 联合识别'
                    : (dwgTrialPdfUploadFiles.length ? '上传 PDF 识图列项' : '上传 DWG 识别项目')
                }}
              </el-button>
              <el-button :icon="Refresh" plain :loading="dwgTrialLoading" @click="loadDwgTrialLatest">最近结果</el-button>
              <el-button
                type="success"
                :icon="Download"
                plain
                :disabled="!dwgTrialQuantityListFile"
                @click="downloadDwgTrialFile(dwgTrialQuantityListFile)"
              >
                下载四字段Excel
              </el-button>
              <el-button :icon="Delete" plain @click="resetDwgTrial">清空</el-button>
            </div>
          </div>

          <div v-if="false && dwgTrialResult" class="metric-grid">
            <div class="metric-card">
              <span>DWG 图纸</span>
              <strong>{{ dwgTrialSummary.dwg_file_count || 0 }}</strong>
              <small>DXF {{ dwgTrialSummary.dxf_file_count || 0 }}</small>
            </div>
            <div class="metric-card">
              <span>图纸线索</span>
              <strong>{{ dwgTrialSummary.source_signal_count || 0 }}</strong>
              <small>已匹配 {{ dwgTrialSummary.matched_signal_count || 0 }}</small>
            </div>
            <div class="metric-card">
              <span>识别项目</span>
              <strong>{{ dwgTrialSummary.recognized_project_count || dwgTrialProjectRows.length || 0 }}</strong>
              <small>标准候选 {{ dwgTrialSummary.item_row_count || 0 }} 条</small>
            </div>
            <div class="metric-card">
              <span>项目-CAD绑定</span>
              <strong>{{ dwgTrialSummary.project_geometry_binding_ready_count || 0 }}</strong>
              <small>待选择 {{ dwgTrialSummary.project_geometry_ambiguous_count || 0 }} / 未绑定 {{ dwgTrialSummary.project_geometry_unbound_count || 0 }}</small>
            </div>
            <div class="metric-card">
              <span>候选选择</span>
              <strong>{{ dwgTrialSelectedCount }}</strong>
              <small>采纳 {{ dwgTrialAdoptedCount }} 条 / 项目候选 {{ dwgTrialSummary.project_geometry_candidate_option_count || dwgTrialProjectGeometryCandidateRows.length || 0 }} 个</small>
            </div>
          </div>

          <el-alert
            v-if="!dwgTrialResult"
            class="dashboard-alert"
            type="info"
            show-icon
            :closable="false"
            title="可只上传正式 PDF 生成四字段列项候选；上传 DWG 可辅助识别图纸项目"
          ></el-alert>

          <section v-if="dwgTrialResult" class="dashboard-section">
            <div class="section-title">
              <el-icon><DocumentChecked /></el-icon>
              <span>识图四字段清单</span>
              <small>{{ dwgTrialQuantityListRows.length }} 项</small>
            </div>
            <el-table
              :data="dwgTrialQuantityListRows"
              row-key="__row_key"
              class="users-table"
              empty-text="暂无识别项目"
            >
              <el-table-column prop="项目名称" label="项目名称" min-width="220" show-overflow-tooltip />
              <el-table-column prop="项目特征" label="项目特征" min-width="420" show-overflow-tooltip />
              <el-table-column prop="单位" label="单位" width="100" />
              <el-table-column prop="工程量" label="工程量" width="160" show-overflow-tooltip />
            </el-table>
          </section>

          <section v-if="dwgTrialHasPdfEvidence" class="dashboard-section">
            <div class="section-title">
              <el-icon><DataAnalysis /></el-icon>
              <span>PDF证据链</span>
              <small>{{ dwgTrialPdfEvidenceSummary.dwg_pdf_match_status || '待匹配' }}</small>
            </div>
            <el-descriptions class="compact-descriptions" :column="4" border>
              <el-descriptions-item label="PDF页数">{{ dwgTrialPdfEvidenceSummary.pdf_page_count || 0 }}</el-descriptions-item>
              <el-descriptions-item label="高清PNG">{{ dwgTrialPdfEvidenceSummary.pdf_render_status || '-' }}</el-descriptions-item>
              <el-descriptions-item label="视觉证据">{{ dwgTrialPdfEvidenceSummary.pdf_visual_evidence_count || 0 }}</el-descriptions-item>
              <el-descriptions-item label="匹配分数">{{ dwgTrialPdfEvidenceSummary.dwg_pdf_match_score ?? '-' }}</el-descriptions-item>
              <el-descriptions-item label="证据合并">{{ dwgTrialPdfEvidenceSummary.dxf_pdf_fusion_status || '-' }}</el-descriptions-item>
              <el-descriptions-item label="融合关系">{{ dwgTrialPdfEvidenceSummary.dxf_pdf_fusion_link_count || 0 }}</el-descriptions-item>
              <el-descriptions-item label="R0-R9信号">{{ dwgTrialPdfEvidenceSummary.r0_r9_pdf_signal_count || 0 }}</el-descriptions-item>
              <el-descriptions-item label="分块tile">{{ dwgTrialPdfEvidenceSummary.pdf_tile_count || 0 }}</el-descriptions-item>
              <el-descriptions-item label="视觉模型">{{ dwgTrialPdfEvidenceSummary.pdf_llm_visual_status || '-' }}</el-descriptions-item>
            </el-descriptions>
            <div v-if="dwgTrialPdfPreviewRow?.preview_url" class="pdf-preview-panel">
              <el-image
                :src="dwgTrialPdfPreviewRow.preview_url"
                fit="contain"
                :preview-src-list="[dwgTrialPdfPreviewRow.preview_url]"
                preview-teleported
              />
              <small>{{ dwgTrialPdfPreviewRow.source_file }} 第 {{ dwgTrialPdfPreviewRow.page }} 页</small>
            </div>
            <el-table
              v-if="dwgTrialPdfVisualEvidenceRows.length"
              :data="dwgTrialPdfVisualEvidenceRows"
              row-key="evidence_id"
              class="users-table"
              empty-text="暂无PDF证据"
            >
              <el-table-column prop="evidence_role" label="角色" width="150" show-overflow-tooltip />
              <el-table-column prop="text" label="证据文本" min-width="360" show-overflow-tooltip />
              <el-table-column prop="source_file" label="PDF文件" min-width="220" show-overflow-tooltip />
              <el-table-column prop="page" label="页码" width="90" />
              <el-table-column prop="confidence" label="置信度" width="100" />
            </el-table>
            <el-table
              v-if="dwgTrialPdfFiles.length"
              :data="dwgTrialPdfFiles"
              row-key="filename"
              class="users-table"
              empty-text="暂无PDF输出文件"
            >
              <el-table-column prop="label" label="文件" min-width="220" show-overflow-tooltip />
              <el-table-column prop="filename" label="名称" min-width="280" show-overflow-tooltip />
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" plain :icon="Download" @click="downloadDwgTrialFile(row)">
                    下载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section v-if="false && dwgTrialProjectRows.length" class="dashboard-section">
            <div class="section-title">
              <el-icon><DocumentChecked /></el-icon>
              <span>图纸项目识别结果</span>
              <small>{{ dwgTrialProjectRows.length }} 项</small>
            </div>
            <el-table :data="dwgTrialProjectRows" row-key="识别项目编号" class="users-table" empty-text="暂无图纸项目">
              <el-table-column prop="识别项目编号" label="编号" width="130" />
              <el-table-column prop="图纸项目名称" label="图纸项目" min-width="220" show-overflow-tooltip />
              <el-table-column prop="项目名称" label="标准项目名称" min-width="180" show-overflow-tooltip />
              <el-table-column prop="项目特征" label="项目特征" min-width="320" show-overflow-tooltip />
              <el-table-column prop="单位" label="单位" width="80" />
              <el-table-column prop="工程量状态" label="工程量状态" min-width="220" show-overflow-tooltip />
              <el-table-column prop="来源类型" label="来源类型" min-width="150" show-overflow-tooltip />
              <el-table-column prop="来源文件" label="来源文件" min-width="220" show-overflow-tooltip />
              <el-table-column prop="识别证据" label="识别证据" min-width="300" show-overflow-tooltip />
            </el-table>
          </section>

          <section v-if="false && dwgTrialProjectGeometryBindingRows.length" class="dashboard-section">
            <div class="section-title">
              <el-icon><DataAnalysis /></el-icon>
              <span>项目与 CAD 区域绑定建议</span>
              <small>{{ dwgTrialProjectGeometryBindingRows.length }} 项</small>
            </div>
            <el-table
              :data="dwgTrialProjectGeometryBindingRows"
              row-key="识别项目编号"
              class="users-table"
              empty-text="暂无项目-CAD绑定建议"
            >
              <el-table-column prop="识别项目编号" label="编号" width="130" />
              <el-table-column prop="图纸项目名称" label="图纸项目" min-width="220" show-overflow-tooltip />
              <el-table-column prop="项目名称" label="标准项目名称" min-width="180" show-overflow-tooltip />
              <el-table-column prop="期望算量类型" label="算量类型" width="120">
                <template #default="{ row }">
                  <el-tag effect="plain">{{ dwgTrialQuantityKindLabel(row.期望算量类型) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="绑定状态" label="绑定状态" min-width="180" show-overflow-tooltip />
              <el-table-column prop="建议工程量" label="建议工程量" width="120" />
              <el-table-column prop="建议单位" label="单位" width="80" />
              <el-table-column prop="绑定置信度" label="置信度" width="120" />
              <el-table-column prop="候选数量" label="候选" width="90" />
              <el-table-column prop="推荐CAD候选编号" label="推荐候选" width="150" />
              <el-table-column prop="绑定说明" label="绑定说明" min-width="320" show-overflow-tooltip />
            </el-table>
          </section>

          <section v-if="false && dwgTrialItemRows.length" class="dashboard-section">
            <div class="section-title">
              <el-icon><DataAnalysis /></el-icon>
              <span>标准列项与 CAD 候选明细</span>
              <small>{{ dwgTrialItemRows.length }} 条</small>
            </div>
            <el-table :data="dwgTrialItemRows" row-key="序号" class="users-table" empty-text="暂无列项候选">
              <el-table-column type="expand" width="52">
                <template #default="{ row }">
                  <div class="dwg-candidate-panel">
                    <div class="dwg-candidate-heading">
                      <div>
                        <strong>{{ row.项目名称 }}</strong>
                        <span>{{ row.图纸识别名称 || '暂无图纸线索' }}</span>
                      </div>
                      <div class="dwg-candidate-tags">
                        <el-tag effect="plain">{{ dwgTrialRowCandidateOptions(row).length }} 个CAD候选</el-tag>
                        <el-tag
                          v-if="dwgTrialRowDecision(row)"
                          :type="dwgTrialDecisionTagType(dwgTrialRowDecision(row).action)"
                          effect="plain"
                        >
                          {{ dwgTrialRowDecision(row).action }} {{ dwgTrialRowDecision(row).suggestion_key }}
                        </el-tag>
                        <el-button
                          v-if="dwgTrialRowDecision(row)"
                          size="small"
                          link
                          type="info"
                          @click="clearDwgTrialCandidateDecision(row)"
                        >
                          清除
                        </el-button>
                      </div>
                    </div>
                    <el-alert
                      v-if="!dwgTrialRowCandidateOptions(row).length"
                      type="info"
                      show-icon
                      :closable="false"
                      title="当前列项没有可选择的 CAD 候选量"
                    ></el-alert>
                    <el-table
                      v-else
                      :data="dwgTrialRowCandidateOptions(row)"
                      size="small"
                      class="users-table dwg-candidate-table"
                      empty-text="暂无候选"
                    >
                      <el-table-column prop="建议编号" label="建议编号" min-width="150" show-overflow-tooltip />
                      <el-table-column prop="建议工程量" label="建议量" width="110" />
                      <el-table-column prop="建议单位" label="单位" width="80" />
                      <el-table-column prop="推荐动作" label="推荐动作" width="120" />
                      <el-table-column prop="绑定置信度" label="置信度" width="110" />
                      <el-table-column prop="CAD来源" label="CAD来源" min-width="220" show-overflow-tooltip />
                      <el-table-column prop="CAD公式" label="CAD公式" min-width="240" show-overflow-tooltip />
                      <el-table-column prop="CAD来源图元行号" label="CAD行号" min-width="140" show-overflow-tooltip />
                      <el-table-column prop="推荐原因" label="推荐原因" min-width="260" show-overflow-tooltip />
                      <el-table-column label="处理" width="230" fixed="right">
                        <template #default="optionScope">
                          <el-button
                            size="small"
                            type="primary"
                            plain
                            :icon="Select"
                            @click="setDwgTrialCandidateDecision(row, optionScope.row, '采纳')"
                          >
                            采纳
                          </el-button>
                          <el-button
                            size="small"
                            plain
                            :icon="Delete"
                            @click="setDwgTrialCandidateDecision(row, optionScope.row, '不采纳')"
                          >
                            不采纳
                          </el-button>
                          <el-button
                            size="small"
                            type="warning"
                            plain
                            :icon="Warning"
                            @click="setDwgTrialCandidateDecision(row, optionScope.row, '有问题')"
                          >
                            有问题
                          </el-button>
                        </template>
                      </el-table-column>
                    </el-table>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="标准项目编码" label="标准编码" width="130" />
              <el-table-column prop="项目名称" label="项目名称" min-width="180" show-overflow-tooltip />
              <el-table-column prop="单位" label="单位" width="130" show-overflow-tooltip />
              <el-table-column prop="匹配置信度" label="置信度" width="100" />
              <el-table-column prop="CAD候选数量" label="候选数" width="90" />
              <el-table-column prop="图纸识别名称" label="图纸线索" min-width="220" show-overflow-tooltip />
              <el-table-column prop="逐条绑定状态" label="绑定状态" min-width="180" show-overflow-tooltip />
              <el-table-column prop="绑定置信度" label="绑定置信度" width="120" show-overflow-tooltip />
              <el-table-column prop="系统建议工程量" label="系统建议工程量" min-width="180" show-overflow-tooltip />
              <el-table-column prop="建议量状态" label="建议量状态" min-width="220" show-overflow-tooltip />
              <el-table-column prop="工程量状态" label="工程量状态" width="180" show-overflow-tooltip />
              <el-table-column prop="绑定说明" label="绑定说明" min-width="260" show-overflow-tooltip />
            </el-table>
          </section>

          <section v-if="false && dwgTrialQuantityTraceRows.length" class="dashboard-section">
            <div class="section-title">
              <el-icon><Histogram /></el-icon>
              <span>工程量建议 trace</span>
              <small>{{ dwgTrialQuantityTraceRows.length }} 条</small>
            </div>
            <el-table :data="dwgTrialQuantityTraceRows" row-key="建议编号" class="users-table" empty-text="暂无工程量建议">
              <el-table-column prop="标准项目编码" label="标准编码" width="130" />
              <el-table-column prop="标准项目名称" label="项目名称" min-width="180" show-overflow-tooltip />
              <el-table-column prop="建议工程量" label="建议工程量" width="130" />
              <el-table-column prop="建议单位" label="单位" width="90" />
              <el-table-column prop="是否可复核" label="可复核" width="90" />
              <el-table-column prop="trace状态" label="trace状态" min-width="240" show-overflow-tooltip />
              <el-table-column prop="CAD公式" label="CAD公式" min-width="260" show-overflow-tooltip />
              <el-table-column prop="未解决事项" label="未解决事项" min-width="260" show-overflow-tooltip />
            </el-table>
          </section>

          <section v-if="false && dwgTrialResult" class="dashboard-section">
            <div class="section-title">
              <el-icon><Download /></el-icon>
              <span>输出文件</span>
              <small>{{ dwgTrialResult.generated_at || '-' }}</small>
            </div>
            <el-table :data="dwgTrialFiles" row-key="filename" class="users-table" empty-text="暂无输出文件">
              <el-table-column prop="label" label="文件" width="180" />
              <el-table-column prop="filename" label="名称" min-width="320" show-overflow-tooltip />
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" plain :icon="Download" @click="downloadDwgTrialFile(row)">
                    下载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section v-if="false && dwgTrialFinalizationResult" class="dashboard-section">
            <div class="section-title">
              <el-icon><Download /></el-icon>
              <span>最终清单结果</span>
              <small>可导出 {{ dwgTrialFinalizationSummary.final_ready_count || 0 }} 行</small>
            </div>
            <el-alert
              v-if="!dwgTrialFinalizationResult.has_final_excel"
              class="dashboard-alert"
              type="warning"
              show-icon
              :closable="false"
              title="本次采纳结果未通过最终四字段校验，请查看问题文件或识别提示"
            ></el-alert>
            <el-table :data="dwgTrialFinalizationFiles" row-key="filename" class="users-table" empty-text="暂无最终清单文件">
              <el-table-column prop="label" label="文件" width="190" />
              <el-table-column prop="filename" label="名称" min-width="320" show-overflow-tooltip />
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" plain :icon="Download" @click="downloadDwgTrialFile(row)">
                    下载
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section v-if="false && dwgTrialFinalizationIssues.length" class="dashboard-section">
            <div class="section-title">
              <el-icon><Warning /></el-icon>
              <span>最终清单校验问题</span>
              <small>{{ dwgTrialFinalizationIssues.length }} 条</small>
            </div>
            <el-table :data="dwgTrialFinalizationIssues" row-key="问题说明" class="users-table" empty-text="暂无问题">
              <el-table-column prop="列项序号" label="列项" width="110" />
              <el-table-column prop="建议编号" label="建议编号" min-width="170" show-overflow-tooltip />
              <el-table-column prop="问题说明" label="问题说明" min-width="320" show-overflow-tooltip />
              <el-table-column prop="处理建议" label="处理建议" min-width="280" show-overflow-tooltip />
            </el-table>
          </section>

          <section v-if="false && dwgTrialIssues.length" class="dashboard-section">
            <div class="section-title">
              <el-icon><Warning /></el-icon>
              <span>识别提示</span>
              <small>{{ dwgTrialIssues.length }} 条</small>
            </div>
            <el-table :data="dwgTrialIssues" row-key="说明" class="users-table" empty-text="暂无识别提示">
              <el-table-column prop="级别" label="级别" width="100" />
              <el-table-column prop="说明" label="说明" min-width="420" show-overflow-tooltip />
            </el-table>
          </section>
        </template>

        <template v-else-if="routeName === 'requirementStandardization'">
          <div class="content-heading">
            <div>
              <p class="eyebrow">需求单处理</p>
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
                :title="`已创建报价任务：${displayQuoteJobNumber(requirementQuoteJob)}`"
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
              <p class="eyebrow">账号与权限</p>
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
        <section class="drawer-section" v-if="canViewAgentCenter">
          <div class="section-title">
            <el-icon><DataAnalysis /></el-icon>
            <span>报价后审计</span>
            <small>已下发报价可手动生成审计记录</small>
          </div>
          <el-button
            type="primary"
            plain
            :icon="DataAnalysis"
            :disabled="!canManualAuditQuoteJob(quoteJobDrawer.job)"
            :loading="agentCenterLoading"
            @click="manualAuditQuoteJob(quoteJobDrawer.job)"
          >
            手动审计此报价
          </el-button>
        </section>
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

    <el-drawer v-model="enterpriseQuotaItemDrawer.visible" size="760px" title="企业定额主项详情">
      <div v-if="enterpriseQuotaItemDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="enterpriseQuotaItemDrawer.item">
        <div class="detail-grid">
          <div>
            <small>定额编号</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.quota_code || '-' }}</strong>
          </div>
          <div>
            <small>项目名称</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.item_name || '-' }}</strong>
          </div>
          <div>
            <small>分部</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.section_name || '-' }}</strong>
          </div>
          <div>
            <small>单位</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.unit || '-' }}</strong>
          </div>
          <div>
            <small>工程量</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.quantity ?? '-' }}</strong>
          </div>
          <div>
            <small>综合单价</small>
            <strong>{{ formatPrice(enterpriseQuotaItemDrawer.item.unit_price) }}</strong>
          </div>
          <div>
            <small>版本</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.active_version?.version_code || '-' }}</strong>
          </div>
          <div>
            <small>组成明细</small>
            <strong>{{ enterpriseQuotaItemDrawer.item.component_count || 0 }} 条</strong>
          </div>
        </div>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>工作内容</span>
          </div>
          <p class="detail-text">{{ enterpriseQuotaItemDrawer.item.work_content || '-' }}</p>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Histogram /></el-icon>
            <span>费用拆分</span>
          </div>
          <div class="detail-grid">
            <div>
              <small>人工费</small>
              <strong>{{ formatPrice(enterpriseQuotaItemDrawer.item.labor_fee) }}</strong>
            </div>
            <div>
              <small>主材费</small>
              <strong>{{ formatPrice(enterpriseQuotaItemDrawer.item.main_material_fee) }}</strong>
            </div>
            <div>
              <small>辅材费</small>
              <strong>{{ formatPrice(enterpriseQuotaItemDrawer.item.auxiliary_material_fee) }}</strong>
            </div>
            <div>
              <small>机械费</small>
              <strong>{{ formatPrice(enterpriseQuotaItemDrawer.item.machinery_fee) }}</strong>
            </div>
          </div>
        </section>
        <section class="drawer-section">
          <div class="section-title">
            <el-icon><Tickets /></el-icon>
            <span>组成明细</span>
          </div>
          <el-table
            :data="enterpriseQuotaItemDrawer.item.components || []"
            class="users-table"
            size="small"
            empty-text="暂无组成明细"
          >
            <el-table-column prop="component_type" label="组成类型" width="110" show-overflow-tooltip />
            <el-table-column label="资源" min-width="210" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ row.resource_name || '-' }}</strong>
                  <small>{{ row.resource_code || '-' }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="unit" label="单位" width="72" />
            <el-table-column label="含量" width="96">
              <template #default="{ row }">{{ row.quantity ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="单价" width="110">
              <template #default="{ row }">{{ formatPrice(row.unit_price) }}</template>
            </el-table-column>
            <el-table-column label="金额" width="110">
              <template #default="{ row }">{{ formatPrice(row.amount) }}</template>
            </el-table-column>
          </el-table>
        </section>
      </template>
      <el-empty v-else description="暂无企业定额主项详情" />
    </el-drawer>

    <el-drawer v-model="costLineageDrawer.visible" size="1100px" title="成本库状态与流向">
      <el-tabs v-model="costLineageDrawer.activeTab" class="dashboard-tabs" @tab-click="handleCostLineageTabClick">
        <el-tab-pane label="总览" name="summary">
          <div v-loading="costLineageDrawer.summaryLoading">
            <div class="detail-grid lineage-summary-grid">
              <div>
                <small>待核定</small>
                <strong>{{ costLineageSummary.by_status?.draft || 0 }}</strong>
              </div>
              <div>
                <small>已启用</small>
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
                <small>已启用且已引用</small>
                <strong>{{ costLineageSummary.active_quote_used_count || 0 }}</strong>
              </div>
              <div>
                <small>已用于报价参考</small>
                <strong>{{ costLineageSummary.active_rag_scope_count || 0 }}</strong>
              </div>
              <div>
                <small>最近成本参考更新</small>
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
        <el-tab-pane label="新增待核定" name="draft"></el-tab-pane>
        <el-tab-pane label="已启用记录" name="active"></el-tab-pane>
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
                    <small>{{ costSourceLabel(row.source) }} · {{ row.origin?.quote_job_number || row.origin?.quote_job_id || row.origin?.created_by_username || '-' }}</small>
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
                  <strong>{{ costLineageDrawer.detail.origin?.quote_job_number || costLineageDrawer.detail.origin?.quote_job_id || '-' }}</strong>
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
                  <span>{{ usage.quote_job_number || usage.quote_job_id || '-' }} · 历史 #{{ usage.quote_history_id || '-' }}</span>
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

    <el-dialog v-model="costRagSyncDialog.visible" title="成本参考更新记录" width="920px">
      <div class="dialog-toolbar">
        <span>记录每次成本参考更新的时间、数量和结果</span>
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

    <el-drawer v-model="quoteJobDrawer.visible" size="min(1360px, 96vw)" title="报价任务详情">
      <div v-if="quoteJobDrawer.loading" class="center-state">
        <el-icon class="spin"><Refresh /></el-icon>
        <span>加载中</span>
      </div>
      <template v-else-if="quoteJobDrawer.job">
        <div class="detail-grid">
          <div>
            <small>任务号</small>
            <strong>{{ displayQuoteJobNumber(quoteJobDrawer.job) }}</strong>
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
            <el-table-column label="确认价/合计" width="160">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(reviewDisplayUnitPrice(row)) }}</strong>
                  <small>合计 {{ formatPrice(reviewDisplayTotalPrice(row)) }}</small>
                  <small v-if="reviewPriceSourceLabel(row)">{{ reviewPriceSourceLabel(row) }}</small>
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
            <el-table-column label="AI 单价" width="88">
              <template #default="{ row }">{{ formatPrice(row.ai_unit_price) }}</template>
            </el-table-column>
            <el-table-column label="行合计" width="112">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.line_total_price ?? row.ai_total_price) }}</strong>
                  <small>{{ row.line_total_source_label || totalSourceLabel(row.line_total_source) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="成本参考" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.reference_price) }}</strong>
                  <small>参考合计 {{ formatPrice(row.reference_total) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="整单合计" width="132">
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ formatPrice(row.quote_total_price) }}</strong>
                  <small>{{ row.quote_total_source_label || totalSourceLabel(row.quote_total_source) }}</small>
                  <small>参考合计 {{ formatPrice(row.quote_reference_total_price) }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="偏差" width="92">
              <template #default="{ row }">
                <span>{{ formatPrice(row.price_delta) }}</span>
                <small class="muted-inline">{{ formatRate(row.price_delta_rate) }}</small>
              </template>
            </el-table-column>
            <el-table-column label="AI来源" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">
                <div class="operation-client">
                  <strong>{{ row.ai_price_source_label || '-' }}</strong>
                  <small>{{ row.ai_price_source_reason || '-' }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="依据" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.comparison || row.match_reason || '-' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="96">
              <template #default="{ row }">
                <el-button
                  size="small"
                  :icon="Document"
                  plain
                  :disabled="!costEvidenceOpenId(row)"
                  @click="openCostEvidenceItem(row)"
                >
                  {{ costEvidenceButtonLabel(row) }}
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

    <el-dialog v-model="projectDialog.visible" title="新建项目" width="560px">
      <el-form label-position="top" :model="projectDialog.form">
        <el-form-item label="项目名称">
          <el-input v-model="projectDialog.form.name" maxlength="120" />
        </el-form-item>
        <el-form-item label="客户名称">
          <el-input v-model="projectDialog.form.client_name" maxlength="80" />
        </el-form-item>
        <el-form-item label="项目经理">
          <el-select v-model="projectDialog.form.project_manager_id" class="full-width" filterable placeholder="请选择项目经理">
            <el-option v-for="user in projectUserOptions" :key="user.id" :label="user.username" :value="user.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属部门">
          <el-input v-model="projectDialog.form.owner_department" maxlength="80" />
        </el-form-item>
        <el-form-item label="项目地址">
          <el-input v-model="projectDialog.form.address" maxlength="160" />
        </el-form-item>
        <div class="ledger-form-grid">
          <el-form-item label="计划开始">
            <el-date-picker v-model="projectDialog.form.planned_start_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
          </el-form-item>
          <el-form-item label="计划完成">
            <el-date-picker v-model="projectDialog.form.planned_finish_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
          </el-form-item>
        </div>
        <el-form-item label="项目说明">
          <el-input v-model="projectDialog.form.description" type="textarea" :rows="3" maxlength="800" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createProject">创建项目</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="projectTrialDialog.visible" title="快速创建项目" width="560px">
      <el-form label-position="top" :model="projectTrialDialog.form">
        <el-form-item label="项目名称">
          <el-input v-model="projectTrialDialog.form.name" maxlength="120" />
        </el-form-item>
        <el-form-item label="客户名称">
          <el-input v-model="projectTrialDialog.form.client_name" maxlength="80" />
        </el-form-item>
        <el-form-item label="负责部门">
          <el-input v-model="projectTrialDialog.form.owner_department" maxlength="80" />
        </el-form-item>
        <el-form-item label="项目地址">
          <el-input v-model="projectTrialDialog.form.address" maxlength="160" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="计划开始">
              <el-date-picker v-model="projectTrialDialog.form.planned_start_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="计划完成">
              <el-date-picker v-model="projectTrialDialog.form.planned_finish_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="projectTrialDialog.form.description" type="textarea" :rows="3" maxlength="800" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectTrialDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createProjectTrial">创建项目</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="projectEpcDialog.visible" title="旗胜EPC流程模板" width="620px">
      <el-form label-position="top" :model="projectEpcDialog.form">
        <el-form-item label="项目名称">
          <el-input v-model="projectEpcDialog.form.name" maxlength="120" />
        </el-form-item>
        <el-form-item label="客户名称">
          <el-input v-model="projectEpcDialog.form.client_name" maxlength="80" />
        </el-form-item>
        <div class="ledger-form-grid">
          <el-form-item label="负责部门">
            <el-input v-model="projectEpcDialog.form.owner_department" maxlength="80" />
          </el-form-item>
          <el-form-item label="模板模式">
            <el-radio-group v-model="projectEpcDialog.form.mode">
              <el-radio-button label="compact">精简节点</el-radio-button>
              <el-radio-button label="full">完整82节点</el-radio-button>
            </el-radio-group>
          </el-form-item>
        </div>
        <el-form-item label="项目地址">
          <el-input v-model="projectEpcDialog.form.address" maxlength="160" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="计划开始">
              <el-date-picker v-model="projectEpcDialog.form.planned_start_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="计划完成">
              <el-date-picker v-model="projectEpcDialog.form.planned_finish_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input v-model="projectEpcDialog.form.description" type="textarea" :rows="3" maxlength="800" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectEpcDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createProjectEpc">创建EPC项目</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="projectEvidenceDrawer.visible" size="720px" :title="`成果证据 - ${projectEvidenceDrawer.task?.title || ''}`">
      <div class="evidence-summary">
        <div class="section-title">
          <el-icon><Document /></el-icon>
          <span>成果要求</span>
        </div>
        <p>{{ projectEvidenceDrawer.summary.requirement || projectEvidenceDrawer.task?.evidence_requirement || '未设置成果要求' }}</p>
      </div>

      <el-table
        :data="projectEvidenceDrawer.items"
        row-key="id"
        class="users-table"
        empty-text="暂无成果证据"
        v-loading="projectEvidenceDrawer.loading"
      >
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="projectEvidenceTypeTag(row.evidence_type)" effect="plain">{{ projectEvidenceTypeLabel(row.evidence_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="成果" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="operation-client">
              <strong>{{ row.title }}</strong>
              <small>
                {{ row.file_original_filename || row.external_url || row.description || '-' }}
                <template v-if="row.file_size_bytes"> · {{ formatFileSize(row.file_size_bytes) }}</template>
              </small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="登记人" width="110">
          <template #default="{ row }">{{ row.created_by_username || '-' }}</template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button v-if="row.evidence_type !== 'text'" size="small" plain @click="openProjectEvidence(row)">打开</el-button>
              <el-button size="small" type="danger" plain @click="removeProjectEvidence(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <el-divider />

      <el-form label-position="top" :model="projectEvidenceDrawer.form">
        <div class="ledger-form-grid">
          <el-form-item label="证据类型">
            <el-select v-model="projectEvidenceDrawer.form.evidence_type" class="full-width" @change="resetProjectEvidencePayload">
              <el-option v-for="option in projectEvidenceTypeOptions" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="成果标题">
            <el-input v-model="projectEvidenceDrawer.form.title" maxlength="120" />
          </el-form-item>
        </div>
        <el-form-item v-if="projectEvidenceDrawer.form.evidence_type === 'file'" label="上传文件">
          <el-upload
            class="full-width"
            action="#"
            :auto-upload="false"
            :limit="1"
            :on-change="handleProjectEvidenceFileChange"
            :on-remove="clearProjectEvidenceFile"
          >
            <el-button plain :icon="Upload">选择文件</el-button>
          </el-upload>
        </el-form-item>
        <template v-if="projectEvidenceDrawer.form.evidence_type === 'link'">
          <div class="ledger-form-grid">
            <el-form-item label="外部链接">
              <el-input v-model="projectEvidenceDrawer.form.external_url" maxlength="500" />
            </el-form-item>
            <el-form-item label="来源">
              <el-select v-model="projectEvidenceDrawer.form.external_provider" class="full-width">
                <el-option label="钉钉" value="dingtalk" />
                <el-option label="企微" value="wecom" />
                <el-option label="其他" value="other" />
              </el-select>
            </el-form-item>
          </div>
        </template>
        <el-form-item label="说明">
          <el-input v-model="projectEvidenceDrawer.form.description" type="textarea" :rows="3" maxlength="800" show-word-limit />
        </el-form-item>
        <div class="dialog-actions">
          <el-button :loading="projectEvidenceDrawer.loading" @click="loadProjectTaskEvidences">刷新</el-button>
          <el-button type="primary" :loading="state.submitting" @click="createProjectEvidence">新增证据</el-button>
        </div>
      </el-form>
    </el-drawer>

    <el-dialog v-model="projectTaskDialog.visible" title="新建项目任务" width="560px">
      <el-form label-position="top" :model="projectTaskDialog.form">
        <el-form-item label="所属阶段">
          <el-select v-model="projectTaskDialog.form.stage_id" class="full-width" filterable placeholder="请选择阶段">
            <el-option v-for="stage in projectDetail?.stages || []" :key="stage.id" :label="stage.stage_name" :value="stage.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="任务标题">
          <el-input v-model="projectTaskDialog.form.title" maxlength="120" />
        </el-form-item>
        <el-form-item label="负责人">
          <el-select v-model="projectTaskDialog.form.owner_user_id" class="full-width" filterable placeholder="请选择负责人">
            <el-option v-for="user in projectUserOptions" :key="user.id" :label="user.username" :value="user.id" />
          </el-select>
        </el-form-item>
        <div class="ledger-form-grid">
          <el-form-item label="责任岗位">
            <el-input v-model="projectTaskDialog.form.owner_role" maxlength="64" />
          </el-form-item>
          <el-form-item label="优先级">
            <el-select v-model="projectTaskDialog.form.priority" class="full-width">
              <el-option v-for="option in projectPriorityOptions" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
          </el-form-item>
        </div>
        <div class="ledger-form-grid">
          <el-form-item label="计划开始">
            <el-date-picker v-model="projectTaskDialog.form.planned_start_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
          </el-form-item>
          <el-form-item label="截止时间">
            <el-date-picker v-model="projectTaskDialog.form.due_at" class="full-width" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
          </el-form-item>
        </div>
        <el-form-item label="下一步动作">
          <el-input v-model="projectTaskDialog.form.next_action" maxlength="300" />
        </el-form-item>
        <el-form-item label="任务说明">
          <el-input v-model="projectTaskDialog.form.description" type="textarea" :rows="3" maxlength="800" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectTaskDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="state.submitting" @click="createProjectTask">创建任务</el-button>
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
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import axios from 'axios'
import BudgetProjects from './BudgetProjects.vue'
import AccountQuotaLibrary from './AccountQuotaLibrary.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  ArrowRight,
  Clock,
  DataAnalysis,
  Delete,
  Document,
  DocumentChecked,
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
  Upload,
  User,
  Warning,
} from '@element-plus/icons-vue'

const TOKEN_KEY = 'ai_token'
const USER_INFO_KEY = 'app_user_info'
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
      localStorage.removeItem(USER_INFO_KEY)
    }
    return Promise.reject(error)
  },
)

const roleOptions = [
  { value: 'system_admin', label: '系统管理员', hint: '权限与系统配置' },
  { value: 'admin', label: '管理人员', hint: '报价与知识库管理' },
  { value: 'quote_operator', label: '报价复核人员', hint: '报价运营只读复核' },
  { value: 'quote_user', label: '报价专员', hint: '报价工作台与需求单标准化' },
  { value: 'cost_viewer', label: '成本库查看人员', hint: '完整成本库只读' },
  { value: 'cost_editor', label: '成本库维护人员', hint: '维护成本库待核定条目' },
  { value: 'cost_approver', label: '成本库核定人员', hint: '启用或归档成本价' },
  { value: 'cost_exporter', label: '成本库导出人员', hint: '导出成本数据' },
  { value: 'enterprise_profile_viewer', label: '企业资料查看人员', hint: '查看企业资料库' },
  { value: 'enterprise_profile_editor', label: '企业资料维护人员', hint: '维护企业资料草稿' },
  { value: 'enterprise_profile_approver', label: '企业资料核定人员', hint: '启用或归档企业资料' },
  { value: 'project_viewer', label: '项目查看人员', hint: '查看参与项目' },
  { value: 'project_member', label: 'project_member', hint: '更新本人项目任务' },
  { value: 'project_manager', label: 'project_manager', hint: '管理项目进度' },
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

const enterpriseProfileCategoryOptions = [
  { value: 'basic_info', label: '企业基本信息' },
  { value: 'certificate', label: '证照证书' },
  { value: 'qualification', label: '企业资质' },
  { value: 'personnel', label: '人员资料' },
  { value: 'project_performance', label: '项目业绩' },
  { value: 'technical_solution', label: '技术方案片段' },
  { value: 'attachment_asset', label: '附件素材' },
  { value: 'commitment_template', label: '承诺函模板' },
  { value: 'other', label: '其他' },
]

const enterpriseProfileMaterialFormOptions = [
  { value: 'text', label: '文本形式' },
  { value: 'attachment', label: '附件形式' },
]

const enterpriseProfileStatusOptions = [
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '已启用' },
  { value: 'archived', label: '已归档' },
]

const projectStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'planning', label: '筹备中' },
  { value: 'active', label: '进行中' },
  { value: 'paused', label: '已暂停' },
  { value: 'completed', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]

const projectRiskOptions = [
  { value: '', label: '全部风险' },
  { value: 'normal', label: '正常' },
  { value: 'warning', label: '临期' },
  { value: 'delayed', label: '延期' },
  { value: 'blocked', label: '阻塞' },
]

const projectTaskStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'todo', label: '未开始' },
  { value: 'started', label: '已开始' },
  { value: 'progressing', label: '进行中' },
  { value: 'submitted', label: '待确认' },
  { value: 'done', label: '已完成' },
  { value: 'blocked', label: '已阻塞' },
  { value: 'cancelled', label: '已取消' },
]

const projectEvidenceTypeOptions = [
  { value: 'text', label: '文字说明' },
  { value: 'link', label: '外部链接' },
  { value: 'file', label: '上传文件' },
]

const projectPriorityOptions = [
  { value: 'low', label: '低' },
  { value: 'normal', label: '普通' },
  { value: 'high', label: '高' },
  { value: 'urgent', label: '紧急' },
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

const biddingStatusOptions = [
  { value: '', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'files_uploaded', label: '已上传资料' },
  { value: 'parsed', label: '已解析' },
  { value: 'reviewing', label: '复核中' },
  { value: 'archived', label: '已归档' },
]

const biddingFileTypeOptions = [
  { value: 'tender_document', label: '招标文件' },
  { value: 'bill_of_quantities', label: '工程量清单' },
  { value: 'drawing', label: '图纸/图纸说明' },
  { value: 'contract', label: '合同范本' },
  { value: 'addendum', label: '补遗文件' },
  { value: 'clarification', label: '答疑文件' },
  { value: 'brand_table', label: '材料品牌表' },
  { value: 'other', label: '其他资料' },
]

const biddingFileFormatContentTypeOptions = [
  { value: 'fixed_form', label: '固定表单' },
  { value: 'draft_section', label: '正文章节' },
  { value: 'attachment_proof', label: '附件证明' },
  { value: 'qualification_attachment', label: '资格附件' },
  { value: 'pricing_table', label: '报价表' },
]

const biddingFileFormatGenerationOptions = [
  { value: 'generate_draft', label: '生成正文' },
  { value: 'from_cost_quote', label: '报价链路' },
  { value: 'manual_upload', label: '人工上传' },
  { value: 'manual_fill', label: '人工填表' },
]

const biddingFileFormatOwnerOptions = [
  { value: '经营', label: '经营' },
  { value: '预算', label: '预算' },
  { value: '技术', label: '技术' },
  { value: '法务', label: '法务' },
]

const biddingLlmDecisionOptions = [
  { value: 'keep', label: '保留' },
  { value: 'rename', label: '建议改名/改类' },
  { value: 'split', label: '建议拆分' },
  { value: 'ignore', label: '建议忽略' },
  { value: 'manual_review', label: '转人工判断' },
]

const biddingBusinessObjectActionOptions = [
  { value: 'bid_compliance', label: '投标合规' },
  { value: 'qualification_response', label: '资格响应' },
  { value: 'document_response', label: '文件响应' },
  { value: 'quote_allowance', label: '报价预留' },
  { value: 'clarification', label: '转答疑' },
  { value: 'legal_review', label: '合同/法务复核' },
  { value: 'delivery_planning', label: '履约计划' },
  { value: 'reference', label: '资料参考' },
]

const biddingResponseActionOptions = [
  { value: 'direct_response', label: '直接响应' },
  { value: 'qualification_material', label: '资格材料准备' },
  { value: 'document_preparation', label: '文件编制' },
  { value: 'clarification', label: '转答疑' },
  { value: 'quote_allowance', label: '报价预留' },
  { value: 'legal_review', label: '法务复核' },
  { value: 'reference', label: '仅参考' },
]

const biddingResponseStatusOptions = [
  { value: 'pending', label: '待处理' },
  { value: 'confirmed', label: '已确认' },
  { value: 'to_clarify', label: '需答疑' },
  { value: 'to_quote_allowance', label: '报价预留' },
  { value: 'legal_review', label: '法务复核' },
  { value: 'done', label: '已完成' },
  { value: 'ignored', label: '已忽略' },
]

const biddingResponseReviewRoleOptions = [
  { value: 'all', label: '全部' },
  { value: 'business', label: '经营' },
  { value: 'budget', label: '预算' },
  { value: 'technical', label: '技术' },
  { value: 'legal', label: '法务' },
]

const loginForm = reactive({ username: '', password: '' })
const session = reactive({ user: null })
const users = ref([])
const roleEvents = ref([])
const businessDashboard = ref(null)
const quoteDashboard = ref(null)
const responseDashboard = ref(null)
const executionDashboard = ref(null)
const projectDashboard = ref(null)
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
const projects = ref([])
const projectTotal = ref(0)
const projectPage = ref(1)
const projectPageSize = 20
const projectDetail = ref(null)
const projectTaskEvidenceFilter = ref('all')
const projectEvents = ref([])
const myProjectTasks = ref([])
const myProjectTaskTotal = ref(0)
const myProjectTaskPage = ref(1)
const myProjectTaskPageSize = 20
const projectUsers = ref([])
const meetings = ref([])
const meetingTotal = ref(0)
const meetingPage = ref(1)
const meetingPageSize = 20
const businessLedgers = ref([])
const businessLedgerTotal = ref(0)
const businessLedgerPage = ref(1)
const businessLedgerPageSize = 20
const businessLedgerLoading = ref(false)
const biddingProjects = ref([])
const biddingProjectTotal = ref(0)
const biddingProjectPage = ref(1)
const biddingProjectPageSize = 20
const biddingLoading = ref(false)
const biddingFeatureDisabled = ref(false)
const biddingFiles = ref([])
const biddingParseRuns = ref([])
const biddingTenderAnalysis = ref(null)
const biddingTenderAnalysisLoading = ref(false)
const biddingTenderAnalysisExporting = ref(false)
const biddingTenderAnalysisTab = ref('summary')
const biddingAnalysisTabsRef = ref(null)
const biddingTenderReviewWorkbenchExpanded = ref(true)
const biddingImportantInfoExpandedKeys = ref([])
const biddingTenderScoringExpandedKeys = ref([])
const biddingRiskClause = ref(null)
const biddingRiskClauseLoading = ref(false)
const biddingRiskClauseAnalyzing = ref(false)
const biddingRiskClauseExporting = ref(false)
const biddingImportantInfoProgress = reactive({
  visible: false,
  percentage: 0,
  status: 'idle',
  stage: '',
  detail: '',
})
let biddingImportantInfoProgressTimer = null
const biddingRiskClauseProgress = reactive({
  visible: false,
  percentage: 0,
  status: 'idle',
  stage: '',
  detail: '',
})
let biddingRiskClauseProgressTimer = null
const biddingBusinessObjectCollapse = ref([])
const biddingBusinessObjects = ref([])
const biddingBusinessObjectsTotal = ref(0)
const biddingBusinessObjectsSummary = ref({})
const biddingResponseItems = ref([])
const biddingResponseItemsTotal = ref(0)
const biddingResponseMatrixSummary = ref({})
const biddingResponseReviewRole = ref('all')
const biddingResponseMatrixGenerating = ref(false)
const biddingResponseItemUpdating = ref(false)
const biddingResponseExpandedKeys = ref([])
const biddingFileFormatPlan = ref(null)
const biddingFileFormatLoading = ref(false)
const biddingFileFormatGenerating = ref(false)
const biddingFileFormatConfirming = ref(false)
const biddingFileFormatPendingEvents = ref([])
const biddingFileFormatItemDialog = reactive({
  visible: false,
  package_key: '',
  item_title: '',
  content_type: 'draft_section',
  owner_role: '技术',
  generation_strategy: 'generate_draft',
  requires_signature: false,
  requires_attachment: false,
})
const biddingFileFormatSummary = computed(() => biddingFileFormatPlan.value?.summary || {})
const biddingFileFormatStructure = computed(() => biddingFileFormatPlan.value?.structure || {})
const biddingFileFormatPackages = computed(() => biddingFileFormatStructure.value?.packages || [])
const biddingFileFormatWarnings = computed(() => biddingFileFormatPlan.value?.warnings || [])
const biddingFileFormatPackagingRequirements = computed(() => biddingFileFormatStructure.value?.packaging_requirements || [])
const biddingFileFormatRows = computed(() => {
  const rows = []
  for (const pkg of biddingFileFormatPackages.value) {
    for (const item of pkg.items || []) {
      rows.push({
        ...item,
        package_title: pkg.package_title,
        package_key: pkg.package_key,
      })
    }
  }
  return rows
})
const biddingFileFormatAuditEvents = computed(() => {
  const pending = biddingFileFormatPendingEvents.value.map((event) => ({
    ...event,
    event_uuid: event.event_uuid || `pending-${event.created_at || Math.random()}`,
    pending: true,
  }))
  const persisted = Array.isArray(biddingFileFormatPlan.value?.edit_events) ? biddingFileFormatPlan.value.edit_events : []
  return [...pending, ...persisted].slice(-30).reverse()
})
const biddingMaterialRequirements = ref([])
const biddingMaterialRequirementSummary = ref({})
const biddingMaterialRequirementsLoading = ref(false)
const biddingMaterialRequirementsGenerating = ref(false)
const biddingMaterialRequirementUpdatingUuid = ref('')
const biddingMaterialRequirementRows = computed(() => biddingMaterialRequirements.value || [])
const biddingTechnicalComposition = ref(null)
const biddingTechnicalCompositionLoading = ref(false)
const biddingTechnicalCompositionGenerating = ref(false)
const biddingTechnicalCompositionComponents = computed(() => biddingTechnicalComposition.value?.components || [])
const biddingTechnicalCompositionSummary = computed(() => biddingTechnicalComposition.value?.summary || {})
const biddingTechnicalCompositionWarnings = computed(() => biddingTechnicalComposition.value?.warnings || [])
const biddingTechnicalCompositionRequirements = computed(() => biddingTechnicalComposition.value?.requirements || [])
const biddingTechnicalCompositionRequirementMap = computed(() => {
  const map = new Map()
  for (const row of biddingTechnicalCompositionRequirements.value) {
    if (row?.component_key && row?.need_key) map.set(`${row.component_key}:${row.need_key}`, row)
  }
  return map
})
const biddingDraftOutline = ref(null)
const biddingDraftOutlineLoading = ref(false)
const biddingDraftOutlineGenerating = ref(false)
const biddingDraftOutlineSections = computed(() => biddingDraftOutline.value?.sections || [])
const biddingDraftOutlineSummary = computed(() => biddingDraftOutline.value?.summary || {})
const biddingDraftOutlineSource = computed(() => biddingDraftOutline.value?.source || {})
const biddingDraftOutlineWarnings = computed(() => biddingDraftOutline.value?.warnings || [])
const biddingDraftSections = ref([])
const biddingDraftSectionsLoading = ref(false)
const biddingDraftSectionGeneratingKey = ref('')
const biddingTechnicalDraftGenerating = ref(false)
const biddingTechnicalDraftExporting = ref(false)
const biddingTechnicalFinalExporting = ref(false)
const biddingTechnicalFinalQualityLoading = ref(false)
const biddingDraftSectionReviewing = ref(false)
const biddingDraftSectionsByKey = computed(() => {
  const map = new Map()
  for (const draft of biddingDraftSections.value) {
    if (draft?.section_key) map.set(draft.section_key, draft)
  }
  return map
})
const biddingTechnicalCompositionDraftSections = computed(() =>
  (biddingDraftSections.value || []).filter((draft) =>
    String(draft?.section_key || '').startsWith('technical_composition:')
    || draft?.generation_decision?.source === 'technical_composition',
  ),
)
const biddingDraftPreviewDrawer = reactive({
  visible: false,
  draft: null,
  editing: false,
  editContent: '',
  saving: false,
  llmGenerating: false,
})
const biddingTechnicalFinalQualityDrawer = reactive({
  visible: false,
  report: null,
})
const biddingFinalQualityReport = computed(() => biddingTechnicalFinalQualityDrawer.report?.quality_report || {})
const biddingFinalQualityTemplateReinforcement = computed(() => biddingFinalQualityReport.value?.section_template_reinforcement || {})
const biddingFinalQualityPlaybookReinforcement = computed(() => biddingFinalQualityReport.value?.section_playbook_reinforcement || {})
const biddingFinalQualityReviewFocusReinforcement = computed(() => biddingFinalQualityReport.value?.section_review_focus_reinforcement || {})
const biddingFinalQualityReinforcement = computed(() => biddingFinalQualityReport.value?.requirement_reinforcement || {})
const biddingFinalQualityCoverage = computed(() => biddingFinalQualityReport.value?.requirement_coverage || {})
const biddingFinalQualityTemplateReinforcementSections = computed(() =>
  (biddingFinalQualityTemplateReinforcement.value?.section_reports || []).slice(0, 80),
)
const biddingFinalQualityPlaybookReinforcementSections = computed(() =>
  (biddingFinalQualityPlaybookReinforcement.value?.section_reports || []).slice(0, 80),
)
const biddingFinalQualityReviewFocusReinforcementSections = computed(() =>
  (biddingFinalQualityReviewFocusReinforcement.value?.section_reports || []).slice(0, 80),
)
const biddingFinalQualityReinforcementTransitions = computed(() =>
  (biddingFinalQualityReinforcement.value?.transitions || []).slice(0, 80),
)
const biddingFinalQualityManualItems = computed(() =>
  (biddingFinalQualityReinforcement.value?.skipped_items || []).slice(0, 80),
)
const biddingFinalQualityCoverageProblemItems = computed(() =>
  (biddingFinalQualityCoverage.value?.problem_items || []).slice(0, 80),
)
const biddingMaterialProfileDialog = reactive({
  visible: false,
  loading: false,
  uploading: false,
  row: null,
  form: {
    category: '',
    keyword: '',
  },
  candidates: [],
  selectedProfiles: [],
  uploadedFiles: [],
})
const filteredBiddingResponseItems = computed(() => {
  const role = biddingResponseReviewRole.value
  if (!role || role === 'all') return biddingResponseItems.value
  return biddingResponseItems.value.filter((row) => biddingResponseRowMatchesRole(row, role))
})
const visibleBiddingResponseItems = computed(() => buildBiddingResponseTaskTree(filteredBiddingResponseItems.value))
const biddingResponseExpandableRows = computed(() =>
  visibleBiddingResponseItems.value.filter((row) => Array.isArray(row.children) && row.children.length),
)
const biddingResponseVisibleSummary = computed(() => buildBiddingResponseLocalSummary(filteredBiddingResponseItems.value))
const biddingBusinessObjectLlmReviewing = ref(false)
const biddingBusinessObjectLlmProgress = reactive({
  visible: false,
  total: 0,
  current: 0,
  completed: 0,
  error: 0,
  skipped: 0,
  currentTitle: '',
  lastMessage: '',
})
const biddingLlmDecisionSubmitting = ref(false)
const biddingLlmEditDialog = reactive({
  visible: false,
  row: null,
  form: {
    decision: 'manual_review',
    suggested_title: '',
    suggested_object_subtype: '',
    primary_business_action: '',
    reason: '',
    suggested_reviewer_note: '',
    reviewer_note: '',
  },
})
const biddingRequirements = ref([])
const biddingRequirementsTotal = ref(0)
const biddingRisks = ref([])
const biddingRisksTotal = ref(0)
const biddingRiskCards = ref([])
const biddingRiskCardsSummary = ref({})
const biddingParsing = ref(false)
const biddingProjectUploadRef = ref(null)
const biddingUploadRef = ref(null)
const costItems = ref([])
const costItemTotal = ref(0)
const costItemPage = ref(1)
const costItemPageSize = 20
const costDbLoading = ref(false)
const enterpriseProfileLoading = ref(false)
const enterpriseProfileItems = ref([])
const enterpriseProfileSummary = ref({})
const enterpriseProfileTotal = ref(0)
const enterpriseProfilePage = ref(1)
const enterpriseProfilePageSize = 20
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
const costMasterSummary = ref(null)
const costMasterLoading = ref(false)
const costMasterActiveTab = ref('quotaItems')
const enterpriseQuotaItems = ref([])
const enterpriseQuotaItemTotal = ref(0)
const enterpriseQuotaComponents = ref([])
const enterpriseQuotaComponentTotal = ref(0)
const enterpriseQuotaResources = ref([])
const enterpriseQuotaResourceTotal = ref(0)
const projectCostImportBatches = ref([])
const projectCostImportTotal = ref(0)
const projectCostImportPage = ref(1)
const projectCostImportPageSize = 10
const projectCostImportLoading = ref(false)
const projectCostImportUploading = ref(false)
const projectCostImportProjectName = ref('')
const projectCostImportFiles = ref([])
const projectCostImportFileInput = ref(null)
const projectCostImportFolderInput = ref(null)
const selectedProjectCostImportBatch = ref(null)
const projectCostCandidates = ref([])
const projectCostCandidateTotal = ref(0)
const projectCostCandidatePage = ref(1)
const projectCostCandidatePageSize = 20
const projectCostCandidateLoading = ref(false)
const selectedProjectCostCandidates = ref([])
const projectCostCandidateFilters = reactive({ status: '', risk_level: '', keyword: '' })
const costMasterPage = ref(1)
const costMasterComponentPage = ref(1)
const costMasterResourcePage = ref(1)
const costMasterPageSize = 20
const ENTERPRISE_QUOTA_REFERENCE_SOURCE = 'enterprise_quota.active'
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
const dwgTrialUploadFiles = ref([])
const dwgTrialPdfUploadFiles = ref([])
const dwgTrialResult = ref(null)
const dwgTrialLoading = ref(false)
const dwgTrialFinalizing = ref(false)
const dwgTrialFinalizationResult = ref(null)
const dwgTrialCandidateSelections = reactive({})
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
const projectFilters = reactive({
  status: '',
  risk_level: '',
  keyword: '',
})
const myProjectTaskFilters = reactive({
  status: '',
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
const biddingFilters = reactive({
  status: '',
  keyword: '',
})
const enterpriseProfileFilters = reactive({
  category: '',
  status: '',
  keyword: '',
})
const costItemFilters = reactive({
  category: '',
  status: [],
  price_type: '',
  source: '',
  keyword: '',
})
const costMasterFilters = reactive({
  keyword: '',
  fee_bucket: '',
  resource_type: '',
})
const costLineageFilters = reactive({
  source: '',
  keyword: '',
  has_quote_usage: '',
})
const dashboardRange = ref('last_30_days')
const dashboardTab = ref('business')
const executionPageTab = ref('tasks')
const dashboardFeature = reactive({ businessDisabled: false, quoteDisabled: false, responseDisabled: false, executionDisabled: false, projectDisabled: false })
const executionFeatureDisabled = ref(false)
const projectFeatureDisabled = ref(false)
const meetingFeatureDisabled = ref(false)
const businessLedgerFeatureDisabled = ref(false)
const costMeasurements = ref([])
const costMeasurementTotal = ref(0)
const costMeasurementPage = ref(1)
const costMeasurementPageSize = 20
const costMeasurementLoading = ref(false)
const costMeasurementFeatureDisabled = ref(false)
const costMeasurementDetail = ref(null)
const costMeasurementFileInput = ref(null)
const costMeasurementDrawer = reactive({ visible: false })
const costMeasurementImportDialog = reactive({
  visible: false,
  file: null,
  preview: null,
  name: '',
  project_name: '',
})
const costMeasurementDraftDialog = reactive({
  visible: false,
  loading: false,
  submitting: false,
  summary: null,
  candidates: [],
  note: '',
})
const costMeasurementDraftSelectedCount = computed(() =>
  costMeasurementDraftDialog.candidates.filter((row) => row.can_create && row.selected).length,
)
const costDbFeatureDisabled = ref(false)
const enterpriseProfileFeatureDisabled = ref(false)
const agentCenterFeatureDisabled = ref(false)
const agentCenterLoading = ref(false)
const agentQuoteJobId = ref('')
const agentRuns = ref([])
const agentRunTotal = ref(0)
const agentRunPage = ref(1)
const agentRunPageSize = 15
const agentRunDetail = ref(null)
const agentLlmExplanation = ref(null)
const agentLlmExplanationLoading = ref(false)
const agentLlmExplanationLoadingMode = ref('')
const agentShowExplanation = ref(false)
const agentDailyDate = ref('')
const agentDailySummary = ref(null)
const agentTodoSummary = ref(null)
const agentClosureSummary = ref(null)
const agentClosureDays = ref(7)
const agentClosureLoading = ref(false)
const agentSchedulerStatus = ref(null)
const agentSchedulerHistory = ref([])
const agentSchedulerHistoryTotal = ref(0)
const agentSchedulerHistoryPage = ref(1)
const agentSchedulerHistoryPageSize = 10
const agentSchedulerHistoryDays = ref(7)
const agentSchedulerHistoryLoading = ref(false)
const agentDailyLoading = ref(false)
const agentDailyFeatureDisabled = ref(false)
const agentPendingSuggestions = ref([])
const agentPendingSuggestionTotal = ref(0)
const agentPendingSuggestionPage = ref(1)
const agentPendingSuggestionPageSize = 10
const state = reactive({ loading: false, submitting: false, error: '' })

const agentIsAuditRun = computed(() =>
  agentRunDetail.value?.output?.audit_mode === 'confirmed_quote_risk_audit'
)
const agentAuditSummary = computed(() => agentRunDetail.value?.output?.audit_summary || {})
const agentAuditRecords = computed(() => agentRunDetail.value?.output?.audit_records || [])
const agentAuditTableRows = computed(() =>
  agentAuditRecords.value.map((record, index) => ({
    ...record,
    _index: index + 1,
    _row_key: record.target_ref || record.target_label || `audit-${index}`,
  }))
)
const agentLlmBeforeAfterRows = computed(() =>
  (agentLlmExplanation.value?.before_after_explanations || []).map((item, index) => ({
    ...item,
    _row_key: item.target_label || item.explanation || `before-after-${index}`,
  }))
)
const agentLlmRiskRows = computed(() =>
  (agentLlmExplanation.value?.risk_explanations || []).map((item, index) => ({
    ...item,
    _row_key: item.target_ref || item.target_label || item.title || `risk-${index}`,
  }))
)
const agentLlmSuggestionRows = computed(() =>
  (agentLlmExplanation.value?.suggestion_priorities || []).map((item, index) => ({
    ...item,
    _row_key: item.suggestion_id || item.target_ref || item.title || `suggestion-${index}`,
  }))
)
const agentMarketReferenceRows = computed(() =>
  agentAuditTableRows.value
    .map((record) => {
      const context = agentAuditMarketContext(record)
      const sources = agentAuditMarketSources(record)
      const cities = context.cities && typeof context.cities === 'object' ? context.cities : {}
      return {
        _row_key: record._row_key,
        target_label: record.target_label,
        item_name: context.item_name || record.project_name,
        spec: context.spec || record.original_preview?.notes,
        confirmed_unit_price: context.confirmed_unit_price ?? record.confirmed_quote?.unit_price,
        confidence: context.confidence,
        explanation: context.explanation || record.market_search_explanation,
        city_price_texts: {
          深圳: agentAuditCitySummary(cities.深圳),
          东莞: agentAuditCitySummary(cities.东莞),
        },
        sources,
      }
    })
    .filter((row) => row.explanation || row.sources.length || row.city_price_texts['深圳'] !== '-' || row.city_price_texts['东莞'] !== '-')
)
const agentActionableSuggestions = computed(() => {
  if (agentIsAuditRun.value) return []
  return (agentRunDetail.value?.suggestions || []).filter((suggestion) => isAgentActionableSuggestion(suggestion))
})
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

const projectDialog = reactive({
  visible: false,
  form: {
    name: '',
    client_name: '',
    project_manager_id: null,
    owner_department: '',
    address: '',
    planned_start_at: '',
    planned_finish_at: '',
    description: '',
  },
})

const projectTrialDialog = reactive({
  visible: false,
  form: {
    name: '',
    client_name: '',
    owner_department: '工程部',
    address: '',
    planned_start_at: '',
    planned_finish_at: '',
    description: '',
  },
})

const projectEpcDialog = reactive({
  visible: false,
  form: {
    name: '',
    client_name: '',
    owner_department: '项目管理部',
    address: '',
    mode: 'compact',
    planned_start_at: '',
    planned_finish_at: '',
    description: '',
  },
})

const projectTaskDialog = reactive({
  visible: false,
  form: {
    stage_id: null,
    title: '',
    owner_user_id: null,
    owner_role: '',
    priority: 'normal',
    planned_start_at: '',
    due_at: '',
    next_action: '',
    description: '',
  },
})

const projectEvidenceDrawer = reactive({
  visible: false,
  loading: false,
  task: null,
  summary: {},
  items: [],
  file: null,
  form: {
    evidence_type: 'text',
    title: '',
    description: '',
    external_url: '',
    external_provider: 'other',
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

const biddingDialog = reactive({
  visible: false,
  loading: false,
  file: null,
  form: {
    project_name: '',
    tenderer_name: '',
    tender_agency: '',
    project_location: '',
    project_type: '',
    tender_deadline_at: '',
  },
})

const biddingDrawer = reactive({
  visible: false,
  loading: false,
  activeTab: 'files',
  project: null,
})

function biddingDraftPackageForTab(tab) {
  if (tab === 'businessBidDraft') return 'business'
  if (tab === 'technicalBidDraft') return 'technical'
  return ''
}

const biddingDraftPackageKey = computed(() => biddingDraftPackageForTab(biddingDrawer.activeTab))
const biddingDraftPackageTitle = computed(() => {
  if (biddingDraftPackageKey.value === 'business') return '商务标'
  if (biddingDraftPackageKey.value === 'technical') return '技术标'
  return '投标书'
})

watch(
  () => biddingDrawer.activeTab,
  async (tab) => {
    const packageKey = biddingDraftPackageForTab(tab)
    if (biddingDrawer.loading || !biddingDrawer.visible || !currentBiddingProjectUuid() || !packageKey) return
    const loaders = [
      loadBiddingFileFormatPlan(),
      loadBiddingMaterialRequirements(undefined, packageKey),
      loadBiddingDraftOutline(),
      loadBiddingDraftSections(),
    ]
    if (tab === 'technicalBidDraft') loaders.push(loadBiddingTechnicalComposition())
    await Promise.all(loaders)
  },
)

const biddingUpload = reactive({
  file: null,
  fileType: 'tender_document',
  loading: false,
})

const enterpriseProfileDialog = reactive({
  visible: false,
  mode: 'create',
  uploading: false,
  itemUuid: '',
  detail: null,
  form: {
    category: 'basic_info',
    subcategory: '',
    profile_key: '',
    title: '',
    material_form: 'text',
    summary: '',
    content_text: '',
    attachment_file_id: '',
    attachment_type: 'source',
    attachment_description: '',
    tagsText: '',
    applicable_scope: '',
    valid_until: '',
    change_reason: '',
  },
})

const enterpriseProfileAttachmentDialog = reactive({
  visible: false,
  uploading: false,
  item: null,
  form: {
    file_id: '',
    attachment_type: 'source',
    description: '',
    is_primary: true,
  },
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

const enterpriseQuotaItemDrawer = reactive({
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
const canViewDashboardMetrics = computed(() => canAccessPermissions.value || roles.value.includes('viewer'))
const canViewDashboard = computed(() => canViewDashboardMetrics.value || roles.value.includes('quote_operator'))
const canViewQuoteOperations = computed(() => canAccessPermissions.value || roles.value.includes('quote_operator'))
const canManageQuoteOperations = computed(() => canAccessPermissions.value)
const canViewExecution = computed(() => canAccessPermissions.value || roles.value.includes('staff') || roles.value.includes('manager'))
const canCreateExecutionTask = computed(() => canAccessPermissions.value)
const canCreateMeetingNote = computed(() => canViewExecution.value)
const canViewProjectProgress = computed(() => canAccessPermissions.value || hasRole('staff', 'manager', 'project_viewer', 'project_member', 'project_manager'))
const canViewMyProjectTasks = computed(() => hasRole('staff', 'manager', 'project_member', 'project_manager'))
const canManageProjectProgress = computed(() => canAccessPermissions.value || hasRole('manager', 'project_manager'))
const canViewBusinessLedger = computed(() => canAccessPermissions.value || roles.value.includes('staff'))
const canManageBusinessLedger = computed(() => canAccessPermissions.value)
const canViewEnterpriseProfile = computed(() => canAccessPermissions.value || hasRole('enterprise_profile_viewer', 'enterprise_profile_editor', 'enterprise_profile_approver'))
const canEditEnterpriseProfile = computed(() => canAccessPermissions.value || hasRole('enterprise_profile_editor', 'enterprise_profile_approver'))
const canApproveEnterpriseProfile = computed(() => canAccessPermissions.value || roles.value.includes('enterprise_profile_approver'))
const canViewCostDb = computed(() => canAccessPermissions.value || hasRole('cost_viewer', 'cost_editor', 'cost_approver', 'cost_exporter'))
const canViewCostMeasurement = computed(() => canViewCostDb.value)
const canEditCostMeasurement = computed(() => canEditCostDb.value)
const canApproveCostMeasurement = computed(() => canApproveCostDb.value)
const canExportCostMeasurement = computed(() => canExportCostDb.value)
const canEditCostDb = computed(() => canAccessPermissions.value || hasRole('cost_editor', 'cost_approver'))
const canApproveCostDb = computed(() => canAccessPermissions.value || roles.value.includes('cost_approver'))
const canExportCostDb = computed(() => canAccessPermissions.value || roles.value.includes('cost_exporter'))
const canViewCostAudit = computed(() => canAccessPermissions.value || roles.value.includes('cost_approver'))
const canViewRequirementStandardization = computed(() => canAccessPermissions.value || hasRole('staff', 'quote_user'))
const budgetProjectsModule = computed(() => (session.user?.available_modules || []).find(
  (module) => module.key === 'budget_projects' || module.path === '/admin/budget-projects',
))
const budgetProjectsFeatureAvailable = computed(() => budgetProjectsModule.value?.status === 'available')
const budgetPricingModule = computed(() => (session.user?.available_modules || []).find(
  (module) => module.key === 'budget_pricing',
))
const budgetPricingFeatureAvailable = computed(() => budgetPricingModule.value?.status === 'available')
const accountQuotasModule = computed(() => (session.user?.available_modules || []).find(
  (module) => module.key === 'account_quotas' || module.path === '/admin/account-quotas',
))
const accountQuotasFeatureAvailable = computed(() => accountQuotasModule.value?.status === 'available')
const canViewAccountQuotas = computed(() => accountQuotasFeatureAvailable.value && canAccessPermissions.value)
const canViewBudgetProjectsByRole = computed(() => canAccessPermissions.value || hasRole(
  'viewer', 'staff', 'quote_user', 'quote_operator', 'manager',
  'project_viewer', 'project_member', 'project_manager',
))
const canViewBudgetProjects = computed(() => budgetProjectsFeatureAvailable.value && canViewBudgetProjectsByRole.value)
const canEditBudgetProjectsByRole = computed(() => canAccessPermissions.value || hasRole(
  'staff', 'quote_user', 'quote_operator', 'manager', 'project_manager',
))
const canEditBudgetProjects = computed(() => budgetProjectsFeatureAvailable.value && canEditBudgetProjectsByRole.value)
const canViewDwgTrial = computed(() => canAccessPermissions.value || hasRole('staff', 'quote_user'))
const canViewBidding = computed(() => canAccessPermissions.value || hasRole('staff', 'quote_user', 'quote_operator', 'manager'))
const canViewAgentCenter = computed(() => canAccessPermissions.value || hasRole('staff', 'quote_user', 'quote_operator'))
const canManageAgentDailyReview = computed(() => canAccessPermissions.value || hasRole('quote_operator'))
const canOpenLegacyQuote = computed(() => canAccessPermissions.value || hasRole('staff', 'quote_user'))
const canOpenLegacyAdmin = computed(() => canAccessPermissions.value)
const selectedCostItemIds = computed(() => selectedCostItems.value.map((item) => item.id).filter(Boolean))
const selectableCostItems = computed(() => costItems.value.filter((item) => costItemSelectable(item)))
const selectedDraftCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'draft').length)
const selectedActiveCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'active').length)
const selectedArchivableCostItemCount = computed(() => selectedCostItems.value.filter((item) => item.status === 'draft' || item.status === 'active').length)
const costCurrentPageStatusCounts = computed(() => costItems.value.reduce((acc, item) => {
  const status = item.status || 'unknown'
  acc[status] = (acc[status] || 0) + 1
  return acc
}, {}))
const costDbActiveFilterSummary = computed(() => {
  const parts = []
  if (costItemFilters.category) parts.push(`类别 ${costItemFilters.category}`)
  if (costItemFilters.status?.length) parts.push(`状态 ${costItemFilters.status.map(costStatusLabel).join('/')}`)
  if (costItemFilters.price_type) parts.push(`价格类型 ${costPriceTypeLabel(costItemFilters.price_type)}`)
  if (costItemFilters.source) parts.push(`来源 ${costSourceLabel(costItemFilters.source)}`)
  if (costItemFilters.keyword) parts.push(`关键词 ${costItemFilters.keyword}`)
  return parts.length ? parts.join(' · ') : '当前未限定筛选条件'
})
const costDbSelectionSummary = computed(() => {
  if (!selectedCostItemIds.value.length) {
    return `未选择条目；当前筛选共 ${costItemTotal.value || 0} 条，可先按状态/来源筛出待审核项。`
  }
  return `已选 ${selectedCostItemIds.value.length} 条：待核定 ${selectedDraftCostItemCount.value}，已启用 ${selectedActiveCostItemCount.value}，可归档 ${selectedArchivableCostItemCount.value}。`
})
const biddingTenderAnalysisQuality = computed(() => biddingTenderAnalysis.value?.quality_summary || {})
const biddingTenderAnalysisReviewQueue = computed(() => (
  (biddingTenderAnalysis.value?.review_queue || []).filter((item) => item?.table_key !== 'risk_clause')
))
const biddingTenderReviewPreviewRows = computed(() => biddingTenderAnalysisReviewQueue.value)
const biddingTenderAnalysisTables = computed(() => biddingTenderAnalysis.value?.tables || {})
const biddingLegacyTenderSummaryRows = computed(() => biddingTenderAnalysisTables.value.summary?.items || [])
const biddingImportantInfo = computed(() => biddingTenderAnalysis.value?.important_info || {})
const biddingImportantInfoSections = computed(() => {
  const sections = Array.isArray(biddingImportantInfo.value?.sections) ? biddingImportantInfo.value.sections : []
  const priorityClarificationText = formatBiddingPriorityClarificationText(biddingImportantInfo.value?.priority_clarifications)
  return sections
    .map((section, sectionIndex) => {
      const sectionKey = String(section?.section_key || `section_${sectionIndex + 1}`)
      const title = String(section?.title || `结构化信息 ${sectionIndex + 1}`)
      const items = (Array.isArray(section?.items) ? section.items : []).map((item, itemIndex) => {
        const fieldKey = String(item?.field_key || '')
        const shouldUsePriorityList = sectionKey === 'pre_bid_clarifications' && fieldKey === 'priority_clarifications' && priorityClarificationText
        return {
          ...item,
          row_key: `${sectionKey}:${item?.field_key || itemIndex}`,
          section_key: sectionKey,
          section_title: title,
          status: shouldUsePriorityList ? 'found' : item?.status || 'not_found',
          value: shouldUsePriorityList ? priorityClarificationText : item?.value || '',
          note: item?.note || '',
          source_file: item?.source_file || '',
          source_location: item?.source_location || '',
          confidence: Number(item?.confidence || 0),
        }
      })
      return {
        ...section,
        section_key: sectionKey,
        title,
        items,
      }
    })
    .filter((section) => section.items.length)
})
const biddingTenderSummaryRows = computed(() => biddingImportantInfoSections.value.flatMap((section) => section.items))
const biddingImportantInfoSectionKeys = computed(() => biddingImportantInfoSections.value.map((section) => section.section_key).filter(Boolean))
const biddingImportantInfoAllExpanded = computed(() => (
  biddingImportantInfoSectionKeys.value.length > 0
  && biddingImportantInfoSectionKeys.value.every((key) => biddingImportantInfoExpandedKeys.value.includes(key))
))
const biddingImportantInfoFieldCount = computed(() => biddingTenderSummaryRows.value.length)
const biddingImportantInfoFoundCount = computed(() => (
  biddingTenderSummaryRows.value.filter((item) => item.status === 'found' && String(item.value || '').trim()).length
))
const biddingImportantInfoIssueCount = computed(() => (
  biddingTenderSummaryRows.value.filter((item) => item.status !== 'found' || !String(item.value || '').trim()).length
))
watch(biddingImportantInfoSectionKeys, (keys) => {
  biddingImportantInfoExpandedKeys.value = [...keys]
})
const biddingImportantInfoProgressBarStatus = computed(() => {
  if (biddingImportantInfoProgress.status === 'success') return 'success'
  if (biddingImportantInfoProgress.status === 'error') return 'exception'
  return undefined
})
const biddingImportantInfoEmptyText = computed(() => {
  const status = biddingImportantInfo.value?.status || biddingImportantInfo.value?.metadata?.status
  if (status === 'error') return `LLM结构化提取失败：${biddingImportantInfo.value?.metadata?.error || '未知错误'}`
  if (status === 'disabled') return 'LLM结构化提取未启用'
  if (status === 'skipped') return 'LLM结构化提取已跳过'
  if (status === 'no_source') return '暂无可分析的招标文件原文片段'
  return '暂无LLM结构化信息，请先解析招标文件'
})
const biddingTenderScoringRows = computed(() => biddingTenderAnalysisTables.value.scoring?.items || [])
const biddingTenderScoringDisplayRows = computed(() => {
  const expandedKeys = new Set(biddingTenderScoringExpandedKeys.value)
  const rows = []
  for (const row of biddingTenderScoringRows.value) {
    const children = Array.isArray(row?.children) ? row.children : []
    const { children: _children, ...parentRow } = row || {}
    const isExpanded = expandedKeys.has(parentRow.row_key)
    rows.push({
      ...parentRow,
      __scoringCanExpand: children.length > 0,
      __scoringChildrenCount: Number(parentRow.child_count || children.length || 0),
      __scoringExpanded: isExpanded,
      __scoringChild: false,
    })
    if (!isExpanded) continue
    for (const child of children) {
      const { children: _grandchildren, ...childRow } = child || {}
      rows.push({
        ...childRow,
        __scoringCanExpand: false,
        __scoringChildrenCount: 0,
        __scoringExpanded: false,
        __scoringChild: true,
        __scoringParentKey: parentRow.row_key,
      })
    }
  }
  return rows
})
const biddingTenderRiskClauseRows = computed(() => (
  (Array.isArray(biddingRiskClause.value?.risks) ? biddingRiskClause.value.risks : []).map((row, index) => ({
    ...row,
    row_key: row?.risk_id || `risk-${index + 1}`,
  }))
))
const biddingRiskClauseBasicInfo = computed(() => biddingRiskClause.value?.basic_info || {})
const biddingRiskClauseBasicRows = computed(() => [
  { label: '依据文件', value: biddingRiskClauseBasicInfo.value.source_files || '-' },
  { label: '适用场景', value: biddingRiskClauseBasicInfo.value.applicable_scenario || '-' },
  { label: '风险分布', value: biddingRiskClauseBasicInfo.value.risk_distribution || '-' },
  { label: '生成说明', value: biddingRiskClauseBasicInfo.value.generation_note || '-' },
])
const biddingRiskClausePriorityAttention = computed(() => (
  Array.isArray(biddingRiskClause.value?.priority_attention) ? biddingRiskClause.value.priority_attention : []
))
const biddingRiskClauseRiskCount = computed(() => biddingTenderRiskClauseRows.value.length)
const biddingRiskClauseHighCount = computed(() => biddingTenderRiskClauseRows.value.filter((row) => row.risk_level === 'high').length)
const biddingRiskClauseMediumCount = computed(() => biddingTenderRiskClauseRows.value.filter((row) => row.risk_level === 'medium').length)
const biddingRiskClauseLowCount = computed(() => biddingTenderRiskClauseRows.value.filter((row) => row.risk_level === 'low').length)
const biddingRiskClauseProgressBarStatus = computed(() => {
  if (biddingRiskClauseProgress.status === 'success') return 'success'
  if (biddingRiskClauseProgress.status === 'error') return 'exception'
  return undefined
})
const biddingRiskClauseEmptyText = computed(() => {
  const status = biddingRiskClause.value?.status || biddingRiskClause.value?.metadata?.status
  if (status === 'error') return `LLM风险分析失败：${biddingRiskClause.value?.metadata?.error || '未知错误'}`
  if (status === 'disabled') return 'LLM风险分析未启用'
  if (status === 'skipped') return 'LLM风险分析已跳过'
  if (status === 'no_source') return '暂无可分析的招标文件原文片段'
  return '暂无风险条款清单，请先点击“风险分析”'
})
const biddingTenderScoringItemCount = computed(() => Number(
  biddingTenderAnalysisQuality.value.scoring_item_count || biddingTenderScoringRows.value.length || 0,
))
const biddingTenderAnalysisResultCount = computed(() => (
  biddingTenderSummaryRows.value.length + biddingTenderScoringItemCount.value + biddingTenderRiskClauseRows.value.length
))
const biddingPendingRiskCount = computed(() => (
  biddingRisks.value.filter((item) => item.review_status === 'pending').length
))
const biddingBusinessObjectTypeRows = computed(() => {
  const counts = biddingBusinessObjectsSummary.value.object_by_type || {}
  return Object.entries(counts)
    .map(([type, count]) => ({
      type,
      label: biddingBusinessObjectTypeLabel(type),
      count: Number(count || 0),
    }))
    .filter((item) => item.count > 0)
    .sort((left, right) => right.count - left.count)
})
const biddingLlmReviewRows = computed(() => (
  (biddingBusinessObjects.value || [])
    .filter((item) => item.normalized?.llm_review || [
      'pending_manual_confirm',
      'accepted',
      'rejected',
      'modified',
      'error',
    ].includes(item.normalized?.llm_review_status))
    .sort((left, right) => {
      const statusOrder = {
        pending_manual_confirm: 0,
        error: 1,
        modified: 2,
        accepted: 3,
        rejected: 4,
      }
      const leftStatus = statusOrder[left.normalized?.llm_review_status] ?? 5
      const rightStatus = statusOrder[right.normalized?.llm_review_status] ?? 5
      return leftStatus - rightStatus
    })
))
const biddingLlmPendingRows = computed(() => (
  (biddingBusinessObjects.value || []).filter((item) => biddingBusinessObjectNeedsLlmReview(item))
))
const biddingLlmProgressPercentage = computed(() => {
  const total = Number(biddingBusinessObjectLlmProgress.total || 0)
  if (!total) return 0
  const done = Number(biddingBusinessObjectLlmProgress.completed || 0)
    + Number(biddingBusinessObjectLlmProgress.error || 0)
    + Number(biddingBusinessObjectLlmProgress.skipped || 0)
  return Math.min(100, Math.round((done / total) * 100))
})
const latestBiddingParseSummary = computed(() => biddingParseRuns.value[0]?.summary || {})
const biddingDocumentStructureRows = computed(() => {
  const counts = latestBiddingParseSummary.value.document_structure?.segment_by_section || {}
  return Object.entries(counts)
    .map(([section, count]) => ({
      section,
      label: biddingDocumentSectionLabel(section),
      count: Number(count || 0),
    }))
    .filter((item) => item.count > 0)
    .sort((left, right) => right.count - left.count)
})
const biddingOverviewCards = computed(() => {
  const projects = biddingProjects.value || []
  const riskCount = projects.reduce((sum, row) => sum + Number(row.counts?.risk_count || 0), 0)
  const highRiskCount = projects.reduce((sum, row) => sum + Number(row.counts?.high_risk_count || 0), 0)
  const pendingRiskCount = projects.reduce((sum, row) => sum + Number(row.counts?.pending_risk_count || 0), 0)
  const parsedCount = projects.filter((row) => row.status === 'parsed').length
  return [
    {
      key: 'projects',
      title: '当前项目',
      value: `${biddingProjectTotal.value || projects.length}`,
      detail: `已解析 ${parsedCount} 个`,
      tone: 'is-info',
    },
    {
      key: 'risks',
      title: '识别风险',
      value: `${riskCount}`,
      detail: `高风险 ${highRiskCount} · 待复核 ${pendingRiskCount}`,
      tone: highRiskCount ? 'is-danger' : 'is-success',
    },
    {
      key: 'files',
      title: '当前资料',
      value: `${biddingFiles.value.length}`,
      detail: biddingDrawer.project ? '详情抽屉内资料数' : '打开项目后查看',
      tone: 'is-info',
    },
    {
      key: 'runs',
      title: '解析版本',
      value: `${biddingParseRuns.value.length}`,
      detail: biddingParseRuns.value[0]?.status ? `最近 ${biddingParseStatusLabel(biddingParseRuns.value[0].status)}` : '暂无解析',
      tone: 'is-info',
    },
  ]
})
const enterpriseProfileOverviewCards = computed(() => {
  const summary = enterpriseProfileSummary.value || {}
  const statusCounts = summary.status_counts || {}
  return [
    {
      key: 'total',
      title: '资料总数',
      value: `${summary.total || 0}`,
      detail: `草稿 ${statusCounts.draft || 0} · 已归档 ${statusCounts.archived || 0}`,
      tone: 'is-info',
    },
    {
      key: 'active',
      title: '可用于投标',
      value: `${statusCounts.active || 0}`,
      detail: '已启用资料才会进入后续填充候选',
      tone: (statusCounts.active || 0) ? 'is-success' : 'is-warning',
    },
    {
      key: 'expiry',
      title: '有效期提醒',
      value: `${summary.expired_count || 0}/${summary.expiring_soon_count || 0}`,
      detail: '过期 / 30天内到期',
      tone: (summary.expired_count || 0) ? 'is-danger' : ((summary.expiring_soon_count || 0) ? 'is-warning' : 'is-success'),
    },
    {
      key: 'missing',
      title: '缺附件',
      value: `${summary.missing_attachment_count || 0}`,
      detail: '证照、人员、业绩等需保留附件证据',
      tone: (summary.missing_attachment_count || 0) ? 'is-warning' : 'is-success',
    },
  ]
})
const costMasterOverviewCards = computed(() => {
  const summary = costMasterSummary.value || {}
  const version = summary.active_version || {}
  const ragRun = summary.latest_successful_rag_sync || costRagSyncStatus.value?.latest_successful_run
  const versionLabel = version.version_code || '暂无已启用版本'
  return [
    {
      key: 'version',
      title: '当前已启用版本',
      value: versionLabel,
      detail: version.version_name || '请先激活企业定额版本',
      tone: version.id ? 'is-success' : 'is-warning',
    },
    {
      key: 'items',
      title: '定额主项',
      value: `${summary.quota_item_count || 0} 条`,
      detail: `分部 ${summary.section_count || 0} 个 · 组成 ${summary.component_count || 0} 条`,
      tone: 'is-info',
    },
    {
      key: 'resources',
      title: '资源价格',
      value: `${summary.resource_count || 0} 条`,
      detail: summary.source ? '已生成成本参考' : '暂无成本参考',
      tone: 'is-info',
    },
    {
      key: 'rag',
      title: '最近成本参考更新',
      value: ragRun?.synced_count != null ? `${ragRun.synced_count} 条` : '暂无',
      detail: ragRun?.finished_at ? formatShanghaiDate(ragRun.finished_at) : '尚未发现成功同步记录',
      tone: ragRun ? 'is-success' : 'is-warning',
    },
  ]
})
const costDbOverviewCards = computed(() => {
  const counts = costCurrentPageStatusCounts.value
  const statusDetail = `当前页待核定 ${counts.draft || 0} · 已启用 ${counts.active || 0} · 已归档 ${counts.archived || 0}`
  const ragLabel = costRagSyncStatus.value?.status_label || costRagSyncSummaryLabel(costRagSyncStatus.value?.status)
  return [
    {
      key: 'result',
      title: '筛选结果',
      value: `${costItemTotal.value || 0} 条`,
      detail: costDbActiveFilterSummary.value,
      tone: 'is-info',
    },
    {
      key: 'status',
      title: '当前页状态',
      value: `${counts.draft || 0}/${counts.active || 0}/${counts.archived || 0}`,
      detail: statusDetail,
      tone: (counts.draft || 0) > 0 ? 'is-warning' : 'is-success',
    },
    {
      key: 'selection',
      title: '已选条目',
      value: `${selectedCostItemIds.value.length}`,
      detail: selectedCostItemIds.value.length
        ? `待核定 ${selectedDraftCostItemCount.value} · 已启用 ${selectedActiveCostItemCount.value}`
        : '用于批量核定、恢复或归档',
      tone: selectedCostItemIds.value.length ? 'is-warning' : 'is-info',
    },
    {
      key: 'rag',
      title: '成本参考更新',
      value: ragLabel || '-',
      detail: `已启用 ${costRagSyncStatus.value?.active_count || 0} · 最近成功 ${formatShanghaiDate(costRagSyncStatus.value?.latest_successful_run?.finished_at)}`,
      tone: costRagSyncSummaryAlertType(costRagSyncStatus.value?.status) === 'success' ? 'is-success' : 'is-warning',
    },
  ]
})
const visibleDailyTrends = computed(() => (quoteDashboard.value?.daily_trends || []).filter((item) => item.sample_count > 0).slice(-12))
const visibleResponseSources = computed(() => (responseDashboard.value?.by_source || []).slice(0, 12))
const visibleResponseResponders = computed(() => (responseDashboard.value?.by_responder || []).slice(0, 12))
const visibleExecutionTrends = computed(() => (executionDashboard.value?.daily_trends || []).filter((item) => item.task_count > 0).slice(-12))
const visibleExecutionAssignees = computed(() => (executionDashboard.value?.by_assignee || []).slice(0, 12))
const visibleProjectManagers = computed(() => (projectDashboard.value?.by_project_manager || []).slice(0, 12))
const businessSectionErrorCount = computed(() => (businessDashboard.value?.section_errors || []).length)
const businessRisks = computed(() => businessDashboard.value?.risks || [])
const managementFocusLinks = computed(() => [
  canViewProjectProgress.value ? { label: '项目进度', path: '/admin/projects' } : null,
  canViewExecution.value ? { label: '执行任务', path: '/admin/execution' } : null,
  canViewBusinessLedger.value ? { label: '商务台账', path: '/admin/business-ledger' } : null,
].filter(Boolean))
const managementFocusCards = computed(() => {
  const cards = []
  const project = projectDashboard.value || {}
  const businessProject = businessDashboard.value?.project_progress || {}
  const execution = executionDashboard.value || {}

  if (canViewProjectProgress.value && !dashboardFeature.projectDisabled && projectDashboard.value) {
    const blockedProjectCount = Number(project.blocked_count || 0)
    const blockedTaskCount = Number(project.blocked_task_count || 0)
    if (blockedProjectCount || blockedTaskCount) {
      cards.push({
        key: 'project_blocked',
        title: '项目阻塞',
        value: `${blockedProjectCount} 个项目`,
        detail: `涉及 ${blockedTaskCount} 项阻塞任务，需要协调推进`,
        action: '进入项目进度处理',
        tone: 'danger',
        priority: 0,
        targetPath: '/admin/projects',
      })
    }

    const delayedProjectCount = Number(project.delayed_count || 0)
    const overdueTaskCount = Number(project.overdue_task_count || 0)
    if (delayedProjectCount || overdueTaskCount) {
      cards.push({
        key: 'project_delayed',
        title: '项目延期',
        value: `${delayedProjectCount} 个项目`,
        detail: `涉及 ${overdueTaskCount} 项逾期任务，建议复核计划`,
        action: '进入项目进度处理',
        tone: 'warning',
        priority: 2,
        targetPath: '/admin/projects',
      })
    }
  }

  if (canViewExecution.value && !dashboardFeature.executionDisabled && executionDashboard.value) {
    const overdueCount = Number(execution.overdue_count || 0)
    if (overdueCount) {
      cards.push({
        key: 'execution_overdue',
        title: '执行任务逾期',
        value: `${overdueCount} 项`,
        detail: `当前未完成 ${Number(execution.open_count || 0)} 项，需明确下一步`,
        action: '进入执行任务处理',
        tone: 'danger',
        priority: 1,
        targetPath: '/admin/execution',
      })
    }
  }

  if (canViewProjectProgress.value && !dashboardFeature.businessDisabled && businessDashboard.value) {
    const missingEvidenceCount = Number(businessProject.missing_evidence_task_count || 0)
    const bypassedGateCount = Number(businessProject.hard_gate_bypassed_missing_evidence_count || 0)
    if (missingEvidenceCount || bypassedGateCount) {
      cards.push({
        key: 'project_evidence',
        title: '成果证据待补',
        value: `${missingEvidenceCount} 项`,
        detail: `其中放行后仍缺证据 ${bypassedGateCount} 项`,
        action: '进入项目进度补充',
        tone: bypassedGateCount ? 'danger' : 'warning',
        priority: bypassedGateCount ? 0 : 3,
        targetPath: '/admin/projects',
      })
    }
  }

  return cards.sort((left, right) => left.priority - right.priority)
})
const managementFocusSummary = computed(() => {
  const count = managementFocusCards.value.length
  return count
    ? `已汇总 ${count} 类需要关注的事项，按风险优先级排序。`
    : '未发现阻塞、延期、逾期或待补证据事项。'
})
const businessQuoteTrendRows = computed(() => visibleBusinessTrendRows(businessDashboard.value?.quote?.daily_trend || [], [
  'task_count',
  'success_count',
  'failed_or_timeout_count',
  'pushed_count',
]))
const businessProjectTrendRows = computed(() => visibleBusinessTrendRows(businessDashboard.value?.project_progress?.daily_trend || [], [
  'bypass_gate_event_count',
  'bypassed_missing_evidence_count',
  'soft_reminder_event_count',
]))
const businessQuoteTrendMax = computed(() => maxBusinessTrendValue(businessQuoteTrendRows.value, [
  'task_count',
  'success_count',
  'failed_or_timeout_count',
  'pushed_count',
]))
const businessProjectTrendMax = computed(() => maxBusinessTrendValue(businessProjectTrendRows.value, [
  'bypass_gate_event_count',
  'bypassed_missing_evidence_count',
  'soft_reminder_event_count',
]))
const businessQuickLinks = computed(() => {
  const links = businessDashboard.value?.links || []
  const preferred = ['quote_workspace', 'cost_db', 'project_progress', 'ops_dashboard']
  return preferred.map((key) => links.find((item) => item.key === key)).filter(Boolean)
})
const businessDistributionGroups = computed(() => {
  const data = businessDashboard.value || {}
  const cost = data.cost || {}
  const project = data.project_progress || {}
  return [
    {
      key: 'cost_status',
      title: '成本库状态',
      rows: buildBusinessDistributionRows(cost.status_distribution || [], 'status'),
    },
    {
      key: 'cost_source',
      title: '成本库来源',
      rows: buildBusinessDistributionRows(cost.source_distribution || [], 'source'),
    },
    {
      key: 'project_status',
      title: '项目状态',
      rows: buildBusinessDistributionRows(project.project_status_distribution || [], 'status'),
    },
    {
      key: 'task_status',
      title: '任务状态',
      rows: buildBusinessDistributionRows(project.task_status_distribution || [], 'status'),
    },
  ]
})
const businessMetricCards = computed(() => {
  const data = businessDashboard.value || {}
  const quote = data.quote || {}
  const cost = data.cost || {}
  const project = data.project_progress || {}
  const environment = data.environment || {}
  return [
    {
      key: 'quote_tasks',
      title: '报价任务',
      value: String(quote.task_count ?? 0),
      subtitle: `成功 ${quote.success_count ?? 0} · 失败 ${quote.failed_count ?? 0} · 草稿 ${quote.draft_count ?? 0}`,
      targetPath: '/admin/dashboard',
    },
    {
      key: 'quote_push',
      title: '报价下发',
      value: String(quote.pushed_count ?? 0),
      subtitle: `下发总价 ${formatAmount(quote.pushed_total_amount ?? 0)}`,
      targetPath: '/admin/dashboard',
    },
    {
      key: 'cost_status',
      title: '成本库状态',
      value: `${cost.active_count ?? 0} / ${cost.draft_count ?? 0} / ${cost.archived_count ?? 0}`,
      subtitle: '已启用 / 待核定 / 已归档',
      targetPath: '/admin/cost-db',
    },
    {
      key: 'rag_sync',
      title: '成本参考更新',
      value: cost.rag_status_label || cost.rag_status || '-',
      subtitle: `最近成功 ${formatDate(cost.last_success_sync_at)}`,
      targetPath: '/admin/cost-db',
    },
    {
      key: 'no_cost_draft',
      title: '无底价待审',
      value: String(cost.no_cost_draft_count ?? 0),
      subtitle: `成本库待核定共 ${cost.draft_count ?? 0} 条`,
      targetPath: '/admin/cost-db',
    },
    {
      key: 'project_progress',
      title: '项目进度',
      value: String(project.active_project_count ?? 0),
      subtitle: `阻塞任务 ${project.blocked_task_count ?? 0} · 逾期 ${project.overdue_task_count ?? 0}`,
      targetPath: '/admin/projects',
    },
    {
      key: 'evidence',
      title: '成果证据',
      value: String(project.missing_evidence_task_count ?? 0),
      subtitle: `硬门禁 ${project.complete_required_task_count ?? 0} · 放行未补 ${project.hard_gate_bypassed_missing_evidence_count ?? 0}`,
      targetPath: '/admin/projects',
    },
    {
      key: 'system',
      title: '系统健康',
      value: businessOverallLabel(environment.overall_status),
      subtitle: `局部降级 ${businessSectionErrorCount.value} 个区块`,
      targetPath: '/api/v1/admin/ops/dashboard',
    },
  ]
})
const businessTrialReadinessCards = computed(() => {
  const data = businessDashboard.value || {}
  const quote = data.quote || {}
  const cost = data.cost || {}
  const project = data.project_progress || {}
  const environment = data.environment || {}
  return [
    {
      key: 'quote',
      label: '报价任务',
      value: `${quote.success_count ?? 0}/${quote.task_count ?? 0}`,
      detail: `成功/总任务，失败 ${quote.failed_count ?? 0}，超时 ${quote.timeout_count ?? 0}`,
      tone: (quote.failed_count || quote.timeout_count) ? 'is-warning' : 'is-success',
    },
    {
      key: 'cost',
      label: '成本库准备',
      value: `${cost.active_count ?? 0} 条已启用`,
      detail: `待核定 ${cost.draft_count ?? 0}，无底价待审 ${cost.no_cost_draft_count ?? 0}`,
      tone: (cost.no_cost_draft_count || cost.draft_count) ? 'is-warning' : 'is-success',
    },
    {
      key: 'project',
      label: '项目证据',
      value: `${project.missing_evidence_task_count ?? 0}`,
      detail: `缺证据任务；放行未补 ${project.hard_gate_bypassed_missing_evidence_count ?? 0}`,
      tone: (project.missing_evidence_task_count || project.hard_gate_bypassed_missing_evidence_count) ? 'is-danger' : 'is-success',
    },
    {
      key: 'system',
      label: '系统运行',
      value: businessOverallLabel(environment.overall_status),
      detail: `局部降级 ${businessSectionErrorCount.value} 个区块，运行状态 ${businessModeLabel(environment.mode)}`,
      tone: environment.overall_status === 'degraded' ? 'is-danger' : (environment.overall_status === 'warning' ? 'is-warning' : 'is-success'),
    },
  ]
})
const businessSummaryRows = computed(() => {
  const data = businessDashboard.value || {}
  const quote = data.quote || {}
  const cost = data.cost || {}
  const project = data.project_progress || {}
  const system = data.system_health || {}
  return [
    {
      key: 'quote',
      label: '报价链路',
      value: `${quote.success_count ?? 0} 成功 / ${quote.failed_count ?? 0} 失败`,
      detail: `平均耗时 ${formatMs(quote.avg_duration_ms)}，超时 ${quote.timeout_count ?? 0}`,
    },
    {
      key: 'cost',
      label: '成本库',
      value: `${cost.active_count ?? 0} active`,
      detail: `draft ${cost.draft_count ?? 0}，审计事件 ${cost.audit_event_count ?? 0}`,
    },
    {
      key: 'project',
      label: '项目进度',
      value: `${project.project_count ?? 0} 个项目`,
      detail: `进行中 ${project.active_project_count ?? 0}，缺证据 ${project.missing_evidence_task_count ?? 0}`,
    },
    {
      key: 'mode',
      label: '运行模式',
      value: businessModeLabel(data.environment?.mode),
      detail: `功能状态：${system.feature_flags?.dashboard_business_lite ? '可用' : '暂不可用'}`,
    },
  ]
})
const requirementSheetMappings = computed(() => requirementPreview.value?.sheet_mappings || [])
const requirementSummary = computed(() => requirementPreview.value?.summary || {})
const dwgTrialSummary = computed(() => dwgTrialResult.value?.summary || {})
const dwgTrialFiles = computed(() => dwgTrialResult.value?.files || [])
const dwgTrialDebugFiles = computed(() => dwgTrialResult.value?.debug_files || [])
const dwgTrialIssues = computed(() => dwgTrialResult.value?.issues || [])
const dwgTrialProjectRows = computed(() => dwgTrialResult.value?.project_rows || [])
const dwgTrialProjectGeometryBindingRows = computed(() => dwgTrialResult.value?.project_geometry_binding_rows || [])
const dwgTrialProjectGeometryCandidateRows = computed(() => dwgTrialResult.value?.project_geometry_candidate_rows || [])
const dwgTrialItemRows = computed(() => dwgTrialResult.value?.item_rows || [])
const dwgTrialQuantityTraceRows = computed(() => dwgTrialResult.value?.quantity_trace_rows || [])
const dwgTrialLineQuantityCandidateRows = computed(() => dwgTrialResult.value?.line_quantity_candidate_rows || [])
const dwgTrialPdfEvidenceSummary = computed(() => dwgTrialResult.value?.pdf_evidence_summary || {})
const dwgTrialPdfRenderRows = computed(() => dwgTrialResult.value?.pdf_render_rows || [])
const dwgTrialPdfPreviewRow = computed(() => dwgTrialPdfRenderRows.value.find((row) => row.preview_url))
const dwgTrialPdfVisualEvidenceRows = computed(() => (dwgTrialResult.value?.pdf_visual_evidence_rows || []).slice(0, 80))
const dwgTrialPdfFiles = computed(() => dwgTrialFiles.value.filter((item) => (
  String(item?.key || '').startsWith('pdf_')
  || ['dwg_pdf_match_csv', 'dxf_pdf_fusion_csv'].includes(item?.key)
)))
const dwgTrialHasPdfEvidence = computed(() => Boolean(
  dwgTrialResult.value?.pdf_evidence_effective
  || dwgTrialResult.value?.has_pdf_evidence
))
const dwgTrialQuantityListRows = computed(() => {
  const directRows = dwgTrialResult.value?.quantity_list_rows
  const sourceRows = Array.isArray(directRows) && directRows.length
    ? directRows
    : (dwgTrialResult.value?.project_rows || []).map((row) => ({
      项目名称: row?.项目名称 || row?.图纸项目名称 || '',
      项目特征: row?.项目特征 || '',
      单位: row?.单位 || '',
      工程量: row?.工程量 || '待算量',
    }))
  return sourceRows.map((row, index) => ({
    ...row,
    工程量: row?.工程量 || '待算量',
    __row_key: `${index + 1}:${row?.项目名称 || ''}:${row?.项目特征 || ''}`,
  }))
})
const dwgTrialSelectedCount = computed(() => Object.keys(dwgTrialCandidateSelections).length)
const dwgTrialAdoptedSelections = computed(() => Object.values(dwgTrialCandidateSelections).filter((item) => item.action === '采纳'))
const dwgTrialAdoptedCount = computed(() => dwgTrialAdoptedSelections.value.length)
const dwgTrialResultJsonFile = computed(() => (
  dwgTrialDebugFiles.value.find((item) => item.key === 'item_list_json' || item.key === 'json')
  || dwgTrialFiles.value.find((item) => item.key === 'item_list_json' || item.key === 'json')
))
const dwgTrialQuantityListFile = computed(() => (
  dwgTrialFiles.value.find((item) => item.key === 'quantity_list_xlsx')
  || dwgTrialFiles.value.find((item) => item.key === 'project_draft_four_field_xlsx')
))
const dwgTrialFinalizationSummary = computed(() => dwgTrialFinalizationResult.value?.summary || {})
const dwgTrialFinalizationFiles = computed(() => dwgTrialFinalizationResult.value?.files || [])
const dwgTrialFinalizationIssues = computed(() => dwgTrialFinalizationResult.value?.issues || [])
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
const projectUserOptions = computed(() => {
  const source = projectUsers.value.length ? projectUsers.value : (session.user ? [session.user] : [])
  return source.filter((user) => user.is_active !== false)
})
const projectEvidenceSummary = computed(() => {
  const backendSummary = projectDetail.value?.evidence_summary
  if (backendSummary) return backendSummary
  const tasks = projectDetail.value?.tasks || []
  const requiredTasks = tasks.filter((task) => projectTaskNeedsEvidence(task))
  const evidencedTasks = requiredTasks.filter((task) => Number(task.evidence_count || 0) > 0)
  const missingTasks = requiredTasks.filter((task) => Number(task.evidence_count || 0) <= 0)
  const doneWithoutEvidence = missingTasks.filter((task) => task.status === 'done')
  return {
    required_task_count: requiredTasks.length,
    evidenced_task_count: evidencedTasks.length,
    missing_evidence_task_count: missingTasks.length,
    done_without_evidence_task_count: doneWithoutEvidence.length,
    open_missing_evidence_task_count: missingTasks.filter((task) => task.status !== 'done').length,
    evidence_completion_percent: requiredTasks.length ? Math.round((evidencedTasks.length * 100) / requiredTasks.length) : 0,
  }
})
const businessLedgerOverviewCards = computed(() => {
  const rows = businessLedgers.value || []
  const openCount = rows.filter((item) => isBusinessLedgerActionable(item)).length
  const overdueCount = rows.filter((item) => isBusinessLedgerOverdue(item)).length
  const dueSoonCount = rows.filter((item) => isBusinessLedgerDueSoon(item)).length
  const quoteStageCount = rows.filter((item) => ['报价中', '跟进议价'].includes(item.stage) && isBusinessLedgerActionable(item)).length
  return [
    {
      key: 'total',
      title: '筛选客户',
      value: `${businessLedgerTotal.value || rows.length}`,
      detail: rows.length ? `当前页 ${rows.length} 条台账` : '暂无客户台账数据',
      tone: 'is-info',
    },
    {
      key: 'open',
      title: '待跟进',
      value: `${openCount}`,
      detail: openCount ? '尚未进入结束阶段的客户' : '当前页无待跟进客户',
      tone: openCount ? 'is-info' : 'is-success',
    },
    {
      key: 'overdue',
      title: '已逾期',
      value: `${overdueCount}`,
      detail: overdueCount ? '请优先联系并更新跟进计划' : '当前页暂无逾期跟进',
      tone: overdueCount ? 'is-danger' : 'is-success',
    },
    {
      key: 'quote-stage',
      title: '报价/议价',
      value: `${quoteStageCount}`,
      detail: dueSoonCount ? `未来 3 天内需跟进 ${dueSoonCount} 条` : '当前页无近期跟进提醒',
      tone: quoteStageCount ? 'is-warning' : 'is-info',
    },
  ]
})

const executionTaskOverviewCards = computed(() => {
  const rows = executionTasks.value || []
  const pendingCount = rows.filter((item) => item.status === 'pending').length
  const progressingCount = rows.filter((item) => item.status === 'in_progress').length
  const overdueCount = rows.filter((item) => item.is_overdue && !['done', 'cancelled'].includes(item.status)).length
  const doneCount = rows.filter((item) => item.status === 'done').length
  return [
    {
      key: 'total',
      title: '执行任务',
      value: `${executionTaskTotal.value || rows.length}`,
      detail: rows.length ? `当前页 ${rows.length} 项任务` : '暂无任务数据',
      tone: 'is-info',
    },
    {
      key: 'pending',
      title: '待开始',
      value: `${pendingCount}`,
      detail: progressingCount ? `推进中 ${progressingCount} 项` : '可从待开始任务安排推进',
      tone: pendingCount ? 'is-info' : 'is-success',
    },
    {
      key: 'priority',
      title: '优先处理',
      value: `${overdueCount}`,
      detail: overdueCount ? '逾期任务需要优先处理' : '当前页暂无逾期任务',
      tone: overdueCount ? 'is-danger' : 'is-success',
    },
    {
      key: 'done',
      title: '已完成',
      value: `${doneCount}`,
      detail: rows.length ? `当前页完成率 ${Math.round((doneCount * 100) / rows.length)}%` : '暂无完成记录',
      tone: doneCount ? 'is-success' : 'is-info',
    },
  ]
})

const meetingOverviewCards = computed(() => {
  const rows = meetings.value || []
  const draftCount = rows.filter((item) => item.status === 'draft').length
  const revisedCount = rows.filter((item) => item.status === 'revised').length
  const pendingDraftCount = rows.reduce((sum, item) => sum + meetingPendingDraftCount(item), 0)
  const confirmedCount = rows.filter((item) => item.status === 'confirmed').length
  return [
    {
      key: 'total',
      title: '会议纪要',
      value: `${meetingTotal.value || rows.length}`,
      detail: rows.length ? `当前页 ${rows.length} 条纪要` : '暂无纪要数据',
      tone: 'is-info',
    },
    {
      key: 'draft',
      title: '待整理',
      value: `${draftCount}`,
      detail: draftCount ? '草稿纪要可继续完善' : '当前页无待整理纪要',
      tone: draftCount ? 'is-warning' : 'is-success',
    },
    {
      key: 'pending-drafts',
      title: '待确认任务',
      value: `${pendingDraftCount}`,
      detail: pendingDraftCount ? '请核对负责人和截止时间' : '任务草稿已全部处理',
      tone: pendingDraftCount ? 'is-warning' : 'is-success',
    },
    {
      key: 'confirmed',
      title: '已确认',
      value: `${confirmedCount}`,
      detail: revisedCount ? `另有 ${revisedCount} 条待跟进更正` : '当前页无待跟进更正',
      tone: revisedCount ? 'is-info' : 'is-success',
    },
  ]
})

const projectListOverviewCards = computed(() => {
  const rows = projects.value || []
  const activeCount = rows.filter((item) => item.status === 'active').length
  const riskCount = rows.filter((item) => ['warning', 'delayed', 'blocked'].includes(item.risk_level)).length
  const blockedCount = rows.filter((item) => item.risk_level === 'blocked').length
  const taskCount = rows.reduce((sum, item) => sum + Number(item.task_count || 0), 0)
  const doneTaskCount = rows.reduce((sum, item) => sum + Number(item.done_task_count || 0), 0)
  const avgProgress = rows.length
    ? Math.round(rows.reduce((sum, item) => sum + Number(item.progress_percent || 0), 0) / rows.length)
    : 0
  return [
    {
      key: 'total',
      title: '筛选项目',
      value: `${projectTotal.value || rows.length} 个`,
      detail: rows.length ? `当前页 ${rows.length} 个项目` : '暂无项目数据',
      tone: 'is-info',
    },
    {
      key: 'active',
      title: '进行中',
      value: `${activeCount}`,
      detail: `当前页平均进度 ${avgProgress}%`,
      tone: activeCount ? 'is-success' : 'is-info',
    },
    {
      key: 'risk',
      title: '风险项目',
      value: `${riskCount}`,
      detail: `其中阻塞 ${blockedCount} 个`,
      tone: blockedCount ? 'is-danger' : (riskCount ? 'is-warning' : 'is-success'),
    },
    {
      key: 'tasks',
      title: '任务闭环',
      value: `${doneTaskCount}/${taskCount}`,
      detail: taskCount ? `当前页完成率 ${Math.round((doneTaskCount * 100) / taskCount)}%` : '暂无任务统计',
      tone: taskCount && doneTaskCount < taskCount ? 'is-warning' : 'is-success',
    },
  ]
})
const myProjectTaskOverviewCards = computed(() => {
  const rows = myProjectTasks.value || []
  const todoCount = rows.filter((item) => item.status === 'todo').length
  const progressingCount = rows.filter((item) => ['started', 'progressing'].includes(item.status)).length
  const submittedCount = rows.filter((item) => item.status === 'submitted').length
  const blockedCount = rows.filter((item) => item.status === 'blocked').length
  const evidenceRequiredCount = rows.filter((item) => projectTaskNeedsEvidence(item)).length
  const missingEvidenceCount = rows.filter((item) => projectTaskNeedsEvidence(item) && Number(item.evidence_count || 0) <= 0).length
  return [
    {
      key: 'total',
      title: '我的任务',
      value: `${myProjectTaskTotal.value || rows.length}`,
      detail: `当前页 ${rows.length} 条，未开始 ${todoCount}`,
      tone: 'is-info',
    },
    {
      key: 'progress',
      title: '推进中',
      value: `${progressingCount}`,
      detail: `待确认 ${submittedCount} 条`,
      tone: progressingCount || submittedCount ? 'is-warning' : 'is-info',
    },
    {
      key: 'blocked',
      title: '阻塞任务',
      value: `${blockedCount}`,
      detail: blockedCount ? '优先解除阻塞或补充下一步' : '当前页无阻塞',
      tone: blockedCount ? 'is-danger' : 'is-success',
    },
    {
      key: 'evidence',
      title: '成果证据',
      value: `${missingEvidenceCount}/${evidenceRequiredCount}`,
      detail: '缺证据/需证据任务',
      tone: missingEvidenceCount ? 'is-warning' : 'is-success',
    },
  ]
})
const projectDetailFocusCards = computed(() => {
  const detail = projectDetail.value || {}
  const summary = projectEvidenceSummary.value || {}
  return [
    {
      key: 'progress',
      title: '总进度',
      value: `${detail.progress_percent || 0}%`,
      detail: `当前阶段 ${detail.current_stage_name || '-'}`,
      tone: 'is-info',
    },
    {
      key: 'blocked',
      title: '阻塞/逾期',
      value: `${detail.blocked_task_count || 0}/${detail.overdue_task_count || 0}`,
      detail: '阻塞任务/逾期任务',
      tone: (detail.blocked_task_count || detail.overdue_task_count) ? 'is-danger' : 'is-success',
    },
    {
      key: 'evidence',
      title: '证据完整性',
      value: `${summary.evidence_completion_percent || 0}%`,
      detail: `缺证据 ${summary.missing_evidence_task_count || 0} 个节点`,
      tone: (summary.missing_evidence_task_count || 0) > 0 ? 'is-warning' : 'is-success',
    },
    {
      key: 'tasks',
      title: '任务闭环',
      value: `${detail.done_task_count || 0}/${detail.task_count || 0}`,
      detail: `${projectStatusLabel(detail.status)} · ${projectRiskLabel(detail.risk_level)}`,
      tone: detail.risk_level === 'blocked' || detail.risk_level === 'delayed' ? 'is-danger' : 'is-info',
    },
  ]
})
const visibleProjectDetailTasks = computed(() => {
  const tasks = projectDetail.value?.tasks || []
  if (projectTaskEvidenceFilter.value === 'required') return tasks.filter((task) => projectTaskNeedsEvidence(task))
  if (projectTaskEvidenceFilter.value === 'evidenced') return tasks.filter((task) => projectTaskNeedsEvidence(task) && Number(task.evidence_count || 0) > 0)
  if (projectTaskEvidenceFilter.value === 'missing') return tasks.filter((task) => projectTaskNeedsEvidence(task) && Number(task.evidence_count || 0) <= 0)
  if (projectTaskEvidenceFilter.value === 'done_missing') return tasks.filter((task) => projectTaskNeedsEvidence(task) && Number(task.evidence_count || 0) <= 0 && task.status === 'done')
  if (projectTaskEvidenceFilter.value === 'open_missing') return tasks.filter((task) => projectTaskNeedsEvidence(task) && Number(task.evidence_count || 0) <= 0 && task.status !== 'done')
  return tasks
})
const businessLedgerResponderOptions = computed(() => {
  const source = users.value.length ? users.value : (session.user ? [session.user] : [])
  return source.filter((user) => user.is_active !== false)
})
const businessLedgerDialogTitle = computed(() => (
  businessLedgerDialog.mode === 'edit' ? '编辑商务台账' : '新建商务台账'
))
const enterpriseProfileDialogTitle = computed(() => {
  if (enterpriseProfileDialog.mode === 'view') return '企业资料详情'
  return enterpriseProfileDialog.mode === 'edit' ? '编辑企业资料' : '新建企业资料'
})
const costItemDialogTitle = computed(() => (
  costItemDialog.mode === 'edit' ? '编辑成本条目' : '新建成本条目'
))

function routeFromPath(path) {
  const pathname = String(path || '').split(/[?#]/)[0] || '/'
  if (pathname === '/login') return 'login'
  if (pathname === '/no-access') return 'noAccess'
  if (pathname === '/quote/new') return 'quoteNew'
  if (pathname === '/admin/budget-projects') return 'budgetProjects'
  if (/^\/admin\/budget-projects\/\d+$/.test(pathname)) return 'budgetProjectDetail'
  if (pathname === '/admin/dashboard') return 'dashboard'
  if (pathname === '/admin/execution') return 'execution'
  if (pathname === '/admin/projects') return 'projects'
  if (pathname === '/admin/project-tasks/my') return 'projectMyTasks'
  if (/^\/admin\/projects\/\d+$/.test(pathname)) return 'projectDetail'
  if (pathname === '/admin/business-ledger') return 'businessLedger'
  if (pathname === '/admin/bidding') return 'bidding'
  if (pathname === '/admin/enterprise-profile') return 'enterpriseProfile'
  if (pathname === '/admin/cost-measurement') return 'costMeasurement'
  if (pathname === '/admin/cost-db') return 'costDb'
  if (pathname === '/admin/account-quotas') return 'accountQuotas'
  if (pathname === '/admin/requirement-standardization') return 'requirementStandardization'
  if (pathname === '/admin/dwg-trial') return 'dwgTrial'
  if (pathname === '/admin/agent-center') return 'agentCenter'
  return 'permissions'
}

function responseData(response) {
  return response.data?.data ?? response.data
}

function apiErrorMessage(error, fallback = '请求失败') {
  const detail = error.response?.data?.detail
  const detailMessages = {
    EVIDENCE_HARD_GATE_BLOCKED: '该关键节点要求成果证据，请先登记证据再完成，或联系项目经理放行',
    EVIDENCE_BYPASS_REASON_REQUIRED: '请填写至少 6 个字的关键节点放行原因',
    EVIDENCE_CONFIRM_REASON_REQUIRED: '请填写无证据确认说明',
    BIDDING_LLM_REVIEW_DISABLED: '请先开启 FEATURE_BIDDING_LLM_REVIEW=true 后使用 DeepSeek 复核',
    INVALID_BIDDING_LLM_REVIEW_ACTION: 'DeepSeek建议处理动作无效',
    BIDDING_LLM_REVIEW_NOT_AVAILABLE: '当前对象没有可处理的 DeepSeek 建议',
    BIDDING_LLM_REVIEW_REJECT_NOTE_REQUIRED: '请填写驳回原因',
    BIDDING_LLM_REVIEW_MODIFIED_REVIEW_REQUIRED: '请填写修改后的建议内容',
    BID_DRAFT_SECTION_LLM_NOT_ALLOWED: '当前章节未通过 LLM 正文增强入口，请先处理质量画像、写作计划或质检问题',
    BID_DRAFT_SECTION_ACCEPTANCE_BLOCKED: '当前 LLM 增强稿接受前检查未通过，请修改或重新生成后再接受',
    BID_DRAFT_SECTION_LLM_NOT_CONFIGURED: 'DeepSeek 正文增强未配置 API Key',
    BID_DRAFT_SECTION_LLM_FAILED: 'DeepSeek 正文增强调用失败',
    BID_DRAFT_SECTION_LLM_BAD_RESPONSE: 'DeepSeek 正文增强返回格式异常',
    BID_DRAFT_SECTION_LLM_EMPTY_CONTENT: 'DeepSeek 未返回章节正文',
    TENDER_RESPONSE_ITEM_NOT_FOUND: '响应项不存在或已被删除',
    INVALID_RESPONSE_ACTION: '响应动作无效',
    INVALID_RESPONSE_STATUS: '响应状态无效',
  }
  if (detailMessages[detail]) return detailMessages[detail]
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  if (error.response?.data?.message) return error.response.data.message
  return fallback
}

function biddingTechnicalRequirementRequiredInformation(row) {
  return String(row?.description || row?.item_title || row?.title || '本章节所需资料')
    .replace(/^.*?需补充[：:]/, '')
    .replace(/[。；;\s]+$/, '')
    .trim()
}

function normalizeBiddingTechnicalFinalIssues(rawIssues) {
  const issues = Array.isArray(rawIssues) ? rawIssues : []
  const normalizeSection = (value) => String(value || '').replace(/^\d+(?:\.\d+)+\s*/, '').trim()
  const openRequirements = biddingMaterialRequirementRows.value.filter((row) => ['missing', 'submitted'].includes(row.status))
  const requirementForIssue = (item) => {
    const section = normalizeSection(item?.section)
    return openRequirements.find((row) => {
      const rowSection = normalizeSection(row.item_title || row.section_key)
      return section && rowSection && (section === rowSection || section.includes(rowSection) || rowSection.includes(section))
    })
  }
  const result = []
  const seen = new Set()
  issues.forEach((item) => {
    const requirement = requirementForIssue(item)
    if (requirement && ['待补充', '资料需求清单'].includes(item?.code)) return
    let normalized = item
    if (requirement && (item?.code === 'material_requirement_missing' || String(item?.issue || '').includes('资料需求'))) {
      const requiredInformation = item.required_information || biddingTechnicalRequirementRequiredInformation(requirement)
      normalized = {
        ...item,
        code: 'material_requirement_missing',
        section: requirement.item_title || item.section,
        issue: `需补充：${requiredInformation}（当前状态：${biddingMaterialRequirementStatusLabel(requirement.status)}）。`,
        suggestion: `处理入口：本页面“技术标资料需求与补齐清单”→“${requirement.title}”→“填写”；录入明确内容或资料位置，保存后点击“确认可用”，再重新生成对应章节。`,
        required_information: requiredInformation,
        requirement_uuid: requirement.requirement_uuid,
        requirement_status: requirement.status,
        action: 'fill_confirm_and_regenerate',
      }
    }
    const key = normalized.requirement_uuid || `${normalized.section || '-'}|${normalized.issue || '-'}`
    if (seen.has(key)) return
    seen.add(key)
    result.push(normalized)
  })
  return result
}

function biddingTechnicalFinalExportBlockMessage(error) {
  const detail = error.response?.data?.detail
  if (detail?.code !== 'BID_TECHNICAL_FINAL_EXPORT_BLOCKED') {
    return apiErrorMessage(error, '正式技术标 Word 导出失败')
  }
  const issues = normalizeBiddingTechnicalFinalIssues(detail.issues)
  const lines = [
    `正式技术标导出被阻断，仍有 ${issues.length} 项需要处理。`,
    '',
    ...issues.flatMap((item, index) => [
      `${index + 1}. ${item.section || '-'}`,
      `需要补充：${item.required_information || item.issue || '-'}`,
      `处理方法：${item.suggestion || '请在章节复核中处理后重新导出。'}`,
      '',
    ]),
  ]
  return lines.join('\n')
}

function biddingFinalQualityStatusLabel(status) {
  return {
    pass: '通过',
    warning: '有提醒',
    blocked: '阻断',
  }[status] || status || '-'
}

function biddingFinalQualityStatusTag(status) {
  return {
    pass: 'success',
    warning: 'warning',
    blocked: 'danger',
  }[status] || 'info'
}

function biddingCoverageStatusLabel(status) {
  return {
    covered: '已覆盖',
    partially_covered: '部分覆盖',
    missing: '未覆盖',
    needs_manual_review: '需人工',
  }[status] || status || '-'
}

function biddingCoverageStatusTag(status) {
  return {
    covered: 'success',
    partially_covered: 'warning',
    missing: 'danger',
    needs_manual_review: 'warning',
  }[status] || 'info'
}

function biddingReinforcementStatusLabel(status) {
  return {
    applied: '已自动补强',
    manual_review_required: '需人工复核',
    no_action: '无需补强',
  }[status] || status || '-'
}

function biddingReinforcementStatusTag(status) {
  return {
    applied: 'success',
    manual_review_required: 'warning',
    no_action: 'info',
  }[status] || 'info'
}

function biddingReinforcementSkipReasonLabel(reason) {
  return {
    hard_fact: '硬事实资料',
    manual_or_enterprise_profile: '人工/企业资料',
  }[reason] || reason || '-'
}

async function hydrateBlobErrorDetail(error) {
  const data = error?.response?.data
  if (!(data instanceof Blob)) return error
  const type = String(data.type || '')
  if (type && !type.includes('json')) return error
  try {
    const text = await data.text()
    if (!text) return error
    error.response.data = JSON.parse(text)
  } catch (_err) {
    // Keep the original error when the blob is not a JSON error body.
  }
  return error
}

async function promptText(title, message) {
  try {
    const result = await ElMessageBox.prompt(message, title, {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValidator: (value) => Boolean(value && value.trim()),
      inputErrorMessage: '请填写原因',
    })
    return result.value?.trim()
  } catch (_error) {
    return ''
  }
}

function navigate(path) {
  window.history.pushState({}, '', path)
  routeName.value = routeFromPath(path)
  if (routeName.value !== 'login') {
    bootstrap()
  }
}

function openLegacy(path) {
  window.location.href = path
}

function openQuickQuote(mode = 'quick') {
  const params = new URLSearchParams({ entry: 'new-quote' })
  if (mode) params.set('mode', mode)
  openLegacy(`/index.html?${params.toString()}`)
}

function openRequirementQuoteEntry() {
  if (!canViewRequirementStandardization.value) {
    state.error = 'forbidden'
    return
  }
  resetRequirementStandardization()
  navigate('/admin/requirement-standardization?entry=new-quote')
}

async function openBusinessTarget(path) {
  if (!path) return
  if (path === '/api/v1/admin/ops/dashboard') {
    await showOpsDashboardSummary()
    return
  }
  if (path.startsWith('/api/v1/')) {
    try {
      const response = await api.get(path.replace('/api/v1', ''))
      const data = responseData(response)
      ElMessage.success(`运维接口已返回：${data?.overall_status || '已响应'}`)
    } catch (error) {
      ElMessage.error(apiErrorMessage(error, '运维接口检查失败'))
    }
    return
  }
  if (path === '/quote/new') {
    navigate(path)
    return
  }
  if (path.startsWith('/admin/')) {
    navigate(path)
    return
  }
  if (path.endsWith('.html')) {
    openLegacy(path)
    return
  }
  window.open(path, '_blank', 'noopener')
}

async function showOpsDashboardSummary() {
  const loading = ElMessage({
    type: 'info',
    message: '正在检查运维接口...',
    duration: 0,
  })
  try {
    const response = await api.get('/admin/ops/dashboard')
    const data = responseData(response) || {}
    const services = data.services || []
    const alerts = data.alerts || []
    const jobs = data.jobs || {}
    const logs = data.logs || []
    const okCount = services.filter((item) => item.ok).length
    const statusText = data.overall_status === 'ready' ? 'ready' : data.overall_status || 'unknown'
    const lines = [
      `状态：${statusText}`,
      `生成时间：${data.generated_at || '-'}`,
      `服务检查：${okCount}/${services.length} 正常`,
      `告警数量：${alerts.length}`,
      `活跃报价任务：${jobs.active_count ?? 0}`,
      `卡住任务：${jobs.stuck_count ?? 0}`,
      `日志事件：${Array.isArray(logs) ? logs.length : 0}`,
    ]
    loading.close()
    await ElMessageBox.alert(lines.join('\n'), '运维接口状态', {
      confirmButtonText: '知道了',
      customClass: 'ops-dashboard-message',
    })
  } catch (error) {
    loading.close()
    const detail = error.response?.data?.detail
    const message = detail === 'PERMISSION_DENIED'
      ? '当前账号没有运维接口查看权限。该接口需要管理员权限，请使用 admin 账号或让管理员查看。'
      : apiErrorMessage(error, '运维接口检查失败')
    await ElMessageBox.alert(message, '运维接口状态', {
      confirmButtonText: '知道了',
      customClass: 'ops-dashboard-message',
    })
  }
}

function visibleBusinessTrendRows(rows, fields) {
  return (rows || [])
    .filter((row) => fields.some((field) => Number(row?.[field] || 0) > 0))
    .slice(-12)
    .map((row) => {
      const result = { date: row.date || '-' }
      fields.forEach((field) => {
        result[field] = Number(row?.[field] || 0)
      })
      return result
    })
}

function maxBusinessTrendValue(rows, fields) {
  return Math.max(
    1,
    ...((rows || []).flatMap((row) => fields.map((field) => Number(row?.[field] || 0)))),
  )
}

function businessBarWidth(value, maxValue) {
  const numeric = Number(value || 0)
  const max = Number(maxValue || 0)
  if (numeric <= 0 || max <= 0) return '0%'
  return `${Math.max(8, Math.round((numeric / max) * 100))}%`
}

function buildBusinessDistributionRows(rows, keyName) {
  const max = Math.max(1, ...((rows || []).map((row) => Number(row?.count || 0))))
  return (rows || []).map((row) => {
    const count = Number(row?.count || 0)
    const key = row?.[keyName] || row?.status || row?.source || row?.label || 'unknown'
    return {
      key,
      label: row?.label || key,
      count,
      percent: count <= 0 ? 0 : Math.max(8, Math.round((count / max) * 100)),
    }
  })
}

function roleTagType(role) {
  if (role === 'system_admin') return 'danger'
  if (role === 'admin') return 'warning'
  if (role?.startsWith('cost_')) return 'warning'
  if (role?.startsWith('project_')) return 'primary'
  if (role === 'staff') return 'success'
  if (role === 'manager') return 'primary'
  return 'info'
}

function formatDate(value) {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 19)
}

function formatDateParam(value) {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (item) => String(item).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function displayQuoteJobNumber(value) {
  if (!value) return '-'
  if (typeof value === 'object') {
    return value.job_number || value.quote_job_number || value.job_id || value.quote_job_id || value.target_number || value.target_id || '-'
  }
  return String(value)
}

function formatDateTimeInput(value) {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (item) => String(item).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
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

function businessOverallLabel(status) {
  const labels = {
    ok: '正常',
    warning: '有风险',
    degraded: '局部降级',
  }
  return labels[status] || status || '-'
}

function businessOverallTagType(status) {
  if (status === 'ok') return 'success'
  if (status === 'degraded') return 'danger'
  if (status === 'warning') return 'warning'
  return 'info'
}

function businessModeLabel(mode) {
  const labels = {
    internal_trial: '内部使用',
    public_access: '公网访问',
  }
  return labels[mode] || mode || '-'
}

function businessSeverityLabel(severity) {
  const labels = {
    critical: '严重',
    warning: '提醒',
    info: '提示',
  }
  return labels[severity] || severity || '-'
}

function businessSeverityTag(severity) {
  if (severity === 'critical') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
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

function executionTaskNextStepLabel(row) {
  if (row?.is_overdue && !['done', 'cancelled'].includes(row?.status)) return '先处理逾期任务'
  if (row?.status === 'pending') return '开始处理'
  if (row?.status === 'in_progress') return '更新进展或完成'
  if (row?.status === 'done') return '任务已完成'
  return '任务已取消'
}

function executionTaskNextStepDetail(row) {
  if (row?.is_overdue && !['done', 'cancelled'].includes(row?.status)) return `截止时间 ${formatDate(row?.due_at)}`
  if (row?.status === 'pending') return '开始后可持续更新进展'
  if (row?.status === 'in_progress') return '完成后将沉淀为执行记录'
  if (row?.status === 'done') return `完成于 ${formatDate(row?.completed_at)}`
  return '如需继续推进，请重新建立任务'
}

function executionTaskNextStepTone(row) {
  if (row?.is_overdue && !['done', 'cancelled'].includes(row?.status)) return 'is-danger'
  if (row?.status === 'in_progress') return 'is-warning'
  if (row?.status === 'done') return 'is-success'
  return 'is-info'
}

function executionTaskRowClassName({ row }) {
  if (row?.is_overdue && !['done', 'cancelled'].includes(row?.status)) return 'execution-task-row-overdue'
  if (row?.status === 'in_progress') return 'execution-task-row-progressing'
  return ''
}

function meetingPendingDraftCount(row) {
  return Math.max(0, Number(row?.draft_count || 0) - Number(row?.accepted_draft_count || 0))
}

function meetingDraftProgressLabel(row) {
  const draftCount = Number(row?.draft_count || 0)
  const pendingCount = meetingPendingDraftCount(row)
  if (!draftCount) return '未生成任务草稿'
  if (pendingCount) return `待确认 ${pendingCount} 项`
  return '草稿已全部确认'
}

function meetingDraftProgressTone(row) {
  if (meetingPendingDraftCount(row)) return 'is-warning'
  if (Number(row?.draft_count || 0)) return 'is-success'
  return 'is-info'
}

function meetingRowClassName({ row }) {
  if (row?.status !== 'cancelled' && meetingPendingDraftCount(row)) return 'meeting-row-pending'
  if (row?.status === 'revised') return 'meeting-row-revised'
  return ''
}

function projectStatusLabel(status) {
  const option = projectStatusOptions.find((item) => item.value === status)
  return option?.label || status || '-'
}

function projectStatusTag(status) {
  if (status === 'completed') return 'success'
  if (status === 'cancelled') return 'info'
  if (status === 'paused') return 'warning'
  if (status === 'active') return 'primary'
  return 'info'
}

function projectRiskLabel(risk) {
  const option = projectRiskOptions.find((item) => item.value === risk)
  return option?.label || risk || '-'
}

function projectRiskTag(risk) {
  if (risk === 'blocked') return 'danger'
  if (risk === 'delayed') return 'danger'
  if (risk === 'warning') return 'warning'
  return 'success'
}

function projectFocusLabel(row) {
  if (row?.risk_level === 'blocked') return '先解除阻塞'
  if (row?.risk_level === 'delayed') return '优先追赶进度'
  if (row?.risk_level === 'warning') return '关注风险提示'
  const taskCount = Number(row?.task_count || 0)
  const doneTaskCount = Number(row?.done_task_count || 0)
  if (row?.status === 'active' && taskCount === 0) return '补充任务安排'
  if (taskCount && doneTaskCount < taskCount) return '跟进未完成任务'
  return '按计划推进'
}

function projectFocusDetail(row) {
  if (row?.risk_level === 'blocked') return '项目存在阻塞风险，请先确认处理人和下一步'
  if (row?.risk_level === 'delayed') return '项目已出现进度延迟，建议复核当前阶段'
  if (row?.risk_level === 'warning') return '项目存在需关注事项，请提前安排处理'
  const taskCount = Number(row?.task_count || 0)
  const doneTaskCount = Number(row?.done_task_count || 0)
  if (row?.status === 'active' && taskCount === 0) return '尚未建立任务闭环，可从当前阶段开始拆分'
  if (taskCount && doneTaskCount < taskCount) return `已完成 ${doneTaskCount}/${taskCount} 项任务`
  return '当前项目未发现需要优先处理的风险'
}

function projectFocusTone(row) {
  if (['blocked', 'delayed'].includes(row?.risk_level)) return 'is-danger'
  if (row?.risk_level === 'warning') return 'is-warning'
  if (Number(row?.task_count || 0) > Number(row?.done_task_count || 0)) return 'is-info'
  return 'is-success'
}

function projectListRowClassName({ row }) {
  if (row?.risk_level === 'blocked') return 'project-row-critical'
  if (row?.risk_level === 'delayed') return 'project-row-delayed'
  return ''
}

function projectTaskRowClassName({ row }) {
  if (row?.status === 'blocked') return 'project-task-row-blocked'
  if (projectTaskRequiresHardGate(row) && !row?.evidence_count) return 'project-task-row-missing-evidence'
  if (row?.status === 'submitted') return 'project-task-row-submitted'
  return ''
}

function projectTaskStatusLabel(status) {
  const option = projectTaskStatusOptions.find((item) => item.value === status)
  return option?.label || status || '-'
}

function projectTaskStatusTag(status) {
  if (status === 'done') return 'success'
  if (status === 'blocked') return 'danger'
  if (status === 'cancelled') return 'info'
  if (status === 'submitted') return 'warning'
  if (status === 'progressing') return 'primary'
  return 'info'
}

function projectEvidenceTypeLabel(type) {
  const option = projectEvidenceTypeOptions.find((item) => item.value === type)
  return option?.label || type || '-'
}

function projectEvidenceTypeTag(type) {
  if (type === 'file') return 'success'
  if (type === 'link') return 'primary'
  return 'info'
}

function formatFileSize(size) {
  const value = Number(size || 0)
  if (!value) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function projectEventLabel(eventType) {
  const labels = {
    project_created: '创建项目',
    project_updated: '更新项目',
    project_started: '启动项目',
    project_paused: '暂停项目',
    project_completed: '完成项目',
    project_cancelled: '取消项目',
    stage_updated: '更新阶段',
    task_created: '创建任务',
    task_updated: '更新任务',
    task_started: '开始任务',
    task_progressing: '推进任务',
    task_submitted: '提交确认',
    task_completed: '确认完成',
    task_blocked: '任务阻塞',
    task_unblocked: '解除阻塞',
    task_rolled_back: '回退任务',
    task_cancelled: '取消任务',
    task_evidence_added: '新增成果证据',
    task_evidence_removed: '删除成果证据',
    task_submitted_without_evidence: '无证据提交',
    task_completed_without_evidence: '无证据完成',
    task_completed_bypass_gate: '关键节点放行完成',
  }
  return labels[eventType] || eventType || '-'
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

function formatPercent(value) {
  if (value === null || value === undefined || value === '') return '-'
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  return `${(number * 100).toFixed(1)}%`
}

function reviewDisplayUnitPrice(row) {
  return row?.display_unit_price ?? row?.final_unit_price ?? row?.ai_unit_price
}

function reviewDisplayTotalPrice(row) {
  return row?.display_total_price ?? row?.final_total_price ?? row?.system_total_price
}

function reviewPriceSourceLabel(row) {
  if (!row) return ''
  const hasFinal = row.final_unit_price !== null && row.final_unit_price !== undefined
  if (!hasFinal) return 'AI预审价'
  const aiText = row.ai_unit_price !== null && row.ai_unit_price !== undefined
    ? `AI原始 ${formatPrice(row.ai_unit_price)}`
    : 'AI原始 -'
  return row.manual_modified ? `人工确认价，${aiText}` : `最终确认价，${aiText}`
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
    empty_active: '无已启用条目',
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
  return canManageQuoteOperations.value && ['failed', 'canceled', 'timed_out'].includes(row.status)
}

function canCancelQuoteJob(row) {
  return canManageQuoteOperations.value && ['queued', 'running'].includes(row.status)
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

function isBusinessLedgerActionable(row) {
  return Boolean(row) && !row.cancelled_at && !isBusinessTerminal(row.stage)
}

function isBusinessLedgerOverdue(row) {
  const followupTime = toTimestamp(row.next_followup_at)
  if (!followupTime || !isBusinessLedgerActionable(row)) return false
  return followupTime < Date.now()
}

function isBusinessLedgerDueSoon(row) {
  const followupTime = toTimestamp(row?.next_followup_at)
  if (!followupTime || !isBusinessLedgerActionable(row) || followupTime < Date.now()) return false
  return followupTime <= Date.now() + 3 * 24 * 60 * 60 * 1000
}

function businessLedgerNextStepLabel(row) {
  if (row?.cancelled_at) return '记录已作废'
  if (row?.stage === '成单') return '沉淀成交结果'
  if (row?.stage === '丢单') return '保留丢单复盘'
  if (isBusinessLedgerOverdue(row)) return '立即联系客户'
  if (isBusinessLedgerDueSoon(row)) return '安排近期跟进'
  if (row?.stage === '初步接触') return '确认客户需求'
  if (row?.stage === '需求确认') return '明确范围与报价条件'
  if (row?.stage === '报价中') return '完成报价并及时跟进'
  if (row?.stage === '跟进议价') return '推进议价与决策'
  return '补充下次跟进计划'
}

function businessLedgerNextStepDetail(row) {
  if (row?.cancelled_at) return '该记录不再参与后续跟进'
  if (row?.stage === '成单') return '可完善成交信息，便于后续复盘'
  if (row?.stage === '丢单') return '建议保留原因和后续机会线索'
  if (row?.next_followup_at) return `下次跟进：${formatDate(row.next_followup_at)}`
  return '尚未设置下次跟进时间'
}

function businessLedgerNextStepTone(row) {
  if (isBusinessLedgerOverdue(row)) return 'is-danger'
  if (isBusinessLedgerDueSoon(row) || ['报价中', '跟进议价'].includes(row?.stage)) return 'is-warning'
  if (row?.stage === '成单') return 'is-success'
  return 'is-info'
}

function businessLedgerRowClass({ row }) {
  if (isBusinessLedgerOverdue(row)) return 'ledger-overdue-row'
  if (isBusinessLedgerDueSoon(row)) return 'ledger-due-soon-row'
  if (['报价中', '跟进议价'].includes(row?.stage) && isBusinessLedgerActionable(row)) return 'ledger-active-row'
  return ''
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
    window.location.replace(landingPath(me))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '登录失败'))
  } finally {
    state.loading = false
  }
}

async function loadMe() {
  const response = await api.get('/auth/me')
  session.user = responseData(response)
  localStorage.setItem(
    USER_INFO_KEY,
    JSON.stringify({
      username: session.user.username,
      role: session.user.role,
      roles: Array.isArray(session.user.roles) ? session.user.roles : [],
    }),
  )
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

function handleDwgTrialFileChange(_file, fileList = []) {
  dwgTrialUploadFiles.value = fileList.map((item) => item.raw || item).filter(Boolean)
  dwgTrialResult.value = null
  dwgTrialFinalizationResult.value = null
  clearDwgTrialCandidateSelections()
}

function clearDwgTrialFile(_file, fileList = []) {
  dwgTrialUploadFiles.value = fileList.map((item) => item.raw || item).filter(Boolean)
  dwgTrialFinalizationResult.value = null
  clearDwgTrialCandidateSelections()
}

function handleDwgTrialPdfFileChange(_file, fileList = []) {
  dwgTrialPdfUploadFiles.value = fileList.map((item) => item.raw || item).filter(Boolean)
  dwgTrialResult.value = null
  dwgTrialFinalizationResult.value = null
  clearDwgTrialCandidateSelections()
}

function clearDwgTrialPdfFile(_file, fileList = []) {
  dwgTrialPdfUploadFiles.value = fileList.map((item) => item.raw || item).filter(Boolean)
  dwgTrialFinalizationResult.value = null
  clearDwgTrialCandidateSelections()
}

function resetDwgTrial() {
  dwgTrialUploadFiles.value = []
  dwgTrialPdfUploadFiles.value = []
  dwgTrialResult.value = null
  dwgTrialFinalizationResult.value = null
  clearDwgTrialCandidateSelections()
}

async function loadDwgTrialLatest(options = {}) {
  if (!canViewDwgTrial.value) return
  dwgTrialLoading.value = true
  try {
    const response = await api.get('/admin/dwg-quantity-trial/latest')
    const data = responseData(response)
    dwgTrialResult.value = data?.has_result === false ? null : data
    dwgTrialFinalizationResult.value = null
    clearDwgTrialCandidateSelections()
    if (!dwgTrialResult.value && !options.quiet) {
      ElMessage.info('暂无最近转换结果')
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '最近结果加载失败'))
  } finally {
    dwgTrialLoading.value = false
  }
}

async function convertDwgTrial() {
  const hasPdf = Boolean(dwgTrialPdfUploadFiles.value.length)
  const hasDwg = Boolean(dwgTrialUploadFiles.value.length)
  const isJointUpload = hasPdf && hasDwg
  if (!hasPdf && !hasDwg) {
    ElMessage.warning('请先选择 PDF 或 DWG 图纸')
    return
  }
  dwgTrialLoading.value = true
  try {
    const form = new FormData()
    if (isJointUpload) {
      for (const file of dwgTrialUploadFiles.value) {
        form.append('dwg_files', file)
      }
      for (const file of dwgTrialPdfUploadFiles.value) {
        form.append('pdf_files', file)
      }
    } else if (hasPdf) {
      for (const file of dwgTrialPdfUploadFiles.value) {
        form.append('pdf_files', file)
      }
    } else {
      for (const file of dwgTrialUploadFiles.value) {
        form.append('files', file)
      }
    }
    const endpoint = isJointUpload
      ? '/admin/dwg-quantity-trial/list-items-with-pdf'
      : (hasPdf ? '/admin/dwg-quantity-trial/list-items-from-pdf' : '/admin/dwg-quantity-trial/list-items')
    const response = await api.post(endpoint, form)
    dwgTrialResult.value = responseData(response)
    dwgTrialFinalizationResult.value = null
    clearDwgTrialCandidateSelections()
    if (dwgTrialResult.value?.has_quantity_list_excel || dwgTrialQuantityListRows.value.length) {
      ElMessage.success(isJointUpload ? 'DWG+PDF 联合识图四字段清单已生成' : (hasPdf ? 'PDF 识图四字段清单已生成' : 'DWG 识图四字段清单已生成'))
    } else {
      ElMessage.warning('已完成识别，暂未形成四字段清单')
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, isJointUpload ? 'DWG+PDF 联合识图失败' : (hasPdf ? 'PDF 识图失败' : 'DWG 识图失败')))
  } finally {
    dwgTrialLoading.value = false
  }
}

function clearDwgTrialCandidateSelections() {
  for (const key of Object.keys(dwgTrialCandidateSelections)) {
    delete dwgTrialCandidateSelections[key]
  }
}

function dwgTrialRowKey(row) {
  return [
    row?.序号 || '',
    row?.标准项目编码 || '',
    row?.图纸识别名称 || '',
    row?.图纸识别规格或做法 || '',
  ].join('|')
}

function dwgTrialRowCandidateOptions(row) {
  return Array.isArray(row?.CAD候选列表) ? row.CAD候选列表 : []
}

function dwgTrialRowDecision(row) {
  return dwgTrialCandidateSelections[dwgTrialRowKey(row)]
}

function setDwgTrialCandidateDecision(row, option, action) {
  const key = dwgTrialRowKey(row)
  dwgTrialCandidateSelections[key] = {
    action,
    suggestion_key: option?.建议编号 || '',
    quantity: option?.建议工程量 || '',
    unit: option?.建议单位 || '',
    item_name: row?.项目名称 || '',
    row_no: row?.序号 || '',
    project_name: row?.项目名称 || '',
    project_feature: buildDwgTrialProjectFeature(row),
    quantity_source_note: buildDwgTrialQuantitySource(option),
  }
  ElMessage.success(`${action}：${option?.建议编号 || 'CAD候选'}`)
}

function clearDwgTrialCandidateDecision(row) {
  delete dwgTrialCandidateSelections[dwgTrialRowKey(row)]
}

function dwgTrialDecisionTagType(action) {
  if (action === '采纳') return 'success'
  if (action === '有问题') return 'warning'
  if (action === '不采纳') return 'info'
  return 'primary'
}

function dwgTrialQuantityKindLabel(kind) {
  const labels = {
    area: '面积',
    length: '长度',
    count: '数量',
  }
  return labels[kind] || kind || '-'
}

function buildDwgTrialProjectFeature(row) {
  const sourceValue = Array.from(new Set([row?.图纸识别规格或做法, row?.图纸识别名称, row?.来源证据].filter(Boolean)))
    .join('；')
  const fields = String(row?.项目特征字段 || '')
    .split(/[；;、]/)
    .map((item) => item.trim())
    .filter(Boolean)
  const uniqueFields = Array.from(new Set(fields))
  if (!uniqueFields.length) return sourceValue
  if (!sourceValue) return ''
  return uniqueFields.map((field) => `${field}：${sourceValue}`).join('；')
}

function buildDwgTrialQuantitySource(option) {
  const parts = ['页面采纳 CAD 候选量，并按标准库工程量计算规则进入校验']
  if (option?.CAD公式) parts.push(`CAD公式：${option.CAD公式}`)
  if (option?.CAD来源图元行号) parts.push(`CAD行号：${option.CAD来源图元行号}`)
  return parts.join('；')
}

async function finalizeDwgTrialSelection() {
  if (!dwgTrialAdoptedCount.value) {
    ElMessage.warning('请先采纳至少一条 CAD 候选量')
    return
  }
  if (!dwgTrialResultJsonFile.value?.filename) {
    ElMessage.warning('缺少本次 DWG 列项结果 JSON，请重新上传或加载最近结果')
    return
  }
  dwgTrialFinalizing.value = true
  try {
    const response = await api.post('/admin/dwg-quantity-trial/finalize-selection', {
      result_filename: dwgTrialResultJsonFile.value.path || dwgTrialResultJsonFile.value.filename,
      selections: Object.values(dwgTrialCandidateSelections),
    })
    dwgTrialFinalizationResult.value = responseData(response)
    if (dwgTrialFinalizationResult.value?.has_final_excel) {
      ElMessage.success('最终四字段 Excel 已生成')
    } else {
      ElMessage.warning('采纳结果已提交，但最终四字段校验未通过')
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '最终清单生成失败'))
  } finally {
    dwgTrialFinalizing.value = false
  }
}

async function downloadDwgTrialFile(file) {
  if (!file?.download_url) return
  try {
    const response = await api.get(file.download_url.replace('/api/v1', ''), { responseType: 'blob' })
    const blob = new Blob([response.data])
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.filename || 'dwg_trial_output.xlsx'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '文件下载失败'))
  }
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
      quote_job_number: job.job_number || '',
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
    quote_job_number: job.job_number || '',
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

async function loadAgentDailySummary() {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  agentDailyLoading.value = true
  const params = {}
  if (agentDailyDate.value) params.review_date = agentDailyDate.value
  try {
    const response = await api.get('/admin/agents/quote-review/daily-summary', {
      params,
    })
    const data = responseData(response) || {}
    agentDailySummary.value = data
    if (data.review_date) {
      agentDailyDate.value = data.review_date
    }
  } catch (error) {
    agentDailySummary.value = null
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '每日复核概览加载失败'))
  } finally {
    agentDailyLoading.value = false
  }
}

async function loadAgentSchedulerStatus() {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  const params = {}
  if (agentDailyDate.value) params.review_date = agentDailyDate.value
  try {
    const response = await api.get('/admin/agents/quote-review/scheduler-runs', { params })
    const data = responseData(response) || {}
    agentSchedulerStatus.value = data
    if (data.review_date && !agentDailyDate.value) {
      agentDailyDate.value = data.review_date
    }
  } catch (error) {
    agentSchedulerStatus.value = null
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '每日复核调度状态加载失败'))
  }
}

async function loadAgentTodos() {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  const params = {}
  if (agentDailyDate.value) params.review_date = agentDailyDate.value
  try {
    const response = await api.get('/admin/agents/quote-review/todos', { params })
    const data = responseData(response) || {}
    agentTodoSummary.value = data
    if (data.review_date && !agentDailyDate.value) {
      agentDailyDate.value = data.review_date
    }
  } catch (error) {
    agentTodoSummary.value = null
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '每日后审计状态加载失败'))
  }
}

function offsetDateParam(dateText, offsetDays) {
  const parts = String(dateText || '').split('-').map((item) => Number(item))
  const date = parts.length === 3 && parts.every((item) => Number.isFinite(item))
    ? new Date(parts[0], parts[1] - 1, parts[2])
    : new Date()
  date.setDate(date.getDate() + offsetDays)
  return formatDateParam(date)
}

function agentDailyRangeParams(daysValue) {
  const days = Number(daysValue) || 7
  const dateTo = agentSchedulerStatus.value?.review_date || agentDailySummary.value?.review_date || formatDateParam(new Date())
  return {
    date_from: offsetDateParam(dateTo, -(days - 1)),
    date_to: dateTo,
  }
}

function agentSchedulerHistoryRangeParams() {
  return agentDailyRangeParams(agentSchedulerHistoryDays.value)
}

function agentClosureRangeParams() {
  return agentDailyRangeParams(agentClosureDays.value)
}

async function loadAgentSchedulerHistory() {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  agentSchedulerHistoryLoading.value = true
  try {
    const response = await api.get('/admin/agents/quote-review/scheduler-runs/history', {
      params: {
        page: agentSchedulerHistoryPage.value,
        page_size: agentSchedulerHistoryPageSize,
        ...agentSchedulerHistoryRangeParams(),
      },
    })
    agentSchedulerHistory.value = responseData(response) || []
    agentSchedulerHistoryTotal.value = response.data?.total ?? agentSchedulerHistory.value.length
  } catch (error) {
    agentSchedulerHistory.value = []
    agentSchedulerHistoryTotal.value = 0
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '每日复核调度记录加载失败'))
  } finally {
    agentSchedulerHistoryLoading.value = false
  }
}

function refreshAgentSchedulerHistory() {
  agentSchedulerHistoryPage.value = 1
  loadAgentSchedulerHistory()
}

async function loadAgentClosureSummary() {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  agentClosureLoading.value = true
  try {
    const response = await api.get('/admin/agents/quote-review/closure-summary', {
      params: agentClosureRangeParams(),
    })
    agentClosureSummary.value = responseData(response) || null
  } catch (error) {
    agentClosureSummary.value = null
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '闭环效果统计加载失败'))
  } finally {
    agentClosureLoading.value = false
  }
}

function refreshAgentClosureSummary() {
  loadAgentClosureSummary()
}

async function rescanAgentSchedulerHistoryRow(row) {
  if (!row?.run_date) return
  agentDailyDate.value = row.run_date
  await runDailyQuoteReview(false)
  await loadAgentSchedulerHistory()
}

async function loadAgentPendingSuggestions() {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  agentDailyLoading.value = true
  const reviewDate = agentDailyDate.value || agentDailySummary.value?.review_date || ''
  try {
    const response = await api.get('/admin/agents/suggestions/pending', {
      params: {
        page: agentPendingSuggestionPage.value,
        page_size: agentPendingSuggestionPageSize,
        status: 'open',
        trigger_source: 'scheduled_daily',
        ...(reviewDate ? { review_date: reviewDate } : {}),
      },
    })
    agentPendingSuggestions.value = responseData(response) || []
    agentPendingSuggestionTotal.value = response.data?.total ?? agentPendingSuggestions.value.length
  } catch (error) {
    agentPendingSuggestions.value = []
    agentPendingSuggestionTotal.value = 0
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '待处理建议加载失败'))
  } finally {
    agentDailyLoading.value = false
  }
}

async function refreshAgentDailyReview() {
  agentPendingSuggestionPage.value = 1
  await loadAgentDailySummary()
  agentTodoSummary.value = null
  agentClosureSummary.value = null
  agentPendingSuggestions.value = []
  agentPendingSuggestionTotal.value = 0
}

async function refreshAgentCenter() {
  await loadAgentRuns()
  if (canManageAgentDailyReview.value) {
    await refreshAgentDailyReview()
  }
}

async function runDailyQuoteReview(dryRun = false) {
  if (!canManageAgentDailyReview.value) return
  agentDailyFeatureDisabled.value = false
  agentDailyLoading.value = true
  const payload = { dry_run: dryRun }
  if (agentDailyDate.value) payload.review_date = agentDailyDate.value
  try {
    const response = await api.post('/admin/agents/quote-review/daily-runs', payload)
    const data = responseData(response) || {}
    await refreshAgentCenter()
    const created = data.created_run_count ?? 0
    const skipped = data.skipped_duplicate_count ?? 0
    ElMessage.success(`每日复核完成：新增 ${created} 单，已跳过 ${skipped} 单`)
  } catch (error) {
    if (isFeatureDisabled(error)) {
      agentDailyFeatureDisabled.value = true
      return
    }
    ElMessage.error(apiErrorMessage(error, '每日自动复核失败'))
  } finally {
    agentDailyLoading.value = false
  }
}

async function loadAgentRuns() {
  if (!canViewAgentCenter.value) return
  agentCenterFeatureDisabled.value = false
  agentCenterLoading.value = true
  const params = {
    page: agentRunPage.value,
    page_size: agentRunPageSize,
    agent_type: 'quote_review_assistant',
  }
  try {
    const response = await api.get('/admin/agents/runs', { params })
    agentRuns.value = responseData(response) || []
    agentRunTotal.value = response.data?.total ?? agentRuns.value.length
  } catch (error) {
    agentRuns.value = []
    agentRunTotal.value = 0
    if (isFeatureDisabled(error)) {
      agentCenterFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, 'Agent 运行记录加载失败'))
  } finally {
    agentCenterLoading.value = false
  }
}

function agentLlmSourceLabel(data) {
  if (!data) return '规则解释'
  if (data.mode === 'deepseek') {
    return data.llm_model ? `DeepSeek · ${data.llm_model}` : 'DeepSeek'
  }
  if (data.llm_provider === 'deepseek') {
    if (data.fallback_reason === 'deepseek_api_key_missing') return '规则解释 · 未配置DeepSeek'
    return '规则解释 · DeepSeek降级'
  }
  return '规则解释'
}

function agentLlmSourceTagType(data) {
  if (data?.mode === 'deepseek') return 'success'
  if (data?.llm_provider === 'deepseek') return 'warning'
  return 'info'
}

async function loadAgentLlmExplanation(mode = 'rule') {
  if (!agentRunDetail.value?.run_id) {
    agentLlmExplanation.value = null
    return
  }
  agentLlmExplanationLoading.value = true
  agentLlmExplanationLoadingMode.value = mode
  try {
    const response = await api.get(`/admin/agents/runs/${agentRunDetail.value.run_id}/llm-explanation`, {
      params: { mode },
    })
    agentLlmExplanation.value = responseData(response) || null
  } catch (error) {
    agentLlmExplanation.value = null
    if (!isFeatureDisabled(error)) {
      ElMessage.error(apiErrorMessage(error, mode === 'deepseek' ? 'DeepSeek解释加载失败' : '规则解释加载失败'))
    }
  } finally {
    agentLlmExplanationLoading.value = false
    agentLlmExplanationLoadingMode.value = ''
  }
}

async function toggleAgentExplanation() {
  agentShowExplanation.value = !agentShowExplanation.value
  if (agentShowExplanation.value && !agentLlmExplanation.value) {
    await loadAgentLlmExplanation('rule')
  }
}

function canManualAuditQuoteJob(row) {
  return Boolean(canViewAgentCenter.value && row?.history?.pushed_to_dingtalk && row?.job_id)
}

async function runQuoteReviewAgent(quoteJobId = agentQuoteJobId.value, options = {}) {
  const targetId = String(quoteJobId || '').trim()
  if (!targetId) {
    ElMessage.warning('请先输入报价任务 ID')
    return
  }
  agentCenterFeatureDisabled.value = false
  agentCenterLoading.value = true
  try {
    const payload = {
      quote_job_id: targetId,
      confirmed_only: options.confirmedOnly !== false,
    }
    if (options.quoteHistoryId) payload.quote_history_id = options.quoteHistoryId
    const response = await api.post('/admin/agents/quote-review/runs', payload)
    agentRunDetail.value = responseData(response)
    agentLlmExplanation.value = null
    agentShowExplanation.value = false
    agentQuoteJobId.value = targetId
    agentRunPage.value = 1
    await loadAgentRuns()
    if (canManageAgentDailyReview.value) {
      await refreshAgentDailyReview()
    }
    if (options.openAgentCenter) {
      navigate('/admin/agent-center')
    }
    ElMessage.success('后审计已完成')
  } catch (error) {
    if (isFeatureDisabled(error)) {
      agentCenterFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 409) {
      ElMessage.error('该报价尚未确认下发，不能生成后审计记录')
      return
    }
    ElMessage.error(apiErrorMessage(error, '后审计失败'))
  } finally {
    agentCenterLoading.value = false
  }
}

async function manualAuditQuoteJob(row) {
  if (!canManualAuditQuoteJob(row)) {
    ElMessage.warning('请先确认下发报价，再生成后审计记录')
    return
  }
  await runQuoteReviewAgent(row.job_id, {
    confirmedOnly: true,
    quoteHistoryId: row.history?.id,
    openAgentCenter: true,
  })
}

async function openAgentRun(row) {
  if (!row?.run_id) return
  agentCenterLoading.value = true
  agentLlmExplanation.value = null
  agentShowExplanation.value = false
  try {
    const response = await api.get(`/admin/agents/runs/${row.run_id}`)
    agentRunDetail.value = responseData(response)
    agentQuoteJobId.value = agentRunDetail.value?.target_id || ''
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'Agent 运行详情加载失败'))
  } finally {
    agentCenterLoading.value = false
  }
}

async function openAgentRunAndFocus(row) {
  await openAgentRun(row)
  await scrollToAgentResult()
}

async function scrollToAgentResult() {
  await nextTick()
  const target = document.querySelector('.agent-result-panel')
  if (target?.scrollIntoView) {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function agentRiskLabel(riskLevel) {
  const labels = {
    high: '高风险',
    medium: '中风险',
    low: '低风险',
  }
  return labels[riskLevel] || '未评估'
}

function agentRiskTagType(riskLevel) {
  if (riskLevel === 'high') return 'danger'
  if (riskLevel === 'medium') return 'warning'
  if (riskLevel === 'low') return 'success'
  return 'info'
}

function agentAuditRiskReasons(record) {
  const reasons = Array.isArray(record?.risk_reasons) ? record.risk_reasons : []
  const typeLabels = {
    manual_quantity_deviation: '人工工程量改动过大',
    manual_unit_price_deviation: '人工单价改动过大',
  }
  const labels = reasons
    .map((item) => item?.label || typeLabels[item?.type] || item?.type)
    .filter(Boolean)
  return labels.length ? labels.join('；') : '未记录明确预审风险'
}

function agentAuditRiskReasonItems(record) {
  const reasons = Array.isArray(record?.risk_reasons) ? record.risk_reasons : []
  const typeLabels = {
    no_cost_reference: '无成本参考',
    multiple_cost_candidates: '多成本候选',
    cost_price_delta: '偏离底价',
    manual_quantity_deviation: '工程量改动',
    manual_unit_price_deviation: '单价改动',
    manual_price_deviation: '价格改动',
    ai_rewrite_conflict: 'AI改写冲突',
    ai_note_conflict: '备注冲突',
    invalid_unit_price: '单价异常',
    invalid_total_price: '合计异常',
    requirement_match_risk: '需求匹配风险',
    cost_fallback_used: '底价兜底',
    missing_requirement_row: '疑似漏报价',
    preview_risk: '预审风险',
  }
  const items = reasons
    .map((item) => ({
      type: item?.type || item?.check_key || 'unknown',
      label: item?.label || typeLabels[item?.type] || item?.type || '预审风险',
      severity: item?.severity || 'medium',
    }))
    .filter((item) => item.label)
  return items.length ? items : [{ type: 'none', label: '未记录明确风险', severity: 'low' }]
}

function agentAuditQuantity(value, unit) {
  if (value === null || value === undefined || value === '') return '-'
  const number = Number(value)
  const text = Number.isFinite(number)
    ? number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
    : String(value)
  return unit ? `${text} ${unit}` : text
}

function agentAuditDeltaText(record, field) {
  const change = record?.price_change || {}
  const fieldMap = {
    quantity: ['quantity_delta', 'quantity_delta_rate'],
    unit_price: ['unit_price_delta', 'unit_price_delta_rate'],
    total_price: ['total_price_delta', 'total_price_delta_rate'],
  }
  const [deltaKey, rateKey] = fieldMap[field] || []
  const delta = Number(change[deltaKey])
  if (!Number.isFinite(delta) || Math.abs(delta) < 0.000001) return '-'
  let text = ''
  if (field === 'quantity') {
    text = agentSignedPlainNumber(delta, record?.unit)
  } else if (field === 'unit_price') {
    text = agentSignedCurrency(delta, true)
  } else {
    text = agentSignedCurrency(delta, false)
  }
  const rate = Number(change[rateKey])
  if (Number.isFinite(rate) && Math.abs(rate) >= 0.000001) {
    return `${text} / ${agentSignedPercent(rate)}`
  }
  return text
}

function agentSignedPlainNumber(value, suffix = '') {
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  const sign = number > 0 ? '+' : '-'
  const text = Math.abs(number).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  return `${sign}${text}${suffix || ''}`
}

function agentSignedCurrency(value, keepCents = false) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  const sign = number > 0 ? '+' : '-'
  const text = Math.abs(number).toLocaleString('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: keepCents ? 2 : 0,
    maximumFractionDigits: keepCents ? 2 : 0,
  })
  return `${sign}${text}`
}

function agentSignedPercent(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '-'
  const sign = number > 0 ? '+' : '-'
  return `${sign}${(Math.abs(number) * 100).toFixed(1)}%`
}

function agentAuditDeltaTagType(value) {
  const number = Number(value)
  if (!Number.isFinite(number) || Math.abs(number) < 0.000001) return 'info'
  return number > 0 ? 'warning' : 'success'
}

function agentAuditConfirmedState(record) {
  if (record?.confirmed_quote?.price_source === 'not_found_in_preview') return '未找到下发行'
  return record?.confirmed_quote?.manual_modified ? '人工已修改' : '沿用/未改'
}

function agentAuditMarketContext(record) {
  return record?.market_search_context && typeof record.market_search_context === 'object'
    ? record.market_search_context
    : {}
}

function agentAuditMarketSources(record) {
  const sources = agentAuditMarketContext(record).sources
  return Array.isArray(sources) ? sources.filter((item) => item?.url).slice(0, 8) : []
}

function agentAuditMarketSourceCount(record) {
  return agentAuditMarketSources(record).length
}

function agentAuditMarketCities(record) {
  const cities = agentAuditMarketContext(record).cities
  if (!cities || typeof cities !== 'object') return []
  return Object.entries(cities)
    .map(([name, value]) => ({
      name,
      text: agentAuditCitySummary(value),
    }))
    .filter((item) => item.text && item.text !== '-')
}

function agentAuditCitySummary(value) {
  if (!value || typeof value !== 'object') return '-'
  const range = value.price_range && typeof value.price_range === 'object' ? value.price_range : {}
  const hasMin = range.min !== null && range.min !== undefined && range.min !== ''
  const hasMax = range.max !== null && range.max !== undefined && range.max !== ''
  const min = hasMin ? Number(range.min) : Number.NaN
  const max = hasMax ? Number(range.max) : Number.NaN
  const unit = value.unit ? ` / ${value.unit}` : ''
  if (Number.isFinite(min) && Number.isFinite(max)) return `${formatPrice(min)} - ${formatPrice(max)}${unit}`
  if (Number.isFinite(min)) return `约 ${formatPrice(min)}${unit}`
  if (Number.isFinite(max)) return `最高 ${formatPrice(max)}${unit}`
  return value.summary || '-'
}

function agentMarketConfidenceLabel(confidence) {
  const labels = {
    high: '高可信',
    medium: '中可信',
    low: '低可信',
    none: '无来源',
  }
  return labels[confidence] || confidence || '未评估'
}

function agentMarketConfidenceTag(confidence) {
  if (confidence === 'high') return 'success'
  if (confidence === 'medium') return 'warning'
  if (confidence === 'low') return 'info'
  if (confidence === 'none') return 'danger'
  return 'info'
}

function agentAuditRecordRowClass({ row }) {
  if (row?.risk_level === 'high') return 'agent-audit-table-row-high'
  if (row?.risk_level === 'medium') return 'agent-audit-table-row-medium'
  return ''
}

function agentSeverityLabel(severity) {
  const labels = {
    high: '高',
    medium: '中',
    low: '低',
  }
  return labels[severity] || '提示'
}

function agentSeverityTagType(severity) {
  if (severity === 'high') return 'danger'
  if (severity === 'medium') return 'warning'
  if (severity === 'low') return 'success'
  return 'info'
}

function agentRecommendationLabel(recommendation) {
  const labels = {
    post_audit_recorded: '已留痕',
    manual_review_required: '必须人工复核',
    review_before_push: '下发前复核',
    spot_check_before_push: '抽查后下发',
    can_push_after_spot_check: '抽查后可下发',
  }
  return labels[recommendation] || '-'
}

function agentPriorityLabel(priority) {
  const labels = {
    high: '高优先级',
    medium: '中优先级',
    low: '低优先级',
  }
  return labels[priority] || '建议'
}

function agentPriorityTagType(priority) {
  if (priority === 'high') return 'danger'
  if (priority === 'medium') return 'warning'
  if (priority === 'low') return 'success'
  return 'info'
}

function agentSuggestionTypeLabel(type) {
  const labels = {
    price_adjustment: '调价建议',
    cost_saving_replacement: '省钱替代',
    risk_mitigation: '降风险',
    manual_price_completion: '人工补价',
  }
  return labels[type] || '建议'
}

function agentSuggestionStatusLabel(status) {
  const labels = {
    pending_review: '待确认',
    approved: '已采纳',
    rejected: '已拒绝',
    draft_generated: '草案已生成',
    agent_result_confirmed: '人工已确认',
    human_modified: '人工另改',
  }
  return labels[status] || status || '-'
}

function agentSuggestionStatusTagType(status) {
  if (status === 'agent_result_confirmed') return 'success'
  if (status === 'draft_generated' || status === 'approved') return 'primary'
  if (status === 'rejected' || status === 'human_modified') return 'warning'
  return 'info'
}

function agentSchedulerStatusLabel(status) {
  const labels = {
    disabled: '未启用',
    not_due: '未到时间',
    pending: '待自动执行',
    running: '执行中',
    success: '已自动执行',
    failed: '执行失败',
    missed: '已错过',
    skipped: '已跳过',
  }
  return labels[status] || '未记录'
}

function agentSchedulerStatusTagType(status) {
  if (status === 'success') return 'success'
  if (status === 'failed' || status === 'missed') return 'warning'
  if (status === 'running' || status === 'pending') return 'primary'
  return 'info'
}

function agentSchedulerNextActionLabel(action) {
  const labels = {
    enable_feature_flags: '等待启用',
    wait_for_run_time: '等待到点',
    scheduler_will_run: '即将自动执行',
    wait_for_finish: '执行中',
    handle_pending_suggestions: '查看结果',
    manual_rescan_available: '查看结果',
    check_result: '查看结果',
  }
  return labels[action] || '-'
}

function agentTodoStatusLabel(status) {
  const labels = {
    action_required: '需要处理',
    waiting: '等待自动复核',
    clear: '暂无待处理项',
  }
  return labels[status] || '后审计状态'
}

function agentTodoSeverityTagType(severity) {
  if (severity === 'critical') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'info'
}

function agentTodoPrimaryActionLabel(action) {
  const labels = {
    manual_rescan: '刷新结果',
    review_high_risk: '打开风险单',
    open_pending_suggestions: '查看记录',
    continue_suggestion_loop: '查看记录',
    wait_for_scheduler: '刷新状态',
    none: '刷新',
  }
  return labels[action] || '查看'
}

async function handleAgentTodoPrimaryAction() {
  const action = agentTodoSummary.value?.primary_action
  if (action === 'manual_rescan') {
    await refreshAgentDailyReview()
    return
  }
  if (action === 'wait_for_scheduler' || action === 'none') {
    await refreshAgentDailyReview()
    return
  }
  if (action === 'review_high_risk') {
    if (!agentRuns.value.length) {
      await loadAgentRuns()
    }
    const row = agentRuns.value.find((item) => item.risk_level === 'high') || agentRuns.value[0]
    if (row) {
      await openAgentRunAndFocus(row)
      return
    }
  }
  if (action === 'open_pending_suggestions') {
    const firstPending = agentPendingSuggestions.value[0]
    if (firstPending?.run) {
      await openAgentRunAndFocus(firstPending.run)
      return
    }
  }
  const target = document.querySelector('.agent-result-panel') || document.querySelector('.agent-pending-actions')
  if (target?.scrollIntoView) {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

async function refreshAgentRunDetail() {
  if (!agentRunDetail.value?.run_id) return
  const response = await api.get(`/admin/agents/runs/${agentRunDetail.value.run_id}`)
  agentRunDetail.value = responseData(response)
  if (agentShowExplanation.value) {
    await loadAgentLlmExplanation(agentLlmExplanation.value?.mode === 'deepseek' ? 'deepseek' : 'rule')
  }
  await loadAgentRuns()
  if (canManageAgentDailyReview.value) {
    await refreshAgentDailyReview()
  }
}

async function decideAgentSuggestion(suggestion, decision) {
  const title = decision === 'approve' ? '采纳 Agent 建议' : '拒绝 Agent 建议'
  try {
    const result = await ElMessageBox.prompt('请填写确认说明（可简短填写）', title, {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputPlaceholder: decision === 'approve' ? '例如：采纳该调价草案' : '例如：现场条件不一致，暂不采纳',
    })
    await api.post(`/admin/agents/suggestions/${suggestion.suggestion_id}/decision`, {
      decision,
      note: result.value || '',
    })
    await refreshAgentRunDetail()
    ElMessage.success(decision === 'approve' ? '已采纳建议' : '已拒绝建议')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '建议确认失败'))
  }
}

function isAgentActionableSuggestion(suggestion) {
  return ['price_adjustment', 'cost_saving_replacement'].includes(suggestion?.suggestion_type)
}

async function adoptAgentSuggestionOneClick(suggestion) {
  if (!suggestion?.suggestion_id) return
  try {
    await ElMessageBox.confirm(
      '确认采用该建议后，系统会自动完成采纳、生成草案和确认记录；不会直接修改报价单。',
      '一键采用建议',
      {
        confirmButtonText: '确认采用',
        cancelButtonText: '取消',
        type: 'info',
      }
    )
    let current = suggestion
    if (current.status === 'pending_review') {
      const decisionResponse = await api.post(`/admin/agents/suggestions/${current.suggestion_id}/decision`, {
        decision: 'approve',
        note: '一键采用建议',
      })
      current = responseData(decisionResponse) || current
    }
    if (current.status === 'approved') {
      const executeResponse = await api.post(`/admin/agents/suggestions/${current.suggestion_id}/execute`, {
        note: '一键生成草案',
      })
      current = responseData(executeResponse) || current
    }
    if (current.status === 'draft_generated') {
      await api.post(`/admin/agents/suggestions/${current.suggestion_id}/final-confirm`, {
        accepted_agent_result: true,
        final_result: { accepted_patch: current.execution_result?.quote_line_patch || null },
        note: '一键确认采用建议',
      })
    }
    await refreshAgentRunDetail()
    ElMessage.success('已一键采用并记录')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '一键采用失败'))
  }
}

async function markAgentSuggestionReviewed(suggestion) {
  if (!suggestion?.suggestion_id) return
  try {
    await ElMessageBox.confirm('确认该风险已人工查看，不生成草案。', '标记已处理', {
      confirmButtonText: '标记已处理',
      cancelButtonText: '取消',
      type: 'info',
    })
    await api.post(`/admin/agents/suggestions/${suggestion.suggestion_id}/decision`, {
      decision: 'reject',
      note: '风险已人工查看，不生成草案',
    })
    await refreshAgentRunDetail()
    ElMessage.success('已标记处理')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '标记处理失败'))
  }
}

async function executeAgentSuggestion(suggestion) {
  try {
    await ElMessageBox.confirm('Agent 将只生成调整草案，不会直接修改报价单。', '生成执行草案', {
      confirmButtonText: '生成草案',
      cancelButtonText: '取消',
      type: 'info',
    })
    await api.post(`/admin/agents/suggestions/${suggestion.suggestion_id}/execute`, {
      note: '由 AI助手中心生成草案',
    })
    await refreshAgentRunDetail()
    ElMessage.success('草案已生成')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '草案生成失败'))
  }
}

async function finalConfirmAgentSuggestion(suggestion, acceptedAgentResult) {
  const title = acceptedAgentResult ? '确认采用 Agent 草案' : '记录人工另改结果'
  try {
    const result = await ElMessageBox.prompt('请填写最终确认说明', title, {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputPlaceholder: acceptedAgentResult ? '例如：已核对，采用草案' : '例如：人工调整为其他单价',
    })
    await api.post(`/admin/agents/suggestions/${suggestion.suggestion_id}/final-confirm`, {
      accepted_agent_result: acceptedAgentResult,
      final_result: acceptedAgentResult
        ? { accepted_patch: suggestion.execution_result?.quote_line_patch || null }
        : { manual_modified: true },
      note: result.value || '',
    })
    await refreshAgentRunDetail()
    ElMessage.success(acceptedAgentResult ? '已确认 Agent 草案' : '已记录人工另改')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '最终确认失败'))
  }
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

function positiveId(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : null
}

function firstPositiveId(...values) {
  for (const value of values) {
    const id = positiveId(value)
    if (id) return id
  }
  return null
}

function costEvidenceReference(row) {
  return row?.cost_reference && typeof row.cost_reference === 'object' ? row.cost_reference : {}
}

function costEvidenceSnapshot(row) {
  return row?.cost_item_snapshot && typeof row.cost_item_snapshot === 'object' ? row.cost_item_snapshot : {}
}

function costEvidenceSourceItem(row) {
  const reference = costEvidenceReference(row)
  return reference.source_cost_item && typeof reference.source_cost_item === 'object' ? reference.source_cost_item : {}
}

function enterpriseQuotaItemIdFromApiUrl(row) {
  const reference = costEvidenceReference(row)
  const match = String(reference.evidence_api_url || '').match(/\/cost-master\/quota-items\/(\d+)/)
  return match ? positiveId(match[1]) : null
}

function enterpriseQuotaItemIdFromCostEvidence(row) {
  const reference = costEvidenceReference(row)
  const sourceItem = costEvidenceSourceItem(row)
  const snapshot = costEvidenceSnapshot(row)
  return firstPositiveId(
    row?.enterprise_quota_item_id,
    reference.enterprise_quota_item_id,
    sourceItem.enterprise_quota_item_id,
    snapshot.enterprise_quota_item_id,
    enterpriseQuotaItemIdFromApiUrl(row),
  )
}

function costEvidenceIsEnterpriseQuota(row) {
  const reference = costEvidenceReference(row)
  const sourceItem = costEvidenceSourceItem(row)
  const snapshot = costEvidenceSnapshot(row)
  return row?.reference_source === ENTERPRISE_QUOTA_REFERENCE_SOURCE
    || reference.reference_source === ENTERPRISE_QUOTA_REFERENCE_SOURCE
    || sourceItem.reference_source === ENTERPRISE_QUOTA_REFERENCE_SOURCE
    || snapshot.reference_source === ENTERPRISE_QUOTA_REFERENCE_SOURCE
    || row?.source_type === 'enterprise_quota_item'
    || reference.source_type === 'enterprise_quota_item'
    || Boolean(enterpriseQuotaItemIdFromCostEvidence(row))
}

function costEvidenceOpenId(row) {
  if (costEvidenceIsEnterpriseQuota(row)) return enterpriseQuotaItemIdFromCostEvidence(row)
  const reference = costEvidenceReference(row)
  const snapshot = costEvidenceSnapshot(row)
  return firstPositiveId(row?.cost_item_id, reference.cost_item_id, snapshot.id)
}

function costEvidenceButtonLabel(row) {
  return costEvidenceIsEnterpriseQuota(row) ? '定额主项' : '成本条目'
}

function openCostEvidenceItem(row) {
  const id = costEvidenceOpenId(row)
  if (!id) return
  if (costEvidenceIsEnterpriseQuota(row)) {
    openEnterpriseQuotaItemDetail({ id })
    return
  }
  openCostItemDetail({ id })
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

async function loadProjectUsers() {
  if (!canManageProjectProgress.value || projectUsers.value.length) return
  try {
    const response = await api.get('/admin/projects/users')
    projectUsers.value = responseData(response) || []
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目人员加载失败'))
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

function isBiddingFeatureDisabled(error) {
  return error.response?.status === 404 && error.response?.data?.detail === 'NOT_FOUND'
}

function isBiddingNoParseRun(error) {
  return error.response?.status === 404 && error.response?.data?.detail === 'BID_PARSE_RUN_NOT_FOUND'
}

function resetBiddingDialog() {
  biddingDialog.file = null
  biddingDialog.form.project_name = ''
  biddingDialog.form.tenderer_name = ''
  biddingDialog.form.tender_agency = ''
  biddingDialog.form.project_location = ''
  biddingDialog.form.project_type = ''
  biddingDialog.form.tender_deadline_at = ''
  biddingProjectUploadRef.value?.clearFiles?.()
}

function openBiddingTenderUpload() {
  resetBiddingDialog()
  biddingDialog.visible = true
}

function isPrimaryTenderFile(file) {
  const filename = String(file?.name || '').toLowerCase()
  return filename.endsWith('.pdf') || filename.endsWith('.docx')
}

function tenderProjectNameFromFile(file) {
  const filename = String(file?.name || '').trim()
  const stem = filename.replace(/\.[^.]+$/, '').trim()
  return stem || '未命名招标项目'
}

function handleBiddingProjectFileChange(uploadFile) {
  const file = uploadFile?.raw || uploadFile || null
  if (file && !isPrimaryTenderFile(file)) {
    ElMessage.warning('首版仅支持甲方招标文件 PDF 或 Word(.docx)')
    biddingDialog.file = null
    biddingProjectUploadRef.value?.clearFiles?.()
    return
  }
  biddingDialog.file = file
}

function clearBiddingProjectFile() {
  biddingDialog.file = null
}

async function saveBiddingProject() {
  if (!biddingDialog.file) {
    ElMessage.warning('请先选择甲方招标文件')
    return
  }
  if (!isPrimaryTenderFile(biddingDialog.file)) {
    ElMessage.warning('首版仅支持甲方招标文件 PDF 或 Word(.docx)')
    return
  }
  const form = new FormData()
  form.append('file', biddingDialog.file)
  const projectName = biddingDialog.form.project_name.trim()
  if (projectName) form.append('project_name', projectName)
  const tendererName = biddingDialog.form.tenderer_name.trim()
  if (tendererName) form.append('tenderer_name', tendererName)
  const tenderAgency = biddingDialog.form.tender_agency.trim()
  if (tenderAgency) form.append('tender_agency', tenderAgency)
  const projectLocation = biddingDialog.form.project_location.trim()
  if (projectLocation) form.append('project_location', projectLocation)
  const projectType = biddingDialog.form.project_type.trim()
  if (projectType) form.append('project_type', projectType)
  if (biddingDialog.form.tender_deadline_at) {
    form.append('tender_deadline_at', biddingDialog.form.tender_deadline_at)
  }
  biddingDialog.loading = true
  try {
    let payload = null
    try {
      const response = await api.post('/admin/bidding/projects/from-tender-file', form)
      payload = responseData(response) || {}
    } catch (error) {
      if (![404, 405].includes(error.response?.status)) throw error
      payload = await createBiddingProjectWithLegacyUpload()
    }
    const project = payload.project
    biddingDialog.visible = false
    ElMessage.success('招标文件已上传，投标项目已创建')
    biddingProjectUploadRef.value?.clearFiles?.()
    await loadBiddingProjects()
    if (project?.project_uuid) {
      await openBiddingProjectDetail(project)
    }
  } catch (error) {
    if (isBiddingFeatureDisabled(error)) {
      biddingFeatureDisabled.value = true
      biddingDialog.visible = false
      return
    }
    ElMessage.error(apiErrorMessage(error, '创建投标项目失败'))
  } finally {
    biddingDialog.loading = false
  }
}

async function createBiddingProjectWithLegacyUpload() {
  const createResponse = await api.post('/admin/bidding/projects', {
    project_name: biddingDialog.form.project_name.trim() || tenderProjectNameFromFile(biddingDialog.file),
    tenderer_name: biddingDialog.form.tenderer_name.trim() || null,
    tender_agency: biddingDialog.form.tender_agency.trim() || null,
    project_location: biddingDialog.form.project_location.trim() || null,
    project_type: biddingDialog.form.project_type.trim() || null,
    tender_deadline_at: biddingDialog.form.tender_deadline_at || null,
  })
  const project = responseData(createResponse)
  const uploadForm = new FormData()
  uploadForm.append('file', biddingDialog.file)
  uploadForm.append('file_type', 'tender_document')
  const uploadResponse = await api.post(`/admin/bidding/projects/${project.project_uuid}/files`, uploadForm)
  const detailResponse = await api.get(`/admin/bidding/projects/${project.project_uuid}`)
  return {
    project: responseData(detailResponse),
    file: responseData(uploadResponse),
  }
}

async function loadBiddingProjects() {
  if (!canViewBidding.value) return
  biddingFeatureDisabled.value = false
  biddingLoading.value = true
  const params = {
    page: biddingProjectPage.value,
    page_size: biddingProjectPageSize,
  }
  if (biddingFilters.status) params.status = biddingFilters.status
  const keyword = biddingFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  try {
    const response = await api.get('/admin/bidding/projects', { params })
    biddingProjects.value = responseData(response) || []
    biddingProjectTotal.value = response.data?.total ?? biddingProjects.value.length
  } catch (error) {
    biddingProjects.value = []
    biddingProjectTotal.value = 0
    if (isBiddingFeatureDisabled(error)) {
      biddingFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '投标项目加载失败'))
  } finally {
    biddingLoading.value = false
  }
}

function applyBiddingFilters() {
  biddingProjectPage.value = 1
  loadBiddingProjects()
}

function currentBiddingProjectUuid() {
  return biddingDrawer.project?.project_uuid || ''
}

async function openBiddingProjectDetail(row, tab = 'files') {
  if (!row?.project_uuid) return
  const nextTab = tab === 'bidDraft' ? 'businessBidDraft' : tab
  biddingDrawer.visible = true
  biddingDrawer.loading = true
  biddingDrawer.project = row
  biddingDrawer.activeTab = nextTab
  biddingFiles.value = []
  biddingParseRuns.value = []
  biddingTenderAnalysis.value = null
  biddingTenderAnalysisTab.value = 'summary'
  biddingTenderReviewWorkbenchExpanded.value = true
  biddingImportantInfoExpandedKeys.value = []
  biddingTenderScoringExpandedKeys.value = []
  biddingRiskClause.value = null
  biddingBusinessObjectCollapse.value = []
  biddingBusinessObjects.value = []
  biddingBusinessObjectsTotal.value = 0
  biddingBusinessObjectsSummary.value = {}
  biddingResponseItems.value = []
  biddingResponseItemsTotal.value = 0
  biddingResponseMatrixSummary.value = {}
  biddingFileFormatPlan.value = null
  biddingMaterialRequirements.value = []
  biddingMaterialRequirementSummary.value = {}
  biddingTechnicalComposition.value = null
  biddingDraftOutline.value = null
  biddingDraftSections.value = []
  biddingDraftPreviewDrawer.visible = false
  biddingDraftPreviewDrawer.draft = null
  biddingDraftPreviewDrawer.editing = false
  biddingDraftPreviewDrawer.editContent = ''
  biddingDraftPreviewDrawer.saving = false
  biddingDraftPreviewDrawer.llmGenerating = false
  biddingTechnicalFinalQualityDrawer.visible = false
  biddingTechnicalFinalQualityDrawer.report = null
  biddingRequirements.value = []
  biddingRequirementsTotal.value = 0
  biddingRisks.value = []
  biddingRisksTotal.value = 0
  biddingRiskCards.value = []
  biddingRiskCardsSummary.value = {}
  try {
    await loadBiddingProjectDetail(row.project_uuid)
    await Promise.all([
      loadBiddingFiles(row.project_uuid),
      loadBiddingParseRuns(row.project_uuid),
      loadBiddingTenderAnalysis(row.project_uuid),
      loadBiddingRiskClause(row.project_uuid),
      loadBiddingBusinessObjects(row.project_uuid),
      loadBiddingResponseMatrix(row.project_uuid),
      loadBiddingFileFormatPlan(row.project_uuid),
      loadBiddingTechnicalComposition(row.project_uuid),
      loadBiddingMaterialRequirements(row.project_uuid),
      loadBiddingDraftOutline(row.project_uuid),
      loadBiddingDraftSections(row.project_uuid),
      loadBiddingRequirements(row.project_uuid),
      loadBiddingRiskCards(row.project_uuid),
      loadBiddingRisks(row.project_uuid),
    ])
  } finally {
    biddingDrawer.loading = false
  }
}

async function refreshBiddingProjectDetail() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid) return
  await Promise.all([
    loadBiddingProjectDetail(projectUuid),
    loadBiddingFiles(projectUuid),
    loadBiddingParseRuns(projectUuid),
    loadBiddingTenderAnalysis(projectUuid),
    loadBiddingBusinessObjects(projectUuid),
    loadBiddingResponseMatrix(projectUuid),
    loadBiddingFileFormatPlan(projectUuid),
    loadBiddingTechnicalComposition(projectUuid),
    loadBiddingMaterialRequirements(projectUuid),
    loadBiddingDraftOutline(projectUuid),
    loadBiddingDraftSections(projectUuid),
    loadBiddingRequirements(projectUuid),
    loadBiddingRiskCards(projectUuid),
    loadBiddingRisks(projectUuid),
  ])
}

async function loadBiddingProjectDetail(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}`)
    biddingDrawer.project = responseData(response)
  } catch (error) {
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '投标项目详情加载失败'))
  }
}

async function loadBiddingFiles(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/files`)
    biddingFiles.value = responseData(response) || []
  } catch (error) {
    biddingFiles.value = []
    if (!isBiddingFeatureDisabled(error)) ElMessage.error(apiErrorMessage(error, '投标资料加载失败'))
  }
}

async function loadBiddingParseRuns(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/parse-runs`)
    biddingParseRuns.value = responseData(response) || []
  } catch (error) {
    biddingParseRuns.value = []
    if (!isBiddingFeatureDisabled(error)) ElMessage.error(apiErrorMessage(error, '解析版本加载失败'))
  }
}

function clearBiddingImportantInfoProgressTimer() {
  if (biddingImportantInfoProgressTimer) {
    clearInterval(biddingImportantInfoProgressTimer)
    biddingImportantInfoProgressTimer = null
  }
}

function startBiddingImportantInfoProgress(stage = '准备解析招标文件') {
  clearBiddingImportantInfoProgressTimer()
  Object.assign(biddingImportantInfoProgress, {
    visible: true,
    percentage: 6,
    status: 'active',
    stage,
    detail: '读取已上传文件和解析版本',
  })
  biddingImportantInfoProgressTimer = setInterval(() => {
    if (biddingImportantInfoProgress.status !== 'active') return
    const current = Number(biddingImportantInfoProgress.percentage || 0)
    if (current < 88) biddingImportantInfoProgress.percentage = current + (current < 45 ? 2 : 1)
  }, 900)
}

function updateBiddingImportantInfoProgress(percentage, stage, detail = '') {
  if (!biddingImportantInfoProgress.visible) startBiddingImportantInfoProgress(stage)
  biddingImportantInfoProgress.percentage = Math.max(Number(biddingImportantInfoProgress.percentage || 0), Number(percentage || 0))
  biddingImportantInfoProgress.stage = stage || biddingImportantInfoProgress.stage
  biddingImportantInfoProgress.detail = detail || biddingImportantInfoProgress.detail
  biddingImportantInfoProgress.status = 'active'
}

function finishBiddingImportantInfoProgress(success, detail = '') {
  clearBiddingImportantInfoProgressTimer()
  Object.assign(biddingImportantInfoProgress, {
    visible: true,
    percentage: success ? 100 : Math.max(Number(biddingImportantInfoProgress.percentage || 0), 92),
    status: success ? 'success' : 'error',
    stage: success ? '结构化信息摘要表已生成' : '结构化提取未完成',
    detail: detail || (success ? '可在页面预览，也可导出 Word' : '请查看错误信息后重试'),
  })
}

function clearBiddingRiskClauseProgressTimer() {
  if (biddingRiskClauseProgressTimer) {
    clearInterval(biddingRiskClauseProgressTimer)
    biddingRiskClauseProgressTimer = null
  }
}

function startBiddingRiskClauseProgress(stage = '准备风险分析') {
  clearBiddingRiskClauseProgressTimer()
  Object.assign(biddingRiskClauseProgress, {
    visible: true,
    percentage: 6,
    status: 'active',
    stage,
    detail: '准备调用风险条款专用提示词',
  })
  biddingRiskClauseProgressTimer = setInterval(() => {
    if (biddingRiskClauseProgress.status !== 'active') return
    const current = Number(biddingRiskClauseProgress.percentage || 0)
    if (current < 88) biddingRiskClauseProgress.percentage = current + (current < 45 ? 2 : 1)
  }, 900)
}

function updateBiddingRiskClauseProgress(percentage, stage, detail = '') {
  if (!biddingRiskClauseProgress.visible) startBiddingRiskClauseProgress(stage)
  biddingRiskClauseProgress.percentage = Math.max(Number(biddingRiskClauseProgress.percentage || 0), Number(percentage || 0))
  biddingRiskClauseProgress.stage = stage || biddingRiskClauseProgress.stage
  biddingRiskClauseProgress.detail = detail || biddingRiskClauseProgress.detail
  biddingRiskClauseProgress.status = 'active'
}

function finishBiddingRiskClauseProgress(success, detail = '') {
  clearBiddingRiskClauseProgressTimer()
  Object.assign(biddingRiskClauseProgress, {
    visible: true,
    percentage: success ? 100 : Math.max(Number(biddingRiskClauseProgress.percentage || 0), 92),
    status: success ? 'success' : 'error',
    stage: success ? '风险条款清单已生成' : '风险分析未完成',
    detail: detail || (success ? '可在页面预览，也可导出风险 Word' : '请查看错误信息后重试'),
  })
}

async function loadBiddingTenderAnalysis(projectUuid = currentBiddingProjectUuid(), options = {}) {
  if (!projectUuid) return
  const trackProgress = Boolean(options.trackProgress || biddingImportantInfoProgress.visible)
  if (trackProgress) updateBiddingImportantInfoProgress(55, '调用LLM结构化提取', '发送提示词、output_schema 和招标文件原文片段')
  biddingTenderAnalysisLoading.value = true
  biddingImportantInfoExpandedKeys.value = []
  biddingTenderScoringExpandedKeys.value = []
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/tender-analysis/preview`, {
      params: { run_uuid: 'latest' },
    })
    const payload = responseData(response) || null
    biddingTenderAnalysis.value = payload
    if (trackProgress) {
      const status = payload?.important_info?.status
      const metadata = payload?.important_info?.metadata || {}
      if (['completed', 'cached'].includes(status)) {
        finishBiddingImportantInfoProgress(true, status === 'cached' ? '已读取缓存的LLM结构化结果' : 'LLM结构化结果已返回并入表')
      } else if (status === 'error') {
        finishBiddingImportantInfoProgress(false, metadata.error || 'LLM结构化提取失败')
      } else {
        finishBiddingImportantInfoProgress(false, metadata.skip_reason || status || 'LLM结构化结果未生成')
      }
    }
  } catch (error) {
    biddingTenderAnalysis.value = null
    if (trackProgress) finishBiddingImportantInfoProgress(false, apiErrorMessage(error, '招标分析成果表加载失败'))
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '招标分析成果表加载失败'))
  } finally {
    biddingTenderAnalysisLoading.value = false
  }
}

async function loadBiddingRiskClause(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  biddingRiskClauseLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/risk-clause/preview`, {
      params: { run_uuid: biddingTenderAnalysis.value?.run_uuid || 'latest' },
    })
    biddingRiskClause.value = responseData(response) || null
  } catch (error) {
    biddingRiskClause.value = null
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '风险条款清单加载失败'))
  } finally {
    biddingRiskClauseLoading.value = false
  }
}

async function analyzeBiddingRiskClause() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingRiskClauseAnalyzing.value) return
  biddingRiskClauseAnalyzing.value = true
  startBiddingRiskClauseProgress('准备风险分析')
  try {
    updateBiddingRiskClauseProgress(32, '调用LLM风险分析', '发送风险条款专用提示词和招标文件原文片段')
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/risk-clause/analyze`, null, {
      params: { run_uuid: biddingTenderAnalysis.value?.run_uuid || 'latest', force: true },
    })
    biddingRiskClause.value = responseData(response) || null
    const status = biddingRiskClause.value?.status
    const metadata = biddingRiskClause.value?.metadata || {}
    if (['completed', 'cached'].includes(status)) {
      finishBiddingRiskClauseProgress(true, `已生成 ${biddingTenderRiskClauseRows.value.length} 条风险条款`)
      biddingTenderAnalysisTab.value = 'risk_clause'
      ElMessage.success('风险条款清单已生成')
    } else {
      finishBiddingRiskClauseProgress(false, metadata.error || metadata.skip_reason || status || '风险条款清单未生成')
      ElMessage.warning(metadata.error || metadata.skip_reason || '风险条款清单未生成')
    }
  } catch (error) {
    finishBiddingRiskClauseProgress(false, apiErrorMessage(error, '风险分析失败'))
    ElMessage.error(apiErrorMessage(error, '风险分析失败'))
  } finally {
    biddingRiskClauseAnalyzing.value = false
  }
}

function filenameFromDisposition(disposition, fallback) {
  const raw = String(disposition || '')
  const encodedMatch = raw.match(/filename\*=UTF-8''([^;]+)/i)
  if (encodedMatch?.[1]) {
    try {
      return decodeURIComponent(encodedMatch[1])
    } catch {
      return encodedMatch[1]
    }
  }
  const match = raw.match(/filename="?([^";]+)"?/i)
  return match?.[1] || fallback
}

function downloadBlob(data, { filename, mimeType }) {
  const blob = new Blob([data], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function openBiddingAnalysisTable(tableKey) {
  const tabMap = {
    summary: 'summary',
    scoring: 'scoring',
    risk_clause: 'risk_clause',
  }
  biddingTenderAnalysisTab.value = tabMap[tableKey] || 'review_queue'
  nextTick(() => {
    const target = biddingAnalysisTabsRef.value?.$el || document.querySelector('.bidding-analysis-tabs')
    target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  })
  const labels = {
    summary: '结构化信息摘要表',
    scoring: '评分细则表',
    risk_clause: '风险条款清单',
    review_queue: '待复核队列',
  }
  ElMessage.info(`已切换到${labels[biddingTenderAnalysisTab.value] || '对应表'}`)
}

async function exportBiddingTenderAnalysis() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingTenderAnalysisExporting.value) return
  biddingTenderAnalysisExporting.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/tender-analysis/export`, {
      params: { run_uuid: biddingTenderAnalysis.value?.run_uuid || 'latest' },
      responseType: 'blob',
    })
    const fallback = `${biddingDrawer.project?.project_name || '招标文件'}_投标重要信息提取.docx`
    const filename = filenameFromDisposition(response.headers?.['content-disposition'], fallback)
    downloadBlob(response.data, {
      filename,
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    ElMessage.success('投标重要信息提取 Word 已导出')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '投标重要信息提取 Word 导出失败'))
  } finally {
    biddingTenderAnalysisExporting.value = false
  }
}

async function exportBiddingRiskClause() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingRiskClauseExporting.value) return
  biddingRiskClauseExporting.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/risk-clause/export`, {
      params: { run_uuid: biddingTenderAnalysis.value?.run_uuid || 'latest' },
      responseType: 'blob',
    })
    const fallback = `${biddingDrawer.project?.project_name || '招标文件'}_风险条款清单.docx`
    const filename = filenameFromDisposition(response.headers?.['content-disposition'], fallback)
    downloadBlob(response.data, {
      filename,
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    ElMessage.success('风险条款清单 Word 已导出')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '风险条款清单 Word 导出失败'))
  } finally {
    biddingRiskClauseExporting.value = false
  }
}

async function loadBiddingBusinessObjects(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/business-objects`, {
      params: { page: 1, page_size: 300 },
    })
    biddingBusinessObjects.value = responseData(response) || []
    biddingBusinessObjectsTotal.value = response.data?.total ?? biddingBusinessObjects.value.length
    biddingBusinessObjectsSummary.value = response.data?.summary || {}
  } catch (error) {
    biddingBusinessObjects.value = []
    biddingBusinessObjectsTotal.value = 0
    biddingBusinessObjectsSummary.value = {}
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '业务对象加载失败'))
  }
}

async function loadBiddingResponseMatrix(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/response-matrix`, {
      params: { page: 1, page_size: 500 },
    })
    biddingResponseItems.value = responseData(response) || []
    biddingResponseItemsTotal.value = response.data?.total ?? biddingResponseItems.value.length
    biddingResponseMatrixSummary.value = response.data?.summary || {}
  } catch (error) {
    biddingResponseItems.value = []
    biddingResponseItemsTotal.value = 0
    biddingResponseMatrixSummary.value = {}
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '响应矩阵加载失败'))
  }
}

async function loadBiddingFileFormatPlan(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  biddingFileFormatLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/format-plan`, {
      params: { run_uuid: 'latest' },
    })
    biddingFileFormatPlan.value = responseData(response) || null
    biddingFileFormatPendingEvents.value = []
  } catch (error) {
    biddingFileFormatPlan.value = null
    biddingFileFormatPendingEvents.value = []
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '投标文件格式加载失败'))
  } finally {
    biddingFileFormatLoading.value = false
  }
}

async function generateBiddingFileFormatPlan() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingFileFormatGenerating.value) return
  biddingFileFormatGenerating.value = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/format-plan/generate`, {
      run_uuid: 'latest',
    })
    biddingFileFormatPlan.value = responseData(response) || null
    biddingFileFormatPendingEvents.value = []
    biddingMaterialRequirements.value = []
    biddingMaterialRequirementSummary.value = {}
    ElMessage.success('投标文件格式确认表已生成')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '投标文件格式生成失败'))
  } finally {
    biddingFileFormatGenerating.value = false
  }
}

async function confirmBiddingFileFormatPlan() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingFileFormatConfirming.value) return
  try {
    await ElMessageBox.confirm(
      '确认后，后续投标书草稿会优先按当前文件包结构生成。请先核对分册、表单、附件和装订密封要求。',
      '确认投标文件格式',
      {
        confirmButtonText: '确认格式',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  biddingFileFormatConfirming.value = true
  try {
    if (!biddingFileFormatPlan.value?.plan_uuid) {
      const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/format-plan/generate`, {
        run_uuid: 'latest',
      })
      biddingFileFormatPlan.value = responseData(response) || null
    }
    const planUuid = biddingFileFormatPlan.value?.plan_uuid
    if (!planUuid) throw new Error('BID_FILE_FORMAT_PLAN_NOT_FOUND')
    const response = await api.patch(`/admin/bidding/bid-draft/format-plan/${planUuid}/confirm`, {
      structure: biddingFileFormatPlan.value?.structure || {},
      reviewer_note: '人工确认投标文件格式',
      edit_events: biddingFileFormatPendingEvents.value,
    })
    biddingFileFormatPlan.value = responseData(response) || null
    biddingFileFormatPendingEvents.value = []
    await loadBiddingMaterialRequirements(projectUuid)
    ElMessage.success('投标文件格式已确认')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '投标文件格式确认失败'))
  } finally {
    biddingFileFormatConfirming.value = false
  }
}

async function loadBiddingTechnicalComposition(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  biddingTechnicalCompositionLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/technical-composition`, {
      params: { run_uuid: 'latest' },
    })
    biddingTechnicalComposition.value = responseData(response) || null
  } catch (error) {
    biddingTechnicalComposition.value = null
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '技术标组成识别结果加载失败'))
  } finally {
    biddingTechnicalCompositionLoading.value = false
  }
}

async function generateBiddingTechnicalComposition() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingTechnicalCompositionGenerating.value) return
  biddingTechnicalCompositionGenerating.value = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/technical-composition/generate`, {
      run_uuid: 'latest',
    })
    biddingTechnicalComposition.value = responseData(response) || null
    const generation = response.data?.generation || {}
    await loadBiddingMaterialRequirements(projectUuid, 'technical')
    ElMessage.success(`技术标组成已识别：自动匹配 ${generation.auto_submitted_count || 0} 项，待补 ${generation.missing_count || 0} 项`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '技术标组成识别失败'))
  } finally {
    biddingTechnicalCompositionGenerating.value = false
  }
}

async function loadBiddingMaterialRequirements(projectUuid = currentBiddingProjectUuid(), packageKey = undefined) {
  if (!projectUuid) return
  const scopePackageKey = packageKey === undefined ? biddingDraftPackageKey.value : packageKey
  biddingMaterialRequirementsLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/material-requirements`, {
      params: { run_uuid: 'latest', package_key: scopePackageKey || undefined },
    })
    biddingMaterialRequirements.value = responseData(response) || []
    biddingMaterialRequirementSummary.value = response.data?.summary || {}
  } catch (error) {
    biddingMaterialRequirements.value = []
    biddingMaterialRequirementSummary.value = {}
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '资料需求清单加载失败'))
  } finally {
    biddingMaterialRequirementsLoading.value = false
  }
}

async function generateBiddingMaterialRequirements() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingMaterialRequirementsGenerating.value) return
  if (biddingDraftPackageKey.value === 'technical') {
    await generateBiddingTechnicalComposition()
    return
  }
  biddingMaterialRequirementsGenerating.value = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/material-requirements/generate`, {
      run_uuid: 'latest',
      package_key: biddingDraftPackageKey.value || undefined,
    })
    biddingMaterialRequirements.value = responseData(response) || []
    biddingMaterialRequirementSummary.value = response.data?.summary || {}
    const generation = response.data?.generation || {}
    ElMessage.success(`资料需求清单已生成：新增 ${generation.created_count || 0} 条，刷新 ${generation.refreshed_count || 0} 条`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '资料需求清单生成失败'))
  } finally {
    biddingMaterialRequirementsGenerating.value = false
  }
}

async function updateBiddingMaterialRequirement(row, patch, successMessage = '资料需求状态已更新') {
  if (!row?.requirement_uuid || biddingMaterialRequirementUpdatingUuid.value) return false
  biddingMaterialRequirementUpdatingUuid.value = row.requirement_uuid
  try {
    await api.patch(`/admin/bidding/bid-draft/material-requirements/${row.requirement_uuid}`, patch)
    await loadBiddingMaterialRequirements(undefined, row.package_key || undefined)
    ElMessage.success(successMessage)
    return true
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '资料需求更新失败'))
    return false
  } finally {
    biddingMaterialRequirementUpdatingUuid.value = ''
  }
}

async function useBiddingMaterialCandidate(row) {
  const itemUuid = row?.candidate_profile_item_uuid || row?.candidate_profile_item?.item_uuid
  if (!itemUuid) return
  await updateBiddingMaterialRequirement(
    row,
    {
      submitted_profile_item_uuid: itemUuid,
      status: 'approved',
      notes: '采用企业资料库候选资料',
    },
    '已采用企业资料库候选资料',
  )
}

async function submitBiddingMaterialValue(row) {
  if (!row?.requirement_uuid) return
  if (biddingMaterialShouldUseEnterpriseProfile(row)) {
    await openBiddingMaterialProfileDialog(row)
    return
  }
  await submitBiddingMaterialManualValue(row)
}

function biddingMaterialShouldUseEnterpriseProfile(row) {
  return (
    biddingDraftPackageKey.value === 'technical'
    && (row?.fulfillment_mode === 'enterprise_profile' || Boolean(row?.profile_category))
  )
}

async function openBiddingMaterialProfileDialog(row) {
  biddingMaterialProfileDialog.row = row
  Object.assign(biddingMaterialProfileDialog.form, {
    category: row?.profile_category || '',
    keyword: row?.normalized?.keyword || row?.item_title || row?.title || '',
  })
  biddingMaterialProfileDialog.candidates = Array.isArray(row?.candidates) ? row.candidates : []
  biddingMaterialProfileDialog.selectedProfiles = []
  biddingMaterialProfileDialog.uploadedFiles = []
  biddingMaterialProfileDialog.visible = true
  await loadBiddingMaterialProfileCandidates()
}

async function loadBiddingMaterialProfileCandidates() {
  if (!biddingMaterialProfileDialog.visible) return
  biddingMaterialProfileDialog.loading = true
  try {
    const response = await api.get('/enterprise-profile/candidates', {
      params: {
        category: biddingMaterialProfileDialog.form.category || undefined,
        keyword: biddingMaterialProfileDialog.form.keyword || undefined,
        limit: 50,
      },
    })
    biddingMaterialProfileDialog.candidates = responseData(response) || []
  } catch (error) {
    biddingMaterialProfileDialog.candidates = []
    ElMessage.error(apiErrorMessage(error, '企业资料候选加载失败'))
  } finally {
    biddingMaterialProfileDialog.loading = false
  }
}

function handleBiddingMaterialProfileSelectionChange(selection) {
  biddingMaterialProfileDialog.selectedProfiles = Array.isArray(selection) ? selection : []
}

async function uploadBiddingMaterialRequirementFile(uploadFile) {
  const rawFile = uploadFile?.raw
  if (!rawFile) return
  biddingMaterialProfileDialog.uploading = true
  try {
    const formData = new FormData()
    formData.append('file', rawFile)
    formData.append('purpose', 'bidding_material_requirement')
    const response = await api.post('/files', formData)
    const data = responseData(response)
    if (data?.file_id && !biddingMaterialProfileDialog.uploadedFiles.some((file) => file.file_id === data.file_id)) {
      biddingMaterialProfileDialog.uploadedFiles.push({
        file_id: data.file_id,
        original_filename: data.original_filename || rawFile.name || '',
      })
    }
    ElMessage.success('补充文件已上传')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '补充文件上传失败'))
  } finally {
    biddingMaterialProfileDialog.uploading = false
  }
}

function removeBiddingMaterialRequirementUploadedFile(fileId) {
  biddingMaterialProfileDialog.uploadedFiles = biddingMaterialProfileDialog.uploadedFiles.filter((file) => file.file_id !== fileId)
}

function uniqueCompact(values) {
  const result = []
  const seen = new Set()
  for (const value of values || []) {
    const text = String(value || '').trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    result.push(text)
  }
  return result
}

async function submitBiddingMaterialProfileCandidates(items = []) {
  const row = biddingMaterialProfileDialog.row
  if (!row?.requirement_uuid) return
  const profileUuids = uniqueCompact([
    ...(row.submitted_profile_item_uuids || []),
    row.submitted_profile_item_uuid,
    ...items.map((item) => item?.item_uuid),
  ])
  const fileIds = uniqueCompact([
    ...(row.submitted_file_ids || []),
    row.submitted_file_id,
    ...biddingMaterialProfileDialog.uploadedFiles.map((file) => file.file_id),
  ])
  if (!profileUuids.length && !fileIds.length) {
    ElMessage.warning('请先选择企业资料或上传补充文件')
    return
  }
  const ok = await updateBiddingMaterialRequirement(
    row,
    {
      submitted_profile_item_uuids: profileUuids,
      submitted_file_ids: fileIds,
      status: 'approved',
      notes: `已提交 ${profileUuids.length} 份企业资料、${fileIds.length} 份补充文件`,
    },
    '已提交多份技术标资料',
  )
  if (ok) {
    biddingMaterialProfileDialog.visible = false
    biddingMaterialProfileDialog.row = null
    biddingMaterialProfileDialog.candidates = []
    biddingMaterialProfileDialog.selectedProfiles = []
    biddingMaterialProfileDialog.uploadedFiles = []
  }
}

async function submitBiddingMaterialManualValue(row) {
  if (!row?.requirement_uuid) return
  biddingMaterialProfileDialog.visible = false
  try {
    const requiredInformation = String(row.description || row.item_title || row.title || '本章节所需资料')
      .replace(/^.*?需补充[：:]/, '')
      .trim()
    const result = await ElMessageBox.prompt(`需要补充：${requiredInformation}\n\n请填写明确内容、资料文件名称/位置或不适用理由。`, row.title || '补齐资料需求', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValue: row.submitted_value || '',
    })
    const submittedValue = String(result.value || '').trim()
    if (!submittedValue) return
    await updateBiddingMaterialRequirement(
      row,
      {
        submitted_value: submittedValue,
        status: 'submitted',
        notes: row.notes || '人工补充资料',
      },
      '资料需求已提交，待确认可用',
    )
  } catch {
    // 用户取消
  }
}

async function approveBiddingMaterialRequirement(row) {
  await updateBiddingMaterialRequirement(
    row,
    { status: 'approved', notes: row.notes || '人工确认可用于投标草稿' },
    '资料需求已确认可用；请重新生成对应技术标章节后再导出正式稿',
  )
}

async function markBiddingMaterialRequirementNotApplicable(row) {
  await updateBiddingMaterialRequirement(row, { status: 'not_applicable', notes: row.notes || '本项目不适用' }, '资料需求已标记不适用')
}

function biddingFileFormatHasPackage(packageKey) {
  return biddingFileFormatPackages.value.some((pkg) => pkg.package_key === packageKey)
}

function biddingFileFormatPackageLabel(packageKey) {
  return biddingFileFormatPackages.value.find((pkg) => pkg.package_key === packageKey)?.package_title || packageKey || ''
}

function appendBiddingFileFormatEditEvent(event) {
  biddingFileFormatPendingEvents.value = [
    ...biddingFileFormatPendingEvents.value,
    {
      ...event,
      created_at: new Date().toISOString(),
    },
  ]
}

function openBiddingFileFormatItemDialog() {
  const firstPackage = biddingFileFormatPackages.value.find((pkg) => pkg.package_key === biddingDraftPackageKey.value) || biddingFileFormatPackages.value[0]
  biddingFileFormatItemDialog.visible = true
  biddingFileFormatItemDialog.package_key = firstPackage?.package_key || 'business'
  biddingFileFormatItemDialog.item_title = ''
  biddingFileFormatItemDialog.content_type = 'draft_section'
  biddingFileFormatItemDialog.owner_role = firstPackage?.package_key === 'business' ? '经营' : '技术'
  biddingFileFormatItemDialog.generation_strategy = 'generate_draft'
  biddingFileFormatItemDialog.requires_signature = false
  biddingFileFormatItemDialog.requires_attachment = false
}

function syncBiddingFileFormatDialogStrategy() {
  biddingFileFormatItemDialog.generation_strategy = biddingFileFormatDefaultGenerationStrategy(
    biddingFileFormatItemDialog.content_type,
  )
  biddingFileFormatItemDialog.requires_attachment = ['attachment_proof', 'qualification_attachment'].includes(
    biddingFileFormatItemDialog.content_type,
  )
  biddingFileFormatItemDialog.requires_signature = biddingFileFormatItemDialog.content_type === 'fixed_form'
}

function addBiddingFileFormatItem() {
  const title = biddingFileFormatItemDialog.item_title.trim()
  if (!title) {
    ElMessage.warning('请填写目录项名称')
    return
  }
  const structure = cloneBiddingFileFormatStructure()
  const targetPackage = findBiddingFileFormatPackage(structure, biddingFileFormatItemDialog.package_key)
  if (!targetPackage) {
    ElMessage.warning('请先选择文件包')
    return
  }
  const itemKey = `manual_${Date.now()}`
  const contentType = biddingFileFormatItemDialog.content_type
  const item = {
    item_key: `${targetPackage.package_key}:${itemKey}`,
    base_item_key: itemKey,
    item_title: title,
    package_key: targetPackage.package_key,
    content_type: contentType,
    content_type_label: biddingFileFormatContentTypeLabel(contentType),
    owner_role: biddingFileFormatItemDialog.owner_role,
    generation_strategy: biddingFileFormatItemDialog.generation_strategy,
    is_required: true,
    requires_signature: biddingFileFormatItemDialog.requires_signature,
    requires_attachment: biddingFileFormatItemDialog.requires_attachment,
    order_index: (targetPackage.items || []).length + 1,
    evidence: [
      {
        source_file: '人工新增',
        source_location: '格式确认表',
        original_text: '人工新增目录项',
        source_kind: 'manual',
      },
    ],
  }
  targetPackage.items = [...(targetPackage.items || []), item]
  touchBiddingFileFormatStructure(structure)
  appendBiddingFileFormatEditEvent({
    event_type: 'add_item',
    item_key: item.item_key,
    item_title: item.item_title,
    to_package_key: targetPackage.package_key,
    detail: {
      to_package_title: targetPackage.package_title,
      content_type: item.content_type,
      generation_strategy: item.generation_strategy,
      owner_role: item.owner_role,
      note: '人工新增投标文件格式目录项',
    },
  })
  biddingFileFormatItemDialog.visible = false
  ElMessage.success('目录项已新增，确认格式后保存')
}

function moveBiddingFileFormatItem(row, targetPackageKey) {
  if (!row?.item_key || row.package_key === targetPackageKey) return
  const structure = cloneBiddingFileFormatStructure()
  const sourcePackage = findBiddingFileFormatPackage(structure, row.package_key)
  const targetPackage = findBiddingFileFormatPackage(structure, targetPackageKey)
  if (!sourcePackage || !targetPackage) return
  const sourceItems = sourcePackage.items || []
  const itemIndex = sourceItems.findIndex((item) => item.item_key === row.item_key)
  if (itemIndex < 0) return
  const [item] = sourceItems.splice(itemIndex, 1)
  item.package_key = targetPackage.package_key
  item.item_key = `${targetPackage.package_key}:${item.base_item_key || item.item_key.split(':').pop()}`
  item.order_index = (targetPackage.items || []).length + 1
  item.conflict_note = item.conflict_note || '人工调整过文件包归属。'
  targetPackage.items = [...(targetPackage.items || []), item]
  touchBiddingFileFormatStructure(structure)
  appendBiddingFileFormatEditEvent({
    event_type: 'move_item',
    item_key: item.item_key,
    item_title: item.item_title,
    from_package_key: sourcePackage.package_key,
    to_package_key: targetPackage.package_key,
    detail: {
      from_package_title: sourcePackage.package_title,
      to_package_title: targetPackage.package_title,
      content_type: item.content_type,
      generation_strategy: item.generation_strategy,
      owner_role: item.owner_role,
      note: `人工从${sourcePackage.package_title}移动到${targetPackage.package_title}`,
    },
  })
  ElMessage.success(`已移动到${targetPackage.package_title}，确认格式后保存`)
}

function removeBiddingFileFormatItem(row) {
  if (!row?.item_key) return
  const structure = cloneBiddingFileFormatStructure()
  const sourcePackage = findBiddingFileFormatPackage(structure, row.package_key)
  if (!sourcePackage) return
  const removed = (sourcePackage.items || []).find((item) => item.item_key === row.item_key)
  sourcePackage.items = (sourcePackage.items || []).filter((item) => item.item_key !== row.item_key)
  touchBiddingFileFormatStructure(structure)
  appendBiddingFileFormatEditEvent({
    event_type: 'remove_item',
    item_key: row.item_key,
    item_title: row.item_title,
    from_package_key: sourcePackage.package_key,
    detail: {
      from_package_title: sourcePackage.package_title,
      content_type: removed?.content_type || row.content_type,
      generation_strategy: removed?.generation_strategy || row.generation_strategy,
      owner_role: removed?.owner_role || row.owner_role,
      note: `人工从${sourcePackage.package_title}删除目录项`,
    },
  })
  ElMessage.success('目录项已删除，确认格式后保存')
}

function cloneBiddingFileFormatStructure() {
  return JSON.parse(JSON.stringify(biddingFileFormatPlan.value?.structure || { packages: [] }))
}

function findBiddingFileFormatPackage(structure, packageKey) {
  return (structure?.packages || []).find((pkg) => pkg.package_key === packageKey)
}

function touchBiddingFileFormatStructure(structure) {
  if (!biddingFileFormatPlan.value) return
  const normalized = refreshBiddingFileFormatStructureStats(structure)
  const wasConfirmed = biddingFileFormatPlan.value.review_status === 'confirmed'
  biddingFileFormatPlan.value = {
    ...biddingFileFormatPlan.value,
    review_status: wasConfirmed ? 'needs_revision' : biddingFileFormatPlan.value.review_status || 'draft',
    confirmed_at: wasConfirmed ? null : biddingFileFormatPlan.value.confirmed_at,
    structure: normalized,
    summary: buildLocalBiddingFileFormatSummary(normalized, biddingFileFormatPlan.value.summary || {}),
  }
}

function refreshBiddingFileFormatStructureStats(structure) {
  const packages = structure?.packages || []
  for (const pkg of packages) {
    const items = pkg.items || []
    pkg.item_count = items.length
    pkg.draft_section_count = items.filter((item) => item.content_type === 'draft_section').length
    pkg.fixed_form_count = items.filter((item) => item.content_type === 'fixed_form').length
    pkg.attachment_count = items.filter((item) => ['attachment_proof', 'qualification_attachment'].includes(item.content_type)).length
    pkg.pricing_table_count = items.filter((item) => item.content_type === 'pricing_table').length
  }
  return structure
}

function buildLocalBiddingFileFormatSummary(structure, previousSummary = {}) {
  const items = (structure?.packages || []).flatMap((pkg) => pkg.items || [])
  const countByType = items.reduce((acc, item) => {
    const key = item.content_type || 'unknown'
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})
  return {
    ...previousSummary,
    package_count: (structure?.packages || []).length,
    item_count: items.length,
    fixed_form_count: countByType.fixed_form || 0,
    draft_section_count: countByType.draft_section || 0,
    pricing_table_count: countByType.pricing_table || 0,
    attachment_count: (countByType.attachment_proof || 0) + (countByType.qualification_attachment || 0),
    manual_input_count: items.filter((item) => ['manual_upload', 'manual_fill'].includes(item.generation_strategy)).length,
    conflict_count: items.filter((item) => item.conflict_status === 'cross_package_duplicate').length,
    by_content_type: countByType,
  }
}

function biddingFileFormatDefaultGenerationStrategy(contentType) {
  if (contentType === 'draft_section') return 'generate_draft'
  if (contentType === 'pricing_table') return 'from_cost_quote'
  if (['attachment_proof', 'qualification_attachment'].includes(contentType)) return 'manual_upload'
  return 'manual_fill'
}

async function loadBiddingDraftOutline(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  biddingDraftOutlineLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/outline`, {
      params: { run_uuid: 'latest', package_key: biddingDraftPackageKey.value || undefined },
    })
    biddingDraftOutline.value = responseData(response) || null
  } catch (error) {
    biddingDraftOutline.value = null
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '投标书目录骨架加载失败'))
  } finally {
    biddingDraftOutlineLoading.value = false
  }
}

async function generateBiddingDraftOutline() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingDraftOutlineGenerating.value) return
  biddingDraftOutlineGenerating.value = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/outline/generate`, {
      run_uuid: 'latest',
      package_key: biddingDraftPackageKey.value || undefined,
    })
    biddingDraftOutline.value = responseData(response) || null
    ElMessage.success('投标书目录骨架已生成')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '投标书目录骨架生成失败'))
  } finally {
    biddingDraftOutlineGenerating.value = false
  }
}

async function loadBiddingDraftSections(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  biddingDraftSectionsLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/sections`, {
      params: { run_uuid: 'latest', package_key: biddingDraftPackageKey.value || undefined },
    })
    biddingDraftSections.value = responseData(response) || []
  } catch (error) {
    biddingDraftSections.value = []
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '章节草稿加载失败'))
  } finally {
    biddingDraftSectionsLoading.value = false
  }
}

async function generateBiddingTechnicalDraftMvp() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingTechnicalDraftGenerating.value) return
  biddingTechnicalDraftGenerating.value = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/technical-draft/generate`, {
      run_uuid: 'latest',
      overwrite: true,
    })
    const result = responseData(response) || {}
    await loadBiddingDraftSections(projectUuid)
    const firstDraft = (result.drafts || [])[0] || biddingTechnicalCompositionDraftSections.value[0]
    if (firstDraft) {
      biddingDraftPreviewDrawer.draft = firstDraft
      biddingDraftPreviewDrawer.visible = true
      biddingDraftPreviewDrawer.editing = false
      biddingDraftPreviewDrawer.editContent = ''
    }
    ElMessage.success(`技术标草案已生成：${result.generated_count || 0} 章，占位 ${result.placeholder_count || 0} 章`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '技术标草案生成失败'))
  } finally {
    biddingTechnicalDraftGenerating.value = false
  }
}

async function exportBiddingTechnicalDraftWord() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingTechnicalDraftExporting.value) return
  biddingTechnicalDraftExporting.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/technical-draft/export`, {
      params: { run_uuid: 'latest' },
      responseType: 'blob',
    })
    const fallback = `${biddingDrawer.project?.project_name || '技术标'}_技术标草稿.docx`
    const filename = filenameFromDisposition(response.headers?.['content-disposition'], fallback)
    downloadBlob(response.data, {
      filename,
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    ElMessage.success('技术标 Word 草稿已导出')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '技术标 Word 草稿导出失败'))
  } finally {
    biddingTechnicalDraftExporting.value = false
  }
}

async function exportBiddingTechnicalFinalWord() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingTechnicalFinalExporting.value) return
  biddingTechnicalFinalExporting.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/technical-final/export`, {
      params: { run_uuid: 'latest' },
      responseType: 'blob',
    })
    const fallback = `${biddingDrawer.project?.project_name || '技术标'}_技术标正式稿.docx`
    const filename = filenameFromDisposition(response.headers?.['content-disposition'], fallback)
    downloadBlob(response.data, {
      filename,
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    ElMessage.success('正式技术标 Word 已导出')
  } catch (error) {
    await hydrateBlobErrorDetail(error)
    if (error.response?.data?.detail?.code === 'BID_TECHNICAL_FINAL_EXPORT_BLOCKED') {
      await loadBiddingMaterialRequirements(projectUuid, 'technical')
    }
    const message = biddingTechnicalFinalExportBlockMessage(error)
    if (error.response?.data?.detail?.code === 'BID_TECHNICAL_FINAL_EXPORT_BLOCKED') {
      await ElMessageBox.alert(message, '正式导出阻断', {
        type: 'warning',
        confirmButtonText: '知道了',
      })
    } else {
      ElMessage.error(message)
    }
  } finally {
    biddingTechnicalFinalExporting.value = false
  }
}

async function openBiddingTechnicalFinalQualityReport() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingTechnicalFinalQualityLoading.value) return
  biddingTechnicalFinalQualityLoading.value = true
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/bid-draft/technical-final/quality`, {
      params: { run_uuid: 'latest' },
    })
    await loadBiddingMaterialRequirements(projectUuid, 'technical')
    const report = responseData(response) || {}
    const issues = normalizeBiddingTechnicalFinalIssues(report.issues)
    biddingTechnicalFinalQualityDrawer.report = { ...report, issues, issue_count: issues.length }
    biddingTechnicalFinalQualityDrawer.visible = true
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '正式技术标质检报告加载失败'))
  } finally {
    biddingTechnicalFinalQualityLoading.value = false
  }
}

async function generateBiddingDraftSection(row) {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || !row?.section_key || biddingDraftSectionGeneratingKey.value) return
  biddingDraftSectionGeneratingKey.value = row.section_key
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/sections/generate`, {
      run_uuid: 'latest',
      section_key: row.section_key,
      generator_type: 'rule',
      package_key: biddingDraftPackageKey.value || undefined,
    })
    const draft = responseData(response)
    await loadBiddingDraftSections(projectUuid)
    biddingDraftPreviewDrawer.draft = draft
    biddingDraftPreviewDrawer.visible = true
    biddingDraftPreviewDrawer.editing = false
    biddingDraftPreviewDrawer.editContent = ''
    ElMessage.success(['blocked', 'review_note'].includes(row.draft_mode) ? '已生成复核说明' : '章节草稿已生成')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '章节草稿生成失败'))
  } finally {
    biddingDraftSectionGeneratingKey.value = ''
  }
}

function openBiddingDraftPreview(row) {
  const draft = row?.draft_uuid ? row : biddingDraftForOutlineSection(row)
  if (!draft) return
  biddingDraftPreviewDrawer.draft = draft
  biddingDraftPreviewDrawer.visible = true
  biddingDraftPreviewDrawer.editing = false
  biddingDraftPreviewDrawer.editContent = ''
}

function startEditingBiddingDraftSection() {
  const draft = biddingDraftPreviewDrawer.draft
  if (!draft) return
  biddingDraftPreviewDrawer.editContent = draft.content_markdown || ''
  biddingDraftPreviewDrawer.editing = true
}

function cancelEditingBiddingDraftSection() {
  biddingDraftPreviewDrawer.editing = false
  biddingDraftPreviewDrawer.editContent = ''
}

async function saveBiddingDraftSectionContent() {
  const draft = biddingDraftPreviewDrawer.draft
  if (!draft?.draft_uuid || biddingDraftPreviewDrawer.saving) return
  biddingDraftPreviewDrawer.saving = true
  try {
    const response = await api.patch(`/admin/bidding/bid-draft/sections/${draft.draft_uuid}/content`, {
      content_markdown: biddingDraftPreviewDrawer.editContent || '',
      editor_note: '人工编辑保存',
    })
    const nextDraft = responseData(response)
    biddingDraftPreviewDrawer.draft = nextDraft
    biddingDraftPreviewDrawer.editing = false
    biddingDraftPreviewDrawer.editContent = ''
    await loadBiddingDraftSections()
    ElMessage.success('章节正文已保存为新版本')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '章节正文保存失败'))
  } finally {
    biddingDraftPreviewDrawer.saving = false
  }
}

async function generateBiddingDraftSectionWithLlm() {
  const draft = biddingDraftPreviewDrawer.draft
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || !draft?.section_key || biddingDraftPreviewDrawer.llmGenerating) return
  if (!biddingDraftCanLlmEnhance(draft)) {
    const reasons = draft?.llm_entry?.blocked_reasons || []
    ElMessage.warning(reasons[0] || '当前章节尚未通过 LLM 正文增强入口')
    return
  }
  biddingDraftPreviewDrawer.llmGenerating = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/bid-draft/sections/generate`, {
      run_uuid: 'latest',
      section_key: draft.section_key,
      generator_type: 'llm',
      package_key: biddingDraftPackageKey.value || draft.package_key || undefined,
    })
    const nextDraft = responseData(response)
    biddingDraftPreviewDrawer.draft = nextDraft
    biddingDraftPreviewDrawer.editing = false
    biddingDraftPreviewDrawer.editContent = ''
    await loadBiddingDraftSections(projectUuid)
    ElMessage.success('DeepSeek 已生成正文版本')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'DeepSeek 正文生成失败'))
  } finally {
    biddingDraftPreviewDrawer.llmGenerating = false
  }
}

async function reviewBiddingDraftSection(reviewStatus) {
  const draft = biddingDraftPreviewDrawer.draft
  if (!draft?.draft_uuid || biddingDraftSectionReviewing.value) return
  if (reviewStatus === 'accepted' && draft.acceptance_check?.status === 'blocked') {
    ElMessage.warning(draft.acceptance_check.summary || '当前 LLM 增强稿接受前检查未通过')
    return
  }
  let reviewerNote = ''
  if (reviewStatus === 'needs_revision') {
    try {
      const result = await ElMessageBox.prompt('请输入需要修改的原因或补充说明。', '章节草稿需修改', {
        confirmButtonText: '提交',
        cancelButtonText: '取消',
        inputType: 'textarea',
      })
      reviewerNote = result.value || ''
    } catch {
      return
    }
  }
  biddingDraftSectionReviewing.value = true
  try {
    const response = await api.patch(`/admin/bidding/bid-draft/sections/${draft.draft_uuid}/review`, {
      review_status: reviewStatus,
      reviewer_note: reviewerNote,
    })
    const nextDraft = responseData(response)
    biddingDraftPreviewDrawer.draft = nextDraft
    await loadBiddingDraftSections()
    ElMessage.success('章节草稿复核状态已更新')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '章节草稿复核失败'))
  } finally {
    biddingDraftSectionReviewing.value = false
  }
}

async function generateBiddingResponseMatrix() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingResponseMatrixGenerating.value) return
  try {
    await ElMessageBox.confirm(
      '系统会从业务对象、风险和关键要求生成响应矩阵初稿；已存在的响应项不会重复生成，也不会覆盖人工修改。',
      '生成响应矩阵',
      {
        confirmButtonText: '生成',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  biddingResponseMatrixGenerating.value = true
  try {
    const response = await api.post(`/admin/bidding/projects/${projectUuid}/response-matrix/generate`, {
      run_uuid: 'latest',
    })
    const data = responseData(response) || {}
    ElMessage.success(`响应矩阵已生成：新增 ${data.created_count || 0} 条，已有 ${data.skipped_existing_count || 0} 条`)
    await Promise.all([
      loadBiddingResponseMatrix(projectUuid),
      loadBiddingDraftOutline(projectUuid),
      loadBiddingProjectDetail(projectUuid),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '响应矩阵生成失败'))
  } finally {
    biddingResponseMatrixGenerating.value = false
  }
}

async function updateBiddingResponseItem(row, patch) {
  if (!row?.response_item_uuid || biddingResponseItemUpdating.value) return
  biddingResponseItemUpdating.value = true
  try {
    await api.patch(`/admin/bidding/response-items/${row.response_item_uuid}`, patch)
    await Promise.all([loadBiddingResponseMatrix(), loadBiddingDraftOutline()])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '响应项更新失败'))
    await Promise.all([loadBiddingResponseMatrix(), loadBiddingDraftOutline()])
  } finally {
    biddingResponseItemUpdating.value = false
  }
}

async function editBiddingResponseItemNote(row) {
  if (!row?.response_item_uuid) return
  try {
    const result = await ElMessageBox.prompt('填写响应说明或处理备注，后续投标书生成会优先读取这里的人工说明。', '编辑响应说明', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputValue: row.response_note || '',
      inputPlaceholder: '例如：已按招标文件要求在商务响应中承诺，报价已考虑该风险。',
    })
    await updateBiddingResponseItem(row, { response_note: result.value || '' })
  } catch {
    // cancel
  }
}

async function editBiddingResponseItemOwner(row) {
  if (!row?.response_item_uuid) return
  try {
    const result = await ElMessageBox.prompt('填写责任角色，便于投标协同跟进。', '编辑责任角色', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: row.owner_role || '',
      inputPlaceholder: '经营 / 预算 / 法务 / 技术',
    })
    await updateBiddingResponseItem(row, { owner_role: result.value || '' })
  } catch {
    // cancel
  }
}

async function loadBiddingRequirements(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/requirements`, {
      params: { page: 1, page_size: 200 },
    })
    biddingRequirements.value = responseData(response) || []
    biddingRequirementsTotal.value = response.data?.total ?? biddingRequirements.value.length
  } catch (error) {
    biddingRequirements.value = []
    biddingRequirementsTotal.value = 0
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '招标要求加载失败'))
  }
}

async function loadBiddingRisks(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/risks`, {
      params: { page: 1, page_size: 200 },
    })
    biddingRisks.value = responseData(response) || []
    biddingRisksTotal.value = response.data?.total ?? biddingRisks.value.length
  } catch (error) {
    biddingRisks.value = []
    biddingRisksTotal.value = 0
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '合同风险加载失败'))
  }
}

async function loadBiddingRiskCards(projectUuid = currentBiddingProjectUuid()) {
  if (!projectUuid) return
  try {
    const response = await api.get(`/admin/bidding/projects/${projectUuid}/risk-cards`)
    const payload = responseData(response) || {}
    biddingRiskCards.value = payload.cards || []
    biddingRiskCardsSummary.value = payload.summary || {}
  } catch (error) {
    biddingRiskCards.value = []
    biddingRiskCardsSummary.value = {}
    if (!isBiddingNoParseRun(error)) ElMessage.error(apiErrorMessage(error, '风险卡片加载失败'))
  }
}

function handleBiddingFileChange(uploadFile) {
  const file = uploadFile?.raw || uploadFile || null
  if (file && !isPrimaryTenderFile(file)) {
    ElMessage.warning('首版仅支持甲方招标文件 PDF 或 Word(.docx)')
    biddingUpload.file = null
    biddingUploadRef.value?.clearFiles?.()
    return
  }
  biddingUpload.file = file
}

function clearBiddingFile() {
  biddingUpload.file = null
}

async function uploadBiddingFile() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || !biddingUpload.file) return
  if (!isPrimaryTenderFile(biddingUpload.file)) {
    ElMessage.warning('首版仅支持甲方招标文件 PDF 或 Word(.docx)')
    return
  }
  const form = new FormData()
  form.append('file', biddingUpload.file)
  form.append('file_type', biddingUpload.fileType || 'tender_document')
  biddingUpload.loading = true
  try {
    await api.post(`/admin/bidding/projects/${projectUuid}/files`, form)
    ElMessage.success('招标资料已上传并抽取文本')
    biddingUpload.file = null
    biddingUploadRef.value?.clearFiles?.()
    await Promise.all([
      loadBiddingFiles(projectUuid),
      loadBiddingProjectDetail(projectUuid),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '资料上传失败'))
  } finally {
    biddingUpload.loading = false
  }
}

async function parseBiddingProject() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid) return
  biddingParsing.value = true
  biddingRiskClause.value = null
  startBiddingImportantInfoProgress('解析招标文件')
  try {
    updateBiddingImportantInfoProgress(18, '解析招标文件', '抽取招标文件文本和原文片段')
    await api.post(`/admin/bidding/projects/${projectUuid}/parse`, { file_uuids: [] })
    updateBiddingImportantInfoProgress(42, '准备LLM结构化提取', '整理提示词、output_schema 和招标文件原文片段')
    biddingDrawer.activeTab = 'analysis'
    await Promise.all([loadBiddingProjectDetail(projectUuid), loadBiddingFiles(projectUuid), loadBiddingParseRuns(projectUuid)])
    await loadBiddingTenderAnalysis(projectUuid, { trackProgress: true })
    await Promise.all([
      loadBiddingRiskClause(projectUuid),
      loadBiddingBusinessObjects(projectUuid),
      loadBiddingResponseMatrix(projectUuid),
      loadBiddingFileFormatPlan(projectUuid),
      loadBiddingDraftOutline(projectUuid),
      loadBiddingDraftSections(projectUuid),
      loadBiddingRequirements(projectUuid),
      loadBiddingRiskCards(projectUuid),
      loadBiddingRisks(projectUuid),
      loadBiddingProjects(),
    ])
    ElMessage.success('招标文件解析与LLM结构化提取完成')
  } catch (error) {
    finishBiddingImportantInfoProgress(false, apiErrorMessage(error, '招标文件解析失败'))
    ElMessage.error(apiErrorMessage(error, '招标文件解析失败'))
  } finally {
    biddingParsing.value = false
  }
}

async function reviewBiddingBusinessObjectsWithLlm() {
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid || biddingBusinessObjectLlmReviewing.value) return
  const pendingRows = biddingLlmPendingRows.value.slice()
  if (!pendingRows.length) {
    ElMessage.info('当前没有新的 weak_split / needs_llm_review / needs_secondary_split 对象需要 DeepSeek 复核')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将逐条把 ${pendingRows.length} 个不确定业务对象交给 deepseek-v4-pro，页面会显示当前处理到哪一条；模型结果仅作为人工确认建议。`,
      'DeepSeek 复核不确定对象',
      {
        confirmButtonText: '开始复核',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  biddingBusinessObjectLlmReviewing.value = true
  Object.assign(biddingBusinessObjectLlmProgress, {
    visible: true,
    total: pendingRows.length,
    current: 0,
    completed: 0,
    error: 0,
    skipped: 0,
    currentTitle: '',
    lastMessage: '准备提交 DeepSeek',
  })
  try {
    for (let index = 0; index < pendingRows.length; index += 1) {
      const row = pendingRows[index]
      biddingBusinessObjectLlmProgress.current = index + 1
      biddingBusinessObjectLlmProgress.currentTitle = row.title || row.object_subtype || row.object_uuid
      biddingBusinessObjectLlmProgress.lastMessage = '正在请求 DeepSeek...'
      try {
        const response = await api.post(`/admin/bidding/projects/${projectUuid}/business-objects/llm-review`, {
          run_uuid: 'latest',
          limit: 1,
          force: false,
          only_pending: true,
          object_uuids: [row.object_uuid],
        })
        const data = responseData(response) || {}
        if (data.status === 'skipped' && data.skip_reason === 'deepseek_api_key_missing') {
          biddingBusinessObjectLlmProgress.lastMessage = '未配置 DEEPSEEK_API_KEY，已停止'
          ElMessage.warning('未配置 DEEPSEEK_API_KEY，已跳过 DeepSeek 复核')
          break
        }
        if (data.status === 'no_candidates') {
          biddingBusinessObjectLlmProgress.skipped += 1
          biddingBusinessObjectLlmProgress.lastMessage = `已跳过：${row.title || row.object_subtype}`
        } else {
          const reviewedCount = Number(data.reviewed_count || 0)
          const errorCount = Number(data.error_count || 0)
          biddingBusinessObjectLlmProgress.completed += reviewedCount
          biddingBusinessObjectLlmProgress.error += errorCount
          const doneItem = data.items?.[0] || data.errors?.[0] || {}
          biddingBusinessObjectLlmProgress.lastMessage = errorCount
            ? `异常：${doneItem.title || row.title || row.object_subtype}`
            : `已完成：${doneItem.title || row.title || row.object_subtype}`
        }
        await loadBiddingBusinessObjects(projectUuid)
      } catch (error) {
        biddingBusinessObjectLlmProgress.error += 1
        biddingBusinessObjectLlmProgress.lastMessage = `请求失败：${row.title || row.object_subtype}`
        ElMessage.error(apiErrorMessage(error, 'DeepSeek 单条复核失败'))
        break
      }
    }
    biddingBusinessObjectLlmProgress.currentTitle = '处理结束'
    ElMessage.success(
      `DeepSeek 复核结束：完成 ${biddingBusinessObjectLlmProgress.completed}，异常 ${biddingBusinessObjectLlmProgress.error}，跳过 ${biddingBusinessObjectLlmProgress.skipped}`,
    )
    await Promise.all([
      loadBiddingBusinessObjects(projectUuid),
      loadBiddingTenderAnalysis(projectUuid),
      loadBiddingResponseMatrix(projectUuid),
      loadBiddingProjectDetail(projectUuid),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'DeepSeek 复核失败'))
  } finally {
    biddingBusinessObjectLlmReviewing.value = false
  }
}

async function submitBiddingLlmReviewDecision(row, action, payload = {}) {
  if (!row?.object_uuid) return
  biddingLlmDecisionSubmitting.value = true
  try {
    await api.patch(`/admin/bidding/business-objects/${row.object_uuid}/llm-review`, {
      action,
      ...payload,
    })
    const labels = { accept: '采纳', reject: '驳回', modify: '修改' }
    ElMessage.success(`DeepSeek建议已${labels[action] || '处理'}`)
    const projectUuid = currentBiddingProjectUuid()
    await Promise.all([
      loadBiddingBusinessObjects(projectUuid),
      loadBiddingTenderAnalysis(projectUuid),
      loadBiddingProjectDetail(projectUuid),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'DeepSeek建议处理失败'))
  } finally {
    biddingLlmDecisionSubmitting.value = false
  }
}

async function acceptBiddingLlmReview(row) {
  if (!row?.object_uuid) return
  try {
    await ElMessageBox.confirm(
      '采纳后会把该 DeepSeek 建议标记为有效建议，后续响应矩阵可读取该结果；不会自动修改业务对象分类或人工复核状态。',
      '采纳 DeepSeek 建议',
      {
        confirmButtonText: '采纳',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
  } catch {
    return
  }
  await submitBiddingLlmReviewDecision(row, 'accept', { reviewer_note: null })
}

async function rejectBiddingLlmReview(row) {
  if (!row?.object_uuid) return
  try {
    const result = await ElMessageBox.prompt('请填写驳回原因，便于后续追溯为什么不采用该建议。', '驳回 DeepSeek 建议', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputType: 'textarea',
      inputPlaceholder: '例如：原文证据不足，仍按系统原分类人工复核。',
      inputValidator: (value) => Boolean(value && value.trim()),
      inputErrorMessage: '请填写驳回原因',
    })
    await submitBiddingLlmReviewDecision(row, 'reject', {
      reviewer_note: result.value.trim(),
    })
  } catch {
    // cancel
  }
}

function openModifyBiddingLlmReview(row) {
  if (!row?.object_uuid) return
  const review = row.normalized?.llm_review || {}
  biddingLlmEditDialog.row = row
  Object.assign(biddingLlmEditDialog.form, {
    decision: review.decision || 'manual_review',
    suggested_title: review.suggested_title || row.title || '',
    suggested_object_subtype: review.suggested_object_subtype || row.object_subtype || '',
    primary_business_action: review.primary_business_action || row.normalized?.business_action || '',
    reason: review.reason || '',
    suggested_reviewer_note: review.suggested_reviewer_note || '',
    reviewer_note: '',
  })
  biddingLlmEditDialog.visible = true
}

async function submitModifyBiddingLlmReview() {
  const row = biddingLlmEditDialog.row
  if (!row?.object_uuid) return
  if (!biddingLlmEditDialog.form.reviewer_note?.trim()) {
    ElMessage.warning('请填写处理备注')
    return
  }
  await submitBiddingLlmReviewDecision(row, 'modify', {
    reviewer_note: biddingLlmEditDialog.form.reviewer_note.trim(),
    modified_review: {
      decision: biddingLlmEditDialog.form.decision,
      suggested_title: biddingLlmEditDialog.form.suggested_title,
      suggested_object_subtype: biddingLlmEditDialog.form.suggested_object_subtype,
      primary_business_action: biddingLlmEditDialog.form.primary_business_action,
      reason: biddingLlmEditDialog.form.reason,
      suggested_reviewer_note: biddingLlmEditDialog.form.suggested_reviewer_note,
    },
  })
  biddingLlmEditDialog.visible = false
}

async function reviewBiddingRisk(row, reviewStatus) {
  if (!row?.risk_uuid) return
  let reviewerNote = ''
  if (reviewStatus !== 'confirmed') {
    try {
      const result = await ElMessageBox.prompt('请填写复核说明，便于后续答疑、报价预留或忽略追溯。', '风险复核说明', {
        confirmButtonText: '提交',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '例如：转答疑，请甲方明确结算口径。',
        inputValidator: (value) => Boolean(value && value.trim()),
        inputErrorMessage: '请填写复核说明',
      })
      reviewerNote = result.value.trim()
    } catch {
      return
    }
  }
  try {
    await api.patch(`/admin/bidding/risks/${row.risk_uuid}/review`, {
      review_status: reviewStatus,
      reviewer_note: reviewerNote || null,
    })
    ElMessage.success('风险复核状态已更新')
    await Promise.all([
      loadBiddingRisks(),
      loadBiddingRiskCards(),
      loadBiddingTenderAnalysis(),
      loadBiddingProjectDetail(),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '风险复核失败'))
  }
}

async function reviewBiddingBusinessObject(row, reviewStatus) {
  if (!row?.object_uuid) return
  let reviewerNote = ''
  if (reviewStatus !== 'confirmed') {
    try {
      const result = await ElMessageBox.prompt('请填写业务复核说明，便于后续答疑、报价预留或忽略追溯。', '业务对象复核说明', {
        confirmButtonText: '提交',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '例如：转答疑，请甲方明确履约保证金是否可用保函替代。',
        inputValidator: (value) => Boolean(value && value.trim()),
        inputErrorMessage: '请填写复核说明',
      })
      reviewerNote = result.value.trim()
    } catch {
      return
    }
  }
  try {
    await api.patch(`/admin/bidding/business-objects/${row.object_uuid}/review`, {
      review_status: reviewStatus,
      reviewer_note: reviewerNote || null,
    })
    ElMessage.success('业务对象复核状态已更新')
    await Promise.all([
      loadBiddingBusinessObjects(),
      loadBiddingTenderAnalysis(),
      loadBiddingProjectDetail(),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '业务对象复核失败'))
  }
}

async function reviewBiddingRiskCard(card, reviewStatus) {
  if (!card?.card_id) return
  const projectUuid = currentBiddingProjectUuid()
  if (!projectUuid) return
  let reviewerNote = ''
  if (reviewStatus !== 'confirmed') {
    try {
      const result = await ElMessageBox.prompt('请填写这张风险卡的复核说明，系统会同步更新卡片内所有风险明细。', '风险卡片复核', {
        confirmButtonText: '提交',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '例如：转答疑，请甲方明确结算/品牌/违约责任边界。',
        inputValidator: (value) => Boolean(value && value.trim()),
        inputErrorMessage: '请填写复核说明',
      })
      reviewerNote = result.value.trim()
    } catch {
      return
    }
  }
  try {
    const response = await api.patch(`/admin/bidding/projects/${projectUuid}/risk-cards/${card.card_id}/review`, {
      review_status: reviewStatus,
      reviewer_note: reviewerNote || null,
    })
    const updated = responseData(response)
    ElMessage.success(`风险卡片已更新，影响 ${updated?.updated_risk_count || card.risk_count || 0} 条明细`)
    await Promise.all([
      loadBiddingRiskCards(projectUuid),
      loadBiddingRisks(projectUuid),
      loadBiddingTenderAnalysis(projectUuid),
      loadBiddingProjectDetail(projectUuid),
      loadBiddingProjects(),
    ])
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '风险卡片复核失败'))
  }
}

function optionLabel(options, value) {
  return options.find((item) => item.value === value)?.label || value || '-'
}

function enterpriseProfileCategoryLabel(value) {
  return optionLabel(enterpriseProfileCategoryOptions, value)
}

function enterpriseProfileStatusLabel(value) {
  return optionLabel(enterpriseProfileStatusOptions, value)
}

function enterpriseProfileStatusTag(value) {
  if (value === 'active') return 'success'
  if (value === 'archived') return 'info'
  return 'warning'
}

function enterpriseProfileIssueLabel(code) {
  const labels = {
    missing_attachment: '缺附件',
    missing_evidence: '缺内容/附件',
    expired: '已过期',
    expiring_soon: '即将到期',
  }
  return labels[code] || code || '-'
}

function enterpriseProfileIssueTag(code) {
  if (code === 'expired' || code === 'missing_evidence') return 'danger'
  if (code === 'missing_attachment' || code === 'expiring_soon') return 'warning'
  return 'info'
}

function biddingStatusLabel(status) {
  return optionLabel(biddingStatusOptions, status)
}

function biddingStatusTag(status) {
  if (status === 'parsed') return 'success'
  if (status === 'files_uploaded' || status === 'reviewing') return 'warning'
  if (status === 'archived') return 'info'
  return ''
}

function biddingFileTypeLabel(value) {
  return optionLabel(biddingFileTypeOptions, value)
}

function biddingParseStatusLabel(status) {
  const labels = {
    running: '解析中',
    completed: '已完成',
    failed: '失败',
  }
  return labels[status] || status || '-'
}

function biddingDocumentSectionLabel(value) {
  const labels = {
    table_of_contents: '目录',
    cover: '封面',
    bid_instructions: '投标须知',
    qualification: '资格要求',
    evaluation: '评标办法',
    contract_terms: '合同条款',
    technical_requirements: '技术要求',
    bill_of_quantities: '工程量清单',
    bid_format: '投标格式',
    material_brand: '材料品牌',
    scope_boundary: '范围界面',
    clarification: '澄清答疑',
    other: '其他段落',
  }
  return labels[value] || value || '-'
}

function biddingRequirementTypeLabel(value) {
  const labels = {
    qualification: '资格要求',
    technical: '技术要求',
    commercial: '商务要求',
    schedule: '工期要求',
    submission: '递交要求',
    bill: '清单要求',
    drawing: '图纸要求',
    brand: '品牌要求',
    other: '其他要求',
  }
  return labels[value] || value || '-'
}

function biddingRiskLevelLabel(level) {
  const labels = { high: '高', medium: '中', low: '低' }
  return labels[level] || level || '-'
}

function biddingRiskLevelTag(level) {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  if (level === 'low') return 'info'
  return ''
}

function biddingRiskGradeV2Label(grade) {
  const labels = {
    blocking: '阻断',
    critical: '重大',
    high: '高',
    medium: '中',
    low: '低',
  }
  return labels[grade] || grade || '-'
}

function biddingRiskGradeV2Tag(grade) {
  if (grade === 'blocking') return 'danger'
  if (grade === 'critical') return 'danger'
  if (grade === 'high') return 'warning'
  if (grade === 'medium') return 'primary'
  if (grade === 'low') return 'info'
  return ''
}

function biddingRiskActionLabel(action) {
  const labels = {
    manual_blocking_review: '阻断复核',
    to_quote_allowance: '报价预留',
    to_clarify: '转答疑',
    confirmed: '确认跟踪',
    bid_decision_review: '投标决策复核',
    ignored: '忽略',
  }
  return labels[action] || action || '-'
}

function biddingRiskTypeLabel(value) {
  const labels = {
    contract: '合同风险',
    disqualification: '废标风险',
    payment: '付款风险',
    settlement: '结算风险',
    warranty: '质保风险',
    schedule: '工期风险',
    penalty: '违约处罚',
    scope: '范围风险',
    fixed_total_price: '总价包干',
    omission_liability: '漏项责任',
    no_price_adjustment: '价格不调',
    advance_funding: '垫资/无预付款',
    delayed_payment: '付款周期',
    liquidated_damages: '违约金',
    claim_time_limit: '签证索赔',
    site_condition: '现场条件',
    design_or_drawing_unclear: '图纸/范围不清',
    material_brand_constraint: '材料品牌',
    material_brand: '材料品牌',
    bid_rejection: '废标/否决',
    anonymous_bid: '暗标',
    other: '其他风险',
  }
  return labels[value] || value || '-'
}

function biddingAnalysisReviewStatusLabel(value) {
  const labels = {
    pending: '待复核',
    confirmed: '已确认',
    needs_revision: '需补充',
    ignored: '已忽略',
    to_clarify: '需答疑',
    to_quote_allowance: '报价预留',
  }
  return labels[value] || value || '-'
}

function biddingAnalysisReviewStatusTag(value) {
  if (value === 'confirmed') return 'success'
  if (value === 'needs_revision') return 'danger'
  if (value === 'to_clarify') return 'primary'
  if (value === 'to_quote_allowance') return 'warning'
  if (value === 'ignored') return 'info'
  return 'warning'
}

function biddingImportantInfoStatusLabel(value) {
  const labels = {
    found: '已识别',
    unclear: '需澄清',
    not_found: '未识别',
  }
  return labels[value] || value || '-'
}

function biddingImportantInfoStatusTag(value) {
  if (value === 'found') return 'success'
  if (value === 'unclear') return 'warning'
  if (value === 'not_found') return 'danger'
  return 'info'
}

function biddingImportantInfoSectionFoundCount(section) {
  return (section?.items || []).filter((item) => item.status === 'found' && String(item.value || '').trim()).length
}

function biddingImportantInfoSourceLabel(row) {
  const file = String(row?.source_file || '').trim()
  const location = String(row?.source_location || '').trim()
  if (file && location) return `${file} · ${location}`
  return file || location || '-'
}

function formatBiddingPriorityClarificationText(rawItems) {
  if (!Array.isArray(rawItems)) return ''
  return rawItems
    .map((raw, index) => {
      if (!raw || typeof raw !== 'object') return ''
      const item = String(raw.item || '').trim()
      const reason = String(raw.reason || '').trim()
      if (item && reason) return `${index + 1}. ${item}: ${reason}`
      if (item) return `${index + 1}. ${item}`
      if (reason) return `${index + 1}. ${reason}`
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

function isBiddingImportantInfoSectionExpanded(section) {
  return biddingImportantInfoExpandedKeys.value.includes(section?.section_key)
}

function toggleBiddingImportantInfoSection(section) {
  const sectionKey = section?.section_key
  if (!sectionKey) return
  const keys = new Set(biddingImportantInfoExpandedKeys.value)
  if (keys.has(sectionKey)) keys.delete(sectionKey)
  else keys.add(sectionKey)
  biddingImportantInfoExpandedKeys.value = Array.from(keys)
}

function expandAllBiddingImportantInfoSections() {
  biddingImportantInfoExpandedKeys.value = [...biddingImportantInfoSectionKeys.value]
}

function collapseAllBiddingImportantInfoSections() {
  biddingImportantInfoExpandedKeys.value = []
}

function toggleBiddingScoringGroup(row) {
  if (!row?.row_key || !row.__scoringCanExpand) return
  const keys = new Set(biddingTenderScoringExpandedKeys.value)
  if (keys.has(row.row_key)) keys.delete(row.row_key)
  else keys.add(row.row_key)
  biddingTenderScoringExpandedKeys.value = Array.from(keys)
}

function biddingAnalysisPackageLabel(value) {
  const labels = {
    business: '商务标',
    technical: '技术标',
    pricing: '报价/商务标',
    contract: '合同/法务',
    mixed: '综合',
    unknown: '待确认',
  }
  return labels[value] || value || '-'
}

function biddingBusinessObjectTypeLabel(value) {
  const labels = {
    bid_rule: '投标规则',
    qualification: '资格审查',
    contract_clause: '合同条款',
    pricing_constraint: '报价约束',
    document_checklist: '文件清单',
  }
  return labels[value] || value || '-'
}

function biddingBusinessObjectActionLabel(value) {
  const labels = {
    bid_compliance: '投标合规',
    qualification_response: '资格响应',
    document_response: '文件编制',
    quote_allowance: '报价预留',
    clarification: '转答疑',
    legal_review: '法务复核',
    delivery_planning: '履约策划',
    reference: '信息参考',
    to_quote_allowance: '报价预留',
    to_clarify: '转答疑',
    manual_blocking_review: '阻断复核',
    bid_decision_review: '投标决策',
    confirmed: '确认跟踪',
  }
  return labels[value] || value || '-'
}

function biddingBusinessObjectActionListLabel(values = []) {
  const list = Array.isArray(values) ? values : [values]
  return list.map((value) => biddingBusinessObjectActionLabel(value)).filter(Boolean).join(' / ')
}

function biddingBusinessObjectActionTag(value) {
  if (value === 'quote_allowance') return 'warning'
  if (value === 'clarification') return 'danger'
  if (value === 'legal_review') return 'primary'
  if (value === 'document_response' || value === 'qualification_response') return 'success'
  if (value === 'delivery_planning') return 'info'
  return ''
}

function biddingResponseReviewRoleLabel(value) {
  const labels = {
    business: '经营',
    budget: '预算',
    technical: '技术',
    legal: '法务',
    经营: '经营',
    预算: '预算',
    技术: '技术',
    法务: '法务',
  }
  return labels[value] || null
}

function biddingResponseRowRoles(row) {
  const rawRoles = Array.isArray(row?.review_roles) ? row.review_roles : row?.normalized?.review_roles
  const roles = Array.isArray(rawRoles) ? rawRoles.map((role) => biddingResponseReviewRoleLabel(role)).filter(Boolean) : []
  if (row?.owner_role) {
    const ownerRole = biddingResponseReviewRoleLabel(row.owner_role)
    if (ownerRole) roles.push(ownerRole)
  }
  for (const action of biddingResponseLinkedActions(row)) {
    const ownerRole = biddingResponseReviewRoleLabel(action?.owner_role)
    if (ownerRole) roles.push(ownerRole)
  }
  return Array.from(new Set(roles))
}

function biddingResponsePrimaryRole(row) {
  return biddingResponseReviewRoleLabel(row?.primary_review_role || row?.normalized?.primary_review_role || row?.owner_role)
}

function biddingResponseSupportingRoles(row) {
  const rawRoles = Array.isArray(row?.supporting_roles) ? row.supporting_roles : row?.normalized?.supporting_roles
  const roles = Array.isArray(rawRoles)
    ? rawRoles.map((role) => biddingResponseReviewRoleLabel(role)).filter(Boolean)
    : biddingResponseRowRoles(row).filter((role) => role !== biddingResponsePrimaryRole(row))
  return Array.from(new Set(roles)).filter((role) => role && role !== biddingResponsePrimaryRole(row))
}

function biddingResponseRowMatchesRole(row, role) {
  const roleLabel = biddingResponseReviewRoleLabel(role)
  if (!roleLabel) return true
  return biddingResponsePrimaryRole(row) === roleLabel
}

function buildBiddingResponseTaskTree(rows = []) {
  const clones = rows.map((row, index) => ({ ...row, __sortIndex: index, children: [] }))
  const parentByGroupKey = new Map()
  const childrenByGroupKey = new Map()
  const childRows = []
  for (const row of clones) {
    const groupKey = biddingResponseEffectiveGroupKey(row)
    if (biddingResponseIsSummaryTask(row) && groupKey) {
      row.task_group_key = row.task_group_key || groupKey
      parentByGroupKey.set(groupKey, row)
    }
    if (biddingResponseIsGroupTask(row) && groupKey) {
      row.task_group_key = row.task_group_key || groupKey
      if (!childrenByGroupKey.has(groupKey)) childrenByGroupKey.set(groupKey, [])
      childrenByGroupKey.get(groupKey).push(row)
    }
  }
  for (const [groupKey, children] of childrenByGroupKey.entries()) {
    const parent = parentByGroupKey.get(groupKey)
    if (parent) {
      parent.children.push(...children)
      childRows.push(...children.map((row) => row.response_item_uuid))
    }
  }
  const childIds = new Set(childRows)
  for (const row of parentByGroupKey.values()) {
    row.children.sort((left, right) => Number(left.task_group_index || 0) - Number(right.task_group_index || 0))
    if (!row.children.length) delete row.children
  }

  const virtualParents = []
  for (const [groupKey, children] of childrenByGroupKey.entries()) {
    if (parentByGroupKey.has(groupKey) || !children.length) continue
    const virtualParent = buildBiddingResponseVirtualGroupParent(groupKey, children)
    virtualParents.push(virtualParent)
    for (const child of children) childIds.add(child.response_item_uuid)
  }

  return [...clones, ...virtualParents]
    .filter((row) => !childIds.has(row.response_item_uuid))
    .sort((left, right) => Number(left.__sortIndex || 0) - Number(right.__sortIndex || 0))
    .map((row) => {
      if (Array.isArray(row.children) && !row.children.length) {
        const { children, __sortIndex, ...rest } = row
        return rest
      }
      delete row.__sortIndex
      return row
    })
}

function biddingResponseIsSummaryTask(row) {
  return row?.task_display_type === 'summary_task' || Boolean(row?.has_group_children)
}

function biddingResponseIsGroupTask(row) {
  return row?.task_display_type === 'group_task' || biddingResponseGroupIndex(row) != null
}

function biddingResponseGroupIndex(row) {
  if (row?.task_group_index != null) return Number(row.task_group_index)
  const match = String(row?.response_title || '').match(/(?:（第(\d+)组）|\(第(\d+)组\))$/)
  if (!match) return null
  return Number(match[1] || match[2])
}

function biddingResponseEffectiveGroupKey(row) {
  if (row?.task_group_key) return row.task_group_key
  const baseTitle = biddingResponseGroupBaseTitle(row?.task_group_parent_title || row?.response_title)
  if (!baseTitle || baseTitle === String(row?.response_title || '').trim()) {
    if (!biddingResponseIsSummaryTask(row)) return ''
  }
  return [
    biddingResponsePrimaryRole(row) || row?.owner_role || '',
    row?.review_action || row?.normalized?.review_action || row?.response_action || '',
    row?.response_category || '',
    baseTitle,
  ].join('|')
}

function biddingResponseTaskDisplayType(row) {
  if (row?.task_display_type) return row.task_display_type
  if (biddingResponseIsGroupTask(row)) return 'group_task'
  if (biddingResponseIsSummaryTask(row)) return 'summary_task'
  return ''
}

function biddingResponseTaskDisplayLabel(row) {
  if (row?.task_display_label) return row.task_display_label
  const type = biddingResponseTaskDisplayType(row)
  if (type === 'group_task') return '分组任务'
  if (type === 'summary_task') return '汇总任务'
  return ''
}

function buildBiddingResponseVirtualGroupParent(groupKey, children) {
  const sortedChildren = [...children].sort((left, right) => Number(left.task_group_index || 0) - Number(right.task_group_index || 0))
  const first = sortedChildren[0] || {}
  const requirementCount = sortedChildren.reduce((sum, row) => sum + Number(row.covered_requirement_count || 0), 0)
  const riskCount = sortedChildren.reduce((sum, row) => sum + Number(row.covered_risk_count || 0), 0)
  return {
    ...first,
    response_item_uuid: `virtual-group:${groupKey}`,
    response_title: first.task_group_parent_title || biddingResponseGroupBaseTitle(first.response_title) || '分组任务汇总',
    source_text: `此行为前端汇总行，展开后处理 ${sortedChildren.length} 个分组任务。`,
    response_note: `当前视图缺少对应汇总父项，已自动收拢 ${sortedChildren.length} 个分组任务。`,
    status: 'pending',
    response_action: first.response_action,
    task_display_type: 'summary_task',
    task_display_label: '汇总任务',
    task_group_key: groupKey,
    task_group_child_count: sortedChildren.length,
    has_group_children: true,
    is_virtual_group_parent: true,
    quality_flags: [],
    covered_requirement_count: requirementCount,
    covered_risk_count: riskCount,
    evidence: [],
    children: sortedChildren,
    __sortIndex: Number(first.__sortIndex || 0) - 0.01,
  }
}

function biddingResponseGroupBaseTitle(value) {
  return String(value || '').replace(/（第\d+组）$/, '').replace(/\(第\d+组\)$/, '').trim()
}

function biddingResponseGroupChildren(row) {
  return Array.isArray(row?.children) ? row.children : []
}

function biddingResponseIsGroupExpanded(row) {
  if (!row?.response_item_uuid) return false
  return biddingResponseExpandedKeys.value.includes(row.response_item_uuid)
}

function toggleBiddingResponseGroup(row) {
  if (!biddingResponseGroupChildren(row).length || !row?.response_item_uuid) return
  if (biddingResponseIsGroupExpanded(row)) {
    biddingResponseExpandedKeys.value = biddingResponseExpandedKeys.value.filter((key) => key !== row.response_item_uuid)
    return
  }
  biddingResponseExpandedKeys.value = Array.from(new Set([...biddingResponseExpandedKeys.value, row.response_item_uuid]))
}

function expandAllBiddingResponseGroups() {
  biddingResponseExpandedKeys.value = biddingResponseExpandableRows.value.map((row) => row.response_item_uuid).filter(Boolean)
}

function collapseAllBiddingResponseGroups() {
  biddingResponseExpandedKeys.value = []
}

function buildBiddingResponseLocalSummary(rows = []) {
  const byStatus = {}
  const byRiskLevel = {}
  const byWorkflowAction = {}
  const byReviewRole = {}
  const byPrimaryReviewRole = {}
  const byReviewAction = {}
  const byTaskDisplayType = {}
  const byReviewPriority = {}
  const byReviewWave = {}
  const requirementIds = new Set()
  const riskIds = new Set()
  let qualityFlagCount = 0
  let splitItemCount = 0
  let clusteredRequirementCount = 0
  for (const row of rows) {
    const status = row?.status || 'pending'
    const riskLevel = row?.risk_level || 'low'
    byStatus[status] = (byStatus[status] || 0) + 1
    byRiskLevel[riskLevel] = (byRiskLevel[riskLevel] || 0) + 1
    for (const action of biddingResponseLinkedActions(row)) {
      if (!action?.action) continue
      byWorkflowAction[action.action] = (byWorkflowAction[action.action] || 0) + 1
    }
    for (const role of biddingResponseRowRoles(row)) {
      byReviewRole[role] = (byReviewRole[role] || 0) + 1
    }
    const primaryRole = biddingResponsePrimaryRole(row)
    if (primaryRole) byPrimaryReviewRole[primaryRole] = (byPrimaryReviewRole[primaryRole] || 0) + 1
    const reviewAction = row?.review_action || row?.normalized?.review_action
    if (reviewAction) byReviewAction[reviewAction] = (byReviewAction[reviewAction] || 0) + 1
    const taskDisplayType = row?.task_display_type || row?.normalized?.task_display_type
    if (taskDisplayType) byTaskDisplayType[taskDisplayType] = (byTaskDisplayType[taskDisplayType] || 0) + 1
    const reviewPriority = row?.review_priority || row?.normalized?.review_priority
    if (reviewPriority) byReviewPriority[reviewPriority] = (byReviewPriority[reviewPriority] || 0) + 1
    const reviewWave = row?.review_wave || row?.normalized?.review_wave
    if (reviewWave) byReviewWave[reviewWave] = (byReviewWave[reviewWave] || 0) + 1
    const coverage = row?.coverage || row?.normalized?.coverage || {}
    for (const id of coverage.requirement_ids || []) requirementIds.add(id)
    for (const id of coverage.risk_ids || []) riskIds.add(id)
    qualityFlagCount += biddingResponseQualityTags(row).length
    if (row?.created_from === 'quality_split' || row?.normalized?.source === 'quality_split') splitItemCount += 1
    if (row?.created_from === 'requirement_cluster' || row?.normalized?.source === 'requirement_cluster') clusteredRequirementCount += 1
  }
  return {
    item_count: rows.length,
    pending_count: byStatus.pending || 0,
    done_count: byStatus.done || 0,
    ignored_count: byStatus.ignored || 0,
    high_risk_count: byRiskLevel.high || 0,
    covered_requirement_count: requirementIds.size,
    covered_risk_count: riskIds.size,
    clustered_requirement_count: clusteredRequirementCount,
    quality_flag_count: qualityFlagCount,
    split_item_count: splitItemCount,
    by_workflow_action: byWorkflowAction,
    by_review_role: byReviewRole,
    by_primary_review_role: byPrimaryReviewRole,
    by_review_action: byReviewAction,
    by_task_display_type: byTaskDisplayType,
    by_review_priority: byReviewPriority,
    by_review_wave: byReviewWave,
  }
}

function biddingResponseActionLabel(value) {
  return optionLabel(biddingResponseActionOptions, value)
}

function biddingResponseStatusLabel(value) {
  return optionLabel(biddingResponseStatusOptions, value)
}

function biddingResponseActionTag(value) {
  if (value === 'quote_allowance') return 'warning'
  if (value === 'clarification') return 'danger'
  if (value === 'legal_review') return 'primary'
  if (value === 'qualification_material') return 'success'
  if (value === 'document_preparation') return 'info'
  return ''
}

function biddingResponseLinkedActions(row) {
  const actions = Array.isArray(row?.linked_actions) ? row.linked_actions : row?.normalized?.workflow_actions
  return Array.isArray(actions) ? actions.filter((item) => item?.action) : []
}

function biddingResponseCoverageText(row) {
  return row?.coverage_explanation || row?.normalized?.coverage_explanation || row?.coverage?.explanation || ''
}

function biddingResponseQualityText(row) {
  return row?.quality_explanation || row?.normalized?.quality_explanation || ''
}

function biddingResponseReviewActionText(row) {
  const label = row?.review_action_label || row?.normalized?.review_action_label
  if (!label) return ''
  return `复核动作：${label}`
}

function biddingResponseDoneText(row) {
  const checklist = Array.isArray(row?.done_checklist) ? row.done_checklist : row?.normalized?.done_checklist
  if (Array.isArray(checklist) && checklist.length) return checklist.slice(0, 3).join('；')
  return row?.done_criteria || row?.normalized?.done_criteria || ''
}

function biddingResponsePriorityTag(value) {
  if (value === 'P0') return 'danger'
  if (value === 'P1') return 'warning'
  if (value === 'P2') return 'primary'
  return 'info'
}

function biddingResponseQualityTags(row) {
  const flags = Array.isArray(row?.quality_flags) ? row.quality_flags : row?.normalized?.quality_flags
  return Array.isArray(flags) ? flags.filter(Boolean).slice(0, 3) : []
}

function biddingResponseQualityLabel(value) {
  const labels = {
    quality_split_child: '拆分项',
    merged_duplicates: '已合并',
    duplicate_merged: '重复项',
    overloaded_split_parent: '已拆分',
    secondary_split_child: '风险族',
    terminal_secondary_split_restored: '已恢复',
  }
  return labels[value] || value || '质量标记'
}

function biddingResponseWorkflowCount(action) {
  return Number(biddingResponseVisibleSummary.value?.by_workflow_action?.[action] || 0)
}

function biddingResponseCreatedFromLabel(value) {
  const labels = {
    business_object: '业务对象',
    risk: '风险明细',
    requirement: '招标要求',
    requirement_cluster: '要求聚类',
  }
  return labels[value] || value || '-'
}

function biddingResponseCategoryLabel(value) {
  if (['bid_rule', 'qualification', 'contract_clause', 'pricing_constraint', 'document_checklist'].includes(value)) {
    return biddingBusinessObjectTypeLabel(value)
  }
  const labels = {
    requirement: '招标要求',
    technical_requirement: '技术要求',
    risk: '风险',
    business_object: '业务对象',
  }
  return labels[value] || value || '-'
}

function biddingDraftOutlineSectionTypeLabel(value) {
  const labels = {
    business: '商务标',
    qualification: '资格资料',
    technical: '技术标',
    pricing: '报价文件',
    legal: '合同/法务',
    clarification: '答疑清单',
    attachment: '附件清单',
  }
  return labels[value] || value || '-'
}

function biddingDraftOutlineSourceText(source) {
  if (source?.source_type === 'file_format_plan') return '格式确认表'
  return '响应矩阵'
}

function biddingDraftOutlineSourceDetail(source, summary = {}) {
  if (source?.source_type === 'file_format_plan') {
    return `格式项 ${summary.format_item_count || source.format_item_count || 0} · 已映射 ${summary.mapped_format_item_count || 0} · 关联响应 ${summary.linked_response_item_count || 0}`
  }
  return `响应项 ${source?.response_item_count || 0} · 要求 ${source?.requirement_count || 0} · 风险 ${source?.risk_count || 0} · 综合拆分 ${summary.generic_split_section_count || 0}`
}

function biddingDraftOutlineMappingLabel(mapping) {
  if (!mapping) return '未映射'
  if (mapping.status !== 'mapped') return '待映射'
  const labels = {
    high: '映射高',
    medium: '映射中',
    low: '映射低',
    none: '待映射',
  }
  return labels[mapping.confidence] || '已映射'
}

function biddingDraftOutlineMappingTag(mapping) {
  if (!mapping || mapping.status !== 'mapped') return 'warning'
  if (mapping.confidence === 'high') return 'success'
  if (mapping.confidence === 'medium') return 'primary'
  return 'info'
}

function biddingFileFormatReviewStatusLabel(value) {
  const labels = {
    preview: '待生成',
    draft: '待确认',
    confirmed: '已确认',
    needs_revision: '需调整',
  }
  return labels[value] || value || '-'
}

function biddingFileFormatContentTypeLabel(value) {
  const labels = {
    fixed_form: '固定表单',
    draft_section: '正文章节',
    attachment_proof: '附件证明',
    qualification_attachment: '资格附件',
    pricing_table: '报价表',
  }
  return labels[value] || value || '-'
}

function biddingFileFormatGenerationStrategyLabel(value) {
  const labels = {
    generate_draft: '生成正文',
    from_cost_quote: '报价链路',
    manual_upload: '人工上传',
    manual_fill: '人工填表',
  }
  return labels[value] || value || '-'
}

function biddingEnterpriseProfileCategoryLabel(value) {
  return enterpriseProfileCategoryLabel(value)
}

function biddingTechnicalCompositionSourceLabel(value) {
  const labels = {
    enterprise_profile: '企业资料库',
    tender_document: '招标文件抽取',
    manual_input: '人工补充',
  }
  return labels[value] || value || '-'
}

function biddingTechnicalCompositionSourceTag(value) {
  if (value === 'enterprise_profile') return 'success'
  if (value === 'tender_document') return 'primary'
  if (value === 'manual_input') return 'warning'
  return 'info'
}

function biddingTechnicalCompositionClassLabel(value) {
  const labels = {
    fixed_enterprise_material: '固定企业资料',
    tender_extracted_content: '招标文件抽取',
    mixed: '混合来源',
    manual_input: '人工补充',
  }
  return labels[value] || value || '-'
}

function biddingTechnicalCompositionRequirementFor(component, need) {
  if (!component?.component_key || !need?.need_key) return null
  return biddingTechnicalCompositionRequirementMap.value.get(`${component.component_key}:${need.need_key}`) || null
}

function biddingMaterialRequirementStatusLabel(value) {
  const labels = {
    missing: '缺失',
    candidate_found: '有候选',
    submitted: '已提交',
    approved: '可用',
    applied: '已应用',
    not_applicable: '不适用',
  }
  return labels[value] || value || '-'
}

function biddingMaterialRequirementStatusTag(value) {
  if (value === 'missing') return 'danger'
  if (value === 'candidate_found') return 'warning'
  if (value === 'submitted') return 'primary'
  if (value === 'approved' || value === 'applied') return 'success'
  if (value === 'not_applicable') return 'info'
  return ''
}

function biddingMaterialRequirementTypeLabel(value) {
  const labels = {
    profile: '企业资料',
    field: '字段确认',
    attachment: '附件',
    section_text: '正文素材',
    form_value: '表单字段',
    pricing: '报价数据',
    other: '其他',
  }
  return labels[value] || value || '-'
}

function biddingMaterialFulfillmentModeLabel(value) {
  const labels = {
    enterprise_profile: '资料库',
    manual_upload: '人工上传',
    manual_fill: '人工填写',
    generate_draft: '生成正文',
    from_cost_quote: '报价链路',
  }
  return labels[value] || value || '-'
}

function biddingMaterialPriorityTag(value) {
  if (value === 'high') return 'danger'
  if (value === 'low') return 'info'
  return 'warning'
}

function biddingFileFormatEventTypeLabel(value) {
  const labels = {
    add_item: '新增目录项',
    move_item: '移动目录项',
    remove_item: '删除目录项',
    edit_item: '修改目录项',
  }
  return labels[value] || value || '-'
}

function biddingDraftOutlineStatusLabel(value) {
  const labels = {
    ready: '可起草',
    needs_input: '待补充',
    blocked: '阻断',
  }
  return labels[value] || value || '-'
}

function biddingDraftOutlineStatusTag(value) {
  if (value === 'ready') return 'success'
  if (value === 'blocked') return 'danger'
  if (value === 'needs_input') return 'warning'
  return 'info'
}

function biddingDraftOutlineDraftModeText(row) {
  if (row?.draft_mode_label) return row.draft_mode_label
  if (row?.can_generate_formal_draft) return '正式可成稿'
  if (row?.can_generate_placeholder_draft || row?.can_generate_draft) return '可带占位起草'
  return '暂不建议生成正文'
}

function biddingDraftOutlineGenerateButtonText(row) {
  if (row?.draft_mode === 'blocked' || row?.draft_mode === 'review_note' || row?.can_generate_review_note) {
    return '生成复核说明'
  }
  return '生成草稿'
}

function biddingDraftGenericSplitLabel(row) {
  const labels = {
    bid_guarantee: '保证金拆分',
    rejection_deviation: '废标边界拆分',
    submission_deadline: '时间规则拆分',
    submission_seal: '递交密封拆分',
    validity_evaluation: '评标规则拆分',
    clarification: '答疑拆分',
    response_table: '响应表拆分',
    document_package: '文件清单拆分',
    business_liability: '责任边界拆分',
    generic_unresolved: '综合项待拆',
  }
  return labels[row?.split_family] || '综合项拆分'
}

function biddingDraftNeedsUpgrade(rowOrDraft) {
  const draft = rowOrDraft?.draft_uuid ? rowOrDraft : biddingDraftForOutlineSection(rowOrDraft)
  return Boolean(draft?.needs_upgrade || draft?.upgrade_hint?.needs_upgrade)
}

function biddingDraftOutlineActionButtonText(row) {
  const draft = biddingDraftForOutlineSection(row)
  if (draft?.upgrade_hint?.needs_upgrade || draft?.needs_upgrade) return '升级草稿'
  if (draft) return '重新生成'
  return biddingDraftOutlineGenerateButtonText(row)
}

function biddingDraftForOutlineSection(row) {
  if (!row?.section_key) return null
  return biddingDraftSectionsByKey.value.get(row.section_key) || null
}

function biddingDraftSectionReviewLabel(value) {
  const labels = {
    draft: '待复核',
    reviewed: '已复核',
    needs_revision: '需修改',
    accepted: '已接受',
  }
  return labels[value] || value || '-'
}

function biddingDraftSectionReviewTag(value) {
  if (value === 'accepted') return 'success'
  if (value === 'reviewed') return 'primary'
  if (value === 'needs_revision') return 'warning'
  return 'info'
}

function biddingDraftQualityResultTag(value) {
  if (value === 'pass') return 'success'
  if (value === 'blocked') return 'danger'
  if (value === 'needs_material' || value === 'needs_review') return 'warning'
  return 'info'
}

function biddingDraftQualityCheckTag(value) {
  if (value === 'pass') return 'success'
  if (value === 'fail') return 'danger'
  if (value === 'warn') return 'warning'
  return 'info'
}

function biddingDraftEvidenceStatusTag(value) {
  if (value === 'supported') return 'success'
  if (value === 'unsupported') return 'danger'
  if (value === 'needs_review') return 'warning'
  return 'info'
}

function biddingDraftLlmEntryTag(entry) {
  return entry?.eligible ? 'success' : 'info'
}

function biddingDraftCanLlmEnhance(draft) {
  return Boolean(draft?.llm_entry?.eligible)
}

function biddingDraftPlanListText(value, limit = 3) {
  if (!Array.isArray(value) || !value.length) return '-'
  return value.slice(0, limit).join('；')
}

function biddingDraftSummaryListText(value, limit = 5) {
  if (!Array.isArray(value) || !value.length) return '-'
  return value.slice(0, limit).join('、')
}

function biddingDraftMarkdownPreview(value) {
  return String(value || '').trim() || '暂无正文草稿'
}

function biddingDraftVersionTypeLabel(value) {
  const labels = {
    generated: '规则生成',
    llm_generated: 'DeepSeek 生成',
    manual_edit: '人工编辑',
  }
  return labels[value] || value || '-'
}

function biddingDraftOutlineListText(value, fallback = '-') {
  if (Array.isArray(value) && value.length) return value.slice(0, 3).join('；')
  return fallback
}

function biddingBusinessObjectNeedsLlmReview(row) {
  const normalized = row?.normalized || {}
  const hasUncertainFlag = Boolean(
    normalized.weak_split || normalized.needs_llm_review || normalized.needs_secondary_split,
  )
  if (!hasUncertainFlag) return false
  if (row?.review_status && row.review_status !== 'pending') return false
  if (['pending_manual_confirm', 'accepted', 'rejected', 'modified', 'error'].includes(normalized.llm_review_status)) return false
  return true
}

function biddingLlmDecisionLabel(value) {
  const labels = {
    keep: '保留',
    rename: '建议改名/改类',
    split: '建议拆分',
    ignore: '建议忽略',
    manual_review: '转人工判断',
  }
  return labels[value] || value || '待判断'
}

function biddingLlmDecisionTag(value) {
  if (value === 'keep') return 'success'
  if (value === 'split' || value === 'rename') return 'warning'
  if (value === 'ignore') return 'info'
  if (value === 'manual_review') return 'primary'
  return ''
}

function biddingLlmReviewStatusLabel(value) {
  const labels = {
    pending_manual_confirm: '待人工确认',
    accepted: '已采纳',
    rejected: '已驳回',
    modified: '已修改',
    error: '调用异常',
  }
  return labels[value] || value || '-'
}

function biddingLlmReviewStatusTag(value) {
  if (value === 'accepted') return 'success'
  if (value === 'modified') return 'warning'
  if (value === 'rejected') return 'info'
  if (value === 'error') return 'danger'
  if (value === 'pending_manual_confirm') return 'primary'
  return ''
}

function biddingConfidenceLabel(value) {
  const score = Number(value)
  if (!Number.isFinite(score)) return '-'
  return `${Math.round(score * 100)}%`
}

function biddingBusinessObjectEvidenceQualityLabel(value) {
  const labels = {
    high: '证据高相关',
    medium: '证据中相关',
    low: '证据低相关',
  }
  return labels[value] || '证据待判定'
}

function biddingBusinessObjectEvidenceContextLabel(value) {
  const labels = {
    body: '正文',
    weak_context: '弱上下文',
    structural_noise: '结构噪声',
  }
  return labels[value] || '未判定'
}

function biddingRiskReviewLabel(status) {
  const labels = {
    pending: '待复核',
    confirmed: '已确认',
    ignored: '已忽略',
    to_clarify: '转答疑',
    to_quote_allowance: '报价预留',
    mixed: '部分处理',
  }
  return labels[status] || status || '-'
}

function biddingRiskReviewTag(status) {
  if (status === 'confirmed') return 'success'
  if (status === 'ignored') return 'info'
  if (status === 'to_clarify') return 'warning'
  if (status === 'to_quote_allowance') return 'primary'
  if (status === 'mixed') return 'warning'
  return 'danger'
}

function applyBusinessLedgerFilters() {
  businessLedgerPage.value = 1
  loadBusinessLedgers()
}

function clearCostMasterData() {
  costMasterSummary.value = null
  enterpriseQuotaItems.value = []
  enterpriseQuotaItemTotal.value = 0
  enterpriseQuotaComponents.value = []
  enterpriseQuotaComponentTotal.value = 0
  enterpriseQuotaResources.value = []
  enterpriseQuotaResourceTotal.value = 0
}

function enterpriseProfileListParams() {
  const params = {
    page: enterpriseProfilePage.value,
    page_size: enterpriseProfilePageSize,
  }
  if (enterpriseProfileFilters.category) params.category = enterpriseProfileFilters.category
  if (enterpriseProfileFilters.status) params.status = enterpriseProfileFilters.status
  if (enterpriseProfileFilters.keyword?.trim()) params.keyword = enterpriseProfileFilters.keyword.trim()
  return params
}

async function loadEnterpriseProfileSummary(options = {}) {
  if (!canViewEnterpriseProfile.value) return
  try {
    const response = await api.get('/admin/enterprise-profile/summary')
    enterpriseProfileSummary.value = responseData(response) || {}
  } catch (error) {
    enterpriseProfileSummary.value = {}
    if (isFeatureDisabled(error)) {
      enterpriseProfileFeatureDisabled.value = true
      return
    }
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '企业资料库概览加载失败'))
  }
}

async function loadEnterpriseProfileItems(options = {}) {
  if (!canViewEnterpriseProfile.value) return
  enterpriseProfileFeatureDisabled.value = false
  enterpriseProfileLoading.value = true
  try {
    const response = await api.get('/admin/enterprise-profile/items', {
      params: enterpriseProfileListParams(),
    })
    enterpriseProfileItems.value = responseData(response) || []
    enterpriseProfileTotal.value = response.data?.total ?? enterpriseProfileItems.value.length
  } catch (error) {
    enterpriseProfileItems.value = []
    enterpriseProfileTotal.value = 0
    if (isFeatureDisabled(error)) {
      enterpriseProfileFeatureDisabled.value = true
      return
    }
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '企业资料列表加载失败'))
  } finally {
    enterpriseProfileLoading.value = false
  }
}

async function refreshEnterpriseProfile(options = {}) {
  if (!canViewEnterpriseProfile.value) return
  await Promise.all([
    loadEnterpriseProfileSummary(options),
    loadEnterpriseProfileItems(options),
  ])
}

function resetEnterpriseProfileDialog(mode = 'create') {
  enterpriseProfileDialog.mode = mode
  enterpriseProfileDialog.uploading = false
  enterpriseProfileDialog.itemUuid = ''
  enterpriseProfileDialog.detail = null
  Object.assign(enterpriseProfileDialog.form, {
    category: 'basic_info',
    subcategory: '',
    profile_key: '',
    title: '',
    material_form: 'text',
    summary: '',
    content_text: '',
    attachment_file_id: '',
    attachment_type: 'source',
    attachment_description: '',
    tagsText: '',
    applicable_scope: '',
    valid_until: '',
    change_reason: '',
  })
}

async function openEnterpriseProfileDialog(mode = 'create', row = null) {
  resetEnterpriseProfileDialog(mode)
  if (row?.item_uuid) {
    enterpriseProfileDialog.itemUuid = row.item_uuid
    try {
      const response = await api.get(`/admin/enterprise-profile/items/${row.item_uuid}`)
      const detail = responseData(response)
      enterpriseProfileDialog.detail = detail
      const materialForm = detail.structured?.material_form || (detail.attachment_count ? 'attachment' : 'text')
      Object.assign(enterpriseProfileDialog.form, {
        category: detail.category || 'basic_info',
        subcategory: detail.subcategory || '',
        profile_key: detail.profile_key || '',
        title: detail.title || '',
        material_form: materialForm,
        summary: detail.summary || '',
        content_text: detail.content_text || '',
        attachment_file_id: '',
        attachment_type: 'source',
        attachment_description: '',
        tagsText: (detail.tags || []).join(', '),
        applicable_scope: detail.applicable_scope || '',
        valid_until: detail.valid_until || '',
        change_reason: '',
      })
    } catch (error) {
      ElMessage.error(apiErrorMessage(error, '企业资料详情加载失败'))
      return
    }
  }
  enterpriseProfileDialog.visible = true
}

function enterpriseProfileSubmitPayload() {
  const form = enterpriseProfileDialog.form
  const structured = {
    ...(enterpriseProfileDialog.detail?.structured || {}),
    material_form: form.material_form || 'text',
  }
  const payload = {
    category: form.category,
    subcategory: form.subcategory || null,
    profile_key: form.profile_key || null,
    title: form.title,
    summary: form.summary || form.attachment_description || null,
    content_text: form.content_text || null,
    structured,
    tags: form.tagsText
      ? form.tagsText.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
      : [],
    applicable_scope: form.applicable_scope || null,
    valid_until: form.valid_until || null,
  }
  if (enterpriseProfileDialog.mode === 'edit') {
    payload.change_reason = form.change_reason || null
  }
  return payload
}

async function submitEnterpriseProfileItem() {
  if (!enterpriseProfileDialog.form.title?.trim()) {
    ElMessage.warning('请填写资料名称')
    return
  }
  const materialForm = enterpriseProfileDialog.form.material_form || 'text'
  if (materialForm === 'text' && !enterpriseProfileDialog.form.content_text?.trim()) {
    ElMessage.warning('文本形式请填写资料内容')
    return
  }
  const hasExistingAttachment = Number(enterpriseProfileDialog.detail?.attachment_count || 0) > 0
  if (
    materialForm === 'attachment'
    && !enterpriseProfileDialog.form.attachment_file_id?.trim()
    && !(enterpriseProfileDialog.mode === 'edit' && hasExistingAttachment)
  ) {
    ElMessage.warning('附件形式请先上传资料附件')
    return
  }
  state.submitting = true
  try {
    let savedItem = null
    if (enterpriseProfileDialog.mode === 'edit') {
      const response = await api.patch(
        `/admin/enterprise-profile/items/${enterpriseProfileDialog.itemUuid}`,
        enterpriseProfileSubmitPayload(),
      )
      savedItem = responseData(response)
    } else {
      const response = await api.post('/admin/enterprise-profile/items', enterpriseProfileSubmitPayload())
      savedItem = responseData(response)
    }
    const itemUuid = enterpriseProfileDialog.itemUuid || savedItem?.item_uuid
    const attachmentFileId = enterpriseProfileDialog.form.attachment_file_id?.trim()
    if (materialForm === 'attachment' && itemUuid && attachmentFileId) {
      await bindEnterpriseProfileAttachment(itemUuid, {
        file_id: attachmentFileId,
        attachment_type: enterpriseProfileDialog.form.attachment_type || 'source',
        description: enterpriseProfileDialog.form.attachment_description || enterpriseProfileDialog.form.summary || null,
        is_primary: true,
      })
    }
    enterpriseProfileDialog.visible = false
    ElMessage.success('企业资料已保存')
    await refreshEnterpriseProfile({ silent: true })
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '企业资料保存失败'))
  } finally {
    state.submitting = false
  }
}

async function uploadEnterpriseProfileInlineAttachmentFile(uploadFile) {
  const rawFile = uploadFile?.raw
  if (!rawFile) return
  enterpriseProfileDialog.uploading = true
  try {
    const formData = new FormData()
    formData.append('file', rawFile)
    formData.append('purpose', 'enterprise_profile')
    const response = await api.post('/files', formData)
    const data = responseData(response)
    enterpriseProfileDialog.form.attachment_file_id = data.file_id
    enterpriseProfileDialog.form.attachment_description = data.original_filename || rawFile.name || ''
    ElMessage.success('附件已上传，file_id 已填入')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '附件上传失败'))
  } finally {
    enterpriseProfileDialog.uploading = false
  }
}

function openEnterpriseProfileAttachmentDialog(row) {
  enterpriseProfileAttachmentDialog.item = row
  Object.assign(enterpriseProfileAttachmentDialog.form, {
    file_id: '',
    attachment_type: 'source',
    description: '',
    is_primary: true,
  })
  enterpriseProfileAttachmentDialog.visible = true
}

async function uploadEnterpriseProfileAttachmentFile(uploadFile) {
  const rawFile = uploadFile?.raw
  if (!rawFile) return
  enterpriseProfileAttachmentDialog.uploading = true
  try {
    const formData = new FormData()
    formData.append('file', rawFile)
    formData.append('purpose', 'enterprise_profile')
    const response = await api.post('/files', formData)
    const data = responseData(response)
    enterpriseProfileAttachmentDialog.form.file_id = data.file_id
    enterpriseProfileAttachmentDialog.form.description = data.original_filename || rawFile.name || ''
    ElMessage.success('附件已上传，file_id 已填入')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '附件上传失败'))
  } finally {
    enterpriseProfileAttachmentDialog.uploading = false
  }
}

async function submitEnterpriseProfileAttachment() {
  const itemUuid = enterpriseProfileAttachmentDialog.item?.item_uuid
  const fileId = enterpriseProfileAttachmentDialog.form.file_id?.trim()
  if (!itemUuid || !fileId) {
    ElMessage.warning('请先上传附件或填写 file_id')
    return
  }
  state.submitting = true
  try {
    await bindEnterpriseProfileAttachment(itemUuid, {
      file_id: fileId,
      attachment_type: enterpriseProfileAttachmentDialog.form.attachment_type || 'source',
      description: enterpriseProfileAttachmentDialog.form.description || null,
      is_primary: Boolean(enterpriseProfileAttachmentDialog.form.is_primary),
    })
    enterpriseProfileAttachmentDialog.visible = false
    ElMessage.success('附件已绑定')
    await refreshEnterpriseProfile({ silent: true })
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '附件绑定失败'))
  } finally {
    state.submitting = false
  }
}

async function bindEnterpriseProfileAttachment(itemUuid, payload) {
  await api.post(`/admin/enterprise-profile/items/${itemUuid}/attachments`, payload)
}

async function activateEnterpriseProfileItem(row) {
  const reason = await promptText('启用原因', '请填写启用原因')
  if (!reason) return
  try {
    await api.post(`/admin/enterprise-profile/items/${row.item_uuid}/activate`, { reason })
    ElMessage.success('资料已启用')
    await refreshEnterpriseProfile({ silent: true })
  } catch (error) {
    const detail = error.response?.data?.detail
    if (detail?.code === 'ENTERPRISE_PROFILE_QUALITY_BLOCKED') {
      const labels = (detail.issues || []).map((issue) => enterpriseProfileIssueLabel(issue.code)).join('、')
      ElMessage.error(`资料体检未通过：${labels || '请补齐资料'}`)
      return
    }
    ElMessage.error(apiErrorMessage(error, '资料启用失败'))
  }
}

async function archiveEnterpriseProfileItem(row) {
  const reason = await promptText('归档原因', '请填写归档原因')
  if (!reason) return
  try {
    await api.post(`/admin/enterprise-profile/items/${row.item_uuid}/archive`, { reason })
    ElMessage.success('资料已归档')
    await refreshEnterpriseProfile({ silent: true })
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '资料归档失败'))
  }
}

async function loadCostMasterSummary(options = {}) {
  if (!canViewCostDb.value) return
  try {
    const response = await api.get('/admin/cost-master/summary')
    costMasterSummary.value = responseData(response)
  } catch (error) {
    costMasterSummary.value = null
    if (isFeatureDisabled(error)) {
      costDbFeatureDisabled.value = true
      clearCostMasterData()
      return
    }
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '企业定额主库汇总加载失败'))
  }
}

function costMasterKeywordParam() {
  const keyword = costMasterFilters.keyword.trim()
  return keyword ? { keyword } : {}
}

function costMasterListParams(page) {
  return {
    page,
    page_size: costMasterPageSize,
    ...costMasterKeywordParam(),
  }
}

async function loadEnterpriseQuotaItems(options = {}) {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  if (!options.silent) costMasterLoading.value = true
  try {
    const response = await api.get('/admin/cost-master/quota-items', {
      params: costMasterListParams(costMasterPage.value),
    })
    enterpriseQuotaItems.value = responseData(response) || []
    enterpriseQuotaItemTotal.value = response.data?.total ?? enterpriseQuotaItems.value.length
  } catch (error) {
    enterpriseQuotaItems.value = []
    enterpriseQuotaItemTotal.value = 0
    if (isFeatureDisabled(error)) {
      costDbFeatureDisabled.value = true
      return
    }
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '企业定额主项加载失败'))
  } finally {
    if (!options.silent) costMasterLoading.value = false
  }
}

async function loadEnterpriseQuotaComponents(options = {}) {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  if (!options.silent) costMasterLoading.value = true
  const params = costMasterListParams(costMasterComponentPage.value)
  const feeBucket = costMasterFilters.fee_bucket.trim()
  if (feeBucket) params.fee_bucket = feeBucket
  try {
    const response = await api.get('/admin/cost-master/components', { params })
    enterpriseQuotaComponents.value = responseData(response) || []
    enterpriseQuotaComponentTotal.value = response.data?.total ?? enterpriseQuotaComponents.value.length
  } catch (error) {
    enterpriseQuotaComponents.value = []
    enterpriseQuotaComponentTotal.value = 0
    if (isFeatureDisabled(error)) {
      costDbFeatureDisabled.value = true
      return
    }
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '企业定额组成明细加载失败'))
  } finally {
    if (!options.silent) costMasterLoading.value = false
  }
}

async function loadEnterpriseQuotaResources(options = {}) {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  if (!options.silent) costMasterLoading.value = true
  const params = costMasterListParams(costMasterResourcePage.value)
  const resourceType = costMasterFilters.resource_type.trim()
  if (resourceType) params.resource_type = resourceType
  try {
    const response = await api.get('/admin/cost-master/resources', { params })
    enterpriseQuotaResources.value = responseData(response) || []
    enterpriseQuotaResourceTotal.value = response.data?.total ?? enterpriseQuotaResources.value.length
  } catch (error) {
    enterpriseQuotaResources.value = []
    enterpriseQuotaResourceTotal.value = 0
    if (isFeatureDisabled(error)) {
      costDbFeatureDisabled.value = true
      return
    }
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '企业定额资源价格加载失败'))
  } finally {
    if (!options.silent) costMasterLoading.value = false
  }
}

function selectProjectCostImportFiles(event) {
  projectCostImportFiles.value = Array.from(event?.target?.files || []).filter((file) => {
    const name = String(file?.name || '').toLowerCase()
    return name.endsWith('.xlsx') || name.endsWith('.xlsm') || name.endsWith('.zip')
  })
  if (!projectCostImportProjectName.value && projectCostImportFiles.value[0]?.webkitRelativePath) {
    projectCostImportProjectName.value = projectCostImportFiles.value[0].webkitRelativePath.split('/')[0] || ''
  }
}

async function uploadProjectCostImport() {
  if (!canEditCostDb.value || !projectCostImportProjectName.value.trim() || !projectCostImportFiles.value.length) return
  projectCostImportUploading.value = true
  try {
    const formData = new FormData()
    formData.append('project_name', projectCostImportProjectName.value.trim())
    formData.append('source_name', `${projectCostImportFiles.value.length} 个采购资料文件`)
    projectCostImportFiles.value.forEach((file) => formData.append('files', file, file.name))
    const response = await api.post('/admin/project-cost-imports', formData)
    const batch = responseData(response)
    ElMessage.success(`解析完成：${batch.observation_count || 0} 条价格观察，${batch.candidate_count || 0} 个候选`)
    projectCostImportFiles.value = []
    if (projectCostImportFileInput.value) projectCostImportFileInput.value.value = ''
    if (projectCostImportFolderInput.value) projectCostImportFolderInput.value.value = ''
    await loadProjectCostImportBatches()
    await openProjectCostImportBatch(batch)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目采购资料解析失败'))
  } finally {
    projectCostImportUploading.value = false
  }
}

async function loadProjectCostImportBatches(options = {}) {
  if (!canViewCostDb.value || costDbFeatureDisabled.value) return
  if (!options.silent) projectCostImportLoading.value = true
  try {
    const response = await api.get('/admin/project-cost-imports', {
      params: { page: projectCostImportPage.value, page_size: projectCostImportPageSize },
    })
    projectCostImportBatches.value = responseData(response) || []
    projectCostImportTotal.value = Number(response.data?.total || projectCostImportBatches.value.length)
    const selectedId = selectedProjectCostImportBatch.value?.id
    if (selectedId) {
      selectedProjectCostImportBatch.value = projectCostImportBatches.value.find((row) => row.id === selectedId)
        || selectedProjectCostImportBatch.value
    }
  } catch (error) {
    projectCostImportBatches.value = []
    projectCostImportTotal.value = 0
    if (isFeatureDisabled(error)) return
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '项目采购入库批次加载失败'))
  } finally {
    if (!options.silent) projectCostImportLoading.value = false
  }
}

async function openProjectCostImportBatch(batch) {
  selectedProjectCostImportBatch.value = batch
  projectCostCandidatePage.value = 1
  selectedProjectCostCandidates.value = []
  await loadProjectCostCandidates()
}

async function loadProjectCostCandidates(options = {}) {
  const batchId = selectedProjectCostImportBatch.value?.id
  if (!batchId) return
  if (!options.silent) projectCostCandidateLoading.value = true
  const params = {
    page: projectCostCandidatePage.value,
    page_size: projectCostCandidatePageSize,
  }
  if (projectCostCandidateFilters.status) params.status = projectCostCandidateFilters.status
  if (projectCostCandidateFilters.risk_level) params.risk_level = projectCostCandidateFilters.risk_level
  if (projectCostCandidateFilters.keyword.trim()) params.keyword = projectCostCandidateFilters.keyword.trim()
  try {
    const response = await api.get(`/admin/project-cost-imports/${batchId}/candidates`, { params })
    projectCostCandidates.value = responseData(response) || []
    projectCostCandidateTotal.value = Number(response.data?.total || projectCostCandidates.value.length)
    selectedProjectCostCandidates.value = []
  } catch (error) {
    projectCostCandidates.value = []
    projectCostCandidateTotal.value = 0
    if (!options.silent) ElMessage.error(apiErrorMessage(error, '采购价格候选加载失败'))
  } finally {
    if (!options.silent) projectCostCandidateLoading.value = false
  }
}

function applyProjectCostCandidateFilters() {
  projectCostCandidatePage.value = 1
  loadProjectCostCandidates()
}

async function reviewSelectedProjectCostCandidates(action) {
  if (!canApproveCostDb.value || !selectedProjectCostCandidates.value.length) return
  const title = action === 'approve' ? '批量通过价格候选' : '批量驳回价格候选'
  try {
    const result = await ElMessageBox.prompt('请填写审核说明，后续可追溯到该批次和源文件。', title, {
      inputPlaceholder: '审核说明',
      inputValidator: (value) => String(value || '').trim().length >= 4 || '请至少填写 4 个字',
    })
    await api.post(`/admin/project-cost-imports/${selectedProjectCostImportBatch.value.id}/review`, {
      candidate_ids: selectedProjectCostCandidates.value.map((row) => row.id),
      action,
      note: result.value.trim(),
    })
    ElMessage.success(action === 'approve' ? '价格候选已通过' : '价格候选已驳回')
    await loadProjectCostImportBatches({ silent: true })
    await loadProjectCostCandidates()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '候选审核失败'))
  }
}

async function createProjectCostDraftVersion() {
  if (!canApproveCostDb.value || !selectedProjectCostImportBatch.value?.approved_count) return
  try {
    await ElMessageBox.confirm(
      '系统将复制当前已启用的企业定额并把已审核采购价写入新的待核定版本；当前启用版本和报价成本参考不会立即变化。确认继续？',
      '生成企业定额草稿',
      { type: 'warning', confirmButtonText: '生成草稿', cancelButtonText: '取消' },
    )
    const response = await api.post(`/admin/project-cost-imports/${selectedProjectCostImportBatch.value.id}/draft-version`, {})
    const data = responseData(response)
    selectedProjectCostImportBatch.value = data.batch
    ElMessage.success(`草稿 ${data.draft_version?.version_code || ''} 已生成，等待版本复核与启用`)
    await loadProjectCostImportBatches({ silent: true })
    await loadCostMasterSummary({ silent: true })
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '企业定额草稿生成失败'))
  }
}

async function loadCostMasterActiveList(options = {}) {
  if (costMasterActiveTab.value === 'purchaseImports') {
    await loadProjectCostImportBatches(options)
    return
  }
  if (costMasterActiveTab.value === 'components') {
    await loadEnterpriseQuotaComponents(options)
    return
  }
  if (costMasterActiveTab.value === 'resources') {
    await loadEnterpriseQuotaResources(options)
    return
  }
  await loadEnterpriseQuotaItems(options)
}

async function refreshCostMaster() {
  if (!canViewCostDb.value) return
  costDbFeatureDisabled.value = false
  costMasterLoading.value = true
  try {
    await loadCostMasterSummary({ silent: true })
    await loadCostMasterActiveList({ silent: true })
    await loadCostRagSyncStatus({ silent: true })
  } finally {
    costMasterLoading.value = false
  }
}

function applyCostMasterFilters() {
  costMasterPage.value = 1
  costMasterComponentPage.value = 1
  costMasterResourcePage.value = 1
  loadCostMasterActiveList()
}

function handleCostMasterTabClick() {
  loadCostMasterActiveList()
}

async function loadCostMeasurements() {
  costMeasurementLoading.value = true
  try {
    const response = await api.get('/admin/cost-measurements', {
      params: { page: costMeasurementPage.value, page_size: costMeasurementPageSize },
    })
    costMeasurements.value = response.data?.data || []
    costMeasurementTotal.value = Number(response.data?.total || 0)
    costMeasurementFeatureDisabled.value = false
  } catch (error) {
    costMeasurements.value = []
    costMeasurementTotal.value = 0
    if (error.response?.status === 403 && error.response?.data?.detail === 'FEATURE_DISABLED') {
      costMeasurementFeatureDisabled.value = true
      return
    }
    ElMessage.error(apiErrorMessage(error, '\u6210\u672c\u6d4b\u7b97\u5217\u8868\u52a0\u8f7d\u5931\u8d25'))
  } finally {
    costMeasurementLoading.value = false
  }
}

async function handleCostMeasurementFile(event) {
  const file = event.target?.files?.[0]
  if (event.target) event.target.value = ''
  if (!file) return
  const form = new FormData()
  form.append('file', file)
  state.submitting = true
  try {
    const response = await api.post('/admin/cost-measurements/import-preview', form)
    const preview = responseData(response)
    costMeasurementImportDialog.file = file
    costMeasurementImportDialog.preview = preview
    costMeasurementImportDialog.name = `${preview.project_name || file.name}\u6210\u672c\u6d4b\u7b97`
    costMeasurementImportDialog.project_name = preview.project_name || ''
    costMeasurementImportDialog.visible = true
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'Excel \u89e3\u6790\u5931\u8d25'))
  } finally {
    state.submitting = false
  }
}

async function commitCostMeasurementImport() {
  const file = costMeasurementImportDialog.file
  if (!file) return
  const form = new FormData()
  form.append('file', file)
  form.append('name', costMeasurementImportDialog.name || '')
  form.append('project_name', costMeasurementImportDialog.project_name || '')
  state.submitting = true
  try {
    const response = await api.post('/admin/cost-measurements/import', form)
    costMeasurementImportDialog.visible = false
    costMeasurementDetail.value = responseData(response)
    costMeasurementDrawer.visible = true
    ElMessage.success('\u6210\u672c\u6d4b\u7b97\u8349\u7a3f\u5df2\u521b\u5efa')
    await loadCostMeasurements()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '\u6210\u672c\u6d4b\u7b97\u5bfc\u5165\u5931\u8d25'))
  } finally {
    state.submitting = false
  }
}

async function openCostMeasurement(row) {
  costMeasurementLoading.value = true
  try {
    const response = await api.get(`/admin/cost-measurements/${row.id}`)
    costMeasurementDetail.value = responseData(response)
    costMeasurementDrawer.visible = true
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '\u6210\u672c\u6d4b\u7b97\u8be6\u60c5\u52a0\u8f7d\u5931\u8d25'))
  } finally {
    costMeasurementLoading.value = false
  }
}

async function saveCostMeasurementLine(row) {
  if (!costMeasurementDetail.value) return
  state.submitting = true
  try {
    const response = await api.patch(
      `/admin/cost-measurements/${costMeasurementDetail.value.id}/lines/${row.id}`,
      {
        quantity: Number(row.quantity || 0),
        labor_unit_price: Number(row.labor_unit_price || 0),
        main_material_unit_price: Number(row.main_material_unit_price || 0),
        material_loss_rate: Number(row.material_loss_rate || 0),
        auxiliary_machinery_unit_price: Number(row.auxiliary_machinery_unit_price || 0),
        subcontract_unit_price: Number(row.subcontract_unit_price || 0),
        review_status: 'reviewed',
      },
    )
    const data = responseData(response)
    Object.assign(row, data.line || {})
    Object.assign(costMeasurementDetail.value, data.summary || {})
    ElMessage.success('\u6d4b\u7b97\u884c\u5df2\u4fdd\u5b58\u5e76\u6807\u8bb0\u590d\u6838')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '\u6d4b\u7b97\u884c\u4fdd\u5b58\u5931\u8d25'))
  } finally {
    state.submitting = false
  }
}

async function recalculateCostMeasurement() {
  if (!costMeasurementDetail.value) return
  costMeasurementLoading.value = true
  try {
    const response = await api.post(`/admin/cost-measurements/${costMeasurementDetail.value.id}/recalculate`)
    costMeasurementDetail.value = responseData(response)
    ElMessage.success('\u7edf\u4e00\u91cd\u7b97\u5df2\u5b8c\u6210')
    await loadCostMeasurements()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '\u7edf\u4e00\u91cd\u7b97\u5931\u8d25'))
  } finally {
    costMeasurementLoading.value = false
  }
}

async function lockCostMeasurement() {
  if (!costMeasurementDetail.value) return
  try {
    const result = await ElMessageBox.prompt(
      '\u8bf7\u586b\u5199\u590d\u6838\u7ed3\u8bba\uff1b\u5b58\u5728\u5dee\u5f02\u6216\u4ec5\u7efc\u5408\u4ef7\u9879\u76ee\u65f6\u81f3\u5c11\u586b\u5199 6 \u4e2a\u5b57\u3002',
      '\u590d\u6838\u5e76\u9501\u5b9a\u6210\u672c\u6d4b\u7b97',
      { confirmButtonText: '\u9501\u5b9a', cancelButtonText: '\u53d6\u6d88', inputType: 'textarea' },
    )
    const response = await api.post(
      `/admin/cost-measurements/${costMeasurementDetail.value.id}/lock`,
      { note: result.value || '' },
    )
    costMeasurementDetail.value = responseData(response)
    ElMessage.success('\u6210\u672c\u6d4b\u7b97\u5df2\u9501\u5b9a')
    await loadCostMeasurements()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '\u6210\u672c\u6d4b\u7b97\u9501\u5b9a\u5931\u8d25'))
  }
}

function costMeasurementDraftStatusLabel(status) {
  const labels = {
    ready: '\u53ef\u751f\u6210',
    ready_with_archived_history: '\u53ef\u751f\u6210\uff0c\u5df2\u6709\u5f52\u6863\u5386\u53f2',
    existing_active: '\u5df2\u6709\u5df2\u542f\u7528\u6761\u76ee',
    existing_draft: '\u5df2\u6709\u5f85\u6838\u5b9a\u6761\u76ee',
    duplicate_within_measurement: '\u91cd\u590d\u5019\u9009\uff0c\u9700\u4e8c\u9009\u4e00',
    blocked: '\u5df2\u963b\u65ad',
  }
  return labels[status] || '\u5df2\u963b\u65ad'
}

function costMeasurementDraftStatusTag(status) {
  if (status === 'ready') return 'success'
  if (status === 'ready_with_archived_history' || status === 'duplicate_within_measurement') return 'warning'
  if (status === 'existing_active' || status === 'existing_draft') return 'info'
  return 'danger'
}

async function previewCostMeasurementDrafts() {
  if (!costMeasurementDetail.value) return
  costMeasurementDraftDialog.visible = true
  costMeasurementDraftDialog.loading = true
  costMeasurementDraftDialog.summary = null
  costMeasurementDraftDialog.candidates = []
  costMeasurementDraftDialog.note = ''
  try {
    const response = await api.post(
      `/admin/cost-measurements/${costMeasurementDetail.value.id}/cost-drafts/preview`,
      {},
    )
    const data = responseData(response)
    costMeasurementDraftDialog.summary = data.summary || {}
    costMeasurementDraftDialog.candidates = (data.candidates || []).map((row) => ({
      ...row,
      selected: Boolean(row.can_create && row.candidate_status !== 'duplicate_within_measurement'),
    }))
  } catch (error) {
    costMeasurementDraftDialog.visible = false
    ElMessage.error(apiErrorMessage(error, '\u6210\u672c\u5e93\u5019\u9009\u9884\u89c8\u5931\u8d25'))
  } finally {
    costMeasurementDraftDialog.loading = false
  }
}

async function commitCostMeasurementDrafts() {
  if (!costMeasurementDetail.value) return
  const lineIds = costMeasurementDraftDialog.candidates
    .filter((row) => row.can_create && row.selected)
    .map((row) => row.line_id)
  if (!lineIds.length) {
    ElMessage.warning('\u8bf7\u81f3\u5c11\u9009\u62e9 1 \u6761\u53ef\u751f\u6210\u7684\u6d4b\u7b97\u660e\u7ec6')
    return
  }
  costMeasurementDraftDialog.submitting = true
  try {
    const response = await api.post(
      `/admin/cost-measurements/${costMeasurementDetail.value.id}/cost-drafts`,
      { line_ids: lineIds, note: costMeasurementDraftDialog.note || null },
    )
    const result = responseData(response)
    costMeasurementDraftDialog.visible = false
    ElMessage.success(`\u5df2\u751f\u6210 ${result.created_count || 0} \u6761\u5f85\u6838\u5b9a\u6210\u672c\u6761\u76ee\uff0c\u8df3\u8fc7 ${result.skipped_count || 0} \u6761`)
    await openCostMeasurement({ id: costMeasurementDetail.value.id })
    await loadCostMeasurements()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '\u751f\u6210\u5f85\u6838\u5b9a\u6210\u672c\u6761\u76ee\u5931\u8d25'))
  } finally {
    costMeasurementDraftDialog.submitting = false
  }
}

async function exportCostMeasurement() {
  if (!costMeasurementDetail.value) return
  try {
    const response = await api.get(
      `/admin/cost-measurements/${costMeasurementDetail.value.id}/export`,
      { responseType: 'blob' },
    )
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = `${costMeasurementDetail.value.measurement_code}-${costMeasurementDetail.value.name}.xlsx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '\u6210\u672c\u6d4b\u7b97\u5bfc\u51fa\u5931\u8d25'))
  }
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
    else ElMessage.error(apiErrorMessage(error, '历史成本条目加载失败'))
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
      ElMessage.error(apiErrorMessage(error, '成本参考更新状态加载失败'))
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
    await ElMessageBox.confirm('确认将当前已启用的企业定额更新为报价成本参考？', '更新报价成本参考', {
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
    ElMessage.success(response.data?.message || `已更新 ${data.synced_count || 0} 条已启用成本条目`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '更新已启用成本条目失败'))
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
      `确认将选中条目中的 ${selectedDraftCostItemCount.value} 条待核定条目批量设为已启用？请输入核定原因`,
      '批量核定为启用',
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
      `确认将选中条目中的 ${selectedActiveCostItemCount.value} 条已启用条目批量恢复为待核定？请输入恢复原因`,
      '批量恢复为待核定',
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

async function openEnterpriseQuotaItemDetail(row) {
  const itemId = firstPositiveId(row?.enterprise_quota_item_id, row?.id)
  if (!itemId) return
  enterpriseQuotaItemDrawer.visible = true
  enterpriseQuotaItemDrawer.loading = true
  enterpriseQuotaItemDrawer.item = null
  try {
    const response = await api.get(`/admin/cost-master/quota-items/${itemId}`)
    enterpriseQuotaItemDrawer.item = responseData(response)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '企业定额主项详情加载失败'))
  } finally {
    enterpriseQuotaItemDrawer.loading = false
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

function currentProjectId() {
  const match = window.location.pathname.match(/^\/admin\/projects\/(\d+)$/)
  return match ? Number(match[1]) : null
}

async function loadProjectDashboard() {
  dashboardFeature.projectDisabled = false
  try {
    const response = await api.get('/admin/dashboard/projects')
    projectDashboard.value = responseData(response)
    return true
  } catch (error) {
    projectDashboard.value = null
    if (isFeatureDisabled(error) || error.response?.data?.detail === 'NOT_FOUND') {
      dashboardFeature.projectDisabled = true
      return false
    }
    else throw error
  }
}

async function loadProjects() {
  projectFeatureDisabled.value = false
  const params = {
    page: projectPage.value,
    page_size: projectPageSize,
  }
  if (projectFilters.status) params.status = projectFilters.status
  if (projectFilters.risk_level) params.risk_level = projectFilters.risk_level
  const keyword = projectFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  try {
    const response = await api.get('/admin/projects', { params })
    projects.value = responseData(response) || []
    projectTotal.value = response.data?.total ?? projects.value.length
  } catch (error) {
    projects.value = []
    projectTotal.value = 0
    if (error.response?.status === 404) {
      projectFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '项目加载失败'))
  }
}

function applyProjectFilters() {
  projectPage.value = 1
  loadProjects()
}

async function loadProjectEvents(projectId = currentProjectId()) {
  if (!projectId) return
  try {
    const response = await api.get(`/admin/projects/${projectId}/events`)
    projectEvents.value = responseData(response) || []
  } catch (error) {
    projectEvents.value = []
    ElMessage.error(apiErrorMessage(error, '项目动态加载失败'))
  }
}

async function loadProjectDetail() {
  projectFeatureDisabled.value = false
  const projectId = currentProjectId()
  if (!projectId) {
    state.error = 'forbidden'
    return
  }
  try {
    const response = await api.get(`/admin/projects/${projectId}`)
    projectDetail.value = responseData(response)
    await loadProjectEvents(projectId)
  } catch (error) {
    projectDetail.value = null
    projectEvents.value = []
    if (error.response?.status === 404) {
      projectFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '项目详情加载失败'))
  }
}

async function loadMyProjectTasks() {
  projectFeatureDisabled.value = false
  const params = {
    page: myProjectTaskPage.value,
    page_size: myProjectTaskPageSize,
  }
  if (myProjectTaskFilters.status) params.status = myProjectTaskFilters.status
  const keyword = myProjectTaskFilters.keyword.trim()
  if (keyword) params.keyword = keyword
  try {
    const response = await api.get('/admin/project-tasks/my', { params })
    myProjectTasks.value = responseData(response) || []
    myProjectTaskTotal.value = response.data?.total ?? myProjectTasks.value.length
  } catch (error) {
    myProjectTasks.value = []
    myProjectTaskTotal.value = 0
    if (error.response?.status === 404) {
      projectFeatureDisabled.value = true
      return
    }
    if (error.response?.status === 401) state.error = 'unauthorized'
    else if (error.response?.status === 403) state.error = 'forbidden'
    else ElMessage.error(apiErrorMessage(error, '我的项目任务加载失败'))
  }
}

function applyMyProjectTaskFilters() {
  myProjectTaskPage.value = 1
  loadMyProjectTasks()
}

async function openProjectCreate() {
  if (!canManageProjectProgress.value) return
  await loadProjectUsers()
  projectDialog.form.name = ''
  projectDialog.form.client_name = ''
  projectDialog.form.project_manager_id = projectUserOptions.value[0]?.id ?? session.user?.id ?? null
  projectDialog.form.owner_department = ''
  projectDialog.form.address = ''
  projectDialog.form.planned_start_at = ''
  projectDialog.form.planned_finish_at = ''
  projectDialog.form.description = ''
  projectDialog.visible = true
}

async function createProject() {
  if (!projectDialog.form.name.trim() || !projectDialog.form.project_manager_id) {
    ElMessage.warning('请填写项目名称并选择项目经理')
    return
  }
  state.submitting = true
  try {
    const response = await api.post('/admin/projects', {
      name: projectDialog.form.name,
      client_name: projectDialog.form.client_name,
      project_manager_id: projectDialog.form.project_manager_id,
      owner_department: projectDialog.form.owner_department,
      address: projectDialog.form.address,
      planned_start_at: projectDialog.form.planned_start_at || null,
      planned_finish_at: projectDialog.form.planned_finish_at || null,
      description: projectDialog.form.description,
    })
    projectDialog.visible = false
    ElMessage.success('已创建项目')
    const project = responseData(response)
    navigate(`/admin/projects/${project.id}`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '创建项目失败'))
  } finally {
    state.submitting = false
  }
}

function openProjectTrialCreate() {
  if (!canManageProjectProgress.value) return
  const start = new Date()
  start.setMinutes(0, 0, 0)
  const finish = new Date(start)
  finish.setDate(finish.getDate() + 30)
  const dateText = `${start.getFullYear()}${String(start.getMonth() + 1).padStart(2, '0')}${String(start.getDate()).padStart(2, '0')}`
  projectTrialDialog.form.name = `项目-${dateText}`
  projectTrialDialog.form.client_name = ''
  projectTrialDialog.form.owner_department = '工程部'
  projectTrialDialog.form.address = ''
  projectTrialDialog.form.planned_start_at = formatDateTimeInput(start)
  projectTrialDialog.form.planned_finish_at = formatDateTimeInput(finish)
  projectTrialDialog.form.description = ''
  projectTrialDialog.visible = true
}

async function createProjectTrial() {
  if (!projectTrialDialog.form.name.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  state.submitting = true
  try {
    const response = await api.post('/admin/projects/trial-template', {
      name: projectTrialDialog.form.name,
      client_name: projectTrialDialog.form.client_name,
      owner_department: projectTrialDialog.form.owner_department,
      address: projectTrialDialog.form.address,
      planned_start_at: projectTrialDialog.form.planned_start_at || null,
      planned_finish_at: projectTrialDialog.form.planned_finish_at || null,
      description: projectTrialDialog.form.description,
    })
    projectTrialDialog.visible = false
    ElMessage.success('已创建项目')
    const project = responseData(response)
    navigate(`/admin/projects/${project.id}`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '创建项目失败'))
  } finally {
    state.submitting = false
  }
}

function openProjectEpcCreate() {
  if (!canManageProjectProgress.value) return
  const start = new Date()
  start.setMinutes(0, 0, 0)
  const finish = new Date(start)
  finish.setDate(finish.getDate() + 180)
  const dateText = `${start.getFullYear()}${String(start.getMonth() + 1).padStart(2, '0')}${String(start.getDate()).padStart(2, '0')}`
  projectEpcDialog.form.name = `旗胜EPC项目-${dateText}`
  projectEpcDialog.form.client_name = ''
  projectEpcDialog.form.owner_department = '项目管理部'
  projectEpcDialog.form.address = ''
  projectEpcDialog.form.mode = 'compact'
  projectEpcDialog.form.planned_start_at = formatDateTimeInput(start)
  projectEpcDialog.form.planned_finish_at = formatDateTimeInput(finish)
  projectEpcDialog.form.description = ''
  projectEpcDialog.visible = true
}

async function createProjectEpc() {
  if (!projectEpcDialog.form.name.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  state.submitting = true
  try {
    const response = await api.post('/admin/projects/epc-template', {
      name: projectEpcDialog.form.name,
      client_name: projectEpcDialog.form.client_name,
      owner_department: projectEpcDialog.form.owner_department,
      address: projectEpcDialog.form.address,
      mode: projectEpcDialog.form.mode,
      planned_start_at: projectEpcDialog.form.planned_start_at || null,
      planned_finish_at: projectEpcDialog.form.planned_finish_at || null,
      description: projectEpcDialog.form.description,
    })
    projectEpcDialog.visible = false
    const project = responseData(response)
    ElMessage.success(`已创建EPC流程项目（${project.template?.task_count || 0}个节点）`)
    navigate(`/admin/projects/${project.id}`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '创建EPC项目失败'))
  } finally {
    state.submitting = false
  }
}

async function openProjectTaskCreate(stage = null) {
  if (!canManageProjectProgress.value || !projectDetail.value) return
  await loadProjectUsers()
  projectTaskDialog.form.stage_id = stage?.id || projectDetail.value.current_stage_id || projectDetail.value.stages?.[0]?.id || null
  projectTaskDialog.form.title = ''
  projectTaskDialog.form.owner_user_id = projectUserOptions.value[0]?.id ?? session.user?.id ?? null
  projectTaskDialog.form.owner_role = stage?.owner_role || ''
  projectTaskDialog.form.priority = 'normal'
  projectTaskDialog.form.planned_start_at = ''
  projectTaskDialog.form.due_at = ''
  projectTaskDialog.form.next_action = ''
  projectTaskDialog.form.description = ''
  projectTaskDialog.visible = true
}

async function createProjectTask() {
  if (!projectDetail.value) return
  if (!projectTaskDialog.form.stage_id || !projectTaskDialog.form.title.trim() || !projectTaskDialog.form.owner_user_id) {
    ElMessage.warning('请补齐阶段、任务标题和负责人')
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/projects/${projectDetail.value.id}/tasks`, {
      stage_id: projectTaskDialog.form.stage_id,
      title: projectTaskDialog.form.title,
      owner_user_id: projectTaskDialog.form.owner_user_id,
      owner_role: projectTaskDialog.form.owner_role,
      priority: projectTaskDialog.form.priority,
      planned_start_at: projectTaskDialog.form.planned_start_at || null,
      due_at: projectTaskDialog.form.due_at || null,
      next_action: projectTaskDialog.form.next_action,
      description: projectTaskDialog.form.description,
    })
    projectTaskDialog.visible = false
    ElMessage.success('已创建项目任务')
    await loadProjectDetail()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '创建项目任务失败'))
  } finally {
    state.submitting = false
  }
}

function resetProjectEvidencePayload() {
  projectEvidenceDrawer.form.title = ''
  projectEvidenceDrawer.form.description = ''
  projectEvidenceDrawer.form.external_url = ''
  projectEvidenceDrawer.form.external_provider = 'other'
  projectEvidenceDrawer.file = null
}

function resetProjectEvidenceForm(type = 'text') {
  projectEvidenceDrawer.form.evidence_type = type
  resetProjectEvidencePayload()
}

async function openProjectTaskEvidence(row) {
  projectEvidenceDrawer.task = row
  projectEvidenceDrawer.summary = {
    requirement: row.evidence_requirement || row.epc_deliverable || '',
    evidence_count: row.evidence_count || 0,
  }
  projectEvidenceDrawer.items = []
  resetProjectEvidenceForm('text')
  projectEvidenceDrawer.visible = true
  await loadProjectTaskEvidences()
}

async function loadProjectTaskEvidences() {
  if (!projectEvidenceDrawer.task?.id) return
  projectEvidenceDrawer.loading = true
  try {
    const response = await api.get(`/admin/project-tasks/${projectEvidenceDrawer.task.id}/evidences`)
    const data = responseData(response) || {}
    projectEvidenceDrawer.summary = data
    projectEvidenceDrawer.items = data.items || []
    projectEvidenceDrawer.task.evidence_count = data.evidence_count || 0
    projectEvidenceDrawer.task.has_evidence = Boolean(data.evidence_count)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '加载成果证据失败'))
  } finally {
    projectEvidenceDrawer.loading = false
  }
}

function handleProjectEvidenceFileChange(uploadFile) {
  projectEvidenceDrawer.file = uploadFile?.raw || null
  if (!projectEvidenceDrawer.form.title && projectEvidenceDrawer.file?.name) {
    projectEvidenceDrawer.form.title = projectEvidenceDrawer.file.name
  }
}

function clearProjectEvidenceFile() {
  projectEvidenceDrawer.file = null
}

async function createProjectEvidence() {
  const task = projectEvidenceDrawer.task
  if (!task?.id) return
  const type = projectEvidenceDrawer.form.evidence_type
  const title = projectEvidenceDrawer.form.title.trim()
  if (!title) {
    ElMessage.warning('请填写成果标题')
    return
  }
  const payload = {
    evidence_type: type,
    title,
    description: projectEvidenceDrawer.form.description,
  }
  if (type === 'link') {
    if (!projectEvidenceDrawer.form.external_url.trim()) {
      ElMessage.warning('请填写外部链接')
      return
    }
    payload.external_url = projectEvidenceDrawer.form.external_url
    payload.external_provider = projectEvidenceDrawer.form.external_provider
  }
  state.submitting = true
  try {
    if (type === 'file') {
      if (!projectEvidenceDrawer.file) {
        ElMessage.warning('请选择文件')
        return
      }
      const formData = new FormData()
      formData.append('file', projectEvidenceDrawer.file)
      formData.append('purpose', 'project_task_evidence')
      const uploadResponse = await api.post('/files', formData)
      payload.file_object_id = responseData(uploadResponse)?.file_id
    }
    await api.post(`/admin/project-tasks/${task.id}/evidences`, payload)
    ElMessage.success('已新增成果证据')
    resetProjectEvidenceForm(type)
    await loadProjectTaskEvidences()
    if (routeName.value === 'projectDetail') await loadProjectDetail()
    if (routeName.value === 'projectMyTasks') await loadMyProjectTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '新增成果证据失败'))
  } finally {
    state.submitting = false
  }
}

async function openProjectEvidence(row) {
  if (row.evidence_type === 'link' && row.external_url) {
    window.open(row.external_url, '_blank', 'noopener')
    return
  }
  if (row.evidence_type !== 'file') return
  try {
    const response = await api.get(`/admin/project-task-evidences/${row.id}/download_url`)
    const data = responseData(response)
    if (data?.download_url) window.open(data.download_url, '_blank', 'noopener')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '打开成果文件失败'))
  }
}

async function removeProjectEvidence(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请填写删除原因', '删除成果证据', {
      inputPattern: /\S+/,
      inputErrorMessage: '删除原因不能为空',
      confirmButtonText: '确认删除',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.delete(`/admin/project-task-evidences/${row.id}`, { data: { reason } })
    ElMessage.success('已删除成果证据')
    await loadProjectTaskEvidences()
    if (routeName.value === 'projectDetail') await loadProjectDetail()
    if (routeName.value === 'projectMyTasks') await loadMyProjectTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '删除成果证据失败'))
  } finally {
    state.submitting = false
  }
}

function rollbackTargetStatus(row) {
  const targets = {
    started: 'todo',
    progressing: 'started',
    submitted: 'progressing',
    done: 'submitted',
  }
  return targets[row?.status] || ''
}

function canRollbackProjectTask(row) {
  if (!row || ['todo', 'blocked', 'cancelled'].includes(row.status)) return false
  if (row.status === 'done' && !canManageProjectProgress.value) return false
  return Boolean(rollbackTargetStatus(row))
}

function projectTaskNeedsEvidence(row) {
  return Boolean(row?.evidence_requirement || row?.epc_deliverable)
}

function projectTaskRequiresHardGate(row) {
  return row?.evidence_policy === 'complete_required'
}

function projectEvidenceButtonType(row) {
  if (projectTaskRequiresHardGate(row) && !row?.evidence_count) return 'danger'
  if (row?.evidence_count) return 'primary'
  return 'warning'
}

function setProjectTaskEvidenceFilter(filter) {
  projectTaskEvidenceFilter.value = filter || 'all'
}

function projectTaskEvidenceFilterLabel(filter) {
  const labels = {
    required: '只看有成果要求',
    evidenced: '只看已留证据',
    missing: '只看缺证据',
    done_missing: '只看无证据已完成',
    open_missing: '只看未完成且缺证据',
  }
  return labels[filter] || '全部任务'
}

async function advanceProjectTask(row, action) {
  const messages = {
    start: '已开始',
    progress: '已推进到 50%',
    submit: '已提交确认',
    complete: '已确认完成',
    unblock: '已解除阻塞',
  }
  const payload = {}
  if (projectTaskNeedsEvidence(row) && !row.evidence_count && action === 'submit') {
    try {
      await ElMessageBox.confirm('当前节点尚未登记成果证据，建议补充后再提交。是否仍继续提交？', '缺少成果证据', {
        confirmButtonText: '继续提交',
        cancelButtonText: '先补证据',
        type: 'warning',
      })
      payload.confirm_without_evidence_reason = '用户确认无证据提交'
    } catch {
      return
    }
  }
  if (projectTaskNeedsEvidence(row) && !row.evidence_count && action === 'complete') {
    if (projectTaskRequiresHardGate(row)) {
      if (!canManageProjectProgress.value) {
        await ElMessageBox.alert('该关键节点要求成果证据，请先登记证据再完成，或联系项目经理放行。', '关键节点缺成果证据', {
          confirmButtonText: '知道了',
          type: 'warning',
        })
        return
      }
      try {
        const result = await ElMessageBox.prompt('当前关键节点尚未登记成果证据。若确认线下已有依据，请填写放行原因。', '关键节点缺成果证据', {
          inputValidator: (value) => (value || '').trim().length >= 6 || '放行原因至少 6 个字',
          confirmButtonText: '放行完成',
          cancelButtonText: '先补证据',
          type: 'warning',
        })
        payload.bypass_reason = result.value.trim()
      } catch {
        return
      }
    } else {
      try {
        const result = await ElMessageBox.prompt('当前节点尚未登记成果证据，请填写仍确认完成的说明', '无证据确认完成', {
          inputPattern: /\S+/,
          inputErrorMessage: '确认说明不能为空',
          confirmButtonText: '确认完成',
          cancelButtonText: '先补证据',
          type: 'warning',
        })
        payload.confirm_without_evidence_reason = result.value
      } catch {
        return
      }
    }
  }
  state.submitting = true
  try {
    await api.post(`/admin/project-tasks/${row.id}/${action}`, Object.keys(payload).length ? payload : undefined)
    ElMessage.success(messages[action] || '任务已更新')
    if (routeName.value === 'projectDetail') await loadProjectDetail()
    if (routeName.value === 'projectMyTasks') await loadMyProjectTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '任务更新失败'))
  } finally {
    state.submitting = false
  }
}

async function rollbackProjectTask(row) {
  const targetStatus = rollbackTargetStatus(row)
  if (!targetStatus) return
  const targetLabel = projectTaskStatusLabel(targetStatus)
  let reason = ''
  try {
    const result = await ElMessageBox.prompt(`回退到「${targetLabel}」，请填写原因`, '回退任务进度', {
      inputPattern: /\S+/,
      inputErrorMessage: '回退原因不能为空',
      confirmButtonText: '确认回退',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/project-tasks/${row.id}/rollback`, { target_status: targetStatus, reason })
    ElMessage.success(`已回退到${targetLabel}`)
    if (routeName.value === 'projectDetail') await loadProjectDetail()
    if (routeName.value === 'projectMyTasks') await loadMyProjectTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '回退任务失败'))
  } finally {
    state.submitting = false
  }
}

async function blockProjectTask(row) {
  let reason = ''
  try {
    const result = await ElMessageBox.prompt('请输入阻塞原因', '标记任务阻塞', {
      inputPattern: /\S+/,
      inputErrorMessage: '阻塞原因不能为空',
      confirmButtonText: '确认阻塞',
      cancelButtonText: '返回',
      type: 'warning',
    })
    reason = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/project-tasks/${row.id}/block`, { reason })
    ElMessage.success('已标记阻塞')
    if (routeName.value === 'projectDetail') await loadProjectDetail()
    if (routeName.value === 'projectMyTasks') await loadMyProjectTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '标记阻塞失败'))
  } finally {
    state.submitting = false
  }
}

async function unblockProjectTask(row) {
  let resolution = ''
  try {
    const result = await ElMessageBox.prompt('请填写阻塞如何解决', '解除任务阻塞', {
      inputPattern: /\S+/,
      inputErrorMessage: '解决说明不能为空',
      confirmButtonText: '确认解除',
      cancelButtonText: '返回',
      type: 'success',
    })
    resolution = result.value
  } catch {
    return
  }
  state.submitting = true
  try {
    await api.post(`/admin/project-tasks/${row.id}/unblock`, { resolution, next_action: row.next_action || '' })
    ElMessage.success('已解除阻塞')
    if (routeName.value === 'projectDetail') await loadProjectDetail()
    if (routeName.value === 'projectMyTasks') await loadMyProjectTasks()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '解除阻塞失败'))
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
  dashboardFeature.businessDisabled = false
  dashboardFeature.quoteDisabled = false
  dashboardFeature.responseDisabled = false
  dashboardFeature.executionDisabled = false
  dashboardFeature.projectDisabled = false
  clientInquiryPage.value = 1
  quoteJobPage.value = 1
  let loadedCount = 0
  try {
    if (canViewDashboardMetrics.value) {
      try {
        const response = await api.get('/admin/dashboard/business-lite', {
          params: { range: dashboardRange.value },
        })
        businessDashboard.value = responseData(response)
        loadedCount += 1
      } catch (error) {
        businessDashboard.value = null
        if (isFeatureDisabled(error)) dashboardFeature.businessDisabled = true
        else throw error
      }
    } else {
      businessDashboard.value = null
      dashboardFeature.businessDisabled = true
    }

    if (canViewDashboardMetrics.value) {
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
    } else {
      quoteDashboard.value = null
      dashboardFeature.quoteDisabled = true
    }

    if (canViewDashboardMetrics.value) {
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
    } else {
      responseDashboard.value = null
      clientInquiries.value = []
      clientInquiryTotal.value = 0
      dashboardFeature.responseDisabled = true
    }

    if (canViewDashboardMetrics.value) {
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
    } else {
      executionDashboard.value = null
      dashboardFeature.executionDisabled = true
    }

    if (canViewDashboardMetrics.value) {
      try {
        if (await loadProjectDashboard()) loadedCount += 1
      } catch (error) {
        projectDashboard.value = null
        if (isFeatureDisabled(error)) dashboardFeature.projectDisabled = true
        else throw error
      }
    } else {
      projectDashboard.value = null
      dashboardFeature.projectDisabled = true
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
    if (!dashboardFeature.businessDisabled) availableTabs.push('business')
    if (!dashboardFeature.quoteDisabled) availableTabs.push('quote')
    if (!dashboardFeature.responseDisabled) availableTabs.push('response')
    if (canViewQuoteOperations.value) availableTabs.push('operations')
    if (!dashboardFeature.executionDisabled) availableTabs.push('execution')
    if (!dashboardFeature.projectDisabled) availableTabs.push('projects')
    if (!availableTabs.includes(dashboardTab.value)) {
      dashboardTab.value = availableTabs[0] || 'quote'
    }
  } catch (error) {
    businessDashboard.value = null
    quoteDashboard.value = null
    responseDashboard.value = null
    executionDashboard.value = null
    projectDashboard.value = null
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
  if (routeName.value === 'login') {
    if (!localStorage.getItem(TOKEN_KEY)) return
    state.loading = true
    state.error = ''
    try {
      const me = await loadMe()
      window.location.replace(landingPath(me))
    } catch (error) {
      if ([401, 403].includes(error.response?.status)) {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(USER_INFO_KEY)
        session.user = null
      } else {
        ElMessage.warning('暂时无法验证已有登录状态，请稍后重试')
      }
    } finally {
      state.loading = false
    }
    return
  }
  state.loading = true
  state.error = ''
  try {
    await loadMe()
    if (routeName.value === 'noAccess') return
    if (routeName.value === 'quoteNew') {
      if (!canOpenLegacyQuote.value) state.error = 'forbidden'
      return
    }
    if (routeName.value === 'dashboard') {
      if (!canViewDashboard.value) {
        state.error = 'forbidden'
        return
      }
      await loadDashboards()
      return
    }
    if (routeName.value === 'budgetProjects' || routeName.value === 'budgetProjectDetail') {
      if (!canViewBudgetProjects.value) state.error = 'forbidden'
      return
    }
    if (routeName.value === 'accountQuotas') {
      if (!canViewAccountQuotas.value) state.error = 'forbidden'
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
    if (routeName.value === 'projects') {
      if (!canViewProjectProgress.value) {
        state.error = 'forbidden'
        return
      }
      await loadProjects()
      return
    }
    if (routeName.value === 'projectDetail') {
      if (!canViewProjectProgress.value) {
        state.error = 'forbidden'
        return
      }
      if (canManageProjectProgress.value) await loadProjectUsers()
      await loadProjectDetail()
      return
    }
    if (routeName.value === 'projectMyTasks') {
      if (!canViewProjectProgress.value) {
        state.error = 'forbidden'
        return
      }
      await loadMyProjectTasks()
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
    if (routeName.value === 'bidding') {
      if (!canViewBidding.value) {
        state.error = 'forbidden'
        return
      }
      await loadBiddingProjects()
      return
    }
    if (routeName.value === 'enterpriseProfile') {
      if (!canViewEnterpriseProfile.value) {
        state.error = 'forbidden'
        return
      }
      await refreshEnterpriseProfile()
      return
    }
    if (routeName.value === 'costMeasurement') {
      if (!canViewCostMeasurement.value) {
        state.error = 'forbidden'
        return
      }
      await loadCostMeasurements()
      return
    }
    if (routeName.value === 'costDb') {
      if (!canViewCostDb.value) {
        state.error = 'forbidden'
        return
      }
      await refreshCostMaster()
      await loadCostItems()
      const urlParams = new URLSearchParams(window.location.search)
      const enterpriseQuotaItemId = positiveId(urlParams.get('enterprise_quota_item_id'))
      if (enterpriseQuotaItemId) {
        await openEnterpriseQuotaItemDetail({ id: enterpriseQuotaItemId })
      }
      const costItemId = positiveId(urlParams.get('cost_item_id'))
      if (costItemId) {
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
    if (routeName.value === 'dwgTrial') {
      if (!canViewDwgTrial.value) {
        state.error = 'forbidden'
        return
      }
      await loadDwgTrialLatest({ quiet: true })
      return
    }
    if (routeName.value === 'agentCenter') {
      if (!canViewAgentCenter.value) {
        state.error = 'forbidden'
        return
      }
      await refreshAgentCenter()
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
  localStorage.removeItem(USER_INFO_KEY)
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

onBeforeUnmount(() => {
  clearBiddingImportantInfoProgressTimer()
  clearBiddingRiskClauseProgressTimer()
})
</script>
