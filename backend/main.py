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
    latitude: float
    longitude: float
    address: Optional[str] = None
    battery: Optional[int] = None
    wifi: Optional[str] = None
    app: Optional[str] = None
    charging: bool = False
    screen_on: bool = True

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

# 定时任务
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

last_pt, next_int = None, None
async def proactive_thinking():
    global last_pt, next_int
    if not supabase: return
    now = datetime.utcnow()
    hr = (now.hour + 8) % 24
    beh = get_behavior()
    mi, ma = int(beh.get("min_interval") or 30), int(beh.get("max_interval") or 120)
    if 3 <= hr < 7: mi, ma = 180, 300
    
    nds, nde = beh.get("no_disturb_start"), beh.get("no_disturb_end")
    if nds and nde:
        nds, nde = int(nds), int(nde)
        if (nds <= hr < nde) if nds < nde else (hr >= nds or hr < nde): return
    
    if last_pt:
        if next_int is None: next_int = random.randint(mi, ma)
        if (now - last_pt).total_seconds()/60 < next_int: return
    
    last_pt, next_int = now, random.randint(mi, ma)
    
    # 环境
    chats = supabase.table("chat_messages").select("content").order("created_at", desc=True).limit(10).execute().data or []
    gps = supabase.table("gps_history").select("address,battery").order("created_at", desc=True).limit(1).execute().data
    persona = get_persona()
    
    env = f"时间：{hr}点\n位置：{gps[0].get('address','未知') if gps else '未知'}\n电量：{gps[0].get('battery','?') if gps else '?'}%"
    chat_ctx = "\n".join([c["content"][:80] for c in chats]) or "无"
    prompt = f"{env}\n\n【画像】\n{persona or '无'}\n\n【聊天】\n{chat_ctx}\n\n---\n判断是否发消息。不发回PASS，发回MESSAGE:内容"
    
    roles = get_all_roles()
    if not roles: return
    active = beh.get("current_active_role_id")
    roles = sorted(roles, key=lambda r: 0 if r.get("id") == active else 1)
    
    winner, msg = None, None
    for role in roles:
        sp = build_role_prompt(role)
        sys = f"{sp}\n只输出PASS或MESSAGE:内容" if sp else "只输出PASS或MESSAGE:内容"
        try:
            dec = (await call_ai(sys, prompt)).strip()
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
        "battery": d.battery, "wifi": d.wifi, "app": d.app,
        "charging": d.charging, "screen_on": d.screen_on
    }).execute()
    print(f"📍 GPS: {d.address or f'{d.latitude},{d.longitude}'} | 电量:{d.battery}% | 充电:{d.charging}")
    
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
    return {
        "roles_count": len(roles),
        "role_names": [r.get("name") for r in roles],
        "current_active_role_id": beh.get("current_active_role_id"),
        "ai_behavior": beh,
        "last_proactive_time": str(last_pt) if last_pt else None,
        "next_interval_min": next_int,
        "recent_messages": [{"content": m["content"], "role": m.get("role_name"), "at": m["created_at"]} for m in recent],
        "scheduler_running": scheduler.running
    }

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
        return {"found": True, "data": {k: r.data[0].get(k) for k in ["chats", "roles", "api_settings", "chat_settings", "user_profile"]}}
    return {"found": False}

@app.post("/sync/save")
async def sync_save(d: SyncData):
    data = {"user_id": "default_user", "updated_at": datetime.utcnow().isoformat()}
    if d.chats is not None: data["chats"] = d.chats
    if d.roles is not None: data["roles"] = d.roles
    if d.api_settings is not None: data["api_settings"] = d.api_settings
    if d.chat_settings is not None: data["chat_settings"] = d.chat_settings
    if d.user_profile is not None: data["user_profile"] = d.user_profile
    supabase.table("user_sync").upsert(data, on_conflict="user_id").execute()
    return {"success": True}

@app.get("/health")
async def health():
    return {"status": "ok", "supabase": "connected" if supabase else "no"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
