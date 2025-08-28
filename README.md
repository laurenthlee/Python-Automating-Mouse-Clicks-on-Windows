### Python Automating Mouse Clicks on Windows

A clean, cross-platform auto-clicker with a simple UI, smart targeting modes, handy keyboard shortcuts, and a tiny HUD for live coordinates.

<img width="754" height="586" alt="image" src="https://github.com/user-attachments/assets/1499687c-3dc3-4b22-818e-1206f5061e63" />

<sub>Replace the path above with your screenshot (e.g., `docs/ui.png`).</sub>

---

## UI Tour

### Toolbar
Start / Stop / Pick position + toggles for **Show HUD**, **Always on top**, **Dark mode**.

### Timing & Click (left panel)
- **Interval (ms)** with quick presets and live **CPS** (clicks/sec).
- **Click type:** Left, Right, or **Double (left)**.
- **Number of clicks** or **Continuous** mode.
- Optional **3-second countdown** before starting.

### Target (right panel)
- **Follow cursor** *(default)*.
- **Fixed point** with **X/Y** boxes *(Pick current position or press **F8**)*.
- **Screen center** *(current screen)*.
- **Active window center** *(Windows only)*.

### Control Row
- Big **Start (F5)** and **Stop (F6)** buttons.

### Live Status
- Status line and **Clicks performed** counter.

### Status Bar
- Quick tips: **F5 / F6 / F8 / ESC**.

### Live Coordinates HUD (optional)
A tiny bubble near your cursor showing global **X, Y**. Great for picking a fixed point.

---

## Highlights
- Clean, two-column layout with toolbar & menu.
- Interval presets and **live CPS** readout.
- **Dark / Light** theme toggle *(remembered between runs)*.
- **Always on top** toggle.
- Four targeting modes: follow cursor, fixed point, screen center, active window center *(Windows)*.
- HUD overlay with live coordinates (**F8** to pick) that floats near your cursor.
- Global **panic stop** via **ESC** while running.
- **Start (F5) / Stop (F6) / Pick (F8)** shortcuts.
- Settings are persisted with **QSettings** (window size, theme, last options, target, etc.).

---

## Shortcuts

| Shortcut | Action |
|---|---|
| **F5** | Start clicking |
| **F6** | Stop clicking |
| **F8** | Pick current mouse position → fills Fixed X/Y and selects “Fixed point” |
| **ESC** | Panic-stop while running *(global)* |
| **Alt + F → X** | Exit *(standard menu mnemonic)* |

---

## Requirements

- **Python:** 3.9+ recommended  
- **OS:**  
  - **Windows 10/11** → fully supported *(includes **Active window center**)*.  
  - **macOS** → works, but you must grant **Accessibility** permission *(see FAQ)*.  
  - **Linux** → works best on **X11**. Some **Wayland** sessions restrict global input.
- **Dependencies:**  
  - `PySide6`  
  - `pynput`

---

## Install

```bash
# create & activate a venv (recommended)
python -m venv .venv
```

# Windows:
```
.venv\Scripts\activate
```
# macOS/Linux:
source .venv/bin/activate
```
pip install --upgrade pip
pip install PySide6 pynput
```


Run
```
python main.py
```
Build a Single-File App (Windows)
```
pip install pyinstaller
pyinstaller -F -w main.py -n AutoClicker
```

## FAQ
- **Q: Nothing happens / it won’t click inside some apps.**
Some apps (browsers with special permissions, UWP windows, elevated apps) block synthetic input. On Windows, try running as Administrator if you need to click into elevated windows. Some protected apps (and most games with anti-cheat) will still block this.
- **Q: Will this work in games?**
Often no. Many games/anti-cheat systems block or penalize synthetic input. Use responsibly and at your own risk.
- **Q: On macOS I see “not permitted to control your computer.”**
Grant System Settings → Privacy & Security → Accessibility permission for your Python or built app. Restart the app after granting.
- **Q: On Linux Wayland, the cursor/HUD works but clicks don’t send.**
Wayland frequently restricts global input. Use an X11 session or a compositor/portal that permits synthetic events.
- **Q: HiDPI coordinates look off on Windows.**
This app calls SetProcessDPIAware best-effort, but mixed-DPI/multi-monitor setups can still be tricky. If a fixed point seems offset, pick it on the same monitor you’ll use, or try the Active window center / Screen center targeting modes.
- **Q: How do I stop it instantly?**
Press ESC (global panic stop) or click Stop (F6).
- **Q: What does “Continuous” do?**
Ignores the click count and keeps going until you press Stop or ESC.
- **Q: Where are settings stored?**
**Using Qt’s QSettings:**
```
Windows: Registry under HKEY_CURRENT_USER\Software\ChatGPT\AutoClicker
macOS: ~/Library/Preferences/...
Linux: usually ~/.config/ChatGPT/AutoClicker.conf
```

- **Q: Can it target the center of my current display or window automatically?**
Yes — choose Screen center (current screen) or Active window center (Windows).
- **Q: The toolbar toggles threw a “setChecked() takes exactly one argument (0 given)” error.**
Fixed by using QAction.toggled(bool) instead of triggered when wiring checkable actions.

## Notes
- **“Double (left)” counts as two physical clicks toward your total.**
- **On Windows, Active window center snaps to the current foreground window.**
- **Settings Persistence**
- **Stored via Qt QSettings; typical keys include window geometry, theme, last-used options, target mode, and coordinates. Paths by platform are listed in the FAQ above.**
