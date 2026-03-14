import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { 
  storeMemory, 
  createReminder, 
  parseReminderFromText, 
  removeReminderFromText, 
  getPendingProactiveMessage,
  addExpense,
  parseExpenseFromText,
  removeExpenseFromText,
  webSearch,
  parseSearchFromText,
  removeSearchFromText,
  createCalendarEvent,
  parseEventFromText,
  removeEventFromText,
  getRecentMemories,
  getMemoriesByTypes,
  getUserStatus,
  searchMemory,
  parseQueryFromText,
  deleteMemoryByContent,
  removeQueryFromText,
  parseSearchChatFromText,
  removeSearchChatFromText,
  loadSyncData,
  saveSyncData,
  syncMessage,
  sendChatMessage,
  deleteChatMessages,
  importChatMessages,
  loadAllChatMessages
} from './services/api';

type Screen = 'home' | 'chatList' | 'chat' | 'settings';

type Role = {
  id: string;
  name: string;
  remark?: string;
  avatar?: string; // dataURL
  persona?: string;
  traits?: string;
  tone?: string;
  examples?: string;
  memory?: string;
};

type Chat = {
  id: string;
  title: string;
  roleId?: string;
  lastMessage?: string;
};

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
};

type HomeApp = {
  id: string;
  title: string;
  subtitle: string;
  size: 'large' | 'small';
  action: 'social' | 'settings' | 'note' | 'lab';
};

type ApiSettings = {
  apiKey: string;
  baseUrl: string;
  model: string;
};

type ChatSettings = {
  bufferMs: number;
  chunkIntervalMs: number;
  chunkSeparator: string;
};

type UserProfile = {
  nickname: string;
  signature?: string;
  avatar?: string; // dataURL
};

const useLocalState = <T,>(key: string, initial: T): [T, React.Dispatch<React.SetStateAction<T>>] => {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === 'undefined') return initial;
    const raw = localStorage.getItem(key);
    if (raw) {
      try {
        return JSON.parse(raw) as T;
      } catch (e) {
        console.warn('Failed to parse localStorage', key, e);
      }
    }
    return initial;
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      console.warn('localStorage存储失败:', key, e);
    }
  }, [key, value]);

  return [value, setValue];
};

// 清理旧的消息localStorage（一次性）
if (typeof window !== 'undefined') {
  localStorage.removeItem('anyone.messages');
}

const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2));

const defaultRole: Role = {
  id: 'role-default',
  name: '默认角色',
  remark: '一个礼貌且简洁的 AI 伙伴',
};

const defaultChat: Chat = {
  id: 'chat-welcome',
  title: '欢迎对话',
  roleId: defaultRole.id,
  lastMessage: '点击进入开始聊天',
};

const defaultMessages: Record<string, Message[]> = {};

const defaultHomeApps: HomeApp[] = [
  { id: 'app-social', title: '对话', subtitle: '开始聊天', size: 'large', action: 'social' },
  { id: 'app-settings', title: '设置', subtitle: '配置', size: 'large', action: 'settings' },
  { id: 'app-note', title: '便签', subtitle: 'note', size: 'small', action: 'note' },
  { id: 'app-lab', title: '灵感', subtitle: 'idea', size: 'small', action: 'lab' },
];

