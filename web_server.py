#!/usr/bin/env python3
"""
现代化Web聊天服务器
支持用户注册登录、好友系统、群聊、消息存储、离线消息
使用WebSocket实现实时通信
"""

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from threading import Lock
import json
import time
from datetime import datetime
import sqlite3
import hashlib
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chat-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# 数据库初始化
def init_db():
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    # 用户表
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 username TEXT UNIQUE NOT NULL,
                 password_hash TEXT NOT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                 )''')
    
    # 好友关系表
    c.execute('''CREATE TABLE IF NOT EXISTS friendships (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 user1_id INTEGER NOT NULL,
                 user2_id INTEGER NOT NULL,
                 status INTEGER DEFAULT 0, -- 0: 待接受, 1: 已接受
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (user1_id) REFERENCES users(id),
                 FOREIGN KEY (user2_id) REFERENCES users(id),
                 UNIQUE(user1_id, user2_id)
                 )''')
    
    # 群组表
    c.execute('''CREATE TABLE IF NOT EXISTS groups (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 group_name TEXT NOT NULL,
                 creator_id INTEGER NOT NULL,
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (creator_id) REFERENCES users(id)
                 )''')
    
    # 群组成员表
    c.execute('''CREATE TABLE IF NOT EXISTS group_members (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 group_id INTEGER NOT NULL,
                 user_id INTEGER NOT NULL,
                 role TEXT DEFAULT 'member', -- 'admin', 'member'
                 joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (group_id) REFERENCES groups(id),
                 FOREIGN KEY (user_id) REFERENCES users(id),
                 UNIQUE(group_id, user_id)
                 )''')
    
    # 聊天消息表
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 sender_id INTEGER NOT NULL,
                 receiver_id INTEGER,  -- NULL表示私聊消息的目标
                 group_id INTEGER,     -- 群组ID，用于群聊
                 message TEXT NOT NULL,
                 message_type TEXT DEFAULT 'group', -- 'group', 'private', 'group_chat'
                 timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (sender_id) REFERENCES users(id),
                 FOREIGN KEY (receiver_id) REFERENCES users(id),
                 FOREIGN KEY (group_id) REFERENCES groups(id)
                 )''')
    
    # 离线消息表
    c.execute('''CREATE TABLE IF NOT EXISTS offline_messages (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 receiver_id INTEGER NOT NULL,
                 sender_id INTEGER NOT NULL,
                 message TEXT NOT NULL,
                 message_type TEXT DEFAULT 'private',
                 timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (receiver_id) REFERENCES users(id),
                 FOREIGN KEY (sender_id) REFERENCES users(id)
                 )''')
    
    # 好友请求表
    c.execute('''CREATE TABLE IF NOT EXISTS friend_requests (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 sender_id INTEGER NOT NULL,
                 receiver_id INTEGER NOT NULL,
                 message TEXT,
                 status TEXT DEFAULT 'pending', -- 'pending', 'accepted', 'rejected'
                 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (sender_id) REFERENCES users(id),
                 FOREIGN KEY (receiver_id) REFERENCES users(id)
                 )''')
    
    conn.commit()
    conn.close()

def hash_password(password):
    """简单的密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_username(username):
    """根据用户名获取用户信息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    conn.close()
    return user

def get_user_by_id(user_id):
    """根据ID获取用户信息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def get_user_friends(user_id):
    """获取用户的好友列表"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    # 修复SQL查询，正确处理双向好友关系
    c.execute("""
        SELECT DISTINCT u.id, u.username 
        FROM users u
        INNER JOIN friendships f ON (
            (u.id = f.user1_id AND f.user2_id = ?) OR 
            (u.id = f.user2_id AND f.user1_id = ?)
        )
        WHERE f.status = 1
    """, (user_id, user_id))
    friends = c.fetchall()
    conn.close()
    return friends

def get_user_groups(user_id):
    """获取用户所属的群组"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("""
        SELECT g.id, g.group_name, g.creator_id
        FROM groups g
        INNER JOIN group_members gm ON g.id = gm.group_id
        WHERE gm.user_id = ?
    """, (user_id,))
    groups = c.fetchall()
    conn.close()
    return groups

def get_all_users(exclude_user_id=None):
    """获取所有注册用户"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    if exclude_user_id:
        c.execute("SELECT id, username FROM users WHERE id != ?", (exclude_user_id,))
    else:
        c.execute("SELECT id, username FROM users")
    users = c.fetchall()
    conn.close()
    return users

