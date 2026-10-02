// Execute the actual browser fetch function with mocked network responses.
// Uses Node built-ins only; no browser, npm install, or real network access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const run = eval('(' + fs.readFileSync(0, 'utf8') + ')');
const origin = 'https://anyrouter.top';
const privateValue = 'server-private-marker';
const args = {
    origin, path: '/api/user/self', method: 'GET', userId: '123',
    marker: 'TEST_ONLY_LOCAL_MARKER', limit: 65536, timeout: 1000,
};
global.location = {origin};

async function responseCase(body, expected, options = {}, overrides = {}) {
    let calls = 0;
    global.fetch = async (path, init) => {
        calls++;
        assert.equal(path, overrides.path || args.path);
        assert.equal(init.credentials, 'same-origin');
        assert.equal(init.mode, 'same-origin');
        assert.equal(init.redirect, 'error');
        assert.equal(init.headers['new-api-user'], '123');
        assert(init.signal instanceof AbortSignal);
        assert.equal(init.body, undefined);
        if (overrides.method === 'POST') {
            assert.equal(init.headers['x-anyrenew-check-in'], args.marker);
        } else {
            assert.equal(init.headers['x-anyrenew-check-in'], undefined);
        }
        return new Response(body, options);
    };
    const result = await run({...args, ...overrides});
    for (const [key, value] of Object.entries(expected)) assert.equal(result[key], value);
    assert.equal(calls, 1);
    assert(!JSON.stringify(result).includes(privateValue));
    assert(!JSON.stringify(result).includes(args.marker));
}

(async () => {
    await responseCase(JSON.stringify({success: true, data: privateValue}), {kind: 'json', success: true});
    await responseCase('{"success":true}', {kind: 'json', success: true},
        {headers: {'content-type': 'text/html'}}, {path: '/api/user/sign_in', method: 'POST'});
    for (const value of [{success: false}, {success: 'true'}, {success: 1}, {}, [], null, true]) {
        await responseCase(JSON.stringify(value), {kind: 'json', success: false});
    }
    await responseCase('<script>' + privateValue + '</script>', {kind: 'script'},
        {headers: {'content-type': 'text/html'}});
    await responseCase('<html>login</html>', {kind: 'html'}, {headers: {'content-type': 'text/html'}});
    await responseCase('not json', {kind: 'invalid_json'});
    await responseCase('x'.repeat(101), {kind: 'too_large'}, {}, {limit: 100});
    await responseCase('{"success":true}', {kind: 'json', content_type: 'other'},
        {headers: {'content-type': privateValue}});
    await responseCase('{"success":false}', {status: 403, success: false}, {status: 403});
    await responseCase(null, {kind: 'invalid_json'}, {status: 204});

    for (const name of ['Error', 'AbortError']) {
        global.fetch = async () => { const error = new Error(privateValue); error.name = name; throw error; };
        const result = await run(args);
        assert.equal(result.kind, name === 'AbortError' ? 'timeout' : 'network_error');
        assert(!JSON.stringify(result).includes(privateValue));
    }
    let called = false;
    global.fetch = async () => { called = true; throw new Error('Unexpected network call'); };
    global.location = {origin: 'https://example.com'};
    assert.equal((await run(args)).kind, 'offsite');
    global.location = {origin};
    assert.equal((await run({...args, path: '/api/admin'})).kind, 'offsite');
    assert.equal(called, false);
    console.log('Browser fetch checks passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
