# `POST /api/v1/detect` 接口契约

版本 2.0.0。该接口由 `backend/app.py` 提供，内部调用纯算法库 `star16.detect`。

## 无状态语义

- 只使用本次请求携带的 `intrinsics` 与 `config`。
- 不读取、也不写入服务端保存的 `data/config.json`、`data/intrinsics.json`。
- 请求中的参数不会影响 `PUT /api/config`、`PUT /api/intrinsics` 或后端 UVC 实时管线。
- 省略参数时使用库默认值，而不是现场保存值；需要固定现场参数时请显式传入。

后端 UVC 实时管线（`/ws/camera`、`/api/camera/frame`）仍使用服务端保存的参数与内参；浏览器相机与上传检测使用本接口，由前端携带当前页面参数。两条路径互不影响。

## 请求

`multipart/form-data`：

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `image` | 是 | 文件 | JPEG/PNG/WebP/BMP，非空，≤10 MB，最小边 ≥32 px，≤1600 万像素 |
| `intrinsics` | 否 | JSON 字符串 | `Intrinsics` 对象；省略时只解码 ID/方向，不计算位姿 |
| `config` | 否 | JSON 字符串 | `DetectorConfig` 对象；省略时使用库默认值 |
| `include_images` | 否 | 布尔 | 默认 `false`；`true` 返回四张 JPEG data URL |

`intrinsics` 示例：

```json
{
  "width": 1280,
  "height": 720,
  "fx": 1000,
  "fy": 1000,
  "cx": 640,
  "cy": 360,
  "distortion": [0, 0, 0, 0, 0],
  "calibrated": true,
  "source": "opencv_chessboard",
  "rms_px": 0.42
}
```

- `distortion` 支持 4、5 或 8 项；`calibrated` 必须为 `true` 才会输出位姿。
- 内参分辨率与图像宽高比相差超过 1% 时暂停位姿，返回 `warnings` 中的 `calibration_aspect_mismatch`。

`config` 示例（全部字段）：

```json
{
  "spacing_mm": 40,
  "dot_diameter_mm": 20,
  "threshold_mode": "fixed",
  "threshold": 185,
  "min_area": 8,
  "max_area": 6000,
  "min_circularity": 0.35,
  "blur_size": 3,
  "max_hamming": 0,
  "grid_tolerance": 0.23,
  "max_reprojection_error_px": 2.5,
  "edge_pose_max_reprojection_error_px": 20,
  "max_blobs": 96,
  "max_markers": 8,
  "temporal_smoothing_ms": 300
}
```

未知字段会被拒绝（422）。`temporal_smoothing_ms` 只为保持配置结构一致；本接口不做时间平滑。

### 省略行为

| 输入 | 结果 |
| --- | --- |
| 无 `intrinsics` | 200，`pose=null`，`pose_status=calibration_required`，`warnings` 含 `intrinsics_not_calibrated` |
| `intrinsics.calibrated=false` | 同上 |
| 内参宽高比与图像不符 | 200，`pose=null`，`warnings` 含 `calibration_aspect_mismatch` |
| 无 `config` | 使用 `DetectorConfig()` 默认值（间距 40 mm、直径 20 mm、固定阈值 185） |
| 无标记或位姿被拒绝 | 200，`markers` 为空或 `pose=null` |

## 响应

```json
{
  "family": "star16-v2",
  "width": 1280,
  "height": 720,
  "markers": [
    {
      "id": 7,
      "direction_deg": 0.0,
      "pose_status": "valid",
      "quality_score": 0.88,
      "pose": {
        "T_camera_marker": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 650], [0, 0, 0, 1]],
        "T_marker_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, -650], [0, 0, 0, 1]],
        "tvec_mm": [0.0, 0.0, 650.0],
        "tvec_cm": [0.0, 0.0, 65.0],
        "camera_position_marker_mm": [0.0, 0.0, -650.0],
        "camera_position_marker_cm": [0.0, 0.0, -65.0],
        "distance_mm": 650.0,
        "distance_cm": 65.0,
        "optical_depth_mm": 650.0,
        "reprojection_rms_px": 0.31,
        "ambiguous": false,
        "candidates": [{"...": "精化候选，每项字段同 pose；candidates[0] 即顶层解"}]
      }
    }
  ],
  "blobs": [],
  "timing_ms": 18.4,
  "threshold_used": 185.0,
  "warnings": [],
  "diagnostics": {
    "blob_count": 16,
    "candidate_quads": 24,
    "rejected_code_hypotheses": 0,
    "matched_dots": 16,
    "undistortion_roundtrip_max_px": 0.0,
    "raw_grid_fallback_count": 0
  },
  "image_statistics": {"min": 13, "max": 254, "mean": 19.2, "p95": 27.0, "p99": 251.0},
  "timestamp": 1790000000.0,
  "config": {"spacing_mm": 40, "dot_diameter_mm": 20, "...": "实际生效的完整配置"},
  "source": "image",
  "intrinsics_source": "opencv_chessboard",
  "metric_validated": true,
  "timings": {"detection_ms": 18.4, "image_encoding_ms": 0.0, "total_ms": 18.5}
}
```

`include_images=true` 时额外返回：

```json
{"images": {"raw": "data:image/jpeg;base64,...", "binary": "...", "glow": "...", "blobs": "..."}}
```

