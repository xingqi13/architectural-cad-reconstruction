# 可选脚本

脚本只读输入，结果写入不存在的新文件；均不能替代建筑识图或原生 CAD 校核。路径独立于原始项目。示例中的 `python` 须指向装有相应依赖的环境。

## PDF 结构概览

```text
python scripts/inspect_pdf.py INPUT.pdf --out NEW_REPORT.json
```

依赖 PyMuPDF。统计每页文字、图像覆盖估计、矢量路径和页面范围；图像可能重叠，覆盖率和“扫描／矢量／混合”仅供路由。检查图名、尺寸、图例和清晰度仍需看原页。

## 已知标高对扫描图的局部插值

```text
python scripts/calibrate_section.py CONTROLS.json --out NEW_REPORT.json
```

输入 JSON 示例：

```json
{
  "reference_x_px": 100,
  "scan_slope_px_per_px": 0.01,
  "levels": [
    {"y_px": 100, "level_mm": 0, "source": "LEVEL 1"},
    {"y_px": 200, "level_mm": -4000, "source": "LEVEL 2"}
  ],
  "samples": [{"name": "candidate", "x_px": 200, "y_px": 151}]
}
```

标高点均须在指定参考横坐标处，并已经人工核对；倾斜量由独立观测取得。脚本只在控制点覆盖范围内插值并给出所用两个控制点，范围外返回空值，不猜测或自动纠偏图元。结果受来源像素、线宽、扫描变形和控制点解释影响；对变形或冲突显著的区域分区重核。

## DXF 几何候选检查

```text
python scripts/audit_dxf.py INPUT.dxf --layers WALL_1 WALL_2 --unit-mm 1 --gap-mm 10 --out NEW_REPORT.json
```

依赖 ezdxf 和 Shapely 2。输入只选择经确认的二维墙或结构图层，`--unit-mm` 是一个绘图单位对应的毫米，`--gap-mm` 由任务和精度确定。可用 `--axes-deg` 指定已知主轴方向；接近主轴但略偏的线才列为角度候选，其他角度不判错。

脚本按端点对其他直线段的距离列出近缺口，并报告线段穿越／重叠。洞口、构造缝、平行墙面、圆弧、插入块和投影关系均可能导致误报或漏报；报告明确列出未处理的对象类型。永远不要直接把候选列表应用为自动闭合或自动移动。DXF 文件和依赖参照存在不代表 DWG 已经打开或图纸完整。

## 用 AutoCAD 批量生成已核定的对称直墙

`cad_batch.py` 读取已从原图核定的墙中心线、墙厚、洞口和预期空间数据。输入示例见 [合成住宅片段](../scripts/cad_batch_example.json)。依赖 ezdxf 和 Shapely 2。用当前技能根目录下的 `scripts` 路径运行，并把输出与过程目录放在项目指定位置。

```text
python scripts/cad_batch.py --input WALLS.json --output NEW_DIR --work SCRATCH_DIR
```

支持直墙和基线两侧等宽墙体，生成可编辑墙轮廓、实际洞口与门窗块。存在明确的非对称偏移、异厚共面、曲线、复杂墙柱节点或楼梯时，按已经核定的控制面生成相应几何。脚本的候选空间和门洞检查属于预检，整层逐房围护、实际洞口及流线仍按 [平面推理与验收](plan-topology.md) 检查。

需要 DWG 时，在确认本机已装有 AutoCAD Core Console 后，可另加 `--accoreconsole "<已确认的 accoreconsole.exe 路径>"`。用 AutoCAD 重新打开保存后的 DWG 并检查整张图及修改节点。输出缺少有效 DWG 时，按实际生成的 DXF 报告结果。
