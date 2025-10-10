import requests
import json
import redis
import re
import yaml
import os
from loguru import logger
from openai import OpenAI

# 全局缓存，避免重复请求
_survey_data_cache = {}
_user_info_cache = {}
_simplify_cache = {}  # LLM精简结果缓存

# Redis 连接配置
REDIS_HOST = 'localhost'
REDIS_PORT = 6779
REDIS_DB = 0

# LLM精简功能独立配置
SIMPLIFY_LLM_CONFIG = {
    "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": os.getenv("DASHSCOPE_API_KEY"),
    "model": "qwen-plus",
    "temperature": 0.1,
    "max_tokens": 2000
}

# 全局LLM客户端实例
_simplify_llm_client = None

def get_default_llm_config():
    """获取LLM精简功能的默认配置"""
    return {
        "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "model": "qwen-flash",
        "temperature": 0.1,
        "max_tokens": 2000,
        "enable_llm_simplify": True,
        "fallback_to_regex": True,
        "system_prompt": "你是一个专业的心理测评报告处理助手，擅长精简和提取核心信息。",
        "user_prompt_template": ""
    }

def load_simplify_llm_config():
    """从配置文件加载LLM精简功能配置"""
    global SIMPLIFY_LLM_CONFIG
    
    config_path = os.path.join(os.path.dirname(__file__), '../../../config/simplify_llm_config.yaml')
    
    # 获取默认配置
    default_config = get_default_llm_config()
    
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f)
            
            # 使用默认配置作为基础，文件配置覆盖默认值
            SIMPLIFY_LLM_CONFIG = {**default_config, **file_config}
            
            logger.info(f"✅ LLM精简配置已从文件加载: {config_path}")
            return True
        else:
            # 使用默认配置
            SIMPLIFY_LLM_CONFIG = default_config
            logger.warning(f"⚠️ LLM精简配置文件不存在: {config_path}，使用默认配置")
            return False
    except Exception as e:
        logger.error(f"❌ 加载LLM精简配置失败: {e}，使用默认配置")
        SIMPLIFY_LLM_CONFIG = default_config
        return False

def get_redis_connection():
    """获取 Redis 连接，确保以文本格式存储"""
    try:
        r = redis.Redis(
            host=REDIS_HOST, 
            port=REDIS_PORT, 
            db=REDIS_DB, 
            decode_responses=True,  # 确保返回字符串而不是字节
            encoding='utf-8'        # 明确指定编码
        )
        # 测试连接
        r.ping()
        return r
    except Exception as e:
        logger.error(f"Redis 连接失败: {e}")
        return None


def get_simplify_llm_client():
    """获取LLM精简功能的客户端，避免多次初始化"""
    global _simplify_llm_client
    
    if _simplify_llm_client is None:
        try:
            # 检查API Key是否有效
            api_key = SIMPLIFY_LLM_CONFIG.get("api_key")
            if not api_key or api_key == "sk-your-simplify-api-key":
                logger.warning("⚠️ LLM精简API Key未配置或使用默认值，跳过初始化")
                return None
            
            _simplify_llm_client = OpenAI(
                api_key=api_key,
                base_url=SIMPLIFY_LLM_CONFIG["api_url"]
            )
            logger.info(f"✅ LLM精简客户端初始化成功，模型: {SIMPLIFY_LLM_CONFIG['model']}")
        except Exception as e:
            logger.error(f"❌ LLM精简客户端初始化失败: {e}")
            return None
    else:
        logger.debug("🔄 使用已初始化的LLM精简客户端")
    
    return _simplify_llm_client


