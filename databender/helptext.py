"""Plain-language help for every effect and parameter: what it does and what you will see on a picture.

Kept in one place so it is easy to proof-read. `apply()` attaches the text to the effect registry; a test
checks that nothing is missing. Texts describe the picture, not the audio: the image is read as one long list
of numbers (3 per pixel: red, green, blue), row after row, so "later in the list" means "further along the
rows".
"""
from __future__ import annotations

import html

# name -> (what it does, {parameter key: what it does})
HELP: dict[str, tuple[str, dict[str, str]]] = {
    # ---------------------------------------------------------------- Volume
    "Amplify": ("Scales every value away from (or toward) mid-grey. Louder = more contrast; values past black or white are clipped.",
                {"db": "Gain. +6 dB doubles every value's distance from mid-grey; negative values wash the picture out toward grey."}),
    "Normalize": ("Stretches the contrast so the strongest value reaches a chosen level, without clipping.",
                  {"peak": "Target for the strongest value. 0 dB = full black/white; lower leaves some headroom."}),
    "Fade In": ("Ramps from mid-grey up to the original over the selection: the picture emerges from grey along the reading direction.", {}),
    "Fade Out": ("Ramps from the original down to mid-grey over the selection.", {}),
    "Tremolo": ("Pulses the contrast up and down like a wobbling volume knob: repeating bands of strong and washed-out picture.",
                {"rate": "Pulses per second of audio. Low = broad bands; high = fine stripes or moiré.",
                 "depth": "How far the contrast dips. 0 % = no change; 100 % = fully grey at the dips."}),
    "Limiter / Hard Clip": ("Cuts everything beyond a level to that level: flattens highlights and shadows, leaving a low-contrast, banded picture.",
                            {"thr": "Clipping level. 1 = no change; lower values squash more of the picture toward mid-grey."}),
    "Compressor": ("Turns down the strongest swings, then lifts everything: evens out contrast between busy and calm areas.",
                   {"thr": "Level above which values are turned down.",
                    "ratio": "How strongly. 4:1 means 4 dB over the threshold becomes 1 dB.",
                    "smooth": "How fast it reacts. Short reacts to fine detail; long reacts to broad areas.",
                    "makeup": "Gain added afterwards to bring the level back up."}),
    "Noise Gate": ("Squashes quiet passages toward mid-grey and keeps strong ones: faint detail disappears, bold areas stay.",
                   {"thr": "Level below which the gate closes.",
                    "floor": "How much the gated parts are turned down. -90 dB = fully grey.",
                    "smooth": "How quickly the gate opens and closes; short = chattery edges, long = soft transitions."}),
    # --------------------------------------------------------- EQ & Filters
    "Bass and Treble": ("Boosts or cuts the slow part of the picture (broad brightness) and the fast part (edges, fine detail, colour) separately.",
                        {"bass": "Boost or cut for slow changes: broad areas and overall brightness.",
                         "treble": "Boost or cut for fast changes: edges, fine detail and colour. Cutting it greys the picture.",
                         "bf": "Frequencies below this count as bass.",
                         "tf": "Frequencies above this count as treble."}),
    "Low-pass Filter": ("Keeps slow changes and removes fast ones: smears along the rows and, below about 14.7 kHz, strips the colour.",
                        {"f": "Frequencies above this are cut. High = subtle softening; low = heavy smear and grey.",
                         "q": "Ringing at the cutoff. About 0.7 is smooth; high values add rippling halos."}),
    "High-pass Filter": ("Keeps fast changes and removes slow ones: flat areas turn mid-grey and only edges and detail remain.",
                         {"f": "Frequencies below this are cut. Higher = only the finest detail survives.",
                          "q": "Ringing at the cutoff. About 0.7 is smooth; high values add rippling halos."}),
    "Band-pass Filter": ("Keeps only a narrow band of detail sizes: a rippled, striped texture of one scale.",
                         {"f": "Centre of the band that is kept.",
                          "q": "Narrowness. High = a thin band (regular stripes); low = a wide band."}),
    "Notch Filter": ("Removes one band and keeps the rest: deletes one particular size of detail or stripe.",
                     {"f": "Centre of the band that is removed.",
                      "q": "Narrowness of the cut. High = a thin notch; low = a wide one."}),
    "Parametric EQ (Peak)": ("One EQ knob: boosts or cuts a single band of detail sizes.",
                             {"f": "Centre of the band.", "q": "Width of the band. High = narrow.", "g": "Boost (+) or cut (−) at that band."}),
    "5-Band EQ": ("Five fixed EQ bands from broad areas (100 Hz) to fine detail and colour (12 kHz).",
                  {"b1": "Gain for the slowest changes: broad brightness.", "b2": "Gain for low-mid detail.",
                   "b3": "Gain for mid detail.", "b4": "Gain for fine detail.", "b5": "Gain for the finest detail and colour."}),
    "Median Filter": ("Replaces each value with the middle one of its neighbours: removes speckles and spikes but keeps edges (a despeckle).",
                      {"size": "Neighbourhood size in samples (3 samples = 1 pixel). Larger removes bigger spikes but smooths more."}),
    "Differentiate (edge detect)": ("Outputs how fast values change: flat areas go mid-grey and edges pop out, like an edge detector.",
                                    {"gain": "Strength of the edge response. Higher = brighter, harsher edges."}),
    # -------------------------------------------------------- Delay & Reverb
    "Echo": ("Repeats the data later on, fading each repeat: ghost copies of the picture shifted along the reading direction.",
             {"delay": "Gap between repeats. 0.1 s = 4,410 samples, so ghosts land that far further along (about a row or two on a normal image).",
              "decay": "How much of each repeat survives. 0 = none; near 1 = long trails of ghosts."}),
    "Reverb": ("Thousands of tiny overlapping echoes: a washed-out smear along the rows that mixes neighbouring colours.",
               {"room": "Size of the echoes. Bigger = longer smear.",
                "fb": "How long it keeps ringing. Higher = longer, more washed out.",
                "wet": "Mix of smeared signal against the original."}),
    "Flanger": ("Mixes the picture with a copy whose timing wobbles slightly: rippled, wavy doubling.",
                {"rate": "Speed of the wobble.", "depth": "How far the copy swings. More = bigger waves.", "fb": "Amount of wobbling copy mixed in."}),
    "Vibrato": ("Wobbles the timing itself, with no mixing: lines waver sideways like a heat haze.",
                {"rate": "Speed of the wobble.", "depth": "How far it swings. More = bigger waves."}),
    "Chorus": ("Mixes in several slightly wobbling copies: a soft, thick, shimmering doubling.",
               {"voices": "Number of copies.", "depth": "Size of each copy's wobble.", "rate": "Speed of the wobble.", "mix": "Amount of copies against the original."}),
    "Comb Filter": ("A very short feedback echo that carves a regular pattern into the detail: fine repeating stripes and ringing texture.",
                    {"ms": "Echo delay. Short values give fine stripes; it also sets the spacing of the pattern.",
                     "fb": "Feedback strength. Near ±1 = strong ringing; negative flips the pattern."}),
    "Multi-tap Delay": ("Several evenly spaced echoes with no feedback: a row of ghost copies.",
                        {"taps": "Number of echoes.", "space": "Gap between echoes.", "decay": "How much quieter each successive echo is."}),
    "Noise Convolution (smear)": ("Smears the picture through a burst of random noise: a blurred, grainy, colourful smear, like a noisy reverb.",
                                  {"ms": "Length of the noise burst. Longer = wider smear.",
                                   "decay": "How quickly the burst fades. Higher = tighter smear.",
                                   "mix": "Smeared signal against the original.", "seed": "Changes the random pattern; the same seed gives the same result."}),
    # ------------------------------------------------------------ Modulation
    "Phaser": ("A sweeping filter that moves notches through the picture: flowing bands where colour and brightness drift.",
               {"stages": "Number of filter stages. More = more and deeper notches.", "rate": "Speed of the sweep.",
                "depth": "How far the sweep travels.", "fb": "Filtered signal against the original."}),
    "Wahwah": ("A band-pass filter swept up and down: pulsing bands that alternate between murky and sharp, coloured.",
               {"rate": "Speed of the sweep.", "depth": "How far the sweep travels.",
                "res": "Sharpness of the resonance. Higher = stronger, narrower pulses.", "freq": "Lowest frequency the sweep starts from."}),
    "Ring Modulator": ("Multiplies the picture by a sine wave: striped interference where contrast flips back and forth.",
                       {"f": "Frequency of the sine. Low = broad bands; high = fine moiré."}),
    # ------------------------------------------------------------ Distortion
    "Distortion": ("Pushes values toward black and white: contrast jumps and the picture goes posterized.",
                   {"drive": "How hard it is pushed. A few dB = subtle; 40+ dB = nearly pure black and white."}),
    "Wavefolder": ("Folds values back when they pass the limit instead of clipping: contrast loops, producing rainbow-like banding in gradients.",
                   {"drive": "Fold amount. Higher = more loops and more banding."}),
    "Asymmetric Saturation": ("Soft clipping with a bias: light and dark sides saturate differently, shifting contrast and tinting colours.",
                              {"drive": "How hard it is pushed.", "bias": "How lopsided it is. 0 = symmetric; higher shifts one side more."}),
    "Bitcrusher": ("Reduces the number of distinct values: posterized banding.",
                   {"bits": "Bits per value. The image is 8-bit, so 8 or more changes almost nothing; 1 gives only two levels."}),
    "Sample-rate Reducer": ("Holds each value for several samples: horizontal runs of repeated values, which mix up the colour channels.",
                            {"factor": "Samples sharing one value. 3 copies each pixel's red value into all three channels (a grey picture)."}),
    # --------------------------------------------------------------- Special
    "Invert": ("Flips every value: a negative.", {}),
    "Reverse": ("Plays the selection backwards: rotates it 180° and swaps red with blue.", {}),
    "Shift (Rotate)": ("Slides the selection along the data and wraps the end around to the start: the picture moves along the rows.",
                       {"amt": "How far, as a % of the selection. Negative goes the other way. A shift that isn't a multiple of 3 samples rotates the colour channels."}),
    "Change Speed (tile/crop)": ("Plays the data faster or slower and tiles or crops to fit: squashed repeated copies (faster) or a stretched, zoomed part (slower).",
                                 {"f": "Speed. 2 = twice as fast (two squashed copies); 0.5 = half speed (only the first half, stretched)."}),
    "Repeat Beginning": ("Loops the first part of the selection over the rest, like wallpaper.",
                         {"pct": "How much of the start is repeated, as a % of the selection."}),
    "Shuffle Chunks": ("Cuts the selection into chunks and shuffles them randomly: scrambled strips and blocks.",
                       {"chunk": "Chunk size in samples. One row = 3 × image width; a chunk of one row shuffles whole rows.",
                        "seed": "Changes the shuffle; the same seed gives the same result."}),
    "Swap Halves of Chunks": ("Swaps neighbouring chunks in pairs: strips trade places.",
                              {"chunk": "Chunk size in samples. One row = 3 × image width."}),
    "Add Noise": ("Adds random noise: film grain.",
                  {"amt": "Strength. 100 % buries the picture in noise.", "seed": "Changes the noise pattern; the same seed gives the same result."}),
    "Silence (mid-grey)": ("Replaces the selection with flat mid-grey: wipes out an area.", {}),
    "Sparse Glitch": ("Randomly freezes the data for short runs: horizontal dashes and streaks of repeated colour.",
                      {"p": "How much of the data is hit, in %.", "run": "Length of each frozen run in samples. Longer = longer streaks.",
                       "seed": "Changes where hits land; the same seed gives the same result."}),
    # -------------------------------------------------------------- Spectral
    "Paulstretch": ("An extreme smooth stretch: spreads the first part of the selection over all of it with randomised phases. The shapes dissolve into a dreamy smear.",
                    {"stretch": "How many times it is stretched. Higher = a smaller start region is used and smeared further.",
                     "win": "Analysis window. Bigger = smoother and blurrier; smaller = grainier."}),
    "Whisper (random phase)": ("Keeps which frequencies are present but randomises their timing: shapes dissolve into coloured noise with a similar overall tint.",
                               {"win": "Analysis window. Bigger = smoother; smaller = grainier.", "seed": "Changes the random pattern; the same seed gives the same result."}),
    "Robotize (zero phase)": ("Sets every frequency's timing to zero: the picture becomes a buzzy, regularly repeating texture.",
                              {"win": "Analysis window. It sets the spacing of the repeat: bigger = coarser pattern."}),
    "Frequency Shifter": ("Moves every frequency up or down by a fixed amount: patterns and colours shift, large shifts turn detail into stripes.",
                          {"hz": "How far to shift. Positive = up, negative = down."}),
    "Spectral Gate (keep peaks)": ("Keeps only the strongest frequencies at each moment and drops the rest: a simplified, ringing version of the picture.",
                                   {"db": "Frequencies weaker than this (relative to the strongest) are removed. Smaller = fewer survive."}),
    "Spectral Blur": ("Smooths the frequency content: softens fine detail and colour while the overall layout stays.",
                      {"width": "Smoothing width in frequency bins. More = softer."}),
    "Spectral Tilt": ("Gradually boosts one end of the frequency range and cuts the other: more detail and colour, or smoother and greyer.",
                      {"tilt": "Slope in dB per octave. Positive = more fine detail and colour; negative = smoother, more broad areas."}),
    "Frame Freeze / Stutter": ("Holds one short moment of the spectrum and repeats it: blocky, repeating textures.",
                               {"hold": "How many analysis frames are held (each is about 512 samples). More = longer repeats."}),
    # --------------------------------------------------------- Time & Glitch
    "Stutter": ("Repeats a small grain over and over inside each period: streaks and blocks repeated along the rows.",
                {"period": "Length of each repeat cycle in samples.", "grain": "How much of the start of each period is looped, as a % of the period."}),
    "Reverse Chunks": ("Reverses each chunk in place: strips are flipped left-right (with red and blue swapped).",
                       {"chunk": "Chunk size in samples. One row = 3 × image width."}),
    "Sort Chunks (pixel-sort)": ("Sorts the values inside each chunk from dark to light: classic pixel-sort streaks. It sorts the channels as one stream, so colours scramble.",
                                 {"chunk": "Chunk size in samples. One row = 3 × image width sorts each row.", "dir": "Dark to light, or light to dark."}),
    "Tape Stop / Speed Ramp": ("Speed glides from normal to a final speed across the selection: the picture is progressively stretched or compressed, like a tape slowing down.",
                               {"end": "Speed at the end. Below 1 stretches and repeats the start; above 1 compresses and wraps around."}),
    "Mirror": ("Makes the second half of the selection a reversed copy of the first: a symmetric, rotated mirror image.", {}),
    "Swap Neighbours": ("Swaps neighbouring blocks of samples. Block size 1 swaps adjacent values (channel mix-ups); larger blocks swap strips.",
                        {"block": "Block size in samples (3 samples = 1 pixel)."}),
    # ---------------------------------------------------------- Bits & Bytes
    "Bitwise Operation": ("Applies a binary operation to every byte value. XOR flips chosen bits (banded colour inversions); AND removes bits (posterize); OR sets bits (brightens); rotate moves bits around (wild colour jumps).",
                          {"op": "Which operation.", "val": "The bit pattern (0–255) for XOR, AND and OR. For rotate, how many bits to rotate."}),
    "Byte Add (wrap-around)": ("Adds a number to every byte and wraps past 255 back to 0: colours jump hard where values wrap, like solarization.",
                               {"add": "Amount added. Negative subtracts."}),
    "Wraparound Gain": ("Like Amplify, but values past the limit wrap to the other side instead of clipping: rainbow banding and solarized looks.",
                        {"db": "Gain. The higher it is, the more times values wrap."}),
    "Rectify": ("Folds values around mid-grey: the dark half is mirrored into the bright half (full-wave), or one half is flattened to grey (half-wave).",
                {"mode": "Full-wave mirrors; half-wave (+) keeps only values above grey; half-wave (−) keeps only values below grey."}),
    "DC Offset": ("Adds a constant to every value: shifts the whole brightness, clipping at the ends.",
                  {"off": "Amount. Positive brightens, negative darkens; 1 = the full range."}),
    # ----------------------------------------------------------------- Tape
    "Tape Recording (record → age → playback)": (
        "Simulates recording the picture to a cassette and playing it back: treble loss (so colour fades), hiss, dropouts, wavering speed, Dolby, and copies of copies. See ⓘ for the full guide.",
        {"tape": "Cassette formulation. I = hissier with less treble headroom; II = brighter and quieter; IV (metal) = most headroom and quietest.",
         "deck_rec": "The machine that recorded it. Cheaper machines lose treble (colour) earlier and add noise and wobble.",
         "rec_q": "How well it was recorded. Scales treble up to the machine's ceiling; low = soft and grey.",
         "rec_lvl": "Record level. High levels overload the tape (saturation).",
         "nr_rec": "Noise reduction used when recording. It must match the playback setting.",
         "nr_play": "Noise reduction used when playing back. A mismatch sounds dull or harsh.",
         "nr_err": "Playback level error. A wrong level makes the Dolby decoder pump.",
         "age": "Years on the shelf: treble loss, ghost copies (print-through) and dropouts.",
         "wear": "Physical damage. Stretched or creased tape drifts in speed and gets clicks and dropouts.",
         "hiss": "Tape noise level: grain over the picture.",
         "dropouts": "How often patches of oxide are missing: dim streaks.",
         "wow": "Slow speed wobble: shapes sway sideways in waves.",
         "flutter": "Fast speed jitter: fine wiggles.",
         "lock": "Whether timing errors snap to whole pixels (keeps colours) or are free (hues rotate).",
         "deck_play": "The machine that plays it back.",
         "play_q": "Playback head condition. Scales treble up to the playing machine's ceiling.",
         "azimuth": "Playback head tilt. Cancels treble, so colour fades.",
         "deck_speed": "Whether the two machines' small speed differences are applied. When on, recording and playing on different machines slants the picture.",
         "speed": "Extra playback speed error. Even a tiny one slants every row; −0.05 cancels the Nakamichi-to-AKAI mismatch.",
         "gens": "How many times it was re-recorded onto another tape. Each copy adds noise and loses treble.",
         "plays": "How many times the final tape was played. Head wear dulls the treble and adds dropouts.",
         "seed": "Changes the random pattern of noise and dropouts; the same seed gives the same result."}),
}


