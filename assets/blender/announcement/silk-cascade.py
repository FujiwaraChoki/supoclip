"""Silk Cascade: a tangle of satin ribbons in every colour pours in from the left,
is wrung through a single twisting knot, and leaves on the right sorted into five
broad, waving bands of pure colour. One video, five kinds of clip.

Everything is built with numpy and rebuilt every frame from cyclic functions of
the loop phase t = (frame - 1) / FRAMES, so frame FRAMES + 1 equals frame 1.

    blender -b -P silk-cascade.py -- OUT_DIR [SAMPLES] [FRAMES_SPEC] [RES_PERCENT]

FRAMES_SPEC is "all" (default), "a-b" or "a,b,c".
"""

import math
import os
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/silk-cascade"
SAMPLES = int(argv[1]) if len(argv) > 1 else 96
SPEC = argv[2] if len(argv) > 2 else "all"
RES = int(argv[3]) if len(argv) > 3 else 100
FRAMES = 120
TAU = math.tau

os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
rng = np.random.default_rng(7)


def parse_frames(spec):
    if spec == "all":
        return list(range(1, FRAMES + 1))
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- palette
# Five workflow colours (linear-ish sRGB), deep and saturated.
BANDS = np.array([
    (1.00, 0.30, 0.02),   # tangerine
    (0.95, 0.02, 0.28),   # crimson-magenta
    (0.45, 0.04, 0.95),   # violet
    (0.00, 0.35, 1.00),   # cobalt
    (0.00, 0.80, 0.55),   # jade
])


