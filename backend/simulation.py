"""Independent 3D projection/rasterizer; detector never receives synthetic labels."""
import cv2
import numpy as np
from .family import grid, CODES
from .models import DetectorConfig, Intrinsics, Simulation

SIM_INTRINSICS = Intrinsics(width=960, height=600, fx=1000, fy=1000, cx=480, cy=300,
                            calibrated=True, source='synthetic_ground_truth', distortion=[0., 0., 0., 0., 0.])


def render_simulation(opts: Simulation, cfg: DetectorConfig):
    rng = np.random.default_rng(opts.seed)
    h, w = 600, 960
    img = np.full((h, w), 13., np.float32)
    # Dim ceiling structure and one bright elongated distractor.
    for x in range(0, w, 160):
        cv2.line(img, (x, 0), (x, h), 27., 2)
    for y in range(0, h, 150):
        cv2.line(img, (0, y), (w, y), 27., 2)
    cv2.rectangle(img, (45, 50), (160, 59), 225., -1)
    K = np.array([[1000., 0, 480], [0, 1000., 300], [0, 0, 1.]])
    ax, ay, az = np.radians([opts.tilt_x_deg, opts.tilt_y_deg, opts.yaw_deg])
    Rx = np.array([[1,0,0],[0,np.cos(ax),-np.sin(ax)],[0,np.sin(ax),np.cos(ax)]])
    Ry = np.array([[np.cos(ay),0,np.sin(ay)],[0,1,0],[-np.sin(ay),0,np.cos(ay)]])
    Rz = np.array([[np.cos(az),-np.sin(az),0],[np.sin(az),np.cos(az),0],[0,0,1]])
    R = Rz@Ry@Rx
    rvec = cv2.Rodrigues(R)[0]
    specs = [(opts.marker_id, np.array([0., 0., opts.distance_mm]))]
    if opts.second_marker:
        specs = [(opts.marker_id, np.array([-105., 0., opts.distance_mm])), (opts.marker_id % len(CODES)+1, np.array([115., 30., opts.distance_mm+70]))]
    truths = []
    for marker_id, t in specs:
        a = grid(marker_id)
        for r in range(4):
            for c in range(4):
                if not a[r,c] or opts.missing_cell == r*4+c:
                    continue
                center = np.array([(c-1.5)*cfg.spacing_mm, (r-1.5)*cfg.spacing_mm, 0.])
                angles = np.linspace(0, 2*np.pi, 36, endpoint=False)
                disk = center+np.column_stack((np.cos(angles)*cfg.dot_diameter_mm/2, np.sin(angles)*cfg.dot_diameter_mm/2, np.zeros(len(angles))))
                polygon = cv2.projectPoints(disk, rvec, t, K, np.zeros(5))[0].reshape(-1,2)
                cv2.fillConvexPoly(img, np.round(polygon*4).astype(np.int32), float(rng.uniform(248, 254)), lineType=cv2.LINE_AA, shift=2)
        T = np.eye(4)
        T[:3,:3], T[:3,3] = R, t
        truths.append({'id': marker_id, 'T_camera_marker': T.tolist(), 'camera_position_marker_mm': (-R.T@t).tolist()})
    for _ in range(opts.extra_blobs):
        x, y = int(rng.integers(30, w-30)), int(rng.integers(30, h-30))
        if abs(x-480)<240 and abs(y-300)<170:
            x = 35 + int(rng.integers(0, 140))
        cv2.circle(img, (x, y), int(rng.integers(3, 9)), 245., -1)
    img = cv2.GaussianBlur(img, (3,3), .7)
    img = np.clip(img+rng.normal(0, opts.noise_sigma, img.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), truths
