from __future__ import annotations
import math
from typing import Literal
from .family import CODES
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class DetectorConfig(StrictModel):
    spacing_mm: float = Field(40, ge=1, le=500)
    dot_diameter_mm: float = Field(20, ge=.5, le=400)
    threshold_mode: Literal['fixed', 'otsu', 'adaptive'] = 'fixed'
    threshold: int = Field(185, ge=0, le=255)
    min_area: float = Field(8, ge=2, le=10000)
    max_area: float = Field(6000, ge=3, le=200000)
    min_circularity: float = Field(.35, ge=.05, le=1)
    blur_size: int = Field(3, ge=1, le=len(CODES))
    max_hamming: int = Field(0, ge=0, le=1)
    grid_tolerance: float = Field(.23, ge=.08, le=.35)
    max_reprojection_error_px: float = Field(2.5, ge=.2, le=20)
    edge_pose_max_reprojection_error_px: float = Field(20, ge=.2, le=100)
    max_blobs: int = Field(96, ge=16, le=160)
    max_markers: int = Field(8, ge=1, le=16)
    temporal_smoothing_ms: int = Field(300, ge=0, le=1500)

    @model_validator(mode='after')
    def check(self):
        if self.max_area <= self.min_area:
            raise ValueError('max_area must exceed min_area')
        if self.blur_size % 2 != 1:
            raise ValueError('blur_size must be odd')
        if self.dot_diameter_mm > .8 * self.spacing_mm:
            raise ValueError('dot diameter must be <= 0.8 * spacing')
        return self

class Intrinsics(StrictModel):
    width: int = Field(..., ge=64, le=8192)
    height: int = Field(..., ge=64, le=8192)
    fx: float = Field(..., gt=0, le=100000)
    fy: float = Field(..., gt=0, le=100000)
    cx: float = Field(..., ge=0, le=8192)
    cy: float = Field(..., ge=0, le=8192)
    distortion: list[float] = Field(default_factory=lambda: [0., 0., 0., 0., 0.], min_length=4, max_length=8)
    calibrated: bool = False
    source: str = Field('manual', max_length=160)
    rms_px: float | None = Field(None, ge=0)

    @model_validator(mode='after')
    def check(self):
        if len(self.distortion) not in (4, 5, 8):
            raise ValueError('distortion length must be 4, 5 or 8 (OpenCV pinhole)')
        if self.cx > self.width or self.cy > self.height:
            raise ValueError('principal point must lie within the calibration resolution')
        return self

class CameraRequest(StrictModel):
    index: int = Field(0, ge=0, le=15)
    width: int = Field(1280, ge=320, le=4096)
    height: int = Field(720, ge=240, le=2160)
    fps: int = Field(30, ge=1, le=120)

class CameraControlsRequest(StrictModel):
    mode: Literal['auto', 'manual']
    exposure_100us: int | None = Field(None, ge=1, le=1_000_000)
    gain: int | None = Field(None, ge=0, le=65535)

class Simulation(StrictModel):
    marker_id: int = Field(1, ge=1, le=len(CODES))
    yaw_deg: float = Field(24, ge=-180, le=180)
    tilt_x_deg: float = Field(13, ge=-65, le=65)
    tilt_y_deg: float = Field(-18, ge=-65, le=65)
    distance_mm: float = Field(650, ge=180, le=2500)
    noise_sigma: float = Field(3, ge=0, le=35)
    extra_blobs: int = Field(3, ge=0, le=20)
    missing_cell: int | None = Field(None, ge=0, le=15)
    second_marker: bool = False
    seed: int = Field(7, ge=0, le=2**31-1)

class MapEntry(StrictModel):
    id: int = Field(..., ge=1, le=len(CODES))
    T_world_marker: list[list[float]]

class MarkerWorldPose(StrictModel):
    id: int = Field(..., ge=1, le=len(CODES))
    x_mm: float | None = None
    y_mm: float | None = None
    z_mm: float | None = None
    x_cm: float | None = None
    y_cm: float | None = None
    z_cm: float | None = None
    yaw_deg: float = Field(0, ge=-360, le=360)
    pitch_deg: float = Field(0, ge=-180, le=180)
    roll_deg: float = Field(0, ge=-180, le=180)

    @model_validator(mode='after')
    def normalize_lengths(self):
        for axis in ('x', 'y', 'z'):
            millimeters = getattr(self, f'{axis}_mm')
            centimeters = getattr(self, f'{axis}_cm')
            if millimeters is None and centimeters is None:
                raise ValueError(f'请提供 {axis}_cm 或 {axis}_mm')
            if centimeters is not None:
                converted = centimeters*10
                if not math.isfinite(converted) or (millimeters is not None and not math.isclose(millimeters, converted, rel_tol=1e-9, abs_tol=1e-9)):
                    raise ValueError(f'{axis}_cm 与 {axis}_mm 不一致或超出范围')
                setattr(self, f'{axis}_mm', converted)
        return self

class MapConfig(StrictModel):
    markers: list[MapEntry] = Field(default_factory=list, max_length=len(CODES))
    T_camera_robot: list[list[float]] | None = None

class CalibrationOptions(StrictModel):
    columns: int = Field(9, ge=3, le=20)
    rows: int = Field(6, ge=3, le=20)
    square_mm: float = Field(25, ge=1, le=200)

class CalibrationObservation(CalibrationOptions):
    collect: bool = False
    target_views: int = Field(20, ge=8, le=50)
    session_revision: int | None = Field(None, ge=0)
