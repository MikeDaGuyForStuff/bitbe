#!/usr/bin/env python3
"""Bitbe — desktop virtual pet powered by Claude."""

import tkinter as tk
from tkinter import messagebox
import math, random, os, sys, threading, json, time, signal

# ── .env loader ───────────────────────────────────────────────────────────────
def _load_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))

_load_env()

# ── constants ─────────────────────────────────────────────────────────────────
W, H         = 130, 120
PET_W, PET_H = 90, 78
CORNER_R     = 18
EYE_R        = 12
FRAME_MS     = 16        # ≈60 fps
GRAVITY      = 0.5

HOUSE_W, HOUSE_H = 120, 100

# Strip window: full-width, fixed at screen bottom. Pet draws at px within it.
# Never moves → no compositor ghost-trail from window repositioning.
STRIP_H = 300   # tall enough to cover ground + max jump (~81 px)

SLIDE_OUT_FRAMES = 65   # ~1.1 s at 60 fps
SLIDE_IN_FRAMES  = 70   # ~1.2 s

PARTICLE_STYLES = {
    '❤️': ('♥', '#ff8fab'),
    '💤': ('z', '#93c5fd'),
    '🫧': ('○', '#bfdbfe'),
    '🎵': ('♪', '#fde68a'),
}

MOOD_DOT = {'happy':'#4ade80','sleepy':'#93c5fd','excited':'#f97316',
            'bored':'#a78bfa','surprised':'#fb7185','hungry':'#fbbf24'}
BODY_CLR = {'happy':'#60a5fa','sleepy':'#bfdbfe','excited':'#fb923c',
            'bored':'#c4b5fd','surprised':'#f9a8d4','hungry':'#fde68a'}
OUTLINE  = {'happy':'#1d4ed8','sleepy':'#3b82f6','excited':'#c2410c',
            'bored':'#7c3aed','surprised':'#be123c','hungry':'#b45309'}

# Pet states
S_ROAMING   = 'roaming'
S_IN_HOUSE  = 'in_house'
S_EXITING   = 'exiting'
S_RETURNING = 'returning'
S_ENTERING  = 'entering'

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.pet_state.json')


