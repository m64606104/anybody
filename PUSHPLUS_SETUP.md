# PushPlus 配置指南

## 什么是 PushPlus？

PushPlus 是一个免费的消息推送服务，支持推送到微信、企业微信、钉钉等平台。

**优势**：
- ✅ 免费额度：每天 200 条消息
- ✅ 支持微信通知（无需 iOS 设备）
- ✅ 跨平台（iOS + Android）
- ✅ 配置简单

## 配置步骤

### 1. 注册 PushPlus

访问：http://www.pushplus.plus/

1. 使用微信扫码登录
2. 关注 PushPlus 公众号
3. 获取你的 Token

### 2. 获取 Token

登录后，在首页可以看到你的 **Token**，类似：
```
a1b2c3d4e5f6g7h8i9j0
```

### 3. 配置环境变量

在 Render 后端添加环境变量：

```
PUSHPLUS_TOKEN=你的token
```

**步骤**：
1. 登录 Render Dashboard
2. 进入你的 `anybody` 服务
3. 点击 `Environment` 标签
4. 添加新的环境变量：
   - Key: `PUSHPLUS_TOKEN`
   - Value: 你的 Token
5. 保存并重新部署

### 4. 测试推送

部署完成后，访问测试接口：

```
POST https://anybody.onrender.com/notification/test
```

或使用 curl：
```bash
curl -X POST "https://anybody.onrender.com/notification/test?title=测试&content=这是一条测试消息"
```

如果配置正确，你会在微信收到 PushPlus 公众号的推送消息。

## 推送场景

配置完成后，以下场景都会自动推送到微信：

1. **AI 回复消息** - 每次 AI 回复你的消息
2. **主动消息** - AI 主动发送的消息
3. **充电触发消息** - 手机充电时的提醒
4. **闹钟提醒** - 设置的提醒到期

## 双通道推送

系统支持 **Bark + PushPlus** 双通道推送：

- **Bark**：iOS 设备，体验更好
- **PushPlus**：微信通知，跨平台

两个通道可以同时配置，也可以只配置其中一个。

## 检查配置状态

访问健康检查接口：
```
GET https://anybody.onrender.com/health
```

返回示例：
```json
{
  "status": "ok",
  "supabase": "connected",
  "bark": "configured",
  "pushplus": "configured",
  "version": "v20260315-unified-push"
}
```

## 常见问题

### Q: 收不到推送？
A: 检查：
1. Token 是否正确配置
2. 是否关注了 PushPlus 公众号
3. 公众号是否被屏蔽或静音

### Q: 推送延迟？
A: PushPlus 通常在 1-3 秒内送达，如果延迟较大，可能是微信服务器问题。

### Q: 免费额度够用吗？
A: 每天 200 条，对于个人使用完全足够。如果超出，可以升级付费版或使用 Bark。

## 其他推送渠道

如果需要其他推送渠道，可以考虑：

- **Server酱**：https://sct.ftqq.com/ （微信，每天 5 条免费）
- **Telegram Bot**：完全免费，但需要科学上网
- **企业微信机器人**：适合团队使用
- **钉钉机器人**：适合工作场景
