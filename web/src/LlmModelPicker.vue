<template>
  <div class="relative" ref="rootEl">
    <div class="flex items-center gap-1.5">
      <button
        type="button"
        @click="toggleOpen"
        class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs border transition-all"
        :class="open ? 'bg-blue-600/20 text-blue-300 border-blue-500/40' : 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:border-zinc-500'"
        title="选择对话使用的大模型"
      >
        <i class="fa-solid fa-microchip text-[10px] opacity-80"></i>
        <span class="max-w-[9rem] truncate">{{ currentLabel }}</span>
        <i class="fa-solid fa-chevron-down text-[9px] opacity-60"></i>
      </button>
      <button
        type="button"
        @click="openManage"
        class="px-2 py-1.5 rounded-lg text-xs text-zinc-400 border border-zinc-800 hover:text-zinc-200 hover:border-zinc-600"
        title="配置自己的大模型"
      >
        <i class="fa-solid fa-sliders"></i>
      </button>
    </div>

    <Teleport to="body">
    <div
      v-if="open"
      ref="menuEl"
      class="fixed z-[210] w-72 rounded-xl border border-zinc-700 bg-zinc-900 shadow-xl flex flex-col overflow-hidden"
      :style="menuStyle"
    >
      <div class="flex-shrink-0 px-3 py-2 text-[10px] text-zinc-500 border-b border-zinc-800">勾选启用后点选即可对话</div>
      <div class="flex-1 min-h-0 overflow-y-auto overscroll-contain py-1">
        <button
          v-for="p in enabledProfiles"
          :key="'p-'+p.id"
          type="button"
          class="w-full text-left px-3 py-2 text-xs flex items-start gap-2 hover:bg-zinc-800/80"
          :class="isSelected(p) ? 'bg-blue-600/10' : ''"
          @click="selectProfile(p)"
        >
          <span class="mt-0.5 w-3.5 h-3.5 rounded-full border flex-shrink-0"
            :class="isSelected(p) ? 'border-blue-400 bg-blue-500' : 'border-zinc-600'"></span>
          <span class="min-w-0">
            <span class="block text-zinc-200 truncate">{{ p.name }}</span>
            <span class="block text-[10px] text-zinc-500 truncate">{{ p.provider }} · {{ p.model }}</span>
          </span>
        </button>
        <div v-if="!enabledProfiles.length" class="px-3 py-3 text-[11px] text-zinc-500">
          还没有已勾选的模型。点右侧滑块添加自己的 API Key。
        </div>
        <div v-if="systemFallbacks.length" class="border-t border-zinc-800 mt-1 pt-1">
          <div class="px-3 py-1 text-[10px] text-zinc-600">服务器已配置</div>
          <button
            v-for="m in systemFallbacks"
            :key="'s-'+m.id"
            type="button"
            class="w-full text-left px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800/80"
            :class="selected?.source === 'system' && selected.model === m.id ? 'text-blue-300' : ''"
            @click="selectSystem(m)"
          >
            {{ m.name }}
            <span v-if="m.available" class="ml-1 text-[9px] text-emerald-500/80">可用</span>
          </button>
        </div>
      </div>
      <button type="button" class="flex-shrink-0 w-full text-left px-3 py-2 text-[11px] text-blue-300 border-t border-zinc-800 hover:bg-zinc-800" @click="openManage">
        <i class="fa-solid fa-plus mr-1"></i>添加 / 管理模型
      </button>
    </div>
    </Teleport>
  </div>

  <Teleport to="body">
  <div v-if="showManage" class="fixed inset-0 z-[200] overflow-y-auto overscroll-contain bg-black/70 pointer-events-auto" @click.self="showManage = false">
    <div class="min-h-full flex items-start md:items-center justify-center p-4">
    <div class="w-full max-w-3xl my-4 max-h-[calc(100vh-2rem)] overflow-hidden rounded-2xl border border-zinc-700 bg-zinc-950 shadow-2xl flex flex-col">
      <div class="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <div>
          <div class="text-sm font-medium text-zinc-100">配置大模型</div>
          <div class="text-[11px] text-zinc-500">每人填自己的厂商、模型名和 API Key，勾选后用于对话</div>
        </div>
        <button type="button" class="text-zinc-500 hover:text-zinc-200" @click="showManage = false">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-5">
        <div class="md:col-span-2 border-r border-zinc-800 p-3 flex flex-col gap-3 min-h-0 overflow-hidden">
          <div class="flex-shrink-0 max-h-[36%] overflow-y-auto space-y-2">
            <div class="text-[10px] text-zinc-500">已保存</div>
            <label
              v-for="p in profiles"
              :key="p.id"
              class="flex gap-2 items-start rounded-xl border px-2.5 py-2 cursor-pointer"
              :class="editing?.id === p.id ? 'border-blue-500/50 bg-blue-600/10' : 'border-zinc-800 hover:border-zinc-700'"
            >
              <input type="checkbox" class="mt-1 rounded border-zinc-600" :checked="p.enabled" @change="toggleEnabled(p, ($event.target as HTMLInputElement).checked)" />
              <button type="button" class="flex-1 text-left min-w-0" @click="edit(p)">
                <div class="text-xs text-zinc-200 truncate">{{ p.name }}</div>
                <div class="text-[10px] text-zinc-500 truncate">{{ p.provider }} · {{ p.model }}</div>
                <div class="text-[10px] mt-0.5" :class="p.is_active ? 'text-emerald-400' : 'text-zinc-600'">
                  {{ p.is_active ? '当前对话使用' : '点选编辑，保存时勾选「用作当前模型」' }}
                </div>
              </button>
              <button type="button" class="text-zinc-600 hover:text-red-400 px-1" title="删除" @click.stop="remove(p)">
                <i class="fa-solid fa-trash text-[10px]"></i>
              </button>
            </label>
            <div v-if="!profiles.length" class="text-[11px] text-zinc-600 px-1">还没有配置</div>
          </div>
          <div
            class="flex-1 min-h-0 flex flex-col rounded-xl border overflow-hidden"
            :class="leftStatus
              ? (leftStatus.ok ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-red-500/30 bg-red-500/5')
              : 'border-zinc-800 bg-zinc-900/40'"
          >
            <div class="flex-shrink-0 px-3 py-2 border-b border-zinc-800/80 flex items-center justify-between">
              <span class="text-xs font-medium" :class="leftStatus ? (leftStatus.ok ? 'text-emerald-300' : 'text-red-300') : 'text-zinc-400'">
                {{ leftStatus?.title || '结果' }}
              </span>
              <span class="text-[10px] text-zinc-600">测试连通 / 添加并保存</span>
            </div>
            <pre class="flex-1 min-h-0 overflow-auto p-3 text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-all text-zinc-300">{{ leftJsonText }}</pre>
          </div>
        </div>
        <div class="md:col-span-3 overflow-auto p-4 space-y-3">
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="pre in presets"
              :key="pre.id"
              type="button"
              class="px-2 py-1 rounded-md text-[10px] border border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
              @click="applyPreset(pre)"
            >{{ pre.name }}</button>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div class="col-span-2">
              <label class="text-[10px] text-zinc-500 block mb-1">显示名称</label>
              <input v-model="form.name" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" placeholder="我的 DeepSeek" />
            </div>
            <div>
              <label class="text-[10px] text-zinc-500 block mb-1">厂商</label>
              <select v-model="form.provider" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none">
                <option value="deepseek">DeepSeek</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="grok">xAI Grok</option>
                <option value="ollama">Ollama 本地</option>
                <option value="openai_compat">OpenAI 兼容</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] text-zinc-500 block mb-1">模型名</label>
              <input v-model="form.model" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none font-mono" placeholder="deepseek-chat" />
            </div>
            <div class="col-span-2">
              <label class="text-[10px] text-zinc-500 block mb-1">Base URL</label>
              <input v-model="form.base_url" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none font-mono" placeholder="https://api.deepseek.com/v1" />
            </div>
            <div class="col-span-2">
              <label class="text-[10px] text-zinc-500 block mb-1">
                API Key
                <span v-if="form.id && form.has_api_key" class="text-zinc-600">已保存 {{ form.api_key_masked }}，留空则不改</span>
              </label>
              <input
                ref="apiKeyInput"
                v-model="form.api_key"
                type="password"
                name="llm-api-key"
                autocomplete="new-password"
                class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none font-mono"
                :placeholder="form.provider === 'ollama' ? '本地模型可不填' : 'sk-...'"
              />
            </div>
            <div>
              <label class="text-[10px] text-zinc-500 block mb-1">温度 {{ form.temperature }}</label>
              <input v-model.number="form.temperature" type="range" min="0" max="2" step="0.1" class="w-full" />
            </div>
            <div>
              <label class="text-[10px] text-zinc-500 block mb-1">最大 token</label>
              <input v-model.number="form.max_tokens" type="number" min="256" max="128000" class="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
            </div>
          </div>
          <label class="flex items-center gap-2 text-xs text-zinc-300">
            <input v-model="form.enabled" type="checkbox" class="rounded border-zinc-600" /> 勾选启用（出现在选择列表）
          </label>
          <label class="flex items-center gap-2 text-xs text-zinc-300">
            <input v-model="form.is_active" type="checkbox" class="rounded border-zinc-600" /> 用作当前对话模型
          </label>
          <p v-if="formHint" class="text-[10px] text-zinc-500">{{ formHint }}</p>
          <p v-if="formError" class="text-[11px] text-red-400">{{ formError }}</p>
          <div class="flex gap-2 pt-1">
            <button
              type="button"
              class="px-3 py-1.5 rounded-lg text-xs bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-60"
              :disabled="!!busy"
              @click.stop.prevent="testNow"
            >
              <i v-if="busy === 'test'" class="fa-solid fa-spinner animate-spin mr-1"></i>
              {{ busy === 'test' ? '测试中…' : '测试连通' }}
            </button>
            <button
              type="button"
              class="px-3 py-1.5 rounded-lg text-xs bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-60"
              :disabled="!!busy"
              @click.stop.prevent="save"
            >
              <i v-if="busy === 'save'" class="fa-solid fa-spinner animate-spin mr-1"></i>
              {{ busy === 'save' ? '保存中…' : (form.id ? '保存' : '添加并保存') }}
            </button>
            <button type="button" class="px-3 py-1.5 rounded-lg text-xs text-zinc-500" @click="resetForm">清空为新建</button>
          </div>
        </div>
      </div>
    </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import axios from 'axios'
