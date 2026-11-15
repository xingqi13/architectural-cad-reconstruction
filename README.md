# 建筑纸图识读与CAD重建

一个面向 WorkBuddy 和兼容 `SKILL.md` 技能目录的建筑图纸识读与 CAD 重建技能。它帮助使用者根据扫描纸图、照片或 PDF 理解建筑围护、空间与构件关系，重建可编辑图纸，并整理现场核查问题。

![技能图标](logo.png)

## 能做什么

- 从源图的图名、楼层、轴线、尺寸及标高入手，识读外围、房间、开敞边界和主要空间流线。
- 辅助重建墙柱、门窗、楼梯、电梯等可编辑 CAD 图元，检查接头、墙厚、洞口、空间连通和跨层柱轴关系。
- 结合平面、立面、剖面检查楼板、梁、墙柱、楼梯、坡道和标高，并对缺页、不清楚或不一致之处记录依据及现场待核事项。
- 附带 PDF 页面盘点、DXF 几何候选检查和剖面标高插值的只读辅助脚本。

此仓库提供的是技能指令和辅助检查脚本；需要由具备读图、文件处理及 CAD 操作能力的智能体结合实际环境执行。脚本不会自动完成整套图纸重建；生成 DWG 需要实际可用的 CAD 转换工具，并应重新打开检查。

## WorkBuddy 使用

1. 下载本仓库并解压，找到直接包含 `SKILL.md` 的文件夹。
2. 在 WorkBuddy 的技能管理中选择导入该文件夹，或按照客户端支持的方式将文件夹复制到个人技能目录。
3. 在新对话中指定扫描图文件和交付范围，例如：

   > 使用建筑纸图识读与CAD重建技能，根据扫描纸质图重建可编辑 CAD。先理解外围、房间、墙柱和流线，再核查门窗、楼梯及各层柱位；无法确认的内容标注待现场核实，并提供修改前后的局部对照。

如 WorkBuddy 开放平台要求上传 ZIP，请将仓库内的 `SKILL.md`、`logo.png`、`references/` 和 `scripts/` 放在 ZIP 根目录，而不是再包一层父文件夹。在 Windows PowerShell 中，可以在仓库目录运行：

```powershell
Compress-Archive -LiteralPath .\SKILL.md,.\logo.png,.\references,.\scripts -DestinationPath ..\architectural-cad-reconstruction-workbuddy.zip
```

头像为 512×512 PNG，可在平台要求时单独上传。页面、安装入口和兼容性以所用 WorkBuddy 版本为准。

## 环境与脚本

辅助脚本需要 Python 3；PDF 盘点需要 PyMuPDF，DXF 检查需要 ezdxf 与 Shapely 2。依赖可按需安装：

```bash
python -m pip install -r requirements.txt
```

在仓库目录执行以下命令，或改为实际脚本的绝对路径：

```bash
python scripts/inspect_pdf.py INPUT.pdf --out NEW_REPORT.json
python scripts/audit_dxf.py INPUT.dxf --layers WALL --unit-mm 1 --gap-mm 10 --out NEW_REPORT.json
python scripts/calibrate_section.py CONTROLS.json --out NEW_REPORT.json
```

输入示例、参数含义和限制见 [脚本说明](references/tools.md)。几何检查只生成候选清单，门洞、构造缝和不同构件可能带来误报；不得自动把所有缺口封闭或强制移动柱子。

## 使用边界

- 原始图纸只读。图面标注、图形估算和现场实测应分开记录；候选几何及尺寸必须清楚标注待核实。
- 空间和构件连接检查不等于结构承载力、消防、无障碍或规范符合性审查；工程结论应由相应专业人员结合资料和现场复核。
- 仓库不包含用户提供的原始图纸、工程成果或商用 CAD 软件；天正等专用构件须由使用者确认安装、授权和兼容性。

## 许可证

本仓库按 [MIT License](LICENSE) 开源。
