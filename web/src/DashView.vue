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
          @click="setTimeframe(tf)"
          :disabled="tf.id === 'intraday' && selectedTab !== 'stock'"
          :class="timeframe === tf.id
            ? 'bg-blue-600 text-white shadow'
            : 'text-zinc-400 hover:text-white hover:bg-zinc-700'"
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed"
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
          class="px-2.5 py-1 rounded-md text-xs font-medium transition-all inline-flex items-center gap-1"
        >
          <i v-if="indicatorBusy.includes(ind.id)" class="fa-solid fa-spinner animate-spin text-[10px]"></i>
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
                  <div class="text-[10px] text-zinc-500 font-mono truncate flex items-center gap-1">
                    <span>{{ item.ts_code }}</span>
                    <span v-if="(item.kind || 'stock') === 'stock'" class="text-[8px] text-emerald-500/80">日更</span>
                  </div>
                  <div
                    v-if="showFetchProgress(item.ts_code)"
                    class="mt-1 h-1 w-full rounded-full bg-zinc-800 overflow-hidden"
                    :title="fetchProgress.label"
                  >
                    <div class="h-full bg-sky-400 transition-all duration-200" :style="{ width: fetchProgress.percent + '%' }"></div>
                  </div>
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
        <div
          v-if="dashFromScreener"
          class="flex items-center gap-2 px-4 py-1.5 bg-emerald-950/40 border-b border-emerald-900/40 text-[11px] text-emerald-300 flex-shrink-0"
        >
          <button
            type="button"
            class="px-2 py-0.5 rounded bg-emerald-600/20 border border-emerald-500/30 hover:bg-emerald-600/30"
            @click="backToScreener"
          >
            <i class="fa-solid fa-arrow-left mr-1"></i>返回选股结果
          </button>
          <span class="text-emerald-500/80">来自「低于5日均线」，搜索结果仍保留</span>
        </div>
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
            <div
              v-if="showFetchProgress(currentStock.ts_code)"
              class="flex items-center gap-1.5 min-w-[108px]"
              :title="fetchProgress.label"
            >
              <div class="h-1.5 w-24 rounded-full bg-zinc-800 overflow-hidden">
                <div class="h-full bg-sky-400 transition-all duration-200" :style="{ width: fetchProgress.percent + '%' }"></div>
              </div>
              <span class="text-[9px] text-sky-300 tabular-nums">{{ fetchProgress.percent }}%</span>
            </div>
            <span class="text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">{{ currentStock.industry }}</span>
            <span
              v-if="timeframe === 'intraday' && (chartData.source || chartData.trade_date)"
              class="text-[10px] px-1.5 py-0.5 rounded"
              :class="chartData.live ? 'bg-red-500/15 text-red-300' : chartData.session_phase === 'after_close' ? 'bg-amber-500/15 text-amber-300' : 'bg-sky-500/15 text-sky-300'"
            >{{ intradayBadgeText }}</span>
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

        <!-- 图表容器：块级固定高度，避免 flex/overflow 导致 ECharts 画布 0 尺寸 -->
        <div class="flex-1 flex flex-col min-h-0 overflow-hidden relative bg-zinc-950">
          <div
            v-if="loading && !chartData.bars.length"
            class="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950"
          >
            <div class="flex flex-col items-center gap-3">
              <i class="fa-solid fa-spinner animate-spin text-3xl text-blue-400"></i>
              <span class="text-zinc-400 text-sm">{{ loadingHint }}</span>
            </div>
          </div>
          <div
            v-else-if="!loading && !chartData.bars.length"
            class="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950"
          >
            <div class="text-center text-zinc-500">
              <i class="fa-solid fa-chart-line text-5xl mb-4 block opacity-40"></i>
              <p class="text-lg mb-2">实时行情看板</p>
              <p class="text-sm">点击上方大盘指数查看走势，或搜索个股名称查看K线</p>
              <p v-if="chartData.message" class="text-xs text-amber-500/80 mt-2">{{ chartData.message }}</p>
            </div>
          </div>
          <div
            v-else-if="chartRenderError"
            class="absolute inset-0 z-10 flex items-center justify-center bg-zinc-950/95"
          >
            <div class="text-center text-amber-400 px-6">
              <i class="fa-solid fa-triangle-exclamation text-3xl mb-3 block"></i>
              <p class="text-sm mb-2">图表渲染失败</p>
              <p class="text-xs text-zinc-500 mb-3">{{ chartRenderError }}</p>
              <button
                class="px-3 py-1.5 rounded bg-zinc-800 text-xs text-zinc-200 hover:bg-zinc-700"
                @click="retryRenderCharts"
              >重新渲染</button>
            </div>
          </div>

          <div class="flex-1 min-h-0 overflow-y-auto">
            <div
              :key="'main-' + chartMountKey"
              ref="mainChart"
              class="w-full border-b border-zinc-800/80"
              style="height: 420px;"
            ></div>
            <div
              v-show="hasBars"
              :key="'vol-' + chartMountKey"
              ref="volumeChart"
              class="w-full border-b border-zinc-800"
              style="height: 112px;"
            ></div>
            <div v-show="hasBars && hasSubIndicators" class="bg-zinc-950/50">
              <div class="px-3 py-1.5 text-[10px] text-zinc-500 border-b border-zinc-800/80 tracking-wide">
                技术指标子图
              </div>
              <div v-show="showMACD" class="relative border-b border-zinc-800/60" style="height: 128px;">
                <span class="absolute left-3 top-1.5 text-[10px] font-medium text-amber-400/80 z-10">MACD</span>
                <div :key="'macd-' + chartMountKey" ref="macdChart" class="w-full h-full"></div>
              </div>
              <div v-show="showRSI" class="relative border-b border-zinc-800/60" style="height: 128px;">
                <span class="absolute left-3 top-1.5 text-[10px] font-medium text-indigo-400/80 z-10">RSI (14)</span>
                <div :key="'rsi-' + chartMountKey" ref="rsiChart" class="w-full h-full"></div>
              </div>
              <div v-show="showKDJ" class="relative" style="height: 128px;">
                <span class="absolute left-3 top-1.5 text-[10px] font-medium text-purple-400/80 z-10">KDJ</span>
                <div :key="'kdj-' + chartMountKey" ref="kdjChart" class="w-full h-full"></div>
              </div>
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

        <!-- 均线 -->
        <h3 class="text-xs font-semibold mt-5 mb-3 text-zinc-400 uppercase tracking-wide">均线 MA</h3>
        <div class="space-y-1.5 text-sm">
          <div v-for="row in maRows" :key="row.key" class="flex justify-between py-1.5 border-b border-zinc-800/50">
            <span class="text-zinc-500">{{ row.key }}</span>
            <span class="tabular-nums" :style="{ color: row.color }">{{ row.value ?? '-' }}</span>
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

      <!-- ===== 东财行业板块（气泡图下钻） ===== -->
      <div class="border-t-2 border-zinc-700 bg-zinc-950">
        <div class="sticky top-0 z-20 px-4 py-3 bg-zinc-900/95 backdrop-blur border-b border-zinc-800 flex items-center justify-between">
          <div>
            <h2 class="text-sm font-semibold text-white flex items-center gap-2">
              <i class="fa-solid fa-layer-group text-amber-400/80"></i>
              东财行业板块
            </h2>
            <p class="text-xs text-zinc-500 mt-0.5">
              {{ sectorBoard.status_message || '加载中...' }}
              <span v-if="sectorBoard.trade_date"> · 行情日期 {{ sectorBoard.trade_date }}</span>
            </p>
          </div>
          <div class="flex items-center gap-3">
            <button @click="syncAndLoadSectors()" class="text-xs text-zinc-400 hover:text-white px-2 py-1" title="同步 Datahubco">
              <i class="fa-solid fa-cloud-arrow-down" :class="{ 'animate-pulse': sectorSyncing }"></i>
            </button>
            <button @click="loadSectorBoard()" class="text-xs text-zinc-400 hover:text-white px-2 py-1">
              <i class="fa-solid fa-rotate" :class="{ 'animate-spin': sectorLoading }"></i>
            </button>
          </div>
        </div>

        <div v-if="sectorLoading && !sectorBoard.sectors?.length" class="py-12 text-center text-zinc-500 text-sm">
          <i class="fa-solid fa-spinner animate-spin mr-2"></i>加载行业板块...
        </div>

        <template v-else>
          <div class="flex items-center justify-between px-4 py-2 border-b border-zinc-800 bg-zinc-900/50">
            <div class="flex gap-1 bg-zinc-800 rounded-lg p-0.5">
              <button
                v-for="lv in sectorLevelTabs"
                :key="lv.key"
                @click="sectorLevel = lv.key; clearSectorDrill(); clearActiveSector(); loadSectorBoard()"
                :class="sectorLevel === lv.key ? 'bg-amber-600/30 text-amber-300' : 'text-zinc-400 hover:text-white'"
                class="px-3 py-1.5 rounded-md text-xs font-medium transition-all"
              >{{ lv.label }}</button>
            </div>
            <div v-if="sectorBoard.summary" class="text-[10px] text-zinc-500 tabular-nums">
              展示 {{ sectorBoard.summary.count }} 个
              <span class="text-red-400 ml-2">↑{{ sectorBoard.summary.up_count }}</span>
              <span class="text-green-400 ml-1">↓{{ sectorBoard.summary.down_count }}</span>
              <span class="ml-2">均{{ sectorBoard.summary.avg_pct_change >= 0 ? '+' : '' }}{{ sectorBoard.summary.avg_pct_change }}%</span>
            </div>
          </div>

          <div class="px-4 py-2 text-[10px] text-zinc-500 border-b border-zinc-800/60 flex flex-wrap gap-x-3 gap-y-1">
            <span>分位疏密坐标 · 气泡名始终显示（重叠时自动避让，放大后露出更多）· 点击一级/二级下钻</span>
            <span class="text-zinc-600">滚轮缩放 · 拖拽平移 · 双击放大 · 工具栏框选 · 缩放时坐标持续显示真实值</span>
          </div>

          <div class="relative bg-zinc-950">
            <div v-if="!(sectorBoard.sectors?.length)" class="h-[420px] flex items-center justify-center text-zinc-500 text-sm">
              暂无行业板块数据，可点击云图标同步
            </div>
            <div v-show="sectorBoard.sectors?.length" ref="sectorBubbleChart" class="h-[420px] w-full" @contextmenu.prevent></div>
          </div>

          <!-- 层级下钻副图 -->
          <div v-if="drillParent" class="border-t border-zinc-800">
            <div class="px-4 py-2 flex items-center justify-between bg-zinc-900/70 gap-2">
              <div class="flex items-center gap-1 text-xs text-zinc-400 flex-wrap min-w-0">
                <button @click="clearSectorDrill()" class="hover:text-amber-300 shrink-0">总览</button>
                <template v-for="(node, idx) in drillStack" :key="node.ts_code">
                  <span class="text-zinc-600">/</span>
                  <button
                    @click="jumpDrillTo(idx)"
                    :class="idx === drillStack.length - 1 ? 'text-amber-300 font-medium' : 'hover:text-amber-200'"
                    class="truncate max-w-[140px]"
                  >{{ node.name || node.ts_code }}</button>
                </template>
                <span class="text-zinc-600 ml-2 shrink-0">子板块气泡</span>
                <span v-if="childBoard.summary" class="text-[10px] text-zinc-500 tabular-nums shrink-0">
                  {{ childBoard.summary.count }} 个
                  <span class="text-red-400 ml-1">↑{{ childBoard.summary.up_count }}</span>
                  <span class="text-green-400 ml-1">↓{{ childBoard.summary.down_count }}</span>
                </span>
              </div>
              <button @click="clearSectorDrill()" class="text-[10px] text-zinc-500 hover:text-white shrink-0">关闭副图</button>
            </div>
            <div v-if="childLoading" class="h-[360px] flex items-center justify-center text-zinc-500 text-xs">
              <i class="fa-solid fa-spinner animate-spin mr-2"></i>
              加载子板块（首次会推断层级，可能稍慢）...
            </div>
            <div v-else-if="!(childBoard.sectors?.length)" class="h-[200px] flex items-center justify-center text-zinc-500 text-xs px-4 text-center">
              暂无子板块映射。可点击云同步后，或稍后重试（需成分股推断 L1→L2→L3）
            </div>
            <div v-show="!childLoading && childBoard.sectors?.length" ref="sectorChildBubbleChart" class="h-[360px] w-full bg-zinc-950" @contextmenu.prevent></div>
          </div>

          <!-- 成分股列表 -->
          <div v-if="activeSector" class="border-t border-zinc-800">
            <div class="px-4 py-2 flex items-center justify-between bg-zinc-900/60">
              <div class="text-xs text-zinc-300">
                <span class="text-amber-300 font-medium">{{ activeSector.name || activeSector.ts_code }}</span>
                <span class="text-zinc-500 ml-2">{{ activeSector.level || '' }}</span>
                <span class="text-zinc-500 ml-2">成分股 {{ sectorMembers.length }}</span>
                <span v-if="activeSector.pct_change != null" :class="activeSector.pct_change >= 0 ? 'text-red-400' : 'text-green-400'" class="ml-2 tabular-nums">
                  {{ activeSector.pct_change >= 0 ? '+' : '' }}{{ Number(activeSector.pct_change).toFixed(2) }}%
                </span>
              </div>
              <button @click="clearActiveSector()" class="text-[10px] text-zinc-500 hover:text-white">关闭</button>
            </div>
            <div v-if="sectorMembersLoading" class="py-8 text-center text-zinc-500 text-xs">
              <i class="fa-solid fa-spinner animate-spin mr-1"></i>加载成分股...
            </div>
            <div v-else class="max-h-64 overflow-auto">
              <table class="w-full text-xs">
                <thead class="sticky top-0 bg-zinc-900 text-zinc-500">
                  <tr>
                    <th class="text-left px-3 py-2 font-medium">代码</th>
                    <th class="text-left px-3 py-2 font-medium">名称</th>
                    <th class="text-right px-3 py-2 font-medium">现价</th>
                    <th class="text-right px-3 py-2 font-medium">涨跌幅</th>
                    <th class="text-right px-3 py-2 font-medium">成交额</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="m in sectorMembers"
                    :key="m.ts_code"
                    @click="selectStockFromSector(m)"
                    class="border-t border-zinc-800/60 hover:bg-zinc-800/40 cursor-pointer"
                  >
                    <td class="px-3 py-1.5 font-mono text-zinc-400">{{ m.ts_code }}</td>
                    <td class="px-3 py-1.5 text-zinc-200">{{ m.name || '-' }}</td>
                    <td class="px-3 py-1.5 text-right tabular-nums">{{ m.close ?? '-' }}</td>
                    <td class="px-3 py-1.5 text-right tabular-nums" :class="(m.pct_chg ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'">
                      {{ m.pct_chg == null ? '-' : ((m.pct_chg >= 0 ? '+' : '') + Number(m.pct_chg).toFixed(2) + '%') }}
                    </td>
                    <td class="px-3 py-1.5 text-right tabular-nums text-zinc-400">{{ formatAmount(m.amount) }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-if="!sectorMembers.length" class="py-6 text-center text-zinc-500 text-xs">暂无成分股</div>
            </div>
          </div>

          <div class="px-4 py-3 text-[10px] text-zinc-600 text-center border-t border-zinc-800">
            数据来源 PostgreSQL · sector_dc_* · Datahubco 持续同步
            <span class="text-zinc-500"> · 闭市前显示上一交易日，闭市后显示当日</span>
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
import { dashFocus, dashFromScreener, returnToScreener } from './appNav'

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
const loadingHint = ref('加载K线数据...')
const indices = ref<any[]>([])
const sectorBoard = ref<any>({ sectors: [], summary: null })
const sectorLoading = ref(false)
const sectorSyncing = ref(false)
const sectorLevel = ref('东财一级行业')
const sectorLevelTabs = [
  { key: '东财一级行业', label: '一级行业' },
  { key: '东财二级行业', label: '二级行业' },
  { key: '东财三级行业', label: '三级行业' },
]
const sectorBubbleChart = ref<HTMLDivElement>()
const sectorChildBubbleChart = ref<HTMLDivElement>()
const activeSector = ref<any>(null)
const sectorMembers = ref<any[]>([])
const sectorMembersLoading = ref(false)
const drillStack = ref<any[]>([])
const drillParent = computed(() => drillStack.value.length ? drillStack.value[drillStack.value.length - 1] : null)
const childBoard = ref<any>({ sectors: [], summary: null })
const childLoading = ref(false)

let sectorBubbleInst: any = null
let sectorChildBubbleInst: any = null
let etfBubbleInst: any = null // 兼容旧 resize 引用

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

const SEARCH_BARS = 30
const EXTEND_CAL_DAYS = 90
const fetchProgress = ref({ active: false, ts_code: '', percent: 0, label: '' })
const klineBarLimit = ref(SEARCH_BARS)
const historyExhausted = ref(false)
let extendInFlight = false
let pinnedZoom: { start: number; end: number } | null = null
let lastZoom = { start: 0, end: 100 }
let ignoreZoomUntil = 0
let progressTick: any = null
let progressClear: any = null
let extendTimer: any = null

function showFetchProgress(code?: string) {
  return fetchProgress.value.active && (!code || fetchProgress.value.ts_code === code)
}

function setFetchProgress(code: string, percent: number, label: string) {
  fetchProgress.value = {
    active: true,
    ts_code: code,
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    label,
  }
}

function stopProgressTicker() {
  if (progressTick) {
    clearInterval(progressTick)
    progressTick = null
  }
}

function startProgressTicker(code: string, label: string) {
  stopProgressTicker()
  if (progressClear) {
    clearTimeout(progressClear)
    progressClear = null
  }
  let p = 10
  setFetchProgress(code, p, label)
  progressTick = setInterval(() => {
    p = Math.min(p + 6, 68)
    setFetchProgress(code, p, fetchProgress.value.label || label)
  }, 260)
}

function finishFetchProgress(code: string, label: string) {
  stopProgressTicker()
  setFetchProgress(code, 100, label)
  if (progressClear) clearTimeout(progressClear)
  progressClear = setTimeout(() => {
    fetchProgress.value = { ...fetchProgress.value, active: false }
  }, 800)
}

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

async function hydrateWatchlist() {
  try {
    const res = await fetch('/data/selective/watchlist')
    const data = await res.json().catch(() => ({}))
    const items = data.items || []
    if (items.length) {
      watchlist.value = items.map((w: any) => ({
        ts_code: w.ts_code,
        name: w.name,
        short_name: w.short_name || w.name,
        kind: w.kind || 'stock',
        industry: w.industry || '',
        added_at: w.created_at,
      }))
      return
    }
    for (const w of watchlist.value) {
      try {
        await fetch('/data/selective/watchlist', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ts_code: w.ts_code,
            name: w.name,
            short_name: w.short_name,
            kind: w.kind || 'stock',
            industry: w.industry || '',
          }),
        })
      } catch (_) { /* 本地迁移失败不影响展示 */ }
    }
  } catch (e) {
    console.warn('同步自选名单失败', e)
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
  try {
    await fetch('/data/selective/watchlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ts_code: item.ts_code,
        name: item.name || item.short_name || item.ts_code,
        short_name: item.short_name || item.name || item.ts_code,
        kind: item.kind || 'stock',
        industry: item.industry || '',
      }),
    })
  } catch (e) {
    console.warn('登记自选失败', e)
  }
  // 挑选式：个股拉近一季度日线 + 周/月 → PG，并开启持续日更
    if ((item.kind || 'stock') === 'stock') {
      showToast('正在补齐自选行情并开启日更…')
      startProgressTicker(item.ts_code, '补齐自选日线')
      const r = await ensureSelectiveBars(item.ts_code, 'watchlist')
      if (r?.ok) {
        const bar = r.latest_bar || r.verify?.local
        const match = r.verify?.match
        const tip = bar
          ? `已入库 ${bar.trade_date} 收 ${bar.close}` + (match === false ? '（与远端不一致，请复查）' : '')
          : `行情已入库（日线 ${r.upserted?.daily ?? 0} 条）`
        finishFetchProgress(item.ts_code, '日更已开启')
        showToast(tip)
        if (currentTsCode.value === item.ts_code) {
          await loadData()
        }
      } else {
        finishFetchProgress(item.ts_code, '补齐失败')
      }
    }
}

