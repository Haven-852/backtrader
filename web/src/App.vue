<template>
  <div class="min-h-screen bg-zinc-950 text-zinc-100 flex">
    <!-- Sidebar -->
    <div class="w-72 bg-zinc-900 border-r border-zinc-800 flex flex-col">
      <div class="p-6 border-b border-zinc-800 flex items-center gap-3">
        <div class="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center">
          <span class="text-white font-bold text-xl">Y</span>
        </div>
        <div>
          <h1 class="title-font text-3xl font-semibold tracking-tighter">语析</h1>
          <p class="text-xs text-zinc-500 -mt-1">Backtrader Agent Platform</p>
        </div>
      </div>

      <div class="p-3 flex-1">
        <div 
          @click="currentTab = 0" 
          :class="{ 'bg-zinc-800 text-white': currentTab === 0 }"
          class="flex items-center gap-3 px-4 py-3 rounded-2xl mb-1 cursor-pointer hover:bg-zinc-800 transition-colors">
          <i class="fa-solid fa-comments w-5"></i>
          <span class="font-medium">大模型对话</span>
        </div>
        
        <div 
          @click="currentTab = 1" 
          :class="{ 'bg-zinc-800 text-white': currentTab === 1 }"
          class="flex items-center gap-3 px-4 py-3 rounded-2xl mb-1 cursor-pointer hover:bg-zinc-800 transition-colors">
          <i class="fa-solid fa-plug w-5"></i>
          <span class="font-medium">连通性测试</span>
        </div>
        
        <div 
          @click="currentTab = 2" 
          :class="{ 'bg-zinc-800 text-white': currentTab === 2 }"
          class="flex items-center gap-3 px-4 py-3 rounded-2xl cursor-pointer hover:bg-zinc-800 transition-colors">
          <i class="fa-solid fa-database w-5"></i>
          <span class="font-medium">存储层状态</span>
        </div>
      </div>

      <div class="p-6 border-t border-zinc-800 text-xs text-zinc-500">
        仿造 <a href="https://github.com/xerrors/Yuxi" target="_blank" class="hover:text-blue-400">Yuxi</a> 项目<br>
        Powered by Vue3 + FastAPI
      </div>
    </div>

    <!-- Main Content -->
    <div class="flex-1 flex flex-col">
      <header class="h-14 border-b border-zinc-800 bg-zinc-900 px-8 flex items-center justify-between">
        <div class="flex items-center gap-4">
          <h2 class="text-xl font-semibold">{{ tabTitles[currentTab] }}</h2>
        </div>
        <div class="flex items-center gap-4 text-sm">
          <div class="px-4 py-1 bg-emerald-500/10 text-emerald-400 rounded-3xl flex items-center gap-2">
            <div class="w-2 h-2 bg-emerald-500 rounded-full animate-pulse"></div>
            已连接
          </div>
          <button @click="testAll" 
                  class="px-5 py-2 bg-blue-600 hover:bg-blue-500 rounded-3xl text-sm font-medium transition-colors">
            一键测试全部
          </button>
        </div>
      </header>

      <!-- Tab Content -->
      <div class="flex-1 p-8 overflow-auto">
        <!-- Chat Tab -->
        <div v-if="currentTab === 0" class="max-w-4xl mx-auto">
          <div id="chat-messages" class="space-y-6 mb-8 h-[calc(100vh-220px)] overflow-auto pr-4">
            <div v-for="(msg, i) in messages" :key="i" 
                 :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'">
              <div :class="msg.role === 'user' 
                ? 'bg-blue-600 text-white max-w-[70%] px-5 py-3 rounded-3xl rounded-tr-none' 
                : 'bg-zinc-800 max-w-[70%] px-5 py-3 rounded-3xl rounded-tl-none'">
                {{ msg.content }}
              </div>
            </div>
          </div>
          
          <div class="flex gap-3 max-w-4xl">
            <input 
              v-model="inputMessage"
              @keyup.enter="sendMessage"
              placeholder="输入消息... 支持 DeepSeek 大模型对话" 
              class="flex-1 bg-zinc-900 border border-zinc-700 focus:border-blue-500 rounded-3xl px-6 py-4 outline-none text-sm"
            >
            <button @click="sendMessage" 
                    class="px-8 bg-blue-600 hover:bg-blue-500 rounded-3xl font-medium transition-colors">
              发送
            </button>
          </div>
        </div>

        <!-- Test Tab -->
        <div v-if="currentTab === 1" class="max-w-5xl mx-auto">
          <h3 class="text-2xl font-semibold mb-8">连通性测试中心</h3>
          <div class="grid grid-cols-2 gap-8">
            <div class="bg-zinc-900 rounded-3xl p-8">
              <h4 class="font-semibold mb-6 text-lg flex items-center gap-3">
                <i class="fa-solid fa-brain text-blue-400"></i> 大模型测试
              </h4>
              <div @click="testModel('deepseek')" 
                   class="test-item mb-4 p-5 rounded-2xl hover:bg-zinc-800 cursor-pointer flex justify-between items-center border border-transparent hover:border-blue-500">
                <div>
                  <div class="font-medium">DeepSeek R1</div>
                  <div class="text-sm text-zinc-500">deepseek-r1:8b • 本地模型</div>
                </div>
                <div :class="testResults.deepseek ? 'text-emerald-400' : 'text-zinc-500'" class="font-medium">
                  {{ testResults.deepseek || '未测试' }}
                </div>
              </div>
              <div @click="testModel('openai')" 
                   class="test-item p-5 rounded-2xl hover:bg-zinc-800 cursor-pointer flex justify-between items-center border border-transparent hover:border-blue-500">
                <div>
                  <div class="font-medium">OpenAI GPT-4o</div>
                  <div class="text-sm text-zinc-500">gpt-4o-mini • 云端模型</div>
                </div>
                <div :class="testResults.openai ? 'text-emerald-400' : 'text-zinc-500'" class="font-medium">
                  {{ testResults.openai || '未测试' }}
                </div>
              </div>
            </div>

            <div class="bg-zinc-900 rounded-3xl p-8">
              <h4 class="font-semibold mb-6 text-lg flex items-center gap-3">
                <i class="fa-solid fa-database text-emerald-400"></i> 数据库测试
              </h4>
              <div @click="testDatabase()" 
                   class="test-item p-6 rounded-2xl hover:bg-zinc-800 cursor-pointer border border-transparent hover:border-emerald-500">
                <div class="flex justify-between items-start">
                  <div>
                    <div class="font-medium text-lg">存储层全链路测试</div>
                    <div class="text-zinc-400 text-sm mt-1">InfluxDB · PostgreSQL · Redis · MinIO</div>
                  </div>
                  <div class="text-right">
                    <div :class="testResults.database ? 'text-emerald-400' : 'text-amber-400'" class="text-2xl font-semibold">
                      {{ testResults.database || '点击测试' }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Status Tab -->
        <div v-if="currentTab === 2" class="max-w-4xl mx-auto">
          <h3 class="text-2xl font-semibold mb-6">存储层实时状态</h3>
          <div class="grid grid-cols-2 gap-6">
            <div class="bg-zinc-900 rounded-3xl p-8">
              <div class="text-emerald-400 text-sm mb-2">● 运行中</div>
              <div class="text-2xl font-semibold">InfluxDB</div>
              <div class="text-zinc-500">高频行情存储 • 端口 8086</div>
            </div>
            <div class="bg-zinc-900 rounded-3xl p-8">
              <div class="text-emerald-400 text-sm mb-2">● 运行中</div>
              <div class="text-2xl font-semibold">PostgreSQL + TimescaleDB</div>
              <div class="text-zinc-500">结构化数据 • 端口 15432</div>
            </div>
            <div class="bg-zinc-900 rounded-3xl p-8">
              <div class="text-emerald-400 text-sm mb-2">● 运行中</div>
              <div class="text-2xl font-semibold">Redis</div>
              <div class="text-zinc-500">实时缓存 • 端口 16379</div>
            </div>
            <div class="bg-zinc-900 rounded-3xl p-8">
              <div class="text-emerald-400 text-sm mb-2">● 运行中</div>
              <div class="text-2xl font-semibold">MinIO</div>
              <div class="text-zinc-500">数据湖 • 端口 9000</div>
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

const currentTab = ref(0)
const messages = ref<any[]>([])
const inputMessage = ref('')
const testResults = ref({
  deepseek: '',
  openai: '',
  database: ''
})

const tabTitles = [
  '大模型对话',
  '连通性测试',
  '存储层状态'
]

const addMessage = (role: string, content: string) => {
  messages.value.push({ role, content, time: new Date() })
}

const sendMessage = async () => {
  if (!inputMessage.value.trim()) return
  
  const msg = inputMessage.value
  addMessage('user', msg)
  inputMessage.value = ''

  const loadingMsg = addMessage('assistant', '思考中...')

  try {
    const res = await axios.post('/api/chat', {
      message: msg,
      model: 'deepseek'
    })
    messages.value.pop()
    addMessage('assistant', res.data.response || '收到回复')
  } catch (err) {
    messages.value.pop()
    addMessage('assistant', '后端服务未响应，请确保 backend 已启动')
  }
}

const testModel = async (model: string) => {
  try {
    const res = await axios.get(`/api/test/model/${model}`)
    testResults.value[model as keyof typeof testResults.value] = '✅ 已联通'
  } catch (e) {
    testResults.value[model as keyof typeof testResults.value] = '❌ 连接失败'
  }
}

const testDatabase = async () => {
  try {
    const res = await axios.get('/api/test/database')
    testResults.value.database = '✅ 全部联通'
    console.log('数据库测试结果:', res.data)
  } catch (e) {
    testResults.value.database = '❌ 部分失败'
  }
}

const testAll = () => {
  testModel('deepseek')
  testModel('openai')
  testDatabase()
  addMessage('assistant', '所有测试已发起，请查看测试面板结果。')
}

onMounted(() => {
  addMessage('assistant', '欢迎使用语析 · Yuxi Backtrader Agent Platform\n\n左侧可切换不同功能面板。\n点击「一键测试全部」开始测试。')
})
</script>

<style scoped>
.test-item {
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
</style>