export const TOKEN_KEY = 'ai_token'
export const USER_INFO_KEY = 'app_user_info'

function storage() {
  return window.sessionStorage
}

export function cleanupSharedAuthStorage() {
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(USER_INFO_KEY)
}

export function getToken() {
  return storage().getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  cleanupSharedAuthStorage()
  if (token) storage().setItem(TOKEN_KEY, token)
  else storage().removeItem(TOKEN_KEY)
}

export function getUserInfo() {
  const raw = storage().getItem(USER_INFO_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    storage().removeItem(USER_INFO_KEY)
    return null
  }
}

export function setUserInfo(userInfo) {
  cleanupSharedAuthStorage()
  if (userInfo) storage().setItem(USER_INFO_KEY, JSON.stringify(userInfo))
  else storage().removeItem(USER_INFO_KEY)
}

export function clearAuth() {
  storage().removeItem(TOKEN_KEY)
  storage().removeItem(USER_INFO_KEY)
  cleanupSharedAuthStorage()
}