async function removeFromWatchlist(tsCode: string) {
  watchlist.value = watchlist.value.filter(w => w.ts_code !== tsCode)
  try {
    await fetch(`/data/selective/watchlist?ts_code=${encodeURIComponent(tsCode)}`, { method: 'DELETE' })
  } catch (e) {
    console.warn('取消持续获取失败', e)
  }
  showToast('已移出自选，已停止持续获取')
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
    selectETF({
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
    }, { fromWatchlist: true })
  }
}

function onGlobalClick() {
  if (ctxJustOpened) return
  if (ctxMenu.value.visible) closeCtxMenu()
}

const chartData = ref<any>({ bars: [], indicators: {}, count: 0 })

const SUB_CHART_INDICATORS = ['macd', 'rsi', 'kdj']
const ASYNC_INDICATORS = ['ema', 'boll', 'macd', 'rsi', 'kdj']
const CORE_KLINE_INDICATORS = ['ma', 'volume']
const indicatorBusy = ref<string[]>([])
let loadToken = 0
let indicatorPrefetch: Promise<void> | null = null

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

/** 强制重建图表 DOM，避免残留实例导致空白 */
const chartMountKey = ref(0)
const chartRenderError = ref('')
let mainChartObserver: ResizeObserver | null = null

let mainChartInst: any = null
let volumeChartInst: any = null
let macdChartInst: any = null
let rsiChartInst: any = null
let kdjChartInst: any = null
let searchTimer: any = null
let resizeHandler: any = null
let etfRefreshTimer: any = null
let indexRefreshTimer: any = null
let afterCloseTimer: any = null
let intradayTimer: any = null

