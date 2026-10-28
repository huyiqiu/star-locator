from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
from io import BytesIO
import json
import math
import os
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .camera import CameraManager
from .calibration import CalibrationSession,CalibrationError
from .detector import detect
from .family import manifest, marker_svg, FAMILY_NAME
from .models import CalibrationOptions, CalibrationObservation, CameraRequest, CameraControlsRequest, DetectorConfig, Intrinsics, MapConfig, MapEntry, MarkerWorldPose, Simulation
from .simulation import SIM_INTRINSICS, render_simulation
from .temporal import add_centimeter_aliases
from .uvc_controls import UvcControls, UvcControlError

# Avoid OpenCV spawning a large worker pool for every concurrent request.
cv2.setNumThreads(2)

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('STAR16_DATA_DIR', str(ROOT/'data')))
DATA.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 10 * 1024 * 1024
WORLD_POSITION_MAX_RMS_PX = 2.5

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = DetectorConfig()
        self.intrinsics = None
        self.map = MapConfig()
        for filename, attr, model in [('config.json','config',DetectorConfig), ('intrinsics.json','intrinsics',Intrinsics), ('map.json','map',MapConfig)]:
            path = DATA/filename
            if path.exists():
                setattr(self, attr, model.model_validate_json(path.read_text()))

    def snapshot(self):
        with self.lock:
            return self.config.model_copy(deep=True), self.intrinsics.model_copy(deep=True) if self.intrinsics else None, self.map.model_copy(deep=True)

    def save(self, attr, value):
        with self.lock:
            path = DATA/f'{attr}.json'
            tmp = path.with_suffix('.tmp')
            tmp.write_text(value.model_dump_json(indent=2))
            tmp.replace(path)
            setattr(self, attr, value)

state = State()
calibration = CalibrationSession()


def encode_image(img):
    ok, data = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError('JPEG encoding failed')
    return 'data:image/jpeg;base64,'+base64.b64encode(data).decode('ascii')


def add_world_poses(result, marker_map):
    mapping = {m.id: np.array(m.T_world_marker, dtype=float) for m in marker_map.markers}
    for m in result['markers']:
        if m['id'] not in mapping:
            m['world_position_status'] = 'unmapped_marker'
            continue
        if not m['pose'] or m['pose_status'] not in ('valid', 'approximate'):
            m['world_position_status'] = 'relative_pose_invalid'
            continue
        if m['pose_status'] == 'approximate' and m['hamming_distance'] == 0:
            Twc = mapping[m['id']]@np.array(m['pose']['T_marker_camera'])
            m['pose']['approximate_camera_position_world_mm'] = Twc[:3, 3].tolist()
            m['world_position_status'] = 'approximate'
            if marker_map.T_camera_robot is not None:
                Twr = Twc@np.array(marker_map.T_camera_robot)
                m['pose']['approximate_robot_position_world_mm'] = Twr[:3, 3].tolist()
            continue
        if (m['hamming_distance'] != 0 or m['grid_rms_px'] > WORLD_POSITION_MAX_RMS_PX
                or m['pose']['reprojection_rms_px'] > WORLD_POSITION_MAX_RMS_PX):
            m['world_position_status'] = 'geometry_rejected'
            continue
        Twc = mapping[m['id']]@np.array(m['pose']['T_marker_camera'])
        m['pose']['T_world_camera'] = Twc.tolist()
        m['pose']['camera_position_world_mm'] = Twc[:3, 3].tolist()
        m['world_position_status'] = 'valid'
        if marker_map.T_camera_robot is not None:
            Twr = Twc@np.array(marker_map.T_camera_robot)
            m['pose']['T_world_robot'] = Twr.tolist()
            m['pose']['robot_position_world_mm'] = Twr[:3, 3].tolist()
    # No averaging across incompatible boards and no invented map at startup.
    return result


