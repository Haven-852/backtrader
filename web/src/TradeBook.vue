<template>
  <div class="p-6 max-w-3xl">
    <template v-if="!current">
      <h2 class="text-base font-medium mb-1">{{ mode === 'failures' ? '我的失败' : '我的交易' }}</h2>
      <p class="text-xs text-zinc-500 mb-4">{{ intro }}</p>

      <div v-if="mode === 'trades'" class="flex flex-wrap gap-2 mb-3">
        <button
          v-for="f in filters"
          :key="f.id"
          type="button"
          class="px-2.5 py-1 text-xs rounded-full border"
          :class="view === f.id ? 'bg-zinc-100 text-zinc-900 border-zinc-100' : 'border-zinc-700 text-zinc-400'"
          @click="setView(f.id)"
        >{{ f.label }}</button>
      </div>
      <div v-if="mode === 'trades'" class="flex flex-wrap gap-2 mb-4">
        <button
          v-for="f in quality"
          :key="f.id"
          type="button"
          class="px-2.5 py-1 text-xs rounded-full border"
          :class="view === f.id ? 'bg-sky-500/20 text-sky-100 border-sky-500/40' : 'border-zinc-800 text-zinc-500'"
          @click="setView(f.id)"
        >{{ f.label }}</button>
      </div>

      <div v-if="mode === 'trades'" class="grid grid-cols-4 gap-3 mb-4 text-sm">
        <div v-for="cell in statCells" :key="cell.label" class="border border-zinc-800 rounded-xl px-3 py-2">
          <div class="text-[11px] text-zinc-500">{{ cell.label }}</div>
          <div class="mt-1">{{ cell.value }}</div>
        </div>
      </div>
      <p v-if="mode === 'trades'" class="text-[11px] text-zinc-500 mb-4">胜率只是统计指标，不是评价。</p>

      <div v-if="mode === 'trades' || editingId" class="mb-4">
        <button v-if="mode === 'trades'" type="button" class="text-xs px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700" @click="togglePlan">
          {{ planning ? '收起' : '写下交易计划' }}
        </button>
        <form v-if="planning" class="mt-3 border border-zinc-800 rounded-xl p-4 space-y-3" @submit.prevent="createPlan">
          <div class="text-xs text-zinc-400">{{ editingId ? '修改已保存的交易。改完后列表里的数字会更新。' : '计划在买入前写。已经做完的交易，把下面的实际成交和结果一起补上。' }}</div>
          <div class="grid grid-cols-2 gap-3">
            <label class="text-xs text-zinc-400">股票<input v-model="plan.stock_name" required class="field" /></label>
            <label class="text-xs text-zinc-400">代码<input v-model="plan.symbol" class="field" /></label>
            <label class="text-xs text-zinc-400">计划日期<input v-model="plan.trade_date" type="date" required class="field" /></label>
            <label class="text-xs text-zinc-400">策略
              <select v-model="plan.strategy_name" required class="field">
                <option value="" disabled>选择策略</option>
                <option v-for="s in strategyOptions" :key="s.name" :value="s.name">{{ s.name }}</option>
              </select>
            </label>
            <label class="text-xs text-zinc-400">计划仓位 %<input v-model="plan.planned_position_pct" type="number" step="0.1" class="field" /></label>
            <label class="text-xs text-zinc-400">计划持仓
              <select v-model="plan.holding_mode" class="field">
                <option value="intraday">日内</option>
                <option value="days">按天</option>
              </select>
            </label>
            <label v-if="plan.holding_mode === 'days'" class="text-xs text-zinc-400">计划持仓天数<input v-model="plan.planned_holding_days" type="number" min="1" class="field" /></label>
            <label class="text-xs text-zinc-400">计划买入<input v-model="plan.planned_entry_price" type="number" step="0.01" class="field" /></label>
            <label v-if="activeStrategy.target" class="text-xs text-zinc-400">{{ activeStrategy.targetLabel }}
              <input v-model="plan.target_price" type="number" step="0.01" class="field" />
            </label>
            <label class="text-xs text-zinc-400">止损<input v-model="plan.stop_price" type="number" step="0.01" class="field" /></label>
            <label class="text-xs text-zinc-400">预期收益 %<input v-model="plan.expected_return" type="number" step="0.1" class="field" /></label>
            <label class="text-xs text-zinc-400">最大允许亏损/回撤 %<input v-model="plan.expected_risk" type="number" step="0.1" class="field" /></label>
          </div>
          <div class="text-xs text-zinc-500">{{ activeStrategy.hint }}</div>
          <div class="text-xs text-zinc-400">为什么做这笔交易？</div>
          <div class="flex flex-wrap gap-2">
            <label v-for="r in reasonOptions" :key="r" class="text-xs flex items-center gap-1">
              <input v-model="plan.reasons" type="checkbox" :value="r" /> {{ r }}
            </label>
          </div>
          <label class="block text-xs text-zinc-400">核心逻辑
            <textarea v-model="plan.thesis_text" required rows="3" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100"></textarea>
          </label>
          <div class="text-xs text-zinc-400">逻辑失效条件</div>
          <div class="flex flex-wrap gap-2">
            <label v-for="r in invalidOptions" :key="r" class="text-xs flex items-center gap-1">
              <input v-model="plan.invalidation" type="checkbox" :value="r" /> {{ r }}
            </label>
          </div>
          <div class="text-xs text-zinc-500 pt-1">已经完成的成交。还没买的可以留空。</div>
          <div class="grid grid-cols-2 gap-3">
            <label class="text-xs text-zinc-400">实际买入<input v-model="plan.executed_price" type="number" step="0.01" class="field" /></label>
            <label class="text-xs text-zinc-400">实际卖出<input v-model="plan.sell_price" type="number" step="0.01" class="field" /></label>
            <label class="text-xs text-zinc-400">实际仓位 %<input v-model="plan.actual_position_pct" type="number" step="0.1" class="field" /></label>
            <label class="text-xs text-zinc-400">实际收益 %<input v-model="plan.return_pct" type="number" step="0.01" class="field" /></label>
            <label class="text-xs text-zinc-400">最大浮盈 MFE %<input v-model="plan.max_favorable_excursion" type="number" step="0.01" class="field" /></label>
            <label class="text-xs text-zinc-400">最大浮亏 MAE %<input v-model="plan.max_adverse_excursion" type="number" step="0.01" class="field" /></label>
          </div>
          <div class="text-xs text-zinc-400">是否按计划执行</div>
          <div class="flex gap-4 text-xs">
            <label><input v-model="plan.followed_plan" type="radio" value="yes" /> 是</label>
            <label><input v-model="plan.followed_plan" type="radio" value="no" /> 否</label>
            <label><input v-model="plan.followed_plan" type="radio" value="" /> 还没成交</label>
          </div>
          <label v-if="plan.followed_plan === 'no'" class="block text-xs text-zinc-400">偏离原因
            <textarea v-model="plan.deviation_reason" rows="2" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100"></textarea>
          </label>
          <label class="block text-xs text-zinc-400">失败归因
            <select v-model="plan.failure_cause" class="field">
              <option value="">没有失败归因</option>
              <option v-for="c in failCauses" :key="c" :value="c">{{ c }}</option>
            </select>
          </label>
          <label class="block text-xs text-zinc-400">交易经验
            <textarea v-model="plan.lesson" rows="2" class="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-100"></textarea>
          </label>
          <div class="text-xs text-zinc-400">是否形成规则</div>
          <div class="flex flex-wrap gap-3 text-xs">
            <label><input v-model="plan.rule_intent" type="radio" value="none" /> 暂不形成</label>
            <label><input v-model="plan.rule_intent" type="radio" value="lesson" /> 只保存为经验</label>
            <label><input v-model="plan.rule_intent" type="radio" value="experiment" /> 放进实验室</label>
          </div>
          <p class="text-[11px] text-zinc-500">单次交易不会直接变成规则。实验室验证有效之后，才进入交易规则。</p>
          <div class="flex gap-2">
            <button type="submit" class="px-3 py-1.5 text-xs rounded-lg bg-sky-600 hover:bg-sky-500" :disabled="saving">{{ editingId ? '保存修改' : '保存' }}</button>
            <button v-if="editingId" type="button" class="px-3 py-1.5 text-xs rounded-lg bg-zinc-800" @click="cancelEdit">取消</button>
          </div>
        </form>
      </div>

      <div class="border border-zinc-800 rounded-xl overflow-hidden">
        <div class="grid grid-cols-[4.2rem_minmax(0,1fr)_4.8rem_3.4rem_4.2rem_3rem_auto] gap-2 px-3 py-2 text-[11px] text-zinc-500 border-b border-zinc-800">
          <span>日期</span><span>股票</span><span>策略</span><span>状态</span><span>收益</span><span>复盘</span><span></span>
        </div>
        <div
          v-for="trade in shown"
          :key="trade.id"
          class="grid grid-cols-[4.2rem_minmax(0,1fr)_4.8rem_3.4rem_4.2rem_3rem_auto] gap-2 px-3 py-2.5 text-left text-xs border-t border-zinc-800 hover:bg-zinc-900"
        >
          <button type="button" class="contents text-left" @click="openTrade(trade)">
            <span>{{ slash(trade.trade_date || trade.created_at) }}</span>
            <span class="truncate">{{ trade.name || trade.symbol || '未命名' }}</span>
            <span class="truncate">{{ trade.strategy_name || '—' }}</span>
            <span>{{ trade.list_state || '—' }}</span>
            <span :class="Number(trade.return_pct) < 0 ? 'text-red-400' : 'text-emerald-400'">{{ fmtPct(trade.return_pct) }}</span>
            <span>{{ trade.review_state || '—' }}</span>
          </button>
          <div class="flex items-center gap-2 justify-end">
            <button type="button" class="text-sky-300 hover:text-sky-200" @click="startEdit(trade)">修改</button>
            <button type="button" class="text-zinc-500 hover:text-red-300" @click="removeTrade(trade)">删除</button>
          </div>
        </div>
        <div v-if="!shown.length" class="px-3 py-6 text-sm text-zinc-500">这一栏还没有记录。</div>
      </div>
    </template>

    <template v-else>
      <div class="flex items-center justify-between mb-4">
        <button type="button" class="text-xs text-zinc-500 hover:text-zinc-200" @click="current = null">返回列表</button>
        <div v-if="!current.demo" class="flex gap-3">
          <button type="button" class="text-xs text-sky-300 hover:text-sky-200" @click="startEdit(current)">修改</button>
          <button type="button" class="text-xs text-red-300/80 hover:text-red-200" @click="removeTrade(current)">删除这笔交易</button>
        </div>
      </div>
      <div class="border border-zinc-800 rounded-xl overflow-hidden">
        <div class="px-4 py-3 border-b border-zinc-800 flex items-start justify-between">
          <div>
            <div class="text-base">{{ current.name || '未命名' }}</div>
            <div class="text-xs text-zinc-500 mt-1">计划日期 {{ slash(current.trade_date || current.created_at) }} · {{ current.code }}</div>
          </div>
          <div class="text-right">
            <div class="text-lg" :class="Number(current.return_pct) < 0 ? 'text-red-400' : 'text-emerald-400'">{{ fmtPct(current.return_pct) }}</div>
            <div class="text-xs text-zinc-400">{{ current.list_state }}</div>
          </div>
        </div>

        <div v-if="current.is_failure" class="px-4 py-3 border-b border-zinc-800 bg-red-500/5">
          <div class="text-sm">亏损不等于失败。这一笔进入失败视图，是因为逻辑错误或执行偏差。</div>
          <div class="text-xs text-zinc-400 mt-1">逻辑：{{ logicText(current.logic_result) }}</div>
        </div>

        <section class="px-4 py-3 border-b border-zinc-800 text-sm space-y-1">
          <div class="text-xs text-zinc-500 mb-2">① 交易计划</div>
          <div>策略：{{ current.strategy_name || '—' }}</div>
          <div>方向：{{ current.direction || '做多' }}</div>
          <div>计划仓位：{{ pct(current.planned_position_pct) }} · 计划持仓：{{ holdLabel(current.planned_holding_days) }}</div>
          <div>计划买入 {{ money(current.planned_entry_price) }}<template v-if="current.target_price != null"> · 目标 {{ money(current.target_price) }}</template> · 止损 {{ money(current.stop_price) }}</div>
          <div>预期收益 {{ fmtPct(current.expected_return) }} · 最大允许亏损/回撤 {{ fmtPct(current.expected_risk) }} · 风险收益比 {{ ratio(current.expected_return, current.expected_risk) }}</div>
          <div class="text-xs text-zinc-500 mt-2">为什么买</div>
          <div>{{ lines(current.entry_conditions).join('、') || current.entry_reason || '—' }}</div>
          <div class="whitespace-pre-wrap text-zinc-200">{{ current.thesis_text || '还没有核心逻辑。' }}</div>
          <div class="text-xs text-zinc-500 mt-2">失效条件</div>
          <div>{{ lines(current.invalidation_conditions).join(' / ') || '—' }}</div>
        </section>

        <section class="px-4 py-3 border-b border-zinc-800 text-sm">
          <div class="text-xs text-zinc-500 mb-2">② 实际执行</div>
          <template v-if="current.executed_price != null">
            <div>买入时间 {{ clock(current.executed_at) }}</div>
            <div>计划价格 {{ money(current.planned_price || current.planned_entry_price) }} · 实际成交 {{ money(current.executed_price) }}</div>
            <div>计划仓位 {{ pct(current.exec_planned_pos ?? current.planned_position_pct) }} · 实际仓位 {{ pct(current.actual_position_pct) }}</div>
            <div>数量 {{ current.executed_quantity ?? '—' }} · 手续费 {{ current.fee ?? '—' }} · 滑点 {{ slip(current.slippage) }}</div>
            <div>实际卖出 {{ money(current.sell_price) }} · 实际收益 {{ fmtPct(current.return_pct) }}</div>
            <div>最大浮盈 MFE {{ fmtPct(current.max_favorable_excursion) }} · 最大浮亏 MAE {{ fmtPct(current.max_adverse_excursion) }}</div>
            <div>是否按计划执行：{{ followedText(current.followed_plan) }}</div>
            <div v-if="current.deviation_reason">偏离原因：{{ current.deviation_reason }}</div>
            <div v-for="line in deviationLines" :key="line" class="text-amber-300 text-xs mt-1">⚠ {{ line }}</div>
          </template>
          <form v-else-if="!current.demo" class="grid grid-cols-2 gap-2" @submit.prevent="saveExecution">
            <p class="col-span-2 text-xs text-zinc-500">成交回报接入后自动写入。现在可以按实际成交补记，不必重写交易理由。</p>
            <input v-model="exec.executed_at" type="datetime-local" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="exec.executed_price" type="number" step="0.01" placeholder="实际买入" required class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="exec.actual_position_pct" type="number" step="0.1" placeholder="实际仓位 %" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <select v-model="exec.followed_plan" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs">
              <option value="">是否按计划执行</option>
              <option value="yes">按计划执行</option>
              <option value="no">没有按计划执行</option>
            </select>
            <input v-if="exec.followed_plan === 'no'" v-model="exec.deviation_reason" placeholder="偏离原因" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="exec.executed_quantity" type="number" placeholder="数量" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="exec.fee" type="number" step="0.01" placeholder="手续费" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <button class="px-3 py-1 text-xs rounded-lg bg-zinc-800" :disabled="saving">记录执行</button>
          </form>
          <div v-else class="text-xs text-zinc-500">版式示例没有真实成交。</div>
        </section>

        <section class="px-4 py-3 border-b border-zinc-800 text-sm space-y-1">
          <div class="text-xs text-zinc-500 mb-2">③ 交易结果</div>
          <template v-if="current.outcome_id">
            <div>买入 {{ money(current.buy_price) }} · 卖出 {{ money(current.sell_price) }}</div>
            <div>收益 {{ fmtPct(current.return_pct) }} · 持仓 {{ current.holding_days ?? '—' }} 天</div>
            <div>最大浮盈 {{ fmtPct(current.max_favorable_excursion) }} · 最大浮亏 {{ fmtPct(current.max_adverse_excursion) }}</div>
            <div>最终盈亏 {{ current.realized_pnl ?? '—' }}</div>
            <div class="mt-2">盈亏：{{ outcomeText(current.outcome_type) }}</div>
            <div>逻辑：{{ logicText(current.logic_result) }}</div>
            <p v-if="current.expected_return != null" class="text-xs text-zinc-400">预期 {{ fmtPct(current.expected_return) }}，实际 {{ fmtPct(current.return_pct) }}。两者分开看。</p>
          </template>
          <form v-else-if="!current.demo" class="grid grid-cols-2 gap-2" @submit.prevent="saveOutcome">
            <input v-model="outForm.sell_price" type="number" step="0.01" placeholder="实际卖出" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="outForm.return_pct" type="number" step="0.01" placeholder="实际收益 %" required class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="outForm.holding_days" type="number" placeholder="持仓天数" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="outForm.realized_pnl" type="number" step="0.01" placeholder="最终盈亏金额" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="outForm.max_favorable_excursion" type="number" step="0.01" placeholder="最大浮盈 MFE %" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <input v-model="outForm.max_adverse_excursion" type="number" step="0.01" placeholder="最大浮亏 MAE %" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs" />
            <div class="col-span-2 text-xs text-zinc-400">盈亏</div>
            <label v-for="o in outcomeOptions" :key="o.id" class="text-xs flex items-center gap-1"><input v-model="outForm.outcome_type" type="radio" :value="o.id" /> {{ o.label }}</label>
            <div class="col-span-2 text-xs text-zinc-400">逻辑结果。赚钱不代表做对，亏钱也不代表做错。</div>
            <label v-for="o in logicOptions" :key="o.id" class="text-xs flex items-center gap-1"><input v-model="outForm.logic_result" type="radio" :value="o.id" /> {{ o.label }}</label>
            <button class="col-span-2 px-3 py-1 text-xs rounded-lg bg-zinc-800 w-fit" :disabled="saving">记录结果</button>
          </form>
          <div v-else class="text-xs text-zinc-500">版式示例。</div>
        </section>

        <section class="px-4 py-3 border-b border-zinc-800 text-sm">
          <div class="text-xs text-zinc-500 mb-2">④ 交易归因</div>
          <div v-for="row in dimRows" :key="row.key" class="flex justify-between text-xs py-0.5">
            <span>{{ row.label }}</span><span>{{ mark(dims[row.key]) }}</span>
          </div>
          <div v-if="current.primary_category" class="mt-2 text-xs">
            主因 {{ current.primary_category }}<span v-if="current.secondary_category"> · 次因 {{ current.secondary_category }}</span>
            · {{ current.severity || '—' }}
          </div>
          <div v-if="current.mistake_note" class="text-xs mt-2 whitespace-pre-wrap">我真正犯的错误：{{ current.mistake_note }}</div>
          <div v-if="current.redo_choice" class="text-xs mt-1">如果重新来一次：{{ current.redo_choice }}</div>
          <form v-if="current.outcome_id && !current.demo" class="mt-3 space-y-2" @submit.prevent="saveAttr">
            <div class="text-xs text-zinc-500">主因</div>
            <div class="flex flex-wrap gap-2">
              <label v-for="c in causeOptions" :key="c" class="text-[11px] flex items-center gap-1"><input v-model="attr.primary_category" type="radio" :value="c" /> {{ c }}</label>
            </div>
            <div class="text-xs text-zinc-500">次因</div>
            <select v-model="attr.secondary_category" class="bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs">
              <option value="">不选</option>
              <option v-for="c in causeOptions" :key="c" :value="c">{{ c }}</option>
            </select>
            <div class="flex gap-3 text-xs">
              <label v-for="s in severities" :key="s"><input v-model="attr.severity" type="radio" :value="s" /> {{ s }}</label>
            </div>
            <div v-if="showRedo" class="space-y-1">
              <div class="text-xs text-zinc-500">如果重新来一次</div>
              <label v-for="r in redoOptions" :key="r" class="block text-xs"><input v-model="attr.redo_choice" type="radio" :value="r" /> {{ r }}</label>
              <textarea v-model="attr.mistake_note" rows="2" placeholder="我真正犯的错误" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-2 py-1 text-xs"></textarea>
            </div>
            <button class="px-3 py-1 text-xs rounded-lg bg-zinc-800" :disabled="saving">保存归因</button>
          </form>
          <div v-if="pattern.count" class="mt-3 border border-amber-500/30 rounded-lg px-3 py-2 text-xs">
            <div class="text-amber-200">历史重复：{{ pattern.cause }} 出现 {{ pattern.count }} 次</div>
            <div class="text-zinc-400 mt-1">盈利 {{ pattern.wins }} · 亏损 {{ pattern.losses }} · 平均 {{ fmtPct(pattern.avg_return) }} · 最大亏损 {{ fmtPct(pattern.max_loss) }}</div>
            <div v-if="pattern.codes?.length" class="mt-1 text-zinc-300">相关交易 {{ pattern.codes.join('、') }}</div>
          </div>
        </section>

        <section class="px-4 py-3 border-b border-zinc-800 text-sm">
          <div class="text-xs text-zinc-500 mb-2">⑤ AI 复盘</div>
          <p class="whitespace-pre-wrap text-zinc-200">{{ narrative || '还没有复盘。分析师只判断逻辑和执行，不因为赚钱就判对，也不因为亏钱就判错。' }}</p>
          <button v-if="!current.demo && current.outcome_id" type="button" class="mt-2 px-3 py-1 text-xs rounded-lg bg-sky-600 disabled:opacity-50" :disabled="reviewing" @click="runReview">
            {{ reviewing ? '复盘中…' : '请分析师复盘' }}
          </button>
        </section>

        <section class="px-4 py-3 border-b border-zinc-800 text-sm">
          <div class="text-xs text-zinc-500 mb-2">⑥ 我的经验</div>
          <textarea v-model="lessonText" rows="3" class="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm" :disabled="!!current.demo"></textarea>
          <button v-if="!current.demo" type="button" class="mt-2 px-3 py-1 text-xs rounded-lg bg-zinc-800" :disabled="saving" @click="saveLesson">保存为经验</button>
          <p class="text-[11px] text-zinc-500 mt-1">保存之后出现在「我的经验」。它还不是规则。</p>
        </section>

        <section class="px-4 py-3 text-sm">
          <div class="text-xs text-zinc-500 mb-2">⑦ 是否形成规则</div>
          <p class="text-xs text-zinc-400 mb-2">失败或成功都不会自动变成规则。路径是：经验 → 实验室验证 → 验证有效 → 规则。</p>
          <div class="flex gap-2">
            <button type="button" class="px-3 py-1 text-xs rounded-lg bg-zinc-800" @click="held = true">暂不形成</button>
            <button v-if="!current.demo" type="button" class="px-3 py-1 text-xs rounded-lg bg-emerald-800" :disabled="saving" @click="startExp">发起实验</button>
          </div>
          <p v-if="held" class="text-xs text-zinc-400 mt-2">这次先留在档案里。</p>
          <p v-if="expMsg" class="text-xs text-emerald-300 mt-2">{{ expMsg }}</p>
        </section>
      </div>
    </template>
    <p v-if="error" class="mt-3 text-sm text-red-300">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import axios from 'axios'

