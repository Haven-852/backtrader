<template>
  <section class="p-6 max-w-5xl">
    <div class="flex items-center justify-between gap-3 mb-2">
      <h2 class="text-base font-medium">外部知识</h2>
      <button type="button" class="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700" @click="adding = !adding">
        {{ adding ? '收起' : '读入一篇' }}
      </button>
    </div>
    <p class="text-xs text-zinc-500 mb-3">{{ meta.note || '外部知识只是候选。它记录别人发现了什么，不会直接变成交易规则。' }}</p>
    <p class="text-[11px] text-zinc-600 mb-3">当前体系：{{ meta.user_system }}</p>

    <form v-if="adding" class="mb-4 border border-zinc-800 rounded-xl p-4 space-y-3" @submit.prevent="readIn">
      <div class="grid grid-cols-2 gap-3">
        <label class="text-xs text-zinc-400">标题
          <input v-model="form.title" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100" />
        </label>
        <label class="text-xs text-zinc-400">作者
          <input v-model="form.author" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100" />
        </label>
        <label class="text-xs text-zinc-400">谁提供的
          <select v-model="form.source_type" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100">
            <option v-for="item in meta.source_types" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </label>
        <label class="text-xs text-zinc-400">讲的是什么
          <select v-model="form.knowledge_type" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100">
            <option v-for="item in meta.knowledge_types" :key="item.id" :value="item.id">{{ item.label }}</option>
          </select>
        </label>
      </div>
      <label class="block text-xs text-zinc-400">来源链接
        <input v-model="form.source_url" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5 text-sm text-zinc-100" />
      </label>
      <label class="block text-xs text-zinc-400">原文
        <textarea v-model="form.content" required rows="8" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100" placeholder="贴上论文、文章、访谈或交易员的原话"></textarea>
      </label>
      <button type="submit" class="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500" :disabled="reading">{{ reading ? '正在阅读…' : '阅读并提取观点' }}</button>
    </form>

    <div class="flex flex-wrap gap-2 mb-2">
      <button v-for="item in sourceChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="source === item.id ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'" @click="source = item.id">{{ item.label }}</button>
    </div>
    <div class="flex flex-wrap gap-2 mb-2">
      <button v-for="item in knowledgeChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="kind === item.id ? 'bg-zinc-700 text-zinc-100 border-zinc-500' : 'border-zinc-800 text-zinc-500'" @click="kind = item.id">{{ item.label }}</button>
    </div>
    <div class="flex flex-wrap gap-2 mb-4">
      <button v-for="item in evidenceChips" :key="item.id" type="button" class="px-2.5 py-1 rounded-full text-xs border" :class="evidence === item.id ? 'bg-amber-500/15 text-amber-100 border-amber-500/40' : 'border-zinc-800 text-zinc-500'" @click="evidence = item.id">{{ item.label }}</button>
    </div>
    <p v-if="error" class="mb-3 text-sm text-red-300">{{ error }}</p>
    <div v-if="!shown.length" class="text-sm text-zinc-500">还没有外部知识。读入一篇之后，这里会留下观点、方法、条件和证据等级，而不是一篇可以照做的规则。</div>
    <div v-else class="border border-zinc-800 rounded-xl overflow-hidden">
      <div v-for="row in shown" :key="row.id" class="border-b border-zinc-800 last:border-b-0">
        <button type="button" class="w-full px-3 py-2 text-left hover:bg-zinc-900" @click="toggle(row)">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="text-zinc-100">{{ row.title }}</span>
            <span class="text-xs text-zinc-400">{{ row.source_label }} · {{ row.knowledge_label }} · {{ row.evidence_label }}</span>
          </div>
          <div class="text-[11px] text-zinc-500 mt-1">适用 {{ applyText(row) }} · {{ row.summary }}</div>
        </button>
        <div v-if="openId === row.id" class="px-3 pb-3 text-xs text-zinc-400 space-y-2">
          <div v-if="detailLoading">正在打开…</div>
          <template v-else-if="detail">
            <div v-if="detail.author || detail.source_url">来源：{{ detail.author || '未署名' }} <span v-if="detail.source_url">· {{ detail.source_url }}</span></div>
            <div>证据说明：{{ detail.credibility_notes || '—' }}</div>
            <div>适用范围：{{ scopeText(detail.market_scope) }}</div>
            <div class="text-zinc-200">观点</div>
            <div v-for="claim in detail.claims || []" :key="claim.id" class="border border-zinc-800 rounded-lg p-2 space-y-1">
              <div>{{ claim.id }} · {{ claim.kind_label }} · {{ claim.statement }}</div>
              <div v-if="(claim.conditions || []).length">条件：{{ claim.conditions.join('；') }}</div>
              <button v-if="claim.testable" type="button" class="px-2 py-1 rounded bg-zinc-800 text-zinc-200" @click="toLab(claim)">送进实验室</button>
              <span v-else>还没有可观察条件，先不进实验室。</span>
            </div>
            <div v-if="(detail.strategies || []).length" class="text-zinc-200">方法</div>
            <div v-for="method in detail.strategies || []" :key="method.name">
              {{ method.name }}
              <span v-if="method.entry"> · 买 {{ method.entry }}</span>
              <span v-if="method.exit"> · 卖 {{ method.exit }}</span>
              <span v-if="method.position"> · 仓 {{ method.position }}</span>
              <span v-if="method.risk"> · 风险 {{ method.risk }}</span>
            </div>
            <div v-if="(detail.risks || []).length">风险：{{ detail.risks.join('；') }}</div>
            <div class="text-zinc-200">追溯</div>
            <div v-if="!(detail.experiments || []).length && !(detail.experience_links || []).length">还没有实验，也还没有连到我的经验。</div>
            <div v-for="exp in detail.experiments || []" :key="exp.id">{{ exp.claim_id || '观点' }} → 实验室 · {{ exp.status }} · {{ exp.hypothesis }}</div>
            <div v-for="link in detail.experience_links || []" :key="link.id">{{ link.claim_id || '全文' }} → {{ link.relationship_label }} · {{ link.title }}</div>
            <div v-for="rule in detail.rules || []" :key="rule.rule_code">经验之后才出现规则 {{ rule.rule_code }} {{ rule.name }}（{{ rule.status }}）</div>
            <div class="flex flex-wrap gap-2 items-center pt-1">
              <select v-model="link.experience_id" class="bg-zinc-950 border border-zinc-800 rounded px-2 py-1">
                <option value="">关联一条我的经验</option>
                <option v-for="lesson in meta.lessons" :key="lesson.id" :value="lesson.id">{{ lesson.title }}</option>
              </select>
              <select v-model="link.relationship" class="bg-zinc-950 border border-zinc-800 rounded px-2 py-1">
                <option v-for="item in meta.relations" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
              <button type="button" class="px-2 py-1 rounded bg-zinc-800" @click="linkLesson">记下关系</button>
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
const meta = ref<any>({ source_types: [], knowledge_types: [], evidence_levels: [], relations: [], lessons: [], note: '', user_system: '' })
const error = ref('')
const adding = ref(false)
const reading = ref(false)
const source = ref('ALL')
const kind = ref('ALL')
const evidence = ref('ALL')
const openId = ref('')
const detail = ref<any>(null)
const detailLoading = ref(false)
const form = ref({ title: '', author: '', source_type: 'ARTICLE', knowledge_type: 'STRATEGY', source_url: '', content: '' })
const link = ref({ experience_id: '', relationship: 'INSPIRES' })

