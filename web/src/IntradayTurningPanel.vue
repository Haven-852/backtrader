<template>
  <div class="space-y-4">
    <!-- 日频次日高低点（全市场 LightGBM） -->
    <div class="bg-zinc-900 rounded-2xl border border-emerald-500/20 p-4 space-y-3">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h4 class="text-xs font-semibold text-zinc-200 flex items-center gap-2">
            <i class="fa-solid fa-calendar-day text-emerald-400"></i>
            次日高低点 · 日频 LightGBM
          </h4>
          <p class="text-[10px] text-zinc-500 mt-1 leading-relaxed max-w-3xl">
            收盘后读取该股在 <code class="text-zinc-400">ml_train_daily</code> 的当日特征；
            若无当日行，可自动经 datahubco <code class="text-zinc-400">fill_complete</code> + 截面 polish 入库后再预测。
            输出为次日高低相对今日收盘的参考价位与操作偏向。
          </p>
        </div>
        <button
          type="button"
          @click="loadNextdayStatus"
          class="px-3 py-1.5 rounded-lg text-[11px] bg-zinc-800 text-zinc-300 border border-zinc-700 hover:bg-zinc-700 flex-shrink-0"
        >刷新状态</button>
      </div>

      <div v-if="ndStatus" class="flex flex-wrap gap-2 text-[10px]">
        <span
          class="px-2 py-0.5 rounded border"
          :class="ndStatus.ok
            ? 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10'
            : 'text-rose-300 border-rose-500/40 bg-rose-500/10'"
        >{{ ndStatus.ok ? '模型已就绪' : '模型未找到' }}</span>
        <span v-if="ndStatus.mode" class="px-2 py-0.5 rounded border border-zinc-700 text-zinc-400">
          mode={{ ndStatus.mode }} · {{ ndStatus.n_features }} 特征
        </span>
        <span v-if="ndTest.y_high" class="px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 tabular-nums">
          Test IC 高 {{ icText(ndTest.y_high?.ic_mean) }} / 低 {{ icText(ndTest.y_low?.ic_mean) }}
          · MAE {{ maeText(ndTest.y_high?.mae) }} / {{ maeText(ndTest.y_low?.mae) }}
        </span>
        <span
          v-if="ndSched"
          class="px-2 py-0.5 rounded border"
          :class="ndSched.enabled
            ? 'text-sky-300 border-sky-500/40 bg-sky-500/10'
            : 'text-zinc-500 border-zinc-700'"
        >
          日更调度 {{ ndSched.enabled ? (ndSched.running ? '运行中' : '已启用未跑') : '未启用' }}
          <template v-if="ndSched.daily_at"> · {{ ndSched.daily_at }}</template>
          <template v-if="ndDailyProg"> · {{ ndDailyProg }}</template>
        </span>
      </div>

      <div class="flex flex-wrap items-end gap-2">
        <label class="space-y-1">
          <span class="text-[10px] text-zinc-500 block">股票代码</span>
          <input
            v-model="ndCode"
            type="text"
            placeholder="000001.SZ"
            class="w-40 px-2.5 py-1.5 rounded-lg text-[12px] bg-zinc-950 border border-zinc-700 text-zinc-200 tabular-nums focus:outline-none focus:border-emerald-500/50"
            @keyup.enter="runNextdayPredict"
          />
        </label>
        <label class="space-y-1">
          <span class="text-[10px] text-zinc-500 block">预测日</span>
          <input
            v-model="ndDate"
            type="date"
            class="w-[11.5rem] px-2.5 py-1.5 rounded-lg text-[12px] bg-zinc-950 border border-zinc-700 text-zinc-200 tabular-nums [color-scheme:dark] focus:outline-none focus:border-emerald-500/50"
            @click="openDatePicker"
          />
        </label>
        <label class="flex items-center gap-1.5 pb-1.5 text-[10px] text-zinc-400 select-none">
          <input v-model="ndAutoFill" type="checkbox" class="rounded border-zinc-600 bg-zinc-950" />
          无当日行则自动补数
        </label>
        <button
          type="button"
          @click="runNextdayPredict"
          :disabled="ndLoading || !ndCode.trim()"
          class="px-3 py-1.5 rounded-lg text-[11px] bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30 disabled:opacity-50"
        >
          <i class="fa-solid fa-spinner animate-spin mr-1" v-if="ndLoading"></i>
          {{ ndLoadingLabel }}
        </button>
        <button
          type="button"
          @click="runDailyBatch"
          :disabled="ndBatchLoading"
          class="px-3 py-1.5 rounded-lg text-[11px] bg-zinc-800 text-zinc-300 border border-zinc-700 hover:bg-zinc-700 disabled:opacity-50"
          title="手动灌一批全市场当日特征"
        >
          <i class="fa-solid fa-spinner animate-spin mr-1" v-if="ndBatchLoading"></i>
          {{ ndBatchLoading ? '灌数中…' : '手动日更一批' }}
        </button>
      </div>
      <p v-if="ndFillMsg" class="text-[11px] text-sky-300/90">{{ ndFillMsg }}</p>
      <p v-if="ndError" class="text-[11px] text-red-300">{{ ndError }}</p>

      <div v-if="ndResult?.ok" class="space-y-3 pt-1">
        <div class="flex flex-wrap items-center gap-2 text-[11px]">
          <span class="text-zinc-300 tabular-nums">{{ ndResult.symbol }}</span>
          <span class="text-zinc-500">特征日 {{ ndResult.trade_date }}</span>
          <span class="text-zinc-500 tabular-nums">收盘 {{ ndResult.asof_close }}</span>
          <span
            class="px-2 py-0.5 rounded border text-[10px]"
            :class="biasClass"
          >{{ ndAdvice.bias }} · {{ ndAdvice.action }}</span>
          <span v-if="ndResult.session_flag && ndResult.session_flag !== '正常'" class="text-amber-300 text-[10px]">
            {{ ndResult.session_flag }}
          </span>
          <span v-if="ndResult.auto_fill?.filled" class="text-[10px] text-sky-400/90">
            {{ ndResult.auto_fill.ok ? '已自动补数' : '补数未完全成功' }}
          </span>
          <span v-if="ndResult.fallback_latest" class="text-[10px] text-amber-400/90">已回退到库内最新特征日</span>
        </div>

        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px]">
          <div class="bg-zinc-950/60 rounded-lg p-2.5 border border-zinc-800">
            <div class="text-zinc-500 mb-0.5">预测次日最高</div>
            <div class="text-emerald-300 text-sm tabular-nums font-medium">{{ ndLevels.pred_high ?? '—' }}</div>
            <div class="text-zinc-500 tabular-nums mt-0.5">+{{ ndPred.y_high_pct ?? '—' }}% vs 收盘</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2.5 border border-zinc-800">
            <div class="text-zinc-500 mb-0.5">预测次日最低</div>
            <div class="text-rose-300 text-sm tabular-nums font-medium">{{ ndLevels.pred_low ?? '—' }}</div>
            <div class="text-zinc-500 tabular-nums mt-0.5">{{ ndPred.y_low_pct ?? '—' }}% vs 收盘</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2.5 border border-zinc-800">
            <div class="text-zinc-500 mb-0.5">低吸观察区</div>
            <div class="text-zinc-200 tabular-nums">{{ zoneText(ndLevels.buy_zone) }}</div>
            <div class="text-zinc-600 mt-0.5">含 MAE 误差带</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2.5 border border-zinc-800">
            <div class="text-zinc-500 mb-0.5">冲高减仓区</div>
            <div class="text-zinc-200 tabular-nums">{{ zoneText(ndLevels.sell_zone) }}</div>
            <div class="text-zinc-600 mt-0.5">含 MAE 误差带</div>
          </div>
        </div>

        <p class="text-[11px] text-zinc-300 leading-relaxed bg-zinc-950/40 rounded-lg px-3 py-2 border border-zinc-800/80">
          {{ ndAdvice.summary }}
        </p>
        <p class="text-[10px] text-zinc-600 leading-relaxed">
          {{ ndAdvice.disclaimer }}
          <span v-if="ndResult.feature_quality?.warn" class="text-amber-400/90">
            · 特征缺失 {{ ndResult.feature_quality.missing_count }} 项，结果可信度下降。
          </span>
        </p>

        <details class="text-[10px] text-zinc-500">
          <summary class="cursor-pointer text-zinc-400 hover:text-zinc-300">收盘后需要哪些数据？</summary>
          <ul class="mt-2 space-y-1 list-disc list-inside text-zinc-500 leading-relaxed">
            <li v-for="(item, i) in (ndResult.required_inputs?.must_exist_after_close || ndInputs)" :key="i">{{ item }}</li>
          </ul>
          <p class="mt-2 text-zinc-600">{{ ndResult.required_inputs?.label_note }}</p>
        </details>
      </div>
    </div>

    <!-- 分钟线日内转折（原有） -->
    <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
      <div class="flex items-start justify-between gap-3">
        <div>
          <h4 class="text-xs font-semibold text-zinc-200 flex items-center gap-2">
            <i class="fa-solid fa-chart-line text-sky-400"></i>
            日内转折 · 分钟 LightGBM
          </h4>
          <p class="text-[10px] text-zinc-500 mt-1 leading-relaxed">
            与上方「次日高低点」不同：这里用 5 分钟 K 线，预测未来 3 根（15 分钟）先碰到上涨/下跌障碍还是两边都没碰到。
            障碍幅度 0.5×ATR，不是精确点位。
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