const props = defineProps<{ mode: string; query?: string }>()

const filters = [
  { id: 'all', label: '全部' },
  { id: 'open', label: '进行中' },
  { id: 'pending', label: '待复盘' },
  { id: 'win', label: '盈利' },
  { id: 'loss', label: '亏损' },
  { id: 'idle', label: '未执行' },
]
const quality = [
  { id: 'logic_ok', label: '逻辑正确' },
  { id: 'logic_bad', label: '逻辑错误' },
  { id: 'exec_ok', label: '执行优秀' },
  { id: 'exec_bad', label: '执行偏差' },
  { id: 'reuse', label: '值得复用' },
  { id: 'watch', label: '需要警惕' },
]
const reasonOptions = ['板块强势', '个股突破', '成交量放大', '市场环境支持', '事件驱动', '超跌反弹']
const strategyOptions = [
  { name: '强势板块突破', target: true, targetLabel: '突破目标价', hint: '目标价是突破后计划卖出的价格。' },
  { name: '趋势', target: true, targetLabel: '趋势目标价', hint: '目标价是趋势延续后计划离场的价格。' },
  { name: '轮动', target: true, targetLabel: '轮动目标价', hint: '目标价是切入新方向后的预期价格。' },
  { name: '超跌反弹', target: true, targetLabel: '反弹目标价', hint: '目标价是反弹计划到达的价格。' },
  { name: '事件驱动', target: true, targetLabel: '事件目标价', hint: '目标价是事件定价后计划离场的价格。' },
  { name: '日内', target: true, targetLabel: '日内目标价', hint: '日内交易当天平仓，目标价是当日计划卖出的价格。' },
  { name: '无固定目标', target: false, targetLabel: '', hint: '这笔不设价格目标，离场看失效条件。' },
]
const failCauses = ['市场环境错误', '板块判断错误', '个股判断错误', '入场太晚', '追高', '止损太晚', '仓位过大', '没有按照计划执行', '信息判断错误', '突发事件', '情绪影响', '其他']
function blankPlan() {
  return {
    reasons: [], invalidation: [], direction: '做多', trade_date: '', strategy_name: '',
    holding_mode: 'days', planned_holding_days: '', followed_plan: '', deviation_reason: '',
    failure_cause: '', lesson: '', rule_intent: 'none',
  }
}
const invalidOptions = ['跌破突破位', '板块强度明显下降', '龙头出现明显退潮', '成交量快速萎缩']
const outcomeOptions = [
  { id: 'WIN', label: '盈利' },
  { id: 'LOSS', label: '亏损' },
  { id: 'FLAT', label: '持平' },
]
const logicOptions = [
  { id: 'LOGIC_CORRECT', label: '逻辑正确' },
  { id: 'PARTIALLY_CORRECT', label: '逻辑部分正确' },
  { id: 'LOGIC_INCORRECT', label: '逻辑错误' },
  { id: 'UNDETERMINED', label: '无法判断' },
]
const causeOptions = [
  '策略正确', '选股正确', '入场时机正确', '市场环境正确', '板块判断正确', '执行正确',
  '市场环境错误', '板块判断错误', '个股判断错误', '入场太晚', '追高', '止损太晚',
  '仓位过大', '没有按照计划执行', '信息判断错误', '突发事件', '情绪影响', '其他',
]
const severities = ['轻微', '中等', '严重', '致命']
const redoOptions = ['不做这笔交易', '更早买入', '减少仓位', '等确认后再买', '设置更严格止损']
const dimRows = [
  { key: 'market', label: '市场判断' },
  { key: 'sector', label: '板块判断' },
  { key: 'stock', label: '个股判断' },
  { key: 'timing', label: '入场时机' },
  { key: 'execution', label: '执行' },
  { key: 'risk', label: '风控' },
]

