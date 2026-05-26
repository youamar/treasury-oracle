"""Test fixtures + LLM mocking + isolated test DB. Tests run without hitting Chutes."""
import json
import os
import sys
import tempfile
from pathlib import Path
import pytest

# Make `app` importable from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def stub_fx_http(monkeypatch):
    """Make tests deterministic + offline by returning a fake ECB response.
    The agent's FX-provenance guardrail (agent.py) requires a trusted source
    for strict matches, so we mimic frankfurter.app's success shape here."""
    import httpx
    import app.tools as tools_mod

    class _FakeResp:
        status_code = 200
        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    def fake_get(url, *a, **kw):
        # frankfurter.app/{date}?from=USD&to=MYR -> {"rates": {"MYR": 4.72}, ...}
        # We only need one pair populated; other tests use USD->MYR.
        if "frankfurter.app" in url and "to=" in url:
            to_ccy = url.split("to=")[-1].split("&")[0]
            return _FakeResp({"rates": {to_ccy.upper(): 4.72}})
        return _FakeResp({})

    monkeypatch.setattr(httpx, "get", fake_get)
    # Cached results from previous tests must not leak through.
    tools_mod.get_fx_rate.cache_clear()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file — no state leaks between tests."""
    from app import db as dbmod
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(dbmod, "DB_PATH", test_db)
    monkeypatch.setattr(dbmod, "_initialized", False)
    dbmod.init_db(test_db)
    yield test_db


class FakeChoice:
    def __init__(self, content): self.message = type("M", (), {"content": content})

class FakeResp:
    def __init__(self, content): self.choices = [FakeChoice(content)]


@pytest.fixture(autouse=True)
def mock_chutes(monkeypatch):
    """Stub out Chutes chat for every test — returns JSON-shaped responses."""
    def fake_chat(messages, model, **kwargs):
        user_text = ""
        for m in messages:
            c = m.get("content")
            if isinstance(c, str): user_text += c
            elif isinstance(c, list):
                for part in c:
                    if part.get("type") == "text": user_text += part.get("text", "")

        if "payment-proof OCR specialist" in user_text:
            payload = {
                "amount": 1000.00, "currency": "USD", "date": "2026-05-20",
                "payer": "Acme Corp", "payee": "BrightTech",
                "reference": "INV-2026-001", "description": "Test payment",
            }
        elif "voice note" in user_text or "voice-note" in user_text or "transcript" in user_text.lower():
            payload = {
                "amount": 500, "currency": "USD", "date": "2026-05-25",
                "payer": "Voice Client", "payee": None,
                "reference": "INV-007", "description": "voice extracted",
            }
        elif "high-EQ accounts-receivable" in user_text:
            payload = {"subject": "Friendly reminder",
                       "body": "Dear Client, please settle the small shortfall.",
                       "language": "English"}
        elif "FX-driven shortfall" in user_text:
            payload = {"headline": "FX moved against us",
                       "explanation": "Rates shifted between invoice and payment dates."}
        elif "documentary" in user_text or "voice-over script" in user_text:
            payload = {"title": "The Currents of Currency",
                       "paragraphs": ["Para 1.", "Para 2.", "Para 3."],
                       "tldr_for_boss": "FX moved, books are fine."}
        elif "compliance bot" in user_text:
            # accept if "MISSING" not in the metadata block
            verdict = "reject" if "MISSING" in user_text else "accept"
            payload = {"verdict": verdict, "severity": "high" if verdict=="reject" else "ok",
                       "issues": ["bad currency"] if verdict == "reject" else [],
                       "message_to_sales": "Fix it." if verdict == "reject" else "Looks fine."}
        elif "dunning campaign" in user_text:
            payload = {"subject": "Reminder", "body": "Please pay."}
        else:
            payload = {"ok": True}

        return FakeResp(json.dumps(payload))

    import app.chutes_client as cc
    monkeypatch.setattr(cc, "chat", fake_chat)
    # also patch the re-exported references in the modules that imported it directly
    for mod in ["app.ocr", "app.dunning", "app.voice", "app.validator",
                "app.campaign", "app.documentary"]:
        try:
            m = __import__(mod, fromlist=["chat"])
            if hasattr(m, "chat"): monkeypatch.setattr(m, "chat", fake_chat)
        except Exception:
            pass

    # Patch the OpenAI client used by the agent (tool-calling path).
    # See test_agent.py for the dedicated fixture that overrides per-test.
    import app.agent as ag_mod
    class _StubClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    # Default: immediate finish, no tool call, no decision parsing.
                    class _M:
                        content = '{"decision":"no_match","confidence":0,"reasoning":"stub"}'
                        tool_calls = None
                    class _R: choices = [type("C", (), {"message": _M})]
                    return _R()
    def _fake_get_client(use_fallback=False): return _StubClient()
    monkeypatch.setattr(ag_mod, "get_client", _fake_get_client)

    # The verifier module also calls get_client — mock it too. Default behavior:
    # confirm every strict claim so the verifier doesn't accidentally downgrade
    # legitimate test matches. Tests that want a rejecting verifier override
    # this fixture with their own monkeypatch.
    try:
        import app.verifier as v_mod
        class _VerifierStub:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        class _M:
                            content = '{"verdict":"confirm","concerns":[],"reasoning":"stub"}'
                            tool_calls = None
                        class _R: choices = [type("C", (), {"message": _M})]
                        return _R()
        monkeypatch.setattr(v_mod, "get_client",
                            lambda use_fallback=False: _VerifierStub())
    except Exception:
        pass


@pytest.fixture
def sample_proof():
    return {
        "amount": 1000.0, "currency": "USD", "date": "2026-05-20",
        "payer": "Acme Corp USA", "payee": "BrightTech",
        "reference": "INV-2026-001", "description": "Test",
        "source_file": "proof_01.png",
    }


@pytest.fixture
def sample_txn():
    return {
        "id": "txn_0", "date": "2026-05-20",
        "amount": round(1000 * 4.72 * 0.995, 2),
        "currency": "MYR", "description": "INWARD TT ACME CORP",
        "reference": "INV-2026-001",
    }
