# OutdoorGraph v0.2 六系统集成清单

核查日期：2026-10-25（Asia/Shanghai）。本公开清单将已核查服务器的用户主目录统一记为 `~`；它们是本机实查路径，不是新机器默认路径。以下路径均从服务器实际查得。第三方仓库只读；
HEAD 不代表已包含其本地补丁，dirty 状态逐项记录；历史产物不等于本次重新跑通。
当前可运行主链为 MoGe v2 → SceneBundle → Infinigen 原生 GrassTuft/Scatter → Blender。
主机授权查询确认 8×NVIDIA A100-PCIE-40GB；普通 sandbox 无法访问驱动，不据此判定服务器失效。

| 系统 | 实际代码根 | Git HEAD | 本次状态 |
|---|---|---|---|
| MoGe | `~/outdoorsmodel/BuildRome/local/src/MoGe` | `74fbce054ebed49800de42d0ad0e83495065719a` | 官方 v2 API 真推理；独立 Python 3.13 + 本地权重 |
| Infinigen | `~/outdoorsmodel/infinigen/source` | `f5bcba8de47623da9b715348cf95822445e0e9a7` | 原生草生成/Scatter，独立 Blender CPU 真渲染 |
| UrbanWorld2.0 | `~/outdoorsmodel/UrbanWorld2.0/outdoor` | `110dcfc80533c487a6f845ede966ae4988183a2b` | 有历史场景；当前 editable 路径失效，规划外部调用 |
| fal-worldclaw | `~/outdoorsmodel/fal-worldclaw` | `3843e4f85ac80ee31b7b6b3824231f27c3a878a6` | Node 可执行，有纯程序化历史导出；未启用远程 API |
| RCWM | `~/outdoorsmodel/RecursiveCWM_code` | `3ecb133bc51d663ca296f2c653b70fa51cb02564` | 独立 Agentic Baseline；区分原 API 中断与本地续跑 |
| Building Rome | `~/outdoorsmodel/BuildRome` | `bf23d0244ceb53ce9fb6f64d309b5180fa4ef092` | 有历史几何结果；两推理环境存在搬迁坏链 |

## MoGe：当前主链实际复用

**实际来源。** 选择带 Git 元数据的 `~/outdoorsmodel/BuildRome/local/src/MoGe`，
HEAD `74fbce054ebed49800de42d0ad0e83495065719a`。另一个可用源码副本
`~/models/source/MoGe` 没有 `.git`，不能凭路径声称其 commit。
辅助 `~/models/source/utils3d-moge` 提供 utils3d_moge，当前无 Git 元数据。
模型为 `~/models/moge/model.pt`（140,550,416 bytes），模型卡标识
`Ruicheng/moge-2-vits-normal`、MIT；不将它混称为 MoGe-3。
BuildRome 另有 MoGe-3 源码/权重和历史 conditioning pack，本次无需启用其坏链环境。

**实际环境。** `~/environments/relateanything-inference/bin/python` 显式调用可运行：
Python 3.13.15，torch 2.13.0+cu126，numpy 2.5.2，Pillow 12.3.0，
opencv-python 5.0.0.93，scipy 1.18.1。MoGe 和 utils3d 通过显式源码路径导入，
没有假称该环境已安装 moge wheel。GPU 推理使用物理 GPU 0（A100），本次峰值约476 MiB；
Blender/Node 不参与 MoGe 推理。CUDA wheel runtime 与服务器 nvcc 工具链版本应分开记录。

**既有运行证据。** `~/projects/scene-benchmark/examples/smoke_models.py:203` 处有现成
MoGe v2 API 推理；`results/model_smoke/moge_smoke.json` 记录512×288推理通过、
point/depth/mask/normal/K输出形状与GPU耗时。这是历史记录，非本次新图像结果。
本次使用 MoGe 官方 `example_images/05_Mountain.jpg`（2048×1015），保持比例缩至512×254，
通过相同公开模型API实际运行，精确 K/点图/深度/validity/normal 写入 NPZ。
本次日志与独立重开验证在 `~/outdoor/runs/mountain_v02_verified/`，具体数值以该目录
`geometry/moge_report.json`、`execution.json` 与 `blender/reopen_verification.json` 为准。

**可复用入口/格式。** 官方脚本是 `moge/scripts/infer.py`，支持
`--input --output --pretrained --version v2 --device cuda:0 --resize 512 --resolution_level 0 --maps`，
可用显式外部 Python 子进程运行。它先完成推理，但本机OpenCV没有EXR writer，
在写 depth.exr 阶段失败；未修改第三方源码来绕过。OutdoorGraph薄封装
`adapters/moge_worker.py` 调用 `MoGeModel.from_pretrained(local_checkpoint).infer(...)`，
只转换I/O为NPZ/PNG，重用其官方 `utils3d.np.depth_map_edge`。
输入RGB；输出 points(H,W,3)、depth(H,W)、mask(H,W)、normal(H,W,3)、归一化K(3,3)。
点坐标是 OpenCV X右/Y下/Z前，depth为相机Z；度量尺度来自模型估计，不是测量真值。
没有区域语义推断：`moge_validity.png` 仅为几何有效性，`ground_manual.png` 来源为显式人工多边形。

