<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import axios from 'axios'

defineOptions({ name: 'PaperTradeView' })

interface Account {
  id: number
  name: string
  cash: number
  equity: number
  initial_cash: number
  lookback_guard: boolean
  status?: string
}

interface Position {
  ts_code: string
  name?: string
  quantity: number
  sellable_qty?: number
  frozen_qty?: number
  avg_cost: number
  market_price?: number | null
  market_value?: number | null
  unrealized_pnl?: number | null
  cost_amount?: number
  pnl_pct?: number | null
  weight?: number | null
  pnl_ready?: boolean
  quote_source?: string
  quote_clock?: string
  quote_time?: string
  quote_trade_date?: string
  quote_error?: string
}

interface JournalEvent {
  id: number
  event_type: string
  trigger_source?: string
  trigger_label?: string
  ts_code?: string
  stock_name?: string
  title?: string
  detail?: string
  observation?: Record<string, any>
  llm_woken?: boolean
  llm_wake_index?: number
  llm_model?: string
  action?: string
  quantity?: number
  price?: number
  created_at?: string
}

interface Message {
  role: string
  content: string
  time?: Date | string
  toolTrace?: any[]
}

const accounts = ref<Account[]>([])
const accountId = ref<number | null>(null)
const account = ref<Account | null>(null)
const positions = ref<Position[]>([])
const stats = ref<Record<string, any> | null>(null)
const orders = ref<any[]>([])
const fills = ref<any[]>([])
const decisions = ref<any[]>([])
const loadingPanel = ref(false)
const journalDate = ref(todayISO())
const journalEvents = ref<JournalEvent[]>([])
const journalSummary = ref('')
const journalWakeCount = ref(0)
const journalFillCount = ref(0)
const journalTriggers = ref<Record<string, number>>({})

const messages = ref<Message[]>([])
const inputMessage = ref('')
const loading = ref(false)
const selectedModel = ref('deepseek-chat')
const sessionId = ref<string | null>(null)
const chatBox = ref<HTMLElement | null>(null)

const createName = ref('交易Agent账户')
const createCash = ref(1_000_000)
const creating = ref(false)
const cycleBusy = ref(false)
const cycleUseLlm = ref(false)
const cycleActions = ref<any[]>([])
const cycleMsg = ref('')
const schedulerOn = ref(false)
const schedulerStatus = ref('')
const markComplete = ref(true)
const markNote = ref('')
const sessionPhase = ref('')

const alertBusy = ref(false)
const alertMsg = ref('')
const alertSettings = ref<any>({})
const alertChannels = ref<any[]>([])
const alertEvents = ref<any[]>([])
const alertAutoOn = ref(false)
const alertStopPct = ref(-5)
const alertTakePct = ref(8)
const alertPullPct = ref(-3)
const alertDropPct = ref(-4)
const alertRisePct = ref(6)
const alertCooldown = ref(30)
const alertAutoSell = ref(false)

const monBusy = ref(false)
const monMsg = ref('')
const monPool = ref<any[]>([])
const monReports = ref<any[]>([])
const monAutoOn = ref(false)
const monUseLlm = ref(true)
const monCode = ref('')
const monCost = ref<number | null>(null)
const monNote = ref('')
const monDropPct = ref(-4)
const monRisePct = ref(6)
const monPullPct = ref(-3)
const monStopPct = ref(-5)
const monTakePct = ref(8)
const monCooldown = ref(45)
const monPrompt = ref('')
const monReportPreview = ref('')

const quickModels = [
  { id: 'deepseek-chat', name: 'DeepSeek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek R1' },
  { id: 'gpt-4o-mini', name: 'GPT-4o Mini' },
]

const currentAccountLabel = computed(() => {
  if (!account.value) return '未选择账户'
  return `#${account.value.id} ${account.value.name}`
})

function todayISO() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function fmtMoney(v: any) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function fmtPct(v: any) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(2)}%`
}

function formatContent(text: string): string {
  if (!text) return ''
  return String(text)
    .replace(/\*\*(.*?)\*\*/g, '<strong class="text-emerald-400">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="bg-zinc-700 px-1 py-0.5 rounded text-xs text-emerald-300">$1</code>')
    .replace(/\n/g, '<br>')
}

function pnlClass(v: any) {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return 'text-zinc-400'
  return n > 0 ? 'text-emerald-400' : 'text-red-400'
}

function fmtSigned(v: any) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '-'
  const s = n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return n > 0 ? `+${s}` : s
}

function formatClock(ts?: string) {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return String(ts).slice(11, 19) || String(ts)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function obsLine(e: JournalEvent) {
  const o = e.observation || {}
  const bits: string[] = []
  if (o.clock) bits.push(`时间 ${o.clock}`)
  if (o.price != null && o.price !== '') bits.push(`现价 ${o.price}`)
  if (o.bias) bits.push(`初判 ${o.bias}`)
  if (o.session_high != null) bits.push(`截至高 ${o.session_high}`)
  if (o.session_low != null) bits.push(`截至低 ${o.session_low}`)
  if (o.ma5 != null) bits.push(`MA5 ${o.ma5}`)
  if (o.algorithm) bits.push(`算法 ${o.algorithm}`)
  if (o.user_query) bits.push(`指令 ${o.user_query}`)
  if (o.tool_rounds) bits.push(`工具 ${o.tool_rounds} 轮`)
  return bits.join(' · ')
}

function formatTime(date?: Date | string) {
  if (!date) return ''
  const d = date instanceof Date ? date : new Date(date)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function loadAccounts() {
  const res = await axios.get('/paper/accounts', { params: { status: 'active' } })
  accounts.value = res.data.accounts || []
  if (!accountId.value && accounts.value.length) {
    accountId.value = accounts.value[0].id
  }
}

async function refreshAccountPanel() {
  if (!accountId.value) {
    account.value = null
    positions.value = []
    stats.value = null
    orders.value = []
    fills.value = []
    decisions.value = []
    journalEvents.value = []
    journalSummary.value = ''
    journalWakeCount.value = 0
    journalFillCount.value = 0
    journalTriggers.value = {}
    return
  }
  loadingPanel.value = true
  try {
    const [a, o, f, d, s, j] = await Promise.all([
      axios.get(`/paper/accounts/${accountId.value}`),
      axios.get(`/paper/accounts/${accountId.value}/orders`, { params: { limit: 30 } }),
      axios.get(`/paper/accounts/${accountId.value}/fills`, { params: { limit: 30 } }),
      axios.get(`/paper/accounts/${accountId.value}/decisions`, { params: { limit: 30 } }),
      axios.get(`/paper/accounts/${accountId.value}/stats`),
      axios.get(`/paper/accounts/${accountId.value}/journal`, { params: { trade_date: journalDate.value } }),
    ])
    account.value = a.data.account
    positions.value = j.data.positions?.length ? j.data.positions : (a.data.positions || [])
    markComplete.value = a.data.intraday_complete !== false
    markNote.value = a.data.note || ''
    sessionPhase.value = a.data.session_phase || ''
    orders.value = o.data.orders || []
    fills.value = f.data.fills || []
    decisions.value = d.data.decisions || []
    stats.value = s.data.stats || null
    journalEvents.value = j.data.events || []
    journalSummary.value = j.data.summary || ''
    journalWakeCount.value = j.data.llm_wake_count || 0
    journalFillCount.value = j.data.fill_count || 0
    journalTriggers.value = j.data.trigger_counts || {}
  } catch (e: any) {
    console.warn('刷新账户失败', e)
  } finally {
    loadingPanel.value = false
  }
}

async function createAccount() {
  creating.value = true
  try {
    const res = await axios.post('/paper/accounts', {
      name: createName.value || '交易Agent账户',
      initial_cash: createCash.value,
      lookback_guard: true,
    })
    if (res.data.ok) {
      accountId.value = res.data.account.id
      await loadAccounts()
      await refreshAccountPanel()
      pushSystem(`已开户 #${res.data.account.id}，初始资金 ${fmtMoney(res.data.account.initial_cash)}`)
    }
  } catch (e: any) {
    pushSystem(`开户失败: ${e?.response?.data?.detail || e.message}`)
  } finally {
    creating.value = false
  }
}

