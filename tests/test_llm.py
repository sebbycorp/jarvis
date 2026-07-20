"""Router: voice-based backend switching, reply cleanup, error handling."""
import unittest
from unittest import mock

import mocks

mocks.install()

import config  # noqa: E402
import llm  # noqa: E402


class TestParseRoute(unittest.TestCase):
    def test_one_shot_routes_and_strips_the_prefix(self):
        for text, backend, rest in [
            ("ask grok what the weather is", "grok", "what the weather is"),
            ("hey gpt tell me a joke", "openai", "tell me a joke"),
            ("use qwen to summarize this", "local", "to summarize this"),
            ("Grok, why is the sky blue?", "grok", "why is the sky blue?"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(llm.parse_route(text), (backend, rest, False))

    def test_switch_is_permanent_and_consumes_the_text(self):
        for text, backend in [("switch to grok", "grok"),
                              ("change to open ai", "openai"),
                              ("use local from now on", "local")]:
            with self.subTest(text=text):
                self.assertEqual(llm.parse_route(text), (backend, "", True))

    def test_plain_questions_are_not_routed(self):
        for text in ["what time is it", "play some music",
                     "tell me about rockets"]:
            with self.subTest(text=text):
                self.assertEqual(llm.parse_route(text), (None, text, False))

    def test_a_bare_backend_name_is_not_a_question(self):
        # "grok" alone would otherwise route an empty prompt to the gateway
        self.assertEqual(llm.parse_route("grok"), (None, "grok", False))


class TestNormalize(unittest.TestCase):
    def test_strips_markdown_that_would_be_read_aloud(self):
        self.assertEqual(llm.normalize("**bold** and `code`"), "bold and code")
        self.assertEqual(llm.normalize("- one\n- two"), "one\ntwo")

    def test_strips_reasoning_tags(self):
        self.assertEqual(
            llm.normalize("<think>hmm, let me see</think>The answer is four."),
            "The answer is four.")

    def test_strips_code_fences(self):
        self.assertEqual(llm.normalize("```python\nprint(1)\n```"), "print(1)")


class TestRouterAsk(unittest.TestCase):
    def setUp(self):
        self.router = llm.Router("local")

    def _post(self, *responses):
        return mock.patch.object(llm.requests, "post",
                                 side_effect=list(responses))

    def test_uses_the_default_backend_and_its_url(self):
        resp = mocks.FakeResponse(mocks.chat_response("Four."))
        with self._post(resp) as p:
            out = self.router.ask("what is two plus two")
        self.assertEqual(out["reply"], "Four.")
        self.assertEqual(out["backend"], "local")
        self.assertEqual(p.call_args.args[0], config.backend_url("local"))
        self.assertEqual(p.call_args.kwargs["json"]["model"],
                         config.BACKENDS["local"]["model"])

    def test_one_shot_route_does_not_change_the_default(self):
        with self._post(mocks.FakeResponse(mocks.chat_response("Sunny."))) as p:
            out = self.router.ask("ask grok what the weather is")
        self.assertEqual(out["backend"], "grok")
        self.assertEqual(p.call_args.args[0], config.backend_url("grok"))
        self.assertEqual(self.router.default, "local")

    def test_switch_changes_the_default_without_calling_out(self):
        with self._post() as p:
            out = self.router.ask("switch to grok")
        p.assert_not_called()
        self.assertTrue(out["switched"])
        self.assertEqual(self.router.default, "grok")

    def test_falls_back_to_reasoning_when_content_is_empty(self):
        # vLLM/Qwen puts the answer in `reasoning` and truncates `content`
        resp = mocks.FakeResponse(mocks.chat_response("", reasoning="Four."))
        with self._post(resp):
            self.assertEqual(self.router.ask("2+2")["reply"], "Four.")

    def test_empty_reply_raises(self):
        with self._post(mocks.FakeResponse(mocks.chat_response(""))):
            with self.assertRaises(llm.LLMError):
                self.router.ask("hello")

    def test_http_error_raises_with_the_backend_named(self):
        with self._post(mocks.FakeResponse({}, status_code=503, text="down")):
            with self.assertRaisesRegex(llm.LLMError, "local.*503"):
                self.router.ask("hello")

    def test_connection_error_raises(self):
        with mock.patch.object(llm.requests, "post",
                               side_effect=llm.requests.RequestException("boom")):
            with self.assertRaisesRegex(llm.LLMError, "unreachable"):
                self.router.ask("hello")

    def test_history_accumulates_and_is_trimmed(self):
        resp = lambda: mocks.FakeResponse(mocks.chat_response("ok"))  # noqa: E731
        with mock.patch.object(llm.requests, "post", side_effect=lambda *a, **k: resp()):
            for i in range(config.HISTORY_TURNS + 3):
                self.router.ask(f"question {i}")
        self.assertLessEqual(len(self.router._history), config.HISTORY_TURNS * 2)

    def test_reset_clears_history(self):
        with self._post(mocks.FakeResponse(mocks.chat_response("ok"))):
            self.router.ask("remember this")
        self.router.reset()
        self.assertEqual(self.router._history, [])

    def test_remember_false_leaves_history_alone(self):
        with self._post(mocks.FakeResponse(mocks.chat_response("ok"))):
            self.router.ask("one off", remember=False)
        self.assertEqual(self.router._history, [])

    def test_image_attached_only_for_vision_backends(self):
        with self._post(mocks.FakeResponse(mocks.chat_response("A cat."))) as p:
            out = self.router.ask("what do you see", image_jpeg=b"\xff\xd8jpeg",
                                  backend="grok")
        self.assertTrue(out["saw_image"])
        content = p.call_args.kwargs["json"]["messages"][-1]["content"]
        self.assertEqual(content[1]["type"], "image_url")

        with self._post(mocks.FakeResponse(mocks.chat_response("Dunno."))) as p:
            out = self.router.ask("what do you see", image_jpeg=b"\xff\xd8jpeg",
                                  backend="local")
        self.assertFalse(out["saw_image"])  # local Qwen is text-only
        self.assertIsInstance(p.call_args.kwargs["json"]["messages"][-1]["content"], str)

    def test_local_backend_disables_qwen_thinking(self):
        # left on, Qwen spends the whole budget reasoning and returns no content
        with self._post(mocks.FakeResponse(mocks.chat_response("Four."))) as p:
            self.router.ask("2+2", backend="local")
        self.assertEqual(
            p.call_args.kwargs["json"]["chat_template_kwargs"],
            {"enable_thinking": False})

    def test_cloud_backends_send_no_extra_payload(self):
        with self._post(mocks.FakeResponse(mocks.chat_response("Four."))) as p:
            self.router.ask("2+2", backend="grok")
        self.assertNotIn("chat_template_kwargs", p.call_args.kwargs["json"])

    def test_empty_model_is_sent_so_the_gateway_can_pin_one(self):
        with self._post(mocks.FakeResponse(mocks.chat_response("Hi."))) as p:
            self.router.ask("hello", backend="openai")
        self.assertEqual(p.call_args.kwargs["json"]["model"], "")

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            llm.Router("gemini")
        with self.assertRaises(ValueError):
            self.router.set_default("gemini")


if __name__ == "__main__":
    unittest.main()
