import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from archive_worker import ArchiveWorker,NotSubmitted,verified_archive_draft
from ordinary_archive import OrdinaryArchive,inventory,payload,digest,batch
from voice_archive import ArchiveError
from archive_worker import QueueMonitor
from test_voice_archive import source,destination,message

class OrdinaryTests(unittest.TestCase):
    def test_listing_cache_never_skips_remaining_chunks(self):
        self.docs['source']['mapping']['u']['message']['content']={'content_type':'text','parts':['x'*95000]}
        self.docs['source']['mapping']['a']['message']['content']['parts']=['y'*95000]
        self.cloud.chat_versions={('project','source'):'v1'}
        with patch('ordinary_archive.projects',return_value={'project':True}):
            first=batch(self.worker,[],{'phase':'idle'},self.root)
            self.assertEqual(first['copied'],1);self.assertEqual(len(self.job()['saved']),1)
            legacy=self.job();legacy['listed_update']='v1'
            (self.root/'ordinary-source.json').write_text(json.dumps(legacy),encoding='utf-8')
            second=batch(self.worker,[],{'phase':'idle'},self.root)
            self.assertEqual(second['copied'],1);self.assertEqual(len(self.job()['saved']),2)
            batch(self.worker,[],{'phase':'idle'},self.root)
            self.cloud.read.reset_mock()
            fourth=batch(self.worker,[],{'phase':'idle'},self.root)
            self.assertEqual(fourth['unchanged_skipped'],1);self.cloud.read.assert_not_called()
        self.cloud.delete.assert_not_called()

    def test_voice_starting_mid_scan_defers_remaining_sources_without_send(self):
        self.cloud.chats.side_effect=None;self.cloud.chats.return_value=['source','next']
        self.cloud.read.side_effect=RuntimeError('Voice is active or its state is not confirmed')
        with patch('ordinary_archive.projects',return_value={'project':True,'other':True}):
            result=batch(self.worker,[],{'phase':'idle'},self.root)
        self.assertEqual(result['deferred'],'voice_active');self.assertEqual(result['retry_after'],15)
        self.cloud.read.assert_called_once_with('source');self.cloud.chats.assert_called_once_with('project')
        self.assertFalse(self.calls);self.cloud.delete.assert_not_called()

    def test_unchanged_listing_skips_history_but_changed_or_unknown_version_reads_it(self):
        self.sync();self.cloud.chat_versions={('project','source'):'v1'}
        with patch('ordinary_archive.projects',return_value={'project':True}):
            batch(self.worker,[],{'phase':'idle'},self.root)
            self.cloud.read.reset_mock()
            result=batch(self.worker,[],{'phase':'idle'},self.root)
            self.assertEqual(result['unchanged_skipped'],1);self.cloud.read.assert_not_called()
            self.cloud.chat_versions[('project','source')]='v2'
            batch(self.worker,[],{'phase':'idle'},self.root);self.cloud.read.assert_called()
            self.cloud.read.reset_mock();self.cloud.chat_versions.clear()
            batch(self.worker,[],{'phase':'idle'},self.root);self.cloud.read.assert_called()

    def test_pending_copy_of_another_project_is_blocked_before_send(self):
        self.ordinary.process('source','project')
        job=self.job();job['pending']['record']['project_id']='g-p-other'
        (self.root/'ordinary-source.json').write_text(json.dumps(job),encoding='utf-8')
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.assertFalse(self.calls);self.cloud.delete.assert_not_called()

    def test_interleaved_projects_keep_separate_reserves_and_checkpoints(self):
        self.docs['second']=copy.deepcopy(self.docs['source'])
        self.docs['second'].update(conversation_id='second',gizmo_id='other-project')
        sent=[]
        def separate_write(project,aid,text):
            target='reserve-'+project;sent.append((project,aid,text))
            self.docs[target]=destination(text);self.docs[target].update(conversation_id=target,gizmo_id=project)
            return target
        self.worker.write=separate_write
        for cid,project in [('source','project'),('second','other-project')]:
            self.assertEqual(self.ordinary.process(cid,project),'prepared')
        for cid,project in [('second','other-project'),('source','project')]:
            self.assertEqual(self.ordinary.process(cid,project),'copied')
            self.assertEqual(self.ordinary.process(cid,project),'unchanged')
        self.assertEqual(self.worker.bindings,{'project':'reserve-project','other-project':'reserve-other-project'})
        for project,_,text in sent:
            self.assertIn('"project_id": "'+project+'"',text)
            self.assertEqual(self.docs['reserve-'+project]['gizmo_id'],project)
        self.cloud.delete.assert_not_called()

    def test_source_from_another_project_is_never_sent(self):
        self.docs['source']['gizmo_id']='g-p-other'
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.assertFalse(self.calls);self.cloud.delete.assert_not_called()

    def test_reserve_from_another_project_blocks_send_before_private_text_leaves(self):
        self.ordinary.process('source','project')
        self.docs['archive']=destination('unrelated');self.docs['archive']['gizmo_id']='g-p-other'
        self.worker.bindings['project']='archive'
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.assertFalse(self.calls);self.cloud.delete.assert_not_called()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.docs={'source':source()};self.calls=[]
        self.cloud=Mock();self.cloud.read.side_effect=lambda cid:copy.deepcopy(self.docs.get(cid))
        self.cloud.chats.side_effect=lambda project:list(self.docs)
        self.worker=ArchiveWorker(self.root,self.cloud,self.write,Mock());self.ordinary=OrdinaryArchive(self.worker)
    def write(self,project,cid,text):
        self.calls.append(text)
        if 'archive' not in self.docs:self.docs['archive']=destination(text)
        else:
            a=self.docs['archive'];key='copy'+str(len(self.calls));a['mapping'][key]={'parent':a['current_node'],'message':message('user',[text])};a['current_node']=key
        return 'archive'
    def sync(self):
        self.assertEqual(self.ordinary.process('source','project'),'prepared')
        self.assertEqual(self.ordinary.process('source','project'),'copied')
    def job(self):return json.loads((self.root/'ordinary-source.json').read_text(encoding='utf-8'))
    def test_first_copy_then_only_new_messages_and_edits_never_deletes(self):
        self.sync();self.assertEqual(len(self.calls),1)
        self.assertEqual(self.ordinary.process('source','project'),'unchanged')
        self.docs['source']['mapping']['next']={'parent':'a','message':message('user',['New topic'])};self.docs['source']['current_node']='next'
        self.sync();self.assertNotIn('Без сокращений',self.calls[-1]);self.assertIn('New topic',self.calls[-1])
        self.docs['source']['mapping']['next']['message']['content']['parts']=['Edited topic']
        self.assertEqual(self.ordinary.process('source','project'),'prepared');rec=self.job()['pending']['record']
        self.assertEqual(rec['messages'][0]['change'],'edit');self.assertTrue(rec['messages'][0]['previous_sha256'])
        self.assertEqual(self.ordinary.process('source','project'),'copied');self.cloud.delete.assert_not_called()
    def test_lost_submission_reply_reconciles_without_resending(self):
        def lost(*args):self.write(*args);raise TimeoutError('reply lost')
        self.worker.write=lost;self.ordinary.process('source','project')
        with self.assertRaises(TimeoutError):self.ordinary.process('source','project')
        self.worker.write=Mock(side_effect=AssertionError('no resend'))
        self.assertEqual(self.ordinary.process('source','project'),'copied');self.assertEqual(len(self.calls),1)
    def test_repeated_edits_back_to_previous_text_have_distinct_receipts(self):
        self.sync()
        for text in ('A','B','A','B'):
            self.docs['source']['mapping']['a']['message']['content']['parts']=[text];self.sync()
        self.assertEqual(len(set(self.calls)),5);self.assertEqual(self.job()['revision'],5)
    def test_uncertain_send_without_copy_blocks_resend_and_checkpoint(self):
        self.worker.write=Mock(side_effect=TimeoutError('unknown'))
        self.ordinary.process('source','project')
        with self.assertRaises(TimeoutError):self.ordinary.process('source','project')
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.assertEqual(self.job()['saved'],{});self.worker.write.assert_called_once()
    def test_confirmed_no_send_can_retry(self):
        self.worker.write=Mock(side_effect=NotSubmitted('no send'));self.ordinary.process('source','project')
        with self.assertRaises(NotSubmitted):self.ordinary.process('source','project')
        self.assertEqual(self.job()['pending']['state'],'prepared')
        self.worker.write=self.write;self.assertEqual(self.ordinary.process('source','project'),'copied')
    def test_source_edit_during_send_is_copied_on_next_pass(self):
        def send(*args):
            aid=self.write(*args);self.docs['source']['mapping']['a']['message']['content']['parts']=['Changed during send'];return aid
        self.worker.write=send;self.sync();self.worker.write=self.write
        self.assertEqual(self.ordinary.process('source','project'),'prepared')
        self.assertEqual([m['id'] for m in self.job()['pending']['record']['messages']],['a'])
    def test_stale_prepared_source_is_not_sent(self):
        self.ordinary.process('source','project');self.docs['source']['mapping']['a']['message']['content']['parts']=['different']
        self.assertEqual(self.ordinary.process('source','project'),'changed');self.assertFalse(self.calls)
    def test_corrupt_destination_does_not_advance_checkpoint(self):
        self.worker.write=lambda *args:'archive';self.docs['archive']=destination('wrong');self.ordinary.process('source','project')
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.assertEqual(self.job()['saved'],{})
    def test_proof_write_failure_is_reconciled(self):
        from ordinary_archive import atomic_json
        self.ordinary.process('source','project')
        def save(path,value):
            if path.name.startswith('ordinary-proof'):raise OSError('disk full')
            atomic_json(path,value)
        with patch('ordinary_archive.atomic_json',side_effect=save):
            with self.assertRaises(OSError):self.ordinary.process('source','project')
        self.assertEqual(self.job()['saved'],{});self.assertEqual(self.ordinary.process('source','project'),'copied');self.assertEqual(len(self.calls),1)
    def test_branches_preserved_and_streaming_or_files_retained(self):
        self.docs['source']['mapping']['alternate']={'parent':'u','message':message('assistant',['Alternative'])}
        self.assertIn('alternate',inventory(self.docs['source'],'source','project'))
        self.docs['source']['mapping']['a']['message']['status']='in_progress'
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.docs['source']['mapping']['a']['message']['status']='finished_successfully'
        self.docs['source']['mapping']['a']['message']['metadata']['attachments']=[{'id':'file','mime_type':'application/pdf','size':10}]
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.cloud.delete.assert_not_called()
    def test_batch_excludes_voice_sources_and_reserved_chats(self):
        with patch('ordinary_archive.projects',return_value={'project':True}):
            result=batch(self.worker,[{'conversation_id':'source'}],{'phase':'idle'},self.root)
        self.assertEqual(result['checked'],0);self.assertFalse(self.calls)
        self.docs['source']['title']='Резервный голосовой чат'
        with patch('ordinary_archive.projects',return_value={'project':True}):batch(self.worker,[],{'phase':'idle'},self.root)
        self.assertFalse(self.calls)
    def test_verified_ordinary_draft_reusable_only_after_commit(self):
        self.ordinary.process('source','project');text=payload(self.job()['pending']['record'])
        self.assertFalse(verified_archive_draft(text,self.root));self.ordinary.process('source','project')
        self.assertTrue(verified_archive_draft(text,self.root));self.assertFalse(verified_archive_draft(text+'x',self.root))

    def test_large_conversation_is_chunked_without_truncating_messages(self):
        self.docs['source']['mapping']['u']['message']['content']={'content_type':'text','parts':['x'*95000]}
        self.docs['source']['mapping']['a']['message']['content']['parts']=['y'*95000]
        self.sync();first=self.job();self.assertEqual(len(first['saved']),1)
        self.sync();self.assertEqual(len(self.job()['saved']),2)
        self.assertEqual(self.ordinary.process('source','project'),'unchanged')
        self.assertTrue(all(len(text)<=180000 for text in self.calls))

    def test_single_oversized_message_is_retained_without_sending(self):
        self.docs['source']['mapping']['u']['message']['content']={'content_type':'text','parts':['x'*200000]}
        with self.assertRaises(ArchiveError):self.ordinary.process('source','project')
        self.assertFalse(self.calls);self.cloud.delete.assert_not_called()

    def test_listing_rate_limit_stops_other_projects_and_survives_next_pass(self):
        self.cloud.chats.side_effect=RuntimeError('Conversation list HTTP 429')
        with patch('ordinary_archive.projects',return_value={'project':True,'other':True}),patch('ordinary_archive.time.time',return_value=100):
            first=batch(self.worker,[],{'phase':'idle'},self.root)
            second=batch(self.worker,[],{'phase':'idle'},self.root)
        self.assertEqual(first['deferred'],'rate_limited');self.assertEqual(second['retry_after'],300)
        self.cloud.chats.assert_called_once_with('project');self.cloud.delete.assert_not_called()

    def test_voice_rate_limit_also_blocks_ordinary_listing(self):
        monitor=QueueMonitor(self.root/'observations.json');monitor.update('voice',100,error=RuntimeError('HTTP 429'))
        with patch('ordinary_archive.projects',return_value={'project':True}),patch('ordinary_archive.time.time',return_value=110):
            result=batch(self.worker,[],{'phase':'idle'},self.root,monitor=monitor)
        self.assertEqual(result['retry_after'],290);self.cloud.chats.assert_not_called()

    def test_bounded_scan_rotates_projects_instead_of_starving_later_projects(self):
        clock=[0];seen=[]
        def chats(project):
            seen.append(project);clock[0]+=2;return ['source']
        self.cloud.chats.side_effect=chats
        with patch('ordinary_archive.projects',return_value={'project':True,'later':True}), \
             patch('ordinary_archive.time.monotonic',side_effect=lambda:clock[0]):
            first=batch(self.worker,[],{'phase':'idle'},self.root,deadline=1)
            second=batch(self.worker,[],{'phase':'idle'},self.root,deadline=3)
        self.assertEqual(seen,['project','later'])
        self.assertEqual(first['deferred'],'batch_limit');self.assertEqual(second['deferred'],'batch_limit')
        self.assertFalse(self.calls);self.cloud.delete.assert_not_called()

    def test_progress_identifies_source_before_send_and_retains_success_before_429(self):
        import copy
        self.docs['second']=copy.deepcopy(self.docs['source']);self.docs['second']['conversation_id']='second'
        events=[];real_read=self.cloud.read.side_effect
        def read(cid):
            if cid=='second':raise RuntimeError('Conversation read HTTP 429')
            return real_read(cid)
        def send(*args):
            self.assertEqual(events[-1]['current'],{'source_id':'source','project_id':'project','state':'send'})
            return self.write(*args)
        self.cloud.read.side_effect=read;self.worker.write=send
        with patch('ordinary_archive.projects',return_value={'project':True}):
            result=batch(self.worker,[],{'phase':'idle'},self.root,progress=lambda value:events.append(copy.deepcopy(value)))
        self.assertEqual(result['copied'],1);self.assertEqual(result['checked'],1)
        self.assertEqual(result['deferred'],'rate_limited');self.assertEqual(result['total'],2)
        self.cloud.delete.assert_not_called()
