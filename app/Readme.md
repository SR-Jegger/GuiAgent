# 总结

  异步 Worker 指的是：
  - FastAPI 启动时在后台运行的 asyncio 协程
  - 从任务队列中持续取任务执行
  - 使用 await run_agent_async() 调用你的 Agent 代码
  - 支持并发处理多个 HTTP 请求、支持任务取消

✻ Brewed for 52s

❯ 所以是并发处理多个 HTTP 请求，但任务依然是放到队列里按顺序执行

● 是的，完全正确！

  并发模型总结

  ┌─────────────────────────────────────────────────────────────┐
  │                    HTTP 请求层（并发）                        │
  │                                                               │
  │  用户 A ──► POST /tasks ──┐                                  │
  │  用户 B ──► POST /tasks ──├──► FastAPI 立即响应               │
  │  用户 C ──► GET /tasks ───┘    （不阻塞）                     │
  └─────────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    任务队列层（串行）                         │
  │                                                               │
  │   ┌─────────┐  ┌─────────┐  ┌─────────┐                     │
  │   │ Task-A  │→ │ Task-B  │→ │ Task-C  │                     │
  │   └─────────┘  └─────────┘  └─────────┘                     │
  │                         │                                    │
  │                         ▼                                    │
  │              ┌───────────────────┐                          │
  │              │    Worker-0       │  ← 只有 1 个 worker        │
  │              │  run_agent_async  │     串行执行任务          │
  │              └───────────────────┘                          │
  └─────────────────────────────────────────────────────────────┘

  为什么这样设计？

  ┌───────────┬──────┬───────────────────────────────────────────────┐
  │   层面    │ 模型 │                     原因                      │
  ├───────────┼──────┼───────────────────────────────────────────────┤
  │ HTTP 请求 │ 并发 │ FastAPI 原生支持，查询状态/取消任务不需要等待 │
  ├───────────┼──────┼───────────────────────────────────────────────┤
  │ 任务执行  │ 串行 │ pyautogui 不是线程安全的，同时执行会冲突      │
  └───────────┴──────┴───────────────────────────────────────────────┘

  如果想并行执行多个任务？

  修改 TaskManager 的 worker 数量即可：
  server.py中的  task_manager = TaskManager(max_concurrent=3)  # 3 个 worker 并行

  但 GUI 自动化场景下，通常不建议并行，因为：
  - 鼠标/键盘是共享资源
  - 多个任务同时操作会互相干扰
  
  # 三个核心类详解

---
## 1. TaskStatus - 任务状态枚举

  class TaskStatus(str, Enum):
      """任务状态枚举"""
      PENDING = "pending"       # 待处理 - 任务已创建，等待执行
      RUNNING = "running"       # 运行中 - 任务正在执行
      COMPLETED = "completed"   # 已完成 - 任务成功完成
      FAILED = "failed"         # 已失败 - 任务执行失败
      CANCELLED = "cancelled"   # 已取消 - 任务被用户取消

  状态流转图：
  PENDING → RUNNING → COMPLETED
                │
                ├→ FAILED
                │
                └→ CANCELLED

  ---
## 2. TaskRequest - 请求模型

  class TaskRequest(BaseModel):
      """创建任务时的请求参数"""
      task_name: Optional[str] = "default_task"  # 任务名称（可选，用于输出目录命名）
      instruction: str                           # 任务指令（必填），如 "Open Notepad"
      max_steps: int = 50                        # 最大执行步数（默认 50 步）
      max_retries: int = 3                       # 每步最大重试次数（默认 3 次）
      add_info: Optional[str] = None             # 补充信息（可选），如额外知识/提示
      rules_dir: str = "./rules"                 # 规则文件目录（默认 ./rules）

  使用示例：
  POST /api/v1/tasks
  {
    "task_name": "my_task",
    "instruction": "Open Notepad and type hello",
    "max_steps": 30,
    "max_retries": 2,
    "add_info": "The target app is already open",
    "rules_dir": "./rules"
  }

  ---
  ## 3. TaskResponse - 响应模型

  class TaskResponse(BaseModel):
      """任务操作的响应数据"""
      task_id: str               # 任务唯一 ID（UUID 格式）
      status: str                # 当前状态（TaskStatus 的值）
      created_at: float          # 创建时间戳（秒）
      started_at: Optional[float] = None    # 开始执行时间戳（可选）
      completed_at: Optional[float] = None  # 完成时间戳（可选）
      result: Optional[Dict] = None         # 执行结果（可选），包含最终状态
      error: Optional[str] = None           # 错误信息（可选），失败时填写

  响应示例：
  // 创建任务后返回
  {
    "task_id": "abc123-def456",
    "status": "pending",
    "created_at": 1711234567.890
  }

  // 查询已完成任务返回
  {
    "task_id": "abc123-def456",
    "status": "completed",
    "created_at": 1711234567.890,
    "started_at": 1711234568.100,
    "completed_at": 1711234590.500,
    "result": {
      "final_state": {
        "step_id": 5,
        "stop_flag": true,
        "output_dir": "C:/Users/.../anno"
      }
    },
    "error": null
  }

  ---
