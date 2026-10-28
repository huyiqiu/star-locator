import cv2
import numpy as np
import pytest
from backend.calibration import CalibrationError,CalibrationSession
from backend.models import CalibrationOptions


def chessboard_frame(index=0,width=960,height=600):
    K=np.array([[900.,0,480.],[0,910.,300.],[0,0,1.]])
    image=np.full((600,960,3),240,np.uint8)
    rvec=np.array([-.35+index*.075,.28*np.sin(index),-.2+index*.045])
    tvec=np.array([-65.+index*14,25.*np.sin(index),650.+index*20])
    for row in range(7):
        for col in range(10):
            points=np.array([[(col-5)*25,(row-3.5)*25,0],[(col-4)*25,(row-3.5)*25,0],
                             [(col-4)*25,(row-2.5)*25,0],[(col-5)*25,(row-2.5)*25,0]],np.float64)
            projected=cv2.projectPoints(points,rvec,tvec,K,np.zeros(5))[0].reshape(-1,2)
            color=(0,0,0) if (row+col)%2==0 else (255,255,255)
            cv2.fillConvexPoly(image,np.round(projected*16).astype(np.int32),color,cv2.LINE_AA,shift=4)
    return cv2.resize(image,(width,height))


def test_auto_collection_requires_a_stable_new_view():
    session=CalibrationSession();opts=CalibrationOptions()
    image=chessboard_frame()
    r,_=session.observe(image,opts,collect=True,now=0,frame_id=1)
    assert r['found'] and not r['accepted'] and r['reason']=='hold_still'
    r,_=session.observe(image,opts,collect=True,now=.4,frame_id=2)
    assert r['accepted'] and r['views']==1 and r['stable']
    r,_=session.observe(image,opts,collect=True,now=1.5,frame_id=3)
    assert not r['accepted'] and r['reason']=='duplicate' and r['views']==1
    moved=chessboard_frame(3)
    r,_=session.observe(moved,opts,collect=True,now=2,frame_id=4)
    assert not r['accepted']
    r,_=session.observe(moved,opts,collect=True,now=2.4,frame_id=5)
    assert r['accepted'] and r['views']==2
    assert r['coverage']['covered_cells']>=1


def test_auto_collection_stops_at_target_and_preview_never_adds_views():
    session=CalibrationSession();opts=CalibrationOptions()
    for i in range(8):
        r,_=session.observe(chessboard_frame(i),opts,collect=True,manual=True,target=50,now=i,frame_id=i)
        assert r['accepted']
    r,_=session.observe(chessboard_frame(9),opts,collect=True,target=8,now=12,frame_id=9)
    assert not r['accepted'] and r['target_reached'] and r['views']==8
    assert r['reason']=='target_reached'
    r,_=session.observe(chessboard_frame(9),opts,collect=False,target=20,now=13,frame_id=10)
    assert r['views']==8 and not r['accepted']


def test_changed_board_or_resolution_requires_reset():
    session=CalibrationSession();opts=CalibrationOptions()
    session.observe(chessboard_frame(),opts,collect=True,manual=True,target=50)
    with pytest.raises(CalibrationError):
        session.observe(chessboard_frame(),CalibrationOptions(square_mm=20))
    with pytest.raises(CalibrationError):
        session.observe(chessboard_frame(width=1280,height=800),opts)
    old=session.status()['revision'];reset=session.reset()
    assert reset['revision']>old and reset['views']==0
    with pytest.raises(CalibrationError):
        session.observe(chessboard_frame(),opts,revision=old)


def test_reset_during_detection_discards_the_inflight_frame(monkeypatch):
    session=CalibrationSession();original=cv2.findChessboardCornersSB
    def detect_and_reset(*args,**kwargs):
        result=original(*args,**kwargs)
        session.reset()
        return result
    monkeypatch.setattr(cv2,'findChessboardCornersSB',detect_and_reset)
    with pytest.raises(CalibrationError):
        session.observe(chessboard_frame(),CalibrationOptions(),collect=True,manual=True)
    assert session.status()['views']==0


def test_non_chessboard_feedback_and_high_resolution_corners():
    session=CalibrationSession();opts=CalibrationOptions()
    r,_=session.observe(np.zeros((600,960,3),np.uint8),opts,collect=True)
    assert not r['found'] and not r['accepted'] and r['reason']=='board_not_found'
    r,_=session.observe(chessboard_frame(width=1280,height=800),opts,collect=True,manual=True,target=50)
    assert r['found'] and r['accepted'] and len(r['corners_px'])==54
    assert r['width']==1280 and r['height']==800
