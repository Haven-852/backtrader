<template>
  <div class="h-full flex bg-zinc-950">
    <!-- Left: Chat Area -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Messages -->
      <div class="flex-1 overflow-auto p-4 lg:p-6">
        <div class="max-w-3xl mx-auto space-y-5">
          <div v-for="(msg, i) in messages" :key="i"
            :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'"
          >
            <div :class="msg.role === 'user'
              ? 'bg-blue-600 text-white max-w-[80%] px-4 py-2.5 rounded-2xl rounded-tr-md'
              : msg.role === 'system' 
                ? 'bg-zinc-800/80 max-w-[80%] px-4 py-2.5 rounded-2xl rounded-tl-md border border-zinc-700' 
                : 'bg-zinc-800/80 max-w-[80%] px-4 py-2.5 rounded-2xl rounded-tl-md'"
            >
              <!-- 系统消息中的 skill 标识 -->
              <div v-if="msg.role === 'system' && msg.skill" class="flex items-center gap-1.5 mb-1.5">
                <span class="text-xs px-1.5 py-0.5 rounded bg-blue-600/20 text-blue-400">{{ msg.skill }}</span>
              </div>
              <div class="text-sm leading-relaxed" v-html="formatContent(msg.content)"></div>
              <!-- 工具调用轨迹 -->
              <div v-if="msg.toolTrace?.length" class="mt-2 space-y-1.5">
                <div class="text-[10px] text-amber-400/90 font-medium tracking-wide">🔧 工具调用</div>
                <div
                  v-for="(step, si) in msg.toolTrace"
                  :key="si"
                  class="bg-zinc-900/80 border border-zinc-700/80 rounded-lg px-2.5 py-2 text-[11px]"
                >
                  <div class="flex items-center gap-1.5 text-zinc-300">
                    <span class="text-zinc-500">#{{ step.round || si + 1 }}</span>
                    <span class="font-mono text-emerald-400/90 truncate">{{ step.function || `${step.skill_id}/${step.tool_id}` }}</span>
                    <span :class="step.result_ok === false ? 'text-red-400' : 'text-emerald-400'" class="ml-auto flex-shrink-0">
                      {{ step.result_ok === false ? '失败' : '成功' }}
                    </span>
                  </div>
                  <div v-if="step.arguments && Object.keys(step.arguments).length" class="mt-1 text-zinc-500 font-mono truncate">
                    {{ JSON.stringify(step.arguments) }}
                  </div>
                  <details v-if="step.result_preview" class="mt-1">
                    <summary class="cursor-pointer text-zinc-500 hover:text-zinc-300">查看结果摘要</summary>
                    <pre class="mt-1 max-h-32 overflow-auto text-zinc-400 whitespace-pre-wrap break-all">{{ step.result_preview }}</pre>
                  </details>
                </div>
              </div>
              <div v-if="msg.data" class="mt-2">
                <div v-if="msg.dataType === 'tables'" class="bg-zinc-900 rounded-xl overflow-hidden border border-zinc-700">
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="border-b border-zinc-700">
                        <th class="text-left py-2 px-3 text-zinc-400 font-medium">表名</th>
                        <th class="text-right py-2 px-3 text-zinc-400 font-medium">大小</th>
                        <th class="text-right py-2 px-3 text-zinc-400 font-medium">行数</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="t in msg.data" :key="t.tablename" class="border-b border-zinc-800/50">
                        <td class="py-1.5 px-3 text-zinc-300 font-mono">{{ t.tablename }}</td>
                        <td class="py-1.5 px-3 text-zinc-500 text-right">{{ t.size || '-' }}</td>
                        <td class="py-1.5 px-3 text-zinc-500 text-right tabular-nums">{{ t.approx_rows ?? '-' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-else-if="msg.dataType === 'query-result'" class="bg-zinc-900 rounded-xl overflow-hidden border border-zinc-700">
                  <div class="max-h-80 overflow-auto">
                    <table class="w-full text-xs">
                      <thead>
                        <tr class="border-b border-zinc-600 bg-zinc-800">
                          <th class="text-left py-1.5 px-2.5 text-zinc-300 whitespace-nowrap" v-for="c in msg.data.columns" :key="c">{{ c }}</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(row, ri) in msg.data.rows" :key="ri" class="border-b border-zinc-800/30">
                          <td class="py-1 px-2.5 text-zinc-400 whitespace-nowrap" v-for="(c, ci) in msg.data.columns" :key="ci">{{ fmt(row[c]) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div class="px-3 py-1.5 border-t border-zinc-700 text-xs text-zinc-500">
                    {{ msg.data.row_count }} 行结果
                  </div>
                </div>
                <div v-else-if="msg.dataType === 'schema'" class="bg-zinc-900 rounded-xl p-3 border border-zinc-700">
                  <pre class="text-xs text-zinc-400 whitespace-pre-wrap font-mono">{{ msg.data.schema_ddl }}</pre>
                </div>
                <div v-else-if="msg.dataType === 'sql-check'" class="bg-zinc-900 rounded-xl p-3 border"
                  :class="msg.data.valid ? 'border-emerald-500/30' : 'border-amber-500/30'">
                  <div class="flex items-center gap-2 mb-1">
                    <span :class="msg.data.valid ? 'text-emerald-400' : 'text-amber-400'" class="text-xs font-medium">
                      {{ msg.data.valid ? '✅ 有效' : '⚠️ 问题' }}
                    </span>
                  </div>
                  <pre class="text-xs text-zinc-400 whitespace-pre-wrap font-mono">{{ msg.data.message }}</pre>
                </div>
              </div>
              <div class="text-xs mt-1.5 opacity-50">{{ formatTime(msg.time) }}</div>
            </div>
          </div>
          <div v-if="loading" class="flex justify-start">
            <div class="bg-zinc-800/80 px-4 py-2.5 rounded-2xl rounded-tl-md flex items-center gap-2">
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
        <div class="max-w-3xl mx-auto">
          <!-- Model Selector -->
          <div class="flex gap-2 mb-3 flex-wrap items-center">
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
            <label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none mr-2">
              <input v-model="useTools" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-blue-500 focus:ring-blue-500/40" />
              自动工具
            </label>
            <button
              @click="clearChat"
              class="px-3 py-1.5 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              <i class="fa-solid fa-trash-can mr-1"></i> 清空
            </button>
          </div>

          <!-- Input -->
          <div class="flex gap-2.5">
            <textarea
              v-model="inputMessage"
              @keydown.enter.exact.prevent="sendMessage"
              placeholder="输入消息... 支持 DeepSeek · GPT · Claude 等多模型"
              rows="1"
              class="flex-1 bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-2xl px-4 py-2.5 outline-none text-sm resize-none"
            ></textarea>
            <button
              @click="sendMessage"
              :disabled="!inputMessage.trim() || loading"
              class="px-5 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:text-zinc-500 rounded-2xl font-medium transition-colors flex items-center gap-2"
            >
              <i v-if="!loading" class="fa-solid fa-paper-plane"></i>
              <i v-else class="fa-solid fa-spinner animate-spin"></i>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Skill Panel -->
    <div class="w-72 flex-shrink-0 flex flex-col border-l border-zinc-800 bg-zinc-900/50 overflow-hidden">
      <!-- Header -->
      <div class="flex-shrink-0 px-4 py-3.5 border-b border-zinc-800 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-wand-magic-sparkles text-blue-400 text-sm"></i>
          <h3 class="text-sm font-semibold">技能面板</h3>
        </div>
        <div class="flex items-center gap-2">
          <button
            @click="toggleHandbook"
            class="text-[10px] px-2 py-1 rounded-md border transition-colors"
            :class="showHandbook ? 'border-amber-500/40 text-amber-300 bg-amber-500/10' : 'border-zinc-700 text-zinc-500 hover:text-zinc-300'"
            title="查看 tools/HANDBOOK.md"
          >
            手册
          </button>
          <span v-if="activeSkill" @click="activeSkill = null" class="text-xs text-zinc-500 hover:text-zinc-300 cursor-pointer transition-colors">
            <i class="fa-solid fa-xmark"></i>
          </span>
        </div>
      </div>

      <div class="flex-1 overflow-auto">
        <!-- 工具总目录手册 -->
        <div v-if="showHandbook" class="p-3">
          <div class="text-[10px] text-zinc-500 mb-2 font-mono break-all">{{ handbookPath || 'tools/HANDBOOK.md' }}</div>
          <pre class="text-[11px] text-zinc-400 whitespace-pre-wrap leading-relaxed bg-zinc-950/60 border border-zinc-800 rounded-xl p-3 max-h-[70vh] overflow-auto">{{ handbookContent || '加载中...' }}</pre>
        </div>

        <!-- Skill 列表（未选中时） -->
        <div v-else-if="!activeSkill" class="p-3 space-y-2">
          <div
            v-for="skill in skills"
            :key="skill.id"
            @click="selectSkill(skill)"
            class="bg-zinc-900 rounded-xl p-3.5 border border-zinc-800 hover:border-blue-500/40 cursor-pointer transition-all group"
          >
            <div class="flex items-center gap-2.5 mb-1.5">
              <div class="w-8 h-8 rounded-lg bg-blue-600/20 flex items-center justify-center">
                <i class="fa-solid fa-database text-blue-400 text-sm"></i>
              </div>
              <div>
                <div class="text-sm font-medium group-hover:text-blue-400 transition-colors">{{ skill.name }}</div>
                <div class="text-xs text-zinc-500">{{ skill.tools_count }} 个工具</div>
              </div>
            </div>
            <p class="text-xs text-zinc-600 leading-relaxed line-clamp-2">{{ skill.desc }}</p>
          </div>
          <div v-if="!skills.length" class="text-center py-10 text-zinc-500 text-xs">
            <i class="fa-solid fa-inbox text-2xl block mb-2 opacity-30"></i>
            暂无可用技能
          </div>
        </div>

        <!-- Skill 工具列表（选中后） -->
        <div v-else class="p-3">
          <!-- 技能信息 -->
          <div class="bg-zinc-900 rounded-xl p-3 border border-blue-500/30 mb-3">
            <div class="flex items-center gap-2.5 mb-2">
              <div class="w-7 h-7 rounded-lg bg-blue-600/20 flex items-center justify-center">
                <i class="fa-solid fa-database text-blue-400 text-xs"></i>
              </div>
              <span class="text-sm font-medium">{{ activeSkill.name }}</span>
            </div>
            <p class="text-xs text-zinc-500 leading-relaxed">{{ activeSkill.desc }}</p>
          </div>

          <!-- 工具按钮 -->
          <div class="space-y-1.5">
            <button
              v-for="tool in activeSkill.tools"
              :key="tool.id"
              @click="openTool(tool)"
              :class="selectedTool?.id === tool.id
                ? 'bg-blue-600/20 border-blue-500/40'
                : 'bg-zinc-900 border-zinc-800 hover:border-zinc-700'"
              class="w-full text-left p-3 rounded-xl border transition-all"
            >
              <div class="text-sm font-medium text-zinc-300 mb-0.5">{{ tool.name }}</div>
              <div class="text-xs text-zinc-600 leading-relaxed">{{ tool.desc }}</div>
            </button>
          </div>
        </div>
      </div>

      <!-- 工具参数区（选中工具后从底部滑入） -->
      <div v-if="selectedTool" class="flex-shrink-0 border-t border-zinc-800 bg-zinc-900 p-3">
        <div class="flex items-center justify-between mb-2">
          <span class="text-sm font-medium text-zinc-300">{{ selectedTool.name }}</span>
          <span @click="selectedTool = null" class="text-xs text-zinc-500 hover:text-zinc-300 cursor-pointer">
            <i class="fa-solid fa-xmark"></i>
          </span>
        </div>
        <p class="text-xs text-zinc-600 mb-2.5">{{ selectedTool.desc }}</p>

        <!-- 参数输入 -->
        <div v-for="param in selectedTool.params" :key="param.name" class="mb-2">
          <label class="text-xs text-zinc-500 block mb-1">{{ param.label }}</label>
          <textarea
            v-if="param.type === 'textarea'"
            v-model="toolInputs[param.name]"
            :placeholder="param.placeholder || ''"
            rows="2"
            class="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-lg px-3 py-1.5 text-xs outline-none resize-none text-zinc-200"
          ></textarea>
          <input
            v-else
            v-model="toolInputs[param.name]"
            :placeholder="param.placeholder || ''"
            class="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-lg px-3 py-1.5 text-xs outline-none text-zinc-200"
          />
        </div>

        <button
          @click="executeTool"
          :disabled="skillExecuting"
          class="w-full mt-2 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 rounded-xl text-sm font-medium transition-colors flex items-center justify-center gap-2"
        >
          <i v-if="!skillExecuting" class="fa-solid fa-play text-xs"></i>
          <i v-else class="fa-solid fa-spinner animate-spin text-xs"></i>
          {{ skillExecuting ? '执行中...' : '执行' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue'
import axios from 'axios'

interface Message {
  role: string
  content: string
  time: Date
  skill?: string
  data?: any
  dataType?: string
  toolTrace?: any[]
}

interface Skill {
  id: string
  name: string
  icon: string
  category: string
  desc: string
  tools_count: number
  tools: Tool[]
}

interface Tool {
  id: string
  name: string
  desc: string
  params: any[]
}

const messages = ref<Message[]>([])
const inputMessage = ref('')
const selectedModel = ref('deepseek-chat')
const loading = ref(false)
const useTools = ref(true)

// ─── Skill Panel ─────────────────────────────────────
const skills = ref<Skill[]>([])
const activeSkill = ref<Skill | null>(null)
const selectedTool = ref<Tool | null>(null)
const toolInputs = reactive<Record<string, string>>({})
const skillExecuting = ref(false)
const showHandbook = ref(false)
const handbookContent = ref('')
const handbookPath = ref('')

const quickModels = [
  { id: 'deepseek-chat', name: 'DeepSeek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek R1' },
  { id: 'gpt-4o-mini', name: 'GPT-4o Mini' },
  { id: 'claude-3-sonnet', name: 'Claude' },
  { id: 'ollama', name: 'Ollama 本地' },
]

// ─── Helpers ─────────────────────────────────────────

function fmt(val: any) {
  if (val === null || val === undefined) return 'NULL'
  return String(val)
}

function addMessage(role: string, content: string, skillName?: string, data?: any, dataType?: string, toolTrace?: any[]) {
  messages.value.push({ role, content, time: new Date(), skill: skillName, data, dataType, toolTrace })
}

function formatContent(text: string): string {
  if (!text) return ''
  return String(text)
    .replace(/\*\*(.*?)\*\*/g, '<strong class="text-blue-400">$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code class="bg-zinc-700 px-1 py-0.5 rounded text-xs text-emerald-300">$1</code>')
    .replace(/\n/g, '<br>')
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

// ─── Chat ────────────────────────────────────────────

async function sendMessage() {
  const msg = inputMessage.value.trim()
  if (!msg || loading.value) return

  addMessage('user', msg)
  inputMessage.value = ''
  loading.value = true

  try {
    const history = messages.value
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }))

    const res = await axios.post('/chat/messages', {
      query: msg,
      model: selectedModel.value,
      history,
      use_tools: useTools.value,
    })
    addMessage(
      'assistant',
      res.data.content || '（空回复）',
      undefined,
      undefined,
      undefined,
      res.data.tool_trace || [],
    )
  } catch (e: any) {
    const detail = e?.response?.data?.detail || ''
    addMessage('assistant', `❌ 后端服务未响应。${detail || '请确保 backend 已启动 (端口 8000)。'}`)
  } finally {
    loading.value = false
  }
}

function clearChat() {
  messages.value = []
  onMountedInit()
}

// ─── Skill Panel ─────────────────────────────────────

async function loadSkills() {
  try {
    const res = await axios.get('/skills')
    skills.value = res.data.skills || []
  } catch (e) {
    console.warn('获取技能列表失败:', e)
  }
}

async function toggleHandbook() {
  showHandbook.value = !showHandbook.value
  if (showHandbook.value && !handbookContent.value) {
    try {
      const res = await axios.get('/skills/handbook')
      handbookContent.value = res.data.content || ''
      handbookPath.value = res.data.path || 'tools/HANDBOOK.md'
    } catch (e) {
      handbookContent.value = '加载手册失败，请确认后端已启动且 tools/HANDBOOK.md 存在。'
    }
  }
  if (showHandbook.value) {
    activeSkill.value = null
  }
}

function selectSkill(skill: Skill) {
  showHandbook.value = false
  activeSkill.value = skill
  selectedTool.value = null
}

function openTool(tool: Tool) {
  selectedTool.value = tool
  // 清空输入
  Object.keys(toolInputs).forEach(k => delete toolInputs[k])
  // 初始化默认值
  for (const p of tool.params) {
    toolInputs[p.name] = ''
  }
}

async function executeTool() {
  if (!activeSkill.value || !selectedTool.value || skillExecuting.value) return

  skillExecuting.value = true
  const skillId = activeSkill.value.id
  const toolId = selectedTool.value.id
  const toolName = selectedTool.value.name

  // 收集参数
  const payload: Record<string, any> = {}
  for (const p of selectedTool.value.params) {
    const val = toolInputs[p.name]
    if (p.required && !val) {
      addMessage('system', `⚠️ 请填写必填参数：${p.label}`, toolName)
      skillExecuting.value = false
      return
    }
    payload[p.name] = val || ''
  }

  // 生成参数描述
  const paramDesc = selectedTool.value.params
    .filter(p => toolInputs[p.name])
    .map(p => `${p.label}: ${toolInputs[p.name]}`)
    .join(', ')

  addMessage('system', `🔧 执行 **${toolName}**${paramDesc ? '\n' + paramDesc : ''}`, toolName)

  try {
    const res = await axios.post(`/skills/${skillId}/${toolId}`, payload)
    const data = res.data

    if (data.ok === false) {
      addMessage('system', `❌ ${data.error || '执行失败'}`, toolName)
    } else if (toolId === 'list-tables') {
      addMessage('system',
        `📋 共找到 **${data.count}** 张用户表：\n${data.tables?.map((t: any) => `\`${t.tablename}\``).join(', ')}`,
        toolName,
        data.tables,
        'tables'
      )
    } else if (toolId === 'info-table') {
      if (data.schema_ddl) {
        addMessage('system', `📊 表结构信息：`, toolName, data, 'schema')
      } else if (data.columns) {
        addMessage('system',
          `📊 字段信息（${data.tables?.join(', ')}）：\n${data.columns?.map((c: any) => `\`${c.table}.${c.column_name}\` — ${c.data_type}${c.is_nullable === 'YES' ? ' (可空)' : ''}`).join('\n')}`,
          toolName
        )
      }
    } else if (toolId === 'query-check') {
      addMessage('system',
        data.valid ? `✅ SQL 语法正确` : `⚠️ SQL 问题：\n${data.message}`,
        toolName,
        data,
        'sql-check'
      )
    } else if (toolId === 'query-exec') {
      const cols = data.columns || []
      const rows = data.rows || []
      if (rows.length) {
        addMessage('system', `⚡ 查询成功，返回 ${data.row_count} 行结果`, toolName, data, 'query-result')
      } else {
        addMessage('system', `⚡ 查询成功，但未返回数据`, toolName)
      }
    } else if (toolId === 'query-nl') {
      addMessage('system', data.error || data.suggestion || '查询完成', toolName)
    } else {
      addMessage('system', JSON.stringify(data, null, 2), toolName)
    }
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e.message || String(e)
    addMessage('system', `❌ 执行失败: ${detail}`, toolName)
  } finally {
    skillExecuting.value = false
  }
}

// ─── Init ────────────────────────────────────────────

function onMountedInit() {
  addMessage('assistant', `👋 欢迎使用 AIquant 多模型对话平台！

**可用模型**：DeepSeek Chat · DeepSeek Reasoner · GPT-4o · Claude · Ollama 本地

**💡 新功能**：右侧「技能面板」支持 SQL 数据库工具箱 —
点击即可浏览表结构、执行查询、校验 SQL，无需手动编写代码。`)
}

onMounted(() => {
  loadSkills()
  onMountedInit()
})
</script>
