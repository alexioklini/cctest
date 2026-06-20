#!/usr/bin/env python3
"""Compare M4 7B (Qwen2.5-7B-Instruct-4bit @ 192.168.1.214:8012) against the cloud
model each background use case previously used, driving the LIVE sidecar /turn
endpoint directly (the real inference path). Prompts are copied verbatim from the
production call sites (handlers/chat.py, engine/wiki_store.py, server.py,
engine/code_graph.py, handlers/admin_artifacts.py, server_lib/translate/detect.py).

For each use case: run the SAME prompt through M4 7B and through the cloud model,
print latency + output so quality/speed can be judged side by side.
"""
import json, urllib.request, time, sys

CFG = json.load(open('config.json'))
PROV = CFG['providers']
def key(n): return PROV[n].get('api_key', '')

M4 = ("M4-7B", "Qwen2.5-7B-Instruct-4bit", "http://192.168.1.214:8012", key("Lokal-M4"))
SMALL = ("cloud-small", "mistral-small-latest", "http://127.0.0.1:8317", key("CLIProxyAPI"))
MEDIUM = ("cloud-medium", "mistral-medium-3.5", "http://127.0.0.1:8317", key("CLIProxyAPI"))
# Larger local fallback for the user-profile case (oMLX). NOTE: gemma-4-12B on
# oMLX can 500 with "Thread group size (1024) > maximum (640)" — a Metal kernel
# limit, not a model-quality issue. If it returns the sidecar EMPTY_GIVEUP_TEXT
# with usage input/output_tokens=0, check ~/.omlx/logs/server.log for that crash
# and adjust the oMLX model config. When serving, it scored 5/5 grounded on the
# profile case (~11s) — on par with the post-fix M4 7B.
GEMMA12 = ("gemma-12B", "gemma-4-12B-it-qat-4bit", "http://localhost:8000",
           PROV.get("Lokal", {}).get("api_key", ""))


def call(prov, system, user, max_tokens, temperature=None, forced_tool=None):
    name, model, base, api_key = prov
    payload = {
        "model": model, "base_url": base, "api_key": api_key,
        "system": system,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
        "tools": [], "max_tokens": max_tokens, "max_rounds": 1,
        "tool_context": {"session_id": "", "agent_id": "main"},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if forced_tool:
        payload["tools"] = [forced_tool]
        payload["tool_choice"] = {"type": "tool", "name": forced_tool["name"]}
        payload["capture_forced_tool"] = forced_tool["name"]
    req = urllib.request.Request("http://127.0.0.1:8421/turn?stream=false",
        data=json.dumps(payload, ensure_ascii=False).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=120)
        d = json.loads(r.read().decode())
        dt = time.time() - t0
        out = d.get("forced_tool_input") if forced_tool else d.get("final_text", "")
        return dt, out, d.get("error")
    except Exception as e:
        return time.time() - t0, "", f"{type(e).__name__}: {e}"


def run(title, system, user, max_tokens, temperature=None, providers=(M4, SMALL), forced_tool=None):
    print("\n" + "=" * 78)
    print(f"# {title}")
    print("=" * 78)
    for prov in providers:
        dt, out, err = call(prov, system, user, max_tokens, temperature, forced_tool)
        tag = prov[0]
        if err:
            print(f"\n[{tag}] {dt:.2f}s  *** ERROR: {err} ***")
        else:
            shown = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            print(f"\n[{tag}] {dt:.2f}s")
            for line in str(shown).splitlines() or [""]:
                print("   " + line)
            if not str(shown).strip():
                print("   <EMPTY>")


# ---- Realistic shared conversation sample (a real-ish dev chat) ----
CONV = """User: How do I set up vLLM on the Mac mini M4 to serve Qwen2.5-7B?
Assistant: You'll want vllm-metal with the MLX backend. Install it in a fresh venv, then launch with the Anthropic-compatible /v1/messages server on port 8012. Pin inference.max_tokens=4096 in the Brain config or chat.py reads the wrong field and hangs.
User: Great, it's serving now. Can you also make all the background tasks use it instead of mistral-small?
Assistant: Yes — point chat_summary_model, user_profile_model, code_graph_model, next_prompt_model and wiki_gate_model at Lokal-M4/Qwen2.5-7B-Instruct-4bit. The classifier and wiki stay on cloud mistral-small for now.
User: Perfect. Remember that I prefer committing directly to main, no feature branches."""


def uc_chat_summary():
    # handlers/chat.py:_generate_chat_summary — user prompt + system, max_tokens=120
    users = [
        "How do I set up vLLM on the Mac mini M4 to serve Qwen2.5-7B?",
        "Can you also make all the background tasks use it instead of mistral-small?",
        "Remember that I prefer committing directly to main, no feature branches.",
    ]
    body = "\n".join(f"- {u}" for u in users)
    user = ("Summarize the topics the user has asked about across this conversation "
            "in one short line (max 100 chars). If several distinct topics came up, "
            "cover them briefly rather than only the latest. Focus on the topics/tasks, "
            "not greetings. Output ONLY the summary, nothing else. Base your summary "
            "ONLY on the user questions below.\n\n" + body)
    run("#7 CHAT SUMMARY (sidebar synopsis)",
        "Output only a brief summary sentence. No quotes, no prefix.",
        user, 120)


def uc_wiki_gate():
    SYS = ("You decide whether a conversation is worth saving to the user's long-term "
           "knowledge wiki. Save it ONLY if it contains something durable and worth "
           "recalling later: a fact about the user or their work, a stated preference, "
           "a decision or plan, or a reference (a resource/tool/project/document). Do "
           "NOT save pure small talk, greetings, one-off lookups with no lasting value, "
           "or exchanges the assistant refused. Reply with ONLY one word: SAVE or SKIP.")
    # Case A: clearly worth saving (has a preference + decision)
    run("#8 WIKI-GATE — case A (durable: preference + decision -> expect SAVE)",
        SYS, CONV, 8)
    # Case B: pure small talk -> expect SKIP
    smalltalk = ("User: hey\nAssistant: Hi! How can I help?\nUser: nothing, just saying hi\n"
                 "Assistant: No problem, have a great day!")
    run("#8 WIKI-GATE — case B (small talk -> expect SKIP)",
        SYS, smalltalk, 8)


def _load_production_profile_prompt():
    """Load the REAL _PROFILE_SYSTEM_PROMPT from server.py via AST so the eval
    tests exactly what the daemon runs (not a hand-copied approximation)."""
    import ast
    src = open('server.py').read()
    ns = {}
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("_PROFILE_SECTION_INSTRUCTIONS",
                                           "_PROFILE_SYSTEM_PROMPT")):
            ns[node.targets[0].id] = eval(
                compile(ast.Expression(node.value), '<x>', 'eval'), {}, ns)
    return ns["_PROFILE_SYSTEM_PROMPT"]


