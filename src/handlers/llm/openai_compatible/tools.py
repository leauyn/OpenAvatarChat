import requests
import json
import redis
from loguru import logger

# 全局缓存，避免重复请求
_survey_data_cache = {}
_user_info_cache = {}

# Redis 连接配置
REDIS_HOST = 'localhost'
REDIS_PORT = 6779
REDIS_DB = 0

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
    """
    detail_lines = []
    
    for item in data_list:
        if "name" not in item or "resulte" not in item or "value" not in item:
            continue
            
        name = item["name"]
        resulte = item["resulte"]
        value = item["value"]
        
        # 清理文本中的 \r\n 换行符，替换为 \n
        value = value.replace('\r\n', '\n')
        
        # 构建输出行
        detail_line = f"{name}: {resulte}\n{value}"
        detail_lines.append(detail_line)
    
    return "\n\n".join(detail_lines)