# Star16 红外星阵定位工作台

本机运行的完整应用：USB/UVC 相机采集、反光亮点检测、4×4 标记 ID 与方向解码、相对位姿估计、调试取景器、相机标定和开放 API。

- 工作台：<http://127.0.0.1:8766/>
- 交互式接口文档：<http://127.0.0.1:8766/docs>
- 标记定义与制作文件：`markers/`，统一 18 种，无基准族模式切换；原 ID 001–009 图案保留
- 手持标定操作：`docs/手持棋盘格标定.md`
- 算法与坐标约定：`docs/算法与坐标.md`
相机参数、标定结果、地图和现场图像保存在本机 `data/` 目录，不随仓库分发。首次使用请按实际设备、反光点尺寸和工作环境设置并标定。

选择“纯点阵”，勾选“亮点调试：包含未编码的反光点”，可只看检测出的亮点；取消该勾选后，纯点阵只显示通过 ID 校验的标记点。

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

默认绑定 `127.0.0.1:8766`，静态页面与 API 使用同一服务。浏览器访问 localhost 可以申请相机权限。macOS 的后端直连采集也需要允许启动 Python 的应用使用相机。

后端将 UVC 采集放在独立进程，通过共享内存处理最新帧；设备重连会重建采集进程。首帧会等待预热，指定帧率失败后尝试同分辨率的设备原生帧率。页面显示实际帧率，分辨率不匹配会明确报错。集成 `CameraManager` 的 Python 脚本应使用 `if __name__ == '__main__':` 入口保护，以支持 `spawn`。

### 相机曝光与增益（macOS UVC）

“实时取景 → 后端 UVC”现在可读取相机允许范围、切换自动/手动曝光、设置曝光时间和增益，并一键恢复相机报告的出厂默认值。每次操作都会从硬件回读，写入被忽略时会报错。曝光输入单位为毫秒；接口字段 `exposure_100us` 的单位为 0.1 ms，例如 `100` 表示 10 ms。相机断电或重插后可能自行恢复硬件设置，请重新读取。

此功能使用随项目附带源码、MIT 许可的 `uvc-util`，本机可通过 `./tools/build_uvc_util.sh` 重建。当前默认控制目标为已实测的 USB 相机 `05a3:9230`；更换设备时用 `STAR16_UVC_VENDOR_ID` 和 `STAR16_UVC_PRODUCT_ID` 指定新 USB ID。若多台相机使用相同 ID，接口会拒绝写入，以免调错设备。相机的光传感器和红外补光灯没有出现在它开放的 UVC 控制列表中，因此这里仅能改变曝光和增益，不能直接强制补光灯常亮。

```bash
curl http://127.0.0.1:8766/api/camera/controls
curl -X PUT http://127.0.0.1:8766/api/camera/controls \
  -H 'Content-Type: application/json' \
  -d '{"mode":"manual","exposure_100us":100,"gain":0}'
curl -X POST http://127.0.0.1:8766/api/camera/controls/reset
```

## 使用顺序

1. **先调亮点。** 在“后端 UVC”中连接设备 0；也可在“浏览器相机”中按设备名称选择相机。同一台设备请只使用一个采集入口。调阈值、面积和圆度，让反光圆点分开、背景被抑制。参数更改后点击“应用参数”。
2. **制作一块基准标记。** 在“标记基准族”输入实际点中心间距和直径，例如 4 cm、0.6 cm，然后下载 SVG 或裁切模板。按 100% 比例打印模板，在低红外反射底板上贴回归反光材料。纸面黑白打印不能代替反光材料。
3. **确认 ID 和方向。** 完整拍到四个角锚点，保持板内圆点不粘连。页面应该出现对应 ID，转动板时图像内方向连续改变。方向以标记 +X 为基准，相对图像右侧顺时针计角。
4. **手持棋盘格标定。** 直接进入“相机标定”，设置横向/纵向内角点、实际方格边长和目标视图数。页内连接 USB 相机，点击“开始自动采集”；手持棋盘依次改变位置、距离和倾角，每个新视角短暂停稳约半秒。程序只保存清晰、稳定、非重复的视图，显示角点、采集进度和 3×3 区域覆盖；达到目标后自动计算内参，检查右侧误差后点击“应用标定结果”。也可暂停、手动采集或上传照片补充。改变棋盘规格或取景分辨率前需清空旧视图。也可导入已有 OpenCV 内参 JSON。
5. **验证物理尺度。** 在独立测量的位置、距离和角度下比较结果。图像重投影误差小不等于物理位置误差小，板平整度、粘贴误差、内参和曝光都会影响结果。

