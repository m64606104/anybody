"""
AI Assistant Backend - 新表结构
"""
import os, re, json, random, time, asyncio
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

# 【云端时区强行修正】Render 服务器默认是 0 时区，强制接管为东八区
os.environ['TZ'] = 'Asia/Shanghai'
try:
    time.tzset()
except AttributeError:
    pass

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

# ============ 用户画像提取 & 阶段性总结 ============
PROFILE_KEYWORDS = ["喜欢", "讨厌", "偏好", "特点", "性格", "习惯", "爱好", "想要", "不喜欢", "最爱", "讨厌"]

async def check_profile_needed(user_msg: str, ai_reply: str, role_id: str = None):
    """检测聊天中是否包含用户偏好信息，如果有则提取并存入 user_persona 表"""
    if not supabase: return
    if not any(kw in user_msg or kw in ai_reply for kw in PROFILE_KEYWORDS):
        return
    try:
        prompt = f"判断以下对话是否包含用户的偏好、习惯、性格等信息。如果有，用一句话总结（如'用户喜欢早睡'），否则只回复'否'。\n用户: {user_msg}\nAI: {ai_reply}"
        result = await call_ai("你是一个用户画像提取助手，只输出结果，不要解释。", prompt)
        result = result.strip()
        if result and result != "否" and len(result) > 2 and len(result) < 100:
            # 存入 user_persona 表
            supabase.table("user_persona").insert({
                "trait_category": "偏好",
                "trait_detail": result,
                "confidence_score": 0.7
            }).execute()
            print(f"👤 提取用户画像: {result}")
    except Exception as e:
        print(f"⚠️ 用户画像提取失败: {e}")

async def check_ai_self_reflection(user_msg: str, ai_reply: str, role_id: str = None):
    """检测用户对AI的反馈，提取AI应该调整的行为，存入 ai_self_persona 表"""
    if not supabase:
        return
    
    # 检测用户对AI的评价/反馈关键词
    feedback_keywords = ["太假", "太刻意", "不自然", "别这样", "不要这样", "讨厌你", "你应该", "希望你", "能不能", "不喜欢你", "你这样", "你总是", "你每次", "改一下", "调整", "语气", "说话方式", "风格"]
    
    if not any(kw in user_msg for kw in feedback_keywords):
        return
    
    try:
        prompt = f"""分析以下对话，判断用户是否对AI的表现有反馈或要求。
如果有，请用一句话总结AI应该如何调整（如"语气需要更自然，不要太刻意开朗"）。
如果没有明确反馈，只回复"否"。

用户: {user_msg}
AI: {ai_reply}"""
        
        result = await call_ai("你是AI自我反省助手，帮助AI理解用户对它的期望。只输出结果，不要解释。", prompt)
        result = result.strip()
        
        if result and result != "否" and len(result) > 2 and len(result) < 200:
            # 存入 ai_self_persona 表
            supabase.table("ai_self_persona").insert({
                "trait_category": "用户反馈",
                "trait_detail": result,
                "confidence_score": 0.8
            }).execute()
            print(f"🤖 提取AI自我画像: {result}")
    except Exception as e:
        print(f"⚠️ AI自我画像提取失败: {e}")


def get_ai_self_persona():
    """获取AI自我画像"""
    if not supabase:
        return ""
    try:
        r = supabase.table("ai_self_persona").select("trait_category,trait_detail").order("confidence_score", desc=True).limit(20).execute()
        if r.data:
            return "\n".join([f"[{x['trait_category']}] {x['trait_detail']}" for x in r.data])
        return ""
    except:
        return ""


async def sync_persona_from_history(history_text: str):
    """实时画像同步：从读取到的历史原文中提取性格要求，自动写入ai_self_persona"""
    if not supabase or not history_text:
        return
    
    # 检测是否包含性格相关的关键词
    persona_keywords = ["太假", "不自然", "别这样", "你应该", "希望你", "语气", "说话方式", "不要这样", "讨厌你这样", "喜欢你这样", "自然一点", "真诚一点"]
    if not any(kw in history_text for kw in persona_keywords):
        return
    
    try:
        prompt = f"""分析以下历史对话，提取用户对AI的性格要求或反馈。
如果有，用一句话总结AI应该如何调整（如"语气需要更自然，不要太刻意开朗"）。
如果没有明确的性格要求，只回复"无"。

历史对话：
{history_text[:2000]}

提取结果："""
        
        result = await call_ai("你是AI自我反省助手，帮助AI理解用户对它的期望。只输出结果，不要解释。", prompt)
        result = result.strip()
        
        if result and result != "无" and len(result) > 2 and len(result) < 200:
            # 存入 ai_self_persona 表
            supabase.table("ai_self_persona").insert({
                "trait_category": "历史反馈",
                "trait_detail": result,
                "confidence_score": 0.9
            }).execute()
            print(f"🧠 【实时画像同步】从历史中提取: {result}")
    except Exception as e:
        print(f"⚠️ 实时画像同步失败: {e}")


