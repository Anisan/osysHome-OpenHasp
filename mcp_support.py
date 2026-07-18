"""MCP integration helpers for OpenHasp plugin."""

from __future__ import annotations

import json
import base64
import os
import time
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from sqlalchemy import delete, or_

from app.core.lib.mcp_contract import (
    build_plugin_mcp_descriptors,
    revision_from_dict,
    validate_entity_payload,
)
from app.database import row2dict, session_scope

from plugins.OpenHasp.models.Device import HaspDevice

PANELS = "panels"
PLUGIN_NAME = "OpenHasp"

_PLUGIN_NOTES = [
    "Each panel is stored as a DB row with human-readable panel_config JSON.",
    "panel_config describes openHASP UI: pages[], optional templates{}, value_event, %Object.property% bindings.",
    "Bindings use osysHome objects/properties; analyze_panel_config validates that they exist.",
    "Use compile_page_jsonl to preview jsonl payload for a specific page before pushing to device.",
    "request_screenshot publishes MQTT command; get_screenshot fetches HTTP screenshot and stores file under files/openhasp_screenshots/.",
]


def _plugin_instance():
    try:
        from app.core.main.PluginsHelper import plugins
        return plugins.get(PLUGIN_NAME, {}).get("instance")
    except Exception:
        return None


def panel_config_spec_text() -> str:
    doc_path = Path(__file__).resolve().parent / "docs" / "PANEL_CONFIGURATION.ru.md"
    if doc_path.is_file():
        return doc_path.read_text(encoding="utf-8")
    return (
        "# OpenHasp panel_config\n\n"
        "Store UI layout in `panel_config` JSON: `pages[]`, optional `templates{}`, "
        "bindings via `%Object.property%`, events `<event>_linkedMethod`, `<event>_linkedTemplate`.\n"
        "See plugins/OpenHasp/docs/PANEL_CONFIGURATION.ru.md for full specification."
    )


def mcp_capabilities() -> dict:
    return {
        "mcp_version": 1,
        "entities": True,
        "config_schema": True,
        "notes": list(_PLUGIN_NOTES),
        "collections": [
            {
                "id": PANELS,
                "title": "OpenHasp Panels",
                "binding_mode": "none",
                "writable": True,
                "has_code": False,
                "list_filters": ["query"],
                "default_sort": "title asc, id asc",
                "writable_fields": ["title", "mqtt_path", "panel_config"],
                "description": (
                    "OpenHasp panels backed by MQTT (mqtt_path) and panel_config JSON "
                    "with pages, templates and %Object.property% bindings."
                ),
            },
        ],
        "operations": [
            "reload_pages",
            "reload_panel",
            "mqtt_publish",
            "set_page",
            "get_page",
            "idle",
            "request_screenshot",
            "get_screenshot",
            "analyze_panel_config",
            "compile_page_jsonl",
            "patch_panel_config",
        ],
        "operation_schemas": {
            "reload_pages": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Reload one panel; omit to reload all"},
                    },
                },
            },
            "reload_panel": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer"},
                    },
                    "required": ["panel_id"],
                },
            },
            "mqtt_publish": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                        "command": {
                            "type": "string",
                            "description": (
                                "Publish to <mqtt_path>/command with this payload. "
                                "Example: 'p1b2.val=25' or 'page next'."
                            ),
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "Publish to <mqtt_path>/command/<key> with payload 'value'. "
                                "Example: key='p1b2.val', value=25."
                            ),
                        },
                        "value": {
                            "description": "Payload for command/<key>. If omitted/null, publishes empty payload (read state)."
                        },
                        "relative_topic": {
                            "type": "string",
                            "description": (
                                "Advanced: relative topic under the panel root, must start with 'command'. "
                                "Example: 'command/p1b2.val' or 'command'."
                            ),
                        },
                        "payload": {
                            "description": "Advanced: payload for relative_topic. If omitted/null, publishes empty payload."
                        },
                        "qos": {"type": "integer", "minimum": 0, "maximum": 2, "default": 0},
                        "retain": {"type": "boolean", "default": False},
                    },
                    "required": ["panel_id"],
                },
            },
            "set_page": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                        "page": {"type": "integer", "description": "Page index to open on panel"},
                    },
                    "required": ["panel_id", "page"],
                },
            },
            "get_page": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                    },
                    "required": ["panel_id"],
                },
            },
            "idle": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                        "state": {
                            "type": "string",
                            "enum": ["off", "short", "long"],
                            "description": (
                                "Optional idle state to set. "
                                "If omitted, sends empty payload to query current idle state."
                            ),
                        },
                    },
                    "required": ["panel_id"],
                },
            },
            "request_screenshot": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                        "trigger": {
                            "type": "boolean",
                            "default": True,
                            "description": "Publish MQTT screenshot command before returning URLs.",
                        },
                        "command_key": {
                            "type": "string",
                            "default": "screenshot",
                            "description": "Keyword for <mqtt_path>/command/<command_key> topic.",
                        },
                        "command_payload": {
                            "description": "Optional payload for screenshot command topic.",
                        },
                    },
                    "required": ["panel_id"],
                },
            },
            "get_screenshot": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                        "page": {
                            "type": "integer",
                            "description": "Optional: switch panel to this page before screenshot",
                        },
                        "sleep_sec": {
                            "type": "number",
                            "default": 0.5,
                            "description": "Delay after page switch before fetching screenshot",
                        },
                        "quality": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 80,
                            "description": "Optional q=... parameter (depends on openHASP HTTP endpoint)",
                        },
                        "return_base64": {
                            "type": "boolean",
                            "default": True,
                            "description": "If true, return image_base64 in response (may be large)",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "default": 2500000,
                            "description": "Max allowed screenshot bytes for base64 return; larger -> file only",
                        },
                    },
                    "required": ["panel_id"],
                },
            },
            "analyze_panel_config": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Load panel_config from DB by panel id"},
                        "panel_config": {
                            "type": "object",
                            "description": "panel_config JSON object (or JSON string). Used if panel_id is not provided.",
                        },
                        "validate_bindings": {
                            "type": "boolean",
                            "default": True,
                            "description": "Validate that referenced %Object.property% objects/properties exist in osysHome.",
                        },
                        "validate_templates": {
                            "type": "boolean",
                            "default": True,
                            "description": "Validate templates references and required fields.",
                        },
                        "validate_structure": {
                            "type": "boolean",
                            "default": True,
                            "description": "Validate pages/objects structure for runtime compatibility.",
                        },
                    },
                },
            },
            "compile_page_jsonl": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Panel id for loading panel_config and panel_current_page."},
                        "panel_config": {
                            "type": "object",
                            "description": "panel_config JSON object (or JSON string). Used if panel_id is not provided.",
                        },
                        "page_index": {"type": "integer", "minimum": 0, "description": "Index in panel_config.pages[]"},
                        "resolve_placeholders": {
                            "type": "boolean",
                            "default": True,
                            "description": "Resolve %Object.property% values using current osysHome values.",
                        },
                        "include_page_atr": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include the first 'page attributes' jsonl line (page/comment/back/next/prev).",
                        },
                    },
                    "required": ["page_index"],
                },
            },
            "patch_panel_config": {
                "params": {
                    "type": "object",
                    "properties": {
                        "panel_id": {"type": "integer", "description": "Target panel id"},
                        "set": {
                            "type": "object",
                            "description": "Root-level keys to set/replace in panel_config",
                        },
                        "unset": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Root-level keys to remove from panel_config",
                        },
                        "if_match": {
                            "type": "string",
                            "description": "Optional revision lock from mcp_entity_revision",
                        },
                        "reload": {
                            "type": "boolean",
                            "default": True,
                            "description": "Reload panel pages after patch",
                        },
                    },
                    "required": ["panel_id"],
                },
            },
        },
    }


