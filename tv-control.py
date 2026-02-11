#!/opt/samsungtv/anaconda3/bin/python

import os, sys, argparse, re, time, json, base64, ssl
from samsungtvws import SamsungTVWS
from websocket import create_connection

# -----------------------------
# Config / secrets
# -----------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SECRET_FILE = os.path.join(SCRIPT_DIR, "secret.json")

SECRET_FILE = DEFAULT_SECRET_FILE
TVS = []  # list of {"ip": str, "mac": str, "token": str|None}

IP = MAC = TOKEN = tv = ws = None
TV_INDEX = None
BROWSER_APP_ID = "3202010022079"


def _normalize_tvs(data: dict):
    """
    Accept either:
      1) {"tvs":[{"ip":"...","mac":"...","token":"..."}]}
      2) {"IPs":[...], "MACs":[...], "TOKENs":[...]} (legacy)
    Returns normalized list[dict].
    """
    if "tvs" in data:
        tvs = data["tvs"]
        if not isinstance(tvs, list) or not tvs:
            raise ValueError("'tvs' must be a non-empty list")
        out = []
        for i, x in enumerate(tvs):
            if not isinstance(x, dict):
                raise ValueError(f"tvs[{i}] must be an object")
            ip = str(x.get("ip", "")).strip()
            mac = str(x.get("mac", "")).strip()
            tok = x.get("token", None)
            tok = None if tok is None else str(tok).strip()
            if not ip or not mac:
                raise ValueError(f"tvs[{i}] missing ip/mac")
            out.append({"ip": ip, "mac": mac, "token": tok if tok else None})
        return out

    # Legacy format
    ips = data.get("IPs")
    macs = data.get("MACs")
    toks = data.get("TOKENs", [])
    if ips is None or macs is None:
        raise ValueError("secret.json must contain either 'tvs' or legacy 'IPs'/'MACs'/'TOKENs' keys")

    if not isinstance(ips, list) or not isinstance(macs, list) or not isinstance(toks, list):
        raise ValueError("'IPs', 'MACs', 'TOKENs' must be arrays")

    n = min(len(ips), len(macs))
    out = []
    for i in range(n):
        ip = str(ips[i]).strip()
        mac = str(macs[i]).strip()
        tok = str(toks[i]).strip() if i < len(toks) and toks[i] is not None else None
        if not ip or not mac:
            raise ValueError(f"Invalid IP/MAC at index {i}")
        out.append({"ip": ip, "mac": mac, "token": tok if tok else None})

    if not out:
        raise ValueError("No TV entries found in secret.json")
    return out


def load_secrets(path: str):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Secret file not found: {path}\n"
            f"Create it, for example:\n"
            f'{{"tvs":[{{"ip":"192.168.1.100","mac":"28:af:42:ac:88:f0","token":"16098157"}}]}}'
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _normalize_tvs(data)


