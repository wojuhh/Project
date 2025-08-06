# smtp_server.py
# 垃圾邮件过滤系统的SMTP服务器模块
# 提供异步SMTP服务器，实现邮件接收、解析和智能过滤功能

import asyncio  # 异步编程支持
import logging  # 日志记录模块
import codecs  # 编码解码工具
import re  # 正则表达式模块
from email.parser import BytesParser  # 邮件解析器
from email.policy import default  # 邮件解析策略
from aiosmtpd.controller import Controller  # 异步SMTP服务器控制器

# 导入自定义模块
import database  # 数据库操作模块
from ai_service import predict_is_spam  # AI垃圾邮件预测服务
from config import SMTP_HOST, SMTP_PORT, LOG_FILE_PATH  # 配置信息

# ====================================
# --- 日志配置 ---
# ====================================

# 配置日志系统，同时输出到文件和控制台
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE_PATH, encoding='utf-8'),  # 文件输出
                        logging.StreamHandler()  # 控制台输出
                    ])
log = logging.getLogger('smtp_server')  # 创建模块专用日志记录器


# ====================================
# --- 辅助函数 ---
# ====================================

def force_unicode(s):
    """
    强制将字符串转换为正确的Unicode格式。
    
    Args:
        s (str): 待处理的字符串
        
    Returns:
        str: 正确解码的Unicode字符串
        
    Note:
        处理邮件中可能出现的Unicode转义序列
        例如：\\u4e2d\\u6587 -> 中文
    """
    if not isinstance(s, str):
        return s  # 非字符串直接返回

    # 检查是否为Unicode转义序列格式
    if re.fullmatch(r'(\\u[0-9a-fA-F]{4})+', s):
        try:
            # 尝试解码Unicode转义序列
            return codecs.decode(s, 'unicode_escape')
        except Exception:
            # 解码失败时返回原字符串
            return s
    return s


def parse_subject_body(raw_message_bytes):
    """
    从原始邮件字节流中解析主题和正文内容。
    
    Args:
        raw_message_bytes (bytes): 原始邮件字节数据
        
    Returns:
        tuple: (subject, body) 元组，包含解析后的主题和正文
        
    Note:
        - 支持多部分邮件（multipart）和单部分邮件
        - 优先提取text/plain部分作为正文
        - 处理各种字符编码，出错时使用替换字符
    """
    try:
        # 使用BytesParser解析邮件，采用默认策略
        msg = BytesParser(policy=default).parsebytes(raw_message_bytes)
        subject = str(msg['subject'])  # 提取邮件主题

        # 处理多部分邮件（如包含附件、HTML等）
        if msg.is_multipart():
            # 遍历所有邮件部分
            for part in msg.walk():
                # 查找纯文本部分
                if part.get_content_type() == 'text/plain':
                    # 解码邮件正文，处理字符编码
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8',
                        errors='replace'  # 遇到无法解码的字符时用替换字符
                    )
                    return subject, force_unicode(body)
            return subject, ''  # 没有找到text/plain部分时返回空正文
        else:
            # 处理单部分邮件
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or 'utf-8',
                errors='replace'
            )
            return subject, force_unicode(body)
    except Exception as e:
        # 邮件解析失败时的错误处理
        log.error(f"解析邮件失败: {e}. 原始消息 (前200字节): {raw_message_bytes[:200]}")
        # 返回错误标识和部分原始内容
        return "解析错误", raw_message_bytes[:1024].decode('utf-8', errors='replace')


# ====================================
# --- SMTP 处理器类 ---
# ====================================

