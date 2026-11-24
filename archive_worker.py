"""Durable copy/verify/delete queue, independent of voice activation."""
import json
import os
import re
from pathlib import Path
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError, Timeout as CurlTimeout

from backend import validate_id
from voice_archive import ArchiveError, archive_message, make_record, verify_archive, verify_closed_source, visible_snapshot


class NotSubmitted(RuntimeError):
    """The transport confirms that no message submission was attempted."""


class QueueMonitor:
    """Observations do not alter archive jobs or authorize deletion."""
    def __init__(self,path):
        self.path=Path(path)
        self.items=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        for cid,item in self.items.items():
            if item.get('state')=='error':
                detail=item.get('detail','')
                if 'Voice is active or its state is not confirmed' in detail:item['state']='voice_busy'
                elif 'Non-text attachments require a separate verified backup' in detail:item['state']='attachments'
            if item.get('content_state'):continue
            if item.get('state') in ('empty','missing'):item['content_state']=item['state']
            elif item.get('state')=='rate_limited' and not (self.path.parent/('job-'+validate_id(cid)+'.json')).exists():
                kinds=[k for k in ('empty','missing') if item.get('checks',{}).get(k)]
                if len(kinds)==1:item['content_state']=kinds[0]

    def ready(self,cid,now):return now>=self.items.get(cid,{}).get('retry_at',0)

    def throttled(self,now):
        return any(i['state']=='rate_limited' and i.get('retry_at',0)>now for i in self.items.values())

    def retry_delay(self,now,exclude=()):
        times=[i.get('retry_at',now) for cid,i in self.items.items()
               if cid not in exclude and i.get('state') in ('voice_busy','rate_limited','temporary_error')]
        return max(0,min(times)-now) if times else None

    def priority(self,cid,job_state):
        if job_state in ('deleting','copied','submitting','pending'):return 0
        return 1 if self.items.get(cid,{}).get('state','waiting')=='waiting' else 2

    def update(self,cid,now,state=None,error=None):
        previous=self.items.get(cid,{})
        detail=str(error)[:200] if error else ''
        if error:
            if 'Empty session is awaiting final persistence' in detail:state,delay='empty',3600
            elif 'No user transcript to archive' in detail:state,delay='empty',300
            elif 'Source is missing without a verified deletion receipt' in detail:state,delay='missing',3600
            elif '429' in detail:
                from chat_requests import cooldown_remaining
                state,delay='rate_limited',cooldown_remaining(self.path.parent) or 300
            elif 'draft' in detail.lower():state,delay='blocked_draft',120
            elif 'Voice is active or its state is not confirmed' in detail:state,delay='voice_busy',15
            elif 'Non-text attachments require a separate verified backup' in detail:state,delay='attachments',3600
            elif (isinstance(error,(TimeoutError,ConnectionError,CurlConnectionError,CurlTimeout))
                  or re.search(r'\bHTTP 50[0-4]\b',detail)
                  or 'TimeoutError: web client preparation failed' in detail
                  or 'ChatGPT недоступен с ПК.' in detail):state,delay='temporary_error',30
            else:state,delay='error',120
        else:delay=0
        content=previous.get('content_state') if state in ('rate_limited','voice_busy','temporary_error') else state if state in ('empty','missing') else None
        checks=dict(previous.get('checks',{}))
        if state in ('empty','missing'):
            checks[state]=checks.get(state,0)+1
            if checks[state]>1:delay=3600 if state=='empty' else 86400
        self.items[cid]={'state':state,'content_state':content,'checked_at':now,'retry_at':now+delay,'detail':detail,'checks':checks}

    def report(self,candidates):
        self.items={i['conversation_id']:self.items.get(i['conversation_id'],{'state':'waiting'}) for i in candidates}
        atomic_json(self.path,self.items)
        states=[x.get('content_state') or x['state'] for x in self.items.values()]
        return {'remaining_sources':sum(s not in ('empty','missing','deleted') for s in states),
                'empty_sources':states.count('empty'),'missing_sources':states.count('missing'),
                'blocked_sources':sum(s in ('blocked_draft','error','attachments','temporary_error') for s in states),
                'queue':[{'source_id':i['conversation_id'],'title':i.get('title'),'project_id':i.get('gizmo_id'),
                          **self.items[i['conversation_id']]} for i in candidates]}


