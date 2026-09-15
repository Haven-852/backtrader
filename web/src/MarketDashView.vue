<template>
  <div class="h-full flex flex-col bg-zinc-950 overflow-hidden">
    <!-- ===== 顶部指数栏 ===== -->
    <div class="flex-shrink-0 bg-zinc-900 border-b border-zinc-800">
      <div class="flex items-stretch overflow-x-auto scrollbar-thin">
        <div class="flex-shrink-0 flex items-center px-4 text-xs text-zinc-500 font-semibold border-r border-zinc-800 uppercase tracking-wider">
          行情中心
        </div>
        <div
          v-for="idx in indices"
          :key="idx.ts_code"
          class="flex-shrink-0 cursor-pointer px-4 py-2.5 min-w-[150px] border-r border-zinc-800 transition-colors hover:bg-zinc-800/50"
          :class="{ 'bg-zinc-800/60': selectedIndex === idx.ts_code && activeTab === 'kline' }"
          @click="selectIndexForKline(idx)"
        >
          <div class="text-xs text-zinc-400 mb-0.5">{{ idx.name }}</div>
          <div class="flex items-baseline gap-2">
            <span :class="idx.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold tabular-nums">
              {{ idx.close > 0 ? fmtNum(idx.close, 2) : '--' }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 mt-0.5">
            <span :class="idx.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'" class="text-xs tabular-nums">
              {{ idx.pct_chg >= 0 ? '+' : '' }}{{ idx.pct_chg.toFixed(2) }}%
            </span>
            <span :class="idx.pct_chg >= 0 ? 'text-red-400/70' : 'text-green-400/70'" class="text-xs tabular-nums">
              {{ idx.change >= 0 ? '+' : '' }}{{ idx.change.toFixed(2) }}
            </span>
          </div>
        </div>
        <div v-if="indicesLoading" class="flex-shrink-0 flex items-center px-4 text-xs text-zinc-500">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i>加载指数...
        </div>
      </div>
    </div>

    <!-- ===== 标签导航 ===== -->
    <div class="flex-shrink-0 bg-zinc-900/70 border-b border-zinc-800 px-4">
      <div class="flex items-center gap-1 overflow-x-auto scrollbar-thin py-2">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="activeTab = tab.id"
          :class="[
            'flex-shrink-0 px-4 py-2 rounded-lg text-sm font-medium transition-all',
            activeTab === tab.id
              ? 'bg-blue-600/20 text-blue-400 border border-blue-500/30'
              : 'text-zinc-400 hover:text-white hover:bg-zinc-800 border border-transparent'
          ]"
        >
          {{ tab.icon }} {{ tab.label }}
        </button>
        <div class="flex-1"></div>
        <!-- 数据源指示 -->
        <div class="text-xs text-zinc-600 flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse"></span>
          PostgreSQL
        </div>
      </div>
    </div>

    <!-- ===== Tab Content ===== -->
    <div class="flex-1 overflow-auto">

      <!-- ──────── 📈 K线行情 Tab ──────── -->
      <div v-if="activeTab === 'kline'" class="flex gap-4 p-4 h-full min-h-0">
        <!-- 左侧：K线图区 -->
        <div class="flex-1 flex flex-col min-w-0 min-h-0">
          <!-- 工具栏 -->
          <div class="flex items-center gap-3 mb-3 flex-shrink-0">
            <!-- 股票搜索 -->
            <div class="relative">
              <input
                v-model="stockSearch"
                @input="onStockSearch"
                @focus="showSearchDropdown = true"
                @blur="showSearchDropdown = false"
                placeholder="搜索股票代码/名称..."
                class="w-56 bg-zinc-800 border border-zinc-700 rounded-lg pl-8 pr-3 py-2 text-sm text-white outline-none focus:border-blue-500 transition-colors"
              />
              <i class="fa-solid fa-search absolute left-2.5 top-3 text-zinc-500 text-xs"></i>
              <!-- 搜索结果下拉 -->
              <div v-if="showSearchDropdown && searchResults.length" class="absolute top-full left-0 mt-1 w-72 bg-zinc-800 border border-zinc-700 rounded-xl shadow-xl z-50 max-h-60 overflow-auto">
                <div
                  v-for="s in searchResults"
                  :key="s.ts_code"
                  @mousedown.prevent="selectStock(s)"
                  class="px-3 py-2 hover:bg-zinc-700 cursor-pointer flex items-center justify-between"
                >
                  <div>
                    <span class="text-sm text-white font-medium">{{ s.name }}</span>
                    <span class="text-xs text-zinc-500 ml-2">{{ s.ts_code }}</span>
                  </div>
                  <span class="text-xs text-zinc-600">{{ s.industry }}</span>
                </div>
              </div>
            </div>

            <!-- 当前股票信息 -->
            <div v-if="currentStock" class="flex items-center gap-2">
              <span class="text-white font-semibold">{{ currentStock.name || currentStock.split('.')[0] }}</span>
              <span class="text-xs text-zinc-500 font-mono">{{ currentStock }}</span>
            </div>

            <!-- 周期切换 -->
            <div class="flex bg-zinc-800 rounded-lg p-0.5 ml-4">
              <button
                v-for="tf in timeframes"
                :key="tf.id"
                @click="currentTimeframe = tf.id; loadKline()"
                :class="[
                  'px-3 py-1.5 text-xs rounded-md transition-all',
                  currentTimeframe === tf.id ? 'bg-blue-600 text-white' : 'text-zinc-400 hover:text-white'
                ]"
              >{{ tf.label }}</button>
            </div>

            <!-- K线条数 -->
            <div class="flex items-center gap-1.5 ml-2">
              <span class="text-xs text-zinc-500">K线数</span>
              <select v-model.number="klineLimit" @change="loadKline()" class="bg-zinc-800 border border-zinc-700 rounded text-xs text-white px-2 py-1.5 outline-none">
                <option :value="60">60</option>
                <option :value="120">120</option>
                <option :value="250">250</option>
                <option :value="500">500</option>
              </select>
            </div>

            <button @click="loadKline()" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-xs text-white transition-colors">
              <i class="fa-solid fa-rotate mr-1" :class="{ 'animate-spin': klineLoading }"></i>刷新
            </button>
          </div>

          <!-- 指标切换 -->
          <div class="flex items-center gap-2 mb-3 flex-shrink-0 flex-wrap">
            <span class="text-xs text-zinc-500 mr-1">指标:</span>
            <button
              v-for="ind in ['ma','ema','macd','rsi','boll','kdj']"
              :key="ind"
              @click="toggleIndicator(ind)"
              :class="[
                'px-2.5 py-1 text-xs rounded-md border transition-all',
                indicatorsOn.includes(ind)
                  ? 'border-amber-500/50 bg-amber-500/10 text-amber-400'
                  : 'border-zinc-700 text-zinc-500 hover:text-zinc-300'
              ]"
            >{{ ind.toUpperCase() }}</button>
          </div>

          <!-- K线图 -->
          <div class="flex-1 min-h-0 relative" ref="klineChartWrap">
            <div v-if="klineLoading" class="absolute inset-0 flex items-center justify-center bg-zinc-950/80 z-10">
              <div class="flex items-center gap-2 text-zinc-400">
                <i class="fa-solid fa-spinner animate-spin"></i>加载K线数据...
              </div>
            </div>
            <div v-if="klineError" class="absolute inset-0 flex items-center justify-center z-10">
              <div class="text-center text-zinc-500">
                <i class="fa-solid fa-triangle-exclamation text-2xl mb-2 block text-amber-500"></i>
                <p class="text-sm">{{ klineError }}</p>
                <p class="text-xs mt-1 text-zinc-600">请检查股票代码是否正确，或数据是否已导入</p>
              </div>
            </div>
            <div ref="klineChart" class="w-full h-full"></div>
          </div>
        </div>

        <!-- 右侧：OHLC面板 -->
        <div v-if="klineStats" class="w-64 flex-shrink-0 bg-zinc-900 border border-zinc-800 rounded-2xl p-4 flex flex-col gap-3">
          <h3 class="text-sm font-semibold text-white mb-1">📊 行情统计</h3>
          <div class="grid grid-cols-2 gap-3">
            <div class="bg-zinc-800/50 rounded-xl p-3">
              <div class="text-xs text-zinc-500 mb-1">起始</div>
              <div class="text-sm text-white font-mono tabular-nums">{{ klineStats.period_start }}</div>
            </div>
            <div class="bg-zinc-800/50 rounded-xl p-3">
              <div class="text-xs text-zinc-500 mb-1">截止</div>
              <div class="text-sm text-white font-mono tabular-nums">{{ klineStats.period_end }}</div>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="bg-zinc-800/50 rounded-xl p-3">
              <div class="text-xs text-zinc-500 mb-1">期初价</div>
              <div class="text-sm text-white font-mono">{{ fmtNum(klineStats.first_close) }}</div>
            </div>
            <div class="bg-zinc-800/50 rounded-xl p-3">
              <div class="text-xs text-zinc-500 mb-1">最新价</div>
              <div class="text-sm text-white font-mono">{{ fmtNum(klineStats.last_close) }}</div>
            </div>
          </div>
          <div class="bg-zinc-800/50 rounded-xl p-3">
            <div class="text-xs text-zinc-500 mb-1">区间涨跌幅</div>
            <div :class="(klineStats.change_pct || 0) >= 0 ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold">
              {{ (klineStats.change_pct || 0) >= 0 ? '+' : '' }}{{ (klineStats.change_pct || 0).toFixed(2) }}%
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="bg-zinc-800/50 rounded-xl p-3">
              <div class="text-xs text-zinc-500 mb-1">最高</div>
              <div class="text-sm text-red-400 font-mono">{{ fmtNum(klineStats.high_max) }}</div>
            </div>
            <div class="bg-zinc-800/50 rounded-xl p-3">
              <div class="text-xs text-zinc-500 mb-1">最低</div>
              <div class="text-sm text-green-400 font-mono">{{ fmtNum(klineStats.low_min) }}</div>
            </div>
          </div>
          <div class="bg-zinc-800/50 rounded-xl p-3">
            <div class="text-xs text-zinc-500 mb-1">K线总数</div>
            <div class="text-sm text-white font-mono">{{ klineStats.bar_count }} 根</div>
          </div>
          <div class="bg-zinc-800/50 rounded-xl p-3">
            <div class="text-xs text-zinc-500 mb-1">总成交量</div>
            <div class="text-sm text-white font-mono">{{ fmtVol(klineStats.vol_total) }}</div>
          </div>
        </div>
      </div>

      <!-- ──────── 📊 基础参照 Tab ──────── -->
      <div v-if="activeTab === 'reference'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">📊 基础参照数据</h3>

        <!-- 表选择 -->
        <div class="flex gap-2 mb-4 flex-wrap">
          <button
            v-for="t in ['ref_stock_basic', 'ref_trade_cal', 'ref_stock_namechange', 'ref_new_share', 'ref_stk_limit_daily', 'ref_suspend_d']"
            :key="t"
            @click="selectedRefTable = t; loadRefTable(t)"
            :class="[
              'px-3 py-1.5 rounded-lg text-sm transition-all',
              selectedRefTable === t ? 'bg-blue-600/20 text-blue-400 border border-blue-500/30' : 'bg-zinc-900 text-zinc-400 hover:text-white border border-zinc-800'
            ]"
          >{{ tableLabelMap[t] || t }}</button>
        </div>

        <!-- 搜索框（仅 stock_basic） -->
        <div v-if="selectedRefTable === 'ref_stock_basic'" class="flex items-center gap-3 mb-3">
          <div class="relative">
            <input
              v-model="refSearchKey"
              placeholder="搜索股票..."
              class="w-56 bg-zinc-800 border border-zinc-700 rounded-lg pl-8 pr-3 py-1.5 text-sm text-white outline-none focus:border-blue-500"
              @keyup.enter="loadRefTable(selectedRefTable)"
            />
            <i class="fa-solid fa-search absolute left-2.5 top-2 text-zinc-500 text-xs"></i>
          </div>
          <button @click="loadRefTable(selectedRefTable)" class="px-3 py-1.5 bg-blue-600 rounded-lg text-xs text-white">查询</button>
        </div>

        <div v-if="refTableLoading" class="text-center py-8 text-zinc-500"><i class="fa-solid fa-spinner animate-spin mr-2"></i>加载中...</div>
        <div v-else-if="refTableData.length" class="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
          <div class="px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
            <span class="text-xs text-zinc-400">共 {{ refTableTotal }} 行 · 显示前 {{ refTableData.length }} 行</span>
            <span class="text-xs text-zinc-600 font-mono">{{ selectedRefTable }}</span>
          </div>
          <div class="overflow-auto max-h-[60vh]">
            <table class="w-full text-xs">
              <thead class="sticky top-0 bg-zinc-900">
                <tr class="border-b border-zinc-700">
                  <th class="text-left py-2 px-3 text-zinc-400 font-medium" v-for="col in refTableCols" :key="col">{{ col }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in refTableData" :key="ri" class="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                  <td v-for="col in refTableCols" :key="col" class="py-1.5 px-3 text-zinc-300 truncate max-w-[200px]">
                    {{ formatCell(row[col]) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div v-else-if="!refTableLoading && selectedRefTable" class="text-center py-8 text-zinc-500 text-sm">暂无数据</div>
      </div>

      <!-- ──────── 💰 财务报表 Tab ──────── -->
      <div v-if="activeTab === 'financials'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">💰 财务报表</h3>
        <div class="flex items-center gap-3 mb-4">
          <input
            v-model="finTsCode"
            placeholder="输入股票代码，如 000001.SZ"
            class="w-48 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white outline-none focus:border-blue-500"
          />
          <button @click="loadFinancials()" :disabled="!finTsCode" class="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-40 rounded-lg text-sm text-white">
            查询财务报表
          </button>
        </div>

        <div v-if="finLoading" class="text-center py-8 text-zinc-500"><i class="fa-solid fa-spinner animate-spin mr-2"></i>加载中...</div>

        <div v-else-if="finData" class="space-y-4">
          <!-- 报表选择 -->
          <div class="flex gap-2 flex-wrap">
            <button
              v-for="r in finReports"
              :key="r.id"
              @click="selectedFinReport = r.id"
              :class="[
                'px-3 py-1.5 rounded-lg text-sm transition-all',
                selectedFinReport === r.id ? 'bg-emerald-600/20 text-emerald-400 border border-emerald-500/30' : 'bg-zinc-900 text-zinc-400 hover:text-white border border-zinc-800'
              ]"
            >{{ r.label }} ({{ finData.reports[r.id]?.count || 0 }})</button>
          </div>

          <!-- 报表数据 -->
          <div v-if="selectedFinReport && finData.reports[selectedFinReport]?.data?.length" class="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
            <div class="overflow-auto max-h-[60vh]">
              <table class="w-full text-xs">
                <thead class="sticky top-0 bg-zinc-900">
                  <tr class="border-b border-zinc-700">
                    <th class="text-left py-2 px-3 text-zinc-400 font-medium" v-for="col in getReportCols(selectedFinReport)" :key="col">{{ col }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in finData.reports[selectedFinReport].data" :key="ri" class="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                    <td v-for="col in getReportCols(selectedFinReport)" :key="col" class="py-1.5 px-3 text-zinc-300 truncate max-w-[220px]">
                      {{ formatCell(row[col]) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-else-if="selectedFinReport && !finLoading" class="text-center py-8 text-zinc-500 text-sm">该报表暂无数据</div>
        </div>
      </div>

      <!-- ──────── 📋 盘前&复权 Tab ──────── -->
      <div v-if="activeTab === 'premarket'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">📋 盘前&复权&竞价</h3>
        <div class="flex gap-2 mb-4 flex-wrap">
          <button
            v-for="t in ['stock_daily_basic', 'stock_adj_factor', 'stock_auction_daily']"
            :key="t"
            @click="selectedPremTable = t; loadGenericTable(t)"
            :class="[
              'px-3 py-1.5 rounded-lg text-sm transition-all',
              selectedPremTable === t ? 'bg-violet-600/20 text-violet-400 border border-violet-500/30' : 'bg-zinc-900 text-zinc-400 hover:text-white border border-zinc-800'
            ]"
          >{{ tableLabelMap[t] || t }}</button>
        </div>
        <!-- 加载按钮和表格 -->
        <div v-if="genTableData === selectedPremTable && genDataLoading" class="text-center py-4 text-zinc-500"><i class="fa-solid fa-spinner animate-spin mr-2"></i>加载中...</div>
        <div v-else-if="genTableData === selectedPremTable && genDataRows.length" class="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden mt-3">
          <div class="overflow-auto max-h-[60vh]">
            <table class="w-full text-xs">
              <thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700"><th v-for="c in genDataCols" :key="c" class="text-left py-2 px-3 text-zinc-400 font-medium">{{c}}</th></tr></thead>
              <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50 hover:bg-zinc-800/30"><td v-for="c in genDataCols" :key="c" class="py-1.5 px-3 text-zinc-300 truncate max-w-[200px]">{{formatCell(r[c])}}</td></tr></tbody>
            </table>
          </div>
          <div class="px-4 py-2 border-t border-zinc-800 text-xs text-zinc-500">共 {{ genDataTotal }} 行</div>
        </div>
        <div v-else-if="genTableData === selectedPremTable && !genDataLoading" class="text-center py-8 text-zinc-500 text-sm">暂无数据</div>
        <div v-else class="text-center py-8 text-zinc-500 text-sm">点击上方表名加载数据</div>
      </div>

      <!-- ──────── 💸 资金&两融 Tab ──────── -->
      <div v-if="activeTab === 'money'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">💸 资金&两融&大宗&龙虎</h3>
        <div class="grid grid-cols-2 gap-4">
          <!-- 资金流向 -->
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
            <h4 class="text-sm font-semibold text-white mb-3">资金流向</h4>
            <div class="flex items-center gap-2 mb-3">
              <input v-model="mfTsCode" placeholder="股票代码" class="w-36 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-white outline-none focus:border-blue-500" />
              <button @click="loadMoneyflow()" :disabled="!mfTsCode" class="px-3 py-1.5 bg-blue-600 rounded text-xs text-white">查询</button>
            </div>
            <div v-if="mfLoading" class="text-zinc-500 text-xs"><i class="fa-solid fa-spinner animate-spin mr-1"></i>加载中...</div>
            <div v-else-if="mfData" class="mt-2">
              <div ref="mfChart" class="w-full h-64"></div>
              <div class="text-xs text-zinc-500 mt-1">共 {{ mfData.count }} 条</div>
            </div>
          </div>

          <!-- 融资融券 -->
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
            <h4 class="text-sm font-semibold text-white mb-3">融资融券汇总</h4>
            <button @click="loadMargin()" class="px-3 py-1.5 bg-blue-600 rounded text-xs text-white mb-3">刷新</button>
            <div v-if="marginLoading" class="text-zinc-500 text-xs"><i class="fa-solid fa-spinner animate-spin mr-1"></i>加载中...</div>
            <div v-else-if="marginData" class="mt-2">
              <div ref="marginChart" class="w-full h-64"></div>
              <div class="text-xs text-zinc-500 mt-1">共 {{ marginData.count }} 条</div>
            </div>
          </div>
        </div>

        <!-- 龙虎榜 / 大宗交易 -->
        <div class="grid grid-cols-2 gap-4 mt-4">
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
            <h4 class="text-sm font-semibold text-white mb-3">龙虎榜</h4>
            <button @click="selectedMoneyTable='stock_top_list_daily'; loadGenericTableData('stock_top_list_daily')" class="px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-400 hover:text-white mb-3">加载数据</button>
            <div class="overflow-auto max-h-48 text-xs" v-if="genTableData === 'stock_top_list_daily' && genDataRows.length">
              <table class="w-full"><thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700"><th v-for="c in genDataCols" :key="c" class="text-left py-1.5 px-2 text-zinc-400">{{c}}</th></tr></thead>
                <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50"><td v-for="c in genDataCols" :key="c" class="py-1 px-2 text-zinc-300 truncate max-w-[150px]">{{formatCell(r[c])}}</td></tr></tbody></table>
            </div>
          </div>
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
            <h4 class="text-sm font-semibold text-white mb-3">大宗交易</h4>
            <button @click="selectedMoneyTable='stock_block_trade'; loadGenericTableData('stock_block_trade')" class="px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-400 hover:text-white mb-3">加载数据</button>
            <div class="overflow-auto max-h-48 text-xs" v-if="genTableData === 'stock_block_trade' && genDataRows.length">
              <table class="w-full"><thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700"><th v-for="c in genDataCols" :key="c" class="text-left py-1.5 px-2 text-zinc-400">{{c}}</th></tr></thead>
                <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50"><td v-for="c in genDataCols" :key="c" class="py-1 px-2 text-zinc-300 truncate max-w-[150px]">{{formatCell(r[c])}}</td></tr></tbody></table>
            </div>
          </div>
        </div>
      </div>

      <!-- ──────── 📉 指数&权重 Tab ──────── -->
      <div v-if="activeTab === 'indices'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">📉 指数&权重</h3>
        <div class="grid grid-cols-2 gap-4">
          <!-- 指数日线 -->
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 col-span-2">
            <h4 class="text-sm font-semibold text-white mb-3">核心指数走势</h4>
            <div class="flex items-center gap-2 mb-3 flex-wrap">
              <button v-for="idx in indexList" :key="idx.code"
                @click="selectedIndexKline = idx.code; loadIndexKline(idx.code)"
                :class="['px-3 py-1 rounded-lg text-xs transition-all',
                  selectedIndexKline === idx.code ? 'bg-amber-600/20 text-amber-400 border border-amber-500/30' : 'bg-zinc-800 text-zinc-400 hover:text-white']"
              >{{ idx.name }}</button>
            </div>
            <div v-if="idxKlineLoading" class="text-zinc-500 text-xs"><i class="fa-solid fa-spinner animate-spin mr-1"></i>加载中...</div>
            <div v-else ref="indexKlineChart" class="w-full h-80"></div>
          </div>

          <!-- 指数权重 -->
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 col-span-2">
            <h4 class="text-sm font-semibold text-white mb-3">指数成分权重</h4>
            <button @click="loadGenericTableData('index_weight')" class="px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-400 hover:text-white mb-3">加载数据</button>
            <div class="overflow-auto max-h-60 text-xs" v-if="genTableData === 'index_weight' && genDataRows.length">
              <table class="w-full"><thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700">
                <th v-for="c in genDataCols" :key="c" class="text-left py-1.5 px-2 text-zinc-400">{{c}}</th></tr></thead>
                <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50">
                  <td v-for="c in genDataCols" :key="c" class="py-1 px-2 text-zinc-300 truncate max-w-[150px]">{{formatCell(r[c])}}</td></tr></tbody></table>
            </div>
          </div>
        </div>
      </div>

      <!-- ──────── 🏦 基金&ETF Tab ──────── -->
      <div v-if="activeTab === 'funds'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">🏦 基金&ETF</h3>
        <div class="grid grid-cols-2 gap-4">
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
            <h4 class="text-sm font-semibold text-white mb-3">ETF日行情</h4>
            <button @click="selectedFundTable='etf_daily'; loadGenericTableData('etf_daily')" class="px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-400 hover:text-white mb-3">加载数据</button>
            <div class="overflow-auto max-h-60 text-xs" v-if="genTableData === 'etf_daily' && genDataRows.length">
              <table class="w-full"><thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700"><th v-for="c in genDataCols" :key="c" class="text-left py-1.5 px-2 text-zinc-400">{{c}}</th></tr></thead>
                <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50"><td v-for="c in genDataCols" :key="c" class="py-1 px-2 text-zinc-300 truncate max-w-[150px]">{{formatCell(r[c])}}</td></tr></tbody></table>
            </div>
          </div>
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4">
            <h4 class="text-sm font-semibold text-white mb-3">基金净值 (fund_nav)</h4>
            <button @click="selectedFundTable='fund_nav'; loadGenericTableData('fund_nav')" class="px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-400 hover:text-white mb-3">加载数据</button>
            <div class="overflow-auto max-h-60 text-xs" v-if="genTableData === 'fund_nav' && genDataRows.length">
              <table class="w-full"><thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700"><th v-for="c in genDataCols" :key="c" class="text-left py-1.5 px-2 text-zinc-400">{{c}}</th></tr></thead>
                <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50"><td v-for="c in genDataCols" :key="c" class="py-1 px-2 text-zinc-300 truncate max-w-[150px]">{{formatCell(r[c])}}</td></tr></tbody></table>
            </div>
          </div>
          <div class="bg-zinc-900 rounded-2xl border border-zinc-800 p-4 col-span-2">
            <h4 class="text-sm font-semibold text-white mb-3">基金日行情 (fund_daily)</h4>
            <button @click="selectedFundTable='fund_daily'; loadGenericTableData('fund_daily')" class="px-3 py-1.5 bg-zinc-800 rounded text-xs text-zinc-400 hover:text-white mb-3">加载数据</button>
            <div class="overflow-auto max-h-60 text-xs" v-if="genTableData === 'fund_daily' && genDataRows.length">
              <table class="w-full"><thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700"><th v-for="c in genDataCols" :key="c" class="text-left py-1.5 px-2 text-zinc-400">{{c}}</th></tr></thead>
                <tbody><tr v-for="(r,i) in genDataRows" :key="i" class="border-b border-zinc-800/50"><td v-for="c in genDataCols" :key="c" class="py-1 px-2 text-zinc-300 truncate max-w-[150px]">{{formatCell(r[c])}}</td></tr></tbody></table>
            </div>
          </div>
        </div>
      </div>

      <!-- ──────── 📝 研报预期 Tab ──────── -->
      <div v-if="activeTab === 'consensus'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">📝 券商一致预期</h3>
        <div class="flex items-center gap-3 mb-4">
          <input v-model="consTsCode" placeholder="股票代码" class="w-48 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white outline-none focus:border-blue-500" />
          <button @click="loadConsensus()" :disabled="!consTsCode" class="px-4 py-1.5 bg-blue-600 rounded-lg text-sm text-white">查询</button>
        </div>
        <div v-if="consLoading" class="text-center py-8 text-zinc-500"><i class="fa-solid fa-spinner animate-spin mr-2"></i>加载中...</div>
        <div v-else-if="consData?.data?.length" class="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
          <div class="overflow-auto max-h-[60vh]">
            <table class="w-full text-xs">
              <thead class="sticky top-0 bg-zinc-900"><tr class="border-b border-zinc-700">
                <th v-for="c in consCols" :key="c" class="text-left py-2 px-3 text-zinc-400 font-medium">{{ c }}</th></tr></thead>
              <tbody><tr v-for="(r,i) in consData.data" :key="i" class="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                <td v-for="c in consCols" :key="c" class="py-1.5 px-3 text-zinc-300 truncate max-w-[150px]">{{ formatCell(r[c]) }}</td></tr></tbody>
            </table>
          </div>
        </div>
        <div v-else-if="!consLoading && consTsCode" class="text-center py-8 text-zinc-500 text-sm">暂无一致预期数据</div>
      </div>

      <!-- ──────── 数据摘要 Tab ──────── -->
      <div v-if="activeTab === 'summary'" class="p-4">
        <h3 class="text-lg font-semibold text-white mb-4">📦 数据摘要 · 全部30张表</h3>
        <div v-if="summaryLoading" class="text-center py-8 text-zinc-500"><i class="fa-solid fa-spinner animate-spin mr-2"></i>加载中...</div>
        <div v-else class="space-y-4">
          <!-- 总览卡片 -->
          <div class="grid grid-cols-4 gap-3">
            <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4 text-center">
              <div class="text-3xl font-bold text-blue-400">{{ summaryData?.total_tables || 0 }}</div>
              <div class="text-xs text-zinc-500 mt-1">数据表</div>
            </div>
            <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4 text-center">
              <div class="text-3xl font-bold text-emerald-400">{{ fmtCount(summaryData?.total_rows || 0) }}</div>
              <div class="text-xs text-zinc-500 mt-1">总行数</div>
            </div>
            <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4 text-center">
              <div class="text-3xl font-bold text-amber-400">{{ summaryData?.categories?.length || 0 }}</div>
              <div class="text-xs text-zinc-500 mt-1">分类</div>
            </div>
            <div class="bg-zinc-900 rounded-xl border border-zinc-800 p-4 text-center">
              <div class="text-lg font-bold" :class="summaryData?.db_connected ? 'text-emerald-400' : 'text-red-400'">
                {{ summaryData?.db_connected ? '✅ 已连接' : '❌ 异常' }}
              </div>
              <div class="text-xs text-zinc-500 mt-1">PostgreSQL</div>
            </div>
          </div>

          <!-- 按分类展示 -->
          <div v-for="cat in summaryData?.categories || []" :key="cat.category" class="bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
            <div class="px-4 py-3 border-b border-zinc-800 flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-lg">{{ catIcon(cat.category) }}</span>
                <span class="font-semibold text-white">{{ cat.category }}</span>
                <span class="text-xs text-zinc-600">({{ cat.table_count }} 表)</span>
              </div>
              <div class="text-xs text-zinc-500">{{ fmtCount(cat.total_rows) }} 行</div>
            </div>
            <div class="p-3 grid grid-cols-2 md:grid-cols-3 gap-2">
              <div
                v-for="t in cat.tables"
                :key="t.name"
                :class="[
                  'rounded-lg px-3 py-2 border flex items-center justify-between transition-colors cursor-pointer',
                  t.has_data ? 'border-emerald-500/20 bg-emerald-500/5 hover:bg-emerald-500/10' : 'border-zinc-800 bg-zinc-800/30 hover:bg-zinc-800/50'
                ]"
                @click="navigateToTable(t.name)"
              >
                <div>
                  <div class="text-sm text-white font-mono">{{ t.name }}</div>
                  <div class="text-xs text-zinc-500 mt-0.5">{{ t.has_data ? '✅' : '⬜' }}</div>
                </div>
                <div class="text-sm font-mono" :class="t.rows > 0 ? 'text-emerald-400' : 'text-zinc-600'">{{ fmtCount(t.rows) }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, onActivated, nextTick, watch } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'

defineOptions({ name: 'MarketDashView' })

// ─── 标签定义 ──────────────────────────────────────────

const tabs = [
  { id: 'summary', label: '数据摘要', icon: '📦' },
  { id: 'kline', label: 'K线行情', icon: '📈' },
  { id: 'reference', label: '基础参照', icon: '📊' },
  { id: 'financials', label: '财务报表', icon: '💰' },
  { id: 'premarket', label: '盘前&复权', icon: '📋' },
  { id: 'money', label: '资金&两融', icon: '💸' },
  { id: 'indices', label: '指数&权重', icon: '📉' },
  { id: 'funds', label: '基金&ETF', icon: '🏦' },
  { id: 'consensus', label: '研报预期', icon: '📝' },
]
const activeTab = ref('summary')

const tableLabelMap: Record<string, string> = {
  ref_stock_basic: '证券主档', ref_trade_cal: '交易日历', ref_stock_namechange: '股票更名',
  ref_new_share: '新股发行', ref_stk_limit_daily: '涨跌停价', ref_suspend_d: '停牌信息',
  stock_daily_basic: '盘前股本指标', stock_adj_factor: '复权因子', stock_auction_daily: '集合竞价',
  stock_top_list_daily: '龙虎榜', stock_block_trade: '大宗交易',
  margin_summary_daily: '两融汇总', margin_detail_daily: '两融明细',
  etf_daily: 'ETF行情', fund_daily: '基金行情', fund_nav: '基金净值',
  index_daily: '指数行情', index_weight: '指数权重',
  broker_report_consensus: '一致预期',
}

// ─── 指数行情 ──────────────────────────────────────────

const indices = ref<any[]>([])
const indicesLoading = ref(false)

async function loadIndices() {
  indicesLoading.value = true
  try {
    const res = await axios.get('/market/indices')
    indices.value = res.data.indices || []
  } catch (e) {
    console.warn('加载指数失败:', e)
  } finally {
    indicesLoading.value = false
  }
}

// ─── 数据摘要 ──────────────────────────────────────────

const summaryData = ref<any>(null)
const summaryLoading = ref(false)

async function loadSummary() {
  summaryLoading.value = true
  try {
    const res = await axios.get('/market/summary')
    summaryData.value = res.data
  } catch (e) {
    console.warn('加载摘要失败:', e)
  } finally {
    summaryLoading.value = false
  }
}

function catIcon(cat: string): string {
  const m: Record<string, string> = {
    '基础参照': '📖', '财务报表': '📑', 'K线行情': '📈', '盘前&复权&竞价': '⏰',
    '资金&两融&大宗': '💸', '指数&权重': '📉', '基金&ETF': '💰', '研报一致预期': '📝',
  }
  return m[cat] || '📋'
}

function navigateToTable(tableName: string) {
  if (tableName.startsWith('ref_')) { activeTab.value = 'reference'; selectedRefTable.value = tableName; loadRefTable(tableName) }
  else if (tableName.startsWith('fin_') || tableName === 'fin_income' || tableName === 'fin_balancesheet' || tableName === 'fin_cashflow' || tableName === 'fin_fina_indicator' || tableName === 'fin_forecast' || tableName === 'fin_dividend') {
    activeTab.value = 'financials'
  }
  else if (tableName.startsWith('stock_daily') || tableName === 'stock_weekly' || tableName === 'stock_monthly' || tableName === 'bars_daily') { activeTab.value = 'kline' }
  else if (tableName.startsWith('stock_daily_basic') || tableName === 'stock_adj_factor' || tableName === 'stock_auction_daily') { activeTab.value = 'premarket'; selectedPremTable.value = tableName; loadGenericTableData(tableName) }
  else if (tableName.includes('moneyflow') || tableName.includes('top_list') || tableName.includes('margin') || tableName.includes('block_trade')) { activeTab.value = 'money'; selectedMoneyTable.value = tableName; loadGenericTableData(tableName) }
  else if (tableName.startsWith('index_')) { activeTab.value = 'indices'; loadGenericTableData(tableName) }
  else if (tableName.startsWith('fund_') || tableName.startsWith('etf_')) { activeTab.value = 'funds'; selectedFundTable.value = tableName; loadGenericTableData(tableName) }
  else if (tableName.includes('broker') || tableName.includes('consensus')) { activeTab.value = 'consensus' }
}

// ─── K线行情 ───────────────────────────────────────────

const currentStock = ref('000001.SZ')
const currentTimeframe = ref('daily')
const klineLimit = ref(250)
const klineLoading = ref(false)
const klineError = ref('')
const klineData = ref<any>(null)
const klineStats = ref<any>(null)
const selectedIndex = ref('')
const klineChart = ref<any>(null)
const klineChartWrap = ref<HTMLElement>()
const indicatorsOn = ref<string[]>([])

const timeframes = [
  { id: 'daily', label: '日K' },
  { id: 'weekly', label: '周K' },
  { id: 'monthly', label: '月K' },
]

// 股票搜索
const stockSearch = ref('')
const searchResults = ref<any[]>([])
const showSearchDropdown = ref(false)
let searchTimer: any = null

function onStockSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  const q = stockSearch.value.trim()
  if (!q) { searchResults.value = []; return }
  searchTimer = setTimeout(async () => {
    try {
      const res = await axios.get('/market/stock-search', { params: { q, limit: 10 } })
      searchResults.value = res.data.results || []
    } catch { searchResults.value = [] }
  }, 300)
}

function selectStock(s: any) {
  currentStock.value = s.ts_code
  stockSearch.value = s.name
  showSearchDropdown.value = false
  searchResults.value = []
  loadKline()
}

function selectIndexForKline(idx: any) {
  currentStock.value = idx.ts_code
  selectedIndex.value = idx.ts_code
  selectedIndexKline.value = idx.ts_code
  activeTab.value = 'kline'
  loadKline()
}

function toggleIndicator(ind: string) {
  const i = indicatorsOn.value.indexOf(ind)
  if (i >= 0) indicatorsOn.value.splice(i, 1)
  else indicatorsOn.value.push(ind)
  renderKlineChart()
}

async function loadKline() {
  if (!currentStock.value) return
  klineLoading.value = true
  klineError.value = ''
  try {
    const res = await axios.get('/market/kline', {
      params: {
        ts_code: currentStock.value,
        freq: currentTimeframe.value,
        limit: klineLimit.value,
      }
    })
    if (res.data.error) {
      klineError.value = res.data.error
      klineData.value = null
      klineStats.value = null
      return
    }
    klineData.value = res.data
    klineStats.value = res.data.stats || null
    await nextTick()
    renderKlineChart()
  } catch (e: any) {
    klineError.value = e.response?.data?.detail || e.message
  } finally {
    klineLoading.value = false
  }
}

function renderKlineChart() {
  if (!klineChartWrap.value) return
  const bars = klineData.value?.bars || []
  if (!bars.length) return

  if (!klineChart.value) {
    klineChart.value = echarts.init(klineChartWrap.value, null, { renderer: 'canvas' })
    const ro = new ResizeObserver(() => klineChart.value?.resize())
    ro.observe(klineChartWrap.value)
  }

  const dates = bars.map((b: any) => b.time)
  const ohlc = bars.map((b: any) => [b.open, b.close, b.low, b.high])
  const volumes = bars.map((b: any) => b.vol)
  const closes = bars.map((b: any) => b.close)

  // 计算MA
  function calcMA(data: number[], period: number) {
    const result: (number | null)[] = []
    for (let i = 0; i < data.length; i++) {
      if (i < period - 1) { result.push(null); continue }
      let sum = 0
      for (let j = i - period + 1; j <= i; j++) sum += data[j]
      result.push(sum / period)
    }
    return result
  }

  const series: any[] = [{
    name: 'K线',
    type: 'candlestick',
    data: ohlc,
    itemStyle: {
      color: '#ef4444', color0: '#22c55e',
      borderColor: '#ef4444', borderColor0: '#22c55e',
    },
    xAxisIndex: 0, yAxisIndex: 0,
  }]

  if (indicatorsOn.value.includes('ma')) {
    [5, 10, 20, 60].forEach(p => {
      const ma = calcMA(closes, p)
      series.push({ name: `MA${p}`, type: 'line', data: ma, smooth: true,
        lineStyle: { width: 1 }, symbol: 'none', xAxisIndex: 0, yAxisIndex: 0 })
    })
  }

  if (indicatorsOn.value.includes('ema')) {
    [12, 26].forEach(p => {
      const ema = calcMA(closes, p) // using EMA-like approximation
      series.push({ name: `EMA${p}`, type: 'line', data: ema, smooth: true,
        lineStyle: { width: 1, type: 'dashed' }, symbol: 'none', xAxisIndex: 0, yAxisIndex: 0 })
    })
  }

  const hasMacd = indicatorsOn.value.includes('macd')
  const hasRsi = indicatorsOn.value.includes('rsi')
  const hasBoll = indicatorsOn.value.includes('boll')

  if (hasBoll) {
    const mid = calcMA(closes, 20)
    const upper = mid.map((v, i) => {
      if (v === null) return null
      let sum = 0, cnt = 0
      for (let j = Math.max(0, i - 19); j <= i; j++) { sum += closes[j]; cnt++ }
      const mean = sum / cnt
      let variance = 0
      for (let j = Math.max(0, i - 19); j <= i; j++) variance += (closes[j] - mean) ** 2
      return mean + 2 * Math.sqrt(variance / cnt)
    })
    const lower = mid.map((v, i) => {
      if (v === null) return null
      let sum = 0, cnt = 0
      for (let j = Math.max(0, i - 19); j <= i; j++) { sum += closes[j]; cnt++ }
      const mean = sum / cnt
      let variance = 0
      for (let j = Math.max(0, i - 19); j <= i; j++) variance += (closes[j] - mean) ** 2
      return mean - 2 * Math.sqrt(variance / cnt)
    })
    series.push({ name: 'BOLL MID', type: 'line', data: mid, smooth: true, lineStyle: { width: 1, color: '#f59e0b' }, symbol: 'none', xAxisIndex: 0, yAxisIndex: 0 })
    series.push({ name: 'BOLL UP', type: 'line', data: upper, smooth: true, lineStyle: { width: 0.5, color: '#f59e0b', type: 'dashed' }, symbol: 'none', xAxisIndex: 0, yAxisIndex: 0 })
    series.push({ name: 'BOLL LO', type: 'line', data: lower, smooth: true, lineStyle: { width: 0.5, color: '#f59e0b', type: 'dashed' }, symbol: 'none', xAxisIndex: 0, yAxisIndex: 0 })
  }

  // 成交量
  series.push({
    name: '成交量', type: 'bar', data: volumes,
    itemStyle: { color: (params: any) => {
      const i = params.dataIndex
      if (i < ohlc.length) return ohlc[i][1] > ohlc[i][0] ? '#ef444466' : '#22c55e66'
      return '#6b7280'
    }},
    xAxisIndex: 0, yAxisIndex: 1,
  })

  const grid: any[] = [
    { left: '3%', right: '1%', top: '5%', height: '55%' },
    { left: '3%', right: '1%', top: '68%', height: '20%' },
  ]
  const xAxes: any[] = [
    { type: 'category', data: dates, gridIndex: 0, axisLine: { lineStyle: { color: '#374151' } }, axisLabel: { color: '#6b7280', fontSize: 10 } },
    { type: 'category', data: dates, gridIndex: 1, axisLine: { lineStyle: { color: '#374151' } }, axisLabel: { show: false } },
  ]
  const yAxes: any[] = [
    { type: 'value', gridIndex: 0, scale: true, splitLine: { lineStyle: { color: '#1f2937' } }, axisLabel: { color: '#6b7280' } },
    { type: 'value', gridIndex: 1, axisLabel: { color: '#6b7280', fontSize: 9 }, splitLine: { show: false } },
  ]

  // RSI子图
  if (hasRsi) {
    const rsiData: (number | null)[] = []
    for (let i = 0; i < closes.length; i++) {
      if (i < 14) { rsiData.push(null); continue }
      let gain = 0, loss = 0
      for (let j = i - 13; j <= i; j++) {
        const diff = closes[j] - (j > 0 ? closes[j - 1] : closes[j])
        if (diff > 0) gain += diff
        else loss -= diff
      }
      const avgGain = gain / 14
      const avgLoss = loss / 14
      rsiData.push(avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss)))
    }
    grid.push({ left: '3%', right: '1%', top: '88%', height: '10%' })
    xAxes.push({ type: 'category', data: dates, gridIndex: grid.length - 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#374151' } } })
    yAxes.push({ type: 'value', gridIndex: grid.length - 1, min: 0, max: 100, splitLine: { lineStyle: { color: '#1f2937' } }, axisLabel: { color: '#6b7280', fontSize: 9 } })
    series.push({ name: 'RSI', type: 'line', data: rsiData, xAxisIndex: 2, yAxisIndex: 2,
      lineStyle: { color: '#8b5cf6', width: 1 }, symbol: 'none',
      markLine: { silent: true, data: [{ yAxis: 70, lineStyle: { color: '#ef4444', type: 'dashed' } }, { yAxis: 30, lineStyle: { color: '#22c55e', type: 'dashed' } }] } })
  }

  if (hasMacd) {
    const dif: (number | null)[] = [], dea: (number | null)[] = [], macdHist: (number | null)[] = []
    let prevEma12 = 0, prevEma26 = 0, prevDea = 0
    for (let i = 0; i < closes.length; i++) {
      const ema12 = i === 0 ? closes[i] : closes[i] * (2/13) + prevEma12 * (11/13)
      const ema26 = i === 0 ? closes[i] : closes[i] * (2/27) + prevEma26 * (25/27)
      const d = ema12 - ema26
      const de = i === 0 ? d : d * (2/10) + prevDea * (8/10)
      dif.push(d)
      dea.push(de)
      macdHist.push(2 * (d - de))
      prevEma12 = ema12; prevEma26 = ema26; prevDea = de
    }
    const macdGridIdx = grid.length
    grid.push({ left: '3%', right: '1%', top: (hasRsi ? '78%' : '88%'), height: '10%' })
    xAxes.push({ type: 'category', data: dates, gridIndex: macdGridIdx, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#374151' } } })
    yAxes.push({ type: 'value', gridIndex: macdGridIdx, splitLine: { lineStyle: { color: '#1f2937' } }, axisLabel: { color: '#6b7280', fontSize: 9 } })
    series.push({ name: 'DIF', type: 'line', data: dif, xAxisIndex: macdGridIdx, yAxisIndex: macdGridIdx, lineStyle: { color: '#f59e0b', width: 1 }, symbol: 'none' })
    series.push({ name: 'DEA', type: 'line', data: dea, xAxisIndex: macdGridIdx, yAxisIndex: macdGridIdx, lineStyle: { color: '#3b82f6', width: 1 }, symbol: 'none' })
    series.push({
      name: 'MACD', type: 'bar', data: macdHist, xAxisIndex: macdGridIdx, yAxisIndex: macdGridIdx,
      itemStyle: { color: (params: any) => {
        const v = macdHist[params.dataIndex]
        return (v ?? 0) >= 0 ? '#ef444488' : '#22c55e88'
      }}
    })
  }

  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid, xAxis: xAxes, yAxis: yAxes, series,
  }

  klineChart.value.setOption(option, true)
}

// ─── 基础参照 ──────────────────────────────────────────

const selectedRefTable = ref('ref_stock_basic')
const refSearchKey = ref('')
const refTableLoading = ref(false)
const refTableData = ref<any[]>([])
const refTableCols = ref<string[]>([])
const refTableTotal = ref(0)

async function loadRefTable(tableName: string) {
  refTableLoading.value = true
  try {
    const params: any = { limit: 100, offset: 0 }
    if (tableName === 'ref_stock_basic' && refSearchKey.value.trim()) {
      params.ts_code = refSearchKey.value.trim()
    }
    const res = await axios.get(`/market/table/${tableName}`, { params })
    refTableData.value = res.data.rows || []
    refTableCols.value = res.data.columns || (refTableData.value.length ? Object.keys(refTableData.value[0]) : [])
    refTableTotal.value = res.data.total_rows || 0
  } catch (e) {
    console.warn('加载表数据失败:', e)
  } finally {
    refTableLoading.value = false
  }
}

// ─── 财务报表 ──────────────────────────────────────────

const finTsCode = ref('000001.SZ')
const finLoading = ref(false)
const finData = ref<any>(null)
const selectedFinReport = ref('income')

const finReports = [
  { id: 'income', label: '利润表' },
  { id: 'balance', label: '资产负债表' },
  { id: 'cashflow', label: '现金流量表' },
  { id: 'indicator', label: '财务指标' },
  { id: 'forecast', label: '业绩预告' },
  { id: 'dividend', label: '分红送股' },
]

function getReportCols(reportId: string): string[] {
  const data = finData.value?.reports?.[reportId]?.data
  if (data?.length) return Object.keys(data[0])
  return []
}

async function loadFinancials() {
  if (!finTsCode.value) return
  finLoading.value = true
  try {
    const res = await axios.get('/market/financials', { params: { ts_code: finTsCode.value } })
    finData.value = res.data
  } catch (e) {
    console.warn('加载财务数据失败:', e)
  } finally {
    finLoading.value = false
  }
}

// ─── 盘前&复权 ─────────────────────────────────────────

const selectedPremTable = ref('stock_daily_basic')

// ─── 通用表数据 ────────────────────────────────────────

const genTableData = ref('')
const genDataRows = ref<any[]>([])
const genDataCols = ref<string[]>([])
const genDataTotal = ref(0)
const genDataLoading = ref(false)

async function loadGenericTableData(tableName: string) {
  genTableData.value = tableName
  genDataLoading.value = true
  try {
    const res = await axios.get(`/market/table/${tableName}`, { params: { limit: 50, offset: 0 } })
    genDataRows.value = res.data.rows || []
    genDataCols.value = res.data.columns || (genDataRows.value.length ? Object.keys(genDataRows.value[0]) : [])
    genDataTotal.value = res.data.total_rows || 0
  } catch (e) {
    console.warn('加载表数据失败:', e)
  } finally {
    genDataLoading.value = false
  }
}

async function loadGenericTable(tableName: string) {
  await loadGenericTableData(tableName)
}

// ─── 资金&两融 ─────────────────────────────────────────

const mfTsCode = ref('000001.SZ')
const mfLoading = ref(false)
const mfData = ref<any>(null)
const mfChart = ref<HTMLElement>()
let mfChartInst: any = null

const marginLoading = ref(false)
const marginData = ref<any>(null)
const marginChart = ref<HTMLElement>()
let marginChartInst: any = null
const selectedMoneyTable = ref('')

async function loadMoneyflow() {
  if (!mfTsCode.value) return
  mfLoading.value = true
  try {
    const res = await axios.get('/market/moneyflow', { params: { ts_code: mfTsCode.value, limit: 100 } })
    mfData.value = res.data
    await nextTick()
    renderMfChart()
  } catch (e) {
    console.warn('加载资金流向失败:', e)
  } finally {
    mfLoading.value = false
  }
}

function renderMfChart() {
  if (!mfChart.value || !mfData.value?.data?.length) return
  if (!mfChartInst) { mfChartInst = echarts.init(mfChart.value); new ResizeObserver(() => mfChartInst?.resize()).observe(mfChart.value) }
  const data = mfData.value.data
  const dates = data.map((d: any) => d.trade_date)
  const net = data.map((d: any) => (d.net_mf_amount || 0))
  mfChartInst.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#6b7280', fontSize: 9 }, axisLine: { lineStyle: { color: '#374151' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1f2937' } }, axisLabel: { color: '#6b7280' } },
    series: [{
      name: '净流入', type: 'bar', data: net,
      itemStyle: { color: (p: any) => (net[p.dataIndex] ?? 0) >= 0 ? '#ef4444' : '#22c55e' }
    }],
    grid: { left: '10%', right: '5%', top: '8%', bottom: '10%' },
  }, true)
}

