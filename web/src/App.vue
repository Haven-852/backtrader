<template>
  <div class="h-screen bg-zinc-950 text-zinc-100 flex overflow-hidden">
    <!-- Sidebar -->
    <div class="w-64 bg-zinc-900 border-r border-zinc-800 flex flex-col">
      <div class="p-5 border-b border-zinc-800 flex items-center gap-3">
        <div class="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center">
          <span class="text-white font-bold text-lg">A</span>
        </div>
        <div>
          <h1 class="font-semibold text-lg tracking-tight">AIquant</h1>
          <p class="text-xs text-zinc-500 -mt-0.5">Backtrader Agent Platform</p>
        </div>
      </div>

      <div class="p-3 flex-1 space-y-1 overflow-y-auto">
        <!-- 大模型对话 -->
        <div
          @click="currentTab = 0"
          :class="{ 'bg-zinc-800 text-white': currentTab === 0, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 0 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-comments w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">大模型对话</div>
            <div class="text-xs text-zinc-500">多模型 AI 分析</div>
          </div>
        </div>

        <!-- 行情看板 -->
        <div
          @click="currentTab = 1"
          :class="{ 'bg-zinc-800 text-white': currentTab === 1, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 1 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-chart-line w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">行情看板</div>
            <div class="text-xs text-zinc-500">K线 · 指标 · 多周期</div>
          </div>
        </div>

        <!-- 存储层状态（含连通性测试） -->
        <div
          @click="currentTab = 2"
          :class="{ 'bg-zinc-800 text-white': currentTab === 2, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 2 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-database w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">存储层状态</div>
            <div class="text-xs text-zinc-500">连通测试 · 数据预览</div>
          </div>
        </div>

        <!-- 行情中心 -->
        <div
          @click="currentTab = 3"
          :class="{ 'bg-zinc-800 text-white': currentTab === 3, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 3 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-globe w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">行情中心</div>
            <div class="text-xs text-zinc-500">期货主连 · 日周月线</div>
          </div>
        </div>

        <!-- 策略分析 -->
        <div
          @click="currentTab = 4"
          :class="{ 'bg-zinc-800 text-white': currentTab === 4, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 4 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-chess w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">策略分析</div>
            <div class="text-xs text-zinc-500">回测 · Agent · 记忆</div>
          </div>
        </div>

        <!-- 模拟交易 -->
        <div
          @click="currentTab = 5"
          :class="{ 'bg-zinc-800 text-white': currentTab === 5, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 5 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-wallet w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">模拟交易</div>
            <div class="text-xs text-zinc-500">纸面账户 · 交易 Agent</div>
          </div>
        </div>

        <!-- 知识库 -->
        <div
          @click="currentTab = 6"
          :class="{ 'bg-zinc-800 text-white': currentTab === 6, 'text-zinc-400 hover:text-white hover:bg-zinc-800/50': currentTab !== 6 }"
          class="flex items-center gap-3 px-4 py-3 rounded-xl cursor-pointer transition-all"
        >
          <i class="fa-solid fa-book-open w-5 text-center"></i>
          <div>
            <div class="font-medium text-sm">知识库</div>
            <div class="text-xs text-zinc-500">复盘 · 规则 · 经验</div>
          </div>
        </div>

      </div>

      <div class="p-5 border-t border-zinc-800 text-xs text-zinc-600">
        <div class="mb-1">v3.0 · Yuxi 风格架构</div>
        <div>web → routers → server → data</div>
      </div>
    </div>

    <!-- Main Content -->
    <div class="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
      <!-- Header -->
      <header class="h-12 border-b border-zinc-800 bg-zinc-900 px-6 flex items-center justify-between flex-shrink-0">
        <div class="flex items-center gap-3">
          <h2 class="text-sm font-semibold text-zinc-300">{{ tabTitles[currentTab] }}</h2>
          <span class="text-xs text-zinc-600">{{ tabSubtitles[currentTab] }}</span>
        </div>
        <div class="flex items-center gap-3 text-xs">
          <div class="flex items-center gap-1.5 px-3 py-1 bg-emerald-500/10 text-emerald-400 rounded-full">
            <div class="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse"></div>
            已连接
          </div>
        </div>
      </header>

      <!-- Tab Content -->
      <div class="flex-1 min-h-0 overflow-hidden flex flex-col">
        <!-- Chat / Dash 用 v-show 保活，避免来回切换时整页销毁重建、反复拉数 -->
        <div v-show="currentTab === 0" class="h-full flex flex-col">
          <ChatView />
        </div>

        <div v-show="currentTab === 1" class="flex-1 min-h-0 flex flex-col overflow-hidden relative">
          <DashView :active="currentTab === 1" />
        </div>

        <!-- 存储层状态（含连通性测试） -->
        <div v-if="currentTab === 2" class="h-full flex flex-col overflow-hidden">
          <div class="flex-shrink-0 px-6 pt-5 pb-3 space-y-4">
            <div class="flex items-center justify-between gap-3 flex-wrap">
              <h3 class="text-xl font-semibold">存储层状态</h3>
              <div class="flex items-center gap-2">
                <button
                  @click="aiCreateOpen = !aiCreateOpen"
                  class="px-3 py-2 bg-violet-600/20 hover:bg-violet-600/30 border border-violet-500/40 rounded-xl text-xs text-violet-200 transition-colors"
                >
                  <i class="fa-solid fa-wand-magic-sparkles mr-1"></i> AI 建表
                </button>
                <button @click="refreshAll" class="px-3 py-2 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 rounded-xl text-xs text-zinc-400 transition-colors">
                  <i class="fa-solid fa-rotate mr-1" :class="{ 'animate-spin': tablesLoading }"></i> 刷新
                </button>
              </div>
            </div>

            <!-- 自然语言建表 -->
            <div v-if="aiCreateOpen" class="bg-zinc-900 rounded-2xl p-4 border border-violet-500/30 space-y-3">
              <div class="flex items-start justify-between gap-3">
                <div>
                  <h4 class="font-semibold text-sm flex items-center gap-2">
                    <i class="fa-solid fa-table text-violet-400"></i> 用大模型创建自定义表
                  </h4>
                  <p class="text-xs text-zinc-500 mt-1 leading-relaxed">
                    写明表用途与字段需求，将调用你已保存的大模型设计 DDL 并在 PostgreSQL 建表；建好后可在左侧预览，也可供对话里的 SQL 工具调用。
                  </p>
                </div>
                <button @click="aiCreateOpen = false" class="text-zinc-500 hover:text-zinc-300 text-xs px-2 py-1">关闭</button>
              </div>
              <textarea
                v-model="aiCreatePrompt"
                rows="3"
                placeholder="例如：建一张记录我每日盯盘笔记的表，含股票代码、交易日、情绪评分、备注…"
                class="w-full bg-zinc-800 border border-zinc-700 focus:border-violet-500 rounded-xl px-3 py-2 text-sm outline-none resize-y min-h-[72px]"
              ></textarea>
              <div class="flex flex-wrap items-center gap-3">
                <label class="text-xs text-zinc-500 flex items-center gap-2">
                  模型
                  <select v-model="aiCreateProfileId" class="bg-zinc-800 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs text-zinc-200 outline-none">
                    <option :value="null">使用当前激活配置</option>
                    <option v-for="p in llmTestItems" :key="p.id" :value="p.id">{{ p.name }} · {{ p.model }}</option>
                  </select>
                </label>
                <label class="text-xs text-zinc-500 flex items-center gap-1.5 cursor-pointer">
                  <input type="checkbox" v-model="aiCreateForLlm" class="rounded border-zinc-600" />
                  允许大模型调用此表
                </label>
                <div class="flex-1"></div>
                <button
                  @click="runAiCreate(true)"
                  :disabled="aiCreateBusy || !aiCreatePrompt.trim()"
                  class="px-3 py-1.5 rounded-lg text-xs bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 disabled:opacity-40"
                >
                  仅预览设计
                </button>
                <button
                  @click="runAiCreate(false)"
                  :disabled="aiCreateBusy || !aiCreatePrompt.trim()"
                  class="px-3 py-1.5 rounded-lg text-xs bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40"
                >
                  <i v-if="aiCreateBusy" class="fa-solid fa-spinner animate-spin mr-1"></i>
                  {{ aiCreateBusy ? '生成中…' : '创建表' }}
                </button>
              </div>
              <div v-if="aiCreateError" class="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{{ aiCreateError }}</div>
              <div v-if="aiCreateResult" class="text-xs bg-zinc-800/80 border border-zinc-700 rounded-xl p-3 space-y-2">
                <div class="flex flex-wrap gap-3 text-zinc-300">
                  <span>表名 <code class="text-violet-300">{{ aiCreateResult.table_name || aiCreateResult.design?.table_name }}</code></span>
                  <span v-if="aiCreateResult.label || aiCreateResult.design?.label">{{ aiCreateResult.label || aiCreateResult.design?.label }}</span>
                  <span v-if="aiCreateResult.dry_run" class="text-amber-400">预览未落库</span>
                  <span v-else-if="aiCreateResult.ok" class="text-emerald-400">已创建</span>
                </div>
                <pre class="text-[11px] text-zinc-400 overflow-x-auto whitespace-pre-wrap max-h-40">{{ aiCreateResult.ddl || aiCreateResult.design?.ddl }}</pre>
              </div>
            </div>

            <!-- 连通性测试（合并自原独立页） -->
            <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <div class="bg-zinc-900 rounded-2xl p-4 border border-zinc-800">
                <h4 class="font-semibold mb-3 flex items-center gap-2 text-sm">
                  <i class="fa-solid fa-brain text-blue-400"></i> 大模型连通测试
                </h4>
                <div class="space-y-2">
                  <div v-if="llmTestsLoading" class="text-xs text-zinc-500 px-1">正在读取已保存的模型配置…</div>
                  <div v-else-if="!llmTestItems.length" class="text-xs text-zinc-500 px-1 leading-relaxed">
                    还没有已保存的模型。请到「大模型对话」填写厂商、模型名和 API Key 并保存；配置写入内部表，不会出现在下方数据预览里。
                  </div>
                  <div v-for="model in llmTestItems" :key="model.id"
                    @click="testSavedModel(model)"
                    class="p-3 rounded-xl bg-zinc-800/50 hover:bg-zinc-800 cursor-pointer border border-transparent hover:border-zinc-700 transition-all"
                  >
                    <div class="flex justify-between items-center">
                      <div>
                        <div class="font-medium text-sm">{{ model.name }}</div>
                        <div class="text-xs text-zinc-500 mt-0.5">{{ model.provider }} · {{ model.model }}</div>
                      </div>
                      <div :class="modelResults[String(model.id)] ? 'text-emerald-400' : 'text-zinc-500'" class="text-xs font-medium">
                        {{ modelResults[String(model.id)] || '点击测试' }}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="bg-zinc-900 rounded-2xl p-4 border border-zinc-800">
                <h4 class="font-semibold mb-3 flex items-center gap-2 text-sm">
                  <i class="fa-solid fa-plug text-emerald-400"></i> 存储连通测试
                </h4>
                <div
                  @click="testAllDatabases()"
                  class="p-4 rounded-xl bg-zinc-800/50 hover:bg-zinc-800 cursor-pointer border border-transparent hover:border-emerald-500/30 transition-all mb-2"
                >
                  <div class="flex justify-between items-center">
                    <div>
                      <div class="font-medium text-sm">全链路存储测试</div>
                      <div class="text-xs text-zinc-500 mt-0.5">PostgreSQL · TimescaleDB · InfluxDB · Redis</div>
                    </div>
                    <div class="text-right">
                      <div :class="dbStatus === 'connected' ? 'text-emerald-400' : dbStatus === 'testing' ? 'text-amber-400' : 'text-zinc-500'"
                        class="text-base font-semibold">
                        {{ dbStatus === 'connected' ? '✅' : dbStatus === 'testing' ? '⏳' : '测试' }}
                      </div>
                    </div>
                  </div>
                </div>
                <div v-if="dbResults.length" class="space-y-1.5">
                  <div v-for="r in dbResults" :key="r.name" class="flex justify-between items-center text-xs py-1.5 border-b border-zinc-800 last:border-0">
                    <span class="text-zinc-400">{{ r.name }}</span>
                    <span :class="r.ok ? 'text-emerald-400' : 'text-red-400'">{{ r.ok ? '✅' : '❌' }} {{ r.msg }}</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 后端在线状态卡片 -->
            <div>
              <div class="flex gap-2 mb-3 flex-wrap">
                <button v-for="be in backends" :key="be.key" @click="switchBackend(be.key)"
                  :class="activeBackend === be.key ? 'bg-blue-600/20 text-blue-400 border-blue-500/40' : 'bg-zinc-900 text-zinc-500 hover:text-zinc-300 border-zinc-800'"
                  class="flex items-center gap-2 px-4 py-2 rounded-xl border text-sm transition-all">
                  <i :class="be.icon" class="text-xs"></i> {{ be.label }}
                  <span class="w-1.5 h-1.5 rounded-full" :class="getBackendStatus(be.key) === 'online' ? 'bg-emerald-500' : getBackendStatus(be.key) === 'error' ? 'bg-red-500' : 'bg-amber-500'"></span>
                </button>
              </div>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-2.5">
                <div v-for="be in backends" :key="'st-'+be.key" @click="switchBackend(be.key)"
                  :class="['rounded-xl px-3.5 py-2.5 border cursor-pointer transition-all', activeBackend === be.key ? (getBackendStatus(be.key) === 'online' ? 'border-emerald-500/50 bg-emerald-500/5' : 'border-amber-500/40 bg-amber-500/5') : 'border-zinc-800 bg-zinc-900/70 hover:border-zinc-700']">
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-medium text-zinc-300">{{ be.label }}</span>
                    <span class="text-xs" :class="getBackendStatus(be.key) === 'online' ? 'text-emerald-400' : getBackendStatus(be.key) === 'error' ? 'text-red-400' : 'text-amber-400'">
                      {{ getBackendStatus(be.key) === 'online' ? '在线' : getBackendStatus(be.key) === 'error' ? '异常' : '离线' }}
                    </span>
                  </div>
                  <div class="text-xs text-zinc-600 truncate">{{ be.desc }}</div>
                </div>
              </div>
            </div>
          </div>

          <div class="flex-1 flex min-h-0 px-6 pb-6">
            <div class="w-80 flex-shrink-0 flex flex-col mr-4">
              <div class="flex items-center gap-2 mb-3">
                <div class="relative flex-1">
                  <input v-model="tableSearch" :placeholder="getSearchPlaceholder()" class="w-full bg-zinc-800 border border-zinc-700 focus:border-blue-500 rounded-lg pl-8 pr-3 py-1.5 text-xs outline-none transition-colors" />
                  <i class="fa-solid fa-search absolute left-2.5 top-2 text-zinc-500 text-xs"></i>
                </div>
                <button @click="loadItems()" class="px-2.5 py-1.5 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-xs transition-colors" :class="{ 'animate-spin': tablesLoading }">
                  <i class="fa-solid fa-rotate"></i>
                </button>
              </div>
              <div
                v-if="activeBackend === 'postgresql' && trainTableItem"
                @click="selectItem(trainTableItem)"
                :class="[
                  'mb-3 rounded-xl border p-3 cursor-pointer transition-all',
                  selectedItem?.name === 'ml_train_daily'
                    ? 'border-violet-500/50 bg-violet-500/10'
                    : 'border-violet-500/25 bg-violet-500/5 hover:border-violet-500/40',
                ]"
              >
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-2">
                    <i class="fa-solid fa-flask text-violet-400 text-xs"></i>
                    <span class="text-sm font-semibold text-violet-200">{{ trainTableItem.label || '训练表' }}</span>
                  </div>
                  <span class="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300">LightGBM</span>
                </div>
                <div class="text-xs text-zinc-500 line-clamp-2 leading-relaxed">{{ trainTableItem.desc }}</div>
                <div class="flex items-center justify-between mt-2 text-xs text-zinc-600">
                  <span>ml_train_daily</span>
                  <span class="tabular-nums">{{ formatRowCount(trainTableItem.rows) }} 行</span>
                </div>
              </div>
              <div class="flex gap-1.5 mb-3 flex-wrap" v-if="tableCategories.length">
                <button v-for="cat in tableCategories" :key="cat" @click="tableCategoryFilter = tableCategoryFilter === cat ? '' : cat"
                  :class="tableCategoryFilter === cat ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-white'" class="px-2 py-1 rounded-md text-xs transition-all">{{ cat }}</button>
              </div>
              <div class="flex-1 overflow-auto space-y-1 pr-1">
                <div v-if="tablesLoading && !filteredItems.length" class="text-center py-8 text-zinc-500">
                  <i class="fa-solid fa-spinner animate-spin text-2xl mb-2 block"></i>加载中...
                </div>
                <div v-else-if="!filteredItems.length" class="text-center py-8 text-zinc-500 text-sm">暂无数据</div>
                <div v-for="t in filteredItems" :key="t.name" @click="selectItem(t)"
                  :class="['group cursor-pointer rounded-lg p-3 border transition-all', selectedItem?.name === t.name ? 'bg-blue-600/20 border-blue-500/50' : t.custom ? 'bg-violet-500/5 border-violet-500/20 hover:border-violet-500/40' : t.name === 'ml_train_daily' ? 'bg-violet-500/5 border-violet-500/20 hover:border-violet-500/40' : 'bg-zinc-900/50 border-transparent hover:border-zinc-700 hover:bg-zinc-900']">
                  <div class="flex items-center justify-between mb-0.5">
                    <span class="text-sm font-medium truncate flex items-center gap-1.5" :title="t.name">
                      {{ t.label || t.name }}
                      <span v-if="t.custom" class="text-[10px] px-1 py-0.5 rounded bg-violet-500/20 text-violet-300 font-normal">我的</span>
                    </span>
                    <span v-if="t.has_data" class="text-xs text-emerald-500 flex-shrink-0 ml-2"><i class="fa-solid fa-circle text-[6px]"></i> 有数据</span>
                    <span v-else class="text-xs text-zinc-600 flex-shrink-0 ml-2">空</span>
                  </div>
                  <div class="text-xs text-zinc-600 line-clamp-1 leading-relaxed">{{ t.desc }}</div>
                  <div class="flex items-center justify-between mt-1.5">
                    <span class="text-xs text-zinc-700">{{ t.category }}</span>
                    <span class="text-xs text-zinc-600 tabular-nums">{{ formatRowCount(t.rows) }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="flex-1 flex flex-col min-w-0 bg-zinc-900 rounded-2xl border border-zinc-800">
              <div v-if="!selectedItem" class="flex-1 flex items-center justify-center">
                <div class="text-center text-zinc-500">
                  <i class="fa-solid fa-database text-4xl mb-3 block opacity-30"></i>
                  <p class="text-sm">选择数据项查看详情</p>
                  <p class="text-xs mt-1 text-zinc-600">{{ getEmptyHint() }}</p>
                </div>
              </div>
              <div v-else-if="previewLoading" class="flex-1 flex items-center justify-center">
                <div class="flex flex-col items-center gap-2">
                  <i class="fa-solid fa-spinner animate-spin text-2xl text-blue-400"></i>
                  <span class="text-sm text-zinc-400">加载预览...</span>
                </div>
              </div>
              <template v-else-if="previewData">
                <div class="flex-shrink-0 px-5 py-4 border-b border-zinc-800">
                  <div class="flex items-center justify-between mb-2 gap-3">
                    <div class="min-w-0">
                      <h3 class="text-lg font-bold flex items-center gap-2 flex-wrap">
                        {{ previewData.label || previewData.info?.name || previewData.name }}
                        <span v-if="selectedItemIsCustom" class="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300 font-normal">我创建的</span>
                      </h3>
                      <div class="flex items-center gap-3 mt-1">
                        <span class="text-xs text-zinc-500">{{ previewData.info?.category || previewData.info?.source || '' }}</span>
                        <span class="text-xs px-2 py-0.5 rounded bg-zinc-800 text-zinc-400">{{ activeBackend }}</span>
                        <span v-if="!selectedItemIsCustom && activeBackend === 'postgresql'" class="text-[10px] text-zinc-600">系统表只读预览</span>
                      </div>
                    </div>
                    <div class="flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">
                      <button
                        type="button"
                        @click="exportTable('csv')"
                        :disabled="exportBusy"
                        class="px-2.5 py-1.5 rounded-lg text-xs border border-emerald-500/40 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 disabled:opacity-40 transition-colors"
                        title="导出为 CSV（最多 10 万行）"
                      >
                        <i v-if="exportBusy && exportFormat === 'csv'" class="fa-solid fa-spinner animate-spin mr-1"></i>
                        <i v-else class="fa-solid fa-file-csv mr-1"></i>
                        导出 CSV
                      </button>
                      <button
                        type="button"
                        @click="exportTable('markdown')"
                        :disabled="exportBusy"
                        class="px-2.5 py-1.5 rounded-lg text-xs border border-sky-500/40 bg-sky-500/10 hover:bg-sky-500/20 text-sky-300 disabled:opacity-40 transition-colors"
                        title="导出为 Markdown 表格（最多 10 万行）"
                      >
                        <i v-if="exportBusy && exportFormat === 'markdown'" class="fa-solid fa-spinner animate-spin mr-1"></i>
                        <i v-else class="fa-solid fa-file-lines mr-1"></i>
                        导出 MD
                      </button>
                      <button
                        v-if="selectedItemIsCustom"
                        @click="deleteSelectedCustomTable"
                        :disabled="deleteCustomBusy"
                        class="px-2.5 py-1.5 rounded-lg text-xs border border-red-500/40 bg-red-500/10 hover:bg-red-500/20 text-red-300 disabled:opacity-40 transition-colors"
                      >
                        <i v-if="deleteCustomBusy" class="fa-solid fa-spinner animate-spin mr-1"></i>
                        <i v-else class="fa-solid fa-trash mr-1"></i>
                        删除此表
                      </button>
                      <div class="text-right" v-if="previewData.total_rows >= 0">
                        <div class="text-xs text-zinc-500">总行数</div>
                        <div class="text-lg font-bold text-emerald-400 tabular-nums">{{ formatRowCount(previewData.total_rows) }}</div>
                      </div>
                    </div>
                  </div>
                  <div class="text-sm text-zinc-400 bg-zinc-800/50 rounded-lg px-3 py-2 leading-relaxed">
                    <span class="text-zinc-500">📋 </span>{{ previewData.info?.desc || '暂无描述' }}
                  </div>
                  <div
                    v-if="previewData.name === 'ml_train_daily'"
                    class="mt-2 text-xs text-violet-300/90 bg-violet-500/10 border border-violet-500/20 rounded-lg px-3 py-2 leading-relaxed"
                  >
                    LightGBM 输入特征不含 <code class="text-violet-200">open/high/low/close</code>；
                    标签列为 <code class="text-violet-200">y_high</code>（相对开盘最高收益）、
                    <code class="text-violet-200">y_low</code>（相对收盘最低收益）。
                  </div>
                </div>
                <div class="flex-shrink-0 px-5 py-3 border-b border-zinc-800" v-if="previewData.columns?.length">
                  <h4 class="text-xs font-semibold text-zinc-400 mb-2 uppercase tracking-wide">列信息 ({{ previewData.columns.length }} 列)</h4>
                  <div class="flex flex-wrap gap-1.5">
                    <span
                      v-for="col in previewData.columns"
                      :key="col.name"
                      class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-zinc-800 text-zinc-400"
                      :class="{
                        'bg-amber-500/10 text-amber-200/90': ['open','high','low','close'].includes(col.name),
                        'bg-rose-500/10 text-rose-200/90': ['y_high','y_low'].includes(col.name),
                      }"
                      :title="(col.label || col.name) + (col.type ? ' · ' + col.type : '')"
                    >
                      <span class="text-zinc-300 font-mono">{{ col.name }}</span>
                      <span class="text-emerald-500/90">：{{ col.label || col.name }}</span>
                    </span>
                  </div>
                </div>
                <div
                  v-if="selectedItemIsCustom"
                  class="flex-shrink-0 px-5 py-3 border-b border-zinc-800 bg-violet-500/5 space-y-2"
                >
                  <div class="flex items-center justify-between gap-2">
                    <h4 class="text-xs font-semibold text-violet-300">填写 / 修改本表（直接写 PostgreSQL）</h4>
                    <button
                      type="button"
                      class="text-[11px] text-zinc-500 hover:text-zinc-300"
                      @click="rowEditOpen = !rowEditOpen"
                    >{{ rowEditOpen ? '收起' : '展开' }}</button>
                  </div>
                  <div v-if="rowEditOpen" class="space-y-2">
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <label
                        v-for="col in editablePreviewColumns"
                        :key="'ins-' + col.name"
                        class="flex flex-col gap-1 text-[11px] text-zinc-400"
                      >
                        <span class="font-mono text-zinc-300">{{ col.name }} <span class="text-zinc-600">{{ col.type }}</span></span>
                        <input
                          v-model="rowDraft[col.name]"
                          class="bg-zinc-900 border border-zinc-700 rounded-lg px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-violet-500/50"
                          :placeholder="col.type?.toUpperCase?.().startsWith('VECTOR') ? '[0.1,0.2,...]' : (col.label || col.name)"
                        />
                      </label>
                    </div>
                    <div class="flex items-center gap-2 flex-wrap">
                      <button
                        type="button"
                        class="px-3 py-1.5 rounded-lg text-xs bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40"
                        :disabled="rowSaveBusy"
                        @click="insertCustomRow"
                      >
                        <i v-if="rowSaveBusy" class="fa-solid fa-spinner animate-spin mr-1"></i>
                        新增一行
                      </button>
                      <span v-if="rowEditError" class="text-xs text-red-400">{{ rowEditError }}</span>
                      <span v-else-if="rowEditHint" class="text-xs text-emerald-400">{{ rowEditHint }}</span>
                    </div>
                  </div>
                </div>
                <div class="flex-1 flex flex-col min-h-0">
                  <div class="flex items-center justify-between px-5 pt-4 pb-2 gap-2 flex-wrap">
                    <h4 class="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
                      数据预览
                      <span class="normal-case font-normal text-zinc-500 ml-1">
                        已加载 {{ previewData.rows?.length || 0 }}
                        <template v-if="previewData.total_rows >= 0"> / {{ formatRowCount(previewData.total_rows) }}</template>
                        行 · 首屏 {{ PREVIEW_LIMIT }}，滚动继续加载
                      </span>
                    </h4>
                    <button
                      v-if="previewHasMore"
                      @click="appendMoreRows"
                      :disabled="previewLoadingMore"
                      class="px-2.5 py-1 text-xs rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 disabled:opacity-40 transition-colors"
                    >
                      <i v-if="previewLoadingMore" class="fa-solid fa-spinner animate-spin mr-1"></i>
                      {{ previewLoadingMore ? '加载中' : '加载更多' }}
                    </button>
                    <span v-else-if="previewData.rows?.length" class="text-xs text-zinc-600">已到底</span>
                  </div>
                  <div
                    class="flex-1 overflow-auto px-5 pb-4"
                    v-if="previewData.rows?.length"
                    @scroll.passive="onPreviewScroll"
                  >
                    <div class="overflow-x-auto">
                      <table class="w-full text-xs border-collapse">
                        <thead class="sticky top-0 z-10">
                          <tr class="border-b border-zinc-700 bg-zinc-900">
                            <th class="text-left py-2 px-3 text-zinc-500 font-medium sticky left-0 bg-zinc-900">#</th>
                            <th v-for="col in previewColumns" :key="col.name" class="text-left py-2 px-3 text-zinc-400 font-medium whitespace-nowrap" :title="col.name">
                              <span class="text-zinc-300">{{ col.label }}</span>
                              <span class="ml-1 text-[10px] text-zinc-600 font-mono">{{ col.name }}</span>
                            </th>
                            <th v-if="selectedItemIsCustom" class="text-left py-2 px-3 text-zinc-500 font-medium whitespace-nowrap">操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="(row, ri) in previewData.rows" :key="ri" class="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                            <td class="py-1.5 px-3 text-zinc-600 sticky left-0 bg-zinc-900 tabular-nums">{{ ri + 1 }}</td>
                            <td
                              v-for="col in previewColumns"
                              :key="col.name"
                              class="py-1.5 px-3 text-zinc-300 whitespace-nowrap max-w-[280px]"
                              :title="formatCell(row[col.name])"
                            >
                              <input
                                v-if="selectedItemIsCustom && editingRowIndex === ri && col.name !== 'id'"
                                v-model="editingRowValues[col.name]"
                                class="w-full min-w-[120px] bg-zinc-950 border border-violet-500/40 rounded px-1.5 py-1 text-xs text-zinc-200 outline-none"
                              />
                              <span v-else class="truncate inline-block max-w-[220px] align-middle">{{ formatCell(row[col.name]) }}</span>
                            </td>
                            <td v-if="selectedItemIsCustom" class="py-1.5 px-3 whitespace-nowrap">
                              <template v-if="editingRowIndex === ri">
                                <button type="button" class="text-emerald-400 hover:text-emerald-300 mr-2" :disabled="rowSaveBusy" @click="saveEditedRow(row)">保存</button>
                                <button type="button" class="text-zinc-500 hover:text-zinc-300" @click="cancelEditRow">取消</button>
                              </template>
                              <button v-else type="button" class="text-violet-300 hover:text-violet-200" @click="startEditRow(ri, row)">编辑</button>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    <div v-if="previewLoadingMore" class="flex justify-center py-3 text-xs text-zinc-500 gap-2">
                      <i class="fa-solid fa-spinner animate-spin"></i> 加载更多…
                    </div>
                    <div v-else-if="previewHasMore" class="text-center py-2 text-[11px] text-zinc-600">继续向下滚动加载</div>
                  </div>
                  <div v-else class="flex-1 flex items-center justify-center text-sm text-zinc-500">
                    暂无数据<span v-if="selectedItemIsCustom">，可在上方「填写 / 修改」新增一行</span>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>

        <!-- Market Data Tab：KeepAlive 避免每次点击都整页重拉 -->
        <div v-show="currentTab === 3" class="h-full min-h-0 overflow-hidden">
          <KeepAlive>
            <MarketDashView v-if="currentTab === 3" />
          </KeepAlive>
        </div>

        <!-- Strategy Analysis Tab：v-show 保活，选股结果离开看板后仍在 -->
        <div v-show="currentTab === 4" class="flex-1 min-h-0 overflow-hidden">
          <StrategyView />
        </div>

        <!-- Paper Trade Tab -->
        <div v-show="currentTab === 5" class="flex-1 min-h-0 overflow-hidden">
          <PaperTradeView />
        </div>

        <!-- Knowledge Tab -->
        <div v-show="currentTab === 6" class="flex-1 min-h-0 overflow-hidden">
          <KnowledgeView />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import axios from 'axios'
