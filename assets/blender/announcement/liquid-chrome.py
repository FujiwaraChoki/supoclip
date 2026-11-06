"""Liquid Chrome: a sea of molten, oil-slick chrome seen up close. From a single
pulsing source at the left edge, rings of liquid spread out and break into five
fanned ridges that roll across the frame. The metal carries a living thin film,
so every crest shimmers through the spectrum while it mirrors a cage of
saturated light strips. One source, rolling out as five.

The surface is a numpy heightfield rebuilt each frame from waves whose phases
advance by whole cycles per loop (t = (frame - 1) / FRAMES), and the film noise
is translated around a circle, so frame FRAMES + 1 equals frame 1.

    blender -b -P liquid-chrome.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]
"""

import math
import os
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/liquid-chrome"
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


# ---------------------------------------------------------------- heightfield
NX, NY = 700, 380
xs = np.linspace(-11.0, 11.0, NX)
ys = np.linspace(-4.5, 9.5, NY)
X, Y = np.meshgrid(xs, ys, indexing="xy")
SRC = np.array([-7.2, 0.4])
DX, DY = X - SRC[0], Y - SRC[1]
R = np.sqrt(DX ** 2 + DY ** 2)
TH = np.arctan2(DY, DX)
FAN = np.radians(np.array([-44, -22, 0, 22, 44]))


def height(t):
    # ring waves leaving the source: 3 crests per loop travel outward
    ring = 0.14 * np.sin(TAU * (R / 3.4 - 2 * t)) * np.exp(-R / 9.0) * (1 - np.exp(-(R / 1.2) ** 2))
    # five ridges fanning out; along each ridge a train of swells rolls outward
    fan = np.zeros_like(R)
    for i, a in enumerate(FAN):
        dth = np.angle(np.exp(1j * (TH - a)))
        lateral = np.exp(-(dth * R / (0.75 + 0.10 * R)) ** 2)
        swell = 0.5 + 0.5 * np.sin(TAU * (R / 4.2 - t) + i * 0.9)
        fan += lateral * (0.25 + 0.75 * swell ** 2) * (0.85 * (1 - np.exp(-(R / 2.5) ** 2)))
    # the source: a liquid dome that breathes twice per loop
    dome = 1.25 * np.exp(-(R / 1.15) ** 2) * (1.0 + 0.18 * np.sin(TAU * 2 * t))
    # slow cross-swell so the whole sea moves
    swell = 0.16 * np.sin(TAU * (X / 7.0 + Y / 11.0 - t)) + 0.10 * np.sin(TAU * (Y / 5.0 - X / 13.0 + t) + 1.3)
    return ring + fan + dome + swell


verts_xy = np.stack([X.ravel(), Y.ravel()], 1)
faces = []
idx = np.arange(NX * NY).reshape(NY, NX)
a = idx[:-1, :-1].ravel()
b = idx[:-1, 1:].ravel()
c = idx[1:, 1:].ravel()
d = idx[1:, :-1].ravel()
faces = np.stack([a, b, c, d], 1)
mesh = bpy.data.meshes.new("sea")
mesh.vertices.add(NX * NY)
mesh.loops.add(len(faces) * 4)
mesh.polygons.add(len(faces))
mesh.loops.foreach_set("vertex_index", faces.ravel().astype(np.int32))
mesh.polygons.foreach_set("loop_start", np.arange(0, len(faces) * 4, 4, dtype=np.int32))
mesh.polygons.foreach_set("loop_total", np.full(len(faces), 4, dtype=np.int32))


def update(t):
    z = height(t)
    co = np.stack([X.ravel(), Y.ravel(), z.ravel()], 1).astype(np.float32)
    mesh.vertices.foreach_set("co", co.ravel())
    mesh.update()


update(0.0)
mesh.validate()
mesh.shade_smooth()
sea = bpy.data.objects.new("sea", mesh)
scene.collection.objects.link(sea)

# ---------------------------------------------------------------- iridescent chrome
mat = bpy.data.materials.new("chrome")
nt = mat.node_tree
nt.nodes.clear()
out = nt.nodes.new("ShaderNodeOutputMaterial")
metal = nt.nodes.new("ShaderNodeBsdfMetallic")
metal.inputs["Base Color"].default_value = (0.95, 0.93, 0.97, 1)
metal.inputs["Edge Tint"].default_value = (1.0, 0.9, 1.0, 1)
metal.inputs["Roughness"].default_value = 0.05
tex = nt.nodes.new("ShaderNodeTexCoord")
offset = nt.nodes.new("ShaderNodeCombineXYZ")
offset.name = "OFFSET"
addv = nt.nodes.new("ShaderNodeVectorMath"); addv.operation = "ADD"
nt.links.new(tex.outputs["Object"], addv.inputs[0])
nt.links.new(offset.outputs[0], addv.inputs[1])
noise = nt.nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = 0.22
noise.inputs["Detail"].default_value = 2.0
noise.inputs["Distortion"].default_value = 0.8
nt.links.new(addv.outputs[0], noise.inputs["Vector"])
film = nt.nodes.new("ShaderNodeMapRange")
film.inputs["From Min"].default_value = 0.3
film.inputs["From Max"].default_value = 0.7
film.inputs["To Min"].default_value = 250.0
film.inputs["To Max"].default_value = 750.0
nt.links.new(noise.outputs["Fac"], film.inputs["Value"])
nt.links.new(film.outputs["Result"], metal.inputs["Thin Film Thickness"])
metal.inputs["Thin Film IOR"].default_value = 1.5
nt.links.new(metal.outputs[0], out.inputs[0])
mesh.materials.append(mat)

