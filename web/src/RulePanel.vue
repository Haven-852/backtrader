<template>
  <section class="p-6 max-w-5xl">
    <div class="flex items-center justify-between gap-3 mb-2">
      <h2 class="text-base font-medium">交易规则</h2>
      <button type="button" class="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700" @click="startCreate">
        {{ editing ? '收起' : '写一条规则' }}
      </button>
    </div>
    <p class="text-xs text-zinc-500 mb-3">经验是发现了什么。规则是条件成立时怎么做。检查通过只进入测试中，样本外结果够了才可以生效。</p>

    <form v-if="editing" class="mb-4 border border-zinc-800 rounded-xl p-4 space-y-3" @submit.prevent="save">
      <label class="block text-xs text-zinc-400">用一句话写这条规则
        <textarea v-model="form.statement" required rows="6" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100" placeholder="例如：市场处于熊市或震荡时，白酒板块可以做出相对沪深300的超额。板块自身持续走弱时停用。"></textarea>
      </label>
      <p class="text-[11px] text-zinc-500">不用逐项填指标。这句话会整理成 JSON，板块名称和市场状态会跟库里的记录对齐，再整段存进数据库。</p>
      <div class="text-xs text-zinc-400">来源经验（进入测试至少 3 条）</div>
      <div class="flex flex-wrap gap-2">
        <label v-for="lesson in catalog.lessons" :key="lesson.id" class="text-xs text-zinc-300 flex items-center gap-1">
          <input v-model="form.lesson_ids" type="checkbox" :value="lesson.id" /> {{ lesson.title }}
        </label>
        <span v-if="!(catalog.lessons || []).length" class="text-xs text-zinc-500">还没有经验。先在「我的经验」里留下记录。</span>
      </div>
      <button type="submit" class="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500" :disabled="saving">{{ saving ? '正在整理…' : (form.id ? '重新整理并保存' : '整理并保存') }}</button>
    </form>

    <div class="flex flex-wrap gap-2 mb-3">
      <button v-for="item in statusChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="status === item.id ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'" @click="status = item.id">{{ item.label }}</button>
    </div>
    <div class="flex flex-wrap gap-2 mb-4">
      <button v-for="item in categoryChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="category === item.id ? 'bg-zinc-700 text-zinc-100 border-zinc-500' : 'border-zinc-800 text-zinc-500'" @click="category = item.id">{{ item.label }} {{ item.count }}</button>
    </div>
    <p v-if="error" class="mb-3 text-sm text-red-300">{{ error }}</p>
    <div v-if="!shown.length" class="text-sm text-zinc-500">还没有规则。一句「优先看强板块」还不能进来，要写成可核对的条件和动作。</div>
    <div v-else class="border border-zinc-800 rounded-xl overflow-hidden">
      <div v-for="row in shown" :key="row.id" class="border-b border-zinc-800 last:border-b-0">
        <button type="button" class="w-full px-3 py-2 text-left hover:bg-zinc-900" @click="openId = openId === row.id ? '' : row.id">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="text-zinc-100">{{ row.rule_code }} · {{ row.name }}</span>
            <span class="text-xs text-zinc-400">{{ row.status_label }} · {{ row.category_label }} · v{{ row.version }}</span>
          </div>
          <div class="text-[11px] text-zinc-500 mt-1">{{ row.check_verdict }} · {{ row.statement || row.description }}</div>
        </button>
        <div v-if="openId === row.id" class="px-3 pb-3 text-xs text-zinc-400 space-y-2">
          <div v-if="revisingId === row.id" class="space-y-2">
            <label class="block text-zinc-400">原句
              <textarea v-model="draft.statement" rows="4" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-100"></textarea>
            </label>
            <label class="block text-zinc-400">待验证
              <input v-model="draft.outcome" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-100" />
            </label>
            <div class="text-zinc-200">条件</div>
            <div v-for="(cond, index) in draft.conditions" :key="index" class="flex gap-2 items-center">
              <input v-model="cond.text" class="flex-1 bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-100" />
              <button type="button" class="px-2 py-1 rounded bg-zinc-800 text-zinc-300" @click="draft.conditions.splice(index, 1)">去掉</button>
            </div>
            <label class="block text-zinc-400">除非
              <input v-model="draft.exceptions" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1.5 text-xs text-zinc-100" />
            </label>
            <div class="flex gap-2 pt-1">
              <button type="button" class="px-2 py-1 rounded bg-sky-700" :disabled="saving" @click="saveRevise(row)">保存修改</button>
              <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="revisingId = ''">取消</button>
            </div>
          </div>
          <template v-else>
          <div>类型 {{ row.rule_type_label }} · 作用范围 {{ row.scope_label }}<span v-if="row.strategy_name"> · 策略 {{ row.strategy_name }}</span></div>
          <div v-if="row.statement" class="text-zinc-300">原句：{{ row.statement }}</div>
          <div v-if="row.document?.outcome">待验证：{{ row.document.outcome }}</div>
          <div class="text-zinc-200">对齐后的条件</div>
          <div v-for="line in row.condition_lines" :key="line">{{ line }}</div>
          <div v-for="item in row.document?.unresolved || []" :key="item.text">未对齐：{{ item.text }} · {{ item.reason }}</div>
          <div class="text-zinc-200">则 {{ row.action_line }}</div>
          <div>除非 {{ row.exception_line || '没有写' }}</div>
          <div>证据：样本 {{ metric(row.sample_size) }} · 胜率 {{ rate(row.win_rate) }} · 平均超额 {{ points(row.expectancy) }} · 盈亏比 {{ metric(row.profit_factor) }} · 累计超额回撤 {{ points(row.max_drawdown) }}</div>
          <div>样本内：{{ windowText(row.in_sample_result) }}</div>
          <div>样本外：{{ windowText(row.out_sample_result) }}</div>
          <div>向前测试：{{ windowText(row.forward_result) }}</div>
          <div>最近 20 次：{{ windowText(row.recent_result?.last_20) }} · 最近 50 次：{{ windowText(row.recent_result?.last_50) }}</div>
          <div v-if="(row.check_items || []).length" class="space-y-0.5">
            <div v-for="item in row.check_items" :key="item.key">{{ item.ok ? '通过' : '未过' }} · {{ item.title }} · {{ item.detail }}</div>
          </div>
          <div class="flex gap-2 pt-1">
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="runValidate(row)">重新测试</button>
            <button v-if="row.status === 'TESTING' || row.status === 'PRODUCTION'" type="button" class="px-2 py-1 rounded bg-zinc-800" @click="runPause(row)">暂停规则</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" :disabled="!canPromote(row)" @click="runPromote(row)">生效</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="beginRevise(row)">修改内容</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800 text-red-300" @click="removeRule(row)">删除规则</button>
          </div>
          </template>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from 'axios'

