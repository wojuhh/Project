# database.py
# 垃圾邮件过滤系统数据库操作模块
# 提供SQLite数据库的初始化、CRUD操作和邮件管理功能

import sqlite3  # SQLite数据库操作模块
from config import DB_PATH  # 导入数据库文件路径配置


def init_db():
    """
    初始化数据库和所有表。
    
    创建系统所需的四个核心数据表：
    - blacklist: 黑名单邮箱地址
    - whitelist: 白名单邮箱地址  
    - keywords: 垃圾邮件关键词
    - inbox: 邮件收件箱
    
    Note:
        使用 IF NOT EXISTS 确保表只在不存在时创建
        使用上下文管理器自动处理数据库连接的关闭
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        
        # 创建黑名单表 - 存储被屏蔽的邮箱地址
        c.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
                email TEXT UNIQUE NOT NULL             -- 邮箱地址，唯一且非空
            )'''
                  )
        
        # 创建白名单表 - 存储信任的邮箱地址
        c.execute('''
            CREATE TABLE IF NOT EXISTS whitelist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
                email TEXT UNIQUE NOT NULL             -- 邮箱地址，唯一且非空
            )'''
                  )
        
        # 创建关键词表 - 存储垃圾邮件特征关键词
        c.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
                keyword TEXT UNIQUE NOT NULL           -- 关键词，唯一且非空
            )'''
                  )
        
        # 创建收件箱表 - 存储接收到的邮件
        c.execute('''
            CREATE TABLE IF NOT EXISTS inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,           -- 邮件唯一ID
                mail_from TEXT,                                 -- 发件人邮箱
                rcpt_to TEXT,                                   -- 收件人邮箱
                subject TEXT,                                   -- 邮件主题
                body TEXT,                                      -- 邮件正文
                label INTEGER,                                  -- 邮件标签 (0=正常, 1=垃圾邮件)
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP         -- 接收时间戳
            )
        '''
                  )
        conn.commit()  # 提交所有表创建操作


def get_list(table, column):
    """
    从指定表中获取一列数据。
    
    Args:
        table (str): 表名
        column (str): 列名
        
    Returns:
        set: 包含所有值的集合，自动去重
        
    Note:
        返回set类型便于快速查找和去重操作
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(f'SELECT {column} FROM {table}')
        # 使用集合推导式将查询结果转换为set，自动去重
        return set(row[0] for row in c.fetchall())


def add_to_list(table, value, column):
    """
    向指定表中添加一个值，忽略已存在的情况。
    
    Args:
        table (str): 表名
        value (str): 要添加的值
        column (str): 列名
        
    Note:
        使用参数化查询防止SQL注入
        捕获IntegrityError异常处理重复值情况
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        try:
            # 使用参数化查询，防止SQL注入攻击
            c.execute(f'INSERT INTO {table} ({column}) VALUES (?)', (value,))
            conn.commit()
        except sqlite3.IntegrityError:
            # 值已存在（违反UNIQUE约束），忽略错误
            pass


def remove_from_list(table, value, column):
    """
    从指定表中删除一个值。
    
    Args:
        table (str): 表名
        value (str): 要删除的值
        column (str): 列名
        
    Note:
        使用参数化查询防止SQL注入
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # 使用参数化查询，防止SQL注入攻击
        c.execute(f'DELETE FROM {table} WHERE {column}=?', (value,))
        conn.commit()


def save_mail(mail_from, rcpt_to, subject, body, label=None):
    """
    保存一封邮件到inbox表，可以指定标签。
    
    Args:
        mail_from (str): 发件人邮箱地址
        rcpt_to (str): 收件人邮箱地址
        subject (str): 邮件主题
        body (str): 邮件正文内容
        label (int, optional): 邮件标签 (0=正常邮件, 1=垃圾邮件, None=未分类)
        
    Note:
        时间戳字段由数据库自动填入当前时间
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO inbox (mail_from, rcpt_to, subject, body, label)
            VALUES (?, ?, ?, ?, ?)
        ''', (mail_from, rcpt_to, subject, body, label))
        conn.commit()


def get_inbox(limit=50, offset=0):
    """
    获取收件箱邮件列表，按ID降序排列。
    
    Args:
        limit (int): 每页显示的邮件数量，默认50
        offset (int): 跳过的邮件数量，用于分页，默认0
        
    Returns:
        list: 邮件列表，每个元素为 (id, mail_from, subject, body, label, ts) 元组
        
    Note:
        按ID降序排列确保最新邮件显示在前面
        字段顺序必须与前端预期一致
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # 确保 subject 在 body 之前，以匹配前端索引预期
        c.execute('SELECT id, mail_from, subject, body, label, ts FROM inbox ORDER BY id DESC LIMIT ? OFFSET ?',
                  (limit, offset))
        return c.fetchall()


def update_label(mail_id, label):
    """
    更新指定邮件的标签。
    
    Args:
        mail_id (int): 邮件ID
        label (int): 新的标签值 (0=正常邮件, 1=垃圾邮件)
        
    Note:
        用于手动修正邮件分类或AI预测结果
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('UPDATE inbox SET label=? WHERE id=?', (label, mail_id))
        conn.commit()


def delete_mail_by_id(mail_id):
    """
    根据ID删除邮件。
    
    Args:
        mail_id (int): 要删除的邮件ID
        
    Note:
        物理删除操作，不可恢复
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('DELETE FROM inbox WHERE id = ?', (mail_id,))
        conn.commit()


def count_total_mails():
    """
    统计收件箱中的邮件总数。
    
    Returns:
        int: 邮件总数
        
    Note:
        用于分页功能计算总页数
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM inbox')
        total = c.fetchone()[0]  # fetchone()返回元组，取第一个元素
        return total


def get_mails_by_ids(mail_ids):
    """
    根据邮件ID列表获取邮件的ID、主题和正文。
    
    Args:
        mail_ids (list): 邮件ID列表
        
    Returns:
        list: 邮件数据列表，每个元素为 (id, subject, body) 元组
        
    Note:
        主要用于AI关键词提取功能
        包含SQL注入防护，过滤非法ID
    """
    if not mail_ids:
        return []

    # 过滤并验证ID，确保都是有效的数字，防止SQL注入
    clean_ids = [int(mid) for mid in mail_ids if isinstance(mid, (int, str)) and str(mid).isdigit()]
    if not clean_ids:
        return []

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # 动态生成占位符，确保参数化查询安全
        placeholders = ','.join('?' * len(clean_ids))
        c.execute(f'SELECT id, subject, body FROM inbox WHERE id IN ({placeholders})', tuple(clean_ids))
        return c.fetchall()


def get_mail_by_id(mail_id):
    """
    根据ID获取单封邮件的完整信息。
    
    Args:
        mail_id (int): 邮件ID
        
    Returns:
        sqlite3.Row or None: 邮件完整信息的Row对象，未找到时返回None
        
    Note:
        使用sqlite3.Row工厂函数，支持通过列名访问字段
        适用于需要邮件完整信息的场景，如邮件详情页面
    """
    with sqlite3.connect(DB_PATH) as conn:
        # 使用 sqlite3.Row 可以让我们通过列名访问数据，更方便
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM inbox WHERE id = ?', (mail_id,))
        # fetchone() 返回单条记录或在未找到时返回 None
        return c.fetchone()
