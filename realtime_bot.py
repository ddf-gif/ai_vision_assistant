"""
基于 Qwen3-Omni-Flash-Realtime 的语音对话模块

提供 RealtimeBot 类封装实时语音对话的连接、回调、音频收发等功能。
支持半双工回声抑制、流式文本输出、键盘/语音打断 AI 回复。
依赖 dashscope SDK 的 OmniRealtimeConversation 和 OmniRealtimeCallback。
"""

import base64
import json
import struct
import sys
import time

import dashscope
import pyaudio
from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)

# ---------- 跨平台非阻塞键盘检测 ----------
_mvcrt_available = False
if sys.platform == "win32":
    try:
        import msvcrt

        _mvcrt_available = True
    except ImportError:
        pass


class BotCallback(OmniRealtimeCallback):
    """
    实时对话回调类，处理模型返回的各类事件。

    核心职责：
    - 音频增量数据：写入扬声器播放，同时维护说话状态标志
    - 流式文本：逐块打印助手回复（打字机效果）
    - 用户语音识别结果：打印到控制台
    - 中断状态管理：被打断时停止音频播放
    """

    def __init__(self, pya: pyaudio.PyAudio):
        """
        初始化回调对象

        Args:
            pya: PyAudio 实例，用于创建音频输出流
        """
        super().__init__()
        self.pya = pya
        self.out = None  # 音频输出流，在 on_open 中初始化

        # ---- 状态标志 ----
        self.assistant_speaking = False  # 助手是否正在说话（半双工回声抑制）
        self.is_speaking = False         # 同 assistant_speaking，供打断逻辑使用
        self._interrupted = False        # 当前回复是否被用户打断
        self._cooldown_until = 0.0       # 冷却期结束时间戳（秒）
        self._voice_intr_cooldown = 0.0  # 语音打断冷却期（打断后 2 秒内不触发语音打断）
        self._post_intr_mute_until = 0.0  # 打断后静音期（打断后 500ms 内不发送麦克风数据）
        self._assistant_text = ""        # 流式文本缓冲区

    # ---- 音频能量计算（静态方法，供语音打断使用） ----

    @staticmethod
    def calculate_rms(audio_bytes: bytes) -> float:
        """
        计算 16-bit PCM 音频数据的 RMS（均方根）能量值。

        用于语音打断检测：当助手说话期间，若麦克风能量超过阈值，
        判定用户正在说话，触发打断。

        Args:
            audio_bytes: 原始 PCM 音频字节（16-bit, mono）

        Returns:
            float: RMS 能量值。背景噪音通常 < 200，说话通常 > 500
        """
        if len(audio_bytes) < 2:
            return 0.0
        count = len(audio_bytes) // 2
        try:
            samples = struct.unpack(f"{count}h", audio_bytes)
        except struct.error:
            return 0.0
        return (sum(s * s for s in samples) / count) ** 0.5

    # ---- 回调方法 ----

    def on_open(self):
        """
        WebSocket 连接建立成功时回调。
        初始化音频输出流（扬声器），采样率 24000 Hz，单声道，16 位整型。
        """
        try:
            self.out = self.pya.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=24000,
                output=True,
            )
            print("✅ 音频输出流已就绪")
        except Exception as e:
            print(f"❌ 初始化音频输出流失败: {e}")
            raise

    def on_event(self, response: dict):
        """
        处理从模型收到的各类事件。

        支持的事件类型：
        - session.created / session.updated: 会话生命周期
        - response.audio.delta: 助手音频增量 → 播放 + 标记 is_speaking
        - response.audio_transcript.delta: 助手文本流式增量 → 打字机输出
        - response.audio_transcript.done: 助手文本完成 → 换行
        - response.done: 一轮回复结束 → 清除说话标记 + 冷却期
        - conversation.item.input_audio_transcription.completed: 用户语音识别完成
        - error: 服务端错误

        Args:
            response: 模型返回的事件字典
        """
        event_type = response.get("type", "")

        try:
            # ---- 会话生命周期 ----
            if event_type == "session.created":
                print("✅ 会话已创建")

            elif event_type == "session.updated":
                print("✅ 会话配置已更新")

            # ---- 助手音频 ----
            elif event_type == "response.audio.delta":
                # 若已被打断，不再播放音频
                if self._interrupted:
                    return
                # 标记助手正在说话
                self.assistant_speaking = True
                self.is_speaking = True
                # 播放音频
                if self.out:
                    self.out.write(base64.b64decode(response["delta"]))

            # ---- 助手文本流式输出 ----
            elif event_type == "response.audio_transcript.delta":
                if self._interrupted:
                    return
                delta_text = response.get("delta", "")
                self._assistant_text += delta_text
                print(delta_text, end="", flush=True)

            # ---- 助手文本完成 ----
            elif event_type == "response.audio_transcript.done":
                # 被打断时只换行不重复打印
                if not self._interrupted:
                    print()
                self._assistant_text = ""

            # ---- 用户语音识别 ----
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                print(f"\n[🎤 用户] {transcript}")

            # ---- 一轮回复结束 ----
            elif event_type == "response.done":
                # WebSocket 保证 response.done 是该回复的最后一个事件，
                # 之后不会再有其 audio.delta / transcript.delta，可安全解除封锁
                self._interrupted = False
                if not self._cooldown_until:
                    # 正常结束：进入冷却期防止回声尾巴
                    self._cooldown_until = time.time() + 0.5
                self.assistant_speaking = False
                self.is_speaking = False
                self._assistant_text = ""

            # ---- 服务端语音活动检测 ----
            elif event_type == "input_audio_buffer.speech_started":
                pass

            elif event_type == "input_audio_buffer.speech_stopped":
                pass

            # ---- 错误 ----
            elif event_type == "error":
                error_msg = response.get("error", {}).get("message", str(response))
                # 忽略打断产生的预期错误（cancel 后服务端返回的正常响应）
                if "none active response" in error_msg.lower():
                    pass
                else:
                    print(f"\n⚠️ 模型返回错误: {error_msg}")

        except Exception as e:
            print(f"\n⚠️ 处理事件时出错: {e}，事件类型: {event_type}")