async function loadMargin() {
  marginLoading.value = true
  try {
    const res = await axios.get('/market/margin', { params: { limit: 30 } })
    marginData.value = res.data
    await nextTick()
    renderMarginChart()
  } catch (e) {
    console.warn('加载两融数据失败:', e)
  } finally {
    marginLoading.value = false
  }
}

function renderMarginChart() {
  if (!marginChart.value || !marginData.value?.data?.length) return
  if (!marginChartInst) { marginChartInst = echarts.init(marginChart.value); new ResizeObserver(() => marginChartInst?.resize()).observe(marginChart.value) }
  const data = marginData.value.data
  const dates = data.map((d: any) => d.trade_date)
  const rzye = data.map((d: any) => d.rzye || 0)
  const rqye = data.map((d: any) => d.rqye || 0)
  marginChartInst.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { data: ['融资余额', '融券余额'], textStyle: { color: '#6b7280', fontSize: 10 }, top: 0 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#6b7280', fontSize: 9 }, axisLine: { lineStyle: { color: '#374151' } } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1f2937' } }, axisLabel: { color: '#6b7280' } },
    series: [
      { name: '融资余额', type: 'line', data: rzye, smooth: true, lineStyle: { color: '#ef4444' }, symbol: 'none' },
      { name: '融券余额', type: 'line', data: rqye, smooth: true, lineStyle: { color: '#3b82f6' }, symbol: 'none' },
    ],
    grid: { left: '10%', right: '5%', top: '15%', bottom: '10%' },
  }, true)
}

