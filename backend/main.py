"""
AI Assistant Backend Service
- 记忆存储与语义搜索
- 闹钟管理
- 主动思考协程
"""
import os
import re
import json
import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager
from urllib.parse import quote

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

class WebSearchRequest(BaseModel):
    query: str
    num_results: int = 5

class ExpenseCreate(BaseModel):
    amount: float
    category: str  # food, transport, shopping, entertainment, other
    description: str
    date: Optional[str] = None  # ISO格式，默认今天

class CalendarEventCreate(BaseModel):
    title: str
    start_time: datetime
    end_time: Optional[datetime] = None
    description: Optional[str] = None
    is_all_day: bool = False

# iOS快捷指令接口模型
class WechatData(BaseModel):
    app: str = "微信"  # App名称
    content: str  # 屏幕文字内容
    sender: Optional[str] = None  # 发送者（如果能识别）
    screenshot_base64: Optional[str] = None  # 截图Base64（可选）

class GPSData(BaseModel):
    latitude: float
    longitude: float
    address: Optional[str] = None  # 地址描述
    battery: Optional[int] = None  # 电量百分比
    app: Optional[str] = None  # 当前运行的App
    screen_on: bool = True  # 屏幕是否亮着

class BarkPush(BaseModel):
    title: str
    body: str
    url: Optional[str] = None  # 点击跳转URL
    sound: Optional[str] = "shake"  # 铃声：shake, alarm, etc
    is_archive: bool = True  # 是否保存通知
    group: Optional[str] = None  # 通知分组

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
    scheduler.add_job(summarize_notifications, 'interval', minutes=30, id='notification_summarizer')
    scheduler.add_job(proactive_thinking, 'interval', minutes=10, id='proactive_thinker')  # 基础间隔，内部有随机逻辑
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

# ============ 消息总结协程 ============
async def summarize_notifications():
    """每30分钟总结App_Pending通知"""
    if not supabase:
        return
    
    try:
        # 查询待处理的通知
        result = supabase.table("notifications")\
            .select("*")\
            .contains("tags", ["App_Pending"])\
            .execute()
        
        if not result.data:
            print("📭 没有待处理的通知")
            return
        
        # 按app分组
        notifications_text = "\n".join([
            f"[{n.get('app_name', '未知')}] {n.get('title', '')}: {n.get('content', '')}"
            for n in result.data
        ])
        
        # 让AI生成总结
        summary = await call_ai(
            system_prompt="你是一个消息总结助手。请简洁地总结以下手机通知，提取重要信息，忽略广告和不重要的内容。用中文回复，控制在100字以内。",
            user_message=notifications_text
        )
        
        print(f"📋 通知总结: {summary}")
        
        # 存入memories
        supabase.table("memories").insert({
            "type": "notification_summary",
            "content": summary,
            "metadata": {"notification_count": len(result.data)},
            "is_important": False
        }).execute()
        
        # 标记通知为已处理
        for n in result.data:
            supabase.table("notifications").update({
                "tags": ["App_Done"],
                "processed_at": datetime.utcnow().isoformat()
            }).eq("id", n["id"]).execute()
        
        print(f"✅ 已处理 {len(result.data)} 条通知")
        
    except Exception as e:
        print(f"❌ 通知总结失败: {e}")

# ============ 主动思考协程 ============
# 存储上次主动思考的时间
last_proactive_time = None