class RealtimeBot:
    """
    实时语音对话机器人

    封装了与 Qwen3-Omni-Flash-Realtime 模型的实时语音交互流程：
    1. 建立 WebSocket 连接
    2. 配置会话参数（音色、指令等）
    3. 从麦克风采集音频并发送
    4. 接收并播放模型返回的语音（流式文本 + 音频）
    5. 支持 ESC 键 / 语音能量 打断 AI 回复
    6. 资源清理与自动重连

    使用示例:
        bot = RealtimeBot(api_key="sk-xxx", voice="Ethan")
        bot.connect()
        bot.start_audio_input()
        bot.run()
    """

    # 默认 WebSocket 地址
    DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    # 语音打断默认能量阈值（RMS），可根据麦克风灵敏度调整
    DEFAULT_ENERGY_THRESHOLD = 600

    def __init__(
        self,
        api_key: str,
        voice: str = "Ethan",
        model: str = "qwen3-omni-flash-realtime",
        instructions: str = "你是个人助理小云，请用幽默风趣的方式回答用户的问题",
        url: str = None,
        energy_threshold: float = None,
        enable_keyboard_interrupt: bool = True,
        enable_voice_interrupt: bool = True,
    ):
        """
        初始化语音对话机器人

        Args:
            api_key: DashScope API Key
            voice: 音色名称，可选值见 DashScope 文档
            model: 模型名称
            instructions: 系统指令，用于设定助手的角色和风格
            url: WebSocket 地址，默认使用阿里云 DashScope
            energy_threshold: 语音打断能量阈值（RMS），默认 600
            enable_keyboard_interrupt: 是否启用 ESC 键打断
            enable_voice_interrupt: 是否启用语音能量打断
        """
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.instructions = instructions
        self.url = url or self.DEFAULT_URL
        self.energy_threshold = energy_threshold or self.DEFAULT_ENERGY_THRESHOLD
        self.enable_keyboard_interrupt = enable_keyboard_interrupt
        self.enable_voice_interrupt = enable_voice_interrupt

        # 音频设备
        self.pya = None
        self.mic = None

        # 回调与会话
        self.callback = None
        self.conv = None

        # 运行状态
        self._is_running = False

    # ==================== 连接管理 ====================

    def connect(self):
        """
        建立与 DashScope 实时语音服务的 WebSocket 连接，并配置会话。

        Steps:
        1. 设置 API Key
        2. 初始化 PyAudio 音频设备
        3. 创建 BotCallback 回调实例
        4. 创建 OmniRealtimeConversation 并建立连接
        5. 发送 session.update 配置音色和系统指令

        Raises:
            Exception: 连接失败或会话配置失败时抛出
        """
        try:
            print("🔗 正在连接 DashScope 实时语音服务...")

            # 设置 API Key（OmniRealtimeConversation 从模块级变量读取）
            dashscope.api_key = self.api_key

            # 初始化 PyAudio（仅首次）
            if self.pya is None:
                self.pya = pyaudio.PyAudio()

            # 创建回调（每次连接用新回调，确保状态干净）
            self.callback = BotCallback(self.pya)

            # 创建 OmniRealtimeConversation 并连接
            self.conv = OmniRealtimeConversation(
                model=self.model,
                callback=self.callback,
                url=self.url,
            )
            self.conv.connect()

            # 配置会话：输出模式（音频+文本）、音色、系统指令
            self.conv.update_session(
                output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
                voice=self.voice,
                instructions=self.instructions,
            )

            print("✅ 连接成功，会话已配置")
            print(f"   模型: {self.model}")
            print(f"   音色: {self.voice}")
            print(f"   打断方式: {'ESC键' if self.enable_keyboard_interrupt else ''}"
                  f"{' + ' if self.enable_keyboard_interrupt and self.enable_voice_interrupt else ''}"
                  f"{'语音能量' if self.enable_voice_interrupt else ''}"
                  f"{' (未启用)' if not self.enable_keyboard_interrupt and not self.enable_voice_interrupt else ''}")
            if self.enable_voice_interrupt:
                print(f"   语音打断阈值: {self.energy_threshold:.0f} RMS")

        except Exception as e:
            print(f"❌ 连接失败: {e}")
            raise

    def _reconnect(self):
        """
        断线后自动重连，恢复 WebSocket 和会话配置。
        对用户透明，仅在控制台打印重连状态。
        """
        print("    🔄 正在重连...")
        # 先关闭旧连接
        if self.conv:
            try:
                self.conv.close()
            except Exception:
                pass
            self.conv = None

        # 短暂等待确保旧连接完全释放
        time.sleep(0.3)

        # 重新建立连接（connect 会创建新的 callback 和 conv）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.connect()
                print("    ✅ 重连成功")
                # 重新打开麦克风（旧流可能已关闭）
                if self.mic is None or not self.mic.is_active():
                    self.start_audio_input()
                return True
            except Exception as e:
                print(f"    ⚠️ 重连尝试 {attempt + 1}/{max_retries} 失败: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1.0)

        print("    ❌ 重连失败，请手动重启")
        return False

    # ==================== 打断逻辑 ====================

    def _cancel_response(self):
        """
        取消当前 AI 回复。

        策略（按优先级）：
        1. SDK 方法: cancel_response / cancel / interrupt
        2. 通用 send_event 方法
        3. 直接 WebSocket 发送 response.cancel
        4. 以上均不可用 → 返回 False，由调用方通过重连实现

        Returns:
            bool: 是否成功取消
        """
        if self.conv is None:
            return False

        # 方式 1：SDK 方法
        for method_name in ("cancel_response", "cancel", "interrupt"):
            try:
                method = getattr(self.conv, method_name, None)
                if callable(method):
                    method()
                    return True
            except Exception:
                continue

        # 方式 2：通用 send_event / send 方法
        for method_name in ("send_event", "send"):
            try:
                method = getattr(self.conv, method_name, None)
                if callable(method):
                    method({"type": "response.cancel"})
                    return True
            except Exception:
                continue

        # 方式 3：直接操作 WebSocket
        try:
            ws = getattr(self.conv, "_ws", None) or getattr(self.conv, "ws", None)
            if ws and hasattr(ws, "send"):
                ws.send(json.dumps({"type": "response.cancel"}))
                return True
        except Exception:
            pass

        # 方式 4：不可用
        return False

    def _do_interrupt(self):
        """
        执行打断操作：

        1. 标记回调为已打断（阻止继续播放音频和打印文本）
        2. 关闭并重建输出流（清空扬声器缓冲，立刻停止播放）
        3. 发送取消事件给服务端
        4. 若取消失败，自动重连
        5. 恢复对话状态
        """
        if not self.callback:
            return

        print("\n[系统] 用户打断回复")

        # 1. 标记打断状态（_interrupted 保持 True，封锁所有后续旧事件）
        self.callback._interrupted = True
        self.callback.assistant_speaking = False
        self.callback.is_speaking = False
        self.callback._cooldown_until = 0.0
        self.callback._voice_intr_cooldown = time.time() + 2.0
        self.callback._post_intr_mute_until = time.time() + 0.5  # 打断后静音 500ms，防尾音回声

        # 2. 关闭并重建输出流 —— 立刻清空扬声器缓冲，停止播放
        if self.callback.out:
            try:
                self.callback.out.stop_stream()
                self.callback.out.close()
            except Exception:
                pass
            try:
                self.callback.out = self.pya.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=24000,
                    output=True,
                )
            except Exception as e:
                print(f"    ⚠️ 重建输出流失败: {e}")
                self.callback.out = None

        # 3. 尝试取消服务端回复
        cancelled = self._cancel_response()
        if not cancelled:
            print("    取消请求未生效，正在重连...")
            old_callback = self.callback
            if old_callback.out:
                try:
                    old_callback.out.close()
                    old_callback.out = None
                except Exception:
                    pass
            mute_deadline = old_callback._post_intr_mute_until  # 保存静音截止时间
            if self._reconnect():
                # 重连后新 callback 继承静音期
                self.callback._post_intr_mute_until = mute_deadline
            return

        time.sleep(0.1)

    # ==================== 键盘检测 ====================

    @staticmethod
    def _is_esc_pressed() -> bool:
        """
        非阻塞检测 ESC 键是否被按下。

        Windows: 使用内置 msvcrt 模块
        其他平台: 返回 False（需安装 keyboard 库作为替代）

        Returns:
            bool: ESC 键是否被按下
        """
        if _mvcrt_available:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                return ch == b"\x1b"  # ESC 的 ASCII 码
        return False

    # ==================== 麦克风管理 ====================

    def start_audio_input(self):
        """
        打开麦克风，准备采集音频。

        采样率 16000 Hz，单声道，16 位整型。
        每次读取 3200 帧（约 200ms 的音频）。
        """
        if not self.pya:
            raise RuntimeError("请先调用 connect() 建立连接")

        # 如果已有麦克风流且活跃，先关闭
        if self.mic and self.mic.is_active():
            self.mic.stop_stream()
            self.mic.close()
            self.mic = None

        try:
            self.mic = self.pya.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=3200,
            )
            print("🎙️ 麦克风已就绪")
        except Exception as e:
            print(f"❌ 无法打开麦克风: {e}")
            raise

    # ==================== 主循环 ====================

    def run(self):
        """
        主循环：持续从麦克风读取音频数据并发送给模型。

        循环状态机：
        ┌─────────────────────────────────────────────┐
        │  读取麦克风数据 → 计算能量                     │
        │    ├─ AI 正在说话?                            │
        │    │   ├─ 检测 ESC 键 / 语音能量超过阈值        │
        │    │   │   → 触发打断，取消回复，回到聆听状态     │
        │    │   └─ 无打断 → 丢弃音频（半双工）           │
        │    ├─ 冷却期中? → 丢弃音频                     │
        │    └─ 正常状态 → 发送音频给模型                 │
        └─────────────────────────────────────────────┘

        按 Ctrl+C 退出循环并自动清理资源。
        """
        if not self.mic:
            raise RuntimeError("请先调用 start_audio_input() 打开麦克风")

        self._is_running = True
        print("=" * 50)
        print("🎧 对话已开始！对着麦克风说话吧")
        if self.enable_keyboard_interrupt:
            print("   [ESC] 打断 AI 回复")
        if self.enable_voice_interrupt:
            print("   [说话] 打断 AI 回复")
        print("   [Ctrl+C] 退出对话")
        print("=" * 50)

        # 能量历史（用于语音打断的连续帧检测，避免瞬态噪声误触发）
        _energy_history = []  # 最近 3 帧的能量值

        try:
            while self._is_running:
                try:
                    # ---- 步骤 1：从麦克风读取音频 ----
                    audio_data = self.mic.read(3200, exception_on_overflow=False)

                    # ---- 步骤 2：计算当前音频能量 ----
                    energy = BotCallback.calculate_rms(audio_data)

                    # ---- 步骤 3：状态判断 ----
                    if self.callback and self.callback.assistant_speaking:
                        # ========== AI 正在说话 ==========

                        # 3a. 键盘打断检测
                        if self.enable_keyboard_interrupt and self._is_esc_pressed():
                            self._do_interrupt()
                            time.sleep(0.01)
                            continue

                        # 3b. 语音打断检测（需连续 2 帧高能量 + 不在冷却期，避免回声死循环）
                        if self.enable_voice_interrupt:
                            # 冷却期内跳过语音打断（打断后 2 秒内扬声器回声会误触发）
                            if time.time() < self.callback._voice_intr_cooldown:
                                _energy_history.clear()
                            else:
                                _energy_history.append(energy)
                                if len(_energy_history) > 3:
                                    _energy_history.pop(0)
                                # 连续 2 帧超过阈值 → 用户正在说话
                                high_energy_count = sum(
                                    1 for e in _energy_history if e > self.energy_threshold
                                )
                                if high_energy_count >= 2:
                                    _energy_history.clear()
                                    self._do_interrupt()
                                    time.sleep(0.01)
                                    continue
                        else:
                            _energy_history.clear()

                        # 无打断：丢弃音频（半双工），短暂休眠
                        time.sleep(0.01)
                        continue

                    # ========== 冷却期中 ==========
                    if self.callback and time.time() < self.callback._cooldown_until:
                        time.sleep(0.01)
                        continue

                    # ========== 正常聆听状态：发送音频 ==========
                    # 打断后静音期：丢弃音频，防止扬声器尾音回声进入麦克风
                    if self.callback and time.time() < self.callback._post_intr_mute_until:
                        time.sleep(0.01)
                        continue
                    # 静音期结束后首次发送 → 提示恢复
                    if self.callback and self.callback._post_intr_mute_until > 0:
                        self.callback._post_intr_mute_until = 0.0
                        print("[系统] 已恢复聆听，请说话")
                    self.conv.append_audio(base64.b64encode(audio_data).decode())
                    time.sleep(0.01)

                except OSError as e:
                    print(f"\n⚠️ 音频读取异常: {e}，尝试继续...")
                    time.sleep(0.1)
                    continue
                except Exception as e:
                    print(f"\n⚠️ 发送音频时出错: {e}，尝试继续...")
                    time.sleep(0.1)
                    continue

        except KeyboardInterrupt:
            print("\n\n🛑 收到退出信号...")
        finally:
            self.close()

    # ==================== 资源清理 ====================

    def close(self):
        """
        清理所有资源：关闭麦克风、扬声器输出流、WebSocket 连接、PyAudio。

        每个资源独立 try/except，确保一个失败不影响其他资源的释放。
        """
        self._is_running = False

        # 关闭 WebSocket 连接
        if self.conv:
            try:
                self.conv.close()
                print("🔌 WebSocket 连接已关闭")
            except Exception as e:
                print(f"⚠️ 关闭 WebSocket 连接时出错: {e}")

        # 关闭麦克风
        if self.mic:
            try:
                self.mic.stop_stream()
                self.mic.close()
                print("🎙️ 麦克风已关闭")
            except Exception as e:
                print(f"⚠️ 关闭麦克风时出错: {e}")

        # 关闭扬声器输出流
        if self.callback and self.callback.out:
            try:
                self.callback.out.stop_stream()
                self.callback.out.close()
                print("🔊 扬声器输出流已关闭")
            except Exception as e:
                print(f"⚠️ 关闭扬声器输出流时出错: {e}")

        # 终止 PyAudio
        if self.pya:
            try:
                self.pya.terminate()
                print("🎵 PyAudio 已终止")
            except Exception as e:
                print(f"⚠️ 终止 PyAudio 时出错: {e}")

        print("👋 对话结束，所有资源已释放")