const view = ref('all')
const trades = ref<any[]>([])
const stats = ref<any>({})
const current = ref<any>(null)
const planning = ref(false)
const saving = ref(false)
const reviewing = ref(false)
const error = ref('')
const held = ref(false)
const expMsg = ref('')
const lessonText = ref('')
const plan = ref<any>(blankPlan())
const editingId = ref('')
const activeStrategy = computed(() => strategyOptions.find((s) => s.name === plan.value.strategy_name) || {
  target: false, targetLabel: '目标价', hint: '先选择策略。选「无固定目标」时不填写目标价。',
})
const exec = ref<any>({})
const outForm = ref<any>({ outcome_type: 'WIN', logic_result: 'UNDETERMINED' })
const attr = ref<any>({ primary_category: '', secondary_category: '', severity: '中等', redo_choice: '', mistake_note: '' })

const intro = computed(() => {
  if (props.mode === 'failures') {
    return '这里不是另一套记录。逻辑错误，或严重偏差的交易会从我的交易里出现。不属于你的，在列表里删除，交易档案会一起去掉。'
  }
  return '一笔交易按计划、执行、结果、归因、经验走。不属于你的记录，点右侧删除。'
})

const shown = computed(() => {
  const key = (props.query || '').trim().toLowerCase()
  const rows = trades.value
  if (!key) return rows
  return rows.filter((t) => `${t.code || ''} ${t.name || ''} ${t.symbol || ''} ${t.strategy_name || ''}`.toLowerCase().includes(key))
})

