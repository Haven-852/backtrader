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

        <label
          v-for="opt in backtestOptions"
          :key="opt.key"
          class="flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer select-none"
          :title="opt.description || opt.ui_hint || ''"
        >
          <input
            v-model="optionValues[opt.key]"
            type="checkbox"
            class="rounded border-zinc-600 bg-zinc-800"
            :class="opt.key === 'lookback_guard' ? 'text-emerald-500' : 'text-amber-500'"
          />
          <span>{{ opt.label }}</span>
          <span class="text-zinc-600 font-mono text-[10px]">{{ opt.label_en }}</span>
          <span
            v-if="opt.recommended"
            class="text-[9px] px-1 rounded bg-emerald-500/15 text-emerald-400"
          >推荐</span>
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
          <span class="text-[10px] text-zinc-500">
            {{ catalogLoading ? '加载中…' : `${strategies.length} 种` }}
          </span>
        </div>
        <div class="px-2 py-2 border-b border-zinc-800 space-y-1.5">
          <div class="text-[9px] text-zinc-600 px-0.5">选股工具</div>
          <div
            @click="openScreener"
            :class="[
              'rounded-xl p-2.5 border cursor-pointer transition-all',
              toolboxMode === 'screener'
                ? 'border-emerald-500/50 bg-emerald-500/10'
                : 'border-zinc-800 bg-zinc-900/60 hover:border-zinc-700'
            ]"
          >
            <div class="flex items-center justify-between mb-0.5">
              <span class="text-xs font-medium text-zinc-200">阶段 × 流程选股</span>
              <span class="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">选股</span>
            </div>
            <p class="text-[10px] text-zinc-500 leading-relaxed">上升/震荡/下降 × 入选到卖出；点方法加载对应日线窗口做全市场筛选</p>
          </div>
          <div
            @click="openIntraday"
            :class="[
              'rounded-xl p-2.5 border cursor-pointer transition-all',
              toolboxMode === 'intraday'
                ? 'border-sky-500/50 bg-sky-500/10'
                : 'border-zinc-800 bg-zinc-900/60 hover:border-zinc-700'
            ]"
          >
            <div class="flex items-center justify-between mb-0.5">
              <span class="text-xs font-medium text-zinc-200">日内高低点</span>
              <span class="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">LightGBM</span>
            </div>
            <p class="text-[10px] text-zinc-500 leading-relaxed">日频：收盘后预测次日高低点参考价；分钟：15 分钟内先碰障碍的三分类。</p>
          </div>
        </div>
        <div v-if="catalogError" class="px-3 py-2 text-[10px] text-red-400 border-b border-zinc-800">
          {{ catalogError }}
          <button @click="loadCatalog" class="ml-2 underline text-sky-400">重试</button>
        </div>
        <div class="flex-1 overflow-y-auto p-2 space-y-1.5 scrollbar-thin">
          <div v-if="catalogLoading && !strategies.length" class="text-[10px] text-zinc-500 px-2 py-4 text-center">
            <i class="fa-solid fa-spinner animate-spin mr-1"></i>从后端拉取策略目录…
          </div>
          <div
            v-for="s in strategies"
            :key="s.id"
            @click="selectStrategy(s)"
            :class="[
              'rounded-xl p-2.5 border cursor-pointer transition-all',
              toolboxMode === 'strategy' && selectedStrategy?.id === s.id
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
          <IntradayTurningPanel v-if="mainTab === 'intraday'" />
          <ScreenerFlowPanel
            v-show="mainTab === 'screener'"
            @log="(msg, level) => pushLog(msg, level as any)"
            @asof="onScreenerAsof"
            @activated="onScreenerActivated"
          />

          <!-- 感知与参数 -->
          <template v-if="mainTab === 'setup'">
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
                <div class="flex items-center justify-between gap-2">
                  <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
                    <i class="fa-solid fa-eye text-sky-400"></i> 数据与感知
                    <span
                      class="text-[10px] px-1.5 py-0.5 rounded font-normal"
                      :class="perceiveOk ? 'bg-emerald-500/15 text-emerald-400' : perceiveError ? 'bg-red-500/15 text-red-400' : 'bg-zinc-800 text-zinc-500'"
                    >
                      {{ perceiveOk ? 'PostgreSQL 真实数据' : perceiveError ? '读取失败' : '未加载' }}
                    </span>
                  </h4>
                  <button
                    @click="loadPerceive"
                    :disabled="perceiveLoading"
                    class="px-2.5 py-1 rounded-lg text-[10px] bg-sky-600/20 text-sky-300 border border-sky-500/30 hover:bg-sky-600/30 disabled:opacity-50"
                  >
                    <i class="fa-solid fa-database mr-1" :class="{ 'fa-spinner animate-spin': perceiveLoading }"></i>
                    {{ perceiveLoading ? '读取中' : '读取真实行情' }}
                  </button>
                </div>
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
                  <div class="col-span-2">
                    <button
                      type="button"
                      @click="fetchRangeData"
                      :disabled="rangeLoading || !config.tsCode || !config.start || !config.end"
                      class="w-full px-2.5 py-1.5 rounded-lg text-[11px] bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30 disabled:opacity-50"
                    >
                      <i class="fa-solid fa-cloud-arrow-down mr-1" :class="{ 'fa-spinner animate-spin': rangeLoading }"></i>
                      {{ rangeLoading ? '正在检查并补齐…' : '获取数据' }}
                    </button>
                    <p v-if="rangeError" class="mt-1.5 text-[10px] text-red-400">{{ rangeError }}</p>
                    <p v-else-if="rangeReport" class="mt-1.5 text-[10px] text-zinc-400 leading-relaxed">
                      {{ rangeReport.table }} · 应有 {{ rangeReport.expected }} 个交易日，库内原有 {{ rangeReport.existing_before }}，缺口 {{ rangeReport.missing_before }}
                      <template v-if="rangeReport.skipped"> · 已齐，无需补拉</template>
                      <template v-else> · 本次写入 {{ rangeUpserted }}，补后仍缺 {{ rangeReport.still_missing }}</template>
                      <span v-if="rangeReport.note" class="text-zinc-500"> · {{ rangeReport.note }}</span>
                    </p>
                    <p v-else class="mt-1.5 text-[10px] text-zinc-600">对照所选区间检查 PostgreSQL，缺口则从 Tushare 兼容源写入对应表</p>
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">初始资金</label>
                    <input v-model.number="config.cash" type="number" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">仓位比例%</label>
                    <input v-model.number="config.positionPct" type="number" min="1" max="100" step="1" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">最大手数（0=不限）</label>
                    <input v-model.number="config.maxLots" type="number" min="0" step="1" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">波动分位窗口</label>
                    <input v-model.number="config.volWindow" type="number" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <label class="col-span-2 flex items-start gap-2 text-[10px] text-zinc-400 cursor-pointer select-none">
                    <input v-model="config.elderLadder" type="checkbox" class="mt-0.5 rounded border-zinc-600 bg-zinc-800 text-sky-500" />
                    <span class="leading-relaxed">
                      高龄阶梯仓位：信号开仓后，跌 15%/25% 增持 10%/25%；涨 25%/35%/45%/60% 减仓 10%/20%/30%/40%，涨 100% 清仓。
                      策略出场信号则全仓卖掉。中间涨跌只要没出场信号，就按阶梯操作。
                    </span>
                  </label>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">佣金(万分)</label>
                    <input v-model.number="config.commissionWan" type="number" min="0" max="10" step="0.1" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">最低佣金(元)</label>
                    <input v-model.number="config.commissionMin" type="number" min="0" max="20" step="1" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500 block mb-1">印花税(万分,仅卖出)</label>
                    <input v-model.number="config.stampWan" type="number" min="0" max="20" step="0.1" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                  </div>
                  <p class="col-span-2 text-[10px] text-zinc-500 leading-relaxed">
                    首次开仓按仓位比例%（默认 30%），A 股整手 100 股。关掉阶梯则持仓不动直到策略卖出。
                    回测默认佣金万二、最低 5 元，卖出另收印花税万五，会从现金和收益里扣除。
                  </p>
                </div>
                <div class="flex flex-wrap gap-1.5 pt-1">
                  <label v-for="f in featureToggles" :key="f.id" class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                    <input v-model="f.enabled" type="checkbox" class="rounded border-zinc-600" />
                    {{ f.label }}
                  </label>
                </div>

                <div v-if="perceiveError" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                  {{ perceiveError }}
                </div>

                <div v-if="perceiveData" class="space-y-2">
                  <div class="text-xs text-zinc-300 bg-zinc-800/50 rounded-lg px-3 py-2 space-y-1">
                    <div>
                      <span class="text-zinc-500">标的</span>
                      {{ perceiveData.name }} <span class="font-mono text-zinc-400">{{ perceiveData.ts_code }}</span>
                    </div>
                    <div>
                      <span class="text-zinc-500">库内区间</span>
                      {{ perceiveData.date_range?.start }} → {{ perceiveData.date_range?.end }}
                      · {{ perceiveData.bar_count }} 根K线
                    </div>
                    <div>
                      <span class="text-zinc-500">最新</span>
                      {{ perceiveData.latest?.trade_date }}
                      收 {{ perceiveData.latest?.close }}
                      <span :class="(perceiveData.latest?.pct_chg ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'">
                        {{ (perceiveData.latest?.pct_chg ?? 0) >= 0 ? '+' : '' }}{{ perceiveData.latest?.pct_chg }}%
                      </span>
                    </div>
                    <div>
                      <span class="text-zinc-500">市况</span>
                      <span class="text-amber-300 font-medium">{{ perceiveData.regime?.label || state.regime }}</span>
                      <span class="text-zinc-600 ml-2">{{ perceiveData.regime?.detail }}</span>
                    </div>
                  </div>

                  <div v-if="featureEntries.length" class="grid grid-cols-2 gap-1.5">
                    <div v-for="[k, v] in featureEntries" :key="k" class="bg-zinc-950/50 border border-zinc-800 rounded-lg px-2 py-1.5">
                      <div class="text-[9px] text-zinc-500">{{ k }}</div>
                      <div class="text-xs tabular-nums text-zinc-200">{{ v ?? '-' }}</div>
                    </div>
                  </div>

                  <div v-if="perceiveData.bars_preview?.length" class="border border-zinc-800 rounded-xl overflow-hidden">
                    <div class="px-2 py-1.5 text-[10px] text-zinc-500 border-b border-zinc-800">最近 {{ perceiveData.bars_preview.length }} 日（PostgreSQL）</div>
                    <div class="max-h-40 overflow-auto">
                      <table class="w-full text-[10px]">
                        <thead class="sticky top-0 bg-zinc-900">
                          <tr class="text-zinc-500 border-b border-zinc-800">
                            <th class="text-left py-1 px-2">日期</th>
                            <th class="text-right py-1 px-2">收盘</th>
                            <th class="text-right py-1 px-2">涨跌%</th>
                            <th class="text-right py-1 px-2">成交量</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="b in [...perceiveData.bars_preview].reverse()" :key="b.trade_date" class="border-b border-zinc-800/40">
                            <td class="py-1 px-2 text-zinc-400">{{ b.trade_date }}</td>
                            <td class="py-1 px-2 text-right tabular-nums">{{ b.close }}</td>
                            <td class="py-1 px-2 text-right tabular-nums" :class="(b.pct_chg ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'">{{ b.pct_chg }}</td>
                            <td class="py-1 px-2 text-right tabular-nums text-zinc-500">{{ b.vol }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>

                <div v-else-if="state.regime" class="text-xs text-zinc-400 bg-zinc-800/50 rounded-lg px-3 py-2">
                  当前识别状态：<span class="text-amber-300 font-medium">{{ regimeLabel(state.regime) }}</span>
                  <span class="text-zinc-600 ml-2">{{ state.analysisNote }}</span>
                </div>
              </div>

              <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
                <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
                  <i class="fa-solid fa-sliders text-violet-400"></i>
                  策略参数 · {{ selectedStrategy?.name || '未选择' }}
                </h4>
                <p v-if="selectedStrategy" class="text-[11px] text-zinc-500 leading-relaxed whitespace-pre-line">{{ selectedStrategy.logic }}</p>
                <p v-if="selectedStrategy?.profit" class="text-[11px] text-amber-200/80 leading-relaxed bg-amber-950/20 border border-amber-900/30 rounded-lg px-2.5 py-2">{{ selectedStrategy.profit }}</p>
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

            <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
                    <i class="fa-solid fa-layer-group text-amber-400"></i>
                    跨股稳健回测
                  </h4>
                  <p class="mt-1 text-[11px] text-zinc-500 leading-relaxed max-w-3xl">
                    不按后来谁涨得多挑股票。默认用回测开始后前 20 个交易日成交额选出约 100 只主板流动性股票，
                    按代码切成寻参 / 确认两半。买卖会按沪深300当时的阶段归类：同一套信号在上升趋势和下降趋势里效果不同，只在当前阶段样本足够时才建议采用。
                  </p>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                  <label class="text-[10px] text-zinc-500">股票数
                    <input v-model.number="universeCfg.n" type="number" min="10" max="150" class="ml-1 w-16 bg-zinc-800 border border-zinc-700 rounded px-1.5 py-1 text-xs" />
                  </label>
                  <select v-model="universeCfg.board" class="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs">
                    <option value="main">主板流动性</option>
                    <option value="all">库内全部（仍按成交额）</option>
                    <option value="chinext">创业板</option>
                    <option value="star">科创板</option>
                  </select>
                  <label class="flex items-center gap-1 text-[10px] text-zinc-400 cursor-pointer">
                    <input v-model="universeCfg.search" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-amber-500" />
                    稳健寻参（确认半区验收）
                  </label>
                  <button
                    type="button"
                    @click="runUniverseBacktest"
                    :disabled="universeLoading || !selectedStrategy || !config.start || !config.end"
                    class="px-2.5 py-1 rounded-lg text-[11px] bg-amber-600/20 text-amber-200 border border-amber-500/30 hover:bg-amber-600/30 disabled:opacity-50"
                  >
                    <i class="fa-solid fa-flask mr-1" :class="{ 'fa-spinner animate-spin': universeLoading }"></i>
                    {{ universeLoading ? '批量回测中…' : '跑 100 只' }}
                  </button>
                </div>
              </div>
              <p v-if="universeError" class="text-[11px] text-red-400">{{ universeError }}</p>
              <template v-if="universeResult">
                <p class="text-[10px] text-zinc-500">
                  {{ universeResult.strategy_name }} · {{ universeResult.date_range?.start }} → {{ universeResult.date_range?.end }}
                  · 池 {{ universeResult.universe_n }} · 测成 {{ universeResult.tested_n }} · 跳过 {{ universeResult.skipped_n }}
                  · {{ universeResult.elapsed_ms }} ms
                </p>
                <p v-if="universeResult.note" class="text-[10px] text-amber-200/80 bg-amber-950/20 border border-amber-900/30 rounded-lg px-3 py-2">{{ universeResult.note }}</p>
                <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
                  <div v-for="m in universeSummaryCards" :key="m.key" class="bg-zinc-950 border border-zinc-800 rounded-xl px-3 py-2.5">
                    <div class="text-[10px] text-zinc-500 mb-0.5">{{ m.label }}</div>
                    <div :class="m.color" class="text-sm font-bold tabular-nums">{{ m.value }}</div>
                  </div>
                </div>
                <div v-if="universeResult.suggested" class="rounded-xl border px-3 py-2 text-[11px]" :class="universeResult.suggested.accepted ? 'border-emerald-800 bg-emerald-950/20 text-emerald-200' : 'border-zinc-700 bg-zinc-950 text-zinc-400'">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span>{{ universeResult.suggested.note }}</span>
                    <button
                      v-if="universeResult.suggested.accepted"
                      type="button"
                      class="px-2 py-1 rounded bg-emerald-700/40 text-emerald-100 text-[10px]"
                      @click="applySuggestedParams"
                    >采用确认区参数</button>
                  </div>
                  <div class="mt-1 text-[10px] font-mono text-zinc-400">{{ JSON.stringify(universeResult.suggested.params) }}</div>
                </div>
                <div v-if="universeResult.signal_stats" class="space-y-2">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <div class="text-xs text-zinc-300">
                      当前阶段
                      <span class="ml-1 px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300">{{ universeResult.signal_stats.current_regime_label }}</span>
                      <span class="ml-2 text-[10px] text-zinc-500">沪深300 · 完整交易 {{ universeResult.signal_stats.trip_count }} 笔 · 建议来自{{ universeResult.signal_stats.advice_from }}</span>
                    </div>
                    <div class="flex gap-1 text-[10px]">
                      <button type="button" class="px-2 py-0.5 rounded" :class="signalFilter === 'current' ? 'bg-sky-600/30 text-sky-200' : 'bg-zinc-800 text-zinc-500'" @click="signalFilter = 'current'">当前阶段</button>
                      <button type="button" class="px-2 py-0.5 rounded" :class="signalFilter === 'all' ? 'bg-sky-600/30 text-sky-200' : 'bg-zinc-800 text-zinc-500'" @click="signalFilter = 'all'">全部阶段</button>
                    </div>
                  </div>
                  <div v-if="universeResult.signal_stats.advice?.length" class="flex flex-wrap gap-1.5">
                    <span
                      v-for="a in universeResult.signal_stats.advice"
                      :key="a.kind + a.signal"
                      class="text-[10px] px-2 py-1 rounded border"
                      :class="a.action === '采用' ? 'border-emerald-800 bg-emerald-950/30 text-emerald-200' : a.action === '暂停' ? 'border-red-900 bg-red-950/30 text-red-300' : a.action === '慎用' ? 'border-amber-800 bg-amber-950/30 text-amber-200' : 'border-zinc-700 bg-zinc-950 text-zinc-400'"
                      :title="a.note"
                    >{{ a.kind }} · {{ a.signal }} · {{ a.action }} · n={{ a.n }}</span>
                  </div>
                  <div class="grid grid-cols-1 lg:grid-cols-2 gap-2">
                    <div class="bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden">
                      <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800">买入信号 × 阶段</div>
                      <div class="max-h-56 overflow-auto">
                        <table class="w-full text-xs">
                          <thead class="sticky top-0 bg-zinc-950 text-zinc-500">
                            <tr>
                              <th class="text-left py-1.5 px-3">信号</th>
                              <th class="text-left py-1.5 px-2">阶段</th>
                              <th class="text-right py-1.5 px-2">次数</th>
                              <th class="text-right py-1.5 px-2">胜率</th>
                              <th class="text-right py-1.5 px-2">中位收益</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="row in filteredSignalRows(universeResult.signal_stats.entry_rows)" :key="row.signal + row.regime" class="border-b border-zinc-800/40">
                              <td class="py-1 px-3 text-zinc-300">{{ row.signal }}</td>
                              <td class="py-1 px-2 text-zinc-500">{{ row.regime_label }}</td>
                              <td class="py-1 px-2 text-right tabular-nums text-zinc-400">{{ row.n }}</td>
                              <td class="py-1 px-2 text-right tabular-nums">{{ fmtPct(row.win_rate) }}</td>
                              <td class="py-1 px-2 text-right tabular-nums" :class="row.median_ret >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.median_ret) }}</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                    <div class="bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden">
                      <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800">卖出信号 × 阶段</div>
                      <div class="max-h-56 overflow-auto">
                        <table class="w-full text-xs">
                          <thead class="sticky top-0 bg-zinc-950 text-zinc-500">
                            <tr>
                              <th class="text-left py-1.5 px-3">信号</th>
                              <th class="text-left py-1.5 px-2">阶段</th>
                              <th class="text-right py-1.5 px-2">次数</th>
                              <th class="text-right py-1.5 px-2">中位收益</th>
                              <th class="text-right py-1.5 px-2">卖后5日</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="row in filteredSignalRows(universeResult.signal_stats.exit_rows)" :key="row.signal + row.regime" class="border-b border-zinc-800/40">
                              <td class="py-1 px-3 text-zinc-300">{{ row.signal }}</td>
                              <td class="py-1 px-2 text-zinc-500">{{ row.regime_label }}</td>
                              <td class="py-1 px-2 text-right tabular-nums text-zinc-400">{{ row.n }}</td>
                              <td class="py-1 px-2 text-right tabular-nums" :class="row.median_ret >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.median_ret) }}</td>
                              <td class="py-1 px-2 text-right tabular-nums text-zinc-400">{{ row.median_after_5d == null ? '—' : fmtPct(row.median_after_5d) }}</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>
                <div class="bg-zinc-950 border border-zinc-800 rounded-2xl overflow-hidden">
                  <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800">按稳健卡尔玛排序 · 点击一行可切到单票回测</div>
                  <div class="max-h-80 overflow-auto">
                    <table class="w-full text-xs">
                      <thead class="sticky top-0 bg-zinc-950">
                        <tr class="border-b border-zinc-800 text-zinc-500">
                          <th class="text-left py-2 px-3">代码</th>
                          <th class="text-left py-2 px-2">名称</th>
                          <th class="text-left py-2 px-2">分组</th>
                          <th class="text-right py-2 px-2">策略收益</th>
                          <th class="text-right py-2 px-2">回撤</th>
                          <th class="text-right py-2 px-2">买持有</th>
                          <th class="text-right py-2 px-2">超额</th>
                          <th class="text-right py-2 px-2">卡尔玛</th>
                          <th class="text-right py-2 px-2">成交</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="row in universeResult.rows"
                          :key="row.ts_code"
                          class="border-b border-zinc-800/50 hover:bg-zinc-800/40 cursor-pointer"
                          @click="openUniverseStock(row)"
                        >
                          <td class="py-1.5 px-3 font-mono text-zinc-300">{{ row.ts_code }}</td>
                          <td class="py-1.5 px-2 text-zinc-400">{{ row.name }}</td>
                          <td class="py-1.5 px-2">
                            <span class="text-[10px] px-1.5 py-0.5 rounded" :class="row.split === '确认' ? 'bg-sky-500/15 text-sky-300' : 'bg-zinc-800 text-zinc-500'">{{ row.split || '—' }}</span>
                          </td>
                          <td class="py-1.5 px-2 text-right tabular-nums" :class="row.total_return >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.total_return) }}</td>
                          <td class="py-1.5 px-2 text-right tabular-nums text-amber-400">{{ fmtPct(row.max_drawdown) }}</td>
                          <td class="py-1.5 px-2 text-right tabular-nums text-zinc-400">{{ fmtPct(row.buyhold_return) }}</td>
                          <td class="py-1.5 px-2 text-right tabular-nums" :class="row.excess_return >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.excess_return) }}</td>
                          <td class="py-1.5 px-2 text-right tabular-nums text-zinc-200">{{ Number(row.calmar || 0).toFixed(2) }}</td>
                          <td class="py-1.5 px-2 text-right tabular-nums text-zinc-500">{{ row.trade_count }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </template>
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
                <span
                  v-if="backtestResult.options_applied?.lookback_guard"
                  :class="backtestResult.options_applied.lookback_guard.enabled ? 'text-emerald-400' : 'text-red-400'"
                  class="px-2 py-0.5 rounded bg-zinc-800"
                  :title="backtestResult.options_applied.lookback_guard.detail"
                >
                  {{ backtestResult.options_applied.lookback_guard.label }}:
                  {{ backtestResult.options_applied.lookback_guard.status }}
                </span>
                <span
                  v-else
                  :class="backtestResult.lookback_ok ? 'text-emerald-400' : 'text-red-400'"
                  class="px-2 py-0.5 rounded bg-zinc-800"
                >
                  lookback_guard: {{ backtestResult.lookback_ok ? '通过' : '拦截' }}
                </span>
                <span
                  v-if="backtestResult.options_applied?.walk_forward"
                  class="text-amber-400 px-2 py-0.5 rounded bg-zinc-800"
                  :title="backtestResult.options_applied.walk_forward.detail"
                >
                  {{ backtestResult.options_applied.walk_forward.label }}:
                  {{ backtestResult.options_applied.walk_forward.status }}
                </span>
                <span v-else-if="backtestResult.walk_forward" class="text-amber-400 px-2 py-0.5 rounded bg-zinc-800">Walk-Forward 已启用</span>
                <span class="text-zinc-600">{{ backtestResult.strategy_name }} · {{ backtestResult.ts_code }}</span>
                <span
                  v-if="backtestResult.options_applied?.position"
                  class="text-sky-300 px-2 py-0.5 rounded bg-zinc-800"
                  :title="backtestResult.options_applied.position.detail"
                >
                  {{ backtestResult.options_applied.position.label }}:
                  {{ backtestResult.options_applied.position.status }}
                </span>
                <span
                  v-if="backtestResult.options_applied?.fees || backtestResult.fees"
                  class="text-amber-200 px-2 py-0.5 rounded bg-zinc-800"
                  :title="(backtestResult.options_applied?.fees || backtestResult.fees)?.detail"
                >
                  {{ (backtestResult.options_applied?.fees || backtestResult.fees)?.status || '已扣佣金/印花税' }}
                </span>
              </div>
              <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
                <div v-for="m in metricCards" :key="m.key" class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2.5">
                  <div class="text-[10px] text-zinc-500 mb-0.5">{{ m.label }}</div>
                  <div :class="m.color" class="text-sm font-bold tabular-nums">{{ m.value }}</div>
                </div>
              </div>
              <p v-if="backtestResult.note" class="mt-2 text-[10px] text-amber-200/80 bg-amber-950/20 border border-amber-900/30 rounded-lg px-3 py-2">{{ backtestResult.note }}</p>
              <div class="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px]">
                <div class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2">
                  <div class="text-zinc-500">初始本金</div>
                  <div class="text-zinc-200 tabular-nums font-medium">{{ fmtMoney(backtestResult.cash) }}</div>
                </div>
                <div class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2">
                  <div class="text-zinc-500">期末权益</div>
                  <div class="tabular-nums font-medium" :class="(backtestResult.final_equity ?? backtestResult.cash) >= (backtestResult.cash || 0) ? 'text-red-400' : 'text-green-400'">
                    {{ fmtMoney(backtestResult.final_equity ?? (backtestResult.cash != null && backtestResult.equity?.length ? backtestResult.cash * backtestResult.equity[backtestResult.equity.length - 1] : null)) }}
                  </div>
                </div>
                <div class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2">
                  <div class="text-zinc-500">区间</div>
                  <div class="text-zinc-300 tabular-nums">{{ backtestResult.date_range?.start || '—' }} → {{ backtestResult.date_range?.end || '—' }}</div>
                </div>
                <div class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2">
                  <div class="text-zinc-500">K线条数 / 成交</div>
                  <div class="text-zinc-300 tabular-nums">{{ backtestResult.bars?.length || backtestResult.bar_count || 0 }} / {{ backtestResult.trades?.length || 0 }}</div>
                </div>
              </div>
              <div class="mt-2 bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
                <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800 flex flex-wrap items-center justify-between gap-2">
                  <span>回测图：K线买卖点 + 策略均线</span>
                  <div class="flex flex-wrap items-center gap-3 text-[10px] font-normal">
                    <label class="flex items-center gap-1 text-zinc-400 cursor-pointer select-none">
                      <input v-model="chartExtras.vol" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-sky-500" />
                      成交量
                    </label>
                    <label class="flex items-center gap-1 text-zinc-400 cursor-pointer select-none">
                      <input v-model="chartExtras.macd" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-sky-500" />
                      MACD
                    </label>
                    <label class="flex items-center gap-1 text-zinc-400 cursor-pointer select-none">
                      <input v-model="chartExtras.rsi" type="checkbox" class="rounded border-zinc-600 bg-zinc-800 text-sky-500" />
                      RSI
                    </label>
                    <span class="text-zinc-600">红三角买入 · 绿倒三角卖出 · 均线随策略参数</span>
                  </div>
                </div>
                <div ref="equityChartEl" class="w-full" :style="{ height: backtestChartHeight + 'px' }"></div>
                <p v-if="chartHint" class="px-3 py-2 text-[10px] text-amber-300/90 border-t border-zinc-800">{{ chartHint }}</p>
              </div>
              <div v-if="backtestResult.walk_forward_rows?.length" class="mt-3 bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
                <div class="px-3 py-2 text-xs font-semibold text-zinc-400 border-b border-zinc-800">Walk-Forward 分段</div>
                <table class="w-full text-xs">
                  <thead>
                    <tr class="border-b border-zinc-800 text-zinc-500">
                      <th class="text-left py-2 px-3">窗口</th>
                      <th class="text-right py-2 px-3">训练收益</th>
                      <th class="text-right py-2 px-3">测试收益</th>
                      <th class="text-right py-2 px-3">测试回撤</th>
                      <th class="text-right py-2 px-3">测试费用</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in backtestResult.walk_forward_rows" :key="row.fold" class="border-b border-zinc-800/50">
                      <td class="py-1.5 px-3 text-zinc-300">#{{ row.fold }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums" :class="row.train_ret >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.train_ret) }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums" :class="row.test_ret >= 0 ? 'text-red-400' : 'text-green-400'">{{ fmtPct(row.test_ret) }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums text-amber-400">{{ fmtPct(row.test_dd) }}</td>
                      <td class="py-1.5 px-3 text-right tabular-nums text-amber-300">{{ fmtMoney(row.test_fee ?? 0) }}</td>
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
                        <th class="text-right py-2 px-3">佣金</th>
                        <th class="text-right py-2 px-3">印花税</th>
                        <th class="text-left py-2 px-3">原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(t, i) in backtestResult.trades" :key="i" class="border-b border-zinc-800/40">
                        <td class="py-1.5 px-3 text-zinc-400">{{ t.date }}</td>
                        <td class="py-1.5 px-3" :class="t.side === '买入' ? 'text-red-400' : 'text-green-400'">{{ t.side }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums">{{ t.price }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums">{{ t.size }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums" :class="(t.pnl || 0) >= 0 ? 'text-red-400' : 'text-green-400'">{{ t.pnl ?? '-' }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums text-zinc-400">{{ t.commission ?? '-' }}</td>
                        <td class="py-1.5 px-3 text-right tabular-nums text-zinc-400">{{ t.stamp_tax ?? '-' }}</td>
                        <td class="py-1.5 px-3 text-zinc-500">{{ t.reason || '-' }}</td>
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
import IntradayTurningPanel from './IntradayTurningPanel.vue'
import ScreenerFlowPanel from './ScreenerFlowPanel.vue'

const MEMORY_KEY = 'strategy_analysis_memory_v1'

const runMode = ref<'A' | 'B'>('A')
const lookbackGuard = computed({
  get: () => !!optionValues.lookback_guard,
  set: (v: boolean) => { optionValues.lookback_guard = v },
})
const useWalkForward = computed({
  get: () => !!optionValues.walk_forward,
  set: (v: boolean) => { optionValues.walk_forward = v },
})
const backtestOptions = ref<any[]>([])
const optionValues = reactive<Record<string, boolean>>({
  lookback_guard: true,
  walk_forward: false,
})
const running = ref(false)
const stopAuto = ref(false)
const mainTab = ref<'setup' | 'screener' | 'backtest' | 'memory' | 'logs' | 'intraday'>('setup')
const mainTabs = [
  { id: 'setup', label: '感知与参数' },
  { id: 'screener', label: '选股器' },
  { id: 'intraday', label: '日内高低点' },
  { id: 'backtest', label: '回测验证' },
  { id: 'memory', label: '策略记忆' },
  { id: 'logs', label: 'Agent 日志' },
]
const toolboxMode = ref<'strategy' | 'screener' | 'intraday'>('strategy')

const cycleNodes = [
  { id: 'analyze', label: '分析' },
  { id: 'hypothesize', label: '假设' },
  { id: 'backtest', label: '回测' },
  { id: 'reflect', label: '反思' },
]

const strategies = ref<any[]>([])
const catalogLoading = ref(false)
const catalogError = ref('')
const selectedStrategy = ref<any>(null)
const paramValues = reactive<Record<string, number>>({})

function selectStrategy(s: any) {
  toolboxMode.value = 'strategy'
  if (mainTab.value === 'intraday') mainTab.value = 'setup'
  selectedStrategy.value = s
  for (const key of Object.keys(paramValues)) delete paramValues[key]
  for (const p of s.params || []) paramValues[p.name] = p.default
}

function openScreener() {
  toolboxMode.value = 'screener'
  mainTab.value = 'screener'
}

function onScreenerActivated() {
  toolboxMode.value = 'screener'
  mainTab.value = 'screener'
}

function onScreenerAsof(asof: string) {
  if (!asof) return
  config.end = asof
}

function openIntraday() {
  toolboxMode.value = 'intraday'
  mainTab.value = 'intraday'
}

function strategyFitsRegime(s: any, regime: string) {
  if (!regime) return false
  const list: string[] = s.suitable_regimes
  if (Array.isArray(list) && list.length) return list.includes(regime)
  return String(s.suitable_regime || '').includes(regime)
}

async function loadCatalog() {
  catalogLoading.value = true
  catalogError.value = ''
  try {
    const res = await fetch('/strategy/catalog')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    strategies.value = data.strategies || []
    if (!selectedStrategy.value && strategies.value.length) {
      selectStrategy(strategies.value[0])
    } else if (selectedStrategy.value) {
      const fresh = strategies.value.find((s: any) => s.id === selectedStrategy.value.id)
      if (fresh) selectStrategy(fresh)
    }
    pushLog(`[目录] 已从后端加载 ${strategies.value.length} 种策略`, 'ok')
  } catch (e: any) {
    catalogError.value = e?.message || String(e)
    pushLog(`[目录] 加载失败: ${catalogError.value}`, 'error')
  } finally {
    catalogLoading.value = false
  }
}

async function loadOptions() {
  try {
    const res = await fetch('/strategy/options')
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
    backtestOptions.value = data.options || []
    const defaults = data.defaults || {}
    for (const [k, v] of Object.entries(defaults)) {
      optionValues[k] = !!v
    }
    // 若后端尚未返回选项，保留本地兜底两项
    if (!backtestOptions.value.length) {
      backtestOptions.value = [
        { key: 'lookback_guard', label: '前视保护', label_en: 'lookback_guard', default: true, recommended: true, description: '次日开盘成交，避免前视' },
        { key: 'walk_forward', label: 'Walk-Forward', label_en: 'walk_forward', default: false, description: '滚动样本外检验' },
      ]
    }
    pushLog(`[选项] 已加载回测开关：${backtestOptions.value.map((o: any) => o.label).join('、')}`, 'ok')
  } catch (e: any) {
    backtestOptions.value = [
      { key: 'lookback_guard', label: '前视保护', label_en: 'lookback_guard', default: true, recommended: true, description: '次日开盘成交，避免前视' },
      { key: 'walk_forward', label: 'Walk-Forward', label_en: 'walk_forward', default: false, description: '滚动样本外检验' },
    ]
    pushLog(`[选项] 拉取失败，使用本地兜底: ${e?.message || e}`, 'error')
  }
}

function shanghaiTodayUtc(): Date {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const y = Number(parts.find((p) => p.type === 'year')?.value)
  const m = Number(parts.find((p) => p.type === 'month')?.value)
  const d = Number(parts.find((p) => p.type === 'day')?.value)
  return new Date(Date.UTC(y, m - 1, d))
}

/** 周末回退到周五；节假日由 /strategy/screener/asof 再校正 */
function latestWeekdayYmd(): string {
  const dt = shanghaiTodayUtc()
  while (dt.getUTCDay() === 0 || dt.getUTCDay() === 6) {
    dt.setUTCDate(dt.getUTCDate() - 1)
  }
  return dt.toISOString().slice(0, 10)
}

const config = reactive({
  tsCode: '000001.SZ',
  benchmark: '000300.SH',
  start: '2024-01-01',
  end: latestWeekdayYmd(),
  cash: 1000000,
  positionPct: 30,
  maxLots: 0,
  reservePct: 2,
  elderLadder: true,
  commissionWan: 2,
  commissionMin: 5,
  stampWan: 5,
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
const universeCfg = reactive({ n: 100, board: 'main', search: false })
const universeLoading = ref(false)
const universeError = ref('')
const universeResult = ref<any>(null)
const signalFilter = ref<'current' | 'all'>('current')
function filteredSignalRows(rows: any[] | undefined) {
  const list = rows || []
  if (signalFilter.value === 'all') return list
  const cur = universeResult.value?.signal_stats?.current_regime
  return list.filter((r) => r.regime === cur)
}
const universeSummaryCards = computed(() => {
  const all = universeResult.value?.summary?.all
  const confirm = universeResult.value?.summary?.confirm
  if (!all) return []
  return [
    { key: 'n', label: '有效股票', value: String(all.n ?? 0), color: 'text-zinc-200' },
    { key: 'pos', label: '盈利占比', value: fmtPct(all.pct_positive ?? 0), color: (all.pct_positive ?? 0) >= 0.5 ? 'text-red-400' : 'text-amber-300' },
    { key: 'med', label: '中位收益', value: fmtPct(all.median_return ?? 0), color: (all.median_return ?? 0) >= 0 ? 'text-red-400' : 'text-green-400' },
    { key: 'dd', label: '中位回撤', value: fmtPct(all.median_max_drawdown ?? 0), color: 'text-amber-400' },
    { key: 'cal', label: '中位卡尔玛', value: Number(all.median_calmar ?? 0).toFixed(2), color: 'text-sky-300' },
    { key: 'score', label: '稳健分', value: Number(all.robustness_score ?? 0).toFixed(2), color: 'text-violet-300' },
    { key: 'cmed', label: '确认区中位收益', value: fmtPct(confirm?.median_return ?? 0), color: (confirm?.median_return ?? 0) >= 0 ? 'text-red-400' : 'text-green-400' },
    { key: 'cdd', label: '确认区中位回撤', value: fmtPct(confirm?.median_max_drawdown ?? 0), color: 'text-amber-400' },
  ]
})
const memories = ref<any[]>(loadMemories())
const equityChartEl = ref<HTMLDivElement>()
const chartHint = ref('')
let equityChart: any = null
const chartExtras = reactive({ vol: false, macd: false, rsi: false })
const backtestChartHeight = computed(() => {
  const extra = (chartExtras.vol ? 1 : 0) + (chartExtras.macd ? 1 : 0) + (chartExtras.rsi ? 1 : 0)
  return 460 + extra * 128
})

const perceiveLoading = ref(false)
const perceiveData = ref<any>(null)
const perceiveError = ref('')
const perceiveOk = computed(() => !!perceiveData.value?.ok)
const rangeLoading = ref(false)
const rangeError = ref('')
const rangeReport = ref<any>(null)
const rangeUpserted = computed(() => {
  const u = rangeReport.value?.upserted || {}
  return Object.values(u).reduce((sum: number, n: any) => sum + (Number(n) || 0), 0)
})

const featureEntries = computed(() => {
  const f = perceiveData.value?.features
  if (!f || typeof f !== 'object') return []
  return Object.entries(f)
})

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
    { key: 'commission', label: '佣金', value: fmtMoney(r.fees?.commission_total ?? 0), color: 'text-amber-300' },
    { key: 'stamp', label: '印花税', value: fmtMoney(r.fees?.stamp_total ?? 0), color: 'text-amber-300' },
  ]
})

function fmtMoney(v: any) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

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

async function fetchRangeData() {
  const tsCode = config.tsCode.trim()
  if (!tsCode || !config.start || !config.end) {
    rangeError.value = '请先填写标的代码、开始日期和结束日期'
    return
  }
  rangeLoading.value = true
  rangeError.value = ''
  rangeReport.value = null
  try {
    const extra = config.benchmark && config.benchmark !== tsCode ? [config.benchmark.trim()] : []
    const res = await fetch('/data/selective/ensure-range', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ts_code: tsCode,
        start: config.start,
        end: config.end,
        extra_codes: extra,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data.detail
      rangeError.value = typeof detail === 'string' ? detail : (detail?.error || `HTTP ${res.status}`)
      pushLog(`[获取数据] 失败: ${rangeError.value}`, 'error')
      return
    }
    rangeReport.value = data
    const extraNote = (data.extras || [])
      .map((x: any) => `${x.ts_code} 缺口${x.missing_before ?? '?'} 写入${Object.values(x.upserted || {}).reduce((a: number, b: any) => a + (Number(b) || 0), 0)}`)
      .join('；')
    pushLog(
      `[获取数据] ${data.ts_code} → ${data.table} · 应有${data.expected} 原有${data.existing_before} 缺口${data.missing_before} 写入${Object.values(data.upserted || {}).reduce((a: number, b: any) => a + (Number(b) || 0), 0)} 仍缺${data.still_missing}` +
        (extraNote ? ` · 基准 ${extraNote}` : ''),
      data.still_missing ? 'warn' : 'ok',
    )
    await loadPerceive({ keepDates: true, skipEnsure: true })
  } catch (e: any) {
    rangeError.value = e?.message || String(e)
    pushLog(`[获取数据] 异常: ${rangeError.value}`, 'error')
  } finally {
    rangeLoading.value = false
  }
}

async function loadPerceive(opts: { keepDates?: boolean; skipEnsure?: boolean } = {}) {
  perceiveLoading.value = true
  perceiveError.value = ''
  try {
    // 挑选式：标的缺/旧则后端感知前会补 Datahubco；区间补齐后可跳过 fresh
    if (!opts.skipEnsure) {
      try {
        await fetch('/data/selective/ensure', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ts_code: config.tsCode.trim(), mode: 'fresh' }),
        })
      } catch (_) { /* 后端 perceive 仍会再补一次 */ }
    }

    const feats = featureToggles.filter(f => f.enabled).map(f => f.id).join(',') || 'ma,rsi,vol_pctile'
    const params = new URLSearchParams({
      ts_code: config.tsCode.trim(),
      limit: '500',
      vol_window: String(config.volWindow || 20),
      features: feats,
    })
    if (config.start) params.set('start', config.start)
    if (config.end) params.set('end', config.end)
    const res = await fetch(`/strategy/perceive?${params}`)
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      perceiveData.value = null
      perceiveError.value = data.detail || `HTTP ${res.status}`
      pushLog(`[感知] 失败: ${perceiveError.value}`, 'error')
      return null
    }
    perceiveData.value = data
    if (data.regime?.regime) {
      state.regime = data.regime.regime
      state.analysisNote = data.regime.detail || `${data.name} ${data.bar_count} bars`
    }
    if (!opts.keepDates) {
      if (data.date_range?.start) config.start = data.date_range.start
    }
    pushLog(
      `[感知] PostgreSQL · ${data.name}(${data.ts_code}) · ${data.bar_count}根 · ${data.regime?.label} · 最新${data.latest?.trade_date} 收${data.latest?.close}`,
      'ok'
    )
    return data
  } catch (e: any) {
    perceiveData.value = null
    perceiveError.value = e?.message || String(e)
    pushLog(`[感知] 异常: ${perceiveError.value}`, 'error')
    return null
  } finally {
    perceiveLoading.value = false
  }
}