function shanghaiYmd(offsetDays = 0): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const y = Number(parts.find((p) => p.type === 'year')?.value)
  const m = Number(parts.find((p) => p.type === 'month')?.value)
  const d = Number(parts.find((p) => p.type === 'day')?.value)
  const dt = new Date(Date.UTC(y, m - 1, d + offsetDays))
  return dt.toISOString().slice(0, 10)
}

function openDatePicker(e: Event) {
  const el = e.currentTarget as HTMLInputElement
  try {
    el.showPicker?.()
  } catch {
    /* 不支持 showPicker 时仍可用系统日历图标 */
  }
}

const ndCode = ref('')
const ndDate = ref(shanghaiYmd(1))
const ndAutoFill = ref(true)
const ndLoading = ref(false)
const ndBatchLoading = ref(false)
const ndError = ref('')
const ndFillMsg = ref('')
const ndStatus = ref<any>(null)
const ndResult = ref<any>(null)
const ndInputs = [
  '当日 OHLCV / 成交额',
  '滚动收益、均线比、ATR%、振幅、影线、换手',
  '市值、板块、session_flag',
  '市场与行业截面特征',
]

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

const ndTest = computed(() => ndStatus.value?.test || {})
const ndSched = computed(() => ndStatus.value?.scheduler || null)
const ndDailyProg = computed(() => {
  const d = ndSched.value?.state?.daily
  if (!d?.asof) return ''
  if (d.done) return `${d.asof} 已完成`
  const tot = d.universe_total
  const off = d.offset
  if (tot) return `${d.asof} ${off}/${tot}`
  return `${d.asof} offset=${off ?? 0}`
})
const ndLoadingLabel = computed(() => {
  if (!ndLoading.value) return '预测次日高低点'
  return ndAutoFill.value ? '补数/预测中…' : '预测中…'
})
const ndAdvice = computed(() => ndResult.value?.advice || {})
const ndLevels = computed(() => ndResult.value?.levels || {})
const ndPred = computed(() => ndResult.value?.pred || {})
const biasClass = computed(() => {
  const c = ndAdvice.value?.bias_code
  if (c === 'bullish') return 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10'
  if (c === 'bearish') return 'text-rose-300 border-rose-500/40 bg-rose-500/10'
  if (c === 'narrow') return 'text-zinc-400 border-zinc-600 bg-zinc-800/60'
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
function icText(v: any) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(2)
}
function maeText(v: any) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(3)
}
function zoneText(z: any) {
  if (!Array.isArray(z) || z.length < 2) return '—'
  return `${Number(z[0]).toFixed(2)} – ${Number(z[1]).toFixed(2)}`
}
function detailMsg(data: any, fallback: string) {
  const d = data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x: any) => x?.msg || JSON.stringify(x)).join('; ')
  return fallback
}

