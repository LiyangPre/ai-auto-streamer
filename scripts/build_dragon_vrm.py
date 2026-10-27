import io
import json
import struct
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/models/vrm-sample.vrm"
OUTPUT = ROOT / "data/models/silver-dragon.vrm"
THUMBNAIL = ROOT / "vibe_images/dragon-vtuber-costume-v3_1792937038403_d44c6282.png"


def normalize(vector):
    length = np.linalg.norm(vector)
    return vector / length if length else vector


def tube(points, radii, sides=12):
    points = np.asarray(points, dtype=np.float64)
    if np.isscalar(radii):
        radii = np.full(len(points), radii)
    vertices = []
    for i, (point, radius) in enumerate(zip(points, radii)):
        tangent = normalize(points[min(i + 1, len(points) - 1)] - points[max(i - 1, 0)])
        reference = np.array([0.0, 0.0, 1.0])
        if abs(np.dot(tangent, reference)) > 0.9:
            reference = np.array([0.0, 1.0, 0.0])
        u = normalize(np.cross(tangent, reference))
        v = normalize(np.cross(tangent, u))
        for j in range(sides):
            angle = 2 * np.pi * j / sides
            vertices.append(point + radius * (np.cos(angle) * u + np.sin(angle) * v))
    faces = []
    for i in range(len(points) - 1):
        for j in range(sides):
            a = i * sides + j
            b = i * sides + (j + 1) % sides
            c = (i + 1) * sides + (j + 1) % sides
            d = (i + 1) * sides + j
            faces.extend([[a, b, c], [a, c, d]])
    end = (len(points) - 1) * sides
    for j in range(1, sides - 1):
        faces.append([0, j + 1, j])
        faces.append([end, end + j, end + j + 1])
    return np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.uint32)


def skirt():
    profile_y = [0.025, 0.08, 0.25, 0.45, 0.65, 0.82, 0.96, 1.055]
    profile_x = [0.49, 0.47, 0.39, 0.31, 0.24, 0.18, 0.145, 0.135]
    profile_z = [0.32, 0.30, 0.25, 0.20, 0.16, 0.125, 0.105, 0.10]
    rings = 42
    sides = 96
    ys = np.linspace(profile_y[0], profile_y[-1], rings)
    vertices = []
    for y in ys:
        rx = np.interp(y, profile_y, profile_x)
        rz = np.interp(y, profile_y, profile_z)
        for j in range(sides):
            angle = 2 * np.pi * j / sides
            vertices.append([rx * np.sin(angle), y, rz * np.cos(angle) - 0.015])
    faces = []
    for i in range(rings - 1):
        y = (ys[i] + ys[i + 1]) * 0.5
        slit_width = 0.34 * max(0.0, (0.78 - y) / 0.78)
        for j in range(sides):
            angle = 2 * np.pi * (j + 0.5) / sides
            distance = abs((angle + 0.28 + np.pi) % (2 * np.pi) - np.pi)
            if y < 0.78 and distance < slit_width:
                continue
            a = i * sides + j
            b = i * sides + (j + 1) % sides
            c = (i + 1) * sides + (j + 1) % sides
            d = (i + 1) * sides + j
            faces.extend([[a, c, b], [a, d, c]])
    return np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.uint32)


def wing(side):
    root = np.array([side * 0.10, 1.24, -0.07])
    elbow = np.array([side * 0.37, 1.47, -0.10])
    tips = [
        np.array([side * 0.78, 1.55, -0.13]),
        np.array([side * 0.84, 1.05, -0.12]),
        np.array([side * 0.59, 0.96, -0.10]),
        np.array([side * 0.36, 1.04, -0.08]),
        np.array([side * 0.12, 1.15, -0.06]),
    ]
    membrane_vertices = []
    membrane_faces = []
    for a, b in zip(tips[:-1], tips[1:]):
        middle = (a + b) * 0.5
        control = elbow + (middle - elbow) * 0.42
        edge = []
        for t in np.linspace(0, 1, 13):
            edge.append((1 - t) ** 2 * a + 2 * (1 - t) * t * control + t ** 2 * b)
        start = len(membrane_vertices)
        membrane_vertices.append(elbow)
        membrane_vertices.extend(edge)
        for j in range(12):
            face = [start, start + j + 1, start + j + 2]
            membrane_faces.append(face if side > 0 else list(reversed(face)))
    bones = []
    bone_radii = []
    bone_paths = [([root, (root + elbow) * 0.5, elbow], [0.022, 0.018, 0.013])]
    for tip in tips:
        middle = elbow * 0.45 + tip * 0.55
        middle[2] -= 0.012
        bone_paths.append(([elbow, middle, tip], [0.012, 0.008, 0.0025]))
    for points, radii in bone_paths:
        v, f = tube(points, radii, 10)
        bones.append((v, f))
    return (np.asarray(membrane_vertices, dtype=np.float32), np.asarray(membrane_faces, dtype=np.uint32)), bones


