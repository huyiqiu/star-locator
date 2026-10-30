# star-locator

本机运行的完整应用：USB/UVC 相机采集、反光亮点检测、4×4 标记 ID 与方向解码、相对位姿估计、调试取景器、相机标定和开放 API。代码分为三层：

- `star16/`：纯算法库 + CLI。输入 BGR 图像、相机内参与检测参数，输出标记 ID、方向与相对位姿；不依赖 FastAPI、相机或全局状态。
- `backend/`：应用后端。负责 UVC 采集、曝光/增益、棋盘标定、合成演示、参数持久化和静态页面托管，内部调用 `star16`。
- `web/`：调试界面，仍由同一后端在 `/` 与 `/assets` 提供；浏览器相机与上传检测通过无状态 `POST /api/v1/detect` 携带自己的内参与参数。

- 工作台：<http://127.0.0.1:8766/>
- 交互式接口文档：<http://127.0.0.1:8766/docs>
- 单帧接口契约：[docs/API-v1.md](docs/API-v1.md)
- 标记定义与制作文件：`markers/`，统一 18 种，无基准族模式切换；原 ID 001–009 图案保留
- 手持标定操作：`docs/手持棋盘格标定.md`
- 算法与坐标约定：`docs/算法与坐标.md`
相机参数、标定结果和现场图像保存在本机 `data/` 目录，不随仓库分发。首次使用请按实际设备、反光点尺寸和工作环境设置并标定。

选择“纯点阵”，勾选“亮点调试：包含未编码的反光点”，可只看检测出的亮点；取消该勾选后，纯点阵只显示通过 ID 校验的标记点。

## 算法库与 CLI

库的公共入口与版本：

```python
import cv2
from star16 import DetectorConfig, Intrinsics, detect, __version__

image = cv2.imread('infrared.jpg')
intrinsics = Intrinsics(
    width=1280, height=720, fx=1000, fy=1000, cx=640, cy=360,
    distortion=[0, 0, 0, 0, 0], calibrated=True, source='my_calibration',
)
result, images = detect(image, DetectorConfig(), intrinsics)
for marker in result['markers']:
    print(marker['id'], marker['pose_status'])
    if marker['pose']:
        print('marker-in-camera', marker['pose']['tvec_mm'])
        print('camera-in-marker', marker['pose']['camera_position_marker_mm'])
```

省略 `intrinsics`（或 `calibrated=False`）时仍解码 ID 与方向，但 `pose` 为 `null`、`pose_status` 为 `calibration_required`。`detect` 返回 `(result, images)`，`images` 含 `raw/binary/glow/blobs` 四个 numpy 数组，是否编码为 data URL 由调用方决定。

CLI 不安装也可在仓库根目录运行：

```bash
python -m star16 --version
python -m star16 detect infrared.jpg --intrinsics camera.json --config detector.json --json
```

也可 `pip install -e .` 后使用 `star16 detect ...`。`--intrinsics` 接受裸 `Intrinsics` 对象或 `{"intrinsics": {...}}`；`--config` 接受裸 `DetectorConfig`、`{"detector": ...}` 或 `{"config": ...}`。退出码：有标记为 0，无标记为 1，文件/JSON/schema 错误为 2。`python -m star16` 与 `star16` 使用同一实现，`star16.__version__`、`/api/health` 与包版本均为 2.0.0。

## 启动

推荐 Python 3.11 或 3.12。首次安装：

```bash
cd star-locator
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

启动脚本优先使用项目 `.venv`。也可选择已经装好依赖的 Python：

```bash
STAR16_PYTHON="$(command -v python3)" ./run.sh
```

修改端口：

```bash
STAR16_PORT=8770 ./run.sh
```

默认绑定 `127.0.0.1:8766`，静态页面与 API 使用同一服务。页面默认选择“后端 UVC”，打开后自动连接设备索引 0；需要合成演示时可打开 `http://127.0.0.1:8766/?source=simulation`。浏览器访问 localhost 可以申请相机权限。macOS 的后端直连采集还需要允许启动 Python 的应用使用相机：在“系统设置 → 隐私与安全性 → 相机”中允许 Qoder 或终端，然后重启服务；未授权时 OpenCV 会报 `not authorized to capture video`。

