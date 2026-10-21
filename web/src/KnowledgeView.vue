<template>
  <div class="h-full flex min-h-0 bg-zinc-950 text-zinc-100">
    <aside class="w-56 shrink-0 border-r border-zinc-800 flex flex-col bg-zinc-900/60">
      <div class="h-12 px-4 flex items-center justify-between border-b border-zinc-800">
        <button type="button" class="text-sm font-medium" @click="openSection('home')">知识库</button>
        <i class="fa-solid fa-magnifying-glass text-zinc-500 text-xs"></i>
      </div>
      <div class="px-3 py-2">
        <input
          v-model="q"
          type="text"
          placeholder="搜索"
          class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-xs outline-none focus:border-zinc-600"
        />
      </div>
      <nav class="px-2 pb-3 space-y-0.5 overflow-y-auto">
        <button
          v-for="item in nav"
          :key="item.id"
          type="button"
          class="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left"
          :class="section === item.id ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100'"
          @click="openSection(item.id)"
        >
          <i :class="['fa-solid', item.icon, 'w-4 text-center text-xs']"></i>
          {{ item.label }}
        </button>
      </nav>
    </aside>

    <main class="flex-1 min-w-0 overflow-y-auto">
      <div v-if="error" class="m-4 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
        {{ error }}
      </div>

      <section v-if="section === 'home'" class="p-6 max-w-3xl">
        <h2 class="text-base font-medium mb-4">今日交易认知</h2>
        <div class="text-xs text-zinc-500 mb-2">市场状态</div>
        <div class="flex flex-wrap gap-2 mb-6">
          <span
            v-for="chip in regimes"
            :key="chip.key"
            class="px-3 py-1 rounded-full text-xs border"
            :class="activeRegime === chip.key ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'"
          >{{ chip.label }}</span>
        </div>

        <div class="text-xs text-zinc-500 mb-2">今日机会</div>
        <div class="border border-zinc-800 rounded-xl divide-y divide-zinc-800 mb-6">
          <div v-if="!filteredOpps.length" class="px-4 py-6 text-sm text-zinc-500">今天还没有入库的机会。</div>
          <div v-for="opp in filteredOpps" :key="opp.id" class="px-4 py-3 flex items-center justify-between text-sm">
            <span>{{ oppLabel(opp) }}</span>
            <span class="tracking-widest text-amber-300">{{ stars(opp.opportunity_score) }}</span>
          </div>
        </div>

        <div class="text-xs text-zinc-500 mb-2">我的历史经验</div>
        <div class="border border-zinc-800 rounded-xl px-4 py-3 text-sm space-y-1.5">
          <div v-if="lessonText" class="text-zinc-300 mb-2">{{ lessonText }}</div>
          <div>类似行情：{{ statsText.count }}</div>
          <div>我的胜率：{{ statsText.win }}</div>
          <div>平均收益：{{ statsText.avg }}</div>
          <div>最大回撤：{{ statsText.dd }}</div>
        </div>
      </section>

      <MarketMemoryPanel v-else-if="section === 'market'" :query="q" />

      <OpportunityPanel v-else-if="section === 'opportunity'" :query="q" />

      <RulePanel v-else-if="section === 'rule'" :query="q" />

      <ExternalKnowledgePanel v-else-if="section === 'external'" :query="q" />

      <ExperimentPanel v-else-if="section === 'experiment'" :query="q" />

      <section v-else-if="section === 'ability'" class="p-6 max-w-3xl">
        <h2 class="text-base font-medium mb-1">交易能力</h2>
        <p class="text-xs text-zinc-500 mb-4">{{ ability.note || '按计划、实际执行和结果计算，不按盈亏。' }}</p>
        <p v-if="ability.early" class="text-xs text-amber-300/90 mb-4">样本量：{{ ability.sample_size }}笔。能力数据仍处于早期积累阶段，不会因为少数交易给出很高的分数。</p>
        <p v-else class="text-xs text-zinc-400 mb-4">样本量：{{ ability.sample_size }}笔</p>
        <div class="border border-zinc-800 rounded-xl px-4 py-3 font-mono text-sm space-y-2">
          <div v-for="row in ability.skills" :key="row.key" class="flex items-center gap-3">
            <span class="w-16 shrink-0 text-zinc-300">{{ row.label }}</span>
            <span v-if="row.score != null" class="tracking-tight text-sky-300">{{ bar(row.score) }}</span>
            <span v-else class="text-zinc-600">待计算</span>
            <span class="w-8 text-right text-zinc-100">{{ row.score == null ? '—' : row.score }}</span>
            <span class="text-[11px] text-zinc-500">样本 {{ row.sample_size }}</span>
          </div>
        </div>
        <div class="mt-4 text-sm text-zinc-300 space-y-1">
          <div>最强能力：{{ ability.strongest || '还没有拉开差距' }}</div>
          <div>主要短板：{{ ability.weakness || '—' }}</div>
          <div>次要短板：{{ ability.second_weakness || '—' }}</div>
        </div>
        <p v-if="abilityMissing" class="mt-4 text-xs text-zinc-500">显示为 — 的项目，是这笔记录里还没有对应证据。到「我的交易」补上市场方向、指数涨跌、板块判断、板块涨跌、卖出理由或实际止损后再计算。</p>
      </section>

      <TradeBook v-else-if="section === 'trades' || section === 'failures'" :mode="section" :query="q" />

      <section v-else-if="section === 'lesson'" class="p-6 max-w-3xl">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-base font-medium">我的经验</h2>
          <button type="button" class="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700" @click="addingLesson = !addingLesson">
            {{ addingLesson ? '收起' : '添加经验' }}
          </button>
        </div>
        <p class="text-xs text-zinc-500 mb-4">可以从交易里保存过来，也可以在这里直接写。删除只去掉这条经验，不会删掉原来的交易。</p>
        <form v-if="addingLesson" class="mb-4 border border-zinc-800 rounded-xl p-4 space-y-3" @submit.prevent="addLesson">
          <label class="block text-xs text-zinc-400">标题
            <input v-model="lessonForm.title" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100" />
          </label>
          <label class="block text-xs text-zinc-400">内容
            <textarea v-model="lessonForm.content" required rows="4" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100"></textarea>
          </label>
          <button type="submit" class="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500" :disabled="lessonSaving">保存经验</button>
        </form>
        <div v-if="!filteredItems.length" class="text-sm text-zinc-500">还没有经验。</div>
        <div v-else class="border border-zinc-800 rounded-xl divide-y divide-zinc-800">
          <div v-for="item in filteredItems" :key="item.id" class="px-4 py-3 text-sm flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="text-zinc-100">{{ itemTitle(item) }}</div>
              <div class="text-xs text-zinc-500 mt-1 whitespace-pre-wrap">{{ itemBody(item) }}</div>
            </div>
            <button type="button" class="shrink-0 text-xs text-zinc-500 hover:text-red-300" @click="removeLesson(item)">删除</button>
          </div>
        </div>
      </section>

      <section v-else class="p-6 max-w-3xl">
        <h2 class="text-base font-medium mb-4">{{ sectionTitle }}</h2>
        <div v-if="!filteredItems.length" class="text-sm text-zinc-500">这一栏还没有记录。</div>
        <div v-else class="border border-zinc-800 rounded-xl divide-y divide-zinc-800">
          <div v-for="item in filteredItems" :key="item.id" class="px-4 py-3 text-sm">
            <div class="text-zinc-100">{{ itemTitle(item) }}</div>
            <div class="text-xs text-zinc-500 mt-1 whitespace-pre-wrap">{{ itemBody(item) }}</div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from 'axios'
