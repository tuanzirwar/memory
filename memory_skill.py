"""
MemoryHancement V5 - 基于 character n-gram Jaccard 的增强版记忆管理系统

改造要点（V4 → V5）：
1. Top-K PPR（K=50）：每轮迭代截断到 Top-50 节点，防止噪音扩散
2. Edge Weight Renormalization：所有 outgoing edges 归一化到和为1，防止 older edges dominate
3. Context-Context Graph：新增 context_context 边类型（共享≥2关键词或Jaccard≥0.3）
4. Query Graph Expansion：PPR 传播后增加 Context→Context 二跳扩展（depth=2）
5. Dual-Channel Scoring：三通道加权评分（keyword_match × PPR × recency）
6. Keyword Routing Gating：过滤图出现频率<2的弱关键词

【重要修复记录 V5】：
- schema：edges 表 CHECK 约束新增 context_context 边类型
- PPR：L1 norm + Top-K + Teleport injection + 收敛判断
- Edge Renormalization：outgoing edges 归一化，防止权重倾斜
- CC边：_build_context_context_edges() 增量构建，skip 已有
"""

import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
import os

import numpy as np
import re
import jieba
import jieba.analyse
from gensim.models import Word2Vec

# 预编译正则
_CHINESE_PAT = re.compile(r"[\u4e00-\u9fff]{2,10}")
_NUM_PAT = re.compile(r"^\d+$")
_DECAY_HALF_LIFE_HOURS = 168
_ENCODING_DIM = 128  # 固定编码维度（float32 数量）

DB_PATH = "/home/gem/workspace/agent/workspace-mem/skills/memoryHancement/data/graph_memory.db"
W2V_MODEL_PATH = "/home/gem/workspace/agent/workspace-mem/skills/memoryHancement/data/word2vec.model"

# ── 阈值配置 ────────────────────────────────────────────────────────────────
CANDIDATE_TOP_K = 10
TEXT_SIMILARITY_THRESHOLD = 0.75   # context 合并阈值（Jaccard keyword overlap）
KW_ENCODING_SIM_THRESHOLD = 0.55  # keyword Jaccard 相似度阈值（入库时合并）
MAX_MEMORIES_PER_CONTEXT = 10000000000
DECAY_HALF_LIFE_HOURS = 168

# ── V5 新增阈值配置 ─────────────────────────────────────────────────────────
TOP_K = 50                    # PPR beam width（每轮迭代保留 top-50 节点）
CONVERGENCE_THRESHOLD = 1e-4  # PPR L1 收敛阈值
CC_DAMPING = 0.8             # Context-Context 传播阻尼因子
ALPHA = 0.4                  # Keyword 匹配权重
BETA = 0.5                   # PPR 传播权重
GAMMA = 0.1                  # 时效性权重

# ── 衰减配置 ────────────────────────────────────────────────────────────────
DECAY_RATE = 0.1
ACCESS_BOOST = 0.2
DELETE_THRESHOLD = 0.3
GRACE_PERIOD_DAYS = 0
PPR_BOOST_RATE = 0.1


# ────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────────────────────────────────
def _vec_to_blob(vec: np.ndarray) -> bytes:
    """numpy向量 → blob（用于sqlite存储）"""
    return vec.astype(np.float32).tobytes()


def _blob_to_vec(blob: bytes, dim: int) -> np.ndarray:
    """blob → numpy向量（固定维度）"""
    return np.frombuffer(blob, dtype=np.float32)[:dim]


# ── 停用词：记忆系统中语义贡献低的常用词（不做合并判断依据）
STOPWORDS = {
    # 代词/主语（无区分度）
    '用户', '我', '你', '他', '她', '它', '我们', '你们', '他们',
    '自己', '本人', '大家', '这个', '那个', '某', '谁',
    # 疑问词（无信息量）
    '什么', '怎么', '如何', '为什么', '为啥', '是不是', '吗', '呢', '吧', '啊', '哦', '嗯', '呀', '哇', '啦', '噢', '嘛',
    # 通用名词（到处出现）
    '问题', '事情', '情况', '东西', '方面', '内容', '结果', '原因',
    '时候', '时间', '之后', '之前', '现在', '目前', '今天', '昨天', '明天', '当天',
    # 通用动词（无主题性）
    '喜欢', '爱', '讨厌', '不喜欢', '想', '要', '做', '进行', '使用', '通过',
    '开始', '继续', '说说', '告诉', '一下', '看看', '试试', '查查看看',
    '获取', '找到', '得到', '回复', '提出', '表示', '说明',
    '了解', '理解', '知道', '觉得', '认为', '感觉', '看来', '发现', '认识',
    '可以', '没有', '有的', '一个', '一些', '有点', '比较',
    '查看', '查询', '搜索', '处理', '解决', '回答', '方式', '类型', '方法', '状态',
    # 副词/连词
    '但是', '而且', '所以', '因为', '如果', '虽然', '然后', '还是',
    '是否', '也许', '可能', '应该', '需要', '能够', '必须',
    # 量词/结构词
    '一个', '一下', '一点', '一样', '一定', '这个', '那个', '每次', '各种',
    # 单字（TF-IDF 残留）
    '的', '地', '得', '了', '着', '过', '是', '在', '有', '和', '与',
    '或', '但', '而', '等', '被', '把', '给', '让', '请', '对', '就',
    # 认知/理解类
    '说', '提及', '提及到',
    # store 确认消息中的通用词
    '记住', '记录', '已记录', '已经', '好的', '了解', '明白', '知道了',
    '好', '哒', '啦', '呢', '啊', '呀', '哦', '呵',
    # 泛化 discourse markers（导致噪音 entries 误合并）
    '顺便', '顺便说一下', '几个', '想要', '哪些', '注意', '操作',
    '怎么样', '不错', '舒服', '心情', '有点累', '精神',
}


