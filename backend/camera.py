from __future__ import annotations
from collections import deque
import multiprocessing as mp
import sys
import threading
import time

import cv2
import numpy as np

from star16.temporal import TemporalPoseFilter

# Camera handles and the native capture runtime never enter the API process.
# A fresh spawned process also recovers a driver session stuck after reconnect.
MAX_FRAME_BYTES=4096*2160*3
STARTUP_TIMEOUT=18.
FIRST_FRAME_TIMEOUT=3.5


def _valid_frame(ok,frame):
    return bool(ok and frame is not None and frame.size and frame.ndim==3 and frame.shape[2]==3)


def _capture_worker(spec,buffer,frame_lock,sequence,timestamps,updated,stop,control):
    cap=None
    try:
        cv2.setNumThreads(1)
        first=None
        # Some UVC drivers stop producing frames after a frame-rate request.
        # Retry at the same resolution with the device's native rate.
        for native_rate in (False,True):
            if stop.is_set():return
            backend=cv2.CAP_AVFOUNDATION if sys.platform=='darwin' else cv2.CAP_ANY
            cap=cv2.VideoCapture(spec['index'],backend)
            if not cap.isOpened():
                hint=(' macOS 请在“系统设置 → 隐私与安全性 → 相机”中允许启动本服务的应用（Qoder 或终端），然后重启服务。'
                      if sys.platform=='darwin' else '')
                raise ValueError('无法打开相机。请检查设备索引、系统相机权限，以及是否被其他应用占用。'+hint)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,spec['width'])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT,spec['height'])
            if not native_rate:cap.set(cv2.CAP_PROP_FPS,spec['fps'])
            deadline=time.monotonic()+FIRST_FRAME_TIMEOUT
            while not stop.is_set() and time.monotonic()<deadline:
                ok,frame=cap.read()
                if _valid_frame(ok,frame):
                    first=frame
                    break
                stop.wait(.08)
            if first is not None:break
            cap.release();cap=None
        if stop.is_set():return
        if first is None:
            raise ValueError('等待相机首帧超时，已尝试请求帧率和设备原生帧率。请关闭其他应用的相机预览后重试，必要时重新插接 USB。')
        height,width=first.shape[:2]
        if (width,height)!=(spec['width'],spec['height']):
            raise ValueError(f'相机返回 {width}×{height}，与请求的 {spec["width"]}×{spec["height"]} 不同。请选择设备支持的分辨率，以保持标定一致。')
        shared=np.frombuffer(buffer,dtype=np.uint8,count=first.size).reshape(first.shape)
        shape=first.shape
        def publish(frame):
            with frame_lock:
                np.copyto(shared,frame)
                sequence.value+=1
                timestamps[0]=time.monotonic()
                timestamps[1]=time.time()
            updated.set()
        publish(first)
        control.send({'ready':True,'device':{'index':spec['index'],'width':width,'height':height,
                      'fps_requested':spec['fps'],'fps_device':cap.get(cv2.CAP_PROP_FPS),
                      'native_rate_fallback':native_rate,'capture_backend':cap.getBackendName()}})
        failures=0
        while not stop.is_set():
            ok,frame=cap.read()
            if not _valid_frame(ok,frame):
                failures+=1
                if failures>=8:raise ValueError('相机连续读取失败，请重新连接相机。')
                stop.wait(.08)
                continue
            if frame.shape!=shape:raise ValueError('相机输出分辨率已改变，请重新连接并检查标定分辨率。')
            failures=0
            publish(frame)
    except Exception as exc:
        try:control.send({'error':str(exc) if isinstance(exc,ValueError) else f'相机采集失败：{type(exc).__name__}'})
        except (BrokenPipeError,EOFError,OSError):pass
    finally:
        if cap is not None:cap.release()
        control.close()