// ─── 计算属性 ────────────────────────────────────────

const timeframeName = computed(() => {
  const map: Record<string, string> = {
    intraday: '分时', day: '日线', week: '周线', month: '月线',
  }
  return map[timeframe.value] || timeframe.value
})

const MA_COLORS: Record<string, string> = {
  MA5: '#fbbf24',
  MA10: '#60a5fa',
  MA20: '#c084fc',
  MA30: '#fb923c',
}

function lastNumeric(arr: any[] | undefined): number | null {
  if (!Array.isArray(arr) || !arr.length) return null
  for (let i = arr.length - 1; i >= 0; i--) {
    const v = arr[i]
    if (v != null && v !== '' && !Number.isNaN(Number(v))) return Number(v)
  }
  return null
}

const maRows = computed(() => {
  const ma = chartData.value.indicators?.ma || {}
  return ['MA5', 'MA10', 'MA20', 'MA30'].map((key) => ({
    key,
    value: lastNumeric(ma[key]),
    color: MA_COLORS[key],
  }))
})

const quickTimeframes = [
  { id: 'intraday', name: '分时' },
  { id: 'day', name: '日K' },
  { id: 'week', name: '周K' },
  { id: 'month', name: '月K' },
]

function setTimeframe(tf: { id: string; name: string }) {
  if (tf.id === 'intraday' && selectedTab.value !== 'stock') return
  timeframe.value = tf.id
  loadData()
}

const latestBar = computed(() => {
  const bars = chartData.value?.bars || []
  return bars.length ? bars[bars.length - 1] : null
})

