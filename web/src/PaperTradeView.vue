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
  quantity: number
  avg_cost: number
  market_price?: number
  market_value?: number
  unrealized_pnl?: number
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

const quickModels = [
  { id: 'deepseek-chat', name: 'DeepSeek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek R1' },
  { id: 'gpt-4o-mini', name: 'GPT-4o Mini' },
]

const currentAccountLabel = computed(() => {
  if (!account.value) return '未选择账户'
  return `#${account.value.id} ${account.value.name}`
})

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
    return
  }
  loadingPanel.value = true
  try {
    const [a, o, f, d, s] = await Promise.all([
      axios.get(`/paper/accounts/${accountId.value}`),
      axios.get(`/paper/accounts/${accountId.value}/orders`, { params: { limit: 30 } }),
      axios.get(`/paper/accounts/${accountId.value}/fills`, { params: { limit: 30 } }),
      axios.get(`/paper/accounts/${accountId.value}/decisions`, { params: { limit: 30 } }),
      axios.get(`/paper/accounts/${accountId.value}/stats`),
    ])
    account.value = a.data.account
    positions.value = a.data.positions || []
    orders.value = o.data.orders || []
    fills.value = f.data.fills || []
    decisions.value = d.data.decisions || []
    stats.value = s.data.stats || null
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
      ? `已开启：每晚 ${res.data.evening} 选股，交易日 ${res.data.open} 日课`
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
    const body = {
      account_id: accountId.value,
      use_llm: cycleUseLlm.value,
    }
    if (step === 'evening-screen') {
      res = await axios.post('/paper/cycle/evening-screen', { fill: false, limit: 200 })
      cycleMsg.value = `晚间选股完成：A/B 共 ${res.data.count || 0} 只入 watchlist`
    } else if (step === 'open-decide') {
      res = await axios.post('/paper/cycle/open-decide', body)
      cycleMsg.value = `开市决策：${(res.data.actions || []).length} 条动作`
    } else if (step === 'risk-check') {
      res = await axios.post('/paper/cycle/risk-check', body)
      cycleMsg.value = `风控：${(res.data.actions || []).length} 条清仓动作`
    } else if (step === 'daily-t0') {
      res = await axios.post('/paper/cycle/daily-t0', body)
      cycleMsg.value = `日度做T：${(res.data.actions || []).length} 条`
    } else if (step === 'weekly-review') {
      res = await axios.post('/paper/cycle/weekly-review', { ...body, auto_apply: true })
      const s = res.data.summary || {}
      cycleMsg.value = `周复盘：净值 ${(s.week_return * 100 || 0).toFixed(2)}% · A门槛→${s.new_min_score_a} B门槛→${s.new_min_score_b}`
    } else if (step === 'run-trading-day') {
      res = await axios.post('/paper/cycle/run-trading-day', body)
      cycleMsg.value = `日课完成 ${res.data.asof || ''}`
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
    await axios.post(`/paper/accounts/${accountId.value}/mark`, { write_equity: true })
    pushSystem('已盯市并写入净值快照')
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

onMounted(async () => {
  try {
    await loadAccounts()
    await refreshAccountPanel()
    await loadCycleActions()
    await refreshScheduler()
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
        <div class="flex justify-between text-zinc-400"><span>净值</span><span class="tabular-nums text-emerald-300">{{ fmtMoney(account?.equity) }}</span></div>
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
          >{{ schedulerOn ? '关闭定时（21:00 / 09:30）' : '开启定时（21:00选股 · 09:30日课）' }}</button>
          <div v-if="schedulerStatus" class="text-[10px] text-zinc-500 leading-snug">{{ schedulerStatus }}</div>
          <button :disabled="cycleBusy" @click="runCycle('evening-screen')" class="w-full px-2 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">① 晚间选股 A/B Top5</button>
          <button :disabled="cycleBusy || !accountId" @click="runCycle('run-trading-day')" class="w-full px-2 py-1 rounded-md bg-emerald-700/70 hover:bg-emerald-600 text-[10px] disabled:opacity-40">② 一键日课（开市+风控+做T）</button>
          <div class="grid grid-cols-2 gap-1">
            <button :disabled="cycleBusy || !accountId" @click="runCycle('open-decide')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">开市决策</button>
            <button :disabled="cycleBusy || !accountId" @click="runCycle('risk-check')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">回撤清仓</button>
            <button :disabled="cycleBusy || !accountId" @click="runCycle('daily-t0')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">日度做T</button>
            <button :disabled="cycleBusy || !accountId" @click="runCycle('weekly-review')" class="px-1.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[10px] disabled:opacity-40">周复盘</button>
          </div>
          <div v-if="cycleMsg" class="text-[10px] text-zinc-500 leading-snug">{{ cycleMsg }}</div>
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

    <!-- Right: Audit -->
    <div class="w-80 flex-shrink-0 flex flex-col border-l border-zinc-800 bg-zinc-900/40 overflow-hidden">
      <div class="px-3 py-2.5 border-b border-zinc-800 text-xs font-semibold text-zinc-300">持仓 · 成交 · 决策审计</div>
      <div class="flex-1 overflow-auto p-3 space-y-4 text-xs">
        <section>
          <div class="text-zinc-500 mb-1.5">持仓 ({{ positions.length }})</div>
          <div v-if="!positions.length" class="text-zinc-600">空仓</div>
          <div v-for="p in positions" :key="p.ts_code" class="bg-zinc-950/50 border border-zinc-800 rounded-lg px-2.5 py-2 mb-1.5">
            <div class="flex justify-between"><span class="text-zinc-200 font-mono">{{ p.ts_code }}</span><span class="tabular-nums">{{ p.quantity }} 股</span></div>
            <div class="text-zinc-500 mt-0.5 flex justify-between gap-2 text-[10px]">
              <span>可卖 {{ p.sellable_qty ?? '-' }}</span>
              <span>冻结 {{ p.frozen_qty ?? 0 }}</span>
            </div>
            <div class="text-zinc-500 mt-0.5 flex justify-between">
              <span>成本 {{ fmtMoney(p.avg_cost) }}</span>
              <span :class="(p.unrealized_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">{{ fmtMoney(p.unrealized_pnl) }}</span>
            </div>
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
      </div>
    </div>
  </div>
</template>
