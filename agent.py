"""Create or update the agent, its webhook tools, and the tool auth secret. Safe to re-run after edits."""
import hashlib

from el import ROOT, api, env, state

ISO = "ISO 8601 with offset for the rental tz, e.g. 2026-10-05T14:00:00-07:00"
TOOLS = {  # name: (description, endpoint, body params)
    "verify_renter": ("Verify identity. Required before sharing any rental details.", "verify", {"zip": "Billing ZIP code the person gives"}),
    "quote_extension": ("Price an extension to a new return time.", "quote", {"new_return_time": ISO}),
    "extend_rental": ("Apply the extension after the renter accepts the quoted price.", "extend", {"new_return_time": ISO}),
    "log_return_eta": ("Record when the renter will return if they decline an extension.", "eta", {"eta": ISO}),
}


def webhook(name, desc, path, params, secret_id):
    props = {k: {"type": "string", "description": v} for k, v in params.items()}
    props["conversation_id"] = {"type": "string", "dynamic_variable": "system__conversation_id"}
    return {"type": "webhook", "name": name, "description": desc, "response_timeout_secs": 10, "api_schema": {
        "url": f"{env('PUBLIC_URL')}/rentals/{{reservation_id}}/{path}", "method": "POST",
        "path_params_schema": {"reservation_id": {"type": "string", "dynamic_variable": "reservation_id"}},
        "request_body_schema": {"type": "object", "required": list(props), "properties": props},
        "request_headers": {"X-Api-Key": {"secret_id": secret_id}}}}


def system(name, **params):
    return {"type": "system", "name": name, "params": {"system_tool_type": name, **params}}


def main():
    s = state()
    token_hash = hashlib.sha256(env("TOOL_TOKEN").encode()).hexdigest()[:12]
    if s.get("token_hash") != token_hash:  # new or rotated TOOL_TOKEN -> new secret
        s = state(token_hash=token_hash, secret_id=api("POST", "/secrets", json={"type": "new", "name": f"rental_api_token_{token_hash}", "value": env("TOOL_TOKEN")})["secret_id"])
    tool_ids = s.get("tool_ids", {})
    for name, spec in TOOLS.items():
        cfg = {"tool_config": webhook(name, *spec, s["secret_id"])}
        if name in tool_ids:
            api("PATCH", f"/tools/{tool_ids[name]}", json=cfg)
        else:
            tool_ids[name] = api("POST", "/tools", json=cfg)["id"]
            state(tool_ids=tool_ids)

    agent = {
        "name": "Northwind late-return agent",
        "conversation_config": {"agent": {
            "first_message": "Hi, this is Northwind Rentals' AI assistant. This call may be recorded. Am I speaking with {{first_name}}?",
            "language": "en",
            "dynamic_variables": {"dynamic_variable_placeholders": {  # sample renter (ZIP 94107) for testing in the browser
                "reservation_id": "R-1001", "first_name": "Maria", "tz": "America/Los_Angeles",
                "due_at": "today at 5:00 PM", "branch": "SFO Airport", "vehicle": "Toyota Camry"}},
            "prompt": {"prompt": (ROOT / "prompt.md").read_text(), "llm": env("LLM"), "tool_ids": list(tool_ids.values()), "built_in_tools": {
                "end_call": system("end_call"),
                "language_detection": system("language_detection"),
                "voicemail_detection": system("voicemail_detection", voicemail_message="Hi, this is Northwind Rentals calling about your rental. Please call your pickup branch. Thank you."),
                "transfer_to_number": system("transfer_to_number", transfers=[{
                    "transfer_destination": {"type": "phone", "phone_number": env("BRANCH_PHONE")},
                    "condition": "Accident, theft, breakdown, safety concern, billing dispute, or the renter asks for a person.",
                    "transfer_type": "conference"}]),
            }}},
            "language_presets": {"es": {"overrides": {"agent": {
                "first_message": "Hola, soy el asistente de IA de Northwind Rentals. Esta llamada puede ser grabada. ¿Hablo con {{first_name}}?"}}}}},
        "platform_settings": {
            "data_collection": {
                "outcome": {"type": "string", "description": "How the call ended.",
                            "enum": ["extended", "returning_on_time", "returning_late", "escalated", "not_verified", "voicemail"]},
                "new_return_time": {"type": "string", "description": "Agreed new return time or ETA, ISO 8601. Empty if none."},
                "escalated": {"type": "boolean", "description": "True if the call was transferred to staff."},
            },
            "evaluation": {"criteria": [
                {"id": "verify_first", "name": "Verified before disclosure", "conversation_goal_prompt": "The agent shared no vehicle, branch, time, or price details before verify_renter succeeded."},
                {"id": "plan_secured", "name": "Plan secured", "conversation_goal_prompt": "The call ended with an extension, a return ETA, or a transfer to staff."},
            ]},
        },
    }
    agent_id = s.get("agent_id")
    if agent_id:
        api("PATCH", f"/agents/{agent_id}", json=agent)
    else:
        agent_id = api("POST", "/agents/create", json=agent)["agent_id"]
    state(agent_id=agent_id)
    print(f"Agent ready: {agent_id}")


if __name__ == "__main__":
    main()
