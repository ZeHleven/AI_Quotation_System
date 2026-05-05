(function (window) {
  'use strict';

  const TOKEN_KEY = 'ai_token';
  const USER_INFO_KEY = 'app_user_info';

  const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);
  const apiBody = (res) => (res && res.data) || {};
  const apiPayloadData = (body, fallback = null) =>
    hasOwn(body, 'data') ? body.data : (body || fallback);
  const apiData = (res, fallback = null) => apiPayloadData(apiBody(res), fallback);
  const apiMessage = (res, fallback = 'ok') => apiBody(res).message || fallback;
  const apiErrorMessage = (error, fallback = 'Request failed') => {
    const body = (error && error.response && error.response.data) || {};
    return body.detail || body.message || (error && error.message) || fallback;
  };

  const getToken = () => window.localStorage.getItem(TOKEN_KEY) || '';
  const setToken = (token) => {
    if (token) {
      window.localStorage.setItem(TOKEN_KEY, token);
    } else {
      window.localStorage.removeItem(TOKEN_KEY);
    }
  };

  const getUserInfo = () => {
    const raw = window.localStorage.getItem(USER_INFO_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      window.localStorage.removeItem(USER_INFO_KEY);
      return null;
    }
  };

  const setUserInfo = (userInfo) => {
    if (userInfo) {
      window.localStorage.setItem(USER_INFO_KEY, JSON.stringify(userInfo));
    } else {
      window.localStorage.removeItem(USER_INFO_KEY);
    }
  };

  const clearAuth = () => {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_INFO_KEY);
  };

  const authHeaders = (onMissing) => {
    const token = getToken();
    if (!token && typeof onMissing === 'function') onMissing();
    return { Authorization: `Bearer ${token}` };
  };

  const installAxiosUnauthorizedHandler = (axiosInstance, onUnauthorized) => {
    if (!axiosInstance || !axiosInstance.interceptors) return;
    axiosInstance.interceptors.response.use(
      response => response,
      error => {
        if (error && error.response && error.response.status === 401 && typeof onUnauthorized === 'function') {
          onUnauthorized();
        }
        return Promise.reject(error);
      }
    );
  };

  window.AIMO = {
    TOKEN_KEY,
    USER_INFO_KEY,
    hasOwn,
    apiBody,
    apiPayloadData,
    apiData,
    apiMessage,
    apiErrorMessage,
    getToken,
    setToken,
    getUserInfo,
    setUserInfo,
    clearAuth,
    authHeaders,
    installAxiosUnauthorizedHandler,
  };
})(window);
