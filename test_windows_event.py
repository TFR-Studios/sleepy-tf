#!/usr/bin/env python3
# coding: utf-8
"""
测试 Windows 关闭事件捕获功能 - 结果写入日志文件
运行后点击 X 关闭，然后查看 test_event_log.txt 文件
"""

import sys
import time
import platform
from datetime import datetime

LOG_FILE = "test_event_log.txt"

def log(message):
    """同时输出到屏幕和文件"""
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {message}\n")

# 清空旧日志
with open(LOG_FILE, "w", encoding="utf-8") as f:
    f.write("")

log("=" * 60)
log("[TEST] Windows Event Capture Test Started")
log("=" * 60)

if platform.system() != 'Windows':
    log("[ERROR] This test can only run on Windows")
    sys.exit(1)

try:
    import ctypes
    from ctypes import wintypes
    
    log("[OK] ctypes imported successfully")
    
    CTRL_CLOSE_EVENT = 2
    CTRL_LOGOFF_EVENT = 5
    CTRL_SHUTDOWN_EVENT = 6
    
    PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    
    def test_handler(ctrl_type):
        reason_map = {
            CTRL_CLOSE_EVENT: 'CLOSE_WINDOW (clicked X button)',
            CTRL_LOGOFF_EVENT: 'LOGOFF (user logged out)', 
            CTRL_SHUTDOWN_EVENT: 'SHUTDOWN (system shutting down)'
        }
        
        reason = reason_map.get(ctrl_type, f'UNKNOWN({ctrl_type})')
        log("")
        log("=" * 60)
        log("!!! SUCCESS !!!")
        log(f"Event captured: {reason}")
        log("=" * 60)
        log("")
        log("Waiting 3 seconds to ensure log is written...")
        time.sleep(3)
        log("Test PASSED! You can check " + LOG_FILE + " now.")
        return True
    
    handler = PHANDLER_ROUTINE(test_handler)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    result = kernel32.SetConsoleCtrlHandler(handler, True)
    
    if result:
        log("[OK] SetConsoleCtrlHandler registered!")
    else:
        error = ctypes.get_last_error()
        log(f"[ERROR] Failed! Error code: {error}")
        sys.exit(1)
    
except Exception as e:
    log(f"[ERROR] Init failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

log("")
log("=" * 60)
log("INSTRUCTIONS:")
log("=" * 60)
log("")
log("1. Click the X button on this window NOW")
log("2. Wait for window to close")
log("3. Open file: " + LOG_FILE)
log("4. If you see 'SUCCESS', the feature works!")
log("")
log("=" * 60)

# 保持程序运行，等待用户关闭窗口
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    log("")
    log("[INFO] User pressed Ctrl+C (normal exit)")
    log("[INFO] Please close the window by clicking X instead")