def mcp_config_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "MQTT broker host"},
            "port": {"type": "integer", "default": 1883},
            "protocol": {"type": "string", "enum": ["3.1", "3.1.1", "5.0"], "default": "3.1.1"},
            "topic": {"type": "string", "description": "Comma-separated MQTT subscribe topics"},
            "login": {"type": "string"},
            "password": {"type": "string", "writeOnly": True},
        },
        "additionalProperties": False,
    }


def _collection_meta(collection: str) -> dict:
    for item in mcp_capabilities()["collections"]:
        if item["id"] == collection:
            return item
    raise ValueError(f"Unsupported collection: {collection}")


def _parse_panel_config(value):
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError("panel_config must be a JSON object or string")


def _is_scalar_json_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _json_inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _format_panel_config_json(value: Any, indent: int = 0, indent_step: int = 2, inline_limit: int = 400) -> str:
    if _is_scalar_json_value(value):
        return _json_inline(value)

    if isinstance(value, list):
        if not value:
            return "[]"
        if all(_is_scalar_json_value(item) for item in value):
            inline = _json_inline(value)
            if len(inline) <= inline_limit:
                return inline
        inner_indent = " " * (indent + indent_step)
        close_indent = " " * indent
        lines = [inner_indent + _format_panel_config_json(item, indent + indent_step, indent_step, inline_limit) for item in value]
        return "[\n" + ",\n".join(lines) + "\n" + close_indent + "]"

    if isinstance(value, dict):
        if not value:
            return "{}"
        items = list(value.items())
        if all(_is_scalar_json_value(v) for _, v in items):
            inline = _json_inline(value)
            if len(inline) <= inline_limit:
                return inline
        inner_indent = " " * (indent + indent_step)
        close_indent = " " * indent
        lines = []
        for key, val in items:
            rendered = _format_panel_config_json(val, indent + indent_step, indent_step, inline_limit)
            lines.append(f"{inner_indent}{json.dumps(str(key), ensure_ascii=False)}: {rendered}")
        return "{\n" + ",\n".join(lines) + "\n" + close_indent + "}"

    return _json_inline(str(value))


def _panel_to_dict(row: HaspDevice) -> dict:
    data = row2dict(row)
    if row.panel_config:
        try:
            data["panel_config"] = json.loads(row.panel_config)
        except (TypeError, ValueError):
            data["panel_config"] = row.panel_config
    return data


