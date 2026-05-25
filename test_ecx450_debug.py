from core.config_loader import load_config
from clients.m3_h5_client import M3H5Client
import time

config = load_config()
config.mode = 'live'
client = M3H5Client(config)
client.connect()

page = client._page

# Add debug listener that mirrors _on_response but with logging
debug_results = []
def debug_on_response(response):
    if 'generic.do' in response.url:
        print(f'  DEBUG: generic.do hit: {response.url}')
        print(f'  DEBUG: status={response.status}')
        try:
            body = response.text()
            print(f'  DEBUG: body length={len(body)}')
            print(f'  DEBUG: has LR={("<LR" in body)}')
            debug_results.append(body)
        except Exception as e:
            print(f'  DEBUG: text() failed: {e}')

page.on('response', debug_on_response)

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
debug_results.clear()

phprno.click()
phprno.press('Control+a')
for ch in '70120808':
    phprno.type(ch, delay=100)
print('Typed 70120808')
time.sleep(1)

print('Pressing F5...')
phprno.press('F5')
time.sleep(10)

print(f'client._captured_responses: {len(client._captured_responses)}')
print(f'debug_results: {len(debug_results)}')

client.close()
