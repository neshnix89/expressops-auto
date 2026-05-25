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
for ch in 'ecx450':
    cmd.type(ch, delay=200)
time.sleep(2)

link = page.locator('a[data-m3-link*="ecx450"]').first
print('Link text:', link.inner_text())
link.dispatch_event('click')
print('dispatch_event click fired')

time.sleep(8)

print('=== FRAMES AFTER CLICK ===')
for i, frame in enumerate(page.frames):
    print(f'  Frame {i}: url={frame.url}')

iframes = page.locator('iframe').all()
for i, iframe in enumerate(iframes):
    src = iframe.get_attribute('src') or ''
    print(f'  iframe {i}: src={src}')

client.close()
