<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-3xl">
      <div class="flex items-start justify-between gap-3 mb-1">
        <div>
          <h2 class="text-base font-medium">市场记忆</h2>
          <p class="text-xs text-zinc-500 mt-1">记录每天市场发生了什么，以及后来证明了什么。事实和判断分开存放。</p>
        </div>
        <button type="button" class="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500" @click="adding = !adding">
          {{ adding ? '收起' : '+ 添加记忆' }}
        </button>
      </div>

      <div class="flex flex-wrap gap-2 my-4">
        <button v-for="tab in spans" :key="tab.id" type="button" class="px-3 py-1 rounded-full text-xs border"
          :class="span === tab.id && view !== 'similar' ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'"
          @click="setSpan(tab.id)">{{ tab.label }}</button>
        <button type="button" class="px-3 py-1 rounded-full text-xs border"
          :class="view === 'similar' ? 'bg-sky-500/20 text-sky-200 border-sky-500/40' : 'border-zinc-700 text-zinc-400'"
          @click="view = view === 'similar' ? 'day' : 'similar'">相似市场</button>
      </div>

      <div v-if="reminders.length" class="mb-4 border border-amber-500/40 bg-amber-500/10 rounded-xl px-4 py-3 text-sm">
        <div class="text-amber-200 mb-1">验证日已到</div>
        <div v-for="item in reminders" :key="item.hypothesis_code" class="text-xs text-zinc-200 mt-1">
          {{ slash(item.verify_on) }} · #{{ item.hypothesis_code }} · {{ item.statement }}
        </div>
      </div>

      <div v-if="error" class="mb-4 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">{{ error }}</div>

      <form v-if="adding" class="mb-5 border border-zinc-800 rounded-xl p-4 space-y-3 text-sm" @submit.prevent="save">
        <div class="text-xs text-zinc-500">事实写在数据里。状态、总结和待验证判断分开写，不会因为写了一句判断就变成经验。</div>
        <label class="block text-xs text-zinc-400">交易日
          <input v-model="form.trade_date" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
        </label>
        <div class="grid grid-cols-2 gap-2">
          <label v-for="f in factFields" :key="f.key" class="text-xs text-zinc-400">{{ f.label }}
            <input v-model="form.facts[f.key]" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
          </label>
        </div>
        <label class="block text-xs text-zinc-400">事实简述
          <input v-model="form.facts.fact_note" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" placeholder="沪深主要指数震荡，成交额较前一日……" />
        </label>
        <div class="border-t border-zinc-800 pt-3 text-xs text-zinc-500">市场状态（判断，不是事实）</div>
        <div class="grid grid-cols-2 gap-2">
          <label class="text-xs text-zinc-400">状态
            <input v-model="form.regime.label" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" placeholder="震荡修复" />
          </label>
          <label class="text-xs text-zinc-400">置信度 0-100
            <input v-model="form.regime.confidence" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
          </label>
        </div>
        <label class="block text-xs text-zinc-400">依据，一行一条
          <textarea v-model="form.regime.evidence" rows="3" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5"></textarea>
        </label>
        <label class="block text-xs text-zinc-400">市场结构
          <textarea v-model="form.structure.note" rows="2" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" placeholder="强势板块轮动，权重……"></textarea>
        </label>
        <div class="grid grid-cols-2 gap-2">
          <label class="text-xs text-zinc-400">热点名称
            <input v-model="form.sector.name" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
          </label>
          <label class="text-xs text-zinc-400">涨跌幅 %
            <input v-model="form.sector.change_pct" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
          </label>
          <label class="text-xs text-zinc-400">今日强度
            <input v-model="form.sector.strength" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
          </label>
          <label class="text-xs text-zinc-400">生命周期
            <select v-model="form.sector.phase" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5">
              <option v-for="p in phases" :key="p">{{ p }}</option>
            </select>
          </label>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <label class="text-xs text-zinc-400">事件时间
            <input v-model="form.event.time" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" placeholder="09:42" />
          </label>
          <label class="text-xs text-zinc-400">事件
            <input v-model="form.event.title" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
          </label>
        </div>
        <label class="block text-xs text-zinc-400">AI 市场总结
          <textarea v-model="form.summary.text" rows="3" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5"></textarea>
        </label>
        <label class="block text-xs text-zinc-400">总结依据，一行一条
          <textarea v-model="form.summary.evidence" rows="3" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5"></textarea>
        </label>
        <div class="border-t border-zinc-800 pt-3 text-xs text-zinc-500">待验证判断。验证之前不会写进经验。</div>
        <label class="block text-xs text-zinc-400">判断
          <textarea v-model="form.hypothesis.statement" rows="2" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5"></textarea>
        </label>
        <label class="block text-xs text-zinc-400">验证条件，一行一条
          <textarea v-model="form.hypothesis.conditions" rows="2" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5"></textarea>
        </label>
        <label class="block text-xs text-zinc-400">验证日期
          <input v-model="form.hypothesis.verify_on" type="date" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-1.5" />
        </label>
        <button type="submit" class="px-3 py-1.5 text-xs rounded-lg bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50" :disabled="saving">
          {{ saving ? '写入中…' : '写入这一天' }}
        </button>
      </form>

      <div class="border border-zinc-800 rounded-xl px-4 py-3 mb-5">
        <div class="text-xs text-zinc-500 mb-2">过去 {{ gauge.days || 30 }} 个交易日{{ usingSample ? ' · 示例' : '' }}</div>
        <div class="font-mono text-sm space-y-1">
          <div v-for="row in gaugeRows" :key="row.label" class="flex items-center gap-3">
            <span class="w-12 text-zinc-400">{{ row.label }}</span>
            <span class="text-sky-300">{{ blocks(row.score) }}</span>
          </div>
        </div>
        <div class="text-sm mt-3">当前：{{ gauge.current || '—' }}</div>
        <div class="text-xs text-zinc-400 mt-1">过去类似状态：{{ gauge.similar_count ?? '—' }} 次</div>
        <div class="text-xs text-zinc-400">后续市场表现：上涨 {{ gauge.later_up ?? '—' }} 次 · 下跌 {{ gauge.later_down ?? '—' }} 次 · 混合 {{ gauge.later_mixed ?? '—' }} 次</div>
      </div>

      <p v-if="usingSample" class="text-xs text-zinc-500 mb-4">库里还没有市场记忆。下面两天是版式示例，用来看事实、判断和后来的验证怎么连在一起。</p>

      <div v-if="view === 'similar'" class="space-y-3">
        <h3 class="text-sm">历史上类似的市场</h3>
        <p class="text-xs text-zinc-500">只列出后来出现过的不同结果，不据此建议买卖。</p>
        <div v-if="!similarRows.length" class="text-sm text-zinc-500">还没有可比较的历史交易日。</div>
        <div v-for="row in similarRows" :key="row.trade_date" class="border border-zinc-800 rounded-xl px-4 py-3 text-sm">
          <div class="flex justify-between"><span>{{ slash(row.trade_date) }}</span><span>相似度 {{ pctScore(row.score) }}</span></div>
          <div class="text-zinc-400 text-xs mt-1">当时：{{ row.regime_label || '—' }}</div>
          <div class="text-xs mt-2 text-zinc-300">之后：指数 {{ fmtPct(row.index_after) }}<span v-if="row.sector_name"> · {{ row.sector_name }} {{ fmtPct(row.sector_after) }}</span></div>
        </div>
      </div>

      <div v-else class="space-y-4">
        <div v-if="!shownDays.length" class="text-sm text-zinc-500">这个时间范围内还没有市场记忆。</div>
        <article v-for="day in shownDays" :key="day.trade_date" class="border border-zinc-800 rounded-xl">
          <button type="button" class="w-full text-left px-4 py-3" @click="openDay(day)">
            <div class="text-xs text-zinc-500">{{ slash(day.trade_date) }}</div>
            <div class="text-sm mt-1">{{ dot(day.regime?.label || day.regime_label) }} {{ day.regime?.label || day.regime_label || '未记录状态' }}</div>
            <div class="text-xs text-zinc-400 mt-1">
              <span v-if="lead(day)">热点 {{ lead(day) }}</span>
              <span v-if="day.hypotheses?.length"> · 判断 {{ hypoStatus(day.hypotheses[0]) }}</span>
              <span v-else-if="day.ai_summary"> · {{ day.ai_summary }}</span>
            </div>
          </button>

          <div v-if="activeDate === day.trade_date && detail" class="px-4 pb-4 border-t border-zinc-800 space-y-5 text-sm">
            <section>
              <div class="text-xs text-zinc-500 mb-2">① 今日市场状态</div>
              <div class="text-base">{{ dot(detail.regime.label) }} {{ detail.regime.label || '还没有状态判断' }}</div>
              <div class="text-xs text-zinc-400 mt-1">置信度 {{ rate(detail.regime.confidence) }}</div>
              <ul class="mt-2 text-xs text-zinc-300 space-y-1">
                <li v-for="(line, i) in detail.regime.evidence || []" :key="i">• {{ line }}</li>
              </ul>
            </section>

            <section>
              <div class="text-xs text-zinc-500 mb-2">② 市场数据</div>
              <div class="text-xs text-zinc-300 mb-2 space-y-0.5">
                <div v-for="name in mainIndexes" :key="name">
                  {{ name }} {{ fmtPct(indexPct(detail, name)) }}
                  <span class="text-zinc-500">前一日 {{ fmtPct(indexPrev(detail, name)) }}</span>
                </div>
              </div>
              <p v-if="detail.facts.fact_note" class="text-zinc-300 mb-2 whitespace-pre-wrap">{{ detail.facts.fact_note }}</p>
              <div class="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs">
                <div>市场温度 {{ num(detail.facts.temperature) }}</div>
                <div>成交额 {{ turnover(detail.facts.turnover_trillion) }}</div>
                <div>涨跌比 {{ num(detail.facts.up_down_ratio) }}</div>
                <div>涨停 {{ num(detail.facts.limit_up) }}</div>
                <div>跌停 {{ num(detail.facts.limit_down) }}</div>
                <div>连板高度 {{ num(detail.facts.streak) }}</div>
                <div>上涨家数 {{ num(detail.facts.advancers) }}</div>
                <div>下跌家数 {{ num(detail.facts.decliners) }}</div>
              </div>
            </section>

            <section>
              <div class="text-xs text-zinc-500 mb-2">③ 市场结构</div>
              <div v-for="group in structureGroups(detail)" :key="group.title" class="mb-2">
                <div class="text-xs text-zinc-400">{{ group.title }}</div>
                <div v-for="line in group.lines" :key="line" class="text-xs text-zinc-300 pl-3">{{ line }}</div>
              </div>
              <div class="text-xs text-zinc-500 mb-2">{{ detail.structure.note || detail.narrative || '' }}</div>
              <div class="text-xs text-zinc-500 mb-1">热点板块</div>
              <div v-for="sec in detail.structure.sectors || []" :key="sec.name" class="mb-2">
                <div class="flex items-center gap-2 text-xs">
                  <span class="w-16">{{ sec.name }}</span>
                  <span class="h-2 bg-zinc-800 rounded flex-1 overflow-hidden"><span class="block h-full bg-sky-500" :style="{ width: barWidth(sec) }"></span></span>
                  <span class="w-14 text-right">{{ fmtPct(sec.change_pct) }}</span>
                </div>
                <div class="text-[11px] text-zinc-500 mt-0.5">强度 {{ sec.strength ?? '—' }} · 连续 {{ sec.streak_days ?? '—' }} 天 · 内部上涨率 {{ rate(sec.up_ratio) }} · 龙头集中度 {{ sec.concentration || '—' }} · {{ sec.phase || '—' }}</div>
              </div>
              <div class="flex gap-1 mt-2 text-[11px]">
                <span v-for="p in phases" :key="p" class="px-1.5 py-0.5 rounded" :class="phaseOn(detail, p) ? 'bg-sky-500/20 text-sky-200' : 'text-zinc-600'">{{ p }}</span>
              </div>
            </section>

            <section>
              <div class="text-xs text-zinc-500 mb-2">④ 市场事件</div>
              <div v-if="!(detail.events || []).length" class="text-xs text-zinc-500">这一天还没有事件。</div>
              <div v-for="ev in detail.events" :key="ev.time + ev.title" class="text-xs py-1">
                <span class="text-zinc-500 mr-2">{{ ev.time }}</span>{{ ev.title }}
              </div>
            </section>

            <section>
              <div class="text-xs text-zinc-500 mb-2">⑤ AI 市场总结</div>
              <p class="whitespace-pre-wrap text-zinc-200">{{ detail.summary.text || '还没有总结。' }}</p>
              <div v-if="(detail.summary.evidence || []).length" class="mt-2 text-xs">
                <div class="text-zinc-500 mb-1">依据</div>
                <div v-for="(line, i) in detail.summary.evidence" :key="i">✓ {{ line }}</div>
                <div class="mt-1 text-zinc-400">置信度 {{ rate(detail.summary.confidence) }}</div>
              </div>
            </section>

            <section>
              <div class="text-xs text-zinc-500 mb-2">⑥ 待验证判断</div>
              <div class="text-xs text-zinc-400 mb-2">
                已验证 {{ hypoStats.checked }} 次 · 正确 {{ hypoStats.correct }} · 部分正确 {{ hypoStats.partial }} · 错误 {{ hypoStats.wrong }} · 等待 {{ hypoStats.pending }}
              </div>
              <div v-if="!(detail.hypotheses || []).length" class="text-xs text-zinc-500">这一天还没有待验证判断。</div>
              <div v-for="h in detail.hypotheses" :key="h.id || h.hypothesis_code" class="border border-zinc-800 rounded-lg px-3 py-2 mb-2">
                <div class="text-xs text-zinc-500">判断 #{{ h.hypothesis_code }}</div>
                <div class="mt-1 whitespace-pre-wrap">{{ h.statement }}</div>
                <div class="text-xs text-zinc-400 mt-2">验证条件</div>
                <div v-for="(c, i) in h.conditions || []" :key="i" class="text-xs">✓ {{ c }}</div>
                <div class="text-xs mt-2">验证时间 {{ slash(h.verify_on) || '—' }}</div>
                <div class="text-xs mt-1">状态：{{ statusText(h.status) }}</div>
                <div v-if="h.status === 'PENDING' && !day.demo" class="flex gap-2 mt-2">
                  <button v-for="s in verdicts" :key="s.id" type="button" class="px-2 py-1 text-[11px] rounded bg-zinc-800 hover:bg-zinc-700" @click="mark(h.id, s.id)">{{ s.label }}</button>
                </div>
              </div>
            </section>

            <section v-if="(detail.similar || []).length">
              <div class="text-xs text-zinc-500 mb-2">⑦ 历史相似市场</div>
              <p class="text-[11px] text-zinc-500 mb-2">相似日后来的结果可以不同。这里不给出买卖建议。</p>
              <div v-for="row in detail.similar" :key="row.trade_date" class="text-xs py-1 flex justify-between">
                <span>{{ slash(row.trade_date) }} {{ row.regime_label }}</span>
                <span>相似度 {{ pctScore(row.score) }} · 指数 {{ fmtPct(row.index_after) }}</span>
              </div>
            </section>

            <section v-if="detail.personal && detail.personal.count">
              <div class="text-xs text-amber-300/90 mb-2">个人历史提醒</div>
              <p class="text-xs text-zinc-300">当前状态与过去 {{ detail.personal.count }} 次相关交易相似。盈利 {{ detail.personal.wins }}，亏损 {{ detail.personal.losses }}。</p>
              <div v-for="(r, i) in detail.personal.reasons" :key="r.label" class="text-xs mt-1">{{ i + 1 }}. {{ r.label }} {{ r.n }} 次</div>
              <div v-if="detail.personal.titles?.length" class="text-xs text-zinc-500 mt-1">相关交易：{{ detail.personal.titles.join('、') }}</div>
            </section>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import axios from 'axios'

