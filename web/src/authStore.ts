import { computed, ref } from 'vue'
import axios, { setAuthToken, getAuthToken } from './api'

export type AuthUser = {
  id: string
  email: string
  display_name: string
  role: string
  status: string
}

export type AuthSubscription = {
  plan: string
  status: string
  expire_at?: string | null
  is_pro?: boolean
}

export type AuthQuota = {
  date: string
  used: { chat: number; tool: number; export: number }
  limits: { chat: number; tool: number; export: number }
}

const user = ref<AuthUser | null>(null)
const subscription = ref<AuthSubscription | null>(null)
const quota = ref<AuthQuota | null>(null)
const bootstrapped = ref(false)
const busy = ref(false)
const error = ref('')

export const isLoggedIn = computed(() => Boolean(user.value?.id && getAuthToken()))

function applyAuthPayload(data: any) {
  user.value = data?.user || null
  subscription.value = data?.subscription || null
  quota.value = data?.quota || null
  if (data?.token) setAuthToken(data.token)
}

/** 仅用于网络抖动时的乐观登录态（不校验签名） */
function readJwtPayload(token: string): Record<string, any> | null {
  try {
    const part = token.split('.')[1]
    if (!part) return null
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(b64)
    return JSON.parse(json)
  } catch {
    return null
  }
}

function applyProvisionalFromToken(token: string) {
  const payload = readJwtPayload(token)
  if (!payload?.sub) return false
  user.value = {
    id: String(payload.sub),
    email: String(payload.email || ''),
    display_name: String(payload.email || '已登录'),
    role: String(payload.role || 'user'),
    status: 'active',
  }
  return true
}

export async function bootstrapAuth() {
  bootstrapped.value = false
  error.value = ''
  const token = getAuthToken()
  if (!token) {
    user.value = null
    bootstrapped.value = true
    return
  }
  try {
    const res = await axios.get('/auth/me')
    applyAuthPayload(res.data)
  } catch (e: any) {
    const status = e?.response?.status
    // 仅凭证失效时清登录；后端短暂重启/网络抖动保留 token，避免挂机采集后被踢回落地页
    if (status === 401 || status === 403) {
      setAuthToken(null)
      user.value = null
      subscription.value = null
      quota.value = null
    } else if (applyProvisionalFromToken(token)) {
      error.value = '暂时连不上认证服务，已保留本地登录态'
      console.warn('bootstrapAuth 非鉴权失败，保留 token', e)
    } else {
      setAuthToken(null)
      user.value = null
    }
  } finally {
    bootstrapped.value = true
  }
}

export async function login(email: string, password: string) {
  busy.value = true
  error.value = ''
  try {
    const res = await axios.post('/auth/login', { email, password })
    applyAuthPayload(res.data)
    return res.data
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '登录失败'
    throw e
  } finally {
    busy.value = false
  }
}

export async function register(payload: {
  email: string
  password: string
  display_name?: string
  invite_code?: string
}) {
  busy.value = true
  error.value = ''
  try {
    const res = await axios.post('/auth/register', payload)
    applyAuthPayload(res.data)
    return res.data
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '注册失败'
    throw e
  } finally {
    busy.value = false
  }
}

export function logout() {
  setAuthToken(null)
  user.value = null
  subscription.value = null
  quota.value = null
}

export function useAuth() {
  return {
    user,
    subscription,
    quota,
    bootstrapped,
    busy,
    error,
    isLoggedIn,
    bootstrapAuth,
    login,
    register,
    logout,
  }
}
