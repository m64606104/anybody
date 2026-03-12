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

# 云端同步模型
class SyncData(BaseModel):
    chats: Optional[List[dict]] = None  # 聊天列表
    messages: Optional[dict] = None  # {chatId: [messages]}
    roles: Optional[List[dict]] = None  # 角色列表
    api_settings: Optional[dict] = None  # API配置
    chat_settings: Optional[dict] = None  # 聊天设置
    user_profile: Optional[dict] = None  # 用户资料

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

def get_all_roles() -> List[dict]:
    """从云端同步数据中获取所有角色"""
    if not supabase:
        return []
    try:
        result = supabase.table("user_sync")\
            .select("roles")\
            .eq("user_id", "default_user")\
            .limit(1)\
            .execute()
        if result.data and result.data[0].get("roles"):
            return result.data[0]["roles"]
        return []
    except Exception as e:
        print(f"⚠️ 获取角色列表失败: {e}")
        return []

def build_role_system_prompt(role: dict) -> str:
    """
    从角色设置中构建 system prompt，完全由用户填写的内容决定。
    不注入任何硬编码的身份、语气或关系描述。
    """
    if not role:
        return ""
    parts = []
    if role.get("persona"):
        parts.append(role["persona"])
    if role.get("traits"):
        parts.append(f"性格特点：{role['traits']}")
    if role.get("tone"):
        parts.append(f"说话风格：{role['tone']}")
    if role.get("memory"):
        parts.append(f"背景记忆：{role['memory']}")
    return "\n".join(parts)

def get_ai_behavior_settings() -> dict:
    """读取 ai_behavior_settings 表，返回 {setting_name: setting_value} 字典"""
    if not supabase:
        return {}
    try:
        result = supabase.table("ai_behavior_settings")\
            .select("setting_name, setting_value")\
            .execute()
        if result.data:
            return {row["setting_name"]: row["setting_value"] for row in result.data}
        return {}
    except Exception as e:
        print(f"⚠️ 读取 ai_behavior_settings 失败（表可能不存在）: {e}")
        return {}

def get_user_persona_summary() -> str:
    """读取 user_persona 表，汇总成可注入 prompt 的文字"""
    if not supabase:
        return ""
    try:
        result = supabase.table("user_persona")\
            .select("trait_category, trait_detail, confidence_score")\
            .order("confidence_score", desc=True)\
            .limit(20)\
            .execute()
        if not result.data:
            return ""
        lines = []
        for row in result.data:
            score = row.get("confidence_score", 0)
            lines.append(f"[{row['trait_category']}] {row['trait_detail']} (确定度{score:.1f})")
        return "\n".join(lines)
    except Exception as e:
        print(f"⚠️ 读取 user_persona 失败（表可能不存在）: {e}")
        return ""

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
    scheduler.add_job(proactive_thinking, 'interval', minutes=5, id='proactive_thinker')  # 每5分钟检查，实际执行由内部随机逻辑控制
    scheduler.add_job(async_update_persona, 'interval', hours=24, id='persona_updater')  # 每24小时更新用户画像
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

@app.get("/memory/recent")
async def get_recent_memories(limit: int = 10):
    """获取最近的记忆（用于注入AI上下文）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    result = supabase.table("memories")\
        .select("*")\
        .order("created_at", desc=True)\
        .limit(limit)\
        .execute()
    
    return {"memories": result.data}

@app.get("/memory/by_types")
async def get_memories_by_types(chat_limit: int = 5, capture_limit: int = 3, gps_limit: int = 2):
    """按类型分别获取记忆，避免互相挤掉"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    result = {}
    
    # 获取最近聊天记录
    chat_result = supabase.table("memories")\
        .select("*")\
        .eq("type", "chat")\
        .order("created_at", desc=True)\
        .limit(chat_limit)\
        .execute()
    result["chats"] = chat_result.data or []
    
    # 获取最近截屏数据
    capture_result = supabase.table("memories")\
        .select("*")\
        .eq("type", "screen_capture")\
        .order("created_at", desc=True)\
        .limit(capture_limit)\
        .execute()
    result["screen_captures"] = capture_result.data or []
    
    # 获取最近GPS记录
    gps_result = supabase.table("memories")\
        .select("*")\
        .eq("type", "gps_history")\
        .order("created_at", desc=True)\
        .limit(gps_limit)\
        .execute()
    result["gps"] = gps_result.data or []
    
    return result

