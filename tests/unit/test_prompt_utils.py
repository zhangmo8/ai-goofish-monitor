import asyncio

from src import prompt_utils


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _RetryableError(Exception):
    def __init__(self, message, status_code=500):
        super().__init__(message)
        self.status_code = status_code


class _VerboseRetryableError(Exception):
    def __init__(self):
        super().__init__("upstream exploded")
        self.status_code = 429
        self.code = "TooManyRequests"
        self.type = "rate_limit_error"
        self.param = "model"
        self.request_id = "req_demo_123"
        self.body = {
            "error": {
                "message": "rate limit reached",
                "type": "rate_limit_error",
                "param": "model",
                "code": "TooManyRequests",
            }
        }


def test_generate_criteria_retries_on_retryable_error(tmp_path, monkeypatch):
    ref_file = tmp_path / "reference.txt"
    ref_file.write_text("参考标准", encoding="utf-8")
    attempts = {"count": 0}

    class _FakeCompletions:
        async def create(self, **_kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise _RetryableError("InternalServiceError")
            return _FakeResponse("生成成功")

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeAIClient:
        def __init__(self):
            self.settings = type(
                "Settings",
                (),
                {"model_name": "demo-model", "enable_thinking": False},
            )()
            self.client = type("Client", (), {"chat": _FakeChat()})()

        def is_available(self):
            return True

        def refresh(self):
            return None

    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr(prompt_utils, "AIClient", _FakeAIClient)
    monkeypatch.setattr(prompt_utils.asyncio, "sleep", _fake_sleep)

    result = asyncio.run(
        prompt_utils.generate_criteria(
            user_description="我要一台成色好的 MacBook",
            reference_file_path=str(ref_file),
        )
    )

    assert result == "生成成功"
    assert attempts["count"] == 2


def test_generate_criteria_raises_clear_error_after_retries(tmp_path, monkeypatch):
    ref_file = tmp_path / "reference.txt"
    ref_file.write_text("参考标准", encoding="utf-8")

    class _FakeCompletions:
        async def create(self, **_kwargs):
            raise _RetryableError("InternalServiceError")

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeAIClient:
        def __init__(self):
            self.settings = type(
                "Settings",
                (),
                {"model_name": "demo-model", "enable_thinking": False},
            )()
            self.client = type("Client", (), {"chat": _FakeChat()})()

        def is_available(self):
            return True

        def refresh(self):
            return None

    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr(prompt_utils, "AIClient", _FakeAIClient)
    monkeypatch.setattr(prompt_utils.asyncio, "sleep", _fake_sleep)

    try:
        asyncio.run(
            prompt_utils.generate_criteria(
                user_description="我要一台成色好的 MacBook",
                reference_file_path=str(ref_file),
            )
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "已重试 3 次仍失败" in str(exc)


def test_generate_criteria_exposes_raw_error_details_after_retries(tmp_path, monkeypatch):
    ref_file = tmp_path / "reference.txt"
    ref_file.write_text("参考标准", encoding="utf-8")

    class _FakeCompletions:
        async def create(self, **_kwargs):
            raise _VerboseRetryableError()

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeAIClient:
        def __init__(self):
            self.settings = type(
                "Settings",
                (),
                {"model_name": "demo-model", "enable_thinking": False},
            )()
            self.client = type("Client", (), {"chat": _FakeChat()})()

        def is_available(self):
            return True

        def refresh(self):
            return None

    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr(prompt_utils, "AIClient", _FakeAIClient)
    monkeypatch.setattr(prompt_utils.asyncio, "sleep", _fake_sleep)

    try:
        asyncio.run(
            prompt_utils.generate_criteria(
                user_description="我要一台成色好的 MacBook",
                reference_file_path=str(ref_file),
            )
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        message = str(exc)
        assert "raw_error=" in message
        assert '"status_code": 429' in message
        assert '"code": "TooManyRequests"' in message
        assert '"type": "rate_limit_error"' in message
        assert '"request_id": "req_demo_123"' in message
