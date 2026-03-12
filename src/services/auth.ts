/**
 * Supabase Auth 认证服务
 * 用于用户注册、登录、登出
 */

import { createClient, User, Session, AuthChangeEvent } from '@supabase/supabase-js';

// Supabase 配置（从环境变量读取，或使用默认值）
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || 'https://lrovvuwqjjgsoyqcmqsh.supabase.co';
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imxyb3Z2dXdxampnc295cWNtcXNoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDE1MDc2NzIsImV4cCI6MjA1NzA4MzY3Mn0.bM7SdYFfCJGMSBMFfMOYBPbTT9cLTSjJGZbLOLWMzlk';

// 创建 Supabase 客户端
export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// 当前用户状态
let currentUser: User | null = null;
let currentSession: Session | null = null;

/**
 * 初始化认证状态（应用启动时调用）
 */
export async function initAuth(): Promise<User | null> {
  const { data: { session } } = await supabase.auth.getSession();
  if (session) {
    currentUser = session.user;
    currentSession = session;
  }
  return currentUser;
}

/**
 * 监听认证状态变化
 */
export function onAuthStateChange(callback: (user: User | null) => void) {
  return supabase.auth.onAuthStateChange((_event: AuthChangeEvent, session: Session | null) => {
    currentUser = session?.user || null;
    currentSession = session;
    callback(currentUser);
  });
}

/**
 * 获取当前用户
 */
export function getCurrentUser(): User | null {
  return currentUser;
}

/**
 * 获取当前用户ID（用于数据隔离）
 */
export function getCurrentUserId(): string {
  return currentUser?.id || 'anonymous';
}

/**
 * 邮箱注册
 */
export async function signUp(email: string, password: string): Promise<{ user: User | null; error: Error | null }> {
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
  });
  
  if (error) {
    return { user: null, error };
  }
  
  return { user: data.user, error: null };
}

/**
 * 邮箱登录
 */
export async function signIn(email: string, password: string): Promise<{ user: User | null; error: Error | null }> {
  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  });
  
  if (error) {
    return { user: null, error };
  }
  
  currentUser = data.user;
  currentSession = data.session;
  return { user: data.user, error: null };
}

/**
 * 登出
 */
export async function signOut(): Promise<{ error: Error | null }> {
  const { error } = await supabase.auth.signOut();
  if (!error) {
    currentUser = null;
    currentSession = null;
  }
  return { error };
}

/**
 * 获取用户配置（API Key、Bark URL等）
 */
export async function getUserConfig(): Promise<{
  openai_api_key?: string;
  openai_base_url?: string;
  openai_model?: string;
  bark_url?: string;
  amap_key?: string;
} | null> {
  if (!currentUser) return null;
  
  const { data, error } = await supabase
    .from('user_configs')
    .select('*')
    .eq('user_id', currentUser.id)
    .single();
  
  if (error || !data) return null;
  return data;
}

/**
 * 保存用户配置
 */
export async function saveUserConfig(config: {
  openai_api_key?: string;
  openai_base_url?: string;
  openai_model?: string;
  bark_url?: string;
  amap_key?: string;
}): Promise<{ success: boolean; error: Error | null }> {
  if (!currentUser) return { success: false, error: new Error('未登录') };
  
  const { error } = await supabase
    .from('user_configs')
    .upsert({
      user_id: currentUser.id,
      ...config,
      updated_at: new Date().toISOString(),
    });
  
  return { success: !error, error };
}

/**
 * 检查是否已登录
 */
export function isAuthenticated(): boolean {
  return currentUser !== null;
}
