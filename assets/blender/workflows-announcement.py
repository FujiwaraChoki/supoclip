"""Renders the "Introducing workflows" announcement image.

One long video, as a film strip, flows in from the back and turns into five
vertical clips, one per workflow, each showing what that workflow makes.

    blender -b -P assets/blender/workflows-announcement.py -- OUT.png [SAMPLES]

Uses Cycles on the GPU (OptiX, else CUDA) when one is available.
"""

import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/workflows-announcement.png"
SAMPLES = int(argv[1]) if len(argv) > 1 else 512

# SupoClip palette: stone neutrals, near-black ink, one orange accent.
STONE = (0.02, 0.018, 0.016, 1)
INK = (0.035, 0.032, 0.03, 1)
SCREEN = (0.012, 0.011, 0.01, 1)
ORANGE = (1.0, 0.22, 0.02, 1)
CREAM = (0.98, 0.965, 0.94, 1)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def material(name, color, *, rough=0.4, metal=0.0, emit=0.0, coat=0.0, transmission=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Coat Weight"].default_value = coat
    bsdf.inputs["Transmission Weight"].default_value = transmission
    if emit:
        bsdf.inputs["Emission Color"].default_value = color
        bsdf.inputs["Emission Strength"].default_value = emit
    return mat


M_BODY = material("body", (0.09, 0.085, 0.08, 1), rough=0.28, metal=0.85, coat=0.4)
M_SCREEN = material("screen", SCREEN, rough=0.3, coat=0.6)
M_INK = material("ink", (0.86, 0.84, 0.8, 1), rough=0.5, emit=0.8)
M_DIM = material("dim", (0.3, 0.29, 0.28, 1), rough=0.5, emit=0.35)
M_ORANGE = material("orange", ORANGE, rough=0.3, emit=7.0)
M_FILM = material("film", (0.05, 0.047, 0.044, 1), rough=0.3, coat=0.8)
M_FRAME = material("frame", ORANGE, rough=0.4, emit=1.4)
M_FLOOR = material("floor", (0.55, 0.5, 0.45, 1), rough=0.9)
M_GROUND = material("ground", (0.012, 0.011, 0.01, 1), rough=0.18, coat=0.6)


def box(name, size, location, mat, *, bevel=0.0, rotation=(0, 0, 0), segments=6):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    if bevel:
        mod = obj.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = segments
        mod.limit_method = "NONE"
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.use_smooth = True
    return obj


def parent(child, to):
    child.parent = to
    child.matrix_parent_inverse = to.matrix_world.inverted()


def disc(name, radius, location, mat, depth=0.01):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=location, rotation=(math.pi / 2, 0, 0), vertices=48)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    return obj


