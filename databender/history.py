"""Edit-history side panel with compact and full views."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget

from . import effects
from .effects import SR


def _fmt_value(p: effects.Param, v) -> str:
    if p.choices:
        return p.choices[int(v)]
    return f"{int(v)}{p.suffix}" if p.integer else f"{v:.10g}{p.suffix}"


def describe_params(info: dict) -> str:
    params = info.get("params") or {}
    try:
        spec = {p.key: p for p in effects.by_name(info["name"]).params}
    except StopIteration:
        spec = {}
    return ", ".join(f"{spec[k].label}: {_fmt_value(spec[k], v)}" if k in spec else f"{k}: {v}" for k, v in params.items())


def describe_place(info: dict) -> str:
    sel = info.get("sel")
    if not sel:
        return "Whole image"
    if len(sel) == 4:  # rectangle (free selection)
        x0, y0, x1, y1 = sel
        return f"Rectangle x {x0}–{x1}, y {y0}–{y1}  ({x1 - x0}×{y1 - y0} px)"
    a, b = sel
    return f"{a / SR:.3f}–{b / SR:.3f} s  ({a:,}–{b:,})"


class HistoryPanel(QWidget):
    jump = pyqtSignal(int)  # id of the history state the user clicked
    viewChanged = pyqtSignal(int)  # 0 = compact, 1 = full
    tweakRequested = pyqtSignal(int)  # id of the step whose settings should be adjusted

    def __init__(self):
        super().__init__()
        self.entries: list[dict] = []
        self.view = QComboBox(); self.view.addItems(["Compact", "Full"])
        self.view.currentIndexChanged.connect(lambda i: (self._rebuild(), self.viewChanged.emit(i)))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setUniformItemSizes(False)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemClicked.connect(lambda it: self.jump.emit(it.data(Qt.ItemDataRole.UserRole)))
        top = QHBoxLayout(); top.addWidget(QLabel("<b>History</b>")); top.addStretch(); top.addWidget(self.view)
        lay = QVBoxLayout(self); lay.addLayout(top); lay.addWidget(self.list, 1)
        hint = QLabel("Click any step to jump to it. Grey = other branch; nothing is discarded."); hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        lay.addWidget(hint)

    def _context_menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        nid = it.data(Qt.ItemDataRole.UserRole)
        info = next(e["info"] for e in self.entries if e["id"] == nid)
        menu = QMenu(self)
        menu.addAction("Go to this step", lambda: self.jump.emit(nid))
        tweak = menu.addAction("Tweak settings…", lambda: self.tweakRequested.emit(nid))
        tweakable = bool(info.get("params")) and any(e.name == info["name"] for e in effects.EFFECTS)
        tweak.setEnabled(tweakable)
        if not tweakable:
            tweak.setToolTip("This step has no adjustable settings")
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def set_history(self, entries: list[dict]):
        self.entries = entries
        self._rebuild()

    def _text(self, e: dict) -> str:
        info, i = e["info"], e["id"]
        name = info["name"]
        if i == 0:
            return name
        title = f"{i}. {'🎲 ' if info.get('random') else ''}{name}"
        if self.view.currentIndex() == 0:
            return title
        lines = [title]
        if info.get("params"):
            lines.append(describe_params(info))
        if "sel" in info:
            lines.append(describe_place(info) + ("  ·  planar" if info.get("mode") == 1 else ""))
        if info.get("tweak_of") is not None:
            lines.append(f"↳ tweak of step {info['tweak_of']}")
        elif e["parent"] is not None and e["parent"] != i - 1:
            lines.append(f"↳ branched from step {e['parent']}")
        return "\n".join(lines)

    def _rebuild(self):
        self.list.clear()
        cur_row = 0
        for row, e in enumerate(self.entries):
            it = QListWidgetItem(self._text(e))
            it.setData(Qt.ItemDataRole.UserRole, e["id"])
            if e["rel"] == 2:  # on a side branch: reachable, but not part of the current line of history
                it.setForeground(QBrush(QColor("#808080")))
            if e["rel"] == 0:
                f = QFont(it.font()); f.setBold(True); it.setFont(f); cur_row = row
            self.list.addItem(it)
        self.list.setCurrentRow(cur_row)
        self.list.scrollToItem(self.list.item(cur_row))
