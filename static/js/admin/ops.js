(function (window) {
  'use strict';

  const EMPTY_DASHBOARD = { services: [], alerts: [], jobs: {}, logs: {} };

  function createOpsModule(options) {
    const {
      ref,
      computed,
      axios,
      ElMessage,
      ElNotification,
      coreApiBaseUrl,
      authHeaders,
      apiData,
      apiErrorMessage,
      refreshIntervalMs = 60000,
    } = options;

    const opsDashboard = ref({ ...EMPTY_DASHBOARD });
    const opsLoading = ref(false);
    const opsAckLoading = ref(false);
    const opsLastAlertKey = ref('');
    let opsTimer = null;

    const opsServices = computed(() => opsDashboard.value.services || []);
    const opsAlerts = computed(() => opsDashboard.value.alerts || []);
    const opsJobs = computed(() => opsDashboard.value.jobs || {});
    const opsLogs = computed(() => opsDashboard.value.logs || {});
    const opsStuckJobs = computed(() => opsJobs.value.stuck_jobs || []);
    const opsLogItems = computed(() => opsLogs.value.items || []);

    const opsOverallType = (status) => {
      if (status === 'ready') return 'success';
      if (status === 'degraded') return 'warning';
      return 'info';
    };

    const opsLogStatusLabel = (row) => {
      if (!row) return '历史';
      if (row.status === 'current') return '当前';
      if (row.status === 'acknowledged') return '已读';
      return '历史';
    };

    const opsLogStatusType = (row) => {
      if (!row) return 'info';
      if (row.status === 'current') return 'warning';
      if (row.status === 'acknowledged') return 'info';
      return 'success';
    };

    const notifyOpsAlerts = (alerts) => {
      if (!alerts || alerts.length === 0) {
        opsLastAlertKey.value = '';
        return;
      }
      const alertKey = alerts.map(item => `${item.level}:${item.title}:${item.message}`).join('|');
      if (alertKey === opsLastAlertKey.value) return;
      opsLastAlertKey.value = alertKey;
      const hasCritical = alerts.some(item => item.level === 'critical');
      ElNotification({
        title: hasCritical ? 'Ops Alert' : 'Ops Reminder',
        message: alerts.map(item => `${item.title}: ${item.message}`).join('\n'),
        type: hasCritical ? 'error' : 'warning',
        duration: 7000,
      });
    };

    const fetchOpsDashboard = async (notify = false) => {
      opsLoading.value = true;
      try {
        const res = await axios.get(`${coreApiBaseUrl}/admin/ops/dashboard`, { headers: authHeaders() });
        opsDashboard.value = apiData(res, EMPTY_DASHBOARD) || { ...EMPTY_DASHBOARD };
        if (notify) notifyOpsAlerts(opsDashboard.value.alerts || []);
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, 'Failed to load ops dashboard'));
      } finally {
        opsLoading.value = false;
      }
    };

    const acknowledgeCurrentOpsLogs = async () => {
      if ((opsLogs.value.current_event_count || 0) <= 0) {
        ElMessage.info('暂无需要标记已读的当前异常');
        return;
      }
      opsAckLoading.value = true;
      try {
        const res = await axios.post(
          `${coreApiBaseUrl}/admin/ops/logs/acknowledge`,
          {},
          { headers: authHeaders() },
        );
        const result = apiData(res, {}) || {};
        const count = result.acknowledged_count || 0;
        opsDashboard.value = {
          ...opsDashboard.value,
          logs: result.logs || opsDashboard.value.logs || {},
        };
        opsLastAlertKey.value = '';
        await fetchOpsDashboard(false);
        ElMessage.success(count > 0 ? `已标记 ${count} 起当前异常日志为已读` : '当前没有新的异常日志需要标记');
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '标记异常日志已读失败'));
      } finally {
        opsAckLoading.value = false;
      }
    };

    const startOpsPolling = () => {
      fetchOpsDashboard(true);
      if (opsTimer) clearInterval(opsTimer);
      opsTimer = setInterval(() => fetchOpsDashboard(true), refreshIntervalMs);
    };

    const stopOpsPolling = () => {
      if (!opsTimer) return;
      clearInterval(opsTimer);
      opsTimer = null;
    };

    return {
      opsDashboard,
      opsLoading,
      opsAckLoading,
      opsServices,
      opsAlerts,
      opsJobs,
      opsLogs,
      opsStuckJobs,
      opsLogItems,
      fetchOpsDashboard,
      acknowledgeCurrentOpsLogs,
      opsOverallType,
      opsLogStatusLabel,
      opsLogStatusType,
      startOpsPolling,
      stopOpsPolling,
    };
  }

  window.AIMOAdminOps = { createOpsModule };
})(window);
