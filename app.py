import os
import sqlite3
import requests
import uuid
import time
from datetime import timedelta
from flask import Flask, render_template, request, redirect, session, jsonify, url_for, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_maocs_v19'
app.permanent_session_lifetime = timedelta(days=30)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.environ.get('MAOCS_DB') or os.path.join(BASE_DIR, 'chat_v3.db')

socketio = SocketIO(app, cors_allowed_origins="*")
online_users = {}

def get_real_username(c, target):
    c.execute('SELECT username FROM users')
    for row in c.fetchall():
        if row[0].strip().lower() == target.strip().lower(): return row[0]
    return None

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, password_2 TEXT DEFAULT "", avatar TEXT DEFAULT "")')
        
        # Обновление структуры для старых БД (добавление password_2)
        try: c.execute('ALTER TABLE users ADD COLUMN password_2 TEXT DEFAULT ""')
        except: pass

        c.execute('CREATE TABLE IF NOT EXISTS messages (room TEXT, username TEXT, type TEXT, content TEXT, reply_user TEXT DEFAULT "", reply_text TEXT DEFAULT "", msg_id TEXT DEFAULT "", reply_id TEXT DEFAULT "", reply_type TEXT DEFAULT "text", reply_content TEXT DEFAULT "", timestamp REAL DEFAULT 0, caption TEXT DEFAULT "")')
        
        try: c.execute('ALTER TABLE messages ADD COLUMN edited INTEGER DEFAULT 0')
        except: pass

        c.execute('CREATE TABLE IF NOT EXISTS user_chats (username TEXT, room_id TEXT, room_name TEXT, UNIQUE(username, room_id))')
        c.execute('CREATE TABLE IF NOT EXISTS groups (room_id TEXT PRIMARY KEY, room_name TEXT, creator TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS friends (user1 TEXT, user2 TEXT, UNIQUE(user1, user2))')
        c.execute('CREATE TABLE IF NOT EXISTS friend_requests (sender TEXT, receiver TEXT, UNIQUE(sender, receiver))')
        c.execute('CREATE TABLE IF NOT EXISTS blocked (blocker TEXT, blocked TEXT, UNIQUE(blocker, blocked))')
        c.execute('CREATE TABLE IF NOT EXISTS group_bans (room_id TEXT, username TEXT, UNIQUE(room_id, username))')
        c.execute('CREATE TABLE IF NOT EXISTS cleared_chats (username TEXT, room_id TEXT, cleared_at REAL, UNIQUE(username, room_id))')
        conn.commit()

init_db()

@app.route('/favicon.ico')
def favicon(): return send_from_directory(os.path.join(app.root_path, 'static'), 'icon.png', mimetype='image/png')

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        password_2 = request.form.get('password_2', '')
        action = request.form['action']
        remember = request.form.get('remember')
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            if action == 'register':
                if get_real_username(c, username): return "<h3>Имя занято!</h3><a href='/'>Назад</a>"
                c.execute('INSERT INTO users (username, password, password_2, avatar) VALUES (?, ?, ?, ?)', (username, generate_password_hash(password), generate_password_hash(password_2), ''))
                c.execute('INSERT INTO user_chats (username, room_id, room_name) VALUES (?, ?, ?)', (username, 'global', '🌍 Общий чат'))
                conn.commit()
                if remember: session.permanent = True; app.permanent_session_lifetime = timedelta(days=14)
                session['username'] = username
                return redirect(url_for('chat'))
            elif action == 'login':
                real_user = get_real_username(c, username)
                if not real_user: return "<h3>Аккаунт не найден!</h3><a href='/'>Назад</a>"
                c.execute('SELECT password, password_2 FROM users WHERE username = ?', (real_user,))
                user_data = c.fetchone()
                
                if check_password_hash(user_data[0], password) and check_password_hash(user_data[1], password_2):
                    if remember: session.permanent = True; app.permanent_session_lifetime = timedelta(days=14)
                    session['username'] = real_user
                    return redirect(url_for('chat'))
                return "<h3>Неверный пароль 1 или 2!</h3><a href='/'>Назад</a>"
    if 'username' in session: return redirect(url_for('chat'))
    return render_template('login.html')