**许可。** MoGe `LICENSE` 首段为Microsoft MIT，随后包含Apache-2.0第三方许可；
DINOv2等组件保留各自声明。该本地checkpoint的model card为MIT（`~/models/moge/README.md`）。
本项目只引用外部代码和权重，没有复制模型源码或权重进入OutdoorGraph，数据样例和下游
权重仍保留原始来源，不能把所有外部模型统一标注成OutdoorGraph许可。

## UrbanWorld2.0

**代码位置与版本。** 外层 `~/outdoorsmodel/UrbanWorld2.0` 不是 Git 仓库；实际仓库为 `~/outdoorsmodel/UrbanWorld2.0/outdoor`（下文 U）。HEAD 为 `110dcfc80533c487a6f845ede966ae4988183a2b`，提交日期 2026-09-07，标题 `feat(evaluation): add scene and building evaluation tools`。`git status --porcelain -uall`：21 个已跟踪文件修改，22 个未跟踪文件。修改涉及 CLI、配置、VLM、OSM、街景、mesh、Blender 组装；未跟踪项主要是复现/下载/统计脚本、Hunyuan 补丁和验证脚本，不能将这些能力归于干净 HEAD。嵌套 Hunyuan 仓库 HEAD 为 `82920d643c0dc2f7bfd7255f45f62d386edfe60c`。

**本机环境实测。**

| 项目 | 实际路径与版本 |
|---|---|
| Python | `U/.venv/bin/python` 可直接执行，3.11.16；包声明 Python ≥3.10，见 `U/pyproject.toml:7` |
| 已安装包元数据 | urbanworld 0.1.0；torch 2.5.1+cu121、torchvision 0.20.1+cu121、numpy 1.24.4、Pillow 11.1.0、diffusers 0.30.0、transformers 4.46.0、trimesh 4.4.7、openai 3.36.0、click 8.5.0 |
| Blender | `U/vendor/blender-3.6.23-linux-x64/blender --version` 实测 3.6.23；环境内没有独立 bpy 包，实际通过 Blender 子进程运行 |
| CUDA | torch.version.cuda 为 12.1；`/usr/local/cuda-12.2/bin/nvcc` 实测 V12.2.140。受限子进程中的 nvidia-smi 无法访问驱动；主 Agent 在授权主机调用中另行确认 8×A100 40GB，本记录不据沙箱结果断言服务器 CUDA 不可用 |
| 本地依赖/权重 | `U/vendor/Hunyuan3D-2.1`、`U/vendor/blosm.zip`、`U/.blender_config/scripts/addons/blosm`；`U/models/Hunyuan3D-2.1/{hunyuan3d-dit-v2-1,hunyuan3d-paintpbr-v2-1}`、OWL-ViT、dinov2、rembg 目录存在；本轮未验证每个权重完整性。该 Python/Blender 路线不依赖 Node |

**当前迁移断点。** `U/.env` 的 Blender、blosm、Hunyuan、OWL-ViT 等路径仍为 `~/outdoor/UrbanWorld2.0/...`，这些旧目标不存在。`U/.venv/bin/activate:38` 的 VIRTUAL_ENV、`bin/urbanworld:1` 和 `bin/pip:1` 的解释器前缀也仍为旧目录。更直接地，`U/.venv/lib/python3.11/site-packages/_editable_impl_urbanworld.pth:1` 指向不存在的旧 `src`，本轮该 Python 的 `importlib.util.find_spec('urbanworld')` 返回 `None`。因此“Python 可执行/包元数据存在”不等于“当前安装可导入/CLI 可用”。本轮仅记录，未修原仓库。

**已有成功产物与边界。** `~/outdoorsmodel/UrbanWorld2.0/outputs`（下文 UO）已有清华 18 栋街区与单体产物：

- `UO/tsinghua_scene/scene.glb` 为 43,532,584 字节。本轮独立解析 GLB：magic=glTF、version=2、声明总长与文件大小一致，21 个 mesh、24 个 material、36 张 image，节点含 18 个 HighPrecision 建筑及森林/道路/植被节点。`scene.blend` 为 116,505,934 字节，头部为 `BLENDER-v306`。另有 scene.gltf/bin、preview/top/street 图片及 18 套 building.glb/imagination/provenance/meta_info。
- `UO/validation.json` 的原验证为 passed：18/18 替换，18 个 PBR 材质，中心/尺寸最大误差均为约 7.63e-6 m；`block_geometry_stats.json` 为 inspected=18、valid=18、720,000 faces、无记录的 generation/inspection failure。该 JSON 未由本轮重新运行验证器生成。
- `UO/run_config.json` 与 `reproduction.txt` 记录街景→OWL-ViT→VLM→Gemini 想象→Hunyuan 几何/PBR→组装完成；但只有 4 栋有直接观测、14 栋为上下文补全；18 栋均采用默认 9 m 高度，没有 GABLE 高度。这是现实对齐精度的明确限制。
- README 的环境扩展存在真实代码（道路 .pb、地块 .gpkg、NDVI 树点），但本轮未在 `U/runs` 找到对应 .pb/.gpkg/.tif、trees.json、road_network.blend 或 parcels.blend。现有 OSM 背景节点不构成完整 env 扩展跑通证据。未发现本轮可引用的全测试通过数量。

