import json
import cv2
import numpy as np
from star16.models import DetectorConfig
from backend.models import Simulation
from backend.simulation import SIM_INTRINSICS, render_simulation


def upload(frame):
    return {'image': ('frame.jpg', cv2.imencode('.jpg', frame)[1].tobytes(), 'image/jpeg')}


def synthetic_frame():
    frame, _ = render_simulation(Simulation(marker_id=3), DetectorConfig())
    return frame


def test_intrinsics_in_request_produce_both_pose_conventions(client):
    response = client.post('/api/v1/detect', files=upload(synthetic_frame()),
                           data={'intrinsics': SIM_INTRINSICS.model_dump_json()})
    assert response.status_code == 200
    packet = response.json()
    assert packet['source'] == 'image'
    assert packet['intrinsics_source'] == 'synthetic_ground_truth'
    assert packet['metric_validated'] is True
    marker = packet['markers'][0]
    assert marker['id'] == 3
    pose = marker['pose']
    T_camera_marker = np.array(pose['T_camera_marker'])
    T_marker_camera = np.array(pose['T_marker_camera'])
    assert np.allclose(T_camera_marker @ T_marker_camera, np.eye(4), atol=1e-6)
    assert np.allclose(pose['tvec_mm'], T_camera_marker[:3, 3], atol=1e-6)
    assert np.allclose(pose['camera_position_marker_mm'], T_marker_camera[:3, 3], atol=1e-6)
    assert np.allclose(pose['tvec_cm'], np.array(pose['tvec_mm']) / 10)
    assert np.isclose(pose['distance_mm'], np.linalg.norm(pose['tvec_mm']))
    assert pose['candidates']
    for candidate in pose['candidates']:
        assert np.allclose(candidate['tvec_cm'], np.array(candidate['tvec_mm']) / 10)
        assert np.allclose(candidate['camera_position_marker_cm'],
                           np.array(candidate['camera_position_marker_mm']) / 10)
        assert np.isclose(candidate['distance_cm'], candidate['distance_mm'] / 10)
        assert np.isclose(candidate['optical_depth_cm'], candidate['optical_depth_mm'] / 10)


def test_missing_intrinsics_ignore_saved_server_state(client):
    frame = synthetic_frame()
    assert client.put('/api/intrinsics', json=SIM_INTRINSICS.model_dump()).status_code == 200
    try:
        assert client.get('/api/config').json()['intrinsics']['calibrated'] is True
        packet = client.post('/api/v1/detect', files=upload(frame)).json()
    finally:
        client.delete('/api/intrinsics')
    marker = packet['markers'][0]
    assert marker['pose'] is None
    assert marker['pose_status'] == 'calibration_required'
    assert 'intrinsics_not_calibrated' in packet['warnings']
    assert packet['intrinsics_source'] is None
    assert packet['metric_validated'] is False
    assert client.get('/api/config').json()['intrinsics'] is None


def test_uncalibrated_intrinsics_do_not_yield_pose(client):
    intr = SIM_INTRINSICS.model_copy(update={'calibrated': False})
    packet = client.post('/api/v1/detect', files=upload(synthetic_frame()),
                         data={'intrinsics': intr.model_dump_json()}).json()
    marker = packet['markers'][0]
    assert marker['pose'] is None
    assert marker['pose_status'] == 'calibration_required'
    assert 'intrinsics_not_calibrated' in packet['warnings']


def test_request_config_overrides_defaults_without_touching_server_config(client):
    server_config = client.get('/api/config').json()['detector']
    filtered = DetectorConfig(min_area=10000, max_area=200000)
    packet = client.post('/api/v1/detect', files=upload(synthetic_frame()),
                         data={'config': filtered.model_dump_json()}).json()
    assert packet['config'] == filtered.model_dump()
    assert packet['markers'] == []
    assert client.get('/api/config').json()['detector'] == server_config


def test_invalid_config_and_intrinsics_payloads_are_422(client):
    frame = synthetic_frame()
    assert client.post('/api/v1/detect', files=upload(frame), data={'config': '{'}).status_code == 422
    assert client.post('/api/v1/detect', files=upload(frame), data={'config': '{"unknown":1}'}).status_code == 422
    assert client.post('/api/v1/detect', files=upload(frame), data={'intrinsics': '{'}).status_code == 422
    assert client.post('/api/v1/detect', files=upload(frame),
                       data={'intrinsics': '{"width":960}'}).status_code == 422
    assert client.post('/api/v1/detect', files=upload(frame), data={'intrinsics': '  '}).status_code == 200


def test_empty_and_oversized_images_are_413(client):
    assert client.post('/api/v1/detect', files={'image': ('empty.jpg', b'', 'image/jpeg')}).status_code == 413
    big = b'x' * (10 * 1024 * 1024 + 1)
    assert client.post('/api/v1/detect', files={'image': ('big.jpg', big, 'image/jpeg')}).status_code == 413


def test_include_images_defaults_off(client):
    frame = synthetic_frame()
    assert 'images' not in client.post('/api/v1/detect', files=upload(frame)).json()
    packet = client.post('/api/v1/detect', files=upload(frame), data={'include_images': 'true'}).json()
    assert set(packet['images']) == {'raw', 'binary', 'glow', 'blobs'}


def test_static_ui_still_served_alongside_v1(client):
    assert client.get('/').status_code == 200
    assert client.get('/assets/app.js').status_code == 200
    assert client.get('/assets/calibration.js').status_code == 200
