from __future__ import annotations
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
