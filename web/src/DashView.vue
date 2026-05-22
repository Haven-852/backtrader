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

    <!-- ===== ETF 行情栏 ===== -->
    <div class="flex-shrink-0 bg-zinc-900/70 border-b border-zinc-800">
      <div class="flex items-stretch overflow-x-auto scrollbar-thin">
        <div class="flex-shrink-0 flex items-center px-3 text-xs text-zinc-500 font-medium border-r border-zinc-800">
          ETF <i class="fa-solid fa-chart-simple ml-1.5"></i>
        </div>
        <div
          v-for="etf in etfs"
          :key="etf.ts_code"
          @click="selectETF(etf)"
          :class="[
            'flex-shrink-0 cursor-pointer px-3 py-2 min-w-[120px] border-r border-zinc-800 transition-colors hover:bg-zinc-800/50',
            selectedTab === 'etf' && currentTsCode === etf.ts_code
              ? 'bg-zinc-800/80 border-b-2 border-b-amber-500'
              : 'border-b-2 border-b-transparent'
          ]"
        >
          <div class="text-xs text-zinc-400 mb-0.5 truncate">{{ etf.short_name }}</div>
          <div class="flex items-baseline gap-1.5">
            <span
              :class="etf.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'"
              class="text-sm font-bold tabular-nums"
            >
              {{ etf.close > 0 ? formatPrice(etf.close) : '--' }}
            </span>
          </div>
          <div class="flex items-center gap-1 mt-0.5" v-if="etf.close > 0">
            <span
              :class="etf.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'"
              class="text-xs tabular-nums"
            >
              {{ etf.pct_chg >= 0 ? '+' : '' }}{{ etf.pct_chg.toFixed(2) }}%
            </span>
          </div>
          <div class="text-xs text-zinc-500 mt-0.5" v-else>无数据</div>
        </div>
        <div v-if="!etfs.length" class="flex-shrink-0 flex items-center px-4 text-xs text-zinc-500">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i> 加载ETF...
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
          <template v-else-if="selectedTab === 'etf' && currentETF">
            <span class="text-base font-bold">{{ currentETF.name }}</span>
            <span class="text-xs text-zinc-500">{{ currentETF.ts_code }}</span>
            <span class="text-xs px-1.5 py-0.5 rounded bg-amber-600/20 text-amber-400">{{ currentETF.fund_type || 'ETF' }}</span>
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
        <div v-else-if="!loading && !chartData.bars.length && !currentIndex && !currentStock && !currentETF" class="flex-1 flex items-center justify-center">
          <div class="text-center text-zinc-500">
            <i class="fa-solid fa-chart-line text-5xl mb-4 block opacity-40"></i>
            <p class="text-lg mb-2">实时行情看板</p>
            <p class="text-sm">点击上方指数查看大盘走势，或搜索个股代码</p>
          </div>
        </div>

        <!-- 图表 -->
        <div v-else class="flex-1 flex flex-col min-h-0">
          <div ref="mainChart" class="flex-1 min-h-0"></div>
          <div v-if="showVolume || selectedTab === 'index' || selectedTab === 'etf'" ref="volumeChart" class="h-32 border-t border-zinc-800 flex-shrink-0"></div>
          <div v-if="showMACD" ref="macdChart" class="h-32 border-t border-zinc-800 flex-shrink-0"></div>
          <div v-if="showRSI" ref="rsiChart" class="h-32 border-t border-zinc-800 flex-shrink-0"></div>
        </div>
      </div>

      <!-- 右侧信息面板 -->
      <div v-if="(currentIndex || currentStock || currentETF) && chartData.bars.length" class="w-72 bg-zinc-900 border-l border-zinc-800 p-4 overflow-auto hidden xl:block flex-shrink-0">
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
const selectedTab = ref<'index' | 'stock' | 'etf'>('index')
const currentIndex = ref<any>(null)
const currentStock = ref<any>(null)
const currentETF = ref<any>(null)
const currentTsCode = ref<string>('000001.SH')
const timeframe = ref('day')
const activeIndicators = ref<string[]>(['ma'])
const loading = ref(false)
const indices = ref<any[]>([])
const etfs = ref<any[]>([])

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

const showVolume = computed(() => activeIndicators.value.includes('volume') || selectedTab.value === 'index' || selectedTab.value === 'etf')
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

async function loadEtfs() {
  try {
    const res = await axios.get('/dash/etfs')
    etfs.value = res.data.etfs || []
  } catch (e) {
    console.warn('获取ETF列表失败:', e)
    // fallback: 即使后端失败也保留空列表，UI会显示加载状态
    etfs.value = []
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
      limit: 1000,  // 覆盖约4年日线，支持缩放回看3年+
    }
    if (activeIndicators.value.length) {
      params.indicators = activeIndicators.value.join(',')
    }

    // 指数、股票、ETF 统一用 kline 接口（后端已自动路由到对应数据表）
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
  currentETF.value = null
  currentTsCode.value = idx.ts_code
  searchText.value = ''
  loadData()
}