def diamond(center, radius, height):
    x, y, z = center
    vertices = np.array([
        [x, y + height, z], [x, y - height, z],
        [x + radius, y, z], [x - radius, y, z],
        [x, y, z + radius], [x, y, z - radius],
    ], dtype=np.float32)
    faces = np.array([
        [0, 2, 4], [0, 4, 3], [0, 3, 5], [0, 5, 2],
        [1, 4, 2], [1, 3, 4], [1, 5, 3], [1, 2, 5],
    ], dtype=np.uint32)
    return vertices, faces


class VRMBuilder:
    def __init__(self, source):
        self.source = source.read_bytes()
        if self.source[:4] != b"glTF":
            raise ValueError("Source must be a binary VRM")
        json_length, json_type = struct.unpack_from("<I4s", self.source, 12)
        if json_type != b"JSON":
            raise ValueError("Missing glTF JSON chunk")
        self.data = json.loads(self.source[20:20 + json_length])
        bin_header = 20 + json_length
        bin_length, bin_type = struct.unpack_from("<I4s", self.source, bin_header)
        if bin_type != b"BIN\x00":
            raise ValueError("Missing glTF binary chunk")
        self.binary = bytearray(self.source[bin_header + 8:bin_header + 8 + bin_length])
        self.parents = {}
        for index, node in enumerate(self.data["nodes"]):
            for child in node.get("children", []):
                self.parents[child] = index
        self.world_matrices = {}

    def node_index(self, name):
        return next(i for i, node in enumerate(self.data["nodes"]) if node.get("name") == name)

    def world_matrix(self, index):
        if index in self.world_matrices:
            return self.world_matrices[index]
        node = self.data["nodes"][index]
        if "matrix" in node:
            local = np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
        else:
            x, y, z, w = node.get("rotation", [0, 0, 0, 1])
            local = trimesh.transformations.quaternion_matrix([w, x, y, z])
            local[:3, :3] *= np.asarray(node.get("scale", [1, 1, 1]))
            local[:3, 3] = node.get("translation", [0, 0, 0])
        parent = self.parents.get(index)
        world = self.world_matrix(parent) @ local if parent is not None else local
        self.world_matrices[index] = world
        return world

    def append_buffer_view(self, payload, target=None):
        while len(self.binary) % 4:
            self.binary.append(0)
        offset = len(self.binary)
        self.binary.extend(payload)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target:
            view["target"] = target
        self.data["bufferViews"].append(view)
        return len(self.data["bufferViews"]) - 1

    def accessor(self, array, component_type, accessor_type, target, bounds=False):
        array = np.ascontiguousarray(array)
        view = self.append_buffer_view(array.tobytes(), target)
        accessor = {
            "bufferView": view,
            "componentType": component_type,
            "count": len(array),
            "type": accessor_type,
        }
        if bounds:
            accessor["min"] = np.min(array, axis=0).astype(float).tolist()
            accessor["max"] = np.max(array, axis=0).astype(float).tolist()
        self.data["accessors"].append(accessor)
        return len(self.data["accessors"]) - 1

    def replace_image(self, image_index, image):
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        view = self.append_buffer_view(output.getvalue())
        self.data["images"][image_index] = {
            "name": self.data["images"][image_index].get("name", "Texture"),
            "mimeType": "image/png",
            "bufferView": view,
        }

    def image_bytes(self, image_index):
        image = self.data["images"][image_index]
        view = self.data["bufferViews"][image["bufferView"]]
        start = view.get("byteOffset", 0)
        return self.binary[start:start + view["byteLength"]]

    def recolor(self, image_index, mode):
        image = Image.open(io.BytesIO(self.image_bytes(image_index))).convert("RGBA")
        array = np.asarray(image).astype(np.float32) / 255.0
        luminance = np.sum(array[:, :, :3] * np.array([0.2126, 0.7152, 0.0722]), axis=2)
        if mode == "silver":
            level = 0.60 + 0.38 * np.power(luminance, 0.55)
            color = np.stack([level * 0.98, level * 0.98, np.minimum(1, level * 1.035)], axis=2)
        elif mode == "pearl":
            level = 0.70 + 0.29 * np.power(luminance, 0.60)
            color = np.stack([level, level, np.minimum(1, level * 1.02)], axis=2)
        elif mode == "iris":
            level = 0.28 + 0.65 * np.power(luminance, 0.70)
            color = np.stack([level * 0.68, level * 0.56, np.minimum(1, level * 0.98)], axis=2)
        else:
            raise ValueError(mode)
        array[:, :, :3] = np.clip(color, 0, 1)
        self.replace_image(image_index, Image.fromarray((array * 255).astype(np.uint8), "RGBA"))

    def material(self, name, color, metallic=0.0, roughness=0.65, alpha=False):
        shade = [color[0] * 0.66, color[1] * 0.64, color[2] * 0.76]
        material = {
            "name": name,
            "doubleSided": True,
            "pbrMetallicRoughness": {
                "baseColorFactor": color,
                "metallicFactor": metallic,
                "roughnessFactor": roughness,
            },
            "extensions": {
                "VRMC_materials_mtoon": {
                    "specVersion": "1.0",
                    "shadeColorFactor": shade,
                    "shadingShiftFactor": -0.05,
                    "shadingToonyFactor": 0.9,
                    "giEqualizationFactor": 0.9,
                    "outlineWidthMode": "worldCoordinates",
                    "outlineWidthFactor": 0.0007,
                    "outlineColorFactor": [0.24, 0.22, 0.30],
                    "outlineLightingMixFactor": 1,
                    "parametricRimColorFactor": [0, 0, 0],
                    "parametricRimFresnelPowerFactor": 5,
                    "parametricRimLiftFactor": 0,
                    "rimLightingMixFactor": 1,
                    "transparentWithZWrite": False,
                    "renderQueueOffsetNumber": 1 if alpha else 0,
                    "uvAnimationScrollXSpeedFactor": 0,
                    "uvAnimationScrollYSpeedFactor": 0,
                    "uvAnimationRotationSpeedFactor": 0,
                }
            },
        }
        if alpha:
            material["alphaMode"] = "BLEND"
            material["alphaCutoff"] = 0.01
        self.data["materials"].append(material)
        return len(self.data["materials"]) - 1

    def add_mesh(self, name, geometry, material, parent):
        vertices, faces = geometry
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        positions = self.accessor(mesh.vertices.astype(np.float32), 5126, "VEC3", 34962, True)
        normals = self.accessor(mesh.vertex_normals.astype(np.float32), 5126, "VEC3", 34962)
        indices = self.accessor(mesh.faces.reshape(-1).astype(np.uint32), 5125, "SCALAR", 34963)
        self.data["meshes"].append({
            "name": name,
            "primitives": [{
                "attributes": {"POSITION": positions, "NORMAL": normals},
                "indices": indices,
                "material": material,
                "mode": 4,
            }],
        })
        node = {"name": name, "mesh": len(self.data["meshes"]) - 1}
        if parent:
            parent_index = self.node_index(parent)
            node["matrix"] = np.linalg.inv(self.world_matrix(parent_index)).T.reshape(-1).tolist()
            self.data["nodes"][parent_index].setdefault("children", []).append(len(self.data["nodes"]))
        else:
            self.data["scenes"][self.data["scene"]]["nodes"].append(len(self.data["nodes"]))
        self.data["nodes"].append(node)

    def save(self):
        self.data["buffers"][0]["byteLength"] = len(self.binary)
        meta = self.data["extensions"]["VRMC_vrm"]["meta"]
        meta["name"] = "Silver Dragon VTuber"
        meta["version"] = "1.0.0"
        meta["authors"] = ["pixiv Inc.", "AI Auto Streamer"]
        json_bytes = json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        json_bytes += b" " * ((-len(json_bytes)) % 4)
        binary = bytes(self.binary) + b"\x00" * ((-len(self.binary)) % 4)
        total = 12 + 8 + len(json_bytes) + 8 + len(binary)
        OUTPUT.write_bytes(
            struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<I4s", len(json_bytes), b"JSON") + json_bytes
            + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
        )


