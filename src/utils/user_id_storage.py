# src/utils/user_id_storage.py
"""
简单的用户ID存储服务
用于在同一台服务器上的前端和后端之间传递用户ID
"""

from typing import Dict, Optional
import threading
import time
from loguru import logger

class UserIDStorage:
    """用户ID存储类，使用内存存储"""
    
    def __init__(self):
        self._storage: Dict[str, str] = {}  # session_id -> user_id
        self._lock = threading.Lock()
        self._cleanup_interval = 300  # 5分钟清理一次
        self._last_cleanup = time.time()
    
    def set_user_id(self, session_id: str, user_id: str):
        """设置用户ID"""
        with self._lock:
            self._storage[session_id] = user_id
            # logger.info(f"存储用户ID: session_id={session_id}, user_id={user_id}")
    
    def get_user_id(self, session_id: str) -> Optional[str]:
        """获取用户ID"""
        with self._lock:
            user_id = self._storage.get(session_id)
            if user_id:
                # logger.info(f"获取用户ID: session_id={session_id}, user_id={user_id}")
                pass
            else:
                # logger.warning(f"未找到用户ID: session_id={session_id}")
                pass
            return user_id
    
    def remove_user_id(self, session_id: str):
        """移除用户ID"""
        with self._lock:
            if session_id in self._storage:
                del self._storage[session_id]
                # logger.info(f"移除用户ID: session_id={session_id}")
    
    def cleanup_expired(self):
        """清理过期的用户ID（简单实现，实际项目中可以使用更复杂的过期机制）"""
        current_time = time.time()
        if current_time - self._last_cleanup > self._cleanup_interval:
            with self._lock:
                # 这里可以添加更复杂的过期逻辑
                self._last_cleanup = current_time
                # logger.debug("执行用户ID清理")

# 全局实例
user_id_storage = UserIDStorage()

def set_user_id(session_id: str, user_id: str):
    """设置用户ID的便捷函数"""
    user_id_storage.set_user_id(session_id, user_id)

def get_user_id(session_id: str) -> Optional[str]:
    """获取用户ID的便捷函数"""
    return user_id_storage.get_user_id(session_id)

def remove_user_id(session_id: str):
    """移除用户ID的便捷函数"""
    user_id_storage.remove_user_id(session_id)