**可复用输入/输出接口。**

| 入口 | 输入 → 输出；复用边界 |
|---|---|
| `urbanworld osm download-buildings --bbox ... --output ... --osm-cache ...`；`U/src/urbanworld/stage0_osm/download_building.py:14` 的 `run(bbox, obj_save_path, osm_save_path, **config_overrides)` | bbox 顺序为 min_lat,min_lon,max_lat,max_lon → OSM 缓存、每栋 OBJ/MTL 与 meta_info.json；联网 OSM 与 Blender/blosm 入口，不能视作纯离线操作 |
| `U/src/urbanworld/stage4_mesh/generate.py:71` 的 `run(obj_save_path, seed=42, steps=30, octree_resolution=256, shape_only=False, device='cuda', **config_overrides)` | 建筑目录中的想象图片 → gen_mesh_plus 下 OBJ/GLB、PBR 图与 textured GLB；返回目录列表；CPU 只允许 shape_only，完整贴图要求 CUDA |
| `urbanworld assemble organize --obj-save-path ... --output ...`；`U/src/urbanworld/stage5_assembly/organize.py:9` 的 `run(...)` | 建筑/背景目录 → GLTF/GLB/Blender 场景；可接 road_blend、parcel_blend、tree_json、tree_mesh；未传 tree_mesh 时树为 Empty 占位，不是可渲染树 mesh |
| `U/scripts/reproduce_block.py:25` / `UO/reproduce_block.sh` | 固定清华街区的 stage=streetview/crop/structure/assessment/imagine/mesh/assemble/all；不是通用 Scene IR 接口。`repro_runtime.configure()` 会读取用户 bashrc 的凭据并按配置改进程 DNS；`assemble` 还会调用余额接口，不能作为“纯离线组装入口”复用 |

适合 OutdoorGraph v0.2 的初始用途：以独立进程或现有 GLB 资产接入，记录 OSM id、经纬度、默认高度、来源和局部变换。现有代码没有 OutdoorGraph 的统一节点/约束 IR；不应整库合并，也不能把 GLB 导入视作依赖传播已实现。

**许可证据与限制。** `U/pyproject.toml:6` 和 `U/README.md:223` 声明主项目 MIT，但未见主项目独立 MIT LICENSE 正文（Git 跟踪的 LICENSE 只包含 blosm）。Hunyuan 适用单独社区许可：`U/vendor/Hunyuan3D-2.1/LICENSE` 第 3/18 行明确许可不适用于欧盟、英国、韩国；授权地域限定为这些地区之外，第 25–28 行要求下游协议/改动声明/Notice，第 32 行对发布时超过 100 万 MAU 的主体要求另行授权，第 39 行禁止用模型或输出改进其他 AI 模型；shape/paint 各自 LICENSE 也保留这些条款。不能把 Hunyuan 权重/输出统一标成 MIT。blosm 的 `U/.blender_config/scripts/addons/blosm/LICENSE:1` 为 GPLv3；Blender 的 `U/vendor/blender-3.6.23-linux-x64/copyright.txt` 为 GPLv2-or-later，具体组件另有许可。OSM、街景、VLM 输出和其他权重的独立权利在本地材料中未全部确认；主项目 MIT 声明不足以证明这些数据都可再分发。

## RecursiveCWM_code

**代码位置与版本。** 仓库 `~/outdoorsmodel/RecursiveCWM_code`（下文 R），HEAD `3ecb133bc51d663ca296f2c653b70fa51cb02564`，2026-09-29，`docs: make feedback invitation more welcoming`。`git status --porcelain -uall`：16 个已跟踪文件修改，85 个未跟踪文件；主要涉及 runner、检查/渲染/计分、离线测试、文档及新本地续跑/导出/资源工具和 outputs/work，存在原有工作，不应覆盖。

**本机环境实测。**

| 项目 | 实际路径与版本 |
|---|---|
| Python | `R/runtime/.venv/bin/python` 可直接执行，3.12.15；该目录实际为带 conda-meta 的 Conda prefix，无 bin/activate |
| Python 包 | torch 2.5.1+cu121、torchvision 0.20.1+cu121、numpy 2.5.2、Pillow 12.3.0、open_clip_torch 3.3.0、lpips 0.1.4、pytest 9.1.1；torch.version.cuda=12.1 |
| Node/runtime | `R/runtime/.render-tools/node/bin/node --version` 为 v22.14.0；本地 package.json 实测 three 0.160.1、Playwright 1.62.1、esbuild 0.25.11、@openai/codex 0.154.0；Playwright 是 Node 包，不是 Python 包 |
| Blender / 渲染 | RCWM 主路线不需要 Blender。`outputs/render-verification.json` 记录 Chromium/SwiftShader CPU 渲染；GPU 只用于离线指标，见 `outputs/gpu-evaluation.json` 与 `environment.json`，本轮未重跑 |

