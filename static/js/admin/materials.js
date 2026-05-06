(function (window) {
  'use strict';

  function createMaterialsModule(options) {
    const {
      ref,
      axios,
      ElMessage,
      ElMessageBox,
      apiBaseUrl,
      authHeaders,
      apiBody,
      apiData,
      apiMessage,
      apiErrorMessage,
      localStorageKey = 'last_milvus_sync',
      pollIntervalMs = 3000,
    } = options;

    const tableData = ref([]);
    const loading = ref(false);
    const saving = ref(false);
    const syncing = ref(false);
    const lastSyncTime = ref(window.localStorage.getItem(localStorageKey) || '\u672a\u540c\u6b65');
    const materialAudit = ref([]);
    const auditLoading = ref(false);
    const ragEvalResult = ref(null);
    const ragEvalPolling = ref(null);

    const initMockData = () => [
      {
        id: '1',
        item_name: '\u76f4\u7ebf\u578b\u540a\u9876',
        unit_price: 120.00,
        unit: '\u5e73\u7c73',
        notes: '\u4f7f\u7528\u9f99\u724c\u8f7b\u94a2\u9f99\u9aa8\u6cf0\u5c71\u77f3\u818f\u677f\uff0cL\u578b\u6297\u88c2\u53ca\u63a5\u7f1d\u5904\u7406\u3002',
      },
    ];

    const fetchRagEvalResult = async () => {
      try {
        const res = await axios.get(`${apiBaseUrl}/rag_eval/latest`, { headers: authHeaders() });
        ragEvalResult.value = apiData(res, null);
      } catch (e) {
        // Keep the admin page usable when the optional eval report is unavailable.
      }
    };

    const stopRagEvalPolling = () => {
      if (!ragEvalPolling.value) return;
      clearInterval(ragEvalPolling.value);
      ragEvalPolling.value = null;
    };

    const startRagEvalPolling = () => {
      stopRagEvalPolling();
      ragEvalPolling.value = setInterval(async () => {
        await fetchRagEvalResult();
        if (ragEvalResult.value && ragEvalResult.value.status !== 'running') {
          stopRagEvalPolling();
        }
      }, pollIntervalMs);
    };

    const fetchMaterialAudit = async () => {
      auditLoading.value = true;
      try {
        const res = await axios.get(`${apiBaseUrl}/materials/audit`, {
          params: { limit: 20 },
          headers: authHeaders(),
        });
        materialAudit.value = apiData(res, []) || [];
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '\u83b7\u53d6\u77e5\u8bc6\u5e93\u5feb\u7167\u5931\u8d25'));
      } finally {
        auditLoading.value = false;
      }
    };

    const fetchData = async () => {
      loading.value = true;
      try {
        const res = await axios.get(`${apiBaseUrl}/materials`, { headers: authHeaders() });
        const rows = apiData(res, []);
        tableData.value = rows && rows.length > 0 ? rows : initMockData();
      } catch (error) {
        ElMessage.warning('\u672a\u80fd\u8fde\u63a5\u5230\u540e\u53f0\uff0c\u6b63\u4f7f\u7528\u672c\u5730\u6a21\u62df\u6570\u636e\u8fd0\u884c\u3002');
        tableData.value = initMockData();
      } finally {
        loading.value = false;
      }
    };

    const rollbackMaterialSnapshot = async (row) => {
      try {
        await ElMessageBox.confirm(
          `\u786e\u5b9a\u56de\u6eda\u5230 ${row.created_at} \u7684\u77e5\u8bc6\u5e93\u5feb\u7167\u5417\uff1f\u5f53\u524d\u7248\u672c\u4f1a\u5148\u81ea\u52a8\u5907\u4efd\u3002`,
          '\u56de\u6eda\u77e5\u8bc6\u5e93\u5feb\u7167',
          { type: 'warning' },
        );
        await axios.post(`${apiBaseUrl}/materials/rollback/${row.snapshot_id}`, {}, { headers: authHeaders() });
        ElMessage.success('\u77e5\u8bc6\u5e93\u5df2\u56de\u6eda\uff0c\u8bf7\u786e\u8ba4\u540e\u518d\u540c\u6b65\u5230 Milvus');
        await fetchData();
        await fetchMaterialAudit();
      } catch (e) {
        if (e !== 'cancel') ElMessage.error(apiErrorMessage(e, '\u56de\u6eda\u77e5\u8bc6\u5e93\u5feb\u7167\u5931\u8d25'));
      }
    };

    const handleAdd = () => {
      tableData.value.push({
        id: Date.now().toString(),
        item_name: '',
        unit_price: 0.00,
        unit: '\u5e73\u7c73',
        notes: '',
        is_draft: false,
      });
    };

    const handleDelete = (index) => {
      ElMessageBox.confirm('\u786e\u5b9a\u8981\u5220\u9664\u8fd9\u9879\u57fa\u7840\u7269\u6599\u5417\uff1f', '\u8b66\u544a', { type: 'warning' })
        .then(() => {
          tableData.value.splice(index, 1);
          ElMessage.success('\u6761\u76ee\u5df2\u79fb\u9664\uff0c\u8bf7\u8bb0\u5f97\u4fdd\u5b58');
        })
        .catch(() => {});
    };

    const handleImportCSV = async (file) => {
      const formData = new FormData();
      formData.append('file', file.raw);
      loading.value = true;
      try {
        const res = await axios.post(`${apiBaseUrl}/upload_csv`, formData, { headers: authHeaders() });
        const newDrafts = apiData(res, []);
        if (newDrafts && newDrafts.length > 0) {
          tableData.value.push(...newDrafts);
          ElMessage.success(`\u89e3\u6790\u6210\u529f\uff01\u667a\u80fd\u63d0\u70bc\u51fa ${newDrafts.length} \u6761\u5f85\u5ba1\u5f02\u5e38/\u65b0\u9879\u76ee\uff0c\u5df2\u8ffd\u52a0\u81f3\u672b\u5c3e\u3002`);
          setTimeout(() => {
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
          }, 500);
        } else {
          ElMessage.info('\u8fc7\u6ee4\u5b8c\u6bd5\u3002\u5bfc\u5165\u7684\u6587\u4ef6\u4e0e\u73b0\u6709\u6807\u51c6\u5e93\u9ad8\u5ea6\u4e00\u81f4\uff0c\u65e0\u9700\u65b0\u589e\u5904\u7406\u9879\u3002');
        }
      } catch (error) {
        ElMessage.error(apiErrorMessage(error, 'CSV \u89e3\u6790\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u6587\u4ef6\u683c\u5f0f\u3002'));
      } finally {
        loading.value = false;
      }
    };

    const saveData = async () => {
      saving.value = true;
      try {
        await axios.post(`${apiBaseUrl}/materials`, tableData.value, { headers: authHeaders() });
        tableData.value.forEach(item => { item.is_draft = false; });
        fetchMaterialAudit();
        ElMessage.success('\ud83c\udf89 \u8868\u683c\u6570\u636e\u5ba1\u6838\u5b8c\u6bd5\uff0c\u5df2\u5b89\u5168\u4fdd\u5b58\u5230\u672c\u5730\u57fa\u7840\u5e93\uff01');
      } catch (error) {
        ElMessage.error(apiErrorMessage(error, '\u4fdd\u5b58\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u5173\u8fde\u63a5\u3002'));
      } finally {
        saving.value = false;
      }
    };

    const syncToMilvus = async () => {
      ElMessageBox.confirm(
        '\u6b64\u64cd\u4f5c\u4f1a\u5c06\u4e0a\u65b9\u8868\u683c\u7684\u6570\u636e\u5b8c\u5168\u8986\u76d6\u704c\u5165 Milvus \u5411\u91cf\u5f15\u64ce\uff0c\u76f4\u63a5\u6210\u4e3a AI \u62a5\u4ef7\u7b97\u529b\u7684\u552f\u4e00\u6807\u51c6\uff0c\u662f\u5426\u7ee7\u7eed\uff1f',
        '\ud83d\ude80 \u53d1\u5e03\u6307\u4ee4',
        { confirmButtonText: '\u26a1 \u786e\u8ba4\u53d1\u5e03', cancelButtonText: '\u53d6\u6d88', type: 'warning' },
      ).then(async () => {
        syncing.value = true;
        try {
          await axios.post(`${apiBaseUrl}/materials`, tableData.value, { headers: authHeaders() });
          fetchMaterialAudit();
          const res = await axios.post(`${apiBaseUrl}/sync_milvus`, {}, { headers: authHeaders() });
          const body = apiBody(res);
          ElMessage({ message: apiMessage(res, '\u70ed\u66f4\u65b0\u5b8c\u6bd5\uff0c\u5411\u91cf\u5f15\u64ce\u5df2\u6ee1\u8840\u590d\u6d3b\uff01'), type: 'success', duration: 4000 });
          if (body.eval_triggered) {
            ragEvalResult.value = { status: 'running' };
            startRagEvalPolling();
          }
          const now = new Date().toLocaleString();
          lastSyncTime.value = now;
          window.localStorage.setItem(localStorageKey, now);
          tableData.value.forEach(item => { item.is_draft = false; });
        } catch (error) {
          ElMessage.error(apiErrorMessage(error, '\u5411\u91cf\u5f15\u64ce\u540c\u6b65\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u5e95\u5c42 Milvus \u72b6\u6001\u3002'));
        } finally {
          syncing.value = false;
        }
      }).catch(() => {});
    };

    const tableRowClassName = ({ row }) => row.is_draft ? 'draft-row' : '';

    return {
      tableData,
      loading,
      saving,
      syncing,
      lastSyncTime,
      materialAudit,
      auditLoading,
      ragEvalResult,
      fetchRagEvalResult,
      startRagEvalPolling,
      stopRagEvalPolling,
      fetchMaterialAudit,
      rollbackMaterialSnapshot,
      fetchData,
      handleAdd,
      handleDelete,
      handleImportCSV,
      saveData,
      syncToMilvus,
      tableRowClassName,
    };
  }

  window.AIMOAdminMaterials = { createMaterialsModule };
})(window);