@app.route('/chat')
def chat():
    if 'username' not in session: return redirect(url_for('login'))
    with sqlite3.connect(DB_NAME) as conn: row = conn.execute('SELECT avatar FROM users WHERE username = ?', (session['username'],)).fetchone()
    avatar = row[0] if row else ''
    return render_template('chat.html', username=session['username'], avatar=avatar)

@app.route('/logout')
def logout(): session.pop('username', None); return redirect(url_for('login'))

@app.route('/get_online_users')
def get_online_users(): return jsonify(list(online_users.keys()))

@app.route('/my_chats')
def my_chats():
    if 'username' not in session: return jsonify([])
    me = session['username']; chats =[]
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT uc.room_id, uc.room_name, g.creator FROM user_chats uc LEFT JOIN groups g ON uc.room_id = g.room_id WHERE uc.username = ?''', (me,))
        for row in c.fetchall():
            r_id, r_name, creator = row[0], row[1], row[2]
            avatar_url, target_user = "", None
            if r_id != 'global' and not r_id.startswith('group_'):
                users = r_id.split('_')
                if len(users) == 2:
                    target_user = users[1] if users[0] == me else users[0]
                    r_name = target_user 
                    res = c.execute('SELECT avatar FROM users WHERE username = ?', (target_user,)).fetchone()
                    if res: avatar_url = res[0]
            chats.append({'id': r_id, 'name': r_name, 'is_creator': creator == me, 'avatar': avatar_url, 'target_user': target_user})
    return jsonify(chats)

@app.route('/get_requests')
def get_requests():
    with sqlite3.connect(DB_NAME) as conn: return jsonify([row[0] for row in conn.execute('SELECT sender FROM friend_requests WHERE receiver = ?', (session['username'],)).fetchall()])

@app.route('/send_friend_req', methods=['POST'])
def send_friend_req():
    me = session.get('username'); target = request.json.get('target')
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor(); target_real = get_real_username(c, target)
        if not target_real: return jsonify({'error': 'Не найден!'})
        if target_real == me: return jsonify({'error': 'Себя нельзя добавить.'})
        if c.execute('SELECT * FROM blocked WHERE blocker=? AND blocked=?', (target_real, me)).fetchone(): return jsonify({'error': 'Он вас заблокировал.'})
        if c.execute('SELECT * FROM friends WHERE user1=? AND user2=?', (me, target_real)).fetchone(): return jsonify({'error': 'Уже в друзьях!'})
        c.execute('INSERT OR IGNORE INTO friend_requests (sender, receiver) VALUES (?, ?)', (me, target_real)); conn.commit()
    socketio.emit('new_friend_req', to=target_real); return jsonify({'success': True, 'msg': 'Отправлено!'})

@app.route('/answer_request', methods=['POST'])
def answer_request():
    me = session['username']; sender = request.json.get('sender'); action = request.json.get('action')
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor(); c.execute('DELETE FROM friend_requests WHERE sender=? AND receiver=?', (sender, me))
        if action == 'accept':
            c.execute('INSERT OR IGNORE INTO friends (user1, user2) VALUES (?,?)', (me, sender)); c.execute('INSERT OR IGNORE INTO friends (user1, user2) VALUES (?,?)', (sender, me))
            room_id = "_".join(sorted([me, sender]))
            c.execute('INSERT OR IGNORE INTO user_chats (username, room_id, room_name) VALUES (?,?,?)', (me, room_id, sender)); c.execute('INSERT OR IGNORE INTO user_chats (username, room_id, room_name) VALUES (?,?,?)', (sender, room_id, me))
            conn.commit(); socketio.emit('chat_invited', {'room_id': room_id}, to=sender)
        else: conn.commit()
    return jsonify({'success': True})

@app.route('/unfriend', methods=['POST'])
def unfriend():
    me = session['username']; target = request.json.get('target')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('DELETE FROM friends WHERE (user1=? AND user2=?) OR (user1=? AND user2=?)', (me, target, target, me))
        room_id = "_".join(sorted([me, target])); conn.execute('DELETE FROM user_chats WHERE room_id=?', (room_id,)); conn.commit()
    socketio.emit('chat_invited', {'room_id': 'none'}, to=target); return jsonify({'success': True})

@app.route('/block_user', methods=['POST'])
def block_user():
    me = session['username']; target = request.json.get('target')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('INSERT OR IGNORE INTO blocked (blocker, blocked) VALUES (?,?)', (me, target))
        conn.execute('DELETE FROM friends WHERE (user1=? AND user2=?) OR (user1=? AND user2=?)', (me, target, target, me))
        room_id = "_".join(sorted([me, target])); conn.execute('DELETE FROM user_chats WHERE room_id=?', (room_id,)); conn.commit()
    socketio.emit('chat_invited', {'room_id': 'none'}, to=target); return jsonify({'success': True})

@app.route('/get_blocked')
def get_blocked():
    with sqlite3.connect(DB_NAME) as conn: return jsonify([row[0] for row in conn.execute('SELECT blocked FROM blocked WHERE blocker = ?', (session['username'],)).fetchall()])

@app.route('/unblock_user', methods=['POST'])
def unblock_user():
    with sqlite3.connect(DB_NAME) as conn: conn.execute('DELETE FROM blocked WHERE blocker=? AND blocked=?', (session['username'], request.json.get('target'))); conn.commit()
    return jsonify({'success': True})

@app.route('/add_group', methods=['POST'])
def add_group():
    me = session.get('username'); target = request.json.get('target').strip()
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor(); room_id = 'group_' + target
        if not c.execute('SELECT creator FROM groups WHERE room_id = ?', (room_id,)).fetchone(): c.execute('INSERT INTO groups (room_id, room_name, creator) VALUES (?, ?, ?)', (room_id, target, me))
        c.execute('INSERT OR IGNORE INTO user_chats (username, room_id, room_name) VALUES (?, ?, ?)', (me, room_id, target)); conn.commit()
    return jsonify({'success': True, 'room_id': room_id, 'room_name': target})

@app.route('/invite_group', methods=['POST'])
def invite_group():
    me = session.get('username'); room_id = request.json.get('room_id'); target = request.json.get('target')
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor(); target_real = get_real_username(c, target)
        if not target_real: return jsonify({'error': 'Не найден!'})
        if c.execute('SELECT * FROM group_bans WHERE room_id=? AND username=?', (room_id, target_real)).fetchone(): return jsonify({'error': 'Забанен!'})
        grp = c.execute('SELECT room_name FROM groups WHERE room_id = ?', (room_id,)).fetchone()
        c.execute('INSERT OR IGNORE INTO user_chats (username, room_id, room_name) VALUES (?, ?, ?)', (target_real, room_id, grp[0]))
        msg_id = str(uuid.uuid4()); ts = time.time(); sys_msg = f"{me} добавил(а) {target_real} в чат"
        c.execute('INSERT INTO messages (room, username, type, content, msg_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)', (room_id, 'MAOCS', 'system', sys_msg, msg_id, ts)); conn.commit()
    socketio.emit('receive_message', {'msg_id': msg_id, 'username': 'MAOCS', 'type': 'system', 'content': sys_msg, 'room': room_id, 'avatar': '', 'timestamp': ts}, to=room_id)
    socketio.emit('chat_invited', {'room_id': room_id}, to=target_real); return jsonify({'success': True})

@app.route('/kick_ban_user', methods=['POST'])
def kick_ban_user():
    me = session.get('username'); room_id = request.json.get('room_id'); target = request.json.get('target'); action = request.json.get('action') 
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        grp = c.execute('SELECT creator FROM groups WHERE room_id = ?', (room_id,)).fetchone()
        if not grp or grp[0] != me: return jsonify({'error': 'Только создатель может!'})
        target_real = get_real_username(c, target)
        if not target_real or target_real == me: return jsonify({'error': 'Нельзя удалить.'})
        c.execute('DELETE FROM user_chats WHERE username=? AND room_id=?', (target_real, room_id))
        if action == 'ban': c.execute('INSERT OR IGNORE INTO group_bans (room_id, username) VALUES (?,?)', (room_id, target_real))
        msg_id = str(uuid.uuid4()); ts = time.time(); sys_msg = f"{target_real} был {'забанен' if action=='ban' else 'исключен'}."
        c.execute('INSERT INTO messages (room, username, type, content, msg_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)', (room_id, 'MAOCS', 'system', sys_msg, msg_id, ts)); conn.commit()
    socketio.emit('receive_message', {'msg_id': msg_id, 'username': 'MAOCS', 'type': 'system', 'content': sys_msg, 'room': room_id, 'avatar': '', 'timestamp': ts}, to=room_id)
    socketio.emit('chat_invited', {'room_id': 'none'}, to=target_real); return jsonify({'success': True})

@app.route('/leave_group', methods=['POST'])
def leave_group():
    with sqlite3.connect(DB_NAME) as conn: conn.execute('DELETE FROM user_chats WHERE username = ? AND room_id = ?', (session['username'], request.json.get('room_id'))); conn.commit()
    return jsonify({'success': True})

@app.route('/delete_group', methods=['POST'])
def delete_group():
    me = session.get('username'); room_id = request.json.get('room_id')
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        row = c.execute('SELECT creator FROM groups WHERE room_id = ?', (room_id,)).fetchone()
        if not row or row[0] != me: return jsonify({'error': 'Нет прав!'}), 403
        c.execute('DELETE FROM groups WHERE room_id = ?', (room_id,)); c.execute('DELETE FROM user_chats WHERE room_id = ?', (room_id,)); c.execute('DELETE FROM messages WHERE room = ?', (room_id,)); conn.commit()
    socketio.emit('group_deleted', {'room_id': room_id}, to=room_id); return jsonify({'success': True})

@app.route('/clear_chat', methods=['POST'])
def clear_chat():
    me = session['username']; room = request.json.get('room_id'); t = time.time()
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('INSERT INTO cleared_chats (username, room_id, cleared_at) VALUES (?, ?, ?) ON CONFLICT(username, room_id) DO UPDATE SET cleared_at=?', (me, room, t, t)); conn.commit()
    return jsonify({'success': True})

# СЕКРЕТНАЯ ФУНКЦИЯ ДЛЯ СБРОСА ВСЕЙ БАЗЫ ДАННЫХ
@app.route('/factory_reset', methods=['POST'])
def factory_reset():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('DROP TABLE IF EXISTS users')
        c.execute('DROP TABLE IF EXISTS messages')
        c.execute('DROP TABLE IF EXISTS user_chats')
        c.execute('DROP TABLE IF EXISTS groups')
        c.execute('DROP TABLE IF EXISTS friends')
        c.execute('DROP TABLE IF EXISTS friend_requests')
        c.execute('DROP TABLE IF EXISTS blocked')
        c.execute('DROP TABLE IF EXISTS group_bans')
        c.execute('DROP TABLE IF EXISTS cleared_chats')
        conn.commit()
    init_db()
    session.clear()
    return jsonify({'success': True})

@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file'); room = request.form.get('room', 'global')
    caption = request.form.get('caption', '')
    ru, rt, r_id, r_type, r_content = request.form.get('reply_user', ''), request.form.get('reply_text', ''), request.form.get('reply_id', ''), request.form.get('reply_type', 'text'), request.form.get('reply_content', '')
    if not file: return jsonify({'error': 'Нет файла'})
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.post('https://catbox.moe/user/api.php', data={'reqtype': 'fileupload'}, files={'fileToUpload': (file.filename, file.stream, file.mimetype)}, headers=headers, timeout=120)
        if resp.status_code == 200: file_url = resp.text.strip()
        else: return jsonify({'error': 'Ошибка сервера Catbox'})
    except: return jsonify({'error': 'Ошибка интернета'})

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext in['png', 'jpg', 'jpeg', 'gif', 'webp']: msg_type = 'image'
    elif ext in['mp4', 'webm', 'ogg', 'mov', 'avi']: msg_type = 'video'
    else: msg_type = 'file'
    msg_id = str(uuid.uuid4()); ts = time.time()

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('INSERT INTO messages (room, username, type, content, reply_user, reply_text, msg_id, reply_id, reply_type, reply_content, timestamp, caption) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (room, session['username'], msg_type, file_url, ru, rt, msg_id, r_id, r_type, r_content, ts, caption))
        avatar = c.execute('SELECT avatar FROM users WHERE username = ?', (session['username'],)).fetchone()[0]; conn.commit()

    socketio.emit('receive_message', {'msg_id': msg_id, 'username': session['username'], 'type': msg_type, 'content': file_url, 'caption': caption, 'room': room, 'avatar': avatar, 'reply_user': ru, 'reply_text': rt, 'reply_id': r_id, 'reply_type': r_type, 'reply_content': r_content, 'timestamp': ts, 'edited': 0}, to=room)
    return jsonify({'success': True})

@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    file = request.files.get('file')
    if not file: return jsonify({'error': 'Нет файла'})
    try: 
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.post('https://catbox.moe/user/api.php', data={'reqtype': 'fileupload'}, files={'fileToUpload': (file.filename, file.stream, file.mimetype)}, headers=headers, timeout=60); file_url = resp.text.strip()
    except: return jsonify({'error': 'Ошибка облака'})
    with sqlite3.connect(DB_NAME) as conn: conn.execute('UPDATE users SET avatar = ? WHERE username = ?', (file_url, session['username'])); conn.commit()
    return jsonify({'success': True, 'avatar': file_url})

@app.route('/history/<room>')
def history(room):
    me = session['username']
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        cl = c.execute('SELECT cleared_at FROM cleared_chats WHERE username=? AND room_id=?', (me, room)).fetchone()
        cl_time = cl[0] if cl else 0
        return jsonify([{'username': row[0], 'type': row[1], 'content': row[2], 'avatar': row[3] or '', 'reply_user': row[4], 'reply_text': row[5], 'msg_id': row[6], 'reply_id': row[7], 'reply_type': row[8], 'reply_content': row[9], 'timestamp': row[10] or 0, 'caption': row[11], 'edited': row[12]} for row in c.execute('SELECT m.username, m.type, m.content, u.avatar, m.reply_user, m.reply_text, m.msg_id, m.reply_id, m.reply_type, m.reply_content, m.timestamp, m.caption, m.edited FROM messages m LEFT JOIN users u ON m.username = u.username WHERE m.room = ? AND m.timestamp > ? ORDER BY m.rowid ASC', (room, cl_time)).fetchall()])

# СОКЕТЫ
@socketio.on('connect')
def on_connect():
    me = session.get('username')
    if me:
        online_users[me] = online_users.get(me, 0) + 1
        join_room(me); emit('user_status', {'username': me, 'online': True}, broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    me = session.get('username')
    if me and me in online_users:
        online_users[me] -= 1
        if online_users[me] <= 0: del online_users[me]; emit('user_status', {'username': me, 'online': False}, broadcast=True)

@socketio.on('join')
def on_join(data): join_room(data['room'])

@socketio.on('send_message')
def handle_message(data):
    username = session.get('username'); room = data['room']; content = data['content']
    ru, rt, r_id, r_type, rc = data.get('reply_user',''), data.get('reply_text',''), data.get('reply_id',''), data.get('reply_type','text'), data.get('reply_content','')
    msg_id = str(uuid.uuid4()); ts = time.time()
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('INSERT INTO messages (room, username, type, content, reply_user, reply_text, msg_id, reply_id, reply_type, reply_content, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (room, username, 'text', content, ru, rt, msg_id, r_id, r_type, rc, ts))
        avatar = c.execute('SELECT avatar FROM users WHERE username = ?', (username,)).fetchone()[0]; conn.commit()
    emit('receive_message', {'msg_id': msg_id, 'username': username, 'type': 'text', 'content': content, 'room': room, 'avatar': avatar, 'reply_user': ru, 'reply_text': rt, 'reply_id': r_id, 'reply_type': r_type, 'reply_content': rc, 'timestamp': ts, 'edited': 0}, to=room)

@socketio.on('edit_message')
def edit_message(data):
    me = session.get('username'); msg_id = data['msg_id']; new_content = data['content']; room = data['room']
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        row = c.execute('SELECT username, type FROM messages WHERE msg_id=?', (msg_id,)).fetchone()
        if row and row[0] == me:
            if row[1] == 'text': c.execute('UPDATE messages SET content=?, edited=1 WHERE msg_id=?', (new_content, msg_id))
            else: c.execute('UPDATE messages SET caption=?, edited=1 WHERE msg_id=?', (new_content, msg_id))
            conn.commit()
            emit('message_edited', {'msg_id': msg_id, 'content': new_content, 'room': room, 'is_caption': row[1] != 'text', 'edited': 1}, to=room)

@socketio.on('delete_message')
def delete_message(data):
    me = session.get('username'); msg_id = data['msg_id']; room = data['room']
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        row = c.execute('SELECT username FROM messages WHERE msg_id=?', (msg_id,)).fetchone()
        if row and row[0] == me:
            c.execute('DELETE FROM messages WHERE msg_id=?', (msg_id,)); conn.commit()
            emit('message_deleted', {'msg_id': msg_id, 'room': room}, to=room)

@socketio.on('send_sys_msg')
def send_sys_msg(data):
    room = data['room']; content = data['content']; msg_id = str(uuid.uuid4()); ts = time.time()
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('INSERT INTO messages (room, username, type, content, msg_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)', (room, 'MAOCS', 'system', content, msg_id, ts)); conn.commit()
    emit('receive_message', {'msg_id': msg_id, 'username': 'MAOCS', 'type': 'system', 'content': content, 'room': room, 'avatar': '', 'timestamp': ts}, to=room)

@socketio.on('typing')
def on_typing(data): emit('user_typing', {'username': session.get('username'), 'room': data['room']}, to=data['room'], include_self=False)
@socketio.on('stop_typing')
def on_stop_typing(data): emit('user_stop_typing', {'username': session.get('username'), 'room': data['room']}, to=data['room'], include_self=False)
@socketio.on('call_user')
def call_user(data): emit('incoming_call', {'from': session['username'], 'room': data['room']}, to=data['room'], include_self=False)

# СЕТЬ GROUP WEBRTC
@socketio.on('join_call_room')
def join_call_room(data):
    call_room = 'call_' + data['room']
    join_room(call_room)
    emit('user_joined_call', {'sid': request.sid, 'username': session.get('username')}, to=call_room, include_self=False)

@socketio.on('leave_call_room')
def leave_call_room(data):
    call_room = 'call_' + data['room']
    leave_room(call_room)
    emit('webrtc_signal', {'user_left': request.sid}, to=call_room)

@socketio.on('webrtc_signal')
def webrtc_signal(data):
    if data.get('broadcast'):
        emit('webrtc_signal', data, to=data.get('room'))
    elif data.get('target_sid'):
        emit('webrtc_signal', data, to=data['target_sid'])

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
