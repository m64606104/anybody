-- ============================================
-- Supabase 数据库表结构
-- 在 Supabase SQL Editor 中执行
-- ============================================

-- 启用 pgvector 扩展（用于向量搜索）
create extension if not exists vector;

-- ============================================
-- memories 表 - 存储用户记忆
-- ============================================
create table if not exists memories (
    id uuid default gen_random_uuid() primary key,
    user_id text not null,
    content text not null,
    embedding vector(1536),  -- text-embedding-3-small 的维度
    tags text[] default '{}',
    metadata jsonb default '{}',
    created_at timestamptz default now()
);

-- 创建向量索引（加速相似度搜索）
create index if not exists memories_embedding_idx 
on memories using ivfflat (embedding vector_cosine_ops)
with (lists = 100);

-- 创建用户索引
create index if not exists memories_user_id_idx on memories(user_id);

-- ============================================
-- 语义搜索函数
-- ============================================
create or replace function search_memories(
    query_embedding vector(1536),
    match_count int default 10,
    filter_user_id text default null,
    filter_tags text[] default null
)
returns table (
    id uuid,
    user_id text,
    content text,
    tags text[],
    metadata jsonb,
    created_at timestamptz,
    similarity float
)
language plpgsql
as $$
begin
    return query
    select
        m.id,
        m.user_id,
        m.content,
        m.tags,
        m.metadata,
        m.created_at,
        1 - (m.embedding <=> query_embedding) as similarity
    from memories m
    where
        (filter_user_id is null or m.user_id = filter_user_id)
        and (filter_tags is null or m.tags && filter_tags)
    order by m.embedding <=> query_embedding
    limit match_count;
end;
$$;

-- ============================================
-- reminders 表 - 闹钟/提醒
-- ============================================
create table if not exists reminders (
    id uuid default gen_random_uuid() primary key,
    user_id text not null,
    content text not null,
    remind_at timestamptz not null,
    repeat text,  -- 'daily', 'weekly', 'monthly', null
    is_done boolean default false,
    created_at timestamptz default now()
);

-- 创建索引
create index if not exists reminders_user_id_idx on reminders(user_id);
create index if not exists reminders_remind_at_idx on reminders(remind_at);
create index if not exists reminders_is_done_idx on reminders(is_done);

-- ============================================
-- notifications 表 - 手机通知（由外部脚本写入）
-- ============================================
create table if not exists notifications (
    id uuid default gen_random_uuid() primary key,
    user_id text not null,
    app_name text,
    title text,
    content text,
    tags text[] default '{App_Pending}',  -- App_Pending, App_Done
    received_at timestamptz default now(),
    processed_at timestamptz
);

-- 创建索引
create index if not exists notifications_user_id_idx on notifications(user_id);
create index if not exists notifications_tags_idx on notifications using gin(tags);

-- ============================================
-- RLS (Row Level Security) - 可选
-- ============================================
-- 如果需要更严格的安全性，可以启用 RLS
-- alter table memories enable row level security;
-- alter table reminders enable row level security;
-- alter table notifications enable row level security;