# name -> what the effect is for in ordinary audio work (shown in the info box next to the picture description)
AUDIO: dict[str, str] = {
    "Amplify": "Raises or lowers the volume by a number of decibels. Pushed too far, the peaks clip and sound harshly distorted.",
    "Normalize": "Scales a clip so its loudest peak lands exactly on a target level, a common last step before export.",
    "Fade In": "Raises the volume smoothly from silence to full, to avoid an abrupt start.",
    "Fade Out": "Lowers the volume smoothly to silence, the classic way to end a track.",
    "Tremolo": "Rapid, regular volume pulsing, the trembling sound of vintage guitar amps and electric pianos.",
    "Limiter / Hard Clip": "Hard clipping chops any peak beyond a threshold flat, which adds harsh distortion. A limiter does the same job gently to stop peaks overloading.",
    "Compressor": "Turns down the loud parts so the difference between loud and quiet shrinks, then make-up gain lifts the whole thing: a punchier, more even sound.",
    "Noise Gate": "Mutes or reduces the signal whenever it falls below a threshold, cutting hiss and spill between notes or words.",
    "Bass and Treble": "A simple tone control: boosts or cuts the low end and the high end, like the two knobs on a hi-fi.",
    "Low-pass Filter": "Lets low frequencies through and cuts the highs: a muffled, 'behind a wall' sound. Resonance adds a whistling emphasis at the cutoff, a staple of synthesizers.",
    "High-pass Filter": "Cuts the lows and keeps the highs: thins the sound out. Used to remove rumble and handling noise.",
    "Band-pass Filter": "Keeps only a band around one frequency: the narrow, 'telephone' or small-radio sound.",
    "Notch Filter": "Removes a very narrow band and leaves the rest: used to kill mains hum or one ringing frequency.",
    "Parametric EQ (Peak)": "Boosts or cuts one band whose frequency, width and gain you choose: the main tool for fixing the tone of a recording.",
    "5-Band EQ": "A simple graphic equaliser: five fixed bands for shaping the overall tone.",
    "Median Filter": "Not a creative effect. In signal processing it removes short clicks and spikes without dulling the sound the way a low-pass filter would, so it is used for de-clicking.",
    "Differentiate (edge detect)": "Not a normal effect. Mathematically it is a filter that falls toward the bass and rises 6 dB per octave toward the treble, a harsh, thin sound that removes the lows.",
    "Echo": "Repeats the sound after a delay with fading repeats, like a shout in a canyon or a tape echo unit.",
    "Reverb": "Simulates the thousands of reflections of a room or hall, adding space and ambience.",
    "Flanger": "Mixes the sound with a copy delayed by a few milliseconds, with the delay sweeping slowly: the swooshing 'jet plane' sound.",
    "Vibrato": "A regular wobble in pitch (made by modulating the delay time), like the wavering of a singer or violinist.",
    "Chorus": "Mixes in several slightly delayed and detuned copies, so one voice or instrument sounds like many.",
    "Comb Filter": "A very short feedback delay creates a pitched, metallic ringing at a frequency set by the delay. It is the basis of Karplus-Strong plucked-string synthesis.",
    "Multi-tap Delay": "Several echoes at fixed spacing with no feedback: rhythmic repeats.",
    "Noise Convolution (smear)": "Convolving a sound with a burst of decaying noise is how a convolution reverb works, so this gives a diffuse, noisy reverb tail.",
    "Phaser": "Sweeping all-pass filters mixed with the original create moving notches: a swirling, whooshing sound, classic on guitar and electric piano.",
    "Wahwah": "A resonant band-pass filter swept up and down, like a wah pedal: the 'wah-wah' vowel sound.",
    "Ring Modulator": "Multiplies the sound by a sine wave, producing sum and difference frequencies: metallic, bell-like or robotic tones (the classic Dalek voice).",
    "Distortion": "Soft clipping adds harmonics, giving the crunch of an overdriven guitar amplifier.",
    "Wavefolder": "Folds the waveform back on itself when it passes a limit, which makes rich, harsh harmonics: the sound of 'West Coast' analog synthesizers.",
    "Asymmetric Saturation": "Soft clipping with a bias treats the positive and negative halves differently, adding even harmonics: the warmer, thicker saturation of valves and tape.",
    "Bitcrusher": "Lowers the bit depth, adding quantisation noise: the gritty lo-fi sound of early digital samplers and game consoles.",
    "Sample-rate Reducer": "Holds each sample for several steps, creating aliasing: the metallic, lo-fi grit of old samplers.",
    "Invert": "Flips the polarity of the waveform. Alone it is inaudible, but mixed with the original it cancels it completely.",
    "Reverse": "Plays the sound backwards: swelling, sucked-in attacks, as with reversed cymbals and reverb tails.",
    "Shift (Rotate)": "Moves the clip in time, with whatever falls off the end reappearing at the start. A circular time shift.",
    "Change Speed (tile/crop)": "Plays faster or slower, which raises or lowers the pitch too, like speeding up a tape or record. Here the result is repeated or cut to keep the length.",
    "Repeat Beginning": "Loops the start of the clip over and over: a loop or freeze effect.",
    "Shuffle Chunks": "Cuts the sound into slices and reorders them randomly: a glitch or beat-slicing effect.",
    "Swap Halves of Chunks": "Swaps neighbouring slices in pairs: a simple glitch edit.",
    "Add Noise": "Adds white noise: hiss, a masking layer, or dither.",
    "Silence (mid-grey)": "Replaces the selection with silence.",
    "Sparse Glitch": "Randomly freezes the signal for short runs: the stuck, skipping sound of a scratched CD.",
    "Paulstretch": "An extreme time-stretch (by Paul Nasca, also in Audacity) that randomises phases and uses very large windows, smearing a sound into a long, smooth ambient drone without changing its pitch.",
    "Whisper (random phase)": "Keeping each frequency's strength but randomising its timing makes speech sound like a whisper: the tone survives, the voice's pulse does not.",
    "Robotize (zero phase)": "Zeroing the timing of every frequency makes each frame repeat at the frame rate, giving a monotone, robotic buzz.",
    "Frequency Shifter": "Adds a fixed number of Hz to every frequency, not a ratio like a pitch shifter, so the harmonic relationships break and the sound turns inharmonic and metallic. Real frequency shifters (like the Bode) use single-sideband modulation; this one shifts spectrum bins, so it is an approximation.",
    "Spectral Gate (keep peaks)": "Keeps only the strongest frequency components at each moment: a sparse, ringing tonal effect, or a crude denoiser.",
    "Spectral Blur": "Smooths the frequency spectrum, which softens the timbre and makes the sound dull and diffuse.",
    "Spectral Tilt": "Tilts the balance of the spectrum by a number of dB per octave: the sound gets darker or brighter overall.",
    "Frame Freeze / Stutter": "Holds one short slice of the spectrum and repeats it, like the freeze function of granular and ambient effect pedals.",
    "Stutter": "Repeats a short grain over and over: the classic stutter or buffer-repeat glitch.",
    "Reverse Chunks": "Reverses each short slice in place: a choppy, back-and-forth texture.",
    "Sort Chunks (pixel-sort)": "A databending trick with no musical meaning: sorting the samples in each slice by value produces a harsh, buzzing noise.",
    "Tape Stop / Speed Ramp": "The speed glides down to a stop (or up) like a tape machine being switched off, so pitch and tempo fall together.",
    "Mirror": "The second half becomes the first half reversed, making a palindrome.",
    "Swap Neighbours": "Swaps neighbouring samples or blocks. At one sample it scrambles the high frequencies into noise; larger blocks are crude glitch edits.",
    "Bitwise Operation": "Bit-twiddling on the sample values. XOR, AND and OR make harsh digital distortion and noise, so it isn't a normal audio effect.",
    "Byte Add (wrap-around)": "Adding with wrap-around makes sudden jumps where the value overflows: harsh clicks and distortion, like integer overflow in digital audio.",
    "Wraparound Gain": "Gain where overflowing values wrap around instead of clipping, like badly overflowing early digital gear: harsh, noisy distortion.",
    "Rectify": "Full-wave rectification flips the negative half of the waveform up, which doubles the apparent pitch (an octave up) and adds harmonics. Half-wave gives a buzzy distortion.",
    "DC Offset": "Adds a constant to the signal. Inaudible on its own, but it wastes headroom and makes clicks at edits.",
    "Tape Recording (record → age → playback)": "Models what an analogue cassette does to sound: gentle saturation, treble roll-off, hiss, wow and flutter (pitch wobble), dropouts and Dolby noise reduction. Every re-recording adds more of all of it.",
}

