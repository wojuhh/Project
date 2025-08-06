# ai_service.py
# 垃圾邮件过滤系统的AI服务模块
# 提供异步邮件垃圾邮件预测和关键词提取功能

import aiohttp  # 异步HTTP客户端
import requests  # 同步HTTP客户端
import asyncio  # 异步编程支持
import re  # 正则表达式模块
import logging  # 日志记录模块
from config import SILICONFLOW_API_KEY, AI_API_URL, AI_MODEL_NAME  # 导入配置信息

# 创建日志记录器
log = logging.getLogger('ai_service')


async def predict_is_spam(subject, body):
    """
    通过调用 SiliconFlow API 异步预测邮件是否为垃圾邮件。
    
    Args:
        subject (str): 邮件主题
        body (str): 邮件正文内容
        
    Returns:
        int: 1 表示垃圾邮件，0 表示正常邮件
        
    Note:
        - 使用异步方式处理，避免阻塞主线程
        - 设置15秒超时限制
        - 发生错误时默认返回0（安全策略）
    """
    # 检查API密钥是否正确配置
    if not SILICONFLOW_API_KEY or "YOUR" in SILICONFLOW_API_KEY:
        log.error("AI_SERVICE: SILICONFLOW_API_KEY 未配置，无法进行AI预测。")
        return 0  # 默认安全，判定为正常邮件

    # 构造发送给AI的提示消息
    user_message = (
        "请判断以下邮件是否为垃圾邮件。如果判断为垃圾邮件，请回复 '1'；如果判断为正常邮件，请回复 '0'。\n"
        "您只能回复 '1' 或 '0'，不要包含任何其他文字或解释。\n\n"
        f"Subject: {subject}\nBody: {body}"
    )
    
    # 设置HTTP请求头，包含认证信息
    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 构造API请求载荷
    payload = {
        "model": AI_MODEL_NAME,  # 指定使用的AI模型
        "messages": [
            {"role": "system", "content": "你是一个严格的邮件分类助手，只回复 '1' 或 '0'。"},
            {"role": "user", "content": user_message}
        ],
        "thinking_budget": 1,  # 思考预算限制
        "max_tokens": 5,  # 最大返回token数，只需要返回'1'或'0'
        "temperature": 0.0  # 设置为0确保结果确定性
    }

    try:
        # 创建异步HTTP会话，设置15秒总超时
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            # 发送POST请求到AI API
            async with session.post(AI_API_URL, json=payload, headers=headers) as response:
                response.raise_for_status()  # 检查HTTP状态码
                result = await response.json()  # 解析JSON响应
                
                # 提取AI返回的内容
                content = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                log.info(f"AI_SERVICE: 原始回复: '{content}'")
                
                # 根据AI回复返回相应结果
                return 1 if content == '1' else 0
    except Exception as e:
        # 记录错误并返回安全默认值
        log.error(f"AI_SERVICE: 预测时发生错误: {e}")
        return 0  # 发生任何错误时，默认安全


def extract_keywords_from_mails(mails_data):
    """
    从邮件内容中同步提取垃圾邮件关键词。
    
    Args:
        mails_data (list): 邮件数据列表，格式为 [(id, subject, body), ...]
        
    Returns:
        tuple: 包含 (all_keywords, failed_ids) 的元组
            - all_keywords (set): 提取到的所有关键词集合
            - failed_ids (list): 处理失败的邮件ID列表
            
    Raises:
        ValueError: 当API密钥未配置时抛出异常
        
    Note:
        - 使用同步方式处理，适合批量处理场景
        - 对每封邮件提取2-5个最重要的垃圾邮件关键词
        - 自动过滤无意义的通用词汇
    """
    # 检查API密钥配置
    if not SILICONFLOW_API_KEY or "YOUR" in SILICONFLOW_API_KEY:
        raise ValueError("AI_SERVICE: SILICONFLOW_API_KEY 未配置，无法提取关键词。")

    # 初始化结果集合和失败ID列表
    all_extracted_keywords = set()  # 使用set避免重复关键词
    failed_mail_ids = []

    # 遍历每封邮件进行关键词提取
    for mail_id, subject, body in mails_data:
        try:
            # 构造关键词提取的提示消息
            user_message = (
                "请从以下邮件内容中提取所有与垃圾邮件相关的关键词，例如：'免费彩票', '中大奖', '投资机会', '高额回报'等。请提取2到5个最重要的关键词。\n"
                "请以逗号分隔的列表形式返回关键词，不要包含任何其他文字或解释。\n"
                "如果邮件内容中没有发现任何明确的垃圾邮件关键词，请回复 '无'。\n\n"
                f"Subject: {subject or ''}\nBody: {body or ''}"
            )
            
            # 设置HTTP请求头
            headers = {
                "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                "Content-Type": "application/json"
            }
            
            # 构造API请求载荷
            payload = {
                "model": AI_MODEL_NAME,
                "messages": [
                    {"role": "system",
                     "content": "你是一个专业的垃圾邮件关键词提取助手，只回复逗号分隔的关键词列表或 '无'。"},
                    {"role": "user", "content": user_message}
                ],
                "thinking_budget": 1,
                "max_tokens": 100,  # 允许更多token以返回关键词列表
                "temperature": 0.2  # 略微增加随机性以获得更好的关键词多样性
            }

            # 发送同步HTTP请求，设置60秒超时
            response = requests.post(AI_API_URL, json=payload, headers=headers, timeout=60)
            response.raise_for_status()  # 检查HTTP状态码
            result = response.json()  # 解析JSON响应
            
            # 提取AI返回的关键词内容
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

            # 处理返回的关键词
            if content and content.lower() != '无':
                # 使用正则表达式分割关键词（支持中英文逗号和空格）
                keywords_raw = re.split(r'[,，\s]+', content)
                
                # 清理和过滤关键词
                for kw in keywords_raw:
                    kw_cleaned = kw.strip().lower()  # 去除空格并转换为小写
                    # 过滤条件：非空、长度大于1、不是常见无意义词汇
                    if kw_cleaned and len(kw_cleaned) > 1 and kw_cleaned not in {'邮件', '主题', '正文', '内容', '无'}:
                        all_extracted_keywords.add(kw_cleaned)
                        
        except Exception as e:
            # 记录处理失败的邮件
            log.error(f"AI_SERVICE: 提取关键词时发生错误 (Mail ID: {mail_id}): {e}")
            failed_mail_ids.append(mail_id)

    # 返回提取结果
    return all_extracted_keywords, failed_mail_ids
