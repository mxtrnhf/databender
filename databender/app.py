"""Main window for Databender."""
from __future__ import annotations

import sys

import numpy as np
from pathlib import Path

from PyQt6.QtCore import QLoggingCategory, QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

from . import effects
from .helptext import help_html, reference_html
from .core import INTERLEAVED, MODE_NAMES, Document, Rect, norm_sel
from .effects import SR, Effect, Param
from .history import HistoryPanel
from .views import ImageView, WaveformView

OPEN_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif *.ppm);;All files (*)"
SAVE_FILTER = "PNG (*.png);;JPEG (*.jpg *.jpeg);;BMP (*.bmp);;TIFF (*.tif *.tiff);;WebP (*.webp)"
CATEGORY_ORDER = ["Volume", "EQ & Filters", "Delay & Reverb", "Modulation", "Distortion", "Spectral",
                  "Time & Glitch", "Bits & Bytes", "Tape & Analog", "Special"]


class InfoDialog(QDialog):
    """Short scrollable help page (HTML), with clickable links."""

    def __init__(self, title: str, html: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.resize(600, 640)
        view = QTextBrowser(); view.setOpenExternalLinks(True); view.setHtml(html)
        lay = QVBoxLayout(self); lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); bb.rejected.connect(self.close); lay.addWidget(bb)


class ParamRow(QWidget):
    """Slider + spinbox pair (or a combo) for one effect parameter."""

    def __init__(self, p: Param, value: float, on_change):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.p = p
        if p.choices:
            self.w = QComboBox(); self.w.addItems(p.choices); self.w.setCurrentIndex(int(value))
            self.w.currentIndexChanged.connect(lambda _: on_change())
            lay.addWidget(self.w); return
        self.w = QSpinBox() if p.integer else QDoubleSpinBox()
        self.w.setRange(p.min, p.max); self.w.setSuffix(p.suffix)
        if not p.integer:
            self.w.setSingleStep(p.step); self.w.setDecimals(effects.DECIMALS)
        else:
            self.w.setSingleStep(int(p.step))
        self.w.setValue(value); self.w.setMinimumWidth(110)
        self.s = QSlider(Qt.Orientation.Horizontal); self.s.setRange(0, 1000)
        self.s.setMinimumWidth(160)
        self._sync_slider()
        self.s.valueChanged.connect(self._from_slider)
        self.w.valueChanged.connect(lambda _: (self._sync_slider(), on_change()))
        lay.addWidget(self.s, 1); lay.addWidget(self.w)

    def _sync_slider(self):
        span = self.p.max - self.p.min
        self.s.blockSignals(True)
        self.s.setValue(int((self.w.value() - self.p.min) / span * 1000) if span else 0)
        self.s.blockSignals(False)

    def _from_slider(self, v):
        val = self.p.min + v / 1000 * (self.p.max - self.p.min)
        self.w.setValue(int(round(val)) if self.p.integer else val)

    def value(self):
        return self.w.currentIndex() if self.p.choices else self.w.value()

    def set_value(self, v):
        if self.p.choices:
            self.w.setCurrentIndex(int(v))
        else:
            self.w.setValue(int(round(v)) if self.p.integer else float(v))


