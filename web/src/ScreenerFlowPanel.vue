<template>
  <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
    <div class="flex items-center justify-between gap-2">
      <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
        <i class="fa-solid fa-diagram-project text-emerald-400"></i> 阶段 × 流程选股
        <span class="text-[10px] text-zinc-500 font-normal">点左边阶段、中间方法或右边步骤，连线会亮；搜索只跑当前选中对应的方法，并按方法加载不同长度的日线</span>
      </h4>
      <div class="flex items-center gap-1.5 flex-shrink-0">
        <button
          type="button"
          @click="runPipeline"
          :disabled="pipelineLoading || screenerLoading"
          class="px-3 py-1.5 rounded-lg text-[11px] bg-rose-600/20 text-rose-300 border border-rose-500/30 hover:bg-rose-600/30 disabled:opacity-50"
          title="缩量筑底观察≥70→自选50→起飞推荐→衰竭出池"
        >
          <i class="fa-solid fa-rocket mr-1" :class="{ 'fa-spinner animate-spin': pipelineLoading }"></i>
          {{ pipelineLoading ? '流水线…' : '跑推荐流水线' }}
        </button>
        <button
          type="button"
          @click="runScreener"
          :disabled="screenerLoading || !methodsToRun.length"
          class="px-3 py-1.5 rounded-lg text-[11px] bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30 disabled:opacity-50"
        >
          <i class="fa-solid fa-magnifying-glass mr-1" :class="{ 'fa-spinner animate-spin': screenerLoading }"></i>
          {{ screenerLoading ? '搜索并加载数据…' : searchLabel }}
        </button>
      </div>
    </div>

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-2">
      <div>
        <label class="text-[10px] text-zinc-500 block mb-1">交易日</label>
        <input
          v-model="screener.asof"
          type="date"
          class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none"
          @change="screenerAsofPinned = true"
        />
      </div>
      <div>
        <label class="text-[10px] text-zinc-500 block mb-1">最多返回</label>
        <input v-model.number="screener.limit" type="number" min="10" max="1000" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
      </div>
      <div class="col-span-2 flex flex-wrap items-end gap-1.5 pb-0.5">
        <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
          <input v-model="screener.includeMain" type="checkbox" class="rounded border-zinc-600" /> 主板
        </label>
        <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
          <input v-model="screener.includeStar" type="checkbox" class="rounded border-zinc-600" /> 科创板
        </label>
        <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
          <input v-model="screener.includeChinext" type="checkbox" class="rounded border-zinc-600" /> 创业板
        </label>
        <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
          <input v-model="screener.includeBse" type="checkbox" class="rounded border-zinc-600" /> 北交所
        </label>
        <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
          <input v-model="screener.includeSt" type="checkbox" class="rounded border-zinc-600" /> 包含 ST
        </label>
      </div>
    </div>

    <div class="flex flex-wrap gap-1.5">
      <button
        type="button"
        class="px-2 py-0.5 rounded-full text-[10px] border"
        :class="filter === 'all' ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-200' : 'border-zinc-700 text-zinc-400'"
        @click="filter = 'all'"
      >全部方法</button>
      <button
        v-for="r in regimes"
        :key="r.id"
        type="button"
        class="px-2 py-0.5 rounded-full text-[10px] border"
        :class="filter === r.id ? 'border-amber-500/50 bg-amber-500/15 text-amber-200' : 'border-zinc-700 text-zinc-400'"
        @click="selectRegime(r.id)"
      >{{ r.name }}</button>
    </div>

    <div class="relative rounded-xl border border-zinc-800 bg-zinc-950 overflow-hidden" style="height: 500px">
      <svg class="absolute inset-0 w-full h-full" viewBox="0 0 1000 500" preserveAspectRatio="xMidYMid meet">
        <text x="24" y="22" fill="#71717a" font-size="11">市场阶段</text>
        <text x="248" y="14" fill="#71717a" font-size="11">方法 / 条件</text>
        <text x="818" y="18" fill="#71717a" font-size="11">流程步骤</text>
        <path
          v-for="e in edges"
          :key="e.key"
          :d="curve(e.from, e.to)"
          fill="none"
          :stroke="e.on ? '#34d399' : '#3f3f46'"
          :stroke-width="e.on ? 2 : 1"
          :opacity="e.on ? 1 : 0.35"
        />
      </svg>
      <button
        v-for="r in regimes"
        :key="r.id"
        type="button"
        class="absolute text-left rounded-md px-2.5 py-1.5 border transition-opacity"
        :style="boxStyle(REGIME_POS[r.id], BOX.regime, sel.kind === 'regime' && sel.id === r.id, !lit.has(r.id))"
        @click="selectRegime(r.id)"
      >
        <div class="text-[13px] font-medium text-zinc-100 leading-[18px]">{{ r.name }}</div>
        <div class="text-[11px] text-zinc-500 leading-[14px] mt-0.5">{{ r.hint }}</div>
      </button>
      <button
        v-for="m in visibleMethods"
        :key="m.id"
        type="button"
        class="absolute text-left rounded-md px-2.5 py-1.5 border transition-opacity"
        :style="boxStyle(methodPos(m.id), BOX.method, sel.kind === 'method' && sel.id === m.id, !lit.has(m.id))"
        @click="selectMethod(m.id)"
      >
        <div class="text-[13px] font-medium text-zinc-100 leading-[18px] truncate">{{ m.name }}</div>
        <div class="text-[11px] leading-[14px] mt-0.5" :class="m.buyable ? 'text-emerald-400/80' : 'text-zinc-500'">
          {{ m.buyable ? '可买入' : '不直接买' }} · {{ m.lookback_bars }}日
        </div>
      </button>
      <button
        v-for="s in steps"
        :key="s.id"
        type="button"
        class="absolute text-left rounded-md px-2.5 py-1.5 border transition-opacity"
        :style="boxStyle(STEP_POS[s.id], BOX.step, sel.kind === 'step' && sel.id === s.id, !lit.has(s.id))"
        @click="selectStep(s.id)"
      >
        <div class="text-[13px] font-medium text-zinc-100 leading-[18px]">{{ s.name }}</div>
        <div class="text-[11px] text-zinc-500 leading-[14px] mt-0.5">{{ s.hint }}</div>
      </button>
    </div>

    <div v-if="selectedDetail" class="rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2.5 space-y-1.5">
      <div class="flex items-center justify-between gap-2">
        <div class="text-xs text-zinc-200">{{ selectedDetail.title }}</div>
        <div class="text-[10px] text-zinc-500">将加载 {{ lookbackNeed }} 个交易日 · {{ methodsToRun.length }} 种方法</div>
      </div>
      <p class="text-[11px] text-zinc-400 leading-relaxed">{{ selectedDetail.body }}</p>
      <p v-if="selectedDetail.need" class="text-[10px] text-zinc-500">数据：{{ selectedDetail.need }}</p>
    </div>

    <p v-if="pipelineError" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{{ pipelineError }}</p>
    <div v-if="pipelineResult" class="text-[10px] text-rose-300/90 bg-rose-950/20 border border-rose-900/40 rounded-lg px-3 py-2 space-y-0.5">
      <div>
        流水线 {{ pipelineResult.asof }}
        · 观察 {{ pipelineResult.observe?.count ?? 0 }}
        · 起飞入池 {{ pipelineResult.takeoff?.promoted_count ?? 0 }}
        · 衰竭出池 {{ pipelineResult.exhaustion?.exhausted_count ?? 0 }}
        · 推荐池活跃 {{ pipelineResult.recommend_active ?? 0 }}
      </div>
    </div>
    <p v-if="screenerError" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{{ screenerError }}</p>
    <div v-if="screenerResult" class="text-[10px] text-zinc-400 space-y-0.5">
      <div>
        交易日 {{ screenerResult.asof }}
        · 股票池 {{ screenerResult.universe }}
        · 窗口 {{ screenerResult.lookback }} 日
        · 命中 {{ screenerResult.hit_count ?? 0 }}
        <span v-if="screenerResult.market_regime?.name"> · 沪深300 {{ screenerResult.market_regime.name }}</span>
      </div>
      <div v-if="screenerResult.fill_report" class="text-zinc-500">
        Datahubco
        · 补 {{ (screenerResult.fill_report.days_fetched || []).length }} 日
        · 写入 {{ screenerResult.fill_report.rows ?? screenerResult.filled_rows ?? 0 }} 行
        · 已齐跳过 {{ (screenerResult.fill_report.days_skipped || []).length }} 日
      </div>
      <div v-if="screenerResult.note" class="text-zinc-600">{{ screenerResult.note }}</div>
    </div>
    <div v-if="screenerLoading" class="text-xs text-zinc-500 py-4 text-center">
      <i class="fa-solid fa-spinner animate-spin mr-1"></i>
      正在加载近 {{ lookbackNeed }} 个交易日全市场日线，并按「{{ methodsToRun.map(id => methodName(id)).join(' / ') }}」筛选…
    </div>

    <div v-for="group in hitGroups" :key="group.id" class="space-y-1.5">
      <div class="flex items-center gap-2 text-[11px]">
        <span class="px-1.5 py-0.5 rounded border" :class="group.buyable ? 'bg-emerald-500/15 border-emerald-500/20 text-emerald-300' : 'bg-zinc-800 border-zinc-700 text-zinc-300'">{{ group.name }}</span>
        <span class="text-zinc-500">{{ group.hits.length }} 只 · {{ group.buyable ? '可进入买入' : '只观察或调仓/卖出' }}</span>
      </div>
      <div class="border border-zinc-800 rounded-xl overflow-hidden">
        <table class="w-full text-[11px]">
          <thead class="bg-zinc-800/80 text-zinc-500">
            <tr>
              <th class="text-left px-2 py-1.5 font-medium">代码</th>
              <th class="text-left px-2 py-1.5 font-medium">名称</th>
              <th class="text-left px-2 py-1.5 font-medium">板块</th>
              <th class="text-right px-2 py-1.5 font-medium">收盘</th>
              <th class="text-right px-2 py-1.5 font-medium">阶段</th>
              <th class="text-right px-2 py-1.5 font-medium">涨跌%</th>
              <th class="text-right px-2 py-1.5 font-medium">得分</th>
              <th class="text-left px-2 py-1.5 font-medium">原因</th>
              <th class="px-2 py-1.5"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="h in group.hits"
              :key="group.id + '-' + h.ts_code"
              class="border-t border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer"
              :class="screenerActiveCode === h.ts_code ? 'bg-emerald-500/10' : ''"
              title="点击在行情看板查看"
              @click="openHit(h)"
            >
              <td class="px-2 py-1.5 font-mono text-zinc-300">{{ h.ts_code }}</td>
              <td class="px-2 py-1.5 text-zinc-200">
                {{ h.name }}
                <span v-if="h.is_st" class="ml-1 text-[9px] text-red-400">ST</span>
              </td>
              <td class="px-2 py-1.5 text-zinc-500">{{ h.market }}</td>
              <td class="px-2 py-1.5 text-right tabular-nums">{{ h.close }}</td>
              <td class="px-2 py-1.5 text-right text-zinc-400">{{ regimeLabel(h.stock_regime) }}</td>
              <td class="px-2 py-1.5 text-right tabular-nums" :class="Number(h.pct_chg) >= 0 ? 'text-red-400' : 'text-emerald-400'">{{ h.pct_chg }}</td>
              <td class="px-2 py-1.5 text-right tabular-nums text-emerald-300">{{ h.score }}</td>
              <td class="px-2 py-1.5 text-zinc-500">{{ h.reason }}</td>
              <td class="px-2 py-1.5 text-right"><span class="text-[10px] text-sky-400">看行情</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-if="screenerResult && !screenerLoading && !hitGroups.length" class="text-xs text-zinc-600 py-6 text-center">
      当前方法没有符合条件的股票
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { openStockInDash } from './appNav'

