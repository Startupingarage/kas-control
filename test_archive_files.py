import hashlib
import unittest
from unittest.mock import Mock

from archive_files import FileTransfer, FileTransferError, collect_source_files, verify_attachment_coverage, verify_message_files, verify_source_files
from test_voice_archive import source


class Response:
    def __init__(self,status=200,data=None,body=b''):
        self.status_code=status;self.data=data;self.content=body
    def json(self):return self.data
    def iter_content(self,chunk_size=65536):
        for i in range(0,len(self.content),chunk_size):yield self.content[i:i+chunk_size]
    def close(self):pass


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        self.cloud=Mock();self.raw_get=Mock();self.raw_put=Mock()
        self.transfer=FileTransfer(self.cloud,self.raw_get,self.raw_put)
        self.body=b'private\x00file\n';self.source={'id':'file_123','name':'example.pdf','mime_type':'application/pdf','size':len(self.body)}

    def test_copy_validates_source_and_destination_bytes(self):
        self.cloud.request.side_effect=[
            Response(data={'download_url':'https://chatgpt.com/backend-api/estuary/content?secret=redacted'}),
            Response(data={'file_id':'file_new','upload_url':'https://store.oaiusercontent.com/upload?signature=private'}),
            Response(data={}),
            Response(data={'download_url':'https://chatgpt.com/backend-api/files/download/destination'}),
        ]
        self.raw_get.side_effect=[Response(body=self.body),Response(body=self.body)]
        sent=[]
        def put(*args,**kwargs):sent.append(kwargs['content'].read());return Response(status=201)
        self.raw_put.side_effect=put
        result=self.transfer.copy(self.source)
        self.assertEqual(result['id'],'file_new')
        self.assertEqual(result['source_id'],'file_123')
        self.assertEqual(result['sha256'],hashlib.sha256(self.body).hexdigest())
        self.assertEqual(sent,[self.body])
        self.assertTrue(self.raw_get.call_args.kwargs['stream'])

    def test_supports_larger_than_old_limit_without_buffering_in_memory(self):
        self.assertGreater(FileTransfer.MAX_FILE_BYTES,25*1024*1024)
        response=Response(body=self.body+b'!')
        response.close=Mock();self.raw_get.return_value=response
        self.cloud.request.return_value=Response(data={'download_url':'https://chatgpt.com/backend-api/estuary/content?x=1'})
        with self.assertRaises(FileTransferError):self.transfer.copy(self.source)
        response.close.assert_called_once();self.raw_put.assert_not_called()

    def test_large_file_is_read_and_uploaded_in_bounded_chunks(self):
        size=26*1024*1024
        class StreamResponse:
            status_code=200
            def iter_content(self,chunk_size=65536):
                remaining=size
                while remaining:
                    block=b'x'*min(chunk_size,remaining);remaining-=len(block);yield block
            def close(self):pass
            @property
            def content(self):raise AssertionError('The entire file was buffered')
        self.cloud.request.side_effect=[Response(data={'download_url':'https://chatgpt.com/backend-api/estuary/content?x=1'}),
            Response(data={'file_id':'file_new','upload_url':'https://store.oaiusercontent.com/upload'}),Response(data={}),
            Response(data={'download_url':'https://chatgpt.com/backend-api/estuary/content?x=2'})]
        self.raw_get.side_effect=[StreamResponse(),StreamResponse()]
        uploaded=[]
        def put(*args,**kwargs):
            digest=hashlib.sha256();total=0
            while block:=kwargs['content'].read(65536):digest.update(block);total+=len(block)
            uploaded.append((total,digest.hexdigest(),kwargs['headers']['Content-Length']))
            return Response(status=201)
        self.raw_put.side_effect=put
        result=self.transfer.copy({**self.source,'size':size})
        self.assertEqual(uploaded,[(size,result['sha256'],str(size))])

    def test_bad_download_or_upload_host_never_exposes_credentials_or_data(self):
        self.cloud.request.return_value=Response(data={'download_url':'https://evil.example/steal'})
        with self.assertRaises(FileTransferError):self.transfer.copy(self.source)
        self.raw_get.assert_not_called();self.raw_put.assert_not_called()
        self.cloud.request.side_effect=[Response(data={'download_url':'https://chatgpt.com/backend-api/files/download/source'}),
            Response(data={'file_id':'file_new','upload_url':'https://oaiusercontent.com.evil.example/a'})]
        self.raw_get.return_value=Response(body=self.body)
        with self.assertRaises(FileTransferError):self.transfer.copy(self.source)
        self.raw_put.assert_not_called()

    def test_mismatch_or_invalid_metadata_never_confirms_copy(self):
        self.cloud.request.side_effect=[Response(data={'download_url':'https://chatgpt.com/backend-api/files/download/source'}),
            Response(data={'file_id':'file_new','upload_url':'https://store.oaiusercontent.com/upload'}),Response(data={}),
            Response(data={'download_url':'https://chatgpt.com/backend-api/files/download/destination'})]
        self.raw_get.side_effect=[Response(body=self.body),Response(body=b'corrupted')]
        self.raw_put.return_value=Response(status=201)
        with self.assertRaises(FileTransferError):self.transfer.copy(self.source)
        for source in ({**self.source,'id':'../secret'},{**self.source,'size':-1},{**self.source,'name':'x\nsecret'}):
            with self.assertRaises(FileTransferError):self.transfer.copy(source)

    def test_source_id_reuse_is_not_a_backup(self):
        self.cloud.request.side_effect=[Response(data={'download_url':'https://chatgpt.com/backend-api/files/download/source'}),
            Response(data={'file_id':'file_123','upload_url':'https://store.oaiusercontent.com/upload'})]
        self.raw_get.return_value=Response(body=self.body)
        with self.assertRaises(FileTransferError):self.transfer.copy(self.source)
        self.raw_put.assert_not_called()

    def test_transport_exceptions_do_not_leak_signed_urls(self):
        self.cloud.request.return_value=Response(data={'download_url':'https://chatgpt.com/backend-api/estuary/content?secret=token'})
        self.raw_get.side_effect=RuntimeError('https://chatgpt.com/backend-api/estuary/content?secret=token')
        with self.assertRaises(FileTransferError) as error:self.transfer.copy(self.source)
        self.assertNotIn('secret',str(error.exception))

    def test_must_verify_bytes_in_the_actual_archived_message(self):
        f={'id':'file_new','name':'example.pdf','mime_type':'application/pdf','size':len(self.body),'sha256':hashlib.sha256(self.body).hexdigest()}
        archive={'current_node':'m','mapping':{'m':{'parent':None,'message':{'author':{'role':'user'},'status':'finished_successfully','metadata':{'attachments':[f]}}}}}
        self.transfer._digest_file=Mock(return_value=hashlib.sha256(self.body).hexdigest())
        verify_message_files(archive,'m',[f],self.transfer)
        self.transfer._digest_file.assert_called_once_with('file_new',len(self.body))
        self.transfer._digest_file.return_value='b'*64
        with self.assertRaises(FileTransferError):verify_message_files(archive,'m',[f],self.transfer)
        archive['mapping']['m']['message']['metadata']['attachments']=[]
        with self.assertRaises(FileTransferError):verify_message_files(archive,'m',[f],self.transfer)
        archive['mapping']['m']['message']['metadata']['attachments']=[f,f]
        with self.assertRaises(FileTransferError):verify_message_files(archive,'m',[f,f],self.transfer)

    def test_source_bytes_must_still_match_before_deletion(self):
        record={'source_id':'file_123','id':'file_new','size':len(self.body),'sha256':hashlib.sha256(self.body).hexdigest()}
        self.transfer._digest_file=Mock(return_value=hashlib.sha256(self.body).hexdigest())
        verify_source_files([record],self.transfer)
        self.transfer._digest_file.assert_called_once_with('file_123',len(self.body))
        self.transfer._digest_file.return_value='b'*64
        with self.assertRaises(FileTransferError):verify_source_files([record],self.transfer)

    def test_recover_reserved_copy_requires_both_bytes_to_match(self):
        self.transfer._digest_file=Mock(side_effect=[hashlib.sha256(self.body).hexdigest()]*2)
        result=self.transfer.recover(self.source,'file_new')
        self.assertEqual(result['sha256'],hashlib.sha256(self.body).hexdigest())
        self.cloud.request.assert_not_called();self.raw_put.assert_not_called()
        self.transfer._digest_file.side_effect=[hashlib.sha256(self.body).hexdigest(),'b'*64]
        with self.assertRaises(FileTransferError):self.transfer.recover(self.source,'file_new')

    def test_inventory_keeps_message_and_attachment_position(self):
        doc=source();m=doc['mapping']['u']['message']
        image={'id':'file_img','name':'pic.jpg','mime_type':'image/jpeg','size':3}
        m['metadata']['attachments']=[image,self.source]
        files=collect_source_files(doc,'source','project')
        self.assertEqual(files,[{'message_id':'u','index':1,**self.source}])

    def test_inventory_rejects_ambiguous_visible_branches_and_bad_metadata(self):
        doc=source();m=doc['mapping']['u']['message'];m['metadata']['attachments']=[self.source]
        doc['mapping']['branch']={'parent':'u','message':doc['mapping']['a']['message']}
        with self.assertRaises(FileTransferError):collect_source_files(doc,'source','project')
        del doc['mapping']['branch'];m['metadata']['attachments']=[{'id':'../bad','name':'x','size':2,'mime_type':'application/pdf'}]
        with self.assertRaises(FileTransferError):collect_source_files(doc,'source','project')

    def test_coverage_requires_each_file_at_original_message_and_index(self):
        source_files=[{'message_id':'u','index':0,**self.source}, {'message_id':'a','index':1,**self.source}]
        copies=[{'message_id':f['message_id'],'index':f['index'],'source_id':f['id'],'id':f'copy_{i}',
                 'name':f['name'],'mime_type':f['mime_type'],'size':f['size'],'sha256':hashlib.sha256(self.body).hexdigest()}
                for i,f in enumerate(source_files)]
        verify_attachment_coverage(source_files,copies)
        with self.assertRaises(FileTransferError):verify_attachment_coverage(source_files,copies[:1])
        with self.assertRaises(FileTransferError):verify_attachment_coverage(source_files,[copies[0],copies[0]])
        copies[0]['id']='file_123'
        with self.assertRaises(FileTransferError):verify_attachment_coverage(source_files,copies)
        copies[0]['id']='copy_0'
        copies[1]['name']='other.pdf'
        with self.assertRaises(FileTransferError):verify_attachment_coverage(source_files,copies)
