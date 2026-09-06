#!/usr/bin/env python3
"""
build_compact_font.py — 从 "Microsoft YaHei Consolas Regular.ttf" 生成
"Microsoft YaHei Consolas Compact.ttf" 的可复现构建脚本。

修改内容：
1. 等比缩放全部全角（雅黑）字形并横向居中（advance 保持 2048，1:2 宽度不变）
2. 垂直度量改回原版 Consolas（行高 2398 = 1.171em）
3. 从 DejaVu Sans Mono 补入源字体缺失的标点/符号（P*/S*），按 Unicode 宽度适配
4. 删除失效的 TrueType hinting（fpgm/prep/cvt + 全部字形指令）与竖排表（vhea/vmtx）
5. 重命名 family 为 "Microsoft YaHei Consolas Compact"

依赖：fontTools >= 4.x   （pip install fonttools）
用法：python3 build_compact_font.py
"""
import sys
from collections import Counter
from unicodedata import category, east_asian_width

from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen

SRC = "Microsoft YaHei Consolas Regular.ttf"
DST = "Microsoft YaHei Consolas Compact.ttf"
DEJAVU_SRC = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
SUPPLEMENT_HALF_ADVANCE = 1024
SUPPLEMENT_FULL_ADVANCE = 2048
# W/F 是 Unicode 明确的宽/全宽字符；A（Ambiguous）在终端/代码场景按单列处理。
SUPPLEMENT_WIDE_EAW = frozenset(("W", "F"))
SUPPLEMENT_CATEGORIES = frozenset(("P", "S"))  # 标点与符号，不混入其他文字脚本

# ---- 目标度量（原版 Consolas consola.ttf）----
WIN_ASCENT = 1884          # usWinAscent
WIN_DESCENT = -514         # usWinDescent（负值表示基线下方）
TYP_ASCENT = 1521          # hhea/OS2 sTypoAscender
TYP_DESCENT = -527         # hhea/OS2 sTypoDescender
LINE_GAP = 350             # hhea/OS2 sTypoLineGap
NEW_FAMILY = "Microsoft YaHei Consolas Compact"
NEW_PS_NAME = "MicrosoftYaHeiConsolasCompact"


