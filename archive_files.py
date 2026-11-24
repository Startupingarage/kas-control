"""Verified attachment transfer; never authorizes conversation deletion."""
import hashlib
import re
import tempfile
from urllib.parse import urlsplit
from voice_archive import _branch, _visible

MAX_FILE_BYTES=512*1024*1024
OMITTED_IMAGES={'image/jpeg','image/png','image/webp','image/gif'}


class FileTransferError(ValueError):
    pass


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        raise FileTransferError('Invalid file identity')
    return value


def _call(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except Exception:raise FileTransferError('File service request failed') from None


def _url(value,upload=False):
    try:
        p=urlsplit(value)
        host=p.hostname or ''
        allowed=(host.endswith('.oaiusercontent.com') if upload else host=='chatgpt.com' and
                 (p.path=='/backend-api/estuary/content' or p.path.startswith('/backend-api/files/')))
        if p.scheme!='https' or not allowed or p.username or p.password or p.port not in (None,443):raise ValueError()
    except (TypeError,ValueError):raise FileTransferError('Unexpected file service address') from None
    return value


def _metadata(source):
    try:
        file_id=_id(source['id']);name=source['name'];mime=source['mime_type'];size=source['size']
        if (not isinstance(name,str) or not name or len(name)>255 or any(ord(c)<32 for c in name)
                or not isinstance(mime,str) or not re.fullmatch(r'[A-Za-z0-9.+_-]+/[A-Za-z0-9.+_-]+',mime)
                or type(size) is not int or not 0<=size<=MAX_FILE_BYTES):raise ValueError()
    except (KeyError,TypeError,ValueError):raise FileTransferError('Invalid or unsupported file metadata') from None
    return {'id':file_id,'name':name,'mime_type':mime,'size':size}


def collect_source_files(conversation,source_id,project_id):
    if conversation.get('conversation_id')!=source_id or conversation.get('gizmo_id')!=project_id:
        raise FileTransferError('Source identity or project mismatch')
    branch=_branch(conversation);selected={key for key,_ in branch}
    if any(key not in selected and _visible(node.get('message')) for key,node in conversation['mapping'].items()):
        raise FileTransferError('Visible branches require full export')
    result=[]
    for key,node in branch:
        message=node.get('message')
        if not _visible(message):continue
        if message.get('status')!='finished_successfully':raise FileTransferError('Source message is not final')
        attachments=message.get('metadata',{}).get('attachments') or []
        if not isinstance(attachments,list):raise FileTransferError('Invalid source attachment list')
        for index,attachment in enumerate(attachments):
            if not isinstance(attachment,dict):raise FileTransferError('Invalid source attachment')
            if attachment.get('mime_type') in OMITTED_IMAGES:continue
            result.append({'message_id':key,'index':index,**_metadata(attachment)})
    return result


def verify_attachment_coverage(source_files,copies):
    if not source_files or len(source_files)!=len(copies):raise FileTransferError('Source attachment count mismatch')
    matched=set();uploaded=set();source_ids={_id(item.get('id')) for item in source_files}
    for source in source_files:
        key=(source.get('message_id'),source.get('index'))
        if key in matched:raise FileTransferError('Duplicate source position')
        matched.add(key)
        found=[c for c in copies if (c.get('message_id'),c.get('index'))==key]
        if len(found)!=1:raise FileTransferError('Missing or duplicate source attachment')
        target=found[0];new_id=_id(target.get('id'))
        if (new_id in uploaded or new_id in source_ids or target.get('source_id')!=source.get('id')
                or any(target.get(k)!=source.get(k) for k in ('name','mime_type','size'))
                or not re.fullmatch(r'[0-9a-f]{64}',target.get('sha256',''))):
            raise FileTransferError('Source attachment proof mismatch')
        uploaded.add(new_id)


class FileTransfer:
    # shortcut: files above 512 MiB retain their source, upgrade when larger uploads are supported.
    MAX_FILE_BYTES=MAX_FILE_BYTES
    def __init__(self,cloud,download,upload):
        self.cloud=cloud;self.download=download;self.upload=upload

    def _digest_file(self,file_id,expected_size,sink=None):
        response=_call(self.cloud.request,'get','/files/'+_id(file_id)+'/download')
        if response.status_code!=200:raise FileTransferError('File download authorization failed')
        url=_url(_call(response.json).get('download_url'))
        response=_call(self.download,url,timeout=(10,300),allow_redirects=False,stream=True)
        try:
            if response.status_code!=200:raise FileTransferError('File download status mismatch')
            digest=hashlib.sha256();count=0
            for chunk in response.iter_content(chunk_size=65536):
                count+=len(chunk)
                if count>expected_size:raise FileTransferError('File download exceeds expected size')
                digest.update(chunk)
                if sink is not None:sink.write(chunk)
            if count!=expected_size:raise FileTransferError('File download size mismatch')
            return digest.hexdigest()
        except FileTransferError:raise
        except Exception:raise FileTransferError('File stream failed') from None
        finally:response.close()

    def copy(self,source,on_reserved=None):
        item=_metadata(source);file_id=item['id'];name=item['name'];mime=item['mime_type'];size=item['size']
        with tempfile.TemporaryFile() as original:
            digest=self._digest_file(file_id,size,original)
            response=_call(self.cloud.request,'post','/files',json={'file_name':name,'file_size':size,'use_case':'multimodal','timezone_offset_min':0,'reset_rate_limits':False})
            if response.status_code!=200:raise FileTransferError('File upload initialization failed')
            data=_call(response.json);new_id=_id(data.get('file_id'));url=_url(data.get('upload_url'),upload=True)
            if new_id==file_id:raise FileTransferError('Upload did not create an independent file')
            if on_reserved:on_reserved(new_id)
            original.seek(0)
            response=_call(self.upload,url,content=original,headers={'Content-Type':mime,'Content-Length':str(size),'x-ms-blob-type':'BlockBlob'},timeout=120,allow_redirects=False)
            if response.status_code not in (200,201,204):raise FileTransferError('File upload failed')
            response=_call(self.cloud.request,'post','/files/'+new_id+'/uploaded',json={})
            if response.status_code!=200:raise FileTransferError('File upload finalization failed')
            if self._digest_file(new_id,size)!=digest:raise FileTransferError('Uploaded file checksum mismatch')
        return {'source_id':file_id,'id':new_id,'name':name,'mime_type':mime,'size':size,'sha256':digest}

    def recover(self,source,new_id):
        item=_metadata(source);new_id=_id(new_id)
        if new_id==item['id']:raise FileTransferError('Upload did not create an independent file')
        digest=self._digest_file(item['id'],item['size'])
        if self._digest_file(new_id,item['size'])!=digest:raise FileTransferError('Reserved copy is not confirmed')
        return {'source_id':item['id'],'id':new_id,'name':item['name'],'mime_type':item['mime_type'],'size':item['size'],'sha256':digest}


def verify_message_files(archive,message_id,copies,transfer):
    nodes=[node for key,node in _branch(archive) if key==message_id]
    if len(nodes)!=1 or not copies:raise FileTransferError('Archive message not found')
    message=nodes[0].get('message') or {}
    if (message.get('author',{}).get('role')!='user' or message.get('status')!='finished_successfully'
            or message.get('metadata',{}).get('is_visually_hidden_from_conversation')):
        raise FileTransferError('Archive message is not visible and final')
    actual=message.get('metadata',{}).get('attachments')
    if not isinstance(actual,list) or len(actual)!=len(copies):raise FileTransferError('Archive attachment count mismatch')
    ids=[_id(item.get('id')) for item in copies]
    if len(set(ids))!=len(ids):raise FileTransferError('Duplicate archive attachment')
    for expected in copies:
        ident=_id(expected.get('id'))
        found=[a for a in actual if isinstance(a,dict) and a.get('id')==ident]
        if (len(found)!=1 or any(found[0].get(k)!=expected.get(k) for k in ('name','mime_type','size'))
                or not re.fullmatch(r'[0-9a-f]{64}',expected.get('sha256',''))):
            raise FileTransferError('Archive attachment metadata mismatch')
        if transfer._digest_file(ident,expected['size'])!=expected['sha256']:
            raise FileTransferError('Archive attachment checksum mismatch')


def verify_source_files(copies,transfer):
    if not copies:raise FileTransferError('No source files to verify')
    for expected in copies:
        source_id=_id(expected.get('source_id'));target_id=_id(expected.get('id'))
        if (source_id==target_id or type(expected.get('size')) is not int
                or not 0<=expected['size']<=transfer.MAX_FILE_BYTES
                or not re.fullmatch(r'[0-9a-f]{64}',expected.get('sha256',''))):
            raise FileTransferError('Invalid source file proof')
        if transfer._digest_file(source_id,expected['size'])!=expected['sha256']:
            raise FileTransferError('Source attachment changed')
