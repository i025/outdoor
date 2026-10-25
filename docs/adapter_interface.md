# OutdoorGraph v0.2 Adapter 接口

## 边界与版本

OutdoorGraph 只负责契约校验、Scene IR、来源记录和独立进程调度。MoGe 推理、
Infinigen 草叶生成/材质/Scatter 均调用现有安装，第三方代码和权重不合并进本项目。
核心 `Scene` schema 保持 **0.1**，兼容原有代理 Demo 和依赖传播；
新增 **SceneBundle schema 0.2** 为核心 IR 附加相机、文件资产、区域与执行来源。
`outdoorgraph integrate` 使用独立后端；旧 `run/demo` 仍使用 v0.1 注册的代理算子。
不能把外部算子交给代理 `execute()` 并声称已经执行 MoGe 或 Blender。

## 统一数据契约

`SceneBundle(scene, camera, assets, regions, coordinate_frame, provenance)` 可
`save/load/to_dict/from_dict`。`validate_bundle()` 先校验核心图，再验证以下数据。

| 字段 | 格式与含义 |
|---|---|
| `scene` | 现有 Scene IR；稳定节点 ID、验证状态明确的执行依赖、参数、seed 和 metadata |
| `coordinate_frame` | `units=m, axis=z_up, handedness=right`；单目尺度标注 `model_estimated`，不视作测量真值 |
| `camera.id/width/height` | 稳定相机 ID、推理/Mask 所用图像尺寸，禁止以原图尺寸混用缩放后的 K |
| `camera.intrinsics_pixels` | 有限 3×3 像素内参，正焦距，最后一行 `[0,0,1]`；当前 Blender 后端只接受零 skew |
| `camera.world_from_camera_cv` | 有限刚性 4×4 变换，旋转正交且 determinant=+1；从 OpenCV 相机坐标到世界 |
| `assets[]` | 唯一 `id`、本地 `uri`、`format`、`coordinate_frame`、`provenance`；几何按引用传递，不嵌入大数组 |
| `regions[]` | 唯一 `id`、PNG `uri`、`semantic`、`source=manual/model`、width/height；与相机像素网格严格匹配 |
| `provenance` | 仓库绝对路径、Git commit、checkpoint 路径/model ID、人工输入与未实现能力；第三方既有 dirty 状态另见 inventory 和 Git 审计记录 |

URI 可以是本地相对路径、绝对路径或 `file:` URI；相对引用一律基于 bundle 文件目录，
不基于执行时 cwd。当前后端不隐式下载远程资产。镜像、权重和商业 API 的许可不因接口统一而改变。

## 坐标、尺度与相机

MoGe 原始点采用 OpenCV：X 向右、Y 向下、Z 向前，depth 为相机 Z。
当前最小场景手工声明相机无滚转、水平朝前，采用刚性旋转：

```text
(x_world, y_world, z_world) = (x_cv, z_cv, -y_cv) * scale_m_per_unit
R_world_from_cv = [[1,0,0], [0,0,1], [0,-1,0]]
```

这不是模型自动推断的重力/地平线，也没有恢复地理位置或多视角外参。
MoGe v2 的公制预测仍有尺度不确定性，额外校准因子保存在生成参数中。
MoGe 归一化 K 的第 0/1 行分别乘实际推理宽/高，得到像素 K。
像素原点为左上角，像素中心为 `(column+0.5,row+0.5)`。

Blender 相机局部采用 X 右、Y 上、-Z 前，因此：
`world_from_blender_camera = world_from_camera_cv @ diag(1,-1,-1,1)`。
后端缩放 K 到渲染分辨率，通过焦距、sensor、pixel aspect 与 principal-point shift
设置相机，并用三维点投影检查误差 ≤0.001 px。Mask 始终保留推理图像分辨率。

GLB 由 Blender 标准导出器转换到 glTF 的 Y-up；文件本身携带节点变换。
不能把 GLB 顶点当作未经转换的 Z-up 数组。源 `geometry.npz` 与 `.blend` 场景均为米制 Z-up。

## 中间文件与稳定 ID

