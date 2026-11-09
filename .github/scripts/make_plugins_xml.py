"""Write a QGIS plugin repository file (plugins.xml) from metadata.txt.

QGIS: Plugins > Manage and Install Plugins > Settings > Add... and enter the URL of this file.

Usage:
  python make_plugins_xml.py --metadata metadata.txt --version 0.2.0 \
      --zip-name GMD_COProcessing-0.2.0.zip --download-url https://.../GMD_COProcessing-0.2.0.zip \
      --icon-url https://.../icon.svg --out plugins.xml
"""

import argparse
import configparser
from datetime import datetime, timezone
from xml.sax.saxutils import escape


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--zip-name", required=True)
    ap.add_argument("--download-url", required=True)
    ap.add_argument("--icon-url", default="")
    ap.add_argument("--plugin-id", default="1")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str  # keep key case (qgisMinimumVersion)
    cfg.read(args.metadata, encoding="utf-8")
    m = cfg["general"]
    get = lambda key, default="": m.get(key, default).strip()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    fields = {
        "description": f"<![CDATA[{get('description')}]]>",
        "about": f"<![CDATA[{get('about')}]]>",
        "version": escape(args.version),
        "trusted": "False",
        "qgis_minimum_version": escape(get("qgisMinimumVersion", "3.0")),
        "qgis_maximum_version": escape(get("qgisMaximumVersion", "3.99")),
        "homepage": escape(get("homepage")),
        "file_name": escape(args.zip_name),
        "icon": escape(args.icon_url),
        "author_name": f"<![CDATA[{get('author')}]]>",
        "download_url": escape(args.download_url),
        "uploaded_by": f"<![CDATA[{get('author')}]]>",
        "create_date": now,
        "update_date": now,
        "experimental": escape(get("experimental", "False")),
        "deprecated": escape(get("deprecated", "False")),
        "tracker": escape(get("tracker")),
        "repository": escape(get("repository")),
        "tags": escape(get("tags")),
        "downloads": "0",
        "average_vote": "0",
        "rating_votes": "0",
        "external_dependencies": escape(get("plugin_dependencies")),
        "server": escape(get("server", "False")),
    }
    body = "\n".join(f"    <{k}>{v}</{k}>" for k, v in fields.items())
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<plugins>\n"
        f'  <pyqgis_plugin name="{escape(get("name"), {chr(34): "&quot;"})}" '
        f'version="{escape(args.version)}" plugin_id="{escape(args.plugin_id)}">\n'
        f"{body}\n"
        "  </pyqgis_plugin>\n"
        "</plugins>\n"
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(xml)
    print(xml)


if __name__ == "__main__":
    main()
