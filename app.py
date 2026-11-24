"""AKORN Controller - Windows BLE client for the Char-Griller AKORN Auto Kamado.

Tabs: Dashboard (temps, set temperature, alerts) / Connect / Explorer / Log.
Grill-specific protocol details live in profile.json (see README).
"""
import asyncio, json, os, queue, random, shutil, sys, threading, time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    import winsound
except ImportError:  # non-Windows dev box
    winsound = None

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AkornController")
PROFILE_PATH = os.path.join(APP_DIR, "profile.json")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
os.makedirs(APP_DIR, exist_ok=True)


# ---------------------------------------------------------------- profile
def load_profile():
    if not os.path.exists(PROFILE_PATH):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.argv[0])))
        for cand in (os.path.join(os.path.dirname(sys.executable), "profile.default.json"),
                     os.path.join(base, "profile.default.json"),
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.default.json")):
            if os.path.exists(cand):
                shutil.copy(cand, PROFILE_PATH)
                break
        else:
            with open(PROFILE_PATH, "w") as f:
                json.dump({"name_contains": [], "notify_uuid": "", "write_uuid": "",
                           "fields": {}, "set_temp": {"template": ""}}, f, indent=2)
    with open(PROFILE_PATH) as f:
        return json.load(f)


def decode_field(data: bytes, spec):
    if not spec:
        return None
    o, n = spec.get("offset", 0), spec.get("length", 2)
    if len(data) < o + n:
        return None
    raw = int.from_bytes(data[o:o + n], spec.get("endian", "big"), signed=spec.get("signed", False))
    return raw * spec.get("scale", 1.0) + spec.get("add", 0.0)


def encode_set_temp(profile, temp):
    st = profile.get("set_temp") or {}
    tpl = (st.get("template") or "").replace(" ", "")
    if not tpl or "{temp}" not in tpl:
        return None
    raw = int(round((temp - st.get("add", 0.0)) / st.get("scale", 1.0)))
    enc = raw.to_bytes(st.get("length", 2), st.get("endian", "big"), signed=st.get("signed", False)).hex()
    return bytes.fromhex(tpl.replace("{temp}", enc))


# ---------------------------------------------------------------- BLE worker
class Ble:
    """Runs bleak on its own asyncio thread; reports to the UI via a queue."""

    def __init__(self, q):
        self.q, self.client = q, None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(self._done)

    def _done(self, fut):
        try:
            fut.result()
        except Exception as e:  # noqa
            self.q.put(("error", f"{type(e).__name__}: {e}"))

    async def scan(self, seconds=6):
        from bleak import BleakScanner
        found = await BleakScanner.discover(timeout=seconds, return_adv=True)
        self.q.put(("scan", [(d.name or adv.local_name or "(unnamed)", d.address, adv.rssi)
                             for d, adv in found.values()]))

    async def connect(self, address, notify_uuid):
        from bleak import BleakClient
        self.client = BleakClient(address, disconnected_callback=lambda c: self.q.put(("disconnected",)))
        await self.client.connect()
        rows = [(s.uuid, c.uuid, ",".join(c.properties)) for s in self.client.services for c in s.characteristics]
        self.q.put(("connected", address, rows))
        if notify_uuid:
            await self.subscribe(notify_uuid)

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()

    async def subscribe(self, uuid):
        await self.client.start_notify(uuid, lambda s, d: self.q.put(("notify", uuid, bytes(d))))
        self.q.put(("log", f"Subscribed to {uuid}"))

    async def read(self, uuid):
        self.q.put(("read", uuid, bytes(await self.client.read_gatt_char(uuid))))

    async def write(self, uuid, data):
        try:
            await self.client.write_gatt_char(uuid, data, response=True)
        except Exception:
            await self.client.write_gatt_char(uuid, data, response=False)
        self.q.put(("log", f"TX {uuid}: {data.hex(' ')}"))


# ---------------------------------------------------------------- alerts
class Alert:
    def __init__(self, key, label, cond, rearm, armed=True):
        self.key, self.label, self.cond, self.rearm, self.armed = key, label, cond, rearm, armed


# ---------------------------------------------------------------- UI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AKORN Controller")
        self.geometry("760x640")
        self.profile = load_profile()
        self.q = queue.Queue()
        self.ble = Ble(self.q)
        self.vals = {"grill_temp": None, "setpoint": None, "probe1": None, "probe2": None}
        self.demo = False
        self.demo_state = {"t": 70.0, "p1": 60.0, "p2": 55.0, "sp": 225.0}
        self.settings = self.load_settings()
        self.connected = False
        self.alerts = []
        self.build_ui()
        self.make_alerts()
        self.after(100, self.pump)
        self.after(1000, self.demo_tick)

    # ----- settings
    def load_settings(self):
        d = {"hi": 275, "lo": 200, "p1": 165, "p2": 165, "hi_on": 1, "lo_on": 0, "p1_on": 1, "p2_on": 0, "last_sp": 225}
        try:
            with open(SETTINGS_PATH) as f:
                d.update(json.load(f))
        except Exception:
            pass
        return d

    def save_settings(self):
        for k in ("hi", "lo", "p1", "p2"):
            try:
                self.settings[k] = float(self.av[k].get())
            except ValueError:
                pass
            self.settings[k + "_on"] = int(self.ae[k].get())
        with open(SETTINGS_PATH, "w") as f:
            json.dump(self.settings, f)

    # ----- layout
    def build_ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.t_dash, self.t_conn, self.t_exp, self.t_log = (ttk.Frame(nb, padding=10) for _ in range(4))
        for f, n in ((self.t_dash, "Dashboard"), (self.t_conn, "Connect"), (self.t_exp, "Explorer"), (self.t_log, "Log")):
            nb.add(f, text=n)
        self.status = tk.StringVar(value="Not connected")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x")
        self.build_dash(); self.build_conn(); self.build_explorer(); self.build_log()

    def build_dash(self):
        f = self.t_dash
        self.lbl = {}
        grid = ttk.Frame(f); grid.pack(fill="x")
        for i, (k, name) in enumerate((("grill_temp", "Grill"), ("setpoint", "Set to"), ("probe1", "Probe 1"), ("probe2", "Probe 2"))):
            box = ttk.LabelFrame(grid, text=name, padding=8); box.grid(row=0, column=i, padx=4, sticky="nsew"); grid.columnconfigure(i, weight=1)
            self.lbl[k] = tk.StringVar(value="--")
            ttk.Label(box, textvariable=self.lbl[k], font=("Segoe UI", 26, "bold")).pack()
        sp = ttk.LabelFrame(f, text="Set grill temperature (°F)", padding=8); sp.pack(fill="x", pady=10)
        self.sp_var = tk.StringVar(value=str(int(self.settings["last_sp"])))
        ttk.Spinbox(sp, from_=150, to=750, increment=5, textvariable=self.sp_var, width=8).pack(side="left")
        ttk.Button(sp, text="Send to grill", command=self.send_setpoint).pack(side="left", padx=8)
        al = ttk.LabelFrame(f, text="Alerts", padding=8); al.pack(fill="x")
        self.av, self.ae = {}, {}
        for r, (k, label) in enumerate((("hi", "Grill above (°F)"), ("lo", "Grill below (°F)"),
                                        ("p1", "Probe 1 reaches (°F)"), ("p2", "Probe 2 reaches (°F)"))):
            self.ae[k] = tk.IntVar(value=self.settings[k + "_on"])
            self.av[k] = tk.StringVar(value=str(int(self.settings[k])))
            ttk.Checkbutton(al, text=label, variable=self.ae[k], command=self.save_settings).grid(row=r, column=0, sticky="w", pady=2)
            e = ttk.Spinbox(al, from_=32, to=800, textvariable=self.av[k], width=8, command=self.save_settings)
            e.grid(row=r, column=1, padx=8); e.bind("<FocusOut>", lambda _e: self.save_settings())
        ttk.Button(al, text="Test alert", command=lambda: self.fire("Test alert")).grid(row=0, column=2, padx=12)

    def build_conn(self):
        f = self.t_conn
        bar = ttk.Frame(f); bar.pack(fill="x")
        ttk.Button(bar, text="Scan", command=lambda: (self.status.set("Scanning…"), self.ble.run(self.ble.scan()))).pack(side="left")
        ttk.Button(bar, text="Connect", command=self.connect_sel).pack(side="left", padx=6)
        ttk.Button(bar, text="Disconnect", command=lambda: self.ble.run(self.ble.disconnect())).pack(side="left")
        ttk.Button(bar, text="Demo mode", command=self.toggle_demo).pack(side="left", padx=6)
        ttk.Button(bar, text="Edit profile.json", command=lambda: os.startfile(PROFILE_PATH) if hasattr(os, "startfile") else None).pack(side="right")
        ttk.Button(bar, text="Reload profile", command=self.reload_profile).pack(side="right", padx=6)
        self.devs = ttk.Treeview(f, columns=("name", "addr", "rssi"), show="headings", height=14)
        for c, w in (("name", 260), ("addr", 200), ("rssi", 60)):
            self.devs.heading(c, text=c.title()); self.devs.column(c, width=w)
        self.devs.pack(fill="both", expand=True, pady=8)
        ttk.Label(f, text="Close the Char-Griller phone app first: the grill accepts one Bluetooth connection at a time.").pack(anchor="w")

    def build_explorer(self):
        f = self.t_exp
        self.chars = ttk.Treeview(f, columns=("svc", "char", "props"), show="headings", height=10)
        for c, w in (("svc", 250), ("char", 250), ("props", 200)):
            self.chars.heading(c, text=c); self.chars.column(c, width=w)
        self.chars.pack(fill="both", expand=True)
        bar = ttk.Frame(f); bar.pack(fill="x", pady=6)
        ttk.Button(bar, text="Read", command=lambda: self.char_op("read")).pack(side="left")
        ttk.Button(bar, text="Subscribe", command=lambda: self.char_op("sub")).pack(side="left", padx=6)
        self.hex_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.hex_var, width=36).pack(side="left", padx=6)
        ttk.Button(bar, text="Write hex", command=lambda: self.char_op("write")).pack(side="left")
        ttk.Label(f, text="Use this to find the characteristics the grill uses, then record them in profile.json.").pack(anchor="w")

    def build_log(self):
        self.log_box = tk.Text(self.t_log, height=20, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True)
        ttk.Button(self.t_log, text="Save log…", command=self.save_log).pack(anchor="e", pady=4)

    # ----- helpers
    def log(self, msg):
        self.log_box.config(state="normal")
        self.log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.log_box.see("end"); self.log_box.config(state="disabled")

    def save_log(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt")
        if p:
            with open(p, "w") as f:
                f.write(self.log_box.get("1.0", "end"))

    def reload_profile(self):
        self.profile = load_profile(); self.log("Profile reloaded")

    def sel_char(self):
        s = self.chars.selection()
        return self.chars.item(s[0])["values"][1] if s else None

    def char_op(self, op):
        if not self.connected or self.demo:
            return messagebox.showinfo("Not connected", "Connect to a real device first.")
        u = self.sel_char()
        if not u:
            return
        if op == "read": self.ble.run(self.ble.read(u))
        elif op == "sub": self.ble.run(self.ble.subscribe(u))
        else:
            try: data = bytes.fromhex(self.hex_var.get().replace(" ", ""))
            except ValueError: return messagebox.showerror("Hex", "Invalid hex")
            self.ble.run(self.ble.write(u, data))

    def connect_sel(self):
        s = self.devs.selection()
        if not s:
            return
        addr = self.devs.item(s[0])["values"][1]
        self.demo = False
        self.status.set(f"Connecting to {addr}…")
        self.ble.run(self.ble.connect(addr, self.profile.get("notify_uuid") or None))

    # ----- set temperature
    def send_setpoint(self):
        try:
            t = float(self.sp_var.get())
        except ValueError:
            return
        self.settings["last_sp"] = t; self.save_settings()
        if self.demo:
            self.demo_state["sp"] = t; self.vals["setpoint"] = t; self.refresh(); return
        if not self.connected:
            return messagebox.showwarning("Not connected", "Connect to the grill first.")
        data = encode_set_temp(self.profile, t)
        wu = self.profile.get("write_uuid")
        if not data or not wu:
            return messagebox.showwarning("Profile incomplete",
                "Set write_uuid and set_temp.template in profile.json (see README) to enable setting the temperature.")
        self.ble.run(self.ble.write(wu, data))

    # ----- alerts
    def make_alerts(self):
        g = lambda k: self.vals.get(k)
        s = lambda k: float(self.settings[k])
        self.alerts = [
            Alert("hi", "Grill temperature is HIGH", lambda: g("grill_temp") is not None and g("grill_temp") > s("hi"),
                  lambda: g("grill_temp") is None or g("grill_temp") < s("hi") - 5),
            Alert("lo", "Grill temperature is LOW", lambda: g("grill_temp") is not None and g("grill_temp") < s("lo"),
                  lambda: g("grill_temp") is not None and g("grill_temp") > s("lo") + 5, armed=False),
            Alert("p1", "Probe 1 reached target", lambda: g("probe1") is not None and g("probe1") >= s("p1"),
                  lambda: g("probe1") is None or g("probe1") < s("p1") - 5),
            Alert("p2", "Probe 2 reached target", lambda: g("probe2") is not None and g("probe2") >= s("p2"),
                  lambda: g("probe2") is None or g("probe2") < s("p2") - 5),
        ]

    def check_alerts(self):
        for a in self.alerts:
            on = bool(self.ae[a.key].get())
            if not a.armed and a.rearm():
                a.armed = True
            if on and a.armed and a.cond():
                a.armed = False
                self.fire(a.label)

    def fire(self, msg):
        self.log(f"ALERT: {msg}")
        if winsound:
            threading.Thread(target=lambda: [winsound.Beep(1200, 250) or time.sleep(0.1) for _ in range(5)], daemon=True).start()
        self.deiconify(); self.lift()
        messagebox.showwarning("AKORN alert", msg)

    # ----- data in
    def ingest(self, data: bytes):
        for k, spec in (self.profile.get("fields") or {}).items():
            v = decode_field(data, spec)
            if v is not None and k in self.vals:
                self.vals[k] = v
        self.refresh()

    def refresh(self):
        for k, v in self.vals.items():
            self.lbl[k].set("--" if v is None else f"{v:.0f}°")
        self.check_alerts()

    def pump(self):
        try:
            while True:
                m = self.q.get_nowait()
                t = m[0]
                if t == "scan":
                    self.devs.delete(*self.devs.get_children())
                    keys = [k.lower() for k in self.profile.get("name_contains", [])]
                    rows = sorted(m[1], key=lambda r: (not any(k in r[0].lower() for k in keys), -r[2]))
                    for r in rows: self.devs.insert("", "end", values=r)
                    self.status.set(f"Scan done: {len(rows)} devices")
                elif t == "connected":
                    self.connected = True; self.status.set(f"Connected: {m[1]}")
                    self.chars.delete(*self.chars.get_children())
                    for r in m[2]: self.chars.insert("", "end", values=r)
                    self.log(f"Connected to {m[1]}; {len(m[2])} characteristics")
                elif t == "disconnected":
                    self.connected = False; self.status.set("Disconnected"); self.log("Disconnected")
                elif t == "notify":
                    self.log(f"RX {m[1]}: {m[2].hex(' ')}")
                    if m[1].lower() == (self.profile.get("notify_uuid") or "").lower():
                        self.ingest(m[2])
                elif t == "read": self.log(f"READ {m[1]}: {m[2].hex(' ')}")
                elif t == "log": self.log(m[1])
                elif t == "error": self.log(f"ERROR {m[1]}"); self.status.set("Error: " + m[1][:80])
        except queue.Empty:
            pass
        self.after(100, self.pump)

    # ----- demo
    def toggle_demo(self):
        self.demo = not self.demo
        self.status.set("Demo mode ON (simulated grill)" if self.demo else "Demo mode off")
        self.vals = {k: None for k in self.vals}
        if self.demo:
            self.demo_state.update(t=70.0, p1=60.0, p2=55.0, sp=float(self.sp_var.get() or 225))
        self.refresh()

    def demo_tick(self):
        if self.demo:
            d = self.demo_state
            d["t"] += (d["sp"] - d["t"]) * 0.03 + random.uniform(-0.8, 0.8)
            d["p1"] += random.uniform(0.2, 1.4); d["p2"] += random.uniform(0.1, 0.8)
            self.vals.update(grill_temp=d["t"], setpoint=d["sp"], probe1=d["p1"], probe2=d["p2"])
            self.refresh()
        self.after(1000, self.demo_tick)


if __name__ == "__main__":
    App().mainloop()