后端将 UVC 采集放在独立进程，通过共享内存处理最新帧；设备重连会重建采集进程。首帧会等待预热，指定帧率失败后尝试同分辨率的设备原生帧率。页面显示实际帧率，分辨率不匹配会明确报错。集成 `CameraManager` 的 Python 脚本应使用 `if __name__ == '__main__':` 入口保护，以支持 `spawn`。

### 相机曝光与增益（macOS UVC）

“实时取景 → 后端 UVC”现在可读取相机允许范围、切换自动/手动曝光、设置曝光时间和增益，并一键恢复相机报告的出厂默认值。每次操作都会从硬件回读，写入被忽略时会报错。曝光输入单位为毫秒；接口字段 `exposure_100us` 的单位为 0.1 ms，例如 `100` 表示 10 ms。应用成功的设置会保存到 `data/camera_controls.json`，服务或相机重启后会在设备重新打开时自动写回并回读确认；“一键恢复默认”会同时清除保存值。

此功能使用随项目附带源码、MIT 许可的 `uvc-util`，本机可通过 `./tools/build_uvc_util.sh` 重建。当前默认控制目标为已实测的 USB 相机 `05a3:9230`；更换设备时用 `STAR16_UVC_VENDOR_ID` 和 `STAR16_UVC_PRODUCT_ID` 指定新 USB ID。若多台相机使用相同 ID，接口会拒绝写入，以免调错设备。相机的光传感器和红外补光灯没有出现在它开放的 UVC 控制列表中，因此这里仅能改变曝光和增益，不能直接强制补光灯常亮。

```bash
curl http://127.0.0.1:8766/api/camera/controls
curl -X PUT http://127.0.0.1:8766/api/camera/controls \
  -H 'Content-Type: application/json' \
  -d '{"mode":"manual","exposure_100us":100,"gain":0}'
curl -X POST http://127.0.0.1:8766/api/camera/controls/reset
```

## 使用顺序

1. **先调亮点。** 页面默认使用“后端 UVC”并自动连接设备 0；也可切换“浏览器相机”按设备名称选择。同一台设备请只使用一个采集入口。调阈值、面积和圆度，让反光圆点分开、背景被抑制。参数更改后点击“应用参数”。
2. **制作一块基准标记。** 在“标记基准族”输入实际点中心间距和直径，例如 4 cm、0.6 cm，然后下载 SVG 或裁切模板。按 100% 比例打印模板，在低红外反射底板上贴回归反光材料。纸面黑白打印不能代替反光材料。
3. **确认 ID 和方向。** 完整拍到四个角锚点，保持板内圆点不粘连。页面应该出现对应 ID，转动板时图像内方向连续改变。方向以标记 +X 为基准，相对图像右侧顺时针计角。
4. **手持棋盘格标定。** 直接进入“相机标定”，设置横向/纵向内角点、实际方格边长和目标视图数。页内连接 USB 相机，点击“开始自动采集”；手持棋盘依次改变位置、距离和倾角，每个新视角短暂停稳约半秒。程序只保存清晰、稳定、非重复的视图，显示角点、采集进度和 3×3 区域覆盖；达到目标后自动计算内参，检查右侧误差后点击“应用标定结果”。也可暂停、手动采集或上传照片补充。改变棋盘规格或取景分辨率前需清空旧视图。也可导入已有 OpenCV 内参 JSON。
5. **验证物理尺度。** 在独立测量的位置、距离和角度下比较结果。图像重投影误差小不等于物理位置误差小，板平整度、粘贴误差、内参和曝光都会影响结果。

手贴点阵若能提取全部亮点、编码却未通过，可在“检测参数 → 高级筛选参数”检查最大重投影误差。该值现在同时约束完整点阵拟合与 PnP；调高可验证 ID，但会放宽位姿质量门限。正式测距应优先校正点中心位置、板面平整度和曝光，再将门限收紧。