/** 分析：调用后端感知（PostgreSQL + 挑选式补数） */
async function runAnalyze() {
  const data = perceiveData.value?.ok ? perceiveData.value : await loadPerceive()
  if (data?.ok) {
    state.regime = data.regime?.regime || 'unknown'
    const featLabels = featureToggles.filter(f => f.enabled).map(f => f.label).join('+') || '无'
    state.analysisNote =
      data.regime?.detail ||
      ('标的 ' + (data.name || '') + '(' + (data.ts_code || '') + ')，特征 ' + featLabels)
    state.step = 'analyze'
    if (!state.completed.includes('analyze')) state.completed.push('analyze')
    pushLog('[分析] 后端感知 regime=' + state.regime + ' · ' + state.analysisNote, 'ok')
    return true
  }
  state.regime = 'unknown'
  state.analysisNote = perceiveError.value || '未能读取 PostgreSQL 行情'
  state.step = 'analyze'
  if (!state.completed.includes('analyze')) state.completed.push('analyze')
  pushLog('[分析] 失败 — ' + state.analysisNote, 'error')
  return false
}

/** 假设：POST /strategy/hypothesize */
async function runHypothesize() {
  if (!selectedStrategy.value) {
    pushLog('[假设] 请先选择策略', 'error')
    return false
  }
  try {
    const res = await fetch('/strategy/hypothesize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_id: selectedStrategy.value.id,
        regime: state.regime || 'unknown',
        params: { ...paramValues },
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status))
    state.hypothesis = data.hypothesis
    if (data.suggested_strategy && runMode.value === 'B') {
      const better = strategies.value.find((s) => s.id === data.suggested_strategy.id)
      if (better) {
        selectStrategy(better)
        pushLog('[假设] 后端建议切换 → ' + better.name, 'ok')
      }
    }
    state.step = 'hypothesize'
    if (!state.completed.includes('hypothesize')) state.completed.push('hypothesize')
    pushLog('[假设] ' + state.hypothesis, 'ok')
    return true
  } catch (e) {
    pushLog('[假设] 失败: ' + (e && e.message ? e.message : String(e)), 'error')
    return false
  }
}

