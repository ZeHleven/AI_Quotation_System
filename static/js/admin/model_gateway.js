(function (window) {
  'use strict';

  function createModelGatewayModule(options) {
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

    const gatewayStats = ref([]);
    const gatewayCircuits = ref({});
    const gatewayLoading = ref(false);
    const gatewayHours = ref(24);

    const fetchGatewayStats = async () => {
      gatewayLoading.value = true;
      try {
        const res = await axios.get(`${apiBaseUrl}/model_gateway/stats`, {
          params: { hours: gatewayHours.value },
          headers: authHeaders(),
        });
        const body = apiBody(res);
        gatewayStats.value = apiData(res, []) || [];
        gatewayCircuits.value = body.circuit_breakers || {};
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, 'Failed to load model gateway stats'));
      } finally {
        gatewayLoading.value = false;
      }
    };

    return {
      gatewayStats,
      gatewayCircuits,
      gatewayLoading,
      gatewayHours,
      fetchGatewayStats,
    };
  }

  window.AIMOAdminModelGateway = { createModelGatewayModule };
})(window);
