import requests
import time

server = 'https://sj.tfr-studio.top'

# Step 1: Trigger screenshot via device fields
print('=== Step 1: Trigger screenshot via device fields ===')
r = requests.post(f'{server}/api/device/screenshot/trigger', timeout=10)
print(f'Trigger response: {r.status_code} - {r.json()}')

time.sleep(2)

# Step 2: Check if screenshot requested flag is set
print('=== Step 2: Check screenshot request flag ===')
r = requests.get(f'{server}/api/device/screenshot/request', timeout=10)
print(f'Check response: {r.status_code} - {r.json()}')

# Step 3: Check device fields directly
print('=== Step 3: Check device fields directly ===')
r = requests.get(f'{server}/api/device/list', timeout=10)
print(f'Device list response: {r.status_code}')
if r.status_code == 200:
    devices = r.json()
    if devices.get('success'):
        my_pc = devices.get('devices', {}).get('my-pc', {})
        fields = my_pc.get('fields', {})
        print(f'my-pc fields: {fields}')