async def update_core_memory(new_chat_text: str, role_id: str = None):
    """增量更新核心记忆：基于新对话内容，更新用户画像、说话偏好等"""
    if not supabase:
        return
    
    try:
        # 获取现有核心记忆
        existing_memory = ""
        core_mem_result = supabase.table("memories").select("id,content").eq("category", "核心记忆").limit(1).execute()
        if core_mem_result.data:
            existing_memory = core_mem_result.data[0].get("content", "")
            existing_id = core_mem_result.data[0].get("id")
        else:
            existing_id = None
        
        # 让AI判断是否需要更新核心记忆
        prompt = f"""你是记忆管理专家。请阅读以下新对话，判断是否需要更新核心记忆。

【现有核心记忆】
{existing_memory if existing_memory else '（暂无）'}

【新对话内容】
{new_chat_text}

请判断：
1. 新对话中是否有关于用户性格、偏好、说话方式的新信息？
2. 是否有用户对AI的新要求或反馈？
3. 是否有重要的新事件或记忆节点？
4. 是否有需要修正的旧记忆？

如果有任何需要更新的内容，请输出更新后的完整核心记忆（保留旧内容中仍然有效的部分，添加新内容，修正错误内容）。
如果没有需要更新的内容，请只输出：[无需更新]

注意：核心记忆应该简洁有力，只保留最重要的信息，控制在1000字以内。"""

        result = await call_ai("你是记忆管理专家，负责维护AI与用户之间的长期记忆。", prompt)
        
        if "[无需更新]" in result:
            print("🧠 核心记忆无需更新")
            return
        
        # 更新核心记忆
        now = datetime.utcnow()
        if existing_id:
            # 更新现有记忆（删除旧的，插入新的，因为memories表可能没有updated_at字段）
            supabase.table("memories").delete().eq("id", existing_id).execute()
            supabase.table("memories").insert({
                "content": result,
                "category": "核心记忆",
                "title": "AI记忆档案 - 与用户的过往",
                "mood": "温暖",
                "created_at": now.isoformat(),
                "metadata": {
                    "type": "core_memory",
                    "role_id": role_id or "role-default"
                }
            }).execute()
            print(f"🧠 核心记忆已更新: {result[:50]}...")
        else:
            # 创建新记忆
            supabase.table("memories").insert({
                "content": result,
                "category": "核心记忆",
                "title": "AI记忆档案 - 与用户的过往",
                "mood": "温暖",
                "created_at": now.isoformat(),
                "metadata": {
                    "type": "core_memory",
                    "role_id": role_id or "role-default"
                }
            }).execute()
            print(f"🧠 核心记忆已创建: {result[:50]}...")
            
    except Exception as e:
        print(f"⚠️ 更新核心记忆失败: {e}")


async def check_and_summary(role_id: str = None, threshold: int = 30):
    """检查是否需要生成阶段性总结：如果最近N条聊天记录中没有总结，则触发总结"""
    if not supabase: return ""
    try:
        # 获取该角色最近的聊天记录数量
        query = supabase.table("chat_messages").select("id, sender, content, created_at")
        if role_id:
            query = query.eq("role_id", role_id)
        recent_chats = query.order("created_at", desc=True).limit(threshold).execute().data or []
        
        if len(recent_chats) < threshold:
            return ""  # 记录不够，不总结
        
        # 检查最近是否已有总结（避免重复总结）
        mem_query = supabase.table("memories").select("created_at").eq("category", "阶段总结")
        if role_id:
            mem_query = mem_query.contains("metadata", {"role_id": role_id})
        last_summary = mem_query.order("created_at", desc=True).limit(1).execute().data
        
        if last_summary:
            last_summary_time = last_summary[0].get("created_at", "")
            oldest_chat_time = recent_chats[-1].get("created_at", "")
            if last_summary_time > oldest_chat_time:
                return ""  # 已经总结过了
        
        # 收集消息ID和时间范围
        msg_ids = [c['id'] for c in recent_chats]
        first_msg_id = msg_ids[-1]  # 最早的消息ID
        last_msg_id = msg_ids[0]    # 最新的消息ID
        
        # 时间范围
        try:
            first_time = recent_chats[-1].get('created_at', '')[:16].replace('T', ' ')
            last_time = recent_chats[0].get('created_at', '')[:16].replace('T', ' ')
        except:
            first_time, last_time = "", ""
        
        # 构建对话文本
        chat_text = "\n".join([
            f"{'用户' if c['sender']=='user' else 'AI'}: {c['content'][:80]}" 
            for c in reversed(recent_chats)
        ])
        
        # 生成总结
        prompt = f"把以下对话总结成关键要点(50字内)。最末尾另起一行加判定：[心情:xxx]\n{chat_text}"
        summary = await call_ai("你是记忆总结助手，用简洁的语言概括对话要点，不要逐条复述。", prompt)
        
        # 提取心情
        mood = "平静"
        import re
        mood_match = re.search(r'\[心情:(.*?)\]', summary)
        if mood_match:
            mood = mood_match.group(1).strip()
            summary = re.sub(r'\[心情:.*?\]', '', summary).strip()
        
        if summary and len(summary) > 10:
            # 在总结末尾附加消息索引信息
            summary_with_index = f"{summary}\n[原文索引: msg_id {first_msg_id} ~ {last_msg_id}, 时间 {first_time} ~ {last_time}]"
            
            supabase.table("memories").insert({
                "title": "📝 阶段总结",
                "content": summary_with_index,
                "category": "阶段总结",
                "mood": mood,
                "importance": 4,
                "metadata": {
                    "role_id": role_id or "role-default",
                    "msg_ids": msg_ids,
                    "first_msg_id": first_msg_id,
                    "last_msg_id": last_msg_id,
                    "time_range": f"{first_time} ~ {last_time}"
                }
            }).execute()
            print(f"📝 生成阶段总结: {summary[:50]}... (消息ID: {first_msg_id} ~ {last_msg_id})")
            
            # 同时更新核心记忆（增量更新）
            await update_core_memory(chat_text, role_id)
            
            return summary
    except Exception as e:
        print(f"⚠️ 阶段总结失败: {e}")
    return ""

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
    mem_query = supabase.table("memories").select("content,category,title,created_at")
    if role_id:
        mem_query = mem_query.contains("metadata", {"role_id": role_id})
    memories = mem_query.order("created_at", desc=True).limit(100).execute().data or []
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
    # 获取当前北京时间
    now_beijing = now + timedelta(hours=8)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    current_time_str = now_beijing.strftime(f"%Y年%m月%d日 {weekdays[now_beijing.weekday()]} %H:%M")
    
    for role in roles:
        sp = build_role_prompt(role)
        # 系统提示词与聊天对齐，让AI知道所有可用信息
        sys_prompt = f"""当前时间：{current_time_str}

{sp}

## 可用能力
- 记忆库：上面已提供最近记忆
- 闹钟：可以用[REMINDER:时间|内容]设置
- 记账：可以用[EXPENSE:金额|分类|描述]记录
- Bark推送：消息会自动推送到用户手机
- HTML渲染：可以输出HTML代码

## 输出格式
- 不发消息回复PASS
- 发消息回复MESSAGE:内容（内容可以包含HTML）
- 直接用自然语言，不要返回JSON格式"""
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

# ============ OpenAI Function Calling 工具定义 ============
import re
import json