import ChatView from './ChatView.vue'
import DashView from './DashView.vue'
import MarketDashView from './MarketDashView.vue'
import StrategyView from './StrategyView.vue'
import PaperTradeView from './PaperTradeView.vue'
import KnowledgeView from './KnowledgeView.vue'
import { currentTab } from './appNav'

const tabTitles = ['大模型对话', '行情看板', '存储层状态', '行情中心', '策略分析', '模拟交易', '知识库']
const tabSubtitles = ['DeepSeek · GPT · Claude', 'K线 · MA · MACD · RSI', '连通测试 · 训练表 · 数据预览', '全市场 · 30张表', '模式A/B · 回测 · 记忆', '纸面账户 · 感知决策执行', '今日认知 · 交易复盘']

const llmTestItems = ref<any[]>([])
const llmTestsLoading = ref(false)
const modelResults = ref<Record<string, string>>({})
const dbStatus = ref('idle')
const dbResults = ref<any[]>([])

async function loadLlmTests() {
  llmTestsLoading.value = true
  try {
    const res = await axios.get('/chat/llm-profiles')
    llmTestItems.value = (res.data.profiles || []).filter((p: any) => p.enabled)
  } catch {
    llmTestItems.value = []
  } finally {
    llmTestsLoading.value = false
  }
}

