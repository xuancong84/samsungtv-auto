#!/opt/samsungtv/anaconda3/bin/python

import os, sys, argparse, re, time, json, base64, ssl
from samsungtvws import SamsungTVWS
from websocket import create_connection

IPs = ["192.168.1.100", "192.168.1.101", "192.168.1.102"]
MACs = ['28:af:42:ac:88:f0', '28:af:42:ac:88:f5', '28:af:42:ac:88:f3']
TOKENs = ['16098157', '99431653', '69881938']
BROWSER_APP_ID = "3202010022079"


ping = lambda ip: os.system(f'ping -W 1 -c 1 {ip}')==0
IP = MAC = TOKEN = tv = ws = None

def ws_connect():
	name_b64 = base64.b64encode(b"py-remote").decode()
	u = f"wss://{IP}:8002/api/v2/channels/samsung.remote.control?name={name_b64}"
	if TOKEN: u += f"&token={TOKEN}"
	return create_connection(u, sslopt={"cert_reqs": ssl.CERT_NONE})

def send_key(key, repeat=1):
	for i in range(repeat):
		ws.send(json.dumps({"method":"ms.remote.control","params":{
			"Cmd":"Click","DataOfCmd":key,"Option":"false","TypeOfRemote":"SendRemoteKey"}}))
		time.sleep(1.5)

def send_text(text):
	ws.send(json.dumps({"method":"ms.remote.control","params":{
		"Cmd": text,
		# "DataOfCmd": base64.b64encode(text.encode()).decode(),
		"Option":"false",
		"TypeOfRemote":"SendInputString"}}))
	time.sleep(2)

def refresh():
	global IP, MAC, TOKEN, tv, ws
	if tv is None:
		tv = SamsungTVWS(host=IP, port=8002, token=TOKEN)
	else:
		tv.close()
		tv.open()
	ws = ws_connect()

def open_browser():
	return tv.rest_app_run(BROWSER_APP_ID)

def close_browser():
	return tv.rest_app_close(BROWSER_APP_ID)

def is_browser_running():
	try:
		obj = tv.rest_app_status("3202010022079")
		if obj['visible'] and obj['running']:
			return 2
		if obj['running']:
			return 1
	except:
		pass
	return 0

def is_tv_on():
	return ping(IP)

def power_on():
	global tv, ws
	if is_tv_on():
		return
	for i in range(3):
		os.system(f'wakeonlan {MAC}')
		time.sleep(5)
		if is_tv_on():
			break
	try:
		tv = SamsungTVWS(host=IP, port=8002, token=TOKEN)
		ws = ws_connect()
		return True
	except:
		return False

def power_off():
	return tv.shortcuts().power()

def open_url(url):
	for i in range(3):
		if is_tv_on():
			break
		power_on()
		time.sleep(5)

	if not is_tv_on():
		print('Error: cannot turn on the TV')
		return False

	ibr = is_browser_running()
	if ibr:
		close_browser()
		time.sleep(2)

	open_browser()
	time.sleep(8 if ibr==0 else 5)

	# Make sure we’re at the top chrome, not on a tile from the start page.
	send_key("KEY_UP", 3)

	# Open the URL field (Enter usually opens the address box / keyboard)
	send_key("KEY_ENTER")
	time.sleep(1)

	# Type the URL, then Go
	send_text(url)
	send_key("KEY_DOWN", 4)
	send_key("KEY_RIGHT", 2)
	send_key("KEY_ENTER")
	send_key("KEY_DOWN")

key_map = {
	'up': 'KEY_UP',
	'down': 'KEY_DOWN',
	'left': 'KEY_LEFT',
	'right': 'KEY_RIGHT',
	'enter': 'KEY_ENTER',
	'backspace': 'KEY_RETURN',
	'home': 'KEY_HOME',
	'p': 'KEY_POWER',
}

def on_press(key):
	global IP, MAC, TOKEN, tv, ws
	key_code = key_map.get(key, None)
	if key_code is None:
		return
	print(f"Key '{key}' is pressed, sending {key_code}")
	while True:
		try:
			if key == 'p':
				if is_tv_on():
					return power_off()
				else:
					return power_on()
			return tv.send_key(key_code)
		except:
			refresh()

def remote():
	global IP, MAC, TOKEN, tv, ws
	from sshkeyboard import listen_keyboard
 
	print(f'Remote controller mode, available keys are {key_map}, press ESC to quit:')

	if not is_tv_on():
		print('TV is off, press P to power on')
  
	listen_keyboard(on_press=on_press)

def control(tv_number, command):
	global IP, MAC, TOKEN, tv, ws
	IP = IPs[tv_number]
	MAC = MACs[tv_number]
	TOKEN = TOKENs[tv_number]

	if is_tv_on():
		tv = SamsungTVWS(host=IP, port=8002, token=TOKEN)
		ws = ws_connect()

	if command is None:
		return remote()
	elif command == 'on':
		return power_on()
	elif command == 'off':
		return power_off()
	elif command == 'openBrowser':
		return open_browser()
	elif command == 'closeBrowser':
		return close_browser()
	elif command.startswith('KEY_'):
		return tv.send_key(command)
	elif command.startswith('http'):
		return open_url(command)
	else:
		print(f'Unknown command: {command}')

if __name__=='__main__':
	parser = argparse.ArgumentParser(usage='$0 tv_number command 1>output 2>progress', description='Control Samsung TVs',
					formatter_class=argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument('tv_number', type=int, help='TV number, e.g., 1, 2, 3')
	parser.add_argument('command', help='Command, e.g., on, off, openBrowser, closeBrowser, KEY_UP, KEY_VOLUP, KEY_MUTE, KEY_HOME, http://www.google.com.sg', nargs='?')
	#nargs='?': optional positional argument; action='append': multiple instances of the arg; type=; default=
	opt=parser.parse_args()

	control(opt.tv_number-1, opt.command)