// ─── 指数K线 ───────────────────────────────────────────

const indexList = [
  { code: '000001.SH', name: '上证指数' },
  { code: '399001.SZ', name: '深证成指' },
  { code: '000300.SH', name: '沪深300' },
  { code: '399006.SZ', name: '创业板指' },
  { code: '000688.SH', name: '科创50' },
]
const selectedIndexKline = ref('000001.SH')
const idxKlineLoading = ref(false)
const idxKlineChart = ref<HTMLElement>()
let idxKlineChartInst: any = null

async function loadIndexKline(code: string) {
  idxKlineLoading.value = true
  try {
    const res = await axios.get('/market/index-kline', { params: { ts_code: code, limit: 300 } })
    const bars = res.data.bars || []
    await nextTick()
    renderIndexKlineChart(bars, indexList.find(i => i.code === code)?.name || code)
  } catch (e) {
    console.warn('加载指数K线失败:', e)
  } finally {
    idxKlineLoading.value = false
  }
}

function renderIndexKlineChart(bars: any[], name: string) {
  if (!idxKlineChart.value || !bars.length) return
  if (!idxKlineChartInst) { idxKlineChartInst = echarts.init(idxKlineChart.value); new ResizeObserver(() => idxKlineChartInst?.resize()).observe(idxKlineChart.value) }
  const dates = bars.map((b: any) => b.time)
  const ohlc = bars.map((b: any) => [b.open, b.close, b.low, b.high])
  const vols = bars.map((b: any) => b.vol)
  idxKlineChartInst.setOption({
    backgroundColor: 'transparent',
    title: { text: name, left: 'center', textStyle: { color: '#d1d5db', fontSize: 14 } },
    tooltip: { trigger: 'axis' },
    grid: [{ left: '3%', right: '1%', top: '12%', height: '55%' }, { left: '3%', right: '1%', top: '72%', height: '18%' }],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: '#6b7280', fontSize: 10 }, axisLine: { lineStyle: { color: '#374151' } } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#374151' } } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, splitLine: { lineStyle: { color: '#1f2937' } }, axisLabel: { color: '#6b7280' } },
      { type: 'value', gridIndex: 1, axisLabel: { color: '#6b7280', fontSize: 9 }, splitLine: { show: false } },
    ],
    series: [
      { name: 'K线', type: 'candlestick', data: ohlc, itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' }, xAxisIndex: 0, yAxisIndex: 0 },
      { name: '成交量', type: 'bar', data: vols, xAxisIndex: 0, yAxisIndex: 1, itemStyle: { color: (p: any) => { const i = p.dataIndex; return i < ohlc.length && ohlc[i][1] > ohlc[i][0] ? '#ef444466' : '#22c55e66' } } },
    ],
  }, true)
}

