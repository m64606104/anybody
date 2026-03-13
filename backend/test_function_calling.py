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
        print("Round 1 msg:", json.dumps(msg1, ensure_ascii=False))
        
        if msg1.get("tool_calls"):
            # Append assistant message
            messages.append({
                "role": "assistant",
                "content": msg1.get("content"),
                "tool_calls": msg1["tool_calls"]
            })
            
            # Append tool results
            for tc in msg1["tool_calls"]:
                messages.append({
                    "tool_call_id": tc["id"],
                    "role": "tool",
                    "name": tc["function"]["name"],
                    "content": "昨天我们聊了关于AI架构设计的事情。"
                })
            
            print("Messages for Round 2:", json.dumps(messages, ensure_ascii=False))
            
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