/** 回测：POST /strategy/backtest（真实日线） */
async function runBacktest() {
  if (!selectedStrategy.value) {
    pushLog('[回测] 请先选择策略', 'error')
    return false
  }
  if (!lookbackGuard.value) {
    pushLog('[回测] lookback_guard 已关闭 — 同日收盘成交，存在前视风险', 'error')
  }
  try {
    const res = await fetch('/strategy/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_id: selectedStrategy.value.id,
        ts_code: config.tsCode.trim(),
        params: {
          ...paramValues,
          position_pct: paramValues.position_pct != null ? Number(paramValues.position_pct) : config.positionPct,
          max_lots: config.maxLots,
          reserve_pct: config.reservePct,
          position_mode: config.elderLadder ? 'elder_ladder' : 'percent',
          commission_rate: (Number(config.commissionWan) || 0) / 10000,
          commission_min: config.commissionMin,
          stamp_rate: (Number(config.stampWan) || 0) / 10000,
        },
        start: config.start || null,
        end: config.end || null,
        cash: config.cash,
        lookback_guard: lookbackGuard.value,
        walk_forward: useWalkForward.value,
        benchmark: config.benchmark,
        limit: 800,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : (data.detail ? JSON.stringify(data.detail) : ('HTTP ' + res.status))
      throw new Error(detail)
    }
    backtestResult.value = data
    state.step = 'backtest'
    if (!state.completed.includes('backtest')) state.completed.push('backtest')
    pushLog(
      '[回测] 后端 · ' + data.strategy_name + ' · 收益 ' + fmtPct(data.total_return) +
        ' · 回撤 ' + fmtPct(data.max_drawdown) + ' · 夏普 ' + data.sharpe +
        (data.lookback_ok ? ' · guard=ON' : ' · guard=OFF') +
        ' · bars=' + data.bar_count,
      data.lookback_ok ? 'ok' : 'error'
    )
    mainTab.value = 'backtest'
    await nextTick()
    requestAnimationFrame(() => renderBacktestCharts())
    return true
  } catch (e) {
    pushLog('[回测] 失败: ' + (e && e.message ? e.message : String(e)), 'error')
    return false
  }
}