const props = defineProps<{ query?: string }>()

const spans = [
  { id: 'today', label: '今日' },
  { id: 'week', label: '本周' },
  { id: 'month', label: '本月' },
  { id: 'all', label: '历史' },
]
const phases = ['启动', '发酵', '高潮', '分歧', '退潮']
const verdicts = [
  { id: 'CORRECT', label: '正确' },
  { id: 'PARTIAL', label: '部分正确' },
  { id: 'WRONG', label: '错误' },
]
const factFields = [
  { key: 'temperature', label: '市场温度' },
  { key: 'turnover_trillion', label: '成交额（万亿）' },
  { key: 'turnover_change_pct', label: '成交额变化 %' },
  { key: 'advancers', label: '上涨家数' },
  { key: 'decliners', label: '下跌家数' },
  { key: 'limit_up', label: '涨停' },
  { key: 'limit_down', label: '跌停' },
  { key: 'streak', label: '连板高度' },
  { key: 'index_sh', label: '上证涨跌 %' },
  { key: 'index_sz', label: '深证涨跌 %' },
  { key: 'index_cy', label: '创业板涨跌 %' },
]
const mainIndexes = ['上证', '深证', '创业板']

const sampleGauge = {
  days: 30, trend: 70, sentiment: 50, turnover: 80, volatility: 60,
  current: '震荡修复', similar_count: 23, later_up: 12, later_down: 8, later_mixed: 3,
}
const sampleDays = [
  {
    demo: true,
    trade_date: '2026-09-29',
    regime_label: '震荡修复',
    ai_summary: '指数平稳，内部结构改善。',
    hypotheses: [{ status: 'PENDING' }],
    regime: { label: '震荡修复' },
    structure: { sectors: [{ name: 'AI算力' }] },
  },
  {
    demo: true,
    trade_date: '2026-09-28',
    regime_label: '情绪退潮',
    ai_summary: '高位股杀跌。',
    hypotheses: [{ status: 'WRONG' }],
    regime: { label: '情绪退潮' },
    structure: { sectors: [{ name: '半导体' }] },
  },
]
const sampleDetail: Record<string, any> = {
  '2026-09-29': {
    trade_date: '2026-09-29',
    facts: {
      fact_note: '沪深主要指数震荡，成交额较前一日放大。',
      temperature: 62, turnover_trillion: 1.82, turnover_change_pct: 12,
      advancers: 2847, decliners: 1931, up_down_ratio: 1.47,
      limit_up: 67, limit_down: 18, streak: 5,
      indices: [
        { name: '上证', change_pct: 0.18 }, { name: '深证', change_pct: 0.42 },
        { name: '创业板', change_pct: 0.66 }, { name: '科创50', change_pct: 1.05 }, { name: '北证', change_pct: -0.21 },
      ],
    },
    regime: {
      label: '震荡修复', confidence: 0.78,
      evidence: ['成交额较5日均值 +12%', '上涨家数连续2日增加', '涨停数量恢复', '高位股分歧仍然存在'],
    },
    structure: {
      note: '强势板块轮动，权重表现平稳，行情集中在少数主题。',
      sectors: [
        { name: 'AI算力', change_pct: 4.8, strength: 92, streak_days: 4, up_ratio: 0.81, concentration: '高', phase: '发酵' },
        { name: '半导体', change_pct: 3.7, strength: 80, streak_days: 2, up_ratio: 0.66, concentration: '中', phase: '启动' },
        { name: '机器人', change_pct: 2.9, strength: 70, streak_days: 1, up_ratio: 0.58, concentration: '中', phase: '启动' },
        { name: '光伏', change_pct: 1.8, strength: 40, streak_days: 1, up_ratio: 0.51, concentration: '低', phase: '启动' },
        { name: '银行', change_pct: 0.4, strength: 20, streak_days: 1, up_ratio: 0.48, concentration: '低', phase: '分歧' },
        { name: '房地产', change_pct: -1.2, strength: 15, streak_days: 2, up_ratio: 0.22, concentration: '低', phase: '退潮' },
      ],
    },
    events: [
      { time: '09:42', title: 'AI算力板块快速拉升' },
      { time: '10:15', title: '机器人板块出现资金回流' },
      { time: '11:03', title: '高位连板股出现明显分歧' },
      { time: '13:47', title: '指数翻红' },
      { time: '14:32', title: '成交额明显放大' },
    ],
    summary: {
      text: '今日市场整体处于震荡修复状态。指数表现相对平稳，但市场内部结构出现明显改善，主要表现为上涨家数增加、涨停数量恢复以及成交额较近期均值有所提升。\n\nAI算力与半导体表现较强，板块内部有分化，高位个股波动增加。当前更接近结构性行情。',
      evidence: ['上涨家数 +18%', '涨停 +21', '成交额 +12%', '强势板块数量 +3'],
      confidence: 0.81,
    },
    hypotheses: [{
      id: 'demo-h1', hypothesis_code: 'M-20260929-01', status: 'PENDING', verify_on: '2026-10-02',
      statement: '未来3个交易日，AI算力板块可能继续保持相对强势。',
      basis: ['连续4日资金流入', '板块成交量持续增加', '龙头股相对指数强度提升'],
      conditions: ['板块3日超额收益 > 2%', '龙头没有出现连续跌停', '板块成交额保持在20日均值以上'],
    }],
    similar: [
      { trade_date: '2026-06-18', score: 0.91, regime_label: '震荡 → 修复', index_after: 1.8, sector_name: 'AI板块', sector_after: 5.2 },
      { trade_date: '2026-05-07', score: 0.87, regime_label: '震荡 → 修复', index_after: -0.6, sector_name: 'AI板块', sector_after: -2.1 },
    ],
    personal: {
      count: 14, wins: 6, losses: 8,
      reasons: [{ label: '追高', n: 5 }, { label: '板块高潮', n: 3 }, { label: '止损过晚', n: 2 }, { label: '市场判断错误', n: 1 }],
      titles: ['T-103', 'T-087', 'T-061'],
    },
  },
  '2026-09-28': {
    trade_date: '2026-09-28',
    facts: { fact_note: '高位股杀跌，情绪退潮。', temperature: 41, turnover_trillion: 1.54, advancers: 1402, decliners: 3310, limit_up: 29, limit_down: 34, streak: 3, up_down_ratio: 0.42 },
    regime: { label: '情绪退潮', confidence: 0.74, evidence: ['上涨家数明显减少', '跌停增加', '高位股大面积回落'] },
    structure: { note: '半导体仍有资金，但高位股杀跌。', sectors: [{ name: '半导体', change_pct: 1.1, strength: 61, streak_days: 1, up_ratio: 0.44, concentration: '中', phase: '分歧' }] },
    events: [{ time: '10:20', title: '高位股杀跌' }],
    summary: { text: '情绪从修复转成退潮，强势只留在少数方向。', evidence: ['跌停增加', '上涨家数下降'], confidence: 0.74 },
    hypotheses: [{ id: 'demo-h2', hypothesis_code: 'M-20260928-01', status: 'WRONG', verify_on: '2026-09-29', statement: '退潮后次日立刻修复。', conditions: ['次日上涨家数回升'], basis: [] }],
    similar: [],
    personal: { count: 0, wins: 0, losses: 0, reasons: [], titles: [] },
  },
}

