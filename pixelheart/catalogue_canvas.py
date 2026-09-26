"""Furniture and farmer interaction previews; never a game or document editor.

Coordinates are unscaled sprite pixels. Furniture images are bottom aligned to
their 16-pixel tile footprint; an explicit seat is a farmer feet position relative
to that footprint. Walking routes stay outside the solid footprint. Only an
authored seat/sleep pose may enter it, and rugs deliberately allow crossing.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget


_DIRECTIONS = ("south", "east", "north", "west")
_NATIVE_FACING = {0: "north", 1: "east", 2: "south", 3: "west"}
_SEATS = {"chair", "armchair", "bench", "couch", "sofa", "stool"}
_TABLES = {"table", "longtable", "desk", "dining_table", "writing_desk"}
_BEDS = {"bed", "bed double", "bed child", "double_bed", "single_bed", "doublebed", "singlebed"}
_WALLS = {"window", "sconce", "painting", "wall_decor"}


def _point(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    if not all(isinstance(n, (int, float)) and math.isfinite(n) and abs(n) <= 4096 for n in value):
        return None
    return float(value[0]), float(value[1])


def _image(value):
    return value if isinstance(value, QImage) and not value.isNull() else None


def _direction(start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    return ("east" if dx > 0 else "west") if abs(dx) >= abs(dy) else ("south" if dy > 0 else "north")


class CataloguePreviewCanvas(QWidget):
    """One comparison pane, with shared-control signals and deterministic time.

    The owner connects ``clicked`` to both panes' ``play`` methods. ``sample``
    reads the current route at an absolute millisecond time without advancing it;
    ``advance`` is the deterministic equivalent of an animation timer tick.
    """

    clicked = Signal()
    state_changed = Signal(str)
    STAGE_SIZE = QSize(176, 160)
    WALK_SPEED = 32.0  # native pixels per second; independent of display zoom

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(self.STAGE_SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Furniture interaction preview")
        self._side = {}
        self._root = Path()
        self._actor = {}
        self._view = {}
        self._view_index = 0
        self._image = QImage()
        self._foreground = QImage()
        self._image_cache = {}
        self._visual_states = {}
        self._base_animation = []
        self._room = QImage()
        self._background_mode = "studio"
        self._time_of_day = "day"
        self._power = False
        self._effects_elapsed_ms = 0.0
        self._scale = 2
        self._interaction = True
        self._playing = False
        self._elapsed_ms = 0.0
        self._segments = []
        self._duration_ms = 0.0
        self._footprint = (16.0, 16.0)
        self._action = "walk"
        self._state = "idle"
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def sizeHint(self):
        return QSize(352, 320)

    @property
    def duration_ms(self):
        return self._duration_ms

    @property
    def elapsed_ms(self):
        return self._elapsed_ms

    @property
    def effects_elapsed_ms(self):
        return self._effects_elapsed_ms

    @property
    def is_playing(self):
        return self._playing

    @property
    def action(self):
        """The supported visual action: walk, cross, approach, sit, or sleep."""
        return self._action

    def set_scene(self, side: dict, root: Path, actor: dict | None = None,
                  background: QImage | None = None):
        self.stop()
        self._side = dict(side or {})
        self._root = Path(root).resolve()
        self._actor = dict(actor or {})
        self._room = QImage(background) if _image(background) is not None else QImage()
        self.set_view(0)

    def _load_image(self, value):
        if not isinstance(value, str) or not value:
            return QImage()
        if value in self._image_cache:
            return self._image_cache[value]
        path = (self._root / value).resolve()
        if not path.is_relative_to(self._root) or not path.is_file():
            return QImage()
        self._image_cache[value] = QImage(str(path))
        return self._image_cache[value]

    def set_view(self, index: int):
        self.stop()
        views = self._side.get("views", [])
        self._view_index = min(max(int(index), 0), max(0, len(views) - 1))
        self._view = dict(views[self._view_index]) if views else {}
        self._image_cache.clear()
        self._image = self._load_image(self._view.get("image"))
        self._foreground = self._load_image(self._view.get("foreground"))
        # An unrelated-size foreground cannot honestly demonstrate occlusion.
        if self._foreground.size() != self._image.size():
            self._foreground = QImage()
        tiles = _point(self._view.get("footprint")) or (1.0, 1.0)
        self._footprint = (max(1, min(32, tiles[0])) * 16, max(1, min(32, tiles[1])) * 16)
        self._visual_states = {}
        states = self._view.get("states", {})
        if isinstance(states, dict):
            for key, state in states.items():
                if not isinstance(state, dict):
                    continue
                image = self._load_image(state.get("image"))
                self._visual_states[key] = {
                    "image": image if image.size() == self._image.size() else self._image,
                    "frames": self._load_animation(state.get("animation_frames")),
                }
        self._base_animation = self._load_animation(self._view.get("animation_frames"))
        self._effects_elapsed_ms = 0.0
        self._build_route()
        self._sync_timer()
        self.update()

    def _load_animation(self, entries):
        frames = []
        if not isinstance(entries, (list, tuple)):
            return frames
        for entry in entries:
            path = entry.get("image") if isinstance(entry, dict) else entry
            duration = entry.get("duration_ms", 100) if isinstance(entry, dict) else 100
            image = self._load_image(path)
            if image.isNull() or image.size() != self._image.size():
                continue
            if not isinstance(duration, (int, float)) or not math.isfinite(duration):
                duration = 100
            frames.append((image, max(16, min(60000, duration))))
        return frames

    def set_time_of_day(self, time_of_day: str):
        if time_of_day not in ("day", "night"):
            raise ValueError("Time of day must be 'day' or 'night'.")
        if self._time_of_day != time_of_day:
            self._time_of_day = time_of_day
            self._effects_elapsed_ms = 0.0
        self._sync_timer()
        self.update()

    def set_power(self, enabled: bool):
        if self._power != bool(enabled):
            self._power = bool(enabled)
            self._effects_elapsed_ms = 0.0
        self._sync_timer()
        self.update()

    def _visual(self):
        state = self._visual_states.get(f"{self._time_of_day}_{'on' if self._power else 'off'}")
        return state or {"image": self._image, "frames": self._base_animation if self._power else []}

    def sample_effects(self, elapsed_ms: float):
        """Read an independent visual clock; ``image`` is a loaded QImage.

        Native state sprites and light masks describe the effect. Ambient colour
        is a preview convention, applied to the entire room including the farmer.
        """
        elapsed_ms = float(elapsed_ms)
        if not math.isfinite(elapsed_ms):
            raise ValueError("Effects time must be finite.")
        visual = self._visual()
        frames = visual["frames"]
        index, image = 0, visual["image"]
        if frames:
            phase = max(0.0, elapsed_ms) % sum(duration for _, duration in frames)
            for index, (image, duration) in enumerate(frames):
                if phase < duration:
                    break
                phase -= duration
        lights = [light for light in self._view.get("lights", [])
                  if isinstance(light, dict) and light.get("when", "always") in ("always", self._time_of_day)
                  and (self._power or not light.get("requires_power", True))]
        return {"image": image, "frame": index, "lights": lights,
                "ambient": QColor("#526086" if self._time_of_day == "night" else "#ffffff")}

    def advance_effects(self, milliseconds: float):
        milliseconds = float(milliseconds)
        if not math.isfinite(milliseconds) or milliseconds < 0:
            raise ValueError("Elapsed effects time must be finite and non-negative.")
        self._effects_elapsed_ms += milliseconds
        self.update()
        return self.sample_effects(self._effects_elapsed_ms)

    def set_scale(self, scale: int | None):
        """Use a common integer pixel scale, or fit the same fixed stage."""
        self._scale = None if scale is None else max(1, min(8, int(scale)))
        self.update()

    def set_background(self, mode: str):
        if mode not in ("studio", "room"):
            raise ValueError("Background must be 'studio' or 'room'.")
        self._background_mode = mode
        self.update()

    def set_interaction(self, enabled: bool):
        self._interaction = bool(enabled)
        if not enabled:
            self.stop()
        self.update()

    def _frames(self, direction):
        frames = self._actor.get("frames", {}).get(direction, [])
        return [frame for frame in frames if _image(frame) is not None] if isinstance(frames, (list, tuple)) else []

    def _pose(self, group, direction):
        poses = self._actor.get(group, {})
        value = poses.get(direction) if isinstance(poses, dict) else poses
        if isinstance(value, (list, tuple)):
            value = next((frame for frame in value if _image(frame) is not None), None)
        return _image(value)

    def _has_actor(self):
        return all(self._frames(direction) or self._pose("idle", direction) is not None for direction in _DIRECTIONS)

    def _facing(self):
        explicit = self._view.get("facing")
        if explicit in _DIRECTIONS:
            return explicit
        if isinstance(explicit, int) and explicit in _NATIVE_FACING:
            return _NATIVE_FACING[explicit]
        return _DIRECTIONS[int(self._view.get("rotation", 0)) % 4]

    def _build_route(self):
        self._segments = []
        self._duration_ms = 0.0
        width, height = self._footprint
        start = (-12.0, height + 12.0)
        kind = str(self._side.get("kind", "other")).lower()
        self._action = "cross" if kind == "rug" else "walk"
        facing = self._facing()
        seat = _point(self._view.get("sleep", self._view.get("seat")) if kind in _BEDS else self._view.get("seat"))
        seat_valid = seat is not None and 0 <= seat[0] <= width and 0 <= seat[1] <= height + 4
        # A seat pose without a matching foreground would falsely paint a farmer
        # on top of chair arms or a bed cover. Fall back to the supported action.
        if kind in _SEATS and seat_valid and self._pose("seated", facing) is not None and not self._foreground.isNull():
            self._action = "sit"
        elif kind in _BEDS:
            verified_sleep = (self._pose("sleeping", "south") is not None
                              and self._pose("bed_awake", "south") is not None
                              and _point(self._actor.get("native_position_anchor")) is not None
                              and _point(self._actor.get("native_sleeping_draw_offset")) is not None)
            self._action = "sleep" if seat_valid and verified_sleep and not self._foreground.isNull() else "approach"
        elif kind in _TABLES or kind in _WALLS:
            self._action = "approach"

        def add(a, b, pose="walk", phase="walking", duration=None, direction=None):
            distance = math.dist(a, b)
            duration = distance / self.WALK_SPEED * 1000 if duration is None else duration
            if duration <= 0:
                return
            self._segments.append({"start": a, "end": b, "pose": pose, "phase": phase,
                                   "direction": direction or _direction(a, b),
                                   "begin": self._duration_ms, "duration": duration})
            self._duration_ms += duration

        if self._action == "cross":
            start = (-12.0, height / 2)
            end = (width + 12.0, height / 2)
            add(start, end)
            add(end, start)
        elif self._action == "walk":
            points = [start, (width + 12, height + 12), (width + 12, -12), (-12.0, -12.0), start]
            for a, b in zip(points, points[1:]):
                add(a, b)
        elif self._action == "sleep":
            native_anchor = _point(self._actor["native_position_anchor"])
            offset = _point(self._actor["native_sleeping_draw_offset"])
            foot = _point(self._actor.get("foot_anchor")) or (8.0, 32.0)
            # Walk to the captured resting body's exact top-left, then change
            # pose in place. The native sleeper stands under its blanket; it
            # does not rotate or slide toward the pillow while asleep.
            entry = tuple(seat[i] - native_anchor[i] + offset[i] + foot[i] for i in (0, 1))
            bedside = (-12.0, entry[1])
            add(start, bedside)
            add(bedside, entry, "walk", "entering")
            add(seat, seat, "bed_awake", "settling", 300, "south")
            add(seat, seat, "sleeping", "sleeping", 2200, "south")
            add(seat, seat, "bed_awake", "waking", 300, "south")
            add(entry, bedside, "walk", "exiting")
            add(bedside, start)
        else:
            approach = (seat[0] if self._action == "sit" else width / 2, height + 12)
            add(start, approach)
            if self._action == "sit":
                add(approach, seat, "seated", "settling", 300, facing)
                add(seat, seat, "seated", "seated", 1600, facing)
                add(seat, approach, "seated", "leaving", 300, facing)
            else:
                add(approach, approach, "idle", "approach", 1200, "north")
            add(approach, start)
        self.setToolTip({"walk": "Click to walk around the furniture", "cross": "Click to walk across the rug",
                         "approach": "Click to approach the furniture", "sit": "Click to preview sitting",
                         "sleep": "Click to preview the sleeping pose"}[self._action])

    def sample(self, elapsed_ms: float):
        """Read an absolute route time; the result uses footprint-relative pixels."""
        elapsed_ms = float(elapsed_ms)
        if not math.isfinite(elapsed_ms):
            raise ValueError("Preview time must be finite.")
        elapsed_ms = max(0.0, elapsed_ms)
        if not self._segments:
            return {"position": (-12.0, self._footprint[1] + 12), "direction": "south", "pose": "idle",
                    "phase": "idle", "frame": 0, "finished": True, "occlusion": "front"}
        segment = next((part for part in self._segments if elapsed_ms < part["begin"] + part["duration"]), self._segments[-1])
        fraction = min(1.0, max(0.0, (elapsed_ms - segment["begin"]) / segment["duration"]))
        x, y = (segment["start"][axis] + (segment["end"][axis] - segment["start"][axis]) * fraction for axis in (0, 1))
        finished = elapsed_ms >= self._duration_ms
        pose = "idle" if finished else segment["pose"]
        frame_duration = self._actor.get("walking_frame_duration_ms", 140)
        if not isinstance(frame_duration, (int, float)) or not math.isfinite(frame_duration):
            frame_duration = 140
        frame_duration = max(50, min(1000, frame_duration))
        bed_cover = not finished and self._action == "sleep" and segment["phase"] in ("entering", "settling", "sleeping", "waking", "exiting")
        return {"position": (x, y), "direction": segment["direction"], "pose": pose,
                "phase": "finished" if finished else segment["phase"], "frame": int(elapsed_ms / frame_duration),
                "finished": finished, "occlusion": "seated" if pose == "seated" or bed_cover else
                "front" if self._action == "cross" or y >= self._footprint[1] - 2 else "behind"}

    def _set_state(self, state):
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)

    def play(self):
        if not self._interaction or not self._has_actor() or self._image.isNull():
            return False
        self._elapsed_ms = 0.0
        self._playing = True
        self._set_state(self.sample(0)["phase"])
        self._sync_timer()
        self.update()
        return True

    def stop(self):
        self._playing = False
        self._elapsed_ms = 0.0
        self._set_state("idle")
        self._sync_timer()
        self.update()

    def advance(self, milliseconds: float):
        milliseconds = float(milliseconds)
        if not math.isfinite(milliseconds) or milliseconds < 0:
            raise ValueError("Elapsed preview time must be finite and non-negative.")
        if self._playing:
            self._elapsed_ms = min(self._duration_ms, self._elapsed_ms + milliseconds)
            state = self.sample(self._elapsed_ms)
            self._set_state(state["phase"])
            if state["finished"]:
                self._playing = False
                self._sync_timer()
            self.update()
        return self.sample(self._elapsed_ms)

    def _sync_timer(self):
        active = self.isVisible() and (self._playing or len(self._visual()["frames"]) > 1)
        if active and not self._timer.isActive():
            self._clock.start()
            self._timer.start()
        elif not active:
            self._timer.stop()

    def _tick(self):
        if not self.isVisible():
            self._timer.stop()
            return
        elapsed = min(self._clock.restart(), 100)
        self.advance(elapsed)
        self.advance_effects(elapsed)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timer()

    def closeEvent(self, event):
        self.stop()
        self._timer.stop()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._interaction:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return) and self._interaction:
            self.clicked.emit()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.stop()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _actor_image(self, state):
        direction = state["direction"]
        if state["pose"] == "walk":
            frames = self._frames(direction)
            if frames:
                return frames[state["frame"] % len(frames)]
        pose = self._pose(state["pose"], direction)
        if pose is not None:
            return pose
        frames = self._frames(direction)
        return frames[0] if frames else None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202630"))
        width, height = self.STAGE_SIZE.width(), self.STAGE_SIZE.height()
        scale = self._scale or max(1, int(min(self.width() / width, self.height() / height)))
        scene = self.render_scene(scale)
        painter.drawImage(QPointF(int((self.width() - width * scale) / 2),
                                 int((self.height() - height * scale) / 2)), scene)
        painter.end()

    def render_scene(self, scale: int = 1):
        """Render the current deterministic scene, including room illumination."""
        scale = max(1, min(8, int(scale)))
        width, height = self.STAGE_SIZE.width(), self.STAGE_SIZE.height()
        scene = QImage(width * scale, height * scale, QImage.Format.Format_ARGB32_Premultiplied)
        scene.fill(Qt.GlobalColor.transparent)
        painter = QPainter(scene)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.scale(scale, scale)
        painter.setClipRect(QRectF(0, 0, width, height))
        painter.fillRect(QRectF(0, 0, width, height), QColor("#2b3440"))
        if self._background_mode == "room" and not self._room.isNull():
            painter.drawImage(QPointF((width - self._room.width()) // 2, 0), self._room)
        else:
            painter.fillRect(QRectF(0, 48, width, height - 48), QColor("#303c47"))
            painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
            for y in range(48, height, 16):
                painter.drawLine(0, y, width, y)
        fw, fh = self._footprint
        wall_piece = str(self._side.get("kind", "other")).lower() in _WALLS
        # Wall art ends at the actual wall/floor seam. Its approach route uses
        # the same anchor, keeping feet below that seam instead of circling
        # through the wall behind a fictitious floor-standing object.
        anchor = QPointF(round((width - fw) / 2), 48 - fh if wall_piece else round(110 - fh / 2))
        effects = self.sample_effects(self._effects_elapsed_ms)
        furniture_image = effects["image"]
        image_position = QPointF(anchor.x(), anchor.y() + fh - furniture_image.height())
        painter.setPen(QPen(QColor(199, 214, 221, 95), 1, Qt.PenStyle.DotLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(anchor.x(), anchor.y(), fw, fh))
        if furniture_image.isNull():
            painter.setPen(QColor("#b6c2cc"))
            painter.drawText(QRectF(8, 60, width - 16, 40), Qt.AlignmentFlag.AlignCenter, "Preview unavailable")
            painter.end()
            return scene
        state = self.sample(self._elapsed_ms)
        if not self._playing and self._elapsed_ms == 0:
            state = dict(state, pose="idle", phase="idle")
        actor = self._actor_image(state) if self._interaction and self._has_actor() else None
        foot = _point(self._actor.get("foot_anchor")) or (8.0, 32.0)

        def draw_actor():
            if actor is None:
                return
            x, y = state["position"]
            left, top = x - foot[0], y - foot[1]
            if state["pose"] in ("seated", "sleeping", "bed_awake"):
                native_anchor = _point(self._actor.get("native_position_anchor"))
                if state["pose"] == "seated":
                    offsets = self._actor.get("native_seated_draw_offset", {})
                    offset = _point(offsets.get(state["direction"])) if isinstance(offsets, dict) else None
                else:
                    offset = _point(self._actor.get("native_sleeping_draw_offset"))
                if native_anchor is not None and offset is not None:
                    # Native seat metadata describes farmer position, while the
                    # canvas seat describes feet. Keep the captured native pose
                    # offset instead of treating its image like a standing body.
                    left, top = x - native_anchor[0] + offset[0], y - native_anchor[1] + offset[1]
            position = QPointF(round((anchor.x() + left) * scale) / scale,
                               round((anchor.y() + top) * scale) / scale)
            painter.drawImage(position, actor)

        if state["occlusion"] == "behind":
            draw_actor()
        painter.drawImage(image_position, furniture_image)
        if state["occlusion"] != "behind":
            draw_actor()
        if actor is not None and state["occlusion"] == "seated" and not self._foreground.isNull():
            painter.drawImage(image_position, self._foreground)
        painter.end()
        self._apply_lighting(scene, anchor, scale, effects)
        return scene

    def _apply_lighting(self, scene, anchor, scale, effects):
        # Multiply the finished room, furniture, and actor together. A light
        # restores local colour through its captured alpha mask, so a farmer
        # cannot remain brightly pasted over a dark room or ignore a nearby lamp.
        if effects["ambient"] == QColor("#ffffff") and not effects["lights"]:
            return
        illumination = QImage(scene.size(), QImage.Format.Format_ARGB32_Premultiplied)
        illumination.fill(effects["ambient"])
        light_painter = QPainter(illumination)
        light_painter.scale(scale, scale)
        overlays = []
        for light in effects["lights"]:
            offset, radius = _point(light.get("offset")), light.get("radius")
            intensity = light.get("intensity", 1)
            if (offset is None or not isinstance(radius, (int, float)) or not math.isfinite(radius)
                    or not 0 < radius <= 4096 or not isinstance(intensity, (int, float)) or not math.isfinite(intensity)):
                continue
            mask = self._load_image(light.get("mask"))
            if not light.get("mask") and light.get("blend", "illuminate") == "illuminate":
                # Packs may author a plain radial light. Captured native masks,
                # when supplied, always retain their own exact shape instead.
                if "_radial_light" not in self._image_cache:
                    radial = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
                    radial.fill(Qt.GlobalColor.transparent)
                    gradient = QRadialGradient(QPointF(64, 64), 64)
                    gradient.setColorAt(0, QColor(0, 0, 0, 255))
                    gradient.setColorAt(1, QColor(0, 0, 0, 0))
                    radial_painter = QPainter(radial)
                    radial_painter.fillRect(radial.rect(), gradient)
                    radial_painter.end()
                    self._image_cache["_radial_light"] = radial
                mask = self._image_cache["_radial_light"]
            if mask.isNull():
                continue
            ratio = 2 * radius / max(mask.width(), mask.height())
            mw, mh = mask.width() * ratio, mask.height() * ratio
            target = QRectF(anchor.x() + offset[0] - mw / 2,
                            anchor.y() + offset[1] - mh / 2, mw, mh)
            opacity = max(0.0, min(1.0, intensity))
            if light.get("blend") == "overlay":
                overlays.append((target, mask, opacity))
                continue
            color = QColor(light.get("color", "#ffffff"))
            if not color.isValid():
                continue
            key = (light.get("mask"), color.rgba(), light.get("mask_channel", "alpha"))
            if key not in self._image_cache:
                tinted = mask.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                if light.get("mask_channel") == "luminance":
                    for y in range(mask.height()):
                        for x in range(mask.width()):
                            pixel = mask.pixelColor(x, y)
                            alpha = round((pixel.red() * .2126 + pixel.green() * .7152 + pixel.blue() * .0722) * pixel.alphaF())
                            tinted.setPixelColor(x, y, QColor(255, 255, 255, alpha))
                tint_painter = QPainter(tinted)
                tint_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                tint_painter.fillRect(tinted.rect(), color)
                tint_painter.end()
                self._image_cache[key] = tinted
            light_painter.setOpacity(opacity)
            light_painter.drawImage(target, self._image_cache[key])
        light_painter.end()
        painter = QPainter(scene)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.drawImage(0, 0, illumination)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.scale(scale, scale)
        for target, mask, opacity in overlays:
            painter.setOpacity(opacity)
            painter.drawImage(target, mask)
        painter.end()