function currentStrategyParams() {
  return {
    ...paramValues,
    position_pct: paramValues.position_pct != null ? Number(paramValues.position_pct) : config.positionPct,
    max_lots: config.maxLots,
    reserve_pct: config.reservePct,
    position_mode: config.elderLadder ? 'elder_ladder' : 'percent',
    commission_rate: (Number(config.commissionWan) || 0) / 10000,
    commission_min: config.commissionMin,
    stamp_rate: (Number(config.stampWan) || 0) / 10000,
  }
}

async function runUniverseBacktest() {
  if (!selectedStrategy.value) {
    pushLog('[跨股] 请先选择策略', 'error')
    return
  }
  universeLoading.value = true
  universeError.value = ''
  try {
    const res = await fetch('/strategy/backtest/universe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        strategy_id: selectedStrategy.value.id,
        params: currentStrategyParams(),
        start: config.start,
        end: config.end,
        cash: config.cash,
        lookback_guard: lookbackGuard.value,
        n: universeCfg.n,
        board: universeCfg.board,
        search: universeCfg.search,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : (data.detail ? JSON.stringify(data.detail) : ('HTTP ' + res.status))
      throw new Error(detail)
    }
    universeResult.value = data
    const all = data.summary?.all || {}
    pushLog(
      `[跨股] ${data.tested_n} 只 · 中位收益 ${fmtPct(all.median_return)} · 中位回撤 ${fmtPct(all.median_max_drawdown)} · 盈利占比 ${fmtPct(all.pct_positive)} · 稳健分 ${all.robustness_score}`,
      'ok',
    )
  } catch (e: any) {
    universeError.value = e?.message || String(e)
    pushLog('[跨股] 失败: ' + universeError.value, 'error')
  } finally {
    universeLoading.value = false
  }
}