GLOSSARY = (
    "<p style='margin-top:14px'><b>Units.</b> The picture is read as one long list of numbers: 3 per pixel (red, green, blue), row after row. "
    "<b>smp</b> = samples in that list, so one row of a 1000 px wide image is 3,000 samples. The list is treated as audio at 44,100 samples per "
    "second, so <b>0.1 s</b> = 4,410 samples and <b>Hz</b> is detail size (high = fine detail and colour, low = broad areas). "
    "A <b>seed</b> picks a random pattern; the same seed always gives the same result.</p>")


def apply(effects: list) -> None:
    """Attach the text to the registry (called once at import)."""
    for e in effects:
        summary, params = HELP.get(e.name, ("", {}))
        e.summary = summary
        e.audio = AUDIO.get(e.name, "")
        for p in e.params:
            p.help = params.get(p.key, "")


def _fmt_range(p) -> str:
    if p.choices:
        return " / ".join(p.choices)
    unit = (p.suffix or "").strip()
    sep = " " if unit and not unit.startswith("%") else ""
    return f"{p.min:g} to {p.max:g}{sep}{unit}, default {p.default:g}"


def help_html(e, with_glossary: bool = True) -> str:
    """The help page for one effect. The tape effect has its own, richer guide."""
    if e.info:
        return e.info
    rows = "".join(f"<li><b>{html.escape(p.label)}</b> <span style='color:gray'>({html.escape(_fmt_range(p))})</span><br>{html.escape(p.help)}</li>"
                   for p in e.params)
    body = f"<h3>{html.escape(e.name)}</h3><p>{html.escape(e.summary)}</p>"
    if e.audio:
        body += f"<h4>In audio</h4><p>{html.escape(e.audio)}</p>"
    body += f"<h4>Settings</h4><ul>{rows}</ul>" if rows else "<p><i>No settings: it just applies.</i></p>"
    return body + (GLOSSARY if with_glossary else "")


def reference_html(effects: list) -> str:
    """Every effect on one page, grouped by category in the order they appear in the effect list."""
    out, cats = ["<h2>Effect reference</h2>"], {}
    for e in effects:
        cats.setdefault(e.category, []).append(e)
    for cat, items in cats.items():
        out.append(f"<h3>{html.escape(cat)}</h3>")
        for e in items:
            out.append(f"<p><b>{html.escape(e.name)}</b>: {html.escape(e.summary)}"
                       + (f"<br><i>In audio:</i> {html.escape(e.audio)}" if e.audio else "") + "</p>")
    return "".join(out) + GLOSSARY
