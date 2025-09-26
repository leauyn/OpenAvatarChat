import sys
import os

from loguru import logger
from service.service_data_models.logger_config_data import LoggerConfigData


def config_loggers(in_logger_config: LoggerConfigData):
    logger.info(f"Set log level to {in_logger_config.log_level}")
    logger.remove()
    logger.add(sys.stdout, level=in_logger_config.log_level)
    
    # 确保logs目录存在
    os.makedirs("logs", exist_ok=True)
    
    # 通用日志文件
    logger.add("logs/log.log", rotation="10 MB", retention=10, encoding="utf-8", enqueue=True)
    
    # LLM模块专用日志文件（如果配置了的话）
    if in_logger_config.llm_log_file:
        logger.info(f"配置LLM模块专用日志文件: {in_logger_config.llm_log_file}")
        logger.add(
            in_logger_config.llm_log_file, 
            filter=lambda record: (
                "llm_handler_openai_compatible" in record["name"] or 
                "handlers.llm" in record["name"] or
                "llm" in record["name"].lower() or
                "🔧" in record["message"] or  # 工具调用相关日志
                "📝" in record["message"] or  # 工具参数相关日志
                "👤" in record["message"] or  # 用户信息相关日志
                "✅" in record["message"] or  # 成功相关日志
                "📤" in record["message"] or  # 结果相关日志
                "🔍" in record["message"] or  # 调试相关日志
                "⚠️" in record["message"] or  # 警告相关日志
                "❌" in record["message"]     # 错误相关日志
            ),
            level=in_logger_config.llm_log_level or "DEBUG",
            rotation=in_logger_config.llm_log_rotation or "50 MB", 
            retention=in_logger_config.llm_log_retention or 30, 
            encoding="utf-8", 
            enqueue=True,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"
        )
