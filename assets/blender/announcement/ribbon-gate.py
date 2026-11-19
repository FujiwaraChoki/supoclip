"""Ribbon Gate: one long satin band of five colours streams in from the left,
twisting as it flows, and passes behind a row of tall 9:16 glass panels. Each
panel catches a piece of the band, refracts and disperses it, so the long
stream is cut into vertical pieces: a long video becoming clips. Dispersion is
faked like Prism Wall, with three glass lobes at slightly different IOR.

Every motion comes from the loop phase t = (frame - 1) / FRAMES: the band's
waves and twist travel by whole periods, the light pulses along it scroll by
one period, and each panel rocks on a sine of t, so frame FRAMES + 1 equals
frame 1.

    blender -b -P ribbon-gate.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]
"""

import math
import os
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/ribbon-gate"
SAMPLES = int(argv[1]) if len(argv) > 1 else 96
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


COLS = [(1.0, 0.32, 0.0), (1.0, 0.02, 0.32), (0.45, 0.02, 1.0), (0.0, 0.32, 1.0), (0.0, 0.85, 0.55)]

# ---------------------------------------------------------------- the band
X0, X1 = -18.0, 18.0
NSEG = 1000
N_STRIPS = 5
STRIP_W = 0.3
STRIP_GAP = 0.04
xs = np.linspace(X0, X1, NSEG + 1)
strip_meshes = []


def band_frame(t):
    """Centreline and across-band direction along x at loop phase t."""
    p = TAU * t
    y = 1.05 + 0.28 * np.sin(0.55 * xs - p) + 0.1 * np.sin(1.3 * xs + 2 * p)
    z = 0.05 + 0.55 * np.sin(0.42 * xs - p + 0.8) + 0.12 * np.sin(1.1 * xs - 2 * p)
    twist = 1.25 * np.sin(0.36 * xs - p) + 0.4 * np.sin(0.9 * xs + p + 1.0)
    # across-band direction: mostly vertical, rolled around the flow axis by the twist
    w = np.stack([np.zeros_like(xs), -np.sin(twist), np.cos(twist)], -1)
    c = np.stack([xs, y, z], -1)
    return c, w


def strip_coords(c, w, k):
    off = (k - (N_STRIPS - 1) / 2) * (STRIP_W + STRIP_GAP)
    a = c + w * (off - STRIP_W / 2)
    b = c + w * (off + STRIP_W / 2)
    return np.concatenate([a, b]).astype(np.float32)


c0, w0 = band_frame(0.0)
faces = [(i, i + 1, NSEG + 2 + i, NSEG + 1 + i) for i in range(NSEG)]
for k in range(N_STRIPS):
    m = bpy.data.meshes.new(f"strip{k}")
    m.from_pydata(strip_coords(c0, w0, k).tolist(), [], faces)
    m.update()
    for poly in m.polygons:
        poly.use_smooth = True
    ob = bpy.data.objects.new(f"strip{k}", m)
    scene.collection.objects.link(ob)
    strip_meshes.append(m)