`source` 固定为 `image`；`intrinsics_source` 为请求内参的 `source`（无内参时为 `null`）；`metric_validated` 表示本次请求提供了已标定内参。帧级位姿可用性仍以每个 marker 的 `pose_status` 为准。

### 位姿字段与坐标系

使用列向量，`p_camera = R·p_marker + t`。矩阵和 `_mm` 字段一律为毫米，`_cm` 是界面别名。

| 语义 | 字段 | 等式 |
| --- | --- | --- |
| marker-in-camera：标记原点在相机系 | `pose.tvec_mm` / `tvec_cm` | `T_camera_marker[:3,3]` |
| camera-in-marker：相机光心在标记系 | `pose.camera_position_marker_mm` / `_cm` | `T_marker_camera[:3,3] = -Rᵀt` |
| marker → camera 变换 | `pose.T_camera_marker` | `p_cam = R·p_marker + t` |
| camera → marker 变换 | `pose.T_marker_camera` | 上式逆矩阵 |
| 光学深度 | `pose.optical_depth_mm` | `tvec_mm[2]` |
| 直线距离 | `pose.distance_mm` / `distance_cm` | `‖tvec_mm‖` |

`pose` 还包含 `rvec`、`ippe_candidate_rms_px`、`candidate_angle_separation_deg`（原始 IPPE 候选诊断）、`refined_candidate_rms_px`、`refined_candidate_angle_separation_deg`（LM 精化后的候选，`ambiguous` 以此为判据）和 `point_count`。`pose=null` 时上述字段不存在。

`pose.candidates` 是 LM 精化后的候选解（按重投影 RMS 升序，1–2 项），每项都带完整的 `T_camera_marker`、`T_marker_camera`、`rvec`、`tvec_mm`/`tvec_cm`、`camera_position_marker_mm`/`_cm`、`distance_mm`/`_cm`、`optical_depth_mm`/`_cm`、`reprojection_rms_px`。顶层 `pose` 等于 `candidates[0]`（最小误差解），其位姿字段与 `candidates[0]` 相同。候选数量为 2 不等于歧义，应以 `pose_status` 为准。

单帧 API（`source=image`）不做分支选择，始终返回最小误差解。实时链路（UVC 与浏览器相机）会在 marker 的 `pose` 上追加：

| 字段 | 含义 |
| --- | --- |
| `branch_selection` | `best_rms`：无可用历史，采用 `candidates[0]`；`continuity`：按上一帧 `camera_position_marker_mm` 选取最近的候选分支 |
| `filtered_candidate_index` | 被选中的候选下标 |
| `filtered` / `filter_status` | 对所选分支做时序滤波后的 `camera_position_marker_mm`/`_cm`、`distance_mm`/`_cm` 等；`ambiguous` 时可能与顶层 `pose` 属于不同分支 |

平面双解的两个候选朝向不同、相机横向位置相差可达约 1 m，但距离只差毫米级。实时显示请优先使用 `filtered`，并同时读取 `pose_status`，不要把 `ambiguous` 当作 `valid`。

### `pose_status`

| 值 | 含义 |
| --- | --- |
| `valid` | 通过几何门限的有效位姿 |
| `approximate` | 完整精确解码后的宽松位姿，或原图精确解码的边缘回退位姿 |
| `ambiguous` | 两个 IPPE 候选分别精化后仍近似等价，姿态存在关于视线的镜像双解；仍返回 `pose` 与 `candidates`，实时链路的 `filtered` 按连续性选择分支 |
| `calibration_required` | 无已标定内参，或内参宽高比不一致 |
| `reprojection_rejected` | ID 可解码，但位姿未通过重投影门限 |

### `warnings`

| 值 | 含义 |
| --- | --- |
| `blob_limit_reached` | 候选亮点超过 `max_blobs`，只保留较大的点 |
| `intrinsics_not_calibrated` | 未提供内参，或 `calibrated=false` |
| `calibration_aspect_mismatch` | 内参宽高比与图像不一致，暂停位姿 |
| `undistortion_unstable` | 畸变反算在部分点不稳定，边缘识别可能失败 |

### marker 与 blob 字段

- marker：`id`、`family`、`code_hex`、`direction_deg`、`quarter_turn`、`hamming_distance`、`corrected`、`quality_score`、`grid_rms_px`、`observed_grid`、`corners_px`、`center_px`、`dot_indices`、`image_points_px`、`cells_rc`、`pose`、`pose_status`；边缘回退项可能带 `geometry_source: "raw_image"`。
- blob：`x`、`y`、`area`、`radius`、`circularity`、`mean_intensity`。

`direction_deg` 是标记 +X 在图像平面内相对右侧的顺时针方向，不是机器人航向角。`quality_score` 只用于相对排序，不是概率或协方差。

## 错误码

| 状态码 | 场景 |
| --- | --- |
| 413 | `image` 为空或超过 10 MB |
| 422 | 缺少 `image`；图片格式/尺寸/解码失败；`intrinsics`/`config` 不是合法 JSON 或 schema 校验失败 |
| 404 / 405 | 路径或方法错误 |
| 500 | 未处理异常（不返回 traceback） |

## 与 CLI / 库的对应关系

```bash
python -m star16 detect infrared.jpg --intrinsics camera.json --config detector.json --json
```

CLI 与本接口使用相同的 `star16.detect` 与模型校验。区别仅在于输入方式：CLI 直接读取图像文件与 JSON 文件，本接口读取 multipart 表单。完整算法与坐标约定见 [算法与坐标.md](算法与坐标.md)。
