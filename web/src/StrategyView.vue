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
              <span class="text-xs font-medium text-zinc-200">观察池 / 升势回踩</span>
              <span class="text-[9px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">选股</span>
            </div>
            <p class="text-[10px] text-zinc-500 leading-relaxed">A 观察池（缩量筑底转弯）/ B 升势回踩；缺数自动走 Datahubco 补日线</p>
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
            <p class="text-[10px] text-zinc-500 leading-relaxed">5 分钟先碰障碍的三分类。旁边列出当前有分钟线、能进训练集的股票和日期。</p>
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
          <template v-if="mainTab === 'screener'">
            <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 space-y-3">
              <div class="flex items-center justify-between gap-2">
                <h4 class="text-xs font-semibold text-zinc-300 flex items-center gap-2">
                  <i class="fa-solid fa-filter text-emerald-400"></i> 观察池 / 升势回踩 / 趋势回踩
                  <span class="text-[10px] text-zinc-500 font-normal">A 只观察；B 回踩 MA5；C 升势缩量回踩 MA10、次日开盘买。缺数自动从 Datahubco 补齐</span>
                </h4>
                <div class="flex items-center gap-1.5 flex-shrink-0">
                  <button
                    type="button"
                    @click="runPipeline"
                    :disabled="pipelineLoading || screenerLoading"
                    class="px-3 py-1.5 rounded-lg text-[11px] bg-rose-600/20 text-rose-300 border border-rose-500/30 hover:bg-rose-600/30 disabled:opacity-50"
                    title="A≥70→自选50→起飞推荐→衰竭出池"
                  >
                    <i class="fa-solid fa-rocket mr-1" :class="{ 'fa-spinner animate-spin': pipelineLoading }"></i>
                    {{ pipelineLoading ? '流水线…' : '跑推荐流水线' }}
                  </button>
                  <button
                    type="button"
                    @click="runScreener"
                    :disabled="screenerLoading"
                    class="px-3 py-1.5 rounded-lg text-[11px] bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30 disabled:opacity-50"
                  >
                    <i class="fa-solid fa-magnifying-glass mr-1" :class="{ 'fa-spinner animate-spin': screenerLoading }"></i>
                    {{ screenerLoading ? '搜索并入库中…' : '开始搜索' }}
                  </button>
                </div>
              </div>
              <div class="grid grid-cols-2 lg:grid-cols-4 gap-2">
                <div>
                  <label class="text-[10px] text-zinc-500 block mb-1">交易日</label>
                  <input
                    v-model="screener.asof"
                    type="date"
                    class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none"
                    title="默认跟随当天已收盘交易日；需要历史回看时再点此修改"
                    @change="screenerAsofPinned = true"
                  />
                </div>
                <div>
                  <label class="text-[10px] text-zinc-500 block mb-1">最多返回</label>
                  <input v-model.number="screener.limit" type="number" min="10" max="1000" class="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-xs outline-none" />
                </div>
              </div>
              <div class="flex flex-wrap gap-1.5">
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-amber-950/40 text-[10px] text-amber-300 cursor-pointer border border-amber-900/40">
                  <input v-model="screener.includeA" type="checkbox" class="rounded border-zinc-600" /> A 观察池
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-emerald-950/40 text-[10px] text-emerald-300 cursor-pointer border border-emerald-900/40">
                  <input v-model="screener.includeB" type="checkbox" class="rounded border-zinc-600" /> B 升势回踩
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-sky-950/40 text-[10px] text-sky-300 cursor-pointer border border-sky-900/40">
                  <input v-model="screener.includeC" type="checkbox" class="rounded border-zinc-600" /> C 趋势回踩
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                  <input v-model="screener.includeMain" type="checkbox" class="rounded border-zinc-600" /> 主板
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                  <input v-model="screener.includeStar" type="checkbox" class="rounded border-zinc-600" /> 科创板
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                  <input v-model="screener.includeChinext" type="checkbox" class="rounded border-zinc-600" /> 创业板
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                  <input v-model="screener.includeBse" type="checkbox" class="rounded border-zinc-600" /> 北交所
                </label>
                <label class="flex items-center gap-1 px-2 py-1 rounded-md bg-zinc-800/80 text-[10px] text-zinc-400 cursor-pointer">
                  <input v-model="screener.includeSt" type="checkbox" class="rounded border-zinc-600" /> 包含 ST
                </label>
              </div>
              <p class="text-[10px] text-zinc-600">B 按得分推荐；A 观察池只观察不买入；C=升势里缩量回踩 MA10，收盘出信号、次日开盘买，止损在近10日结构低点，风险≤4%，T+1 后才能卖，最多持 5 日。窗口约 40 日，缺数会先走 Datahubco 补全日线。点击一行到行情看板看 K 线。「跑推荐流水线」仍只走 A 观察→起飞，不含 C。</p>
              <p v-if="pipelineError" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{{ pipelineError }}</p>
              <div v-if="pipelineResult" class="text-[10px] text-rose-300/90 bg-rose-950/20 border border-rose-900/40 rounded-lg px-3 py-2 space-y-0.5">
                <div>
                  流水线 {{ pipelineResult.asof }}
                  · 观察 {{ pipelineResult.observe?.count ?? 0 }}
                  · 起飞入池 {{ pipelineResult.takeoff?.promoted_count ?? 0 }}
                  · 衰竭出池 {{ pipelineResult.exhaustion?.exhausted_count ?? 0 }}
                  · 推荐池活跃 {{ pipelineResult.recommend_active ?? 0 }}
                </div>
                <div v-if="pipelineResult.observe?.note" class="text-amber-300/90">
                  {{ pipelineResult.observe.note }}
                  <span v-if="pipelineResult.observe?.score_top5?.length" class="text-zinc-500">
                    · Top分 {{ pipelineResult.observe.score_top5.join(' / ') }}
                  </span>
                </div>
                <div v-if="(pipelineResult.takeoff?.promoted || []).length" class="text-zinc-400 truncate">
                  新晋：{{ (pipelineResult.takeoff.promoted || []).slice(0, 5).map((x: any) => x.name || x.ts_code).join('、') }}
                </div>
                <div v-if="(pipelineResult.exhaustion?.exhausted || []).length" class="text-zinc-500 truncate">
                  出池：{{ (pipelineResult.exhaustion.exhausted || []).slice(0, 5).map((x: any) => x.name || x.ts_code).join('、') }}
                </div>
              </div>
              <p v-if="screenerError" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{{ screenerError }}</p>
              <div v-if="screenerResult" class="text-[10px] text-zinc-400 space-y-0.5">
                <div>
                  交易日 {{ screenerResult.asof }} · 股票池 {{ screenerResult.universe }} · B {{ screenerResult.hit_count_b ?? 0 }} · A {{ screenerResult.hit_count_a ?? 0 }} · C {{ screenerResult.hit_count_c ?? 0 }}
                </div>
                <div v-if="screenerResult.fill_report" class="text-zinc-500">
                  Datahubco
                  · 补 {{ (screenerResult.fill_report.days_fetched || []).length }} 日
                  · 写入 {{ screenerResult.fill_report.rows ?? screenerResult.filled_rows ?? 0 }} 行
                  · 已齐跳过 {{ (screenerResult.fill_report.days_skipped || []).length }} 日
                  <span v-if="(screenerResult.fill_report.errors || []).length" class="text-amber-400">
                    · 失败 {{ screenerResult.fill_report.errors.length }} 日
                  </span>
                </div>
                <div v-else-if="screenerResult.filled_rows" class="text-zinc-500">入库 {{ screenerResult.filled_rows }} 行</div>
                <div v-if="screenerResult.note" class="text-zinc-600">{{ screenerResult.note }}</div>
              </div>
              <div v-if="screenerLoading" class="text-xs text-zinc-500 py-4 text-center">
                <i class="fa-solid fa-spinner animate-spin mr-1"></i>正在检查日线窗口，缺口从 Datahubco 自动补齐后分类 A / B / C…
              </div>
              <div v-if="hitsC.length" class="space-y-1.5">
                <div class="flex items-center gap-2 text-[11px] text-sky-300">
                  <span class="px-1.5 py-0.5 rounded bg-sky-500/15 border border-sky-500/20">C 趋势回踩</span>
                  <span class="text-zinc-500">升势缩量回踩 · {{ hitsC.length }} 只 · 按风险从小到大 · 次日开盘买</span>
                </div>
                <div class="border border-sky-900/40 rounded-xl overflow-hidden">
                  <table class="w-full text-[11px]">
                    <thead class="bg-zinc-800/80 text-zinc-500">
                      <tr>
                        <th class="text-left px-2 py-1.5 font-medium">代码</th>
                        <th class="text-left px-2 py-1.5 font-medium">名称</th>
                        <th class="text-left px-2 py-1.5 font-medium">板块</th>
                        <th class="text-right px-2 py-1.5 font-medium">收盘</th>
                        <th class="text-right px-2 py-1.5 font-medium">MA10</th>
                        <th class="text-right px-2 py-1.5 font-medium">止损</th>
                        <th class="text-right px-2 py-1.5 font-medium">风险%</th>
                        <th class="text-right px-2 py-1.5 font-medium">缩量</th>
                        <th class="text-right px-2 py-1.5 font-medium">得分</th>
                        <th class="text-left px-2 py-1.5 font-medium">原因</th>
                        <th class="px-2 py-1.5"></th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="h in hitsC"
                        :key="'C-'+h.ts_code"
                        class="border-t border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer"
                        :class="screenerActiveCode === h.ts_code ? 'bg-sky-500/10' : ''"
                        title="点击在行情看板查看"
                        @click="openHitInDash(h)"
                      >
                        <td class="px-2 py-1.5 font-mono text-zinc-300">{{ h.ts_code }}</td>
                        <td class="px-2 py-1.5 text-zinc-200">
                          {{ h.name }}
                          <span v-if="h.is_st" class="ml-1 text-[9px] text-red-400">ST</span>
                        </td>
                        <td class="px-2 py-1.5 text-zinc-500">{{ h.market }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums">{{ h.close }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-zinc-400">{{ h.ma10 ?? h.ma5 }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-sky-300">{{ h.signal_stop }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-sky-200">{{ h.risk_fraction_signal != null ? (Number(h.risk_fraction_signal) * 100).toFixed(2) : '-' }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-zinc-400">{{ h.vol_pullback }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-sky-300">{{ h.score }}</td>
                        <td class="px-2 py-1.5 text-zinc-500">{{ h.reason }}</td>
                        <td class="px-2 py-1.5 text-right">
                          <span class="text-[10px] text-sky-400">看行情</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div v-if="hitsB.length" class="space-y-1.5">
                <div class="flex items-center gap-2 text-[11px] text-emerald-300">
                  <span class="px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/20">B 升势回踩</span>
                  <span class="text-zinc-500">反转候选 · {{ hitsB.length }} 只 · 按得分排序</span>
                </div>
                <div class="border border-emerald-900/40 rounded-xl overflow-hidden">
                  <table class="w-full text-[11px]">
                    <thead class="bg-zinc-800/80 text-zinc-500">
                      <tr>
                        <th class="text-left px-2 py-1.5 font-medium">代码</th>
                        <th class="text-left px-2 py-1.5 font-medium">名称</th>
                        <th class="text-left px-2 py-1.5 font-medium">板块</th>
                        <th class="text-right px-2 py-1.5 font-medium">收盘</th>
                        <th class="text-right px-2 py-1.5 font-medium">MA5</th>
                        <th class="text-right px-2 py-1.5 font-medium">低于%</th>
                        <th class="text-right px-2 py-1.5 font-medium">涨跌%</th>
                        <th class="text-right px-2 py-1.5 font-medium">得分</th>
                        <th class="text-left px-2 py-1.5 font-medium">原因</th>
                        <th class="px-2 py-1.5"></th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="h in hitsB"
                        :key="'B-'+h.ts_code"
                        class="border-t border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer"
                        :class="screenerActiveCode === h.ts_code ? 'bg-emerald-500/10' : ''"
                        title="点击在行情看板查看"
                        @click="openHitInDash(h)"
                      >
                        <td class="px-2 py-1.5 font-mono text-zinc-300">{{ h.ts_code }}</td>
                        <td class="px-2 py-1.5 text-zinc-200">
                          {{ h.name }}
                          <span v-if="h.is_st" class="ml-1 text-[9px] text-red-400">ST</span>
                        </td>
                        <td class="px-2 py-1.5 text-zinc-500">{{ h.market }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums">{{ h.close }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-zinc-400">{{ h.ma5 }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-emerald-400">{{ h.below_pct }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums" :class="Number(h.pct_chg) >= 0 ? 'text-red-400' : 'text-emerald-400'">{{ h.pct_chg }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-emerald-300">{{ h.score }}</td>
                        <td class="px-2 py-1.5 text-zinc-500">{{ h.reason }}</td>
                        <td class="px-2 py-1.5 text-right">
                          <span class="text-[10px] text-sky-400">看行情</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div v-if="hitsA.length" class="space-y-1.5">
                <div class="flex items-center gap-2 text-[11px] text-amber-300">
                  <span class="px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-500/20">A 观察池</span>
                  <span class="text-zinc-500">缩量筑底转弯 · {{ hitsA.length }} 只 · 只观察不买入</span>
                </div>
                <div class="border border-amber-900/40 rounded-xl overflow-hidden">
                  <table class="w-full text-[11px]">
                    <thead class="bg-zinc-800/80 text-zinc-500">
                      <tr>
                        <th class="text-left px-2 py-1.5 font-medium">代码</th>
                        <th class="text-left px-2 py-1.5 font-medium">名称</th>
                        <th class="text-left px-2 py-1.5 font-medium">板块</th>
                        <th class="text-right px-2 py-1.5 font-medium">收盘</th>
                        <th class="text-right px-2 py-1.5 font-medium">MA5</th>
                        <th class="text-right px-2 py-1.5 font-medium">低于%</th>
                        <th class="text-right px-2 py-1.5 font-medium">涨跌%</th>
                        <th class="text-right px-2 py-1.5 font-medium">跌幅%</th>
                        <th class="text-right px-2 py-1.5 font-medium">观察期</th>
                        <th class="text-right px-2 py-1.5 font-medium">得分</th>
                        <th class="text-left px-2 py-1.5 font-medium">原因</th>
                        <th class="px-2 py-1.5"></th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="h in hitsA"
                        :key="'A-'+h.ts_code"
                        class="border-t border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer"
                        :class="screenerActiveCode === h.ts_code ? 'bg-amber-500/10' : ''"
                        title="点击在行情看板查看"
                        @click="openHitInDash(h)"
                      >
                        <td class="px-2 py-1.5 font-mono text-zinc-300">{{ h.ts_code }}</td>
                        <td class="px-2 py-1.5 text-zinc-200">
                          {{ h.name }}
                          <span v-if="h.is_st" class="ml-1 text-[9px] text-red-400">ST</span>
                        </td>
                        <td class="px-2 py-1.5 text-zinc-500">{{ h.market }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums">{{ h.close }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-zinc-400">{{ h.ma5 }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-amber-400">{{ h.below_pct }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums" :class="Number(h.pct_chg) >= 0 ? 'text-red-400' : 'text-emerald-400'">{{ h.pct_chg }}</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-zinc-300">{{ h.decline_days ?? '-' }}日</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-amber-200">{{ h.expect_up_days ?? '-' }}日</td>
                        <td class="px-2 py-1.5 text-right tabular-nums text-amber-300">{{ h.score }}</td>
                        <td class="px-2 py-1.5 text-zinc-500">{{ h.reason }}</td>
                        <td class="px-2 py-1.5 text-right">
                          <span class="text-[10px] text-sky-400">看行情</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div v-else-if="screenerResult && !screenerLoading && !hitsA.length && !hitsB.length && !hitsC.length" class="text-xs text-zinc-600 py-6 text-center">
                没有满足 A / B / C 条件的股票
              </div>
            </div>
          </template>

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
              </div>
              <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
                <div v-for="m in metricCards" :key="m.key" class="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2.5">
                  <div class="text-[10px] text-zinc-500 mb-0.5">{{ m.label }}</div>
                  <div :class="m.color" class="text-sm font-bold tabular-nums">{{ m.value }}</div>
                </div>
              </div>
              <p v-if="backtestResult.note" class="mt-2 text-[10px] text-amber-200/80 bg-amber-950/20 border border-amber-900/30 rounded-lg px-3 py-2">{{ backtestResult.note }}</p>
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
import { openStockInDash } from './appNav'
import IntradayTurningPanel from './IntradayTurningPanel.vue'

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
const screenerLoading = ref(false)
const screenerError = ref('')
const screenerResult = ref<any>(null)
const screenerActiveCode = ref('')
const pipelineLoading = ref(false)
const pipelineError = ref('')
const pipelineResult = ref<any>(null)
/** 用户手动改过日期后不再自动覆盖；刷新页面仍默认跟随当天 */
const screenerAsofPinned = ref(false)
const SCREENER_CACHE_KEY = 'strategy_screener_cache_v2'
const screener = reactive({
  asof: '',
  limit: 60,
  includeA: true,
  includeB: true,
  includeC: true,
  includeMain: true,
  includeStar: true,
  includeChinext: true,
  includeBse: false,
  includeSt: false,
})
const hitsB = computed(() => {
  const data = screenerResult.value
  if (!data) return []
  if (Array.isArray(data.hits_b)) return data.hits_b
  return (data.hits || []).filter((h: any) => h.setup_type === 'B')
})
const hitsA = computed(() => {
  const data = screenerResult.value
  if (!data) return []
  if (Array.isArray(data.hits_a)) return data.hits_a
  return (data.hits || []).filter((h: any) => h.setup_type === 'A')
})
const hitsC = computed(() => {
  const data = screenerResult.value
  if (!data) return []
  if (Array.isArray(data.hits_c)) return data.hits_c
  return (data.hits || []).filter((h: any) => h.setup_type === 'C')
})

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

function persistScreenerCache() {
  try {
    const form = { ...screener }
    // 交易日默认跟随当天，不把 asof 钉进缓存
    delete (form as any).asof
    sessionStorage.setItem(SCREENER_CACHE_KEY, JSON.stringify({
      form,
      result: screenerResult.value,
      active: screenerActiveCode.value,
    }))
  } catch { /* ignore */ }
}

function restoreScreenerCache() {
  try {
    const raw = sessionStorage.getItem(SCREENER_CACHE_KEY)
    if (!raw) return false
    const data = JSON.parse(raw)
    if (data.form) {
      const { asof: _ignored, ...rest } = data.form
      Object.assign(screener, rest)
    }
    if (data.result) screenerResult.value = data.result
    if (data.active) screenerActiveCode.value = data.active
    return !!data.result
  } catch {
    return false
  }
}

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
  if (!screenerAsofPinned.value) loadScreenerAsof()
}

function openIntraday() {
  toolboxMode.value = 'intraday'
  mainTab.value = 'intraday'
}

async function loadScreenerAsof() {
  try {
    const res = await fetch('/strategy/screener/asof')
    const data = await res.json().catch(() => ({}))
    if (data?.asof) screener.asof = String(data.asof).slice(0, 10)
  } catch (_) { /* 使用手动日期 */ }
}

async function runScreener() {
  screenerLoading.value = true
  screenerError.value = ''
  screenerResult.value = null
  try {
    const res = await fetch('/strategy/screener/setups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        asof: screener.asof || null,
        include_st: screener.includeSt,
        include_main: screener.includeMain,
        include_star: screener.includeStar,
        include_chinext: screener.includeChinext,
        include_bse: screener.includeBse,
        include_cdr: false,
        include_a: screener.includeA,
        include_b: screener.includeB,
        include_c: screener.includeC,
        limit: screener.limit || 60,
        fill: true,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data.detail
      screenerError.value = typeof detail === 'string' ? detail : (detail?.error || `HTTP ${res.status}`)
      pushLog(`[选股] 失败: ${screenerError.value}`, 'error')
      return
    }
    screenerResult.value = data
    if (data.asof) screener.asof = String(data.asof).slice(0, 10)
    persistScreenerCache()
    pushLog(
      `[选股] ${data.asof} · B${data.hit_count_b ?? 0} · A${data.hit_count_a ?? 0} · C${data.hit_count_c ?? 0} · Datahubco补${(data.fill_report?.days_fetched || []).length}日/写${data.fill_report?.rows ?? data.filled_rows ?? 0}行`,
      'ok',
    )
  } catch (e: any) {
    screenerError.value = e?.message || String(e)
    pushLog(`[选股] 异常: ${screenerError.value}`, 'error')
  } finally {
    screenerLoading.value = false
  }
}

async function runPipeline() {
  pipelineLoading.value = true
  pipelineError.value = ''
  pipelineResult.value = null
  try {
    const res = await fetch('/strategy/pipeline/cycle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        asof: screener.asof || null,
        min_score: 70,
        max_n: 50,
        fill: true,
        require_flow_signal: true,
      }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const detail = data.detail
      pipelineError.value = typeof detail === 'string' ? detail : (detail?.error || `HTTP ${res.status}`)
      pushLog(`[流水线] 失败: ${pipelineError.value}`, 'error')
      return
    }
    pipelineResult.value = data
    if (data.asof) screener.asof = String(data.asof).slice(0, 10)
    pushLog(
      `[流水线] 观察${data.observe?.count ?? 0} 起飞${data.takeoff?.promoted_count ?? 0} 衰竭${data.exhaustion?.exhausted_count ?? 0} 推荐池${data.recommend_active ?? 0}`,
      'ok',
    )
  } catch (e: any) {
    pipelineError.value = e?.message || String(e)
    pushLog(`[流水线] 异常: ${pipelineError.value}`, 'error')
  } finally {
    pipelineLoading.value = false
  }
}

function openHitInDash(h: any) {
  if (!h?.ts_code) return
  config.tsCode = h.ts_code
  screenerActiveCode.value = h.ts_code
  persistScreenerCache()
  toolboxMode.value = 'screener'
  mainTab.value = 'screener'
  openStockInDash({
    ts_code: h.ts_code,
    name: h.name,
    industry: h.industry,
  }, 'screener')
  pushLog(`[选股] 打开行情看板 ${h.name || ''} ${h.ts_code}`, 'ok')
}

function useScreenerHit(h: any) {
  openHitInDash(h)
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
      if (data.date_range?.end) config.end = data.date_range.end
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
        params: { ...paramValues },
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
    await nextTick(() => renderEquity())
    return true
  } catch (e) {
    pushLog('[回测] 失败: ' + (e && e.message ? e.message : String(e)), 'error')
    return false
  }
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

function renderEquity() {
  if (!equityChartEl.value || !backtestResult.value?.equity?.length) return
  if (!equityChart) equityChart = echarts.init(equityChartEl.value, 'dark')
  const data = backtestResult.value.equity
  const dates = backtestResult.value.equity_dates || data.map((_: any, i: number) => i + 1)
  const benchRet = backtestResult.value.benchmark_return ?? 0
  const bench = data.map((_: number, i: number) =>
    Number((1 + (benchRet * i) / Math.max(data.length - 1, 1)).toFixed(4))
  )
  equityChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 48, right: 16, top: 28, bottom: 28 },
    legend: { data: ['策略', '基准'], textStyle: { color: '#a1a1aa', fontSize: 10 }, top: 4 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#71717a', fontSize: 9 } },
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

onMounted(async () => {
  window.addEventListener('resize', onResize)
  pushLog('策略分析工作台已就绪 · 可切换模式 A / B', 'ok')
  const hadCache = restoreScreenerCache()
  if (hadCache) {
    toolboxMode.value = 'screener'
    mainTab.value = 'screener'
  }
  // 交易日始终跟随当天会话 asof；用户点日期选择器后才会钉住
  screenerAsofPinned.value = false
  await Promise.all([loadCatalog(), loadOptions(), loadScreenerAsof()])
  if (!hadCache) loadPerceive()
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