async def proactive_thinking():
    """随机间隔主动思考，决定是否发送消息"""
    global last_proactive_time
    
    if not supabase:
        return
    
    try:
        # 随机决定是否这次执行（模拟2-30分钟随机间隔）
        if random.random() > 0.3:  # 70%概率跳过，实现随机间隔
            return
        
        now = datetime.utcnow()
        
        # 获取最近的记忆
        memories_result = supabase.table("memories")\
            .select("content, type, created_at")\
            .order("created_at", desc=True)\
            .limit(10)\
            .execute()
        
        recent_memories = [m["content"] for m in memories_result.data] if memories_result.data else []
        
        # 计算失联时长（最后一条chat类型记忆的时间）
        last_chat = None
        for m in memories_result.data:
            if m.get("type") == "chat":
                last_chat = m
                break
        
        hours_since_last_chat = 0
        if last_chat:
            last_chat_time = datetime.fromisoformat(last_chat["created_at"].replace("Z", "+00:00").replace("+00:00", ""))
            hours_since_last_chat = (now - last_chat_time).total_seconds() / 3600
        
        # 让AI决定是否发消息
        decision_prompt = f"""你是用户的AI助手。根据以下信息决定是否主动发消息：

当前时间: {now.strftime('%H:%M')}
用户失联时长: {hours_since_last_chat:.1f}小时
最近记忆: {'; '.join(recent_memories[:5]) if recent_memories else '无'}

规则：
- 如果是深夜(23:00-7:00)且用户没有活动，不要打扰
- 如果用户失联超过4小时且是白天，可以关心一下
- 如果有重要事项需要提醒，应该发消息
- 大多数情况下应该PASS，不要太频繁打扰

请回复：
- "PASS" 如果不需要发消息
- "MESSAGE: [你想说的话]" 如果决定发消息（15-30字）
- "LOCK" 如果建议用户休息/锁屏"""

        decision = await call_ai(decision_prompt)
        decision = decision.strip()
        
        print(f"🤔 主动思考决策: {decision[:50]}...")
        
        if decision.startswith("MESSAGE:"):
            message = decision[8:].strip()
            # 存入记忆，标记为主动消息
            supabase.table("memories").insert({
                "type": "proactive_message",
                "content": message,
                "metadata": {"hours_since_last_chat": hours_since_last_chat},
                "is_important": False
            }).execute()
            print(f"💬 主动消息已生成: {message}")
            # TODO: 通过WebSocket或轮询机制推送到前端
            
        elif decision == "LOCK":
            print("🔒 建议用户锁屏休息")
            
        else:
            print("⏭️ PASS - 不发送消息")
            
    except Exception as e:
        print(f"❌ 主动思考失败: {e}")

# ============ 获取待推送的主动消息 ============
@app.get("/proactive/pending")
async def get_pending_proactive_messages():
    """获取待推送的主动消息（前端轮询用）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 获取最近5分钟内的主动消息
    five_min_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    
    result = supabase.table("memories")\
        .select("*")\
        .eq("type", "proactive_message")\
        .gte("created_at", five_min_ago)\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    
    if result.data:
        return {"has_message": True, "message": result.data[0]["content"]}
    return {"has_message": False}

# ============ 联网搜索 ============
@app.post("/search/web")
async def web_search(req: WebSearchRequest):
    """使用DuckDuckGo进行联网搜索"""
    try:
        async with httpx.AsyncClient() as client:
            # DuckDuckGo HTML搜索（无需API key）
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": req.query},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=15.0
            )
            
            if resp.status_code != 200:
                return {"success": False, "error": f"搜索失败: {resp.status_code}"}
            
            # 简单解析HTML提取结果
            html = resp.text
            results = []
            
            # 提取搜索结果标题和摘要
            pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>.*?<a[^>]*class="result__snippet"[^>]*>([^<]*)</a>'
            matches = re.findall(pattern, html, re.DOTALL)
            
            for url, title, snippet in matches[:req.num_results]:
                results.append({
                    "title": title.strip(),
                    "url": url,
                    "snippet": snippet.strip()
                })
            
            # 如果正则没匹配到，尝试更简单的方式
            if not results:
                # 提取所有链接和文本
                link_pattern = r'<a[^>]*class="result__a"[^>]*>([^<]+)</a>'
                titles = re.findall(link_pattern, html)
                for title in titles[:req.num_results]:
                    results.append({"title": title.strip(), "url": "", "snippet": ""})
            
            return {"success": True, "results": results, "query": req.query}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

# ============ 记账功能 ============
@app.post("/expense/add")
async def add_expense(expense: ExpenseCreate):
    """添加一笔支出记录"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    date = expense.date or datetime.utcnow().strftime("%Y-%m-%d")
    
    # 存入memories表，type=expense
    data = {
        "type": "expense",
        "content": f"{expense.category}: {expense.description} - ¥{expense.amount}",
        "metadata": {
            "amount": expense.amount,
            "category": expense.category,
            "description": expense.description,
            "date": date
        },
        "is_important": False
    }
    
    result = supabase.table("memories").insert(data).execute()
    return {"success": True, "id": result.data[0]["id"] if result.data else None}

