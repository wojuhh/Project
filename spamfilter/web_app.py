# web_app.py
# 垃圾邮件过滤系统的Web应用模块
# 提供邮件发送客户端和管理后台的Web界面功能

# 导入Flask框架和相关组件
from flask import Flask, request, jsonify, render_template, redirect, url_for, Response
import smtplib  # SMTP邮件发送客户端
from email.message import EmailMessage  # 邮件消息构建工具
import math  # 数学计算，用于分页
import codecs  # 编码解码工具
import urllib.parse  # URL解析和编码工具

# 导入自定义模块
import database  # 数据库操作模块
import ai_service  # AI服务模块
from config import (
    WEB_APP_HOST, WEB_APP_PORT, WEB_APP_DEBUG,  # Web应用配置
    SMTP_HOST, SMTP_PORT, SMTP_TIMEOUT,  # SMTP服务器配置
    INBOX_PER_PAGE, LOG_FILE_PATH, LOG_VIEWER_MAX_LINES  # 页面和日志配置
)

# 创建Flask应用实例
app = Flask(__name__)


# ====================================
# --- 辅助函数 ---
# ====================================

def robust_decode(s):
    """
    健壮的Unicode解码函数，处理邮件中的Unicode转义字符。
    
    Args:
        s (str): 待解码的字符串
        
    Returns:
        str: 解码后的字符串，失败时返回原字符串
        
    Note:
        专门处理形如 \\u4e2d\\u6587 的Unicode转义序列
        只有当字符串包含 '\\u' 时才尝试解码，避免性能损失
    """
    if not isinstance(s, str):
        return s  # 非字符串类型直接返回

    try:
        # 仅当字符串中包含 '\u' 时才尝试解码
        if '\\u' in s:
            return codecs.decode(s, 'unicode_escape')
    except Exception:
        # 解码失败则返回原字符串，确保程序不会崩溃
        pass
    return s


def get_pagination_range(current_page, total_pages, neighbors=2):
    """
    生成智能分页范围，避免显示过多页码。
    
    Args:
        current_page (int): 当前页码
        total_pages (int): 总页数
        neighbors (int): 当前页前后显示的页码数量，默认2
        
    Returns:
        list: 页码列表，包含数字和省略号('...')
        
    Note:
        当总页数较少时显示所有页码
        当总页数较多时显示首页、尾页和当前页附近的页码
        使用省略号('...')表示跳过的页码范围
    """
    # 总页数较少时，显示所有页码
    if total_pages <= 2 * neighbors + 5:
        return list(range(1, total_pages + 1))

    # 总页数较多时，使用智能分页
    pages = [1]  # 始终显示第一页

    # 计算当前页前后的显示范围
    start = max(2, current_page - neighbors)
    if start > 2:
        pages.append('...')  # 添加省略号

    end = min(total_pages - 1, current_page + neighbors)
    pages.extend(range(start, end + 1))  # 添加当前页附近的页码

    # 如果与最后一页之间有间隔，添加省略号
    if end < total_pages - 1:
        pages.append('...')
    pages.append(total_pages)  # 始终显示最后一页

    return pages


# ====================================
# --- 邮件客户端路由 ---
# ====================================

@app.route('/')
def client_page():
    """
    显示邮件发送客户端页面。
    
    Returns:
        str: 渲染后的HTML页面
        
    Note:
        这是系统的主页，提供邮件发送功能
    """
    return render_template('client.html')


@app.route('/send-email', methods=['POST'])
def send_email():
    """
    处理邮件发送请求的API端点。
    
    Returns:
        Response: JSON格式的响应
            - 成功: {'success': True}
            - 失败: {'success': False, 'error': 错误信息}
            
    Note:
        接收JSON格式的邮件数据，通过SMTP服务器发送
        会将邮件发送到本地SMTP服务器进行过滤处理
    """
    data = request.get_json()  # 获取JSON数据

    # 构建邮件消息对象
    msg = EmailMessage()
    msg['From'] = data.get('from')  # 发件人
    msg['To'] = data.get('to')  # 收件人
    msg['Subject'] = data.get('subject')  # 主题
    msg.set_content(data.get('text'))  # 正文内容

    try:
        # 连接到本地SMTP服务器发送邮件
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.send_message(msg)
        return jsonify({'success': True})
    except Exception as e:
        # 处理发送失败的情况
        error_message = str(e)

        # 特殊处理SMTP异常，提取更详细的错误信息
        if isinstance(e, smtplib.SMTPException) and hasattr(e, 'smtp_error'):
            smtp_err = getattr(e, 'smtp_error')
            error_message = smtp_err.decode('utf-8', 'replace') if isinstance(smtp_err, bytes) else str(smtp_err)

        return jsonify({'success': False, 'error': error_message}), 500