def save_secrets(path: str, tvs):
    payload = {
        "tvs": [
            {"ip": t["ip"], "mac": t["mac"], "token": t.get("token")}
            for t in tvs
        ]
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def persist_token_if_changed():
    """
    If samsungtvws updates token after connect, persist it.
    """
    global TOKEN, TVS, TV_INDEX, tv
    if tv is None or TV_INDEX is None:
        return

    new_token = None
    for attr in ("token", "_token"):
        if hasattr(tv, attr):
            v = getattr(tv, attr)
            if v:
                new_token = str(v).strip()
                break

    if not new_token:
        return

    cur = "" if TOKEN is None else str(TOKEN).strip()
    if new_token != cur:
        TOKEN = new_token
        TVS[TV_INDEX]["token"] = new_token
        save_secrets(SECRET_FILE, TVS)
        print(f"Updated token for TV #{TV_INDEX + 1} in {SECRET_FILE}")

def _extract_event_name(exc: Exception):
    # parse e.g. "{'event': 'ms.remote.touchDisable'}"
    m = re.search(r"'event'\s*:\s*'([^']+)'", str(exc))
    return m.group(1) if m else None


def clear_saved_token():
    global TOKEN, TVS, TV_INDEX
    TOKEN = None
    if TV_INDEX is not None and 0 <= TV_INDEX < len(TVS):
        TVS[TV_INDEX]["token"] = None
        save_secrets(SECRET_FILE, TVS)
        print(f"Cleared saved token for TV #{TV_INDEX + 1} in {SECRET_FILE}")


def connect_session(allow_tokenless=True):
    """
    Try connect with saved token first, then (optionally) without token
    so TV can re-pair and issue a new token.
    """
    global tv, ws, TOKEN

    candidates = []
    if TOKEN not in (None, "", "None"):
        candidates.append(str(TOKEN).strip())
    if allow_tokenless:
        candidates.append(None)

    last_err = None
    used = set()

    for tok in candidates:
        # avoid duplicate None / duplicate token
        key = "<none>" if tok is None else tok
        if key in used:
            continue
        used.add(key)

        try:
            # fresh object each try
            if tv is not None:
                try:
                    tv.close()
                except Exception:
                    pass

            tv = SamsungTVWS(host=IP, port=8002, token=tok)
            tv.open()
            ws = ws_connect(tok)

            # keep global TOKEN aligned with the session token used
            TOKEN = tok

            # if library got a new token, persist it to secret.json
            persist_token_if_changed()
            return True
        except Exception as e:
            last_err = e

    raise last_err if last_err else RuntimeError("Failed to connect to TV")

def select_tv(tv_number: int):
    global TV_INDEX, IP, MAC, TOKEN
    if tv_number < 0 or tv_number >= len(TVS):
        raise IndexError(f"tv_number out of range: {tv_number + 1}. Available TVs: 1..{len(TVS)}")
    TV_INDEX = tv_number
    cfg = TVS[tv_number]
    IP = cfg["ip"]
    MAC = cfg["mac"]
    TOKEN = cfg.get("token")


# -----------------------------
# TV operations
# -----------------------------
ping = lambda ip: os.system(f'ping -W 1 -c 1 {ip} >/dev/null 2>&1') == 0

def ws_connect(token_override=None):
    name_b64 = base64.b64encode(b"py-remote").decode()
    tok = TOKEN if token_override is None else token_override
    u = f"wss://{IP}:8002/api/v2/channels/samsung.remote.control?name={name_b64}"
    if tok:
        u += f"&token={tok}"
    return create_connection(u, sslopt={"cert_reqs": ssl.CERT_NONE})

def send_key(key, repeat=1):
    for i in range(repeat):
        ws.send(json.dumps({"method": "ms.remote.control", "params": {
            "Cmd": "Click", "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))
        time.sleep(1.5)


def send_text(text):
    ws.send(json.dumps({"method": "ms.remote.control", "params": {
        "Cmd": text,
        # "DataOfCmd": base64.b64encode(text.encode()).decode(),
        "Option": "false",
        "TypeOfRemote": "SendInputString"}}))
    time.sleep(2)


def refresh():
	return connect_session(allow_tokenless=True)

def open_browser():
    return tv.rest_app_run(BROWSER_APP_ID)


def close_browser():
    return tv.rest_app_close(BROWSER_APP_ID)


def is_browser_running():
    try:
        obj = tv.rest_app_status(BROWSER_APP_ID)
        if obj['visible'] and obj['running']:
            return 2
        if obj['running']:
            return 1
    except Exception:
        pass
    return 0


def is_tv_on():
    return ping(IP)

def power_on():
    if is_tv_on():
        try:
            connect_session(allow_tokenless=True)
        except Exception:
            pass
        return True

    for i in range(3):
        os.system(f'wakeonlan {MAC}')
        time.sleep(5)
        if is_tv_on():
            break

    if not is_tv_on():
        return False

    try:
        connect_session(allow_tokenless=True)
        return True
    except Exception:
        return False

def power_off():
    if not is_tv_on():
        print("TV appears to be already off.")
        return True

    # Up to 3 attempts:
    # 1) normal token connect/send
    # 2) on touchDisable: clear token + tokenless re-pair
    # 3) final retry
    for attempt in range(3):
        try:
            if tv is None:
                connect_session(allow_tokenless=True)
            return tv.send_key("KEY_POWER")
        except Exception as e:
            ev = _extract_event_name(e)
            print(f"power_off attempt {attempt + 1} failed: {e}")

            if ev == "ms.remote.touchDisable":
                print("TV rejected remote session (touchDisable). Trying re-pair...")
                clear_saved_token()
                try:
                    # tokenless connect should trigger TV prompt; accept it on TV
                    connect_session(allow_tokenless=True)
                    return tv.send_key("KEY_POWER")
                except Exception as e2:
                    print(f"Re-pair attempt failed: {e2}")
                    print("On TV, set Access Notification to 'First time only' (or Off),")
                    print("and clear old entries in Device List, then run again.")
                    return False

            time.sleep(1)
            try:
                refresh()
            except Exception:
                pass

    return False

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
    time.sleep(8 if ibr == 0 else 5)

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
    global tv
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
        except Exception:
            refresh()


def remote():
    from sshkeyboard import listen_keyboard

    print(f'Remote controller mode, available keys are {key_map}, press ESC to quit:')

    if not is_tv_on():
        print('TV is off, press P to power on')

    listen_keyboard(on_press=on_press)


def control(tv_number, command):
    global tv, ws
    select_tv(tv_number)

    if is_tv_on():
        try:
            connect_session(allow_tokenless=True)
        except Exception:
            tv = None
            ws = None

    if command is None:
        return remote()
    elif command == 'is_tv_on':
        return sys.exit(0 if is_tv_on() else 1)
    elif command == 'on':
        return power_on()
    elif command == 'off':
        return power_off()
    elif command == 'openBrowser':
        return open_browser()
    elif command == 'closeBrowser':
        return close_browser()
    elif command.startswith('KEY_'):
        if tv is None:
            refresh()
        return tv.send_key(command)
    elif command.startswith('http'):
        return open_url(command)
    else:
        print(f'Unknown command: {command}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        usage='$0 tv_number command 1>output 2>progress',
        description='Control Samsung TVs',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('tv_number', type=int, help='TV number, e.g., 1, 2, 3')
    parser.add_argument(
        'command',
        help='Command, e.g., on, off, openBrowser, closeBrowser, KEY_UP, KEY_VOLUP, KEY_MUTE, KEY_HOME, http://www.google.com.sg',
        nargs='?'
    )
    parser.add_argument(
        '--secret',
        default=DEFAULT_SECRET_FILE,
        help='Path to secret.json containing TV IP/MAC/TOKEN entries'
    )
    opt = parser.parse_args()

    SECRET_FILE = opt.secret
    TVS = load_secrets(SECRET_FILE)

    control(opt.tv_number - 1, opt.command)