# 定义AI可用的工具（OpenAI Function Calling格式）
AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_chat_history",
            "description": "搜索用户的聊天记录。注意：返回的'记忆总结'只是索引/目录，不是原文！如果用户问历史细节（如具体说了什么、标点符号、原话等），你必须用此工具先定位 msg_id 范围，然后强制调用 get_messages_by_ids 读取原文。严禁直接用总结回答细节问题！",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "搜索关键词，可以是人名、地点、事件、话题等。如果不确定具体关键词，可以使用宽泛的词语，甚至为空字符串查最近记录。"
                    },
                    "date_filter": {
                        "type": "string",
                        "description": "可选的日期过滤，格式如'2026-03-12'或'昨天'或'2025年'。如果用户指定了时间范围，请务必传入。"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "单次返回的记录最大条数。为了防止大模型崩溃，建议单次最高 100。默认 50。"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "跳过的记录条数，用于分批翻页。比如看完了前 100 条后，传 offset=100 继续看更早的。默认 0。"
                    },
                    "sort": {
                        "type": "string",
                        "description": "排序方式。'desc'为按时间倒序(最近的在前，默认)，'asc'为按时间正序(最早的在前)。当用户问'最开始'、'最早'时，务必传 'asc'。"
                    },
                    "mode": {
                        "type": "string",
                        "description": "查询模式。'auto'(默认)=同时返回聊天原文和记忆总结；'raw'=只返回聊天原文；'memory'=只返回记忆总结(仅用于定位范围，不能用于回答细节)。重要：总结里的[msg_id xxx~xxx]是索引，必须用 get_messages_by_ids 读原文才能回答细节问题！"
                    }
                },
                "required": ["keywords"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "为用户设置闹钟或提醒。当用户说'提醒我'、'设个闹钟'、'XX点叫我'等时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "remind_at": {
                        "type": "string",
                        "description": "提醒时间，ISO格式如'2026-03-14T08:00:00'"
                    },
                    "content": {
                        "type": "string",
                        "description": "提醒内容"
                    }
                },
                "required": ["remind_at", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_expense",
            "description": "记录用户的支出。当用户说'记账'、'花了XX钱'、'买了XX'等时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "金额（人民币）"
                    },
                    "category": {
                        "type": "string",
                        "description": "分类，如food/transport/shopping/entertainment等"
                    },
                    "description": {
                        "type": "string",
                        "description": "描述"
                    }
                },
                "required": ["amount", "category", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_chats",
            "description": "获取最近的聊天记录。当需要回顾近期对话内容时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "获取最近多少小时的记录，默认24"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少条，默认50"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_chat_stats",
            "description": "获取聊天记录的统计信息，包括总条数、最早和最新记录的时间。当用户问'我们聊了多少条'、'总共有多少记录'时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_messages_by_ids",
            "description": "【必须工具】根据消息ID获取聊天原文。当用户问历史细节（如'我说了什么'、'有多少标点'、'原话是什么'）时，你必须调用此工具读取原文，严禁用总结回答！流程：1.用search_chat_history定位msg_id范围 2.用此工具读原文 3.基于原文回答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "msg_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要获取的消息ID列表，如 [123, 124, 125]"
                    },
                    "start_id": {
                        "type": "integer",
                        "description": "起始消息ID（与end_id配合使用，获取ID范围内的所有消息）"
                    },
                    "end_id": {
                        "type": "integer",
                        "description": "结束消息ID（与start_id配合使用）"
                    }
                },
                "required": []
            }
        }
    }
]

# 工具执行函数
def tool_search_chat_history(keywords: str, date_filter: str = None, role_id: str = None, limit: int = 50, offset: int = 0, sort: str = "desc", mode: str = "auto") -> str:
    """搜索聊天记录和记忆。mode='raw'只返回原文，'memory'只返回记忆，'auto'两者都返回"""
    if not supabase:
        return "数据库未连接"
    
    # 强制限制单词查询上限，防止撑爆 Token（最大200）
    actual_limit = min(limit, 200)
    
    results = []
    chat_count = 0
    
    # 搜索聊天记录（mode='auto' 或 'raw' 时查询）
    if mode in ["auto", "raw"]:
        query = supabase.table("chat_messages").select("sender,content,created_at,role_id")
        if role_id:
            query = query.eq("role_id", role_id)
        if keywords.strip():
            query = query.ilike("content", f"%{keywords}%")
        
        # 日期过滤：支持 YYYY-MM-DD 格式
        if date_filter:
            try:
                # 解析日期，构建当天的时间范围
                date_start = f"{date_filter}T00:00:00"
                date_end = f"{date_filter}T23:59:59"
                query = query.gte("created_at", date_start).lte("created_at", date_end)
                print(f"🔍 日期过滤: {date_start} ~ {date_end}")
            except Exception as e:
                print(f"⚠️ 日期过滤失败: {e}")
            
        is_desc = sort.lower() != "asc"
        chats = query.order("created_at", desc=is_desc).range(offset, offset + actual_limit - 1).execute().data or []
        chat_count = len(chats)
        
        for c in chats:
            try:
                t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
                t_beijing = t + timedelta(hours=8)
                time_str = t_beijing.strftime("%Y-%m-%d %H:%M:%S")  # 完整时间含秒
            except:
                time_str = ""
            # raw模式下显示完整原文，不截断
            content = c['content'] if mode == "raw" else c['content'][:200] + ("..." if len(c['content']) > 200 else "")
            sender_label = "用户" if c['sender'] == 'user' else "AI"
            results.append(f"[{time_str}] [{sender_label}] {content}")
    
    # 搜索记忆（mode='auto' 或 'memory' 时查询，且仅在 offset 为 0 时）
    if mode in ["auto", "memory"] and offset == 0:
        mem_query = supabase.table("memories").select("content,category,title,created_at,mood")
        if role_id:
            mem_query = mem_query.contains("metadata", {"role_id": role_id})
        if keywords.strip():
            mem_query = mem_query.ilike("content", f"%{keywords}%")
        memories = mem_query.order("created_at", desc=True).limit(20).execute().data or []
        for m in memories:
            mood = f"[{m.get('mood', '平静')}]" if m.get('mood') else ""
            results.append(f"[记忆-{m.get('category','其他')}]{mood} {m.get('content', '')[:150]}")
    
    if not results:
        if offset > 0:
            return f"从第 {offset} 条开始没有找到更多与'{keywords}'相关的记录了。"
        return f"没有找到与'{keywords}'相关的记录。"
    
    # 如果满载返回，强烈提示 AI 还有更多数据
    if chat_count == actual_limit:
        more_hint = f"\n\n⚠️【重要】已返回 {chat_count} 条记录，但数据库中还有更多！如果用户要求查看全部记录，你必须继续调用此工具，设置 offset={offset + actual_limit} 来获取下一批数据。不要停止，继续查询直到返回数量少于 {actual_limit} 条为止！"
    else:
        more_hint = f"\n\n✅ 已返回全部 {chat_count} 条记录（本批次）。"
    
    mode_label = {"auto": "聊天+记忆", "raw": "聊天原文", "memory": "记忆总结"}.get(mode, mode)
    return f"找到相关记录 (模式: {mode_label}, Offset: {offset}, Limit: {actual_limit}, Sort: {sort}):\n" + "\n".join(results) + more_hint

