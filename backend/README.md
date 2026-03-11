# AI Assistant Backend

后端服务，提供记忆存储、闹钟管理、主动思考等功能。

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

需要配置：
- `SUPABASE_URL` - Supabase 项目 URL
- `SUPABASE_KEY` - Supabase anon key
- `OPENAI_API_KEY` - OpenAI API Key
- `OPENAI_BASE_URL` - API Base URL（可选，默认 OpenAI）

### 3. 初始化数据库

在 Supabase SQL Editor 中执行 `supabase_schema.sql`

### 4. 本地运行

```bash
python main.py
# 或
uvicorn main:app --reload --port 8000
```

访问 http://localhost:8000/docs 查看 API 文档

## API 接口

### 记忆功能
- `POST /memory/store` - 存储记忆
- `POST /memory/search` - 语义搜索记忆

### 闹钟功能
- `POST /reminder/create` - 创建闹钟
- `GET /reminder/list/{user_id}` - 获取闹钟列表
- `PUT /reminder/{reminder_id}` - 更新闹钟
- `DELETE /reminder/{reminder_id}` - 删除闹钟

### 主动思考
- `POST /proactive/generate` - 生成主动消息

### 健康检查
- `GET /health` - 服务状态

## 部署到 Render

1. 将代码推送到 GitHub
2. 在 Render 创建 Web Service，连接仓库
3. 设置环境变量
4. 部署完成后获取公网 URL

## 后台任务

- **闹钟检查**: 每分钟检查到期的闹钟
- **通知总结**: 每30分钟总结手机通知（待实现）
- **主动思考**: 随机时间主动发消息（前端控制）