class MemoryDelete(BaseModel):
    content: str

@app.post("/memory/delete")
async def delete_memory(req: MemoryDelete):
    """根据内容删除记忆"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 先查找匹配的记录
    result = supabase.table("memories")\
        .select("id")\
        .eq("content", req.content)\
        .execute()
    
    deleted_count = 0
    if result.data:
        for item in result.data:
            supabase.table("memories").delete().eq("id", item["id"]).execute()
            deleted_count += 1
    
    return {"success": True, "deleted_count": deleted_count}

@app.get("/api/user/status")
async def get_user_status():
    """获取用户最新状态（位置、电量等）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 获取最新GPS记录
    gps_result = supabase.table("memories")\
        .select("*")\
        .eq("type", "gps_history")\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    
    status = {}
    if gps_result.data:
        meta = gps_result.data[0].get("metadata", {})
        status["location"] = {
            "latitude": meta.get("latitude"),
            "longitude": meta.get("longitude"),
            "address": meta.get("address")
        }
        status["battery"] = meta.get("battery")
        status["last_app"] = meta.get("app")
        status["last_active"] = gps_result.data[0].get("created_at")
    
    return status

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
    """生成主动消息（使用传入的角色人设）"""
    now = datetime.now()
    beijing_hour = (now.hour + 8) % 24
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    
    # 使用传入的角色人设，如果没有则从云端获取管家角色
    persona = req.role_persona
    if not persona:
        butler_role = get_butler_role()
        if not butler_role:
            return {"message": ""}  # 没有设置管家角色
        persona = build_butler_persona(butler_role)
    
    system_prompt = f"""{persona}

（以上是你的角色设定，请完全按照设定来说话和行动）"""

    # 构建上下文信息
    context_parts = [f"时间：{weekday} {beijing_hour}点"]
    if req.user_status:
        if req.user_status.get("location"):
            context_parts.append(f"位置：{req.user_status['location']}")
    
    user_prompt = f"""【背景信息】
{chr(10).join(context_parts)}

【最近的聊天/记忆】
{chr(10).join(req.recent_memories) if req.recent_memories else "（暂无）"}

---
你想主动跟用户说点什么？自由发挥。"""

    message = await call_ai(system_prompt, user_prompt)
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
        print(f"⏰ 提醒到期: {reminder['content']}")
        
        # 通过Bark推送提醒到手机
        if BARK_KEY:
            try:
                async with httpx.AsyncClient() as client:
                    bark_url = f"https://api.day.app/{BARK_KEY}/⏰闹钟提醒/{quote(reminder['content'])}?sound=alarm&isArchive=1"
                    await client.get(bark_url, timeout=10.0)
                    print(f"📤 已推送闹钟提醒到Bark")
            except Exception as e:
                print(f"⚠️ Bark推送失败: {e}")
        
        # 存入memories作为提醒记录
        supabase.table("memories").insert({
            "type": "reminder_triggered",
            "content": f"闹钟提醒: {reminder['content']}",
            "metadata": {"reminder_id": reminder["id"]},
            "is_important": False
        }).execute()
        
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
# 存储上次主动思考的时间和目标间隔
last_proactive_time = None
next_target_interval = None  # 固定目标间隔，避免每次随机