const changePercent = computed(() => {
  const bars = chartData.value?.bars || []
  if (!bars.length) return null
  const last = bars[bars.length - 1]
  if (selectedTab.value === 'index' && currentIndex.value?.pct_chg != null) {
    return currentIndex.value.pct_chg.toFixed(2)
  }
  if (selectedTab.value === 'etf' && currentETF.value?.pct_chg != null) {
    return Number(currentETF.value.pct_chg).toFixed(2)
  }
  if (chartData.value.pct_chg != null && !Number.isNaN(Number(chartData.value.pct_chg))) {
    return Number(chartData.value.pct_chg).toFixed(2)
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
  const bars = chartData.value?.bars || []
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
  const bars = chartData.value?.bars || []
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

const hasBars = computed(() => (chartData.value?.bars || []).length > 0)
const showMACD = computed(() => activeIndicators.value.includes('macd'))
const showRSI = computed(() => activeIndicators.value.includes('rsi'))
const showKDJ = computed(() => activeIndicators.value.includes('kdj'))
const hasSubIndicators = computed(() => showMACD.value || showRSI.value || showKDJ.value)

const intradayBadgeText = computed(() => {
  const d = chartData.value as any
  const date = d.trade_date || ''
  const auc = d.auction?.price
  const aucTxt = auc != null && auc !== '' ? ` 竞价${Number(auc).toFixed(2)}` : ''
  if (d.live || d.session_phase === 'live') return `当日实时 ${date}${aucTxt}`
  if (d.session_phase === 'after_close') return `当日已收盘 ${date}${aucTxt}`
  if (d.session_phase === 'before_open') return `开盘前 ${date}`
  if (d.session_phase === 'non_trading') return `上一交易日 ${date}`
  if (d.source === 'redis') return `Redis ${date}${aucTxt}`
  return `分时 ${date}${aucTxt}`
})

const activeEtfMarket = computed(() => null as any)
const activeEtfList = computed(() => [] as any[])

// ─── 行业板块数据加载 ────────────────────────────────────────

function sectorBubbleSize(absPct: number): number {
  const v = Math.abs(absPct || 0)
  return Math.max(12, Math.min(72, 10 + v * 6))
}

/** 将数值映射为 0–100 分位秩（并列取平均秩），疏密区自动拉开 */
function quantileRanks(values: number[]): number[] {
  const n = values.length
  if (n === 0) return []
  if (n === 1) return [50]
  const indexed = values.map((v, i) => ({ v, i }))
  indexed.sort((a, b) => a.v - b.v)
  const ranks = new Array<number>(n)
  let i = 0
  while (i < n) {
    let j = i
    while (j < n && indexed[j].v === indexed[i].v) j++
    const avg = (i + j - 1) / 2
    const pct = (avg / (n - 1)) * 100
    for (let k = i; k < j; k++) ranks[indexed[k].i] = pct
    i = j
  }
  return ranks
}

function valueAtPercentile(sortedAsc: number[], p: number): number {
  if (!sortedAsc.length) return 0
  if (sortedAsc.length === 1) return sortedAsc[0]
  const pos = (Math.min(100, Math.max(0, p)) / 100) * (sortedAsc.length - 1)
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  if (lo === hi) return sortedAsc[lo]
  const t = pos - lo
  return sortedAsc[lo] * (1 - t) + sortedAsc[hi] * t
}

/** 给定真实值，插值其在分位轴上的位置 */
function rankOfRawValue(values: number[], ranks: number[], target: number): number {
  const pairs = values
    .map((v, i) => ({ v, r: ranks[i] }))
    .sort((a, b) => a.v - b.v)
  if (!pairs.length) return 50
  if (target <= pairs[0].v) return pairs[0].r
  if (target >= pairs[pairs.length - 1].v) return pairs[pairs.length - 1].r
  for (let i = 0; i < pairs.length - 1; i++) {
    const a = pairs[i]
    const b = pairs[i + 1]
    if (target >= a.v && target <= b.v) {
      const span = b.v - a.v
      const t = span === 0 ? 0 : (target - a.v) / span
      return a.r * (1 - t) + b.r * t
    }
  }
  return 50
}

function formatAmountTick(v: number): string {
  if (!Number.isFinite(v)) return '-'
  if (Math.abs(v) >= 1000) return `${Math.round(v)}`
  if (Math.abs(v) >= 100) return v.toFixed(0)
  if (Math.abs(v) >= 10) return v.toFixed(1)
  return v.toFixed(2)
}

function formatPctTick(v: number): string {
  if (!Number.isFinite(v)) return '-'
  const s = v >= 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function canDrillSector(sector: any): boolean {
  if (Number(sector?.child_count || 0) > 0) return true
  const lv = String(sector?.level || '')
  return lv.includes('一级') || lv.includes('二级')
}

function buildSectorScatterOption(sectors: any[], title: string, subtext: string) {
  const amounts = sectors.map((e: any) => {
    const yi = e.amount_yi ?? (e.amount ? e.amount / 1e8 : 0)
    return Number.isFinite(yi) ? yi : 0
  })
  const pcts = sectors.map((e: any) => {
    const p = e.pct_change ?? 0
    return Number.isFinite(p) ? p : 0
  })

  const xRanks = quantileRanks(amounts)
  const yRanks = quantileRanks(pcts)
  const sortedAmt = [...amounts].sort((a, b) => a - b)
  const sortedPct = [...pcts].sort((a, b) => a - b)
  const zeroY = rankOfRawValue(pcts, yRanks, 0)

  const pctMin = Math.min(...pcts)
  const pctMax = Math.max(...pcts)
  // 点少全显示；点多靠 hideOverlap，放大后自动露出更多名字
  const fontSize = sectors.length > 200 ? 8 : sectors.length > 80 ? 9 : 10

  const scatterData = sectors.map((e: any, i: number) => {
    const amountYi = amounts[i]
    const pct = pcts[i]
    const absPct = e.abs_pct_change ?? Math.abs(pct)
    const label = e.name || e.ts_code
    return {
      name: label,
      value: [xRanks[i], yRanks[i], pct],
      symbolSize: sectorBubbleSize(absPct),
      sector: e,
      amountYi,
      pct,
      label,
    }
  })

  const axisLabelX = (v: number) => formatAmountTick(valueAtPercentile(sortedAmt, v))
  const axisLabelY = (v: number) => formatPctTick(valueAtPercentile(sortedPct, v))

  return {
    backgroundColor: '#09090b',
    animation: false,
    title: {
      text: title,
      subtext,
      left: 'center',
      top: 4,
      textStyle: { color: '#e4e4e7', fontSize: 13, fontWeight: 600 },
      subtextStyle: { color: '#71717a', fontSize: 10 },
    },
    toolbox: {
      right: 56,
      top: 28,
      itemSize: 12,
      iconStyle: { borderColor: '#71717a' },
      feature: {
        dataZoom: {
          title: { zoom: '框选放大', back: '缩放还原' },
          xAxisIndex: 0,
          yAxisIndex: 0,
        },
        restore: { title: '重置视图' },
      },
    },
    grid: { left: 72, right: 76, top: 58, bottom: 56 },
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => {
        const e = p.data?.sector
        if (!e) return ''
        const pct = e.pct_change ?? 0
        const netYi = e.net_amount_yi
        const childN = Number(e.child_count || 0)
        const lines = [
          `<b>${e.name || e.ts_code}</b>`,
          `代码: ${e.ts_code}`,
          e.level ? `层级: ${e.level}` : '',
          `涨跌幅: ${pct >= 0 ? '+' : ''}${Number(pct).toFixed(2)}%`,
          `成交额: ${e.amount_yi ?? '-'} 亿`,
          `主力净流入: ${netYi == null ? '-' : ((netYi >= 0 ? '+' : '') + netYi + ' 亿')}`,
          `领涨股: ${e.leading || '-'} ${e.leading_pct != null ? ((e.leading_pct >= 0 ? '+' : '') + Number(e.leading_pct).toFixed(2) + '%') : ''}`,
          `涨跌家数: ${e.up_num ?? '-'} / ${e.down_num ?? '-'}`,
          `换手率: ${e.turnover_rate ?? '-'}%`,
          childN > 0
            ? `<span style="color:#fbbf24">子板块: ${childN} 个 · 点击下钻</span>`
            : (canDrillSector(e) ? '<span style="color:#a1a1aa">暂无子板块映射</span>' : ''),
        ]
        return lines.filter(Boolean).join('<br/>')
      },
    },
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: 0,
        filterMode: 'none',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: true,
      },
      {
        type: 'inside',
        yAxisIndex: 0,
        filterMode: 'none',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
        moveOnMouseWheel: false,
        preventDefaultMouseMove: true,
      },
    ],
    xAxis: {
      type: 'value',
      min: 0,
      max: 100,
      scale: false,
      name: '成交额(亿) · 分位轴',
      nameLocation: 'middle',
      nameGap: 34,
      nameTextStyle: { color: '#a1a1aa', fontSize: 10 },
      axisLine: { show: true, lineStyle: { color: '#52525b' } },
      axisTick: { show: true, lineStyle: { color: '#52525b' } },
      axisLabel: {
        show: true,
        color: '#d4d4d8',
        fontSize: 10,
        hideOverlap: true,
        showMinLabel: true,
        showMaxLabel: true,
        // 任意刻度（含缩放后）都按分位反查真实成交额，避免空白
        formatter: axisLabelX,
      },
      splitLine: { show: true, lineStyle: { color: '#27272a' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      scale: false,
      name: '涨跌幅(%) · 分位轴',
      nameTextStyle: { color: '#a1a1aa', fontSize: 10 },
      axisLine: { show: true, lineStyle: { color: '#52525b' } },
      axisTick: { show: true, lineStyle: { color: '#52525b' } },
      axisLabel: {
        show: true,
        color: '#d4d4d8',
        fontSize: 10,
        hideOverlap: true,
        showMinLabel: true,
        showMaxLabel: true,
        formatter: axisLabelY,
      },
      splitLine: { show: true, lineStyle: { color: '#27272a' } },
    },
    visualMap: {
      show: true,
      dimension: 2,
      min: pctMin,
      max: pctMax,
      calculable: false,
      orient: 'vertical',
      right: 8,
      top: 'center',
      itemHeight: 120,
      text: ['涨', '跌'],
      textStyle: { color: '#a1a1aa', fontSize: 9 },
      inRange: { color: ['#22c55e', '#fbbf24', '#ef4444'] },
    },
    series: [{
      type: 'scatter',
      data: scatterData,
      label: {
        show: true,
        formatter: (p: any) => p.data?.name || '',
        fontSize,
        color: '#e4e4e7',
        position: 'top',
        distance: 4,
      },
      labelLayout: {
        hideOverlap: true,
        moveOverlap: 'shiftY',
      },
      emphasis: {
        scale: 1.15,
        focus: 'self',
        label: {
          show: true,
          formatter: (p: any) => p.data?.name || '',
          fontSize: Math.max(fontSize, 11),
          color: '#fff',
        },
      },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#52525b', type: 'dashed' },
        data: [{ yAxis: zeroY }],
        label: {
          formatter: '0%',
          color: '#a1a1aa',
          fontSize: 9,
        },
      },
    }],
  }
}

function bindSectorChartNav(inst: any) {
  if (!inst || inst.__sectorNavBound) return
  inst.__sectorNavBound = true
  inst.getZr().on('dblclick', (e: any) => {
    const ptPixel = [e.offsetX, e.offsetY]
    if (!inst.containPixel({ gridIndex: 0 }, ptPixel)) return
    const pt = inst.convertFromPixel({ gridIndex: 0 }, ptPixel)
    if (!pt || pt.length < 2) return
    const [x, y] = pt
    const zooms = inst.getOption()?.dataZoom || []
    const xz = zooms[0] || {}
    const yz = zooms[1] || {}
    const xStart0 = Number(xz.start ?? 0)
    const xEnd0 = Number(xz.end ?? 100)
    const yStart0 = Number(yz.start ?? 0)
    const yEnd0 = Number(yz.end ?? 100)
    const xSpan = Math.max(8, (xEnd0 - xStart0) / 2)
    const ySpan = Math.max(8, (yEnd0 - yStart0) / 2)
    let xStart = x - xSpan / 2
    let xEnd = x + xSpan / 2
    let yStart = y - ySpan / 2
    let yEnd = y + ySpan / 2
    if (xStart < 0) { xEnd -= xStart; xStart = 0 }
    if (xEnd > 100) { xStart -= xEnd - 100; xEnd = 100 }
    if (yStart < 0) { yEnd -= yStart; yStart = 0 }
    if (yEnd > 100) { yStart -= yEnd - 100; yEnd = 100 }
    xStart = Math.max(0, Math.min(92, xStart))
    yStart = Math.max(0, Math.min(92, yStart))
    xEnd = Math.max(xStart + 8, Math.min(100, xEnd))
    yEnd = Math.max(yStart + 8, Math.min(100, yEnd))
    inst.dispatchAction({
      type: 'dataZoom',
      batch: [
        { dataZoomIndex: 0, start: xStart, end: xEnd },
        { dataZoomIndex: 1, start: yStart, end: yEnd },
      ],
    })
  })
}

function ensureSectorChart(
  el: HTMLDivElement | undefined,
  existing: any,
  onClick: (sector: any) => void,
) {
  if (!el) return existing
  let inst = existing
  if (!inst) {
    inst = echarts.init(el, 'dark')
    inst.on('click', (params: any) => {
      const s = params?.data?.sector
      if (s) onClick(s)
    })
    bindSectorChartNav(inst)
  }
  return inst
}

function renderSectorBubbleChart() {
  const sectors = sectorBoard.value?.sectors || []
  if (!sectorBubbleChart.value || !sectors.length) return

  sectorBubbleInst = ensureSectorChart(sectorBubbleChart.value, sectorBubbleInst, (s) => {
    void onSectorBubbleClick(s, false)
  })
  const opt = buildSectorScatterOption(
    sectors,
    `行业板块气泡图 · ${sectorLevel.value}`,
    `分位疏密 · 缩放/平移/框选 · 共 ${sectors.length} 个 · 点击下钻或看成分`,
  )
  sectorBubbleInst.setOption(opt, true)
  sectorBubbleInst.resize()
}

function renderSectorChildBubbleChart() {
  const sectors = childBoard.value?.sectors || []
  if (!sectorChildBubbleChart.value || !sectors.length) return

  sectorChildBubbleInst = ensureSectorChart(sectorChildBubbleChart.value, sectorChildBubbleInst, (s) => {
    void onSectorBubbleClick(s, true)
  })
  const parentName = drillParent.value?.name || drillParent.value?.ts_code || ''
  const childLevel = sectors[0]?.level || '子板块'
  const opt = buildSectorScatterOption(
    sectors,
    `${parentName} · ${childLevel}`,
    `副图下钻 · 共 ${sectors.length} 个 · 点击可继续下钻或看成分`,
  )
  sectorChildBubbleInst.setOption(opt, true)
  sectorChildBubbleInst.resize()
}

async function loadSectorBoard() {
  sectorLoading.value = true
  try {
    const res = await axios.get('/dash/sector-board', {
      params: { level: sectorLevel.value, auto_sync: true },
    })
    sectorBoard.value = res.data || { sectors: [] }
  } catch (e) {
    console.warn('sector-board 加载失败:', e)
    sectorBoard.value = {
      status_message: '行业板块加载失败，请确认后端已同步',
      sectors: [],
      summary: { count: 0, up_count: 0, down_count: 0, avg_pct_change: 0 },
    }
  } finally {
    sectorLoading.value = false
    await nextTick()
    renderSectorBubbleChart()
  }
}

async function syncAndLoadSectors() {
  sectorSyncing.value = true
  try {
    await axios.post('/data/continuous/sectors/run', { with_members: true, member_limit: 31 })
    // 后台重建层级（不阻塞看板刷新）
    axios.post('/dash/sector-hierarchy/rebuild', null, { params: { sync_members: true } }).catch(() => {})
  } catch (e) {
    console.warn('sector sync failed:', e)
  } finally {
    sectorSyncing.value = false
    await loadSectorBoard()
  }
}

async function openSectorMembers(sector: any) {
  activeSector.value = sector
  sectorMembersLoading.value = true
  sectorMembers.value = []
  try {
    const res = await axios.get(`/dash/sector/${encodeURIComponent(sector.ts_code)}/members`)
    sectorMembers.value = res.data?.members || []
    if (res.data?.name) activeSector.value = { ...sector, name: res.data.name, ...(res.data.sector || {}) }
  } catch (e) {
    console.warn('sector members failed:', e)
  } finally {
    sectorMembersLoading.value = false
  }
}

async function loadChildBoard(parent: any) {
  childLoading.value = true
  try {
    const res = await axios.get(`/dash/sector/${encodeURIComponent(parent.ts_code)}/children`, {
      params: { auto_sync: true },
    })
    childBoard.value = res.data || { sectors: [] }
  } catch (e) {
    console.warn('sector children failed:', e)
    childBoard.value = { sectors: [], summary: { count: 0, up_count: 0, down_count: 0, avg_pct_change: 0 } }
  } finally {
    childLoading.value = false
    await nextTick()
    renderSectorChildBubbleChart()
  }
}

async function onSectorBubbleClick(sector: any, fromChildChart: boolean) {
  await openSectorMembers(sector)
  const childN = Number(sector?.child_count || 0)
  if (!canDrillSector(sector) && childN <= 0) return
  // 三级或明确无子节点时只展示成分股
  if (String(sector?.level || '').includes('三级') && childN <= 0) return

  if (fromChildChart) {
    const parentIdx = drillStack.value.findIndex((n) => n.ts_code === sector.ts_code)
    if (parentIdx >= 0) {
      drillStack.value = drillStack.value.slice(0, parentIdx + 1)
    } else {
      drillStack.value = [...drillStack.value, {
        ts_code: sector.ts_code,
        name: sector.name || sector.ts_code,
        level: sector.level,
        child_count: childN,
      }]
    }
  } else {
    drillStack.value = [{
      ts_code: sector.ts_code,
      name: sector.name || sector.ts_code,
      level: sector.level,
      child_count: childN,
    }]
  }
  await loadChildBoard(sector)
}

async function jumpDrillTo(idx: number) {
  if (idx < 0 || idx >= drillStack.value.length) return
  drillStack.value = drillStack.value.slice(0, idx + 1)
  const node = drillStack.value[idx]
  await openSectorMembers(node)
  if (canDrillSector(node)) {
    await loadChildBoard(node)
  } else {
    childBoard.value = { sectors: [] }
  }
}

function clearSectorDrill() {
  drillStack.value = []
  childBoard.value = { sectors: [], summary: null }
  sectorChildBubbleInst?.clear?.()
}

function clearActiveSector() {
  activeSector.value = null
  sectorMembers.value = []
}

function selectStockFromSector(m: any) {
  selectStock({
    ts_code: m.ts_code,
    name: m.name || m.ts_code,
    symbol: String(m.ts_code || '').split('.')[0],
  })
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function loadEtfBoard() {
  // 兼容旧调用：改为加载行业板块
  await loadSectorBoard()
}

function setupEtfAutoRefresh() {
  /* no-op: 行业板块按收盘 asof，不需要盘中 60s 刷 ETF */
}

function renderEtfBubbleChart() {
  renderSectorBubbleChart()
}

async function loadIndices(autoSync = true) {
  try {
    // 先快速展示库内快照，避免 auto_sync 阻塞顶部指数条
    if (autoSync) {
      try {
        const fast = await axios.get('/dash/indices', { params: { auto_sync: false }, timeout: 8000 })
        if (fast.data?.indices?.length) {
          indices.value = fast.data.indices
          if (currentIndex.value) {
            const updated = indices.value.find((i: any) => i.ts_code === currentIndex.value.ts_code)
            if (updated) currentIndex.value = { ...currentIndex.value, ...updated }
          }
        }
      } catch { /* 继续完整同步 */ }
    }
    const res = await axios.get('/dash/indices', {
      params: { auto_sync: autoSync },
      timeout: autoSync ? 60000 : 8000,
    })
    indices.value = res.data.indices || []
    if (currentIndex.value) {
      const updated = indices.value.find((i: any) => i.ts_code === currentIndex.value.ts_code)
      if (updated) currentIndex.value = { ...currentIndex.value, ...updated }
    }
    // 仅在「指数 Tab 且用户尚未选中个股/ETF」时默认上证；搜索个股后不要抢回
    const viewingStockOrEtf = selectedTab.value === 'stock' || selectedTab.value === 'etf'
    if (indices.value.length > 0 && !currentIndex.value && !viewingStockOrEtf && !currentStock.value && !currentETF.value) {
      currentIndex.value = indices.value[0]
      currentTsCode.value = indices.value[0].ts_code
      loadData()
    }
  } catch (e) {
    console.warn('获取指数列表失败:', e)
  }
}

function setupIndexAutoRefresh() {
  if (indexRefreshTimer) clearInterval(indexRefreshTimer)
  indexRefreshTimer = setInterval(() => {
    loadIndices(false)
  }, 60_000)
}

function shanghaiNow(): Date {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date())
  const get = (type: string) => parts.find((p) => p.type === type)?.value || '00'
  const iso = `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:00+08:00`
  const dt = new Date(iso)
  return Number.isNaN(dt.getTime()) ? new Date() : dt
}

function setupAfterCloseCatchup() {
  if (afterCloseTimer) {
    clearInterval(afterCloseTimer)
    afterCloseTimer = null
  }
  const tick = () => {
    if (!props.active) return
    if (loading.value) return
    const now = shanghaiNow()
    const hhmm = now.getHours() * 100 + now.getMinutes()
    const weekday = now.getDay()
    if (weekday === 0 || weekday === 6) return
    if (!Number.isFinite(hhmm) || hhmm < 1500 || hhmm > 1800) return
    loadIndices(true)
    if (currentTsCode.value) void loadData({ silent: true, background: true })
  }
  tick()
  afterCloseTimer = setInterval(tick, 60_000)
}

function waitForChartLayout(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  })
}

