#!/usr/bin/env python3
"""
memory_forget
删除指定记忆或清理过期记忆

用法: python3 memory_forget.py <memory_id> [agent_id] [force]
"""
import sys
import json
import sqlite3

DB_PATH = "/home/gem/workspace/agent/memory_store.db"

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

def forget_memory(memory_id: int, agent_id: str = None, force: bool = False) -> dict:
    """删除记忆"""
    conn = init_db()
    c = conn.cursor()
    
    # 检查记忆是否存在
    c.execute("SELECT id, agent_id FROM memories WHERE id = ?", (memory_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return {"deleted": False, "memory_id": memory_id, "error": "记忆不存在"}
    
    # 检查权限（非 force 模式下，只能删除自己的记忆）
    if not force and agent_id and row[1] != agent_id and row[1] != "global":
        conn.close()
        return {"deleted": False, "memory_id": memory_id, "error": "无权限删除其他 Agent 的记忆"}
    
    # 删除索引
    c.execute("DELETE FROM memory_index WHERE memory_id = ?", (memory_id,))
    
    # 删除记忆
    c.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    
    conn.commit()
    conn.close()
    
    return {"deleted": True, "memory_id": memory_id}

def forget_expired() -> dict:
    """清理所有过期记忆（access_count <= 0 或达到阈值）"""
    conn = init_db()
    c = conn.cursor()
    
    # 删除访问次数耗尽的记忆
    c.execute("DELETE FROM memories WHERE access_count >= 3")
    deleted_count = c.rowcount
    
    # 清理孤立索引
    c.execute("DELETE FROM memory_index WHERE memory_id NOT IN (SELECT id FROM memories)")
    orphan_count = c.rowcount
    
    conn.commit()
    conn.close()
    
    return {
        "cleaned": True,
        "deleted_memories": deleted_count,
        "orphan_indexes": orphan_count
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 无参数时，清理所有过期记忆
        result = forget_expired()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)
    
    try:
        memory_id = int(sys.argv[1])
    except ValueError:
        print(json.dumps({"error": "memory_id 必须是数字"}))
        sys.exit(1)
    
    agent_id = sys.argv[2] if len(sys.argv) > 2 else None
    force = sys.argv[3].lower() == "true" if len(sys.argv) > 3 else False
    
    result = forget_memory(memory_id, agent_id, force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