@app.get("/expense/summary")
async def get_expense_summary(days: int = 30):
    """获取支出统计"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
    
    result = supabase.table("memories")\
        .select("*")\
        .eq("type", "expense")\
        .gte("created_at", start_date)\
        .execute()
    
    # 按分类汇总
    summary = {}
    total = 0
    for item in result.data:
        meta = item.get("metadata", {})
        category = meta.get("category", "other")
        amount = meta.get("amount", 0)
        summary[category] = summary.get(category, 0) + amount
        total += amount
    
    return {
        "total": total,
        "by_category": summary,
        "count": len(result.data),
        "days": days
    }

# ============ 日历功能 ============
@app.post("/calendar/event")
async def create_calendar_event(event: CalendarEventCreate):
    """创建日历事件"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 存入memories表，type=calendar_event
    data = {
        "type": "calendar_event",
        "content": event.title,
        "metadata": {
            "title": event.title,
            "start_time": event.start_time.isoformat(),
            "end_time": event.end_time.isoformat() if event.end_time else None,
            "description": event.description,
            "is_all_day": event.is_all_day
        },
        "is_important": True
    }
    
    result = supabase.table("memories").insert(data).execute()
    
    # 同时创建一个reminder用于提醒
    reminder_data = {
        "user_id": "default_user",
        "content": f"📅 {event.title}" + (f": {event.description}" if event.description else ""),
        "remind_at": event.start_time.isoformat(),
        "is_done": False,
        "created_at": datetime.utcnow().isoformat()
    }
    supabase.table("reminders").insert(reminder_data).execute()
    
    return {"success": True, "id": result.data[0]["id"] if result.data else None}

@app.get("/calendar/events")
async def get_calendar_events(start_date: str = None, end_date: str = None):
    """获取日历事件"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 默认获取当月事件
    if not start_date:
        now = datetime.utcnow()
        start_date = now.replace(day=1).isoformat()
    if not end_date:
        now = datetime.utcnow()
        next_month = now.replace(day=28) + timedelta(days=4)
        end_date = next_month.replace(day=1).isoformat()
    
    result = supabase.table("memories")\
        .select("*")\
        .eq("type", "calendar_event")\
        .gte("created_at", start_date)\
        .lte("created_at", end_date)\
        .order("created_at")\
        .execute()
    
    events = []
    for item in result.data:
        meta = item.get("metadata", {})
        events.append({
            "id": item["id"],
            "title": meta.get("title", item["content"]),
            "start_time": meta.get("start_time"),
            "end_time": meta.get("end_time"),
            "description": meta.get("description"),
            "is_all_day": meta.get("is_all_day", False)
        })
    
    return {"events": events}

@app.get("/calendar/today")
async def get_today_schedule():
    """获取今日日程"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time()).isoformat()
    today_end = datetime.combine(today, datetime.max.time()).isoformat()
    
    # 获取今日事件
    events_result = supabase.table("memories")\
        .select("*")\
        .eq("type", "calendar_event")\
        .execute()
    
    # 获取今日提醒
    reminders_result = supabase.table("reminders")\
        .select("*")\
        .eq("is_done", False)\
        .gte("remind_at", today_start)\
        .lte("remind_at", today_end)\
        .order("remind_at")\
        .execute()
    
    # 过滤今日事件
    today_events = []
    for item in events_result.data:
        meta = item.get("metadata", {})
        start_time = meta.get("start_time", "")
        if start_time and start_time.startswith(str(today)):
            today_events.append({
                "id": item["id"],
                "title": meta.get("title", item["content"]),
                "start_time": start_time,
                "end_time": meta.get("end_time"),
                "type": "event"
            })
    
    # 合并提醒
    for reminder in reminders_result.data:
        today_events.append({
            "id": reminder["id"],
            "title": reminder["content"],
            "start_time": reminder["remind_at"],
            "type": "reminder"
        })
    
    # 按时间排序
    today_events.sort(key=lambda x: x.get("start_time", ""))
    
    return {"date": str(today), "schedule": today_events}

# ============ iOS快捷指令接口 ============
BARK_KEY = os.getenv("BARK_KEY", "")  # Bark推送Key

