# ============ 阶段一：构建前端静态文件 ============
FROM node:24-alpine AS builder

WORKDIR /app

# 国内服务器拉 npm 包走镜像站
RUN npm config set registry https://registry.npmmirror.com

# 先装依赖，命中层缓存
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# 生产环境前后端同源（Nginx 反代 /api、/uploads），baseURL 留空即可，
# 这样 axios 请求的是相对路径，不需要跨域配置。
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build


# ============ 阶段二：Nginx 托管静态文件 + 反向代理后端 ============
FROM nginx:1.27-alpine

# 官方镜像启动时会用 envsubst 把 /etc/nginx/templates/*.template
# 渲染成 /etc/nginx/conf.d/*.conf，域名从环境变量注入。
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
