"""OpenAI client wrapper with hard JSON guarantees and token accounting.

The baseline parses the model's reply with a bare `json.loads`, and on failure
sets `response = {}` — which silently produces an empty script and burns an
iteration (base_vlm.py:405). Given Gemini failed 40% of edits and Claude 67%,
that class of silent failure is the single biggest score leak in the benchmark.
Here a malformed reply is retried with the parse error fed back, and only then
raises.
"""

import base64
import json
import os.path as osp
import time

from openai import OpenAI

from .. import config


def _cost(inp, out):
    return config.COST_IN * inp / 1e6 + config.COST_OUT * out / 1e6


class Usage:
    """Running token / cost total for a whole session.

    Totals are also split PER ROLE (strategist / executor / qa / one-shot).
    The aggregate alone cannot answer the only question worth asking about
    cost — which agent to make cheaper — because the roles differ by an order
    of magnitude in call count and by design in how much they think. Measured
    on this project, reasoning is 87-93% of all output tokens and output is
    ~85% of spend, so the effort dial matters far more than prompt size; the
    per-role split is what says which dial to turn. Roles can also share a
    model, so `per_model` cannot stand in for this.
    """

    _ROLE_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens",
                    "cached_input_tokens", "llm_calls")

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.cached_input_tokens = 0
        self.calls = 0
        self.per_model = {}  # model id -> {"input_tokens": n, "output_tokens": n}
        self.per_role = {}   # role -> {tokens..., "llm_calls": n}

    def add(self, resp, role=None):
        u = getattr(resp, "usage", None)
        if not u:
            return
        # chat.completions names first, Responses API names as fallback
        inp = (getattr(u, "prompt_tokens", None)
               or getattr(u, "input_tokens", 0) or 0)
        out = (getattr(u, "completion_tokens", None)
               or getattr(u, "output_tokens", 0) or 0)
        self.input_tokens += inp
        self.output_tokens += out
        details = (getattr(u, "completion_tokens_details", None)
                   or getattr(u, "output_tokens_details", None))
        reasoning = 0
        if details is not None:
            reasoning = getattr(details, "reasoning_tokens", 0) or 0
            self.reasoning_tokens += reasoning
        # How much of the input the provider served from its prompt cache.
        # Recorded, not priced: the cached rate is a provider setting we do not
        # have a constant for, and guessing one would make `cost_estimate` a
        # fiction. A nonzero number here is the only honest evidence that
        # prompt caching is working at all.
        pdetails = (getattr(u, "prompt_tokens_details", None)
                    or getattr(u, "input_tokens_details", None))
        cached = 0
        if pdetails is not None:
            cached = getattr(pdetails, "cached_tokens", 0) or 0
            self.cached_input_tokens += cached
        self.calls += 1

        m = getattr(resp, "model", "unknown")
        bucket = self.per_model.setdefault(m, {"input_tokens": 0, "output_tokens": 0})
        bucket["input_tokens"] += inp
        bucket["output_tokens"] += out

        r = self.per_role.setdefault(role or "unattributed",
                                     {k: 0 for k in self._ROLE_FIELDS})
        r["input_tokens"] += inp
        r["output_tokens"] += out
        r["reasoning_tokens"] += reasoning
        r["cached_input_tokens"] += cached
        r["llm_calls"] += 1

    def as_dict(self):
        roles = {}
        for name, r in self.per_role.items():
            roles[name] = dict(r)
            roles[name]["cost_estimate"] = round(
                _cost(r["input_tokens"], r["output_tokens"]), 5)
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "llm_calls": self.calls,
            "cost_estimate": round(_cost(self.input_tokens, self.output_tokens), 5),
            "per_model": self.per_model,
            "per_role": roles,
        }


