# AI 视觉助手 (AI Vision Assistant)

基于 **Qwen3-Omni-Flash-Realtime** 的实时语音+视觉多模态对话应用。用户可以通过语音与 AI 对话，AI 同时看到摄像头画面并听到用户说话，实现"边看边聊"的自然交互。

## 功能特性

- **实时语音对话**：麦克风采集 → WebSocket 推流 → 模型语音+文本双模态输出
- **视觉理解**：摄像头画面实时推送给模型，AI 能"看到"用户展示的物体并描述
- **流式输出**：AI 回复的文字随语音逐字打印（打字机效果）
- **ESC 打断**：AI 说话时按 ESC 立即打断，切换话题
- **两种交互模式**：
  - 手动模式 `--mode manual`：按空格键录制，松开停止
  - 自动模式 `--mode auto`：VAD 语音检测，自动判断说话/静音
- **成本控制**：意图过滤 + 动态帧率 + 闲置降频 + 静音休眠，视频帧发送量减少 93-97%

## 架构总览

```
麦克风 + 摄像头
     │
     ▼
端侧控制层 (modes.py / controller.py / utils.py)
     │ 意图过滤、动态帧率、VAD 检测
     ▼
WebSocket → Qwen3-Omni-Flash-Realtime → 语音+文本回调
     │
     ▼
扬声器播放 + 终端流式文字输出
```

## 快速开始

### 1. 环境要求

- Python 3.8+
- 麦克风 + 摄像头（USB 或内置）
- Windows / macOS / Linux

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入阿里云 DashScope API Key
# 获取地址: https://dashscope.console.aliyun.com/apiKey
```

### 4. 运行

```bash
# 手动模式：按空格键说话，松开停止
python main.py

# 自动模式：VAD 自动检测说话
python main.py --mode auto
```

## 使用说明

### 手动模式

```
🎧 手动模式：按 [空格键] 切换录音（开/关）
   [ESC] AI 说话时打断，未说话时退出对话
```

- 按一下空格键：开始录音
- 再按一下空格键：停止录音
- AI 说话时按 ESC：打断当前回复
- AI 未说话时按 ESC：退出程序

### 自动模式

```
🎧 自动模式：VAD 语音检测，自动判断说话/静音
   待机超时: 30 秒无语音后关闭摄像头
   [ESC] AI 说话时打断，未说话时退出对话
```

- 直接说话即可，系统自动检测语音并发送
- 30 秒无语音自动进入待机（关闭摄像头省资源）
- 再次说话自动唤醒
- ESC 行为同手动模式

### 视觉交互

说出以下关键词会自动发送摄像头画面给 AI：

> 看、摄像头、画面、这里、这个、那个、前面、旁边、什么、颜色、形状、牌子、文字、读、识别、找、在哪、钥匙、手机

例如："这是什么？"——AI 会看到摄像头画面并描述。

## 项目结构

```
ai_vision_assistant/
├── main.py             # 入口：CLI 参数解析、组件初始化、模式路由
├── realtime_bot.py     # 核心：WebSocket 连接、事件回调、音频/视频收发、打断/回声抑制
├── camera.py           # 摄像头：OpenCV 采集、JPEG base64 编码、预览窗口
├── controller.py       # 成本控制：意图过滤、动态帧率(1fps/0.1fps)、闲置计时(30s)
├── utils.py            # 工具：17 个视觉关键词检测、VAD 语音能量判断
├── modes.py            # 交互模式：手动模式(空格切换) + 自动模式(VAD检测+待机休眠)
├── design_doc.md       # 设计文档：用户故事、成本控制、技术架构
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板
└── .gitignore
```

## 依赖

| 包 | 用途 |
|---|---|
| `dashscope >= 1.23.9` | 阿里云 DashScope SDK（WebSocket 实时对话） |
| `opencv-python >= 4.5` | 摄像头采集与预览 |
| `numpy` | OpenCV 依赖 |
| `pyaudio` | 麦克风采集与扬声器播放 |
| `python-dotenv` | 从 .env 加载配置 |

## Demo 视频

> [待上传至 B站/云盘，链接将在此处更新]

## 比赛信息

本项目参加七牛云技术比赛，详见 [design_doc.md](design_doc.md)。

## License

MIT