def store_message(sender_id, receiver_id, group_id, message, message_type):
    """存储消息到数据库"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    # 根据消息类型确定存储方式
    if message_type == 'group':
        # 群聊消息（全局群聊）
        c.execute("INSERT INTO messages (sender_id, receiver_id, group_id, message, message_type) VALUES (?, ?, ?, ?, ?)",
                  (sender_id, None, None, message, 'group'))
    elif message_type == 'private':
        # 私聊消息
        c.execute("INSERT INTO messages (sender_id, receiver_id, group_id, message, message_type) VALUES (?, ?, ?, ?, ?)",
                  (sender_id, receiver_id, None, message, 'private'))
    elif message_type == 'group_chat':
        # 群组消息
        c.execute("INSERT INTO messages (sender_id, receiver_id, group_id, message, message_type) VALUES (?, ?, ?, ?, ?)",
                  (sender_id, None, group_id, message, 'group_chat'))
    
    conn.commit()
    
    # 只保留最新的100条消息（按类型分别保留）
    if message_type == 'group':
        c.execute("DELETE FROM messages WHERE message_type = 'group' AND id NOT IN (SELECT id FROM messages WHERE message_type = 'group' ORDER BY timestamp DESC LIMIT 100)")
    elif message_type == 'private':
        # 私聊消息保留更多
        pass
    elif message_type == 'group_chat':
        # 群组消息按群组保留
        if group_id:
            c.execute("DELETE FROM messages WHERE group_id = ? AND message_type = 'group_chat' AND id NOT IN (SELECT id FROM messages WHERE group_id = ? AND message_type = 'group_chat' ORDER BY timestamp DESC LIMIT 100)", (group_id, group_id))
    
    conn.commit()
    conn.close()

def get_recent_messages(count=50, user_id=None, other_user_id=None, group_id=None):
    """获取最近的消息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    if user_id and other_user_id:
        # 获取与特定用户的私聊消息
        c.execute("""
            SELECT u.username, m.message, m.timestamp, m.message_type, m.sender_id
            FROM messages m
            INNER JOIN users u ON m.sender_id = u.id
            WHERE (m.sender_id = ? AND m.receiver_id = ?) OR (m.sender_id = ? AND m.receiver_id = ?)
            ORDER BY m.timestamp ASC
            LIMIT ?
        """, (user_id, other_user_id, other_user_id, user_id, count))
    elif group_id:
        # 获取群组消息
        c.execute("""
            SELECT u.username, m.message, m.timestamp, m.message_type, m.sender_id
            FROM messages m
            INNER JOIN users u ON m.sender_id = u.id
            WHERE m.group_id = ?
            ORDER BY m.timestamp ASC
            LIMIT ?
        """, (group_id, count))
    else:
        # 获取全局群聊消息（按时间顺序）
        c.execute("""
            SELECT u.username, m.message, m.timestamp, m.message_type, m.sender_id
            FROM messages m
            INNER JOIN users u ON m.sender_id = u.id
            WHERE m.message_type = 'group'
            ORDER BY m.timestamp ASC
            LIMIT ?
        """, (count,))
    
    messages = c.fetchall()
    conn.close()
    return messages

def get_recent_private_messages(user_id, other_user_id, count=50):
    """获取与特定用户的私聊消息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("""
        SELECT u.username, m.message, m.timestamp, m.message_type, m.sender_id
        FROM messages m
        INNER JOIN users u ON m.sender_id = u.id
        WHERE ((m.sender_id = ? AND m.receiver_id = ?) OR (m.sender_id = ? AND m.receiver_id = ?)) 
        AND m.message_type = 'private'
        ORDER BY m.timestamp ASC
        LIMIT ?
    """, (user_id, other_user_id, other_user_id, user_id, count))
    messages = c.fetchall()
    conn.close()
    return messages

def get_recent_group_messages(group_id, count=50):
    """获取群组消息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("""
        SELECT u.username, m.message, m.timestamp, m.message_type, m.sender_id
        FROM messages m
        INNER JOIN users u ON m.sender_id = u.id
        WHERE m.group_id = ? AND m.message_type = 'group_chat'
        ORDER BY m.timestamp ASC
        LIMIT ?
    """, (group_id, count))
    messages = c.fetchall()
    conn.close()
    return messages