class CameraManager:
    """Isolate native capture; analyze only the newest shared-memory frame."""
    def __init__(self,processor):
        self.processor=processor
        self.pose_filter=TemporalPoseFilter()
        self.pose_filter_revision=0
        self.lock=threading.RLock()
        self.lifecycle_lock=threading.RLock()
        self.stop_event=threading.Event()
        self.capture_thread=None
        self.process_thread=None
        self.worker=None
        self.worker_stop=None
        self.worker_control=None
        self.worker_buffer=None
        self.latest=None
        self.raw_latest=None
        self.error=None
        self.running=False
        self.spec=None
        self.sequence=0
        self.capture_times=deque(maxlen=120)
        self.process_times=deque(maxlen=60)

    def start(self,spec):
        with self.lifecycle_lock:
            self.stop()
            with self.lock:
                self.error=None
                self.spec=None
            context=mp.get_context('spawn')
            buffer=context.RawArray('B',MAX_FRAME_BYTES)
            frame_lock=context.Lock()
            sequence=context.Value('Q',0,lock=False)
            timestamps=context.Array('d',2,lock=False)
            updated=context.Event()
            stop=context.Event()
            receiver,sender=context.Pipe(duplex=False)
            request={key:getattr(spec,key) for key in ('index','width','height','fps')}
            worker=context.Process(target=_capture_worker,args=(request,buffer,frame_lock,sequence,timestamps,updated,stop,sender),name='star16-camera',daemon=True)
            self.worker,self.worker_stop,self.worker_control,self.worker_buffer=worker,stop,receiver,buffer
            try:
                worker.start()
                sender.close()
                if not receiver.poll(STARTUP_TIMEOUT):
                    raise ValueError('相机启动超时，已释放采集进程；请关闭其他相机应用或重新插接 USB 后再试。')
                response=receiver.recv()
                if not response.get('ready'):raise ValueError(response.get('error','相机采集进程未能启动。'))
                event=threading.Event()
                with self.lock:
                    self.stop_event=event
                    self.spec=response['device']
                    self.running=True
                args=(event,buffer,frame_lock,sequence,timestamps,updated,receiver,worker)
                self.capture_thread=threading.Thread(target=self._capture,args=args,name='star16-frame-reader',daemon=True)
                self.process_thread=threading.Thread(target=self._process,args=(event,),name='star16-detector',daemon=True)
                self.capture_thread.start()
                self.process_thread.start()
                return self.status()
            except Exception as exc:
                self.stop()
                message=str(exc) if isinstance(exc,ValueError) else f'相机启动失败：{type(exc).__name__}'
                with self.lock:self.error=message
                raise ValueError(message) from exc
            finally:
                sender.close()

    def _capture(self,event,buffer,frame_lock,sequence,timestamps,updated,control,worker):
        height,width=self.spec['height'],self.spec['width']
        shared=np.frombuffer(buffer,dtype=np.uint8,count=height*width*3).reshape(height,width,3)
        last_id=0
        try:
            while not event.is_set():
                if control.poll():
                    try:response=control.recv()
                    except EOFError:response={}
                    if response.get('error'):raise ValueError(response['error'])
                if not worker.is_alive():raise ValueError('相机采集进程已退出，请重新连接。')
                updated.wait(.05);updated.clear()
                if not frame_lock.acquire(timeout=.2):continue
                try:
                    frame_id=sequence.value
                    if frame_id==last_id:continue
                    frame=shared.copy()
                    captured_monotonic,captured_at=timestamps[:]
                finally:frame_lock.release()
                last_id=frame_id
                with self.lock:
                    self.capture_times.append(captured_monotonic)
                    self.sequence+=1
                    self.raw_latest=(self.sequence,captured_monotonic,captured_at,frame)
        except Exception as exc:
            if not event.is_set():
                with self.lock:self.error=str(exc) if isinstance(exc,ValueError) else f'相机采集失败：{type(exc).__name__}'
        finally:
            event.set()
            if self.worker_stop:self.worker_stop.set()
            with self.lock:
                self.running=False
                self.latest=self.raw_latest=None
                self.capture_times.clear();self.process_times.clear()

    def _process(self,event):
        last_id=None
        last_filter_revision=-1
        try:
            while not event.is_set():
                with self.lock:item=self.raw_latest
                if item is None or item[0]==last_id:
                    event.wait(.008)
                    continue
                frame_id,captured_monotonic,captured_at,frame=item
                last_id=frame_id
                packet=self.processor(frame)
                if event.is_set():break
                with self.lock:filter_revision=self.pose_filter_revision
                if filter_revision!=last_filter_revision:
                    self.pose_filter.reset()
                    last_filter_revision=filter_revision
                self.pose_filter.apply(packet,captured_monotonic)
                packet.update(source='uvc',frame_id=frame_id,captured_at=captured_at)
                with self.lock:
                    if event.is_set():break
                    self.process_times.append(time.monotonic())
                    packet['rates']=self._rates()
                    self.latest=(captured_monotonic,packet)
        except Exception as exc:
            with self.lock:
                self.error=f'相机处理失败：{type(exc).__name__}'
                self.latest=None
            event.set()
            if self.worker_stop:self.worker_stop.set()

    def stop(self):
        with self.lifecycle_lock:
            self.stop_event.set()
            if self.worker_stop:self.worker_stop.set()
            worker=self.worker
            if worker is not None and worker.pid is not None:
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=1)
            for thread in (self.capture_thread,self.process_thread):
                if thread and thread.is_alive():
                    thread.join(timeout=3)
                    if thread.is_alive():raise ValueError('相机处理线程没有及时停止，请稍后重试。')
            if self.worker_control:self.worker_control.close()
            if worker is not None and worker.pid is not None:worker.close()
            self.capture_thread=self.process_thread=None
            self.worker=self.worker_stop=self.worker_control=self.worker_buffer=None
            with self.lock:
                self.running=False
                self.latest=self.raw_latest=None
                self.capture_times.clear();self.process_times.clear()
            self.pose_filter.reset()
            return self.status()

    def invalidate_pose_filter(self):
        """Reacquire after calibration or detector settings change."""
        with self.lock:self.pose_filter_revision+=1

    def _rates(self):
        def rate(samples):
            return (len(samples)-1)/(samples[-1]-samples[0]) if len(samples)>1 and samples[-1]>samples[0] else None
        return {'capture_fps':rate(self.capture_times),'processing_fps':rate(self.process_times)}

    def status(self):
        with self.lock:
            age=(time.monotonic()-self.latest[0])*1000 if self.latest else None
            return {'running':self.running,'device':self.spec,'error':self.error,'frame_age_ms':age,'rates':self._rates()}

    def raw_sample(self):
        with self.lock:
            if self.raw_latest is None or time.monotonic()-self.raw_latest[1]>1:return None
            frame_id,monotonic_time,captured_at,frame=self.raw_latest
            return frame.copy(),frame_id,captured_at

    def raw_frame(self):
        sample=self.raw_sample()
        return sample[0] if sample else None

    def packet(self):
        with self.lock:
            if not self.latest:return None
            age_ms=(time.monotonic()-self.latest[0])*1000
            if age_ms>1000:return None
            return {**self.latest[1],'frame_age_ms':age_ms}
