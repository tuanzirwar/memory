#!/usr/bin/env python3
"""
memory_store
将飞书消息写入记忆存储

用法: python3 memory_store.py <message_id> <chat_id> <chat_type> <open_id> <user_id> <tenant_key> <content> [agent_id]
"""
import sys
import json
import sqlite3
from datetime import datetime

DB_PATH = "/home/gem/workspace/agent/memory_store.db"

# 触发关键字（与 memory-config.md 保持同步）
KEYWORDS = {
    "recall": ["查询记忆", "查一下记忆", "之前说过", "记得吗", "我的记忆"],
    "forget": ["忘记这段", "清除记忆", "删掉"],
    "sync": ["同步记忆", "共享记忆"]
}

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT DEFAULT 'global',
            message_id TEXT UNIQUE,
            chat_id TEXT,
            chat_type TEXT,
            sender_id TEXT,
            content TEXT,
            keywords_matched TEXT,
            access_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_accessed TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER,
            keyword TEXT,
            FOREIGN KEY(memory_id) REFERENCES memories(id)
        )
    """)
    conn.commit()
    return conn

def detect_keywords(content: str) -> list:
    """检测内容中匹配的关键字"""
    matched = []
    for category, keywords in KEYWORDS.items():
        for kw in keywords:
            if kw in content:
                matched.append(kw)
    return matched

def store_message(
    message_id: str,
    chat_id: str,
    chat_type: str,
    open_id: str,
    user_id: str,
    tenant_key: str,
    content: str,
    agent_id: str = "global"
) -> dict:
    """存储消息到记忆库"""
    conn = init_db()
    c = conn.cursor()
    
    # 检测关键字
    keywords_matched = detect_keywords(content)
    
    # 写入主表
    c.execute("""
        INSERT OR REPLACE INTO memories 
        (message_id, chat_id, chat_type, sender_id, content, agent_id, keywords_matched, access_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """, (
        message_id,
        chat_id,
        chat_type,
        open_id or user_id,
        content,
        agent_id,
        json.dumps(keywords_matched)
    ))
    memory_id = c.lastrowid
    
    # 写入索引表
    for kw in keywords_matched:
        c.execute(
            "INSERT INTO memory_index (memory_id, keyword) VALUES (?, ?)",
            (memory_id, kw)
        )
    
    conn.commit()
    conn.close()
    
    return {
        "memory_id": memory_id,
        "keywords_matched": keywords_matched,
        "stored": True,
        "actions_detected": {
            "recall": "recall" in str(keywords_matched),
            "forget": "forget" in str(keywords_matched),
            "sync": "sync" in str(keywords_matched)
        }
    }

if __name__ == "__main__":
    if len(sys.argv) < 8:
        print(json.dumps({
            "error": "参数不足",
            "usage": "python3 memory_store.py <message_id> <chat_id> <chat_type> <open_id> <user_id> <tenant_key> <content> [agent_id]"
        }, ensure_ascii=False))
        sys.exit(1)
    
    result = store_message(
        message_id=sys.argv[1],
        chat_id=sys.argv[2],
        chat_type=sys.argv[3],
        open_id=sys.argv[4],
        user_id=sys.argv[5],
        tenant_key=sys.argv[6],
        content=sys.argv[7],
        agent_id=sys.argv[8] if len(sys.argv) > 8 else "global"
    )
    
    print(json.dumps(result, ensure_ascii=False, indent=2))