function selectStock(stock: any) {
  selectedTab.value = 'stock'
  currentStock.value = stock
  currentIndex.value = null
  currentETF.value = null
  currentTsCode.value = stock.ts_code
  searchText.value = stock.name
  showDropdown.value = false
  searchResults.value = []
  loadData()
}

function selectETF(etf: any) {
  selectedTab.value = 'etf'
  currentETF.value = etf
  currentIndex.value = null
  currentStock.value = null
  currentTsCode.value = etf.ts_code
  searchText.value = ''
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

  // 将所有图表分组联动：dataZoom、tooltip 自动同步
  const ec = (window as any).echarts
  const allCharts = [mainChartInst, volumeChartInst, macdChartInst, rsiChartInst].filter(Boolean)
  if (allCharts.length >= 2) {
    ec.connect('dash-group')
  }
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

// ─── 共享的 dataZoom 索引（所有图表联动缩放） ──────
let sharedDataZoomId = 0

function makeDataZoom(dataLen: number, showDetail: boolean = true) {
  const id = ++sharedDataZoomId
  // 根据数据量动态决定初始显示范围
  let end = 100
  let start: number
  if (dataLen <= 80) {
    start = 0
    end = 100
  } else if (dataLen <= 250) {
    start = Math.max(0, Math.round(100 - 120 * 100 / dataLen))
  } else {
    start = Math.max(0, Math.round(100 - 150 * 100 / dataLen))
  }

  const parts: any[] = []
  // 只有主图（showDetail=true）显示 slider 滑块
  if (showDetail) {
    parts.push({
      type: 'slider',
      xAxisIndex: 0,
      start,
      end,
      height: 20,
      bottom: 0,
      borderColor: '#333',
      fillerColor: 'rgba(99,102,241,0.2)',
      handleStyle: { color: '#6366f1' },
      textStyle: { color: '#999', fontSize: 9 },
    })
  }
  // inside 缩放保留（子图同步通过 echarts.connect 实现）
  parts.push({
    type: 'inside',
    xAxisIndex: 0,
    start,
    end,
    zoomOnMouseWheel: true,
    moveOnMouseMove: true,
  })
  return parts
}

function renderMainChart() {
  if (!mainChart.value) return
  const c = getThemeColors()

  if (!mainChartInst) {
    mainChartInst = (window as any).echarts.init(mainChart.value, 'dark', { group: 'dash-group' })
  }

  const bars = chartData.value.bars
  const barCount = bars.length
  const dates = bars.map((b: any) => b.time.substring(0, 10))
  const ohlc = bars.map((b: any) => [b.open, b.close, b.low, b.high])

  const series: any[] = [
    {
      name: 'K线',
      type: 'candlestick',
      data: ohlc,
      // 数据量大时启用渐进式渲染
      progressive: barCount > 200 ? 100 : 0,
      progressiveThreshold: 200,
      itemStyle: {
        color: c.up,
        color0: c.down,
        borderColor: c.up,
        borderColor0: c.down,
      },
    },
  ]

  // MA — 大数据集启用 large 模式（跳过重复的图形绘制逻辑）
  const ind = chartData.value.indicators
  if (ind.ma) {
    const maColors: Record<string, string> = { MA5: c.ma5, MA10: c.ma10, MA20: c.ma20, MA60: c.ma60 }
    for (const [key, values] of Object.entries(ind.ma)) {
      series.push({
        name: key,
        type: 'line',
        data: (values as any[]).filter((v: any) => v != null),
        smooth: false,
        symbol: 'none',
        large: (values as any[]).length > 200,
        largeThreshold: 200,
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
        data: (values as any[]).filter((v: any) => v != null),
        smooth: false,
        symbol: 'none',
        large: (values as any[]).length > 200,
        largeThreshold: 200,
        lineStyle: { width: 1, type: 'dashed' },
      })
    }
  }

  // BOLL
  if (ind.boll_BOLL_UPPER) {
    series.push(
      { name: 'BOLL上轨', type: 'line', data: (ind.boll_BOLL_UPPER as any[]).filter((v: any) => v != null), smooth: false, symbol: 'none',
        large: true, largeThreshold: 200, lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
      { name: 'BOLL中轨', type: 'line', data: (ind.boll_BOLL_MID as any[]).filter((v: any) => v != null), smooth: false, symbol: 'none',
        large: true, largeThreshold: 200, lineStyle: { width: 1.5, color: '#818cf8' } },
      { name: 'BOLL下轨', type: 'line', data: (ind.boll_BOLL_LOWER as any[]).filter((v: any) => v != null), smooth: false, symbol: 'none',
        large: true, largeThreshold: 200, lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
    )
  }

  const hasSubChart = showVolume.value || showMACD.value || showRSI.value
  mainChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 20, bottom: hasSubChart ? 5 : 40 },
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
    dataZoom: makeDataZoom(barCount, !hasSubChart),
    series,
  }, true)
}

