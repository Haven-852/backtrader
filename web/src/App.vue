<template>
  <div class="min-h-screen bg-zinc-950 text-zinc-100 flex">
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
      </div>

      <div class="p-5 border-t border-zinc-800 text-xs text-zinc-600">
        <div class="mb-1">v3.0 · Yuxi 风格架构</div>
        <div>web → routers → server → data</div>
      </div>
    </div>

    <!-- Main Content -->
    <div class="flex-1 flex flex-col min-w-0">
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
      <div class="flex-1 overflow-hidden">
        <!-- Chat Tab -->
        <div v-if="currentTab === 0" class="h-full flex flex-col">
          <ChatView />
        </div>

        <!-- Dash Tab -->
        <div v-if="currentTab === 1" class="h-full">
          <DashView />
        </div>

        <!-- Test Tab -->
        <div v-if="currentTab === 2" class="h-full overflow-auto p-6">
          <h3 class="text-xl font-semibold mb-6">连通性测试中心</h3>
          <div class="grid grid-cols-2 gap-6 max-w-5xl">
            <!-- 大模型测试 -->
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

            <!-- 数据库测试 -->
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

        <!-- Status Tab -->
        <div v-if="currentTab === 3" class="h-full overflow-auto p-6">
          <h3 class="text-xl font-semibold mb-6">存储层实时状态</h3>
          <div class="grid grid-cols-2 gap-6 max-w-4xl">
            <div v-for="svc in storageServices" :key="svc.name"
              class="bg-zinc-900 rounded-2xl p-6 border border-zinc-800">
              <div :class="svc.status === 'online' ? 'text-emerald-400' : 'text-amber-400'" class="text-sm mb-2">
                ● {{ svc.status === 'online' ? '运行中' : '待确认' }}
              </div>
              <div class="text-xl font-semibold">{{ svc.name }}</div>
              <div class="text-sm text-zinc-500 mt-1">{{ svc.desc }}</div>
              <div class="text-xs text-zinc-600 mt-2">端口: {{ svc.port }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'
import ChatView from './ChatView.vue'
import DashView from './DashView.vue'

const currentTab = ref(1)

const tabTitles = ['大模型对话', '行情看板', '连通性测试', '存储层状态']
const tabSubtitles = ['DeepSeek · GPT · Claude', 'K线 · MA · MACD · RSI', '模型 & 数据库', 'InfluxDB · PG · Redis']

const testModels = [
  { id: 'deepseek-chat', name: 'DeepSeek Chat', provider: 'deepseek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner', provider: 'deepseek' },
  { id: 'gpt-4o-mini', name: 'GPT-4o Mini', provider: 'openai' },
  { id: 'claude-3-sonnet', name: 'Claude 3 Sonnet', provider: 'anthropic' },
]
const modelResults = ref<Record<string, string>>({})
const dbStatus = ref('idle')
const dbResults = ref<any[]>([])

const storageServices = [
  { name: 'PostgreSQL + TimescaleDB', status: 'online', port: 15432, desc: '结构化数据 & 时序K线' },
  { name: 'InfluxDB', status: 'online', port: 8086, desc: '高频行情 & 分钟线' },
  { name: 'Redis', status: 'online', port: 16379, desc: '实时缓存' },
  { name: 'MinIO', status: 'online', port: 9000, desc: '数据湖 (S3兼容)' },
]

const testModel = async (modelId: string) => {
  try {
    const res = await axios.post(`/models/test/${modelId}`)
    modelResults.value[modelId] = res.data.status === 'success' ? '✅ 已联通' : '⚠️ ' + res.data.message
  } catch {
    modelResults.value[modelId] = '❌ 连接失败'
  }
}

const testAllDatabases = async () => {
  dbStatus.value = 'testing'
  try {
    const res = await axios.get('/models/test/database')
    const details = res.data.details || {}
    dbResults.value = Object.entries(details).map(([name, info]: [string, any]) => ({
      name,
      ok: info.status === 'connected',
      msg: info.message?.replace(/^[✅❌⚠️]\s*/, '') || info.status,
    }))
    dbStatus.value = dbResults.value.every((r: any) => r.ok) ? 'connected' : 'partial'
  } catch {
    dbStatus.value = 'error'
  }
}
</script>
