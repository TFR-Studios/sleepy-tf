import requests
import time

server = 'https://sj.tfr-studio.top'

print('=== Step 1: Trigger screenshot ===')
r = requests.post(f'{server}/api/device/screenshot/trigger', timeout=10)
print(f'Trigger: {r.status_code} - {r.json()}')

time.sleep(3)

print('=== Step 2: Check screenshot request ===')
r = requests.get(f'{server}/api/device/screenshot/request', timeout=10)
print(f'Check: {r.status_code} - {r.json()}')

print('=== Step 3: Check device fields ===')
r = requests.get(f'{server}/api/device/list', timeout=10)
if r.status_code == 200:
    data = r.json()
    if data.get('success') and data.get('devices'):
        my_pc = data['devices'].get('my-pc', {})
        print(f'Fields: {my_pc.get("fields", {})}')
