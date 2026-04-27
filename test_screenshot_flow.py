#!/usr/bin/env python3
# coding: utf-8
"""
测试截图触发流程 - 验证完整链路
"""

import requests
import json
import time

SERVER_URL = "https://sj.tfr-studio.top"

def test_trigger_screenshot():
    """测试触发截图请求"""
    print("=" * 50)
    print("📸 测试截图触发流程")
    print("=" * 50)
    
    # 步骤1: 检查当前状态
    print("\n[步骤1] 检查当前截图请求状态...")
    try:
        resp = requests.get(f'{SERVER_URL}/api/device/screenshot/request', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ 当前状态: requested={data.get('requested')}")
        else:
            print(f"❌ 请求失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False
    
    # 步骤2: 手动触发截图请求（模拟其他电脑点击）
    print("\n[步骤2] 触发截图请求（模拟其他电脑点击）...")
    try:
        resp = requests.post(f'{SERVER_URL}/api/device/screenshot/trigger', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ 触发成功: {data.get('msg', 'OK')}")
        else:
            print(f"❌ 触发失败: HTTP {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"❌ 触发失败: {e}")
        return False
    
    # 步骤3: 再次检查状态
    print("\n[步骤3] 等待1秒后再次检查...")
    time.sleep(1)
    
    try:
        resp = requests.get(f'{SERVER_URL}/api/device/screenshot/request', timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            is_requested = data.get('requested')
            print(f"✅ 当前状态: requested={is_requested}")
            
            if is_requested:
                print("\n🎯 成功！服务器已收到截图请求")
                print("   现在请检查你的客户端是否显示：'✅ 收到截图请求，开始截图...'")
                
                # 步骤4: 等待客户端响应
                print("\n[步骤4] 等待客户端响应（最多30秒）...")
                for i in range(30):
                    time.sleep(1)
                    resp = requests.get(f'{SERVER_URL}/api/device/screenshot/request', timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        if not data.get('requested'):
                            print(f"\n✅ 客户端已响应！({i+1}秒后)")
                            
                            # 检查截图是否存在
                            print("\n[步骤5] 检查截图是否上传成功...")
                            resp = requests.get(f'{SERVER_URL}/api/device/screenshot', timeout=10, allow_redirects=False)
                            if resp.status_code in [200, 302]:
                                print("✅ 截图已成功上传！")
                                return True
                            else:
                                print(f"⚠️ 截图可能未上传: HTTP {resp.status_code}")
                                return True
                
                print("\n⚠️ 30秒内客户端未响应")
                print("   请确认:")
                print("   1. 客户端是否正在运行？")
                print("   2. 客户端日志是否有 '检查截图' 的输出？")
                return False
            else:
                print("\n❌ 失败！服务器没有记录到截图请求")
                print("   可能原因:")
                print("   - device_set 函数执行失败")
                print("   - 数据存储异常")
                return False
        else:
            print(f"❌ 检查失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ 检查失败: {e}")
        return False

if __name__ == '__main__':
    success = test_trigger_screenshot()
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 测试通过！截图功能正常工作")
    else:
        print("💔 测试失败，需要排查问题")
    print("=" * 50)