import {
  type LlmPreset,
  type LlmProfile,
  chatPayload,
  emptyProfile,
  loadCatalog,
  recalledActiveId,
  rememberActiveId,
} from './lib/llmProfiles'

const emit = defineEmits<{
  (e: 'update:model', value: string): void
  (e: 'change', value: LlmProfile | null): void
}>()

const rootEl = ref<HTMLElement | null>(null)
const menuEl = ref<HTMLElement | null>(null)
const apiKeyInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const menuStyle = ref<Record<string, string>>({})
const showManage = ref(false)
const profiles = ref<LlmProfile[]>([])
const presets = ref<LlmPreset[]>([])
const systemModels = ref<any[]>([])
const selected = ref<LlmProfile | null>(null)
const busy = ref('')
const formError = ref('')
const leftStatus = ref<{ kind: 'test' | 'save'; ok: boolean; title: string; payload: any } | null>(null)
const editing = ref<LlmProfile | null>(null)
const form = reactive<LlmProfile>(emptyProfile())

function scrubSecrets(value: any): any {
  if (Array.isArray(value)) return value.map(scrubSecrets)
  if (value && typeof value === 'object') {
    const out: Record<string, any> = {}
    for (const [k, v] of Object.entries(value)) {
      if (/api[_-]?key|token|secret|authorization/i.test(k)) out[k] = v ? '***' : ''
      else out[k] = scrubSecrets(v)
    }
    return out
  }
  return value
}

