# TOOLS.md - 本地工具配置笔记

技能定义了工具怎么用。这个文件记的是你自己的具体配置，属于你的环境、你的设置。

## ML 开发环境

- **深度学习框架**: PyTorch（主力）、TensorFlow（兼容）
- **实验追踪**: MLflow（实验记录、模型注册、参数对比）
- **数据版本管理**: DVC（数据集版本控制、流水线复现）
- **分布式训练**: PyTorch DDP / DeepSpeed / FSDP
- **超参搜索**: Optuna / Ray Tune

## 部署工具

- **容器化**: Docker（模型打包、环境一致性）
- **推理服务**: TorchServe / Triton Inference Server / vLLM
- **模型优化**: ONNX Runtime / TensorRT / 量化（GPTQ、AWQ、bitsandbytes）
- **编排调度**: Kubernetes（推理服务扩缩容）
- **监控**: Prometheus + Grafana（推理延迟、吞吐、错误率）

## 数据工具

- **交互式分析**: DuckDB（本地 OLAP、SQL 分析）
- **数据处理**: pandas / polars（ETL、特征工程）
- **数据校验**: Great Expectations / pandera（数据质量门禁）
- **向量数据库**: Milvus / Qdrant / FAISS（RAG 检索）

## LLM 应用工具

- **Prompt 工程**: LangChain / LlamaIndex（RAG 编排）
- **评测框架**: RAGAS / DeepEval（RAG 质量评测）
- **成本控制**: token 计数、缓存策略、模型路由

## 为什么单独放

技能是共享的，配置是你自己的。分开放，更新技能不会丢你的笔记，分享技能不会泄露你的环境。

---

记下任何帮你干活的东西。这是你的备忘录。