# satin with light pulses that scroll along the band, one period per loop
phase_nodes = []
for k, col in enumerate(COLS):
    mat = bpy.data.materials.new(f"satin{k}")
    nt = mat.node_tree
    p = nt.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*col, 1)
    p.inputs["Roughness"].default_value = 0.28
    p.inputs["Coat Weight"].default_value = 1.0
    p.inputs["Coat Roughness"].default_value = 0.06
    p.inputs["Sheen Weight"].default_value = 0.6
    p.inputs["Emission Color"].default_value = (*col, 1)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    d = nt.nodes.new("ShaderNodeMath"); d.operation = "DIVIDE"; d.inputs[1].default_value = 3.6
    nt.links.new(sep.outputs["X"], d.inputs[0])
    ph = nt.nodes.new("ShaderNodeValue"); ph.name = "PHASE"
    sb = nt.nodes.new("ShaderNodeMath"); sb.operation = "SUBTRACT"
    nt.links.new(d.outputs[0], sb.inputs[0]); nt.links.new(ph.outputs[0], sb.inputs[1])
    # each colour pulses slightly out of step with the next
    ad = nt.nodes.new("ShaderNodeMath"); ad.operation = "ADD"; ad.inputs[1].default_value = k * 0.11
    nt.links.new(sb.outputs[0], ad.inputs[0])
    fr = nt.nodes.new("ShaderNodeMath"); fr.operation = "FRACT"
    nt.links.new(ad.outputs[0], fr.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "EASE"
    e = ramp.color_ramp.elements
    e[0].position = 0.0; e[0].color = (0.25, 0.25, 0.25, 1)
    e[1].position = 0.12; e[1].color = (2.6, 2.6, 2.6, 1)
    e2 = e.new(0.34); e2.color = (0.25, 0.25, 0.25, 1)
    nt.links.new(fr.outputs[0], ramp.inputs[0])
    bw = nt.nodes.new("ShaderNodeRGBToBW")
    nt.links.new(ramp.outputs[0], bw.inputs[0])
    nt.links.new(bw.outputs[0], p.inputs["Emission Strength"])
    strip_meshes[k].materials.append(mat)
    phase_nodes.append(ph)

# ---------------------------------------------------------------- 9:16 glass panels
PW, PH, PD = 1.42, 2.52, 0.55
PANEL_X = [-0.2, 1.52, 3.24, 4.96]
glass = bpy.data.materials.new("glass")
gn = glass.node_tree
gn.nodes.clear()
gout = gn.nodes.new("ShaderNodeOutputMaterial")
lobes = []
for ior, col in ((1.40, (1, 0, 0)), (1.48, (0, 1, 0)), (1.58, (0, 0, 1))):
    g = gn.nodes.new("ShaderNodeBsdfGlass")
    g.inputs["IOR"].default_value = ior
    g.inputs["Roughness"].default_value = 0.0
    g.inputs["Color"].default_value = (*col, 1)
    lobes.append(g)
a1 = gn.nodes.new("ShaderNodeAddShader")
a2 = gn.nodes.new("ShaderNodeAddShader")
gn.links.new(lobes[0].outputs[0], a1.inputs[0])
gn.links.new(lobes[1].outputs[0], a1.inputs[1])
gn.links.new(a1.outputs[0], a2.inputs[0])
gn.links.new(lobes[2].outputs[0], a2.inputs[1])
gn.links.new(a2.outputs[0], gout.inputs[0])

panels = []
for i, x in enumerate(PANEL_X):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, 0.0, 0.1))
    pnl = bpy.context.active_object
    pnl.scale = (PW, PD, PH)
    bpy.ops.object.transform_apply(scale=True)
    bev = pnl.modifiers.new("bevel", "BEVEL")
    bev.width = 0.12
    bev.segments = 6
    bpy.ops.object.shade_smooth()
    pnl.data.materials.append(glass)
    panels.append(pnl)

# ---------------------------------------------------------------- backdrop and light
BACK_Y = 5.5
bm_ = bpy.data.meshes.new("backdrop")
bm_.from_pydata([(-40, BACK_Y, -18), (40, BACK_Y, -18), (40, BACK_Y, 18), (-40, BACK_Y, 18)], [], [(0, 1, 2, 3)])
back = bpy.data.objects.new("backdrop", bm_)
scene.collection.objects.link(back)
bmat = bpy.data.materials.new("backdrop")
bn = bmat.node_tree
bn.nodes.clear()
bo = bn.nodes.new("ShaderNodeOutputMaterial")
be = bn.nodes.new("ShaderNodeEmission")
btc = bn.nodes.new("ShaderNodeTexCoord")
offs = bn.nodes.new("ShaderNodeCombineXYZ"); offs.name = "OFFSET"
addv = bn.nodes.new("ShaderNodeVectorMath"); addv.operation = "ADD"
bn.links.new(btc.outputs["Object"], addv.inputs[0]); bn.links.new(offs.outputs[0], addv.inputs[1])
noise = bn.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 0.08
noise.inputs["Detail"].default_value = 1.0
bn.links.new(addv.outputs[0], noise.inputs["Vector"])
sepb = bn.nodes.new("ShaderNodeSeparateXYZ")
bn.links.new(btc.outputs["Object"], sepb.inputs[0])
d1 = bn.nodes.new("ShaderNodeMath"); d1.operation = "MULTIPLY_ADD"; d1.inputs[1].default_value = 0.5
bn.links.new(sepb.outputs["X"], d1.inputs[0]); bn.links.new(sepb.outputs["Z"], d1.inputs[2])
d2 = bn.nodes.new("ShaderNodeMath"); d2.operation = "DIVIDE"; d2.inputs[1].default_value = 9.0
bn.links.new(d1.outputs[0], d2.inputs[0])
nmul = bn.nodes.new("ShaderNodeMath"); nmul.operation = "MULTIPLY_ADD"; nmul.inputs[1].default_value = 1.1
bn.links.new(noise.outputs["Fac"], nmul.inputs[0]); bn.links.new(d2.outputs[0], nmul.inputs[2])
bphase = bn.nodes.new("ShaderNodeValue"); bphase.name = "PHASE"
sb2 = bn.nodes.new("ShaderNodeMath"); sb2.operation = "SUBTRACT"
bn.links.new(nmul.outputs[0], sb2.inputs[0]); bn.links.new(bphase.outputs[0], sb2.inputs[1])
fr2 = bn.nodes.new("ShaderNodeMath"); fr2.operation = "FRACT"
bn.links.new(sb2.outputs[0], fr2.inputs[0])
ramp2 = bn.nodes.new("ShaderNodeValToRGB")
ramp2.color_ramp.interpolation = "EASE"
els = ramp2.color_ramp.elements
els[0].position = 0.0; els[0].color = (*COLS[0], 1)
els[1].position = 1.0; els[1].color = (*COLS[0], 1)
for i in range(1, 5):
    e = els.new(i * 0.2); e.color = (*COLS[i], 1)