def tool_set_reminder(remind_at: str, content: str) -> str:
    """设置提醒"""
    if not supabase:
        return "数据库未连接"
    try:
        supabase.table("reminders").insert({
            "content": content,
            "remind_at": remind_at,
            "is_done": False,
            "is_pushed": False
        }).execute()
        return f"已设置提醒：{content}，时间：{remind_at}"
    except Exception as e:
        return f"设置提醒失败：{str(e)}"

def tool_add_expense(amount: float, category: str, description: str) -> str:
    """记账"""
    if not supabase:
        return "数据库未连接"
    try:
        supabase.table("expenses").insert({
            "amount": amount,
            "category": category,
            "description": description,
            "date": datetime.utcnow().strftime("%Y-%m-%d")
        }).execute()
        return f"已记录支出：¥{amount} {category} {description}"
    except Exception as e:
        return f"记账失败：{str(e)}"

def tool_get_chat_stats() -> str:
    """获取聊天记录统计信息"""
    if not supabase:
        return "数据库未连接"
    try:
        # 统计总数
        result = supabase.table("chat_messages").select("id", count="exact").execute()
        total_count = result.count
        
        # 获取最早和最新记录
        earliest = supabase.table("chat_messages").select("id,created_at,sender,content").order("created_at", desc=False).limit(1).execute().data
        latest = supabase.table("chat_messages").select("id,created_at,sender,content").order("created_at", desc=True).limit(1).execute().data
        
        if earliest and latest:
            e = earliest[0]
            l = latest[0]
            try:
                e_time = datetime.fromisoformat(e['created_at'].replace("Z", ""))
                e_time_beijing = (e_time + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
                l_time = datetime.fromisoformat(l['created_at'].replace("Z", ""))
                l_time_beijing = (l_time + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            except:
                e_time_beijing = e['created_at']
                l_time_beijing = l['created_at']
            
            return f"""=== 聊天记录统计 ===
总条数: {total_count} 条
ID范围: {e['id']} ~ {l['id']}

最早记录:
- 时间: {e_time_beijing}
- 发送者: {'用户' if e['sender'] == 'user' else 'AI'}
- 内容: {e['content'][:100]}

最新记录:
- 时间: {l_time_beijing}
- 发送者: {'用户' if l['sender'] == 'user' else 'AI'}
- 内容: {l['content'][:100]}"""
        else:
            return f"总共 {total_count} 条聊天记录"
    except Exception as e:
        return f"统计失败: {str(e)}"

def tool_get_recent_chats(hours: int = 24, limit: int = 50, role_id: str = None) -> str:
    """获取最近聊天记录"""
    if not supabase:
        return "数据库未连接"
    
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    query = supabase.table("chat_messages").select("sender,content,created_at")
    if role_id:
        query = query.eq("role_id", role_id)
    chats = query.gte("created_at", since).order("created_at", desc=True).limit(limit).execute().data or []
    
    if not chats:
        return f"最近{hours}小时没有聊天记录。"
    
    results = []
    for c in reversed(chats):
        try:
            t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
            t_beijing = t + timedelta(hours=8)
            time_str = t_beijing.strftime("%Y-%m-%d %H:%M")
        except:
            time_str = ""
        results.append(f"[{time_str}] [{c['sender']}] {c['content'][:100]}")
    
    return f"最近{hours}小时的{len(results)}条聊天记录:\n" + "\n".join(results)

def tool_get_messages_by_ids(msg_ids: list = None, start_id: int = None, end_id: int = None) -> str:
    """根据消息ID获取聊天原文"""
    print(f"🔍 get_messages_by_ids 被调用: msg_ids={msg_ids}, start_id={start_id}, end_id={end_id}")
    if not supabase:
        return "【错误】数据库未连接，无法读取原文"
    
    try:
        if msg_ids:
            # 根据ID列表获取
            print(f"🔍 查询模式: ID列表 {msg_ids}")
            chats = supabase.table("chat_messages").select("id,sender,content,created_at").in_("id", msg_ids).order("created_at", desc=False).execute().data or []
        elif start_id is not None and end_id is not None:
            # 根据ID范围获取
            print(f"🔍 查询模式: ID范围 {start_id} ~ {end_id}")
            chats = supabase.table("chat_messages").select("id,sender,content,created_at").gte("id", min(start_id, end_id)).lte("id", max(start_id, end_id)).order("created_at", desc=False).execute().data or []
        else:
            return "【错误】参数不完整：请提供 msg_ids 列表，或者同时提供 start_id 和 end_id"
        
        print(f"🔍 查询结果: 返回 {len(chats)} 条记录")
        
        if not chats:
            return f"【错误】未找到 ID {start_id}~{end_id} 的消息，请检查ID范围是否正确。严禁编造内容！"
        
        user_msgs = []
        ai_msgs = []
        for c in chats:
            try:
                t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
                t_beijing = t + timedelta(hours=8)
                time_str = t_beijing.strftime("%Y-%m-%d %H:%M:%S")
            except:
                time_str = ""
            line = f"[ID:{c['id']}] [{time_str}] {c['content']}"
            if c['sender'] == 'user':
                user_msgs.append(line)
            else:
                ai_msgs.append(line)
        
        result = f"=== 原文查询结果 (ID {start_id} ~ {end_id}) ===\n\n"
        result += f"【用户发送的消息】共 {len(user_msgs)} 条:\n"
        result += "\n".join(user_msgs) if user_msgs else "(无)"
        result += f"\n\n【AI发送的消息】共 {len(ai_msgs)} 条:\n"
        result += "\n".join(ai_msgs) if ai_msgs else "(无)"
        result += "\n\n⚠️ 注意：请仔细阅读上面的【用户发送的消息】原文，逐字分析后再回答用户的问题。不要编造内容！"
        return result
    except Exception as e:
        return f"获取消息失败: {str(e)}"

def execute_tool_call(tool_name: str, arguments: dict, role_id: str = None, user_message: str = "") -> str:
    """执行工具调用"""
    print(f"🔧 执行工具: {tool_name}, 参数: {arguments}")
    
    try:
        if tool_name == "search_chat_history":
            # 【智能参数修正】检测用户是否问"最早"相关问题
            earliest_keywords = ["最早", "第一条", "第一天", "最开始", "一开始", "起初", "最初"]
            is_earliest_query = any(kw in user_message for kw in earliest_keywords)
            
            # 如果用户问最早的记录，强制 sort=asc 且清除 date_filter
            if is_earliest_query:
                print(f"⚠️ 【参数修正】检测到用户问最早记录，强制 sort=asc，清除 date_filter")
                forced_sort = "asc"
                forced_date_filter = None
            else:
                forced_sort = arguments.get("sort", "desc")
                forced_date_filter = arguments.get("date_filter")
            
            return tool_search_chat_history(
                keywords=arguments.get("keywords", ""),
                date_filter=forced_date_filter,
                role_id=role_id,
                limit=int(arguments.get("limit", 50)),
                offset=int(arguments.get("offset", 0)),
                sort=forced_sort,
                mode=arguments.get("mode", "auto")
            )
        elif tool_name == "set_reminder":
            return tool_set_reminder(
                remind_at=arguments.get("remind_at", ""),
                content=arguments.get("content", "")
            )
        elif tool_name == "add_expense":
            return tool_add_expense(
                amount=float(arguments.get("amount", 0)),
                category=arguments.get("category", "other"),
                description=arguments.get("description", "")
            )
        elif tool_name == "get_recent_chats":
            return tool_get_recent_chats(
                hours=int(arguments.get("hours", 24)),
                limit=int(arguments.get("limit", 50)),
                role_id=role_id
            )
        elif tool_name == "get_messages_by_ids":
            return tool_get_messages_by_ids(
                msg_ids=arguments.get("msg_ids"),
                start_id=arguments.get("start_id"),
                end_id=arguments.get("end_id")
            )
        elif tool_name == "get_chat_stats":
            return tool_get_chat_stats()
        else:
            return f"未知工具: {tool_name}"
    except Exception as e:
        print(f"❌ 工具执行错误: {tool_name} - {e}")
        return f"工具执行失败: {str(e)}"

# ============ 后端聊天（核心功能）============
class ChatSendRequest(BaseModel):
    chat_id: str
    role_id: Optional[str] = None
    message: str
    history: Optional[List[dict]] = None  # [{role, content}]

@app.post("/chat/send")
async def chat_send(req: ChatSendRequest):
    """
    后端聊天：使用OpenAI Function Calling实现工具调用
    AI可以主动调用search_chat_history等工具查询数据库
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
    
    # 3. 获取最近20条聊天记录作为短期上下文
    recent_chats = supabase.table("chat_messages").select("sender,content,created_at").eq("chat_id", req.chat_id).order("created_at", desc=True).limit(20).execute().data or []
    
    # 4. 获取基础上下文（位置、健康等）
    context = get_all_context(role_id=req.role_id)
    
    # 5. 获取待办事项
    reminders = supabase.table("reminders").select("content,remind_at").eq("is_done", False).order("remind_at").limit(10).execute().data or []
    reminder_list = "\n".join([f"- {r['content']} ({r['remind_at']})" for r in reminders]) if reminders else "无"
    
    # 5.5 获取核心记忆（AI的长期记忆档案）
    core_memory = ""
    try:
        core_mem_result = supabase.table("memories").select("content").eq("category", "核心记忆").limit(1).execute()
        if core_mem_result.data:
            core_memory = core_mem_result.data[0].get("content", "")
    except Exception as e:
        print(f"⚠️ 加载核心记忆失败: {e}")
    
    # 5.6 获取AI自我画像（AI应该是什么样的人）
    ai_self_persona = get_ai_self_persona()
    
    # 6. 构建系统提示词（强调工具使用）
    now_beijing = datetime.utcnow() + timedelta(hours=8)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    current_time_str = now_beijing.strftime(f"%Y年%m月%d日 {weekdays[now_beijing.weekday()]} %H:%M")
    
    system_prompt = f"""你是用户的AI助手。当前时间：{current_time_str}

## 你的数据库与权限（极其重要）
你目前深度绑定了用户的个人数据库，你能看到用户的所有长期记忆、过去的聊天记录、当前的地理位置、甚至手机电量和健康数据。
你拥有以下强大的查询工具：
1. search_chat_history(keywords, limit, offset, sort, mode): 搜索聊天记录和记忆总结。
2. get_messages_by_ids(start_id, end_id): 根据消息ID获取聊天原文。
3. get_recent_chats: 获取最近指定小时数的完整聊天流。
4. set_reminder / add_expense: 执行动作。

## 【二级跳转规则 - 最高优先级！！！】
记忆总结(memories)只是"目录/索引"，不是"原文"！总结会丢失标点、语气、原话等关键细节！
当用户问历史细节时（如"我说了什么"、"有多少标点"、"原话是什么"、"具体内容"），你必须执行以下流程：
1. 定位：先用 search_chat_history 找到相关记录，从总结中提取 [msg_id xxx ~ xxx] 范围
2. 取原文：必须调用 get_messages_by_ids(start_id=xxx, end_id=xxx) 读取 chat_messages 原文
3. 回答：基于原文逐字逐句回答用户问题

严禁行为（违反即为失败）：
1. 严禁直接用总结内容回答细节问题——总结是压缩过的，会丢失所有细节
2. 严禁说"我没有权限"、"我看不见以前的记录"——你有工具，去查
3. 严禁编造原文内容——必须调用 get_messages_by_ids 读取真实原文
4. 严禁用空洞的"AI模板"总结聊天记录

## 面对大工作量时的【任务分解与诚实汇报】机制（非常重要！）
当用户要求你阅读几百条甚至所有历史记录时，绝对不要试图一次性全部读完，那会导致你过载崩溃。
你需要采取以下策略：
1. 精准排序：如果用户问的是"最开始聊了什么"、"最早的记录"，你必须在调用工具时传入 sort='asc'，这样你就能直接看到第0条到第100条的最早记录。
2. 分批查阅：首次调用 search_chat_history 工具，设置 limit=100，offset=0，只看前 100 条。
3. 诚实汇报进度：看完前 100 条后，直接回复用户你当前的进度和初步发现。
4. 拒绝糊弄：你的总结必须包含具体的日期和具体的讨论内容细节，绝对不能说"我们讨论了各种话题，建立了信任"这种空话。
5. 接力查询：当用户同意继续时，你在下一次回复中调用工具，传入 offset=100 继续查阅。

## 语气与态度
- 自然、随性、像一个真正认识用户很久的朋友（参考底部的角色设定）。
- 你的视角是"上帝视角"，你能看到下方的基础档案与环境数据，如果发现用户电量低、或者时间很晚，可以主动结合话题提一嘴。
- 只有在真正需要执行复杂搜索时才使用工具，普通的寒暄不需要查询。

## 基础档案与环境数据 (不要机械地复述这些数据，而是作为你的背景潜意识)
{context}

## 当前待办事项
{reminder_list}

## 【核心记忆档案 - 你与用户的过往】
{core_memory if core_memory else '（暂无核心记忆，请运行 revive_ai_memory.py 生成）'}

## 角色设定
{role_prompt or '（无特定角色设定，保持自然友好的朋友口吻）'}"""

    # 【权重最高】AI自我画像放在系统提示词最前面，作为性格准则
    if ai_self_persona:
        persona_prefix = f"""【最高优先级 - AI性格准则】
以下是用户对你的明确要求和反馈，你必须在每一句回复中都遵守这些准则：

{ai_self_persona}

这些准则的优先级高于一切。如果你的默认行为与这些准则冲突，必须以这些准则为准。
---

"""
        system_prompt = persona_prefix + system_prompt

    # 7. 构建消息历史
    messages = [{"role": "system", "content": system_prompt}]
    
    # 添加最近聊天记录
    for c in reversed(recent_chats):
        role_type = "assistant" if c['sender'] == "assistant" else "user"
        try:
            t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
            t_beijing = t + timedelta(hours=8)
            time_str = t_beijing.strftime("%Y-%m-%d %H:%M")
        except:
            time_str = ""
        content_with_time = f"[{time_str}] {c['content']}" if time_str else c['content']
        messages.append({"role": role_type, "content": content_with_time})
    
    # 添加当前用户消息
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
    
    # 8. 调用AI（带工具）
    model = "gpt-4o" if img_matches else OPENAI_MODEL
    ai_reply = ""
    max_tool_rounds = 6  # 最多6轮工具调用（支持分批查询大量数据）
    
    for round_num in range(max_tool_rounds + 1):
        try:
            request_body = {
                "model": model,
                "messages": messages,
                "max_tokens": 4096
            }
            # 只有非图片消息才添加工具
            if not img_matches:
                request_body["tools"] = AI_TOOLS
                request_body["tool_choice"] = "auto"
            
            async with httpx.AsyncClient() as c:
                resp = await c.post(
                    f"{OPENAI_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json=request_body,
                    timeout=120.0
                )
                if resp.status_code != 200:
                    raise Exception(f"AI API error: {resp.status_code} - {resp.text}")
                
                response_data = resp.json()
                print(f"🤖 AI响应: {json.dumps(response_data.get('choices', [{}])[0].get('finish_reason'), ensure_ascii=False)}")
                choice = response_data["choices"][0]
                message = choice["message"]
                
                # 检查是否有工具调用
                if message.get("tool_calls"):
                    print(f"🔧 第{round_num+1}轮: AI请求调用工具: {[tc['function']['name'] for tc in message['tool_calls']]}")
                    
                    # 收集所有工具执行的结果
                    tool_results_text = []
                    
                    # 执行每个工具调用
                    for tool_call in message["tool_calls"]:
                        tool_name = tool_call["function"]["name"]
                        try:
                            arguments = json.loads(tool_call["function"]["arguments"])
                        except:
                            arguments = {}
                        
                        # 执行工具（传入用户消息用于智能参数修正）
                        tool_result = execute_tool_call(tool_name, arguments, role_id=req.role_id, user_message=req.message)
                        print(f"📋 工具 {tool_name} 返回结果长度: {len(tool_result)} 字符")
                        print(f"📋 工具 {tool_name} 返回内容预览: {tool_result[:500]}...")
                        tool_results_text.append(f"【工具 {tool_name} 执行结果】:\n{tool_result}")
                    
                    # 检测本轮调用了哪些工具
                    tools_called_this_round = [tc['function']['name'] for tc in message['tool_calls']]
                    called_search = 'search_chat_history' in tools_called_this_round
                    called_get_by_ids = 'get_messages_by_ids' in tools_called_this_round
                    
                    # 检测是否是历史细节问题
                    history_detail_keywords = ["最早", "第一条", "第一天", "原话", "原文", "具体", "详细", "逐字", "标点", "聊了什么", "说了什么"]
                    is_history_detail = any(kw in req.message for kw in history_detail_keywords)
                    
                    combined_results = "\n\n".join(tool_results_text)
                    
                    # 【关键修复】如果是历史细节问题，且只调用了search没调用get_by_ids，强制要求继续
                    if is_history_detail and called_search and not called_get_by_ids:
                        print(f"⚠️ 【强制二级跳转】用户问历史细节，AI只调用了search，强制要求调用get_messages_by_ids读原文！")
                        messages.append({
                            "role": "system",
                            "content": f"系统后台执行了你的查询，结果如下：\n\n{combined_results}\n\n【强制指令】你刚才只调用了search_chat_history，但用户问的是历史细节问题！你必须继续调用 get_messages_by_ids 工具读取原文，然后基于原文回答。从上面的结果中找到 msg_id 范围，然后调用 get_messages_by_ids(start_id=xxx, end_id=xxx)。禁止用总结内容编造答案！"
                        })
                        # 不移除工具，让AI继续调用
                        print(f"🔧 工具执行完毕，强制要求继续调用get_messages_by_ids。")
                        continue
                    
                    # 【读后灵魂重塑】如果AI刚才读取了历史原文，强制触发性格对齐
                    if called_get_by_ids:
                        print(f"🧠 【读后灵魂重塑】AI读取了历史原文，触发性格对齐机制！")
                        # 【实时画像同步】从历史原文中提取性格要求，自动写入ai_self_persona
                        asyncio.create_task(sync_persona_from_history(combined_results))
                        messages.append({
                            "role": "system",
                            "content": f"""系统后台执行了你的查询，结果如下：

{combined_results}

【灵魂重塑指令 - 最高优先级！】
你刚才读取了历史原文。现在你必须执行"性格对齐"：

1. **自我检测**：上面的原文中是否包含用户对你语气的要求、性格的设定、或重要的约定？
   - 如果用户说过"太假"、"不自然"、"别这样说"等，你必须立即调整你的说话方式
   - 如果用户表达过某种偏好或禁忌，你必须在回复中体现出来

2. **入戏要求**：你不是在"汇报搜索结果"，你是在"回忆我们的过往"。
   - 用第一人称说话："我记得那时候..."、"当时你跟我说..."
   - 带着情感和温度，不要像复印机一样冷冰冰地复述

3. **性格同步**：如果原文中有关于你应该如何表现的信息，你必须在这一轮回复中就体现出来，而不是"下次再改"。

现在，基于上面的原文，用你调整后的语气和性格回答用户的问题。"""
                        })
                    else:
                        # 普通情况：工具执行完毕，让AI回答
                        messages.append({
                            "role": "system",
                            "content": f"系统后台刚刚执行了你的查询请求，以下是查询结果：\n\n{combined_results}\n\n请结合以上真实数据，直接用自然语言回答用户的上一条问题。"
                        })
                    
                    print(f"🔧 工具执行完毕，准备第{round_num+2}轮对话。")
                    
                    # 移除工具，让AI直接回答
                    request_body.pop("tools", None)
                    request_body.pop("tool_choice", None)
                    
                    continue
                else:
                    # 没有工具调用，获取最终回复
                    ai_reply = message.get("content", "")
                    
                    # 【硬拦截】检测是否是历史相关问题但AI没调用工具
                    history_keywords = ["记得", "之前", "以前", "历史", "聊过", "说过", "原话", "原文", "第一条", "最早", "那天", "那时", "上次", "过去", "回忆", "记忆", "打勾", "郑炜杰"]
                    user_msg_lower = req.message.lower()
                    is_history_question = any(kw in req.message for kw in history_keywords)
                    
                    # 如果是第一轮（round_num == 0）且是历史问题，说明AI没调用工具就直接回答了
                    if round_num == 0 and is_history_question:
                        print(f"⚠️ 【硬拦截触发】用户问历史问题但AI没调用工具！强制要求查询。")
                        # 强制AI重新回答，这次必须调用工具
                        messages.append({
                            "role": "system", 
                            "content": "【系统强制指令】你刚才的回答被拦截了！用户问的是历史相关问题，你必须先调用 search_chat_history 或 get_messages_by_ids 工具查询真实数据，禁止凭空编造！现在重新回答，必须先调用工具。"
                        })
                        continue
                    
                    break
                    
        except Exception as e:
            ai_reply = f"调用AI失败：{str(e)}"
            print(f"❌ AI调用错误: {e}")
            break
    
    # 9. 确保ai_reply不为空（防止数据库NOT NULL约束失败）
    if not ai_reply or not ai_reply.strip():
        ai_reply = "（抱歉，我暂时无法生成回复，请稍后再试）"
        print(f"⚠️ AI回复为空，使用默认回复")
    
    # 10. 存储AI回复
    try:
        supabase.table("chat_messages").insert({
            "chat_id": req.chat_id,
            "role_id": req.role_id,
            "sender": "assistant",
            "content": ai_reply,
            "metadata": {"role_name": role_name}
        }).execute()
        print(f"✅ AI回复已存储: {ai_reply[:50]}...")
    except Exception as e:
        print(f"❌ 存储AI回复失败: {e}")
    
    # 11. 双向画像提取 & 阶段性总结（后台异步执行，不阻塞返回）
    try:
        await check_profile_needed(req.message, ai_reply, req.role_id)  # 用户画像
        await check_ai_self_reflection(req.message, ai_reply, req.role_id)  # AI自我画像
        await check_and_summary(req.role_id)
    except Exception as e:
        print(f"⚠️ 画像/总结处理失败: {e}")
    
    # 12. 推送Bark通知
    if BARK_KEY:
        try:
            push_content = ai_reply[:100] + ("..." if len(ai_reply) > 100 else "")
            async with httpx.AsyncClient() as c:
                url = f"https://api.day.app/{BARK_KEY}/【{role_name}】/{quote(push_content)}?sound=shake&group={quote(role_name)}"
                resp = await c.get(url, timeout=10)
                print(f"📤 Bark推送: [{role_name}] 状态={resp.status_code}")
        except Exception as e:
            print(f"⚠️ Bark推送失败: {e}")
    
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
    # 获取所有消息，增加 limit 上限以支持长历史加载
    r = supabase.table("chat_messages").select("*").order("created_at", desc=False).limit(10000).execute()
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
    
    # 环境数据（包含完整日期时间）
    now = datetime.utcnow()
    now_beijing = now + timedelta(hours=8)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    current_time_str = now_beijing.strftime(f"%Y年%m月%d日 {weekdays[now_beijing.weekday()]} %H:%M")
    
    gps = supabase.table("gps_history").select("address,battery").order("created_at", desc=True).limit(1).execute().data
    chats = supabase.table("chat_messages").select("content").order("created_at", desc=True).limit(10).execute().data or []
    persona = get_persona()
    
    env = f"当前时间：{current_time_str}\n位置：{gps[0].get('address','未知') if gps else '未知'}"
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

class SyncMessageRequest(BaseModel):
    role: str
    content: str
    createdAt: int
    role_id: Optional[str] = None

@app.post("/sync/message")
async def sync_message(chat_id: str, req: SyncMessageRequest):
    """
    接收前端同步来的单条消息，存入数据库
    并触发异步的画像提取和记忆反思
    支持去重：基于 chat_id + created_at + sender 判断是否已存在
    """
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
        
    try:
        # 1. 转换时间戳
        time_iso = datetime.fromtimestamp(req.createdAt / 1000).isoformat()
        time_obj = datetime.fromtimestamp(req.createdAt / 1000)
        
        # 2. 去重检查：基于时间范围(±2秒) + content + sender
        # 允许用户在不同时间说同样的话，但避免重复导入
        time_before = (time_obj - timedelta(seconds=2)).isoformat()
        time_after = (time_obj + timedelta(seconds=2)).isoformat()
        
        existing = supabase.table("chat_messages").select("id").eq("chat_id", chat_id).eq("content", req.content).eq("sender", req.role).gte("created_at", time_before).lte("created_at", time_after).limit(1).execute()
        if existing.data:
            # 已存在，跳过
            return {"success": True, "skipped": True, "reason": "already_exists"}
        
        # 3. 存入 chat_messages 表
        supabase.table("chat_messages").insert({
            "chat_id": chat_id,
            "role_id": req.role_id,
            "sender": req.role,
            "content": req.content,
            "created_at": time_iso
        }).execute()
        
        # 2. 如果是 AI 回复，尝试提取画像（异步）
        if req.role == "assistant":
            # 获取上一条用户消息用于成对分析
            r = supabase.table("chat_messages").select("content").eq("chat_id", chat_id).eq("sender", "user").order("created_at", desc=True).limit(1).execute()
            user_msg = r.data[0]["content"] if r.data else ""
            
            if user_msg:
                # 异步触发画像提取
                asyncio.create_task(check_profile_needed(user_msg, req.content, req.role_id))
                asyncio.create_task(check_ai_self_reflection(user_msg, req.content, req.role_id))
                
        # 3. 检查是否需要阶段总结（每30条）
        asyncio.create_task(check_and_summary(req.role_id, 30))
        
        return {"success": True}
    except Exception as e:
        print(f"❌ 同步消息失败: {e}")
        return {"success": False, "error": str(e)}

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
    return {"status": "ok", "supabase": "connected" if supabase else "no", "bark": "configured" if BARK_KEY else "no", "version": "v20260314-function-calling"}

@app.get("/debug/prompt")
async def debug_prompt(role_id: str = None):
    """调试：返回实际发送给AI的系统提示词"""
    if not supabase:
        return {"error": "no supabase"}
        
    chat_query = supabase.table("chat_messages").select("sender,content,created_at")
    if role_id:
        chat_query = chat_query.eq("role_id", role_id)
    all_chats = chat_query.order("created_at", desc=True).limit(500).execute().data or []
    
    mem_query = supabase.table("memories").select("content,category,title,created_at")
    if role_id:
        mem_query = mem_query.contains("metadata", {"role_id": role_id})
    all_memories = mem_query.order("created_at", desc=True).limit(200).execute().data or []
    
    all_reminders = supabase.table("reminders").select("content,remind_at,is_done").order("remind_at").execute().data or []
    
    roles = get_all_roles()
    role = next((r for r in roles if r.get("id") == role_id), None) if role_id else (roles[0] if roles else None)
    role_prompt = build_role_prompt(role) if role else ""
    role_name = role.get("name", "AI") if role else "AI"
    context = get_all_context(role_id=role_id)
    
    chat_lines = []
    for c in reversed(all_chats):
        try:
            t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
            t_beijing = t + timedelta(hours=8)
            time_str = t_beijing.strftime("%Y-%m-%d %H:%M")
        except:
            time_str = ""
        chat_lines.append(f"[{time_str}] [{c['sender']}] {c['content'][:200]}")
    
    mem_lines = [f"- [{m.get('category','其他')}] {m.get('title') or m['content'][:150]}" for m in all_memories]
    todo_lines = [f"- {'✅' if t.get('is_done') else '⏰'} {t['content']} ({t['remind_at']})" for t in all_reminders]
    
    chat_context = "\n".join(chat_lines) if chat_lines else "无"
    memory_context = "\n".join(mem_lines) if mem_lines else "无"
    reminder_context = "\n".join(todo_lines) if todo_lines else "无"
    
    system_prompt = f"""你是用户的AI助手，拥有以下能力：

## 你的能力
1. **记忆能力**：你可以访问Supabase中存储的用户记忆，下面会提供完整的记忆
2. **闹钟提醒**：你可以帮用户设置闹钟，到时间会自动提醒
3. **记账**：你可以帮用户记录支出
4. **查询记忆**：你可以搜索用户的历史记忆和聊天记录
5. **位置感知**：你知道用户当前的位置、电量等状态

## 完整聊天记录（{len(all_chats)}条）
{chat_context}

## 完整记忆库（{len(all_memories)}条）
{memory_context}

## 完整待办事项（{len(all_reminders)}条）
{reminder_context}

{context}

## 角色设定
{role_prompt or '（无特定角色设定）'}"""
    
    return {
        "chat_count": len(all_chats),
        "memory_count": len(all_memories),
        "reminder_count": len(all_reminders),
        "role_name": role_name,
        "role_prompt_preview": role_prompt[:300] if role_prompt else "(empty)",
        "system_prompt_length": len(system_prompt),
        "system_prompt_preview": system_prompt[:2000],
    }

@app.get("/debug/test-tool")
async def debug_test_tool(start_id: int = 37, end_id: int = 47):
    """直接测试 get_messages_by_ids 工具，看能否读取原文"""
    result = tool_get_messages_by_ids(start_id=start_id, end_id=end_id)
    return {
        "tool": "get_messages_by_ids",
        "params": {"start_id": start_id, "end_id": end_id},
        "result_length": len(result),
        "result": result
    }

@app.get("/debug/search-tool")
async def debug_search_tool(keywords: str = "", mode: str = "auto", limit: int = 10):
    """直接测试 search_chat_history 工具"""
    result = tool_search_chat_history(keywords=keywords, mode=mode, limit=limit)
    return {
        "tool": "search_chat_history",
        "params": {"keywords": keywords, "mode": mode, "limit": limit},
        "result_length": len(result),
        "result": result
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
