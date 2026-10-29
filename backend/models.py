from __future__ import annotations
from typing import Literal
from pydantic import Field
from star16.family import CODES
from star16.models import StrictModel

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

class CalibrationOptions(StrictModel):
    columns: int = Field(9, ge=3, le=20)
    rows: int = Field(6, ge=3, le=20)
    square_mm: float = Field(25, ge=1, le=200)

class CalibrationObservation(CalibrationOptions):
    collect: bool = False
    target_views: int = Field(20, ge=8, le=50)
    session_revision: int | None = Field(None, ge=0)
