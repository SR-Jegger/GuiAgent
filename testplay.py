# from playwright.sync_api import sync_playwright
# import json


# INTERACTIVE_SELECTOR = """
# button,
# input,
# textarea,
# select,
# a,
# [role=button],
# [role=link],
# [role=textbox],
# [role=combobox],
# [role=checkbox],
# [role=radio],
# [onclick],
# [tabindex]
# """


# def safe_get(locator, func, default=None):
#     try:
#         return func(locator)
#     except Exception:
#         return default


# def get_element_text(el):
#     text = safe_get(el, lambda x: x.inner_text(timeout=300), "")
#     if text:
#         return " ".join(text.split())

#     value = safe_get(el, lambda x: x.get_attribute("value"), "")
#     if value:
#         return value

#     return ""


# def get_element_info(el, index):
#     tag = safe_get(el, lambda x: x.evaluate("e => e.tagName.toLowerCase()"), "")
#     box = safe_get(el, lambda x: x.bounding_box(), None)

#     return {
#         "id": f"e{index}",
#         "tag": tag,
#         "role": safe_get(el, lambda x: x.get_attribute("role"), None),
#         "type": safe_get(el, lambda x: x.get_attribute("type"), None),
#         "text": get_element_text(el),
#         "placeholder": safe_get(el, lambda x: x.get_attribute("placeholder"), None),
#         "aria_label": safe_get(el, lambda x: x.get_attribute("aria-label"), None),
#         "title": safe_get(el, lambda x: x.get_attribute("title"), None),
#         "href": safe_get(el, lambda x: x.get_attribute("href"), None),
#         "name": safe_get(el, lambda x: x.get_attribute("name"), None),
#         "disabled": safe_get(
#             el,
#             lambda x: x.evaluate("e => e.disabled === true || e.getAttribute('aria-disabled') === 'true'"),
#             False
#         ),
#         "visible": safe_get(el, lambda x: x.is_visible(), False),
#         "box": box
#     }


# def extract_semantic_dom(page):
#     page.wait_for_load_state("domcontentloaded")

#     locators = page.locator(INTERACTIVE_SELECTOR)
#     count = locators.count()

#     elements = []

#     for i in range(count):
#         el = locators.nth(i)

#         try:
#             if not el.is_visible():
#                 continue

#             box = el.bounding_box()
#             if not box or box["width"] <= 0 or box["height"] <= 0:
#                 continue

#             info = get_element_info(el, len(elements))

#             # 简单过滤：没有任何语义信息的元素跳过
#             if not any([
#                 info["text"],
#                 info["placeholder"],
#                 info["aria_label"],
#                 info["title"],
#                 info["href"],
#                 info["name"]
#             ]):
#                 continue

#             elements.append(info)

#         except Exception:
#             continue

#     return {
#         "url": page.url,
#         "title": page.title(),
#         "elements": elements
#     }


# def main():
#     url = "http://localhost:3000/"

#     with sync_playwright() as p:
#         # browser = p.chromium.launch(headless=False)
#         browser = p.chromium.launch(
#         channel="chrome",   # 或 "msedge"
#         headless=False,
#         args=["--start-maximized"]
#         )
#         page = browser.new_page(no_viewport=True)
#         page.goto(url)

#         dom_tree = extract_semantic_dom(page)

#         print(json.dumps(dom_tree, ensure_ascii=False, indent=2))

#         browser.close()


# if __name__ == "__main__":
#     main()

import random
import json
from playwright.sync_api import sync_playwright, Page


URL = "http://localhost:3000/"


PLAN = [
    {"action": "observe", "wait": 500},

    {
        "action": "right_click_by_text",
        "text": "J20-112",
        "description": "右键点击无人机 J20-112 平台卡片"
    },

    {"action": "observe", "wait": 500},

    {
        "action": "click_by_text",
        "text": "指派任务"
    },

    {"action": "observe", "wait": 500},

    {
        "action": "fill_by_label_or_placeholder",
        "keywords": ["任务名称"],
        "value": "打击任务-C1"
    },

    {
        "action": "fill_by_label_or_placeholder",
        "keywords": ["打击", "侦察目标", "目标"],
        "value": "巴拉基地"
    },

    {
        "action": "click_by_text",
        "text": "确认并选择目标位置"
    },

    {"action": "observe", "wait": 500},

    {
        "action": "click_map_random_right",
        "description": "在地图右侧随机点击目标位置"
    }
]


INTERACTIVE_SELECTOR = """
button,
input,
textarea,
select,
a,
label,
span,
[role=button],
[role=menuitem],
[onclick],
[tabindex],
.leaflet-container
"""


def safe_call(func, default=None):
    try:
        return func()
    except Exception:
        return default


def normalize_text(text):
    if not text:
        return ""
    return " ".join(text.split())


