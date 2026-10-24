"""Patch the 3x-ui xray template so panel traffic exits through the gateway."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

TEMPLATE_KEY = "xrayTemplateConfig"


def backup(db_path: Path) -> Path:
    target = db_path.with_suffix(".db.bak")
    shutil.copy2(db_path, target)
    return target


def patch_database(db_path: Path, outbound: dict, rule: dict) -> dict:
    """Idempotently merge the gateway outbound and catch-all rule into the template."""
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (TEMPLATE_KEY,)).fetchone()
        if not row:
            raise KeyError(TEMPLATE_KEY)
        template = json.loads(row[0]) if row[0] and row[0].strip() else {}

        outbounds = [item for item in template.get("outbounds", []) if item.get("tag") != outbound["tag"]]
        outbounds.append(outbound)
        template["outbounds"] = outbounds

        routing = template.setdefault("routing", {})
        rules = [item for item in routing.get("rules", []) if item.get("outboundTag") != rule["outboundTag"]]
        rules.append(rule)
        routing["rules"] = rules
        routing.setdefault("domainStrategy", "IPIfNonMatch")

        payload = json.dumps(template, indent=2, ensure_ascii=False)
        connection.execute("UPDATE settings SET value = ? WHERE key = ?", (payload, TEMPLATE_KEY))
        connection.commit()
    finally:
        connection.close()
    return {"outbounds": [item.get("tag") for item in outbounds], "rules": len(rules)}
