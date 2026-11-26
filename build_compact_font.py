#!/usr/bin/env python3
"""
build_compact_font.py — 从 "Microsoft YaHei Consolas Regular.ttf" 生成
"YaHei Consolas Mono.ttf" 的可复现构建脚本。

修改内容：
1. 等比缩放全部全角（雅黑）字形并横向居中（advance 保持 2048，1:2 宽度不变）
2. 垂直度量改回原版 Consolas（行高 2398 = 1.171em）
3. 从 DejaVu Sans Mono 补入源字体缺失的标点/符号（P*/S*），按 Unicode 宽度适配
4. 删除失效的 TrueType hinting（fpgm/prep/cvt + 全部字形指令）与竖排表（vhea/vmtx）
5. 重命名 family 为 "YaHei Consolas Mono"（单字重家族，full name 不再带 Regular）
6. 把 0.6em(1229) 档的块元素/几何图形归一化到 1024 半角格（消掉同码块宽度分裂
   与 14 个块元素字形的横向越界），并把块元素的纵向盒子统一到整格 [-514, 1884]
7. 声明 isFixedPitch=1 / panose.bProportion=9，写入版本/厂商标识，并在保存前做自检

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
DST = "YaHei Consolas Mono.ttf"
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
NEW_FAMILY = "YaHei Consolas Mono"
NEW_PS_NAME = "YaHeiConsolasMono"

# ---- 版本与来源标识 ----
# FONT_VERSION 是版本信息的单一真源：head.fontRevision 与 name ID 3/5 都由它
# 派生，verify_font 会校验三者一致。递增规则：改字形/度量/元数据 -> +0.001；
# 仅重建 -> 不变号；重新同步上游 -> 主版本 +1 并更新 BASE_VERSION。
FONT_VERSION = "1.001"          # 1.000 首次发布；1.001 改族名与 full name
BASE_VERSION = "6.250"          # 所基于的 Microsoft YaHei Consolas 版本
BUILD_TOOL = "build_compact_font.py"
VENDOR_ID = "RUAN"              # OS/2.achVendID，必须 4 字符 ASCII
VENDOR_NAME = "ruanimal"        # name ID 8（厂商）
VENDOR_URL = (                  # name ID 11（厂商网址）
    "https://github.com/ruanimal/Microsoft-Yahei-Consolas-Compact"
)

# ---- 半宽符号归一化 + 等宽声明 ----
# 上游雅黑把块元素/几何图形设计在 0.6em(1229) 的比例格位里，与本字体的 1024
# 半角格冲突：同一个码块内 1024 与 1229 并存，`▁▂▃▄` 这类进度条会在 1/4 处
# 突然变宽。这里把它们归一到 1024，与脚本自身对补入符号的策略保持一致
# （W/F -> 2048，其余 -> 1024）。
SYMBOL_OFFGRID_ADVANCE = 1229             # 雅黑 0.6em 比例档
SYMBOL_TARGET_ADVANCE = 1024              # 本字体的半角格
BLOCK_ELEMENT_RANGE = (0x2580, 0x259F)    # 制表块元素：整组共享同一格位
GEOMETRIC_RANGE = (0x25A0, 0x25EF)        # 几何图形：等比居中

# ---- 块元素纵向盒子统一（P0.5）----
# 同一个码块里的块元素纵向范围有四种（含一组自带下边距的影线），混排时高低不一：
#   Consolas   ▀ ▄ █ ▌ ▐ ▓ ░ ▒  -405 .. 1775   (2180；░▒ 由设计自带 89
#                                              单位下边距，顶边与 ▓ 对齐)
#   雅黑       ▁▂▃▅▆▇▉▊▋▍▎▏▔▕  -446 .. 2036   (2482，超出 win 盒 84)
#   DejaVu     ▖▗▘▙▚▛▜▝▞▟      -493 .. 1872   (2365)
# 统一到整格 [-514, 1884]（usWinDescent..usWinAscent，也是全角字形的范围），
# 这样 █ 能贴满单元格、`▁..▇` 按 1/8 递增、象限块正好半格。
BLOCK_TARGET_BOX = (WIN_DESCENT, WIN_ASCENT)
BLOCK_SOURCE_BOXES = {
    "consolas": (-405, 1775),
    "yahei": (-446, 2036),
    "dejavu": (-493, 1872),
}
BLOCK_SOURCE_FAMILY = {
    # Consolas 侧
    0x2580: "consolas", 0x2584: "consolas", 0x2588: "consolas",
    0x258C: "consolas", 0x2590: "consolas", 0x2591: "consolas",
    0x2592: "consolas", 0x2593: "consolas",
    # 雅黑侧：1/8..7/8 下阶、左竖条、上下/右贴边
    0x2581: "yahei", 0x2582: "yahei", 0x2583: "yahei", 0x2585: "yahei",
    0x2586: "yahei", 0x2587: "yahei", 0x2589: "yahei", 0x258A: "yahei",
    0x258B: "yahei", 0x258D: "yahei", 0x258E: "yahei", 0x258F: "yahei",
    0x2594: "yahei", 0x2595: "yahei",
    # DejaVu 补入的象限
    0x2596: "dejavu", 0x2597: "dejavu", 0x2598: "dejavu", 0x2599: "dejavu",
    0x259A: "dejavu", 0x259B: "dejavu", 0x259C: "dejavu", 0x259D: "dejavu",
    0x259E: "dejavu", 0x259F: "dejavu",
}
# 应当贴满整格的"满块"字形：█ ▉ ▐ ▕
BLOCK_FULL_CODEPOINTS = (0x2588, 0x2589, 0x2590, 0x2595)

# 已知仍为比例宽度的字形（P1/P2 未处理的标点与数学符号），自检时作为白名单；
# 除此之外出现任何非 {0, 1024, 2048} 的 advance 都视为回归。
KNOWN_OFFGRID_CODEPOINTS = {
    0x02C9: 701, 0x2010: 886, 0x2025: 985, 0x2027: 493, 0x2032: 523, 0x2035: 522,
    0x203B: 1724, 0x215F: 1925, 0x221D: 1806, 0x2220: 1438, 0x2223: 1124,
    0x2225: 1491, 0x2227: 1489, 0x2228: 1489, 0x222E: 1190, 0x2234: 1438,
    0x2235: 1438, 0x2236: 1767, 0x2237: 1767, 0x223D: 1489, 0x224C: 1124,
    0x2252: 1491, 0x2266: 1124, 0x2267: 1124, 0x226E: 1124, 0x226F: 1124,
    0x2295: 1638, 0x22BF: 1460, 0x2605: 1671, 0x2606: 1686, 0x2609: 1638,
}


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
        # DejaVu 与本字体都是 2048 UPM，但少数符号越过本字体的
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


def _glyph_x_bounds(font, glyph_names):
    """返回 {glyph: (xMin, yMin, xMax, yMax)}，空字形不收录。"""
    gs = font.getGlyphSet()
    bounds = {}
    for gname in glyph_names:
        bp = BoundsPen(gs)
        gs[gname].draw(bp)
        if bp.bounds is not None:
            bounds[gname] = bp.bounds
    return bounds


def normalize_half_width_symbols(font, all_glyphs):
    """把 1229(0.6em) 档的块元素与几何图形归一到 1024 半角格。

    块元素（U+2580-259F）是拼贴用的矩形，须按整组的联合 bbox 做一次仿射，
    这样 `▁..▇` 与 `▉..▏` 共享同一个格位；逐字形居中会让左右块互相错位。
    几何图形（U+25A0-25EF）的轮廓本来就在半角格内，等比（必要时缩小）后
    居中即可。只动横向，纵向位置保持上游设计。
    """
    hmtx = font["hmtx"]
    cp_of = {}
    for cp, gname in font.getBestCmap().items():
        cp_of.setdefault(gname, cp)

    blocks, shapes = [], []
    for gname in all_glyphs:
        if hmtx[gname][0] != SYMBOL_OFFGRID_ADVANCE:
            continue
        cp = cp_of.get(gname)
        if cp is None:
            continue
        if BLOCK_ELEMENT_RANGE[0] <= cp <= BLOCK_ELEMENT_RANGE[1]:
            blocks.append(gname)
        elif GEOMETRIC_RANGE[0] <= cp <= GEOMETRIC_RANGE[1]:
            shapes.append(gname)
    if not blocks and not shapes:
        print("半宽符号归一化: 无需处理")
        return

    bounds = _glyph_x_bounds(font, blocks + shapes)
    blocks = [g for g in blocks if g in bounds]
    shapes = [g for g in shapes if g in bounds]
    transforms = {}
    if blocks:
        xs_min = min(bounds[g][0] for g in blocks)
        xs_max = max(bounds[g][2] for g in blocks)
        sx = SYMBOL_TARGET_ADVANCE / (xs_max - xs_min)
        dx = -sx * xs_min
        transforms.update({g: (sx, dx) for g in blocks})
    for gname in shapes:
        x_min, _, x_max, _ = bounds[gname]
        sx = min(1.0, SYMBOL_TARGET_ADVANCE / (x_max - x_min))
        cx = (x_min + x_max) / 2.0
        transforms[gname] = (sx, SYMBOL_TARGET_ADVANCE / 2.0 - sx * cx)

    gs = font.getGlyphSet()
    for gname, (sx, dx) in transforms.items():
        x_min = bounds[gname][0]
        ttpen = TTGlyphPen(gs)
        gs[gname].draw(TransformPen(ttpen, (sx, 0, 0, 1, dx, 0)))
        font["glyf"][gname] = ttpen.glyph()
        hmtx[gname] = (SYMBOL_TARGET_ADVANCE, round(sx * x_min + dx))

    print(
        f"半宽符号归一化: 块元素 {len(blocks)} 个 + 几何图形 {len(shapes)} 个，"
        f"advance {SYMBOL_OFFGRID_ADVANCE} -> {SYMBOL_TARGET_ADVANCE}"
    )


def unify_block_element_box(font, all_glyphs):
    """把 U+2580-259F 的纵向盒子统一到整格 [-514, 1884]。

    这些字形是终端里画进度条/字符图用的拼贴块，必须共享同一个纵向格子，
    否则 `▉▉▉█` 这类混排会出现高低差（雅黑侧顶部到 2036，Consolas 侧只到
    1775）。这里按各自来源盒子做纵向仿射，保持"1/8 阶""半块""象限"等相对
    语义；横向已在 normalize_half_width_symbols 里归一，此处只动 y。
    """
    glyf, hmtx = font["glyf"], font["hmtx"]
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    target_lo, target_hi = BLOCK_TARGET_BOX
    done = {}
    mismatched = []
    for cp in sorted(BLOCK_SOURCE_FAMILY):
        gname = cmap.get(cp)
        if gname is None or gname not in glyf or glyf[gname].numberOfContours == 0:
            continue
        src_lo, src_hi = BLOCK_SOURCE_BOXES[BLOCK_SOURCE_FAMILY[cp]]
        # 只应作用在原始合并字体上：几何已统一过（重复执行）时必须报错，
        # 否则会二次缩放，得到超出整格的字形。
        g = glyf[gname]
        g.recalcBounds(glyf)
        if g.yMin < src_lo - 4 or g.yMax > src_hi + 4:
            mismatched.append(f"U+{cp:04X}[{g.yMin},{g.yMax}]")
            continue
        sy = (target_hi - target_lo) / (src_hi - src_lo)
        dy = target_lo - sy * src_lo
        ttpen = TTGlyphPen(gs)
        gs[gname].draw(TransformPen(ttpen, (1, 0, 0, sy, 0, dy)))
        glyf[gname] = ttpen.glyph()
        done[cp] = (sy, dy)
    if mismatched:
        raise SystemExit(
            "块元素纵向盒子与预期不符（输入可能不是原合并字体，或本步骤被重复执行）: "
            + ", ".join(mismatched[:8])
        )
    if not done:
        print("块元素纵向统一: 无需处理")
        return
    ratios = sorted({round(sy, 4) for sy, _ in done.values()})
    print(
        f"块元素纵向统一: {len(done)} 个字形 -> 整格 [{target_lo}, {target_hi}]，"
        f"纵向缩放比 {ratios}"
    )


def recalc_hhea_metrics(font):
    """重算 hhea 中由字形度量派生的四个字段（归一化会改变 lsb/rsb）。"""
    hmtx, glyf, hhea = font["hmtx"], font["glyf"], font["hhea"]
    order = font.getGlyphOrder()
    lsb_min = rsb_min = extent_max = None
    for gname in order:
        g = glyf[gname]
        if g.numberOfContours == 0:
            continue
        advance, lsb = hmtx[gname]
        ink = g.xMax - g.xMin
        lsb_min = lsb if lsb_min is None else min(lsb_min, lsb)
        rsb = advance - lsb - ink
        rsb_min = rsb if rsb_min is None else min(rsb_min, rsb)
        extent = lsb + ink
        extent_max = extent if extent_max is None else max(extent_max, extent)
    hhea.advanceWidthMax = max(hmtx[g][0] for g in order)
    if lsb_min is not None:
        hhea.minLeftSideBearing = lsb_min
        hhea.minRightSideBearing = rsb_min
        hhea.xMaxExtent = extent_max
    print(
        f"hhea 派生度量: advWidthMax={hhea.advanceWidthMax} "
        f"minLSB={hhea.minLeftSideBearing} minRSB={hhea.minRightSideBearing} "
        f"xMaxExtent={hhea.xMaxExtent}"
    )


def rename_family(font):
    """把字体族改成独立名字，并写成单字重（Regular）家族的命名结构。

    "Compact" 属于宽度/设计修饰词，用作族名会让人以为还存在一个非 Compact
    的同族版本；本字体实际是独立的等宽编码字体，因此族名用用途后缀 Mono。
    单字重家族的正确写法：name ID 2 = Regular（RIBBI 必需），full name
    （ID 4）直接等于族名，不再冗余拼 "Regular"。
    """
    name = font["name"]
    for nid in (1, 2, 4, 6, 16, 17):
        name.removeNames(nameID=nid)
    name.setName(NEW_FAMILY, 1, 3, 1, 0x409)
    name.setName("Regular", 2, 3, 1, 0x409)
    name.setName(NEW_FAMILY, 4, 3, 1, 0x409)
    name.setName(NEW_PS_NAME, 6, 3, 1, 0x409)
    name.setName(NEW_FAMILY, 16, 3, 1, 0x409)
    name.setName("Regular", 17, 3, 1, 0x409)
    print(f"字体重命名: family={NEW_FAMILY}，style=Regular，PS={NEW_PS_NAME}")


def apply_version_metadata(font):
    """写入版本与来源标识：head.fontRevision、name ID 3/5/8/11、OS/2.achVendID。

    上游把这几个字段留在了 2022 年的 FontCreator 构建上（`Version 6.250;May 4,
    2022;FontCreator ...`），无法区分本仓库的任何一次改动。这里以 FONT_VERSION
    为准统一重写，方便按版本号判断装的是哪一版；版权/商标/许可（name ID 0/7/13/14）
    保持上游原样。
    """
    major, minor = FONT_VERSION.split(".")
    font["head"].fontRevision = float(f"{major}.{minor}")
    name = font["name"]
    for nid in (3, 5, 8, 11):
        name.removeNames(nameID=nid)
    name.setName(f"{NEW_FAMILY};{FONT_VERSION}", 3, 3, 1, 0x409)
    name.setName(
        f"Version {FONT_VERSION};{BUILD_TOOL} (base {BASE_VERSION})", 5, 3, 1, 0x409
    )
    name.setName(VENDOR_NAME, 8, 3, 1, 0x409)
    name.setName(VENDOR_URL, 11, 3, 1, 0x409)
    font["OS/2"].achVendID = VENDOR_ID
    print(
        f"版本标识: Version {FONT_VERSION} (base {BASE_VERSION})，"
        f"vendor={VENDOR_ID}，{VENDOR_URL}"
    )


def declare_fixed_pitch(font):
    """恢复上游语义：半角格与全角格各自内部等宽（duospaced）。

    上游 Microsoft-YaHei-Mono 与本机的 Sarasa Mono SC / Maple Mono NF CN /
    Cascadia Next SC 都声明 isFixedPitch=1 + panose.bProportion=9，尽管各自
    都保留了少量设计特例的离格 advance。真正决定终端对齐的是 advance 网格，
    这两个字段只影响 Linux fontconfig 的字体匹配与编辑器的 mono 筛选。
    """
    font["post"].isFixedPitch = 1
    font["OS/2"].panose.bProportion = 9
    print("等宽声明: post.isFixedPitch=1, OS/2.panose.bProportion=9")


def verify_font(font):
    """保存前自检：advance 网格、半角格越界、等宽声明、hhea 派生度量。"""
    problems = []
    hmtx, glyf, cmap = font["hmtx"], font["glyf"], font.getBestCmap()

    # 1) advance 只允许 0/1024/2048；白名单之外的离格值即回归
    unexpected = sorted(
        (cp, hmtx[gname][0]) for cp, gname in cmap.items()
        if hmtx[gname][0] not in (0, SYMBOL_TARGET_ADVANCE, 2048)
        and KNOWN_OFFGRID_CODEPOINTS.get(cp) != hmtx[gname][0]
    )
    if unexpected:
        problems.append(
            f"出现 {len(unexpected)} 个非网格 advance: "
            + ", ".join(f"U+{cp:04X}={adv}" for cp, adv in unexpected[:10])
        )

    # 2) 块元素/几何图形必须已归一化到半角格且不越界
    stray = []
    for cp, gname in cmap.items():
        in_block = BLOCK_ELEMENT_RANGE[0] <= cp <= BLOCK_ELEMENT_RANGE[1]
        in_geo = GEOMETRIC_RANGE[0] <= cp <= GEOMETRIC_RANGE[1]
        if not (in_block or in_geo):
            continue
        advance = hmtx[gname][0]
        if advance != SYMBOL_TARGET_ADVANCE:
            stray.append(f"U+{cp:04X}=adv{advance}")
            continue
        g = glyf[gname]
        if g.numberOfContours and (g.xMin < 0 or g.xMax > advance):
            stray.append(f"U+{cp:04X}=bbox[{g.xMin},{g.xMax}]")
    if stray:
        problems.append("块元素/几何图形未落到半角格: " + ", ".join(stray[:10]))

    # 2b) 块元素纵向必须落在整格内；满块字形（█ ▉ ▐ ▕）要贴满整格
    lo, hi = BLOCK_TARGET_BOX
    for cp in sorted(BLOCK_SOURCE_FAMILY):
        gname = cmap.get(cp)
        if gname is None:
            continue
        g = glyf[gname]
        if g.numberOfContours == 0:
            continue
        if g.yMin < lo - 2 or g.yMax > hi + 2:
            problems.append(f"U+{cp:04X} 纵向越出整格: [{g.yMin}, {g.yMax}]")
    for cp in BLOCK_FULL_CODEPOINTS:
        gname = cmap.get(cp)
        if gname is None:
            continue
        g = glyf[gname]
        if abs(g.yMin - lo) > 2 or abs(g.yMax - hi) > 2:
            problems.append(f"U+{cp:04X} 未贴满整格: [{g.yMin}, {g.yMax}]")

    # 3) 全角字形不得越界（半角拉丁字形的历史越界不在本次修复范围内）
    over = [
        gname for gname in font.getGlyphOrder()
        if hmtx[gname][0] == 2048 and glyf[gname].numberOfContours
        and (glyf[gname].xMin < 0 or glyf[gname].xMax > 2048)
    ]
    if over:
        problems.append(f"全角字形越界 {len(over)} 个: {over[:5]}")

    # 4) 等宽声明
    if font["post"].isFixedPitch != 1:
        problems.append(f"post.isFixedPitch={font['post'].isFixedPitch}，应为 1")
    panose = font["OS/2"].panose.bProportion
    if panose != 9:
        problems.append(f"OS/2.panose.bProportion={panose}，应为 9")

    # 5) hhea 派生度量与实测一致
    hhea = font["hhea"]
    lsb_min = rsb_min = extent_max = None
    for gname in font.getGlyphOrder():
        g = glyf[gname]
        if g.numberOfContours == 0:
            continue
        advance, lsb = hmtx[gname]
        ink = g.xMax - g.xMin
        rsb = advance - lsb - ink
        lsb_min = lsb if lsb_min is None else min(lsb_min, lsb)
        rsb_min = rsb if rsb_min is None else min(rsb_min, rsb)
        extent = lsb + ink
        extent_max = extent if extent_max is None else max(extent_max, extent)
    if lsb_min is not None:
        expected = (lsb_min, rsb_min, extent_max)
        actual = (hhea.minLeftSideBearing, hhea.minRightSideBearing, hhea.xMaxExtent)
        if expected != actual:
            problems.append(f"hhea 派生度量过期: 期望 {expected}，实际 {actual}")

    # 6) 版本标识一致：head.fontRevision 与 name ID 3/5 必须同源
    major, minor = FONT_VERSION.split(".")
    expect_rev = float(f"{major}.{minor}")
    if abs(font["head"].fontRevision - expect_rev) > 1 / 65536:
        problems.append(
            f"head.fontRevision={font['head'].fontRevision}，应为 {FONT_VERSION}"
        )
    for nid, expect_prefix in ((5, f"Version {FONT_VERSION}"), (3, NEW_FAMILY)):
        values = [r.toUnicode() for r in font["name"].names if r.nameID == nid]
        if not values or any(not v.startswith(expect_prefix) for v in values):
            problems.append(f"name ID {nid} 不是 {expect_prefix} 开头: {values}")
    if font["OS/2"].achVendID != VENDOR_ID:
        problems.append(f"achVendID={font['OS/2'].achVendID!r}，应为 {VENDOR_ID!r}")

    if problems:
        raise SystemExit("自检失败:\n  - " + "\n  - ".join(problems))
    print("自检通过: advance 网格 / 半角格越界 / 块元素整格 / 版本标识 / 等宽声明 / hhea 派生度量")


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

    # ---- 5b. 把 1229 档的块元素/几何图形归一到 1024 半角格 ----
    normalize_half_width_symbols(font, all_glyphs)

    # ---- 5c. 把块元素的纵向盒子统一到整格（四个来源的高度各不相同）----
    unify_block_element_box(font, all_glyphs)

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

    # ---- 7b. 归一化改变了 lsb/rsb，重算 hhea 的派生字段 ----
    recalc_hhea_metrics(font)

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
    rename_family(font)

    # ---- 13. 版本标识 + 等宽声明 + 保存前自检 ----
    apply_version_metadata(font)
    declare_fixed_pitch(font)
    verify_font(font)

    font.save(out)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
