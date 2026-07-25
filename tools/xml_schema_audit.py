#!/usr/bin/env python3
"""Download pinned SEC XML specifications and inventory schema/sample fields."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


USER_AGENT = "Stanford EDGAR research nick.bettencourt@stanford.edu"
XSD_NAMESPACE = "http://www.w3.org/2001/XMLSchema"
XSD = f"{{{XSD_NAMESPACE}}}"


def local_name(value: str | None) -> str:
    return str(value or "").rsplit("}", 1)[-1].split(":", 1)[-1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())


def unpack_recursive(archive: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)
    processed = set()
    while True:
        nested = [
            path
            for path in destination.rglob("*.zip")
            if path.resolve() not in processed
        ]
        if not nested:
            break
        for path in nested:
            processed.add(path.resolve())
            nested_destination = path.with_suffix("")
            nested_destination.mkdir(exist_ok=True)
            with zipfile.ZipFile(path) as bundle:
                bundle.extractall(nested_destination)


def schema_inventory(root: Path) -> dict:
    elements = Counter()
    attributes = Counter()
    xsd_files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.name.casefold().endswith((".xsd", ".xsd.xml"))
    ]
    for path in xsd_files:
        try:
            schema = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for node in schema.iter(f"{XSD}element"):
            name = local_name(node.get("name") or node.get("ref"))
            if name:
                elements[name] += 1
        for node in schema.iter(f"{XSD}attribute"):
            name = local_name(node.get("name") or node.get("ref"))
            if name:
                attributes[name] += 1
    return {
        "xsd_files": len(xsd_files),
        "declared_elements": sorted(elements),
        "declared_attributes": sorted(attributes),
        "element_declaration_count": sum(elements.values()),
        "attribute_declaration_count": sum(attributes.values()),
    }


def sample_paths(root: Path) -> dict:
    paths = Counter()
    attributes = Counter()
    sample_files = []
    for path in root.rglob("*.xml"):
        if path.name.casefold().endswith(".xsd.xml"):
            continue
        try:
            document = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        sample_files.append(path)

        def walk(node: ET.Element, ancestors: tuple[str, ...]) -> None:
            current = (*ancestors, local_name(node.tag))
            paths["/".join(current)] += 1
            for name in node.attrib:
                if not str(name).casefold().startswith("xmlns"):
                    attributes[f"{'/'.join(current)}/@{local_name(name)}"] += 1
            for child in node:
                walk(child, current)

        walk(document, ())
    return {
        "sample_xml_files": len(sample_files),
        "observed_element_paths": sorted(paths),
        "observed_attribute_paths": sorted(attributes),
    }


def main() -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=here / "xml_specs_manifest.json")
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    families = {}
    for specification in manifest["specifications"]:
        family = specification["family"]
        archive = args.cache_dir / f"{family}.zip"
        extracted = args.cache_dir / family
        if args.download and not archive.exists():
            download(specification["url"], archive)
        if not archive.exists():
            raise SystemExit(f"Missing {archive}; rerun with --download")
        actual_digest = digest(archive)
        if actual_digest != specification["sha256"]:
            raise SystemExit(
                f"Checksum mismatch for {family}: expected {specification['sha256']}, "
                f"found {actual_digest}"
            )
        if not extracted.exists():
            unpack_recursive(archive, extracted)
        families[family] = {
            "forms": specification["forms"],
            "version": specification["version"],
            "url": specification["url"],
            "sha256": actual_digest,
            **schema_inventory(extracted),
            **sample_paths(extracted),
        }

    report = {
        "schema_version": 1,
        "source_index": manifest["source_index"],
        "families": families,
        "totals": {
            "families": len(families),
            "xsd_files": sum(item["xsd_files"] for item in families.values()),
            "sample_xml_files": sum(
                item["sample_xml_files"] for item in families.values()
            ),
            "unique_declared_elements": len(
                {
                    field
                    for item in families.values()
                    for field in item["declared_elements"]
                }
            ),
            "unique_observed_paths": len(
                {
                    field
                    for item in families.values()
                    for field in item["observed_element_paths"]
                }
            ),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