async function loadNextdayStatus() {
  try {
    const res = await fetch('/strategy/nextday-hl/status')
    const data = await res.json().catch(() => ({}))
    if (res.ok) ndStatus.value = data
  } catch (_) { /* ignore */ }
}

async function runNextdayPredict() {
  ndLoading.value = true
  ndError.value = ''
  ndFillMsg.value = ''
  ndResult.value = null
  try {
    const body: Record<string, any> = {
      ts_code: ndCode.value.trim().toUpperCase(),
      auto_fill: ndAutoFill.value,
    }
    if (ndDate.value && ndDate.value <= shanghaiYmd(0)) body.trade_date = ndDate.value
    const res = await fetch('/strategy/nextday-hl/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(detailMsg(data, '预测失败'))
    ndResult.value = data
    const af = data.auto_fill
    if (af?.filled) {
      ndFillMsg.value = af.message || (af.ok ? '已自动补数并 polish' : '自动补数未完全成功')
    } else if (af && af.filled === false) {
      ndFillMsg.value = '特征行已存在，未重新拉取'
    }
  } catch (e: any) {
    ndError.value = e?.message || '预测失败'
  } finally {
    ndLoading.value = false
  }
}

async function runDailyBatch() {
  ndBatchLoading.value = true
  ndError.value = ''
  try {
    const res = await fetch('/strategy/nextday-hl/scheduler/daily-batch', { method: 'POST' })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(detailMsg(data, '日更失败'))
    const p = data.progress || {}
    ndFillMsg.value = data.done
      ? `日更完成 ${data.asof}（ok=${p.ok_count ?? 0}）`
      : `日更一批 ${data.asof} offset=${data.offset}/${data.universe_total ?? '?'}`
    await loadNextdayStatus()
  } catch (e: any) {
    ndError.value = e?.message || '日更失败'
  } finally {
    ndBatchLoading.value = false
  }
}

async function loadCoverage() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/strategy/intraday/coverage')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(detailMsg(data, '读取分钟覆盖失败'))
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
    if (!res.ok) throw new Error(detailMsg(data, '训练失败'))
    run.value = data
  } catch (e: any) {
    error.value = e?.message || '训练失败'
  } finally {
    training.value = false
    loading.value = false
  }
}

onMounted(() => {
  loadNextdayStatus()
  loadCoverage()
  loadLatest()
})
</script>
