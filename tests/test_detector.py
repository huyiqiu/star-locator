import cv2
import numpy as np
import pytest
from backend.detector import detect
from backend.family import grid,CODES
from backend.models import DetectorConfig,Simulation,Intrinsics
from backend.simulation import SIM_INTRINSICS,render_simulation


def test_extreme_edge_undistortion_is_reported():
    frame=np.zeros((720,1280,3),np.uint8)
    for point in [(130,37),(108,289),(124,118),(218,130),(309,140),(391,308)]:
        cv2.circle(frame,point,8,(255,255,255),-1)
    intr=Intrinsics(width=1280,height=720,fx=1347,fy=1327,cx=805,cy=311,
                    distortion=[.228,-5.977,.003,.015,46.6],calibrated=True)
    result,_=detect(frame,DetectorConfig(),intr)
    assert 'undistortion_unstable' in result['warnings']
    assert result['diagnostics']['undistortion_roundtrip_max_px']>2.5


def test_edge_fallback_preserves_exact_id_and_labels_approximate_pose():
    # Realistic edge layout: all ten dots form ID 001; the deliberately
    # troublesome calibration makes the standard inverse distortion diverge.
    frame=np.zeros((720,1280,3),np.uint8)
    centers=[(73,594),(214,588),(466,574),(345,581),(219,466),
             (344,355),(92,350),(453,258),(223,352),(101,239)]
    for point in centers:
        cv2.circle(frame,point,10,(255,255,255),-1)
    intr=Intrinsics(width=1280,height=720,fx=1347,fy=1327,cx=805,cy=311,
                    distortion=[.228,-5.977,.003,.015,46.6],calibrated=True)
    cfg=DetectorConfig(threshold=235,min_area=25,max_area=3000,min_circularity=.5,
                       max_reprojection_error_px=5,edge_pose_max_reprojection_error_px=20)
    result,_=detect(frame,cfg,intr)
    marker=result['markers'][0]
    assert (marker['id'],marker['hamming_distance'],marker['geometry_source'])==(1,0,'raw_image')
    assert marker['pose_status']=='approximate'
    assert 5 < marker['pose']['reprojection_rms_px'] < 20
    strict,_=detect(frame,cfg.model_copy(update={'edge_pose_max_reprojection_error_px':5}),intr)
    assert strict['markers'][0]['id']==1
    assert strict['markers'][0]['pose_status']=='reprojection_rejected'
    assert strict['markers'][0]['pose'] is None
    for missing in centers:
        incomplete=np.zeros_like(frame)
        for point in centers:
            if point != missing:
                cv2.circle(incomplete,point,10,(255,255,255),-1)
        result,_=detect(incomplete,cfg.model_copy(update={'max_hamming':1}),intr)
        assert not result['markers']
    multiple=frame.copy()
    for x,y in centers:
        cv2.circle(multiple,(x+500,y),10,(255,255,255),-1)
    result,_=detect(multiple,cfg,intr)
    assert len(result['markers'])==2
    assert sum(m.get('geometry_source')=='raw_image' for m in result['markers'])==1


def test_exact_marker_with_reprojection_over_normal_limit_is_approximate():
    # A marker decoded with stable undistortion, reconstructed from a viewer
    # screenshot: its pose RMS is just above the 5 px normal gate.
    centers=[(638,410),(536,408),(429,406),(316,404),(434,301),
             (540,205),(437,198),(329,190),(639,117),(333,87)]
    frame=np.zeros((720,1280,3),np.uint8)
    for point in centers:
        cv2.circle(frame,point,9,(255,255,255),-1)
    intr=Intrinsics(width=1280,height=720,fx=1347.287911263969,fy=1326.9093747958955,
                    cx=804.8291471739756,cy=311.30504522071556,
                    distortion=[.2280408567889303,-5.977149149611263,.0032418995028864676,
                                .015374902232756706,46.616611363262685],calibrated=True)
    cfg=DetectorConfig(threshold=235,min_area=25,max_area=3000,min_circularity=.5,
                       max_hamming=1,max_reprojection_error_px=5,edge_pose_max_reprojection_error_px=20)
    result,_=detect(frame,cfg,intr)
    assert 'undistortion_unstable' not in result['warnings']
    marker=result['markers'][0]
    assert (marker['id'],marker['hamming_distance'],marker['pose_status'])==(1,0,'approximate')
    assert 5 < marker['pose']['reprojection_rms_px'] < 20
    strict,_=detect(frame,cfg.model_copy(update={'edge_pose_max_reprojection_error_px':5}),intr)
    assert strict['markers'][0]['pose_status']=='reprojection_rejected'

