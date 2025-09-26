from pydantic import BaseModel, Field
from typing import Optional


class LoggerConfigData(BaseModel):
    log_level: str = Field(default="INFO")
    # LLM模块专用日志配置
    llm_log_file: Optional[str] = Field(default="logs/llm_handler.log")
    llm_log_level: Optional[str] = Field(default="DEBUG")
    llm_log_rotation: Optional[str] = Field(default="50 MB")
    llm_log_retention: Optional[int] = Field(default=30)