def update_simplify_llm_config(**kwargs):
    """更新LLM精简功能配置"""
    global _simplify_llm_client
    
    # 更新配置
    for key, value in kwargs.items():
        if key in SIMPLIFY_LLM_CONFIG:
            SIMPLIFY_LLM_CONFIG[key] = value
    
    # 重置客户端实例，强制重新初始化
    _simplify_llm_client = None
    
    logger.info(f"✅ LLM精简配置已更新: {SIMPLIFY_LLM_CONFIG}")
    return SIMPLIFY_LLM_CONFIG

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_user_info",
            "description": "当你想查询指定用户的个人信息时非常有用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "基本信息，比如姓名、性别、年龄、地址、学校等。",
                    }
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_survey_data",
            "description": "当你想查询指定用户的测评数据、报告、或结果时非常有用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "测评数据，比如重点关注、一般关注、健康等。",
                    }
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "当用户询问任何心理健康、心理理论、心理咨询、心理疾病、心理症状、心理测评、心理治疗方法、心理干预等相关问题时，必须使用此工具查询专业知识库获取权威答案。这是获取专业心理知识的唯一途径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要查询的心理健康相关问题，如心理理论、咨询方法、测评知识等。",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_guidance_plan",
            "description": "根据测评结果的code值查询对应的指导方案。当用户需要获取具体的心理指导建议时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "测评结果的code值，如'1-5-C'，用于查询对应的指导方案。",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_guidance_by_dimension",
            "description": "根据测评维度名称获取对应的指导方案。当用户询问具体测评维度的解决方案或指导时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "用户ID，用于获取该用户的测评结果。",
                    },
                    "dimension_name": {
                        "type": "string",
                        "description": "测评维度名称，如'师生关系'、'同伴关系'、'学习焦虑'、'抑郁'、'自我效能感'等。",
                    }
                },
                "required": ["user_id", "dimension_name"],
            },
        },
    },
]

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