const span = ref('all')
const view = ref<'day' | 'similar'>('day')
const days = ref<any[]>([])
const gaugeRaw = ref<any>({})
const hypoStats = ref({ checked: 0, correct: 0, partial: 0, wrong: 0, pending: 0 })
const reminders = ref<any[]>([])
const activeDate = ref('')
const detail = ref<any>(null)
const error = ref('')
const adding = ref(false)
const saving = ref(false)
const form = ref(blankForm())

const usingSample = computed(() => days.value.length === 0 && span.value === 'all')
const shownDays = computed(() => {
  const src = usingSample.value ? sampleDays : days.value
  const key = (props.query || '').trim().toLowerCase()
  if (!key) return src
  return src.filter((d) => JSON.stringify(d).toLowerCase().includes(key))
})
const gauge = computed(() => usingSample.value ? sampleGauge : (gaugeRaw.value || {}))
const gaugeRows = computed(() => [
  { label: '趋势', score: gauge.value.trend },
  { label: '情绪', score: gauge.value.sentiment },
  { label: '成交', score: gauge.value.turnover },
  { label: '波动', score: gauge.value.volatility },
])
const similarRows = computed(() => {
  if (detail.value?.similar?.length) return detail.value.similar
  if (usingSample.value) return sampleDetail['2026-09-29'].similar
  return []
})

