"""Audio-style effects that operate on a 1-D float32 stream in [-1, 1].

Every effect is `fn(x, sr, **params) -> ndarray` and must return an array the
same length as `x` (the caller crops/pads defensively, but length-preserving
effects give the most predictable image results).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.signal import lfilter

SR = 44100
DECIMALS = 3  # precision of every non-integer parameter, shared by the dialogs and Randomize


@dataclass
class Param:
    key: str
    label: str
    default: float
    min: float = 0.0
    max: float = 100.0
    step: float = 1.0
    suffix: str = ""
    integer: bool = False
    choices: list[str] | None = None  # if set, value is the choice index
    group: str = ""  # section heading shown above this parameter in the dialog
    help: str = ""  # one-line explanation shown as a tooltip and in the help page (see helptext.py)


@dataclass
class Effect:
    name: str
    category: str
    fn: Callable
    params: list[Param] = field(default_factory=list)
    presets: dict[str, dict] = field(default_factory=dict)  # named parameter sets offered in the dialog
    info: str = ""  # optional hand-written HTML guide; otherwise the help page is generated from `summary` and the Param.help texts
    summary: str = ""  # what the effect does, in picture terms (see helptext.py)
    audio: str = ""  # what it is for in ordinary audio work (see helptext.py)


EFFECTS: list[Effect] = []


def effect(name: str, category: str, params: list[Param] | None = None, presets: dict[str, dict] | None = None,
           info: str = ""):
    def deco(fn):
        EFFECTS.append(Effect(name, category, fn, params or [], presets or {}, info))
        return fn
    return deco


# ---------------------------------------------------------------- helpers

def _biquad(kind: str, f0: float, sr: int, q: float = 0.707, gain_db: float = 0.0):
    f0 = float(np.clip(f0, 10, sr / 2 - 100))
    w0 = 2 * np.pi * f0 / sr
    cs, sn = np.cos(w0), np.sin(w0)
    alpha = sn / (2 * max(q, 1e-3))
    A = 10 ** (gain_db / 40)
    if kind == "lowpass":
        b = [(1 - cs) / 2, 1 - cs, (1 - cs) / 2]; a = [1 + alpha, -2 * cs, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cs) / 2, -(1 + cs), (1 + cs) / 2]; a = [1 + alpha, -2 * cs, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0, -alpha]; a = [1 + alpha, -2 * cs, 1 - alpha]
    elif kind == "notch":
        b = [1, -2 * cs, 1]; a = [1 + alpha, -2 * cs, 1 - alpha]
    elif kind == "allpass":
        b = [1 - alpha, -2 * cs, 1 + alpha]; a = [1 + alpha, -2 * cs, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * A, -2 * cs, 1 - alpha * A]; a = [1 + alpha / A, -2 * cs, 1 - alpha / A]
    elif kind in ("lowshelf", "highshelf"):
        beta = 2 * np.sqrt(A) * (sn / 2 * np.sqrt(2))
        if kind == "lowshelf":
            b = [A * ((A + 1) - (A - 1) * cs + beta), 2 * A * ((A - 1) - (A + 1) * cs),
                 A * ((A + 1) - (A - 1) * cs - beta)]
            a = [(A + 1) + (A - 1) * cs + beta, -2 * ((A - 1) + (A + 1) * cs),
                 (A + 1) + (A - 1) * cs - beta]
        else:
            b = [A * ((A + 1) + (A - 1) * cs + beta), -2 * A * ((A - 1) + (A + 1) * cs),
                 A * ((A + 1) + (A - 1) * cs - beta)]
            a = [(A + 1) - (A - 1) * cs + beta, 2 * ((A - 1) - (A + 1) * cs),
                 (A + 1) - (A - 1) * cs - beta]
    else:
        raise ValueError(kind)
    b = np.array(b, dtype=np.float64); a = np.array(a, dtype=np.float64)
    return b / a[0], a / a[0]


def _filt(x, kind, f0, sr, q=0.707, gain_db=0.0):
    b, a = _biquad(kind, f0, sr, q, gain_db)
    return lfilter(b, a, x).astype(np.float32)


def _comb(x: np.ndarray, d: int, g: float) -> np.ndarray:
    """y[n] = x[n] + g*y[n-d], computed block-wise so it stays vectorised."""
    d = max(int(d), 16)
    y = x.astype(np.float32).copy()
    for s in range(d, len(x), d):
        e = min(s + d, len(x))
        y[s:e] += g * y[s - d:e - d]
    return y


def _allpass(x: np.ndarray, d: int, g: float) -> np.ndarray:
    """y[n] = -g*x[n] + x[n-d] + g*y[n-d]."""
    y = (-g * x).astype(np.float32)
    for s in range(0, len(x), d):
        e = min(s + d, len(x))
        if s >= d:
            y[s:e] += x[s - d:e - d] + g * y[s - d:e - d]
    return y


def _blockwise(x, sr, block, coeff_for_block, stages=1):
    """Run time-varying biquads: coeffs are updated every `block` samples."""
    y = x.astype(np.float64)
    zis = [np.zeros(2) for _ in range(stages)]
    out = np.empty_like(y)
    for i, s in enumerate(range(0, len(y), block)):
        seg = y[s:s + block]
        b, a = coeff_for_block(i, s + len(seg) / 2)
        for k in range(stages):
            seg, zis[k] = lfilter(b, a, seg, zi=zis[k])
        out[s:s + block] = seg
    return out.astype(np.float32)


def _delayed(x, delay_samples):
    n = np.arange(len(x), dtype=np.float64)
    return np.interp(np.clip(n - delay_samples, 0, None), n, x).astype(np.float32)


def _rng(seed): return np.random.default_rng(int(seed))


# ----------------------------------------------------- Volume & dynamics

@effect("Amplify", "Volume", [Param("db", "Amplification", 6, -60, 60, 0.5, " dB")])
def amplify(x, sr, db):
    return x * 10 ** (db / 20)


@effect("Normalize", "Volume", [Param("peak", "Peak level", -1, -40, 0, 0.5, " dB")])
def normalize(x, sr, peak):
    m = np.max(np.abs(x)) or 1.0
    return x * (10 ** (peak / 20) / m)


@effect("Fade In", "Volume")
def fade_in(x, sr):
    return x * np.linspace(0, 1, len(x), dtype=np.float32)


@effect("Fade Out", "Volume")
def fade_out(x, sr):
    return x * np.linspace(1, 0, len(x), dtype=np.float32)


@effect("Tremolo", "Volume", [Param("rate", "Rate", 8, 0.01, 2000, 0.5, " Hz"),
                              Param("depth", "Depth", 80, 0, 100, 1, " %")])
def tremolo(x, sr, rate, depth):
    t = np.arange(len(x)) / sr
    lfo = 1 - (depth / 100) * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t))
    return (x * lfo).astype(np.float32)


@effect("Limiter / Hard Clip", "Volume", [Param("thr", "Threshold", 0.5, 0.01, 1, 0.01)])
def hard_clip(x, sr, thr):
    return np.clip(x, -thr, thr)


# ------------------------------------------------------------ EQ / filters

@effect("Bass and Treble", "EQ & Filters",
        [Param("bass", "Bass", 9, -30, 30, 0.5, " dB"), Param("treble", "Treble", 0, -30, 30, 0.5, " dB"),
         Param("bf", "Bass corner", 250, 20, 2000, 10, " Hz"), Param("tf", "Treble corner", 4000, 500, 20000, 100, " Hz")])
def bass_treble(x, sr, bass, treble, bf, tf):
    y = _filt(x, "lowshelf", bf, sr, gain_db=bass)
    return _filt(y, "highshelf", tf, sr, gain_db=treble)


@effect("Low-pass Filter", "EQ & Filters",
        [Param("f", "Cutoff", 1000, 20, 20000, 10, " Hz"), Param("q", "Resonance (Q)", 0.707, 0.1, 20, 0.1)])
def lowpass(x, sr, f, q):
    return _filt(x, "lowpass", f, sr, q)


@effect("High-pass Filter", "EQ & Filters",
        [Param("f", "Cutoff", 1000, 20, 20000, 10, " Hz"), Param("q", "Resonance (Q)", 0.707, 0.1, 20, 0.1)])
def highpass(x, sr, f, q):
    return _filt(x, "highpass", f, sr, q)


@effect("Band-pass Filter", "EQ & Filters",
        [Param("f", "Center", 1000, 20, 20000, 10, " Hz"), Param("q", "Q", 1.0, 0.1, 50, 0.1)])
def bandpass(x, sr, f, q):
    return _filt(x, "bandpass", f, sr, q)


@effect("Notch Filter", "EQ & Filters",
        [Param("f", "Frequency", 1000, 20, 20000, 10, " Hz"), Param("q", "Q", 1.0, 0.1, 50, 0.1)])
def notch(x, sr, f, q):
    return _filt(x, "notch", f, sr, q)


@effect("Parametric EQ (Peak)", "EQ & Filters",
        [Param("f", "Frequency", 1000, 20, 20000, 10, " Hz"), Param("q", "Q", 1.0, 0.1, 50, 0.1),
         Param("g", "Gain", 12, -40, 40, 0.5, " dB")])
def peak_eq(x, sr, f, q, g):
    return _filt(x, "peak", f, sr, q, g)


# --------------------------------------------------------- Delay & reverb

@effect("Echo", "Delay & Reverb",
        [Param("delay", "Delay time", 0.1, 0.001, 5, 0.01, " s"), Param("decay", "Decay", 0.5, 0, 0.98, 0.01)])
def echo(x, sr, delay, decay):
    return _comb(x, int(delay * sr), decay)


@effect("Reverb", "Delay & Reverb",
        [Param("room", "Room size", 70, 10, 400, 5, " %"), Param("fb", "Reverb time", 0.8, 0.1, 0.97, 0.01),
         Param("wet", "Wet", 50, 0, 100, 1, " %")])
def reverb(x, sr, room, fb, wet):
    k = room / 100
    combs = [int(d * k) for d in (1557, 1617, 1491, 1422, 1277, 1356)]
    w = sum(_comb(x, d, fb) for d in combs) / len(combs)
    for d in (225, 556, 441):
        w = _allpass(w, max(int(d * k), 16), 0.5)
    m = wet / 100
    return (1 - m) * x + m * w


@effect("Flanger", "Delay & Reverb",
        [Param("rate", "Rate", 0.5, 0.01, 50, 0.05, " Hz"), Param("depth", "Depth", 3, 0.1, 30, 0.1, " ms"),
         Param("fb", "Mix", 70, 0, 100, 1, " %")])
def flanger(x, sr, rate, depth, fb):
    n = np.arange(len(x))
    d = depth * sr / 1000 * (0.5 + 0.5 * np.sin(2 * np.pi * rate * n / sr))
    idx = np.clip(n - d, 0, None)
    return x + (fb / 100) * np.interp(idx, n, x).astype(np.float32)


@effect("Vibrato", "Delay & Reverb",
        [Param("rate", "Rate", 5, 0.01, 100, 0.1, " Hz"), Param("depth", "Depth", 2, 0.1, 100, 0.1, " ms")])
def vibrato(x, sr, rate, depth):
    n = np.arange(len(x))
    d = depth * sr / 1000 * (0.5 + 0.5 * np.sin(2 * np.pi * rate * n / sr))
    return np.interp(np.clip(n - d, 0, None), n, x).astype(np.float32)


# ------------------------------------------------------------- Modulation

@effect("Phaser", "Modulation",
        [Param("stages", "Stages", 4, 1, 12, 1, "", True), Param("rate", "LFO rate", 0.4, 0.01, 20, 0.05, " Hz"),
         Param("depth", "Depth", 80, 0, 100, 1, " %"), Param("fb", "Dry/wet", 50, 0, 100, 1, " %")])
def phaser(x, sr, stages, rate, depth, fb):
    def coeff(i, c):
        lfo = 0.5 + 0.5 * np.sin(2 * np.pi * rate * c / sr)
        f = 200 * (4000 / 200) ** (lfo * depth / 100)
        return _biquad("allpass", f, sr, 0.7)
    wet = _blockwise(x, sr, 256, coeff, int(stages))
    m = fb / 100
    return (1 - m) * x + m * wet


@effect("Wahwah", "Modulation",
        [Param("rate", "LFO rate", 1.5, 0.01, 20, 0.05, " Hz"), Param("depth", "Depth", 70, 0, 100, 1, " %"),
         Param("res", "Resonance", 2.5, 0.5, 15, 0.1), Param("freq", "Base freq", 250, 50, 2000, 10, " Hz")])
def wahwah(x, sr, rate, depth, res, freq):
    def coeff(i, c):
        lfo = 0.5 + 0.5 * np.sin(2 * np.pi * rate * c / sr)
        return _biquad("bandpass", freq * (1 + 8 * lfo * depth / 100), sr, res)
    wet = _blockwise(x, sr, 256, coeff)
    return wet * (1 + res * 0.5)


@effect("Ring Modulator", "Modulation", [Param("f", "Frequency", 440, 1, 20000, 1, " Hz")])
def ring(x, sr, f):
    return (x * np.sin(2 * np.pi * f * np.arange(len(x)) / sr)).astype(np.float32)


# ------------------------------------------------------------- Distortion

@effect("Distortion", "Distortion", [Param("drive", "Drive", 20, 0, 60, 0.5, " dB")])
def distortion(x, sr, drive):
    return np.tanh(x * 10 ** (drive / 20)).astype(np.float32)


@effect("Wavefolder", "Distortion", [Param("drive", "Fold amount", 4, 1, 40, 0.1)])
def wavefold(x, sr, drive):
    return np.sin(x * drive * np.pi / 2).astype(np.float32)


@effect("Bitcrusher", "Distortion", [Param("bits", "Bit depth", 4, 1, 16, 1, "", True)])
def bitcrush(x, sr, bits):
    levels = 2 ** (int(bits) - 1)
    return (np.round(x * levels) / levels).astype(np.float32)


@effect("Sample-rate Reducer", "Distortion", [Param("factor", "Hold samples", 8, 1, 4000, 1, "", True)])
def decimate(x, sr, factor):
    f = max(int(factor), 1)
    return np.repeat(x[::f], f)[:len(x)]


# --------------------------------------------------- Databending specials

@effect("Invert", "Special")
def invert(x, sr):
    return -x


@effect("Reverse", "Special")
def reverse(x, sr):
    return x[::-1].copy()


@effect("Shift (Rotate)", "Special", [Param("amt", "Amount", 10, -100, 100, 0.5, " %")])
def shift(x, sr, amt):
    return np.roll(x, int(len(x) * amt / 100))


@effect("Change Speed (tile/crop)", "Special", [Param("f", "Speed factor", 1.5, 0.1, 16, 0.05, "x")])
def speed(x, sr, f):
    n = len(x)
    r = np.interp(np.arange(0, n - 1, f), np.arange(n), x).astype(np.float32)
    reps = int(np.ceil(n / max(len(r), 1)))
    return np.tile(r, reps)[:n]


@effect("Repeat Beginning", "Special", [Param("pct", "Length of loop", 10, 0.01, 100, 0.1, " %")])
def repeat(x, sr, pct):
    k = max(int(len(x) * pct / 100), 1)
    return np.tile(x[:k], int(np.ceil(len(x) / k)))[:len(x)]


@effect("Shuffle Chunks", "Special",
        [Param("chunk", "Chunk size", 4096, 3, 200000, 3, " smp", True), Param("seed", "Seed", 1, 0, 9999, 1, "", True)])
def shuffle(x, sr, chunk, seed):
    c = max(int(chunk), 1)
    parts = [x[i:i + c] for i in range(0, len(x), c)]
    order = _rng(seed).permutation(len(parts))
    return np.concatenate([parts[i] for i in order])


@effect("Swap Halves of Chunks", "Special", [Param("chunk", "Chunk size", 3000, 3, 200000, 3, " smp", True)])
def swap_chunks(x, sr, chunk):
    c = max(int(chunk), 1); y = x.copy()
    for s in range(0, len(x) - 2 * c + 1, 2 * c):
        y[s:s + c], y[s + c:s + 2 * c] = x[s + c:s + 2 * c], x[s:s + c]
    return y


@effect("Add Noise", "Special",
        [Param("amt", "Amount", 20, 0, 100, 1, " %"), Param("seed", "Seed", 1, 0, 9999, 1, "", True)])
def noise(x, sr, amt, seed):
    return x + (amt / 100) * _rng(seed).uniform(-1, 1, len(x)).astype(np.float32)


@effect("Silence (mid-grey)", "Special")
def silence(x, sr):
    return np.zeros_like(x)


@effect("Sparse Glitch", "Special",
        [Param("p", "Hit probability", 0.5, 0.01, 20, 0.01, " %"), Param("run", "Run length", 200, 1, 20000, 1, " smp", True),
         Param("seed", "Seed", 1, 0, 9999, 1, "", True)])
def sparse_glitch(x, sr, p, run, seed):
    r = _rng(seed); y = x.copy()
    n = int(len(x) * p / 100 / max(run, 1)) + 1
    for s in r.integers(0, len(x), n):
        e = min(s + int(run), len(x))
        y[s:e] = y[s]
    return y


# ---------------------------------------------------------------- spectral helper

def _spectral(x, fn, n=2048, pos=None, chunk=1024):
    """Overlap-add STFT: x -> frames -> fn(spectrum, frame_idx) -> x. Length-preserving, chunked for memory.

    `pos(idx)` may remap which input sample each output frame is analysed from (time warping)."""
    L = len(x)
    if L < n:
        return x.copy()
    hop = n // 4
    win = np.hanning(n + 1)[:-1].astype(np.float32)  # periodic Hann; hop n/4 overlaps sum to 1.5
    xp = np.pad(x.astype(np.float32), (n, n + hop))
    view = np.lib.stride_tricks.sliding_window_view(xp, n)
    nframes = (len(xp) - n) // hop + 1
    out = np.zeros(len(xp), np.float32)
    for c0 in range(0, nframes, chunk):
        idx = np.arange(c0, min(c0 + chunk, nframes))
        p = np.clip(pos(idx) if pos else idx * hop, 0, len(xp) - n).astype(np.int64)
        S = fn(np.fft.rfft(view[p] * win, axis=1), idx)
        fr = (np.fft.irfft(S, n, axis=1) * win).astype(np.float32)
        for j, i in enumerate(idx):
            out[i * hop:i * hop + n] += fr[j]
    return out[n:n + L] / 1.5


_WINDOWS = ["512", "1024", "2048", "4096", "8192", "16384"]


def _bytes(x):  # float stream -> the uint8 values it came from
    return np.clip(np.rint(x * 127.5 + 127.5), 0, 255).astype(np.uint8)


def _unbytes(b):
    return (b.astype(np.float32) - 127.5) / 127.5


def _env(x, sr, ms):  # smoothed amplitude envelope
    a = 1 - np.exp(-1 / max(ms / 1000 * sr, 1))
    return lfilter([a], [1, -(1 - a)], np.abs(x)).astype(np.float32) + 1e-6


# ----------------------------------------------------- Spectral (STFT based)

@effect("Paulstretch", "Spectral",
        [Param("stretch", "Stretch factor", 8, 1, 60, 0.5, "x"), Param("win", "Window", 4, 0, 5, 1, "", True, _WINDOWS)])
def paulstretch(x, sr, stretch, win):
    n = int(_WINDOWS[int(win)]); rng = np.random.default_rng(1)
    # random phases spread each frame's energy, so overlap-add comes out ~half as loud; x2 restores the level
    return 2 * _spectral(x, lambda S, i: np.abs(S) * np.exp(2j * np.pi * rng.random(S.shape).astype(np.float32)),
                         n, pos=lambda i: i * (n // 4) / stretch)


@effect("Whisper (random phase)", "Spectral",
        [Param("win", "Window", 2, 0, 5, 1, "", True, _WINDOWS), Param("seed", "Seed", 1, 0, 9999, 1, "", True)])
def whisper(x, sr, win, seed):
    rng = np.random.default_rng(int(seed))
    return 2 * _spectral(x, lambda S, i: np.abs(S) * np.exp(2j * np.pi * rng.random(S.shape).astype(np.float32)),
                         int(_WINDOWS[int(win)]))


@effect("Robotize (zero phase)", "Spectral", [Param("win", "Window", 2, 0, 5, 1, "", True, _WINDOWS)])
def robotize(x, sr, win):
    return _spectral(x, lambda S, i: np.abs(S).astype(np.complex64), int(_WINDOWS[int(win)]))


@effect("Frequency Shifter", "Spectral", [Param("hz", "Shift", 500, -8000, 8000, 10, " Hz")])
def freq_shift(x, sr, hz):
    k = int(round(hz / (sr / 2048)))

    def fn(S, i):
        if k == 0:
            return S
        out = np.zeros_like(S)
        if k > 0: out[:, k:] = S[:, :-k]
        else: out[:, :k] = S[:, -k:]
        return out
    return _spectral(x, fn)


@effect("Spectral Gate (keep peaks)", "Spectral", [Param("db", "Keep within", 20, 1, 80, 1, " dB of peak")])
def spectral_gate(x, sr, db):
    def fn(S, i):
        m = np.abs(S)
        return S * (m >= m.max(axis=1, keepdims=True) * 10 ** (-db / 20))
    return _spectral(x, fn)


@effect("Spectral Blur", "Spectral", [Param("width", "Width", 16, 1, 400, 1, " bins", True)])
def spectral_blur(x, sr, width):
    from scipy.ndimage import uniform_filter1d

    def fn(S, i):
        m = np.abs(S)
        return S * (uniform_filter1d(m, int(width), axis=1) / (m + 1e-9))
    return _spectral(x, fn)


@effect("Spectral Tilt", "Spectral", [Param("tilt", "Tilt", 6, -24, 24, 0.5, " dB/oct")])
def spectral_tilt(x, sr, tilt):
    def fn(S, i):
        k = np.arange(S.shape[1]) + 1.0
        return S * (10 ** (np.clip(tilt * np.log2(k / 16), -80, 80) / 20)).astype(np.float32)
    return _spectral(x, fn)


@effect("Frame Freeze / Stutter", "Spectral", [Param("hold", "Hold frames", 12, 1, 400, 1, "", True)])
def frame_freeze(x, sr, hold):
    h = int(hold); hop = 2048 // 4
    return _spectral(x, lambda S, i: S, pos=lambda i: (i // h) * h * hop)


# -------------------------------------------------------- Time & glitch

@effect("Stutter", "Time & Glitch",
        [Param("period", "Period", 6000, 16, 400000, 3, " smp", True), Param("grain", "Repeated grain", 25, 1, 100, 1, " % of period")])
def stutter(x, sr, period, grain):
    P = int(period); g = max(int(P * grain / 100), 1); y = x.copy()
    for s in range(0, len(x) - 1, P):
        e = min(s + P, len(x))
        y[s:e] = np.tile(x[s:s + g], int(np.ceil((e - s) / g)))[:e - s]
    return y


@effect("Reverse Chunks", "Time & Glitch", [Param("chunk", "Chunk size", 3000, 3, 400000, 3, " smp", True)])
def reverse_chunks(x, sr, chunk):
    c = max(int(chunk), 2); m = (len(x) // c) * c; y = x.copy()
    y[:m] = x[:m].reshape(-1, c)[:, ::-1].ravel(); y[m:] = x[m:][::-1]
    return y


@effect("Sort Chunks (pixel-sort)", "Time & Glitch",
        [Param("chunk", "Chunk size", 1920, 3, 400000, 3, " smp", True), Param("dir", "Order", 0, 0, 1, 1, "", True, ["Ascending", "Descending"])])
def sort_chunks(x, sr, chunk, dir):
    c = max(int(chunk), 2); m = (len(x) // c) * c; y = x.copy()
    y[:m] = np.sort(x[:m].reshape(-1, c), axis=1).ravel(); y[m:] = np.sort(x[m:])
    if dir == 1:  # descending: same sort, each chunk flipped
        y[:m] = y[:m].reshape(-1, c)[:, ::-1].ravel(); y[m:] = y[m:][::-1]
    return y


@effect("Tape Stop / Speed Ramp", "Time & Glitch", [Param("end", "Final speed", 0.1, 0.02, 4, 0.02, "x")])
def tape_stop(x, sr, end):
    n = len(x); speed = np.linspace(1, end, n)
    pos = np.mod(np.cumsum(speed) - speed[0], n)
    return np.interp(pos, np.arange(n), x).astype(np.float32)


@effect("Multi-tap Delay", "Time & Glitch",
        [Param("taps", "Taps", 6, 1, 32, 1, "", True), Param("space", "Spacing", 0.05, 0.001, 2, 0.01, " s"), Param("decay", "Decay per tap", 0.7, 0.05, 1, 0.01)])
def multitap(x, sr, taps, space, decay):
    y = x.copy()
    for k in range(1, int(taps) + 1):
        d = int(k * space * sr)
        if d >= len(x): break
        y[d:] += decay ** k * x[:-d]
    return y


@effect("Mirror", "Time & Glitch")
def mirror(x, sr):
    y = x.copy(); h = len(x) // 2
    y[len(x) - h:] = x[:h][::-1]
    return y


@effect("Swap Neighbours", "Time & Glitch", [Param("block", "Block size", 1, 1, 20000, 1, " smp", True)])
def swap_neighbours(x, sr, block):
    k = max(int(block), 1); m = (len(x) // (2 * k)) * 2 * k; y = x.copy()
    y[:m] = x[:m].reshape(-1, 2, k)[:, ::-1, :].ravel()
    return y


# ------------------------------------------------------- Bits & bytes

@effect("Bitwise Operation", "Bits & Bytes",
        [Param("op", "Operation", 0, 0, 4, 1, "", True, ["XOR", "AND", "OR", "Rotate left", "Rotate right"]),
         Param("val", "Value (rotate: bits)", 85, 0, 255, 1, "", True)])
def bitwise(x, sr, op, val):
    b = _bytes(x).astype(np.uint16); v = int(val)
    if op == 0: b ^= v
    elif op == 1: b &= v
    elif op == 2: b |= v
    else:
        r = v % 8 if op == 3 else (8 - v % 8) % 8
        b = ((b << r) | (b >> (8 - r))) & 255
    return _unbytes(b.astype(np.uint8))


@effect("Byte Add (wrap-around)", "Bits & Bytes", [Param("add", "Add", 64, -128, 127, 1, "", True)])
def byte_add(x, sr, add):
    return _unbytes((_bytes(x).astype(np.int16) + int(add)) % 256)


@effect("Wraparound Gain", "Bits & Bytes", [Param("db", "Gain", 12, -24, 40, 0.5, " dB")])
def wrap_gain(x, sr, db):
    return ((x * 10 ** (db / 20) + 1) % 2 - 1).astype(np.float32)


@effect("Rectify", "Bits & Bytes",
        [Param("mode", "Mode", 0, 0, 2, 1, "", True, ["Full-wave", "Half-wave (+)", "Half-wave (−)"])])
def rectify(x, sr, mode):
    return np.abs(x) if mode == 0 else np.maximum(x, 0) if mode == 1 else np.minimum(x, 0)


@effect("DC Offset", "Bits & Bytes", [Param("off", "Offset", 0.3, -1, 1, 0.01)])
def dc_offset(x, sr, off):
    return x + off


# ----------------------------------------------------- More Audacity-style

@effect("Chorus", "Delay & Reverb",
        [Param("voices", "Voices", 3, 1, 8, 1, "", True), Param("depth", "Depth", 6, 0.5, 40, 0.5, " ms"),
         Param("rate", "Rate", 0.8, 0.05, 20, 0.05, " Hz"), Param("mix", "Mix", 60, 0, 100, 1, " %")])
def chorus(x, sr, voices, depth, rate, mix):
    n = np.arange(len(x)); y = np.zeros(len(x), np.float32); V = int(voices)
    for v in range(V):
        d = (15 + depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate * (1 + 0.17 * v) * n / sr + v * 2.1))) * sr / 1000
        y += np.interp(np.clip(n - d, 0, None), n, x).astype(np.float32)
    m = mix / 100
    return (1 - m) * x + m * y / V


@effect("Comb Filter", "Delay & Reverb",
        [Param("ms", "Delay", 2, 0.4, 50, 0.1, " ms"), Param("fb", "Feedback", 0.85, -0.97, 0.97, 0.01)])
def comb_filter(x, sr, ms, fb):
    return _comb(x, int(ms * sr / 1000), fb)


@effect("Compressor", "Volume",
        [Param("thr", "Threshold", -20, -60, 0, 0.5, " dB"), Param("ratio", "Ratio", 4, 1, 40, 0.5, ":1"),
         Param("smooth", "Smoothing", 20, 0.1, 500, 1, " ms"), Param("makeup", "Make-up gain", 6, 0, 40, 0.5, " dB")])
def compressor(x, sr, thr, ratio, smooth, makeup):
    db = 20 * np.log10(_env(x, sr, smooth))
    gain = -np.maximum(db - thr, 0) * (1 - 1 / ratio) + makeup
    return x * (10 ** (gain / 20)).astype(np.float32)


@effect("Noise Gate", "Volume",
        [Param("thr", "Threshold", -24, -80, 0, 0.5, " dB"), Param("floor", "Gate floor", -60, -90, 0, 1, " dB"),
         Param("smooth", "Smoothing", 5, 0.1, 200, 0.5, " ms")])
def noise_gate(x, sr, thr, floor, smooth):
    open_ = (20 * np.log10(_env(x, sr, smooth)) > thr).astype(np.float32)
    g = lfilter([0.02], [1, -0.98], open_).astype(np.float32)
    return x * (10 ** (floor / 20) + (1 - 10 ** (floor / 20)) * g)


@effect("5-Band EQ", "EQ & Filters",
        [Param("b1", "100 Hz", 9, -24, 24, 0.5, " dB"), Param("b2", "400 Hz", 0, -24, 24, 0.5, " dB"),
         Param("b3", "1.5 kHz", -9, -24, 24, 0.5, " dB"), Param("b4", "5 kHz", 0, -24, 24, 0.5, " dB"),
         Param("b5", "12 kHz", 9, -24, 24, 0.5, " dB")])
def eq5(x, sr, b1, b2, b3, b4, b5):
    y = _filt(x, "lowshelf", 100, sr, gain_db=b1)
    for f, g in ((400, b2), (1500, b3), (5000, b4)):
        y = _filt(y, "peak", f, sr, 1.0, g)
    return _filt(y, "highshelf", 12000, sr, gain_db=b5)


@effect("Median Filter", "EQ & Filters", [Param("size", "Window", 9, 3, 201, 2, " smp", True)])
def median(x, sr, size):
    from scipy.signal import medfilt
    k = min(int(size) | 1, (len(x) - 1) | 1) if len(x) > 2 else 1  # kernel can't exceed the data
    return medfilt(x, k).astype(np.float32)


@effect("Differentiate (edge detect)", "EQ & Filters", [Param("gain", "Gain", 4, 0.1, 50, 0.1, "x")])
def differentiate(x, sr, gain):
    return np.diff(x, prepend=x[:1]) * gain


@effect("Noise Convolution (smear)", "Delay & Reverb",
        [Param("ms", "Length", 40, 1, 500, 1, " ms"), Param("decay", "Decay", 6, 0.5, 60, 0.5, " dB/10ms"),
         Param("mix", "Mix", 70, 0, 100, 1, " %"), Param("seed", "Seed", 1, 0, 9999, 1, "", True)])
def noise_conv(x, sr, ms, decay, mix, seed):
    from scipy.signal import fftconvolve
    L = max(int(ms * sr / 1000), 8); t = np.arange(L) / sr
    ir = np.random.default_rng(int(seed)).standard_normal(L) * 10 ** (-decay * t * 100 / 20)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    m = mix / 100
    return ((1 - m) * x + m * fftconvolve(x, ir)[:len(x)]).astype(np.float32)


@effect("Asymmetric Saturation", "Distortion",
        [Param("drive", "Drive", 12, 0, 40, 0.5, " dB"), Param("bias", "Bias", 0.4, 0, 1, 0.01)])
def asym_sat(x, sr, drive, bias):
    d = 10 ** (drive / 20)
    return (np.tanh(d * (x + bias)) - np.tanh(d * bias)).astype(np.float32)


def by_name(name: str) -> Effect:
    return next(e for e in EFFECTS if e.name == name)


def random_params(eff: Effect, rng: np.random.Generator | None = None) -> dict:
    """Random values for every parameter of `eff`, within each parameter's range."""
    rng = rng or np.random.default_rng()
    out = {}
    for p in eff.params:
        if p.choices:
            v = int(rng.integers(len(p.choices)))
        else:
            if p.min > 0 and p.max / p.min >= 50:  # wide ranges (Hz, samples): sample on a log scale
                v = float(np.exp(rng.uniform(np.log(p.min), np.log(p.max))))
            else:
                v = float(rng.uniform(p.min, p.max))
            v = int(round(v)) if p.integer else round(v, DECIMALS)
        out[p.key] = v
    return out


def random_effect(rng: np.random.Generator | None = None) -> tuple[Effect, dict]:
    rng = rng or np.random.default_rng()
    eff = EFFECTS[int(rng.integers(len(EFFECTS)))]
    return eff, random_params(eff, rng)


from . import tape  # noqa: E402,F401  (registers the tape emulation effect)
from . import helptext as _helptext  # noqa: E402

_helptext.apply(EFFECTS)