手贴点阵若能提取全部亮点、编码却未通过，可在“检测参数 → 高级筛选参数”检查最大重投影误差。该值现在同时约束完整点阵拟合与 PnP；调高可验证 ID，但会放宽位姿质量门限。正式测距应优先校正点中心位置、板面平整度和曝光，再将门限收紧。

默认按严格模式识别：编码须精确匹配（`max_hamming=0`），点阵拟合和位姿均须通过重投影门限；如果完整点阵的编码候选因几何误差被拒绝，重叠的局部候选也会被拒绝，避免只用部分亮点误报另一个 ID。需要容错时可显式启用 1 位纠错，但会提高误报风险。

当畸变反算在边缘不稳定时，程序会尝试在原始画面中补充识别完整点阵，回退仅接受精确编码。位姿仍使用已保存的相机内参与畸变系数计算；边缘位姿误差门限 `edge_pose_max_reprojection_error_px` 默认 20 px，可在高级筛选参数中调整。回退位姿标记为 `pose_status: approximate`，页面显示“粗略估计”；如果位姿超出此门限，仍显示精确解码的 ID，但不显示位置。
6. **调用 API。** 下游应检查 `pose_status === "valid"`，并检查帧是否新鲜。世界坐标需要明确配置地标地图；底盘坐标还需要相机安装外参。

棋盘默认 9×6 **内角点**、2.5 cm 方格，含留白的 SVG 为 30×22.5 cm。A4 横向打印可将方格设为 2 cm，此时为 24×18 cm；请保持 100% 打印比例，并在界面输入实际方格尺寸。

## 单帧接口

```bash
curl -X POST http://127.0.0.1:8766/api/detect \
  -F 'file=@infrared.jpg' \
  -F 'include_images=false'
```

```python
import requests

with open('infrared.jpg', 'rb') as image:
    frame = requests.post(
        'http://127.0.0.1:8766/api/detect',
        files={'file': image},
        data={'include_images': 'false'},
        timeout=10,
    ).json()

for marker in frame['markers']:
    if marker['pose_status'] == 'valid':
        print(marker['id'], marker['direction_deg'])
        print(marker['pose']['camera_position_marker_mm'])
```

`include_images=true` 会附带 `raw`、`binary`、`glow`、`blobs` 四张 JPEG data URL。前两张为原图和阈值图；后两张分别是已解码标记点、所有候选亮点。

接口接受 JPEG、PNG、WebP、BMP，单图最大 10 MB、1600 万像素。检测参数以服务当前配置为准。`PUT /api/config` 使用完整配置替换语义，省略字段恢复该字段默认值。

