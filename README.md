# Databender
[Disclaimer: this project is entirely vibecoded and lightly tested. As I continue working on it I try to get better understanding of the code underneath]

Databending for images, Audacity-style. The image's pixel data is treated as an audio
stream: pick a range on the waveform, apply an effect (echo, phaser, bitcrusher, …) and
watch the image change live.

![Databender main window](screnshot_1.png)
## Run

Requires **Python 3.10+** (with `venv`/`pip`) and a desktop session.

```bash
git clone <repo-url> databender && cd databender
./run.sh                 # first run creates .venv and installs dependencies
./run.sh photo.jpg       # or open an image directly
```

Manual alternative (Windows/macOS or if you prefer):

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m databender                 # Windows: .venv\Scripts\python
```

On some Linux distros you may need `python3-venv` and the Qt xcb libraries (`libxcb-cursor0` on Debian/Ubuntu).

## Use

- **Open** an image (Ctrl+O or drag it in). **Drag on the image** or **on the waveform** to select a range (on the image, a selection runs in reading order, left-to-right then top-to-bottom, like the data itself). Click to clear. The outline shows what's selected. No selection = whole image.
- **Hold Space and drag** (or middle-drag) to pan the image; wheel zooms.
- **Double-click** an effect on the right (or Apply…). Tweak sliders with live preview, OK to commit.
- **🎲 Randomize** (Ctrl+Shift+R) applies a random effect with random settings to the selection. Undo (Ctrl+Z) if you don't like it.
- **Ctrl+C** copies the selection (or the whole image) to the clipboard as a picture, ready to paste into other apps. Unselected ends of the first and last row are transparent.
- Ctrl+R repeats the last effect. On the waveform: wheel zooms, Shift+wheel / middle-drag pans, double-click fits.
- **Data** menu: interleaved RGB (classic) or planar (R, then G, then B) interpretation.
- Save never overwrites your original unless you choose it: first Save asks for a path; *Save a Copy* leaves the current file untouched.

## Tape emulation

*Tape & Analog → Tape Recording* simulates recording the image to cassette/reel and playing it back. Start from a preset
(Fresh cassette, Old mixtape, Dolby mismatch, Dubbed five times, Basement find, …) and tweak:

- **Recording**: tape type, *which machine recorded it* (Nakamichi-class, AKAI-class, JVC-class, boombox, portable recorder), recording quality, record level (overload/saturation). Each machine sets a treble ceiling, noise floor, built-in wow/flutter, overload margin and head alignment (my own rough approximations, not measured specs).
- **Noise reduction**: what it was *recorded* with vs *played back* with (Off / Dolby B / Dolby C) plus a playback-level error. Matched Dolby cuts hiss; a mismatch makes it too bright or too dull.
- **Tape condition**: age, physical damage, hiss, dropouts, wow, flutter. Age and damage also add treble loss and print-through ghosts.
- **Playback**: *which machine plays it* (recording on one machine and playing on another can add a speed mismatch that slants the rows; it's off unless you enable *Machine speed difference*), head quality, azimuth error, extra speed error.
- **Passes**: how many times it was re-recorded onto another tape, and extra plays (head/oxide wear).

Good to know: colour is stored at ~14.7 kHz in the "audio", so lost treble = lost colour. By default timing errors (wow, flutter, speed)
snap to whole pixels so they shear the picture without rotating the hue; switch *Timing errors* to "Rotate hue freely" for rainbow chaos.
Several generations on large images can take a few seconds per preview; untick *Live preview* if it feels slow.
