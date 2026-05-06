# MemoryHancement  - 基于 character n-gram Jaccard 的增强版记忆管理系统

基于 **character n-gram Jaccard** 的记忆解析、索引、存储和检索服务，支持去重合并、PPR 传播检索和动态遗忘机制。

## 概述

MemoryHancement 是一个高效的 Agent 记忆管理系统，集成 jieba TF-IDF 算法进行关键词提取（带 stopwords 过滤），在存储和检索层面引入图结构驱动的动态记忆演化机制。

### 核心功能

- **解析（parse）**：jieba TF-IDF 关键词提取（stopwords 过滤）
- **去重存储（store）**：keyword Jaccard overlap 判断是否合并到已有 context
- **概率传播检索（query）**：Personalized PageRank（PPR）风格的语义扩散（V5 升级为 Stable Top-K）
- **动态权重更新**：共现次数驱动边权增长，时间衰减
- **自动遗忘**：权重 < 0.3 的边自动删除
- **Context-Context**：检索时通过 context_context 边扩展相似记忆网络

## 技术架构

```
用户提问/回答 → Agent
                    │
                    ├── parse() → 提取关键词（jieba TF-IDF + stopwords 过滤）
                    │
                    ├── query() ──────────────────────────┐
                    │    │                                │
                    │    ├── Keyword Gating（freq<2 过滤）│
                    │    ├── Jaccard 匹配 keyword 节点       │
                    │    ├── Stable Top-K PPR（V5: K=50 + L1 + Teleport）│
                    │    ├── Context→Context 二跳扩展（V5: depth=2）│
                    │    ├── Dual-Channel Scoring（V5: kw×PPR×recency）│
                    │    └── text fallback（空结果兜底）    │
                    │                                      │
                    └── store() ←──────────────────────────┘
                         │
                         ├── keyword overlap 得分 ≥ 0.1 → 合并到已有 context
                         ├── keyword Jaccard ≥ 0.55 → 合并到已有 keyword 节点
                         ├── 更新 keyword_keyword 共现边
                         ├── Edge Renormalization（V5: outgoing edges 归一化）
                         └── 固定 128 维 SHA256-Hash encoding（维度永不膨胀）
```

## 三层图结构

| 层级 | 类型 | 说明 |
|------|------|------|
| L0 | agent | Agent 标识符节点 |
| L1 | keyword | 关键词/短语节点（Concept） |
| L2 | context | 记忆节点（支持多记忆合并的 Context） |

## 边类型

| 边类型 | 连接关系 | 权重来源 |
|--------|---------|---------|
| `agent_keyword` | agent → keyword | 固定 1.0 |
| `keyword_context` | keyword → context | 关联记忆数 + 访问权重 |
| `keyword_keyword` | keyword ↔ keyword | 共现次数 |
| `context_context` | context ↔ context | Jaccard 相似度（V5 新增） |

## 阈值配置

| 参数 | 值 | 说明 |
|------|---|---|
| `KW_JACCARD_THRESHOLD` | 0.55 | keyword Jaccard 相似度（入库时合并） |
| `CTX_OVERLAP_THRESHOLD` | 0.1 | context keyword overlap 得分（合并触发） |
| `MAX_MEMORIES_PER_CONTEXT` | 10^9 | 单个 Context 最大记忆条数 |
| `DECAY_HALF_LIFE_HOURS` | 168 | 边权重半衰期（7 天） |
| `DELETE_THRESHOLD` | 0.3 | 边权重低于此值则删除 |
| `GRACE_PERIOD_DAYS` | 0 | 新记忆保护期 |
| `PPR_BOOST_RATE` | 0.1 | PPR 边权重增强率 |
| `PPR_ITERATIONS` | 10 | PPR 迭代轮数 |
| `PPR_DAMPING` | 0.85 | PPR 阻尼因子 |
| `ENCODING_DIM` | 128 | 固定 encoding 维度（SHA256-Hash） |
| `TOP_K` | 50 | PPR beam width（每轮迭代保留 top-50 节点） |
| `CONVERGENCE_THRESHOLD` | 1e-4 | PPR L1 收敛阈值 |
| `CC_DAMPING` | 0.8 | Context-Context 传播阻尼因子 |
| `ALPHA` | 0.4 | Keyword 匹配权重（三通道评分） |
| `BETA` | 0.5 | PPR 传播权重（三通道评分） |
| `GAMMA` | 0.1 | 时效性权重（三通道评分） |

## 文件结构

```
skills/memoryHancement/
├── SKILL.md                 # 本文件
├── memory_skill.py          # V5 核心实现（Stable Top-K PPR + CC Graph + Dual-Channel Scoring）
├── memory_skill.py.v4.bak   # V4 备份
├── run_migrate.py           # 一次性迁移脚本（清理 sig + 重建 encoding）
├── data/
│   └── graph_memory.db       # SQLite 图数据库
```

## 数据库 Schema

| 表 | 关键字段 |
|----|---------|
| contexts | context_id, topic, encoding(512B), original_text, access_count, is_pinned, created_at, updated_at |
| keywords | keyword_id, label, encoding(512B) |
| edges | source_id, target_id, edge_type, weight, label, ctx_topic（UNIQUE 约束防重；V5 CHECK 新增 context_context） |
| metadata | key, value（存 store_count 等全局状态） |

**简化说明：**
- `topic` = 唯一关键词容器，append 时从 original_text 重建（包含所有记忆的关键词）
- `original_text` = 唯一数据载体，格式 `Q: ...\nA: ...\n---\n...`，answers 从中解析展示


## 使用方式

### 消息格式调用（通过 mem agent）

**查询：**
```
查询记忆：用户问题
```

**存储：**
```json
{"type":"store","question":"用户问题","answer":"AI回答"}
```

**统计：**
```json
{"type":"stats"}
```

**清理：**
```json
{"type":"prune"}
```

### 直接调用

```python
from memory_skill import MemorySkill

skill = MemorySkill()

# 解析关键词
keywords = skill.parse("用户想学习Python做深度学习")

# 存储（自动去重合并）
result = skill.store(
    question="Python是什么",
    answer="Python是一门高级编程语言，易学易用"
)

# 查询（Stable Top-K PPR + CC 二跳扩展 + 三通道评分）
result = skill.query("Python能做什么", top_k=5)

# 统计
stats = skill.get_stats()

skill.close()
```
