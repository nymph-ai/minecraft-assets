#!/usr/bin/env python3
import argparse
import base64
import json
import pathlib
import urllib.request
import zipfile

MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
        out.write(resp.read())
    return dest


def load_json_from_zip(zf, name):
    with zf.open(name) as f:
        return json.load(f)


def strip_namespace(value):
    if value.startswith("minecraft:"):
        return value[len("minecraft:") :]
    return value


def normalize_texture_path(value, default_prefix):
    if not value:
        return None
    value = strip_namespace(value)
    if value.startswith("block/"):
        value = "blocks/" + value[len("block/") :]
    elif value.startswith("item/"):
        value = "items/" + value[len("item/") :]
    elif value.startswith("blocks/") or value.startswith("items/"):
        pass
    elif "/" not in value:
        value = f"{default_prefix}/{value}"
    return f"minecraft:{value}"


def normalize_model_ref(model_ref, default_kind):
    model_ref = strip_namespace(model_ref)
    if model_ref.startswith("block/"):
        return "block", model_ref[len("block/") :]
    if model_ref.startswith("item/"):
        return "item", model_ref[len("item/") :]
    if model_ref.startswith("blocks/"):
        return "block", model_ref[len("blocks/") :]
    if model_ref.startswith("items/"):
        return "item", model_ref[len("items/") :]
    return default_kind, model_ref


def resolve_model_textures(models, kind, name, visiting):
    key = (kind, name)
    if key in visiting:
        return {}
    visiting.add(key)
    model = models.get(key)
    if not model:
        visiting.remove(key)
        return {}
    textures = {}
    parent = model.get("parent")
    if isinstance(parent, str):
        parent_kind, parent_name = normalize_model_ref(parent, kind)
        textures.update(resolve_model_textures(models, parent_kind, parent_name, visiting))
    textures.update(model.get("textures", {}))
    resolved = {}
    for k, v in textures.items():
        if not isinstance(v, str):
            resolved[k] = v
            continue
        depth = 0
        while v.startswith("#") and depth < 10:
            ref = v[1:]
            if ref not in textures or not isinstance(textures[ref], str):
                break
            v = textures[ref]
            depth += 1
        resolved[k] = v
    visiting.remove(key)
    return resolved


def pick_texture(textures):
    if not textures:
        return None
    for key in ("all", "texture", "side", "end", "top", "bottom", "layer0", "particle"):
        if key in textures and isinstance(textures[key], str):
            return textures[key]
    for key in sorted(textures.keys()):
        value = textures[key]
        if isinstance(value, str):
            return value
    return None


def pick_blockstate_model(state):
    variants = state.get("variants")
    if isinstance(variants, dict) and variants:
        key = sorted(variants.keys())[0]
        entry = variants[key]
        if isinstance(entry, list) and entry:
            entry = entry[0]
        if isinstance(entry, dict):
            return entry.get("model")
    multipart = state.get("multipart")
    if isinstance(multipart, list) and multipart:
        entry = multipart[0].get("apply")
        if isinstance(entry, list) and entry:
            entry = entry[0]
        if isinstance(entry, dict):
            return entry.get("model")
    return None


def base64_texture(texture_path):
    if texture_path is None:
        return None
    return "data:image/png;base64," + base64.b64encode(texture_path).decode("ascii")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True, separators=(",", ":"))


