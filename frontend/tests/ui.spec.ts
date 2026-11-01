import {test,expect,type Page} from '@playwright/test';

test('themed scrollbars keep long project, chat, command and dropdown lists usable',async({page})=>{
  await page.setViewportSize({width:980,height:640});
  await page.addInitScript(()=>{
    const projects=Array.from({length:32},(_,i)=>({id:'p'+i,name:'Проект '+i}));
    const chats=Array.from({length:32},(_,i)=>({key:'c'+i,title:'Разговор '+i,project_id:'p0',conversation_id:'c'+i,local_id:'c'+i,draft:false,command:'открой разговор '+i,current:i===0}));
    const routes=chats.map(c=>({phrase:c.command,chat_id:c.local_id,project_id:'p0',conversation_id:c.conversation_id,title:c.title,project_name:'Проект 0'}));
    window.pywebview={api:{
      bootstrap:async()=>({ok:true,data:{projects,current:{project_id:'p0',key:'c0',title:'Разговор 0',phase:'idle',voice:'Spruce'},router_ready:true,native_commands:[],routes}}),
      list_chats:async()=>({ok:true,data:chats}),
    }};
  });
  await page.goto('/');await expect(page.locator('h1')).toHaveText('Проект 0');
  for(const selector of ['.projects','.chat-list']){
    const list=page.locator(selector);await list.hover();await page.mouse.wheel(0,350);
    await expect.poll(()=>list.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  }
  await page.screenshot({path:'test-results/themed-scrollbars-lists.png'});
  await page.getByRole('button',{name:'Все команды',exact:true}).click();
  const dialog=page.getByRole('dialog');await dialog.hover();await page.mouse.wheel(0,350);
  await expect.poll(()=>dialog.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  await page.screenshot({path:'test-results/themed-scrollbars-dialog.png'});
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Новый чат',exact:true}).click();
  await page.getByRole('combobox',{name:'Проект',exact:true}).click();
  const menu=page.getByRole('listbox');await menu.hover();await page.mouse.wheel(0,350);
  await expect.poll(()=>menu.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
  await page.screenshot({path:'test-results/themed-scrollbars-dropdown.png'});
});

for(const mode of ['normal','reduced','failure'] as const){
  test(`droid loading indicator follows refresh lifecycle: ${mode}`,async({page})=>{
    await page.setViewportSize({width:1200,height:800});
    await page.emulateMedia({reducedMotion:mode==='reduced'?'reduce':'no-preference'});
    await ambiguousBridge(page);await page.goto('/');
    const refresh=page.getByRole('button',{name:'Обновить состояние',exact:true});
    await expect(refresh).toBeEnabled();
    await expect(page.getByAltText('Core Drone OS')).toBeVisible();
    expect(await page.getByAltText('Core Drone OS').evaluate((el:HTMLImageElement)=>el.complete&&el.naturalWidth>0)).toBeTruthy();
    await page.evaluate(fail=>{
      const w=window as any;const original=w.pywebview.api.bootstrap;
      w.pywebview.api.bootstrap=()=>new Promise(resolve=>{
        w.__finishRefresh=async()=>resolve(fail?{ok:false,error:'Нет связи'}:await original());
      });
    },mode==='failure');
    await refresh.click();await expect(page.getByRole('progressbar')).toBeVisible();
    await expect(refresh).toBeDisabled();
    const runner=page.locator('.refresh-runner');
    if(mode==='reduced')await expect(runner).toHaveCSS('animation-name','none');
    else{
      const start=(await runner.locator('img').boundingBox())!.x;
      await expect.poll(async()=>Math.abs((await runner.locator('img').boundingBox())!.x-start)).toBeGreaterThan(8);
    }
    await page.screenshot({path:`test-results/droid-loading-${mode}.png`});
    await page.evaluate(()=>(window as any).__finishRefresh());
    await expect(page.getByRole('progressbar')).not.toBeVisible();
    await expect(refresh).toBeEnabled();
    if(mode==='failure')await expect(page.locator('.error-banner')).toContainText('Нет связи');
    expect(await page.evaluate(()=>(window as any).__calls)).not.toContain('connect_chat');
  });
}

test('styled project menu scrolls within the window and Escape keeps the form open',async({page})=>{
  await page.setViewportSize({width:980,height:640});
  await page.addInitScript(()=>{
    const projects=Array.from({length:32},(_,i)=>({id:'p'+i,name:'Проект '+i+(i===31?' — Длинное название проекта для проверки отображения':'')}));
    window.pywebview={api:{
      bootstrap:async()=>({ok:true,data:{projects,current:{project_id:'p0',key:'',phase:'idle',voice:'Spruce'},router_ready:true,native_commands:[],routes:[]}}),
      list_chats:async()=>({ok:true,data:[]}),
    }};
  });
  await page.goto('/');await page.getByRole('button',{name:'Новый чат',exact:true}).click();
  const project=page.getByRole('combobox',{name:'Проект',exact:true});
  await project.click();
  const menu=page.getByRole('listbox');
  const bounds=await menu.boundingBox();expect(bounds).not.toBeNull();
  expect(bounds!.y+bounds!.height).toBeLessThanOrEqual(640);
  expect(await menu.evaluate(el=>el.scrollHeight>el.clientHeight)).toBeTruthy();
  await page.screenshot({path:'test-results/styled-project-dropdown-scroll.png'});
  const last=page.getByRole('option',{name:'Проект 31 — Длинное название проекта для проверки отображения',exact:true});
  await last.scrollIntoViewIfNeeded();await last.click();
  await expect(project).toContainText('Проект 31');
  await project.click();await expect(page.getByRole('listbox')).toBeVisible();await page.keyboard.press('Escape');
  await expect(page.getByRole('listbox')).not.toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByLabel('Название чата',{exact:true})).toBeVisible();
});

test('new chat selects a project independently and saves its command there',async({page})=>{
  await page.setViewportSize({width:980,height:640});await page.goto('/?demo=1');
  await page.getByRole('button',{name:'Новый чат',exact:true}).click();
  const project=page.getByRole('combobox',{name:'Проект',exact:true});
  await expect(project).toHaveText('Core Drone OS');
  await project.click();
  await expect(page.getByRole('option')).toHaveText(['Core Drone OS','Работа и проекты','Идеи и заметки','Повседневное']);
  await page.screenshot({path:'test-results/styled-project-dropdown.png'});
  await page.getByRole('option',{name:'Работа и проекты',exact:true}).click();
  await page.getByRole('button',{name:'Отмена',exact:true}).click();
  await expect(page.locator('h1')).toHaveText('Core Drone OS');
  expect(await page.evaluate(()=>window.__demoCalls)).not.toContain('create_chat');
  await page.getByRole('button',{name:'Новый чат',exact:true}).click();
  await expect(project).toHaveText('Core Drone OS');
  await project.focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
  await expect(project).toHaveText('Работа и проекты');
  await page.getByLabel('Название чата',{exact:true}).fill('Рабочие планы');
  await page.getByLabel('Фраза вызова (необязательно)',{exact:true}).fill('открой планы');
  await page.screenshot({path:'test-results/create-project-dropdown.png'});
  await page.getByRole('button',{name:'Создать чат',exact:true}).click();
  await expect(page.locator('h1')).toHaveText('Работа и проекты');
  await expect(page.getByRole('heading',{name:'Рабочие планы',exact:true})).toBeVisible();
  await expect(page.getByLabel('Голосовая команда',{exact:true})).toHaveValue('открой планы');
  await expect(page.locator('.station-chat')).toHaveText('Основной разговор');
  const calls=await page.evaluate(()=>window.__demoCalls||[]);
  expect(calls.filter(c=>c==='create_chat')).toHaveLength(1);
  expect(calls).not.toContain('connect_chat');
});

test('new chat can save its call phrase without connecting Voice',async({page})=>{
  await page.setViewportSize({width:980,height:640});await page.goto('/?demo=1');
  await page.getByRole('button',{name:'Новый чат',exact:true}).click();
  await page.getByLabel('Название чата',{exact:true}).fill('Кулинарные идеи');
  await page.getByLabel('Фраза вызова (необязательно)',{exact:true}).fill('открой кулинарные идеи');
  await page.screenshot({path:'test-results/create-with-command.png'});
  await page.getByRole('button',{name:'Создать чат',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Кулинарные идеи',exact:true})).toBeVisible();
  await expect(page.getByLabel('Голосовая команда',{exact:true})).toHaveValue('открой кулинарные идеи');
  const calls=await page.evaluate(()=>window.__demoCalls||[]);
  expect(calls.filter(c=>c==='create_chat')).toHaveLength(1);
  expect(calls).not.toContain('save_command');expect(calls).not.toContain('connect_chat');
});

test('web login refreshes projects without changing the voice session',async({page})=>{
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'Войти в ChatGPT',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.__demoCalls?.slice(-3))).toEqual(['login_chatgpt','bootstrap','list_chats']);
  await expect(page.locator('.station-summary')).toContainText('Кас подключён');
});

async function ambiguousBridge(page:Page,refreshFails=false,delayedApi=false){
  await page.addInitScript(({refreshFails,delayedApi})=>{
    const w=window as typeof window&{__calls:string[]};w.__calls=[];
    let phase='active';let mutated=false;
    const chat={key:'main',title:'Разговор',project_id:'core',conversation_id:'main',local_id:'main',draft:false,command:'',current:true};
    const projects=[{id:'core',name:'Исходный проект'}];
    w.pywebview={api:{
      bootstrap:async()=>{w.__calls.push('bootstrap');return mutated&&refreshFails?{ok:false,data:null,error:'Нет связи'}:{ok:true,data:{projects,current:{project_id:'core',key:'main',chat_id:'main',title:'Разговор',conversation_id:'main',phase,voice:'Spruce'},router_ready:true,native_commands:[],routes:[]}};},
      list_chats:async()=>{w.__calls.push('list_chats');return {ok:true,data:[chat]};},
      close_session:async()=>{w.__calls.push('close_session');phase='idle';mutated=true;return {ok:false,data:null,error:'Ответ потерян. Операция могла завершиться.'};},
      create_project:async(name:string)=>{w.__calls.push('create_project');projects.push({id:'new',name});mutated=true;return {ok:false,data:null,error:'Создание не подтверждено.'};},
    }};
    if(delayedApi){
      const pending=w.pywebview.api;
      w.pywebview.api={};
      setTimeout(()=>{w.pywebview!.api=pending;w.dispatchEvent(new Event('pywebviewready'));},800);
    }
  },{refreshFails,delayedApi});
}

test('closing refreshes the settled station state automatically',async({page})=>{
  await page.goto('/?demo=1');
  await page.getByRole('button',{name:'Закрыть сеанс',exact:true}).click();
  await expect(page.locator('.station-summary')).toContainText('Голосовой сеанс закрыт');
  expect(await page.evaluate(()=>window.__demoCalls?.slice(-3))).toEqual(['close_session','bootstrap','list_chats']);
});

test('waits for methods when WebView2 initially exposes an empty API object',async({page})=>{
  await ambiguousBridge(page,false,true);
  await page.goto('/');
  await expect(page.locator('h1')).toHaveText('Исходный проект');
  await expect(page.locator('.station-summary')).toContainText('Кас подключён');
  await expect(page.locator('.error-banner')).not.toBeVisible();
  expect(await page.evaluate(()=>(window as any).__calls)).toEqual(['bootstrap','list_chats']);
});

test('ambiguous mutation refreshes actual state without retry and preserves error',async({page})=>{
  await ambiguousBridge(page);await page.goto('/');
  await page.getByRole('button',{name:'Закрыть сеанс',exact:true}).click();
  await expect(page.locator('.station-summary')).toContainText('Голосовой сеанс закрыт');
  await expect(page.locator('.error-banner')).toContainText('Ответ потерян');
  expect(await page.evaluate(()=>(window as any).__calls)).toEqual(['bootstrap','list_chats','close_session','bootstrap','list_chats']);
});

test('failed recovery read marks old status stale instead of reporting active',async({page})=>{
  await ambiguousBridge(page,true);await page.goto('/');
  await page.getByRole('button',{name:'Закрыть сеанс',exact:true}).click();
  await expect(page.locator('.station-summary')).toContainText('Статус не обновлён');
  await expect(page.locator('.station-summary')).not.toContainText('Кас подключён');
  await expect(page.locator('.error-banner')).toContainText('Ответ потерян');
  expect(await page.evaluate(()=>(window as any).__calls)).toEqual(['bootstrap','list_chats','close_session','bootstrap']);
});

test('uncertain project creation refreshes projects while preserving selected project',async({page})=>{
  await ambiguousBridge(page);await page.goto('/');
  await page.getByRole('button',{name:'Новый проект',exact:true}).click();
  await page.getByLabel('Название проекта').fill('Добавленный проект');
  await page.getByRole('dialog').getByRole('button',{name:'Создать проект',exact:true}).click();
  await expect(page.locator('.error-banner')).toContainText('Создание не подтверждено');
  await page.getByRole('button',{name:'Закрыть окно',exact:true}).click();
  await expect(page.locator('h1')).toHaveText('Исходный проект');
  await expect(page.getByRole('navigation',{name:'Проекты'})).toContainText('Добавленный проект');
  expect(await page.evaluate(()=>(window as any).__calls)).toEqual(['bootstrap','list_chats','create_project','bootstrap','list_chats']);
});
test('create and assign never connect; connect is separate',async({page})=>{
  await page.setViewportSize({width:1200,height:800});await page.goto('/?demo=1');
  await expect(page.getByRole('button',{name:'Новый чат',exact:true})).toBeEnabled();
  await page.screenshot({path:'test-results/desktop.png'});
  await page.getByRole('button',{name:'Новый чат',exact:true}).click();
  await page.getByLabel('Название чата').fill('Тестовый разговор');
  await page.screenshot({path:'test-results/create-dialog.png'});
  await page.getByRole('button',{name:'Создать чат',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Тестовый разговор'})).toBeVisible();
  await page.getByLabel('Голосовая команда',{exact:true}).fill('открой тестовый чат');
  await page.getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect(page.getByRole('button',{name:'Сохранить',exact:true})).toBeDisabled();
  await expect.poll(()=>page.evaluate(()=>window.__demoCalls)).not.toContain('connect_chat');
  await page.getByRole('button',{name:'Подключить Каса',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.__demoCalls)).toContain('connect_chat');
  await expect(page.getByRole('button',{name:'Подключить Каса',exact:true})).toBeEnabled();
});
test('global commands and minimum window layout',async({page})=>{
  await page.setViewportSize({width:980,height:640});await page.goto('/?demo=1');
  await expect(page.getByRole('button',{name:'Все команды',exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:'test-results/minimum.png'});
  await page.getByRole('button',{name:'Все команды',exact:true}).click();
  await expect(page.getByText('«закрой чат»',{exact:true})).toBeVisible();
  await expect(page.getByText('«заверши чат»',{exact:true})).toBeVisible();
  await expect(page.getByText('«хватит»',{exact:true})).toBeVisible();
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).not.toBeVisible();
});
test('production does not fall back to fixtures',async({page})=>{
  await page.goto('/');
  await expect(page.getByRole('alert').filter({hasText:'Откройте Kas Control'})).toBeVisible({timeout:16000});
  await expect(page.getByText('Core Drone OS',{exact:true})).not.toBeVisible();
});