默认按严格模式识别：编码须精确匹配（`max_hamming=0`），点阵拟合和位姿均须通过重投影门限；如果完整点阵的编码候选因几何误差被拒绝，重叠的局部候选也会被拒绝，避免只用部分亮点误报另一个 ID。需要容错时可显式启用 1 位纠错，但会提高误报风险。

当畸变反算在边缘不稳定时，程序会尝试在原始画面中补充识别完整点阵，回退仅接受精确编码。对于完整且精确解码的点阵，位姿重投影误差超过常规门限但不超过宽松位姿门限时，也可输出粗略估计。后端 UVC 位姿使用服务端已保存的相机内参与畸变系数；浏览器相机与上传检测使用请求中携带的内参。宽松位姿门限 `edge_pose_max_reprojection_error_px` 默认 20 px，可在高级筛选参数中调整。结果标记为 `pose_status: approximate`，页面显示“粗略估计”；如果位姿超出此门限，仍显示精确解码的 ID，但不显示位置。两个精化候选近似等价时标记为 `pose_status: ambiguous`（页面显示“平面双解”），仍返回位姿与 `pose.candidates`，不按标记像素尺寸拒绝位姿。
6. **调用 API。** 下游应检查 `pose_status` 与帧新鲜度：`ambiguous` 虽然带位姿，但两个候选关于视线镜像，横向位置可相差约 1 m，只有确认连续分支或具备安装先验后才应使用。服务只输出相对标记的位姿；世界坐标与底盘坐标由集成应用根据自己的地标位姿和相机安装外参转换。

棋盘默认 9×6 **内角点**、2.5 cm 方格，含留白的 SVG 为 30×22.5 cm。A4 横向打印可将方格设为 2 cm，此时为 24×18 cm；请保持 100% 打印比例，并在界面输入实际方格尺寸。

## 单帧接口

`POST /api/v1/detect` 是**无状态**接口：检测只使用本次请求携带的内参与参数，不读取也不写入服务端保存的 `data/config.json`、`data/intrinsics.json`，因此多个客户端可以各自使用不同相机和参数。

```bash
curl -X POST http://127.0.0.1:8766/api/v1/detect \
  -F 'image=@infrared.jpg' \
  -F 'intrinsics=<camera-intrinsics.json' \
  -F 'config=<detector.json' \
  -F 'include_images=false'
```

```python
import json
import requests

with open('infrared.jpg', 'rb') as image:
    frame = requests.post(
        'http://127.0.0.1:8766/api/v1/detect',
        files={'image': image},
        data={
            'intrinsics': json.dumps(intrinsics),  # 可省略：只解码 ID
            'config': json.dumps(config),          # 可省略：使用库默认值
            'include_images': 'false',
        },
        timeout=10,
    ).json()

for marker in frame['markers']:
    if marker['pose_status'] == 'valid':
        print(marker['id'], marker['direction_deg'])
        print(marker['pose']['tvec_mm'])
        print(marker['pose']['camera_position_marker_mm'])
```

`intrinsics` 与 `config` 都是 JSON 字符串。省略 `intrinsics`（或 `calibrated=false`、宽高比不一致）时仍返回 ID 与方向，但 `pose=null`、`pose_status=calibration_required`；省略 `config` 时使用库默认值（间距 40 mm、直径 20 mm、固定阈值 185），**不是** `data/config.json` 中的现场值。省略参数时响应中的 `config` 字段回显实际生效的完整配置。

`include_images=true` 会附带 `raw`、`binary`、`glow`、`blobs` 四张 JPEG data URL。前两张为原图和阈值图；后两张分别是已解码标记点、所有候选亮点。

接口接受 JPEG、PNG、WebP、BMP，单图最大 10 MB、1600 万像素；空文件或超限返回 413，格式/尺寸/JSON/schema 非法返回 422。完整的字段、单位、坐标系、warnings 与错误码见 [docs/API-v1.md](docs/API-v1.md)。

