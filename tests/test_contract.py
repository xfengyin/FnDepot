"""Contract baseline: pins the external behaviour that a rewrite must preserve.

Everything runs offline - the Gemini upstream is patched out.  Covers the HTTP
surface (OpenAI / Google / panel), the tool-call parsers, credential parsing and
the config validators.
"""
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gemini_web2api import account, models, multimodal, server, tools
from gemini_web2api.config import CONFIG, save_config


def fake_generate(text):
    return lambda *a, **k: text


def fake_stream(parts):
    def gen(*a, **k):
        for p in parts:
            yield p
    return gen


class HttpCase(unittest.TestCase):
    """Boots the real threaded server on an ephemeral port."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.instance = server.ThreadedServer(('127.0.0.1', 0), server.GeminiHandler)
        self.thread = threading.Thread(target=self.instance.serve_forever, daemon=True)
        self.thread.start()
        self.cfg = patch.dict(CONFIG, {
            'api_keys': [], 'log_requests': False, 'default_model': 'gemini-auto',
            'proxy': None, 'cookie_file': None, 'temporary_chats': False,
            '_path': os.path.join(self.tmp.name, 'config.json'),
        })
        self.cfg.start()
        self.url = 'http://127.0.0.1:%s' % self.instance.server_port

    def tearDown(self):
        self.cfg.stop()
        self.instance.shutdown()
        self.instance.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def call(self, path, data=None, raw=False):
        headers = {'Content-Type': 'application/json'} if data is not None else {}
        try:
            with urlopen(Request(self.url + path, data=data, headers=headers), timeout=5) as r:
                return r.status, r.read().decode()
        except HTTPError as e:
            return e.code, e.read().decode()

    def post_json(self, path, obj):
        return self.call(path, json.dumps(obj).encode())

    def body(self, path, obj):
        status, text = self.post_json(path, obj)
        return status, json.loads(text)


class OpenAiChatTests(HttpCase):
    def test_models_list(self):
        status, data = self.call('/v1/models')
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload['object'], 'list')
        ids = [m['id'] for m in payload['data']]
        self.assertIn('gemini-auto', ids)
        self.assertTrue(all(m['object'] == 'model' for m in payload['data']))

    def test_non_stream_shape(self):
        with patch.object(server, 'generate', fake_generate('hello world')):
            status, data = self.body('/v1/chat/completions',
                                     {'model': 'gemini-auto', 'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(status, 200)
        self.assertEqual(data['object'], 'chat.completion')
        self.assertEqual(data['choices'][0]['message']['content'], 'hello world')
        self.assertEqual(data['choices'][0]['finish_reason'], 'stop')
        self.assertTrue(data['id'].startswith('chatcmpl-'))
        self.assertEqual(data['usage']['total_tokens'],
                         data['usage']['prompt_tokens'] + data['usage']['completion_tokens'])

    def test_stream_sse_shape(self):
        with patch.object(server, 'generate_stream', fake_stream(['he', 'llo'])):
            status, data = self.body_raw_stream({'model': 'gemini-auto', 'stream': True,
                                                 'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(status, 200)
        self.assertTrue(data.rstrip().endswith('data: [DONE]'))
        chunks = [json.loads(l[6:]) for l in data.splitlines() if l.startswith('data: ') and l != 'data: [DONE]']
        self.assertEqual(chunks[0]['choices'][0]['delta']['role'], 'assistant')
        text = ''.join(c['choices'][0]['delta'].get('content', '') for c in chunks)
        self.assertEqual(text, 'hello')
        self.assertEqual(chunks[-1]['choices'][0]['finish_reason'], 'stop')

    def body_raw_stream(self, obj):
        return self.post_json('/v1/chat/completions', obj)

    def test_empty_prompt_is_400(self):
        status, _ = self.post_json('/v1/chat/completions', {'messages': []})
        self.assertEqual(status, 400)

    def test_invalid_json_is_400(self):
        status, _ = self.call('/v1/chat/completions', b'{not json')
        self.assertEqual(status, 400)

    def test_tool_call_parsing(self):
        text = 'sure\n```tool_call\n{"name": "get_weather", "arguments": {"city": "SF"}}\n```'
        tools_payload = [{'type': 'function', 'function': {'name': 'get_weather', 'parameters': {}}}]
        with patch.object(server, 'generate', fake_generate(text)):
            status, data = self.body('/v1/chat/completions',
                                     {'model': 'gemini-auto', 'tools': tools_payload,
                                      'messages': [{'role': 'user', 'content': 'weather?'}]})
        self.assertEqual(status, 200)
        msg = data['choices'][0]['message']
        self.assertEqual(data['choices'][0]['finish_reason'], 'tool_calls')
        self.assertEqual(msg['tool_calls'][0]['function']['name'], 'get_weather')
        self.assertEqual(json.loads(msg['tool_calls'][0]['function']['arguments']), {'city': 'SF'})
        self.assertEqual(msg['content'], 'sure')

    def test_upstream_failure_is_502(self):
        def boom(*a, **k):
            from gemini_web2api.gemini import UpstreamError
            raise UpstreamError('rejected', 'BardErrorInfo [1060]')
        with patch.object(server, 'generate', boom):
            status, _ = self.post_json('/v1/chat/completions',
                                       {'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(status, 502)


class OpenAiResponsesTests(HttpCase):
    def test_non_stream_shape(self):
        with patch.object(server, 'generate', fake_generate('pong')):
            status, data = self.body('/v1/responses', {'model': 'gemini-auto', 'input': 'ping'})
        self.assertEqual(status, 200)
        self.assertEqual(data['object'], 'response')
        self.assertEqual(data['status'], 'completed')
        self.assertTrue(data['id'].startswith('resp_'))
        self.assertEqual(data['output'][0]['type'], 'message')
        self.assertEqual(data['output'][0]['content'][0]['text'], 'pong')

    def test_stream_event_sequence(self):
        with patch.object(server, 'generate', fake_generate('pong')):
            status, raw = self.post_json('/v1/responses', {'model': 'gemini-auto', 'input': 'ping', 'stream': True})
        self.assertEqual(status, 200)
        events = [json.loads(l[6:]) for l in raw.splitlines() if l.startswith('data: {')]
        types = [e['type'] for e in events]
        self.assertEqual(types[0], 'response.created')
        self.assertIn('response.output_text.delta', types)
        self.assertEqual(types[-1], 'response.completed')
        self.assertEqual([e['sequence_number'] for e in events], list(range(1, len(events) + 1)))

    def test_function_call_output_becomes_tool_message(self):
        captured = {}

        def capture(prompt, *a, **k):
            captured['prompt'] = prompt
            return 'ok'

        with patch.object(server, 'generate', capture):
            self.post_json('/v1/responses', {'input': [
                {'type': 'function_call_output', 'call_id': 'c1', 'name': 'f', 'output': '42'}]})
        self.assertIn('[Tool result for f]: 42', captured['prompt'])


class GoogleApiTests(HttpCase):
    def test_models_list_shape(self):
        status, data = self.call('/v1beta/models')
        self.assertEqual(status, 200)
        payload = json.loads(data)
        names = [m['name'] for m in payload['models']]
        self.assertTrue(all(n.startswith('models/') for n in names))
        self.assertEqual(payload['models'][0]['supportedGenerationMethods'],
                         ['generateContent', 'streamGenerateContent'])

    def test_generate_content(self):
        with patch.object(server, 'generate', fake_generate('hi there')):
            status, data = self.body('/v1beta/models/gemini-auto:generateContent',
                                     {'contents': [{'parts': [{'text': 'hello'}]}]})
        self.assertEqual(status, 200)
        self.assertEqual(data['candidates'][0]['content']['parts'][0]['text'], 'hi there')
        self.assertEqual(data['candidates'][0]['finishReason'], 'STOP')
        self.assertIn('usageMetadata', data)

    def test_stream_generate_ends_with_usage(self):
        with patch.object(server, 'generate_stream', fake_stream(['a', 'b'])):
            status, raw = self.post_json('/v1beta/models/gemini-auto:streamGenerateContent',
                                         {'contents': [{'parts': [{'text': 'hi'}]}]})
        self.assertEqual(status, 200)
        chunks = [json.loads(l[6:]) for l in raw.splitlines() if l.startswith('data: ')]
        self.assertEqual(chunks[0]['candidates'][0]['content']['parts'][0]['text'], 'a')
        self.assertEqual(chunks[-1]['candidates'][0]['finishReason'], 'STOP')
        self.assertIn('usageMetadata', chunks[-1])

    def test_function_call_parsing(self):
        text = '```function_call\n{"name": "f", "args": {"x": 1}}\n```'
        with patch.object(server, 'generate', fake_generate(text)):
            status, data = self.body('/v1beta/models/gemini-auto:generateContent', {
                'contents': [{'parts': [{'text': 'go'}]}],
                'tools': [{'functionDeclarations': [{'name': 'f'}]}],
            })
        self.assertEqual(status, 200)
        part = data['candidates'][0]['content']['parts'][0]
        self.assertEqual(part['functionCall'], {'name': 'f', 'args': {'x': 1}})


class PanelTests(HttpCase):
    def test_overview(self):
        status, data = self.call('/panel/api/overview')
        self.assertEqual(status, 200)
        payload = json.loads(data)
        for key in ('version', 'uptime_sec', 'model_count', 'totals', 'cookie', 'retry'):
            self.assertIn(key, payload)

    def test_models_and_default(self):
        payload = json.loads(self.call('/panel/api/models')[1])
        self.assertEqual(len(payload['models']), len(models.MODELS))

    def test_config_roundtrip(self):
        status, data = self.body('/panel/api/config', {'log_requests': False, 'retry_attempts': 5})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(data['applied']), ['log_requests', 'retry_attempts'])
        self.assertEqual(CONFIG['retry_attempts'], 5)

    def test_config_rejects_bad_values(self):
        for bad in [{'retry_attempts': 0}, {'retry_attempts': 99}, {'port': 'abc'}, {'nope': 1}]:
            status, _ = self.post_json('/panel/api/config', bad)
            self.assertEqual(status, 400, bad)

    def test_logs_and_usage(self):
        self.assertIn('entries', json.loads(self.call('/panel/api/logs')[1]))
        self.assertIn('series', json.loads(self.call('/panel/api/usage?hours=24')[1]))

    def test_unknown_endpoint_404(self):
        self.assertEqual(self.call('/panel/api/nope')[0], 404)

    def test_api_key_enforced_when_configured(self):
        with patch.dict(CONFIG, {'api_keys': ['k1']}):
            self.assertEqual(self.call('/panel/api/overview')[0], 401)
            self.assertEqual(self.call('/v1/models')[0], 401)
            self.assertEqual(self.call('/v1/chat/completions', b'{}')[0], 401)

    def test_query_key_reaches_route(self):
        with patch.dict(CONFIG, {'api_keys': ['k1']}):
            self.assertEqual(self.call('/v1/models?key=k1')[0], 200)


class ParserTests(unittest.TestCase):
    def test_messages_to_prompt_roles_and_images(self):
        prompt, images = tools.messages_to_prompt([
            {'role': 'system', 'content': 'be nice'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'look'},
                                         {'type': 'image_url', 'image_url': {'url': 'http://x/y.png'}}]},
            {'role': 'assistant', 'content': 'ok', 'tool_calls': [
                {'function': {'name': 'f', 'arguments': '{"a":1}'}}]},
            {'role': 'tool', 'name': 'f', 'content': '42'},
        ])
        self.assertIn('[System instruction]: be nice', prompt)
        self.assertIn('[Image attached]', prompt)
        self.assertIn('[Assistant]: ok', prompt)
        self.assertIn('"name": "f"', prompt)
        self.assertIn('[Tool result for f]: 42', prompt)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0][0], 'http://x/y.png')

    def test_data_url_decoding(self):
        import base64
        raw = b'\x89PNG\r\n\x1a\n1234'
        url = 'data:image/png;base64,' + base64.b64encode(raw).decode()
        prompt, images = tools.messages_to_prompt([{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': url}}]}])
        self.assertEqual(images[0][0], raw)

    def test_parse_tool_calls_removes_block_and_keeps_prose(self):
        clean, calls = tools.parse_tool_calls('before\n```tool_call\n{"name": "f", "arguments": {}}\n```\nafter')
        self.assertEqual(clean, 'before\n\nafter')
        self.assertEqual(calls[0]['type'], 'function')
        self.assertEqual(calls[0]['function']['name'], 'f')

    def test_parse_tool_calls_ignores_malformed(self):
        clean, calls = tools.parse_tool_calls('```tool_call\nnot json\n```')
        self.assertEqual(calls, [])

    def test_google_contents_and_function_response(self):
        prompt, images = tools.google_contents_to_prompt({
            'systemInstruction': {'parts': [{'text': 'sys'}]},
            'contents': [
                {'role': 'user', 'parts': [{'text': 'hi'}]},
                {'role': 'model', 'parts': [{'functionCall': {'name': 'f', 'args': {'a': 1}}}]},
                {'role': 'user', 'parts': [{'functionResponse': {'name': 'f', 'response': {'ok': True}}}]},
            ]})
        self.assertIn('sys', prompt)
        self.assertIn('[Assistant]: ', prompt)
        self.assertIn('function_call', prompt)
        self.assertIn('[Tool result for f]:', prompt)
        self.assertEqual(images, [])

    def test_google_inline_data_becomes_image(self):
        import base64
        raw = b'\xff\xd8\xff\xe0jpegdata'
        prompt, images = tools.google_contents_to_prompt({'contents': [
            {'parts': [{'inlineData': {'data': base64.b64encode(raw).decode(), 'mimeType': 'image/jpeg'}}]}]})
        self.assertEqual(images[0][0], raw)
        self.assertEqual(images[0][1], 'image/jpeg')

    def test_parse_google_function_calls_fenced_and_raw(self):
        for text in ['```function_call\n{"name": "f", "args": {"x": 1}}\n```',
                     '{"name": "f", "args": {"x": 1}}']:
            clean, calls = tools.parse_google_function_calls(text)
            self.assertEqual(calls, [{'name': 'f', 'args': {'x': 1}}], text)
            self.assertEqual(clean, '')

    def test_parse_google_function_calls_bare_format(self):
        clean, calls = tools.parse_google_function_calls('function_call\n{"name": "f", "args": {"x": 1}}')
        self.assertEqual(calls, [{'name': 'f', 'args': {'x': 1}}])

    def test_tool_choice_none_suppresses_tool_block(self):
        prompt, _ = tools.messages_to_prompt([{'role': 'user', 'content': 'hi'}],
                                             [{'type': 'function', 'function': {'name': 'f'}}], 'none')
        self.assertNotIn('# Tool Use', prompt)

    def test_tool_choice_required_adds_constraint(self):
        prompt, _ = tools.messages_to_prompt([{'role': 'user', 'content': 'hi'}],
                                             [{'type': 'function', 'function': {'name': 'f'}}], 'required')
        self.assertIn('MUST call at least one tool', prompt)


class ModelAndConfigTests(unittest.TestCase):
    def test_resolve_think_override_and_fallback(self):
        name, mode, think, err, extra = models.resolve_model('gemini-3.1-pro@think=9')
        self.assertEqual((name, mode, think, err), ('gemini-3.1-pro', 3, 9, None))
        self.assertEqual(models.resolve_model('gemini-3.1-pro-enhanced')[4], {31: 2, 80: 3})
        self.assertEqual(models.resolve_model('nope', 'gemini-auto')[0], 'gemini-auto')
        self.assertIsNotNone(models.resolve_model('gemini-auto@think=x')[3])
        self.assertIsNotNone(models.resolve_model('')[3])

    def test_parse_cookie_text_all_forms(self):
        raw = 'SAPISID=abc; __Secure-3PAPISID=def'
        parsed = account.parse_cookie_text(raw)
        self.assertEqual(parsed['sapisid'], 'abc')
        self.assertEqual(sorted(parsed['names']), ['SAPISID', '__Secure-3PAPISID'])
        from_list = account.parse_cookie_text(json.dumps({'cookies': [{'name': 'A', 'value': '1'}]}))
        self.assertEqual(from_list['cookie'], 'A=1')
        with self.assertRaises(ValueError):
            account.parse_cookie_text('   ')
        with self.assertRaises(ValueError):
            account.parse_cookie_text('no-equals-sign')

    def test_detect_image_mime(self):
        cases = [(b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff\xe0', 'image/jpeg'),
                 (b'GIF89a', 'image/gif'), (b'RIFF0000WEBP', 'image/webp'), (b'BM00', 'image/bmp')]
        for data, expected in cases:
            self.assertEqual(multimodal.detect_image_mime(data), expected)
        self.assertEqual(multimodal.detect_image_mime(b'????', 'image/png'), 'image/png')

    def test_save_config_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'config.json')
            with open(path, 'w') as fh:
                json.dump({'port': 8081, 'future_key': 'keep-me'}, fh)
            with patch.dict(CONFIG, {'_path': path}):
                save_config({'retry_attempts': 4})
            written = json.load(open(path))
            self.assertEqual(written['retry_attempts'], 4)
            self.assertEqual(written['future_key'], 'keep-me')

    def test_save_config_restart_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(CONFIG, {'_path': os.path.join(tmp, 'c.json')}):
                applied, restart = save_config({'port': 9090, 'log_requests': True})
        self.assertEqual(restart, ['port'])
        with self.assertRaises(ValueError):
            save_config({'log_requests': 'yes'})


if __name__ == '__main__':
    unittest.main()