import MarketMemoryPanel from './MarketMemoryPanel.vue'
import OpportunityPanel from './OpportunityPanel.vue'
import RulePanel from './RulePanel.vue'
import ExternalKnowledgePanel from './ExternalKnowledgePanel.vue'
import ExperimentPanel from './ExperimentPanel.vue'
import TradeBook from './TradeBook.vue'

type AnyRec = Record<string, any>

const nav = [
  { id: 'market', label: '市场记忆', icon: 'fa-clock-rotate-left' },
  { id: 'opportunity', label: '机会库', icon: 'fa-bullseye' },
  { id: 'trades', label: '我的交易', icon: 'fa-folder' },
  { id: 'failures', label: '我的失败', icon: 'fa-xmark' },
  { id: 'lesson', label: '我的经验', icon: 'fa-lightbulb' },
  { id: 'ability', label: '交易能力', icon: 'fa-chart-simple' },
  { id: 'rule', label: '交易规则', icon: 'fa-list-check' },
  { id: 'external', label: '外部知识', icon: 'fa-book' },
  { id: 'experiment', label: '实验室', icon: 'fa-flask' },
]

const regimes = [
  { key: 'RECOVERY', label: '修复' },
  { key: 'RANGE', label: '震荡' },
  { key: 'HOT', label: '强势' },
  { key: 'WEAK', label: '弱势' },
]

const section = ref('home')
const q = ref('')
const error = ref('')
const overview = ref<AnyRec>({})
const items = ref<AnyRec[]>([])
const ability = ref<AnyRec>({ skills: [], sample_size: 0, early: true, note: '' })
const addingLesson = ref(false)
const lessonSaving = ref(false)
const lessonForm = ref({ title: '', content: '' })

const filteredOpps = computed(() => {
  const rows = overview.value.opportunities || []
  const key = q.value.trim().toLowerCase()
  if (!key) return rows
  return rows.filter((o: AnyRec) => `${o.sector_name || ''} ${o.name || ''} ${o.symbol || ''} ${o.phase || ''} ${o.opportunity_type || ''}`.toLowerCase().includes(key))
})

