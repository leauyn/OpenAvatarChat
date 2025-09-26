

import os
import re
import requests
import json
from typing import Dict, Optional, cast
from loguru import logger
from pydantic import BaseModel, Field
from abc import ABC
from openai import APIStatusError, OpenAI
from chat_engine.contexts.handler_context import HandlerContext
from chat_engine.data_models.chat_engine_config_data import ChatEngineConfigModel, HandlerBaseConfigModel
from chat_engine.common.handler_base import HandlerBase, HandlerBaseInfo, HandlerDataInfo, HandlerDetail
from chat_engine.data_models.chat_data.chat_data_model import ChatData
from chat_engine.data_models.chat_data_type import ChatDataType
from chat_engine.contexts.session_context import SessionContext
from chat_engine.data_models.runtime_data.data_bundle import DataBundle, DataBundleDefinition, DataBundleEntry
from handlers.llm.openai_compatible.chat_history_manager import ChatHistory, HistoryMessage
from handlers.llm.openai_compatible.tools import tools, get_user_info as tool_get_user_info, get_user_survey_data as tool_get_user_survey_data, query_knowledge_base as tool_query_knowledge_base

# 全局缓存，避免重复请求
_survey_data_cache = {}
_user_info_cache = {}

def execute_tool_call(tool_call, context=None):
    """
    执行工具调用
    """
    function_name = tool_call.function.name
    function_args = json.loads(tool_call.function.arguments)
    
    logger.info(f"🔧 开始执行工具调用: {function_name}")
    logger.info(f"📝 工具调用参数: {function_args}")
    
    if function_name == "get_user_info":
        user_id = function_args.get("user_id", "")
        logger.info(f"👤 获取用户信息，用户ID: {user_id}")
        result = tool_get_user_info(user_id)
    elif function_name == "get_user_survey_data":
        user_id = function_args.get("user_id", "")
        logger.info(f"📊 获取用户测评数据，用户ID: {user_id}")
        result = tool_get_user_survey_data(user_id)
    elif function_name == "query_knowledge_base":
        query = function_args.get("query", "")
        logger.info(f"🧠 查询知识库，问题: {query[:100]}...")
        
        # 从context获取RAG配置
        if context and hasattr(context, 'handler_config') and context.handler_config:
            logger.info(f"⚙️ 使用配置中的RAG参数:")
            logger.info(f"   - API URL: {context.handler_config.rag_api_url}")
            logger.info(f"   - API Key: {context.handler_config.rag_api_key[:20]}...")
            logger.info(f"   - Model: {context.handler_config.rag_model}")
            result = tool_query_knowledge_base(
                query,
                context.handler_config.rag_api_url,
                context.handler_config.rag_api_key,
                context.handler_config.rag_model
            )
        else:
            logger.info("⚠️ 使用默认RAG配置")
            result = tool_query_knowledge_base(query)
    else:
        logger.warning(f"❌ 未知的工具调用: {function_name}")
        result = f"未知的工具调用: {function_name}"
    
    logger.info(f"✅ 工具调用完成: {function_name}")
    logger.info(f"📤 工具调用结果长度: {len(str(result))} 字符")
    if len(str(result)) > 200:
        logger.info(f"📤 工具调用结果预览: {str(result)[:200]}...")
    else:
        logger.info(f"📤 工具调用结果: {result}")
    return result



class LLMConfig(HandlerBaseConfigModel, BaseModel):
    model_name: str = Field(default="qwen-plus")
    system_prompt: str = Field(default="请你扮演一个 AI 助手，用简短的对话来回答用户的问题，并在对话内容中加入合适的标点符号，不需要加入标点符号相关的内容")
    api_key: str = Field(default=os.getenv("DASHSCOPE_API_KEY"))
    api_url: str = Field(default=None)
    enable_video_input: bool = Field(default=False)
    history_length: int = Field(default=20)
    user_id: str = Field(default="4d8f3a08-e886-43ff-ba7f-93ca0a1b0f96")
    survey_api_url: str = Field(default="https://www.zhgk-mind.com/api/dwsurvey/anon/response/getUserResultInfo.do")
    user_info_api_url: str = Field(default="https://www.zhgk-mind.com/api/dwsurvey/anon/response/userInfo.do")
    # 支持多个提示词模板
    system_prompt_templates: Optional[Dict[str, str]] = Field(default=None)
    # RAG配置
    enable_rag: bool = Field(default=True)
    rag_api_url: str = Field(default="https://ragflow.thinnovate.com/api/v1/chats_openai/9a15923a991b11f088f40242ac170006/chat/completions")
    rag_api_key: str = Field(default="ragflow-I5ZWIyNDk0OTg3MDExZjBiZWNlMDI0Mm")
    rag_model: str = Field(default="model")


