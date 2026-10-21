"""Real stdio registrations must not become sibling-session capabilities."""
import concurrent.futures
import sys


def test_session_mcp_registry_isolation_and_scoped_release(tmp_path):
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers

    script = tmp_path / 'peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'owned-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'owned echo','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':os.environ['OWNED_MARKER']}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')
    def connect(scope, name, marker):
        with session_tool_scope(scope):
            register_mcp_servers({name: {'command': sys.executable, 'args': [str(script)],
                'env': {'OWNED_MARKER': marker}, 'connect_timeout': 5}})
            names = [n for n in registry.get_all_tool_names() if n.startswith('mcp__')]
            assert len(names) == 1, names
            return names[0], registry.dispatch(names[0], {})
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a = pool.submit(connect, 'editor-session:a', 'owned_a', 'ONLY_A')
            b = pool.submit(connect, 'editor-session:b', 'owned_b', 'ONLY_B')
            an, av = a.result(timeout=30)
            bn, bv = b.result(timeout=30)
        assert 'ONLY_A' in av and 'ONLY_B' not in av
        assert 'ONLY_B' in bv and 'ONLY_A' not in bv
        assert registry.get_entry(an) is None and registry.get_entry(bn) is None
        shutdown_mcp_servers(scope='editor-session:a')
        with session_tool_scope('editor-session:b'):
            assert registry.get_entry(an) is None
            assert 'ONLY_B' in registry.dispatch(bn, {})
    finally:
        shutdown_mcp_servers(scope='editor-session:a')
        shutdown_mcp_servers(scope='editor-session:b')


def test_session_scope_retains_profile_mcp_servers(tmp_path):
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool import _server_visible_in_scope
    from tools.mcp_tool_scope import _resolve_server_key

    script = tmp_path / 'peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'test-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'test echo','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':os.environ.get('TEST_MARKER', 'OK')}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    try:
        register_mcp_servers({'native_server': {
            'command': sys.executable, 'args': [str(script)],
            'env': {'TEST_MARKER': 'NATIVE_RESPONSE'}, 'connect_timeout': 5
        }})
        native_tool = 'mcp__native_server__echo'
        assert registry.get_entry(native_tool) is not None
        assert 'NATIVE_RESPONSE' in registry.dispatch(native_tool, {})

        with session_tool_scope('editor-session:client1'):
            register_mcp_servers({'editor_server': {
                'command': sys.executable, 'args': [str(script)],
                'env': {'TEST_MARKER': 'EDITOR_RESPONSE'}, 'connect_timeout': 5
            }})
            editor_tool = 'mcp__editor_server__echo'

            assert registry.get_entry(editor_tool) is not None
            assert registry.get_entry(native_tool) is not None

            assert 'EDITOR_RESPONSE' in registry.dispatch(editor_tool, {})
            assert 'NATIVE_RESPONSE' in registry.dispatch(native_tool, {})

            assert _resolve_server_key('native_server') == 'native_server'
            assert _server_visible_in_scope('native_server', 'editor-session:client1')

        with session_tool_scope('editor-session:client2'):
            assert registry.get_entry('mcp__editor_server__echo') is None
            assert 'NATIVE_RESPONSE' in registry.dispatch(native_tool, {})

        shutdown_mcp_servers(scope='editor-session:client1')
        assert registry.get_entry('mcp__editor_server__echo') is None
        assert 'NATIVE_RESPONSE' in registry.dispatch(native_tool, {})
    finally:
        shutdown_mcp_servers(scope='editor-session:client1')
        shutdown_mcp_servers()


def test_session_scope_retains_multiplexed_profile_mcp_servers(tmp_path):
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool import _server_visible_in_scope
    from tools.mcp_tool_scope import _resolve_server_key
    from agent.secret_scope import _MULTIPLEX_CONTEXT

    script = tmp_path / 'multi_peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'multi-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'multi echo','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':os.environ.get('TEST_MARKER', 'MULTI_OK')}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    token = _MULTIPLEX_CONTEXT.set(True)
    try:
        profile_scope = registry.current_scope_key()
        register_mcp_servers({'multi_native': {
            'command': sys.executable, 'args': [str(script)],
            'env': {'TEST_MARKER': 'MULTI_NATIVE_RESPONSE'}, 'connect_timeout': 5
        }})
        native_tool = 'mcp__multi_native__echo'
        assert registry.get_entry(native_tool) is not None
        assert 'MULTI_NATIVE_RESPONSE' in registry.dispatch(native_tool, {})

        with session_tool_scope('editor-session:multi_client'):
            register_mcp_servers({'multi_editor': {
                'command': sys.executable, 'args': [str(script)],
                'env': {'TEST_MARKER': 'MULTI_EDITOR_RESPONSE'}, 'connect_timeout': 5
            }})
            editor_tool = 'mcp__multi_editor__echo'

            assert registry.get_entry(editor_tool) is not None
            assert registry.get_entry(native_tool) is not None

            assert 'MULTI_EDITOR_RESPONSE' in registry.dispatch(editor_tool, {})
            assert 'MULTI_NATIVE_RESPONSE' in registry.dispatch(native_tool, {})

            assert _resolve_server_key('multi_native') == (profile_scope, 'multi_native')
            assert _server_visible_in_scope((profile_scope, 'multi_native'), 'editor-session:multi_client')

        shutdown_mcp_servers(scope='editor-session:multi_client')
        assert registry.get_entry('mcp__multi_editor__echo') is None
        assert 'MULTI_NATIVE_RESPONSE' in registry.dispatch(native_tool, {})
    finally:
        shutdown_mcp_servers(scope='editor-session:multi_client')
        shutdown_mcp_servers(scope=registry.current_scope_key())
        _MULTIPLEX_CONTEXT.reset(token)