def _jaccard_ngram(text1: str, text2: str, n: int = 3) -> float:
    """计算两个文本的 character n-gram Jaccard 相似度（不受词表影响）"""
    def ngrams(t: str) -> set:
        t = t.strip()
        return set(t[i:i+n] for i in range(max(1, len(t)-n+1)))
    if not text1 or not text2:
        return 0.0
    ng1 = ngrams(text1)
    ng2 = ngrams(text2)
    if not ng1 or not ng2:
        return 0.0
    inter = len(ng1 & ng2)
    union = len(ng1 | ng2)
    return inter / union if union > 0 else 0.0


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的 cosine similarity"""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ────────────────────────────────────────────────────────────────────────────
# MemorySkill V5 - 升级版
# ────────────────────────────────────────────────────────────────────────────
class MemorySkill:
    """基于 character n-gram Jaccard 的增强版记忆管理（V5 升级版）"""

    DEFAULT_WEIGHT = 1.0
    AGENT_ID = "202605011540"

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_init()
        # V5: 首次初始化时构建 Context-Context 边（增量）
        self._build_context_context_edges(force=False)
        # 加载或训练Word2Vec模型
        self.w2v_model = self._load_or_train_w2v()

    def _load_or_train_w2v(self) -> Word2Vec:
        """加载已有Word2Vec模型，或从现有记忆语料训练新模型"""
        if os.path.exists(W2V_MODEL_PATH):
            return Word2Vec.load(W2V_MODEL_PATH)
        
        # 从数据库提取所有文本作为训练语料
        conn = self._get_conn()
        # 获取所有context的文本
        ctx_rows = conn.execute("SELECT original_text, topic FROM contexts").fetchall()
        # 获取所有keyword
        kw_rows = conn.execute("SELECT label FROM keywords").fetchall()
        
        sentences = []
        # 处理context文本
        for (orig_text, topic) in ctx_rows:
            if orig_text:
                sentences.append(jieba.lcut(orig_text))
            if topic:
                sentences.append(jieba.lcut(topic))
        # 处理keyword
        for (label,) in kw_rows:
            if label:
                sentences.append(jieba.lcut(label))
        
        # 如果没有语料，初始化空模型
        if not sentences:
            sentences = [['默认', '语料']]
        
        # 训练Word2Vec模型，维度和原来保持一致128维
        model = Word2Vec(
            sentences=sentences,
            vector_size=_ENCODING_DIM,
            window=5,
            min_count=1,
            workers=4,
            sg=1  # 使用skip-gram，适合小语料
        )
        model.save(W2V_MODEL_PATH)
        return model

    def _update_w2v_model(self, new_text: str) -> None:
        """新增文本时更新Word2Vec模型"""
        if not new_text or not new_text.strip():
            return
        new_sentence = jieba.lcut(new_text)
        self.w2v_model.build_vocab([new_sentence], update=True)
        self.w2v_model.train([new_sentence], total_examples=1, epochs=self.w2v_model.epochs)
        self.w2v_model.save(W2V_MODEL_PATH)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ────────────────────────────────────────────────────────────────────────
    # Word2Vec 语义编码（替代Hash编码，保留语义信息）
    # ────────────────────────────────────────────────────────────────────────
    def _fixed_encode(self, text: str) -> np.ndarray:
        """将文本编码为固定 128 维 Word2Vec 语义向量"""
        if not text or not text.strip():
            return np.zeros(_ENCODING_DIM, dtype=np.float32)
        
        words = jieba.lcut(text)
        vecs = []
        for word in words:
            if word in self.w2v_model.wv:
                vecs.append(self.w2v_model.wv[word])
        
        if not vecs:
            # OOV情况返回随机向量，避免全零
            return np.random.rand(_ENCODING_DIM).astype(np.float32) * 0.1
        
        # 取词向量的平均值作为句子向量
        mean_vec = np.mean(vecs, axis=0).astype(np.float32)
        # L2归一化，和原来的Hash编码保持一致的数值范围
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        return mean_vec

    def _encode(self, text: str) -> np.ndarray:
        """对外暴露的编码接口（内部使用固定 hash 编码）"""
        return self._fixed_encode(text)

    # ────────────────────────────────────────────────────────────────────────
    # 数据库初始化
    # ────────────────────────────────────────────────────────────────────────
    def _ensure_init(self) -> None:
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contexts'"
        )
        if not cursor.fetchone():
            self._create_schema()
            conn.execute(
                "INSERT INTO agents (agent_id, label) VALUES (?, ?)",
                (f"agent_{self.AGENT_ID}", self.AGENT_ID)
            )
            conn.commit()
        else:
            # V5 迁移：确保 edges 表 CHECK 包含 context_context 边类型
            self._migrate_add_context_context_edge_type()

    def _migrate_add_context_context_edge_type(self) -> None:
        """V5 迁移：为已有数据库的 edges 表重建 schema（添加 context_context 边类型）"""
        conn = self._get_conn()
        # 检查当前 edges 表 CHECK 约束是否已支持 context_context
        # 使用普通 INSERT（非 OR IGNORE）确保 CHECK 违规会正确抛出 IntegrityError
        try:
            test_id = "__cc_migrate_check__"
            conn.execute(
                "INSERT INTO edges (edge_id, source_id, target_id, edge_type, weight) VALUES (?, ?, ?, 'context_context', 0.0)",
                (test_id, test_id, test_id)
            )
            conn.execute("DELETE FROM edges WHERE edge_id = ?", (test_id,))
            conn.commit()
            return  # CHECK 已支持 context_context，无需迁移
        except sqlite3.IntegrityError:
            conn.rollback()  # CHECK 不支持 context_context，需要重建 edges 表
        except sqlite3.Error:
            conn.rollback()

        # 重建 edges 表：备份数据 → 删除旧表 → 创建新表（临时不加 CHECK）→ 恢复数据 → 加 CHECK
        # 临时不加 CHECK 是为了避免 INSERT OR IGNORE 静默丢弃数据
        try:
            # 1. 备份现有 edges 数据
            backup_rows = conn.execute(
                "SELECT edge_id, source_id, target_id, edge_type, weight, label, ctx_topic, properties, created_at, last_accessed FROM edges"
            ).fetchall()
            edge_count_before = len(backup_rows)

            # 2. 删除旧 edges 表
            conn.execute("DROP TABLE edges")

            # 3. 创建新的 edges 表（CHECK 包含 context_context）
            conn.execute("""
                CREATE TABLE edges (
                    edge_id       TEXT PRIMARY KEY,
                    source_id     TEXT NOT NULL,
                    target_id     TEXT NOT NULL,
                    edge_type     TEXT NOT NULL
                                  CHECK (edge_type IN (
                                      'agent_keyword',
                                      'keyword_context',
                                      'keyword_keyword',
                                      'context_context'
                                  )),
                    weight        REAL NOT NULL DEFAULT 1.0,
                    label         TEXT,
                    ctx_topic     TEXT,
                    properties    TEXT,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP,
                    UNIQUE(source_id, target_id, edge_type)
                )
            """)

            # 4. 重建索引
            conn.execute("CREATE INDEX idx_edges_source ON edges(source_id)")
            conn.execute("CREATE INDEX idx_edges_target ON edges(target_id)")
            conn.execute("CREATE INDEX idx_edges_type ON edges(edge_type)")

            # 5. 恢复数据（批量插入避免单条 UNIQUE 冲突）
            restored = 0
            skipped_duplicate = 0
            for row in backup_rows:
                try:
                    conn.execute(
                        "INSERT INTO edges (edge_id, source_id, target_id, edge_type, weight, label, ctx_topic, properties, created_at, last_accessed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9])
                    )
                    restored += 1
                except sqlite3.IntegrityError as e:
                    # UNIQUE 冲突：忽略重复边（V4 可能有重复边，由 _create_edge 的 UNIQUE 保证不会新增）
                    skipped_duplicate += 1

            conn.commit()

            # 6. 验证恢复结果
            edge_count_after = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            print(f"[V5 Migration] Restored {restored}/{edge_count_before} edges (skipped {skipped_duplicate} duplicates)")

        except sqlite3.Error as e:
            conn.rollback()
            raise e

    def _create_schema(self) -> None:
        conn = self._get_conn()

        # ── metadata 表：存储全局状态 ───────────────────────────────────────
        conn.execute("""
            CREATE TABLE metadata (
                key    TEXT PRIMARY KEY,
                value  BLOB
            )
        """)

        # ── agents 表 (L0) ───────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE agents (
                agent_id   TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)

        # ── keywords 表 (L1) ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE keywords (
                keyword_id TEXT PRIMARY KEY,
                label      TEXT NOT NULL UNIQUE,
                encoding   BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)

        # ── contexts 表 (L2) ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE contexts (
                context_id        TEXT PRIMARY KEY,
                topic             TEXT NOT NULL,
                encoding          BLOB,
                original_text     TEXT NOT NULL,
                access_count      INTEGER NOT NULL DEFAULT 0,
                is_pinned         INTEGER NOT NULL DEFAULT 0,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at        TIMESTAMP
            )
        """)

        # ── edges 表（带 UNIQUE 约束防止重复边）────────────────────────────
        # V5: CHECK 约束新增 context_context 边类型
        conn.execute("""
            CREATE TABLE edges (
                edge_id       TEXT PRIMARY KEY,
                source_id     TEXT NOT NULL,
                target_id     TEXT NOT NULL,
                edge_type     TEXT NOT NULL
                              CHECK (edge_type IN (
                                  'agent_keyword',
                                  'keyword_context',
                                  'keyword_keyword',
                                  'context_context'
                              )),
                weight        REAL NOT NULL DEFAULT 1.0,
                label         TEXT,
                ctx_topic     TEXT,
                properties    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP,
                UNIQUE(source_id, target_id, edge_type)
            )
        """)

        # ── 索引 ────────────────────────────────────────────────────────
        conn.execute("CREATE INDEX idx_contexts_topic ON contexts(topic)")
        conn.execute("CREATE INDEX idx_keywords_label ON keywords(label)")
        conn.execute("CREATE INDEX idx_edges_source ON edges(source_id)")
        conn.execute("CREATE INDEX idx_edges_target ON edges(target_id)")
        conn.execute("CREATE INDEX idx_edges_type ON edges(edge_type)")

    def _generate_id(self, prefix: str) -> str:
        return f"{prefix}_{str(uuid.uuid4())[:8]}"

    # ────────────────────────────────────────────────────────────────────────
    # V5: Edge Weight Renormalization
    # ────────────────────────────────────────────────────────────────────────
    def _normalize_outgoing_edges(self, node_id: str) -> None:
        """将指定节点的所有 outgoing edges 权重归一化到和为1，防止 older edges dominate"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT edge_id, weight FROM edges WHERE source_id = ?", (node_id,)
            ).fetchall()
            if not rows:
                conn.close()
                return
            total = sum(r[1] for r in rows)
            if total <= 0 or abs(total - 1.0) < 1e-6:
                conn.close()
                return
            for (edge_id, weight) in rows:
                conn.execute(
                    "UPDATE edges SET weight = ? WHERE edge_id = ?",
                    (weight / total, edge_id)
                )
            conn.commit()
        finally:
            conn.close()

    # ────────────────────────────────────────────────────────────────────────
    # V5: Context-Context Graph 构建
    # ────────────────────────────────────────────────────────────────────────
    def _build_context_context_edges(self, force: bool = False) -> Dict[str, Any]:
        """构建 context-context 边：共享≥2个关键词或Jaccard≥0.3（增量构建）"""
        conn = self._get_conn()

        # 检查是否需要强制重建（force=True）
        if not force:
            existing = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE edge_type = 'context_context'"
            ).fetchone()[0]
            if existing > 0:
                return {'existing_edges': existing, 'action': 'skip'}

        ctx_rows = conn.execute(
            "SELECT context_id, topic FROM contexts"
        ).fetchall()

        contexts = []
        for (ctx_id, topic) in ctx_rows:
            topic_kws = {k.strip().lower() for k in topic.split(' / ') if k.strip()}
            contexts.append({'context_id': ctx_id, 'keywords': topic_kws})

        created = 0
        for i in range(len(contexts)):
            for j in range(i + 1, len(contexts)):
                ctx_i = contexts[i]
                ctx_j = contexts[j]

                overlap = len(ctx_i['keywords'] & ctx_j['keywords'])

                # 条件1：共享≥2个关键词
                if overlap >= 2:
                    weight = overlap / len(ctx_i['keywords'] | ctx_j['keywords'])
                    self._create_edge(
                        ctx_i['context_id'], ctx_j['context_id'],
                        edge_type='context_context', weight=weight,
                        label=f"ctx_ctx_overlap_{overlap}",
                        properties={'overlap_count': overlap}
                    )
                    created += 1
                    continue

                # 条件2：Jaccard ≥ 0.3（当关键词数较少时）
                union_count = len(ctx_i['keywords'] | ctx_j['keywords'])
                if union_count > 0:
                    jaccard = overlap / union_count
                    if jaccard >= 0.3 and overlap >= 1:
                        self._create_edge(
                            ctx_i['context_id'], ctx_j['context_id'],
                            edge_type='context_context', weight=jaccard,
                            label=f"ctx_ctx_jaccard_{jaccard:.2f}",
                            properties={'jaccard': jaccard}
                        )
                        created += 1

        conn.commit()
        return {'new_edges_created': created, 'action': 'built'}

    # ────────────────────────────────────────────────────────────────────────
    # 主动记忆检测
    # ────────────────────────────────────────────────────────────────────────
    _ACTIVE_Q_PATTERNS = [
        r'记住', r'记下', r'记下来', r'帮我记', r'请记', r'麻烦记',
        r'收藏', r'收下', r'保存', r'存一下', r'存起来',
        r'保存到记忆', r'保存到我的记忆', r'加入记忆', r'加入我的记忆',
        r'这个(我要|要|请)记住', r'这个重要', r'这点很重要', r'这条要记',
        r'重要', r'很重要', r'特别重要', r'千万别忘', r'不要忘', r'不要忘记',
        r'永远记住', r'务必记住', r'一定记住', r'一定要记住',
        r'这(件|点|条)要记住', r'这条规则', r'这个规则',
        r'提醒我', r'以后提醒我',
    ]
    _ACTIVE_A_PATTERNS = [
        r'已永久保存', r'已加入记忆库', r'已保存到记忆', r'已加入我的记忆',
        r'已记入永久记忆', r'已收藏',
    ]

    def _detect_active_memory(self, question: str, answer: str = "") -> bool:
        q = question.lower()
        a = answer.lower()
        for pat in self._ACTIVE_Q_PATTERNS:
            if re.search(pat, q):
                return True
        for pat in self._ACTIVE_A_PATTERNS:
            if re.search(pat, a):
                return True
        return False

    # ────────────────────────────────────────────────────────────────────────
    # 解析 - 基于 jieba TF-IDF
    # ────────────────────────────────────────────────────────────────────────
    def parse(self, text: str, max_keywords: int = 5) -> List[str]:
        if not text or not text.strip():
            return []

        keywords = jieba.analyse.extract_tags(
            text,
            topK=max_keywords,
            withWeight=True,
            allowPOS=()
        )

        seen: Set[str] = set()
        result: List[str] = []
        for kw, _ in keywords:
            kw_lower = kw.lower()
            if kw_lower in seen:
                continue
            if kw_lower in STOPWORDS:
                continue
            if _NUM_PAT.match(kw):
                continue
            if len(kw) < 2:
                continue
            if re.match(r'^[a-zA-Z]+$', kw) and len(kw) < 3:
                continue
            seen.add(kw_lower)
            result.append(kw)
            if len(result) >= max_keywords:
                break

        return result

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        if not text1 or not text2:
            return 0.0
        text1, text2 = text1.strip(), text2.strip()
        if text1 == text2:
            return 1.0
        if len(text1) <= 2 or len(text2) <= 2:
            return 1.0 if text1 in text2 or text2 in text1 else 0.0
        v1 = self._encode(text1)
        v2 = self._encode(text2)
        return _cosine_sim(v1, v2)

    # ────────────────────────────────────────────────────────────────────────
    # 节点操作
    # ────────────────────────────────────────────────────────────────────────
    def _find_similar_keywords(self, keyword: str,
                                threshold: float = KW_ENCODING_SIM_THRESHOLD
                                ) -> List[Dict]:
        """用 character n-gram Jaccard 找到相似的已有 keyword"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT keyword_id, label FROM keywords"
        ).fetchall()
        scored = []
        for (kw_id, label) in rows:
            if (len(label) >= 2 and keyword in label) or (len(keyword) >= 2 and label in keyword):
                scored.append({
                    'keyword_id': kw_id,
                    'label': label,
                    'similarity': 0.99
                })
                continue
            sim = _jaccard_ngram(keyword, label, n=3)
            sim2 = _jaccard_ngram(keyword, label, n=2)
            best_sim = max(sim, sim2)
            if best_sim >= threshold:
                scored.append({
                    'keyword_id': kw_id,
                    'label': label,
                    'similarity': best_sim
                })
        scored.sort(key=lambda x: x['similarity'], reverse=True)
        return scored

    def _get_or_create_keyword(self, keyword: str) -> Dict:
        """获取或创建 keyword：先做 Jaccard 相似匹配，高相似度则复用"""
        conn = self._get_conn()

        row = conn.execute(
            "SELECT keyword_id, encoding FROM keywords WHERE label = ?", (keyword,)
        ).fetchone()
        if row:
            return {'keyword': keyword, 'keyword_id': row[0], 'created': False,
                    'match_type': 'exact'}

        similar = self._find_similar_keywords(keyword, threshold=KW_ENCODING_SIM_THRESHOLD)
        if similar:
            best = similar[0]
            return {
                'keyword':     best['label'],
                'keyword_id':  best['keyword_id'],
                'created':     False,
                'match_type':  'jaccard_merge',
                'merged_from': keyword,
                'similarity':  best['similarity']
            }

        kw_id = self._generate_id("kw")
        vec = self._encode(keyword)
        blob = _vec_to_blob(vec)
        conn.execute(
            "INSERT INTO keywords (keyword_id, label, encoding) VALUES (?, ?, ?)",
            (kw_id, keyword, blob)
        )

        agent_pk = f"agent_{self.AGENT_ID}"
        edge_id = self._generate_id("edge")
        try:
            conn.execute("""
                INSERT INTO edges (edge_id, source_id, target_id, edge_type, weight, last_accessed)
                VALUES (?, ?, ?, 'agent_keyword', ?, ?)
            """, (edge_id, agent_pk, kw_id, self.DEFAULT_WEIGHT, datetime.now().isoformat()))
        except sqlite3.IntegrityError:
            pass

        conn.commit()
        return {'keyword': keyword, 'keyword_id': kw_id, 'created': True, 'match_type': 'new'}

    def _edge_exists(self, source_id: str, target_id: str, edge_type: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM edges WHERE source_id=? AND target_id=? AND edge_type=?",
            (source_id, target_id, edge_type)
        ).fetchone()
        return row is not None

    def _create_edge(self, source_id: str, target_id: str,
                     edge_type: str, weight: float = 1.0,
                     label: str = None,
                     ctx_topic: str = None,
                     properties: Dict = None) -> str:
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT edge_id FROM edges WHERE source_id=? AND target_id=? AND edge_type=?",
            (source_id, target_id, edge_type)
        ).fetchone()
        if existing:
            return existing[0]

        edge_id = self._generate_id("edge")
        conn.execute("""
            INSERT INTO edges (edge_id, source_id, target_id, edge_type, weight,
                               label, ctx_topic, properties, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (edge_id, source_id, target_id, edge_type, weight,
              label, ctx_topic,
              json.dumps(properties or {}), datetime.now().isoformat()))
        conn.commit()
        return edge_id

    def _generate_topic_label(self, keywords) -> str:
        if isinstance(keywords, set):
            keywords = list(keywords)
        kw_list = [k for k in keywords if k and k not in STOPWORDS]
        return " / ".join(sorted(kw_list)) if kw_list else "general"

    # ────────────────────────────────────────────────────────────────────────
    # original_text 解析 & topic 重建
    # ────────────────────────────────────────────────────────────────────────
    def _parse_original_text(self, raw: str) -> List[Dict[str, str]]:
        if not raw:
            return []
        pairs = []
        blocks = raw.split('\n---\n')
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            q_line, a_line = None, None
            for line in block.split('\n'):
                line = line.strip()
                if line.startswith('Q:'):
                    q_line = line[2:].strip()
                elif line.startswith('A:'):
                    a_line = line[2:].strip()
            if q_line is not None and a_line is not None:
                pairs.append({'question': q_line, 'answer': a_line})
            elif q_line is not None:
                pairs.append({'question': q_line, 'answer': ''})
        return pairs

    def _rebuild_topic(self, original_text: str) -> str:
        pairs = self._parse_original_text(original_text)
        all_kw: Set[str] = set()
        for p in pairs:
            kws = self.parse(p.get('question', '') or '', max_keywords=8)
            all_kw.update(k.lower() for k in kws)
        if not all_kw:
            return 'general'
        return ' / '.join(sorted(all_kw))

    # ────────────────────────────────────────────────────────────────────────
    # Context 操作
    # ────────────────────────────────────────────────────────────────────────
    def _find_similar_contexts_by_keyword_overlap(self, new_keywords: Set[str],
                                               threshold: float = 0.3
                                               ) -> List[Dict]:
        conn = self._get_conn()
        content_kw = {k for k in new_keywords if k not in STOPWORDS}
        if not content_kw:
            return []

        rows = conn.execute(
            "SELECT context_id, topic FROM contexts"
        ).fetchall()

        scored = []
        for (ctx_id, topic) in rows:
            topic_kws = {k.strip().lower() for k in topic.split(' / ') if k.strip()}
            topic_content = {k for k in topic_kws if k not in STOPWORDS}

            if not topic_content:
                continue

            overlap = len(content_kw & topic_content)
            score = len(content_kw & topic_content) / len(content_kw | topic_content) if (content_kw | topic_content) else 0.0

            if score >= threshold:
                scored.append({
                    'context_id':   ctx_id,
                    'topic':        topic,
                    'keyword_overlap_score': score,
                    'overlap_count': len(content_kw & topic_content),
                    'new_content_kw': sorted(content_kw),
                    'covered_ratio': f"{overlap}/{len(content_kw)}"
                })
        scored.sort(key=lambda x: x['keyword_overlap_score'], reverse=True)
        return scored[:CANDIDATE_TOP_K]

    def _append_to_context(self, ctx: Dict, question: str, answer: str,
                           keywords_set: Set[str],
                           question_keywords: List[str] = None,
                           is_pinned: bool = False) -> Dict[str, Any]:
        conn = self._get_conn()

        row = conn.execute(
            "SELECT original_text FROM contexts WHERE context_id = ?",
            (ctx['context_id'],)
        ).fetchone()

        if not row:
            return {'action': 'error', 'message': 'context not found'}

        existing_raw = row[0] or ''
        pairs = self._parse_original_text(existing_raw)
        if len(pairs) >= MAX_MEMORIES_PER_CONTEXT:
            return {'action': 'full', 'need_new': True}

        pairs.append({'question': question, 'answer': answer})

        new_raw = f"Q: {question}\nA: {answer}"
        raw_text = (existing_raw + "\n---\n" + new_raw).strip() if existing_raw else new_raw

        topic = self._rebuild_topic(raw_text)

        new_topic_vec = self._encode(topic)
        new_topic_blob = _vec_to_blob(new_topic_vec)

        conn.execute("""
            UPDATE contexts SET
                topic        = ?,
                encoding     = ?,
                original_text= ?,
                is_pinned    = MAX(is_pinned, ?),
                updated_at   = CURRENT_TIMESTAMP
            WHERE context_id = ?
        """, (topic, new_topic_blob, raw_text, 1 if is_pinned else 0, ctx['context_id']))

        for kw in keywords_set:
            if not kw or kw.lower() in STOPWORDS:
                continue
            kw_node = self._get_or_create_keyword(kw)
            self._create_edge(
                kw_node['keyword_id'], ctx['context_id'],
                edge_type='keyword_context', weight=1.0,
                label=kw, ctx_topic=topic
            )
            conn.execute("""
                UPDATE edges SET
                    weight = weight + 0.1,
                    last_accessed = ?
                WHERE source_id = ? AND target_id = ? AND edge_type = 'keyword_context'
            """, (datetime.now().isoformat(), kw_node['keyword_id'], ctx['context_id']))

        conn.commit()
        return {
            'action': 'merged',
            'merged_into': ctx['context_id'],
            'memory_count': len(pairs),
            'topic': topic,
        }

    def _create_new_context(self, question: str, answer: str,
                             keywords: List[str],
                             keywords_set: Set[str],
                             question_keywords: List[str] = None,
                             is_pinned: bool = False) -> Dict[str, Any]:
        conn = self._get_conn()

        topic_kw = question_keywords if question_keywords else keywords_set
        topic = self._generate_topic_label(topic_kw)
        raw_text = f"Q: {question}\nA: {answer}"

        topic_vec = self._encode(topic)
        topic_blob = _vec_to_blob(topic_vec)

        ctx_id = self._generate_id("ctx")
        conn.execute("""
            INSERT INTO contexts
                (context_id, topic, encoding, original_text, is_pinned)
            VALUES (?, ?, ?, ?, ?)
        """, (ctx_id, topic, topic_blob, raw_text, 1 if is_pinned else 0))

        clean_kw_set = {k.lower() for k in keywords_set if k and k.lower() not in STOPWORDS}
        edges_count = 0
        for kw in clean_kw_set:
            kw_node = self._get_or_create_keyword(kw)
            self._create_edge(
                kw_node['keyword_id'], ctx_id,
                edge_type='keyword_context', weight=1.0,
                label=kw, ctx_topic=topic
            )
            edges_count += 1

        conn.commit()
        return {
            'action': 'created',
            'context_id': ctx_id,
            'topic': topic,
            'keywords_count': len(clean_kw_set),
            'edges_count': edges_count,
            'is_pinned': is_pinned
        }

    # ────────────────────────────────────────────────────────────────────────
    # 存储（核心入口）
    # ────────────────────────────────────────────────────────────────────────
    def store(self, question: str, answer: str) -> Dict[str, Any]:
        is_pinned = self._detect_active_memory(question, answer)

        decay_result = self._decay_all_edges()
        extra_result = self._maybe_extra_decay()
        prune_result = self._auto_prune_edges()

        combined_text = f"{question} {answer}"
        question_keywords = self.parse(question)
        keywords = self.parse(combined_text)
        keywords_set = set(k.lower() for k in keywords)

        # 回填已有 keyword（仅当长度>=2）
        conn = self._get_conn()
        all_db_kws = [r[0] for r in conn.execute(
            "SELECT label FROM keywords"
        ).fetchall()]
        for text in [question, answer]:
            for db_kw in all_db_kws:
                if len(db_kw) >= 2 and db_kw in text:
                    keywords_set.add(db_kw.lower())

        if not keywords_set:
            result = self._create_new_context(question, answer, [], keywords_set, question_keywords, is_pinned=is_pinned)
            result['decay'] = {**decay_result, **extra_result}
            result['prune'] = prune_result
            return result

        candidates = self._find_similar_contexts_by_keyword_overlap(keywords_set)

        best_match = candidates[0] if candidates else None

        if best_match:
            result = self._append_to_context(best_match, question, answer, keywords_set, question_keywords, is_pinned=is_pinned)
            if result.get('need_new'):
                all_kw_list = list(keywords_set)
                result = self._create_new_context(question, answer, all_kw_list, keywords_set, question_keywords, is_pinned=is_pinned)
        else:
            all_kw_list = list(keywords_set)
            result = self._create_new_context(question, answer, all_kw_list, keywords_set, question_keywords, is_pinned=is_pinned)

        self._update_keyword_cooccurrence(keywords)
        # 更新Word2Vec模型，加入新文本
        self._update_w2v_model(f"{question} {answer}")

        # V5: store 后做 edge renormalization
        self._normalize_all_keyword_outgoing_edges()

        result['decay'] = {**decay_result, **extra_result}
        result['prune'] = prune_result
        return result

    def _normalize_all_keyword_outgoing_edges(self) -> None:
        """V5: 遍历所有 keyword 节点，normalize 其 outgoing edges"""
        conn = self._get_conn()
        kw_rows = conn.execute("SELECT keyword_id FROM keywords").fetchall()
        for (kw_id,) in kw_rows:
            self._normalize_outgoing_edges(kw_id)

    def _maybe_extra_decay(self) -> Dict[str, Any]:
        conn = self._get_conn()

        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'store_count'"
        ).fetchone()
        count = int(row[0]) if row else 0
        count += 1
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('store_count', ?)",
            (str(count),)
        )
        conn.commit()

        if count % 10 != 0:
            return {'extra_decayed': 0, 'store_count': count, 'applied': False}

        EXTRA_DECAY_RATE = 0.01
        pinned_ctx_ids = set(
            r[0] for r in conn.execute(
                "SELECT context_id FROM contexts WHERE is_pinned = 1"
            ).fetchall()
        )
        if pinned_ctx_ids:
            placeholders = ','.join('?' * len(pinned_ctx_ids))
            rows = conn.execute(
                f"SELECT edge_id, weight FROM edges WHERE target_id NOT IN ({placeholders})",
                list(pinned_ctx_ids)
            ).fetchall()
        else:
            rows = conn.execute("SELECT edge_id, weight FROM edges").fetchall()

        for (edge_id, weight) in rows:
            conn.execute(
                "UPDATE edges SET weight = ? WHERE edge_id = ?",
                (weight * (1 - EXTRA_DECAY_RATE), edge_id)
            )
        conn.commit()
        return {'extra_decayed': len(rows), 'store_count': count, 'applied': True, 'rate': '1%'}

    def _update_keyword_cooccurrence(self, keywords: List[str]) -> None:
        conn = self._get_conn()
        for i, kw1 in enumerate(keywords):
            for kw2 in keywords[i+1:]:
                rows = conn.execute(
                    "SELECT keyword_id FROM keywords WHERE label IN (?, ?)",
                    (kw1, kw2)
                ).fetchall()
                if len(rows) != 2:
                    continue
                ids = [r[0] for r in rows]
                row = conn.execute("""
                    SELECT edge_id, weight FROM edges
                    WHERE source_id = ? AND target_id = ? AND edge_type = 'keyword_keyword'
                """, (ids[0], ids[1])).fetchone()

                if row:
                    conn.execute(
                        "UPDATE edges SET weight = weight + 0.1 WHERE edge_id = ?",
                        (row[0],)
                    )
                else:
                    self._create_edge(ids[0], ids[1],
                                    edge_type='keyword_keyword', weight=1.0,
                                    label=f"{kw1}|||{kw2}",
                                    properties={'co_occurrence_count': 1})
        conn.commit()
        # V5: keyword_keyword 边更新后 normalize source 节点
        for i, kw1 in enumerate(keywords):
            row = conn.execute("SELECT keyword_id FROM keywords WHERE label = ?", (kw1,)).fetchone()
            if row:
                self._normalize_outgoing_edges(row[0])

    # ────────────────────────────────────────────────────────────────────────
    # 查询（V5：Stable Top-K PPR + CC 二跳扩展 + Dual-Channel Scoring）
    # ────────────────────────────────────────────────────────────────────────
    def query(self, question: str, top_k: int = 5) -> Dict[str, Any]:
        decay_result = self._decay_all_edges()
        prune_result = self._auto_prune_edges()

        keywords = self.parse(question)
        keywords_set = set(k.lower() for k in keywords)

        # ── V5: Keyword Gating ───────────────────────────────────────────
        # 过滤图出现频率 < 2 的弱关键词（防止噪音扩散）
        if keywords_set:
            conn = self._get_conn()
            gated_kw_set = set()
            for kw in keywords_set:
                freq = conn.execute("""
                    SELECT COUNT(*) FROM edges
                    WHERE (source_id IN (SELECT keyword_id FROM keywords WHERE label = ?)
                           OR target_id IN (SELECT keyword_id FROM keywords WHERE label = ?))
                """, (kw, kw)).fetchone()[0]
                if freq >= 2:
                    gated_kw_set.add(kw)
            if gated_kw_set:
                keywords_set = gated_kw_set

        # ── 空关键词兜底：text fallback ───────────────────────────────────
        if not keywords_set:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT context_id, topic, original_text FROM contexts"
            ).fetchall()
            fallback_contexts = []
            for (ctx_id, topic, orig_text) in rows:
                if orig_text and question in orig_text:
                    fallback_contexts.append({
                        'context_id': ctx_id, 'topic': topic,
                        'original_text': orig_text,
                        'score': 0.5
                    })
                elif orig_text:
                    chinese_terms = _CHINESE_PAT.findall(question)
                    matched = next((t for t in chinese_terms if t in orig_text), None)
                    if matched:
                        fallback_contexts.append({
                            'context_id': ctx_id, 'topic': topic,
                            'original_text': orig_text,
                            'score': 0.4
                        })
            if fallback_contexts:
                return {
                    'keywords': [], 'matched_keywords': [],
                    'contexts': fallback_contexts[:top_k],
                    'scores': {c['context_id']: c['score'] for c in fallback_contexts[:top_k]},
                    'decay': decay_result, 'prune': prune_result,
                    'ppr_boosted_edges': 0
                }
            return {
                'keywords': [], 'contexts': [], 'scores': {},
                'decay': decay_result, 'prune': prune_result
            }

        # ── 阶段1：用 Jaccard 匹配 keyword 节点 ────────────────────────────
        conn = self._get_conn()
        all_kw_rows = conn.execute(
            "SELECT keyword_id, label FROM keywords"
        ).fetchall()

        kw_match_scores: Dict[str, float] = {}
        kw_to_contexts: Dict[str, List[Dict]] = {}

        for q_kw in keywords_set:
            for (kw_id, db_label) in all_kw_rows:
                is_noise_label = any(c.isdigit() or ('a' <= c <= 'z') or ('A' <= c <= 'Z') for c in db_label)
                if (not is_noise_label and len(db_label) >= 2 and q_kw in db_label) or (len(q_kw) >= 2 and db_label in q_kw):
                    best_sim = 0.99
                else:
                    sim = _jaccard_ngram(q_kw, db_label, n=3)
                    sim2 = _jaccard_ngram(q_kw, db_label, n=2)
                    best_sim = max(sim, sim2)
                if best_sim >= KW_ENCODING_SIM_THRESHOLD:
                    if db_label not in kw_match_scores or best_sim > kw_match_scores[db_label]:
                        kw_match_scores[db_label] = best_sim
                    if db_label not in kw_to_contexts:
                        ctx_rows = conn.execute("""
                            SELECT c.context_id, c.topic, e.weight, e.edge_id
                            FROM contexts c
                            JOIN edges e ON c.context_id = e.target_id
                            JOIN keywords k ON e.source_id = k.keyword_id
                            WHERE k.label = ? AND e.edge_type = 'keyword_context'
                        """, (db_label,)).fetchall()
                        kw_to_contexts[db_label] = [{
                            'context_id': r[0], 'label': r[1],
                            'weight': r[2] or 1.0, 'edge_id': r[3]
                        } for r in ctx_rows]

        # ── 阶段2：Stable Top-K PPR 传播 ─────────────────────────────────
        keyword_probs: Dict[str, float] = {}
        for kw in keywords_set:
            keyword_probs[kw] = kw_match_scores.get(kw, 0.0)
        for existing_kw, score in kw_match_scores.items():
            if existing_kw not in keyword_probs or score > keyword_probs[existing_kw]:
                keyword_probs[existing_kw] = max(keyword_probs.get(existing_kw, 0), score)

        edge_flows: Dict[str, float] = {}
        ITERATIONS = 10
        DAMPING = 0.85

        for iteration in range(ITERATIONS):
            new_probs: Dict[str, float] = {}
            for kw, prob in keyword_probs.items():
                if prob <= 0:
                    continue
                related = kw_to_contexts.get(kw, [])
                if not related:
                    continue
                for ctx in related:
                    edge_weight = ctx.get('weight', 1.0)
                    propagated = prob * edge_weight * DAMPING / len(related)
                    edge_id = ctx.get('edge_id')
                    if edge_id:
                        edge_flows[edge_id] = edge_flows.get(edge_id, 0) + abs(propagated)
                    ctx_id = ctx['context_id']
                    new_probs[ctx_id] = new_probs.get(ctx_id, 0) + propagated

            # V5: L1 normalization
            total = sum(new_probs.values())
            if total > 0:
                new_probs = {k: v/total for k, v in new_probs.items()}

            # V5: Top-K truncation
            if len(new_probs) > TOP_K:
                sorted_probs = sorted(new_probs.items(), key=lambda x: x[1], reverse=True)
                new_probs = dict(sorted_probs[:TOP_K])

            # V5: Teleport injection（强化种子节点，防止权重退化）
            for kw in keywords_set:
                if kw in new_probs:
                    new_probs[kw] += (1.0 / len(keywords_set)) * (1 - DAMPING)
                elif kw.startswith('ctx'):
                    new_probs[kw] = (1.0 / len(keywords_set)) * (1 - DAMPING)

            # V5: Convergence check（L1 delta < threshold 时提前停止）
            if iteration > 0:
                l1_delta = sum(
                    abs(new_probs.get(k, 0) - keyword_probs.get(k, 0))
                    for k in set(new_probs) | set(keyword_probs)
                )
                if l1_delta < CONVERGENCE_THRESHOLD:
                    break

            keyword_probs = new_probs

        self._boost_ppr_edges(edge_flows)

        # ── 阶段3：Context → Context 传播（V5: depth=2 expansion）─────────
        cc_top_k = TOP_K
        new_ctx_probs: Dict[str, float] = {}
        for ctx_id, prob in [(k, v) for k, v in keyword_probs.items() if str(k).startswith('ctx')]:
            if prob <= 0:
                continue
            cc_rows = conn.execute("""
                SELECT e.target_id, e.weight
                FROM edges e
                WHERE e.source_id = ? AND e.edge_type = 'context_context'
                UNION ALL
                SELECT e.source_id, e.weight
                FROM edges e
                WHERE e.target_id = ? AND e.edge_type = 'context_context'
            """, (ctx_id, ctx_id)).fetchall()

            if not cc_rows:
                continue

            for (neighbor_id, edge_weight) in cc_rows:
                propagated = prob * edge_weight * CC_DAMPING
                new_ctx_probs[neighbor_id] = new_ctx_probs.get(neighbor_id, 0) + propagated

        # Top-K truncation for context-context expansion
        if len(new_ctx_probs) > cc_top_k:
            sorted_cc = sorted(new_ctx_probs.items(), key=lambda x: x[1], reverse=True)
            new_ctx_probs = dict(sorted_cc[:cc_top_k])

        # L1 normalization for CC propagation
        if new_ctx_probs:
            total_cc = sum(new_ctx_probs.values())
            if total_cc > 0:
                new_ctx_probs = {k: v/total_cc for k, v in new_ctx_probs.items()}
            # 合并到主得分（加权叠加）
            for ctx_id, prob in new_ctx_probs.items():
                keyword_probs[ctx_id] = keyword_probs.get(ctx_id, 0) + prob * 0.3

        # ── 阶段4：V5 Dual-Channel Scoring ────────────────────────────────
        # 计算 keyword_match_score（每个 context 的关键词覆盖率）
        keyword_match_scores: Dict[str, float] = {}
        ctx_ids_all = [k for k in keyword_probs.keys() if str(k).startswith('ctx')]
        for ctx_id in ctx_ids_all:
            row = conn.execute("SELECT topic FROM contexts WHERE context_id = ?", (ctx_id,)).fetchone()
            if row:
                ctx_kws = {k.strip().lower() for k in row[0].split(' / ') if k.strip()}
                if keywords_set and ctx_kws:
                    overlap = len(keywords_set & ctx_kws)
                    keyword_match_scores[ctx_id] = overlap / max(len(keywords_set), len(ctx_kws))
                else:
                    keyword_match_scores[ctx_id] = 0.0

        # 计算 recency_score（小时为单位，指数衰减）
        from datetime import datetime as dt
        recency_scores: Dict[str, float] = {}
        for ctx_id in ctx_ids_all:
            row = conn.execute(
                "SELECT updated_at FROM contexts WHERE context_id = ?", (ctx_id,)
            ).fetchone()
            if row and row[0]:
                try:
                    hours = (dt.now() - dt.fromisoformat(row[0])).total_seconds() / 3600
                    recency_scores[ctx_id] = min(1.0, math.exp(-0.01 * max(0, hours - 24)))
                except:
                    recency_scores[ctx_id] = 0.5
            else:
                recency_scores[ctx_id] = 0.5

        # 计算 final_score = α*kw_match + β*ppr + γ*recency
        for ctx_id in ctx_ids_all:
            kw_score = keyword_match_scores.get(ctx_id, 0.0)
            ppr_score = keyword_probs.get(ctx_id, 0.0)
            rec_score = recency_scores.get(ctx_id, 0.5)
            final = ALPHA * kw_score + BETA * ppr_score + GAMMA * rec_score
            keyword_probs[ctx_id] = final

        # 重新排序
        sorted_results = sorted(keyword_probs.items(), key=lambda x: x[1], reverse=True)
        context_ids = [r[0] for r in sorted_results[:top_k] if str(r[0]).startswith('ctx')]
        scores = {r[0]: r[1] for r in sorted_results[:top_k] if str(r[0]).startswith('ctx')}

        contexts = []
        for ctx_id in context_ids:
            row = conn.execute(
                "SELECT topic, original_text FROM contexts WHERE context_id = ?",
                (ctx_id,)
            ).fetchone()
            if row:
                topic, orig_text = row
                contexts.append({
                    'context_id':    ctx_id,
                    'topic':         topic,
                    'original_text': orig_text,
                    'score':         scores.get(ctx_id, 0),
                    'kw_score':      keyword_match_scores.get(ctx_id, 0),
                    'recency_score': recency_scores.get(ctx_id, 0),
                })
        matched_existing_kws = list(kw_match_scores.keys())

        # ── PPR 结果为空时的 text fallback ─────────────────────────────────
        _SHORT_NOISE_Q = {'好的', '好的。', '好的！', '嗯', '嗯嗯', '了解', '知道', '行', '好', '啊啊啊', '哈哈', '呵呵', '喔', '呃'}
        q_clean = question.strip().lower()
        if not contexts and q_clean not in _SHORT_NOISE_Q and len(q_clean) > 2:
            rows = conn.execute(
                "SELECT context_id, topic, original_text FROM contexts"
            ).fetchall()
            for (ctx_id, topic, orig_text) in rows:
                if not orig_text:
                    continue
                if question in orig_text:
                    contexts.append({
                        'context_id': ctx_id, 'topic': topic,
                        'original_text': orig_text,
                        'score': 0.5
                    })
                    scores[ctx_id] = 0.5
                else:
                    chinese_terms = _CHINESE_PAT.findall(question)
                    for term in chinese_terms:
                        if term in orig_text:
                            contexts.append({
                                'context_id': ctx_id, 'topic': topic,
                                'original_text': orig_text,
                                'score': 0.4
                            })
                            scores[ctx_id] = max(scores.get(ctx_id, 0), 0.4)
                            break

        return {
            'keywords':         list(keywords_set),
            'matched_keywords': matched_existing_kws,
            'contexts':         contexts,
            'scores':           scores,
            'decay':            decay_result,
            'prune':            prune_result,
            'ppr_boosted_edges': len(edge_flows)
        }

    # ────────────────────────────────────────────────────────────────────────
    # 动态权重 & 清理
    # ────────────────────────────────────────────────────────────────────────
    def _decay_all_edges(self) -> Dict[str, Any]:
        conn = self._get_conn()
        lambda_ = math.log(2) / DECAY_HALF_LIFE_HOURS
        now = datetime.now()

        pinned_ctx_ids = set(
            r[0] for r in conn.execute(
                "SELECT context_id FROM contexts WHERE is_pinned = 1"
            ).fetchall()
        )
        if pinned_ctx_ids:
            pinned_placeholders = ','.join('?' * len(pinned_ctx_ids))
            rows = conn.execute(
                f"SELECT edge_id, weight, created_at, last_accessed FROM edges WHERE target_id NOT IN ({pinned_placeholders})",
                list(pinned_ctx_ids)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT edge_id, weight, created_at, last_accessed FROM edges"
            ).fetchall()

        decayed = 0
        protected = 0
        for row in rows:
            ref_time_str = row[3] or row[2]
            ref_time = datetime.fromisoformat(ref_time_str) if ref_time_str else row[2]
            age_days = (now - ref_time).total_seconds() / 86400
            if age_days < GRACE_PERIOD_DAYS:
                protected += 1
                continue
            hours = max(0, (now - ref_time).total_seconds() / 3600)
            new_weight = row[1] * math.exp(-lambda_ * hours)
            if new_weight < row[1]:
                conn.execute(
                    "UPDATE edges SET weight = ?, last_accessed = ? WHERE edge_id = ?",
                    (new_weight, now.isoformat(), row[0])
                )
                decayed += 1
        conn.commit()
        return {'decayed': decayed, 'protected': protected}

    def _auto_prune_edges(self) -> Dict[str, Any]:
        conn = self._get_conn()
        pinned_ctx_ids = set(
            r[0] for r in conn.execute(
                "SELECT context_id FROM contexts WHERE is_pinned = 1"
            ).fetchall()
        )
        if pinned_ctx_ids:
            pinned_placeholders = ','.join('?' * len(pinned_ctx_ids))
            to_delete = conn.execute(
                f"SELECT edge_id FROM edges WHERE weight < ? AND target_id NOT IN ({pinned_placeholders})",
                [DELETE_THRESHOLD] + list(pinned_ctx_ids)
            ).fetchall()
        else:
            to_delete = conn.execute(
                "SELECT edge_id FROM edges WHERE weight < ?", (DELETE_THRESHOLD,)
            ).fetchall()
        deleted = sum(
            1 for (eid,) in to_delete
            if conn.execute("DELETE FROM edges WHERE edge_id = ?", (eid,)).rowcount > 0
        )
        orphan_kw = conn.execute("""
            SELECT keyword_id FROM keywords k
            WHERE NOT EXISTS (SELECT 1 FROM edges e
                              WHERE e.source_id = k.keyword_id OR e.target_id = k.keyword_id)
        """).fetchall()
        orphan_deleted = sum(
            1 for (kid,) in orphan_kw
            if conn.execute("DELETE FROM keywords WHERE keyword_id = ?", (kid,)).rowcount > 0
        )
        conn.commit()
        return {'edges_deleted': deleted, 'orphan_keywords_deleted': orphan_deleted}

    def _boost_ppr_edges(self, edge_flows: Dict[str, float]) -> None:
        if not edge_flows:
            return
        conn = self._get_conn()
        now = datetime.now().isoformat()
        for edge_id, flow in edge_flows.items():
            conn.execute(
                "UPDATE edges SET weight = weight + ?, last_accessed = ? WHERE edge_id = ?",
                (PPR_BOOST_RATE * flow, now, edge_id)
            )
        conn.commit()

    def calculate_memory_importance(self, context_id: str) -> float:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT created_at, updated_at, access_count FROM contexts WHERE context_id = ?",
            (context_id,)
        ).fetchone()
        if not row:
            return 0.0

        created_at = datetime.fromisoformat(row[0])
        updated_at = datetime.fromisoformat(row[1]) if row[1] else created_at
        days_since_update = (datetime.now() - updated_at).days
        recency_factor = math.exp(-DECAY_RATE * days_since_update)
        if (datetime.now() - created_at).days < GRACE_PERIOD_DAYS:
            recency_factor = max(recency_factor, 0.8)

        access_freq = min(1.0, (row[2] or 0) * ACCESS_BOOST)
        avg_w = conn.execute("""
            SELECT AVG(weight) FROM edges
            WHERE target_id = ? AND edge_type = 'keyword_context'
        """, (context_id,)).fetchone()[0] or 1.0

        return min(1.0, 0.3 * recency_factor + 0.3 * access_freq + 0.4 * (avg_w / 2.0))

    def should_delete(self, context_id: str) -> tuple:
        importance = self.calculate_memory_importance(context_id)
        should_del = importance < 0.5
        return should_del, 1.0 - importance

    def prune(self) -> Dict[str, Any]:
        conn = self._get_conn()
        deleted = preserved = 0
        for (cid,) in conn.execute("SELECT context_id FROM contexts").fetchall():
            do_del, prob = self.should_delete(cid)
            if do_del and np.random.random() < prob:
                conn.execute("DELETE FROM edges WHERE target_id = ? OR source_id = ?", (cid, cid))
                conn.execute("DELETE FROM contexts WHERE context_id = ?", (cid,))
                deleted += 1
            else:
                preserved += 1
        conn.execute("""
            DELETE FROM keywords WHERE keyword_id NOT IN (
                SELECT DISTINCT source_id FROM edges WHERE edge_type = 'keyword_context'
            )
        """)
        conn.commit()
        return {'deleted': deleted, 'preserved': preserved,
                'message': f'清理完成，删除 {deleted} 条低价值记忆'}

    # ────────────────────────────────────────────────────────────────────────
    # 统计 & 消息处理
    # ────────────────────────────────────────────────────────────────────────
    def get_stats(self) -> Dict[str, int]:
        conn = self._get_conn()
        enc_count = conn.execute(
            "SELECT COUNT(*) FROM keywords WHERE encoding IS NOT NULL"
        ).fetchone()[0]
        ctx_enc_count = conn.execute(
            "SELECT COUNT(*) FROM contexts WHERE encoding IS NOT NULL"
        ).fetchone()[0]
        stats = {
            'agents':           conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
            'keywords':         conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0],
            'keywords_encoded': enc_count,
            'contexts':         conn.execute("SELECT COUNT(*) FROM contexts").fetchone()[0],
            'contexts_encoded': ctx_enc_count,

            'total_edges':      conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            'agent_keyword':    conn.execute("SELECT COUNT(*) FROM edges WHERE edge_type='agent_keyword'").fetchone()[0],
            'keyword_context':  conn.execute("SELECT COUNT(*) FROM edges WHERE edge_type='keyword_context'").fetchone()[0],
            'keyword_keyword':  conn.execute("SELECT COUNT(*) FROM edges WHERE edge_type='keyword_keyword'").fetchone()[0],
            # V5 新增
            'context_context':  conn.execute("SELECT COUNT(*) FROM edges WHERE edge_type='context_context'").fetchone()[0],
            'vocab_size':       _ENCODING_DIM,
            'pinned_contexts':  conn.execute(
                "SELECT COUNT(*) FROM contexts WHERE is_pinned = 1"
            ).fetchone()[0],
        }
        return stats

    def get_pinned(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT context_id, topic, original_text, access_count, created_at
               FROM contexts WHERE is_pinned = 1
               ORDER BY created_at DESC"""
        ).fetchall()
        return [
            {
                'context_id':   r[0],
                'topic':        r[1],
                'original_text': r[2],
                'access_count': r[3],
                'created_at':   r[4],
            }
            for r in rows
        ]

    def pin(self, context_id: str) -> Dict[str, Any]:
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT is_pinned FROM contexts WHERE context_id = ?", (context_id,)
        ).fetchone()
        if not existing:
            return {'success': False, 'message': 'context not found'}
        if existing[0] == 1:
            return {'success': True, 'already_pinned': True}
        conn.execute(
            "UPDATE contexts SET is_pinned = 1, updated_at = CURRENT_TIMESTAMP WHERE context_id = ?",
            (context_id,)
        )
        conn.commit()
        return {'success': True, 'already_pinned': False}

    def unpin(self, context_id: str) -> Dict[str, Any]:
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT is_pinned FROM contexts WHERE context_id = ?", (context_id,)
        ).fetchone()
        if not existing:
            return {'success': False, 'message': 'context not found'}
        conn.execute(
            "UPDATE contexts SET is_pinned = 0, updated_at = CURRENT_TIMESTAMP WHERE context_id = ?",
            (context_id,)
        )
        conn.commit()
        return {'success': True}

    def handle_message(self, message: str) -> Dict[str, Any]:
        message = message.strip()

        if message.startswith("查询记忆："):
            question = message.replace("查询记忆：", "").strip()
            result = self.query(question)
            contexts = result['contexts']
            keywords = result['keywords']
            matched = result.get('matched_keywords', [])

            lines = []
            if not contexts:
                lines.append(f"未找到与「{question}」相关的记忆。")
            else:
                lines.append(f"找到 {len(contexts)} 条相关记忆：\n")
                for c in contexts:
                    pairs = self._parse_original_text(c.get('original_text', ''))
                    answer_lines = [p['answer'][:80] for p in pairs if p.get('answer')]
                    lines.append(f"• [{c['topic']}] (相关度 {c['score']:.2f})")
                    for t in answer_lines:
                        lines.append(f"  - {t}...")
            extra = f"\n匹配到关键词：{', '.join(matched[:5])}" if matched else ""
            lines.append(f"\n查询词提取：{', '.join(keywords[:5])}{extra}")
            return {"type": "query_result", "text": '\n'.join(lines), "count": len(contexts)}

        if message.startswith("{"):
            try:
                data = json.loads(message)
                t = data.get("type", "")
                if t == "store":
                    r = self.store(data.get("question", ""), data.get("answer", ""))
                    action = r.get('action', 'unknown')
                    if action == 'merged':
                        text = f"已合并到现有记忆（{r.get('topic', '')}`），当前共 {r.get('memory_count', 0)} 条相关记录。"
                    else:
                        text = f"已新建记忆节点「{r.get('topic', '')}」，含 {r.get('keywords_count', 0)} 个关键词。"
                    return {"type": "store_result", "success": True, "text": text}
                if t == "prune":
                    rr = self.prune()
                    return {"type": "prune_result", "text": f"清理完成：删除 {rr.get('edges_deleted', 0)} 条边，{rr.get('orphan_keywords_deleted', 0)} 个孤立关键词。"}
                if t == "stats":
                    s = self.get_stats()
                    text = (f"记忆库统计：L1 关键词 {s.get('keywords', 0)} 个（编码 {s.get('keywords_encoded', 0)} 个），"
                            f"L2 Context {s.get('contexts', 0)} 个，"
                            f"边 {s.get('total_edges', 0)} 条（其中 context_context {s.get('context_context', 0)} 条），"
                            f"词表维度 {s.get('vocab_size', 0)}。")
                    return {"type": "stats_result", "text": text}
            except json.JSONDecodeError:
                pass

        return self.handle_message(f"查询记忆：{message}")

    def update_weights(self) -> Dict[str, Any]:
        return self._decay_all_edges()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


def create_skill() -> MemorySkill:
    return MemorySkill()


if __name__ == "__main__":
    import sys
    skill = MemorySkill()
    if len(sys.argv) > 1:
        result = skill.handle_message(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage: python memory_skill.py <message>")
        print("  查询: python memory_skill.py '查询记忆：Python是什么'")
        print("  存储: python memory_skill.py '{\"type\":\"store\",\"question\":\"...\",\"answer\":\"...\"}'")
        print()
        print("Statistics:", skill.get_stats())
    skill.close()