class LLMContext(HandlerContext):
    def __init__(self, session_id: str):
        super().__init__(session_id)
        self.config = None
        self.local_session_id = 0
        self.model_name = None
        self.system_prompt = None
        self.api_key = None
        self.api_url = None
        self.client = None
        self.input_texts = ""
        self.output_texts = ""
        self.current_image = None
        self.history = None
        self.enable_video_input = False
        # 对话状态跟踪
        self.is_first_interaction = True  # 标记是否为首次交互
        self.system_prompt_templates = None
        self.handler_config = None  # 存储配置信息
        self.user_id = None  # 存储用户ID


class HandlerLLM(HandlerBase, ABC):
    def __init__(self):
        super().__init__()

    def get_handler_info(self) -> HandlerBaseInfo:
        return HandlerBaseInfo(
            config_model=LLMConfig,
        )

    def get_handler_detail(self, session_context: SessionContext,
                           context: HandlerContext) -> HandlerDetail:
        definition = DataBundleDefinition()
        definition.add_entry(DataBundleEntry.create_text_entry("avatar_text"))
        inputs = {
            ChatDataType.HUMAN_TEXT: HandlerDataInfo(
                type=ChatDataType.HUMAN_TEXT,
            ),
            ChatDataType.CAMERA_VIDEO: HandlerDataInfo(
                type=ChatDataType.CAMERA_VIDEO,
            ),
        }
        outputs = {
            ChatDataType.AVATAR_TEXT: HandlerDataInfo(
                type=ChatDataType.AVATAR_TEXT,
                definition=definition,
            )
        }
        return HandlerDetail(
            inputs=inputs, outputs=outputs,
        )

    def load(self, engine_config: ChatEngineConfigModel, handler_config: Optional[BaseModel] = None):
        if isinstance(handler_config, LLMConfig):
            if handler_config.api_key is None or len(handler_config.api_key) == 0:
                error_message = 'api_key is required in config/xxx.yaml, when use handler_llm'
                logger.error(error_message)
                raise ValueError(error_message)

    def create_context(self, session_context, handler_config=None):
        if not isinstance(handler_config, LLMConfig):
            handler_config = LLMConfig()
        context = LLMContext(session_context.session_info.session_id)
        context.model_name = handler_config.model_name
        context.system_prompt_templates = handler_config.system_prompt_templates
        
        # 存储配置信息，供后续使用
        context.handler_config = handler_config
        
        # 详细排查用户ID获取逻辑
        # logger.info(f"🔍 create_context 用户ID排查开始:")
        # logger.info(f"  - session_context.user_id: {getattr(session_context, 'user_id', 'NOT_SET')}")
        # logger.info(f"  - hasattr(session_context, 'user_id'): {hasattr(session_context, 'user_id')}")
        # logger.info(f"  - hasattr(session_context, 'is_user_id_updated'): {hasattr(session_context, 'is_user_id_updated')}")
        # if hasattr(session_context, 'is_user_id_updated'):
        #     logger.info(f"  - session_context.is_user_id_updated(): {session_context.is_user_id_updated()}")
        # logger.info(f"  - handler_config.user_id: {handler_config.user_id}")
        
        # 尝试从会话上下文获取用户ID，如果没有则使用配置中的默认值
        user_id = getattr(session_context, 'user_id', None) or handler_config.user_id
        
        # 如果会话上下文有用户ID更新标志，优先使用会话上下文中的用户ID
        if hasattr(session_context, 'is_user_id_updated') and session_context.is_user_id_updated():
            user_id = getattr(session_context, 'user_id', None) or user_id
            # logger.info(f"✅ 使用已更新的会话用户ID: {user_id}")
        
        # 如果仍然没有用户ID，尝试从存储中获取（使用session_id作为key）
        if not user_id or user_id == handler_config.user_id:
            try:
                from src.utils.user_id_storage import get_user_id
                stored_user_id = get_user_id(session_context.session_info.session_id)
                if stored_user_id:
                    user_id = stored_user_id
                    # logger.info(f"✅ 从存储中获取到用户ID: {user_id}")
            except Exception as e:
                logger.warning(f"⚠️ 从存储获取用户ID失败: {e}")
        
        # 将获取到的用户ID也更新到会话上下文中
        if user_id and user_id != handler_config.user_id:
            if hasattr(session_context, 'update_user_id'):
                session_context.update_user_id(user_id)
                # logger.info(f"✅ 更新会话上下文用户ID: {user_id}")
        
        # logger.info(f"🎯 create_context 最终使用的用户ID: {user_id}")
        
        # 将用户ID存储到context中
        context.user_id = user_id
        
        # 选择系统提示词模板
        if context.system_prompt_templates and "B" in context.system_prompt_templates:
            # 初始时使用模板B（对话模板）
            base_prompt = context.system_prompt_templates["B"]
        else:
            # 使用默认提示词
            base_prompt = handler_config.system_prompt
        
        # 构建增强的系统提示
        enhanced_parts = [base_prompt]
        
        # 添加用户ID信息（如果模板中没有包含）
        if "当前用户ID" not in base_prompt:
            enhanced_parts.append(f"""
            
            **重要：当前用户ID是 {user_id}，调用用户相关工具时请使用此ID作为user_id参数。**
            """)

        # 只在首次交互时添加开场白指令
        if context.is_first_interaction:
            enhanced_parts.append("""
            
            ---
            
            ### 6. 开始执行
            请严格按照以上所有要求，特别是【本次任务】，生成你的第一句开场白。
            如果需要用户信息或测评数据，请先调用相应的工具获取。
            如果涉及专业心理知识，请先调用 query_knowledge_base 工具获取权威答案。
            """)
        else:
            enhanced_parts.append("""
            
            ---
            
            ### 6. 开始执行
            请严格按照以上所有要求，特别是【本次任务】，生成你的回应。
            如果需要用户信息或测评数据，请先调用相应的工具获取。
            如果涉及专业心理知识，请先调用 query_knowledge_base 工具获取权威答案。
            """)
        
        enhanced_system_prompt = "\n\n".join(enhanced_parts)
        context.system_prompt = {'role': 'system', 'content': enhanced_system_prompt}
        
        # 调试日志：检查系统提示词是否包含RAG工具说明
        if "query_knowledge_base" in enhanced_system_prompt:
            logger.info("✅ 系统提示词包含RAG工具说明")
        else:
            logger.warning("⚠️ 系统提示词缺少RAG工具说明")
        
        logger.info(f"📝 系统提示词长度: {len(enhanced_system_prompt)} 字符")
        print(context.system_prompt)
        context.api_key = handler_config.api_key
        context.api_url = handler_config.api_url
        context.enable_video_input = handler_config.enable_video_input
        context.history = ChatHistory(history_length=handler_config.history_length)
        context.client = OpenAI(
            # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx",
            api_key=context.api_key,
            base_url=context.api_url,
        )
        return context
    
    def update_system_prompt_for_conversation(self, context: LLMContext, handler_config=None, template="B"):
        """
        更新系统提示词为指定模板
        template: "A" 为开场白模式, "B" 为对话模式
        """
        if not context.system_prompt_templates or template not in context.system_prompt_templates:
            logger.warning(f"无法切换到模板{template}：system_prompt_templates或模板{template}不存在")
            return
        
        template_name = "开场白模式" if template == "A" else "对话模式"
        logger.info(f"正在切换到{template_name}（模板{template}）")
        
        # 从配置中获取API URL和用户ID
        if handler_config:
            default_user_id = handler_config.user_id
            user_info_api_url = handler_config.user_info_api_url
            survey_api_url = handler_config.survey_api_url
        else:
            # 使用默认值
            default_user_id = "4d8f3a08-e886-43ff-ba7f-93ca0a1b0f96"
            user_info_api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/userInfo.do"
            survey_api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/getUserResultInfo.do"
        
        # 详细排查用户ID获取逻辑
        # logger.info(f"🔍 用户ID排查开始:")
        # logger.info(f"  - context.user_id: {getattr(context, 'user_id', 'NOT_SET')}")
        # logger.info(f"  - hasattr(context, 'user_id'): {hasattr(context, 'user_id')}")
        # logger.info(f"  - context.user_id is not None: {getattr(context, 'user_id', None) is not None}")
        # logger.info(f"  - context.user_id bool值: {bool(getattr(context, 'user_id', None))}")
        # logger.info(f"  - handler_config.user_id: {default_user_id}")
        
        # 如果context中有用户ID，优先使用context中的
        if hasattr(context, 'user_id') and context.user_id is not None and context.user_id.strip():
            user_id = context.user_id
            # logger.info(f"✅ 使用context中的用户ID: {user_id}")
        else:
            # 尝试从存储中获取最新的用户ID
            try:
                from src.utils.user_id_storage import get_user_id
                # 尝试从多个可能的key获取用户ID
                stored_user_id = None
                
                # 尝试从context的session_id获取
                if hasattr(context, 'session_id'):
                    stored_user_id = get_user_id(context.session_id)
                    # logger.info(f"🔍 尝试从存储获取用户ID，session_id: {context.session_id}")
                
                if stored_user_id:
                    user_id = stored_user_id
                    # logger.info(f"✅ 从存储中获取到用户ID: {user_id}")
                else:
                    user_id = default_user_id
                    logger.warning(f"⚠️ 使用默认用户ID: {user_id}")
            except Exception as e:
                logger.error(f"⚠️ 从存储获取用户ID失败: {e}")
                user_id = default_user_id
                logger.warning(f"⚠️ 使用默认用户ID: {user_id}")
        
        # logger.info(f"🎯 最终使用的用户ID: {user_id}")
        
        # 使用指定模板
        base_prompt = context.system_prompt_templates[template]
        
        # 构建增强的系统提示
        enhanced_parts = [base_prompt]
        
        # 添加用户ID信息（如果模板中没有包含）
        if "当前用户ID" not in base_prompt:
            enhanced_parts.append(f"""
            
            **重要：当前用户ID是 {user_id}，调用用户相关工具时请使用此ID作为user_id参数。**
            """)
        
        enhanced_system_prompt = "\n\n".join(enhanced_parts)
        context.system_prompt = {'role': 'system', 'content': enhanced_system_prompt}
        
        # 调试日志：检查系统提示词是否包含RAG工具说明
        if "query_knowledge_base" in enhanced_system_prompt:
            logger.info("✅ 更新后的系统提示词包含RAG工具说明")
        else:
            logger.warning("⚠️ 更新后的系统提示词缺少RAG工具说明")
        
        logger.info(f"📝 更新后的系统提示词长度: {len(enhanced_system_prompt)} 字符")
        
        # 更新对话状态
        context.is_first_interaction = False
        logger.info(f"已成功切换到{template_name}（模板{template}）")
    
    def start_context(self, session_context, handler_context):
        pass

    def handle(self, context: HandlerContext, inputs: ChatData,
               output_definitions: Dict[ChatDataType, HandlerDataInfo]):
        output_definition = output_definitions.get(ChatDataType.AVATAR_TEXT).definition
        context = cast(LLMContext, context)
        
        # 如果是首次交互，在第一次处理用户输入前切换到开场白模式
        template_switched = False
        if context.is_first_interaction and inputs.type == ChatDataType.HUMAN_TEXT:
            logger.info("首次用户输入，切换到开场白模式（模板A）")
            # 使用存储的配置信息
            self.update_system_prompt_for_conversation(context, context.handler_config, template="A")
            template_switched = True
        
        text = None
        # 检查是否为视频输入且启用了视频处理
        if inputs.type == ChatDataType.CAMERA_VIDEO and context.enable_video_input:
            # 存储视频帧到上下文
            context.current_image = inputs.data.get_main_data()
            return # 立即返回，不进行LLM调用
        # 处理文本输入
        elif inputs.type == ChatDataType.HUMAN_TEXT:
            text = inputs.data.get_main_data()
        else:
            return
        speech_id = inputs.data.get_meta("speech_id")
        if (speech_id is None):
            speech_id = context.session_id

        if text is not None:
            context.input_texts += text

        text_end = inputs.data.get_meta("human_text_end", False)
        if not text_end:
            return

        chat_text = context.input_texts
        chat_text = re.sub(r"<\|.*?\|>", "", chat_text)
        if len(chat_text) < 1:
            return
        logger.info(f'llm input {context.model_name} {chat_text} ')
        current_content = context.history.generate_next_messages(chat_text, 
                                                                 [context.current_image] if context.current_image is not None else [])
        logger.info(f"📚 对话历史长度: {len(current_content)} 条消息")
        logger.debug(f'llm input {context.model_name} {current_content} ')
        
        # 如果模板已切换，记录新的系统提示词
        if template_switched:
            logger.info(f"使用更新后的系统提示词（模板A）: {context.system_prompt['content'][:100]}...")
        
        # 直接调用大模型，让模型根据需要使用工具
        logger.info("调用大模型处理用户输入...")
        try:
            completion = context.client.chat.completions.create(
                model=context.model_name,  # 此处以qwen-plus为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
                messages=[
                    context.system_prompt,
                ] + current_content,
                tools=tools,  # 添加工具定义
                tool_choice="auto",  # 自动选择工具
                stream=True,
                stream_options={"include_usage": True}
            )
            context.current_image = None
            context.input_texts = ''
            context.output_texts = ''
            
            # 处理流式响应，支持工具调用
            tool_calls = []
            logger.info(f"🔄 开始处理流式响应，模型: {context.model_name}")
            for chunk in completion:
                if chunk and chunk.choices and chunk.choices[0]:
                    choice = chunk.choices[0]
                    
                    # 处理工具调用
                    if choice.delta.tool_calls:
                        logger.info(f"🔧 检测到工具调用: {len(choice.delta.tool_calls)} 个")
                        for tool_call in choice.delta.tool_calls:
                            if tool_call.id not in [tc.id for tc in tool_calls]:
                                tool_calls.append(tool_call)
                            else:
                                # 更新现有工具调用
                                for i, existing_tc in enumerate(tool_calls):
                                    if existing_tc.id == tool_call.id:
                                        if tool_call.function:
                                            if not existing_tc.function:
                                                existing_tc.function = tool_call.function
                                            else:
                                                if tool_call.function.name:
                                                    existing_tc.function.name = tool_call.function.name
                                                if tool_call.function.arguments:
                                                    # 确保arguments不为None
                                                    if existing_tc.function.arguments is None:
                                                        existing_tc.function.arguments = tool_call.function.arguments
                                                    else:
                                                        existing_tc.function.arguments += tool_call.function.arguments
                                        break
                    
                    # 处理普通文本输出
                    if choice.delta.content:
                        output_text = choice.delta.content
                        context.output_texts += output_text
                        logger.info(output_text)
                        output = DataBundle(output_definition)
                        output.set_main_data(output_text)
                        output.add_meta("avatar_text_end", False)
                        output.add_meta("speech_id", speech_id)
                        yield output
            
            # 流式响应处理完成，检查工具调用
            logger.info(f"📊 流式响应处理完成，共收集到 {len(tool_calls)} 个工具调用")
            
            # 执行工具调用
            if tool_calls:
                logger.info(f"🔍 检测到 {len(tool_calls)} 个工具调用")
                for i, tool_call in enumerate(tool_calls):
                    if tool_call.function:
                        logger.info(f"   {i+1}. 工具名称: {tool_call.function.name}")
                        logger.info(f"      工具ID: {tool_call.id}")
                        logger.info(f"      工具参数: {tool_call.function.arguments}")
                
                # 构建assistant消息，包含工具调用
                assistant_message = {
                    "role": "assistant",
                    "content": context.output_texts or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        } for tc in tool_calls if tc.function
                    ]
                }
                
                # 将assistant的工具调用消息添加到对话历史
                assistant_history_message = HistoryMessage(
                    role="assistant",
                    content=context.output_texts or "",
                    tool_calls=assistant_message["tool_calls"]
                )
                context.history.add_message(assistant_history_message)
                logger.info(f"📝 已添加assistant工具调用消息到对话历史，包含 {len(assistant_message['tool_calls'])} 个工具调用")
                
                # 执行工具调用并收集结果
                tool_results = []
                for i, tool_call in enumerate(tool_calls):
                    if tool_call.function:
                        logger.info(f"🔄 执行第 {i+1}/{len(tool_calls)} 个工具调用")
                        tool_result = execute_tool_call(tool_call, context)
                        tool_results.append(tool_result)
                        
                        # 将工具调用结果添加到对话历史
                        context.history.add_message(HistoryMessage(
                            role="tool", 
                            content=tool_result,
                            tool_call_id=tool_call.id
                        ))
                        
                        logger.info(f"📝 工具调用结果已添加到对话历史，工具ID: {tool_call.id}")
                
                # 构建包含工具调用和结果的完整消息列表
                messages_with_tools = [context.system_prompt] + current_content + [assistant_message]
                
                # 添加工具结果消息
                for i, tool_call in enumerate(tool_calls):
                    if i < len(tool_results):
                        messages_with_tools.append({
                            "role": "tool",
                            "content": tool_results[i],
                            "tool_call_id": tool_call.id
                        })
                
                # 让大模型基于工具调用结果生成最终回答
                logger.info("基于工具调用结果生成最终回答...")
                final_completion = context.client.chat.completions.create(
                    model=context.model_name,
                    messages=messages_with_tools,
                    stream=True,
                    stream_options={"include_usage": True}
                )
                
                # 清空之前的输出，准备输出最终结果
                context.output_texts = ''
                
                for chunk in final_completion:
                    if chunk and chunk.choices and chunk.choices[0] and chunk.choices[0].delta.content:
                        output_text = chunk.choices[0].delta.content
                        context.output_texts += output_text
                        logger.info(output_text)
                        output = DataBundle(output_definition)
                        output.set_main_data(output_text)
                        output.add_meta("avatar_text_end", False)
                        output.add_meta("speech_id", speech_id)
                        yield output
            else:
                # 没有工具调用，流式输出已经在上面处理了，这里不需要重复输出
                logger.info("ℹ️ 没有检测到工具调用，直接使用流式输出结果")
                logger.info(f"📝 用户输入: {chat_text[:100]}...")
                logger.info("💡 提示：如果这是心理相关问题，模型应该调用 query_knowledge_base 工具")
            
            # 添加对话历史
            context.history.add_message(HistoryMessage(role="human", content=chat_text))
            
            # 只有在没有工具调用时才添加avatar消息到历史
            # 如果有工具调用，assistant消息已经在上面添加了
            if not tool_calls:
                context.history.add_message(HistoryMessage(role="avatar", content=context.output_texts))
                logger.info("📝 已添加avatar消息到对话历史")
            else:
                # 如果有工具调用，添加最终回答的avatar消息
                final_avatar_message = HistoryMessage(role="avatar", content=context.output_texts)
                context.history.add_message(final_avatar_message)
                logger.info("📝 已添加最终回答的avatar消息到对话历史")
        except Exception as e:
            logger.error(e)
            response = "抱歉，处理您的请求时出现了错误，请稍后再试。"
            if (isinstance(e, APIStatusError)):
                error_body = e.body
                if isinstance(error_body, dict) and "message" in error_body:
                    response = f"API错误: {error_body['message']}"
            output_text = response 
            output = DataBundle(output_definition)
            output.set_main_data(output_text)
            output.add_meta("avatar_text_end", False)
            output.add_meta("speech_id", speech_id)
            yield output
        context.input_texts = ''
        context.output_texts = ''
        logger.info('avatar text end')
        end_output = DataBundle(output_definition)
        end_output.set_main_data('')
        end_output.add_meta("avatar_text_end", True)
        end_output.add_meta("speech_id", speech_id)
        yield end_output

    def destroy_context(self, context: HandlerContext):
        pass