def mcp_entity_schema(collection: str) -> dict:
    _collection_meta(collection)
    if collection == PANELS:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "mqtt_path": {"type": "string", "description": "MQTT root path (node_t without trailing slash)"},
                "panel_config": {
                    "type": "object",
                    "description": (
                        "openHASP UI JSON: pages[], templates{}, value_event, "
                        "%Object.property% bindings — read osys://plugin/OpenHasp/panel_config/spec"
                    ),
                },
                "current_page": {"type": "integer", "readOnly": True},
                "online": {"type": "boolean", "readOnly": True},
                "ip": {"type": "string", "readOnly": True},
            },
            "required": ["title"],
        }
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_list_entities(collection: str, query: str = None, limit: int = 100) -> List[dict]:
    limit = max(1, min(int(limit or 100), 5000))
    if collection == PANELS:
        with session_scope() as session:
            q = session.query(HaspDevice)
            if query:
                like = f"%{query}%"
                q = q.filter(or_(HaspDevice.title.ilike(like), HaspDevice.mqtt_path.ilike(like), HaspDevice.panel_config.ilike(like)))
            rows = q.order_by(HaspDevice.title, HaspDevice.id).limit(limit).all()
            return [_panel_to_dict(row) for row in rows]
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_get_entity(collection: str, entity_id) -> dict:
    with session_scope() as session:
        if collection == PANELS:
            row = session.query(HaspDevice).filter(HaspDevice.id == int(entity_id)).one_or_none()
            if row is None:
                raise ValueError(f"Panel not found: {entity_id}")
            return _panel_to_dict(row)
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_upsert_entity(collection: str, payload: dict, entity_id=None) -> dict:
    meta = _collection_meta(collection)
    if not meta.get("writable"):
        raise ValueError(f"Collection '{collection}' is read-only")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    if collection == PANELS:
        instance = _plugin_instance()
        with session_scope() as session:
            if entity_id not in (None, ""):
                row = session.query(HaspDevice).filter(HaspDevice.id == int(entity_id)).one_or_none()
                if row is None:
                    raise ValueError(f"Panel not found: {entity_id}")
            else:
                row = HaspDevice()
                session.add(row)
            if "title" in payload:
                row.title = payload.get("title")
            if "mqtt_path" in payload:
                row.mqtt_path = payload.get("mqtt_path")
            if "panel_config" in payload:
                config = _parse_panel_config(payload.get("panel_config"))
                # Store in DB as human-readable JSON (diff-friendly, readable in admin/SQL).
                row.panel_config = _format_panel_config_json(config)
            session.commit()
            session.refresh(row)
            if instance is not None and row.panel_config:
                instance.reload_pages(row)
            return _panel_to_dict(row)

    raise ValueError(f"Unsupported collection: {collection}")


def mcp_delete_entity(collection: str, entity_id) -> bool:
    meta = _collection_meta(collection)
    if not meta.get("writable"):
        raise ValueError(f"Collection '{collection}' is read-only")
    if collection == PANELS:
        with session_scope() as session:
            session.execute(delete(HaspDevice).where(HaspDevice.id == int(entity_id)))
            session.commit()
            return True
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_validate_entity_code(collection: str, code: str) -> dict:
    raise ValueError(f"Collection '{collection}' does not support code validation")


def mcp_run_entity_dry(collection: str, code: str, context: dict = None) -> dict:
    raise ValueError(f"Collection '{collection}' does not support dry-run code")