async def _collect_environment_context() -> dict:
    """采集所有环境感知数据，返回结构化字典"""
    now = datetime.utcnow()
    beijing_hour = (now.hour + 8) % 24
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]

    # 聊天记录
    chat_result = supabase.table("memories")\
        .select("content, created_at")\
        .eq("type", "chat")\
        .order("created_at", desc=True)\
        .limit(20).execute()
    recent_chats = chat_result.data or []

    # GPS
    gps_result = supabase.table("memories")\
        .select("content, metadata, created_at")\
        .eq("type", "gps_history")\
        .order("created_at", desc=True)\
        .limit(3).execute()
    recent_gps = gps_result.data or []

    # 截屏/应用活动
    screen_result = supabase.table("memories")\
        .select("content, metadata, created_at")\
        .eq("type", "screen_capture")\
        .order("created_at", desc=True)\
        .limit(5).execute()
    recent_screens = screen_result.data or []

    # 系统状态（由快捷指令上传）
    status_result = supabase.table("memories")\
        .select("content, metadata, created_at")\
        .eq("type", "system_status")\
        .order("created_at", desc=True)\
        .limit(1).execute()
    system_status = status_result.data[0] if status_result.data else None

    # 计算失联时长
    hours_since_last_chat = 0.0
    if recent_chats:
        try:
            last_chat_time = datetime.fromisoformat(
                recent_chats[0]["created_at"].replace("Z", "").replace("+00:00", "")
            )
            hours_since_last_chat = (now - last_chat_time).total_seconds() / 3600
        except Exception:
            pass

    # 格式化文本上下文
    gps_context = ""
    if recent_gps:
        meta = recent_gps[0].get("metadata", {})
        location = meta.get("address") or meta.get("location", "未知位置")
        gps_context = f"位置：{location}（{recent_gps[0].get('created_at','')[:16]}）"

    status_context = ""
    if system_status:
        meta = system_status.get("metadata", {})
        battery = meta.get("battery", "未知")
        wifi = meta.get("wifi", "未知")
        status_context = f"电量：{battery}% | WiFi：{wifi}"

    screen_context = ""
    if recent_screens:
        apps = [s.get("metadata", {}).get("app", "?") for s in recent_screens[:3]]
        screen_context = f"最近使用应用：{', '.join(apps)}"

    chat_summary = "\n".join([c["content"][:120] for c in recent_chats[:10]]) or "（暂无聊天记录）"

    return {
        "now": now,
        "beijing_hour": beijing_hour,
        "weekday": weekday,
        "hours_since_last_chat": hours_since_last_chat,
        "gps_context": gps_context,
        "status_context": status_context,
        "screen_context": screen_context,
        "chat_summary": chat_summary,
    }

