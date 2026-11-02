<template>
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-size: 16px; font-weight: bold">AI智能助手</span>
          <div style="display: flex; align-items: center; gap: 8px">
            <!-- 本次登录的剩余额度：重新登录会刷新（额度按登录会话计，不是按天） -->
            <span v-if="quota && !quota.is_admin" style="font-size: 12px; color: #909399">
              本次登录剩余 {{ quota.run_left }} 次 / {{ quota.token_left }} token
            </span>
            <span v-else-if="quota && quota.is_admin" style="font-size: 12px; color: #909399">
              管理员不限额度
            </span>
            <el-button size="small" :disabled="loading" @click="handleNewChat">新对话</el-button>
          </div>
        </div>
      </template>

      <div
        ref="listRef"
        style="
          height: 480px;
          overflow-y: auto;
          padding: 8px 4px;
          background-color: #fafafa;
          border-radius: 6px;
        "
      >
        <div
          v-for="(item, index) in messages"
          :key="index"
          :style="{
            display: 'flex',
            justifyContent: item.role === 'user' ? 'flex-end' : 'flex-start',
            marginBottom: '12px'
          }"
        >
          <div
            :style="{
              maxWidth: '70%',
              padding: '10px 12px',
              borderRadius: '8px',
              backgroundColor: item.role === 'user' ? '#409eff' : '#fff',
              color: item.role === 'user' ? '#fff' : '#333',
              lineHeight: '1.6',
              boxShadow: '0 1px 2px rgba(0, 0, 0, 0.06)'
            }"
          >
            <div
              v-if="item.role === 'assistant' && item.status"
              style="margin-bottom: 6px; font-size: 12px; color: #909399"
            >
              {{ item.status }}
            </div>

            <div
              v-if="item.role === 'assistant' && item.steps?.length"
              style="margin-bottom: 8px; font-size: 12px; color: #909399; line-height: 1.6"
            >
              <div v-for="(step, i) in item.steps" :key="i">· {{ step }}</div>
            </div>
            <div v-html="parseMarkdown(item.content)"></div>
          </div>
        </div>
      </div>

      <div style="margin-top: 12px; display: flex; gap: 8px; align-items: flex-end">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          placeholder="问问实验室怎么预约、开放时间..."
          @keydown.enter.exact.prevent="handleSend"
          @keydown.enter.shift.stop
        ></el-input>
        <el-button type="primary" @click="handleSend" :loading="loading">发送</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { chatStreamApi, getQuotaApi, resetSessionApi } from '@/api/ai'
import { ref, reactive, nextTick, onMounted, onActivated, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify' // 过滤危险的 HTML，防止 XSS 攻击

// keep-alive 的 include 认的是组件名，<script setup> 必须显式声明
defineOptions({ name: 'AIChat' })

const STORAGE_KEY = 'labpilot:ai-chat'
const WELCOME = {
  role: 'assistant',
  content:
    '你好，我是实验室预约助手。可以问规则和开放实验室，也可以说「帮我预约明天下午的实验室」'
}

// 进页面时先尝试从 sessionStorage 恢复，没有就用欢迎语
const loadMessages = () => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return [{ ...WELCOME }]
    const saved = JSON.parse(raw)
    if (!Array.isArray(saved) || !saved.length) return [{ ...WELCOME }]
    // 恢复时把过程态清掉，避免残留「正在…」和旧的工具流水
    return saved.map((m) => ({
      role: m.role,
      content: m.content || '',
      status: '',
      steps: []
    }))
  } catch {
    return [{ ...WELCOME }]
  }
}

const messages = ref(loadMessages())
const input = ref('')
const loading = ref(false)
const listRef = ref()
// 今日额度（后端按 agent_runs 当天聚合算的）
const quota = ref(null)

const refreshQuota = async () => {
  try {
    const res = await getQuotaApi()
    quota.value = res.data
  } catch {
    // 额度接口失败不影响聊天，静默忽略
    quota.value = null
  }
}

