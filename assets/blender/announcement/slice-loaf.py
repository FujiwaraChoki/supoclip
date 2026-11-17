"""Slice Loaf: one long glass loaf with a 9:16 cross-section (the long video)
slides into a blade of light. Every cut leaves a thin 9:16 tile that carries its
own clip on its face (a lit subject, a hook bar, two caption lines), turns to
face the camera and marches off in a row: one long video becoming a week of
shorts. Sparks fly at every cut. Glass dispersion is faked like Prism Wall.

The loaf's content is a function of its own length coordinate, periodic over
the N slices cut in one loop, and each tile keeps the stretch it was cut from.
Every motion comes from the loop phase t = (frame - 1) / FRAMES, so frame
FRAMES + 1 equals frame 1.

    blender -b -P slice-loaf.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]
"""

import math
import os
import random
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/slice-loaf"
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


def smooth(a, b, x):
    x = min(1.0, max(0.0, (x - a) / (b - a)))
    return x * x * (3 - 2 * x)


# orange, magenta, violet, blue, mint: the announcement palette
COLS = [(1.0, 0.32, 0.0), (1.0, 0.02, 0.32), (0.45, 0.02, 1.0), (0.0, 0.32, 1.0), (0.0, 0.85, 0.55)]

N = 5                 # tiles cut per loop
W, H = 1.0, 1.778     # 9:16 cross-section (y, z)
S = 0.2               # tile thickness along the loaf
PERIOD = N * S        # the loaf's content repeats over the tiles of one loop
FLOOR = -H / 2 - 0.004
SHELL = 0.035         # glass around the glowing core


