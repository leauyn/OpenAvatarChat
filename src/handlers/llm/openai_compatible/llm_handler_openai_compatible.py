

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

# 全局缓存，避免重复请求
_survey_data_cache = {}
_user_info_cache = {}


def parse_survey_data(data_list: list) -> str:
    """
    解析测评数据，按群体分类组织数据
    输出格式：重点关注: 项目1, 项目2; 一般关注: 项目3, 项目4; 健康: 项目5, 项目6
    自动去重，每个测评项目只保留一个结果
    """
    # 按群体分类存储数据，使用集合去重
    group_categories = {
        "重点关注": set(),
        "一般关注": set(),
        "健康": set()
    }
    
    for item in data_list:
        if "name" not in item or "value" not in item:
            continue
            
        name = item["name"]
        value = item["value"]
        
        # 提取群体信息：查找 "A." 和 "B." 之间的群体信息
        import re
        pattern = r'A\.\s*根据学校量表测评结果，该学生.*?情况，处于(.*?)群体'
        match = re.search(pattern, value)
        
        if match:
            group_info = match.group(1).strip()
        else:
            # 如果没有找到标准格式，尝试其他可能的格式
            pattern2 = r'处于(.*?)群体'
            match2 = re.search(pattern2, value)
            if match2:
                group_info = match2.group(1).strip()
            else:
                # 如果都没有找到，跳过该项目
                continue
        
        # 根据群体信息分类
        if group_info == "重点关注":
            group_categories["重点关注"].add(name)
        elif group_info == "一般关注":
            group_categories["一般关注"].add(name)
        elif group_info == "健康":
            group_categories["健康"].add(name)
    
    # 构建输出字符串
    result_lines = []
    for category, items in group_categories.items():
        if items:  # 只添加非空的分类
            # 将集合转换为排序的列表，确保输出顺序一致
            sorted_items = sorted(list(items))
            result_lines.append(f"{category}: {', '.join(sorted_items)}")
    
    return "\n".join(result_lines)


def parse_user_info(user_data: dict) -> str:
    """
    解析用户信息，提取指定字段
    包含：姓名(name), 年级(nj)，班级(bj)，地址(addressCode)，性别（sex)， 学校名称（schoolName）
    如为 null 则不解析
    """
    user_info_lines = []
    
    # 定义字段映射
    field_mapping = {
        'name': '姓名',
        'nj': '年级', 
        'bj': '班级',
        'addressCode': '地址',
        'sex': '性别',
        'schoolName': '学校名称'
    }
    
    # 性别映射
    sex_mapping = {'1': '男', '2': '女', '0': '未知'}
    
    for field, display_name in field_mapping.items():
        if field in user_data and user_data[field] is not None:
            value = user_data[field]
            # 特殊处理性别字段
            if field == 'sex' and value in sex_mapping:
                value = sex_mapping[value]
            user_info_lines.append(f"{display_name}: {value}")
    
    return "\n".join(user_info_lines)


