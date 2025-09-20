"""
用户数据提取工具
用于从请求中提取用户信息并传递给会话上下文
"""

import re
from typing import Optional, Dict, Any
from loguru import logger


def extract_user_id_from_request(request) -> Optional[str]:
    """
    从请求中提取用户ID
    
    Args:
        request: FastAPI 请求对象
        
    Returns:
        用户ID字符串，如果未找到则返回None
    """
    try:
        # 方法1: 从查询参数获取
        user_id = request.query_params.get('user_id')
        if user_id:
            logger.info(f"从查询参数获取到用户ID: {user_id}")
            return user_id
        
        # 方法2: 从请求头获取
        user_id = request.headers.get('X-User-ID')
        if user_id:
            logger.info(f"从请求头获取到用户ID: {user_id}")
            return user_id
        
        # 方法3: 从请求体获取（如果是POST请求）
        if hasattr(request, 'json') and request.json:
            user_id = request.json.get('user_id')
            if user_id:
                logger.info(f"从请求体获取到用户ID: {user_id}")
                return user_id
        
        # 方法4: 从URL路径中提取（如果URL包含用户ID）
        url_path = str(request.url.path)
        user_id_match = re.search(r'/user/([^/]+)', url_path)
        if user_id_match:
            user_id = user_id_match.group(1)
            logger.info(f"从URL路径获取到用户ID: {user_id}")
            return user_id
        
        logger.warning("未找到用户ID")
        return None
        
    except Exception as e:
        logger.error(f"提取用户ID时发生错误: {e}")
        return None


def extract_user_data_from_request(request) -> Dict[str, Any]:
    """
    从请求中提取完整的用户数据
    
    Args:
        request: FastAPI 请求对象
        
    Returns:
        包含用户数据的字典
    """
    user_data = {}
    
    try:
        # 从查询参数获取
        query_params = request.query_params
        user_data.update({
            'user_id': query_params.get('user_id'),
            'user_name': query_params.get('user_name'),
            'school_id': query_params.get('school_id'),
            'school_name': query_params.get('school_name'),
            'grade': query_params.get('grade'),
            'class': query_params.get('class'),
        })
        
        # 从请求头获取
        headers = request.headers
        if not user_data.get('user_id'):
            user_data['user_id'] = headers.get('X-User-ID')
        if not user_data.get('user_name'):
            user_data['user_name'] = headers.get('X-User-Name')
        
        # 从请求体获取（如果是POST请求）
        if hasattr(request, 'json') and request.json:
            body_data = request.json
            for key in ['user_id', 'user_name', 'school_id', 'school_name', 'grade', 'class']:
                if not user_data.get(key) and body_data.get(key):
                    user_data[key] = body_data[key]
        
        # 过滤掉None值
        user_data = {k: v for k, v in user_data.items() if v is not None}
        
        if user_data:
            logger.info(f"成功提取用户数据: {user_data}")
        else:
            logger.warning("未找到任何用户数据")
            
        return user_data
        
    except Exception as e:
        logger.error(f"提取用户数据时发生错误: {e}")
        return {}


def set_user_data_to_session_context(session_context, user_data: Dict[str, Any]):
    """
    将用户数据设置到会话上下文中
    
    Args:
        session_context: 会话上下文对象
        user_data: 用户数据字典
    """
    try:
        # 设置用户ID
        if user_data.get('user_id'):
            session_context.user_id = user_data['user_id']
            logger.info(f"设置会话用户ID: {user_data['user_id']}")
        
        # 设置其他用户信息
        for key, value in user_data.items():
            if key != 'user_id' and value:
                setattr(session_context, key, value)
                logger.debug(f"设置会话属性 {key}: {value}")
                
    except Exception as e:
        logger.error(f"设置用户数据到会话上下文时发生错误: {e}")


def create_session_context_with_user_data(session_id: str, user_data: Dict[str, Any]):
    """
    创建包含用户数据的会话上下文
    
    Args:
        session_id: 会话ID
        user_data: 用户数据字典
        
    Returns:
        会话上下文对象
    """
    try:
        # 这里需要根据实际的会话上下文类来创建
        # 假设有一个SessionContext类
        from chat_engine.contexts.session_context import SessionContext
        
        # 创建会话上下文
        session_context = SessionContext(session_id)
        
        # 设置用户数据
        set_user_data_to_session_context(session_context, user_data)
        
        return session_context
        
    except Exception as e:
        logger.error(f"创建会话上下文时发生错误: {e}")
        return None
