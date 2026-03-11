/**
 * 后端 API 服务
 * 用于对接 AI Assistant Backend
 */

// 后端地址（开发时用本地，生产用Render部署的URL）
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

// ============ 类型定义 ============
export interface Memory {
  id: number;
  content: string;
  type: string;  // chat, event, note 等
  metadata: Record<string, any>;
  is_important: boolean;
  created_at: string;
}

export interface Reminder {
  id: string;
  user_id: string;
  content: string;
  remind_at: string;
  repeat: 'daily' | 'weekly' | 'monthly' | null;
  is_done: boolean;
  created_at: string;
}

export interface ProactiveMessageRequest {
  user_id: string;
  role_persona: string;
  recent_memories?: string[];
  user_status?: {
    last_active?: string;
    location?: string;
  };
}

// ============ API 函数 ============

/**
 * 存储记忆
 */
export async function storeMemory(
  content: string,
  type: string = 'chat',
  metadata?: Record<string, any>,
  isImportant: boolean = false
): Promise<{ success: boolean; id?: number }> {
  const resp = await fetch(`${API_BASE_URL}/memory/store`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, type, metadata, is_important: isImportant }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 搜索记忆
 */
export async function searchMemory(
  query: string,
  limit = 10,
  type?: string
): Promise<{ memories: Memory[] }> {
  const resp = await fetch(`${API_BASE_URL}/memory/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit, type }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 创建闹钟
 */
export async function createReminder(
  userId: string,
  content: string,
  remindAt: Date,
  repeat?: 'daily' | 'weekly' | 'monthly'
): Promise<{ success: boolean; id?: string }> {
  const resp = await fetch(`${API_BASE_URL}/reminder/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      content,
      remind_at: remindAt.toISOString(),
      repeat,
    }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取闹钟列表
 */
export async function listReminders(
  userId: string,
  includeDone = false
): Promise<{ reminders: Reminder[] }> {
  const resp = await fetch(
    `${API_BASE_URL}/reminder/list/${userId}?include_done=${includeDone}`
  );
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 更新闹钟
 */
export async function updateReminder(
  reminderId: string,
  update: Partial<Pick<Reminder, 'content' | 'remind_at' | 'repeat' | 'is_done'>>
): Promise<{ success: boolean }> {
  const resp = await fetch(`${API_BASE_URL}/reminder/${reminderId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 删除闹钟
 */
export async function deleteReminder(reminderId: string): Promise<{ success: boolean }> {
  const resp = await fetch(`${API_BASE_URL}/reminder/${reminderId}`, {
    method: 'DELETE',
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 生成主动消息
 */
export async function generateProactiveMessage(
  req: ProactiveMessageRequest
): Promise<{ message: string }> {
  const resp = await fetch(`${API_BASE_URL}/proactive/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<{
  status: string;
  supabase: string;
  time: string;
}> {
  const resp = await fetch(`${API_BASE_URL}/health`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}