# ---------------------------------------------------------------- node helpers
class Nodes:
    def __init__(self, nt):
        self.nt = nt

    def new(self, kind, **props):
        n = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def link(self, a, b):
        self.nt.links.new(a, b)

    def feed(self, socket, value):
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            self.link(value, socket)

    def math(self, op, a, b=None, c=None, clamp=False):
        n = self.new("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        if c is not None:
            self.feed(n.inputs[2], c)
        return n.outputs[0]

    def band(self, x, centre, half, soft):
        """1 inside |x - centre| < half, fading to 0 over `soft`."""
        d = self.math("ABSOLUTE", self.math("SUBTRACT", x, centre))
        return self.math("SUBTRACT", 1.0, self.math("MULTIPLY", self.math("SUBTRACT", d, half), 1.0 / soft), clamp=True)

    def mix(self, a, b, fac, blend="MIX"):
        n = self.new("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        self.feed(n.inputs["Factor"], fac)
        for sock, v in ((n.inputs[6], a), (n.inputs[7], b)):
            if isinstance(v, tuple):
                sock.default_value = (*v, 1)
            else:
                self.link(v, sock)
        return n.outputs[2]


def content_material(name, offset):
    """The clip inside the loaf: emission from the length coordinate xc and the
    cross-section (y, z). `offset` turns object x into xc (a Value node, so it can move)."""
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    g = Nodes(nt)
    out = g.new("ShaderNodeOutputMaterial")
    tc = g.new("ShaderNodeTexCoord")
    sep = g.new("ShaderNodeSeparateXYZ")
    g.link(tc.outputs["Object"], sep.inputs[0])
    x, y, z = sep.outputs["X"], sep.outputs["Y"], sep.outputs["Z"]
    off = g.new("ShaderNodeValue", name="OFFSET")
    off.outputs[0].default_value = offset
    xc = g.math("ADD", x, off.outputs[0])

    # each tile's hue, drifting a little up the frame
    hue = g.math("FRACT", g.math("ADD", g.math("DIVIDE", xc, PERIOD), g.math("MULTIPLY", z, 0.07)))
    ramp = g.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "EASE"
    els = ramp.color_ramp.elements
    els[0].position = 0.0; els[0].color = (*COLS[0], 1)
    els[1].position = 1.0; els[1].color = (*COLS[0], 1)
    for i in range(1, 5):
        e = els.new(i * 0.2); e.color = (*COLS[i], 1)
    g.link(hue, ramp.inputs[0])
    base = ramp.outputs[0]

    # backdrop of the clip: darker at the bottom, hotter at the top
    lift = g.math("ADD", 0.35, g.math("MULTIPLY", z, 0.35), clamp=False)
    bg = g.mix(base, (0, 0, 0), g.math("SUBTRACT", 1.0, lift, clamp=True))

    # the subject: a soft lit head-and-shoulders glow
    dy = y
    dz = g.math("SUBTRACT", z, 0.18)
    head = g.math("SQRT", g.math("ADD", g.math("MULTIPLY", dy, dy), g.math("MULTIPLY", g.math("MULTIPLY", dz, dz), 1.15)))
    headg = g.math("SUBTRACT", 1.0, g.math("DIVIDE", head, 0.26), clamp=True)
    sz = g.math("SUBTRACT", z, -0.38)
    shoulders = g.math("SQRT", g.math("ADD", g.math("MULTIPLY", dy, dy), g.math("MULTIPLY", g.math("MULTIPLY", sz, sz), 2.2)))
    shg = g.math("SUBTRACT", 1.0, g.math("DIVIDE", shoulders, 0.42), clamp=True)
    subject = g.math("MAXIMUM", g.math("POWER", headg, 0.6), g.math("MULTIPLY", g.math("POWER", shg, 0.7), 0.8))
    warm = g.mix(base, (1.0, 0.9, 0.8), 0.55)
    col = g.mix(bg, warm, g.math("MULTIPLY", subject, 0.95))

    # hook bar at the top and two caption lines
    wide = g.band(y, 0.0, 0.3, 0.012)
    hook = g.math("MULTIPLY", g.band(z, 0.66, 0.045, 0.01), wide)
    cap1 = g.math("MULTIPLY", g.band(z, -0.5, 0.035, 0.008), g.band(y, 0.0, 0.34, 0.01))
    cap2 = g.math("MULTIPLY", g.band(z, -0.61, 0.03, 0.008), g.band(y, 0.0, 0.22, 0.01))
    caps = g.math("MAXIMUM", cap1, cap2)
    col = g.mix(col, (1.0, 1.0, 1.0), hook)
    col = g.mix(col, (1.0, 0.97, 0.9), caps)

    strength = g.math("ADD", 2.2, g.math("MULTIPLY", g.math("MAXIMUM", hook, caps), 3.5))
    em = g.new("ShaderNodeEmission")
    g.link(col, em.inputs["Color"])
    g.link(strength, em.inputs["Strength"])
    g.link(em.outputs[0], out.inputs[0])
    return mat, off


def glass_material():
    mat = bpy.data.materials.new("glass")
    nt = mat.node_tree
    nt.nodes.clear()
    g = Nodes(nt)
    out = g.new("ShaderNodeOutputMaterial")
    lobes = []
    for ior, col in ((1.40, (1, 0, 0)), (1.47, (0, 1, 0)), (1.56, (0, 0, 1))):
        gl = g.new("ShaderNodeBsdfGlass")
        gl.inputs["IOR"].default_value = ior
        gl.inputs["Roughness"].default_value = 0.02
        gl.inputs["Color"].default_value = (*col, 1)
        lobes.append(gl)
    a1 = g.new("ShaderNodeAddShader")
    a2 = g.new("ShaderNodeAddShader")
    g.link(lobes[0].outputs[0], a1.inputs[0]); g.link(lobes[1].outputs[0], a1.inputs[1])
    g.link(a1.outputs[0], a2.inputs[0]); g.link(lobes[2].outputs[0], a2.inputs[1])
    g.link(a2.outputs[0], out.inputs[0])
    return mat


def emission(name, color, strength):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*color, 1)
    e.inputs[1].default_value = strength
    nt.links.new(e.outputs[0], o.inputs[0])
    return mat, e


GLASS = glass_material()


def box(name, x0, x1, w, h, bevel=0.0, z=0.0):
    """A box from x0 to x1 along x, w wide (y) and h tall (z), as a mesh we can rewrite."""
    m = bpy.data.meshes.new(name)
    m.from_pydata(box_verts(x0, x1, w, h, z), [], [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)])
    m.update()
    ob = bpy.data.objects.new(name, m)
    scene.collection.objects.link(ob)
    if bevel:
        b = ob.modifiers.new("bevel", "BEVEL")
        b.width = bevel
        b.segments = 5
        b.limit_method = "NONE"
        for p in m.polygons:
            p.use_smooth = True
        ob.data.shade_smooth()
    return ob


