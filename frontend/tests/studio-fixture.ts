import type {Page} from '@playwright/test';
export async function setupStudio(page:Page,options:{running?:boolean;note?:string;queue?:boolean;rateLimit?:boolean;memoryScope?:string;view?:string;hourly?:boolean;deferredCatalog?:boolean;nextRun?:number;example?:{checkedAt:number;projectNames:Record<string,string>}}={}){
 await page.addInitScript((options)=>{
  const archive={next_run_at:options.nextRun,available:!!options.running,running:!!options.running,hourly:options.hourly??true,ordinary_projects:{kitchen:false,core:true},remaining:options.queue?2:0,empty:options.queue?1:0,missing:options.queue?1:0,blocked:options.queue?1:0,updated:1794481800,note:options.note||'checked',ordinary:{checked:4,copied:2,pending:0,errors:[] as any[],current:options.running?{source_id:'source',project_id:'kitchen',title:'Подбор режима приготовления',state:'send'}:null},items:options.queue?[{source_id:'empty',title:'Пробный разговор',project_id:'kitchen',state:'empty'},{source_id:'missing',title:'Ранее закрытый разговор',project_id:'kitchen',state:'missing'},{source_id:'draft',title:'Рабочий разговор',project_id:'core',state:'blocked_draft'}]:[]};
  if(options.rateLimit){archive.note='waiting_rate_limit';archive.ordinary.errors=[{project_id:'core',source_id:'ordinary',detail:'Conversation read HTTP 429'}];}
  const state={project_sessions:true,project_status:{prepared:{kitchen:8,core:7,empty:0},hot_preparation:true,archive},projects:[{id:'kitchen',name:'Кухня и рецепты'},{id:'core',name:'Личные проекты'},{id:'study',name:'Учеба и языки'},{id:'travel',name:'Путешествия'},{id:'work',name:'Рабочие заметки'},{id:'long',name:'Длинное название проекта для проверки переноса текста'},{id:'empty',name:'Новый проект'}],current:{project_id:'kitchen',phase:'idle',voice:'orbit'},router_ready:true,native_commands:[],routes:[{project_id:'kitchen',phrase:'готовка'},{project_id:'core',phrase:'голосовой чат'}]};
  if(options.example){
   archive.updated=options.example.checkedAt;
   for(const project of state.projects)project.name=options.example.projectNames[project.id]??project.name;
  }
  if(options.memoryScope)Object.assign(state.projects[0],{memory_scope:options.memoryScope});
  const w=window as any;w.__setArchive=(value:any)=>Object.assign(archive,value);w.__calls=[];w.__fail='';w.__offline=()=>{state.router_ready=false;};w.__finish=()=>{archive.running=false;archive.available=false;archive.note='checked';};w.__startExternal=()=>{archive.running=true;archive.available=true;archive.note='running';};
  const result=async(method:string,args:unknown[],job:()=>unknown)=>{w.__calls.push({method,args});await new Promise(r=>setTimeout(r,60));return w.__fail===method?{ok:false,error:'Контрольная ошибка '+method}:{ok:true,data:job()};};
  w.pywebview={api:{
   bootstrap:()=>result('bootstrap',[],()=>structuredClone(state)),
   archive_status:()=>result('archive_status',[],()=>structuredClone(archive)),
   set_archive_mode:(mode:string)=>result('set_archive_mode',[mode],()=>{archive.hourly=mode!=='manual';return {hourly:archive.hourly};}),
   set_ordinary_archive:(project:string,mode:string)=>result('set_ordinary_archive',[project,mode],()=>{archive.ordinary_projects[project as 'kitchen']=mode==='enabled';return {ordinary_projects:archive.ordinary_projects};}),
   start_archive:()=>result('start_archive',[],()=>{archive.running=true;archive.available=true;archive.note='running';return {};}),
   stop_archive:()=>result('stop_archive',[],()=>{archive.running=false;archive.available=false;archive.note='checked';return {};}),
   list_chats:(project:string)=>result('list_chats',[project],()=>(project==='empty'||project==='created')?[]:Array.from({length:35},(_,i)=>({key:'cloud:'+project+i,title:i===0?'План на неделю':`Разговор ${i} — подробное обсуждение и заметки`,conversation_id:project+i,project_id:project,draft:false}))),
   open_in_chatgpt:(...a:string[])=>result('open_in_chatgpt',a,()=>({})),
   save_project_command:(project:string,phrase:string)=>result('save_project_command',[project,phrase],()=>{const route=state.routes.find(r=>r.project_id===project);if(route)route.phrase=phrase;else state.routes.push({project_id:project,phrase});return {};}),
   remove_project_command:(project:string)=>result('remove_project_command',[project],()=>{state.routes=state.routes.filter(r=>r.project_id!==project);return {};}),
   open_project:(project:string)=>result('open_project',[project],()=>{state.current.phase='active';state.current.project_id=project;state.project_status.prepared[project as 'kitchen']--;return {};}),
   close_session:()=>result('close_session',[],()=>{state.current.phase='idle';return {};}),
   create_project:(name:string)=>result('create_project',[name],()=>{state.projects.push({id:'created',name});return {id:'created',name};}),
   login_chatgpt:()=>result('login_chatgpt',[],()=>({}))
  }};
  if(options.deferredCatalog)w.pywebview.api.cloud_projects=()=>new Promise(resolve=>{w.__finishCatalog=(fail=false)=>resolve(fail?{ok:false,error:'ChatGPT временно недоступен'}:{ok:true,data:structuredClone(state.projects)});});
  w.pywebview.api.diagnostics=()=>result('diagnostics',[],()=>({path:'C:/Mock/KasControl/Diagnostics/report.zip'}));
  w.pywebview.api.reconnect_project=(project:string)=>result('reconnect_project',[project],()=>({reconnected:true,previous_preserved:true}));
 },options);
 await page.goto('/');await page.getByRole('button',{name:'Выбрать проект'}).waitFor({state:'visible'});if(options.view!=='conversation')await page.getByRole('button',{name:'Архив',exact:true}).click();
}
export async function chooseProject(page:Page,name:string){await page.keyboard.press('Control+k');await page.getByRole('dialog').getByRole('button',{name,exact:true}).click();}
