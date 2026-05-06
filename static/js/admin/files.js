(function (window) {
  'use strict';

  function createFilesModule(options) {
    const {
      ref,
      axios,
      ElMessage,
      coreApiBaseUrl,
      authHeaders,
      apiData,
      apiErrorMessage,
    } = options;

    const storageFiles = ref([]);
    const storageLoading = ref(false);
    const storageUploading = ref(false);
    const storageHealth = ref({});
    const storagePurpose = ref('quote_attachment');

    const fetchStorageHealth = async () => {
      try {
        const res = await axios.get(`${coreApiBaseUrl}/admin/files/storage/health`, { headers: authHeaders() });
        storageHealth.value = apiData(res, {}) || {};
      } catch (e) {
        storageHealth.value = { ok: false, status: 'error' };
        ElMessage.error(apiErrorMessage(e, 'Failed to check MinIO storage'));
      }
    };

    const fetchStorageFiles = async () => {
      storageLoading.value = true;
      try {
        const res = await axios.get(`${coreApiBaseUrl}/files`, {
          params: { page: 1, page_size: 20 },
          headers: authHeaders(),
        });
        storageFiles.value = apiData(res, []) || [];
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, 'Failed to load file list'));
      } finally {
        storageLoading.value = false;
      }
    };

    const handleStorageUpload = async (file) => {
      const formData = new FormData();
      formData.append('file', file.raw);
      formData.append('purpose', storagePurpose.value || 'general');
      storageUploading.value = true;
      try {
        const res = await axios.post(`${coreApiBaseUrl}/files`, formData, { headers: authHeaders() });
        const uploaded = apiData(res, {});
        ElMessage.success(`File uploaded: ${uploaded.original_filename || file.name || 'uploaded file'}`);
        await fetchStorageFiles();
        await fetchStorageHealth();
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, 'Failed to upload file; check MinIO configuration'));
      } finally {
        storageUploading.value = false;
      }
    };

    const openStorageDownload = async (row) => {
      try {
        const res = await axios.get(`${coreApiBaseUrl}/files/${row.file_id}/download_url`, { headers: authHeaders() });
        window.open(apiData(res, {}).download_url, '_blank');
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, 'Failed to generate temporary download URL'));
      }
    };

    return {
      storageFiles,
      storageLoading,
      storageUploading,
      storageHealth,
      storagePurpose,
      fetchStorageHealth,
      fetchStorageFiles,
      handleStorageUpload,
      openStorageDownload,
    };
  }

  window.AIMOAdminFiles = { createFilesModule };
})(window);
