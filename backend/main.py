"""
AI Assistant Backend - 新表结构
"""
import os, re, json, random
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

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BARK_KEY = os.getenv("BARK_KEY", "")

supabase: Client = None
scheduler = AsyncIOScheduler()

# Models
class GPSData(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    battery: Optional[int] = None
    wifi: Optional[str] = None
    app: Optional[str] = None
    charging: bool = False
    screen_on: bool = True

class HealthData(BaseModel):
    # 天气
    weather: Optional[str] = None  # 当前天气描述
    temperature: Optional[float] = None  # 温度
    humidity: Optional[int] = None  # 湿度
    weather_forecast: Optional[str] = None  # 天气预报
    # 健康
    heart_rate: Optional[int] = None  # 心率
    hrv: Optional[int] = None  # 心率变异性
    steps: Optional[int] = None  # 步数
    sleep_hours: Optional[float] = None  # 睡眠时长
    # 月经
    menstrual_status: Optional[str] = None  # 月经状态：period/fertile/ovulation/luteal/none
    menstrual_day: Optional[int] = None  # 周期第几天
    # 其他
    note: Optional[str] = None  # 备注

class ReminderCreate(BaseModel):
    content: str
    remind_at: datetime
    repeat: Optional[str] = None

class ExpenseCreate(BaseModel):
    amount: float
    category: str
    description: Optional[str] = None

class MemoryCreate(BaseModel):
    content: str
    title: Optional[str] = None
    category: Optional[str] = None
    importance: int = 1

class SyncData(BaseModel):
    chats: Optional[List[dict]] = None
    messages: Optional[dict] = None  # {chatId: Message[]}
    roles: Optional[List[dict]] = None
    api_settings: Optional[dict] = None
    chat_settings: Optional[dict] = None
    user_profile: Optional[dict] = None

class AiBehaviorUpdate(BaseModel):
    setting_name: str
    setting_value: str

# 工具函数
async def call_ai(sys: str, user: str = "") -> str:
    async with httpx.AsyncClient() as c:
        msgs = [{"role": "system", "content": sys}]
        if user: msgs.append({"role": "user", "content": user})
        r = await c.post(f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": OPENAI_MODEL, "messages": msgs}, timeout=30)
        return r.json()["choices"][0]["message"]["content"]

def get_all_roles():
    if not supabase: return []
    try:
        r = supabase.table("user_sync").select("roles").eq("user_id", "default_user").limit(1).execute()
        return r.data[0].get("roles", []) if r.data else []
    except: return []

def build_role_prompt(role):
    if not role: return ""
    p = []
    if role.get("persona"): p.append(role["persona"])
    if role.get("traits"): p.append(f"性格：{role['traits']}")
    if role.get("tone"): p.append(f"风格：{role['tone']}")
    return "\n".join(p)

def get_behavior():
    if not supabase: return {}
    try:
        r = supabase.table("ai_behavior_settings").select("setting_name,setting_value").execute()
        return {x["setting_name"]: x["setting_value"] for x in r.data} if r.data else {}
    except: return {}

def get_persona():
    if not supabase: return ""
    try:
        r = supabase.table("user_persona").select("trait_category,trait_detail").order("confidence_score", desc=True).limit(15).execute()
        return "\n".join([f"[{x['trait_category']}]{x['trait_detail']}" for x in r.data]) if r.data else ""
    except: return ""

def get_all_context(role_id: str = None):
    """获取Supabase所有数据作为AI的资料库，可按role_id过滤聊天记录"""
    if not supabase: return ""
    
    # 时间
    now_beijing = datetime.utcnow() + timedelta(hours=8)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    time_str = now_beijing.strftime(f"%Y年%m月%d日 {weekdays[now_beijing.weekday()]} %H:%M:%S")
    
    # GPS/位置/电量
    gps = supabase.table("gps_history").select("*").order("created_at", desc=True).limit(1).execute().data
    gps_info = ""
    if gps:
        g = gps[0]
        gps_info = f"位置：{g.get('city') or g.get('address','未知')}\n街道：{g.get('street','')}\n州/省：{g.get('state','')}\n经纬度：{g.get('latitude')},{g.get('longitude')}\n电量：{g.get('battery','?')}%\n充电：{'是' if g.get('charging') else '否'}\nWiFi：{g.get('wifi','未知')}\n当前应用：{g.get('app','未知')}"
    
    # 健康数据
    health = supabase.table("health_data").select("*").order("created_at", desc=True).limit(1).execute().data
    health_info = ""
    if health:
        h = health[0]
        parts = []
        if h.get('weather'): parts.append(f"天气：{h['weather']}")
        if h.get('temperature'): parts.append(f"温度：{h['temperature']}°")
        if h.get('humidity'): parts.append(f"湿度：{h['humidity']}%")
        if h.get('weather_forecast'): parts.append(f"预报：{h['weather_forecast']}")
        if h.get('heart_rate'): parts.append(f"心率：{h['heart_rate']}bpm")
        if h.get('hrv'): parts.append(f"HRV：{h['hrv']}ms")
        if h.get('steps'): parts.append(f"步数：{h['steps']}")
        if h.get('sleep_hours'): parts.append(f"睡眠：{h['sleep_hours']}小时")
        if h.get('menstrual_status'): parts.append(f"月经：{h['menstrual_status']}")
        if h.get('menstrual_day'): parts.append(f"周期第{h['menstrual_day']}天")
        health_info = "\n".join(parts)
    
    # 用户画像
    persona = get_persona()
    
    # 待办事项（未完成）
    reminders = supabase.table("reminders").select("content,remind_at,repeat").eq("is_done", False).order("remind_at").limit(10).execute().data or []
    reminder_list = "\n".join([f"- {r['content']} ({r['remind_at']})" + (f" [重复:{r['repeat']}]" if r.get('repeat') else "") for r in reminders]) if reminders else "无"
    
    # 所有记忆（不限制数量）
    memories = supabase.table("memories").select("content,category,title,created_at").order("created_at", desc=True).limit(100).execute().data or []
    mem_list = "\n".join([f"- [{m.get('category','其他')}] {m.get('title') or m['content'][:100]}" for m in memories]) if memories else "无"
    
    # 聊天记录（扩大到100条）- 按role_id过滤
    chat_query = supabase.table("chat_messages").select("sender,content,created_at,role_id")
    if role_id:
        chat_query = chat_query.eq("role_id", role_id)
    chats = chat_query.order("created_at", desc=True).limit(100).execute().data or []
    def format_chat_time(created_at):
        try:
            t = datetime.fromisoformat(created_at.replace("Z", "+00:00").replace("+00:00", ""))
            t_beijing = t + timedelta(hours=8)
            return t_beijing.strftime("%m/%d %H:%M")
        except:
            return ""
    chat_list = "\n".join([f"[{format_chat_time(c['created_at'])}] [{c['sender']}] {c['content'][:80]}" for c in reversed(chats)]) if chats else "无"
    
    # 最近支出
    expenses = supabase.table("expenses").select("amount,category,description,date").order("created_at", desc=True).limit(10).execute().data or []
    expense_list = "\n".join([f"- ¥{e['amount']} {e['category']} {e.get('description','')} ({e['date']})" for e in expenses]) if expenses else "无"
    
    # 最近主动消息（避免重复）
    proactive = supabase.table("proactive_messages").select("content,created_at").order("created_at", desc=True).limit(5).execute().data or []
    proactive_list = "\n".join([f"- {p['content'][:60]}" for p in proactive]) if proactive else "无"
    
    return f"""【当前时间】
{time_str}

【位置与设备】
{gps_info or '无数据'}

【健康与天气】
{health_info or '无数据'}

【用户画像】
{persona or '无'}

【待办事项】
{reminder_list}

【最近记忆】
{mem_list}

【最近支出】
{expense_list}

【最近聊天记录】
{chat_list}

【最近主动消息（避免重复）】
{proactive_list}"""

# 定时任务
async def self_ping():
    """每10分钟自我ping，防止Render休眠"""
    try:
        async with httpx.AsyncClient() as c:
            await c.get("https://anybody.onrender.com/health", timeout=30)
        print("🏓 Self-ping OK")
    except Exception as e:
        print(f"🏓 Self-ping failed: {e}")

async def check_reminders():
    if not supabase: return
    now = datetime.utcnow()
    r = supabase.table("reminders").select("*").eq("is_done", False).eq("is_pushed", False).lte("remind_at", now.isoformat()).execute()
    for rem in r.data or []:
        if BARK_KEY:
            try:
                async with httpx.AsyncClient() as c:
                    await c.get(f"https://api.day.app/{BARK_KEY}/⏰/{quote(rem['content'])}?sound=alarm", timeout=10)
            except: pass
        if rem.get("repeat"):
            t = datetime.fromisoformat(rem["remind_at"].replace("Z",""))
            d = {"daily": 1, "weekly": 7, "monthly": 30}
            supabase.table("reminders").update({"remind_at": (t + timedelta(days=d.get(rem["repeat"],1))).isoformat()}).eq("id", rem["id"]).execute()
        else:
            supabase.table("reminders").update({"is_done": True, "is_pushed": True}).eq("id", rem["id"]).execute()

async def proactive_thinking():
    if not supabase: return
    now = datetime.utcnow()
    hr = (now.hour + 8) % 24
    beh = get_behavior()
    mi, ma = int(beh.get("min_interval") or 30), int(beh.get("max_interval") or 120)
    if 3 <= hr < 7: mi, ma = 180, 300
    
    # 免打扰时段
    nds, nde = beh.get("no_disturb_start"), beh.get("no_disturb_end")
    if nds and nde:
        nds, nde = int(nds), int(nde)
        if (nds <= hr < nde) if nds < nde else (hr >= nds or hr < nde): return
    
    # 从数据库获取上次发送时间（避免服务器重启后重置）
    last_msg = supabase.table("proactive_messages").select("created_at").order("created_at", desc=True).limit(1).execute().data
    if last_msg:
        last_time = datetime.fromisoformat(last_msg[0]["created_at"].replace("Z", "+00:00").replace("+00:00", ""))
        minutes_since = (now - last_time).total_seconds() / 60
        next_interval = random.randint(mi, ma)
        if minutes_since < next_interval:
            return  # 还没到下次发送时间
    
    # 获取完整资料库
    context = get_all_context()
    
    prompt = f"""{context}

---
以上是你的资料库，你可以自由参考任何内容。
判断是否发消息：不发回复PASS，发回复MESSAGE:内容
注意避免和【最近主动消息】重复。"""
    
    roles = get_all_roles()
    if not roles: return
    active = beh.get("current_active_role_id")
    roles = sorted(roles, key=lambda r: 0 if r.get("id") == active else 1)
    
    winner, msg = None, None
    for role in roles:
        sp = build_role_prompt(role)
        # 系统提示词与聊天对齐，让AI知道所有可用信息
        sys_prompt = f"""{sp}

## 可用能力
- 记忆库：上面已提供最近记忆
- 闹钟：可以用[REMINDER:时间|内容]设置
- 记账：可以用[EXPENSE:金额|分类|描述]记录
- Bark推送：消息会自动推送到用户手机
- HTML渲染：可以输出HTML代码

## 输出格式
- 不发消息回复PASS
- 发消息回复MESSAGE:内容（内容可以包含HTML）"""
        try:
            dec = (await call_ai(sys_prompt, prompt)).strip()
            if dec.upper().startswith("MESSAGE:"):
                winner, msg = role, dec[8:].strip()
                if role.get("id") == active: break
        except: pass
    
    if msg and winner:
        name = winner.get("name", "AI")
        supabase.table("proactive_messages").insert({"role_id": winner.get("id"), "role_name": name, "content": msg, "trigger": "proactive"}).execute()
        if BARK_KEY:
            try:
                async with httpx.AsyncClient() as c:
                    await c.get(f"https://api.day.app/{BARK_KEY}/{quote(name)}/{quote(msg)}?sound=shake&group={quote(name)}", timeout=10)
            except: pass

async def update_persona():
    if not supabase: return
    since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    chats = supabase.table("chat_messages").select("content").gte("created_at", since).limit(30).execute().data or []
    if not chats: return
    txt = "\n".join([c["content"][:60] for c in chats])
    existing = get_persona()
    sys = '提取用户新认知，JSON:[{"category":"lifestyle/preference","detail":"认知","confidence":0.8}]，无返回[]'
    try:
        res = await call_ai(sys, f"聊天:\n{txt}\n已有:\n{existing or '无'}")
        m = re.search(r'\[.*\]', res, re.DOTALL)
        if m:
            for t in json.loads(m.group(0)):
                if t.get("category") and t.get("detail"):
                    supabase.table("user_persona").insert({"trait_category": t["category"], "trait_detail": t["detail"], "confidence_score": t.get("confidence", 0.7)}).execute()
    except: pass

# 生命周期
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase
    if SUPABASE_URL and SUPABASE_KEY:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    scheduler.add_job(check_reminders, 'interval', minutes=1)
    scheduler.add_job(proactive_thinking, 'interval', minutes=5)
    scheduler.add_job(update_persona, 'interval', hours=24)
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# API端点
@app.post("/gps/upload")
async def upload_gps(d: GPSData):
    """上传GPS数据，充电时触发主动消息"""
    r = supabase.table("gps_history").insert({
        "latitude": d.latitude, "longitude": d.longitude, "address": d.address,
        "street": d.street, "city": d.city, "state": d.state,
        "battery": d.battery, "wifi": d.wifi, "app": d.app,
        "charging": d.charging, "screen_on": d.screen_on
    }).execute()
    print(f"📍 GPS: {d.city or d.address or f'{d.latitude},{d.longitude}'} | 电量:{d.battery}% | 充电:{d.charging}")
    
    # 充电时触发主动消息
    if d.charging:
        try:
            roles = get_all_roles()
            if roles:
                beh = get_behavior()
                active_id = beh.get("current_active_role_id")
                role = next((r for r in roles if r.get("id") == active_id), roles[0])
                
                hr = (datetime.utcnow().hour + 8) % 24
                chats = supabase.table("chat_messages").select("content").order("created_at", desc=True).limit(5).execute().data or []
                chat_ctx = "\n".join([c["content"][:60] for c in chats]) or "无"
                
                prompt = f"时间：{hr}点\n位置：{d.address or '未知'}\n电量：{d.battery}%\n\n【最近聊天】\n{chat_ctx}\n\n---\n用户刚给手机充电，你想主动说点什么？"
                sp = build_role_prompt(role)
                msg = (await call_ai(sp or "你是AI助手", prompt)).strip()
                
                if msg:
                    name = role.get("name", "AI")
                    supabase.table("proactive_messages").insert({
                        "role_id": role.get("id"), "role_name": name,
                        "content": msg, "trigger": "charging"
                    }).execute()
                    if BARK_KEY:
                        async with httpx.AsyncClient() as c:
                            await c.get(f"https://api.day.app/{BARK_KEY}/{quote(name)}/{quote(msg)}?sound=shake", timeout=10)
                    print(f"💬 充电触发消息: {msg[:50]}...")
        except Exception as e:
            print(f"⚠️ 充电触发消息失败: {e}")
    
    return {"success": True, "id": r.data[0]["id"] if r.data else None}

@app.get("/gps/latest")
async def latest_gps():
    r = supabase.table("gps_history").select("*").order("created_at", desc=True).limit(1).execute()
    return {"found": bool(r.data), "data": r.data[0] if r.data else None}

@app.post("/health/upload")
async def upload_health(d: HealthData):
    """上传健康数据（天气、心率、步数、月经等）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    data = {
        "weather": d.weather,
        "temperature": d.temperature,
        "humidity": d.humidity,
        "weather_forecast": d.weather_forecast,
        "heart_rate": d.heart_rate,
        "hrv": d.hrv,
        "steps": d.steps,
        "sleep_hours": d.sleep_hours,
        "menstrual_status": d.menstrual_status,
        "menstrual_day": d.menstrual_day,
        "note": d.note
    }
    # 移除None值
    data = {k: v for k, v in data.items() if v is not None}
    
    r = supabase.table("health_data").insert(data).execute()
    print(f"🏥 健康数据: 天气={d.weather} 温度={d.temperature}° 心率={d.heart_rate} 步数={d.steps} 月经={d.menstrual_status}")
    return {"success": True, "id": r.data[0]["id"] if r.data else None}

@app.get("/health/latest")
async def latest_health():
    """获取最新健康数据"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    r = supabase.table("health_data").select("*").order("created_at", desc=True).limit(1).execute()
    return {"found": bool(r.data), "data": r.data[0] if r.data else None}

@app.post("/reminder/create")
async def create_reminder(r: ReminderCreate):
    res = supabase.table("reminders").insert({"content": r.content, "remind_at": r.remind_at.isoformat(), "repeat": r.repeat}).execute()
    return {"success": True, "id": res.data[0]["id"] if res.data else None}

@app.get("/reminder/list")
async def list_reminders():
    r = supabase.table("reminders").select("*").eq("is_done", False).order("remind_at").execute()
    return {"reminders": r.data or []}

@app.post("/expense/add")
async def add_expense(e: ExpenseCreate):
    r = supabase.table("expenses").insert({"amount": e.amount, "category": e.category, "description": e.description, "date": datetime.utcnow().strftime("%Y-%m-%d")}).execute()
    return {"success": True}

@app.get("/expense/summary")
async def expense_summary(days: int = 30):
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    r = supabase.table("expenses").select("*").gte("date", since).execute()
    total = sum(float(e.get("amount", 0)) for e in r.data or [])
    return {"total": total, "count": len(r.data or [])}

@app.post("/memory/store")
async def store_memory(m: MemoryCreate):
    r = supabase.table("memories").insert({"content": m.content, "title": m.title, "category": m.category, "importance": m.importance}).execute()
    return {"success": True}

@app.get("/memory/recent")
async def recent_memories(limit: int = 10):
    r = supabase.table("memories").select("*").order("created_at", desc=True).limit(limit).execute()
    return {"memories": r.data or []}

# ============ 后端聊天（核心功能）============
class ChatSendRequest(BaseModel):
    chat_id: str
    role_id: Optional[str] = None
    message: str
    history: Optional[List[dict]] = None  # [{role, content}]

@app.post("/chat/send")
async def chat_send(req: ChatSendRequest):
    """
    后端聊天：接收用户消息 -> 调用AI -> 存入数据库 -> 推送Bark
    用户发完消息可以离开页面，AI回复会自动推送到手机
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    # 1. 存储用户消息
    supabase.table("chat_messages").insert({
        "chat_id": req.chat_id,
        "role_id": req.role_id,
        "sender": "user",
        "content": req.message
    }).execute()
    
    # 2. 获取角色设置
    role = None
    roles = get_all_roles()
    if req.role_id and roles:
        role = next((r for r in roles if r.get("id") == req.role_id), None)
    if not role and roles:
        role = roles[0]
    
    role_prompt = build_role_prompt(role) if role else ""
    role_name = role.get("name", "AI") if role else "AI"
    
    # 3. 获取基础上下文（不包含大量聊天记录，AI可以自己查询）
    context = get_all_context(role_id=req.role_id)
    
    # 4. 获取完整数据（不限制数量）
    # 完整聊天记录
    all_chats = supabase.table("chat_messages").select("sender,content,created_at").order("created_at", desc=True).limit(500).execute().data or []
    chat_lines = []
    for c in reversed(all_chats):
        try:
            t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
            t_beijing = t + timedelta(hours=8)
            time_str = t_beijing.strftime("%Y-%m-%d %H:%M")
        except:
            time_str = ""
        chat_lines.append(f"[{time_str}] [{c['sender']}] {c['content'][:200]}")
    chat_context = "\n".join(chat_lines) if chat_lines else "无"
    
    # 完整记忆库
    all_memories = supabase.table("memories").select("content,category,title,created_at").order("created_at", desc=True).limit(200).execute().data or []
    mem_lines = [f"- [{m.get('category','其他')}] {m.get('title') or m['content'][:150]}" for m in all_memories]
    memory_context = "\n".join(mem_lines) if mem_lines else "无"
    
    # 完整待办事项
    all_reminders = supabase.table("reminders").select("content,remind_at,repeat,is_done").order("remind_at").execute().data or []
    todo_lines = [f"- {'✅' if t.get('is_done') else '⏰'} {t['content']} ({t['remind_at']})" for t in all_reminders]
    reminder_context = "\n".join(todo_lines) if todo_lines else "无"
    
    # 5. 构建系统提示词（采用之前版本的风格）
    system_prompt = f"""你是用户的AI助手，拥有以下能力：

## 你的能力
1. **记忆能力**：你可以访问Supabase中存储的用户记忆，下面会提供完整的记忆
2. **闹钟提醒**：你可以帮用户设置闹钟，到时间会自动提醒
3. **记账**：你可以帮用户记录支出
4. **查询记忆**：你可以搜索用户的历史记忆和聊天记录
5. **位置感知**：你知道用户当前的位置、电量等状态

## 特殊指令格式（在回复中使用，系统会自动执行）
- 设置闹钟：[REMINDER:2026-03-12T08:00:00|提醒内容]
- 记账：[EXPENSE:50|food|午餐]
- 查询记忆：[QUERY:关键词]

## 完整聊天记录（{len(all_chats)}条）
{chat_context}

## 完整记忆库（{len(all_memories)}条）
{memory_context}

## 完整待办事项（{len(all_reminders)}条）
{reminder_context}

{context}

## 角色设定
{role_prompt or '（无特定角色设定）'}

请基于上面的完整数据回复用户。"""

    # 6. 构建消息历史（支持图片识别）
    messages = [{"role": "system", "content": system_prompt}]
    if req.history:
        for h in req.history[-20:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    
    # 检查用户消息是否包含图片
    import re
    img_pattern = r'<img[^>]+src="(data:image/[^"]+)"[^>]*>'
    img_matches = re.findall(img_pattern, req.message)
    
    if img_matches:
        content_parts = []
        text_only = re.sub(img_pattern, '', req.message).strip()
        if text_only:
            content_parts.append({"type": "text", "text": text_only})
        for img_url in img_matches:
            content_parts.append({"type": "image_url", "image_url": {"url": img_url}})
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": req.message})
    
    # 5. 调用AI（简单调用，不使用Function Calling）
    model = "gpt-4o" if img_matches else OPENAI_MODEL
    ai_reply = ""
    
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": model, "messages": messages, "max_tokens": 4096},
                timeout=120.0
            )
            if resp.status_code != 200:
                raise Exception(f"AI API error: {resp.status_code} - {resp.text}")
            ai_reply = resp.json()["choices"][0]["message"].get("content", "")
    except Exception as e:
        ai_reply = f"调用AI失败：{str(e)}"
        print(f"❌ AI调用错误: {e}")
    
    # 7. 存储AI回复
    supabase.table("chat_messages").insert({
        "chat_id": req.chat_id,
        "role_id": req.role_id,
        "sender": "assistant",
        "content": ai_reply,
        "metadata": {"role_name": role_name}
    }).execute()
    
    # 8. 推送Bark通知
    if BARK_KEY and ai_reply:
        try:
            # 截取前100字符作为推送内容
            push_content = ai_reply[:100] + ("..." if len(ai_reply) > 100 else "")
            async with httpx.AsyncClient() as c:
                url = f"https://api.day.app/{BARK_KEY}/{quote(role_name)}/{quote(push_content)}?sound=shake&group={quote(role_name)}"
                await c.get(url, timeout=10)
            print(f"📤 Bark推送: [{role_name}] {push_content[:30]}...")
        except Exception as e:
            print(f"⚠️ Bark推送失败: {e}")
    
    # 9. 解析并执行指令
    # REMINDER指令
    reminder_match = re.search(r'\[REMINDER:([^\|]+)\|([^\]]+)\]', ai_reply)
    if reminder_match:
        try:
            remind_time = reminder_match.group(1)
            remind_content = reminder_match.group(2)
            supabase.table("reminders").insert({
                "content": remind_content,
                "remind_at": remind_time
            }).execute()
            print(f"⏰ 创建闹钟: {remind_content} @ {remind_time}")
        except: pass
    
    # EXPENSE指令
    expense_match = re.search(r'\[EXPENSE:([^\|]+)\|([^\|]+)\|([^\]]+)\]', ai_reply)
    if expense_match:
        try:
            amount = float(expense_match.group(1))
            category = expense_match.group(2)
            desc = expense_match.group(3)
            supabase.table("expenses").insert({
                "amount": amount,
                "category": category,
                "description": desc,
                "date": datetime.utcnow().strftime("%Y-%m-%d")
            }).execute()
            print(f"💰 记账: {category} {desc} ¥{amount}")
        except: pass
    
    # BARK指令（AI主动推送额外通知）
    bark_match = re.search(r'\[BARK:([^\|]+)\|([^\|]+)(?:\|([^\|]*))?(?:\|([^\]]*))?\]', ai_reply)
    if bark_match and BARK_KEY:
        try:
            bark_title = bark_match.group(1)
            bark_body = bark_match.group(2)
            bark_group = bark_match.group(3) or ""
            bark_icon = bark_match.group(4) or ""
            async with httpx.AsyncClient() as c:
                url = f"https://api.day.app/{BARK_KEY}/{quote(bark_title)}/{quote(bark_body)}?sound=shake"
                if bark_group:
                    url += f"&group={quote(bark_group)}"
                if bark_icon:
                    url += f"&icon={quote(bark_icon)}"
                await c.get(url, timeout=10)
            print(f"📢 BARK指令推送: [{bark_title}] {bark_body}")
        except Exception as e:
            print(f"⚠️ BARK指令推送失败: {e}")
    
    return {
        "success": True,
        "reply": ai_reply,
        "role_name": role_name,
        "chat_id": req.chat_id
    }

@app.get("/chat/messages/{chat_id}")
async def get_chat_messages(chat_id: str, limit: int = 50):
    """获取某个会话的消息历史"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    r = supabase.table("chat_messages").select("*").eq("chat_id", chat_id).order("created_at", desc=False).limit(limit).execute()
    return {"messages": r.data or []}

@app.get("/chat/all-messages")
async def get_all_chat_messages():
    """获取所有聊天的消息，按chat_id分组返回"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    # 获取最近1000条消息
    r = supabase.table("chat_messages").select("*").order("created_at", desc=False).limit(1000).execute()
    messages = r.data or []
    # 按chat_id分组
    grouped: dict = {}
    for msg in messages:
        chat_id = msg.get("chat_id", "unknown")
        if chat_id not in grouped:
            grouped[chat_id] = []
        # 转换为前端Message格式
        grouped[chat_id].append({
            "id": msg.get("id", str(msg.get("created_at", ""))),
            "role": "assistant" if msg.get("sender") == "assistant" else "user",
            "content": msg.get("content", ""),
            "createdAt": int(datetime.fromisoformat(msg["created_at"].replace("Z", "+00:00").replace("+00:00", "")).timestamp() * 1000) if msg.get("created_at") else 0
        })
    return {"messages": grouped}

@app.post("/chat/delete")
async def delete_chat_messages(data: dict):
    """删除聊天消息"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    chat_id = data.get("chat_id")
    contents = data.get("contents", [])  # 要删除的消息内容列表
    
    deleted = 0
    for content in contents:
        try:
            r = supabase.table("chat_messages").delete().eq("chat_id", chat_id).eq("content", content).execute()
            if r.data:
                deleted += len(r.data)
        except Exception as e:
            print(f"⚠️ 删除消息失败: {e}")
    
    print(f"🗑️ 删除了 {deleted} 条聊天消息")
    return {"success": True, "deleted": deleted}

@app.post("/chat/import")
async def import_chat_messages(data: dict):
    """导入聊天消息到Supabase（让AI能看到）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    chat_id = data.get("chat_id")
    role_id = data.get("role_id")
    messages = data.get("messages", [])  # [{role, content, id}]
    
    imported = 0
    for msg in messages:
        try:
            supabase.table("chat_messages").insert({
                "chat_id": chat_id,
                "role_id": role_id,
                "sender": msg.get("role", "user"),
                "content": msg.get("content", "")
            }).execute()
            imported += 1
        except Exception as e:
            print(f"⚠️ 导入消息失败: {e}")
    
    print(f"📥 导入了 {imported} 条聊天消息")
    return {"success": True, "imported": imported}

@app.get("/chat/latest")
async def get_latest_reply(chat_id: str):
    """获取最新的AI回复（前端轮询用）"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    r = supabase.table("chat_messages").select("*").eq("chat_id", chat_id).eq("sender", "assistant").order("created_at", desc=True).limit(1).execute()
    if r.data:
        return {"has_reply": True, "message": r.data[0]}
    return {"has_reply": False}

@app.post("/ai/behavior")
async def set_behavior(u: AiBehaviorUpdate):
    supabase.table("ai_behavior_settings").upsert({"setting_name": u.setting_name, "setting_value": u.setting_value}, on_conflict="setting_name").execute()
    return {"success": True}

@app.get("/ai/behavior")
async def get_beh():
    return get_behavior()

@app.get("/persona/summary")
async def persona_summary():
    return {"summary": get_persona()}

@app.get("/proactive/pending")
async def pending_proactive():
    ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    r = supabase.table("proactive_messages").select("*").eq("is_read", False).gte("created_at", ago).limit(1).execute()
    if r.data:
        supabase.table("proactive_messages").update({"is_read": True}).eq("id", r.data[0]["id"]).execute()
        return {"has_message": True, "message": r.data[0]["content"], "role": r.data[0].get("role_name")}
    return {"has_message": False}

@app.get("/proactive/debug")
async def debug_proactive():
    """调试主动思考状态"""
    roles = get_all_roles()
    beh = get_behavior()
    recent = supabase.table("proactive_messages").select("content,role_name,created_at").order("created_at", desc=True).limit(5).execute().data or []
    now_beijing = datetime.utcnow() + timedelta(hours=8)
    return {
        "server_time": now_beijing.strftime("%Y-%m-%d %H:%M:%S"),
        "roles_count": len(roles),
        "role_names": [r.get("name") for r in roles],
        "current_active_role_id": beh.get("current_active_role_id"),
        "ai_behavior": beh,
        "last_proactive_time": str(last_pt) if last_pt else None,
        "next_interval_min": next_int,
        "recent_messages": [{"content": m["content"], "role": m.get("role_name"), "at": m["created_at"]} for m in recent],
        "scheduler_running": scheduler.running
    }

@app.post("/proactive/test")
async def test_proactive():
    """手动触发一次主动思考（测试用）"""
    global last_pt, next_int
    # 重置时间，强制触发
    last_pt = None
    next_int = None
    await proactive_thinking()
    # 返回最新消息
    recent = supabase.table("proactive_messages").select("content,role_name,created_at").order("created_at", desc=True).limit(1).execute().data
    if recent:
        return {"triggered": True, "message": recent[0]}
    return {"triggered": True, "message": None, "note": "AI决定PASS，没有发消息"}

class ProactiveRequest(BaseModel):
    role_id: Optional[str] = None
    context: Optional[str] = None

@app.post("/proactive/generate")
async def generate_proactive(req: ProactiveRequest):
    """手动生成主动消息"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    
    roles = get_all_roles()
    if not roles:
        return {"message": "", "error": "无角色"}
    
    # 找指定角色或活跃角色
    role = None
    if req.role_id:
        role = next((r for r in roles if r.get("id") == req.role_id), None)
    if not role:
        active_id = get_behavior().get("current_active_role_id")
        role = next((r for r in roles if r.get("id") == active_id), roles[0])
    
    # 环境数据
    hr = (datetime.utcnow().hour + 8) % 24
    gps = supabase.table("gps_history").select("address,battery").order("created_at", desc=True).limit(1).execute().data
    chats = supabase.table("chat_messages").select("content").order("created_at", desc=True).limit(10).execute().data or []
    persona = get_persona()
    
    env = f"时间：{hr}点\n位置：{gps[0].get('address','未知') if gps else '未知'}"
    chat_ctx = "\n".join([c["content"][:80] for c in chats]) or "无"
    extra = f"\n额外上下文：{req.context}" if req.context else ""
    
    prompt = f"{env}\n\n【画像】\n{persona or '无'}\n\n【聊天】\n{chat_ctx}{extra}\n\n---\n你想主动跟用户说什么？直接输出消息内容。"
    
    sp = build_role_prompt(role)
    sys = sp if sp else "你是用户的AI助手"
    
    msg = (await call_ai(sys, prompt)).strip()
    
    if msg:
        name = role.get("name", "AI")
        supabase.table("proactive_messages").insert({
            "role_id": role.get("id"),
            "role_name": name,
            "content": msg,
            "trigger": "manual_generate"
        }).execute()
        return {"message": msg, "role": name}
    return {"message": ""}

@app.get("/sync/load")
async def sync_load():
    r = supabase.table("user_sync").select("*").eq("user_id", "default_user").limit(1).execute()
    if r.data:
        return {"found": True, "data": {k: r.data[0].get(k) for k in ["chats", "messages", "roles", "api_settings", "chat_settings", "user_profile", "updated_at"]}}
    return {"found": False}

@app.post("/sync/save")
async def sync_save(d: SyncData):
    data = {"user_id": "default_user", "updated_at": datetime.utcnow().isoformat()}
    if d.chats is not None: data["chats"] = d.chats
    if d.messages is not None: data["messages"] = d.messages
    if d.roles is not None: data["roles"] = d.roles
    if d.api_settings is not None: data["api_settings"] = d.api_settings
    if d.chat_settings is not None: data["chat_settings"] = d.chat_settings
    if d.user_profile is not None: data["user_profile"] = d.user_profile
    supabase.table("user_sync").upsert(data, on_conflict="user_id").execute()
    return {"success": True}

@app.post("/bark/push")
async def bark_push(title: str, body: str, sound: str = "shake", group: str = None):
    """通过Bark推送消息到手机"""
    if not BARK_KEY:
        return {"success": False, "error": "BARK_KEY not configured"}
    try:
        async with httpx.AsyncClient() as c:
            url = f"https://api.day.app/{BARK_KEY}/{quote(title)}/{quote(body)}?sound={sound}"
            if group:
                url += f"&group={quote(group)}"
            await c.get(url, timeout=10)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

class BarkRequest(BaseModel):
    title: str
    body: str
    sound: str = "shake"
    group: Optional[str] = None

@app.post("/bark/send")
async def bark_send(req: BarkRequest):
    """通过Bark推送消息到手机（JSON body）"""
    if not BARK_KEY:
        return {"success": False, "error": "BARK_KEY not configured"}
    try:
        async with httpx.AsyncClient() as c:
            url = f"https://api.day.app/{BARK_KEY}/{quote(req.title)}/{quote(req.body)}?sound={req.sound}"
            if req.group:
                url += f"&group={quote(req.group)}"
            await c.get(url, timeout=10)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/health")
async def health():
    return {"status": "ok", "supabase": "connected" if supabase else "no", "bark": "configured" if BARK_KEY else "no"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
