"""
AI Assistant Backend Service
- 记忆存储与语义搜索
- 闹钟管理
- 主动思考协程
"""
import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx

load_dotenv()

# ============ 配置 ============
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

supabase: Client = None
scheduler = AsyncIOScheduler()

# ============ Pydantic Models ============
class MemoryCreate(BaseModel):
    content: str
    type: str = "chat"  # chat, event, note 等
    metadata: Optional[dict] = None
    is_important: bool = False

class MemorySearch(BaseModel):
    query: str
    limit: int = 10
    type: Optional[str] = None  # 可选过滤类型

class ReminderCreate(BaseModel):
    user_id: str
    content: str
    remind_at: datetime
    repeat: Optional[str] = None  # "daily", "weekly", "monthly", None

class ReminderUpdate(BaseModel):
    content: Optional[str] = None
    remind_at: Optional[datetime] = None
    repeat: Optional[str] = None
    is_done: Optional[bool] = None

class ProactiveMessageRequest(BaseModel):
    user_id: str
    role_persona: str
    recent_memories: Optional[List[str]] = None
    user_status: Optional[dict] = None  # 位置、失联时长等

# ============ 工具函数 ============
async def get_embedding(text: str) -> Optional[List[float]]:
    """获取文本的embedding向量，如果失败返回None"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{OPENAI_BASE_URL}/embeddings",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": EMBEDDING_MODEL, "input": text},
                timeout=30.0
            )
            if resp.status_code != 200:
                print(f"⚠️ Embedding API失败: {resp.status_code} {resp.text}")
                return None
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        print(f"⚠️ Embedding异常: {e}")
        return None

async def call_ai(system_prompt: str, user_message: str = "") -> str:
    """调用AI生成回复"""
    async with httpx.AsyncClient() as client:
        messages = [{"role": "system", "content": system_prompt}]
        if user_message:
            messages.append({"role": "user", "content": user_message})
        
        resp = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": OPENAI_MODEL, "messages": messages},
            timeout=30.0
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()
        return data["choices"][0]["message"]["content"]

# ============ 生命周期 ============
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase
    # 启动时
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase connected")
    else:
        print("⚠️ Supabase not configured")
    
    # 启动定时任务
    scheduler.add_job(check_reminders, 'interval', minutes=1, id='reminder_checker')
    scheduler.start()
    print("✅ Scheduler started")
    
    yield
    
    # 关闭时
    scheduler.shutdown()
    print("🛑 Scheduler stopped")

app = FastAPI(title="AI Assistant Backend", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 记忆功能 ============
@app.post("/memory/store")
async def store_memory(memory: MemoryCreate):
    """存储记忆（适配现有表结构）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 存入数据库（适配现有表结构：id, created_at, type, content, metadata, is_important）
    data = {
        "type": memory.type,
        "content": memory.content,
        "metadata": memory.metadata or {},
        "is_important": memory.is_important
    }
    
    result = supabase.table("memories").insert(data).execute()
    return {"success": True, "id": result.data[0]["id"] if result.data else None}

@app.post("/memory/search")
async def search_memory(search: MemorySearch):
    """搜索记忆（关键词搜索）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 使用ilike进行文本匹配
    query = supabase.table("memories").select("*")
    if search.type:
        query = query.eq("type", search.type)
    result = query.ilike("content", f"%{search.query}%").order("created_at", desc=True).limit(search.limit).execute()
    
    return {"memories": result.data}

# ============ 闹钟功能 ============
@app.post("/reminder/create")
async def create_reminder(reminder: ReminderCreate):
    """创建闹钟"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    data = {
        "user_id": reminder.user_id,
        "content": reminder.content,
        "remind_at": reminder.remind_at.isoformat(),
        "repeat": reminder.repeat,
        "is_done": False,
        "created_at": datetime.utcnow().isoformat()
    }
    
    result = supabase.table("reminders").insert(data).execute()
    return {"success": True, "id": result.data[0]["id"] if result.data else None}