def box_verts(x0, x1, w, h, z=0.0):
    y0, y1, z0, z1 = -w / 2, w / 2, z - h / 2, z + h / 2
    return [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1),
            (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]


# ---------------------------------------------------------------- the loaf
LOAF_START = -30.0
loaf_core = box("loaf_core", LOAF_START, 0.0, W - 2 * SHELL, H - 2 * SHELL)
loaf_mat, loaf_off = content_material("loaf_content", 0.0)
loaf_core.data.materials.append(loaf_mat)
loaf_glass = box("loaf_glass", LOAF_START, 0.0, W, H, bevel=0.05)
loaf_glass.data.materials.append(GLASS)

# ---------------------------------------------------------------- the tiles
tiles = []
for j in range(N):
    core = box(f"tile_core{j}", -S / 2 + SHELL * 0.5, S / 2 - SHELL * 0.5, W - 2 * SHELL, H - 2 * SHELL)
    # this tile was cut from the stretch [j*S, (j+1)*S] of the loaf's content
    mat, _ = content_material(f"tile_content{j}", S / 2 + j * S)
    core.data.materials.append(mat)
    shell = box(f"tile_glass{j}", -S / 2, S / 2, W, H, bevel=0.045)
    shell.data.materials.append(GLASS)
    core.parent = shell
    tiles.append(shell)

# ---------------------------------------------------------------- the gate
# A glowing 9:16 frame the loaf passes through: where the long video is cut.
GW, GH, GR = W + 0.22, H + 0.22, 0.12
pts = []
for cy, cz, a0 in ((GW / 2 - GR, GH / 2 - GR, 0), (-GW / 2 + GR, GH / 2 - GR, 90), (-GW / 2 + GR, -GH / 2 + GR, 180), (GW / 2 - GR, -GH / 2 + GR, 270)):
    for k in range(9):
        a = math.radians(a0 + k * 90 / 8)
        pts.append((0.0, cy + GR * math.cos(a), cz + GR * math.sin(a)))
cu = bpy.data.curves.new("gate", "CURVE")
cu.dimensions = "3D"
cu.bevel_depth = 0.028
cu.bevel_resolution = 6
sp_ = cu.splines.new("POLY")
sp_.points.add(len(pts) - 1)
for pt, co in zip(sp_.points, pts):
    pt.co = (*co, 1)
