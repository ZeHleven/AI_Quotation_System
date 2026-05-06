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
      opsServices,
      opsAlerts,
      opsJobs,
      opsLogs,
      opsStuckJobs,
      opsLogItems,
      fetchOpsDashboard,
      opsOverallType,
      startOpsPolling,
      stopOpsPolling,
    };
  }

  window.AIMOAdminOps = { createOpsModule };
})(window);
