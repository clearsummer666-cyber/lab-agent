<template>
  <div>
    <!-- 顶部：四张统计卡 -->
    <el-row :gutter="12">
      <el-col :span="6" v-for="c in cards" :key="c.label">
        <el-card shadow="never" class="stat-card">
          <div class="stat-label">{{ c.label }}</div>
          <div class="stat-value">
            {{ c.value }}<span class="stat-unit">{{ c.unit }}</span>
          </div>
          <div class="stat-sub">{{ c.sub }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 中部：趋势 + 工具排行 -->
    <el-row :gutter="12" style="margin-top: 12px">
      <el-col :span="15">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span>调用趋势</span>
              <el-radio-group v-model="days" size="small" @change="loadAll">
                <el-radio-button :value="7">近 7 天</el-radio-button>
                <el-radio-button :value="14">近 14 天</el-radio-button>
                <el-radio-button :value="30">近 30 天</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <div class="chart">
            <div class="chart-col" v-for="d in stats.by_day" :key="d.date">
              <div class="bar-wrap">
                <div
                  class="bar"
                  :class="{ 'bar-fail': d.failed > 0 }"
                  :style="{ height: barHeight(d.total) }"
                  :title="`${d.date}：${d.total} 次调用 / ${d.tokens} token / 失败 ${d.failed}`"
                />
              </div>
              <div class="chart-label">{{ d.date }}</div>
            </div>
          </div>
          <div class="chart-tip">
            柱子高度 = 当天调用次数；红色表示当天有失败。悬停看 token 与失败数。
          </div>
        </el-card>
      </el-col>

      <el-col :span="9">
        <el-card shadow="never">
          <template #header>工具调用排行</template>
          <div v-if="!stats.top_tools.length" class="empty-tip">暂无数据</div>
          <div v-for="t in stats.top_tools" :key="t.name" class="tool-row">
            <span class="tool-name">{{ toolLabel(t.name) }}</span>
            <div class="tool-bar-bg">
              <div class="tool-bar" :style="{ width: toolWidth(t.count) }" />
            </div>
            <span class="tool-count">{{ t.count }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 底部：调用日志 -->
    <el-card shadow="never" style="margin-top: 12px">
      <template #header>
        <div class="card-head">
          <span>调用日志</span>
          <div>
            <el-select
              v-model="params.status"
              placeholder="全部状态"
              clearable
              size="small"
              style="width: 120px; margin-right: 8px"
              @change="handleSearch"
            >
              <el-option :value="0" label="成功" />
              <el-option :value="1" label="失败" />
            </el-select>
            <el-input
              v-model="params.keyword"
              placeholder="搜索提问内容"
              clearable
              size="small"
              style="width: 200px"
              @keyup.enter="handleSearch"
              @clear="handleSearch"
            />
            <el-button type="primary" size="small" @click="handleSearch">查询</el-button>
          </div>
        </div>
      </template>

      <el-table
        :header-cell-style="{ backgroundColor: '#f5f7fa', color: '#333' }"
        :data="tableData"
        style="width: 100%"
        v-loading="loading"
      >
        <el-table-column prop="create_time" label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.create_time) }}</template>
        </el-table-column>
        <el-table-column prop="question" label="用户提问" show-overflow-tooltip />
        <el-table-column label="工具调用" width="220">
          <template #default="{ row }">
            <el-tag v-if="row.tool_count === 0" size="small" type="info">未调工具</el-tag>
            <el-tag
              v-for="name in splitTools(row.tool_calls)"
              :key="name"
              size="small"
              style="margin-right: 4px"
            >
              {{ toolLabel(name) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="118">
          <template #default="{ row }">
            <div>{{ row.duration_ms }} ms</div>
            <div class="split-tip">
              模 {{ row.model_ms }} / 工 {{ row.tool_ms }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="token" width="110">
          <template #default="{ row }">
            {{ row.input_tokens }} / {{ row.output_tokens }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 0 ? 'success' : 'danger'" size="small">
              {{ row.status === 0 ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button type="primary" text bg size="small" @click="openDetail(row)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        style="margin-top: 12px; justify-content: flex-end"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="params.page_size"
        :current-page="params.page"
        @current-change="handlePageChange"
      />
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawerVisible" title="调用详情" size="440px">
      <el-descriptions v-if="detail" :column="1" border>
        <el-descriptions-item label="记录 ID">{{ detail.id }}</el-descriptions-item>
        <el-descriptions-item label="用户 ID">{{ detail.user_id }}</el-descriptions-item>
        <el-descriptions-item label="会话 ID">{{ detail.thread_id }}</el-descriptions-item>
        <el-descriptions-item label="时间">
          {{ formatTime(detail.create_time) }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="detail.status === 0 ? 'success' : 'danger'" size="small">
            {{ detail.status === 0 ? '成功' : '失败' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="耗时">
          {{ detail.duration_ms }} ms（模型 {{ detail.model_ms }} ms · 工具
          {{ detail.tool_ms }} ms · 其他
          {{ Math.max(0, detail.duration_ms - detail.model_ms - detail.tool_ms) }} ms）
        </el-descriptions-item>
        <el-descriptions-item label="token">
          输入 {{ detail.input_tokens }} / 输出 {{ detail.output_tokens }}（合计
          {{ detail.input_tokens + detail.output_tokens }}）
        </el-descriptions-item>
        <el-descriptions-item label="工具序列">
          <span v-if="!detail.tool_calls">未调用工具</span>
          <div v-else>
            <el-tag
              v-for="(name, i) in splitTools(detail.tool_calls)"
              :key="i"
              size="small"
              style="margin: 0 4px 4px 0"
            >
              {{ i + 1 }}. {{ toolLabel(name) }}
            </el-tag>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="用户提问">{{ detail.question }}</el-descriptions-item>
        <el-descriptions-item v-if="detail.error" label="失败原因">
          <span style="color: #f56c6c">{{ detail.error }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { getAgentRunDetailApi, getAgentRunsApi, getAgentStatsApi } from '@/api/agentAdmin'

// 与后端 agent_service.TOOL_LABELS 保持一致；后端新增工具时这里也要加
const TOOL_LABELS = {
  search_lab_docs: '检索知识库',
  list_open_labs: '查开放实验室',
  list_lab_equipments: '查设备',
  list_availability: '查空闲时段',
  create_lab_reservation: '提交预约',
  get_today: '取当前日期',
  list_my_reservations: '查我的预约',
  cancel_my_reservation: '取消预约',
  save_slot: '记录槽位'
}

const days = ref(7)
const loading = ref(false)
const stats = reactive({
  total: 0,
  success: 0,
  failed: 0,
  success_rate: 0,
  avg_duration_ms: 0,
  avg_model_ms: 0,
  avg_tool_ms: 0,
  total_tokens: 0,
  avg_tokens: 0,
  by_day: [],
  top_tools: []
})

const tableData = ref([])
const total = ref(0)
const params = reactive({ page: 1, page_size: 10, status: null, keyword: '' })

const drawerVisible = ref(false)
const detail = ref(null)

const cards = computed(() => [
  {
    label: '总调用',
    value: stats.total,
    unit: ' 次',
    sub: `成功 ${stats.success} / 失败 ${stats.failed}`
  },
  {
    label: '成功率',
    value: stats.success_rate,
    unit: ' %',
    sub: '失败也要记账，否则看不见问题'
  },
  {
    label: '平均耗时',
    value: stats.avg_duration_ms,
    unit: ' ms',
    sub: `模型 ${stats.avg_model_ms} · 工具 ${stats.avg_tool_ms}`
  },
  {
    label: '总 token',
    value: stats.total_tokens,
    unit: '',
    sub: `平均每次 ${stats.avg_tokens}`
  }
])

// 柱状图最高值，用来算百分比高度；全 0 时给 1 防止除零
const maxDay = computed(() => Math.max(1, ...stats.by_day.map((d) => d.total)))
const maxTool = computed(() => Math.max(1, ...stats.top_tools.map((t) => t.count)))

function barHeight(n) {
  return `${Math.max(2, Math.round((n / maxDay.value) * 100))}%`
}
function toolWidth(n) {
  return `${Math.max(6, Math.round((n / maxTool.value) * 100))}%`
}
function toolLabel(name) {
  return TOOL_LABELS[name] || name
}
function splitTools(text) {
  if (!text) return []
  return text.split(',').filter(Boolean)
}
function formatTime(t) {
  if (!t) return '-'
  return String(t).replace('T', ' ').slice(0, 19)
}

async function loadStats() {
  const res = await getAgentStatsApi(days.value)
  if (res.code === 200) {
    Object.assign(stats, res.data)
  }
}

async function loadRuns() {
  loading.value = true
  try {
    const res = await getAgentRunsApi({
      page: params.page,
      page_size: params.page_size,
      status: params.status ?? undefined,
      keyword: params.keyword || undefined
    })
    if (res.code === 200) {
      tableData.value = res.data.list
      total.value = res.data.total
    }
  } finally {
    loading.value = false
  }
}

function loadAll() {
  loadStats()
  loadRuns()
}
function handleSearch() {
  params.page = 1
  loadRuns()
}
function handlePageChange(page) {
  params.page = page
  loadRuns()
}

async function openDetail(row) {
  const res = await getAgentRunDetailApi(row.id)
  if (res.code === 200) {
    detail.value = res.data
    drawerVisible.value = true
  }
}

onMounted(loadAll)
</script>

<style scoped>
.stat-card {
  border-radius: 8px;
}
.stat-label {
  font-size: 13px;
  color: #909399;
}
.stat-value {
  font-size: 26px;
  font-weight: 600;
  color: #16324f;
  margin: 6px 0 4px;
  line-height: 1.1;
}
.stat-unit {
  font-size: 13px;
  font-weight: 400;
  color: #909399;
}
.stat-sub {
  font-size: 12px;
  color: #b0b3b8;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
}

/* 柱状图：纯 CSS，不引图表库（避免给项目加依赖） */
.chart {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 150px;
  padding: 0 4px;
}
.chart-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}
.bar-wrap {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
}
.bar {
  width: 100%;
  background: linear-gradient(180deg, #4a90d9, #2b5f8a);
  border-radius: 3px 3px 0 0;
  transition: height 0.3s;
}
.bar-fail {
  background: linear-gradient(180deg, #f0a0a0, #d9534f);
}
.chart-label {
  font-size: 11px;
  color: #909399;
  margin-top: 6px;
  white-space: nowrap;
}
.chart-tip {
  font-size: 12px;
  color: #b0b3b8;
  margin-top: 8px;
}

/* 工具排行 */
.tool-row {
  display: flex;
  align-items: center;
  margin-bottom: 9px;
  font-size: 12px;
}
.tool-name {
  width: 82px;
  color: #606266;
  flex-shrink: 0;
}
.tool-bar-bg {
  flex: 1;
  height: 10px;
  background: #f0f2f5;
  border-radius: 5px;
  overflow: hidden;
}
.tool-bar {
  height: 100%;
  background: linear-gradient(90deg, #7fb3e0, #2b5f8a);
  border-radius: 5px;
}
.tool-count {
  width: 28px;
  text-align: right;
  color: #909399;
  flex-shrink: 0;
}
.empty-tip {
  color: #b0b3b8;
  font-size: 13px;
  padding: 20px 0;
  text-align: center;
}
.split-tip {
  font-size: 11px;
  color: #b0b3b8;
  line-height: 1.2;
}
</style>