/** 确保容器上有可用的 ECharts 实例（先清残留，再新建） */
function initChart(el: HTMLElement | undefined, inst: any): any {
  if (!el) return null
  try {
    const existing = echarts.getInstanceByDom(el)
    if (existing) {
      existing.dispose()
    }
  } catch { /* ignore */ }
  if (inst) {
    try { inst.dispose() } catch { /* ignore */ }
  }
  // 清空可能残留的空 canvas，保证干净挂载
  try { el.innerHTML = '' } catch { /* ignore */ }
  const chart = echarts.init(el, undefined, {
    renderer: 'canvas',
    width: Math.max(el.clientWidth || 0, 320),
    height: Math.max(el.clientHeight || 0, 120),
  })
  return chart
}

function forceChartResize(inst: any, el?: HTMLElement | null) {
  if (!inst) return
  try {
    const w = el?.clientWidth || 0
    const h = el?.clientHeight || 0
    if (w > 8 && h > 8) inst.resize({ width: w, height: h })
    else inst.resize()
  } catch (e) {
    console.warn('chart resize failed:', e)
  }
}

function bindMainChartObserver() {
  if (typeof ResizeObserver === 'undefined') return
  try { mainChartObserver?.disconnect() } catch { /* ignore */ }
  mainChartObserver = new ResizeObserver(() => {
    forceChartResize(mainChartInst, mainChart.value)
    forceChartResize(volumeChartInst, volumeChart.value)
  })
  if (mainChart.value) mainChartObserver.observe(mainChart.value)
}

async function renderAllCharts() {
  chartRenderError.value = ''
  await nextTick()
  await waitForChartLayout()
  try {
    // key 变更后需等新 DOM 就绪
    if (!mainChart.value) {
      await nextTick()
      await waitForChartLayout()
    }
    renderMainChart()
    if (hasBars.value) renderVolumeChart()
    await renderSubCharts()
    bindMainChartObserver()
    await nextTick()
    handleResize()
    window.setTimeout(() => handleResize(), 30)
    window.setTimeout(() => handleResize(), 120)
    window.setTimeout(() => handleResize(), 360)
  } catch (e: any) {
    console.error('渲染K线失败:', e)
    chartRenderError.value = e?.message || String(e)
  }
}

async function retryRenderCharts() {
  disposeKlineCharts()
  chartMountKey.value += 1
  await nextTick()
  await renderAllCharts()
}

function applyChartPayload(data: any) {
  const bars = Array.isArray(data?.bars) ? data.bars : []
  chartData.value = {
    ...(data || {}),
    bars,
    indicators: data?.indicators || {},
    count: data?.count ?? bars.length,
  }
}

