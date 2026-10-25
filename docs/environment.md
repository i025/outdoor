# 环境安装与复现

OutdoorGraph 主环境只安装核心包和测试工具。MoGe、PyTorch、Infinigen、Blender
分别保留在外部环境；不要把它们加入 `environment.yml` 或改动系统 Python。
核心 `Scene` schema 为 0.1，外部交换 `SceneBundle` schema 为 0.2。

## 核心环境与测试

在新的项目 checkout 中，用已有 Conda/Miniforge 创建独立前缀；不需要 sudo。
若 `.venv` 已存在，直接激活它，不重新创建或覆盖。

```bash
cd ~/outdoor
conda env create --prefix ./.venv --file environment.yml
conda activate ./.venv
# environment.yml 不自动安装本地源码；在源码目录手动安装。
python -m pip install -e '.[dev]'
python -m pip check
python -m pytest -q
outdoorgraph demo --scene configs/demo_valley.json --out runs/demo_valley
```

如果已有独立 Python 3.11，也可在一个尚无 `.venv` 的 checkout 中使用：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

两个方法任选其一。后续会话只需激活现有环境。
本次核心验证使用 Python **3.11.16**、pytest **9.1.1**，共 **110 项 pytest 通过**。
默认 pytest 不加载 NumPy、模型、GPU 或 Blender。
额外的 5 项几何数值单测在已有 MoGe 环境中显式运行：

```bash
/absolute/path/to/moge-env/bin/python tests/external_geometry_checks.py
```

这些数值单测使用小型人工数组验证三角化和索引转换，不能代替真实模型与渲染验收。
完整运行证据和能力边界见 [Adapter 接口](adapter_interface.md)。

## MoGe v2：独立安装与已验证组合

本次实际调用的是官方源码中 **v2** 的 `MoGeModel`，不是 MoGe-3 模型：

- MoGe 源码 commit：`74fbce054ebed49800de42d0ad0e83495065719a`。
- checkpoint：`Ruicheng/moge-2-vits-normal`，配置中填写已下载的本地 `model.pt`。
- 实际可运行的外部环境：Python **3.13.15**、PyTorch **2.13.0+cu126**、
  NumPy **2.5.2**、Pillow **12.3.0**、OpenCV **5.0.0.93**。
- 该 checkout 的 `pyproject.toml` 声明 Python **>=3.10**、torch **>=2.4**、
  torchvision **>=0.19**、NumPy **>=2**。Python 3.13 与 cu126 是本机验证组合，
  不是上游要求所有用户采用的版本。PyTorch CUDA wheel runtime 与 `nvcc` 版本不同。

