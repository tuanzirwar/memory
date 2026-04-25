#!/usr/bin/env python3
"""
memory_recall
根据关键字检索记忆

用法: python3 memory_recall.py <query> [agent_id] [limit]
"""
import sys
import json
import sqlite3

DB_PATH = "/home/gem/workspace/agent/memory_store.db"
FORGET_THRESHOLD = 3  # 被查询 N 次后删除

# 检索关键字（与 memory-config.md 保持同步）
RECALL_KEYWORDS = ["查询记忆", "查一下记忆", "之前说过", "记得吗", "我的记忆"]

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

def recall_memories(query: str, agent_id: str = None, limit: int = 5) -> dict:
    """检索记忆"""
    conn = init_db()
    c = conn.cursor()
    
    # 检测 query 中是否包含召回关键字
    matched_keywords = [kw for kw in RECALL_KEYWORDS if kw in query]
    
    # 如果 query 中没有召回关键字，检查是否包含其他操作关键字
    other_ops = []
    if any(kw in query for kw in ["忘记", "清除", "删掉"]):
        other_ops.append("forget")
    if any(kw in query for kw in ["同步", "共享"]):
        other_ops.append("sync")
    
    if not matched_keywords and not other_ops:
        return {
            "results": [],
            "total": 0,
            "query": query,
            "message": "未检测到召回关键字"
        }
    
    # 关键词匹配
    matched_ids = set()
    for kw in RECALL_KEYWORDS:
        if kw in query:
            c.execute("SELECT memory_id FROM memory_index WHERE keyword = ?", (kw,))
            matched_ids.update([r[0] for r in c.fetchall()])
    
    if not matched_ids:
        conn.close()
        return {"results": [], "total": 0, "query": query}
    
    # 查询记忆
    agent_filter = ""
    params = list(matched_ids)
    if agent_id:
        agent_filter = " AND (agent_id = ? OR agent_id = 'global')"
        params.append(agent_id)
    params.append(limit)
    
    placeholders = ",".join("?" * len(matched_ids))
    c.execute(f"""
        SELECT id, message_id, chat_id, chat_type, sender_id, content, agent_id, 
               access_count, keywords_matched, created_at
        FROM memories
        WHERE id IN ({placeholders}) {agent_filter}
        ORDER BY access_count ASC, created_at DESC
        LIMIT ?
    """, params)
    
    results = []
    forgotten_ids = []
    
    for row in c.fetchall():
        # 更新访问次数
        new_count = row[7] + 1
        c.execute(
            "UPDATE memories SET access_count = ?, last_accessed = CURRENT_TIMESTAMP WHERE id = ?",
            (new_count, row[0])
        )
        
        # 检查遗忘触发
        forgot = new_count >= FORGET_THRESHOLD
        if forgot:
            forgotten_ids.append(row[0])
        
        results.append({
            "memory_id": row[0],
            "message_id": row[1],
            "chat_id": row[2],
            "chat_type": row[3],
            "sender_id": row[4],
            "content": row[5],
            "agent_id": row[6],
            "access_count": new_count,
            "keywords_matched": json.loads(row[8]) if row[8] else [],
            "created_at": row[9],
            "forgot": forgot
        })
    
    # 删除已遗忘的记忆
    if forgotten_ids:
        placeholders = ",".join("?" * len(forgotten_ids))
        c.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", forgotten_ids)
    
    conn.commit()
    conn.close()
    
    return {
        "results": results,
        "total": len(results),
        "query": query,
        "keywords_matched": matched_keywords,
        "other_operations": other_ops,
        "forgotten": len(forgotten_ids)
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "参数不足",
            "usage": "python3 memory_recall.py <query> [agent_id] [limit]"
        }, ensure_ascii=False))
        sys.exit(1)
    
    query = sys.argv[1]
    agent_id = sys.argv[2] if len(sys.argv) > 2 else None
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    
    result = recall_memories(query, agent_id, limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