def process(frame, include_images=True, config=None, intrinsics=None, simulated=False):
    started=time.perf_counter()
    cfg, intr, marker_map = state.snapshot()
    cfg = config or cfg
    intr = intrinsics if simulated else intr
    result, images = detect(frame, cfg, intr)
    if not simulated:
        result = add_world_poses(result, marker_map)
    for marker in result['markers']:
        if marker.get('pose'):
            add_centimeter_aliases(marker['pose'])
    result.update(timestamp=time.time(), config=cfg.model_dump(), source='simulation' if simulated else 'image',
                  intrinsics_source=intr.source if intr else None, metric_validated=bool(intr and intr.calibrated))
    encoding_started=time.perf_counter()
    if include_images:
        result['images'] = {name: encode_image(image) for name, image in images.items()}
    finished=time.perf_counter()
    result['timings']={'detection_ms':result['timing_ms'],'image_encoding_ms':(finished-encoding_started)*1000,'total_ms':(finished-started)*1000}
    return result

camera = CameraManager(process)
uvc_controls = UvcControls()

@asynccontextmanager
async def lifespan(app):
    yield
    try:
        camera.stop()
    except ValueError:
        pass

app = FastAPI(title='star-locator API', version='1.6.2', lifespan=lifespan,
              description='本机红外反射点阵检测 / ID / 方向 / 平面 PnP。毫米单位；不兼容论文与厂商 ID。')