function blankForm() {
  const today = new Date()
  const iso = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
  return {
    trade_date: iso,
    facts: {} as Record<string, string>,
    regime: { label: '', confidence: '', evidence: '' },
    structure: { note: '' },
    sector: { name: '', change_pct: '', strength: '', phase: '发酵' },
    event: { time: '', title: '' },
    summary: { text: '', evidence: '' },
    hypothesis: { statement: '', conditions: '', verify_on: '' },
  }
}

function indexPct(detail: any, name: string) {
  return (detail?.facts?.indices || []).find((r: any) => r.name === name)?.change_pct
}
function indexPrev(detail: any, name: string) {
  return (detail?.facts?.indices || []).find((r: any) => r.name === name)?.prev_change_pct
}
function structureGroups(detail: any) {
  const s = detail?.structure || {}
  const b = s.breadth || {}
  const t = s.turnover || {}
  const f = s.funds || {}
  const v = s.volatility || {}
  const indices = (s.indices || detail?.facts?.indices || []).map((r: any) => `${r.name}  ${fmtPct(r.change_pct)}`)
  const margin = f.margin
  return [
    { title: '指数', lines: indices.length ? indices : ['—'] },
    { title: '市场宽度', lines: [`上涨 ${num(b.advancers)}`, `下跌 ${num(b.decliners)}`, `涨停 ${num(b.limit_up)}`, `跌停 ${num(b.limit_down)}`, `炸板 ${num(b.limit_break)}`, `连板 ${num(b.streak)}`] },
    { title: '成交', lines: [`两市成交额 ${t.amount_trillion ?? '—'} 万亿`, `成交额变化 ${fmtPct(t.change_pct)}`, `量比 ${num(t.volume_ratio)}`, `换手 ${num(t.turnover_rate)}`] },
    { title: '资金', lines: [f.main_note || `主力 ${num(f.main)}`, f.etf ? `ETF ${f.etf.names} ${fmtPct(f.etf.change_pct)}` : 'ETF —', f.north_note || `北向 ${num(f.north)}`, margin ? `融资融券 ${margin.trade_date} 融资买入 ${margin.rzmre}` : '融资融券 —'] },
    { title: '波动', lines: [`波动率 ${num(v.volatility)}`, `振幅 ${num(v.amplitude)}`, `指数波动 ${num(v.index_swing)}`] },
  ]
}
function slash(v: unknown) {
  return String(v || '').slice(0, 10).replace(/-/g, '/')
}
function num(v: unknown) {
  return v == null || v === '' ? '—' : String(v)
}
function turnover(v: unknown) {
  if (v == null || v === '') return '—'
  return `${Number(v)}万亿`
}
function rate(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const pct = Math.abs(n) <= 1 ? n * 100 : n
  return `${pct.toFixed(0)}%`
}
function fmtPct(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  const digits = Math.abs(n) < 1 && n !== 0 ? 2 : 1
  return `${sign}${n.toFixed(digits)}%`
}
function pctScore(v: unknown) {
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${Math.round((n <= 1 ? n * 100 : n))}%`
}
function blocks(score: unknown) {
  if (score == null || score === '') return '░░░░░░░░░░'
  const n = Math.max(0, Math.min(10, Math.round(Number(score) / 10)))
  return '█'.repeat(n) + '░'.repeat(10 - n)
}
function dot(label: string) {
  const s = label || ''
  if (s.includes('退潮') || s.includes('弱')) return '🔴'
  if (s.includes('强')) return '🟢'
  return '🟡'
}
function lead(day: any) {
  return day.lead_sector || day.structure?.sectors?.[0]?.name || ''
}
function hypoStatus(h: any) {
  return statusText(h?.status)
}
function statusText(status: string) {
  if (status === 'CORRECT') return '正确'
  if (status === 'PARTIAL') return '部分正确'
  if (status === 'WRONG') return '错误'
  if (status === 'PENDING') return '等待验证'
  return '等待验证'
}
function barWidth(sec: any) {
  const n = Number(sec.strength ?? Math.abs(Number(sec.change_pct) || 0) * 15)
  return `${Math.max(6, Math.min(100, n))}%`
}
function phaseOn(day: any, phase: string) {
  return (day.structure?.sectors || []).some((s: any) => s.phase === phase && s.strength >= 70)
}

async function load() {
  error.value = ''
  try {
    const res = await axios.get('/kb/memory', { params: { span: span.value } })
    days.value = res.data?.days || []
    gaugeRaw.value = res.data?.gauge || {}
    reminders.value = res.data?.reminders || []
    hypoStats.value = res.data?.hypothesis_stats || hypoStats.value
    if (!usingSample.value && shownDays.value.length && !activeDate.value) {
      await openDay(shownDays.value[0])
    }
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '市场记忆读取失败'
  }
}

async function setSpan(id: string) {
  span.value = id
  view.value = 'day'
  activeDate.value = ''
  detail.value = null
  await load()
}

async function openDay(day: any) {
  if (activeDate.value === day.trade_date) {
    activeDate.value = ''
    detail.value = null
    return
  }
  activeDate.value = day.trade_date
  if (day.demo) {
    detail.value = sampleDetail[day.trade_date]
    return
  }
  try {
    const res = await axios.get(`/kb/memory/${day.trade_date}`)
    detail.value = res.data?.day
    if (res.data?.day?.hypothesis_stats) hypoStats.value = res.data.day.hypothesis_stats
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '读取这一天失败'
  }
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    const sector = form.value.sector.name ? [{
      name: form.value.sector.name,
      change_pct: form.value.sector.change_pct === '' ? null : Number(form.value.sector.change_pct),
      strength: form.value.sector.strength === '' ? null : Number(form.value.sector.strength),
      phase: form.value.sector.phase,
    }] : []
    const facts: Record<string, any> = { fact_note: form.value.facts.fact_note || '' }
    const indexMap: Record<string, string> = { index_sh: '上证', index_sz: '深证', index_cy: '创业板' }
    const indices: any[] = []
    for (const f of factFields) {
      const raw = form.value.facts[f.key]
      const value = raw === '' || raw == null ? null : Number(raw)
      if (indexMap[f.key]) {
        if (value != null) indices.push({ name: indexMap[f.key], change_pct: value })
        continue
      }
      facts[f.key] = value
    }
    if (indices.length) {
      facts.indices = indices
      facts.change_pct = indices.find((r) => r.name === '上证')?.change_pct ?? null
    }
    await axios.post('/kb/memory', {
      trade_date: form.value.trade_date,
      facts,
      regime: form.value.regime,
      structure: { note: form.value.structure.note, sectors: sector },
      events: form.value.event.title ? [form.value.event] : [],
      summary: form.value.summary,
      hypotheses: form.value.hypothesis.statement ? [form.value.hypothesis] : [],
    })
    adding.value = false
    const savedDate = form.value.trade_date
    span.value = 'all'
    activeDate.value = ''
    await load()
    activeDate.value = ''
    detail.value = null
    const saved = days.value.find((d) => String(d.trade_date).slice(0, 10) === savedDate)
    if (saved) await openDay(saved)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '写入失败'
  } finally {
    saving.value = false
  }
}

async function mark(id: string, status: string) {
  error.value = ''
  try {
    const res = await axios.post(`/kb/memory/hypothesis/${id}/validate`, { status })
    detail.value = res.data?.day
    if (res.data?.day?.hypothesis_stats) hypoStats.value = res.data.day.hypothesis_stats
    await load()
    activeDate.value = res.data?.day?.trade_date || activeDate.value
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '验证没有写入'
  }
}

watch(() => props.query, () => {
  if (usingSample.value) return
})

onMounted(load)
</script>