sp_.use_cyclic_u = True
gate = bpy.data.objects.new("gate", cu)
scene.collection.objects.link(gate)
gate_mat, gate_em = emission("gate", (1.0, 0.9, 0.8), 20.0)
gate.data.materials.append(gate_mat)
gate.visible_shadow = False
# a faint sheet of light across the gate, flaring as it cuts
sheet_mat = bpy.data.materials.new("sheet")
snt = sheet_mat.node_tree
snt.nodes.clear()
so = snt.nodes.new("ShaderNodeOutputMaterial")
se = snt.nodes.new("ShaderNodeEmission")
se.inputs[0].default_value = (1.0, 0.85, 0.7, 1)
st = snt.nodes.new("ShaderNodeBsdfTransparent")
smix = snt.nodes.new("ShaderNodeMixShader")
smix.inputs[0].default_value = 0.1
snt.links.new(st.outputs[0], smix.inputs[1]); snt.links.new(se.outputs[0], smix.inputs[2])
snt.links.new(smix.outputs[0], so.inputs[0])
sm = bpy.data.meshes.new("sheet")
sm.from_pydata([(0, -GW / 2, -GH / 2), (0, GW / 2, -GH / 2), (0, GW / 2, GH / 2), (0, -GW / 2, GH / 2)], [], [(0, 1, 2, 3)])
sheet = bpy.data.objects.new("sheet", sm)
sheet.location.x = 0.002
scene.collection.objects.link(sheet)
sheet.data.materials.append(sheet_mat)
sheet.visible_shadow = False

# ---------------------------------------------------------------- sparks
rng = random.Random(7)
spark_mat, _ = emission("spark", (1.0, 0.72, 0.38), 60.0)
sparks = []
for i in range(110):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=8, ring_count=6)
    sp = bpy.context.active_object
    sp.data.materials.append(spark_mat)
    sp.visible_shadow = False
    side = rng.choice((-1, 1))
    sparks.append({
        "ob": sp,
        "cut": i % N,                       # which cut of the loop throws it
        "z0": rng.uniform(FLOOR + 0.1, FLOOR + H - 0.1),
        "y0": side * W / 2,
        "v": (rng.uniform(0.6, 2.6), side * rng.uniform(0.4, 1.8), rng.uniform(0.6, 3.2)),
        "life": rng.uniform(0.45, 0.95),    # in cut intervals
        "r": rng.uniform(0.02, 0.04),
    })

# ---------------------------------------------------------------- stage
floor_m = bpy.data.meshes.new("floor")
floor_m.from_pydata([(-40, -40, FLOOR), (40, -40, FLOOR), (40, 40, FLOOR), (-40, 40, FLOOR)], [], [(0, 1, 2, 3)])
floor = bpy.data.objects.new("floor", floor_m)
scene.collection.objects.link(floor)
fmat = bpy.data.materials.new("floor")
p = fmat.node_tree.nodes["Principled BSDF"]
p.inputs["Base Color"].default_value = (0.012, 0.008, 0.02, 1)
p.inputs["Roughness"].default_value = 0.18
p.inputs["Specular IOR Level"].default_value = 0.6
floor.data.materials.append(fmat)

# a deep coloured cyclorama far behind
back_m = bpy.data.meshes.new("back")
BY = 14.0
back_m.from_pydata([(-60, BY, FLOOR), (60, BY, FLOOR), (60, BY, 40), (-60, BY, 40)], [], [(0, 1, 2, 3)])
back = bpy.data.objects.new("back", back_m)
back.rotation_euler.z = math.radians(-38)
scene.collection.objects.link(back)
bkm = bpy.data.materials.new("back")
bnt = bkm.node_tree
bnt.nodes.clear()
g = Nodes(bnt)
bo = g.new("ShaderNodeOutputMaterial")
btc = g.new("ShaderNodeTexCoord")
bsep = g.new("ShaderNodeSeparateXYZ")
g.link(btc.outputs["Object"], bsep.inputs[0])
bphase = g.new("ShaderNodeValue", name="PHASE")
grad = g.math("FRACT", g.math("ADD", g.math("MULTIPLY", g.math("ADD", bsep.outputs["X"], g.math("MULTIPLY", bsep.outputs["Z"], 0.6)), 1 / 26), bphase.outputs[0]))
bramp = g.new("ShaderNodeValToRGB")
bramp.color_ramp.interpolation = "EASE"
be = bramp.color_ramp.elements
be[0].position = 0.0; be[0].color = (*COLS[2], 1)
be[1].position = 1.0; be[1].color = (*COLS[2], 1)
for i, c in enumerate((COLS[1], COLS[0], COLS[3])):
    e = be.new((i + 1) * 0.25); e.color = (*c, 1)