def get_user_info(user_id: str, api_url: str) -> str:
    """
    获取用户信息并返回解析结果
    使用缓存避免重复请求
    """
    # 检查缓存
    cache_key = f"{user_id}_{api_url}"
    if cache_key in _user_info_cache:
        logger.debug(f"Using cached user info for user {user_id}")
        return _user_info_cache[cache_key]
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'Referer': 'https://www.zhgk-mind.com/'
        }
        
        response = requests.get(f"{api_url}?userId={user_id}", headers=headers, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        if result.get("resultCode") == 200 and "data" in result:
            user_data = result["data"]
            parsed_info = parse_user_info(user_data)
            # 缓存结果
            _user_info_cache[cache_key] = parsed_info
            logger.info(f"Cached user info for user {user_id}")
            return parsed_info
        else:
            logger.warning(f"Failed to get user info: {result.get('resultMsg', 'Unknown error')}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
        return ""


def get_user_survey_data(user_id: str, api_url: str) -> str:
    """
    获取用户测评数据并返回简化的解析结果
    使用缓存避免重复请求
    """
    # 检查缓存
    cache_key = f"{user_id}_{api_url}"
    if cache_key in _survey_data_cache:
        logger.debug(f"Using cached survey data for user {user_id}")
        return _survey_data_cache[cache_key]
    
    try:
        headers = {'content-type': 'application/json'}
        data = {"userId": user_id}
        
        response = requests.post(api_url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        if result.get("resultCode") == 200 and "data" in result:
            data_list = result["data"]
            parsed_data = parse_survey_data(data_list)
            # 缓存结果
            _survey_data_cache[cache_key] = parsed_data
            logger.info(f"Cached survey data for user {user_id}")
            return parsed_data
        else:
            logger.warning(f"Failed to get survey data: {result.get('resultMsg', 'Unknown error')}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching user survey data: {e}")
        return ""


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
        
        # 获取用户信息和测评数据
        user_info = get_user_info(handler_config.user_id, handler_config.user_info_api_url)
        survey_data = get_user_survey_data(handler_config.user_id, handler_config.survey_api_url)
        
        # 选择系统提示词模板
        if context.system_prompt_templates and "B" in context.system_prompt_templates:
            # 初始时使用模板B（对话模板）
            base_prompt = context.system_prompt_templates["B"]
        else:
            # 使用默认提示词
            base_prompt = handler_config.system_prompt
        
        # 构建增强的系统提示
        enhanced_parts = [base_prompt]
        
        if user_info:
            enhanced_parts.append(f"【用户信息】：\n{user_info}")
        
        if survey_data:
            enhanced_parts.append(f"【用户测评数据】：\n{survey_data}")

        # 只在首次交互时添加开场白指令
        if context.is_first_interaction:
            enhanced_parts.append("""
            
            ---
            
            ### 7. 开始执行
            请严格按照以上所有要求，特别是【当前任务】和【外部输入数据】，生成你的第一句开场白。
            """)
        
        enhanced_system_prompt = "\n\n".join(enhanced_parts)
        context.system_prompt = {'role': 'system', 'content': enhanced_system_prompt}
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
        
        # 从配置中获取API URL
        if handler_config:
            user_id = handler_config.user_id
            user_info_api_url = handler_config.user_info_api_url
            survey_api_url = handler_config.survey_api_url
        else:
            # 使用默认值
            user_id = "4d8f3a08-e886-43ff-ba7f-93ca0a1b0f96"
            user_info_api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/userInfo.do"
            survey_api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/getUserResultInfo.do"
        
        # 获取用户信息和测评数据
        user_info = get_user_info(user_id, user_info_api_url)
        survey_data = get_user_survey_data(user_id, survey_api_url)
        
        # 使用指定模板
        base_prompt = context.system_prompt_templates[template]
        
        # 构建增强的系统提示
        enhanced_parts = [base_prompt]
        
        if user_info:
            enhanced_parts.append(f"【用户信息】：\n{user_info}")
        
        if survey_data:
            enhanced_parts.append(f"【用户测评数据】：\n{survey_data}")
        
        enhanced_system_prompt = "\n\n".join(enhanced_parts)
        context.system_prompt = {'role': 'system', 'content': enhanced_system_prompt}
        
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
        if inputs.type == ChatDataType.CAMERA_VIDEO and context.enable_video_input:
            context.current_image = inputs.data.get_main_data()
            return
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
        logger.debug(f'llm input {context.model_name} {current_content} ')
        
        # 如果模板已切换，记录新的系统提示词
        if template_switched:
            logger.info(f"使用更新后的系统提示词（模板A）: {context.system_prompt['content'][:100]}...")
        
        try:
            completion = context.client.chat.completions.create(
                model=context.model_name,  # 此处以qwen-plus为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
                messages=[
                    context.system_prompt,
                ] + current_content,
                stream=True,
                stream_options={"include_usage": True}
            )
            context.current_image = None
            context.input_texts = ''
            context.output_texts = ''
            for chunk in completion:
                if (chunk and chunk.choices and chunk.choices[0] and chunk.choices[0].delta.content):
                    output_text = chunk.choices[0].delta.content
                    context.output_texts += output_text
                    logger.info(output_text)
                    output = DataBundle(output_definition)
                    output.set_main_data(output_text)
                    output.add_meta("avatar_text_end", False)
                    output.add_meta("speech_id", speech_id)
                    yield output
            context.history.add_message(HistoryMessage(role="human", content=chat_text))
            context.history.add_message(HistoryMessage(role="avatar", content=context.output_texts))
        except Exception as e:
            logger.error(e)
            if (isinstance(e, APIStatusError)):
                response = e.body
                if isinstance(response, dict) and "message" in response:
                    response = f"{response['message']}"
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