def uc_user_profile():
    SYS = _load_production_profile_prompt()
    user = ("Build the profile from scratch. The user's preferred name is Alexander.\n\n"
            "CHAT SAMPLES (most recent first):\n" + CONV +
            "\n\nOutput the COMPLETE profile using the schema above.")
    # The profile case is where M4 7B regressed (hallucination) before the
    # v9.177.0 GROUNDING/CAPTURE prompt fix; gemma-12B is the larger local
    # fallback. Compare all three so a future prompt/model change can be
    # re-checked the same way. gemma is included only if oMLX is generating.
    run("#10 USER-PROFILE DAEMON", SYS, user, 2000,
        providers=(M4, GEMMA12, SMALL))


def uc_code_graph():
    SYS = "Output only numbered one-line summaries. No markdown, no explanations."
    user = ("For each function/class below from `sidecar_proxy.py`, write a ONE-LINE "
            "summary (max 80 chars) describing what it does. Output as numbered list "
            "matching the order.\n\n"
            "1. **background_call(messages, model, system_prompt, cost_purpose, ...)**\n"
            "```\nResolves provider + inference params from the model id, builds a minimal\n"
            "tool_context, calls run_turn_blocking, logs the cost centrally, returns the\n"
            "reply dict. Thin wrapper for non-interactive background LLM calls.\n```\n\n"
            "2. **_normalise_anthropic_base_url(base_url) -> str**\n```\n"
            "Strips at most one trailing /v1 from an OpenAI-style base url so the Anthropic\n"
            "SDK doesn't post to /v1/v1/messages. Idempotent.\n```\n\n"
            "3. **run_turn_blocking(messages, model, api_key, base_url, ...)**\n```\n"
            "Non-streaming background turn: mints a nonce, builds the tool list + payload,\n"
            "POSTs to the sidecar /turn?stream=false, returns final_text + usage + error.\n```")
    run("#16 CODE-GRAPH SUMMARIES", SYS, user, 2000)


def uc_refine():
    POLISH = ("You are a PROMPT REWRITER for an AI chat system. The user will give you a "
              "draft prompt/message they want to send to an AI assistant. Your job is to "
              "rewrite it into a better, clearer version of the SAME request. CRITICAL RULES:\n"
              "- Output ONLY the rewritten prompt, nothing else\n"
              "- Do NOT answer the question or fulfill the request — REWRITE it\n"
              "- Do NOT add explanations, analysis, alternatives, or commentary\n"
              "- Do NOT use markdown headings, bullet points, or formatting\n"
              "- Fix grammar, spelling, punctuation\n- Keep the same intent and language\n"
              "Example: Input: 'whats weather vienna' -> Output: 'What is the weather like "
              "in Vienna today?'")
    # Refine puts the system text in the user content per the handler; replicate simply.
    draft = "make the local 7b model handle chat summaries and check its not slower than mistral"
    user = f"{POLISH}\n\nDraft to rewrite:\n{draft}"
    run("#5 REFINE (Polish tier)", "", user, 400, temperature=0.2)


def uc_lang_detect():
    SYS = ("You are a language identifier. Output a single ISO 639-1 code, lowercase, "
           "nothing else.")
    for label, text in [
        ("German", "Bitte fasse die wichtigsten Punkte dieses Berichts kurz zusammen."),
        ("English", "Please summarize the key points of this report briefly."),
        ("French", "Veuillez résumer brièvement les points clés de ce rapport."),
    ]:
        user = ("Detect the language of this text. Reply with only the ISO 639-1 two-letter "
                f"code (e.g. 'en', 'de'). No explanation.\n\n{text}")
        run(f"#21 LANG-DETECT — {label}", SYS, user, 8)


ALL = {
    "summary": uc_chat_summary, "wiki": uc_wiki_gate, "profile": uc_user_profile,
    "codegraph": uc_code_graph, "refine": uc_refine, "lang": uc_lang_detect,
}

if __name__ == "__main__":
    which = sys.argv[1:] or list(ALL)
    for w in which:
        ALL[w]()
