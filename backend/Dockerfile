# ============ lab-agent 后端镜像 ============
# FastAPI + Uvicorn + MySQL(PyMySQL) + Chroma 向量库 + sentence-transformers
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    TZ=Asia/Shanghai \
    # 国内服务器直连 huggingface.co 基本不通，向量模型走镜像站下载
    HF_ENDPOINT=https://hf-mirror.com

WORKDIR /app

# chromadb / sentence-transformers 的部分传递依赖在 slim 镜像里没有预编译 wheel，
# 需要 gcc 现场编译；curl 留给容器健康检查用。
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单，命中 Docker 层缓存：改代码不会重装依赖
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    # 隐性依赖：文件上传接口（UploadFile）必须有它，教程漏写了
    && pip install python-multipart

COPY . .

# 运行期目录（compose 里挂卷持久化，这里先建好避免权限/路径问题）
RUN mkdir -p /app/uploads /app/data/chroma

EXPOSE 8000

# --workers 1 是刻意的：kb_service 里 embedding 模型和 Chroma 客户端是进程内单例，
# 多 worker 会让每个进程各加载一份模型，内存直接翻倍。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