## UVC 实时接口

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
      console.log(marker.id, marker.pose.filtered?.camera_position_world_cm ?? null);
    }
  }
};
```

采集和算法处理分别运行，始终分析最新一帧，不累积待处理队列。后端不返回超过 1 秒的旧帧；页面在连续 1.8 秒没有新观测时清空显示。实际处理帧率取决于分辨率、CPU、亮点数量和候选数量，`fps_requested` 不能作为算法帧率。结果中的 `rates` 分别给出实际采集和处理帧率，`timings` 给出检测、图像编码及处理总耗时。实时页面通过 `image_mode=raw|binary|glow|blobs` 只接收当前模式图像；不传该参数时保持返回全部调试图。

实时 UVC 与浏览器相机的 `pose.filtered` 包含平滑后的 `camera_position_marker_cm`、`camera_position_world_cm`、`robot_position_world_cm`（有对应地图或安装外参时）和 `distance_cm`，同时保留 `_mm` 兼容字段。原始 PnP 坐标、变换矩阵及重投影误差保持不变；上传单张图片和合成测试也保持原始结果。`temporal_smoothing_ms` 默认为 300，可通过检测参数或 `PUT /api/config` 调整，0 表示关闭。第一帧有效位姿立即输出；之后用 3 次观测的中值抑制单帧尖峰，并根据位置变化量自动缩短平滑时间，让相机移动时更快跟随。超过 350 ms 没有有效观测时，新位姿立即重新建立。标记未被识别时仍不输出旧位置。

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

页面的长度和位置使用 **厘米**。API 提供 `_cm` 位置及距离字段；为兼容已有调用，`_mm` 字段和 API 4×4 矩阵的平移仍以毫米为单位，页面高级矩阵编辑框则使用厘米并在保存时转换。标记原点为整个 4×4 网格中心，+X 向右、+Y 向下、+Z 指向板背面。相机坐标使用 OpenCV 的 +X 右、+Y 下、+Z 前。

- `T_camera_marker`：标记点变换到相机系。
- `T_marker_camera`：相机坐标变换到标记系，为上一矩阵的逆。
- `camera_position_marker_cm`：相机光心在标记系中的坐标，即 `-Rᵀt` 换算为厘米，不是 `tvec`。
- `T_world_camera = T_world_marker @ T_marker_camera`；`camera_position_world_cm` 是该矩阵平移换算为厘米后的结果，页面主要显示其地图 X/Y。
- `T_world_robot = T_world_camera @ T_camera_robot`；配置安装外参后还会返回 `robot_position_world_cm`。

世界坐标的 +X/+Y 方向由使用者定义，建议 +Z 向上。在“相机标定 → 地标地图”选择标记 ID，填写标记中心的地图 X/Y/Z 和偏航、俯仰、横滚；旋转约定为 `Rz(yaw) @ Ry(pitch) @ Rx(roll)`，角度单位为度。标记物正面朝下贴在天花板时，标记 +Z 指向天花板，通常与世界 +Z 同向。这里的 Z 是坐标轴分量，直线距离另见 `distance_cm`；即使主要使用地图 X/Y，3D 姿态和标记高度仍需正确设置，倾斜时才能正确换算。

也可以通过 `PUT /api/map/marker-pose` 保存一个地标，通过 `GET /api/map/marker-poses` 读取；完整矩阵和安装外参继续由 `PUT /api/map` 管理。例如：

```bash
curl -X PUT http://127.0.0.1:8766/api/map/marker-pose \
  -H 'Content-Type: application/json' \
  -d '{"id":1,"x_cm":100,"y_cm":200,"z_cm":280,"yaw_deg":90,"pitch_deg":0,"roll_deg":0}'
```

没有地图时只输出相对位姿，不会把标记系 X/Y 冒充世界坐标；没有安装外参时不会假设相机等于底盘中心。可信地图坐标 `camera_position_world_cm` 仍要求 ID 精确匹配、网格拟合和位姿重投影误差都不超过 2.5 px。边缘回退则用单独的 `approximate_camera_position_world_cm` 输出粗略坐标，`world_position_status` 为 `approximate`；实时平滑值位于 `pose.filtered` 的同名字段。下游应检查状态与误差，不应把粗略坐标当作可信结果。

## 测试

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest tests -q
node --check web/app.js
node --check web/calibration.js
```

测试覆盖编码族旋转与镜像距离、所有单点编码错误、十八个 ID 的旋转、透视与噪声、多标记、遮挡、畸变、内参缩放、相对/世界/底盘变换、接口输入校验、新鲜度，以及合成棋盘照片的完整标定流程。合成投影器与检测器分离，解码器不会接收生成器的 ID 或位姿真值。

实机照片只用于当前亮点检测验证；尚未使用实际按模板制作的板、真实相机标定与独立真值进行定位精度验证。

## 工程结构

```text
backend/family.py        冻结的码本、ID、真实毫米 SVG
backend/detector.py      分割、亚像素亮度中心、候选分组、解码、IPPE / LM
backend/simulation.py    独立 3D 投影与图像生成
backend/camera.py        UVC 采集、最新帧处理、新鲜度与断开处理
backend/calibration.py   棋盘检测、稳定/重复视图筛选、覆盖与采集状态
backend/models.py        参数与输入校验
backend/app.py           FastAPI、标定、地图、HTTP、WebSocket
web/                    无构建依赖的调试界面
markers/                18 块标记和裁切模板、完整编码清单
presets/                当前实机反光点参数预设
tests/                  算法和接口测试
data/                   本机参数、内参、地图；不纳入 Git
```

服务默认本机访问。外部网页或其他设备的访问需自行配置部署、身份验证与访问范围；单独前端域名可通过 `STAR16_CORS_ORIGINS` 显式列出允许来源。当前实现为单个设备工作台，多个客户端共享同一组检测参数和相机。
