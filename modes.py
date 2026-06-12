"""
对话交互模式模块

提供两种交互模式：
- run_manual_mode: 手动模式（按住空格键说话，松开停止）
- run_auto_mode:   自动模式（VAD 语音检测，自动判断说话/静音）

两种模式均通过 CostController 控制视频帧发送策略。
"""

import base64
import time
import sys

import cv2

from utils import detect_speech_energy


# ==================== 手动模式 ====================

def run_manual_mode(bot, camera, controller):
    """
    手动模式：按住空格键说话，松开停止。

    按住空格期间持续采集麦克风音频发送给模型，并根据 CostController
    策略发送摄像头画面。松开空格后停止发送，但仍读取麦克风清空缓冲。

    ESC 键退出。

    Args:
        bot: RealtimeBot 实例（已完成 connect + start_audio_input）
        camera: Camera 实例（可选）
        controller: CostController 实例
    """
    print("=" * 50)
    print("🎧 手动模式：按 [空格键] 切换录音（开/关）")
    print("   [ESC] 退出对话")
    print("=" * 50)

    push_to_talk = False
    last_video_send = 0.0
    _audio_ever_sent = False  # API 要求音频先于视频
    _running = True            # 本地运行标志
    bot._is_running = True     # 同步 bot 状态
    _space_was_pressed = False # 上一帧空格是否按下（边沿检测）

    # 确保麦克风已打开
    if not bot.mic or not bot.mic.is_active():
        bot.start_audio_input()

    while _running:
        # ---- 显示摄像头预览 ----
        if camera:
            frame = camera.get_frame()
            if frame is not None:
                cv2.imshow("摄像头预览", frame)

        # ---- 键盘检测 ----
        key = cv2.waitKey(1) & 0xFF

        if key == 27:  # ESC 退出
            print("\n[系统] ESC 按下，退出对话")
            break

        # ---- 空格键边沿检测（按下瞬间切换）----
        space_pressed = (key == 32)
        if space_pressed and not _space_was_pressed:
            # 空格键按下边沿：切换录音状态
            push_to_talk = not push_to_talk
            if push_to_talk:
                controller.update_idle_timer(is_speaking=True)
                print("[🎙️] 开始录音")
            else:
                controller.update_idle_timer(is_speaking=False)
                print("[🔇] 停止录音")
        _space_was_pressed = space_pressed

        # ---- 音频处理 ----
        try:
            audio_data = bot.mic.read(3200, exception_on_overflow=False)
        except Exception:
            time.sleep(0.01)
            continue

        if push_to_talk and bot.conv and bot.callback and not bot.callback.assistant_speaking:
            # 按住空格 + AI 没在说话 → 发送音频
            bot.conv.append_audio(base64.b64encode(audio_data).decode())
            _audio_ever_sent = True

            # ---- 视频发送（CostController 驱动） ----
            if camera and _audio_ever_sent:
                now = time.time()
                fps = controller.get_current_fps()
                if now - last_video_send >= 1.0 / fps:
                    user_text = bot.callback.latest_user_text or ""
                    bot.callback.latest_user_text = ""
                    if controller.should_send_video(user_text):
                        b64 = camera.get_frame_base64()
                        if b64:
                            bot.send_video_frame(b64)
                            last_video_send = now
                            status = "闲置保活" if controller.is_idle() else "意图触发"
                            print(f"[📷 视觉] 发送画面 ({status}, {fps}fps)")
        else:
            # 没按空格或 AI 在说话 → 丢弃音频（清空缓冲 + 半双工）
            pass

        time.sleep(0.01)


# ==================== 自动模式 ====================

# VAD 能量阈值（说话/静音分界线，RMS）
_SPEECH_THRESHOLD = 500
# 待机超时（秒）：超过此时间无语音则关闭摄像头
_STANDBY_TIMEOUT = 30.0
# 待机唤醒缓冲区：检测到语音后需连续多少帧才唤醒（防误触发）
_WAKEUP_FRAMES = 3


