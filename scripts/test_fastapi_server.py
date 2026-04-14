#!/usr/bin/env python3
"""
FastAPI 服务器功能测试

测试步骤：
1. 确保服务器正在运行：python start_server.py
2. 运行此脚本：python self_test/test_fastapi_server.py

测试内容：
- 健康检查
- 创建任务
- 查询任务状态
- 列出所有任务
- 取消任务
"""

import sys
import os
import time
import requests

# 添加项目根目录到 Python 路径，以便导入 utils 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.utils import process_markdown_task

# 服务器地址（根据实际部署修改）
BASE_URL = "http://192.168.137.1:8000"


def print_section(title: str):
    """打印分隔线"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def test_health():
    """测试 1: 健康检查"""
    print_section("Test 1: 健康检查")

    try:
        resp = requests.get(f"{BASE_URL}/api/v1/health", timeout=5)
        print(f"状态码：{resp.status_code}")
        print(f"响应：{resp.json()}")

        if resp.status_code == 200:
            print("✓ 健康检查通过")
            return True
        else:
            print("✗ 健康检查失败")
            return False
    except requests.exceptions.ConnectionError:
        print("✗ 无法连接到服务器，请确认服务器正在运行")
        print(f"   启动命令：python start_server.py")
        return False
    except Exception as e:
        print(f"✗ 错误：{e}")
        return False


def test_create_task(mdpath: str = "../test_md/test_ui9.md"):
    """测试 2: 创建任务"""
    print_section("Test 2: 创建任务")
    
    # mdpath = "../test_md/test_ui9.md"
    read_markdown = process_markdown_task(mdpath)
    task_name = None
    if read_markdown:
        task_name = read_markdown["extracted_title"]
        instruction = read_markdown["prompt_for_llm"]
        
    payload = {
        "task_name": task_name,
        "instruction": instruction,
        "max_steps": 30,
        "max_retries": 3,
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/api/v1/tasks",
            json=payload,
            timeout=10
        )
        print(f"状态码：{resp.status_code}")
        data = resp.json()
        print(f"响应：{data}")

        if resp.status_code == 200 and "task_id" in data:
            print("✓ 任务创建成功")
            print(f"  Task ID: {data['task_id']}")
            print(f"  状态：{data['status']}")
            return data["task_id"]
        else:
            print("✗ 任务创建失败")
            return None
    except Exception as e:
        print(f"✗ 错误：{e}")
        return None


# def test_create_task(mdpath: str = ""):
#     """测试 2: 创建任务"""
#     print_section("Test : 创建任务")
    
    
#     payload = {
#         "task_name": "自动化测试示例1",
#         "instruction": "双击打开Edge Dev浏览器",
#         "max_steps": 30,
#         "max_retries": 3,
#     }

#     try:
#         resp = requests.post(
#             f"http://192.168.137.1:8000/api/v1/tasks",
#             json=payload,
#             timeout=10
#         )
#         print(f"状态码：{resp.status_code}")
#         data = resp.json()
#         print(f"响应：{data}")

#         if resp.status_code == 200 and "task_id" in data:
#             print("✓ 任务创建成功")
#             print(f"  Task ID: {data['task_id']}")
#             print(f"  状态：{data['status']}")
#             return data["task_id"]
#         else:
#             print("✗ 任务创建失败")
#             return None
#     except Exception as e:
#         print(f"✗ 错误：{e}")
#         return None

def test_get_task(task_id: str):
    """测试 3: 查询任务状态"""
    print_section(f"Test 3: 查询任务状态 ({task_id[:8]}...)")

    max_wait = 600  # 最多等待 600 秒
    poll_interval = 2  # 每 2 秒查询一次
    elapsed = 0

    while elapsed < max_wait:
        try:
            resp = requests.get(f"{BASE_URL}/api/v1/tasks/{task_id}", timeout=5)
            data = resp.json()

            print(f"[{elapsed:2d}s] 状态：{data.get('status', 'unknown')}")

            if resp.status_code != 200:
                print("✗ 查询失败")
                return False

            status = data.get("status")
            if status in ("completed", "failed", "cancelled"):
                print(f"\n最终状态：{status}")
                if data.get("result"):
                    print(f"结果：{data['result']}")
                if data.get("error"):
                    print(f"错误：{data['error']}")

                if status == "completed":
                    print("✓ 任务完成")
                    return True
                else:
                    print(f"✗ 任务结束：{status}")
                    return False

            elapsed += poll_interval
            time.sleep(poll_interval)

        except Exception as e:
            print(f"✗ 错误：{e}")
            return False

    print(f"✗ 超时：{max_wait}秒内未完成")
    return False


def test_list_tasks():
    """测试 4: 列出所有任务"""
    print_section("Test 4: 列出所有任务")

    try:
        resp = requests.get(f"{BASE_URL}/api/v1/tasks", timeout=5)
        print(f"状态码：{resp.status_code}")
        data = resp.json()
        print(f"任务总数：{len(data)}")

        for task in data[:5]:  # 只显示前 5 个
            print(f"  - {task['task_id'][:8]}... : {task['status']}")

        if len(data) > 5:
            print(f"  ... 还有 {len(data) - 5} 个任务")

        print("✓ 列表查询成功")
        return True
    except Exception as e:
        print(f"✗ 错误：{e}")
        return False


def test_cancel_task():
    """测试 5: 取消任务（创建一个耗时任务并取消）"""
    print_section("Test 5: 取消任务")

    # 创建一个可能耗时的任务
    payload = {
        "task_name": "test_cancel",
        "instruction": "Open Calculator and do nothing",
        "max_steps": 100,  # 多步骤
        "max_retries": 3,
    }

    try:
        resp = requests.post(f"{BASE_URL}/api/v1/tasks", json=payload, timeout=10)
        data = resp.json()
        task_id = data.get("task_id")
        print(f"创建任务：{task_id}")

        # 等待 1 秒后立即取消
        time.sleep(1)

        resp = requests.post(
            f"{BASE_URL}/api/v1/tasks/{task_id}/cancel",
            timeout=5
        )
        print(f"取消响应：{resp.json()}")

        if resp.status_code == 200:
            print("✓ 取消请求成功")
            return True
        else:
            print(f"✗ 取消失败（可能任务已完成）：{resp.status_code}")
            return False

    except Exception as e:
        print(f"✗ 错误：{e}")
        return False


def main(mdpath: str = "../test_md/test_ui9.md"):
    """运行所有测试"""
    print_section("FastAPI 服务器功能测试")
    print(f"服务器地址：{BASE_URL}")
    print("请确保服务器正在运行：python start_server.py")

    results = {}

    # Test 1: 健康检查
    if not test_health():
        print("\n服务器未响应，测试终止")
        sys.exit(1)

    # Test 2: 创建任务
    task_id = test_create_task(mdpath)
    results["create"] = task_id is not None

    if task_id:
        # Test 3: 查询状态
        results["query"] = test_get_task(task_id)

    # Test 4: 列出任务
    results["list"] = test_list_tasks()

    # Test 5: 取消任务
    # results["cancel"] = test_cancel_task()

    # 总结
    print_section("测试结果总结")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        status = "✓" if result else "✗"
        print(f"  {status} {test_name}: {'通过' if result else '失败'}")

    print(f"\n总计：{passed}/{total} 通过")

    if passed == total:
        print("\n✓ 所有测试通过！")
        sys.exit(0)
    else:
        print("\n✗ 部分测试失败")
        sys.exit(1)


if __name__ == "__main__":
    mdpath = "../test_md/test_ui8.md"
    main(mdpath)
20 120 