import time

# Session timeout: 2 hours of inactivity resets the conversation
SESSION_TIMEOUT = 2 * 60 * 60

# In-memory store: user_id -> {"messages": [...], "last_active": timestamp}
# Simple dict is enough — no persistence needed for session-only memory
_sessions: dict = {}


def get_history(user_id: int) -> list:
    session = _sessions.get(user_id)
    if not session:
        return []
    if time.time() - session["last_active"] > SESSION_TIMEOUT:
        del _sessions[user_id]
        return []
    return session["messages"]


def add_message(user_id: int, role: str, content: str):
    if user_id not in _sessions:
        _sessions[user_id] = {"messages": [], "last_active": time.time()}
    _sessions[user_id]["messages"].append({"role": role, "content": content})
    _sessions[user_id]["last_active"] = time.time()


def clear_history(user_id: int):
    _sessions.pop(user_id, None)


def has_history(user_id: int) -> bool:
    return bool(get_history(user_id))
