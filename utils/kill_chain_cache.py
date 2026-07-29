"""杀伤链实时缓存：轮询页面 DOM，维护所有杀伤链卡片的快照。

供 task_decomposer_node 的 confirm_kill_chain_by_target dispatcher 查询：
  1. 用户说"确认丰田-6676"
  2. dispatcher 通过 get_kill_chain_cache() 拿到当前快照
  3. resolve_first/resolve_platform 找到对应链和平台
  4. parse_stage 读 img src 得到阶段 (fix/track/target/engage/assess)
  5. 生成对应阶段的 sub_steps

并发：_poll_loop 写 _chains_ref，读方走 chains property。Python 引用赋值在 GIL 下原子，无需锁。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _normalize(s: str | None) -> str:
    """规范化字符串：去短横、去空格。

    用户语音/手打常不带短横（"丰指8686"），但页面 DOM 和 cache 里
    target_id 是"丰指-8686"。所有匹配都先 normalize 再比较，避免
    短横差异导致漏匹配。模块级函数，供 KillChain.__post_init__ 和
    KillChainCache 共用。
    """
    if not s:
        return ""
    return s.replace("-", "").replace(" ", "")


@dataclass
class PlatformInfo:
    platform_type: str
    platform_id: str
    status_img_src: str | None = None
    stage: str | None = None  # 由 parse_stage 在 _parse_chain 时填好


@dataclass
class KillChain:
    target_id: str
    grid_id: str
    platforms: list[PlatformInfo] = field(default_factory=list)
    # 预计算的 normalized tokens（target_id/grid_id/platform_ids 去短横去空格后的版本）。
    # refresh 创建 KillChain 时 __post_init__ 一次性算好，resolve 直接用，避免每次重复 normalize。
    _norm_tokens: list[str] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        # 预计算 normalized tokens，仅含 target_id 和 platform_id。
        # grid_id 不参与匹配：它通常是网格编号，用户不会用它定位链，
        # 且实际抓取常为 "-1"/空等无效值，参与子串匹配会误命中所有链。
        # 长度过滤（>= 2）防御性兜底，避免单字符 token 误匹配。
        raw_tokens = [
            self.target_id,
            *(p.platform_id for p in self.platforms if p.platform_id),
        ]
        self._norm_tokens = [
            t for t in (_normalize(x) for x in raw_tokens)
            if t and len(t) >= 2
        ]

    @property
    def platform_ids(self) -> list[str]:
        return [p.platform_id for p in self.platforms if p.platform_id]

    def matches(self, token: str) -> bool:
        if not token:
            return False
        return (
            token == self.target_id
            or token == self.grid_id
            or token in self.platform_ids
        )

    def find_platform(self, platform_id: str) -> PlatformInfo | None:
        for p in self.platforms:
            if p.platform_id == platform_id:
                return p
        return None

    def pick_platform_by_priority(self) -> PlatformInfo | None:
        """按 (stage_priority DESC, platform_id ASC) 选最优平台。

        - 多平台不同阶段：选阶段最靠后的（assess > engage > target > track > fix）
        - 同阶段多平台：选 platform_id 数字小的
        - stage 不可解析的排最后
        """
        if not self.platforms:
            return None

        def sort_key(p: PlatformInfo) -> tuple:
            priority = KillChainCache.STAGE_PRIORITY.get(p.stage or "", 0)
            # platform_id 数字小的排前；非数字的按字符串排，数字优先
            pid = p.platform_id or ""
            try:
                pid_num = (0, int(pid))  # (0, num) 让数字排在非数字前
            except ValueError:
                pid_num = (1, pid)
            # stage 优先级取负 -> 降序；pid_num 升序
            return (-priority, pid_num)

        return min(self.platforms, key=sort_key)


class KillChainCache:
    """维护当前页面上所有杀伤链卡片的实时快照。"""

    # 单条杀伤链的边界 selector。注意：外层 div.kill_chain_card_grops.target_outer
    # 是整个杀伤链区域的容器（页面上只有 1 个），不是单条链的边界。每条链是
    # div.target_info_container 下的直接子 div。加 target_outer 前缀避免误匹配
    # 页面其他区域可能存在的 div.target_info_container。
    CHAIN_ROOT = "div.kill_chain_card_grops.target_outer > div.target_info_container > div"
    NAME_EL = "div.font_four > div.wrap_name"
    GRID_EL = "div.font_four > div.wrap_grids > div.default_grids.words"
    PLATFORM_EL = "div.wrap_each_mini_item > div"
    PLAT_TITLE_EL = "div.wrap_card_title"
    PLAT_NUM_EL = "div.wrap_num_img > div.wrap_num"
    PLAT_IMG_EL = "div.wrap_num_img > div.wrap_img"

    STAGE_KEYWORDS = ("fix", "track", "target", "engage", "assess")

    # 阶段优先级：数字越大越靠后，多平台不同阶段时选最靠后的
    STAGE_PRIORITY = {"fix": 1, "track": 2, "target": 3, "engage": 4, "assess": 5}

    def __init__(self) -> None:
        self._chains_ref: list[KillChain] = []
        self._poll_task: asyncio.Task | None = None
        self._last_snapshot: str = ""  # 变更检测，避免每秒重复打印

    @property
    def chains(self) -> list[KillChain]:
        """读快照（原子引用，无需锁）。"""
        return self._chains_ref

    @staticmethod
    def parse_stage(img_src: str | None) -> str | None:
        """从 img src 子串匹配阶段。src 例：'.../fix.png'、'.../track.png?v=1'。"""
        if not img_src:
            return None
        if "ding_wei" in img_src:
            return "fix"
        elif "gen_zong" in img_src:
            return "track"
        elif "miao_zhun" in img_src:
            return "target"
        elif "jiao_zhan" in img_src:
            return "engage"
        elif "hui_ping" in img_src:
            return "assess"
        return None

    @staticmethod
    def _normalize(s: str | None) -> str:
        """规范化字符串用于 dash-insensitive 匹配：去短横、去空格。

        保留为静态方法向后兼容（application_service._try_cache_dispatch 调用），
        内部转模块级函数。
        """
        return _normalize(s)

    @staticmethod
    def _split_tokens(s: str) -> set[str]:
        """把字符串分成 token 集合：汉字段 + 数字段分开。

        例：
          "确认206"        -> {"确认", "206"}
          "确认丰指8686"    -> {"确认", "丰指", "8686"}
          "206号平台"       -> {"206", "号平台"}
          "确认1206"        -> {"确认", "1206"}

        用于精确匹配：避免短 token（如"206"）误匹配长 token（如"1206"）
        的子串。用户输入"确认1206"分词后是 {"确认","1206"}，cache 的
        platform_id="206" normalized 后"206"不在这个集合里，精确匹配
        失败，不会误命中。
        """
        if not s:
            return set()
        # [^\W\d_]+ 匹配字母/汉字段（不含数字、下划线）
        # \d+      匹配数字段
        return set(re.findall(r"[^\W\d_]+|\d+", s))

    async def refresh(self, page) -> None:
        """从页面重新抓取所有杀伤链，原子替换快照。

        page 由调用方动态挑选（见 start_polling 注释），本方法只负责
        从指定 page 抓 DOM。page 为 None 时跳过本次刷新。
        """
        if page is None:
            return
        new_chains: list[KillChain] = []
        try:
            roots = await page.locator(self.CHAIN_ROOT).all()
        except Exception as e:
            logger.warning("[KillChainCache] refresh: locator.all() failed: %s", e)
            return
        for root in roots:
            chain = await self._parse_chain(root)
            if chain and chain.target_id:
                new_chains.append(chain)
        self._chains_ref = new_chains  # 原子换

    async def _parse_chain(self, root) -> KillChain | None:
        try:
            target_id = await root.locator(self.NAME_EL).first.inner_text(timeout=2000)
            target_id = target_id.strip()
        except Exception:
            return None

        try:
            grid_id = await root.locator(self.GRID_EL).first.inner_text(timeout=2000)
            grid_id = grid_id.strip()
        except Exception:
            grid_id = ""

        platforms: list[PlatformInfo] = []
        plat_nodes = await root.locator(self.PLATFORM_EL).all()
        for node in plat_nodes:
            try:
                ptype = await node.locator(self.PLAT_TITLE_EL).first.inner_text(timeout=1000)
                ptype = ptype.strip()
            except Exception:
                ptype = ""
            try:
                pid = await node.locator(self.PLAT_NUM_EL).first.inner_text(timeout=1000)
                pid = pid.strip()
            except Exception:
                pid = ""
            img_src = None
            try:
                img_src = await node.locator(self.PLAT_IMG_EL + " img").first.get_attribute("src", timeout=1000)
            except Exception:
                pass
            if pid or ptype:
                platforms.append(PlatformInfo(
                    platform_type=ptype,
                    platform_id=pid,
                    status_img_src=img_src,
                    stage=self.parse_stage(img_src),
                ))

        return KillChain(target_id=target_id, grid_id=grid_id, platforms=platforms)

    def resolve(self, user_input: str) -> list[KillChain]:
        """返回所有 token 命中的杀伤链（支持跨链，dash-insensitive）。

        匹配策略（两轮）：
        1. 精确匹配：cache 的 normalized token 出现在用户输入的分词 token 集合里。
           避免短 token 误匹配长 token 的子串（如"206"误匹配"1206"）。
        2. 子串兜底：cache 的 normalized token 是用户输入 normalized 后的子串。
           处理"确认目标丰指8686"这种汉字+数字粘连无法分词的情况。

        用户输入"丰指8686"能命中 cache 里 target_id="丰指-8686"的链。
        """
        if not user_input:
            return []
        user_input_norm = _normalize(user_input)
        user_tokens = self._split_tokens(user_input_norm)

        # 第一轮：精确匹配
        exact = [
            c for c in self.chains
            if any(t in user_tokens for t in c._norm_tokens)
        ]
        if exact:
            return exact

        # 第二轮：子串兜底
        return [
            c for c in self.chains
            if any(t and t in user_input_norm for t in c._norm_tokens)
        ]

    def resolve_first(self, user_input: str) -> KillChain | None:
        hits = self.resolve(user_input)
        return hits[0] if hits else None

    def resolve_platform(self, user_input: str) -> tuple[KillChain, PlatformInfo] | None:
        """优先匹配 platform_id；若命中 target_id/grid_id 但无具体 platform，返回 None。

        dash-insensitive：用户说"101"能命中 platform_id="101"。
        精确匹配优先：用户说"确认1206"不会误命中 platform_id="206"的链。
        """
        user_input_norm = _normalize(user_input)
        user_tokens = self._split_tokens(user_input_norm)

        # 第一轮：精确匹配 platform_id
        for chain in self.chains:
            for p in chain.platforms:
                if p.platform_id and _normalize(p.platform_id) in user_tokens:
                    return (chain, p)

        # 第二轮：子串兜底 platform_id
        for chain in self.chains:
            for p in chain.platforms:
                if p.platform_id and _normalize(p.platform_id) in user_input_norm:
                    return (chain, p)

        # 没命中 platform，看是否命中链（target_id/grid_id）
        chain = self.resolve_first(user_input)
        if chain:
            return None  # 命中链但未指定平台，由调用方处理歧义
        return None

    # ===== 轮询生命周期 =====

    async def start_polling(self, browser_tools, interval: float = 1.0) -> None:
        """启动后台轮询任务。重复调用安全（已有任务则不重启）。

        Args:
            browser_tools: BrowserTools 实例。每次 refresh 前调它的
                pick_kill_chain_page() 动态拿当前指控页，避免固定 page
                引用因标签页关闭/切换而失效。
            interval: 轮询间隔（秒）。
        """
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop(browser_tools, interval))
        logger.info("[KillChainCache] polling started (interval=%ss)", interval)

    async def stop_polling(self) -> None:
        """停止轮询任务。"""
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        await asyncio.gather(self._poll_task, return_exceptions=True)
        self._poll_task = None
        logger.info("[KillChainCache] polling stopped")

    async def _poll_loop(self, browser_tools, interval: float) -> None:
        """轮询主循环。cancel 即退出。

        每次迭代都通过 browser_tools.pick_kill_chain_page() 重新拿当前
        指控页，避免固定 page 引用失效。拿不到 page（用户没开指控页/
        标签页被关）时跳过本次刷新，cache 保持上次快照。
        """
        while True:
            try:
                page = await browser_tools.pick_kill_chain_page()
                await self.refresh(page)
                self._log_if_changed()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[KillChainCache] poll iteration failed: %s", e)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    def _log_if_changed(self) -> None:
        """快照变化时打印（新增/移除链、阶段切换、平台增减）。"""
        snapshot = self._snapshot_str()
        if snapshot == self._last_snapshot:
            return
        if not self._chains_ref:
            print(f"[KillChainCache] {len(self._chains_ref)} chain(s) (empty)")
        else:
            print(f"[KillChainCache] {len(self._chains_ref)} chain(s): {snapshot}")
        self._last_snapshot = snapshot

    def _snapshot_str(self) -> str:
        """紧凑快照：target_id[grid](pid:stage,...)。"""
        parts = []
        for c in self._chains_ref:
            plats = ",".join(
                f"{p.platform_id}:{p.stage or '?'}" for p in c.platforms
            )
            parts.append(f"{c.target_id}[{c.grid_id}]({plats})")
        return " | ".join(parts)


# ===== 模块级单例 =====

_singleton: KillChainCache | None = None


def get_kill_chain_cache() -> KillChainCache:
    """懒加载单例（仿 nodes.task_decomposer_node.get_intent_mapping_config）。"""
    global _singleton
    if _singleton is None:
        _singleton = KillChainCache()
    return _singleton


# ===== 数字优先匹配 + 同音错字纠正（方案一） =====

def find_chain_by_number(text: str) -> KillChain | None:
    """从文本里抽数字标识，在 cache 里找唯一命中的杀伤链。

    数字优先匹配策略（方案一）：
    1. 从 text 抽 \\d{2,} 数字段（>=2 位过滤单字符噪声），长度降序
       dict.fromkeys 保序去重，同长度按出现位置先到先尝试
    2. 在 cache 里找 target_id 的数字部分 == 抽到的数字
    3. 唯一命中即返回；多条命中尝试用 target_id 子串在 text 里消歧
    4. 都失败返回 None

    用于 ASR 同音不同字场景：数字部分准确，可作为强信号定位链。
    例：cache 有 "剑发-8686"，ASR 把 "剑发" 识别成 "建发"，
        "打击建发8686" 仍能通过 "8686" 命中。

    注意：仅匹配 target_id，不匹 platform_id/grid_id，
    避免 "确认206" 误触发 target dispatcher。
    """
    if not text:
        return None
    cache = get_kill_chain_cache()
    if not cache.chains:
        return None

    norm = _normalize(text)
    numbers = sorted(
        dict.fromkeys(re.findall(r"\d{2,}", norm)),
        key=len,
        reverse=True,
    )
    for num in numbers:
        hits = [
            c for c in cache.chains
            if c.target_id
            and num in re.findall(r"\d+", _normalize(c.target_id))
        ]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            # 数字不足以消歧（多条链共用同一编号），
            # 用 target_id 子串在 text 里定位（要求汉字部分也对得上）
            for chain in hits:
                target_norm = _normalize(chain.target_id)
                if target_norm and target_norm in norm:
                    return chain
            return None
    return None


def correct_target_id_homophone(text: str) -> str:
    """纠正 ASR 同音错字：用 cache 真实 target_id 替换 text 里的同音汉字部分。

    例：cache 有 "剑发-8686"，ASR 把 "剑发" 识别成 "建发"，
        "打击建发8686" -> "打击剑发8686"

    纯字符串操作，<1ms。未命中 cache 或数字未唯一匹配时原样返回。

    限制：
    - 只替换数字前紧邻的等长纯汉字段。如果 text 里数字前不是纯汉字
      （含短横/空格等，如 "打击建发-8686"），不替换。
    - 纯数字输入（如 "8686"）不替换（没有汉字部分可纠正）。
    - 多条链同号且汉字也对不上时原样返回（保守，不强行猜）。
    """
    if not text:
        return text
    chain = find_chain_by_number(text)
    if chain is None or not chain.target_id:
        return text

    target_norm = _normalize(chain.target_id)
    # target_id 的汉字部分（去数字、短横、空格）
    chinese_part = re.sub(r"[\d\s-]", "", target_norm)
    if not chinese_part:
        return text  # target_id 没有汉字部分，没法替换

    # 用最长的数字段在 text 里定位（避免短数字误匹配）
    target_nums = re.findall(r"\d+", target_norm)
    if not target_nums:
        return text
    target_num = max(target_nums, key=len)
    match = re.search(target_num, text)
    if not match:
        return text

    end_pos = match.start()
    chinese_len = len(chinese_part)
    if end_pos < chinese_len:
        return text  # 数字前字符不够，说明没有汉字部分（如 "8686"），不替换
    start_pos = end_pos - chinese_len
    prefix = text[start_pos:end_pos]
    # 只替换数字前紧邻的纯汉字段（避免误替换 "打击8686" 这种）。
    # range 一-龥 是 CJK 统一汉字基本区。
    if not re.match(r"^[一-龥]+$", prefix):
        return text
    # 额外检查：text 里除 prefix 外还要有其他汉字，才能确认 prefix 是
    # target_id 汉字部分（ASR 错字）而非操作动词。
    # 例："打击建发8686" 除"建发"外有"打击" -> 替换
    #     "打击8686" 除"打击"外无其他汉字 -> 不替换（"打击"是操作动词）
    #     "建发8686" 除"建发"外无其他汉字 -> 不替换（无操作动词的边界 case）
    rest_text = text[:start_pos] + text[match.end():]
    if not re.search(r"[一-龥]", rest_text):
        return text
    return text[:start_pos] + chinese_part + text[end_pos:]


# ===== 优先级配置 =====

_priority_config_cache: dict | None = None


def load_priority_config(path: str = "data/kill_chain_priority.json") -> dict:
    """读优先级 JSON，返回 {prefixes: [{prefix, priority}], default}。模块级缓存。"""
    global _priority_config_cache
    if _priority_config_cache is not None:
        return _priority_config_cache
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _priority_config_cache = {
            "prefixes": data.get("target_priorities", []),
            "default": data.get("default_priority", 1),
        }
    except Exception as e:
        logger.warning("[KillChainCache] load_priority_config failed: %s, using default", e)
        _priority_config_cache = {"prefixes": [], "default": 1}
    return _priority_config_cache


def get_priority(target_id: str, config: dict | None = None) -> int:
    """按前缀匹配返回 target_id 的优先级。无匹配返回 default。"""
    if config is None:
        config = load_priority_config()
    for entry in config.get("prefixes", []):
        prefix = entry.get("prefix", "")
        if prefix and target_id.startswith(prefix):
            return entry.get("priority", config.get("default", 1))
    return config.get("default", 1)

