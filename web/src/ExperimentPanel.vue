<template>
  <section class="p-6 max-w-5xl">
    <div class="flex items-center justify-between gap-3 mb-2">
      <h2 class="text-base font-medium">实验室</h2>
      <button type="button" class="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700" @click="creating = !creating">
        {{ creating ? '收起' : '创建实验' }}
      </button>
    </div>
    <p class="text-xs text-zinc-500 mb-3">{{ meta.note }}</p>

    <div class="grid gap-3 md:grid-cols-2 mb-4">
      <div class="border border-zinc-800 rounded-xl p-3">
        <div class="text-xs text-zinc-500 mb-2">交易规则</div>
        <div v-if="!(meta.rules || []).length" class="text-xs text-zinc-600">还没有规则。</div>
        <div v-for="row in meta.rules" :key="row.id" class="flex items-center justify-between gap-2 py-1 text-sm">
          <span class="truncate text-zinc-200">{{ row.rule_code }} {{ row.name }}</span>
          <button type="button" class="shrink-0 text-xs px-2 py-1 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50" :disabled="busyId === row.id" @click="backtest('rule', row.id)">{{ busyId === row.id ? '回测中…' : '回测' }}</button>
        </div>
      </div>
      <div class="border border-zinc-800 rounded-xl p-3">
        <div class="text-xs text-zinc-500 mb-2">外部知识</div>
        <div v-if="!(meta.knowledge || []).length" class="text-xs text-zinc-600">还没有外部知识。</div>
        <div v-for="row in meta.knowledge" :key="row.id" class="flex items-center justify-between gap-2 py-1 text-sm">
          <span class="truncate text-zinc-200">{{ row.title }}</span>
          <button type="button" class="shrink-0 text-xs px-2 py-1 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50" :disabled="busyId === row.id" @click="backtest('external', row.id)">{{ busyId === row.id ? '回测中…' : '回测' }}</button>
        </div>
      </div>
    </div>

    <form v-if="creating" class="mb-4 border border-zinc-800 rounded-xl p-4 space-y-3" @submit.prevent="save">
      <label class="block text-xs text-zinc-400">实验名称
        <input v-model="form.strategy_name" required class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100" />
      </label>
      <label class="block text-xs text-zinc-400">假设
        <textarea v-model="form.hypothesis" required rows="3" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100"></textarea>
      </label>
      <div class="grid grid-cols-2 gap-3">
        <label class="text-xs text-zinc-400">来源
          <select v-model="form.hypothesis_source" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100">
            <option v-for="item in meta.sources" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </label>
        <label class="text-xs text-zinc-400">板块
          <input v-model="form.universe_name" required placeholder="白酒" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100" />
        </label>
      </div>
      <div class="text-xs text-zinc-400">市场环境（可多选，不选表示不按环境过滤）</div>
      <div class="flex flex-wrap gap-2">
        <label v-for="name in meta.regimes" :key="name" class="text-xs text-zinc-300">
          <input v-model="form.regimes" type="checkbox" :value="name" /> {{ name }}
        </label>
        <label class="text-xs text-zinc-300"><input v-model="form.require_benchmark_down" type="checkbox" /> 当天沪深300下跌</label>
      </div>
      <div class="text-xs text-zinc-400">持有周期</div>
      <div class="flex gap-2">
        <label v-for="day in meta.holdings" :key="day" class="text-xs text-zinc-300">
          <input v-model="form.holding_days" type="checkbox" :value="day" /> {{ day }}日
        </label>
      </div>
      <div class="grid grid-cols-3 gap-2 text-xs text-zinc-400">
        <label>训练起<input v-model="form.train_start" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-100" /></label>
        <label>训练止<input v-model="form.train_end" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-100" /></label>
        <label>验证起<input v-model="form.validation_start" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-100" /></label>
        <label>验证止<input v-model="form.validation_end" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-100" /></label>
        <label>测试起<input v-model="form.test_start" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-100" /></label>
        <label>测试止<input v-model="form.test_end" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-2 py-1 text-zinc-100" /></label>
      </div>
      <p class="text-[11px] text-zinc-500">改条件会变成新版本，测试集重新上锁。测试集不拿来调参。</p>
      <button type="submit" class="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500" :disabled="saving">{{ form.id ? '保存为新版本' : '保存假设' }}</button>
    </form>

    <div class="flex flex-wrap gap-2 mb-2">
      <button v-for="item in sourceChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="source === item.id ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'" @click="source = item.id">{{ item.label }}</button>
    </div>
    <div class="flex flex-wrap gap-2 mb-4">
      <button v-for="item in statusChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="status === item.id ? 'bg-zinc-700 text-zinc-100 border-zinc-500' : 'border-zinc-800 text-zinc-500'" @click="status = item.id">{{ item.label }}</button>
    </div>
    <p v-if="note" class="mb-3 text-sm text-zinc-300">{{ note }}</p>
    <p v-if="error" class="mb-3 text-sm text-red-300">{{ error }}</p>
    <div v-if="!shown.length" class="text-sm text-zinc-500">还没有实验。先写一个假设，再跑训练集。</div>
    <div v-else class="border border-zinc-800 rounded-xl overflow-hidden">
      <div v-for="row in shown" :key="row.id" class="border-b border-zinc-800 last:border-b-0">
        <button type="button" class="w-full px-3 py-2 text-left hover:bg-zinc-900" @click="openId = openId === row.id ? '' : row.id">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="text-zinc-100">{{ row.experiment_code || '实验' }} · {{ row.strategy_name }}</span>
            <span class="text-xs text-zinc-400">{{ row.status_label }} · {{ row.version }}</span>
          </div>
          <div class="text-[11px] text-zinc-500 mt-1">{{ row.source_label }} · {{ row.universe_name }} · {{ row.hypothesis }}</div>
        </button>
        <div v-if="openId === row.id" class="px-3 pb-3 text-xs text-zinc-400 space-y-2">
          <div v-if="row.parameters?.design_reason">模型这样测：{{ row.parameters.design_reason }}</div>
          <div v-if="row.parameters?.data_fill?.note">补数：{{ row.parameters.data_fill.note }}</div>
          <div v-if="row.parameters?.mode === 'full_market' || row.parameters?.mode === 'named_sector'">整段 {{ row.train_start }} ~ {{ row.train_end }} · {{ row.parameters?.mode === 'named_sector' ? ('板块 ' + (row.parameters?.sector_name || row.universe_name)) : '全部东财二级行业' }} · 未来 {{ row.parameters?.primary_holding }} 日相对沪深300<span v-if="row.parameters?.min_up_ratio != null"> · 上涨家数占比不低于 {{ Math.round(row.parameters.min_up_ratio * 100) }}%</span><span v-if="(row.parameters?.regimes || []).length"> · 环境 {{ row.parameters.regimes.join('、') }}</span></div>
          <template v-else>
          <div>基准 沪深300 · 持有 {{ (row.parameters?.holding_days || []).join(' / ') }} 日，主结果用 {{ row.parameters?.primary_holding }} 日超额<span v-if="row.parameters?.min_up_ratio != null"> · 上涨家数占比不低于 {{ Math.round(row.parameters.min_up_ratio * 100) }}%</span></div>
          <div>训练 {{ row.train_start }} ~ {{ row.train_end }}</div>
          <div>验证 {{ row.validation_start }} ~ {{ row.validation_end }}</div>
          <div>测试 {{ row.test_start }} ~ {{ row.test_end }} · {{ row.test_locked ? '测试集尚未解锁' : '测试集已打开' }}</div>
          </template>
          <div v-if="(row.result?.warnings || []).length">{{ row.result.warnings.join('；') }}</div>
          <div v-if="row.result?.full">全时段：{{ metricText(row.result.full) }}</div>
          <div v-if="row.result?.consistency">年份 {{ row.result.consistency.years_positive }}/{{ row.result.consistency.years }} 为正 · 板块 {{ row.result.consistency.sectors_positive }}/{{ row.result.consistency.sectors }} 为正 · 触发板块 {{ row.result.consistency.sectors_triggered }} 个</div>
          <div v-if="row.result?.train">训练：{{ metricText(row.result.train) }}</div>
          <div v-if="row.result?.validation">验证：{{ metricText(row.result.validation) }}</div>
          <div v-if="row.result?.test">测试：{{ metricText(row.result.test) }}</div>
          <div v-if="row.result?.by_regime">环境：<span v-for="(block, name) in row.result.by_regime" :key="name" class="mr-2">{{ name }} {{ metricText(block) }}</span></div>
          <div v-if="row.result?.by_year">年份：<span v-for="(block, name) in row.result.by_year" :key="name" class="mr-2">{{ name }} {{ metricText(block) }}</span></div>
          <div v-if="row.result?.by_sector">板块样本：<span v-for="(block, name) in row.result.by_sector" :key="name" class="mr-2">{{ name }} {{ metricText(block) }}</span></div>
          <div v-if="row.parameters?.mode !== 'full_market' && row.parameters?.mode !== 'named_sector' && row.overfit_check">过拟合：{{ row.overfit_check.overfit_risk }} <span v-if="(row.overfit_check.warnings || []).length">· {{ row.overfit_check.warnings.join('；') }}</span></div>
          <div v-if="row.conclusion" class="text-zinc-200">{{ row.conclusion }}</div>
          <div v-if="row.failure_label">原因：{{ row.failure_label }}</div>
          <div class="flex flex-wrap gap-2">
            <button v-if="row.parameters?.mode === 'full_market'" type="button" class="px-2 py-1 rounded bg-zinc-800" @click="run(row, 'train')">重新核对全市场</button>
            <button v-else-if="row.parameters?.mode === 'named_sector'" type="button" class="px-2 py-1 rounded bg-zinc-800" @click="run(row, 'train')">重新核对此板块</button>
            <template v-else>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="run(row, 'train')">运行训练集</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="run(row, 'validation')">运行验证集</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" :disabled="row.test_locked && !row.result?.validation" @click="run(row, 'test')">打开测试集</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" :disabled="row.status !== 'VALIDATED'" @click="makeRule(row)">生成候选规则</button>
            <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="edit(row)">修改条件</button>
            </template>
          </div>
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
const meta = ref<any>({ sources: [], statuses: [], regimes: [], holdings: [1, 3, 5, 10], note: '' })
const error = ref('')
const note = ref('')
const creating = ref(false)
const saving = ref(false)
const busyId = ref('')
const source = ref('ALL')
const status = ref('ALL')
const openId = ref('')

