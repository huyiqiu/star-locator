from itertools import combinations
import numpy as np
import pytest
from star16.family import ANCHORS,CODES,CELLS,bits,grid,transforms,validate_family,marker_svg,manifest


def test_d4_minimum_distance_and_manifest():
    assert validate_family()==3
    assert len(manifest()['markers'])==18
    assert len(CELLS)==12
    for i in range(1,len(CODES)+1):
        assert all(grid(i)[r,c] for r,c in ANCHORS)
        assert len({bits(x) for x in transforms(grid(i))})==8


def test_all_single_bit_errors_have_unique_id_and_rotation():
    allowed=[(ident,k,bits(a)) for ident in range(1,len(CODES)+1) for k,a in enumerate(transforms(grid(ident))[:4])]
    for ident,k,code in allowed:
        for bit in range(12):
            noisy=code^(1<<bit)
            matches=[(i,j) for i,j,c in allowed if (noisy^c).bit_count()<=1]
            assert matches==[(ident,k)]


def test_mirrors_rejected_and_double_error_limit_is_explicit():
    allowed=[bits(a) for ident in range(1,len(CODES)+1) for a in transforms(grid(ident))[:4]]
    for ident in range(1,len(CODES)+1):
        for mirror in transforms(grid(ident))[4:]:
            assert min((bits(mirror)^c).bit_count() for c in allowed)>=3
    assert manifest()['double_bit_rejection_guaranteed'] is False
    assert CODES[:9]==(0x7A8,0xAD1,0x674,0x3CC,0x533,0xFE1,0xAEB,0xCF0,0x6D8)
    assert 'profiles' not in manifest()


def test_real_size_svg():
    svg=marker_svg(1,40,20)
    from xml.etree import ElementTree
    root=ElementTree.fromstring(svg)
    assert float(root.attrib['width'].removesuffix('mm'))==160
    assert svg.count('<circle')==10
    with pytest.raises(ValueError):marker_svg(1,40,39)
    with pytest.raises(ValueError):marker_svg(len(CODES)+1)