async def proactive_thinking():
    """
    多角色竞争推送机制：
    1. 从 ai_behavior_settings 读取间隔/禁扰配置
    2. 采集环境感知数据 + user_persona 用户画像
    3. 让所有角色各自独立决策（系统prompt完全来自角色自身设置，不硬编码任何描述）
    4. 按优先级（当前活跃角色优先）选出发送者，推送Bark
    """
    global last_proactive_time, next_target_interval

    print("🔄 主动思考检查开始...")

    if not supabase:
        print("❌ Supabase未连接，跳过主动思考")
        return

    try:
        now = datetime.utcnow()
        current_hour = (now.hour + 8) % 24

        # ── 第一步：读取 AI 行为设置 ────────────────────────────────────
        behavior = get_ai_behavior_settings()

        # 间隔配置（分钟）
        min_interval = int(behavior.get("min_interval", 0) or 0)
        max_interval = int(behavior.get("max_interval", 0) or 0)
        if not min_interval or not max_interval:
            # 没有配置时使用时间段默认值
            if 3 <= current_hour < 7:
                min_interval, max_interval = 180, 300
            elif 23 <= current_hour or current_hour < 3:
                min_interval, max_interval = 120, 240
            else:
                min_interval, max_interval = 30, 120
        print(f"📊 间隔设置: {min_interval}-{max_interval}分钟 (北京时间{current_hour}点)")

        # 禁扰时段
        no_disturb_start = behavior.get("no_disturb_start")
        no_disturb_end = behavior.get("no_disturb_end")
        if no_disturb_start is not None and no_disturb_end is not None:
            try:
                nds, nde = int(no_disturb_start), int(no_disturb_end)
                in_no_disturb = (nds <= current_hour < nde) if nds < nde else (current_hour >= nds or current_hour < nde)
                if in_no_disturb:
                    print(f"⏸️ 禁扰时段 {nds}:00-{nde}:00，跳过")
                    return
            except Exception:
                pass

        # 时间间隔判断
        if last_proactive_time:
            elapsed_minutes = (now - last_proactive_time).total_seconds() / 60
            if next_target_interval is None:
                next_target_interval = random.randint(min_interval, max_interval)
            print(f"⏱️ 已过{elapsed_minutes:.1f}分钟，目标{next_target_interval}分钟")
            if elapsed_minutes < next_target_interval:
                return
        else:
            print("🆕 首次执行主动思考")

        last_proactive_time = now
        next_target_interval = random.randint(min_interval, max_interval)
        print(f"✅ 开始执行主动思考，下次间隔{next_target_interval}分钟")

        # ── 第二步：环境感知数据 + 用户画像 ─────────────────────────────
        env = await _collect_environment_context()
        persona_summary = get_user_persona_summary()

        env_block = f"""【当前时间】{env['weekday']} {env['beijing_hour']}点
{env['gps_context'] or '位置：未知'}
{env['status_context'] or '系统状态：未知'}
{env['screen_context'] or '应用活动：未知'}
失联时长：{env['hours_since_last_chat']:.1f}小时"""

        persona_block = f"\n\n【关于用户的了解】\n{persona_summary}" if persona_summary else ""

        chat_block = f"\n\n【最近聊天记录】\n{env['chat_summary']}"

        user_prompt = f"""{env_block}{persona_block}{chat_block}

---
根据以上信息，判断此刻是否适合发送消息。
- 不发送：回复 PASS
- 发送：回复 MESSAGE: 你想说的话（消息本身，不含前缀）"""

        # ── 第三步：当前活跃角色 ─────────────────────────────────────────
        current_active_role_id = behavior.get("current_active_role_id")
        all_roles = get_all_roles()

        if not all_roles:
            print("⚠️ 没有找到任何角色，跳过主动思考")
            return

        # 按优先级排序：活跃角色排最前
        def role_priority(r: dict) -> int:
            return 0 if r.get("id") == current_active_role_id else 1

        sorted_roles = sorted(all_roles, key=role_priority)

        # ── 第四步：多角色竞争决策 ───────────────────────────────────────
        winner_role = None
        winner_message = None

        for role in sorted_roles:
            role_name = role.get("name", "AI")
            role_system_prompt = build_role_system_prompt(role)

            # 如果角色没有填写任何人设，system prompt 为空字符串，
            # 此时 call_ai 将只传 user_prompt（无人格约束）
            if role_system_prompt:
                system_prompt = f"{role_system_prompt}\n\n## 指令格式\n只输出 PASS 或 MESSAGE: 消息内容，不要输出其他内容。"
            else:
                system_prompt = "你是用户的AI助手。只输出 PASS 或 MESSAGE: 消息内容，不要输出其他内容。"

            try:
                decision = (await call_ai(system_prompt, user_prompt)).strip()
                print(f"🤔 [{role_name}] 决策: {decision[:80]}...")

                if decision.upper().startswith("MESSAGE:"):
                    msg = decision[len("MESSAGE:"):].strip()
                    if msg:
                        winner_role = role
                        winner_message = msg
                        # 活跃角色直接采纳，非活跃角色仅在无其他候选时采纳
                        if role.get("id") == current_active_role_id:
                            break  # 活跃角色优先，直接定稿
            except Exception as role_err:
                print(f"⚠️ [{role_name}] 决策失败: {role_err}")
                continue

        # ── 第五步：推送 ──────────────────────────────────────────────────
        if winner_message and winner_role:
            role_name = winner_role.get("name", "AI")
            print(f"💬 [{role_name}] 决定发送: {winner_message}")

            # 存入记忆
            supabase.table("memories").insert({
                "type": "proactive_message",
                "content": winner_message,
                "metadata": {
                    "role_id": winner_role.get("id"),
                    "role_name": role_name,
                    "hours_since_last_chat": env["hours_since_last_chat"],
                    "trigger": "proactive_thinking",
                },
                "is_important": False
            }).execute()

            # Bark 推送，标题为角色名
            if BARK_KEY:
                try:
                    async with httpx.AsyncClient() as client:
                        bark_url = (
                            f"https://api.day.app/{BARK_KEY}"
                            f"/{quote(role_name)}/{quote(winner_message)}"
                            f"?sound=shake&isArchive=1&group={quote(role_name)}"
                        )
                        await client.get(bark_url, timeout=10.0)
                        print(f"📤 已通过Bark推送（{role_name}）")
                except Exception as bark_err:
                    print(f"⚠️ Bark推送失败: {bark_err}")
        else:
            print("⏭️ 所有角色均选择 PASS，不发送消息")

    except Exception as e:
        print(f"❌ 主动思考失败: {e}")

