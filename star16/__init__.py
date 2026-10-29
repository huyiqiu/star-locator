"""Star16 infrared marker detection and planar pose estimation."""

from .detector import detect
from .family import FAMILY_NAME, manifest, marker_svg
from .models import DetectorConfig, Intrinsics
from .temporal import TemporalPoseFilter, add_centimeter_aliases

__version__ = '2.0.0'

__all__ = [
    'detect', 'DetectorConfig', 'Intrinsics', 'FAMILY_NAME', 'manifest',
    'marker_svg', 'TemporalPoseFilter', 'add_centimeter_aliases', '__version__',
]