class SpamFilterHandler:
    """
    垃圾邮件过滤SMTP处理器。
    
    实现aiosmtpd要求的处理器接口，负责处理接收到的邮件数据。
    按照以下顺序进行过滤：
    1. 白名单检查（优先级最高）
    2. 黑名单检查
    3. 关键词过滤
    4. AI模型预测
    """

    async def handle_DATA(self, server, session, envelope):
        """
        处理邮件数据的核心方法。
        
        Args:
            server: SMTP服务器实例
            session: SMTP会话信息
            envelope: 邮件信封，包含发件人、收件人和邮件内容
            
        Returns:
            str: SMTP响应码和消息
                - 250: 邮件接受
                - 550: 邮件拒绝
                
        Note:
            该方法实现了完整的垃圾邮件过滤流程
        """
        # 提取邮件基本信息
        mail_from = envelope.mail_from  # 发件人邮箱
        rcpt_tos = envelope.rcpt_tos  # 收件人列表
        raw_message_bytes = envelope.content  # 原始邮件内容

        log.info(f"收到邮件: From <{mail_from}> To <{rcpt_tos}>")

        # 解析邮件主题和正文
        subject, body = parse_subject_body(raw_message_bytes)

        # 第一步：检查发件人是否在白名单中
        if mail_from in database.get_list('whitelist', 'email'):
            log.info(f"结果: ALLOWED (Whitelist) - From: {mail_from}")
            # 白名单邮件直接保存为正常邮件
            database.save_mail(mail_from, ','.join(rcpt_tos), subject, body, label=0)
            return '250 Message accepted (whitelisted)'

        # 第二步：检查发件人是否在黑名单中
        if mail_from in database.get_list('blacklist', 'email'):
            log.warning(f"结果: BLOCKED (Blacklist) - From: {mail_from}")
            # 黑名单邮件也保存，标记为垃圾邮件，用于后续分析
            database.save_mail(mail_from, ','.join(rcpt_tos), subject, body, label=1)
            return '550 Message blocked (sender blacklisted)'

        # 第三步：检查邮件内容是否包含垃圾邮件关键词
        lower_content = (subject + ' ' + body).lower()  # 转换为小写便于匹配
        keywords = database.get_list('keywords', 'keyword')  # 获取关键词列表
        for kw in keywords:
            if kw.lower() in lower_content:
                log.warning(f"结果: BLOCKED (Keyword: {kw}) - From: {mail_from}")
                database.save_mail(mail_from, ','.join(rcpt_tos), subject, body, label=1)
                return f'550 Message blocked (spam keyword: {kw})'

        # 第四步：使用AI模型进行垃圾邮件预测
        prediction_result = await predict_is_spam(subject, body)
        if prediction_result == 1:  # AI判定为垃圾邮件
            log.warning(f"结果: BLOCKED (AI Model) - From: {mail_from}")
            database.save_mail(mail_from, ','.join(rcpt_tos), subject, body, label=1)
            return '550 Message blocked (classified as spam by AI)'

        # 所有检查都通过，接受邮件
        log.info(f"结果: ALLOWED (Passed all checks) - From: {mail_from}")
        database.save_mail(mail_from, ','.join(rcpt_tos), subject, body, label=0)
        return '250 Message accepted for delivery'


# ====================================
# --- 主程序入口 ---
# ====================================

async def amain(hostname, port):
    """
    异步主函数，负责启动和管理SMTP服务器。
    
    Args:
        hostname (str): 服务器监听的主机地址
        port (int): 服务器监听的端口号
        
    Note:
        - 启动前初始化数据库
        - 使用无限循环保持服务器运行
        - 优雅处理关闭信号
    """
    # 启动时确保数据库已初始化
    database.init_db()

    # 创建SMTP服务器控制器
    controller = Controller(SpamFilterHandler(), hostname=hostname, port=port)
    controller.start()  # 启动服务器
    log.info(f"SMTP 服务器已在 {hostname}:{port} 启动...")

    try:
        # 主循环：每小时检查一次，保持服务器运行
        while True:
            await asyncio.sleep(3600)  # 休眠1小时
    except asyncio.CancelledError:
        # 接收到取消信号时优雅关闭
        log.info("SMTP 服务器正在关闭...")
        controller.stop()


# ====================================
# --- 程序入口点 ---
# ====================================

if __name__ == '__main__':
    """
    程序主入口点。
    
    从配置文件读取主机和端口信息，启动SMTP服务器。
    支持Ctrl+C优雅退出。
    """
    try:
        # 使用asyncio.run启动异步主函数
        asyncio.run(amain(hostname=SMTP_HOST, port=SMTP_PORT))
    except KeyboardInterrupt:
        # 用户按Ctrl+C时优雅退出
        log.info("用户中断，程序退出。")