const testSavedModel = async (model: any) => {
  const key = String(model.id)
  modelResults.value[key] = '测试中…'
  try {
    const res = await axios.post('/chat/llm-test', {
      profile_id: model.id,
      provider: model.provider,
      model: model.model,
    })
    modelResults.value[key] = res.data.ok
      ? `✅ ${res.data.latency_ms || 0}ms`
      : `⚠️ ${res.data.message || '未连通'}`
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.response?.data?.message
    modelResults.value[key] = '❌ ' + (typeof detail === 'string' ? detail : '连接失败')
  }
}

const testAllDatabases = async () => {
  dbStatus.value = 'testing'
  try {
    const res = await axios.get('/models/test/database')
    const details = res.data.details || {}
    dbResults.value = Object.entries(details).map(([name, info]: [string, any]) => ({
      name, ok: info.status === 'connected', msg: info.message?.replace(/^[✅❌⚠️]\s*/, '') || info.status,
    }))
    dbStatus.value = dbResults.value.every((r: any) => r.ok) ? 'connected' : 'partial'
  } catch { dbStatus.value = 'error' }
}

interface TableInfo { table_name: string; name: string; label: string; category: string; desc: string; source: string; rows: number; has_data: boolean; custom?: boolean }
interface PreviewData { table_name?: string; name?: string; label?: string; info?: TableInfo; columns: { name: string; type: string; nullable: boolean; label?: string }[]; rows: Record<string, any>[]; total_rows: number; preview_count: number; has_more?: boolean }