def test_session_scope_same_name_server_precedence(tmp_path):
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool_scope import _resolve_server_key, _server_key

    script = tmp_path / 'peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'test-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'test echo','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':os.environ.get('TEST_MARKER', 'OK')}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    scope = 'editor-session:same_name'
    try:
        register_mcp_servers({'same_name': {
            'command': sys.executable, 'args': [str(script)],
            'env': {'TEST_MARKER': 'PROFILE_RESPONSE'}, 'connect_timeout': 5
        }})
        tool_name = 'mcp__same_name__echo'
        assert registry.get_entry(tool_name) is not None
        assert 'PROFILE_RESPONSE' in registry.dispatch(tool_name, {})
        assert _resolve_server_key('same_name') == 'same_name'

        with session_tool_scope(scope):
            register_mcp_servers({'same_name': {
                'command': sys.executable, 'args': [str(script)],
                'env': {'TEST_MARKER': 'SESSION_RESPONSE'}, 'connect_timeout': 5
            }})
            assert registry.get_entry(tool_name) is not None
            assert 'SESSION_RESPONSE' in registry.dispatch(tool_name, {})
            assert _resolve_server_key('same_name') == (scope, 'same_name')

        assert 'PROFILE_RESPONSE' in registry.dispatch(tool_name, {})
        assert _resolve_server_key('same_name') == 'same_name'

        shutdown_mcp_servers(scope=scope)
        assert 'PROFILE_RESPONSE' in registry.dispatch(tool_name, {})
        assert _resolve_server_key('same_name') == 'same_name'
    finally:
        shutdown_mcp_servers(scope=scope)
        shutdown_mcp_servers()


def test_session_scope_trust_and_cost_inheritance_and_override(tmp_path):
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool_handlers import _trust_gate_check

    script = tmp_path / 'peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'test-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'write_cmd','description':'write tool','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':'DONE'}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    scope = 'editor-session:trust_check'
    try:
        register_mcp_servers({
            'native_untrusted': {
                'command': sys.executable, 'args': [str(script)],
                'trust': 'untrusted', 'connect_timeout': 5
            },
            'native_full': {
                'command': sys.executable, 'args': [str(script)],
                'trust': 'full', 'connect_timeout': 5
            }
        })

        with session_tool_scope(scope):
            register_mcp_servers({
                'session_untrusted': {
                    'command': sys.executable, 'args': [str(script)],
                    'trust': 'untrusted', 'connect_timeout': 5
                }
            })
            # 1. Inherited untrusted server from parent profile remains untrusted
            gate_native = _trust_gate_check('native_untrusted', 'write_cmd')
            assert gate_native is not None and ('needs your approval' in gate_native or 'did not approve' in gate_native)

            # 2. Inherited full server proceeds without gate
            assert _trust_gate_check('native_full', 'write_cmd') is None

            # 3. Session own untrusted server is gated
            gate_session = _trust_gate_check('session_untrusted', 'write_cmd')
            assert gate_session is not None and ('needs your approval' in gate_session or 'did not approve' in gate_session)
    finally:
        shutdown_mcp_servers(scope=scope)
        shutdown_mcp_servers()


def test_session_scope_connect_error_does_not_fallback(tmp_path):
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool_scope import _resolve_server_key

    script = tmp_path / 'peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'test-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'test echo','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':'PROFILE_OK'}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    scope = 'editor-session:err_check'
    try:
        register_mcp_servers({'err_server': {
            'command': sys.executable, 'args': [str(script)],
            'connect_timeout': 5
        }})
        tool_name = 'mcp__err_server__echo'
        assert registry.get_entry(tool_name) is not None
        assert 'PROFILE_OK' in registry.dispatch(tool_name, {})

        with session_tool_scope(scope):
            register_mcp_servers({'err_server': {
                'command': sys.executable, 'args': ['-c', 'import sys; sys.exit(1)'],
                'connect_timeout': 1
            }})
            assert _resolve_server_key('err_server') == (scope, 'err_server')
            res = registry.dispatch(tool_name, {})
            assert 'PROFILE_OK' not in res
            assert 'not connected' in res or 'error' in res
    finally:
        shutdown_mcp_servers(scope=scope)
        shutdown_mcp_servers()

