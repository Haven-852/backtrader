import axios from 'axios'

const TOKEN_KEY = 'aiquant_auth_token'

export function getAuthToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setAuthToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

/** 防止后端短暂重启/并发 401 连发时反复踢登录、拆掉整棵工作台 */
let unauthorizedLockUntil = 0

function emitUnauthorizedOnce() {
  const now = Date.now()
  if (now < unauthorizedLockUntil) return
  unauthorizedLockUntil = now + 4000
  window.dispatchEvent(new CustomEvent('aiquant:unauthorized'))
}

axios.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

axios.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err?.response?.status
    const url = String(err?.config?.url || '')
    const skipAuthKick =
      url.includes('/auth/login') ||
      url.includes('/auth/register') ||
      // 启动探测类接口失败不应清会话；由页面自己降级展示
      url.includes('/chat/llm-profiles')
    if (status === 401 && !skipAuthKick && getAuthToken()) {
      setAuthToken(null)
      emitUnauthorizedOnce()
    }
    return Promise.reject(err)
  },
)

export default axios