`R/runtime/.venv/bin/pip:2` 的 shell exec 仍指向不存在的 `~/outdoor/RecursiveCWM_code/...`。历史 migration.json 和复现说明也记录旧目录，不能证明此后再次搬迁已完全修复。直接显式 Python 路径可用，后续应优先用 `python -m ...` 并另行修正失效启动入口；本轮不修改。

**已有结果与真实完成方式。** `R/outputs/police-corner-3d.html`（709,523 字节）和 code-and-scene.zip（14,409,260 字节）、对照图、四向/新视角、指标文件均实际存在。`R/runtime/runs/police-corner-subagents/fractal` 实际有 9 个 part.json；根 part.json 给出 interface_version=1、module=component.js、export=build、children=[police-station,tower-pair,homes]。本轮未重新启动浏览器或执行场景代码。

- `outputs/local-completion.json` 明确原 API 因额度限制中断，`original_api_run_completed=false`。`outputs/subagent-run.json` 记录由本地 Codex collaboration 的 8 个节点 Agent 加主调度续跑完成，depth=2（1/3/5），不是 README 默认 depth=4 的原 API 全程复现；没有新增中转站 API 调用。
- 历史 `outputs/render-verification.json` 记录根 1,054 meshes、24,234 vertices，9 个模块均构造为 Group、invalid_vertices=0、无 page error/占位回退。`viewer-verification.json` 记录离线打开、拖动可旋转、无外网请求。它的 triangle 数与 render-verification 不同，属于两个验证口径，未在本轮统一。
- `outputs/interface-verification.json` 记录裁剪图、view、manifest 与相机保持一致；其契约是文件与函数接口，不能解释为 API 对话重放。`outputs/migration.json` 曾验证旧迁移后像素一致，但路径现已过时。本轮未核对 README 所列整套论文场景/所有测试。

**可复用输入/输出接口。**

| 入口 | 输入 → 输出；复用边界 |
|---|---|
| `R/rcwm.sh reference.png run-name [max-depth=4] [max-cycles=3]` | 一张参考图 → runtime/runs/<name>/，含 conditions、camera-contract、trace 与递归 fractal 节点；会启动 Codex 推理，需要用户账户/模型访问，不能在离线验证时调用 |
| `R/runtime/runs/police-corner-subagents/fractal/scene/component.js:6` | `export async function build(THREE, context={})` → THREE.Group；根导入三个子模块，能作为层次化程序资产参考。不是稳定的 JSON Scene IR，也无通用外部参数 schema |
| `R/runner/check_part.py` 与 `R/docs/code-structure.md:136` | part.json 接收 module/entry/main/file 或 components；children 引用节点/路径。检查现存文件与结构，不执行 build、不判视觉质量、也不独立验证渲染；runner 的 visual_stop 不是独立视觉验收 |
| `R/tools/export_scene.py:15` 的 `export_scene(run,runtime,workspace,output=None)` | 完成的 run（必须含 camera-contract.json、scene/viewer.js 与相应模块）→ 单文件离线 HTML；本地 esbuild 打包。该工具带 police-corner 的标题/UI/源码替换假设，需要适配新 scene；不是任意 RCWM 结果的通用导出器 |
| `tools/view.sh` / `tools/render_views.sh` / `tools/score_run.py` | 已有 run → 浏览器查看、静态多视角、记录 PSNR/SSIM/edge/LPIPS/CLIP。首次指标可能下载权重，离线时应显式指缓存。`tools/run_local.py` 会加载凭据、启动 relay 并读余额，不能当离线入口 |

适合 OutdoorGraph v0.2 的初始用途：对既有 JS 层次程序、part.json 和相机契约建立外部适配，保留来源与运行方式；独立进程渲染/资产导出。尚无 glTF/mesh 形式的通用几何输出、统一依赖约束或可验证程序参数协议，不宜直接作为 OutdoorGraph 约束执行器。

**许可证据与限制。** `R/LICENSE:1` 为 MIT，分发软件/重要部分需保留版权和许可；本地 Three.js 的 `R/runtime/.render-tools/node_modules/three/LICENSE` 同为 MIT。`R/experiments/pilot-scenes/REFERENCES.md:4` 将 city-full 及警局等裁剪标为 JanaChumi 的 CC0 Isometric city 来源；第 9–13 行另将 WorldClaw 图作为论文图输入，四张图不再分发，没有给出对这些论文图片的一般再许可。因此 MIT 代码、CC0 城市输入、第三方论文图/指标权重应分别记录，不能将整套 runtime/图片自动归为 MIT。模型服务与 Codex 访问是外部运行条件，不是软件许可授予的 API 权限。

## fal-worldclaw：可重用离线地形、摆放及预览；远程生成须显式启用