function setLeft(kind: 'test' | 'save', ok: boolean, title: string, payload: any) {
  leftStatus.value = { kind, ok, title, payload: scrubSecrets(payload) }
}

const leftJsonText = computed(() => {
  if (!leftStatus.value) {
    return '点击右侧「测试连通」或「添加并保存」，返回的 JSON 会显示在这里。'
  }
  try {
    return JSON.stringify(leftStatus.value.payload, null, 2)
  } catch {
    return String(leftStatus.value.payload ?? '')
  }
})

const enabledProfiles = computed(() => profiles.value.filter(p => p.enabled))
const systemFallbacks = computed(() => systemModels.value.filter((m: any) => m.available))
const currentLabel = computed(() => selected.value?.name || selected.value?.model || '选择模型')
const formHint = computed(() => presets.value.find(p => p.provider === form.provider)?.hint || '')

function isSelected(p: LlmProfile) {
  return selected.value?.id === p.id && selected.value?.source !== 'system'
}

function pickDefault(list: LlmProfile[], catalogActive: LlmProfile | null) {
  const remembered = recalledActiveId()
  const byMemory = list.find(p => p.id === remembered && p.enabled)
  const active = catalogActive && catalogActive.enabled ? list.find(p => p.id === catalogActive.id) : null
  return byMemory || active || list.find(p => p.enabled) || list[0] || null
}