def srgb_to_lin(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


# ---------------------------------------------------------------- ribbons
class RibbonField:
    """N ribbons sampled at S points along x; topology fixed, coordinates per frame."""

    def __init__(self, name, n, samples, x0, x1, width, layer, knot=True):
        self.knot = knot
        self.n, self.s = n, samples
        self.x = np.linspace(x0, x1, samples)
        self.width = width
        self.layer = layer
        self.band = np.arange(n) % 5
        rng.shuffle(self.band)
        # left: random interleaved heights; right: sorted bands
        self.yl = rng.uniform(-2.6, 2.6, n)
        order = np.argsort(self.band + rng.uniform(0, 0.999, n))
        rank = np.empty(n)
        rank[order] = np.arange(n)
        self.yr = (rank / (n - 1) - 0.5) * 5.4 + (self.band - 2) * 0.10
        self.zl = rng.uniform(-0.6, 0.6, n) + layer
        self.zr = (self.band - 2) * 0.12 * np.sign(rng.uniform(-1, 1, n)) + rng.uniform(-0.25, 0.25, n) + layer
        self.theta = rng.uniform(0, TAU, n)
        self.rad = np.sqrt(rng.uniform(0.0, 1.0, n)) * 0.42
        self.phase = rng.uniform(0, TAU, n)
        self.twist0 = rng.uniform(0, TAU, n)
        self.twist_rate = rng.choice([-2, -1, 1, 2], n)
        self.rand = rng.uniform(0, 1, n)
        self.left_col = rng.integers(0, 5, n)

        verts = n * samples * 2
        faces = []
        for i in range(n):
            base = i * samples * 2
            for j in range(samples - 1):
                a = base + j * 2
                faces.append((a, a + 2, a + 3, a + 1))
        self.mesh = bpy.data.meshes.new(name)
        self.mesh.vertices.add(verts)
        self.mesh.loops.add(len(faces) * 4)
        self.mesh.polygons.add(len(faces))
        f = np.array(faces, dtype=np.int32)
        self.mesh.loops.foreach_set("vertex_index", f.ravel())
        self.mesh.polygons.foreach_set("loop_start", np.arange(0, len(faces) * 4, 4, dtype=np.int32))
        self.mesh.polygons.foreach_set("loop_total", np.full(len(faces), 4, dtype=np.int32))
        self.update(0.0)
        self.mesh.update()
        self.mesh.validate()
        self.mesh.shade_smooth()

        # UV: u along length, v across
        u = np.tile(np.linspace(0, 1, samples).repeat(2), n)
        v = np.tile(np.array([0.0, 1.0]), n * samples)
        vuv = np.stack([u, v], 1)
        uv = self.mesh.uv_layers.new(name="UVMap")
        uv.data.foreach_set("uv", vuv[f.ravel()].ravel())

        # per-vertex colour: mixed palette on the left, band colour on the right
        xs = np.tile(self.x.repeat(2), n)
        s = smoothstep(-1.4, 0.9, xs)
        lc = BANDS[np.repeat(self.left_col, samples * 2)]
        rc = BANDS[np.repeat(self.band, samples * 2)]
        col = lc * (1 - s[:, None]) + rc * s[:, None]
        # a hot, pale core inside the knot
        knot = np.exp(-((xs + 0.25) / 0.55) ** 2)
        col = col * (1 - 0.3 * knot[:, None]) + np.array([1.0, 0.75, 0.5]) * 0.3 * knot[:, None]
        rgba = np.concatenate([col, np.ones((len(col), 1))], 1).astype(np.float32)
        ca = self.mesh.color_attributes.new("col", "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", rgba.ravel())
        ra = self.mesh.attributes.new("rand", "FLOAT", "POINT")
        ra.data.foreach_set("value", np.repeat(self.rand, samples * 2).astype(np.float32))

        self.obj = bpy.data.objects.new(name, self.mesh)
        scene.collection.objects.link(self.obj)

    def centerline(self, t):
        x = self.x[None, :]
        n = self.n
        ph = self.phase[:, None]
        k = -0.25  # knot position
        s = smoothstep(k - 2.2, k + 2.2, x)
        w = np.exp(-((x - k) / 1.05) ** 2) * (1.0 if self.knot else 0.0)
        # flowing waves, travelling left -> right, integer cycles per loop
        wave_l = 0.38 * np.sin(TAU * (x / 3.1 - t) + ph) * (1 - s)
        wave_r = (0.30 + 0.10 * (x - k).clip(0)) * np.sin(TAU * (x / 3.6 - t) + 0.8 * self.band[:, None]) * s
        y_far = self.yl[:, None] * (1 - s) + self.yr[:, None] * s + wave_l + wave_r * (1 - w)
        z_far = self.zl[:, None] * (1 - s) + self.zr[:, None] * s
        z_far = z_far + 0.35 * np.sin(TAU * (x / 2.7 - t) + 1.7 * ph) * (1 - w)
        # the knot: a rotating twisted cable
        ang = self.theta[:, None] + 2.2 * (x - k) + TAU * t
        yk = self.rad[:, None] * np.cos(ang) + 0.05 * np.sin(TAU * t)
        zk = self.rad[:, None] * np.sin(ang) + self.layer
        y = y_far * (1 - w) + yk * w
        z = z_far * (1 - w) + zk * w
        xx = np.broadcast_to(x, (n, self.s))
        return np.stack([xx, y, z], -1)

    def update(self, t):
        c = self.centerline(t)
        tan = np.gradient(c, axis=1)
        tan /= np.linalg.norm(tan, axis=-1, keepdims=True)
        ref = np.array([0.0, 0.0, 1.0])
        b1 = np.cross(tan, ref)
        b1 /= np.linalg.norm(b1, axis=-1, keepdims=True) + 1e-9
        b2 = np.cross(tan, b1)
        x = self.x[None, :]
        phi = self.twist0[:, None] + self.twist_rate[:, None] * (0.55 * x) + 0.35 * np.sin(TAU * t + self.phase[:, None])
        wdir = b1 * np.cos(phi)[..., None] + b2 * np.sin(phi)[..., None]
        k = -0.25
        width = self.width * (1.0 - 0.45 * np.exp(-((x - k) / 0.9) ** 2))
        half = 0.5 * width[..., None] * wdir
        a = c - half
        b = c + half
        co = np.stack([a, b], 2).reshape(-1, 3).astype(np.float32)
        self.mesh.vertices.foreach_set("co", co.ravel())
        self.mesh.update()


front = RibbonField("silk", 170, 260, -6.5, 6.5, 0.16, 0.0)
back = RibbonField("silk_back", 90, 160, -7.5, 7.5, 0.42, -2.6, knot=False)
back.obj.scale = (1.25, 1.25, 1.0)


# ---------------------------------------------------------------- material
def silk_material(name, emission_gain):
    m = bpy.data.materials.new(name)
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    p = nt.nodes.new("ShaderNodeBsdfPrincipled")
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "col"
    rnd = nt.nodes.new("ShaderNodeAttribute")
    rnd.attribute_name = "rand"
    uvn = nt.nodes.new("ShaderNodeUVMap")
    uvn.uv_map = "UVMap"
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uvn.outputs["UV"], sep.inputs[0])
    tangent = nt.nodes.new("ShaderNodeTangent")
    tangent.direction_type = "UV_MAP"
    tangent.uv_map = "UVMap"

    # light pulses running along each ribbon: fract(u*K + rand - t)
    phase = nt.nodes.new("ShaderNodeValue")
    phase.name = "PHASE"
    mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = 3.0
    nt.links.new(sep.outputs["X"], mul.inputs[0])
    add = nt.nodes.new("ShaderNodeMath"); add.operation = "ADD"
    nt.links.new(mul.outputs[0], add.inputs[0]); nt.links.new(rnd.outputs["Fac"], add.inputs[1])
    sub = nt.nodes.new("ShaderNodeMath"); sub.operation = "SUBTRACT"
    nt.links.new(add.outputs[0], sub.inputs[0]); nt.links.new(phase.outputs[0], sub.inputs[1])
    fr = nt.nodes.new("ShaderNodeMath"); fr.operation = "FRACT"
    nt.links.new(sub.outputs[0], fr.inputs[0])
    # sharp comet: pow(fract, 18)
    pw = nt.nodes.new("ShaderNodeMath"); pw.operation = "POWER"; pw.inputs[1].default_value = 22.0
    nt.links.new(fr.outputs[0], pw.inputs[0])
    gain = nt.nodes.new("ShaderNodeMath"); gain.operation = "MULTIPLY"; gain.inputs[1].default_value = emission_gain
    nt.links.new(pw.outputs[0], gain.inputs[0])

    # brighten colour a touch towards the edges of the ribbon (woven sheen)
    nt.links.new(attr.outputs["Color"], p.inputs["Base Color"])
    nt.links.new(attr.outputs["Color"], p.inputs["Emission Color"])
    nt.links.new(gain.outputs[0], p.inputs["Emission Strength"])
    p.inputs["Roughness"].default_value = 0.32
    p.inputs["Anisotropic"].default_value = 0.85
    nt.links.new(tangent.outputs[0], p.inputs["Tangent"])
    p.inputs["Sheen Weight"].default_value = 0.5
    p.inputs["Sheen Roughness"].default_value = 0.35
    nt.links.new(attr.outputs["Color"], p.inputs["Sheen Tint"])
    p.inputs["Coat Weight"].default_value = 0.25
    p.inputs["Coat Roughness"].default_value = 0.12
    p.inputs["Specular IOR Level"].default_value = 0.6
    nt.links.new(p.outputs[0], out.inputs[0])
    return m, phase