async function deleteAccount(id: number) {
  if (!id) return
  if (!confirm(`确认删除账户 #${id}？将同时清除持仓、委托、成交与决策日志。`)) return
  try {
    const res = await axios.delete(`/paper/accounts/${id}`, { params: { hard: true } })
    if (res.data.ok) {
      pushSystem(res.data.message || `已删除账户 #${id}`)
      if (accountId.value === id) {
        accountId.value = null
        sessionId.value = null
      }
      await loadAccounts()
      if (!accountId.value && accounts.value.length) {
        accountId.value = accounts.value[0].id
      }
      await refreshAccountPanel()
    }
  } catch (e: any) {
    pushSystem(`删除失败: ${e?.response?.data?.detail || e.message}`)
  }
}

async function refreshScheduler() {
  try {
    const res = await axios.get('/paper/cycle/scheduler')
    schedulerOn.value = !!res.data.running
    schedulerStatus.value = res.data.running
      ? `运行中 · 账户#${res.data.account_id || '-'} · 晚${res.data.evening} 早${res.data.open}${res.data.use_llm ? ' · LLM' : ''}`
      : '定时器未开启'
  } catch {
    schedulerStatus.value = ''
  }
}

function pctToRatio(v: number) {
  return Number(v) / 100
}

function ratioToPct(v: any, fallback: number) {
  const n = Number(v)
  if (!Number.isFinite(n)) return fallback
  return Math.round(n * 1000) / 10
}

async function loadAlertSettings() {
  try {
    const res = await axios.get('/paper/alerts/settings')
    const s = res.data.settings || {}
    alertSettings.value = s
    alertChannels.value = s.channels_ready || []
    alertAutoOn.value = !!s.alert_auto_enabled
    alertStopPct.value = ratioToPct(s.alert_stop_loss_pct, -5)
    alertTakePct.value = ratioToPct(s.alert_take_profit_pct, 8)
    alertPullPct.value = ratioToPct(s.alert_peak_pullback_pct, -3)
    alertDropPct.value = ratioToPct(s.alert_daily_drop_pct, -4)
    alertRisePct.value = ratioToPct(s.alert_daily_rise_pct, 6)
    alertCooldown.value = Number(s.alert_cooldown_minutes || 30)
    alertAutoSell.value = !!s.alert_auto_sell_stop
  } catch {
    alertSettings.value = {}
  }
}

async function loadAlertEvents() {
  try {
    const res = await axios.get('/paper/alerts/events', {
      params: { account_id: accountId.value || undefined, limit: 30 },
    })
    alertEvents.value = res.data.events || []
  } catch {
    alertEvents.value = []
  }
}

async function saveAlertSettings(extra: Record<string, any> = {}) {
  const patch = {
    alert_auto_enabled: alertAutoOn.value,
    alert_stop_loss_pct: pctToRatio(alertStopPct.value),
    alert_take_profit_pct: pctToRatio(alertTakePct.value),
    alert_peak_pullback_pct: pctToRatio(alertPullPct.value),
    alert_daily_drop_pct: pctToRatio(alertDropPct.value),
    alert_daily_rise_pct: pctToRatio(alertRisePct.value),
    alert_cooldown_minutes: Number(alertCooldown.value) || 30,
    alert_auto_sell_stop: alertAutoSell.value,
    ...extra,
  }
  const res = await axios.patch('/paper/alerts/settings', patch)
  alertSettings.value = res.data.settings || {}
  alertChannels.value = alertSettings.value.channels_ready || []
  return res.data
}

async function toggleAlertAuto() {
  if (!accountId.value && !alertAutoOn.value) {
    pushSystem('请先选择账户，再开启自动告警')
    return
  }
  alertBusy.value = true
  try {
    const next = !alertAutoOn.value
    alertAutoOn.value = next
    await saveAlertSettings({ alert_auto_enabled: next })
    if (next && !schedulerOn.value) {
      await toggleScheduler()
    }
    alertMsg.value = next
      ? '已开启自动告警（随定时器盘中扫描，默认只推送不卖出）'
      : '已关闭自动告警'
    pushSystem(alertMsg.value)
  } catch (e: any) {
    alertAutoOn.value = !alertAutoOn.value
    alertMsg.value = `告警开关失败: ${e?.response?.data?.detail || e.message}`
    pushSystem(alertMsg.value)
  } finally {
    alertBusy.value = false
  }
}

async function saveAlertThresholds() {
  alertBusy.value = true
  try {
    await saveAlertSettings()
    alertMsg.value = '告警阈值已保存'
    pushSystem(alertMsg.value)
  } catch (e: any) {
    alertMsg.value = `保存失败: ${e?.response?.data?.detail || e.message}`
  } finally {
    alertBusy.value = false
  }
}

async function runAlertScan() {
  if (!accountId.value) {
    pushSystem('请先选择账户再扫描告警')
    return
  }
  alertBusy.value = true
  alertMsg.value = '扫描持仓告警…'
  try {
    const res = await axios.post('/paper/alerts/scan', {
      account_id: accountId.value,
      notify: true,
    })
    const n = res.data.fired_count || 0
    const ch = (res.data.channels || []).join(',') || '无通道'
    alertMsg.value = `扫描完成：触发 ${n} 条 · 通道 ${ch}`
    pushSystem(alertMsg.value)
    await loadAlertEvents()
  } catch (e: any) {
    alertMsg.value = `扫描失败: ${e?.response?.data?.detail || e.message}`
    pushSystem(alertMsg.value)
  } finally {
    alertBusy.value = false
  }
}