# ====================================
# --- 管理后台路由 ---
# ====================================

@app.route('/admin')
def admin_redirect():
    """
    管理后台默认页面重定向。
    
    Returns:
        Response: 重定向到收件箱页面
        
    Note:
        简化管理员访问，直接重定向到最常用的收件箱页面
    """
    return redirect(url_for('admin_inbox'))


@app.route('/admin/inbox')
def admin_inbox():
    """
    显示收件箱管理页面，支持分页浏览。
    
    Returns:
        str: 渲染后的收件箱HTML页面
        
    Note:
        - 支持分页显示邮件列表
        - 对邮件内容进行Unicode解码处理
        - 生成智能分页导航
    """
    # 获取当前页码，默认为第1页
    page = request.args.get('page', 1, type=int)

    # 计算分页参数
    total_mails = database.count_total_mails()  # 总邮件数
    total_pages = math.ceil(total_mails / INBOX_PER_PAGE)  # 总页数
    offset = (page - 1) * INBOX_PER_PAGE  # 数据库查询偏移量

    # 从数据库获取当前页的邮件数据
    raw_mails = database.get_inbox(limit=INBOX_PER_PAGE, offset=offset)

    # 对邮件数据进行Unicode解码处理
    mails = [[robust_decode(item) if isinstance(item, str) else item for item in row] for row in raw_mails]

    # 生成分页导航范围
    pagination_range = get_pagination_range(page, total_pages)

    return render_template('inbox.html',
                           mails=mails,
                           current_page=page,
                           total_pages=total_pages,
                           pagination_range=pagination_range)


@app.route('/admin/mark/<int:mail_id>/<int:label>')
def mark_mail(mail_id, label):
    """
    标记邮件为垃圾邮件或正常邮件。
    
    Args:
        mail_id (int): 邮件ID
        label (int): 标签值 (0=正常邮件, 1=垃圾邮件)
        
    Returns:
        Response: 重定向回收件箱页面
        
    Note:
        用于手动修正AI预测结果或重新分类邮件
        保持当前页码以改善用户体验
    """
    database.update_label(mail_id, label)

    # 获取当前页码，操作后返回相同页面
    page = request.args.get('page', 1, type=int)
    return redirect(url_for('admin_inbox', page=page))


@app.route('/admin/delete/<int:mail_id>')
def delete_mail(mail_id):
    """
    删除指定邮件。
    
    Args:
        mail_id (int): 要删除的邮件ID
        
    Returns:
        Response: 重定向回收件箱页面
        
    Note:
        物理删除操作，不可恢复
        保持当前页码以改善用户体验
    """
    database.delete_mail_by_id(mail_id)

    # 获取当前页码，操作后返回相同页面
    page = request.args.get('page', 1, type=int)
    return redirect(url_for('admin_inbox', page=page))


@app.route('/admin/mail/<int:mail_id>')
def view_mail(mail_id):
    """
    显示单封邮件的详细内容。
    
    Args:
        mail_id (int): 邮件ID
        
    Returns:
        str: 渲染后的邮件详情HTML页面
        Response: 404错误（邮件不存在时）
        
    Note:
        提供邮件完整内容的查看功能
        支持返回按钮回到正确的列表页面
    """
    # 从查询参数获取页码，以便"返回"按钮能回到正确页面
    page = request.args.get('page', 1, type=int)

    # 从数据库获取邮件数据
    mail_row = database.get_mail_by_id(mail_id)

    # 如果邮件不存在，返回404错误
    if not mail_row:
        return "邮件未找到", 404

    # 将数据库行对象转换为字典，并对可能包含转义字符的字段进行解码
    mail_data = {
        'id': mail_row['id'],
        'from': robust_decode(mail_row['mail_from']),
        'to': robust_decode(mail_row['rcpt_to']),
        'subject': robust_decode(mail_row['subject']),
        'body': robust_decode(mail_row['body']),
        'label': mail_row['label'],
        'timestamp': mail_row['ts']
    }

    return render_template('mail_detail.html', mail=mail_data, page=page)