bn.links.new(fr2.outputs[0], ramp2.inputs[0])
# deepen the field so the band stays the brightest thing on screen
mixd = bn.nodes.new("ShaderNodeMix"); mixd.data_type = "RGBA"; mixd.blend_type = "MULTIPLY"
mixd.inputs["Factor"].default_value = 1.0
mixd.inputs[7].default_value = (0.2, 0.13, 0.3, 1)
bn.links.new(ramp2.outputs[0], mixd.inputs[6])
be.inputs["Strength"].default_value = 0.9
bn.links.new(mixd.outputs[2], be.inputs["Color"])
bn.links.new(be.outputs[0], bo.inputs[0])
bm_.materials.append(bmat)

# thin bright bars far behind, smeared into vertical streaks by the panels
for i, x in enumerate((-1.0, 6.0)):
    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=30, location=(x, BACK_Y - 0.1, 0))
    bar = bpy.context.active_object
    bar.rotation_euler = (0, math.radians(32), 0)
    m = bpy.data.materials.new(f"bar{i}")
    mt = m.node_tree
    mt.nodes.clear()
    mo = mt.nodes.new("ShaderNodeOutputMaterial")
    me = mt.nodes.new("ShaderNodeEmission")
    me.inputs[0].default_value = (1, 0.9, 0.82, 1)
    me.inputs[1].default_value = 12.0
    mt.links.new(me.outputs[0], mo.inputs[0])
    bar.data.materials.append(m)


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


area("key", (-4.0, -5.0, 5.5), (50, 0, -35), (6, 3), (1.0, 0.95, 0.9), 1400)
area("rim", (7.0, 3.5, 3.0), (-60, 0, 125), (4, 4), (0.55, 0.65, 1.0), 900)
area("fill", (0.0, -6.0, -3.5), (125, 0, 0), (10, 2), (1.0, 0.6, 0.8), 260)

world = bpy.data.worlds.new("w")
scene.world = world
wn = world.node_tree
wn.nodes.clear()
wo = wn.nodes.new("ShaderNodeOutputWorld")
bg = wn.nodes.new("ShaderNodeBackground")
bg.inputs[0].default_value = (0.5, 0.1, 0.55, 1)
bg.inputs[1].default_value = 0.35
wn.links.new(bg.outputs[0], wo.inputs[0])

# ---------------------------------------------------------------- camera
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 34
cam_data.dof.use_dof = True
cam_data.dof.focus_distance = 9.6
cam_data.dof.aperture_fstop = 3.2
cam = bpy.data.objects.new("cam", cam_data)
cam.location = (-0.3, -9.7, 0.45)
cam.rotation_euler = (math.radians(89), 0, math.radians(-10))
scene.collection.objects.link(cam)
scene.camera = cam

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
scene.cycles.max_bounces = 12
scene.cycles.transmission_bounces = 12
scene.cycles.glossy_bounces = 6
scene.cycles.seed = 11
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
for k, v in (("Type", "Bloom"), ("Threshold", 1.0), ("Strength", 0.45), ("Size", 0.6), ("Quality", "High")):
    try:
        glare.inputs[k].default_value = v
    except (KeyError, TypeError, AttributeError):
        pass
cout = tree.nodes.new("NodeGroupOutput")
tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
tree.links.new(glare.outputs[0], cout.inputs[0])

back_phase = bn.nodes["PHASE"]
back_offset = bn.nodes["OFFSET"]
for f in parse_frames(SPEC):
    t = ((f - 1) / FRAMES) % 1.0
    scene.frame_set(f)
    c, w = band_frame(t)
    for k, m in enumerate(strip_meshes):
        m.vertices.foreach_set("co", strip_coords(c, w, k).ravel())
        m.update()
    for ph in phase_nodes:
        ph.outputs[0].default_value = t
    back_phase.outputs[0].default_value = t
    back_offset.inputs[0].default_value = 3.0 * math.cos(TAU * t)
    back_offset.inputs[2].default_value = 3.0 * math.sin(TAU * t)
    for i, pnl in enumerate(panels):
        pnl.rotation_euler = (0, 0, math.radians(14) * math.sin(TAU * t - i * 0.9))
    scene.render.filepath = os.path.join(OUT, f"frame_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
