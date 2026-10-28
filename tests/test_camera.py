import time
import numpy as np
from backend.camera import CameraManager


def test_stale_capture_is_never_returned_as_a_valid_pose():
    camera=CameraManager(lambda image:None)
    camera.latest=(time.monotonic()-2,{'markers':[{'pose_status':'valid'}],'frame_id':1})
    camera.raw_latest=(1,time.monotonic()-2,time.time()-2,np.zeros((10,10,3),np.uint8))
    assert camera.packet() is None
    assert camera.raw_frame() is None
    camera.latest=(time.monotonic(),{'markers':[],'frame_id':2})
    assert camera.packet()['frame_id']==2
    camera.stop()
    assert camera.packet() is None


def test_first_frame_warmup_retries_without_changing_resolution(monkeypatch):
    import threading
    from types import SimpleNamespace
    from backend import camera as module
    frame=np.zeros((12,16,3),np.uint8)
    stop=threading.Event()
    calls=[]
    class Capture:
        def isOpened(self):return True
        def set(self,key,value):calls.append((key,value));return True
        def get(self,key):return 60.
        def getBackendName(self):return 'TEST'
        def release(self):calls.append('release')
        def read(self):
            reads.append(1)
            if len(reads)<3:return False,None
            if len(reads)>3:stop.set()
            return True,frame
    class Control:
        def send(self,data):messages.append(data)
        def close(self):pass
    reads=[];messages=[]
    monkeypatch.setattr(module.cv2,'VideoCapture',lambda *args:Capture())
    sequence=SimpleNamespace(value=0)
    buffer=bytearray(frame.nbytes)
    module._capture_worker({'index':0,'width':16,'height':12,'fps':30},buffer,threading.Lock(),sequence,[0.,0.],threading.Event(),stop,Control())
    assert messages[0]['ready']
    assert messages[0]['device']['width']==16
    assert sequence.value>=1
    assert len(reads)>=3
    assert calls[-1]=='release'


def test_no_frames_reopens_at_native_rate_with_the_same_resolution(monkeypatch):
    import threading
    from types import SimpleNamespace
    from backend import camera as module
    frame=np.zeros((12,16,3),np.uint8)
    stop=threading.Event();captures=[];messages=[]
    class Capture:
        def __init__(self):self.settings=[];self.closed=False;captures.append(self)
        def isOpened(self):return True
        def set(self,key,value):self.settings.append((key,value));return True
        def get(self,key):return 60.
        def getBackendName(self):return 'TEST'
        def release(self):self.closed=True
        def read(self):
            if len(captures)==1:return False,None
            if messages:stop.set()
            return True,frame
    class Control:
        def send(self,data):messages.append(data)
        def close(self):pass
    monkeypatch.setattr(module.cv2,'VideoCapture',lambda *args:Capture())
    monkeypatch.setattr(module,'FIRST_FRAME_TIMEOUT',.01)
    module._capture_worker({'index':0,'width':16,'height':12,'fps':30},bytearray(frame.nbytes),threading.Lock(),SimpleNamespace(value=0),[0.,0.],threading.Event(),stop,Control())
    assert len(captures)==2
    assert all(cap.closed for cap in captures)
    assert messages[0]['device']['native_rate_fallback'] is True
    assert (module.cv2.CAP_PROP_FPS,30) in captures[0].settings
    assert all(key!=module.cv2.CAP_PROP_FPS for key,_ in captures[1].settings)
    for cap in captures:
        assert (module.cv2.CAP_PROP_FRAME_WIDTH,16) in cap.settings
        assert (module.cv2.CAP_PROP_FRAME_HEIGHT,12) in cap.settings


def test_resolution_mismatch_is_reported_and_does_not_publish(monkeypatch):
    import threading
    from types import SimpleNamespace
    from backend import camera as module
    class Capture:
        released=False
        def isOpened(self):return True
        def set(self,*args):return True
        def read(self):return True,np.zeros((24,32,3),np.uint8)
        def release(self):self.released=True
    class Control:
        def send(self,data):messages.append(data)
        def close(self):pass
    capture=Capture();messages=[];sequence=SimpleNamespace(value=0)
    monkeypatch.setattr(module.cv2,'VideoCapture',lambda *args:capture)
    module._capture_worker({'index':0,'width':16,'height':12,'fps':30},bytearray(16*12*3),threading.Lock(),sequence,[0.,0.],threading.Event(),threading.Event(),Control())
    assert '32×24' in messages[0]['error']
    assert sequence.value==0
    assert capture.released


def test_stop_terminates_a_hung_driver_without_stale_frames():
    import threading
    class HungWorker:
        pid=123
        alive=True
        terminated=False
        closed=False
        def join(self,timeout):pass
        def is_alive(self):return self.alive
        def terminate(self):self.terminated=True;self.alive=False
        def close(self):self.closed=True
    camera=CameraManager(lambda image:{})
    worker=HungWorker()
    camera.worker=worker
    camera.worker_stop=threading.Event()
    camera.latest=(time.monotonic(),{'markers':[]})
    camera.capture_times.extend([1.,2.])
    status=camera.stop()
    assert worker.terminated and worker.closed
    assert status['running'] is False
    assert camera.packet() is None
    assert status['rates']=={'capture_fps':None,'processing_fps':None}


def test_worker_exit_clears_frames_rates_and_stops_processing():
    import threading
    from types import SimpleNamespace
    camera=CameraManager(lambda image:{})
    camera.spec={'width':16,'height':12}
    camera.worker_stop=threading.Event()
    camera.latest=(time.monotonic(),{'markers':[{'pose_status':'valid'}]})
    camera.raw_latest=(1,time.monotonic(),time.time(),np.zeros((12,16,3),np.uint8))
    camera.capture_times.extend([1.,2.])
    camera.process_times.extend([1.,2.])
    event=threading.Event()
    camera._capture(event,bytearray(16*12*3),threading.Lock(),SimpleNamespace(value=0),[0.,0.],threading.Event(),SimpleNamespace(poll=lambda:False),SimpleNamespace(is_alive=lambda:False))
    assert event.is_set() and camera.worker_stop.is_set()
    status=camera.status()
    assert not status['running']
    assert status['error']=='相机采集进程已退出，请重新连接。'
    assert camera.packet() is None
    assert camera.raw_sample() is None
    assert status['rates']=={'capture_fps':None,'processing_fps':None}