def run_auto_mode(bot, camera, controller):
    """
    自动模式：基于 VAD 语音检测自动判断说话/静音。

    - 检测到语音 → 发送音频 + 视频（按 CostController 策略）
    - 静音 → 更新闲置计时器
    - 超过 30 秒无语音 → 进入待机（关闭摄像头 + 停止预览）
    - 待机后再次检测到语音 → 自动恢复摄像头和预览

    ESC 键退出。

    Args:
        bot: RealtimeBot 实例（已完成 connect + start_audio_input）
        camera: Camera 实例（可选）
        controller: CostController 实例
    """
    print("=" * 50)
    print("🎧 自动模式：VAD 语音检测，自动判断说话/静音")
    print(f"   待机超时: {_STANDBY_TIMEOUT:.0f} 秒无语音后关闭摄像头")
    print("   [ESC] 退出对话")
    print("=" * 50)

    last_video_send = 0.0
    _audio_ever_sent = False
    _silence_start = time.time()      # 静音开始时间
    _in_standby = False               # 是否处于待机状态
    _wakeup_counter = 0               # 唤醒连续计数
    _running = True                    # 本地运行标志
    bot._is_running = True             # 同步 bot 状态

    # 确保麦克风已打开
    if not bot.mic or not bot.mic.is_active():
        bot.start_audio_input()

    while _running:
        # ---- 摄像头预览（非待机时）----
        if camera and not _in_standby:
            frame = camera.get_frame()
            if frame is not None:
                cv2.imshow("摄像头预览", frame)

        # ---- 键盘检测 ----
        key = cv2.waitKey(1) & 0xFF
        if key == 27:  # ESC 退出
            print("\n[系统] ESC 按下，退出对话")
            break

        # ---- 读取麦克风音频 ----
        try:
            audio_data = bot.mic.read(3200, exception_on_overflow=False)
        except Exception:
            time.sleep(0.01)
            continue

        # ---- VAD 语音检测 ----
        is_speaking_now = detect_speech_energy(audio_data, _SPEECH_THRESHOLD)

        if is_speaking_now:
            # ---- 有语音 ----
            _silence_start = time.time()

            # 待机唤醒
            if _in_standby:
                _wakeup_counter += 1
                if _wakeup_counter >= _WAKEUP_FRAMES:
                    _in_standby = False
                    _wakeup_counter = 0
                    print("[系统] 检测到语音，退出待机")
                    # 重新打开摄像头
                    if camera:
                        try:
                            camera.cap = cv2.VideoCapture(camera.camera_id)
                            camera.cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera.width)
                            camera.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera.height)
                            camera.show_preview()
                            print("[📷] 摄像头已恢复")
                        except Exception as e:
                            print(f"[⚠️] 摄像头恢复失败: {e}")
                else:
                    # 还在确认唤醒中，不发送音频
                    time.sleep(0.01)
                    continue
            else:
                _wakeup_counter = 0

            controller.update_idle_timer(is_speaking=True)

            # 发送音频（AI 没在说话时）
            if bot.conv and bot.callback and not bot.callback.assistant_speaking:
                bot.conv.append_audio(base64.b64encode(audio_data).decode())
                _audio_ever_sent = True

                # ---- 视频发送（CostController 驱动） ----
                if camera and _audio_ever_sent and not _in_standby:
                    now = time.time()
                    fps = controller.get_current_fps()
                    if now - last_video_send >= 1.0 / fps:
                        user_text = bot.callback.latest_user_text or ""
                        bot.callback.latest_user_text = ""
                        if controller.should_send_video(user_text):
                            b64 = camera.get_frame_base64()
                            if b64:
                                bot.send_video_frame(b64)
                                last_video_send = now
                                status = "闲置保活" if controller.is_idle() else "意图触发"
                                print(f"[📷 视觉] 发送画面 ({status}, {fps}fps)")
        else:
            # ---- 无语音 ----
            _wakeup_counter = 0

            if not _in_standby:
                silent_duration = time.time() - _silence_start

                # 超时进入待机
                if silent_duration >= _STANDBY_TIMEOUT:
                    _in_standby = True
                    print(f"[系统] 进入待机（{_STANDBY_TIMEOUT:.0f} 秒无语音），关闭摄像头")
                    if camera:
                        camera.stop_preview()
                        camera.release()
                        cv2.destroyAllWindows()
                        print("[📷] 摄像头已关闭，等待唤醒...")

                controller.update_idle_timer(is_speaking=False)

        time.sleep(0.01)