def main():
    parser = argparse.ArgumentParser(description="Build minecraft-assets data from official Mojang client assets.")
    parser.add_argument("--version", required=True, help="Minecraft version, e.g. 1.21.11")
    parser.add_argument("--cache-dir", default=".cache", help="Cache dir for downloaded client jars")
    parser.add_argument("--data-dir", default="data", help="Output data directory root")
    parser.add_argument("--force", action="store_true", help="Overwrite existing data")
    args = parser.parse_args()

    version = args.version
    cache_dir = pathlib.Path(args.cache_dir)
    data_root = pathlib.Path(args.data_dir)

    with urllib.request.urlopen(MANIFEST_URL) as resp:
        manifest = json.load(resp)
    version_info = next((v for v in manifest["versions"] if v["id"] == version), None)
    if not version_info:
        raise SystemExit(f"Version {version} not found in manifest")
    with urllib.request.urlopen(version_info["url"]) as resp:
        meta = json.load(resp)

    client_url = meta["downloads"]["client"]["url"]
    jar_path = cache_dir / f"minecraft-{version}-client.jar"
    download(client_url, jar_path)

    out_dir = data_root / version
    if out_dir.exists() and args.force:
        for child in out_dir.iterdir():
            if child.is_dir():
                for sub in child.rglob("*"):
                    if sub.is_file() or sub.is_symlink():
                        sub.unlink()
                for sub in sorted(child.rglob("*"), reverse=True):
                    if sub.is_dir():
                        sub.rmdir()
                child.rmdir()
            else:
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    models = {}
    blockstates = {}
    item_models = {}

    with zipfile.ZipFile(jar_path) as zf:
        for name in zf.namelist():
            if not name.startswith("assets/minecraft/blockstates/") or not name.endswith(".json"):
                continue
            key = name.split("/")[-1].replace(".json", "")
            blockstates[key] = load_json_from_zip(zf, name)

        for name in zf.namelist():
            if not name.startswith("assets/minecraft/models/block/") or not name.endswith(".json"):
                continue
            key = name.split("/")[-1].replace(".json", "")
            models[("block", key)] = load_json_from_zip(zf, name)

        for name in zf.namelist():
            if not name.startswith("assets/minecraft/models/item/") or not name.endswith(".json"):
                continue
            key = name.split("/")[-1].replace(".json", "")
            models[("item", key)] = load_json_from_zip(zf, name)
            item_models[key] = models[("item", key)]

        texture_prefix = "assets/minecraft/textures/"
        for name in zf.namelist():
            if not name.startswith(texture_prefix) or name.endswith("/"):
                continue
            rel = name[len(texture_prefix) :]
            if rel.startswith("block/"):
                rel = "blocks/" + rel[len("block/") :]
            elif rel.startswith("item/"):
                rel = "items/" + rel[len("item/") :]
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                out.write(src.read())

    blocks_textures = []
    for name in sorted(blockstates.keys()):
        model_ref = pick_blockstate_model(blockstates[name])
        model_name = None
        texture_path = None
        if isinstance(model_ref, str):
            model_kind, model_name = normalize_model_ref(model_ref, "block")
            textures = resolve_model_textures(models, model_kind, model_name, set())
            texture_ref = pick_texture(textures)
            texture_path = normalize_texture_path(texture_ref, "blocks")
        if not texture_path:
            texture_path = "minecraft:missingno"
        model_out = f"minecraft:blocks/{model_name}" if model_name else "minecraft:blocks/air"
        blocks_textures.append(
            {
                "name": name,
                "blockState": name,
                "model": model_out,
                "texture": texture_path,
            }
        )

    items_textures = []
    for name in sorted(item_models.keys()):
        textures = resolve_model_textures(models, "item", name, set())
        texture_ref = pick_texture(textures)
        texture_path = normalize_texture_path(texture_ref, "items")
        if not texture_path:
            texture_path = "minecraft:missingno"
        items_textures.append(
            {
                "name": name,
                "model": name,
                "texture": texture_path,
            }
        )

    texture_content = []

    def texture_bytes(texture_entry):
        if texture_entry is None:
            return None
        rel = strip_namespace(texture_entry)
        path = out_dir / (rel + ".png")
        if not path.exists():
            return None
        return path.read_bytes()

    for entry in blocks_textures + items_textures:
        texture_content.append(
            {
                "name": entry["name"],
                "texture": base64_texture(texture_bytes(entry["texture"])),
            }
        )

    write_json(out_dir / "blocks_states.json", dict(sorted(blockstates.items())))
    write_json(out_dir / "blocks_models.json", dict(sorted({k[1]: v for k, v in models.items() if k[0] == "block"}.items())))
    write_json(out_dir / "blocks_textures.json", blocks_textures)
    write_json(out_dir / "items_textures.json", items_textures)
    write_json(out_dir / "texture_content.json", texture_content)


if __name__ == "__main__":
    main()
