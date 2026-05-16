"""
Fridge NeoPixel welcome show + optional OLED quotes.

Sense "door open" from the same 12V feed that turns the fridge interior LED on/off.
Do NOT connect 12V to a Pico GPIO — it will destroy the board.

Recommended: optocoupler (e.g. PC817, 4N35)
- Pico GND tied to the fridge 12V return (negative) you are already referencing.
- Opto IR LED side in series with a resistor (~1.5k–2.2k) across the points where
  the 12V appears only when the door opens (same as the stock LED gets power).
  Polarity must match the IR LED (anode toward +12V side, cathode toward ground).
- Opto phototransistor: collector -> LIGHT_SENSE_PIN, emitter -> GND.
  Configure Pico pin INPUT_PULLUP. When 12V is on, transistor conducts -> pin reads LOW.

Alternative (less isolation): resistor divider 12V -> ~3V plus a 3.3V Zener clamp to
GPIO — only if you fully understand the fridge wiring and share a solid common ground.

NeoPixel:
- WS2812 DATA -> NEOPIXEL_PIN (default GP15). Use external 5V + shared GND; cap brightness.

Optional SSD1306 OLED I2C -> GP8 (SDA), GP9 (SCL), 3V3, GND (set USE_OLED True).
"""

import random
import time
from machine import Pin, I2C

import neopixel

from messages import MESSAGES

# --- pins & strip ---
NEOPIXEL_PIN = 15
NUM_LEDS = 16  # change to match your strip

# Conditioned signal from the fridge 12V interior light (see docstring).
LIGHT_SENSE_PIN = 14
# Typical opto + pull-up: 12V light ON -> GPIO LOW. If your circuit drives HIGH when lit, set True.
LIGHT_ON_HIGH = False

# Brightness cap (0.0–1.0) to keep current draw sane on USB power
MAX_BRIGHT = 0.35

# Optional I2C OLED (SSD1306 128x64 common). Set True after wiring + ssd1306 driver.
USE_OLED = False
OLED_WIDTH = 128
OLED_HEIGHT = 64
I2C_ID = 0
I2C_SDA = 8
I2C_SCL = 9

# Timing (ms)
FADE_STEP_MS = 18
OPEN_SHIMMER_MS = 90


def _clamp(x, lo, hi):
    return hi if x > hi else lo if x < lo else x


def _scale_color(r, g, b, factor):
    f = _clamp(factor, 0.0, 1.0) * MAX_BRIGHT
    return (int(r * f), int(g * f), int(b * f))


def lerp(a, b, t):
    return a + (b - a) * t


def fridge_light_on(sense):
    v = sense.value()
    return (v == 1) if LIGHT_ON_HIGH else (v == 0)


def light_on_stable(sense, samples=4, gap_ms=12):
    """True if the 12V sense line reads 'light on' for several consecutive samples."""
    for _ in range(samples):
        if not fridge_light_on(sense):
            return False
        time.sleep_ms(gap_ms)
    return True


def random_accent_rgb():
    return (random.randint(80, 255), random.randint(40, 220), random.randint(40, 220))


def fill_np(np, r, g, b):
    for i in range(np.n):
        np[i] = (r, g, b)
    np.write()


def fade_to(np, fr, fg, fb, tr, tg, tb, steps, step_ms):
    for s in range(steps + 1):
        t = s / steps
        r, g, b = (
            int(lerp(fr, tr, t)),
            int(lerp(fg, tg, t)),
            int(lerp(fb, tb, t)),
        )
        r, g, b = _scale_color(r, g, b, 1.0)
        fill_np(np, r, g, b)
        time.sleep_ms(step_ms)


def sparkle_to_white(np):
    n = np.n
    starts = [random_accent_rgb() for _ in range(n)]
    ends = [
        (random.randint(200, 255), random.randint(200, 245), random.randint(160, 220))
        for _ in range(n)
    ]
    steps = 55
    for s in range(steps + 1):
        t = s / steps
        te = 1 - (1 - t) ** 3
        for i in range(n):
            sr, sg, sb = starts[i]
            er, eg, eb = ends[i]
            r = int(lerp(sr, er, te)) + random.randint(-6, 10)
            g = int(lerp(sg, eg, te)) + random.randint(-6, 10)
            b = int(lerp(sb, eb, te)) + random.randint(-6, 10)
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))
            rr, gg, bb = _scale_color(r, g, b, 1.0)
            np[i] = (rr, gg, bb)
        np.write()
        time.sleep_ms(FADE_STEP_MS)