function blank() {
  return {
    id: '',
    strategy_name: '',
    hypothesis: '',
    hypothesis_source: 'MY_OBSERVATION',
    universe_name: '',
    regimes: [] as string[],
    require_benchmark_down: false,
    holding_days: [5] as number[],
    train_start: '2020-01-01',
    train_end: '2024-12-31',
    validation_start: '2025-01-01',
    validation_end: '2025-12-31',
    test_start: '2026-01-01',
    test_end: '2026-09-30',
  }
}
const form = ref(blank())

const sourceChips = computed(() => [{ id: 'ALL', label: '全部' }, ...(meta.value.sources || [])])
const statusChips = computed(() => [{ id: 'ALL', label: '全部状态' }, ...(meta.value.statuses || [])])
const shown = computed(() => {
  const key = (props.query || '').trim().toLowerCase()
  return items.value.filter((row) => {
    if (source.value !== 'ALL' && row.hypothesis_source !== source.value) return false
    if (status.value !== 'ALL' && row.status !== status.value) return false
    if (!key) return true
    return `${row.strategy_name || ''} ${row.hypothesis || ''} ${row.experiment_code || ''}`.toLowerCase().includes(key)
  })
})

function metricText(block: any) {
  if (!block) return '—'
  const win = block.win_rate == null ? '—' : `${(Number(block.win_rate) * 100).toFixed(1)}%`
  const exp = block.expectancy == null ? '—' : `${Number(block.expectancy) > 0 ? '+' : ''}${Number(block.expectancy).toFixed(2)} 个百分点`
  return `样本 ${block.sample_size ?? 0}，胜率 ${win}，平均超额 ${exp}`
}
async function load() {
  const [list, info] = await Promise.all([axios.get('/kb/experiments'), axios.get('/kb/experiments/meta')])
  items.value = list.data?.items || []
  meta.value = info.data || meta.value
}
async function save() {
  saving.value = true
  error.value = ''
  try {
    const body = { ...form.value, holding_days: form.value.holding_days }
    if (form.value.id) await axios.put(`/kb/experiments/${form.value.id}`, body)
    else await axios.post('/kb/experiments', body)
    creating.value = false
    form.value = blank()
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有保存'
  } finally {
    saving.value = false
  }
}
function edit(row: any) {
  creating.value = true
  form.value = {
    id: row.id,
    strategy_name: row.strategy_name,
    hypothesis: row.hypothesis,
    hypothesis_source: row.hypothesis_source || 'MY_OBSERVATION',
    universe_name: row.universe_name || '',
    regimes: [...(row.parameters?.regimes || [])],
    require_benchmark_down: !!row.parameters?.require_benchmark_down,
    holding_days: [...(row.parameters?.holding_days || [5])],
    train_start: row.train_start,
    train_end: row.train_end,
    validation_start: row.validation_start,
    validation_end: row.validation_end,
    test_start: row.test_start,
    test_end: row.test_end,
  }
}
async function run(row: any, phase: string) {
  error.value = ''
  try {
    const res = await axios.post(`/kb/experiments/${row.id}/run`, { phase }, { timeout: 360000 })
    if (res.data?.message) error.value = res.data.message
    await load()
    openId.value = row.id
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有跑完'
  }
}
async function backtest(kind: string, id: string) {
  busyId.value = id
  error.value = ''
  note.value = ''
  try {
    const res = await axios.post('/kb/experiments/backtest', { kind, id }, { timeout: 360000 })
    await load()
    openId.value = res.data?.item?.id || ''
    if (res.data?.design_reason) note.value = `已按模型设计跑完训练和验证：${res.data.design_reason}`
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '回测没有完成'
  } finally {
    busyId.value = ''
  }
}
async function makeRule(row: any) {
  error.value = ''
  try {
    const res = await axios.post(`/kb/experiments/${row.id}/candidate-rule`)
    error.value = res.data?.message || '已生成草稿规则'
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有生成规则'
  }
}
onMounted(load)
</script>
