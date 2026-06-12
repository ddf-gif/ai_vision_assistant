# AI 视觉助手 — 设计文档

## 一、用户故事

### 计划实现

| 故事 | 场景 | 交互方式 |
|---|---|---|
| **A 找钥匙** | 用户找不到钥匙，打开摄像头环顾四周，问"钥匙在哪" | AI 看到画面后语音指引："桌子右上角有个银色反光的东西，你看看是不是" |
| **B 识物** | 用户拿起一个物体对准镜头，问"这是什么" | AI 识别物体并用口语化中文描述 |
| **C 读菜谱** | 用户把菜谱放在摄像头前，说"帮我读一下怎么做" | AI 识别文字并朗读步骤 |

### 实际实现

以上三个故事的核心链路均已跑通：

- **故事 A/B**: 已实现。用户说"这是什么"/"钥匙在哪"等视觉关键词 → `contains_visual_keyword()` 命中 → 自动发送摄像头帧 → 模型结合画面回答。实测可正确识别保温水壶等物体。
- **故事 C**: 已实现。用户说"读"/"文字"/"识别"等关键词触发画面发送，模型会尝试识别画面中的文字并朗读。

当前系统支持所有视觉关键词触发的场景（17 个关键词覆盖看/找/识别/颜色/文字/读等意图），未单独拆分"找钥匙"或"读菜谱"的专属逻辑——均通过统一的意图过滤+视觉帧发送通道实现，符合通用视觉助手定位。

---

## 二、运营成本控制

### 采用的技巧

| 技巧 | 实现位置 | 效果 |
|---|---|---|
| **意图过滤** | `utils.py:contains_visual_keyword()` + `controller.py:should_send_video()` | 用户说"你好"时不发画面（省 99% 非视觉对话的 token） |
| **动态帧率** | `controller.py:get_current_fps()` | 活跃对话 1fps，闲置 0.1fps。由 `update_idle_timer()` 30 秒无语音自动切换 |
| **闲置降频** | `controller.py:update_idle_timer()` | 30 秒无对话进入 `_idle` 状态，帧率降至 0.1fps（每 10 秒 1 帧保活） |
| **静音休眠** | `modes.py:run_auto_mode()` 待机逻辑 | 30 秒无语音关闭摄像头 + 停止预览 + 销毁 cv2 窗口，检测到语音连续 3 帧后自动恢复 |
| **音频优先** | `realtime_bot.py:_audio_ever_sent` | 视频帧仅在首次音频发送后才允许推送（API 要求且避免无效调用） |
| **半双工回声抑制** | `realtime_bot.py:BotCallback.assistant_speaking` | AI 说话时丢弃麦克风数据，避免回声被转录产生额外 API 调用 |
| **能量门控** | `realtime_bot.py:_waiting_silence` | AI 说完后检测麦克风能量，确认扬声器静音才恢复拾音，杜绝回声循环消耗 token |

### 对比估算

假设每小时对话含 20 轮交互，每轮用户平均说话 3 秒：

| 方案 | 视频帧发送量 / 小时 | 估算 |
|---|---|---|
| **全云端无控制**（每 2 秒 1 帧） | 1,800 帧 | 全部消耗 token，包含大量无用画面 |
| **当前方案**（意图过滤 + 动态帧率 + 静音休眠） | ~40-120 帧 | 仅在视觉意图时 1fps 发送，非视觉对话不发送，闲置降至 0.1fps 保活，休眠时完全停止 |
| **节省比例** | ~93-97% | |

---

## 三、技术架构简述

### 数据流

```
┌──────────┐   ┌──────────┐
│ 麦克风    │   │ 摄像头    │
│ 16kHz    │   │ 640x480  │
│ PCM 音频  │   │ BGR 画面  │
└────┬─────┘   └────┬─────┘
     │               │
     ▼               ▼
┌──────────────────────────────┐
│        端侧控制层             │
│                              │
│  modes.py                    │
│  ├─ 手动模式: 空格键切换      │
│  └─ 自动模式: VAD 检测       │
│                              │
│  controller.py               │
│  ├─ 意图过滤                  │
│  ├─ 动态帧率                  │
│  └─ 闲置计时                  │
│                              │
│  utils.py                    │
│  ├─ contains_visual_keyword  │
│  └─ detect_speech_energy     │
└──────────┬───────────────────┘
           │
           ▼  WebSocket (wss://dashscope.aliyuncs.com)
┌──────────────────────────────┐
│   Qwen3-Omni-Flash-Realtime  │
│   ├─ append_audio (实时流)    │
│   ├─ append_video (base64帧)  │
│   ├─ 语音识别 (ASR)           │
│   ├─ 视觉理解 + 文本生成       │
│   └─ 语音合成 (TTS)           │
└──────────┬───────────────────┘
           │
           ▼  WebSocket 回调
┌──────────────────────────────┐
│  realtime_bot.py             │
│  BotCallback                 │
│  ├─ response.audio.delta     │ → PyAudio 扬声器播放
│  ├─ response.audio_transcript │ → 终端流式打字机输出
│  └─ conversation.item.       │
│      input_audio_transcription│ → [🎤 用户] 打印 + 提交给 CostController
└──────────────────────────────┘
```

### 文件结构

```
ai_vision_assistant/
├── main.py             # 入口：CLI 参数解析、组件初始化、模式路由
├── realtime_bot.py     # 核心：WebSocket 连接、BotCallback 事件处理、RealtimeBot 封装
├── camera.py           # 摄像头：OpenCV 采集、JPEG base64 编码、预览窗口
├── controller.py       # 成本控制：意图过滤、动态帧率、闲置计时
├── utils.py            # 工具：视觉关键词检测、VAD 能量判断
├── modes.py            # 交互模式：run_manual_mode（空格切换） + run_auto_mode（VAD）
├── requirements.txt    # 依赖：dashscope opencv-python numpy pyaudio python-dotenv
├── .env.example        # 环境变量模板
├── .gitignore          # Git 忽略规则（排除 .env 和 __pycache__）
└── design_doc.md       # 本文档
```

---

## 四、已知限制与改进方向

| 项目 | 说明 | 原因 |
|---|---|---|
| **语音打断** | `ENABLE_VOICE_INTERRUPT=False`（默认关闭） | 无耳机时扬声器回声会误触发。代码已完整实现，戴耳机时设为 True 即可启用 |
| **服务端取消** | `cancel_response()` 为 fire-and-forget，无响应确认 | SDK 不返回取消结果，依赖 `response.done` 隐式确认 |
| **待机恢复** | 恢复时直接重建 `cv2.VideoCapture`，无预热帧 | 引起首帧偏暗，可通过丢弃前 5 帧改善 |
| **连接健康监控** | 无 WebSocket 断线自动重连 | 当前仅在打断重连路径中实现了重试，主链路未加入心跳检测 |
| **gh CLI 跨终端** | Git Bash 环境无法使用 Windows 凭据管理器中的 gh 认证 | 需在 Git Bash 中单独执行 `gh auth login` |