def label(text, location, size, mat):
    bpy.ops.object.text_add(location=location, rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.object
    obj.data.body = text
    obj.data.size = size
    obj.data.extrude = 0.004
    obj.data.align_x = "CENTER"
    obj.data.align_y = "CENTER"
    obj.data.materials.append(mat)
    return obj


# One vertical clip: a rounded 9:16 card with a dark screen. Its contents are drawn on
# the screen's face (y slightly in front), in the card's local space.
CARD_W, CARD_H, CARD_D = 0.9, 1.6, 0.07
FACE = -CARD_D / 2 - 0.006


def card(name, x, z, yaw, tilt):
    body = box(name, (CARD_W, CARD_D, CARD_H), (0, 0, 0), M_BODY, bevel=0.07, segments=8)
    screen = box(name + "-screen", (CARD_W - 0.09, 0.01, CARD_H - 0.09), (0, -CARD_D / 2, 0), M_SCREEN, bevel=0.035)
    parent(screen, body)
    body["layout"] = (x, z, yaw, tilt)
    return body


def place(card_obj):
    # Contents are built around the origin first, then the card moves with them.
    x, z, yaw, tilt = card_obj["layout"]
    # A shallow arc in depth too: the centre clip comes forward.
    card_obj.location = (x, 0.55 * (abs(x) / 2.3) ** 1.6 - 0.25, z)
    card_obj.rotation_euler = (tilt, 0, yaw)


def on(card_obj, obj):
    parent(obj, card_obj)
    return obj


def bars(card_obj, rows, *, x0=-0.26, z0=-0.45, gap=0.1, accent_first=False):
    for index, width in enumerate(rows):
        mat = M_ORANGE if accent_first and index == 0 else M_INK
        on(card_obj, box(f"bar{index}", (width, 0.006, 0.035), (x0 + width / 2, FACE, z0 - index * gap), mat, bevel=0.017))


cards = []
# Positions on a shallow arc, centre card slightly forward and higher.
layout = [(-2.3, -0.05, 0.42, 0.04), (-1.16, 0.12, 0.2, 0.0), (0.0, 0.22, 0.0, -0.03), (1.16, 0.12, -0.2, 0.0), (2.3, -0.05, -0.42, 0.04)]

# Highlights: one standout moment, scored.
c = card("highlights", *layout[0])
on(c, disc("score", 0.17, (0, FACE, 0.28), M_ORANGE))
on(c, disc("score-hole", 0.11, (0, FACE - 0.004, 0.28), M_SCREEN))
bars(c, [0.52, 0.4], x0=-0.26, z0=-0.38)
cards.append(c)

# Series: numbered parts that follow on.
c = card("series", *layout[1])
for index, zc in enumerate((0.42, 0.0, -0.42)):
    on(c, box(f"part{index}", (0.52, 0.006, 0.3), (0, FACE, zc), M_FILM, bevel=0.05))
    on(c, label(str(index + 1), (0, FACE - 0.008, zc), 0.2, M_ORANGE if index == 0 else M_INK))
cards.append(c)

# Topic: only the moments about one subject light up.
c = card("topic", *layout[2])
ring = on(c, disc("lens", 0.17, (-0.06, FACE, 0.36), M_ORANGE))
on(c, disc("lens-hole", 0.12, (-0.06, FACE - 0.004, 0.36), M_SCREEN))
on(c, box("handle", (0.05, 0.006, 0.2), (0.12, FACE, 0.18), M_ORANGE, rotation=(0, math.radians(-45), 0), bevel=0.02))
for index, (zc, lit) in enumerate([(-0.12, False), (-0.27, True), (-0.42, False), (-0.57, True)]):
    on(c, box(f"moment{index}", (0.52 if lit else 0.4, 0.006, 0.045), (0, FACE, zc), M_INK if lit else M_DIM, bevel=0.02))
cards.append(c)

# Recap: the main points, listed.
c = card("recap", *layout[3])
for index, zc in enumerate((0.42, 0.18, -0.06, -0.3)):
    on(c, disc(f"dot{index}", 0.045, (-0.25, FACE, zc), M_ORANGE if index == 0 else M_INK))
    on(c, box(f"point{index}", (0.36 if index % 2 else 0.42, 0.006, 0.04), (0.02 + (0.36 if index % 2 else 0.42) / 2 - 0.18, FACE, zc), M_INK, bevel=0.02))
cards.append(c)

# Trailer: a quick preview that points to the full video.
c = card("trailer", *layout[4])
bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.24, depth=0.012, location=(0.03, FACE, 0.12), rotation=(math.pi / 2, math.radians(90), 0))
play = bpy.context.object
play.data.materials.append(M_ORANGE)
on(c, play)
for index, xc in enumerate((-0.22, -0.07, 0.08, 0.23)):
    on(c, box(f"cut{index}", (0.1, 0.006, 0.05), (xc, FACE, -0.42), M_INK if index % 2 == 0 else M_DIM, bevel=0.02))
cards.append(c)

for obj in cards:
    place(obj)
bpy.context.view_layer.update()

# The source: one long film strip lying under the cards, running off both edges.
STRIP_Y, STRIP_Z = 0.9, -1.42
box("strip", (16, 0.9, 0.04), (0, STRIP_Y, STRIP_Z), M_FILM, bevel=0.02)
# Each clip comes from one moment of the video: light up the frame under its thread.
sources = [round((obj.location.x * 0.85 + 8 - 0.11) / 0.29) for obj in cards]
for index in range(56):
    x = -8 + index * 0.29
    box(f"frame{index}", (0.22, 0.6, 0.01), (x, STRIP_Y, STRIP_Z + 0.022), M_FRAME if index in sources else M_SCREEN, bevel=0.01)
    for side in (-0.39, 0.39):
        box(f"hole{index}{side}", (0.07, 0.07, 0.012), (x + 0.07, STRIP_Y + side, STRIP_Z + 0.022), M_FLOOR, bevel=0.012)