# ============ 调试：检查主动思考状态 ============
@app.get("/proactive/debug")
async def debug_proactive():
    """调试主动思考功能"""
    global last_proactive_time

    all_roles = get_all_roles()
    behavior = get_ai_behavior_settings()

    recent_proactive = []
    if supabase:
        result = supabase.table("memories")\
            .select("content, metadata, created_at")\
            .eq("type", "proactive_message")\
            .order("created_at", desc=True)\
            .limit(5)\
            .execute()
        recent_proactive = result.data or []

    return {
        "roles_count": len(all_roles),
        "role_names": [r.get("name") for r in all_roles],
        "current_active_role_id": behavior.get("current_active_role_id"),
        "ai_behavior_settings": behavior,
        "last_proactive_time": str(last_proactive_time) if last_proactive_time else None,
        "next_target_interval_min": next_target_interval,
        "recent_proactive_messages": [
            {"content": m["content"], "role": m.get("metadata", {}).get("role_name"), "at": m["created_at"]}
            for m in recent_proactive
        ],
        "scheduler_running": scheduler.running if scheduler else False,
    }

# ============ AI 自主修改运行参数 ============
class AiBehaviorUpdate(BaseModel):
    setting_name: str
    setting_value: str
    reason: Optional[str] = None  # AI 说明为什么要改

@app.post("/ai/behavior")
async def update_ai_behavior(update: AiBehaviorUpdate):
    """
    允许 AI（或快捷指令）修改自身运行参数。
    常用 setting_name：
      min_interval / max_interval  推送间隔（分钟）
      no_disturb_start / no_disturb_end  禁扰时段（小时，北京时间）
      current_active_role_id       当前活跃角色 ID
      morning_check_in_time        晨间打招呼时间（HH:MM）
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    try:
        # upsert：存在则更新，不存在则插入
        supabase.table("ai_behavior_settings").upsert({
            "setting_name": update.setting_name,
            "setting_value": update.setting_value,
            "last_updated": datetime.utcnow().isoformat()
        }, on_conflict="setting_name").execute()
        print(f"⚙️ AI更新行为设置: {update.setting_name}={update.setting_value} ({update.reason or '无说明'})")
        return {"success": True, "setting_name": update.setting_name, "setting_value": update.setting_value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ai/behavior")
async def get_ai_behavior():
    """读取当前所有 AI 行为设置"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    return get_ai_behavior_settings()

