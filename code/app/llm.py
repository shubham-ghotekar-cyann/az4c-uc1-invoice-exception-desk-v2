"""Model calls: strict JSON schema, Pydantic validation, max 2 attempts per agent, per-case budget, trace.

Attempt policy (per agent, per case):
  attempt 1  primary provider (Groq)
  attempt 2  only with a stated reason, logged in the trace:
             - provider_error  -> fallback provider (Gemini)
             - schema_invalid  -> same provider, with the validation error fed back
             - validation      -> same provider, with what did not match the tool results fed back
A rate-limit (429) wait is not an attempt: no reply was produced. It is logged as rate_limit_wait.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ValidationError

from . import config


# ------------------------------------------------------------------ trace
class Trace:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.path = config.out_dir() / "traces" / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []

    def log(self, type_: str, **data) -> None:
        event = {"t": round(time.time(), 3), "type": type_, **data}
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


@dataclass
class Budget:
    max_calls: int = field(default_factory=config.MAX_CALLS_PER_CASE)
    max_tokens: int = field(default_factory=config.MAX_TOKENS_PER_CASE)
    calls: int = 0
    tokens: int = 0

    def exhausted(self) -> str | None:
        if self.calls >= self.max_calls:
            return f"call budget reached ({self.calls}/{self.max_calls})"
        if self.tokens >= self.max_tokens:
            return f"token budget reached ({self.tokens}/{self.max_tokens})"
        return None


# ------------------------------------------------------------------ schema
def strict_schema(model: type[BaseModel], for_gemini: bool = False) -> dict:
    """Pydantic schema -> strict JSON schema: refs inlined, every property required, no extras."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: walk(v) for k, v in node.items() if k not in ("default", "title")}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                if for_gemini:
                    node.pop("additionalProperties", None)
                else:
                    node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(raw)


# ------------------------------------------------------------------ providers
class ProviderError(Exception):
    pass


@dataclass
class Provider:
    name: str
    model: str
    client: object = None
    fake: Callable[[str, str], dict] | None = None  # test double only (AZ4C_FAKE_LLM=1)

    def complete(self, messages: list[dict], schema_model: type[BaseModel], trace: Trace, agent: str) -> tuple[str, int, int]:
        if self.fake is not None:
            reply = self.fake(agent, messages[-1]["content"])
            text = json.dumps(reply)
            return text, sum(len(m["content"]) for m in messages) // 4, len(text) // 4

        import openai

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": 2500,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema_model.__name__,
                "schema": strict_schema(schema_model, for_gemini=self.name == "gemini"),
                "strict": self.name != "gemini"}},
        }
        if self.model.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = "low"
        for wait_no in range(4):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                break
            except openai.RateLimitError as e:
                if wait_no == 3:
                    raise ProviderError(f"rate limited after {wait_no} waits") from e
                retry_after = e.response.headers.get("retry-after") if e.response is not None else None
                wait = min(max(float(retry_after or 2 ** (wait_no + 1)), 1.0), 30.0)
                trace.log("rate_limit_wait", agent=agent, provider=self.name, model=self.model, seconds=wait)
                time.sleep(wait)
            except openai.APIError as e:
                raise ProviderError(f"{type(e).__name__}: {str(e)[:300]}") from e
        choice = resp.choices[0]
        text = choice.message.content
        if not text:
            raise ProviderError(f"empty reply (finish_reason={choice.finish_reason})")
        usage = resp.usage
        return text, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0


def providers_for(role: str, fake: Callable | None = None) -> list[Provider]:
    """Primary Groq model for the role, then Gemini as fallback. Keys and base URLs come from the environment."""
    if config.env("AZ4C_FAKE_LLM") == "1" and fake is not None:
        return [Provider("fake", "fake-echo", fake=fake)]
    import openai

    out = []
    model = config.ORCHESTRATOR_MODEL() if role == "orchestrator" else config.SPECIALIST_MODEL()
    if config.env("GROQ_API_KEY"):
        out.append(Provider("groq", model, client=openai.OpenAI(
            api_key=config.env("GROQ_API_KEY"), base_url=config.env("GROQ_BASE_URL").rstrip("/") + "/openai/v1",
            max_retries=0, timeout=60)))
    if config.env("GEMINI_API_KEY"):
        out.append(Provider("gemini", config.FALLBACK_MODEL(), client=openai.OpenAI(
            api_key=config.env("GEMINI_API_KEY"), base_url=config.env("GEMINI_BASE_URL").rstrip("/") + "/v1beta/openai/",
            max_retries=0, timeout=60)))
    return out


# ------------------------------------------------------------------ one agent run
@dataclass
class AgentRun:
    agent: str
    ok: bool
    output: BaseModel | None = None
    attempts: int = 0
    error: str = ""


class LLM:
    def __init__(self, trace: Trace, budget: Budget | None = None):
        self.trace = trace
        self.budget = budget or Budget()

    def run(self, agent: str, role: str, system: str, user: str, schema_model: type[BaseModel],
            validate: Callable[[BaseModel], list[str]], fake: Callable | None = None) -> AgentRun:
        providers = providers_for(role, fake)
        if not providers:
            self.trace.log("llm_skipped", agent=agent, reason="no provider configured (set GROQ_API_KEY or GEMINI_API_KEY)")
            return AgentRun(agent, ok=False, error="no provider configured")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        p_index, retry_reason, attempt = 0, None, 0
        while attempt < config.MAX_ATTEMPTS_PER_AGENT:
            stop = self.budget.exhausted()
            if stop:
                self.trace.log("llm_skipped", agent=agent, reason=stop)
                return AgentRun(agent, ok=False, attempts=attempt, error=stop)
            attempt += 1
            prov = providers[p_index]
            started = time.time()
            event = {"agent": agent, "attempt": attempt, "retry_reason": retry_reason,
                     "provider": prov.name, "model": prov.model}
            self.budget.calls += 1
            try:
                text, p_tok, c_tok = prov.complete(messages, schema_model, self.trace, agent)
            except ProviderError as e:
                self.trace.log("llm_call", **event, status="provider_error", error=str(e),
                               latency_ms=int((time.time() - started) * 1000))
                retry_reason = f"provider_error: {str(e)[:160]}"
                if p_index + 1 < len(providers):
                    p_index += 1
                continue
            self.budget.tokens += p_tok + c_tok
            event.update(prompt_tokens=p_tok, completion_tokens=c_tok, latency_ms=int((time.time() - started) * 1000))
            try:
                out = schema_model.model_validate_json(text)
            except ValidationError as e:
                err = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
                self.trace.log("llm_call", **event, status="schema_invalid", error=err)
                retry_reason = f"schema_invalid: {err[:160]}"
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"Your reply did not match the JSON schema: {err}. Reply again with valid JSON only."}]
                continue
            problems = validate(out)
            if problems:
                self.trace.log("llm_call", **event, status="validation_failed", error=problems)
                retry_reason = "validation: " + "; ".join(problems)[:200]
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": "Your reply does not match the tool results:\n- " + "\n- ".join(problems)
                              + "\nCorrect it using only the tool results. Reply with JSON only."}]
                continue
            self.trace.log("llm_call", **event, status="ok", output=json.loads(text))
            return AgentRun(agent, ok=True, output=out, attempts=attempt)
        self.trace.log("agent_failed", agent=agent, attempts=attempt, last_reason=retry_reason)
        return AgentRun(agent, ok=False, attempts=attempt, error=retry_reason or "failed")