type RegimeId = 'up' | 'range' | 'down'
type StepId = 'select' | 'watch' | 'buy' | 'hold' | 'sell'
type Sel = { kind: 'method'; id: string } | { kind: 'regime'; id: RegimeId } | { kind: 'step'; id: StepId }

const emit = defineEmits<{
  log: [msg: string, level?: string]
  asof: [value: string]
  activated: []
}>()

const FALLBACK_REGIMES = [
  { id: 'up' as const, name: '上升期', hint: '收盘 > MA20 > MA60' },
  { id: 'range' as const, name: '震荡期', hint: '均线纠缠' },
  { id: 'down' as const, name: '下降期', hint: '收盘 < MA20 < MA60' },
]
const FALLBACK_STEPS = [
  { id: 'select' as const, name: '入选', hint: '进池，不是买入' },
  { id: 'watch' as const, name: '观察', hint: '等条件，可剔除' },
  { id: 'buy' as const, name: '买入', hint: '名单 + 阶段 + 条件' },
  { id: 'hold' as const, name: '持有/调仓', hint: '无卖点才走阶梯' },
  { id: 'sell' as const, name: '卖出', hint: '按机制离场' },
]
const FALLBACK_METHODS = [
  { id: 'b-ma5', name: '升势回踩 MA5', regimes: ['up'], steps: ['select', 'watch', 'buy'], buyable: true, lookback_bars: 40, need: 'stock_daily 约 40 根', when: '上升期', rule: '回踩 MA5 且未放量砸破。' },
  { id: 'c-struct', name: '趋势回踩结构止损', regimes: ['up'], steps: ['select', 'buy', 'hold', 'sell'], buyable: true, lookback_bars: 40, need: 'stock_daily 约 40 根', when: '上升期', rule: '缩量回踩 MA10，次日开盘买。' },
  { id: 'ma60-slope', name: 'MA60 斜率转正', regimes: ['up'], steps: ['buy'], buyable: true, lookback_bars: 90, need: 'stock_daily 约 90 根', when: '上升期', rule: '斜率由负转正当天买。' },
  { id: 'ma60-add', name: '贴近 MA60 补仓', regimes: ['up'], steps: ['hold'], buyable: false, lookback_bars: 90, need: 'stock_daily 约 90 根', when: '已持仓', rule: '贴近 MA60 约 2%。' },
  { id: 'doji-sell', name: '高位十字星放量卖', regimes: ['up'], steps: ['sell'], buyable: false, lookback_bars: 90, need: 'stock_daily 约 90 根', when: '主升浪后', rule: '放量十字星卖出。' },
  { id: 'elder', name: '高龄阶梯调仓', regimes: ['up', 'range', 'down'], steps: ['hold'], buyable: false, lookback_bars: 40, need: 'stock_daily 约 21 根', when: '已持仓', rule: '相对约 20 日前涨跌幅落在加减仓带。' },
  { id: 'ma60-shrink', name: 'MA60 缩量折价', regimes: ['range', 'down'], steps: ['select', 'watch', 'buy', 'sell'], buyable: true, lookback_bars: 90, need: 'stock_daily 约 90 根', when: '低于 MA60', rule: '缩量折价。' },
  { id: 'ma20-cash', name: 'MA20 放量兑现', regimes: ['range', 'down'], steps: ['sell'], buyable: false, lookback_bars: 90, need: 'stock_daily 约 90 根', when: '机制 A', rule: '回到 MA20 放量卖。' },
  { id: 'base-watch', name: '缩量筑底观察', regimes: ['down'], steps: ['select', 'watch'], buyable: false, lookback_bars: 40, need: 'stock_daily 约 40 根', when: '下降期', rule: '只观察不买入。' },
  { id: 'crash-buy', name: '深折价 15% 放量买', regimes: ['down'], steps: ['buy', 'sell'], buyable: true, lookback_bars: 90, need: 'stock_daily 约 90 根', when: '深折价', rule: '偏离 MA60≥15% 放量买。' },
  { id: 'long-yin', name: '站上 MA5 长阴卖', regimes: ['down', 'range'], steps: ['sell'], buyable: false, lookback_bars: 40, need: 'stock_daily 约 40 根', when: '反弹后', rule: '站上 MA5 放量长阴。' },
  { id: 'vol-halt', name: '高波动禁止开仓', regimes: ['up', 'range', 'down'], steps: ['watch'], buyable: false, lookback_bars: 40, need: 'stock_daily 约 25 根', when: '波动过高', rule: '禁止新开仓。' },
]

