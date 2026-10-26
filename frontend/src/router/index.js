import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '@/utils/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/manager',
    },
    {
      path: '/manager',
      name: 'layout',
      component: () => import('@/layouts/layout.vue'),
      children: [
        {
          path: 'home',
          name: 'Home',
          component: () => import('@/views/HomeView.vue'),
        },
        {
          path: 'lab',
          name: 'Lab',
          component: () => import('@/views/LabView.vue'),
        },
        {
          path: 'equipment',
          name: 'Equipment',
          component: () => import('@/views/EquView.vue'),
        },
        {
          path: 'lablist',
          name: 'LabList',
          component: () => import('@/views/LabList.vue'),
        },
        {
          path: 'lab-equipment',
          name: 'LabEquipment',
          component: () => import('@/views/LabEquipment.vue'),
        },
        {
          path: 'user',
          name: 'User',
          component: () => import('@/views/User.vue'),
        },
        {
          path: 'my-reservation',
          name: 'MyReservation',
          component: () => import('@/views/MyReservation.vue')
        },
        {
          path: 'audit-reservation',
          name: 'AuditReservation',
          component: () => import('@/views/AuditReservation.vue')
        },
        {
          path: 'ai-chat',
          name: 'AIChat',
          component: () => import('@/views/AIChat.vue')
        },
        {
          // Agent 运营后台：调用日志 / token 成本 / 任务状态 / 效果评估
          path: 'agent-admin',
          name: 'AgentAdmin',
          component: () => import('@/views/AgentAdmin.vue')
        }
      ],
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/LoginView.vue'),
    },
    // 2026-10-26 公网部署：关闭自助注册（后端 /api/auth/register 也加了管理员鉴权）。
    // 保留这个跳转，避免老书签 / 外部链接点进来是空白页。
    {
      path: '/register',
      redirect: '/login',
    },
  ],
})

// 路由守卫：验证 token
router.beforeEach((to, from) => {
  const token = getToken()

  if (to.path === '/login') {
    return true
  } else {
    return token ? true : '/login'
  }
})

export default router
