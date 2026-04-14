# 技能坐标归一化改造

**日期**: 2026-04-09

## 问题背景

原有的技能学习系统存储的是绝对像素坐标（如 `[1070, 567]`），这导致：

1. 技能只能在采集时的分辨率下工作
2. 分辨率变化后坐标完全失效
3. 无法跨设备/显示器复用已学习的技能

## 解决方案

将坐标存储为归一化格式（0-1000 范围），执行时根据当前屏幕分辨率动态转换。

## 修改的文件

### 1. `nodes/execution_node.py`

**新增函数：**

```python
def normalize_coordinates(action_parameter: dict, width: int, height: int) -> dict:
    """将像素坐标转换为归一化坐标（0-1000范围）"""

def _get_screen_size() -> tuple[int, int]:
    """获取当前屏幕分辨率"""
```

**修改逻辑：**

- Fast Path 执行时：检测 `coordinate_normalized` 标记，自动转换归一化坐标到当前分辨率
- 日志记录时：存储归一化坐标而非像素坐标

### 2. `learning/skill_generator.py`

**修改内容：**

- `_generate_single_operation_action()`: 生成的技能添加 `coordinate_normalized: True` 标记
- `_generate_sequence_actions()`: 序列技能同样添加标记
- 删除未使用的 `_generate_actions()` 函数

## 数据流对比

```
修改前:
VLM输出(0-1000) → rescale → 像素坐标 → 执行 → 记录像素坐标 → 技能存像素坐标
                                                        ↓
                                           不同分辨率失效 ❌

修改后:
VLM输出(0-1000) → rescale → 像素坐标 → 执行
                    ↓
              记录归一化坐标 → 技能存归一化坐标(0-1000)
                                        ↓
                              执行时根据当前分辨率转换 ✅
```

## 坐标格式示例

| 阶段 | 格式 | 示例 |
|------|------|------|
| VLM 输出 | 归一化 (0-1000) | `[500, 500]` |
| 执行时转换 (1920x1080) | 像素坐标 | `[960, 540]` |
| 执行时转换 (2560x1440) | 像素坐标 | `[1280, 720]` |
| 日志/技能存储 | 归一化 + 标记 | `{"coordinate": [500, 500], "coordinate_normalized": true}` |

## 兼容性

- **旧技能**: 不含 `coordinate_normalized` 标记的技能仍按原逻辑执行
- **新技能**: 含标记的技能自动适配当前分辨率

## 测试验证

```python
# 测试归一化和反归一化
screen_w, screen_h = 1920, 1080
action = {'action': 'left_click', 'coordinate': [960, 540]}

# 归一化
normalized = normalize_coordinates(action, screen_w, screen_h)
# 结果: {'coordinate': [500, 500], 'coordinate_normalized': True}

# 反归一化到不同分辨率
rescale_coordinates(action2, 2560, 1440)
# 结果: {'coordinate': [1280, 720]}  (正确!)
```

## 后续优化方向

1. **窗口相对坐标**: 相对于目标窗口的位置，支持窗口移动
2. **元素描述**: 存储 UI 元素特征描述，配合 VLM 重新定位
3. **多显示器支持**: 处理跨显示器场景