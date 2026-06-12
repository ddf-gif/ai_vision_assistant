"""
视觉语音对话应用入口

基于 Qwen3-Omni-Flash-Realtime 模型的实时语音+视觉对话程序。
从 .env 加载配置，创建 RealtimeBot 和 Camera 实例并启动对话。
模型同时接收麦克风音频和摄像头画面，实现多模态交互。
"""

import os
import sys

# 修复 Windows 终端 GBK 编码无法输出 emoji 的问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from camera import Camera
from realtime_bot import RealtimeBot

# ---------- 加载环境变量 ----------
# 从 .env 文件加载 DASHSCOPE_API_KEY
# 若 .env 不存在，尝试从系统环境变量中读取
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
    print("📄 已加载 .env 配置文件")
else:
    # 尝试从系统环境变量获取
    print("📄 未找到 .env 文件，使用系统环境变量")

# ---------- 配置参数 ----------
# 从环境变量获取 API Key
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# 模型与音色配置（可根据需要修改）
VOICE = "Ethan"  # 音色选择，可选值见 DashScope 文档
MODEL = "qwen3-omni-flash-realtime"  # 模型名称
INSTRUCTIONS = (
    "你是AI视觉助手，能看到摄像头画面并听到用户说话。"
    "用简短、口语化的中文回答，直接提供帮助。"
)

# 摄像头配置
CAMERA_ID = 0          # 摄像头设备 ID，默认 0
CAMERA_WIDTH = 640     # 捕获分辨率宽度
CAMERA_HEIGHT = 480    # 捕获分辨率高度
VIDEO_INTERVAL = 2.0   # 自动发送视频帧的间隔（秒）

# 打断功能配置
ENABLE_KEYBOARD_INTERRUPT = True   # ESC 键打断 AI 回复
ENABLE_VOICE_INTERRUPT = False     # 语音能量打断（需要耳机避免回声误触发，否则不推荐开启）
ENERGY_THRESHOLD = 600             # 语音打断能量阈值（RMS），值越低越灵敏（200-2000）


def main():
    """
    主函数：创建并启动视觉语音对话机器人。

    流程：
    1. 检查 API Key 是否存在
    2. 初始化摄像头和预览窗口
    3. 创建 RealtimeBot 实例
    4. 建立连接并配置会话
    5. 打开麦克风开始对话
    """

    # 检查 API Key
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

    # 初始化摄像头
    camera = None
    try:
        camera = Camera(
            camera_id=CAMERA_ID,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
        )
        camera.show_preview()
    except Exception as e:
        print(f"⚠️ 摄像头初始化失败: {e}")
        print("   将以纯语音模式运行（无视觉输入）")

    # 创建视觉语音对话机器人
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
    )

    try:
        # 建立连接
        bot.connect()

        # 打开麦克风
        bot.start_audio_input()

        # 进入主循环（音频 + 视频）
        bot.run()

    except KeyboardInterrupt:
        print("\n\n🛑 程序被用户中断")
    except ConnectionError as e:
        print(f"\n❌ 网络连接错误: {e}")
        print("请检查网络连接是否正常")
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
