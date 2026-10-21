<template>
  <section class="p-6 max-w-5xl">
    <div class="flex items-center justify-between gap-3 mb-2">
      <h2 class="text-base font-medium">机会库</h2>
      <button type="button" class="text-xs px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500" :disabled="scanning" @click="scan">
        {{ scanning ? '扫描中…' : '扫描最近5个交易日' }}
      </button>
    </div>
    <p class="text-xs text-zinc-500 mb-3">东财二级行业。启动和修复会标出来，退潮不收录。扫描时会补主力净额，并用成分股当日涨跌标出共振、龙头，以及涨后短暂回调的股票。</p>
    <p v-if="note" class="text-xs text-zinc-400 mb-3">{{ note }}</p>
    <p v-if="meta" class="text-xs text-zinc-500 mb-3">
      交易日 {{ meta.used }}<span v-if="meta.missing">，缺少 {{ meta.missing }}</span>
      · 退潮跳过 {{ meta.ebb }} · 写入 {{ meta.saved }}
    </p>
    <div class="flex flex-wrap gap-2 mb-4">
      <button
        v-for="chip in chips"
        :key="chip"
        type="button"
        class="px-2.5 py-1 rounded-full text-xs border"
        :class="filter === chip ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'"
        @click="filter = chip"
      >{{ chip }}</button>
    </div>
    <div v-if="error" class="mb-3 text-sm text-red-300">{{ error }}</div>
    <div v-if="!shown.length" class="text-sm text-zinc-500">还没有符合条件的板块。点扫描后，启动、修复和仍强势的板块会出现在这里。</div>
    <div v-else class="border border-zinc-800 rounded-xl overflow-hidden">
      <div class="grid grid-cols-[7rem_4.2rem_5.5rem_minmax(0,1fr)_4rem_4.5rem] gap-2 px-3 py-2 text-[11px] text-zinc-500 border-b border-zinc-800">
        <span>板块</span><span>时机</span><span>主类型</span><span>同时满足</span><span>强度</span><span>自身量比</span>
      </div>
      <div v-for="row in shown" :key="row.id || row.sector_code + row.trade_date" class="border-b border-zinc-800 last:border-b-0">
        <button type="button" class="w-full grid grid-cols-[7rem_4.2rem_5.5rem_minmax(0,1fr)_4rem_4.5rem] gap-2 px-3 py-2 text-left text-sm hover:bg-zinc-900" @click="openId = openId === rowKey(row) ? '' : rowKey(row)">
          <span class="text-zinc-100 truncate">{{ row.sector_name || row.name }}</span>
          <span :class="row.phase === '退潮' ? 'text-zinc-500' : 'text-amber-200'">{{ row.phase || '—' }}</span>
          <span class="text-zinc-300">{{ row.opportunity_type || '—' }}</span>
          <span class="text-xs text-zinc-500 truncate">{{ typeText(row) }}<span v-if="brief(row)" class="block text-zinc-600 truncate">{{ brief(row) }}</span></span>
          <span class="text-zinc-200">{{ num(row.sector_strength) }}</span>
          <span class="text-zinc-400">{{ ratio(row.amount_ratio) }}</span>
        </button>
        <div v-if="openId === rowKey(row)" class="px-3 pb-3 text-xs text-zinc-400 space-y-2">
          <div>{{ row.trade_date }} · {{ row.trigger_reason }}</div>
          <div v-if="row.market_regime">当时市场：{{ row.market_regime }}</div>
          <div v-if="stockLine(row, 'leader')">领涨：{{ stockLine(row, 'leader') }}</div>
          <div v-if="stockLine(row, 'peers')">跟涨：{{ stockLine(row, 'peers') }}</div>
          <div v-if="stockLine(row, 'pullbacks')">涨后回调：{{ stockLine(row, 'pullbacks') }}</div>
          <div v-if="pickLine(row)">模型推荐：{{ pickLine(row) }}</div>
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
const error = ref('')
const scanning = ref(false)
const note = ref('')
const meta = ref<any>(null)
const filter = ref('全部')
const openId = ref('')

