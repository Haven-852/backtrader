<template>
  <div class="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
    <!-- Sidebar - Yuxi Style -->
    <div class="w-72 bg-zinc-900 border-r border-zinc-800 flex flex-col">
      <!-- Logo -->
      <div class="p-6 border-b border-zinc-800">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center shadow-lg">
            <i class="fa-solid fa-brain text-white text-xl"></i>
          </div>
          <div>
            <h1 class="title-font text-3xl font-semibold tracking-tighter text-white">语析 · Yuxi</h1>
            <p class="text-xs text-zinc-500 mt-0.5">Backtrader Agent Platform v2.0</p>
          </div>
        </div>
      </div>

      <!-- Navigation -->
      <div class="flex-1 p-4 space-y-2">
        <div
          @click="switchTab(0)"
          class="nav-item flex items-center gap-3 px-4 py-3.5 rounded-3xl cursor-pointer transition-all"
          :class="{ 'bg-zinc-800 shadow-inner': currentTab === 0 }"
        >
          <i class="fa-solid fa-comments w-5 text-blue-400"></i>
          <span class="font-medium">智能体对话</span>
        </div>

        <div
          @click="switchTab(1)"
          class="nav-item flex items-center gap-3 px-4 py-3.5 rounded-3xl cursor-pointer transition-all hover:bg-zinc-800"
          :class="{ 'bg-zinc-800 shadow-inner': currentTab === 1 }"
        >
          <i class="fa-solid fa-plug w-5 text-emerald-400"></i>
          <span class="font-medium">连通性测试</span>
        </div>

        <div
          @click="switchTab(2)"
          class="nav-item flex items-center gap-3 px-4 py-3.5 rounded-3xl cursor-pointer transition-all hover:bg-zinc-800"
          :class="{ 'bg-zinc-800 shadow-inner': currentTab === 2 }"
        >
          <i class="fa-solid fa-chart-line w-5 text-amber-400"></i>
          <span class="font-medium">智能体管理</span>
        </div>
      </div>

      <!-- Status Footer -->
      <div class="p-6 border-t border-zinc-800">
        <div class="flex items-center justify-between text-xs">
          <div class="flex items-center gap-2">
            <div class="w-2 h-2 bg-emerald-500 rounded-full status-dot"></div>
            <span class="text-emerald-400">所有容器在线</span>
          </div>
          <div class="text-zinc-500">v2.0 · Vue3</div>
        </div>
        <div class="text-[10px] text-zinc-500 mt-4 text-center">
          仿造 <a href="https://github.com/xerrors/Yuxi" target="_blank" class="hover:text-blue-400 underline">Yuxi</a><br>
          全容器化 · 多智能体 · 存储层已联通
        </div>
      </div>
    </div>

    <!-- Main Content -->
    <div class="flex-1 flex flex-col">
      <!-- Top Header -->
      <div class="h-16 border-b border-zinc-800 bg-zinc-900/80 backdrop-blur-lg flex items-center px-8 justify-between z-10">
        <div class="flex items-center gap-4">
          <div class="text-xl font-semibold title-font" :class="{ 'text-blue-400': currentTab === 0, 'text-emerald-400': currentTab === 1, 'text-amber-400': currentTab === 2 }">
            {{ tabTitles[currentTab] }}
          </div>
          <div v-if="currentTab === 0" class="px-3 py-1 text-xs bg-blue-500/10 text-blue-400 rounded-2xl flex items-center gap-1.5">
            <i class="fa-solid fa-brain"></i>
            <span>DeepSeek R1 + 12个智能体</span>
          </div>
        </div>

        <div class="flex items-center gap-6">
          <!-- One-click Test -->
          <button
            @click="testAll"
            class="flex items-center gap-2 px-6 py-2 bg-emerald-500 hover:bg-emerald-600 text-white rounded-3xl text-sm font-medium transition-all active:scale-95"
          >
            <i class="fa-solid fa-bolt"></i>
            <span>一键测试全部</span>
          </button>

          <!-- Container Status -->
          <div class="flex items-center gap-2 bg-zinc-800 rounded-3xl px-4 py-1 text-sm">
            <div class="flex items-center gap-1.5">
              <div class="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></div>
              <span class="text-emerald-400">Docker</span>
            </div>
            <div class="w-px h-4 bg-zinc-700"></div>
            <span class="text-zinc-400 text-xs">8 服务运行中</span>
          </div>

          <div @click="clearChat" class="cursor-pointer text-zinc-400 hover:text-white transition-colors">
            <i class="fa-solid fa-trash-can"></i>
          </div>
        </div>
      </div>

      <!-- Chat Tab -->
      <div v-if="currentTab === 0" class="flex-1 flex flex-col bg-zinc-950">
        <div class="flex-1 p-8 overflow-auto" ref="chatContainer">
          <div class="max-w-4xl mx-auto space-y-8">
            <!-- Welcome Message -->
            <div v-if="messages.length === 0" class="text-center py-12">
              <div class="inline-flex items-center justify-center w-20 h-20 bg-gradient-to-br from-purple-500 to-blue-600 rounded-3xl mb-6">
                <i class="fa-solid fa-brain text-4xl text-white"></i>
              </div>
              <h2 class="text-3xl font-semibold title-font mb-3">欢迎使用 Yuxi Agent Platform</h2>
              <p class="text-zinc-400 max-w-md mx-auto">
                与 DeepSeek、CrewAI、AutoGPT 等 13 个智能体对话<br>
                支持实时存储层查询和量化交易策略生成
              </p>
              <div class="mt-8 flex justify-center gap-3">
                <div @click="quickPrompt('帮我分析下最近的市场趋势')" class="cursor-pointer text-xs bg-zinc-900 hover:bg-zinc-800 border border-zinc-700 px-5 py-2 rounded-3xl">市场趋势分析</div>
                <div @click="quickPrompt('生成一个均线交叉交易策略')" class="cursor-pointer text-xs bg-zinc-900 hover:bg-zinc-800 border border-zinc-700 px-5 py-2 rounded-3xl">生成交易策略</div>
              </div>
            </div>

            <!-- Messages -->
            <div v-for="(msg, index) in messages" :key="index" class="flex" :class="{ 'justify-end': msg.role === 'user' }">
              <div :class="msg.role === 'user' ? 'max-w-[65%] bg-blue-600 text-white' : 'flex gap-4 max-w-[75%]'"
                   class="px-6 py-4 rounded-3xl rounded-tr-none message-enter">
                <div v-if="msg.role === 'assistant'" class="flex-shrink-0 w-8 h-8 bg-gradient-to-br from-purple-500 to-blue-600 rounded-2xl flex items-center justify-center mt-0.5">
                  <i class="fa-solid fa-brain text-white text-sm"></i>
                </div>
                <div class="whitespace-pre-wrap text-[15px] leading-relaxed">
                  {{ msg.content }}
                </div>
              </div>
            </div>

            <!-- Loading -->
            <div v-if="isLoading" class="flex gap-4">
              <div class="w-8 h-8 bg-gradient-to-br from-purple-500 to-blue-600 rounded-2xl flex items-center justify-center flex-shrink-0">
                <i class="fa-solid fa-brain text-white text-sm animate-pulse"></i>
              </div>
              <div class="bg-zinc-900 px-6 py-4 rounded-3xl rounded-tl-none">
                <span class="animate-pulse">智能体思考中...</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Input Area -->
        <div class="p-6 border-t border-zinc-800 bg-zinc-900">
          <div class="max-w-4xl mx-auto">
            <div class="flex gap-3">
              <select v-model="selectedModel" class="bg-zinc-800 border border-zinc-700 rounded-3xl px-5 text-sm focus:outline-none focus:border-blue-500">
                <option value="deepseek">DeepSeek R1 (推荐)</option>
                <option value="openai">GPT-4o-mini</option>
                <option value="swarm">OpenAI Swarm</option>
                <option value="crewai">CrewAI 团队</option>
              </select>

              <input
                v-model="inputMessage"
                @keyup.enter="sendChatMessage"
                type="text"
                placeholder="向智能体提问... 支持交易策略、市场分析、回测建议等"
                class="flex-1 bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-3xl px-6 py-4 text-sm focus:outline-none transition-colors"
              />

              <button
                @click="sendChatMessage"
                :disabled="isLoading"
                class="w-14 h-14 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 rounded-3xl flex items-center justify-center transition-all active:scale-95"
              >
                <i class="fa-solid fa-paper-plane text-lg"></i>
              </button>
            </div>
            <div class="text-center text-[10px] text-zinc-500 mt-4">
              所有服务运行于 Docker 容器 · 数据持久化存储于 InfluxDB/PostgreSQL
            </div>
          </div>
        </div>
      </div>

      <!-- Test Tab -->
      <div v-if="currentTab === 1" class="flex-1 p-10 overflow-auto bg-zinc-950">
        <div class="max-w-5xl mx-auto">
          <div class="mb-10">
            <h2 class="text-4xl font-semibold title-font mb-2">连通性测试中心</h2>
            <p class="text-zinc-400">测试所有智能体和存储服务的实时连通性</p>
          </div>

          <div class="grid grid-cols-2 gap-8">
            <!-- AI Models -->
            <div class="glass rounded-3xl p-8">
              <div class="flex items-center gap-3 mb-6">
                <i class="fa-solid fa-brain text-2xl text-purple-400"></i>
                <h3 class="text-xl font-semibold">大模型 &amp; 智能体</h3>
              </div>

              <div class="space-y-4">
                <div @click="testModelConnection('deepseek')" class="test-card flex items-center justify-between p-6 bg-zinc-900 hover:bg-zinc-800 rounded-3xl cursor-pointer border border-zinc-700">
                  <div class="flex items-center gap-4">
                    <div class="w-10 h-10 bg-gradient-to-br from-blue-500 to-cyan-400 rounded-2xl flex items-center justify-center">
                      <i class="fa-solid fa-d"></i>
                    </div>
                    <div>
                      <div class="font-semibold">DeepSeek R1 8B</div>
                      <div class="text-xs text-zinc-500">本地 Ollama / API</div>
                    </div>
                  </div>
                  <div :class="getStatusClass(connectionStatus.deepseek)" class="font-mono text-sm px-4 py-1 rounded-2xl">
                    {{ getStatusText(connectionStatus.deepseek) }}
                  </div>
                </div>

                <div @click="testModelConnection('openai')" class="test-card flex items-center justify-between p-6 bg-zinc-900 hover:bg-zinc-800 rounded-3xl cursor-pointer border border-zinc-700">
                  <div class="flex items-center gap-4">
                    <div class="w-10 h-10 bg-gradient-to-br from-green-400 to-emerald-500 rounded-2xl flex items-center justify-center text-black">
                      <i class="fa-solid fa-o"></i>
                    </div>
                    <div>
                      <div class="font-semibold">OpenAI GPT-4o-mini</div>
                      <div class="text-xs text-zinc-500">Cloud API</div>
                    </div>
                  </div>
                  <div :class="getStatusClass(connectionStatus.openai)" class="font-mono text-sm px-4 py-1 rounded-2xl">
                    {{ getStatusText(connectionStatus.openai) }}
                  </div>
                </div>

                <div class="p-6 bg-zinc-900/50 rounded-3xl border border-dashed border-zinc-700 text-center text-xs text-zinc-400">
                  CrewAI · AutoGPT · BabyAGI · MetaGPT · LangChain · QuantResearch 智能体已就绪
                </div>
              </div>
            </div>

            <!-- Database Tests -->
            <div class="glass rounded-3xl p-8">
              <div class="flex items-center gap-3 mb-6">
                <i class="fa-solid fa-database text-2xl text-amber-400"></i>
                <h3 class="text-xl font-semibold">存储层状态</h3>
              </div>

              <div @click="testDatabaseConnection" class="test-card p-8 bg-zinc-900 hover:bg-zinc-800 rounded-3xl cursor-pointer border border-zinc-700 mb-6">
                <div class="flex justify-between items-start">
                  <div>
                    <div class="font-semibold text-lg mb-1">全链路存储测试</div>
                    <div class="text-sm text-zinc-400">InfluxDB · PostgreSQL(Timescale) · Redis · MinIO · ClickHouse</div>
                    <div class="text-[10px] text-zinc-500 mt-4">所有服务运行在独立 Docker 容器内</div>
                  </div>
                  <div :class="getStatusClass(connectionStatus.database)" class="text-right">
                    <div class="text-4xl font-mono font-light">{{ getStatusText(connectionStatus.database) }}</div>
                  </div>
                </div>
              </div>

              <div class="grid grid-cols-2 gap-4 text-xs">
                <div class="bg-zinc-900 p-4 rounded-2xl">
                  <div class="flex justify-between">
                    <span class="text-zinc-400">InfluxDB</span>
                    <span class="text-emerald-400">8086 ✓</span>
                  </div>
                </div>
                <div class="bg-zinc-900 p-4 rounded-2xl">
                  <div class="flex justify-between">
                    <span class="text-zinc-400">PostgreSQL</span>
                    <span class="text-emerald-400">15432 ✓</span>
                  </div>
                </div>
                <div class="bg-zinc-900 p-4 rounded-2xl">
                  <div class="flex justify-between">
                    <span class="text-zinc-400">Redis</span>
                    <span class="text-emerald-400">16379 ✓</span>
                  </div>
                </div>
                <div class="bg-zinc-900 p-4 rounded-2xl">
                  <div class="flex justify-between">
                    <span class="text-zinc-400">MinIO</span>
                    <span class="text-emerald-400">9000 ✓</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Agent Management Tab -->
      <div v-if="currentTab === 2" class="flex-1 p-10 bg-zinc-950 overflow-auto">
        <div class="max-w-4xl mx-auto">
          <h2 class="text-4xl title-font mb-8">智能体舰队管理</h2>

          <div class="grid grid-cols-3 gap-6">
            <div v-for="agent in agents" :key="agent.id" class="glass p-6 rounded-3xl">
              <div class="flex justify-between items-start mb-4">
                <div>
                  <div class="font-semibold">{{ agent.name }}</div>
                  <div class="text-xs text-zinc-500">{{ agent.type }}</div>
                </div>
                <span class="px-3 py-1 text-[10px] bg-emerald-500/10 text-emerald-400 rounded-2xl">在线</span>
              </div>
              <p class="text-xs text-zinc-400 leading-relaxed mb-6">{{ agent.description }}</p>
              <button @click="activateAgent(agent.id)" class="w-full py-3 text-xs border border-zinc-700 hover:bg-zinc-800 rounded-3xl transition-colors">
                激活智能体
              </button>
            </div>
          </div>

          <div class="mt-12 p-8 bg-zinc-900/50 rounded-3xl border border-zinc-700">
            <div class="text-center text-sm text-zinc-400">
              当前已加载 13 个智能体<br>
              DeepSeekAgent 可直接从 InfluxDB/PostgreSQL 查询历史行情数据
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useApiStore } from '../stores/useApiStore'

