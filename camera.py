"""
摄像头模块

提供 Camera 类用于摄像头采集、JPEG 编码和预览显示。
依赖 opencv-python (cv2)。
"""

import base64
import threading
import sys
import time

import cv2
import numpy as np


class Camera:
    """
    摄像头采集与预览

    使用 OpenCV 从 USB/内置摄像头抓取帧，转为 JPEG base64 供模型使用，
    可开独立线程显示实时预览窗口。

    使用示例:
        cam = Camera(camera_id=0)
        cam.show_preview()
        b64 = cam.get_frame_base64()  # 抓取一帧
        cam.release()
    """

    def __init__(self, camera_id: int = 0, width: int = 640, height: int = 480):
        """
        初始化摄像头

        Args:
            camera_id: 摄像头设备 ID，默认 0（第一个摄像头）
            width: 捕获分辨率宽度（像素）
            height: 捕获分辨率高度（像素）
        """
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.cap = None
        self._preview_thread = None
        self._preview_running = False

        # 打开摄像头
        self.cap = cv2.VideoCapture(camera_id)
        if not self.cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 (ID={camera_id})")

        # 设置分辨率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # 预热：丢弃前几帧（摄像头刚打开时曝光不稳定）
        for _ in range(5):
            self.cap.read()
            time.sleep(0.05)

        actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        print(f"📷 摄像头已就绪 ({actual_w:.0f}x{actual_h:.0f})")

    def get_frame_base64(self) -> str:
        """
        抓取一帧画面，编码为 JPEG base64 字符串。

        Returns:
            str: base64 编码的 JPEG 图像，可直接传给 append_video()
                 若抓取失败返回空字符串 ""
        """
        if not self.cap or not self.cap.isOpened():
            return ""

        ret, frame = self.cap.read()
        if not ret or frame is None:
            return ""

        # JPEG 编码 → base64
        _, jpeg_bytes = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(jpeg_bytes).decode("utf-8")

    def get_frame(self):
        """
        抓取一帧画面，返回原始 numpy 数组（BGR 格式）。

        Returns:
            np.ndarray | None: 图像帧，失败返回 None
        """
        if not self.cap or not self.cap.isOpened():
            return None
        ret, frame = self.cap.read()
        return frame if ret else None

    def show_preview(self):
        """
        在独立线程中显示摄像头预览窗口。

        窗口标题为 "摄像头预览"，按 ESC 键关闭预览（不释放摄像头）。
        预览窗口在后台线程运行，不阻塞主线程。
        """
        if self._preview_thread and self._preview_thread.is_alive():
            print("⚠️ 预览窗口已在运行")
            return

        self._preview_running = True
        self._preview_thread = threading.Thread(
            target=self._preview_loop, daemon=True, name="camera-preview"
        )
        self._preview_thread.start()
        print("👁️ 预览窗口已打开")

    def _preview_loop(self):
        """
        预览线程主循环：持续显示摄像头画面直到收到停止信号或 ESC 按下。
        """
        cv2.namedWindow("摄像头预览", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("摄像头预览", 640, 480)

        while self._preview_running:
            frame = self.get_frame()
            if frame is not None:
                cv2.imshow("摄像头预览", frame)

            # 检查按键：ESC(27) 关闭预览
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                self._preview_running = False
                break

        cv2.destroyWindow("摄像头预览")

    def stop_preview(self):
        """停止预览窗口"""
        self._preview_running = False
        if self._preview_thread:
            self._preview_thread.join(timeout=1.0)

    def release(self):
        """
        释放摄像头资源，关闭预览窗口。
        """
        # 停止预览
        self.stop_preview()

        # 释放摄像头
        if self.cap:
            try:
                self.cap.release()
                print("📷 摄像头已释放")
            except Exception as e:
                print(f"⚠️ 释放摄像头时出错: {e}")
            self.cap = None

        # 确保所有 OpenCV 窗口关闭
        cv2.destroyAllWindows()