async function testAlertNotify() {
  alertBusy.value = true
  try {
    const res = await axios.post('/paper/alerts/test', {})
    alertMsg.value = res.data.ok
      ? `测试推送成功：${(res.data.results || []).map((r: any) => r.channel).join(',')}`
      : `测试推送失败：${res.data.error || '未配置通道'}`
    pushSystem(alertMsg.value)
  } catch (e: any) {
    alertMsg.value = `测试失败: ${e?.response?.data?.detail || e.message}`
    pushSystem(alertMsg.value)
  } finally {
    alertBusy.value = false
  }
}

async function loadMonitor() {
  try {
    const [setRes, poolRes, repRes] = await Promise.all([
      axios.get('/paper/monitor/settings'),
      axios.get('/paper/monitor/pool'),
      axios.get('/paper/monitor/reports', { params: { limit: 15 } }),
    ])
    const s = setRes.data.settings || {}
    monAutoOn.value = !!s.monitor_auto_enabled
    monUseLlm.value = s.monitor_use_llm !== false
    monDropPct.value = ratioToPct(s.monitor_daily_drop_pct, -4)
    monRisePct.value = ratioToPct(s.monitor_daily_rise_pct, 6)
    monPullPct.value = ratioToPct(s.monitor_peak_pullback_pct, -3)
    monStopPct.value = ratioToPct(s.monitor_stop_loss_pct, -5)
    monTakePct.value = ratioToPct(s.monitor_take_profit_pct, 8)
    monCooldown.value = Number(s.monitor_cooldown_minutes || 45)
    monPrompt.value = String(s.monitor_report_prompt || setRes.data.default_report_prompt || '')
    monPool.value = poolRes.data.items || []
    monReports.value = repRes.data.reports || []
  } catch {
    monPool.value = []
    monReports.value = []
  }
}

async function saveMonitorSettings(extra: Record<string, any> = {}) {
  const patch = {
    monitor_auto_enabled: monAutoOn.value,
    monitor_use_llm: monUseLlm.value,
    monitor_daily_drop_pct: pctToRatio(monDropPct.value),
    monitor_daily_rise_pct: pctToRatio(monRisePct.value),
    monitor_peak_pullback_pct: pctToRatio(monPullPct.value),
    monitor_stop_loss_pct: pctToRatio(monStopPct.value),
    monitor_take_profit_pct: pctToRatio(monTakePct.value),
    monitor_cooldown_minutes: Number(monCooldown.value) || 45,
    monitor_report_prompt: monPrompt.value || '',
    ...extra,
  }
  await axios.patch('/paper/monitor/settings', patch)
}

async function toggleMonitorAuto() {
  monBusy.value = true
  try {
    const next = !monAutoOn.value
    monAutoOn.value = next
    await saveMonitorSettings({ monitor_auto_enabled: next })
    if (next && !schedulerOn.value) {
      const res = await axios.post('/paper/cycle/scheduler', {
        enabled: true,
        account_id: accountId.value || undefined,
        use_llm: cycleUseLlm.value,
      })
      schedulerOn.value = !!res.data.running
      schedulerStatus.value = res.data.running
        ? `已开启定时（含自选监控）`
        : '定时器未开启'
    }
    monMsg.value = next
      ? '已开启现实自选自动监控（盘中扫描 → LLM Markdown 推送）'
      : '已关闭现实自选自动监控'
    pushSystem(monMsg.value)
  } catch (e: any) {
    monAutoOn.value = !monAutoOn.value
    monMsg.value = `开关失败: ${e?.response?.data?.detail || e.message}`
  } finally {
    monBusy.value = false
  }
}

async function addMonitorSymbol() {
  const code = monCode.value.trim()
  if (!code) {
    pushSystem('请输入股票代码，如 600519 或 600519.SH')
    return
  }
  monBusy.value = true
  try {
    const res = await axios.post('/paper/monitor/pool', {
      ts_code: code,
      cost_basis: monCost.value != null && Number(monCost.value) > 0 ? Number(monCost.value) : null,
      note: monNote.value || null,
    })
    if (res.data.ok) {
      monCode.value = ''
      monCost.value = null
      monNote.value = ''
      monMsg.value = `已加入监控：${res.data.item?.name || ''} ${res.data.item?.ts_code}`
      pushSystem(monMsg.value)
      await loadMonitor()
    } else {
      monMsg.value = res.data.error || '添加失败'
    }
  } catch (e: any) {
    const status = e?.response?.status
    const detail = e?.response?.data?.detail
    const detailText = typeof detail === 'string'
      ? detail
      : (detail ? JSON.stringify(detail) : e.message)
    // 常见原因：后端未重启，旧进程没有 /paper/monitor/* 路由 → 404 Not Found
    monMsg.value = status === 404
      ? '添加失败: 接口不存在(404)。请重启后端后再试（python -m uvicorn backend.main:app --host 127.0.0.1 --port 8010）'
      : `添加失败: ${detailText}`
    pushSystem(monMsg.value)
  } finally {
    monBusy.value = false
  }
}

async function removeMonitorSymbol(tsCode: string) {
  if (!confirm(`从监控池移除 ${tsCode}？`)) return
  try {
    await axios.delete(`/paper/monitor/pool/${encodeURIComponent(tsCode)}`)
    await loadMonitor()
    pushSystem(`已移除 ${tsCode}`)
  } catch (e: any) {
    pushSystem(`移除失败: ${e?.response?.data?.detail || e.message}`)
  }
}

async function saveMonitorThresholds() {
  monBusy.value = true
  try {
    await saveMonitorSettings()
    monMsg.value = '监控阈值已保存'
    pushSystem(monMsg.value)
  } catch (e: any) {
    monMsg.value = `保存失败: ${e?.response?.data?.detail || e.message}`
  } finally {
    monBusy.value = false
  }
}

async function runMonitorScan(force = false) {
  if (!monPool.value.length) {
    pushSystem('请先添加要监控的股票')
    return
  }
  monBusy.value = true
  monMsg.value = force ? '生成监控日报…' : '扫描自选监控…'
  try {
    const res = await axios.post('/paper/monitor/scan', {
      notify: true,
      use_llm: monUseLlm.value,
      force_report: force,
    })
    const n = res.data.fired_count || 0
    const pushed = (res.data.notify?.results || [])
      .map((r: any) => `${r.channel}${r.ok ? '成功' : '失败'}`)
      .join('，')
    monMsg.value = res.data.report_id
      ? `已生成简报 #${res.data.report_id} · 触发 ${n} · ${pushed || (res.data.notified ? '已推送' : '未推送')}`
      : (res.data.message || `扫描完成，触发 ${n}`)
    monReportPreview.value = res.data.markdown || res.data.why_summary || ''
    pushSystem(monMsg.value)
    await loadMonitor()
  } catch (e: any) {
    monMsg.value = `扫描失败: ${e?.response?.data?.detail || e.message}`
    pushSystem(monMsg.value)
  } finally {
    monBusy.value = false
  }
}

async function openMonitorReport(id: number) {
  try {
    const res = await axios.get(`/paper/monitor/reports/${id}`)
    monReportPreview.value = res.data.report?.markdown || ''
  } catch (e: any) {
    pushSystem(`打开报告失败: ${e?.response?.data?.detail || e.message}`)
  }
}

