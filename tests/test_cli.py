import json
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
from star16.models import DetectorConfig
from backend.models import Simulation
from backend.simulation import SIM_INTRINSICS, render_simulation

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    return subprocess.run([sys.executable, '-m', 'star16', *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_cli_reports_pose_with_intrinsics(tmp_path):
    frame, _ = render_simulation(Simulation(marker_id=3), DetectorConfig())
    image = tmp_path / 'frame.jpg'
    intrinsics = tmp_path / 'intrinsics.json'
    cv2.imwrite(str(image), frame)
    intrinsics.write_text(SIM_INTRINSICS.model_dump_json())
    process = run_cli('detect', str(image), '--intrinsics', str(intrinsics), '--json')
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    marker = result['markers'][0]
    assert marker['id'] == 3
    assert marker['pose_status'] == 'valid'
    pose = marker['pose']
    T_camera_marker = np.array(pose['T_camera_marker'])
    assert np.allclose(pose['tvec_mm'], T_camera_marker[:3, 3], atol=1e-6)
    assert np.allclose(np.array(pose['T_camera_marker']) @ np.array(pose['T_marker_camera']),
                       np.eye(4), atol=1e-6)


def test_cli_without_intrinsics_reports_ids_only(tmp_path):
    frame, _ = render_simulation(Simulation(marker_id=3), DetectorConfig())
    image = tmp_path / 'frame.jpg'
    cv2.imwrite(str(image), frame)
    process = run_cli('detect', str(image), '--json')
    assert process.returncode == 0, process.stderr
    marker = json.loads(process.stdout)['markers'][0]
    assert marker['id'] == 3
    assert marker['pose'] is None
    assert marker['pose_status'] == 'calibration_required'


def test_cli_returns_one_when_no_marker(tmp_path):
    image = tmp_path / 'black.jpg'
    cv2.imwrite(str(image), np.zeros((600, 960, 3), np.uint8))
    process = run_cli('detect', str(image))
    assert process.returncode == 1
    assert '0 marker(s)' in process.stdout


def test_cli_returns_two_for_bad_input(tmp_path):
    assert run_cli('detect', str(tmp_path / 'missing.jpg')).returncode == 2
    image = tmp_path / 'frame.jpg'
    cv2.imwrite(str(image), np.zeros((600, 960, 3), np.uint8))
    bad_json = tmp_path / 'intrinsics.json'
    bad_json.write_text('{')
    assert run_cli('detect', str(image), '--intrinsics', str(bad_json)).returncode == 2


def test_cli_version():
    process = run_cli('--version')
    assert process.returncode == 0
    assert process.stdout.strip() == '2.0.0'