function applySuggestedParams() {
  const p = universeResult.value?.suggested?.params
  if (!p) return
  for (const [k, v] of Object.entries(p)) {
    if (v == null) continue
    paramValues[k] = Number(v)
  }
  pushLog('[跨股] 已写入确认区验收通过的参数，请再跑一次单票或批量回测', 'ok')
}

async function openUniverseStock(row: any) {
  if (!row?.ts_code) return
  config.tsCode = row.ts_code
  pushLog(`[跨股] 切到 ${row.ts_code} ${row.name || ''}`, 'ok')
  await runBacktest()
}

/** 反思：POST /strategy/reflect */
async function runReflect() {
  const r = backtestResult.value
  if (!r) {
    pushLog('[反思] 无回测结果，跳过', 'error')
    return false
  }
  try {
    const res = await fetch('/strategy/reflect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        backtest_result: r,
        regime: state.regime,
        max_drawdown_pct: harness.maxDrawdownPct,
        min_sharpe: harness.minSharpe,
        mode: runMode.value,
        auto_save_memory: harness.autoSaveMemory,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status))
    state.reflection = data.reflection
    state.step = 'reflect'
    if (!state.completed.includes('reflect')) state.completed.push('reflect')
    pushLog('[反思] ' + state.reflection, data.success ? 'ok' : 'error')

    if (data.save_memory && data.memory_item) {
      memories.value = [data.memory_item, ...memories.value].slice(0, 50)
      saveMemories()
      pushLog('[记忆] 已写入策略记忆库', 'ok')
      mainTab.value = 'memory'
    }
    if (data.param_tweak && runMode.value === 'B') {
      paramValues[data.param_tweak.name] = data.param_tweak.value
      pushLog('[Agent] 后端建议改参 ' + data.param_tweak.name + '=' + data.param_tweak.value, 'ok')
    }
    return data
  } catch (e) {
    pushLog('[反思] 失败: ' + (e && e.message ? e.message : String(e)), 'error')
    return false
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
    if (next === 'analyze') await runAnalyze()
    else if (next === 'hypothesize') await runHypothesize()
    else if (next === 'backtest') await runBacktest()
    else if (next === 'reflect') await runReflect()
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
  pushLog('===== 模式 B 启动 · 最多 ' + harness.maxIterations + ' 轮 =====', 'ok')

  try {
    for (let i = 1; i <= harness.maxIterations; i++) {
      if (stopAuto.value) {
        pushLog('用户停止自动循环', 'error')
        break
      }
      state.iteration = i
      state.completed = []
      state.step = ''
      pushLog('--- 迭代 ' + i + '/' + harness.maxIterations + ' ---')

      if (stopAuto.value) break
      await runAnalyze()

      if (stopAuto.value) break
      const better = strategies.value.find(s => strategyFitsRegime(s, state.regime))
      if (better && better.id !== selectedStrategy.value.id) {
        selectStrategy(better)
        pushLog('[Agent] 根据 regime 切换策略 → ' + better.name, 'ok')
      }
      await runHypothesize()

      if (stopAuto.value) break
      const btOk = await runBacktest()
      if (!btOk) continue

      if (stopAuto.value) break
      const ref = await runReflect()
      if (ref && ref.success) {
        pushLog('[Agent] 迭代 ' + i + ' 达到目标，提前结束', 'ok')
        break
      }
    }
  } finally {
    running.value = false
    stopAuto.value = false
    pushLog('===== 模式 B 结束 =====', 'ok')
  }
}