class EffectDialog(QDialog):
    def __init__(self, eff: Effect, last: dict, preview_cb, parent=None):
        super().__init__(parent)
        self.eff, self.preview_cb = eff, preview_cb
        self._applying = False
        self.setWindowTitle(eff.name)
        self.rows: dict[str, ParamRow] = {}
        form = QFormLayout()
        if not eff.params:
            note = QLabel("This effect has no settings. The picture shows the result; press OK to apply it.")
            note.setWordWrap(True); note.setStyleSheet("color: gray"); form.addRow(note)
        group = ""
        for p in eff.params:
            if p.group and p.group != group:  # section heading
                group = p.group
                head = QLabel(f"<b>{group}</b>"); head.setContentsMargins(0, 8, 0, 0)
                form.addRow(head)
            row = ParamRow(p, last.get(p.key, p.default), self._changed)
            self.rows[p.key] = row; form.addRow(p.label, row)
            if p.help:  # hover for a quick reminder of what this setting does
                row.setToolTip(p.help); form.labelForField(row).setToolTip(p.help)
        self.live = QCheckBox("Live preview"); self.live.setChecked(True)
        self.live.toggled.connect(self._changed)
        self.busy = QLabel(""); self.busy.setStyleSheet("color: gray")
        st = QSettings("databender", "databender")
        self.small = QCheckBox("Small-scale preview of"); self.small.setChecked(str(st.value("small_preview", "0")) in ("1", "true"))
        self.small.setToolTip("Live preview computes only a stretch of full-resolution rows, centred in the selection (or the whole image).\n"
                              "The rest stays untouched while you tweak. OK still applies the effect to everything.")
        self.small_pct = QSpinBox(); self.small_pct.setRange(1, 100); self.small_pct.setSuffix(" %"); self.small_pct.setValue(int(st.value("small_pct", 20)))
        self.small_pct.setEnabled(self.small.isChecked())
        self.small.toggled.connect(lambda on: (self.small_pct.setEnabled(on), self._save_small(), self._changed()))
        self.small_pct.valueChanged.connect(lambda _: (self._save_small(), self._changed()))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        self.preset_cb = None
        if eff.presets or eff.info or eff.summary:
            pl = QHBoxLayout()
            if eff.presets:
                self.preset_cb = QComboBox(); self.preset_cb.addItems(["(custom)"] + list(eff.presets))
                self.preset_cb.currentIndexChanged.connect(self._apply_preset)
                pl.addWidget(QLabel("Preset:")); pl.addWidget(self.preset_cb, 1)
            else:
                pl.addStretch(1)
            if eff.info or eff.summary:
                ib = QPushButton("ⓘ About this effect"); ib.setAutoDefault(False)
                ib.clicked.connect(lambda: InfoDialog(eff.name, help_html(eff), self).show())
                pl.addWidget(ib)
            lay.addLayout(pl)
        if len(eff.params) > 10:  # long forms scroll instead of growing past the screen
            body = QWidget(); body.setLayout(form)
            area = QScrollArea(); area.setWidget(body); area.setWidgetResizable(True); area.setFrameShape(QScrollArea.Shape.NoFrame)
            lay.addWidget(area, 1)
            self.resize(660, 720)
        else:
            lay.addLayout(form)
        live_row = QHBoxLayout(); live_row.addWidget(self.live); live_row.addWidget(self.busy, 1)
        small_row = QHBoxLayout(); small_row.addWidget(self.small); small_row.addWidget(self.small_pct)
        small_row.addWidget(QLabel("of the selection / image")); small_row.addStretch(1)
        lay.addLayout(live_row); lay.addLayout(small_row); lay.addWidget(bb)
        self.timer = QTimer(self, singleShot=True, interval=120)
        self.timer.timeout.connect(self._preview)
        self._changed()

    def _apply_preset(self, i):
        if i == 0:
            return
        vals = {**{p.key: p.default for p in self.eff.params}, **self.eff.presets[self.preset_cb.itemText(i)]}
        self._applying = True
        try:
            for k, r in self.rows.items():
                r.set_value(vals[k])
        finally:
            self._applying = False
        self.timer.start()  # not _changed(): that would flip the preset box back to "(custom)"

    def _save_small(self):
        st = QSettings("databender", "databender")
        st.setValue("small_preview", int(self.small.isChecked())); st.setValue("small_pct", self.small_pct.value())

    def small_percent(self) -> int | None:
        """Share of the target to preview, or None for a full preview."""
        return self.small_pct.value() if self.small.isChecked() and self.small_pct.value() < 100 else None

    def set_busy(self, busy: bool):
        self.busy.setText("⏳ computing preview…" if busy else "")

    def values(self) -> dict:
        out = {}
        for k, r in self.rows.items():
            v = r.value()
            out[k] = int(v) if r.p.integer or r.p.choices else float(v)
        return out

    def _changed(self, *_):
        if self.preset_cb is not None and not self._applying and self.preset_cb.currentIndex() != 0:
            self.preset_cb.blockSignals(True); self.preset_cb.setCurrentIndex(0); self.preset_cb.blockSignals(False)
        self.timer.start()

    def _preview(self):
        self.preview_cb(self.values() if self.live.isChecked() else None)


class _PreviewSignals(QObject):
    done = pyqtSignal(object, object)  # request key, resulting pixels (None if the effect failed)


class _PreviewTask(QRunnable):
    """Computes one effect preview off the UI thread, on a private copy of the image."""

    def __init__(self, doc, eff, params, sel, base, mode, key, signals, keep_from=None, keep_rows=0):
        super().__init__()
        self.args = (doc, eff, params, sel, base, mode, keep_from, keep_rows); self.key, self.signals = key, signals

    def run(self):
        doc, eff, params, sel, base, mode, keep_from, keep_rows = self.args
        try:
            px = doc.compute(eff, params, sel, base=base, mode=mode, keep_from=keep_from, keep_rows=keep_rows)
        except Exception:  # noqa: BLE001
            px = None
        self.signals.done.emit(self.key, px)


