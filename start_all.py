# coding: utf-8
"""
Sleepy Project 启动脚本
同时启动服务器和客户端
"""

import subprocess
import sys
import os
import time
import threading

# 设置 UTF-8 编码
os.environ['PYTHONUTF8'] = '1'
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

print('''
╔══════════════════════════════════════╗
║     Sleepy Project 启动脚本          ║
╚══════════════════════════════════════╝
''')

# 启动服务器
print('[1/2] 正在启动服务器...')
server_process = subprocess.Popen(
    [sys.executable, 'main.py'],
    env={**os.environ, 'PYTHONUTF8': '1'},
    creationflags=subprocess.CREATE_NEW_CONSOLE
)

print('      等待服务器启动...')
time.sleep(3)

# 检查服务器是否启动成功
import requests
max_retries = 5
for i in range(max_retries):
    try:
        response = requests.get('http://localhost:9010/', timeout=5)
        if response.status_code == 200:
            print('      服务器已启动！')
            break
    except:
        if i < max_retries - 1:
            print(f'      等待中... ({i+1}/{max_retries})')
            time.sleep(2)
        else:
            print('      警告：服务器可能未完全启动，请检查新窗口')

print()
print('[2/2] 正在启动客户端...')
print()

# 启动客户端
try:
    subprocess.run([sys.executable, 'sleepy_client.py'])
except KeyboardInterrupt:
    print('\n客户端已停止')

# 询问是否关闭服务器
print()
choice = input('是否关闭服务器？(y/n): ')
if choice.lower() == 'y':
    server_process.terminate()
    print('服务器已关闭')
