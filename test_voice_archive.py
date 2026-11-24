import copy
import unittest

from voice_archive import ArchiveError, make_record, archive_message, verify_archive, verify_closed_source, visible_snapshot


def message(role, parts, kind='text', **extra):
    return dict(author={'role': role}, content={'content_type': kind, 'parts': parts},
                status='finished_successfully', recipient='all', metadata={}, **extra)


def source():
    return {'conversation_id': 'source', 'gizmo_id': 'project', 'current_node': 'a',
            'title': 'Test', 'mapping': {
                'root': {'parent': None, 'message': None},
                'u': {'parent': 'root', 'message': message('user', [
                    {'content_type': 'real_time_user_audio_video_asset_pointer', 'video_frames': []},
                    {'content_type': 'audio_transcription', 'text': 'Все, еще?'}], 'multimodal_text')},
                'a': {'parent': 'u', 'message': message('assistant', ['Да.\nБез сокращений.'], channel='final')}}}


def destination(text):
    return {'conversation_id': 'archive', 'gizmo_id': 'project', 'current_node': 'copy',
            'mapping': {'copy': {'parent': None, 'message': message('user', [text])}}}


class ArchiveTests(unittest.TestCase):
    def test_images_are_explicitly_omitted_and_changed_image_blocks_deletion(self):
        s=source();m=s['mapping']['u']['message']
        m['metadata']['attachments']=[{'id':'file_123','name':'photo.jpg','mime_type':'image/jpeg','size':10}]
        m['content']['parts'].append({'content_type':'image_asset_pointer','asset_pointer':'sediment://file_123','mime_type':'image/jpeg','size_bytes':10})
        record=make_record(s,'source','project')
        self.assertIn('Изображение пропущено',record['messages'][0]['parts'][-1])
        self.assertEqual(make_record(visible_snapshot(s),'source','project'),record)
        archive=destination(archive_message([record]))
        self.assertEqual(verify_archive(record,archive,s),'copy')
        changed=copy.deepcopy(s)
        changed['mapping']['u']['message']['content']['parts'][-1]['asset_pointer']='sediment://file_456'
        with self.assertRaises(ArchiveError):verify_archive(record,archive,changed)

    def test_unknown_or_unmatched_attachment_still_blocks(self):
        s=source();m=s['mapping']['u']['message']
        m['metadata']['attachments']=[{'id':'file_123','mime_type':'application/pdf','size':10}]
        with self.assertRaises(ArchiveError):make_record(s,'source','project')

    def test_image_marker_does_not_replace_missing_audio_transcription(self):
        s=source();m=s['mapping']['u']['message']
        m['metadata']['attachments']=[{'id':'file_123','mime_type':'image/jpeg','size':10}]
        m['content']['parts']=[{'content_type':'real_time_user_audio_video_asset_pointer','video_frames':[]},
            {'content_type':'image_asset_pointer','asset_pointer':'sediment://file_123','mime_type':'image/jpeg','size_bytes':10}]
        with self.assertRaises(ArchiveError):make_record(s,'source','project')
    def test_closed_source_gate(self):
        r=make_record(source(), 'source', 'project')
        identity={'project_session':True,'conversation_id':'source','gizmo_id':'project','id':'old-slot'}
        status={'phase':'idle','native':{'mode':'idle'},'agent_connected':True,'error':''}
        routes={'project_sessions':True,'routes':[{'project_id':'project','chat_id':'next-slot'}]}
        verify_closed_source(r,identity,status,routes)
        for mutation in ['active','disconnected','error','unmanaged','reachable','disabled']:
            i,s,rs=copy.deepcopy((identity,status,routes))
            if mutation=='active':s['phase']='active'
            if mutation=='disconnected':s['agent_connected']=False
            if mutation=='error':s['error']='connection uncertain'
            if mutation=='unmanaged':i['project_session']=False
            if mutation=='reachable':rs['routes'][0]['chat_id']='old-slot'
            if mutation=='disabled':rs['project_sessions']=False
            with self.subTest(mutation=mutation):
                with self.assertRaises(ArchiveError):verify_closed_source(r,i,s,rs)

    def test_roundtrip_verbatim(self):
        s = source()
        s['mapping']['u']['message']['content']['parts'][1]['text'] = 'Еще: ё, е\n  пробелы  '
        record = make_record(s, 'source', 'project')
        self.assertEqual(record['messages'][0]['parts'], ['Еще: ё, е\n  пробелы  '])
        self.assertEqual(verify_archive(record, destination(archive_message([record])), s), 'copy')

    def test_changed_source_or_copy_blocks(self):
        s = source(); record = make_record(s, 'source', 'project')
        d = destination(archive_message([record]))
        changed = copy.deepcopy(s)
        changed['mapping']['a']['message']['content']['parts'][0] += '!'
        for current, target in [(changed, d), (s, destination(archive_message([record])[:-10]))]:
            with self.subTest(current=current['current_node']):
                with self.assertRaises(ArchiveError): verify_archive(record, target, current)

    def test_wrong_project_or_source_is_not_archive(self):
        s = source(); record = make_record(s, 'source', 'project')
        for key, value in [('gizmo_id', 'other'), ('conversation_id', 'source'), ('is_archived', True)]:
            d = destination(archive_message([record])); d[key] = value
            with self.assertRaises(ArchiveError): verify_archive(record, d, s)

    def test_hidden_analysis_never_exported(self):
        s = source()
        s['mapping']['internal'] = {'parent': 'u', 'message': message('assistant', ['secret'], channel='analysis')}
        s['mapping']['a']['parent'] = 'internal'
        record = make_record(s, 'source', 'project')
        self.assertEqual(len(record['messages']), 2)
        self.assertNotIn('secret', archive_message([record]))
        snapshot=visible_snapshot(s)
        self.assertNotIn('secret', str(snapshot))
        self.assertEqual(make_record(snapshot,'source','project'),record)
        self.assertNotIn('asset_pointer',str(snapshot))

    def test_missing_transcript_attachments_branches_or_pending_blocks(self):
        for kind in ['no_text', 'image', 'branch', 'pending', 'broken', 'cycle', 'attachment']:
            s = source()
            if kind == 'no_text': s['mapping']['u']['message']['content']['parts'].pop()
            if kind == 'image': s['mapping']['u']['message']['content']['parts'].append({'content_type': 'image_asset_pointer'})
            if kind == 'branch': s['mapping']['b'] = {'parent': 'u', 'message': message('assistant', ['Other branch'])}
            if kind == 'pending': s['mapping']['a']['message']['status'] = 'in_progress'
            if kind == 'broken': s['mapping']['u']['parent'] = 'missing'
            if kind == 'cycle': s['mapping']['u']['parent'] = 'a'
            if kind == 'attachment': s['mapping']['u']['message']['metadata']['attachments'] = [{'id': 'file'}]
            with self.subTest(kind=kind):
                with self.assertRaises(ArchiveError): make_record(s, 'source', 'project')

    def test_marker_in_assistant_or_unselected_branch_is_not_proof(self):
        s = source(); r = make_record(s, 'source', 'project')
        d = destination(archive_message([r]))
        d['mapping']['copy']['message']['author']['role'] = 'assistant'
        with self.assertRaises(ArchiveError): verify_archive(r, d, s)
        d = destination(archive_message([r]))
        d['mapping']['current'] = {'parent': None, 'message': message('user', ['Not the archive'])}
        d['current_node'] = 'current'
        with self.assertRaises(ArchiveError): verify_archive(r, d, s)


if __name__ == '__main__': unittest.main()