def mcp_invoke(operation: str, params: dict = None) -> dict:
    params = params or {}
    instance = _plugin_instance()
    if instance is None:
        raise ValueError("OpenHasp plugin not loaded")
    if operation == "reload_pages":
        panel_id = params.get("panel_id")
        if panel_id not in (None, ""):
            with session_scope() as session:
                panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
                if panel is None:
                    raise ValueError(f"Panel not found: {panel_id}")
                instance.reload_pages(panel)
        else:
            instance.reload_panels()
        return {"ok": True, "operation": operation}
    if operation == "reload_panel":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")
            instance.reload_pages(panel)
        return {"ok": True, "operation": operation, "panel_id": int(panel_id)}
    if operation == "mqtt_publish":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        qos = int(params.get("qos", 0) or 0)
        if qos not in (0, 1, 2):
            raise ValueError("qos must be 0, 1, or 2")
        retain = bool(params.get("retain", False))

        def _to_payload(val) -> str:
            if val in (None, ""):
                return ""
            if isinstance(val, (dict, list)):
                return json.dumps(val, ensure_ascii=False)
            return str(val)

        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")
            mqtt_root = str(panel.mqtt_path or "").strip().rstrip("/")
            if not mqtt_root:
                raise ValueError(f"Panel {panel_id} has empty mqtt_path")

            # 1) Simple command mode: publish to <root>/command
            if params.get("command") not in (None, ""):
                cmd = str(params.get("command") or "")
                instance.send_mqtt_command(f"{mqtt_root}/command", cmd, qos=qos, retain=retain)
                return {"ok": True, "operation": operation, "panel_id": int(panel_id), "topic": f"{mqtt_root}/command"}

            # 2) Key/value mode: publish to <root>/command/<key>
            if params.get("key") not in (None, ""):
                key = str(params.get("key") or "").strip().lstrip("/")
                if not key:
                    raise ValueError("key must be a non-empty string")
                payload = _to_payload(params.get("value"))
                topic = f"{mqtt_root}/command/{key}"
                instance.send_mqtt_command(topic, payload, qos=qos, retain=retain)
                return {"ok": True, "operation": operation, "panel_id": int(panel_id), "topic": topic}

            # 3) Advanced mode: relative topic (still restricted to command*)
            if params.get("relative_topic") not in (None, ""):
                rel = str(params.get("relative_topic") or "").strip().lstrip("/")
                if not rel:
                    raise ValueError("relative_topic must be non-empty")
                if not rel.startswith("command"):
                    raise ValueError("relative_topic must start with 'command'")
                if ".." in rel:
                    raise ValueError("relative_topic contains invalid path segment")
                payload = _to_payload(params.get("payload"))
                topic = f"{mqtt_root}/{rel}"
                instance.send_mqtt_command(topic, payload, qos=qos, retain=retain)
                return {"ok": True, "operation": operation, "panel_id": int(panel_id), "topic": topic}

        raise ValueError("Provide one of: command, key(+value), or relative_topic(+payload)")
    if operation == "set_page":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        if "page" not in params:
            raise ValueError("page is required")
        page = int(params.get("page"))
        if page < 0:
            raise ValueError("page must be >= 0")
        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")
            mqtt_root = str(panel.mqtt_path or "").strip().rstrip("/")
            if not mqtt_root:
                raise ValueError(f"Panel {panel_id} has empty mqtt_path")
            instance.send_mqtt_command(f"{mqtt_root}/command/page", str(page))
            return {
                "ok": True,
                "operation": operation,
                "panel_id": int(panel_id),
                "page": page,
                "topic": f"{mqtt_root}/command/page",
            }
    if operation == "get_page":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")
            return {
                "ok": True,
                "operation": operation,
                "panel_id": int(panel_id),
                "page": panel.current_page,
                "online": bool(panel.online),
                "ip": panel.ip,
            }
    if operation == "idle":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        state = params.get("state")
        if state not in (None, "", "off", "short", "long"):
            raise ValueError("state must be one of: off, short, long")
        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")
            mqtt_root = str(panel.mqtt_path or "").strip().rstrip("/")
            if not mqtt_root:
                raise ValueError(f"Panel {panel_id} has empty mqtt_path")
            payload = "" if state in (None, "") else str(state)
            # openHASP: command/idle with off|short|long, empty payload queries current idle state.
            instance.send_mqtt_command(f"{mqtt_root}/command/idle", payload)
            return {
                "ok": True,
                "operation": operation,
                "panel_id": int(panel_id),
                "state": state or None,
                "topic": f"{mqtt_root}/command/idle",
            }
    if operation == "request_screenshot":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        trigger = bool(params.get("trigger", True))
        command_key = str(params.get("command_key") or "screenshot").strip().lstrip("/")
        if not command_key:
            raise ValueError("command_key must be non-empty")
        command_payload = params.get("command_payload")
        if isinstance(command_payload, (dict, list)):
            command_payload = json.dumps(command_payload, ensure_ascii=False)
        elif command_payload in (None, ""):
            command_payload = ""
        else:
            command_payload = str(command_payload)
        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")
            mqtt_root = str(panel.mqtt_path or "").strip().rstrip("/")
            if not mqtt_root:
                raise ValueError(f"Panel {panel_id} has empty mqtt_path")
            topic = f"{mqtt_root}/command/{command_key}"
            if trigger:
                instance.send_mqtt_command(topic, command_payload)
            ip = str(panel.ip or "").strip()
            screenshot_urls = []
            if ip:
                screenshot_urls = [
                    f"http://{ip}/screenshot",
                    f"http://{ip}/screenshot?q=80",
                ]
            return {
                "ok": True,
                "operation": operation,
                "panel_id": int(panel_id),
                "triggered": trigger,
                "mqtt_topic": topic,
                "ip": ip or None,
                "screenshot_urls": screenshot_urls,
                "note": (
                    "Use screenshot_urls when panel HTTP server is enabled. "
                    "MQTT command topic can be customized with command_key."
                ),
            }
    if operation == "get_screenshot":
        from app.configuration import Config
        from app.core.lib.common import getUrl

        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        page = params.get("page")
        sleep_sec = float(params.get("sleep_sec", 0.5) or 0.0)
        if sleep_sec < 0:
            sleep_sec = 0.0
        quality = int(params.get("quality", 80) or 80)
        quality = max(1, min(quality, 100))
        return_base64 = bool(params.get("return_base64", True))
        max_bytes = int(params.get("max_bytes", 2500000) or 2500000)
        max_bytes = max(1, max_bytes)

        with session_scope() as session:
            panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if panel is None:
                raise ValueError(f"Panel not found: {panel_id}")

            mqtt_root = str(panel.mqtt_path or "").strip().rstrip("/")
            if not mqtt_root:
                raise ValueError(f"Panel {panel_id} has empty mqtt_path")

            ip = str(panel.ip or "").strip()
            if not ip:
                raise ValueError(
                    f"Panel {panel_id} has no ip yet (need statusupdate). "
                    "Wait for panel to publish statusupdate and try again."
                )

            # Optional: switch page first (via MQTT command/page)
            if page not in (None, ""):
                page_int = int(page)
                if page_int < 0:
                    raise ValueError("page must be >= 0")
                instance.send_mqtt_command(f"{mqtt_root}/command/page", str(page_int))
                if sleep_sec:
                    time.sleep(sleep_sec)
            else:
                page_int = None

            # Fetch screenshot from panel HTTP endpoint
            ts = int(time.time() * 1000)
            url = f"http://{ip}/screenshot?q={quality}&t={ts}"
            content = getUrl(url, timeout=10)
            if not content:
                raise ValueError(f"Failed to fetch screenshot from {url}")

        # Determine mime/extension from signature (best-effort)
        mime_type = "application/octet-stream"
        ext = "bin"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type = "image/png"
            ext = "png"
        elif content.startswith(b"\xff\xd8"):
            mime_type = "image/jpeg"
            ext = "jpg"
        elif content.startswith(b"BM"):
            mime_type = "image/bmp"
            ext = "bmp"

        # Save to disk for local inspection
        out_dir = os.path.join(Config.FILES_DIR, "openhasp_screenshots")
        os.makedirs(out_dir, exist_ok=True)
        filename = f"panel_{int(panel_id)}_{ts}.{ext}"
        file_path = os.path.join(out_dir, filename)
        with open(file_path, "wb") as f:
            f.write(content)

        result = {
            "ok": True,
            "operation": operation,
            "panel_id": int(panel_id),
            "page": page_int,
            "ip": ip,
            "url": url,
            "mime_type": mime_type,
            "bytes": len(content),
            "file_path": file_path,
        }

        if return_base64 and len(content) <= max_bytes:
            result["image_base64"] = base64.b64encode(content).decode("ascii")
        elif return_base64:
            result["image_base64"] = None
            result["note"] = f"Screenshot is {len(content)} bytes, exceeds max_bytes={max_bytes}; returned file_path only"

        return result
    if operation == "analyze_panel_config":
        panel_id = params.get("panel_id")
        config_value = params.get("panel_config")
        validate_bindings = bool(params.get("validate_bindings", True))
        validate_templates = bool(params.get("validate_templates", True))
        validate_structure = bool(params.get("validate_structure", True))

        if panel_id not in (None, ""):
            with session_scope() as session:
                panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
                if panel is None:
                    raise ValueError(f"Panel not found: {panel_id}")
                config_value = panel.panel_config

        if config_value in (None, ""):
            raise ValueError("Provide panel_id or panel_config")

        config = _parse_panel_config(config_value)
        return _analyze_panel_config(
            config,
            validate_bindings=validate_bindings,
            validate_templates=validate_templates,
            validate_structure=validate_structure,
        )

    if operation == "compile_page_jsonl":
        instance = _plugin_instance()
        if instance is None:
            raise ValueError("OpenHasp plugin not loaded")

        panel_id = params.get("panel_id")
        config_value = params.get("panel_config")
        page_index = params.get("page_index")
        resolve_placeholders = bool(params.get("resolve_placeholders", True))
        include_page_atr = bool(params.get("include_page_atr", True))

        if page_index in (None, ""):
            raise ValueError("page_index is required")
        page_index = int(page_index)
        if page_index < 0:
            raise ValueError("page_index must be >= 0")

        panel_current_page = page_index
        if panel_id not in (None, ""):
            with session_scope() as session:
                panel = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
                if panel is None:
                    raise ValueError(f"Panel not found: {panel_id}")
                config_value = panel.panel_config
                if panel.current_page is not None:
                    panel_current_page = int(panel.current_page)

        if config_value in (None, ""):
            raise ValueError("Provide panel_id or panel_config")

        config = _parse_panel_config(config_value)
        return _compile_page_jsonl(
            instance=instance,
            config=config,
            page_index=page_index,
            panel_current_page=panel_current_page,
            resolve_placeholders=resolve_placeholders,
            include_page_atr=include_page_atr,
        )
    if operation == "patch_panel_config":
        panel_id = params.get("panel_id")
        if panel_id in (None, ""):
            raise ValueError("panel_id is required")
        set_patch = params.get("set") or {}
        unset_patch = params.get("unset") or []
        if_match = str(params.get("if_match") or "").strip() or None
        reload_after = bool(params.get("reload", True))

        if set_patch and not isinstance(set_patch, dict):
            raise ValueError("set must be an object")
        if unset_patch and not isinstance(unset_patch, list):
            raise ValueError("unset must be an array")

        instance = _plugin_instance()
        with session_scope() as session:
            row = session.query(HaspDevice).filter(HaspDevice.id == int(panel_id)).one_or_none()
            if row is None:
                raise ValueError(f"Panel not found: {panel_id}")

            config = _parse_panel_config(row.panel_config)
            if not isinstance(config, dict):
                raise ValueError("panel_config must be an object")

            current_entity = _panel_to_dict(row)
            current_revision = revision_from_dict(
                current_entity, keys=["id", "title", "mqtt_path", "panel_config"]
            )
            if if_match and if_match != current_revision:
                raise ValueError(f"Revision mismatch: if_match={if_match}, current={current_revision}")

            for key, value in set_patch.items():
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                config[key_text] = value

            for key in unset_patch:
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                config.pop(key_text, None)

            validation_errors = _validate_panel_config_structure(config)
            if validation_errors:
                return {"ok": False, "errors": validation_errors}

            row.panel_config = _format_panel_config_json(config)
            session.commit()
            session.refresh(row)

            if reload_after and instance is not None:
                instance.reload_pages(row)

            updated = _panel_to_dict(row)
            return {
                "ok": True,
                "operation": operation,
                "panel_id": int(panel_id),
                "reload": reload_after,
                "revision": revision_from_dict(
                    updated, keys=["id", "title", "mqtt_path", "panel_config"]
                ),
                "entity": updated,
            }
    raise ValueError(f"Unsupported operation: {operation}")