const backends = [
  { key: 'postgresql', label: 'PostgreSQL', icon: 'fa-solid fa-database', desc: '结构化 & 时序K线' },
  { key: 'influxdb', label: 'InfluxDB', icon: 'fa-solid fa-bolt', desc: '高频行情 & 分钟线' },
  { key: 'redis', label: 'Redis', icon: 'fa-solid fa-memory', desc: '实时缓存' },
  { key: 'minio', label: 'MinIO', icon: 'fa-solid fa-box-archive', desc: '数据湖 (S3兼容)' },
]
const activeBackend = ref('postgresql')
const storageServices = ref<Record<string, any>>({})
const storageItems = ref<TableInfo[]>([])
const tableCategories = ref<string[]>([])
const tableSearch = ref('')
const tableCategoryFilter = ref('')
const selectedItem = ref<TableInfo | null>(null)
const previewData = ref<PreviewData | null>(null)
const tablesLoading = ref(false)
const previewLoading = ref(false)
const previewLoadingMore = ref(false)
/** 下一页请求的 offset（已累积行数） */
const previewNextOffset = ref(0)
const previewHasMore = ref(false)
const PREVIEW_LIMIT = 50

const aiCreateOpen = ref(false)
const aiCreatePrompt = ref('')
const aiCreateProfileId = ref<number | null>(null)
const aiCreateForLlm = ref(true)
const aiCreateBusy = ref(false)
const aiCreateError = ref('')
const aiCreateResult = ref<any>(null)
const deleteCustomBusy = ref(false)
const exportBusy = ref(false)
const exportFormat = ref<'csv' | 'markdown'>('csv')
const rowEditOpen = ref(true)
const rowDraft = ref<Record<string, string>>({})
const rowSaveBusy = ref(false)
const rowEditError = ref('')
const rowEditHint = ref('')
const editingRowIndex = ref<number | null>(null)
const editingRowValues = ref<Record<string, string>>({})

