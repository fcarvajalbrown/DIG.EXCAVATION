"""
core/monitor_view.py — 3D monitor effect for dig_excavation.

Owns the OpenGL context, camera, and the terminal quad.
The terminal surface (a pygame.Surface) is passed in each frame,
uploaded as an OpenGL texture, and rendered onto a tilted 3D plane
to simulate a CRT monitor viewed at a slight angle.

Architecture note:
    This module is the ONLY place that touches OpenGL directly.
    Everything else in the codebase works with plain pygame Surfaces.
    If the rendering backend ever needs to change, only this file changes.

Dependencies:
    pygame, PyOpenGL, PyOpenGL_accelerate (optional but recommended)
"""

import pygame
import numpy as np

from OpenGL.GL import (
    glEnable, glDisable, glClear, glClearColor,
    glGenTextures, glBindTexture, glTexImage2D, glTexParameteri,
    glTexSubImage2D,
    glMatrixMode, glLoadIdentity, glPushMatrix, glPopMatrix,
    glTranslatef, glRotatef,
    glBegin, glEnd, glTexCoord2f, glVertex3f,
    glBlendFunc, glColor4f,
    GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
    GL_LINEAR, GL_NEAREST,
    GL_RGB, GL_RGBA, GL_UNSIGNED_BYTE,
    GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT,
    GL_DEPTH_TEST, GL_BLEND,
    GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,
    GL_MODELVIEW, GL_PROJECTION,
    GL_QUADS,
)
from OpenGL.GLU import gluPerspective, gluLookAt

import config