g.link(grad, bramp.inputs[0])
dim = g.mix(bramp.outputs[0], (0.2, 0.13, 0.26), 1.0, blend="MULTIPLY")
bem = g.new("ShaderNodeEmission")
bem.inputs["Strength"].default_value = 1.0
g.link(dim, bem.inputs["Color"])
g.link(bem.outputs[0], bo.inputs[0])
back.data.materials.append(bkm)


def area(name, loc, rot, size, color, power):
    ld = bpy.data.lights.new(name, "AREA")
    ld.shape = "RECTANGLE"
    ld.size, ld.size_y = size
    ld.color = color
    ld.energy = power
    lo = bpy.data.objects.new(name, ld)
    lo.location = loc
    lo.rotation_euler = [math.radians(a) for a in rot]
    scene.collection.objects.link(lo)
    return ld


area("key", (5.0, -6.0, 5.0), (50, 0, 40), (5, 3), (1.0, 0.95, 0.92), 500)
area("rim_warm", (-3.0, 4.0, 3.5), (-60, 0, 200), (4, 3), (1.0, 0.45, 0.2), 900)
area("rim_cool", (8.0, 5.0, 2.5), (-70, 0, 140), (4, 4), (0.45, 0.4, 1.0), 900)

world = bpy.data.worlds.new("w")
scene.world = world
wn = world.node_tree
wn.nodes.clear()
wo = wn.nodes.new("ShaderNodeOutputWorld")
bg = wn.nodes.new("ShaderNodeBackground")
bg.inputs[0].default_value = (0.35, 0.12, 0.45, 1)
bg.inputs[1].default_value = 0.15
wn.links.new(bg.outputs[0], wo.inputs[0])
# a little haze, so the gate and the tiles throw light into the air
vol = wn.nodes.new("ShaderNodeVolumePrincipled")
vol.inputs["Density"].default_value = 0.006
vol.inputs["Anisotropy"].default_value = 0.35
wn.links.new(vol.outputs[0], wo.inputs["Volume"])

# ---------------------------------------------------------------- camera
TARGET = (1.25, 0.2, 0.05)
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 42
cam_data.dof.use_dof = True
cam_data.dof.focus_distance = 8.9
cam_data.dof.aperture_fstop = 2.8
cam = bpy.data.objects.new("cam", cam_data)
cam.location = (3.2, -8.4, 0.85)
scene.collection.objects.link(cam)
aim = bpy.data.objects.new("aim", None)
aim.location = TARGET
scene.collection.objects.link(aim)
tr = cam.constraints.new("TRACK_TO")
tr.target = aim
tr.track_axis = "TRACK_NEGATIVE_Z"
tr.up_axis = "UP_Y"
scene.camera = cam

# where the tiles go: out along the cut, then turning to face the camera and off to the right
FACE = math.atan2(cam.location.y - TARGET[1], cam.location.x - TARGET[0])  # direction to the camera, around z
RIGHT = (math.cos(FACE + math.pi / 2), math.sin(FACE + math.pi / 2))
RIGHT = (-RIGHT[0], -RIGHT[1]) if RIGHT[0] < 0 else RIGHT


def tile_pose(u):
    """Position, rotation and scale of a tile at life phase u in [0, 1)."""
    # out of the cut: a short slide along the loaf, then a lift
    slide = 0.55 * smooth(0.0, 0.16, u)
    # then away to the right of the frame, gathering speed
    run = smooth(0.1, 1.0, u)
    d = 7.2 * (run ** 1.1)
    x = S / 2 + slide + RIGHT[0] * d
    y = RIGHT[1] * d - 0.9 * smooth(0.1, 0.5, u)
    z = 0.42 * smooth(0.08, 0.4, u) + 0.35 * math.sin(TAU * u) * smooth(0.3, 0.6, u)
    # turn the cut face (+x) to the camera, with a little lean that settles
    yaw = (FACE) * smooth(0.06, 0.42, u)
    roll = math.radians(9) * math.sin(math.pi * smooth(0.05, 0.55, u))
    pitch = math.radians(-6) * smooth(0.2, 0.6, u)
    return (x, y, z), (roll, pitch, yaw)


