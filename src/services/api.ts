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
 * 获取最近记忆（用于注入AI上下文）
 */
export async function getRecentMemories(limit = 10): Promise<{ memories: Memory[] }> {
  const resp = await fetch(`${API_BASE_URL}/memory/recent?limit=${limit}`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取最新GPS位置
 */
export async function getLatestGPS(): Promise<{
  found: boolean;
  data?: {
    latitude: number;
    longitude: number;
    address?: string;
    battery?: number;
    charging?: boolean;
    created_at: string;
  };
}> {
  const resp = await fetch(`${API_BASE_URL}/gps/latest`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 删除记忆（根据内容匹配删除）
 */
export async function deleteMemoryByContent(content: string): Promise<{ success: boolean; deleted_count?: number }> {
  const resp = await fetch(`${API_BASE_URL}/memory/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取用户状态（从GPS表获取）
 */
export async function getUserStatus(): Promise<{
  location?: { latitude: number; longitude: number; address?: string };
  battery?: number;
  charging?: boolean;
  last_active?: string;
}> {
  const gps = await getLatestGPS();
  if (!gps.found || !gps.data) return {};
  return {
    location: {
      latitude: gps.data.latitude,
      longitude: gps.data.longitude,
      address: gps.data.address,
    },
    battery: gps.data.battery,
    charging: gps.data.charging,
    last_active: gps.data.created_at,
  };
}

/**
 * 创建闹钟
 */
export async function createReminder(
  _userId: string,
  content: string,
  remindAt: Date,
  repeat?: 'daily' | 'weekly' | 'monthly'
): Promise<{ success: boolean; id?: string }> {
  const resp = await fetch(`${API_BASE_URL}/reminder/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
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
  _userId?: string,
  includeDone = false
): Promise<{ reminders: Reminder[] }> {
  const resp = await fetch(
    `${API_BASE_URL}/reminder/list?include_done=${includeDone}`
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
  role?: string;
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
 * 创建日历事件
 */
export async function createCalendarEvent(
  title: string,
  startTime: Date,
  endTime?: Date,
  description?: string,
  isAllDay = false
): Promise<{ success: boolean; id?: number }> {
  const resp = await fetch(`${API_BASE_URL}/calendar/event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title,
      start_time: startTime.toISOString(),
      end_time: endTime?.toISOString(),
      description,
      is_all_day: isAllDay,
    }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取日历事件
 */
export async function getCalendarEvents(
  startDate?: string,
  endDate?: string
): Promise<{
  events: {
    id: number;
    title: string;
    start_time: string;
    end_time?: string;
    description?: string;
    is_all_day: boolean;
  }[];
}> {
  const params = new URLSearchParams();
  if (startDate) params.append('start_date', startDate);
  if (endDate) params.append('end_date', endDate);
  const resp = await fetch(`${API_BASE_URL}/calendar/events?${params}`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取今日日程
 */
export async function getTodaySchedule(): Promise<{
  date: string;
  schedule: {
    id: string | number;
    title: string;
    start_time: string;
    end_time?: string;
    type: 'event' | 'reminder';
  }[];
}> {
  const resp = await fetch(`${API_BASE_URL}/calendar/today`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 解析AI回复中的EVENT指令
 * 格式: [EVENT:2026-03-12T14:00:00|开会|讨论项目进度]
 */
export function parseEventFromText(text: string): { time: Date; title: string; description?: string } | null {
  const match = text.match(/\[EVENT:([^\|]+)\|([^\|\]]+)(?:\|([^\]]+))?\]/);
  if (!match) return null;
  
  try {
    const time = new Date(match[1]);
    const title = match[2];
    const description = match[3];
    if (isNaN(time.getTime())) return null;
    return { time, title, description };
  } catch {
    return null;
  }
}

/**
 * 从文本中移除EVENT指令
 */
export function removeEventFromText(text: string): string {
  return text.replace(/\[EVENT:[^\]]+\]/g, '').trim();
}

/**
 * 解析AI回复中的QUERY指令
 * 格式: [QUERY:关键词]
 */
export function parseQueryFromText(text: string): string | null {
  const match = text.match(/\[QUERY:([^\]]+)\]/);
  return match ? match[1] : null;
}

/**
 * 从文本中移除QUERY指令
 */
export function removeQueryFromText(text: string): string {
  return text.replace(/\[QUERY:[^\]]+\]/g, '').trim();
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

// ============ 云端同步 ============

export interface SyncData {
  chats?: any[];
  messages?: Record<string, any[]>;
  roles?: any[];
  api_settings?: any;
  chat_settings?: any;
  user_profile?: any;
}

/**
 * 从云端加载所有数据
 */
export async function loadSyncData(): Promise<{
  found: boolean;
  data?: SyncData & { updated_at?: string };
}> {
  const resp = await fetch(`${API_BASE_URL}/sync/load`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 保存数据到云端
 */
export async function saveSyncData(data: SyncData): Promise<{
  success: boolean;
  updated_at?: string;
}> {
  const resp = await fetch(`${API_BASE_URL}/sync/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 同步单条消息（实时同步）
 */
export async function syncMessage(chatId: string, message: any): Promise<{
  success: boolean;
}> {
  const resp = await fetch(`${API_BASE_URL}/sync/message?chat_id=${chatId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(message),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 从chat_messages表加载所有消息（按chat_id分组）
 */
export async function loadAllChatMessages(): Promise<{
  messages: Record<string, any[]>;
}> {
  const resp = await fetch(`${API_BASE_URL}/chat/all-messages`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// ============ 后端聊天 ============

/**
 * 通过后端发送聊天消息
 * 后端会调用AI、存入数据库、推送Bark通知
 */
export async function sendChatMessage(
  chatId: string,
  message: string,
  roleId?: string,
  history?: { role: string; content: string }[]
): Promise<{
  success: boolean;
  reply: string;
  role_name: string;
  chat_id: string;
}> {
  const resp = await fetch(`${API_BASE_URL}/chat/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chat_id: chatId,
      role_id: roleId,
      message,
      history,
    }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取聊天消息历史
 */
export async function getChatMessages(chatId: string, limit = 50): Promise<{
  messages: {
    id: number;
    chat_id: string;
    sender: string;
    content: string;
    created_at: string;
    metadata?: any;
  }[];
}> {
  const resp = await fetch(`${API_BASE_URL}/chat/messages/${chatId}?limit=${limit}`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 获取最新AI回复（轮询用）
 */
export async function getLatestReply(chatId: string): Promise<{
  has_reply: boolean;
  message?: {
    id: number;
    content: string;
    created_at: string;
    metadata?: any;
  };
}> {
  const resp = await fetch(`${API_BASE_URL}/chat/latest?chat_id=${chatId}`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 删除聊天消息
 */
export async function deleteChatMessages(
  chatId: string,
  contents: string[]
): Promise<{ success: boolean; deleted: number }> {
  const resp = await fetch(`${API_BASE_URL}/chat/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, contents }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 导入聊天消息到Supabase（让AI能看到）
 */
export async function importChatMessages(
  chatId: string,
  roleId: string | undefined,
  messages: { role: string; content: string }[]
): Promise<{ success: boolean; imported: number }> {
  const resp = await fetch(`${API_BASE_URL}/chat/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, role_id: roleId, messages }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

/**
 * 推送Bark通知
 */
export async function sendBarkNotification(
  title: string,
  body: string,
  sound = 'shake',
  group?: string
): Promise<{ success: boolean; error?: string }> {
  const resp = await fetch(`${API_BASE_URL}/bark/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, body, sound, group }),
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}
