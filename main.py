"""
视觉语音对话应用入口

基于 Qwen3-Omni-Flash-Realtime 模型的实时语音+视觉对话程序。
从 .env 加载配置，创建 RealtimeBot 和 Camera 实例并启动对话。

支持两种交互模式：
  --mode manual  手动模式（按住空格键说话，松开停止）
  --mode auto    自动模式（VAD 语音检测，自动判断）
"""

import argparse
import os
import sys

# 修复 Windows 终端 GBK 编码无法输出 emoji 的问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from camera import Camera
from controller import CostController
from realtime_bot import RealtimeBot
from modes import run_manual_mode, run_auto_mode

# ---------- 加载环境变量 ----------
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
    print("📄 已加载 .env 配置文件")
else:
    print("📄 未找到 .env 文件，使用系统环境变量")

# ---------- 配置参数 ----------
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

VOICE = "Ethan"
MODEL = "qwen3-omni-flash-realtime"
INSTRUCTIONS = (
    "你是AI视觉助手，能看到摄像头画面并听到用户说话。"
    "用简短、口语化的中文回答，直接提供帮助。"
)

# 摄像头配置
CAMERA_ID = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
VIDEO_INTERVAL = 2.0

# 打断功能配置
ENABLE_KEYBOARD_INTERRUPT = False  # 手动/自动模式下由 modes 管理，此处关闭
ENABLE_VOICE_INTERRUPT = False
ENERGY_THRESHOLD = 600


def main():
    # ---- 命令行参数 ----
    parser = argparse.ArgumentParser(description="视觉语音对话助手")
    parser.add_argument(
        "--mode", choices=["manual", "auto"], default="manual",
        help="交互模式：manual=按住空格说话，auto=VAD 自动检测（默认 manual）",
    )
    args = parser.parse_args()

    # ---- 检查 API Key ----
    if not DASHSCOPE_API_KEY:
        print("=" * 50)
        print("❌ 错误：未找到 DASHSCOPE_API_KEY")
        print("=" * 50)
        print("\n请通过以下任一方式配置 API Key：")
        print("  1. 在项目目录下创建 .env 文件，写入：")
        print("     DASHSCOPE_API_KEY=your_key_here")
        print("  2. 设置系统环境变量 DASHSCOPE_API_KEY")
        print("\n你可以在阿里云 DashScope 控制台获取 API Key：")
        print("  https://dashscope.console.aliyun.com/apiKey")
        sys.exit(1)

    # ---- 初始化摄像头 ----
    camera = None
    try:
        camera = Camera(
            camera_id=CAMERA_ID,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
        )
    except Exception as e:
        print(f"⚠️ 摄像头初始化失败: {e}")
        print("   将以纯语音模式运行（无视觉输入）")

    # ---- 初始化成本控制器 ----
    cost_ctrl = CostController()
    print("💰 成本控制器已就绪 (活跃:1fps, 闲置:0.1fps, 超时:30s)")

    # ---- 创建 RealtimeBot ----
    bot = RealtimeBot(
        api_key=DASHSCOPE_API_KEY,
        voice=VOICE,
        model=MODEL,
        instructions=INSTRUCTIONS,
        energy_threshold=ENERGY_THRESHOLD,
        enable_keyboard_interrupt=ENABLE_KEYBOARD_INTERRUPT,
        enable_voice_interrupt=ENABLE_VOICE_INTERRUPT,
        camera=camera,
        video_interval=VIDEO_INTERVAL,
        cost_controller=cost_ctrl,
    )

    try:
        # 建立连接
        bot.connect()

        # 打开麦克风
        bot.start_audio_input()

        # 根据模式启动对应的交互循环
        if args.mode == "manual":
            run_manual_mode(bot, camera, cost_ctrl)
        elif args.mode == "auto":
            run_auto_mode(bot, camera, cost_ctrl)

    except KeyboardInterrupt:
        print("\n\n🛑 程序被用户中断")
    except ConnectionError as e:
        print(f"\n❌ 网络连接错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生未预期的错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # 释放资源
        if bot:
            bot.close()
        if camera:
            camera.release()


if __name__ == "__main__":
    main()
