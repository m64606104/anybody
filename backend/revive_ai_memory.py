"""
复活AI记忆脚本
功能：分批阅读所有历史聊天记录，让AI生成总体总结，存入memories表
"""

import os
import json
import httpx
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# 配置
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BATCH_SIZE = 100  # 每批读取100条


async def call_ai(system_prompt: str, user_prompt: str) -> str:
    """调用AI"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 4096
            },
            timeout=120.0
        )
        if resp.status_code != 200:
            raise Exception(f"AI API error: {resp.status_code} - {resp.text}")
        return resp.json()["choices"][0]["message"]["content"]


def get_all_chats():
    """获取所有聊天记录"""
    all_chats = []
    offset = 0
    
    while True:
        result = supabase.table("chat_messages").select(
            "id,sender,content,created_at"
        ).order("created_at", desc=False).range(offset, offset + BATCH_SIZE - 1).execute()
        
        if not result.data:
            break
        
        all_chats.extend(result.data)
        print(f"📥 已加载 {len(all_chats)} 条记录...")
        
        if len(result.data) < BATCH_SIZE:
            break
        
        offset += BATCH_SIZE
    
    return all_chats


def format_chats_for_ai(chats: list) -> str:
    """格式化聊天记录供AI阅读"""
    lines = []
    for c in chats:
        try:
            t = datetime.fromisoformat(c['created_at'].replace("Z", ""))
            t_beijing = t + timedelta(hours=8)
            time_str = t_beijing.strftime("%Y-%m-%d %H:%M")
        except:
            time_str = ""
        sender = "用户" if c['sender'] == 'user' else "AI"
        # 截断过长的内容
        content = c['content'][:300] + "..." if len(c['content']) > 300 else c['content']
        lines.append(f"[{time_str}] [{sender}] {content}")
    return "\n".join(lines)


async def generate_batch_summary(chats: list, batch_num: int, total_batches: int) -> str:
    """为一批聊天记录生成总结"""
    formatted = format_chats_for_ai(chats)
    
    system_prompt = """你是一个记忆分析专家。你的任务是阅读一段聊天记录，提取关键信息。

请关注以下方面：
1. 用户的性格特点、说话习惯、情绪表达方式
2. 用户对AI的期望和要求（比如希望AI怎么说话、怎么回应）
3. 重要的事件或话题（比如用户分享的生活经历、烦恼、开心的事）
4. AI之前的表现（哪些回复用户喜欢，哪些不喜欢）
5. 用户和AI之间建立的默契或约定

输出格式：
- 用简洁的要点列出关键信息
- 如果这批记录没有特别重要的内容，可以简短总结
- 不要编造，只基于实际内容"""

    user_prompt = f"""这是第 {batch_num}/{total_batches} 批聊天记录，请阅读并提取关键信息：

{formatted}

请总结这批记录中的关键信息："""

    print(f"🤖 正在分析第 {batch_num}/{total_batches} 批...")
    summary = await call_ai(system_prompt, user_prompt)
    return summary


async def generate_final_summary(batch_summaries: list) -> str:
    """将所有批次的总结合并为最终总结"""
    all_summaries = "\n\n---\n\n".join([
        f"【第{i+1}批总结】\n{s}" for i, s in enumerate(batch_summaries)
    ])
    
    system_prompt = """你是一个记忆整合专家。你的任务是将多批聊天记录的总结合并为一份完整的"AI记忆档案"。

这份档案将被存储，用于让AI在未来的对话中"记住"与用户的过往。

请生成一份结构化的记忆档案，包含以下部分：

## 1. 用户画像
- 性格特点
- 说话习惯和风格
- 情绪表达方式
- 生活背景（如果有提到）

## 2. 用户对AI的期望
- 希望AI怎么说话（语气、风格）
- 希望AI怎么回应（主动/被动、详细/简洁）
- 用户明确表达过的不满或要求

## 3. 重要记忆节点
- 按时间顺序列出重要事件或话题
- 包括用户分享的经历、烦恼、开心的事

## 4. AI的表现反馈
- 用户喜欢的AI回复方式
- 用户不喜欢的AI回复方式
- AI需要改进的地方

## 5. 默契与约定
- 用户和AI之间形成的默契
- 任何明确或隐含的约定

请基于以下各批次总结，生成这份完整的记忆档案："""

    user_prompt = f"""以下是所有批次的聊天记录总结：

{all_summaries}

请整合以上内容，生成一份完整的AI记忆档案："""

    print("🧠 正在整合所有记忆，生成最终档案...")
    final_summary = await call_ai(system_prompt, user_prompt)
    return final_summary


def save_memory(content: str, category: str = "核心记忆"):
    """将记忆存入memories表"""
    now = datetime.utcnow()
    
    # 先删除旧的核心记忆（如果有）
    supabase.table("memories").delete().eq("category", category).execute()
    
    # 插入新的核心记忆
    supabase.table("memories").insert({
        "content": content,
        "category": category,
        "title": "AI记忆档案 - 与用户的过往",
        "mood": "温暖",
        "created_at": now.isoformat(),
        "metadata": {
            "type": "core_memory",
            "generated_at": now.isoformat(),
            "source": "revive_ai_memory.py"
        }
    }).execute()
    
    print(f"💾 记忆已存入 memories 表 (category: {category})")


async def main():
    print("=" * 50)
    print("🔄 开始复活AI记忆...")
    print("=" * 50)
    
    # 1. 获取所有聊天记录
    print("\n📚 第一步：加载所有聊天记录...")
    all_chats = get_all_chats()
    print(f"✅ 共加载 {len(all_chats)} 条聊天记录")
    
    if not all_chats:
        print("❌ 没有找到聊天记录，退出")
        return
    
    # 2. 分批生成总结
    print("\n🔍 第二步：分批阅读并总结...")
    batches = [all_chats[i:i+BATCH_SIZE] for i in range(0, len(all_chats), BATCH_SIZE)]
    total_batches = len(batches)
    
    batch_summaries = []
    for i, batch in enumerate(batches):
        summary = await generate_batch_summary(batch, i+1, total_batches)
        batch_summaries.append(summary)
        print(f"✅ 第 {i+1}/{total_batches} 批总结完成")
        print(f"   预览: {summary[:100]}...")
    
    # 3. 整合为最终总结
    print("\n🧠 第三步：整合所有记忆...")
    final_summary = await generate_final_summary(batch_summaries)
    
    print("\n" + "=" * 50)
    print("📋 最终AI记忆档案：")
    print("=" * 50)
    print(final_summary)
    print("=" * 50)
    
    # 4. 存入memories表
    print("\n💾 第四步：存储记忆...")
    save_memory(final_summary, "核心记忆")
    
    print("\n" + "=" * 50)
    print("✅ AI记忆复活完成！")
    print("=" * 50)
    print("\n现在AI每次对话都会加载这份记忆档案。")
    print("你可以去聊天界面测试，看看AI是否'记得'你们的过往。")


if __name__ == "__main__":
    asyncio.run(main())