const regimes = ref(FALLBACK_REGIMES)
const steps = ref(FALLBACK_STEPS)
const methods = ref<any[]>(FALLBACK_METHODS)

const REGIME_POS: Record<string, { x: number; y: number }> = {
  up: { x: 24, y: 48 },
  range: { x: 24, y: 214 },
  down: { x: 24, y: 380 },
}
const STEP_POS: Record<string, { x: number; y: number }> = {
  select: { x: 818, y: 28 },
  watch: { x: 818, y: 124 },
  buy: { x: 818, y: 220 },
  hold: { x: 818, y: 316 },
  sell: { x: 818, y: 412 },
}
const BOX = { regime: { w: 168, h: 56 }, method: { w: 196, h: 52 }, step: { w: 158, h: 56 } }

const sel = ref<Sel>({ kind: 'method', id: 'b-ma5' })
const filter = ref<RegimeId | 'all'>('all')
const screenerLoading = ref(false)
const screenerError = ref('')
const screenerResult = ref<any>(null)
const screenerActiveCode = ref('')
const pipelineLoading = ref(false)
const pipelineError = ref('')
const pipelineResult = ref<any>(null)
const screenerAsofPinned = ref(false)
const SCREENER_CACHE_KEY = 'strategy_screener_cache_v3'
const screener = reactive({
  asof: '',
  limit: 60,
  includeMain: true,
  includeStar: true,
  includeChinext: true,
  includeBse: false,
  includeSt: false,
})

