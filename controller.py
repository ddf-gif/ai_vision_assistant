"""
成本控制模块

提供 CostController 类，通过意图过滤和帧率动态调节来降低 API 调用成本。
仅在用户明确需要视觉信息时才高频发送摄像头帧，闲置时降至最低帧率。
"""

import time

from utils import contains_visual_keyword


class CostController:
    """
    视觉帧发送的成本控制器

    核心策略：
    1. 意图过滤：用户说话内容包含视觉关键词时才允许发送画面
    2. 帧率调节：对话活跃时 1fps，闲置 >30 秒降至 0.1fps（每 10 秒 1 帧）

    使用示例:
        ctrl = CostController()
        ctrl.update_idle_timer(is_speaking=True)
        if ctrl.should_send_video("这是什么"):
            frame = camera.get_frame_base64()
            bot.send_video_frame(frame)
    """

    # 帧率配置
    ACTIVE_FPS = 1.0       # 对话活跃时帧率（每秒 1 帧）
    IDLE_FPS = 0.1         # 闲置时帧率（每 10 秒 1 帧）
    IDLE_TIMEOUT = 30.0    # 闲置判定时间（秒）

    def __init__(self):
        """初始化成本控制器"""
        # 帧率
        self.active_fps = self.ACTIVE_FPS
        self.idle_fps = self.IDLE_FPS

        # 闲置计时
        self._last_speech_time = time.time()  # 最后一次用户说话的时间戳
        self._idle = False                     # 当前是否处于闲置状态

        # 意图过滤
        self._last_user_text = ""              # 最近一次用户说话文本

    # ==================== 意图过滤 ====================

    def should_send_video(self, user_text: str = "") -> bool:
        """
        基于用户意图判断是否应发送视频帧。

        - 有用户文本且包含视觉关键词 → 允许发送
        - 闲置超过阈值 → 也允许低频发送（保持背景上下文）
        - 否则 → 不发送

        Args:
            user_text: 最近一次用户语音识别文本（可为空）

        Returns:
            bool: 是否应该发送视频帧
        """
        if user_text:
            self._last_user_text = user_text

        # 用户表达了视觉意图 → 发送
        if contains_visual_keyword(self._last_user_text):
            return True

        # 长时间闲置 → 保持低频背景帧
        if self._idle:
            return True

        # 用户没说要"看"，且不闲置 → 不发送（省 token）
        return False

    # ==================== 帧率控制 ====================

    def get_current_fps(self) -> float:
        """
        根据当前闲置状态返回应使用的帧率。

        Returns:
            float: 当前帧率（fps）
        """
        return self.idle_fps if self._idle else self.active_fps

    def update_idle_timer(self, is_speaking: bool):
        """
        更新闲置计时器。

        用户说话时重置计时；静默时累积计时。
        超过 IDLE_TIMEOUT 秒无对话则进入闲置状态。

        Args:
            is_speaking: 当前是否有语音活动（助手说话不算，用户才算）
        """
        if is_speaking:
            # 有人在说话 → 重置闲置计时，退出闲置
            self._last_speech_time = time.time()
            if self._idle:
                self._idle = False
                print("[系统] 检测到对话活动，恢复活跃帧率 (1fps)")
        else:
            # 无人说话 → 检查是否超时
            if not self._idle and time.time() - self._last_speech_time > self.IDLE_TIMEOUT:
                self._idle = True
                print(f"[系统] 对话闲置超过 {self.IDLE_TIMEOUT:.0f} 秒，进入低帧率模式 (0.1fps)")

    def is_idle(self) -> bool:
        """
        返回当前是否处于闲置状态。

        Returns:
            bool: 闲置返回 True
        """
        return self._idle

    def reset(self):
        """重置所有状态（用于会话重连后）"""
        self._last_speech_time = time.time()
        self._idle = False
        self._last_user_text = ""