```text
config.resolved.json       实际解析后的路径、模型/生成配置、人工多边形、seed
geometry/moge_raw.npz      原始 points(H,W,3)、depth(H,W)、mask(H,W)、intrinsics(3,3)、normal
geometry/reference.png     EXIF 定向并等比缩放后的 RGB，与上述网格一致
geometry/ground_manual.png 人工区域；MoGe validity 另存 moge_validity.png，不冒充语义 Mask
geometry/geometry.npz      vertices(N,3)、faces(M,3)、uv(N,2)、vertex_colors(N,3)、region_weight(N)
scene_ir.json              外部生成节点及声明的执行依赖
scene_bundle.json          完整的相机/资产/区域/来源契约
blender/scene.blend        可编辑网格、原生 GN Scatter、草资产、相机、打包纹理与完整 bundle
blender/scene.glb          Realize Instances 后的通用可打开几何、嵌入纹理、节点 extras
blender/render.png         校准参考相机的真实 Cycles CPU 渲染
blender/overview.png       人工选区局部的新视角真实渲染，用来观察新生成草丛
blender/objects.json       实际生成对象、来源、参数、seed、实例矩阵及快照 ID
blender/reopen_verification.json 另一进程重开/导入后的真实检查
execution.json            精确 argv、cwd、每阶段退出码、耗时和日志
summary.json/failure.json  只有全部阶段和检查成功才生成 completed 摘要；失败保留原因
```

NPZ 禁止 pickle。UV 采用图像左上原点的归一化像素中心，进入 Blender 时只翻转 V。
网格复用官方 depth-edge 检查，降采样单元必须全块有效，过滤后移除孤立顶点。
这只是单视图三角化/文件转换，不补全不可见几何，也不重新实现重建模型。

节点稳定 ID 为 `moge_surface`、`infinigen_grass`。源资产和 Scatter 对象保存
`og_node_id/og_object_id/og_generator/og_config/og_provenance`。
完整 bundle 保存为 `.blend` 内的 `OutdoorGraph.SceneBundle.json` 文本，scene 另带来源属性；
GLB 使用 `export_extras=True` 保留导出对象的同类字段。
实例 ID 只保证相同 seed/config 下的快照可重现，不承诺改变 density 后仍逐实例对应。

## 真正执行的后端

1. `moge_worker.py` 用现有 Python 3.13 环境和本地 checkpoint 调用官方
   `moge.model.v2.MoGeModel.from_pretrained(...).infer(...)`。现有官方 infer CLI
   在本机 OpenCV 5 无 EXR writer 而导出失败；薄封装只改用 NumPy 序列化，没有改推理算法。
2. Python 3.11 主进程形成并验证 SceneBundle。依赖由 Adapter 明确声明为
   surface → grass，不把它声称为图像归纳出的因果机制。目标实例数换算为面积密度，
   实际数量由 Infinigen 随机 Scatter 决定，不保证等于目标数。
3. Blender 3.6/Python 3.10 独立进程导入网格，调用已有 `GrassTuftFactory`、
   `make_asset_collection`、`scatter_instances`。FACE boolean selection 要求三角形三个
   顶点均在手工区内。草叶形状、材质、点分布、朝向与实例化均来自 Infinigen。
4. 第二个 Blender 进程重新打开 `.blend`，核对内嵌 bundle/来源、打包图片、稳定 ID、
   相机投影，重算实际实例的 BVH 地表接触和像素 Mask 范围；真正解码两张 PNG。
   随后清空场景，独立导入 GLB，检查几何面和 extras。任何断言失败使流程返回非零。

进程不使用 shell 拼接，也不执行 IR 中任意生成代码；后端工厂是固定的已审核入口。
不同 Python 版本和原生扩展依赖不安装到 OutdoorGraph 主环境中。
新缓存、日志和输出只写 OutdoorGraph 的新运行目录；禁止默认覆盖已有运行。

## 其他系统的接入计划与研究边界

| 系统 | 下个独立 Adapter，当前状态 |
|---|---|
| UrbanWorld2.0 | 以 OSM/building ID、局部地理变换、道路/地块文件、资产 GLB 接入；先修复独立环境的迁移路径，再调用已有 stage5 assembly。现有 assemble 复现脚本含联网余额逻辑，不能直接当纯离线入口。未接入本次主链 |
| fal-worldclaw | 用 Node 子进程消费 WorldSpec JSON，复用 buildTerrain、solvePlacements、区域权重和现有反馈；显式记录 Y-up→Z-up/UV/seed。远程 fal 服务不默认启用；本地已有结果全为 procedural，不能声称 AI 资产生成已验证 |
| RCWM | 原始输入/JS程序/分层 part.json/渲染/评估保留独立 Agentic Baseline；通过文件引用与相机变换比较。既有本地续跑不等于原 API 完整复现 |
| Building Rome | 可选深度 pack、几何 GLB 和独立 Baseline；保留模型、裁剪、K 与 cam_to_yaw，不强装其 CUDA 依赖到主环境。搬迁损坏入口和非商业许可先单独处理 |

后续研究依次为：自动区域和重力证据 → 机制假设及置信度 → 显式验证可执行依赖 →
联合相机/几何/生成参数优化及改动范围控制。v0.2 仅完成数据通路和真实执行，
没有实现自动程序归纳、完整三维补全、多视角重建或全局一致性优化。