def atomic_json(path, value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


def verified_archive_draft(text,root):
    """Only a complete known archive payload backed by its snapshot may be replaced."""
    root=Path(root).resolve()
    from ordinary_archive import payload,verify
    for path in root.glob('ordinary-proof-*.json'):
        try:
            receipt=json.loads(path.read_text(encoding='utf-8'))
            if payload(receipt['record'])==text:return True
        except (OSError,ValueError,KeyError,TypeError):continue
    for path in root.glob('job-*.json'):
        try:
            job=json.loads(path.read_text(encoding='utf-8'))
            if job.get('state') not in ('copied','deleting','deleted'):continue
            record=job['record']
            if archive_message([record])!=text:continue
            snapshot=(root/job['snapshot']).resolve()
            if snapshot.parent!=root:continue
            source=json.loads(snapshot.read_text(encoding='utf-8'))
            if make_record(source,record['source_id'],record['project_id'])==record:return True
        except (OSError,ValueError,KeyError,TypeError):continue
    return False


def recoverable_archive_draft(text,root,project):
    """Recognize only complete generated payloads, including the observed HTML decoding.

    This permits replacing a backed-up composer draft, never verifying a cloud copy.
    Pending submission state remains unchanged and still requires reconciliation.
    """
    import html
    from ordinary_archive import payload
    from voice_archive import _block
    root=Path(root).resolve()
    voice=[]
    for path in list(root.glob('ordinary-*.json'))+list(root.glob('job-*.json')):
        try:
            job=json.loads(path.read_text(encoding='utf-8'))
            record=job.get('record') or job.get('pending',{}).get('record')
            if not record or record.get('project_id')!=project:continue
            if path.name.startswith('job-'):
                snapshot=(root/job['snapshot']).resolve()
                if snapshot.parent!=root:continue
                source=json.loads(snapshot.read_text(encoding='utf-8'))
                if make_record(source,record['source_id'],project)!=record:continue
                expected=archive_message([record])
                block=_block(record)
                positions=[text.find(value) for value in (block,html.unescape(block)) if value in text]
                if positions:voice.append((min(positions),record))
            else:
                if 'pending' in job and (job.get('source_id')!=record['source_id'] or job.get('project_id')!=project):continue
                expected=payload(record)
            if text==expected or text==html.unescape(expected):return True
        except (OSError,ValueError,KeyError,TypeError,AttributeError):continue
    if voice:
        expected=archive_message([record for _,record in sorted(voice,key=lambda item:item[0])])
        return text==expected or text==html.unescape(expected)
    return False


class ArchiveWorker:
    def __init__(self,root,cloud,write,closed_state):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.cloud=cloud;self.write=write;self.closed_state=closed_state
        self.bindings_path=self.root/'bindings.json'
        self.bindings=json.loads(self.bindings_path.read_text(encoding='utf-8')) if self.bindings_path.exists() else {}

    def job(self,cid):
        path=self.root/('job-'+validate_id(cid)+'.json')
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def save(self,cid,job):
        atomic_json(self.root/('job-'+validate_id(cid)+'.json'),job)

    def _bind(self,project,cid):
        validate_id(cid)
        if project in self.bindings and self.bindings[project]!=cid:
            raise ArchiveError('Reserved chat identity conflict')
        self.bindings[project]=cid
        atomic_json(self.bindings_path,self.bindings)

    def _closed(self,record):
        identities,status,routes=self.closed_state()
        matches=[i for i in identities if i.get('conversation_id')==record['source_id']]
        if len(matches)!=1:raise ArchiveError('Managed source identity is ambiguous')
        verify_closed_source(record,matches[0],status,routes)

    def _reconcile_copy(self,record,source):
        project=record['project_id']
        candidates=[self.bindings[project]] if project in self.bindings else self.cloud.chats(project)[:20]
        found=[]
        for cid in candidates:
            if cid==record['source_id']:continue
            archive=self.cloud.read(cid)
            if not archive:continue
            try:verify_archive(record,archive,source)
            except ArchiveError:continue
            found.append(cid)
        if len(found)!=1:raise ArchiveError('Submission uncertain; no unique verified copy, no automatic resend')
        return found[0]

    def _pending_batch(self,cid,job,source,now):
        batch=[(cid,job,source)]
        for path in sorted(self.root.glob('job-*.json')):
            try:
                other=json.loads(path.read_text(encoding='utf-8'));record=other['record'];other_id=validate_id(record['source_id'])
                if (other_id==cid or path.name!='job-'+other_id+'.json' or other.get('state')!='pending'
                        or record['project_id']!=job['record']['project_id'] or now-other['stable_since']<30):continue
                snapshot=(self.root/other['snapshot']).resolve()
                if snapshot.parent!=self.root.resolve():continue
                saved=json.loads(snapshot.read_text(encoding='utf-8'))
                if make_record(saved,other_id,record['project_id'])!=record:continue
                self._closed(record)
                if len(archive_message([j['record'] for _,j,_ in batch]+[record]))>200000:continue
                batch.append((other_id,other,saved))
            except (OSError,ValueError,KeyError,TypeError):continue
        return batch

    def process(self,identity,now):
        archive=None
        cid=validate_id(identity.get('conversation_id'))
        project=validate_id(identity.get('gizmo_id'))
        job=self.job(cid)
        if job.get('state')=='deleted':return 'deleted'
        self._closed({'source_id':cid,'project_id':project})
        copied_at_entry=job.get('state')=='copied'
        if copied_at_entry:archive=self.cloud.read(job['archive_id'])
        source=self.cloud.read(cid)
        if source is None:
            if job.get('state')!='deleting':raise ArchiveError('Source is missing without a verified deletion receipt')
            snapshot=json.loads((self.root/job['snapshot']).read_text(encoding='utf-8'))
            proof=self.root/('copy-proof-'+cid+'-'+job['record']['sha256']+'.json')
            # This receipt was flushed before DELETE; recovery need not refetch a large reserve.
            archive=json.loads(proof.read_text(encoding='utf-8')) if proof.exists() else self.cloud.read(job['archive_id'])
            if verify_archive(job['record'],archive,snapshot)!=job.get('archive_message_id'):
                raise ArchiveError('Deletion receipt does not match the verified copy')
            job['state']='deleted';job['deleted_at']=now;self.save(cid,job)
            return 'deleted'
        record=make_record(source,cid,project)
        if record.get('empty_session'):
            self._closed(record)
            if now-record['empty_session']['update_time']<86400:
                raise ArchiveError('Empty session is awaiting final persistence; retain source')
        if job.get('state')=='deleting':raise ArchiveError('Previous deletion remains unconfirmed; no repeat')
        if job.get('record',{}).get('sha256')!=record['sha256']:
            if job.get('state')=='submitting':
                old_source=json.loads((self.root/job['snapshot']).read_text(encoding='utf-8'))
                archive_id=self._reconcile_copy(job['record'],old_source)
                self._bind(project,archive_id)
            snapshot='source-'+cid+'-'+record['sha256']+'.json'
            atomic_json(self.root/snapshot,visible_snapshot(source))
            job={'state':'pending','record':record,'snapshot':snapshot,'stable_since':now}
            self.save(cid,job)
            return 'pending'
        if job['state']=='pending':
            if now-job['stable_since']<30:return 'pending'
            batch=self._pending_batch(cid,job,source,now)
            bound=self.bindings.get(project)
            if bound:
                destination=self.cloud.read(bound)
                if (not destination or bound==cid or destination.get('gizmo_id')!=project
                        or destination.get('is_archived')):
                    raise ArchiveError('Reserved chat moved, hidden or missing; no transcript sent')
            # One submission only. A crash here is reconciled from cloud history.
            for member_id,member,_ in batch:
                member['state']='submitting';self.save(member_id,member)
            try:
                archive_id=self.write(project,self.bindings.get(project),archive_message([j['record'] for _,j,_ in batch]))
            except NotSubmitted:
                for member_id,member,_ in batch:
                    member['state']='pending';self.save(member_id,member)
                raise
            archive=getattr(self.write,'confirmed_archive',None)
            if not isinstance(archive,dict) or archive.get('conversation_id')!=archive_id:
                archive=self.cloud.read(archive_id)
            # Establish the accepted copy first; the fresh source check below still gates deletion.
            for _,member,saved in batch:verify_archive(member['record'],archive,saved)
            self._bind(project,archive_id)
            for member_id,member,_ in batch:
                member.update(state='copied',archive_id=archive_id);self.save(member_id,member)
        elif job['state']=='submitting':
            archive_id=self._reconcile_copy(record,source)
            self._bind(project,archive_id)
            job.update(state='copied',archive_id=archive_id);self.save(cid,job)
        if job['state']!='copied':raise ArchiveError('Unknown archive job state')
        if archive is None:archive=self.cloud.read(job['archive_id'])
        # On resume the source was just read after the archive; no write happened since.
        fresh=source if copied_at_entry else self.cloud.read(cid)
        if fresh is None:raise ArchiveError('Source disappeared before deletion')
        proof=verify_archive(record,archive,fresh)
        self.cloud.rename(job['archive_id'],'Резервный голосовой чат')
        self._closed(record)
        atomic_json(self.root/('copy-proof-'+cid+'-'+record['sha256']+'.json'),visible_snapshot(archive))
        job.update(state='deleting',archive_message_id=proof,verified_at=now)
        self.save(cid,job)
        self.cloud.delete(cid)
        if self.cloud.read(cid) is not None or cid in self.cloud.chats(project):
            raise ArchiveError('Deletion has not been confirmed')
        job.update(state='deleted',deleted_at=now);self.save(cid,job)
        return 'deleted'