const visibleMethods = computed(() => {
  if (filter.value === 'all') return methods.value
  return methods.value.filter((m) => (m.regimes || []).includes(filter.value))
})

const lit = computed(() => relatedIds(sel.value))

const edges = computed(() => {
  const visible = visibleMethods.value
  const onSet = lit.value
  const out: { key: string; from: { x: number; y: number }; to: { x: number; y: number }; on: boolean }[] = []
  for (const m of visible) {
    const mp = methodPos(m.id)
    const midL = { x: mp.x, y: mp.y + BOX.method.h / 2 }
    const midR = { x: mp.x + BOX.method.w, y: mp.y + BOX.method.h / 2 }
    for (const r of m.regimes || []) {
      const rp = REGIME_POS[r]
      if (!rp) continue
      out.push({
        key: `${r}-${m.id}`,
        from: { x: rp.x + BOX.regime.w, y: rp.y + BOX.regime.h / 2 },
        to: midL,
        on: onSet.has(m.id) && onSet.has(r),
      })
    }
    for (const s of m.steps || []) {
      const sp = STEP_POS[s]
      if (!sp) continue
      out.push({
        key: `${m.id}-${s}`,
        from: midR,
        to: { x: sp.x, y: sp.y + BOX.step.h / 2 },
        on: onSet.has(m.id) && onSet.has(s),
      })
    }
  }
  return out
})

