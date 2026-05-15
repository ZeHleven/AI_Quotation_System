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

    const normalizeMaterialRow = (row = {}) => ({
      id: row.id || row.material_id || Date.now().toString(),
      item_name: row.item_name || '',
      unit_price: Number(row.unit_price || 0),
      unit: row.unit || '\u9879',
      notes: row.notes || '',
      category: row.category || '',
      spec: row.spec || '',
      brand: row.brand || '',
      supplier: row.supplier || '',
      region: row.region || '',
      source: row.source || 'manual',
      status: row.status || (row.is_draft ? 'draft' : 'active'),
      last_verified_at: row.last_verified_at || '',
      usage_count: Number(row.usage_count || 0),
      last_used_at: row.last_used_at || '',
      is_draft: row.status ? row.status === 'draft' : Boolean(row.is_draft),
    });

    const initMockData = () => [
      normalizeMaterialRow({
        id: '1',
        item_name: '\u76f4\u7ebf\u578b\u540a\u9876',
        unit_price: 120.00,
        unit: '\u5e73\u7c73',
        notes: '\u4f7f\u7528\u9f99\u724c\u8f7b\u94a2\u9f99\u9aa8\u6cf0\u5c71\u77f3\u818f\u677f\uff0cL\u578b\u6297\u88c2\u53ca\u63a5\u7f1d\u5904\u7406\u3002',
      }),
    ];

    const categoryRules = [
      { label: '吊顶', keywords: ['吊顶', '龙骨', '石膏板', '跌级'] },
      { label: '防水', keywords: ['防水', '闭水', '止水'] },
      { label: '拆改', keywords: ['拆除', '铲除', '砸墙', '清运', '开槽'] },
      { label: '瓦工铺贴', keywords: ['地砖', '墙砖', '瓷砖', '铺贴', '找平', '美缝', '踢脚线'] },
      { label: '墙面涂装', keywords: ['腻子', '乳胶漆', '墙面', '刷漆', '涂刷', '阴阳角'] },
      { label: '水电', keywords: ['水路', '电路', '强电', '弱电', '开关', '插座', '线管', 'PPR'] },
      { label: '木作地板', keywords: ['木地板', '地板', '木门', '柜体', '柜门', '木作'] },
      { label: '厨卫洁具', keywords: ['马桶', '花洒', '浴室', '洁具', '地漏', '洗手盆', '橱柜', '厨房'] },
      { label: '门窗安装', keywords: ['门套', '窗套', '窗台', '门窗', '断桥铝'] },
      { label: '灯具电器', keywords: ['灯具', '筒灯', '射灯', '浴霸', '排风', '热水器'] },
    ];

    const inferMaterialCategory = (row = {}) => {
      const text = `${row.item_name || ''} ${row.notes || ''} ${row.spec || ''}`;
      const match = categoryRules.find(rule => rule.keywords.some(keyword => text.includes(keyword)));
      return match ? match.label : '其他';
    };

    const materialCategoryPlaceholder = row => {
      if (String((row && row.category) || '').trim()) return '分类';
      return `建议：${inferMaterialCategory(row)}`;
    };

    const fillSuggestedCategories = () => {
      let count = 0;
      tableData.value.forEach(row => {
        if (!String(row.category || '').trim()) {
          row.category = inferMaterialCategory(row);
          count += 1;
        }
      });
      if (count > 0) {
        ElMessage.success(`已补全 ${count} 条分类，请确认后保存`);
      } else {
        ElMessage.info('没有需要补全的空分类');
      }
    };

    const materialStatusLabel = (status, isDraft) => {
      const value = status || (isDraft ? 'draft' : 'active');
      const map = { active: '\u751f\u6548', draft: '\u8349\u7a3f', archived: '\u505c\u7528' };
      return map[value] || value || '\u2014';
    };

    const materialStatusType = (status, isDraft) => {
      const value = status || (isDraft ? 'draft' : 'active');
      const map = { active: 'success', draft: 'warning', archived: 'info' };
      return map[value] || 'info';
    };

    const materialSourceLabel = (source) => {
      const map = {
        manual: '\u4eba\u5de5\u7ef4\u62a4',
        csv_import: 'CSV \u5bfc\u5165',
        knowledge_candidate: '\u77e5\u8bc6\u5019\u9009',
      };
      return map[source] || source || '\u2014';
    };

    const materialFieldLabel = (field) => {
      const map = {
        item_name: '\u540d\u79f0',
        unit_price: '\u5355\u4ef7',
        unit: '\u5355\u4f4d',
        notes: '\u5907\u6ce8',
        category: '\u5206\u7c7b',
        spec: '\u89c4\u683c',
        brand: '\u54c1\u724c',
        supplier: '\u4f9b\u5e94\u5546',
        region: '\u533a\u57df',
        source: '\u6765\u6e90',
        status: '\u72b6\u6001',
        is_draft: '\u8349\u7a3f',
      };
      return map[field] || field || '\u5b57\u6bb5';
    };

    const snapshotDiffItems = (row, key) => {
      const summary = row && row.diff_summary ? row.diff_summary : {};
      return Array.isArray(summary[key]) ? summary[key] : [];
    };

    const changedFieldLabels = (item) => {
      const changes = Array.isArray(item && item.changed_fields) ? item.changed_fields : [];
      if (!changes.length) return '\u5b57\u6bb5\u53d8\u66f4';
      return changes.map(change => materialFieldLabel(change.field)).join('\u3001');
    };

    const syncMaterialStatus = (row) => {
      row.is_draft = row.status === 'draft';
    };

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
        tableData.value = rows && rows.length > 0 ? rows.map(normalizeMaterialRow) : initMockData();
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
        ...normalizeMaterialRow({ id: Date.now().toString(), unit: '\u5e73\u7c73' }),
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
          tableData.value.push(...newDrafts.map(normalizeMaterialRow));
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
        } catch (error) {
          ElMessage.error(apiErrorMessage(error, '\u5411\u91cf\u5f15\u64ce\u540c\u6b65\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u5e95\u5c42 Milvus \u72b6\u6001\u3002'));
        } finally {
          syncing.value = false;
        }
      }).catch(() => {});
    };

    const tableRowClassName = ({ row }) => row.is_draft || row.status === 'draft' ? 'draft-row' : '';

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
      materialStatusLabel,
      materialStatusType,
      materialSourceLabel,
      materialFieldLabel,
      snapshotDiffItems,
      changedFieldLabels,
      syncMaterialStatus,
      materialCategoryPlaceholder,
      fillSuggestedCategories,
    };
  }

  window.AIMOAdminMaterials = { createMaterialsModule };
})(window);