silk_mat, silk_phase = silk_material("silk", 5.0)
back_mat, back_phase = silk_material("silk_back", 1.5)
front.obj.data.materials.append(silk_mat)
back.obj.data.materials.append(back_mat)

# ---------------------------------------------------------------- world + lights
world = bpy.data.worlds.new("w")
scene.world = world
wn = world.node_tree
wn.nodes.clear()
wo = wn.nodes.new("ShaderNodeOutputWorld")
bg = wn.nodes.new("ShaderNodeBackground")
tc = wn.nodes.new("ShaderNodeTexCoord")
sep = wn.nodes.new("ShaderNodeSeparateXYZ")
ramp = wn.nodes.new("ShaderNodeValToRGB")
wn.links.new(tc.outputs["Window"], sep.inputs[0])
wn.links.new(sep.outputs["Y"], ramp.inputs[0])
ramp.color_ramp.elements[0].color = (0.05, 0.01, 0.12, 1)
ramp.color_ramp.elements[1].color = (0.20, 0.02, 0.18, 1)
wn.links.new(ramp.outputs[0], bg.inputs[0])
bg.inputs[1].default_value = 1.0
wn.links.new(bg.outputs[0], wo.inputs[0])


def area(name, loc, rot, size, color, energy):
    d = bpy.data.lights.new(name, "AREA")
    d.size = size
    d.color = color
    d.energy = energy
    o = bpy.data.objects.new(name, d)
    o.location = loc
    o.rotation_euler = rot
    scene.collection.objects.link(o)
    return o


