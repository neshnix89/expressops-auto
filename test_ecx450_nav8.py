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
print('ECX450 iframe found in DOM')

ecx_frame = page.frame_locator('iframe[src*="ecx450"]')

phprno = ecx_frame.locator('input#PHPRNO')
phprno.wait_for(state='visible', timeout=30000)
print('PHPRNO input visible')

# Clear any captured responses from page load
client._captured_responses.clear()
print('Cleared captured responses')

phprno.fill('70120808')
print('Filled 70120808')
phprno.press('Enter')
print('Enter pressed, waiting 10s...')
time.sleep(10)

print(f'Captured responses: {len(client._captured_responses)}')
if client._captured_responses:
    from clients.m3_h5_client import parse_ecx450_xml
    result = parse_ecx450_xml(client._captured_responses[-1])
    print('RESULT:', result)
else:
    print('No XHR captured')

client.close()