const selectedItemIsCustom = computed(() => Boolean(selectedItem.value?.custom))

const editablePreviewColumns = computed(() => {
  const cols = previewData.value?.columns || []
  return cols.filter((c) => {
    const t = String(c.type || '').toUpperCase()
    return !['BIGSERIAL', 'SERIAL', 'SMALLSERIAL'].includes(t)
  })
})

const trainTableItem = computed(() =>
  storageItems.value.find((t) => t.name === 'ml_train_daily') || null
)

const filteredItems = computed(() => {
  let list = [...storageItems.value]
  if (tableCategoryFilter.value) list = list.filter(t => t.category === tableCategoryFilter.value)
  if (tableSearch.value.trim()) {
    const kw = tableSearch.value.toLowerCase()
    list = list.filter(t => (t.name || '').toLowerCase().includes(kw) || (t.label || '').toLowerCase().includes(kw) || (t.desc || '').toLowerCase().includes(kw))
  }
  // 训练表置顶，便于在存储层状态中直接找到
  list.sort((a, b) => {
    const ap = a.name === 'ml_train_daily' ? 0 : a.category === '模型训练' ? 1 : 2
    const bp = b.name === 'ml_train_daily' ? 0 : b.category === '模型训练' ? 1 : 2
    if (ap !== bp) return ap - bp
    return 0
  })
  return list
})

