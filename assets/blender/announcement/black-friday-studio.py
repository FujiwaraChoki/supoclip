"""Black Friday Studio: a creator in their own studio cubicle, seen as a small clay
diorama floating in the dark. They type at a desk under a neon strip that runs
through the five announcement colours; their monitor shows the clip editor (a 9:16
preview with word-by-word captions, clip cards and a timeline). The right half is
left dark for the copy, which is set over the frames afterwards from
black-friday-copy.html (see the README).

Every motion comes from the loop phase t = (frame - 1) / FRAMES (typing, the
playhead, the caption word, the strip's colours, the camera drift), so frame
FRAMES + 1 equals frame 1.

    blender -b -P black-friday-studio.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]
"""

import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/black-friday-studio"
SAMPLES = int(argv[1]) if len(argv) > 1 else 128
SPEC = argv[2] if len(argv) > 2 else "all"
RES = int(argv[3]) if len(argv) > 3 else 100
FRAMES = 120
TAU = math.tau

os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def parse_frames(spec):
    if spec == "all":
        return list(range(1, FRAMES + 1))
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


# orange, magenta, violet, blue, mint: the announcement palette
COLS = [(1.0, 0.32, 0.0), (1.0, 0.02, 0.32), (0.45, 0.02, 1.0), (0.0, 0.32, 1.0), (0.0, 0.85, 0.55)]
ORANGE = COLS[0]


# ---------------------------------------------------------------- materials
def principled(name, color, rough=0.6, sheen=0.0, coat=0.0, emit=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    if sheen:
        b.inputs["Sheen Weight"].default_value = sheen
    if coat:
        b.inputs["Coat Weight"].default_value = coat
    if emit is not None:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


def emission(name, color, strength):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1)
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m, em


CLAY_DARK = principled("clay_dark", (0.018, 0.017, 0.016), 0.75)
CLAY_WALL = principled("clay_wall", (0.03, 0.028, 0.027), 0.8)
FOAM_A = principled("foam_a", (0.022, 0.02, 0.02), 0.95, sheen=0.4)
FOAM_B = principled("foam_b", (0.06, 0.012, 0.03), 0.95, sheen=0.4)
WOOD = principled("desk", (0.09, 0.055, 0.035), 0.45, coat=0.3)
METAL = principled("metal", (0.05, 0.05, 0.055), 0.3)
SKIN = principled("skin", (0.55, 0.33, 0.22), 0.55, sheen=0.2)
HAIR = principled("hair", (0.02, 0.014, 0.01), 0.6, sheen=0.5)
HOODIE = principled("hoodie", (0.11, 0.105, 0.1), 0.85, sheen=0.6)
PANTS = principled("pants", (0.025, 0.025, 0.03), 0.8, sheen=0.3)
SHOE = principled("shoe", (0.8, 0.78, 0.74), 0.5)
CHAIR = principled("chair", (0.015, 0.015, 0.017), 0.5, coat=0.4)
CHAIR_ACCENT = principled("chair_accent", ORANGE, 0.5)
PLANT = principled("plant", (0.03, 0.16, 0.06), 0.6, sheen=0.3)
POT = principled("pot", (0.75, 0.72, 0.68), 0.5)
BEZEL = principled("bezel", (0.01, 0.01, 0.012), 0.35, coat=0.6)
CUP = principled("cup", ORANGE, 0.4, coat=0.5)


# ---------------------------------------------------------------- geometry helpers
def link(ob):
    scene.collection.objects.link(ob)
    return ob


def box(name, x0, x1, y0, y1, z0, z1, mat, bevel=0.015):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = x0 + (v.co.x + 0.5) * (x1 - x0)
        v.co.y = y0 + (v.co.y + 0.5) * (y1 - y0)
        v.co.z = z0 + (v.co.z + 0.5) * (z1 - z0)
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    ob.data.materials.append(mat)
    if bevel:
        mod = ob.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
    return ob


def sphere(name, center, r, mat, scale=(1, 1, 1), segs=48):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=segs // 2, radius=r)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = link(bpy.data.objects.new(name, me))
    ob.location = center
    ob.scale = scale
    ob.data.materials.append(mat)
    return ob


