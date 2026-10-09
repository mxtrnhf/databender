"""Tape emulation: record -> (Dolby) -> aged medium -> playback, repeated once per generation.

The image is the "audio", so every tape artefact lands on the picture. Colour lives at ~14.7 kHz (the RGB
triplet repeats every 3 samples), so lost treble means lost colour, and any speed wobble rotates the hue.
Every tape artefact lands on the picture: wow/flutter shear the rows,
dropouts punch holes, print-through leaves ghost copies, hiss adds grain and Dolby mismatches change
how much of that grain survives.
"""
from __future__ import annotations

import numpy as np

from scipy.signal import lfilter

from .effects import Param, _biquad, _env, effect

TYPES = ["Type I (ferric)", "Type II (chrome)", "Type IV (metal)"]
NR = ["Off", "Dolby B", "Dolby C"]
# Tape recorders. The numbers are my own rough approximations of each class of machine, not measured specs.
#   bw: treble ceiling (Hz)        noise: electronics noise floor (dBFS)    wow/flutter: speed instability the transport
#   always has (fraction of the 0-100 % sliders)   headroom: relative overload margin   az: head misalignment (%)
#   speed: constant speed error (%). Recording and playback errors cancel on the same machine, add up on different ones.
DECKS = [
    dict(name="Nakamichi-class (audiophile 3-head)", bw=24000, noise=-88, wow=0.05, flutter=0.02, headroom=1.15, az=0, speed=0.0),
    dict(name="AKAI-class (high end)", bw=22000, noise=-82, wow=0.10, flutter=0.05, headroom=1.10, az=2, speed=0.05),
    dict(name="JVC-class (mid-range)", bw=17500, noise=-76, wow=0.25, flutter=0.10, headroom=1.00, az=6, speed=-0.15),
    dict(name="Boombox (cheap)", bw=12000, noise=-66, wow=0.60, flutter=0.25, headroom=0.80, az=18, speed=0.60),
    dict(name="Portable recorder (dictaphone-class)", bw=7000, noise=-58, wow=1.00, flutter=0.45, headroom=0.60, az=32, speed=-1.00),
]
DECK_NAMES = [d["name"] for d in DECKS]
HEADROOM = [1.0, 1.25, 1.6]  # how hard the tape can be driven before it saturates
NOISE_OFFSET_DB = [0.0, -5.0, -8.0]  # better formulations hiss less
HF_EXTENSION = [1.0, 1.12, 1.25]  # ... and keep more treble

# (corner frequency, maximum boost in dB) per compander stage; C is two stages in series
_STAGES = {1: [(2000.0, 10.0)], 2: [(800.0, 10.0), (3500.0, 10.0)]}
_LO, _HI = -55.0, -22.0  # band level (dBFS) below which the compander boosts fully / above which it does nothing


def _lerp(a, b, t):
    return a + (b - a) * t


def _zp(x, kind, f0, sr, q=0.707, gain_db=0.0):
    """Zero-phase biquad (forward then backward): same shape as two cascaded biquads but with no delay.
    A causal filter would delay the 14.7 kHz colour signal by a fraction of a sample and rotate every hue."""
    b, a = _biquad(kind, f0, sr, q, gain_db / 2)  # the backward pass doubles the gain, so halve it here
    y = lfilter(b, a, x)
    return lfilter(b, a, y[::-1])[::-1].astype(np.float32)


def _lowpass(x, sr, fc):
    return _zp(x, "lowpass", min(fc, sr * 0.45), sr)


def _gain_db(level_db, boost):
    return boost * np.clip((_HI - level_db) / (_HI - _LO), 0, 1)


def _dolby(x, sr, mode, decode, err_db=0.0):
    """Sliding-band compander. Encoding boosts quiet treble; decoding applies the exact inverse curve,
    so tape hiss added in between is pushed down. `err_db` models a mis-calibrated playback level."""
    if mode == 0:
        return x
    grid = np.linspace(-120, 6, 700)
    out_grid = grid + _gain_db(grid, 10.0)  # envelope after encoding (monotonic, so it can be inverted)
    stages = _STAGES[mode][::-1] if decode else _STAGES[mode]
    for fc, boost in stages:
        h = _zp(x, "highpass", fc, sr)
        e = 20 * np.log10(_env(h, sr, 8))
        if decode:
            e = e + err_db
            g = 10 ** ((np.interp(e, grid + _gain_db(grid, boost), grid) - e) / 20)
        else:
            g = 10 ** (_gain_db(e, boost) / 20)
        x = x + h * (g - 1).astype(np.float32)
    return x