const previewColumns = computed(() => {
  const cols = previewData.value?.columns
  if (cols?.length) {
    return cols.map((c) => ({ name: c.name, label: c.label || c.name }))
  }
  if (!previewData.value?.rows?.length) return [] as { name: string; label: string }[]
  return Object.keys(previewData.value.rows[0]).map((name) => ({ name, label: name }))
})

function getBackendStatus(key: string): string {
  const svc = storageServices.value[key]
  if (!svc) return 'unknown'
  return svc.status || 'unknown'
}

function getSearchPlaceholder(): string {
  const map: Record<string, string> = { postgresql: '搜索表名或描述...', influxdb: '搜索 Bucket 或 Measurement...', redis: '搜索 Key...', minio: '搜索 Bucket 名称...' }
  return map[activeBackend.value] || '搜索...'
}

function getEmptyHint(): string {
  const map: Record<string, string> = { postgresql: '点击左侧数据表可查看表结构与数据预览', influxdb: '点击左侧 Bucket/Measurement 查看数据预览', redis: '点击左侧 Key 查看缓存内容', minio: '点击左侧 Bucket 查看对象列表' }
  return map[activeBackend.value] || '选择左侧数据项查看详情'
}

function switchBackend(key: string) {
  activeBackend.value = key
  selectedItem.value = null
  previewData.value = null
  previewNextOffset.value = 0
  previewHasMore.value = false
  loadItems()
}
async function refreshAll() { await Promise.all([loadStorageStatus(), loadItems(), loadLlmTests()]) }

