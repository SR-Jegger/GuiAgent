#!/usr/bin/env python3
"""
模板知识库模块

支持：
- 模板元数据管理（描述、标签、分类）
- 语义搜索（关键词匹配）
- 向量检索（可选，需要 sentence-transformers）
- 使用统计（热度排序）

使用方法:
    from template_knowledge import TemplateKnowledgeBase
    
    kb = TemplateKnowledgeBase("./templates")
    
    # 添加模板
    kb.add_template(
        file="buttons/close.png",
        name="关闭按钮",
        description="红色的 X 关闭按钮，常见于窗口右上角",
        tags=["关闭", "退出", "取消", "红色"],
        category="buttons"
    )
    
    # 语义搜索
    results = kb.search("红色的关闭按钮")
    print(results)  # ['buttons/close.png', ...]
    
    # 查找并定位
    coord = kb.find_and_locate("关闭按钮", "screenshot.png")
"""

import os
import json
import re
from typing import List, Dict, Optional, Tuple
from collections import Counter

# 向量检索支持（可选）
from sentence_transformers import SentenceTransformer, util
VECTOR_SEARCH_AVAILABLE = True

from utils import TemplateMatcher, CV2_AVAILABLE



class TemplateKnowledgeBase:
    """
    模板知识库：管理模板元数据 + 支持语义搜索
    """
    
    def __init__(self, template_dir="./templates", auto_load=True):
        """
        Args:
            template_dir: 模板库目录
            auto_load: 是否自动加载 registry.json
        """
        self.template_dir = template_dir
        self.registry_path = os.path.join(template_dir, "registry.json")
        self.templates = []  # 模板元数据列表
        self.matcher = None
        
        if CV2_AVAILABLE and TemplateMatcher:
            self.matcher = TemplateMatcher(template_dir)
        
        if auto_load:
            self.load_registry()
    
    # =========================================================================
    # 注册表管理
    # =========================================================================
    
    def load_registry(self) -> bool:
        """加载注册表"""
        if not os.path.exists(self.registry_path):
            print(f"[INFO] Registry not found: {self.registry_path}")
            return False
        
        try:
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.templates = data.get("templates", [])
            print(f"[INFO] Loaded {len(self.templates)} templates from registry")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to load registry: {e}")
            return False
    
    def save_registry(self) -> bool:
        """保存注册表"""
        try:
            os.makedirs(self.template_dir, exist_ok=True)
            data = {"templates": self.templates}
            with open(self.registry_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[INFO] Saved {len(self.templates)} templates to registry")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save registry: {e}")
            return False
    
    def add_template(self, file: str, name: str, description: str = "",
                     tags: List[str] = None, category: str = "",
                     usage_count: int = 0) -> bool:
        """
        添加模板到注册表
        
        Args:
            file: 模板文件名（如 "buttons/close.png"）
            name: 模板名称（如 "关闭按钮"）
            description: 详细描述
            tags: 标签列表
            category: 分类（buttons/icons/apps）
            usage_count: 使用次数
        """
        # 检查文件是否存在
        template_path = os.path.join(self.template_dir, file)
        if not os.path.exists(template_path):
            print(f"[WARN] Template file not found: {file}")
            return False
        
        # 检查是否已存在
        for tmpl in self.templates:
            if tmpl.get("file") == file:
                print(f"[WARN] Template already exists: {file}")
                return False
        
        template = {
            "file": file,
            "name": name,
            "description": description,
            "tags": tags or [],
            "category": category,
            "usage_count": usage_count,
            "created_at": self._get_timestamp()
        }
        
        self.templates.append(template)
        self.save_registry()
        print(f"[INFO] Added template: {name} ({file})")
        return True
    
    def remove_template(self, file: str) -> bool:
        """删除模板"""
        for i, tmpl in enumerate(self.templates):
            if tmpl.get("file") == file:
                removed = self.templates.pop(i)
                self.save_registry()
                print(f"[INFO] Removed template: {removed.get('name')}")
                return True
        print(f"[WARN] Template not found: {file}")
        return False
    
    def update_usage(self, file: str):
        """更新使用次数"""
        for tmpl in self.templates:
            if tmpl.get("file") == file:
                tmpl["usage_count"] = tmpl.get("usage_count", 0) + 1
                self.save_registry()
                return
    
    def list_templates(self, category: str = None) -> List[Dict]:
        """列出所有模板（可按分类过滤）"""
        if category:
            return [t for t in self.templates if t.get("category") == category]
        return self.templates
    
    def get_template(self, file: str) -> Optional[Dict]:
        """获取单个模板信息"""
        for tmpl in self.templates:
            if tmpl.get("file") == file:
                return tmpl
        return None
    
    # =========================================================================
    # 搜索功能
    # =========================================================================
    
    def search(self, query: str, limit: int = 10) -> List[str]:
        """
        搜索模板（关键词匹配）
        
        Args:
            query: 搜索词（如 "红色关闭按钮"）
            limit: 返回数量限制
        
        Returns:
            匹配的模板文件名列表
        """
        matches = []
        
        for tmpl in self.templates:
            score = self._calculate_match_score(tmpl, query)
            if score > 0:
                matches.append((tmpl["file"], score))
        
        # 按分数排序
        matches.sort(key=lambda x: x[1], reverse=True)
        
        # 返回前 N 个
        return [m[0] for m in matches[:limit]]
    
    def _calculate_match_score(self, template: Dict, query: str) -> int:
        """计算匹配分数"""
        score = 0
        
        # 构建搜索文本
        search_text = " ".join([
            template.get("name", ""),
            template.get("description", ""),
            " ".join(template.get("tags", [])),
            template.get("category", "")
        ]).lower()
        
        # 分词匹配
        query_words = self._tokenize(query)
        
        for word in query_words:
            if len(word) < 2:
                continue
            
            # 完全匹配
            if word in search_text:
                score += 2
            
            # 部分匹配
            for tmpl_word in self._tokenize(search_text):
                if word in tmpl_word or tmpl_word in word:
                    score += 1
        
        # 使用次数加成
        usage_bonus = min(template.get("usage_count", 0) // 10, 5)
        score += usage_bonus
        
        return score
    
    def _tokenize(self, text: str) -> List[str]:
        """中文分词（简单版）"""
        # 移除标点
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text)
        # 按空格和常见分隔符分词
        words = re.split(r'[\s,，.。、;；]+', text)
        return [w for w in words if w.strip()]
    
    # =========================================================================
    # 向量检索（可选）
    # =========================================================================
    
    def search_vector(self, query: str, limit: int = 10, threshold: float = 0.5) -> List[str]:
        """
        向量语义搜索（需要 sentence-transformers）
        
        Args:
            query: 搜索词
            limit: 返回数量
            threshold: 相似度阈值
        
        Returns:
            匹配的模板文件名列表
        """
        if not VECTOR_SEARCH_AVAILABLE:
            print("[WARN] Vector search not available")
            return self.search(query, limit)
        
        if not self.templates:
            return []
        
        # 加载模型（首次调用时）
        if not hasattr(self, '_model'):
            print("[INFO] Loading sentence-transformer model...")
            self._model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            self._embeddings = None
        
        # 计算查询向量
        query_embedding = self._model.encode(query, convert_to_tensor=True)
        
        # 计算模板文本向量（缓存）
        if self._embeddings is None:
            template_texts = [
                f"{t['name']} {t['description']} {' '.join(t['tags'])}"
                for t in self.templates
            ]
            self._embeddings = self._model.encode(template_texts, convert_to_tensor=True)
        
        # 计算相似度
        similarities = util.cos_sim(query_embedding, self._embeddings)[0]
        
        # 过滤和排序
        matches = []
        for i, sim in enumerate(similarities):
            if sim > threshold:
                matches.append((self.templates[i]["file"], float(sim)))
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return [m[0] for m in matches[:limit]]
    
    # =========================================================================
    # 定位功能
    # =========================================================================
    
    def find_and_locate(self, query: str, screenshot_path: str) -> Optional[Tuple[int, int]]:
        """
        搜索并定位模板
        
        Args:
            query: 搜索词（如 "关闭按钮"）
            screenshot_path: 截图路径
        
        Returns:
            坐标 (x, y) 或 None
        """
        if not self.matcher:
            print("[ERROR] Template matcher not available")
            return None
        
        # 搜索候选
        candidates = self.search(query, limit=5)
        
        if not candidates:
            print(f"[WARN] No matching templates for: {query}")
            return None
        
        # 依次尝试匹配
        for template_file in candidates:
            coord = self.matcher.find(template_file, screenshot_path)
            if coord:
                self.update_usage(template_file)
                print(f"[INFO] Found '{query}' → {template_file} at {coord}")
                return coord
        
        print(f"[WARN] Template matching failed for: {query}")
        return None
    
    # =========================================================================
    # 工具方法
    # =========================================================================
    
    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def export_to_markdown(self) -> str:
        """导出为 Markdown 文档"""
        lines = ["# 模板知识库\n", "## 模板列表\n"]
        
        # 按分类组织
        categories = {}
        for tmpl in self.templates:
            cat = tmpl.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(tmpl)
        
        for cat, templates in sorted(categories.items()):
            lines.append(f"### {cat}\n")
            for tmpl in templates:
                lines.append(f"- **{tmpl['name']}** (`{tmpl['file']}`)")
                if tmpl.get("description"):
                    lines.append(f"  - {tmpl['description']}")
                if tmpl.get("tags"):
                    lines.append(f"  - 标签：{', '.join(tmpl['tags'])}")
                lines.append("")
        
        return "\n".join(lines)


