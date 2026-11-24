import {VoicePicker} from './VoicePicker';
import {ArchivePanel} from './ArchivePanel';
import {useState,useRef,useEffect} from 'react';
import {call,type Overview,type Chat,type Project} from './api';
import {Dialog} from '@base-ui/react/dialog';
import {RefreshCw,Folder,AudioLines,Plus,Square,X,ArrowUpRight,Search,Check,ChevronDown,Archive,MessageSquare,History,LogIn,Mic} from 'lucide-react';
import droidLogo from '../../assets/kas-control.png';
type ArchiveState=NonNullable<Overview['project_status']>['archive'];
type View='archive'|'conversation'|'history';

export function ProjectPanel({initial}:{initial:Overview}){
 const [state,setState]=useState(initial),[project,setProject]=useState(initial.current.project_id||initial.projects[0]?.id||'');
 const [phrase,setPhrase]=useState(initial.routes.find(r=>r.project_id===project)?.phrase||'');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[stale,setStale]=useState(false);
 const [view,setView]=useState<View>('conversation'),[projectPicker,setProjectPicker]=useState(false),[query,setQuery]=useState(''),[name,setName]=useState('');
 const [projectQuery,setProjectQuery]=useState('');
 const [catalogBusy,setCatalogBusy]=useState(false),[catalogError,setCatalogError]=useState('');
 const [diagnosticPath,setDiagnosticPath]=useState('');
 const [history,setHistory]=useState<Chat[]|null>(null);const lock=useRef(false),mainRef=useRef<HTMLElement>(null);
 const selected=state.projects.find(p=>p.id===project),route=state.routes.find(r=>r.project_id===project),status=state.project_status;
 const ready=!!route&&state.router_ready&&!stale&&!busy;
 const activeProject=state.projects.find(p=>p.id===state.current.project_id)?.name;
 const stageTitle=state.current.phase==='active'?'Кас разговаривает':stale?'Обновите состояние перед запуском':!state.router_ready?'Колонка пока недоступна':!route?'Добавьте команду для запуска':'Готов к разговору';
 useEffect(()=>{mainRef.current?.scrollTo({top:0});},[view,project]);
 async function loadCatalog(){
  setCatalogBusy(true);setCatalogError('');
  try{const projects=await call<Project[]|null>('cloud_projects');if(projects)setState(s=>({...s,projects:[...projects,...s.projects.filter(p=>!projects.some(x=>x.id===p.id)&&s.routes.some(r=>r.project_id===p.id))]}));}
  catch(e){setCatalogError(e instanceof Error?e.message:String(e));}
  finally{setCatalogBusy(false);}
 }
 useEffect(()=>{void loadCatalog();},[]);
 const updateArchive=(archive:ArchiveState)=>setState(s=>({...s,project_status:s.project_status?{...s.project_status,archive}:s.project_status}));
 useEffect(()=>{
  let active=true;
  const timer=setInterval(()=>{void call<ArchiveState>('archive_status').then(a=>{if(active)updateArchive(a);}).catch(()=>{});},status?.archive.running?2000:12000);
  return()=>{active=false;clearInterval(timer);};
 },[status?.archive.running]);
 useEffect(()=>{
  const keys=(event:KeyboardEvent)=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!lock.current){event.preventDefault();setProjectPicker(true);}};
  document.addEventListener('keydown',keys);return()=>document.removeEventListener('keydown',keys);
 },[]);
 async function refresh(desired=project){
  try{const next=await call<Overview>('bootstrap_local');setState(next);const target=next.projects.some(p=>p.id===desired)?desired:next.projects[0]?.id||'';setProject(target);setPhrase(next.routes.find(r=>r.project_id===target)?.phrase||'');setStale(false);}
  catch(e){setStale(true);throw e;}
 }
 async function run(job:()=>Promise<unknown>,reload=true){
  if(lock.current)return;lock.current=true;setBusy(true);setError('');
  try{await job();if(reload)await refresh();}
  catch(e){let message=e instanceof Error?e.message:String(e);if(reload){try{await refresh();}catch{message+=' Статус не обновлен. Обновите состояние перед повторной операцией.';}}setError(message);}
  finally{lock.current=false;setBusy(false);}
 }
 async function archiveRun(job:()=>Promise<unknown>){await run(async()=>{await job();updateArchive(await call<ArchiveState>('archive_status'));},false);}
 function chooseProject(p:Project){setProject(p.id);setPhrase(state.routes.find(r=>r.project_id===p.id)?.phrase||'');setHistory(null);setProjectPicker(false);setQuery('');if(view==='history')void run(async()=>setHistory(await call<Chat[]>('list_chats',p.id)),false);}
 function changeView(next:View){if(busy)return;setView(next);setError('');if(next==='history')void run(async()=>setHistory(await call<Chat[]>('list_chats',project)),false);}
 const open=()=>void run(()=>call('open_project',project));
 const tabs=[{id:'archive' as View,title:'Архив',icon:Archive},{id:'conversation' as View,title:'Разговор',icon:MessageSquare},{id:'history' as View,title:'История',icon:History}];
 return <div className="app studio-app" aria-busy={busy}>
  <aside className="project-sidebar" aria-label="Проекты">
   <div className="studio-brand"><img src={droidLogo} alt="Core Drone OS"/><span>Kas<span> / Control</span></span></div>
   <div className="sidebar-project-title"><span>Проекты</span><span className="sidebar-dot-key"><i/>есть команда</span></div>
   <div className="catalog-status"><span>{catalogBusy?'Обновляем список…':catalogError?'Облако недоступно':''}</span><button aria-label="Обновить список проектов" title="Обновить список проектов" disabled={catalogBusy||busy} onClick={()=>void loadCatalog()}><RefreshCw size={13}/></button></div>
   {catalogError&&<details className="catalog-error"><summary>Почему список не обновился</summary><p>{catalogError}</p><p>Команды колонки доступны по сохраненным привязкам.</p></details>}
   <label className="sidebar-project-search"><Search size={15}/><input aria-label="Найти проект в списке" placeholder="Найти проект" value={projectQuery} onChange={e=>setProjectQuery(e.target.value)}/></label>
   <nav className="sidebar-project-list" aria-label="Список проектов">{state.projects.filter(p=>p.name.toLowerCase().includes(projectQuery.toLowerCase())).map(p=>{const command=state.routes.find(r=>r.project_id===p.id)?.phrase;return <button key={p.id} disabled={busy} aria-current={p.id===project?'page':undefined} onClick={()=>chooseProject(p)}><Folder size={16}/><span>{p.name}</span>{command&&<i className="project-command-dot" role="img" aria-label="Команда назначена" title={'Команда: '+command}/>}</button>;})}{!state.projects.some(p=>p.name.toLowerCase().includes(projectQuery.toLowerCase()))&&<p>Ничего не найдено</p>}</nav>
   <button className="sidebar-create-project" aria-label="Добавить проект" disabled={busy||catalogBusy} onClick={()=>setProjectPicker(true)}><Plus size={16}/>Создать проект</button>
   <p className="sidebar-footnote">Команды работают без ПК</p>
  </aside>
  <div className="studio-workspace">
  <header className="studio-topbar">
   {view==='archive'?<div className="global-archive-context"><Archive size={16}/>Все проекты</div>:<button className="project-trigger" aria-label="Выбрать проект" disabled={busy} onClick={()=>setProjectPicker(true)}><Folder size={16}/><span>{selected?.name||'Выберите проект'}</span><ChevronDown size={14}/></button>}
   <nav className="studio-tabs" aria-label="Разделы приложения">{tabs.map(tab=><button key={tab.id} aria-current={view===tab.id?'page':undefined} disabled={busy} onClick={()=>changeView(tab.id)}><tab.icon size={15}/>{tab.title}</button>)}</nav>
   <button className="studio-account" aria-label="Войти в ChatGPT" title="Войти в ChatGPT" disabled={busy||catalogBusy} onClick={()=>void run(async()=>{await call('login_chatgpt');await loadCatalog();})}><LogIn size={17}/></button>
  </header>
  {busy&&<div className="studio-loading" role="progressbar" aria-label="Загрузка"><span/></div>}
  <main className="studio-main" ref={mainRef}>
   {error&&<div className="studio-error" role="alert"><span>{error}</span><button aria-label="Скрыть ошибку" onClick={()=>setError('')}><X size={16}/></button></div>}
   {view==='archive'&&<ArchivePanel archive={status?.archive} project={project} projects={state.projects} busy={busy} run={archiveRun}/>}
   {view==='conversation'&&<section className="conversation-scene" aria-label="Разговор с Касом"><div className="studio-eyebrow">Голосовое пространство</div><h1>{selected?.name||'Выберите проект'}</h1><p className="scene-intro">Новый разговор. Контекст вашего проекта.</p><div className="voice-stage"><span className="stage-mark"><AudioLines size={32}/></span><div><h2>{stageTitle}</h2><p>{state.current.phase==='active'?'Сейчас разговор в проекте «'+(activeProject||'текущий')+'».':!route?'Назначьте фразу в поле ниже.':!state.router_ready||stale?'Проверьте связь и обновите состояние.':'Каждый запуск открывает новый подготовленный чат.'}</p></div><div className="stage-actions"><button className="studio-primary" aria-label="Открыть новый разговор" disabled={!ready} onClick={open}><Mic size={17}/>Новый разговор</button><span>{status?.prepared[project]??0} чатов в запасе</span>{state.current.phase==='active'&&state.current.project_id===project&&<button type="button" className="studio-text" disabled={!ready} onClick={()=>void run(()=>call('reconnect_project',project))}>Восстановить разговор</button>}</div></div><form className="scene-command" onSubmit={e=>{e.preventDefault();void run(()=>call('save_project_command',project,phrase));}}><label htmlFor="project-command">Команда колонки</label><p>Произнесите фразу после «Алиса» или «Яндекс».</p><div><input id="project-command" aria-label="Фраза вызова" value={phrase} maxLength={200} disabled={busy} onChange={e=>setPhrase(e.target.value)} placeholder="Например, готовка"/><button className="studio-secondary" aria-label="Сохранить команду" disabled={busy||stale||!phrase.trim()||phrase===route?.phrase}><Check size={15}/>Сохранить</button>{route&&<button className="studio-text" type="button" aria-label="Убрать команду" disabled={busy||stale} onClick={()=>void run(()=>call('remove_project_command',project))}>Убрать</button>}</div></form><div className="scene-common"><span>Общие команды</span><code>открой чат</code><span>·</span><code>закрой чат</code></div></section>}
   {view==='history'&&<section className="history-scene" aria-label="История проекта"><div className="studio-eyebrow">Ваши разговоры</div><div className="scene-heading"><div><h1>История проекта</h1><p>{selected?.name}. Открытие в ChatGPT не переключает колонку.</p></div><button className="studio-secondary" disabled={busy} aria-label="Обновить историю" onClick={()=>void run(async()=>setHistory(await call<Chat[]>('list_chats',project)),false)}><RefreshCw size={15}/>Обновить</button></div><div className="history-ledger">{history?.filter(c=>!!c.conversation_id&&!c.draft).map(c=><button key={c.key} disabled={busy} onClick={()=>void run(()=>call('open_in_chatgpt',project,c.key),false)}><MessageSquare size={17}/><span>{c.title}</span><ArrowUpRight size={16}/></button>)}{history&&!history.some(c=>c.conversation_id&&!c.draft)&&<div className="studio-empty"><History size={28}/><h2>Пока нет сохраненных разговоров</h2><p>Разговоры выбранного проекта появятся здесь.</p></div>}{history===null&&!error&&<p className="studio-muted">Загружаем историю…</p>}</div></section>}
   <details className="support-tools"><summary>Диагностика и версия</summary><p>Kas Control · 2026.11.13-opt1. Отчет сохраняется на этом ПК, без текста разговоров и учетных данных.</p><button className="studio-secondary" disabled={busy} onClick={()=>void run(async()=>{const result=await call<{path:string}>('diagnostics');setDiagnosticPath(result.path);},false)}>Собрать диагностику</button>{diagnosticPath&&<><p className="diagnostic-path">{diagnosticPath}</p><button className="studio-text" disabled={busy} onClick={()=>void run(()=>call('open_diagnostics_folder'),false)}>Открыть папку с отчетом</button></>}</details>
  </main>
  <footer className="session-dock"><div className="session-state" title="Статус при последнем обновлении"><i className={!stale&&state.router_ready?'ready':''}/><div><span>{stale?'Статус не обновлен':!state.router_ready?'Колонка недоступна':state.current.phase==='active'?'Кас разговаривает':state.current.phase==='idle'?'Сеанс закрыт':'Подключение…'}</span><small>{state.current.phase==='active'?activeProject:stale||!state.router_ready?'Требуется обновление связи':!route?'Выберите проект с командой':'Готов к следующему разговору'}</small></div></div><div className="dock-actions"><span className="dock-voice-label">Голос</span><VoicePicker voice={state.current.voice} disabled={busy||stale} onSaved={voice=>setState(s=>({...s,current:{...s.current,voice}}))} onBusyChange={value=>{lock.current=value;setBusy(value);}}/><button className="dock-icon" aria-label="Обновить состояние" title="Обновить состояние" disabled={busy} onClick={()=>void run(()=>refresh(),false)}><RefreshCw size={17}/></button><button className="dock-close" aria-label="Закрыть чат" disabled={busy||stale} onClick={()=>void run(()=>call('close_session'))}><Square size={12}/><span>Закрыть чат</span></button>{view!=='conversation'&&<button className="studio-secondary dock-new" aria-label="Открыть новый разговор" disabled={!ready} onClick={open}><AudioLines size={16}/>Новый разговор</button>}</div></footer>
  </div>
  <Dialog.Root open={projectPicker} onOpenChange={open=>{if(!lock.current){setProjectPicker(open);if(!open)setQuery('');}}}><Dialog.Portal><Dialog.Backdrop className="studio-backdrop"/><Dialog.Popup className="studio-project-dialog"><div className="picker-title"><Dialog.Title>Ваши проекты</Dialog.Title><Dialog.Close aria-label="Закрыть выбор проекта" disabled={busy}><X size={18}/></Dialog.Close></div><Dialog.Description className="picker-description">Выберите пространство для разговора и настроек архива.</Dialog.Description><label className="picker-search"><Search size={17}/><input autoFocus aria-label="Поиск проектов" placeholder="Найти проект" value={query} onChange={e=>setQuery(e.target.value)}/><kbd>Ctrl K</kbd></label><div className="picker-list">{state.projects.filter(p=>p.name.toLowerCase().includes(query.toLowerCase())).map(p=><button key={p.id} disabled={busy} onClick={()=>chooseProject(p)} className={p.id===project?'selected':''}><Folder size={17}/><span>{p.name}</span>{p.id===project&&<Check size={16}/>}</button>)}{!state.projects.some(p=>p.name.toLowerCase().includes(query.toLowerCase()))&&<p>Проекты не найдены</p>}</div><form className="picker-create" onSubmit={e=>{e.preventDefault();if(catalogBusy)return;void run(async()=>{const created=await call<{id?:string}>('create_project',name);setName('');setQuery('');const target=created.id||project;if(view==='history')setHistory(null);await refresh(target);if(view==='history')setHistory(await call<Chat[]>('list_chats',target));},false);}}><label htmlFor="new-project">Новый проект</label><div><input id="new-project" aria-label="Название нового проекта" placeholder="Название проекта" value={name} disabled={busy} onChange={e=>setName(e.target.value)}/><button className="studio-secondary" aria-label="Создать проект" disabled={busy||catalogBusy||stale||!name.trim()}><Plus size={16}/>Создать</button></div></form></Dialog.Popup></Dialog.Portal></Dialog.Root>
 </div>;
}
