"""Frozen 4x4 dot codebook. IDs 1..9 preserve the original Star16-v1 patterns."""
from __future__ import annotations
from functools import lru_cache
import numpy as np

FAMILY_NAME = 'star16-v2'
ANCHORS = ((0,0),(0,3),(3,3),(3,0))
CELLS = tuple((r,c) for r in range(4) for c in range(4) if (r,c) not in ANCHORS)
CODES = (0x7A8,0xAD1,0x674,0x3CC,0x533,0xFE1,0xAEB,0xCF0,0x6D8,
         0x017,0x1F5,0x0EF,0x17B,0x3D6,0x1E2,0x076,0x2CA,0x00E)
MINIMUM_DISTANCE = 3


def grid(marker_id: int) -> np.ndarray:
    if not 1 <= marker_id <= len(CODES):
        raise ValueError(f'marker_id must be between 1 and {len(CODES)}')
    a=np.zeros((4,4),np.uint8)
    for r,c in ANCHORS:a[r,c]=1
    for i,(r,c) in enumerate(CELLS):a[r,c]=(CODES[marker_id-1]>>i)&1
    return a


def bits(a):
    return sum(int(a[r,c])<<i for i,(r,c) in enumerate(CELLS))


def transforms(a):
    return [np.rot90(a,k) for k in range(4)]+[np.rot90(np.fliplr(a),k) for k in range(4)]


@lru_cache(maxsize=1)
def validate_family():
    entries=[bits(v) for idx in range(1,len(CODES)+1) for v in transforms(grid(idx))]
    return min((a^b).bit_count() for i,a in enumerate(entries) for b in entries[i+1:])


def manifest():
    return {'name':FAMILY_NAME,'grid_size':4,'marker_count':len(CODES),
            'minimum_d4_hamming_distance':MINIMUM_DISTANCE,'max_correctable_bits':1,
            'double_bit_rejection_guaranteed':False,'legacy_compatible_ids':list(range(1,10)),
            'anchors_rc':ANCHORS,'payload_order_rc':CELLS,
            'axis_convention':'origin=center of board; +X=right, +Y=down, +Z=into board; canonical print viewed from front',
            'markers':[{'id':i,'code_hex':f'{CODES[i-1]:03X}','grid':grid(i).tolist(),
                        'dot_count':int(grid(i).sum()),'canonical_direction_deg':0} for i in range(1,len(CODES)+1)]}


def marker_svg(marker_id: int,spacing_mm=40.,diameter_mm=20.,template=False):
    if not(1<=spacing_mm<=500 and .5<=diameter_mm<=spacing_mm*.8):
        raise ValueError('spacing must be 1..500 mm and dot diameter must be <= 0.8 * spacing')
    a=grid(marker_id)
    margin=max(diameter_mm,spacing_mm*.5)
    size=3*spacing_mm+2*margin
    bg,fill,stroke=('#fff','#fff','#111') if template else ('#080b10','#fff','#fff')
    circles=''.join(f'<circle cx="{margin+c*spacing_mm:.4f}" cy="{margin+r*spacing_mm:.4f}" r="{diameter_mm/2:.4f}" fill="{fill}" stroke="{stroke}" stroke-width="0.25"/>' for r in range(4) for c in range(4) if a[r,c])
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}mm" height="{size}mm" viewBox="0 0 {size} {size}"><title>Star16-v2 ID {marker_id:03d}; spacing {spacing_mm} mm; dot diameter {diameter_mm} mm; +X right +Y down</title><rect width="{size}" height="{size}" fill="{bg}"/>{circles}</svg>'