def store_offline_message(receiver_id, sender_id, message, message_type):
    """存储离线消息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("INSERT INTO offline_messages (receiver_id, sender_id, message, message_type) VALUES (?, ?, ?, ?)",
              (receiver_id, sender_id, message, message_type))
    conn.commit()
    conn.close()

def get_and_clear_offline_messages(user_id):
    """获取并清除用户的离线消息"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("SELECT u.username, om.message, om.timestamp FROM offline_messages om INNER JOIN users u ON om.sender_id = u.id WHERE om.receiver_id = ? ORDER BY om.timestamp ASC", (user_id,))
    offline_msgs = c.fetchall()
    
    # 删除已获取的离线消息
    c.execute("DELETE FROM offline_messages WHERE receiver_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return offline_msgs

def get_pending_friend_requests(user_id):
    """获取待处理的好友请求"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("""
        SELECT u.username, fr.message, fr.id, u.id as sender_id
        FROM friend_requests fr
        INNER JOIN users u ON fr.sender_id = u.id
        WHERE fr.receiver_id = ? AND fr.status = 'pending'
    """, (user_id,))
    requests = c.fetchall()
    conn.close()
    return requests

def get_group_members(group_id):
    """获取群组成员"""
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("""
        SELECT u.id, u.username
        FROM users u
        INNER JOIN group_members gm ON u.id = gm.user_id
        WHERE gm.group_id = ?
    """, (group_id,))
    members = c.fetchall()
    conn.close()
    return members

# 存储在线用户信息 (session_id -> user_info)
online_users = {}

def get_online_user_info():
    """安全地获取当前在线用户信息"""
    try:
        return online_users.get(request.sid)
    except:
        # 在某些上下文中 request 可能不可用
        return None

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    print(f"客户端连接: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    try:
        user_info = online_users.get(request.sid)
    except:
        return
    
    if user_info:
        username = user_info['username']
        user_id = user_info['user_id']
        
        # 从在线用户列表中移除
        if request.sid in online_users:
            del online_users[request.sid]
        
        # 广播用户下线消息
        emit('system_message', f'用户 {username} 已离开聊天室', broadcast=True)
        emit('update_users', list(online_users.values()), broadcast=True)

@socketio.on('register')
def handle_register(data):
    """处理用户注册"""
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        emit('registration_error', '用户名和密码不能为空')
        return
    
    if len(username) < 3 or len(username) > 20:
        emit('registration_error', '用户名长度应在3-20字符之间')
        return
    
    if len(password) < 6:
        emit('registration_error', '密码长度至少6位')
        return
    
    password_hash = hash_password(password)
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    try:
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        user_id = c.lastrowid
        conn.commit()
        emit('registration_success', {'message': '注册成功，请登录', 'username': username})
    except sqlite3.IntegrityError:
        emit('registration_error', '用户名已存在，请选择其他用户名')
    finally:
        conn.close()

@socketio.on('login')
def handle_login(data):
    """处理用户登录"""
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        emit('login_error', '用户名和密码不能为空')
        return
    
    password_hash = hash_password(password)
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE username = ? AND password_hash = ?", (username, password_hash))
    user = c.fetchone()
    conn.close()
    
    if user:
        # 检查用户名是否已在其他地方登录
        for session_id, user_info in online_users.items():
            if user_info['user_id'] == user[0]:
                emit('login_error', '此账户已在别处登录')
                return
        
        # 保存用户信息到在线用户列表
        online_users[request.sid] = {
            'user_id': user[0],
            'username': user[1],
            'sid': request.sid,
            'join_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        user_id = user[0]
        username = user[1]
        
        # 获取好友列表
        friends_list = get_user_friends(user_id)
        
        # 获取群组列表
        groups_list = get_user_groups(user_id)
        
        # 发送登录成功消息
        emit('login_success', {
            'username': username,
            'user_id': user_id,
            'friends': [{'id': f[0], 'username': f[1]} for f in friends_list],
            'groups': [{'id': g[0], 'group_name': g[1], 'creator_id': g[2]} for g in groups_list]
        })
        
        # 发送全局群聊历史消息（最新的50条）
        recent_group_messages = get_recent_messages(50, group_id=None)
        for msg in recent_group_messages:  # 按时间顺序显示
            formatted_msg = f"[{msg[2][:19]}] {msg[0]}: {msg[1]}"
            is_self = (msg[4] == user_id)
            emit('group_message', {
                'message': formatted_msg,
                'sender': msg[0],
                'type': 'group',
                'is_self': is_self
            })
        
        # 发送离线消息
        offline_messages = get_and_clear_offline_messages(user_id)
        for msg in offline_messages:
            formatted_msg = f"[{msg[2][:19]}] [离线消息] {msg[0]} -> 你: {msg[1]}"
            emit('private_message', {
                'message': formatted_msg,
                'sender': msg[0],
                'type': 'private',
                'is_self': False
            })
        
        # 获取所有注册用户（用于添加好友）
        all_users = get_all_users(exclude_user_id=user_id)
        emit('all_users_list', [{'id': u[0], 'username': u[1]} for u in all_users])
        
        # 获取待处理的好友请求
        pending_requests = get_pending_friend_requests(user_id)
        if pending_requests:
            emit('friend_requests', [{'sender': r[0], 'sender_id': r[3], 'message': r[1], 'request_id': r[2]} for r in pending_requests])
        
        # 广播用户上线消息
        emit('system_message', f'用户 {username} 加入了聊天室', broadcast=True)
        emit('update_users', list(online_users.values()), broadcast=True)
    else:
        emit('login_error', '用户名或密码错误')

@socketio.on('get_friends')
def handle_get_friends():
    """获取好友列表"""
    user_info = online_users.get(request.sid)
    if user_info:
        friends = get_user_friends(user_info['user_id'])
        emit('update_friends', [{'id': f[0], 'username': f[1]} for f in friends])

@socketio.on('get_groups')
def handle_get_groups():
    """获取群组列表"""
    user_info = online_users.get(request.sid)
    if user_info:
        groups = get_user_groups(user_info['user_id'])
        emit('update_groups', [{'id': g[0], 'group_name': g[1], 'creator_id': g[2]} for g in groups])

@socketio.on('get_all_users')
def handle_get_all_users():
    """获取所有注册用户"""
    current_user_id = online_users.get(request.sid, {}).get('user_id', 0)
    all_users = get_all_users(exclude_user_id=current_user_id)
    emit('all_users_list', [{'id': u[0], 'username': u[1]} for u in all_users])

@socketio.on('get_private_chat_history')
def handle_get_private_chat_history(data):
    """获取与特定用户的私聊历史"""
    current_user_info = online_users.get(request.sid)
    if not current_user_info:
        return
    
    other_username = data.get('other_username')
    other_user = get_user_by_username(other_username)
    
    if not other_user:
        return
    
    # 获取与该用户的私聊历史
    messages = get_recent_private_messages(current_user_info['user_id'], other_user[0])
    
    for msg in messages:
        is_self = (msg[4] == current_user_info['user_id'])
        if is_self:
            # 当前用户发送的消息
            formatted_msg = f"[{msg[2][:19]}] 你 -> {other_username}: {msg[1]}"
            emit('private_message', {
                'message': formatted_msg,
                'sender': current_user_info['username'],
                'target': other_username,
                'type': 'private',
                'is_self': True
            })
        else:
            # 对方发送的消息
            formatted_msg = f"[{msg[2][:19]}] {msg[0]} -> 你: {msg[1]}"
            emit('private_message', {
                'message': formatted_msg,
                'sender': msg[0],
                'target': other_username,
                'type': 'private',
                'is_self': False
            })

@socketio.on('get_group_chat_history')
def handle_get_group_chat_history(data):
    """获取群组聊天历史"""
    current_user_info = online_users.get(request.sid)
    if not current_user_info:
        return
    
    group_id = data.get('group_id')
    if not group_id:
        return
    
    # 获取群组消息
    messages = get_recent_group_messages(group_id)
    
    for msg in messages:
        is_self = (msg[4] == current_user_info['user_id'])
        formatted_msg = f"[{msg[2][:19]}] {msg[0]}: {msg[1]}"
        emit('group_chat_message', {
            'message': formatted_msg,
            'sender': msg[0],
            'group_id': group_id,
            'type': 'group_chat',
            'is_self': is_self
        })

@socketio.on('create_group')
def handle_create_group(data):
    """创建群组"""
    creator_info = online_users.get(request.sid)
    if not creator_info:
        return
    
    group_name = data.get('group_name', '').strip()
    member_ids = data.get('member_ids', [])
    
    if not group_name:
        emit('error_message', '群组名称不能为空')
        return
    
    if not member_ids:
        emit('error_message', '请选择至少一个成员')
        return
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    try:
        # 创建群组
        c.execute("INSERT INTO groups (group_name, creator_id) VALUES (?, ?)", 
                  (group_name, creator_info['user_id']))
        group_id = c.lastrowid
        
        # 添加创建者为管理员
        c.execute("INSERT INTO group_members (group_id, user_id, role) VALUES (?, ?, 'admin')", 
                  (group_id, creator_info['user_id']))
        
        # 添加其他成员
        for member_id in member_ids:
            c.execute("INSERT INTO group_members (group_id, user_id) VALUES (?, ?)", 
                      (group_id, member_id))
        
        conn.commit()
        
        # 获取群组成员信息
        members = get_group_members(group_id)
        member_usernames = [m[1] for m in members]
        
        # 通知创建者
        emit('group_created', {
            'message': f'群组 "{group_name}" 创建成功！',
            'group': {
                'id': group_id,
                'group_name': group_name,
                'members': member_usernames
            }
        })
        
        # 更新创建者的群组列表
        groups = get_user_groups(creator_info['user_id'])
        emit('update_groups', [{'id': g[0], 'group_name': g[1], 'creator_id': g[2]} for g in groups])
        
        # 通知群组成员
        for member in members:
            member_sid = None
            for sid, user_info in online_users.items():
                if user_info['user_id'] == member[0]:
                    member_sid = sid
                    break
            
            if member_sid:
                emit('group_invitation', {
                    'group_name': group_name,
                    'creator': creator_info['username'],
                    'members': member_usernames,
                    'group_id': group_id
                }, room=member_sid)
                
                # 同时更新成员的群组列表
                member_groups = get_user_groups(member[0])
                emit('update_groups', [{'id': g[0], 'group_name': g[1], 'creator_id': g[2]} for g in member_groups], room=member_sid)
                
    except Exception as e:
        conn.rollback()
        emit('error_message', f'创建群组失败: {str(e)}')
    finally:
        conn.close()

@socketio.on('send_friend_request')
def handle_send_friend_request(data):
    """发送好友请求"""
    sender_info = online_users.get(request.sid)
    if not sender_info:
        return
    
    receiver_username = data.get('receiver_username', '').strip()
    message = data.get('message', '')
    
    if not receiver_username:
        emit('error_message', '请选择要添加的好友')
        return
    
    receiver_user = get_user_by_username(receiver_username)
    if not receiver_user:
        emit('error_message', f'用户 {receiver_username} 不存在')
        return
    
    sender_id = sender_info['user_id']
    receiver_id = receiver_user[0]
    
    if sender_id == receiver_id:
        emit('error_message', '不能添加自己为好友')
        return
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    try:
        # 检查是否已经是好友
        c.execute("""
            SELECT 1 FROM friendships 
            WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)) 
            AND status = 1
        """, (sender_id, receiver_id, receiver_id, sender_id))
        is_friend = c.fetchone()
        
        if is_friend:
            emit('error_message', f'您已经和 {receiver_username} 是好友了')
            return
        
        # 检查是否已经发送过请求
        c.execute("""
            SELECT 1 FROM friend_requests 
            WHERE sender_id = ? AND receiver_id = ? AND status = 'pending'
        """, (sender_id, receiver_id))
        existing_request = c.fetchone()
        
        if existing_request:
            emit('error_message', f'您已经向 {receiver_username} 发送过好友请求了')
            return
        
        # 发送好友请求
        c.execute("INSERT INTO friend_requests (sender_id, receiver_id, message) VALUES (?, ?, ?)", 
                  (sender_id, receiver_id, message))
        request_id = c.lastrowid
        conn.commit()
        
        emit('friend_request_sent', {
            'message': f'已向 {receiver_username} 发送好友请求',
            'receiver': receiver_username
        })
        
        # 通知接收方
        receiver_sid = None
        for sid, user_info in online_users.items():
            if user_info['user_id'] == receiver_id:
                receiver_sid = sid
                break
        
        if receiver_sid:
            emit('new_friend_request', {
                'sender': sender_info['username'],
                'sender_id': sender_id,
                'message': message,
                'request_id': request_id
            }, room=receiver_sid)
            
    except Exception as e:
        conn.rollback()
        emit('error_message', f'发送好友请求失败: {str(e)}')
    finally:
        conn.close()

@socketio.on('accept_friend_request')
def handle_accept_friend_request(data):
    """接受好友请求"""
    # 使用 request.sid 之前需要确保它可用
    try:
        receiver_info = online_users.get(request.sid)
    except:
        # 如果 request 不可用，尝试从上下文获取
        from flask import request as flask_request
        receiver_info = online_users.get(flask_request.sid)
    
    if not receiver_info:
        return
    
    request_id = data.get('request_id')
    if not request_id:
        return
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    try:
        # 获取请求信息
        c.execute("SELECT sender_id, receiver_id FROM friend_requests WHERE id = ? AND status = 'pending'", (request_id,))
        request_data = c.fetchone()
        
        if not request_data or request_data[1] != receiver_info['user_id']:
            emit('error_message', '无效的好友请求')
            return
        
        sender_id = request_data[0]
        
        # 更新请求状态
        c.execute("UPDATE friend_requests SET status = 'accepted' WHERE id = ?", (request_id,))
        
        # 创建好友关系
        c.execute("INSERT INTO friendships (user1_id, user2_id, status) VALUES (?, ?, 1)", 
                  (min(sender_id, receiver_info['user_id']), max(sender_id, receiver_info['user_id'])))
        
        conn.commit()
        
        # 获取发送方信息
        sender_user = get_user_by_id(sender_id)
        
        # 通知双方
        emit('friend_request_accepted', {
            'message': f'您已接受 {sender_user[1]} 的好友请求',
            'friend': {'id': sender_id, 'username': sender_user[1]}
        })
        
        # 通知发送方
        sender_sid = None
        for sid, user_info in online_users.items():
            if user_info['user_id'] == sender_id:
                sender_sid = sid
                break
        
        if sender_sid:
            emit('friend_request_accepted_by_other', {
                'message': f'{receiver_info["username"]} 接受了您的好友请求',
                'friend': {'id': receiver_info['user_id'], 'username': receiver_info['username']}
            }, room=sender_sid)
        
        # 更新双方的好友列表
        receiver_friends = get_user_friends(receiver_info['user_id'])
        emit('update_friends', [{'id': f[0], 'username': f[1]} for f in receiver_friends])
        
        if sender_sid:
            sender_friends = get_user_friends(sender_id)
            emit('update_friends', [{'id': f[0], 'username': f[1]} for f in sender_friends], room=sender_sid)
            
    except Exception as e:
        conn.rollback()
        emit('error_message', f'接受好友请求失败: {str(e)}')
    finally:
        conn.close()

@socketio.on('reject_friend_request')
def handle_reject_friend_request(data):
    """拒绝好友请求"""
    try:
        receiver_info = online_users.get(request.sid)
    except:
        from flask import request as flask_request
        receiver_info = online_users.get(flask_request.sid)
    
    if not receiver_info:
        return
    
    request_id = data.get('request_id')
    if not request_id:
        return
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    
    try:
        # 更新请求状态
        c.execute("UPDATE friend_requests SET status = 'rejected' WHERE id = ? AND receiver_id = ?", 
                  (request_id, receiver_info['user_id']))
        conn.commit()
        
        emit('friend_request_rejected', {'message': '已拒绝好友请求'})
        
    except Exception as e:
        conn.rollback()
        emit('error_message', f'拒绝好友请求失败: {str(e)}')
    finally:
        conn.close()

@socketio.on('get_group_members')
def handle_get_group_members(data):
    """获取群组成员"""
    user_info = online_users.get(request.sid)
    if not user_info:
        return
    
    group_id = data.get('group_id')
    if not group_id:
        return
    
    # 检查用户是否是群组成员
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?", (group_id, user_info['user_id']))
    is_member = c.fetchone()
    conn.close()
    
    if not is_member:
        emit('error_message', '您不是该群组的成员')
        return
    
    members = get_group_members(group_id)
    emit('group_members_list', {
        'members': [{'id': m[0], 'username': m[1]} for m in members],
        'group_id': group_id
    })

@socketio.on('group_chat')
def handle_group_chat(data):
    """处理群聊消息"""
    user_info = online_users.get(request.sid)
    if not user_info:
        return
    
    message = data.get('message', '').strip()
    if not message:
        return
    
    # 存储消息到数据库
    store_message(user_info['user_id'], None, None, message, 'group')
    
    timestamp = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"[{timestamp}] {user_info['username']}: {message}"
    
    # 发送给所有在线用户
    for sid, user_info_target in online_users.items():
        is_self = (user_info_target['user_id'] == user_info['user_id'])
        emit('group_message', {
            'message': formatted_msg,
            'sender': user_info['username'],
            'type': 'group',
            'is_self': is_self
        }, room=sid)

@socketio.on('private_chat')
def handle_private_chat(data):
    """处理私聊消息"""
    sender_info = online_users.get(request.sid)
    if not sender_info:
        return
    
    target_username = data.get('target', '').strip()
    message = data.get('message', '').strip()
    
    if not target_username or not message:
        return
    
    # 获取目标用户信息
    target_user = get_user_by_username(target_username)
    if not target_user:
        emit('error_message', f'用户 {target_username} 不存在')
        return
    
    # 检查是否为好友
    sender_id = sender_info['user_id']
    target_id = target_user[0]
    
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("""
        SELECT 1 FROM friendships 
        WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)) 
        AND status = 1
    """, (sender_id, target_id, target_id, sender_id))
    is_friend = c.fetchone()
    conn.close()
    
    if not is_friend:
        emit('error_message', f'您必须先添加 {target_username} 为好友才能发送私信')
        return
    
    # 存储消息
    store_message(sender_id, target_id, None, message, 'private')
    
    timestamp = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"[{timestamp}] [私聊] {target_user[1]} <- {sender_info['username']}: {message}"
    private_msg = f"[{timestamp}] [私聊] {target_username} -> 你: {message}"
    
    # 检查目标用户是否在线
    target_sid = None
    for sid, user_info in online_users.items():
        if user_info['username'] == target_username:
            target_sid = sid
            break
    
    if target_sid:
        # 目标用户在线，直接发送
        emit('private_message', {
            'message': formatted_msg,
            'sender': target_user[1],
            'target': sender_info['username'],
            'type': 'private',
            'is_self': False
        }, room=target_sid)
        
        # 发送给自己确认
        emit('private_message', {
            'message': private_msg,
            'sender': sender_info['username'],
            'target': target_username,
            'type': 'private',
            'is_self': True
        })
    else:
        # 目标用户不在线，存储为离线消息
        store_offline_message(target_id, sender_id, message, 'private')
        
        # 发送给自己确认
        emit('private_message', {
            'message': f"{private_msg} (对方不在线，已存为离线消息)",
            'sender': sender_info['username'],
            'target': target_username,
            'type': 'private',
            'is_self': True
        })
        
        emit('error_message', f'用户 {target_username} 不在线，消息已存为离线消息')

@socketio.on('group_chat_message')
def handle_group_chat_message(data):
    """处理群组消息"""
    sender_info = online_users.get(request.sid)
    if not sender_info:
        return
    
    group_id = data.get('group_id')
    message = data.get('message', '').strip()
    
    if not group_id or not message:
        return
    
    # 检查用户是否是群组成员
    conn = sqlite3.connect('chat.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?", (group_id, sender_info['user_id']))
    is_member = c.fetchone()
    conn.close()
    
    if not is_member:
        emit('error_message', '您不是该群组的成员', room=request.sid)
        return
    
    # 存储消息
    store_message(sender_info['user_id'], None, group_id, message, 'group_chat')
    
    timestamp = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"[{timestamp}] {sender_info['username']}: {message}"
    
    # 发送给所有群组成员
    group_members = get_group_members(group_id)
    for member in group_members:
        member_sid = None
        for sid, user_info in online_users.items():
            if user_info['user_id'] == member[0]:
                member_sid = sid
                break
        
        if member_sid:
            is_self = (member[0] == sender_info['user_id'])
            emit('group_chat_message', {
                'message': formatted_msg,
                'sender': sender_info['username'],
                'group_id': group_id,
                'type': 'group_chat',
                'is_self': is_self
            }, room=member_sid)

if __name__ == '__main__':
    # 初始化数据库
    init_db()
    
    print("=== Web聊天服务器启动成功 ===")
    print("访问地址: http://localhost:5000")
    print("支持功能: 用户注册/登录、好友系统、群聊、消息存储、离线消息")
    print("================================")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
