<template>
  <div class="space-y-4">
    <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h4 class="text-xs font-semibold text-zinc-200 flex items-center gap-2">
            <i class="fa-solid fa-chart-line text-sky-400"></i>
            日内高低点 · LightGBM
          </h4>
          <p class="text-[10px] text-zinc-500 mt-1 leading-relaxed">
            5 分钟 K 线，预测未来 3 根（15 分钟）先碰到上涨障碍、下跌障碍，还是两边都没碰到。
            上涨/下跌空间用 0.5×ATR 障碍幅度，不是模型报出的精确点位。只读库里的分钟线。
          </p>
        </div>
        <div class="flex items-center gap-1.5 flex-shrink-0">
          <button
            type="button"
            @click="loadCoverage"
            :disabled="loading"
            class="px-3 py-1.5 rounded-lg text-[11px] bg-zinc-800 text-zinc-300 border border-zinc-700 hover:bg-zinc-700 disabled:opacity-50"
          >刷新覆盖</button>
          <button
            type="button"
            @click="train"
            :disabled="loading"
            class="px-3 py-1.5 rounded-lg text-[11px] bg-sky-600/20 text-sky-300 border border-sky-500/30 hover:bg-sky-600/30 disabled:opacity-50"
          >
            <i class="fa-solid fa-spinner animate-spin mr-1" v-if="training"></i>
            {{ training ? '训练中…' : '用推荐集训练' }}
          </button>
        </div>
      </div>
      <p v-if="error" class="text-[11px] text-red-300">{{ error }}</p>
      <p v-if="summary" class="text-[11px] text-zinc-400 leading-relaxed">
        库内有分钟线的股票 {{ summary.stocks_with_minutes }} 只、{{ summary.minute_bars }} 根。
        适合这版训练集的 {{ summary.recommended }} 只（{{ summary.rule }}）
      </p>
      <p class="text-[10px] text-zinc-600">{{ summary?.price_note }}</p>
    </div>

    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 min-h-0">
        <h5 class="text-[11px] font-semibold text-zinc-300 mb-2">可用于训练的股票和时间</h5>
        <div class="max-h-80 overflow-auto scrollbar-thin">
          <table class="w-full text-[10px] text-left">
            <thead class="text-zinc-500 sticky top-0 bg-zinc-900">
              <tr>
                <th class="py-1 pr-2">代码</th>
                <th class="py-1 pr-2">名称</th>
                <th class="py-1 pr-2">起始</th>
                <th class="py-1 pr-2">结束</th>
                <th class="py-1 pr-2">天数</th>
                <th class="py-1 pr-2">分钟条数</th>
                <th class="py-1">建议</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in recommended" :key="r.ts_code" class="border-t border-zinc-800/80 text-zinc-300">
                <td class="py-1 pr-2 tabular-nums">{{ r.ts_code }}</td>
                <td class="py-1 pr-2">{{ r.name || '—' }}</td>
                <td class="py-1 pr-2 tabular-nums">{{ r.start }}</td>
                <td class="py-1 pr-2 tabular-nums">{{ r.end }}</td>
                <td class="py-1 pr-2 tabular-nums">{{ r.days }}</td>
                <td class="py-1 pr-2 tabular-nums">{{ r.bars }}</td>
                <td class="py-1" :class="r.tier === '优先' ? 'text-emerald-400' : 'text-amber-300'">{{ r.tier }}</td>
              </tr>
              <tr v-if="!recommended.length">
                <td colspan="7" class="py-4 text-zinc-500">还没有读到覆盖，点「刷新覆盖」。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
        <h5 class="text-[11px] font-semibold text-zinc-300 mb-2">推荐集里有分钟线的交易日</h5>
        <div class="max-h-80 overflow-auto scrollbar-thin">
          <table class="w-full text-[10px] text-left">
            <thead class="text-zinc-500 sticky top-0 bg-zinc-900">
              <tr>
                <th class="py-1 pr-2">交易日</th>
                <th class="py-1 pr-2">股票数</th>
                <th class="py-1">分钟条数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in dates" :key="d.trade_date" class="border-t border-zinc-800/80 text-zinc-300">
                <td class="py-1 pr-2 tabular-nums">{{ d.trade_date }}</td>
                <td class="py-1 pr-2 tabular-nums">{{ d.stocks }}</td>
                <td class="py-1 tabular-nums">{{ d.bars }}</td>
              </tr>
              <tr v-if="!dates.length">
                <td colspan="3" class="py-4 text-zinc-500">暂无。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div v-if="run" class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
      <div class="flex items-center gap-2">
        <span class="text-[11px] px-2 py-0.5 rounded border" :class="statusClass">{{ run.status }}</span>
        <span class="text-[10px] text-zinc-500">{{ run.model_version }}</span>
      </div>
      <p class="text-[11px] text-zinc-300 leading-relaxed">{{ reason }}</p>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px]">
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500">训练 / 验证 / 测试</div>
          <div class="text-zinc-200 tabular-nums">{{ metrics.train_rows ?? '—' }} / {{ metrics.validation_rows ?? '—' }} / {{ metrics.test_rows ?? '—' }}</div>
        </div>
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500">测试交易日</div>
          <div class="text-zinc-200 tabular-nums">{{ metrics.test_days ?? '—' }}</div>
        </div>
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500">模糊标签占比</div>
          <div class="text-zinc-200 tabular-nums">{{ ambiguousText }}</div>
        </div>
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500">测试准确率 / Brier</div>
          <div class="text-zinc-200 tabular-nums">{{ pct(cls.accuracy) }} / {{ num(cls.brier) }}</div>
        </div>
      </div>
      <div class="overflow-auto">
        <table class="w-full text-[10px] text-left">
          <thead class="text-zinc-500">
            <tr>
              <th class="py-1 pr-2">类别</th>
              <th class="py-1 pr-2">Precision</th>
              <th class="py-1 pr-2">Recall</th>
              <th class="py-1 pr-2">F1</th>
              <th class="py-1">样本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="name in classNames" :key="name" class="border-t border-zinc-800 text-zinc-300">
              <td class="py-1 pr-2">{{ name }}</td>
              <td class="py-1 pr-2 tabular-nums">{{ pct(cls.per_class?.[name]?.precision) }}</td>
              <td class="py-1 pr-2 tabular-nums">{{ pct(cls.per_class?.[name]?.recall) }}</td>
              <td class="py-1 pr-2 tabular-nums">{{ pct(cls.per_class?.[name]?.f1) }}</td>
              <td class="py-1 tabular-nums">{{ cls.per_class?.[name]?.support ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px]">
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500 mb-1">模型交易（扣费后）</div>
          <div class="text-zinc-300">净收益 {{ pct(trade.net_return) }} · 期望 {{ pct(trade.expectancy) }}</div>
          <div class="text-zinc-500">胜率 {{ pct(trade.win_rate) }} · 盈亏比 {{ num(trade.payoff) }} · 回撤 {{ pct(trade.max_drawdown) }} · {{ trade.trade_count ?? 0 }} 笔 · 未成交 {{ trade.unfilled ?? 0 }}</div>
        </div>
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500 mb-1">RSI 基线</div>
          <div class="text-zinc-300">净收益 {{ pct(rsi.net_return) }} · 期望 {{ pct(rsi.expectancy) }}</div>
          <div class="text-zinc-500">{{ rsi.trade_count ?? 0 }} 笔</div>
        </div>
        <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
          <div class="text-zinc-500 mb-1">VWAP 基线</div>
          <div class="text-zinc-300">净收益 {{ pct(vwap.net_return) }} · 期望 {{ pct(vwap.expectancy) }}</div>
          <div class="text-zinc-500">{{ vwap.trade_count ?? 0 }} 笔</div>
        </div>
      </div>
      <p class="text-[10px] text-zinc-600 leading-relaxed">
        信号在 5 分钟 K 线走完后才产生，成交价用下一根开盘价。当天新买的股票当天不能卖。
        涨跌停、停牌和缺少前收盘时不算成交。阈值来自验证集，测试集只评一次。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

const loading = ref(false)
const training = ref(false)
const error = ref('')
const summary = ref<any>(null)
const recommended = ref<any[]>([])
const dates = ref<any[]>([])
const run = ref<any>(null)
const classNames = ['UP_FIRST', 'DOWN_FIRST', 'NEUTRAL']

const metrics = computed(() => run.value?.metrics || {})
const cls = computed(() => metrics.value.classification_test || {})
const trade = computed(() => metrics.value.trading_test || {})
const rsi = computed(() => metrics.value.baseline_rsi || {})
const vwap = computed(() => metrics.value.baseline_vwap || {})
const reason = computed(() => run.value?.reason || metrics.value.reason || '')
const ambiguousText = computed(() => {
  const share = metrics.value.label_share?.all
  if (!share || share.ambiguous_ratio == null) return '—'
  return `${(share.ambiguous_ratio * 100).toFixed(1)}%`
})
const statusClass = computed(() => {
  const s = run.value?.status
  if (s === 'CANDIDATE') return 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10'
  if (s === 'NOT_EFFECTIVE') return 'text-rose-300 border-rose-500/40 bg-rose-500/10'
  return 'text-amber-300 border-amber-500/40 bg-amber-500/10'
})

function pct(v: any) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return `${(Number(v) * 100).toFixed(2)}%`
}
function num(v: any) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(3)
}

async function loadCoverage() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/strategy/intraday/coverage')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '读取分钟覆盖失败')
    summary.value = data.summary
    recommended.value = data.recommended || []
    dates.value = data.dates || []
  } catch (e: any) {
    error.value = e?.message || '读取分钟覆盖失败'
  } finally {
    loading.value = false
  }
}

async function loadLatest() {
  try {
    const res = await fetch('/strategy/intraday/latest')
    const data = await res.json().catch(() => ({}))
    if (res.ok && data.run) {
      run.value = {
        ...data.run,
        reason: data.run.metrics?.reason,
      }
    }
  } catch (_) { /* 没有历史训练也不挡住覆盖表 */ }
}

async function train() {
  training.value = true
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/strategy/intraday/train', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '训练失败')
    run.value = data
  } catch (e: any) {
    error.value = e?.message || '训练失败'
  } finally {
    training.value = false
    loading.value = false
  }
}

onMounted(() => {
  loadCoverage()
  loadLatest()
})
</script>
