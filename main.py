from __future__ import annotations

import shlex
from typing import Dict, List, Optional, Sequence, Tuple, Union

from astrbot.api import logger, llm_tool
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config import AstrBotConfig
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.message.components import Image, Plain, Reply

try:
    from .pyjimeng.errors import JimengAPIError
    from .pyjimeng.jimeng_service import JimengAPIService
    from .pyjimeng import constants as jimeng_constants
except ImportError:  # pragma: no cover - fallback for direct execution
    from pyjimeng.errors import JimengAPIError  # type: ignore
    from pyjimeng.jimeng_service import JimengAPIService  # type: ignore
    from pyjimeng import constants as jimeng_constants  # type: ignore


@register(
    "jimeng2api",
    "AstrBot Contributors",
    "在 AstrBot 内直接调用 Jimeng（Dreamina）生成服务。",
    "0.1.0",
)
class JimengServicePlugin(Star):
    """提供 Jimeng 文生图、图生图与文生视频指令的插件。"""

    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context, config)
        self.config = config
        self.session_ids: List[str] = []
        self.auto_start: bool = False
        self.image_defaults: Dict[str, Union[str, float]] = {}
        self.video_defaults: Dict[str, Union[str, int]] = {}
        self.service: Optional[JimengAPIService] = None
        self._supported_image_models: Tuple[str, ...] = tuple(
            jimeng_constants.IMAGE_MODEL_MAP.keys()
        )
        self._supported_video_models: Tuple[str, ...] = tuple(
            jimeng_constants.VIDEO_MODEL_MAP.keys()
        )
        self._load_config()

    async def initialize(self) -> None:
        logger.info(
            "JimengServicePlugin 已初始化（session 数量=%d, auto_start=%s）",
            len(self.session_ids),
            self.auto_start,
        )
        if self.auto_start and self.session_ids:
            service = self._ensure_service()
            try:
                service.start()
                logger.info("已在插件初始化时自动启动 Jimeng 服务。")
            except JimengAPIError as exc:
                logger.error("自动启动 Jimeng 服务失败：%s", exc)

    async def terminate(self) -> None:
        if self.service and self.service.is_running():
            self.service.stop()
            logger.info("已停止 Jimeng 服务。")

    @filter.command_group("jimeng")
    def jimeng(self):
        """与 Jimeng 生成接口交互的指令组。"""
        pass

    @jimeng.command("status")
    async def jimeng_status(self, event: AstrMessageEvent):
        """查看服务运行状态与远程 session 健康情况。"""
        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return
        try:
            status = service.check_session_status()
        except JimengAPIError as exc:
            logger.exception("查询 Jimeng 状态失败。")
            yield event.plain_result(f"Jimeng 接口错误：{exc}")
            return

        running = "是" if service.is_running() else "否"
        live = status.get("live")
        session_line = ", ".join(self.session_ids) if self.session_ids else "无"
        yield event.plain_result(
            "Jimeng 服务状态：\n"
            f"- 本地服务运行中：{running}\n"
            f"- 远程 session 存活：{live}\n"
            f"- 已配置 session id：{session_line}"
        )

    @jimeng.command("start")
    async def jimeng_start(self, event: AstrMessageEvent):
        """启动本地 Jimeng 服务包装器。"""
        if not self.session_ids:
            yield event.plain_result(
                "尚未配置任何 session id，请先执行 /jimeng session set <token>。"
            )
            return
        service = self._ensure_service()
        if service.is_running():
            yield event.plain_result("Jimeng 服务已在运行。")
            return
        try:
            service.start()
        except JimengAPIError as exc:
            logger.exception("启动 Jimeng 服务失败。")
            yield event.plain_result(f"启动 Jimeng 服务失败：{exc}")
            return
        yield event.plain_result("已启动 Jimeng 服务。")

    @jimeng.command("stop")
    async def jimeng_stop(self, event: AstrMessageEvent):
        """停止本地 Jimeng 服务包装器。"""
        if not self.service or not self.service.is_running():
            yield event.plain_result("Jimeng 服务当前未运行。")
            return
        self.service.stop()
        yield event.plain_result("已停止 Jimeng 服务。")

    @jimeng.command("points")
    async def jimeng_points(self, event: AstrMessageEvent):
        """查询当前 session id 的积分信息。"""
        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return
        try:
            records = service.get_points()
        except JimengAPIError as exc:
            logger.exception("获取 Jimeng 积分信息失败。")
            yield event.plain_result(f"Jimeng 接口错误：{exc}")
            return
        if not records:
            yield event.plain_result("Jimeng 未返回任何积分记录。")
            return

        lines = ["Jimeng 积分概览："]
        for item in records:
            token = item.get("token", "<未知>")
            points = item.get("points") or {}
            total = points.get("totalCredit", "未知")
            gift = points.get("giftCredit", "未知")
            purchase = points.get("purchaseCredit", "未知")
            vip = points.get("vipCredit", "未知")
            lines.append(
                f"- {token}: 总积分={total}, 赠送={gift}, 购买={purchase}, VIP={vip}"
            )
        yield event.plain_result("\n".join(lines))

    @jimeng.command("image")
    async def jimeng_image(self, event: AstrMessageEvent, prompt: GreedyStr):
        """根据文本生成图片，支持 key=value 覆盖参数。"""
        raw_prompt = prompt.strip()
        reply_text, _ = self._extract_reply_context(event)
        prompt_text, options = self._extract_prompt_options(raw_prompt)
        prompt_text = self._resolve_prompt(event, prompt_text, reply_text)
        if not prompt_text:
            yield event.plain_result("请提供用于生成的提示词，可以通过引用消息实现。")
            return

        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return

        model = options.get("model", self.image_defaults["model"])
        ratio = options.get("ratio", self.image_defaults["ratio"])
        resolution = options.get("resolution", self.image_defaults["resolution"])
        response_format = options.get("response", self.image_defaults["response_format"])
        negative_prompt = options.get("negative", self.image_defaults["negative"])
        sample_strength = self._coerce_float(
            options.get("sample"), float(self.image_defaults["sample_strength"])
        )
        session_override = self._parse_session_override(options.get("session"))
        model_error = self._validate_image_model(model)
        if model_error:
            yield event.plain_result(model_error)
            return

        media_results, error_message, headline = self._generate_image_with_service(
            service,
            prompt=prompt_text,
            model=model,
            ratio=ratio,
            resolution=resolution,
            response_format=response_format,
            negative_prompt=negative_prompt,
            sample_strength=sample_strength,
            session_override=session_override,
        )
        if error_message:
            yield event.plain_result(error_message)
            return
        for item in media_results:
            yield item
        if headline:
            yield event.plain_result(headline)

    @jimeng.command("compose")
    async def jimeng_compose(
        self, event: AstrMessageEvent, sources: str, prompt: GreedyStr
    ):
        """使用已有图片链接进行再创作。"""
        raw_prompt = prompt.strip()
        reply_text, reply_images = self._extract_reply_context(event)
        images = self._split_tokens(sources)

        if len(images) == 1:
            token = images[0].strip()
            token_lower = token.lower()
            if token in {"引用", "引用消息"} or token_lower in {"reply", "use_reply", "quoted", "quote", "-"}:
                images = []

        if not images:
            images = reply_images

        if not images:
            yield event.plain_result("请提供至少一张源图，或引用带图片的消息。")
            return

        prompt_text, options = self._extract_prompt_options(raw_prompt)
        prompt_text = self._resolve_prompt(event, prompt_text, reply_text)
        if not prompt_text:
            yield event.plain_result("请提供用于生成的提示词，可以通过引用消息实现。")
            return

        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return

        model = options.get("model", self.image_defaults["model"])
        ratio = options.get("ratio", self.image_defaults["ratio"])
        resolution = options.get("resolution", self.image_defaults["resolution"])
        response_format = options.get("response", self.image_defaults["response_format"])
        negative_prompt = options.get("negative", self.image_defaults["negative"])
        sample_strength = self._coerce_float(
            options.get("sample"), float(self.image_defaults["sample_strength"])
        )
        session_override = self._parse_session_override(options.get("session"))
        model_error = self._validate_image_model(model)
        if model_error:
            yield event.plain_result(model_error)
            return

        media_results, error_message, headline = self._compose_image_with_service(
            service,
            prompt=prompt_text,
            images=images,
            model=model,
            ratio=ratio,
            resolution=resolution,
            response_format=response_format,
            negative_prompt=negative_prompt,
            sample_strength=sample_strength,
            session_override=session_override,
        )
        if error_message:
            yield event.plain_result(error_message)
            return
        for item in media_results:
            yield item
        if headline:
            yield event.plain_result(headline)

    @jimeng.command("video")
    async def jimeng_video(self, event: AstrMessageEvent, prompt: GreedyStr):
        """根据文本生成短视频。"""
        raw_prompt = prompt.strip()
        reply_text, _ = self._extract_reply_context(event)
        prompt_text, options = self._extract_prompt_options(raw_prompt)
        prompt_text = self._resolve_prompt(event, prompt_text, reply_text)
        if not prompt_text:
            yield event.plain_result("请提供用于生成的提示词，可以通过引用消息实现。")
            return

        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return

        model = options.get("model", self.video_defaults["model"])
        width = self._coerce_int(options.get("width"), int(self.video_defaults["width"]))
        height = self._coerce_int(options.get("height"), int(self.video_defaults["height"]))
        resolution = options.get("resolution", self.video_defaults["resolution"])
        response_format = options.get("response", self.video_defaults["response_format"])
        session_override = self._parse_session_override(options.get("session"))
        model_error = self._validate_video_model(model)
        if model_error:
            yield event.plain_result(model_error)
            return

        media_results, error_message, headline = self._generate_video_with_service(
            service,
            prompt=prompt_text,
            model=model,
            width=width,
            height=height,
            resolution=resolution,
            response_format=response_format,
            session_override=session_override,
        )
        if error_message:
            yield event.plain_result(error_message)
            return
        for item in media_results:
            yield item
        if headline:
            yield event.plain_result(headline)

    @jimeng.command("auto")
    async def jimeng_auto(self, event: AstrMessageEvent, state: str):
        """设置自动启动（on/off）。"""
        normalized = state.strip().lower()
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            self.auto_start = True
        elif normalized in {"off", "disable", "disabled", "false", "0"}:
            self.auto_start = False
        else:
            yield event.plain_result("用法：/jimeng auto on|off")
            return
        self._save_config()
        yield event.plain_result(f"自动启动状态已更新为 {self.auto_start}。")

    @jimeng.group("session")
    def jimeng_session(self):
        """管理 Jimeng session token。"""
        pass

    @jimeng_session.command("list")
    async def session_list(self, event: AstrMessageEvent):
        """列出已配置的 session id。"""
        if not self.session_ids:
            yield event.plain_result("当前未配置任何 session id。")
            return
        lines = ["Jimeng session id 列表："] + [
            f"{idx + 1}. {token}" for idx, token in enumerate(self.session_ids)
        ]
        yield event.plain_result("\n".join(lines))

    @jimeng_session.command("set")
    async def session_set(self, event: AstrMessageEvent, tokens: GreedyStr):
        """使用给定 token 覆盖现有 session id。"""
        new_tokens = self._split_tokens(tokens)
        if not new_tokens:
            yield event.plain_result(
                "用法：/jimeng session set <token> [token...] （逗号或空格分隔）"
            )
            return
        self.session_ids = new_tokens
        self._sync_service_sessions()
        self._save_config()
        yield event.plain_result(
            f"已配置 {len(self.session_ids)} 个 session id。"
            "可通过 /jimeng session list 查看。"
        )

    @jimeng_session.command("add")
    async def session_add(self, event: AstrMessageEvent, tokens: GreedyStr):
        """追加一个或多个 session id。"""
        new_tokens = self._split_tokens(tokens)
        if not new_tokens:
            yield event.plain_result(
                "用法：/jimeng session add <token> [token...] （逗号或空格分隔）"
            )
            return

        added = 0
        for token in new_tokens:
            if token not in self.session_ids:
                self.session_ids.append(token)
                added += 1
        if added:
            self._sync_service_sessions()
            self._save_config()
        yield event.plain_result(f"新增 {added} 个 session id。")

    @jimeng_session.command("remove")
    async def session_remove(self, event: AstrMessageEvent, tokens: GreedyStr):
        """移除一个或多个 session id。"""
        target_tokens = self._split_tokens(tokens)
        if not target_tokens:
            yield event.plain_result(
                "用法：/jimeng session remove <token> [token...] （逗号或空格分隔）"
            )
            return

        removed = 0
        for token in target_tokens:
            if token in self.session_ids:
                self.session_ids.remove(token)
                removed += 1
        if removed:
            self._sync_service_sessions(stop_on_empty=True)
            self._save_config()
        yield event.plain_result(f"移除 {removed} 个 session id。")

    @jimeng_session.command("clear")
    async def session_clear(self, event: AstrMessageEvent):
        """清空所有已配置的 session id。"""
        self.session_ids.clear()
        self._sync_service_sessions(stop_on_empty=True)
        self._save_config()
        yield event.plain_result("已清空所有 session id。")

    @llm_tool(name="jimeng_image")
    async def tool_jimeng_image(
        self,
        event: AstrMessageEvent,
        prompt: str,
        model: str = "",
        ratio: str = "",
        resolution: str = "",
        response_format: str = "",
        negative_prompt: str = "",
        sample_strength: Optional[float] = None,
        session: str = "",
    ):
        """向 Jimeng 请求文生图。

        Args:
            prompt(string): 主要提示词，可留空并引用消息。
            model(string, optional): 模型名称，留空则沿用配置默认值。
            ratio(string, optional): 画面比例，留空则沿用配置默认值。
            resolution(string, optional): 分辨率标签，留空则沿用配置默认值。
            response_format(string, optional): 返回格式，支持 url 或 b64_json。
            negative_prompt(string, optional): 负面提示词。
            sample_strength(number, optional): 采样强度，留空使用默认值。
            session(string, optional): 逗号分隔的 session id 覆盖值。
        """
        reply_text, _ = self._extract_reply_context(event)
        prompt_text = self._resolve_prompt(event, prompt, reply_text)
        if not prompt_text:
            yield event.plain_result("未提供提示词。")
            return
        session_value = session
        if isinstance(session_value, list):
            session_value = ",".join(str(item) for item in session_value)

        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return

        model_value = model or self.image_defaults["model"]
        model_error = self._validate_image_model(model_value)
        if model_error:
            yield event.plain_result(model_error)
            return
        ratio_value = ratio or self.image_defaults["ratio"]
        resolution_value = resolution or self.image_defaults["resolution"]
        response_format_value = response_format or self.image_defaults["response_format"]
        negative_value = negative_prompt or self.image_defaults["negative"]
        sample_value = self._coerce_float(
            sample_strength, float(self.image_defaults["sample_strength"])
        )
        session_override = (
            self._parse_session_override(session_value) if session_value else None
        )

        media_results, error_message, headline = self._generate_image_with_service(
            service,
            prompt=prompt_text,
            model=model_value,
            ratio=ratio_value,
            resolution=resolution_value,
            response_format=response_format_value,
            negative_prompt=negative_value,
            sample_strength=sample_value,
            session_override=session_override,
        )
        if error_message:
            yield event.plain_result(error_message)
            return
        for item in media_results:
            yield item
        if headline:
            yield event.plain_result(headline)
        return

    @llm_tool(name="jimeng_image_compose")
    async def tool_jimeng_compose(
        self,
        event: AstrMessageEvent,
        prompt: str,
        image_urls: str = "",
        model: str = "",
        ratio: str = "",
        resolution: str = "",
        response_format: str = "",
        negative_prompt: str = "",
        sample_strength: Optional[float] = None,
        session: str = "",
    ):
        """使用 Jimeng 图生图能力。

        Args:
            prompt(string): 主要提示词，可留空并引用消息。
            image_urls(string, optional): 源图链接，支持逗号或空格分隔，留空可引用消息中的图片。
            model(string, optional): 模型名称。
            ratio(string, optional): 画面比例。
            resolution(string, optional): 分辨率标签。
            response_format(string, optional): 返回格式，支持 url 或 b64_json。
            negative_prompt(string, optional): 负面提示词。
            sample_strength(number, optional): 采样强度。
            session(string, optional): 逗号分隔的 session id 覆盖值。
        """
        reply_text, reply_images = self._extract_reply_context(event)

        if isinstance(image_urls, list):
            images = [str(item) for item in image_urls]
        else:
            images = self._split_tokens(image_urls or "")

        if len(images) == 1:
            token = images[0].strip()
            token_lower = token.lower()
            if token in {"引用", "引用消息"} or token_lower in {"reply", "use_reply", "quoted", "quote", "-"}:
                images = []

        if not images:
            images = reply_images

        if not images:
            yield event.plain_result("未找到可用的源图。")
            return

        prompt_text = self._resolve_prompt(event, prompt, reply_text)
        if not prompt_text:
            yield event.plain_result("未提供提示词。")
            return

        session_value = session
        if isinstance(session_value, list):
            session_value = ",".join(str(item) for item in session_value)

        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return

        model_value = model or self.image_defaults["model"]
        model_error = self._validate_image_model(model_value)
        if model_error:
            yield event.plain_result(model_error)
            return
        ratio_value = ratio or self.image_defaults["ratio"]
        resolution_value = resolution or self.image_defaults["resolution"]
        response_format_value = response_format or self.image_defaults["response_format"]
        negative_value = negative_prompt or self.image_defaults["negative"]
        sample_value = self._coerce_float(
            sample_strength, float(self.image_defaults["sample_strength"])
        )
        session_override = (
            self._parse_session_override(session_value) if session_value else None
        )

        media_results, error_message, headline = self._compose_image_with_service(
            service,
            prompt=prompt_text,
            images=images,
            model=model_value,
            ratio=ratio_value,
            resolution=resolution_value,
            response_format=response_format_value,
            negative_prompt=negative_value,
            sample_strength=sample_value,
            session_override=session_override,
        )
        if error_message:
            yield event.plain_result(error_message)
            return
        for item in media_results:
            yield item
        if headline:
            yield event.plain_result(headline)
        return

    @llm_tool(name="jimeng_video")
    async def tool_jimeng_video(
        self,
        event: AstrMessageEvent,
        prompt: str,
        model: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        resolution: str = "",
        response_format: str = "",
        session: str = "",
    ):
        """调用 Jimeng 文生视频接口。

        Args:
            prompt(string): 主要提示词，可留空并引用消息。
            model(string, optional): 模型名称。
            width(number, optional): 视频宽度。
            height(number, optional): 视频高度。
            resolution(string, optional): 分辨率标签。
            response_format(string, optional): 返回格式，支持 url 或 b64_json。
            session(string, optional): 逗号分隔的 session id 覆盖值。
        """
        reply_text, _ = self._extract_reply_context(event)
        prompt_text = self._resolve_prompt(event, prompt, reply_text)
        if not prompt_text:
            yield event.plain_result("未提供提示词。")
            return

        session_value = session
        if isinstance(session_value, list):
            session_value = ",".join(str(item) for item in session_value)

        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return

        model_value = model or self.video_defaults["model"]
        model_error = self._validate_video_model(model_value)
        if model_error:
            yield event.plain_result(model_error)
            return
        width_value = self._coerce_int(width, int(self.video_defaults["width"]))
        height_value = self._coerce_int(height, int(self.video_defaults["height"]))
        resolution_value = resolution or self.video_defaults["resolution"]
        response_format_value = response_format or self.video_defaults["response_format"]
        session_override = (
            self._parse_session_override(session_value) if session_value else None
        )

        media_results, error_message, headline = self._generate_video_with_service(
            service,
            prompt=prompt_text,
            model=model_value,
            width=width_value,
            height=height_value,
            resolution=resolution_value,
            response_format=response_format_value,
            session_override=session_override,
        )
        if error_message:
            yield event.plain_result(error_message)
            return
        for item in media_results:
            yield item
        if headline:
            yield event.plain_result(headline)
        return

    @llm_tool(name="jimeng_points")
    async def tool_jimeng_points(self, event: AstrMessageEvent):
        """查询当前配置 session 的积分信息。"""
        service, error = self._ensure_ready()
        if error:
            yield event.plain_result(error)
            return
        try:
            records = service.get_points()
        except JimengAPIError as exc:
            logger.exception("获取 Jimeng 积分信息失败。")
            message = f"Jimeng 接口错误：{exc}"
            yield event.plain_result(message)
            return
        if not records:
            message = "Jimeng 未返回任何积分记录。"
            yield event.plain_result(message)
            return

        lines = ["Jimeng 积分概览："]
        for item in records:
            token = item.get("token", "<未知>")
            points = item.get("points") or {}
            total = points.get("totalCredit", "未知")
            gift = points.get("giftCredit", "未知")
            purchase = points.get("purchaseCredit", "未知")
            vip = points.get("vipCredit", "未知")
            lines.append(
                f"- {token}: 总积分={total}, 赠送={gift}, 购买={purchase}, VIP={vip}"
            )
        message = "\n".join(lines)
        yield event.plain_result(message)
        return



    def _ensure_service(self) -> JimengAPIService:
        if self.service is None:
            self.service = JimengAPIService(
                session_id=self.session_ids or None,
                auto_start=False,
            )
        else:
            self.service.set_session_ids(self.session_ids or [])
        return self.service

    def _ensure_ready(self) -> Tuple[Optional[JimengAPIService], Optional[str]]:
        if not self.session_ids:
            return None, (
                "尚未配置 session id，请先执行 /jimeng session set <token>。"
            )
        service = self._ensure_service()
        if not service.is_running():
            try:
                service.start()
            except JimengAPIError as exc:
                logger.exception("启动 Jimeng 服务失败。")
                return None, f"启动 Jimeng 服务失败：{exc}"
        return service, None

    def _sync_service_sessions(self, stop_on_empty: bool = False) -> None:
        if self.service:
            self.service.set_session_ids(self.session_ids or [])
            if stop_on_empty and not self.session_ids and self.service.is_running():
                self.service.stop()

    def _load_config(self) -> None:
        cfg = self.config or {}
        self.session_ids = [
            token.strip()
            for token in cfg.get("session_ids", [])
            if isinstance(token, str) and token.strip()
        ]
        self.auto_start = bool(cfg.get("auto_start", False))
        sample_default = self._coerce_float(cfg.get("image_sample_strength"), 0.5)
        image_model = cfg.get("image_model", jimeng_constants.DEFAULT_IMAGE_MODEL)
        if image_model not in self._supported_image_models:
            image_model = jimeng_constants.DEFAULT_IMAGE_MODEL
        video_model = cfg.get("video_model", jimeng_constants.DEFAULT_VIDEO_MODEL)
        if video_model not in self._supported_video_models:
            video_model = jimeng_constants.DEFAULT_VIDEO_MODEL
        self.image_defaults = {
            "model": image_model,
            "ratio": cfg.get("image_ratio", "1:1"),
            "resolution": cfg.get("image_resolution", "1k"),
            "response_format": cfg.get("image_response_format", "url"),
            "negative": cfg.get("image_negative_prompt", ""),
            "sample_strength": sample_default,
        }
        self.video_defaults = {
            "model": video_model,
            "width": self._coerce_int(cfg.get("video_width"), 960),
            "height": self._coerce_int(cfg.get("video_height"), 540),
            "resolution": cfg.get("video_resolution", "720p"),
            "response_format": cfg.get("video_response_format", "url"),
        }

    def _save_config(self) -> None:
        if self.config is None:
            return
        self.config["session_ids"] = self.session_ids
        self.config["auto_start"] = self.auto_start
        self.config["image_model"] = self.image_defaults["model"]
        self.config["image_ratio"] = self.image_defaults["ratio"]
        self.config["image_resolution"] = self.image_defaults["resolution"]
        self.config["image_response_format"] = self.image_defaults["response_format"]
        self.config["image_negative_prompt"] = self.image_defaults["negative"]
        self.config["image_sample_strength"] = self.image_defaults["sample_strength"]
        self.config["video_model"] = self.video_defaults["model"]
        self.config["video_width"] = self.video_defaults["width"]
        self.config["video_height"] = self.video_defaults["height"]
        self.config["video_resolution"] = self.video_defaults["resolution"]
        self.config["video_response_format"] = self.video_defaults["response_format"]
        self.config.save_config()

    def _extract_reply_context(self, event: AstrMessageEvent) -> Tuple[str, List[str]]:
        reply_text = ""
        image_urls: List[str] = []
        for component in event.get_messages():
            if isinstance(component, Reply):
                if component.message_str:
                    reply_text = component.message_str.strip()
                if component.chain:
                    text_parts: List[str] = []
                    for seg in component.chain:
                        if isinstance(seg, Plain) and seg.text:
                            text = seg.text.strip()
                            if text:
                                text_parts.append(text)
                        elif isinstance(seg, Image):
                            url = seg.url or seg.file or ""
                            if url and url not in image_urls:
                                image_urls.append(url)
                    if not reply_text and text_parts:
                        reply_text = " ".join(text_parts)
                break
        for component in event.get_messages():
            if isinstance(component, Image):
                url = component.url or component.file or ""
                if url and url not in image_urls:
                    image_urls.append(url)
        return reply_text.strip(), image_urls

    def _resolve_prompt(
        self,
        event: AstrMessageEvent,
        prompt_text: str,
        fallback_text: Optional[str] = None,
    ) -> str:
        prompt_text = (prompt_text or "").strip()
        if prompt_text:
            return prompt_text
        if fallback_text is None:
            fallback_text, _ = self._extract_reply_context(event)
        return (fallback_text or "").strip()

    @staticmethod
    def _split_tokens(raw: str) -> List[str]:
        if not raw:
            return []
        return [token for token in raw.replace(",", " ").split() if token]

    @staticmethod
    def _coerce_float(value: Union[str, float, int, None], default: float) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_int(value: Union[str, float, int, None], default: int) -> int:
        try:
            if value is None or value == "":
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_prompt_options(raw: str) -> Tuple[str, Dict[str, str]]:
        if not raw:
            return "", {}
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()

        prompt_parts: List[str] = []
        options: Dict[str, str] = {}
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                options[key.lower()] = value
            else:
                prompt_parts.append(part)
        prompt_text = " ".join(prompt_parts).strip()
        return prompt_text, options

    @staticmethod
    def _parse_session_override(raw: Optional[str]) -> Optional[Union[str, List[str]]]:
        if not raw:
            return None
        tokens = [
            token.strip()
            for token in raw.replace(",", " ").split()
            if token.strip()
        ]
        if not tokens:
            return None
        if len(tokens) == 1:
            return tokens[0]
        return tokens

    def _validate_image_model(self, model: str) -> Optional[str]:
        if model not in self._supported_image_models:
            choices = "、".join(self._supported_image_models)
            return f"不支持的图片模型：{model}。可选值：{choices}"
        return None

    def _validate_video_model(self, model: str) -> Optional[str]:
        if model not in self._supported_video_models:
            choices = "、".join(self._supported_video_models)
            return f"不支持的视频模型：{model}。可选值：{choices}"
        return None

    def _generate_image_with_service(
        self,
        service: JimengAPIService,
        *,
        prompt: str,
        model: str,
        ratio: str,
        resolution: str,
        response_format: str,
        negative_prompt: str,
        sample_strength: float,
        session_override: Optional[Union[str, List[str]]],
    ) -> Tuple[List[MessageEventResult], Optional[str], Optional[str]]:
        try:
            result = service.generate_image(
                prompt=prompt,
                model=model,
                ratio=ratio,
                resolution=resolution,
                response_format=response_format,
                negative_prompt=negative_prompt,
                sample_strength=sample_strength,
                session_id=session_override,
            )
        except JimengAPIError as exc:
            logger.exception("调用 Jimeng 生成图片失败。")
            return [], f"Jimeng 接口错误：{exc}", None
        media_results, headline = self._render_generation_output(
            result,
            response_format=response_format,
            headline=f"已生成图片 (model={model}, ratio={ratio}, resolution={resolution})",
        )
        if not media_results:
            return [], headline, None
        return media_results, None, headline

    def _compose_image_with_service(
        self,
        service: JimengAPIService,
        *,
        prompt: str,
        images: Sequence[Union[str, bytes]],
        model: str,
        ratio: str,
        resolution: str,
        response_format: str,
        negative_prompt: str,
        sample_strength: float,
        session_override: Optional[Union[str, List[str]]],
    ) -> Tuple[List[MessageEventResult], Optional[str], Optional[str]]:
        try:
            result = service.image_composition(
                prompt=prompt,
                images=images,
                model=model,
                ratio=ratio,
                resolution=resolution,
                response_format=response_format,
                negative_prompt=negative_prompt,
                sample_strength=sample_strength,
                session_id=session_override,
            )
        except JimengAPIError as exc:
            logger.exception("调用 Jimeng 图生图失败。")
            return [], f"Jimeng 接口错误：{exc}", None
        media_results, headline = self._render_generation_output(
            result,
            response_format=response_format,
            headline=(
                "图生图任务完成 "
                f"(model={model}, ratio={ratio}, resolution={resolution}, 源图数量={len(images)})"
            ),
        )
        if not media_results:
            return [], headline, None
        return media_results, None, headline

    def _generate_video_with_service(
        self,
        service: JimengAPIService,
        *,
        prompt: str,
        model: str,
        width: int,
        height: int,
        resolution: str,
        response_format: str,
        session_override: Optional[Union[str, List[str]]],
    ) -> Tuple[List[MessageEventResult], Optional[str], Optional[str]]:
        try:
            result = service.generate_video(
                prompt=prompt,
                model=model,
                width=width,
                height=height,
                resolution=resolution,
                response_format=response_format,
                session_id=session_override,
            )
        except JimengAPIError as exc:
            logger.exception("调用 Jimeng 生成视频失败。")
            return [], f"Jimeng 接口错误：{exc}", None
        media_results, headline = self._render_generation_output(
            result,
            response_format=response_format,
            headline=(
                "视频生成任务完成 "
                f"(model={model}, size={width}x{height}, resolution={resolution})"
            ),
        )
        if not media_results:
            return [], headline, None
        return media_results, None, headline

    @staticmethod
    def _render_generation_output(
        payload: Dict[str, object],
        *,
        response_format: str,
        headline: str,
    ) -> Tuple[List[MessageEventResult], Optional[str]]:
        data = payload.get("data") or []
        if not isinstance(data, list) or not data:
            return [], "Jimeng 返回结果为空。"

        media_messages: List[MessageEventResult] = []
        if response_format == "b64_json":
            for item in data:
                if isinstance(item, dict) and item.get("b64_json"):
                    media_messages.append(
                        MessageEventResult().base64_image(item["b64_json"])
                    )
        else:
            for item in data:
                if isinstance(item, dict) and item.get("url"):
                    media_messages.append(
                        MessageEventResult().url_image(item["url"])
                    )

        if not media_messages:
            return [], "Jimeng 返回结果中缺少可用数据。"

        return media_messages, headline