const methodsToRun = computed(() => {
  if (sel.value.kind === 'method') return [sel.value.id]
  if (sel.value.kind === 'regime') {
    return methods.value.filter((m) => (m.regimes || []).includes(sel.value.id)).map((m) => m.id)
  }
  return methods.value.filter((m) => (m.steps || []).includes(sel.value.id)).map((m) => m.id)
})

const lookbackNeed = computed(() => {
  const ids = new Set(methodsToRun.value)
  const bars = methods.value.filter((m) => ids.has(m.id)).map((m) => Number(m.lookback_bars || 40))
  return bars.length ? Math.max(...bars) : 40
})

const searchLabel = computed(() => {
  if (sel.value.kind === 'method') return `搜索「${methodName(sel.value.id)}」`
  if (sel.value.kind === 'regime') return `搜索${regimeLabel(sel.value.id)}方法`
  const step = steps.value.find((s) => s.id === sel.value.id)
  return `搜索「${step?.name || sel.value.id}」`
})

const selectedDetail = computed(() => {
  if (sel.value.kind === 'method') {
    const m = methods.value.find((x) => x.id === sel.value.id)
    if (!m) return null
    return { title: m.name, body: `${m.when}。${m.rule}`, need: m.need }
  }
  if (sel.value.kind === 'regime') {
    const r = regimes.value.find((x) => x.id === sel.value.id)
    const list = methods.value.filter((m) => (m.regimes || []).includes(sel.value.id)).map((m) => m.name)
    return { title: `${r?.name}可用方法`, body: list.join('、') || '无', need: `按这些方法取最长窗口 ${lookbackNeed.value} 日` }
  }
  const s = steps.value.find((x) => x.id === sel.value.id)
  const list = methods.value.filter((m) => (m.steps || []).includes(sel.value.id)).map((m) => m.name)
  return { title: `${s?.name}会连到哪些方法`, body: list.join('、') || '无', need: `按这些方法取最长窗口 ${lookbackNeed.value} 日` }
})

