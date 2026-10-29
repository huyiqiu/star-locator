"""Command-line entry point for the star16 toolkit."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

import cv2
from pydantic import ValidationError

from . import __version__
from .detector import detect
from .models import DetectorConfig, Intrinsics


def _load_json(path, kind):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'无法读取 {kind} 文件 {path}: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{kind} 文件必须是一个 JSON 对象')
    if kind == 'intrinsics' and 'intrinsics' in data:
        return data['intrinsics']
    if kind == 'config':
        return data.get('config', data.get('detector', data))
    return data


def _load_intrinsics(path):
    return None if path is None else Intrinsics.model_validate(_load_json(path, 'intrinsics'))


def _load_config(path):
    return DetectorConfig() if path is None else DetectorConfig.model_validate(_load_json(path, 'config'))


def _print_summary(result):
    markers = result.get('markers', [])
    print(f"{result['family']}  {result['width']}x{result['height']}  "
          f"{len(markers)} marker(s)  {result['timing_ms']:.1f} ms")
    for marker in markers:
        pose = marker.get('pose')
        status = marker['pose_status']
        if pose:
            camera = pose['camera_position_marker_mm']
            marker_in_camera = pose['tvec_mm']
            print(f"ID {marker['id']:03d}  {status:<20} "
                  f"camera_in_marker_mm=({camera[0]:.1f}, {camera[1]:.1f}, {camera[2]:.1f})  "
                  f"marker_in_camera_mm=({marker_in_camera[0]:.1f}, {marker_in_camera[1]:.1f}, {marker_in_camera[2]:.1f})  "
                  f"distance_mm={pose['distance_mm']:.1f}  direction_deg={marker['direction_deg']:.1f}")
        else:
            print(f"ID {marker['id']:03d}  {status:<20} direction_deg={marker['direction_deg']:.1f}")
    for warning in result.get('warnings', []):
        print(f'warning: {warning}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='star16', description='Star16 marker detection and planar pose estimation.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('detect', help='Detect markers in one image.')
    command.add_argument('image')
    command.add_argument('--intrinsics', help='相机内参 JSON（裸对象或 {"intrinsics": ...}）')
    command.add_argument('--config', help='检测参数 JSON（裸对象、{"detector": ...} 或 {"config": ...}）')
    command.add_argument('--json', action='store_true', help='输出完整 JSON 结果')
    args = parser.parse_args(argv)

    try:
        intrinsics = _load_intrinsics(args.intrinsics)
        config = _load_config(args.config)
    except (ValueError, ValidationError) as exc:
        print(f'star16: {exc}', file=sys.stderr)
        return 2

    image = cv2.imread(args.image, cv2.IMREAD_COLOR)
    if image is None:
        print(f'star16: 无法读取图像 {args.image}', file=sys.stderr)
        return 2

    result, _ = detect(image, config, intrinsics)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_summary(result)
    return 0 if result.get('markers') else 1
