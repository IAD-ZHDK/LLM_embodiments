from __future__ import annotations

import json
from typing import Any, Dict, List


class FunctionHandler:
    def __init__(self, config: Dict[str, Any], com_object: Any):
        self.config = config
        self.com_object = com_object
        self.frontend_functions: List[Dict[str, Any]] = []
        self.all_functions: List[Dict[str, Any]] = [
            {
                "name": "checkConection",
                "description": "check if the connection to external device is established",
                "commType": "read",
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
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "boolean", "description": "mandatory property"}
                    },
                },
            },
        ]

        funcs = config.get("functions", {})
        self._format_and_add_functions(funcs.get("actions", {}), self.all_functions)
        self._format_and_add_functions(funcs.get("notifications", {}), self.all_functions)
        self._format_and_add_functions(funcs.get("frontEnd", {}), self.all_functions)
        self._format_and_add_functions(funcs.get("frontEnd", {}), self.frontend_functions)

    def _format_and_add_functions(self, source: Dict[str, Any], target: List[Dict[str, Any]]) -> None:
        for name, item in source.items():
            target.append(
                {
                    "name": name,
                    "description": item.get("description", ""),
                    "commType": item.get("commType", "read"),
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
            )

    def get_all_functions(self) -> List[Dict[str, Any]]:
        return self.all_functions

    def handle_call(self, function_name: str, function_arguments: Dict[str, Any]) -> Dict[str, Any]:
        if any(fn["name"] == function_name for fn in self.frontend_functions):
            return {"role": "function", "message": function_name, "arguments": function_arguments}

        notifications = self.config.get("functions", {}).get("notifications", {})
        if function_name in notifications:
            return {
                "role": "notification",
                "message": f"notification received: {function_name}",
                "description": notifications[function_name].get("description", ""),
            }

        func_def = next((f for f in self.all_functions if f["name"] == function_name), None)
        if func_def is None:
            return {"role": "error", "message": "Error: function does not exist"}

        comm_type = func_def.get("commType", "read")
        payload = {
            "name": function_name,
            "value": function_arguments.get("value", ""),
            "dataType": func_def.get("parameters", {}).get("type", "string"),
        }

        if hasattr(self.com_object, function_name):
            result = getattr(self.com_object, function_name)()
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