async function toggleScheduler() {
  if (!accountId.value && !schedulerOn.value) {
    pushSystem('请先选择账户，再开启定时器')
    return
  }
  try {
    const res = await axios.post('/paper/cycle/scheduler', {
      enabled: !schedulerOn.value,
      account_id: accountId.value,
      use_llm: cycleUseLlm.value,
    })
    schedulerOn.value = !!res.data.running
    schedulerStatus.value = res.data.running
      ? `已开启：每晚 ${res.data.evening} 选股，交易日盘中每 ${res.data.t_scan_minutes || 5} 分钟做T`
      : '已关闭'
    pushSystem(schedulerStatus.value)
  } catch (e: any) {
    pushSystem(`定时器失败: ${e?.response?.data?.detail || e.message}`)
  }
}

async function loadCycleActions() {
  try {
    const res = await axios.get('/paper/cycle/actions', {
      params: { account_id: accountId.value || undefined, limit: 30 },
    })
    cycleActions.value = res.data.actions || []
  } catch {
    cycleActions.value = []
  }
}

async function runCycle(step: string) {
  if (cycleBusy.value) return
  if (step !== 'evening-screen' && !accountId.value) {
    pushSystem('请先选择账户再跑交易循环')
    return
  }
  cycleBusy.value = true
  cycleMsg.value = `运行 ${step}…`
  try {
    let res
    const body: Record<string, any> = {
      account_id: accountId.value,
      use_llm: cycleUseLlm.value,
    }
    if (step === 'run-trading-day') body.trigger_source = 'manual_daily_lesson'
    if (step === 'daily-t0' || step === 'open-decide') body.trigger_source = 'manual_price_scan'
    if (step === 'afternoon-exposure') body.trigger_source = 'user_button'
    if (step === 'evening-screen') {
      res = await axios.post('/paper/cycle/evening-screen', { fill: true, limit: 200 })
      cycleMsg.value = `晚间选股完成：${res.data.asof || ''} · A/B 共 ${res.data.count || 0} 只入 watchlist`
    } else if (step === 'open-decide') {
      res = await axios.post('/paper/cycle/open-decide', body)
      cycleMsg.value = `日切并扫描：${res.data.asof || ''} · ${(res.data.actions || []).length} 条`
    } else if (step === 'risk-check') {
      res = await axios.post('/paper/cycle/risk-check', body)
      cycleMsg.value = `风控：${(res.data.actions || []).length} 条清仓动作`
    } else if (step === 'daily-t0') {
      res = await axios.post('/paper/cycle/daily-t0', body)
      cycleMsg.value = `分时做T：${res.data.asof || ''} · ${(res.data.actions || []).length} 条`
    } else if (step === 'weekly-review') {
      res = await axios.post('/paper/cycle/weekly-review', { ...body, auto_apply: true })
      const s = res.data.summary || {}
      cycleMsg.value = `周复盘：净值 ${(s.week_return * 100 || 0).toFixed(2)}% · A门槛→${s.new_min_score_a} B门槛→${s.new_min_score_b}`
    } else if (step === 'run-trading-day') {
      res = await axios.post('/paper/cycle/run-trading-day', body)
      cycleMsg.value = `日课完成 ${res.data.asof || ''}`
    } else if (step === 'afternoon-exposure') {
      res = await axios.post('/paper/cycle/afternoon-exposure', body)
      const prob = res.data.portfolio_probability
      const cap = Math.round((res.data.exposure_cap || 0) * 100)
      const ptxt = prob == null ? '无持仓' : `${Math.round(prob * 100)}%`
      cycleMsg.value = `13:00仓位：明日上涨概率 ${ptxt}，总仓位上限 ${cap}%`
    }
    pushSystem(cycleMsg.value)
    await refreshAccountPanel()
    await loadCycleActions()
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e.message
    cycleMsg.value = `失败: ${msg}`
    pushSystem(`循环失败(${step}): ${msg}`)
  } finally {
    cycleBusy.value = false
  }
}

async function settleNow() {
  if (!accountId.value) return
  try {
    const res = await axios.post(`/paper/accounts/${accountId.value}/settle`, {})
    pushSystem(
      `结算完成：成交 ${res.data.filled_count || 0}，跳过 ${res.data.skipped_count || 0}，拒绝 ${res.data.rejected_count || 0}`
    )
    await refreshAccountPanel()
  } catch (e: any) {
    pushSystem(`结算失败: ${e?.response?.data?.detail || e.message}`)
  }
}

async function markNow() {
  if (!accountId.value) return
  try {
    const res = await axios.post(`/paper/accounts/${accountId.value}/mark`, { write_equity: true })
    const src =
      res.data.mark_source === 'intraday'
        ? '分时末价'
        : res.data.mark_source === 'daily_close'
          ? '上一交易日收盘价'
          : res.data.mark_source === 'mixed'
            ? '分时+收盘价'
            : '盯市'
    const miss = (res.data.missing_quotes || []).length
    const fallback = (res.data.fallback_quotes || []).length
    pushSystem(
      miss
        ? `${src}已更新；${miss} 只仍无行情，市值未计入`
        : fallback
          ? `已按${src}盯市（${fallback} 只用收盘价）` + (res.data.asof ? ` · ${res.data.asof}` : '')
          : `已按${src}盯市` + (res.data.asof ? ` · ${res.data.asof}` : '')
    )
    await refreshAccountPanel()
  } catch (e: any) {
    pushSystem(`盯市失败: ${e?.response?.data?.detail || e.message}`)
  }
}

function pushSystem(content: string) {
  messages.value.push({ role: 'system', content, time: new Date() })
}

function startNewChat() {
  sessionId.value = null
  messages.value = []
  pushSystem(
    accountId.value
      ? `纸面交易 Agent 已就绪。当前账户 #${accountId.value}。可说：「感知 300750.SZ 并决定是否建仓」。`
      : '请先开户或选择左侧账户，再开始交易对话。'
  )
}

async function scrollChat() {
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}

async function sendMessage() {
  const msg = inputMessage.value.trim()
  if (!msg || loading.value) return
  if (!accountId.value) {
    pushSystem('请先选择或创建一个纸面账户')
    return
  }

  messages.value.push({ role: 'user', content: msg, time: new Date() })
  inputMessage.value = ''
  loading.value = true
  await scrollChat()

  try {
    const history = sessionId.value
      ? []
      : messages.value
          .filter(m => m.role === 'user' || m.role === 'assistant')
          .slice(0, -1)
          .slice(-10)
          .map(m => ({ role: m.role, content: m.content }))

    const res = await axios.post('/chat/messages', {
      query: msg,
      model: selectedModel.value,
      history,
      use_tools: true,
      agent_mode: 'paper_trade',
      tool_skills: ['paper-trade'],
      account_id: accountId.value,
      session_id: sessionId.value,
      persist: true,
    })

    if (res.data.session_id) sessionId.value = res.data.session_id
    messages.value.push({
      role: 'assistant',
      content: res.data.content || '',
      time: new Date(),
      toolTrace: res.data.tool_trace || [],
    })
    await refreshAccountPanel()
  } catch (e: any) {
    messages.value.push({
      role: 'system',
      content: `对话失败: ${e?.response?.data?.detail || e.message}`,
      time: new Date(),
    })
  } finally {
    loading.value = false
    await scrollChat()
  }
}