const shown = computed(() => {
  const key = (props.query || '').trim().toLowerCase()
  return items.value.filter((row) => {
    if (source.value !== 'ALL' && row.source_type !== source.value) return false
    if (kind.value !== 'ALL' && row.knowledge_type !== kind.value) return false
    if (evidence.value !== 'ALL' && row.evidence_level !== evidence.value) return false
    if (!key) return true
    return `${row.title || ''} ${row.summary || ''} ${row.author || ''}`.toLowerCase().includes(key)
  })
})
const sourceChips = computed(() => [{ id: 'ALL', label: '全部' }, ...(meta.value.source_types || [])])
const knowledgeChips = computed(() => [{ id: 'ALL', label: '全部类型' }, ...(meta.value.knowledge_types || []).filter((item: any) => ['STRATEGY', 'ENTRY_METHOD', 'EXIT_METHOD', 'STOCK_SELECTION', 'SECTOR_ANALYSIS', 'MARKET_REGIME', 'POSITION_MANAGEMENT', 'RISK_MANAGEMENT', 'PSYCHOLOGY', 'CASE_STUDY'].includes(item.id))])
const evidenceChips = computed(() => [{ id: 'ALL', label: '全部证据' }, ...(meta.value.evidence_levels || [])])

function applyText(row: any) {
  const level = row.applicability?.level || '—'
  const map: Record<string, string> = { HIGH: '高', MEDIUM: '中', LOW: '低' }
  return map[level] || level
}
function scopeText(scope: any) {
  if (!scope) return '—'
  return [scope.market, (scope.style || []).join('/') , scope.holding_period].filter(Boolean).join(' · ')
}

async function load() {
  const [list, info] = await Promise.all([axios.get('/kb/external'), axios.get('/kb/external/meta')])
  items.value = list.data?.items || []
  meta.value = info.data || meta.value
}
async function readIn() {
  reading.value = true
  error.value = ''
  try {
    const res = await axios.post('/kb/external/read', form.value, { timeout: 120000 })
    form.value = { title: '', author: '', source_type: 'ARTICLE', knowledge_type: 'STRATEGY', source_url: '', content: '' }
    adding.value = false
    await load()
    if (res.data?.item?.id) await toggle({ id: res.data.item.id })
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有读入'
  } finally {
    reading.value = false
  }
}
async function toggle(row: any) {
  if (openId.value === row.id) {
    openId.value = ''
    detail.value = null
    return
  }
  openId.value = row.id
  detail.value = null
  detailLoading.value = true
  error.value = ''
  try {
    const res = await axios.get(`/kb/external/${row.id}`)
    detail.value = res.data?.item || null
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有打开'
  } finally {
    detailLoading.value = false
  }
}
async function toLab(claim: any) {
  if (!detail.value) return
  error.value = ''
  try {
    const id = detail.value.id
    await axios.post(`/kb/external/${id}/claims/${claim.id}/experiment`)
    const res = await axios.get(`/kb/external/${id}`)
    detail.value = res.data?.item || detail.value
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有进入实验室'
  }
}
async function linkLesson() {
  if (!detail.value) return
  error.value = ''
  try {
    const res = await axios.post(`/kb/external/${detail.value.id}/experiences`, link.value)
    detail.value = res.data?.item || detail.value
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '没有记下关系'
  }
}
onMounted(load)
</script>