@app.get("/reminder/list/{user_id}")
async def list_reminders(user_id: str, include_done: bool = False):
    """获取用户的闹钟列表"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    query = supabase.table("reminders").select("*").eq("user_id", user_id)
    if not include_done:
        query = query.eq("is_done", False)
    
    result = query.order("remind_at").execute()
    return {"reminders": result.data}

@app.put("/reminder/{reminder_id}")
async def update_reminder(reminder_id: str, update: ReminderUpdate):
    """更新闹钟"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    data = {k: v for k, v in update.dict().items() if v is not None}
    if "remind_at" in data:
        data["remind_at"] = data["remind_at"].isoformat()
    
    result = supabase.table("reminders").update(data).eq("id", reminder_id).execute()
    return {"success": True}

@app.delete("/reminder/{reminder_id}")
async def delete_reminder(reminder_id: str):
    """删除闹钟"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    supabase.table("reminders").delete().eq("id", reminder_id).execute()
    return {"success": True}

# ============ 主动思考 ============
@app.post("/proactive/generate")
async def generate_proactive_message(req: ProactiveMessageRequest):
    """生成主动消息"""
    hour = datetime.now().hour
    time_context = ""
    if hour < 6:
        time_context = "现在是凌晨"
    elif hour < 9:
        time_context = "现在是早上"
    elif hour < 12:
        time_context = "现在是上午"
    elif hour < 14:
        time_context = "现在是中午"
    elif hour < 18:
        time_context = "现在是下午"
    elif hour < 22:
        time_context = "现在是晚上"
    else:
        time_context = "现在是深夜"
    
    memories_context = ""
    if req.recent_memories:
        memories_context = f"最近的记忆：\n" + "\n".join(req.recent_memories)
    
    status_context = ""
    if req.user_status:
        if "last_active" in req.user_status:
            status_context += f"用户上次活跃：{req.user_status['last_active']}\n"
        if "location" in req.user_status:
            status_context += f"用户位置：{req.user_status['location']}\n"
    
    system_prompt = f"""你是用户的AI助手，以下是你的角色设定：
{req.role_persona}

{time_context}
{memories_context}
{status_context}

请用你的角色人设和语气，生成一条简短的主动问候或关心的话（15-30字左右）。
要自然、温暖、符合当前时间和上下文。不要太正式。
只输出问候语本身，不要加引号或其他格式。"""

    message = await call_ai(system_prompt)
    return {"message": message.strip()}

# ============ 后台任务 ============
async def check_reminders():
    """每分钟检查到期的闹钟"""
    if not supabase:
        return
    
    now = datetime.utcnow()
    
    # 查询到期的闹钟
    result = supabase.table("reminders")\
        .select("*")\
        .eq("is_done", False)\
        .lte("remind_at", now.isoformat())\
        .execute()
    
    for reminder in result.data:
        # TODO: 发送提醒到前端（可以用WebSocket或轮询）
        print(f"⏰ 提醒到期: {reminder['content']}")
        
        # 处理重复闹钟
        if reminder.get("repeat"):
            next_time = None
            remind_at = datetime.fromisoformat(reminder["remind_at"])
            
            if reminder["repeat"] == "daily":
                next_time = remind_at + timedelta(days=1)
            elif reminder["repeat"] == "weekly":
                next_time = remind_at + timedelta(weeks=1)
            elif reminder["repeat"] == "monthly":
                next_time = remind_at + timedelta(days=30)
            
            if next_time:
                supabase.table("reminders").update({
                    "remind_at": next_time.isoformat()
                }).eq("id", reminder["id"]).execute()
        else:
            # 标记为完成
            supabase.table("reminders").update({
                "is_done": True
            }).eq("id", reminder["id"]).execute()

# ============ 健康检查 ============
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "supabase": "connected" if supabase else "not configured",
        "time": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