@pytest.mark.parametrize('ident',range(1,len(CODES)+1))
@pytest.mark.parametrize('yaw',[15,105,-165,-75])
def test_id_direction_and_pose_all_ids_and_orientations(ident,yaw):
    cfg=DetectorConfig()
    frame,truth=render_simulation(Simulation(marker_id=ident,yaw_deg=yaw,seed=ident),cfg)
    result,_=detect(frame,cfg,SIM_INTRINSICS)
    assert len(result['markers'])==1
    m=result['markers'][0]
    assert m['id']==ident
    assert m['hamming_distance']==0
    delta=(m['direction_deg']-yaw+180)%360-180
    assert abs(delta)<1
    assert m['pose'] is not None
    assert m['pose']['reprojection_rms_px']<.5
    assert np.linalg.norm(np.array(m['pose']['camera_position_marker_mm'])-truth[0]['camera_position_marker_mm'])<5
    assert np.allclose(np.array(m['pose']['T_camera_marker'])@np.array(m['pose']['T_marker_camera']),np.eye(4),atol=1e-7)

@pytest.mark.parametrize('tilt',[(-45,30),(40,-30),(55,20),(0,0)])
def test_perspective_and_noise(tilt):
    cfg=DetectorConfig()
    frame,_=render_simulation(Simulation(tilt_x_deg=tilt[0],tilt_y_deg=tilt[1],noise_sigma=8),cfg)
    result,_=detect(frame,cfg,SIM_INTRINSICS)
    assert result['markers'][0]['id']==1
    assert result['markers'][0]['pose']['reprojection_rms_px']<1

@pytest.mark.parametrize('ident',[1,6,9])
def test_two_simultaneous_markers(ident):
    cfg=DetectorConfig()
    frame,_=render_simulation(Simulation(marker_id=ident,second_marker=True),cfg)
    result,_=detect(frame,cfg,SIM_INTRINSICS)
    assert {m['id'] for m in result['markers']}=={ident,ident%len(CODES)+1}


def test_missing_payload_corrected_but_missing_anchor_rejected():
    cfg=DetectorConfig(max_hamming=1)
    payload=next(r*4+c for r in range(4) for c in range(4) if grid(1)[r,c] and (r,c) not in [(0,0),(0,3),(3,0),(3,3)])
    frame,_=render_simulation(Simulation(missing_cell=payload,extra_blobs=0),cfg)
    result,_=detect(frame,cfg,SIM_INTRINSICS)
    assert result['markers'][0]['id']==1
    assert result['markers'][0]['hamming_distance']==1
    cfg.max_hamming=0
    strict,_=detect(frame,cfg,SIM_INTRINSICS)
    assert not strict['markers']
    frame,_=render_simulation(Simulation(missing_cell=0,extra_blobs=0),cfg)
    result,_=detect(frame,DetectorConfig(),SIM_INTRINSICS)
    assert not result['markers']


def test_reflected_image_and_unencoded_random_dots_have_no_id():
    cfg=DetectorConfig()
    frame,_=render_simulation(Simulation(extra_blobs=0),cfg)
    result,_=detect(cv2.flip(frame,1),cfg,SIM_INTRINSICS)
    assert not result['markers']
    img=np.zeros((600,960,3),np.uint8)
    for x,y in [(170,160),(340,130),(580,190),(180,300),(450,380),(600,480),(700,310),(235,470),(525,280),(760,160)]:
        cv2.circle(img,(x,y),9,(255,255,255),-1)
    result,images=detect(img,cfg)
    assert len(result['blobs'])==10
    assert not result['markers']
    assert not images['glow'].any()
    assert images['blobs'].any()


def test_irregular_real_world_id001_respects_configured_geometry_limit():
    # Rounded centers from a hand-placed 90-degree ID 001 board. The code is
    # exact, but its 4 px grid error must be rejected at 2.5 px and accepted
    # when the operator explicitly raises the geometry limit.
    centers=[(583,464),(645,466),(721,464),(788,465),(647,405),
             (587,342),(722,340),(780,280),(589,271),(651,339)]
    frame=np.zeros((720,1280,3),np.uint8)
    for center in centers:
        cv2.circle(frame,center,11,(255,255,255),-1)
    strict=DetectorConfig(threshold=235,min_area=80,max_area=3000,
                          min_circularity=.5,max_reprojection_error_px=2.5)
    rejected,_=detect(frame,strict)
    assert not rejected['markers']
    accepted,_=detect(frame,strict.model_copy(update={'max_reprojection_error_px':6}))
    assert len(accepted['markers'])==1
    assert accepted['markers'][0]['id']==1
    assert accepted['markers'][0]['hamming_distance']==0
    assert 3 < accepted['markers'][0]['grid_rms_px'] < 6