// ─── 研报预期 ──────────────────────────────────────────

const consTsCode = ref('000001.SZ')
const consLoading = ref(false)
const consData = ref<any>(null)
const consCols = ref<string[]>([])

async function loadConsensus() {
  if (!consTsCode.value) return
  consLoading.value = true
  try {
    const res = await axios.get('/market/consensus', { params: { ts_code: consTsCode.value, limit: 50 } })
    consData.value = res.data
    if (consData.value?.data?.length) consCols.value = Object.keys(consData.value.data[0])
  } catch (e) {
    console.warn('加载一致预期失败:', e)
  } finally {
    consLoading.value = false
  }
}

// ─── 基金&ETF ──────────────────────────────────────────

const selectedFundTable = ref('etf_daily')

// ─── 格式化工具 ────────────────────────────────────────

function fmtNum(v: any, decimals = 2): string {
  if (v == null || v === undefined || isNaN(v)) return '--'
  return Number(v).toFixed(decimals)
}

function fmtVol(v: any): string {
  if (v == null || v === undefined || isNaN(v)) return '--'
  const n = Number(v)
  if (n >= 1e8) return (n / 1e8).toFixed(1) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return n.toLocaleString()
}

function fmtCount(n: number): string {
  if (n < 0) return '-'
  if (n >= 1e8) return (n / 1e8).toFixed(1) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return n.toLocaleString()
}

