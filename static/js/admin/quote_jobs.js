(function (window) {
  'use strict';

  function createQuoteJobsModule(options) {
    const {
      ref,
      axios,
      ElMessage,
      ElMessageBox,
      coreApiBaseUrl,
      authHeaders,
      apiBody,
      apiData,
      apiErrorMessage,
    } = options;

    const jobData = ref([]);
    const jobLoading = ref(false);
    const jobTotal = ref(0);
    const jobPage = ref(1);
    const jobStatusFilter = ref('queued,running');
    const jobUsernameFilter = ref('');
    const timeoutMinutes = ref(30);
    const markingTimeouts = ref(false);
    const showJobDetail = ref(false);
    const jobDetail = ref(null);
    const jobDetailLoading = ref(false);

    const statusTagType = (status) => {
      const map = {
        queued: 'info',
        running: 'warning',
        succeeded: 'success',
        failed: 'danger',
        canceled: 'info',
        timed_out: 'danger',
      };
      return map[status] || 'info';
    };

    const formatDuration = (durationMs) => {
      const ms = Number(durationMs);
      if (!Number.isFinite(ms) || ms <= 0) return '\u2014';
      if (ms < 1000) return `${Math.round(ms)} ms`;
      const seconds = Math.round(ms / 1000);
      if (seconds < 60) return `${seconds} s`;
      const minutes = Math.floor(seconds / 60);
      const restSeconds = seconds % 60;
      return `${minutes}m ${restSeconds}s`;
    };

    const displayJobNumber = (row) => {
      if (!row) return '\u2014';
      return row.job_number || row.quote_job_number || row.job_id || row.quote_job_id || '\u2014';
    };

    const fetchJobs = async (page = 1) => {
      jobPage.value = page;
      jobLoading.value = true;
      try {
        const params = { page, page_size: 10 };
        if (jobStatusFilter.value) params.status = jobStatusFilter.value;
        if (jobUsernameFilter.value) params.username = jobUsernameFilter.value;
        const res = await axios.get(`${coreApiBaseUrl}/quote/jobs`, { params, headers: authHeaders() });
        const body = apiBody(res);
        jobData.value = apiData(res, []) || [];
        jobTotal.value = body.total || 0;
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '获取任务队列失败'));
      } finally {
        jobLoading.value = false;
      }
    };

    const cancelJob = async (row) => {
      try {
        await ElMessageBox.confirm(`确定取消任务 ${displayJobNumber(row)} 吗？`, '取消任务', { type: 'warning' });
        await axios.post(`${coreApiBaseUrl}/quote/jobs/${row.job_id}/cancel`, {}, { headers: authHeaders() });
        ElMessage.success('任务已取消');
        fetchJobs(jobPage.value);
      } catch (e) {
        if (e !== 'cancel') ElMessage.error(apiErrorMessage(e, '取消任务失败'));
      }
    };

    const retryJob = async (row) => {
      try {
        await axios.post(`${coreApiBaseUrl}/quote/jobs/${row.job_id}/retry`, {}, { headers: authHeaders() });
        ElMessage.success('重试任务已创建');
        fetchJobs(1);
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '重试任务失败'));
      }
    };

    const markTimeouts = async () => {
      markingTimeouts.value = true;
      try {
        const res = await axios.post(`${coreApiBaseUrl}/admin/quote/jobs/mark_timeouts`, {}, {
          params: { timeout_minutes: timeoutMinutes.value },
          headers: authHeaders(),
        });
        ElMessage.success(`已标记 ${apiBody(res).marked_count || 0} 个超时任务`);
        fetchJobs(1);
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '标记超时任务失败'));
      } finally {
        markingTimeouts.value = false;
      }
    };

    const openJobDetail = async (row) => {
      jobDetail.value = null;
      showJobDetail.value = true;
      jobDetailLoading.value = true;
      try {
        const res = await axios.get(`${coreApiBaseUrl}/quote/jobs/${row.job_id}`, { headers: authHeaders() });
        jobDetail.value = apiData(res, apiBody(res));
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '获取任务详情失败'));
        showJobDetail.value = false;
      } finally {
        jobDetailLoading.value = false;
      }
    };

    const formatJson = (val) => {
      if (!val) return '（无数据）';
      try {
        return JSON.stringify(typeof val === 'string' ? JSON.parse(val) : val, null, 2);
      } catch {
        return String(val);
      }
    };

    return {
      jobData,
      jobLoading,
      jobTotal,
      jobPage,
      jobStatusFilter,
      jobUsernameFilter,
      timeoutMinutes,
      markingTimeouts,
      showJobDetail,
      jobDetail,
      jobDetailLoading,
      fetchJobs,
      cancelJob,
      retryJob,
      markTimeouts,
      openJobDetail,
      formatJson,
      formatDuration,
      displayJobNumber,
      statusTagType,
    };
  }

  window.AIMOAdminQuoteJobs = { createQuoteJobsModule };
})(window);