const chips = computed(() => {
  const set = new Set<string>(['全部', '启动', '修复', '持续强势'])
  for (const row of items.value) {
    if (row.opportunity_type) set.add(row.opportunity_type)
  }
  return [...set]
})

const shown = computed(() => {
  const key = (props.query || '').trim().toLowerCase()
  return items.value.filter((row) => {
    if (filter.value === '启动' || filter.value === '修复' || filter.value === '持续强势') {
      if (row.phase !== filter.value) return false
    } else if (filter.value !== '全部' && row.opportunity_type !== filter.value && !(row.matched_types || []).includes(filter.value)) {
      return false
    }
    if (!key) return true
    return `${row.sector_name || ''} ${row.name || ''} ${row.opportunity_type || ''} ${row.phase || ''}`.toLowerCase().includes(key)
  })
})

function rowKey(row: any) {
  return String(row.id || `${row.sector_code}-${row.trade_date}`)
}
function typeText(row: any) {
  const list = row.matched_types
  if (Array.isArray(list) && list.length) return list.join('、')
  return row.opportunity_type || '—'
}
function num(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  return Number.isNaN(n) ? '—' : n.toFixed(0)
}
function evidenceOf(row: any) {
  return row?.evidence && typeof row.evidence === 'object' ? row.evidence : {}
}
function brief(row: any) {
  const ev = evidenceOf(row)
  const parts: string[] = []
  if (ev.up_num != null) parts.push(`上涨${ev.up_num}家`)
  if (ev.leading_name) {
    const pct = ev.leading_pct == null ? '' : `${Number(ev.leading_pct) >= 0 ? '+' : ''}${Number(ev.leading_pct).toFixed(2)}%`
    parts.push(`领涨${ev.leading_name}${pct}`)
  }
  if (ev.main_net != null) parts.push(`净额${yuan(ev.main_net)}`)
  return parts.join(' · ')
}
function yuan(v: unknown) {
  const n = Number(v)
  if (Number.isNaN(n)) return ''
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(0)}万`
  return n.toFixed(0)
}
function stockLine(row: any, key: string) {
  const stocks = evidenceOf(row).stocks || {}
  const one = (item: any) => {
    if (!item) return ''
    const pct = item.last_pct == null ? '' : `${Number(item.last_pct) >= 0 ? '+' : ''}${Number(item.last_pct).toFixed(2)}%`
    return `${item.name}${pct}`
  }
  if (key === 'leader') return one(stocks.leader)
  const list = stocks[key] || []
  return list.map(one).filter(Boolean).join('、')
}
function pickLine(row: any) {
  const picks = evidenceOf(row).review?.picks || []
  return picks.map((item: any) => `${item.name}（${item.role}）${item.reason ? '：' + item.reason : ''}`).join('；')
}
function ratio(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  return Number.isNaN(n) ? '—' : `${n.toFixed(2)}倍`
}

async function load() {
  const res = await axios.get('/kb/opportunities')
  items.value = res.data?.items || []
}

async function scan() {
  scanning.value = true
  error.value = ''
  try {
    const res = await axios.post('/kb/opportunities/scan', null, { timeout: 300000 })
    const backfill = res.data?.backfill
    const flow = res.data?.flow_backfill
    const backfillText = backfill?.assistant
      ? `补数：${backfill.assistant}`
      : (backfill?.note || '')
    const flowText = flow?.assistant
      ? `资金流向：${flow.assistant}`
      : (flow?.note || '')
    note.value = [backfillText, flowText, res.data?.note || ''].filter(Boolean).join(' ')
    meta.value = {
      used: (res.data?.used_dates || []).join('、'),
      missing: (res.data?.missing_dates || []).join('、'),
      ebb: res.data?.skipped_ebb ?? 0,
      saved: res.data?.saved ?? 0,
    }
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '扫描没有完成'
  } finally {
    scanning.value = false
  }
}

onMounted(load)
</script>
