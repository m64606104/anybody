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
  getUserStatus,
  searchMemory,
  parseQueryFromText,
  deleteMemoryByContent,
  removeQueryFromText,
  loadSyncData,
  saveSyncData,
  syncMessage,
  sendChatMessage
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
    localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);

  return [value, setValue];
};

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
  const [messagesMap, setMessagesMap] = useLocalState<Record<string, Message[]>>('anyone.messages', defaultMessages);
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
        const result = await loadSyncData();
        if (result.found && result.data) {
          console.log('☁️ 从云端加载数据:', result.data.updated_at);
          // 只有云端数据比本地新才覆盖
          if (result.data.chats?.length) setChats(result.data.chats);
          if (result.data.messages && Object.keys(result.data.messages).length) setMessagesMap(result.data.messages);
          if (result.data.roles?.length) setRoles(result.data.roles);
          if (result.data.api_settings?.apiKey) setApiSettings(result.data.api_settings);
          if (result.data.chat_settings?.bufferMs) setChatSettings(result.data.chat_settings);
          if (result.data.user_profile?.nickname) setUserProfile(result.data.user_profile);
          setLastSyncTime(result.data.updated_at || null);
          setCloudSyncStatus('synced');
        } else {
          console.log('☁️ 云端无数据，使用本地数据');
          setCloudSyncStatus('synced');
        }
      } catch (e) {
        console.warn('☁️ 云端加载失败，使用本地数据:', e);
        setCloudSyncStatus('offline');
      }
    };
    loadFromCloud();
  }, []);

  // 数据变化时自动保存到云端（防抖）
  const saveToCloud = useCallback(() => {
    if (syncTimeoutRef.current) {
      window.clearTimeout(syncTimeoutRef.current);
    }
    syncTimeoutRef.current = window.setTimeout(async () => {
      try {
        await saveSyncData({
          chats,
          messages: messagesMap,
          roles,
          api_settings: apiSettings,
          chat_settings: chatSettings,
          user_profile: userProfile,
        });
        setLastSyncTime(new Date().toISOString());
        setCloudSyncStatus('synced');
        console.log('☁️ 已同步到云端');
      } catch (e) {
        console.warn('☁️ 同步失败:', e);
        setCloudSyncStatus('error');
      }
    }, 2000); // 2秒防抖
  }, [chats, messagesMap, roles, apiSettings, chatSettings, userProfile]);

  // 监听数据变化，触发同步
  useEffect(() => {
    if (cloudSyncStatus !== 'loading') {
      saveToCloud();
    }
  }, [chats, messagesMap, roles, apiSettings, chatSettings, userProfile]);

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
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (screen === 'chat') {
      scrollToBottom();
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
    
    void callModelForChat(chatId, history);
  };

  // 独立的AI调用，通过后端API（支持Bark推送）
  const callModelForChat = async (chatId: string, history: Message[]) => {
    const chat = chats.find((c) => c.id === chatId);
    const role = roles.find((r) => r.id === chat?.roleId);
    
    // 获取最后一条用户消息
    const lastUserMsg = history.filter(m => m.role === 'user').pop();
    if (!lastUserMsg) {
      pushAssistantChunkWithUnread(chatId, '没有找到用户消息');
      return;
    }

    console.log('%c[DEBUG] 调用后端聊天API', 'color: #4ecdc4; font-weight: bold');
    console.log('  chatId:', chatId);
    console.log('  roleId:', role?.id);
    console.log('  message:', lastUserMsg.content);

    try {
      // 调用后端API，后端会：调用AI、存入数据库、推送Bark
      const result = await sendChatMessage(
        chatId,
        lastUserMsg.content,
        role?.id,
        history.slice(-20).map(m => ({ role: m.role, content: m.content }))
      );

      console.log('%c[DEBUG] 后端返回', 'color: #6c5ce7; font-weight: bold');
      console.log('  reply:', result.reply);
      console.log('  role_name:', result.role_name);

      if (result.success && result.reply) {
        // 直接显示AI回复（后端已经推送了Bark）
        pushAssistantChunkWithUnread(chatId, result.reply);
      } else {
        pushAssistantChunkWithUnread(chatId, '后端返回异常');
      }
    } catch (e: any) {
      console.error('后端聊天失败:', e);
      pushAssistantChunkWithUnread(chatId, `调用失败：${e?.message || e}`);
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
    
    // 移除所有指令后的干净文本
    let cleanContent = removeReminderFromText(content);
    cleanContent = removeExpenseFromText(cleanContent);
    cleanContent = removeSearchFromText(cleanContent);
    cleanContent = removeEventFromText(cleanContent);
    cleanContent = removeQueryFromText(cleanContent);
    if (!cleanContent) return; // 如果只有指令没有其他内容，不显示空消息
    
    const message: Message = { id: uuid(), role: 'assistant', content: cleanContent, createdAt: Date.now() };
    updateMessages(chatId, (prev) => [...prev, message]);
    setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, lastMessage: cleanContent } : c)));
    
    // 存储AI回复到记忆（异步，不阻塞）
    const chat = chats.find((c) => c.id === chatId);
    const role = roles.find((r) => r.id === chat?.roleId);
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
      
      // 同步删除Supabase中的记录
      for (const msg of messagesToDelete) {
        try {
          await deleteMemoryByContent(msg.content);
          console.log('🗑️ 已从Supabase删除:', msg.content.slice(0, 50));
        } catch (e) {
          console.warn('⚠️ Supabase删除失败:', e);
        }
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
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result as string);
        if (data.messages && Array.isArray(data.messages)) {
          setMessagesMap((prev) => ({ ...prev, [chatId]: data.messages }));
          alert('聊天记录导入成功（已覆盖）！');
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
    const handleRoleClick = (roleId: string) => {
      const existingChat = chats.find((c) => c.roleId === roleId);
      if (existingChat) {
        setSelectedChatId(existingChat.id);
        setVisibleMessageCount(50); // 切换聊天时重置分页
        setScreen('chat');
      } else {
        const newChat: Chat = { id: uuid(), title: roles.find((r) => r.id === roleId)?.name || '新对话', roleId };
        setChats((prev) => [newChat, ...prev]);
        setMessagesMap((prev) => ({ ...prev, [newChat.id]: [] }));
        setSelectedChatId(newChat.id);
        setVisibleMessageCount(50); // 切换聊天时重置分页
        setScreen('chat');
      }
    };

    return (
      <div className="flex flex-col h-full relative overflow-hidden">
        <div className="absolute top-0 left-0 right-0 z-10 p-4 glass">
          <div className="flex items-center gap-3">
            <div className="flex-1 relative">
              <input
                className="w-full bg-white/60 border border-white/70 rounded-xl px-4 py-2 pl-10 focus:outline-none text-sm"
                placeholder="搜索角色..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <svg className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <button className="icon-btn glass" onClick={addChat} title="新对话">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            </button>
          </div>
        </div>

        <div className="flex-1 pt-20 pb-4 px-4 relative">
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="text-xs text-slate-400">拖动角色卡片重新排列</div>
          </div>
          <div className="grid grid-cols-2 gap-4 h-full content-start">
            {filteredRoles.map((role, idx) => (
              <div
                key={role.id}
                draggable
                onDragStart={() => (floatingDragId.current = role.id)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => {
                  if (floatingDragId.current && floatingDragId.current !== role.id) {
                    setRoles((prev) => {
                      const current = [...prev];
                      const fromIndex = current.findIndex((r) => r.id === floatingDragId.current);
                      const toIndex = current.findIndex((r) => r.id === role.id);
                      if (fromIndex < 0 || toIndex < 0) return prev;
                      const [item] = current.splice(fromIndex, 1);
                      current.splice(toIndex, 0, item);
                      return current;
                    });
                  }
                  floatingDragId.current = null;
                }}
                onClick={() => handleRoleClick(role.id)}
                className="card glass p-4 cursor-pointer hover:scale-105 transition-transform duration-200 flex flex-col items-center gap-3"
                style={{
                  animation: `float ${3 + idx * 0.3}s ease-in-out infinite`,
                  animationDelay: `${idx * 0.2}s`,
                }}
              >
                <div className="w-16 h-16 rounded-full bg-gradient-to-br from-cyan-400/30 to-blue-500/30 border-2 border-white/50 overflow-hidden flex items-center justify-center">
                  {role.avatar ? (
                    <img src={role.avatar} className="w-full h-full object-cover" alt={role.name} />
                  ) : (
                    <svg className="w-8 h-8 text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                  )}
                </div>
                <div className="text-sm font-semibold text-slate-800 text-center">{role.name}</div>
              </div>
            ))}
          </div>
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
      <div className="card glass p-3 flex items-center gap-2">
        {/* 上传图片按钮 */}
        <label className="cursor-pointer p-2 rounded-full hover:bg-white/30 transition-colors">
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
          className="flex-1 bg-white/60 border border-white/60 rounded-xl px-3 py-2 focus:outline-none"
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
        <button className="btn bg-slate-800 text-white shadow-lg" onClick={handleSend}>发送</button>
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
};

const RolePanel: React.FC<RolePanelProps> = ({ role, chatSettings, currentChatId, onClose, onSave, onChatSettingsSave, onAvatarUpload, onExportChat, onImportChat, onDeleteRole }) => {
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
