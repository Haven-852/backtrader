<template>
  <div class="absolute inset-0 flex flex-col min-h-0 bg-zinc-950 overflow-hidden">
    <!-- ===== 顶部指数栏 (同花顺风格) ===== -->
    <div class="flex-shrink-0 bg-zinc-900 border-b border-zinc-800">
      <div class="flex items-stretch overflow-x-auto scrollbar-thin">
        <div
          v-for="idx in indices"
          :key="idx.ts_code"
          @click="selectIndex(idx)"
          @contextmenu.prevent="openCtxMenu($event, { ts_code: idx.ts_code, name: idx.name || idx.short_name, short_name: idx.short_name, kind: 'index' })"
          :class="[
            'flex-shrink-0 cursor-pointer px-4 py-2.5 min-w-[140px] border-r border-zinc-800 transition-colors hover:bg-zinc-800/50',
            selectedTab === 'index' && currentIndex?.ts_code === idx.ts_code
              ? 'bg-zinc-800/80 border-b-2 border-b-white'
              : 'border-b-2 border-b-transparent'
          ]"
        >
          <div class="text-xs text-zinc-400 mb-0.5">{{ idx.short_name }}</div>
          <div class="flex items-baseline gap-2">
            <span
              :class="idx.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'"
              class="text-lg font-bold tabular-nums"
            >
              {{ formatPrice(idx.close) }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 mt-0.5">
            <span
              :class="idx.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'"
              class="text-xs tabular-nums"
            >
              {{ (idx.pct_chg ?? 0) >= 0 ? '+' : '' }}{{ Number(idx.pct_chg ?? 0).toFixed(2) }}%
            </span>
            <span
              :class="idx.pct_chg >= 0 ? 'text-red-400/70' : 'text-green-400/70'"
              class="text-xs tabular-nums"
            >
              {{ (idx.change ?? 0) >= 0 ? '+' : '' }}{{ Number(idx.change ?? 0).toFixed(2) }}
            </span>
          </div>
        </div>

        <!-- 指数加载指示 -->
        <div v-if="!indices.length" class="flex-shrink-0 flex items-center px-4 text-xs text-zinc-500">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i> 加载指数...
        </div>
      </div>
    </div>

    <!-- ===== 主工具栏 ===== -->
    <div class="flex items-center gap-3 px-4 py-2.5 bg-zinc-900/70 border-b border-zinc-800 flex-wrap flex-shrink-0">
      <!-- 股票搜索 -->
      <div class="relative flex-1 min-w-[200px] max-w-sm">
        <div class="relative">
          <input
            v-model="searchText"
            @input="onSearchInput"
            @focus="showDropdown = true"
            placeholder="搜索股票名称或代码..."
            class="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-lg pl-9 pr-3 py-1.5 text-sm outline-none transition-colors"
          />
          <i class="fa-solid fa-search absolute left-3 top-2 text-zinc-500 text-xs"></i>
        </div>
        <div
          v-if="showDropdown && searchResults.length > 0"
          class="absolute top-full mt-1 left-0 right-0 bg-zinc-800 border border-zinc-700 rounded-xl shadow-2xl z-50 max-h-80 overflow-auto"
        >
          <div
            v-for="stock in searchResults"
            :key="stock.ts_code"
            @mousedown.prevent="selectStock(stock)"
            @contextmenu.prevent="openCtxMenu($event, { ts_code: stock.ts_code, name: stock.name, short_name: stock.name, industry: stock.industry, kind: 'stock' })"
            class="flex items-center justify-between px-4 py-2.5 hover:bg-zinc-700 cursor-pointer transition-colors border-b border-zinc-700/50 last:border-0"
          >
            <div>
              <div class="text-sm font-medium">{{ stock.name }}</div>
              <div class="text-xs text-zinc-400">{{ stock.ts_code }}</div>
            </div>
            <div class="text-xs text-zinc-500">{{ stock.industry }}</div>
          </div>
        </div>
      </div>

      <!-- 周期选择 -->
      <div class="flex gap-0.5 bg-zinc-800 rounded-lg p-0.5">
        <button
          v-for="tf in quickTimeframes"
          :key="tf.id"
          @click="timeframe = tf.id; loadData()"
          :class="timeframe === tf.id
            ? 'bg-blue-600 text-white shadow'
            : 'text-zinc-400 hover:text-white hover:bg-zinc-700'"
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
        >
          {{ tf.name }}
        </button>
      </div>

      <!-- 技术指标（成交量固定显示在下方，不作为切换项） -->
      <div class="flex gap-1">
        <button
          v-for="ind in chartIndicators"
          :key="ind.id"
          @click="toggleIndicator(ind.id)"
          :class="activeIndicators.includes(ind.id)
            ? 'bg-emerald-600 text-white'
            : 'bg-zinc-800 text-zinc-400 hover:text-white'"
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
        >
          {{ ind.label }}
        </button>
      </div>

      <!-- 刷新 -->
      <button
        @click="loadData()"
        class="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-xs transition-colors flex items-center gap-1.5"
      >
        <i class="fa-solid fa-rotate" :class="{ 'animate-spin': loading }"></i>
      </button>
    </div>

    <!-- ===== 主内容区（可下滑） ===== -->
    <div class="flex-1 overflow-y-auto min-h-0 scrollbar-thin">
      <div class="flex min-h-[68vh]">
      <!-- ===== 自选股侧栏（可折叠，同花顺风格） ===== -->
      <aside
        class="relative flex-shrink-0 border-r border-zinc-800 bg-zinc-900/90 flex flex-col min-h-[68vh] transition-[width] duration-300 ease-out overflow-hidden"
        :style="{ width: watchlistOpen ? '200px' : '28px' }"
      >
        <!-- 折叠态：竖条触发 -->
        <button
          v-if="!watchlistOpen"
          @click="watchlistOpen = true"
          class="h-full w-full flex flex-col items-center justify-start pt-3 gap-2 hover:bg-zinc-800/80 transition-colors"
          title="展开自选股"
        >
          <i class="fa-solid fa-star text-amber-400 text-xs"></i>
          <span class="text-[11px] text-zinc-300 writing-vertical tracking-widest">自选股</span>
          <span class="text-[10px] text-zinc-600 tabular-nums">{{ watchlist.length }}</span>
          <i class="fa-solid fa-chevron-right text-[10px] text-zinc-500 mt-2"></i>
        </button>

        <!-- 展开态 -->
        <template v-else>
          <div class="flex items-center justify-between px-2.5 py-2 border-b border-zinc-800 flex-shrink-0">
            <div class="flex items-center gap-1.5 min-w-0">
              <i class="fa-solid fa-star text-amber-400 text-xs"></i>
              <span class="text-xs font-semibold text-zinc-200">自选股</span>
              <span class="text-[10px] text-zinc-500 tabular-nums">{{ watchlist.length }}</span>
            </div>
            <button
              @click="watchlistOpen = false"
              class="w-6 h-6 rounded hover:bg-zinc-800 text-zinc-400 hover:text-white flex items-center justify-center"
              title="收起"
            >
              <i class="fa-solid fa-chevron-left text-[10px]"></i>
            </button>
          </div>

          <div class="flex-1 overflow-y-auto scrollbar-thin">
            <div v-if="!watchlist.length" class="px-3 py-8 text-center text-[11px] text-zinc-500 leading-relaxed">
              暂无自选<br/>右键股票名称<br/>「添加到自选股」
            </div>
            <div
              v-for="item in watchlist"
              :key="item.ts_code"
              @click="selectWatchlistItem(item)"
              @contextmenu.prevent="openCtxMenu($event, item, true)"
              :class="[
                'px-2.5 py-2 border-b border-zinc-800/70 cursor-pointer transition-colors',
                currentTsCode === item.ts_code ? 'bg-zinc-800/90' : 'hover:bg-zinc-800/50'
              ]"
            >
              <div class="flex items-start justify-between gap-1">
                <div class="min-w-0">
                  <div class="text-xs text-zinc-100 truncate font-medium">{{ item.short_name || item.name }}</div>
                  <div class="text-[10px] text-zinc-500 font-mono truncate">{{ item.ts_code }}</div>
                </div>
                <button
                  @click.stop="removeFromWatchlist(item.ts_code)"
                  class="text-zinc-600 hover:text-red-400 text-[10px] p-0.5 flex-shrink-0"
                  title="移除自选"
                >
                  <i class="fa-solid fa-xmark"></i>
                </button>
              </div>
              <div v-if="item.kind" class="mt-0.5 text-[9px] text-zinc-600">
                {{ item.kind === 'index' ? '指数' : item.kind === 'etf' ? 'ETF' : '股票' }}
              </div>
            </div>
          </div>
        </template>
      </aside>

      <!-- 图表区域 -->
      <div class="flex-1 flex flex-col min-w-0 min-h-[68vh]">
        <!-- 标题栏 -->
        <div
          class="flex items-center gap-3 px-4 py-2 bg-zinc-900/50 border-b border-zinc-800 flex-shrink-0"
          @contextmenu.prevent="openCtxMenuForCurrent($event)"
        >
          <template v-if="selectedTab === 'index' && currentIndex">
            <span class="text-base font-bold">{{ currentIndex.name }}</span>
            <span class="text-xs text-zinc-500">{{ currentIndex.ts_code }}</span>
            <span class="text-xs text-zinc-600">{{ currentIndex.trade_date }}</span>
            <div class="ml-auto flex items-center gap-4 text-sm">
              <span class="text-zinc-500">最新</span>
              <span :class="currentIndex.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold tabular-nums">
                {{ formatPrice(currentIndex.close) }}
              </span>
              <span :class="currentIndex.pct_chg >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
                {{ currentIndex.pct_chg >= 0 ? '+' : '' }}{{ currentIndex.pct_chg.toFixed(2) }}%
              </span>
              <span :class="(currentIndex.pct_chg >= 0 ? 'text-red-400/70' : 'text-green-400/70')" class="tabular-nums">
                {{ currentIndex.change >= 0 ? '+' : '' }}{{ currentIndex.change.toFixed(2) }}
              </span>
            </div>
          </template>
          <template v-else-if="selectedTab === 'etf' && currentETF">
            <span class="text-base font-bold">{{ currentETF.name }}</span>
            <span class="text-xs text-zinc-500">{{ currentETF.ts_code }}</span>
            <span class="text-xs px-1.5 py-0.5 rounded bg-amber-600/20 text-amber-400">{{ currentETF.fund_type || 'ETF' }}</span>
            <span class="text-xs text-zinc-600">{{ currentETF.trade_date }}</span>
            <div v-if="latestBar" class="ml-auto flex items-center gap-4 text-sm">
              <span class="text-zinc-500">最新</span>
              <span :class="Number(changePercent) >= 0 ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold tabular-nums">
                {{ formatPrice(latestBar.close) }}
              </span>
              <span v-if="changePercent !== null" :class="Number(changePercent) >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
                {{ Number(changePercent) >= 0 ? '+' : '' }}{{ changePercent }}%
              </span>
            </div>
          </template>
          <template v-else-if="selectedTab === 'stock' && currentStock">
            <span class="text-base font-bold">{{ currentStock.name }}</span>
            <span class="text-xs text-zinc-500">{{ currentStock.ts_code }}</span>
            <span class="text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">{{ currentStock.industry }}</span>
            <div v-if="latestBar" class="ml-auto flex items-center gap-4 text-sm">
              <span class="text-zinc-500">最新</span>
              <span :class="Number(changePercent) >= 0 ? 'text-red-400' : 'text-green-400'" class="text-lg font-bold tabular-nums">
                {{ formatPrice(latestBar.close) }}
              </span>
              <span v-if="changePercent !== null" :class="Number(changePercent) >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
                {{ Number(changePercent) >= 0 ? '+' : '' }}{{ changePercent }}%
              </span>
              <span v-if="changeAmount !== null" :class="Number(changeAmount) >= 0 ? 'text-red-400/70' : 'text-green-400/70'" class="tabular-nums">
                {{ Number(changeAmount) >= 0 ? '+' : '' }}{{ changeAmount }}
              </span>
            </div>
          </template>
        </div>

        <!-- 加载态 -->
        <div v-if="loading && !chartData.bars.length" class="flex-1 flex items-center justify-center">
          <div class="flex flex-col items-center gap-3">
            <i class="fa-solid fa-spinner animate-spin text-3xl text-blue-400"></i>
            <span class="text-zinc-400 text-sm">加载K线数据...</span>
          </div>
        </div>

        <!-- 初始态（无选中） -->
        <div v-else-if="!loading && !chartData.bars.length" class="flex-1 flex items-center justify-center">
          <div class="text-center text-zinc-500">
            <i class="fa-solid fa-chart-line text-5xl mb-4 block opacity-40"></i>
            <p class="text-lg mb-2">实时行情看板</p>
            <p class="text-sm">点击上方大盘指数查看走势，或搜索个股名称查看K线</p>
            <p v-if="chartData.message" class="text-xs text-amber-500/80 mt-2">{{ chartData.message }}</p>
          </div>
        </div>

        <!-- 图表 -->
        <div v-else class="flex-1 flex flex-col min-h-0 overflow-y-auto">
          <!-- K 线主图（MA / EMA / BOLL 叠加于此） -->
          <div ref="mainChart" :class="hasSubIndicators ? 'h-[42vh] min-h-[260px]' : 'flex-1 min-h-[300px]'" class="flex-shrink-0"></div>

          <!-- 成交量（固定显示） -->
          <div v-show="hasBars" ref="volumeChart" class="h-28 flex-shrink-0 border-t border-zinc-800"></div>

          <!-- MACD / RSI / KDJ 独立子图窗口 -->
          <div v-show="hasBars && hasSubIndicators" class="flex-shrink-0 border-t border-zinc-800 bg-zinc-950/50">
            <div class="px-3 py-1.5 text-[10px] text-zinc-500 border-b border-zinc-800/80 tracking-wide">
              技术指标子图
            </div>
            <div v-show="showMACD" class="relative h-32 border-b border-zinc-800/60">
              <span class="absolute left-3 top-1.5 text-[10px] font-medium text-amber-400/80 z-10">MACD</span>
              <div ref="macdChart" class="w-full h-full"></div>
            </div>
            <div v-show="showRSI" class="relative h-32 border-b border-zinc-800/60">
              <span class="absolute left-3 top-1.5 text-[10px] font-medium text-indigo-400/80 z-10">RSI (14)</span>
              <div ref="rsiChart" class="w-full h-full"></div>
            </div>
            <div v-show="showKDJ" class="relative h-32">
              <span class="absolute left-3 top-1.5 text-[10px] font-medium text-purple-400/80 z-10">KDJ</span>
              <div ref="kdjChart" class="w-full h-full"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧信息面板 -->
      <div v-if="(currentIndex || currentStock || currentETF) && chartData.bars.length" class="w-72 bg-zinc-900 border-l border-zinc-800 p-4 overflow-auto hidden xl:block flex-shrink-0">
        <!-- 最新行情 -->
        <h3 class="text-xs font-semibold mb-3 text-zinc-400 uppercase tracking-wide">最新行情</h3>
        <div class="space-y-1.5 text-sm">
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">开盘</span>
            <span class="tabular-nums">{{ latestBar?.open ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">最高</span>
            <span class="text-red-400 tabular-nums">{{ latestBar?.high ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">最低</span>
            <span class="text-green-400 tabular-nums">{{ latestBar?.low ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">昨收</span>
            <span class="tabular-nums">{{ prevClose ?? '-' }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">成交量</span>
            <span class="tabular-nums">{{ formatVolume(latestBar?.vol) }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">成交额</span>
            <span class="tabular-nums">{{ formatAmount(latestBar?.amount || currentIndex?.amount) }}</span>
          </div>
        </div>

        <!-- 涨跌幅 -->
        <h3 class="text-xs font-semibold mt-5 mb-3 text-zinc-400 uppercase tracking-wide">涨跌</h3>
        <div class="space-y-1.5 text-sm">
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">涨跌额</span>
            <span :class="Number(changeAmount ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
              {{ changeAmount != null ? (Number(changeAmount) >= 0 ? '+' : '') + changeAmount : '-' }}
            </span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">涨跌幅</span>
            <span :class="Number(changePercent ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'" class="tabular-nums">
              {{ changePercent != null ? (Number(changePercent) >= 0 ? '+' : '') + changePercent + '%' : '-' }}
            </span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">振幅</span>
            <span class="tabular-nums">{{ amplitude }}%</span>
          </div>
        </div>

        <!-- 数据统计 -->
        <h3 class="text-xs font-semibold mt-5 mb-3 text-zinc-400 uppercase tracking-wide">统计</h3>
        <div class="space-y-1.5 text-sm">
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">数据条数</span>
            <span class="tabular-nums">{{ chartData.count }}</span>
          </div>
          <div class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">周期</span>
            <span>{{ timeframeName }}</span>
          </div>
        </div>

        <!-- 已启用指标 -->
        <h3 class="text-xs font-semibold mt-5 mb-2 text-zinc-400 uppercase tracking-wide">技术指标</h3>
        <div v-if="activeIndicators.length" class="flex flex-wrap gap-1.5">
          <span v-for="ind in activeIndicators" :key="ind"
            class="px-2 py-1 bg-emerald-600/20 text-emerald-400 rounded text-xs"
          >{{ ind.toUpperCase() }}</span>
        </div>
        <div v-else class="text-xs text-zinc-500">未选择</div>
      </div>
      </div>

      <!-- ===== ETF 板块（下滑可见） ===== -->
      <div class="border-t-2 border-zinc-700 bg-zinc-950">
        <div class="sticky top-0 z-20 px-4 py-3 bg-zinc-900/95 backdrop-blur border-b border-zinc-800 flex items-center justify-between">
          <div>
            <h2 class="text-sm font-semibold text-white flex items-center gap-2">
              <i class="fa-solid fa-layer-group text-amber-400/80"></i>
              ETF 板块行情
            </h2>
            <p class="text-xs text-zinc-500 mt-0.5">
              {{ etfBoard.status_message || '加载中...' }}
              <span v-if="etfBoard.trade_date"> · 行情日期 {{ etfBoard.trade_date }}</span>
            </p>
          </div>
          <div class="flex items-center gap-3">
            <span
              :class="etfBoard.is_trading ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' : 'text-zinc-400 bg-zinc-800 border-zinc-700'"
              class="text-xs px-2.5 py-1 rounded-full border flex items-center gap-1.5"
            >
              <span class="w-1.5 h-1.5 rounded-full" :class="etfBoard.is_trading ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'"></span>
              {{ etfBoard.is_trading ? '交易中' : '已休市' }}
            </span>
            <button @click="loadEtfBoard()" class="text-xs text-zinc-400 hover:text-white px-2 py-1">
              <i class="fa-solid fa-rotate" :class="{ 'animate-spin': etfLoading }"></i>
            </button>
          </div>
        </div>

        <div v-if="etfLoading && !etfBoard.primary_market" class="py-12 text-center text-zinc-500 text-sm">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i>加载 ETF 行情...
        </div>

        <template v-else>
          <!-- 市场切换 + 统计 -->
          <div class="flex items-center justify-between px-4 py-2 border-b border-zinc-800 bg-zinc-900/50">
            <div class="flex gap-1 bg-zinc-800 rounded-lg p-0.5">
              <button
                @click="etfActiveMarket = 'primary'"
                :class="etfActiveMarket === 'primary' ? 'bg-amber-600/30 text-amber-300' : 'text-zinc-400 hover:text-white'"
                class="px-3 py-1.5 rounded-md text-xs font-medium transition-all"
              >一级市场 · 上交所</button>
              <button
                @click="etfActiveMarket = 'secondary'"
                :class="etfActiveMarket === 'secondary' ? 'bg-blue-600/30 text-blue-300' : 'text-zinc-400 hover:text-white'"
                class="px-3 py-1.5 rounded-md text-xs font-medium transition-all"
              >二级市场 · 深交所</button>
            </div>
            <div v-if="activeEtfMarket?.summary" class="text-[10px] text-zinc-500 tabular-nums">
              展示 {{ activeEtfMarket.summary.count }} 只
              <span class="text-red-400 ml-2">↑{{ activeEtfMarket.summary.up_count }}</span>
              <span class="text-green-400 ml-1">↓{{ activeEtfMarket.summary.down_count }}</span>
              <span class="ml-2">均{{ activeEtfMarket.summary.avg_pct_chg >= 0 ? '+' : '' }}{{ activeEtfMarket.summary.avg_pct_chg }}%</span>
            </div>
          </div>

          <!-- 气泡图说明 -->
          <div class="px-4 py-2 text-[10px] text-zinc-500 border-b border-zinc-800/60">
            ETF 涨跌归因 · 气泡越大表示涨跌幅越大 · X轴=成交额(亿) · Y轴=涨跌幅(%) · 点击气泡查看K线
          </div>

          <!-- 气泡散点图 -->
          <div class="relative bg-zinc-950">
            <div v-if="!activeEtfList.length" class="h-[420px] flex items-center justify-center text-zinc-500 text-sm">
              暂无 {{ etfActiveMarket === 'primary' ? '一级' : '二级' }}市场 ETF 数据
            </div>
            <div v-show="activeEtfList.length" ref="etfBubbleChart" class="h-[420px] w-full" @contextmenu.prevent></div>
          </div>

          <div class="px-4 py-3 text-[10px] text-zinc-600 text-center border-t border-zinc-800">
            数据来源 PostgreSQL · etf_daily · 全市场按成交额取前 {{ etfBoard.limit || 100 }} 只
            <span v-if="!etfBoard.is_trading" class="text-zinc-500"> · 非交易时段显示最近收盘</span>
          </div>
        </template>
      </div>
    </div>

    <!-- 右键菜单 -->
    <div
      v-if="ctxMenu.visible"
      class="fixed z-[100] min-w-[160px] bg-zinc-800 border border-zinc-700 rounded-lg shadow-2xl py-1 text-sm"
      :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
      @click.stop
    >
      <div class="px-3 py-1.5 text-[10px] text-zinc-500 border-b border-zinc-700 truncate max-w-[200px]">
        {{ ctxMenu.item?.short_name || ctxMenu.item?.name || ctxMenu.item?.ts_code }}
      </div>
      <button
        v-if="!isInWatchlist(ctxMenu.item?.ts_code)"
        @click="ctxAddToWatchlist"
        class="w-full text-left px-3 py-2 hover:bg-zinc-700 text-zinc-100 flex items-center gap-2"
      >
        <i class="fa-solid fa-star text-amber-400 text-xs"></i>
        添加到自选股
      </button>
      <button
        v-if="isInWatchlist(ctxMenu.item?.ts_code)"
        @click="ctxRemoveFromWatchlist"
        class="w-full text-left px-3 py-2 hover:bg-zinc-700 text-zinc-100 flex items-center gap-2"
      >
        <i class="fa-solid fa-star-half-stroke text-zinc-400 text-xs"></i>
        从自选股移除
      </button>
      <button
        @click="ctxOpenChart"
        class="w-full text-left px-3 py-2 hover:bg-zinc-700 text-zinc-100 flex items-center gap-2 border-t border-zinc-700/60"
      >
        <i class="fa-solid fa-chart-line text-blue-400 text-xs"></i>
        查看 K 线
      </button>
    </div>

    <!-- 轻提示 -->
    <div
      v-if="toastMsg"
      class="fixed bottom-6 left-1/2 -translate-x-1/2 z-[110] px-4 py-2 rounded-lg bg-zinc-800 border border-zinc-600 text-sm text-zinc-100 shadow-xl"
    >
      {{ toastMsg }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'

const props = withDefaults(defineProps<{ active?: boolean }>(), { active: true })

// ─── 状态 ────────────────────────────────────────────

const searchText = ref('')
const searchResults = ref<any[]>([])
const showDropdown = ref(false)
const selectedTab = ref<'index' | 'stock' | 'etf'>('index')
const currentIndex = ref<any>(null)
const currentStock = ref<any>(null)
const currentETF = ref<any>(null)
const currentTsCode = ref<string>('000001.SH')
const timeframe = ref('day')
const activeIndicators = ref<string[]>(['ma'])
const loading = ref(false)
const indices = ref<any[]>([])
const etfBoard = ref<any>({})
const etfLoading = ref(false)
const etfActiveMarket = ref<'primary' | 'secondary'>('primary')
const etfBubbleChart = ref<HTMLDivElement>()

let etfBubbleInst: any = null

// ─── 自选股 ──────────────────────────────────────────
const WATCHLIST_KEY = 'dash_watchlist_v1'
const WATCHLIST_OPEN_KEY = 'dash_watchlist_open_v1'

function loadWatchlistFromStorage(): any[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY)
    const list = raw ? JSON.parse(raw) : []
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

const watchlist = ref<any[]>(loadWatchlistFromStorage())
const watchlistOpen = ref(localStorage.getItem(WATCHLIST_OPEN_KEY) !== '0')

watch(watchlist, (list) => {
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list))
}, { deep: true })

watch(watchlistOpen, (open) => {
  localStorage.setItem(WATCHLIST_OPEN_KEY, open ? '1' : '0')
  nextTick(() => handleResize())
})

const ctxMenu = ref<{ visible: boolean; x: number; y: number; item: any; isWatchlist: boolean }>({
  visible: false, x: 0, y: 0, item: null, isWatchlist: false,
})
const toastMsg = ref('')
let toastTimer: any = null
let ctxJustOpened = false

function showToast(msg: string) {
  toastMsg.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMsg.value = '' }, 1800)
}

function isInWatchlist(tsCode?: string) {
  if (!tsCode) return false
  return watchlist.value.some(w => w.ts_code === tsCode)
}

function openCtxMenu(e: MouseEvent, item: any, isWatchlist = false) {
  if (!item?.ts_code) return
  const pad = 8
  const menuW = 180
  const menuH = 120
  let x = e.clientX
  let y = e.clientY
  if (x + menuW > window.innerWidth) x = window.innerWidth - menuW - pad
  if (y + menuH > window.innerHeight) y = window.innerHeight - menuH - pad
  ctxMenu.value = { visible: true, x, y, item: { ...item }, isWatchlist }
  ctxJustOpened = true
  setTimeout(() => { ctxJustOpened = false }, 50)
}

function openCtxMenuForCurrent(e: MouseEvent) {
  if (selectedTab.value === 'index' && currentIndex.value) {
    openCtxMenu(e, {
      ts_code: currentIndex.value.ts_code,
      name: currentIndex.value.name,
      short_name: currentIndex.value.short_name,
      kind: 'index',
    })
  } else if (selectedTab.value === 'stock' && currentStock.value) {
    openCtxMenu(e, {
      ts_code: currentStock.value.ts_code,
      name: currentStock.value.name,
      short_name: currentStock.value.name,
      industry: currentStock.value.industry,
      kind: 'stock',
    })
  } else if (selectedTab.value === 'etf' && currentETF.value) {
    openCtxMenu(e, {
      ts_code: currentETF.value.ts_code,
      name: currentETF.value.name,
      short_name: currentETF.value.short_name || currentETF.value.name,
      kind: 'etf',
    })
  }
}

function closeCtxMenu() {
  ctxMenu.value.visible = false
}

async function ensureSelectiveBars(tsCode: string, mode: 'watchlist' | 'fresh' | 'extend' = 'fresh') {
  if (!tsCode) return null
  try {
    const res = await fetch('/data/selective/ensure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ts_code: tsCode, mode }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      console.warn('selective ensure failed', data)
      return null
    }
    return data
  } catch (e) {
    console.warn('selective ensure error', e)
    return null
  }
}

async function addToWatchlist(item: any) {
  if (!item?.ts_code) return
  if (isInWatchlist(item.ts_code)) {
    showToast('已在自选股中')
    return
  }
  watchlist.value = [
    {
      ts_code: item.ts_code,
      name: item.name || item.short_name || item.ts_code,
      short_name: item.short_name || item.name || item.ts_code,
      kind: item.kind || 'stock',
      industry: item.industry || '',
      added_at: new Date().toISOString(),
    },
    ...watchlist.value,
  ]
  if (!watchlistOpen.value) watchlistOpen.value = true
  showToast(`已添加：${item.short_name || item.name || item.ts_code}`)
  // 挑选式：个股拉近一季度日线 + 周/月 → PG
    if ((item.kind || 'stock') === 'stock') {
      showToast('正在补齐自选行情…')
      const r = await ensureSelectiveBars(item.ts_code, 'watchlist')
      if (r?.ok) {
        const bar = r.latest_bar || r.verify?.local
        const match = r.verify?.match
        const tip = bar
          ? `已入库 ${bar.trade_date} 收 ${bar.close}` + (match === false ? '（与远端不一致，请复查）' : '')
          : `行情已入库（日线 ${r.upserted?.daily ?? 0} 条）`
        showToast(tip)
        // 若当前正在看该股，刷新 K 线
        if (currentTsCode.value === item.ts_code) {
          await loadData()
        }
      }
    }
}

function removeFromWatchlist(tsCode: string) {
  watchlist.value = watchlist.value.filter(w => w.ts_code !== tsCode)
  showToast('已移出自选股')
}

function ctxAddToWatchlist() {
  addToWatchlist(ctxMenu.value.item)
  closeCtxMenu()
}

function ctxRemoveFromWatchlist() {
  const code = ctxMenu.value.item?.ts_code
  if (code) removeFromWatchlist(code)
  closeCtxMenu()
}

function ctxOpenChart() {
  const item = ctxMenu.value.item
  closeCtxMenu()
  if (item) selectWatchlistItem(item)
}

function selectWatchlistItem(item: any) {
  if (!item?.ts_code) return
  const kind = item.kind || 'stock'
  if (kind === 'index') {
    const found = indices.value.find((i: any) => i.ts_code === item.ts_code)
    selectIndex(found || {
      ts_code: item.ts_code,
      name: item.name,
      short_name: item.short_name || item.name,
      close: 0, pct_chg: 0, change: 0, pre_close: 0,
    })
  } else if (kind === 'etf') {
    const allEtfs = [
      ...(etfBoard.value.primary_market?.etfs || []),
      ...(etfBoard.value.secondary_market?.etfs || []),
    ]
    const found = allEtfs.find((e: any) => e.ts_code === item.ts_code)
    selectETF(found || {
      ts_code: item.ts_code,
      name: item.name,
      short_name: item.short_name || item.name,
      fund_type: 'ETF',
    })
  } else {
    selectStock({
      ts_code: item.ts_code,
      name: item.name || item.short_name,
      industry: item.industry || '',
    })
  }
}

function onGlobalClick() {
  if (ctxJustOpened) return
  if (ctxMenu.value.visible) closeCtxMenu()
}

const chartData = ref<any>({ bars: [], indicators: {}, count: 0 })

const SUB_CHART_INDICATORS = ['macd', 'rsi', 'kdj']

const chartIndicators = [
  { id: 'ma', label: 'MA', type: 'overlay' },
  { id: 'ema', label: 'EMA', type: 'overlay' },
  { id: 'boll', label: 'BOLL', type: 'overlay' },
  { id: 'macd', label: 'MACD', type: 'sub' },
  { id: 'rsi', label: 'RSI', type: 'sub' },
  { id: 'kdj', label: 'KDJ', type: 'sub' },
]

const mainChart = ref<HTMLDivElement>()
const volumeChart = ref<HTMLDivElement>()
const macdChart = ref<HTMLDivElement>()
const rsiChart = ref<HTMLDivElement>()
const kdjChart = ref<HTMLDivElement>()

let mainChartInst: any = null
let volumeChartInst: any = null
let macdChartInst: any = null
let rsiChartInst: any = null
let kdjChartInst: any = null
let searchTimer: any = null
let resizeHandler: any = null
let etfRefreshTimer: any = null

// ─── 计算属性 ────────────────────────────────────────

const timeframeName = computed(() => {
  const map: Record<string, string> = {
    day: '日线', week: '周线', month: '月线',
  }
  return map[timeframe.value] || timeframe.value
})

const quickTimeframes = [
  { id: 'day', name: '日K' },
  { id: 'week', name: '周K' },
  { id: 'month', name: '月K' },
]

const latestBar = computed(() => {
  const bars = chartData.value.bars
  return bars.length ? bars[bars.length - 1] : null
})

const changePercent = computed(() => {
  const bars = chartData.value.bars
  if (!bars.length) return null
  const last = bars[bars.length - 1]
  if (selectedTab.value === 'index' && currentIndex.value?.pct_chg != null) {
    return currentIndex.value.pct_chg.toFixed(2)
  }
  if (selectedTab.value === 'etf' && currentETF.value?.pct_chg != null) {
    return Number(currentETF.value.pct_chg).toFixed(2)
  }
  // 优先用后端/PG 的 pct_chg（与 Datahubco 一致）
  if (last.pct_chg != null && last.pct_chg !== '' && !Number.isNaN(Number(last.pct_chg))) {
    return Number(last.pct_chg).toFixed(4)
  }
  if (bars.length >= 2) {
    const prev = bars[bars.length - 2]
    if (prev.close && prev.close !== 0) {
      return (((last.close - prev.close) / prev.close) * 100).toFixed(2)
    }
  }
  const open = last.open
  if (!open || open === 0) return null
  return (((last.close - open) / open) * 100).toFixed(2)
})

const changeAmount = computed(() => {
  const bars = chartData.value.bars
  if (!bars.length) return null
  if (selectedTab.value === 'index' && currentIndex.value?.change != null) {
    return currentIndex.value.change
  }
  if (selectedTab.value === 'etf' && currentETF.value?.change != null) {
    return currentETF.value.change
  }
  const last = bars[bars.length - 1]
  if (last.change != null && last.change !== '' && !Number.isNaN(Number(last.change))) {
    return +Number(last.change).toFixed(2)
  }
  if (bars.length >= 2) {
    return +(last.close - bars[bars.length - 2].close).toFixed(2)
  }
  return null
})

const prevClose = computed(() => {
  const bars = chartData.value.bars
  if (selectedTab.value === 'index' && currentIndex.value?.pre_close) {
    return currentIndex.value.pre_close
  }
  const last = latestBar.value
  if (last?.pre_close != null && last.pre_close !== '') return Number(last.pre_close)
  if (bars.length >= 2) return bars[bars.length - 2].close
  return last?.open ?? null
})

const amplitude = computed(() => {
  if (!latestBar.value) return '-'
  const h = latestBar.value.high
  const l = latestBar.value.low
  const pre = prevClose.value || latestBar.value.open
  if (!pre || pre === 0) return '-'
  return ((h - l) / pre * 100).toFixed(2)
})

const hasBars = computed(() => chartData.value.bars.length > 0)
const showMACD = computed(() => activeIndicators.value.includes('macd'))
const showRSI = computed(() => activeIndicators.value.includes('rsi'))
const showKDJ = computed(() => activeIndicators.value.includes('kdj'))
const hasSubIndicators = computed(() => showMACD.value || showRSI.value || showKDJ.value)

const activeEtfMarket = computed(() =>
  etfActiveMarket.value === 'primary' ? etfBoard.value.primary_market : etfBoard.value.secondary_market
)
const activeEtfList = computed(() => activeEtfMarket.value?.etfs || [])

// ─── 数据加载 ────────────────────────────────────────

function summarizeEtfs(etfs: any[]) {
  if (!etfs.length) return { count: 0, up_count: 0, down_count: 0, flat_count: 0, avg_pct_chg: 0 }
  const ups = etfs.filter(e => (e.pct_chg ?? 0) > 0).length
  const downs = etfs.filter(e => (e.pct_chg ?? 0) < 0).length
  const avg = etfs.reduce((s, e) => s + (e.pct_chg ?? 0), 0) / etfs.length
  return { count: etfs.length, up_count: ups, down_count: downs, flat_count: etfs.length - ups - downs, avg_pct_chg: +avg.toFixed(2) }
}

async function loadEtfBoardFallback() {
  const res = await axios.get('/dash/etfs')
  const all: any[] = res.data.etfs || []
  const primary = all.filter(e => String(e.ts_code).endsWith('.SH'))
  const secondary = all.filter(e => String(e.ts_code).endsWith('.SZ'))
  const tradeDate = primary[0]?.trade_date || secondary[0]?.trade_date || ''
  etfBoard.value = {
    is_trading: false,
    status: 'halted',
    status_message: '已休市 · 显示最近交易日收盘',
    trade_date: tradeDate,
    primary_market: {
      id: 'primary', label: '一级市场（上交所）', exchange: 'SSE',
      etfs: primary, summary: summarizeEtfs(primary),
    },
    secondary_market: {
      id: 'secondary', label: '二级市场（深交所）', exchange: 'SZSE',
      etfs: secondary, summary: summarizeEtfs(secondary),
    },
    _fallback: true,
  }
}

function etfBubbleSize(absPct: number): number {
  const v = Math.abs(absPct || 0)
  return Math.max(10, Math.min(80, 8 + v * 7))
}

function renderEtfBubbleChart() {
  if (!etfBubbleChart.value || !activeEtfList.value.length) return

  if (!etfBubbleInst) {
    etfBubbleInst = echarts.init(etfBubbleChart.value, 'dark')
    etfBubbleInst.on('click', (params: any) => {
      const etf = params?.data?.etf
      if (etf) selectETF(etf)
    })
    etfBubbleInst.getZr().on('contextmenu', (params: any) => {
      params?.event?.preventDefault?.()
      const pointInPixel = [params.offsetX, params.offsetY]
      if (!etfBubbleInst.containPixel('grid', pointInPixel)) return
      // 找最近气泡
      let nearest: any = null
      let best = Infinity
      for (const d of (activeEtfList.value || [])) {
        const amountYi = d.amount_yi ?? (d.amount ? d.amount / 100000 : 0)
        const px = etfBubbleInst.convertToPixel({ seriesIndex: 0 }, [amountYi, d.pct_chg ?? 0])
        if (!px) continue
        const dist = Math.hypot(px[0] - params.offsetX, px[1] - params.offsetY)
        if (dist < best) { best = dist; nearest = d }
      }
      if (nearest && best < 40) {
        openCtxMenu(
          { clientX: params.event.clientX, clientY: params.event.clientY } as MouseEvent,
          {
            ts_code: nearest.ts_code,
            name: nearest.name,
            short_name: nearest.short_name || nearest.name,
            kind: 'etf',
          }
        )
      }
    })
  }

  const etfs = activeEtfList.value
  const pctValues = etfs.map((e: any) => e.pct_chg ?? 0)
  const pctMin = Math.min(...pctValues, -1)
  const pctMax = Math.max(...pctValues, 1)

  const scatterData = etfs.map((e: any) => {
    const amountYi = e.amount_yi ?? (e.amount ? e.amount / 100000 : 0)
    const absPct = e.abs_pct_chg ?? Math.abs(e.pct_chg ?? 0)
    const label = e.short_name && e.short_name !== e.ts_code ? e.short_name : (e.name || e.ts_code)
    return {
      name: label,
      value: [amountYi, e.pct_chg ?? 0],
      symbolSize: etfBubbleSize(absPct),
      etf: e,
      absPct,
      label: Math.abs(e.pct_chg ?? 0) >= 1.5 ? label : '',
    }
  })

  const marketTitle = etfActiveMarket.value === 'primary' ? '一级市场（上交所）' : '二级市场（深交所）'

  etfBubbleInst.setOption({
    backgroundColor: '#09090b',
    title: {
      text: `ETF 涨跌气泡图 · ${marketTitle}`,
      subtext: `气泡大小 = |涨跌幅| · 共 ${etfs.length} 只`,
      left: 'center',
      top: 4,
      textStyle: { color: '#e4e4e7', fontSize: 13, fontWeight: 600 },
      subtextStyle: { color: '#71717a', fontSize: 10 },
    },
    grid: { left: 56, right: 72, top: 52, bottom: 48 },
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => {
        const e = p.data?.etf
        if (!e) return ''
        return [
          `<b>${e.name || e.ts_code}</b>`,
          `代码: ${e.ts_code}`,
          `收盘: ${e.close}`,
          `涨跌幅: ${e.pct_chg >= 0 ? '+' : ''}${e.pct_chg}%`,
          `涨跌额: ${e.change}`,
          `成交额: ${e.amount_yi ?? '-'} 亿`,
          `成交量: ${e.vol ?? '-'}`,
        ].join('<br/>')
      },
    },
    xAxis: {
      name: '成交额(亿)',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { color: '#71717a', fontSize: 10 },
      axisLine: { lineStyle: { color: '#3f3f46' } },
      axisLabel: { color: '#a1a1aa', fontSize: 10 },
      splitLine: { lineStyle: { color: '#27272a' } },
    },
    yAxis: {
      name: '涨跌幅(%)',
      nameTextStyle: { color: '#71717a', fontSize: 10 },
      axisLine: { lineStyle: { color: '#3f3f46' } },
      axisLabel: { color: '#a1a1aa', fontSize: 10, formatter: (v: number) => `${v}%` },
      splitLine: { lineStyle: { color: '#27272a' } },
    },
    visualMap: {
      show: true,
      dimension: 1,
      min: pctMin,
      max: pctMax,
      calculable: false,
      orient: 'vertical',
      right: 8,
      top: 'center',
      itemHeight: 120,
      text: ['涨', '跌'],
      textStyle: { color: '#a1a1aa', fontSize: 9 },
      inRange: {
        color: ['#22c55e', '#fbbf24', '#ef4444'],
      },
    },
    series: [{
      type: 'scatter',
      data: scatterData,
      label: {
        show: true,
        formatter: (p: any) => p.data.label || '',
        fontSize: 9,
        color: '#d4d4d8',
        position: 'top',
      },
      emphasis: {
        scale: 1.15,
        label: { show: true, formatter: (p: any) => p.data.name },
      },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#52525b', type: 'dashed' },
        data: [{ yAxis: 0 }],
      },
    }],
  }, true)
  etfBubbleInst.resize()
}

async function loadEtfBoard() {
  etfLoading.value = true
  try {
    const res = await axios.get('/dash/etf-board', { params: { limit: 100 } })
    etfBoard.value = { ...res.data, limit: 100 }
  } catch (e) {
    console.warn('etf-board 不可用，降级到 /dash/etfs:', e)
    try {
      await loadEtfBoardFallback()
    } catch (e2) {
      console.warn('ETF 降级加载也失败:', e2)
      etfBoard.value = {
        status_message: 'ETF 数据加载失败，请重启后端后刷新',
        is_trading: false,
        primary_market: { etfs: [], summary: summarizeEtfs([]) },
        secondary_market: { etfs: [], summary: summarizeEtfs([]) },
      }
    }
  } finally {
    etfLoading.value = false
    setupEtfAutoRefresh()
    await nextTick()
    renderEtfBubbleChart()
  }
}

function setupEtfAutoRefresh() {
  if (etfRefreshTimer) clearInterval(etfRefreshTimer)
  if (etfBoard.value?.is_trading) {
    etfRefreshTimer = setInterval(() => loadEtfBoard(), 60000)
  }
}

async function loadIndices() {
  try {
    const res = await axios.get('/dash/indices')
    indices.value = res.data.indices || []
    if (indices.value.length > 0 && !currentIndex.value) {
      currentIndex.value = indices.value[0]
      currentTsCode.value = indices.value[0].ts_code
      loadData()
    }
  } catch (e) {
    console.warn('获取指数列表失败:', e)
  }
}

async function loadData() {
  if (!currentTsCode.value) return
  loading.value = true
  try {
    const inds = [...activeIndicators.value]
    if (!inds.includes('volume')) inds.push('volume')

    const params: any = {
      ts_code: currentTsCode.value,
      timeframe: timeframe.value,
      limit: 500,
      indicators: inds.join(','),
    }

    const res = await axios.get('/dash/kline', { params })
    chartData.value = res.data
    await renderAllCharts()
  } catch (e: any) {
    console.error('加载K线失败:', e)
    chartData.value = { bars: [], indicators: {}, count: 0, message: '加载K线失败，请检查后端是否启动' }
  } finally {
    loading.value = false
  }
}

function selectIndex(idx: any) {
  selectedTab.value = 'index'
  currentIndex.value = idx
  currentStock.value = null
  currentETF.value = null
  currentTsCode.value = idx.ts_code
  searchText.value = ''
  timeframe.value = 'day'
  loadData()
}

function selectStock(stock: any) {
  selectedTab.value = 'stock'
  currentStock.value = stock
  currentIndex.value = null
  currentETF.value = null
  currentTsCode.value = stock.ts_code
  searchText.value = stock.name
  showDropdown.value = false
  searchResults.value = []
  timeframe.value = 'day'
  loadData()
}

function selectETF(etf: any) {
  selectedTab.value = 'etf'
  currentETF.value = etf
  currentIndex.value = null
  currentStock.value = null
  currentTsCode.value = etf.ts_code
  searchText.value = ''
  timeframe.value = 'day'
  loadData()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function onSearchInput() {
  clearTimeout(searchTimer)
  if (searchText.value.length < 1) {
    searchResults.value = []
    showDropdown.value = false
    return
  }
  searchTimer = setTimeout(async () => {
    try {
      const res = await axios.get('/dash/search', { params: { q: searchText.value, limit: 10 } })
      searchResults.value = res.data.results || []
      showDropdown.value = true
    } catch (e) {
      console.warn('搜索失败:', e)
    }
  }, 300)
}

function indicatorDataReady(id: string): boolean {
  const ind = chartData.value.indicators || {}
  if (id === 'macd') return !!(ind.macd_DIF?.length)
  if (id === 'rsi') return !!(ind.rsi?.length)
  if (id === 'kdj') return !!(ind.kdj_K?.length)
  if (id === 'ma') return !!ind.ma
  if (id === 'ema') return !!ind.ema
  if (id === 'boll') return !!ind.boll_BOLL_MID
  return false
}

function disposeSubChart(id: string) {
  if (id === 'macd') { macdChartInst?.dispose(); macdChartInst = null }
  if (id === 'rsi') { rsiChartInst?.dispose(); rsiChartInst = null }
  if (id === 'kdj') { kdjChartInst?.dispose(); kdjChartInst = null }
}

async function toggleIndicator(id: string) {
  const idx = activeIndicators.value.indexOf(id)
  if (idx >= 0) {
    activeIndicators.value.splice(idx, 1)
    if (SUB_CHART_INDICATORS.includes(id)) {
      disposeSubChart(id)
      await nextTick()
      handleResize()
    } else {
      renderMainChart()
    }
    return
  }

  activeIndicators.value.push(id)
  if (!indicatorDataReady(id)) {
    await loadData()
  } else if (SUB_CHART_INDICATORS.includes(id)) {
    await renderSubCharts()
  } else {
    renderMainChart()
  }
}

function formatPrice(v: number | null): string {
  if (v == null) return '-'
  if (Math.abs(v) < 10) return Number(v).toFixed(3)
  return Number(v).toFixed(2)
}

function formatVolume(vol: number | null): string {
  if (!vol) return '-'
  if (vol >= 100000000) return (vol / 100000000).toFixed(2) + '亿'
  if (vol >= 10000) return (vol / 10000).toFixed(1) + '万'
  return String(vol)
}

function formatAmount(amount: number | null): string {
  if (!amount) return '-'
  if (amount >= 1e8) return (amount / 1e8).toFixed(2) + '亿'
  if (amount >= 1e4) return (amount / 1e4).toFixed(1) + '万'
  return String(amount)
}

// ─── 图表渲染（同花顺红涨绿跌配色） ──────────────────

function getChartDates(): string[] {
  return chartData.value.bars.map((b: any) => String(b.time).substring(0, 10))
}

function initChart(el: HTMLElement | undefined, inst: any): any {
  if (!el) return inst
  if (!inst) {
    return echarts.init(el, 'dark', { renderer: 'canvas' })
  }
  return inst
}

async function renderAllCharts() {
  await nextTick()
  renderMainChart()
  if (hasBars.value) renderVolumeChart()
  await renderSubCharts()
  await nextTick()
  handleResize()
}

async function renderSubCharts() {
  await nextTick()
  if (!hasBars.value) return

  if (showMACD.value) {
    renderMACDChart()
    macdChartInst?.resize()
  }
  if (showRSI.value) {
    renderRSIChart()
    rsiChartInst?.resize()
  }
  if (showKDJ.value) {
    renderKDJChart()
    kdjChartInst?.resize()
  }

  connectChartGroup()
}

function connectChartGroup() {
  const allCharts = [mainChartInst, volumeChartInst, macdChartInst, rsiChartInst, kdjChartInst].filter(Boolean)
  if (allCharts.length >= 2) {
    echarts.connect(allCharts)
  }
}

function getThemeColors() {
  return {
    bg: '#18181b',
    text: '#a1a1aa',
    border: '#27272a',
    up: '#ef4444',      // 红涨（中国习惯）
    down: '#22c55e',    // 绿跌
    ma5: '#fbbf24',
    ma10: '#60a5fa',
    ma20: '#c084fc',
    ma60: '#fb923c',
    grid: '#27272a',
  }
}

// ─── 共享的 dataZoom 索引（所有图表联动缩放） ──────
let sharedDataZoomId = 0

function makeDataZoom(dataLen: number, showDetail: boolean = true) {
  const id = ++sharedDataZoomId
  // 根据数据量动态决定初始显示范围
  let end = 100
  let start: number
  if (dataLen <= 80) {
    start = 0
    end = 100
  } else if (dataLen <= 250) {
    start = Math.max(0, Math.round(100 - 120 * 100 / dataLen))
  } else {
    start = Math.max(0, Math.round(100 - 150 * 100 / dataLen))
  }

  const parts: any[] = []
  // 只有主图（showDetail=true）显示 slider 滑块
  if (showDetail) {
    parts.push({
      type: 'slider',
      xAxisIndex: 0,
      start,
      end,
      height: 20,
      bottom: 0,
      borderColor: '#333',
      fillerColor: 'rgba(99,102,241,0.2)',
      handleStyle: { color: '#6366f1' },
      textStyle: { color: '#999', fontSize: 9 },
    })
  }
  // inside 缩放保留（子图同步通过 echarts.connect 实现）
  parts.push({
    type: 'inside',
    xAxisIndex: 0,
    start,
    end,
    zoomOnMouseWheel: true,
    moveOnMouseMove: true,
  })
  return parts
}

function renderMainChart() {
  if (!mainChart.value) return
  const c = getThemeColors()

  mainChartInst = initChart(mainChart.value, mainChartInst)
  if (!mainChartInst) return

  const bars = chartData.value.bars
  const barCount = bars.length
  const dates = bars.map((b: any) => b.time.substring(0, 10))
  const ohlc = bars.map((b: any) => [b.open, b.close, b.low, b.high])

  const series: any[] = [
    {
      name: 'K线',
      type: 'candlestick',
      data: ohlc,
      // 数据量大时启用渐进式渲染
      progressive: barCount > 200 ? 100 : 0,
      progressiveThreshold: 200,
      itemStyle: {
        color: c.up,
        color0: c.down,
        borderColor: c.up,
        borderColor0: c.down,
      },
    },
  ]

  // MA — 大数据集启用 large 模式（跳过重复的图形绘制逻辑）
  const ind = chartData.value.indicators
  if (ind.ma) {
    const maColors: Record<string, string> = { MA5: c.ma5, MA10: c.ma10, MA20: c.ma20, MA60: c.ma60 }
    for (const [key, values] of Object.entries(ind.ma)) {
      series.push({
        name: key,
        type: 'line',
        data: (values as any[]).filter((v: any) => v != null),
        smooth: false,
        symbol: 'none',
        large: (values as any[]).length > 200,
        largeThreshold: 200,
        lineStyle: { width: 1.5, color: (maColors as any)[key] || '#888' },
      })
    }
  }

  // EMA
  if (ind.ema) {
    for (const [key, values] of Object.entries(ind.ema)) {
      series.push({
        name: key,
        type: 'line',
        data: (values as any[]).filter((v: any) => v != null),
        smooth: false,
        symbol: 'none',
        large: (values as any[]).length > 200,
        largeThreshold: 200,
        lineStyle: { width: 1, type: 'dashed' },
      })
    }
  }

  // BOLL
  if (ind.boll_BOLL_UPPER) {
    series.push(
      { name: 'BOLL上轨', type: 'line', data: (ind.boll_BOLL_UPPER as any[]).filter((v: any) => v != null), smooth: false, symbol: 'none',
        large: true, largeThreshold: 200, lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
      { name: 'BOLL中轨', type: 'line', data: (ind.boll_BOLL_MID as any[]).filter((v: any) => v != null), smooth: false, symbol: 'none',
        large: true, largeThreshold: 200, lineStyle: { width: 1.5, color: '#818cf8' } },
      { name: 'BOLL下轨', type: 'line', data: (ind.boll_BOLL_LOWER as any[]).filter((v: any) => v != null), smooth: false, symbol: 'none',
        large: true, largeThreshold: 200, lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
    )
  }

  const hasSubChart = hasBars.value || showMACD.value || showRSI.value || showKDJ.value
  mainChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 20, bottom: hasSubChart ? 5 : 40 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: c.border } },
      axisLabel: { color: c.text, fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { lineStyle: { color: c.border } },
      axisLabel: { color: c.text, fontSize: 10, formatter: (v: number) => v.toFixed(0) },
      splitLine: { lineStyle: { color: c.grid } },
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: makeDataZoom(barCount, !hasSubChart),
    series,
  }, true)
  connectChartGroup()
}

function renderVolumeChart() {
  if (!volumeChart.value) return
  const c = getThemeColors()

  volumeChartInst = initChart(volumeChart.value, volumeChartInst)
  if (!volumeChartInst) return

  const bars = chartData.value.bars
  const barCount = bars.length
  const dates = bars.map((b: any) => b.time.substring(0, 10))
  const volumes = bars.map((b: any) => b.vol)
  const colors = bars.map((b: any) => b.close >= b.open ? c.up : c.down)

  const series: any[] = [{
    name: '成交量',
    type: 'bar',
    progressive: barCount > 200 ? 100 : 0,
    progressiveThreshold: 200,
    data: volumes.map((v: number, i: number) => ({ value: v, itemStyle: { color: colors[i] } })),
  }]

  const ind = chartData.value.indicators
  if (ind.VOL_MA5) {
    const d5 = (ind.VOL_MA5 as any[]).filter((v: any) => v != null)
    series.push({ name: 'VOL_MA5', type: 'line', data: d5, smooth: false,
      symbol: 'none', large: d5.length > 200, largeThreshold: 200,
      lineStyle: { width: 1, color: c.ma5 } })
  }
  if (ind.VOL_MA10) {
    const d10 = (ind.VOL_MA10 as any[]).filter((v: any) => v != null)
    series.push({ name: 'VOL_MA10', type: 'line', data: d10, smooth: false,
      symbol: 'none', large: d10.length > 200, largeThreshold: 200,
      lineStyle: { width: 1, color: c.ma10 } })
  }

  volumeChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 5, bottom: 5 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: {
      type: 'value',
      axisLabel: { color: c.text, fontSize: 9, formatter: (v: number) => v >= 100000000 ? (v / 100000000).toFixed(1) + '亿' : v >= 10000 ? (v / 10000).toFixed(0) + '万' : v },
      splitLine: { lineStyle: { color: c.grid } },
    },
    dataZoom: makeDataZoom(barCount, false),
    series,
  }, true)
}

function renderMACDChart() {
  if (!showMACD.value || !macdChart.value) return
  const c = getThemeColors()
  macdChartInst = initChart(macdChart.value, macdChartInst)
  if (!macdChartInst) return

  const ind = chartData.value.indicators || {}
  const dif = ind.macd_DIF || []
  const dea = ind.macd_DEA || []
  const macdHist = ind.macd_MACD || []
  if (!dif.length) return

  const dates = getChartDates()
  const histColors = macdHist.map((v: number) => (v ?? 0) >= 0 ? c.up : c.down)

  macdChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    legend: { data: ['DIF', 'DEA', 'MACD'], textStyle: { color: c.text, fontSize: 9 }, top: 0, right: 8 },
    grid: { left: '8%', right: '3%', top: 22, bottom: 5 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'line' } },
    dataZoom: makeDataZoom(dates.length, false),
    series: [
      { name: 'MACD', type: 'bar', data: macdHist.map((v: number, i: number) => ({ value: v, itemStyle: { color: histColors[i] } })) },
      { name: 'DIF', type: 'line', data: dif, symbol: 'none', lineStyle: { width: 1.2, color: '#fbbf24' } },
      { name: 'DEA', type: 'line', data: dea, symbol: 'none', lineStyle: { width: 1.2, color: '#60a5fa' } },
    ],
  }, true)
}