优先复用已经可运行的外部 MoGe 环境，只在 local config 填入其解释器和源码路径。
新机器可按下列顺序准备独立安装，具体依赖仍以固定源码的
[安装说明](https://github.com/microsoft/MoGe/blob/74fbce054ebed49800de42d0ad0e83495065719a/README.md)
与 `pyproject.toml` 为准：

```bash
git clone https://github.com/microsoft/MoGe.git ~/src/MoGe
git -C ~/src/MoGe checkout 74fbce054ebed49800de42d0ad0e83495065719a
git clone https://github.com/EasternJournalist/utils3d-moge.git ~/src/utils3d-moge
git -C ~/src/utils3d-moge checkout 62f09d58509485564e24d5d9f6aac9ee9ebc0c37
conda create --name outdoorgraph-moge python=3.13 pip -y
conda activate outdoorgraph-moge
# 先按 GPU 驱动安装兼容的 torch/torchvision；不要照搬另一台机器的 CUDA wheel。
# 然后安装这个外部 checkout 的依赖，始终不进入 OutdoorGraph 核心环境。
python -m pip install -e ~/src/utils3d-moge
python -m pip install -e ~/src/MoGe
```

上述是上游安装路线，**本次没有在全新机器重新执行整套安装**。
这个较新的 MoGe checkout 同时包含 v1/v2/v3，完整安装会引入 v3 所需的额外依赖；
已有 v2 环境不必为本项目升级成完整 v3 环境。本次只通过显式源码路径导入已安装的依赖。
若选择 `uv sync`，该 checkout 默认配置 CUDA 13.0 索引；pip 不读取该 uv 索引配置，
应按自己的驱动明确选择 PyTorch 构建，不能将此默认值混同于本次 cu126 运行。

按模型发布方说明取得 checkpoint 后，将其放在项目外或被忽略的 `weights/` 中；
不把权重、缓存、凭据加入 Git。这里不运行任何自动模型下载。
本机官方 EXR 导出 CLI 在推理后因 OpenCV 无 EXR writer 失败，因此 Adapter
调用相同的公开 v2 推理 API，将数组保存为 NPZ/PNG；没有修改上游重建算法。

## Infinigen 1.3.3 与 Blender 3.6：另一个 Python 3.10 环境

实际使用 Infinigen **1.3.3**，commit
`f5bcba8de47623da9b715348cf95822445e0e9a7`，外部 Blender **3.6.0**
（内置 Python **3.10.12**）。本机独立依赖环境是 Python **3.10.22**、NumPy **1.26.2**；
Blender 子进程通过该环境的 `site-packages` 和 Infinigen 源码加载依赖。
上游 `pyproject.toml` 要求 Python `==3.10.*` 与 `bpy==3.6.0`，不能直接安装到核心 Python 3.11。

已有可用安装只需填写 Blender 可执行文件、Infinigen 源码根和 Python 3.10
`site-packages`。新安装可采用固定源码的
[Blender Python 安装路线](https://github.com/princeton-vl/infinigen/blob/f5bcba8de47623da9b715348cf95822445e0e9a7/docs/Installation.md)：

```bash
git clone https://github.com/princeton-vl/infinigen.git ~/src/infinigen
git -C ~/src/infinigen checkout f5bcba8de47623da9b715348cf95822445e0e9a7
cd ~/src/infinigen
# 上游最小安装：对象/材质；本适配无需重新生成完整 Terrain。
INFINIGEN_MINIMAL_INSTALL=True bash scripts/install/interactive_blender.sh
./blender/blender --version
./blender/3.6/python/bin/python3.10 --version
```

该 Linux 路线下载独立 Blender，并在其内置 Python 中安装依赖；它不替换系统 Python。
采用此路线时，`blender.python_site_packages` 通常填写
`/absolute/path/to/infinigen/blender/3.6/python/lib/python3.10/site-packages`。
采用独立 Conda Python 3.10 依赖环境时，则填写那个环境的实际 `site-packages`。
完整原生安装可能需要平台编译工具；遵循上游说明，**本次没有重新验证全自动安装**。
本次验收验证的是既有安装中的 `GrassTuftFactory`、`make_asset_collection`、
`scatter_instances` 和 CPU Cycles 渲染，没有声称重新跑通完整地形/树木生成。

## 填写本地配置并执行集成

回到 OutdoorGraph 源码目录并激活核心环境，将公开配置复制为被 Git 忽略的本地配置：

```bash
cd ~/outdoor
conda activate ./.venv
cp configs/integration_mountain.json configs/integration_mountain.local.json
```

编辑 `configs/integration_mountain.local.json` 中的路径：

| 字段 | 必须指向 |
|---|---|
| `input.image` | 实际 RGB 图像；可使用已有 MoGe `example_images/05_Mountain.jpg` |
| `moge.python` | 已准备好的外部 MoGe 环境 Python 可执行文件 |
| `moge.repo` | 含 `moge/model/v2.py` 的固定源码根 |
| `moge.utils3d_repo` | 含 `utils3d_moge/` 的源码根 |
| `moge.weights` | 与 v2 small-normal 架构对应的本地 checkpoint |
| `blender.executable` | 独立 Blender 3.6 可执行文件 |
| `blender.infinigen_source` | 固定版本 Infinigen 源码根 |
| `blender.python_site_packages` | 兼容 Blender Python 3.10 的依赖目录 |

路径可以使用绝对路径或 `~`；相对路径基于 **config 所在目录**，不是 shell 的 cwd。
JSON 中不要填写 `$HOME`、`${VAR}` 或其他环境变量表达式：加载器不做环境变量替换。
人工区域多边形使用原图归一化坐标 `[0,1]`，请按自己的输入图像调整；
它不是模型自动语义分割。`cuda_visible_devices` 要选择自己可用的 GPU，
不要抢占其他任务；当前 Blender 渲染固定使用 CPU。

```bash
python -m pip check
python -m pytest -q
# 使用尚不存在的新输出目录；不覆盖已有结果。
outdoorgraph integrate --config configs/integration_mountain.local.json --out runs/my_mountain_v02
```

本次真实验证生成 **86** 个 Infinigen 草实例；另一 Blender 进程重开场景后
验证实例地表接触、手工 Mask、相机、PNG 解码与 GLB 重导入。这些检查与核心单测分开。
不同输入/环境下实际实例数可变化，不能将 86 当作所有运行的固定验收值。
没有本地 checkpoint 或外部原生环境时，仍可运行全部核心测试和代理 Demo，
不能把它们当作 MoGe/Blender 集成已经通过。

## 缓存、凭据与发布

`.gitignore` 排除独立环境、Conda 缓存、`.env*`（允许公开 `.env.example/.env.sample`）、
凭据/私钥、本地 JSON 配置、权重、生成场景/数组/视频与下载压缩包。
正常示例 PNG、JSON 和文档仍可提交；小型必要的二进制测试 fixture 应单独审核后明确例外。
本地推理不要求商业 API key。外部系统代码、权重和数据的许可分别保留，
具体版本、历史运行边界与许可见 [集成盘点](integration_inventory.md)。