function smaSeries(values: number[], period: number): (number | null)[] {
  const n = Math.max(1, Math.floor(period))
  const out: (number | null)[] = Array(values.length).fill(null)
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    const v = Number(values[i])
    if (!Number.isFinite(v)) {
      sum = 0
      continue
    }
    sum += v
    if (i >= n) {
      const old = Number(values[i - n])
      if (Number.isFinite(old)) sum -= old
    }
    if (i >= n - 1) out[i] = Number((sum / n).toFixed(4))
  }
  return out
}

function emaSeries(values: number[], period: number): number[] {
  const n = Math.max(1, Math.floor(period))
  const k = 2 / (n + 1)
  const out: number[] = []
  let prev: number | null = null
  for (const raw of values) {
    const v = Number(raw)
    if (!Number.isFinite(v)) {
      out.push(prev ?? 0)
      continue
    }
    prev = prev == null ? v : v * k + prev * (1 - k)
    out.push(Number(prev.toFixed(4)))
  }
  return out
}

function rsiSeries(values: number[], period = 14): (number | null)[] {
  const n = Math.max(2, Math.floor(period))
  const out: (number | null)[] = Array(values.length).fill(null)
  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i < values.length; i++) {
    const chg = Number(values[i]) - Number(values[i - 1])
    const gain = chg > 0 ? chg : 0
    const loss = chg < 0 ? -chg : 0
    if (i < n) {
      avgGain += gain
      avgLoss += loss
      continue
    }
    if (i === n) {
      avgGain = (avgGain + gain) / n
      avgLoss = (avgLoss + loss) / n
    } else {
      avgGain = (avgGain * (n - 1) + gain) / n
      avgLoss = (avgLoss * (n - 1) + loss) / n
    }
    const rs = avgLoss === 0 ? (avgGain > 0 ? 100 : 0) : avgGain / avgLoss
    out[i] = Number((100 - 100 / (1 + rs)).toFixed(2))
  }
  return out
}

