"""Fast (<2s) LLM provider auto-detection and neutrality-guarded enrichment wrapper."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aiwatchdogs.core.safety import lint_neutrality, scrub_pii

RULE_ENGINE_PROVIDER = "rule-engine"
RULE_ENGINE_MODEL = "deterministic-python-v1"
RULE_ENGINE_LABEL = "Deterministic CPU Auditor (rule-engine) — ₹0 API cost"

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_PROBE_TIMEOUT_S = 0.4

PREFERRED_OLLAMA_PREFIXES = (
    "qwen2.5",
    "qwen3",
    "llama3.3",
    "llama3.2",
    "llama3.1",
    "mistral",
    "gemma",
    "deepseek",
    "phi",
)

CLOUD_PROVIDER_SPECS: tuple[dict[str, str], ...] = (
    {
        "provider": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "label_prefix": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
    },
    {
        "provider": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-sonnet-4-20250514",
        "label_prefix": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1",
    },
    {
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "label_prefix": "OpenAI",
        "base_url": "https://api.openai.com/v1",
    },
    {
        "provider": "openrouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "default_model": "meta-llama/llama-3.3-70b-instruct",
        "label_prefix": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
    },
)

DEFAULT_BENIGN_FALLBACK = (
    "Requires verification against original sanction file notings, applicable GFR 2017 "
    "exceptions, or departmental approval memos before drawing operational conclusions."
)


class ProviderInfo(BaseModel):
    """Describes an available or selected compute/model provider for the watchdog runner."""

    provider: str
    model: str
    label: str
    available: bool = True
    is_local: bool = False
    zero_cost: bool = False
    base_url: str | None = None
    api_key_env: str | None = None
    detected_models: list[str] = Field(default_factory=list)


def rule_engine_provider() -> ProviderInfo:
    """Return the always-available offline zero-cost deterministic Python engine."""
    return ProviderInfo(
        provider=RULE_ENGINE_PROVIDER,
        model=RULE_ENGINE_MODEL,
        label=RULE_ENGINE_LABEL,
        available=True,
        is_local=True,
        zero_cost=True,
        base_url=None,
        api_key_env=None,
        detected_models=[RULE_ENGINE_MODEL],
    )


def _normalize_url(raw: str) -> str:
    cleaned = raw.strip().rstrip("/")
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"http://{cleaned}"
    return cleaned


def _pick_ollama_model(models: list[str], preferred_env: str = "") -> str:
    if preferred_env and preferred_env in models:
        return preferred_env
    lowered = [(m, m.lower()) for m in models]
    for prefix in PREFERRED_OLLAMA_PREFIXES:
        for original, low in lowered:
            if low.startswith(prefix):
                return original
    if models:
        return models[0]
    return preferred_env or "qwen2.5:7b"


def _probe_ollama(
    base_url: str,
    *,
    http_client: httpx.Client | None = None,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> list[str] | None:
    """Quick 400ms probe of Ollama /api/tags; returns list of installed model names or None."""
    url = f"{base_url.rstrip('/')}/api/tags"
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=httpx.Timeout(timeout_s))
    try:
        resp = client.get(url, timeout=timeout_s)
        if resp.status_code != 200:
            return None
        data = resp.json()
        raw_models = data.get("models", []) if isinstance(data, dict) else []
        names: list[str] = []
        for item in raw_models:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
        return names
    except (httpx.HTTPError, ValueError, OSError):
        return None
    finally:
        if owns_client:
            client.close()


def _probe_openai_compat_models(
    base_url: str,
    *,
    api_key: str | None = None,
    http_client: httpx.Client | None = None,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> list[str]:
    """Best-effort 400ms probe of an OpenAI-compatible /models endpoint."""
    normalized = base_url.rstrip("/")
    candidate_urls = [f"{normalized}/models"]
    if not normalized.endswith("/v1"):
        candidate_urls.append(f"{normalized}/v1/models")

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=httpx.Timeout(timeout_s))
    try:
        for url in candidate_urls:
            try:
                resp = client.get(url, headers=headers, timeout=timeout_s)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", []) if isinstance(data, dict) else []
                    names = [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]
                    if names:
                        return names
            except (httpx.HTTPError, ValueError, OSError):
                continue
        return []
    finally:
        if owns_client:
            client.close()


def detect_providers(
    *,
    probe_local: bool = True,
    env: Mapping[str, str] | None = None,
    http_client: httpx.Client | None = None,
    probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> list[ProviderInfo]:
    """Detect available local & cloud LLM providers in <2s, always including rule-engine."""
    active_env = os.environ if env is None else env
    detected: list[ProviderInfo] = []

    # 1. Local Ollama (400ms probe of OLLAMA_HOST or http://127.0.0.1:11434)
    ollama_env_host = active_env.get("OLLAMA_HOST", "").strip()
    ollama_url = _normalize_url(ollama_env_host or DEFAULT_OLLAMA_HOST)
    ollama_model_env = active_env.get("OLLAMA_MODEL", "").strip()
    if probe_local:
        ollama_models = _probe_ollama(
            ollama_url,
            http_client=http_client,
            timeout_s=probe_timeout_s,
        )
        if ollama_models is not None:
            chosen = _pick_ollama_model(ollama_models, preferred_env=ollama_model_env)
            detected.append(
                ProviderInfo(
                    provider="ollama",
                    model=chosen,
                    label=f"Local Ollama ({chosen}) — ₹0 API cost",
                    available=True,
                    is_local=True,
                    zero_cost=True,
                    base_url=ollama_url,
                    detected_models=ollama_models or [chosen],
                )
            )
    elif ollama_env_host:
        chosen = ollama_model_env or "qwen2.5:7b"
        detected.append(
            ProviderInfo(
                provider="ollama",
                model=chosen,
                label=f"Local Ollama ({chosen}) — ₹0 API cost",
                available=True,
                is_local=True,
                zero_cost=True,
                base_url=ollama_url,
                detected_models=[chosen],
            )
        )

    # 2. Local vLLM / OpenAI-compatible base URL
    vllm_raw = (
        active_env.get("VLLM_BASE_URL", "").strip()
        or active_env.get("OPENAI_BASE_URL", "").strip()
    )
    if vllm_raw:
        vllm_url = _normalize_url(vllm_raw)
        vllm_key = active_env.get("VLLM_API_KEY", "").strip() or active_env.get("OPENAI_API_KEY", "").strip() or None
        probed_models: list[str] = []
        if probe_local:
            probed_models = _probe_openai_compat_models(
                vllm_url,
                api_key=vllm_key,
                http_client=http_client,
                timeout_s=probe_timeout_s,
            )
        chosen_vllm = (
            active_env.get("VLLM_MODEL", "").strip()
            or active_env.get("OPENAI_MODEL", "").strip()
            or (probed_models[0] if probed_models else "local-vllm")
        )
        detected.append(
            ProviderInfo(
                provider="vllm",
                model=chosen_vllm,
                label=f"OpenAI-Compatible / vLLM ({chosen_vllm})",
                available=True,
                is_local=True,
                zero_cost=True,
                base_url=vllm_url,
                api_key_env="VLLM_API_KEY" if active_env.get("VLLM_API_KEY") else None,
                detected_models=probed_models or [chosen_vllm],
            )
        )

    # 3. Cloud BYO API keys
    for spec in CLOUD_PROVIDER_SPECS:
        key_env = spec["api_key_env"]
        if active_env.get(key_env, "").strip():
            model_name = active_env.get(spec["model_env"], "").strip() or spec["default_model"]
            detected.append(
                ProviderInfo(
                    provider=spec["provider"],
                    model=model_name,
                    label=f"{spec['label_prefix']} ({model_name} via {key_env})",
                    available=True,
                    is_local=False,
                    zero_cost=False,
                    base_url=spec["base_url"],
                    api_key_env=key_env,
                    detected_models=[model_name],
                )
            )

    # 4. Always available: offline deterministic Python rule-engine
    detected.append(rule_engine_provider())
    return detected


def detect_best_provider(
    *,
    probe_local: bool = True,
    env: Mapping[str, str] | None = None,
    http_client: httpx.Client | None = None,
    probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> ProviderInfo:
    """Return the highest-priority detected provider (falls back cleanly to rule-engine)."""
    providers = detect_providers(
        probe_local=probe_local,
        env=env,
        http_client=http_client,
        probe_timeout_s=probe_timeout_s,
    )
    return providers[0] if providers else rule_engine_provider()


def resolve_provider_config(
    provider: str | None = None,
    model: str | None = None,
    *,
    base_url: str | None = None,
    env: Mapping[str, str] | None = None,
    probe_local: bool = True,
    http_client: httpx.Client | None = None,
) -> ProviderInfo:
    """Resolve an explicit provider/model choice or auto-detect when provider is None/'auto'."""
    active_env = os.environ if env is None else env
    normalized = (provider or "auto").strip().lower()

    if normalized in ("", "auto"):
        chosen = detect_best_provider(
            probe_local=probe_local,
            env=active_env,
            http_client=http_client,
        )
        if model:
            return chosen.model_copy(update={"model": model})
        return chosen

    if normalized in (RULE_ENGINE_PROVIDER, "deterministic", "cpu", "offline"):
        base = rule_engine_provider()
        if model:
            return base.model_copy(update={"model": model})
        return base

    if normalized == "ollama":
        url = _normalize_url(base_url or active_env.get("OLLAMA_HOST", "") or DEFAULT_OLLAMA_HOST)
        chosen_model = model or active_env.get("OLLAMA_MODEL", "").strip() or "qwen2.5:7b"
        return ProviderInfo(
            provider="ollama",
            model=chosen_model,
            label=f"Local Ollama ({chosen_model}) — ₹0 API cost",
            available=True,
            is_local=True,
            zero_cost=True,
            base_url=url,
            detected_models=[chosen_model],
        )

    if normalized in ("vllm", "openai-compatible"):
        url = _normalize_url(
            base_url
            or active_env.get("VLLM_BASE_URL", "")
            or active_env.get("OPENAI_BASE_URL", "")
            or "http://127.0.0.1:8001/v1"
        )
        chosen_model = (
            model
            or active_env.get("VLLM_MODEL", "").strip()
            or active_env.get("OPENAI_MODEL", "").strip()
            or "local-vllm"
        )
        return ProviderInfo(
            provider="vllm",
            model=chosen_model,
            label=f"OpenAI-Compatible / vLLM ({chosen_model})",
            available=True,
            is_local=True,
            zero_cost=True,
            base_url=url,
            api_key_env="VLLM_API_KEY" if active_env.get("VLLM_API_KEY") else None,
            detected_models=[chosen_model],
        )

    for spec in CLOUD_PROVIDER_SPECS:
        if spec["provider"] == normalized:
            chosen_model = model or active_env.get(spec["model_env"], "").strip() or spec["default_model"]
            return ProviderInfo(
                provider=spec["provider"],
                model=chosen_model,
                label=f"{spec['label_prefix']} ({chosen_model})",
                available=bool(active_env.get(spec["api_key_env"], "").strip()),
                is_local=False,
                zero_cost=False,
                base_url=_normalize_url(base_url) if base_url else spec["base_url"],
                api_key_env=spec["api_key_env"],
                detected_models=[chosen_model],
            )

    # Unknown provider string: degrade safely to rule-engine
    return rule_engine_provider()


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    """Extract a JSON object from an LLM text response, stripping markdown fences if present."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _build_enrichment_prompt(lead_dict: dict[str, Any]) -> tuple[str, str]:
    system_prompt = (
        "You are a neutral Indian public-finance civic-audit assistant following BNS Sec 356 "
        "fair public-interest reporting standards and DPDP Act 2023 privacy safeguards. "
        "NEVER use accusatory or defamatory words (such as scam, corrupt, corruption, bribe, "
        "fraud, kickback, guilty, stolen, rigged, crook, loot, embezzle). "
        "NEVER invent or alter numeric metrics. "
        "Return ONLY a JSON object with keys 'summary' (string, 2-3 factual sentences) and "
        "'benign_explanations' (array of 2-3 plausible lawful institutional explanations under "
        "GFR 2017, CVC guidelines, or statutory rules)."
    )
    user_payload = {
        "methodology_code": lead_dict.get("methodology_code", ""),
        "title": lead_dict.get("title", ""),
        "entity_name": lead_dict.get("entity_name", ""),
        "jurisdiction": lead_dict.get("jurisdiction", ""),
        "fiscal_year": lead_dict.get("fiscal_year", ""),
        "rule_triggered": lead_dict.get("rule_triggered", ""),
        "monetary_exposure_inr": lead_dict.get("monetary_exposure_inr", 0.0),
        "anomaly_metrics": lead_dict.get("anomaly_metrics", {}),
        "deterministic_summary": lead_dict.get("summary", ""),
        "existing_benign_explanations": lead_dict.get("benign_explanations", []),
    }
    user_prompt = (
        "Polish the factual summary and expand lawful benign explanations for this public-record "
        "audit lead. Respond strictly with valid JSON:\n"
        f"{json.dumps(user_payload, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt


def _call_llm_provider(
    provider_cfg: ProviderInfo,
    system_prompt: str,
    user_prompt: str,
    *,
    env: Mapping[str, str],
    http_client: httpx.Client | None = None,
    timeout_s: float = 15.0,
) -> str | None:
    """Make a lightweight httpx REST call to the configured LLM provider (zero extra SDKs)."""
    provider = provider_cfg.provider.lower()
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=httpx.Timeout(timeout_s))

    try:
        if provider == "ollama":
            base = (provider_cfg.base_url or DEFAULT_OLLAMA_HOST).rstrip("/")
            resp = client.post(
                f"{base}/api/chat",
                json={
                    "model": provider_cfg.model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            message = data.get("message", {}) if isinstance(data, dict) else {}
            content = message.get("content") if isinstance(message, dict) else None
            return str(content) if content else None

        if provider in ("vllm", "openai", "openrouter"):
            default_base = (
                "https://openrouter.ai/api/v1"
                if provider == "openrouter"
                else "https://api.openai.com/v1"
            )
            base = (provider_cfg.base_url or default_base).rstrip("/")
            endpoint = f"{base}/chat/completions" if base.endswith("/v1") or provider == "openrouter" else f"{base}/v1/chat/completions"

            api_key = ""
            if provider_cfg.api_key_env:
                api_key = env.get(provider_cfg.api_key_env, "").strip()
            elif provider == "openai":
                api_key = env.get("OPENAI_API_KEY", "").strip()
            elif provider == "openrouter":
                api_key = env.get("OPENROUTER_API_KEY", "").strip()
            elif provider == "vllm":
                api_key = env.get("VLLM_API_KEY", "").strip() or env.get("OPENAI_API_KEY", "").strip()

            if provider in ("openai", "openrouter") and not api_key:
                return None

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://aiwatchdogs.in"
                headers["X-Title"] = "AI Watchdogs India"

            resp = client.post(
                endpoint,
                headers=headers,
                json={
                    "model": provider_cfg.model,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            choices = data.get("choices", []) if isinstance(data, dict) else []
            if not choices:
                return None
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content")
            return str(content) if content else None

        if provider == "gemini":
            api_key = env.get(provider_cfg.api_key_env or "GEMINI_API_KEY", "").strip()
            if not api_key:
                return None
            base = (provider_cfg.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            endpoint = f"{base}/models/{provider_cfg.model}:generateContent"
            resp = client.post(
                endpoint,
                params={"key": api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            candidates = data.get("candidates", []) if isinstance(data, dict) else []
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return None
            text = parts[0].get("text")
            return str(text) if text else None

        if provider == "anthropic":
            api_key = env.get(provider_cfg.api_key_env or "ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                return None
            base = (provider_cfg.base_url or "https://api.anthropic.com/v1").rstrip("/")
            resp = client.post(
                f"{base}/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": provider_cfg.model,
                    "max_tokens": 512,
                    "temperature": 0.1,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            blocks = data.get("content", []) if isinstance(data, dict) else []
            texts = [str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            joined = "\n".join(t for t in texts if t).strip()
            return joined or None

        return None
    except (httpx.HTTPError, ValueError, OSError, KeyError):
        return None
    finally:
        if owns_client:
            client.close()


def enrich_finding_summary(
    lead: BaseModel | dict[str, Any],
    provider_cfg: ProviderInfo | dict[str, Any] | None = None,
    *,
    http_client: httpx.Client | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Optionally enrich a deterministic audit lead's summary & benign explanations via LLM.

    ALWAYS scrubs PII and validates output with ``lint_neutrality()``. Falls back safely
    to the deterministic baseline if the LLM is unavailable, fails, or violates neutrality.
    """
    lead_dict = lead.model_dump(mode="json") if isinstance(lead, BaseModel) else dict(lead)
    active_env = os.environ if env is None else env

    raw_title = str(lead_dict.get("title", "Public Record Anomaly Lead")).strip()
    raw_summary = str(lead_dict.get("summary", "")).strip()
    raw_benign = [
        str(item).strip()
        for item in (lead_dict.get("benign_explanations") or [])
        if str(item).strip()
    ]
    if not raw_benign:
        raw_benign = [DEFAULT_BENIGN_FALLBACK]

    clean_title, _ = scrub_pii(raw_title)
    clean_summary, _ = scrub_pii(raw_summary)
    clean_benign = [scrub_pii(item)[0] for item in raw_benign]

    baseline_lint = lint_neutrality(clean_title, clean_summary, clean_benign)

    cfg: ProviderInfo | None
    if provider_cfg is None:
        cfg = None
    elif isinstance(provider_cfg, ProviderInfo):
        cfg = provider_cfg
    else:
        cfg = ProviderInfo.model_validate(provider_cfg)

    def _deterministic_result(reason: str | None = None) -> dict[str, Any]:
        return {
            "title": clean_title,
            "summary": clean_summary,
            "benign_explanations": clean_benign,
            "enriched_by": (
                f"{RULE_ENGINE_PROVIDER} (fallback)"
                if reason
                else RULE_ENGINE_PROVIDER
            ),
            "used_llm": False,
            "neutrality_passed": baseline_lint.passed,
            "fallback_reason": reason,
        }

    if cfg is None or cfg.provider.lower() == RULE_ENGINE_PROVIDER:
        return _deterministic_result()

    system_prompt, user_prompt = _build_enrichment_prompt(
        {
            **lead_dict,
            "title": clean_title,
            "summary": clean_summary,
            "benign_explanations": clean_benign,
        }
    )

    llm_raw = _call_llm_provider(
        cfg,
        system_prompt,
        user_prompt,
        env=active_env,
        http_client=http_client,
        timeout_s=timeout_s,
    )
    if not llm_raw:
        return _deterministic_result(reason="llm_unavailable")

    parsed = _extract_json_object(llm_raw)
    if not parsed:
        return _deterministic_result(reason="invalid_llm_json")

    candidate_summary_raw = str(parsed.get("summary", "")).strip()
    candidate_benign_raw = parsed.get("benign_explanations")
    if not candidate_summary_raw:
        return _deterministic_result(reason="empty_llm_summary")

    candidate_benign_list: list[str] = []
    if isinstance(candidate_benign_raw, list):
        candidate_benign_list = [
            scrub_pii(str(item).strip())[0]
            for item in candidate_benign_raw
            if str(item).strip()
        ]
    if not candidate_benign_list:
        candidate_benign_list = list(clean_benign)

    candidate_summary, _ = scrub_pii(candidate_summary_raw)
    lint_result = lint_neutrality(clean_title, candidate_summary, candidate_benign_list)
    if not lint_result.passed:
        return _deterministic_result(
            reason=f"neutrality_violation: {', '.join(lint_result.violations)}"
        )

    return {
        "title": clean_title,
        "summary": candidate_summary,
        "benign_explanations": candidate_benign_list,
        "enriched_by": f"{cfg.provider}/{cfg.model}",
        "used_llm": True,
        "neutrality_passed": True,
        "fallback_reason": None,
    }
