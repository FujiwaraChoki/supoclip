"""Prism Wall: a wall of tall glass prisms stands in front of a luminous field of
five drifting colours. A wave rolls across the wall and every prism turns a
third of a turn as it passes, so the light behind is sliced into vertical
shards, refracted, dispersed and recombined: one picture becoming many vertical
pieces and folding back into one. Dispersion is faked by adding three glass
lobes with slightly different IOR (red, green, blue).

Each prism turns by exactly 120 degrees per loop (its own symmetry) on an eased
travelling wave, and the backdrop pattern scrolls by whole periods, all from the
loop phase t = (frame - 1) / FRAMES, so frame FRAMES + 1 equals frame 1.

    blender -b -P prism-wall.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]
"""

import math
import os
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/prism-wall"
SAMPLES = int(argv[1]) if len(argv) > 1 else 160
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


# ---------------------------------------------------------------- prism template
SIDE = 1.25
CUT = 0.10
HALF_H = 6.0
N_RODS = 15
corners = [np.array([math.cos(a), math.sin(a)]) * SIDE / math.sqrt(3) for a in (math.pi / 2, math.pi / 2 + TAU / 3, math.pi / 2 + 2 * TAU / 3)]
ring = []
for i in range(3):
    p, q, r = corners[i - 1], corners[i], corners[(i + 1) % 3]
    ring.append(q + (p - q) * CUT)
    ring.append(q + (r - q) * CUT)
ring = np.array(ring)
nr = len(ring)
# section in XY (y = depth), extruded along Z (up)
tverts = np.concatenate([np.c_[ring, np.full(nr, -HALF_H)], np.c_[ring, np.full(nr, HALF_H)]])
tfaces = [tuple(range(nr - 1, -1, -1)), tuple(range(nr, 2 * nr))]
for i in range(nr):
    j = (i + 1) % nr
    tfaces.append((i, j, nr + j, nr + i))

pitch = SIDE * 0.9
xs = (np.arange(N_RODS) - (N_RODS - 1) / 2) * pitch
verts_per = len(tverts)
all_faces = []
for k in range(N_RODS):
    off = k * verts_per
    all_faces += [tuple(v + off for v in f) for f in tfaces]
mesh = bpy.data.meshes.new("rods")
mesh.from_pydata(np.zeros((N_RODS * verts_per, 3)).tolist(), [], all_faces)
mesh.update()


def update(t):
    # the wave travels left -> right, once per loop; each rod eases through 120 deg
    ph = (t - (xs - xs[0]) / (xs[-1] - xs[0] + pitch) * 1.0) % 1.0
    e = np.clip(ph / 0.45, 0, 1)
    e = e * e * e * (e * (e * 6 - 15) + 10)
    ang = (TAU / 3) * e
    ca, sa = np.cos(ang)[:, None], np.sin(ang)[:, None]
    vx = tverts[None, :, 0] * ca - tverts[None, :, 1] * sa + xs[:, None]
    vy = tverts[None, :, 0] * sa + tverts[None, :, 1] * ca
    vz = np.broadcast_to(tverts[None, :, 2], vx.shape)
    co = np.stack([vx, vy, vz], -1).reshape(-1, 3).astype(np.float32)
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()


update(0.0)
rods = bpy.data.objects.new("rods", mesh)
scene.collection.objects.link(rods)

# ---------------------------------------------------------------- dispersive glass
mat = bpy.data.materials.new("glass")
nt = mat.node_tree
nt.nodes.clear()
out = nt.nodes.new("ShaderNodeOutputMaterial")
lobes = []
for ior, col in ((1.42, (1, 0, 0)), (1.50, (0, 1, 0)), (1.60, (0, 0, 1))):
    g = nt.nodes.new("ShaderNodeBsdfGlass")
    g.inputs["IOR"].default_value = ior
    g.inputs["Roughness"].default_value = 0.0
    g.inputs["Color"].default_value = (*col, 1)
    lobes.append(g)
a1 = nt.nodes.new("ShaderNodeAddShader")
a2 = nt.nodes.new("ShaderNodeAddShader")
nt.links.new(lobes[0].outputs[0], a1.inputs[0])
nt.links.new(lobes[1].outputs[0], a1.inputs[1])
nt.links.new(a1.outputs[0], a2.inputs[0])
nt.links.new(lobes[2].outputs[0], a2.inputs[1])
nt.links.new(a2.outputs[0], out.inputs[0])
mesh.materials.append(mat)