def get_user_info(user_id: str) -> str:
    """
    获取用户信息并返回解析结果
    使用 Redis 缓存避免重复请求
    """
    # 默认API URL
    api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/userInfo.do"
    
    # Redis 缓存 key 格式: userid:user_info
    redis_key = f"{user_id}:user_info"
    
    # 尝试从 Redis 获取缓存
    redis_conn = get_redis_connection()
    if redis_conn:
        try:
            cached_data = redis_conn.get(redis_key)
            if cached_data:
                logger.debug(f"Using Redis cached user info for user {user_id}")
                return cached_data
        except Exception as e:
            logger.warning(f"Redis 读取失败，回退到内存缓存: {e}")
    
    # 如果 Redis 不可用，回退到内存缓存
    cache_key = f"{user_id}_{api_url}"
    if cache_key in _user_info_cache:
        logger.debug(f"Using memory cached user info for user {user_id}")
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
            
            # 优先存储到 Redis
            if redis_conn:
                try:
                    redis_conn.set(redis_key, parsed_info, ex=604800)  # 设置1周过期时间
                    logger.info(f"Cached user info to Redis for user {user_id} (expires in 1 week)")
                except Exception as e:
                    logger.warning(f"Redis 写入失败，回退到内存缓存: {e}")
                    # 回退到内存缓存
                    _user_info_cache[cache_key] = parsed_info
                    logger.info(f"Cached user info to memory for user {user_id}")
            else:
                # Redis 不可用时使用内存缓存
                _user_info_cache[cache_key] = parsed_info
                logger.info(f"Cached user info to memory for user {user_id}")
            
            return parsed_info
        else:
            logger.warning(f"Failed to get user info: {result.get('resultMsg', 'Unknown error')}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
        return ""


def get_user_survey_data(user_id: str) -> str:
    """
    获取用户测评数据并返回详细解析结果
    使用 get_survey_detail 函数获取详细测评报告
    支持LLM精简内容
    """
    # 直接调用 get_survey_detail 函数获取详细测评报告
    return get_survey_detail(user_id)


def query_knowledge_base(query: str, rag_api_url: str = None, rag_api_key: str = None, rag_model: str = None) -> str:
    """
    查询知识库获取专业心理知识答案
    返回完整的回答内容，如果未找到则返回空字符串
    """
    # 默认RAG配置
    default_rag_api_url = "https://ragflow.thinnovate.com/api/v1/chats_openai/9a15923a991b11f088f40242ac170006/chat/completions"
    default_rag_api_key = "ragflow-I5ZWIyNDk0OTg3MDExZjBiZWNlMDI0Mm"
    default_rag_model = "model"
    
    # 使用传入的参数或默认值
    api_url = rag_api_url or default_rag_api_url
    api_key = rag_api_key or default_rag_api_key
    model = rag_model or default_rag_model
    
    logger.info(f"🧠 RAG工具调用开始")
    logger.info(f"📝 查询问题: {query}")
    logger.info(f"🔗 API URL: {api_url}")
    logger.info(f"🔑 API Key: {api_key[:20]}...")
    logger.info(f"🤖 Model: {model}")
    
    try:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        data = {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "stream": True
        }
        
        logger.info(f"查询知识库，问题: {query[:50]}...")
        response = requests.post(api_url, headers=headers, json=data, timeout=30, stream=True)
        response.raise_for_status()
        
        full_response = ""
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data:'):
                    try:
                        json_data = json.loads(line[5:])  # 去掉 'data:' 前缀
                        if (json_data.get('choices') and 
                            len(json_data['choices']) > 0 and 
                            json_data['choices'][0].get('delta', {}).get('content') is not None):
                            content = json_data['choices'][0]['delta']['content']
                            if content:  # 确保content不为空字符串
                                full_response += content
                    except json.JSONDecodeError as e:
                        logger.debug(f"JSON解析错误: {e}, 原始数据: {line}")
                        continue
        
        # 检查是否返回了"知识库中未找到您要的答案"
        if "知识库中未找到您要的答案" in full_response:
            logger.warning("⚠️ 知识库中未找到相关答案")
            return ""
        
        logger.info(f"✅ 知识库查询成功，返回答案长度: {len(full_response)} 字符")
        if full_response:
            logger.info(f"📄 知识库返回内容预览: {full_response[:200]}...")
        return full_response
        
    except Exception as e:
        logger.error(f"❌ 知识库查询失败: {e}")
        return ""


def get_guidance_plan(code: str) -> str:
    """
    根据测评结果的code值查询对应的指导方案
    使用 Redis 缓存避免重复请求
    """
    # 默认API URL
    api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/getBaseMindResult.do"
    
    # Redis 缓存 key 格式: code:guidance
    redis_key = f"{code}:guidance"
    
    # 尝试从 Redis 获取缓存
    redis_conn = get_redis_connection()
    if redis_conn:
        try:
            cached_data = redis_conn.get(redis_key)
            if cached_data:
                logger.debug(f"Using Redis cached guidance plan for code {code}")
                return cached_data
        except Exception as e:
            logger.warning(f"Redis 读取失败，回退到内存缓存: {e}")
    
    # 如果 Redis 不可用，回退到内存缓存
    cache_key = f"{code}_guidance"
    if cache_key in _user_info_cache:  # 复用现有的缓存字典
        logger.debug(f"Using memory cached guidance plan for code {code}")
        return _user_info_cache[cache_key]
    
    try:
        headers = {'content-type': 'application/json'}
        data = {"code": code}
        
        logger.info(f"查询指导方案，code: {code}")
        response = requests.post(api_url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        if result.get("resultCode") == 200 and "data" in result and result["data"]:
            guidance_data = result["data"][0]  # 取第一个结果
            guidance_text = guidance_data.get("value", "")
            
            # 清理文本中的 \r\n 换行符，替换为 \n
            guidance_text = guidance_text.replace('\r\n', '\n')
            
            # 优先存储到 Redis
            if redis_conn:
                try:
                    redis_conn.set(redis_key, guidance_text, ex=604800)  # 设置1周过期时间
                    logger.info(f"Cached guidance plan to Redis for code {code} (expires in 1 week)")
                except Exception as e:
                    logger.warning(f"Redis 写入失败，回退到内存缓存: {e}")
                    # 回退到内存缓存
                    _user_info_cache[cache_key] = guidance_text
                    logger.info(f"Cached guidance plan to memory for code {code}")
            else:
                # Redis 不可用时使用内存缓存
                _user_info_cache[cache_key] = guidance_text
                logger.info(f"Cached guidance plan to memory for code {code}")
            
            logger.info(f"✅ 指导方案查询成功，code: {code}, 内容长度: {len(guidance_text)} 字符")
            return guidance_text
        else:
            logger.warning(f"Failed to get guidance plan for code {code}: {result.get('resultMsg', 'Unknown error')}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching guidance plan for code {code}: {e}")
        return ""


def get_survey_detail(user_id: str) -> str:
    """
    获取用户详细测评报告
    从响应列表中抽取各个测评维度中的 name（维度名称）、resulte（测评结果 code）与 value值（详细测评信息）
    使用 Redis 缓存避免重复请求
    支持LLM精简内容
    """
    # 默认API URL
    api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/getUserResultInfo.do"
    
    # Redis 缓存 key 格式: userid:survey_detail
    redis_key = f"{user_id}:survey_detail"
    
    # 尝试从 Redis 获取缓存
    redis_conn = get_redis_connection()
    if redis_conn:
        try:
            cached_data = redis_conn.get(redis_key)
            if cached_data:
                logger.debug(f"Using Redis cached survey detail for user {user_id}")
                return cached_data
        except Exception as e:
            logger.warning(f"Redis 读取失败，回退到内存缓存: {e}")
    
    # 如果 Redis 不可用，回退到内存缓存
    cache_key = f"{user_id}_survey_detail"
    if cache_key in _user_info_cache:  # 复用现有的缓存字典
        logger.debug(f"Using memory cached survey detail for user {user_id}")
        return _user_info_cache[cache_key]
    
    try:
        headers = {'content-type': 'application/json'}
        data = {"userId": user_id}
        
        logger.info(f"查询详细测评报告，user_id: {user_id}")
        response = requests.post(api_url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        if result.get("resultCode") == 200 and "data" in result:
            data_list = result["data"]
            parsed_detail = parse_survey_detail(data_list)
            
            # 优先存储到 Redis
            if redis_conn:
                try:
                    redis_conn.set(redis_key, parsed_detail, ex=604800)  # 设置1周过期时间
                    logger.info(f"Cached survey detail to Redis for user {user_id} (expires in 1 week)")
                except Exception as e:
                    logger.warning(f"Redis 写入失败，回退到内存缓存: {e}")
                    # 回退到内存缓存
                    _user_info_cache[cache_key] = parsed_detail
                    logger.info(f"Cached survey detail to memory for user {user_id}")
            else:
                # Redis 不可用时使用内存缓存
                _user_info_cache[cache_key] = parsed_detail
                logger.info(f"Cached survey detail to memory for user {user_id}")
            
            logger.info(f"✅ 详细测评报告查询成功，user_id: {user_id}, 内容长度: {len(parsed_detail)} 字符")
            return parsed_detail
        else:
            logger.warning(f"Failed to get survey detail for user {user_id}: {result.get('resultMsg', 'Unknown error')}")
            return ""
    except Exception as e:
        logger.error(f"Error fetching survey detail for user {user_id}: {e}")
        return ""


def parse_survey_detail(data_list: list) -> str:
    """
    解析详细测评数据，提取各个维度的信息
    输出格式：维度名称: 测评结果code - 详细测评信息
    使用批量处理减少LLM调用次数
    """
    # 收集所有需要精简的内容
    items_to_process = []
    for item in data_list:
        if "name" not in item or "resulte" not in item or "value" not in item:
            continue
        items_to_process.append(item)
    
    if not items_to_process:
        return ""
    
    # 使用批量精简功能，一次性处理所有项目
    import time
    start_time = time.time()
    logger.info(f"🔄 开始批量精简 {len(items_to_process)} 个测评项目")
    simplified_values = simplify_survey_values_batch(items_to_process)
    end_time = time.time()
    logger.info(f"⏱️ 批量精简耗时: {end_time - start_time:.2f}秒")
    
    # 构建输出结果
    detail_lines = []
    for i, item in enumerate(items_to_process):
        name = item["name"]
        resulte = item["resulte"]
        
        # 获取对应的精简后内容
        if i < len(simplified_values):
            value = simplified_values[i]
        else:
            # 如果批量处理失败，回退到单个处理
            logger.warning(f"⚠️ 批量处理结果不足，回退到单个处理项目 {i+1}")
            value = simplify_survey_value(item["value"].replace('\r\n', '\n'))
        
        # 构建输出行
        detail_line = f"{name}: {resulte}\n{value}"
        detail_lines.append(detail_line)
    
    logger.info(f"✅ 批量精简完成，处理了 {len(detail_lines)} 个测评项目")
    return "\n\n".join(detail_lines)


def simplify_survey_value_with_llm(value: str) -> str:
    """
    使用LLM精简测评报告内容，提取核心信息
    直接使用硬编码的LLM客户端，支持缓存
    """
    # 检查缓存
    import hashlib
    value_hash = hashlib.md5(value.encode('utf-8')).hexdigest()
    if value_hash in _simplify_cache:
        logger.debug("🔄 使用缓存的LLM精简结果")
        return _simplify_cache[value_hash]
    
    # 检查是否启用LLM精简功能
    if not SIMPLIFY_LLM_CONFIG.get("enable_llm_simplify", True):
        logger.info("ℹ️ LLM精简功能已禁用，使用正则表达式方法")
        result = simplify_survey_value_regex(value)
        _simplify_cache[value_hash] = result
        return result
    
    # 直接使用独立的LLM客户端
    simplify_client = get_simplify_llm_client()
    if not simplify_client:
        # 如果没有可用的LLM客户端，回退到正则表达式方法
        if SIMPLIFY_LLM_CONFIG.get("fallback_to_regex", True):
            logger.warning("⚠️ 没有可用的LLM客户端，回退到正则表达式方法")
            result = simplify_survey_value_regex(value)
            _simplify_cache[value_hash] = result
            return result
        else:
            logger.error("❌ 没有可用的LLM客户端且未启用正则表达式回退")
            _simplify_cache[value_hash] = value
            return value
    
    try:
        # 构建LLM提示词
        user_prompt_template = SIMPLIFY_LLM_CONFIG.get("user_prompt_template", "")
        if user_prompt_template:
            prompt = user_prompt_template.format(value=value)
        else:
            # 使用默认提示词
            prompt = f"""请精简以下测评报告内容，只保留核心信息：

要求：
1. 移除开头的冗余描述"根据学校量表测评结果，将学生划分为健康（深蓝）、一般关注（浅蓝）、重点关注（黄色）三类，"
2. 保留标准描述（健康为...，一般关注为...，重点关注为...）
3. 保留A条（学生状态描述）
4. 对于B、C、D条，仅保留包含数字及前后内容的核心部分，如：
   - "优于学校统一样本集39.9的人群"
   - "优于中海高科数据提供单位统一样本空间27.6的人群" 
   - "劣于全国其他地区常模7.1"
   - "该学生同伴关系得分为85分"
   - "该学生抑郁情况等于全国其他地区常模"
5. 移除所有冗余的描述性文字
6. 保持原有的A.、B.、C.、D.前缀格式
7. 无数字的B、C、D条直接跳过

原始内容：
{value}

精简后的内容："""

        # 调用LLM进行精简
        response = simplify_client.chat.completions.create(
            model=SIMPLIFY_LLM_CONFIG["model"],
            messages=[
                {"role": "system", "content": SIMPLIFY_LLM_CONFIG.get("system_prompt", "你是一个专业的心理测评报告处理助手，擅长精简和提取核心信息。")},
                {"role": "user", "content": prompt}
            ],
            temperature=SIMPLIFY_LLM_CONFIG["temperature"],
            max_tokens=SIMPLIFY_LLM_CONFIG["max_tokens"]
        )
        
        # 提取精简后的内容
        simplified_content = response.choices[0].message.content.strip()
        logger.info(f"✅ LLM精简完成，原长度: {len(value)}, 精简后长度: {len(simplified_content)}")
        # 缓存结果
        _simplify_cache[value_hash] = simplified_content
        return simplified_content
        
    except Exception as e:
        logger.warning(f"⚠️ LLM精简失败，回退到正则表达式方法: {e}")
        if SIMPLIFY_LLM_CONFIG.get("fallback_to_regex", True):
            result = simplify_survey_value_regex(value)
            _simplify_cache[value_hash] = result
            return result
        else:
            _simplify_cache[value_hash] = value
            return value


def simplify_survey_value_regex(value: str) -> str:
    """
    使用正则表达式精简测评报告内容（备用方法）
    """
    # 1. 保留开头的分类说明，只移除"根据学校量表测评结果，将学生划分为"部分
    classification_pattern = r"根据学校量表测评结果，将学生划分为"
    value = re.sub(classification_pattern, "", value)
    
    # 2. 移除注解部分（注：...）
    note_pattern = r"（注：.*?）"
    value = re.sub(note_pattern, "", value)
    
    # 3. 移除"将学生划分为*三类，"内容（使用通配符匹配）
    classification_short_pattern = r"将学生划分为.*?三类，"
    value = re.sub(classification_short_pattern, "", value)
    
    # 3.1 移除单独的"将学生划分为*三类"内容（不在句子开头的情况）
    classification_standalone_pattern = r"将学生划分为.*?三类"
    value = re.sub(classification_standalone_pattern, "", value)
    
    # 4. 移除"根据学校量表测*相比较"内部内容（使用通配符匹配）
    school_test_pattern = r"根据学校量表测.*?相比较,"
    value = re.sub(school_test_pattern, "", value)
    
    # 5. 移除"在测评结果的对比上"等D条前缀
    d_prefix_patterns = [
        r"在测评结果的对比上,由于[^,]*?,因此显示为该学生的分数,",
        r"全国其他地区常模相比较,",
    ]
    for pattern in d_prefix_patterns:
        value = re.sub(pattern, "", value)
    
    # 6. 移除所有"根据学校量表测评结果"内容
    school_result_pattern = r"根据学校量表测评结果"
    value = re.sub(school_result_pattern, "", value)
    
    # 7. 处理B、C、D条：按标点符号分割，保留数字所在的完整项目
    value = simplify_bcd_items(value)
    
    # 8. 清理多余的空行和换行符
    value = re.sub(r'\n\s*\n', '\n', value)  # 移除多余空行
    value = value.strip()  # 移除首尾空白
    
    return value


def simplify_survey_value(value: str) -> str:
    """
    精简测评报告内容，优先使用LLM，回退到正则表达式
    """
    # 直接使用LLM精简功能
    return simplify_survey_value_with_llm(value)


def simplify_survey_values_batch(items_to_process: list) -> list:
    """
    批量精简多个测评报告内容，减少LLM调用次数
    返回精简后的内容列表，顺序与输入一致
    """
    if not items_to_process:
        return []
    
    # 如果只有一个项目，直接处理
    if len(items_to_process) == 1:
        item = items_to_process[0]
        value = item["value"].replace('\r\n', '\n')
        simplified_value = simplify_survey_value_with_llm(value)
        return [simplified_value]
    
    # 检查是否启用LLM精简功能
    if not SIMPLIFY_LLM_CONFIG.get("enable_llm_simplify", True):
        logger.info("ℹ️ LLM精简功能已禁用，使用正则表达式方法")
        return [simplify_survey_value_regex(item["value"].replace('\r\n', '\n')) for item in items_to_process]
    
    # 获取LLM客户端
    simplify_client = get_simplify_llm_client()
    if not simplify_client:
        # 如果没有可用的LLM客户端，回退到正则表达式方法
        if SIMPLIFY_LLM_CONFIG.get("fallback_to_regex", True):
            logger.warning("⚠️ 没有可用的LLM客户端，回退到正则表达式方法")
            return [simplify_survey_value_regex(item["value"].replace('\r\n', '\n')) for item in items_to_process]
        else:
            logger.error("❌ 没有可用的LLM客户端且未启用正则表达式回退")
            return [item["value"].replace('\r\n', '\n') for item in items_to_process]
    
    # 构建批量处理的提示词
    batch_content = []
    for i, item in enumerate(items_to_process):
        name = item["name"]
        value = item["value"].replace('\r\n', '\n')
        batch_content.append(f"【项目{i+1}】{name}:\n{value}")
    
    combined_content = "\n\n".join(batch_content)
    
    # 检查缓存
    import hashlib
    content_hash = hashlib.md5(combined_content.encode('utf-8')).hexdigest()
    cache_key = f"batch_{content_hash}"
    if cache_key in _simplify_cache:
        logger.debug("🔄 使用缓存的批量LLM精简结果")
        return _simplify_cache[cache_key]
    
    try:
        # 构建LLM提示词
        user_prompt_template = SIMPLIFY_LLM_CONFIG.get("user_prompt_template", "")
        if user_prompt_template:
            prompt = user_prompt_template.format(value=combined_content)
        else:
            # 使用默认提示词
            prompt = f"""请精简以下多个测评报告内容，只保留核心信息：

要求：
1. 移除开头的冗余描述"根据学校量表测评结果，将学生划分为健康（深蓝）、一般关注（浅蓝）、重点关注（黄色）三类，"
2. 保留标准描述（健康为...，一般关注为...，重点关注为...）
3. 保留A条（学生状态描述）
4. 对于B、C、D条，仅保留包含数字及前后内容的核心部分，如：
   - "优于学校统一样本集39.9的人群"
   - "优于中海高科数据提供单位统一样本空间27.6的人群" 
   - "劣于全国其他地区常模7.1"
   - "该学生同伴关系得分为85分"
   - "该学生抑郁情况等于全国其他地区常模"
5. 移除所有冗余的描述性文字
6. 保持原有的A.、B.、C.、D.前缀格式
7. 无数字的B、C、D条直接跳过

**重要：请按照【项目1】、【项目2】等格式分别处理每个项目，并在每个项目之间用【项目分隔符】分隔。**

原始内容：
{combined_content}

精简后的内容："""

        # 调用LLM进行批量精简
        response = simplify_client.chat.completions.create(
            model=SIMPLIFY_LLM_CONFIG["model"],
            messages=[
                {"role": "system", "content": SIMPLIFY_LLM_CONFIG.get("system_prompt", "你是一个专业的心理测评报告处理助手，擅长精简和提取核心信息。")},
                {"role": "user", "content": prompt}
            ],
            temperature=SIMPLIFY_LLM_CONFIG["temperature"],
            max_tokens=SIMPLIFY_LLM_CONFIG["max_tokens"]
        )
        
        # 提取精简后的内容
        simplified_content = response.choices[0].message.content.strip()
        logger.info(f"✅ 批量LLM精简完成，原长度: {len(combined_content)}, 精简后长度: {len(simplified_content)}")
        
        # 按项目分隔符分割结果
        if "【项目分隔符】" in simplified_content:
            results = simplified_content.split("【项目分隔符】")
        else:
            # 如果没有分隔符，尝试按项目编号分割
            import re
            project_pattern = r'【项目\d+】'
            results = re.split(project_pattern, simplified_content)
            results = [r.strip() for r in results if r.strip()]
        
        # 确保结果数量与输入一致
        if len(results) != len(items_to_process):
            logger.warning(f"⚠️ 批量精简结果数量不匹配，期望{len(items_to_process)}个，实际{len(results)}个")
            # 如果数量不匹配，回退到逐个处理
            return [simplify_survey_value_with_llm(item["value"].replace('\r\n', '\n')) for item in items_to_process]
        
        # 缓存结果
        _simplify_cache[cache_key] = results
        return results
        
    except Exception as e:
        logger.warning(f"⚠️ 批量LLM精简失败，回退到逐个处理: {e}")
        if SIMPLIFY_LLM_CONFIG.get("fallback_to_regex", True):
            return [simplify_survey_value_regex(item["value"].replace('\r\n', '\n')) for item in items_to_process]
        else:
            return [item["value"].replace('\r\n', '\n') for item in items_to_process]


def simplify_bcd_items(value: str) -> str:
    """
    简化B、C、D条内容，仅保留包含数字的核心部分
    无数字的B、C、D条直接跳过
    """
    # 先处理行内的B、C、D项
    # 移除B条无数字内容（如"B.社会用户暂无学校对比。"）
    b_no_number_pattern = r'B\.社会用户暂无学校对比。'
    value = re.sub(b_no_number_pattern, '', value)
    
    lines = value.split('\n')
    result_lines = []
    
    for line in lines:
        # 检查是否是B、C或D项
        if re.match(r'^[BCD]\.', line.strip()):
            # 提取包含数字的核心内容
            number_content = extract_number_item(line)
            if number_content:
                # 保持原有的B.、C.或D.前缀
                prefix = re.match(r'^[BCD]\.', line.strip()).group()
                result_lines.append(f"{prefix} {number_content}")
            # 无数字的B、C、D条直接跳过
        else:
            result_lines.append(line)
    
    return '\n'.join(result_lines)


def extract_number_item(text: str) -> str:
    """
    提取BCD项目中包含数字的核心部分
    仅保留如"优于学校统一样本集39.9的人群"、"该学生同伴关系得分为85分"等核心内容
    """
    # 移除B.、C.或D.前缀
    text = re.sub(r'^[BCD]\.\s*', '', text.strip())
    
    # 定义数字模式，匹配数字及其直接上下文，按优先级排序
    number_patterns = [
        # 得分为模式：如"该学生同伴关系得分为85分"、"该生数字划销的得分为16分"
        r'([^，,。！？；]*?得分为\d+\.?\d*分[^，,。！？；]*)',
        # 优于+数字模式：如"优于学校统一样本集39.9的人群"、"优于中海高科数据提供单位统一样本空间27.6的人群"
        r'(优于[^，,。！？；]*?\d+\.?\d*[%分]?[^，,。！？；]*)',
        # 劣于+数字模式：如"劣于全国其他地区常模7.1"
        r'(劣于[^，,。！？；]*?\d+\.?\d*[%分]?[^，,。！？；]*)',
        # 百分比+人群模式：如"39.9%的人群"、"27.6%的人群"
        r'(\d+\.?\d*%的人群)',
        # 百分比+常模模式：如"48.6%的常模"
        r'(\d+\.?\d*%的常模)',
        # 百分比+样本空间模式：如"87.8%的样本空间"
        r'(\d+\.?\d*%的样本空间)',
    ]
    
    # 按优先级匹配，找到第一个匹配的模式就返回
    for pattern in number_patterns:
        matches = re.findall(pattern, text)
        if matches:
            # 去重并保持顺序
            unique_matches = []
            seen = set()
            for match in matches:
                match = match.strip()
                if match and match not in seen:
                    unique_matches.append(match)
                    seen.add(match)
            
            if unique_matches:
                # 用逗号连接多条得分
                result = '，'.join(unique_matches)
                # 清理多余的空格
                result = re.sub(r'\s+', ' ', result)
                return result
    
    # 如果没有找到匹配模式，返回空字符串（跳过该项）
    return ""




def get_guidance_by_dimension(user_id: str, dimension_name: str) -> str:
    """
    根据测评维度名称获取对应的指导方案
    先获取用户的详细测评报告，找到对应维度的code值，然后获取指导方案
    支持LLM精简内容
    """
    logger.info(f"🔍 根据维度获取指导方案，用户ID: {user_id}, 维度: {dimension_name}")
    
    # 先获取用户的详细测评报告
    survey_detail = get_survey_detail(user_id)
    if not survey_detail:
        logger.warning(f"无法获取用户 {user_id} 的测评报告")
        return ""
    
    # 从测评报告中提取对应维度的code值
    code = extract_code_by_dimension(survey_detail, dimension_name)
    if not code:
        logger.warning(f"未找到维度 '{dimension_name}' 对应的code值")
        return f"抱歉，未找到您关于'{dimension_name}'的测评结果，无法提供针对性指导。"
    
    logger.info(f"找到维度 '{dimension_name}' 对应的code值: {code}")
    
    # 使用code值获取指导方案
    guidance_plan = get_guidance_plan(code)
    if not guidance_plan:
        logger.warning(f"无法获取code '{code}' 对应的指导方案")
        return f"抱歉，无法获取关于'{dimension_name}'的指导方案。"
    
    logger.info(f"✅ 成功获取维度 '{dimension_name}' 的指导方案，内容长度: {len(guidance_plan)} 字符")
    return guidance_plan


def extract_code_by_dimension(survey_detail: str, dimension_name: str) -> str:
    """
    从详细测评报告中提取指定维度对应的code值
    格式：维度名称: code值\n详细测评信息
    """
    lines = survey_detail.split('\n')
    
    for i, line in enumerate(lines):
        # 查找包含维度名称的行
        if dimension_name in line and ':' in line:
            # 提取code值（格式：维度名称: code值）
            parts = line.split(':')
            if len(parts) >= 2:
                code = parts[1].strip()
                # 如果code值包含换行符，只取第一行（即code值本身）
                if '\n' in code:
                    code = code.split('\n')[0].strip()
                # 验证code格式（如 1-1-A, 1-2-A, 1-5-C等）
                if '-' in code and len(code) >= 5:
                    logger.info(f"从测评报告中提取到code值: {code}")
                    return code
    
    # 如果直接匹配失败，尝试模糊匹配
    dimension_mapping = {
        '学习焦虑': '学习焦虑',
        '状态焦虑': '状态焦虑', 
        '抑郁': '抑郁',
        '同伴关系': '同伴关系',
        '师生关系': '师生关系',
        '亲子关系': '亲子关系',
        '自我效能感': '自我效能感',
        '计算能力': '计算能力',
        '注意能力': '注意能力',
        '识字能力': '识字能力',
        '流畅能力': '流畅能力'
    }
    
    # 尝试模糊匹配
    for key, value in dimension_mapping.items():
        if key in dimension_name or dimension_name in key:
            for line in lines:
                if value in line and ':' in line:
                    parts = line.split(':')
                    if len(parts) >= 2:
                        code = parts[1].strip()
                        # 如果code值包含换行符，只取第一行（即code值本身）
                        if '\n' in code:
                            code = code.split('\n')[0].strip()
                        if '-' in code and len(code) >= 5:
                            logger.info(f"通过模糊匹配找到code值: {code}")
                            return code
    
    logger.warning(f"未找到维度 '{dimension_name}' 对应的code值")
    return ""


# 模块加载时自动加载配置文件
load_simplify_llm_config()