def main():
    builder = VRMBuilder(SOURCE)
    for image in [5, 13, 15]:
        builder.recolor(image, "silver")
    for image in [2, 3, 4]:
        builder.recolor(image, "pearl")
    builder.recolor(8, "iris")

    for index in [1, 2, 3, 4, 10, 12]:
        material = builder.data["materials"][index]
        mtoon = material["extensions"]["VRMC_materials_mtoon"]
        if index in [4, 10, 12]:
            mtoon["outlineColorFactor"] = [0.24, 0.22, 0.30]
        else:
            mtoon["shadeColorFactor"] = [0.68, 0.67, 0.78]
            mtoon["outlineColorFactor"] = [0.30, 0.28, 0.38]

    builder.data["meshes"][0]["primitives"] = [
        primitive for primitive in builder.data["meshes"][0]["primitives"]
        if primitive.get("material") != 1
    ]

    pearl = builder.material("Dragon_Pearl_Silk", [0.91, 0.90, 0.96, 1], 0.08, 0.48)
    silver = builder.material("Dragon_Silver", [0.75, 0.76, 0.86, 1], 0.48, 0.32)
    lavender = builder.material("Dragon_Lavender", [0.58, 0.50, 0.78, 1], 0.12, 0.48)
    membrane = builder.material("Dragon_Wing_Membrane", [0.78, 0.70, 0.92, 0.72], 0.0, 0.72, True)

    builder.add_mesh("Dragon_Gown", skirt(), pearl, "J_Bip_C_Hips")

    for side, label in [(-1, "R"), (1, "L")]:
        x = side * 0.088
        points = [
            [x, 1.515, -0.005],
            [side * 0.105, 1.575, -0.012],
            [side * 0.125, 1.645, -0.025],
            [side * 0.133, 1.715, -0.045],
            [side * 0.122, 1.775, -0.070],
        ]
        builder.add_mesh(f"Dragon_Horn_{label}", tube(points, [0.036, 0.032, 0.024, 0.014, 0.0015], 16), silver, "J_Bip_C_Head")
        for i in range(1, 4):
            p = np.asarray(points[i], dtype=float)
            tangent = normalize(np.asarray(points[i + 1]) - np.asarray(points[i - 1]))
            builder.add_mesh(
                f"Dragon_Horn_Ring_{label}_{i}",
                tube([p - tangent * 0.004, p + tangent * 0.004], [[0.035, 0.027, 0.017][i - 1]] * 2, 16),
                lavender,
                "J_Bip_C_Head",
            )

        wing_membrane, wing_bones = wing(side)
        builder.add_mesh(f"Dragon_Wing_Membrane_{label}", wing_membrane, membrane, "J_Bip_C_UpperChest")
        for i, geometry in enumerate(wing_bones):
            builder.add_mesh(f"Dragon_Wing_Bone_{label}_{i}", geometry, silver, "J_Bip_C_UpperChest")

    tail_points = [
        [0.0, 0.64, -0.10], [0.015, 0.42, -0.16], [0.025, 0.22, -0.24],
        [0.035, 0.08, -0.31], [0.055, 0.025, -0.37], [0.075, 0.045, -0.405],
    ]
    builder.add_mesh("Dragon_Hidden_Tail", tube(tail_points, [0.04, 0.037, 0.030, 0.022, 0.012, 0.0015], 14), silver, "J_Bip_C_Hips")

    waist_points = []
    for angle in np.linspace(0, 2 * np.pi, 73):
        waist_points.append([0.139 * np.sin(angle), 1.052, 0.104 * np.cos(angle) - 0.015])
    builder.add_mesh("Dragon_Waist_Silver_Trim", tube(waist_points, 0.004, 8), silver, "J_Bip_C_Hips")

    for i, y in enumerate([1.10, 1.16, 1.22]):
        builder.add_mesh(f"Dragon_Bodice_Ornament_{i}", diamond([0, y, 0.135], 0.012, 0.020), silver, "J_Bip_C_Chest")
    builder.add_mesh("Dragon_Bodice_Gem", diamond([0, 1.16, 0.149], 0.008, 0.012), lavender, "J_Bip_C_Chest")

    spiral = []
    for t in np.linspace(0, 4.5 * np.pi, 90):
        radius = 0.004 + 0.0022 * t
        spiral.append([-0.128 + radius * np.cos(t), 1.51 + radius * np.sin(t), 0.055])
    builder.add_mesh("Dragon_Hair_Spiral", tube(spiral, 0.0035, 8), silver, "J_Bip_C_Head")

    thumbnail = Image.open(THUMBNAIL).convert("RGBA")
    thumbnail = thumbnail.crop((270, 70, 754, 554)).resize((512, 512), Image.Resampling.LANCZOS)
    builder.replace_image(18, thumbnail)
    builder.save()
    print(f"Created {OUTPUT} ({OUTPUT.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
