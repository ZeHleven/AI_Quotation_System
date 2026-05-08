(function (window) {
  'use strict';

  function createKnowledgeCandidatesModule(options) {
    const {
      ref,
      axios,
      ElMessage,
      ElMessageBox,
      apiBaseUrl,
      authHeaders,
      apiData,
      apiErrorMessage,
    } = options;

    const candidateList    = ref([]);
    const candidateLoading = ref(false);
    const candidateTotal   = ref(0);
    const candidatePage    = ref(1);
    const candidateStatusFilter = ref('pending');
    const candidateKindFilter   = ref('');
    const building         = ref(false);

    const showApproveDialog  = ref(false);
    const showRejectDialog   = ref(false);
    const showCandidateDetail = ref(false);
    const selectedCandidate  = ref(null);
    const approveResult      = ref(null);  // null = not yet submitted; object = response
    const approving          = ref(false);
    const rejecting          = ref(false);

    const approveForm = ref({ review_note: '', as_draft: true, item_name: '', unit_price: null, unit: '', notes: '' });
    const rejectForm  = ref({ review_note: '' });

    // ── helpers ──────────────────────────────────────────────────────────────

    const kindLabel = (kind) => {
      const map = { price_update: '价格更新', new_material: '新增物料', missing_knowledge: '缺失知识' };
      return map[kind] || kind || '—';
    };

    const kindTagType = (kind) => {
      const map = { price_update: 'warning', new_material: 'success', missing_knowledge: 'danger' };
      return map[kind] || 'info';
    };

    const candidateStatusType = (status) => {
      const map = { pending: 'warning', approved: 'success', rejected: 'danger' };
      return map[status] || 'info';
    };

    // ── fetch list ────────────────────────────────────────────────────────────

    const fetchCandidates = async (page) => {
      if (page) candidatePage.value = page;
      candidateLoading.value = true;
      try {
        const params = { page: candidatePage.value, page_size: 15 };
        if (candidateStatusFilter.value) params.status = candidateStatusFilter.value;
        if (candidateKindFilter.value)   params.candidate_kind = candidateKindFilter.value;
        const res = await axios.get(`${apiBaseUrl}/knowledge_candidates`, { params, headers: authHeaders() });
        const body = res.data || {};
        candidateList.value  = body.data  || [];
        candidateTotal.value = body.total || 0;
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '获取知识候选失败'));
      } finally {
        candidateLoading.value = false;
      }
    };

    // ── build ─────────────────────────────────────────────────────────────────

    const buildCandidates = async () => {
      building.value = true;
      try {
        const res = await axios.post(`${apiBaseUrl}/knowledge_candidates/build`,
          { limit: 200, include_rejected: false, overwrite: false },
          { headers: authHeaders() });
        const data = apiData(res, {});
        ElMessage.success(`已生成 ${data.created ?? 0} 个候选（跳过 ${data.skipped ?? 0}）`);
        fetchCandidates(1);
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '生成候选失败'));
      } finally {
        building.value = false;
      }
    };

    // ── approve ───────────────────────────────────────────────────────────────

    const openApproveDialog = (row) => {
      selectedCandidate.value = row;
      approveForm.value = {
        review_note: '',
        as_draft: true,
        item_name:  row.item_name  || '',
        unit_price: row.unit_price != null ? row.unit_price : null,
        unit:       row.unit       || '',
        notes:      row.notes      || '',
      };
      approveResult.value = null;
      showApproveDialog.value = true;
    };

    const submitApprove = async () => {
      if (!selectedCandidate.value) return;
      approving.value = true;
      try {
        const payload = { as_draft: approveForm.value.as_draft };
        if (approveForm.value.review_note) payload.review_note = approveForm.value.review_note;
        // only include override fields if non-empty / non-null
        if (approveForm.value.item_name  !== (selectedCandidate.value.item_name  || '')) payload.item_name  = approveForm.value.item_name;
        if (approveForm.value.unit       !== (selectedCandidate.value.unit       || '')) payload.unit       = approveForm.value.unit;
        if (approveForm.value.notes      !== (selectedCandidate.value.notes      || '')) payload.notes      = approveForm.value.notes;
        if (approveForm.value.unit_price !== (selectedCandidate.value.unit_price ?? null)) payload.unit_price = approveForm.value.unit_price;

        const res = await axios.post(
          `${apiBaseUrl}/knowledge_candidates/${selectedCandidate.value.id}/approve`,
          payload,
          { headers: authHeaders() }
        );
        approveResult.value = apiData(res, {});
        fetchCandidates(candidatePage.value);
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '审批失败'));
      } finally {
        approving.value = false;
      }
    };

    // ── reject ────────────────────────────────────────────────────────────────

    const openRejectDialog = (row) => {
      selectedCandidate.value = row;
      rejectForm.value = { review_note: '' };
      showRejectDialog.value = true;
    };

    const submitReject = async () => {
      if (!selectedCandidate.value) return;
      rejecting.value = true;
      try {
        await axios.post(
          `${apiBaseUrl}/knowledge_candidates/${selectedCandidate.value.id}/reject`,
          { review_note: rejectForm.value.review_note || '' },
          { headers: authHeaders() }
        );
        ElMessage.success('已拒绝该候选');
        showRejectDialog.value = false;
        fetchCandidates(candidatePage.value);
      } catch (e) {
        ElMessage.error(apiErrorMessage(e, '拒绝失败'));
      } finally {
        rejecting.value = false;
      }
    };

    // ── detail ────────────────────────────────────────────────────────────────

    const openCandidateDetail = (row) => {
      selectedCandidate.value = row;
      showCandidateDetail.value = true;
    };

    return {
      candidateList, candidateLoading, candidateTotal, candidatePage,
      candidateStatusFilter, candidateKindFilter,
      building,
      showApproveDialog, showRejectDialog, showCandidateDetail,
      selectedCandidate, approveResult, approving, rejecting,
      approveForm, rejectForm,
      kindLabel, kindTagType, candidateStatusType,
      fetchCandidates, buildCandidates,
      openApproveDialog, submitApprove,
      openRejectDialog, submitReject,
      openCandidateDetail,
    };
  }

  window.AIMOAdminKnowledgeCandidates = { createKnowledgeCandidatesModule };
})(window);