def _events(rng, n, sr, per_second):
    """Random event start positions (Poisson process)."""
    k = rng.poisson(max(per_second, 0) * n / sr)
    return rng.integers(0, max(n, 1), k)


def _shift(x, delay, lock):
    """Read x at (index + delay). With `lock`, delays snap to whole pixels (multiples of 3 samples), so the
    R,G,B phase is preserved and timing errors move shapes without rotating the hue."""
    n = len(x); idx = np.arange(n)
    if lock:
        return x[np.clip(idx + 3 * np.rint(delay / 3).astype(np.int64), 0, n - 1)]
    return np.interp(np.clip(idx + delay, 0, n - 1), idx, x).astype(np.float32)


def _medium(x, sr, rng, c):
    """What the tape itself does to the signal between record and playback."""
    n = len(x); idx = np.arange(n)
    # --- speed instability: wow (slow), flutter (fast) and random drift from mechanical wear
    dev = np.zeros(n)
    t = idx / sr
    if c["wow"] > 0:
        f = rng.uniform(0.5, 1.2); ph = rng.uniform(0, 2 * np.pi)
        dev += c["wow"] * 0.004 * (np.sin(2 * np.pi * f * t + ph) + 0.35 * np.sin(2 * np.pi * 0.41 * f * t + 2 * ph))
    if c["flutter"] > 0:
        f = rng.uniform(7, 14); ph = rng.uniform(0, 2 * np.pi)
        dev += c["flutter"] * 0.012 * (np.sin(2 * np.pi * f * t + ph) + 0.4 * np.sin(2 * np.pi * 2.3 * f * t))
    if c["wear"] > 0:
        knots = rng.standard_normal(n // 4096 + 3)
        dev += c["wear"] * 0.01 * np.interp(idx, np.arange(len(knots)) * 4096, knots)
    if np.any(dev):
        x = _shift(x, np.cumsum(dev), c["lock"])
    # --- oxide loss: treble disappears with age and damage
    if c["hf_loss_db"] > 0.05:
        x = _zp(x, "highshelf", 5500, sr, gain_db=-c["hf_loss_db"])
    # --- print-through: faint ghost copies from neighbouring layers of the reel (the pre-echo is stronger)
    D = int(0.45 * sr)
    if 0 < D < n and c["print_db"] < -10:
        post = 10 ** (c["print_db"] / 20)
        y = x.copy()
        y[D:] += post * x[:-D]
        y[:-D] += post * 1.6 * x[D:]
        x = y
    # --- dropouts: patches where the oxide has come off
    g = np.ones(n, np.float32)
    for s in _events(rng, n, sr, c["dropout_rate"]):
        L = int(np.exp(rng.uniform(np.log(0.0015), np.log(0.03))) * sr)
        e = min(s + max(L, 8), n)
        g[s:e] *= 1 - rng.uniform(0.35, 1.0) * np.hanning(e - s + 2)[1:-1]
    x = x * g
    # --- clicks from creases and sticky shed
    for s in _events(rng, n, sr, c["click_rate"]):
        e = min(s + int(rng.integers(2, 10)), n)
        x[s:e] += rng.choice([-1, 1]) * rng.uniform(0.3, 0.9)
    # --- hiss, tilted towards the treble like real tape noise
    if c["hiss_db"] > -90:
        noise = rng.standard_normal(n).astype(np.float32) * 10 ** (c["hiss_db"] / 20)
        x = x + _zp(noise, "highshelf", 3000, sr, gain_db=6)
    return x



INFO_HTML = """
<h3>Tape emulation: what the controls model</h3>
<p>Your picture is played as a long audio signal (3 samples per pixel: red, green, blue). Everything below happens to that
signal, which then becomes a picture again.</p>
<p><b>In audio:</b> this models an analogue cassette: gentle saturation, treble roll-off, hiss, wow and flutter (pitch wobble),
dropouts and Dolby noise reduction, and every re-recording adds more of all of it.</p>

<h4>Why pictures are touchy</h4>
<ul>
<li><b>Colour lives at the very top of the frequency range</b> (the R,G,B pattern repeats every 3 samples, about 14.7&nbsp;kHz).
Lost treble means lost colour, so worn tapes and cheap machines fade towards grey.</li>
<li><b>Audio has no clock.</b> A constant speed error slides every row sideways a little more than the last. 0.05&nbsp;% is about
1&nbsp;cent of pitch, below what most people hear (real decks are around 0.1&ndash;0.2&nbsp;% or worse), yet it leans a 2200&nbsp;px wide
image about 48&deg;. The lean grows with image width.</li>
<li>Timing errors also rotate hue. <b>Keep colours</b> snaps them to whole pixels, so shapes shear but hues survive.</li>
</ul>

<h4>Recording</h4>
<ul>
<li><b>Tape type:</b> I = most hiss and least treble headroom; II = more treble, less hiss; IV (metal) = most headroom, least hiss.</li>
<li><b>Recorded on / Played on:</b> five classes of machine. Each sets a treble ceiling, noise floor, built-in wow and flutter,
overload margin, head alignment and speed. The same machine for both cancels the speed error; two different machines do not, if you
switch on <b>Machine speed difference</b> (off by default, since it slants the picture). The numbers are my rough approximations of
each class, not measured specs.</li>
<li><b>Quality / level:</b> quality scales bandwidth up to the machine's ceiling; a high record level overloads the tape (saturation).</li>
</ul>

<h4>Noise reduction</h4>
<ul>
<li><b>Dolby B / C:</b> boosts quiet treble when recording and reverses it on playback, which pushes tape hiss down
(roughly 10&nbsp;dB for B and 20&nbsp;dB for C around 5&nbsp;kHz). It must match: B-recorded but played flat sounds harsh and bright,
flat-recorded but played with B sounds dull, and a level error makes it pump.</li>
</ul>

<h4>Tape condition</h4>
<ul>
<li><b>Age:</b> treble loss, <b>print-through</b> (faint ghost copies from neighbouring layers of the wound tape, stronger before the sound than
after) and dropouts. In reality binder breakdown, heat and humidity drive this.</li>
<li><b>Damage:</b> creases and stretch: irregular speed drift, clicks and extra dropouts.</li>
<li><b>Hiss / Dropouts:</b> the noise floor, and patches where oxide has come off.</li>
<li><b>Wow</b> is a slow speed wobble (below roughly 4&ndash;6&nbsp;Hz) and <b>flutter</b> a faster one (up to about 100&nbsp;Hz). Both shear shapes sideways.</li>
</ul>

<h4>Playback</h4>
<ul>
<li><b>Head quality / azimuth:</b> head wear and head tilt. A tilted head cancels treble, and treble is colour.</li>
<li><b>Speed error:</b> slants the rows (see above).</li>
</ul>

<h4>Passes</h4>
<ul>
<li><b>Times re-recorded:</b> every dub onto another tape adds hiss and loses treble, so colour drops with each copy.</li>
<li><b>Extra plays:</b> head and oxide wear slowly dull the treble and add dropouts.</li>
</ul>

<p><i>This is a stylised model, not a measured one. Real behaviour (print-through delay, Dolby curves, generation loss) is
simplified.</i></p>

<p>Further reading:
<a href="https://en.wikipedia.org/wiki/Wow_and_flutter_measurement">wow and flutter</a> &middot;
<a href="https://en.wikipedia.org/wiki/Compact_Cassette_tape_types_and_formulations">tape types</a> &middot;
<a href="https://www.tvtechnology.com/opinions/dolby-c-how-dolby-sees-it">Dolby C</a> &middot;
<a href="https://iasa-web.org/node/3125">print-through and storage</a> &middot;
<a href="https://en.wikipedia.org/wiki/Kansas_City_standard">how home computers used cassettes</a></p>
"""

@effect("Tape Recording (record → age → playback)", "Tape & Analog", [
    Param("tape", "Tape type", 0, 0, 2, 1, "", True, TYPES, "Recording"),
    Param("deck_rec", "Recorded on", 1, 0, 4, 1, "", True, DECK_NAMES, "Recording"),
    Param("rec_q", "Recording quality", 75, 0, 100, 1, " %", group="Recording"),
    Param("rec_lvl", "Record level (overload)", 0, -12, 12, 0.5, " dB", group="Recording"),
    Param("nr_rec", "Recorded with", 0, 0, 2, 1, "", True, NR, "Noise reduction"),
    Param("nr_play", "Played back with", 0, 0, 2, 1, "", True, NR, "Noise reduction"),
    Param("nr_err", "Playback level error", 0, -6, 6, 0.25, " dB", group="Noise reduction"),
    Param("age", "Tape age", 15, 0, 60, 0.5, " years", group="Tape condition"),
    Param("wear", "Physical damage (creases, stretch)", 20, 0, 100, 1, " %", group="Tape condition"),
    Param("hiss", "Hiss", 40, 0, 100, 1, " %", group="Tape condition"),
    Param("dropouts", "Dropouts", 20, 0, 100, 1, " %", group="Tape condition"),
    Param("wow", "Wow (slow speed wobble)", 25, 0, 100, 1, " %", group="Tape condition"),
    Param("flutter", "Flutter (fast speed jitter)", 20, 0, 100, 1, " %", group="Tape condition"),
    Param("lock", "Timing errors", 0, 0, 1, 1, "", True, ["Keep colours (snap to whole pixels)", "Rotate hue freely"], "Tape condition"),
    Param("deck_play", "Played on", 1, 0, 4, 1, "", True, DECK_NAMES, "Playback"),
    Param("play_q", "Playback head quality", 80, 0, 100, 1, " %", group="Playback"),
    Param("azimuth", "Head azimuth misalignment", 0, 0, 100, 1, " %", group="Playback"),
    Param("deck_speed", "Machine speed difference", 0, 0, 1, 1, "", True,
          ["Ignore (machines run at the same speed)", "Apply (recording vs playback machine, slants the picture)"], "Playback"),
    Param("speed", "Playback speed error", 0, -10, 10, 0.1, " %", group="Playback"),
    Param("gens", "Times re-recorded (dubbed onto another tape)", 1, 1, 8, 1, "", True, group="Passes"),
    Param("plays", "Extra plays of the final tape", 0, 0, 300, 1, "", True, group="Passes"),
    Param("seed", "Random seed", 1, 0, 9999, 1, "", True, group="Passes"),
], presets={
    "Fresh cassette (chrome, Dolby B)": dict(deck_rec=1, deck_play=1, tape=1, rec_q=90, rec_lvl=0, nr_rec=1, nr_play=1, nr_err=0, age=1, wear=0, hiss=15, dropouts=2,
                                              wow=8, flutter=6, play_q=95, azimuth=0, speed=0, gens=1, plays=0),
    "Pristine studio reel (metal, Dolby C)": dict(deck_rec=0, deck_play=0, tape=2, rec_q=100, rec_lvl=-3, nr_rec=2, nr_play=2, nr_err=0, age=0, wear=0, hiss=5, dropouts=0,
                                                   wow=3, flutter=2, play_q=100, azimuth=0, speed=0, gens=1, plays=0),
    "Old mixtape": dict(deck_rec=3, deck_play=2, tape=0, rec_q=72, rec_lvl=3, nr_rec=0, nr_play=0, nr_err=0, age=22, wear=25, hiss=50, dropouts=25,
                        wow=35, flutter=25, play_q=78, azimuth=15, speed=0, gens=1, plays=40),
    "Dolby mismatch (B recorded, played flat)": dict(deck_rec=2, deck_play=2, tape=0, rec_q=75, rec_lvl=0, nr_rec=1, nr_play=0, nr_err=0, age=8, wear=5, hiss=35, dropouts=8,
                                                      wow=15, flutter=10, play_q=80, azimuth=0, speed=0, gens=1, plays=0),
    "Dubbed five times": dict(deck_rec=1, deck_play=1, tape=1, rec_q=92, rec_lvl=0, nr_rec=1, nr_play=1, nr_err=0, age=6, wear=10, hiss=35,
                              dropouts=10, wow=15, flutter=10, play_q=92, azimuth=0, speed=0, gens=5, plays=0),
    "Worn out by playing (200 plays)": dict(deck_rec=2, deck_play=2, tape=0, rec_q=80, rec_lvl=0, nr_rec=0, nr_play=0, nr_err=0, age=15, wear=35, hiss=40, dropouts=30,
                                             wow=30, flutter=20, play_q=85, azimuth=20, speed=0, gens=1, plays=200),
    "Basement find (35 years, damp)": dict(deck_rec=3, deck_play=3, tape=0, rec_q=45, rec_lvl=4, nr_rec=0, nr_play=0, nr_err=0, age=35, wear=70, hiss=65, dropouts=55,
                                            wow=60, flutter=40, play_q=45, azimuth=35, speed=-2.5, gens=1, plays=30),
    "Chewed by the machine": dict(deck_rec=4, deck_play=4, tape=0, rec_q=40, rec_lvl=5, nr_rec=0, nr_play=0, nr_err=0, age=25, wear=100, hiss=70, dropouts=80,
                                   wow=90, flutter=70, lock=1, play_q=35, azimuth=50, speed=4, gens=2, plays=100),
}, info=INFO_HTML)
def tape(x, sr, tape, deck_rec, deck_play, rec_q, rec_lvl, nr_rec, nr_play, nr_err, age, wear, hiss, dropouts, wow, flutter, lock,
         play_q, azimuth, deck_speed, speed, gens, plays, seed):
    ty = int(tape); q = rec_q / 100; pq = play_q / 100
    dr, dp = DECKS[int(deck_rec)], DECKS[int(deck_play)]
    # each machine runs slightly off nominal speed; the same machine for both cancels it, two different ones don't.
    # Optional, because on a picture even 0.05 % slants every row (see the guide).
    net_speed = speed + (dp["speed"] - dr["speed"] if int(deck_speed) == 1 else 0.0)
    wear_p = 1 - np.exp(-plays / 120)  # head/oxide wear from repeated playing, saturating
    for gen in range(int(gens)):
        last = gen == int(gens) - 1
        rng = np.random.default_rng(int(seed) * 1000 + gen)
        pw = wear_p if last else 0.0
        c = dict(
            wow=min(wow / 100 + dr["wow"] + dp["wow"], 1.5), flutter=min(flutter / 100 + dr["flutter"] + dp["flutter"], 1.5),
            wear=wear / 100,
            hf_loss_db=min(0.2 * age + 0.1 * wear + 6 * pw, 36),
            print_db=min(-62 + 0.55 * age + 0.1 * wear, -30),
            dropout_rate=dropouts / 100 * 2.0 + 0.03 * age + wear / 100 * 0.8 + 1.0 * pw,
            click_rate=wear / 100 * 4.0 + 0.5 * pw,
            lock=int(lock) == 0,
            hiss_db=10 * np.log10(10 ** ((-72 + 50 * hiss / 100 + NOISE_OFFSET_DB[ty] + min(0.1 * age, 5)) / 10)  # tape
                                  + 10 ** (dr["noise"] / 10) + 10 ** (dp["noise"] / 10)),  # + both machines' electronics
        )
        y = _dolby(x, sr, int(nr_rec), False)
        # recording: tape saturates when driven hard; better tapes and quality extend the treble
        g = 10 ** (rec_lvl / 20); head = HEADROOM[ty] * dr["headroom"]
        y = (head / g * np.tanh(g * y / head)).astype(np.float32)
        y = _lowpass(y, sr, min(_lerp(2500, dr["bw"], q ** 1.2) * HF_EXTENSION[ty], 24000))
        y = _medium(y, sr, rng, c)
        # playback: speed error, head azimuth (treble cancellation), head quality / wear
        n = len(y); idx = np.arange(n)
        if net_speed:  # a constant speed error slants every row; the read position wraps around the end
            d = idx * (net_speed / 100)
            if int(lock) == 0:
                y = y[np.mod(idx + 3 * np.rint(d / 3).astype(np.int64), n)]
            else:
                y = np.interp(np.mod(idx + d, n), idx, y).astype(np.float32)
        az = min(azimuth + dp["az"], 100)
        if az > 0:
            y = 0.5 * (y + np.interp(idx - az / 100 * 4, idx, y).astype(np.float32))
        y = _lowpass(y, sr, _lerp(2500, dp["bw"], pq ** 1.2) * (1 - 0.45 * pw))
        x = _dolby(y, sr, int(nr_play), True, nr_err)
    return x
