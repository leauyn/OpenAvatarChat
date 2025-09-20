# 用户数据集成完整示例

## 概述

本示例展示如何将前端获取的用户信息传递给后端LLM处理器，实现个性化的AI对话。

## 前端实现

### 1. 修改 VideoChat 组件

```vue
<template>
  <div class="page-container" ref="wrapRef">
    <!-- 现有内容 -->
    <div class="content-container">
      <!-- 视频容器等 -->
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getUserDataForBackend, addUserDataToUrl } from '@/utils/userDataBridge'

// 现有代码...

// 用户数据
const userData = ref(null)

onMounted(() => {
  // 获取用户数据
  userData.value = getUserDataForBackend()
  
  if (userData.value?.userId) {
    console.log('✅ 获取到用户数据:', userData.value)
    
    // 将用户数据添加到WebRTC连接URL
    const baseUrl = '/webrtc/offer'
    const urlWithUserData = addUserDataToUrl(baseUrl)
    console.log('🔗 WebRTC URL with user data:', urlWithUserData)
    
    // 使用包含用户数据的URL建立连接
    // 这里需要根据实际的WebRTC连接代码来修改
  } else {
    console.warn('⚠️ 未获取到用户数据，使用默认配置')
  }
})
</script>
```

### 2. 在 WebRTC 连接中使用

```typescript
// 在 WebRTC 连接建立时
import { addUserDataToUrl } from '@/utils/userDataBridge'

export function createWebRTCConnection() {
  const baseUrl = 'https://your-backend.com/webrtc/offer'
  const urlWithUserData = addUserDataToUrl(baseUrl)
  
  // 建立WebRTC连接
  const peerConnection = new RTCPeerConnection()
  // ... 其他WebRTC逻辑
}
```

## 后端实现

### 1. 修改 WebRTC 服务

```python
# 在 rtc_stream.py 中
from src.utils.user_data_extractor import extract_user_data_from_request, set_user_data_to_session_context

class RtcStream(AsyncAudioVideoStreamHandler):
    def __init__(self, session_id, request=None):
        super().__init__()
        self.session_id = session_id
        
        # 从请求中提取用户数据
        if request:
            user_data = extract_user_data_from_request(request)
            self.user_data = user_data
            print(f"🔗 提取到用户数据: {user_data}")
        else:
            self.user_data = {}

    async def on_offer(self, offer, request):
        # 创建会话上下文
        session_context = self.create_session_context_with_user_data()
        
        # 继续WebRTC连接逻辑...
        
    def create_session_context_with_user_data(self):
        # 创建会话上下文并设置用户数据
        session_context = SessionContext(self.session_id)
        
        if self.user_data:
            set_user_data_to_session_context(session_context, self.user_data)
            
        return session_context
```

### 2. 修改 WebRTC 端点

```python
# 在 WebRTC 端点中
from fastapi import Request
from src.utils.user_data_extractor import extract_user_data_from_request

@app.post("/webrtc/offer")
async def webrtc_offer(request: Request):
    # 提取用户数据
    user_data = extract_user_data_from_request(request)
    print(f"🔗 从请求中提取用户数据: {user_data}")
    
    # 创建会话ID
    session_id = str(uuid.uuid4())
    
    # 创建RtcStream实例，传递用户数据
    rtc_stream = RtcStream(session_id, request)
    
    # 继续WebRTC连接逻辑...
```

## 测试示例

### 1. 前端测试

```typescript
// 测试用户数据获取
import { getUserDataForBackend, addUserDataToUrl } from '@/utils/userDataBridge'

// 模拟URL参数
const mockUrl = 'https://test.com?wj_oss_authority=["1","test-user-123","6","3","","school-456","","张三","测试学校","","北京"]'
Object.defineProperty(window, 'location', {
  value: new URL(mockUrl),
  writable: true
})

// 测试获取用户数据
const userData = getUserDataForBackend()
console.assert(userData?.userId === 'test-user-123', '应该能获取到用户ID')
console.assert(userData?.userName === '张三', '应该能获取到用户姓名')

// 测试URL生成
const url = addUserDataToUrl('https://test.com/webrtc/offer')
console.assert(url.includes('user_id=test-user-123'), 'URL应该包含用户ID参数')
```

### 2. 后端测试

```python
# 测试用户数据提取
from src.utils.user_data_extractor import extract_user_data_from_request

# 模拟请求
class MockRequest:
    def __init__(self, query_params):
        self.query_params = query_params

# 测试查询参数
request = MockRequest({
    'user_id': 'test-user-123',
    'user_name': '张三',
    'school_id': 'school-456'
})

user_data = extract_user_data_from_request(request)
assert user_data['user_id'] == 'test-user-123'
assert user_data['user_name'] == '张三'
assert user_data['school_id'] == 'school-456'
```

## 完整的数据流程

1. **前端获取数据**:
   ```typescript
   // 从URL参数解析用户信息
   const userData = getUserDataForBackend()
   // 结果: { userId: 'test-user-123', userName: '张三', ... }
   ```

2. **前端传递数据**:
   ```typescript
   // 将用户数据添加到请求URL
   const url = addUserDataToUrl('/webrtc/offer')
   // 结果: '/webrtc/offer?user_id=test-user-123&user_name=张三&...'
   ```

3. **后端提取数据**:
   ```python
   # 从请求中提取用户数据
   user_data = extract_user_data_from_request(request)
   # 结果: {'user_id': 'test-user-123', 'user_name': '张三', ...}
   ```

4. **后端使用数据**:
   ```python
   # LLM处理器自动使用用户ID
   user_id = getattr(session_context, 'user_id', None) or handler_config.user_id
   # 结果: 'test-user-123'
   ```

## 配置检查

### 1. 前端配置检查

```typescript
// 检查用户数据获取
const userData = getUserDataForBackend()
if (!userData?.userId) {
  console.error('❌ 无法获取用户ID，请检查URL参数')
} else {
  console.log('✅ 用户数据获取成功:', userData)
}
```

### 2. 后端配置检查

```python
# 检查用户数据提取
user_data = extract_user_data_from_request(request)
if not user_data.get('user_id'):
    logger.warning('❌ 无法获取用户ID，使用默认配置')
else:
    logger.info('✅ 用户数据提取成功:', user_data)
```

## 故障排除

### 常见问题

1. **用户ID为None**:
   - 检查前端URL参数格式
   - 检查后端请求解析逻辑

2. **API调用失败**:
   - 检查用户ID格式是否正确
   - 检查API端点是否可访问

3. **缓存问题**:
   - 清除浏览器缓存
   - 清除后端缓存

### 调试步骤

1. 检查前端控制台日志
2. 检查后端日志中的用户数据提取
3. 验证API调用是否成功
4. 检查会话上下文中的用户ID

## 部署注意事项

1. **环境变量**: 确保API密钥正确配置
2. **CORS设置**: 确保跨域请求正确配置
3. **日志级别**: 设置适当的日志级别便于调试
4. **错误处理**: 实现完善的错误处理机制