## UVC 实时接口

后端 UVC 管线使用服务端保存的检测参数与内参（`PUT /api/config`、`PUT /api/intrinsics`）。浏览器相机与上传路径改用无状态 `/api/v1/detect`，使用请求携带的当前页面参数；两条路径互不影响。

```bash
curl -X POST http://127.0.0.1:8766/api/camera/start \
  -H 'Content-Type: application/json' \
  -d '{"index":0,"width":1280,"height":720,"fps":30}'

curl http://127.0.0.1:8766/api/camera/frame
```

```javascript
const ws = new WebSocket('ws://127.0.0.1:8766/ws/camera');
ws.onmessage = ({data}) => {
  const frame = JSON.parse(data);
  if (frame.stale) {
    // 清除之前的位姿；不能把旧位姿当作新观测。
    return;
  }
  for (const marker of frame.markers) {
    if (marker.pose_status === 'valid') {
      console.log(marker.id, marker.pose.filtered?.camera_position_marker_cm ?? null);
    }
  }
};
```

采集和算法处理分别运行，始终分析最新一帧，不累积待处理队列。后端不返回超过 1 秒的旧帧；页面在连续 1.8 秒没有新观测时清空显示。实际处理帧率取决于分辨率、CPU、亮点数量和候选数量，`fps_requested` 不能作为算法帧率。结果中的 `rates` 分别给出实际采集和处理帧率，`timings` 给出检测、图像编码及处理总耗时。实时页面通过 `image_mode=raw|binary|glow|blobs` 只接收当前模式图像；不传该参数时保持返回全部调试图。

实时 UVC 与浏览器相机的 `pose.filtered` 包含平滑后的 `camera_position_marker_cm` 和 `distance_cm`，同时保留 `_mm` 兼容字段。原始 PnP 坐标、变换矩阵及重投影误差保持不变；上传单张图片和合成测试也保持原始结果，并返回最小误差解与 `pose.candidates`。`temporal_smoothing_ms` 默认为 300，可通过检测参数或 `PUT /api/config` 调整，0 表示关闭。第一帧有效位姿立即输出；之后用 3 次观测的中值抑制单帧尖峰，并根据位置变化量自动缩短平滑时间，让相机移动时更快跟随。超过 350 ms 没有有效观测时，新位姿立即重新建立。标记未被识别时仍不输出旧位置。

`pose_status=ambiguous` 时仍会输出位姿：检测器返回两个精化候选（`pose.candidates`，按 RMS 升序），实时链路用上一帧 `camera_position_marker_mm` 选择最近的连续分支，在 `pose` 上写入 `branch_selection`（`best_rms` 或 `continuity`）和 `filtered_candidate_index`，`filtered` 对应所选分支，可能与顶层最小误差解相差约 1 m。若首帧没有历史，则取最小误差解并在下一帧起按连续性保持；状态不会升级为 `valid`。

停止采集会释放相机：

```bash
curl -X POST http://127.0.0.1:8766/api/camera/stop
```

## 内参与坐标

保存的内参示例结构如下，数值必须替换为真实标定结果；不应把示例焦距用于物理定位：

```json
{
  "width": 1280,
  "height": 720,
  "fx": 1000,
  "fy": 1000,
  "cx": 640,
  "cy": 360,
  "distortion": [0, 0, 0, 0, 0],
  "calibrated": false,
  "source": "replace_with_real_calibration"
}
```

图像宽高比改变时暂停位姿计算；同宽高比的纯缩放会缩放内参。裁切、变焦、数字防抖或模式切换不能仅靠等比缩放处理。支持 OpenCV 针孔模型 4、5、8 项畸变系数，鱼眼模型需另行接入。

页面的长度和位置使用 **厘米**。API 同时提供 `_cm` 字段；为兼容已有调用，`_mm` 字段和 API 4×4 矩阵的平移仍以毫米为单位。标记原点为整个 4×4 网格中心，+X 向右、+Y 向下、+Z 指向板背面。相机坐标使用 OpenCV 的 +X 右、+Y 下、+Z 前。

