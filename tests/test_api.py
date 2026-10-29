from io import BytesIO
import json
import cv2
import numpy as np
from star16 import __version__
from star16.models import DetectorConfig
from backend.models import Simulation
from backend.simulation import SIM_INTRINSICS,render_simulation


def as_upload(frame):
    return {'file':('frame.jpg',cv2.imencode('.jpg',frame)[1].tobytes(),'image/jpeg')}


def as_detect_upload(frame):
    return {'image':('frame.jpg',cv2.imencode('.jpg',frame)[1].tobytes(),'image/jpeg')}


def test_health_static_and_openapi(client):
    assert client.get('/api/health').json()['version']==__version__=='2.0.0'
    assert '<title>star-locator · 红外星阵定位</title>' in client.get('/').text
    assert client.get('/openapi.json').json()['info']['title']=='star-locator API'
    assert client.get('/assets/app.js').status_code==200
    paths=client.get('/openapi.json').json()['paths']
    assert '/api/v1/detect' in paths
    assert '/api/detect' not in paths
    assert client.get('/api/family').json()['minimum_d4_hamming_distance']==3
    assert client.get('/api/family/1/svg').status_code==200
    assert client.get('/api/family/99/svg').status_code==422


def test_simulation_and_upload_have_different_calibration_sources(client):
    packet=client.post('/api/simulate',json={'marker_id':3}).json()
    assert packet['source']=='simulation'
    assert packet['markers'][0]['id']==3
    assert packet['markers'][0]['pose'] is not None
    frame,_=render_simulation(Simulation(marker_id=3),DetectorConfig())
    r=client.post('/api/v1/detect',files=as_detect_upload(frame))
    assert r.status_code==200
    packet=r.json()
    assert packet['source']=='image'
    assert packet['markers'][0]['pose'] is None
    assert 'images' not in packet
    assert client.get('/api/config').json()['intrinsics'] is None


def test_validation_and_damaged_upload(client):
    assert client.put('/api/config',json={'spacing_mm':10,'dot_diameter_mm':20}).status_code==422
    assert client.put('/api/config',json={'blur_size':4}).status_code==422
    assert client.put('/api/config',json={'max_hamming':2}).status_code==422
    assert client.put('/api/config',json={'temporal_smoothing_ms':1501}).status_code==422
    assert client.put('/api/config',json={'unknown':1}).status_code==422
    assert client.post('/api/v1/detect',files={'image':('bad.jpg',b'broken','image/jpeg')}).status_code==422
    assert client.post('/api/v1/detect',files={'image':('big.jpg',b'x'*(10*1024*1024+1),'image/jpeg')}).status_code==413
    assert client.get('/api/camera/frame').status_code==409
    assert client.post('/api/calibration/solve').status_code==422
    assert client.put('/api/intrinsics',json={'width':960,'height':600,'fx':1000,'fy':1000,'cx':480,'cy':300,'distortion':[0]*6}).status_code==422


def test_camera_exposure_endpoints_validate_and_report_hardware(client,monkeypatch):
    from backend.app import DATA,uvc_controls
    from backend.uvc_controls import UvcControlError
    calls=[]
    status={'device':{'name':'USB 2.0 Camera'},'mode':'manual',
            'exposure_100us':{'current_value':100},'gain':{'current_value':5}}
    monkeypatch.setattr(uvc_controls,'status',lambda:status)
    monkeypatch.setattr(uvc_controls,'apply',lambda mode,exposure,gain:(calls.append((mode,exposure,gain)) or status))
    monkeypatch.setattr(uvc_controls,'reset',lambda:(calls.append('reset') or status))
    initial=client.get('/api/camera/controls').json()
    assert initial['saved'] is None
    assert {key:value for key,value in initial.items() if key!='saved'}==status
    applied=client.put('/api/camera/controls',json={'mode':'manual','exposure_100us':100,'gain':5}).json()
    assert calls[-1]==('manual',100,5)
    assert {key:value for key,value in applied.items() if key!='saved'}==status
    assert applied['saved']=={'mode':'manual','exposure_100us':100,'gain':5}
    assert json.loads((DATA/'camera_controls.json').read_text())==applied['saved']
    assert client.put('/api/camera/controls',json={'mode':'manual','gain':-1}).status_code==422
    reset=client.post('/api/camera/controls/reset').json()
    assert calls[-1]=='reset' and reset['saved'] is None
    assert not (DATA/'camera_controls.json').exists()
    def unavailable():raise UvcControlError('相机没有开放曝光控制。')
    monkeypatch.setattr(uvc_controls,'status',unavailable)
    assert client.get('/api/camera/controls').status_code==409