function suggest(text: string) {
  inputMessage.value = text
}

watch(accountId, async (id) => {
  await refreshAccountPanel()
  if (id) {
    pushSystem(`已切换到账户 #${id}`)
  }
})

watch(journalDate, async () => {
  if (accountId.value) await refreshAccountPanel()
})

watch(accountId, async () => {
  await loadAlertEvents()
})

onMounted(async () => {
  try {
    await loadAccounts()
    await refreshAccountPanel()
    await loadCycleActions()
    await refreshScheduler()
    await loadAlertSettings()
    await loadAlertEvents()
    await loadMonitor()
  } catch (e) {
    console.warn(e)
  }
  startNewChat()
})
</script>

<template>
  <div class="h-full min-h-0 flex bg-zinc-950 overflow-hidden">
    <!-- Left: Account -->
    <div class="w-64 flex-shrink-0 flex flex-col border-r border-zinc-800 bg-zinc-900/50">
      <div class="px-3 py-3 border-b border-zinc-800">
        <div class="text-xs font-semibold text-zinc-300 flex items-center gap-1.5">
          <i class="fa-solid fa-wallet text-emerald-400"></i> 纸面账户
        </div>
        <div class="mt-2 space-y-1.5">
          <input v-model="createName" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs outline-none" placeholder="账户名" />
          <input v-model.number="createCash" type="number" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs outline-none" />
          <button
            @click="createAccount"
            :disabled="creating"
            class="w-full px-2 py-1.5 rounded-lg text-xs bg-emerald-600/80 hover:bg-emerald-500 disabled:bg-zinc-700"
          >
            {{ creating ? '开户中…' : '新建账户' }}
          </button>
        </div>
      </div>

      <div class="flex-1 overflow-auto p-2 space-y-1">
        <div
          v-for="a in accounts"
          :key="a.id"
          @click="accountId = a.id"
          :class="accountId === a.id ? 'bg-zinc-800 border-emerald-500/40' : 'border-transparent hover:bg-zinc-800/60'"
          class="group relative rounded-lg border px-2.5 py-2 cursor-pointer text-xs"
        >
          <div class="text-zinc-200 font-medium truncate pr-5">#{{ a.id }} {{ a.name }}</div>
          <div class="text-zinc-500 mt-0.5 tabular-nums">净值 {{ fmtMoney(a.equity) }}</div>
          <div v-if="accountId === a.id" class="text-[10px] text-emerald-400/80 mt-0.5">
            持仓 {{ positions.length }} · 今日唤醒 {{ journalWakeCount }}
          </div>
          <button
            @click.stop="deleteAccount(a.id)"
            class="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 text-zinc-600 hover:text-red-400 p-1"
            title="删除账户"
          >
            <i class="fa-solid fa-trash text-[10px]"></i>
          </button>
        </div>
        <div v-if="!accounts.length" class="text-[11px] text-zinc-600 px-2 py-4">暂无账户，请先开户</div>
      </div>

      <div class="border-t border-zinc-800 p-3 space-y-2 text-xs">
        <div class="flex justify-between text-zinc-400"><span>现金</span><span class="tabular-nums text-zinc-200">{{ fmtMoney(account?.cash) }}</span></div>
        <div class="flex justify-between text-zinc-400">
          <span>{{ sessionPhase === 'live' ? '盘中净值' : '分时净值' }}</span>
          <span class="tabular-nums text-emerald-300">{{ fmtMoney(account?.equity) }}</span>
        </div>
        <div v-if="markNote" class="text-[10px] text-amber-400/90 leading-snug">{{ markNote }}</div>
        <div class="flex justify-between text-zinc-400"><span>收益</span><span class="tabular-nums" :class="(stats?.total_return || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ fmtPct(stats?.total_return) }}</span></div>
        <div class="flex gap-1.5 pt-1">
          <button @click="settleNow" :disabled="!accountId" class="flex-1 px-2 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40">结算</button>
          <button @click="markNow" :disabled="!accountId" class="flex-1 px-2 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40">盯市</button>
          <button @click="refreshAccountPanel" class="px-2 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700" :class="{ 'animate-pulse': loadingPanel }">
            <i class="fa-solid fa-rotate"></i>
          </button>
        </div>

        <div class="pt-2 border-t border-zinc-800 space-y-1.5">
          <div class="flex items-center justify-between text-[10px] text-zinc-500">
            <span>交易循环</span>
            <label class="flex items-center gap-1 cursor-pointer">
              <input v-model="cycleUseLlm" type="checkbox" class="rounded border-zinc-600" />
              LLM
            </label>
          </div>
          <button
            :disabled="cycleBusy"
            @click="toggleScheduler"
            :class="schedulerOn ? 'bg-amber-700/80 hover:bg-amber-600' : 'bg-blue-700/70 hover:bg-blue-600'"
            class="w-full px-2 py-1 rounded-md text-[10px] disabled:opacity-40"
          >{{ schedulerOn ? '关闭定时（21:00 / 盘中做T）' : '开启定时（21:00选股 · 盘中每5分钟做T）' }}</button>
          <div v-if="schedulerStatus" class="text-[10px] text-zinc-500 leading-snug">{{ schedulerStatus }}</div>
          <button :disabled="cycleBusy" @click="runCycle('evening-screen')" class="w-full px-2 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">① 晚间选股 A/B Top5</button>
          <button :disabled="cycleBusy || !accountId" @click="runCycle('run-trading-day')" class="w-full px-2 py-1 rounded-md bg-emerald-700/70 hover:bg-emerald-600 text-[10px] disabled:opacity-40">② 一键日课（日切+风控+当前分时做T）</button>
          <div class="grid grid-cols-2 gap-1">
            <button :disabled="cycleBusy || !accountId" @click="runCycle('open-decide')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">日切+扫一次</button>
            <button :disabled="cycleBusy || !accountId" @click="runCycle('risk-check')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">回撤清仓</button>
            <button :disabled="cycleBusy || !accountId" @click="runCycle('daily-t0')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">扫一次分时做T</button>
            <button :disabled="cycleBusy || !accountId" @click="runCycle('weekly-review')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">周复盘</button>
          </div>
          <button :disabled="cycleBusy || !accountId" @click="runCycle('afternoon-exposure')" class="w-full px-2 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">13:00仓位（概率&gt;60%满仓，否则留20%现金）</button>
          <div v-if="cycleMsg" class="text-[10px] text-zinc-500 leading-snug">{{ cycleMsg }}</div>
        </div>

        <div class="pt-2 border-t border-zinc-800 space-y-1.5">
          <div class="flex items-center justify-between text-[10px] text-zinc-500">
            <span>现实自选监控</span>
            <label class="flex items-center gap-1 cursor-pointer">
              <input v-model="monUseLlm" type="checkbox" class="rounded border-zinc-600" />
              LLM简报
            </label>
          </div>
          <div class="text-[10px] text-zinc-600 leading-snug">自己选股监控（与虚拟持仓无关）。勾选 LLM 简报后，按下面的提示词写正文，企业微信和 PushPlus 发送同一份。</div>
          <textarea
            v-model="monPrompt"
            rows="5"
            class="w-full px-1.5 py-1 rounded bg-zinc-900 border border-zinc-700 text-[10px] leading-snug text-zinc-200 outline-none resize-y"
            placeholder="写下简报要包含的栏目。留空并保存，则恢复内置四段结构。"
          />
          <div class="flex gap-1">
            <input
              v-model="monCode"
              placeholder="代码 600519"
              class="flex-1 min-w-0 px-1.5 py-1 rounded bg-zinc-900 border border-zinc-700 text-[10px] text-zinc-200 outline-none"
              @keyup.enter="addMonitorSymbol"
            />
            <input
              v-model.number="monCost"
              type="number"
              step="0.01"
              placeholder="成本"
              class="w-14 px-1 py-1 rounded bg-zinc-900 border border-zinc-700 text-[10px] text-zinc-200 outline-none"
            />
            <button :disabled="monBusy" @click="addMonitorSymbol" class="px-2 py-1 rounded-md bg-emerald-700/70 hover:bg-emerald-600 text-[10px] disabled:opacity-40">加入</button>
          </div>
          <input
            v-model="monNote"
            placeholder="备注（可选）"
            class="w-full px-1.5 py-1 rounded bg-zinc-900 border border-zinc-700 text-[10px] text-zinc-200 outline-none"
          />
          <div v-if="monPool.length" class="max-h-28 overflow-y-auto space-y-0.5">
            <div
              v-for="p in monPool"
              :key="p.ts_code"
              class="flex items-center justify-between gap-1 text-[10px] bg-zinc-900/50 rounded px-1.5 py-1"
            >
              <span class="truncate text-zinc-300">
                <span class="font-mono">{{ p.ts_code }}</span>
                <span class="text-zinc-500 ml-1">{{ p.name }}</span>
                <span v-if="p.cost_basis" class="text-zinc-600 ml-1">成本{{ p.cost_basis }}</span>
              </span>
              <button @click="removeMonitorSymbol(p.ts_code)" class="text-zinc-600 hover:text-red-400 shrink-0">×</button>
            </div>
          </div>
          <div v-else class="text-[10px] text-zinc-600">监控池为空，请添加你现实持仓的股票</div>
          <button
            :disabled="monBusy"
            @click="toggleMonitorAuto"
            :class="monAutoOn ? 'bg-amber-700/80 hover:bg-amber-600' : 'bg-teal-700/70 hover:bg-teal-600'"
            class="w-full px-2 py-1 rounded-md text-[10px] disabled:opacity-40"
          >{{ monAutoOn ? '关闭自动监控（盘中 LLM 简报）' : '开启自动监控（盘中扫描 → Markdown 推送）' }}</button>
          <div class="grid grid-cols-2 gap-1 text-[10px]">
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">大跌%</span>
              <input v-model.number="monDropPct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">大涨%</span>
              <input v-model.number="monRisePct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">回撤%</span>
              <input v-model.number="monPullPct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">冷却分</span>
              <input v-model.number="monCooldown" type="number" step="5" min="1" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">成本止损%</span>
              <input v-model.number="monStopPct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">成本止盈%</span>
              <input v-model.number="monTakePct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
          </div>
          <div class="grid grid-cols-3 gap-1">
            <button :disabled="monBusy" @click="saveMonitorThresholds" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">保存</button>
            <button :disabled="monBusy || !monPool.length" @click="runMonitorScan(false)" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">立即扫描</button>
            <button :disabled="monBusy || !monPool.length" @click="runMonitorScan(true)" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">强制日报</button>
          </div>
          <div v-if="monMsg" class="text-[10px] text-zinc-500 leading-snug">{{ monMsg }}</div>
        </div>

        <div class="pt-2 border-t border-zinc-800 space-y-1.5">
          <div class="flex items-center justify-between text-[10px] text-zinc-500">
            <span>虚拟账户告警</span>
            <span class="text-zinc-600">{{ (alertChannels.filter(c => c.configured).map(c => c.name).join(' · ')) || '未配置通道' }}</span>
          </div>
          <button
            :disabled="alertBusy"
            @click="toggleAlertAuto"
            :class="alertAutoOn ? 'bg-amber-700/80 hover:bg-amber-600' : 'bg-violet-700/70 hover:bg-violet-600'"
            class="w-full px-2 py-1 rounded-md text-[10px] disabled:opacity-40"
          >{{ alertAutoOn ? '关闭自动告警（盘中随定时器扫描）' : '开启自动告警（盘中扫描 · 微信推送）' }}</button>
          <div class="grid grid-cols-2 gap-1 text-[10px]">
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">止损%</span>
              <input v-model.number="alertStopPct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">止盈%</span>
              <input v-model.number="alertTakePct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">回撤%</span>
              <input v-model.number="alertPullPct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">冷却分</span>
              <input v-model.number="alertCooldown" type="number" step="5" min="1" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">大跌%</span>
              <input v-model.number="alertDropPct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
            <label class="flex items-center justify-between gap-1 bg-zinc-900/50 rounded px-1.5 py-1">
              <span class="text-zinc-500">大涨%</span>
              <input v-model.number="alertRisePct" type="number" step="0.5" class="w-14 bg-transparent text-right text-zinc-200 outline-none" />
            </label>
          </div>
          <label class="flex items-center gap-1.5 text-[10px] text-zinc-500 cursor-pointer">
            <input v-model="alertAutoSell" type="checkbox" class="rounded border-zinc-600" />
            止损时自动卖出（默认关，仅提醒）
          </label>
          <div class="grid grid-cols-3 gap-1">
            <button :disabled="alertBusy" @click="saveAlertThresholds" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">保存阈值</button>
            <button :disabled="alertBusy || !accountId" @click="runAlertScan" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">立即扫描</button>
            <button :disabled="alertBusy" @click="testAlertNotify" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">测试推送</button>
          </div>
          <div v-if="alertMsg" class="text-[10px] text-zinc-500 leading-snug">{{ alertMsg }}</div>
        </div>
      </div>
    </div>

    <!-- Center: Agent chat -->
    <div class="flex-1 min-w-0 flex flex-col">
      <div class="flex-shrink-0 px-4 py-2.5 border-b border-zinc-800 bg-zinc-900/40 flex items-center gap-3">
        <div class="text-sm font-semibold text-zinc-200">交易 Agent</div>
        <span class="text-[11px] text-zinc-500">{{ currentAccountLabel }} · 感知→决策→执行→反馈</span>
        <div class="flex-1"></div>
        <div class="flex gap-1">
          <button
            v-for="m in quickModels"
            :key="m.id"
            @click="selectedModel = m.id"
            :class="selectedModel === m.id ? 'bg-emerald-600/25 text-emerald-300 border-emerald-500/40' : 'text-zinc-500 border-transparent hover:text-zinc-300'"
            class="text-[10px] px-2 py-1 rounded-md border"
          >{{ m.name }}</button>
        </div>
        <button @click="startNewChat" class="text-[10px] px-2 py-1 rounded-md bg-zinc-800 text-zinc-400 hover:text-white">新对话</button>
      </div>

      <div ref="chatBox" class="flex-1 overflow-auto p-4 space-y-4">
        <div v-for="(msg, i) in messages" :key="i" :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'">
          <div
            :class="msg.role === 'user'
              ? 'bg-emerald-700 text-white max-w-[80%] px-4 py-2.5 rounded-2xl rounded-tr-md'
              : msg.role === 'system'
                ? 'bg-zinc-800/70 max-w-[85%] px-4 py-2.5 rounded-2xl border border-zinc-700 text-zinc-400'
                : 'bg-zinc-800/90 max-w-[85%] px-4 py-2.5 rounded-2xl rounded-tl-md'"
          >
            <div class="text-sm leading-relaxed" v-html="formatContent(msg.content)"></div>
            <div v-if="msg.toolTrace?.length" class="mt-2 space-y-1">
              <div class="text-[10px] text-amber-400/90">工具轨迹</div>
              <div
                v-for="(step, si) in msg.toolTrace"
                :key="si"
                class="bg-zinc-950/60 border border-zinc-700/70 rounded-lg px-2 py-1.5 text-[11px]"
              >
                <div class="flex items-center gap-1.5">
                  <span class="font-mono text-emerald-400/90 truncate">{{ step.function }}</span>
                  <span :class="step.result_ok === false ? 'text-red-400' : 'text-emerald-400'" class="ml-auto">{{ step.result_ok === false ? '失败' : '成功' }}</span>
                </div>
                <div v-if="step.arguments" class="text-zinc-500 font-mono truncate mt-0.5">{{ JSON.stringify(step.arguments) }}</div>
              </div>
            </div>
            <div class="text-[10px] text-zinc-500 mt-1">{{ formatTime(msg.time) }}</div>
          </div>
        </div>
        <div v-if="loading" class="text-xs text-zinc-500 px-2"><i class="fa-solid fa-spinner animate-spin mr-1"></i>Agent 思考 / 调工具中…</div>
      </div>

      <div class="flex-shrink-0 border-t border-zinc-800 p-3 space-y-2">
        <div class="flex flex-wrap gap-1.5">
          <button @click="suggest('查看当前账户与持仓，并给出风险提示')" class="text-[10px] px-2 py-1 rounded-md bg-zinc-800 text-zinc-400 hover:text-white">查账户</button>
          <button @click="suggest('感知 300750.SZ 今日状态，并决定是否买入 500 股（说明理由）')" class="text-[10px] px-2 py-1 rounded-md bg-zinc-800 text-zinc-400 hover:text-white">感知+决策</button>
          <button @click="suggest('结算所有 pending 挂单，然后盯市并汇报收益')" class="text-[10px] px-2 py-1 rounded-md bg-zinc-800 text-zinc-400 hover:text-white">结算反馈</button>
          <button @click="suggest('统计本账户交易表现，说明最大回撤与胜率')" class="text-[10px] px-2 py-1 rounded-md bg-zinc-800 text-zinc-400 hover:text-white">看统计</button>
        </div>
        <div class="flex gap-2">
          <textarea
            v-model="inputMessage"
            @keydown.enter.exact.prevent="sendMessage"
            rows="2"
            class="flex-1 bg-zinc-900 border border-zinc-700 focus:border-emerald-500/50 rounded-xl px-3 py-2 text-sm outline-none resize-none"
            placeholder="对交易 Agent 下指令，如：感知某标的并决定买卖…"
          />
          <button
            @click="sendMessage"
            :disabled="loading || !inputMessage.trim()"
            class="px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-sm"
          >发送</button>
        </div>
      </div>
    </div>

    <!-- Right: Positions + journal -->
    <div class="w-[22rem] flex-shrink-0 flex flex-col border-l border-zinc-800 bg-zinc-900/40 overflow-hidden">
      <div class="px-3 py-2.5 border-b border-zinc-800 text-xs font-semibold text-zinc-300">持仓 · 当日日志</div>
      <div class="flex-1 overflow-auto p-3 space-y-4 text-xs">
        <section>
          <div class="text-zinc-500 mb-1.5 flex items-center justify-between">
            <span>持仓 ({{ positions.length }})</span>
            <span v-if="account" class="text-[10px] text-zinc-600">点击左侧账户查看</span>
          </div>
          <div v-if="!positions.length" class="text-zinc-600">空仓</div>
          <div v-for="p in positions" :key="p.ts_code" class="bg-zinc-950/50 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5">
            <div class="flex justify-between items-baseline gap-2">
              <div class="min-w-0">
                <div class="text-zinc-100 font-medium truncate">{{ p.name || '—' }}</div>
                <div class="font-mono text-[10px] text-zinc-500">{{ p.ts_code }}</div>
              </div>
              <div class="text-right shrink-0">
                <div class="tabular-nums text-zinc-200">{{ p.quantity }} 股</div>
                <div class="text-[10px] tabular-nums" :class="p.pnl_ready === false ? 'text-zinc-600' : pnlClass(p.unrealized_pnl)">
                  {{ p.pnl_ready === false ? '盈亏未计' : fmtSigned(p.unrealized_pnl) }}
                </div>
              </div>
            </div>
            <div class="mt-1.5 grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px] text-zinc-500">
              <span>成本 {{ fmtMoney(p.avg_cost) }}</span>
              <span class="text-right">{{
                p.pnl_ready === false
                  ? '暂无行情'
                  : (p.quote_source === 'daily_close' ? `收盘 ${fmtMoney(p.market_price)}` : `分时 ${fmtMoney(p.market_price)}`)
              }}</span>
              <span>市值 {{ p.pnl_ready === false ? '-' : fmtMoney(p.market_value) }}</span>
              <span class="text-right" :class="p.pnl_ready === false ? 'text-zinc-600' : pnlClass(p.pnl_pct)">
                {{ p.pnl_ready === false ? (p.quote_error || '盈亏未计') : `盈亏 ${fmtPct(p.pnl_pct)}` }}
              </span>
              <span>可卖 {{ p.sellable_qty ?? '-' }}</span>
              <span class="text-right">{{ p.quote_clock ? `分时 ${p.quote_clock}` : (p.weight != null ? `仓位 ${fmtPct(p.weight)}` : '-') }}</span>
            </div>
          </div>
        </section>

        <section>
          <div class="flex items-center justify-between text-zinc-500 mb-1.5 gap-2">
            <span>当日操作</span>
            <input v-model="journalDate" type="date" class="bg-zinc-900 border border-zinc-700 rounded px-1 py-0.5 text-[10px] text-zinc-300 outline-none" />
          </div>
          <div class="text-[10px] text-zinc-500 mb-1.5 leading-snug">
            {{ journalSummary || '当日尚无操作' }}
            <span v-if="journalWakeCount"> · 模型唤醒 {{ journalWakeCount }} 次</span>
          </div>
          <div v-if="!journalEvents.length" class="text-zinc-600">跑日课、盘中扫描或对话交易后会写在这里</div>
          <div v-for="e in journalEvents.slice(0, 40)" :key="e.id" class="bg-zinc-950/40 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5">
            <div class="flex justify-between gap-2 items-start">
              <div class="text-zinc-200 leading-snug">{{ e.title || e.event_type }}</div>
              <span class="text-[10px] text-zinc-600 tabular-nums shrink-0">{{ formatClock(e.created_at) }}</span>
            </div>
            <div class="mt-0.5 flex flex-wrap gap-1 text-[10px]">
              <span v-if="e.trigger_label" class="px-1 rounded bg-zinc-800 text-amber-300/90">{{ e.trigger_label }}</span>
              <span v-if="e.llm_woken" class="px-1 rounded bg-emerald-900/50 text-emerald-300">第 {{ e.llm_wake_index || '-' }} 次唤醒</span>
              <span v-if="e.ts_code" class="font-mono text-zinc-400">{{ e.stock_name ? e.stock_name + ' ' : '' }}{{ e.ts_code }}</span>
              <span v-if="e.action" class="text-zinc-400">{{ e.action }}</span>
            </div>
            <div v-if="obsLine(e)" class="text-zinc-500 mt-1 leading-snug">观察：{{ obsLine(e) }}</div>
            <div v-else-if="e.detail" class="text-zinc-500 mt-1 leading-snug line-clamp-3">{{ e.detail }}</div>
          </div>
        </section>

        <section>
          <div class="text-zinc-500 mb-1.5">最近成交</div>
          <div v-if="!fills.length" class="text-zinc-600">暂无</div>
          <div v-for="f in fills.slice(0, 12)" :key="f.id" class="border-b border-zinc-800/80 py-1.5 flex justify-between gap-2">
            <span class="font-mono text-zinc-300">{{ f.ts_code }}</span>
            <span :class="f.side === 'buy' ? 'text-emerald-400' : 'text-red-400'">{{ f.side }}</span>
            <span class="tabular-nums text-zinc-400">{{ f.quantity }}@{{ fmtMoney(f.price) }}</span>
          </div>
        </section>

        <section>
          <div class="text-zinc-500 mb-1.5">委托</div>
          <div v-if="!orders.length" class="text-zinc-600">暂无</div>
          <div v-for="o in orders.slice(0, 10)" :key="o.id" class="border-b border-zinc-800/80 py-1.5">
            <div class="flex justify-between">
              <span class="font-mono">#{{ o.id }} {{ o.ts_code }}</span>
              <span class="text-zinc-500">{{ o.status }}</span>
            </div>
            <div class="text-zinc-500">{{ o.side }} {{ o.quantity }} · {{ o.exec_price_mode }}</div>
          </div>
        </section>

        <section>
          <div class="text-zinc-500 mb-1.5">决策日志</div>
          <div v-if="!decisions.length" class="text-zinc-600">对话后自动写入</div>
          <div v-for="d in decisions.slice(0, 8)" :key="d.id" class="bg-zinc-950/40 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5">
            <div class="text-zinc-400 line-clamp-2">{{ d.user_query }}</div>
            <div class="text-zinc-600 mt-1 text-[10px]">订单 {{ (d.order_ids || []).join(',') || '-' }} · {{ d.created_at }}</div>
          </div>
        </section>

        <section>
          <div class="flex items-center justify-between text-zinc-500 mb-1.5">
            <span>循环动作</span>
            <button @click="loadCycleActions" class="text-[10px] text-zinc-600 hover:text-zinc-300">刷新</button>
          </div>
          <div v-if="!cycleActions.length" class="text-zinc-600">跑循环后显示买卖理由</div>
          <div v-for="a in cycleActions.slice(0, 12)" :key="a.id" class="bg-zinc-950/40 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5">
            <div class="flex justify-between gap-2">
              <span class="font-mono text-zinc-300">{{ a.ts_code || '-' }}</span>
              <span class="text-emerald-400">{{ a.action }}</span>
            </div>
            <div class="text-zinc-500 mt-0.5 line-clamp-2">{{ a.reason }}</div>
            <div class="text-zinc-600 mt-1 text-[10px]">{{ a.step }} · {{ a.asof }}</div>
          </div>
        </section>

        <section>
          <div class="flex items-center justify-between text-zinc-500 mb-1.5">
            <span>自选监控简报</span>
            <button @click="loadMonitor" class="text-[10px] text-zinc-600 hover:text-zinc-300">刷新</button>
          </div>
          <div v-if="!monReports.length" class="text-zinc-600">触发后生成 Markdown 简报</div>
          <div
            v-for="r in monReports.slice(0, 8)"
            :key="r.id"
            class="bg-zinc-950/40 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5 cursor-pointer hover:border-zinc-600"
            @click="openMonitorReport(r.id)"
          >
            <div class="flex justify-between gap-2">
              <span class="text-zinc-300 line-clamp-1">{{ r.title }}</span>
              <span class="text-zinc-600 shrink-0">#{{ r.id }}</span>
            </div>
            <div class="text-zinc-500 mt-0.5 line-clamp-2">{{ r.why_summary }}</div>
            <div class="text-zinc-600 mt-1 text-[10px]">{{ r.notified ? '已推送' : '未推送' }} · {{ r.created_at }}</div>
          </div>
          <div v-if="monReportPreview" class="mt-2 max-h-64 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950/60 p-2.5 text-[10px] text-zinc-400 whitespace-pre-wrap leading-relaxed">{{ monReportPreview }}</div>
        </section>

        <section>
          <div class="flex items-center justify-between text-zinc-500 mb-1.5">
            <span>虚拟账户告警</span>
            <button @click="loadAlertEvents" class="text-[10px] text-zinc-600 hover:text-zinc-300">刷新</button>
          </div>
          <div v-if="!alertEvents.length" class="text-zinc-600">触发止损/止盈/回撤/大涨跌后显示</div>
          <div v-for="e in alertEvents.slice(0, 12)" :key="e.id" class="bg-zinc-950/40 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5">
            <div class="flex justify-between gap-2">
              <span class="font-mono text-zinc-300">{{ e.ts_code || '-' }}</span>
              <span :class="e.severity === 'critical' ? 'text-red-400' : e.severity === 'good' ? 'text-emerald-400' : 'text-amber-400'">{{ e.rule_id }}</span>
            </div>
            <div class="text-zinc-500 mt-0.5 line-clamp-2">{{ e.title }}</div>
            <div class="text-zinc-600 mt-1 text-[10px]">{{ e.notified ? '已推送' : '未推送' }} · {{ e.created_at }}</div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