const props = defineProps<{ query?: string }>()
const items = ref<any[]>([])
const counts = ref<Record<string, number>>({})
const catalog = ref<any>({ categories: [], statuses: [], observables: [], actions: [], lessons: [], rule_types: [], scopes: [] })
const error = ref('')
const saving = ref(false)
const editing = ref(false)
const revisingId = ref('')
const draft = ref<any>({ statement: '', outcome: '', exceptions: '', conditions: [] })
const status = ref('ALL')
const category = ref('ALL')
const openId = ref('')

const blank = () => ({
  id: '',
  statement: '',
  lesson_ids: [] as string[],
})
const form = ref(blank())

const statusChips = [
  { id: 'ALL', label: '全部' },
  { id: 'PRODUCTION', label: '生效中' },
  { id: 'TESTING', label: '测试中' },
  { id: 'DRAFT', label: '草稿' },
  { id: 'PAUSED', label: '已暂停' },
]

const categoryChips = computed(() => {
  const rows = [{ id: 'ALL', label: '全部种类', count: items.value.length }]
  for (const item of catalog.value.categories || []) {
    rows.push({ id: item.id, label: item.label, count: counts.value[item.id] || 0 })
  }
  return rows
})

const shown = computed(() => {
  const key = (props.query || '').trim().toLowerCase()
  return items.value.filter((row) => {
    if (status.value !== 'ALL' && row.status !== status.value) return false
    if (category.value !== 'ALL' && row.category !== category.value) return false
    if (!key) return true
    return `${row.name || ''} ${row.rule_code || ''} ${row.description || ''}`.toLowerCase().includes(key)
  })
})