const hitGroups = computed(() => {
  const data = screenerResult.value
  if (!data) return []
  const by = data.hits_by_method || {}
  const groups: { id: string; name: string; buyable: boolean; hits: any[] }[] = []
  const order = methodsToRun.value.length ? methodsToRun.value : methods.value.map((m) => m.id)
  for (const id of order) {
    const hits = by[id] || (data.hits || []).filter((h: any) => h.method_id === id)
    if (!hits.length) continue
    const spec = methods.value.find((m) => m.id === id)
    groups.push({
      id,
      name: spec?.name || hits[0]?.label || id,
      buyable: !!(spec?.buyable ?? hits[0]?.buyable),
      hits,
    })
  }
  if (!groups.length && Array.isArray(data.hits) && data.hits.length) {
    groups.push({ id: 'all', name: '命中', buyable: true, hits: data.hits })
  }
  return groups
})

function relatedIds(current: Sel): Set<string> {
  const ids = new Set<string>([current.id])
  if (current.kind === 'method') {
    const m = methods.value.find((x) => x.id === current.id)
    if (!m) return ids
    ;(m.regimes || []).forEach((r: string) => ids.add(r))
    ;(m.steps || []).forEach((s: string) => ids.add(s))
  } else if (current.kind === 'regime') {
    methods.value.filter((m) => (m.regimes || []).includes(current.id)).forEach((m) => {
      ids.add(m.id)
      ;(m.steps || []).forEach((s: string) => ids.add(s))
    })
  } else {
    methods.value.filter((m) => (m.steps || []).includes(current.id)).forEach((m) => {
      ids.add(m.id)
      ;(m.regimes || []).forEach((r: string) => ids.add(r))
    })
  }
  return ids
}

function methodPos(id: string) {
  const i = visibleMethods.value.findIndex((m) => m.id === id)
  const idx = i < 0 ? 0 : i
  return { x: idx % 2 === 0 ? 248 : 508, y: 20 + Math.floor(idx / 2) * 78 }
}

function boxStyle(pos: { x: number; y: number }, size: { w: number; h: number }, active: boolean, dim: boolean) {
  return {
    left: `${(pos.x / 1000) * 100}%`,
    top: `${(pos.y / 500) * 100}%`,
    width: `${(size.w / 1000) * 100}%`,
    height: `${(size.h / 500) * 100}%`,
    borderColor: active ? 'rgba(52,211,153,0.7)' : '#3f3f46',
    background: active ? 'rgba(16,185,129,0.12)' : '#18181b',
    opacity: dim ? 0.35 : 1,
  }
}

