"""Run generated shell transactions against an isolated fake station in WSL."""
import hashlib,json,subprocess,unittest,uuid,shlex
import backend
from test_backend import settings,PROJECT

class ShellTransactionTests(unittest.TestCase):
    def setUp(self):
        self.root='/tmp/kas-control-test-'+uuid.uuid4().hex
        self.old_base,self.old_backups=backend.B,backend.BACKUPS
        backend.B=self.root+'/client';backend.BACKUPS=self.root+'/backups'
        self.doc=settings();self.raw=json.dumps(self.doc)
        self.backup=backend.BACKUPS+'/test'
        fake_cli=f'''#!/bin/sh
B={backend.B}
case "$1" in
 status) phase=$(cat "$B/phase"); printf '{{"agent_connected": true, "phase": "%s", "settings": {{"selected": "local-old"}}}}\\n' "$phase";;
 open) if grep -q 'FAIL_OPEN' "$B/state/settings.json"; then exit 0; fi; echo active > "$B/phase";;
 close) echo idle > "$B/phase";;
esac
'''
        fake_runner=f'''#!/bin/sh
B={backend.B}
echo "$1" >> "$B/actions"
echo idle > "$B/phase"
'''
        self.shell(f'mkdir -p {backend.B}/bin {backend.B}/state; printf %s '+shlex.quote(self.raw)+f' > {backend.B}/state/settings.json; printf %s '+shlex.quote(fake_cli)+f' > {backend.B}/bin/alisa-standalone; printf %s '+shlex.quote(fake_runner)+f' > {backend.B}/run.sh; chmod 700 {backend.B}/run.sh {backend.B}/bin/alisa-standalone; echo retained > {backend.B}/runtime.env; echo active > {backend.B}/phase')

    def tearDown(self):
        # Only this UUID-owned directory is removed; no user or station path.
        self.shell('rm -rf '+shlex.quote(self.root))
        backend.B,backend.BACKUPS=self.old_base,self.old_backups

    def shell(self,script):
        return subprocess.run(['wsl.exe','-d','StationLab','-u','stationuser','--','sh','-s'],input=script.encode(),capture_output=True,timeout=30)

    def transaction(self,title,expected=None):
        doc,_=backend.make_settings(self.doc,PROJECT,None,title)
        restore=backend.restore_script(self.backup,True)
        code=backend.transaction_script(doc,expected or hashlib.sha256(self.raw.encode()).hexdigest(),self.backup,restore)
        # Change only test paths and wait durations; run the production transaction logic.
        return self.shell(code.replace('/tmp/kas-control-switch.lock',self.root+'/lock').replace('sleep .5','sleep .01'))

    def test_success_and_explicit_restore(self):
        p=self.transaction('New');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        after=json.loads(self.shell('cat '+backend.B+'/state/settings.json').stdout)
        self.assertEqual(after['voice'],'orbit');self.assertEqual(len(after['chats']),2)
        p=self.shell('sh '+self.backup+'/restore.sh');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(json.loads(self.shell('cat '+backend.B+'/state/settings.json').stdout),self.doc)

    def test_failed_open_automatically_restores(self):
        p=self.transaction('FAIL_OPEN');self.assertNotEqual(p.returncode,0)
        self.assertIn(b'KAS_ERROR:ROLLED_BACK',p.stdout)
        self.assertEqual(json.loads(self.shell('cat '+backend.B+'/state/settings.json').stdout),self.doc)
        self.assertEqual(self.shell('cat '+backend.B+'/phase').stdout.strip(),b'active')

    def test_stale_settings_does_not_stop(self):
        p=self.transaction('New','0'*64);self.assertNotEqual(p.returncode,0)
        self.assertIn(b'KAS_ERROR:STALE',p.stdout)
        self.assertEqual(self.shell('test ! -e '+backend.B+'/actions').returncode,0)

    def test_shutdown_update_is_preserved_by_rollback(self):
        extra=f'''#!/bin/sh
B={backend.B}
if [ "$1" = stop ] && [ ! -f "$B/changed" ]; then
 sed -i 's/old-parent/final-parent/g' "$B/state/settings.json"
 touch "$B/changed"
fi
echo idle > "$B/phase"
'''
        self.shell('printf %s '+shlex.quote(extra)+' > '+backend.B+'/run.sh')
        p=self.transaction('New');self.assertNotEqual(p.returncode,0)
        actual=json.loads(self.shell('cat '+backend.B+'/state/settings.json').stdout)
        expected=settings();expected['chats'][0]['parent_message_id']='final-parent'
        self.assertEqual(actual,expected)

    def test_killed_transaction_does_not_block_manual_restore(self):
        extra=f'''#!/bin/sh
B={backend.B}
if [ "$1" = stop ] && [ ! -f "$B/interrupted" ]; then
 touch "$B/interrupted"
 kill -STOP "$PPID"
fi
echo idle > "$B/phase"
'''
        self.shell('printf %s '+shlex.quote(extra)+' > '+backend.B+'/run.sh')
        doc,_=backend.make_settings(self.doc,PROJECT,None,'New')
        code=backend.transaction_script(doc,hashlib.sha256(self.raw.encode()).hexdigest(),self.backup,backend.restore_script(self.backup,True))
        code=code.replace('/tmp/kas-control-switch.lock',self.root+'/lock').replace('sleep .5','sleep .01')
        self.shell('printf %s '+shlex.quote(code)+' > '+self.root+'/transaction.sh')
        p=self.shell(f'''sh {self.root}/transaction.sh &
target=$!
n=0
while [ ! -f {backend.B}/interrupted ]; do n=$((n+1)); [ "$n" -lt 100 ] || exit 1; sleep .02; done
sleep .05
kill -KILL "$target"
wait "$target" 2>/dev/null || true
sh {self.backup}/restore.sh
''')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn(b'KAS_RESTORED',p.stdout)

    def test_guarded_restore_preserves_later_switch(self):
        p=self.transaction('New');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        _,_,digest=backend.parse_snapshot(p.stdout)
        later=self.raw.replace('old-parent','later-parent')
        self.shell('printf %s '+shlex.quote(later)+' > '+backend.B+'/state/settings.json')
        code=backend.guarded_restore_script(self.backup,digest).replace('/tmp/kas-control-switch.lock',self.root+'/lock')
        p=self.shell(code)
        self.assertNotEqual(p.returncode,0)
        self.assertIn(b'KAS_ERROR:STALE',p.stdout)
        self.assertEqual(self.shell('cat '+backend.B+'/state/settings.json').stdout.decode(),later)

    def test_same_chat_changed_snapshot_does_not_open(self):
        self.shell('echo idle > '+backend.B+'/phase')
        code=backend.ensure_open_script('local-old','0'*64).replace('/tmp/kas-control-switch.lock',self.root+'/lock')
        p=self.shell(code)
        self.assertNotEqual(p.returncode,0)
        self.assertIn(b'KAS_ERROR:STALE',p.stdout)

    def test_same_chat_wrong_loaded_selection_does_not_open(self):
        self.shell('echo idle > '+backend.B+'/phase')
        code=backend.ensure_open_script('different-chat',hashlib.sha256(self.raw.encode()).hexdigest()).replace('/tmp/kas-control-switch.lock',self.root+'/lock')
        p=self.shell(code)
        self.assertNotEqual(p.returncode,0)
        self.assertIn(b'KAS_ERROR:STALE',p.stdout)
        self.assertEqual(self.shell('cat '+backend.B+'/phase').stdout.strip(),b'idle')

    def test_same_chat_open_captures_locked_result(self):
        self.shell('echo idle > '+backend.B+'/phase')
        code=backend.ensure_open_script('local-old',hashlib.sha256(self.raw.encode()).hexdigest()).replace('/tmp/kas-control-switch.lock',self.root+'/lock')
        p=self.shell(code)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        state,saved,_=backend.parse_snapshot(p.stdout)
        self.assertEqual(state['phase'],'active')
        self.assertEqual(saved['selected'],'local-old')

if __name__=='__main__':unittest.main()
