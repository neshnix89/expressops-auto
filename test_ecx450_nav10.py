from core.config_loader import load_config
from clients.m3_h5_client import M3H5Client
import time

config = load_config()
config.mode = 'live'
client = M3H5Client(config)
client.connect()

page = client._page

all_responses = []
def capture_all(response):
    try:
        ct = response.headers.get('content-type', '')
        if 'xml' in ct or 'html' in ct or 'json' in ct:
            all_responses.append({'url': response.url, 'ct': ct})
    except:
        pass

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
print('PHPRNO visible')
time.sleep(3)

all_responses.clear()
print('--- typing product number ---')

phprno.click()
phprno.press('Control+a')
for ch in '70120808':
    phprno.type(ch, delay=100)
print('Typed 70120808')
time.sleep(1)

print('Pressing Enter...')
phprno.press('Enter')
time.sleep(10)

print(f'Responses after Enter: {len(all_responses)}')
for r in all_responses:
    print(f'  {r["url"][:120]}  ct={r["ct"]}')

if len(all_responses) == 0:
    print('No response from Enter. Trying F5...')
    all_responses.clear()
    phprno.press('F5')
    time.sleep(10)
    print(f'Responses after F5: {len(all_responses)}')
    for r in all_responses:
        print(f'  {r["url"][:120]}  ct={r["ct"]}')

client.close()
