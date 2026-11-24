import {VoicePicker} from './VoicePicker';
import {ProjectPanel} from './ProjectPanel';
import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Dialog} from '@base-ui/react/dialog';
import {Select} from '@base-ui/react/select';
import {AudioLines, ArrowUpRight, Check, ChevronDown, ChevronRight, Folder, MessageSquare, Mic, Plus, RefreshCw, Search, Square, Undo2, X, LoaderCircle, Command} from 'lucide-react';
import {Toaster, toast} from 'sonner';
import {call, demo, type Chat, type Overview, type Project} from './api';
import './style.css';
import './control.css';
import droidLogo from '../../assets/kas-control.png';

function App(){
  const [overview,setOverview]=useState<Overview|null>(null);const [stale,setStale]=useState(false);
  const [project,setProject]=useState('');const [chats,setChats]=useState<Chat[]>([]);const [selected,setSelected]=useState('');
  const [projectQuery,setProjectQuery]=useState('');const [chatQuery,setChatQuery]=useState('');const [phrase,setPhrase]=useState('');
  const [busy,setBusy]=useState('');const lock=useRef(false);const [error,setError]=useState('');
  const [dialog,setDialog]=useState<'project'|'chat'|'commands'|null>(null);const [name,setName]=useState('');
  const [newPhrase,setNewPhrase]=useState('');
  const [newProject,setNewProject]=useState('');
  const currentProject=overview?.projects.find(p=>p.id===project);
  const selectedChat=chats.find(c=>c.key===selected);
  const active=overview?.current.phase==='active';
  async function run(label:string,job:()=>Promise<void>){
    if(lock.current)return;lock.current=true;setBusy(label);setError('');
    try{await job();}catch(e){const message=e instanceof Error?e.message:'Не удалось выполнить действие.';setError(message);toast.error(message);}
    finally{lock.current=false;setBusy('');}
  }
  function applyChats(rows:Chat[],key:string){setChats(rows);const pick=rows.find(c=>c.key===key)||rows.find(c=>c.current)||rows[0];setSelected(pick?.key||'');setPhrase(pick?.command||'');}
  async function refresh(wantedProject=project,wantedKey=selected){
    try{
      let state=await call<Overview>('bootstrap_local');
      if(!state.project_sessions&&typeof window.pywebview?.api.bootstrap_local==='function')state=await call<Overview>('bootstrap');
      setOverview(state);
      const next=state.projects.some(p=>p.id===wantedProject)?wantedProject:state.current.project_id||state.projects[0]?.id||'';
      const rows=next&&!state.project_sessions?await call<Chat[]>('list_chats',next):[];
      setProject(next);applyChats(rows,wantedKey);setStale(false);
    }catch(e){setStale(true);throw e;}
  }
  async function mutation<T>(method:string,...args:string[]):Promise<T>{
    try{return await call<T>(method,...args);}
    catch(e){
      const original=e instanceof Error?e.message:'Результат действия не подтверждён.';
      try{await refresh();}
      catch{throw Error(original+' Статус не обновлён. Нажмите «Обновить состояние» перед повторной попыткой.');}
      throw Error(original);
    }
  }
  async function refreshAfter(p=project,k=selected){try{await refresh(p,k);}catch(e){throw Error('Действие выполнено, но список не обновлён. Нажмите «Обновить состояние». '+(e instanceof Error?e.message:''));}}
  useEffect(()=>{void run('Подключаюсь к колонке…',()=>refresh());},[]);
  async function chooseProject(id:string){await run('Загружаю разговоры…',async()=>{const rows=await call<Chat[]>('list_chats',id);setProject(id);setChatQuery('');applyChats(rows,'');});}
  function chooseChat(chat:Chat){if(lock.current)return;setSelected(chat.key);setPhrase(chat.command||'');}
  async function mutate(method:string,args:string[],message:string){await run('Сохраняю изменения…',async()=>{await mutation(method,...args);toast.success(message);await refreshAfter();});}
  function showCreate(kind:'project'|'chat'){setName('');setNewPhrase('');setNewProject(project);setDialog(kind);}
  async function create(e:React.FormEvent){
    e.preventDefault();if(!name.trim())return;
    await run(dialog==='project'?'Создаю проект…':'Создаю чат…',async()=>{
      if(dialog==='project'){const p=await mutation<Project>('create_project',name.trim());setDialog(null);setProjectQuery('');setChatQuery('');toast.success('Проект создан. Теперь можно добавить чат.');await refreshAfter(p.id,'');}
      else{const c=await mutation<Chat>('create_chat',newProject,name.trim(),newPhrase.trim());setDialog(null);setChatQuery('');toast.success(c.command?'Чат и команда сохранены.':'Чат подготовлен.');await refreshAfter(newProject,c.key);}
    });
  }
  const visibleChats=chats.filter(c=>(c.title+' '+c.command).toLocaleLowerCase().includes(chatQuery.toLocaleLowerCase()));
  if(!overview)return <div className="startup-shell" aria-busy={!!busy}><section className="startup-card" aria-label="Подключение пульта"><div className="startup-brand"><img src={droidLogo} alt="Core Drone OS"/><span>Kas / Control</span></div><h1>{error?'Не удалось подключиться':'Подключаемся к колонке'}</h1>{error?<><p role="alert">{error}</p><div className="startup-actions"><button disabled={!!busy} onClick={()=>void run('Подключаемся…',()=>refresh())}><RefreshCw size={15}/>Повторить подключение</button><button disabled={!!busy} onClick={()=>void run('Проверяем вход…',async()=>{await call('login_chatgpt');await refresh();})}>Войти в ChatGPT</button></div></>:<><p>Загружаем проекты и состояние колонки.</p><div className="startup-progress" role="status"><LoaderCircle size={18} className="spinning"/><span>Подождите немного…</span></div></>}</section></div>;
  if(overview.project_sessions)return <ProjectPanel initial={overview}/>;
  return <div className="app" aria-busy={!!busy}>
    <aside className="sidebar">
      <div className="brand"><div className="brand-symbol"><AudioLines size={21}/></div><span>Kas<span className="brand-light"> Control</span></span><img className="brand-droid" src={droidLogo} alt="Core Drone OS"/>{demo&&<span className="demo">Демо</span>}</div>
      <div className="sidebar-heading"><span>Проекты</span><button className="icon-button" title="Новый проект" aria-label="Новый проект" onClick={()=>showCreate('project')} disabled={!!busy}><Plus size={18}/></button></div>
      <label className="search"><Search size={16}/><input aria-label="Поиск проектов" placeholder="Найти проект" value={projectQuery} disabled={!!busy} onChange={e=>setProjectQuery(e.target.value)}/></label>
      <nav className="projects" aria-label="Проекты">{overview?.projects.filter(p=>p.name.toLocaleLowerCase().includes(projectQuery.toLocaleLowerCase())).map(p=><button key={p.id} className={'project '+(project===p.id?'selected':'')} aria-current={project===p.id?'page':undefined} disabled={!!busy} onClick={()=>void chooseProject(p.id)}><Folder size={17}/><span>{p.name}</span>{project===p.id&&<ChevronRight size={14}/>}</button>)}{overview&&!overview.projects.length&&<p className="small-muted">Создайте первый проект.</p>}</nav>
      <button className="sidebar-create" disabled={!!busy} onClick={()=>showCreate('project')}><Plus size={16}/>Создать проект</button>
      <div className="sidebar-bottom"><button className="text-button" disabled={!!busy} onClick={()=>void run('Ожидаю вход в ChatGPT…',async()=>{await call('login_chatgpt');toast.success('Вход выполнен. Обновляю проекты…');await refreshAfter();})}><ArrowUpRight size={17}/>Войти в ChatGPT</button><button className="text-button" disabled={!!busy} onClick={()=>setDialog('commands')}><Command size={17}/>Все команды</button><div className="local-note"><span className={'dot '+(overview?.router_ready?'online':'')}/>{overview?.router_ready?'Команды работают без ПК':'Ожидаю обработчик команд'}</div></div>
    </aside>
    <main>
      <header className="station-bar"><div className="station-summary"><span className={'dot '+(!stale&&active?'online':'')}/><span>{stale?'Статус не обновлён':active?'Кас подключён':overview?.current.phase==='idle'?'Голосовой сеанс закрыт':'Состояние колонки'}</span><span className="station-chat">{overview?.current.title||'Подключение…'}</span></div><div className="station-loading">{busy&&<div className="refresh-progress" role="progressbar" aria-label={busy}><span className="refresh-track"/><span className="refresh-runner"><img src={droidLogo} alt=""/></span></div>}</div><div className="station-actions"><VoicePicker voice={overview?.current.voice||''} disabled={!!busy||stale||!overview} onSaved={voice=>setOverview(s=>s?({...s,current:{...s.current,voice}}):s)} onBusyChange={value=>{lock.current=value;setBusy(value?'Сохраняю голос…':'');}}/><button className="icon-button" title="Обновить состояние" aria-label="Обновить состояние" disabled={!!busy} onClick={()=>void run('Обновляю состояние…',()=>refresh())}><RefreshCw size={16} className={busy?'spinning':''}/></button><button className="icon-button" title="Вернуть предыдущую привязку" aria-label="Вернуть предыдущую привязку" disabled={!!busy||!overview} onClick={()=>void mutate('restore_binding',[],'Предыдущая привязка восстановлена.')}><Undo2 size={17}/></button><button className="quiet-button" disabled={!!busy||!overview} onClick={()=>void mutate('close_session',[],'Голосовой сеанс закрыт.')}><Square size={12}/>Закрыть сеанс</button></div></header>
      <div className="workspace">
        <section className="project-header"><div><div className="eyebrow">Ваши разговоры</div><h1>{currentProject?.name||'Выберите проект'}</h1><p>Отдельный чат для каждой темы. Своя команда для каждого чата.</p></div><button className="primary" disabled={!!busy||!project} onClick={()=>showCreate('chat')}><Plus size={18}/>Новый чат</button></section>
        {error&&<div className="error-banner" role="alert"><span>{error}</span><button className="icon-button" aria-label="Скрыть сообщение" onClick={()=>setError('')}><X size={16}/></button></div>}
        <div className="content-grid">
          <section className="chat-panel" aria-label="Чаты проекта"><div className="list-toolbar"><h2>Чаты <span>{chats.length}</span></h2><label className="search chat-search"><Search size={16}/><input disabled={!!busy} aria-label="Поиск чатов" value={chatQuery} onChange={e=>setChatQuery(e.target.value)} placeholder="Найти чат или команду"/></label></div>
            <div className="chat-list">{visibleChats.map(c=><button key={c.key} className={'chat-row '+(selected===c.key?'selected':'')} disabled={!!busy} aria-pressed={selected===c.key} onClick={()=>chooseChat(c)}><div className="chat-icon"><MessageSquare size={18}/></div><div className="chat-copy"><div className="chat-title">{c.title}</div><div className={'chat-command '+(!c.command?'unassigned':'')}>{c.command?<><Mic size={12}/>«{c.command}»</>:'Команда не назначена'}</div></div><div className="chat-badges">{c.current&&<span className="badge active"><span className="dot"/>Текущий</span>}{c.draft&&<span className="badge">Подготовлен</span>}</div><ChevronRight className="row-chevron" size={15}/></button>)}{!visibleChats.length&&<div className="empty"><MessageSquare size={28}/><h3>{chatQuery?'Ничего не найдено':'Здесь начнётся разговор'}</h3><p>{chatQuery?'Попробуйте другое название или команду.':'Создайте чат, назовите тему и назначьте удобную команду.'}</p>{!chatQuery&&<button className="secondary" disabled={!!busy||!project} onClick={()=>showCreate('chat')}><Plus size={16}/>Создать чат</button>}</div>}</div>
          </section>
          <aside className="inspector" aria-label="Настройки выбранного чата">{selectedChat?<><div className="inspector-heading"><div className="eyebrow">Выбранный чат</div><h2>{selectedChat.title}</h2>{selectedChat.draft&&<p className="draft-note">Чат подготовлен. В ChatGPT он сохранится после первого сообщения. Команду можно назначить уже сейчас.</p>}</div><div className="command-editor"><label htmlFor="command">Голосовая команда</label><p className="field-help">После слова «Алиса» произнесите эту фразу.</p><input id="command" placeholder="Например, открой рабочий чат" value={phrase} maxLength={200} disabled={!!busy} onChange={e=>setPhrase(e.target.value)}/><div className="editor-actions"><button className="secondary" disabled={!!busy||!phrase.trim()||phrase===selectedChat.command} onClick={()=>void mutate('save_command',[project,selectedChat.key,phrase.trim()],'Команда сохранена. Текущий сеанс не изменён.')}><Check size={15}/>Сохранить</button>{selectedChat.command&&<button className="text-button" disabled={!!busy} onClick={()=>void mutate('remove_command',[project,selectedChat.key],'Команда удалена. Чат сохранён.')}>Удалить</button>}</div><div className="field-footnote">Назначение команды не переключает разговор.</div>{overview&&!overview.router_ready&&<p className="router-warning">Обработчик команд пока недоступен. Обновите состояние колонки.</p>}</div><div className="connect-area"><button className="primary connect" disabled={!!busy} onClick={()=>void run('Подключаю выбранный чат…',async()=>{await mutation('connect_chat',project,selectedChat.key);toast.success('Выбранный чат подключён.');await refreshAfter();})}><AudioLines size={18}/>Подключить Каса</button><p>Переключить голосовой разговор на этот чат.</p><button className="text-button web-link" disabled={!!busy||selectedChat.draft||!selectedChat.conversation_id} onClick={()=>void run('Открываю ChatGPT…',async()=>{await call('open_in_chatgpt',project,selectedChat.key);})}>Открыть в ChatGPT<ArrowUpRight size={15}/></button></div></>:<div className="inspector-empty"><MessageSquare size={24}/><p>Выберите чат, чтобы настроить команду или подключить Каса.</p></div>}</aside>
        </div>
        <footer className="workspace-footer"><span role="status">{busy?<><LoaderCircle size={13} className="spinning"/>{busy}</>:<><Check size={13}/>Готово</>}</span><span>Создание и команды — отдельно от подключения</span></footer>
      </div>
    </main>
    <Dialog.Root open={dialog!==null} onOpenChange={open=>{if(!open&&!lock.current)setDialog(null);}}><Dialog.Portal><Dialog.Backdrop className="dialog-backdrop"/><Dialog.Popup className={'dialog '+(dialog==='commands'?'commands-dialog':'')}><div className="dialog-header"><Dialog.Title>{dialog==='project'?'Новый проект':dialog==='chat'?'Новый чат':'Команды колонки'}</Dialog.Title><Dialog.Close className="icon-button" aria-label="Закрыть окно" disabled={!!busy}><X size={19}/></Dialog.Close></div><Dialog.Description className="dialog-description">{dialog==='commands'?'Говорите после слова «Алиса». Команды выполняются на колонке, даже когда компьютер выключен.':dialog==='chat'?'Подготовьте отдельный разговор. Текущий голосовой сеанс останется без изменений.':'Проект объединяет ваши разговоры по одной теме.'}</Dialog.Description>
      {dialog==='commands'?<div className="commands-list"><h3>Ваши чаты</h3>{overview?.routes.length?overview.routes.map((r,i)=><div className="command-line" key={i}><div><strong>{r.title}</strong><small>{r.project_name}</small></div><span>«{r.phrase}»</span></div>):<p className="small-muted">Вы пока не назначили команды чатам.</p>}<h3>Управление разговором</h3>{overview?.native_commands.filter(n=>!overview.routes.some(r=>r.phrase===n.phrase)).map((n,i)=><div className="command-line" key={i}><div><strong>{({1:'Открыть текущий чат',2:'Новый чат',3:'Закрыть разговор',4:'Предыдущий чат'} as Record<number,string>)[n.action]||'Команда колонки'}</strong></div><span>«{n.phrase}»</span></div>)}</div>:<form onSubmit={create}>{dialog==='chat'&&<div className="dialog-project"><Select.Root required disabled={!!busy} value={newProject} onValueChange={value=>setNewProject(value||'')} items={overview?.projects.map(p=>({value:p.id,label:p.name}))}>
        <Folder size={16}/><Select.Label>Проект</Select.Label>
        <Select.Trigger className="project-select"><Select.Value className="project-select-value"/><Select.Icon><ChevronDown size={15}/></Select.Icon></Select.Trigger>
        <Select.Portal><Select.Positioner className="project-select-positioner" sideOffset={6} alignItemWithTrigger={false}>
          <Select.Popup className="project-select-popup"><Select.List className="project-options">{overview?.projects.map(p=><Select.Item className="project-option" key={p.id} value={p.id}>
            <span className="project-option-check"><Select.ItemIndicator><Check size={14} aria-hidden="true"/></Select.ItemIndicator></span><Select.ItemText title={p.name}>{p.name}</Select.ItemText>
          </Select.Item>)}</Select.List></Select.Popup>
        </Select.Positioner></Select.Portal>
      </Select.Root></div>}<label className="form-label" htmlFor="new-name">{dialog==='project'?'Название проекта':'Название чата'}</label><input id="new-name" autoFocus required maxLength={dialog==='project'?100:120} disabled={!!busy} value={name} onChange={e=>setName(e.target.value)} placeholder={dialog==='project'?'Например, Домашняя инфраструктура':'Например, Планы на неделю'}/>{dialog==='chat'&&<div className="creation-note"><label className="form-label" htmlFor="new-phrase">Фраза вызова (необязательно)</label><input id="new-phrase" maxLength={200} disabled={!!busy} value={newPhrase} onChange={e=>setNewPhrase(e.target.value)} placeholder="Например, открой кулинарный чат"/><p className="field-help">Произнесите эту фразу после «Алиса» или «Яндекс». Чат и команда сохранятся вместе, без запуска голоса.</p></div>}<div className="dialog-actions"><Dialog.Close className="secondary" disabled={!!busy}>Отмена</Dialog.Close><button className="primary" type="submit" disabled={!!busy||!name.trim()||(dialog==='chat'&&!newProject)}>{busy?<LoaderCircle size={16} className="spinning"/>:<Plus size={16}/>}Создать {dialog==='project'?'проект':'чат'}</button></div></form>}
    </Dialog.Popup></Dialog.Portal></Dialog.Root>
    <Toaster theme="dark" position="bottom-right" closeButton richColors={false}/>
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