def test_saved_exposure_is_reapplied_when_camera_starts(client,monkeypatch):
    from backend.app import DATA,camera,uvc_controls
    from backend.uvc_controls import UvcControlError
    status={'device':{'name':'USB 2.0 Camera'},'mode':'manual',
            'exposure_100us':{'current_value':120},'gain':{'current_value':7}}
    monkeypatch.setattr(uvc_controls,'apply',lambda mode,exposure,gain:status)
    assert client.put('/api/camera/controls',json={'mode':'manual','exposure_100us':120,'gain':7}).status_code==200
    applied=[]
    monkeypatch.setattr(uvc_controls,'apply',lambda mode,exposure,gain:(applied.append((mode,exposure,gain)) or status))
    monkeypatch.setattr(camera,'start',lambda options:{'running':True,'device':{'index':0,'width':1280,'height':720}})
    restored=client.post('/api/camera/start',json={})
    assert restored.status_code==200
    assert restored.json()['controls']=={'restored':True,'mode':'manual','exposure_100us':120,'gain':7}
    assert applied==[('manual',120,7)]
    def unavailable(mode,exposure,gain):raise UvcControlError('未找到 USB 相机')
    monkeypatch.setattr(uvc_controls,'apply',unavailable)
    failed=client.post('/api/camera/start',json={}).json()
    assert failed['running'] and failed['controls']=={'restored':False,'error':'未找到 USB 相机'}
    monkeypatch.setattr(uvc_controls,'reset',lambda:status)
    client.post('/api/camera/controls/reset')
    assert not (DATA/'camera_controls.json').exists()


def test_relative_pose_units_and_no_world_map_api(client):
    frame,_=render_simulation(Simulation(),DetectorConfig())
    response=client.post('/api/v1/detect',files=as_detect_upload(frame),
                         data={'include_images':'true','intrinsics':json.dumps(SIM_INTRINSICS.model_dump())})
    assert response.status_code==200
    packet=response.json()
    pose=packet['markers'][0]['pose']
    assert np.allclose(pose['camera_position_marker_cm'],np.array(pose['camera_position_marker_mm'])/10)
    assert np.allclose(pose['tvec_cm'],np.array(pose['tvec_mm'])/10)
    assert np.isclose(pose['distance_cm'],pose['distance_mm']/10)
    assert np.allclose(np.array(pose['T_camera_marker'])@np.array(pose['T_marker_camera']),np.eye(4),atol=1e-6)
    assert not any(key.startswith(('camera_position_world','robot_position_world','T_world','approximate_')) for key in pose)
    assert set(packet['images'])=={'raw','binary','glow','blobs'}
    assert 'map' not in client.get('/api/config').json()
    assert client.put('/api/map',json={'markers':[]}).status_code==404
    assert client.get('/api/map/marker-poses').status_code==404


def test_calibration_view_rejects_non_chessboard(client):
    black=np.zeros((480,640,3),np.uint8)
    response=client.post('/api/calibration/views',files=as_upload(black),data={'columns':9,'rows':6,'square_mm':25})
    assert response.status_code==422
    assert client.get('/api/calibration/status').json()['views']==0
    assert client.delete('/api/calibration/views').json()['views']==0
    svg=client.get('/api/calibration/chessboard.svg?columns=9&rows=6&square_mm=25')
    assert svg.status_code==200
    assert 'width="300.0mm"' in svg.text


def test_camera_websocket_stale_and_origin_checks(client):
    with client.websocket_connect('/ws/camera') as ws:
        packet=ws.receive_json()
        assert packet['stale'] and packet['markers']==[]
    from starlette.websockets import WebSocketDisconnect
    try:
        with client.websocket_connect('/ws/camera',headers={'origin':'https://unrelated.example'}):
            raise AssertionError('Untrusted origin accepted')
    except WebSocketDisconnect as exc:
        assert exc.code==1008


