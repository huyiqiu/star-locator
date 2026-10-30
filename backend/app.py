from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
from io import BytesIO
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
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from star16 import __version__ as CORE_VERSION
from star16.detector import detect
from star16.family import manifest, marker_svg, FAMILY_NAME
from star16.models import DetectorConfig, Intrinsics
from star16.temporal import add_centimeter_aliases
from .camera import CameraManager
from .calibration import CalibrationSession,CalibrationError
from .models import CalibrationOptions, CalibrationObservation, CameraRequest, CameraControlsRequest, Simulation
from .simulation import SIM_INTRINSICS, render_simulation
from .uvc_controls import UvcControls, UvcControlError

# Avoid OpenCV spawning a large worker pool for every concurrent request.
cv2.setNumThreads(2)

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('STAR16_DATA_DIR', str(ROOT/'data')))
DATA.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 10 * 1024 * 1024

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = DetectorConfig()
        self.intrinsics = None
        self.camera_controls = None
        for filename, attr, model in [('config.json','config',DetectorConfig), ('intrinsics.json','intrinsics',Intrinsics),
                                      ('camera_controls.json','camera_controls',CameraControlsRequest)]:
            path = DATA/filename
            if path.exists():
                setattr(self, attr, model.model_validate_json(path.read_text()))

    def snapshot(self):
        with self.lock:
            return self.config.model_copy(deep=True), self.intrinsics.model_copy(deep=True) if self.intrinsics else None

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


def run_detection(frame, cfg, intr, *, include_images, source='image'):
    started=time.perf_counter()
    result, images = detect(frame, cfg, intr)
    for marker in result['markers']:
        if marker.get('pose'):
            add_centimeter_aliases(marker['pose'])
            for candidate in marker['pose'].get('candidates', []):
                add_centimeter_aliases(candidate)
    result.update(timestamp=time.time(), config=cfg.model_dump(), source=source,
                  intrinsics_source=intr.source if intr else None, metric_validated=bool(intr and intr.calibrated))
    encoding_started=time.perf_counter()
    if include_images:
        result['images'] = {name: encode_image(image) for name, image in images.items()}
    finished=time.perf_counter()
    result['timings']={'detection_ms':result['timing_ms'],'image_encoding_ms':(finished-encoding_started)*1000,'total_ms':(finished-started)*1000}
    return result


def process(frame, include_images=True, config=None, intrinsics=None, simulated=False):
    saved_cfg, saved_intr = state.snapshot()
    cfg = config or saved_cfg
    intr = intrinsics if simulated else saved_intr
    return run_detection(frame, cfg, intr, include_images=include_images,
                         source='simulation' if simulated else 'image')

camera = CameraManager(process)
uvc_controls = UvcControls()


def saved_camera_controls():
    with state.lock:
        return state.camera_controls.model_copy(deep=True) if state.camera_controls else None


def apply_saved_camera_controls():
    saved = saved_camera_controls()
    if saved is None:
        return None
    try:
        uvc_controls.apply(saved.mode, saved.exposure_100us, saved.gain)
        return {'restored': True, 'mode': saved.mode,
                'exposure_100us': saved.exposure_100us, 'gain': saved.gain}
    except UvcControlError as exc:
        return {'restored': False, 'error': str(exc)}


@asynccontextmanager
async def lifespan(app):
    yield
    try:
        camera.stop()
    except ValueError:
        pass

app = FastAPI(title='star-locator API', version=CORE_VERSION, lifespan=lifespan,
              description='本机红外反射点阵检测 / ID / 方向 / 平面 PnP。毫米单位；不兼容论文与厂商 ID。')
# Same-origin by default. Optional explicit origins support a separately hosted UI.
origins = [x.strip() for x in os.environ.get('STAR16_CORS_ORIGINS','').split(',') if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET','POST','PUT','DELETE'], allow_headers=['Content-Type'])

@app.get('/api/health')
def health():
    return {'status':'ok', 'version':CORE_VERSION, 'opencv':cv2.__version__, 'family':FAMILY_NAME, 'camera':camera.status()}

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
    cfg, intr = state.snapshot()
    return {'detector': cfg.model_dump(), 'intrinsics': intr.model_dump() if intr else None}

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

def parse_form_model(model, raw, label):
    if raw is None or not raw.strip():
        return None
    try:
        return model.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, f'{label} 必须是合法的 {model.__name__} JSON。') from exc

@app.post('/api/v1/detect')
async def detect_v1(image: UploadFile=File(...), intrinsics: str|None=Form(None),
                    config: str|None=Form(None), include_images: bool=Form(False)):
    """Stateless detection: request-supplied intrinsics/config, never server state."""
    frame = await decode_upload(image)
    intr = parse_form_model(Intrinsics, intrinsics, 'intrinsics')
    cfg = parse_form_model(DetectorConfig, config, 'config') or DetectorConfig()
    return await run_in_threadpool(run_detection, frame, cfg, intr,
                                   include_images=include_images, source='image')

@app.post('/api/simulate')
def simulate(options: Simulation):
    cfg, _ = state.snapshot()
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
        result = uvc_controls.status()
    except UvcControlError as exc:
        raise HTTPException(409, str(exc)) from exc
    saved = saved_camera_controls()
    return {**result, 'saved': saved.model_dump() if saved else None}

@app.put('/api/camera/controls')
def camera_controls_apply(options: CameraControlsRequest):
    try:
        result = uvc_controls.apply(options.mode, options.exposure_100us, options.gain)
    except UvcControlError as exc:
        raise HTTPException(409, str(exc)) from exc
    state.save('camera_controls', options)
    return {**result, 'saved': options.model_dump()}

@app.post('/api/camera/controls/reset')
def camera_controls_reset():
    try:
        result = uvc_controls.reset()
    except UvcControlError as exc:
        raise HTTPException(409, str(exc)) from exc
    with state.lock:
        (DATA/'camera_controls.json').unlink(missing_ok=True)
        state.camera_controls = None
    return {**result, 'saved': None}

@app.post('/api/camera/start')
def camera_start(options: CameraRequest):
    try:
        result = camera.start(options)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    # Opening the UVC device through AVFoundation resets hardware exposure; restore
    # the saved manual values only after the capture process owns the device.
    return {**result, 'controls': apply_saved_camera_controls()}

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
