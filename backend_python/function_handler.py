from __future__ import annotations

import json
from typing import Any, Dict, List


class FunctionHandler:
    def __init__(self, config: Dict[str, Any], com_object: Any):
        self.config = config
        self.com_object = com_object
        self.frontend_functions: List[Dict[str, Any]] = []
        self._frontend_names: set[str] = set()
        self._notification_names: set[str] = set()
        self._function_index: Dict[str, Dict[str, Any]] = {}
        self.all_functions: List[Dict[str, Any]] = [
            {
                "name": "checkConection",
                "description": "check if the connection to external device is established",
                "commType": "read",
                "target": "device",
                "deviceCommand": "checkConection",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "integer", "description": "no parameters are needed"}
                    },
                },
            },
            {
                "name": "connect",
                "description": "connect to external device",
                "commType": "read",
                "target": "device",
                "deviceCommand": "connect",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "boolean", "description": "mandatory property"}
                    },
                },
            },
        ]

        for fn in self.all_functions:
            self._function_index[fn["name"]] = fn

        funcs = config.get("functions", {})
        self._format_and_add_functions(funcs.get("tools", {}), self.all_functions, default_target="device")

    def _iter_function_items(self, source: Any) -> List[tuple[str, Dict[str, Any]]]:
        items: List[tuple[str, Dict[str, Any]]] = []
        if isinstance(source, dict):
            for name, item in source.items():
                if isinstance(item, dict):
                    items.append((str(name), item))
        elif isinstance(source, list):
            for entry in source:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                if name:
                    items.append((name, entry))
        return items

    def _format_and_add_functions(self, source: Any, target: List[Dict[str, Any]], default_target: str) -> None:
        for name, item in self._iter_function_items(source):
            fn = {
                "name": name,
                "description": item.get("description", ""),
                "commType": item.get("commType", "read"),
                "target": item.get("target", default_target),
                "deviceCommand": item.get("deviceCommand", name),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": item.get("dataType", "string"),
                            "description": item.get("description", ""),
                        }
                    },
                },
            }

            self._function_index[name] = fn
            target[:] = [existing for existing in target if existing.get("name") != name]
            target.append(fn)

            if fn["target"] == "frontEnd":
                self._frontend_names.add(name)
                self.frontend_functions[:] = [existing for existing in self.frontend_functions if existing.get("name") != name]
                self.frontend_functions.append(fn)

            if fn["target"] == "notification":
                self._notification_names.add(name)

    def get_all_functions(self) -> List[Dict[str, Any]]:
        return self.all_functions

    def handle_call(self, function_name: str, function_arguments: Dict[str, Any]) -> Dict[str, Any]:
        if function_name in self._frontend_names:
            return {"role": "function", "message": function_name, "arguments": function_arguments}

        if function_name in self._notification_names:
            fn = self._function_index.get(function_name, {})
            return {
                "role": "notification",
                "message": f"notification received: {function_name}",
                "description": str(fn.get("description", "")),
            }

        func_def = self._function_index.get(function_name)
        if func_def is None:
            return {"role": "error", "message": f"Error: function does not exist: {function_name}"}

        comm_type = func_def.get("commType", "read")
        device_command = str(func_def.get("deviceCommand", function_name))
        payload = {
            "name": device_command,
            "value": function_arguments.get("value", ""),
            "dataType": func_def.get("parameters", {}).get("type", "string"),
        }

        if hasattr(self.com_object, function_name):
            result = getattr(self.com_object, function_name)()
        elif hasattr(self.com_object, device_command):
            result = getattr(self.com_object, device_command)()
        elif comm_type in ("write", "readWrite"):
            result = self.com_object.write(payload)
        elif comm_type == "writeRaw":
            result = self.com_object.write(str(function_arguments.get("value", "")))
        else:
            result = self.com_object.read(payload)

        if result.get("description") == "Error":
            return {"role": "error", "message": f"function_call with error: {result.get('value')}"}

        formatted = json.dumps({result.get("description", "response"): result.get("value")})
        return {"role": "functionReturnValue", "message": f"function_call complete: {function_name}", "value": formatted}
