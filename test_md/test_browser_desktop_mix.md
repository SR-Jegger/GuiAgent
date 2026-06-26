## 自动化任务-浏览器+桌面混合示例

本示例演示同一个任务中穿插使用 Playwright 浏览器动作与桌面动作:
- 浏览器动作(navigate/fill/click/wait)走 Playwright,精确作用于 DOM
- 桌面动作(双击图标、键盘输入)走 pyautogui,作用于操作系统
- 两者在 sub-step 之间任意穿插,无需切换模式

【自动化任务】打开浏览器搜索并记录到记事本
步骤0：使用 Playwright 导航到 http://localhost:3000/
步骤1：使用 Playwright 在搜索框(#search-box)中输入 "playwright"
步骤2：使用 Playwright 点击搜索按钮(#search-btn)
步骤3：使用 Playwright 等待搜索结果(.results)出现
步骤4：在桌面双击打开记事本
步骤5：在记事本中输入搜索完成,关键词为 playwright
