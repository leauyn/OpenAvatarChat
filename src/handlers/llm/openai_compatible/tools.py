
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
            "description": "当你想查询指定用户的测评数据时非常有用。",
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

def get_user_info(user_id: str="1234567890") -> str:
    """
    获取用户信息并返回简化的解析结果
    """
    test_user_info = "姓名：张三，性别：男，年龄：18，地址：北京市海淀区，学校：北京大学"

    return test_user_info

def get_user_survey_data(user_id: str="1234567890") -> str:
    """
    获取用户测评数据并返回简化的解析结果
    """
    test_user_survey_data = "重点关注：注意力不集中、情绪管理，一般关注：社交能力，健康：身体健康、心理健康"

    return test_user_survey_data