async function loadStorageStatus() {
  try { const res = await axios.get('/storage/status'); storageServices.value = res.data.services || {} } catch (e) { console.warn('获取存储状态失败:', e) }
}

async function loadItems() {
  tablesLoading.value = true
  try {
    const res = await axios.get('/storage/tables', { params: { backend: activeBackend.value } })
    storageItems.value = (res.data.items || []).map((i: any) => ({ ...i, table_name: i.name, label: i.label || i.name || '' }))
    tableCategories.value = res.data.categories || []
  } catch (e) { console.warn('获取数据列表失败:', e) } finally { tablesLoading.value = false }
}

async function selectItem(t: TableInfo) {
  selectedItem.value = t
  previewLoading.value = true
  previewData.value = null
  previewNextOffset.value = 0
  previewHasMore.value = false
  rowDraft.value = {}
  rowEditError.value = ''
  rowEditHint.value = ''
  editingRowIndex.value = null
  editingRowValues.value = {}
  try {
    const itemName = encodeURIComponent(t.name || t.table_name || '')
    const res = await axios.get(`/storage/tables/${itemName}`, {
      params: { backend: activeBackend.value, limit: PREVIEW_LIMIT, offset: 0 },
    })
    if (!res.data.error) {
      previewData.value = res.data
      const n = res.data.rows?.length || 0
      previewNextOffset.value = n
      previewHasMore.value = Boolean(res.data.has_more)
    }
  } catch (e) {
    console.warn('获取预览失败:', e)
  } finally {
    previewLoading.value = false
  }
}

async function insertCustomRow() {
  if (!selectedItemIsCustom.value || !selectedItem.value) return
  const name = selectedItem.value.name || selectedItem.value.table_name
  if (!name) return
  rowSaveBusy.value = true
  rowEditError.value = ''
  rowEditHint.value = ''
  try {
    const values: Record<string, any> = {}
    for (const [k, v] of Object.entries(rowDraft.value)) {
      if (v !== undefined && String(v).trim() !== '') values[k] = v
    }
    await axios.post(`/storage/custom-tables/${encodeURIComponent(name)}/rows`, {
      values,
      user_id: 'default',
    })
    rowDraft.value = {}
    rowEditHint.value = '已写入 PostgreSQL'
    await selectItem(selectedItem.value)
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    rowEditError.value = typeof detail === 'string' ? detail : (e?.message || '插入失败')
  } finally {
    rowSaveBusy.value = false
  }
}

function startEditRow(ri: number, row: Record<string, any>) {
  editingRowIndex.value = ri
  const draft: Record<string, string> = {}
  for (const col of editablePreviewColumns.value) {
    const v = row[col.name]
    draft[col.name] = v === null || v === undefined ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v))
  }
  editingRowValues.value = draft
  rowEditError.value = ''
  rowEditHint.value = ''
}

function cancelEditRow() {
  editingRowIndex.value = null
  editingRowValues.value = {}
}

async function saveEditedRow(row: Record<string, any>) {
  if (!selectedItemIsCustom.value || !selectedItem.value) return
  const name = selectedItem.value.name || selectedItem.value.table_name
  const pk = row?.id
  if (!name || pk === undefined || pk === null) {
    rowEditError.value = '缺少主键 id，无法更新'
    return
  }
  rowSaveBusy.value = true
  rowEditError.value = ''
  try {
    await axios.patch(`/storage/custom-tables/${encodeURIComponent(name)}/rows/${encodeURIComponent(String(pk))}`, {
      values: editingRowValues.value,
      user_id: 'default',
      pk_column: 'id',
    })
    cancelEditRow()
    rowEditHint.value = `已更新 id=${pk}`
    await selectItem(selectedItem.value)
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    rowEditError.value = typeof detail === 'string' ? detail : (e?.message || '更新失败')
  } finally {
    rowSaveBusy.value = false
  }
}

