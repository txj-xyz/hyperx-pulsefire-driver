"""PyQt6 settings window for the Pulsefire Haste 2 Pro 4K."""
from __future__ import annotations

import copy
import html
import queue
import sys
import threading

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QRadioButton,
    QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from . import desktop
from . import protocol as p
from . import remap


class Worker(QObject):
    """Owns the Device on a background thread so a sleeping mouse never freezes the UI."""
    done = pyqtSignal(object, object, object)  # callback, result, error

    def __init__(self):
        super().__init__()
        self.jobs: queue.Queue = queue.Queue()
        self.dev: p.Device | None = None
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, fn, callback=None):
        self.jobs.put((fn, callback))

    def _loop(self):
        while True:
            fn, cb = self.jobs.get()
            try:
                if self.dev is None:
                    self.dev = p.Device()
                result, err = fn(self.dev), None
            except OSError as e:  # unplugged: reopen on the next job
                if self.dev:
                    self.dev.close()
                self.dev, result, err = None, None, p.DeviceError(f'device disconnected ({e})')
            except Exception as e:
                result, err = None, e
            self.done.emit(cb, result, err)


class ColorButton(QPushButton):
    changed = pyqtSignal()

    def __init__(self, rgb=(255, 0, 0)):
        super().__init__()
        self.setFixedWidth(56)
        self.clicked.connect(self._pick)
        self.set_rgb(rgb)

    def set_rgb(self, rgb):
        self.rgb = tuple(rgb)
        self.setStyleSheet(f'background-color: #{bytes(self.rgb).hex()}; border: 1px solid #555; '
                           'border-radius: 4px; min-height: 22px;')

    def _pick(self):
        c = QColorDialog.getColor(QColor(*self.rgb), self, 'Choose colour')
        if c.isValid():
            self.set_rgb((c.red(), c.green(), c.blue()))
            self.changed.emit()


# Choices in each binding's dropdown: (label, action). "keys" asks for a shortcut.
ACTIONS = [('Default', {'type': 'default'}), ('Disabled', {'type': 'disabled'})] + \
    [(label, {'type': 'button', 'button': b}) for b, label in remap.MOUSE_BUTTONS.items()] + \
    [('Scroll up', {'type': 'scroll', 'dir': 'up'}), ('Scroll down', {'type': 'scroll', 'dir': 'down'}),
     ('Keyboard shortcut…', {'type': 'keys'})]
# Keys the desktop usually swallows before a window sees them, offered from a menu instead.
SPECIAL_KEYS = ['KEY_PLAYPAUSE', 'KEY_NEXTSONG', 'KEY_PREVIOUSSONG', 'KEY_STOPCD', 'KEY_VOLUMEUP',
                'KEY_VOLUMEDOWN', 'KEY_MUTE', 'KEY_LEFTMETA', 'KEY_SYSRQ', 'KEY_ESC']
MODIFIERS = {'KEY_LEFTCTRL', 'KEY_RIGHTCTRL', 'KEY_LEFTSHIFT', 'KEY_RIGHTSHIFT', 'KEY_LEFTALT',
             'KEY_RIGHTALT', 'KEY_LEFTMETA', 'KEY_RIGHTMETA'}