async function loadData(opts: { silent?: boolean; background?: boolean } = {}) {
  if (!currentTsCode.value) return
  if (opts.background && loading.value) return
  const token = ++loadToken
  const code = currentTsCode.value
  const tf = timeframe.value
  const silent = !!opts.silent && !!(chartData.value.bars || []).length
  if (!silent) loading.value = true
  const isIntraday = selectedTab.value === 'stock' && tf === 'intraday'
  const isStockDay = selectedTab.value === 'stock' && tf === 'day'
  if (isIntraday) {
    loadingHint.value = '加载当日实时分时（盘中随行情刷新）...'
  } else if (isStockDay) {
    loadingHint.value = `从行情源拉取近 ${klineBarLimit.value} 日并写入 PostgreSQL...`
  } else if (selectedTab.value === 'stock') {
    loadingHint.value = '挑选式加载行情与均线...'
  } else {
    loadingHint.value = '加载K线数据...'
  }
  try {
    if (isIntraday) {
      if (!silent) startProgressTicker(code, '拉取当日分时')
      const res = await axios.get('/dash/intraday', { params: { ts_code: code }, timeout: 25000 })
      if (token !== loadToken || currentTsCode.value !== code || timeframe.value !== tf) return
      applyChartPayload(res.data)
      const barCount = res.data.count || res.data.bars?.length || 0
      const src = res.data.live ? '实时' : (res.data.source === 'redis' ? 'Redis' : 'Timescale')
      if (!silent) finishFetchProgress(code, `${src} ${barCount} 根`)
      try {
        await renderAllCharts()
        await waitForChartLayout()
        handleResize()
      } catch (renderErr) {
        console.error('渲染分时失败:', renderErr)
      }
      return
    }
    if (isStockDay) {
      startProgressTicker(code, `拉取近 ${klineBarLimit.value} 日`)
      const ens = await axios.post('/data/selective/ensure-recent', {
        ts_code: code,
        trading_days: klineBarLimit.value,
      }, { timeout: 45000 })
      if (token !== loadToken || currentTsCode.value !== code) return
      const upserted = Number(ens.data?.upserted?.daily ?? ens.data?.fetched?.daily ?? 0)
      const skipped = !!ens.data?.skipped
      loadingHint.value = skipped
        ? '近30日已在 PostgreSQL，正在读取 K 线...'
        : `已写入 PostgreSQL ${upserted} 条日线，正在读取 K 线...`
      stopProgressTicker()
      setFetchProgress(code, 78, skipped ? '读取已入库 K 线' : `已入库 ${upserted} 条`)
    }
    const params: any = {
      ts_code: code,
      timeframe: tf,
      limit: isStockDay ? Math.max(klineBarLimit.value + 5, SEARCH_BARS) : 500,
      indicators: CORE_KLINE_INDICATORS.join(','),
      auto_fill: !isStockDay,
    }

    const res = await axios.get('/dash/kline', { params, timeout: 30000 })
    if (token !== loadToken || currentTsCode.value !== code || timeframe.value !== tf) return
    applyChartPayload(res.data)
    const barCount = res.data.count || res.data.bars?.length || 0
    if (isStockDay) finishFetchProgress(code, `已加载 ${barCount} 日`)
    try {
      await renderAllCharts()
      await waitForChartLayout()
      handleResize()
    } catch (renderErr) {
      console.error('渲染K线失败:', renderErr)
    }
    prefetchAsyncIndicators(token, code, tf)
  } catch (e: any) {
    if (token !== loadToken) return
    console.error('加载K线失败:', e)
    const detail = e?.response?.data?.detail || e?.message || '请检查后端是否启动'
    chartData.value = { bars: [], indicators: {}, count: 0, message: `加载K线失败：${detail}` }
    if (isStockDay || isIntraday) finishFetchProgress(code, '加载失败')
  } finally {
    stopProgressTicker()
    if (token === loadToken) loading.value = false
  }
}

function prefetchAsyncIndicators(token: number, code: string, tf: string) {
  if (tf === 'intraday') return
  indicatorPrefetch = (async () => {
    try {
      const res = await axios.get('/dash/kline', {
        params: {
          ts_code: code,
          timeframe: tf,
          limit: selectedTab.value === 'stock' ? Math.max(klineBarLimit.value + 5, SEARCH_BARS) : 500,
          indicators: ASYNC_INDICATORS.join(','),
          auto_fill: selectedTab.value !== 'stock',
        },
      })
      if (token !== loadToken || currentTsCode.value !== code || timeframe.value !== tf) return
      const prev = chartData.value.indicators || {}
      chartData.value = {
        ...chartData.value,
        indicators: { ...prev, ...(res.data.indicators || {}) },
      }
      if (activeIndicators.value.some((id) => ASYNC_INDICATORS.includes(id))) {
        await renderAllCharts()
      }
    } catch (e) {
      console.warn('异步指标加载失败:', e)
    } finally {
      if (token === loadToken) indicatorPrefetch = null
    }
  })()
}

function scheduleLoadOlderHistory() {
  if (extendTimer) clearTimeout(extendTimer)
  extendTimer = setTimeout(() => { void loadOlderHistory() }, 280)
}

async function loadOlderHistory() {
  if (selectedTab.value !== 'stock' || timeframe.value !== 'day') return
  if (extendInFlight || historyExhausted.value) return
  const code = currentTsCode.value
  if (!code) return
  const bars = chartData.value.bars || []
  if (!bars.length) return
  extendInFlight = true
  const token = loadToken
  const oldLen = bars.length
  const z = lastZoom || { start: 0, end: 100 }
  startProgressTicker(code, '向前补历史')
  try {
    let remote = false
    try {
      const metaRes = await fetch(`/data/selective/meta?ts_code=${encodeURIComponent(code)}&freq=daily`)
      const meta = await metaRes.json().catch(() => ({}))
      const dbCount = Number(meta.count || 0)
      if (dbCount > oldLen + 2) {
        klineBarLimit.value = Math.min(2000, Math.min(dbCount, oldLen + 80))
      } else {
        remote = true
        await fetch('/data/selective/ensure', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ts_code: code, mode: 'extend', extend_days: EXTEND_CAL_DAYS }),
        })
        klineBarLimit.value = Math.min(2000, Math.max(klineBarLimit.value + 80, oldLen + 60))
      }
    } catch {
      remote = true
      await fetch('/data/selective/ensure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ts_code: code, mode: 'extend', extend_days: EXTEND_CAL_DAYS }),
      })
      klineBarLimit.value = Math.min(2000, Math.max(klineBarLimit.value + 80, oldLen + 60))
    }
    if (token !== loadToken || currentTsCode.value !== code) return
    stopProgressTicker()
    setFetchProgress(code, 80, remote ? '写入并读取历史' : '展开已入库历史')
    const kres = await axios.get('/dash/kline', {
      params: {
        ts_code: code,
        timeframe: 'day',
        limit: klineBarLimit.value,
        indicators: CORE_KLINE_INDICATORS.join(','),
        auto_fill: false,
      },
    })
    if (token !== loadToken || currentTsCode.value !== code) return
    const newBars = kres.data.bars || []
    const newLen = newBars.length
    if (newLen <= oldLen) {
      historyExhausted.value = true
      finishFetchProgress(code, '已无更早数据')
      return
    }
    const span = (z.end ?? 100) - (z.start ?? 0)
    if (span >= 90) {
      pinnedZoom = { start: 0, end: 100 }
    } else {
      const added = newLen - oldLen
      pinnedZoom = {
        start: Math.max(0, ((Number(z.start) / 100) * oldLen + added) / newLen * 100),
        end: Math.min(100, ((Number(z.end) / 100) * oldLen + added) / newLen * 100),
      }
    }
    chartData.value = kres.data
    finishFetchProgress(code, `已补至 ${newLen} 日`)
    ignoreZoomUntil = Date.now() + 1600
    await renderAllCharts()
    prefetchAsyncIndicators(token, code, 'day')
  } catch (e) {
    console.warn('向前补历史失败', e)
    finishFetchProgress(code, '补历史失败')
  } finally {
    stopProgressTicker()
    extendInFlight = false
  }
}

function bindKlineHistoryZoom(inst: any) {
  if (!inst || inst.__histZoomBound) return
  inst.__histZoomBound = true
  inst.on('datazoom', (params: any) => {
    const batch = params.batch || [params]
    const z = batch[0] || {}
    if (z.start != null) {
      lastZoom = { start: Number(z.start), end: Number(z.end ?? 100) }
    }
    if (Date.now() < ignoreZoomUntil) return
    if (selectedTab.value !== 'stock' || timeframe.value !== 'day') return
    const start = Number(z.start ?? lastZoom.start ?? 0)
    if (start <= 2) {
      scheduleLoadOlderHistory()
    }
  })
  const zr = inst.getZr?.()
  if (zr && !inst.__histWheelBound) {
    inst.__histWheelBound = true
    zr.on('mousewheel', (e: any) => {
      if (Date.now() < ignoreZoomUntil) return
      if (selectedTab.value !== 'stock' || timeframe.value !== 'day') return
      const span = (lastZoom.end ?? 100) - (lastZoom.start ?? 0)
      const delta = e.wheelDelta ?? 0
      if (span >= 95 && delta < 0) {
        scheduleLoadOlderHistory()
      }
    })
  }
}

function disposeKlineCharts() {
  for (const inst of [mainChartInst, volumeChartInst, macdChartInst, rsiChartInst, kdjChartInst]) {
    try { inst?.dispose() } catch { /* ignore */ }
  }
  for (const el of [mainChart.value, volumeChart.value, macdChart.value, rsiChart.value, kdjChart.value]) {
    if (!el) continue
    try {
      const existing = echarts.getInstanceByDom(el)
      existing?.dispose()
    } catch { /* ignore */ }
    try { el.innerHTML = '' } catch { /* ignore */ }
  }
  mainChartInst = null
  volumeChartInst = null
  macdChartInst = null
  rsiChartInst = null
  kdjChartInst = null
}