## 三者关系

  用户请求 ──► TaskRequest  ──► 创建 Task 对象 ──► 执行 ──► TaskStatus 变化
                                                          │
                                                          ▼
  用户查询 ◄── TaskResponse ◄───────────────────── 返回结果

---

## 疑惑解答1
### ❯ create_app()里我只在create_task里运行了task_manager.submit(task)，并没有start，那么任务是怎么开始运行的?

● 好问题！关键在于 submit() 之后，任务是被 后台 Worker 自动取走执行的。

  让我看看代码流程：

  Read 1 file (ctrl+o to expand)

● 看到了！关键是 lifespan 中的 await task_manager.start() 在服务器启动时就运行了。

  完整流程

  ┌─────────────────────────────────────────────────────────────┐
  │  1. 服务器启动时（lifespan startup）                          │
  ├─────────────────────────────────────────────────────────────┤
  │                                                              │
  │  await task_manager.start()                                  │
  │           │                                                   │
  │           ▼                                                   │
  │  启动 Worker 协程（后台运行）                                   │
  │  ┌─────────────────────────────────────────────────────────┐│
  │  │ async def _worker(self, worker_id: int):                ││
  │  │     while True:  ← 无限循环                             ││
  │  │         task = await self._queue.get()  ← 等待任务       ││
  │  │         await self._execute_task(worker_id, task)       ││
  │  └─────────────────────────────────────────────────────────┘│
  │                                                              │
  └─────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────┐
  │  2. 用户请求创建任务时（create_task）                         │
  ├─────────────────────────────────────────────────────────────┤
  │                                                              │
  │  await task_manager.submit(task)                             │
  │           │                                                   │
  │           ▼                                                   │
  │  await self._queue.put(task)  ← 放入队列                     │
  │           │                                                   │
  │           ▼                                                   │
  │  立即返回 task_id（不等待执行）                                │
  │                                                              │
  └─────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────┐
  │  3. Worker 自动取走任务（后台）                                 │
  ├─────────────────────────────────────────────────────────────┤
  │                                                              │
  │  Worker 从队列取出任务                                         │
  │           │                                                   │
  │           ▼                                                   │
  │  await run_agent_async(...)  ← 执行任务                     │
  │                                                              │
  └─────────────────────────────────────────────────────────────┘

  关键点

  ┌─────────────┬───────────────────────────────────────┬──────────────────────┐
  │    时机     │               代码位置                │         动作         │
  ├─────────────┼───────────────────────────────────────┼──────────────────────┤
  │ 服务器启动  │ lifespan() → task_manager.start()     │ 启动 Worker 后台运行 │
  ├─────────────┼───────────────────────────────────────┼──────────────────────┤
  │ 收到请求    │ create_task() → task_manager.submit() │ 任务加入队列         │
  ├─────────────┼───────────────────────────────────────┼──────────────────────┤
  │ Worker 执行 │ _worker() → _execute_task()           │ 从队列取任务并执行   │
  └─────────────┴───────────────────────────────────────┴──────────────────────┘

  Worker 的无限循环

  async def _worker(self, worker_id: int):
      while True:  # ← 服务器运行期间一直循环
          task = await self._queue.get()  # ← 队列空时会等待
          await self._execute_task(worker_id, task)  # ← 执行任务

  所以 Worker 是服务器启动时就启动的后台线程，一直在等待队列中的任务。

---
## lifesapn
  ┌─────────────────────────────────────────────────────────────┐
  │  uvicorn app.server:app 启动                                 │
  │           │                                                   │
  │           ▼                                                   │
  │  FastAPI 创建 app 实例                                         │
  │           │                                                   │
  │           ▼                                                   │
  │  调用 lifespan(app)                                           │
  │           │                                                   │
  │           ▼                                                   │
  │  运行 yield 之前的代码（初始化）                                 │
  │           │                                                   │
  │           ▼                                                   │
  │  遇到 yield ──────────────────► 服务器开始接受请求             │
  │                                      │                        │
  │                                      │ (服务器运行中...)       │
  │                                      │                        │
  │           ◄─────────────────────────┘                        │
  │           │                                                   │
  │  Ctrl+C 或收到关闭信号                                         │