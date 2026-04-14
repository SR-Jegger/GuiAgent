# 参数化技能设计文档

## 背景

用户场景：指挥系统界面有多个飞机卡片，需要通过"指派任务给XX飞机"指令完成右键点击卡片 + 选择菜单项的组合操作。

**核心挑战**：不同飞机卡片位置不同（X坐标变化），但Y坐标固定；菜单选项相对右键位置有固定偏移。当前技能系统只能存储固定坐标，无法处理动态定位场景。

---

## 解决方案

设计**参数化技能**：支持动态坐标占位符，执行时通过OCR定位替换为实际坐标。

---

## 技能存储格式

```json
{
  "id": "manual_054ceff1",
  "name": "指派任务给飞机",
  "description": "右键点击指定飞机卡片，选择指派任务",
  "source": "manual",
  "trigger": {
    "patterns": ["指派任务给(.+)飞机"],
    "app_context": ["指挥系统"]
  },
  "actions": [
    {
      "type": "right_click",
      "coordinate": "{{ocr:{{match_group_1}}}}"
    },
    {
      "type": "click",
      "coordinate": "{{prev_x+20}}, {{prev_y+120}}"
    }
  ],
  "enabled": true
}
```

---

## 占位符语法定义

| 语法 | 含义 | 示例 |
|---|---|---|
| `{{ocr:text}}` | OCR定位包含text的UI元素中心坐标 | `{{ocr:飞机01}}` → `[350, 280]` |
| `{{match_group_n}}` | 正则捕获组n的内容 | `"指派任务给(歼击机A)飞机"` → `歼击机A` |
| `{{prev_x}}` | 上一步动作的X坐标 | 上一步 `[350, 280]` → `350` |
| `{{prev_y}}` | 上一步动作的Y坐标 | → `280` |
| `{{prev_x+n}}` | X坐标偏移 | `{{prev_x+20}}` → `370` |
| `{{prev_y+n}}` | Y坐标偏移 | `{{prev_y+120}}` → `400` |

**组合示例**：
- `{{ocr:{{match_group_1}}}}` → 先取捕获组内容，再OCR定位
- `{{prev_x+20}}, {{prev_y+120}}` → 相对上一步偏移点击

---

## 执行流程

用户输入 `"指派任务给歼击机A飞机"`：

```
步骤1: 正则匹配
  模式: "指派任务给(.+)飞机"
  捕获: match_group_1 = "歼击机A"

步骤2: 解析动作1
  原始: {"type": "right_click", "coordinate": "{{ocr:{{match_group_1}}}}"}
  替换: "{{match_group_1}}" → "歼击机A"
  结果: {"type": "right_click", "coordinate": "{{ocr:歼击机A}}"}

步骤3: 执行OCR定位
  输入: "歼击机A"
  截图: 使用capture_node提供的截图
  输出: [350, 280] (假设)

步骤4: 执行动作1
  右键点击坐标 [350, 280]
  记录: prev_x=350, prev_y=280

步骤5: 解析动作2
  原始: {"type": "click", "coordinate": "{{prev_x+20}}, {{prev_y+120}}"}
  替换: prev_x=350, prev_y=280
  结果: {"type": "click", "coordinate": [370, 400]}

步骤6: 执行动作2
  点击坐标 [370, 400] (菜单中的"指派任务"选项位置)
```

---

## 系统架构变更

### 流程修改（agent_graph.py）

**原流程**：
```
task_decomposer -> fast_path -> (matched: execution | not matched: capture -> reasoning)
```

**新流程**（capture前置，为参数化技能提供截图）：
```
task_decomposer -> capture -> fast_path -> (matched: execution | not matched: reasoning)
```

**修改点**：
1. `task_decomposer -> capture`（不再直接到fast_path）
2. `capture成功后 -> fast_path`（不再是reasoning）
3. `fast_path匹配成功 -> execution`（不变）
4. `fast_path匹配失败 -> reasoning`（不再是capture）

**性能影响**：
- 固定坐标技能：增加约0.1-0.3秒截图开销
- VLM推理流程：无变化
- 参数化技能：必需截图

---

## 新增模块

### 1. OCR定位模块

**文件**: `utils/ocr_locator.py`

```python
from paddleocr import PaddleOCR
import numpy as np

class OCRLocator:
    """基于PaddleOCR的UI元素定位器"""

    def __init__(self):
        self.ocr = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            use_gpu=False,
            show_log=False
        )

    def locate_element(
        self,
        target_text: str,
        screenshot: np.ndarray,
        threshold: float = 0.8
    ) -> tuple[int, int] | None:
        """
        定位包含指定文字的UI元素中心坐标

        Args:
            target_text: 要定位的文字
            screenshot: 截图数组
            threshold: 文字匹配阈值

        Returns:
            坐标 [x, y]，未找到返回 None
        """
        # 1. OCR识别截图中的所有文字及其位置
        # 2. 匹配包含target_text的区域（模糊匹配，threshold控制）
        # 3. 返回该区域的中心坐标
```

### 2. 占位符解析模块

**文件**: `utils/action_resolver.py`

```python
class ActionResolver:
    """解析技能动作中的占位符，生成可执行的动作"""

    def __init__(self, ocr_locator: OCRLocator):
        self.ocr_locator = ocr_locator
        self.prev_x = None
        self.prev_y = None

    def resolve_actions(
        self,
        actions: list,
        match_groups: tuple,
        screenshot: np.ndarray
    ) -> list:
        """
        解析动作列表中的所有占位符

        Args:
            actions: 原始动作列表（含占位符）
            match_groups: 正则捕获组
            screenshot: 当前截图

        Returns:
            解析后的动作列表（纯坐标）
        """
        resolved = []
        for action in actions:
            resolved_action = self._resolve_single_action(
                action, match_groups, screenshot
            )
            resolved.append(resolved_action)
            # 记录上一步坐标，供相对偏移使用
            if "coordinate" in resolved_action:
                self.prev_x, self.prev_y = resolved_action["coordinate"]
        return resolved
```

---

## 修改文件清单

| 文件 | 修改内容 |
|---|---|
| `agent_graph.py` | 流程变更：capture前置到fast_path之前，添加中文注释 |
| `nodes/fast_path_node.py` | 新增占位符检测和ActionResolver调用 |
| `nodes/capture_node.py` | 截图结果存入state，供后续节点复用 |
| `rule_matcher.py` | 支持加载 rules/manual_skills.json |

---

## 新增文件清单

| 文件 | 作用 |
|---|---|
| `utils/ocr_locator.py` | PaddleOCR定位模块 |
| `utils/action_resolver.py` | 占位符解析模块 |
| `rules/manual_skills.json` | 存放手动定义的参数化技能 |

---

## 测试技能示例

用于验证参数化技能执行流程：

```json
{
  "id": "manual_054ceff1",
  "name": "指派任务给飞机",
  "description": "右键点击指定飞机卡片，选择指派任务",
  "source": "manual",
  "trigger": {
    "patterns": ["指派任务给(.+)飞机"]
  },
  "actions": [
    {"type": "right_click", "coordinate": "{{ocr:{{match_group_1}}}}"},
    {"type": "click", "coordinate": "{{prev_x+20}}, {{prev_y+120}}"}
  ],
  "enabled": true
}
```

---

## 后续扩展方向

1. **参数化技能自动识别**：聚类时检测"指令有变量 + Y坐标固定 + X坐标变化"的模式，自动提示为参数化技能候选
2. **更多占位符类型**：支持 `{{screen_width}}`、`{{screen_height}}` 等屏幕信息变量
3. **前端编辑支持**：Dashboard界面支持创建/编辑参数化技能