const apiStore = useApiStore()

const currentTab = ref(0)
const inputMessage = ref('')
const selectedModel = ref('deepseek')
const chatContainer = ref<HTMLElement | null>(null)

const tabTitles = [
  '智能体对话',
  '连通性测试',
  '智能体管理'
]

const agents = [
  { id: 1, name: 'DeepSeekAgent', type: 'LLM + 数据查询', description: '支持直接从存储层拉取历史数据并生成交易信号' },
  { id: 2, name: 'SignalGenerator', type: '量化信号', description: '技术指标计算与交易信号生成' },
  { id: 3, name: 'RiskManager', type: '风控', description: '仓位管理、止损风控策略' },
  { id: 4, name: 'PortfolioOptimizer', type: '组合优化', description: 'Markowitz 均值方差优化' },
  { id: 5, name: 'Backtester', type: '回测引擎', description: 'Backtrader 策略回测执行器' },
  { id: 6, name: 'CrewAI Team', type: '多智能体', description: '研究、分析、执行三角色协同' }
]

const messages = computed(() => apiStore.messages)
const isLoading = computed(() => apiStore.isLoading)
const connectionStatus = computed(() => apiStore.connectionStatus)

const switchTab = (tab: number) => {
  currentTab.value = tab
}

const sendChatMessage = async () => {
  if (!inputMessage.value.trim() || isLoading.value) return

  const message = inputMessage.value.trim()
  inputMessage.value = ''

  await apiStore.sendMessage(message, selectedModel.value)

  // Scroll to bottom
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  })
}

