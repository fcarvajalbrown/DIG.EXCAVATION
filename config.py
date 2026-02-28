"""
config.py — Global configuration for dig_excavation.

All tunable constants live here. No game logic; pure data.
Imported by any module that needs settings — never the other way around.
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
DATA_DIR   = os.path.join(ROOT_DIR, "data")
SAVES_DIR  = os.path.join(DATA_DIR, "saves")

FONT_PATH          = os.path.join(ASSETS_DIR, "fonts", "terminal_font.ttf")
CRT_OVERLAY_PATH   = os.path.join(ASSETS_DIR, "images", "crt_overlay.png")
MONITOR_FRAME_PATH = os.path.join(ASSETS_DIR, "images", "monitor_frame.png")

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

DEFAULT_WIDTH  = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS    = 60
WINDOW_TITLE   = "DIG.EXCAVATION"

# OpenGL near/far clipping planes and field of view
GL_FOV   = 45.0   # degrees
GL_NEAR  = 0.1
GL_FAR   = 100.0

# ---------------------------------------------------------------------------
# Monitor (3D plane in OpenGL world space)
# ---------------------------------------------------------------------------

# The terminal is rendered onto a quad positioned in 3D space.
# These values control the default camera view to simulate a
# slightly-angled CRT monitor sitting on a desk.

MONITOR_QUAD_WIDTH  = 3.2   # world-space units
MONITOR_QUAD_HEIGHT = 1.8   # world-space units (16:9 at this scale)

# Camera position (x, y, z) — offset from the monitor center
CAMERA_POS      = (0.0, 0.3, 4.0)
# Point the camera looks at
CAMERA_TARGET   = (0.0, 0.0, 0.0)
# Tilt of the monitor quad around the X axis (degrees); positive = top leans back
MONITOR_TILT_X  = 8.0
# Slight Y rotation to give a mild off-center perspective (degrees)
MONITOR_TILT_Y  = -3.0

# ---------------------------------------------------------------------------
# Terminal (the virtual text surface rendered as the monitor texture)
# ---------------------------------------------------------------------------

# Resolution of the offscreen surface that acts as the terminal "screen".
# Independent of display resolution — gets texture-mapped onto the 3D quad.
TERMINAL_WIDTH  = 1280
TERMINAL_HEIGHT = 720

TERMINAL_FONT_SIZE  = 16   # px, at TERMINAL_WIDTH x TERMINAL_HEIGHT
TERMINAL_COLS       = 80
TERMINAL_ROWS       = 40
TERMINAL_LINE_HEIGHT = TERMINAL_HEIGHT // TERMINAL_ROWS

# ---------------------------------------------------------------------------
# Colour palette  (CRT amber-on-black default)
# ---------------------------------------------------------------------------

COLOR_BG          = (10,  10,  10)    # near-black background
COLOR_FG          = (255, 176,  0)    # amber phosphor text
COLOR_FG_DIM      = (140,  96,  0)    # dimmed / secondary text
COLOR_CURSOR      = (255, 220, 80)    # cursor blink color
COLOR_HIGHLIGHT   = (255, 255, 180)   # selected / important text
COLOR_ERROR       = (220,  50,  50)   # error messages
COLOR_DAEMON      = (180,  30,  30)   # daemon threat indicators
COLOR_SCANLINE    = (0,    0,   0,  60)  # RGBA — semi-transparent scanline overlay

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

AUDIO_FREQUENCY  = 44100
AUDIO_CHANNELS   = 2
AUDIO_BUFFER     = 512
MASTER_VOLUME    = 0.8   # 0.0 – 1.0

# ---------------------------------------------------------------------------
# Gameplay
# ---------------------------------------------------------------------------

# Starting resource values for a new dig session
STARTING_CPU    = 100
STARTING_MEMORY = 64    # MB (narrative unit, not real)
STARTING_ENERGY = 100

# How many turns a daemon waits before moving when the player is idle
DAEMON_IDLE_PATIENCE = 3

# Probability that a file node is corrupted during procgen (0.0–1.0)
CORRUPTION_CHANCE = 0.25

# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

SAVE_FORMAT  = "yaml"   # "yaml" or "json"
MAX_SAVES    = 5