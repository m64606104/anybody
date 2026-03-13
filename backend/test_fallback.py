import asyncio
import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

async def test():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_chat_history",
                "description": "搜索聊天记录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keywords": {"type": "string"}
                    },
                    "required": ["keywords"]
                }
            }
        }
    ]
    
    messages = [
        {"role": "user", "content": "我们之前聊过什么？"}
    ]
    
    async with httpx.AsyncClient() as c:
        resp1 = await c.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": MODEL, "messages": messages, "tools": tools, "tool_choice": "auto"},
            timeout=30.0
        )
        data1 = resp1.json()
        msg1 = data1["choices"][0]["message"]
        
        if msg1.get("tool_calls"):
            # 【Fallback 策略】不使用严格的 tool_calls 格式，直接注入 system 提示词
            # 只保留原有的 messages
            messages.append({
                "role": "system",
                "content": f"系统查询结果:\n昨天我们聊了关于AI架构设计的事情。\n\n请结合以上结果，直接用自然语言回答用户的问题。"
            })
            
            # Round 2
            resp2 = await c.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={"model": MODEL, "messages": messages},
                timeout=30.0
            )
            data2 = resp2.json()
            print("Round 2 raw response:", json.dumps(data2, ensure_ascii=False))

asyncio.run(test())
