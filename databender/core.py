"""Document model: an RGB image treated as a stream of audio samples."""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageOps

from .effects import SR, Effect

MAX_STEPS = 1000  # history entries kept ...
MAX_HISTORY_BYTES = 1 << 30  # ... or until their stored deltas exceed this (oldest abandoned ones go first)
INTERLEAVED, PLANAR = 0, 1
MODE_NAMES = ["Interleaved RGB (classic)", "Planar (R, then G, then B)"]


class Rect(NamedTuple):
    """A rectangular selection in pixels, half-open: columns x0..x1-1, rows y0..y1-1."""
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def w(self) -> int: return self.x1 - self.x0

    @property
    def h(self) -> int: return self.y1 - self.y0


def norm_sel(sel):
    """A usable selection, or None. A selection is a Rect, or a (start, end) run of samples in reading order."""
    if isinstance(sel, Rect):
        return sel if sel.x1 > sel.x0 and sel.y1 > sel.y0 else None
    return sel if sel and sel[1] - sel[0] > 1 else None


def _rect_view(px: np.ndarray, mode: int, size, r: Rect) -> np.ndarray:
    """The rectangle as a writable array in stream order: rows of 3w samples (interleaved) or (plane, row, x) (planar)."""
    W, H = size
    if mode == INTERLEAVED:
        return px.reshape(H, W * 3)[r.y0:r.y1, r.x0 * 3:r.x1 * 3]
    return px.reshape(H, W, 3)[r.y0:r.y1, r.x0:r.x1].transpose(2, 0, 1)


def rect_read(px: np.ndarray, mode: int, size, r: Rect) -> np.ndarray:
    """The samples a picture cropped to `r` would produce: the rectangle's rows joined in order."""
    return (_rect_view(px, mode, size, r).astype(np.float32).ravel() - 127.5) / 127.5


def rect_write(px: np.ndarray, mode: int, size, r: Rect, x: np.ndarray):
    q = np.clip(np.rint(x * 127.5 + 127.5), 0, 255).astype(np.uint8)
    v = _rect_view(px, mode, size, r)
    v[...] = q.reshape(v.shape)


def to_stream(pixels: np.ndarray, mode: int) -> np.ndarray:
    """Flat uint8 RGB bytes -> float32 samples in [-1, 1]."""
    data = pixels.reshape(-1, 3).T.ravel() if mode == PLANAR else pixels
    return (data.astype(np.float32) - 127.5) / 127.5


def from_stream(x: np.ndarray, mode: int) -> np.ndarray:
    data = np.clip(np.rint(x * 127.5 + 127.5), 0, 255).astype(np.uint8)
    return data.reshape(3, -1).T.ravel() if mode == PLANAR else data


def read_range(px: np.ndarray, mode: int, a: int, b: int) -> np.ndarray:
    """Samples [a, b) of the stream as float32, converting only that range."""
    if mode == INTERLEAVED:
        return (px[a:b].astype(np.float32) - 127.5) / 127.5
    rgb = px.reshape(-1, 3); P = len(rgb)
    parts = [rgb[max(a, k * P) - k * P:min(b, (k + 1) * P) - k * P, k] for k in range(3) if max(a, k * P) < min(b, (k + 1) * P)]
    return (np.concatenate(parts).astype(np.float32) - 127.5) / 127.5


def write_range(px: np.ndarray, mode: int, a: int, b: int, x: np.ndarray):
    q = np.clip(np.rint(x * 127.5 + 127.5), 0, 255).astype(np.uint8)
    if mode == INTERLEAVED:
        px[a:b] = q; return
    rgb = px.reshape(-1, 3); P = len(rgb); pos = 0
    for k in range(3):
        lo, hi = max(a, k * P) - k * P, min(b, (k + 1) * P) - k * P
        if lo < hi:
            rgb[lo:hi, k] = q[pos:pos + hi - lo]; pos += hi - lo


class Step:
    """The bytes of [lo, lo+len(data)) that are *not* currently in the live image for one history edge.

    Moving across the edge in either direction is the same operation: swap this slice with the live
    image. So an edit costs only the region it changed, once, instead of a whole-image copy.
    """
    __slots__ = ("lo", "data")

    def __init__(self, lo: int, data: np.ndarray):
        self.lo, self.data = lo, data

    def swap(self, px: np.ndarray):
        region = px[self.lo:self.lo + len(self.data)]
        old = region.copy()
        region[:] = self.data
        self.data = old