def test_rejected_complete_id001_does_not_become_partial_id018():
    # A second real hand-placed layout: all ten dots form ID 001, but at this
    # geometry limit the complete board fails while seven dots fit ID 018.
    centers=[(509,480),(602,476),(513,406),(681,477),(519,327),
             (433,478),(441,331),(605,328),(444,245),(674,257)]
    frame=np.zeros((720,1280,3),np.uint8)
    for center in centers:
        cv2.circle(frame,center,12,(255,255,255),-1)
    cfg=DetectorConfig(threshold=235,min_area=80,max_area=3000,
                       min_circularity=.5,max_hamming=0,max_reprojection_error_px=4.5)
    result,_=detect(frame,cfg)
    assert result['diagnostics']['blob_count']==10
    assert not result['markers']


def test_requires_real_intrinsics_and_rejects_aspect_mismatch():
    cfg=DetectorConfig()
    frame,_=render_simulation(Simulation(),cfg)
    result,_=detect(frame,cfg)
    assert result['markers'][0]['pose'] is None
    assert result['markers'][0]['pose_status']=='calibration_required'
    fake=SIM_INTRINSICS.model_copy(update={'calibrated':False})
    result,_=detect(frame,cfg,fake)
    assert result['markers'][0]['pose'] is None
    cropped=frame[:500]
    result,_=detect(cropped,cfg,SIM_INTRINSICS)
    assert 'calibration_aspect_mismatch' in result['warnings']
    assert all(m['pose'] is None for m in result['markers'])


def test_uniformly_rescaled_calibration_preserves_metric_pose():
    cfg=DetectorConfig(min_area=2)
    frame,_=render_simulation(Simulation(extra_blobs=0),cfg)
    small=cv2.resize(frame,(768,480),interpolation=cv2.INTER_AREA)
    result,_=detect(small,cfg,SIM_INTRINSICS)
    assert abs(result['markers'][0]['pose']['optical_depth_mm']-650)<5


def test_distortion_is_removed_before_grid_decode():
    from backend.detector import camera_matrix
    cfg=DetectorConfig()
    intr=SIM_INTRINSICS.model_copy(update={'distortion':[-.3,.08,.001,-.002,0.]})
    # Forward splat the independently projected dot centers through the distortion model.
    obj=np.array([[(c-1.5)*40,(r-1.5)*40,0] for r in range(4) for c in range(4) if grid(1)[r,c]],np.float64)
    K=camera_matrix(intr,960,600)
    frame=np.zeros((600,960,3),np.uint8)
    pts=cv2.projectPoints(obj,np.array([.25,-.3,.4]),np.array([110.,30.,650.]),K,np.array(intr.distortion))[0].reshape(-1,2)
    for x,y in pts:cv2.circle(frame,(round(x),round(y)),10,(255,255,255),-1,cv2.LINE_AA)
    result,_=detect(frame,cfg,intr)
    assert result['markers'][0]['id']==1
    assert abs(result['markers'][0]['pose']['optical_depth_mm']-650)<10


@pytest.mark.parametrize('blobs',[
    [],
    [{'x':450.2,'y':299.8,'radius':14.5},{'x':510.4,'y':325.3,'radius':15.2}],
    [{'x':2.,'y':3.,'radius':5.},{'x':957.,'y':597.,'radius':6.}],
    [{'x':470.,'y':300.,'radius':16.},{'x':482.,'y':309.,'radius':17.}],
])
def test_cropped_glow_matches_full_frame_filter(blobs):
    from backend.detector import render_glow
    reference=np.zeros((600,960,3),np.uint8)
    for b in blobs:
        cv2.circle(reference,(round(b['x']),round(b['y'])),max(2,round(b['radius'])),(230,255,210),-1,cv2.LINE_AA)
    reference=cv2.addWeighted(reference,1.,cv2.GaussianBlur(reference,(0,0),6),1.8,0)
    actual=render_glow(600,960,blobs)
    # SIMD alignment can change separable 8-bit rounding by one intensity level.
    assert np.max(np.abs(actual.astype(np.int16)-reference.astype(np.int16)))<=1
