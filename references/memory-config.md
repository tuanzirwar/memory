# memory-config.md - 飞书记忆系统配置

## 触发关键字

```yaml
trigger_keywords:
  recall:
    - "查询记忆"
    - "查一下记忆"
    - "之前说过"
    - "记得吗"
    - "我的记忆"
    - "之前说"
    - "我记得"
    - "上次说"
  
  forget:
    - "忘记这段"
    - "清除记忆"
    - "删掉"
    - "不用记"
    - "忘掉"
  
  sync:
    - "同步记忆"
    - "共享记忆"
    - "广播记忆"
    - "同步到"
```

## 遗忘阈值

```yaml
forget_threshold: 3
# 说明：记忆被查询达到此次数后自动删除
# 设为 0 则禁用自动遗忘
```

## 多 Agent 协作配置

```yaml
agents:
  - id: "agent-001"
    name: "main"
    role: "coordinator"  # coordinator=协调者，worker=工作者
    memory_scope: "global"  # global=所有 agent 共享，private=仅自身可见
  
  - id: "agent-002"
    name: "researcher"
    role: "worker"
    memory_scope: "private"
  
  - id: "agent-003"
    name: "writer"
    role: "worker"
    memory_scope: "private"
```

## 返回字段说明

| 字段 | 类型 | 说明 |
|-----|------|------|
| `memory_id` | int | 记忆唯一 ID |
| `message_id` | string | 原始消息 ID |
| `chat_id` | string | 所属会话 ID |
| `chat_type` | string | 会话类型：p2p/group |
| `sender_id` | string | 发送者 ID |
| `content` | string | 记忆内容 |
| `agent_id` | string | 归属 Agent |
| `memory_scope` | string | global=全局共享，private=私有 |
| `access_count` | int | 被查询次数，每次召回+1 |
| `created_at` | string | 创建时间 |
| `keywords_matched` | array | 匹配到的触发关键字 |
| `forgot` | bool | 是否因达到阈值而被遗忘 |

## 同步场景示例

### 场景 1：main agent 同步到所有 worker
```
用户：同步记忆给 researcher 和 writer
→ 调用 memory_sync("agent-001", ["agent-002", "agent-003"])
```

### 场景 2：worker 之间不直接同步（通过 main 协调）
```
用户：把 researcher 的发现同步给 writer
→ 调用 memory_sync("agent-002", ["agent-001", "agent-003"])
   （先同步到 main，再由 main 分发给 writer）
```

### 场景 3：全局公告记忆
```
用户：这是重要信息，所有人都要知道
→ 调用 memory_sync("agent-001", ["agent-002", "agent-003"])
   （设置 memory_scope=global）
```

## 数据库路径

```yaml
DB_PATH: "/home/gem/workspace/agent/memory_store.db"
# 注意：重启后会清空，如需持久化改为绝对路径
```

## 自定义配置

修改此文件后，下次调用脚本时自动生效。
如需修改数据库路径，编辑 `scripts/` 目录下的各文件。