def supplement_missing_symbols(font, all_glyphs):
    """从 DejaVu Sans Mono 补入目标字体缺失的标点/符号。"""
    supplement = TTFont(DEJAVU_SRC)
    try:
        target_cmap = font.getBestCmap() or {}
        source_cmap = supplement.getBestCmap() or {}
        missing_cps = sorted(
            cp for cp in source_cmap
            if cp not in target_cmap
            and category(chr(cp))[0] in SUPPLEMENT_CATEGORIES
        )
        if not missing_cps:
            print("DejaVu Sans Mono 缺失符号: 0")
            return all_glyphs

        source_gs = supplement.getGlyphSet()
        source_glyf = supplement["glyf"]
        source_hmtx = supplement["hmtx"]
        source_bounds = {}
        for cp in missing_cps:
            source_glyph = source_cmap[cp]
            if source_glyph in source_bounds:
                continue
            source_g = source_glyf[source_glyph]
            source_g.recalcBounds(source_glyf)
            if source_g.numberOfContours == 0:
                source_bounds[source_glyph] = None
            else:
                # 使用 glyf 的 TrueType bbox，而不是仅由曲线极值计算的 BoundsPen
                # bbox；前者还覆盖控制点，能保证字体表本身不会越过单元格。
                source_bounds[source_glyph] = (
                    source_g.xMin, source_g.yMin, source_g.xMax, source_g.yMax
                )

        # 空轮廓（例如 U+FFFC OBJECT REPLACEMENT CHARACTER）不提供可见字形，
        # 保持其缺失状态，继续交给应用自己的 fallback 处理。
        missing_cps = [
            cp for cp in missing_cps
            if source_bounds[source_cmap[cp]] is not None
        ]
        if not missing_cps:
            print("DejaVu Sans Mono 缺失符号: 0（无可见轮廓）")
            return all_glyphs

        nonempty_bounds = [b for b in source_bounds.values() if b is not None]
        # DejaVu 与 Compact 都是 2048 UPM，但少数符号越过 Compact 的
        # winAscent/winDescent；统一做轻微纵向压缩，避免新符号被裁剪。
        y_min = min(b[1] for b in nonempty_bounds)
        y_max = max(b[3] for b in nonempty_bounds)
        sy = (WIN_ASCENT - WIN_DESCENT) / (y_max - y_min)
        dy = WIN_DESCENT - sy * y_min

        target_glyf = font["glyf"]
        target_hmtx = font["hmtx"]
        used_names = set(all_glyphs)
        source_to_target = {}
        added_names = []
        added_codepoints = 0
        width_counts = Counter()

        for cp in missing_cps:
            source_glyph = source_cmap[cp]
            target_advance = (
                SUPPLEMENT_FULL_ADVANCE
                if east_asian_width(chr(cp)) in SUPPLEMENT_WIDE_EAW
                else SUPPLEMENT_HALF_ADVANCE
            )
            width_key = (source_glyph, target_advance)
            target_glyph = source_to_target.get(width_key)
            if target_glyph is None:
                base_name = "dvsm_" + source_glyph.replace(".", "_")
                if target_advance == SUPPLEMENT_FULL_ADVANCE:
                    base_name += "_wide"
                target_glyph = base_name
                suffix = 1
                while target_glyph in used_names:
                    target_glyph = f"{base_name}_{suffix}"
                    suffix += 1
                used_names.add(target_glyph)

                bounds = source_bounds[source_glyph]
                if bounds is None:
                    # missing_cps 已过滤空轮廓；此分支仅作为防御性保护。
                    continue
                x_min, y_min_glyph, x_max, y_max_glyph = bounds
                source_advance = source_hmtx[source_glyph][0]
                sx = target_advance / source_advance
                source_width = x_max - x_min
                if source_width > target_advance:
                    # 少数框线/数学符号的轮廓本身超出源字体 advance；
                    # 进一步缩小，确保新增字形完整落在目标单元格内。
                    sx = min(sx, target_advance / source_width)
                cx = (x_min + x_max) / 2.0
                dx = target_advance / 2.0 - sx * cx

                # 先展开复合字形，再做横向适配和统一的纵向适配。
                rec = DecomposingRecordingPen(source_gs)
                source_gs[source_glyph].draw(rec)
                ttpen = TTGlyphPen(None)
                tpen = TransformPen(ttpen, (sx, 0, 0, sy, dx, dy))
                rec.replay(tpen)
                new_glyph = ttpen.glyph()
                new_lsb = round(sx * x_min + dx)

                target_glyf[target_glyph] = new_glyph
                target_hmtx[target_glyph] = (target_advance, new_lsb)
                source_to_target[width_key] = target_glyph
                all_glyphs.append(target_glyph)
                added_names.append(target_glyph)

            for cmap_table in font["cmap"].tables:
                if cmap_table.isUnicode():
                    cmap_table.cmap[cp] = target_glyph
            width_counts[target_advance] += 1
            added_codepoints += 1

        font.setGlyphOrder(all_glyphs)
        print(
            f"补入 DejaVu Sans Mono 标点/符号: {added_codepoints} 个码点，"
            f"{len(added_names)} 个字形；"
            f"半角={width_counts[SUPPLEMENT_HALF_ADVANCE]}，"
            f"全角={width_counts[SUPPLEMENT_FULL_ADVANCE]}；"
            f"纵向变换 sy={sy:.6f}, dy={dy:.3f}"
        )
        return all_glyphs
    finally:
        supplement.close()


