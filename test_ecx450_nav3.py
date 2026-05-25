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

# Click OK button
ok_btn = page.locator('button:has-text("OK")')
print('OK button found:', ok_btn.count())
ok_btn.click()
print('OK clicked')

time.sleep(8)

print('=== FRAMES AFTER OK ===')
for i, frame in enumerate(page.frames):
    print(f'  Frame {i}: url={frame.url}')

iframes = page.locator('iframe').all()
for i, iframe in enumerate(iframes):
    src = iframe.get_attribute('src') or ''
    print(f'  iframe {i}: src={src}')

client.close()