# ── House widget ──────────────────────────────────────────────────────────────
class House:
    _ROOF_F  = '#f97316'
    _ROOF_O  = '#c2410c'
    _BODY_F  = '#dbeafe'
    _BODY_O  = '#1e40af'
    _DOOR_F  = '#1d4ed8'
    _DARK    = '#0f172a'
    _WIN_LIT = '#fef08a'
    _WIN_DIM = '#bfdbfe'
    _CHIM_F  = '#94a3b8'

    def __init__(self, pet):
        self.pet = pet
        self._sw = pet._sw or pet.root.winfo_screenwidth()
        self._sh = pet._sh or pet.root.winfo_screenheight()

        self.win = tk.Toplevel(pet.root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.resizable(False, False)

        self.canvas = tk.Canvas(self.win, width=HOUSE_W, height=HOUSE_H,
                                bg='#e0f2fe', highlightthickness=0)
        self.canvas.pack()

        self.win.configure(bg='#e0f2fe')

        saved = pet._saved_state
        hx = saved.get('house_x')
        hy = saved.get('house_y')
        self.hx = float(hx) if hx is not None else self._sw / 2
        # Always pin to floor regardless of saved value
        self.hy = float(self._sh - HOUSE_H / 2)

        # Door animation: 0.0 = fully closed, 1.0 = fully open
        self._door_frac   = 0.0
        self._door_target = 0.0

        # Drag tracking
        self._press_rx    = 0
        self._press_ry    = 0
        self._drag_ox     = 0.0
        self._drag_oy     = 0.0
        self._is_dragging = False

        self.canvas.bind('<ButtonPress-1>',   self._press)
        self.canvas.bind('<B1-Motion>',       self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._release)

        self._place()
        self._draw()

    # ── Geometry ──────────────────────────────────────────────────────────────
    def _place(self):
        x = int(self.hx - HOUSE_W / 2)
        y = int(self.hy - HOUSE_H / 2)
        self.win.geometry(f'{HOUSE_W}x{HOUSE_H}+{x}+{y}')

    # ── Update (called every frame from Pet._update) ───────────────────────────
    def update(self):
        if abs(self._door_frac - self._door_target) > 0.005:
            self._door_frac += (self._door_target - self._door_frac) * 0.10
            self._draw()
        elif self._door_frac != self._door_target:
            self._door_frac = self._door_target
            self._draw()

    def open_door(self):
        self._door_target = 1.0

    def close_door(self):
        self._door_target = 0.0

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _draw(self):
        c = self.canvas
        c.delete('all')
        pet_home = (self.pet.state == S_IN_HOUSE)

        # Background scene
        c.create_rectangle(0, 0, HOUSE_W, HOUSE_H, fill='#e0f2fe', outline='')
        c.create_oval(90, 4, 112, 26, fill='#fde68a', outline='#fbbf24', width=1.5)
        c.create_rectangle(0, 88, HOUSE_W, HOUSE_H, fill='#86efac', outline='')
        c.create_rectangle(0, 88, HOUSE_W, 92, fill='#4ade80', outline='')

        # Chimney (behind roof visually)
        c.create_rectangle(76, 8, 90, 40,
                           fill=self._CHIM_F, outline=self._BODY_O, width=1.5)

        # Roof triangle
        c.create_polygon([10, 43, 60, 3, 110, 43],
                         fill=self._ROOF_F, outline=self._ROOF_O, width=2.5)

        # House body with rounded corners
        r = 6
        x1, y1, x2, y2 = 18, 40, 102, 98
        bp = [x1+r, y1,  x2-r, y1,
              x2,   y1+r, x2,  y2-r,
              x2-r, y2,  x1+r, y2,
              x1,   y2-r, x1,  y1+r]
        c.create_polygon(bp, fill=self._BODY_F, outline=self._BODY_O,
                         width=2, smooth=True)

        # Side window
        c.create_rectangle(24, 50, 44, 68,
                           fill=self._WIN_LIT if pet_home else self._WIN_DIM,
                           outline=self._BODY_O, width=1.5)
        c.create_line(34, 50, 34, 68, fill=self._BODY_O, width=1)
        c.create_line(24, 59, 44, 59, fill=self._BODY_O, width=1)

        # Door — dark interior
        dx1, dy1, dx2, dy2 = 44, 61, 76, 98
        c.create_rectangle(dx1, dy1, dx2, dy2,
                           fill=self._DARK, outline=self._BODY_O, width=1.5)

        # Door panel
        panel_w = int((dx2 - dx1) * (1.0 - self._door_frac))
        if panel_w > 0:
            c.create_rectangle(dx1, dy1 + 1, dx1 + panel_w, dy2,
                               fill=self._DOOR_F, outline='')
            kx = dx1 + panel_w - 5
            ky = (dy1 + dy2) // 2
            if kx > dx1 + 3:
                c.create_oval(kx - 3, ky - 3, kx + 3, ky + 3,
                              fill='#fbbf24', outline='#b45309', width=1)

        # Tiny Bitbe face in window when home
        if pet_home:
            c.create_oval(27, 53, 41, 67,
                          fill='#60a5fa', outline='#1d4ed8', width=1)
            c.create_oval(30, 57, 33, 60, fill='#1e293b', outline='')
            c.create_oval(35, 57, 38, 60, fill='#1e293b', outline='')

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _press(self, e):
        self._press_rx    = e.x_root
        self._press_ry    = e.y_root
        self._drag_ox     = e.x_root - self.hx
        self._drag_oy     = e.y_root - self.hy
        self._is_dragging = False

    def _on_drag(self, e):
        self._is_dragging = True
        # Only allow horizontal dragging — house stays on the floor
        self.hx = e.x_root - self._drag_ox
        self.hx = max(HOUSE_W / 2, min(self._sw - HOUSE_W / 2, self.hx))
        # hy stays pinned to floor — never changes
        self._place()

    def _release(self, e):
        dist = math.hypot(e.x_root - self._press_rx,
                          e.y_root - self._press_ry)
        if dist < 20 and not self._is_dragging:
            self._clicked()
        self._is_dragging = False

    def _clicked(self):
        p = self.pet
        if p.state == S_IN_HOUSE:
            p._exit_house()
        elif p.state == S_ROAMING:
            p._return_home()


# ── Desktop button ────────────────────────────────────────────────────────────
class DesktopButton:
    """Non-topmost button that lives at desktop level (below all app windows).
    Triggers cute slide-off / slide-back animations."""
    BW, BH = 114, 34

    def __init__(self, pet):
        self.pet      = pet
        self._hovered = False

        self.win = tk.Toplevel(pet.root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', False)   # stays BELOW normal windows
        self.win.resizable(False, False)

        self.canvas = tk.Canvas(self.win, width=self.BW, height=self.BH,
                                highlightthickness=0, bg='#0f172a')
        self.canvas.pack()
        self.win.configure(bg='#0f172a')

        # Top-left corner, just below where a taskbar would sit
        self.win.geometry(f'{self.BW}x{self.BH}+16+50')
        self.win.lower()   # push below all windows

        self.canvas.bind('<Button-1>', lambda e: pet._toggle_slide())
        self.canvas.bind('<Enter>',    lambda e: self._on_hover(True))
        self.canvas.bind('<Leave>',    lambda e: self._on_hover(False))
        self._draw()

    def _on_hover(self, on):
        self._hovered = on
        self._draw()

    def _draw(self):
        c = self.canvas
        w, h = self.BW, self.BH
        c.delete('all')
        hidden = (getattr(self.pet, '_slide_anim', None) == 'hidden')

        bg  = '#334155' if self._hovered else ('#0f172a' if hidden else '#1e293b')
        rim = '#93c5fd' if hidden else '#60a5fa'

        # Rounded rect background
        r = 8
        pts = [r,0, w-r,0, w-r,0, w,0, w,r,
               w,h-r, w,h, w-r,h, r,h, 0,h,
               0,h-r, 0,r, 0,0, r,0]
        c.create_polygon(pts, fill=bg, outline=rim, width=1.5, smooth=True)

        label = '🐾  Wake Bitbe!' if hidden else '🐾  Hide Bitbe'
        c.create_text(w // 2, h // 2, text=label,
                      fill='#e2e8f0' if not hidden else '#93c5fd',
                      font=('Segoe UI', 9, 'bold'))

    def keep_lowered(self):
        """Call every frame to prevent other windows pushing us up."""
        try:
            self.win.lower()
        except Exception:
            pass

    def refresh(self):
        self._draw()


# ── Pet ───────────────────────────────────────────────────────────────────────
class Pet:
    def __init__(self):
        self._load_state()   # populates self._saved_state

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)

        self.root.configure(bg='#e0f2fe')

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self._sw, self._sh = sw, sh

        self.canvas = tk.Canvas(self.root, width=W, height=H,
                                bg='#e0f2fe', highlightthickness=0)
        self.canvas.pack()

        self.px        = float(sw // 2)
        self._ground_y = float(sh - H // 2)
        self.py        = self._ground_y
        self.vx        = random.choice([-1, 1]) * random.uniform(0.7, 1.3)

        self._jump_dy  = 0.0
        self._jump_vy  = 0.0
        self._jumping  = False
        self._jump_t   = random.randint(350, 650)

        self.bob      = 0.0
        self.wiggle   = 0.0
        self._wig_rem = 0
        self._wig_dir = 1

        self.blink_t  = random.randint(150, 280)
        self.blink_ph = 0
        self.blinking = False

        self.mood      = 'happy'
        self.happiness = 100.0
        self.hunger    = 0.0
        self.boredom   = 0.0
        self._decay_t  = 0

        self.eating  = False
        self._eat_t  = 0
        self.playing = False
        self._play_t = 0

        self._dart_x  = 0.0;  self._dart_y  = 0.0
        self._dart_tx = 0.0;  self._dart_ty = 0.0
        self._dart_t  = 0

        self._particles = []
        self._zzz_t    = random.randint(80, 140)
        self._tod_t    = 60
        self.dancing   = False
        self._dance_t  = 0
        self.bathing   = False
        self._bath_t   = 0

        self._following    = False
        self._follow_panel = None

        self._dragging = False
        self._drag_ox  = 0.0
        self._press_rx = 0
        self._press_ry = 0

        self._client    = None
        self._chat_win  = None
        self._chat_hist = []
        self._init_claude()

        # ── State machine ──────────────────────────────────────────────────────
        self.state       = S_IN_HOUSE if self._saved_state.get('in_house', True) else S_ROAMING
        # _slide_anim: None | 'out' | 'hidden' | 'in'
        self._slide_anim = 'hidden' if self._saved_state.get('hidden', False) else None
        self._slide_t    = 0
        self._slide_ox   = float(self._sw // 2)   # saved pet px for restore
        self._slide_ohx  = float(self._sw // 2)   # saved house hx for restore
        self._exit_frame  = 0
        self._enter_frame = 0

        self.canvas.bind('<ButtonPress-1>',   self._press)
        self.canvas.bind('<B1-Motion>',       self._drag)
        self.canvas.bind('<ButtonRelease-1>', self._release)
        self.canvas.bind('<Button-3>',        self._rclick)

        self._place()

        # House must come after sw/sh and _saved_state are ready
        self.house       = House(self)
        self.desktop_btn = DesktopButton(self)

        self.root.withdraw()

        # ── Persistence ────────────────────────────────────────────────────────
        self.root.protocol('WM_DELETE_WINDOW', self._quit)
        try:
            signal.signal(signal.SIGTERM, lambda *_: self._quit())
            signal.signal(signal.SIGINT,  lambda *_: self._quit())
        except Exception:
            pass
        self._auto_save()
        self._lowered_for_return = False

        if self._slide_anim == 'hidden':
            self.house.win.withdraw()   # keep hidden; desktop button still shows
        elif self.state != S_IN_HOUSE:
            self.root.deiconify()
            self.root.lift()

        self._loop()

    # ── Persistence ───────────────────────────────────────────────────────────
    def _load_state(self):
        # Default: pet starts inside house, position resolved after screen size known
        default = {'in_house': True, 'house_x': None, 'house_y': None,
                   'timestamp': 0.0}
        try:
            with open(STATE_FILE) as f:
                saved = json.load(f)
        except Exception:
            self._saved_state = default
            return

        # If the computer rebooted since the state was saved, default to in_house
        try:
            with open('/proc/uptime') as f:
                uptime_s = float(f.read().split()[0])
            boot_time = time.time() - uptime_s
            if saved.get('timestamp', 0.0) < boot_time:
                saved['in_house'] = True
        except Exception:
            pass  # non-Linux or unreadable; keep saved value

        self._saved_state = saved

    def _save_state(self):
        if not hasattr(self, 'house'):
            return
        data = {
            'timestamp': time.time(),
            'in_house':  self.state == S_IN_HOUSE,
            'house_x':   self.house.hx,
            'house_y':   self.house.hy,
            'hidden':    self._slide_anim == 'hidden',
        }
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())  # force write to disk before power-off
        except Exception:
            pass

    def _auto_save(self):
        self._save_state()
        self.root.after(60_000, self._auto_save)

    def _quit(self):
        self._save_state()
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    # ── Claude ────────────────────────────────────────────────────────────────
    def _init_claude(self):
        key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not key:
            return
        try:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=key)
        except ImportError:
            pass

    # ── Slide animation ───────────────────────────────────────────────────────
    def _toggle_slide(self):
        if self._slide_anim in ('out', 'in'):
            return   # already animating — ignore
        if self._slide_anim == 'hidden':
            # ── Slide IN ──────────────────────────────────────────────────────
            self._slide_anim = 'in'
            self._slide_t    = 0
            # Place both windows off-screen below before deiconifying
            self.py      = float(self._sh + H)
            self.px      = self._slide_ox
            self._jumping = False;  self._jump_dy = 0.0;  self._jump_vy = 0.0
            self._place()
            self.house.hx = self._slide_ohx
            self.house.hy = float(self._sh + HOUSE_H)
            self.house._place()
            self.house.win.deiconify()
            if self.state != S_IN_HOUSE:
                self.root.deiconify()
                self.root.lift()
        else:
            # ── Slide OUT — save positions then start wiggle phase ─────────────
            self._slide_ox  = self.px
            self._slide_ohx = self.house.hx
            self._slide_anim = 'out'
            self._slide_t    = 0
            self._jumping = False;  self._jump_dy = 0.0;  self._jump_vy = 0.0
            self.mood = 'excited'
            self._wiggle()
        self.desktop_btn.refresh()

    def _update_slide(self):
        self._slide_t += 1

        if self._slide_anim == 'out':
            if self._slide_t < 22:
                # Phase 1: wave goodbye (wiggle already running)
                return

            # Phase 2: ease-in slide DOWN
            p  = min(1.0, (self._slide_t - 22) / (SLIDE_OUT_FRAMES - 22))
            ep = p * p   # ease-in: slow → fast

            off_pet   = ep * (self._sh + H      + H      // 2)
            off_house = ep * (self._sh + HOUSE_H + HOUSE_H // 2)

            self.py      = self._ground_y + off_pet
            self._place()
            self.house.hy = (self._sh - HOUSE_H // 2) + off_house
            self.house._place()

            if p >= 1.0:
                self._particles.clear()
                self.root.withdraw()
                self.house.win.withdraw()
                self._slide_anim = 'hidden'
                self.py       = self._ground_y
                self.house.hy = float(self._sh - HOUSE_H // 2)
                self.desktop_btn.refresh()
                self._save_state()

        elif self._slide_anim == 'in':
            total = SLIDE_IN_FRAMES

            # House: ease-out with a tiny overshoot bounce
            hp  = min(1.0, self._slide_t / (total * 0.70))
            ehp = 1.0 - (1.0 - hp) ** 2   # ease-out
            # slight bounce: goes 8px past target then settles
            bounce = math.sin(hp * math.pi) * 8 if hp < 1.0 else 0
            target_hy = float(self._sh - HOUSE_H // 2)
            self.house.hy = (self._sh + HOUSE_H) + (target_hy - self._sh - HOUSE_H) * ehp - bounce
            self.house._place()

            # Pet: ease-out, delayed by 18 frames
            if self._slide_t > 18:
                pp  = min(1.0, (self._slide_t - 18) / (total * 0.72))
                epp = 1.0 - (1.0 - pp) ** 2
                self.py = (self._sh + H) + (self._ground_y - self._sh - H) * epp
                self._place()

            if self._slide_t >= total:
                # Snap to rest, celebrate
                self.py       = self._ground_y
                self.house.hy = target_hy
                self._place()
                self.house._place()
                self._slide_anim = None
                self.mood = 'excited'
                self._wiggle()
                self._spawn_particle('❤️', count=4)
                self.desktop_btn.refresh()
                self._save_state()

    # ── Main loop ─────────────────────────────────────────────────────────────
    def _loop(self):
        self.desktop_btn.keep_lowered()
        self._update()
        self._draw()
        self.root.after(FRAME_MS, self._loop)

    # ── Shared tick helpers ───────────────────────────────────────────────────
    def _tick_bob(self):
        self.bob = (self.bob + 0.05) % (2 * math.pi)

    def _tick_blink(self):
        self.blink_t -= 1
        if self.blink_t <= 0:
            if not self.blinking:
                self.blinking = True
                self.blink_ph = 1
                self.blink_t  = 5
            else:
                self.blink_ph += 1
                if self.blink_ph > 3:
                    self.blinking = False
                    self.blink_ph = 0
                    self.blink_t  = random.randint(150, 280)
                else:
                    self.blink_t = 5

    # ── Update ────────────────────────────────────────────────────────────────
    def _update(self):
        if self._slide_anim == 'hidden':
            return
        if self._slide_anim in ('out', 'in'):
            self._update_slide()
            return
        self.house.update()

        if self.state == S_IN_HOUSE:
            return

        self._update_particles()  # always run for all visible states

        if self.state == S_EXITING:
            self._update_exit()
            self._tick_bob()
            self._tick_blink()
            return

        if self.state == S_RETURNING:
            self._update_return()
            self._tick_bob()
            self._tick_blink()
            return

        if self.state == S_ENTERING:
            self._update_enter()
            self._tick_bob()
            return

        # ── S_ROAMING ─────────────────────────────────────────────────────────
        self._tick_bob()

        if self._wig_rem > 0:
            self.wiggle += self._wig_dir * 3.5
            if abs(self.wiggle) > 13:
                self._wig_dir *= -1
            self._wig_rem -= 1
            if self._wig_rem == 0:
                self.wiggle = 0.0

        self._tick_blink()

        if self.eating:
            self._eat_t += 1
            if self._eat_t > 50:
                self.eating = False
                self._eat_t = 0

        if self.playing:
            self._play_t -= 1
            if self._play_t <= 0:
                self.playing = False
                self.vx = random.choice([-1, 1]) * random.uniform(0.7, 1.3)
                self._calc_mood()

        if self.dancing:
            self._dance_t -= 1
            if self._dance_t % 18 < 9:
                if self._wig_rem <= 0:
                    self._wig_rem = 9
                    self._wig_dir = 1 if (self._dance_t // 9) % 2 == 0 else -1
            if self._dance_t % 38 == 0 and not self._jumping:
                self._do_jump()
            if self._dance_t % 70 == 0:
                self._spawn_particle('🎵')
            if self._dance_t <= 0:
                self.dancing = False
                self.vx = random.choice([-1, 1]) * random.uniform(0.7, 1.3)
                self._calc_mood()

        if self.bathing:
            self._bath_t -= 1
            if self._bath_t % 14 == 0:
                self._spawn_particle('🫧')
            if self._bath_t <= 0:
                self.bathing = False

        if self.mood == 'sleepy':
            self._zzz_t -= 1
            if self._zzz_t <= 0:
                self._zzz_t = random.randint(80, 140)
                self._spawn_particle('💤')

        self._tod_t -= 1
        if self._tod_t <= 0:
            self._tod_t = 1800
            h = time.localtime().tm_hour
            if 22 <= h or h < 6:
                self.happiness = max(0,   self.happiness - 4)
                self.boredom   = min(100, self.boredom   + 3)
            elif 6 <= h < 9:
                self.happiness = min(100, self.happiness + 6)
                self.boredom   = max(0,   self.boredom   - 6)
            self._calc_mood()

        if (self.happiness < 10 and self.state == S_ROAMING
                and not self.dancing and not self.bathing
                and not self._following and not self._dragging):
            self._return_home()

        if not self._jumping and not self._following and not self._dragging and not self.dancing:
            self._jump_t -= 1
            if self._jump_t <= 0:
                self._do_jump()

        if self._jumping:
            self._jump_vy += GRAVITY
            self._jump_dy -= self._jump_vy
            if self._jump_dy <= 0:
                self._jump_dy = 0.0
                self._jump_vy = 0.0
                self._jumping = False
                self._jump_t  = random.randint(350, 650)

        self.py = self._ground_y - self._jump_dy

        if self._following:
            mx = self.root.winfo_pointerx()
            self.px = float(max(W // 2, min(self._sw - W // 2, mx)))
        elif not self._dragging:
            spd = 3.5 if self.dancing else (3.0 if self.playing else 1.0)
            self.px += self.vx * spd
            hw = W / 2
            if self.px - hw < 0:
                self.px = hw;              self.vx =  abs(self.vx)
            if self.px + hw > self._sw:
                self.px = self._sw - hw;   self.vx = -abs(self.vx)
            if not self.playing and random.random() < 0.008:
                self.vx = max(-1.8, min(1.8, self.vx + random.uniform(-0.25, 0.25)))

        self._place()

        self._dart_t -= 1
        if self._dart_t <= 0:
            self._dart_t  = random.randint(15, 40)
            self._dart_tx = random.uniform(-5, 5)
            self._dart_ty = random.uniform(-4, 4)
        self._dart_x += (self._dart_tx - self._dart_x) * 0.18
        self._dart_y += (self._dart_ty - self._dart_y) * 0.18

        self._decay_t += 1
        if self._decay_t >= 60 * 8:
            self._decay_t   = 0
            self.hunger     = min(100, self.hunger   + 3)
            self.boredom    = min(100, self.boredom  + 4)
            self.happiness  = max(0,   self.happiness - 2)
            self._calc_mood()

    # ── House state transitions ───────────────────────────────────────────────
    def _exit_house(self):
        self.state       = S_EXITING
        self._exit_frame = 0
        self.px          = float(self.house.hx)
        self.py          = self._ground_y
        self.vx          = random.choice([-1, 1]) * 2.0
        self.mood        = 'excited'
        self._wig_rem    = 30
        self._wig_dir    = 1
        self._jumping    = False
        self._jump_dy    = 0.0
        self._jump_vy    = 0.0
        self._place()
        self.root.deiconify()
        self.root.lift()
        self._lowered_for_return = False
        self.house.open_door()

    def _return_home(self):
        self.state               = S_RETURNING
        self.mood                = 'sleepy'
        self._lowered_for_return = False
        self._jumping = False
        self._jump_dy = 0.0
        self._jump_vy = 0.0
        self.py       = self._ground_y
        self.wiggle   = 0.0
        self._wig_rem = 0

    def _update_exit(self):
        self._exit_frame += 1
        if self._exit_frame < 25:
            self.px = float(self.house.hx)
            self.py = self._ground_y
        elif self._exit_frame < 60:
            self.px += self.vx * 2.5
            hw = W / 2
            if self.px - hw < 0:
                self.px = hw;              self.vx =  abs(self.vx)
            if self.px + hw > self._sw:
                self.px = self._sw - hw;   self.vx = -abs(self.vx)
            self.py = self._ground_y
        else:
            self.state = S_ROAMING
            self.house.close_door()
            self._jump_t = random.randint(200, 400)
            self._calc_mood()
        self._place()

    def _update_return(self):
        target_x = self.house.hx
        dx   = target_x - self.px
        dist = abs(dx)

        if dist < 90:
            self.house.open_door()
            if not self._lowered_for_return:
                self._lowered_for_return = True
                try:
                    self.root.lower(self.house.win)
                except Exception:
                    pass

        speed = 2.0
        if dist > speed:
            self.px += math.copysign(speed, dx)
            self.vx  = math.copysign(1.0, dx)
        else:
            self.px           = target_x
            self.state        = S_ENTERING
            self._enter_frame = 0
            try:
                self.house.win.lift()
            except Exception:
                pass

        self.py = self._ground_y
        self._place()

    def _update_enter(self):
        self._enter_frame += 1
        self.px += (self.house.hx - self.px) * 0.18
        self.py  = self._ground_y
        self._place()

        if self._enter_frame >= 32:
            self.state = S_IN_HOUSE
            self.root.withdraw()
            self.house.close_door()
            self._particles.clear()

    # ── Jump ─────────────────────────────────────────────────────────────────
    def _do_jump(self):
        self._jumping  = True
        self._jump_vy  = -9.0
        self._jump_dy  = 0.0
        if self.mood not in ('excited', 'surprised'):
            self.mood = 'excited'
            self.root.after(800, self._calc_mood)

    def _calc_mood(self):
        if self.hunger > 70:
            self.mood = 'hungry'
        elif self.boredom > 65:
            self.mood = 'bored'
        elif self.happiness > 75:
            self.mood = 'happy'
        elif self.happiness < 25:
            self.mood = 'sleepy'
        else:
            self.mood = 'bored'

    def _place(self):
        canvas_h = H + int(self._jump_dy)
        x = int(self.px - W // 2)
        y = int(self.py - H // 2)   # py already includes -jump_dy, so bottom stays at sh
        self.canvas.config(height=canvas_h)
        self.root.geometry(f'{W}x{canvas_h}+{x}+{y}')

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _draw(self):
        if self.state == S_IN_HOUSE or self._slide_anim == 'hidden':
            return

        c = self.canvas
        c.delete('all')

        # Background scene — sky fills up to the ground, ground stays at screen bottom
        canvas_h  = H + int(self._jump_dy)
        ground_top = canvas_h - 22
        c.create_rectangle(0, 0, W, ground_top, fill='#e0f2fe', outline='')
        c.create_rectangle(0, ground_top, W, canvas_h, fill='#86efac', outline='')
        c.create_rectangle(0, ground_top, W, ground_top + 4, fill='#4ade80', outline='')

        bob_offset = 0.0 if (self._jumping or self._jump_dy > 0) else abs(math.sin(self.bob)) * 3
        cx = W // 2
        cy = H // 2 - bob_offset

        self._body(c, cx, cy)
        self._eyes(c, cx, cy)
        self._mood_dot(c, cx, cy)
        if self.eating:
            self._food(c, cx, cy)
        self._draw_particles(c)

    def _body(self, c, cx, cy):
        m       = self.mood
        fill    = BODY_CLR.get(m, '#60a5fa')
        outline = OUTLINE.get(m, '#1d4ed8')
        ang     = math.radians(self.wiggle)
        hw, hh, r = PET_W / 2, PET_H / 2, CORNER_R

        corners = [
            (cx - hw + r, cy - hh + r, 180),
            (cx + hw - r, cy - hh + r, 270),
            (cx + hw - r, cy + hh - r, 0),
            (cx - hw + r, cy + hh - r, 90),
        ]
        pts = []
        for (ex, ey, sa) in corners:
            for deg in range(sa, sa + 91, 9):
                rd = math.radians(deg)
                pts.append(ex + r * math.cos(rd))
                pts.append(ey + r * math.sin(rd))

        if ang:
            co, si = math.cos(ang), math.sin(ang)
            rot = []
            for i in range(0, len(pts), 2):
                dx, dy = pts[i] - cx, pts[i + 1] - cy
                rot += [cx + dx * co - dy * si, cy + dx * si + dy * co]
            pts = rot

        c.create_polygon(pts, fill=fill, outline=outline, width=2.5, smooth=True)

    def _eyes(self, c, cx, cy):
        ang = math.radians(self.wiggle)
        lx, ly = cx - 20, cy - 8
        rx, ry = cx + 20, cy - 8
        if ang:
            co, si = math.cos(ang), math.sin(ang)
            def rot(x, y):
                dx, dy = x - cx, y - cy
                return cx + dx * co - dy * si, cy + dx * si + dy * co
            lx, ly = rot(lx, ly)
            rx, ry = rot(rx, ry)
        self._eye(c, lx, ly, left=True)
        self._eye(c, rx, ry, left=False)

    def _eye(self, c, ex, ey, left):
        m  = self.mood
        r  = EYE_R
        bf = BODY_CLR.get(m, '#60a5fa')

        if self.blinking and m not in ('sleepy',):
            if self.blink_ph == 1:
                c.create_arc(ex - r, ey - r, ex + r, ey + r,
                             start=180, extent=180, fill='#1e293b', outline='')
                return
            if self.blink_ph == 2:
                c.create_line(ex - r, ey, ex + r, ey,
                              fill='#1e293b', width=3, capstyle='round')
                return

        if m == 'happy':
            c.create_oval(ex-r, ey-r, ex+r, ey+r, fill='#1e293b', outline='')
            c.create_oval(ex-5, ey-5, ex-1, ey-1, fill='white', outline='')
            c.create_oval(ex+1, ey-3, ex+3, ey-1, fill='white', outline='')
        elif m == 'sleepy':
            c.create_oval(ex-r, ey-r, ex+r, ey+r, fill='#1e293b', outline='')
            c.create_rectangle(ex-r-1, ey+1, ex+r+1, ey+r+2, fill=bf, outline='')
            c.create_line(ex-r, ey+1, ex+r, ey+1, fill='#1e293b', width=2)
        elif m == 'excited':
            br = r + 6
            c.create_oval(ex-br, ey-br, ex+br, ey+br,
                          fill='white', outline='#1e293b', width=2)
            px = ex + self._dart_x; py = ey + self._dart_y
            c.create_oval(px-5, py-5, px+5, py+5, fill='#1e293b', outline='')
            c.create_oval(px-8, py-8, px-4, py-4, fill='white', outline='')
        elif m == 'bored':
            c.create_oval(ex-r, ey-r, ex+r, ey+r,
                          fill='white', outline='#7c3aed', width=1.5)
            off = 4 if left else -4
            c.create_oval(ex+off-5, ey-5, ex+off+5, ey+5, fill='#334155', outline='')
        elif m == 'surprised':
            sr = r + 8
            c.create_oval(ex-sr, ey-sr, ex+sr, ey+sr,
                          fill='white', outline='#1e293b', width=2.5)
            c.create_oval(ex-4, ey-4, ex+4, ey+4, fill='#1e293b', outline='')
        elif m == 'hungry':
            c.create_oval(ex-r, ey-r, ex+r, ey+r,
                          fill='white', outline='#b45309', width=1.5)
            c.create_oval(ex-4, ey+1, ex+4, ey+9, fill='#1e293b', outline='')
        else:
            c.create_oval(ex-r, ey-r, ex+r, ey+r, fill='#1e293b', outline='')

    def _mood_dot(self, c, cx, cy):
        color = MOOD_DOT.get(self.mood, '#4ade80')
        dx = cx + PET_W / 2 - 12
        dy = cy - PET_H / 2 + 10
        c.create_oval(dx-5, dy-5, dx+5, dy+5,
                      fill=color, outline='white', width=1)

    def _food(self, c, cx, cy):
        munch = abs(math.sin(self._eat_t * 0.4)) * 5
        fy = cy + 16 + munch   # mouth is in the lower third of the face
        c.create_text(cx, fy, text='🍪', font=('', 14))

    # ── Input ─────────────────────────────────────────────────────────────────
    def _press(self, e):
        self._press_rx = e.x_root
        self._press_ry = e.y_root
        self._drag_ox  = e.x_root - self.px
        self._dragging = True

    def _drag(self, e):
        if self.state in (S_IN_HOUSE, S_ENTERING):
            return
        self.px = e.x_root - self._drag_ox
        self.py = self._ground_y
        self._place()

    def _release(self, e):
        self._dragging = False
        dist = math.hypot(e.x_root - self._press_rx, e.y_root - self._press_ry)
        if dist < 8:
            self._pet()

    def _pet(self):
        if self.state in (S_IN_HOUSE, S_ENTERING):
            return
        self.mood      = 'happy'
        self.happiness = min(100, self.happiness + 25)
        self.boredom   = max(0,   self.boredom   - 20)
        self._wig_rem  = 40
        self._wig_dir  = 1
        self._spawn_particle('❤️', count=3)

    # ── Right-click: grab + follow ─────────────────────────────────────────────
    def _rclick(self, e):
        if self.state in (S_IN_HOUSE, S_ENTERING, S_RETURNING):
            return
        if self._following:
            return
        self._following = True
        self.mood = 'surprised'
        self._show_panel()

    def _show_panel(self):
        panel = tk.Toplevel(self.root)
        panel.overrideredirect(True)
        panel.attributes('-topmost', True)
        panel.configure(bg='#1e293b')
        self._follow_panel = panel

        tk.Label(panel, text='Bitbe!', bg='#1e293b', fg='#93c5fd',
                 font=('Segoe UI', 9, 'bold')).pack(padx=12, pady=(8, 3))

        btn = dict(bg='#334155', fg='white', activebackground='#475569',
                   font=('Segoe UI', 9), relief='flat', bd=0,
                   padx=12, pady=5, cursor='hand2')

        tk.Button(panel, text='🍪  Feed',  command=self._pnl_feed,  **btn
                  ).pack(fill='x', padx=8, pady=2)
        tk.Button(panel, text='🎮  Play',  command=self._pnl_play,  **btn
                  ).pack(fill='x', padx=8, pady=2)
        tk.Button(panel, text='💃  Dance', command=self._pnl_dance, **btn
                  ).pack(fill='x', padx=8, pady=2)
        tk.Button(panel, text='🛁  Bath',  command=self._pnl_bath,  **btn
                  ).pack(fill='x', padx=8, pady=2)
        tk.Button(panel, text='💬  Talk',  command=self._pnl_talk,  **btn
                  ).pack(fill='x', padx=8, pady=2)

        tk.Frame(panel, bg='#334155', height=1).pack(fill='x', padx=8, pady=(4, 2))

        sf = tk.Frame(panel, bg='#1e293b')
        sf.pack(fill='x', padx=10, pady=(0, 4))

        def _bar(label, val, color):
            row = tk.Frame(sf, bg='#1e293b')
            row.pack(fill='x', pady=1)
            tk.Label(row, text=label, bg='#1e293b', fg='#94a3b8',
                     font=('Segoe UI', 7), width=7, anchor='w').pack(side='left')
            bg = tk.Frame(row, bg='#334155', height=5, width=60)
            bg.pack(side='left', padx=(2, 0))
            w_fill = max(1, int(val * 0.6))
            tk.Frame(bg, bg=color, height=5, width=w_fill).place(x=0, y=0)

        _bar('Happy', self.happiness,        '#4ade80')
        _bar('Food',  100 - self.hunger,     '#fbbf24')
        _bar('Fun',   100 - self.boredom,    '#a78bfa')

        tk.Frame(panel, bg='#334155', height=1).pack(fill='x', padx=8, pady=(2, 4))

        tk.Button(panel, text='✕  Release', command=self._release_follow,
                  bg='#1e293b', fg='#64748b', activebackground='#1e293b',
                  font=('Segoe UI', 8), relief='flat', bd=0,
                  padx=12, pady=3, cursor='hand2').pack(pady=(0, 7))

        self._track_panel(panel)

    def _track_panel(self, panel):
        if not panel.winfo_exists() or not self._following:
            return
        panel.update_idletasks()
        pw = panel.winfo_reqwidth()
        ph = panel.winfo_reqheight()
        px = int(self.px - pw // 2)
        py = int(self._sh - H - ph - 6)
        px = max(0, min(self._sw - pw, px))
        py = max(0, py)
        panel.geometry(f'+{px}+{py}')
        panel.after(FRAME_MS, lambda: self._track_panel(panel))

    def _release_follow(self):
        self._following = False
        if self._follow_panel and self._follow_panel.winfo_exists():
            self._follow_panel.destroy()
        self._follow_panel = None
        self._calc_mood()

    def _pnl_feed(self):
        self._release_follow();  self._feed()

    def _pnl_play(self):
        self._release_follow();  self._play()

    def _pnl_dance(self):
        self._release_follow();  self._dance()

    def _pnl_bath(self):
        self._release_follow();  self._bath()

    def _pnl_talk(self):
        self._release_follow();  self._talk()

    # ── Actions ───────────────────────────────────────────────────────────────
    def _feed(self):
        self.eating    = True
        self._eat_t    = 0
        self.hunger    = max(0,   self.hunger    - 50)
        self.happiness = min(100, self.happiness + 15)
        self.mood      = 'happy'
        self.root.after(3000, self._calc_mood)

    def _play(self):
        self.playing   = True
        self._play_t   = 240
        self.vx        = random.choice([-1, 1]) * 3.0
        self.mood      = 'excited'
        self.boredom   = max(0,   self.boredom   - 40)
        self.happiness = min(100, self.happiness + 25)
        self.root.after(500, self._do_jump)

    def _talk(self):
        if not self._client:
            messagebox.showinfo(
                'Bitbe says:',
                'Add ANTHROPIC_API_KEY to .env to chat with me!\n\n*sad blorp*',
                parent=self.root,
            )
            return
        self.mood = 'surprised'
        self.root.after(1500, lambda: setattr(self, 'mood', 'excited'))
        self._chat_win = _ChatWindow(self.root, self)

    def _dance(self):
        if self.state in (S_IN_HOUSE, S_ENTERING, S_RETURNING):
            return
        self.dancing   = True
        self._dance_t  = 360
        self.boredom   = max(0,   self.boredom   - 60)
        self.happiness = min(100, self.happiness  + 30)
        self.mood      = 'excited'
        self._spawn_particle('🎵', count=3)

    def _wiggle(self):
        self._wig_rem = 50
        self._wig_dir = 1

    def _bath(self):
        if self.state in (S_IN_HOUSE, S_ENTERING, S_RETURNING):
            return
        self.bathing   = True
        self._bath_t   = 200
        self.happiness = min(100, self.happiness + 20)
        self.hunger    = max(0,   self.hunger    - 10)
        self.mood      = 'happy'
        self._spawn_particle('🫧', count=4)
        self.root.after(3500, self._calc_mood)

    # ── Particle system ───────────────────────────────────────────────────────
    def _spawn_particle(self, emoji, count=1):
        # Store in screen coordinates so they float correctly as pet moves
        sx = self.px
        sy = self.py - 24 - self._jump_dy
        for _ in range(count):
            self._particles.append({
                'x':    sx + random.uniform(-22, 22),
                'y':    sy + random.uniform(-8, 8),
                'emoji': emoji,
                'vy':   random.uniform(0.55, 1.0),
                'vx':   random.uniform(-0.35, 0.35),
                'life': random.randint(45, 70),
            })

    def _update_particles(self):
        kept = []
        for p in self._particles:
            p['life'] -= 1
            p['y']    -= p['vy']
            p['x']    += p['vx']
            if p['life'] > 0:
                kept.append(p)
        self._particles = kept

    def _draw_particles(self, c):
        for p in self._particles:
            # Convert screen→canvas coordinates
            px = p['x'] - self.px + W // 2
            py = p['y'] - self.py + H // 2
            glyph, fill = PARTICLE_STYLES.get(p['emoji'], (p['emoji'], 'white'))
            c.create_text(int(px), int(py), text=glyph,
                          fill=fill, font=('', 13, 'bold'))

    def run(self):
        self.root.mainloop()


# ── Chat window ───────────────────────────────────────────────────────────────
class _ChatWindow:
    _SYSTEM = (
        "You are Bitbe, an adorable virtual desktop pet who looks like a rounded blue square "
        "with big expressive circular eyes. You live on your human's computer screen. "
        "Personality: bubbly, silly, easily excited, loves snacks and zooming around. "
        "You also have a cozy little house on the desktop that you can go in and out of. "
        "Rules: keep every reply to 1-2 sentences max. "
        "Use action emotes — they trigger REAL animations on screen: "
        "*zooms* or *dashes* (runs fast across screen), "
        "*dances* or *spins* (dance animation), "
        "*boings* or *jumps* (jump), "
        "*bounces* (excited bouncing), "
        "*wiggles* (wiggles body), "
        "*squeaks* (heart particles float up), "
        "*takes a bath* (bubble bath), "
        "*eats snack* or *munches* (eating animation). "
        "Use these emotes naturally in your replies to express yourself. "
        "Never break character. You have a name: Bitbe."
    )

    def __init__(self, parent, pet):
        self.pet = pet
        w = tk.Toplevel(parent)
        w.title('💬 Bitbe Chat')
        w.geometry('320x260')
        w.attributes('-topmost', True)
        w.resizable(True, True)
        w.configure(bg='#0f172a')
        self._win = w

        self._txt = tk.Text(
            w, wrap='word', state='disabled',
            bg='#0f172a', fg='#e2e8f0',
            font=('Segoe UI', 10), padx=8, pady=6,
            relief='flat', bd=0,
        )
        self._txt.pack(fill='both', expand=True, padx=8, pady=(8, 0))

        bar = tk.Frame(w, bg='#1e293b', pady=6)
        bar.pack(fill='x', padx=8, pady=6)

        self._ent = tk.Entry(
            bar, bg='#334155', fg='white',
            insertbackground='white',
            font=('Segoe UI', 10), relief='flat', bd=4,
        )
        self._ent.pack(side='left', fill='x', expand=True, ipady=4)
        self._ent.bind('<Return>', lambda _: self._send())
        self._ent.focus()

        tk.Button(
            bar, text='Send', bg='#3b82f6', fg='white',
            font=('Segoe UI', 10, 'bold'), relief='flat',
            activebackground='#2563eb', bd=0, padx=10,
            command=self._send,
        ).pack(side='right', padx=(6, 0))

        greeting = '*bounces* HIIII!! You wanna chat?? yaaay!!! *spins in a circle*'
        self._append('Bitbe', greeting)
        self._win.after(400, lambda: self._trigger_actions(greeting))

    def _trigger_actions(self, text):
        import re
        pet = self.pet
        if pet.state not in (S_ROAMING, S_EXITING):
            return

        emotes = re.findall(r'\*([^*]+)\*', text.lower())
        triggered = set()

        ACTIONS = [
            (['zoom', 'dash', 'run', 'race'],          pet._play),
            (['danc', 'spin', 'twirl'],                 pet._dance),
            (['boing', 'jump', 'leap', 'bounce'],       pet._do_jump),
            (['wiggl', 'waggl', 'shake'],               pet._wiggle),
            (['squeak', 'heart', 'love', 'yay'],        lambda: pet._spawn_particle('❤️', count=3)),
            (['bath', 'splash', 'bubble', 'wash'],      pet._bath),
            (['eat', 'munch', 'nom', 'snack', 'cookie'], pet._feed),
            (['zzz', 'sleep', 'nap', 'yawn'],           lambda: setattr(pet, 'mood', 'sleepy')),
        ]

        for emote in emotes:
            for keywords, action in ACTIONS:
                if any(kw in emote for kw in keywords):
                    key = keywords[0]
                    if key not in triggered:
                        triggered.add(key)
                        act = action
                        self._win.after(0, act)
                    break

    def _append(self, who, msg):
        self._txt.config(state='normal')
        tag = 'sq' if who == 'Bitbe' else 'you'
        self._txt.insert('end', f'{who}: ', tag)
        self._txt.insert('end', msg + '\n\n')
        self._txt.tag_config('sq',  foreground='#60a5fa')
        self._txt.tag_config('you', foreground='#4ade80')
        self._txt.see('end')
        self._txt.config(state='disabled')

    def _send(self):
        msg = self._ent.get().strip()
        if not msg:
            return
        self._ent.delete(0, 'end')
        self._append('You', msg)
        self.pet._chat_hist.append({'role': 'user', 'content': msg})

        def worker():
            try:
                resp = self.pet._client.messages.create(
                    model='claude-haiku-4-5-20251001',
                    max_tokens=120,
                    system=self._SYSTEM,
                    messages=self.pet._chat_hist,
                )
                reply = resp.content[0].text
            except Exception as ex:
                reply = f'*confused bloop* (uh oh: {ex})'

            self.pet._chat_hist.append({'role': 'assistant', 'content': reply})
            self.pet.mood      = 'excited'
            self.pet.happiness = min(100, self.pet.happiness + 10)
            self._win.after(0, lambda r=reply: self._append('Bitbe', r))
            self._win.after(300, lambda r=reply: self._trigger_actions(r))
            self._win.after(6000, self.pet._calc_mood)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == '__main__':
    Pet().run()