class Node:
    """One state in the history tree. `step` converts between the parent's state and this one."""
    __slots__ = ("id", "parent", "children", "info", "step", "redo_child")

    def __init__(self, id: int, parent: "Node | None", info: dict, step: Step | None):
        self.id, self.parent, self.info, self.step = id, parent, info, step
        self.children: list[Node] = []
        self.redo_child: Node | None = None  # which branch Redo follows


class Document:
    def __init__(self):
        self.live = np.zeros(0, np.uint8)  # the live image, flat RGB
        self.original = self.live
        self._reset_history()
        self.size = (0, 0)  # w, h
        self.path: Path | None = None  # where Save writes to
        self.source_name = ""
        self.mode = INTERLEAVED
        self.dirty = False

    def _reset_history(self):
        self.root = self.node = Node(0, None, {"name": "Original"}, None)
        self.nodes: dict[int, Node] = {0: self.root}
        self._next_id = 1

    @property
    def pixels(self) -> np.ndarray:
        return self.live

    @property
    def loaded(self) -> bool:
        return self.pixels.size > 0

    def open(self, path: str):
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            im = im.convert("RGB")
            self.size = im.size
            px = np.asarray(im, dtype=np.uint8).reshape(-1).copy()
        self.original = px.copy()
        self.live = px; self._reset_history()
        self.source_name = Path(path).name
        self.path = None  # first Save always asks, so the source is never overwritten by accident
        self.dirty = False

    def save(self, path: str, adopt: bool):
        w, h = self.size
        im = Image.fromarray(self.pixels.reshape(h, w, 3), "RGB")
        kw = {"quality": 95, "subsampling": 0} if Path(path).suffix.lower() in (".jpg", ".jpeg") else {}
        im.save(path, **kw)
        if adopt:
            self.path = Path(path)
            self.dirty = False

    # ---- editing
    def stream(self, pixels: np.ndarray | None = None) -> np.ndarray:
        return to_stream(self.pixels if pixels is None else pixels, self.mode)

    def compute(self, eff: Effect, params: dict, sel: tuple[int, int] | None,
                base: np.ndarray | None = None, mode: int | None = None, keep_from: int | None = None,
                keep_rows: int = 0) -> np.ndarray:
        """Return new pixel data with `eff` applied to `sel` (or everything). Pure.

        `base` (which this call then owns and modifies) and `mode` let a background thread work on a
        snapshot while the document stays free to change. `keep_from` runs the effect on all of `sel` but only
        keeps the result from that sample onwards: the part before it is a lead-in that lets echoes, reverb and
        filters build up their tails, as they would have in a full run."""
        mode = self.mode if mode is None else mode
        out = self.live.copy() if base is None else base
        if isinstance(sel, Rect):
            # the effect sees the rectangle as its own small picture. `keep_rows` leading rows are a lead-in:
            # they are processed (so tails build up) but their original pixels are kept.
            seg = rect_read(out, mode, self.size, sel)
            res = np.nan_to_num(np.asarray(eff.fn(seg, SR, **params), dtype=np.float32), nan=0.0, posinf=1.0, neginf=-1.0)
            res = np.pad(res, (0, max(len(seg) - len(res), 0)))[:len(seg)]
            if keep_rows:
                planes = 3 if mode == PLANAR else 1
                shape = (planes, sel.h, len(seg) // (planes * sel.h))  # (plane, row, samples in a row)
                res = res.reshape(shape).copy()
                res[:, :keep_rows] = seg.reshape(shape)[:, :keep_rows]
                res = res.ravel()
            rect_write(out, mode, self.size, sel, res)
            return out
        a, b = sel if sel and sel[1] - sel[0] > 1 else (0, out.size)
        seg = read_range(out, mode, a, b)
        res = np.asarray(eff.fn(seg, SR, **params), dtype=np.float32)
        res = np.nan_to_num(res, nan=0.0, posinf=1.0, neginf=-1.0)  # a misbehaving effect must never corrupt the image
        if len(res) < len(seg):
            res = np.pad(res, (0, len(seg) - len(res)))
        lo = 0 if keep_from is None else min(max(keep_from - a, 0), len(seg))
        if lo < len(seg):
            write_range(out, mode, a + lo, b, res[lo:len(seg)])
        return out

    def commit(self, pixels: np.ndarray, info: dict):
        """Make `pixels` the live image as a new history node under the current one.

        Anything that was undone stays in the tree as a side branch; nothing is ever discarded
        except by the memory budget in _trim().
        """
        changed = np.flatnonzero(pixels != self.live)
        lo, hi = (int(changed[0]), int(changed[-1]) + 1) if changed.size else (0, 0)
        n = Node(self._next_id, self.node, info, Step(lo, self.live[lo:hi].copy()))  # keep only what gets overwritten
        self._next_id += 1
        self.node.children.append(n); self.node.redo_child = n
        self.nodes[n.id] = n
        self.live = pixels
        self.node = n
        self._trim()
        self.dirty = True

    def _path_ids(self) -> set[int]:
        out, n = set(), self.node
        while n:
            out.add(n.id); n = n.parent
        return out

    def _trim(self):
        """Over budget: drop the oldest branch tips that are not on the way to the current state."""
        total = sum(len(n.step.data) for n in self.nodes.values() if n.step)
        while len(self.nodes) > 2 and (len(self.nodes) > MAX_STEPS or total > MAX_HISTORY_BYTES):
            on_path = self._path_ids()
            tips = [n for n in self.nodes.values() if not n.children and n.id not in on_path]
            if tips:
                t = min(tips, key=lambda n: n.id)
                t.parent.children.remove(t)
                if t.parent.redo_child is t:
                    t.parent.redo_child = t.parent.children[-1] if t.parent.children else None
                total -= len(t.step.data); del self.nodes[t.id]
                continue
            # only a straight line is left: the oldest state becomes unreachable, so the next one is the new base
            c = self.root.children[0]
            total -= len(c.step.data)
            del self.nodes[self.root.id]
            c.parent = None; c.step = None; c.info = {"name": "(earlier steps trimmed)"}
            self.root = c

    def goto(self, node_id: int) -> bool:
        """Move the live image to any state in the tree (up to the common ancestor, then down)."""
        target = self.nodes.get(node_id)
        if target is None or target is self.node:
            return False
        ancestors = set()
        n = self.node
        while n:
            ancestors.add(n.id); n = n.parent
        down, n = [], target
        while n.id not in ancestors:
            down.append(n); n = n.parent
        while self.node is not n:  # climb: step back across each edge
            self.node.step.swap(self.live)
            self.node.parent.redo_child = self.node
            self.node = self.node.parent
        for d in reversed(down):  # descend
            d.step.swap(self.live)
            self.node = d
        self.dirty = True
        return True

    def undo(self) -> bool:
        return self.node.parent is not None and self.goto(self.node.parent.id)

    def redo(self) -> bool:
        n = self.node.redo_child or (self.node.children[-1] if self.node.children else None)
        return n is not None and self.goto(n.id)

    def entries(self) -> list[dict]:
        """All history states in creation order. rel: 0 = current, 1 = leads to current, 2 = other branch."""
        path = self._path_ids()
        return [{"id": n.id, "parent": n.parent.id if n.parent else None, "info": n.info,
                 "rel": 0 if n is self.node else 1 if n.id in path else 2}
                for n in sorted(self.nodes.values(), key=lambda n: n.id)]

    def history_bytes(self) -> int:
        return sum(len(n.step.data) for n in self.nodes.values() if n.step)

    def revert(self):
        self.commit(self.original.copy(), {"name": "Revert to original"})

    @property
    def can_undo(self): return self.node.parent is not None
    @property
    def can_redo(self): return bool(self.node.children)

    def selection_rects(self, sel) -> list:
        return [sel] if isinstance(sel, Rect) and self.loaded else []

    def selection_runs(self, sel):
        """The selection as sample runs in stream coordinates: (starts, ends) arrays. A Rect is one run per row
        (per plane in planar mode)."""
        if not sel or not self.loaded:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        if not isinstance(sel, Rect):
            return np.array([sel[0]], np.int64), np.array([sel[1]], np.int64)
        W, H = self.size
        y = np.arange(sel.y0, sel.y1, dtype=np.int64)
        if self.mode == INTERLEAVED:
            return (y * W + sel.x0) * 3, (y * W + sel.x1) * 3
        base = (np.arange(3, dtype=np.int64)[:, None] * (W * H) + y[None, :] * W).ravel()
        return base + sel.x0, base + sel.x1

    def selection_pixel_ranges(self, sel):
        """Map a sample range to [(pixel_start, pixel_end)) ranges for highlighting."""
        if not sel or not self.loaded or isinstance(sel, Rect):
            return []
        a, b = sel
        if self.mode == INTERLEAVED:
            return [(a // 3, -(-b // 3))]
        n = self.size[0] * self.size[1]
        out = []
        for k in range(3):
            lo, hi = max(a, k * n), min(b, (k + 1) * n)
            if lo < hi:
                out.append((lo - k * n, hi - k * n))
        return out
