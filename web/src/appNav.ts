import { ref } from 'vue'

/** 与 App.vue 侧栏 tab 同步：0 对话 / 1 行情看板 / 2 存储 / 3 行情中心 / 4 策略分析 */
export const currentTab = ref(1)

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