- `T_camera_marker`：标记点变换到相机系。
- `T_marker_camera`：相机坐标变换到标记系，为上一矩阵的逆。
- `tvec_mm` / `tvec_cm`：标记原点在相机系中的坐标（marker-in-camera），等于 `T_camera_marker[:3,3]`。
- `camera_position_marker_mm` / `_cm`：相机光心在标记系中的坐标（camera-in-marker），即 `T_marker_camera[:3,3] = -Rᵀt`，不是 `tvec`。
- `optical_depth_mm`：标记中心在相机系中的 Z 分量，即 `tvec_mm[2]`。
- `distance_mm` / `distance_cm`：标记中心至相机光心的欧氏距离。

服务不保存地标地图或相机安装外参，只输出上述相对位姿。世界坐标与机器人坐标由集成应用自行计算：

```text
T_world_camera = T_world_marker @ T_marker_camera
T_world_robot  = T_world_camera @ T_camera_robot
```

`T_world_marker` 和 `T_camera_robot` 由集成应用维护。若标记位姿以标记中心 X/Y/Z 与偏航、俯仰、横滚描述，可采用 `Rz(yaw) @ Ry(pitch) @ Rx(roll)`，角度单位为度；世界 +Z 建议向上。这里的 Z 是坐标轴分量，直线距离另见 `distance_cm`。下游应检查 `pose_status`，不要用相对位姿冒充世界坐标或底盘中心位置。

## 测试

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q
node --check web/app.js
node --check web/calibration.js
node --check web/temporal.js
```

测试覆盖编码族旋转与镜像距离、所有单点编码错误、十八个 ID 的旋转、透视与噪声、多标记、遮挡、畸变、内参缩放、相对位姿单位与逆变换、无状态 v1 接口的输入校验与参数隔离、CLI 退出码与两种位姿语义、接口输入校验、新鲜度，以及合成棋盘照片的完整标定流程。合成投影器与检测器分离，解码器不会接收生成器的 ID 或位姿真值。

实机照片只用于当前亮点检测验证；尚未使用实际按模板制作的板、真实相机标定与独立真值进行定位精度验证。

## 工程结构

```text
star16/family.py         冻结的码本、ID、真实毫米 SVG
star16/models.py         库级 DetectorConfig / Intrinsics 校验
star16/detector.py       分割、亚像素亮度中心、候选分组、解码、IPPE / LM
star16/temporal.py       实时位姿平滑
star16/cli.py            star16 detect ... 命令行入口
star16/__init__.py       公共 API 与版本
backend/app.py           FastAPI、v1 无状态检测、标定、HTTP、WebSocket、静态托管
backend/camera.py        UVC 采集、最新帧处理、新鲜度与断开处理
backend/calibration.py   棋盘检测、稳定/重复视图筛选、覆盖与采集状态
backend/simulation.py    独立 3D 投影与图像生成
backend/models.py        应用层输入校验
web/                     无构建依赖的调试界面（由后端托管）
markers/                 18 块标记和裁切模板、完整编码清单
presets/                 当前实机反光点参数预设
docs/                    接口契约、算法与坐标、手持标定说明
tests/                   算法、库/CLI 和接口测试
data/                    本机参数、内参；不纳入 Git
pyproject.toml           star16-locator 包与 console script
```

`star16` 只依赖 numpy、OpenCV 与 pydantic，可单独安装或复制使用；`backend` 额外依赖 FastAPI 等应用组件。`requirements.txt` 覆盖运行本服务所需的全部依赖，`pip install -e .` 仅用于获得 `star16` 命令，两种方式可并存。

服务默认本机访问。外部网页或其他设备的访问需自行配置部署、身份验证与访问范围；单独前端域名可通过 `STAR16_CORS_ORIGINS` 显式列出允许来源。当前实现为单个设备工作台，多个客户端共享同一组检测参数和相机。
