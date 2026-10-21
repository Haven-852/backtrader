<template>
  <div class="h-full flex flex-col bg-zinc-950 overflow-hidden">
    <!-- 顶栏 -->
    <div class="flex-shrink-0 bg-zinc-900 border-b border-zinc-800 px-4 py-2.5 flex items-center gap-3 flex-wrap">
      <div class="text-sm font-semibold text-zinc-200 tracking-wide">期货行情中心</div>
      <div class="text-[11px] text-zinc-500">主连日线 · 自动对齐上一交易日 · A股指数请用「行情看板」</div>
      <div class="flex-1"></div>
      <div v-if="boardAsof" class="text-[11px] text-emerald-400/90 tabular-nums">上一交易日 {{ boardAsof }}</div>
      <div v-if="lastSyncAt" class="text-[10px] text-zinc-600">刷新 {{ lastSyncAt }}</div>
      <label class="flex items-center gap-1.5 text-[11px] text-zinc-400 cursor-pointer select-none">
        <input v-model="autoRefresh" type="checkbox" class="rounded border-zinc-600" />
        自动更新
      </label>
      <select
        v-model.number="refreshMinutes"
        class="bg-zinc-800 border border-zinc-700 rounded text-[11px] text-zinc-300 px-2 py-1 outline-none"
        :disabled="!autoRefresh"
      >
        <option :value="3">每 3 分钟</option>
        <option :value="5">每 5 分钟</option>
        <option :value="10">每 10 分钟</option>
        <option :value="30">每 30 分钟</option>
      </select>
      <button
        @click="loadBoard(true)"
        class="px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-xs text-zinc-200 disabled:opacity-40"
        :disabled="boardLoading"
      >
        <i class="fa-solid fa-rotate mr-1" :class="{ 'animate-spin': boardLoading }"></i>刷新看板
      </button>
    </div>

    <!-- 分类 tabs（对齐行情看板分区） -->
    <div class="flex-shrink-0 bg-zinc-900/80 border-b border-zinc-800 px-3 py-2 flex items-center gap-1 overflow-x-auto scrollbar-thin">
      <button
        v-for="tab in categoryTabs"
        :key="tab.id"
        @click="activeCategory = tab.id"
        :class="[
          'flex-shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
          activeCategory === tab.id
            ? 'bg-blue-600/20 text-blue-400 border border-blue-500/30'
            : 'text-zinc-400 hover:text-white hover:bg-zinc-800 border border-transparent'
        ]"
      >{{ tab.label }}</button>
    </div>

    <div class="flex-1 min-h-0 flex overflow-hidden">
      <!-- 左：分类表格看板 -->
      <div class="flex-1 min-w-0 overflow-auto p-3 space-y-3">
        <div v-if="boardLoading && !visibleSections.length" class="text-center py-16 text-zinc-500 text-sm">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i>加载期货主连...
        </div>
        <div v-else-if="boardError" class="text-center py-12 text-amber-400/90 text-sm">{{ boardError }}</div>

        <div
          :class="activeCategory === '全部' ? 'grid grid-cols-1 xl:grid-cols-2 gap-3' : 'space-y-3'"
        >
          <section
            v-for="sec in visibleSections"
            :key="sec.category"
            class="bg-zinc-900/70 border border-zinc-800 rounded-xl overflow-hidden"
          >
            <div class="px-3 py-2 border-b border-zinc-800 flex items-center justify-between">
              <div class="text-xs font-semibold text-zinc-200">{{ sec.category }}</div>
              <div class="text-[10px] text-zinc-500">{{ sec.ready }}/{{ sec.count }} 有行情</div>
            </div>
            <div class="overflow-auto max-h-[28rem]">
              <table class="w-full text-[11px]">
                <thead class="sticky top-0 bg-zinc-900 z-10">
                  <tr class="text-zinc-500 border-b border-zinc-800">
                    <th class="text-left font-medium py-1.5 px-2">名称</th>
                    <th class="text-right font-medium py-1.5 px-2">现价</th>
                    <th class="text-right font-medium py-1.5 px-2">涨幅</th>
                    <th class="text-right font-medium py-1.5 px-2">涨跌</th>
                    <th class="text-right font-medium py-1.5 px-2 hidden md:table-cell">成交量</th>
                    <th class="text-right font-medium py-1.5 px-2 hidden lg:table-cell">持仓</th>
                    <th class="text-right font-medium py-1.5 px-2 hidden lg:table-cell">日增仓</th>
                    <th class="text-right font-medium py-1.5 px-2 hidden xl:table-cell">振幅</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in sec.items"
                    :key="row.ts_code"
                    @click="selectContract(row)"
                    :class="[
                      'border-b border-zinc-800/40 cursor-pointer transition-colors',
                      selected?.ts_code === row.ts_code ? 'bg-blue-600/15' : 'hover:bg-zinc-800/40',
                      row.missing ? 'opacity-50' : ''
                    ]"
                  >
                    <td class="py-1.5 px-2">
                      <div class="text-zinc-100 font-medium truncate max-w-[9rem]">{{ row.name }}</div>
                      <div class="font-mono text-[10px] text-zinc-500">{{ row.ts_code }}</div>
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums text-zinc-200">
                      {{ row.missing ? '--' : fmtPrice(row.close) }}
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums" :class="chgClass(row.pct_chg)">
                      {{ row.missing ? '--' : fmtPct(row.pct_chg) }}
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums" :class="chgClass(row.change)">
                      {{ row.missing ? '--' : fmtSigned(row.change) }}
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums text-zinc-400 hidden md:table-cell">
                      {{ row.missing ? '--' : fmtVol(row.vol) }}
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums text-zinc-400 hidden lg:table-cell">
                      {{ row.missing ? '--' : fmtVol(row.oi) }}
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums hidden lg:table-cell" :class="chgClass(row.oi_chg)">
                      {{ row.missing ? '--' : fmtSigned(row.oi_chg, 0) }}
                    </td>
                    <td class="py-1.5 px-2 text-right tabular-nums text-zinc-400 hidden xl:table-cell">
                      {{ row.missing ? '--' : (Number(row.amplitude || 0).toFixed(2) + '%') }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      <!-- 右：K线详情 -->
      <aside class="w-[26rem] xl:w-[30rem] flex-shrink-0 border-l border-zinc-800 bg-zinc-900/40 flex flex-col min-h-0">
        <div class="px-3 py-2.5 border-b border-zinc-800 flex items-center gap-2 flex-wrap">
          <div class="min-w-0 flex-1">
            <div class="text-sm text-zinc-100 font-semibold truncate">
              {{ selected?.name || '选择左侧合约' }}
            </div>
            <div class="text-[10px] font-mono text-zinc-500">{{ selected?.ts_code || '—' }}</div>
          </div>
          <div class="flex bg-zinc-800 rounded-lg p-0.5">
            <button
              v-for="tf in timeframes"
              :key="tf.id"
              @click="freq = tf.id; loadKline()"
              :disabled="!selected"
              :class="[
                'px-2.5 py-1 text-[11px] rounded-md transition-all disabled:opacity-40',
                freq === tf.id ? 'bg-blue-600 text-white' : 'text-zinc-400 hover:text-white'
              ]"
            >{{ tf.label }}</button>
          </div>
        </div>

        <div class="px-3 py-2 flex items-center gap-2 border-b border-zinc-800/80">
          <select
            v-model.number="klineLimit"
            @change="loadKline()"
            class="bg-zinc-800 border border-zinc-700 rounded text-[11px] text-white px-2 py-1 outline-none"
          >
            <option :value="60">60</option>
            <option :value="120">120</option>
            <option :value="250">250</option>
            <option :value="500">500</option>
          </select>
          <button
            @click="loadKline()"
            :disabled="!selected || klineLoading"
            class="px-2.5 py-1 rounded-md bg-zinc-800 hover:bg-zinc-700 text-[11px] text-zinc-200 disabled:opacity-40"
          >
            <i class="fa-solid fa-rotate mr-1" :class="{ 'animate-spin': klineLoading }"></i>刷新K线
          </button>
          <span v-if="klineSource" class="text-[10px] text-zinc-600 ml-auto">{{ klineSource }}</span>
        </div>

        <div class="flex-1 min-h-0 relative px-1 pt-1">
          <div v-if="!selected" class="absolute inset-0 flex items-center justify-center text-zinc-600 text-xs">
            点击左侧主连查看日 / 周 / 月线
          </div>
          <div v-else-if="klineLoading" class="absolute inset-0 flex items-center justify-center text-zinc-500 text-xs z-10">
            <i class="fa-solid fa-spinner animate-spin mr-2"></i>加载K线...
          </div>
          <div v-else-if="klineError" class="absolute inset-0 flex items-center justify-center text-amber-400/90 text-xs px-4 text-center">
            {{ klineError }}
          </div>
          <div ref="chartEl" class="w-full h-full min-h-[280px]"></div>
        </div>

        <div v-if="klineStats" class="flex-shrink-0 border-t border-zinc-800 p-3 grid grid-cols-2 gap-2 text-[11px]">
          <div class="bg-zinc-950/60 rounded-lg px-2.5 py-2">
            <div class="text-zinc-500">区间</div>
            <div class="text-zinc-300 tabular-nums">{{ klineStats.period_start }} → {{ klineStats.period_end }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg px-2.5 py-2">
            <div class="text-zinc-500">区间涨跌</div>
            <div class="tabular-nums font-semibold" :class="chgClass(klineStats.change_pct)">
              {{ fmtPct(klineStats.change_pct) }}
            </div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg px-2.5 py-2">
            <div class="text-zinc-500">最新 / 根数</div>
            <div class="text-zinc-200 tabular-nums">{{ fmtPrice(klineStats.last_close) }} · {{ klineStats.bar_count }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg px-2.5 py-2">
            <div class="text-zinc-500">最高 / 最低</div>
            <div class="tabular-nums"><span class="text-red-400">{{ fmtPrice(klineStats.high_max) }}</span>
              / <span class="text-emerald-400">{{ fmtPrice(klineStats.low_min) }}</span></div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'

defineOptions({ name: 'MarketDashView' })

type FutRow = {
  ts_code: string
  name: string
  trade_date?: string
  close?: number
  settle?: number
  change?: number
  pct_chg?: number
  vol?: number
  amount?: number
  oi?: number
  oi_chg?: number
  amplitude?: number
  missing?: boolean
  source?: string
}

type Section = {
  category: string
  count: number
  ready: number
  items: FutRow[]
}

const boardLoading = ref(false)
const boardError = ref('')
const boardAsof = ref('')
const sections = ref<Section[]>([])
const activeCategory = ref('全部')
const autoRefresh = ref(true)
const refreshMinutes = ref(5)
const lastSyncAt = ref('')
const syncNote = ref('')
let refreshTimer: number | null = null

const categoryTabs = computed(() => {
  const cats = sections.value.map((s) => ({ id: s.category, label: s.category }))
  return [{ id: '全部', label: '全部主连' }, ...cats]
})

const visibleSections = computed(() => {
  if (activeCategory.value === '全部') return sections.value
  return sections.value.filter((s) => s.category === activeCategory.value)
})

const selected = ref<FutRow | null>(null)
const freq = ref<'daily' | 'weekly' | 'monthly'>('daily')
const timeframes = [
  { id: 'daily' as const, label: '日线' },
  { id: 'weekly' as const, label: '周线' },
  { id: 'monthly' as const, label: '月线' },
]
const klineLimit = ref(120)
const klineLoading = ref(false)
const klineError = ref('')
const klineSource = ref('')
const klineStats = ref<any>(null)
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

function chgClass(v: any) {
  const n = Number(v || 0)
  return n > 0 ? 'text-red-400' : n < 0 ? 'text-emerald-400' : 'text-zinc-400'
}
function fmtPrice(v: any) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  if (Math.abs(n) >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  return n.toFixed(n < 10 ? 3 : 2)
}
function fmtPct(v: any) {
  const n = Number(v || 0)
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}
function fmtSigned(v: any, digits = 2) {
  const n = Number(v || 0)
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`
}
function fmtVol(v: any) {
  const n = Number(v || 0)
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return Math.round(n).toLocaleString()
}
function fmtClock(d = new Date()) {
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

async function loadBoard(force = false) {
  boardLoading.value = true
  boardError.value = ''
  try {
    // board-live：自动对齐上一交易日并补数入库
    const res = await axios.get('/market/futures/board-live', {
      params: {
        force: force ? true : false,
      },
      timeout: 240000,
    })
    sections.value = res.data.sections || []
    boardAsof.value = res.data.prev_trade_date || res.data.asof || ''
    syncNote.value = res.data.note || ''
    lastSyncAt.value = fmtClock()
    if (!selected.value) {
      const first = sections.value.flatMap((s) => s.items).find((i) => !i.missing)
      if (first) await selectContract(first)
    } else {
      const hit = sections.value.flatMap((s) => s.items).find((i) => i.ts_code === selected.value?.ts_code)
      if (hit) selected.value = hit
    }
  } catch (e: any) {
    boardError.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    boardLoading.value = false
  }
}

function clearRefreshTimer() {
  if (refreshTimer != null) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function setupAutoRefresh() {
  clearRefreshTimer()
  if (!autoRefresh.value) return
  const ms = Math.max(1, Number(refreshMinutes.value) || 5) * 60 * 1000
  refreshTimer = window.setInterval(() => {
    if (!boardLoading.value) loadBoard(false)
  }, ms)
}

async function selectContract(row: FutRow) {
  selected.value = row
  await loadKline()
}

async function loadKline() {
  if (!selected.value?.ts_code) return
  klineLoading.value = true
  klineError.value = ''
  try {
    const res = await axios.get('/market/futures/kline', {
      params: {
        ts_code: selected.value.ts_code,
        freq: freq.value,
        limit: klineLimit.value,
      },
      timeout: 120000,
    })
    klineSource.value = res.data.source || ''
    klineStats.value = res.data.stats || null
    await nextTick()
    renderChart(res.data.bars || [], res.data.name || selected.value.name)
  } catch (e: any) {
    klineError.value = e?.response?.data?.detail || e.message || 'K线失败'
    klineStats.value = null
    chart?.clear()
  } finally {
    klineLoading.value = false
  }
}

function ensureChart() {
  if (!chartEl.value) return null
  if (!chart) {
    chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  }
  return chart
}

function renderChart(bars: any[], title: string) {
  const c = ensureChart()
  if (!c) return
  if (!bars.length) {
    c.clear()
    return
  }
  const dates = bars.map((b) => b.trade_date)
  const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high])
  const vols = bars.map((b) => b.vol)
  const up = bars.map((b) => (b.close >= b.open ? b.vol : 0))
  const down = bars.map((b) => (b.close < b.open ? b.vol : 0))

  c.setOption(
    {
      backgroundColor: 'transparent',
      animation: false,
      title: {
        text: title,
        left: 8,
        top: 4,
        textStyle: { color: '#a1a1aa', fontSize: 12, fontWeight: 500 },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(24,24,27,0.95)',
        borderColor: '#3f3f46',
        textStyle: { color: '#e4e4e7', fontSize: 11 },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 48, right: 16, top: 36, height: '58%' },
        { left: 48, right: 16, top: '78%', height: '14%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#3f3f46' } },
          axisLabel: { show: false },
          splitLine: { show: false },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: '#3f3f46' } },
          axisLabel: { color: '#71717a', fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          axisLine: { show: false },
          axisLabel: { color: '#71717a', fontSize: 10 },
          splitLine: { lineStyle: { color: '#27272a' } },
        },
        {
          scale: true,
          gridIndex: 1,
          axisLabel: { show: false },
          axisLine: { show: false },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 40, end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          bottom: 4,
          height: 14,
          borderColor: '#3f3f46',
          textStyle: { color: '#71717a', fontSize: 10 },
          fillerColor: 'rgba(59,130,246,0.15)',
          handleStyle: { color: '#52525b' },
        },
      ],
      series: [
        {
          name: 'K',
          type: 'candlestick',
          data: ohlc,
          itemStyle: {
            color: '#f87171',
            color0: '#34d399',
            borderColor: '#f87171',
            borderColor0: '#34d399',
          },
        },
        {
          name: 'VolUp',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: up,
          itemStyle: { color: 'rgba(248,113,113,0.45)' },
          stack: 'vol',
        },
        {
          name: 'VolDown',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: down,
          itemStyle: { color: 'rgba(52,211,153,0.45)' },
          stack: 'vol',
        },
      ],
    },
    true
  )
}

function onResize() {
  chart?.resize()
}

watch(activeCategory, () => {
  // 仅前端过滤，不重复拉数
})

watch([autoRefresh, refreshMinutes], () => {
  setupAutoRefresh()
})

onMounted(async () => {
  window.addEventListener('resize', onResize)
  await loadBoard(false)
  setupAutoRefresh()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  clearRefreshTimer()
  chart?.dispose()
  chart = null
})
</script>