function macdSeries(values: number[], fast = 12, slow = 26, signal = 9) {
  const emaFast = emaSeries(values, fast)
  const emaSlow = emaSeries(values, slow)
  const dif = emaFast.map((a, i) => Number((a - emaSlow[i]).toFixed(4)))
  const dea = emaSeries(dif, signal)
  const hist = dif.map((a, i) => Number((2 * (a - dea[i])).toFixed(4)))
  return { dif, dea, hist }
}

function rollingPrev(values: number[], period: number, mode: 'max' | 'min'): (number | null)[] {
  const n = Math.max(1, Math.floor(period))
  const out: (number | null)[] = Array(values.length).fill(null)
  for (let i = n; i < values.length; i++) {
    const slice = values.slice(i - n, i)
    out[i] = Number((mode === 'max' ? Math.max(...slice) : Math.min(...slice)).toFixed(4))
  }
  return out
}

function fmtAxisAmt(v: number) {
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return String(v)
}

function strategyOverlayLines(r: any, closes: number[], highs: number[], lows: number[]) {
  const id = String(r.strategy_id || selectedStrategy.value?.id || '')
  const p = { ...(paramValues || {}), ...(r.params || {}) }
  const lines: { name: string; data: (number | null)[]; color: string; dashed?: boolean }[] = []
  if (id === 'dual_ma') {
    const fast = Number(p.fast) || 5
    const slow = Number(p.slow) || 20
    lines.push({ name: `MA${fast}`, data: smaSeries(closes, fast), color: '#fbbf24' })
    lines.push({ name: `MA${slow}`, data: smaSeries(closes, slow), color: '#c084fc' })
  } else if (id === 'mean_reversion') {
    const w = Number(p.window) || 20
    lines.push({ name: `MA${w}`, data: smaSeries(closes, w), color: '#fbbf24' })
  } else if (id === 'boll_revert') {
    const w = Number(p.window) || 20
    const nstd = Number(p.nstd) || 2
    const mid = smaSeries(closes, w)
    const upper: (number | null)[] = []
    const lower: (number | null)[] = []
    for (let i = 0; i < closes.length; i++) {
      if (mid[i] == null || i < w - 1) {
        upper.push(null)
        lower.push(null)
        continue
      }
      const slice = closes.slice(i - w + 1, i + 1)
      const mean = mid[i] as number
      const variance = slice.reduce((s, x) => s + (x - mean) ** 2, 0) / w
      const sd = Math.sqrt(variance)
      upper.push(Number((mean + nstd * sd).toFixed(4)))
      lower.push(Number((mean - nstd * sd).toFixed(4)))
    }
    lines.push({ name: 'BOLL上轨', data: upper, color: '#94a3b8', dashed: true })
    lines.push({ name: 'BOLL中轨', data: mid, color: '#eab308' })
    lines.push({ name: 'BOLL下轨', data: lower, color: '#94a3b8', dashed: true })
  } else if (id === 'structure_pullback') {
    lines.push({ name: 'MA10', data: smaSeries(closes, 10), color: '#38bdf8' })
    lines.push({ name: 'MA20', data: smaSeries(closes, 20), color: '#c084fc' })
  } else if (id === 'ma60_shrink') {
    lines.push({ name: 'MA5', data: smaSeries(closes, 5), color: '#fbbf24' })
    lines.push({ name: 'MA20', data: smaSeries(closes, 20), color: '#c084fc' })
    lines.push({ name: 'MA60', data: smaSeries(closes, 60), color: '#38bdf8' })
  } else if (id === 'short_swing') {
    lines.push({ name: 'MA10', data: smaSeries(closes, 10), color: '#38bdf8' })
  } else if (id === 'vol_filter') {
    lines.push({ name: 'MA20', data: smaSeries(closes, 20), color: '#c084fc' })
  } else if (id === 'breakout') {
    const entryN = Number(p.entry_n) || 20
    const exitN = Number(p.exit_n) || 10
    lines.push({ name: `${entryN}日高`, data: rollingPrev(highs, entryN, 'max'), color: '#f87171', dashed: true })
    lines.push({ name: `${exitN}日低`, data: rollingPrev(lows, exitN, 'min'), color: '#4ade80', dashed: true })
  }
  return lines
}