def sweep_random_to_white(np):
    n = np.n
    hue = random_accent_rgb()
    fill_np(np, 0, 0, 0)
    for i in range(n + 18):
        for j in range(n):
            dist = abs(j - min(i, n - 1))
            if dist > 10:
                tr, tg, tb = 0, 0, 0
            else:
                blend = 1 - dist / 10
                wr, wg, wb = 255, 245, 210
                tr = int(lerp(0, wr, blend))
                tg = int(lerp(0, wg, blend))
                tb = int(lerp(0, wb, blend))
                tr = int(lerp(hue[0], tr, 0.35))
                tg = int(lerp(hue[1], tg, 0.35))
                tb = int(lerp(hue[2], tb, 0.35))
            r, g, b = _scale_color(tr, tg, tb, 1.0)
            np[j] = (r, g, b)
        np.write()
        time.sleep_ms(22)
    fade_to(np, 255, 245, 210, 255, 255, 255, 30, FADE_STEP_MS)


def soft_off(np):
    fade_to(np, 255, 255, 255, 0, 0, 0, 40, FADE_STEP_MS)


def try_show_message(i2c, msg):
    if not USE_OLED or i2c is None:
        return
    try:
        from ssd1306 import SSD1306_I2C
    except ImportError:
        return

    try:
        oled = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
    except OSError:
        return

    oled.fill(0)
    oled.text("Hello, snacker", 0, 0, 1)
    y = 16
    chunk = 18
    for i in range(0, len(msg), chunk):
        line = msg[i : i + chunk]
        oled.text(line, 0, y, 1)
        y += 10
        if y > OLED_HEIGHT - 12:
            break
    oled.show()


def clear_oled(i2c):
    if not USE_OLED or i2c is None:
        return
    try:
        from ssd1306 import SSD1306_I2C

        oled = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
        oled.fill(0)
        oled.show()
    except (ImportError, OSError):
        pass


def pick_animation(np):
    r = random.random()
    if r < 0.45:
        sparkle_to_white(np)
    elif r < 0.8:
        sweep_random_to_white(np)
    else:
        cr, cg, cb = random_accent_rgb()
        fade_to(np, 0, 0, 0, cr, cg, cb, 18, FADE_STEP_MS)
        fade_to(np, cr, cg, cb, 255, 250, 220, 40, FADE_STEP_MS)


def main():
    sense = Pin(LIGHT_SENSE_PIN, Pin.IN, Pin.PULL_UP)
    np = neopixel.NeoPixel(Pin(NEOPIXEL_PIN), NUM_LEDS)
    fill_np(np, 0, 0, 0)

    i2c = None
    if USE_OLED:
        try:
            i2c = I2C(I2C_ID, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=400_000)
        except OSError:
            i2c = None

    was_lit = fridge_light_on(sense)

    while True:
        lit_now = fridge_light_on(sense)

        if lit_now and not was_lit:
            if light_on_stable(sense):
                pick_animation(np)
                try_show_message(i2c, random.choice(MESSAGES))
                while fridge_light_on(sense):
                    for j in range(np.n):
                        wobble = random.randint(-4, 4)
                        r = _clamp(255 + wobble, 0, 255)
                        g = _clamp(245 + wobble, 0, 255)
                        b = _clamp(210 + wobble, 0, 255)
                        rr, gg, bb = _scale_color(r, g, b, 1.0)
                        np[j] = (rr, gg, bb)
                    np.write()
                    time.sleep_ms(OPEN_SHIMMER_MS)
                soft_off(np)
                clear_oled(i2c)

        was_lit = fridge_light_on(sense)
        time.sleep_ms(25)


if __name__ == "__main__":
    main()
