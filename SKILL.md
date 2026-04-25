---
name: feishu-memory
description: |
  飞书消息记忆系统。当需要存储、检索、同步飞书对话记忆时使用。
  触发场景：
  (1) 用户说"查询记忆"、"记得吗"、"之前说过"等关键词时，调用 memory_recall
  (2) 用户说"忘记这段"、"清除记忆"时，调用 memory_forget
  (3) 用户说"同步记忆"、"共享记忆"时，调用 memory_sync
  (4) 收到飞书消息时，调用 memory_store 存储消息及其元数据
  (5) 需要获取飞书消息元数据（message_id, chat_id, chat_type, sender info）时调用 feishu_get_message_metadata
---

# Feishu Memory Skill

飞书消息记忆系统，基于 SQLite 实现，支持多 Agent 协作场景下的记忆共享与遗忘机制。

## 核心工具

### 1. feishu_get_message_metadata

获取飞书消息元数据。**每次调用自动通过 app_id/app_secret 获取 tenant_access_token**。

**输入参数：**
- `message_id`: 飞书消息 ID（om_xxx 格式）
- `app_id`: 飞书应用 App ID（cli_xxx）
- `app_secret`: 飞书应用 App Secret
- `tenant_key`: 可选

**调用方式：**
```bash
python3 /home/gem/workspace/agent/skills/feishu-memory/scripts/get_metadata.py \
  "<message_id>" "<app_id>" "<app_secret>" [tenant_key]
```

**示例：**
```bash
python3 get_metadata.py om_xxx cli_a9619974c4fb9bd2 "your_secret_here"
```

**返回示例：**
```json
{
  "success": true,
  "message_id": "om_xxx",
  "chat_id": "oc_xxx",
  "chat_type": "group",
  "sender": {
    "open_id": "ou_xxx",
    "user_id": "uid_xxx"
  }
}
```

---

### 2. memory_store

将飞书消息写入记忆存储。

**输入参数：**
- `message_id`: 消息唯一 ID
- `chat_id`: 会话 ID
- `chat_type`: 会话类型（p2p/group）
- `open_id`: 发送者 open_id
- `user_id`: 发送者 user_id
- `tenant_key`: 企业标识
- `content`: 消息原文
- `agent_id`: 归属 Agent ID（可选，默认为 global）

**调用方式：**
```bash
python3 /home/gem/workspace/agent/skills/feishu-memory/scripts/memory_store.py \
  "<message_id>" "<chat_id>" "<chat_type>" "<open_id>" "<user_id>" "<tenant_key>" "<content>" [agent_id]
```

**返回：**
```json
{"memory_id": 42, "keywords_matched": ["查询记忆"], "stored": true}
```

---

### 3. memory_recall

根据关键字检索记忆。

**输入参数：**
- `query`: 检索 query
- `agent_id`: 限定 Agent 范围（可选，默认 global+自身）
- `limit`: 返回数量（可选，默认 5）

**调用方式：**
```bash
python3 /home/gem/workspace/agent/skills/feishu-memory/scripts/memory_recall.py "<query>" [agent_id] [limit]
```

**返回：**
```json
{
  "results": [
    {
      "memory_id": 7,
      "content": "项目截止日是这周五",
      "agent_id": "global",
      "access_count": 2,
      "forgot": false,
      "created_at": "2026-04-25 10:00:00"
    }
  ],
  "total": 1,
  "query": "项目截止日"
}
```

---

### 4. memory_forget

删除指定记忆或清理过期记忆。

**输入参数：**
- `memory_id`: 记忆 ID
- `agent_id`: Agent ID（可选）
- `force`: 是否强制删除（可选，默认 false）

**调用方式：**
```bash
python3 /home/gem/workspace/agent/skills/feishu-memory/scripts/memory_forget.py <memory_id> [agent_id] [force]
```

**返回：**
```json
{"deleted": true, "memory_id": 42}
```

---

### 5. memory_sync

同步记忆到多个 Agent。

**输入参数：**
- `from_agent`: 源 Agent ID
- `to_agents`: 目标 Agent ID 列表（逗号分隔）
- `memory_ids`: 要同步的记忆 ID 列表（可选，默认全部）

**调用方式：**
```bash
python3 /home/gem/workspace/agent/skills/feishu-memory/scripts/memory_sync.py "<from_agent>" "<to_agent1,to_agent2>" [memory_ids]
```

**返回：**
```json
{"synced": true, "count": 5, "to_agents": ["agent-002", "agent-003"]}
```

---

## 工作流程

### 存储流程
```
收到飞书消息
  → 提取元数据（message_id, chat_id, chat_type, sender）
  → 调用 memory_store
  → 关键词检测（recall/forget/sync）
  → 根据检测结果决定是否需要召回/遗忘/同步
```

### 检索流程
```
用户发送包含 recall 关键词的消息
  → 调用 memory_recall
  → 返回匹配的记忆条目
  → 附带上一次查询结果
```

### 遗忘流程
```
每次 memory_recall 被调用
  → access_count + 1
  → 如果 access_count >= forget_threshold（默认 3）
     → 自动删除该记忆
```

---

## 配置说明

关键字和多 Agent 配置在 `references/memory-config.md` 中维护：

- `trigger_keywords`: 触发关键字配置
- `forget_threshold`: 遗忘阈值（被查询多少次后删除）
- `agents`: 多 Agent 协作配置

---

## 注意事项

1. 数据库文件位于 `/home/gem/workspace/agent/memory_store.db`，持久化存储，重启后不丢失
3. 多 Agent 场景下，`agent_id` 用于隔离记忆范围
4. `global` 范围的记忆对所有 Agent 可见
