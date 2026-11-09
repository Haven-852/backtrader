import { ref, watch } from 'vue'

const TAB_KEY = 'aiquant_current_tab'
const TAB_MIN = 0
const TAB_MAX = 6

function readStoredTab(): number {
  try {
    const raw = localStorage.getItem(TAB_KEY)
    const n = raw == null ? NaN : Number(raw)
    if (Number.isInteger(n) && n >= TAB_MIN && n <= TAB_MAX) return n
  } catch {
    /* ignore */
  }
  return 1
}

/** 与 App.vue 侧栏 tab 同步：0 对话 / 1 行情看板 / 2 存储 / 3 行情中心 / 4 策略分析 / 5 模拟交易 / 6 知识库 */
export const currentTab = ref(readStoredTab())

watch(
  currentTab,
  (tab) => {
    try {
      localStorage.setItem(TAB_KEY, String(tab))
    } catch {
      /* ignore */
    }
  },
  { flush: 'sync' },
)

export type DashFocus = {
  ts_code: string
  name?: string
  industry?: string
  from?: 'screener' | string
  nonce: number
}

export const dashFocus = ref<DashFocus | null>(null)
export const dashFromScreener = ref(false)

export function openStockInDash(hit: { ts_code: string; name?: string; industry?: string }, from: string = 'screener') {
  dashFocus.value = {
    ts_code: hit.ts_code,
    name: hit.name,
    industry: hit.industry,
    from,
    nonce: Date.now(),
  }
  dashFromScreener.value = from === 'screener'
  currentTab.value = 1
}

export function returnToScreener() {
  currentTab.value = 4
}