PANEL_CONFIG_SPEC_URI = f"osys://plugin/{PLUGIN_NAME}/panel_config/spec"
PANEL_CONFIG_PROMPT = "osys_openhasp_panel_config"


def mcp_read_resource(path: str) -> Tuple[str, str]:
    rel = str(path or "").strip().lstrip("/")
    if rel == "panel_config/spec":
        return panel_config_spec_text(), "text/markdown"
    raise ValueError(f"Unsupported resource path: {path}")


def mcp_get_prompt(name: str, arguments: dict = None) -> dict:
    arguments = arguments or {}
    if name != PANEL_CONFIG_PROMPT:
        raise ValueError(f"Unsupported prompt: {name}")
    task = str(arguments.get("task") or "").strip()
    if not task:
        raise ValueError("task is required")
    panel_id = str(arguments.get("panel_id") or "").strip()
    object_name = str(arguments.get("object_name") or "").strip()
    spec_excerpt = panel_config_spec_text()
    if len(spec_excerpt) > 12000:
        spec_excerpt = (
            spec_excerpt[:12000]
            + f"\n\n...(truncated, read full spec via {PANEL_CONFIG_SPEC_URI})"
        )
    prompt_text = (
        "Author or update OpenHasp panel_config JSON for osysHome.\n"
        f"Task: {task}\n"
        f"Panel id: {panel_id or '-'}\n"
        f"Primary object: {object_name or '-'}\n\n"
        "Workflow:\n"
        f"1. Read resource {PANEL_CONFIG_SPEC_URI}\n"
        f"2. Read osys://plugin/{PLUGIN_NAME}/schema/panels\n"
        "3. Build JSON with pages[] and optional templates{}\n"
        "4. Use %Object.property% for values and <event>_linkedMethod for actions\n"
        "5. osys_plugin_validate_entity then osys_plugin_upsert_entity on collection panels\n"
        "6. osys_plugin_invoke OpenHasp reload_pages to push config to device\n\n"
        "Specification excerpt:\n"
        f"{spec_excerpt}"
    )
    return {"messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}]}


def mcp_descriptors() -> Tuple[list, list, list]:
    tools, resources, prompts = build_plugin_mcp_descriptors(PLUGIN_NAME, mcp_capabilities())
    resources.append(
        {
            "uri": PANEL_CONFIG_SPEC_URI,
            "kind": "panel_config_spec",
            "plugin": PLUGIN_NAME,
            "mimeType": "text/markdown",
        }
    )
    prompts.append(
        {
            "name": PANEL_CONFIG_PROMPT,
            "plugin": PLUGIN_NAME,
            "description": "Guide for authoring OpenHasp panel_config JSON (pages, templates, bindings)",
            "arguments": [
                {"name": "task", "required": True},
                {"name": "panel_id", "required": False},
                {"name": "object_name", "required": False},
            ],
        }
    )
    return tools, resources, prompts


def mcp_entity_revision(collection: str, entity_id) -> str:
    entity = mcp_get_entity(collection, entity_id)
    if collection == PANELS:
        return revision_from_dict(entity, keys=["id", "title", "mqtt_path", "panel_config"])
    raise ValueError(f"Unsupported collection: {collection}")


def _validate_panel_config_structure(config) -> List[dict]:
    errors: List[dict] = []
    if not isinstance(config, dict):
        return [{"field": "panel_config", "message": "must be an object"}]
    pages = config.get("pages")
    if pages is not None and not isinstance(pages, list):
        errors.append({"field": "panel_config.pages", "message": "must be an array"})
    elif isinstance(pages, list):
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append({"field": f"panel_config.pages[{index}]", "message": "must be an object"})
            elif "objects" in page and not isinstance(page["objects"], list):
                errors.append({"field": f"panel_config.pages[{index}].objects", "message": "must be an array"})
    templates = config.get("templates")
    if templates is not None and not isinstance(templates, dict):
        errors.append({"field": "panel_config.templates", "message": "must be an object"})
    return errors


_PANEL_BINDING_PATTERN = re.compile(r"%([^%\'\"]+)\.([^%\'\"]+)%")


def _extract_bindings_from_config(config) -> List[dict]:
    bindings: List[dict] = []

    def walk(node, path: str):
        if isinstance(node, str):
            for match in _PANEL_BINDING_PATTERN.finditer(node):
                bindings.append(
                    {
                        "object": match.group(1),
                        "property": match.group(2),
                        "matched": match.group(0),
                        "path": path,
                    }
                )
            return
        if isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, f"{path}[{idx}]" if path else f"[{idx}]")
            return
        if isinstance(node, dict):
            for key, value in node.items():
                new_path = f"{path}.{key}" if path else str(key)
                walk(value, new_path)

    walk(config, "")
    return bindings


