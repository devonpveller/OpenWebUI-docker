"""J.1 identity transport: inject the virtual key's lane/alias as an upstream
header so llm-queue can attribute callers. Pre-call hook per LiteLLM docs."""
from litellm.integrations.custom_logger import CustomLogger


class LaneHeaderInjector(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        md = getattr(user_api_key_dict, "metadata", None) or {}
        lane = md.get("lane") or getattr(user_api_key_dict, "key_alias", None)
        if lane:
            hdrs = data.get("extra_headers") or {}
            hdrs["x-ai-stack-caller"] = str(lane)
            data["extra_headers"] = hdrs
        return data


proxy_handler_instance = LaneHeaderInjector()