// 落盘：只存 role/content，截断单条 + 限制条数，防 sessionStorage 超限
const persist = () => {
  try {
    const plain = messages.value
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map(({ role, content }) => ({ role, content: (content || '').slice(0, 4000) }))
      .slice(-50)
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(plain))
  } catch {
    // 存不进去就算了，不影响对话
  }
}

// 流式输出时每个 token 都会改 messages，同步写会卡，所以流式中跳过；
// 一轮结束在 finally 里补存一次。
watch(messages, () => {
  if (!loading.value) persist()
}, { deep: true })

onMounted(() => {
  refreshQuota()
  scrollToBottom()
})

// 被 keep-alive 缓存后 onMounted 只跑一次，切回来走这里补一次滚动和额度刷新
onActivated(() => {
  scrollToBottom()
  refreshQuota()
})

const scrollToBottom = () => {
  nextTick(() => {
    const elem = listRef.value
    if (elem) elem.scrollTop = elem.scrollHeight
  })
}

const handleSend = async () => {
  const text = input.value.trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', content: text })
  input.value = ''
  loading.value = true
  scrollToBottom()

  // 必须用 reactive：普通对象 push 后再改字段，视图可能不更新
  const assistant = reactive({
    role: 'assistant',
    content: '', // 正文：靠 token 一点点拼
    status: '正在思考…', // 当前状态文案
    steps: [] // 过程流水，不进下次 history
  })
  messages.value.push(assistant)

  const history = messages.value
    .filter((item) => item.role === 'user' || item.role === 'assistant')
    .slice(0, -1) // 去掉刚插入的空助手，避免把空 content 送回去
    // 只保留 role/content；status、steps 丢掉
    .map(({ role, content }) => ({ role, content }))

  try {
    // 第二个参数是回调：每解析出一条 SSE 事件就进一次
    await chatStreamApi({ messages: history }, (evt) => {
      if (evt.type === 'status') {
        assistant.status = evt.message || '正在思考…'
      } else if (evt.type === 'tool_start') {
        const label = evt.label || evt.name || '工具'
        assistant.status = `正在${label}…`
        assistant.steps.push(`开始：${label}`)
      } else if (evt.type === 'tool_end') {
        const label = evt.label || evt.name || '工具'
        assistant.status = `${label}完成`
        assistant.steps.push(`完成：${label}`)
      } else if (evt.type === 'token') {
        assistant.content += evt.content || '' // 打字机：追加，不是覆盖
        assistant.status = '' // 开始出字后清掉「正在…」，避免叠在一起
      } else if (evt.type === 'done') {
        assistant.status = ''
        // 注意：不要在这里再 append 一整段正文，否则会双份
      } else if (evt.type === 'error') {
        assistant.status = ''
        if (!assistant.content) {
          assistant.content = evt.message || '请求失败'
        }
        ElMessage.error(evt.message || '请求失败')
      }
      scrollToBottom()
    })
  } catch (err) {
    assistant.status = ''
    if (!assistant.content) {
      assistant.content = err.message || '网络异常'
    }
    ElMessage.error(err.message || '网络异常')
  } finally {
    loading.value = false
    assistant.status = ''
    persist() // 流式期间 watch 跳过了，这里补存一次最终结果
    scrollToBottom()
    refreshQuota() // 每轮结束刷新剩余额度（后端是唯一账本，不在前端自己减）
  }
}

// 开启新对话：后端 session_no +1，前端回到欢迎语（流式进行中按钮已禁用）
const handleNewChat = async () => {
  try {
    await resetSessionApi()
    messages.value = [{ ...WELCOME }]
    persist()
    input.value = ''
    ElMessage.success('已开启新对话')
  } catch (err) {
    ElMessage.error(err.message || '开启新对话失败')
  }
}

// 将 markdown 字符串转换为安全的 HTML 字符串
const parseMarkdown = (text) => {
  if (!text) return ''
  marked.setOptions({ breaks: true }) // 把 \n 转换成 <br>
  // 使用 marked 解析，然后用 DOMPurify 清理
  const rawHtml = marked.parse(text)
  return DOMPurify.sanitize(rawHtml)
}
</script>