# ---------------------------------------------------------------- render
scene.render.engine = "CYCLES"
scene.render.resolution_x = 900
scene.render.resolution_y = 900
scene.render.resolution_percentage = RES
scene.render.image_settings.file_format = "PNG"
scene.render.use_persistent_data = True
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Punchy"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 14
scene.cycles.transmission_bounces = 14
scene.cycles.glossy_bounces = 6
scene.cycles.seed = 3
scene.frame_start, scene.frame_end = 1, FRAMES

prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "OPTIX"
prefs.get_devices()
for d in prefs.devices:
    d.use = d.type == "OPTIX"
if os.environ.get("CYCLES_DEVICE") == "CPU" or not any(d.use for d in prefs.devices):
    scene.cycles.device = "CPU"
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
else:
    scene.cycles.device = "GPU"
    scene.cycles.denoiser = "OPTIX"

tree = bpy.data.node_groups.new("comp", "CompositorNodeTree")
scene.compositing_node_group = tree
rl = tree.nodes.new("CompositorNodeRLayers")
glare = tree.nodes.new("CompositorNodeGlare")
for k, v in (("Type", "Bloom"), ("Threshold", 1.0), ("Strength", 0.6), ("Size", 0.7), ("Quality", "High")):
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
    cut = (t * N) % 1.0  # 0 just after a cut, 1 at the next one
    # the loaf has advanced `cut` of a tile past the blade
    end = S * cut
    loaf_core.data.vertices.foreach_set("co", [c for v in box_verts(LOAF_START, max(end - SHELL * 0.5, 1e-3 - SHELL), W - 2 * SHELL, H - 2 * SHELL) for c in v])
    loaf_core.data.update()
    loaf_glass.data.vertices.foreach_set("co", [c for v in box_verts(LOAF_START, max(end, 1e-3), W, H) for c in v])
    loaf_glass.data.update()
    # content moves with the loaf: one PERIOD per loop
    loaf_off.outputs[0].default_value = -PERIOD * t

    for j, tile in enumerate(tiles):
        u = (t + j / N) % 1.0
        loc, rot = tile_pose(u)
        tile.location = loc
        tile.rotation_euler = rot
        tile.hide_render = False

    # the gate flares at each cut
    flare = math.exp(-cut * 7.0)
    gate_em.inputs[1].default_value = 14.0 + 50.0 * flare
    se.inputs[1].default_value = 0.4 + 9.0 * flare
    smix.inputs[0].default_value = 0.08 + 0.5 * flare

    for sp in sparks:
        # time since this spark's cut, in cut intervals
        since = ((t * N - sp["cut"]) % N)
        age = since / sp["life"]
        ob = sp["ob"]
        if age >= 1.0:
            ob.scale = (0, 0, 0)
            ob.location = (0, 0, -50)
            continue
        tau = since / N * 5.0  # seconds since its cut (a loop is 5 s)
        vx, vy, vz = sp["v"]
        ob.location = (0.02 + vx * tau, sp["y0"] + vy * tau, sp["z0"] + vz * tau - 4.9 * tau * tau)
        r = sp["r"] * (1 - age) ** 0.7
        stretch = 1 + 4 * (1 - age)
        ob.scale = (r * stretch, r, r)
        ob.rotation_euler = (0, -math.atan2(vz - 9.8 * tau, vx), math.atan2(vy, vx))

    back_phase = bnt.nodes["PHASE"]
    back_phase.outputs[0].default_value = t
    scene.render.filepath = os.path.join(OUT, f"frame_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