function curve(a: { x: number; y: number }, b: { x: number; y: number }) {
  const dx = Math.max(40, (b.x - a.x) * 0.45)
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`
}

function methodName(id: string) {
  return methods.value.find((m) => m.id === id)?.name || id
}

function regimeLabel(id?: string) {
  if (!id) return '-'
  const map: Record<string, string> = { up: '上升', range: '震荡', down: '下降', trend_up: '上升', trend_down: '下降', high_vol: '高波动', unknown: '未知' }
  return map[id] || regimes.value.find((r) => r.id === id)?.name || id
}

function selectMethod(id: string) {
  sel.value = { kind: 'method', id }
}

function selectRegime(id: RegimeId) {
  sel.value = { kind: 'regime', id }
  filter.value = id
}

function selectStep(id: StepId) {
  sel.value = { kind: 'step', id }
}

function persistCache() {
  try {
    const form = { ...screener }
    delete (form as any).asof
    sessionStorage.setItem(SCREENER_CACHE_KEY, JSON.stringify({
      form,
      result: screenerResult.value,
      active: screenerActiveCode.value,
      sel: sel.value,
      filter: filter.value,
    }))
  } catch { /* ignore */ }
}

function restoreCache() {
  try {
    const raw = sessionStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return false
    const data = JSON.parse(raw)
    if (data.form) {
      const { asof: _ignored, ...rest } = data.form
      Object.assign(screener, rest)
    }
    if (data.result) screenerResult.value = data.result
    if (data.active) screenerActiveCode.value = data.active
    if (data.sel) sel.value = data.sel
    if (data.filter) filter.value = data.filter
    return !!data.result
  } catch {
    return false
  }
}

async function loadCatalog() {
  try {
    const res = await fetch('/strategy/screener/methods')
    const data = await res.json().catch(() => ({}))
    if (res.ok && data?.methods?.length) {
      methods.value = data.methods
      if (data.regimes?.length) regimes.value = data.regimes
      if (data.steps?.length) steps.value = data.steps
    }
  } catch { /* 使用本地目录 */ }
}

async function loadScreenerAsof() {
  try {
    const res = await fetch('/strategy/screener/asof')
    const data = await res.json().catch(() => ({}))
    if (data?.asof) {
      const asof = String(data.asof).slice(0, 10)
      screener.asof = asof
      emit('asof', asof)
    }
  } catch { /* 手动日期 */ }
}

async function runScreener() {
  screenerLoading.value = true
  screenerError.value = ''
  screenerResult.value = null
  try {
    const res = await fetch('/strategy/screener/setups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        asof: screener.asof || null,
        include_st: screener.includeSt,
        include_main: screener.includeMain,
        include_star: screener.includeStar,
        include_chinext: screener.includeChinext,
        include_bse: screener.includeBse,
        include_cdr: false,
        methods: methodsToRun.value,
        limit: screener.limit || 60,
        fill: true,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data.detail
      screenerError.value = typeof detail === 'string' ? detail : (detail?.error || `HTTP ${res.status}`)
      emit('log', `[选股] 失败: ${screenerError.value}`, 'error')
      return
    }
    screenerResult.value = data
    if (data.asof) {
      screener.asof = String(data.asof).slice(0, 10)
      emit('asof', screener.asof)
    }
    persistCache()
    const counts = data.hit_count_by_method || {}
    const summary = Object.entries(counts).map(([k, n]) => `${methodName(k)}${n}`).join(' · ')
    emit('log', `[选股] ${data.asof} · ${summary || ('命中' + (data.hit_count ?? 0))} · 窗口${data.lookback}日`, 'ok')
  } catch (e: any) {
    screenerError.value = e?.message || String(e)
    emit('log', `[选股] 异常: ${screenerError.value}`, 'error')
  } finally {
    screenerLoading.value = false
  }
}

async function runPipeline() {
  pipelineLoading.value = true
  pipelineError.value = ''
  pipelineResult.value = null
  try {
    const res = await fetch('/strategy/pipeline/cycle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        asof: screener.asof || null,
        min_score: 70,
        max_n: 50,
        fill: true,
        require_flow_signal: true,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data.detail
      pipelineError.value = typeof detail === 'string' ? detail : (detail?.error || `HTTP ${res.status}`)
      emit('log', `[流水线] 失败: ${pipelineError.value}`, 'error')
      return
    }
    pipelineResult.value = data
    if (data.asof) {
      screener.asof = String(data.asof).slice(0, 10)
      emit('asof', screener.asof)
    }
    emit('log', `[流水线] 观察${data.observe?.count ?? 0} 起飞${data.takeoff?.promoted_count ?? 0} 衰竭${data.exhaustion?.exhausted_count ?? 0}`, 'ok')
  } catch (e: any) {
    pipelineError.value = e?.message || String(e)
    emit('log', `[流水线] 异常: ${pipelineError.value}`, 'error')
  } finally {
    pipelineLoading.value = false
  }
}

function openHit(h: any) {
  if (!h?.ts_code) return
  screenerActiveCode.value = h.ts_code
  persistCache()
  openStockInDash({ ts_code: h.ts_code, name: h.name, industry: h.industry }, 'screener')
  emit('log', `[选股] 打开行情看板 ${h.name || ''} ${h.ts_code}`, 'ok')
}

onMounted(async () => {
  const had = restoreCache()
  if (had) emit('activated')
  await Promise.all([loadCatalog(), loadScreenerAsof()])
})
</script>