function formatCell(val: any): string {
  if (val === null || val === undefined) return 'NULL'
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'object') return JSON.stringify(val).substring(0, 60)
  return String(val)
}

// ─── 生命周期 ──────────────────────────────────────────

onMounted(async () => {
  await loadIndices()
  if (!summaryData.value) loadSummary()
})

onActivated(() => {
  nextTick(() => {
    klineChart.value?.resize()
    mfChartInst?.resize()
    marginChartInst?.resize()
    idxKlineChartInst?.resize()
  })
})

onUnmounted(() => {
  klineChart.value?.dispose()
  mfChartInst?.dispose()
  marginChartInst?.dispose()
  idxKlineChartInst?.dispose()
})

watch(activeTab, (tab) => {
  if (tab === 'summary') {
    if (!summaryData.value && !summaryLoading.value) loadSummary()
  } else if (tab === 'kline') {
    nextTick(() => {
      if (!klineData.value && !klineLoading.value) loadKline()
      else klineChart.value?.resize()
    })
  } else if (tab === 'reference') {
    if (!refTableData.value.length && !refTableLoading.value) loadRefTable(selectedRefTable.value)
  } else if (tab === 'indices') {
    nextTick(() => loadIndexKline(selectedIndexKline.value))
  }
})
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar { height: 4px; width: 4px; }
.scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
.scrollbar-thin::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
</style>
