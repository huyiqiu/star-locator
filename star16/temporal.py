"""Responsive temporal position filtering for camera frames.

Raw PnP measurements remain untouched. The additional ``pose.filtered`` values
are for live display and consumers that prefer a stable position.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
import math

import numpy as np


POSITION_FIELDS = ('camera_position_marker_mm',)
LENGTH_FIELDS = POSITION_FIELDS + ('distance_mm', 'optical_depth_mm', 'tvec_mm')
WINDOW = 3
MAX_GAP_S = .35
FASTEST_TAU_S = .04
MOTION_SCALE_MM = 50.


def add_centimeter_aliases(pose: dict) -> dict:
    """Expose centimeter values while keeping explicit legacy millimeter keys."""
    for name in LENGTH_FIELDS:
        value = pose.get(name)
        if value is None:
            continue
        pose[name[:-3]+'_cm'] = ([float(v)/10 for v in value]
                                if isinstance(value, (list, tuple)) else float(value)/10)
    return pose


def _point3(value) -> np.ndarray | None:
    try:
        point = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    return point if point.shape == (3,) and np.isfinite(point).all() else None


@dataclass
class _Track:
    samples: deque = field(default_factory=lambda: deque(maxlen=WINDOW))
    value: np.ndarray | None = None
    last_time: float = 0.


class TemporalPoseFilter:
    """Immediate output, three-sample spike rejection, motion-adaptive smoothing."""

    def __init__(self):
        self.tracks: dict[tuple[int, str], _Track] = {}

    def reset(self):
        self.tracks.clear()

    def apply(self, packet: dict, captured_monotonic: float) -> dict:
        smoothing_ms = float(packet.get('config', {}).get('temporal_smoothing_ms', 300))
        if smoothing_ms <= 0:
            self.reset()
        ids = Counter(m['id'] for m in packet.get('markers', []))
        for marker in packet.get('markers', []):
            pose = marker.get('pose')
            status = marker.get('pose_status')
            if not pose or status not in ('valid', 'approximate', 'ambiguous') or ids[marker['id']] != 1:
                continue
            chosen = pose
            if status == 'ambiguous':
                candidates = pose.get('candidates') or [pose]
                history = self.tracks.get((marker['id'], 'camera_position_marker_mm'))
                index, selection = 0, 'best_rms'
                if (history is not None and history.value is not None
                        and 0 < captured_monotonic-history.last_time <= MAX_GAP_S):
                    # The mirrored solutions differ by roughly a metre, far more
                    # than normal frame-to-frame motion; the nearest branch is
                    # the one continuous with the last observed pose.
                    best = None
                    for i, candidate in enumerate(candidates):
                        point = _point3(candidate.get('camera_position_marker_mm'))
                        if point is None:
                            continue
                        distance = float(np.linalg.norm(point-history.value))
                        if best is None or distance < best:
                            best, index, selection = distance, i, 'continuity'
                chosen = candidates[index]
                pose['branch_selection'] = selection
                pose['filtered_candidate_index'] = index
            filtered = {}
            for name in POSITION_FIELDS:
                raw = chosen.get(name)
                if raw is None:
                    continue
                point = _point3(raw)
                if point is None:
                    continue
                key = (marker['id'], name)
                if smoothing_ms <= 0:
                    filtered[name] = point.tolist()
                    continue
                track = self.tracks.get(key)
                if track is None or captured_monotonic-track.last_time > MAX_GAP_S or captured_monotonic <= track.last_time:
                    track = _Track()
                    self.tracks[key] = track
                dt = captured_monotonic-track.last_time if track.samples else 0.
                track.samples.append(point)
                track.last_time = captured_monotonic
                if track.value is None:
                    # The first valid pose is useful immediately; do not create
                    # a blank interval every time a moving marker is reacquired.
                    track.value = point.copy()
                elif len(track.samples) >= WINDOW:
                    median = np.median(np.stack(track.samples), axis=0)
                    innovation = float(np.linalg.norm(median-track.value))
                    tau = max(FASTEST_TAU_S, smoothing_ms/1000./(1+innovation/MOTION_SCALE_MM))
                    alpha = -math.expm1(-dt/tau)
                    track.value = track.value + alpha*(median-track.value)
                # Two samples cannot distinguish motion from a single spike.
                # Keep the first output for one frame, then use the median.
                filtered[name] = track.value.tolist()
            if 'camera_position_marker_mm' in filtered:
                filtered['distance_mm'] = float(np.linalg.norm(filtered['camera_position_marker_mm']))
            pose['filtered'] = add_centimeter_aliases(filtered)
            pose['filter_status'] = 'ready' if 'camera_position_marker_mm' in filtered else 'unavailable'
        # A missed detection never outputs a stale pose, but a brief gap does
        # not force the next valid frame to warm up from scratch.
        self.tracks = {key: track for key, track in self.tracks.items()
                       if captured_monotonic-track.last_time <= MAX_GAP_S}
        return packet