def extract_semantic_dom(page: Page):
    page.wait_for_load_state("domcontentloaded")

    locator = page.locator(INTERACTIVE_SELECTOR)
    count = locator.count()

    elements = []

    for i in range(count):
        el = locator.nth(i)

        try:
            if not el.is_visible():
                continue

            box = el.bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
                continue

            text = normalize_text(safe_call(lambda: el.inner_text(timeout=300), ""))
            tag = safe_call(lambda: el.evaluate("e => e.tagName.toLowerCase()"), "")

            info = {
                "id": f"e{len(elements)}",
                "tag": tag,
                "role": safe_call(lambda: el.get_attribute("role")),
                "type": safe_call(lambda: el.get_attribute("type")),
                "text": text,
                "placeholder": safe_call(lambda: el.get_attribute("placeholder")),
                "aria_label": safe_call(lambda: el.get_attribute("aria-label")),
                "title": safe_call(lambda: el.get_attribute("title")),
                "name": safe_call(lambda: el.get_attribute("name")),
                "visible": True,
                "box": box
            }
            # print(f"元素 {info['id']} - tag: {info['tag']}, role: {info['role']}, text: {info['text'][:30]}")
            if not any([
                info["text"],
                info["placeholder"],
                info["aria_label"],
                info["title"],
                info["name"],
                "leaflet" in str(safe_call(lambda: el.get_attribute("class"), "")).lower()
            ]):
                continue

            elements.append(info)

        except Exception:
            continue

    return {
        "url": page.url,
        "title": page.title(),
        "elements": elements
    }


def center(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def find_element_by_text(dom, text):
    for el in dom["elements"]:
        full_text = " ".join([
            el.get("text") or "",
            el.get("aria_label") or "",
            el.get("title") or "",
            el.get("placeholder") or "",
            el.get("name") or ""
        ])

        if text in full_text:
            return el

    raise ValueError(f"未找到包含文本的元素: {text}")


def find_map_element(dom):
    candidates = []

    for el in dom["elements"]:
        text = el.get("text") or ""
        box = el.get("box") or {}

        if "Leaflet" in text or "高德地图" in text:
            candidates.append(el)

        # 兜底：面积较大的可见区域，很可能是地图
        if box.get("width", 0) > 500 and box.get("height", 0) > 300:
            candidates.append(el)

    if not candidates:
        raise ValueError("未找到地图区域")

    # 选面积最大的区域
    return max(
        candidates,
        key=lambda e: e["box"]["width"] * e["box"]["height"]
    )


def right_click_by_text(page, dom, text):
    el = find_element_by_text(dom, text)
    print(f"找到元素 {el['id']} - tag: {el['tag']}, role: {el['role']}, text: {el['text'][:30]}")
    x, y = center(el["box"])
    print(f"右键点击坐标: ({x:.1f}, {y:.1f})")
    page.mouse.click(x, y, button="right")
    # page.get_by_text(text, exact=False).right_click()


def click_by_text(page, text):
    page.get_by_text(text, exact=False).click()
    # page.get_by_text(text, exact=False).right_click()


def fill_by_label_or_placeholder(page, keywords, value):
    # 1. 优先尝试 label
    for kw in keywords:
        try:
            page.get_by_label(kw, exact=False).fill(value)
            return
        except Exception:
            pass

    # 2. 尝试 placeholder
    for kw in keywords:
        try:
            page.get_by_placeholder(kw, exact=False).fill(value)
            return
        except Exception:
            pass

    # 3. 兜底：找 label 文本附近的 input/textarea
    for kw in keywords:
        try:
            field = page.locator(
                f"text={kw}"
            ).locator(
                "xpath=following::input[1] | following::textarea[1]"
            )
            field.fill(value)
            return
        except Exception:
            pass

    raise ValueError(f"找不到输入框: {keywords}")


def click_map_random_right(page, dom):
    el = find_map_element(dom)
    box = el["box"]

    x = box["x"] + box["width"] * random.uniform(0.70, 0.90)
    y = box["y"] + box["height"] * random.uniform(0.30, 0.70)

    print(f"地图点击坐标: ({x:.1f}, {y:.1f})")
    page.mouse.click(x, y)


def execute_step(page, dom, step):
    action = step["action"]

    if action == "right_click_by_text":
        right_click_by_text(page, dom, step["text"])

    elif action == "click_by_text":
        click_by_text(page, step["text"])

    elif action == "fill_by_label_or_placeholder":
        fill_by_label_or_placeholder(
            page,
            step["keywords"],
            step["value"]
        )

    elif action == "click_map_random_right":
        click_map_random_right(page, dom)

    else:
        raise ValueError(f"未知 action: {action}")


def execute_plan(page, plan):
    dom = None

    for index, step in enumerate(plan):
        action = step["action"]
        print(f"\n[{index + 1}/{len(plan)}] 执行: {action}")

        if action == "observe":
            wait = step.get("wait", 300)
            page.wait_for_timeout(wait)

            dom = extract_semantic_dom(page)

            print(f"重新提取 DOM，元素数量: {len(dom['elements'])}")

            with open("latest_dom.json", "w", encoding="utf-8") as f:
                json.dump(dom, f, ensure_ascii=False, indent=2)

            continue

        if dom is None:
            dom = extract_semantic_dom(page)

        execute_step(page, dom, step)

        page.wait_for_timeout(step.get("wait_after", 500))

    return dom


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",   # 或 "msedge"
            args=["--start-maximized"]
        )

        page = browser.new_page(no_viewport=True)
        page.goto(URL)

        execute_plan(page, PLAN)

        page.wait_for_timeout(3000)
        browser.close()


if __name__ == "__main__":
    main()