# ============================================================================
# 命令行工具
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="模板知识库管理工具")
    parser.add_argument("--template-dir", default="./templates", help="模板目录")
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # add 命令
    add_parser = subparsers.add_parser("add", help="添加模板")
    add_parser.add_argument("--file", required=True, help="模板文件名")
    add_parser.add_argument("--name", required=True, help="模板名称")
    add_parser.add_argument("--desc", default="", help="描述")
    add_parser.add_argument("--tags", default="", help="标签（逗号分隔）")
    add_parser.add_argument("--category", default="", help="分类")
    add_parser.set_defaults(func=lambda args: cmd_add(args, parser))
    
    # search 命令
    search_parser = subparsers.add_parser("search", help="搜索模板")
    search_parser.add_argument("query", help="搜索词")
    search_parser.add_argument("--limit", type=int, default=10, help="返回数量")
    search_parser.set_defaults(func=lambda args: cmd_search(args, parser))
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出所有模板")
    list_parser.add_argument("--category", default="", help="分类过滤")
    list_parser.set_defaults(func=lambda args: cmd_list(args, parser))
    
    # export 命令
    export_parser = subparsers.add_parser("export", help="导出为 Markdown")
    export_parser.add_argument("--output", default="TEMPLATE_INDEX.md", help="输出文件")
    export_parser.set_defaults(func=lambda args: cmd_export(args, parser))
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    kb = TemplateKnowledgeBase(args.template_dir)
    args.func(args)