- **位置/版本**：`~/outdoorsmodel/fal-worldclaw`，Git HEAD `3843e4f85ac80ee31b7b6b3824231f27c3a878a6`。当前 dirty：8 个 tracked 修改（`.env.example`、`.gitignore`、`README.md`、`src/app/page.tsx`、`src/components/ui/PromptPanel.tsx`、`src/lib/{director,fal,prompts}.ts`）；另有 `LOCAL_SETUP.md`、`run.py`、`scripts/balance.ts`、`src/app/api/agent/`、`src/lib/{client-config,relay-server}.ts` 未跟踪。这是含本地中转适配的工作副本，不能仅用 HEAD 表示其完整行为。依据：实测 `git rev-parse HEAD`、`git status --short`、`git diff --stat`。
- **环境实测**：`.runtime/node/bin/node` 为 **v22.23.4**，同目录 npm **10.9.9**；现存 `node_modules/{next,react,three,typescript}/package.json` 分别为 **16.3.0 / 19.2.8 / 0.185.1 / 5.9.3**。程序化构建是 TypeScript/CPU 路径，不需要 Python、Blender 或 CUDA；页面渲染是浏览器 WebGL。`run.py:14` 起设置项目 Node PATH；默认绑定 `127.0.0.1:3777`。它会读取现有 `.bashrc` 的特定凭据，因此盘点没有执行启动器、生成入口或余额接口，也没有显示凭据。`.runtime/browsers/` 和 `.next/BUILD_ID` 存在，仅证明浏览器/构建产物留存，不等同本次完整浏览器复验。
- **已运行证据（历史留存，非本次调用远程 API）**：`outputs/verification.json` 记录 Sunmere、6 区域、10 原型、580 实例，3 次 agent HTTP 200，其中视觉请求含 3 图，浏览器错误为零，截图渲染器是 SwiftShader；`outputs/screenshots/` 留有四通道及生成效果图。本次独立解析 `outputs/alpine-village.worldclaw.json` 确认其键为 `spec / instances / artifacts`、580 实例、10 资产，**10/10 资产都是 `procedural: true`**。这能支持 LLM 规划/视觉精修与程序化资产链路的历史成功，不能支持 Patina/FLUX/SAM3D/Hunyuan 网格链路已成功。后者仅在 `README.md`、`LOCAL_SETUP.md:16` 与 `src/lib/fal.ts` 声明/实现；本次未验证、未计费调用。`LOCAL_SETUP.md:32` 给出了 smoke 命令；文档声称 smoke/build 通过，盘点未重跑。
- **实际可重用接口**：`src/lib/types.ts` 定义 JSON `WorldSpec`（seed、size 米、regions/rivers/assets/relations）；UV `[0,1]^2` 对应 `+x` 东、`+z` 南，实例坐标 **Y-up**。`src/lib/terrain.ts:137` 的 `buildTerrain(spec)` 输出默认 256×256 `Float32Array heights`、region weights、`heightAt/normalAt/slopeAt`；`src/lib/placement.ts:27` 的 `solvePlacements(spec, terrain)` 输出带 id/protoId/position/rotation/scale 的实例；`src/lib/procedural.ts:269` 的 `buildProceduralGeometry(hint, seed, ...)` 输出 Three.js `BufferGeometry`。这些函数可由独立 Node subprocess 适配，不必移植到 OutdoorGraph。`scripts/smoke.ts` 已组合演示上述离线路径。
- **导出边界**：`src/components/ui/Dock.tsx:38` 导出 `{spec, instances, artifacts}` 为 `.worldclaw.json`；不是完整世界 GLB 导出器。现有 asset `meshUrl` 只在远程模型成功时存在。`src/lib/director.ts:57` / `:308` 是浏览器端 `generateWorld` / `runRefinement`，受 Zustand、截图回调和远程 API 影响，不宜直接当无副作用 CLI。`src/app/api/agent/route.ts` 与 `src/app/api/fal/proxy/route.ts` 是远程调用入口，默认适配应保持关闭。
- **许可/接入计划**：根目录及 Git 跟踪文件未发现项目级 LICENSE/COPYING，`package.json` 没有 `license` 字段；不要将依赖的开源许可推定为该项目源码许可，也不要直接复制源码。优先保存来源与版本、通过外部进程消费 JSON/调用已有离线函数，任何源码或生成模型内容的分发另核对授权。Y-up → OutdoorGraph Z-up 的轴变换与原点/南北方向必须显式记录；保留原生 UV/seed 防止伪装成图像恢复证据。

## BuildRome：已有真实单图到公制网格结果；迁移后独立环境入口损坏

