# AKORN Controller (Windows 11, Bluetooth LE)

Monitor and control a Char-Griller AKORN Auto Kamado from a Windows PC.
Features: BLE scan/connect, live grill + probe temps, set temperature, high/low and probe alerts
(sound + popup), BLE explorer, log export, demo mode.

## Get the installer
- **GitHub:** push this folder to a repo; Actions builds `AkornControllerSetup.exe` (Artifacts).
- **Locally on Windows:** install Python 3.11+ and Inno Setup 6, then run `./build.ps1`.
Run from source: `pip install bleak` then `python app.py`.

## One-time protocol setup (required for real grill data)
Char-Griller has not published the grill's Bluetooth protocol, so `%APPDATA%\AkornController\profile.json` holds it.
1. Close the phone app, power the grill, **Connect** tab > Scan > Connect.
2. **Explorer** tab: find the characteristic with `notify` and subscribe; watch the Log while the grill runs.
   Put its UUID in `notify_uuid` and map bytes to `fields` (offset/length/endian/scale).
3. To learn the "set temperature" command, enable Android *Bluetooth HCI snoop log*, change the setpoint in the
   official app, open the log in Wireshark, and copy the write (UUID + bytes). Put the UUID in `write_uuid` and the
   bytes in `set_temp.template` with `{temp}` where the temperature goes. Test it from the Explorer's *Write hex* first.
4. **Reload profile**. Until then, use **Demo mode** to try the UI and alerts.

Notes: the grill allows one BLE connection at a time. Writing wrong bytes to a grill controller is at your own risk;
test at a safe setpoint while supervising the grill. Don't rely on PC alerts as your only safety measure.
