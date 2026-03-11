/**
 * 后端 API 服务
 * 用于对接 AI Assistant Backend
 */

// 后端地址（开发时用本地，生产用Render部署的URL）
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://anybody.onrender.com';

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
 * 获取待推送的主动消息
 */
export async function getPendingProactiveMessage(): Promise<{
  has_message: boolean;
  message?: string;
}> {
  const resp = await fetch(`${API_BASE_URL}/proactive/pending`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 解析AI回复中的REMINDER指令
 * 格式: [REMINDER:2026-03-12T08:00:00|开会]
 * 返回: { time: Date, content: string } 或 null
 */
export function parseReminderFromText(text: string): { time: Date; content: string } | null {
  const match = text.match(/\[REMINDER:([^\|]+)\|([^\]]+)\]/);
  if (!match) return null;
  
  try {
    const time = new Date(match[1]);
    const content = match[2];
    if (isNaN(time.getTime())) return null;
    return { time, content };
  } catch {
    return null;
  }
}

/**
 * 从文本中移除REMINDER指令（用于显示给用户的干净文本）
 */
export function removeReminderFromText(text: string): string {
  return text.replace(/\[REMINDER:[^\]]+\]/g, '').trim();
}

/**
 * 联网搜索
 */
export async function webSearch(
  query: string,
  numResults = 5
): Promise<{
  success: boolean;
  results?: { title: string; url: string; snippet: string }[];
  error?: string;
}> {
  const resp = await fetch(`${API_BASE_URL}/search/web`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, num_results: numResults }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 添加支出记录
 */
export async function addExpense(
  amount: number,
  category: string,
  description: string,
  date?: string
): Promise<{ success: boolean; id?: number }> {
  const resp = await fetch(`${API_BASE_URL}/expense/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ amount, category, description, date }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取支出统计
 */
export async function getExpenseSummary(
  days = 30
): Promise<{
  total: number;
  by_category: Record<string, number>;
  count: number;
  days: number;
}> {
  const resp = await fetch(`${API_BASE_URL}/expense/summary?days=${days}`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 解析AI回复中的EXPENSE指令
 * 格式: [EXPENSE:50|food|午餐]
 */
export function parseExpenseFromText(text: string): { amount: number; category: string; description: string } | null {
  const match = text.match(/\[EXPENSE:([^\|]+)\|([^\|]+)\|([^\]]+)\]/);
  if (!match) return null;
  
  try {
    const amount = parseFloat(match[1]);
    const category = match[2];
    const description = match[3];
    if (isNaN(amount)) return null;
    return { amount, category, description };
  } catch {
    return null;
  }
}

/**
 * 从文本中移除EXPENSE指令
 */
export function removeExpenseFromText(text: string): string {
  return text.replace(/\[EXPENSE:[^\]]+\]/g, '').trim();
}

/**
 * 解析AI回复中的SEARCH指令
 * 格式: [SEARCH:查询内容]
 */
export function parseSearchFromText(text: string): string | null {
  const match = text.match(/\[SEARCH:([^\]]+)\]/);
  return match ? match[1] : null;
}

/**
 * 从文本中移除SEARCH指令
 */
export function removeSearchFromText(text: string): string {
  return text.replace(/\[SEARCH:[^\]]+\]/g, '').trim();
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