- **位置/版本**：`~/outdoorsmodel/BuildRome`，HEAD `bf23d0244ceb53ce9fb6f64d309b5180fa4ef092`。dirty：tracked `.gitignore`、`inference/models.py`（支持 `BUILDROME_DINO_PATH`），整个 `local/` 未跟踪。`local/buildrome-local.patch` 保存适配补丁；勿覆盖现有工作。依据：Git 实测、`inference/models.py`、`local/reproduction-report.txt`。
- **环境：历史安装与当前可执行性分开**。历史日志与现存 `.dist-info`：Rome **Python 3.10.22 / torch 2.6.0+cu124 / torchvision 0.21.0+cu124 / transformers 5.12.1 / flash-attn 2.7.3 / flex-gemm 1.0.0**；MoGe3 **Python 3.10.22 / torch 2.8.0+cu128 / torchvision 0.23.0+cu128 / moge 3.0.0**。现 `.envs/{rome,moge3}/bin/python` 均为指向已不存在的 `~/outdoor/BuildRome/.envs/python/cpython-3.10-linux-x86_64-gnu/bin/python3.10` 的绝对软链接，且该 Python 别名本身仍指旧路径，执行均报 `No such file or directory`。实际二进制 `.envs/python/cpython-3.10.22-linux-x86_64-gnu/bin/python3.10 --version` 可运行并报 **3.10.22**，但不能据此声称两个环境已恢复。盘点未修第三方软链接。
- **CUDA/Blender**：本项目推理依赖 CUDA 扩展；`local/install_{rome,extensions}.sh` 指定 `/usr/local/cuda-12.2`，本次 `/usr/local/cuda-12.2/bin/nvcc --version` 实测 **12.2.140**（编译工具版本不等于 torch wheel CUDA runtime）。`local/logs/smoke_cuda.log` 历史记录 A100 40GB、torch/cu124 和 7/7 CUDA检查；`local/logs/moge3-cuda-import-smoke.log` 记录 MoGe3/cu128 的矩阵乘通过。当前普通 sandbox `nvidia-smi` 无法访问驱动，是受限执行现象；root 已在提权读查询确认 **8×A100 40GB**。源码推理与视频渲染使用原生渲染扩展，不要求 Blender/Node；`tools/render_glb_flythrough.py` 是另一个渲染工具。
- **已运行证据（历史留存 + 本次轻量核对）**：`local/results/03_Traffic/{run,validation}.json`、`local/logs/scene.log`、`local/logs/moge3-inference.log` 记录官方示例 `03_Traffic.jpg` 的 MoGe3 + 全部 **6 bands** 成功，**45,690,642 顶点 / 93,970,297 三角面**、有限坐标、无纹理公制 GLB，GPU峰值采样 **27,529 MiB**，主场景 **1194.07s**、MoGe3 **32.4s**。现存 `scene.glb` **1,675,935,460 bytes**；本次读取 GLB header 确认 magic `glTF`、版本 2、声明长度与文件大小一致。`layout_bands.json`、`layout_topview.png`、`spiral.mp4` 均存在；视频的120帧/30fps/4s/2054×1024来自历史 `validation.json`，本次未解码重验巨型网格或视频。历史 `run.json` 仍含迁移前绝对路径；不得直接作为现位置可复跑证明。`local/example_depth.npz` 现存且143,571 bytes，`local/logs/moge3-pack-validation.json` 记录512×512公制depth、K、cam_to_yaw、camera_height与floor状态。
- **入口/输入输出**：现有 `local/run.py:10` 以文件位置确定 ROOT，收 `--image /abs/image --out /abs/out --gpu N --depth-pack /abs/depth.npz` 并透传原 CLI 参数；它检查空闲 GPU，先运行 MoGe3 pack 再运行 Rome，默认 `--max-inflated-voxels 30000` 适配40GB卡（历史100000分组 OOM），不降低体素分辨率。环境坏链未修前不能直接运行。原 CLI `inference/image_to_scene.py:47` 收 image、SS/SLat delta checkpoints、seed、depth pack 和 band 参数；输出 `<out>/<stem>/{scene.glb,layout_bands.json,layout_topview.png,spiral.mp4}`。`build_rome.ScenePipeline.from_pretrained(...)` / `pipe(image, depth_pack=...)` / `SceneOutput.save(...)` 见 `inference/pipeline.py:317,348,397,428`，模型可加载一次处理多图；直接 API 可能触发权重获取，离线模式应传本地权重与pack。
- **坐标与pack契约**：`inference/image_to_scene.py:11` 输出 **公制 glTF Y-up，camera origin、视线 -Z**；内部 floor frame 为 x右/y前/z上。`build_rome/modules/lifting.py:15` 精确定义 camera(OpenCV x右/y下/z前)、floor、NPZ：`depth/conf[H,W]`、`sky`、`K[3,3]`、`cam_to_yaw[4,4]`、`camera_height`、`floor_ok`、`floor_inlier_frac`、`version/model`。只将它作为几何先验/artifact，不能凭整体mesh自动宣称恢复了可编辑语义 Scene IR 或生成依赖。
- **许可证/重用决策**：根 `LICENSE`、`README.md:178`、`NOTICE` 明确项目原始代码/项目修改和 Rome delta 为 **CC BY-NC 4.0**；保留署名及修改说明，商业使用这些贡献需另获授权。TRELLIS派生部分保留MIT，`o-voxel/LICENSE`为MIT；DINOv3、MoGe、TRELLIS权重分别保留自身条款。`NOTICE:75` 特别要求 DINOv3 条款/准入；下载镜像不替代许可。`NOTICE:91` 说明 pinned nvdiffrast v0.4.0 的 NVIDIA 条款限定研究/评估，optional renderutils亦有限制。集成仅外部调用与清单引用，不将此仓库/权重合并进 OutdoorGraph；优先复用已有 `example_depth.npz` 与结果作adapter fixture，完整后端运行列为可选，保持独立环境与GPU资源调度。


## Infinigen：现有自然生成与执行后端

Inspected `~/outdoorsmodel/infinigen` read-only. No `AGENTS.md` was found in the repository or its ancestor directories. The third-party source, environment, prior results and launchers were not edited. `git status --short` already shows the documented pre-existing `infinigen/core/rendering/render.py` change.