function renderBacktestCharts() {
  chartHint.value = ''
  const r = backtestResult.value
  if (!equityChartEl.value || !r) return

  const bars: any[] = Array.isArray(r.bars) ? r.bars : []
  const eqDates: string[] = (r.equity_dates || []).map((d: any) => String(d).slice(0, 10))
  const cash0 = Number(r.cash) || 0
  let equityCash: number[] = Array.isArray(r.equity_cash) ? r.equity_cash.map((x: any) => Number(x)) : []
  if ((!equityCash.length || equityCash.every((x) => !Number.isFinite(x))) && Array.isArray(r.equity) && cash0 > 0) {
    equityCash = r.equity.map((x: any) => Number((cash0 * Number(x)).toFixed(2)))
  }

  const dates = bars.length
    ? bars.map((b) => String(b.date).slice(0, 10))
    : eqDates
  if (!dates.length) {
    chartHint.value = '本次回测没有可绘制的日期序列（缺少 bars / equity_dates）。'
    return
  }

  const ohlc = bars.length
    ? bars.map((b) => [Number(b.open), Number(b.close), Number(b.low), Number(b.high)])
    : []
  const closes = bars.map((b) => Number(b.close))
  const highs = bars.map((b) => Number(b.high))
  const lows = bars.map((b) => Number(b.low))
  const vols = bars.map((b) => Number(b.volume ?? b.vol ?? 0) || 0)
  const overlays = ohlc.length ? strategyOverlayLines(r, closes, highs, lows) : []

  const eqMap = new Map<string, number>()
  eqDates.forEach((d, i) => {
    if (equityCash[i] != null && Number.isFinite(equityCash[i])) eqMap.set(d, equityCash[i])
  })
  let lastEq = cash0 || (equityCash[0] ?? 0)
  const equityAligned = dates.map((d) => {
    if (eqMap.has(d)) lastEq = eqMap.get(d) as number
    return lastEq
  })

  const trades: any[] = Array.isArray(r.trades) ? r.trades : []
  const buyPts: any[] = []
  const sellPts: any[] = []
  for (const t of trades) {
    const d = String(t.date || '').slice(0, 10)
    const px = Number(t.price)
    if (!d || !Number.isFinite(px)) continue
    const size = Number(t.size) || 0
    const item = {
      value: [d, px],
      size,
      pnl: t.pnl,
      label: `${t.side === '买入' ? '买' : '卖'}${size || ''}`,
    }
    if (t.side === '买入') buyPts.push(item)
    else sellPts.push(item)
  }

  if (!ohlc.length && !equityAligned.length) {
    chartHint.value = '回测结果缺少 K 线与本金数据，无法绘图。'
    return
  }
  if (!ohlc.length) {
    chartHint.value = '未返回 K 线 bars，仅显示本金曲线。请重新跑一次回测以加载完整图表。'
  }

  if (equityChart) {
    equityChart.dispose()
    equityChart = null
  }
  equityChart = echarts.init(equityChartEl.value, 'dark')

  const hasK = ohlc.length > 0
  const extras: string[] = []
  if (hasK && chartExtras.vol) extras.push('vol')
  if (hasK && chartExtras.macd) extras.push('macd')
  if (hasK && chartExtras.rsi) extras.push('rsi')
  const panels = hasK ? ['k', ...extras, 'eq'] : ['eq']
  const weights: Record<string, number> = { k: 3.2, vol: 1, macd: 1.15, rsi: 1, eq: 1.35 }
  const totalW = panels.reduce((s, p) => s + (weights[p] || 1), 0)
  const top0 = 8
  const bottom = 7
  const gap = 1.4
  const usable = 100 - top0 - bottom - gap * Math.max(0, panels.length - 1)
  let y = top0
  const grids = panels.map((p) => {
    const h = usable * ((weights[p] || 1) / totalW)
    const g = { left: 56, right: 18, top: `${y}%`, height: `${h}%` }
    y += h + gap
    return g
  })
  const axisIdx = Object.fromEntries(panels.map((p, i) => [p, i])) as Record<string, number>
  const xIdxAll = panels.map((_, i) => i)
  const lastPanel = panels[panels.length - 1]

  const xAxis = panels.map((p, i) => ({
    type: 'category',
    data: dates,
    boundaryGap: true,
    gridIndex: i,
    axisLabel: {
      show: p === lastPanel,
      color: '#71717a',
      fontSize: 9,
    },
    axisTick: { show: p === lastPanel },
    axisLine: { lineStyle: { color: '#3f3f46' } },
    splitLine: { show: false },
  }))
  const yAxis = panels.map((p, i) => {
    const moneyFmt = (v: number) => (Math.abs(v) >= 10000 ? `${(v / 10000).toFixed(1)}万` : String(v))
    if (p === 'vol') {
      return {
        scale: true,
        gridIndex: i,
        axisLabel: { color: '#71717a', fontSize: 9, formatter: fmtAxisAmt },
        splitLine: { lineStyle: { color: '#27272a' } },
      }
    }
    if (p === 'rsi') {
      return {
        min: 0,
        max: 100,
        gridIndex: i,
        axisLabel: { color: '#71717a', fontSize: 9 },
        splitLine: { lineStyle: { color: '#27272a' } },
      }
    }
    if (p === 'eq') {
      return {
        scale: true,
        gridIndex: i,
        axisLabel: { color: '#71717a', fontSize: 9, formatter: moneyFmt },
        splitLine: { lineStyle: { color: '#27272a' } },
      }
    }
    return {
      scale: true,
      gridIndex: i,
      axisLabel: { color: '#71717a', fontSize: 9 },
      splitLine: { lineStyle: { color: '#27272a' } },
    }
  })

  const legendData = hasK
    ? ['K线', ...overlays.map((o) => o.name), '买入', '卖出', '本金']
    : ['本金']
  if (extras.includes('vol')) legendData.push('成交量')
  if (extras.includes('macd')) legendData.push('DIF', 'DEA', 'MACD')
  if (extras.includes('rsi')) legendData.push('RSI')

  const series: any[] = []
  if (hasK) {
    series.push({
      name: 'K线',
      type: 'candlestick',
      data: ohlc,
      xAxisIndex: axisIdx.k,
      yAxisIndex: axisIdx.k,
      itemStyle: {
        color: '#ef4444',
        color0: '#22c55e',
        borderColor: '#ef4444',
        borderColor0: '#22c55e',
      },
    })
    for (const ov of overlays) {
      series.push({
        name: ov.name,
        type: 'line',
        data: ov.data,
        xAxisIndex: axisIdx.k,
        yAxisIndex: axisIdx.k,
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.2, color: ov.color, type: ov.dashed ? 'dashed' : 'solid' },
        itemStyle: { color: ov.color },
        z: 5,
      })
    }
  }
  series.push(
    {
      name: '买入',
      type: 'scatter',
      data: buyPts,
      symbol: 'triangle',
      symbolSize: 14,
      itemStyle: { color: '#f43f5e' },
      label: {
        show: true,
        position: 'bottom',
        color: '#fda4af',
        fontSize: 9,
        formatter: (p: any) => p.data?.label || '买',
      },
      xAxisIndex: 0,
      yAxisIndex: 0,
      z: 10,
    },
    {
      name: '卖出',
      type: 'scatter',
      data: sellPts,
      symbol: 'triangle',
      symbolRotate: 180,
      symbolSize: 14,
      itemStyle: { color: '#4ade80' },
      label: {
        show: true,
        position: 'top',
        color: '#86efac',
        fontSize: 9,
        formatter: (p: any) => p.data?.label || '卖',
      },
      xAxisIndex: 0,
      yAxisIndex: 0,
      z: 10,
    },
    {
      name: '本金',
      type: 'line',
      data: equityAligned,
      showSymbol: false,
      xAxisIndex: axisIdx.eq ?? 0,
      yAxisIndex: axisIdx.eq ?? 0,
      lineStyle: { width: 1.8, color: '#a78bfa' },
      areaStyle: { color: 'rgba(167,139,250,0.12)' },
    },
  )

  if (extras.includes('vol')) {
    series.push({
      name: '成交量',
      type: 'bar',
      data: vols.map((v, i) => ({
        value: v,
        itemStyle: {
          color: Number(bars[i]?.close) >= Number(bars[i]?.open) ? '#ef4444' : '#22c55e',
        },
      })),
      xAxisIndex: axisIdx.vol,
      yAxisIndex: axisIdx.vol,
      barMaxWidth: 8,
    })
  }
  if (extras.includes('macd')) {
    const macd = macdSeries(closes)
    series.push(
      {
        name: 'MACD',
        type: 'bar',
        data: macd.hist.map((v) => ({
          value: v,
          itemStyle: { color: v >= 0 ? '#ef4444' : '#22c55e' },
        })),
        xAxisIndex: axisIdx.macd,
        yAxisIndex: axisIdx.macd,
        barMaxWidth: 6,
      },
      {
        name: 'DIF',
        type: 'line',
        data: macd.dif,
        showSymbol: false,
        xAxisIndex: axisIdx.macd,
        yAxisIndex: axisIdx.macd,
        lineStyle: { width: 1, color: '#38bdf8' },
      },
      {
        name: 'DEA',
        type: 'line',
        data: macd.dea,
        showSymbol: false,
        xAxisIndex: axisIdx.macd,
        yAxisIndex: axisIdx.macd,
        lineStyle: { width: 1, color: '#f59e0b' },
      },
    )
  }
  if (extras.includes('rsi')) {
    series.push({
      name: 'RSI',
      type: 'line',
      data: rsiSeries(closes, 14),
      showSymbol: false,
      xAxisIndex: axisIdx.rsi,
      yAxisIndex: axisIdx.rsi,
      lineStyle: { width: 1.2, color: '#fb7185' },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#52525b', type: 'dashed', width: 1 },
        data: [{ yAxis: 70 }, { yAxis: 30 }],
        label: { color: '#71717a', fontSize: 9 },
      },
    })
  }

  equityChart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    legend: {
      data: legendData,
      textStyle: { color: '#a1a1aa', fontSize: 10 },
      top: 2,
      type: 'scroll',
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter(params: any) {
        const arr = Array.isArray(params) ? params : [params]
        const axis = arr[0]?.axisValue || ''
        const lines = [`<div class="text-zinc-300">${axis}</div>`]
        for (const p of arr) {
          if (!p) continue
          if (p.seriesName === 'K线') {
            const v = p.data
            if (Array.isArray(v) && v.length >= 4) {
              lines.push(`开 ${v[0]} 收 ${v[1]} 低 ${v[2]} 高 ${v[3]}`)
            }
            continue
          }
          if (p.seriesName === '买入' || p.seriesName === '卖出') {
            const size = p.data?.size
            const pnl = p.data?.pnl
            lines.push(`${p.marker}${p.seriesName} ${p.data?.value?.[1] ?? p.value?.[1] ?? ''} · ${size || 0}股${pnl != null ? ` · 盈亏 ${pnl}` : ''}`)
            continue
          }
          if (p.seriesName === '本金') {
            lines.push(`${p.marker}本金 ${fmtMoney(p.data)}`)
            continue
          }
          if (p.seriesName === '成交量') {
            lines.push(`${p.marker}成交量 ${fmtAxisAmt(Number(p.data?.value ?? p.data ?? 0))}`)
            continue
          }
          const val = Array.isArray(p.data) ? p.data[1] : (p.data?.value ?? p.data)
          if (val != null && val !== '') lines.push(`${p.marker}${p.seriesName} ${val}`)
        }
        return lines.join('<br/>')
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: grids,
    xAxis,
    yAxis,
    dataZoom: [
      { type: 'inside', xAxisIndex: xIdxAll, start: 0, end: 100 },
      {
        type: 'slider',
        xAxisIndex: xIdxAll,
        bottom: 6,
        height: 18,
        borderColor: '#27272a',
        textStyle: { color: '#71717a', fontSize: 9 },
      },
    ],
    series,
  }, true)
  equityChart.resize()
}

watch(mainTab, async (t) => {
  if (t === 'backtest' && backtestResult.value) {
    await nextTick()
    requestAnimationFrame(() => renderBacktestCharts())
  }
})

watch(chartExtras, async () => {
  if (mainTab.value !== 'backtest' || !backtestResult.value) return
  await nextTick()
  requestAnimationFrame(() => renderBacktestCharts())
}, { deep: true })

function onResize() {
  equityChart?.resize()
}

onMounted(async () => {
  window.addEventListener('resize', onResize)
  pushLog('策略分析工作台已就绪 · 可切换模式 A / B', 'ok')
  await Promise.all([loadCatalog(), loadOptions()])
  if (toolboxMode.value !== 'screener') loadPerceive()
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