def _validate_panel_config_templates(config, templates_errors: List[dict]) -> None:
    templates = config.get("templates") or {}
    if not isinstance(templates, dict):
        templates_errors.append({"field": "panel_config.templates", "message": "must be an object"})
        return

    pages = config.get("pages") or []
    if not isinstance(pages, list):
        return

    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        objects = page.get("objects") or []
        if not isinstance(objects, list):
            continue
        for obj_index, obj in enumerate(objects):
            if not isinstance(obj, dict):
                continue
            if obj.get("obj") != "template":
                continue

            parent_id = obj.get("id")
            linked_object = obj.get("linkedObject")
            template_name = obj.get("template")
            field_prefix = f"panel_config.pages[{page_index}].objects[{obj_index}]"

            if parent_id in (None, ""):
                templates_errors.append({"field": f"{field_prefix}.id", "message": "template parent id is required"})
            if not linked_object:
                templates_errors.append(
                    {"field": f"{field_prefix}.linkedObject", "message": "linkedObject is required for template"}
                )
            if not template_name:
                templates_errors.append({"field": f"{field_prefix}.template", "message": "template is required"})
                continue

            if template_name not in templates:
                templates_errors.append(
                    {
                        "field": f"{field_prefix}.template",
                        "message": f"template '{template_name}' not found in panel_config.templates",
                    }
                )
                continue

            tpl = templates.get(template_name)
            if not isinstance(tpl, list) or not tpl:
                templates_errors.append(
                    {
                        "field": f"panel_config.templates.{template_name}",
                        "message": "template definition must be a non-empty array",
                    }
                )
                continue

            # Minimal checks for elements inside template array
            for item_index, item in enumerate(tpl):
                if not isinstance(item, dict):
                    templates_errors.append(
                        {
                            "field": f"panel_config.templates.{template_name}[{item_index}]",
                            "message": "template items must be objects",
                        }
                    )
                    continue
                if item.get("id") in (None, ""):
                    templates_errors.append(
                        {
                            "field": f"panel_config.templates.{template_name}[{item_index}].id",
                            "message": "template item id is required",
                        }
                    )
                if item.get("obj") in (None, ""):
                    templates_errors.append(
                        {
                            "field": f"panel_config.templates.{template_name}[{item_index}].obj",
                            "message": "template item obj is required",
                        }
                    )


