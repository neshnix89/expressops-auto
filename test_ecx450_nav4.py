from core.config_loader import load_config
from clients.m3_h5_client import M3H5Client
import time

config = load_config()
config.mode = 'live'
client = M3H5Client(config)
client.connect()

page = client._page

page.keyboard.press('Control+r')
time.sleep(2)

cmd = page.locator('#cmdText')
cmd.click()
cmd.fill('xecx450')
time.sleep(1)

page.locator('button:has-text("OK")').click()
print('OK clicked, waiting 15s for page load...')
time.sleep(15)

print('=== FRAMES ===')
for i, frame in enumerate(page.frames):
    print(f'  Frame {i}: url={frame.url}')

# Find the ecx450 frame
ecx_frame = None
for frame in page.frames:
    if 'ecx450' in frame.url:
        ecx_frame = frame
        print(f'Found ECX450 frame: {frame.url}')
        break

if ecx_frame:
    phprno = ecx_frame.locator('[name="PHPRNO"]')
    print(f'PHPRNO field count: {phprno.count()}')
    if phprno.count() > 0:
        phprno.fill('70120808')
        print('Filled PHPRNO with 70120808')
        ecx_frame.keyboard.press('Enter')
        print('Enter pressed, waiting 10s...')
        time.sleep(10)
        print(f'Captured responses: {len(client._captured_responses)}')
        if client._captured_responses:
            from clients.m3_h5_client import parse_ecx450_xml
            result = parse_ecx450_xml(client._captured_responses[-1])
            print('RESULT:', result)
        else:
            print('No XHR captured')
else:
    print('ECX450 frame not found')

client.close()