class MonitorView:
    """
    Manages the OpenGL scene that displays the terminal as a tilted 3D quad.

    Usage (inside the main game loop):
        monitor = MonitorView()
        monitor.init()

        # each frame:
        terminal_surface = ...  # pygame.Surface with text drawn on it
        monitor.render(terminal_surface)
        pygame.display.flip()

    The caller is responsible for calling pygame.display.flip() after render().
    """

    def __init__(self):
        """
        Store configuration references. Does not touch OpenGL yet —
        init() must be called after pygame + OpenGL display mode is set.
        """
        self._display_w = config.DEFAULT_WIDTH
        self._display_h = config.DEFAULT_HEIGHT

        self._quad_w = config.MONITOR_QUAD_WIDTH
        self._quad_h = config.MONITOR_QUAD_HEIGHT

        self._camera_pos    = config.CAMERA_POS
        self._camera_target = config.CAMERA_TARGET
        self._tilt_x        = config.MONITOR_TILT_X
        self._tilt_y        = config.MONITOR_TILT_Y

        self._texture_id: int = 0
        self._tex_w = config.TERMINAL_WIDTH
        self._tex_h = config.TERMINAL_HEIGHT

        # CRT overlay texture (scanlines), optional
        self._overlay_texture_id: int = 0
        self._has_overlay: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init(self) -> None:
        """
        Set up OpenGL state and allocate GPU textures.
        Must be called once after pygame.display.set_mode() with OPENGL flag.
        """
        self._setup_gl_state()
        self._texture_id = self._alloc_texture(self._tex_w, self._tex_h)
        self._load_overlay()

    def render(self, terminal_surface: pygame.Surface) -> None:
        """
        Upload the terminal surface as a texture and draw the 3D monitor scene.

        Args:
            terminal_surface: A pygame.Surface of size (TERMINAL_WIDTH, TERMINAL_HEIGHT)
                              with the current frame of terminal content drawn on it.
        """
        self._upload_texture(self._texture_id, terminal_surface)
        self._draw_scene()

        if self._has_overlay:
            self._draw_overlay()

    def resize(self, width: int, height: int) -> None:
        """
        Handle window resize events. Recalculates the projection matrix.

        Args:
            width:  New window width in pixels.
            height: New window height in pixels.
        """
        self._display_w = width
        self._display_h = height
        self._setup_projection(width, height)

    def set_camera(
        self,
        pos: tuple[float, float, float] | None = None,
        target: tuple[float, float, float] | None = None,
        tilt_x: float | None = None,
        tilt_y: float | None = None,
    ) -> None:
        """
        Update camera parameters at runtime (e.g. from the settings UI).
        Any argument left as None keeps its current value.

        Args:
            pos:    Camera position (x, y, z) in world space.
            target: Point the camera looks at (x, y, z).
            tilt_x: Monitor tilt around X axis in degrees (top leans back).
            tilt_y: Monitor tilt around Y axis in degrees (slight side angle).
        """
        if pos    is not None: self._camera_pos    = pos
        if target is not None: self._camera_target = target
        if tilt_x is not None: self._tilt_x        = tilt_x
        if tilt_y is not None: self._tilt_y        = tilt_y

    def cleanup(self) -> None:
        """
        Release GPU resources. Call before pygame.quit().
        """
        from OpenGL.GL import glDeleteTextures
        if self._texture_id:
            glDeleteTextures([self._texture_id])
        if self._overlay_texture_id:
            glDeleteTextures([self._overlay_texture_id])

    # ------------------------------------------------------------------
    # Private — GL setup
    # ------------------------------------------------------------------

    def _setup_gl_state(self) -> None:
        """Configure global OpenGL state flags and clear color."""
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        r, g, b = (c / 255.0 for c in config.COLOR_BG)
        glClearColor(r, g, b, 1.0)

        self._setup_projection(self._display_w, self._display_h)

    def _setup_projection(self, width: int, height: int) -> None:
        """
        Set the perspective projection matrix.

        Args:
            width:  Viewport width in pixels.
            height: Viewport height in pixels.
        """
        from OpenGL.GL import glViewport
        glViewport(0, 0, width, height)

        aspect = width / max(height, 1)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(config.GL_FOV, aspect, config.GL_NEAR, config.GL_FAR)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    # ------------------------------------------------------------------
    # Private — Texture management
    # ------------------------------------------------------------------

    def _alloc_texture(self, width: int, height: int) -> int:
        """
        Allocate an uninitialized OpenGL texture of the given size.

        Args:
            width:  Texture width in pixels.
            height: Texture height in pixels.

        Returns:
            The OpenGL texture ID (integer handle).
        """
        tex_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

        # Allocate GPU memory with an empty buffer
        empty = np.zeros((height, width, 3), dtype=np.uint8)
        glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGB,
            width, height, 0,
            GL_RGB, GL_UNSIGNED_BYTE, empty,
        )
        return int(tex_id)

    def _upload_texture(self, tex_id: int, surface: pygame.Surface) -> None:
        """
        Upload a pygame Surface's pixel data to an existing OpenGL texture.
        The surface must match the texture dimensions (TERMINAL_WIDTH x TERMINAL_HEIGHT).

        Args:
            tex_id:  OpenGL texture handle.
            surface: pygame.Surface with the terminal frame drawn on it.
        """
        # pygame stores pixels in a different byte order than OpenGL expects
        rgb_surface = pygame.transform.flip(surface, False, True)
        raw = pygame.image.tostring(rgb_surface, "RGB", False)

        glBindTexture(GL_TEXTURE_2D, tex_id)
        glTexSubImage2D(
            GL_TEXTURE_2D, 0,
            0, 0,
            self._tex_w, self._tex_h,
            GL_RGB, GL_UNSIGNED_BYTE, raw,
        )

    def _load_overlay(self) -> None:
        """
        Attempt to load the CRT scanline overlay image as a texture.
        Silently skips if the file is not found — overlay is optional.
        """
        try:
            overlay_surf = pygame.image.load(config.CRT_OVERLAY_PATH).convert_alpha()
            overlay_surf = pygame.transform.scale(
                overlay_surf, (self._display_w, self._display_h)
            )
            tex_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            raw = pygame.image.tostring(overlay_surf, "RGBA", False)
            w, h = overlay_surf.get_size()
            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA,
                w, h, 0,
                GL_RGBA, GL_UNSIGNED_BYTE, raw,
            )
            self._overlay_texture_id = int(tex_id)
            self._has_overlay = True
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------
    # Private — Scene drawing
    # ------------------------------------------------------------------

    def _draw_scene(self) -> None:
        """
        Clear the buffer, position the camera, and draw the terminal quad.
        """
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        cx, cy, cz = self._camera_pos
        tx, ty, tz = self._camera_target
        gluLookAt(
            cx, cy, cz,   # eye
            tx, ty, tz,   # center
            0.0, 1.0, 0.0 # up vector
        )

        # Apply monitor tilt as local rotations on the quad
        glPushMatrix()
        glRotatef(self._tilt_x, 1.0, 0.0, 0.0)
        glRotatef(self._tilt_y, 0.0, 1.0, 0.0)

        self._draw_terminal_quad()

        glPopMatrix()

    def _draw_terminal_quad(self) -> None:
        """
        Draw a texture-mapped quad in the XY plane centered at the origin.
        UV coordinates map the terminal texture exactly onto the quad face.
        """
        hw = self._quad_w / 2.0   # half-width
        hh = self._quad_h / 2.0   # half-height

        glBindTexture(GL_TEXTURE_2D, self._texture_id)
        glColor4f(1.0, 1.0, 1.0, 1.0)  # no tint — pure texture color

        glBegin(GL_QUADS)
        # bottom-left
        glTexCoord2f(0.0, 0.0); glVertex3f(-hw, -hh, 0.0)
        # bottom-right
        glTexCoord2f(1.0, 0.0); glVertex3f( hw, -hh, 0.0)
        # top-right
        glTexCoord2f(1.0, 1.0); glVertex3f( hw,  hh, 0.0)
        # top-left
        glTexCoord2f(0.0, 1.0); glVertex3f(-hw,  hh, 0.0)
        glEnd()

    def _draw_overlay(self) -> None:
        """
        Draw the CRT scanline overlay as a full-screen 2D quad in orthographic
        projection, blended over the 3D scene.
        """
        from OpenGL.GL import (
            glOrtho, glDepthMask, GL_FALSE, GL_TRUE,
        )

        glDisable(GL_DEPTH_TEST)
        glDepthMask(GL_FALSE)

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, self._display_w, self._display_h, 0, -1, 1)

        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glBindTexture(GL_TEXTURE_2D, self._overlay_texture_id)
        glColor4f(1.0, 1.0, 1.0, 0.18)  # subtle overlay opacity

        w, h = float(self._display_w), float(self._display_h)
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 0.0); glVertex3f(0, 0, 0)
        glTexCoord2f(1.0, 0.0); glVertex3f(w, 0, 0)
        glTexCoord2f(1.0, 1.0); glVertex3f(w, h, 0)
        glTexCoord2f(0.0, 1.0); glVertex3f(0, h, 0)
        glEnd()

        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)

        glDepthMask(GL_TRUE)
        glEnable(GL_DEPTH_TEST)