const filteredItems = computed(() => {
  const key = q.value.trim().toLowerCase()
  if (!key) return items.value
  return items.value.filter((item) => JSON.stringify(item).toLowerCase().includes(key))
})

const activeRegime = computed(() => mapRegime(overview.value.snapshot?.market_regime))

const lessonText = computed(() => overview.value.lesson?.content || overview.value.lesson?.title || '')

const statsText = computed(() => {
  const lesson = overview.value.lesson || {}
  const stats = overview.value.stats || {}
  const count = lesson.sample_size ?? stats.n
  const win = lesson.win_rate ?? stats.win_rate
  const avg = lesson.avg_return ?? stats.avg_return
  const dd = lesson.max_drawdown ?? stats.max_drawdown
  return {
    count: count == null || Number(count) === 0 ? '—' : `${count}次`,
    win: fmtRate(win),
    avg: fmtPct(avg),
    dd: fmtPct(dd),
  }
})

const sectionTitle = computed(() => nav.find((n) => n.id === section.value)?.label || '')

const abilityMissing = computed(() => (ability.value.skills || []).some((row: AnyRec) => row.score == null))

function mapRegime(raw: string | undefined) {
  const v = (raw || '').toUpperCase()
  if (['RECOVERY', '修复'].includes(v)) return 'RECOVERY'
  if (['RANGE', '震荡', 'ROTATION'].includes(v)) return 'RANGE'
  if (['HOT', 'BULL', 'BULL_TREND', '强势'].includes(v)) return 'HOT'
  if (['WEAK', 'BEAR', 'BEAR_TREND', 'COLD', 'PANIC', '弱势'].includes(v)) return 'WEAK'
  return ''
}

function fmtPct(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const pct = Math.abs(n) <= 1 ? n * 100 : n
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

function fmtRate(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const pct = Math.abs(n) <= 1 ? n * 100 : n
  return `${pct.toFixed(1)}%`
}

function bar(score: number) {
  const n = Math.max(0, Math.min(10, Math.round((score || 0) / 10)))
  return '█'.repeat(n) + '░'.repeat(10 - n)
}

function stars(score: unknown) {
  if (score == null || score === '') return '☆☆☆☆☆'
  let s = Number(score)
  if (Number.isNaN(s)) return '☆☆☆☆☆'
  if (s <= 1) s *= 5
  else if (s <= 10) s /= 2
  else s /= 20
  const n = Math.max(0, Math.min(5, Math.round(s)))
  return '★'.repeat(n) + '☆'.repeat(5 - n)
}

function oppLabel(opp: AnyRec) {
  const name = opp.sector_name || opp.name || opp.symbol || '机会'
  const kind = opp.phase ? `${opp.phase} · ${opp.opportunity_type || '机会'}` : (opp.opportunity_type || '机会')
  return `${name}  ${kind}`
}

function itemTitle(item: AnyRec) {
  return item.name || item.title || item.strategy_name || item.index_name || item.symbol || item.rule_code || '记录'
}

function itemBody(item: AnyRec) {
  return item.description || item.content || item.summary || item.hypothesis || item.market_regime || item.opportunity_type || ''
}

async function loadOverview() {
  const res = await axios.get('/kb/overview')
  overview.value = res.data || {}
}

async function loadLessons() {
  const res = await axios.get('/kb/section/lesson')
  items.value = res.data?.items || []
}

async function addLesson() {
  lessonSaving.value = true
  error.value = ''
  try {
    await axios.post('/kb/lessons', lessonForm.value)
    lessonForm.value = { title: '', content: '' }
    addingLesson.value = false
    await loadLessons()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '经验没有保存'
  } finally {
    lessonSaving.value = false
  }
}

async function removeLesson(item: AnyRec) {
  const name = itemTitle(item)
  if (!window.confirm(`删除经验「${name}」？原来的交易记录还在。`)) return
  error.value = ''
  try {
    await axios.delete(`/kb/lessons/${item.id}`)
    await loadLessons()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '删除失败'
  }
}

async function loadAbility() {
  const res = await axios.get('/kb/ability')
  ability.value = res.data || { skills: [], sample_size: 0, early: true }
}

async function openSection(id: string) {
  section.value = id
  error.value = ''
  if (id === 'home') {
    await loadOverview()
    return
  }
  if (id === 'lesson') {
    await loadLessons()
    return
  }
  if (id === 'ability') {
    await loadAbility()
    return
  }
  if (id === 'trades' || id === 'failures' || id === 'market' || id === 'opportunity' || id === 'rule' || id === 'external' || id === 'experiment') return
  const res = await axios.get(`/kb/section/${id}`)
  items.value = res.data?.items || []
}

onMounted(async () => {
  try {
    await loadOverview()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '知识库接口不可用'
  }
})
</script>
