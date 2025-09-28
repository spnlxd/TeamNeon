from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory, url_for
import time
import uuid
from queue import Queue
from threading import Lock
import json
import socket
import random
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# In-memory message store
messages = []
# subscribers per room: { room_id: set(queue.Queue()) }
subscribers = {}
sub_lock = Lock()

# waiting queues per topic: { topic: [Queue(), ...] }
waiting = {}
wait_lock = Lock()

# track active users per room: { room_id: set(user_names) }
active_users = {}
users_lock = Lock()

# track typing users per room: { room_id: { user_name: timestamp } }
typing_users = {}
typing_lock = Lock()

# predefined mental-health topics to assign
TOPICS = [
    "Anxiety",
    "Stress management",
    "Depression",
    "Sleep problems",
    "Loneliness",
    "Coping skills",
    "Mindfulness",
    "Work-life balance",
    "Self-esteem",
    "Motivation"
]
room_topics = {}  # room_id -> topic
UPLOAD_DIR = os.path.join('static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.gif'}

def get_local_ip():
    """Returns the local IPv4 address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

@app.route('/message', methods=['POST'])
def post_message():
    """Accept a plaintext chat message and store it.
    Expected JSON body: {"author": "name", "text": "message text", "room": "<room-id>"}
    """
    data = request.get_json(force=True)
    if not data:
        return jsonify({'error': 'invalid json'}), 400

    room = (data.get('room') or '').strip()
    if not room:
        return jsonify({'error': 'missing room'}), 400

    author = (data.get('author') or '').strip()
    text = (data.get('text') or '').strip()

    if not text:
        return jsonify({'error': 'empty message'}), 400

    # allow an optional media URL
    media = data.get('media')

    msg = {
        'id': str(uuid.uuid4()),
        'ts': time.time(),
        'author': author or 'Anonymous',
        'text': text,
        'room': room,
        'media': media,
        'type': 'message'
    }

    messages.append(msg)

    # Broadcast to subscribers
    with sub_lock:
        for q in list(subscribers.get(room, set())):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    return jsonify({'status': 'ok', 'id': msg['id']})

@app.route('/join', methods=['POST'])
def join_room():
    """User joins a room - send join message"""
    data = request.get_json(force=True) or {}
    room = (data.get('room') or '').strip()
    author = (data.get('author') or '').strip()
    
    if not room or not author:
        return jsonify({'error': 'missing room or author'}), 400
    
    # Generate unique name if user is anonymous
    if not author or author == 'Anonymous':
        with users_lock:
            existing_names = active_users.get(room, set())
            counter = 1
            while f'Anonymous{counter}' in existing_names:
                counter += 1
            author = f'Anonymous{counter}'
    
    # Add user to active users and send join message
    with users_lock:
        if room not in active_users:
            active_users[room] = set()
        
        # Only send join message if user is not already in the room
        if author not in active_users[room]:
            active_users[room].add(author)
            
            # Create join message for this user
            join_msg = {
                'id': str(uuid.uuid4()),
                'ts': time.time(),
                'author': author,
                'text': f'{author} joined the chat',
                'room': room,
                'type': 'system'
            }
            
            messages.append(join_msg)
            
            # Broadcast join message
            with sub_lock:
                for q in list(subscribers.get(room, set())):
                    try:
                        q.put_nowait(join_msg)
                    except Exception:
                        pass
    
    return jsonify({'status': 'ok', 'assigned_name': author})

@app.route('/leave', methods=['POST'])
def leave_room():
    """User leaves a room - send leave message"""
    data = request.get_json(force=True) or {}
    room = (data.get('room') or '').strip()
    author = (data.get('author') or '').strip()
    
    if not room or not author:
        return jsonify({'error': 'missing room or author'}), 400
    
    # Remove user from active users and send leave message
    with users_lock:
        if room in active_users:
            active_users[room].discard(author)
            
            # Create leave message
            leave_msg = {
                'id': str(uuid.uuid4()),
                'ts': time.time(),
                'author': author,
                'text': f'{author} left the chat',
                'room': room,
                'type': 'system'
            }
            
            messages.append(leave_msg)
            
            # Broadcast leave message to all clients in the room
            with sub_lock:
                for q_sub in list(subscribers.get(room, set())):
                    try:
                        q_sub.put_nowait(leave_msg)
                    except Exception:
                        pass
            
            # Clean up empty room
            if not active_users[room]:
                del active_users[room]
    
    return jsonify({'status': 'ok'})

@app.route('/typing', methods=['POST'])
def typing():
    """User is typing indicator"""
    data = request.get_json(force=True) or {}
    room = (data.get('room') or '').strip()
    author = (data.get('author') or '').strip()
    
    if not room or not author:
        return jsonify({'error': 'missing room or author'}), 400
    
    # Update typing status
    with typing_lock:
        typing_users.setdefault(room, {})[author] = time.time()
    
    return jsonify({'status': 'ok'})

@app.route('/typing-status/<room>')
def get_typing_status(room):
    """Get current typing users for a room"""
    with typing_lock:
        room_typing = typing_users.get(room, {})
        # Remove users who haven't typed in 3 seconds
        current_time = time.time()
        active_typing = {user: ts for user, ts in room_typing.items() 
                        if current_time - ts < 3}
        typing_users[room] = active_typing
        return jsonify({'typing': list(active_typing.keys())})

@app.route('/match', methods=['POST'])
def match():
    """Match the requester with another waiting user on a topic.

    Behavior:
    - If client requests a specific topic: try to match with any random seeker first (waiting['']),
      then with another specific-topic waiter.
    - If client requests no topic (random): try to match with any existing specific-topic waiter (first non-empty),
      otherwise enqueue into waiting[''] (random bucket).
    """
    data = request.get_json(force=True) or {}
    requested = (data.get('topic') or '').strip()

    q = Queue()
    enqueue_key = None  # which bucket we put this queue into ('' for random, or topic name)

    with wait_lock:
        if requested:
            # requester wants a specific topic: prefer matching with random seekers first
            any_bucket = waiting.get('', [])
            if any_bucket:
                other_q = any_bucket.pop(0)
                room = str(uuid.uuid4())
                room_topics[room] = requested
                try:
                    other_q.put_nowait(room)
                except Exception:
                    pass
                return jsonify({'matched': True, 'room': room, 'topic': requested})

            # then try same-topic bucket
            bucket = waiting.setdefault(requested, [])
            if bucket:
                other_q = bucket.pop(0)
                room = str(uuid.uuid4())
                room_topics[room] = requested
                try:
                    other_q.put_nowait(room)
                except Exception:
                    pass
                return jsonify({'matched': True, 'room': room, 'topic': requested})

            # no match: enqueue into requested topic
            bucket.append(q)
            enqueue_key = requested
        else:
            # requester is random: first try to match with another random user
            random_bucket = waiting.get('', [])
            if random_bucket:
                other_q = random_bucket.pop(0)
                room = str(uuid.uuid4())
                # assign a random topic for random-to-random matches
                random_topic = random.choice(TOPICS)
                room_topics[room] = random_topic
                try:
                    other_q.put_nowait(room)
                except Exception:
                    pass
                return jsonify({'matched': True, 'room': room, 'topic': random_topic})
            
            # then try to match with any waiting specific-topic user
            for t in TOPICS:
                bucket = waiting.get(t, [])
                if bucket:
                    other_q = bucket.pop(0)
                    room = str(uuid.uuid4())
                    room_topics[room] = t
                    try:
                        other_q.put_nowait(room)
                    except Exception:
                        pass
                    return jsonify({'matched': True, 'room': room, 'topic': t})
            # no waiters: enqueue into random bucket ''
            waiting.setdefault('', []).append(q)
            enqueue_key = ''

    # wait outside the lock to be paired or timeout
    try:
        room = q.get(timeout=30)  # Reduced timeout from 60 to 30 seconds
        topic = room_topics.get(room, requested or '')
        return jsonify({'matched': True, 'room': room, 'topic': topic})
    except Exception:
        # timeout: remove from the exact waiting bucket we were placed into
        with wait_lock:
            if enqueue_key is not None:
                bucket = waiting.get(enqueue_key, [])
                try:
                    bucket.remove(q)
                except ValueError:
                    pass
        return jsonify({'matched': False, 'reason': 'timeout'}), 408


@app.route('/topics')
def topics():
    """Return available topics."""
    return jsonify(TOPICS)


@app.route('/status')
def status():
    """Return approximate number of people currently searching per topic and total waiting."""
    with wait_lock:
        counts = {t: len(waiting.get(t, [])) for t in TOPICS}
        total = sum(counts.values())
    return jsonify({'counts': counts, 'total': total})


def allowed_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXT


@app.route('/upload', methods=['POST'])
def upload():
    """Accept multipart file upload and return a public URL to include in messages."""
    if 'file' not in request.files:
        return jsonify({'error': 'no file part'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'no selected file'}), 400
    filename = secure_filename(f.filename)
    if not allowed_file(filename):
        return jsonify({'error': 'disallowed file type'}), 400
    # unique name
    name = f"{uuid.uuid4().hex}_{filename}"
    path = os.path.join(UPLOAD_DIR, name)
    f.save(path)
    # build absolute URL so client can display it
    url = url_for('static_files', filename=f'uploads/{name}', _external=True)
    return jsonify({'url': url})

@app.route('/leave-queue', methods=['POST'])
def leave_queue():
    """Optional: client calls to leave a waiting queue (non-blocking). Body: {topic: "..."}"""
    data = request.get_json(force=True) or {}
    topic = (data.get('topic') or '').strip()
    if not topic:
        return jsonify({'ok': True})
    with wait_lock:
        bucket = waiting.get(topic, [])
        # remove any stale empty queues (best-effort)
        waiting[topic] = [q for q in bucket if not q.empty()]
        # also clean up empty buckets
        if not waiting[topic]:
            waiting.pop(topic, None)
    return jsonify({'ok': True})

@app.route('/messages', methods=['GET'])
def get_messages():
    """Return all messages as a JSON array."""
    room = request.args.get('room')
    if room:
        return jsonify([m for m in messages if m.get('room') == room])
    return jsonify(messages)

@app.route('/stream/<room>')
def stream(room):
    """Stream messages to the client for a specific room using Server-Sent Events."""
    def sse_format(msg):
        payload = json.dumps(msg)
        return f"id: {msg.get('id')}\nevent: message\ndata: {payload}\n\n"

    def event_stream():
        q = Queue()
        with sub_lock:
            subscribers.setdefault(room, set()).add(q)
        try:
            # send existing history for this room first
            for m in [m for m in messages if m.get('room') == room]:
                yield sse_format(m)

            # then stream new messages for this room
            while True:
                try:
                    msg = q.get(timeout=30)  # Add timeout to prevent hanging
                    yield sse_format(msg)
                except Exception:
                    # Send keepalive to prevent connection timeout
                    yield "event: keepalive\ndata: {}\n\n"
        finally:
            with sub_lock:
                subs = subscribers.get(room)
                if subs:
                    subs.discard(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    print("Server running at http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
    