class KeyCapture(QPushButton):
    """Click, then press a key combination. Records evdev key names (what uinput needs)."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.keys: list[str] = []
        self.held: list[str] = []
        self.capturing = False
        self.setMinimumWidth(170)
        self.clicked.connect(self._toggle)
        menu = QMenu(self)
        for k in SPECIAL_KEYS:
            menu.addAction(remap.key_label(k), lambda k=k: self._finish([k]))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: menu.exec(self.mapToGlobal(pos)))
        self.setToolTip('Click, then press a key or combination.\n'
                        'Right-click for media keys and keys the desktop intercepts.')
        self._show()

    def set_keys(self, keys):
        self.keys = list(keys)
        self._show()

    def _show(self):
        if self.capturing:
            self.setText('+'.join(remap.key_label(k) for k in self.held) + '…' if self.held else 'Press keys…')
        else:
            self.setText('+'.join(remap.key_label(k) for k in self.keys) or 'Click to set keys')

    def _toggle(self):
        if self.capturing:
            return self._finish(None)
        self.capturing, self.held = True, []
        self.grabKeyboard()
        self._show()

    def _finish(self, keys):
        if self.capturing:
            self.releaseKeyboard()
            self.capturing = False
        if keys:
            self.keys = keys
            self.changed.emit()
        self._show()

    def focusOutEvent(self, ev):
        self._finish(None)
        super().focusOutEvent(ev)

    @staticmethod
    def _name(ev) -> str | None:
        from evdev import ecodes
        # On Linux (X11 and Wayland) Qt's native scan code is the XKB keycode = evdev code + 8.
        name = ecodes.KEY.get(ev.nativeScanCode() - 8)
        return (name[0] if isinstance(name, list) else name) if name else None

    def keyPressEvent(self, ev):
        if not self.capturing:
            return super().keyPressEvent(ev)
        name = self._name(ev)
        if ev.isAutoRepeat() or name is None:
            return
        if name not in self.held:
            self.held.append(name)
        if name in MODIFIERS:
            self._show()
        else:
            self._finish(self.held)

    def keyReleaseEvent(self, ev):
        if not self.capturing:
            return super().keyReleaseEvent(ev)
        if not ev.isAutoRepeat() and self.held:  # modifiers only, e.g. a bare Shift
            self._finish(self.held)


class BindingsTab(QWidget):
    """Edits ~/.config/hyperx-haste2/bindings.json; the remap process picks changes up live."""

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        note = QLabel('Bind any mouse button or scroll direction to another button, a scroll, or a keyboard '
                      'shortcut. Changes apply immediately. Bindings are applied by a small background process '
                      '(the mouse\'s on-board remap protocol is unknown), which starts on its own and at login '
                      'while any binding is set. The DPI button is handled by the mouse and cannot be rebound.')
        note.setWordWrap(True)
        note.setStyleSheet('color: palette(placeholder-text);')
        lay.addWidget(note)

        box = QGroupBox('Bindings')
        g = QGridLayout(box)
        for col, text in enumerate(['Input', 'Action', 'Keys']):
            g.addWidget(QLabel(f'<b>{text}</b>'), 0, col)
        self.rows = {}
        for row, (src, label) in enumerate(remap.SOURCES.items(), 1):
            combo = QComboBox()
            for text, action in ACTIONS:
                combo.addItem(text, action)
            keys = KeyCapture()
            combo.activated.connect(lambda _, s=src: self._changed(s))
            keys.changed.connect(lambda s=src: self._changed(s))
            g.addWidget(QLabel(label), row, 0)
            g.addWidget(combo, row, 1)
            g.addWidget(keys, row, 2)
            self.rows[src] = (combo, keys)
        reset = QPushButton('Reset all to default')
        reset.clicked.connect(self._reset)
        g.addWidget(reset, len(self.rows) + 1, 2)
        g.setColumnStretch(2, 1)
        lay.addWidget(box)

        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self._load()
        self._sync_remapper()  # also refreshes the autostart command, in case the app moved
        try:
            import evdev  # noqa: F401
        except ImportError:
            box.setEnabled(False)
        self.timer = QTimer(self, interval=2000, timeout=self._update_status)
        self.timer.start()

    # --- config <-> widgets ---------------------------------------------------
    def _load(self):
        cfg = remap.load()
        for src, (combo, keys) in self.rows.items():
            action = cfg['bindings'].get(src, {'type': 'default'})
            idx = next((i for i, (_, a) in enumerate(ACTIONS)
                        if a['type'] == action.get('type') and all(action.get(k) == v for k, v in a.items())), 0)
            combo.setCurrentIndex(idx)
            keys.set_keys(action.get('keys', []))
            keys.setEnabled(ACTIONS[idx][1]['type'] == 'keys')
        self._update_status()

    def _action(self, src) -> dict:
        combo, keys = self.rows[src]
        action = dict(combo.currentData())
        if action['type'] == 'keys':
            action['keys'] = keys.keys
        return action

    def _changed(self, src):
        combo, keys = self.rows[src]
        is_keys = combo.currentData()['type'] == 'keys'
        keys.setEnabled(is_keys)
        if is_keys and not keys.keys:
            keys.click()  # go straight to "press keys"
            return  # saved once keys are captured
        if src == 'BTN_LEFT' and combo.currentData()['type'] != 'default' and QMessageBox.warning(
                self, 'Rebind left click?',
                'You are changing the left mouse button. Make sure you can still use the keyboard '
                '(Tab, arrow keys, Space) to set it back to Default.',
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            combo.setCurrentIndex(0)
            keys.setEnabled(False)
        cfg = remap.load()
        cfg['bindings'] = {s: self._action(s) for s in self.rows if self._action(s)['type'] != 'default'}
        remap.save(cfg)
        self._sync_remapper()

    def _reset(self):
        cfg = remap.load()
        cfg['bindings'] = {}
        remap.save(cfg)
        self._load()
        self._sync_remapper()

    def _sync_remapper(self):
        """Run the remapper (now and at login) exactly while some binding is set.
        With no bindings it exits by itself after seeing the config change."""
        on = bool(remap.load()['bindings'])
        try:
            remap.set_autostart(on)
        except OSError as e:
            self.status.setText(f'<span style="color:#e55">Could not change login autostart: {e}</span>')
            return
        if on and not remap.check():
            remap.start()
        QTimer.singleShot(700, self._update_status)

    def _update_status(self):
        n = len(remap.load()['bindings'])
        if not n:
            self.status.setText('No bindings set; every button does its default.')
        elif remap.is_running():
            self.status.setText(f'<span style="color:#4b4">● Active</span> · {n} binding{"s" * (n != 1)}.')
        elif problem := remap.check():
            self.status.setText(f'<span style="color:#e55">{problem}</span>')
        else:
            err = remap.last_error()
            self.status.setText(f'<span style="color:#e55">Remapper stopped: {html.escape(err)} (retrying)</span>'
                                if err else 'Starting…')
            remap.start()  # e.g. it crashed, or the mouse was plugged in later


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('HyperX Pulsefire Haste 2 Pro')
        self.table: p.DpiTable | None = None
        self.worker = Worker()
        self.worker.done.connect(self._finished)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        root = QWidget()
        lay = QVBoxLayout(root)
        tabs.addTab(root, 'Settings')
        tabs.addTab(BindingsTab(), 'Bindings')

        # --- status ------------------------------------------------------
        status = QGroupBox('Status')
        g = QGridLayout(status)
        self.device_label = QLabel('Connecting…')
        self.battery_bar = QProgressBar()
        self.battery_bar.setFormat('%p%')
        self.battery_detail = QLabel()
        g.addWidget(QLabel('Device'), 0, 0)
        g.addWidget(self.device_label, 0, 1)
        g.addWidget(QLabel('Battery'), 1, 0)
        g.addWidget(self.battery_bar, 1, 1)
        g.addWidget(self.battery_detail, 2, 1)
        lay.addWidget(status)

        # --- performance -------------------------------------------------
        perf = QGroupBox('DPI stages')
        g = QGridLayout(perf)
        for col, text in enumerate(['On', 'Active', 'DPI', 'Indicator']):
            g.addWidget(QLabel(f'<b>{text}</b>'), 0, col)
        self.active_group = QButtonGroup(self)
        self.stage_rows = []
        for i in range(5):
            on = QCheckBox(f'Stage {i + 1}')
            active = QRadioButton()
            spin = QSpinBox()
            spin.setRange(p.DPI_MIN, p.DPI_MAX)
            spin.setSingleStep(p.DPI_STEP)
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(lambda s=spin: s.setValue(round(s.value() / p.DPI_STEP) * p.DPI_STEP))
            color = ColorButton()
            self.active_group.addButton(active, i)
            on.toggled.connect(self._sync_enabled)
            for col, w in enumerate([on, active, spin, color]):
                g.addWidget(w, i + 1, col)
            self.stage_rows.append((on, active, spin, color))
        self.polling = QComboBox()
        for hz in sorted(p.POLLING_TO_CODE):
            self.polling.addItem(f'{hz} Hz', hz)
        g.addWidget(QLabel('Polling rate'), 6, 0, 1, 2)
        g.addWidget(self.polling, 6, 2, 1, 2)
        lay.addWidget(perf)

        # --- lighting ----------------------------------------------------
        light = QGroupBox('Lighting')
        h = QHBoxLayout(light)
        self.led_color = ColorButton((255, 0, 0))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness_label = QLabel()
        self.brightness.valueChanged.connect(lambda v: self.brightness_label.setText(f'{v}%'))
        set_light = QPushButton('Set lighting')
        set_light.clicked.connect(self._apply_lighting)
        h.addWidget(QLabel('Colour'))
        h.addWidget(self.led_color)
        h.addWidget(QLabel('Brightness'))
        h.addWidget(self.brightness, 1)
        h.addWidget(self.brightness_label)
        h.addWidget(set_light)
        lay.addWidget(light)

        # --- pointer acceleration (a desktop setting, not stored in the mouse) ---
        accel = QGroupBox('Pointer acceleration')
        g = QGridLayout(accel)
        self.accel_profile = QComboBox()
        self.accel_profile.addItem('Flat (no acceleration, 1:1 with DPI)', 'flat')
        self.accel_profile.addItem('Adaptive (desktop default)', 'adaptive')
        self.accel_speed = QSlider(Qt.Orientation.Horizontal)
        self.accel_speed.setRange(-100, 100)
        self.accel_speed_label = QLabel()
        self.accel_speed_label.setMinimumWidth(40)
        self.accel_speed.valueChanged.connect(lambda v: self.accel_speed_label.setText(f'{v / 100:+.2f}'))
        self.accel_note = QLabel()
        self.accel_note.setWordWrap(True)
        self.accel_note.setStyleSheet('color: palette(placeholder-text);')
        g.addWidget(QLabel('Profile'), 0, 0)
        g.addWidget(self.accel_profile, 0, 1, 1, 2)
        g.addWidget(QLabel('Speed'), 1, 0)
        g.addWidget(self.accel_speed, 1, 1)
        g.addWidget(self.accel_speed_label, 1, 2)
        g.addWidget(self.accel_note, 2, 0, 1, 3)
        lay.addWidget(accel)
        self.accel_group = accel
        self._load_accel()
        # Apply as soon as the user changes something, like the desktop's own mouse settings.
        self.accel_profile.activated.connect(self._apply_accel)
        self.accel_speed.sliderReleased.connect(self._apply_accel)
        self.accel_speed.actionTriggered.connect(
            lambda action: action != QSlider.SliderAction.SliderMove and QTimer.singleShot(0, self._apply_accel))

        # --- actions -----------------------------------------------------
        h = QHBoxLayout()
        self.message = QLabel()
        self.message.setWordWrap(True)
        reload_btn = QPushButton('Reload')
        reload_btn.clicked.connect(self.refresh)
        apply_btn = QPushButton('Apply')
        apply_btn.setToolTip('Apply DPI and polling now (lost when the mouse powers off)')
        apply_btn.clicked.connect(lambda: self._apply_dpi(onboard=False))
        save_btn = QPushButton('Save to mouse')
        save_btn.setToolTip('Apply and store DPI and polling in the mouse\'s onboard memory')
        save_btn.clicked.connect(lambda: self._apply_dpi(onboard=True))
        h.addWidget(self.message, 1)
        for b in (reload_btn, apply_btn, save_btn):
            h.addWidget(b)
        lay.addLayout(h)
        self.controls = [perf, light, apply_btn, save_btn]

        # Battery every 60 s; DPI button presses picked up every second.
        self.battery_timer = QTimer(self, interval=60_000, timeout=self._poll_battery)
        self.events_timer = QTimer(self, interval=1000, timeout=self._poll_events)
        self.refresh()

    # --- worker plumbing --------------------------------------------------
    def _finished(self, cb, result, err):
        if err is not None:
            self._error(err)
        elif cb is not None:
            cb(result)

    def _error(self, err):
        self.message.setText(f'<span style="color:#e55">{err}</span>')
        if self.table is None:
            for w in self.controls:
                w.setEnabled(False)
            QTimer.singleShot(3000, self.refresh)

    def _say(self, text):
        self.message.setText(text)

    # --- pointer acceleration ---------------------------------------------------
    def _load_accel(self):
        try:
            a = desktop.read()
        except (desktop.DesktopError, OSError) as e:
            self.accel_group.setEnabled(False)
            self.accel_note.setText(str(e))
            return
        self.accel_group.setEnabled(True)
        self.accel_profile.setCurrentIndex(self.accel_profile.findData(a.profile))
        self.accel_speed.setValue(round(a.speed * 100))
        self.accel_speed_label.setText(f'{a.speed:+.2f}')
        where = {'kde': 'KDE, for this mouse only', 'gnome': 'GNOME, for all mice'}[desktop.backend()]
        self.accel_note.setText(f'Applied by the desktop ({where}) and saved; changes take effect immediately.')

    def _apply_accel(self):
        a = desktop.Accel(self.accel_profile.currentData(), self.accel_speed.value() / 100)
        try:
            self._say(f'Pointer acceleration {desktop.apply(a)}.')
        except (desktop.DesktopError, OSError) as e:
            self._error(e)
            self._load_accel()

    # --- reads --------------------------------------------------------------
    def refresh(self):
        def read(dev):
            info = dev.info_raw()
            return info, dev.battery(), dev.dpi_table(), dev.brightness(), dev.node
        self.worker.submit(read, self._show_all)

    def _show_all(self, res):
        info, battery, table, brightness, node = res
        fw = int.from_bytes(info[43:45], 'little')
        self.device_label.setText(f'{node.mode.capitalize()} · {node.path} · firmware {fw >> 8:x}.{fw & 0xff:02x}')
        self._show_battery(battery)
        self._show_table(table)
        self.brightness.setValue(round(brightness * 100 / 255))
        for w in self.controls:
            w.setEnabled(True)
        # Cable mode is capped at 1000 Hz.
        for i in range(self.polling.count()):
            item = self.polling.model().item(i)
            item.setEnabled(node.mode == 'wireless' or self.polling.itemData(i) <= 1000)
        self._say('Loaded settings from the mouse.')
        self.battery_timer.start()
        self.events_timer.start()

    def _show_battery(self, b: p.Battery):
        self.battery_bar.setValue(b.percent)
        self.battery_detail.setText(f'{b.state_text} · {b.voltage_mv / 1000:.2f} V · {b.temperature_c} °C')

    def _show_table(self, t: p.DpiTable):
        self.table = t
        for (on, active, spin, color), stage, enabled in zip(self.stage_rows, t.stages, t.enabled):
            on.setChecked(enabled)
            spin.setValue(stage.dpi)
            color.set_rgb(stage.color)
        self.stage_rows[t.active][1].setChecked(True)
        self.polling.setCurrentIndex(self.polling.findData(t.polling_hz))
        self._sync_enabled()

    def _sync_enabled(self):
        checked = [row[0].isChecked() for row in self.stage_rows]
        for (on, active, spin, color), c in zip(self.stage_rows, checked):
            on.setEnabled(not c or sum(checked) > 1)  # at least one stage stays on
            active.setEnabled(c)
            spin.setEnabled(c)
            color.setEnabled(c)
        current = self.active_group.checkedId()
        if current < 0 or not checked[current]:
            self.stage_rows[checked.index(True)][1].setChecked(True)

    def _poll_battery(self):
        self.worker.submit(lambda dev: dev.battery(), self._show_battery)

    def _poll_events(self):
        def events(dev):
            dev.drain()
            ev, dev.dpi_stage_events[:] = list(dev.dpi_stage_events), []
            return ev
        self.worker.submit(events, self._stage_events)

    def _stage_events(self, events):
        if events and events[-1] < len(self.stage_rows):
            self.stage_rows[events[-1]][1].setChecked(True)
            if self.table:
                self.table.active = events[-1]

    # --- writes -------------------------------------------------------------
    def _apply_dpi(self, onboard: bool):
        if self.table is None:
            return
        t = copy.deepcopy(self.table)
        t.enabled_mask = sum(1 << i for i, row in enumerate(self.stage_rows) if row[0].isChecked())
        t.active = self.active_group.checkedId()
        t.polling_hz = self.polling.currentData()
        for stage, (on, active, spin, color) in zip(t.stages, self.stage_rows):
            stage.dpi = round(spin.value() / p.DPI_STEP) * p.DPI_STEP
            stage.color = color.rgb

        def write(dev):
            if onboard:
                dev.set_dpi_table(t, onboard=False)
            return dev.set_dpi_table(t, onboard=onboard)
        self._say('Writing…')
        self.worker.submit(write, lambda back: (
            self._show_table(back),
            self._say('Saved to the mouse.' if onboard else 'Applied (until the mouse powers off).')))

    def _apply_lighting(self):
        rgb, level = self.led_color.rgb, round(self.brightness.value() * 255 / 100)

        def write(dev):
            dev.set_color(rgb)
            dev.set_brightness(level)
        self.worker.submit(write, lambda _: self._say('Lighting set.'))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('HyperX Haste 2 Pro')
    app.setDesktopFileName('hyperx-haste2')  # Wayland: match the .desktop entry for the icon
    w = Window()
    w.resize(520, 0)
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
