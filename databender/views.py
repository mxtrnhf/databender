"""Custom widgets: image preview and waveform/spectrogram strip."""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QWidget

from .core import Rect
from .effects import SR

BG = QColor("#1e1f22")
SEL = QColor(80, 160, 255, 70)
SEL_EDGE = QColor(120, 190, 255)


def _lut() -> np.ndarray:
    """Inferno-ish colour ramp, 256x3 uint8."""
    anchors = [(0, (0, 0, 4)), (0.25, (80, 18, 123)), (0.5, (182, 54, 121)),
               (0.75, (251, 136, 97)), (1, (252, 253, 191))]
    t = np.linspace(0, 1, 256)
    xs = [a for a, _ in anchors]
    return np.stack([np.interp(t, xs, [c[i] for _, c in anchors]) for i in range(3)], 1).astype(np.uint8)


LUT = _lut()


def _qimage(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class ImageView(QWidget):
    """Image preview. Drag = select; hold Space (or use the middle button) and drag = pan; wheel = zoom."""

    viewChanged = pyqtSignal(float)  # current scale in percent (100 = one image pixel per screen pixel)
    rangeSelected = pyqtSignal(object)  # (first_pixel, end_pixel) in raster order, or None to clear
    rectSelected = pyqtSignal(object)  # Rect in pixels (free selection mode), or None to clear

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(120)
        self.qimg: QImage | None = None
        self.ranges: list[tuple[int, int]] = []
        self.preview_ranges: list[tuple[int, int]] = []  # the part a small-scale preview is actually computing (orange)
        self.rects: list[Rect] = []  # rectangular selection outline(s)
        self.preview_rects: list[Rect] = []
        self.free_mode = False  # True: dragging draws a rectangle instead of selecting a run of rows
        self.zoom = 1.0  # multiple of fit scale
        self.pan = QPointF(0, 0)
        self._drag = None
        self._space = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # needed to see Space press/release
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _update_cursor(self):
        if self._drag and self._drag[0] == "pan":
            c = Qt.CursorShape.ClosedHandCursor
        else:
            c = Qt.CursorShape.OpenHandCursor if self._space else Qt.CursorShape.CrossCursor
        self.setCursor(c)

    def enterEvent(self, e):
        # grab the keyboard so Space works right away, but never steal it from a text/number field
        if not isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox)):
            self.setFocus()
        super().enterEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            if not e.isAutoRepeat():
                self._space = True; self._update_cursor()
            e.accept()
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            if not e.isAutoRepeat():
                self._space = False; self._update_cursor()
            e.accept()
        else:
            super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self._space = False; self._update_cursor()
        super().focusOutEvent(e)

    def _xy_at(self, pos: QPointF) -> tuple[int, int]:
        """Column and row of the image pixel under `pos`, clamped to the image."""
        s, ox, oy = self._geom()
        return (int(np.clip(np.floor((pos.x() - ox) / s), 0, self.qimg.width() - 1)),
                int(np.clip(np.floor((pos.y() - oy) / s), 0, self.qimg.height() - 1)))

    def _pixel_at(self, pos: QPointF) -> int:
        """Raster index of the image pixel under `pos`, clamped to the image."""
        s, ox, oy = self._geom()
        w, h = self.qimg.width(), self.qimg.height()
        x = int(np.clip(np.floor((pos.x() - ox) / s), 0, w - 1))
        y = int(np.clip(np.floor((pos.y() - oy) / s), 0, h - 1))
        return y * w + x

    def set_pixels(self, pixels: np.ndarray, size: tuple[int, int]):
        w, h = size
        self.qimg = _qimage(pixels.reshape(h, w, 3)) if pixels.size else None
        self._changed()

    def set_rects(self, rects):
        self.rects = rects
        self.update()

    def set_preview_rects(self, rects):
        self.preview_rects = rects
        self.update()

    def set_preview_ranges(self, ranges):
        self.preview_ranges = ranges
        self.update()

    def set_ranges(self, ranges):
        self.ranges = ranges
        self.update()

    def _changed(self):
        self.update()
        self.viewChanged.emit(self.scale() * 100 if self.qimg else 0.0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._changed()

    def _fit_scale(self) -> float:
        return min(self.width() / self.qimg.width(), self.height() / self.qimg.height())

    def scale(self) -> float:
        return self._fit_scale() * self.zoom if self.qimg else 1.0

    def set_scale(self, s: float, anchor: QPointF | None = None):
        """Zoom to absolute scale `s`, keeping the point under `anchor` (default: view centre) fixed."""
        if not self.qimg:
            return
        fs = self._fit_scale()
        new = float(np.clip(s, 0.02, 64))
        f = new / (fs * self.zoom)
        cx, cy = self.width() / 2, self.height() / 2
        m = anchor if anchor is not None else QPointF(cx, cy)
        self.pan = QPointF(m.x() - cx - (m.x() - cx - self.pan.x()) * f,
                           m.y() - cy - (m.y() - cy - self.pan.y()) * f)
        self.zoom = new / fs
        self._changed()

    def zoom_by(self, f: float):
        self.set_scale(self.scale() * f)

    def fit(self):
        self.zoom = 1.0; self.pan = QPointF(0, 0); self._changed()

    def fill(self):
        """Scale so the image covers the whole view (cropping the overflow)."""
        if self.qimg:
            self.pan = QPointF(0, 0)
            self.zoom = max(self.width() / self.qimg.width(), self.height() / self.qimg.height()) / self._fit_scale()
            self._changed()

    def actual_size(self):
        """100%: one image pixel per screen pixel, centred."""
        if self.qimg:
            self.pan = QPointF(0, 0); self.zoom = 1 / self._fit_scale(); self._changed()

    def center(self):
        self.pan = QPointF(0, 0); self._changed()

    def _geom(self):
        iw, ih = self.qimg.width(), self.qimg.height()
        s = min(self.width() / iw, self.height() / ih) * self.zoom
        ox = (self.width() - iw * s) / 2 + self.pan.x()
        oy = (self.height() - ih * s) / 2 + self.pan.y()
        return s, ox, oy

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        if not self.qimg:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open an image (Ctrl+O) or drop one here")
            return
        s, ox, oy = self._geom()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, s < 1.5)
        p.drawImage(QRectF(ox, oy, self.qimg.width() * s, self.qimg.height() * s), self.qimg)
        w = self.qimg.width()
        p.setBrush(Qt.BrushStyle.NoBrush)
        for ranges, color in ((self.ranges, QColor(255, 255, 255)), (self.preview_ranges, QColor(255, 170, 0))):
            black = QPen(QColor(0, 0, 0), 1); light = QPen(color, 1, Qt.PenStyle.DashLine)
            for r in ranges:  # outline only: the image itself is never tinted
                path = self._outline(r, w, ox, oy, s)
                if path is not None:
                    p.setPen(black); p.drawPath(path)
                    p.setPen(light); p.drawPath(path)
        for rects, color in ((self.rects, QColor(255, 255, 255)), (self.preview_rects, QColor(255, 170, 0))):
            black = QPen(QColor(0, 0, 0), 1); light = QPen(color, 1, Qt.PenStyle.DashLine)
            for r in rects:
                path = QPainterPath()
                path.addRect(QRectF(ox + r.x0 * s, oy + r.y0 * s, (r.x1 - r.x0) * s, (r.y1 - r.y0) * s))
                p.setPen(black); p.drawPath(path)
                p.setPen(light); p.drawPath(path)

    @staticmethod
    def _outline(r, w, ox, oy, s):
        a, b = r
        if b <= a:
            return None
        r0, c0 = divmod(a, w); r1, c1 = divmod(b - 1, w)
        if r0 == r1:
            pts = [(c0, r0), (c1 + 1, r0), (c1 + 1, r0 + 1), (c0, r0 + 1)]
        else:  # raster order: partial first row, full rows, partial last row
            pts = [(c0, r0), (w, r0), (w, r1), (c1 + 1, r1), (c1 + 1, r1 + 1), (0, r1 + 1), (0, r0 + 1), (c0, r0 + 1)]
        path = QPainterPath()
        path.moveTo(ox + pts[0][0] * s, oy + pts[0][1] * s)
        for x, y in pts[1:]:
            path.lineTo(ox + x * s, oy + y * s)
        path.closeSubpath()
        return path

    def wheelEvent(self, e):
        if self.qimg:
            self.set_scale(self.scale() * (1.2 if e.angleDelta().y() > 0 else 1 / 1.2), e.position())

    def mousePressEvent(self, e):
        if not self.qimg:
            return
        left = e.button() == Qt.MouseButton.LeftButton
        if e.button() == Qt.MouseButton.MiddleButton or (left and self._space):
            self._drag = ("pan", e.position(), QPointF(self.pan))
        elif left and self.free_mode:
            self._drag = ("rect", self._xy_at(e.position()), e.position(), False)
        elif left:
            self._drag = ("sel", self._pixel_at(e.position()), e.position(), False)
        self._update_cursor()

    def mouseMoveEvent(self, e):
        if not self._drag or not self.qimg:
            return
        if self._drag[0] == "pan":
            self.pan = self._drag[2] + (e.position() - self._drag[1])
            self._changed()
            return
        kind, anchor, start, moved = self._drag
        if not moved:
            if (e.position() - start).manhattanLength() < 4:  # ignore jitter on a plain click
                return
            self._drag = (kind, anchor, start, True)
        if kind == "rect":
            cx, cy = self._xy_at(e.position())
            self.rectSelected.emit(Rect(min(anchor[0], cx), min(anchor[1], cy), max(anchor[0], cx) + 1, max(anchor[1], cy) + 1))
            return
        cur = self._pixel_at(e.position())
        self.rangeSelected.emit((min(anchor, cur), max(anchor, cur) + 1))

    def mouseReleaseEvent(self, _):
        if self._drag and self._drag[0] in ("sel", "rect") and not self._drag[3]:
            (self.rectSelected if self._drag[0] == "rect" else self.rangeSelected).emit(None)  # a plain click clears the selection
        self._drag = None
        self._update_cursor()


