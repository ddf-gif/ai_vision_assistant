"""
工具函数模块

提供视觉关键词检测和语音能量判断等辅助函数。
"""

import struct

# 视觉关键词列表：用户说出这些词时表示需要看画面
_VISUAL_KEYWORDS = [
    "看", "摄像头", "画面", "这里", "这个", "那个",
    "前面", "旁边", "什么", "颜色", "形状", "牌子",
    "文字", "读", "识别", "找", "在哪", "钥匙", "手机",
]


def contains_visual_keyword(text: str) -> bool:
    """
    判断用户文本是否包含视觉相关关键词。

    用于成本控制的意图过滤：只有当用户明确表达需要
    视觉信息时才发送摄像头帧，避免无故消耗 token。

    Args:
        text: 用户语音识别后的文本

    Returns:
        bool: 包含任一视觉关键词返回 True，否则 False
    """
    if not text:
        return False
    for kw in _VISUAL_KEYWORDS:
        if kw in text:
            return True
    return False


def detect_speech_energy(audio_bytes: bytes, threshold: float = 500) -> bool:
    """
    判断音频能量是否超过阈值（简易 VAD）。

    计算 16-bit PCM 音频的 RMS 值，超过阈值则判定为有人说话。

    Args:
        audio_bytes: 原始 PCM 音频字节（16-bit, mono）
        threshold: 能量阈值（RMS），默认 500

    Returns:
        bool: 能量超过阈值返回 True
    """
    if len(audio_bytes) < 2:
        return False
    count = len(audio_bytes) // 2
    try:
        samples = struct.unpack(f"{count}h", audio_bytes)
    except struct.error:
        return False
    rms = (sum(s * s for s in samples) / count) ** 0.5
    return rms > threshold
