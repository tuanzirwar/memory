"""
MemoryHancement Skill

基于 TextRank4ZH 的记忆管理系统

支持两个版本：
- MemorySkill (v1): 基础版，每次存储创建新 context
- MemorySkillV2: 增强版，支持 context 去重合并、动态权重、概率检索/删除
"""

from .memory_skill import MemorySkill, create_skill
from .memory_v2 import MemorySkillV2, create_skill as create_skill_v2

__all__ = [
    'MemorySkill', 
    'create_skill',
    'MemorySkillV2', 
    'create_skill_v2'
]
