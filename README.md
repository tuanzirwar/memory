# 飞书记忆系统 (feishu-memory)

基于 SQLite 的飞书对话记忆系统，支持多 Agent 协作、关键词触发召回、自动遗忘机制。

---

## 目录

- [简介](#简介)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [核心功能](#核心功能)
- [使用示例](#使用示例)
- [配置说明](#配置说明)
- [API 参考](#api-参考)
- [常见问题](#常见问题)

---

## 简介

### 这是什么

一个轻量级的飞书对话记忆系统，可以：
- 自动存储飞书消息及其元数据
- 通过关键词触发记忆检索
- 支持多 Agent 场景下的记忆共享
- 自动遗忘（记忆被查询 N 次后删除）

### 工作原理

```
用户发送消息（含触发词）
    ↓
检测触发词（recall/forget/sync）
    ↓
调用对应脚本
    ↓
SQLite 数据库读写
    ↓
返回结果
```

---

## 快速开始

### 第一步：克隆/复制 Skill 目录

```
skills/feishu-memory/
├── SKILL.md
├── scripts/
│   ├── get_metadata.py      # 获取飞书消息元数据
│   ├── memory_store.py      # 存储记忆
│   ├── memory_recall.py     # 检索记忆
│   ├── memory_forget.py      # 删除记忆
│   └── memory_sync.py        # 同步记忆
└── references/
    └── memory-config.md      # 配置文件
```

### 第二步：安装依赖

```bash
pip install httpx
```

### 第三步：配置飞书凭证

编辑 `scripts/get_metadata.py`，填入你的飞书应用凭证：

```python
# 在 get_metadata.py 中
async def get_tenant_access_token(app_id: str, app_secret: str) -> dict:
    # app_id 和 app_secret 通过参数传入
    ...
```

调用时传入凭证：
```bash
python3 scripts/get_metadata.py "<message_id>" "<app_id>" "<app_secret>"
```

### 第四步：测试运行

```bash
# 测试存储
python3 scripts/memory_store.py \
  "om_xxx" "oc_xxx" "group" "ou_xxx" "uid_xxx" "cli_xxx" \
  "这是一条测试记忆"

# 测试检索
python3 scripts/memory_recall.py "查询记忆：测试"
```

---

## 目录结构

| 文件/目录 | 说明 |
|----------|------|
| `SKILL.md` | Skill 定义，描述触发条件和工具说明 |
| `scripts/get_metadata.py` | 获取飞书消息元数据 |
| `scripts/memory_store.py` | 存储消息到记忆库 |
| `scripts/memory_recall.py` | 检索记忆 |
| `scripts/memory_forget.py` | 删除记忆 |
| `scripts/memory_sync.py` | 多 Agent 记忆同步 |
| `references/memory-config.md` | 关键字、多 Agent、阈值配置 |
| `memory_store.db` | SQLite 数据库文件（自动创建） |

---

## 核心功能

### 1. 存储记忆 (memory_store)

将消息及其元数据存入记忆库。

```bash
python3 scripts/memory_store.py \
  "<message_id>" "<chat_id>" "<chat_type>" "<open_id>" "<user_id>" "<tenant_key>" \
  "<content>" [agent_id]
```

### 2. 检索记忆 (memory_recall)

通过关键词检索记忆，支持触发词自动检测。

```bash
python3 scripts/memory_recall.py "<query>" [agent_id] [limit]
```

**触发词（自动检测）：**
- 查询记忆
- 记得吗
- 之前说过
- 我的记忆
- ...

### 3. 删除记忆 (memory_forget)

手动删除指定记忆，或自动清理过期记忆。

```bash
# 删除指定记忆
python3 scripts/memory_forget.py <memory_id> [agent_id] [force]

# 清理所有过期记忆
python3 scripts/memory_forget.py
```

### 4. 同步记忆 (memory_sync)

将记忆同步到多个 Agent。

```bash
python3 scripts/memory_sync.py "<from_agent>" "<to_agent1,to_agent2>" [memory_ids]
```

### 5. 获取消息元数据 (get_metadata)

通过飞书 API 获取消息的完整元数据。

```bash
python3 scripts/get_metadata.py "<message_id>" "<app_id>" "<app_secret>" [tenant_key]
```

---

## 使用示例

### 场景 1：普通对话记忆

```
用户：项目截止日是这周五
Agent：已记住

用户：查询记忆：项目截止日
Agent：项目截止日是这周五
```

### 场景 2：多 Agent 协作

```
用户：同步记忆给 researcher
Agent：已同步 3 条记忆到 researcher
```

### 场景 3：手动删除

```
用户：清除记忆 ID=5
Agent：已删除记忆 #5
```

---

## 配置说明

### 触发关键词

编辑 `references/memory-config.md`：

```yaml
trigger_keywords:
  recall:    # 召回触发词
    - "查询记忆"
    - "记得吗"
    - "之前说过"
  forget:    # 遗忘触发词
    - "忘记这段"
    - "清除记忆"
  sync:      # 同步触发词
    - "同步记忆"
    - "共享记忆"
```

### 遗忘阈值

```yaml
forget_threshold: 3
# 记忆被查询 3 次后自动删除
```

### 多 Agent 配置

```yaml
agents:
  - id: "agent-001"
    name: "main"
    role: "coordinator"
    memory_scope: "global"
  
  - id: "agent-002"
    name: "researcher"
    role: "worker"
    memory_scope: "private"
```

---

## API 参考

### memory_store

**参数：**

| 参数 | 必须 | 说明 |
|-----|------|------|
| message_id | 是 | 消息唯一 ID |
| chat_id | 是 | 会话 ID |
| chat_type | 是 | 会话类型：p2p / group |
| open_id | 是 | 发送者 open_id |
| user_id | 是 | 发送者 user_id |
| tenant_key | 是 | 企业标识 |
| content | 是 | 消息内容 |
| agent_id | 否 | 归属 Agent（默认 global） |

**返回：**

```json
{
  "memory_id": 1,
  "keywords_matched": ["查询记忆"],
  "stored": true,
  "actions_detected": {
    "recall": false,
    "forget": false,
    "sync": false
  }
}
```

### memory_recall

**参数：**

| 参数 | 必须 | 说明 |
|-----|------|------|
| query | 是 | 检索 query |
| agent_id | 否 | 限定 Agent 范围 |
| limit | 否 | 返回数量（默认 5） |

**返回：**

```json
{
  "results": [
    {
      "memory_id": 1,
      "content": "项目截止日是这周五",
      "agent_id": "global",
      "access_count": 1,
      "forgot": false,
      "created_at": "2026-04-25 10:00:00"
    }
  ],
  "total": 1,
  "query": "项目截止日",
  "keywords_matched": ["查询记忆"]
}
```

### memory_forget

**参数：**

| 参数 | 必须 | 说明 |
|-----|------|------|
| memory_id | 否 | 记忆 ID（不传则清理所有过期） |
| agent_id | 否 | Agent ID |
| force | 否 | 强制删除（默认 false） |

**返回：**

```json
{
  "deleted": true,
  "memory_id": 1
}
```

### memory_sync

**参数：**

| 参数 | 必须 | 说明 |
|-----|------|------|
| from_agent | 是 | 源 Agent ID |
| to_agents | 是 | 目标 Agent ID（逗号分隔） |
| memory_ids | 否 | 指定记忆 ID（默认全部） |

**返回：**

```json
{
  "synced": true,
  "count": 3,
  "from_agent": "main",
  "to_agents": ["researcher", "writer"]
}
```

---

## 常见问题

### Q: 数据库路径在哪里？

默认：`/home/gem/workspace/agent/memory_store.db`

修改方式：编辑 `scripts/` 下所有文件的 `DB_PATH` 变量。

### Q: 如何持久化存储？

当前已配置为绝对路径，重启后不会丢失。如需修改，编辑所有脚本中的 `DB_PATH`。

### Q: 遗忘阈值可以调整吗？

可以。编辑 `references/memory-config.md` 中的 `forget_threshold`，或修改 `scripts/memory_recall.py` 中的 `FORGET_THRESHOLD` 常量。

### Q: 如何添加新的触发词？

编辑 `references/memory-config.md` 和 `scripts/memory_recall.py` 中的 `RECALL_KEYWORDS` 列表，两边保持同步。

### Q: 多 Agent 场景如何使用？

1. 在 `memory-config.md` 中注册 Agent
2. 调用 `memory_sync` 同步记忆
3. 不同 Agent 设置不同的 `agent_id` 实现隔离

### Q: 调用失败怎么办？

检查：
1. 飞书凭证是否正确
2. httpx 是否已安装
3. 数据库路径是否存在且可写
4. 消息 ID 是否有效

---

## 更新日志

### v1.0.0
- 基础功能：存储、检索、删除、同步
- 关键词触发机制
- 多 Agent 协作支持
- 自动遗忘机制