def cmd_add(args, parser):
    """添加模板"""
    kb = TemplateKnowledgeBase(args.template_dir)
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    
    success = kb.add_template(
        file=args.file,
        name=args.name,
        description=args.desc,
        tags=tags,
        category=args.category
    )
    
    if success:
        print("✓ 模板已添加")
    else:
        print("✗ 添加失败")
        parser.exit(1)


def cmd_search(args, parser):
    """搜索模板"""
    kb = TemplateKnowledgeBase(args.template_dir)
    results = kb.search(args.query, limit=args.limit)
    
    if not results:
        print("未找到匹配的模板")
        return
    
    print(f"找到 {len(results)} 个匹配:\n")
    for file in results:
        tmpl = kb.get_template(file)
        print(f"  - {tmpl['name']} ({file})")
        if tmpl.get("description"):
            print(f"    {tmpl['description']}")


def cmd_list(args, parser):
    """列出模板"""
    kb = TemplateKnowledgeBase(args.template_dir)
    templates = kb.list_templates(category=args.category or None)
    
    if not templates:
        print("暂无模板")
        return
    
    print(f"共 {len(templates)} 个模板:\n")
    for tmpl in templates:
        print(f"  {tmpl['file']}")
        print(f"    名称：{tmpl['name']}")
        if tmpl.get("tags"):
            print(f"    标签：{', '.join(tmpl['tags'])}")


def cmd_export(args, parser):
    """导出"""
    kb = TemplateKnowledgeBase(args.template_dir)
    markdown = kb.export_to_markdown()
    
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(markdown)
    
    print(f"✓ 已导出到 {args.output}")


if __name__ == "__main__":
    main()
