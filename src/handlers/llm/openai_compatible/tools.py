import requests
import json
import redis
import re
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
        
        # 精简内容：移除冗余信息
        value = simplify_survey_value(value)
        
        # 构建输出行
        detail_line = f"{name}: {resulte}\n{value}"
        detail_lines.append(detail_line)
    
    return "\n\n".join(detail_lines)


def simplify_survey_value(value: str) -> str:
    """
    精简测评报告内容，移除冗余信息
    """
    # 1. 移除开头的分类说明
    classification_pattern = r"根据学校量表测评结果，将学生划分为健康（深蓝）、一般关注（浅蓝）、重点关注（黄色）三类，.*?。\r?\n"
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


def simplify_bcd_items(value: str) -> str:
    """
    简化B、C、D条内容，按标点符号分割，保留数字所在的完整项目
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
            # 检查是否包含数字
            if has_number(line):
                # 按标点符号分割，保留数字所在的完整项目
                number_content = extract_number_item(line)
                if number_content:
                    # 保持原有的B.、C.或D.前缀
                    prefix = re.match(r'^[BCD]\.', line.strip()).group()
                    result_lines.append(f"{prefix} {number_content}")
                else:
                    # 如果提取失败，保留原行
                    result_lines.append(line)
            else:
                # 无数字的B、C、D条直接跳过
                continue
        else:
            result_lines.append(line)
    
    return '\n'.join(result_lines)


def has_number(text: str) -> bool:
    """
    检查文本是否包含数字（包括百分比、分数等）
    """
    # 检查是否包含数字（包括百分比、分数等）
    return bool(re.search(r'\d+\.?\d*[%分]?', text))


def extract_number_item(text: str) -> str:
    """
    按标点符号分割B、C、D条内容，保留数字所在的完整项目
    支持全角和半角逗号作为分割符
    """
    # 移除B.、C.或D.前缀
    text = re.sub(r'^[BCD]\.\s*', '', text.strip())
    
    # 按标点符号分割成项目列表，保留分割符
    # 使用多种标点符号作为分割符：。！？；，,（全角和半角逗号）
    items = re.split(r'([。！？；，,])', text)
    
    # 重新组合项目，每个项目包含其标点符号
    combined_items = []
    for i in range(0, len(items), 2):
        if i + 1 < len(items):
            item = items[i].strip() + items[i + 1]
            if item.strip():
                combined_items.append(item.strip())
        elif items[i].strip():
            combined_items.append(items[i].strip())
    
    # 查找包含数字的项目
    for item in combined_items:
        # 检查项目是否包含数字（包括百分比、分数等）
        if re.search(r'\d+\.?\d*[%分]?', item):
            return item
    
    # 如果没有找到包含数字的项目，返回原文本
    return text


def extract_number_content(text: str) -> str:
    """
    提取含有数字的部分内容
    根据图片示例，提取类似"30.7%的人群"、"27.6%的人群"、"12.2%"这样的内容
    """
    # 移除B.、C.或D.前缀
    text = re.sub(r'^[BCD]\.\s*', '', text.strip())
    
    # 查找含有数字和百分比的模式
    # 匹配模式：数字% + 可选的人群/常模等词汇
    number_patterns = [
        r'(\d+\.?\d*%[^。！？；，]*?[人群常模样本空间])',  # 数字% + 人群/常模等
        r'(\d+\.?\d*%[^。！？；，]*)',  # 数字% + 其他内容
        r'(\d+\.?\d*[^。！？；，]*?%)',  # 数字 + 其他内容 + %
    ]
    
    for pattern in number_patterns:
        matches = re.findall(pattern, text)
        if matches:
            # 返回第一个匹配的内容
            result = matches[0].strip()
            # 确保"人群"等词汇完整
            if result.endswith('人') and '人群' in text:
                result = result + '群'
            return result
    
    # 如果没有找到百分比，查找其他数字模式
    simple_number_pattern = r'(\d+\.?\d*[^。！？；，]*)'
    matches = re.findall(simple_number_pattern, text)
    if matches:
        return matches[0].strip()
    
    return text


def extract_last_sentence(text: str) -> str:
    """
    提取文本中的最后一句话（以标点符号为分割）
    """
    # 移除C.或D.前缀
    text = re.sub(r'^[CD]\.\s*', '', text.strip())
    
    # 按标点符号分割句子，保留分隔符
    sentences = re.split(r'([。！？；，])', text)
    
    # 重新组合句子，每个句子包含其标点符号
    combined_sentences = []
    for i in range(0, len(sentences), 2):
        if i + 1 < len(sentences):
            sentence = sentences[i].strip() + sentences[i + 1]
            if sentence.strip():
                combined_sentences.append(sentence.strip())
        elif sentences[i].strip():
            combined_sentences.append(sentences[i].strip())
    
    # 返回最后一句话
    if combined_sentences:
        return combined_sentences[-1]
    else:
        return text


def get_guidance_by_dimension(user_id: str, dimension_name: str) -> str:
    """
    根据测评维度名称获取对应的指导方案
    先获取用户的详细测评报告，找到对应维度的code值，然后获取指导方案
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
    """
    lines = survey_detail.split('\n')
    
    for i, line in enumerate(lines):
        # 查找包含维度名称的行
        if dimension_name in line and ':' in line:
            # 提取code值（格式：维度名称: code值）
            parts = line.split(':')
            if len(parts) >= 2:
                code = parts[1].strip()
                # 验证code格式（如 1-5-C）
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
                        if '-' in code and len(code) >= 5:
                            logger.info(f"通过模糊匹配找到code值: {code}")
                            return code
    
    logger.warning(f"未找到维度 '{dimension_name}' 对应的code值")
    return ""