def capsule_mesh(name, r, length, segs=32):
    """A capsule along +z from 0 to `length`."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=segs // 2 + 1, radius=r)
    for v in bm.verts:
        if v.co.z > 1e-6:
            v.co.z += length
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    return me


def align(ob, p1, p2):
    """Put a +z capsule from p1 to p2 (its length must already match)."""
    a, b = Vector(p1), Vector(p2)
    d = (b - a).normalized()
    ob.location = a
    ob.rotation_mode = "QUATERNION"
    ob.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d)


def capsule(name, p1, p2, r, mat):
    length = (Vector(p2) - Vector(p1)).length
    ob = link(bpy.data.objects.new(name, capsule_mesh(name, r, length)))
    ob.data.materials.append(mat)
    align(ob, p1, p2)
    return ob


def cylinder(name, center, r, depth, mat, rot=(0, 0, 0), verts=48):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=r, radius2=r, depth=depth)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = len(p.vertices) == 4
    ob = link(bpy.data.objects.new(name, me))
    ob.location = center
    ob.rotation_euler = rot
    ob.data.materials.append(mat)
    return ob


def torus(name, center, R, r, mat, rot=(0, 0, 0), arc=TAU):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    seg_u, seg_v = 96, 20
    rings = []
    closed = arc >= TAU - 1e-6
    n_u = seg_u if closed else seg_u + 1
    for i in range(n_u):
        a = arc * i / seg_u - (0 if closed else arc / 2)
        ring = []
        for j in range(seg_v):
            b = TAU * j / seg_v
            x = (R + r * math.cos(b)) * math.cos(a)
            y = (R + r * math.cos(b)) * math.sin(a)
            z = r * math.sin(b)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
    for i in range(n_u if closed else n_u - 1):
        r0, r1 = rings[i], rings[(i + 1) % n_u]
        for j in range(seg_v):
            bm.faces.new((r0[j], r0[(j + 1) % seg_v], r1[(j + 1) % seg_v], r1[j]))
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = link(bpy.data.objects.new(name, me))
    ob.location = center
    ob.rotation_euler = rot
    ob.data.materials.append(mat)
    return ob


# ---------------------------------------------------------------- the cubicle
ROOM_X0, ROOM_X1 = -2.0, 0.75
ROOM_Y0, ROOM_Y1 = -1.45, 1.6
WALL_T, WALL_H = 0.12, 2.5

box("floor", ROOM_X0, ROOM_X1, ROOM_Y0, ROOM_Y1, -0.18, 0.0, CLAY_DARK, bevel=0.04)
box("wall_back", ROOM_X0, ROOM_X1, ROOM_Y1 - WALL_T, ROOM_Y1, 0.0, WALL_H, CLAY_WALL, bevel=0.03)
box("wall_left", ROOM_X0, ROOM_X0 + WALL_T, ROOM_Y0, ROOM_Y1, 0.0, WALL_H, CLAY_WALL, bevel=0.03)
# a rug under the chair
rug = cylinder("rug", (-0.45, 0.2, 0.006), 0.8, 0.012, principled("rug", (0.05, 0.02, 0.035), 0.95, sheen=0.8), verts=96)

# acoustic panels on both walls: a checker of two dark felts
P = 0.3
for i in range(9):
    for j in range(4):
        x = ROOM_X0 + 0.3 + i * (P + 0.03)
        z = 0.92 + j * (P + 0.03)
        if x + P > ROOM_X1 - 0.15:
            continue
        box(f"foam_b{i}_{j}", x, x + P, ROOM_Y1 - WALL_T - 0.05, ROOM_Y1 - WALL_T, z, z + P,
            FOAM_B if (i + j) % 2 else FOAM_A, bevel=0.02)
for i in range(9):
    for j in range(4):
        y = ROOM_Y0 + 0.25 + i * (P + 0.03)
        z = 0.92 + j * (P + 0.03)
        if y + P > ROOM_Y1 - WALL_T - 0.1:
            continue
        box(f"foam_l{i}_{j}", ROOM_X0 + WALL_T, ROOM_X0 + WALL_T + 0.05, y, y + P, z, z + P,
            FOAM_B if (i + j) % 2 else FOAM_A, bevel=0.02)

# ---------------------------------------------------------------- the neon strip
# A strip along the top of both walls whose colour cycles through the palette.
strip_mat = bpy.data.materials.new("strip")
strip_mat.use_nodes = True
snt = strip_mat.node_tree
for n in list(snt.nodes):
    snt.nodes.remove(n)
s_out = snt.nodes.new("ShaderNodeOutputMaterial")
s_em = snt.nodes.new("ShaderNodeEmission")
s_em.inputs["Strength"].default_value = 18.0
s_geo = snt.nodes.new("ShaderNodeNewGeometry")
s_sep = snt.nodes.new("ShaderNodeSeparateXYZ")
s_add = snt.nodes.new("ShaderNodeMath")
s_add.operation = "ADD"
s_phase = snt.nodes.new("ShaderNodeValue")
s_phase.name = "PHASE"
s_scale = snt.nodes.new("ShaderNodeMath")
s_scale.operation = "MULTIPLY_ADD"
s_scale.inputs[1].default_value = 0.22
s_frac = snt.nodes.new("ShaderNodeMath")
s_frac.operation = "FRACT"
s_ramp = snt.nodes.new("ShaderNodeValToRGB")
ramp = s_ramp.color_ramp
ramp.interpolation = "EASE"
stops = COLS + [COLS[0]]
while len(ramp.elements) < len(stops):
    ramp.elements.new(0.5)
for k, c in enumerate(stops):
    ramp.elements[k].position = k / (len(stops) - 1)
    ramp.elements[k].color = (*c, 1)
snt.links.new(s_geo.outputs["Position"], s_sep.inputs[0])
# distance along the strip: x on the back wall plus y on the left wall
snt.links.new(s_sep.outputs[0], s_add.inputs[0])
snt.links.new(s_sep.outputs[1], s_add.inputs[1])
snt.links.new(s_add.outputs[0], s_scale.inputs[0])
snt.links.new(s_phase.outputs[0], s_scale.inputs[2])
snt.links.new(s_scale.outputs[0], s_frac.inputs[0])
snt.links.new(s_frac.outputs[0], s_ramp.inputs["Fac"])
snt.links.new(s_ramp.outputs["Color"], s_em.inputs["Color"])
snt.links.new(s_em.outputs[0], s_out.inputs["Surface"])

STRIP_Z = 2.32
box("strip_back", ROOM_X0 + WALL_T, ROOM_X1 - 0.05, ROOM_Y1 - WALL_T - 0.035, ROOM_Y1 - WALL_T, STRIP_Z, STRIP_Z + 0.035, strip_mat, bevel=0.008)
box("strip_left", ROOM_X0 + WALL_T, ROOM_X0 + WALL_T + 0.035, ROOM_Y0 + 0.05, ROOM_Y1 - WALL_T, STRIP_Z, STRIP_Z + 0.035, strip_mat, bevel=0.008)

# ---------------------------------------------------------------- the desk
DESK_Z = 0.76
DX0, DX1 = ROOM_X0 + WALL_T, -0.95
DY0, DY1 = -0.95, 1.2
box("desk_top", DX0, DX1, DY0, DY1, DESK_Z - 0.045, DESK_Z, WOOD, bevel=0.012)
for y in (DY0 + 0.06, DY1 - 0.06):
    box(f"desk_leg{y:.1f}", DX1 - 0.09, DX1 - 0.04, y - 0.025, y + 0.025, 0.0, DESK_Z - 0.045, METAL, bevel=0.008)
# a strip of the same neon under the desk edge
box("strip_desk", DX1 - 0.03, DX1 - 0.005, DY0 + 0.05, DY1 - 0.05, DESK_Z - 0.07, DESK_Z - 0.05, strip_mat, bevel=0.004)

# ---------------------------------------------------------------- the monitor
# Turned from facing +x towards the camera, so the screen reads over the creator's shoulder.
MON_YAW = math.radians(-32)
MON_C = Vector((-1.5, 0.28, 1.2))
MON_W, MON_H = 1.3, 0.7
MON_N = Vector((math.cos(MON_YAW), math.sin(MON_YAW), 0))       # screen normal
MON_R = Vector((-math.sin(MON_YAW), math.cos(MON_YAW), 0))      # screen's right, seen from the front: towards +y side


def on_screen(u, v, depth=0.0):
    """A point on the screen: u, v in [-0.5, 0.5] across its width and height, `depth` out of it."""
    return MON_C + MON_R * (-u * MON_W) + Vector((0, 0, v * MON_H)) + MON_N * depth


screen_root = bpy.data.objects.new("screen_root", None)
link(screen_root)
screen_root.location = MON_C
screen_root.rotation_euler = (math.pi / 2, 0, MON_YAW + math.pi / 2)  # local +z = screen normal, +x = screen right

bezel = box("bezel", -MON_W / 2 - 0.03, MON_W / 2 + 0.03, -MON_H / 2 - 0.03, MON_H / 2 + 0.03, -0.05, -0.002, BEZEL, bevel=0.012)
bezel.parent = screen_root
screen_bg, screen_bg_em = emission("screen_bg", (0.03, 0.03, 0.04), 1.0)
scr = box("screen", -MON_W / 2, MON_W / 2, -MON_H / 2, MON_H / 2, -0.003, 0.0, screen_bg, bevel=0)
scr.parent = screen_root
# stand
cylinder("mon_neck", MON_C - MON_N * 0.08 + Vector((0, 0, -0.33)), 0.025, 0.34, METAL)
cylinder("mon_base", MON_C - MON_N * 0.08 + Vector((0, 0, -(MON_C.z - DESK_Z) + 0.006)), 0.15, 0.012, METAL, verts=64)


def screen_plane(name, x0, x1, y0, y1, mat, z=0.001):
    ob = box(name, x0, x1, y0, y1, z, z + 0.0005, mat, bevel=0)
    ob.parent = screen_root
    return ob


# the editor on the screen: a top bar, a 9:16 preview, a timeline of five clips and a playhead
ui_grey, _ = emission("ui_grey", (0.14, 0.14, 0.15), 1.0)
ui_dim, _ = emission("ui_dim", (0.07, 0.07, 0.08), 1.0)
screen_plane("ui_bar", -MON_W / 2, MON_W / 2, MON_H / 2 - 0.035, MON_H / 2, ui_dim)
for k in range(3):
    dot, _ = emission(f"ui_dot{k}", COLS[k] if k == 0 else (0.25, 0.25, 0.27), 2.0)
    d = sphere(f"ui_dot{k}", (0, 0, 0), 0.007, dot, segs=16)
    d.parent = screen_root
    d.location = (-MON_W / 2 + 0.025 + k * 0.022, MON_H / 2 - 0.0175, 0.002)
# the clip being edited: a 9:16 preview with a lit subject and two caption lines,
# and a column of clip cards on the right
prev, _ = emission("ui_preview", (0.09, 0.08, 0.1), 1.0)
PV_W, PV_H = 0.2, 0.355
PV_X, PV_Y = -0.1, 0.03
screen_plane("ui_preview", PV_X - PV_W / 2, PV_X + PV_W / 2, PV_Y - PV_H / 2, PV_Y + PV_H / 2, prev)
subj, _ = emission("ui_subject", COLS[2], 1.4)
s_head = sphere("ui_subject_head", (0, 0, 0), 0.03, subj, segs=24)
s_head.parent = screen_root
s_head.location = (PV_X, PV_Y + 0.05, 0.0015)
s_head.scale = (1, 1, 0.05)
s_body = sphere("ui_subject_body", (0, 0, 0), 0.065, subj, segs=24)
s_body.parent = screen_root
s_body.location = (PV_X, PV_Y - 0.06, 0.0015)
s_body.scale = (1, 0.75, 0.05)
cap_white, _ = emission("ui_caption", (1, 1, 1), 2.2)
cap_hi, cap_hi_em = emission("ui_caption_word", COLS[0], 3.0)
screen_plane("ui_cap1", PV_X - 0.07, PV_X + 0.07, PV_Y - 0.13, PV_Y - 0.115, cap_white, z=0.002)
screen_plane("ui_cap2", PV_X - 0.05, PV_X + 0.05, PV_Y - 0.152, PV_Y - 0.137, cap_white, z=0.002)
# the word being spoken, highlighted, steps along the first caption line
cap_word = screen_plane("ui_cap_word", -0.022, 0.022, PV_Y - 0.132, PV_Y - 0.113, cap_hi, z=0.0025)
for k in range(4):
    y = 0.17 - k * 0.075
    screen_plane(f"ui_card{k}", 0.1, 0.5, y - 0.03, y + 0.03, ui_grey if k == 1 else ui_dim)
    m, _ = emission(f"ui_card_dot{k}", COLS[k + 1], 1.6)
    screen_plane(f"ui_card_thumb{k}", 0.11, 0.14, y - 0.022, y + 0.022, m, z=0.0015)
    screen_plane(f"ui_card_line{k}", 0.155, 0.155 + (0.26 if k % 2 else 0.2), y + 0.004, y + 0.012, ui_grey if k != 1 else cap_white, z=0.0015)
    screen_plane(f"ui_card_line_b{k}", 0.155, 0.155 + 0.12, y - 0.014, y - 0.006, ui_dim if k != 1 else ui_grey, z=0.0015)
# timeline
TL_Y0, TL_Y1 = -MON_H / 2 + 0.035, -MON_H / 2 + 0.085
TL_X0, TL_X1 = -MON_W / 2 + 0.04, MON_W / 2 - 0.04
screen_plane("ui_track", TL_X0, TL_X1, TL_Y0 - 0.008, TL_Y1 + 0.008, ui_dim)
widths = [0.17, 0.24, 0.14, 0.2, 0.18]
total = sum(widths) + 0.012 * (len(widths) - 1)
x = TL_X0
clip_mats = []
for k, w in enumerate(widths):
    w = w * (TL_X1 - TL_X0) / total
    m, em = emission(f"ui_clip{k}", COLS[k], 1.6)
    clip_mats.append(em)
    screen_plane(f"ui_clip{k}", x, x + w, TL_Y0, TL_Y1, m, z=0.0015)
    x += w + 0.012 * (TL_X1 - TL_X0) / total
play_mat, _ = emission("ui_playhead", (1, 1, 1), 4.0)
playhead = screen_plane("ui_playhead", -0.002, 0.002, TL_Y0 - 0.02, TL_Y1 + 0.02, play_mat, z=0.0025)

# glow the screen throws on the creator
screen_light = bpy.data.lights.new("screen_light", "AREA")
screen_light.shape = "RECTANGLE"
screen_light.size, screen_light.size_y = MON_W, MON_H
screen_light.energy = 60
screen_light.color = (0.85, 0.9, 1.0)
sl = link(bpy.data.objects.new("screen_light", screen_light))
sl.location = on_screen(0, 0, 0.02)
sl.rotation_euler = MON_N.to_track_quat("-Z", "Z").to_euler()  # area lights emit along -z

# keyboard and mouse
KB = MON_C + MON_N * 0.36
kb_rot = (0, 0, MON_YAW + math.pi / 2)
kb = box("keyboard", -0.22, 0.22, -0.07, 0.07, 0, 0.022, principled("keys", (0.06, 0.06, 0.065), 0.5), bevel=0.008)
kb.location = (KB.x, KB.y, DESK_Z)
kb.rotation_euler = kb_rot
keyglow, _ = emission("keyglow", COLS[2], 2.0)
kbg = box("keyboard_glow", -0.215, 0.215, -0.065, 0.065, 0.0225, 0.023, keyglow, bevel=0)
kbg.location = kb.location
kbg.rotation_euler = kb_rot
mouse = sphere("mouse", (0, 0, 0), 0.04, principled("mouse", (0.06, 0.06, 0.065), 0.4), scale=(1.4, 0.9, 0.45))
mouse.location = KB + MON_R * -0.34 + Vector((0, 0, DESK_Z + 0.012 - KB.z))
mouse.rotation_euler = (0, 0, MON_YAW)

# mug, plant, ring light
mug = cylinder("mug", (-1.72, -0.55, DESK_Z + 0.055), 0.045, 0.11, CUP)
pot = cylinder("pot", (-1.75, 0.95, DESK_Z + 0.08), 0.09, 0.16, POT)
for k in range(9):
    a = TAU * k / 9
    capsule(f"leaf{k}", (-1.75, 0.95, DESK_Z + 0.15),
            (-1.75 + 0.2 * math.cos(a), 0.95 + 0.2 * math.sin(a), DESK_Z + 0.42 + 0.06 * math.sin(3 * a)), 0.035, PLANT)
ring_mat, ring_em = emission("ring", (1.0, 0.92, 0.82), 9.0)
RING_C = Vector((-1.62, 1.05, 1.42))
ring = torus("ring_light", RING_C, 0.2, 0.02, ring_mat)
ring.rotation_euler = (Vector((-0.2, -0.6, 1.25)) - RING_C).to_track_quat("Z", "Y").to_euler()
cylinder("ring_pole", (RING_C.x, RING_C.y, (RING_C.z - 0.2 + DESK_Z) / 2), 0.012, RING_C.z - 0.2 - DESK_Z, METAL)

# a shelf on the back wall with a few boxes and a tiny framed clip
box("shelf", -0.95, 0.35, ROOM_Y1 - WALL_T - 0.2, ROOM_Y1 - WALL_T, 1.9, 1.93, WOOD, bevel=0.008)
for k, (x0, w, h, c) in enumerate([(-0.9, 0.09, 0.24, COLS[3]), (-0.8, 0.07, 0.2, (0.6, 0.58, 0.55)), (-0.72, 0.1, 0.27, COLS[1])]):
    box(f"book{k}", x0, x0 + w, ROOM_Y1 - WALL_T - 0.18, ROOM_Y1 - WALL_T - 0.02, 1.93, 1.93 + h, principled(f"book{k}", c, 0.6), bevel=0.006)
frame = box("frame", -0.2, 0.0, ROOM_Y1 - WALL_T - 0.09, ROOM_Y1 - WALL_T - 0.05, 1.93, 2.28, BEZEL, bevel=0.006)
fr_mat, _ = emission("frame_img", COLS[4], 1.2)
box("frame_img", -0.18, -0.02, ROOM_Y1 - WALL_T - 0.096, ROOM_Y1 - WALL_T - 0.09, 1.95, 2.26, fr_mat, bevel=0)

# ---------------------------------------------------------------- the creator
# Seated on a chair facing the screen, a little to the side so the screen reads past them.
FACE = -MON_N                       # where the creator looks
SIDE = Vector((-FACE.y, FACE.x, 0))  # their left
SEAT = MON_C + MON_N * 0.92 + SIDE * -0.36
SEAT.z = 0.0
HIP = SEAT + Vector((0, 0, 0.52))

# chair
seat = box("chair_seat", -0.24, 0.24, -0.24, 0.24, 0.42, 0.5, CHAIR, bevel=0.04)
seat.location = (SEAT.x, SEAT.y, 0)
seat.rotation_euler = (0, 0, math.atan2(FACE.y, FACE.x))
back_c = SEAT - FACE * 0.24
backrest = box("chair_back", -0.04, 0.04, -0.23, 0.23, 0.5, 1.12, CHAIR, bevel=0.04)
backrest.location = (back_c.x, back_c.y, 0)
backrest.rotation_euler = (0, math.radians(-8), math.atan2(FACE.y, FACE.x))
stripe = box("chair_stripe", -0.045, 0.045, -0.035, 0.035, 0.55, 1.08, CHAIR_ACCENT, bevel=0.01)
stripe.location = (back_c.x, back_c.y, 0)
stripe.rotation_euler = backrest.rotation_euler
cylinder("chair_pole", (SEAT.x, SEAT.y, 0.24), 0.03, 0.36, METAL)
for k in range(5):
    a = TAU * k / 5
    capsule(f"chair_leg{k}", (SEAT.x, SEAT.y, 0.07), (SEAT.x + 0.28 * math.cos(a), SEAT.y + 0.28 * math.sin(a), 0.05), 0.02, METAL)
    sphere(f"chair_wheel{k}", (SEAT.x + 0.28 * math.cos(a), SEAT.y + 0.28 * math.sin(a), 0.03), 0.03, METAL)

up = Vector((0, 0, 1))
CHEST = HIP + FACE * 0.1 + up * 0.42
NECK = CHEST + FACE * 0.03 + up * 0.14
HEAD = NECK + FACE * 0.02 + up * 0.14

torso = capsule("torso", HIP + up * 0.04, CHEST, 0.19, HOODIE)
torso.scale = (1.0, 1.0, 1.0)
hood = torus("hood", CHEST - FACE * 0.05 + up * 0.07, 0.11, 0.05, HOODIE, rot=(math.radians(70), 0, math.atan2(FACE.y, FACE.x) + math.pi / 2))
neck = capsule("neck", CHEST, NECK, 0.06, SKIN)
head = sphere("head", HEAD, 0.125, SKIN, scale=(1.0, 1.0, 1.08))
hair = sphere("hair", HEAD + up * 0.025 - FACE * 0.02, 0.13, HAIR, scale=(1.02, 1.02, 0.92))
# headphones: a band over the top and two cups
hp_mat = principled("headphones", (0.02, 0.02, 0.022), 0.3, coat=0.6)
band = torus("hp_band", HEAD + up * 0.005, 0.15, 0.016, hp_mat, arc=math.pi * 1.05)
# the arc is centred on local +x and lies in local xy: point x up and y to the side
band.rotation_euler = Matrix((up, SIDE, up.cross(SIDE))).transposed().to_euler()
for s in (1, -1):
    cup = cylinder(f"hp_cup{s}", HEAD + SIDE * (0.135 * s) + up * -0.01, 0.055, 0.045, hp_mat,
                   rot=(math.pi / 2, 0, math.atan2(SIDE.y, SIDE.x) + math.pi / 2))
    glow, _ = emission(f"hp_glow{s}", ORANGE, 4.0)
    cylinder(f"hp_ring{s}", HEAD + SIDE * (0.16 * s) + up * -0.01, 0.03, 0.004, glow,
             rot=(math.pi / 2, 0, math.atan2(SIDE.y, SIDE.x) + math.pi / 2))

# legs: thighs forward, shins down
legs = []
for s in (1, -1):
    hip = HIP + SIDE * (0.1 * s)
    knee = hip + FACE * 0.44 + up * 0.0
    foot = knee + FACE * 0.06 - up * 0.47
    capsule(f"thigh{s}", hip, knee, 0.085, PANTS)
    capsule(f"shin{s}", knee, foot, 0.07, PANTS)
    shoe = sphere(f"shoe{s}", foot + FACE * 0.06 + up * -0.01, 0.07, SHOE, scale=(1, 1, 0.55))
    shoe.rotation_euler = (0, 0, math.atan2(FACE.y, FACE.x))
    shoe.scale = (1.5, 0.85, 0.55)

# arms: rebuilt every frame so the hands type
ARM_SEG = {}
for s in (1, -1):
    for part, r, mat in (("upper", 0.065, HOODIE), ("fore", 0.055, HOODIE)):
        ARM_SEG[(s, part)] = (r, mat)
arm_obs = {}
hands = {}


def place_arms(t):
    for s in (1, -1):
        shoulder = CHEST + SIDE * (0.2 * s) - up * 0.04
        # hands rest on the keyboard and tap out of phase
        tap = max(0.0, math.sin(TAU * (6 * t) + (0 if s > 0 else 1.7))) ** 3
        hand = KB + SIDE * (0.11 * s) + up * (0.05 + 0.018 * tap) - FACE * 0.02
        hand.z = DESK_Z + 0.04 + 0.018 * tap
        elbow = shoulder + (hand - shoulder) * 0.5 - up * 0.13 + SIDE * (0.07 * s) - FACE * 0.08
        for part, a, b in (("upper", shoulder, elbow), ("fore", elbow, hand)):
            r, mat = ARM_SEG[(s, part)]
            key = (s, part)
            if key in arm_obs:
                ob = arm_obs[key]
                bpy.data.objects.remove(ob, do_unlink=True)
            arm_obs[key] = capsule(f"arm_{part}{s}", a, b, r, mat)
        if s not in hands:
            hands[s] = sphere(f"hand{s}", hand, 0.045, SKIN, scale=(1.3, 1.0, 0.65))
        hands[s].location = hand
        hands[s].rotation_euler = (0, 0, math.atan2(FACE.y, FACE.x))


# ---------------------------------------------------------------- lights and world
def area(name, loc, target, size, energy, color):
    data = bpy.data.lights.new(name, "AREA")
    data.size = size
    data.energy = energy
    data.color = color
    ob = link(bpy.data.objects.new(name, data))
    ob.location = loc
    d = Vector(target) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


area("key", (3.5, -3.0, 5.0), (-0.6, 0.2, 0.8), 3.0, 560, (1.0, 0.93, 0.86))
area("rim", (-0.4, 3.5, 3.4), (-0.5, -0.2, 1.0), 2.0, 260, (0.55, 0.35, 1.0))
area("fill", (2.0, -4.5, 1.2), (-0.6, 0.2, 1.0), 4.0, 60, (1.0, 0.55, 0.3))

world = bpy.data.worlds.new("world")
scene.world = world
world.use_nodes = True
wn = world.node_tree
bg = wn.nodes["Background"]
bg.inputs["Color"].default_value = (0.004, 0.0038, 0.0036, 1)
bg.inputs["Strength"].default_value = 1.0

# ---------------------------------------------------------------- camera
TARGET = Vector((-0.65, 0.1, 1.0))
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam_data.sensor_fit = "HORIZONTAL"
cam = link(bpy.data.objects.new("cam", cam_data))
cam.location = (6.0, -8.6, 5.0)
aim = link(bpy.data.objects.new("aim", None))
scene.camera = cam
# The diorama sits in the left half of the frame: aim to the right of it.
d = (TARGET - Vector(cam.location)).normalized()
right = d.cross(up).normalized()
cam_dist = (TARGET - Vector(cam.location)).length
aim.location = TARGET + right * (cam_dist * 0.185)
CAM_HOME = Vector(cam.location)
AIM_HOME = Vector(aim.location)
tr = cam.constraints.new("TRACK_TO")
tr.target = aim
tr.track_axis = "TRACK_NEGATIVE_Z"
tr.up_axis = "UP_Y"

# ---------------------------------------------------------------- render
scene.render.engine = "CYCLES"
scene.render.resolution_x = 1600
scene.render.resolution_y = 800
scene.render.resolution_percentage = RES
scene.render.image_settings.file_format = "PNG"
scene.render.use_persistent_data = True
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Punchy"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 8
scene.cycles.seed = 5
scene.frame_start, scene.frame_end = 1, FRAMES

prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "OPTIX"
prefs.get_devices()
for dev in prefs.devices:
    dev.use = dev.type == "OPTIX"
if os.environ.get("CYCLES_DEVICE") == "CPU" or not any(dev.use for dev in prefs.devices):
    scene.cycles.device = "CPU"
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
else:
    scene.cycles.device = "GPU"
    scene.cycles.denoiser = "OPTIX"

tree = bpy.data.node_groups.new("comp", "CompositorNodeTree")
scene.compositing_node_group = tree
rl = tree.nodes.new("CompositorNodeRLayers")
glare = tree.nodes.new("CompositorNodeGlare")
for k, v in (("Type", "Bloom"), ("Threshold", 1.0), ("Strength", 0.55), ("Size", 0.7), ("Quality", "High")):
    try:
        glare.inputs[k].default_value = v
    except (KeyError, TypeError, AttributeError):
        pass
cout = tree.nodes.new("NodeGroupOutput")
tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
tree.links.new(glare.outputs[0], cout.inputs[0])

for f in parse_frames(SPEC):
    t = ((f - 1) / FRAMES) % 1.0
    scene.frame_set(f)
    place_arms(t)
    # a slow drift around the diorama, once per loop; the copy rides with the camera
    drift = Matrix.Rotation(math.radians(1.6) * math.sin(TAU * t), 4, "Z")
    cam.location = TARGET + drift @ (CAM_HOME - TARGET) + up * (0.06 * math.sin(TAU * t + 1.0))
    aim.location = TARGET + drift @ (AIM_HOME - TARGET)
    s_phase.outputs[0].default_value = -t
    # the playhead sweeps the timeline once per loop
    playhead.location.x = TL_X0 + (TL_X1 - TL_X0) * t
    # the highlighted caption word steps along the line, four words, twice per loop
    word = int((t * 8) % 4)
    cap_word.location.x = PV_X - 0.048 + word * 0.032
    pulse = 0.5 + 0.5 * math.sin(TAU * 2 * t)
    screen_light.energy = 60 + 10 * pulse
    # head nods a little with the typing
    head.location = HEAD + up * (0.006 * math.sin(TAU * 2 * t))
    hair.location = head.location + up * 0.025 - FACE * 0.02
    scene.render.filepath = os.path.join(OUT, f"frame_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