function applySelected(p: LlmProfile | null) {
  selected.value = p
  if (p?.id) rememberActiveId(p.id)
  emit('update:model', p?.model || 'deepseek-chat')
  emit('change', p)
}

function apiError(e: any, fallback: string) {
  const status = e?.response?.status
  if (status === 404) return '后端还没有模型配置接口，请重启后端后再保存 / 测试'
  if (e?.code === 'ECONNABORTED' || /timeout/i.test(e?.message || '')) {
    return '请求超时：后端 20 秒内没有返回。请确认后端已启动，或检查 Base URL / 网络。'
  }
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  return e?.message || fallback
}

function typedApiKey() {
  const fromDom = (apiKeyInput.value?.value || '').trim()
  const fromForm = (form.api_key || '').trim()
  const key = fromDom || fromForm
  if (key && form.api_key !== key) form.api_key = key
  return key
}

async function refresh() {
  try {
    const cat = await loadCatalog()
    presets.value = cat.presets
    profiles.value = cat.profiles
    systemModels.value = cat.system_models
    const next = pickDefault(cat.profiles, cat.active)
    if (next) applySelected(next)
    else if (systemFallbacks.value[0]) selectSystem(systemFallbacks.value[0], false)
  } catch (e: any) {
    formError.value = apiError(e, '无法读取已保存的模型配置')
  }
}

function selectProfile(p: LlmProfile) {
  applySelected({ ...p, source: 'profile' })
  open.value = false
  if (p.id) {
    axios.post(`/chat/llm-profiles/${p.id}/activate`).catch(() => {})
  }
}

function selectSystem(m: any, close = true) {
  applySelected({
    name: m.name,
    provider: m.provider,
    model: m.id,
    base_url: '',
    temperature: 0.7,
    max_tokens: 4096,
    enabled: true,
    is_active: true,
    source: 'system',
  })
  if (close) open.value = false
}

function openManage() {
  open.value = false
  showManage.value = true
  resetForm()
}

function resetForm() {
  Object.assign(form, emptyProfile(presets.value.find(p => p.id === 'deepseek')), {
    id: undefined,
    api_key: '',
    has_api_key: false,
    api_key_masked: '',
  })
  editing.value = null
  formError.value = ''
}

function applyPreset(pre: LlmPreset) {
  Object.assign(form, {
    ...form,
    name: form.name || pre.name,
    provider: pre.provider,
    model: pre.model || form.model,
    base_url: pre.base_url,
  })
}

function edit(p: LlmProfile) {
  editing.value = p
  Object.assign(form, {
    ...p,
    api_key: '',
  })
  formError.value = ''
}

async function toggleEnabled(p: LlmProfile, enabled: boolean) {
  if (!p.id) return
  await axios.patch(`/chat/llm-profiles/${p.id}`, { enabled })
  await refresh()
}

async function remove(p: LlmProfile) {
  if (!p.id || !confirm(`删除「${p.name}」？`)) return
  await axios.delete(`/chat/llm-profiles/${p.id}`)
  if (selected.value?.id === p.id) applySelected(null)
  await refresh()
}