function beginInstrumentSwitch() {
  loadToken += 1
  indicatorPrefetch = null
  chartData.value = { bars: [], indicators: {}, count: 0 }
  chartRenderError.value = ''
  loading.value = true
  disposeKlineCharts()
  chartMountKey.value += 1
}

function selectIndex(idx: any) {
  selectedTab.value = 'index'
  currentIndex.value = idx
  currentStock.value = null
  currentETF.value = null
  currentTsCode.value = idx.ts_code
  searchText.value = ''
  timeframe.value = 'day'
  beginInstrumentSwitch()
  loadData()
}

function selectStock(stock: any, opts: { fromWatchlist?: boolean } = {}) {
  selectedTab.value = 'stock'
  currentStock.value = stock
  currentIndex.value = null
  currentETF.value = null
  currentTsCode.value = stock.ts_code
  searchText.value = stock.name
  showDropdown.value = false
  searchResults.value = []
  if (timeframe.value !== 'intraday') timeframe.value = 'day'
  klineBarLimit.value = opts.fromWatchlist ? 100 : SEARCH_BARS
  historyExhausted.value = false
  pinnedZoom = null
  lastZoom = { start: 0, end: 100 }
  beginInstrumentSwitch()
  loadData()
}

function backToScreener() {
  returnToScreener()
}

let lastDashNonce = 0

function applyDashFocus(p: { ts_code?: string; name?: string; industry?: string; nonce?: number } | null) {
  const code = String(p?.ts_code || '').trim()
  if (!code) return
  if (p?.nonce && p.nonce === lastDashNonce) return
  lastDashNonce = p?.nonce || Date.now()
  selectStock({
    ts_code: code,
    name: p?.name || code,
    industry: p?.industry || '',
  })
}

// 等看板真正显示后再拉数/渲染，避免 v-show 隐藏时 ECharts 绑到 0 尺寸或已销毁的 DOM
watch(
  () => [props.active, dashFocus.value] as const,
  ([visible, p]) => {
    if (!visible || !p?.ts_code) return
    applyDashFocus(p)
  },
  { flush: 'post' },
)

function selectETF(etf: any) {
  selectedTab.value = 'etf'
  currentETF.value = etf
  currentIndex.value = null
  currentStock.value = null
  currentTsCode.value = etf.ts_code
  searchText.value = ''
  timeframe.value = 'day'
  beginInstrumentSwitch()
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

function markIndicatorBusy(id: string, on: boolean) {
  if (on) {
    if (!indicatorBusy.value.includes(id)) indicatorBusy.value = [...indicatorBusy.value, id]
  } else {
    indicatorBusy.value = indicatorBusy.value.filter((x) => x !== id)
  }
}

async function ensureIndicatorData(id: string) {
  if (indicatorDataReady(id)) return true
  if (indicatorPrefetch) {
    markIndicatorBusy(id, true)
    try {
      await indicatorPrefetch
    } finally {
      markIndicatorBusy(id, false)
    }
  }
  if (indicatorDataReady(id)) return true

  markIndicatorBusy(id, true)
  const token = loadToken
  const code = currentTsCode.value
  const tf = timeframe.value
  try {
    const res = await axios.get('/dash/kline', {
      params: {
        ts_code: code,
        timeframe: tf,
        limit: selectedTab.value === 'stock' ? Math.max(klineBarLimit.value + 5, SEARCH_BARS) : 500,
        indicators: id,
        auto_fill: selectedTab.value !== 'stock',
      },
    })
    if (token !== loadToken || currentTsCode.value !== code) return false
    const prev = chartData.value.indicators || {}
    chartData.value = {
      ...chartData.value,
      indicators: { ...prev, ...(res.data.indicators || {}) },
    }
    return indicatorDataReady(id)
  } catch (e) {
    console.warn(`指标 ${id} 加载失败:`, e)
    return false
  } finally {
    markIndicatorBusy(id, false)
  }
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
  if (ASYNC_INDICATORS.includes(id) && !indicatorDataReady(id)) {
    await ensureIndicatorData(id)
  }
  if (SUB_CHART_INDICATORS.includes(id)) {
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
  const charts = [mainChartInst, volumeChartInst, macdChartInst, rsiChartInst, kdjChartInst].filter(Boolean)
  for (const c of charts) {
    try { c.group = 'dash-kline' } catch { /* ignore */ }
  }
  if (charts.length >= 2) {
    try { echarts.connect('dash-kline') } catch (e) {
      console.warn('echarts.connect failed:', e)
    }
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
    ma30: '#fb923c',
    grid: '#27272a',
  }
}

// ─── 共享的 dataZoom 索引（所有图表联动缩放） ──────
let sharedDataZoomId = 0

function makeDataZoom(dataLen: number, showDetail: boolean = true) {
  const id = ++sharedDataZoomId
  ignoreZoomUntil = Date.now() + 450
  // 根据数据量动态决定初始显示范围
  let end = 100
  let start: number
  if (pinnedZoom) {
    start = pinnedZoom.start
    end = pinnedZoom.end
  } else if (dataLen <= 80) {
    start = 0
    end = 100
  } else if (dataLen <= 250) {
    start = Math.max(0, Math.round(100 - 120 * 100 / dataLen))
  } else {
    start = Math.max(0, Math.round(100 - 150 * 100 / dataLen))
  }
  lastZoom = { start, end }

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

function barAxisLabel(b: any): string {
  const t = String(b.time || '')
  if (timeframe.value === 'intraday' || chartData.value.chart_type === 'intraday') {
    const m = t.match(/(\d{2}:\d{2})/)
    return m ? m[1] : t.slice(11, 16)
  }
  return t.substring(0, 10)
}

function isIntradayChart(): boolean {
  return timeframe.value === 'intraday' || chartData.value?.chart_type === 'intraday'
}

function isAuctionBar(b: any): boolean {
  if (b?.phase === 'auction' || b?.freq === 'auction') return true
  const t = String(b?.time || '')
  const m = t.match(/(\d{2}):(\d{2})/)
  if (!m) return false
  const hhmm = Number(m[1]) * 100 + Number(m[2])
  return hhmm >= 915 && hhmm <= 925
}

function volumeBarColor(b: any, i: number, bars: any[], c: ReturnType<typeof getThemeColors>): string {
  if (isIntradayChart()) {
    const side = Number(b?.side)
    if (side > 0) return c.up
    if (side < 0) return c.down
    const close = Number(b?.close)
    const prev = i > 0 ? Number(bars[i - 1]?.close) : Number((chartData.value as any).pre_close)
    if (Number.isFinite(close) && Number.isFinite(prev)) {
      if (close > prev) return c.up
      if (close < prev) return c.down
    }
    return '#71717a'
  }
  return Number(b.close) >= Number(b.open) ? c.up : c.down
}

function renderIntradayMainChart() {
  if (!mainChart.value) return
  const c = getThemeColors()
  mainChartInst = initChart(mainChart.value, mainChartInst)
  if (!mainChartInst) return

  const bars = chartData.value.bars || []
  const dates = bars.map((b: any) => barAxisLabel(b))
  const closes = bars.map((b: any) => b.close)
  const avgs = bars.map((b: any) => (isAuctionBar(b) ? null : b.avg_price))
  const auctionCloses = bars.map((b: any) => (isAuctionBar(b) ? b.close : null))
  const sessionCloses = bars.map((b: any) => (isAuctionBar(b) ? null : b.close))
  const pre = Number(chartData.value.pre_close)
  const last = closes.length ? Number(closes[closes.length - 1]) : pre
  const up = last >= pre
  const lineColor = up ? c.up : c.down
  const auctionIdx = bars.reduce((acc: number, b: any, i: number) => (isAuctionBar(b) ? i : acc), -1)
  const markArea = auctionIdx >= 0
    ? {
        silent: true,
        itemStyle: { color: 'rgba(251, 191, 36, 0.08)' },
        data: [
          [
            { xAxis: dates[0], name: '集合竞价' },
            { xAxis: dates[auctionIdx] },
          ],
        ],
        label: { color: '#fbbf24', fontSize: 10, position: 'insideTopLeft' },
      }
    : undefined

  mainChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    legend: {
      data: ['集合竞价', '分时', '均价'],
      textStyle: { color: c.text, fontSize: 10 },
      top: 0,
      left: 8,
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: { left: '8%', right: '3%', top: 28, bottom: 8 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: c.border } },
      axisLabel: {
        color: c.text,
        fontSize: 10,
        hideOverlap: true,
        formatter: (v: string) => (
          ['09:15', '09:25', '09:30', '10:00', '11:30', '13:00', '14:00', '15:00'].includes(v) ? v : ''
        ),
      },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { lineStyle: { color: c.border } },
      axisLabel: { color: c.text, fontSize: 10, formatter: (v: number) => v.toFixed(2) },
      splitLine: { lineStyle: { color: c.grid } },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any) => {
        const list = Array.isArray(params) ? params : [params]
        const idx = list[0]?.dataIndex ?? 0
        const bar = bars[idx]
        const t = list[0]?.axisValue || ''
        const lines = [bar && isAuctionBar(bar) ? `${t} 集合竞价` : `${t}`]
        for (const p of list) {
          if (p?.data == null) continue
          lines.push(`${p.marker}${p.seriesName} ${Number(p.data).toFixed(2)}`)
        }
        return lines.join('<br/>')
      },
    },
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
    series: [
      {
        name: '集合竞价',
        type: 'line',
        data: auctionCloses,
        symbol: 'none',
        connectNulls: false,
        lineStyle: { width: 1.4, color: '#fbbf24' },
        itemStyle: { color: '#fbbf24' },
        markArea,
        markLine: Number.isFinite(pre)
          ? {
              silent: true,
              symbol: 'none',
              lineStyle: { color: '#71717a', type: 'dashed', width: 1 },
              data: [{ yAxis: pre, label: { formatter: '昨收', color: '#a1a1aa', fontSize: 10 } }],
            }
          : undefined,
      },
      {
        name: '分时',
        type: 'line',
        data: sessionCloses,
        symbol: 'none',
        connectNulls: false,
        lineStyle: { width: 1.5, color: lineColor },
        itemStyle: { color: lineColor },
      },
      {
        name: '均价',
        type: 'line',
        data: avgs,
        symbol: 'none',
        lineStyle: { width: 1.2, color: '#38bdf8' },
        itemStyle: { color: '#38bdf8' },
      },
    ],
  }, { notMerge: true })
  forceChartResize(mainChartInst, mainChart.value)
  connectChartGroup()
}

