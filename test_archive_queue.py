import tempfile,unittest
from pathlib import Path
from archive_worker import QueueMonitor,NotSubmitted

class QueueTests(unittest.TestCase):
    def test_voice_or_network_pause_preserves_known_content_without_inflating_queue(self):
        for error in (ValueError('Voice is active or its state is not confirmed'),TimeoutError('timeout')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as d:
                q=QueueMonitor(Path(d)/'observations.json')
                q.update('empty',0,error=ValueError('No user transcript to archive'))
                q.update('empty',1,error=error)
                report=q.report([{'conversation_id':'empty'}])
                self.assertEqual((report['remaining_sources'],report['empty_sources']),(0,1))
                q.update('empty',32,state='pending')
                self.assertEqual(q.report([{'conversation_id':'empty'}])['remaining_sources'],1)

    def test_temporary_voice_block_is_retryable_and_attachment_block_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            q=QueueMonitor(Path(d)/'observations.json')
            q.update('voice',10,error=ValueError('Voice is active or its state is not confirmed'))
            q.update('file',10,error=ValueError('Non-text attachments require a separate verified backup'))
            self.assertEqual(q.items['voice']['state'],'voice_busy')
            self.assertEqual(q.items['file']['state'],'attachments')
            self.assertEqual(q.retry_delay(10),15)
            self.assertIsNone(q.retry_delay(200,exclude={'voice'}))

    def test_checked_note_does_not_hide_blocked_items(self):
        with tempfile.TemporaryDirectory() as d:
            q=QueueMonitor(Path(d)/'observations.json')
            q.update('file',10,error=ValueError('Non-text attachments require a separate verified backup'))
            result=q.report([{'conversation_id':'file'}])
            self.assertEqual(result['blocked_sources'],1)
            self.assertEqual(result['queue'][0]['state'],'attachments')

    def test_network_failure_retries_but_uncertain_submission_does_not_blindly_retry(self):
        with tempfile.TemporaryDirectory() as d:
            q=QueueMonitor(Path(d)/'observations.json')
            q.update('offline',10,error=TimeoutError('Connection timed out'))
            self.assertEqual(q.items['offline']['state'],'temporary_error')
            self.assertEqual(q.retry_delay(10),30)
            q.update('uncertain',10,error=ValueError('Submission uncertain; no unique verified copy, no automatic resend'))
            self.assertEqual(q.items['uncertain']['state'],'error')
            self.assertIsNone(q.retry_delay(10,exclude={'offline'}))

    def test_curl_network_failure_is_temporary_without_reclassifying_http_403(self):
        from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
        with tempfile.TemporaryDirectory() as d:
            q=QueueMonitor(Path(d)/'observations.json')
            q.update('offline',10,error=CurlConnectionError('network unavailable'))
            q.update('forbidden',10,error=ValueError('Conversation read HTTP 403'))
            self.assertEqual(q.items['offline']['state'],'temporary_error')
            self.assertEqual(q.items['forbidden']['state'],'error')
    def test_rate_limit_does_not_turn_a_known_empty_chat_into_pending_copy(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'observations.json';q=QueueMonitor(path)
            q.update('empty',0,error=ValueError('No user transcript to archive'))
            q.update('empty',1,error=ValueError('Conversation read HTTP 429'))
            r=q.report([{'conversation_id':'empty'}])
            self.assertEqual((r['remaining_sources'],r['empty_sources'],r['blocked_sources']),(0,1,0))
            q=QueueMonitor(path);self.assertTrue(q.throttled(2))
            q.update('empty',302,state='pending')
            self.assertEqual(q.report([{'conversation_id':'empty'}])['remaining_sources'],1)

    def test_stale_sources_back_off_without_becoming_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            q=QueueMonitor(Path(d)/'observations.json')
            for now in (0,300):q.update('empty',now,error=ValueError('No user transcript to archive'))
            self.assertFalse(q.ready('empty',3899));self.assertTrue(q.ready('empty',3900))
            for now in (0,3600):q.update('missing',now,error=ValueError('Source is missing without a verified deletion receipt'))
            self.assertFalse(q.ready('missing',89999));self.assertTrue(q.ready('missing',90000))
            self.assertEqual(q.items['empty']['state'],'empty')
            self.assertEqual(q.items['missing']['state'],'missing')

    def test_unknown_sources_are_not_starved_by_empty_rechecks(self):
        with tempfile.TemporaryDirectory() as d:
            q=QueueMonitor(Path(d)/'observations.json')
            q.update('empty',0,error=ValueError('No user transcript to archive'))
            items=[('empty',None),('unknown',None),('copy','copied')]
            self.assertEqual(sorted(items,key=lambda x:q.priority(*x)),[('copy','copied'),('unknown',None),('empty',None)])

    def test_counts_separate_empty_missing_and_blocked_without_deleting(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'observations.json';q=QueueMonitor(path)
            q.update('empty',0,error=ValueError('No user transcript to archive'))
            q.update('missing',0,error=ValueError('Source is missing without a verified deletion receipt'))
            q.update('draft',0,error=NotSubmitted('Existing composer draft retained; no overwrite'))
            q.update('real',0,state='pending')
            candidates=[{'conversation_id':x,'gizmo_id':'project'} for x in ('empty','missing','draft','real')]
            r=q.report(candidates)
            self.assertEqual((r['remaining_sources'],r['empty_sources'],r['missing_sources'],r['blocked_sources']),(2,1,1,1))
            restored=QueueMonitor(path)
            self.assertFalse(restored.ready('empty',299));self.assertTrue(restored.ready('empty',300))
            self.assertFalse(restored.ready('missing',3599))
            self.assertEqual(list(Path(d).iterdir()),[path])

    def test_rate_limit_pauses_all_sources_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'observations.json';q=QueueMonitor(path)
            q.update('source',10,error=RuntimeError('Conversation read HTTP 429'))
            q.report([{'conversation_id':'source'}]);q=QueueMonitor(path)
            self.assertTrue(q.throttled(309));self.assertFalse(q.throttled(310))
            self.assertEqual(q.retry_delay(10),300)
            q.update('source',311,state='deleted')
            self.assertEqual(q.report([{'conversation_id':'source'}])['remaining_sources'],0)

    def test_observations_are_pruned_without_modifying_archive_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'observations.json';receipt=Path(d)/'job-a.json';receipt.write_text('receipt')
            q=QueueMonitor(path);q.update('a',0,state='pending');q.report([])
            self.assertEqual(q.items,{});self.assertEqual(receipt.read_text(),'receipt')

if __name__=='__main__':unittest.main()