# ---------------------------------------------------------------- light cage
COLORS = [
    (1.00, 0.22, 0.01),
    (1.00, 0.01, 0.32),
    (0.50, 0.03, 1.00),
    (0.01, 0.30, 1.00),
    (0.00, 0.85, 0.55),
    (1.00, 0.75, 0.10),
]


def emissive(name, color, strength):
    m = bpy.data.materials.new(name)
    t = m.node_tree
    t.nodes.clear()
    o = t.nodes.new("ShaderNodeOutputMaterial")
    e = t.nodes.new("ShaderNodeEmission")
    e.inputs[0].default_value = (*color, 1)
    e.inputs[1].default_value = strength
    t.links.new(e.outputs[0], o.inputs[0])
    return m


# long bars arching over the sea, like a tunnel of coloured tubes
for i in range(11):
    yy = -10 + i * 3.1
    col = COLORS[i % len(COLORS)]
    bpy.ops.mesh.primitive_cylinder_add(radius=0.55, depth=60, location=(0, yy, 9.0 + 1.5 * math.sin(i)))
    bar = bpy.context.active_object
    bar.rotation_euler = (0, math.radians(90), math.radians(8 * math.sin(i * 1.7)))
    bar.data.materials.append(emissive(f"bar{i}", col, 3.0))
# a cross set for richer crossings
for i in range(5):
    xx = -16 + i * 8.0
    col = COLORS[(i + 3) % len(COLORS)]
    bpy.ops.mesh.primitive_cylinder_add(radius=0.6, depth=60, location=(xx, 4, 11.0))
    bar = bpy.context.active_object
    bar.rotation_euler = (math.radians(90), 0, 0)
    bar.data.materials.append(emissive(f"cross{i}", col, 2.5))

world = bpy.data.worlds.new("w")
scene.world = world
wn = world.node_tree
wn.nodes.clear()
wo = wn.nodes.new("ShaderNodeOutputWorld")
bg = wn.nodes.new("ShaderNodeBackground")
tc = wn.nodes.new("ShaderNodeTexCoord")
sepw = wn.nodes.new("ShaderNodeSeparateXYZ")
wr = wn.nodes.new("ShaderNodeValToRGB")
wn.links.new(tc.outputs["Generated"], sepw.inputs[0])
wn.links.new(sepw.outputs["X"], wr.inputs[0])
wr.color_ramp.elements[0].color = (0.05, 0.0, 0.08, 1)
wr.color_ramp.elements[1].color = (0.0, 0.02, 0.09, 1)
wn.links.new(wr.outputs[0], bg.inputs[0])
bg.inputs[1].default_value = 1.0
wn.links.new(bg.outputs[0], wo.inputs[0])

# ---------------------------------------------------------------- camera
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 30
cam_data.dof.use_dof = True
cam_data.dof.aperture_fstop = 4.0
cam = bpy.data.objects.new("cam", cam_data)
cam.location = (-1.0, -6.5, 5.2)
scene.collection.objects.link(cam)
look = bpy.data.objects.new("look", None)
look.location = (-1.2, 1.2, 0.2)
scene.collection.objects.link(look)
tcn = cam.constraints.new("TRACK_TO")
tcn.target = look
tcn.track_axis = "TRACK_NEGATIVE_Z"
tcn.up_axis = "UP_Y"
cam_data.dof.focus_object = look
scene.camera = cam

# ---------------------------------------------------------------- render
scene.render.engine = "CYCLES"
scene.render.resolution_x = 1600
scene.render.resolution_y = 800
scene.render.resolution_percentage = RES
scene.render.image_settings.file_format = "PNG"
scene.render.use_persistent_data = True
scene.view_settings.view_transform = "Khronos PBR Neutral"
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 8
scene.cycles.glossy_bounces = 8
scene.cycles.seed = 5
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
for k, v in (("Type", "Bloom"), ("Threshold", 1.0), ("Strength", 0.5), ("Size", 0.7), ("Quality", "High")):
    try:
        glare.inputs[k].default_value = v
    except (KeyError, TypeError, AttributeError):
        pass
gout = tree.nodes.new("NodeGroupOutput")
tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
tree.links.new(glare.outputs[0], gout.inputs[0])

offset_node = nt.nodes["OFFSET"]
for f in parse_frames(SPEC):
    t = ((f - 1) / FRAMES) % 1.0
    scene.frame_set(f)
    update(t)
    offset_node.inputs[0].default_value = 2.0 * math.cos(TAU * t)
    offset_node.inputs[1].default_value = 2.0 * math.sin(TAU * t)
    scene.render.filepath = os.path.join(OUT, f"frame_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