def image_part(path, detail="low"):
    """An image content part. `detail=low` keeps six views affordable."""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = osp.splitext(path)[1].lstrip(".").lower()
    mime = "jpeg" if ext in ("jpg", "jpeg") else "png"
    return {"type": "image_url",
            "image_url": {"url": f"data:image/{mime};base64,{b64}", "detail": detail}}


def text_part(text):
    return {"type": "text", "text": text}


class LLM:
    """Thin, defensive wrapper around chat.completions."""

    def __init__(self, model, usage=None, role=None):
        config.require_key()
        self.model = model
        self.role = role
        self.client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL,
                             timeout=config.LLM_TIMEOUT_S,
                             max_retries=config.LLM_MAX_RETRIES)
        self.usage = usage or Usage()
        # Codex models are only served on the Responses API; chat.completions
        # 404s with "Use the v1/responses endpoint instead".
        self._use_responses = "codex" in model.lower()

    @staticmethod
    def _to_responses_input(messages):
        """chat.completions message list -> Responses API `input` list."""
        def part(p, role):
            if p.get("type") == "text":
                t = "output_text" if role == "assistant" else "input_text"
                return {"type": t, "text": p["text"]}
            if p.get("type") == "image_url":
                iu = p["image_url"]
                return {"type": "input_image", "image_url": iu["url"],
                        "detail": iu.get("detail", "auto")}
            return p
        out = []
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                t = "output_text" if m["role"] == "assistant" else "input_text"
                parts = [{"type": t, "text": c}]
            else:
                parts = [part(p, m["role"]) for p in c]
            out.append({"role": m["role"], "content": parts})
        return out

    def _create(self, messages, json_mode=True):
        if self._use_responses:
            kw = {"model": self.model,
                  "input": self._to_responses_input(messages)}
            if json_mode:
                kw["text"] = {"format": {"type": "json_object"}}
            if config.REASONING_EFFORT:
                kw["reasoning"] = {"effort": config.REASONING_EFFORT}
            try:
                return self.client.responses.create(**kw)
            except Exception as e:
                if "reasoning" in str(e):
                    kw.pop("reasoning", None)
                    return self.client.responses.create(**kw)
                raise
        kw = {"model": self.model, "messages": messages}
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        if config.REASONING_EFFORT:
            kw["reasoning_effort"] = config.REASONING_EFFORT
        try:
            return self.client.chat.completions.create(**kw)
        except TypeError:
            kw.pop("reasoning_effort", None)
            return self.client.chat.completions.create(**kw)
        except Exception as e:
            # Some models reject reasoning_effort with a 400 rather than a TypeError
            if "reasoning_effort" in str(e):
                kw.pop("reasoning_effort", None)
                return self.client.chat.completions.create(**kw)
            raise

    def json(self, system, parts, retries=3):
        """Call the model and return a parsed dict.

        `parts` is a list of content parts (use text_part / image_part).
        On a parse failure the error is fed back and the call retried, rather
        than silently degrading to {}.
        """
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": parts}]
        last_err = None
        for attempt in range(retries):
            resp = self._create(messages)
            # Attributed on EVERY attempt, including the JSON-retry ones below:
            # a role that keeps returning unparseable JSON is paying for it,
            # and hiding those calls would understate exactly the role that
            # needs fixing.
            self.usage.add(resp, self.role)
            if self._use_responses:
                raw = (getattr(resp, "output_text", "") or "").strip()
            else:
                raw = (resp.choices[0].message.content or "").strip()
            if "```" in raw:  # strip accidental fencing
                seg = raw.split("```")
                raw = next((s[4:] if s.startswith("json") else s
                            for s in seg if s.strip()), raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_err = e
                messages.append({"role": "assistant", "content": raw[:4000]})
                messages.append({"role": "user", "content": [text_part(
                    f"That was not valid JSON ({e}). Reply with a single valid "
                    f"JSON object and nothing else.")]})
                time.sleep(0.6)
        raise ValueError(f"model did not return valid JSON after {retries} tries: {last_err}")
