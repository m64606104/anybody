"""
初始化Supabase数据库表
运行: python init_db.py
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 需要service_role key来执行DDL，anon key权限不够
# 这里我们用REST API测试连接，实际建表需要在Supabase Dashboard执行

def test_connection():
    """测试Supabase连接"""
    print(f"Testing connection to: {SUPABASE_URL}")
    
    # 测试REST API
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }
    )
    
    if resp.status_code == 200:
        print("✅ Supabase REST API 连接成功!")
        return True
    else:
        print(f"❌ 连接失败: {resp.status_code} {resp.text}")
        return False

def check_tables():
    """检查表是否存在"""
    tables = ["memories", "reminders", "notifications"]
    
    for table in tables:
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/{table}?limit=1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}"
            }
        )
        
        if resp.status_code == 200:
            print(f"✅ 表 {table} 存在")
        elif resp.status_code == 404:
            print(f"❌ 表 {table} 不存在 - 需要在Supabase SQL Editor中创建")
        else:
            print(f"⚠️ 表 {table} 状态未知: {resp.status_code}")

if __name__ == "__main__":
    print("=" * 50)
    print("Supabase 数据库初始化检查")
    print("=" * 50)
    
    if test_connection():
        print("\n检查数据库表...")
        check_tables()
        print("\n" + "=" * 50)
        print("如果表不存在，请在Supabase Dashboard的SQL Editor中")
        print("执行 supabase_schema.sql 文件的内容")
        print("=" * 50)
