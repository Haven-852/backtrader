<template>
  <div class="h-screen bg-zinc-950 text-zinc-100 flex overflow-hidden">
    <!-- Sidebar -->
    <div class="w-64 bg-zinc-900 border-r border-zinc-800 flex flex-col">
      <div class="p-5 border-b border-zinc-800 flex items-center gap-3">
        <div class="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center">
          <span class="text-white font-bold text-lg">A</span>
        </div>
        <div>
          <h1 class="font-semibold text-lg tracking-tight">AIquant</h1>
          <p class="text-xs text-zinc-500 -mt-0.5">Backtrader Agent Platform</p>
        </div>
      </div>

      <div class="p-3 flex-1 space-y-1">
        <!-- 大模型对话 -->
        <div
          @click="currentTab = 0"
          :class="{ 'bg-zinc-800 text-white': currentTab === 0, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 0 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-comments w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">大模型对话</div>
            <div class="text-xs text-zinc-500">多模型 AI 分析</div>
          </div>
        </div>

        <!-- 行情看板 -->
        <div
          @click="currentTab = 1"
          :class="{ 'bg-zinc-800 text-white': currentTab === 1, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 1 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-chart-line w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">行情看板</div>
            <div class="text-xs text-zinc-500">K线 · 指标 · 多周期</div>
          </div>
        </div>

        <!-- 连通性测试 -->
        <div
          @click="currentTab = 2"
          :class="{ 'bg-zinc-800 text-white': currentTab === 2, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 2 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-plug w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">连通性测试</div>
            <div class="text-xs text-zinc-500">模型 · 数据库</div>
          </div>
        </div>

        <!-- 存储层状态 -->
        <div
          @click="currentTab = 3"
          :class="{ 'bg-zinc-800 text-white': currentTab === 3, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 3 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-database w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">存储层状态</div>
            <div class="text-xs text-zinc-500">基础设施监控</div>
          </div>
        </div>

        <!-- 行情中心 -->
        <div
          @click="currentTab = 4"
          :class="{ 'bg-zinc-800 text-white': currentTab === 4, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 4 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-globe w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">行情中心</div>
            <div class="text-xs text-zinc-500">全市场 · 30张表</div>
          </div>
        </div>
      </div>

      <div class="p-5 border-t border-zinc-800 text-xs text-zinc-600">
        <div class="mb-1">v3.0 · Yuxi 风格架构</div>
        <div>web → routers → server → data</div>
      </div>
    </div>

    <!-- Main Content -->
    <div class="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
      <!-- Header -->
      <header class="h-12 border-b border-zinc-800 bg-zinc-900 px-6 flex items-center justify-between flex-shrink-0">
        <div class="flex items-center gap-3">
          <h2 class="text-sm font-semibold text-zinc-300">{{ tabTitles[currentTab] }}</h2>
          <span class="text-xs text-zinc-600">{{ tabSubtitles[currentTab] }}</span>
        </div>
        <div class="flex items-center gap-3 text-xs">
          <div class="flex items-center gap-1.5 px-3 py-1 bg-emerald-500/10 text-emerald-400 rounded-full">
            <div class="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse"></div>
            已连接
          </div>
        </div>
      </header>

      <!-- Tab Content -->
      <div class="flex-1 min-h-0 overflow-hidden flex flex-col">
        <!-- Chat / Dash 用 v-show 保活，避免来回切换时整页销毁重建、反复拉数 -->
        <div v-show="currentTab === 0" class="h-full flex flex-col">
          <ChatView />
        </div>

        <div v-show="currentTab === 1" class="flex-1 min-h-0 flex flex-col overflow-hidden relative">
          <DashView :active="currentTab === 1" />
        </div>

        <!-- Test Tab -->
        <div v-if="currentTab === 2" class="h-full overflow-auto p-6">
          <h3 class="text-xl font-semibold mb-6">连通性测试中心</h3>
          <div class="grid grid-cols-2 gap-6 max-w-5xl">
            <div class="bg-zinc-900 rounded-2xl p-6">
              <h4 class="font-semibold mb-4 flex items-center gap-2">
                <i class="fa-solid fa-brain text-blue-400"></i> 大模型连通测试
              </h4>
              <div class="space-y-3">
                <div v-for="model in testModels" :key="model.id"
                  @click="testModel(model.id)"
                  class="p-4 rounded-xl bg-zinc-800/50 hover:bg-zinc-800 cursor-pointer border border-transparent hover:border-zinc-700 transition-all"
                >
                  <div class="flex justify-between items-center">
                    <div>
                      <div class="font-medium text-sm">{{ model.name }}</div>
                      <div class="text-xs text-zinc-500 mt-0.5">{{ model.provider }}</div>
                    </div>
                    <div :class="modelResults[model.id] ? 'text-emerald-400' : 'text-zinc-500'" class="text-sm font-medium">
                      {{ modelResults[model.id] || '点击测试' }}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="bg-zinc-900 rounded-2xl p-6">
              <h4 class="font-semibold mb-4 flex items-center gap-2">
                <i class="fa-solid fa-database text-emerald-400"></i> 存储层测试
              </h4>
              <div
                @click="testAllDatabases()"
                class="p-5 rounded-xl bg-zinc-800/50 hover:bg-zinc-800 cursor-pointer border border-transparent hover:border-emerald-500/30 transition-all mb-3"
              >
                <div class="flex justify-between items-center">
                  <div>
                    <div class="font-medium text-sm">全链路存储测试</div>
                    <div class="text-xs text-zinc-500 mt-0.5">PostgreSQL · TimescaleDB · InfluxDB · Redis</div>
                  </div>
                  <div class="text-right">
                    <div :class="dbStatus === 'connected' ? 'text-emerald-400' : dbStatus === 'testing' ? 'text-amber-400' : 'text-zinc-500'"
                      class="text-lg font-semibold">
                      {{ dbStatus === 'connected' ? '✅' : dbStatus === 'testing' ? '⏳' : '测试' }}
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="dbResults.length" class="space-y-2">
                <div v-for="r in dbResults" :key="r.name" class="flex justify-between items-center text-sm py-1.5 border-b border-zinc-800 last:border-0">
                  <span class="text-zinc-400">{{ r.name }}</span>
                  <span :class="r.ok ? 'text-emerald-400' : 'text-red-400'">{{ r.ok ? '✅' : '❌' }} {{ r.msg }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Storage Tab -->
        <div v-if="currentTab === 3" class="h-full flex flex-col overflow-hidden">
          <div class="flex-shrink-0 px-6 pt-5 pb-3">
            <h3 class="text-xl font-semibold mb-3">存储层实时状态</h3>
            <div class="flex gap-2 mb-4">
              <button v-for="be in backends" :key="be.key" @click="switchBackend(be.key)"
                :class="activeBackend === be.key ? 'bg-blue-600/20 text-blue-400 border-blue-500/40' : 'bg-zinc-900 text-zinc-500 hover:text-zinc-300 border-zinc-800'"
                class="flex items-center gap-2 px-4 py-2 rounded-xl border text-sm transition-all">
                <i :class="be.icon" class="text-xs"></i> {{ be.label }}
                <span class="w-1.5 h-1.5 rounded-full" :class="getBackendStatus(be.key) === 'online' ? 'bg-emerald-500' : getBackendStatus(be.key) === 'error' ? 'bg-red-500' : 'bg-amber-500'"></span>
              </button>
              <div class="flex-1"></div>
              <button @click="refreshAll" class="px-3 py-2 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 rounded-xl text-xs text-zinc-400 transition-colors">
                <i class="fa-solid fa-rotate mr-1" :class="{ 'animate-spin': tablesLoading }"></i> 刷新
              </button>
            </div>
            <div class="grid grid-cols-4 gap-2.5">
              <div v-for="be in backends" :key="'st-'+be.key" @click="switchBackend(be.key)"
                :class="['rounded-xl px-3.5 py-2.5 border cursor-pointer transition-all', activeBackend === be.key ? (getBackendStatus(be.key) === 'online' ? 'border-emerald-500/50 bg-emerald-500/5' : 'border-amber-500/40 bg-amber-500/5') : 'border-zinc-800 bg-zinc-900/70 hover:border-zinc-700']">
                <div class="flex items-center justify-between mb-1">
                  <span class="text-xs font-medium text-zinc-300">{{ be.label }}</span>
                  <span class="text-xs" :class="getBackendStatus(be.key) === 'online' ? 'text-emerald-400' : getBackendStatus(be.key) === 'error' ? 'text-red-400' : 'text-amber-400'">
                    {{ getBackendStatus(be.key) === 'online' ? '在线' : getBackendStatus(be.key) === 'error' ? '异常' : '离线' }}
                  </span>
                </div>
                <div class="text-xs text-zinc-600 truncate">{{ be.desc }}</div>
              </div>
            </div>
          </div>

          <div class="flex-1 flex min-h-0 px-6 pb-6">
            <div class="w-80 flex-shrink-0 flex flex-col mr-4">
              <div class="flex items-center gap-2 mb-3">
                <div class="relative flex-1">
                  <input v-model="tableSearch" :placeholder="getSearchPlaceholder()" class="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-lg pl-8 pr-3 py-1.5 text-xs outline-none transition-colors" />
                  <i class="fa-solid fa-search absolute left-2.5 top-2 text-zinc-500 text-xs"></i>
                </div>
                <button @click="loadItems()" class="px-2.5 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-xs transition-colors" :class="{ 'animate-spin': tablesLoading }">
                  <i class="fa-solid fa-rotate"></i>
                </button>
              </div>
              <div class="flex gap-1.5 mb-3 flex-wrap" v-if="tableCategories.length">
                <button v-for="cat in tableCategories" :key="cat" @click="tableCategoryFilter = tableCategoryFilter === cat ? '' : cat"
                  :class="tableCategoryFilter === cat ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-white'" class="px-2 py-1 rounded-md text-xs transition-all">{{ cat }}</button>
              </div>
              <div class="flex-1 overflow-auto space-y-1 pr-1">
                <div v-if="tablesLoading && !filteredItems.length" class="text-center py-8 text-zinc-500">
                  <i class="fa-solid fa-spinner animate-spin text-2xl mb-2 block"></i>加载中...
                </div>
                <div v-else-if="!filteredItems.length" class="text-center py-8 text-zinc-500 text-sm">暂无数据</div>
                <div v-for="t in filteredItems" :key="t.name" @click="selectItem(t)"
                  :class="['group cursor-pointer rounded-lg p-3 border transition-all', selectedItem?.name === t.name ? 'bg-blue-600/20 border-blue-500/50' : 'bg-zinc-900/50 border-transparent hover:border-zinc-700 hover:bg-zinc-900']">
                  <div class="flex items-center justify-between mb-0.5">
                    <span class="text-sm font-medium truncate">{{ t.label }}</span>
                    <span v-if="t.has_data" class="text-xs text-emerald-500 flex-shrink-0 ml-2"><i class="fa-solid fa-circle text-[6px]"></i> 有数据</span>
                    <span v-else class="text-xs text-zinc-600 flex-shrink-0 ml-2">空</span>
                  </div>
                  <div class="text-xs text-zinc-500 font-mono mb-1 truncate">{{ t.name }}</div>
                  <div class="text-xs text-zinc-600 line-clamp-1 leading-relaxed">{{ t.desc }}</div>
                  <div class="flex items-center justify-between mt-1.5">
                    <span class="text-xs text-zinc-700">{{ t.category }}</span>
                    <span class="text-xs text-zinc-600 tabular-nums">{{ formatRowCount(t.rows) }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="flex-1 flex flex-col min-w-0 bg-zinc-900 rounded-2xl border border-zinc-800">
              <div v-if="!selectedItem" class="flex-1 flex items-center justify-center">
                <div class="text-center text-zinc-500">
                  <i class="fa-solid fa-database text-4xl mb-3 block opacity-30"></i>
                  <p class="text-sm">选择数据项查看详情</p>
                  <p class="text-xs mt-1 text-zinc-600">{{ getEmptyHint() }}</p>
                </div>
              </div>
              <div v-else-if="previewLoading" class="flex-1 flex items-center justify-center">
                <div class="flex flex-col items-center gap-2">
                  <i class="fa-solid fa-spinner animate-spin text-2xl text-blue-400"></i>
                  <span class="text-sm text-zinc-400">加载预览...</span>
                </div>
              </div>
              <template v-else-if="previewData">
                <div class="flex-shrink-0 px-5 py-4 border-b border-zinc-800">
                  <div class="flex items-center justify-between mb-2">
                    <div>
                      <h3 class="text-lg font-bold">{{ previewData.label || previewData.name }}</h3>
                      <div class="flex items-center gap-3 mt-1">
                        <span class="text-xs text-zinc-500 font-mono">{{ previewData.name }}</span>
                        <span class="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">{{ activeBackend }}</span>
                      </div>
                    </div>
                    <div class="text-right" v-if="previewData.total_rows >= 0">
                      <div class="text-xs text-zinc-500">总行数</div>
                      <div class="text-lg font-bold text-emerald-400 tabular-nums">{{ formatRowCount(previewData.total_rows) }}</div>
                    </div>
                  </div>
                  <div class="text-sm text-zinc-400 bg-zinc-800/50 rounded-lg px-3 py-2 leading-relaxed">
                    <span class="text-zinc-500">📋 </span>{{ previewData.info?.desc || '暂无描述' }}
                  </div>
                </div>
                <div class="flex-shrink-0 px-5 py-3 border-b border-zinc-800" v-if="previewData.columns?.length">
                  <h4 class="text-xs font-semibold text-zinc-400 mb-2 uppercase tracking-wide">列信息 ({{ previewData.columns.length }} 列)</h4>
                  <div class="flex flex-wrap gap-1.5">
                    <span v-for="col in previewData.columns.slice(0, 20)" :key="col.name" class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-zinc-800 text-zinc-400" :title="col.name+': '+col.type">{{ col.name }}<span class="text-zinc-600">:{{ col.type }}</span></span>
                    <span v-if="previewData.columns.length > 20" class="text-xs text-zinc-600 self-center">...还有 {{ previewData.columns.length - 20 }} 列</span>
                  </div>
                </div>
                <div class="flex-1 flex flex-col min-h-0">
                  <div class="flex items-center justify-between px-5 pt-4 pb-2">
                    <h4 class="text-xs font-semibold text-zinc-400 uppercase tracking-wide">数据预览 ({{ previewOffset + 1 }} - {{ previewOffset + (previewData.rows?.length || 0) }})</h4>
                    <div class="flex items-center gap-2" v-if="previewData.has_more !== undefined">
                      <button @click="loadMoreRows(-1)" :disabled="previewOffset === 0 || previewLoadingMore" class="px-2 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-30 transition-colors"><i class="fa-solid fa-chevron-left"></i></button>
                      <span class="text-xs text-zinc-600 tabular-nums">{{ Math.floor(previewOffset / PREVIEW_LIMIT) + 1 }}</span>
                      <button @click="loadMoreRows(1)" :disabled="!previewData.has_more || previewLoadingMore" class="px-2 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-30 transition-colors"><i class="fa-solid fa-chevron-right"></i></button>
                    </div>
                  </div>
                  <div class="flex-1 overflow-auto px-5 pb-4" v-if="previewData.rows?.length">
                    <div class="overflow-x-auto max-h-full">
                      <table class="w-full text-xs border-collapse">
                        <thead class="sticky top-0 z-10"><tr class="border-b border-zinc-700 bg-zinc-900"><th class="text-left py-2 px-3 text-zinc-500 font-medium sticky left-0 bg-zinc-900">#</th><th v-for="col in previewColumns" :key="col" class="text-left py-2 px-3 text-zinc-400 font-medium whitespace-nowrap">{{ col }}</th></tr></thead>
                        <tbody><tr v-for="(row, ri) in previewData.rows" :key="ri" class="border-b border-zinc-800/50 hover:bg-zinc-800/30"><td class="py-1.5 px-3 text-zinc-600 sticky left-0 bg-zinc-900 tabular-nums">{{ previewOffset + ri + 1 }}</td><td v-for="col in previewColumns" :key="col" class="py-1.5 px-3 text-zinc-300 whitespace-nowrap max-w-[220px] truncate" :title="formatCell(row[col])">{{ formatCell(row[col]) }}</td></tr></tbody>
                      </table>
                    </div>
                    <div v-if="previewLoadingMore" class="flex justify-center py-3"><i class="fa-solid fa-spinner animate-spin text-zinc-500"></i></div>
                  </div>
                  <div v-else class="flex-1 flex items-center justify-center text-sm text-zinc-500">暂无数据</div>
                </div>
              </template>
            </div>
          </div>
        </div>

        <!-- Market Data Tab -->
        <div v-if="currentTab === 4" class="h-full">
          <MarketDashView />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'
import ChatView from './ChatView.vue'
import DashView from './DashView.vue'
import MarketDashView from './MarketDashView.vue'

const currentTab = ref(1)

const tabTitles = ['大模型对话', '行情看板', '连通性测试', '存储层状态', '行情中心']
const tabSubtitles = ['DeepSeek · GPT · Claude', 'K线 · MA · MACD · RSI', '模型 & 数据库', '数据表 · 内容预览', '全市场 · 30张表']

const testModels = [
  { id: 'deepseek-chat', name: 'DeepSeek Chat', provider: 'deepseek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner', provider: 'deepseek' },
  { id: 'gpt-4o-mini', name: 'GPT-4o Mini', provider: 'openai' },
  { id: 'claude-3-sonnet', name: 'Claude 3 Sonnet', provider: 'anthropic' },
]
const modelResults = ref<Record<string, string>>({})
const dbStatus = ref('idle')
const dbResults = ref<any[]>([])

const testModel = async (modelId: string) => {
  try {
    const res = await axios.post(`/models/test/${modelId}`)
    modelResults.value[modelId] = res.data.status === 'success' ? '✅ 已联通' : '⚠️ ' + res.data.message
  } catch { modelResults.value[modelId] = '❌ 连接失败' }
}

const testAllDatabases = async () => {
  dbStatus.value = 'testing'
  try {
    const res = await axios.get('/models/test/database')
    const details = res.data.details || {}
    dbResults.value = Object.entries(details).map(([name, info]: [string, any]) => ({
      name, ok: info.status === 'connected', msg: info.message?.replace(/^[✅❌⚠️]\s*/, '') || info.status,
    }))
    dbStatus.value = dbResults.value.every((r: any) => r.ok) ? 'connected' : 'partial'
  } catch { dbStatus.value = 'error' }
}

interface TableInfo { table_name: string; name: string; label: string; category: string; desc: string; source: string; rows: number; has_data: boolean }
interface PreviewData { table_name: string; info: TableInfo; columns: { name: string; type: string; nullable: boolean }[]; rows: Record<string, any>[]; total_rows: number; preview_count: number }

const backends = [
  { key: 'postgresql', label: 'PostgreSQL', icon: 'fa-solid fa-database', desc: '结构化 & 时序K线' },
  { key: 'influxdb', label: 'InfluxDB', icon: 'fa-solid fa-bolt', desc: '高频行情 & 分钟线' },
  { key: 'redis', label: 'Redis', icon: 'fa-solid fa-memory', desc: '实时缓存' },
  { key: 'minio', label: 'MinIO', icon: 'fa-solid fa-box-archive', desc: '数据湖 (S3兼容)' },
]
const activeBackend = ref('postgresql')
const storageServices = ref<Record<string, any>>({})
const storageItems = ref<TableInfo[]>([])
const tableCategories = ref<string[]>([])
const tableSearch = ref('')
const tableCategoryFilter = ref('')
const selectedItem = ref<TableInfo | null>(null)
const previewData = ref<PreviewData | null>(null)
const tablesLoading = ref(false)
const previewLoading = ref(false)
const previewLoadingMore = ref(false)
const previewOffset = ref(0)
const PREVIEW_LIMIT = 50

const filteredItems = computed(() => {
  let list = storageItems.value
  if (tableCategoryFilter.value) list = list.filter(t => t.category === tableCategoryFilter.value)
  if (tableSearch.value.trim()) {
    const kw = tableSearch.value.toLowerCase()
    list = list.filter(t => (t.name || '').toLowerCase().includes(kw) || (t.label || '').toLowerCase().includes(kw) || (t.desc || '').toLowerCase().includes(kw))
  }
  return list
})

const previewColumns = computed(() => {
  if (!previewData.value?.rows?.length) return []
  return Object.keys(previewData.value.rows[0])
})

function getBackendStatus(key: string): string {
  const svc = storageServices.value[key]
  if (!svc) return 'unknown'
  return svc.status || 'unknown'
}

function getSearchPlaceholder(): string {
  const map: Record<string, string> = { postgresql: '搜索表名或描述...', influxdb: '搜索 Bucket 或 Measurement...', redis: '搜索 Key...', minio: '搜索 Bucket 名称...' }
  return map[activeBackend.value] || '搜索...'
}

function getEmptyHint(): string {
  const map: Record<string, string> = { postgresql: '点击左侧数据表可查看表结构与数据预览', influxdb: '点击左侧 Bucket/Measurement 查看数据预览', redis: '点击左侧 Key 查看缓存内容', minio: '点击左侧 Bucket 查看对象列表' }
  return map[activeBackend.value] || '选择左侧数据项查看详情'
}

function switchBackend(key: string) { activeBackend.value = key; selectedItem.value = null; previewData.value = null; previewOffset.value = 0; loadItems() }
async function refreshAll() { await Promise.all([loadStorageStatus(), loadItems()]) }

async function loadStorageStatus() {
  try { const res = await axios.get('/storage/status'); storageServices.value = res.data.services || {} } catch (e) { console.warn('获取存储状态失败:', e) }
}

async function loadItems() {
  tablesLoading.value = true
  try {
    const res = await axios.get('/storage/tables', { params: { backend: activeBackend.value } })
    storageItems.value = (res.data.items || []).map((i: any) => ({ ...i, table_name: i.name, label: i.label || i.name || '' }))
    tableCategories.value = res.data.categories || []
  } catch (e) { console.warn('获取数据列表失败:', e) } finally { tablesLoading.value = false }
}

async function selectItem(t: TableInfo) {
  selectedItem.value = t; previewLoading.value = true; previewData.value = null; previewOffset.value = 0
  try {
    const itemName = encodeURIComponent(t.name || t.table_name || '')
    const res = await axios.get(`/storage/tables/${itemName}`, { params: { backend: activeBackend.value, limit: PREVIEW_LIMIT, offset: 0 } })
    if (!res.data.error) previewData.value = res.data
  } catch (e) { console.warn('获取预览失败:', e) } finally { previewLoading.value = false }
}

async function loadMoreRows(direction: 1 | -1) {
  if (!selectedItem.value || previewLoadingMore.value) return
  const newOffset = previewOffset.value + direction * PREVIEW_LIMIT
  if (newOffset < 0) return
  previewLoadingMore.value = true
  try {
    const itemName = encodeURIComponent(selectedItem.value.name || selectedItem.value.table_name || '')
    const res = await axios.get(`/storage/tables/${itemName}`, { params: { backend: activeBackend.value, limit: PREVIEW_LIMIT, offset: newOffset } })
    if (!res.data.error && res.data.rows?.length) { previewData.value = res.data; previewOffset.value = newOffset }
  } catch (e) { console.warn('加载更多失败:', e) } finally { previewLoadingMore.value = false }
}

function formatRowCount(n: number): string {
  if (n < 0) return '-'
  if (n >= 1_0000_0000) return (n / 1_0000_0000).toFixed(1) + '亿'
  if (n >= 1_0000) return (n / 1_0000).toFixed(1) + '万'
  return n.toLocaleString()
}

function formatCell(val: any): string {
  if (val === null || val === undefined) return 'NULL'
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'object') return JSON.stringify(val).substring(0, 60)
  return String(val)
}

onMounted(async () => { await Promise.all([loadStorageStatus(), loadItems()]) })
</script>