function renderRSIChart() {
  if (!showRSI.value || !rsiChart.value) return
  const c = getThemeColors()
  rsiChartInst = initChart(rsiChart.value, rsiChartInst)
  if (!rsiChartInst) return

  const rsiData = chartData.value.indicators?.rsi || []
  if (!rsiData.length) return

  const dates = getChartDates()
  rsiChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: '8%', right: '3%', top: 18, bottom: 5 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'line' } },
    dataZoom: makeDataZoom(dates.length, false),
    series: [{
      name: 'RSI', type: 'line', data: rsiData, symbol: 'none',
      lineStyle: { width: 1.5, color: '#818cf8' },
      markLine: { silent: true, data: [
        { yAxis: 70, lineStyle: { color: c.up, width: 1, type: 'dashed' } },
        { yAxis: 30, lineStyle: { color: c.down, width: 1, type: 'dashed' } },
      ] },
    }],
  }, true)
}

function renderKDJChart() {
  if (!showKDJ.value || !kdjChart.value) return
  const c = getThemeColors()
  kdjChartInst = initChart(kdjChart.value, kdjChartInst)
  if (!kdjChartInst) return

  const ind = chartData.value.indicators || {}
  const kData = ind.kdj_K || []
  const dData = ind.kdj_D || []
  const jData = ind.kdj_J || []
  if (!kData.length) return

  const dates = getChartDates()
  kdjChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    legend: { data: ['K', 'D', 'J'], textStyle: { color: c.text, fontSize: 9 }, top: 0, right: 8 },
    grid: { left: '8%', right: '3%', top: 22, bottom: 5 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: c.text, fontSize: 9 }, splitLine: { lineStyle: { color: c.grid } } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'line' } },
    dataZoom: makeDataZoom(dates.length, false),
    series: [
      { name: 'K', type: 'line', data: kData, symbol: 'none', lineStyle: { width: 1.2, color: '#fbbf24' } },
      { name: 'D', type: 'line', data: dData, symbol: 'none', lineStyle: { width: 1.2, color: '#60a5fa' } },
      { name: 'J', type: 'line', data: jData, symbol: 'none', lineStyle: { width: 1.2, color: '#c084fc' } },
    ],
  }, true)
}