const App: React.FC = () => {
  const [screen, setScreen] = useState<Screen>('home');
  const [selectedChatId, setSelectedChatId] = useState<string>(defaultChat.id);

  const [roles, setRoles] = useLocalState<Role[]>('anyone.roles', [defaultRole]);
  const [chats, setChats] = useLocalState<Chat[]>('anyone.chats', [defaultChat]);
  // 消息不存localStorage，只存云端，避免配额超限
  const [messagesMap, setMessagesMap] = useState<Record<string, Message[]>>(defaultMessages);
  const [apiSettings, setApiSettings] = useLocalState<ApiSettings>('anyone.api', {
    apiKey: '',
    baseUrl: 'https://api.openai.com',
    model: '',
  });
  const [chatSettings, setChatSettings] = useLocalState<ChatSettings>('anyone.chatsettings', {
    bufferMs: 15000,
    chunkIntervalMs: 1500,
    chunkSeparator: '<|chunk|>',
  });
  const [userProfile, setUserProfile] = useLocalState<UserProfile>('anyone.user', {
    nickname: '',
    signature: '',
  });

  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [modelTestStatus, setModelTestStatus] = useState<string>('');
  const [input, setInput] = useState('');
  const [showRolePanel, setShowRolePanel] = useState(false);
  const [isTyping, setIsTyping] = useState(false); // AI正在输入中
  const [homeApps, setHomeApps] = useLocalState<HomeApp[]>('anyone.homeapps', defaultHomeApps);
  const [searchQuery, setSearchQuery] = useState('');
  const [deleteMode, setDeleteMode] = useState(false);
  const [selectedMessages, setSelectedMessages] = useState<Set<string>>(new Set());
  const [visibleMessageCount, setVisibleMessageCount] = useState(50); // 分页：默认显示50条
  const [unreadMessages, setUnreadMessages] = useState<Record<string, Message[]>>({}); // 未读消息 {chatId: messages[]}
  const [cloudSyncStatus, setCloudSyncStatus] = useState<'loading' | 'synced' | 'error' | 'offline'>('loading');
  const [lastSyncTime, setLastSyncTime] = useState<string | null>(null);
  const syncTimeoutRef = useRef<number | null>(null);

  // ============ 云端同步逻辑 ============
  // 启动时从云端加载数据
  useEffect(() => {
    const loadFromCloud = async () => {
      try {
        // 1. 加载设置数据
        const result = await loadSyncData();
        if (result.found && result.data) {
          console.log('☁️ 从云端加载设置:', result.data.updated_at);
          if (result.data.chats?.length) setChats(result.data.chats);
          if (result.data.roles?.length) setRoles(result.data.roles);
          if (result.data.api_settings?.apiKey) setApiSettings(result.data.api_settings);
          if (result.data.chat_settings?.bufferMs) setChatSettings(result.data.chat_settings);
          if (result.data.user_profile?.nickname) setUserProfile(result.data.user_profile);
          setLastSyncTime(result.data.updated_at || null);
        }
        
        // 2. 从chat_messages表加载消息（这是消息的真正来源）
        try {
          const msgResult = await loadAllChatMessages();
          if (msgResult.messages && Object.keys(msgResult.messages).length > 0) {
            console.log('☁️ 从chat_messages表加载消息:', Object.keys(msgResult.messages).length, '个聊天');
            setMessagesMap(prev => ({...prev, ...msgResult.messages}));
          }
        } catch (msgErr) {
          console.warn('☁️ 加载消息失败:', msgErr);
        }
        
        setCloudSyncStatus('synced');
      } catch (e) {
        console.warn('☁️ 云端加载失败，使用本地数据:', e);
        setCloudSyncStatus('offline');
      }
    };
    loadFromCloud();
  }, []);

  // 数据变化时自动保存到云端（防抖）- 不保存messages，消息已在chat_messages表
  const saveToCloud = useCallback(() => {
    if (syncTimeoutRef.current) {
      window.clearTimeout(syncTimeoutRef.current);
    }
    syncTimeoutRef.current = window.setTimeout(async () => {
      try {
        await saveSyncData({
          chats,
          roles,
          api_settings: apiSettings,
          chat_settings: chatSettings,
          user_profile: userProfile,
        });
        setLastSyncTime(new Date().toISOString());
        setCloudSyncStatus('synced');
        console.log('☁️ 已同步设置到云端');
      } catch (e) {
        console.warn('☁️ 同步失败:', e);
        setCloudSyncStatus('error');
      }
    }, 2000); // 2秒防抖
  }, [chats, roles, apiSettings, chatSettings, userProfile]);

  // 监听数据变化，触发同步（不监听messagesMap，消息通过后端API存储）
  useEffect(() => {
    if (cloudSyncStatus !== 'loading') {
      saveToCloud();
    }
  }, [chats, roles, apiSettings, chatSettings, userProfile]);

  // 每个聊天独立的缓冲区和计时器
  const chatBuffers = useRef<Record<string, string[]>>({});
  const chatTimers = useRef<Record<string, number>>({});
  const dragId = useRef<string | null>(null);
  const floatingDragId = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  // 保持messagesMap的最新引用，避免闭包问题
  const messagesMapRef = useRef(messagesMap);
  messagesMapRef.current = messagesMap;

  const currentChat = useMemo(() => chats.find((c) => c.id === selectedChatId) ?? chats[0], [chats, selectedChatId]);
  const currentMessages = messagesMap[currentChat?.id ?? ''] ?? [];
  const currentRole = roles.find((r) => r.id === currentChat?.roleId) ?? roles[0];

  const scrollToBottom = () => {
    // 使用scrollIntoView但限制在容器内，避免整页被顶上去
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  };

  useEffect(() => {
    if (screen === 'chat') {
      // 延迟滚动，等待DOM渲染完成
      setTimeout(scrollToBottom, 100);
    }
  }, [screen, currentMessages]);

  // 轮询主动消息（每30秒检查一次）- 主动消息通过Bark推送，这里只是同步到聊天记录
  useEffect(() => {
    const pollProactiveMessages = async () => {
      try {
        const result = await getPendingProactiveMessage();
        if (result.has_message && result.message) {
          console.log('💬 收到主动消息:', result.message);
          // 使用第一个聊天作为目标
          const targetChat = chats[0];
          
          if (targetChat) {
            // 推送主动消息到聊天
            const message: Message = { 
              id: uuid(), 
              role: 'assistant', 
              content: result.message, 
              createdAt: Date.now() 
            };
            updateMessages(targetChat.id, (prev) => [...prev, message]);
            setChats((prev) => prev.map((c) => 
              c.id === targetChat.id ? { ...c, lastMessage: result.message } : c
            ));
            // 如果不在该聊天页，加入未读
            if (selectedChatId !== targetChat.id || screen !== 'chat') {
              setUnreadMessages((prev) => ({
                ...prev,
                [targetChat.id]: [...(prev[targetChat.id] || []), message],
              }));
            }
          }
        }
      } catch (e) {
        // 静默失败，不影响用户体验
        console.debug('轮询主动消息失败:', e);
      }
    };

    const intervalId = setInterval(pollProactiveMessages, 30000); // 30秒
    pollProactiveMessages(); // 立即执行一次
    
    return () => clearInterval(intervalId);
  }, [chats, selectedChatId, screen]);

  const handleNav = (target: Screen) => setScreen(target);

  const addChat = () => {
    const newRole: Role = { id: uuid(), name: '新角色' };
    const newChat: Chat = { id: uuid(), title: '新的对话', roleId: newRole.id };
    setRoles((prev) => [newRole, ...prev]);
    setChats((prev) => [newChat, ...prev]);
    setMessagesMap((prev) => ({ ...prev, [newChat.id]: [] }));
    setSelectedChatId(newChat.id);
    setScreen('chat');
    setShowRolePanel(true);
  };

  const updateMessages = (chatId: string, updater: (prev: Message[]) => Message[]) => {
    setMessagesMap((prev) => ({ ...prev, [chatId]: updater(prev[chatId] ?? []) }));
  };

  const pushAssistantChunk = (chatId: string, content: string) => {
    const message: Message = { id: uuid(), role: 'assistant', content, createdAt: Date.now() };
    updateMessages(chatId, (prev) => [...prev, message]);
    setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, lastMessage: content } : c)));
  };

  // 为指定聊天刷新缓冲区并调用AI
  const flushBufferForChat = (chatId: string) => {
    const items = chatBuffers.current[chatId] || [];
    chatBuffers.current[chatId] = [];
    if (chatTimers.current[chatId]) {
      window.clearTimeout(chatTimers.current[chatId]);
      delete chatTimers.current[chatId];
    }
    if (!items.length) return;
    
    // ⚠️ 使用ref获取最新的messagesMap，避免闭包捕获旧值
    const history = messagesMapRef.current[chatId] ?? [];
    
    // 🔍 调试日志：缓冲区内容和历史记录
    console.log('%c[DEBUG] flushBufferForChat', 'color: #ff6b6b; font-weight: bold');
    console.log('  chatId:', chatId);
    console.log('  缓冲区内容:', items);
    console.log('  历史记录数量:', history.length);
    console.log('  历史记录:', history.map(m => ({ role: m.role, content: m.content.slice(0, 50) + '...' })));
    
    setIsTyping(true);
    void callModelForChat(chatId, history);
  };

  // 独立的AI调用，直接调用AI API（旧版本方式，完整历史传给AI）
  const callModelForChat = async (chatId: string, history: Message[]) => {
    if (!apiSettings.apiKey || !apiSettings.model || !apiSettings.baseUrl) {
      pushAssistantChunkWithUnread(chatId, '请先在设置页配置 Base URL、API Key 和模型');
      setIsTyping(false);
      return;
    }

    const chat = chats.find((c) => c.id === chatId);
    const role = roles.find((r) => r.id === chat?.roleId);
    const rolePrompt = buildRolePrompt(role);

    // 🔄 自动查询最近记忆和用户状态，注入到AI上下文
    let memoryContext = '';
    let userStatusContext = '';
    let screenCaptureContext = '';
    
    try {
      // 按类型分别获取记忆（避免互相挤掉）
      const memoriesResult = await getMemoriesByTypes(20, 50, 10);
      
      // 截屏数据（微信、美团、小红书、咸鱼等）
      if (memoriesResult.screen_captures?.length) {
        const captureList = memoriesResult.screen_captures
          .map(m => {
            const app = m.metadata?.app || '未知应用';
            const time = m.created_at ? new Date(m.created_at).toLocaleString('zh-CN') : '';
            return `- [${app} ${time}] ${m.content.slice(0, 500)}`;
          })
          .join('\n');
        screenCaptureContext = `\n## 用户最近的应用截屏内容\n${captureList}`;
      }
      
      // GPS数据
      if (memoriesResult.gps?.length) {
        const gpsList = memoriesResult.gps
          .map(m => {
            const addr = m.metadata?.address || '未知位置';
            const time = m.created_at ? new Date(m.created_at).toLocaleString('zh-CN') : '';
            return `- [${time}] ${addr}`;
          })
          .join('\n');
        memoryContext += `\n## 用户最近的位置记录\n${gpsList}`;
      }
      
      // 获取用户状态（位置、电量等）
      const status = await getUserStatus();
      if (status.location || status.battery) {
        const parts = [];
        if (status.location?.address) parts.push(`位置: ${status.location.address}`);
        if (status.battery) parts.push(`电量: ${status.battery}%`);
        if (status.last_active) parts.push(`最后活跃: ${new Date(status.last_active).toLocaleString('zh-CN')}`);
        if (parts.length) {
          userStatusContext = `\n## 用户当前状态\n${parts.join(' | ')}`;
        }
      }
    } catch (e) {
      console.warn('获取记忆/状态失败:', e);
    }

    const systemPrompt = `你是用户的AI助手，拥有以下能力：

## 你的能力
1. **记忆能力**：你可以访问用户记忆，下面会提供最近的记忆和截屏数据
2. **闹钟提醒**：你可以帮用户设置闹钟，到时间会自动提醒
3. **日历事件**：你可以帮用户创建日程安排
4. **记账**：你可以帮用户记录支出
5. **联网搜索**：你可以搜索网络获取最新信息
6. **查询记忆**：你可以搜索用户的历史记忆（用[QUERY:关键词]查询）
7. **查询聊天记录**：你可以搜索之前的聊天历史（用[SEARCH_CHAT:关键词]查询，比如查找某人说过的话、某个事件等）
8. **应用截屏感知**：你可以看到用户在微信、美团、小红书、咸鱼等应用的截屏内容

## 特殊指令格式
这些指令会被系统自动执行，**指令本身会被隐藏，用户看不到**。你可以自由使用。
- 设置闹钟：[REMINDER:2026-03-12T08:00:00|提醒内容]
- 创建日程：[EVENT:2026-03-12T14:00:00|会议标题|会议描述]
- 记账：[EXPENSE:50|food|午餐]
- 搜索网络：[SEARCH:查询内容]
- 查询记忆：[QUERY:关键词]
- 查询聊天：[SEARCH_CHAT:查询内容]

**注意：指令会被自动移除，用户只会看到你的自然语言回复。所以不要在回复中提及"我正在搜索"之类的话，直接给出结果即可。**
${memoryContext}
${screenCaptureContext}
${userStatusContext}

## 角色设定
${rolePrompt || '（无特定角色设定）'}

## 回复格式
请输出JSON：{"segments": ["第一段回复", "第二段回复"]}
若无法输出JSON，用分隔符 ${chatSettings.chunkSeparator} 分段。`;

    // 像 momoyu 一样：默认发送全部消息给 API
    const apiMessages = [
      { role: 'system', content: systemPrompt },
      ...history.map((m) => ({ role: m.role, content: m.content })),
    ];
    
    console.log('%c[DEBUG] callModelForChat - 发送给AI的消息', 'color: #4ecdc4; font-weight: bold');
    console.log('  chatId:', chatId);
    console.log('  消息数量:', apiMessages.length);
    console.log('  历史条数:', history.length);

    try {
      const resp = await fetch(`${apiSettings.baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiSettings.apiKey}`,
        },
        body: JSON.stringify({
          model: apiSettings.model,
          messages: apiMessages,
          response_format: { type: 'json_object' },
        }),
      });

      if (!resp.ok) {
        const text = await resp.text();
        pushAssistantChunkWithUnread(chatId, `调用失败：${resp.status} ${text}`);
        setIsTyping(false);
        return;
      }

      const data = await resp.json();
      const rawContent: string = data?.choices?.[0]?.message?.content ?? '';
      
      console.log('%c[DEBUG] AI回复', 'color: #6c5ce7; font-weight: bold');
      console.log('  原始回复:', rawContent.slice(0, 200));
      
      let segments: string[] = [];
      try {
        const parsed = JSON.parse(rawContent);
        if (parsed?.segments && Array.isArray(parsed.segments)) {
          segments = parsed.segments.map((s: any) => String(s));
        }
      } catch (e) {
        // ignore JSON parse error, fallback below
      }

      if (!segments.length) {
        const text = rawContent || data?.choices?.[0]?.message?.content || '';
        const sep = chatSettings.chunkSeparator || '<|chunk|>';
        segments = text.split(sep).filter(Boolean);
        if (!segments.length && text) segments = [text];
      }

      let delay = 200;
      const totalDelay = delay + segments.length * chatSettings.chunkIntervalMs;
      segments.forEach((chunk) => {
        window.setTimeout(() => pushAssistantChunkWithUnread(chatId, chunk), delay);
        delay += chatSettings.chunkIntervalMs;
      });
      window.setTimeout(() => setIsTyping(false), totalDelay);
    } catch (e: any) {
      pushAssistantChunkWithUnread(chatId, `调用异常：${e?.message || e}`);
      setIsTyping(false);
    }
  };

  // 已创建的指令缓存，避免重复执行
  const createdRemindersRef = useRef<Set<string>>(new Set());
  const createdExpensesRef = useRef<Set<string>>(new Set());
  
  // 推送助手消息，如果用户不在该聊天页则加入未读
  const pushAssistantChunkWithUnread = async (chatId: string, content: string) => {
    // 解析REMINDER指令（避免重复创建）
    const reminder = parseReminderFromText(content);
    if (reminder) {
      const reminderKey = `${reminder.time}|${reminder.content}`;
      if (!createdRemindersRef.current.has(reminderKey)) {
        console.log('🔔 检测到REMINDER指令:', reminder);
        createdRemindersRef.current.add(reminderKey);
        createReminder('default_user', reminder.content, reminder.time)
          .then(() => console.log('✅ 闹钟创建成功'))
          .catch(e => console.warn('❌ 闹钟创建失败:', e));
      } else {
        console.log('⏭️ 跳过重复的REMINDER指令:', reminderKey);
      }
    }
    
    // 解析EXPENSE指令（避免重复记账）
    const expense = parseExpenseFromText(content);
    if (expense) {
      const expenseKey = `${expense.amount}|${expense.category}|${expense.description}`;
      if (!createdExpensesRef.current.has(expenseKey)) {
        console.log('💰 检测到EXPENSE指令:', expense);
        createdExpensesRef.current.add(expenseKey);
        addExpense(expense.amount, expense.category, expense.description)
          .then(() => console.log('✅ 记账成功'))
          .catch(e => console.warn('❌ 记账失败:', e));
      } else {
        console.log('⏭️ 跳过重复的EXPENSE指令:', expenseKey);
      }
    }
    
    // 解析SEARCH指令并执行搜索
    const searchQuery = parseSearchFromText(content);
    if (searchQuery) {
      console.log('🔍 检测到SEARCH指令:', searchQuery);
      try {
        const searchResult = await webSearch(searchQuery);
        if (searchResult.success && searchResult.results?.length) {
          const searchSummary = searchResult.results
            .map((r, i) => `${i + 1}. ${r.title}${r.snippet ? ': ' + r.snippet : ''}`)
            .join('\n');
          // 将搜索结果作为新消息推送
          const searchMessage: Message = { 
            id: uuid(), 
            role: 'assistant', 
            content: `🔍 搜索结果:\n${searchSummary}`, 
            createdAt: Date.now() 
          };
          updateMessages(chatId, (prev) => [...prev, searchMessage]);
        }
      } catch (e) {
        console.warn('❌ 搜索失败:', e);
      }
    }
    
    // 解析EVENT指令
    const event = parseEventFromText(content);
    if (event) {
      console.log('📅 检测到EVENT指令:', event);
      createCalendarEvent(event.title, event.time, undefined, event.description)
        .then(() => console.log('✅ 日历事件创建成功'))
        .catch(e => console.warn('❌ 日历事件创建失败:', e));
    }
    
    // 解析QUERY指令并搜索记忆
    const queryKeyword = parseQueryFromText(content);
    if (queryKeyword) {
      console.log('🧠 检测到QUERY指令:', queryKeyword);
      try {
        const queryResult = await searchMemory(queryKeyword, 5);
        if (queryResult.memories?.length) {
          const memorySummary = queryResult.memories
            .map((m, i) => `${i + 1}. [${m.type}] ${m.content.slice(0, 100)}`)
            .join('\n');
          const queryMessage: Message = { 
            id: uuid(), 
            role: 'assistant', 
            content: `🧠 记忆搜索结果:\n${memorySummary}`, 
            createdAt: Date.now() 
          };
          updateMessages(chatId, (prev) => [...prev, queryMessage]);
        } else {
          const noResultMessage: Message = { 
            id: uuid(), 
            role: 'assistant', 
            content: `🧠 没有找到与"${queryKeyword}"相关的记忆`, 
            createdAt: Date.now() 
          };
          updateMessages(chatId, (prev) => [...prev, noResultMessage]);
        }
      } catch (e) {
        console.warn('❌ 记忆搜索失败:', e);
      }
    }
    
    // 解析SEARCH_CHAT指令并搜索本地聊天记录
    const searchChatKeyword = parseSearchChatFromText(content);
    if (searchChatKeyword) {
      console.log('💬 检测到SEARCH_CHAT指令:', searchChatKeyword);
      try {
        // 从当前缓存的10000条数据中本地搜索
        const allMessages = messagesMapRef.current[chatId] || [];
        const matchedMessages = allMessages.filter(m => 
          m.content.toLowerCase().includes(searchChatKeyword.toLowerCase())
        );
        
        // 按时间排序，取最近的20条匹配结果
        const recentMatches = matchedMessages
          .sort((a, b) => b.createdAt - a.createdAt)
          .slice(0, 20)
          .reverse();

        if (recentMatches.length > 0) {
          const chatSummary = recentMatches
            .map((m, i) => {
              const date = new Date(m.createdAt);
              const timeStr = date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
              const roleName = m.role === 'user' ? '用户' : 'AI';
              return `[${timeStr}] ${roleName}: ${m.content.slice(0, 50)}${m.content.length > 50 ? '...' : ''}`;
            })
            .join('\n');
            
          const searchChatMessage: Message = { 
            id: uuid(), 
            role: 'assistant', 
            content: `💬 聊天记录搜索结果 (关于"${searchChatKeyword}"):\n${chatSummary}`, 
            createdAt: Date.now() 
          };
          updateMessages(chatId, (prev) => [...prev, searchChatMessage]);
        } else {
          const noResultMessage: Message = { 
            id: uuid(), 
            role: 'assistant', 
            content: `💬 在本地聊天记录中没有找到与"${searchChatKeyword}"相关的内容`, 
            createdAt: Date.now() 
          };
          updateMessages(chatId, (prev) => [...prev, noResultMessage]);
        }
      } catch (e) {
        console.warn('❌ 聊天记录搜索失败:', e);
      }
    }
    
    // 移除所有指令后的干净文本
    let cleanContent = removeReminderFromText(content);
    cleanContent = removeExpenseFromText(cleanContent);
    cleanContent = removeSearchFromText(cleanContent);
    cleanContent = removeEventFromText(cleanContent);
    cleanContent = removeQueryFromText(cleanContent);
    cleanContent = removeSearchChatFromText(cleanContent);
    if (!cleanContent) return; // 如果只有指令没有其他内容，不显示空消息
    
    const message: Message = { id: uuid(), role: 'assistant', content: cleanContent, createdAt: Date.now() };
    updateMessages(chatId, (prev) => [...prev, message]);
    setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, lastMessage: cleanContent } : c)));
    
    // 存储AI回复到记忆（异步，不阻塞）
    const chat = chats.find((c) => c.id === chatId);
    const role = roles.find((r) => r.id === chat?.roleId);
    
    // 同步 AI 回复到云端
    syncMessage(chatId, { role: 'assistant', content: cleanContent, createdAt: message.createdAt, role_id: chat?.roleId })
      .then(() => console.log('☁️ AI回复已同步到云端'))
      .catch(e => console.warn('❌ AI回复同步失败:', e));
    storeMemory(cleanContent, 'chat', { 
      chatId, 
      role: 'assistant',
      roleName: role?.name || '未知角色'
    }).catch(e => console.warn('存储记忆失败:', e));
    
    // 如果用户不在该聊天页，加入未读消息
    if (selectedChatId !== chatId || screen !== 'chat') {
      setUnreadMessages((prev) => ({
        ...prev,
        [chatId]: [...(prev[chatId] || []), message],
      }));
    }
  };

  const buildRolePrompt = (role?: Role) => {
    if (!role) return '';
    const parts = [
      role.remark && `备注: ${role.remark}`,
      role.persona && `人物设定: ${role.persona}`,
      role.traits && `性格特征: ${role.traits}`,
      role.tone && `语言风格: ${role.tone}`,
      role.examples && `语言示例: ${role.examples}`,
      role.memory && `记忆事件: ${role.memory}`,
    ].filter(Boolean);
    return parts.join('\n');
  };

  // 通用发送消息函数，支持任意chatId
  const sendMessageToChat = (chatId: string, userMsg: string) => {
    // 🔍 调试日志：用户发送消息
    console.log('%c[DEBUG] sendMessageToChat - 用户发送消息', 'color: #f9ca24; font-weight: bold');
    console.log('  chatId:', chatId);
    console.log('  用户消息:', userMsg);
    console.log('  当前缓冲区:', chatBuffers.current[chatId] || []);
    
    // 存储用户消息到记忆（异步，不阻塞）
    const chat = chats.find((c) => c.id === chatId);
    const role = roles.find((r) => r.id === chat?.roleId);
    storeMemory(userMsg, 'chat', { 
      chatId, 
      role: 'user',
      roleName: role?.name || '未知角色'
    }).catch(e => console.warn('存储记忆失败:', e));
    
    // 立即显示用户消息
    const newMessage: Message = { id: uuid(), role: 'user', content: userMsg, createdAt: Date.now() };
    updateMessages(chatId, (prev) => [...prev, newMessage]);
    setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, lastMessage: userMsg } : c)));
    
    // 同步用户消息到云端
    syncMessage(chatId, { role: 'user', content: userMsg, createdAt: newMessage.createdAt, role_id: chat?.roleId })
      .then(() => console.log('☁️ 用户消息已同步到云端'))
      .catch(e => console.warn('❌ 用户消息同步失败:', e));
    
    // 加入该聊天的缓冲区
    if (!chatBuffers.current[chatId]) {
      chatBuffers.current[chatId] = [];
    }
    chatBuffers.current[chatId].push(userMsg);
    
    console.log('  更新后缓冲区:', chatBuffers.current[chatId]);
    
    // 重置或启动该聊天的计时器（独立运行，不受页面切换影响）
    if (chatTimers.current[chatId]) {
      window.clearTimeout(chatTimers.current[chatId]);
    }
    chatTimers.current[chatId] = window.setTimeout(() => flushBufferForChat(chatId), chatSettings.bufferMs);
  };

  const handleSend = () => {
    if (!input.trim() || !currentChat) return;
    const userMsg = input.trim();
    setInput('');
    sendMessageToChat(currentChat.id, userMsg);
  };

  const handleTestModels = async () => {
    setModelTestStatus('测试中...');
    try {
      const resp = await fetch(`${apiSettings.baseUrl}/v1/models`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiSettings.apiKey}`,
        },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const names: string[] = (data.data || []).map((m: any) => m.id).slice(0, 30);
      setModelOptions(names);
      setModelTestStatus(names.length ? '测试成功，可选择模型' : '未获取到模型');
    } catch (e) {
      console.warn(e);
      setModelOptions([]);
      setModelTestStatus('接口失败');
    }
  };

  const handleAvatarUpload = (file: File, roleId: string) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result as string;
      setRoles((prev) => prev.map((r) => (r.id === roleId ? { ...r, avatar: dataUrl } : r)));
    };
    reader.readAsDataURL(file);
  };

  const deleteRole = (roleId: string) => {
    if (roles.length <= 1) {
      alert('至少需要保留一个角色');
      return;
    }
    if (confirm('确定删除该角色？相关聊天也将被删除。')) {
      setRoles((prev) => prev.filter((r) => r.id !== roleId));
      setChats((prev) => prev.filter((c) => c.roleId !== roleId));
      const deletedChatIds = chats.filter((c) => c.roleId === roleId).map((c) => c.id);
      setMessagesMap((prev) => {
        const next = { ...prev };
        deletedChatIds.forEach((id) => delete next[id]);
        return next;
      });
      setShowRolePanel(false);
      setScreen('chatList');
    }
  };

  const toggleMessageSelection = (msgId: string) => {
    setSelectedMessages((prev) => {
      const next = new Set(prev);
      if (next.has(msgId)) {
        next.delete(msgId);
      } else {
        next.add(msgId);
      }
      return next;
    });
  };

  const deleteSelectedMessages = async () => {
    if (!currentChat || selectedMessages.size === 0) return;
    if (confirm(`确定删除 ${selectedMessages.size} 条消息？`)) {
      // 获取要删除的消息内容
      const messagesToDelete = (messagesMap[currentChat.id] || []).filter((m) => selectedMessages.has(m.id));
      const contents = messagesToDelete.map(m => m.content);
      
      // 同步删除Supabase中的聊天记录
      try {
        const result = await deleteChatMessages(currentChat.id, contents);
        console.log('🗑️ 已从Supabase删除:', result.deleted, '条消息');
      } catch (e) {
        console.warn('⚠️ Supabase删除失败:', e);
      }
      
      // 删除本地消息
      setMessagesMap((prev) => ({
        ...prev,
        [currentChat.id]: (prev[currentChat.id] || []).filter((m) => !selectedMessages.has(m.id)),
      }));
      setSelectedMessages(new Set());
      setDeleteMode(false);
    }
  };

  const exportAllData = () => {
    const data = {
      roles,
      chats,
      messagesMap,
      apiSettings,
      chatSettings,
      userProfile,
      homeApps,
      exportTime: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `anyone-backup-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importAllData = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result as string);
        if (data.roles) setRoles(data.roles);
        if (data.chats) setChats(data.chats);
        if (data.messagesMap) setMessagesMap(data.messagesMap);
        if (data.apiSettings) setApiSettings(data.apiSettings);
        if (data.chatSettings) setChatSettings(data.chatSettings);
        if (data.userProfile) setUserProfile(data.userProfile);
        if (data.homeApps) setHomeApps(data.homeApps);
        alert('数据导入成功！');
      } catch (e) {
        alert('导入失败，文件格式错误');
      }
    };
    reader.readAsText(file);
  };

  const exportChatHistory = (chatId: string) => {
    const messages = messagesMap[chatId] || [];
    const chat = chats.find((c) => c.id === chatId);
    const data = {
      chatId,
      chatTitle: chat?.title,
      messages,
      exportTime: new Date().toISOString(),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat-${chat?.title || chatId}-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importChatHistory = (file: File, chatId: string) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const data = JSON.parse(reader.result as string);
        if (data.messages && Array.isArray(data.messages)) {
          // 更新本地消息
          setMessagesMap((prev) => ({ ...prev, [chatId]: data.messages }));
          
          // 同步到Supabase（让AI能看到）
          const chat = chats.find(c => c.id === chatId);
          try {
            const result = await importChatMessages(
              chatId,
              chat?.roleId,
              data.messages.map((m: Message) => ({ role: m.role, content: m.content }))
            );
            alert(`聊天记录导入成功！已同步 ${result.imported} 条到云端，AI可以看到这些记录了。`);
          } catch (e) {
            console.warn('⚠️ 同步到Supabase失败:', e);
            alert('聊天记录导入成功（本地），但同步到云端失败，AI可能看不到这些记录。');
          }
        } else {
          alert('导入失败，文件格式错误');
        }
      } catch (e) {
        alert('导入失败，文件格式错误');
      }
    };
    reader.readAsText(file);
  };

  const handleHomeClick = (action: HomeApp['action']) => {
    if (action === 'social') handleNav('chatList');
    if (action === 'settings') handleNav('settings');
  };

  // 同步本地聊天记录到云端（让AI能看到）
  const syncLocalChatToCloud = async (chatId: string) => {
    const messages = messagesMap[chatId] || [];
    if (messages.length === 0) {
      alert('当前聊天没有消息');
      return;
    }
    const chat = chats.find(c => c.id === chatId);
    try {
      const result = await importChatMessages(
        chatId,
        chat?.roleId,
        messages.map(m => ({ role: m.role, content: m.content }))
      );
      alert(`同步成功！已上传 ${result.imported} 条消息到云端，AI现在可以看到这些记录了。`);
    } catch (e) {
      console.error('同步失败:', e);
      alert('同步失败，请稍后重试');
    }
  };

  const reorderHomeApps = (fromId: string, toId: string) => {
    setHomeApps((prev) => {
      const current = [...prev];
      const fromIndex = current.findIndex((i) => i.id === fromId);
      const toIndex = current.findIndex((i) => i.id === toId);
      if (fromIndex < 0 || toIndex < 0) return prev;
      const [item] = current.splice(fromIndex, 1);
      current.splice(toIndex, 0, item);
      return current;
    });
  };

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 6) return '凌晨好';
    if (hour < 9) return '早上好';
    if (hour < 12) return '上午好';
    if (hour < 14) return '中午好';
    if (hour < 18) return '下午好';
    if (hour < 22) return '晚上好';
    return '夜深了';
  };

  // 进入聊天页时清除该聊天的未读消息
  useEffect(() => {
    if (screen === 'chat' && selectedChatId) {
      setUnreadMessages((prev) => {
        const next = { ...prev };
        delete next[selectedChatId];
        return next;
      });
    }
  }, [screen, selectedChatId]);

  // 计算总未读消息数
  const totalUnreadCount = useMemo(() => {
    return Object.values(unreadMessages).reduce((sum, msgs) => sum + msgs.length, 0);
  }, [unreadMessages]);

  const renderHome = () => {
    const largeApps = homeApps.filter(app => app.size === 'large');
    const smallApps = homeApps.filter(app => app.size === 'small');
    
    return (
      <div className="flex flex-col h-full pt-[120px] pb-6 relative">
        {/* 问候语区域 */}
        <div className="pl-[30px] pr-4 mb-8">
          <div className="text-3xl font-bold text-slate-800">
            {getGreeting()}{userProfile.nickname ? `, ${userProfile.nickname}` : ''}
          </div>
          {userProfile.signature && (
            <div className="text-sm text-slate-600 opacity-70 mt-2">{userProfile.signature}</div>
          )}
        </div>
        
        {/* 大按钮 - 垂直排列 */}
        <div className="flex flex-col gap-4 mb-auto pr-4 items-end mt-[70px]">
          {largeApps.map((app) => (
            <div key={app.id} className="relative w-[calc(50%-8px)]">
              <div
                draggable
                onDragStart={() => (dragId.current = app.id)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => {
                  if (dragId.current && dragId.current !== app.id) {
                    reorderHomeApps(dragId.current, app.id);
                  }
                  dragId.current = null;
                }}
                className="card glass p-5 cursor-pointer text-left"
                onClick={() => handleHomeClick(app.action)}
              >
                <div className="text-sm text-slate-600">{app.subtitle}</div>
                <div className="text-2xl font-semibold mt-2 text-slate-800">{app.title}</div>
              </div>
              
              {/* 未读消息数量提示 */}
              {app.action === 'social' && totalUnreadCount > 0 && (
                <div className="mt-2">
                  <div className="bg-white/60 backdrop-blur-sm rounded-xl px-3 py-2 border border-white/40">
                    <span className="text-xs text-slate-600">{totalUnreadCount} 条新消息</span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
        
        {/* 小按钮 - 左下角 */}
        <div className="flex gap-4 pl-4">
          {smallApps.map((app) => (
            <div
              key={app.id}
              draggable
              onDragStart={() => (dragId.current = app.id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                if (dragId.current && dragId.current !== app.id) {
                  reorderHomeApps(dragId.current, app.id);
                }
                dragId.current = null;
              }}
              className="card glass p-2 cursor-pointer flex flex-col items-center justify-center gap-1 text-center w-14"
              onClick={() => handleHomeClick(app.action)}
            >
              {app.subtitle === 'note' ? (
                <svg className="w-4 h-4 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              ) : (
                <svg className="w-4 h-4 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
              )}
              <div className="text-[10px] text-slate-700 font-medium">{app.title}</div>
            </div>
          ))}
        </div>
        
      </div>
    );
  };

  const filteredRoles = useMemo(() => {
    if (!searchQuery.trim()) return roles;
    const q = searchQuery.toLowerCase();
    return roles.filter((r) => r.name.toLowerCase().includes(q));
  }, [roles, searchQuery]);

  const renderChatList = () => {
    const handleChatClick = (chatId: string) => {
      setSelectedChatId(chatId);
      setVisibleMessageCount(50);
      // 清除该聊天的未读消息
      setUnreadMessages(prev => ({ ...prev, [chatId]: [] }));
      setScreen('chat');
    };

    // 获取每个聊天的最新消息和未读数
    const getChatInfo = (chat: Chat) => {
      const messages = messagesMap[chat.id] || [];
      const lastMsg = messages[messages.length - 1];
      const unreadCount = (unreadMessages[chat.id] || []).length;
      const role = roles.find(r => r.id === chat.roleId);
      return { lastMsg, unreadCount, role };
    };

    // 按最新消息时间排序聊天列表
    const sortedChats = [...chats].sort((a, b) => {
      const aMsg = (messagesMap[a.id] || []).slice(-1)[0];
      const bMsg = (messagesMap[b.id] || []).slice(-1)[0];
      return (bMsg?.createdAt || 0) - (aMsg?.createdAt || 0);
    });

    return (
      <div className="flex flex-col h-full">
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-xs text-slate-600">消息</div>
            <div className="text-lg font-semibold text-slate-800">聊天列表</div>
          </div>
          <button className="icon-btn glass" onClick={addChat} title="新对话">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </button>
        </div>

        {/* 搜索框 */}
        <div className="relative mb-3">
          <input
            className="w-full bg-white/60 border border-white/70 rounded-xl px-4 py-2 pl-10 focus:outline-none text-sm"
            placeholder="搜索..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <svg className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>

        {/* 聊天列表 */}
        <div className="flex-1 overflow-y-auto scrollbar space-y-2">
          {sortedChats.filter(chat => {
            const role = roles.find(r => r.id === chat.roleId);
            return !searchQuery || role?.name.toLowerCase().includes(searchQuery.toLowerCase());
          }).map((chat) => {
            const { lastMsg, unreadCount, role } = getChatInfo(chat);
            const lastMsgText = lastMsg?.content?.replace(/<[^>]*>/g, '').slice(0, 30) || '暂无消息';
            const lastMsgTime = lastMsg ? new Date(lastMsg.createdAt) : null;
            const timeStr = lastMsgTime 
              ? (Date.now() - lastMsgTime.getTime() < 86400000 
                  ? lastMsgTime.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
                  : lastMsgTime.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }))
              : '';

            return (
              <div
                key={chat.id}
                onClick={() => handleChatClick(chat.id)}
                className="card glass p-3 cursor-pointer hover:bg-white/70 transition-colors flex items-center gap-3"
              >
                {/* 头像 */}
                <div className="relative flex-shrink-0">
                  <div className="w-12 h-12 rounded-full bg-gradient-to-br from-cyan-400/30 to-blue-500/30 border-2 border-white/50 overflow-hidden flex items-center justify-center">
                    {role?.avatar ? (
                      <img src={role.avatar} className="w-full h-full object-cover" alt={role.name} />
                    ) : (
                      <svg className="w-6 h-6 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                      </svg>
                    )}
                  </div>
                  {/* 未读红点 */}
                  {unreadCount > 0 && (
                    <div className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 rounded-full flex items-center justify-center text-white text-xs font-bold">
                      {unreadCount > 9 ? '9+' : unreadCount}
                    </div>
                  )}
                </div>

                {/* 聊天信息 */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-slate-800 truncate">{role?.name || chat.title}</span>
                    <span className="text-xs text-slate-500 flex-shrink-0 ml-2">{timeStr}</span>
                  </div>
                  <div className="text-sm text-slate-500 truncate mt-0.5">
                    {lastMsg?.role === 'user' ? '我: ' : ''}{lastMsgText}
                  </div>
                </div>
              </div>
            );
          })}

          {/* 空状态 */}
          {sortedChats.length === 0 && (
            <div className="text-center text-slate-500 py-8">
              <p>暂无聊天</p>
              <button className="btn glass mt-3" onClick={addChat}>开始新对话</button>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderChat = () => (
    <div className="flex flex-col h-full max-h-full overflow-hidden">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-xs text-slate-600">私聊</div>
          <div className="text-lg font-semibold text-slate-800">{currentChat?.title}</div>
        </div>
        <div className="flex gap-2">
          {deleteMode ? (
            <>
              <button className="btn glass text-xs" onClick={() => { setDeleteMode(false); setSelectedMessages(new Set()); }}>取消</button>
              <button className="btn text-xs bg-red-500 text-white shadow-lg" onClick={deleteSelectedMessages} disabled={selectedMessages.size === 0}>删除({selectedMessages.size})</button>
            </>
          ) : (
            <>
              <button className="icon-btn glass" onClick={() => setDeleteMode(true)} title="选择删除">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
              <button className="icon-btn glass" onClick={() => setShowRolePanel(true)} title="角色设置">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </button>
            </>
          )}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar space-y-3 pb-4 min-h-0">
        {/* 分页：如果消息超过显示数量，显示"加载更多"按钮 */}
        {currentMessages.length > visibleMessageCount && (
          <button 
            className="w-full py-2 text-sm text-slate-500 hover:text-slate-700 hover:bg-white/30 rounded-lg transition-colors"
            onClick={() => setVisibleMessageCount(prev => prev + 50)}
          >
            ↑ 加载更早的消息（还有 {currentMessages.length - visibleMessageCount} 条）
          </button>
        )}
        {currentMessages.slice(-visibleMessageCount).map((m, idx, arr) => {
          // 时间显示逻辑：与上一条消息间隔超过5分钟则显示时间
          const showTime = idx === 0 || (m.createdAt - arr[idx - 1].createdAt > 5 * 60 * 1000);
          const msgDate = new Date(m.createdAt);
          const now = new Date();
          const isCurrentYear = msgDate.getFullYear() === now.getFullYear();
          const timeStr = isCurrentYear
            ? msgDate.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
            : msgDate.toLocaleString('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
          
          // 头像：助手用角色头像，用户用用户头像
          const avatar = m.role === 'assistant' ? currentRole?.avatar : userProfile.avatar;
          
          return (
            <div key={m.id}>
              {showTime && (
                <div className="text-center text-xs text-slate-400 my-2">{timeStr}</div>
              )}
              <div className={`flex ${m.role === 'assistant' ? 'justify-start' : 'justify-end'} items-end gap-2`}>
                {deleteMode && (
                  <input
                    type="checkbox"
                    checked={selectedMessages.has(m.id)}
                    onChange={() => toggleMessageSelection(m.id)}
                    className="w-4 h-4 cursor-pointer"
                  />
                )}
                {/* 助手头像（左侧） */}
                {m.role === 'assistant' && avatar && (
                  <img src={avatar} alt="" className="w-8 h-8 rounded-full object-cover flex-shrink-0" />
                )}
                <div 
                  className={`max-w-[70%] px-3 py-2 rounded-2xl text-sm ${m.role === 'assistant' ? 'bg-white/80 text-slate-800' : 'bg-slate-800 text-white'} ${deleteMode ? 'cursor-pointer' : ''}`}
                  onClick={() => deleteMode && toggleMessageSelection(m.id)}
                  dangerouslySetInnerHTML={{ __html: m.content }}
                />
                {/* 用户头像（右侧） */}
                {m.role === 'user' && avatar && (
                  <img src={avatar} alt="" className="w-8 h-8 rounded-full object-cover flex-shrink-0" />
                )}
              </div>
            </div>
          );
        })}
        <div ref={messagesEndRef} />
      </div>
      <div className="card glass p-2 flex items-center gap-2 chat-input-area flex-shrink-0">
        {/* 上传图片按钮 */}
        <label className="cursor-pointer p-1.5 rounded-full hover:bg-white/30 transition-colors flex-shrink-0">
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                const reader = new FileReader();
                reader.onload = (ev) => {
                  const dataUrl = ev.target?.result as string;
                  // 将图片作为消息发送
                  const imgHtml = `<img src="${dataUrl}" style="max-width: 200px; max-height: 200px; border-radius: 8px;" />`;
                  setInput((prev) => prev + imgHtml);
                };
                reader.readAsDataURL(file);
              }
              e.target.value = '';
            }}
          />
          <svg className="w-5 h-5 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        </label>
        <input
          className="flex-1 min-w-0 bg-white/60 border border-white/60 rounded-xl px-3 py-2 focus:outline-none text-sm"
          placeholder="输入消息..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <button className="btn bg-slate-800 text-white shadow-lg flex-shrink-0 text-sm px-3 py-2" onClick={handleSend}>发送</button>
      </div>

      {showRolePanel && currentRole && currentChat && (
        <RolePanel
          role={currentRole}
          chatSettings={chatSettings}
          currentChatId={currentChat.id}
          onClose={() => setShowRolePanel(false)}
          onSave={(next) => setRoles((prev) => prev.map((r) => (r.id === next.id ? next : r)))}
          onChatSettingsSave={setChatSettings}
          onAvatarUpload={(file) => handleAvatarUpload(file, currentRole.id)}
          onExportChat={exportChatHistory}
          onImportChat={importChatHistory}
          onDeleteRole={deleteRole}
          onSyncToCloud={syncLocalChatToCloud}
        />
      )}
    </div>
  );

  const renderSettings = () => (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-xs text-slate-600">主设置</div>
          <div className="text-lg font-semibold text-slate-800">API & 用户</div>
        </div>
      </div>
      <div className="space-y-4 overflow-y-auto scrollbar pb-6">
        <section className="card glass p-4 space-y-3">
          <div className="font-semibold text-slate-800">API 配置</div>
          <label className="text-sm text-slate-700 flex flex-col gap-1">
            Base URL
            <input
              className="bg-white/60 border border-white/70 rounded-xl px-3 py-2"
              value={apiSettings.baseUrl}
              onChange={(e) => setApiSettings({ ...apiSettings, baseUrl: e.target.value })}
              placeholder="https://api.openai.com"
            />
          </label>
          <label className="text-sm text-slate-700 flex flex-col gap-1">
            API Key
            <input
              className="bg-white/60 border border-white/70 rounded-xl px-3 py-2"
              value={apiSettings.apiKey}
              onChange={(e) => setApiSettings({ ...apiSettings, apiKey: e.target.value })}
              placeholder="sk-..."
            />
          </label>
          <div className="flex items-center gap-3">
            <button className="btn bg-slate-800 text-white shadow-lg" onClick={handleTestModels}>测试模型列表</button>
            <span className="text-sm text-slate-600">{modelTestStatus}</span>
          </div>
          {modelOptions.length > 0 && (
            <label className="text-sm text-slate-700 flex flex-col gap-1">
              选择模型
              <select
                className="bg-white/60 border border-white/70 rounded-xl px-3 py-2"
                value={apiSettings.model}
                onChange={(e) => setApiSettings({ ...apiSettings, model: e.target.value })}
              >
                <option value="">请选择</option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </label>
          )}
        </section>

        <section className="card glass p-4 space-y-3">
          <div className="font-semibold text-slate-800">用户信息</div>
          {/* 用户头像上传 */}
          <div className="flex items-center gap-4">
            <div className="relative">
              {userProfile.avatar ? (
                <img src={userProfile.avatar} alt="头像" className="w-16 h-16 rounded-full object-cover border-2 border-white/50" />
              ) : (
                <div className="w-16 h-16 rounded-full bg-slate-200 flex items-center justify-center text-slate-400">
                  <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
              )}
              <label className="absolute bottom-0 right-0 w-6 h-6 bg-slate-800 rounded-full flex items-center justify-center cursor-pointer hover:bg-slate-700">
                <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      const reader = new FileReader();
                      reader.onload = (ev) => {
                        setUserProfile({ ...userProfile, avatar: ev.target?.result as string });
                      };
                      reader.readAsDataURL(file);
                    }
                  }}
                />
              </label>
            </div>
            <div className="text-sm text-slate-600">
              <div>点击上传头像</div>
              {userProfile.avatar && (
                <button 
                  className="text-red-500 text-xs mt-1"
                  onClick={() => setUserProfile({ ...userProfile, avatar: undefined })}
                >
                  删除头像
                </button>
              )}
            </div>
          </div>
          <label className="text-sm text-slate-700 flex flex-col gap-1">
            昵称
            <input
              className="bg-white/60 border border-white/70 rounded-xl px-3 py-2"
              value={userProfile.nickname}
              onChange={(e) => setUserProfile({ ...userProfile, nickname: e.target.value })}
              placeholder="你的名字"
            />
          </label>
          <label className="text-sm text-slate-700 flex flex-col gap-1">
            个性签名
            <input
              className="bg-white/60 border border-white/70 rounded-xl px-3 py-2"
              value={userProfile.signature || ''}
              onChange={(e) => setUserProfile({ ...userProfile, signature: e.target.value })}
              placeholder="一句话介绍自己"
            />
          </label>
        </section>

        <section className="card glass p-4 space-y-3">
          <div className="font-semibold text-slate-800">应用迁移</div>
          <p className="text-xs text-slate-600">导出或导入全部应用数据（角色、聊天、设置等）</p>
          <div className="flex gap-3">
            <button className="btn glass flex-1" onClick={exportAllData}>
              <svg className="w-4 h-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              导出数据
            </button>
            <label className="btn glass flex-1 cursor-pointer text-center">
              <svg className="w-4 h-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
              </svg>
              导入数据
              <input type="file" accept=".json" className="hidden" onChange={(e) => e.target.files?.[0] && importAllData(e.target.files[0])} />
            </label>
          </div>
        </section>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="phone-shell">
        <div className="phone-inner flex flex-col gap-4">
          <header className="flex items-center justify-between">
            <div className="text-sm font-medium text-slate-700">anyone</div>
            <div className="flex gap-2">
              <button 
                className={`icon-btn glass ${screen === 'home' ? 'bg-white/80' : ''}`} 
                onClick={() => handleNav('home')}
                title="主屏"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
                </svg>
              </button>
              <button 
                className={`icon-btn glass ${screen === 'chatList' ? 'bg-white/80' : ''}`} 
                onClick={() => handleNav('chatList')}
                title="对话"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
              </button>
              <button 
                className={`icon-btn glass ${screen === 'settings' ? 'bg-white/80' : ''}`} 
                onClick={() => handleNav('settings')}
                title="设置"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </button>
            </div>
          </header>

          <main className="flex-1 overflow-hidden">
            {screen === 'home' && renderHome()}
            {screen === 'chatList' && renderChatList()}
            {screen === 'chat' && renderChat()}
            {screen === 'settings' && renderSettings()}
          </main>
        </div>
      </div>
    </div>
  );
};

type RolePanelProps = {
  role: Role;
  chatSettings: ChatSettings;
  currentChatId: string;
  onClose: () => void;
  onSave: (r: Role) => void;
  onChatSettingsSave: (c: ChatSettings) => void;
  onAvatarUpload: (file: File) => void;
  onExportChat: (chatId: string) => void;
  onImportChat: (file: File, chatId: string) => void;
  onDeleteRole: (roleId: string) => void;
  onSyncToCloud: (chatId: string) => void;
};

const RolePanel: React.FC<RolePanelProps> = ({ role, chatSettings, currentChatId, onClose, onSave, onChatSettingsSave, onAvatarUpload, onExportChat, onImportChat, onDeleteRole, onSyncToCloud }) => {
  const [draft, setDraft] = useState<Role>(role);
  const [chatDraft, setChatDraft] = useState<ChatSettings>(chatSettings);
  const [activeTab, setActiveTab] = useState<'role' | 'chat' | 'history'>('role');
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => setDraft(role), [role]);
  useEffect(() => setChatDraft(chatSettings), [chatSettings]);

  const handleFile = (files: FileList | null) => {
    if (!files?.length) return;
    onAvatarUpload(files[0]);
  };

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-sm flex items-center justify-center p-4 z-50">
      <div className="glass w-full max-w-md max-h-[85vh] rounded-3xl overflow-hidden shadow-2xl flex flex-col">
        {/* Header */}
        <div className="relative bg-gradient-to-br from-white/40 to-white/20 backdrop-blur-md border-b border-white/30 p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex gap-2">
              <button 
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${activeTab === 'role' ? 'bg-white/80 text-slate-800 shadow-sm' : 'text-slate-600 hover:bg-white/40'}`}
                onClick={() => setActiveTab('role')}
              >
                角色
              </button>
              <button 
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${activeTab === 'chat' ? 'bg-white/80 text-slate-800 shadow-sm' : 'text-slate-600 hover:bg-white/40'}`}
                onClick={() => setActiveTab('chat')}
              >
                策略
              </button>
              <button 
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${activeTab === 'history' ? 'bg-white/80 text-slate-800 shadow-sm' : 'text-slate-600 hover:bg-white/40'}`}
                onClick={() => setActiveTab('history')}
              >
                记录
              </button>
            </div>
            <button className="w-8 h-8 rounded-full bg-white/60 hover:bg-white/80 flex items-center justify-center transition-all" onClick={onClose}>
              <svg className="w-4 h-4 text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        
        {/* Content */}
        <div className="flex-1 overflow-y-auto scrollbar p-5 space-y-4">
        {activeTab === 'role' && (
          <>
        {/* Avatar */}
        <div className="flex flex-col items-center gap-3 pb-4 border-b border-white/20">
          <input 
            ref={fileInputRef}
            type="file" 
            accept="image/*" 
            onChange={(e) => handleFile(e.target.files)} 
            className="hidden"
          />
          <div 
            className="w-24 h-24 rounded-full bg-gradient-to-br from-cyan-400/30 to-blue-500/30 border-3 border-white/60 overflow-hidden flex items-center justify-center cursor-pointer hover:scale-105 transition-transform shadow-lg"
            onClick={() => fileInputRef.current?.click()}
          >
            {role.avatar ? (
              <img src={role.avatar} className="w-full h-full object-cover" alt="avatar" />
            ) : (
              <svg className="w-12 h-12 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
              </svg>
            )}
          </div>
          <p className="text-xs text-slate-600">点击头像上传图片</p>
        </div>

        {/* Form */}
        <div className="space-y-3 text-sm text-slate-700">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-slate-600">角色名称</span>
            <input
              className="bg-white/50 border border-white/60 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="输入角色名称"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-slate-600">人物设定</span>
            <input
              className="bg-white/50 border border-white/60 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
              value={draft.persona || ''}
              onChange={(e) => setDraft({ ...draft, persona: e.target.value })}
              placeholder="角色的基本设定"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-slate-600">性格特征</span>
            <input
              className="bg-white/50 border border-white/60 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
              value={draft.traits || ''}
              onChange={(e) => setDraft({ ...draft, traits: e.target.value })}
              placeholder="性格描述"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-slate-600">语言风格</span>
            <input
              className="bg-white/50 border border-white/60 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
              value={draft.tone || ''}
              onChange={(e) => setDraft({ ...draft, tone: e.target.value })}
              placeholder="说话方式"
            />
          </label>
        </div>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-slate-600">语言示例</span>
          <textarea
            rows={3}
            className="bg-white/50 border border-white/60 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all resize-none"
            value={draft.examples || ''}
            onChange={(e) => setDraft({ ...draft, examples: e.target.value })}
            placeholder="角色的典型对话示例"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-slate-600">记忆事件</span>
          <textarea
            rows={3}
            className="bg-white/50 border border-white/60 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all resize-none"
            value={draft.memory || ''}
            onChange={(e) => setDraft({ ...draft, memory: e.target.value })}
            placeholder="角色需要记住的重要信息"
          />
        </label>
        
          </>
        )}

        {/* Footer Buttons for Role Tab */}
        {activeTab === 'role' && (
          <div className="flex gap-2 pt-4 border-t border-white/20">
            <button 
              className="flex-1 py-2.5 rounded-xl bg-white/50 hover:bg-white/70 text-slate-700 font-medium transition-all text-sm"
              onClick={() => onDeleteRole(role.id)}
            >
              删除角色
            </button>
            <button
              className="flex-1 py-2.5 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-500 hover:from-cyan-600 hover:to-blue-600 text-white font-medium transition-all shadow-lg text-sm"
              onClick={() => {
                onSave(draft);
                onClose();
              }}
            >
              保存
            </button>
          </div>
        )}

        {activeTab === 'chat' && (
          <>
            <div className="space-y-3 text-sm text-slate-700">
              <div className="rounded-2xl border border-white/30 bg-white/35 p-3 space-y-3">
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-slate-600">缓冲时间 (毫秒)</span>
                  <input
                    type="number"
                    className="bg-white/60 border border-white/70 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
                    value={chatDraft.bufferMs}
                    onChange={(e) => setChatDraft({ ...chatDraft, bufferMs: Number(e.target.value) })}
                  />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-slate-600">拆条间隔 (毫秒)</span>
                  <input
                    type="number"
                    className="bg-white/60 border border-white/70 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
                    value={chatDraft.chunkIntervalMs}
                    onChange={(e) => setChatDraft({ ...chatDraft, chunkIntervalMs: Number(e.target.value) })}
                  />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-slate-600">拆条分隔符</span>
                  <input
                    className="bg-white/60 border border-white/70 rounded-xl px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-cyan-400/50 transition-all"
                    value={chatDraft.chunkSeparator}
                    onChange={(e) => setChatDraft({ ...chatDraft, chunkSeparator: e.target.value })}
                  />
                </label>
              </div>
              <p className="text-xs text-slate-600 px-1">缓冲时间：用户最后一条消息发出后等待多久合并传给 AI</p>
            </div>
            <div className="flex gap-2 pt-4 border-t border-white/20">
              <button className="flex-1 py-2.5 rounded-xl bg-white/50 hover:bg-white/70 text-slate-700 font-medium transition-all text-sm" onClick={onClose}>取消</button>
              <button className="flex-1 py-2.5 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-500 hover:from-cyan-600 hover:to-blue-600 text-white font-medium transition-all shadow-lg text-sm" onClick={() => { onChatSettingsSave(chatDraft); onClose(); }}>保存</button>
            </div>
          </>
        )}

        {activeTab === 'history' && (
          <>
            <div className="space-y-3">
              <div className="rounded-2xl border border-white/30 bg-white/35 p-3 space-y-3">
                <p className="text-xs text-slate-600">导出或导入当前对话的聊天记录（导入将覆盖现有记录）</p>
                <div className="flex gap-3">
                  <button className="btn glass flex-1" onClick={() => onExportChat(currentChatId)}>
                    <svg className="w-4 h-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    导出记录
                  </button>
                  <label className="btn glass flex-1 cursor-pointer text-center">
                    <svg className="w-4 h-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    导入记录
                    <input 
                      type="file" 
                      accept=".json" 
                      className="hidden" 
                      onChange={(e) => {
                        if (e.target.files?.[0]) {
                          onImportChat(e.target.files[0], currentChatId);
                          e.target.value = '';
                        }
                      }} 
                    />
                  </label>
                </div>
              </div>
              <div className="rounded-2xl border border-amber-200/50 bg-amber-50/50 p-3 space-y-2">
                <p className="text-xs text-amber-700">⚠️ 本地聊天记录AI看不到？点击下方按钮同步到云端</p>
                <button 
                  className="btn w-full bg-amber-500 text-white hover:bg-amber-600"
                  onClick={() => onSyncToCloud(currentChatId)}
                >
                  <svg className="w-4 h-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                  同步本地记录到云端
                </button>
              </div>
            </div>
            <div className="flex gap-2 pt-4 border-t border-white/20">
              <button className="flex-1 py-2.5 rounded-xl bg-white/50 hover:bg-white/70 text-slate-700 font-medium transition-all text-sm" onClick={onClose}>关闭</button>
            </div>
          </>
        )}
        </div>
      </div>
    </div>
  );
};

export default App;