# Same-origin by default. Optional explicit origins support a separately hosted UI.
origins = [x.strip() for x in os.environ.get('STAR16_CORS_ORIGINS','').split(',') if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET','POST','PUT','DELETE'], allow_headers=['Content-Type'])

@app.get('/api/health')
def health():
    return {'status':'ok', 'version':'1.6.2', 'opencv':cv2.__version__, 'family':FAMILY_NAME, 'camera':camera.status()}

@app.get('/api/family')
def get_family():
    return manifest()

@app.get('/api/family/{marker_id}/svg')
def get_svg(marker_id: int, spacing_mm: float=40, diameter_mm: float=20, template: bool=False):
    try:
        svg = marker_svg(marker_id, spacing_mm, diameter_mm, template)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(svg, media_type='image/svg+xml', headers={'Content-Disposition':f'attachment; filename="star16-v2-{marker_id:03d}.svg"'})

@app.get('/api/config')
def get_config():
    cfg, intr, marker_map = state.snapshot()
    return {'detector': cfg.model_dump(), 'intrinsics': intr.model_dump() if intr else None, 'map':marker_map.model_dump()}

@app.put('/api/config')
def put_config(config: DetectorConfig):
    state.save('config', config)
    camera.invalidate_pose_filter()
    return {'detector':config.model_dump()}

@app.put('/api/intrinsics')
def put_intrinsics(intrinsics: Intrinsics):
    state.save('intrinsics', intrinsics)
    camera.invalidate_pose_filter()
    return intrinsics

@app.delete('/api/intrinsics')
def delete_intrinsics():
    with state.lock:
        (DATA/'intrinsics.json').unlink(missing_ok=True)
        state.intrinsics = None
    camera.invalidate_pose_filter()
    return {'deleted':True}


def valid_transform(T):
    a = np.asarray(T, dtype=float)
    if a.shape != (4,4) or not np.isfinite(a).all() or not np.allclose(a[3],[0,0,0,1],atol=1e-6):
        raise HTTPException(422, '变换必须是有限的 4×4 齐次矩阵，最后一行为 [0,0,0,1]。')
    R = a[:3,:3]
    if not np.allclose(R.T@R,np.eye(3),atol=1e-4) or not np.isclose(np.linalg.det(R),1,atol=1e-4):
        raise HTTPException(422, '旋转矩阵必须正交且 det(R)=+1；平移单位为毫米。')

@app.put('/api/map')
def put_map(config: MapConfig):
    if len({m.id for m in config.markers}) != len(config.markers):
        raise HTTPException(422,'地图中 ID 不得重复。')
    for m in config.markers:
        valid_transform(m.T_world_marker)
    if config.T_camera_robot is not None:
        valid_transform(config.T_camera_robot)
    state.save('map',config)
    camera.invalidate_pose_filter()
    return config


def marker_pose_matrix(pose: MarkerWorldPose) -> np.ndarray:
    """Marker axes into world axes: Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    yaw, pitch, roll = (math.radians(v) for v in
                        (pose.yaw_deg, pose.pitch_deg, pose.roll_deg))
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    T = np.eye(4)
    T[:3, :3] = [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                 [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                 [-sp, cp*sr, cp*cr]]
    T[:3, 3] = [pose.x_mm, pose.y_mm, pose.z_mm]
    return T


def marker_pose_fields(entry: MapEntry) -> dict:
    T = np.asarray(entry.T_world_marker, dtype=float)
    R = T[:3, :3]
    pitch = math.atan2(-R[2, 0], math.hypot(R[0, 0], R[1, 0]))
    if abs(math.cos(pitch)) > 1e-8:
        yaw = math.atan2(R[1, 0], R[0, 0])
        roll = math.atan2(R[2, 1], R[2, 2])
    else:
        yaw = math.atan2(-R[0, 1], R[1, 1])
        roll = 0.0
    return {'id':entry.id, 'x_mm':float(T[0, 3]), 'y_mm':float(T[1, 3]),
            'z_mm':float(T[2, 3]), 'x_cm':float(T[0, 3]/10),
            'y_cm':float(T[1, 3]/10), 'z_cm':float(T[2, 3]/10), 'yaw_deg':math.degrees(yaw),
            'pitch_deg':math.degrees(pitch), 'roll_deg':math.degrees(roll)}


@app.get('/api/map/marker-poses')
def get_marker_poses():
    with state.lock:
        return [marker_pose_fields(m) for m in state.map.markers]


@app.put('/api/map/marker-pose')
def put_marker_pose(pose: MarkerWorldPose):
    T = marker_pose_matrix(pose)
    with state.lock:
        marker_map = state.map.model_copy(deep=True)
        marker_map.markers = [m for m in marker_map.markers if m.id != pose.id]
        marker_map.markers.append(MapEntry(id=pose.id, T_world_marker=T.tolist()))
        marker_map.markers.sort(key=lambda m: m.id)
        state.save('map', marker_map)
    camera.invalidate_pose_filter()
    return {'pose': marker_pose_fields(next(m for m in marker_map.markers if m.id == pose.id)),
            'map': marker_map}


@app.delete('/api/map/marker-pose/{marker_id}')
def delete_marker_pose(marker_id: int):
    if not 1 <= marker_id <= len(manifest()['markers']):
        raise HTTPException(422, '标记 ID 不在基准族范围内。')
    with state.lock:
        marker_map = state.map.model_copy(deep=True)
        marker_map.markers = [m for m in marker_map.markers if m.id != marker_id]
        state.save('map', marker_map)
    camera.invalidate_pose_filter()
    return marker_map


async def decode_upload(file):
    data = await file.read(MAX_UPLOAD+1)
    await file.close()
    if not data or len(data)>MAX_UPLOAD:
        raise HTTPException(413,'图片必须非空且不超过 10 MB。')
    try:
        with Image.open(BytesIO(data)) as image:
            width,height=image.size
            if image.format not in ('JPEG','PNG','WEBP','BMP') or width*height>16_000_000 or min(width,height)<32:
                raise HTTPException(422,'仅支持 JPEG/PNG/WebP/BMP，图片尺寸必须在 32 像素至 1600 万像素之间。')
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise HTTPException(422,'无法识别图片文件。')
    frame = cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(422,'图片解码失败。')
    return frame

@app.post('/api/detect')
async def detect_upload(file: UploadFile=File(...), include_images: bool=Form(False)):
    frame = await decode_upload(file)
    return await run_in_threadpool(process,frame,include_images)

@app.post('/api/simulate')
def simulate(options: Simulation):
    cfg, _, _ = state.snapshot()
    frame, truths = render_simulation(options, cfg)
    result = process(frame,config=cfg,intrinsics=SIM_INTRINSICS,simulated=True)
    result['ground_truth'] = truths
    return result

@app.get('/api/camera/status')
def camera_status():
    return camera.status()

@app.get('/api/camera/controls')
def camera_controls_status():
    try:
        return uvc_controls.status()
    except UvcControlError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.put('/api/camera/controls')
def camera_controls_apply(options: CameraControlsRequest):
    try:
        return uvc_controls.apply(options.mode, options.exposure_100us, options.gain)
    except UvcControlError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post('/api/camera/controls/reset')
def camera_controls_reset():
    try:
        return uvc_controls.reset()
    except UvcControlError as exc:
        raise HTTPException(409, str(exc)) from exc

@app.post('/api/camera/start')
def camera_start(options: CameraRequest):
    try:
        return camera.start(options)
    except ValueError as exc:
        raise HTTPException(409,str(exc))

@app.post('/api/camera/stop')
def camera_stop():
    try:
        return camera.stop()
    except ValueError as exc:
        raise HTTPException(409,str(exc))

@app.get('/api/camera/frame')
def camera_frame(include_images: bool=False):
    packet = camera.packet()
    if not packet:
        raise HTTPException(409, '没有新鲜相机帧，当前位姿无效。')
    if not include_images:
        packet = {k:v for k,v in packet.items() if k!='images'}
    return packet

@app.websocket('/ws/camera')
async def camera_websocket(ws: WebSocket):
    # Protect a local camera from cross-site WebSocket hijacking.
    origin=ws.headers.get('origin')
    host=ws.headers.get('host')
    allowed={f'http://{host}',f'https://{host}',*origins}
    if origin and origin not in allowed:
        await ws.close(code=1008)
        return
    image_mode=ws.query_params.get('image_mode','all')
    if image_mode not in {'all','raw','binary','glow','blobs'}:
        await ws.close(code=1008)
        return
    await ws.accept()
    last_id = None
    try:
        while True:
            packet = camera.packet()
            if packet and packet['frame_id']!=last_id:
                if image_mode!='all' and 'images' in packet:
                    packet={**packet,'images':{image_mode:packet['images'][image_mode]}}
                await ws.send_json(packet)
                last_id=packet['frame_id']
            elif not packet:
                await ws.send_json({'source':'uvc','markers':[], 'status':camera.status(),'stale':True})
            await asyncio.sleep(.02)
    except (WebSocketDisconnect,RuntimeError):
        pass

@app.get('/api/calibration/status')
def calibration_status():
    return calibration.status()

@app.delete('/api/calibration/views')
def reset_calibration():
    return calibration.reset()


def _observe_calibration(frame,opts,**kwargs):
    try:
        result,preview=calibration.observe(frame,opts,**kwargs)
    except CalibrationError as exc:
        raise HTTPException(422,str(exc))
    result['preview']=encode_image(preview)
    if result['accepted']:
        thumb=cv2.resize(preview,(200,round(preview.shape[0]*200/preview.shape[1])))
        result['thumbnail']=encode_image(thumb)
    return result

@app.post('/api/calibration/views')
async def add_calibration_view(file: UploadFile=File(...),columns: int=Form(9),rows: int=Form(6),square_mm: float=Form(25)):
    try:
        opts=CalibrationOptions(columns=columns,rows=rows,square_mm=square_mm)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    frame=await decode_upload(file)
    return await run_in_threadpool(_add_calibration_view,frame,opts)


def _add_calibration_view(frame,opts):
    return _observe_calibration(frame,opts,collect=True,manual=True,target=50)

@app.post('/api/calibration/observe')
def observe_live_calibration(options: CalibrationObservation):
    sample=camera.raw_sample()
    if sample is None:
        raise HTTPException(409,'请在标定页连接 USB 相机，当前没有新鲜图像。')
    frame,frame_id,captured_at=sample
    result=_observe_calibration(frame,options,collect=options.collect,target=options.target_views,
                                revision=options.session_revision,frame_id=frame_id)
    result.update(frame_id=frame_id,captured_at=captured_at)
    return result

@app.post('/api/calibration/solve')
def solve_calibration():
    views,size,opts,revision,coverage=calibration.snapshot()
    if len(views)<8:
        raise HTTPException(422,'至少需要 8 个不同视角，建议 15–25 张，覆盖图像四角和多个倾斜角度。')
    obj=np.zeros((opts['columns']*opts['rows'],3),np.float32)
    obj[:,:2]=np.mgrid[0:opts['columns'],0:opts['rows']].T.reshape(-1,2)*opts['square_mm']
    rms,K,dist,rvecs,tvecs=cv2.calibrateCamera([obj]*len(views),views,size,None,None)
    if not np.isfinite(K).all() or not np.isfinite(dist).all() or not np.isfinite(rms):
        raise HTTPException(422,'标定没有收敛，请重新采集具有明显倾斜的视图。')
    per_view=[]
    for image_points,r,t in zip(views,rvecs,tvecs):
        projected=cv2.projectPoints(obj,r,t,K,dist)[0]
        per_view.append(float(np.sqrt(np.mean(np.sum((projected-image_points)**2,axis=2)))))
    try:
        intr=Intrinsics(width=size[0],height=size[1],fx=float(K[0,0]),fy=float(K[1,1]),cx=float(K[0,2]),cy=float(K[1,2]),
                        distortion=dist.ravel().tolist(),calibrated=True,source='opencv_chessboard',rms_px=rms)
    except ValueError:
        raise HTTPException(422,'内参不合理，请增加倾斜视图并检查棋盘规格。')
    if calibration.status()['revision']!=revision:
        raise HTTPException(409,'标定期间视图已被清空，请使用当前会话重新计算。')
    warnings=[]
    if rms>1:warnings.append('high_calibration_rms')
    if not coverage['has_tilt_diversity']:warnings.append('insufficient_tilt_diversity')
    if coverage['covered_cells']<6:warnings.append('limited_image_coverage')
    return {'intrinsics':intr.model_dump(),'per_view_rms_px':per_view,'warnings':warnings,
            'coverage':coverage,'views':len(views),'revision':revision}

@app.post('/api/calibration/camera-view')
def calibration_camera_view(options: CalibrationOptions):
    sample=camera.raw_sample()
    if sample is None:
        raise HTTPException(409,'请在标定页连接 USB 相机，或上传照片。')
    frame,frame_id,captured_at=sample
    return _observe_calibration(frame,options,collect=True,manual=True,target=50,frame_id=frame_id)

@app.get('/api/calibration/chessboard.svg')
def chessboard_svg(columns:int=9,rows:int=6,square_mm:float=25):
    try:
        opts=CalibrationOptions(columns=columns,rows=rows,square_mm=square_mm)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    sw,sh=(opts.columns+1)*square_mm,(opts.rows+1)*square_mm
    pad=square_mm
    rects=''.join(f'<rect x="{pad+c*square_mm}" y="{pad+r*square_mm}" width="{square_mm}" height="{square_mm}"/>' for r in range(rows+1) for c in range(columns+1) if (r+c)%2==0)
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{sw+2*pad}mm" height="{sh+2*pad}mm" viewBox="0 0 {sw+2*pad} {sh+2*pad}"><rect width="100%" height="100%" fill="white"/>{rects}</svg>'
    return Response(svg,media_type='image/svg+xml',headers={'Content-Disposition':'attachment; filename="star-locator-chessboard.svg"'})

app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')

@app.get('/',include_in_schema=False)
def index():
    return FileResponse(ROOT/'web'/'index.html')