class WaveformView(QWidget):
    """Waveform and/or spectrogram of the image-as-audio, with range selection."""

    selectionChanged = pyqtSignal(object)  # (a, b) or None
    WAVE, SPEC, BOTH = 0, 1, 2

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.setMouseTracking(True)
        self.x = np.zeros(0, np.float32)
        self.version = 0
        self.view = (0.0, 1.0)
        self.sel: tuple[int, int] | None = None
        self.runs = None  # (starts, ends) arrays when a rectangle is selected: shown as bands, not editable here
        self.mode = self.BOTH
        self._drag = None
        self._moved = False
        self._spec_key = None
        self._spec_img: QImage | None = None

    # ---- data
    def set_stream(self, x: np.ndarray, reset_view: bool = False):
        keep = len(x) == len(self.x) and not reset_view
        self.x = x; self.version += 1
        if not keep:
            self.view = (0.0, float(max(len(x), 1))); self.sel = None
        self.update()

    def set_mode(self, m):
        self.mode = m; self.update()

    def set_selection(self, sel):
        self.sel = sel; self.runs = None; self.update()

    def set_runs(self, starts, ends):
        self.sel = None; self.runs = (starts, ends) if len(starts) else None; self.update()

    # ---- coordinate helpers
    def _s2x(self, s): return (s - self.view[0]) / (self.view[1] - self.view[0]) * self.width()
    def _x2s(self, x): return self.view[0] + x / max(self.width(), 1) * (self.view[1] - self.view[0])

    def _regions(self):
        top = 18
        h = self.height() - top
        if self.mode == self.WAVE: return (top, h), None
        if self.mode == self.SPEC: return None, (top, h)
        wh = int(h * 0.42)
        return (top, wh), (top + wh, h - wh)

    # ---- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), BG)
        if self.x.size == 0:
            return
        wave, spec = self._regions()
        self._paint_ruler(p)
        if spec: self._paint_spec(p, *spec)
        if wave: self._paint_wave(p, *wave)
        if self.runs is not None:  # rectangle: merge the many row-runs into the pixel columns they cover
            w = self.width(); x0, x1 = self._s2x(self.runs[0]), self._s2x(self.runs[1])
            vis = (x1 > 0) & (x0 < w)
            lo = np.clip(np.floor(x0[vis]).astype(int), 0, w - 1); hi = np.clip(np.ceil(x1[vis]).astype(int), 1, w)
            hi = np.maximum(hi, lo + 1)
            diff = np.zeros(w + 1, int); np.add.at(diff, lo, 1); np.add.at(diff, hi, -1)
            cover = (np.cumsum(diff[:w]) > 0).astype(int)
            edges = np.flatnonzero(np.diff(np.concatenate([[0], cover, [0]])))
            for a, b in zip(edges[::2], edges[1::2]):
                p.fillRect(QRectF(float(a), 0, float(b - a), self.height()), SEL)
        if self.sel:
            a, b = self._s2x(self.sel[0]), self._s2x(self.sel[1])
            p.fillRect(QRectF(a, 0, max(b - a, 1), self.height()), SEL)
            p.setPen(QPen(SEL_EDGE, 1))
            p.drawLine(int(a), 0, int(a), self.height()); p.drawLine(int(b), 0, int(b), self.height())

    def _paint_ruler(self, p):
        p.setPen(QColor("#9aa0a6"))
        span = (self.view[1] - self.view[0]) / SR
        raw = span / max(self.width() / 90, 1)
        step = next((s for s in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60) if s >= raw), 120)
        t = np.ceil(self.view[0] / SR / step) * step
        while t * SR < self.view[1]:
            x = self._s2x(t * SR)
            p.drawLine(int(x), 12, int(x), 18)
            p.drawText(int(x) + 3, 11, f"{t:g}s")
            t += step

    def _paint_wave(self, p, y0, h):
        w = self.width()
        a, b = int(max(self.view[0], 0)), int(min(self.view[1], len(self.x)))
        if b <= a:
            return
        mid = y0 + h / 2
        p.setPen(QColor("#2c2f33")); p.drawLine(0, int(mid), w, int(mid))
        seg = self.x[a:b]
        spp = (self.view[1] - self.view[0]) / w
        p.setPen(QPen(QColor("#4fc3f7"), 1))
        if spp <= 2:
            xs = self._s2x(np.arange(a, b))
            pts = [QPointF(float(x), float(mid - v * h / 2)) for x, v in zip(xs, seg)]
            p.drawPolyline(pts)
            return
        edges = ((np.arange(w + 1) * spp + self.view[0]) - a).astype(int).clip(0, len(seg))
        starts = edges[:-1]
        valid = starts < np.append(edges[1:], len(seg))[:len(starts)]
        starts = np.minimum(starts, len(seg) - 1)
        lo = np.minimum.reduceat(seg, starts); hi = np.maximum.reduceat(seg, starts)
        for x in np.nonzero(valid)[0]:
            p.drawLine(int(x), int(mid - hi[x] * h / 2), int(x), int(mid - lo[x] * h / 2) + 1)

    def _paint_spec(self, p, y0, h):
        w = self.width()
        key = (self.version, self.view, w, h)
        if key != self._spec_key:
            self._spec_img = _qimage(self._spectrogram(w, max(h, 1)))
            self._spec_key = key
        p.drawImage(0, y0, self._spec_img)

    def _spectrogram(self, w: int, h: int) -> np.ndarray:
        N = 1024
        centers = self.view[0] + (np.arange(w) + 0.5) * (self.view[1] - self.view[0]) / w
        idx = centers[:, None].astype(np.int64) + (np.arange(N) - N // 2)[None, :]
        ok = (idx >= 0) & (idx < len(self.x))
        frames = np.where(ok, self.x[np.clip(idx, 0, len(self.x) - 1)], 0).astype(np.float32)
        spec = np.abs(np.fft.rfft(frames * np.hanning(N).astype(np.float32), axis=1))
        db = 20 * np.log10(spec / (N / 4) + 1e-6)
        norm = ((db + 80) / 80).clip(0, 1)  # (w, 513)
        rows = (512 * (1 - np.arange(h) / h) ** 2).astype(int).clip(0, 512)
        img = LUT[(norm[:, rows].T * 255).astype(np.uint8)]  # (h, w, 3)
        img[:, ~ok.any(axis=1)] = 0
        return img

    # ---- interaction
    def mousePressEvent(self, e):
        if self.x.size == 0:
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self._drag = ("pan", e.position().x(), self.view); return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        px = e.position().x()
        if self.sel:  # grab an edge to resize the selection
            for edge, other in ((0, 1), (1, 0)):
                if abs(self._s2x(self.sel[edge]) - px) < 6:
                    self._drag = ("sel", self.sel[other]); return
        s = int(np.clip(self._x2s(px), 0, len(self.x)))
        self._moved = False
        self._drag = ("sel", s, px)

    def mouseMoveEvent(self, e):
        px = e.position().x()
        if self.sel:
            near = any(abs(self._s2x(s) - px) < 6 for s in self.sel)
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.ArrowCursor)
        if not self._drag:
            return
        if self._drag[0] == "pan":
            _, x0, (v0, v1) = self._drag
            d = (x0 - px) / self.width() * (v1 - v0)
            self._set_view(v0 + d, v1 + d)
        else:
            anchor = self._drag[1]
            if len(self._drag) > 2 and not self._moved:
                if abs(px - self._drag[2]) < 3:
                    return
                self._moved = True
            s =int(np.clip(self._x2s(px), 0, len(self.x)))
            self.sel = (min(anchor, s), max(anchor, s)); self.runs = None
            self.selectionChanged.emit(self.sel); self.update()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d and d[0] == "sel" and len(d) > 2 and not self._moved:  # plain click clears the selection
            if self.sel is not None:
                self.sel = None; self.selectionChanged.emit(None); self.update()
        elif self.sel and self.sel[1] - self.sel[0] < 2:
            self.sel = None; self.selectionChanged.emit(None); self.update()

    def mouseDoubleClickEvent(self, _):
        self._set_view(0, len(self.x))

    def wheelEvent(self, e):
        if self.x.size == 0:
            return
        v0, v1 = self.view
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            d = (v1 - v0) * 0.1 * (-1 if e.angleDelta().y() > 0 else 1)
            self._set_view(v0 + d, v1 + d); return
        f = 0.75 if e.angleDelta().y() > 0 else 1 / 0.75
        c = self._x2s(e.position().x())
        self._set_view(c - (c - v0) * f, c + (v1 - c) * f)

    def _set_view(self, v0, v1):
        n = len(self.x)
        span = float(np.clip(v1 - v0, 64, max(n, 64)))
        v0 = float(np.clip(v0, 0, max(n - span, 0)))
        self.view = (v0, v0 + span)
        self.update()