class MainWindow(QMainWindow):
    selChanged = pyqtSignal(object)  # emitted whenever the selection changes, from either view

    def __init__(self):
        super().__init__()
        self.doc = Document()
        self.sel = None  # None, a (start, end) run of samples, or a Rect (free selection)
        self.free_mode = False
        self.last_effect: tuple[Effect, dict] | None = None
        self.last_params: dict[str, dict] = {}
        self.settings = QSettings("databender", "databender")
        self.resize(1440, 840)
        self.setAcceptDrops(True)

        self._pv_pool = QThreadPool(self); self._pv_pool.setMaxThreadCount(1)  # one preview at a time
        self._pv_signals = _PreviewSignals(); self._pv_signals.done.connect(self._on_preview_done)
        self._pv_busy = False; self._pv_latest = None; self._pv_cache = None
        self.image_view = ImageView()
        self.wave = WaveformView()
        self.wave.selectionChanged.connect(self._on_selection)
        self.image_view.rangeSelected.connect(self._on_image_selection)
        self.image_view.rectSelected.connect(lambda r: self._set_selection(r))
        img_panel = QWidget(); il = QVBoxLayout(img_panel); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(0)
        il.addWidget(self._build_view_strip()); il.addWidget(self.image_view, 1)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(img_panel); left.addWidget(self.wave)
        left.setStretchFactor(0, 3); left.setStretchFactor(1, 1)
        left.setSizes([620, 170])  # starting split: waveform strip is short; drag the handle for more

        self.search = QLineEdit(placeholderText="Search effects…", clearButtonEnabled=True)
        self.search.textChanged.connect(self._filter_effects)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._tree_activate)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):  # not itemActivated: it also fires on double-click and on KDE single clicks
            sc = QShortcut(QKeySequence(key), self.tree); sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(lambda: self._tree_activate(self.tree.currentItem()))
        self._build_tree()
        apply_btn = self.apply_btn = QPushButton("Apply…"); apply_btn.clicked.connect(lambda: self._tree_activate(self.tree.currentItem()))
        again_btn = self.again_btn = QPushButton("Repeat last"); again_btn.clicked.connect(self.repeat_last)
        right = QWidget(); rl = QVBoxLayout(right)
        self.desc = QLabel("Select an effect to see what it does. Double-click to apply. Right-click for more help.")
        self.desc.setWordWrap(True); self.desc.setMinimumHeight(86); self.desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.desc.setStyleSheet("color: gray")
        self.tree.currentItemChanged.connect(lambda cur, _: self._show_desc(cur))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        rl.addWidget(QLabel("<b>Effects</b>")); rl.addWidget(self.search); rl.addWidget(self.tree, 1); rl.addWidget(self.desc)
        rand_btn = self.rand_btn = QPushButton("🎲 Randomize"); rand_btn.clicked.connect(self.randomize)
        rand_btn.setToolTip("Apply a random effect with random settings to the selection (Ctrl+Shift+R)")
        brow = QHBoxLayout(); brow.addWidget(apply_btn); brow.addWidget(again_btn); rl.addLayout(brow)
        reroll_btn = self.reroll_btn = QPushButton("↺ Revert and randomize"); reroll_btn.clicked.connect(self.reroll)
        reroll_btn.setToolTip("Undo the last randomize, then apply a new random effect (Ctrl+Shift+E)")
        rl.addWidget(rand_btn); rl.addWidget(reroll_btn)
        right.setMinimumWidth(230); right.setMaximumWidth(360)

        main = QSplitter(Qt.Orientation.Horizontal)
        self.history = HistoryPanel()
        self.history.setMinimumWidth(190); self.history.setMaximumWidth(380)
        self.history.view.setCurrentIndex(int(self.settings.value("history_view", 0)))
        self.history.viewChanged.connect(lambda i: self.settings.setValue("history_view", i))
        self.history.jump.connect(self.goto_state)
        self.history.tweakRequested.connect(self.tweak_step)
        main.addWidget(self.history); main.addWidget(left); main.addWidget(right)
        main.setStretchFactor(1, 1); main.setSizes([230, 940, 270])
        self.setCentralWidget(main)

        self.info = QLabel(); self.sel_info = QLabel()
        self.statusBar().addWidget(self.info, 1); self.statusBar().addPermanentWidget(self.sel_info)
        self._build_menus()
        for w in self.findChildren((QPushButton, QComboBox)):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh(reset=True)

        if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
            self.open_path(sys.argv[1])

    # ------------------------------------------------------------ UI build
    def _act(self, menu, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self); a.triggered.connect(slot); a.setCheckable(checkable)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        menu.addAction(a); return a

    def _build_menus(self):
        m = self.menuBar().addMenu("&File")
        self._act(m, "&Open…", self.open_dialog, QKeySequence.StandardKey.Open)
        m.addSeparator()
        self.a_save = self._act(m, "&Save", self.save, QKeySequence.StandardKey.Save)
        self._act(m, "Save &As…", self.save_as, "Ctrl+Shift+S")
        self._act(m, "Save a &Copy…", self.save_copy, "Ctrl+Alt+S")
        m.addSeparator()
        self.a_revert = self._act(m, "Re&vert to Original", self.revert)
        m.addSeparator()
        self._act(m, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        m = self.menuBar().addMenu("&Edit")
        self.a_undo = self._act(m, "&Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.a_redo = self._act(m, "&Redo", self.redo, "Ctrl+Shift+Z")
        m.addSeparator()
        self.a_copy = self._act(m, "&Copy", self.copy_image, QKeySequence.StandardKey.Copy)
        self._act(m, "Copy &Whole Image", lambda: self.copy_image(whole=True))
        m.addSeparator()
        self.a_free = self._act(m, "Enable &free selection (rectangle)", lambda on: self.set_free_selection(on), checkable=True)
        self.a_free.setStatusTip("Drag rectangles on the image instead of runs of rows")
        self._act(m, "Select &All", self.select_all, QKeySequence.StandardKey.SelectAll)
        self._act(m, "Select &None", lambda: self._set_selection(None), "Ctrl+Shift+A")
        m.addSeparator()
        self._act(m, "Repeat Last Effect", self.repeat_last, "Ctrl+R")
        self._act(m, "Randomize", self.randomize, "Ctrl+Shift+R")
        self._act(m, "Revert and Randomize", self.reroll, "Ctrl+Shift+E")

        m = self.menuBar().addMenu("&View")
        g = QActionGroup(self)
        for i, name in enumerate(["Waveform", "Spectrogram", "Waveform + Spectrogram"]):
            a = self._act(m, name, lambda _, i=i: self.wave.set_mode(i), checkable=True)
            g.addAction(a); a.setChecked(i == WaveformView.BOTH)
        m.addSeparator()
        self._act(m, "Fit Image to Window", self.image_view.fit, "Ctrl+0")
        self._act(m, "Fill Window", self.image_view.fill)
        self._act(m, "Actual Size (100%)", self.image_view.actual_size, "Ctrl+1")
        self._act(m, "Center Image", self.image_view.center, "Ctrl+Shift+C")
        self._act(m, "Zoom In", lambda: self.image_view.zoom_by(1.25), "Ctrl+=")
        self._act(m, "Zoom Out", lambda: self.image_view.zoom_by(0.8), "Ctrl+-")
        m.addSeparator()
        self._act(m, "Zoom Waveform to Fit", lambda: self.wave._set_view(0, len(self.wave.x)), "Ctrl+Shift+0")

        m = self.menuBar().addMenu("&Help")
        tape_info = next((e.info for e in effects.EFFECTS if e.info), "")
        self._act(m, "&Effect reference…", lambda: InfoDialog("Effect reference", reference_html(effects.EFFECTS), self).show())
        self._act(m, "&Tape emulation guide…", lambda: InfoDialog("Tape emulation guide", tape_info, self).show())

        m = self.menuBar().addMenu("&Data")
        g = QActionGroup(self)
        for i, name in enumerate(MODE_NAMES):
            a = self._act(m, name, lambda _, i=i: self.set_data_mode(i), checkable=True)
            g.addAction(a); a.setChecked(i == 0)

        tb = self.toolbar = self.addToolBar("Main"); tb.setMovable(False)
        for act in (self.a_save, self.a_undo, self.a_redo):
            tb.addAction(act)
        tb.addSeparator()
        tb.addWidget(QLabel(" View: "))
        cb = QComboBox(); cb.addItems(["Waveform", "Spectrogram", "Both"]); cb.setCurrentIndex(2)
        cb.currentIndexChanged.connect(self.wave.set_mode); tb.addWidget(cb)
        tb.addWidget(QLabel("  Data: "))
        self.mode_cb = QComboBox(); self.mode_cb.addItems(MODE_NAMES)
        self.mode_cb.currentIndexChanged.connect(self.set_data_mode); tb.addWidget(self.mode_cb)

    def _build_view_strip(self) -> QWidget:
        strip = QWidget(); lay = QHBoxLayout(strip); lay.setContentsMargins(4, 2, 4, 2)
        iv = self.image_view
        for text, tip, fn in [("−", "Zoom out (Ctrl+-)", lambda: iv.zoom_by(0.8)), ("+", "Zoom in (Ctrl+=)", lambda: iv.zoom_by(1.25)),
                              ("Fit", "Fit image to window (Ctrl+0)", iv.fit), ("Fill", "Fill window, cropping overflow", iv.fill),
                              ("100%", "Actual size (Ctrl+1)", iv.actual_size), ("Center", "Center image (Ctrl+Shift+C)", iv.center)]:
            b = QPushButton(text); b.setToolTip(tip); b.setAutoDefault(False)
            b.setFixedWidth(34 if len(text) == 1 else 56); b.clicked.connect(lambda _, fn=fn: fn())
            lay.addWidget(b)
        self.zoom_label = QLabel(); self.zoom_label.setMinimumWidth(60)
        iv.viewChanged.connect(lambda pct: self.zoom_label.setText(f"{pct:.0f}%" if pct else ""))
        lay.addWidget(self.zoom_label); lay.addStretch()
        self.view_strip_buttons = strip.findChildren(QPushButton)
        return strip

    def _build_tree(self):
        self.tree.clear()
        cats = {}
        for c in CATEGORY_ORDER:
            cats[c] = QTreeWidgetItem(self.tree, [c])
        for e in effects.EFFECTS:
            it = QTreeWidgetItem(cats[e.category], [e.name + "…"])
            it.setData(0, Qt.ItemDataRole.UserRole, e.name)
            it.setToolTip(0, e.summary)
        self.tree.expandAll()

    def _show_desc(self, item):
        name = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if name:
            e = effects.by_name(name)
            self.desc.setText(f"<b>{e.name}</b><br>{e.summary}")
            self.desc.setStyleSheet("")
        else:
            self.desc.setText("Select an effect to see what it does. Double-click to apply. Right-click for more help.")
            self.desc.setStyleSheet("color: gray")

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        name = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not name:
            return
        e = effects.by_name(name)
        menu = QMenu(self)
        menu.addAction("About this effect…", lambda: InfoDialog(e.name, help_html(e), self).show())
        menu.addAction("Apply…", lambda: self.run_effect(e))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _filter_effects(self, text):
        t = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            cat = self.tree.topLevelItem(i); vis = False
            for j in range(cat.childCount()):
                ch = cat.child(j); hide = bool(t) and t not in ch.text(0).lower()
                ch.setHidden(hide); vis |= not hide
            cat.setHidden(not vis)

    # ------------------------------------------------------------- display
    def _refresh(self, reset=False, pixels=None):
        d = self.doc
        px = d.pixels if pixels is None else pixels
        self.image_view.set_pixels(px, d.size)
        self.wave.set_stream(d.stream(px), reset_view=reset)
        if pixels is None:
            self.history.set_history(d.entries())
        self._sync_outlines()
        if pixels is None:
            self.a_undo.setEnabled(d.can_undo); self.a_redo.setEnabled(d.can_redo)
            self.a_revert.setEnabled(d.loaded)
            self.a_save.setEnabled(d.loaded)
            name = (d.path.name if d.path else d.source_name) or "untitled"
            self.setWindowTitle(f"{name}{'*' if d.dirty else ''} — Databender")
            self.info.setText(f"{d.size[0]}×{d.size[1]}  ·  {px.size:,} samples  ·  {px.size / SR:.2f} s @ {SR} Hz" if d.loaded else "")
        self._update_sel_label()

    def _sync_outlines(self):
        self.image_view.set_ranges(self.doc.selection_pixel_ranges(self.sel))
        self.image_view.set_rects(self.doc.selection_rects(self.sel))

    def _update_sel_label(self):
        if not self.sel:
            self.sel_info.setText("No selection (effects apply to everything)" if self.doc.loaded else "")
        elif isinstance(self.sel, Rect):
            r = self.sel
            self.sel_info.setText(f"Rectangle: x {r.x0}–{r.x1}, y {r.y0}–{r.y1}  ({r.w}×{r.h} px)")
        else:
            a, b = self.sel
            self.sel_info.setText(f"Selection: {a:,}–{b:,}  ({a / SR:.3f}–{b / SR:.3f} s, {b - a:,} samples)")

    def _on_image_selection(self, r):
        """Dragging on the image selects a raster-order run of pixels; map it to samples."""
        if r is None:
            self._set_selection(None); return
        a, b = r
        if self.doc.mode == INTERLEAVED:
            self._set_selection((a * 3, b * 3))
        else:  # planar data keeps R, G, B in separate runs, so one pixel run maps to the red plane only
            self._set_selection((a, b))
            self.statusBar().showMessage("Planar mode: image selection covers the red plane only", 4000)

    def _on_selection(self, sel):
        self.sel = sel
        self._sync_outlines()
        self._update_sel_label()
        self.selChanged.emit(sel)

    def _set_selection(self, sel):
        self.sel = sel
        if isinstance(sel, Rect):
            self.wave.set_runs(*self.doc.selection_runs(sel))
        else:
            self.wave.set_selection(sel)
        self._on_selection(sel)

    def set_free_selection(self, on: bool):
        """Free selection mode: dragging on the image draws a rectangle. Off = the normal run-of-rows selection."""
        self.free_mode = on
        self.image_view.free_mode = on
        if self.a_free.isChecked() != on:
            self.a_free.setChecked(on)
        if on:
            self.statusBar().showMessage("Free selection on: drag a rectangle on the image (Space+drag pans)", 8000)
            self._free_hint()

    def _free_hint(self):
        if str(self.settings.value("free_hint_off", "0")) in ("1", "true"):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information); box.setWindowTitle("Free selection")
        box.setText("<b>Free selection draws a rectangle.</b>")
        box.setInformativeText(
            "• The effect treats the rectangle as its own small picture: its rows are joined together. Settings measured in "
            "samples (chunk sizes, delays) therefore relate to the rectangle's width, not the whole image's, so the same settings "
            "can look quite different from a normal selection.<br>"
            "• Only pixels inside the rectangle change.<br>"
            "• The waveform shows a rectangle as many thin bands, and you can't edit it there.<br>"
            "• Turn this off in the Edit menu to get the normal selection back.")
        cb = QCheckBox("Do not show again"); box.setCheckBox(cb)
        box.exec()
        if cb.isChecked():
            self.settings.setValue("free_hint_off", 1)

    def copy_image(self, whole=False):
        """Put the selection (or the whole image) on the system clipboard as a picture.

        A selection is a run in reading order, so the copy is the band of rows it touches; the unselected ends of
        the first and last row are transparent, which makes the paste match the selection exactly."""
        d = self.doc
        if not d.loaded:
            return
        W, H = d.size
        rgb = d.pixels.reshape(H, W, 3)
        sel = None if whole else norm_sel(self.sel)
        what = "whole image"
        mask = None
        if isinstance(sel, Rect):  # a rectangle is a plain, fully opaque crop
            crop = np.ascontiguousarray(rgb[sel.y0:sel.y1, sel.x0:sel.x1])
            QApplication.clipboard().setImage(QImage(crop.data, sel.w, sel.h, 3 * sel.w, QImage.Format.Format_RGB888).copy())
            self.statusBar().showMessage(f"Copied {sel.w}×{sel.h} px (rectangle) to the clipboard", 4000)
            return
        if sel:
            m = np.zeros(W * H, bool)
            for a, b in d.selection_pixel_ranges(sel):
                m[a:b] = True
            idx = np.flatnonzero(m)
            r0, r1 = int(idx[0]) // W, int(idx[-1]) // W
            rgb, m = rgb[r0:r1 + 1], m.reshape(H, W)[r0:r1 + 1]
            mask = None if m.all() else m
            what = f"selection, rows {r0}-{r1}"
        h = rgb.shape[0]
        if mask is None:
            img = QImage(np.ascontiguousarray(rgb).data, W, h, 3 * W, QImage.Format.Format_RGB888).copy()
        else:
            rgba = np.dstack([rgb, mask.astype(np.uint8) * 255])
            img = QImage(np.ascontiguousarray(rgba).data, W, h, 4 * W, QImage.Format.Format_RGBA8888).copy()
        QApplication.clipboard().setImage(img)
        self.statusBar().showMessage(f"Copied {W}×{h} px ({what}) to the clipboard", 4000)

    def select_all(self):
        if self.doc.loaded:
            W, H = self.doc.size
            self._set_selection(Rect(0, 0, W, H) if self.free_mode else (0, self.doc.pixels.size))

    # --------------------------------------------------------------- files
    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Discard unsaved changes?",
                                 QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    def open_dialog(self):
        if not self._confirm_discard():
            return
        start = self.settings.value("lastdir", str(Path.home()))
        p, _ = QFileDialog.getOpenFileName(self, "Open image", start, OPEN_FILTER)
        if p:
            self.open_path(p, confirmed=True)

    def open_path(self, p, confirmed=False):
        if not confirmed and not self._confirm_discard():
            return
        try:
            self.doc.open(p)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Can't open image", f"{p}\n\n{e}"); return
        self.settings.setValue("lastdir", str(Path(p).parent))
        self.sel = None
        self.image_view.fit()
        self._refresh(reset=True)

    def _ask_path(self, title) -> str | None:
        d = self.doc
        base = d.path or (Path(self.settings.value("lastdir", str(Path.home()))) / (Path(d.source_name).stem + "_bent.png"))
        p, flt = QFileDialog.getSaveFileName(self, title, str(base), SAVE_FILTER)
        if p and not Path(p).suffix:
            ext = {"PNG": ".png", "JPEG": ".jpg", "BMP": ".bmp", "TIFF": ".tif", "WebP": ".webp"}[flt.split(" ")[0]]
            p += ext
        return p or None

    def _write(self, p, adopt):
        try:
            self.doc.save(p, adopt)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Can't save", str(e)); return False
        self.statusBar().showMessage(f"Saved {p}", 4000)
        self._refresh()
        return True

    def save(self):
        if not self.doc.loaded:
            return
        if self.doc.path is None:
            return self.save_as()
        self._write(str(self.doc.path), True)

    def save_as(self):
        if self.doc.loaded and (p := self._ask_path("Save As")):
            self._write(p, True)

    def save_copy(self):
        if self.doc.loaded and (p := self._ask_path("Save a Copy")):
            self._write(p, False)

    def revert(self):
        if self.doc.loaded:
            self.doc.revert(); self._refresh()

    def closeEvent(self, e):
        if self._confirm_discard():
            e.accept()
        else:
            e.ignore()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.open_path(urls[0].toLocalFile())

    # ---------------------------------------------------------------- edit
    def undo(self):
        if self.doc.undo():
            self._refresh()

    def redo(self):
        if self.doc.redo():
            self._refresh()

    def set_data_mode(self, i):
        self.doc.mode = i
        self.mode_cb.blockSignals(True); self.mode_cb.setCurrentIndex(i); self.mode_cb.blockSignals(False)
        self._set_selection(None)
        self._refresh(reset=True)

    def _tree_activate(self, item):
        if not item or not item.data(0, Qt.ItemDataRole.UserRole):
            return
        self.run_effect(effects.by_name(item.data(0, Qt.ItemDataRole.UserRole)))

    def run_effect(self, eff: Effect, initial: dict | None = None, extra: dict | None = None, on_cancel=None):
        if not self.doc.loaded:
            self.statusBar().showMessage("Open an image first", 3000); return
        if getattr(self, "_dlg", None):  # one effect dialog at a time
            self._dlg.raise_(); return

        def preview(vals):
            self.request_preview(eff, vals)

        # Non-modal: a modal dialog makes the window manager dim the main window, hiding the live preview.
        locked = [self.menuBar(), self.toolbar, self.history, self.tree, self.search, self.apply_btn, self.again_btn, self.rand_btn, self.reroll_btn]
        dlg = EffectDialog(eff, initial if initial is not None else self.last_params.get(eff.name, {}), preview, self)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        self._dlg = dlg
        for w in locked:
            w.setEnabled(False)

        def done(result):
            for w in locked:
                w.setEnabled(True)
            self._dlg = None
            self._pv_latest = None  # forget queued previews; a finished one stays cached so OK can reuse it
            self._clear_preview_outline()
            if result == QDialog.DialogCode.Accepted:
                params = dlg.values()
                self.last_params[eff.name] = params
                self._apply(eff, params, extra=extra)
            elif on_cancel:
                self._pv_cache = None
                on_cancel()
            else:
                self._pv_cache = None
                self._refresh()
            dlg.deleteLater()

        self.selChanged.connect(dlg._changed)  # re-preview when the selection changes
        dlg.finished.connect(done)
        dlg.show()

    def tweak_step(self, node_id: int):
        """Re-run a past step with adjusted settings. It starts from the state *before* that step and
        becomes a new branch next to it, so the original version is never lost."""
        d = self.doc
        node = d.nodes.get(node_id)
        if node is None or node.parent is None or getattr(self, "_dlg", None):
            return
        info = node.info
        try:
            eff = effects.by_name(info["name"])
        except StopIteration:
            return
        if not eff.params:
            return
        back = (d.node.id, self.sel, d.mode)

        def restore():  # cancelled: put everything back exactly as it was
            d.goto(back[0])
            if d.mode != back[2]:
                self.set_data_mode(back[2])
            self._set_selection(back[1])
            self._refresh()

        d.goto(node.parent.id)
        if info.get("mode", d.mode) != d.mode:
            self.set_data_mode(info["mode"])
        self._set_selection(info.get("sel"))
        self._refresh()
        self.statusBar().showMessage(f"Tweaking step {node_id} ({eff.name}); applies as a new branch", 6000)
        self.run_effect(eff, initial=info.get("params", {}), extra={"tweak_of": node_id}, on_cancel=restore)

    def goto_state(self, i):
        if self.doc.goto(i):
            self._refresh()

    def _pv_key(self, eff, params, crop=None):
        return (eff.name, tuple(sorted(params.items())), norm_sel(self.sel), self.doc.node.id, self.doc.mode, crop)

    def small_crop(self, pct):
        """Sample range to preview when only `pct` % is wanted: a whole-row stretch centred in the target
        (the selection, or the whole image), so every row keeps its real length. None = preview everything."""
        d = self.doc
        n = d.pixels.size
        sel = norm_sel(self.sel)
        if isinstance(sel, Rect):  # a stretch of the rectangle's rows, full width
            if pct is None or pct >= 100:
                return None
            k = max(int(sel.h * pct / 100), 1)
            if k >= sel.h:
                return None
            y0 = sel.y0 + (sel.h - k) // 2
            return Rect(sel.x0, y0, sel.x1, y0 + k)
        a, b = sel if sel else (0, n)
        if pct is None or pct >= 100 or b - a < 2:
            return None
        row = 3 * d.size[0] if d.mode == INTERLEAVED else d.size[0]
        k = min(max(int((b - a) * pct / 100), row), b - a)  # at least one row, never more than the target
        s = a + (b - a - k) // 2
        e = s + k
        s, e = max((s // row) * row, a), min(-(-e // row) * row, b)  # snap outwards to whole rows, but stay inside the target
        return (s, e) if e - s >= 2 and (e - s) < (b - a) else None

    def _clear_preview_outline(self):
        self.image_view.set_preview_ranges([]); self.image_view.set_preview_rects([])

    def request_preview(self, eff, vals):
        """Live preview without blocking the UI: only the newest request is ever computed."""
        if vals is None:  # live preview switched off
            self._pv_latest = None
            self._clear_preview_outline()
            self._refresh(); return
        dlg = getattr(self, "_dlg", None)
        crop = self.small_crop(dlg.small_percent()) if dlg else None
        self._clear_preview_outline()
        if isinstance(crop, Rect):
            self.image_view.set_preview_rects([crop])
        elif crop:
            self.image_view.set_preview_ranges(self.doc.selection_pixel_ranges(crop))
        key = self._pv_key(eff, vals, crop)
        self._pv_latest = (eff, vals, key, crop)
        if self._pv_cache and self._pv_cache[0] == key:
            self._show_preview(self._pv_cache[1]); self._set_dlg_busy(False)
        elif not self._pv_busy:
            self._pv_start()

    LEAD_IN = 66150  # 1.5 s of audio (~10 rows of a 2200 px image) so echoes/filters have built up by the previewed part

    def _pv_start(self):
        eff, vals, key, crop = self._pv_latest
        self._pv_busy = True
        self._set_dlg_busy(True)
        d = self.doc
        sel = norm_sel(self.sel)
        keep, keep_rows = None, 0
        if isinstance(crop, Rect):  # run on a few extra rows above the preview rows, then keep the originals of those
            rowlen = 3 * d.size[0] if d.mode == INTERLEAVED else d.size[0]
            y0 = max(sel.y0, crop.y0 - -(-self.LEAD_IN // rowlen))
            keep_rows = crop.y0 - y0
            sel = Rect(crop.x0, y0, crop.x1, crop.y1)
        elif crop:  # run on [crop start - lead-in, crop end], keep only [crop start, crop end]
            a = sel[0] if sel else 0
            keep = crop[0]; sel = (max(a, crop[0] - self.LEAD_IN), crop[1])
        self._pv_pool.start(_PreviewTask(d, eff, vals, sel, d.live.copy(), d.mode, key, self._pv_signals, keep, keep_rows))

    def _on_preview_done(self, key, px):
        self._pv_busy = False
        if self._pv_latest is None:  # dialog closed (discard) or live preview switched off (keep for OK)
            if px is not None and getattr(self, "_dlg", None):
                self._pv_cache = (key, px)
            return
        if px is not None:
            self._pv_cache = (key, px)
            self._show_preview(px)
        if self._pv_latest[2] != key:  # the user moved on while we were computing
            self._pv_start()
        else:
            self._set_dlg_busy(False)

    def _show_preview(self, px):
        self.image_view.set_pixels(px, self.doc.size)
        if px.size <= 8_000_000:  # converting a huge image to a waveform on every tick would stall the UI
            self.wave.set_stream(self.doc.stream(px))

    def _set_dlg_busy(self, busy):
        if getattr(self, "_dlg", None):
            self._dlg.set_busy(busy)

    def _apply(self, eff, params, random=False, extra=None):
        cached = self._pv_cache
        self._pv_cache = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if cached and cached[0] == self._pv_key(eff, params):
                px = cached[1]  # the preview already is the result
            else:
                px = self.doc.compute(eff, params, self.sel)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, eff.name, f"Effect failed: {e}"); return
        finally:
            QApplication.restoreOverrideCursor()
        sel = norm_sel(self.sel)
        self.doc.commit(px, {"name": eff.name, "params": params, "sel": sel, "mode": self.doc.mode, "random": random, **(extra or {})})
        self.last_effect = (eff, params)
        self._refresh()
        self.statusBar().showMessage(f"Applied {eff.name}", 3000)

    def randomize(self):
        if not self.doc.loaded:
            self.statusBar().showMessage("Open an image first", 3000); return
        eff, params = effects.random_effect()
        self._apply(eff, params, random=True)
        self._rand_node = self.doc.node  # lets reroll() tell whether the last edit was a randomize
        vals = ", ".join(f"{k}={v}" for k, v in params.items())
        self.statusBar().showMessage(f"Randomized: {eff.name}" + (f" ({vals})" if vals else ""), 8000)

    def reroll(self):
        """Step back from the last randomize (it stays in History) and randomize again."""
        if self.doc.loaded and getattr(self, "_rand_node", None) is self.doc.node:
            self.doc.undo()
        self.randomize()

    def repeat_last(self):
        if self.last_effect and self.doc.loaded:
            self._apply(*self.last_effect)


def main():
    # KDE's file dialog logs a harmless warning whenever files in the folder it is showing get rewritten
    QLoggingCategory.setFilterRules("kf.kio.widgets.kdirmodel.warning=false")
    app = QApplication(sys.argv)
    app.setApplicationName("Databender")
    w = MainWindow(); w.show()
    sys.exit(app.exec())