## Source, executables and environment

- Source: `~/outdoorsmodel/infinigen/source`
- Infinigen: **1.3.3**, commit `f5bcba8de47623da9b715348cf95822445e0e9a7` (detached checkout; installer requests `nature-stable`).
- Blender: `~/outdoorsmodel/infinigen/blender/blender`, **3.6.0**, build `c7fc78b81ecb`, bundled Python **3.10.12**.
- Existing dependency environment: `~/outdoorsmodel/infinigen/.venv`, Conda Python **3.10.22**.
- Actual key installed versions: numpy 1.26.2, scipy 1.15.3, gin-config 0.5.0, landlab 2.7.0, scikit-image 0.19.3, trimesh 5.1.1, setuptools 80.10.2. Full prior lock is `requirements.lock.txt` in the outer project.
- Source metadata explicitly requires `==3.10.*` and `bpy==3.6.0`. Use an external Blender process; do not import this implementation into OutdoorGraph's Python 3.11 environment.
- A second Blender distribution exists under `work/blender-3.6.23`; the existing successful launcher uses the **3.6.0** executable above.

## Existing successful pipeline and outputs

`run.sh` computes paths relative to itself, exports its Conda site-packages plus the source via `PYTHONPATH`, then launches `infinigen_examples/generate_nature.py` with `desert.gin simple.gin`. Its stages are coarse, populate+fine_terrain, RGB render, then ground-truth render. It selects available GPUs through `nvidia-smi`; do not reuse that launcher when forcing CPU or when avoiding writes into third-party outputs.

Existing `~/outdoorsmodel/infinigen/outputs/desert_seed0/validation.json` records passed validation:

- RGB `frames/Image/camera_0/Image_0_0_0001_0.png`, 960×540.
- Depth float32, 540×960, finite valid fraction 0.66044 and reported valid range 13.397–586.303 m.
- Surface normals, segmentation, flow, camera K/T/HW NPZ files.
- `coarse/scene.blend`, `fine/scene.blend` (the latter is about 502 MiB), four logs under `logs/`.
- Prior pipeline times: coarse 147.841 s, fine 95.202 s, RGB 11.763 s, GT 15.570 s. These are prior GPU-run records, not new performance measurements.

The historical `outputs/reproduction.txt` still contains old `~/outdoor/infinigen` paths. The actual migrated location and working launcher are under `~/outdoorsmodel/infinigen`.

## Minimal tested natural-generation API

The following functions are **existing third-party algorithms**, called without copying their implementations:

```python
from infinigen.assets.grassland.grass_tuft import GrassTuftFactory
from infinigen.core.placement.factory import make_asset_collection
from infinigen.core.placement.instance_scatter import scatter_instances
from infinigen.core.util.math import FixedSeed

with FixedSeed(2026):
    factory = GrassTuftFactory(2026)
    assets = make_asset_collection([factory], n=2, verbose=False)
    scatter = scatter_instances(
        base_obj=terrain_mesh, collection=assets,
        density=12.0, scale=2.0, scale_rand=0.2, scale_rand_axi=0.1,
        normal_fac=0.2, ground_offset=0,
        fov=None, dist_max=None,
    )
```

`GrassTuftFactory` creates 30–59 curved blades per tuft using its existing stochastic blade geometry. `make_asset_collection` invokes deterministic `spawn_asset` and the factory's `finalize_assets`, which adds the native `grass_blade_texture` material. It hides the source collection; the Geometry Nodes scatter instances it. `density` is instances per unit surface area. Supply exactly one of `density` and `vol_density`; direct density is easier to bound. `scale` is required by the existing scatter wrapper. `fov=None, dist_max=None` avoids camera culling and its camera-info node dependency.

`infinigen.assets.scatters.grass.apply(mesh)` is the convenient full wrapper, but hardcodes generation of ten assets and randomized volume density. For a small controlled adapter, use the factory and scatter directly.

For a strict manual image-mask region, compute a FACE-domain boolean named attribute on the reconstructed mesh. Allow a triangle only when all its vertices have `region_weight=1`. Pass a callable returning a `Nodes.NamedAttribute` BOOLEAN field as `selection` to the **existing** `scatter_instances` API. This conservatively excludes mixed boundary triangles and leaves point distribution entirely to Infinigen/Blender.

Native evaluated Geometry Nodes instance names need not match source object names. The existing `infinigen.assets.utils.misc.CountInstance` counts `inst.is_instance` in the evaluated dependency graph. In an isolated adapter scene, the same strategy yields actual instance counts and `inst.matrix_world` yields generated locations.

## CPU initialization and verified probe

Full Gin parsing is unnecessary for the grass factory/scatter path. Explicit initialization works:

```python
from infinigen.core import init
init.configure_render_cycles(
    min_samples=0, num_samples=8, time_limit=0,
    adaptive_threshold=0.1, exposure=1.0, denoise=False,
)
init.configure_cycles_devices(use_gpu=False)
```

The two calls avoid the default GPU enumeration. Do not call `configure_blender()` without Gin bindings: `configure_render_cycles` normally requires its six render parameters from configuration and `configure_cycles_devices` defaults to `use_gpu=True`.