# ============ 用户画像更新（定时任务）============
async def async_update_persona():
    """
    每24小时扫描近期聊天记录和GPS数据，提取用户特征，更新 user_persona 表。
    只提取 AI 本身不知道的新信息，不重复插入已有认知。
    """
    if not supabase or not OPENAI_API_KEY:
        return
    try:
        print("🧠 开始更新用户画像...")
        # 获取近24小时聊天记录
        since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        chat_result = supabase.table("memories")\
            .select("content, created_at")\
            .eq("type", "chat")\
            .gte("created_at", since)\
            .order("created_at", desc=False)\
            .limit(50).execute()
        chats = chat_result.data or []
        if not chats:
            print("🧠 近24小时无新聊天，跳过画像更新")
            return

        # 获取近24小时GPS轨迹
        gps_result = supabase.table("memories")\
            .select("content, metadata, created_at")\
            .eq("type", "gps_history")\
            .gte("created_at", since)\
            .order("created_at", desc=False)\
            .limit(20).execute()
        gps_data = gps_result.data or []

        # 读取现有画像（避免重复提取）
        existing_traits = get_user_persona_summary()

        chat_text = "\n".join([c["content"][:100] for c in chats])
        gps_text = ""
        if gps_data:
            locations = [g.get("metadata", {}).get("address") or g.get("metadata", {}).get("location", "?") for g in gps_data]
            gps_text = f"\n今日轨迹：{' -> '.join(locations)}"

        system_prompt = """你是一个用户画像分析师。根据用户今天的聊天记录和位置轨迹，提取关于用户的新认知。
输出格式（每行一条，JSON数组）：
[{"category": "类别", "detail": "具体认知", "confidence": 0.8}]
类别包括：lifestyle（生活习惯）, preference（偏好）, emotional_state（情绪状态）, work_habit（工作习惯）, location_habit（位置习惯）
只提取有明确证据支撑的认知，confidence 0.5-1.0。
如果没有新认知，输出空数组 []"""

        user_prompt = f"""【今日聊天记录】\n{chat_text}{gps_text}

【已有画像（不要重复）】\n{existing_traits or '（无）'}

请提取今日新增的用户认知："""

        result_text = await call_ai(system_prompt, user_prompt)
        result_text = result_text.strip()

        # 尝试解析JSON
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if not json_match:
            print(f"🧠 画像提取返回非JSON: {result_text[:100]}")
            return

        traits = json.loads(json_match.group(0))
        if not traits:
            print("🧠 无新用户认知")
            return

        for trait in traits:
            if trait.get("category") and trait.get("detail"):
                supabase.table("user_persona").insert({
                    "trait_category": trait["category"],
                    "trait_detail": trait["detail"],
                    "confidence_score": float(trait.get("confidence", 0.7)),
                    "updated_at": datetime.utcnow().isoformat()
                }).execute()

        print(f"🧠 用户画像已更新，新增 {len(traits)} 条认知")

    except Exception as e:
        print(f"❌ 用户画像更新失败: {e}")

# ============ 获取待推送的主动消息 ============
@app.get("/proactive/pending")
async def get_pending_proactive_messages():
    """获取待推送的主动消息（前端轮询用）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 获取最近5分钟内未读的主动消息
    five_min_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    
    result = supabase.table("memories")\
        .select("*")\
        .eq("type", "proactive_message")\
        .gte("created_at", five_min_ago)\
        .order("created_at", desc=True)\
        .limit(1)\
        .execute()
    
    if result.data:
        msg = result.data[0]
        # 检查是否已读
        metadata = msg.get("metadata") or {}
        if metadata.get("is_read"):
            return {"has_message": False}
        
        # 标记为已读
        metadata["is_read"] = True
        supabase.table("memories").update({"metadata": metadata}).eq("id", msg["id"]).execute()
        
        return {"has_message": True, "message": msg["content"], "id": msg["id"]}
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
    """接收iOS快捷指令发送的位置数据，并触发AI主动消息"""
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
    
    # 🎯 触发AI主动消息
    try:
        # 获取管家角色
        butler_role = get_butler_role()
        if not butler_role:
            print("⚠️ 未设置管家角色，跳过主动消息")
            return {"success": True, "id": result.data[0]["id"] if result.data else None}
        
        butler_persona = build_butler_persona(butler_role)
        
        # 获取最近的聊天记录
        recent_chats = supabase.table("memories")\
            .select("content, type")\
            .in_("type", ["chat", "proactive_message"])\
            .order("created_at", desc=True)\
            .limit(10)\
            .execute()
        
        chat_history = [m["content"][:100] for m in (recent_chats.data or [])]
        
        # 获取重要记忆
        important_memories = supabase.table("memories")\
            .select("content")\
            .eq("is_important", True)\
            .order("created_at", desc=True)\
            .limit(5)\
            .execute()
        
        important_context = [m["content"] for m in important_memories.data] if important_memories.data else []
        
        # 构建上下文
        now = datetime.now()
        beijing_hour = (now.hour + 8) % 24
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        
        location_info = data.address or f"经纬度({data.latitude:.2f}, {data.longitude:.2f})"
        battery_info = data.battery
        
        # 使用管家角色的人设生成消息
        system_prompt = f"""{butler_persona}