function renderVolumeChart() {
  if (!volumeChart.value) return
  const c = getThemeColors()

  if (!volumeChartInst) {
    volumeChartInst = (window as any).echarts.init(volumeChart.value, 'dark', { group: 'dash-group' })
  }

  const bars = chartData.value.bars
  const barCount = bars.length
  const dates = bars.map((b: any) => b.time.substring(0, 10))
  const volumes = bars.map((b: any) => b.vol)
  const colors = bars.map((b: any) => b.close >= b.open ? c.up : c.down)

  const series: any[] = [{
    name: '成交量',
    type: 'bar',
    progressive: barCount > 200 ? 100 : 0,
    progressiveThreshold: 200,
    data: volumes.map((v: number, i: number) => ({ value: v, itemStyle: { color: colors[i] } })),
  }]

  const ind = chartData.value.indicators
  if (ind.VOL_MA5) {
    const d5 = (ind.VOL_MA5 as any[]).filter((v: any) => v != null)
    series.push({ name: 'VOL_MA5', type: 'line', data: d5, smooth: false,
      symbol: 'none', large: d5.length > 200, largeThreshold: 200,
      lineStyle: { width: 1, color: c.ma5 } })
  }
  if (ind.VOL_MA10) {
    const d10 = (ind.VOL_MA10 as any[]).filter((v: any) => v != null)
    series.push({ name: 'VOL_MA10', type: 'line', data: d10, smooth: false,
      symbol: 'none', large: d10.length > 200, largeThreshold: 200,
      lineStyle: { width: 1, color: c.ma10 } })
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
    dataZoom: makeDataZoom(barCount, false),
    series,
  }, true)
}

function renderMACDChart() {
  if (!macdChart.value) return
  const c = getThemeColors()
  if (!macdChartInst) macdChartInst = (window as any).echarts.init(macdChart.value, 'dark', { group: 'dash-group' })

  const ind = chartData.value.indicators
  const dif = ind.macd_DIF || []
  const dea = ind.macd_DEA || []
  const macdHist = ind.macd_MACD || []
  const histColors = macdHist.map((v: number) => v >= 0 ? c.up : c.down)
  const dataLen = dif.length

  macdChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 10, bottom: 5 },
    xAxis: { type: 'category', data: dif.map((_: any, i: number) => i), axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    dataZoom: makeDataZoom(dataLen, false),
    series: [
      { name: 'MACD', type: 'bar', data: macdHist.map((v: number, i: number) => ({ value: v, itemStyle: { color: histColors[i] } })), progressive: dataLen > 200 ? 100 : 0, progressiveThreshold: 200 },
      { name: 'DIF', type: 'line', data: dif, symbol: 'none', large: dataLen > 200, largeThreshold: 200, lineStyle: { width: 1, color: '#fbbf24' } },
      { name: 'DEA', type: 'line', data: dea, symbol: 'none', large: dataLen > 200, largeThreshold: 200, lineStyle: { width: 1, color: '#60a5fa' } },
    ],
  }, true)
}

function renderRSIChart() {
  if (!rsiChart.value) return
  const c = getThemeColors()
  if (!rsiChartInst) rsiChartInst = (window as any).echarts.init(rsiChart.value, 'dark', { group: 'dash-group' })

  const rsiData = chartData.value.indicators.rsi || []
  rsiChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 10, bottom: 5 },
    xAxis: { type: 'category', data: rsiData.map((_: any, i: number) => i), axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    dataZoom: makeDataZoom(rsiData.length, false),
    series: [{
      name: 'RSI', type: 'line', data: rsiData, symbol: 'none',
      large: rsiData.length > 200, largeThreshold: 200,
      lineStyle: { width: 1.5, color: '#818cf8' },
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
  await Promise.all([loadIndices(), loadEtfs(), loadIndicators()])
  resizeHandler = () => handleResize()
  window.addEventListener('resize', resizeHandler)
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  window.removeEventListener('resize', resizeHandler)
  ;(window as any).echarts?.disconnect('dash-group')
  mainChartInst?.dispose()
  volumeChartInst?.dispose()
  macdChartInst?.dispose()
  rsiChartInst?.dispose()
})
</script>