def test_chessboard_calibration_complete_flow(client):
    # Render a known pinhole camera independently from the calibration implementation.
    K=np.array([[900.,0,480.],[0,910.,300.],[0,0,1.]])
    rng=np.random.default_rng(17)
    frames=[]
    for i in range(10):
        frame=np.full((600,960,3),240,np.uint8)
        rvec=np.array([-.35+i*.075,.28*np.sin(i),-.2+i*.045])
        tvec=np.array([float(rng.uniform(-70,70)),float(rng.uniform(-35,35)),650.+i*20])
        for row in range(7):
            for col in range(10):
                obj=np.array([[(col-5)*25,(row-3.5)*25,0],[(col-4)*25,(row-3.5)*25,0],
                              [(col-4)*25,(row-2.5)*25,0],[(col-5)*25,(row-2.5)*25,0]],np.float64)
                p=cv2.projectPoints(obj,rvec,tvec,K,np.zeros(5))[0].reshape(-1,2)
                color=(0,0,0) if (row+col)%2==0 else (255,255,255)
                cv2.fillConvexPoly(frame,np.round(p*16).astype(np.int32),color,cv2.LINE_AA,shift=4)
        frames.append(frame)
        response=client.post('/api/calibration/views',files=as_upload(frame),data={'columns':9,'rows':6,'square_mm':25})
        assert response.status_code==200,response.text
    duplicate=client.post('/api/calibration/views',files=as_upload(frames[0]))
    assert duplicate.status_code==422
    solved=client.post('/api/calibration/solve')
    assert solved.status_code==200,solved.text
    intr=solved.json()['intrinsics']
    assert intr['rms_px']<.8
    assert abs(intr['fx']/900-1)<.08
    assert abs(intr['fy']/910-1)<.08
    # A solve result is reviewable; it does not silently replace the real camera's intrinsics.
    assert client.get('/api/config').json()['intrinsics'] is None
    client.delete('/api/calibration/views')


def test_stream_mode_sends_one_image_without_mutating_shared_frame(client):
    import time
    from backend.app import camera
    images={'raw':'raw-data','binary':'binary-data','glow':'glow-data','blobs':'blobs-data'}
    camera.latest=(time.monotonic(),{'frame_id':123,'markers':[],'images':images})
    try:
        with client.websocket_connect('/ws/camera?image_mode=blobs') as ws:
            frame=ws.receive_json()
            assert frame['images']=={'blobs':'blobs-data'}
        assert set(camera.packet()['images'])==set(images)
    finally:
        camera.latest=None


def test_live_chessboard_observation_reuses_the_connected_camera(client,monkeypatch):
    from backend.app import camera
    from test_calibration import chessboard_frame
    client.delete('/api/calibration/views')
    import time
    monkeypatch.setattr(camera,'raw_sample',lambda:(chessboard_frame(),101,time.time()))
    revision=client.get('/api/calibration/status').json()['revision']
    response=client.post('/api/calibration/observe',json={'columns':9,'rows':6,'square_mm':25,'session_revision':revision})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['found'] and result['views']==0 and len(result['corners_px'])==54
    assert result['preview'].startswith('data:image/jpeg;base64,') and result['frame_id']==101
    manual=client.post('/api/calibration/camera-view',json={'columns':9,'rows':6,'square_mm':25})
    assert manual.status_code==200 and manual.json()['accepted']
    assert manual.json()['views']==1 and 'thumbnail' in manual.json()
    client.delete('/api/calibration/views')
    stale=client.post('/api/calibration/observe',json={'session_revision':revision,'collect':True})
    assert stale.status_code==422


def test_live_chessboard_no_camera_is_clear_and_does_not_add_views(client,monkeypatch):
    from backend.app import camera
    monkeypatch.setattr(camera,'raw_sample',lambda:None)
    response=client.post('/api/calibration/observe',json={})
    assert response.status_code==409
    assert client.get('/api/calibration/status').json()['views']==0
    assert client.get('/assets/calibration.js').status_code==200


def test_new_ids_available_in_family_exports_and_simulator(client):
    family=client.get('/api/family').json()
    assert family['marker_count']==18 and len(family['markers'])==18
    assert 'profiles' not in family
    assert client.get('/api/family/18/svg').status_code==200
    assert client.get('/api/family/19/svg').status_code==422
    packet=client.post('/api/simulate',json={'marker_id':18}).json()
    assert packet['markers'][0]['id']==18
