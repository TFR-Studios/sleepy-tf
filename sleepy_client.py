# coding: utf-8
"""
Sleepy Client - 设备状态监控客户端
自动检测设备使用状态并推送到 Sleepy 服务端
"""

import time
import sys
import platform
import requests
import logging
import io
from datetime import datetime
from client_config import (
    SERVER_URL,
    SECRET,
    DEVICE_ID,
    DEVICE_NAME,
    CHECK_INTERVAL,
    MONITOR_MODE,
    IDLE_TIMEOUT,
    IDLE_STATUS_TEXT,
    SHOW_WINDOW_TITLE
)

# 设置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class ActivityMonitor:
    """监控设备活动状态"""
    
    def __init__(self):
        self.last_activity_time = time.time()
        
        # 根据操作系统选择监控方式
        self.system = platform.system()
        logger.info(f'运行在 {self.system} 系统上')
        
        # 初始化鼠标/键盘监控
        if 'mouse_keyboard' in MONITOR_MODE or MONITOR_MODE == 'both':
            try:
                import pynput
                self.mouse_keyboard_monitor = pynput
                self._setup_input_monitor()
                logger.info('鼠标/键盘监控已启用')
            except ImportError:
                logger.warning('pynput 未安装，请运行: pip install pynput')
                self.mouse_keyboard_monitor = None
        
        # 初始化窗口标题监控
        if 'window_title' in MONITOR_MODE or MONITOR_MODE == 'both':
            try:
                import pygetwindow
                self.window_monitor = pygetwindow
                logger.info('窗口标题监控已启用')
            except ImportError:
                logger.warning('pygetwindow 未安装，请运行: pip install pygetwindow')
                self.window_monitor = None
    
    def _setup_input_monitor(self):
        """设置鼠标和键盘监听器"""
        if not self.mouse_keyboard_monitor:
            return
            
        def on_activity():
            self.last_activity_time = time.time()
        
        try:
            from pynput import mouse, keyboard
            
            mouse_listener = mouse.Listener(
                on_move=lambda x, y: on_activity(),
                on_click=lambda *args: on_activity(),
                on_scroll=lambda *args: on_activity()
            )
            keyboard_listener = keyboard.Listener(
                on_press=lambda key: on_activity()
            )
            
            mouse_listener.start()
            keyboard_listener.start()
            logger.info('鼠标和键盘监听器已启动')
        except Exception as e:
            logger.error(f'启动输入监听器失败: {e}')
    
    def get_current_status(self):
        """
        获取当前设备状态
        返回: (is_using: bool, status_text: str)
        """
        current_time = time.time()
        idle_duration = current_time - self.last_activity_time
        
        # 检查是否空闲
        if idle_duration > IDLE_TIMEOUT:
            return False, IDLE_STATUS_TEXT
        
        # 如果活跃，尝试获取当前活动窗口标题
        status_text = "正在使用"
        if hasattr(self, 'window_monitor') and self.window_monitor:
            try:
                active_window = self.window_monitor.getActiveWindow()
                if active_window and active_window.title:
                    title = active_window.title
                    if SHOW_WINDOW_TITLE:
                        status_text = title
                    logger.debug(f'活动窗口: {title}')
            except Exception as e:
                logger.debug(f'获取窗口标题失败: {e}')
        
        return True, status_text


