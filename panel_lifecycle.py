"""Keep the launch panel open and return focus after a tool has finished."""
import weakref

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QWidget

import common as C


class PanelLaunchSession(QObject):
    _TOOLS = {"TaskWidget", "ClipHistoryWindow", "NotesWindow", "TodoWindow",
              "CalendarWindow", "PomodoroWindow", "FileSearchWindow", "NetSpeedWidget",
              "LongShotBar", "RecBar", "GifBar", "LongEditor"}
    _BARS = {"LongShotBar", "RecBar", "GifBar"}

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._launches = []
        self._holds = {}
        self._windows = {}
        self._closing = set()
        self._finished = set()
        self._stopped = False
        self._return = QTimer(self)
        self._return.setSingleShot(True)
        self._return.timeout.connect(self._settle)
        app = QApplication.instance()
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.stop)

    @property
    def launching(self):
        return bool(self._launches)

    def launch(self, callback, group=None):
        group = group if group is not None else object()
        self._launches.append(group)
        try:
            return callback()
        finally:
            self._launches.pop()
            self._finished.add(group)
            self._request_return()

    def hold(self):
        if not self.launching:
            return None
        ticket = object()
        self._holds[ticket] = self._launches[-1]
        return ticket

    def release(self, ticket):
        if ticket in self._holds:
            self._finished.add(self._holds.pop(ticket))
            self._request_return()

    def group_for(self, window):
        entry = self._windows.get(id(window)) if window is not None else None
        return entry[1] if entry is not None else None

    def track(self, window, group=None):
        if window is None or window is self.panel:
            return
        if group is None:
            group = (self._launches[-1] if self._launches else
                     next(reversed(self._holds.values()), None))
        if group is None:
            return
        key = id(window)
        if key not in self._windows:
            reference = weakref.ref(window)
            window.destroyed.connect(lambda _obj=None, key=key, reference=reference:
                                     self._destroyed(key, reference))
        else:
            reference = self._windows[key][0]
        self._windows[key] = (reference, group)
        self._closing.discard(key)

    def _destroyed(self, key, reference):
        entry = self._windows.get(key)
        if entry is None or entry[0] is not reference:
            return
        self._finished.add(self._windows.pop(key)[1])
        self._closing.discard(key)
        self._request_return()

    def eventFilter(self, window, event):
        kind = event.type()
        if kind == QEvent.Show and (self.launching or self._holds):
            if (isinstance(window, QWidget) and window.isWindow()
                    and (isinstance(window, QDialog) or type(window).__name__ in self._TOOLS)):
                self.track(window)
        elif kind in (QEvent.Close, QEvent.Hide) and id(window) in self._windows:
            # Recording bars temporarily hide while reselecting a region.
            if kind == QEvent.Close or type(window).__name__ not in self._BARS:
                self._closing.add(id(window))
                self._request_return()
        return False

    def _request_return(self):
        if not self._stopped:
            self._return.start(0)

    def _settle(self):
        # Run after the close handler: an ignored close is still visible, and a
        # screenshot-to-recording or long-image-editor handoff has already run.
        for key, (reference, group) in list(self._windows.items()):
            window = reference()
            try:
                visible = window is not None and window.isVisible()
            except RuntimeError:
                visible = False
            if window is None or (key in self._closing and not visible):
                self._windows.pop(key, None)
                self._finished.add(group)
            self._closing.discard(key)
        busy = set(self._launches) | set(self._holds.values()) | {g for _r, g in self._windows.values()}
        ready = self._finished - busy
        if self._holds or not ready or self._stopped:
            return
        self._finished.difference_update(ready)
        if not self.panel.isVisible() or self.panel.isMinimized():
            return
        if QApplication.activeModalWidget() is not None:
            return
        self.panel.raise_()
        self.panel.activateWindow()
        C.force_foreground(self.panel)

    def stop(self):
        self._stopped = True
        self._return.stop()
        self._windows.clear()
        self._holds.clear()
        self._finished.clear()
