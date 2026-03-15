"""
混合语境注入模式 - 流式聊天接口
前端打包历史记录和角色设定，后端持续生成并流式返回
"""
import os, re, json
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from urllib.parse import quote
import httpx

router = APIRouter()

# 环境变量
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BARK_KEY = os.getenv("BARK_KEY")

class HistoryMessage(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None

class RoleSettings(BaseModel):
    nickname: Optional[str] = None
    systemPrompt: Optional[str] = None
    personality: Optional[str] = None
    languageStyle: Optional[str] = None
    examples: Optional[str] = None
    memory: Optional[str] = None
    knowledgeBase: Optional[str] = None

class ChatStreamRequest(BaseModel):
    chat_id: str
    role_id: Optional[str] = None
    message: str
    history_messages: Optional[List[HistoryMessage]] = []
    role_settings: Optional[RoleSettings] = None
    search_results: Optional[str] = None

def parse_reminder(text: str):
    """解析 [REMINDER:时间|内容] 指令"""
    pattern = r'\[REMINDER:([^\|]+)\|([^\]]+)\]'
    match = re.search(pattern, text)
    if match:
        return {"time": match.group(1), "content": match.group(2)}
    return None

def parse_expense(text: str):
    """解析 [EXPENSE:金额|分类|备注] 指令"""
    pattern = r'\[EXPENSE:([^\|]+)\|([^\|]+)\|([^\]]+)\]'
    match = re.search(pattern, text)
    if match:
        return {"amount": match.group(1), "category": match.group(2), "note": match.group(3)}
    return None

def parse_event(text: str):
    """解析 [EVENT:时间|标题|描述] 指令"""
    pattern = r'\[EVENT:([^\|]+)\|([^\|]+)\|([^\]]+)\]'
    match = re.search(pattern, text)
    if match:
        return {"time": match.group(1), "title": match.group(2), "description": match.group(3)}
    return None

def remove_all_commands(text: str) -> str:
    """移除所有指令，只保留自然语言"""
    text = re.sub(r'\[REMINDER:[^\]]+\]', '', text)
    text = re.sub(r'\[EXPENSE:[^\]]+\]', '', text)
    text = re.sub(r'\[EVENT:[^\]]+\]', '', text)
    text = re.sub(r'\[SEARCH:[^\]]+\]', '', text)
    return text.strip()

@router.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest):
    """
    混合语境注入模式的流式聊天接口
    """
    from main import supabase  # 导入主应用的supabase实例
    
    if not supabase:
        return {"error": "Supabase not configured"}
    
    # 1. 存储用户消息
    try:
        supabase.table("chat_messages").insert({
            "chat_id": req.chat_id,
            "role_id": req.role_id,
            "sender": "user",
            "content": req.message
        }).execute()
    except Exception as e:
        print(f"⚠️ 存储用户消息失败: {e}")
    
    # 2. 构建角色设定
    role_name = "AI"
    role_prompt = ""
    
    if req.role_settings:
        rs = req.role_settings
        role_name = rs.nickname or "AI"
        parts = []
        if rs.systemPrompt:
            parts.append(f"## 角色定位\n{rs.systemPrompt}")
        if rs.personality:
            parts.append(f"## 性格特点\n{rs.personality}")
        if rs.languageStyle:
            parts.append(f"## 语言风格\n{rs.languageStyle}")
        if rs.examples:
            parts.append(f"## 语言示例\n{rs.examples}")
        if rs.memory:
            parts.append(f"## 记忆事件\n{rs.memory}")
        if rs.knowledgeBase:
            parts.append(f"## 知识库\n{rs.knowledgeBase}")
        role_prompt = "\n\n".join(parts)
    
    # 3. 获取长期记忆
    core_memory = ""
    try:
        core_mem_result = supabase.table("memories").select("content").eq("category", "核心记忆").limit(1).execute()
        if core_mem_result.data:
            core_memory = core_mem_result.data[0].get("content", "")
    except Exception as e:
        print(f"⚠️ 加载核心记忆失败: {e}")
    
    # 4. 构建系统提示词
    now_beijing = datetime.utcnow() + timedelta(hours=8)
    weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
    current_time_str = now_beijing.strftime(f"%Y年%m月%d日 {weekdays[now_beijing.weekday()]} %H:%M")
    
    system_parts = []
    
    if role_prompt:
        system_parts.append(f"## 【层级1：角色定义】\n{role_prompt}")
    
    if core_memory:
        system_parts.append(f"## 【层级2：长期记忆总结】\n{core_memory}")
    
    system_parts.append(f"## 【层级3：实时对话场景】\n当前时间：{current_time_str}\n以下是你们最近的对话记录：")
    
    if req.search_results:
        system_parts.append(f"## 【相关搜索结果】\n{req.search_results}")
    
    system_prompt = "\n\n".join(system_parts)
    
    # 5. 构建消息历史
    messages = [{"role": "system", "content": system_prompt}]
    
    # 添加前端传来的历史记录（带时间戳）
    if req.history_messages:
        for msg in req.history_messages:
            content = msg.content
            if msg.timestamp:
                content = f"[{msg.timestamp}] {content}"
            messages.append({"role": msg.role, "content": content})
    
    # 添加当前用户消息
    messages.append({"role": "user", "content": req.message})
    
    # 6. 流式生成器
    async def generate():
        full_reply = ""
        
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{OPENAI_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json={
                        "model": OPENAI_MODEL,
                        "messages": messages,
                        "stream": True,
                        "max_tokens": 4096
                    },
                    timeout=120.0
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_reply += content
                                    yield f"data: {json.dumps({'content': content})}\n\n"
                            except json.JSONDecodeError:
                                continue
        
        except Exception as e:
            error_msg = f"生成失败: {str(e)}"
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
            full_reply = error_msg
        
        # 7. 生成完成后的处理
        if full_reply:
            # 解析并执行指令
            reminder = parse_reminder(full_reply)
            if reminder:
                try:
                    supabase.table("reminders").insert({
                        "content": reminder["content"],
                        "remind_at": reminder["time"],
                        "is_done": False
                    }).execute()
                    print(f"✅ 创建提醒: {reminder}")
                except Exception as e:
                    print(f"❌ 创建提醒失败: {e}")
            
            expense = parse_expense(full_reply)
            if expense:
                try:
                    supabase.table("expenses").insert({
                        "amount": float(expense["amount"]),
                        "category": expense["category"],
                        "note": expense["note"],
                        "expense_at": datetime.utcnow().isoformat()
                    }).execute()
                    print(f"✅ 记账: {expense}")
                except Exception as e:
                    print(f"❌ 记账失败: {e}")
            
            event = parse_event(full_reply)
            if event:
                try:
                    supabase.table("calendar_events").insert({
                        "title": event["title"],
                        "description": event["description"],
                        "event_at": event["time"]
                    }).execute()
                    print(f"✅ 创建日程: {event}")
                except Exception as e:
                    print(f"❌ 创建日程失败: {e}")
            
            # 移除指令，保留纯文本
            clean_reply = remove_all_commands(full_reply)
            
            # 存储AI回复
            try:
                supabase.table("chat_messages").insert({
                    "chat_id": req.chat_id,
                    "role_id": req.role_id,
                    "sender": "assistant",
                    "content": clean_reply,
                    "metadata": {"role_name": role_name}
                }).execute()
                print(f"✅ AI回复已存储")
            except Exception as e:
                print(f"❌ 存储AI回复失败: {e}")
            
            # Bark推送
            if BARK_KEY:
                try:
                    push_content = clean_reply[:100] + ("..." if len(clean_reply) > 100 else "")
                    async with httpx.AsyncClient() as c:
                        url = f"https://api.day.app/{BARK_KEY}/【{role_name}】/{quote(push_content)}?sound=shake&group={quote(role_name)}"
                        await c.get(url, timeout=10)
                        print(f"📤 Bark推送成功")
                except Exception as e:
                    print(f"⚠️ Bark推送失败: {e}")
        
        # 发送完成信号
        yield f"data: {json.dumps({'done': True})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")
