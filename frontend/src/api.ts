export type Project = {id:string; name:string; memory_scope?:string};
export type Chat = {key:string; title:string; project_id:string; conversation_id:string; local_id:string; draft:boolean; command:string; current:boolean};
export type Route = {phrase:string; chat_id:string; project_id:string; conversation_id:string; title:string; project_name:string};
export type Overview = {project_sessions?:boolean; project_status?:{prepared:Record<string,number>;hot_preparation:boolean;archive:{available:boolean;running?:boolean;hourly?:boolean;automatic_error?:string;next_run_at?:number;ordinary_projects?:Record<string,boolean>;ordinary?:{checked?:number;copied?:number;pending?:number;deferred?:string;current?:{source_id?:string;project_id?:string;title?:string;state?:string};errors?:{detail:string;source_id?:string;project_id?:string}[]};updated?:number;note?:string;remaining?:number;empty?:number;missing?:number;blocked?:number;voice_copied?:number;items?:{source_id:string;title?:string;project_id:string;state:string;detail?:string;retry_at?:number}[]}};projects:Project[]; current:{project_id:string; key:string; chat_id:string; title:string; conversation_id:string; phase:string; voice:string}; router_ready:boolean; native_commands:{action:number; phrase:string}[]; routes:Route[]};
type Result = {ok:boolean; data:unknown; error?:string};
type Bridge = Record<string,(...args:string[])=>Promise<Result>>;
declare global {interface Window {pywebview?:{api:Bridge}; __demoCalls?:string[]}}
export const demo = new URLSearchParams(location.search).get('demo')==='1';
let demoState:Overview = {projects:[{id:'core',name:'Core Drone OS'},{id:'work',name:'Работа и проекты'},{id:'ideas',name:'Идеи и заметки'},{id:'home',name:'Повседневное'}],current:{project_id:'core',key:'main',chat_id:'main',title:'Основной разговор',conversation_id:'main',phase:'active',voice:'orbit'},router_ready:true,native_commands:[{action:3,phrase:'закрой чат'},{action:3,phrase:'заверши чат'},{action:3,phrase:'хватит'},{action:4,phrase:'открой предыдущий чат'}],routes:[]};
let demoChats:Chat[] = ['Основной разговор','Архитектура и разработка','Домашняя инфраструктура','Идеи для следующего проекта','Книги, кино и всё остальное','Планы на неделю','Новый разговор'].map((title,i)=>({key:i?'chat-'+i:'main',title,project_id:'core',conversation_id:i===6?'':i?'chat-'+i:'main',local_id:'local-'+i,draft:i===6,command:i===0?'открой чат':i===1?'открой рабочий чат':'',current:i===0}));
function syncRoutes(){demoState.routes=demoChats.filter(c=>c.command).map(c=>({phrase:c.command,chat_id:c.local_id,project_id:c.project_id,conversation_id:c.conversation_id,title:c.title,project_name:demoState.projects.find(p=>p.id===c.project_id)?.name||''}));}
syncRoutes();
async function demoCall(method:string,args:string[]):Promise<unknown>{
  window.__demoCalls??=[];window.__demoCalls.push(method);
  await new Promise(r=>setTimeout(r,150));
  const [project,key,value]=args;
  const chat=demoChats.find(c=>c.project_id===project&&c.key===key);
  switch(method){
    case 'set_voice':demoState.current.voice=project;return {voice:project,next_session:true};
    case 'login_chatgpt':return {signed_in:true};
    case 'bootstrap_local':
    case 'bootstrap':syncRoutes();return structuredClone(demoState);
    case 'cloud_projects':return structuredClone(demoState.projects);
    case 'list_chats':return structuredClone(demoChats.filter(c=>c.project_id===project));
    case 'create_project':{const p={id:'project-'+Date.now(),name:project};demoState.projects.push(p);return p;}
    case 'create_chat':{if(value&&demoChats.some(c=>c.command===value))throw Error('Эта команда уже назначена другому чату.');const c:Chat={key:'draft-'+Date.now(),title:key,project_id:project,conversation_id:'',local_id:'',draft:true,command:value||'',current:false};demoChats.push(c);return c;}
    case 'save_command':if(!chat)throw Error('Чат не найден.');if(demoChats.some(c=>c!==chat&&c.command===value))throw Error('Эта команда уже назначена другому чату.');chat.command=value;return {phrase:value};
    case 'remove_command':if(chat)chat.command='';return {};
    case 'connect_chat':if(!chat)throw Error('Чат не найден.');demoChats.forEach(c=>c.current=c===chat);chat.draft=false;chat.conversation_id ||= chat.key;demoState.current={project_id:project,key:chat.key,chat_id:chat.local_id,title:chat.title,conversation_id:chat.conversation_id,phase:'active',voice:'Spruce'};return {};
    case 'close_session':demoState.current.phase='idle';return {};
    case 'restore_binding':return {};
    case 'open_in_chatgpt':return {};
    default:throw Error('Неизвестное действие.');
  }
}
async function bridge():Promise<Bridge>{
  if(typeof window.pywebview?.api?.bootstrap==='function')return window.pywebview.api;
  return new Promise((resolve,reject)=>{
    // WebView2 creates the API object before it injects callable methods.
    const ready=()=>{
      const api=window.pywebview?.api;
      if(typeof api?.bootstrap!=='function')return;
      clearTimeout(timer);window.removeEventListener('pywebviewready',ready);resolve(api);
    };
    const timer=setTimeout(()=>{window.removeEventListener('pywebviewready',ready);reject(Error('Откройте Kas Control через приложение Windows. Связь с колонкой не установлена.'));},12000);
    window.addEventListener('pywebviewready',ready);
    ready();
  });
}
export async function call<T>(method:string,...args:string[]):Promise<T>{
  if(demo)return await demoCall(method,args) as T;
  const api=await bridge();
  if(method==='bootstrap_local'&&typeof api[method]!=='function')method='bootstrap';
  if(method==='cloud_projects'&&typeof api[method]!=='function')return null as T;
  if(typeof api[method]!=='function')throw Error('Обновите приложение: это действие пока недоступно.');
  const result=await api[method](...args);
  if(!result?.ok)throw Error(result?.error||'Не удалось выполнить действие. Обновите состояние и попробуйте ещё раз.');
  return result.data as T;
}
