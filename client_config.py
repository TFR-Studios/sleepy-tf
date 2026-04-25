# Sleepy Client 配置文件
# 配置设备信息和服务器连接参数

# ========== 服务器配置 ==========
SERVER_URL = "http://localhost:9010"
SECRET = "Wnnz3tz!"

# ========== 设备配置 ==========
DEVICE_ID = "my-pc"  # 设备唯一标识符（建议使用英文）
DEVICE_NAME = "我的电脑"  # 设备显示名称

# ========== 监控配置 ==========
# 状态检测间隔（秒）
CHECK_INTERVAL = 10

# 检测设备活动的方式
# 可选值: "mouse_keyboard" - 检测鼠标和键盘活动
#          "window_title" - 检测活动窗口标题
#          "both" - 同时使用两种方式
MONITOR_MODE = "window_title"

# 无活动多少秒后判定为"未在使用"
IDLE_TIMEOUT = 300  # 5分钟

# ========== 状态文本配置 ==========
# 当未在使用时显示的文本
IDLE_STATUS_TEXT = "未在使用"

# 是否显示活动窗口标题（如果 MONITOR_MODE 包含 window_title）
SHOW_WINDOW_TITLE = True
