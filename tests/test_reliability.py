import io
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gemini_web2api import gemini, models, server
from gemini_web2api.config import CONFIG


def response_line(text):
    inner = [None] * 5
    inner[4] = [[None, [text]]]
    return json.dumps([["wrb.fr", None, json.dumps(inner), None, None, None]], ensure_ascii=False)


class FakeResponse:
    def __init__(self, body, error=None):
        self.body = body
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_text(self):
        for part in self.body:
            yield part
        if self.error:
            raise self.error


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def stream(self, *args, **kwargs):
        self.calls += 1
        return next(self.responses)


class ResponseParserTests(unittest.TestCase):
    def test_rejected_and_empty_are_errors(self):
        with self.assertRaisesRegex(gemini.UpstreamError, '1060'):
            gemini.extract_response_text('"BardErrorInfo",[1060]')
        with self.assertRaisesRegex(gemini.UpstreamError, 'empty_response'):
            gemini.extract_response_text('no useful response')

    def test_valid_line(self):
        self.assertEqual(gemini.extract_response_text(response_line('hello')), 'hello')

    def test_rejected_does_not_retry(self):
        with patch.object(gemini, '_get_url', return_value='https://example.invalid/'), patch.object(gemini, '_build_headers', return_value={}), patch.object(gemini.urllib.request, 'urlopen', return_value=io.BytesIO(b'"BardErrorInfo",[1060]')) as request, patch.dict(CONFIG, {'proxy': None, 'retry_attempts': 8}):
            with self.assertRaisesRegex(gemini.UpstreamError, '1060'):
                gemini.generate('hello', 1, 4)
        self.assertEqual(request.call_count, 1)

    def test_transport_failure_retries(self):
        raw = response_line('hello').encode()
        with patch.object(gemini, '_get_url', return_value='https://example.invalid/'), patch.object(gemini, '_build_headers', return_value={}), patch.object(gemini.urllib.request, 'urlopen', side_effect=[EOFError(), io.BytesIO(raw)]) as request, patch.object(gemini.time, 'sleep'), patch.dict(CONFIG, {'proxy': None, 'retry_attempts': 2}):
            self.assertEqual(gemini.generate('hello', 1, 4), 'hello')
        self.assertEqual(request.call_count, 2)

    def test_stream_final_line_without_newline(self):
        client = FakeClient([FakeResponse([response_line('hello')])])
        with patch.object(gemini, '_get_httpx_client', return_value=client), patch.dict(CONFIG, {'retry_attempts': 2}):
            self.assertEqual(''.join(gemini.generate_stream('hello', 1, 4)), 'hello')

    def test_stream_retries_before_first_delta(self):
        client = FakeClient([FakeResponse([], EOFError()), FakeResponse([response_line('ok') + '\n'])])
        with patch.object(gemini, '_get_httpx_client', return_value=client), patch.object(gemini.time, 'sleep'), patch.dict(CONFIG, {'retry_attempts': 2}):
            self.assertEqual(''.join(gemini.generate_stream('hello', 1, 4)), 'ok')
        self.assertEqual(client.calls, 2)

    def test_stream_empty_and_interrupted(self):
        client = FakeClient([FakeResponse(['garbage'])])
        with patch.object(gemini, '_get_httpx_client', return_value=client), patch.dict(CONFIG, {'retry_attempts': 2}):
            with self.assertRaisesRegex(gemini.UpstreamError, 'empty_response'):
                list(gemini.generate_stream('hello', 1, 4))
        self.assertEqual(client.calls, 1)
        line = response_line('hello') + '\n'
        client = FakeClient([FakeResponse([line], EOFError()), FakeResponse([line])])
        with patch.object(gemini, '_get_httpx_client', return_value=client), patch.dict(CONFIG, {'retry_attempts': 2}):
            it = gemini.generate_stream('hello', 1, 4)
            self.assertEqual(next(it), 'hello')
            with self.assertRaisesRegex(gemini.UpstreamError, 'connection'):
                list(it)
        self.assertEqual(client.calls, 1)

    def test_default_model_fallback(self):
        self.assertEqual(models.resolve_model('unknown', 'gemini-auto')[0], 'gemini-auto')
        self.assertIsNotNone(models.resolve_model('unknown', 'invalid')[3])
        self.assertIsNotNone(models.resolve_model(None)[3])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.instance = server.ThreadedServer(('127.0.0.1', 0), server.GeminiHandler)
        self.thread = threading.Thread(target=self.instance.serve_forever, daemon=True)
        self.thread.start()
        self.config = patch.dict(CONFIG, {'api_keys': ['secret'], 'log_requests': False, 'default_model': 'gemini-auto'})
        self.config.start()
        self.url = 'http://127.0.0.1:%s' % self.instance.server_port

    def tearDown(self):
        self.config.stop()
        self.instance.shutdown()
        self.instance.server_close()
        self.thread.join()

    def call(self, path, data=None, key=None):
        headers = {'Authorization': 'Bearer secret'} if key else {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        try:
            with urlopen(Request(self.url + path, data=data, headers=headers), timeout=3) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            return error.code, error.read().decode()

    def test_authentication(self):
        for path, data in [('/v1/models', None), ('/v1beta/models', None), ('/panel/api/overview', None), ('/v1/chat/completions', b'{}'), ('/v1beta/models/gemini-auto:generateContent', b'{}')]:
            self.assertEqual(self.call(path, data)[0], 401, path)
            self.assertNotEqual(self.call(path, data, key=True)[0], 401, path)

    def test_rejected_stream_before_headers(self):
        payload = json.dumps({'model': 'gemini-auto', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True}).encode()
        with patch.object(server, 'generate_stream', side_effect=lambda *a, **k: iter(())):
            self.assertEqual(self.call('/v1/chat/completions', payload, key=True)[0], 502)

    def test_stream_error_after_content(self):
        def broken(*args):
            yield 'hi'
            raise gemini.UpstreamError('connection')
        payload = json.dumps({'model': 'gemini-auto', 'messages': [{'role': 'user', 'content': 'hello'}], 'stream': True}).encode()
        with patch.object(server, 'generate_stream', side_effect=broken):
            status, body = self.call('/v1/chat/completions', payload, key=True)
        self.assertEqual(status, 200)
        self.assertIn('"error"', body)
        self.assertNotIn('[DONE]', body)

    def test_query_key_is_redacted(self):
        from gemini_web2api import logbuf
        self.call('/v1/models?key=secret')
        self.assertNotIn('key=secret', '\n'.join(entry['text'] for entry in logbuf.snapshot()))

    def test_body_limit(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.instance.server_port, timeout=3)
        connection.putrequest('POST', '/v1/chat/completions')
        connection.putheader('Authorization', 'Bearer secret')
        connection.putheader('Content-Length', str(server.GeminiHandler.MAX_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()


if __name__ == '__main__':
    unittest.main()