Executable probe: `work/v02/infinigen_probe.py` in the OutdoorGraph task workspace. It uses a 2 m support-plane fixture or an optional OBJ; the fixture is explicitly not a terrain-generation substitute. A new CPU run completed successfully with two original grass assets, **2060 source vertices**, **48 native scatter instances**, a `.blend`, and a **320×240 PNG**. Evidence: `work/v02/infinigen_probe.log` and `work/v02/probe-output/probe_manifest.json`.

Launch from the OutdoorGraph task workspace:

```bash
env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=~/outdoorsmodel/infinigen/.venv/lib/python3.10/site-packages:~/outdoorsmodel/infinigen/source \
  BLENDER_USER_RESOURCES="$PWD/work/v02/probe-cache/blender" \
  XDG_CACHE_HOME="$PWD/work/v02/probe-cache/cache" \
  MPLCONFIGDIR="$PWD/work/v02/probe-cache/matplotlib" \
  CUDA_CACHE_PATH="$PWD/work/v02/probe-cache/cuda" \
  OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
  ~/outdoorsmodel/infinigen/blender/blender \
  --background -noaudio --threads 4 --python-use-system-env --python-exit-code 1 \
  --python "$PWD/work/v02/infinigen_probe.py" -- \
  --out "$PWD/work/v02/probe-output"
```

All new caches and outputs stay in this workspace; bytecode writes to the source/environment are disabled. The probe logged harmless NumPy subnormal warnings, `pkg_resources` deprecation, and an unavailable desktop GVFS directory.

## Implemented external bundle worker

`work/v02/project/src/outdoorgraph/adapters/blender_worker.py` accepts `--bundle`, `--out`, and `--infinigen-source`, without importing OutdoorGraph. It consumes the reconstructed NPZ mesh and reference image, uses native Infinigen grass and scatter with a FACE-domain manual-mask selector, maps the calibrated CV camera to Blender, and saves `scene.blend`, `scene.glb`, `render.png`, `overview.png`, and `objects.json`. Native Blender `Realize Instances` is used only for exporting the existing generated scatter geometry into GLB.

A separate 2 m fixture test in `work/v02/worker-fixture` completed successfully:

- Four of eight mesh triangles allowed by the mask; **24** native scatter instances.
- All instance centers stayed in the selected half-plane (maximum x = -0.0537056 m), ground error 0 m.
- Deliberately unequal fx/fy and shifted principal point: camera projection error **6.68e-05 px**.
- Two 640×480 CPU renders, valid Blender file, valid GLB 2 header/length, **1,594,740 bytes**.
- Reloading the `.blend` confirmed the complete `OutdoorGraph.SceneBundle.json` text datablock, one packed texture image, scene provenance and source mesh/scatter/asset provenance properties.
- Parsing the GLB JSON confirmed scene and mesh-node extras retain the same IDs, configuration and provenance.

Blender-specific details verified while implementing the worker: render pixel-aspect components are clamped to at least 1, so normalize the pair when fx != fy. Geometry Nodes Named Attribute has multiple identically named output sockets; return `(node, "Attribute")` so Infinigen's NodeWrangler selects the enabled BOOLEAN output. Instance IDs describe stable snapshots for an identical seed/configuration; parameter changes do not promise one-to-one instance identity.

## Larger existing tree and terrain APIs (inspected, not newly executed)

- Trees: `infinigen.assets.trees.generate.TreeFactory(seed, season="summer", fruit_chance=0.0)` followed by `make_asset_collection` or `factory.spawn_asset`. This is the existing recursive/space-colonization tree machinery. It requires `surface.registry('bark')`, so import required materials and load `base_surface_registry.gin` or the full base configuration, then call `surface.registry.initialize_from_gin()`. Constructor generates twig/leaf collections and is substantially heavier than grass. Do not claim this API was verified by the small probe.
- Terrain: `infinigen.terrain.Terrain(seed, surface.registry, task="coarse", asset_folder="", asset_version="May27", on_the_fly_asset_folder=<workspace_dir>, device="cpu")`, then `coarse_terrain()`. Terrain also requires the existing scene-type Gin configuration and initialized surface registry. Existing native CPU terrain object files are present under `infinigen/terrain/lib/cpu/`. The official `generate_nature.py` pipeline already initializes these correctly. Full terrain generation is unnecessary when the geometry comes from MoGe; use the imported reconstructed surface as `base_obj`.

## Local license evidence

- `~/outdoorsmodel/infinigen/source/LICENSE`: **BSD 3-Clause**, Princeton University copyright 2023. Grass factory, scatter and factory modules include matching BSD notices.
- Blender `~/outdoorsmodel/infinigen/blender/copyright.txt` identifies Blender's **GNU GPL** distribution; detailed texts and third-party licenses are in its `license/` directory.
- Infinigen includes separately licensed GPL/submodule components (`infinigen/infinigen_gpl`, terrain/other vendor submodules). Keep the external installation separate and retain applicable upstream notices if redistributing source or binaries. This inventory does not copy those sources into OutdoorGraph.
