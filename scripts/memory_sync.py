#!/usr/bin/env python3
"""
memory_sync
同步记忆到多个 Agent

用法: python3 memory_sync.py <from_agent> <to_agents> [memory_ids]
  from_agent: 源 Agent ID
  to_agents: 目标 Agent ID，多个用逗号分隔
  memory_ids: 要同步的记忆 ID，多个用逗号分隔（可选，默认全部）
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

def sync_memories(from_agent: str, to_agents: list, memory_ids: list = None) -> dict:
    """同步记忆"""
    if not to_agents:
        return {"synced": False, "error": "目标 Agent 列表为空"}
    
    conn = init_db()
    c = conn.cursor()
    
    # 获取要同步的记忆
    if memory_ids:
        placeholders = ",".join("?" * len(memory_ids))
        c.execute(f"SELECT * FROM memories WHERE id IN ({placeholders})", memory_ids)
    else:
        c.execute("SELECT * FROM memories WHERE agent_id = ?", (from_agent,))
    
    source_memories = c.fetchall()
    
    if not source_memories:
        conn.close()
        return {"synced": False, "error": f"源 Agent {from_agent} 没有找到记忆"}
    
    column_names = [
        "id", "agent_id", "message_id", "chat_id", "chat_type",
        "sender_id", "content", "keywords_matched", "access_count",
        "created_at", "last_accessed"
    ]
    
    synced_count = 0
    for row in source_memories:
        memory_data = dict(zip(column_names, row))
        
        for to_agent in to_agents:
            if to_agent == from_agent:
                continue  # 跳过自己
            
            try:
                # 插入或替换到目标 Agent
                c.execute("""
                    INSERT OR REPLACE INTO memories 
                    (message_id, chat_id, chat_type, sender_id, content, 
                     agent_id, keywords_matched, access_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    memory_data["message_id"],
                    memory_data["chat_id"],
                    memory_data["chat_type"],
                    memory_data["sender_id"],
                    memory_data["content"],
                    to_agent,
                    memory_data["keywords_matched"]
                ))
                synced_count += 1
            except Exception as e:
                pass  # 忽略重复插入
    
    conn.commit()
    conn.close()
    
    return {
        "synced": True,
        "count": synced_count,
        "from_agent": from_agent,
        "to_agents": to_agents,
        "source_memories": len(source_memories)
    }

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({
            "error": "参数不足",
            "usage": "python3 memory_sync.py <from_agent> <to_agents> [memory_ids]"
        }, ensure_ascii=False))
        sys.exit(1)
    
    from_agent = sys.argv[1]
    to_agents = [a.strip() for a in sys.argv[2].split(",")]
    memory_ids = None
    
    if len(sys.argv) > 3:
        try:
            memory_ids = [int(mid.strip()) for mid in sys.argv[3].split(",")]
        except ValueError:
            print(json.dumps({"error": "memory_ids 必须是逗号分隔的数字列表"}))
            sys.exit(1)
    
    result = sync_memories(from_agent, to_agents, memory_ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))
