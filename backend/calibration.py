"""Chessboard observations, stable automatic collection and coverage feedback."""
from __future__ import annotations
import threading
import time
import cv2
import numpy as np

class CalibrationError(ValueError):
    pass

class CalibrationSession:
    def __init__(self):
        self.lock=threading.RLock()
        self.revision=0
        self.reset()

    def reset(self):
        with self.lock:
            self.views=[]
            self.qualities=[]
            self.size=None
            self.options=None
            self.previous=None
            self.last_saved_time=None
            self.last_saved_frame=None
            self.revision+=1
            return self.status()

    def _coverage(self):
        occupied=set()
        centers=set()
        for q in self.qualities:
            occupied.update(q['coverage_cells'])
            centers.add(q['center_cell'])
        descriptors=np.array([q['tilt_descriptor'] for q in self.qualities])
        tilt_var=float(np.max(np.ptp(descriptors,axis=0))) if len(descriptors)>1 else 0.
        return {'cells':sorted(occupied),'covered_cells':len(occupied),'total_cells':9,
                'center_cells':sorted(centers),'tilt_variation':tilt_var,
                'has_tilt_diversity':tilt_var>=.07}

    def status(self):
        with self.lock:
            return {'views':len(self.views),'resolution':self.size,'options':self.options,
                    'revision':self.revision,'coverage':self._coverage()}

    def _check_options(self,options,size):
        if self.size and tuple(self.size)!=tuple(size):
            raise CalibrationError('取景分辨率改变，请清空视图并使用统一分辨率重新采集。')
        if self.options and self.options!=options:
            raise CalibrationError('棋盘格规格改变，请先清空已有视图。')

    def observe(self,frame,opts,*,collect=False,manual=False,target=20,revision=None,frame_id=None,now=None):
        now=time.monotonic() if now is None else now
        options={k:getattr(opts,k) for k in ('columns','rows','square_mm')}
        with self.lock:
            started_revision=self.revision
            if revision is not None and revision!=self.revision:
                raise CalibrationError('标定会话已重置，请重新开始采集。')
            self._check_options(options,(frame.shape[1],frame.shape[0]))
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        size=(gray.shape[1],gray.shape[0])
        # Cap detection resolution for a responsive hand-held preview. Accepted
        # points are refined at the original resolution used by calibration.
        factor=min(1.,960/size[0])
        small=cv2.resize(gray,None,fx=factor,fy=factor,interpolation=cv2.INTER_AREA) if factor<1 else gray
        pattern=(opts.columns,opts.rows)
        found,corners=cv2.findChessboardCornersSB(small,pattern,flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
        annotated=frame.copy()
        quality=None
        reason='board_not_found'
        message=f'请将完整的 {opts.columns} × {opts.rows} 内角点棋盘放入画面。'
        accepted=False
        stable=False
        if found:
            corners=(corners/factor).astype(np.float32)
            # SB already gives subpixel positions; refine after restoring scale.
            if factor<1:
                cv2.cornerSubPix(gray,corners,(4,4),(-1,-1),(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,20,.01))
            cv2.drawChessboardCorners(annotated,pattern,corners,True)
            p=corners.reshape(-1,2)
            hull=cv2.convexHull(p)
            area=float(cv2.contourArea(hull)/(size[0]*size[1]))
            lo=np.floor(p.min(axis=0)).astype(int)
            hi=np.ceil(p.max(axis=0)).astype(int)
            roi=gray[max(0,lo[1]):min(size[1],hi[1]+1),max(0,lo[0]):min(size[0],hi[0]+1)]
            sharpness=float(cv2.Laplacian(roi,cv2.CV_64F).var()) if roi.size else 0.
            q=p.reshape(opts.rows,opts.columns,2)
            top,bottom=np.linalg.norm(q[0,-1]-q[0,0]),np.linalg.norm(q[-1,-1]-q[-1,0])
            left,right=np.linalg.norm(q[-1,0]-q[0,0]),np.linalg.norm(q[-1,-1]-q[0,-1])
            descriptor=[float(np.log(max(top,1)/max(bottom,1))),float(np.log(max(left,1)/max(right,1))),
                        float(np.log(max(top+bottom,1)/max(left+right,1)*(opts.rows-1)/(opts.columns-1)))]
            norm=p/np.array(size)
            cells=np.clip((norm*3).astype(int),0,2)
            coverage_cells=sorted({int(y*3+x) for x,y in cells})
            center=p.mean(axis=0)/np.array(size)
            center_cell=int(min(2,int(center[1]*3))*3+min(2,int(center[0]*3)))
            edge=min(float(p[:,0].min()),float(p[:,1].min()),float(size[0]-p[:,0].max()),float(size[1]-p[:,1].max()))
            quality={'board_area_ratio':area,'sharpness':sharpness,'coverage_cells':coverage_cells,
                     'center_cell':center_cell,'tilt_descriptor':descriptor}
            if edge<8:
                reason,message='near_edge','棋盘过于靠近边缘，请让所有内角点完整入镜。'
            elif area<.018:
                reason,message='board_too_small','棋盘成像太小，请靠近相机。'
            elif sharpness<18:
                reason,message='blurred','画面偏模糊，请缓慢移动并短暂停稳。'
            else:
                reason,message='ready','棋盘已检测，保持约半秒以采集当前视角。'
        with self.lock:
            if self.revision!=started_revision:
                raise CalibrationError('标定会话在检测期间已重置，请重新开始。')
            self._check_options(options,size)
            if not found or reason!='ready':
                self.previous=None
            elif reason=='ready':
                if self.previous and self.previous['options']==options and self.previous['size']==size:
                    previous=self.previous
                    a=previous['corners']
                    motion=min(float(np.sqrt(np.mean((corners-a)**2))),float(np.sqrt(np.mean((corners-a[::-1])**2))))
                    if motion<max(3.,np.hypot(*size)*.004):
                        stable=now-previous['since']>=.35
                    else:
                        previous={'since':now,'corners':corners.copy(),'options':options,'size':size}
                else:
                    previous={'since':now,'corners':corners.copy(),'options':options,'size':size}
                self.previous=previous
                duplicate=any(min(float(np.sqrt(np.mean((old-corners)**2))),float(np.sqrt(np.mean((old-corners[::-1])**2))))<max(8.,np.hypot(*size)*.009) for old in self.views)
                if duplicate:
                    reason,message='duplicate','这个视角已经采集过，请移动到画面其他区域，或改变棋盘倾角、距离。'
                elif frame_id is not None and self.last_saved_frame==frame_id:
                    reason,message='same_frame','等待相机的新画面。'
                elif len(self.views)>=min(target,50) and collect:
                    reason,message='target_reached','已达到目标数量，可以计算标定结果。'
                elif len(self.views)>=50:
                    reason,message='limit_reached','已采集 50 个视图，请计算结果或清空重采。'
                elif collect and not manual and not stable:
                    reason,message='hold_still','检测到棋盘，请短暂停稳，程序会自动采集。'
                elif collect and not manual and self.last_saved_time is not None and now-self.last_saved_time<.8:
                    reason,message='cooldown','当前视角清晰，请稍等片刻。'
                elif collect:
                    self.views.append(corners.copy())
                    self.qualities.append(quality)
                    self.size=size
                    self.options=options
                    self.last_saved_time=now
                    self.last_saved_frame=frame_id
                    accepted=True
                    reason,message='accepted','已采集。请改变棋盘位置、距离或倾角，再短暂停稳。'
                elif stable:
                    message='当前视角稳定，可开始自动采集或点击手动采集。'
            status=self.status()
        if manual and not accepted:
            raise CalibrationError(message)
        return {**status,'found':bool(found),'accepted':accepted,'stable':stable,'reason':reason,'message':message,
                'quality':quality,'corners_px':corners.reshape(-1,2).tolist() if found else [],
                'width':size[0],'height':size[1],'target_views':target,
                'target_reached':status['views']>=target},annotated

    def snapshot(self):
        with self.lock:
            return [x.copy() for x in self.views],self.size,dict(self.options) if self.options else None,self.revision,self._coverage()