@app.route('/admin/extract-keywords', methods=['POST'])
def extract_keywords():
    """
    从选中的邮件中提取垃圾邮件关键词的API端点。
    
    Returns:
        Response: JSON格式的响应
            - 成功: 包含提取的关键词列表和处理统计
            - 失败: 包含错误信息
            
    Note:
        使用AI服务分析选中邮件，提取垃圾邮件特征关键词
        提取的关键词会自动添加到关键词过滤列表中
    """
    # 获取选中的邮件ID列表
    mail_ids = request.json.get('mail_ids', [])
    if not mail_ids:
        return jsonify({'success': False, 'error': "未选择任何邮件。"}), 400

    # 从数据库获取邮件内容
    mails_to_process = database.get_mails_by_ids(mail_ids)
    if not mails_to_process:
        return jsonify({'success': False, 'error': "未能找到选中的邮件内容。"}), 404

    try:
        # 调用AI服务提取关键词
        keywords, failed_ids = ai_service.extract_keywords_from_mails(mails_to_process)

        # 将提取的关键词添加到数据库
        newly_added_keywords = []
        if keywords:
            for keyword in keywords:
                database.add_to_list('keywords', keyword, 'keyword')
                newly_added_keywords.append(keyword)

        # 返回处理结果
        return jsonify({
            'success': True,
            'extracted_keywords': sorted(list(newly_added_keywords)),  # 排序后的关键词列表
            'failed_mail_ids': failed_ids,  # 处理失败的邮件ID
            'total_processed': len(mails_to_process)  # 总处理数量
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/lists', methods=['GET', 'POST'])
def admin_lists():
    """
    显示和管理黑白名单及关键词列表。
    
    Returns:
        str: 渲染后的列表管理HTML页面（GET请求）
        Response: 重定向到列表页面（POST请求）
        
    Note:
        - GET: 显示当前的黑名单、白名单和关键词列表
        - POST: 处理添加新条目的请求
        - 支持三种类型的条目添加：黑名单邮箱、白名单邮箱、关键词
    """
    if request.method == 'POST':
        # 处理表单提交，添加新条目

        # 处理黑名单添加
        if 'black_add' in request.form:
            email_to_add = request.form['black_add'].strip().lower()
            if email_to_add:  # 服务器端验证：非空检查
                database.add_to_list('blacklist', email_to_add, 'email')

        # 处理白名单添加
        if 'white_add' in request.form:
            email_to_add = request.form['white_add'].strip().lower()
            if email_to_add:  # 服务器端验证：非空检查
                database.add_to_list('whitelist', email_to_add, 'email')

        # 处理关键词添加
        if 'keyword_add' in request.form:
            keyword_to_add = request.form['keyword_add'].strip().lower()
            if keyword_to_add:  # 服务器端验证：非空检查
                database.add_to_list('keywords', keyword_to_add, 'keyword')

        # 添加完成后重定向，避免重复提交
        return redirect(url_for('admin_lists'))

    # GET请求：显示当前列表
    black = sorted(list(database.get_list('blacklist', 'email')))  # 黑名单（排序）
    white = sorted(list(database.get_list('whitelist', 'email')))  # 白名单（排序）
    keywords = sorted(list(database.get_list('keywords', 'keyword')))  # 关键词（排序）

    return render_template('lists.html', black=black, white=white, keywords=keywords)


@app.route('/admin/remove/<table>/<path:value>')
def remove_from_list(table, value):
    """
    从指定列表中移除条目。
    
    Args:
        table (str): 表名 (blacklist/whitelist/keywords)
        value (str): 要移除的值（URL编码）
        
    Returns:
        Response: 重定向到列表管理页面
        
    Note:
        支持从黑名单、白名单、关键词列表中删除条目
        URL参数会被自动解码以处理特殊字符
    """
    # URL解码处理特殊字符
    decoded_value = urllib.parse.unquote(value)

    # 表名与列名的映射关系
    column_map = {
        'blacklist': 'email',
        'whitelist': 'email',
        'keywords': 'keyword'
    }

    # 验证表名并执行删除操作
    if table in column_map:
        database.remove_from_list(table, decoded_value, column_map[table])

    return redirect(url_for('admin_lists'))


@app.route('/admin/logs')
def admin_logs():
    """
    显示系统日志页面。
    
    Returns:
        str: 渲染后的日志查看HTML页面
        
    Note:
        显示日志文件的最后N行内容，用于系统监控和故障排查
        处理文件不存在或读取失败的情况
    """
    # 默认错误信息
    log_content = f'日志文件不存在: {LOG_FILE_PATH}'

    try:
        # 读取日志文件
        with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
            log_lines = f.readlines()
            # 只显示最后N行，避免页面过大
            log_content = "".join(log_lines[-LOG_VIEWER_MAX_LINES:])
    except FileNotFoundError:
        # 文件不存在，使用默认消息
        pass
    except Exception as e:
        # 其他读取错误
        log_content = f"读取日志文件失败: {e}"

    return render_template('logs.html', log_content=log_content)


# ====================================
# --- 程序入口点 ---
# ====================================

if __name__ == '__main__':
    """
    程序主入口点。
    
    启动Flask Web应用服务器，提供邮件客户端和管理后台功能。
    启动前确保数据库已正确初始化。
    """
    # 确保在启动 Web 应用时数据库和表已创建
    database.init_db()

    # 启动Flask开发服务器
    app.run(host=WEB_APP_HOST, port=WEB_APP_PORT, debug=WEB_APP_DEBUG)