const statCells = computed(() => {
  const s = stats.value || {}
  return [
    { label: '总交易', value: s.total ?? '—' },
    { label: '盈利', value: s.wins ?? '—' },
    { label: '亏损', value: s.losses ?? '—' },
    { label: '胜率', value: rate(s.win_rate) },
    { label: '平均盈利', value: fmtPct(s.avg_win) },
    { label: '平均亏损', value: fmtPct(s.avg_loss) },
    { label: '期望值', value: fmtPct(s.expectancy) },
  ]
})

const dims = computed(() => current.value?.dimensions || current.value?.evidence?.dimensions || {})
const narrative = computed(() => current.value?.evidence?.narrative || current.value?.evidence?.lesson || '')
const pattern = computed(() => current.value?.pattern || { count: 0, codes: [] })
const deviationLines = computed(() => current.value?.execution_deviation?.lines || [])
const showRedo = computed(() => current.value?.outcome_type === 'LOSS' || current.value?.logic_result === 'LOGIC_INCORRECT' || current.value?.is_failure)

function lines(v: unknown): string[] {
  return Array.isArray(v) ? v.map((x) => String(x)) : []
}
function slash(v: unknown) {
  return String(v || '').slice(0, 10).replace(/-/g, '/')
}
function fmtPct(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}
function pct(v: unknown) {
  if (v == null || v === '') return '—'
  return `${Number(v)}%`
}
function rate(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  return `${(Math.abs(n) <= 1 ? n * 100 : n).toFixed(1)}%`
}
function money(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  return Number.isNaN(n) ? '—' : n.toFixed(2)
}
function slip(v: unknown) {
  if (v == null || v === '') return '—'
  const n = Number(v) * 100
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}
function ratio(exp: unknown, risk: unknown) {
  const a = Number(exp)
  const b = Math.abs(Number(risk))
  if (!a || !b || Number.isNaN(a) || Number.isNaN(b)) return '—'
  return `${(a / b).toFixed(1)} : 1`
}
function clock(v: unknown) {
  const s = String(v || '')
  const m = s.match(/(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : (s ? s.replace('T', ' ').slice(0, 19) : '—')
}
function mark(v: string) {
  if (v === 'ok') return '✓'
  if (v === 'partial') return '⚠'
  if (v === 'bad') return '✗'
  return '—'
}
function outcomeText(v: string) {
  return outcomeOptions.find((o) => o.id === v)?.label || '—'
}
function logicText(v: string) {
  return logicOptions.find((o) => o.id === v)?.label || '—'
}
function holdLabel(v: unknown) {
  if (v === 0 || v === '0') return '日内'
  if (v == null || v === '') return '—'
  return `${v}天`
}
function followedText(v: unknown) {
  if (v === true || v === 'yes') return '是'
  if (v === false || v === 'no') return '否'
  return '—'
}

async function load() {
  error.value = ''
  const viewId = props.mode === 'failures' ? 'failure' : view.value
  try {
    const res = await axios.get('/kb/trades', { params: { view: viewId } })
    const rows = res.data?.trades || []
    stats.value = res.data?.stats || {}
    trades.value = rows
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '交易读取失败'
  }
}

async function setView(id: string) {
  view.value = id
  await load()
}

async function removeTrade(trade: any) {
  if (!trade || trade.demo) return
  const name = trade.name || trade.symbol || '这笔交易'
  if (!window.confirm(`删除「${name}」？计划、成交、结果，以及由它保存的经验会一起去掉。`)) return
  saving.value = true
  error.value = ''
  try {
    await axios.delete(`/kb/trades/${trade.id}`)
    if (current.value?.id === trade.id) current.value = null
    await load()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '删除失败'
  } finally {
    saving.value = false
  }
}

function togglePlan() {
  planning.value = !planning.value
  if (!planning.value) cancelEdit()
}

function cancelEdit() {
  editingId.value = ''
  planning.value = false
  plan.value = blankPlan()
}

async function startEdit(trade: any) {
  if (!trade || trade.demo) return
  error.value = ''
  const res = await axios.get(`/kb/trades/${trade.id}`)
  const t = res.data?.trade || trade
  const days = t.planned_holding_days
  current.value = null
  editingId.value = t.id
  planning.value = true
  plan.value = {
    ...blankPlan(),
    stock_name: t.name || t.stock_name || '',
    symbol: t.symbol || '',
    trade_date: String(t.trade_date || t.created_at || '').slice(0, 10),
    strategy_name: t.strategy_name || '',
    holding_mode: Number(days) === 0 ? 'intraday' : 'days',
    planned_holding_days: Number(days) === 0 ? '' : (days ?? ''),
    planned_position_pct: t.planned_position_pct ?? '',
    planned_entry_price: t.planned_entry_price ?? '',
    target_price: t.target_price ?? '',
    stop_price: t.stop_price ?? '',
    expected_return: t.expected_return ?? '',
    expected_risk: t.expected_risk ?? '',
    reasons: lines(t.entry_conditions).length ? lines(t.entry_conditions) : lines(String(t.entry_reason || '').split('、')),
    thesis_text: t.thesis_text || '',
    invalidation: lines(t.invalidation_conditions),
    executed_price: t.executed_price ?? '',
    sell_price: t.sell_price ?? '',
    actual_position_pct: t.actual_position_pct ?? '',
    return_pct: t.return_pct ?? '',
    max_favorable_excursion: t.max_favorable_excursion ?? '',
    max_adverse_excursion: t.max_adverse_excursion ?? '',
    followed_plan: t.followed_plan === true ? 'yes' : t.followed_plan === false ? 'no' : '',
    deviation_reason: t.deviation_reason || '',
    failure_cause: t.primary_category || '',
    lesson: t.lesson?.content || '',
    logic_result: t.logic_result || 'UNDETERMINED',
    outcome_type: t.outcome_type || '',
    realized_pnl: t.realized_pnl ?? '',
    rule_intent: 'none',
  }
}

async function openTrade(trade: any) {
  held.value = false
  expMsg.value = ''
  if (trade.demo) {
    current.value = trade
    lessonText.value = ''
    return
  }
  const res = await axios.get(`/kb/trades/${trade.id}`)
  current.value = res.data?.trade || trade
  lessonText.value = current.value?.lesson?.content || current.value?.evidence?.lesson || ''
}

async function createPlan() {
  saving.value = true
  error.value = ''
  try {
    const body = { ...plan.value }
    if (!activeStrategy.value.target) body.target_price = null
    if (body.holding_mode === 'intraday') body.planned_holding_days = 0
    const res = editingId.value
      ? await axios.put(`/kb/trades/${editingId.value}`, body)
      : await axios.post('/kb/trades', body)
    const edited = !!editingId.value
    planning.value = false
    editingId.value = ''
    plan.value = blankPlan()
    await load()
    if (!edited && res.data?.trade) await openTrade(res.data.trade)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '计划没有保存'
  } finally {
    saving.value = false
  }
}

async function saveExecution() {
  if (!current.value) return
  saving.value = true
  error.value = ''
  try {
    const res = await axios.post(`/kb/trades/${current.value.id}/execution`, {
      ...exec.value,
      executed_at: exec.value.executed_at ? new Date(exec.value.executed_at).toISOString() : null,
    })
    current.value = res.data?.trade || current.value
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '执行没有保存'
  } finally {
    saving.value = false
  }
}

async function saveOutcome() {
  if (!current.value) return
  saving.value = true
  error.value = ''
  try {
    const res = await axios.post(`/kb/trades/${current.value.id}/outcome`, outForm.value)
    current.value = res.data?.trade || current.value
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '结果没有保存'
  } finally {
    saving.value = false
  }
}

async function saveAttr() {
  if (!current.value) return
  saving.value = true
  error.value = ''
  try {
    const res = await axios.post(`/kb/trades/${current.value.id}/attribution`, attr.value)
    current.value = res.data?.trade || current.value
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '归因没有保存'
  } finally {
    saving.value = false
  }
}

async function runReview() {
  if (!current.value || current.value.demo) return
  reviewing.value = true
  error.value = ''
  try {
    const res = await axios.post(`/kb/trades/${current.value.id}/review`)
    if (!res.data?.ok) {
      error.value = res.data?.error || '复盘没有完成'
      return
    }
    current.value = res.data?.trade || current.value
    if (!lessonText.value) lessonText.value = current.value?.evidence?.lesson || ''
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '复盘失败'
  } finally {
    reviewing.value = false
  }
}

async function saveLesson() {
  if (!current.value) return
  saving.value = true
  error.value = ''
  try {
    const res = await axios.post(`/kb/trades/${current.value.id}/lesson`, { content: lessonText.value })
    current.value = res.data?.trade || current.value
    expMsg.value = res.data?.message || '已保存为经验'
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '经验没有保存'
  } finally {
    saving.value = false
  }
}

async function startExp() {
  if (!current.value) return
  saving.value = true
  error.value = ''
  try {
    const res = await axios.post(`/kb/trades/${current.value.id}/experiment`, {
      hypothesis: lessonText.value || `${current.value.primary_category || '这个做法'} 需要和另一种做法比较后再决定是否成为规则。`,
    })
    expMsg.value = res.data?.message || '已放进实验室'
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '实验没有创建'
  } finally {
    saving.value = false
  }
}

watch(() => plan.value.strategy_name, (name) => {
  if (name === '日内') plan.value.holding_mode = 'intraday'
  if (name === '无固定目标') plan.value.target_price = ''
})

watch(() => props.mode, () => {
  current.value = null
  view.value = 'all'
  load()
})

onMounted(load)
</script>

<style scoped>
.field {
  display: block;
  margin-top: 0.25rem;
  width: 100%;
  background: #09090b;
  border: 1px solid #27272a;
  border-radius: 0.5rem;
  padding: 0.375rem 0.75rem;
  font-size: 0.875rem;
  color: #f4f4f5;
}
</style>
