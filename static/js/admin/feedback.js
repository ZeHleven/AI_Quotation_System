(function (window) {
  'use strict';

  function createFeedbackModule(options) {
    const {
      ref,
      axios,
      ElMessage,
      apiBaseUrl,
      authHeaders,
      apiBody,
      apiData,
      apiErrorMessage,
    } = options;

    const feedbackSummary = ref({});
    const feedbackData = ref([]);
    const feedbackLoading = ref(false);
    const feedbackSummaryLoading = ref(false);
    const feedbackPage = ref(1);
    const feedbackTotal = ref(0);
    const feedbackDays = ref(7);
    const feedbackStatus = ref('');
    const feedbackUsername = ref('');
    const showFeedbackDetail = ref(false);
    const feedbackDetail = ref(null);
    const feedbackDetailLoading = ref(false);

    const feedbackStatusType = (status) => {
      const map = {
        confirmed: 'success',
        rejected: 'danger',
        pending_review: 'warning',
      };
      return map[status] || 'info';
    };

    const money = (value) => {
      const num = Number(value);
      if (!Number.isFinite(num)) return '0.00';
      return num.toFixed(2);
    };

    const percent = (value) => {
      const num = Number(value);
      if (!Number.isFinite(num)) return '0.0%';
      return `${(num * 100).toFixed(1)}%`;
    };

    const filterParams = () => {
      const params = { days: feedbackDays.value };
      if (feedbackStatus.value) params.status = feedbackStatus.value;
      if (feedbackUsername.value) params.username = feedbackUsername.value;
      return params;
    };

    const fetchFeedbackSummary = async () => {
      feedbackSummaryLoading.value = true;
      try {
        const res = await axios.get(`${apiBaseUrl}/quote_feedback/summary`, {
          params: filterParams(),
          headers: authHeaders(),
        });
        feedbackSummary.value = apiData(res, apiBody(res)) || {};
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '获取报价反馈汇总失败'));
      } finally {
        feedbackSummaryLoading.value = false;
      }
    };

    const fetchFeedbackList = async (page = 1) => {
      feedbackPage.value = page;
      feedbackLoading.value = true;
      try {
        const res = await axios.get(`${apiBaseUrl}/quote_feedback`, {
          params: { ...filterParams(), page, page_size: 10 },
          headers: authHeaders(),
        });
        const body = apiBody(res);
        feedbackData.value = apiData(res, []) || [];
        feedbackTotal.value = body.total || 0;
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '获取报价反馈列表失败'));
      } finally {
        feedbackLoading.value = false;
      }
    };

    const refreshFeedback = async () => {
      await Promise.all([fetchFeedbackSummary(), fetchFeedbackList(1)]);
    };

    const openFeedbackDetail = async (row) => {
      feedbackDetail.value = null;
      showFeedbackDetail.value = true;
      feedbackDetailLoading.value = true;
      try {
        const res = await axios.get(`${apiBaseUrl}/quote_feedback/${row.id}`, {
          headers: authHeaders(),
        });
        feedbackDetail.value = apiData(res, apiBody(res));
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '获取报价反馈详情失败'));
        showFeedbackDetail.value = false;
      } finally {
        feedbackDetailLoading.value = false;
      }
    };

    return {
      feedbackSummary,
      feedbackData,
      feedbackLoading,
      feedbackSummaryLoading,
      feedbackPage,
      feedbackTotal,
      feedbackDays,
      feedbackStatus,
      feedbackUsername,
      showFeedbackDetail,
      feedbackDetail,
      feedbackDetailLoading,
      fetchFeedbackSummary,
      fetchFeedbackList,
      refreshFeedback,
      openFeedbackDetail,
      feedbackStatusType,
      money,
      percent,
    };
  }

  window.AIMOAdminFeedback = { createFeedbackModule };
})(window);