@app.post("/api/wechat")
async def receive_wechat_data(data: WechatData):
    """接收iOS快捷指令发送的屏幕内容/微信消息"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    print(f"📱 收到{data.app}数据: {data.content[:100]}...")
    
    # 存入memories表
    memory_data = {
        "type": "screen_capture",
        "content": data.content,
        "metadata": {
            "app": data.app,
            "sender": data.sender,
            "has_screenshot": bool(data.screenshot_base64),
            "captured_at": datetime.utcnow().isoformat()
        },
        "is_important": False
    }
    
    result = supabase.table("memories").insert(memory_data).execute()
    
    # 让AI分析内容，提取重要信息
    try:
        analysis = await call_ai(
            system_prompt="""你是一个信息分析助手。分析用户手机屏幕捕获的内容，提取重要信息。
如果发现以下类型的重要信息，请标注：
- 用户偏好（喜欢/讨厌什么）
- 待办事项或约定
- 账单/消费信息
- 重要联系人消息

用JSON格式回复：
{"important": true/false, "category": "preference/todo/expense/message/other", "summary": "简短总结", "action": "建议的后续动作（可选）"}

如果内容不重要（如广告、无意义内容），返回：{"important": false}""",
            user_message=f"App: {data.app}\n内容: {data.content}"
        )
        
        # 解析AI分析结果
        try:
            analysis_result = json.loads(analysis)
            if analysis_result.get("important"):
                # 存入重要记忆
                important_memory = {
                    "type": "user_insight",
                    "content": analysis_result.get("summary", data.content[:200]),
                    "metadata": {
                        "category": analysis_result.get("category"),
                        "source_app": data.app,
                        "action": analysis_result.get("action"),
                        "analyzed_at": datetime.utcnow().isoformat()
                    },
                    "is_important": True
                }
                supabase.table("memories").insert(important_memory).execute()
                print(f"⭐ 发现重要信息: {analysis_result.get('summary')}")
        except json.JSONDecodeError:
            pass
            
    except Exception as e:
        print(f"⚠️ AI分析失败: {e}")
    
    return {"success": True, "id": result.data[0]["id"] if result.data else None}

@app.post("/api/gps")
async def receive_gps_data(data: GPSData):
    """接收iOS快捷指令发送的位置数据"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    print(f"📍 收到位置: {data.latitude}, {data.longitude} | 电量: {data.battery}%")
    
    # 存入memories表（用type=gps_history区分）
    gps_data = {
        "type": "gps_history",
        "content": data.address or f"({data.latitude}, {data.longitude})",
        "metadata": {
            "latitude": data.latitude,
            "longitude": data.longitude,
            "address": data.address,
            "battery": data.battery,
            "app": data.app,
            "screen_on": data.screen_on,
            "recorded_at": datetime.utcnow().isoformat()
        },
        "is_important": False
    }
    
    result = supabase.table("memories").insert(gps_data).execute()
    return {"success": True, "id": result.data[0]["id"] if result.data else None}

@app.get("/api/gps/latest")
async def get_latest_gps():
    """获取最新位置"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    result = supabase.table("memories")\
        .select("*")\
        .eq("type", "gps_history")\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    
    if result.data:
        return {"found": True, "location": result.data[0]}
    return {"found": False}

@app.post("/api/bark/push")
async def send_bark_push(push: BarkPush):
    """通过Bark发送推送通知到iPhone"""
    if not BARK_KEY:
        raise HTTPException(status_code=500, detail="Bark key not configured")
    
    try:
        async with httpx.AsyncClient() as client:
            # 构建URL参数
            params = []
            if push.sound:
                params.append(f"sound={push.sound}")
            if push.is_archive:
                params.append("isArchive=1")
            if push.group:
                params.append(f"group={quote(push.group)}")
            if push.url:
                params.append(f"url={quote(push.url)}")
            
            # 构建完整URL
            url = f"https://api.day.app/{BARK_KEY}/{quote(push.title)}/{quote(push.body)}"
            if params:
                url += "?" + "&".join(params)
            
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                return {"success": True}
            return {"success": False, "error": resp.text}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/insights")
async def get_user_insights(limit: int = 10):
    """获取用户洞察（重要记忆）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    result = supabase.table("memories")\
        .select("*")\
        .eq("type", "user_insight")\
        .eq("is_important", True)\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    
    return {"insights": result.data}

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
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
