# 用户数据集成指南

本指南说明如何将前端获取的用户信息传递给后端LLM处理器。

## 概述

前端通过 `urlDataUtils.ts` 从URL参数中获取用户信息，后端通过 `llm_handler_openai_compatible.py` 使用这些信息来个性化AI回复。

## 前端集成

### 1. 使用 userDataBridge.ts

```typescript
import { getUserDataForBackend, addUserDataToUrl } from '@/utils/userDataBridge'

// 获取用户数据
const userData = getUserDataForBackend()
if (userData?.userId) {
  console.log('用户ID:', userData.userId)
  console.log('用户姓名:', userData.userName)
}

// 将用户数据添加到URL中
const baseUrl = 'https://your-backend.com/webrtc/offer'
const urlWithUserData = addUserDataToUrl(baseUrl)
```

### 2. 在WebRTC连接中使用

```typescript
// 在建立WebRTC连接时传递用户数据
const webrtcUrl = addUserDataToUrl('/webrtc/offer')
// 或者
const webrtcUrl = addUserDataToUrl('https://your-backend.com/webrtc/offer')
```

## 后端集成

### 1. 在WebRTC服务中提取用户数据

```python
from src.utils.user_data_extractor import extract_user_data_from_request, set_user_data_to_session_context

@app.post("/webrtc/offer")
async def webrtc_offer(request: Request):
    # 提取用户数据
    user_data = extract_user_data_from_request(request)
    
    # 创建会话上下文
    session_context = create_session_context_with_user_data(session_id, user_data)
    
    # 继续WebRTC连接逻辑...
```

### 2. 在LLM处理器中使用

LLM处理器会自动从会话上下文中获取用户ID：

```python
# 在 create_context 中
user_id = getattr(session_context, 'user_id', None) or handler_config.user_id
context.user_id = user_id

# 在 update_system_prompt_for_conversation 中
if hasattr(context, 'user_id') and context.user_id:
    user_id = context.user_id
```

## 数据流程

1. **前端获取数据**: `urlDataUtils.ts` 从URL参数解析 `wj_oss_authority` 数据
2. **前端传递数据**: `userDataBridge.ts` 将用户数据添加到请求URL中
3. **后端提取数据**: `user_data_extractor.py` 从请求中提取用户信息
4. **后端使用数据**: `llm_handler_openai_compatible.py` 使用用户ID获取个性化信息

## 配置示例

### 前端配置

```typescript
// 在 VideoChat 组件中
import { getUserDataForBackend } from '@/utils/userDataBridge'

export default {
  mounted() {
    const userData = getUserDataForBackend()
    if (userData?.userId) {
      // 将用户ID添加到WebRTC连接URL
      this.webrtcUrl = addUserDataToUrl(this.webrtcUrl)
    }
  }
}
```

### 后端配置

```python
# 在 rtc_stream.py 中
from src.utils.user_data_extractor import extract_user_data_from_request

class RtcStream(AsyncAudioVideoStreamHandler):
    def __init__(self, session_id, request=None):
        super().__init__()
        self.session_id = session_id
        
        # 从请求中提取用户数据
        if request:
            user_data = extract_user_data_from_request(request)
            self.user_data = user_data
```

## 测试

### 前端测试

```typescript
// 测试用户数据获取
const userData = getUserDataForBackend()
console.assert(userData?.userId, '应该能获取到用户ID')

// 测试URL生成
const url = addUserDataToUrl('https://test.com/webrtc/offer')
console.assert(url.includes('user_id='), 'URL应该包含用户ID参数')
```

### 后端测试

```python
# 测试用户数据提取
from src.utils.user_data_extractor import extract_user_id_from_request

# 模拟请求
class MockRequest:
    def __init__(self, query_params):
        self.query_params = query_params

request = MockRequest({'user_id': 'test-user-123'})
user_id = extract_user_id_from_request(request)
assert user_id == 'test-user-123'
```

## 注意事项

1. **安全性**: 确保用户ID验证和授权
2. **错误处理**: 处理用户数据缺失的情况
3. **缓存**: 用户信息会被缓存，避免重复API调用
4. **日志**: 记录用户数据传递过程，便于调试

## 故障排除

### 常见问题

1. **用户ID为None**: 检查前端是否正确传递了用户数据
2. **API调用失败**: 检查用户ID格式是否正确
3. **缓存问题**: 清除缓存重新测试

### 调试步骤

1. 检查前端控制台日志
2. 检查后端日志中的用户数据提取
3. 验证API调用是否成功
4. 检查会话上下文中的用户ID

