<template>
  <div class="h-full flex flex-col bg-zinc-950">
    <!-- ===== 顶部指数栏 (同花顺风格) ===== -->
    <div class="flex-shrink-0 bg-zinc-900 border-b border-zinc-800">
      <div class="flex items-stretch overflow-x-auto scrollbar-thin">
        <div
          v-for="idx in indices"
          :key="idx.ts_code"
          @click="selectIndex(idx)"
          :class="[
            'flex-shrink-0 cursor-pointer px-4 py-2.5 min-w-[140px] border-r border-zinc-800 transition-colors hover:bg-zinc-800/50',
            selectedTab === 'index' && currentIndex?.ts_code === idx.ts_code
              ? 'bg-zinc-800/80 border-b-2 border-b-white'
              : 'border-b-2 border-b-transparent'
          ]"
        >
          <div class="text-xs text-zinc-400 mb-0.5">{{ idx.short_name }}</div>
          <div class="flex items-baseline gap-2">
            <span
              :class="idx.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'"
              class="text-lg font-bold tabular-nums"
            >
              {{ formatPrice(idx.close) }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 mt-0.5">
            <span
              :class="idx.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'"
              class="text-xs tabular-nums"
            >
              {{ idx.pct_chg >= 0 ? '+' : '' }}{{ idx.pct_chg.toFixed(2) }}%
            </span>
            <span
              :class="idx.pct_chg >= 0 ? 'text-red-400/70' : 'text-green-400/70'"
              class="text-xs tabular-nums"
            >
              {{ idx.change >= 0 ? '+' : '' }}{{ idx.change.toFixed(2) }}
            </span>
          </div>
        </div>

        <!-- 指数加载指示 -->
        <div v-if="!indices.length" class="flex-shrink-0 flex items-center px-4 text-xs text-zinc-500">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i> 加载指数...
        </div>
      </div>
    </div>

    <!-- ===== 主工具栏 ===== -->
    <div class="flex items-center gap-3 px-4 py-2.5 bg-zinc-900/70 border-b border-zinc-800 flex-wrap flex-shrink-0">
      <!-- 股票搜索 -->
      <div class="relative flex-1 min-w-[200px] max-w-sm">
        <div class="relative">
          <input
            v-model="searchText"
            @input="onSearchInput"
            @focus="showDropdown = true"
            placeholder="搜索股票代码或名称..."
            class="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-lg pl-9 pr-3 py-1.5 text-sm outline-none transition-colors"
          />
          <i class="fa-solid fa-search absolute left-3 top-2 text-zinc-500 text-xs"></i>
        </div>
        <div
          v-if="showDropdown && searchResults.length > 0"
          class="absolute top-full mt-1 left-0 right-0 bg-zinc-800 border border-zinc-700 rounded-xl shadow-2xl z-50 max-h-80 overflow-auto"
        >
          <div
            v-for="stock in searchResults"
            :key="stock.ts_code"
            @click="selectStock(stock)"
            class="flex items-center justify-between px-4 py-2.5 hover:bg-zinc-700 cursor-pointer transition-colors border-b border-zinc-700/50 last:border-0"
          >
            <div>
              <div class="text-sm font-medium">{{ stock.name }}</div>
              <div class="text-xs text-zinc-400">{{ stock.ts_code }}</div>
            </div>
            <div class="text-xs text-zinc-500">{{ stock.industry }}</div>
          </div>
        </div>
      </div>

      <!-- 周期选择 -->
      <div class="flex gap-0.5 bg-zinc-800 rounded-lg p-0.5">
        <button
          v-for="tf in quickTimeframes"
          :key="tf.id"
          @click="timeframe = tf.id; loadData()"
          :class="timeframe === tf.id
            ? 'bg-blue-600 text-white shadow'
            : 'text-zinc-400 hover:text-white hover:bg-zinc-700'"
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
        >
          {{ tf.name }}
        </button>
      </div>

      <!-- 技术指标 -->
      <div class="flex gap-1">
        <button
          v-for="ind in indicators"
          :key="ind.id"
          @click="toggleIndicator(ind.id)"
          :class="activeIndicators.includes(ind.id)
            ? 'bg-emerald-600 text-white'
            : 'bg-zinc-800 text-zinc-400 hover:text-white'"
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
        >
          {{ ind.id.toUpperCase() }}
        </button>
      </div>

      <!-- 刷新 -->
      <button
        @click="loadData()"
        class="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-xs transition-colors flex items-center gap-1.5"
      >
        <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i>
      </button>
    </div>

    <!-- ===== 主内容区 ===== -->
    <div class="flex-1 flex min-h-0">
      <!-- 图表区域 -->
      <div class="flex-1 flex flex-col min-w-0">
        <!-- 标题栏 -->
        <div class="flex items-center gap-3 px-4 py-2 bg-zinc-900/50 border-b border-zinc-800 flex-shrink-0">
          <template v-if="selectedTab === 'index' && currentIndex">
            <span class="text-base font-bold">{{ currentIndex.name }}</span>
            <span class="text-xs text-zinc-500">{{ currentIndex.ts_code }}</span>
            <span class="text-xs text-zinc-600">{{ currentIndex.trade_date }}</span>
            <div class="ml-auto flex items-center gap-4 text-sm">
              <span class="text-zinc-500">最新</span>
              <span :class="currentIndex.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold tabular-nums">
                {{ formatPrice(currentIndex.close) }}
              </span>
              <span :class="currentIndex.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
                {{ currentIndex.pct_chg >= 0 ? '+' : '' }}{{ currentIndex.pct_chg.toFixed(2) }}%
              </span>
              <span :class="(currentIndex.pct_chg >= 0 ? 'text-red-400/70' : 'text-green-400/70')" class="tabular-nums">
                {{ currentIndex.change >= 0 ? '+' : '' }}{{ currentIndex.change.toFixed(2) }}
              </span>
            </div>
          </template>
          <template v-else-if="selectedTab === 'stock' && currentStock">
            <span class="text-base font-bold">{{ currentStock.name }}</span>
            <span class="text-xs text-zinc-500">{{ currentStock.ts_code }}</span>
            <span class="text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">{{ currentStock.industry }}</span>
            <div v-if="latestBar" class="ml-auto flex items-center gap-4 text-sm">
              <span class="text-zinc-500">最新</span>
              <span :class="latestBar.close >= latestBar.open ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold tabular-nums">
                {{ latestBar.close }}
              </span>
              <span v-if="changePercent !== null" :class="changePercent >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
                {{ changePercent >= 0 ? '+' : '' }}{{ changePercent }}%
              </span>
            </div>
          </template>
        </div>

        <!-- 加载态 -->
        <div v-if="loading && !chartData.bars.length" class="flex-1 flex items-center justify-center">
          <div class="flex flex-col items-center gap-3">
            <i class="fa-solid fa-spinner animate-spin text-3xl text-blue-400"></i>
            <span class="text-zinc-400 text-sm">加载K线数据...</span>
          </div>
        </div>

        <!-- 初始态（无选中） -->
        <div v-else-if="!loading && !chartData.bars.length && !currentIndex && !currentStock" class="flex-1 flex items-center justify-center">
          <div class="text-center text-zinc-500">
            <i class="fa-solid fa-chart-line text-5xl mb-4 block opacity-40"></i>
            <p class="text-lg mb-2">实时行情看板</p>
            <p class="text-sm">点击上方指数查看大盘走势，或搜索个股代码</p>
          </div>
        </div>

        <!-- 图表 -->
        <div v-else class="flex-1 flex flex-col min-h-0">
          <div ref="mainChart" class="flex-1 min-h-0"></div>
          <div v-if="showVolume || !currentStock" ref="volumeChart" class="h-32 border-t border-zinc-800 flex-shrink-0"></div>
          <div v-if="showMACD" ref="macdChart" class="h-32 border-t border-zinc-800 flex-shrink-0"></div>
          <div v-if="showRSI" ref="rsiChart" class="h-32 border-t border-zinc-800 flex-shrink-0"></div>
        </div>
      </div>

      <!-- 右侧信息面板 -->
      <div v-if="(currentIndex || currentStock) && chartData.bars.length" class="w-72 bg-zinc-900 border-l border-zinc-800 p-4 overflow-auto hidden xl:block flex-shrink-0">
        <!-- 最新行情 -->
        <h3 class="text-xs font-semibold mb-3 text-zinc-400 uppercase tracking-wide">最新行情</h3>
        <div class="space-y-1.5 text-sm">
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">开盘</span>
            <span class="tabular-nums">{{ latestBar?.open ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">最高</span>
            <span class="text-red-400 tabular-nums">{{ latestBar?.high ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">最低</span>
            <span class="text-green-400 tabular-nums">{{ latestBar?.low ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">昨收</span>
            <span class="tabular-nums">{{ currentIndex?.pre_close ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">成交量</span>
            <span class="tabular-nums">{{ formatVolume(latestBar?.vol) }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">成交额</span>
            <span class="tabular-nums">{{ formatAmount(latestBar?.amount || currentIndex?.amount) }}</span>
          </div>
        </div>

        <!-- 涨跌幅 -->
        <h3 class="text-xs font-semibold mt-5 mb-3 text-zinc-400 uppercase tracking-wide">涨跌</h3>
        <div class="space-y-1.5 text-sm">
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">涨跌额</span>
            <span :class="(currentIndex?.change ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
              {{ currentIndex?.change ?? '-' }}
            </span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">涨跌幅</span>
            <span :class="(currentIndex?.pct_chg ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
              {{ currentIndex?.pct_chg != null ? (currentIndex.pct_chg >= 0 ? '+' : '') + currentIndex.pct_chg.toFixed(2) + '%' : '-' }}
            </span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">振幅</span>
            <span class="tabular-nums">{{ amplitude }}%</span>
          </div>
        </div>

        <!-- 数据统计 -->
        <h3 class="text-xs font-semibold mt-5 mb-3 text-zinc-400 uppercase tracking-wide">统计</h3>
        <div class="space-y-1.5 text-sm">
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">数据条数</span>
            <span class="tabular-nums">{{ chartData.count }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">周期</span>
            <span>{{ timeframeName }}</span>
          </div>
        </div>

        <!-- 已启用指标 -->
        <h3 class="text-xs font-semibold mt-5 mb-2 text-zinc-400 uppercase tracking-wide">技术指标</h3>
        <div v-if="activeIndicators.length" class="flex flex-wrap gap-1.5">
          <span v-for="ind in activeIndicators" :key="ind"
            class="px-2 py-1 bg-emerald-600/20 text-emerald-400 rounded text-xs"
          >{{ ind.toUpperCase() }}</span>
        </div>
        <div v-else class="text-xs text-zinc-500">未选择</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import axios from 'axios'

// ─── 状态 ────────────────────────────────────────────

const searchText = ref('')
const searchResults = ref<any[]>([])
const showDropdown = ref(false)
const selectedTab = ref<'index' | 'stock'>('index')
const currentIndex = ref<any>(null)
const currentStock = ref<any>(null)
const currentTsCode = ref<string>('000001.SH')
const timeframe = ref('day')
const activeIndicators = ref<string[]>(['ma'])
const loading = ref(false)
const indices = ref<any[]>([])

const chartData = ref<any>({ bars: [], indicators: {}, count: 0 })
const indicators = ref<any[]>([])

const mainChart = ref<HTMLDivElement>()
const volumeChart = ref<HTMLDivElement>()
const macdChart = ref<HTMLDivElement>()
const rsiChart = ref<HTMLDivElement>()

let mainChartInst: any = null
let volumeChartInst: any = null
let macdChartInst: any = null
let rsiChartInst: any = null
let searchTimer: any = null
let resizeHandler: any = null

// ─── 计算属性 ────────────────────────────────────────

const timeframeName = computed(() => {
  const map: Record<string, string> = {
    '1m': '1分钟', '5m': '5分钟', '15m': '15分钟', '30m': '30分钟', '60m': '60分钟',
    'day': '日线', 'week': '周线', 'month': '月线',
  }
  return map[timeframe.value] || timeframe.value
})

const quickTimeframes = [
  { id: 'day', name: '日K' },
  { id: 'week', name: '周K' },
  { id: 'month', name: '月K' },
  { id: '60m', name: '60分' },
  { id: '30m', name: '30分' },
]

const latestBar = computed(() => {
  const bars = chartData.value.bars
  return bars.length ? bars[bars.length - 1] : null
})

const changePercent = computed(() => {
  if (!latestBar.value) return null
  const close = latestBar.value.close
  const open = latestBar.value.open
  if (!open || open === 0) return null
  return ((close - open) / open * 100).toFixed(2)
})

const amplitude = computed(() => {
  if (!latestBar.value) return '-'
  const h = latestBar.value.high
  const l = latestBar.value.low
  const pre = currentIndex.value?.pre_close || latestBar.value.open
  if (!pre || pre === 0) return '-'
  return ((h - l) / pre * 100).toFixed(2)
})

const showVolume = computed(() => activeIndicators.value.includes('volume') || selectedTab.value === 'index')
const showMACD = computed(() => activeIndicators.value.includes('macd'))
const showRSI = computed(() => activeIndicators.value.includes('rsi'))

// ─── 数据加载 ────────────────────────────────────────

async function loadIndices() {
  try {
    const res = await axios.get('/dash/indices')
    indices.value = res.data.indices || []
    if (indices.value.length > 0 && !currentIndex.value) {
      currentIndex.value = indices.value[0]
      currentTsCode.value = indices.value[0].ts_code
      loadData()
    }
  } catch (e) {
    console.warn('获取指数列表失败:', e)
  }
}

async function loadIndicators() {
  try {
    const res = await axios.get('/dash/indicators')
    indicators.value = res.data.indicators || []
  } catch (e) {
    console.warn('获取指标列表失败:', e)
  }
}

async function loadData() {
  if (!currentTsCode.value) return
  loading.value = true
  try {
    const params: any = {
      ts_code: currentTsCode.value,
      timeframe: timeframe.value,
      limit: 300,
    }
    if (activeIndicators.value.length) {
      params.indicators = activeIndicators.value.join(',')
    }

    // 指数和股票用同一个 kline 接口（dash_service 已兼容 index_daily 表）
    const res = await axios.get('/dash/kline', { params })
    chartData.value = res.data
    await nextTick()
    renderCharts()
  } catch (e: any) {
    console.error('加载K线失败:', e)
  } finally {
    loading.value = false
  }
}

function selectIndex(idx: any) {
  selectedTab.value = 'index'
  currentIndex.value = idx
  currentStock.value = null
  currentTsCode.value = idx.ts_code
  searchText.value = ''
  loadData()
}

function selectStock(stock: any) {
  selectedTab.value = 'stock'
  currentStock.value = stock
  currentIndex.value = null
  currentTsCode.value = stock.ts_code
  searchText.value = stock.name
  showDropdown.value = false
  searchResults.value = []
  loadData()
}

async function onSearchInput() {
  clearTimeout(searchTimer)
  if (searchText.value.length < 1) {
    searchResults.value = []
    showDropdown.value = false
    return
  }
  searchTimer = setTimeout(async () => {
    try {
      const res = await axios.get('/dash/search', { params: { q: searchText.value, limit: 10 } })
      searchResults.value = res.data.results || []
      showDropdown.value = true
    } catch (e) {
      console.warn('搜索失败:', e)
    }
  }, 300)
}

function toggleIndicator(id: string) {
  const idx = activeIndicators.value.indexOf(id)
  if (idx >= 0) {
    activeIndicators.value.splice(idx, 1)
  } else {
    activeIndicators.value.push(id)
  }
  loadData()
}

function formatPrice(v: number | null): string {
  if (v == null) return '-'
  return v.toFixed(2)
}

function formatVolume(vol: number | null): string {
  if (!vol) return '-'
  if (vol >= 100000000) return (vol / 100000000).toFixed(2) + '亿'
  if (vol >= 10000) return (vol / 10000).toFixed(1) + '万'
  return String(vol)
}

function formatAmount(amount: number | null): string {
  if (!amount) return '-'
  if (amount >= 1e8) return (amount / 1e8).toFixed(2) + '亿'
  if (amount >= 1e4) return (amount / 1e4).toFixed(1) + '万'
  return String(amount)
}

// ─── 图表渲染（同花顺红涨绿跌配色） ──────────────────

function renderCharts() {
  renderMainChart()
  if (showVolume.value) renderVolumeChart()
  if (showMACD.value) renderMACDChart()
  if (showRSI.value) renderRSIChart()
}

function getThemeColors() {
  return {
    bg: '#18181b',
    text: '#a1a1aa',
    border: '#27272a',
    up: '#ef4444',      // 红涨（中国习惯）
    down: '#22c55e',    // 绿跌
    ma5: '#fbbf24',
    ma10: '#60a5fa',
    ma20: '#c084fc',
    ma60: '#fb923c',
    grid: '#27272a',
  }
}

function renderMainChart() {
  if (!mainChart.value) return
  const c = getThemeColors()

  if (!mainChartInst) {
    mainChartInst = (window as any).echarts.init(mainChart.value, 'dark')
  }

  const bars = chartData.value.bars
  const dates = bars.map((b: any) => b.time.substring(0, 10))
  const ohlc = bars.map((b: any) => [b.open, b.close, b.low, b.high])

  const series: any[] = [
    {
      name: 'K线',
      type: 'candlestick',
      data: ohlc,
      itemStyle: {
        color: c.up,
        color0: c.down,
        borderColor: c.up,
        borderColor0: c.down,
      },
    },
  ]

  // MA
  const ind = chartData.value.indicators
  if (ind.ma) {
    const maColors: Record<string, string> = { MA5: c.ma5, MA10: c.ma10, MA20: c.ma20, MA60: c.ma60 }
    for (const [key, values] of Object.entries(ind.ma)) {
      series.push({
        name: key,
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.5, color: (maColors as any)[key] || '#888' },
      })
    }
  }

  // EMA
  if (ind.ema) {
    for (const [key, values] of Object.entries(ind.ema)) {
      series.push({
        name: key,
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1, type: 'dashed' },
      })
    }
  }

  // BOLL
  if (ind.boll_BOLL_UPPER) {
    series.push(
      { name: 'BOLL上轨', type: 'line', data: ind.boll_BOLL_UPPER, smooth: true, symbol: 'none',
        lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
      { name: 'BOLL中轨', type: 'line', data: ind.boll_BOLL_MID, smooth: true, symbol: 'none',
        lineStyle: { width: 1.5, color: '#818cf8' } },
      { name: 'BOLL下轨', type: 'line', data: ind.boll_BOLL_LOWER, smooth: true, symbol: 'none',
        lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
    )
  }

  mainChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 20, bottom: showVolume.value ? 5 : 40 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: c.border } },
      axisLabel: { color: c.text, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { lineStyle: { color: c.border } },
      axisLabel: { color: c.text, fontSize: 10, formatter: (v: number) => v.toFixed(0) },
      splitLine: { lineStyle: { color: c.grid } },
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    series,
  }, true)
}

function renderVolumeChart() {
  if (!volumeChart.value) return
  const c = getThemeColors()

  if (!volumeChartInst) {
    volumeChartInst = (window as any).echarts.init(volumeChart.value, 'dark')
  }

  const bars = chartData.value.bars
  const dates = bars.map((b: any) => b.time.substring(0, 10))
  const volumes = bars.map((b: any) => b.vol)
  const colors = bars.map((b: any) => b.close >= b.open ? c.up : c.down)

  const series: any[] = [{
    name: '成交量',
    type: 'bar',
    data: volumes.map((v: number, i: number) => ({ value: v, itemStyle: { color: colors[i] } })),
  }]

  const ind = chartData.value.indicators
  if (ind.VOL_MA5) {
    series.push({ name: 'VOL_MA5', type: 'line', data: ind.VOL_MA5, smooth: true,
      symbol: 'none', lineStyle: { width: 1, color: c.ma5 } })
  }
  if (ind.VOL_MA10) {
    series.push({ name: 'VOL_MA10', type: 'line', data: ind.VOL_MA10, smooth: true,
      symbol: 'none', lineStyle: { width: 1, color: c.ma10 } })
  }

  volumeChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 5, bottom: 5 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: {
      type: 'value',
      axisLabel: { color: c.text, fontSize: 9, formatter: (v: number) => v >= 100000000 ? (v / 100000000).toFixed(1) + '亿' : v >= 10000 ? (v / 10000).toFixed(0) + '万' : v },
      splitLine: { lineStyle: { color: c.grid } },
    },
    series,
  }, true)
}

function renderMACDChart() {
  if (!macdChart.value) return
  const c = getThemeColors()
  if (!macdChartInst) macdChartInst = (window as any).echarts.init(macdChart.value, 'dark')

  const ind = chartData.value.indicators
  const dif = ind.macd_DIF || []
  const dea = ind.macd_DEA || []
  const macdHist = ind.macd_MACD || []
  const histColors = macdHist.map((v: number) => v >= 0 ? c.up : c.down)

  macdChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 10, bottom: 5 },
    xAxis: { type: 'category', data: dif.map((_: any, i: number) => i), axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    series: [
      { name: 'MACD', type: 'bar', data: macdHist.map((v: number, i: number) => ({ value: v, itemStyle: { color: histColors[i] } })) },
      { name: 'DIF', type: 'line', data: dif, symbol: 'none', lineStyle: { width: 1, color: '#fbbf24' } },
      { name: 'DEA', type: 'line', data: dea, symbol: 'none', lineStyle: { width: 1, color: '#60a5fa' } },
    ],
  }, true)
}

function renderRSIChart() {
  if (!rsiChart.value) return
  const c = getThemeColors()
  if (!rsiChartInst) rsiChartInst = (window as any).echarts.init(rsiChart.value, 'dark')

  const rsiData = chartData.value.indicators.rsi || []
  rsiChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 10, bottom: 5 },
    xAxis: { type: 'category', data: rsiData.map((_: any, i: number) => i), axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    series: [{
      name: 'RSI', type: 'line', data: rsiData, symbol: 'none', lineStyle: { width: 1.5, color: '#818cf8' },
      markLine: { silent: true, data: [
        { yAxis: 70, lineStyle: { color: c.up, width: 1, type: 'dashed' } },
        { yAxis: 30, lineStyle: { color: c.down, width: 1, type: 'dashed' } },
      ] },
    }],
  }, true)
}

function handleResize() {
  mainChartInst?.resize()
  volumeChartInst?.resize()
  macdChartInst?.resize()
  rsiChartInst?.resize()
}

// ─── 生命周期 ────────────────────────────────────────

onMounted(async () => {
  await Promise.all([loadIndices(), loadIndicators()])
  resizeHandler = () => handleResize()
  window.addEventListener('resize', resizeHandler)
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  window.removeEventListener('resize', resizeHandler)
  mainChartInst?.dispose()
  volumeChartInst?.dispose()
  macdChartInst?.dispose()
  rsiChartInst?.dispose()
})
</script>