function startCreate() {
  editing.value = !editing.value
  if (editing.value) form.value = blank()
}
function beginRevise(row: any) {
  openId.value = row.id
  revisingId.value = row.id
  draft.value = {
    statement: row.statement || '',
    outcome: row.document?.outcome || '',
    exceptions: row.exception_line || '',
    conditions: (row.conditions || []).map((item: any) => ({
      observable: item.observable,
      op: item.op,
      basis: item.basis,
      text: item.text || (Array.isArray(item.value) ? item.value.join('、') : String(item.value ?? '')),
      value: item.value,
    })),
  }
}
async function saveRevise(row: any) {
  saving.value = true
  error.value = ''
  try {
    await axios.put(`/kb/rules/${row.id}/content`, draft.value)
    revisingId.value = ''
    await load()
    openId.value = row.id
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有保存'
  } finally {
    saving.value = false
  }
}
async function removeRule(row: any) {
  if (!window.confirm(`删除 ${row.rule_code}？删除后这条规则的原句和条件都会去掉。`)) return
  error.value = ''
  try {
    await axios.delete(`/kb/rules/${row.id}`)
    if (openId.value === row.id) openId.value = ''
    if (revisingId.value === row.id) revisingId.value = ''
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有删除'
  }
}
function metric(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  return Number.isNaN(n) ? '—' : String(n)
}
function rate(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}
function points(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)} 个百分点`
}
function windowText(block: any) {
  if (!block) return '还没有跑过'
  return `样本 ${block.sample_size ?? 0}，胜率 ${rate(block.win_rate)}，平均超额 ${points(block.expectancy)}`
}
function canPromote(row: any) {
  const out = row.out_sample_result || {}
  return row.status === 'TESTING' && row.check_verdict === 'PASS' && Number(out.sample_size || 0) >= 20 && Number(out.expectancy || 0) > 0
}

async function load() {
  const [list, meta] = await Promise.all([axios.get('/kb/rules'), axios.get('/kb/rules/catalog')])
  items.value = list.data?.items || []
  counts.value = list.data?.category_counts || {}
  catalog.value = meta.data || catalog.value
}
async function save() {
  saving.value = true
  error.value = ''
  try {
    const res = await axios.post('/kb/rules/compose', {
      id: form.value.id || undefined,
      statement: form.value.statement,
      lesson_ids: form.value.lesson_ids,
    }, { timeout: 120000 })
    editing.value = false
    await load()
    openId.value = res.data?.item?.id || ''
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有保存'
  } finally {
    saving.value = false
  }
}
async function runValidate(row: any) {
  error.value = ''
  try {
    await axios.post(`/kb/rules/${row.id}/validate`, null, { timeout: 120000 })
    await load()
    openId.value = row.id
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '验证没有完成'
  }
}
async function runPause(row: any) {
  error.value = ''
  try {
    await axios.post(`/kb/rules/${row.id}/pause`)
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有暂停'
  }
}
async function runPromote(row: any) {
  error.value = ''
  try {
    await axios.post(`/kb/rules/${row.id}/promote`)
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '还不能生效'
  }
}
onMounted(load)
</script>
