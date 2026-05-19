<template>
  <div class="h-full flex flex-col bg-zinc-950">
    <!-- Messages -->
    <div class="flex-1 overflow-auto p-4 lg:p-8">
      <div class="max-w-4xl mx-auto space-y-6">
        <div v-for="(msg, i) in messages" :key="i"
          :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'"
        >
          <div :class="msg.role === 'user'
            ? 'bg-blue-600 text-white max-w-[75%] px-5 py-3 rounded-3xl rounded-tr-lg'
            : 'bg-zinc-800/80 max-w-[75%] px-5 py-3 rounded-3xl rounded-tl-lg'"
          >
            <div class="text-sm leading-relaxed whitespace-pre-wrap" v-html="formatContent(msg.content)"></div>
            <div class="text-xs mt-1.5 opacity-50">{{ formatTime(msg.time) }}</div>
          </div>
        </div>
        <div v-if="loading" class="flex justify-start">
          <div class="bg-zinc-800/80 px-5 py-3 rounded-3xl rounded-tl-lg flex items-center gap-2">
            <div class="flex gap-1">
              <div class="w-2 h-2 bg-zinc-500 rounded-full animate-bounce"></div>
              <div class="w-2 h-2 bg-zinc-500 rounded-full animate-bounce" style="animation-delay:0.1s"></div>
              <div class="w-2 h-2 bg-zinc-500 rounded-full animate-bounce" style="animation-delay:0.2s"></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Input Area -->
    <div class="border-t border-zinc-800 bg-zinc-900 p-4">
      <div class="max-w-4xl mx-auto">
        <!-- Model Selector -->
        <div class="flex gap-2 mb-3 flex-wrap">
          <button
            v-for="model in quickModels"
            :key="model.id"
            @click="selectedModel = model.id"
            :class="selectedModel === model.id
              ? 'bg-blue-600/20 text-blue-400 border-blue-500/30'
              : 'bg-zinc-800 text-zinc-500 hover:text-zinc-300 border-transparent'"
            class="px-3 py-1.5 rounded-lg text-xs border transition-all"
          >
            {{ model.name }}
          </button>
          <div class="flex-1"></div>
          <button
            @click="clearChat"
            class="px-3 py-1.5 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            <i class="fa-solid fa-trash-can mr-1"></i> 清空
          </button>
        </div>

        <!-- Input -->
        <div class="flex gap-3">
          <textarea
            v-model="inputMessage"
            @keydown.enter.exact.prevent="sendMessage"
            placeholder="输入消息... 支持 DeepSeek · GPT · Claude 等多模型"
            rows="1"
            class="flex-1 bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-2xl px-5 py-3 outline-none text-sm resize-none"
          ></textarea>
          <button
            @click="sendMessage"
            :disabled="!inputMessage.trim() || loading"
            class="px-6 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:text-zinc-500 rounded-2xl font-medium transition-colors flex items-center gap-2"
          >
            <i v-if="!loading" class="fa-solid fa-paper-plane"></i>
            <i v-else class="fa-solid fa-spinner animate-spin"></i>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'

interface Message {
  role: string
  content: string
  time: Date
}

const messages = ref<Message[]>([])
const inputMessage = ref('')
const selectedModel = ref('deepseek-chat')
const loading = ref(false)

const quickModels = [
  { id: 'deepseek-chat', name: 'DeepSeek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek R1' },
  { id: 'gpt-4o-mini', name: 'GPT-4o Mini' },
  { id: 'claude-3-sonnet', name: 'Claude' },
  { id: 'ollama', name: 'Ollama 本地' },
]

function addMessage(role: string, content: string) {
  messages.value.push({ role, content, time: new Date() })
}

function formatContent(text: string): string {
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong class="text-blue-400">$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code class="bg-zinc-700 px-1 py-0.5 rounded text-xs">$1</code>')
    .replace(/\n/g, '<br>')
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function sendMessage() {
  const msg = inputMessage.value.trim()
  if (!msg || loading.value) return

  addMessage('user', msg)
  inputMessage.value = ''
  loading.value = true

  try {
    const res = await axios.post('/chat/messages', {
      query: msg,
      model: selectedModel.value,
      history: messages.value.slice(-10).map(m => ({
        role: m.role,
        content: m.content,
      })),
    })
    addMessage('assistant', res.data.content || '（空回复）')
  } catch (e: any) {
    addMessage('assistant', '❌ 后端服务未响应。请确保 backend 已启动 (端口 8000)。')
  } finally {
    loading.value = false
  }
}

function clearChat() {
  messages.value = []
  onMountedInit()
}

function onMountedInit() {
  addMessage('assistant', `👋 欢迎使用 AIquant 多模型对话平台！

**可用模型**：DeepSeek Chat · DeepSeek Reasoner · GPT-4o · Claude · Ollama 本地模型

请在上方选择模型后开始对话。`)
}

onMounted(() => {
  onMountedInit()
})
</script>