class SleepyClient:
    """Sleepy 客户端，负责推送状态到服务端"""
    
    def __init__(self):
        self.server_url = SERVER_URL.rstrip('/')
        self.secret = SECRET
        self.device_id = DEVICE_ID
        self.device_name = DEVICE_NAME
        
        self.monitor = ActivityMonitor()
        self.last_push_time = 0
        self.last_status = None
        
        logger.info(f'Sleepy Client 已启动')
        logger.info(f'服务器: {self.server_url}')
        logger.info(f'设备ID: {self.device_id}')
        logger.info(f'设备名称: {self.device_name}')
    
    def push_status(self, is_using: bool, status_text: str):
        """推送设备状态到服务端"""
        # 如果状态没有变化，不推送（避免频繁请求）
        current_status = (is_using, status_text)
        if current_status == self.last_status:
            return
        
        try:
            payload = {
                'secret': self.secret,
                'id': self.device_id,
                'show_name': self.device_name,
                'using': is_using,
                'status': status_text
            }
            
            url = f'{self.server_url}/api/device/set'
            logger.debug(f'POST {url}')
            logger.debug(f'Payload: {payload}')
            
            # 禁用代理，确保直接连接
            response = requests.post(
                url,
                json=payload,
                timeout=10,
                proxies={'http': None, 'https': None}
            )
            
            logger.debug(f'Response status: {response.status_code}')
            logger.debug(f'Response text: {response.text}')
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    logger.info(f'状态已推送 - {"使用中" if is_using else "空闲"}: {status_text}')
                    self.last_status = current_status
                    self.last_push_time = time.time()
                else:
                    logger.error(f'推送失败: {data}')
            else:
                logger.error(f'HTTP错误: {response.status_code} - {response.text}')
        
        except requests.exceptions.ConnectionError:
            logger.error('无法连接到服务器，请检查服务是否运行')
        except Exception as e:
            logger.error(f'推送异常: {e}')
    
    def take_screenshot(self):
        """截取屏幕并返回字节流"""
        try:
            import io
            import mss
            
            # 截取屏幕
            with mss.mss() as sct:
                # monitors[0] 是所有显示器的合并区域
                # monitors[1] 是主显示器（显示器1）
                monitor = sct.monitors[1]
                screenshot = sct.grab(monitor)
                
                # 转换为 PNG 字节流
                from PIL import Image
                img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                img_byte_arr.seek(0)
                
                logger.info('截图已捕获（主显示器）')
                return img_byte_arr
        
        except Exception as e:
            logger.error(f'截图异常: {e}')
        return None
    
    def upload_screenshot(self):
        """上传截图到服务器"""
        try:
            screenshot_bytes = self.take_screenshot()
            if not screenshot_bytes:
                return
            
            files = {'screenshot': ('screenshot.png', screenshot_bytes, 'image/png')}
            data = {'secret': self.secret, 'device_id': self.device_id}
            
            url = f'{self.server_url}/api/device/screenshot'
            resp = requests.post(url, files=files, data=data, proxies={'http': None, 'https': None}, timeout=30)
            
            if resp.status_code == 200:
                logger.info('截图已上传到服务器')
            else:
                logger.error(f'截图上传失败: {resp.status_code} - {resp.text}')
        except Exception as e:
            logger.error(f'截图上传异常: {e}')
    
    def check_and_upload_screenshot(self):
        """检查是否有截图请求，如有则上传"""
        try:
            url = f'{self.server_url}/api/device/screenshot/request'
            logger.debug(f'检查截图请求: {url}')
            resp = requests.get(url, proxies={'http': None, 'https': None}, timeout=10)
            logger.debug(f'截图请求响应: {resp.status_code} - {resp.text}')
            if resp.status_code == 200:
                data = resp.json()
                if data.get('requested'):
                    logger.info('✅ 收到截图请求，开始截图...')
                    self.upload_screenshot()
                else:
                    logger.debug('❌ 没有截图请求')
            else:
                logger.error(f'检查截图请求失败: HTTP {resp.status_code}')
        except Exception as e:
            logger.error(f'检查截图请求异常: {e}')
    
    def run(self):
        """主循环"""
        logger.info('开始监控设备活动...')
        logger.info(f'检测间隔: {CHECK_INTERVAL}秒')
        logger.info(f'空闲超时: {IDLE_TIMEOUT}秒')
        logger.info(f'截图检查频率: 每{max(1, 10 // CHECK_INTERVAL) * CHECK_INTERVAL}秒')
        print()
        
        screenshot_check_counter = 0
        screenshot_check_freq = max(1, 10 // CHECK_INTERVAL)  # 每 10 秒检查一次截图请求
        
        try:
            while True:
                is_using, status_text = self.monitor.get_current_status()
                self.push_status(is_using, status_text)
                
                # 定期检查是否有截图请求
                screenshot_check_counter += 1
                if screenshot_check_counter >= screenshot_check_freq:
                    self.check_and_upload_screenshot()
                    screenshot_check_counter = 0
                
                time.sleep(CHECK_INTERVAL)
        
        except KeyboardInterrupt:
            logger.info('客户端已停止')
            self.push_status(False, '已关闭')


if __name__ == '__main__':
    print('''
╔══════════════════════════════════════╗
║     Sleepy Client v1.0              ║
║     设备状态监控客户端                ║
╚══════════════════════════════════════╝
''', flush=True)
    
    client = SleepyClient()
    
    # 启动本地 HTTP 接口（供网页调用截图）
    from flask import Flask as MiniFlask, send_file, jsonify, make_response
    import io
    import os
    
    cmd_app = MiniFlask('sleepy_client')
    latest_screenshot = None
    
    @cmd_app.route('/command/screenshot', methods=['POST'])
    def cmd_screenshot():
        global latest_screenshot
        # 截图
        screenshot_bytes = client.take_screenshot()
        if screenshot_bytes:
            latest_screenshot = screenshot_bytes.getvalue()
            # 允许跨域
            response = make_response(jsonify({'success': True, 'timestamp': int(time.time())}))
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return response
        response = make_response(jsonify({'success': False, 'error': '截图失败'}), 500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    
    @cmd_app.route('/command/screenshot/latest', methods=['GET'])
    def cmd_get_latest_screenshot():
        global latest_screenshot
        if latest_screenshot:
            response = make_response(send_file(
                io.BytesIO(latest_screenshot),
                mimetype='image/png',
                as_attachment=False
            ))
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        response = make_response(jsonify({'error': 'No screenshot available'}), 404)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    
    # 在后台线程启动本地接口
    import threading
    cmd_thread = threading.Thread(target=lambda: cmd_app.run(host='0.0.0.0', port=9011, use_reloader=False), daemon=True)
    cmd_thread.start()
    logger.info('本地接口已启动: http://0.0.0.0:9011')
    logger.info('访客可通过网页调用截图功能')
    
    # 主线程运行客户端
    try:
        while True:
            is_using, status_text = client.monitor.get_current_status()
            client.push_status(is_using, status_text)
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        logger.info('客户端已停止')
        client.push_status(False, '已关闭')