（以上是你的角色设定，请完全按照设定来说话和行动）"""

        user_prompt = f"""【当前状态】
时间：{weekday} {beijing_hour}点
位置：{location_info}
电量：{f'{battery_info}%' if battery_info else '未知'}{' (电量偏低)' if battery_info and battery_info < 30 else ''}

【最近的聊天记录】
{chr(10).join(chat_history) if chat_history else "（暂无）"}

【用户的重要记忆】
{chr(10).join(important_context) if important_context else "（暂无）"}

---
用户刚给手机充电，你想主动跟ta说点什么？自由发挥。"""

        message = await call_ai(system_prompt, user_prompt)
        
        if message and BARK_KEY:
            # 通过Bark推送到手机
            async with httpx.AsyncClient() as client:
                bark_url = f"https://api.day.app/{BARK_KEY}/AI助手/{quote(message)}?sound=shake&isArchive=1"
                await client.get(bark_url, timeout=10.0)
                print(f"📤 已推送主动消息: {message[:50]}...")
        
        # 同时存入memories
        if message:
            supabase.table("memories").insert({
                "type": "proactive_message",
                "content": message,
                "metadata": {"trigger": "gps_upload", "location": location_info},
                "is_important": False
            }).execute()
            
    except Exception as e:
        print(f"⚠️ 生成主动消息失败: {e}")
    
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

# ============ 云端同步 ============
USER_ID = "default_user"  # 单用户模式，固定用户ID

@app.get("/sync/load")
async def load_sync_data():
    """从云端加载所有数据"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    result = supabase.table("user_sync")\
        .select("*")\
        .eq("user_id", USER_ID)\
        .single()\
        .execute()
    
    if result.data:
        return {
            "found": True,
            "data": {
                "chats": result.data.get("chats", []),
                "messages": result.data.get("messages", {}),
                "roles": result.data.get("roles", []),
                "api_settings": result.data.get("api_settings", {}),
                "chat_settings": result.data.get("chat_settings", {}),
                "user_profile": result.data.get("user_profile", {}),
                "updated_at": result.data.get("updated_at")
            }
        }
    return {"found": False}

@app.post("/sync/save")
async def save_sync_data(data: SyncData):
    """保存数据到云端"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 构建更新数据
    update_data = {"user_id": USER_ID, "updated_at": datetime.utcnow().isoformat()}
    if data.chats is not None:
        update_data["chats"] = data.chats
    if data.messages is not None:
        update_data["messages"] = data.messages
    if data.roles is not None:
        update_data["roles"] = data.roles
    if data.api_settings is not None:
        update_data["api_settings"] = data.api_settings
    if data.chat_settings is not None:
        update_data["chat_settings"] = data.chat_settings
    if data.user_profile is not None:
        update_data["user_profile"] = data.user_profile
    
    # upsert: 存在则更新，不存在则插入
    result = supabase.table("user_sync").upsert(update_data, on_conflict="user_id").execute()
    
    return {"success": True, "updated_at": update_data["updated_at"]}

@app.post("/sync/message")
async def sync_single_message(chat_id: str, message: dict):
    """同步单条消息（实时同步用）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 获取当前数据
    result = supabase.table("user_sync")\
        .select("messages")\
        .eq("user_id", USER_ID)\
        .single()\
        .execute()
    
    messages = result.data.get("messages", {}) if result.data else {}
    
    # 添加新消息
    if chat_id not in messages:
        messages[chat_id] = []
    messages[chat_id].append(message)
    
    # 更新
    supabase.table("user_sync").upsert({
        "user_id": USER_ID,
        "messages": messages,
        "updated_at": datetime.utcnow().isoformat()
    }, on_conflict="user_id").execute()
    
    return {"success": True}

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