def _analyze_panel_config(
    config,
    *,
    validate_bindings: bool = True,
    validate_templates: bool = True,
    validate_structure: bool = True,
) -> dict:
    errors: List[dict] = []
    warnings: List[dict] = []

    if validate_structure:
        errors.extend(_validate_panel_config_structure(config))

    if validate_structure:
        pages = config.get("pages")
        if isinstance(pages, list):
            for page_index, page in enumerate(pages):
                if not isinstance(page, dict):
                    continue
                objects = page.get("objects")
                if objects is None:
                    errors.append(
                        {"field": f"panel_config.pages[{page_index}].objects", "message": "page objects are required"}
                    )
                    continue
                if not isinstance(objects, list):
                    continue
                for obj_index, obj in enumerate(objects):
                    if not isinstance(obj, dict):
                        errors.append(
                            {
                                "field": f"panel_config.pages[{page_index}].objects[{obj_index}]",
                                "message": "object must be an object",
                            }
                        )
                        continue
                    if obj.get("id") in (None, ""):
                        errors.append(
                            {"field": f"panel_config.pages[{page_index}].objects[{obj_index}].id", "message": "id is required"}
                        )
                    if obj.get("obj") in (None, ""):
                        errors.append(
                            {"field": f"panel_config.pages[{page_index}].objects[{obj_index}].obj", "message": "obj is required"}
                        )

    if validate_templates:
        _validate_panel_config_templates(config, errors)

    extracted = _extract_bindings_from_config(config)
    # Deduplicate bindings for existence checks
    uniq = {(b["object"], b["property"]) for b in extracted}
    bindings_list = sorted(list(uniq), key=lambda x: (x[0], x[1]))

    if validate_bindings:
        from app.core.lib.object import getObject

        for obj_name, prop_name in bindings_list:
            if not obj_name or not prop_name:
                continue
            obj = getObject(obj_name)
            if obj is None:
                errors.append({"field": f"%{obj_name}.{prop_name}%", "message": f"osysHome object '{obj_name}' not found"})
                continue
            properties = getattr(obj, "properties", {}) or {}
            if prop_name not in properties:
                errors.append(
                    {
                        "field": f"%{obj_name}.{prop_name}%",
                        "message": f"property '{obj_name}.{prop_name}' not found",
                    }
                )

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "bindings": bindings_list}