# ---------------------------------------------------------------- luminous backdrop
BACK_Y = 3.2
bm_ = bpy.data.meshes.new("backdrop")
bm_.from_pydata([(-20, BACK_Y, -12), (20, BACK_Y, -12), (20, BACK_Y, 12), (-20, BACK_Y, 12)], [], [(0, 1, 2, 3)])
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
# warp the coordinate with soft noise (looping: offset travels on a circle)
noise = bn.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 0.09
noise.inputs["Detail"].default_value = 1.0
bn.links.new(addv.outputs[0], noise.inputs["Vector"])
sepv = bn.nodes.new("ShaderNodeSeparateXYZ")
bn.links.new(btc.outputs["Object"], sepv.inputs[0])
# u = (x * 0.55 + z) / P + 1.4 * noise - t : diagonal bands, scrolled one period per loop
P = 7.5
d1 = bn.nodes.new("ShaderNodeMath"); d1.operation = "MULTIPLY_ADD"; d1.inputs[1].default_value = 0.55
bn.links.new(sepv.outputs["X"], d1.inputs[0]); bn.links.new(sepv.outputs["Z"], d1.inputs[2])
d2 = bn.nodes.new("ShaderNodeMath"); d2.operation = "DIVIDE"; d2.inputs[1].default_value = P
bn.links.new(d1.outputs[0], d2.inputs[0])
nmul = bn.nodes.new("ShaderNodeMath"); nmul.operation = "MULTIPLY_ADD"
nmul.inputs[1].default_value = 1.1
bn.links.new(noise.outputs["Fac"], nmul.inputs[0]); bn.links.new(d2.outputs[0], nmul.inputs[2])
phase = bn.nodes.new("ShaderNodeValue"); phase.name = "PHASE"
sb = bn.nodes.new("ShaderNodeMath"); sb.operation = "SUBTRACT"
bn.links.new(nmul.outputs[0], sb.inputs[0]); bn.links.new(phase.outputs[0], sb.inputs[1])
fr = bn.nodes.new("ShaderNodeMath"); fr.operation = "FRACT"
bn.links.new(sb.outputs[0], fr.inputs[0])
ramp = bn.nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.interpolation = "EASE"
cols = [(1.0, 0.32, 0.0), (1.0, 0.02, 0.32), (0.45, 0.02, 1.0), (0.0, 0.32, 1.0), (0.0, 0.85, 0.55)]
els = ramp.color_ramp.elements
els[0].position = 0.0; els[0].color = (*cols[0], 1)
els[1].position = 1.0; els[1].color = (*cols[0], 1)
for i in range(1, 5):
    e = els.new(i * 0.2); e.color = (*cols[i], 1)
bn.links.new(fr.outputs[0], ramp.inputs[0])
be.inputs["Strength"].default_value = 1.6
bn.links.new(ramp.outputs[0], be.inputs["Color"])
bn.links.new(be.outputs[0], bo.inputs[0])
bm_.materials.append(bmat)

# a few bright, thin light bars far behind, seen through the gaps & smeared by the prisms
for i, (x, c) in enumerate([(-4.5, (1, 0.9, 0.8)), (0.7, (1, 0.9, 0.8)), (5.2, (1, 0.9, 0.8))]):
    bpy.ops.mesh.primitive_cylinder_add(radius=0.06, depth=30, location=(x, BACK_Y - 0.05, 0))
    bar = bpy.context.active_object
    bar.rotation_euler = (0, math.radians(35), 0)
    m = bpy.data.materials.new(f"bar{i}")
    mt = m.node_tree
    mt.nodes.clear()
    mo = mt.nodes.new("ShaderNodeOutputMaterial")
    me = mt.nodes.new("ShaderNodeEmission")
    me.inputs[0].default_value = (*c, 1)
    me.inputs[1].default_value = 30.0
    mt.links.new(me.outputs[0], mo.inputs[0])
    bar.data.materials.append(m)

world = bpy.data.worlds.new("w")
scene.world = world
wn = world.node_tree
wn.nodes.clear()
wo = wn.nodes.new("ShaderNodeOutputWorld")
bg = wn.nodes.new("ShaderNodeBackground")
bg.inputs[0].default_value = (0.75, 0.1, 0.6, 1)
bg.inputs[1].default_value = 0.6
wn.links.new(bg.outputs[0], wo.inputs[0])

# ---------------------------------------------------------------- camera
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 35
cam_data.dof.use_dof = True
cam_data.dof.aperture_fstop = 5.6
cam = bpy.data.objects.new("cam", cam_data)
cam.location = (5.2, -7.6, -1.2)
cam.rotation_euler = (math.radians(90 + 6), 0, math.radians(30))
scene.collection.objects.link(cam)
cam_data.dof.focus_distance = 8.6
cam_data.dof.aperture_fstop = 2.8
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
scene.cycles.max_bounces = 16
scene.cycles.transmission_bounces = 16
scene.cycles.glossy_bounces = 8
scene.cycles.seed = 9
scene.frame_start, scene.frame_end = 1, FRAMES

prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "OPTIX"
prefs.get_devices()
for d in prefs.devices:
    d.use = d.type == "OPTIX"
scene.cycles.device = "GPU"
scene.cycles.denoiser = "OPTIX"
if os.environ.get("CYCLES_DEVICE") == "CPU":  # for quick tests while the GPU is busy
    scene.cycles.device = "CPU"
    scene.cycles.denoiser = "OPENIMAGEDENOISE"

tree = bpy.data.node_groups.new("comp", "CompositorNodeTree")
scene.compositing_node_group = tree
rl = tree.nodes.new("CompositorNodeRLayers")
glare = tree.nodes.new("CompositorNodeGlare")
for k, v in (("Type", "Bloom"), ("Threshold", 1.0), ("Strength", 0.5), ("Size", 0.65), ("Quality", "High")):
    try:
        glare.inputs[k].default_value = v
    except (KeyError, TypeError, AttributeError):
        pass
gout = tree.nodes.new("NodeGroupOutput")
tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
tree.links.new(glare.outputs[0], gout.inputs[0])

phase_node = bn.nodes["PHASE"]
offset_node = bn.nodes["OFFSET"]
for f in parse_frames(SPEC):
    t = ((f - 1) / FRAMES) % 1.0
    scene.frame_set(f)
    update(t)
    phase_node.outputs[0].default_value = t
    offset_node.inputs[0].default_value = 3.0 * math.cos(TAU * t)
    offset_node.inputs[2].default_value = 3.0 * math.sin(TAU * t)
    scene.render.filepath = os.path.join(OUT, f"frame_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
