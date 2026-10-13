# lab-agent 前端

智能实验室预约系统的前端工程，基于 Vue 3 + Vite + Element Plus 构建。

## 技术栈

| 类别 | 选型 |
|---|---|
| 框架 | Vue 3（Composition API，`<script setup>`） |
| 构建 | Vite |
| UI | Element Plus |
| 路由 | Vue Router |
| 请求 | Axios（统一封装于 `src/utils/request.js`，自动注入 JWT） |
| Markdown 渲染 | marked + DOMPurify（用于 AI 助手回答） |

## 目录结构

```
src/
├── api/          # 接口封装，按业务模块拆分
├── assets/       # 全局样式与静态资源
├── components/   # 公共组件（如预约弹窗）
├── layouts/      # 页面布局
├── router/       # 路由配置与导航守卫
├── utils/        # 请求封装、鉴权、用户信息等工具
└── views/        # 业务页面
```

## 开发

```bash
npm install
npm run dev      # 启动开发服务器，默认 http://localhost:5173
npm run build    # 生产构建
npm run preview  # 预览构建产物
```

开发环境下 `/api` 与 `/uploads` 前缀会由 Vite 代理转发到后端 `http://127.0.0.1:8000`
（见 `vite.config.js`），启动前请先确保后端服务已运行。
如需指定其他后端地址，可通过环境变量 `VITE_API_BASE_URL` 覆盖请求基地址。

## 代码规范

使用 Prettier 格式化，配置见 `.prettierrc.json`：

```bash
npm run format
```
