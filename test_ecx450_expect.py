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
print('OK clicked')

page.locator('iframe[src*="ecx450"]').wait_for(state='attached', timeout=30000)
ecx_frame = page.frame_locator('iframe[src*="ecx450"]')
phprno = ecx_frame.locator('input#PHPRNO')
phprno.wait_for(state='visible', timeout=30000)
print('PHPRNO visible')
time.sleep(3)

client._captured_responses.clear()

phprno.click()
phprno.press('Control+a')
for ch in '70120808':
    phprno.type(ch, delay=100)
print('Typed 70120808')
time.sleep(1)

print('Pressing Enter and waiting for generic.do response...')
with page.expect_response(lambda r: 'generic.do' in r.url and r.status == 200, timeout=30000) as resp_info:
    phprno.press('Enter')

response = resp_info.value
body = response.text()
print(f'Got response: {len(body)} bytes, has LR: {"<LR" in body}')

if '<LR' in body:
    from clients.m3_h5_client import parse_ecx450_xml
    result = parse_ecx450_xml(body)
    print('RESULT:', result)
else:
    print('Response has no LR rows')
    print('First 500 chars:', body[:500])

client.close()