async function save() {
  formError.value = ''
  if (!form.model.trim()) {
    formError.value = '请填写模型名'
    setLeft('save', false, '添加失败', { ok: false, message: '请填写模型名' })
    return
  }
  const apiKey = typedApiKey()
  if (form.provider !== 'ollama' && !apiKey && !form.has_api_key) {
    formError.value = '请填写 API Key'
    setLeft('save', false, '添加失败', { ok: false, message: '请填写 API Key 后再保存' })
    return
  }
  busy.value = 'save'
  const creating = !form.id
  try {
    const body: Record<string, any> = {
      name: form.name.trim() || `${form.provider} / ${form.model}`,
      provider: form.provider,
      model: form.model.trim(),
      base_url: form.base_url,
      temperature: Number(form.temperature),
      max_tokens: Number(form.max_tokens),
      enabled: form.enabled,
      is_active: form.is_active,
    }
    if (apiKey) body.api_key = apiKey
    const res = form.id
      ? await axios.patch(`/chat/llm-profiles/${form.id}`, body)
      : await axios.post('/chat/llm-profiles', body)
    const saved = res.data?.profile
    if (!res.data?.ok || !saved?.id) {
      throw new Error('后端未确认写入')
    }
    await refresh()
    const found = profiles.value.find(x => x.id === saved.id)
    if (!found) {
      setLeft('save', false, creating ? '添加失败' : '保存失败', {
        ok: false,
        message: '接口返回成功，但列表里没有这条配置',
        response: res.data,
      })
      return
    }
    applySelected(found)
    setLeft('save', true, creating ? '添加成功' : '保存成功', res.data)
  } catch (e: any) {
    const msg = apiError(e, creating ? '添加失败' : '保存失败')
    formError.value = msg
    setLeft('save', false, creating ? '添加失败' : '保存失败', {
      ok: false,
      message: msg,
      status: e?.response?.status,
      detail: e?.response?.data ?? null,
    })
  } finally {
    busy.value = ''
  }
}

async function testNow() {
  formError.value = ''
  const apiKey = typedApiKey()
  if (!form.model.trim()) {
    formError.value = '请填写模型名'
    setLeft('test', false, '连通测试失败', { ok: false, message: '请填写模型名' })
    return
  }
  if (form.provider !== 'ollama' && !apiKey && !form.has_api_key) {
    formError.value = '请填写 API Key'
    setLeft('test', false, '连通测试失败', { ok: false, message: '请填写 API Key 后再测试' })
    return
  }
  busy.value = 'test'
  setLeft('test', true, '正在测试连通…', {
    ok: true,
    status: 'pending',
    message: '已发送 POST /chat/llm-test，等待后端返回 JSON',
    provider: form.provider,
    model: form.model || '',
    base_url: form.base_url || '',
    has_api_key: Boolean(apiKey || form.has_api_key),
  })
  try {
    const body: Record<string, any> = {
      provider: form.provider,
      model: form.model,
      base_url: form.base_url,
      api_key: apiKey || undefined,
      profile_id: form.id || undefined,
    }
    const res = await axios.post('/chat/llm-test', body, { timeout: 20000 })
    if (res.data?.ok) {
      setLeft('test', true, '连通测试通过', res.data)
    } else {
      const msg = res.data?.message || '测试失败'
      formError.value = msg
      setLeft('test', false, '连通测试失败', res.data || { ok: false, message: msg })
    }
  } catch (e: any) {
    const msg = apiError(e, '测试失败')
    formError.value = msg
    setLeft('test', false, '连通测试失败', {
      ok: false,
      message: msg,
      status: e?.response?.status ?? e?.code ?? null,
      detail: e?.response?.data ?? null,
    })
  } finally {
    busy.value = ''
  }
}

function placeMenu() {
  const box = rootEl.value?.getBoundingClientRect()
  if (!box) return
  const gap = 6
  const width = 288
  const pad = 8
  const spaceBelow = window.innerHeight - box.bottom - pad
  const spaceAbove = box.top - pad
  const openUp = spaceBelow < 220 && spaceAbove > spaceBelow
  const maxH = Math.max(160, Math.min(360, openUp ? spaceAbove - gap : spaceBelow - gap))
  let left = box.left
  if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - width - pad)
  menuStyle.value = openUp
    ? {
        left: `${left}px`,
        bottom: `${window.innerHeight - box.top + gap}px`,
        maxHeight: `${maxH}px`,
      }
    : {
        left: `${left}px`,
        top: `${box.bottom + gap}px`,
        maxHeight: `${maxH}px`,
      }
}

async function toggleOpen() {
  open.value = !open.value
  if (open.value) await nextTick(placeMenu)
}

function onDocClick(ev: MouseEvent) {
  const t = ev.target as Node
  if (!open.value) return
  if (rootEl.value?.contains(t) || menuEl.value?.contains(t)) return
  open.value = false
}

onMounted(() => {
  refresh().catch(() => {})
  document.addEventListener('click', onDocClick)
  window.addEventListener('resize', placeMenu)
  window.addEventListener('scroll', placeMenu, true)
})
onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('resize', placeMenu)
  window.removeEventListener('scroll', placeMenu, true)
})

defineExpose({
  selected,
  chatPayload: () => chatPayload(selected.value),
  refresh,
})
</script>