function handleResize() {
  mainChartInst?.resize()
  volumeChartInst?.resize()
  macdChartInst?.resize()
  rsiChartInst?.resize()
  kdjChartInst?.resize()
  etfBubbleInst?.resize()
}

// ─── 生命周期 ────────────────────────────────────────

watch([showMACD, showRSI, showKDJ], async () => {
  if (!hasBars.value) return
  await renderSubCharts()
})

watch(etfActiveMarket, async () => {
  await nextTick()
  renderEtfBubbleChart()
})

// Tab 从隐藏切回显示时，容器刚恢复尺寸，ECharts 需要 resize，否则会空白或卡顿感
watch(
  () => props.active,
  async (visible) => {
    if (!visible) return
    await nextTick()
    handleResize()
    if (hasBars.value) {
      await renderAllCharts()
    }
    if (activeEtfList.value.length) {
      renderEtfBubbleChart()
    }
  }
)

onMounted(() => {
  loadIndices()
  loadEtfBoard()
  resizeHandler = () => handleResize()
  window.addEventListener('resize', resizeHandler)
  document.addEventListener('click', onGlobalClick)
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  clearTimeout(toastTimer)
  if (etfRefreshTimer) clearInterval(etfRefreshTimer)
  window.removeEventListener('resize', resizeHandler)
  document.removeEventListener('click', onGlobalClick)
  mainChartInst?.dispose()
  volumeChartInst?.dispose()
  macdChartInst?.dispose()
  rsiChartInst?.dispose()
  kdjChartInst?.dispose()
  etfBubbleInst?.dispose()
})
</script>

<style scoped>
.scrollbar-thin::-webkit-scrollbar { height: 4px; width: 4px; }
.scrollbar-thin::-webkit-scrollbar-track { background: transparent; }
.scrollbar-thin::-webkit-scrollbar-thumb { background: #374151; border-radius: 2px; }
.writing-vertical {
  writing-mode: vertical-rl;
  text-orientation: mixed;
}
</style>