# Thin orange threads from the strip up to each card: the video becoming clips.
for index, obj in enumerate(cards):
    start = Vector((-8 + sources[index] * 0.29, STRIP_Y, STRIP_Z + 0.03))
    end = obj.matrix_world @ Vector((0, 0.0, -CARD_H / 2 - 0.02))
    curve = bpy.data.curves.new(f"thread{index}", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.006
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(1)
    for point, co, handle in ((spline.bezier_points[0], start, start + Vector((0, 0, 0.35))), (spline.bezier_points[1], end, end + Vector((0, 0, -0.35)))):
        point.co = co
        point.handle_left_type = point.handle_right_type = "FREE"
        point.handle_right = handle
        point.handle_left = co * 2 - handle
    thread = bpy.data.objects.new(f"thread{index}", curve)
    thread.data.materials.append(M_ORANGE)
    scene.collection.objects.link(thread)

# Floor and a soft backdrop that curves up behind everything.
bpy.ops.mesh.primitive_plane_add(size=60, location=(0, 0, -1.47))
bpy.context.object.data.materials.append(M_GROUND)
backdrop = box("backdrop", (60, 0.1, 30), (0, 9, 10), M_GROUND)

world = bpy.data.worlds.new("world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = STONE
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
scene.world = world


def area(name, location, energy, size, color=(1, 1, 1)):
    light = bpy.data.lights.new(name, "AREA")
    light.energy = energy
    light.size = size
    light.color = color
    obj = bpy.data.objects.new(name, light)
    obj.location = location
    scene.collection.objects.link(obj)
    direction = Vector((0, 0.2, 0)) - Vector(location)
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


area("key", (-4, -6, 6), 900, 4, (1, 0.96, 0.9))
area("fill", (6, -5, 2), 160, 8, (0.92, 0.95, 1))
area("rim", (0, 3, 4.5), 1300, 9, (1, 0.62, 0.35))

cam_data = bpy.data.cameras.new("camera")
cam_data.lens = 52
cam_data.dof.use_dof = True
cam_data.dof.aperture_fstop = 2.8
camera = bpy.data.objects.new("camera", cam_data)
camera.location = (0, -10.4, 1.2)
scene.collection.objects.link(camera)
camera.rotation_euler = (Vector((0, 0.3, -0.12)) - camera.location).to_track_quat("-Z", "Y").to_euler()
cam_data.dof.focus_object = cards[2]
scene.camera = camera

scene.render.engine = "CYCLES"
scene.render.resolution_x = 2000
scene.render.resolution_y = 860
scene.render.resolution_percentage = 100
scene.render.filepath = OUT
scene.render.image_settings.file_format = "PNG"
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Punchy"
scene.view_settings.exposure = 0.0
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True

prefs = bpy.context.preferences.addons["cycles"].preferences
for backend in ("OPTIX", "CUDA"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        devices = [d for d in prefs.devices if d.type == backend]
        if devices:
            for device in prefs.devices:
                device.use = device.type == backend
            scene.cycles.device = "GPU"
            scene.cycles.denoiser = "OPTIX" if backend == "OPTIX" else "OPENIMAGEDENOISE"
            print("render device:", backend, [d.name for d in devices])
            break
    except TypeError:
        continue

# A soft glow on what emits light, like a screen in a dim room.
scene.use_nodes = True
tree = scene.node_tree if hasattr(scene, "node_tree") and scene.node_tree else None
if tree is None and hasattr(scene, "compositing_node_group"):
    tree = bpy.data.node_groups.new("compositor", "CompositorNodeTree")
    scene.compositing_node_group = tree
if tree is not None:
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    layers = tree.nodes.new("CompositorNodeRLayers")
    glare = tree.nodes.new("CompositorNodeGlare")
    for attr, value in (("glare_type", "FOG_GLOW"), ("quality", "HIGH"), ("threshold", 0.85), ("size", 8)):
        if hasattr(glare, attr):
            setattr(glare, attr, value)
    for name, value in (("Threshold", 0.85), ("Strength", 0.6), ("Size", 0.5)):
        if name in glare.inputs:
            try:
                glare.inputs[name].default_value = value
            except (TypeError, AttributeError):
                pass
    if "Type" in glare.inputs:
        try:
            glare.inputs["Type"].default_value = "Fog Glow"
        except (TypeError, AttributeError):
            pass
    try:
        out = tree.nodes.new("CompositorNodeComposite")
    except RuntimeError:
        out = tree.nodes.new("NodeGroupOutput")
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    tree.links.new(layers.outputs["Image"], glare.inputs["Image"])
    tree.links.new(glare.outputs["Image"] if "Image" in glare.outputs else glare.outputs[0], out.inputs[0])

bpy.ops.render.render(write_still=True)