def _compile_template_objects(
    *,
    instance,
    config: dict,
    parent: dict,
    template_name: str,
    panel_current_page: int,
    resolve_placeholders: bool,
) -> List[dict]:
    from app.core.lib.object import getObject

    templates = config.get("templates") or {}
    template_def = templates.get(template_name)
    if not isinstance(template_def, list):
        raise ValueError(f"Template '{template_name}' not found or not an array")

    linked_object = parent.get("linkedObject")
    if not linked_object:
        raise ValueError("linkedObject is required for template parent object")

    parent_id = parent.get("id")
    if parent_id in (None, ""):
        raise ValueError("id is required for template parent object")
    parent_id_int = int(parent_id)

    # Deep copy template array so we can mutate ids safely
    template_items = json.loads(json.dumps(template_def, ensure_ascii=False))

    # Apply parent fields into the first template item (OpenHasp behaviour)
    if not template_items:
        return []
    instance.merge_objects(template_items[0], parent)

    compiled: List[dict] = []
    for index, obj in enumerate(template_items):
        if not isinstance(obj, dict):
            continue

        if "tag" not in obj:
            obj["tag"] = {
                "object": linked_object,
                "template": template_name,
                "id": obj.get("id"),
                "parent": parent_id_int,
            }

        if obj.get("id") in (None, ""):
            raise ValueError(f"Template '{template_name}' contains item without id")
        obj["id"] = parent_id_int + int(obj["id"])

        if index > 0 and "parentid" in obj and obj["parentid"] not in (None, ""):
            obj["parentid"] = parent_id_int + int(obj["parentid"])

        # Resolve template string placeholders
        for key, val in list(obj.items()):
            if not isinstance(val, str):
                continue
            if val == "%.description%":
                o = getObject(linked_object)
                obj[key] = getattr(o, "description", None) if o else None
                obj[key] = obj[key] if obj[key] not in (None, "") else "nil"
            elif val == "%.name%":
                obj[key] = linked_object
            else:
                # Replace %.property% with %<linkedObject>.property%
                op = instance.replace_object(linked_object, val)
                if resolve_placeholders:
                    obj[key] = instance.process_value(op, "", "")
                else:
                    obj[key] = op

        instance.clean_object(obj)
        compiled.append(obj)

    return compiled


def _compile_page_jsonl(
    *,
    instance,
    config: dict,
    page_index: int,
    panel_current_page: int,
    resolve_placeholders: bool,
    include_page_atr: bool,
) -> dict:
    # Validate basic structure first (so we can fail early on non-compilable configs).
    # Bindings validation is intentionally skipped here because compilation can still be
    # helpful for debugging even if some properties are missing.
    pre_diag = _analyze_panel_config(
        config,
        validate_bindings=False,
        validate_templates=True,
        validate_structure=True,
    )
    if pre_diag.get("errors"):
        return {"ok": False, "errors": pre_diag["errors"], "warnings": pre_diag.get("warnings") or [], "jsonl_lines": []}

    # Binding diagnostics (only relevant when we resolve placeholders)
    diagnostics = _analyze_panel_config(
        config,
        validate_bindings=resolve_placeholders,
        validate_templates=True,
        validate_structure=True,
    )

    config_copy = json.loads(json.dumps(config, ensure_ascii=False))
    pages = config_copy.get("pages") or []
    if page_index < 0 or page_index >= len(pages):
        return {"ok": False, "errors": [{"field": "page_index", "message": "page_index is out of range"}], "warnings": [], "jsonl_lines": []}

    page = pages[page_index]
    page_objects = page.get("objects") or []

    jsonl_lines: List[str] = []
    compiled_objects: List[dict] = []

    if include_page_atr:
        page_atr = {"page": page_index}
        if "comment" in page:
            page_atr["comment"] = page["comment"]
        if "back" in page:
            page_atr["back"] = page["back"]
        if "next" in page:
            page_atr["next"] = page["next"]
        if "prev" in page:
            page_atr["prev"] = page["prev"]
        jsonl_lines.append("jsonl " + json.dumps(page_atr, ensure_ascii=False))

    for obj in page_objects:
        if not isinstance(obj, dict):
            continue
        if obj.get("obj") == "template" and "template" in obj:
            template_name = obj.get("template")
            compiled = _compile_template_objects(
                instance=instance,
                config=config_copy,
                parent=obj,
                template_name=template_name,
                panel_current_page=panel_current_page,
                resolve_placeholders=resolve_placeholders,
            )
            for cobj in compiled:
                compiled_objects.append(cobj)
                jsonl_lines.append("jsonl " + json.dumps(cobj, ensure_ascii=False))
        elif obj.get("obj") != "template":
            obj_copy = json.loads(json.dumps(obj, ensure_ascii=False))
            if resolve_placeholders:
                for key, val in list(obj_copy.items()):
                    if isinstance(val, str):
                        obj_copy[key] = instance.process_value(val, "", "")
            instance.clean_object(obj_copy)
            compiled_objects.append(obj_copy)
            jsonl_lines.append("jsonl " + json.dumps(obj_copy, ensure_ascii=False))

    return {
        "ok": bool(diagnostics.get("ok")),
        "errors": diagnostics.get("errors") or [],
        "warnings": diagnostics.get("warnings") or [],
        "page_index": page_index,
        "panel_current_page": panel_current_page,
        "jsonl_lines": jsonl_lines,
        "objects": compiled_objects,
    }




def mcp_validate_entity(collection: str, payload: dict, entity_id=None) -> dict:
    schema = mcp_entity_schema(collection)
    result = validate_entity_payload(payload, schema)
    if not result.get("ok"):
        return result
    if collection == PANELS and "panel_config" in payload:
        try:
            config = _parse_panel_config(payload.get("panel_config"))
        except (TypeError, ValueError) as exc:
            return {"ok": False, "errors": [{"field": "panel_config", "message": str(exc)}]}
        errors = _validate_panel_config_structure(config)
        if errors:
            return {"ok": False, "errors": errors}
    return {"ok": True, "errors": []}