def main():
    if len(sys.argv) > 1:
        out = sys.argv[1]
    else:
        out = DST

    font = TTFont(SRC)
    glyf = font['glyf']
    hmtx = font['hmtx']
    head = font['head']
    hhea = font['hhea']
    os2 = font['OS/2']
    all_glyphs = list(font.getGlyphOrder())

    # ---- 1. 分组：advance == 2048 的全角（雅黑）字形 ----
    to_scale = set(gn for gn in all_glyphs if hmtx[gn][0] == 2048)
    print(f"缩放字形数: {len(to_scale)} / 总字形 {len(all_glyphs)}")

    # ---- 2. 全局垂直变换参数：把 [yMin,yMax] 线性映射到 [WIN_DESCENT, WIN_ASCENT] ----
    ys = [glyf[gn].yMin for gn in to_scale if glyf[gn].numberOfContours != 0]
    ye = [glyf[gn].yMax for gn in to_scale if glyf[gn].numberOfContours != 0]
    y_min_all, y_max_all = min(ys), max(ye)
    s = (WIN_ASCENT - WIN_DESCENT) / (y_max_all - y_min_all)
    dy = WIN_DESCENT - s * y_min_all
    print(f"全角字形 y 范围: [{y_min_all}, {y_max_all}] -> s={s:.6f}, dy={dy:.3f}")

    # ---- 3. 先展平所有复合字形为简单字形（消除组件/复合的处理顺序依赖）----
    gs = font.getGlyphSet()  # draw(pen) 单参数包装，正确解析复合字形
    n_composite = 0
    for gn in all_glyphs:
        g = glyf[gn]
        if g.isComposite():
            rec = DecomposingRecordingPen(gs)  # 递归展开组件为纯轮廓点
            gs[gn].draw(rec)
            ttpen = TTGlyphPen(gs)
            rec.replay(ttpen)
            glyf[gn] = ttpen.glyph()
            n_composite += 1
    if n_composite:
        print(f"展平复合字形: {n_composite}")

    # ---- 4. 补入缺失的标点/符号，按 Unicode 宽度适配半角或全角 ----
    all_glyphs = supplement_missing_symbols(font, all_glyphs)
    # 新增字形没有旧字体的竖排度量；这些表最终也会被删除，提前移除后
    # fontTools 的 GlyphSet 不会再尝试读取不存在的新 glyph 的 vMetrics。
    for t in ('vhea', 'vmtx'):
        if t in font:
            del font[t]
    # 新增字形后刷新 GlyphSet，供后续边界计算使用。
    gs = font.getGlyphSet()

    # ---- 5. 逐字形变换（此时全部为简单字形），数学方式记录新边界 ----
    new_bounds = {}
    n_scaled = 0
    for gn in to_scale:
        g = glyf[gn]
        if g.numberOfContours == 0 and not g.isComposite():
            continue  # 空字形（.null 等）
        bp = BoundsPen(gs)
        gs[gn].draw(bp)
        if bp.bounds is None:
            continue
        x_min, y_min, x_max, y_max = bp.bounds
        cx = (x_min + x_max) / 2.0
        dx = 1024.0 - s * cx  # 横向居中到全角格 [0, 2048]
        ttpen = TTGlyphPen(gs)
        tpen = TransformPen(ttpen, (s, 0, 0, s, dx, dy))
        gs[gn].draw(tpen)
        glyf[gn] = ttpen.glyph()
        # 新边界 = 线性映射端点（xMin/xMax 单调）
        nx_min, nx_max = round(s * x_min + dx), round(s * x_max + dx)
        ny_min, ny_max = round(s * y_min + dy), round(s * y_max + dy)
        new_bounds[gn] = (nx_min, ny_min, nx_max, ny_max)
        hmtx[gn] = (hmtx[gn][0], nx_min)  # 更新 lsb，advance 保持 2048
        n_scaled += 1
    print(f"实际缩放字形: {n_scaled}")

    # ---- 6. 让全部字形可计算边界（TTGlyphPen 产物为懒加载状态，maxp.recalc 需要）----
    for gn in all_glyphs:
        g = glyf[gn]
        if g.numberOfContours != 0:
            g.recalcBounds(glyf)

    # ---- 7. 重算 head 全局边界（统一用 BoundsPen，兼容懒加载字形）----
    x_mins, x_maxs, y_mins, y_maxs = [], [], [], []
    for gn in all_glyphs:
        g = glyf[gn]
        if g.numberOfContours == 0:
            continue
        bp = BoundsPen(gs)
        gs[gn].draw(bp)
        if bp.bounds is None:
            continue
        x_min, y_min, x_max, y_max = bp.bounds
        x_mins.append(x_min); x_maxs.append(x_max)
        y_mins.append(y_min); y_maxs.append(y_max)
    head.xMin, head.xMax = min(x_mins), max(x_maxs)
    head.yMin, head.yMax = min(y_mins), max(y_maxs)
    print(f"head 边界: x[{head.xMin},{head.xMax}] y[{head.yMin},{head.yMax}]")

    # ---- 8. 垂直度量改回原版 Consolas ----
    hhea.ascent, hhea.descent, hhea.lineGap = TYP_ASCENT, TYP_DESCENT, LINE_GAP
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = TYP_ASCENT, TYP_DESCENT, LINE_GAP
    os2.usWinAscent, os2.usWinDescent = WIN_ASCENT, -WIN_DESCENT
    line_h = TYP_ASCENT - TYP_DESCENT + LINE_GAP
    print(f"行高 = {line_h} ({line_h / head.unitsPerEm:.3f}em)")

    # ---- 9. 删除失效/无用表 ----
    for t in ('fpgm', 'prep', 'cvt ', 'vhea', 'vmtx'):
        if t in font:
            del font[t]

    # ---- 10. 清除全部字形 hinting 指令（含未缩放半角字形残留）----
    removed = 0
    for gn in all_glyphs:
        g = glyf[gn]
        if g.numberOfContours == 0:
            continue
        prog = getattr(g, 'program', None)
        if prog and len(prog.getBytecode()):
            g.removeHinting()
            removed += 1
    print(f"清除 hinting 指令字形: {removed}")

    # ---- 11. maxp 重新计算 ----
    font['maxp'].recalc(font)

    # ---- 12. 重命名 family，避免与已安装同名字体冲突 ----
    name = font['name']
    for nid in (1, 2, 4, 6, 16, 17):
        name.removeNames(nameID=nid)
    name.setName(NEW_FAMILY, 1, 3, 1, 0x409)
    name.setName("Regular", 2, 3, 1, 0x409)
    name.setName(NEW_FAMILY + " Regular", 4, 3, 1, 0x409)
    name.setName(NEW_PS_NAME, 6, 3, 1, 0x409)
    name.setName(NEW_FAMILY, 16, 3, 1, 0x409)
    name.setName("Regular", 17, 3, 1, 0x409)

    font.save(out)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