area("key", (-2.0, 3.5, 6.0), (math.radians(-30), math.radians(-15), 0), 6.0, (1.0, 0.95, 0.9), 700)
area("rimL", (-6.0, -3.0, 3.0), (math.radians(60), math.radians(-60), 0), 4.0, (1.0, 0.45, 0.2), 900)
area("rimR", (6.5, 3.0, 2.5), (math.radians(-55), math.radians(65), 0), 4.0, (0.3, 0.55, 1.0), 1000)
area("fill", (0.0, -4.0, 5.0), (math.radians(40), 0, 0), 8.0, (0.9, 0.5, 1.0), 200)

core = bpy.data.lights.new("core", "POINT")
core.energy = 260
core.color = (1.0, 0.62, 0.3)
core.shadow_soft_size = 0.3
core_o = bpy.data.objects.new("core", core)
core_o.location = (-0.25, 0.0, 0.9)
scene.collection.objects.link(core_o)

# glowing backdrop behind everything so nothing reads as void
bd_mesh = bpy.data.meshes.new("backdrop")
bd_mesh.from_pydata([(-14, -8, -6), (14, -8, -6), (14, 8, -6), (-14, 8, -6)], [], [(0, 1, 2, 3)])
bd = bpy.data.objects.new("backdrop", bd_mesh)
scene.collection.objects.link(bd)
bm = bpy.data.materials.new("backdrop")
bnt = bm.node_tree
bnt.nodes.clear()
bo = bnt.nodes.new("ShaderNodeOutputMaterial")
be = bnt.nodes.new("ShaderNodeEmission")
btc = bnt.nodes.new("ShaderNodeTexCoord")
bsep = bnt.nodes.new("ShaderNodeSeparateXYZ")
bramp = bnt.nodes.new("ShaderNodeValToRGB")
bnt.links.new(btc.outputs["Object"], bsep.inputs[0])
bmath = bnt.nodes.new("ShaderNodeMath"); bmath.operation = "MULTIPLY_ADD"
bmath.inputs[1].default_value = 1 / 28.0; bmath.inputs[2].default_value = 0.5
bnt.links.new(bsep.outputs["X"], bmath.inputs[0])
bnt.links.new(bmath.outputs[0], bramp.inputs[0])
els = bramp.color_ramp.elements
els[0].position = 0.25; els[0].color = (0.30, 0.03, 0.12, 1)
els[1].position = 0.75; els[1].color = (0.03, 0.06, 0.30, 1)
e = els.new(0.5); e.color = (0.25, 0.02, 0.35, 1)
bnt.links.new(bramp.outputs[0], be.inputs[0])
be.inputs[1].default_value = 1.0
bnt.links.new(be.outputs[0], bo.inputs[0])
bd_mesh.materials.append(bm)

# ---------------------------------------------------------------- camera
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 40
cam_data.sensor_width = 36
cam_data.dof.use_dof = True
cam_data.dof.focus_distance = 9.2
cam_data.dof.aperture_fstop = 2.4
cam = bpy.data.objects.new("cam", cam_data)
cam.location = (0.4, -2.2, 8.9)
cam.rotation_euler = (math.radians(14), math.radians(0), math.radians(0))
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
scene.view_settings.exposure = 0.0
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 6
scene.cycles.seed = 1
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
for k, v in (("Type", "Bloom"), ("Threshold", 1.0), ("Strength", 0.6), ("Size", 0.6), ("Quality", "High")):
    try:
        glare.inputs[k].default_value = v
    except (KeyError, TypeError, AttributeError):
        pass
gout = tree.nodes.new("NodeGroupOutput")
tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
tree.links.new(rl.outputs["Image"], glare.inputs["Image"])
tree.links.new(glare.outputs[0], gout.inputs[0])

for f in parse_frames(SPEC):
    t = ((f - 1) / FRAMES) % 1.0
    scene.frame_set(f)
    front.update(t)
    back.update(t)
    silk_phase.outputs[0].default_value = t * 2.0  # 2 pulse cycles per loop
    back_phase.outputs[0].default_value = t
    scene.render.filepath = os.path.join(OUT, f"frame_{f:04d}.png")
    bpy.ops.render.render(write_still=True)
