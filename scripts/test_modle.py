import os
import time
import math
from typing import TYPE_CHECKING

from openai import OpenAI
from PIL import Image


from utils.utils import (
    build_messages,
    GUIOwlWrapper,
    smart_resize,
)


# ============ 配置区 ============
model = "/mnt/data1/automl/Bigdata/model/GUI-Owl-1.5-8B-Instruct"
base_url = "http://192.168.137.2:4040/v1"
api_key = "EMPTY"
screenshot_path = "E:\\automl\\AI_project\\GuiAgent\\test_screenshot.png"
current_instruction = "请分析当前屏幕截图，识别出'F22-206'的位置"
history = []

# 可调参数：降低图片分辨率以加速
MAX_PIXELS = 14 * 14 * 4 * 640  # 默认: 14*14*4*1280 ≈ 1M pixels，降低到约500K
MAX_LONG_SIDE = 1280  # 默认: 8192，降低以加速


def analyze_latency():
    """分步计时分析延迟来源"""
    total_start = time.time()

    # Step 1: 加载图片
    t1 = time.time()
    img = Image.open(screenshot_path)
    original_size = img.size
    t2 = time.time()
    print(f"[1] 图片加载: {t2 - t1:.2f}s, 原始尺寸: {original_size}")

    # Step 2: 图片缩放
    # t3 = time.time()
    # resized_h, resized_w = smart_resize(
    #     img.height, img.width,
    #     max_pixels=MAX_PIXELS,
    #     max_long_side=MAX_LONG_SIDE
    # )
    # img_resized = img.resize((resized_w, resized_h))
    # t4 = time.time()
    # print(f"[2] 图片缩放: {t4 - t3:.2f}s, 缩放后尺寸: {resized_w}x{resized_h}")

    # Step 3: 构建消息
    t5 = time.time()
    messages = build_messages(screenshot_path, current_instruction, history, model)
    t6 = time.time()
    print(f"[3] 构建消息: {t6 - t5:.2f}s")

    # Step 4: API 请求
    t7 = time.time()
    try:
        client = OpenAI(base_url=base_url, api_key=api_key)
        print(f"\n[4] 发送请求到 VLM: {base_url}")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        t8 = time.time()
        print(f"[4] API 响应时间: {t8 - t7:.2f}s")

        llm_response = response.choices[0].message.content
        thought = getattr(response.choices[0].message, "reasoning_content", None)
        if thought:
            llm_response = f"<thinking>\n{thought}\n</thinking>{llm_response}"

        print(f"\n[RESULT] VLM 响应:")
        print(llm_response[:500] + "..." if len(llm_response) > 500 else llm_response)
    except Exception as e:
        print(f"[ERROR] API 调用失败: {e}")
        llm_response = None

    total_end = time.time()
    print(f"\n" + "=" * 50)
    print(f"总耗时: {total_end - total_start:.2f}s")
    print(f"主要瓶颈: API响应 ({t8 - t7:.2f}s)")
    print("=" * 50)

    return llm_response


if __name__ == "__main__":
    analyze_latency()