function renderMainChart() {
  if (isIntradayChart()) {
    renderIntradayMainChart()
    return
  }
  if (!mainChart.value) {
    throw new Error('主图容器未就绪')
  }
  const c = getThemeColors()

  mainChartInst = initChart(mainChart.value, mainChartInst)
  if (!mainChartInst) throw new Error('主图 ECharts 初始化失败')

  const bars = chartData.value.bars || []
  const barCount = bars.length
  if (!barCount) return

  const dates = bars.map((b: any) => String(b.time || '').substring(0, 10))
  const ohlc = bars.map((b: any) => [
    Number(b.open),
    Number(b.close),
    Number(b.low),
    Number(b.high),
  ])

  const series: any[] = [
    {
      name: 'K线',
      type: 'candlestick',
      data: ohlc,
      // 关闭 progressive：大数据 + null 均线时部分环境会整图空白
      progressive: 0,
      itemStyle: {
        color: c.up,
        color0: c.down,
        borderColor: c.up,
        borderColor0: c.down,
      },
    },
  ]

  // MA — 与 K 线等长，保留 null 以免均线错位；勿开 large
  const ind = chartData.value.indicators || {}
  if (ind.ma && activeIndicators.value.includes('ma')) {
    const maColors: Record<string, string> = {
      MA5: c.ma5, MA10: c.ma10, MA20: c.ma20, MA30: c.ma30,
    }
    for (const [key, values] of Object.entries(ind.ma)) {
      const arr = Array.isArray(values) ? values : []
      series.push({
        name: key,
        type: 'line',
        data: arr.map((v: any) => (v == null || v === '' ? null : Number(v))),
        smooth: false,
        symbol: 'none',
        connectNulls: false,
        lineStyle: { width: 1.5, color: (maColors as any)[key] || '#888' },
      })
    }
  }

  // EMA
  if (ind.ema && activeIndicators.value.includes('ema')) {
    for (const [key, values] of Object.entries(ind.ema)) {
      const arr = Array.isArray(values) ? values : []
      series.push({
        name: key,
        type: 'line',
        data: arr.map((v: any) => (v == null || v === '' ? null : Number(v))),
        smooth: false,
        symbol: 'none',
        connectNulls: false,
        lineStyle: { width: 1, type: 'dashed' },
      })
    }
  }

  // BOLL
  if (ind.boll_BOLL_UPPER && activeIndicators.value.includes('boll')) {
    const mapN = (arr: any) => (Array.isArray(arr) ? arr.map((v: any) => (v == null || v === '' ? null : Number(v))) : [])
    series.push(
      { name: 'BOLL上轨', type: 'line', data: mapN(ind.boll_BOLL_UPPER), smooth: false, symbol: 'none', connectNulls: false,
        lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
      { name: 'BOLL中轨', type: 'line', data: mapN(ind.boll_BOLL_MID), smooth: false, symbol: 'none', connectNulls: false,
        lineStyle: { width: 1.5, color: '#818cf8' } },
      { name: 'BOLL下轨', type: 'line', data: mapN(ind.boll_BOLL_LOWER), smooth: false, symbol: 'none', connectNulls: false,
        lineStyle: { width: 1, color: '#6366f1', type: 'dashed' } },
    )
  }

  const overlayLegend = series.filter((s) => s.type === 'line').map((s) => s.name)
  const hasSubChart = hasBars.value || showMACD.value || showRSI.value || showKDJ.value
  mainChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    legend: overlayLegend.length
      ? { data: overlayLegend, textStyle: { color: c.text, fontSize: 10 }, top: 0, left: 8, itemWidth: 12, itemHeight: 8 }
      : { show: false },
    grid: { left: 56, right: 16, top: overlayLegend.length ? 28 : 16, bottom: hasSubChart ? 8 : 36 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: true,
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
  }, { notMerge: true, lazyUpdate: false })
  bindKlineHistoryZoom(mainChartInst)
  forceChartResize(mainChartInst, mainChart.value)
}

function renderVolumeChart() {
  if (!volumeChart.value) return
  const c = getThemeColors()

  volumeChartInst = initChart(volumeChart.value, volumeChartInst)
  if (!volumeChartInst) return

  const bars = chartData.value.bars || []
  const barCount = bars.length
  const dates = bars.map((b: any) => barAxisLabel(b))
  const volumes = bars.map((b: any) => b.vol)
  const colors = bars.map((b: any, i: number) => volumeBarColor(b, i, bars, c))

  const series: any[] = [{
    name: '成交量',
    type: 'bar',
    progressive: 0,
    data: volumes.map((v: number, i: number) => ({
      value: Number(v) || 0,
      itemStyle: { color: colors[i] },
    })),
  }]

  const ind = chartData.value.indicators
  if (ind.VOL_MA5) {
    series.push({
      name: 'VOL_MA5',
      type: 'line',
      data: ind.VOL_MA5 as any[],
      smooth: false,
      symbol: 'none',
      connectNulls: false,
      large: barCount > 200,
      largeThreshold: 200,
      lineStyle: { width: 1, color: c.ma5 },
    })
  }
  if (ind.VOL_MA10) {
    series.push({
      name: 'VOL_MA10',
      type: 'line',
      data: ind.VOL_MA10 as any[],
      smooth: false,
      symbol: 'none',
      connectNulls: false,
      large: barCount > 200,
      largeThreshold: 200,
      lineStyle: { width: 1, color: c.ma10 },
    })
  }

  volumeChartInst.setOption({
    backgroundColor: c.bg,
    animation: false,
    grid: { left: 56, right: 16, top: 8, bottom: 8 },
    xAxis: { type: 'category', data: dates, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    yAxis: {
      type: 'value',
      axisLabel: { color: c.text, fontSize: 9, formatter: (v: number) => v >= 100000000 ? (v / 100000000).toFixed(1) + '亿' : v >= 10000 ? (v / 10000).toFixed(0) + '万' : v },
      splitLine: { lineStyle: { color: c.grid } },
    },
    dataZoom: makeDataZoom(barCount, false),
    series,
  }, { notMerge: true })
  forceChartResize(volumeChartInst, volumeChart.value)
  connectChartGroup()
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
  forceChartResize(mainChartInst, mainChart.value)
  forceChartResize(volumeChartInst, volumeChart.value)
  forceChartResize(macdChartInst, macdChart.value)
  forceChartResize(rsiChartInst, rsiChart.value)
  forceChartResize(kdjChartInst, kdjChart.value)
  forceChartResize(etfBubbleInst)
  forceChartResize(sectorBubbleInst, sectorBubbleChart.value)
  forceChartResize(sectorChildBubbleInst, sectorChildBubbleChart.value)
}

// ─── 生命周期 ────────────────────────────────────────

watch([showMACD, showRSI, showKDJ], async () => {
  if (!hasBars.value) return
  await renderSubCharts()
})

watch(sectorLevel, async () => {
  /* loadSectorBoard 在按钮点击里已调用 */
})

// Tab 从隐藏切回显示时，容器刚恢复尺寸，ECharts 需要 resize，否则会空白或卡顿感
watch(
  () => props.active,
  async (visible) => {
    if (!visible) return
    await nextTick()
    await waitForChartLayout()
    handleResize()
    if (hasBars.value) {
      try {
        await renderAllCharts()
        await waitForChartLayout()
        handleResize()
      } catch (e) {
        console.error('看板切回渲染失败:', e)
      }
    }
    if (sectorBoard.value?.sectors?.length) {
      renderSectorBubbleChart()
    }
  }
)

function setupIntradayPoll() {
  if (intradayTimer) {
    clearInterval(intradayTimer)
    intradayTimer = null
  }
  if (!props.active) return
  if (timeframe.value !== 'intraday' || selectedTab.value !== 'stock' || !currentTsCode.value) return
  if (!(chartData.value as any).live) return
  intradayTimer = setInterval(() => {
    void loadData({ silent: true })
  }, 15_000)
}

watch(
  () => [props.active, timeframe.value, selectedTab.value, currentTsCode.value, (chartData.value as any).live],
  () => setupIntradayPoll(),
)

onMounted(() => {
  loadIndices(true)
  setupIndexAutoRefresh()
  setupAfterCloseCatchup()
  loadSectorBoard()
  hydrateWatchlist()
  resizeHandler = () => handleResize()
  window.addEventListener('resize', resizeHandler)
  document.addEventListener('click', onGlobalClick)
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  clearTimeout(toastTimer)
  stopProgressTicker()
  if (progressClear) clearTimeout(progressClear)
  if (extendTimer) clearTimeout(extendTimer)
  if (etfRefreshTimer) clearInterval(etfRefreshTimer)
  if (indexRefreshTimer) clearInterval(indexRefreshTimer)
  if (afterCloseTimer) clearInterval(afterCloseTimer)
  if (intradayTimer) clearInterval(intradayTimer)
  try { mainChartObserver?.disconnect() } catch { /* ignore */ }
  mainChartObserver = null
  window.removeEventListener('resize', resizeHandler)
  document.removeEventListener('click', onGlobalClick)
  disposeKlineCharts()
  etfBubbleInst?.dispose()
  sectorBubbleInst?.dispose()
  sectorChildBubbleInst?.dispose()
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
