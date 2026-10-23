# Calex Lights for Windows

A small app for controlling Calex smart lights from your Windows PC. It sits in the **system tray**:

- **Left-click the bulb icon** for a quick popup with an on/off switch and a brightness slider for every light, plus All on / All off.
- **"Open control panel"** (in the popup or the right-click menu) opens the full web panel at `http://localhost:8765`, with colour and warm/cool white controls.
- **Right-click the icon** for All on / All off, **Start with Windows** and Quit.

The Calex Smart app is a rebranded **Tuya** app. This app skips the phone app and the cloud and talks to the lights directly over your Wi‑Fi using [tinytuya](https://github.com/jasonacox/tinytuya). Your PC has to be on the same network as the lights.

## One-time setup

To talk to each light, the app needs that light's *local key*. Tuya only gives these out through its developer portal, so you do the following once:

1. **Get the app.** Either:
   - **Easy way:** download **`CalexLights.exe`** from this repo's **Releases** page (or from the latest *Build Windows exe* run under **Actions**). Python is built in, so there's nothing else to install. Put it in its own folder, because it saves `devices.json` next to itself.
   - **From source:** install Python from https://www.python.org/downloads/ (tick **"Add python.exe to PATH"**) and use `start.bat`.
2. **Move the lights to the Smart Life app.** Tuya's developer portal can only link *Smart Life* or *Tuya Smart* accounts, not Calex accounts. Install **Smart Life** on your phone, remove the lights from the Calex app, and add them in Smart Life. They're the same Tuya hardware, so they pair the normal way. From then on you use Smart Life on your phone.
3. **Get Tuya API keys.** It's free and takes about 10 minutes:
   1. Sign up at https://iot.tuya.com.
   2. Go to **Cloud → Development → Create Cloud Project**. Pick "Smart Home" and the **data centre for your region** (Central Europe for the UK/EU).
   3. When it asks about API services, make sure **IoT Core** and **Authorization** are added.
   4. Open the project's **Devices** tab, then **Link App Account → Add App Account**, and scan the QR code with Smart Life (Me tab → scan icon, top right).
   5. Copy the **Access ID** and **Access Secret** from the project's Overview tab.
4. **Double-click `CalexLights.exe`** (or `start.bat`). The first time, the tinytuya wizard runs and asks for:
   - **API Key / Secret:** the Access ID and Access Secret from step 3.
   - **Device ID:** the ID of any one of your lights (see the Devices tab on iot.tuya.com), or just type `scan`.
   - **Region:** `eu` for Europe (`us`, `cn` or `in` otherwise).
   - When it asks to poll the devices, either answer is fine.

   This writes a `devices.json` with your lights and their keys. Keep it private.

## Everyday use

Double-click **`CalexLights.exe`** (or `start.bat`). A bulb icon appears in the system tray; click it to control your lights. If you can't see it, click the **^** arrow in the taskbar and drag the icon out so it's always visible. Tick **Start with Windows** in the right-click menu so it's always there. Quit from the right-click menu.

Running the app again while it's already running just opens the control panel.

If you add a new light or re-pair an existing one, its key changes. Delete `devices.json` and start the app again to re-run the wizard.

## Building the exe

GitHub Actions (`.github/workflows/build-exe.yml`) builds `CalexLights.exe` with PyInstaller on every push. The exe is attached to the workflow run as an artifact. To publish a release, push a version tag:

```
git tag v1.0.0
git push origin v1.0.0
```

Windows SmartScreen may warn about an unsigned exe. Click **More info → Run anyway**.

## Troubleshooting

- **Something went wrong and nothing appears:** check `calex-lights.log` next to the exe.
- **A light shows "Offline":** check that the PC and the light are on the same network or router. If Windows Firewall asks, allow CalexLights (or Python) on **private networks**, because the app finds lights with UDP broadcasts (ports 6666, 6667 and 7000).
- **Wrong or old key errors:** the light was re-paired after you ran the wizard. Re-run the wizard as described above.
- **The Tuya developer trial "expired":** the cloud project is only needed to fetch keys. Local control keeps working after it expires.
