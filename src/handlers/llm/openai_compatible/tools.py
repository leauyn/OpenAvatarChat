import requests
import json
from loguru import logger

# 全局缓存，避免重复请求
_survey_data_cache = {}
_user_info_cache = {}

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
    使用缓存避免重复请求
    """
    # 默认API URL
    api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/userInfo.do"
    
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


def get_user_survey_data(user_id: str) -> str:
    """
    获取用户测评数据并返回简化的解析结果
    使用缓存避免重复请求
    """
    # 默认API URL
    api_url = "https://www.zhgk-mind.com/api/dwsurvey/anon/response/getUserResultInfo.do"
    
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