async function appendMoreRows() {
  if (!selectedItem.value || !previewData.value || previewLoadingMore.value || !previewHasMore.value) return
  if (activeBackend.value !== 'postgresql') {
    // 非 PG 暂无可靠游标分页，避免重复追加
    previewHasMore.value = false
    return
  }
  previewLoadingMore.value = true
  try {
    const itemName = encodeURIComponent(selectedItem.value.name || selectedItem.value.table_name || '')
    const res = await axios.get(`/storage/tables/${itemName}`, {
      params: {
        backend: activeBackend.value,
        limit: PREVIEW_LIMIT,
        offset: previewNextOffset.value,
      },
    })
    const batch = res.data.rows || []
    if (!res.data.error && batch.length) {
      previewData.value = {
        ...previewData.value,
        ...res.data,
        rows: [...(previewData.value.rows || []), ...batch],
        columns: previewData.value.columns?.length ? previewData.value.columns : res.data.columns,
        total_rows: res.data.total_rows ?? previewData.value.total_rows,
      }
      previewNextOffset.value += batch.length
      previewHasMore.value = Boolean(res.data.has_more)
    } else {
      previewHasMore.value = false
    }
  } catch (e) {
    console.warn('加载更多失败:', e)
  } finally {
    previewLoadingMore.value = false
  }
}

function onPreviewScroll(ev: Event) {
  const el = ev.target as HTMLElement | null
  if (!el || !previewHasMore.value || previewLoadingMore.value) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120) {
    appendMoreRows()
  }
}

async function runAiCreate(dryRun: boolean) {
  aiCreateError.value = ''
  aiCreateResult.value = null
  const prompt = aiCreatePrompt.value.trim()
  if (!prompt) return
  aiCreateBusy.value = true
  try {
    const res = await axios.post('/storage/ai-create-table', {
      prompt,
      profile_id: aiCreateProfileId.value || undefined,
      dry_run: dryRun,
      for_llm: aiCreateForLlm.value,
      user_id: 'default',
    })
    aiCreateResult.value = res.data
    if (!dryRun && res.data?.ok && res.data.table_name) {
      await loadItems()
      const created = storageItems.value.find((t) => t.name === res.data.table_name)
      if (created) await selectItem(created)
    }
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    aiCreateError.value = typeof detail === 'string' ? detail : (e?.message || '建表失败')
  } finally {
    aiCreateBusy.value = false
  }
}

async function deleteSelectedCustomTable() {
  if (!selectedItemIsCustom.value || !selectedItem.value) return
  const name = selectedItem.value.name || selectedItem.value.table_name
  if (!name) return
  const ok = window.confirm(`确认删除自定义表「${selectedItem.value.label || name}」(${name})？\n仅你创建的表可删，系统表无法删除。`)
  if (!ok) return
  deleteCustomBusy.value = true
  try {
    await axios.delete(`/storage/custom-tables/${encodeURIComponent(name)}`, {
      params: { user_id: 'default' },
    })
    selectedItem.value = null
    previewData.value = null
    previewHasMore.value = false
    await loadItems()
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    window.alert(typeof detail === 'string' ? detail : '删除失败（系统表不可删除）')
  } finally {
    deleteCustomBusy.value = false
  }
}

function filenameFromDisposition(header: string | undefined, fallback: string): string {
  if (!header) return fallback
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (star?.[1]) {
    try { return decodeURIComponent(star[1]) } catch { /* ignore */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain?.[1] || fallback
}

async function exportTable(fmt: 'csv' | 'markdown') {
  if (!selectedItem.value || exportBusy.value) return
  const name = selectedItem.value.name || selectedItem.value.table_name
  if (!name) return
  exportBusy.value = true
  exportFormat.value = fmt
  try {
    const res = await axios.get(`/storage/export/${encodeURIComponent(name)}`, {
      params: {
        backend: activeBackend.value,
        format: fmt,
        limit: 50000,
      },
      responseType: 'blob',
    })
    const fallback = `${name}.${fmt === 'csv' ? 'csv' : 'md'}`
    const filename = filenameFromDisposition(res.headers?.['content-disposition'], fallback)
    const blob = new Blob([res.data], {
      type: fmt === 'csv' ? 'text/csv;charset=utf-8' : 'text/markdown;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    const truncated = String(res.headers?.['x-export-truncated'] || '') === '1'
    const rows = res.headers?.['x-export-rows']
    if (truncated) {
      window.alert(`已导出 ${rows || ''} 行（超过上限已截断）。大表请缩小范围或分批导出。`)
    }
  } catch (e: any) {
    let msg = e?.message || '导出失败'
    const data = e?.response?.data
    if (data instanceof Blob) {
      try {
        const text = await data.text()
        const j = JSON.parse(text)
        if (typeof j?.detail === 'string') msg = j.detail
      } catch { /* ignore */ }
    } else if (typeof data?.detail === 'string') {
      msg = data.detail
    }
    window.alert(msg)
  } finally {
    exportBusy.value = false
  }
}

function formatRowCount(n: number): string {
  if (n < 0) return '-'
  if (n >= 1_0000_0000) return (n / 1_0000_0000).toFixed(1) + '亿'
  if (n >= 1_0000) return (n / 1_0000).toFixed(1) + '万'
  return n.toLocaleString()
}

function formatCell(val: any): string {
  if (val === null || val === undefined) return 'NULL'
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'object') return JSON.stringify(val).substring(0, 60)
  return String(val)
}

watch(currentTab, (tab) => {
  if (tab === 2) loadLlmTests()
})

onMounted(async () => {
  await Promise.all([loadStorageStatus(), loadItems(), loadLlmTests()])
})
</script>
