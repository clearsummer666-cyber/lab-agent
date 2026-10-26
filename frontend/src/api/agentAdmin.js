import request from '@/utils/request'

/** 运营后台统计：四张卡 + 按天趋势 + 工具排行 */
export function getAgentStatsApi(days = 7) {
  return request({
    url: '/api/agent-admin/stats',
    method: 'get',
    params: { days }
  })
}

/** 调用日志分页列表 */
export function getAgentRunsApi(params) {
  return request({
    url: '/api/agent-admin/runs',
    method: 'get',
    params
  })
}

/** 单条调用详情 */
export function getAgentRunDetailApi(runId) {
  return request({
    url: `/api/agent-admin/runs/${runId}`,
    method: 'get'
  })
}
