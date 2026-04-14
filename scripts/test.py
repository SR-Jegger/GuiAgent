import sys
sys.path.insert(0, '.')

from learning.llm_cluster_engine import LLMClusterEngine, EmbeddingModel
from learning import OperationLogger
import os

# 1. 检查依赖
print("=== 检查依赖 ===")
try:
    import sentence_transformers
    print(f"sentence-transformers: {sentence_transformers.__version__}")
except ImportError as e:
    print(f"sentence-transformers: 未安装 - {e}")

try:
    import sklearn
    print(f"sklearn: {sklearn.__version__}")
except ImportError as e:
    print(f"sklearn: 未安装 - {e}")

# 2. 测试 embedding 相似度
print("\n=== 测试语义相似度 ===")
local_path = os.path.join(
    os.getcwd(),
    '.hf_cache', 'hub',
    'models--Alibaba-NLP--gte-multilingual-base',
    'snapshots', '9bbca17d9273fd0d03d5725c7a4b0f6b45142062'
)

print(f"模型路径: {local_path}")
print(f"路径存在: {os.path.exists(local_path)}")

model = EmbeddingModel(local_path)

texts = [
    '打击/侦察目标一栏输入 温哥华基地',
    '打击/侦察目标一栏输入 墨西哥基地',
    '打击/侦察目标一栏输入 侦察基地',
]

print("\n相似度计算:")
for i, t1 in enumerate(texts):
    for j, t2 in enumerate(texts[i+1:], i+1):
        sim = model.similarity(t1, t2)
        dist = 1 - sim
        print(f"  [{i+1}] vs [{j+1}]: sim={sim:.4f}, dist={dist:.4f}, 聚类条件(dist<0.25): {dist < 0.25}")

# 3. 实际运行聚类
print("\n=== 运行 LLM 序列聚类 ===")
logger = OperationLogger()
logs = logger.load_logs(limit=200)

engine = LLMClusterEngine()
engine.min_cluster_size = 2

clusters = engine.cluster_sequences(logs, min_cluster_size=2)
print(f"\n结果: 发现 {len(clusters)} 个序列聚类")

for i, c in enumerate(clusters):
    print(f"\n聚类 {i+1}: {c['count']} 个序列")
    for seq in c.get('sample_sequences', [])[:2]:
        instr = seq.get('instruction', '')[:50]
        h = seq.get('instruction_hash', '')
        print(f"  - [{h}] {instr}")