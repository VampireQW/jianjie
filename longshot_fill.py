"""Bounded RGB sampling and padding; original screenshot pixels are never changed."""
from dataclasses import dataclass
import numpy as np


@dataclass
class FillStyle:
    color: tuple
    confidence: float = 1.0
    edge: object = None


def _samples(image, ys, xs):
    h, w = image.shape[:2]
    ys = np.asarray(ys, dtype=np.intp)
    xs = np.asarray(xs, dtype=np.intp)
    pixels = image[ys[:, None], xs].reshape(-1, 3)
    # Local contrast removes antialiasing, thin rules and most text outlines.
    neighbors = (image[ys[:, None], np.minimum(xs + 1, w - 1)].reshape(-1, 3),
                 image[np.minimum(ys + 1, h - 1)[:, None], xs].reshape(-1, 3))
    flat = np.ones(len(pixels), dtype=bool)
    for neighbor in neighbors:
        flat &= np.max(np.abs(pixels.astype(np.int16) - neighbor.astype(np.int16)), axis=1) <= 12
    return pixels, flat


def _dominant(samples):
    pixels, flat = samples
    stable = pixels[flat]
    if not len(stable):
        return tuple(np.median(pixels, axis=0).astype(np.uint8)), 0.0
    bins = stable.astype(np.uint16) // 16
    keys = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    key = np.bincount(keys, minlength=4096).argmax()
    center = np.median(stable[keys == key], axis=0)
    near = np.max(np.abs(stable.astype(np.float32) - center), axis=1) <= 14
    color = tuple(np.median(stable[near], axis=0).astype(np.uint8))
    confidence = float(near.mean()) * min(1.0, len(stable) / max(1, len(pixels)) / .55)
    return color, confidence


def _distance(first, second):
    return float(np.max(np.abs(np.asarray(first, dtype=np.int16) - np.asarray(second, dtype=np.int16))))


def infer_background(image, footer_h=0):
    h, w = image.shape[:2]
    if not h or not w:
        return FillStyle((245, 245, 247))
    # The stitcher already knows fixed footers; do not mistake one for page background.
    body = image[:h-footer_h] if 0 < footer_h < h else image
    h = body.shape[0]
    inset = min(12, max(1, w // 80)) if w > 4 else 0
    xs = np.unique(np.linspace(inset, max(inset, w-1-inset), min(w, 80)).astype(np.intp))
    bottom_y = np.unique(np.linspace(max(0, h-min(80, max(8, h//5))), h-1,
                                     min(h, 24)).astype(np.intp))
    body_y = np.unique(np.linspace(min(h-1, h//10), max(0, h*4//5-1), min(h, 40)).astype(np.intp))
    side_x = np.unique(np.clip(np.asarray([.025, .05, .075, .925, .95, .975]) * (w-1),
                               inset, max(inset, w-1-inset)).astype(np.intp))
    bottom = _dominant(_samples(body, bottom_y, xs))
    middle = _dominant(_samples(body, body_y, xs[::2]))
    sides = _dominant(_samples(body, body_y, side_x))
    # A large stable body with agreeing margins wins over a differently colored toolbar.
    if middle[1] >= .56 and sides[1] >= .60 and _distance(middle[0], sides[0]) <= 16:
        return FillStyle(middle[0], min(middle[1], sides[1]))
    for color, confidence in (bottom, middle, sides):
        if confidence >= .68:
            return FillStyle(color, confidence)
    # Complex pictures: blur only a tiny edge profile, then fade the added area to its median.
    edge_x = np.unique(np.linspace(0, w-1, min(64, w)).astype(np.intp))
    edge_y = np.unique(np.linspace(max(0, h-16), h-1, min(16, h)).astype(np.intp))
    profile = np.median(body[edge_y[:, None], edge_x], axis=0).astype(np.float32)
    for _ in range(4):
        padded = np.pad(profile, ((2, 2), (0, 0)), mode="edge")
        profile = (padded[:-4] + 4*padded[1:-3] + 6*padded[2:-2] + 4*padded[3:-1] + padded[4:]) / 16
    color = tuple(np.median(profile, axis=0).astype(np.uint8))
    return FillStyle(color, max(bottom[1], middle[1], sides[1]), profile)


class ColumnFill:
    def __init__(self):
        self.manual = None
        self.style = None
        self.key = None

    def reset(self):
        self.style, self.key = None, None

    def resolve(self, image, footer_h=0):
        if self.manual is not None:
            return FillStyle(tuple(self.manual))
        key = (id(image), image.shape, footer_h)
        if key != self.key:
            candidate = infer_background(image, footer_h)
            if self.style is not None and self.style.edge is None:
                if (_distance(self.style.color, candidate.color) <= 12 or
                        candidate.confidence < .80):
                    candidate = self.style
            self.style, self.key = candidate, key
        return self.style


def fill_padding(target, style):
    h, w = target.shape[:2]
    if not h or not w:
        return
    target[:] = style.color
    if style.edge is None:
        return
    x = np.linspace(0, len(style.edge)-1, w)
    profile = np.stack([np.interp(x, np.arange(len(style.edge)), style.edge[:, channel])
                        for channel in range(3)], axis=1).astype(np.float32)
    color = np.asarray(style.color, dtype=np.float32)
    fade = min(h, 128)
    # Keep temporary memory bounded even for very wide, very long screenshots.
    for start in range(0, fade, 16):
        stop = min(fade, start+16)
        alpha = ((np.arange(start, stop, dtype=np.float32) + 1) / fade)[:, None, None]
        target[start:stop] = np.clip(profile[None] * (1-alpha) + color * alpha, 0, 255).astype(np.uint8)


def compose_columns(left, right, fills=None):
    lh, lw = left.shape[:2]
    rh, rw = right.shape[:2]
    out = np.empty((max(lh, rh), lw+rw, 3), dtype=np.uint8)
    out[:lh, :lw] = left
    out[:rh, lw:] = right
    if lh < rh:
        fill_padding(out[lh:, :lw], fills[0] if fills else infer_background(left))
    elif rh < lh:
        fill_padding(out[rh:, lw:], fills[1] if fills else infer_background(right))
    return out
