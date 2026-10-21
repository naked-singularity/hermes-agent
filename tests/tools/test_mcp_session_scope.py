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
    from tools import mcp_tool_costs
    from tools.approval_context import set_current_session_key, reset_current_session_key

    script = tmp_path / 'peer.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'test-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[
  {'name':'write_cmd','description':'write tool','inputSchema':{'type':'object','properties':{}}},
  {'name':'paid_cmd','description':'paid tool','inputSchema':{'type':'object','properties':{}}},
  {'name':'costly_cmd','description':'costly tool','inputSchema':{'type':'object','properties':{}}}
 ]}
 elif method=='tools/call': result={'content':[{'type':'text','text':'DONE'}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    scope = 'editor-session:trust_check'
    mcp_tool_costs._spent.clear()
    session_policy_key = (scope, 'same_costed')
    try:
        register_mcp_servers({
            'native_untrusted': {
                'command': sys.executable, 'args': [str(script)],
                'trust': 'untrusted', 'connect_timeout': 5
            },
            'native_full': {
                'command': sys.executable, 'args': [str(script)],
                'trust': 'full', 'connect_timeout': 5
            },
            'native_costed': {
                'command': sys.executable, 'args': [str(script)],
                'trust': 'full',
                'tool_costs': {'paid_cmd': 40},
                'spend_limit': 100,
                'connect_timeout': 5
            },
            'same_costed': {
                'command': sys.executable, 'args': [str(script)],
                'trust': 'full',
                'tool_costs': {'costly_cmd': 50},
                'spend_limit': 100,
                'connect_timeout': 5
            }
        })

        with session_tool_scope(scope):
            register_mcp_servers({
                'session_untrusted': {
                    'command': sys.executable, 'args': [str(script)],
                    'trust': 'untrusted', 'connect_timeout': 5
                },
                'same_costed': {
                    'command': sys.executable, 'args': [str(script)],
                    'trust': 'full',
                    'tool_costs': {'costly_cmd': 25},
                    'spend_limit': 40,
                    'connect_timeout': 5
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

            # 4. Parent budget inheritance and spend limit enforcement in chat-a
            tok_a = set_current_session_key('chat-a')
            try:
                booked = []
                assert _trust_gate_check('native_costed', 'paid_cmd', booked) is None
                assert len(booked) == 1
                assert mcp_tool_costs._spent[('native_costed', 'chat-a')] == 40.0

                assert _trust_gate_check('native_costed', 'paid_cmd', booked) is None
                assert len(booked) == 2
                assert mcp_tool_costs._spent[('native_costed', 'chat-a')] == 80.0

                gate_over = _trust_gate_check('native_costed', 'paid_cmd', booked)
                assert gate_over is not None
                assert 'spend limit is 100 credits' in gate_over or 'was NOT run' in gate_over
                assert mcp_tool_costs._spent[('native_costed', 'chat-a')] == 80.0
            finally:
                reset_current_session_key(tok_a)

            # 5. Session isolation: chat-b has its own independent tally
            tok_b = set_current_session_key('chat-b')
            try:
                booked_b = []
                assert _trust_gate_check('native_costed', 'paid_cmd', booked_b) is None
                assert mcp_tool_costs._spent[('native_costed', 'chat-b')] == 40.0
                assert mcp_tool_costs._spent[('native_costed', 'chat-a')] == 80.0
            finally:
                reset_current_session_key(tok_b)

            # 6. Independent policy for same-named editor MCP server (cost 25, limit 40)
            tok_a = set_current_session_key('chat-a')
            try:
                booked_same = []
                assert _trust_gate_check('same_costed', 'costly_cmd', booked_same) is None
                assert mcp_tool_costs._spent[(session_policy_key, 'chat-a')] == 25.0
                assert mcp_tool_costs._spent.get(('same_costed', 'chat-a'), 0.0) == 0.0

                gate_same_over = _trust_gate_check('same_costed', 'costly_cmd', booked_same)
                assert gate_same_over is not None
                assert 'spend limit is 40 credits' in gate_same_over or 'was NOT run' in gate_same_over
                assert mcp_tool_costs._spent[(session_policy_key, 'chat-a')] == 25.0
            finally:
                reset_current_session_key(tok_a)

        # 7. Outside session scope: parent profile retains its own independent policy & tally
        tok_a = set_current_session_key('chat-a')
        try:
            booked_p = []
            assert _trust_gate_check('same_costed', 'costly_cmd', booked_p) is None
            assert mcp_tool_costs._spent[('same_costed', 'chat-a')] == 50.0
            assert mcp_tool_costs._spent[(session_policy_key, 'chat-a')] == 25.0
        finally:
            reset_current_session_key(tok_a)
    finally:
        shutdown_mcp_servers(scope=scope)
        shutdown_mcp_servers()
        mcp_tool_costs._spent.clear()

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


def test_session_scope_in_routed_profiles_isolation_and_no_leak(tmp_path):
    from agent.secret_scope import _MULTIPLEX_CONTEXT
    from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
    from tools.registry import registry, session_tool_scope
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.mcp_tool import _server_visible_in_scope
    from tools.mcp_tool_scope import _resolve_server_key

    home_a = tmp_path / "profiles" / "a"
    home_b = tmp_path / "profiles" / "b"
    home_a.mkdir(parents=True)
    home_b.mkdir(parents=True)

    script = tmp_path / "peer.py"
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'routed-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'echo tool','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':os.environ.get('ROUTED_MARKER', 'UNKNOWN')}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')

    m_token = _MULTIPLEX_CONTEXT.set(True)
    tok_a = set_hermes_home_override(home_a)
    scope_a = registry.current_scope_key()
    reset_hermes_home_override(tok_a)

    tok_b = set_hermes_home_override(home_b)
    scope_b = registry.current_scope_key()
    reset_hermes_home_override(tok_b)

    try:
        # 1. Register Profile A servers
        tok_a = set_hermes_home_override(home_a)
        try:
            register_mcp_servers({
                'common_srv': {
                    'command': sys.executable, 'args': [str(script)],
                    'env': {'ROUTED_MARKER': 'FROM_PROFILE_A'}, 'connect_timeout': 5
                },
                'only_a_srv': {
                    'command': sys.executable, 'args': [str(script)],
                    'env': {'ROUTED_MARKER': 'ONLY_IN_A'}, 'connect_timeout': 5
                },
                'failing_in_b': {
                    'command': sys.executable, 'args': [str(script)],
                    'env': {'ROUTED_MARKER': 'HEALTHY_IN_A'}, 'connect_timeout': 5
                }
            })
        finally:
            reset_hermes_home_override(tok_a)

        # 2. Register Profile B servers (same common_srv name, different config/marker; failing_in_b fails)
        tok_b = set_hermes_home_override(home_b)
        try:
            register_mcp_servers({
                'common_srv': {
                    'command': sys.executable, 'args': [str(script)],
                    'env': {'ROUTED_MARKER': 'FROM_PROFILE_B'}, 'connect_timeout': 5
                },
                'failing_in_b': {
                    'command': sys.executable, 'args': ['-c', 'import sys; sys.exit(1)'],
                    'connect_timeout': 1
                }
            })
        finally:
            reset_hermes_home_override(tok_b)

        # 3. ACP/editor-session under Profile A gets only Profile A's connection
        tok_a = set_hermes_home_override(home_a)
        try:
            with session_tool_scope('editor-session:client_a'):
                assert _resolve_server_key('common_srv') == (scope_a, 'common_srv')
                assert _server_visible_in_scope((scope_a, 'common_srv'), 'editor-session:client_a') is True
                assert _server_visible_in_scope((scope_b, 'common_srv'), 'editor-session:client_a') is False

                res_a = registry.dispatch('mcp__common_srv__echo', {})
                assert 'FROM_PROFILE_A' in res_a
                assert 'FROM_PROFILE_B' not in res_a
        finally:
            reset_hermes_home_override(tok_a)

        # 4. ACP/editor-session under Profile B gets only Profile B's connection
        tok_b = set_hermes_home_override(home_b)
        try:
            with session_tool_scope('editor-session:client_b'):
                assert _resolve_server_key('common_srv') == (scope_b, 'common_srv')
                assert _server_visible_in_scope((scope_b, 'common_srv'), 'editor-session:client_b') is True
                assert _server_visible_in_scope((scope_a, 'common_srv'), 'editor-session:client_b') is False

                res_b = registry.dispatch('mcp__common_srv__echo', {})
                assert 'FROM_PROFILE_B' in res_b
                assert 'FROM_PROFILE_A' not in res_b

                # 5. Absence of server in Profile B does not leak Profile A's server
                assert _resolve_server_key('only_a_srv') != (scope_a, 'only_a_srv')
                assert _resolve_server_key('only_a_srv') == ('editor-session:client_b', 'only_a_srv')
                assert _server_visible_in_scope((scope_a, 'only_a_srv'), 'editor-session:client_b') is False
                res_missing = registry.dispatch('mcp__only_a_srv__echo', {})
                assert 'ONLY_IN_A' not in res_missing
                assert 'Unknown tool' in res_missing or 'error' in res_missing

                # 6. Connection error in Profile B does not fall back to Profile A's healthy server
                assert _resolve_server_key('failing_in_b') != (scope_a, 'failing_in_b')
                assert _server_visible_in_scope((scope_a, 'failing_in_b'), 'editor-session:client_b') is False
                res_err = registry.dispatch('mcp__failing_in_b__echo', {})
                assert 'HEALTHY_IN_A' not in res_err
                assert 'not connected' in res_err or 'error' in res_err or 'Unknown tool' in res_err

            # 7. Connection error in editor session's own server does not fall back to other or parent profile
            with session_tool_scope('editor-session:client_b_own_err'):
                register_mcp_servers({'common_srv': {
                    'command': sys.executable, 'args': ['-c', 'import sys; sys.exit(1)'],
                    'connect_timeout': 1
                }})
                assert _resolve_server_key('common_srv') == ('editor-session:client_b_own_err', 'common_srv')
                res_own_err = registry.dispatch('mcp__common_srv__echo', {})
                assert 'FROM_PROFILE_A' not in res_own_err
                assert 'FROM_PROFILE_B' not in res_own_err
                assert 'not connected' in res_own_err or 'error' in res_own_err
        finally:
            reset_hermes_home_override(tok_b)
    finally:
        shutdown_mcp_servers(scope='editor-session:client_a')
        shutdown_mcp_servers(scope='editor-session:client_b')
        shutdown_mcp_servers(scope='editor-session:client_b_own_err')
        shutdown_mcp_servers(scope=scope_a)
        shutdown_mcp_servers(scope=scope_b)
        for s in (scope_a, scope_b):
            for t in ('mcp__common_srv__echo', 'mcp__only_a_srv__echo', 'mcp__failing_in_b__echo'):
                registry.deregister(t, scope=s)
        _MULTIPLEX_CONTEXT.reset(m_token)
