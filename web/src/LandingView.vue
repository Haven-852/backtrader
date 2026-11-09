<template>
  <div class="landing min-h-full text-[#e8e2d6] overflow-x-hidden">
    <!-- ambient -->
    <div class="landing-bg" aria-hidden="true"></div>

    <header class="relative z-10 px-6 md:px-10 pt-6 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-[#c4a35a] text-[#14110c] font-bold flex items-center justify-center landing-brand-mark">A</div>
        <div>
          <div class="landing-display text-lg tracking-tight text-[#f3ede6]">AIquant</div>
          <div class="text-[10px] uppercase tracking-[0.22em] text-[#8a8070]">Backtrader Agent</div>
        </div>
      </div>
      <button
        type="button"
        class="text-xs tracking-wide text-[#c4a35a] hover:text-[#e6d3a0] transition-colors"
        @click="emit('get-started')"
      >登录</button>
    </header>

    <!-- HERO -->
    <section class="relative z-10 px-6 md:px-10 pt-16 md:pt-24 pb-20 md:pb-28 max-w-6xl mx-auto landing-reveal">
      <p class="landing-display text-[#c4a35a] text-sm md:text-base mb-4 tracking-wide">本地量化智能体工作台</p>
      <h1 class="landing-display text-4xl md:text-6xl lg:text-7xl leading-[1.08] text-[#f6f0e4] max-w-4xl">
        把行情、补数、对话与复盘<br class="hidden md:block" />收进同一张桌面
      </h1>
      <p class="mt-6 text-base md:text-lg text-[#b0a693] max-w-2xl leading-relaxed">
        面向 A 股研究与纸面交易的 Agent 平台：大模型可调用真实工具查库补数，
        而不是凭空编涨跌。下方数字来自你当前实例的实时探测。
      </p>
      <div class="mt-10 flex flex-wrap items-center gap-4">
        <button
          type="button"
          class="landing-cta px-7 py-3.5 text-sm font-semibold tracking-wide"
          @click="emit('get-started')"
        >
          Get Started
        </button>
        <a href="#capabilities" class="text-sm text-[#8a8070] hover:text-[#e8e2d6] transition-colors underline-offset-4 hover:underline">
          先看看能做什么
        </a>
      </div>
    </section>

    <!-- LIVE STATS -->
    <section class="relative z-10 px-6 md:px-10 pb-16 max-w-6xl mx-auto landing-reveal delay-1">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-6 md:gap-8 border-y border-[#2a251c] py-8">
        <div v-for="s in stats" :key="s.label">
          <div class="landing-display text-2xl md:text-3xl text-[#f3ede6] tabular-nums">{{ s.value }}</div>
          <div class="mt-1 text-xs text-[#8a8070] tracking-wide">{{ s.label }}</div>
        </div>
      </div>
      <p v-if="statsError" class="mt-3 text-[11px] text-[#8a8070]">统计暂时读不到在线服务，已展示产品能力概览。</p>
    </section>

    <!-- CAPABILITIES -->
    <section id="capabilities" class="relative z-10 px-6 md:px-10 py-16 md:py-24 max-w-6xl mx-auto">
      <h2 class="landing-display text-2xl md:text-4xl text-[#f3ede6] mb-3 landing-reveal">七件事，一套工作台</h2>
      <p class="text-[#8a8070] mb-12 max-w-xl landing-reveal delay-1">从看盘到训练表，能力都挂在同一后端上；登录后左侧导航即可切换。</p>

      <div class="space-y-0">
        <article
          v-for="(cap, i) in capabilities"
          :key="cap.title"
          class="landing-cap grid md:grid-cols-12 gap-4 md:gap-8 py-8 border-t border-[#2a251c] landing-reveal"
          :style="capDelayStyle(i)"
        >
          <div class="md:col-span-1 landing-display text-[#c4a35a]">{{ padIndex(i) }}</div>
          <div class="md:col-span-4">
            <h3 class="landing-display text-xl md:text-2xl text-[#f3ede6]">{{ cap.title }}</h3>
          </div>
          <div class="md:col-span-7 text-sm md:text-base text-[#b0a693] leading-relaxed">
            {{ cap.desc }}
            <div v-if="cap.tags?.length" class="mt-3 flex flex-wrap gap-2">
              <span v-for="t in cap.tags" :key="t" class="text-[11px] tracking-wide text-[#8a8070] border border-[#3a3226] px-2 py-0.5 rounded">{{ t }}</span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- SKILLS STRIP -->
    <section class="relative z-10 px-6 md:px-10 py-16 max-w-6xl mx-auto landing-reveal">
      <h2 class="landing-display text-2xl md:text-3xl text-[#f3ede6] mb-2">模型真正能调用的技能</h2>
      <p class="text-[#8a8070] text-sm mb-8 max-w-2xl">不是聊天套壳：对话里可挂工具——SQL、Datahubco 补数、竞价筛选、板块分时、纸面账户、日度复盘等。</p>
      <div class="flex flex-wrap gap-x-6 gap-y-3">
        <span v-for="sk in skillLabels" :key="sk" class="landing-display text-sm md:text-base text-[#e8e2d6]">{{ sk }}</span>
      </div>
    </section>

    <!-- DATA HIGHLIGHT -->
    <section class="relative z-10 px-6 md:px-10 py-16 md:py-20 max-w-6xl mx-auto landing-reveal">
      <div class="landing-panel p-8 md:p-12">
        <h2 class="landing-display text-2xl md:text-4xl text-[#f3ede6] max-w-xl leading-snug">
          你的库里已经在跑真数据
        </h2>
        <ul class="mt-8 space-y-4 text-[#b0a693] text-sm md:text-base">
          <li v-for="line in dataHighlights" :key="line" class="flex gap-3">
            <span class="text-[#c4a35a] mt-0.5">▸</span>
            <span>{{ line }}</span>
          </li>
        </ul>
      </div>
    </section>

    <!-- CTA -->
    <section class="relative z-10 px-6 md:px-10 py-20 md:py-28 max-w-6xl mx-auto text-center landing-reveal">
      <h2 class="landing-display text-3xl md:text-5xl text-[#f3ede6]">从注册开始，进入工作台</h2>
      <p class="mt-4 text-[#8a8070] max-w-md mx-auto text-sm md:text-base">首个账号自动成为管理员。登录后即可使用大模型对话、行情看板与存储预览。</p>
      <button
        type="button"
        class="landing-cta mt-10 px-8 py-3.5 text-sm font-semibold tracking-wide"
        @click="emit('get-started')"
      >
        Get Started
      </button>
    </section>

    <footer class="relative z-10 px-6 md:px-10 pb-10 text-[11px] text-[#5c5548] flex flex-wrap justify-between gap-2 max-w-6xl mx-auto">
      <span>AIquant · Backtrader Agent Platform v3.0</span>
      <span>研究工具 · 不构成投资建议</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from 'axios'

const emit = defineEmits<{ (e: 'get-started'): void }>()

const live = ref({
  tables: 126,
  universe: 5575,
  trainRows: 3181878,
  skills: 11,
  models: 8,
  storageOnline: '3/4',
})
const statsError = ref(false)
const skillLabels = ref<string[]>([
  'SQL 工具箱',
  'Datahubco 补数',
  '期货主连',
  '盘中做 T',
  '纸面交易',
  '市场记忆',
  '竞价强买筛选',
  '板块分时情绪',
  '收盘复盘',
  '期货 DeepView',
  '个股尽调档案',
])

const stats = computed(() => [
  { label: 'PostgreSQL 数据项', value: formatNum(live.value.tables) },
  { label: '全市场股票池', value: formatNum(live.value.universe) },
  { label: '训练表行数', value: formatCompact(live.value.trainRows) },
  { label: '可调用技能', value: String(live.value.skills) },
])

const capabilities = [
  {
    title: '大模型对话',
    desc: 'DeepSeek 等模型接入后，可按手册调用工具：查表、补缺口、跑筛选，回答落在真实库表上。',
    tags: ['多模型配置', '会话持久化', '工具轨迹'],
  },
  {
    title: '行情看板',
    desc: '多周期 K 线与指标视图，自选与板块信息同屏，适合盘前盘后快速扫一眼。',
    tags: ['ETF', '板块', '多周期'],
  },
  {
    title: '存储层',
    desc: '一眼看清 Postgres / Redis / Influx 等是否在线；表预览滚动加载，支持 CSV / Markdown 导出，还能用自然语言让模型建自定义表。',
    tags: ['AI 建表', '导出', '连通测试'],
  },
  {
    title: '行情中心',
    desc: '期货主连与更广市场数据入口，缺数时可走补数技能写回，而不是空页面干等。',
    tags: ['期货', '日周月'],
  },
  {
    title: '策略分析',
    desc: '结构回撤、盘中转折等分析模块，把想法落到可复查的流程里。',
    tags: ['回测辅助', '记忆'],
  },
  {
    title: '模拟交易',
    desc: '纸面账户、盯盘与周期动作，可把 Agent 决策留痕，适合验证规则而不是实盘赌运气。',
    tags: ['纸面', '告警'],
  },
  {
    title: '知识库',
    desc: '规则、实验、外部方法与市场记忆沉淀，让复盘不只停留在聊天窗口。',
    tags: ['复盘', '规则'],
  },
]

const dataHighlights = computed(() => [
  `全市场股票清单约 ${formatNum(live.value.universe)} 只（ref_market_universe），可作为批量训练与筛选的母池。`,
  `日频训练表 ml_train_daily 当前约 ${formatCompact(live.value.trainRows)} 行，带特征与 train_ready 标记，供 LightGBM 一类流程使用。`,
  `个股日线 / 基本面表均在百万行量级，存储页可直接预览与导出切片。`,
  `技能目录现有 ${live.value.skills} 项；模型列表探测到 ${live.value.models} 个条目，存储后端在线 ${live.value.storageOnline}。`,
])

function formatNum(n: number) {
  return Number(n || 0).toLocaleString('zh-CN')
}

function formatCompact(n: number) {
  const v = Number(n || 0)
  if (v >= 1e8) return (v / 1e8).toFixed(2) + ' 亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + ' 万'
  return v.toLocaleString('zh-CN')
}

function padIndex(i: number) {
  return String(i + 1).padStart(2, '0')
}

function capDelayStyle(i: number) {
  return { animationDelay: `${0.05 * i}s` }
}

onMounted(async () => {
  try {
    const [skillsRes, tablesRes, modelsRes, storageRes] = await Promise.all([
      axios.get('/skills').catch(() => null),
      axios.get('/storage/tables', { params: { backend: 'postgresql' } }).catch(() => null),
      axios.get('/chat/models').catch(() => null),
      axios.get('/storage/status').catch(() => null),
    ])
    if (skillsRes?.data?.skills?.length) {
      live.value.skills = skillsRes.data.total || skillsRes.data.skills.length
      skillLabels.value = skillsRes.data.skills.map((s: any) => s.name || s.id).filter(Boolean)
    }
    if (tablesRes?.data?.items) {
      live.value.tables = tablesRes.data.total || tablesRes.data.items.length
      const find = (name: string) => tablesRes.data.items.find((x: any) => x.name === name)
      const uni = find('ref_market_universe')
      const train = find('ml_train_daily')
      if (uni?.rows >= 0) live.value.universe = uni.rows
      if (train?.rows >= 0) live.value.trainRows = train.rows
    }
    if (modelsRes?.data?.total != null) live.value.models = modelsRes.data.total
    if (storageRes?.data) {
      live.value.storageOnline = `${storageRes.data.online ?? '-'}/${storageRes.data.total ?? '-'}`
    }
  } catch {
    statsError.value = true
  }
})
</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Noto+Sans+SC:wght@400;500;600&display=swap');

.landing {
  font-family: 'Noto Sans SC', system-ui, sans-serif;
  background: #14110c;
  position: relative;
}

.landing-display {
  font-family: 'Fraunces', 'Noto Serif SC', Georgia, serif;
  font-optical-sizing: auto;
}

.landing-brand-mark {
  font-family: 'Fraunces', Georgia, serif;
}

.landing-bg {
  pointer-events: none;
  position: absolute;
  inset: 0;
  background:
    radial-gradient(900px 480px at 12% -10%, rgba(196, 163, 90, 0.18), transparent 55%),
    radial-gradient(700px 420px at 90% 8%, rgba(72, 110, 98, 0.16), transparent 50%),
    linear-gradient(180deg, #1a1610 0%, #14110c 40%, #100e0a 100%);
  animation: landingGlow 12s ease-in-out infinite alternate;
}

@keyframes landingGlow {
  from { filter: saturate(1) brightness(1); }
  to { filter: saturate(1.15) brightness(1.05); }
}

.landing-cta {
  background: #c4a35a;
  color: #14110c;
  border-radius: 999px;
  transition: transform 0.25s ease, background 0.25s ease, box-shadow 0.25s ease;
  box-shadow: 0 0 0 0 rgba(196, 163, 90, 0.35);
}

.landing-cta:hover {
  background: #d4b56a;
  transform: translateY(-1px);
  box-shadow: 0 10px 30px -12px rgba(196, 163, 90, 0.55);
}

.landing-panel {
  background: linear-gradient(135deg, rgba(196, 163, 90, 0.08), rgba(255, 255, 255, 0.02));
  border: 1px solid #2a251c;
  border-radius: 1.25rem;
}

.landing-reveal {
  animation: landingIn 0.8s ease both;
}

.landing-reveal.delay-1 {
  animation-delay: 0.12s;
}

.landing-cap:hover h3 {
  color: #c4a35a;
  transition: color 0.2s ease;
}

@keyframes landingIn {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
