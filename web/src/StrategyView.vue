<template>
  <div class="h-full min-h-0 flex flex-col bg-zinc-950 overflow-hidden">
    <!-- 顶栏：模式 A/B + 运行控制 -->
    <div class="flex-shrink-0 border-b border-zinc-800 bg-zinc-900/80 px-4 py-3 space-y-3">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2 mr-2">
          <i class="fa-solid fa-chess text-violet-400"></i>
          <span class="text-sm font-semibold">策略分析工作台</span>
        </div>

        <div class="flex gap-1 bg-zinc-800 rounded-lg p-0.5">
          <button
            @click="runMode = 'A'"
            :class="runMode === 'A' ? 'bg-blue-600/30 text-blue-300' : 'text-zinc-400 hover:text-white'"
            class="px-3 py-1.5 rounded-md text-xs font-medium transition-all flex items-center gap-1.5"
          >
            <i class="fa-solid fa-hand-pointer text-[10px]"></i> 模式 A · 人机协作
          </button>
          <button
            @click="runMode = 'B'"
            :class="runMode === 'B' ? 'bg-violet-600/30 text-violet-300' : 'text-zinc-400 hover:text-white'"
            class="px-3 py-1.5 rounded-md text-xs font-medium transition-all flex items-center gap-1.5"
          >
            <i class="fa-solid fa-robot text-[10px]"></i> 模式 B · 全自动 Agent
          </button>
        </div>

        <label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none">
          <input v-model="lookbackGuard" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-emerald-500" />
          前视保护 lookback_guard
        </label>
        <label class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none">
          <input v-model="useWalkForward" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-amber-500" />
          Walk-Forward
        </label>

        <div class="flex-1"></div>

        <button
          v-if="runMode === 'A'"
          @click="runManualStep"
          :disabled="running"
          class="px-3 py-1.5 rounded-lg text-xs bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:text-zinc-500 transition-colors"
        >
          <i class="fa-solid fa-play mr-1" :class="{ 'animate-spin': running }"></i>
          {{ stepButtonLabel }}
        </button>
        <button
          v-if="runMode === 'A'"
          @click="resetCycle"
          class="px-3 py-1.5 rounded-lg text-xs bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
        >
          重置循环
        </button>
        <button
          v-if="runMode === 'B'"
          @click="runAutoAgent"
          :disabled="running"
          class="px-3 py-1.5 rounded-lg text-xs bg-violet-600 hover:bg-violet-500 disabled:bg-zinc-700 disabled:text-zinc-500 transition-colors"
        >
          <i class="fa-solid fa-bolt mr-1" :class="{ 'fa-spinner animate-spin': running }"></i>
          启动自动循环
        </button>
        <button
          v-if="runMode === 'B' && running"
          @click="stopAuto = true"
          class="px-3 py-1.5 rounded-lg text-xs bg-red-600/80 hover:bg-red-500 text-white transition-colors"
        >
          停止
        </button>
      </div>

      <!-- 循环节点 -->
      <div class="flex items-center gap-1.5 overflow-x-auto scrollbar-thin pb-0.5">
        <template v-for="(node, i) in cycleNodes" :key="node.id">
          <div
            :class="[
              'flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs flex-shrink-0 transition-all',
              state.step === node.id
                ? 'border-violet-500/60 bg-violet-500/10 text-violet-200'
                : state.completed.includes(node.id)
                  ? 'border-emerald-500/40 bg-emerald-500/5 text-emerald-300'
                  : 'border-zinc-800 bg-zinc-900/50 text-zinc-500'
            ]"
          >
            <span class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold"
              :class="state.completed.includes(node.id) ? 'bg-emerald-600/40' : state.step === node.id ? 'bg-violet-600/40' : 'bg-zinc-800'"
            >{{ i + 1 }}</span>
            <span>{{ node.label }}</span>
            <i v-if="state.completed.includes(node.id)" class="fa-solid fa-check text-[10px] text-emerald-400"></i>
          </div>
          <i v-if="i < cycleNodes.length - 1" class="fa-solid fa-chevron-right text-[10px] text-zinc-600 flex-shrink-0"></i>
        </template>
        <div class="ml-auto text-[10px] text-zinc-500 tabular-nums flex-shrink-0">
          迭代 {{ state.iteration }}/{{ harness.maxIterations }}
          · regime: <span class="text-amber-400/90">{{ state.regime || '-' }}</span>
        </div>
      </div>
    </div>

    <!-- 主体 -->
    <div class="flex-1 min-h-0 flex overflow-hidden">
      <!-- 左：策略工具箱 -->
      <aside class="w-64 flex-shrink-0 border-r border-zinc-800 bg-zinc-900/40 flex flex-col min-h-0">
        <div class="px-3 py-2.5 border-b border-zinc-800 flex items-center justify-between">
          <span class="text-xs font-semibold text-zinc-300">策略工具箱</span>
          <span class="text-[10px] text-zinc-500">{{ strategies.length }} 种</span>
        </div>
        <div class="flex-1 overflow-y-auto p-2 space-y-1.5 scrollbar-thin">
          <div
            v-for="s in strategies"
            :key="s.id"
            @click="selectStrategy(s)"
            :class="[
              'rounded-xl p-2.5 border cursor-pointer transition-all',
              selectedStrategy?.id === s.id
                ? 'border-violet-500/50 bg-violet-500/10'
                : 'border-zinc-800 bg-zinc-900/60 hover:border-zinc-700'
            ]"
          >
            <div class="flex items-center justify-between mb-0.5">
              <span class="text-xs font-medium text-zinc-200">{{ s.name }}</span>
              <span class="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">{{ s.category }}</span>
            </div>
            <p class="text-[10px] text-zinc-500 leading-relaxed line-clamp-2">{{ s.desc }}</p>
            <div class="mt-1 text-[9px] text-zinc-600">适合：{{ s.suitable_regime }}</div>
          </div>
        </div>
      </aside>

      <!-- 中：配置 + 结果 -->
      <div class="flex-1 min-w-0 flex flex-col min-h-0 overflow-hidden">
        <!-- 子 Tab -->
        <div class="flex-shrink-0 flex gap-1 px-3 pt-2 border-b border-zinc-800 bg-zinc-950">
          <button
            v-for="t in mainTabs"
            :key="t.id"
            @click="mainTab = t.id"
            :class="mainTab === t.id ? 'text-white border-b-2 border-b-violet-500' : 'text-zinc-500 hover:text-zinc-300 border-b-2 border-b-transparent'"
            class="px-3 py-2 text-xs font-medium transition-colors"
          >{{ t.label }}</button>
        </div>

        <div class="flex-1 overflow-y-auto scrollbar-thin p-4 space-y-4">
          <!-- 感知与参数 -->
          <template v-if="mainTab === 'setup'">
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
                <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
                  <i class="fa-solid fa-eye text-sky-400"></i> 数据与感知
                </h4>
                <div class="grid grid-cols-2 gap-2">
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">标的代码</label>
                    <input v-model="config.tsCode" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none focus:border-violet-500" placeholder="000001.SZ" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">基准</label>
                    <select v-model="config.benchmark" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none">
                      <option value="000300.SH">沪深300</option>
                      <option value="000001.SH">上证指数</option>
                      <option value="399006.SZ">创业板指</option>
                    </select>
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">开始日期</label>
                    <input v-model="config.start" type="date" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">结束日期</label>
                    <input v-model="config.end" type="date" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">初始资金</label>
                    <input v-model.number="config.cash" type="number" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">波动分位窗口</label>
                    <input v-model.number="config.volWindow" type="number" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                </div>
                <div class="flex flex-wrap gap-1.5 pt-1">
                  <label v-for="f in featureToggles" :key="f.id" class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                    <input v-model="f.enabled" type="checkbox" class="rounded border-zinc-600" />
                    {{ f.label }}
                  </label>
                </div>
                <div v-if="state.regime" class="text-xs text-zinc-400 bg-zinc-800/50 rounded-lg px-3 py-2">
                  当前识别状态：<span class="text-amber-300 font-medium">{{ regimeLabel(state.regime) }}</span>
                  <span class="text-zinc-600 ml-2">{{ state.analysisNote }}</span>
                </div>
              </div>

              <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
                <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
                  <i class="fa-solid fa-sliders text-violet-400"></i>
                  策略参数 · {{ selectedStrategy?.name || '未选择' }}
                </h4>
                <p v-if="selectedStrategy" class="text-[11px] text-zinc-500 leading-relaxed">{{ selectedStrategy.logic }}</p>
                <div v-if="selectedStrategy" class="space-y-2">
                  <div v-for="p in selectedStrategy.params" :key="p.name" class="grid grid-cols-3 gap-2 items-center">
                    <label class="text-[10px] text-zinc-500">{{ p.label }}</label>
                    <input
                      v-model.number="paramValues[p.name]"
                      type="number"
                      :min="p.min"
                      :max="p.max"
                      :step="p.step || 1"
                      class="col-span-2 bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none focus:border-violet-500"
                    />
                  </div>
                </div>
                <div v-else class="text-xs text-zinc-600 py-6 text-center">请从左侧选择策略</div>

                <!-- 模式 B 配置 -->
                <div v-if="runMode === 'B'" class="border-t border-zinc-800 pt-3 space-y-2">
                  <h5 class="text-[10px] font-semibold text-violet-300 uppercase tracking-wide">Harness 配置</h5>
                  <div class="grid grid-cols-2 gap-2">
                    <div>
                      <label class="text-[10px] text-zinc-500 block mb-1">最大迭代</label>
                      <input v-model.number="harness.maxIterations" type="number" min="1" max="10" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs" />
                    </div>
                    <div>
                      <label class="text-[10px] text-zinc-500 block mb-1">最大回撤容忍%</label>
                      <input v-model.number="harness.maxDrawdownPct" type="number" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs" />
                    </div>
                    <div>
                      <label class="text-[10px] text-zinc-500 block mb-1">最小夏普</label>
                      <input v-model.number="harness.minSharpe" type="number" step="0.1" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs" />
                    </div>
                    <label class="flex items-center gap-1.5 text-[10px] text-zinc-400 mt-4 cursor-pointer">
                      <input v-model="harness.autoSaveMemory" type="checkbox" class="rounded border-zinc-600" />
                      自动写入记忆
                    </label>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- 回测结果 -->
          <template v-else-if="mainTab === 'backtest'">
            <div v-if="!backtestResult" class="h-64 flex items-center justify-center text-zinc-500 text-sm">
              <div class="text-center">
                <i class="fa-solid fa-chart-area text-3xl mb-3 opacity-30 block"></i>
                尚未运行回测。模式 A 逐步执行，或模式 B 一键自动循环。
              </div>
            </div>
            <template v-else>
              <div class="flex items-center gap-2 text-[10px] mb-1">
                <span :class="backtestResult.lookback_ok ? 'text-emerald-400' : 'text-red-400'" class="px-2 py-0.5 rounded bg-zinc-800">
                  lookback_guard: {{ backtestResult.lookback_ok ? '通过' : '拦截' }}
                </span>
                <span v-if="backtestResult.walk_forward" class="text-amber-400 px-2 py-0.5 rounded bg-zinc-800">Walk-Forward 已启用</span>
                <span class="text-zinc-600">{{ backtestResult.strategy_name }} · {{ backtestResult.ts_code }}</span>
              </div>
              <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
                <div v-for="m in metricCards" :key="m.key" class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2.5">
                  <div class="text-[10px] text-zinc-500 mb-0.5">{{ m.label }}</div>
                  <div :class="m.color" class="text-sm font-bold tabular-nums">{{ m.value }}</div>
                </div>
              </div>
              <div ref="equityChartEl" class="h-64 bg-zinc-900 border border-zinc-800 rounded-2xl mt-2"></div>
              <div v-if="backtestResult.walk_forward_rows?.length" class="mt-3 bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
                <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800">Walk-Forward 分段</div>
                <table class="w-full text-xs">
                  <thead>
                    <tr class="border-b border-zinc-800 text-zinc-500">
                      <th class="text-left py-2 px-3">窗口</th>
                      <th class="text-right py-2 px-3">训练收益</th>
                      <th class="text-right py-2 px-3">测试收益</th>
                      <th class="text-right py-2 px-3">测试回撤</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in backtestResult.walk_forward_rows" :key="row.fold" class="border-b border-zinc-800/50">
                      <td class="py-1.5 px-3 text-zinc-300">#{{ row.fold }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums" :class="row.train_ret >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.train_ret) }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums" :class="row.test_ret >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.test_ret) }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums text-amber-400">{{ fmtPct(row.test_dd) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="mt-3 bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
                <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800">成交明细（模拟）</div>
                <div class="max-h-48 overflow-auto">
                  <table class="w-full text-xs">
                    <thead class="sticky top-0 bg-zinc-900">
                      <tr class="border-b border-zinc-800 text-zinc-500">
                        <th class="text-left py-2 px-3">日期</th>
                        <th class="text-left py-2 px-3">方向</th>
                        <th class="text-right py-2 px-3">价格</th>
                        <th class="text-right py-2 px-3">数量</th>
                        <th class="text-right py-2 px-3">盈亏</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(t, i) in backtestResult.trades" :key="i" class="border-b border-zinc-800/40">
                        <td class="py-1.5 px-3 text-zinc-400">{{ t.date }}</td>
                        <td class="py-1.5 px-3" :class="t.side === '买入' ? 'text-red-400' : 'text-green-400'">{{ t.side }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums">{{ t.price }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums">{{ t.size }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums" :class="(t.pnl || 0) >= 0 ? 'text-red-400' : 'text-green-400'">{{ t.pnl ?? '-' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </template>
          </template>

          <!-- 记忆库 -->
          <template v-else-if="mainTab === 'memory'">
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs text-zinc-400">共 {{ memories.length }} 条分析记忆</span>
              <button @click="clearMemories" class="text-[10px] text-zinc-500 hover:text-red-400">清空记忆</button>
            </div>
            <div v-if="!memories.length" class="py-16 text-center text-zinc-600 text-sm">暂无记忆。完成「反思」步骤后会自动写入。</div>
            <div class="space-y-2">
              <div v-for="m in memories" :key="m.id" class="bg-zinc-900 border border-zinc-800 rounded-xl p-3">
                <div class="flex items-center gap-2 mb-1">
                  <span :class="m.success ? 'text-emerald-400 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'" class="text-[10px] px-1.5 py-0.5 rounded">
                    {{ m.success ? '成功' : '失败' }}
                  </span>
                  <span class="text-xs font-medium text-zinc-200">{{ m.strategy_name }}</span>
                  <span class="text-[10px] text-zinc-500 font-mono">{{ m.ts_code }}</span>
                  <span class="text-[10px] text-zinc-600 ml-auto">{{ m.created_at }}</span>
                </div>
                <div class="text-[10px] text-zinc-500 mb-1">
                  regime={{ m.regime }} · 收益 {{ fmtPct(m.total_return) }} · 回撤 {{ fmtPct(m.max_drawdown) }} · 夏普 {{ m.sharpe }}
                </div>
                <p class="text-[11px] text-zinc-400 leading-relaxed">{{ m.reflection }}</p>
              </div>
            </div>
          </template>

          <!-- Agent 日志 -->
          <template v-else-if="mainTab === 'logs'">
            <div class="bg-zinc-900 border border-zinc-800 rounded-2xl p-3 font-mono text-[11px] text-zinc-400 space-y-1 max-h-[70vh] overflow-auto">
              <div v-if="!logs.length" class="text-zinc-600 py-8 text-center">运行后将在此显示节点日志与 State 变更</div>
              <div v-for="(line, i) in logs" :key="i" :class="line.level === 'error' ? 'text-red-400' : line.level === 'ok' ? 'text-emerald-400' : 'text-zinc-400'">
                <span class="text-zinc-600">[{{ line.time }}]</span> {{ line.msg }}
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 右：State 面板 -->
      <aside class="w-56 flex-shrink-0 border-l border-zinc-800 bg-zinc-900/40 hidden xl:flex flex-col min-h-0">
        <div class="px-3 py-2.5 border-b border-zinc-800 text-xs font-semibold text-zinc-300">State 快照</div>
        <div class="flex-1 overflow-y-auto p-3 space-y-2 text-[10px] scrollbar-thin">
          <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
            <div class="text-zinc-500 mb-1">mode</div>
            <div class="text-zinc-200">{{ runMode === 'A' ? '人机协作' : '全自动 Agent' }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
            <div class="text-zinc-500 mb-1">step</div>
            <div class="text-violet-300">{{ state.step || 'idle' }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
            <div class="text-zinc-500 mb-1">strategy</div>
            <div class="text-zinc-200">{{ selectedStrategy?.id || '-' }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
            <div class="text-zinc-500 mb-1">hypothesis</div>
            <div class="text-zinc-300 leading-relaxed">{{ state.hypothesis || '-' }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
            <div class="text-zinc-500 mb-1">reflection</div>
            <div class="text-zinc-300 leading-relaxed">{{ state.reflection || '-' }}</div>
          </div>
          <div class="bg-zinc-950/60 rounded-lg p-2 border border-zinc-800">
            <div class="text-zinc-500 mb-1">completed</div>
            <div class="text-emerald-400/80">{{ state.completed.join(', ') || '-' }}</div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'

const MEMORY_KEY = 'strategy_analysis_memory_v1'

const runMode = ref<'A' | 'B'>('A')
const lookbackGuard = ref(true)
const useWalkForward = ref(false)
const running = ref(false)
const stopAuto = ref(false)
const mainTab = ref<'setup' | 'backtest' | 'memory' | 'logs'>('setup')
const mainTabs = [
  { id: 'setup', label: '感知与参数' },
  { id: 'backtest', label: '回测验证' },
  { id: 'memory', label: '策略记忆' },
  { id: 'logs', label: 'Agent 日志' },
]

const cycleNodes = [
  { id: 'analyze', label: '分析' },
  { id: 'hypothesize', label: '假设' },
  { id: 'backtest', label: '回测' },
  { id: 'reflect', label: '反思' },
]

const strategies = [
  {
    id: 'dual_ma',
    name: '双均线动量',
    category: '动量',
    desc: '快线上穿慢线买入，下穿卖出。',
    suitable_regime: 'trend_up / trend_down',
    logic: '信号：MA_fast 上穿 MA_slow → 多；下穿 → 平仓。适合趋势市。',
    params: [
      { name: 'fast', label: '快线周期', min: 2, max: 60, step: 1, default: 5 },
      { name: 'slow', label: '慢线周期', min: 5, max: 200, step: 1, default: 20 },
    ],
  },
  {
    id: 'mean_reversion',
    name: '均值回归',
    category: '均值回归',
    desc: '价格偏离均线过大后反向交易。',
    suitable_regime: 'range',
    logic: '偏离度 = (close-MA)/MA；低于 -z 买入，高于 +z 卖出。',
    params: [
      { name: 'window', label: '均线窗口', min: 5, max: 120, default: 20 },
      { name: 'z', label: '偏离阈值%', min: 1, max: 15, step: 0.5, default: 3 },
    ],
  },
  {
    id: 'rsi_revert',
    name: 'RSI 超买超卖',
    category: '均值回归',
    desc: 'RSI 超卖买入、超买卖出。',
    suitable_regime: 'range',
    logic: 'RSI < oversold 开多；RSI > overbought 平仓。',
    params: [
      { name: 'period', label: 'RSI 周期', min: 5, max: 30, default: 14 },
      { name: 'oversold', label: '超卖', min: 10, max: 40, default: 30 },
      { name: 'overbought', label: '超买', min: 60, max: 90, default: 70 },
    ],
  },
  {
    id: 'breakout',
    name: '通道突破',
    category: '突破',
    desc: '突破 N 日最高价做多。',
    suitable_regime: 'trend_up',
    logic: '收盘价 > 近 N 日最高 → 买入；跌破近 M 日最低 → 卖出。',
    params: [
      { name: 'entry_n', label: '突破窗口', min: 5, max: 60, default: 20 },
      { name: 'exit_n', label: '退出窗口', min: 3, max: 40, default: 10 },
    ],
  },
  {
    id: 'vol_filter',
    name: '波动率过滤',
    category: '过滤',
    desc: '高波动时降仓或空仓，可与主策略组合。',
    suitable_regime: 'high_vol（规避）',
    logic: '波动分位 > 阈值则禁止开仓，仅允许减仓。',
    params: [
      { name: 'vol_window', label: '波动窗口', min: 10, max: 60, default: 20 },
      { name: 'pctile', label: '分位阈值', min: 50, max: 95, default: 80 },
    ],
  },
  {
    id: 'short_swing',
    name: '短线心得规则包',
    category: '规则',
    desc: '基于短线炒股心得的轮动与风控规则（示意）。',
    suitable_regime: '轮动市',
    logic: '隔夜浮盈减仓、均线上方卖出、中间时段只卖不买等规则组合（演示版用简化信号）。',
    params: [
      { name: 'take_half', label: '隔夜减仓%', min: 30, max: 70, default: 50 },
      { name: 'max_hold_days', label: '最长持仓天', min: 1, max: 10, default: 3 },
    ],
  },
]

const selectedStrategy = ref<any>(strategies[0])
const paramValues = reactive<Record<string, number>>({})

function selectStrategy(s: any) {
  selectedStrategy.value = s
  for (const p of s.params) paramValues[p.name] = p.default
}

selectStrategy(strategies[0])

const config = reactive({
  tsCode: '000001.SZ',
  benchmark: '000300.SH',
  start: '2024-01-01',
  end: '2025-12-31',
  cash: 1000000,
  volWindow: 20,
})

const featureToggles = reactive([
  { id: 'ma', label: 'MA', enabled: true },
  { id: 'rsi', label: 'RSI', enabled: true },
  { id: 'boll', label: 'BOLL', enabled: false },
  { id: 'vol_pctile', label: '波动分位', enabled: true },
  { id: 'macd', label: 'MACD', enabled: false },
])

const harness = reactive({
  maxIterations: 3,
  maxDrawdownPct: 20,
  minSharpe: 0.5,
  autoSaveMemory: true,
})

const state = reactive({
  step: '' as string,
  completed: [] as string[],
  iteration: 0,
  regime: '',
  analysisNote: '',
  hypothesis: '',
  reflection: '',
})

const logs = ref<{ time: string; msg: string; level?: string }[]>([])
const backtestResult = ref<any>(null)
const memories = ref<any[]>(loadMemories())
const equityChartEl = ref<HTMLDivElement>()
let equityChart: any = null

const stepButtonLabel = computed(() => {
  const order = ['analyze', 'hypothesize', 'backtest', 'reflect']
  const next = order.find(id => !state.completed.includes(id))
  const map: Record<string, string> = {
    analyze: '① 执行分析',
    hypothesize: '② 生成假设',
    backtest: '③ 运行回测',
    reflect: '④ 反思入库',
  }
  return next ? map[next] : '循环已完成 · 可重置'
})

const metricCards = computed(() => {
  const r = backtestResult.value
  if (!r) return []
  return [
    { key: 'ret', label: '总收益', value: fmtPct(r.total_return), color: r.total_return >= 0 ? 'text-red-400' : 'text-green-400' },
    { key: 'dd', label: '最大回撤', value: fmtPct(r.max_drawdown), color: 'text-amber-400' },
    { key: 'sharpe', label: '夏普', value: String(r.sharpe), color: 'text-sky-400' },
    { key: 'win', label: '胜率', value: fmtPct(r.win_rate), color: 'text-zinc-200' },
    { key: 'trades', label: '交易次数', value: String(r.trade_count), color: 'text-zinc-200' },
    { key: 'alpha', label: '相对基准', value: fmtPct(r.alpha), color: r.alpha >= 0 ? 'text-red-400' : 'text-green-400' },
  ]
})

function fmtPct(v: number) {
  if (v == null || Number.isNaN(v)) return '-'
  const s = (v * 100).toFixed(2)
  return (v >= 0 ? '+' : '') + s + '%'
}

function regimeLabel(r: string) {
  const map: Record<string, string> = {
    trend_up: '上升趋势',
    trend_down: '下降趋势',
    range: '震荡市',
    high_vol: '高波动',
  }
  return map[r] || r
}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function pushLog(msg: string, level?: string) {
  logs.value.push({ time: nowTime(), msg, level })
}

function loadMemories(): any[] {
  try {
    const raw = localStorage.getItem(MEMORY_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveMemories() {
  localStorage.setItem(MEMORY_KEY, JSON.stringify(memories.value))
}

function clearMemories() {
  memories.value = []
  saveMemories()
}

function resetCycle() {
  state.step = ''
  state.completed = []
  state.iteration = 0
  state.regime = ''
  state.analysisNote = ''
  state.hypothesis = ''
  state.reflection = ''
  backtestResult.value = null
  pushLog('循环已重置', 'ok')
}

function sleep(ms: number) {
  return new Promise(r => setTimeout(r, ms))
}

/** 演示用：本地模拟分析/回测（后续可接真实 backtrader API） */
function mockAnalyze() {
  const regimes = ['trend_up', 'range', 'high_vol', 'trend_down']
  const regime = regimes[Math.floor(Math.random() * regimes.length)]
  state.regime = regime
  state.analysisNote = `标的 ${config.tsCode}，特征 ${featureToggles.filter(f => f.enabled).map(f => f.label).join('+') || '无'}`
  state.step = 'analyze'
  if (!state.completed.includes('analyze')) state.completed.push('analyze')
  pushLog(`[分析] regime=${regime} · ${state.analysisNote}`, 'ok')
}

function mockHypothesize() {
  if (!selectedStrategy.value) return
  // 按 regime 简单推荐
  const s = selectedStrategy.value
  const fit = s.suitable_regime.includes(state.regime) || state.regime === 'range'
  state.hypothesis = fit
    ? `市况 ${regimeLabel(state.regime)} 与「${s.name}」匹配，采用默认/当前参数验证。`
    : `市况 ${regimeLabel(state.regime)} 与「${s.name}」不完全匹配，仍回测以对比记忆库。`
  state.step = 'hypothesize'
  if (!state.completed.includes('hypothesize')) state.completed.push('hypothesize')
  pushLog(`[假设] ${state.hypothesis}`, 'ok')
}

function mockBacktest() {
  if (lookbackGuard.value === false) {
    pushLog('[回测] lookback_guard 已关闭 — 结果仅供演示，存在前视风险', 'error')
  }

  const seed = (selectedStrategy.value?.id?.length || 1) + config.cash / 1e5
  const total_return = Number((((seed * 17) % 40) - 12) / 100)
  const max_drawdown = -Math.abs(Number((((seed * 9) % 18) + 5) / 100))
  const sharpe = Number((((seed * 3) % 25) / 10 - 0.3).toFixed(2))
  const win_rate = Number((0.35 + (seed % 7) / 20).toFixed(2))
  const trade_count = 8 + Math.floor(seed % 20)
  const alpha = Number((total_return - 0.05).toFixed(4))

  const equity = []
  let v = 1
  for (let i = 0; i < 80; i++) {
    v *= 1 + (Math.sin(i / 5 + seed) * 0.008 + total_return / 80)
    equity.push(Number(v.toFixed(4)))
  }

  const trades = []
  for (let i = 0; i < Math.min(trade_count, 12); i++) {
    const buy = i % 2 === 0
    trades.push({
      date: `2025-${String((i % 12) + 1).padStart(2, '0')}-${String((i % 27) + 1).padStart(2, '0')}`,
      side: buy ? '买入' : '卖出',
      price: (10 + (i * 1.3) % 5).toFixed(2),
      size: 100 * (1 + (i % 5)),
      pnl: buy ? null : Number((((i * 7) % 17) - 6).toFixed(2)),
    })
  }

  let walk_forward_rows: any[] = []
  if (useWalkForward.value) {
    walk_forward_rows = [1, 2, 3].map(fold => ({
      fold,
      train_ret: Number((total_return + fold * 0.01).toFixed(4)),
      test_ret: Number((total_return - fold * 0.015).toFixed(4)),
      test_dd: Number((max_drawdown - fold * 0.005).toFixed(4)),
    }))
  }

  backtestResult.value = {
    strategy_name: selectedStrategy.value.name,
    ts_code: config.tsCode,
    total_return,
    max_drawdown,
    sharpe,
    win_rate,
    trade_count,
    alpha,
    equity,
    trades,
    lookback_ok: lookbackGuard.value,
    walk_forward: useWalkForward.value,
    walk_forward_rows,
    params: { ...paramValues },
  }

  state.step = 'backtest'
  if (!state.completed.includes('backtest')) state.completed.push('backtest')
  pushLog(
    `[回测] 收益 ${fmtPct(total_return)} · 回撤 ${fmtPct(max_drawdown)} · 夏普 ${sharpe}` +
      (lookbackGuard.value ? ' · guard=ON' : ' · guard=OFF'),
    lookbackGuard.value ? 'ok' : 'error'
  )
  mainTab.value = 'backtest'
  nextTick(() => renderEquity())
}

function mockReflect() {
  const r = backtestResult.value
  if (!r) {
    pushLog('[反思] 无回测结果，跳过', 'error')
    return
  }
  const success =
    r.total_return > 0 &&
    Math.abs(r.max_drawdown) * 100 <= harness.maxDrawdownPct &&
    r.sharpe >= harness.minSharpe

  state.reflection = success
    ? `策略「${r.strategy_name}」在 ${regimeLabel(state.regime)} 下达到风控阈值，可写入记忆供后续假设检索。`
    : `未达 harness 阈值（回撤≤${harness.maxDrawdownPct}% 或 夏普≥${harness.minSharpe}）。建议换参或换策略，并记录失败案例。`

  state.step = 'reflect'
  if (!state.completed.includes('reflect')) state.completed.push('reflect')
  pushLog(`[反思] ${state.reflection}`, success ? 'ok' : 'error')

  if (runMode.value === 'A' || harness.autoSaveMemory) {
    const item = {
      id: `${Date.now()}`,
      created_at: new Date().toLocaleString('zh-CN'),
      strategy_name: r.strategy_name,
      ts_code: r.ts_code,
      regime: state.regime,
      total_return: r.total_return,
      max_drawdown: r.max_drawdown,
      sharpe: r.sharpe,
      success,
      reflection: state.reflection,
      mode: runMode.value,
    }
    memories.value = [item, ...memories.value].slice(0, 50)
    saveMemories()
    pushLog('[记忆] 已写入策略记忆库', 'ok')
    mainTab.value = 'memory'
  }
}

async function runManualStep() {
  if (running.value) return
  running.value = true
  try {
    const order = ['analyze', 'hypothesize', 'backtest', 'reflect']
    const next = order.find(id => !state.completed.includes(id))
    if (!next) {
      pushLog('本轮四步已完成，可重置后再次运行', 'ok')
      return
    }
    if (next === 'analyze') mockAnalyze()
    else if (next === 'hypothesize') mockHypothesize()
    else if (next === 'backtest') mockBacktest()
    else if (next === 'reflect') mockReflect()
  } finally {
    running.value = false
  }
}

async function runAutoAgent() {
  if (running.value) return
  if (!selectedStrategy.value) {
    pushLog('请先选择策略', 'error')
    return
  }
  running.value = true
  stopAuto.value = false
  mainTab.value = 'logs'
  pushLog(`===== 模式 B 启动 · 最多 ${harness.maxIterations} 轮 =====`, 'ok')

  try {
    for (let i = 1; i <= harness.maxIterations; i++) {
      if (stopAuto.value) {
        pushLog('用户停止自动循环', 'error')
        break
      }
      state.iteration = i
      state.completed = []
      state.step = ''
      pushLog(`--- 迭代 ${i}/${harness.maxIterations} ---`)

      await sleep(400)
      if (stopAuto.value) break
      mockAnalyze()

      await sleep(400)
      if (stopAuto.value) break
      // 自动按 regime 选更合适策略
      const better = strategies.find(s => s.suitable_regime.includes(state.regime))
      if (better && better.id !== selectedStrategy.value.id) {
        selectStrategy(better)
        pushLog(`[Agent] 根据 regime 切换策略 → ${better.name}`, 'ok')
      }
      mockHypothesize()

      await sleep(500)
      if (stopAuto.value) break
      mockBacktest()

      await sleep(400)
      if (stopAuto.value) break
      mockReflect()

      const r = backtestResult.value
      const ok =
        r &&
        r.total_return > 0 &&
        Math.abs(r.max_drawdown) * 100 <= harness.maxDrawdownPct &&
        r.sharpe >= harness.minSharpe

      if (ok) {
        pushLog(`[Agent] 迭代 ${i} 达到目标，提前结束`, 'ok')
        break
      }

      // 简单改参
      if (selectedStrategy.value.params[0]) {
        const p = selectedStrategy.value.params[0]
        paramValues[p.name] = Math.min(p.max, (paramValues[p.name] || p.default) + 1)
        pushLog(`[Agent] 未达标，调整参数 ${p.name}=${paramValues[p.name]}`, 'ok')
      }
    }
  } finally {
    running.value = false
    stopAuto.value = false
    pushLog('===== 模式 B 结束 =====', 'ok')
  }
}

function renderEquity() {
  if (!equityChartEl.value || !backtestResult.value?.equity?.length) return
  if (!equityChart) equityChart = echarts.init(equityChartEl.value, 'dark')
  const data = backtestResult.value.equity
  const bench = data.map((v: number, i: number) => Number((1 + (v - 1) * 0.6 + i * 0.0005).toFixed(4)))
  equityChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 48, right: 16, top: 28, bottom: 28 },
    legend: { data: ['策略', '基准'], textStyle: { color: '#a1a1aa', fontSize: 10 }, top: 4 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: data.map((_: any, i: number) => i + 1), axisLabel: { color: '#71717a', fontSize: 9 } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#71717a', fontSize: 9 }, splitLine: { lineStyle: { color: '#27272a' } } },
    series: [
      { name: '策略', type: 'line', data, showSymbol: false, lineStyle: { width: 1.5, color: '#a78bfa' } },
      { name: '基准', type: 'line', data: bench, showSymbol: false, lineStyle: { width: 1, color: '#71717a', type: 'dashed' } },
    ],
  }, true)
  equityChart.resize()
}

watch(mainTab, async (t) => {
  if (t === 'backtest' && backtestResult.value) {
    await nextTick()
    renderEquity()
  }
})

function onResize() {
  equityChart?.resize()
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  pushLog('策略分析工作台已就绪 · 可切换模式 A / B', 'ok')
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  equityChart?.dispose()
})
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar { width: 4px; height: 4px; }
.scrollbar-thin::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
</style>
