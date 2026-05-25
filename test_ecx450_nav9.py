from core.config_loader import load_config
from clients.m3_h5_client import M3H5Client
import time

config = load_config()
config.mode = 'live'
client = M3H5Client(config)
client.connect()

page = client._page

# Add a catch-all response listener
all_responses = []
def capture_all(response):
    url = response.url
    if 'pepperl' in url or 'p-f.biz' in url:
        all_responses.append(url)

page.on('response', capture_all)

page.keyboard.press('Control+r')
time.sleep(2)

cmd = page.locator('#cmdText')
cmd.click()
cmd.fill('xecx450')
time.sleep(1)

page.locator('button:has-text("OK")').click()
print('OK clicked')

page.locator('iframe[src*="ecx450"]').wait_for(state='attached', timeout=30000)
ecx_frame = page.frame_locator('iframe[src*="ecx450"]')
phprno = ecx_frame.locator('input#PHPRNO')
phprno.wait_for(state='visible', timeout=30000)

# Clear and mark the boundary
all_responses.clear()
print('--- BOUNDARY: filling field now ---')

phprno.fill('70120808')
phprno.press('Enter')
print('Enter pressed, waiting 10s...')
time.sleep(10)

print(f'Responses after Enter: {len(all_responses)}')
for url in all_responses:
    print(f'  {url}')

client.close()