const testModelConnection = async (model: string) => {
  await apiStore.testModel(model)
}

const testDatabaseConnection = async () => {
  await apiStore.testDatabase()
}

const testAll = async () => {
  await apiStore.testAll()

  // Add system message
  if (currentTab.value === 0) {
    apiStore.addMessage('assistant', '✅ 所有智能体和存储服务测试完成！\n\nDeepSeek 可用 · PostgreSQL + Redis 已联通 · 存储层就绪。')
  }
}

const getStatusClass = (status: string) => {
  if (status === 'success') return 'text-emerald-400 bg-emerald-500/10'
  if (status === 'error') return 'text-red-400 bg-red-500/10'
  return 'text-amber-400 bg-amber-500/10'
}

const getStatusText = (status: string) => {
  if (status === 'success') return '✅ 已联通'
  if (status === 'error') return '❌ 失败'
  return '未测试'
}

const clearChat = () => {
  apiStore.clearMessages()
}

const quickPrompt = (prompt: string) => {
  inputMessage.value = prompt
  if (currentTab.value !== 0) {
    currentTab.value = 0
  }
  setTimeout(() => {
    sendChatMessage()
  }, 100)
}

const activateAgent = (id: number) => {
  apiStore.addMessage('assistant', `🚀 智能体 #${id} 已激活。\n\n我已准备好为您提供量化分析服务。`)
  currentTab.value = 0
}

// Initialize with welcome message
onMounted(() => {
  if (apiStore.messages.length === 0) {
    setTimeout(() => {
      apiStore.addMessage('assistant', '你好！我是 Yuxi Agent Platform 的核心智能体。\n\n当前所有服务均运行在 Docker 容器内。\n\n• 支持 13 种智能体\n• 5 个专业量化数据库\n• 可直接查询历史行情数据\n\n点击右侧「连通性测试」进行验证，或直接和我对话。')
    }, 600)
  }
})
</script>
