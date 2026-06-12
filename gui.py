"""
Tkinter 图形界面模块

将 ai_vision_assistant 从控制台+OpenCV 独立窗口升级为单一 Tkinter 图形窗口。
保留 realtime_bot.py / camera.py / controller.py / utils.py / modes.py 不变，
通过新建 gui.py 实现 GUI 封装。

布局：左侧 60% 摄像头预览 + 右侧 40% 对话历史 + 底部状态栏 + 按钮区。
后台线程：音频收发线程 + 摄像头帧读取线程，通过 queue 与主线程通信。

依赖：tkinter（Python 自带，Linux 需手动安装 python3-tk）
"""

import base64
import queue
import threading
import time
import sys

# ---------- Tkinter 导入 ----------
try:
    import tkinter as tk
    from tkinter import scrolledtext
    from tkinter import ttk
except ImportError:
    print("❌ 未找到 tkinter 模块。")
    print("   Windows/macOS: Python 安装时已自带，请检查 Python 安装。")
    print("   Linux: 运行 sudo apt-get install python3-tk")
    sys.exit(1)

import cv2
from PIL import Image, ImageTk

from utils import detect_speech_energy


# ==================== GUI 应用主类 ====================

class AIAssistantGUI:
    """
    AI 视觉助手 Tkinter 图形界面

    整合摄像头预览、对话历史、状态栏、模式切换、打断控制于单一窗口。
    后台线程处理音频收发和摄像头帧采集，通过 Queue 与主线程通信。
    """

    def __init__(self, bot, camera, controller):
        """
        初始化 GUI

        Args:
            bot: RealtimeBot 实例（已完成 connect + start_audio_input）
            camera: Camera 实例（可选，None 时降级纯语音模式）
            controller: CostController 实例
        """
        self.bot = bot
        self.camera = camera
        self.controller = controller

        # 消息队列（线程安全）
        self.msg_queue = queue.Queue()

        # 模式状态
        self.mode = "manual"         # "manual" | "auto"
        self.push_to_talk = False    # 手动模式下是否按住说话
        self.ai_speaking = False     # AI 是否正在说话

        # 线程控制
        self.running = True
        self.audio_thread = None
        self.camera_thread = None

        # ---- 补丁：拦截 BotCallback 事件，路由到 GUI 队列 ----
        self._patch_callback()

        # ---- 构建 Tkinter 窗口 ----
        self.root = tk.Tk()
        self.root.title("AI视觉对话助手 v1.0")
        self.root.geometry("1024x768")
        self.root.minsize(800, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._show_welcome()

        # ---- 启动后台线程 ----
        self._start_audio_thread()
        self._start_camera_thread()

        # ---- 启动队列轮询 ----
        self.root.after(50, self._poll_queue)

    # ==================== 回调补丁 ====================

    def _patch_callback(self):
        """
        猴子补丁：替换 bot.callback.on_event，在不修改 realtime_bot.py 的前提下
        将用户文本、AI 文本、说话状态等事件路由到 GUI 队列。
        原有的音频播放逻辑通过调用原方法保留。
        """
        if not self.bot.callback:
            return

        original_on_event = self.bot.callback.on_event
        gui_queue = self.msg_queue

        def patched_on_event(response):
            event_type = response.get("type", "")

            # 拦截关键事件 → 发送到 GUI 队列
            if event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                gui_queue.put(("user_text", transcript))
            elif event_type == "response.audio_transcript.delta":
                gui_queue.put(("ai_delta", response.get("delta", "")))
            elif event_type == "response.audio_transcript.done":
                gui_queue.put(("ai_done", ""))
            elif event_type == "response.audio.delta":
                gui_queue.put(("ai_speaking", True))
            elif event_type == "response.done":
                gui_queue.put(("ai_speaking", False))

            # 调用原始方法（处理音频播放、打印等原有逻辑）
            try:
                original_on_event(response)
            except Exception:
                pass

        self.bot.callback.on_event = patched_on_event

    # ==================== UI 构建 ====================

    def _build_ui(self):
        """构建 Tkinter 界面：左右分栏 + 底部状态栏 + 按钮区"""
        # ---- 主容器 ----
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- 左侧 60%：摄像头预览 ----
        left_frame = tk.Frame(main_frame, bg="black")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 2), pady=5)

        self.video_label = tk.Label(left_frame, text="摄像头加载中...",
                                    bg="black", fg="white", font=("微软雅黑", 12))
        self.video_label.pack(fill=tk.BOTH, expand=True)

        # ---- 右侧 40%：对话历史 ----
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(2, 5), pady=5)

        self.chat_text = tk.Text(right_frame, wrap=tk.WORD, state=tk.DISABLED,
                                 font=("微软雅黑", 10), padx=8, pady=8)
        scrollbar = tk.Scrollbar(right_frame, command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=scrollbar.set)

        self.chat_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 文字标签颜色配置
        self.chat_text.tag_configure("user", foreground="#1565C0", font=("微软雅黑", 10, "bold"))
        self.chat_text.tag_configure("ai", foreground="#2E7D32", font=("微软雅黑", 10))
        self.chat_text.tag_configure("system", foreground="#9E9E9E", font=("微软雅黑", 9))
        self.chat_text.tag_configure("welcome", foreground="#FF6F00", font=("微软雅黑", 11, "bold"))

        # ---- 底部状态栏 ----
        self.status_bar = tk.Label(self.root, text="准备就绪", bd=1,
                                   relief=tk.SUNKEN, anchor=tk.W, padx=8,
                                   font=("微软雅黑", 9))
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # ---- 按钮区 ----
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 2))

        # "按住说话"按钮
        self.talk_btn = tk.Button(btn_frame, text="按住说话", width=14,
                                  font=("微软雅黑", 10),
                                  bg="#4CAF50", fg="white")
        self.talk_btn.pack(side=tk.LEFT, padx=3)
        self.talk_btn.bind("<ButtonPress-1>", self._on_talk_press)
        self.talk_btn.bind("<ButtonRelease-1>", self._on_talk_release)

        # "切换自动模式"按钮
        self.mode_btn = tk.Button(btn_frame, text="切换自动模式", width=14,
                                  font=("微软雅黑", 10),
                                  command=self._on_toggle_mode)
        self.mode_btn.pack(side=tk.LEFT, padx=3)

        # "打断"按钮
        self.interrupt_btn = tk.Button(btn_frame, text="打断", width=8,
                                       font=("微软雅黑", 10),
                                       bg="#FF5722", fg="white",
                                       state=tk.DISABLED,
                                       command=self._on_interrupt)
        self.interrupt_btn.pack(side=tk.LEFT, padx=3)

        # "退出"按钮
        self.exit_btn = tk.Button(btn_frame, text="退出", width=8,
                                  font=("微软雅黑", 10),
                                  command=self._on_close)
        self.exit_btn.pack(side=tk.RIGHT, padx=3)

        self._update_status_bar()

    # ==================== 欢迎横幅 ====================

    def _show_welcome(self):
        """在对话历史中插入欢迎横幅"""
        welcome_lines = [
            "=" * 40,
            "   AI视觉对话助手 v1.0",
            "   基于 Qwen3-Omni-Flash-Realtime",
            "=" * 40,
        ]
        self._append_chat("\n".join(welcome_lines), "welcome")
        self._append_chat("", "system")

        if not self.camera:
            self._append_chat("[系统] 摄像头不可用，降级纯语音模式", "system")
            self.video_label.config(text="摄像头不可用\n降级纯语音模式")

    # ==================== 对话历史 ====================

    def _append_chat(self, text, tag):
        """追加文本到对话历史，自动滚动到底部"""
        self.chat_text.configure(state=tk.NORMAL)
        if self.chat_text.get("1.0", tk.END).strip():
            self.chat_text.insert(tk.END, "\n")
        self.chat_text.insert(tk.END, text, tag)
        self.chat_text.configure(state=tk.DISABLED)
        self.chat_text.see(tk.END)

    # ==================== 按钮事件 ====================

    def _on_talk_press(self, event):
        """按住说话按钮按下"""
        if self.mode == "manual":
            self.push_to_talk = True
            self.talk_btn.config(text="松开结束", bg="#F44336")
            self.controller.update_idle_timer(is_speaking=True)

    def _on_talk_release(self, event):
        """按住说话按钮松开"""
        if self.mode == "manual":
            self.push_to_talk = False
            self.talk_btn.config(text="按住说话", bg="#4CAF50")
            self.controller.update_idle_timer(is_speaking=False)

    def _on_toggle_mode(self):
        """切换手动/自动模式"""
        if self.mode == "manual":
            self.mode = "auto"
            self.mode_btn.config(text="切换手动模式")
            self.talk_btn.pack_forget()  # 隐藏按住说话按钮
            self._append_chat("[系统] 已切换到自动模式（VAD 语音检测）", "system")
        else:
            self.mode = "manual"
            self.mode_btn.config(text="切换自动模式")
            # 恢复按住说话按钮（插到 mode_btn 前面）
            self.talk_btn.pack(side=tk.LEFT, padx=3, before=self.mode_btn)
            self.push_to_talk = False
            self.talk_btn.config(text="按住说话", bg="#4CAF50")
            self._append_chat("[系统] 已切换到手动模式（按住说话）", "system")
        self._update_status_bar()

    def _on_interrupt(self):
        """打断 AI 回复"""
        if self.ai_speaking:
            self.bot._do_interrupt()
            self._append_chat("[系统] 用户打断回复", "system")

    def _on_close(self):
        """关闭窗口，释放所有资源"""
        self.running = False
        # 等待后台线程退出
        if self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=1.0)
        if self.camera_thread and self.camera_thread.is_alive():
            self.camera_thread.join(timeout=1.0)
        # 释放 bot 和 camera
        try:
            self.bot.close()
        except Exception:
            pass
        if self.camera:
            try:
                self.camera.release()
            except Exception:
                pass
        self.root.destroy()

    # ==================== 后台线程 ====================

    def _start_audio_thread(self):
        """启动音频收发后台线程"""
        self.audio_thread = threading.Thread(
            target=self._audio_loop, daemon=True, name="audio-thread"
        )
        self.audio_thread.start()

    def _audio_loop(self):
        """
        音频处理主循环（后台线程）

        手动模式：push_to_talk 为 True 时采集+发送
        自动模式：VAD 判断说话 → 采集+发送
        AI 说话时暂停采集（半双工回声抑制）
        """
        last_video_send = 0.0
        _audio_ever_sent = False

        if not self.bot.mic or not self.bot.mic.is_active():
            try:
                self.bot.start_audio_input()
            except Exception:
                self.msg_queue.put(("error", "麦克风初始化失败"))
                return

        self.msg_queue.put(("status_update",))

        while self.running:
            try:
                audio_data = self.bot.mic.read(3200, exception_on_overflow=False)
            except Exception:
                time.sleep(0.01)
                continue

            # ---- 判断是否应发送音频 ----
            should_send = False
            if self.mode == "manual":
                should_send = self.push_to_talk
            else:
                # 自动模式：VAD 检测
                should_send = detect_speech_energy(audio_data, 300)

            if should_send and self.bot.conv and self.bot.callback:
                if not self.bot.callback.assistant_speaking:
                    # AI 没在说话 → 发送音频
                    self.bot.conv.append_audio(base64.b64encode(audio_data).decode())
                    _audio_ever_sent = True

                    # 视频帧发送（CostController 驱动）
                    if self.camera and _audio_ever_sent:
                        now = time.time()
                        fps = self.controller.get_current_fps()
                        if now - last_video_send >= 1.0 / fps:
                            user_text = ""
                            if self.bot.callback:
                                user_text = getattr(self.bot.callback, "latest_user_text", "") or ""
                            self.bot.callback.latest_user_text = ""
                            if self.controller.should_send_video(user_text):
                                b64 = self.camera.get_frame_base64()
                                if b64:
                                    try:
                                        self.bot.send_video_frame(b64)
                                        last_video_send = now
                                    except Exception:
                                        pass

            # 更新闲置计时器
            if should_send:
                self.controller.update_idle_timer(is_speaking=True)
            else:
                self.controller.update_idle_timer(is_speaking=False)

            time.sleep(0.01)

    def _start_camera_thread(self):
        """启动摄像头帧采集后台线程"""
        if not self.camera:
            self.msg_queue.put(("camera_unavailable",))
            return
        self.camera_thread = threading.Thread(
            target=self._camera_loop, daemon=True, name="camera-thread"
        )
        self.camera_thread.start()

    def _camera_loop(self):
        """
        摄像头帧采集循环（后台线程）

        按 CostController 当前帧率定时抓帧，转为 ImageTk 对象放入队列供主线程渲染。
        """
        fps = self.controller.get_current_fps()
        interval = 1.0 / fps if fps > 0 else 1.0

        while self.running:
            if not self.camera or not self.camera.cap or not self.camera.cap.isOpened():
                self.msg_queue.put(("camera_unavailable",))
                time.sleep(1.0)
                continue

            frame = self.camera.get_frame()
            if frame is not None:
                # BGR → RGB → 缩放到面板宽度 → ImageTk
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # 缩放适配面板（保持宽高比，目标宽度约 600px）
                h, w = frame_rgb.shape[:2]
                target_w = 600
                scale = target_w / w
                new_w, new_h = int(w * scale), int(h * scale)
                frame_resized = cv2.resize(frame_rgb, (new_w, new_h))
                img = Image.fromarray(frame_resized)
                img_tk = ImageTk.PhotoImage(img)
                self.msg_queue.put(("video_frame", img_tk))

            # 更新帧率（可能因闲置状态变化）
            fps = self.controller.get_current_fps()
            interval = 1.0 / fps if fps > 0 else 1.0
            time.sleep(interval)

    # ==================== 队列轮询 ====================

    def _poll_queue(self):
        """
        定时从队列中拉取消息并更新 GUI（主线程调用）

        支持的消息类型：
        - user_text: 用户语音识别文本
        - ai_delta: AI 流式文本增量
        - ai_done: AI 文本完成（换行）
        - ai_speaking: AI 说话状态切换
        - video_frame: 摄像头帧（ImageTk 对象）
        - camera_unavailable: 摄像头不可用
        - status_update: 刷新状态栏
        - error: 错误消息
        """
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "user_text":
                    self._append_chat(f"[用户] {msg[1]}", "user")
                    self._update_status_bar()

                elif msg_type == "ai_delta":
                    # 流式追加 AI 文本
                    self.chat_text.configure(state=tk.NORMAL)
                    self.chat_text.insert(tk.END, msg[1], "ai")
                    self.chat_text.configure(state=tk.DISABLED)
                    self.chat_text.see(tk.END)

                elif msg_type == "ai_done":
                    # AI 说完，换行
                    self.chat_text.configure(state=tk.NORMAL)
                    self.chat_text.insert(tk.END, "\n")
                    self.chat_text.configure(state=tk.DISABLED)
                    self.chat_text.see(tk.END)
                    self._update_status_bar()

                elif msg_type == "ai_speaking":
                    self.ai_speaking = msg[1]
                    self._update_status_bar()

                elif msg_type == "video_frame":
                    # 更新摄像头预览
                    img_tk = msg[1]
                    self.video_label.config(image=img_tk, text="")
                    self.video_label.image = img_tk  # 保持引用防止 GC

                elif msg_type == "camera_unavailable":
                    self.video_label.config(
                        text="摄像头不可用\n降级纯语音模式",
                        bg="black", fg="gray"
                    )

                elif msg_type == "error":
                    self._append_chat(f"[系统] 错误: {msg[1]}", "system")

                elif msg_type == "status_update":
                    self._update_status_bar()

        except queue.Empty:
            pass

        # 持续轮询
        self.root.after(50, self._poll_queue)

    # ==================== 状态栏 ====================

    def _update_status_bar(self):
        """更新底部状态栏"""
        mic_status = "聆听中" if self.running else "关闭"
        cam_status = "活动" if (self.camera and self.camera.cap and self.camera.cap.isOpened()) else "不可用"
        ai_status = "说话中" if self.ai_speaking else "空闲"
        fps = self.controller.get_current_fps()
        mode_text = "手动" if self.mode == "manual" else "自动"

        status_text = (
            f"模式: {mode_text}  |  麦克风: {mic_status}  |  "
            f"摄像头: {cam_status}  |  AI: {ai_status}  |  帧率: {fps}fps"
        )
        self.status_bar.config(text=status_text)

        # 更新打断按钮状态
        if self.ai_speaking:
            self.interrupt_btn.config(state=tk.NORMAL, bg="#FF5722")
        else:
            self.interrupt_btn.config(state=tk.DISABLED, bg="#BDBDBD")

    # ==================== 启动 ====================

    def run(self):
        """启动 Tkinter 主循环"""
        self.root.mainloop()
